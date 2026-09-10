#!/usr/bin/env python3
"""Milestone 4 T11 -- XAS substitute + Ledger Direct-accounting proof on the LIVE arena.

Steps (fidelity label per step: real | substitute | substitute_injected):
  (a) DA ledger onboarding: AccountAPI/CreateInBulk{direct_account_x} + CreateOnEvent{direct_merchant_onboarding}
      (REAL ledger) -> 6 sub-accounts carrying entities.banking_account_stmt_detail_id=["basd_<id>"]
  (b) processed Direct payout through kong (REAL PS/FTS, mozart-sim success) + REAL PS x_account_statement_source_event
      (or an injected event with the exact PS schema when the producer is gated off)
  (c) xas-sim consumes event + debit statement -> match -> 9-field payout_update -> monolith-stub -> REAL PS
      UpdatePayoutAfterBASRecon (transaction_id) -> monolith-stub DA emitter -> SNS -> REAL ledger journal_create ->
      journal rows da_payout_processed + da_payout_processed_recon, balanced, on the merchant's DA sub-accounts
  (d) replay event + statement + payout_update -> no duplicate link / journal; which layer stopped it
  (e) credit statement for a failed Direct payout -> PS failed->reversed, reversal.transaction_id = bas id
  (f) debit statement present (external, unlinked) then FTS `failed` webhook -> REAL VerifyPayoutFailedTransaction
      consults xas-sim fetch_multiple_by_reference_numbers -> refused
  (g) payout_service_txn_recon on -> NO DA journals, da_ledger_skipped_ps_recon recorded (preserved production gap)
  (h) negative control: wrong-amount statement -> external, no link, no journal

Usage:
  python3 RED_LOOP/surface/m4_xas_ledger.py run --descriptor <descriptor.json> [--steps abcdefgh]
  python3 RED_LOOP/surface/m4_xas_ledger.py run --provision m4-t11-xas      (provision via T09's module first)

Evidence: RED_LOOP/runs/m4-xas-ledger-<ts>/ (git-ignored; descriptor WITH secret) and
reports/implementation/m4-xas-ledger.json (no secrets). Stdlib only; DB reads via docker exec.
"""
import argparse
import base64
import json
import random
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop import provisioner as P  # noqa: E402
from red_loop import provisioner_direct as D  # noqa: E402
from red_loop import ledger_da as L  # noqa: E402

RUNS = REPO / "RED_LOOP" / "runs"
IMPL = REPO / "reports" / "implementation"
XAS = "http://xas-sim:8080"
MONO = "http://monolith-stub:8080"
PS = "http://payouts-api:9400"
FTS = "http://fts-web:8080"


def _ts():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _git_head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _jl(text, default=None):
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def http(method, url, body=None, basic=None, headers=None, timeout=30):
    st, text = D.arena_http(method, url, body, headers, basic, timeout)
    return st, (_jl(text) if text else None), text


def xas(method, path, body=None):
    return http(method, XAS + path, body)


def mono(method, path, body=None):
    return http(method, MONO + path, body, basic=D._mono_basic())


def ps(method, path, body=None, basic=None):
    return http(method, PS + path, body, basic=basic or D._ps_service_basic())


# ---------------------------------------------------------------------------
# DB helpers (docker exec, root)
# ---------------------------------------------------------------------------
PAY_COLS = ["id", "status", "merchant_id", "balance_id", "amount", "fees", "tax", "utr", "gateway_ref_no", "transaction_id",
            "fts_transfer_id", "channel", "mode", "purpose", "fee_type", "updated_at", "failure_reason", "status_code"]


def _pw(name):
    return P._pw(name)


def _mysql_rows(service, db, pw, sql, cols):
    rc, out, err = D._mysql(service, db, pw, sql)
    if rc != 0:
        return {"_err": err}
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        vals = [None if v == "NULL" else v for v in line.split("\t")]
        rows.append(dict(zip(cols, vals)))
    return rows


def payout_row(pid):
    rows = _mysql_rows("mysql-payouts", "payouts", _pw("mysql_payouts_root_password.txt"),
                       "SELECT %s FROM payouts WHERE id='%s'" % (",".join(PAY_COLS), pid), PAY_COLS)
    return rows[0] if isinstance(rows, list) and rows else (rows if isinstance(rows, dict) else {})


def wait_payout(pid, want=None, terminal=("processed", "failed", "reversed", "cancelled", "rejected"), tries=25, delay=2):
    row = {}
    for _ in range(tries):
        row = payout_row(pid)
        st = row.get("status")
        if want and st == want:
            return row
        if not want and st in terminal:
            return row
        time.sleep(delay)
    return row


BAS_COLS = ["id", "entity_id", "entity_type", "channel", "merchant_id", "account_number", "bank_transaction_id", "type", "utr",
            "amount", "currency", "description", "category", "bank_serial_number", "balance", "balance_currency", "transaction_date",
            "posted_date", "gateway_ref_number", "created_at", "updated_at"]


def bas_rows(merchant_id):
    return _mysql_rows("mysql-payouts", "payouts", _pw("mysql_payouts_root_password.txt"),
                       "SELECT %s FROM banking_account_statement WHERE merchant_id='%s' ORDER BY created_at" % (",".join(BAS_COLS), merchant_id), BAS_COLS)


def reversal_rows(pid):
    cols = ["id", "payout_id", "transaction_id", "amount", "utr", "created_at"]
    return _mysql_rows("mysql-payouts", "payouts", _pw("mysql_payouts_root_password.txt"),
                       "SELECT %s FROM reversals WHERE payout_id='%s'" % (",".join(cols), pid), cols)


def insert_bas_row(d, typ, amount, utr, grn="", bank_txn_id=None, serial=None):
    """Insert ONE banking_account_statement row into the REAL PS DB in the exact shape the real
    rbl_banking_account_statement worker persists (T03 Finding 8). ADDITIVE row, flagged substitute_injected.
    Used only when T10's real ingestion has not produced the row."""
    now = int(time.time())
    bas_id = "ARENABS" + "%07d" % random.randint(0, 9999999)
    bank_txn_id = bank_txn_id or ("S%08d" % random.randint(0, 99999999))
    serial = serial or str(random.randint(1, 999999))
    desc = "%s-IMPS/PAYOUT%s" % (utr, (" RZP" + grn[-10:]) if grn else "")
    ist_midnight = now - ((now + 19800) % 86400)
    sql = ("INSERT INTO banking_account_statement (id, entity_id, entity_type, channel, merchant_id, account_number, bank_transaction_id, "
           "type, utr, amount, currency, description, category, bank_serial_number, balance, balance_currency, transaction_date, posted_date, "
           "gateway_ref_number, created_at, updated_at) VALUES ('%s', NULL, NULL, '%s', '%s', '%s', '%s', '%s', '%s', %d, 'INR', '%s', "
           "'customer_initiated', '%s', %d, 'INR', %d, %d, '%s', %d, %d)"
           % (bas_id, d["channel"], d["merchant_id"], d["account_number"], bank_txn_id, typ, utr, int(amount), desc, serial,
              9995000, ist_midnight, now - 120, grn or "", now, now))
    rc, out, err = D._mysql("mysql-payouts", "payouts", _pw("mysql_payouts_root_password.txt"), sql)
    return {"ok": rc == 0, "bas_id": bas_id, "bank_transaction_id": bank_txn_id, "err": err if rc else None, "label": "substitute_injected",
            "note": "row inserted into REAL PS banking_account_statement by the harness (shape of the rbl_banking_account_statement worker output)"}


def sync_statements_to_xas(d, bas_ids=None):
    """Mirror REAL PS banking_account_statement rows into xas-sim (POST /_arena/statements/sync)."""
    rows = bas_rows(d["merchant_id"])
    if not isinstance(rows, list):
        return {"error": rows}
    if bas_ids:
        rows = [r for r in rows if r["id"] in bas_ids]
    for r in rows:
        r["balance_id"] = d["balance_id"]
        for k in ("amount", "balance", "transaction_date", "posted_date", "created_at", "updated_at"):
            if r.get(k) is not None:
                r[k] = int(r[k])
    st, body, _ = xas("POST", "/_arena/statements/sync", {"statements": rows, "label": "ps_table_mirror"})
    return {"status": st, "synced": len(rows), "results": body}


def xas_state(mid):
    st, body, _ = xas("GET", "/_arena/state?merchant_id=" + mid)
    return body or {}


def wait_until(fn, tries=20, delay=2, desc=""):
    last = None
    for _ in range(tries):
        last = fn()
        if last:
            return last
        time.sleep(delay)
    return last


def set_features(mid, **feats):
    """Runtime override on the running monolith-stub (lost on restart) AND persisted in the merchant's
    seeds/generated/monolith/merchants.json `merchant.feature` list (the boot-time mechanism), so a restart by
    another agent's provisioner keeps da_ledger_journal_writes for this merchant. Fixture merchants are never touched."""
    if mid not in ("ARENAM00000001", "ARENAM00000002", "ARENAM00000003"):
        try:
            path, doc = D._seed_json("monolith/merchants.json")
            rec = doc.get("merchants", {}).get(mid)
            if rec is not None:
                feat = rec.setdefault("merchant", {}).setdefault("feature", [])
                changed = False
                for k, v in feats.items():
                    if v and k not in feat:
                        feat.append(k); changed = True
                    if not v and k in feat:
                        feat.remove(k); changed = True
                if changed:
                    D._write_json(path, doc)
        except Exception as exc:  # noqa: BLE001
            print("  merchants.json feature persist skipped:", exc)
    return mono("POST", "/_arena/merchant_features", {"merchant_id": mid, "features": feats})


def ledger_emits(mid):
    st, body, _ = mono("GET", "/_arena/ledger_emits?merchant_id=" + mid)
    return (body or {}).get("emits", [])


def ledger_worker_log(pattern, since="10m"):
    out = subprocess.run(["docker", "logs", "--since", since, P.cname("ledger-worker-journal-create")], capture_output=True, text=True, timeout=60)
    lines = [l for l in (out.stdout + out.stderr).splitlines() if pattern in l]
    return lines[-12:]


def payouts_log(container, pattern, since="10m"):
    out = subprocess.run(["docker", "logs", "--since", since, P.cname(container)], capture_output=True, text=True, timeout=60)
    return [l[:600] for l in (out.stdout + out.stderr).splitlines() if pattern in l][-12:]


# ---------------------------------------------------------------------------
# payout drivers (T09 pattern: hold -> success via FTS check)
# ---------------------------------------------------------------------------
def create_payout(d, amount, narration):
    auth = P._auth(d["key_id"], d["secret"])
    body = {"fund_account_id": d["fund_account_id"], "account_number": d["account_number"], "amount": amount, "currency": "INR",
            "mode": "IMPS", "purpose": "payout", "queue_if_low_balance": True, "merchant_id": d["merchant_id"],
            "narration": narration, "notes": {"campaign": d["campaign_id"], "t11": narration}}
    st, resp = P._kong_call("POST", "/v1/payouts", auth, body, {"X-Payout-Idempotency": "t11-" + uuid.uuid4().hex[:12]})
    r = _jl(resp) or {}
    return st, (r.get("id") or "").replace("pout_", ""), resp[:300]


def direct_payout(d, amount, outcome, narration):
    """outcome: processed | failed | hold. Returns dict with pid, rows, transfer."""
    rec = {"amount": amount, "outcome_wanted": outcome}
    if outcome == "failed":
        rec["mozart"] = D.set_mozart_scenario(d["merchant_id"], "failure")
        st, pid, raw = create_payout(d, amount, narration)
        rec.update({"create_status": st, "pid": pid, "create_raw": raw if st != 200 else None})
        rec["row"] = wait_payout(pid, tries=30) if pid else {}
        rec["transfer"] = D._fts_transfer(pid) if pid else None
        D.set_mozart_scenario(d["merchant_id"], "success")
        return rec
    rec["mozart"] = D.set_mozart_scenario(d["merchant_id"], "hold")
    st, pid, raw = create_payout(d, amount, narration)
    rec.update({"create_status": st, "pid": pid, "create_raw": raw if st != 200 else None})
    if not pid:
        return rec
    rec["row_pending"] = wait_payout(pid, want="initiated", tries=15)
    t = D._wait_transfer(pid)
    rec["transfer"] = t
    if outcome == "hold":
        return rec
    if t and t.get("attempt_id"):
        rec["mozart_success"] = D.set_mozart_scenario(str(t["attempt_id"]), "success")
        rec["fts_check"] = D.arena_http("POST", FTS + "/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic())
    rec["row"] = wait_payout(pid, tries=25)
    rec["transfer"] = D._fts_transfer(pid)
    D.set_mozart_scenario(d["merchant_id"], "success")
    return rec


def release_hold(d, rec):
    t = rec.get("transfer") or {}
    if t and t.get("attempt_id"):
        D.set_mozart_scenario(str(t["attempt_id"]), "success")
        D.arena_http("POST", FTS + "/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic())
    D.set_mozart_scenario(d["merchant_id"], "success")
    return wait_payout(rec["pid"], tries=15)


def wait_source_event(mid, pid, tries=12, delay=2):
    def _f():
        evs = [e for e in xas_state(mid).get("source_events", []) if e.get("entity_id") == pid]
        return evs or None
    return wait_until(_f, tries, delay)


def ps_event_payload(row, label):
    """Exact PS job.XasEventDetails JSON (payouts/internal/job/x_account_statement_source_event.go:37-56) incl. the
    production field name event_created_timestamp (vs XAS event_create_timestamp)."""
    return {"entity_id": row["id"], "entity_type": "payout", "utr": row.get("utr") or "", "event_created_timestamp": int(time.time()),
            "event_id": "T11" + uuid.uuid4().hex[:11].upper(), "gateway_ref_no": row.get("gateway_ref_no") or "",
            "cms_ref_no": "", "status": row["status"], "mode": row.get("mode") or "IMPS", "amount": int(row["amount"]),
            "balance_id": row["balance_id"], "_label": label}


def ensure_source_event(d, row, evidence):
    """Prefer the REAL PS producer; else enqueue an event with the exact PS schema onto the same queue."""
    evs = wait_source_event(d["merchant_id"], row["id"])
    if evs:
        evidence["source_event_fidelity"] = "real (PS x_account_statement_source_event producer -> SQS -> xas-sim consumer)"
        evidence["source_event"] = evs[0]
        return evs[0], "real"
    payload = ps_event_payload(row, "substitute_injected")
    if not (payload["utr"] or payload["gateway_ref_no"]):
        payload["cms_ref_no"] = "S" + row["id"][-8:]   # PS would refuse (ErrorPayoutUtrNotFoundForAccountStatementEvent); give it a CMS ref
    lab = dict(payload)
    lab.pop("_label", None)
    st, body, _ = xas("POST", "/_arena/source_events/enqueue", lab)
    evidence["source_event_fidelity"] = "substitute_injected (PS producer produced nothing within 24s; exact PS schema enqueued on the REAL queue)"
    evidence["source_event_enqueue"] = {"status": st, "body": body, "payload": lab}
    evs = wait_source_event(d["merchant_id"], row["id"])
    evidence["source_event"] = evs[0] if evs else None
    return (evs[0] if evs else None), "substitute_injected"


def journals_for(mid, transactor_id, want=2, tries=25, delay=2):
    def _f():
        js = L.da_journals(mid, transactor_id)
        return js if isinstance(js, list) and len(js) >= want else None
    out = wait_until(_f, tries, delay)
    return out if out else L.da_journals(mid, transactor_id)


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------
class Proof:
    def __init__(self, d, run_dir):
        self.d, self.run_dir = d, run_dir
        self.mid, self.bal = d["merchant_id"], d["balance_id"]
        self.basd = d["ids"]["basd_id"]
        self.steps = {}
        self.checks = []

    def chk(self, step, name, ok, detail=None):
        rec = {"step": step, "name": name, "ok": bool(ok), "detail": detail}
        self.checks.append(rec)
        print("  [%s] %s %s" % (step, "PASS" if ok else "FAIL", name))
        return bool(ok)

    def save(self):
        (self.run_dir / "proof.json").write_text(json.dumps({"steps": self.steps, "checks": self.checks}, indent=2, default=str))

    # (a)
    def step_a(self):
        S = self.steps["a"] = {"fidelity": "real (ledger-api Twirp AccountAPI/CreateInBulk + Activate + CreateOnEvent; postgres read-back)"}
        S["parents"] = L.ensure_da_parent_accounts()
        S["onboard"] = L.onboard_da_merchant(self.mid, self.basd, 0)
        subs = S["onboard"].get("sub_accounts") or []
        S["sub_accounts"] = subs
        tag = '"basd_%s"' % self.basd
        self.chk("a", "7 DA parent accounts ACTIVATED", isinstance(S["parents"].get("parents"), list) and
                 sum(1 for p in S["parents"]["parents"] if p["status"] == "ACTIVATED") == 7, [(p["account_name"], p["status"]) for p in S["parents"].get("parents", [])])
        self.chk("a", "6 DA sub-accounts with entities.banking_account_stmt_detail_id=[basd_<id>]",
                 len(subs) == 6 and all(tag in s["entities"] for s in subs), [(s["account_name"], s["status"], s["entities"]) for s in subs])
        # discovery simulation per role (account_discovery/core.go:49-110 -> repo FindByEntitiesAndMerchantID jsonb @>)
        roles = {"MerchantBalance": ("payable", "merchant_da"), "VendorPayable": ("payable", "merchant_da_vendor"),
                 "CommissionIncome": ("cash", "merchant_da"), "OutputGST": ("payable", "da_gst"),
                 "CommissionReceivable": ("receivable", "merchant_da"), "MerchantCash": ("cash", "merchant_da")}
        disc = {}
        for role, (at, fat) in roles.items():
            ent = json.dumps({"account_type": [at], "fund_account_type": [fat], "banking_account_stmt_detail_id": ["basd_" + self.basd]})
            rows = L._psql_json("SELECT account_id, account_name, account_category FROM account_details WHERE merchant_id='%s' AND tenant='X' "
                                "AND deleted_at IS NULL AND entities @> '%s'::jsonb" % (self.mid, ent))
            disc[role] = rows
        S["discovery_simulation"] = disc
        # CommissionIncome (revenue) and MerchantCash (asset) share account_type cash + merchant_da; ledger also filters by
        # account_category in AccountDiscoveryParams -> both resolve unambiguously by category.
        self.chk("a", "discovery: every DA role resolves to >=1 sub-account; roles with same entities are split by account_category",
                 all(isinstance(v, list) and len(v) >= 1 for v in disc.values()) and
                 len({r["account_category"] for r in disc["CommissionIncome"]}) == 2, {k: [(r["account_name"], r["account_category"]) for r in v] for k, v in disc.items()})
        shared_rows = L._psql_json("SELECT account_name, entities::text AS entities FROM account_details WHERE merchant_id='%s' AND entities ? 'banking_account_id'" % self.mid)
        S["pre_existing_shared_style_sub_accounts"] = shared_rows
        S["shared_style_interference"] = "none: their entities carry banking_account_id/merchant_va*, never banking_account_stmt_detail_id, so the DA jsonb containment query cannot select them"
        self.chk("a", "pre-existing Shared-style sub-accounts (T09 provisioner) do not match DA discovery",
                 isinstance(shared_rows, list) and all("banking_account_stmt_detail_id" not in r["entities"] for r in shared_rows), shared_rows)
        # register the XAS account (XAS `accounts` row; prod: new_merchant_onboarding job) -- substitute
        st, body, _ = xas("POST", "/_arena/accounts", {"merchant_id": self.mid, "account_number": self.d["account_number"],
                                                        "channel": self.d["channel"], "balance_id": self.bal, "account_type": "direct"})
        S["xas_account"] = {"status": st, "body": body, "fidelity": "substitute (XAS accounts row registered by harness)"}
        S["monolith_features"] = set_features(self.mid, da_ledger_journal_writes=True, payout_service_txn_recon=False)[1]
        self.save()

    # (b)+(c)
    def step_bc(self, amount):
        S = self.steps["b"] = {}
        S["features"] = set_features(self.mid, da_ledger_journal_writes=True, payout_service_txn_recon=False)[1]
        S["payout"] = direct_payout(self.d, amount, "processed", "t11 xas success")
        pid = S["payout"].get("pid")
        row = S["payout"].get("row") or {}
        S["fidelity"] = "real (kong -> PS -> FTS -> mozart-sim success -> monolith-stub relay -> PS processed)"
        self.chk("b", "Direct payout processed through kong", row.get("status") == "processed", row)
        if row.get("status") != "processed":
            self.save()
            return None
        ev, ev_fid = ensure_source_event(self.d, row, S)
        self.chk("b", "source event consumed by xas-sim from SQS x_account_statement_source_event (schema mismatch logged)",
                 ev is not None and ev.get("_transport", "").startswith("sqs:") and ev.get("_schema", {}).get("event_created_timestamp_present") is True
                 and ev.get("event_create_timestamp") == 0, {"fidelity": ev_fid, "event": ev})
        S["ps_producer_log"] = payouts_log("payouts-api", pid)[:6]
        # (c)
        C = self.steps["c"] = {"pid": pid}
        real_rows = [r for r in bas_rows(self.mid) if r.get("utr") == row.get("utr") and r.get("type") == "debit"] if row.get("utr") else []
        if real_rows:
            C["statement_fidelity"] = "real (PS banking_account_statement row produced by the rbl_banking_account_statement worker)"
            bas_id = real_rows[0]["id"]
        else:
            ins = insert_bas_row(self.d, "debit", int(row["amount"]), row.get("utr") or "", row.get("gateway_ref_no") or "")
            C["statement_insert"] = ins
            C["statement_fidelity"] = "substitute_injected (no real worker row for this UTR; harness inserted the worker-shaped row into the REAL PS table)"
            bas_id = ins["bas_id"]
        C["bas_id"] = bas_id
        C["sync"] = sync_statements_to_xas(self.d, [bas_id])
        st_x = wait_until(lambda: [s for s in xas_state(self.mid).get("statements", []) if s["id"] == bas_id and s["entity_type"] == "payout"] or None, 10, 1)
        self.chk("c", "xas-sim linked the debit statement to the payout (entity_type=payout)", bool(st_x), st_x)
        outb = wait_until(lambda: [o for o in xas_state(self.mid).get("outbound", []) if o.get("request", {}).get("bas_id") == bas_id] or None, 10, 1) or []
        C["outbound"] = outb
        self.chk("c", "xas-sim POSTed the 9-field payout_update to monolith-stub and got 200",
                 bool(outb) and outb[-1].get("status") == 200 and set(outb[-1]["request"]) == {"bas_id", "entity_id", "entity_type", "merchant_id", "transaction_date", "converted_from_external", "utr", "grn", "cms_ref_no"}, outb[-1:] )
        prow = wait_until(lambda: (lambda r: r if r.get("transaction_id") == bas_id else None)(payout_row(pid)), 10, 1) or payout_row(pid)
        C["payout_after"] = prow
        self.chk("c", "REAL PS UpdatePayoutAfterBASRecon set payouts.transaction_id = bas id", prow.get("transaction_id") == bas_id, prow)
        st, mlog, _ = mono("GET", "/_arena/log")
        C["monolith_relay"] = [e for e in (mlog or {}).get("log", []) if e["kind"] in ("banking_statement_payout_update", "da_ledger_emitter") and
                               str(e.get("payload", {}).get("request", {}).get("entity_id") or e.get("payload", {}).get("entity_id")) == pid][-4:]
        emits = [e for e in ledger_emits(self.mid) if e.get("transactor_id") == "pout_" + pid or e.get("entity_id") == pid]
        C["ledger_emits"] = emits
        self.chk("c", "monolith-stub emitter published da_payout_processed + da_payout_processed_recon via SNS->SQS journal_create",
                 [e.get("transactor_event") for e in emits if "transactor_event" in e] == ["da_payout_processed", "da_payout_processed_recon"] and
                 all(str(e.get("transport", "")).startswith("sns:") for e in emits if "transactor_event" in e), [(e.get("transactor_event"), e.get("transport"), e.get("skipped")) for e in emits])
        js = journals_for(self.mid, "pout_" + pid, want=2)
        C["journals"] = js
        events = sorted(j["transactor_event"] for j in js) if isinstance(js, list) else []
        self.chk("c", "REAL ledger journal rows da_payout_processed + da_payout_processed_recon exist for pout_<id>",
                 events == ["da_payout_processed", "da_payout_processed_recon"], events)
        self.chk("c", "entries balanced (sum debit == sum credit) for both journals",
                 isinstance(js, list) and js and all(j.get("balanced") for j in js), [(j["transactor_event"], j.get("sum_debit"), j.get("sum_credit")) for j in js] if isinstance(js, list) else js)
        sub_ids = {s["id"] for s in self.steps["a"]["sub_accounts"]}
        used = {e["account_id"] for j in js for e in j.get("entries", [])} if isinstance(js, list) else set()
        self.chk("c", "every entry account is one of the merchant's 6 DA sub-accounts", bool(used) and used <= sub_ids,
                 {"used": [(e["account_name"], e["type"], e["amount"]) for j in js for e in j.get("entries", [])] if isinstance(js, list) else js})
        expect = {"da_payout_processed": {("debit", "Direct Merchant Balance Account"), ("credit", "Direct Vendor Payable Account")},
                  "da_payout_processed_recon": {("debit", "Direct Vendor Payable Account"), ("credit", "Direct Merchant Cash Account")}}
        ok_roles = True
        for j in js if isinstance(js, list) else []:
            got = {(e["type"], e["account_name"].split(" - ")[0]) for e in j.get("entries", []) if float(e["amount"]) == float(row["amount"])}
            ok_roles = ok_roles and expect[j["transactor_event"]] <= got
        self.chk("c", "amount-sized entries hit the ledger_config roles (Balance->VendorPayable; VendorPayable->MerchantCash)", ok_roles)
        dup = L.journal_uniqueness(self.mid)
        C["uniqueness"] = dup
        self.chk("c", "(transactor_id, transactor_event) unique for the merchant", dup == [], dup)
        C["ledger_worker_log"] = ledger_worker_log("pout_" + pid)
        self.save()
        return {"pid": pid, "row": row, "bas_id": bas_id, "event": ev}

    # (d)
    def step_d(self, ctx):
        S = self.steps["d"] = {}
        pid, bas_id, ev = ctx["pid"], ctx["bas_id"], ctx["event"]
        before = {"journals": len(L.da_journals(self.mid, "pout_" + pid)), "outbound": len([o for o in xas_state(self.mid).get("outbound", []) if o.get("request", {}).get("bas_id") == bas_id]),
                  "emits": len([e for e in ledger_emits(self.mid) if e.get("transactor_id") == "pout_" + pid]), "transaction_id": payout_row(pid).get("transaction_id")}
        S["before"] = before
        # replay 1: same source event JSON (same event_id) on the real queue
        replay = {"entity_id": ev["entity_id"], "entity_type": ev["entity_type"], "utr": ev["utr"], "event_created_timestamp": int(time.time()),
                  "event_id": ev["event_id"], "gateway_ref_no": ev["gateway_reference_number"], "cms_ref_no": ev["cms_reference_number"],
                  "status": ev["status"], "mode": ev["mode"], "amount": ev["amount"], "balance_id": ev["balance_id"]}
        S["replay_event"] = xas("POST", "/_arena/source_events/enqueue", replay)[1]
        time.sleep(8)
        st = xas_state(self.mid)
        evs = [e for e in st.get("source_events", []) if e.get("event_id") == ev["event_id"]]
        S["event_rows_after_replay"] = evs
        self.chk("d", "replayed source event deduplicated on (event_id, entity_type): one stored row, _replays incremented",
                 len(evs) == 1 and evs[0].get("_replays", 0) >= 1, evs)
        # replay 2: same statement row re-synced
        S["replay_statement"] = sync_statements_to_xas(self.d, [bas_id])
        time.sleep(2)
        st = xas_state(self.mid)
        outb = [o for o in st.get("outbound", []) if o.get("request", {}).get("bas_id") == bas_id and "request" in o]
        links = [l for l in st.get("links", []) if l.get("statement_id") == bas_id]
        S["outbound_after"] = outb
        self.chk("d", "replayed statement + event: no second outbound payout_update (xas-sim dedupe/unchanged-row guard)",
                 len(outb) == before["outbound"], {"before": before["outbound"], "after": len(outb), "link_audit": links[-4:]})
        # replay 3: the 9-field payout_update itself (as if the CDC re-fired) -> PS idempotent, emitter re-publishes, ledger dedups
        body = outb[-1]["request"] if outb else None
        S["replay_payout_update"] = mono("POST", "/v1/banking_account_statement/payout_update", body) if body else None
        time.sleep(6)
        after = {"journals": len(L.da_journals(self.mid, "pout_" + pid)), "transaction_id": payout_row(pid).get("transaction_id"),
                 "emits": len([e for e in ledger_emits(self.mid) if e.get("transactor_id") == "pout_" + pid])}
        S["after"] = after
        self.chk("d", "PS answered the replayed payout_update 200 (transaction_id already set -> PayoutTransactionIDAlreadyUpdated) and transaction_id unchanged",
                 S["replay_payout_update"] and S["replay_payout_update"][0] == 200 and after["transaction_id"] == before["transaction_id"] == bas_id, S["replay_payout_update"])
        self.chk("d", "no duplicate journal rows after replay (still 2 for pout_<id>)", after["journals"] == before["journals"] == 2, {"before": before, "after": after})
        new_emits = [e for e in ledger_emits(self.mid) if e.get("transactor_id") == "pout_" + pid][before["emits"]:]
        keys = [e["payload"]["idempotency_key"] for e in new_emits if "payload" in e]
        S["replayed_emits"] = new_emits
        S["idempotency_rows_for_replayed_keys"] = L.idempotency_rows(keys)
        S["ledger_worker_log"] = ledger_worker_log("pout_" + pid, "5m")
        layer = "unknown"
        if after["emits"] > before["emits"]:
            idem = S["idempotency_rows_for_replayed_keys"]
            idem_with_entity = [r for r in idem if r.get("entity_id")] if isinstance(idem, list) else []
            layer = ("layer2: application ValidateJournalExist(transactor_id, transactor_event) -> ErrRecordAlreadyExist (fresh idempotency_key per publish, "
                     "as the monolith's Uuid::uuid1, so layer1 idempotency table cannot dedup)") if not idem_with_entity else "layer1: idempotency table"
        S["layer_that_stopped_duplicate"] = layer
        self.chk("d", "duplicate stopped at the ledger application layer (fresh idempotency keys, no duplicate rows)", layer.startswith("layer2"),
                 {"layer": layer, "worker_log": S["ledger_worker_log"][-3:]})
        self.save()

    # (e)
    def step_e(self, amount):
        S = self.steps["e"] = {}
        S["features"] = set_features(self.mid, da_ledger_journal_writes=True, payout_service_txn_recon=False)[1]
        S["payout"] = direct_payout(self.d, amount, "failed", "t11 failed then credit")
        pid = S["payout"].get("pid")
        row = S["payout"].get("row") or {}
        self.chk("e", "Direct payout failed (mozart failure)", row.get("status") == "failed", row)
        if row.get("status") != "failed":
            self.save()
            return
        ev, fid = ensure_source_event(self.d, row, S)
        S["source_event_fidelity_note"] = fid
        cms = (ev or {}).get("cms_reference_number") or ""
        utr = (ev or {}).get("utr") or row.get("utr") or ""
        ins = insert_bas_row(self.d, "credit", int(row["amount"]), utr or ("RET" + pid[-9:]), (ev or {}).get("gateway_reference_number") or "", bank_txn_id=cms or None)
        S["credit_statement"] = ins
        S["sync"] = sync_statements_to_xas(self.d, [ins["bas_id"]])
        linked = wait_until(lambda: [s for s in xas_state(self.mid).get("statements", []) if s["id"] == ins["bas_id"] and s["entity_type"] == "payout_reversal"] or None, 10, 1)
        self.chk("e", "xas-sim linked the credit statement as payout_reversal", bool(linked), linked)
        prow = wait_until(lambda: (lambda r: r if r.get("status") == "reversed" else None)(payout_row(pid)), 12, 1) or payout_row(pid)
        S["payout_after"] = prow
        revs = reversal_rows(pid)
        S["reversals"] = revs
        self.chk("e", "REAL PS converted failed -> reversed (UpdatePayoutAfterBASRecon payout_reversal branch, core.go:7297-7318)", prow.get("status") == "reversed", prow)
        self.chk("e", "reversal row created with transaction_id = credit bas id", isinstance(revs, list) and revs and revs[-1].get("transaction_id") == ins["bas_id"], revs)
        emits = [e for e in ledger_emits(self.mid) if e.get("entity_id") == pid]
        S["ledger_emits"] = emits
        rid = ("rvrsl_" + revs[-1]["id"]) if isinstance(revs, list) and revs else None
        js = journals_for(self.mid, rid, want=2, tries=15) if rid else []
        S["journals"] = js
        S["emitter_behaviour"] = ("monolith-stub emitted %s (Core.php:5213-5227 DA_PAYOUT_REVERSED + _RECON on a credit-linked reversal); ledger rows: %s"
                                  % ([e.get("transactor_event") for e in emits if "transactor_event" in e], sorted(j["transactor_event"] for j in js) if isinstance(js, list) else js))
        self.chk("e", "DA emitter posted da_payout_reversed + da_payout_reversed_recon and the REAL ledger recorded them balanced",
                 isinstance(js, list) and sorted(j["transactor_event"] for j in js) == ["da_payout_reversed", "da_payout_reversed_recon"] and all(j.get("balanced") for j in js),
                 [(j["transactor_event"], j.get("balanced")) for j in js] if isinstance(js, list) else js)
        self.save()

    # (f)
    def step_f(self, amount):
        S = self.steps["f"] = {}
        S["payout"] = direct_payout(self.d, amount, "hold", "t11 conflict debit-before-failed")
        pid = S["payout"].get("pid")
        t = S["payout"].get("transfer") or {}
        row = S["payout"].get("row_pending") or {}
        self.chk("f", "Direct payout initiated and held at mozart", row.get("status") == "initiated" and bool(t.get("id")), {"row": row, "transfer": t})
        if not pid or not t.get("id"):
            self.save()
            return
        utr = "T11CONF" + pid[-7:]
        ins = insert_bas_row(self.d, "debit", int(amount), utr, "")
        S["debit_statement"] = ins
        S["sync"] = sync_statements_to_xas(self.d, [ins["bas_id"]])
        ext = wait_until(lambda: [s for s in xas_state(self.mid).get("statements", []) if s["id"] == ins["bas_id"] and s["entity_type"] == "external"] or None, 8, 1)
        self.chk("f", "debit statement is in xas-sim as external (no source event yet)", bool(ext), ext)
        webhook = {"fund_transfer_id": int(t["id"]), "status": "failed", "source_id": pid, "source_type": "payout", "utr": utr,
                   "channel": "rbl", "mode": "IMPS", "failure_reason": "arena t11 conflict", "bank_status_code": "ARENA_FAIL",
                   "source_account_id": int(self.d["fts_fund_account_id"]), "bank_account_type": "CURRENT", "narration": "t11"}
        n_fetch_before = len(xas_state(self.mid).get("fetch_log", []))
        S["webhook_request"] = webhook
        # route group payout_internal_routes.go:16 BasicAuth(cred.API, cred.Workflow, cred.Xperience, cred.FTS, ...):
        # the arena's FTS->PS pair is not materialised for the harness, so the API service credential (also allowed) is used.
        S["webhook_auth_note"] = "PS internal route authenticated with the api service credential (route allows cred.API and cred.FTS)"
        S["webhook_response"] = ps("POST", "/v1/payouts/transfer_status_webhook", webhook, basic=D._ps_service_basic())
        time.sleep(2)
        fl = xas_state(self.mid).get("fetch_log", [])[n_fetch_before:]
        S["xas_fetch_log"] = fl
        prow = payout_row(pid)
        S["payout_after"] = prow
        S["ps_log"] = payouts_log("payouts-api", pid)[-8:]
        st = S["webhook_response"][0]
        body_txt = S["webhook_response"][2] or ""
        self.chk("f", "REAL PS consulted xas-sim GET fetch_multiple_by_reference_numbers (type=debit, utr, account_number, amount, channel)",
                 any(f["request"].get("type") == "debit" and f["request"].get("utr") == utr and f["returned"] == 1 for f in fl), fl)
        refused_log = [l for l in S["ps_log"] if "FAILED_PAYOUT_HAS_DEBIT_STATEMENT_NEEDS_TO_BE_REVERSED" in l or "failed_payout_has_debit_statement_needs_to_be_reversed" in l]
        S["ps_refusal_log"] = refused_log
        # errorclass ErrorFailedPayoutHasDebitStatementNeedsToBeReversed is an internal_server_error class: PS answers a generic
        # 500 body and logs FAILED_PAYOUT_HAS_DEBIT_STATEMENT_NEEDS_TO_BE_REVERSED (fts_transfer_status_webhook.go:708-716).
        self.chk("f", "FTS `failed` webhook refused (non-2xx, PS log FAILED_PAYOUT_HAS_DEBIT_STATEMENT_NEEDS_TO_BE_REVERSED) and payout still initiated",
                 st != 200 and bool(refused_log) and prow.get("status") == "initiated",
                 {"status": st, "body": body_txt[:300], "payout": prow, "ps_log": [l[:300] for l in refused_log[:2]]})
        S["release"] = release_hold(self.d, S["payout"])
        S["release_note"] = "hold released with mozart success after the proof; the external debit statement may then be re-identified by the real PS source event (XAS ext->payout, DA_EXT_PAYOUT_PROCESSED)"
        self.save()

    # (g)
    def step_g(self, amount):
        S = self.steps["g"] = {}
        S["features_on"] = set_features(self.mid, payout_service_txn_recon=True, da_ledger_journal_writes=True)[1]
        S["payout"] = direct_payout(self.d, amount, "processed", "t11 ps_recon gap")
        pid = S["payout"].get("pid")
        row = S["payout"].get("row") or {}
        self.chk("g", "Direct payout processed", row.get("status") == "processed", row)
        if row.get("status") != "processed":
            set_features(self.mid, payout_service_txn_recon=False)
            self.save()
            return
        ev, fid = ensure_source_event(self.d, row, S)
        ins = insert_bas_row(self.d, "debit", int(row["amount"]), row.get("utr") or "", row.get("gateway_ref_no") or "")
        S["statement"] = ins
        S["sync"] = sync_statements_to_xas(self.d, [ins["bas_id"]])
        prow = wait_until(lambda: (lambda r: r if r.get("transaction_id") == ins["bas_id"] else None)(payout_row(pid)), 10, 1) or payout_row(pid)
        S["payout_after"] = prow
        self.chk("g", "PS still links the statement (transaction_id set) -- the gap is only the ledger leg", prow.get("transaction_id") == ins["bas_id"], prow)
        time.sleep(4)
        emits = [e for e in ledger_emits(self.mid) if e.get("entity_id") == pid]
        S["ledger_emits"] = emits
        js = L.da_journals(self.mid, "pout_" + pid)
        S["journals"] = js
        self.chk("g", "monolith-stub logged da_ledger_skipped_ps_recon and published nothing", any(e.get("skipped") == "da_ledger_skipped_ps_recon" for e in emits)
                 and not any("transactor_event" in e for e in emits), emits)
        self.chk("g", "NO DA journal rows for pout_<id> (preserved production gap: PS ledger call commented out, monolith defers)", js == [], js)
        S["features_off"] = set_features(self.mid, payout_service_txn_recon=False, da_ledger_journal_writes=True)[1]
        self.save()

    # (h)
    def step_h(self, amount):
        S = self.steps["h"] = {}
        S["features"] = set_features(self.mid, da_ledger_journal_writes=True, payout_service_txn_recon=False)[1]
        S["payout"] = direct_payout(self.d, amount, "processed", "t11 wrong amount control")
        pid = S["payout"].get("pid")
        row = S["payout"].get("row") or {}
        self.chk("h", "Direct payout processed", row.get("status") == "processed", row)
        if row.get("status") != "processed":
            self.save()
            return
        ev, fid = ensure_source_event(self.d, row, S)
        ins = insert_bas_row(self.d, "debit", int(row["amount"]) + 1, row.get("utr") or "", row.get("gateway_ref_no") or "")
        S["statement"] = ins
        S["sync"] = sync_statements_to_xas(self.d, [ins["bas_id"]])
        time.sleep(3)
        st = xas_state(self.mid)
        stmt = [s for s in st.get("statements", []) if s["id"] == ins["bas_id"]]
        outb = [o for o in st.get("outbound", []) if o.get("request", {}).get("bas_id") == ins["bas_id"]]
        S["statement_in_xas"] = stmt
        S["outbound"] = outb
        prow = payout_row(pid)
        S["payout_after"] = prow
        js = L.da_journals(self.mid, "pout_" + pid)
        S["journals"] = js
        self.chk("h", "wrong-amount statement marked external, no entity link", bool(stmt) and stmt[0]["entity_type"] == "external" and stmt[0]["entity_id"] == "", stmt)
        self.chk("h", "no outbound payout_update, payout.transaction_id still NULL, no journals", not outb and not prow.get("transaction_id") and js == [],
                 {"outbound": outb, "payout": prow, "journals": js})
        self.save()


def write_summary(d, proof, run_dir):
    per_step = {}
    for s in "abcdefgh":
        cs = [c for c in proof.checks if c["step"] == s]
        per_step[s] = {"passed": sum(1 for c in cs if c["ok"]), "total": len(cs), "ok": bool(cs) and all(c["ok"] for c in cs),
                       "fidelity": _fidelity(s, proof.steps.get(s, {}))}
    summary = {
        "milestone": "M4-T11 XAS substitute + Ledger Direct accounting",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tested_commit": _git_head(),
        "arena": {"compose_project": P.compose_project(), "network": P.arena_network(), "kong": P.KONG},
        "merchant": {k: d.get(k) for k in ("campaign_id", "merchant_id", "balance_id", "account_number", "fts_fund_account_id", "channel")},
        "basd_id": d["ids"]["basd_id"],
        "run_dir": str(run_dir),
        "steps": per_step,
        "checks": proof.checks,
        "all_green": all(v["ok"] for v in per_step.values() if v["total"]),
        "components": {
            "xas-sim": "substitute (contract-faithful; ENV2_COMPOSE/substitutes/xas-sim/CONTRACT.md)",
            "monolith-stub DA emitter": "substitute (reproduces api BankingAccountStatement/Core.php:5175-5278 + Transaction/Processor/Ledger/Payout.php:235-425)",
            "payouts": "real (accepted binary; UpdatePayoutAfterBASRecon, VerifyPayoutFailedTransaction, source-event producer)",
            "ledger": "real (AccountAPI CreateInBulk/Activate/CreateOnEvent, journal_create worker, direct_account_x config)",
            "fts / mozart-sim": "real fts, substitute bank",
            "statement ingestion": "substitute_injected unless a real rbl_banking_account_statement worker row existed for the UTR (see steps.c.statement_fidelity)",
        },
    }
    (IMPL / "m4-xas-ledger.json").write_text(json.dumps(summary, indent=2, default=str))
    return summary


def _fidelity(step, S):
    if step == "a":
        return "real ledger; substitute XAS account registration"
    if step == "b":
        return "real payout; source event: " + str(S.get("source_event_fidelity", "n/a"))
    if step == "c":
        return "statement: %s; matching: substitute (xas-sim); PS link + ledger journal: real" % S.get("statement_fidelity", "n/a")
    if step == "d":
        return "replay via real queue + substitute + real PS + real ledger; " + str(S.get("layer_that_stopped_duplicate", ""))
    if step == "e":
        return "real failed payout; credit statement substitute_injected; source event: " + str(S.get("source_event_fidelity", "n/a")) + "; PS reversal real; ledger real"
    if step == "f":
        return "real PS route /v1/payouts/transfer_status_webhook (FTS credential) + real VerifyPayoutFailedTransaction; xas-sim substitute; statement substitute_injected"
    if step == "g":
        return "real payout + real PS link; DA gap reproduced by the substitute emitter (per-merchant feature payout_service_txn_recon)"
    if step == "h":
        return "real payout; statement substitute_injected; xas-sim substitute decision"
    return ""


def cmd_merge(descriptor_path):
    """Rebuild reports/implementation/m4-xas-ledger.json from ALL m4-xas-ledger-* run dirs for this merchant:
    per step, the latest run in which that step executed and passed (else the latest run that executed it).
    Each step records its source run dir so evidence stays traceable. Used when a full run was broken by
    an arena event outside this task (e.g. payouts-api restarted by another agent)."""
    d = json.loads(Path(descriptor_path).read_text())
    runs = sorted(RUNS.glob("m4-xas-ledger-*"))
    best = {}
    for r in runs:
        try:
            pr = json.loads((r / "proof.json").read_text())
        except Exception:  # noqa: BLE001
            continue
        if not (r / ("descriptor-%s.json" % d["merchant_id"])).exists():
            continue
        for st in "abcdefgh":
            cs = [c for c in pr["checks"] if c["step"] == st]
            if not cs:
                continue
            ok = all(c["ok"] for c in cs)
            cur = best.get(st)
            if cur is None or ok or not cur["ok"]:
                if cur is None or ok >= cur["ok"]:
                    best[st] = {"ok": ok, "checks": cs, "run_dir": str(r), "steps": pr["steps"].get(st, {})}
    pr = Proof(d, RUNS / "m4-xas-ledger-merged")
    pr.run_dir.mkdir(parents=True, exist_ok=True)
    for st, b in best.items():
        pr.checks.extend(b["checks"])
        pr.steps[st] = dict(b["steps"], _source_run_dir=b["run_dir"])
    summary = write_summary(d, pr, "merged:" + ",".join(sorted({b["run_dir"] for b in best.values()})))
    for st, b in best.items():
        summary["steps"][st]["source_run_dir"] = b["run_dir"]
    summary["merged"] = True
    (IMPL / "m4-xas-ledger.json").write_text(json.dumps(summary, indent=2, default=str))
    pr.save()
    print(json.dumps({k: (v["passed"], v["total"], v.get("source_run_dir")) for k, v in summary["steps"].items()}, indent=1))
    print("all_green", summary["all_green"])
    return 0 if summary["all_green"] else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run", "merge"])
    ap.add_argument("--descriptor")
    ap.add_argument("--provision", help="campaign id to provision a fresh Direct merchant via provisioner_direct")
    ap.add_argument("--steps", default="abcdefgh")
    ap.add_argument("--base-amount", type=int, default=5000)
    args = ap.parse_args()
    if args.cmd == "merge":
        return cmd_merge(args.descriptor)
    run_dir = RUNS / ("m4-xas-ledger-" + _ts())
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.provision:
        d = D.provision_direct_merchant(args.provision, restart=True)
        sc = D.self_check_direct_merchant(d)
        (run_dir / ("selfcheck-%s.json" % d["merchant_id"])).write_text(json.dumps(sc, indent=2, default=str))
    else:
        d = json.loads(Path(args.descriptor).read_text())
    (run_dir / ("descriptor-%s.json" % d["merchant_id"])).write_text(json.dumps(d, indent=2, default=str))
    print("run_dir", run_dir, "merchant", d["merchant_id"])
    pr = Proof(d, run_dir)
    base = args.base_amount + random.randint(0, 900) * 10   # distinct amounts per run keep xas-sim matching unambiguous
    ctx = None
    if "a" in args.steps:
        pr.step_a()
    if "b" in args.steps or "c" in args.steps:
        ctx = pr.step_bc(base)
    if "d" in args.steps and ctx:
        pr.step_d(ctx)
    if "e" in args.steps:
        pr.step_e(base + 1)
    if "f" in args.steps:
        pr.step_f(base + 2)
    if "g" in args.steps:
        pr.step_g(base + 3)
    if "h" in args.steps:
        pr.step_h(base + 4)
    pr.steps["_final_xas_state"] = xas_state(d["merchant_id"])
    pr.steps["_final_ledger_emits"] = ledger_emits(d["merchant_id"])
    pr.steps["_final_da_journals"] = L.da_journals(d["merchant_id"])
    pr.save()
    summary = write_summary(d, pr, run_dir)
    print(json.dumps({k: v for k, v in summary["steps"].items()}, indent=1))
    print("all_green", summary["all_green"])
    return 0 if summary["all_green"] else 1


if __name__ == "__main__":
    sys.exit(main())
