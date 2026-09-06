"""Multi-context orchestration (M4).

Four named exploration policies, each an isolated context with its own mandate
suffix, tool allow-list, artifact directory, correlation-id prefix, and a
merchant namespace slot the coordinator fills with fresh Direct merchants. A
supervisor runs contexts sequentially (the host has one arena; model calls are
serial-safe) with per-context budgets and a machine-readable run summary.

The existing ``run_campaign`` is reused unchanged; a context is a thin wrapper
that supplies (a) the policy mandate suffix and (b) a Dispatcher tool allowlist.
For offline tests a ``runner`` callable is injected so no gateway is required.

No network, no credentials in this module.
"""
import json
import time
from pathlib import Path

from . import config


# --------------------------------------------------------------------------
# Tool groups
# --------------------------------------------------------------------------
_READ_TOOLS = {"whoami", "code_search", "code_read", "code_list", "code_find_symbol",
               "recall", "note"}
_RECORD_TOOLS = {"record_hypothesis", "update_hypothesis", "record_observation",
                 "define_experiment", "mark_blocked", "request_replay", "conclude"}
_BASE = _READ_TOOLS | _RECORD_TOOLS


class ContextPolicy:
    def __init__(self, name, mandate_suffix, tool_allowlist, correlation_prefix,
                 target_bias=None):
        self.name = name
        self.mandate_suffix = mandate_suffix
        self.tool_allowlist = set(tool_allowlist)
        self.correlation_prefix = correlation_prefix
        self.target_bias = target_bias or []

    def artifact_dir(self, root):
        d = Path(root) / ("ctx-" + self.name)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def mandate(self, base_mandate):
        return base_mandate.rstrip() + "\n\n## Context policy: %s\n%s\n" % (
            self.name, self.mandate_suffix)

    def to_dict(self):
        return {"name": self.name, "mandate_suffix": self.mandate_suffix,
                "tool_allowlist": sorted(self.tool_allowlist),
                "correlation_prefix": self.correlation_prefix,
                "target_bias": self.target_bias}


POLICIES = [
    ContextPolicy(
        name="broad_coverage",
        mandate_suffix=("Survey the full merchant surface; breadth over depth. Open many "
                        "shallow hypotheses across payouts, contacts, fund accounts, balances, "
                        "transactions and payout links. Do not go deep on any single asset."),
        tool_allowlist=_BASE | {"merchant_request", "claim_candidate"},
        correlation_prefix="bc",
        target_bias=["/v1/payouts", "/v1/contacts", "/v1/fund_accounts", "/v1/balances",
                     "/v1/transactions", "/v1/payout_links"]),
    ContextPolicy(
        name="deep_direct_accounting",
        mandate_suffix=("Focus on the Direct payout path and ledger zero-sum / reversal / "
                        "idempotency invariants. Go deep on one asset; prove accounting effects "
                        "at runtime, not from code alone."),
        tool_allowlist=_BASE | {"merchant_request", "read_own_webhooks", "claim_candidate"},
        correlation_prefix="dda",
        target_bias=["/v1/payouts", "/v1/transactions", "/v1/balances"]),
    ContextPolicy(
        name="identity_tenant_boundary",
        mandate_suffix=("Probe identity resolution and cross-tenant authorization. EVERY "
                        "experiment must include: own-merchant control + merchant-B variant + "
                        "malformed/absent identity variant + expected-denial control + a state "
                        "observation + a clean reset. A denial is information, never proof of "
                        "safety; an own-merchant success is only a control."),
        tool_allowlist=_BASE | {"merchant_request", "claim_candidate"},
        correlation_prefix="itb",
        target_bias=["/v1/payouts", "/v1/fund_accounts", "/v1/contacts"]),
    ContextPolicy(
        name="concurrency_event_order",
        mandate_suffix=("Probe races, duplicate delivery, and event ordering: idempotency-key "
                        "reuse, dedupe, webhook duplication, and out-of-order state transitions."),
        # the ONLY policy granted the concurrent tool
        tool_allowlist=_BASE | {"merchant_request", "merchant_request_concurrent",
                                "read_own_webhooks", "claim_candidate"},
        correlation_prefix="ceo",
        target_bias=["/v1/payouts", "/v1/payouts_batch"]),
]

POLICY_BY_NAME = {p.name: p for p in POLICIES}


class MerchantDescriptor:
    """A merchant namespace slot the coordinator fills with a fresh Direct
    merchant. The supervisor never provisions merchants itself (another stream
    owns provisioner.py); it accepts descriptors."""

    def __init__(self, merchant_id=None, key_id=None, namespace=None, label=None):
        self.merchant_id = merchant_id
        self.key_id = key_id
        self.namespace = namespace
        self.label = label

    def to_dict(self):
        return {"merchant_id": self.merchant_id, "key_id": self.key_id,
                "namespace": self.namespace, "label": self.label}


class ContextSupervisor:
    """Runs policies as isolated contexts against one shared durable store and a
    shared judge. Sequential by default (parallel is a future opt-in)."""

    def __init__(self, store, runner=None):
        self.store = store
        self.store.ensure_kind("contexts")
        # runner(policy, merchant, budget) -> summary dict. Default calls the real
        # campaign; injectable for offline tests.
        self.runner = runner or self._default_runner

    def run(self, policies=None, merchants=None, per_context_budget=None, base_mandate=""):
        policies = policies or POLICIES
        merchants = merchants or {}
        started = _utc()
        results = []
        for policy in policies:
            merch = merchants.get(policy.name)
            budget = (per_context_budget or {}).get(policy.name, {})
            self.store._append("contexts", {
                "event": "context_start", "policy": policy.name,
                "correlation_prefix": policy.correlation_prefix,
                "merchant": merch.to_dict() if isinstance(merch, MerchantDescriptor) else merch,
                "budget": budget, "ts": _utc()})
            try:
                summary = self.runner(policy, merch, budget, base_mandate)
                summary = dict(summary or {})
                summary["error"] = None
            except Exception as e:  # noqa: BLE001 - one context must not kill the rest
                summary = {"policy": policy.name, "error": str(e)[:400],
                           "stop_reason": "context_exception"}
            summary["policy"] = policy.name
            self.store._append("contexts", {"event": "context_end", "policy": policy.name,
                                            "summary": summary, "ts": _utc()})
            results.append(summary)
        return {
            "started_at": started, "ended_at": _utc(),
            "policies": [p.name for p in policies],
            "contexts": results,
            "total_turns": sum(int(r.get("turns_completed") or 0) for r in results),
        }

    def _default_runner(self, policy, merchant, budget, base_mandate):
        """Real runner: builds a broker+dispatcher wired to the policy allowlist
        and lifecycle managers, then runs the existing campaign loop. Requires a
        gateway; used only outside tests."""
        from .campaign import run_campaign
        from .hypotheses import HypothesisManager
        from .leases import LeaseManager
        mandate = policy.mandate(base_mandate)
        hyp_mgr = HypothesisManager(self.store)
        lease_mgr = LeaseManager(self.store)
        # The caller supplies broker/judge via a partially-applied runner in prod;
        # this default is a safe stub that records that a real gateway is needed.
        raise RuntimeError("ContextSupervisor._default_runner requires a broker/judge "
                           "wiring; inject a runner (see run.py contexts subcommand)")


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
