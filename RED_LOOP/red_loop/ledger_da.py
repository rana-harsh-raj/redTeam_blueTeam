"""Ledger Direct-account (DA) bookkeeping for Milestone 4 -- stdlib only, live arena.

Real Twirp calls against the REAL ledger-api (tenant X), exactly what the monolith does at
current-account onboarding (api Merchant/Balance/Ledger/Core.php createXLedgerAccountForDirect,
Feature/Service.php:640-720 on feature da_ledger_journal_writes):

  ensure_da_parent_accounts()   POST AccountAPI/CreateInBulk {"account_data_identifier":"direct_account_x"}
                                -> the 7 DA parent accounts (ledger internal/account/seed_data/direct_account_x.go)
  onboard_da_merchant(mid, basd_id, opening_balance)
                                POST AccountAPI/CreateOnEvent {merchant_id, events:[{name:"direct_merchant_onboarding",
                                entities:{banking_account_stmt_detail_id:["basd_<id>"]}}], currency:"INR",
                                merchant_balance_opening_balance} headers Ledger-Tenant: X, idempotency-key: <uuid>
                                -> one sub-account per parent carrying direct_merchant_onboarding (6)
  da_accounts(mid)              Postgres read-back (docker exec psql, like provisioner._psql)
  da_journals(mid)              journal + ledger_entries read-back for the DA transactor events

Both create calls are idempotent at the ledger: parent names are UNIQUE (account_details.account_name)
and CreateOnEvent under an existing sub-account returns the existing rows (account/server.go mutex on
merchant_id+event+currency; core.go createSubAccountsUnderParent). Errors are reported verbatim.

In-arena HTTP goes through a throwaway curl container on the arena network (no host bridge for
ledger-api). Never touches ARENAM00000001/2/3 unless explicitly asked.
"""
import base64
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# M11: honour ARENA_ENV2_ROOT (a factory twin's own ENV2 copy + secrets); the repo checkout is only the single-arena default
ENV2 = Path(os.environ.get("ARENA_ENV2_ROOT") or (REPO / "ENV2_COMPOSE")).resolve()
CURL_IMAGE = os.environ.get("ARENA_CURL_IMAGE", "curlimages/curl:latest")
LEDGER_URL = os.environ.get("ARENA_LEDGER_URL", "http://ledger-api:8080")
DA_EVENTS = ["da_payout_processed", "da_payout_reversed", "da_fee_payout_processed", "da_fee_payout_reversed",
             "da_payout_processed_recon", "da_payout_reversed_recon", "da_ext_debit", "da_ext_credit",
             "da_ext_payout_processed", "da_ext_payout_reversed", "da_ext_fee_payout_processed", "da_ext_fee_payout_reversed"]
DA_PARENT_NAMES = ["Direct Merchant Balance Account", "Direct Vendor Payable Account", "Direct Commission Income Account",
                   "Direct Output GST Account", "Direct Commission Receivable Account", "Direct Merchant Cash Account",
                   "Direct Cash Account Legacy"]


def compose_project():
    return os.environ.get("ARENA_COMPOSE_PROJECT", os.environ.get("COMPOSE_PROJECT_NAME", "env2_compose"))


def cname(service):
    return "%s-%s-1" % (compose_project(), service)


def arena_network():
    return os.environ.get("ARENA_NETWORK", "rzp-arena" + os.environ.get("ARENA_SUFFIX", ""))


def _pw(name):
    return (ENV2 / "secrets" / name).read_text().strip()


def _sh(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def _psql(sql):
    pw = _pw("postgres_ledger_password.txt")
    out = subprocess.run(["docker", "exec", cname("postgres-ledger"), "sh", "-c",
                          "PGPASSWORD=%s psql -U ledger -d ledger -tA -v ON_ERROR_STOP=1 -c %s" % (_sh(pw), _sh(sql))],
                         capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout.strip(), out.stderr[:2000]


def _psql_json(sql):
    rc, out, err = _psql("SELECT COALESCE(json_agg(t), '[]'::json) FROM (%s) t" % sql.rstrip(";"))
    if rc != 0:
        return {"_err": err}
    try:
        return json.loads(out or "[]")
    except ValueError:
        return {"_err": "bad json: " + out[:200]}


def ledger_basic():
    return "payouts_key:" + _pw("auth_payouts_ledger.txt")


def arena_http(method, url, body=None, headers=None, basic=None, timeout=25):
    cmd = ["docker", "run", "--rm", "--network", arena_network(), CURL_IMAGE, "-s", "-m", str(timeout),
           "-o", "/dev/stdout", "-w", "\n__STATUS__%{http_code}", "-X", method, url]
    for k, v in (headers or {}).items():
        cmd += ["-H", "%s: %s" % (k, v)]
    if basic:
        cmd += ["-H", "Authorization: Basic " + base64.b64encode(basic.encode()).decode()]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    text = out.stdout
    if "__STATUS__" not in text:
        return "ERR", (out.stderr or text)[:500]
    payload, status = text.rsplit("__STATUS__", 1)
    try:
        return int(status.strip()), payload
    except ValueError:
        return "ERR", payload[:500]


def _jload(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _twirp(rpc, body, extra_headers=None):
    h = {"Ledger-Tenant": "X"}
    h.update(extra_headers or {})
    return arena_http("POST", LEDGER_URL + "/twirp/rzp.ledger.account.v1.AccountAPI/" + rpc, body, h, ledger_basic(), timeout=40)


# ---------------------------------------------------------------------------
def da_parent_accounts():
    return _psql_json("SELECT a.id, d.account_name, d.merchant_id, d.entities::text AS entities, d.account_category "
                      "FROM account_details d JOIN accounts a ON a.id=d.account_id "
                      "WHERE d.tenant='X' AND d.deleted_at IS NULL AND (d.parent_account_id IS NULL OR d.parent_account_id='') "
                      "AND d.account_name LIKE 'Direct %%' ORDER BY d.account_name")


def activate_da_parent_accounts():
    """AccountAPI/Activate {id} for every DA parent still IN_REVIEW. CreateInBulk creates parents
    IN_REVIEW (ledger internal/account/core.go:331); production activates them out-of-band (ops), and the
    twin's seeded Shared parents are ACTIVATED (seeds/generated/s4/ledger.sql). Sub-accounts inherit the
    parent status at CreateOnEvent (core.go createSubAccountsUnderParent), so activation precedes onboarding."""
    rows = _psql_json("SELECT a.id, a.status, d.account_name FROM accounts a JOIN account_details d ON d.account_id=a.id "
                      "WHERE d.tenant='X' AND d.deleted_at IS NULL AND (d.parent_account_id IS NULL OR d.parent_account_id='') "
                      "AND d.account_name LIKE 'Direct %%'")
    out = []
    for r in rows if isinstance(rows, list) else []:
        if r["status"] == "ACTIVATED":
            continue
        st, body = _twirp("Activate", {"id": r["id"]})
        out.append({"id": r["id"], "account_name": r["account_name"], "status": st, "response": (body or "")[:300]})
    return out


def ensure_da_parent_accounts():
    """Idempotent. Returns {existed_before, created, status, activated, parents:[...]}."""
    before = da_parent_accounts()
    have = {p["account_name"] for p in before} if isinstance(before, list) else set()
    result = {"existed_before": all(n in have for n in DA_PARENT_NAMES), "created": 0, "status": None}
    if not result["existed_before"]:
        st, body = _twirp("CreateInBulk", {"account_data_identifier": "direct_account_x"})
        after = da_parent_accounts()
        result.update({"status": st, "response": (body or "")[:1500],
                       "created": (len(after) if isinstance(after, list) else 0) - len(have)})
    result["activated"] = activate_da_parent_accounts()
    result["parents"] = _psql_json("SELECT a.id, a.status, d.account_name, d.merchant_id, d.entities::text AS entities "
                                   "FROM accounts a JOIN account_details d ON d.account_id=a.id WHERE d.tenant='X' AND d.deleted_at IS NULL "
                                   "AND (d.parent_account_id IS NULL OR d.parent_account_id='') AND d.account_name LIKE 'Direct %%' ORDER BY d.account_name")
    return result


def da_accounts(merchant_id):
    return _psql_json("SELECT a.id, d.account_name, a.merchant_id, a.status, a.balance::text AS balance, a.min_balance::text AS min_balance, "
                      "d.parent_account_id, d.account_category, d.business_category, d.entities::text AS entities "
                      "FROM accounts a JOIN account_details d ON d.account_id=a.id "
                      "WHERE a.merchant_id='%s' AND d.tenant='X' AND d.deleted_at IS NULL ORDER BY d.account_name" % merchant_id)


def da_sub_accounts(merchant_id, basd_id):
    rows = da_accounts(merchant_id)
    if not isinstance(rows, list):
        return rows
    tag = '"basd_%s"' % basd_id.replace("basd_", "")
    return [r for r in rows if tag in (r.get("entities") or "")]


def onboard_da_merchant(merchant_id, basd_id, opening_balance=0, currency="INR"):
    """CreateOnEvent direct_merchant_onboarding. Returns {status, response, sub_accounts, idempotency_key}."""
    basd = basd_id if basd_id.startswith("basd_") else "basd_" + basd_id
    existing = da_sub_accounts(merchant_id, basd)
    if isinstance(existing, list) and len(existing) >= 6:
        return {"existed_before": True, "status": None, "sub_accounts": existing, "count": len(existing)}
    ikey = str(uuid.uuid4())
    body = {"merchant_id": merchant_id,
            "events": [{"name": "direct_merchant_onboarding", "description": "arena m4 direct merchant onboarding",
                        "entities": {"banking_account_stmt_detail_id": [basd]}}],
            "currency": currency}
    if opening_balance:
        body["merchant_balance_opening_balance"] = str(int(opening_balance))
    st, resp = _twirp("CreateOnEvent", body, {"idempotency-key": ikey})
    subs = da_sub_accounts(merchant_id, basd)
    return {"existed_before": False, "status": st, "response": (resp or "")[:2000], "idempotency_key": ikey,
            "request": body, "sub_accounts": subs, "count": len(subs) if isinstance(subs, list) else None}


def da_journals(merchant_id, transactor_id=None):
    """journal rows for the DA events (+ their entries) for one merchant."""
    where = "j.merchant_id='%s' AND j.transactor_event IN (%s)" % (merchant_id, ",".join("'%s'" % e for e in DA_EVENTS))
    if transactor_id:
        where += " AND j.transactor_id='%s'" % transactor_id
    journals = _psql_json("SELECT j.id, j.transactor_id, j.transactor_event, j.amount::text AS amount, j.merchant_id, j.currency, "
                          "j.transaction_date, j.created_at, j.tenant FROM journal j WHERE %s ORDER BY j.created_at, j.transactor_event" % where)
    if not isinstance(journals, list):
        return journals
    for j in journals:
        j["entries"] = _psql_json("SELECT e.account_id, d.account_name, e.type, e.amount::text AS amount FROM ledger_entries e "
                                  "JOIN account_details d ON d.account_id=e.account_id WHERE e.journal_id='%s' ORDER BY e.type, d.account_name" % j["id"])
        debit = sum(float(e["amount"]) for e in j["entries"] if e["type"] == "debit") if isinstance(j["entries"], list) else None
        credit = sum(float(e["amount"]) for e in j["entries"] if e["type"] == "credit") if isinstance(j["entries"], list) else None
        j["sum_debit"], j["sum_credit"] = debit, credit
        j["balanced"] = (debit is not None and abs(debit - credit) < 1e-6)
    return journals


def journal_count(transactor_id, transactor_event):
    rc, out, _ = _psql("SELECT COUNT(*) FROM journal WHERE transactor_id='%s' AND transactor_event='%s'" % (transactor_id, transactor_event))
    return int(out) if rc == 0 and out.isdigit() else None


def idempotency_rows(keys):
    if not keys:
        return []
    return _psql_json("SELECT idempotency_key, entity, entity_id, created_at FROM idempotency WHERE idempotency_key IN (%s)"
                      % ",".join("'%s'" % k for k in keys))


def journal_uniqueness(merchant_id):
    return _psql_json("SELECT transactor_id, transactor_event, COUNT(*) AS n FROM journal WHERE merchant_id='%s' "
                      "GROUP BY transactor_id, transactor_event HAVING COUNT(*) > 1" % merchant_id)


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "ensure-parents":
        print(json.dumps(ensure_da_parent_accounts(), indent=2))
    elif cmd == "onboard":
        print(json.dumps(onboard_da_merchant(argv[2], argv[3], int(argv[4]) if len(argv) > 4 else 0), indent=2))
    elif cmd == "accounts":
        print(json.dumps(da_accounts(argv[2]), indent=2))
    elif cmd == "journals":
        print(json.dumps(da_journals(argv[2], argv[3] if len(argv) > 3 else None), indent=2))
    else:
        print("unknown command", cmd)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
