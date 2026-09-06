"""Per-campaign namespace provisioning + hidden canaries (Workstream B / Section 7).

Two mechanisms, both deterministic and campaign-scoped:

1. CANARY MINTING (default, safe): for each victim/control merchant the
   provisioner creates fresh payouts through the NORMAL merchant API carrying
   UNIQUE canary strings (reference/notes/narration). The canary values and the
   resulting victim-owned payout ids are recorded in the JUDGE-ONLY manifest and
   never given to the primary red agent. If any attacker response later contains
   a canary, the judge issues a confidentiality/tenant-boundary impact. Because
   the values are fresh per campaign, two campaigns never share a canary, and the
   attacker's own namespace is time-windowed + idempotency-prefixed so no old
   payout silently becomes a later precondition.

2. FRESH FUNDED MERCHANT (provision_funded_merchant): provisions a brand-new
   funded merchant across the six datastores from the live-verified recipe
   (kong key + ledger + x-balances + payouts + apidb + cfa + fts) so a campaign
   can run under a fresh attacker identity. Additive (new ids only; never touches
   the M1/M2/M3 fixtures). Verified by a real payout create before use; callers
   fall back to a fixture attacker with a fresh idempotency namespace if
   verification fails, and that fallback is recorded in the manifest.

Control-plane only. The red agent never imports or reaches this module.
"""
import base64
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from . import allocator, config
from .pricing_ids import reserve_plan_id, validate_pricing_id

KONG = config.KONG_LITE_URL
C_PAYOUTS = "env2_compose-mysql-payouts-1"


def _secret_for(mkey):
    return allocator._read_secret(allocator.NAMED[mkey]["secret_file"])


def _auth(key_id, secret):
    return "Basic " + base64.b64encode(("%s:%s" % (key_id, secret)).encode()).decode()


def _kong_call(method, path, auth, body=None, headers=None):
    h = {"Authorization": auth}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)
    req = urllib.request.Request(KONG + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return "ERR", str(e)[:200]


def _canary_token(campaign_id, mkey, idx):
    """A unique, unguessable canary string that exists ONLY in this campaign's
    victim resources. Not derivable by the attacker."""
    seed = "%s|%s|%d|%s" % (campaign_id, mkey, idx, uuid.uuid4().hex)
    dig = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return "CANARY-%s-%s%d-%s" % (campaign_id.split("-")[-1], mkey, idx, dig)


# Per-victim payout mode: matches the fixture's seeded beneficiary account type
# (M2 = bank account -> IMPS; M3 = UPI VPA -> UPI). Amount tuned so UPI passes.
_VICTIM_MODE = {"M1": ("IMPS", 1000), "M2": ("IMPS", 1000), "M3": ("UPI", 250000)}


def mint_victim_canaries(campaign_id, victim_keys=("M2", "M3"), per_victim=2):
    """Create fresh canary payouts as each control merchant. Returns
    {victim_canaries:[...], victim_records:[...]} (JUDGE-ONLY). Only canaries
    whose payout actually persisted (200 + id) are included, so the judge's
    canary set contains exactly the values that really exist in victim state."""
    canaries = []
    records = []
    for mkey in victim_keys:
        m = allocator.NAMED[mkey]
        secret = _secret_for(mkey)
        if not secret:
            records.append({"merchant": m["merchant_id"], "error": "secret_missing"})
            continue
        mode, amount = _VICTIM_MODE.get(mkey, ("IMPS", 1000))
        auth = _auth("rzp_live_" + m["merchant_id"], secret)
        for i in range(per_victim):
            canary = _canary_token(campaign_id, mkey, i)
            ik = "canary-%s-%s-%d" % (campaign_id.split("-")[-1], mkey, i)
            body = {"fund_account_id": m["fund_account_id"], "account_number": m["account_number"],
                    "amount": amount + i, "currency": "INR", "mode": mode, "purpose": "refund",
                    "queue_if_low_balance": False, "merchant_id": m["merchant_id"],
                    "narration": canary[:30],
                    "notes": {"campaign_canary": canary, "campaign": campaign_id}}
            st, resp = _kong_call("POST", "/v1/payouts", auth, body, {"X-Payout-Idempotency": ik})
            pid = None
            try:
                pid = json.loads(resp).get("id")
            except ValueError:
                pass
            rec = {"merchant": m["merchant_id"], "payout_id": pid, "status": st, "canary": canary}
            records.append(rec)
            if st == 200 and pid:
                canaries.append(canary)      # only real, persisted victim values
            else:
                rec["skipped_from_canary_set"] = True
    return {"victim_canaries": canaries, "victim_records": records}


def provision_campaign(campaign_id, attacker_key="M1", victim_keys=("M2", "M3"),
                       fund_fresh_attacker=False, per_victim=2):
    """Deterministic per-campaign provisioning. Returns a manifest fragment:
      attacker (control-plane, incl. secret) + public_attacker view,
      victim_control_merchants, victim_canaries (JUDGE-ONLY), provenance.
    The attacker gets a campaign-scoped idempotency namespace so no prior
    campaign's payout/idempotency record becomes a later precondition."""
    alloc = allocator.allocate(attacker_key=attacker_key, victim_keys=victim_keys)
    attacker = alloc["attacker"]
    fresh = None
    if fund_fresh_attacker:
        fresh = provision_funded_merchant(campaign_id, role="attacker")
        if fresh and fresh.get("verified"):
            attacker = {"merchant_id": fresh["merchant_id"], "key_id": fresh["key_id"],
                        "secret": fresh["secret"], "mode": "live", "archetype": "shared",
                        "roles": [], "account_number": fresh["account_number"],
                        "fund_account_id": fresh["fund_account_id"]}
            alloc["actor_merchants"] = [attacker["merchant_id"]] + \
                [v["merchant_id"] for v in alloc["victims"]]

    canary = mint_victim_canaries(campaign_id, victim_keys=victim_keys, per_victim=per_victim)

    idempotency_namespace = "camp-%s" % campaign_id.split("-")[-1]
    manifest = {
        "provisioning_version": 2,
        "attacker_merchant_id": attacker["merchant_id"],
        "attacker_is_fresh_funded": bool(fresh and fresh.get("verified")),
        "attacker_fallback": (None if (fresh and fresh.get("verified"))
                              else "fixture_attacker_with_fresh_idempotency_namespace"),
        "idempotency_namespace": idempotency_namespace,
        "victim_control_merchants": [v["merchant_id"] for v in alloc["victims"]],
        "victim_canaries": canary["victim_canaries"],
        "victim_records": canary["victim_records"],           # judge-only provenance
        "actor_merchants": alloc["actor_merchants"],
        "fresh_merchant_detail": (fresh if fresh else None),
    }
    return {"attacker": attacker, "victims": alloc["victims"],
            "actor_merchants": alloc["actor_merchants"], "manifest": manifest,
            "public_attacker": allocator.public_attacker_view(attacker)}


# --------------------------------------------------------------------------
# Fresh funded merchant provisioning (full recipe). Additive; new ids only.
# --------------------------------------------------------------------------
def _pw(name):
    p = config.ENV2 / "secrets" / name
    return p.read_text().strip() if p.exists() else ""


def _sh(s):
    return "'" + str(s).replace("'", "'\"'\"'") + "'"


def _mysql(container, db, pw, sql):
    out = subprocess.run(["docker", "exec", container, "sh", "-c",
                          "mysql -uroot -p%s -N -B -e %s %s" % (_sh(pw), _sh(sql), db)],
                         capture_output=True, text=True, timeout=30)
    return out.returncode, out.stdout, out.stderr[:400]


def _psql(pw, sql):
    out = subprocess.run(["docker", "exec", "env2_compose-postgres-ledger-1", "sh", "-c",
                          "PGPASSWORD=%s psql -U ledger -d ledger -tA -c %s" % (_sh(pw), _sh(sql))],
                         capture_output=True, text=True, timeout=30)
    return out.returncode, out.stdout, out.stderr[:400]


def _ids_for(campaign_id, role):
    num = 90000000 + (int(hashlib.sha256((campaign_id + role).encode()).hexdigest(), 16) % 9000000)
    mtok = str(num)[-2:]
    return {
        "merchant_id": "ARENAM%08d" % num,
        "balance_id": "ARENABAL%06d" % (num % 1000000),
        "banking_account": "ARENABA%07d" % (num % 10000000),
        "counter": "ARENACTR%06d" % (num % 1000000),
        "fund_account_row": "ARENAFA%07d" % (num % 10000000),
        "fa_account_id": "ARENAFAX%06d" % (num % 1000000),
        "bank_account": "ARENABK%07d" % (num % 10000000),
        "purpose": "ARENAPP%07d" % (num % 10000000),
        "acc_prefix": ("ARENA" + mtok)[:7].ljust(7, "0"),
        "num": num,
    }


def provision_funded_merchant(campaign_id, role="attacker", opening=10000000):
    """Provision one fresh funded merchant across the six datastores using the
    live-verified recipe. Idempotent. Returns a descriptor incl. verified:bool.
    Never mutates the M1/M2/M3 fixtures (uses a high, hashed id range)."""
    ids = _ids_for(campaign_id, role)
    mid = ids["merchant_id"]
    # Reserve and validate before the first database insertion or seed mutation.
    plan_id = reserve_plan_id(config.ENV2 / "seeds" / "generated", campaign_id, mid)
    secret = "S" + hashlib.sha256((campaign_id + role + "sec").encode()).hexdigest()[:40]
    key_id = "rzp_live_" + mid
    ts = int(time.time())
    result = {"merchant_id": mid, "key_id": key_id, "secret": secret,
              "balance_id": ids["balance_id"], "account_number": "2323230099999999",
              "fund_account_id": "fa_" + ids["fa_account_id"], "steps": [], "verified": False}

    pw_pay = _pw("mysql_payouts_root_password.txt")
    pw_xbal = _pw("mysql_xbalances_root_password.txt")
    pw_api = _pw("mysql_apidb_root_password.txt")
    pw_led = _pw("postgres_ledger_password.txt")

    def step(name, rc, err):
        result["steps"].append({"step": name, "ok": rc == 0, "err": err if rc != 0 else None})

    # 1 apidb-stub
    rc, _, e = _mysql("env2_compose-mysql-apidb-stub-1", "api_local", pw_api,
        "INSERT IGNORE INTO merchants (id,name,live,activated,created_at) "
        "VALUES ('%s','Arena Fresh %s',1,1,%d);" % (mid, role, ts))
    step("apidb.merchants", rc, e)
    rc, _, e = _mysql("env2_compose-mysql-apidb-stub-1", "api_local", pw_api,
        "INSERT IGNORE INTO `balance` (id,merchant_id,balance,currency,type,name,on_hold,credits,"
        "fee_credits,refund_credits,account_number,account_type,channel,locked_balance,created_at,updated_at) "
        "VALUES ('%s','%s',%d,'INR','banking','fresh',0,0,0,0,'2323230099999999','shared',NULL,0,%d,%d);"
        % (ids["balance_id"], mid, opening, ts, ts))
    step("apidb.balance", rc, e)

    # 2 x-balances
    rc, _, e = _mysql("env2_compose-mysql-xbalances-1", "rx_balances_local", pw_xbal,
        "INSERT IGNORE INTO balance (id,created_at,updated_at,status,merchant_id,account_number,"
        "account_type,channel,currency,balance,priority,last_change_at,last_fetched_at,last_attempted_at,"
        "fts_fund_account_id) VALUES ('%s',%d,%d,'activated','%s','2323230099999999','pool','rbl','INR',"
        "%d,0,%d,%d,0,'900001');" % (ids["balance_id"], ts, ts, mid, opening, ts, ts))
    step("xbalances.balance", rc, e)

    # 3 payouts
    rc, _, e = _mysql(C_PAYOUTS, "payouts", pw_pay,
        "INSERT IGNORE INTO banking_accounts (id,merchant_id,balance_id,channel,status,account_number,"
        "account_type,fts_fund_account_id,payout_service_enabled,counter_migrated,created_at,updated_at) "
        "VALUES ('%s','%s','%s',NULL,'activated','2323230099999999','shared','900001',1,1,%d,%d);"
        % (ids["banking_account"], mid, ids["balance_id"], ts, ts))
    step("payouts.banking_accounts", rc, e)
    rc, _, e = _mysql(C_PAYOUTS, "payouts", pw_pay,
        "INSERT IGNORE INTO counters (id,balance_id,free_payouts_consumed_last_reset_at,"
        "free_payouts_consumed,account_type,created_at,updated_at) VALUES ('%s','%s',%d,0,'shared',%d,%d);"
        % (ids["counter"], ids["balance_id"], ts, ts, ts))
    step("payouts.counters", rc, e)
    rc, _, e = _mysql(C_PAYOUTS, "payouts", pw_pay,
        "INSERT IGNORE INTO fund_accounts (id,account_type,account_id,created_at,updated_at) "
        "VALUES ('%s','bank_account','%s',%d,%d);" % (ids["fund_account_row"], ids["fa_account_id"], ts, ts))
    step("payouts.fund_accounts", rc, e)
    rc, _, e = _mysql(C_PAYOUTS, "payouts", pw_pay,
        "INSERT IGNORE INTO bank_accounts (id,ifsc_code,bank_identifier,identifier_type,created_at,updated_at) "
        "VALUES ('%s','ARNA0000001',NULL,'IFSC',%d,%d);" % (ids["bank_account"], ts, ts))
    step("payouts.bank_accounts", rc, e)

    # 4 ledger (4 sub-accounts; fund the merchant-va one)
    acc = ids["acc_prefix"]
    rows = []
    for i, bal in ((1, opening), (2, 0), (3, 0), (4, 0)):
        rows.append("('%sAC%04d','%s','ACTIVATED',%d,0,NULL,%d,%d,NULL)" % (acc, i, mid, bal, ts, ts))
    rc, _, e = _psql(pw_led,
        "INSERT INTO accounts (id,merchant_id,status,balance,min_balance,negative_balance,created_at,"
        "updated_at,deleted_at) VALUES %s ON CONFLICT (id) DO NOTHING;" % ",".join(rows))
    step("ledger.accounts", rc, e)
    # All four sub-account roles, matching the working M1 fixture (merchant_va,
    # merchant_va_vendor, commission/cash, va_gst). Every detail row carries the
    # banking_account_id entity key the way M1 does; without the vendor/commission/
    # gst rows any flow resolving those sub-accounts would fail.
    bacc = "bacc_" + ids["banking_account"]
    roles = [
        (1, "liability", "payable", "merchant_va", "Merchant Balance"),
        (2, "liability", "payable", "merchant_va_vendor", "Vendor Payable"),
        (3, "revenue", "cash", "merchant_va", "Commission Income"),
        (4, "liability", "payable", "va_gst", "Output GST"),
    ]
    det_rows = []
    for i, cat, atype, ftype, label in roles:
        ent = ('{"account_type":["%s"],"fund_account_type":["%s"],"banking_account_id":["%s"]}'
               % (atype, ftype, bacc))
        det_rows.append(
            "('%sDT%02d','%sAC%04d','%s - %s','%s','ARENAPRACC%04d','INR','%s','real','%s',NULL,%d,%d,NULL,'X',0)"
            % (acc, i, acc, i, label, mid, mid, i, cat, ent, ts, ts))
    det = ("INSERT INTO account_details (id,account_id,account_name,merchant_id,parent_account_id,currency,"
           "account_category,business_category,entities,description,created_at,updated_at,deleted_at,tenant,"
           "use_split_accounts) VALUES %s ON CONFLICT (id) DO NOTHING;" % ",".join(det_rows))
    rc, _, e = _psql(pw_led, det)
    step("ledger.account_details", rc, e)

    # 6 fts merchant->pool mapping (source_account_mappings + account_type_mappings)
    pw_fts = _pw("mysql_fts_root_password.txt")
    for mode in ("IMPS", "NEFT"):
        rc, _, e = _mysql("env2_compose-mysql-fts-1", "fts", pw_fts,
            "INSERT IGNORE INTO source_account_mappings (operation,merchant_id,product,channel,"
            "mozart_identifier,source_account_id,source_account_type,account_type,mode,title,priority,"
            "routing_enabled,created_at,created_by,updated_at,integration_type,creation_reason) VALUES "
            "('TRANSFER','%s','PAYOUT','RBL','v1',900001,'NODAL','POOL','%s','fresh_%s',1,1,%d,'arena_seed',%d,"
            "'API','arena fresh');" % (mid, mode, mode, ts, ts))
        step("fts.source_account_mappings.%s" % mode, rc, e)
        rc, _, e = _mysql("env2_compose-mysql-fts-1", "fts", pw_fts,
            "INSERT IGNORE INTO account_type_mappings (mode,product,account_type,merchant_id,created_at,"
            "created_by,updated_at) VALUES ('%s','PAYOUT','NODAL','%s',%d,'arena_seed',%d);"
            % (mode, mid, ts, ts))
        step("fts.account_type_mappings.%s" % mode, rc, e)

    # 5 cfa (contact + fund_account + hash_lookup)
    fa_hash = hashlib.sha256((mid + ids["fa_account_id"]).encode()).hexdigest()
    contact = "ARENACO%07d" % (ids["num"] % 10000000)
    js = (
        'db.contacts.updateOne({id:"%s"},{$setOnInsert:{active:true,contact:"",created_at:%d,'
        'email:"fresh@arena.test",hash:"%s",id:"%s",merchant_id:"%s",name:"Arena Fresh",notes:{},'
        'reference_id:"SYNTHETIC-%s",type:"employee",updated_at:%d}},{upsert:true});'
        'db.fund_accounts.updateOne({id:"%s"},{$setOnInsert:{account_type:"bank_account",active:true,'
        'bank_account:{account_number:"1112220099",bank_identifier:"RATN",id:"%s",ifsc:"RATN0000001",'
        'ifsc_code:"RATN0000001",name:"Arena Fresh"},contact_id:"%s",created_at:%d,hash:"%s",id:"%s",'
        'merchant_id:"%s",updated_at:%d}},{upsert:true});'
        'db.hash_lookup.updateOne({id:"ARENAHL%07d"},{$setOnInsert:{created_at:%d,entity_id:"%s",'
        'entity_type:"fund_accounts",hash:"%s",id:"ARENAHL%07d",updated_at:%d}},{upsert:true});'
    ) % (contact, ts * 1000, fa_hash, contact, mid, ids["fa_account_id"], ts * 1000,
         ids["fa_account_id"], ids["bank_account"], contact, ts * 1000, fa_hash, ids["fa_account_id"],
         mid, ts * 1000, ids["num"] % 10000000, ts * 1000, ids["fa_account_id"], fa_hash,
         ids["num"] % 10000000, ts * 1000)
    mongo_pw = _pw("mongo_cfa_root_password.txt")
    out = subprocess.run(["docker", "exec", "env2_compose-mongo-cfa-1", "mongosh", "cfa",
                          "--quiet", "--username", "cfa_root", "--password", mongo_pw,
                          "--authenticationDatabase", "admin", "--eval", js],
                         capture_output=True, text=True, timeout=30)
    step("cfa", out.returncode, (out.stderr or out.stdout)[:400])

    # 6b monolith merchant-config: payout create fetches merchant config via
    # GET /v1/internal/merchants/{id}; a 404 here (fresh id absent from the
    # monolith merchants.json seed) is the create-500 root cause. Register the
    # merchant + a pricing plan + a free-payout counter (all reloaded by the
    # single monolith restart in step 7 below).
    # Fidelity: the substitute returns this plan ID as pricing_rule_id;
    # no separate rule-level ID is seeded. It was reserved before step 1.
    mono_m_err = _register_monolith_merchant(mid, ids, plan_id)
    step("monolith.merchant_config", 0 if not mono_m_err else 1, mono_m_err)
    price_err = _register_pricing(mid, plan_id, ids["balance_id"])
    step("pricing.plan", 0 if not price_err else 1, price_err)
    result["pricing_plan_id"] = plan_id

    # 7 monolith-stub fund_accounts seed (payout create resolves the fund account
    # via monolith /fund_accounts_internal, which reads this file at boot). This
    # step's restart reloads merchant-config, pricing and fund_accounts together.
    mono_err = _register_monolith_fund_account(mid, ids, contact)
    step("monolith.fund_accounts", 0 if not mono_err else 1, mono_err)

    # 8 kong key registration + restart
    reg_err = register_kong_key(mid, secret)
    step("kong.register", 0 if not reg_err else 1, reg_err)

    result["contact_id"] = contact
    return result


def _register_monolith_merchant(merchant_id, ids, plan_id, restart=False):
    """Register the fresh merchant in monolith-stub's merchant-config seed so
    payout create's GET /v1/internal/merchants/{id} returns 200 (not 404).
    Modelled field-for-field on the working M1 fixture. Idempotent."""
    validate_pricing_id(plan_id)
    path = config.ENV2 / "seeds" / "generated" / "monolith" / "merchants.json"
    try:
        doc = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        return "monolith merchants.json unreadable: %s" % e
    doc.setdefault("merchants", {})[merchant_id] = {
        "account_type": "shared", "balance_id": ids["balance_id"], "channel": "",
        "fts_fund_account_id": 900001, "fts_source_account_id": 900001,
        "merchant": {
            "activated": True, "billing_label": "Arena Fresh " + merchant_id,
            "business_banking": True, "category": "other", "category2": "other",
            "country_code": "IN", "created_at": int(time.time()),
            "email": "fresh@arena.test",
            "feature": ["payout_service_enabled", "banking"], "hold_funds": False,
            "id": merchant_id, "live": True, "name": "Arena Fresh Merchant",
            "org_id": "ARENAORG000001", "pricing_plan_id": plan_id,
            "purpose_code": "P0806"},
        "merchant_detail": {
            "business_name": "Arena Fresh", "business_registered_address": "1 Arena Street",
            "business_registered_address_l2": "", "business_registered_city": "Bengaluru",
            "business_registered_country": "IN", "business_registered_pin": "560001",
            "business_registered_state": "Karnataka", "business_type": "individual",
            "iec_code": ""}}
    path.write_text(json.dumps(doc, indent=2))
    if restart:
        rs = subprocess.run(["docker", "restart", "env2_compose-monolith-stub-1"],
                            capture_output=True, text=True, timeout=40)
        if rs.returncode != 0:
            return "monolith restart failed: %s" % rs.stderr[:200]
        time.sleep(3)
    return None


def _register_pricing(merchant_id, plan_id, balance_id):
    """Add a fresh pricing plan (keyed by merchant_id, so monolith-stub's
    PRICING.get(mid) resolves) plus a free-payout counter keyed by balance_id.
    Rules mirror the M1 synthetic tariff. Idempotent."""
    validate_pricing_id(plan_id)
    path = config.ENV2 / "seeds" / "generated" / "pricing.json"
    try:
        doc = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        return "pricing.json unreadable: %s" % e
    tariff = {"IMPS": (200, 36), "NEFT": (100, 18), "RTGS": (500, 90), "UPI": (50, 9)}
    rules = {}
    for mode, (fees, tax) in tariff.items():
        rules[mode] = {"_classification": "ASSUMED synthetic tariff (fresh merchant, mirrors M1)",
                       "channel": "*", "fee_type": "*", "fees": fees, "method": "fund_transfer",
                       "min_amount": 1, "tax": tax}
    doc.setdefault("plans", {})[merchant_id] = {"plan_id": plan_id, "rules": rules}
    doc.setdefault("free_payout_counters", {})[merchant_id] = {
        "balance_id": balance_id, "free_payouts_consumed": 0,
        "free_payouts_consumed_last_reset_at": int(time.time())}
    path.write_text(json.dumps(doc, indent=2))
    return None


def _register_monolith_fund_account(merchant_id, ids, contact_id, restart=True):
    """Add the fresh fund account to monolith-stub's seed file + reload it."""
    path = config.ENV2 / "seeds" / "generated" / "monolith" / "fund_accounts.json"
    try:
        doc = json.loads(path.read_text())
    except Exception as e:  # noqa: BLE001
        return "fund_accounts.json unreadable: %s" % e
    fas = doc.get("fund_accounts", doc)
    key = ids["fa_account_id"]
    fas[key] = {
        "account_type": "bank_account", "active": True,
        "bank_account": {"account_number": "1112220099", "account_type": None,
                         "bank_identifier": "RATN", "bank_name": "RBL Bank",
                         "id": "ba_" + ids["bank_account"], "ifsc": "RATN0000001",
                         "ifsc_code": "RATN0000001", "name": "Arena Fresh"},
        "batch_id": "", "card": {},
        "contact": {"active": True, "contact": "", "email": "fresh@arena.test",
                    "entity": "contact", "id": "cont_" + contact_id, "name": "Arena Fresh",
                    "type": "employee"},
        "contact_id": "cont_" + contact_id, "created_at": int(time.time()),
        "entity": "fund_account", "id": "fa_" + key, "vpa": {}, "wallet": {},
        "merchant_id": merchant_id}
    if "fund_accounts" in doc:
        doc["fund_accounts"] = fas
    else:
        doc = fas
    path.write_text(json.dumps(doc, indent=2))
    if restart:
        rs = subprocess.run(["docker", "restart", "env2_compose-monolith-stub-1"],
                            capture_output=True, text=True, timeout=40)
        if rs.returncode != 0:
            return "monolith restart failed: %s" % rs.stderr[:200]
        time.sleep(3)
    return None


def register_kong_key(merchant_id, secret, secret_file=None, restart=True):
    """Add a merchant key to kong-lite's merchants.json + secret volume and
    restart kong so it reloads. Returns None on success, else an error string."""
    secret_file = secret_file or ("merchant_fresh_%s" % merchant_id.lower())
    merchants_path = config.ENV2 / "seeds" / "generated" / "merchants.json"
    try:
        doc = json.loads(merchants_path.read_text())
    except Exception as e:  # noqa: BLE001
        return "merchants.json unreadable: %s" % e
    doc.setdefault("merchants", {})[merchant_id] = {
        "key_id_live": "rzp_live_" + merchant_id, "key_id_test": "rzp_test_" + merchant_id,
        "roles": [], "secret_file": secret_file}
    merchants_path.write_text(json.dumps(doc, indent=2))
    # write secret into the kong secrets volume via a helper container
    hostdir = config.ENV2 / "secrets" / "merchant-keys"
    (hostdir / (secret_file + ".txt")).write_text(secret)
    cp = subprocess.run(["docker", "run", "--rm", "-v", "rzp-arena-secrets-kong:/s",
                         "-v", "%s:/in:ro" % hostdir, "alpine", "sh", "-c",
                         "cp /in/%s.txt /s/merchants/" % secret_file],
                        capture_output=True, text=True, timeout=40)
    if cp.returncode != 0:
        return "secret volume copy failed: %s" % cp.stderr[:200]
    if restart:
        rs = subprocess.run(["docker", "restart", "env2_compose-kong-lite-1"],
                            capture_output=True, text=True, timeout=40)
        if rs.returncode != 0:
            return "kong restart failed: %s" % rs.stderr[:200]
        time.sleep(4)
    return None


def verify_merchant(descriptor):
    """Confirm a provisioned merchant can create+fetch a payout through kong."""
    auth = _auth(descriptor["key_id"], descriptor["secret"])
    ik = "verify-" + uuid.uuid4().hex[:10]
    body = {"fund_account_id": descriptor["fund_account_id"],
            "account_number": descriptor["account_number"], "amount": 1000, "currency": "INR",
            "mode": "IMPS", "purpose": "refund", "queue_if_low_balance": False,
            "merchant_id": descriptor["merchant_id"]}
    st, resp = _kong_call("POST", "/v1/payouts", auth, body, {"X-Payout-Idempotency": ik})
    ok = False
    pid = None
    try:
        pid = json.loads(resp).get("id")
        ok = (st == 200 and bool(pid))
    except ValueError:
        pass
    descriptor["verified"] = ok
    descriptor["verify_status"] = st
    descriptor["verify_payout_id"] = pid
    if not ok:
        descriptor["verify_body"] = resp[:300]
    return ok
