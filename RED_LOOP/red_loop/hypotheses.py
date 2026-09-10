"""M4 hypothesis lifecycle (Lifecycle v2).

Additive, backward-compatible layer over the existing free-text hypothesis
records (state.py:add_hypothesis). The legacy runtime is untouched; this module
provides a real state machine, semantic fingerprinting, duplicate suppression,
priority ordering, and a durable v2 record store (``hypotheses_v2.jsonl``) that
survives a process kill exactly like the other append-only ledgers.

Nothing here reads or prints any credential; there are no network calls.
"""
import hashlib
import re
import time
from enum import Enum


def _utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------
# Lifecycle states
# --------------------------------------------------------------------------
class HypothesisState(str, Enum):
    PROPOSED = "proposed"
    CLAIMED = "claimed"
    EXPERIMENT_DEFINED = "experiment_defined"
    EXECUTING = "executing"
    EVIDENCE_GATHERED = "evidence_gathered"
    FALSIFIED = "falsified"
    BLOCKED = "blocked"
    SUPPORTED = "supported"
    REPLAY_REQUESTED = "replay_requested"
    REPRODUCED = "reproduced"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    CLOSED = "closed"
    # coordinator-only administrative outcome
    DEPRIORITIZED = "deprioritized"

    def __str__(self):
        return self.value


S = HypothesisState

# The mandated primary chain:
#   proposed -> claimed -> experiment_defined -> executing -> evidence_gathered
#     -> (falsified | blocked | supported)
#   supported -> replay_requested -> (reproduced | rejected)
#   reproduced -> (accepted | closed)
LEGAL_TRANSITIONS = {
    S.PROPOSED: {S.CLAIMED, S.DEPRIORITIZED, S.BLOCKED},
    S.CLAIMED: {S.EXPERIMENT_DEFINED, S.DEPRIORITIZED, S.BLOCKED},
    S.EXPERIMENT_DEFINED: {S.EXECUTING, S.BLOCKED, S.DEPRIORITIZED},
    S.EXECUTING: {S.EVIDENCE_GATHERED, S.BLOCKED, S.DEPRIORITIZED},
    S.EVIDENCE_GATHERED: {S.FALSIFIED, S.BLOCKED, S.SUPPORTED},
    S.SUPPORTED: {S.REPLAY_REQUESTED, S.CLOSED},
    S.REPLAY_REQUESTED: {S.REPRODUCED, S.REJECTED, S.BLOCKED},
    S.REPRODUCED: {S.ACCEPTED, S.CLOSED},
    # blocked is recoverable: the coordinator may re-claim once the blocker clears
    S.BLOCKED: {S.CLAIMED, S.DEPRIORITIZED},
    S.DEPRIORITIZED: {S.PROPOSED, S.CLAIMED},
    # terminal sinks
    S.FALSIFIED: set(),
    S.REJECTED: set(),
    S.ACCEPTED: set(),
    S.CLOSED: set(),
}

# Fully resolved states (no further exploration work expected).
TERMINAL_STATES = {
    S.FALSIFIED, S.BLOCKED, S.REJECTED, S.ACCEPTED, S.CLOSED, S.DEPRIORITIZED,
}
# States that must stamp a machine-readable reason on entry.
REASON_REQUIRED_ON_ENTRY = {
    S.FALSIFIED, S.BLOCKED, S.ACCEPTED, S.CLOSED, S.REJECTED, S.DEPRIORITIZED,
}


class Blocker(str, Enum):
    ROUTE_NOT_EXPOSED = "route_not_exposed"
    REQUIRED_ACTOR_UNAVAILABLE = "required_actor_unavailable"
    SERVICE_NOT_MODELED = "service_not_modeled"
    FIDELITY_INSUFFICIENT = "fidelity_insufficient"
    MISSING_EVENT_CONSUMER = "missing_event_consumer"
    MISSING_TOOL_CAPABILITY = "missing_tool_capability"
    ENVIRONMENT_FAILURE = "environment_failure"
    TIME_BUDGET_EXHAUSTED = "time_budget_exhausted"

    def __str__(self):
        return self.value


class LifecycleError(ValueError):
    """Raised on an illegal transition or a missing required field."""


# --------------------------------------------------------------------------
# Priority: mandate says "int; higher first". Highest priority == max value.
# --------------------------------------------------------------------------
class Priority:
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @staticmethod
    def coerce(v):
        if v is None:
            return Priority.MEDIUM
        try:
            return int(v)
        except (TypeError, ValueError):
            return Priority.MEDIUM


# --------------------------------------------------------------------------
# Semantic fingerprint (normalized claim + target assets hash)
# --------------------------------------------------------------------------
_ID_PATTERNS = [
    re.compile(r"\bpout_[A-Za-z0-9]+", re.I),
    re.compile(r"\b[a-z]{2,6}_[A-Za-z0-9]{10,}", re.I),   # razorpay-style typed ids
    re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I),
    re.compile(r"\b[0-9a-f]{16,}\b", re.I),               # long hex blobs
    re.compile(r"\b\d{6,}\b"),                            # long numeric ids
]


def _normalize_text(text):
    t = (text or "").lower()
    for pat in _ID_PATTERNS:
        t = pat.sub("<id>", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _normalize_assets(target_assets):
    parts = []
    for a in (target_assets or []):
        if isinstance(a, dict):
            key = "|".join(str(a.get(k, "")) for k in ("service", "asset", "kind"))
        else:
            key = str(a)
        parts.append(_normalize_text(key))
    return sorted(p for p in parts if p)


def fingerprint(target_assets, claim, suspected_cause=None):
    """Stable 16-hex semantic fingerprint of (normalized claim + target assets).

    ``suspected_cause`` is accepted for call-site convenience but deliberately
    NOT hashed: the mandate defines the fingerprint over claim + target assets
    so that two phrasings of the same attack on the same assets collide.
    """
    norm_claim = _normalize_text(claim)
    norm_assets = _normalize_assets(target_assets)
    basis = norm_claim + " " + " ".join(norm_assets)
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def duplicate_of(new_fp, existing):
    """Return the hypothesis_id of the first existing record sharing ``new_fp``,
    else None. ``existing`` is an iterable of hypothesis dicts."""
    for h in existing:
        if h.get("fingerprint") == new_fp and h.get("status") != S.DEPRIORITIZED.value:
            return h.get("hypothesis_id")
    return None


# --------------------------------------------------------------------------
# Pure transition helper (operates on a plain dict record)
# --------------------------------------------------------------------------
def advance(record, to, reason=None, actor=None, blocker=None):
    """Advance ``record`` (a dict) to state ``to`` in place, enforcing legality.

    Raises LifecycleError on an illegal edge, a BLOCKED without a valid Blocker,
    or a reason-required terminal entered without a reason. Returns the record.
    """
    frm = _as_state(record.get("status", S.PROPOSED.value))
    to = _as_state(to)

    allowed = LEGAL_TRANSITIONS.get(frm, set())
    if to not in allowed:
        raise LifecycleError(
            "illegal transition %s -> %s (allowed: %s)"
            % (frm.value, to.value, sorted(s.value for s in allowed)))

    if to is S.BLOCKED:
        bl = _as_blocker(blocker if blocker is not None else record.get("blocker"))
        record["blocker"] = bl.value
    else:
        # leaving/entering a non-blocked state clears any stale blocker
        if to not in (S.BLOCKED,):
            record["blocker"] = None

    if to in REASON_REQUIRED_ON_ENTRY:
        if not reason:
            raise LifecycleError("entering %s requires a machine-readable reason" % to.value)
        record["status_reason"] = reason
        record["terminal_status_reason"] = reason
    elif reason:
        record["status_reason"] = reason

    record["status"] = to.value
    record["updated_at"] = _utc()
    hist = record.setdefault("transitions", [])
    hist.append({"from": frm.value, "to": to.value, "reason": reason,
                 "actor": actor, "blocker": record.get("blocker"), "ts": record["updated_at"]})
    return record


def _as_state(v):
    if isinstance(v, HypothesisState):
        return v
    try:
        return HypothesisState(str(v).lower())
    except ValueError:
        raise LifecycleError("unknown lifecycle state: %r" % (v,))


def _as_blocker(v):
    if v is None:
        raise LifecycleError("BLOCKED requires a blocker from the fixed enum")
    if isinstance(v, Blocker):
        return v
    try:
        return Blocker(str(v).lower())
    except ValueError:
        raise LifecycleError("invalid blocker %r (must be one of %s)"
                             % (v, [b.value for b in Blocker]))


def is_blocked(record):
    return _as_state(record.get("status", S.PROPOSED.value)) is S.BLOCKED


def is_falsified(record):
    return _as_state(record.get("status", S.PROPOSED.value)) is S.FALSIFIED


def is_terminal(record):
    return _as_state(record.get("status", S.PROPOSED.value)) in TERMINAL_STATES


def is_open(record):
    return not is_terminal(record)


def validate_falsification(record):
    """A hypothesis may only be FALSIFIED with real evidence from an executed
    experiment. Blocked != falsified. Returns (ok, reason)."""
    if not is_falsified(record):
        return True, None
    if not record.get("evidence_refs"):
        return False, "falsified_without_evidence_refs"
    executed = any(t.get("to") == S.EXECUTING.value for t in record.get("transitions", []))
    if not executed and not record.get("experiment_executed"):
        return False, "falsified_without_executed_experiment"
    return True, None


def highest_priority_open(hyps):
    """Return the highest-priority OPEN hypothesis (max priority; ties broken by
    earliest created_at). ``hyps`` is an iterable of dicts. None if all resolved.
    """
    opens = [h for h in hyps if is_open(h)]
    if not opens:
        return None
    return max(opens, key=lambda h: (Priority.coerce(h.get("priority")),
                                     _neg_created(h)))


def _neg_created(h):
    # earliest created_at wins the tie -> sort ascending on created_at, so we
    # negate by using a reversed comparable. created_at is an ISO string.
    return _InverseStr(h.get("created_at") or "")


class _InverseStr:
    __slots__ = ("s",)

    def __init__(self, s):
        self.s = s

    def __lt__(self, other):
        return self.s > other.s

    def __eq__(self, other):
        return self.s == other.s


def rollup_by_state(hyps):
    """Count hypotheses by lifecycle state. BLOCKED is counted separately and
    NEVER folded into falsified/safe."""
    counts = {s.value: 0 for s in HypothesisState}
    for h in hyps:
        st = _as_state(h.get("status", S.PROPOSED.value)).value
        counts[st] = counts.get(st, 0) + 1
    return counts


# --------------------------------------------------------------------------
# Legacy projection (old free-text status -> v2 state)
# --------------------------------------------------------------------------
_LEGACY_MAP = {
    "proposed": S.PROPOSED, "testing": S.EXECUTING, "supported": S.SUPPORTED,
    "confirmed": S.SUPPORTED, "refuted": S.FALSIFIED, "failed": S.FALSIFIED,
    "rejected": S.FALSIFIED, "claimed": S.CLAIMED,
}


def project_legacy(hyp):
    """Project a legacy hypotheses.jsonl record onto a v2-shaped dict so old and
    mixed runs render under the new lifecycle."""
    status = (hyp.get("status") or "proposed").lower()
    state = _LEGACY_MAP.get(status, S.PROPOSED)
    claim = hyp.get("claim")
    assets = hyp.get("assets") or []
    return {
        "hypothesis_id": hyp.get("hypothesis_id"),
        "fingerprint": fingerprint(assets, claim),
        "priority": Priority.MEDIUM,
        "target_assets": [{"asset": a, "kind": "unknown"} if not isinstance(a, dict) else a
                          for a in assets],
        "claim": claim,
        "suspected_cause": hyp.get("suspected_weakness"),
        "preconditions": [],
        "expected_observation": hyp.get("expected_impact"),
        "experiment": {"raw": hyp.get("planned_experiment")} if hyp.get("planned_experiment") else None,
        "evidence_refs": hyp.get("evidence_refs") or [],
        "owner": "legacy",
        "status": state.value,
        "status_reason": "projected_from_legacy",
        "terminal_status_reason": None,
        "blocker": None,
        "next_best_action": None,
        "created_at": hyp.get("ts"),
        "updated_at": hyp.get("ts"),
        "_legacy": True,
    }


# --------------------------------------------------------------------------
# Durable manager over a CampaignStore
# --------------------------------------------------------------------------
class HypothesisManager:
    """Durable, restart-safe hypothesis lifecycle over a CampaignStore.

    Records are appended to ``hypotheses_v2.jsonl`` (base + _update records,
    folded last-writer-wins by id), mirroring the legacy discipline so a killed
    process recovers full lifecycle state from disk.
    """

    KIND = "hypotheses_v2"

    def __init__(self, store):
        self.store = store
        store.ensure_kind(self.KIND)

    # -- proposal / dedup ---------------------------------------------------
    def propose(self, claim, target_assets=None, suspected_cause=None,
                expected_observation=None, preconditions=None, experiment=None,
                success_condition=None, stop_condition=None, max_actions=25,
                max_duration_s=900, priority=None, owner=None, next_best_action=None):
        target_assets = target_assets or []
        fp = fingerprint(target_assets, claim)
        existing = list(self.latest().values())
        dup = duplicate_of(fp, existing)
        if dup:
            self.store.event("duplicate_suppressed", fingerprint=fp,
                             duplicate_of=dup, claim=(claim or "")[:200])
            return dup, True   # (existing id, was_duplicate)

        hid = "H-%03d" % (len(existing) + 1)
        now = _utc()
        rec = {
            "hypothesis_id": hid,
            "fingerprint": fp,
            "priority": Priority.coerce(priority),
            "target_assets": _typed_assets(target_assets),
            "claim": claim,
            "suspected_cause": suspected_cause,
            "preconditions": _normalize_preconditions(preconditions),
            "expected_observation": expected_observation,
            "experiment": experiment,
            "success_condition": success_condition,
            "stop_condition": stop_condition,
            "max_actions": max_actions,
            "max_duration_s": max_duration_s,
            "evidence_refs": [],
            "owner": owner,
            "status": S.PROPOSED.value,
            "status_reason": None,
            "terminal_status_reason": None,
            "blocker": None,
            "next_best_action": next_best_action,
            "experiment_executed": False,
            "transitions": [],
            "created_at": now,
            "updated_at": now,
        }
        self.store._append(self.KIND, rec)
        return hid, False

    # -- lifecycle ----------------------------------------------------------
    def get(self, hid):
        return self.latest().get(hid)

    def advance(self, hid, to, reason=None, actor=None, blocker=None,
                evidence_refs=None, next_best_action=None):
        rec = self.get(hid)
        if rec is None:
            raise LifecycleError("unknown hypothesis %s" % hid)
        if evidence_refs:
            merged = list(rec.get("evidence_refs") or [])
            for r in evidence_refs:
                if r not in merged:
                    merged.append(r)
            rec["evidence_refs"] = merged
        advance(rec, to, reason=reason, actor=actor, blocker=blocker)
        if next_best_action is not None:
            rec["next_best_action"] = next_best_action
        # falsification integrity guard
        ok, why = validate_falsification(rec)
        if not ok:
            raise LifecycleError("cannot falsify %s: %s" % (hid, why))
        update = {k: rec[k] for k in ("hypothesis_id", "status", "status_reason",
                                      "terminal_status_reason", "blocker",
                                      "evidence_refs", "next_best_action",
                                      "transitions", "updated_at")}
        update["_update"] = True
        self.store._append(self.KIND, update)
        self.store.event("lifecycle_transition", hypothesis_id=hid, to=rec["status"],
                         reason=reason, blocker=rec.get("blocker"), actor=actor)
        return rec

    def mark_executed(self, hid):
        rec = self.get(hid)
        if rec is None:
            raise LifecycleError("unknown hypothesis %s" % hid)
        self.store._append(self.KIND, {"hypothesis_id": hid, "experiment_executed": True,
                                       "_update": True, "updated_at": _utc()})

    def set_priority(self, hid, priority):
        self.store._append(self.KIND, {"hypothesis_id": hid,
                                       "priority": Priority.coerce(priority),
                                       "_update": True, "updated_at": _utc()})

    def deprioritize(self, hid, reason, actor="coordinator"):
        if not reason:
            raise LifecycleError("deprioritize requires a machine-readable reason")
        return self.advance(hid, S.DEPRIORITIZED, reason=reason, actor=actor)

    # -- reads --------------------------------------------------------------
    def latest(self):
        merged = {}
        order = []
        for rec in self.store._read_all(self.KIND):
            hid = rec.get("hypothesis_id")
            if hid is None:
                continue
            if hid not in merged:
                merged[hid] = {}
                order.append(hid)
            merged[hid].update({k: v for k, v in rec.items() if k != "_update"})
        return {hid: merged[hid] for hid in order}

    def all(self):
        return list(self.latest().values())

    def highest_priority_open(self):
        return highest_priority_open(self.all())

    def rollup(self):
        return rollup_by_state(self.all())


def _typed_assets(assets):
    out = []
    for a in assets:
        if isinstance(a, dict):
            out.append(a)
        else:
            out.append({"asset": str(a), "kind": "unknown"})
    return out


def _normalize_preconditions(preconds):
    if not preconds:
        return []
    out = []
    for p in preconds:
        if isinstance(p, dict):
            label = (p.get("label") or p.get("provenance") or "assumed").lower()
            if label not in ("verified", "observed", "assumed"):
                label = "assumed"
            out.append({"text": p.get("text"), "label": label,
                        "evidence_ref": p.get("evidence_ref")})
        else:
            out.append({"text": str(p), "label": "assumed", "evidence_ref": None})
    return out
