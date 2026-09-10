#!/usr/bin/env python3
"""Milestone 4 T10 -- bank-statement ingestion + ART reconciliation proof (REAL PS code path).

Against the LIVE arena (compose project env2_compose by default). For one Direct
merchant it runs, in order:

  (a) Direct payout through kong with mozart success -> processed; UTR read from PS + FTS
  (b) matching debit statement row enqueued at mozart-sim -> REAL cron route -> REAL
      rbl_banking_account_statement worker -> one banking_account_statement row; BASD advanced
  (c) exact replay of the same CSV row -> NO new row (worker dedupe tuple recorded)
  (d) ART batch process Reconciled (REAL PS route) -> BAS linked, monolith-stub payout_update
      relay, payouts.transaction_id == bas id, NO ledger journal from PS
  (e) ART Unreconciled on a second unmatched row -> BAS external, PS da_ext_debit journal
      attempt observed (SNS publish outcome recorded as-is)
  (f) statement-first ordering: new payout held pending at the bank, statement + ART link
      delivered BEFORE the terminal FTS status, then bank resolves failed -> REAL
      VerifyPayoutFailedTransaction guard (transaction_id set => failed refused)
  (g) mozart-sim no_records and failure scenarios -> worker handles gracefully, no rows

Merchant selection: --descriptor <json> (a T09 descriptor with key/secret), else
--campaign <id> provisions a fresh Direct merchant via red_loop.provisioner_direct
(T09's module; restarts monolith-stub/bankingaccounts-stub/dcs-stub/splitz-stub/
kong-lite exactly as that module does), else --fixture-m2 (ADDITIVE BASD row for
ARENAM00000002 -- development only, flagged).

Evidence: RED_LOOP/runs/m4-bas-ingest-<ts>/ (descriptor WITH secret, git-ignored)
and reports/implementation/m4-bas-ingest.json (no secrets). Stdlib only.
"""
import argparse
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "RED_LOOP"))
from red_loop import provisioner as P  # noqa: E402
from red_loop import provisioner_direct as D  # noqa: E402
from red_loop import bas_fixtures as B  # noqa: E402

IMPL = REPO / "reports" / "implementation"
RUNS = REPO / "RED_LOOP" / "runs"
SUMMARY = IMPL / "m4-bas-ingest.json"


def _ts():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _save(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=str))
    return path


def _git_head():
    try:
        return subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:  # noqa: BLE001
        return None


def _fingerprint():
    p = REPO / "ENV2_COMPOSE" / ".runtime" / "arena-fingerprint.json"
    try:
        fp = json.loads(p.read_text())
        return {k: fp.get(k) for k in ("boot_id", "compose_project", "route_profile", "config_digest", "git_head")}
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# merchant
# --------------------------------------------------------------------------
def fixture_m2_descriptor():
    secret = P._pw("merchant_arena_m2_secret.txt")
    return {"campaign_id": "fixture-m2", "role": "fixture", "archetype": "direct", "channel": "rbl",
            "merchant_id": "ARENAM00000002", "key_id": "rzp_live_ARENAM00000002", "secret": secret,
            "balance_id": "ARENABAL000002", "account_number": "2323230000000002",
            "fund_account_id": "fa_ARENAFAX000002", "opening_balance": 10_000_000, "fixture": True}


def latest_descriptor():
    cands = sorted(RUNS.glob("m4-direct-provision-*/descriptor-*.json"))
    return json.loads(cands[-1].read_text()) if cands else None


def load_merchant(args, run_dir):
    if args.descriptor:
        d = json.loads(Path(args.descriptor).read_text())
        src = "descriptor:" + args.descriptor
    elif args.campaign:
        d = D.provision_direct_merchant(args.campaign)
        _save(run_dir / ("descriptor-%s.json" % d["merchant_id"]), d)
        sc = D.self_check_direct_merchant(d)
        _save(run_dir / ("selfcheck-%s.json" % d["merchant_id"]), sc)
        d["self_check"] = {"ready": sc.get("ready"), "passed": sc.get("passed"), "total": sc.get("total"),
                           "failed": [c["name"] for c in sc.get("checks", []) if not c["ok"]]}
        src = "provisioned:" + args.campaign
    elif args.fixture_m2:
        d = fixture_m2_descriptor()
        src = "fixture:ARENAM00000002 (ADDITIVE BASD row; development only)"
    else:
        d = latest_descriptor()
        if not d:
            raise SystemExit("no descriptor found; pass --descriptor, --campaign or --fixture-m2")
        src = "latest T09 descriptor"
    d["_source"] = src
    return d


# --------------------------------------------------------------------------
# payout helpers (kong, REAL route)
# --------------------------------------------------------------------------
def create_payout(d, amount, note):
    auth = P._auth(d["key_id"], d["secret"])
    body = {"fund_account_id": d["fund_account_id"], "account_number": d["account_number"], "amount": amount,
            "currency": "INR", "mode": "IMPS", "purpose": "payout", "queue_if_low_balance": True,
            "merchant_id": d["merchant_id"], "narration": "m4 bas ingest " + note, "notes": {"m4": note}}
    ik = "m4bas-" + uuid.uuid4().hex[:10]
    st, resp = P._kong_call("POST", "/v1/payouts", auth, body, {"X-Payout-Idempotency": ik})
    r = B._jload(resp) or {}
    pid = (r.get("id") or "").replace("pout_", "")
    return {"status": st, "payout_id": pid, "response_status": r.get("status"), "idempotency": ik,
            "body": resp[:400] if st != 200 else None, "request": {k: v for k, v in body.items()}}


MONOLITH_BRIDGE_ROUTE = "/v1/payouts/banking_account_statement/payout_update"
BRIDGE_NOTE = ("REAL PS posts {api.host}/v1 + /banking_account_statement/payout_update (pkg/api/bas_recon_payout_update.go:14) "
               "but monolith-stub registers only /payouts/banking_account_statement/payout_update -> 404 "
               "(BAS_UPDATE_PAYOUT_REQUEST_FAILURE invalid_status_code). The bridge re-issues PS's own 6-field body to the "
               "stub's existing relay route so the downstream REAL hop (monolith -> PS UpdatePayoutAfterBASRecon) is still "
               "exercised. Substitute gap, owner T11 (monolith-stub/server.py ROUTES).")


def bridge_payout_update(req_body):
    st, txt = B.arena_http("POST", B.MONOLITH + MONOLITH_BRIDGE_ROUTE, req_body, basic=B.monolith_basic())
    return {"route": MONOLITH_BRIDGE_ROUTE, "request": req_body, "status": st, "body": B._jload(txt, txt), "at": _now()}


def ps_payout_update_attempt(api_lines):
    """The REAL PS -> monolith request/response as logged by SendUpdatesToPayoutSource."""
    out = {"request_logged": None, "outcome": "not_observed", "error": None}
    for l in api_lines:
        d = B._jload(l[l.find("{"):]) or {}
        m = d.get("message")
        if m == "BAS_UPDATE_PAYOUT_REQUEST":
            out["request_logged"] = ((d.get("context") or {}).get("requestParams") or {}).get("requestParams") or (d.get("context") or {}).get("requestParams")
        elif m == "BAS_UPDATE_PAYOUT_REQUEST_SUCCESS":
            out["outcome"] = "success"
        elif m == "BAS_UPDATE_PAYOUT_REQUEST_FAILURE":
            out["outcome"] = "failure"
            out["error"] = (d.get("context") or {}).get("error")
    return out


# --------------------------------------------------------------------------
# steps
# --------------------------------------------------------------------------
class Proof:
    def __init__(self, d, run_dir, since):
        self.d, self.run_dir, self.since = d, run_dir, since
        self.steps = []
        self.mid, self.acct, self.bal = d["merchant_id"], d["account_number"], d["balance_id"]

    def step(self, key, title, fidelity, ok, request=None, observed=None, note=None):
        rec = {"step": key, "title": title, "fidelity": fidelity, "pass": bool(ok), "at": _now(),
               "request": request, "observed": observed}
        if note:
            rec["note"] = note
        self.steps.append(rec)
        print("[%s] %s -> %s" % (key, title, "PASS" if ok else "FAIL"))
        return ok

    def worker_msgs(self, since, *needles):
        lines = B.docker_logs("payouts-worker-rbl-banking-account-statement", since, needles)
        return B.log_messages(lines), lines[-12:]

    def api_lines(self, since, *needles):
        return B.docker_logs("payouts-api", since, needles)

    # (a) ------------------------------------------------------------------
    def step_a(self, amount=5000):
        t0 = _now()
        scen = D.set_mozart_scenario(self.mid, "success")
        cr = create_payout(self.d, amount, "step-a")
        pid = cr["payout_id"]
        row = D._wait_payout(pid, tries=30) if pid else {}
        fts = B.fts_transfer(pid) if pid else None
        ok = cr["status"] == 200 and row.get("status") == "processed" and bool(row.get("utr")) and fts and fts.get("utr") == row.get("utr")
        time.sleep(2)
        se = B.log_messages(self.api_lines(t0, pid, "X_ACCOUNT_STATEMENT_SOURCE_EVENT"))
        self.step("a", "Direct payout through kong -> processed; UTR from PS + FTS", "real",
                  ok, request={"mozart_scenario": scen, "create": cr["request"], "idempotency": cr["idempotency"]},
                  observed={"create_status": cr["status"], "payout_id": pid, "payout_row": row, "fts_transfer": fts,
                            "ledger_journals_for_merchant": B.ledger_journal_count(self.mid, pid), "started_at": t0,
                            "xas_source_event_producer_messages": se},
                  note="xas_source_event_producer_messages shows the REAL x_account_statement_source_event push (config gate + queue from T10); consumer is xas-sim (T11)")
        return pid, row

    # (b) ------------------------------------------------------------------
    def step_b(self, pid, prow):
        basd = B.ensure_basd_row(self.d)
        before = B.read_basd(self.acct)
        amount = int(prow["amount"])
        closing = int(before["statement_closing_balance"] or self.d.get("opening_balance") or 10_000_000)
        self.row_b = B.statement_row(prow["utr"], amount, closing - amount, tran_id="M4B" + uuid.uuid4().hex[:8].upper(), ptsn="1")
        enq = B.enqueue_statement(None, self.acct, [self.row_b])
        since = _now()
        trig = B.trigger_fetch()
        rows = B.wait_for_bas_rows(self.acct, utr=prow["utr"], timeout=120)
        after = B.read_basd(self.acct)
        msgs, tail = self.worker_msgs(since, self.mid)
        r = rows[0] if rows else {}
        expect = {"utr": prow["utr"], "amount": amount, "type": "debit", "merchant_id": self.mid, "account_number": self.acct,
                  "bank_transaction_id": self.row_b["tran_id"], "channel": "rbl", "category": "customer_initiated",
                  "bank_serial_number": "1", "balance": closing - amount, "entity_type": None}
        mism = {k: (r.get(k), v) for k, v in expect.items() if r.get(k) != v}
        advanced = (after or {}).get("metadata_json", {}).get("statement_fetched_till_date") != (before or {}).get("metadata_json", {}).get("statement_fetched_till_date") \
            or (after or {}).get("pagination_key") != (before or {}).get("pagination_key")
        ok = len(rows) == 1 and not mism and self.acct in json.dumps(trig.get("body")) and advanced \
            and int(after["statement_closing_balance"]) == closing - amount
        self.bas_b = r.get("id")
        self.step("b", "matching debit statement row -> REAL cron route -> REAL worker -> one BAS row; BASD advanced",
                  "real-worker/substitute-bank", ok,
                  request={"basd_ensure": {k: basd[k] for k in ("basd_id", "existed", "additive")}, "statement_row": self.row_b,
                           "enqueue": enq, "trigger": trig},
                  observed={"bas_rows": rows, "field_mismatches": mism, "basd_before": before, "basd_after": after,
                            "worker_messages": msgs, "worker_tail": tail})
        return rows

    # (c) ------------------------------------------------------------------
    def step_c(self, prow):
        count_before = len(B.read_bas_rows(self.acct))
        enq = B.enqueue_statement(None, self.acct, [self.row_b])       # SAME dict -> byte-identical CSV row
        since = _now()
        trig = B.trigger_fetch()
        hits = B.wait_for_worker_message("BANKING_ACCOUNT_STATEMENT_FETCH_DEDUPE_CHECK_DUPLICATES_FOUND", since, (self.mid,), timeout=120)
        time.sleep(3)
        rows_all = B.read_bas_rows(self.acct)
        rows_utr = [x for x in rows_all if x.get("utr") == prow["utr"]]
        msgs, tail = self.worker_msgs(since, self.mid)
        served = B.inspect_statement(self.acct)
        ok = bool(hits) and len(rows_all) == count_before and len(rows_utr) == 1
        self.step("c", "replay the same CSV row -> NO new BAS row (worker DB dedupe tuple)", "real-worker/substitute-bank", ok,
                  request={"enqueue_replay": enq, "trigger": trig},
                  observed={"rows_before": count_before, "rows_after": len(rows_all), "rows_for_utr": len(rows_utr),
                            "dedup_key_fields": list(B.DEDUP_KEY_FIELDS), "dedup_key": B.dedup_key(rows_utr[0]) if rows_utr else None,
                            "duplicates_found_log": hits[:2], "worker_messages": msgs, "mozart_served": (served.get("body") or {}).get("served")},
                  note="rbl_gateway.go DeDuplicateTransactionsDb: FetchByTxnIds(bank_transaction_id IN, account_number, created_at>=min txn date) then struct-equality on the 7-field tuple")

    # (d) ------------------------------------------------------------------
    def step_d(self, pid):
        since = _now()
        art = B.art_process_batch([B.art_entry(self.bas_b, "Reconciled", pid, "payout")])
        ps_attempt = {"outcome": "not_observed"}
        for _ in range(10):
            time.sleep(2)
            api = self.api_lines(since, self.bas_b)
            ps_attempt = ps_payout_update_attempt(api)
            if ps_attempt["outcome"] != "not_observed":
                break
        link = B.get_transaction_link(self.bas_b)
        relay = B.monolith_relay_log(self.bas_b)
        bridge = None
        if ps_attempt["outcome"] != "success" and not link["linked"]:
            body = ps_attempt["request_logged"] or {}
            body = body.get("Body") if isinstance(body, dict) and "Body" in body else body
            if not body:
                body = {"bas_id": self.bas_b, "entity_id": pid, "entity_type": "payout", "merchant_id": self.mid,
                        "transaction_date": int(link["bas"]["transaction_date"]), "converted_from_external": False}
            bridge = bridge_payout_update(body)
            time.sleep(3)
            link = B.get_transaction_link(self.bas_b)
            relay = B.monolith_relay_log(self.bas_b)
        api = self.api_lines(since, self.bas_b)
        api_msgs = B.log_messages(api)
        journals = B.ledger_journal_count(self.mid, pid)
        ledger_attempt = [l for l in api if "CREATE_LEDGER_JOURNAL" in l]
        linked_ok = art["status"] == 200 and link["linked"] and link["bas"]["entity_type"] == "payout" and link["bas"]["entity_id"] == pid \
            and bool(relay) and journals == "0" and not ledger_attempt
        real_hop_ok = ps_attempt["outcome"] == "success"
        self.step("d", "ART Reconciled (REAL route) -> BAS linked, payout_update relay at monolith-stub, payouts.transaction_id == bas id, no PS ledger journal",
                  "real", linked_ok and real_hop_ok, request={"art": art, "bridge": bridge},
                  observed={"link": link, "ps_to_monolith_payout_update": ps_attempt, "monolith_relay_log": relay,
                            "relay_source": "real PS request" if real_hop_ok else ("bridge" if bridge else "none"),
                            "payouts_api_messages": api_msgs, "ledger_journal_count_merchant": journals,
                            "ledger_publish_attempts_for_bas": ledger_attempt[:2],
                            "assertions": {"art_200_and_bas_linked": bool(art["status"] == 200 and link["bas"] and link["bas"]["entity_type"] == "payout" and link["bas"]["entity_id"] == pid),
                                           "ps_to_monolith_hop_real": real_hop_ok,
                                           "monolith_to_ps_hop_and_transaction_id": bool(link["linked"]),
                                           "relay_observed_at_monolith_stub": bool(relay),
                                           "no_ps_ledger_journal": journals == "0" and not ledger_attempt}},
                  note=None if real_hop_ok else BRIDGE_NOTE)

    # (e) ------------------------------------------------------------------
    def step_e(self):
        basd = B.read_basd(self.acct)
        closing = int(basd["statement_closing_balance"])
        row = B.statement_row("M4EXT" + uuid.uuid4().hex[:9].upper(), 777, closing - 777, tran_id="M4E" + uuid.uuid4().hex[:8].upper(), ptsn="2")
        B.make_basd_selectable(self.acct)
        enq = B.enqueue_statement(None, self.acct, [row])
        trig = B.trigger_fetch()
        rows = B.wait_for_bas_rows(self.acct, utr=row["particulars"].split("-")[0], timeout=120)
        bas_e = rows[0]["id"] if rows else None
        since = _now()
        art = B.art_process_batch([B.art_entry(bas_e, "Unreconciled")]) if bas_e else {"status": "skipped"}
        api = []
        for _ in range(20):   # SNS publish retries (3 attempts through the blackholed proxy, exponential backoff) take up to ~40 s
            time.sleep(3)
            api = self.api_lines(since, bas_e) if bas_e else []
            if any("CREATE_LEDGER_JOURNAL_ASYNC_FAILED" in l or "CREATE_LEDGER_JOURNAL_ASYNC_RESPONSE" in l for l in api):
                break
        link = B.get_transaction_link(bas_e) if bas_e else None
        api_msgs = B.log_messages(api)
        ledger_lines = [l for l in api if "CREATE_LEDGER_JOURNAL" in l or "SNS" in l.upper()]
        journal_req = next((l for l in api if "CREATE_LEDGER_JOURNAL_ASYNC_REQUEST" in l), None)
        payload = None
        if journal_req:
            dd = B._jload(journal_req[journal_req.find("{"):]) or {}
            payload = (dd.get("context") or {}).get("message")
            while isinstance(payload, dict) and set(payload) == {"message"}:
                payload = payload["message"]           # logger nests {"message": {"message": {...}}}
            if isinstance(payload, str):
                payload = B._jload(payload)
        ledger_worker = B.docker_logs("ledger-worker-journal-create", since, ("bas_" + bas_e,)) if bas_e else []
        journals = B.ledger_journal_count(self.mid, "bas_" + bas_e if bas_e else None)
        external = bool(link and link["bas"]["entity_type"] == "external")
        attempted = "CREATE_LEDGER_JOURNAL_ASYNC_REQUEST" in api_msgs
        outcome = ("published" if "CREATE_LEDGER_JOURNAL_ASYNC_RESPONSE" in api_msgs else
                   "publish_failed" if "CREATE_LEDGER_JOURNAL_ASYNC_FAILED" in api_msgs else "not_observed")
        self.step("e", "ART Unreconciled on an unmatched debit -> BAS external; PS da_ext_debit journal attempt observed", "real",
                  art.get("status") == 200 and external and attempted,
                  request={"statement_row": row, "enqueue": enq, "trigger": trig, "art": art},
                  observed={"bas": link and link["bas"], "payouts_api_messages": api_msgs, "journal_publish_outcome": outcome,
                            "journal_payload_from_log": payload, "ledger_related_log_lines": ledger_lines[:4],
                            "ledger_worker_journal_create_lines": ledger_worker[:4], "ledger_journal_count": journals},
                  note="PS publishes the da_ext_debit LedgerJournalCreateRequest to SNS topic api-ledger-journal-create-live "
                       "(pkg/sns getTopicArn with [sns].aws_account_id, no LocalStack endpoint) -- outcome recorded as observed; "
                       "DA accounts are not seeded so a delivered journal would fail account discovery in the real ledger worker")

    # (f) ------------------------------------------------------------------
    def step_f(self, amount=6100):
        hold = D.set_mozart_scenario(self.mid, "hold")
        cr = create_payout(self.d, amount, "step-f")
        pid = cr["payout_id"]
        row = D._wait_payout(pid, want="initiated", tries=20) if pid else {}
        t = D._wait_transfer(pid) if pid else None
        # statement + ART link BEFORE the terminal bank status
        basd = B.read_basd(self.acct)
        closing = int(basd["statement_closing_balance"])
        utr = "M4HOLD" + uuid.uuid4().hex[:8].upper()
        srow = B.statement_row(utr, amount, closing - amount, tran_id="M4F" + uuid.uuid4().hex[:8].upper(), ptsn="3")
        B.make_basd_selectable(self.acct)
        enq = B.enqueue_statement(None, self.acct, [srow])
        trig = B.trigger_fetch()
        rows = B.wait_for_bas_rows(self.acct, utr=utr, timeout=120)
        bas_f = rows[0]["id"] if rows else None
        since_art = _now()
        art = B.art_process_batch([B.art_entry(bas_f, "Reconciled", pid, "payout")]) if bas_f else {"status": "skipped"}
        time.sleep(4)
        link_before = B.get_transaction_link(bas_f) if bas_f else None
        ps_attempt = ps_payout_update_attempt(self.api_lines(since_art, bas_f)) if bas_f else None
        bridge = None
        if bas_f and link_before and not link_before["linked"]:
            body = {"bas_id": bas_f, "entity_id": pid, "entity_type": "payout", "merchant_id": self.mid,
                    "transaction_date": int(link_before["bas"]["transaction_date"]), "converted_from_external": False}
            bridge = bridge_payout_update(body)
            time.sleep(3)
            link_before = B.get_transaction_link(bas_f)
        prow_linked = B.get_payout_row(pid)
        # The live arena runs route_profile=monolith: FTS reports status to monolith-stub /update_fts_fund_transfer, which
        # relays to PS's legacy source-update route -- a path that does NOT call VerifyPayoutFailedTransaction (the guard
        # lives only in HandleTransferStatusWebhookForPayout, the FTS-direct route /v1/payouts/transfer_status_webhook,
        # fts_transfer_status_webhook.go:612). So: hold the relay for this payout, let the bank fail, then deliver the
        # FTS body to the REAL direct route ourselves (synthetic caller, real route + real guard), then release the relay
        # and record what the legacy path does.
        hold_relay = B.arena_http("POST", B.MONOLITH + "/_arena/relay-control", {"payout_id": pid, "action": "hold"}, basic=B.monolith_basic())
        since = _now()
        fail = D.set_mozart_scenario(str(t["attempt_id"]), "failure") if t and t.get("attempt_id") else None
        check = D.arena_http("POST", "http://fts-web:8080/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic()) if t else None
        fts_final = None
        for _ in range(30):
            fts_final = B.fts_transfer(pid)
            if fts_final and fts_final.get("status") in ("failed", "processed", "reversed"):
                break
            time.sleep(2)
        time.sleep(4)
        prow_held = B.get_payout_row(pid)
        webhook_body = {"fund_transfer_id": int(t["id"]), "status": "failed", "source_type": "payout", "source_id": pid,
                        "bank_status_code": "INVALID_ACCOUNT_NUMBER", "failure_reason": "synthetic failure", "channel": "RBL",
                        "mode": "IMPS", "source_account_id": int(self.d["fts_fund_account_id"]), "bank_account_type": "CURRENT",
                        "utr": "", "gateway_ref_no": "", "extra_info": {}}
        since_wh = _now()
        wh_st, wh_txt = B.arena_http("POST", B.PS_API + "/v1/payouts/transfer_status_webhook", webhook_body,
                                     basic="fts:" + P._pw("auth_fts_payouts.txt"))
        webhook = {"route": "/v1/payouts/transfer_status_webhook", "auth": "payouts [auth.fts]", "request": webhook_body,
                   "status": wh_st, "body": B._jload(wh_txt, wh_txt[:400])}
        time.sleep(3)
        prow_after = B.get_payout_row(pid)
        api = self.api_lines(since_wh, pid)
        api_msgs = B.log_messages(api)
        guard = [l for l in api if "ATTEMPT_TO_MOVE_PAYOUT_TO_TERMINAL_STATE_HAVING_TRANSACTION" in l or "FAILED_PAYOUT_HAS_DEBIT_STATEMENT" in l]
        release = B.arena_http("POST", B.MONOLITH + "/_arena/relay/release", {"payout_id": pid}, basic=B.monolith_basic())
        time.sleep(6)
        prow_released = B.get_payout_row(pid)
        relay = D._monolith_relay_log(pid)
        legacy_msgs = B.log_messages(self.api_lines(since_wh, pid))
        ok = bool(bas_f) and link_before and link_before["linked"] and prow_linked and prow_linked.get("transaction_id") == bas_f \
            and fts_final and str(fts_final.get("status")).lower() == "failed" and prow_held and prow_held.get("status") == "initiated" \
            and wh_st not in (200, 201) and prow_after and prow_after.get("status") == "initiated" and prow_after.get("transaction_id") == bas_f \
            and bool(guard)
        self.step("f", "statement-first: link BEFORE terminal status, then bank fails -> REAL VerifyPayoutFailedTransaction refuses the failed transition",
                  "real", ok,
                  request={"mozart_hold": hold, "create": cr["request"], "statement_row": srow, "enqueue": enq, "trigger": trig, "art": art,
                           "bridge": bridge, "relay_hold": {"status": hold_relay[0], "body": B._jload(hold_relay[1], hold_relay[1][:200])},
                           "mozart_failure_on_attempt": fail, "fts_check": check, "fts_direct_webhook": webhook,
                           "relay_release": {"status": release[0], "body": B._jload(release[1], release[1][:300])}},
                  observed={"payout_id": pid, "payout_pending": row, "fts_pending": t, "link_before_terminal": link_before,
                            "payout_while_relay_held_after_bank_failed": prow_held, "payout_after_direct_webhook": prow_after,
                            "payout_after_relay_release": prow_released, "legacy_relay_messages_after_release": legacy_msgs,
                            "guard_refused_on_fts_direct_route": bool(guard) and wh_st not in (200, 201),
                            "legacy_relay_route_bypasses_guard": bool(prow_released and prow_released.get("status") == "failed"),
                            "ps_to_monolith_payout_update": ps_attempt, "link_source": "real PS request" if (ps_attempt or {}).get("outcome") == "success" else ("bridge" if bridge else "none"),
                            "payout_after_link": prow_linked, "fts_final": fts_final, "payout_after_bank_failed": prow_after,
                            "guard_log_lines": guard[:3], "payouts_api_messages": api_msgs, "monolith_relay_log": relay},
                  note=("guard is the REAL VerifyPayoutFailedTransaction (fts_transfer_status_webhook.go:654-663) on the FTS-direct route "
                        "/v1/payouts/transfer_status_webhook, driven here by a synthetic caller with the FTS body because the arena's "
                        "monolith route profile reports status through monolith-stub's legacy relay, which never calls the guard; "
                        + ("" if (ps_attempt or {}).get("outcome") == "success" else "the link itself needed the bridge -- " + BRIDGE_NOTE)))
        D.set_mozart_scenario(self.mid, "success")
        return pid

    # (g) ------------------------------------------------------------------
    def step_g(self):
        res = {}
        count_before = len(B.read_bas_rows(self.acct))
        for name, opts, msg in (("no_records", {"no_records": True}, "BANKING_ACCOUNT_STATEMENT_FETCH_NO_RECORDS"),
                                ("failure", {"failure": True}, "BANKING_ACCOUNT_STATEMENT_FETCH_RETRIES_EXHAUSTED")):
            B.make_basd_selectable(self.acct)
            setopt = B.set_statement_options(self.acct, opts)
            since = _now()
            served_before = len(((B.inspect_statement(self.acct).get("body") or {}).get("requests") or []))
            trig = B.trigger_fetch()
            # NO_RECORDS is logged without fields (rbl_gateway.go FetchPage: Warn(trace...)) so it cannot be tied to a
            # merchant from the log line alone; correlate with mozart-sim's per-account request log instead.
            needles = (self.mid,) if name == "failure" else ()
            hits = B.wait_for_worker_message(msg, since, needles, timeout=120)
            time.sleep(2)
            processed = B.wait_for_worker_message("JOB_PROCESSED_SUCCESSFULLY", since, (), timeout=60)
            msgs, tail = self.worker_msgs(since, self.mid)
            reqs = ((B.inspect_statement(self.acct).get("body") or {}).get("requests") or [])[served_before:]
            outcomes = [r.get("outcome") for r in reqs]
            res[name] = {"options": setopt, "trigger": trig, "expected_message": msg, "hit": bool(hits) and name in outcomes, "hit_line": hits[:1],
                         "mozart_outcomes_for_account": outcomes, "worker_messages": msgs, "job_processed": bool(processed),
                         "rows_after": len(B.read_bas_rows(self.acct))}
        B.set_statement_options(self.acct, {})
        alive = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}} {{.State.Health.Status}}",
                                P.cname("payouts-worker-rbl-banking-account-statement")], capture_output=True, text=True).stdout.strip()
        ok = all(v["hit"] and v["rows_after"] == count_before for v in res.values()) and alive.startswith("running")
        self.step("g", "mozart-sim no_records and failure -> worker logs the outcome, no rows, worker stays healthy",
                  "real-worker/substitute-bank", ok, request={k: {"options": v["options"], "trigger": v["trigger"]} for k, v in res.items()},
                  observed={"scenarios": res, "rows_before": count_before, "worker_container": alive})


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--descriptor")
    ap.add_argument("--campaign")
    ap.add_argument("--fixture-m2", action="store_true")
    ap.add_argument("--run-dir")
    ap.add_argument("--steps", default="abcdefg", help="subset of steps to run (a-g); b needs a, d needs b/c, f needs b")
    args = ap.parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else RUNS / ("m4-bas-ingest-" + _ts())
    run_dir.mkdir(parents=True, exist_ok=True)
    since = _now()
    d = load_merchant(args, run_dir)
    _save(run_dir / "merchant-descriptor.json", d)
    pr = Proof(d, run_dir, since)
    pid = None
    prow = None
    if "a" in args.steps:
        pid, prow = pr.step_a()
    if "b" in args.steps and prow and prow.get("status") == "processed":
        pr.step_b(pid, prow)
    if "c" in args.steps and getattr(pr, "bas_b", None):
        pr.step_c(prow)
    if "d" in args.steps and getattr(pr, "bas_b", None):
        pr.step_d(pid)
    if "e" in args.steps:
        pr.step_e()
    if "f" in args.steps:
        pr.step_f()
    if "g" in args.steps:
        pr.step_g()
    public = {k: v for k, v in d.items() if k not in ("secret", "steps", "ids")}
    summary = {"schema_version": 1, "task": "T10", "started_at": since, "finished_at": _now(), "git_head": _git_head(),
               "arena": _fingerprint(), "merchant": public, "run_dir": str(run_dir.relative_to(REPO)),
               "fidelity_legend": {"real": "accepted service binary + real route", "real-worker/substitute-bank": "REAL payouts worker fed by mozart-sim substitute + bankingaccounts-stub credentials",
                                   "substitute": "twin substitute only"},
               "steps": pr.steps, "passed": sum(1 for s in pr.steps if s["pass"]), "total": len(pr.steps),
               "all_pass": all(s["pass"] for s in pr.steps)}
    _save(run_dir / "m4-bas-ingest.json", summary)
    _save(SUMMARY, summary)
    print(json.dumps({k: summary[k] for k in ("passed", "total", "all_pass", "run_dir")}))
    return 0 if summary["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
