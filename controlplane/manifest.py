"""The immutable campaign manifest.

The human supplies scope and constraints only. The manifest pins everything the
campaign is bound to and is content-addressed: its ``campaign_id`` is derived
from the manifest body, so the same inputs reproduce the same identity and any
tampering changes the id. The human never assigns a target, vulnerability class
or attack path — only ``mode`` and a broad ``mandate``.
"""
import time

from archkit import canon

# Campaign modes are coarse postures, never a target or vuln class.
MODES = ("recon_and_probe", "boundary_stress", "accounting_integrity", "broad_autonomous")

DEFAULT_BUDGETS = {
    "max_actions": 4000,          # broker/tool actions across the whole campaign
    "max_model_calls": 6000,
    "max_wall_seconds": 6 * 3600,
    "max_usd": None,              # optional hard cost ceiling
    "planning_cycles_min": 3,
}

DEFAULT_SAFETY = {
    # the broker boundary is authoritative; these are declared policy the plane
    # enforces on top (never weakens the twin).
    "tool_policy": "typed_broker_only",
    "attacker_denied_fragments": ["/_arena", "/twirp/", "/metrics", "/debug"],
    "no_direct_docker": True,
    "no_host_files": True,
    "no_self_verification": True,   # a claimant may never verify its own claim
    "isolation": "one_broker_per_assigned_twin",
}


def build_manifest(mandate, snapshot_id, twins, model_pool, mode="broad_autonomous",
                   budgets=None, safety=None, tool_policy=None, human="operator",
                   notes=None, created_at=None):
    """Assemble and content-address a campaign manifest.

    twins: list of {"instance_id","runtime_instance_id","profile","seed",
                    "starting_access_profile","backend"} — the assigned M9 twins.
    model_pool: list of model ids the plane may route among.
    """
    if mode not in MODES:
        raise ValueError("unknown campaign mode %r (modes: %s)" % (mode, ", ".join(MODES)))
    if not twins:
        raise ValueError("a campaign must be assigned at least one twin")
    if not model_pool:
        raise ValueError("a campaign needs a non-empty model pool")
    b = dict(DEFAULT_BUDGETS)
    b.update(budgets or {})
    s = dict(DEFAULT_SAFETY)
    s.update(safety or {})
    if tool_policy:
        s["tool_policy"] = tool_policy

    body = {
        "schema": "m10.manifest.1",
        "mode": mode,
        "mandate": mandate.strip(),
        "architecture_snapshot_id": snapshot_id,
        "twins": [_norm_twin(t) for t in twins],
        "model_pool": list(model_pool),
        "budgets": b,
        "safety": s,
        "human": human,
        "notes": notes,
        "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    cid = canon.content_id(body)
    body["manifest_content_id"] = cid
    body["campaign_id"] = "camp-" + cid[:12]
    return body


def _norm_twin(t):
    return {
        "instance_id": t["instance_id"],
        "runtime_instance_id": t.get("runtime_instance_id"),
        "profile": t.get("profile"),
        "seed": t.get("seed"),
        "starting_access_profile": t.get("starting_access_profile", "merchant_ordinary"),
        "backend": t.get("backend", "colima"),
        "role": t.get("role"),
    }


def validate(manifest):
    """Re-derive the content id and confirm the manifest was not tampered with."""
    body = {k: v for k, v in manifest.items()
            if k not in ("manifest_content_id", "campaign_id")}
    cid = canon.content_id(body)
    ok = cid == manifest.get("manifest_content_id")
    return ok, cid
