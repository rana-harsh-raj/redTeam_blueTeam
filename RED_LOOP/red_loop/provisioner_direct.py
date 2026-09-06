"""Fresh DIRECT (current-account, RBL) merchant provisioning for Milestone 4.

Companion to provisioner.provision_funded_merchant (Shared/pool recipe). Builds a
brand-new Direct merchant across every store the M2 fixture (ARENAM00000002)
occupies, mirroring M2's real rows (reports/implementation/m4-subagent-handoffs/
T07.md section 1/12, T02 handoff, T04 section 12, T05 handoff):

  apidb-stub   merchants, balance(account_type=direct, channel=rbl), features,
               banking_accounts (the monolith's own current-account row)
  x-balances   balance(direct/rbl/status=active/fts_fund_account_id)
  payouts      banking_accounts(direct/rbl), counters(direct), fund_accounts,
               bank_accounts, banking_account_statement_details(active)
  FTS          bank_accounts, fund_accounts, source_accounts(CURRENT/'direct'),
               source_account_mappings + account_type_mappings +
               preferred_routing_weights (parity_only), direct_account_routing_rules
  ledger       FTS-current receivable/payable pair keyed by the new fts id (M2
               parity) + the 4 merchant sub-accounts at balance 0 (topology
               parity with the Shared recipe). Direct payouts post NO journal.
  cfa          contact, fund_account, hash_lookup
  monolith-stub merchants.json (account_type=direct + features + pricing plan),
               fund_accounts.json, misc.json(on_hold_slas), pricing.json
  dcs-stub     seed block copied from M2 (in_flight_reservation_enabled=true)
  splitz-stub  every experiment variant copied from M2 (incl. Rjdj0sAd4paXMG=on)
  stork        subscription seed + live WebhookAPI/Create (secret
               arena_test_secret_min5 = merchant-webhook-sink fallback)
  bankingaccounts-stub  (reads merchants.json at import -> restart)
  kong-lite    key + secret volume (+ restart)

Deterministic ids (hash of campaign_id) in a range disjoint from the Shared
provisioner and every generated namespace. Idempotent: re-running with the same
campaign_id reports which rows already existed and creates nothing twice.
Errors carry the raw mysql/psql/mongosh/docker stderr.

Container names come from provisioner.cname() (env ARENA_COMPOSE_PROJECT,
default env2_compose); in-arena HTTP goes through a throwaway curl container on
provisioner.arena_network().

Control-plane only. Never touches ARENAM00000001/2/3.
"""
import base64
import hashlib
import json
import os
import subprocess
import time
import uuid

from . import config
from . import provisioner as P
from .pricing_ids import reserve_plan_id, validate_pricing_id

SEEDS = config.ENV2 / "seeds" / "generated"
M2 = "ARENAM00000002"
SINK_SECRET = "arena_test_secret_min5"          # merchant-webhook-sink WEBHOOK_SECRET fallback
PAYOUT_EVENTS = ["payout.initiated", "payout.processed", "payout.reversed", "payout.failed",
                 "payout.updated", "payout.queued", "payout.rejected", "payout.pending"]
FTS_CREDENTIALS = ('{"auth_username":"arena","auth_password":"arena-rbl-pass","client_id":"arena-rbl-client",'
                   '"client_secret":"arena-rbl-secret","corp_id":"ARENACORP","user_id":"arena"}')
CURL_IMAGE = os.environ.get("ARENA_CURL_IMAGE", "curlimages/curl:latest")


# --------------------------------------------------------------------------
# ids
# --------------------------------------------------------------------------
def ids_for_direct(campaign_id, role="direct"):
    """Deterministic, collision-free ids. Shared uses ARENAM9xxxxxxx (num in
    9xxxxxxx); Direct uses ARENAD8xxxxxxx (num in 80000000..88999999) so the two
    recipes can never mint the same id, and every id fits its CHAR(14) column.
    FTS integer id 910000..999999 is disjoint from the generator's 900001..900400."""
    num = 80000000 + (int(hashlib.sha256((campaign_id + "|" + role + "|direct").encode()).hexdigest(), 16) % 9000000)
    n7 = num % 10000000
    fts_id = 910000 + (num % 90000)
    assert not (900001 <= fts_id <= 900400)
    ids = {
        "num": num,
        "merchant_id": "ARENAD%08d" % num,
        "balance_id": "ARENADB%07d" % n7,
        "banking_account": "ARENADA%07d" % n7,
        "counter": "ARENADC%07d" % n7,
        "fund_account_row": "ARENADF%07d" % n7,
        "fa_account_id": "ARENADX%07d" % n7,
        "bank_account": "ARENADK%07d" % n7,
        "contact": "ARENADO%07d" % n7,
        "feature_id": "ARENADT%07d" % n7,
        "basd_id": "ARENADS%07d" % n7,
        "hash_lookup": "ARENADH%07d" % n7,
        "apidb_banking_account": "ARENADM%07d" % n7,
        "ledger_prefix": "ARD%07d" % n7,            # + AC%02d / DT%02d -> 14 chars
        "fts_id": fts_id,
        "account_number": "2323" + str(num).zfill(12),   # 16 digits, own CA number
    }
    for k, v in ids.items():
        if isinstance(v, str) and k not in ("account_number",):
            assert len(v) <= 14, (k, v)
    assert len(str(fts_id)) <= 14
    return ids


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------
def _mysql(service, db, pw, sql):
    out = subprocess.run(["docker", "exec", P.cname(service), "sh", "-c",
                          "mysql -uroot -p%s -N -B -e %s %s" % (P._sh(pw), P._sh(sql), db)],
                         capture_output=True, text=True, timeout=60)
    err = "\n".join(l for l in out.stderr.splitlines() if "Using a password" not in l)
    return out.returncode, out.stdout.strip(), err[:2000]


def _psql(pw, sql):
    out = subprocess.run(["docker", "exec", P.cname("postgres-ledger"), "sh", "-c",
                          "PGPASSWORD=%s psql -U ledger -d ledger -tA -v ON_ERROR_STOP=1 -c %s" % (P._sh(pw), P._sh(sql))],
                         capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout.strip(), out.stderr[:2000]


def _mongo(js):
    pw = P._pw("mongo_cfa_root_password.txt")
    out = subprocess.run(["docker", "exec", P.cname("mongo-cfa"), "mongosh", "cfa", "--quiet",
                          "--username", "cfa_root", "--password", pw, "--authenticationDatabase", "admin",
                          "--eval", js], capture_output=True, text=True, timeout=60)
    return out.returncode, out.stdout.strip(), (out.stderr or "")[:2000]


def _redis(*args):
    out = subprocess.run(["docker", "exec", P.cname("redis"), "redis-cli"] + list(args),
                         capture_output=True, text=True, timeout=30)
    return out.stdout.strip()


def arena_http(method, url, body=None, headers=None, basic=None, timeout=25):
    """HTTP inside the arena network via a throwaway curl container.
    Returns (status:int|'ERR', text)."""
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


def _restart(service):
    rs = subprocess.run(["docker", "restart", P.cname(service)], capture_output=True, text=True, timeout=60)
    if rs.returncode != 0:
        return rs.stderr[:500] or "restart failed"
    return None


def _wait_health(service_host, port=8080, tries=20):
    for _ in range(tries):
        st, _ = arena_http("GET", "http://%s:%d/health" % (service_host, port), timeout=5)
        if st == 200:
            return True
        time.sleep(1)
    return False


def _seed_json(name):
    path = SEEDS / name
    return path, json.loads(path.read_text())


def _write_json(path, doc):
    path.write_text(json.dumps(doc, indent=2))


# --------------------------------------------------------------------------
# provisioning
# --------------------------------------------------------------------------
def provision_direct_merchant(campaign_id, role="direct", opening=10_000_000, channel="rbl", restart=True):
    """Create every record for one fresh Direct merchant. Returns a descriptor
    with `steps` (name/ok/existed/err), `verified` is NOT set here: run
    self_check_direct_merchant() and prove_direct_merchant() afterwards."""
    ids = ids_for_direct(campaign_id, role)
    mid = ids["merchant_id"]
    ch = channel.lower()
    CH = channel.upper()
    _refuse_foreign_id(mid)
    plan_id = reserve_plan_id(SEEDS, campaign_id, mid)   # fail closed before any write
    validate_pricing_id(plan_id)
    secret = "D" + hashlib.sha256((campaign_id + role + "direct-sec").encode()).hexdigest()[:40]
    ts = int(time.time())
    fts = ids["fts_id"]
    acct = ids["account_number"]
    d = {
        "campaign_id": campaign_id, "role": role, "archetype": "direct", "channel": ch,
        "merchant_id": mid, "key_id": "rzp_live_" + mid, "secret": secret,
        "balance_id": ids["balance_id"], "banking_account_id": ids["banking_account"],
        "account_number": acct, "fund_account_id": "fa_" + ids["fa_account_id"],
        "contact_id": ids["contact"], "fts_fund_account_id": str(fts), "fts_source_account_id": None,
        "pricing_plan_id": plan_id, "opening_balance": opening,
        "compose_project": P.compose_project(), "arena_network": P.arena_network(),
        "ids": ids, "steps": [], "restarts": [], "created_at": ts,
    }
    steps = d["steps"]

    def step(name, rc, err, existed=None, detail=None):
        rec = {"step": name, "ok": rc == 0, "existed": existed, "err": (err if rc != 0 else None)}
        if detail is not None:
            rec["detail"] = detail
        steps.append(rec)
        return rc == 0

    def sql_exists(service, db, pw, sql):
        rc, out, err = _mysql(service, db, pw, sql)
        if rc != 0:
            return None, err
        return (out.strip() not in ("", "0")), None

    pw_api = P._pw("mysql_apidb_root_password.txt")
    pw_xbal = P._pw("mysql_xbalances_root_password.txt")
    pw_pay = P._pw("mysql_payouts_root_password.txt")
    pw_fts = P._pw("mysql_fts_root_password.txt")
    pw_led = P._pw("postgres_ledger_password.txt")

    def insert(name, service, db, pw, exists_sql, insert_sql):
        ex, err = sql_exists(service, db, pw, exists_sql)
        if ex is None:
            return step(name, 1, "existence check failed: " + err)
        if ex:
            return step(name, 0, None, existed=True)
        rc, _, err = _mysql(service, db, pw, insert_sql)
        return step(name, rc, err, existed=False)

    # ---- 1 apidb-stub (monolith DB) -------------------------------------
    insert("apidb.merchants", "mysql-apidb-stub", "api_local", pw_api,
           "SELECT count(*) FROM merchants WHERE id='%s'" % mid,
           "INSERT INTO merchants (id,name,live,activated,created_at) VALUES ('%s','Arena Direct %s',1,1,%d)"
           % (mid, campaign_id[:20], ts))
    insert("apidb.balance", "mysql-apidb-stub", "api_local", pw_api,
           "SELECT count(*) FROM `balance` WHERE id='%s'" % ids["balance_id"],
           "INSERT INTO `balance` (id,merchant_id,balance,currency,type,name,on_hold,credits,fee_credits,"
           "refund_credits,account_number,account_type,channel,locked_balance,created_at,updated_at) VALUES "
           "('%s','%s',%d,'INR','banking','SYNTHETIC direct',0,0,0,0,'%s','direct','%s',0,%d,%d)"
           % (ids["balance_id"], mid, opening, acct, ch, ts - 90 * 86400, ts))
    insert("apidb.features.in_flight_reservation_enabled", "mysql-apidb-stub", "api_local", pw_api,
           "SELECT count(*) FROM features WHERE entity_id='%s' AND name='in_flight_reservation_enabled'" % mid,
           "INSERT INTO features (id,name,entity_id,entity_type,created_at,updated_at) VALUES "
           "('%s','in_flight_reservation_enabled','%s','merchant',%d,%d)" % (ids["feature_id"], mid, ts, ts))
    # The monolith's own banking_accounts row (BankingAccount\Core::activate() output; the
    # fixture never seeds it, 0 rows arena-wide -- added for T02 parity, nothing in the twin reads it).
    insert("apidb.banking_accounts", "mysql-apidb-stub", "api_local", pw_api,
           "SELECT count(*) FROM banking_accounts WHERE id='%s'" % ids["apidb_banking_account"],
           "INSERT INTO banking_accounts (id,merchant_id,account_ifsc,account_number,status,channel,"
           "fts_fund_account_id,balance_id,gateway_balance,beneficiary_name) VALUES "
           "('%s','%s','ARNA0000001','%s','activated','%s','%s','%s',%d,'ARENA DIRECT %s PRIVATE LIMITED')"
           % (ids["apidb_banking_account"], mid, acct, ch, fts, ids["balance_id"], opening, mid[-6:]))

    # ---- 2 x-balances (authoritative Direct balance) ----------------------
    # status='active' (x-balances constant.go) -- the M2 seed row says 'activated', which the
    # code's status filters never match (T04 C1). Recorded as a contradiction in T09.md.
    insert("xbalances.balance", "mysql-xbalances", "rx_balances_local", pw_xbal,
           "SELECT count(*) FROM balance WHERE id='%s'" % ids["balance_id"],
           "INSERT INTO balance (id,created_at,updated_at,status,merchant_id,account_number,account_type,channel,"
           "currency,balance,priority,last_change_at,last_fetched_at,metadata,last_attempted_at,fts_fund_account_id) "
           "VALUES ('%s',%d,%d,'active','%s','%s','direct','%s','INR',%d,0,%d,%d,JSON_OBJECT('seed','m4-direct',"
           "'campaign','%s'),0,'%s')" % (ids["balance_id"], ts, ts, mid, acct, ch, opening, ts, ts, campaign_id, fts))

    # ---- 3 payouts ---------------------------------------------------------
    insert("payouts.banking_accounts", "mysql-payouts", "payouts", pw_pay,
           "SELECT count(*) FROM banking_accounts WHERE id='%s'" % ids["banking_account"],
           "INSERT INTO banking_accounts (id,merchant_id,balance_id,channel,status,account_number,account_type,"
           "fts_fund_account_id,payout_service_enabled,counter_migrated,created_at,updated_at) VALUES "
           "('%s','%s','%s','%s','activated','%s','direct','%s',1,1,%d,%d)"
           % (ids["banking_account"], mid, ids["balance_id"], ch, acct, fts, ts, ts))
    insert("payouts.counters", "mysql-payouts", "payouts", pw_pay,
           "SELECT count(*) FROM counters WHERE id='%s'" % ids["counter"],
           "INSERT INTO counters (id,balance_id,free_payouts_consumed_last_reset_at,free_payouts_consumed,"
           "account_type,created_at,updated_at) VALUES ('%s','%s',%d,0,'direct',%d,%d)"
           % (ids["counter"], ids["balance_id"], ts, ts, ts))
    insert("payouts.fund_accounts", "mysql-payouts", "payouts", pw_pay,
           "SELECT count(*) FROM fund_accounts WHERE id='%s'" % ids["fund_account_row"],
           "INSERT INTO fund_accounts (id,account_type,account_id,created_at,updated_at) VALUES "
           "('%s','bank_account','%s',%d,%d)" % (ids["fund_account_row"], ids["fa_account_id"], ts, ts))
    insert("payouts.bank_accounts", "mysql-payouts", "payouts", pw_pay,
           "SELECT count(*) FROM bank_accounts WHERE id='%s'" % ids["bank_account"],
           "INSERT INTO bank_accounts (id,ifsc_code,bank_identifier,identifier_type,created_at,updated_at) VALUES "
           "('%s','RATN0000001',NULL,'IFSC',%d,%d)" % (ids["bank_account"], ts, ts))
    insert("payouts.payout_purpose", "mysql-payouts", "payouts", pw_pay,
           "SELECT count(*) FROM payout_purpose WHERE merchant_id='%s'" % mid,
           "INSERT INTO payout_purpose (id,merchant_id,purpose_type,created_at,updated_at) VALUES "
           "('ARENADP%07d','%s','[\"refund\",\"cashback\",\"vendor_bill\",\"utility bill\",\"salary\"]',%d,%d)"
           % (ids["num"] % 10000000, mid, ts, ts))
    # PS-side balance mirror (fallback authority when the merchant is NOT in Splitz
    # Rjdj0sAd4paXMG; also the create precondition alternative per T02).
    insert("payouts.banking_account_statement_details", "mysql-payouts", "payouts", pw_pay,
           "SELECT count(*) FROM banking_account_statement_details WHERE balance_id='%s'" % ids["balance_id"],
           "INSERT INTO banking_account_statement_details (id,merchant_id,balance_id,account_number,channel,status,"
           "statement_closing_balance,gateway_balance,statement_closing_balance_change_at,gateway_balance_change_at,"
           "last_statement_attempt_at,balance_last_fetched_at,pagination_key,account_type,created_at,updated_at,metadata)"
           " VALUES ('%s','%s','%s','%s','%s','active',%d,%d,%d,%d,%d,%d,NULL,'direct',%d,%d,NULL)"
           % (ids["basd_id"], mid, ids["balance_id"], acct, ch, opening, opening, ts, ts, ts, ts, ts, ts))

    # ---- 4 FTS --------------------------------------------------------------
    # One integer id F for bank_accounts.id == fund_accounts.id == source_accounts.id ==
    # source_accounts.fund_account_id (exactly how M2/900002 is laid out). FTS resolves
    # transfers.preferred_source_account_id against source_accounts.fund_account_id (T05 1.2).
    insert("fts.bank_accounts", "mysql-fts", "fts", pw_fts,
           "SELECT count(*) FROM bank_accounts WHERE id=%d" % fts,
           "INSERT INTO bank_accounts (id,product,merchant_id,account_type,account_number,ifsc_code,bank_identifier,"
           "identifier_type,is_virtual_account,beneficiary_name,created_at,updated_at) VALUES "
           "(%d,'PAYOUT','%s','CURRENT','%s','ARNA0000001',NULL,NULL,0,'ARENA DIRECT %s PRIVATE LIMITED',%d,%d)"
           % (fts, mid, acct, mid[-6:], ts, ts))
    insert("fts.fund_accounts", "mysql-fts", "fts", pw_fts,
           "SELECT count(*) FROM fund_accounts WHERE id=%d" % fts,
           "INSERT INTO fund_accounts (id,product,merchant_id,account_type,account_id,default_channel,created_at,"
           "updated_at) VALUES (%d,'PAYOUT','%s','BANK_ACCOUNT',%d,'%s',%d,%d)" % (fts, mid, fts, CH, ts, ts))
    # account_type 'direct' LOWERCASE: FTS constants (source_account.go:94) and compares are
    # case-sensitive; the M2 seed row says 'DIRECT' (T05 finding 1.3) -- contradiction logged in T09.md.
    insert("fts.source_accounts", "mysql-fts", "fts", pw_fts,
           "SELECT count(*) FROM source_accounts WHERE id=%d" % fts,
           "INSERT INTO source_accounts (id,product,channel,fund_account_id,mozart_identifier,bank_account_type,"
           "account_type,credentials,configuration,created_at,updated_at) VALUES "
           "(%d,'PAYOUT','%s',%d,'v1','CURRENT','direct','%s','{\"beneficiary_required\":false}',%d,%d)"
           % (fts, CH, fts, FTS_CREDENTIALS, ts, ts))
    d["fts_source_account_id"] = str(fts)
    for mode in ("IMPS", "NEFT"):
        insert("fts.source_account_mappings.%s(parity_only)" % mode, "mysql-fts", "fts", pw_fts,
               "SELECT count(*) FROM source_account_mappings WHERE merchant_id='%s' AND mode='%s' AND operation='TRANSFER'" % (mid, mode),
               "INSERT INTO source_account_mappings (operation,merchant_id,product,channel,mozart_identifier,"
               "source_account_id,source_account_type,account_type,mode,credentials,title,priority,routing_enabled,"
               "created_at,created_by,updated_at,integration_type,creation_reason) VALUES "
               "('TRANSFER','%s','PAYOUT','%s','v1',%d,'CURRENT','DIRECT','%s','{}','%s_%s',1,1,%d,'m4_direct',%d,'API','m4 direct')"
               % (mid, CH, fts, mode, mid.lower(), mode.lower(), ts, ts))
        insert("fts.account_type_mappings.%s(parity_only)" % mode, "mysql-fts", "fts", pw_fts,
               "SELECT count(*) FROM account_type_mappings WHERE merchant_id='%s' AND mode='%s'" % (mid, mode),
               "INSERT INTO account_type_mappings (mode,product,account_type,merchant_id,created_at,created_by,updated_at)"
               " VALUES ('%s','PAYOUT','CURRENT','%s',%d,'m4_direct',%d)" % (mode, mid, ts, ts))
        insert("fts.preferred_routing_weights.%s(parity_only)" % mode, "mysql-fts", "fts", pw_fts,
               "SELECT count(*) FROM preferred_routing_weights WHERE source_account_id=%d AND mode='%s'" % (fts, mode),
               "INSERT INTO preferred_routing_weights (mode,product,source_account_id,preferred_routing_weight,"
               "created_at,created_by,updated_at) VALUES ('%s','PAYOUT',%d,100,%d,'m4_direct',%d)" % (mode, fts, ts, ts))
        insert("fts.direct_account_routing_rules.%s" % mode, "mysql-fts", "fts", pw_fts,
               "SELECT count(*) FROM direct_account_routing_rules WHERE merchant_id='%s' AND mode='%s'" % (mid, mode),
               "INSERT INTO direct_account_routing_rules (merchant_id,product,channel,mozart_identifier,"
               "source_account_id,mode,created_at,created_by,updated_at) VALUES ('%s','PAYOUT','%s','v1',%d,'%s',%d,'m4_direct',%d)"
               % (mid, CH, fts, mode, ts, ts))

    # ---- 5 ledger (no merchant journal is ever posted for Direct) ------------
    lp = ids["ledger_prefix"]
    rc, out, err = _psql(pw_led, "SELECT count(*) FROM accounts WHERE id LIKE '%sAC%%'" % lp)
    if rc != 0:
        step("ledger.accounts", 1, err)
    elif out.strip() not in ("", "0"):
        step("ledger.accounts", 0, None, existed=True)
        step("ledger.account_details", 0, None, existed=True)
    else:
        bacc = "bacc_" + ids["banking_account"]
        accs = [
            # FTS-current pair, exactly like ARENAM2NODAC01/02 (owner = ledger system merchants)
            ("01", "Gh0YfsxpRlykwn", 0, "ARENAPRACC0009", "FTS Current Receivable - %s %d" % (mid, fts), "asset",
             '{"account_type":["receivable"],"fts_fund_account_id":["%d"],"fund_account_type":["current"]}' % fts),
            ("02", "Gh0Yfp7SMMBIRp", 0, "ARENAPRACC0010", "FTS Current Payable - %s %d" % (mid, fts), "liability",
             '{"account_type":["payable"],"fts_fund_account_id":["%d"],"fund_account_type":["current"]}' % fts),
            # 4 merchant sub-accounts (Shared-recipe topology parity; balance 0: funds live in x-balances)
            ("03", mid, 0, "ARENAPRACC0001", "Merchant Balance Account - " + mid, "liability",
             '{"account_type":["payable"],"banking_account_id":["%s"],"fund_account_type":["merchant_va"]}' % bacc),
            ("04", mid, 0, "ARENAPRACC0002", "Vendor Payable Account - " + mid, "liability",
             '{"account_type":["payable"],"banking_account_id":["%s"],"fund_account_type":["merchant_va_vendor"]}' % bacc),
            ("05", mid, 0, "ARENAPRACC0003", "Commission Income Account - " + mid, "revenue",
             '{"account_type":["cash"],"banking_account_id":["%s"],"fund_account_type":["merchant_va"]}' % bacc),
            ("06", mid, 0, "ARENAPRACC0004", "Output GST Account - " + mid, "liability",
             '{"account_type":["payable"],"banking_account_id":["%s"],"fund_account_type":["va_gst"]}' % bacc),
        ]
        rows = ",".join("('%sAC%s','%s','ACTIVATED',%d,0,NULL,%d,%d,NULL)" % (lp, i, owner, bal, ts, ts)
                        for i, owner, bal, _, _, _, _ in accs)
        rc, _, err = _psql(pw_led, "INSERT INTO accounts (id,merchant_id,status,balance,min_balance,negative_balance,"
                           "created_at,updated_at,deleted_at) VALUES %s ON CONFLICT (id) DO NOTHING;" % rows)
        step("ledger.accounts", rc, err, existed=False)
        det = ",".join("('%sDT%s','%sAC%s','%s','%s','%s','INR','%s','real','%s','Direct %s current account (m4)',%d,%d,NULL,'X',0)"
                       % (lp, i, lp, i, name, owner, parent, cat, ent, CH, ts, ts)
                       for i, owner, _, parent, name, cat, ent in accs)
        rc, _, err = _psql(pw_led, "INSERT INTO account_details (id,account_id,account_name,merchant_id,parent_account_id,"
                           "currency,account_category,business_category,entities,description,created_at,updated_at,"
                           "deleted_at,tenant,use_split_accounts) VALUES %s ON CONFLICT (id) DO NOTHING;" % det)
        step("ledger.account_details", rc, err, existed=False)

    # ---- 6 CFA -----------------------------------------------------------------
    fa_hash = hashlib.sha256((mid + ids["fa_account_id"]).encode()).hexdigest()
    ms = ts * 1000
    rc, out, err = _mongo('print(db.fund_accounts.countDocuments({id:"%s"}))' % ids["fa_account_id"])
    if rc != 0:
        step("cfa", 1, err or out)
    elif out.strip().endswith("1"):
        step("cfa", 0, None, existed=True)
    else:
        js = (
            'db.contacts.updateOne({id:"%(c)s"},{$setOnInsert:{active:true,contact:"",created_at:%(ms)d,'
            'email:"direct@arena.test",hash:"%(h)s",id:"%(c)s",merchant_id:"%(m)s",name:"Arena Direct Beneficiary",'
            'notes:{},reference_id:"SYNTHETIC-%(fa)s",type:"vendor",updated_at:%(ms)d}},{upsert:true});'
            'db.fund_accounts.updateOne({id:"%(fa)s"},{$setOnInsert:{account_type:"bank_account",active:true,'
            'bank_account:{account_number:"1112220%(n3)s",account_type:"current",bank_identifier:"RATN",bank_name:"RBL Bank",'
            'id:"%(bk)s",ifsc:"RATN0000001",ifsc_code:"RATN0000001",name:"Arena Direct Beneficiary"},'
            'contact_id:"%(c)s",created_at:%(ms)d,hash:"%(h)s",id:"%(fa)s",merchant_id:"%(m)s",updated_at:%(ms)d}},{upsert:true});'
            'db.hash_lookup.updateOne({id:"%(hl)s"},{$setOnInsert:{created_at:%(ms)d,entity_id:"%(fa)s",'
            'entity_type:"fund_accounts",hash:"%(h)s",id:"%(hl)s",updated_at:%(ms)d}},{upsert:true});'
        ) % {"c": ids["contact"], "ms": ms, "h": fa_hash, "m": mid, "fa": ids["fa_account_id"],
             "bk": ids["bank_account"], "hl": ids["hash_lookup"], "n3": str(ids["num"])[-3:]}
        rc, out, err = _mongo(js)
        step("cfa", rc, err or out, existed=False)

    # ---- 7 monolith-stub seeds (merchant config / pricing / fund account / slas) --
    existed = _register_monolith_direct_merchant(mid, ids, plan_id, ch)
    step("monolith.merchant_config", 0 if existed is not None else 1, "merchants.json write failed", existed=existed)
    err = P._register_pricing(mid, plan_id, ids["balance_id"])
    step("pricing.plan", 0 if not err else 1, err)
    err = P._register_monolith_fund_account(mid, ids, ids["contact"], restart=False)
    step("monolith.fund_accounts", 0 if not err else 1, err)
    existed = _register_monolith_misc(mid)
    step("monolith.on_hold_slas", 0, None, existed=existed)

    # ---- 8 dcs / splitz / stork seed files ----------------------------------
    existed, err = _register_dcs(mid)
    step("dcs.seed(copy_of_M2)", 0 if not err else 1, err, existed=existed)
    existed, err = _register_splitz(mid)
    step("splitz.seed(copy_of_M2)", 0 if not err else 1, err, existed=existed)
    existed, err = _register_stork_seed(mid)
    step("stork.seed", 0 if not err else 1, err, existed=existed)

    # ---- 9 restarts (one each) ----------------------------------------------
    if restart:
        for svc in ("monolith-stub", "bankingaccounts-stub", "dcs-stub", "splitz-stub"):
            err = _restart(svc)
            d["restarts"].append(svc)
            step("restart." + svc, 0 if not err else 1, err)
        for svc in ("monolith-stub", "bankingaccounts-stub", "dcs-stub", "splitz-stub"):
            if not _wait_health(svc):
                step("health." + svc, 1, "no 200 from /health after restart")

    # ---- 10 stork live registration (no restart; sink verifies with fallback secret)
    existed, err = register_stork_live(mid)
    step("stork.live_create", 0 if not err else 1, err, existed=existed)

    # ---- 11 kong key (+ kong restart) ----------------------------------------
    err = P.register_kong_key(mid, secret, restart=restart)
    d["restarts"].append("kong-lite")
    step("kong.register", 0 if not err else 1, err)
    d["failed_steps"] = [s["step"] for s in steps if not s["ok"]]
    d["existed_steps"] = [s["step"] for s in steps if s.get("existed")]
    return d


def _refuse_foreign_id(mid):
    """Fail closed if the minted id already belongs to a merchant this recipe did
    not create (generator namespaces are ARENA+9 hex chars; a hex string of the
    form D+8 digits would collide with ARENAD%08d)."""
    try:
        doc = json.loads(config.GENERATED_MERCHANTS.read_text()).get("merchants", {})
    except Exception:  # noqa: BLE001
        return
    rec = doc.get(mid)
    if rec and rec.get("secret_file") != "merchant_fresh_%s" % mid.lower():
        raise RuntimeError("id %s already exists in the arena under a foreign secret_file (%s); refusing to provision"
                           % (mid, rec.get("secret_file")))


def _register_monolith_direct_merchant(mid, ids, plan_id, ch):
    path, doc = _seed_json("monolith/merchants.json")
    if mid in doc.setdefault("merchants", {}):
        return True
    doc["merchants"][mid] = {
        "account_type": "direct", "balance_id": ids["balance_id"], "channel": ch,
        "fts_fund_account_id": ids["fts_id"], "fts_source_account_id": ids["fts_id"],
        "merchant": {
            "activated": True, "billing_label": "Arena Direct " + mid, "business_banking": True,
            "category": "other", "category2": "other", "country_code": "IN", "created_at": int(time.time()),
            "email": "direct@arena.test",
            "feature": ["payout_service_enabled", "banking", "in_flight_reservation_enabled"],
            "hold_funds": False, "id": mid, "live": True, "name": "Arena Direct Merchant (%s)" % ch.upper(),
            "org_id": "ARENAORG000001", "pricing_plan_id": plan_id, "purpose_code": "P0806"},
        "merchant_detail": {
            "business_name": "Arena Direct %s Pvt Ltd" % mid[-6:], "business_registered_address": "4 Arena Street",
            "business_registered_address_l2": "", "business_registered_city": "Bengaluru",
            "business_registered_country": "IN", "business_registered_pin": "560001",
            "business_registered_state": "Karnataka", "business_type": "private_limited", "iec_code": ""}}
    _write_json(path, doc)
    return False


def _register_monolith_misc(mid):
    path, doc = _seed_json("monolith/misc.json")
    slas = doc.setdefault("on_hold_slas", {}).setdefault("merchant_slas", {})
    if mid in slas:
        return True
    slas[mid] = 0
    _write_json(path, doc)
    return False


def _register_dcs(mid):
    path, doc = _seed_json("dcs/merchants.json")
    merchants = doc.setdefault("merchants", {})
    if mid in merchants:
        return True, None
    m2 = merchants.get(M2)
    if not m2:
        return False, "dcs seed has no %s block to copy" % M2
    rec = json.loads(json.dumps(m2))
    rec["_role"] = "m4 fresh Direct merchant (copy of M2 block)"
    merchants[mid] = rec
    _write_json(path, doc)
    return False, None


def _register_splitz(mid):
    path, doc = _seed_json("splitz/experiments.json")
    exps = doc.get("experiments", {})
    already = all(mid in (e.get("variants") or {}) for e in exps.values())
    if already:
        return True, None
    copied = 0
    for e in exps.values():
        v = e.setdefault("variants", {})
        if M2 in v:
            v[mid] = v[M2]
            copied += 1
    for k, a in doc.get("_by_name_alias", {}).items():
        if isinstance(a, dict) and M2 in (a.get("variants") or {}):
            a["variants"][mid] = a["variants"][M2]
    if not copied:
        return False, "no experiment carries a %s variant" % M2
    _write_json(path, doc)
    return False, None


def _stork_subscription(mid):
    return {"disabled": False, "owner_id": mid, "owner_type": "merchant", "secret": SINK_SECRET,
            "service": "rx-live", "subscriptions": [{"eventmeta": {"name": n}} for n in PAYOUT_EVENTS],
            "url": "http://merchant-webhook-sink:8080/webhook/" + mid}


def _register_stork_seed(mid):
    path, doc = _seed_json("stork/subscriptions.json")
    if any(w.get("owner_id") == mid for w in doc.get("webhooks", [])):
        return True, None
    doc.setdefault("webhooks", []).append(_stork_subscription(mid))
    _write_json(path, doc)
    return False, None


def register_stork_live(mid):
    """WebhookAPI/Create on the running stork-capture (payouts' stork creds)."""
    st, body = arena_http("POST", "http://stork-capture:8080/twirp/rzp.stork.webhook.v1.WebhookAPI/List",
                          {"owner_id": mid}, basic=_stork_basic())
    if st == 200 and any(w.get("url", "").endswith("/" + mid) for w in (_jload(body) or {}).get("webhooks", [])):
        return True, None
    st, body = arena_http("POST", "http://stork-capture:8080/twirp/rzp.stork.webhook.v1.WebhookAPI/Create",
                          {"webhook": _stork_subscription(mid)}, basic=_stork_basic())
    if st != 200:
        return False, "stork Create %s: %s" % (st, body[:300])
    return False, None


def _stork_basic():
    tok = P._pw("auth_stork_payouts.txt")
    return ("payouts:%s" % tok) if tok else None


def _ps_service_basic():
    p = config.ENV2 / "secrets" / "verifier-bridge" / "ps-service"
    return p.read_text().strip() if p.exists() else None


def _fts_basic():
    p = config.ENV2 / "secrets" / "verifier-bridge" / "fts"
    return p.read_text().strip() if p.exists() else None


def _xbal_basic():
    return "x_balances:" + P._pw("auth_xbalances_payouts.txt")


def _mono_basic():
    return "rzp_live:" + P._pw("auth_monolith_shared.txt")


# --------------------------------------------------------------------------
# self-check
# --------------------------------------------------------------------------
def self_check_direct_merchant(d):
    """Read back every record (SQL/Mongo/HTTP) and assert the cross-service
    relationships. Returns {checks:[{name,ok,detail}], ready:bool}."""
    mid, ids = d["merchant_id"], d["ids"]
    bal, fts = d["balance_id"], int(d["fts_fund_account_id"])
    checks = []

    def chk(name, ok, detail):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    pw_api = P._pw("mysql_apidb_root_password.txt")
    pw_xbal = P._pw("mysql_xbalances_root_password.txt")
    pw_pay = P._pw("mysql_payouts_root_password.txt")
    pw_fts = P._pw("mysql_fts_root_password.txt")
    pw_led = P._pw("postgres_ledger_password.txt")

    # apidb
    rc, out, err = _mysql("mysql-apidb-stub", "api_local", pw_api,
                          "SELECT id,account_type,channel,account_number FROM `balance` WHERE merchant_id='%s'" % mid)
    apidb_bal = out.split("\t") if out else []
    chk("apidb.balance.direct", rc == 0 and len(apidb_bal) >= 3 and apidb_bal[0] == bal and apidb_bal[1] == "direct"
        and apidb_bal[2] == d["channel"], out or err)
    rc, out, err = _mysql("mysql-apidb-stub", "api_local", pw_api,
                          "SELECT name FROM features WHERE entity_id='%s'" % mid)
    chk("apidb.features.in_flight_reservation_enabled", "in_flight_reservation_enabled" in out, out or err)

    # x-balances (SQL + HTTP)
    rc, out, err = _mysql("mysql-xbalances", "rx_balances_local", pw_xbal,
                          "SELECT id,status,account_type,channel,fts_fund_account_id,balance FROM balance WHERE merchant_id='%s'" % mid)
    xb = out.split("\t") if out else []
    chk("xbalances.balance.row", rc == 0 and len(xb) >= 5 and xb[0] == bal and xb[1] == "active" and xb[2] == "direct"
        and xb[4] == str(fts), out or err)
    st, body = arena_http("GET", "http://xbalances-server:8081/v1/balances/%s" % bal, basic=_xbal_basic())
    jb = (_jload(body) or {}).get("balance", {})
    chk("xbalances.http.GET_v1_balances", st == 200 and jb.get("accountType") == "direct" and jb.get("status") == "active"
        and str(jb.get("ftsFundAccountId")) == str(fts) and str(jb.get("ftsFundAccountId", "x")).isdigit(),
        {"status": st, "balance": jb})

    # payouts
    rc, out, err = _mysql("mysql-payouts", "payouts", pw_pay,
                          "SELECT id,balance_id,channel,account_type,fts_fund_account_id,account_number,status FROM banking_accounts WHERE merchant_id='%s'" % mid)
    pb = out.split("\t") if out else []
    chk("payouts.banking_accounts.direct", rc == 0 and len(pb) >= 6 and pb[1] == bal and pb[2] == d["channel"]
        and pb[3] == "direct" and pb[4] == str(fts) and pb[5] == d["account_number"], out or err)
    chk("relationship.balance_id_equal(payouts==apidb==xbalances)",
        len(pb) >= 2 and len(apidb_bal) >= 1 and len(xb) >= 1 and pb[1] == apidb_bal[0] == xb[0] == bal,
        {"payouts": pb[1:2], "apidb": apidb_bal[:1], "xbalances": xb[:1]})
    rc, out, err = _mysql("mysql-payouts", "payouts", pw_pay,
                          "SELECT account_type FROM counters WHERE balance_id='%s'" % bal)
    chk("payouts.counters.direct", out.strip() == "direct", out or err)
    rc, out, err = _mysql("mysql-payouts", "payouts", pw_pay,
                          "SELECT status,account_type,gateway_balance FROM banking_account_statement_details WHERE balance_id='%s'" % bal)
    chk("payouts.banking_account_statement_details.active", out.startswith("active\tdirect"), out or err)
    rc, out, err = _mysql("mysql-payouts", "payouts", pw_pay,
                          "SELECT count(*) FROM fund_accounts WHERE account_id='%s'" % ids["fa_account_id"])
    chk("payouts.fund_accounts", out.strip() == "1", out or err)

    # FTS
    rc, out, err = _mysql("mysql-fts", "fts", pw_fts,
                          "SELECT id,fund_account_id,channel,bank_account_type,account_type,mozart_identifier FROM source_accounts WHERE fund_account_id=%d" % fts)
    sa = out.split("\t") if out else []
    chk("fts.source_accounts.current_direct", rc == 0 and len(sa) >= 5 and sa[1] == str(fts) and sa[2] == d["channel"].upper()
        and sa[3] == "CURRENT" and sa[4] == "direct", out or err)
    chk("relationship.fts_fund_account_id==source_accounts.fund_account_id",
        len(sa) >= 2 and pb[4:5] == [sa[1]], {"payouts.fts_fund_account_id": pb[4:5], "fts.fund_account_id": sa[1:2]})
    rc, out, err = _mysql("mysql-fts", "fts", pw_fts,
                          "SELECT id,merchant_id,account_type,account_id FROM fund_accounts WHERE id=%d" % fts)
    chk("fts.fund_accounts", out.startswith(str(fts)), out or err)
    rc, out, err = _mysql("mysql-fts", "fts", pw_fts,
                          "SELECT GROUP_CONCAT(mode ORDER BY mode) FROM direct_account_routing_rules WHERE merchant_id='%s' AND source_account_id=%d" % (mid, fts))
    chk("fts.direct_account_routing_rules.IMPS+NEFT", out.strip() == "IMPS,NEFT", out or err)
    rc, out, err = _mysql("mysql-fts", "fts", pw_fts,
                          "SELECT GROUP_CONCAT(mode ORDER BY mode) FROM source_account_mappings WHERE merchant_id='%s' AND source_account_id=%d AND routing_enabled=1" % (mid, fts))
    chk("fts.source_account_mappings.IMPS+NEFT(parity_only)", out.strip() == "IMPS,NEFT", out or err)
    rc, out, err = _mysql("mysql-fts", "fts", pw_fts,
                          "SELECT count(*) FROM channel_information_status WHERE channel='%s' AND status=100 AND mode IN ('IMPS','NEFT')" % d["channel"].upper())
    chk("fts.channel_information_status.global_UP_rows", out.strip() not in ("", "0"), out or err)

    # ledger
    rc, out, err = _psql(pw_led, "SELECT string_agg(entities->'fund_account_type'->>0, ',' ORDER BY id) FROM account_details WHERE id LIKE '%sDT%%'" % ids["ledger_prefix"])
    chk("ledger.account_details.topology(2 fts-current + 4 merchant)", out.strip() == "current,current,merchant_va,merchant_va_vendor,merchant_va,va_gst", out or err)
    rc, out, err = _psql(pw_led, "SELECT count(*) FROM journal WHERE merchant_id='%s'" % mid)
    chk("ledger.journal.none_for_merchant", out.strip() == "0", out or err)

    # cfa
    rc, out, err = _mongo('const f=db.fund_accounts.findOne({id:"%s"});const c=db.contacts.findOne({id:"%s"});'
                          'print(JSON.stringify({fa:!!f,mid:f&&f.merchant_id,contact:!!c,bat:f&&f.bank_account.account_type}))'
                          % (ids["fa_account_id"], ids["contact"]))
    jc = _jload(out.splitlines()[-1] if out else "", {}) or {}
    chk("cfa.contact+fund_account", jc.get("fa") and jc.get("contact") and jc.get("mid") == mid, out or err)

    # monolith-stub
    st, body = arena_http("GET", "http://monolith-stub:8080/internal/merchants/%s" % mid, basic=_mono_basic())
    jm = (_jload(body) or {}).get("merchant", {})
    chk("monolith.GET_internal_merchants", st == 200 and "in_flight_reservation_enabled" in jm.get("feature", [])
        and jm.get("pricing_plan_id") == d["pricing_plan_id"] and jm.get("business_banking") is True, {"status": st, "merchant": jm})
    _, mono = _seed_json("monolith/merchants.json")
    rec = mono.get("merchants", {}).get(mid, {})
    chk("monolith.seed.account_type_direct+numeric_fts_id", rec.get("account_type") == "direct"
        and isinstance(rec.get("fts_fund_account_id"), int) and rec.get("fts_fund_account_id") == fts
        and rec.get("channel") == d["channel"], rec and {k: rec[k] for k in ("account_type", "channel", "fts_fund_account_id", "fts_source_account_id")})
    st, body = arena_http("GET", "http://monolith-stub:8080/fund_accounts_internal/%s" % d["fund_account_id"], basic=_mono_basic())
    chk("monolith.GET_fund_accounts_internal", st == 200 and (_jload(body) or {}).get("id") == d["fund_account_id"], {"status": st, "body": body[:200]})
    # pricing: monolith-stub /payouts_service/fetch_pricing_info
    st, body = arena_http("POST", "http://monolith-stub:8080/payouts_service/fetch_pricing_info",
                          {"payout_id": "pout_selfcheck", "merchant_id": mid, "balance_id": bal, "amount": 5000,
                           "method": "fund_transfer", "mode": "IMPS", "channel": d["channel"], "account_type": "direct"},
                          basic=_mono_basic())
    jp = _jload(body) or {}
    chk("pricing.plan_resolves", st == 200 and jp.get("pricing_rule_id") == d["pricing_plan_id"] and jp.get("fees") is not None,
        {"status": st, "body": jp})

    # bankingaccounts-stub
    st, body = arena_http("GET", "http://bankingaccounts-stub:8080/payouts/shield/merchant/%s/details?account_number=%s" % (mid, d["account_number"]))
    chk("bankingaccounts.shield_merchant_details", st == 200 and (_jload(body) or {}).get("data", {}).get("merchant_id") == mid, {"status": st, "body": body[:200]})

    # dcs
    st, body = arena_http("POST", "http://dcs-stub:8080/v1/kv/get",
                          {"queries": [{"key": {"namespace": "rzp", "entity": "merchant", "entity_id": mid,
                                                "domain": "payouts/direct_accounts", "object_name": "Configs"}}]})
    kv = ((_jload(body) or {}).get("kvs") or [{}])[0].get("value")
    chk("dcs.direct_accounts.Configs.in_flight_reservation_enabled", st == 200 and kv == "CAE=",
        {"status": st, "value_b64": kv, "expect": "CAE= (field1 varint 1)"})

    # splitz
    st, body = arena_http("POST", "http://splitz-stub:8080/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate",
                          {"id": mid, "experiment_id": "Rjdj0sAd4paXMG"})
    jv = _jload(body) or {}
    variant = (jv.get("variant") or {}).get("name")
    chk("splitz.Rjdj0sAd4paXMG(fetch_balance_entity_from_x_balance)=on", st == 200 and variant == "on", {"status": st, "variant": variant})

    # stork + sink
    st, body = arena_http("POST", "http://stork-capture:8080/twirp/rzp.stork.webhook.v1.WebhookAPI/List", {"owner_id": mid}, basic=_stork_basic())
    whs = (_jload(body) or {}).get("webhooks", [])
    subs = set()
    for w in whs:
        for s in w.get("subscriptions", []):
            subs.add(s.get("eventmeta", {}).get("name") if isinstance(s, dict) else s)
    chk("stork.subscription.payout_events", st == 200 and whs and {"payout.processed", "payout.failed"} <= subs
        and whs[0].get("url", "").endswith("/webhook/" + mid), {"status": st, "count": len(whs), "events": sorted(subs)})
    st, _ = arena_http("GET", "http://merchant-webhook-sink:8080/_arena/deliveries?merchant=%s" % mid)
    chk("sink.reachable", st == 200, st)

    # kong auth probe (401 vs 200)
    st_ok, _ = P._kong_call("GET", "/v1/payouts?count=1&account_number=" + d["account_number"], P._auth(d["key_id"], d["secret"]))
    st_bad, _ = P._kong_call("GET", "/v1/payouts?count=1&account_number=" + d["account_number"], P._auth(d["key_id"], "wrong"))
    chk("kong.auth_probe(200_vs_401)", st_ok == 200 and st_bad == 401, {"good_secret": st_ok, "bad_secret": st_bad})

    # reconciler heartbeat (gate fails closed without it)
    hb = _redis("EXISTS", "payouts:in_flight_reconciliation_heartbeat")
    chk("redis.reconciler_heartbeat_present", hb.strip() == "1", hb)

    ready = all(c["ok"] for c in checks)
    return {"merchant_id": mid, "checks": checks, "ready": ready, "passed": sum(1 for c in checks if c["ok"]),
            "total": len(checks)}


# --------------------------------------------------------------------------
# operational proof
# --------------------------------------------------------------------------
def set_mozart_scenario(key, scenario, polls=2):
    return arena_http("POST", "http://mozart-sim:8085/_arena/scenario", {"key": key, "scenario": scenario, "polls": polls})


def run_reconciler_once():
    out = subprocess.run(["docker", "exec", P.cname("cron-driver"), "python3", "/app/driver.py", "--once", "reservation_reconcile"],
                         capture_output=True, text=True, timeout=90)
    return out.returncode, (out.stdout + out.stderr)[-600:]


def _payout_row(pid):
    pw = P._pw("mysql_payouts_root_password.txt")
    rc, out, err = _mysql("mysql-payouts", "payouts", pw,
                          "SELECT status,balance_id,merchant_id,fts_transfer_id,transaction_id,queued_reason,utr,fees,tax,amount "
                          "FROM payouts WHERE id='%s'" % pid)
    if rc != 0 or not out:
        return {"_err": err or "no row"}
    f = out.split("\t")
    keys = ["status", "balance_id", "merchant_id", "fts_transfer_id", "transaction_id", "queued_reason", "utr", "fees", "tax", "amount"]
    return dict(zip(keys, [None if v == "NULL" else v for v in f]))


def _wait_payout(pid, terminal=("processed", "failed", "reversed", "cancelled", "rejected"), tries=20, delay=2, want=None):
    row = {}
    for _ in range(tries):
        row = _payout_row(pid)
        st = row.get("status")
        if want and st == want:
            return row
        if not want and st in terminal:
            return row
        time.sleep(delay)
    return row


def _fts_transfer(pid):
    pw = P._pw("mysql_fts_root_password.txt")
    rc, out, err = _mysql("mysql-fts", "fts", pw,
                          "SELECT t.id,t.status,t.preferred_source_account_id,t.source_account_id,t.source_account_primary_id,"
                          "t.fund_account_id,t.channel,t.mode,t.utr,s.account_type,s.bank_account_type,"
                          "(SELECT MAX(a.id) FROM attempts a WHERE a.transfer_id=t.id) FROM transfers t "
                          "LEFT JOIN source_accounts s ON s.id=t.source_account_primary_id WHERE t.source_id='%s' AND t.source_type='payout'" % pid)
    if rc != 0 or not out:
        return None
    keys = ["id", "status", "preferred_source_account_id", "source_account_id", "source_account_primary_id", "fund_account_id",
            "channel", "mode", "utr", "sa_account_type", "sa_bank_account_type", "attempt_id"]
    return dict(zip(keys, [None if v == "NULL" else v for v in out.split("\t")]))


def _wait_transfer(pid, tries=20, delay=2):
    t = None
    for _ in range(tries):
        t = _fts_transfer(pid)
        if t and t.get("attempt_id"):
            return t
        time.sleep(delay)
    return t


def _reservations(mid, bal):
    st, body = arena_http("GET", "http://payouts-api:9400/v1/inflight_reservations?merchant_id=%s&balance_id=%s" % (mid, bal),
                          basic=_ps_service_basic())
    return {"status": st, "body": _jload(body) or body[:200],
            "redis_counter": _redis("GET", "payouts:in_flight:{%s:%s}" % (mid, bal)),
            "redis_items": _redis("HGETALL", "payouts:in_flight_items:{%s:%s}" % (mid, bal))}


def _deliveries(mid, pid=None):
    st, body = arena_http("GET", "http://merchant-webhook-sink:8080/_arena/deliveries?merchant=%s%s" % (mid, ("&payout_id=pout_" + pid) if pid else ""))
    rows = (_jload(body) or {}).get("deliveries", [])
    out = []
    for r in rows:
        b = r.get("body", {}) if isinstance(r.get("body"), dict) else {}
        out.append({"event": b.get("event") or (b.get("payload", {}) or {}).get("event"),
                    "merchant": r.get("merchant"), "signature_valid": r.get("signature_valid"),
                    "payout_id": (((b.get("payload") or {}).get("payout") or {}).get("entity") or {}).get("id"),
                    "payout_status": (((b.get("payload") or {}).get("payout") or {}).get("entity") or {}).get("status")})
    return {"status": st, "deliveries": out}


def _fts_worker_route(transfer_id, attempt_id):
    """Which FTS worker container logged this transfer/attempt (rbl::direct::imps vs rbl::v1::imps)."""
    seen = {}
    for svc in ("fts-worker-rbl-direct-imps-initiate-transfer", "fts-worker-rbl-imps-initiate-transfer",
                "fts-worker-rbl-direct-imps-check-transfer-status", "fts-worker-rbl-imps-check-transfer-status",
                "fts-worker-initiate-transfer", "fts-worker-default"):
        out = subprocess.run(["docker", "logs", "--since", "20m", P.cname(svc)], capture_output=True, text=True, timeout=60)
        txt = out.stdout + out.stderr
        hits = [l for l in txt.splitlines() if ('"transfer_id":%s' % transfer_id) in l or ('"transfer_id":"%s"' % transfer_id) in l
                or ('"attempt_id":%s' % attempt_id) in l or ('"attempt_id":"%s"' % attempt_id) in l]
        if hits:
            seen[svc] = len(hits)
    return seen


def _ledger_journals(mid, pid):
    pw = P._pw("postgres_ledger_password.txt")
    rc, out, err = _psql(pw, "SELECT count(*) FROM journal WHERE merchant_id='%s' OR transactor_id='%s'" % (mid, pid))
    return out.strip() if rc == 0 else "ERR " + err


def _reversal(pid):
    pw = P._pw("mysql_payouts_root_password.txt")
    rc, out, err = _mysql("mysql-payouts", "payouts", pw, "SELECT count(*) FROM reversals WHERE payout_id='%s'" % pid)
    return out.strip() if rc == 0 else "ERR " + err


def _meta_temp(pid):
    pw = P._pw("mysql_payouts_root_password.txt")
    rc, out, err = _mysql("mysql-payouts", "payouts", pw, "SELECT meta_name,meta_value FROM payout_meta_temporary WHERE payout_id='%s'" % pid)
    return out if rc == 0 else "ERR " + err


def _monolith_relay_log(pid):
    """monolith-stub /_arena/log entries for this payout (route E relay evidence)."""
    st, body = arena_http("GET", "http://monolith-stub:8080/_arena/log", basic=_mono_basic())
    doc = _jload(body) or {}
    ev = doc if isinstance(doc, list) else (doc.get("log") or doc.get("events") or [])
    out = []
    for e in ev:
        if pid in json.dumps(e) and e.get("kind") != "dual_write":
            out.append({"kind": e.get("kind"), "payload": e.get("payload")})
    return out[-6:]


def _settle_reservation(mid, bal, pid, want_state, tries=4, delay=2):
    """Wait briefly for the terminal-transition hook to move the item; if it does
    not, run one reconciler pass (the twin's convergence path) and re-read.
    Returns (snapshot, source) with source in {terminal_hook, reconciler_pass, unsettled}."""
    snap = None
    for _ in range(tries):
        snap = _reservations(mid, bal)
        items = (snap["body"] or {}).get("items", []) if isinstance(snap["body"], dict) else []
        mine = [i for i in items if i.get("payout_id") == pid]
        if want_state is None and not mine:
            return snap, "terminal_hook"
        if want_state and any(i.get("state") == want_state for i in mine):
            return snap, "terminal_hook"
        time.sleep(delay)
    before = snap
    # The reconciler preserves items younger than a 60 s grace window
    # (inflight_reservation_reconciler.go ApplyDBDerivedState); wait it out
    # before asking it to reclaim a released/failed item.
    items = (before["body"] or {}).get("items", []) if before and isinstance(before["body"], dict) else []
    mine = [i for i in items if i.get("payout_id") == pid]
    if mine:
        wait_until = max(int(i.get("dispatched_at") or 0) for i in mine) + 65
        while time.time() < wait_until:
            time.sleep(2)
    rc, out = run_reconciler_once()
    snap = _reservations(mid, bal)
    snap["before_reconciler"] = before["body"] if before else None
    snap["reconciler_once"] = {"rc": rc, "out": out}
    items = (snap["body"] or {}).get("items", []) if isinstance(snap["body"], dict) else []
    mine = [i for i in items if i.get("payout_id") == pid]
    ok = (not mine) if want_state is None else any(i.get("state") == want_state for i in mine)
    return snap, ("reconciler_pass" if ok else "unsettled")


def _xbal_authority_log(bal, since):
    out = subprocess.run(["docker", "logs", "--since", since, P.cname("xbalances-server")], capture_output=True, text=True, timeout=60)
    txt = out.stdout + out.stderr
    return sum(1 for l in txt.splitlines() if bal in l and ("GetBalanceById" in l or "/v1/balances/" in l))


def prove_direct_merchant(d, amount=5000):
    """Through kong: one IMPS payout with mozart 'hold' (pending window ->
    reservation live) then 'success' via FTS check (-> processed, awaiting
    refresh, webhook, no journal); then one immediate 'failure' (-> failed, no
    reversal, no journal, reservation released)."""
    mid, bal = d["merchant_id"], d["balance_id"]
    auth = P._auth(d["key_id"], d["secret"])
    t0 = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    proof = {"merchant_id": mid, "balance_id": bal, "started_at": t0, "gateway": "kong hardened (KONG_ENFORCE_ROUTE_POLICY=1)",
             "create_body_note": "queue_if_low_balance=true so the Direct queueing strategy (reservation gate) runs",
             "success": {}, "failure": {}, "checks": []}

    def chk(name, ok, detail):
        proof["checks"].append({"name": name, "ok": bool(ok), "detail": detail})

    rc, out = run_reconciler_once()
    proof["reconciler_once"] = {"rc": rc, "out": out}
    proof["heartbeat_before_dispatch"] = _redis("EXISTS", "payouts:in_flight_reconciliation_heartbeat")
    xbal_before = _xbal_authority_log(bal, "1m")

    def body(amt):
        return {"fund_account_id": d["fund_account_id"], "account_number": d["account_number"], "amount": amt,
                "currency": "INR", "mode": "IMPS", "purpose": "payout", "queue_if_low_balance": True, "merchant_id": mid,
                "narration": "m4 direct proof", "notes": {"campaign": d["campaign_id"]}}

    # ---------------- success path ----------------
    S = proof["success"]
    S["mozart_hold"] = set_mozart_scenario(mid, "hold")
    S["reservations_before"] = _reservations(mid, bal)
    ik = "m4d-succ-" + uuid.uuid4().hex[:10]
    st, resp = P._kong_call("POST", "/v1/payouts", auth, body(amount), {"X-Payout-Idempotency": ik})
    r = _jload(resp) or {}
    pid = (r.get("id") or "").replace("pout_", "")
    S["create"] = {"status": st, "payout_id": pid, "fees": r.get("fees"), "tax": r.get("tax"), "status_field": r.get("status"),
                   "body": resp[:400] if st != 200 else None}
    chk("success.create_200", st == 200 and pid, S["create"])
    S["ledger_journals_after_create"] = _ledger_journals(mid, pid) if pid else None
    row = _wait_payout(pid, want="initiated", tries=15) if pid else {}
    t = _wait_transfer(pid) if pid else None
    S["payout_row_pending"] = row
    S["fts_transfer_pending"] = t
    S["reservations_pending"] = _reservations(mid, bal)
    items = (S["reservations_pending"]["body"] or {}).get("items", []) if isinstance(S["reservations_pending"]["body"], dict) else []
    live = [i for i in items if i.get("payout_id") == pid and i.get("state") == "live"]
    chk("success.reservation_live_while_pending", bool(live), {"items": items, "redis_items": S["reservations_pending"]["redis_items"]})
    chk("success.payout_uses_direct_banking_account", row.get("balance_id") == bal and row.get("status") in ("initiated", "created", "processed"), row)
    chk("success.fts_preferred_source_account_id==new_fund_account_id",
        t and t.get("preferred_source_account_id") == d["fts_fund_account_id"] and t.get("source_account_id") == d["fts_fund_account_id"]
        and t.get("source_account_primary_id") == d["fts_source_account_id"] and t.get("sa_account_type") == "direct"
        and t.get("sa_bank_account_type") == "CURRENT", t)
    # release the hold: success keyed by the real attempt id, then FTS check (verifier finish_bank pattern)
    if t and t.get("attempt_id"):
        S["mozart_success"] = set_mozart_scenario(str(t["attempt_id"]), "success")
        S["fts_check"] = arena_http("POST", "http://fts-web:8080/v1/transfer/%s/check" % t["id"], {}, basic=_fts_basic())
    row = _wait_payout(pid, tries=25) if pid else {}
    S["payout_row_terminal"] = row
    S["fts_transfer_terminal"] = _fts_transfer(pid) if pid else None
    chk("success.terminal_processed", row.get("status") == "processed", row)
    S["reservations_after"], S["reservation_transition_source"] = _settle_reservation(mid, bal, pid, "awaiting_balance_refresh")
    items = (S["reservations_after"]["body"] or {}).get("items", []) if isinstance(S["reservations_after"]["body"], dict) else []
    aw = [i for i in items if i.get("payout_id") == pid and i.get("state") == "awaiting_balance_refresh"]
    chk("success.reservation_awaiting_balance_refresh_after_processed", bool(aw),
        {"items": items, "redis": S["reservations_after"]["redis_items"], "transition_source": S["reservation_transition_source"]})
    S["monolith_relay_log"] = _monolith_relay_log(pid)
    S["status_route_observed"] = ("monolith relay (route E: FTS -> monolith-stub /update_fts_fund_transfer -> PS)"
                                  if any(e.get("kind") == "payout_status_relay" for e in S["monolith_relay_log"]) else "UNOBSERVED")
    S["ledger_journals_after_processed"] = _ledger_journals(mid, pid)
    chk("success.no_ledger_journal", S["ledger_journals_after_processed"] == "0", S["ledger_journals_after_processed"])
    S["payout_meta_temporary"] = _meta_temp(pid)
    time.sleep(3)
    S["webhooks"] = _deliveries(mid)
    ev = [w for w in S["webhooks"]["deliveries"] if w.get("payout_id") == "pout_" + pid]
    chk("success.webhook_payout.processed_delivered_to_own_sink_path",
        any(w.get("event") == "payout.processed" and w.get("signature_valid") is True and w.get("merchant") == mid for w in ev),
        ev)
    chk("success.webhooks_scoped_to_merchant", all(w.get("merchant") == mid for w in S["webhooks"]["deliveries"]),
        [w.get("merchant") for w in S["webhooks"]["deliveries"]])
    S["fts_worker_route_observed"] = _fts_worker_route(t["id"], t["attempt_id"]) if t else {}
    S["xbalances_GET_v1_balances_calls_during_flow"] = _xbal_authority_log(bal, "3m") - xbal_before
    S["balance_authority_observed"] = ("x-balances GET /v1/balances/{id} (Splitz Rjdj0sAd4paXMG=on)"
                                       if S["xbalances_GET_v1_balances_calls_during_flow"] > 0 else "UNOBSERVED (no xbalances GET logged)")

    # ---------------- immediate failure path ----------------
    F = proof["failure"]
    F["mozart_failure"] = set_mozart_scenario(mid, "failure")
    ik = "m4d-fail-" + uuid.uuid4().hex[:10]
    st, resp = P._kong_call("POST", "/v1/payouts", auth, body(amount + 1), {"X-Payout-Idempotency": ik})
    r = _jload(resp) or {}
    fpid = (r.get("id") or "").replace("pout_", "")
    F["create"] = {"status": st, "payout_id": fpid, "body": resp[:400] if st != 200 else None}
    chk("failure.create_200", st == 200 and fpid, F["create"])
    row = _wait_payout(fpid, tries=30) if fpid else {}
    F["payout_row_terminal"] = row
    F["fts_transfer_terminal"] = _fts_transfer(fpid) if fpid else None
    chk("failure.terminal_failed_not_reversed", row.get("status") == "failed", row)
    F["reversal_rows"] = _reversal(fpid) if fpid else None
    chk("failure.no_reversal_row", F["reversal_rows"] == "0", F["reversal_rows"])
    F["ledger_journals"] = _ledger_journals(mid, fpid) if fpid else None
    chk("failure.no_ledger_journal", F["ledger_journals"] == "0", F["ledger_journals"])
    F["reservations_after"], F["reservation_transition_source"] = _settle_reservation(mid, bal, fpid, None)
    items = (F["reservations_after"]["body"] or {}).get("items", []) if isinstance(F["reservations_after"]["body"], dict) else []
    chk("failure.reservation_released", not any(i.get("payout_id") == fpid for i in items),
        {"items": items, "transition_source": F["reservation_transition_source"]})
    F["monolith_relay_log"] = _monolith_relay_log(fpid)
    time.sleep(3)
    F["webhooks"] = _deliveries(mid, fpid)
    chk("failure.webhook_payout.failed_delivered",
        any(w.get("event") == "payout.failed" and w.get("signature_valid") is True for w in F["webhooks"]["deliveries"]),
        F["webhooks"]["deliveries"])
    set_mozart_scenario(mid, "success")   # leave the merchant in a sane default
    proof["passed"] = sum(1 for c in proof["checks"] if c["ok"])
    proof["total"] = len(proof["checks"])
    proof["ok"] = proof["passed"] == proof["total"]
    proof["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return proof


def public_view(d):
    """Descriptor without the secret (for evidence files)."""
    v = dict(d)
    v.pop("secret", None)
    return v


def secret_b64(d):
    return base64.b64encode(d["secret"].encode()).decode()
