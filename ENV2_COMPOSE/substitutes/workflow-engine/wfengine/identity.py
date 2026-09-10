"""Identity + provisioning for the workflow engine.

Actors authenticate with an opaque bearer token; only its sha256 is stored.
Resolving a token yields the actor's org and role set, which the decision core
uses to enforce org-boundary, maker/checker separation, and role eligibility.

Provisioning (orgs/actors/policies) is an admin-plane operation guarded by the
admin token. Campaign workers are handed actor tokens only, never the admin
token and never another org's tokens.
"""
import hashlib
import os
import secrets
import time


def hash_token(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


# M6 arena integration: payouts stores the workflow id in CHAR(14) columns
# (payouts migrations 20210824110739_create_workflow_entity_map_table.go:16 and
# 20220912025953_create_workflow_state_map_table.go:16 -- `workflow_id char(14)`),
# so an id longer than 14 chars is rejected by MySQL strict mode on the real
# payouts side. WFE_WORKFLOW_ID_LEN=14 (set by the arena compose block) makes
# workflow ids exactly that long ("wfl" + 11 hex, no underscore). Default 0 keeps
# the legacy standalone form "wfl_<16 hex>" used by the M5 suite/benchmark.
WORKFLOW_ID_LEN = int(os.environ.get("WFE_WORKFLOW_ID_LEN", "0") or 0)


def new_id(prefix: str) -> str:
    if prefix == "wfl" and WORKFLOW_ID_LEN > 0:
        body_len = max(WORKFLOW_ID_LEN - len(prefix), 4)
        return (prefix + secrets.token_hex((body_len + 1) // 2))[:WORKFLOW_ID_LEN]
    return f"{prefix}_{secrets.token_hex(8)}"


def new_token() -> str:
    return "wtk_" + secrets.token_urlsafe(24)


class Identity:
    def __init__(self, store):
        self.store = store

    # --- provisioning ------------------------------------------------------
    def create_org(self, name, org_id=None):
        """Create an org. `org_id` may be supplied explicitly (M6 arena: the org
        is the payouts merchant id, so the tenant boundary the engine enforces is
        the merchant boundary payouts' `owner_id` names); an existing explicit id
        is returned as-is (idempotent) so provisioning can be re-run safely."""
        if org_id:
            row = self.store.one("SELECT org_id,name FROM orgs WHERE org_id=?", (org_id,))
            if row:
                return {"org_id": row["org_id"], "name": row["name"], "existed": True}
            oid = org_id
        else:
            oid = new_id("org")
        self.store.exec("INSERT INTO orgs(org_id,name,created_at) VALUES(?,?,?)",
                        (oid, name, time.time()))
        return {"org_id": oid, "name": name}

    def ensure_org(self, org_id, name=None):
        """Idempotently make `org_id` known (service-plane create path)."""
        return self.create_org(name or org_id, org_id=org_id)

    def create_actor(self, org_id, name, roles):
        if not self.store.one("SELECT 1 FROM orgs WHERE org_id=?", (org_id,)):
            raise KeyError("unknown org")
        aid = new_id("act")
        tok = new_token()
        roles_csv = ",".join(sorted(set(roles)))
        self.store.exec(
            "INSERT INTO actors(actor_id,org_id,name,roles,token_hash,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (aid, org_id, name, roles_csv, hash_token(tok), time.time()),
        )
        # token returned exactly once, at creation
        return {"actor_id": aid, "org_id": org_id, "name": name,
                "roles": roles_csv.split(","), "token": tok}

    def set_policy(self, org_id, entity_type, required_approvals,
                   separation, eligible_approvers, expiry_seconds):
        pid = new_id("pol")
        self.store.exec(
            "INSERT INTO policies(policy_id,org_id,entity_type,required_approvals,"
            "separation,eligible_approvers,expiry_seconds,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)"
            " ON CONFLICT(org_id,entity_type) DO UPDATE SET"
            "  required_approvals=excluded.required_approvals,"
            "  separation=excluded.separation,"
            "  eligible_approvers=excluded.eligible_approvers,"
            "  expiry_seconds=excluded.expiry_seconds",
            (pid, org_id, entity_type, int(required_approvals),
             1 if separation else 0, ",".join(eligible_approvers or []),
             int(expiry_seconds), time.time()),
        )
        return self.get_policy(org_id, entity_type)

    def get_policy(self, org_id, entity_type="payout"):
        row = self.store.one(
            "SELECT * FROM policies WHERE org_id=? AND entity_type=?",
            (org_id, entity_type))
        if not row:
            return None
        return dict(row)

    # --- resolution --------------------------------------------------------
    def resolve(self, token):
        """token -> actor dict, or None. Constant-ish work regardless of hit."""
        if not token:
            return None
        row = self.store.one("SELECT * FROM actors WHERE token_hash=?",
                             (hash_token(token),))
        if not row:
            return None
        d = dict(row)
        d["roles"] = d["roles"].split(",") if d["roles"] else []
        return d
