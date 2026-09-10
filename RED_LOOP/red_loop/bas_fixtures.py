"""Bank-account-statement (BAS) ingestion fixtures for Milestone 4 (T10).

Drives the REAL payouts worker `rbl_banking_account_statement` (accepted payouts
binary, unchanged) through its real inputs:

  banking_account_statement_details row   (the monolith normally inserts it into
                                           the PS DB, Details/Core.php:219-237;
                                           here the provisioner does -- D-006)
  cron route  POST /v1/cron/banking_account_statement/fetch/initiate {"channel":"rbl"}
                                          (FastCron credential; cron-driver job
                                           bas_fetch_initiate, or direct HTTP here)
  mozart-sim  POST /razorpayx/rbl/v2/account_statement
                                          (substitute bank: statement rows are
                                           queued per account via /_arena/statement)
  ART route   POST /v1/banking_account_statement/process/batch
                                          (REAL PS route, cred.API Basic auth,
                                           body per T03 Finding 4(c))

Every DB read/write goes through `docker exec` on the arena's mysql/postgres
containers (they sit on an internal network). Stdlib only (D-005). Container
names and secrets come from provisioner.cname()/provisioner._pw() so a
disposable instance can be targeted the same way (ARENA_COMPOSE_PROJECT).

Never touches ARENAM00000001/2/3 unless a caller passes such a descriptor
explicitly (then only ADDITIVE rows are written: a BASD row keyed by the
fixture's account number; flagged in the T10 handoff).
"""
import base64
import datetime
import json
import re
import subprocess
import time
import uuid

from . import config
from . import provisioner as P

PS_API = "http://payouts-api:9400"
MOZART = "http://mozart-sim:8085"
MONOLITH = "http://monolith-stub:8080"
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
CURL_IMAGE = "curlimages/curl:latest"

# Exact dedupe tuple the REAL worker compares against existing rows
# (processor/rbl_gateway.go DeDuplicateTransactionsDb; channel added for account_type=direct).
DEDUP_KEY_FIELDS = ("account_number", "posted_date", "type", "bank_serial_number", "amount", "bank_transaction_id", "channel")

BAS_COLUMNS = ("id", "entity_id", "entity_type", "channel", "merchant_id", "account_number", "bank_transaction_id",
               "type", "utr", "amount", "currency", "description", "category", "bank_serial_number", "balance",
               "balance_currency", "transaction_date", "posted_date", "created_at", "updated_at", "gateway_ref_number")
BASD_COLUMNS = ("id", "merchant_id", "balance_id", "account_number", "channel", "status", "statement_closing_balance",
                "gateway_balance", "statement_closing_balance_change_at", "gateway_balance_change_at",
                "last_statement_attempt_at", "balance_last_fetched_at", "pagination_key", "account_type",
                "created_at", "updated_at", "metadata")
PAYOUT_COLUMNS = ("id", "status", "merchant_id", "balance_id", "amount", "utr", "transaction_id", "fts_transfer_id",
                  "gateway_ref_no", "mode", "channel", "failure_reason", "created_at", "updated_at")


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------
def _sh(s):
    return P._sh(s)


def _mysql(sql, db="payouts", service="mysql-payouts", pw_file="mysql_payouts_root_password.txt"):
    pw = P._pw(pw_file)
    out = subprocess.run(["docker", "exec", P.cname(service), "sh", "-c",
                          "mysql -uroot -p%s -N -B -e %s %s" % (_sh(pw), _sh(sql), db)],
                         capture_output=True, text=True, timeout=60)
    err = "\n".join(l for l in out.stderr.splitlines() if "Using a password" not in l)
    return out.returncode, out.stdout, err[:2000]


def _rows(sql, keys, **kw):
    rc, out, err = _mysql(sql, **kw)
    if rc != 0:
        raise RuntimeError("mysql failed: %s" % err)
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        vals = [None if v == "NULL" else v for v in line.split("\t")]
        rows.append(dict(zip(keys, vals)))
    return rows


def _psql(sql):
    pw = P._pw("postgres_ledger_password.txt")
    out = subprocess.run(["docker", "exec", P.cname("postgres-ledger"), "sh", "-c",
                          "PGPASSWORD=%s psql -U ledger -d ledger -tA -c %s" % (_sh(pw), _sh(sql))],
                         capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout.strip(), out.stderr[:1000]


def arena_http(method, url, body=None, headers=None, basic=None, timeout=30):
    """HTTP inside the arena network via a throwaway curl container. Returns (status|'ERR', text)."""
    cmd = ["docker", "run", "--rm", "--network", P.arena_network(), CURL_IMAGE, "-s", "-m", str(timeout),
           "-o", "/dev/stdout", "-w", "\n__STATUS__%{http_code}", "-X", method, url]
    if basic:
        cmd += ["-u", basic]
    for k, v in (headers or {}).items():
        cmd += ["-H", "%s: %s" % (k, v)]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    txt = out.stdout
    if "__STATUS__" not in txt:
        return "ERR", (out.stderr or txt)[:500]
    payload, code = txt.rsplit("__STATUS__", 1)
    try:
        return int(code.strip()), payload
    except ValueError:
        return "ERR", payload[:500]


def _jload(text, default=None):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def api_basic():
    """payouts [auth.api] -- the credential the monolith (and ART via the monolith) presents to PS."""
    return "api:" + P._pw("auth_api_payouts.txt")


def fastcron_basic():
    return "fast_cron:" + P._pw("auth_fastcron_payouts.txt")


def monolith_basic():
    return "rzp_live:" + P._pw("auth_monolith_shared.txt")


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def docker_logs(service, since, needles=(), limit=200):
    """Lines of a container's logs (since = docker --since value) containing every needle."""
    out = subprocess.run(["docker", "logs", "--since", since, P.cname(service)], capture_output=True, text=True, timeout=120)
    hits = []
    for line in (out.stdout + out.stderr).splitlines():
        if all(n in line for n in needles):
            hits.append(line[:1500])
    return hits[-limit:]


def log_messages(lines):
    """Distinct "message" values in a list of PS JSON log lines (order preserved)."""
    seen = []
    for l in lines:
        d = _jload(l[l.find("{"):]) if "{" in l else None
        m = (d or {}).get("message")
        if not m:   # long lines (stack traces) are truncated by docker_logs and no longer parse as JSON
            mm = re.search(r'"message":"([^"]+)"', l)
            m = mm.group(1) if mm else None
        if m and m not in seen:
            seen.append(m)
    return seen


# --------------------------------------------------------------------------
# banking_account_statement_details (BASD)
# --------------------------------------------------------------------------
def read_basd(account_number, channel="rbl"):
    rows = _rows("SELECT %s FROM banking_account_statement_details WHERE account_number='%s' AND channel='%s'"
                 % (",".join(BASD_COLUMNS), account_number, channel), BASD_COLUMNS)
    for r in rows:
        r["metadata_json"] = _jload(r["metadata"]) if r.get("metadata") else None
    return rows[0] if rows else None


def _basd_id_for(descriptor):
    ids = descriptor.get("ids") or {}
    cand = ids.get("basd_id") or descriptor.get("basd_id")
    if cand:
        return cand
    acct = str(descriptor["account_number"])
    return ("ARENABASD" + acct[-5:])[:14]


def ensure_basd_row(descriptor, fetched_till_days_ago=7, gateway_delta=-12345):
    """Insert (or repair) the `banking_account_statement_details` row for a Direct merchant so the REAL
    fetch path selects it and can build a date-window request:

      * metadata.statement_fetched_till_date = <today IST - N days> (DD-MM-YYYY). Without it the worker
        parses "" -> epoch 0 and asks the bank for 01-01-1970.. in 5 paged attempts (observed on the live
        arena for rows the Direct provisioner inserted with metadata NULL).
      * gateway_balance != statement_closing_balance (synthetic mismatch, `gateway_delta`), so the
        `balance_changed_rule` selection criterion is satisfied on every cron tick regardless of the
        `others` remainder rule (bankingAccountStatementDetails/core.go CheckIfAccountNumberSatisfiesSelectionCriteria).

    Mirrors the monolith's insert into the PS DB (api Details/Core.php:219-237 createBasDetailsInPayoutService).
    Idempotent: returns {basd_id, existed, before, after}.
    """
    acct = str(descriptor["account_number"])
    ch = str(descriptor.get("channel") or "rbl").lower()
    mid = descriptor["merchant_id"]
    bal = descriptor["balance_id"]
    opening = int(descriptor.get("opening_balance") or 10_000_000)
    ts = int(time.time())
    till = (datetime.datetime.now(IST) - datetime.timedelta(days=fetched_till_days_ago)).strftime("%d-%m-%Y")
    before = read_basd(acct, ch)
    existed = before is not None
    if not existed:
        basd_id = _basd_id_for(descriptor)
        meta = json.dumps({"statement_fetched_till_date": till})
        sql = ("INSERT INTO banking_account_statement_details (id,merchant_id,balance_id,account_number,channel,status,"
               "statement_closing_balance,gateway_balance,statement_closing_balance_change_at,gateway_balance_change_at,"
               "last_statement_attempt_at,balance_last_fetched_at,pagination_key,account_type,created_at,updated_at,metadata)"
               " VALUES ('%s','%s','%s','%s','%s','active',%d,%d,%d,%d,0,%d,NULL,'direct',%d,%d,'%s')"
               % (basd_id, mid, bal, acct, ch, opening, opening + gateway_delta, ts, ts, ts, ts, ts, meta))
        rc, _, err = _mysql(sql)
        if rc != 0:
            raise RuntimeError("BASD insert failed: " + err)
    else:
        basd_id = before["id"]
        meta = before.get("metadata_json") or {}
        if not meta.get("statement_fetched_till_date"):
            meta["statement_fetched_till_date"] = till
            rc, _, err = _mysql("UPDATE banking_account_statement_details SET metadata='%s', updated_at=%d WHERE id='%s'"
                                % (json.dumps(meta), ts, basd_id))
            if rc != 0:
                raise RuntimeError("BASD metadata update failed: " + err)
        make_basd_selectable(acct, ch, gateway_delta)
    after = read_basd(acct, ch)
    return {"basd_id": basd_id, "existed": existed, "before": before, "after": after, "additive": not existed}


def make_basd_selectable(account_number, channel="rbl", gateway_delta=-12345):
    """Force gateway_balance != statement_closing_balance (+ fresh gateway_balance_change_at) so the next
    fetch/initiate selects the account under balance_changed_rule."""
    ts = int(time.time())
    rc, _, err = _mysql("UPDATE banking_account_statement_details SET gateway_balance=COALESCE(statement_closing_balance,0)+(%d), "
                        "gateway_balance_change_at=%d, updated_at=%d WHERE account_number='%s' AND channel='%s'"
                        % (gateway_delta, ts, ts, account_number, channel))
    if rc != 0:
        raise RuntimeError("BASD selectable update failed: " + err)
    return read_basd(account_number, channel)


# --------------------------------------------------------------------------
# mozart-sim statement control plane
# --------------------------------------------------------------------------
def statement_row(utr, amount_paise, balance_paise, kind="debit", tran_id=None, ptsn=None, grn=None,
                  narration="M4 BAS INGEST", tran_date=None, posted_date=None, posted_age_s=120):
    """One bank row in the shape mozart-sim /_arena/statement accepts. The particulars encode the UTR the way
    RBL does for IMPS (debit: everything before the first '-' -> RblIMPSDebitRegex; credit: '<12 digits>_IMPS'
    or 'IMPS <utr> ...' -> RblIMPSCreditRegex/RblCreditRegex) and, optionally, a GRN as trailing ' RZP<10>'.
    Dates are fixed at build time so the SAME dict can be re-enqueued for an exact replay."""
    now = datetime.datetime.now(IST)
    if kind == "debit":
        particulars = "%s-IMPS/%s" % (utr, narration)
    else:
        particulars = "IMPS %s %s" % (utr, narration)
    if grn:
        particulars += " RZP%s" % grn
    return {
        "tran_id": tran_id or ("S%s" % uuid.uuid4().hex[:10].upper()),
        "ptsn": str(ptsn or (int(time.time()) % 100000)),
        "tran_date": tran_date or now.strftime("%d-%m-%Y"),
        "posted_date": posted_date or (now - datetime.timedelta(seconds=posted_age_s)).strftime("%d-%m-%Y %H:%M:%S"),
        "tran_type": "TCI",
        "type": kind,
        "particulars": particulars,
        "amount_paise": int(amount_paise),
        "balance_paise": int(balance_paise),
    }


def enqueue_statement(mozart, account_number, rows, options=None, mode="append", replace_options=True):
    body = {"account_number": account_number, "rows": rows, "mode": mode, "replace_options": replace_options}
    if options is not None:
        body["options"] = options
    st, txt = arena_http("POST", (mozart or MOZART) + "/_arena/statement", body)
    return {"status": st, "body": _jload(txt, txt)}


def set_statement_options(account_number, options, mozart=None):
    st, txt = arena_http("POST", (mozart or MOZART) + "/_arena/statement",
                         {"account_number": account_number, "options": options, "replace_options": True})
    return {"status": st, "body": _jload(txt, txt)}


def clear_statement(account_number, mozart=None):
    st, txt = arena_http("POST", (mozart or MOZART) + "/_arena/statement", {"account_number": account_number, "clear": True})
    return {"status": st, "body": _jload(txt, txt)}


def inspect_statement(account_number, mozart=None):
    st, txt = arena_http("GET", (mozart or MOZART) + "/_arena/statement?account_number=" + account_number)
    return {"status": st, "body": _jload(txt, txt)}


# --------------------------------------------------------------------------
# fetch trigger (REAL cron route)
# --------------------------------------------------------------------------
def trigger_fetch(channel="rbl", account_rate_limit=50, inactive_duration_limit=10 * 365 * 24 * 3600, via="http"):
    """POST the REAL cron route with the FastCron credential (what cron-driver's bas_fetch_initiate job does).
    via="exec" runs the cron-driver container's own --once path instead (docker exec)."""
    if via == "exec":
        out = subprocess.run(["docker", "exec", P.cname("cron-driver"), "python3", "/app/driver.py", "--once", "bas_fetch_initiate"],
                             capture_output=True, text=True, timeout=90)
        return {"via": "exec", "rc": out.returncode, "out": (out.stdout + out.stderr)[-800:]}
    body = {"channel": channel, "account_rate_limit": account_rate_limit,
            "inactive_duration_limit": inactive_duration_limit, "account_type": "direct"}
    st, txt = arena_http("POST", PS_API + "/v1/cron/banking_account_statement/fetch/initiate", body, basic=fastcron_basic())
    return {"via": "http", "request": body, "status": st, "body": _jload(txt, txt), "at": now_iso()}


# --------------------------------------------------------------------------
# banking_account_statement rows
# --------------------------------------------------------------------------
def read_bas_rows(account_number, utr=None, since_created=None, channel="rbl"):
    where = ["account_number='%s'" % account_number, "channel='%s'" % channel]
    if utr:
        where.append("utr='%s'" % utr)
    if since_created:
        where.append("created_at>=%d" % int(since_created))
    rows = _rows("SELECT %s FROM banking_account_statement WHERE %s ORDER BY created_at, id"
                 % (",".join(BAS_COLUMNS), " AND ".join(where)), BAS_COLUMNS)
    for r in rows:
        for k in ("amount", "balance", "transaction_date", "posted_date", "created_at", "updated_at"):
            if r.get(k) is not None:
                r[k] = int(r[k])
    return rows


def wait_for_bas_rows(account_number, utr=None, min_count=1, timeout=90, delay=2, since_created=None):
    deadline = time.time() + timeout
    rows = []
    while time.time() < deadline:
        rows = read_bas_rows(account_number, utr=utr, since_created=since_created)
        if len(rows) >= min_count:
            return rows
        time.sleep(delay)
    return rows


def dedup_key(row):
    return {k: row.get(k) for k in DEDUP_KEY_FIELDS}


def wait_for_worker_message(message, since, needles=(), timeout=90, delay=3):
    """Poll the REAL worker's logs for a trace message (e.g. BANKING_ACCOUNT_STATEMENT_FETCH_NO_RECORDS)
    that also contains every needle (account number / merchant id)."""
    deadline = time.time() + timeout
    hits = []
    while time.time() < deadline:
        hits = docker_logs("payouts-worker-rbl-banking-account-statement", since, (message,) + tuple(needles))
        if hits:
            return hits
        time.sleep(delay)
    return hits


# --------------------------------------------------------------------------
# ART batch process (REAL PS route)
# --------------------------------------------------------------------------
def art_entry(bas_id, recon_status, entity_id=None, entity_type="payout"):
    """One decoded entry (basdtos.BankingAccountStatementProcessPostReconPayload). entity_id is the ART
    "<a>,<b>,<14-char id>" triple; a bare 14-char id is wrapped (ExtractEntityInfoFromStatement takes [2])."""
    if entity_id and "," not in entity_id:
        entity_id = "art,%s,%s" % (entity_type, entity_id)
    return {"banking_account_statement_id": bas_id, "recon_status": recon_status,
            "entity_id": entity_id or "", "entity_type": entity_type if entity_id else ""}


def art_process_batch(entries):
    """POST /v1/banking_account_statement/process/batch (bas_internal_routes.go:11-25, BasicAuth(cred.API,
    cred.Workflow)) with body [{data: base64(JSON entry), idempotent_id}]. Returns request+response."""
    body = [{"data": base64.b64encode(json.dumps(e).encode()).decode(), "idempotent_id": "m4-art-" + uuid.uuid4().hex[:12]}
            for e in entries]
    st, txt = arena_http("POST", PS_API + "/v1/banking_account_statement/process/batch", body, basic=api_basic())
    return {"request_decoded": entries, "request": body, "status": st, "body": _jload(txt, txt), "at": now_iso()}


# --------------------------------------------------------------------------
# read-backs
# --------------------------------------------------------------------------
def get_payout_row(payout_id):
    rows = _rows("SELECT %s FROM payouts WHERE id='%s'" % (",".join(PAYOUT_COLUMNS), payout_id), PAYOUT_COLUMNS)
    return rows[0] if rows else None


def get_transaction_link(bas_id):
    bas = _rows("SELECT %s FROM banking_account_statement WHERE id='%s'" % (",".join(BAS_COLUMNS), bas_id), BAS_COLUMNS)
    bas = bas[0] if bas else None
    payout = get_payout_row(bas["entity_id"]) if bas and bas.get("entity_id") else None
    return {"bas": bas, "payout": payout,
            "linked": bool(bas and payout and payout.get("transaction_id") == bas_id and bas.get("entity_type") in ("payout", "payout_reversal"))}


def monolith_relay_log(needle=None, kind="banking_statement_payout_update"):
    st, txt = arena_http("GET", MONOLITH + "/_arena/log", basic=monolith_basic())
    doc = _jload(txt) or {}
    ev = doc if isinstance(doc, list) else (doc.get("log") or doc.get("events") or [])
    out = []
    for e in ev:
        if kind and e.get("kind") != kind:
            continue
        if needle and needle not in json.dumps(e):
            continue
        out.append(e)
    return out[-10:]


def ledger_journal_count(merchant_id, transactor_id=None):
    sql = "SELECT count(*) FROM journal WHERE merchant_id='%s'" % merchant_id
    if transactor_id:
        sql += " OR transactor_id='%s'" % transactor_id
    rc, out, err = _psql(sql)
    return out if rc == 0 else "ERR " + err


def fts_transfer(payout_id):
    keys = ("id", "status", "utr", "attempt_id")
    rows = _rows("SELECT t.id,t.status,t.utr,(SELECT MAX(a.id) FROM attempts a WHERE a.transfer_id=t.id) FROM transfers t "
                 "WHERE t.source_id='%s' AND t.source_type='payout'" % payout_id, keys,
                 db="fts", service="mysql-fts", pw_file="mysql_fts_root_password.txt")
    return rows[0] if rows else None
