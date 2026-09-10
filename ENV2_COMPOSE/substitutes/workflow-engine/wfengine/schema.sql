-- workflow-engine durable schema (SQLite, WAL).
-- High-fidelity RECONSTRUCTION of the Razorpay Workflows/Cadence approval
-- engine. See FIDELITY.md for what is real vs reconstructed.
--
-- Every state-affecting mutation is recorded in audit_log, and workflow rows
-- carry a monotonic `version` for optimistic-concurrency / stale-decision
-- protection. The database is the single source of truth so the service is
-- fully restart-recoverable.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS orgs (
    org_id      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL
);

-- An actor is any identity that can authenticate: a requester (maker),
-- approver (checker), operator, or the service callback identity. Roles are a
-- comma-separated set drawn from {requester,approver,operator,service,admin}.
-- token_hash is sha256(token); the raw token is never stored.
CREATE TABLE IF NOT EXISTS actors (
    actor_id    TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES orgs(org_id),
    name        TEXT NOT NULL,
    roles       TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_actors_token ON actors(token_hash);
CREATE INDEX IF NOT EXISTS idx_actors_org ON actors(org_id);

-- An approval policy binds an org (+ optional entity_type) to the rules the
-- engine enforces: how many distinct approvals are required, whether
-- maker!=checker separation is enforced, the set of eligible approver ids, and
-- how long a pending workflow lives before it expires.
CREATE TABLE IF NOT EXISTS policies (
    policy_id           TEXT PRIMARY KEY,
    org_id              TEXT NOT NULL REFERENCES orgs(org_id),
    entity_type         TEXT NOT NULL DEFAULT 'payout',
    required_approvals  INTEGER NOT NULL DEFAULT 1,
    separation          INTEGER NOT NULL DEFAULT 1,   -- 1 = enforce maker!=checker
    eligible_approvers  TEXT NOT NULL DEFAULT '',     -- csv of actor_id; '' = any approver role in org
    expiry_seconds      INTEGER NOT NULL DEFAULT 86400,
    created_at          REAL NOT NULL,
    UNIQUE(org_id, entity_type)
);

-- A workflow instance. state in
--   pending -> approved | rejected | cancelled | expired  (all terminal but approved is only terminal after callback ack)
CREATE TABLE IF NOT EXISTS workflows (
    id                  TEXT PRIMARY KEY,       -- wfl_<hex>
    org_id              TEXT NOT NULL REFERENCES orgs(org_id),
    entity_type         TEXT NOT NULL DEFAULT 'payout',
    entity_id           TEXT NOT NULL,          -- payout id
    creator_id          TEXT NOT NULL,          -- the maker
    owner_id            TEXT NOT NULL,          -- merchant / owner (== org for these tests)
    amount              INTEGER NOT NULL DEFAULT 0,
    state               TEXT NOT NULL DEFAULT 'pending',
    version             INTEGER NOT NULL DEFAULT 1,
    required_approvals  INTEGER NOT NULL DEFAULT 1,
    separation          INTEGER NOT NULL DEFAULT 1,
    eligible_approvers  TEXT NOT NULL DEFAULT '',
    approvals_count     INTEGER NOT NULL DEFAULT 0,
    idempotency_key     TEXT,                   -- create-time dup-request key
    callback_details    TEXT NOT NULL DEFAULT '{}',
    created_at          REAL NOT NULL,
    expires_at          REAL NOT NULL,
    decided_at          REAL,
    terminal            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_wf_entity ON workflows(org_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_wf_idem ON workflows(org_id, idempotency_key);
CREATE INDEX IF NOT EXISTS idx_wf_state ON workflows(state);

-- Each recorded approval by a distinct approver. UNIQUE(workflow,approver)
-- makes duplicate approvals idempotent at the storage layer in the fixed
-- reference (a mutant may bypass this).
CREATE TABLE IF NOT EXISTS approvals (
    workflow_id TEXT NOT NULL REFERENCES workflows(id),
    approver_id TEXT NOT NULL,
    org_id      TEXT NOT NULL,
    created_at  REAL NOT NULL,
    PRIMARY KEY (workflow_id, approver_id)
);

-- Append-only audit trail.
CREATE TABLE IF NOT EXISTS audit_log (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT,
    org_id      TEXT,
    actor_id    TEXT,
    action      TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '{}',
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_wf ON audit_log(workflow_id);

-- Callback delivery attempts to the payout side (for retry + idempotency).
CREATE TABLE IF NOT EXISTS callbacks (
    delivery_id     TEXT PRIMARY KEY,           -- idempotency key for the delivery
    workflow_id     TEXT NOT NULL,
    kind            TEXT NOT NULL,              -- approve | reject
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_status     INTEGER,
    delivered       INTEGER NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    UNIQUE(workflow_id, kind)
);
