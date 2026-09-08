#!/usr/bin/env python3
"""bulk_client — stdlib helper for driving batch-sim from an M6 journey.

batch-sim is a HIGH-FIDELITY REPLACEMENT for razorpay/batch's payout batch
types (ENV2_COMPOSE/substitutes/batch-sim/CONTRACT.md). It is NOT the real Batch
service: read that CONTRACT before treating anything observed through this
helper as a production finding.

WHAT THIS DOES
--------------
batch-sim listens on the `rzp-arena` network only (compose block: no `ports:`),
exactly like monolith-stub, workflow-sim and the rest of the substitutes. This
module therefore reaches it the same way every other in-arena probe in RED_LOOP
does: `red_loop.provisioner_direct.arena_http`, which shells out to a throwaway
`curlimages/curl` container attached to `provisioner.arena_network()`
(provisioner_direct.py:131-151). No host port is opened and no new mechanism is
introduced.

    from RED_LOOP.m6.journeys import bulk_client as bc

    batch = bc.create_payout_batch(rows, merchant_id="ARENAM00000002",
                                   creator_id="usr_ARENA0001")
    final = bc.wait_batch(batch["id"])            # -> COMPLETED / FAILED
    ids   = bc.payout_ids(final["id"])            # payout ids PS actually created

    appr  = bc.create_approval_batch(ids, merchant_id="ARENAM00000002")
    bc.wait_batch(appr["id"])

Every call returns the parsed JSON body and raises `BatchSimError` on a non-2xx
or a transport failure, with the raw text attached.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
* It does not start, stop, restart or rebuild anything in the arena.
* It does not reach batch-sim from the attacker surface. batch-sim is a
  control-plane actor (it stands in for Batch + the monolith hop); the attacker
  broker's allow-list (RED_LOOP/red_loop/config.py ATTACKER_ALLOWED_PREFIXES)
  does not and must not include it.
* It does not poke `POST /v1/cron/process_batch_submitted_payouts`. PS may leave
  bulk-created payouts in `batch_submitted`
  (payouts internal/routing/router/cron_routes.go:46-47); the arena's own
  cron-driver already drives that route every 300 s
  (ENV2_COMPOSE/scripts/cron-driver/driver.py:44). A journey that wants a
  deterministic result should call `nudge_batch_submitted_cron()` below rather
  than sleep for five minutes.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from RED_LOOP.red_loop import config                      # noqa: E402
from RED_LOOP.red_loop import provisioner as P            # noqa: E402
from RED_LOOP.red_loop.provisioner_direct import arena_http  # noqa: E402

ARENA_YAML = config.ENV2 / "config" / "arena.yaml"

# batch types batch-sim implements (CONTRACT.md section 2)
TYPE_PAYOUT = "payout"
TYPE_PAYOUT_APPROVAL = "payout_approval"
V2_BENE_TYPES = (
    "payouts_amazonpay_bene_details_process",
    "payouts_amazonpay_bene_id_process",
    "payouts_bank_transfer_bene_details_process",
    "payouts_bank_transfer_bene_id_process",
    "payouts_upi_bene_details_process",
    "payouts_upi_bene_id_process",
)

TERMINAL = ("COMPLETED", "FAILED", "CANCELLED", "VALIDATION_FAILED")


class BatchSimError(RuntimeError):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body


# --------------------------------------------------------------------------
# address resolution
# --------------------------------------------------------------------------
def _from_arena_yaml():
    """Read `batch_sim: {host: batch-sim, port: 8094}` out of config/arena.yaml.

    arena.yaml is the single source of truth for arena endpoints, but RED_LOOP
    is stdlib-only (no PyYAML), so this is a targeted regex over the one line we
    need rather than a YAML parse. Falls back to the compose defaults when the
    coordinator has not yet pasted the snippet
    (substitutes/batch-sim/arena-yaml-snippet.txt)."""
    try:
        text = ARENA_YAML.read_text()
    except OSError:
        return None
    m = re.search(r"^\s*batch_sim:\s*\{[^}]*host:\s*([A-Za-z0-9_.-]+)[^}]*"
                  r"port:\s*(\d+)", text, re.M)
    if m:
        return m.group(1), int(m.group(2))
    return None


def base_url():
    """In-arena base URL for batch-sim. Override with BATCH_SIM_URL."""
    override = os.environ.get("BATCH_SIM_URL")
    if override:
        return override.rstrip("/")
    resolved = _from_arena_yaml() or ("batch-sim", 8094)
    return "http://%s:%d" % resolved


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------
def _call(method, path, body=None, headers=None, timeout=30, expect=(200,)):
    url = base_url() + path
    status, text = arena_http(method, url, body=body, headers=headers, timeout=timeout)
    if status == "ERR":
        raise BatchSimError("batch-sim unreachable at %s (%s). Is the "
                            "`substitutes` profile up and the compose block pasted?"
                            % (url, (text or "")[:200]), status=status, body=text)
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        parsed = {"raw": text}
    if expect and status not in expect:
        raise BatchSimError("%s %s -> %s: %s" % (method, path, status, str(parsed)[:400]),
                            status=status, body=parsed)
    return parsed


def health():
    """GET /_arena/health -- also tells you whether the passport signer loaded.
    `passport != "ready"` means batch-sim will omit X-Passport-JWT-V1 and the
    REAL payouts-api will 400 every bulk create."""
    return _call("GET", "/_arena/health")


def available():
    try:
        health()
        return True
    except BatchSimError:
        return False


# --------------------------------------------------------------------------
# row builders
# --------------------------------------------------------------------------
def payout_row(account_number, amount, fund_account_id, mode="IMPS",
               purpose="payout", currency="INR", narration=None, reference_id=None,
               contact_name=None, notes=None):
    """One `payout` row, using the REAL payout.json template headers
    (batch src/main/resources/payout.json `columnNames`). `amount` is in paise
    and is sent as a string, matching dtos.BulkPayoutCreateRequest, whose fields
    are all strings (bulkPayoutCreateRequest.go:18-28)."""
    row = {
        "RazorpayX Account Number": str(account_number),
        "Payout Amount": str(amount),
        "Payout Currency": currency,
        "Payout Mode": mode,
        "Payout Purpose": purpose,
        "Fund Account Id": fund_account_id,
    }
    if narration is not None:
        row["Payout Narration"] = narration
    if reference_id is not None:
        row["Payout Reference Id"] = reference_id
    if contact_name is not None:
        row["Contact Name"] = contact_name
    for key, value in (notes or {}).items():
        row["notes[%s]" % key] = str(value)
    return row


def approval_row(payout_id, action="A", account_number=None, amount=None,
                 currency=None, mode=None, purpose=None, status=None,
                 fund_account_id=None, contact_id=None, contact_name=None):
    """One `payout_approval` row, using the REAL payout_approval.json headers.
    `action` is "A" (approve) or "R" (reject) -- the literal values the
    "Approve (A) / Reject (R) payout" column carries."""
    row = {
        "Approve (A) / Reject (R) payout": action,
        "payout_id (do not edit)": payout_id,
    }
    optional = (
        ("account_number (do not edit)", account_number),
        ("amount(Rupees) (do not edit)", amount),
        ("currency (do not edit)", currency),
        ("mode (do not edit)", mode),
        ("purpose (do not edit)", purpose),
        ("status (do not edit)", status),
        ("fund_account_id (do not edit)", fund_account_id),
        ("contact_id (do not edit)", contact_id),
        ("contact_name (do not edit)", contact_name),
    )
    for header, value in optional:
        if value is not None:
            row[header] = str(value)
    return row


# --------------------------------------------------------------------------
# batch operations
# --------------------------------------------------------------------------
def create_batch(batch_type_id, rows, merchant_id, creator_id="", creator_type="user",
                 name=None, settings=None, mode="live", version="2.0"):
    """POST /v1/batches. Headers mirror the real Batch create
    (BatchController.java:48-59 + HeaderConstant.java:9-13): X-Entity-Id is the
    merchant, X-Creator-Id/X-Creator-Type the acting user. Returns the Batch
    entity; processing starts immediately in batch-sim's worker."""
    if not merchant_id:
        raise BatchSimError("merchant_id is required (sent as X-Entity-Id)")
    if not rows:
        raise BatchSimError("no rows")
    body = {
        "batch_type_id": batch_type_id,
        "name": name or ("m6-%s-%d" % (batch_type_id, int(time.time()))),
        "version": version,
        "mode": mode,
        "settings": settings or {},
        "rows": list(rows),
    }
    headers = {
        "X-Entity-Id": merchant_id,
        "X-Creator-Id": creator_id or "",
        "X-Creator-Type": creator_type,
        "mode": mode,
    }
    return _call("POST", "/v1/batches", body=body, headers=headers)


def create_payout_batch(rows, merchant_id, creator_id="", batch_type_id=TYPE_PAYOUT,
                        settings=None, name=None, mode="live"):
    """Bulk payout creation. `rows` are `payout_row(...)` dicts (or raw dicts
    keyed by the payout.json template headers / their snake_case aliases).

    batch-sim chunks them 5 at a time (payout.json "bulkSize": 5) into
    POST {payouts-api}/v1/payouts/bulk with idempotency key
    "batch_"+<entry id> per row."""
    if batch_type_id not in (TYPE_PAYOUT,) + V2_BENE_TYPES:
        raise BatchSimError("batch_type_id %r is not a payout-create type" % batch_type_id)
    return create_batch(batch_type_id, rows, merchant_id, creator_id=creator_id,
                        settings=settings, name=name, mode=mode)


def create_approval_batch(payout_ids, merchant_id, creator_id="", action="A",
                          user_comment="Bulk approved", queue_if_low_balance=False,
                          settings=None, name=None, mode="live"):
    """Bulk approval. `payout_ids` may be a list of ids (all get `action`) or a
    list of `approval_row(...)` dicts for per-row actions.

    batch-sim calls the REAL PS internal route per row,
    POST {payouts-api}/v1/payouts/payouts_internal/{id}/{approve,reject}, with
    the workflow credential (200/201/409 all count as success)."""
    rows = []
    for item in payout_ids:
        rows.append(item if isinstance(item, dict) else approval_row(item, action=action))
    merged = {"user_comment": user_comment,
              "queue_if_low_balance": bool(queue_if_low_balance)}
    merged.update(settings or {})
    return create_batch(TYPE_PAYOUT_APPROVAL, rows, merchant_id, creator_id=creator_id,
                        settings=merged, name=name, mode=mode)


def get_batch(batch_id):
    """GET /v1/batches/{id} -- the Batch entity (Batch.java:38-129) plus the
    derived, NON-SOURCE `outcome` field
    (created|processing|processed|partially_processed|failed)."""
    return _call("GET", "/v1/batches/%s" % batch_id)


def get_entries(batch_id, status=None, limit=1000):
    """GET /v1/batches/{id}/entries -- BatchEntry rows with the PS response
    recorded per row in `response_data`."""
    path = "/v1/batches/%s/entries?limit=%d" % (batch_id, limit)
    if status:
        path += "&status=%s" % status
    return _call("GET", path)["entries"]


def wait_batch(batch_id, timeout=180, poll=2.0):
    """Poll until the batch reaches a terminal BatchStatus (COMPLETED / FAILED /
    CANCELLED / VALIDATION_FAILED) or `timeout` seconds elapse."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = get_batch(batch_id)
        if last.get("status") in TERMINAL:
            return last
        time.sleep(poll)
    raise BatchSimError("batch %s did not finish in %ss (last=%s)"
                        % (batch_id, timeout, json.dumps(last or {})[:300]),
                        body=last)


def payout_ids(batch_id):
    """Payout ids PS actually created, in row order. Only PROCESSED rows of a
    payout-create batch have one."""
    out = []
    for entry in get_entries(batch_id):
        try:
            data = json.loads(entry.get("response_data") or "{}")
        except (ValueError, TypeError):
            continue
        pid = data.get("payout_id") or (data.get("item") or {}).get("id")
        if pid:
            out.append(pid)
    return out


def entry_errors(batch_id):
    """[{seq_number, idempotency_key, http_status_code, code, description}] for
    every FAILED row -- what a journey should attach as evidence."""
    errors = []
    for entry in get_entries(batch_id, status="FAILED"):
        try:
            data = json.loads(entry.get("response_data") or "{}")
        except (ValueError, TypeError):
            data = {}
        err = (data.get("item") or {}).get("error") or \
              (data.get("response") or {}).get("error") or {}
        errors.append({
            "seq_number": entry.get("seq_number"),
            "idempotency_key": entry.get("idempotency_key"),
            "http_status_code": data.get("http_status_code"),
            "code": err.get("code"),
            "description": err.get("description"),
        })
    return errors


def reprocess(batch_id):
    """Control plane: resend EVERY row (including already-PROCESSED ones) with
    its original idempotency key. Used to demonstrate PS's duplicate-key path
    (bulkPayoutsProcessor/core.go:352-390) -- it must create no new payouts."""
    return _call("POST", "/_arena/batches/%s/reprocess" % batch_id, body={})


def process(batch_id):
    """Re-trigger a batch for its not-yet-PROCESSED rows
    (BatchController.java:74-86 triggerBatch)."""
    return _call("POST", "/v1/batches/%s/process" % batch_id, body={})


def arena_batch(batch_id):
    """GET /_arena/batches/{id} -- the batch WITH all its entries and recorded
    PS responses, in one call. Evidence plane."""
    return _call("GET", "/_arena/batches/%s" % batch_id)


def arena_calls():
    """GET /_arena/calls -- the last 200 outbound PS calls (url, attempt,
    status, idempotency keys). The primary evidence artefact for a bulk journey."""
    return _call("GET", "/_arena/calls")["calls"]


def fail_next(count=1, status=503, scope="create", reason="m6 journey", drop=False):
    """Control plane: short-circuit the next `count` outbound PS calls with a
    synthetic status (or a connection failure with drop=True). Bounded and
    local; the destination never changes. `{"clear": true}` via clear_faults()."""
    body = {"count": count, "scope": scope, "reason": reason}
    if drop:
        body["drop"] = True
    else:
        body["status"] = status
    return _call("POST", "/_arena/fail-next", body=body)


def clear_faults():
    return _call("POST", "/_arena/fail-next", body={"clear": True})


# --------------------------------------------------------------------------
# post-create cron nudge
# --------------------------------------------------------------------------
def nudge_batch_submitted_cron(timeout=30):
    """POST {payouts-api}/v1/cron/process_batch_submitted_payouts.

    PS may park bulk-created payouts in `batch_submitted` and drain them from a
    cron (cron_routes.go:46-47 -> InitiateBatchSubmittedPayouts ->
    job/batch_submitted_merchants.go). The arena's cron-driver already calls this
    every 300 s (scripts/cron-driver/driver.py:44); this makes a journey
    deterministic instead of waiting for that tick.

    Uses the fast-cron credential the cron-driver itself uses
    (CRON_BASIC_AUTH_USER "fast_cron" + secrets/auth_fastcron_payouts.txt)."""
    password = P._pw("auth_fastcron_payouts.txt")
    basic = "fast_cron:%s" % password if password else None
    status, text = arena_http(
        "POST", "http://payouts-api:9400/v1/cron/process_batch_submitted_payouts",
        body={}, basic=basic, timeout=timeout)
    return {"status": status, "body": text}


def summarise(batch_id):
    """One compact dict for a journey report."""
    batch = get_batch(batch_id)
    return {
        "id": batch["id"],
        "batch_type_id": batch["batch_type_id"],
        "entity_id": batch["entity_id"],
        "status": batch["status"],
        "outcome": batch["outcome"],
        "total_count": batch["total_count"],
        "success_count": batch["success_count"],
        "failure_count": batch["failure_count"],
        "attempts": batch["attempts"],
        "payout_ids": payout_ids(batch_id),
        "errors": entry_errors(batch_id),
    }


if __name__ == "__main__":
    # Smoke check: is batch-sim reachable inside the arena?
    try:
        print(json.dumps(health(), indent=2))
    except BatchSimError as exc:
        print("batch-sim NOT reachable: %s" % exc)
        raise SystemExit(1)
