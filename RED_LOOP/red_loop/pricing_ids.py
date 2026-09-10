"""Fail-closed identifier reservations for synthetic pricing provisioning.

The substitute returns a plan ID as payouts.pricing_rule_id (CHAR(14));
no separate rule-level ID is seeded. This is a fidelity limitation.
"""
import fcntl
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def validate_pricing_id(value):
    """Validate the narrowest destination column before any seed writes."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9]{1,14}", value):
        raise ValueError("pricing identifier must be 1..14 ASCII alphanumeric characters")
    return value


def bounded_plan_id(merchant_id):
    # Preserve the existing bounded mapping, including its output for retries.
    return validate_pricing_id("ARENAPLAN" + hashlib.sha256(merchant_id.encode()).hexdigest()[:5].upper())


def reserve_plan_id(seed_dir, run_id, merchant_id):
    """Reserve before seeding; collisions fail without changing the registry.

    One lock serializes reservations across processes. Reservations survive failed
    provisioning and are idempotent. Keep this file with the arena's seed lifetime.
    Entries are run-scoped, with ownership checked across all runs in the arena.
    """
    seed_dir = Path(seed_dir)
    plan_id = bounded_plan_id(merchant_id)
    scope = hashlib.sha256(run_id.encode()).hexdigest()
    path = seed_dir / "pricing-id-reservations.json"
    with (seed_dir / "pricing-id-reservations.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        registry = json.loads(path.read_text()) if path.exists() else {}
        pricing = json.loads((seed_dir / "pricing.json").read_text())
        owners = {}

        def claim(mid, pid):
            validate_pricing_id(pid)
            if pid in owners and owners[pid] != mid:
                raise ValueError("pricing plan identifier collision; no seeding performed")
            owners[pid] = mid

        for plans in registry.values():
            for mid, pid in plans.items():
                claim(mid, pid)
        for mid, plan in pricing["plans"].items():
            claim(mid, plan["plan_id"])
            for rule in plan.get("rules", {}).values():
                if "id" in rule:
                    validate_pricing_id(rule["id"])
        claim(merchant_id, plan_id)
        existing = registry.get(scope, {}).get(merchant_id)
        if existing is not None and existing != plan_id:
            raise ValueError("pricing plan mapping changed for an existing reservation")
        registry.setdefault(scope, {})[merchant_id] = plan_id
        fd, temporary = tempfile.mkstemp(prefix=".pricing-reservation-", dir=seed_dir)
        try:
            with os.fdopen(fd, "w") as out:
                json.dump(registry, out, indent=2, sort_keys=True)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return plan_id
