"""Gateway-backed context runner adapter (M4, task T15).

The offline ``ContextSupervisor``/``Soak`` machinery (contexts.py, soak.py) is
driven by an injected ``runner``/``cycle_runner`` callable so it can be unit
tested with no gateway. This module supplies the *production* adapter: it wires a
per-context Broker (reusing the boundary construction from run.py's
``run_campaign_cmd``), assembles the policy mandate, builds a Dispatcher with the
policy tool allow-list + HypothesisManager + LeaseManager + owner, and drives the
existing ``run_campaign`` loop.

Provisioning: each context gets its OWN fresh Direct merchant via
``provisioner_direct.provision_direct_merchant`` (a per-policy role off the
campaign id, so ids are deterministic and disjoint per policy AND fresh per run).
``self_check_direct_merchant`` must pass before the context runs; if provisioning
or the self-check fails, the context is recorded **blocked** with blocker
``environment_failure`` -- never silently skipped.

Injection seam (no edit to the frozen campaign.py): ``run_campaign`` constructs
``Dispatcher(broker, store, judge_hook)`` internally, so this module temporarily
rebinds the ``campaign.Dispatcher`` module attribute to a ``functools.partial``
that carries the policy allow-list + lifecycle managers + owner, and restores it
in a ``finally``. ContextSupervisor/Soak are sequential, so the rebind is safe.

SAFETY: model calls go only through the LiteLLM gateway (llm.ChatClient). No
credential is ever printed or persisted un-redacted; provider error strings are
passed through ``llm.redact`` before they touch disk.
"""
import functools
import time

from . import config, llm, provisioner_direct
from .broker import Broker
from .campaign import run_campaign
from .hypotheses import HypothesisManager
from .leases import LeaseManager
from .tools import Dispatcher

# Default per-context budgets (modest; this is a correctness proof, not the soak).
DEFAULT_CONTEXT_BUDGET = {
    "emergency_max_turns": 14,
    "request_budget": 60,
    "max_wall_seconds": 240,
}
# Default per-soak-cycle budget (one short bounded turn-budget per cycle).
DEFAULT_CYCLE_BUDGET = {
    "emergency_max_turns": 4,
    "request_budget": 20,
    "max_wall_seconds": 90,
}


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _base_mandate():
    return (config.PROMPTS_DIR / "primary_mandate.txt").read_text()


def _blocked_summary(policy, blocker, reason, **extra):
    """Machine-readable blocked-context record (never a silent skip)."""
    out = {
        "policy": policy.name,
        "blocked": True,
        "blocker": blocker,
        "stop_reason": "context_blocked",
        "reason": reason,
        "turns_completed": 0,
        "normal_completion": False,
        "hypotheses_by_status": {},
    }
    out.update(extra)
    return out


def _prefixed_sink(store, correlation_prefix, policy_name):
    """Wrap the action sink so every recorded request carries the policy
    correlation prefix (correlation ids carry policy.correlation_prefix)."""
    def sink(record):
        rec = dict(record)
        rec.setdefault("correlation_prefix", correlation_prefix)
        rec.setdefault("policy", policy_name)
        return store.add_action(rec)
    return sink


def provision_context_merchant(store, policy, restart=True, role=None):
    """Provision + self-check ONE fresh Direct merchant for a policy.

    Returns ``(cred, public_summary, blocked)`` where ``cred`` is the broker
    credential dict (or None when blocked) and ``blocked`` is a blocked-context
    summary dict (or None on success)."""
    role = role or ("ctx-" + policy.correlation_prefix)
    try:
        d = provisioner_direct.provision_direct_merchant(
            store.campaign_id, role=role, restart=restart)
    except Exception as e:  # noqa: BLE001
        detail = llm.redact(str(e))[:300]
        store.event("context_provision_error", policy=policy.name, detail=detail)
        return None, None, _blocked_summary(
            policy, "environment_failure", "provisioning raised: " + detail)

    failed = d.get("failed_steps") or []
    # Self-check can race a just-restarted service (a check reads a store that has
    # not yet reloaded the new merchant). Retry a few times with a short settle
    # delay before declaring an environment failure; a genuinely broken provision
    # stays not-ready across all attempts.
    sc = None
    for attempt in range(4):
        try:
            sc = provisioner_direct.self_check_direct_merchant(d)
        except Exception as e:  # noqa: BLE001
            detail = llm.redact(str(e))[:300]
            store.event("context_self_check_error", policy=policy.name,
                        merchant_id=d.get("merchant_id"), attempt=attempt, detail=detail)
            time.sleep(3)
            continue
        if sc.get("ready"):
            break
        if attempt < 3:
            store.event("context_self_check_retry", policy=policy.name,
                        merchant_id=d.get("merchant_id"), attempt=attempt,
                        passed=sc.get("passed"), total=sc.get("total"))
            time.sleep(3)
    if sc is None:
        return None, None, _blocked_summary(
            policy, "environment_failure", "self_check raised on every attempt",
            merchant_id=d.get("merchant_id"))

    public = {
        "merchant_id": d["merchant_id"], "key_id": d["key_id"],
        "account_number": d["account_number"], "fund_account_id": d["fund_account_id"],
        "channel": d["channel"], "role": d["role"],
        "self_check_passed": bool(sc["ready"]),
        "checks_passed": sc["passed"], "checks_total": sc["total"],
        "failed_steps": failed,
    }
    store._append("contexts", {"event": "context_provision", "policy": policy.name,
                               "ts": _utc(), **public})

    if not sc["ready"]:
        return None, public, _blocked_summary(
            policy, "environment_failure",
            "self_check not ready (%d/%d)" % (sc["passed"], sc["total"]),
            merchant_id=d["merchant_id"], self_check=public)

    cred = {"merchant_id": d["merchant_id"], "key_id": d["key_id"],
            "secret": d["secret"], "mode": "live"}
    return cred, public, None


def make_runner(store, judge_hook=None, judge_hook_factory=None, primary_model=None,
                provision=True, restart=True, merchant_creds=None, reproducer_hook=None):
    """Return a production ``runner(policy, merchant, budget, base_mandate)``.

    ``merchant_creds`` is an optional mutable cache mapping ``policy.name`` -> a
    broker credential dict. When ``provision`` is True and no cached credential
    exists, a fresh Direct merchant is provisioned and cached (so a soak reuses
    the same merchant across cycles rather than re-provisioning each cycle).

    Each context needs a judge scoped to ITS merchant (the attacker id differs per
    context), so pass ``judge_hook_factory(store, cred, policy) -> judge_hook``.
    A plain ``judge_hook`` (used for every context) is accepted as a fallback.
    ``reproducer_hook`` is accepted for symmetry with the campaign's replay path;
    replay of a supported hypothesis is driven by the closing lifecycle, not here.
    """
    primary_model = primary_model or config.PRIMARY_MODEL
    cache = merchant_creds if merchant_creds is not None else {}

    def runner(policy, merchant, budget, base_mandate):
        budget = dict(DEFAULT_CONTEXT_BUDGET, **(budget or {}))

        # 1. per-context fresh Direct merchant (or a cached one) --------------
        cred = cache.get(policy.name)
        public = None
        if cred is None and isinstance(merchant, dict) and merchant.get("secret"):
            cred = {"merchant_id": merchant["merchant_id"], "key_id": merchant["key_id"],
                    "secret": merchant["secret"], "mode": merchant.get("mode", "live")}
        if cred is None and provision:
            cred, public, blocked = provision_context_merchant(
                store, policy, restart=restart)
            if blocked is not None:
                return blocked
            cache[policy.name] = cred
        if cred is None:
            return _blocked_summary(policy, "environment_failure",
                                    "no credential and provisioning disabled")

        # 2. broker + mandate -------------------------------------------------
        req_budget = int(budget["request_budget"])
        broker = Broker(cred, _prefixed_sink(store, policy.correlation_prefix, policy.name),
                        request_budget=req_budget)
        base = base_mandate or _base_mandate()
        base = base.replace("{attacker_merchant_id}", cred["merchant_id"]) \
                   .replace("{attacker_key_id}", cred["key_id"])
        mandate = policy.mandate(base)

        # 3. lifecycle managers + Dispatcher injection ------------------------
        hyp_mgr = HypothesisManager(store)
        lease_mgr = LeaseManager(store)
        before = len(hyp_mgr.all())
        jh = judge_hook_factory(store, cred, policy) if judge_hook_factory else judge_hook
        if jh is None:
            return _blocked_summary(policy, "environment_failure",
                                    "no judge_hook or judge_hook_factory supplied")

        from . import campaign as campaign_mod
        original_dispatcher = campaign_mod.Dispatcher
        campaign_mod.Dispatcher = functools.partial(
            Dispatcher, hyp_manager=hyp_mgr, lease_manager=lease_mgr,
            allowed_tools=policy.tool_allowlist, owner=policy.name)
        store.event("context_run_start", policy=policy.name,
                    merchant_id=cred["merchant_id"], model=primary_model,
                    correlation_prefix=policy.correlation_prefix, budget=budget)
        try:
            summary = run_campaign(
                store, broker, primary_model, mandate, jh,
                emergency_max_turns=int(budget["emergency_max_turns"]),
                max_wall_seconds=budget.get("max_wall_seconds"),
                model_fallbacks=budget.get("model_fallbacks"))
        except llm.LLMError as e:
            # gateway budget/availability failure mid-context: honest, not fabricated
            detail = llm.redact(str(e))[:300]
            store.event("context_model_unrecoverable", policy=policy.name, detail=detail)
            summary = {"policy": policy.name, "stop_reason": "model_unrecoverable",
                       "normal_completion": False, "turns_completed": 0,
                       "detail": detail}
        finally:
            campaign_mod.Dispatcher = original_dispatcher

        # 4. enrich the summary ----------------------------------------------
        after_all = hyp_mgr.all()
        summary = dict(summary or {})
        summary["policy"] = policy.name
        summary["merchant_id"] = cred["merchant_id"]
        summary["correlation_prefix"] = policy.correlation_prefix
        summary["model"] = primary_model
        summary["hypotheses_by_status"] = hyp_mgr.rollup()
        summary["new_hypotheses"] = max(0, len(after_all) - before)
        if public is not None:
            summary["provisioning"] = public
        store.event("context_run_end", policy=policy.name,
                    stop_reason=summary.get("stop_reason"),
                    turns=summary.get("turns_completed"),
                    new_hypotheses=summary.get("new_hypotheses"))
        return summary

    return runner


def make_cycle_runner(store, judge_hook=None, judge_hook_factory=None,
                      primary_model=None, provision=True, restart=True,
                      merchant_creds=None, cycle_budget=None):
    """Return a ``cycle_runner(policy, cycle_idx, ctx)`` for ``Soak.run``.

    Each cycle runs ONE short bounded context turn-budget on the policy's merchant
    (provisioned lazily and cached across cycles) and returns the soak's expected
    ``{produced_work, new_hypotheses, duplicates_suppressed, experiments_executed}``.
    """
    primary_model = primary_model or config.PRIMARY_MODEL
    cache = merchant_creds if merchant_creds is not None else {}
    cbudget = dict(DEFAULT_CYCLE_BUDGET, **(cycle_budget or {}))
    runner = make_runner(store, judge_hook=judge_hook,
                         judge_hook_factory=judge_hook_factory,
                         primary_model=primary_model, provision=provision,
                         restart=restart, merchant_creds=cache)

    def _dup_count():
        n = 0
        for ev in store._read_all("events"):
            if ev.get("kind") == "duplicate_suppressed":
                n += 1
        return n

    def cycle_runner(policy, cycle_idx, ctx):
        dups_before = _dup_count()
        summary = runner(policy, None, dict(cbudget), _base_mandate())
        dups_after = _dup_count()
        if summary.get("blocked"):
            return {"produced_work": False, "new_hypotheses": 0,
                    "duplicates_suppressed": 0, "experiments_executed": 0,
                    "blocked": True, "blocker": summary.get("blocker"),
                    "policy": policy.name}
        experiments = int(summary.get("broker_requests") or 0)
        new_h = int(summary.get("new_hypotheses") or 0)
        produced = bool(experiments > 0 or new_h > 0
                        or int(summary.get("turns_completed") or 0) > 0)
        return {
            "produced_work": produced,
            "new_hypotheses": new_h,
            "duplicates_suppressed": max(0, dups_after - dups_before),
            "experiments_executed": experiments,
            "policy": policy.name,
            "scenario": ctx.get("scenario"),
            "stop_reason": summary.get("stop_reason"),
            "merchant_id": summary.get("merchant_id"),
        }

    return cycle_runner
