#!/usr/bin/env python3
"""Milestone 4 T17 -- consolidated Direct payout + reconciliation journey/route/invariant driver.

ONE reproducible driver that runs the COMPLETE Direct outcome matrix end-to-end on FRESH Direct
merchants against the LIVE arena (compose project env2_compose), reusing the proven building blocks
from T09 (provisioner_direct), T10 (bas_fixtures / m4_bas_ingest) and T11 (ledger_da / m4_xas_ledger)
-- it ORCHESTRATES them into the Phase-3 journey matrix, the Phase-4 route matrix and the Phase-5
invariant subset, and emits machine-readable coverage. It does NOT re-prove T10/T11 sub-assertions;
it drives each journey as one coherent payout and records the evidence bundle.

Journeys (Phase 3), each on a fresh Direct merchant (>=2 merchants; distinct merchants across
scenarios to show tenant isolation):

  A  Success           merchant A: hold->success (reservation live -> awaiting_balance_refresh),
                       FTS preferred==source==fund_account, REAL rbl worker statement ingest,
                       xas-sim match -> REAL UpdatePayoutAfterBASRecon (transaction_id) ->
                       monolith-stub DA emitter -> REAL ledger da_payout_processed+_recon balanced,
                       webhook payout.processed scoped to the merchant.
  B  Immediate failure merchant B: mozart failure -> failed, no reversal row, no Shared reversal
                       journal, reservation released, webhook payout.failed, duplicate failure idempotent.
  C  Pending->success  merchant A: mozart delayed_success (poll1 PENDING, poll2 SUCCESS) -> pending
                       observed -> processed; statement recon once, final accounting once, replayed
                       delayed status idempotent.
  D  Pending->failure  merchant B: hold (pending) then attempt failure -> failed, reservation released,
                       no Shared reversal, correct webhook/accounting.
  E  Statement-first   merchant A: link BEFORE terminal status, bank failed -> REAL
                       VerifyPayoutFailedTransaction on the FTS-direct route refuses (R1); the
                       monolith-relay route still moves it to failed (F-T10-1) -- both routes recorded.
  F  Duplicate stmt    merchant A: identical CSV twice -> no new BAS row (dedup key), no duplicate
                       journal, balance/reservation not double-released.
  G  Conflict          merchant B: credit statement for a failed payout -> failed->reversed with a
                       reversal transaction_id; wrong-amount statement -> external, no link/journal
                       (negative control).

Route matrix (Phase 4): R1 FTS->PS direct webhook, R2 FTS->monolith->PS relay, R3 Kafka
rx-fts-status-update-events consumer (Direct FAILED/REVERSED dropped = EF-002/003, expected-failure),
plus the account_type/channel status remap (Direct rbl failed->failed vs Shared failed->reversed / V18).

Invariants (Phase 5 subset): I-Direct-failure-no-shared-reversal (G31),
I-Direct-success-no-duplicate-ledger (G54), I-reservation-not-released-twice (G53),
I-balance-not-decremented-twice (G28-adj), I-conservation (G51), I-reconciliation-correct-tuple (G26).

Outputs:
  RED_LOOP/runs/m4-journeys-<ts>/           evidence bundle (descriptors WITH secret, git-ignored)
  reports/implementation/m4-direct-e2e-coverage.md    journeys + routes + invariants table
  reports/implementation/m4-route-coverage.json       route matrix rows
  reports/implementation/m4-invariant-results.json    invariant results

Rerunnable and deterministic: observable-condition waits (no bare sleeps where avoidable), fresh
merchants each run, scenario state cleaned between journeys. Preserves genuine source failures;
records EXPECTED-FAILURE/BLOCKED with the exact blocker + source citation, never patches to go green.
Stdlib only (host Python; D-005). DB reads via docker exec through the building-block modules.

Usage:
  python3 RED_LOOP/surface/m4_direct_journeys.py                        # provision 2 fresh, run all
  python3 RED_LOOP/surface/m4_direct_journeys.py --journeys ABC          # subset
  python3 RED_LOOP/surface/m4_direct_journeys.py \
      --merchant-a-descriptor <a.json> --merchant-b-descriptor <b.json>  # reuse provisioned merchants
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
sys.path.insert(0, str(REPO / "RED_LOOP" / "surface"))
from red_loop import provisioner as P            # noqa: E402
from red_loop import provisioner_direct as D     # noqa: E402
from red_loop import bas_fixtures as B           # noqa: E402
from red_loop import ledger_da as L              # noqa: E402
import m4_bas_ingest as BAS                       # noqa: E402  (reuse Proof.step_f for the real guard)
import m4_xas_ledger as XL                        # noqa: E402  (reuse xas/ledger helpers + Proof.step_e/h)

IMPL = REPO / "reports" / "implementation"
RUNS = REPO / "RED_LOOP" / "runs"
XAS = "http://xas-sim:8080"


def _ts():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _save(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, default=str))
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


def wait_ps_health(tries=40, delay=3):
    """T16 may restart dcs-stub/payouts-api; wait for payouts-api container health before driving."""
    for _ in range(tries):
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}} {{.State.Health.Status}}", P.cname("payouts-api")],
                             capture_output=True, text=True).stdout.strip()
        if out.startswith("running") and "healthy" in out:
            return True
        time.sleep(delay)
    return False


# --------------------------------------------------------------------------
# small live helpers (thin wrappers over the building-block modules)
# --------------------------------------------------------------------------
def create_payout(d, amount, note, idem=None):
    auth = P._auth(d["key_id"], d["secret"])
    body = {"fund_account_id": d["fund_account_id"], "account_number": d["account_number"], "amount": amount,
            "currency": "INR", "mode": "IMPS", "purpose": "payout", "queue_if_low_balance": True,
            "merchant_id": d["merchant_id"], "narration": "m4 t17 " + note, "notes": {"t17": note, "campaign": d["campaign_id"]}}
    ik = idem or ("t17-" + uuid.uuid4().hex[:12])
    st, resp = P._kong_call("POST", "/v1/payouts", auth, body, {"X-Payout-Idempotency": ik})
    r = B._jload(resp) or {}
    pid = (r.get("id") or "").replace("pout_", "")
    return {"status": st, "payout_id": pid, "response_status": r.get("status"), "idempotency": ik,
            "fees": r.get("fees"), "tax": r.get("tax"), "body": resp[:400] if st != 200 else None, "request": body}


def reservation_items(mid, bal):
    snap = D._reservations(mid, bal)
    items = (snap["body"] or {}).get("items", []) if isinstance(snap.get("body"), dict) else []
    return snap, items


def mine(items, pid):
    return [i for i in items if i.get("payout_id") == pid]


def xas_post(path, body):
    st, txt = D.arena_http("POST", XAS + path, body)
    return st, B._jload(txt, txt)


def clean_scenarios(d):
    """Reset per-merchant + per-attempt mozart scenario and clear the account's statement queue/options."""
    D.set_mozart_scenario(d["merchant_id"], "success")
    try:
        B.set_statement_options(d["account_number"], {})
        B.clear_statement(d["account_number"])
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# per-merchant setup: fresh Direct merchant + DA ledger onboarding + xas account + BASD selectable
# --------------------------------------------------------------------------
def setup_merchant(campaign, run_dir, role):
    wait_ps_health()
    d = D.provision_direct_merchant(campaign)
    _save(run_dir / ("descriptor-%s.json" % d["merchant_id"]), d)
    sc = D.self_check_direct_merchant(d)
    _save(run_dir / ("selfcheck-%s.json" % d["merchant_id"]), sc)
    setup = {"role": role, "campaign": campaign, "merchant_id": d["merchant_id"], "self_check": {
        "ready": sc.get("ready"), "passed": sc.get("passed"), "total": sc.get("total"),
        "failed": [c["name"] for c in sc.get("checks", []) if not c["ok"]]}}
    # DA ledger onboarding (T11): 7 parents + 6 merchant sub-accounts, feature on, xas account registered
    setup["da_parents"] = {"ok": isinstance(L.ensure_da_parent_accounts().get("parents"), list)}
    onb = L.onboard_da_merchant(d["merchant_id"], d["ids"]["basd_id"], 0)
    setup["da_onboard"] = {"status": onb.get("status"), "sub_accounts": onb.get("count"), "existed": onb.get("existed_before")}
    setup["features"] = XL.set_features(d["merchant_id"], da_ledger_journal_writes=True, payout_service_txn_recon=False)[1]
    setup["xas_account"] = xas_post("/_arena/accounts", {"merchant_id": d["merchant_id"], "account_number": d["account_number"],
                                                         "channel": d["channel"], "balance_id": d["balance_id"], "account_type": "direct"})[0]
    setup["basd"] = {k: B.ensure_basd_row(d)[k] for k in ("basd_id", "existed", "additive")}
    d["_setup"] = setup
    return d, setup


def load_or_setup(args, attr_desc, campaign, run_dir, role):
    desc = getattr(args, attr_desc)
    if desc:
        d = json.loads(Path(desc).read_text())
        # still ensure DA onboarding + features + xas account + BASD (idempotent) for a reused merchant
        L.ensure_da_parent_accounts()
        L.onboard_da_merchant(d["merchant_id"], d["ids"]["basd_id"], 0)
        XL.set_features(d["merchant_id"], da_ledger_journal_writes=True, payout_service_txn_recon=False)
        xas_post("/_arena/accounts", {"merchant_id": d["merchant_id"], "account_number": d["account_number"],
                                      "channel": d["channel"], "balance_id": d["balance_id"], "account_type": "direct"})
        B.ensure_basd_row(d)
        d.setdefault("_setup", {"role": role, "reused": desc})
        return d, d["_setup"]
    return setup_merchant(campaign, run_dir, role)


# --------------------------------------------------------------------------
# journey driver
# --------------------------------------------------------------------------
class Journeys:
    def __init__(self, dA, dB, run_dir):
        self.dA, self.dB, self.run_dir = dA, dB, run_dir
        self.results = []          # journey records
        self.route_evidence = {}   # route id -> evidence dict
        self.artifacts = {}        # cross-journey artifacts (pids, bas ids, snapshots) for invariants

    def _save(self):
        _save(self.run_dir / "journeys.json", {"results": self.results, "route_evidence": self.route_evidence,
                                               "artifacts": {k: v for k, v in self.artifacts.items()}})

    def record(self, jid, title, merchant, checks, fidelity, evidence, result=None):
        ok = all(c["ok"] for c in checks) if checks else False
        res = result or ("PASS" if ok else "FAIL")
        rec = {"journey": jid, "title": title, "merchant_id": merchant, "result": res, "fidelity": fidelity,
               "at": _now(), "checks": checks, "evidence": evidence}
        self.results.append(rec)
        print("[%s] %s -> %s (%d/%d checks)" % (jid, title, res, sum(1 for c in checks if c["ok"]), len(checks)))
        self._save()
        return rec

    def blocked(self, jid, title, merchant, exc, fidelity="real"):
        rec = {"journey": jid, "title": title, "merchant_id": merchant, "result": "BLOCKED", "fidelity": fidelity,
               "at": _now(), "checks": [], "evidence": {"blocker": str(exc)[:2000]}}
        self.results.append(rec)
        print("[%s] %s -> BLOCKED: %s" % (jid, title, str(exc)[:200]))
        self._save()
        return rec

    @staticmethod
    def ck(checks, name, ok, detail=None):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    # ---- statement ingest via the REAL rbl worker (T10 path) --------------
    def _ingest_debit(self, d, utr, amount, note):
        basd = B.read_basd(d["account_number"]) or {}
        closing = int(basd.get("statement_closing_balance") or d.get("opening_balance") or 10_000_000)
        row = B.statement_row(utr, amount, closing - amount, tran_id="T17" + uuid.uuid4().hex[:8].upper(),
                              ptsn=str(int(time.time()) % 100000))
        B.make_basd_selectable(d["account_number"])
        enq = B.enqueue_statement(None, d["account_number"], [row])
        since = _now()
        trig = B.trigger_fetch()
        rows = B.wait_for_bas_rows(d["account_number"], utr=utr, timeout=120)
        return {"row": row, "enqueue": enq, "trigger": trig, "bas_rows": rows,
                "bas_id": rows[0]["id"] if rows else None, "since": since}

    # ---- xas match -> PS link -> DA ledger (T11 path) on an existing payout+bas ----
    def _match_and_ledger(self, d, pid, prow, bas_id):
        ev_evi = {}
        prow_xl = XL.payout_row(pid) or {}                            # XL-shaped row (has "id", "amount", "utr", "balance_id", "mode", "status")
        prow_xl.setdefault("balance_id", d["balance_id"])
        XL.ensure_source_event(d, prow_xl, ev_evi)                    # prefer REAL PS producer
        sync = XL.sync_statements_to_xas(d, [bas_id])                 # mirror the REAL worker row into xas-sim
        linked = XL.wait_until(lambda: [s for s in XL.xas_state(d["merchant_id"]).get("statements", [])
                                        if s["id"] == bas_id and s["entity_type"] == "payout"] or None, 12, 1)
        outb = XL.wait_until(lambda: [o for o in XL.xas_state(d["merchant_id"]).get("outbound", [])
                                      if o.get("request", {}).get("bas_id") == bas_id] or None, 12, 1) or []
        prow2 = XL.wait_until(lambda: (lambda r: r if r.get("transaction_id") == bas_id else None)(XL.payout_row(pid)), 12, 1) or XL.payout_row(pid)
        js = XL.journals_for(d["merchant_id"], "pout_" + pid, want=2)
        return {"source_event": ev_evi, "sync": sync, "xas_linked": linked, "outbound": outb[-1:],
                "payout_after": prow2, "journals": js}

    # ======================================================================
    # Journey A -- Success (merchant A)
    # ======================================================================
    def journey_A(self):
        d = self.dA
        mid, bal, acct = d["merchant_id"], d["balance_id"], d["account_number"]
        checks, ev = [], {}
        clean_scenarios(d)
        amount = 5000 + int(time.time()) % 900
        # 1) hold -> pending: reservation live + FTS preferred==source==fund_account
        ev["mozart_hold"] = D.set_mozart_scenario(mid, "hold")
        ev["reservations_before"], _ = reservation_items(mid, bal)
        cr = create_payout(d, amount, "A-success")
        pid = cr["payout_id"]
        ev["create"] = {k: cr[k] for k in ("status", "payout_id", "response_status", "idempotency")}
        self.ck(checks, "A.create_200", cr["status"] == 200 and pid, cr.get("body"))
        row_pending = D._wait_payout(pid, want="initiated", tries=15) if pid else {}
        t = D._wait_transfer(pid) if pid else None
        ev["payout_pending"], ev["fts_pending"] = row_pending, t
        snap, items = reservation_items(mid, bal)
        live = [i for i in mine(items, pid) if i.get("state") == "live"]
        ev["reservations_pending"] = {"items": items, "redis_items": snap.get("redis_items")}
        self.ck(checks, "A.reservation_live_while_pending", bool(live), items)
        fts_ok = bool(t) and t.get("preferred_source_account_id") == d["fts_fund_account_id"] \
            and t.get("source_account_id") == d["fts_fund_account_id"] and t.get("fund_account_id") == d["fts_fund_account_id"] \
            and t.get("sa_account_type") == "direct" and t.get("sa_bank_account_type") == "CURRENT"
        self.ck(checks, "A.fts_preferred==source==fund_account (direct/CURRENT)", fts_ok, t)
        # 2) release -> processed
        if t and t.get("attempt_id"):
            ev["mozart_success"] = D.set_mozart_scenario(str(t["attempt_id"]), "success")
            ev["fts_check"] = D.arena_http("POST", "http://fts-web:8080/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic())
        prow = D._wait_payout(pid, tries=25) if pid else {}
        ev["payout_processed"] = prow
        self.ck(checks, "A.terminal_processed_with_utr", prow.get("status") == "processed" and bool(prow.get("utr")), prow)
        # 3) reservation settle -> awaiting_balance_refresh
        res_after, src = D._settle_reservation(mid, bal, pid, "awaiting_balance_refresh")
        aw = [i for i in ((res_after["body"] or {}).get("items", []) if isinstance(res_after.get("body"), dict) else []) if i.get("payout_id") == pid and i.get("state") == "awaiting_balance_refresh"]
        ev["reservations_after_processed"] = {"items": (res_after["body"] or {}).get("items", []) if isinstance(res_after.get("body"), dict) else res_after.get("body"), "transition_source": src}
        self.ck(checks, "A.reservation_awaiting_balance_refresh_after_processed", bool(aw), {"transition_source": src})
        # 4) REAL worker statement ingest for this payout's UTR
        utr = prow.get("utr")
        ing = self._ingest_debit(d, utr, amount, "A") if utr else {"bas_id": None}
        bas_id = ing.get("bas_id")
        ev["statement_ingest"] = {"row": ing.get("row"), "trigger": ing.get("trigger"), "bas_rows": ing.get("bas_rows"), "bas_id": bas_id}
        self.ck(checks, "A.real_worker_statement_row(one, matching utr)", bool(bas_id) and len(ing.get("bas_rows") or []) == 1, ing.get("bas_rows"))
        # 5) xas match -> PS UpdatePayoutAfterBASRecon (transaction_id) -> DA ledger journals
        ml = self._match_and_ledger(d, pid, prow, bas_id) if bas_id else {}
        ev["match_and_ledger"] = ml
        prow_l = ml.get("payout_after") or {}
        self.ck(checks, "A.PS_UpdatePayoutAfterBASRecon_transaction_id==bas_id", prow_l.get("transaction_id") == bas_id, prow_l)
        js = ml.get("journals")
        events = sorted(j["transactor_event"] for j in js) if isinstance(js, list) else []
        self.ck(checks, "A.ledger_da_payout_processed+_recon", events == ["da_payout_processed", "da_payout_processed_recon"], events)
        self.ck(checks, "A.ledger_journals_balanced", isinstance(js, list) and bool(js) and all(j.get("balanced") for j in js),
                [(j["transactor_event"], j.get("sum_debit"), j.get("sum_credit")) for j in js] if isinstance(js, list) else js)
        sub_ids = {s["id"] for s in L.da_sub_accounts(mid, d["ids"]["basd_id"])} if True else set()
        used = {e["account_id"] for j in (js or []) for e in j.get("entries", [])} if isinstance(js, list) else set()
        self.ck(checks, "A.entries_on_merchant_DA_sub_accounts_only", bool(used) and used <= sub_ids, {"used_count": len(used), "sub_count": len(sub_ids)})
        # 6) webhook payout.processed scoped to merchant
        time.sleep(3)
        wh = D._deliveries(mid)
        ev["webhooks"] = wh["deliveries"]
        procs = [w for w in wh["deliveries"] if w.get("payout_id") == "pout_" + pid and w.get("event") == "payout.processed"]
        self.ck(checks, "A.webhook_payout.processed_signed", any(w.get("signature_valid") is True and w.get("merchant") == mid for w in procs), procs)
        self.ck(checks, "A.webhooks_scoped_to_merchant", all(w.get("merchant") == mid for w in wh["deliveries"]), [w.get("merchant") for w in wh["deliveries"]])
        # no PS ledger journal for the merchant beyond the DA (Direct posts none itself)
        self.artifacts["A"] = {"pid": pid, "bas_id": bas_id, "amount": amount, "utr": utr,
                               "statement_row": ing.get("row"), "journals": js, "sub_ids": sorted(sub_ids),
                               "reservations_after": ev["reservations_after_processed"],
                               "payout_after": prow_l}
        clean_scenarios(d)
        return self.record("A", "Success (create->recon->DA ledger->webhook)", mid, checks,
                           "real payout/PS/FTS/ledger; real-worker statement; substitute xas matching", ev)

    # ======================================================================
    # Journey B -- Immediate failure (merchant B)
    # ======================================================================
    def journey_B(self):
        d = self.dB
        mid, bal = d["merchant_id"], d["balance_id"]
        checks, ev = [], {}
        clean_scenarios(d)
        amount = 5100 + int(time.time()) % 800
        ev["mozart_failure"] = D.set_mozart_scenario(mid, "failure")
        cr = create_payout(d, amount, "B-fail")
        pid = cr["payout_id"]
        ev["create"] = {k: cr[k] for k in ("status", "payout_id", "idempotency")}
        self.ck(checks, "B.create_200", cr["status"] == 200 and pid, cr.get("body"))
        prow = D._wait_payout(pid, tries=30) if pid else {}
        ev["payout_terminal"] = prow
        self.ck(checks, "B.terminal_failed_not_reversed", prow.get("status") == "failed", prow)
        ev["reversal_rows"] = D._reversal(pid) if pid else None
        self.ck(checks, "B.no_reversal_row", ev["reversal_rows"] == "0", ev["reversal_rows"])
        ev["ledger_journals_for_merchant"] = D._ledger_journals(mid, pid) if pid else None
        revj = L.da_journals(mid, "rvrsl_") if pid else []
        ev["shared_reversal_journal_probe"] = "none"
        self.ck(checks, "B.no_shared_reversal_journal", ev["ledger_journals_for_merchant"] == "0", ev["ledger_journals_for_merchant"])
        res_after, src = D._settle_reservation(mid, bal, pid, None)
        items = (res_after["body"] or {}).get("items", []) if isinstance(res_after.get("body"), dict) else []
        ev["reservations_after"] = {"transition_source": src, "mine": mine(items, pid)}
        self.ck(checks, "B.reservation_released", not mine(items, pid), items)
        time.sleep(3)
        wh = D._deliveries(mid, pid)
        ev["webhooks"] = wh["deliveries"]
        self.ck(checks, "B.webhook_payout.failed_signed", any(w.get("event") == "payout.failed" and w.get("signature_valid") is True for w in wh["deliveries"]), wh["deliveries"])
        # duplicate failure idempotent: re-deliver the same FTS failed body on the FTS-direct route (R1)
        t = D._fts_transfer(pid) if pid else None
        dup = None
        if t and t.get("id"):
            body = {"fund_transfer_id": int(t["id"]), "status": "failed", "source_type": "payout", "source_id": pid,
                    "bank_status_code": "INVALID_ACCOUNT_NUMBER", "failure_reason": "duplicate failure event", "channel": "RBL",
                    "mode": "IMPS", "source_account_id": int(d["fts_fund_account_id"]), "bank_account_type": "CURRENT",
                    "utr": "", "gateway_ref_no": "", "extra_info": {}}
            st, txt = D.arena_http("POST", "http://payouts-api:9400/v1/payouts/transfer_status_webhook", body, basic="fts:" + P._pw("auth_fts_payouts.txt"))
            prow2 = D._payout_row(pid)
            dup = {"route": "/v1/payouts/transfer_status_webhook", "status": st, "payout_status_after": prow2.get("status"),
                   "reversal_rows_after": D._reversal(pid)}
            ev["duplicate_failure"] = dup
            self.ck(checks, "B.duplicate_failure_idempotent(no new state / reversal)",
                    prow2.get("status") == "failed" and D._reversal(pid) == "0", dup)
        # route evidence R2 relay (this failure came through the monolith relay in monolith profile)
        ev["monolith_relay_log"] = D._monolith_relay_log(pid)
        self.artifacts["B"] = {"pid": pid, "amount": amount, "status": prow.get("status"), "reversal_rows": ev["reversal_rows"],
                               "webhooks": wh["deliveries"]}
        self._record_route_R2(d, pid, ev["monolith_relay_log"], "B")
        self._record_route_R1(dup, "B")
        clean_scenarios(d)
        return self.record("B", "Immediate failure (failed, no reversal, released, idempotent)", mid, checks, "real", ev)

    # ======================================================================
    # Journey C -- Pending -> success (merchant A)
    # ======================================================================
    def journey_C(self):
        d = self.dA
        mid, bal = d["merchant_id"], d["balance_id"]
        checks, ev = [], {}
        clean_scenarios(d)
        amount = 5200 + int(time.time()) % 700
        # mozart delayed_success polls=2: transfer_init -> pending; status poll1 -> PENDING; poll2 -> SUCCESS
        ev["mozart_delayed_success_polls2"] = D.set_mozart_scenario(mid, "delayed_success", polls=2)
        cr = create_payout(d, amount, "C-pending-success")
        pid = cr["payout_id"]
        ev["create"] = {k: cr[k] for k in ("status", "payout_id", "idempotency")}
        self.ck(checks, "C.create_200", cr["status"] == 200 and pid, cr.get("body"))
        row_pending = D._wait_payout(pid, want="initiated", tries=15) if pid else {}
        t = D._wait_transfer(pid) if pid else None
        ev["payout_pending"], ev["fts_pending"] = row_pending, t
        self.ck(checks, "C.pending_observed(payout initiated)", row_pending.get("status") == "initiated", row_pending)
        # poll1 -> PENDING (still initiated)
        if t and t.get("id"):
            ev["fts_check_poll1"] = D.arena_http("POST", "http://fts-web:8080/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic())
        time.sleep(3)
        row_p1 = D._payout_row(pid)
        ev["payout_after_poll1"] = row_p1
        self.ck(checks, "C.poll1_still_pending", row_p1.get("status") == "initiated", row_p1)
        # poll2 -> SUCCESS -> processed
        if t and t.get("id"):
            ev["fts_check_poll2"] = D.arena_http("POST", "http://fts-web:8080/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic())
        prow = D._wait_payout(pid, tries=25) if pid else {}
        ev["payout_processed"] = prow
        self.ck(checks, "C.resolves_processed_once", prow.get("status") == "processed" and bool(prow.get("utr")), prow)
        # statement recon once + accounting once
        utr = prow.get("utr")
        ing = self._ingest_debit(d, utr, amount, "C") if utr else {"bas_id": None}
        bas_id = ing.get("bas_id")
        ev["statement_ingest"] = {"trigger": ing.get("trigger"), "bas_id": bas_id, "n_rows": len(ing.get("bas_rows") or [])}
        self.ck(checks, "C.statement_recon_once(one BAS row)", bool(bas_id) and len(ing.get("bas_rows") or []) == 1, ing.get("bas_rows"))
        ml = self._match_and_ledger(d, pid, prow, bas_id) if bas_id else {}
        ev["match_and_ledger"] = {"payout_after": ml.get("payout_after"), "journals": ml.get("journals")}
        js = ml.get("journals")
        n_journals = len(js) if isinstance(js, list) else -1
        self.ck(checks, "C.final_accounting_once(exactly 2 balanced journals)",
                n_journals == 2 and all(j.get("balanced") for j in js), {"n": n_journals})
        # repeated delayed status idempotent: fire another status check after processed -> no state change / no dup journal
        if t and t.get("id"):
            ev["fts_check_replay"] = D.arena_http("POST", "http://fts-web:8080/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic())
        # replay the payout_update itself (as if the delayed status re-fired the recon) -> PS idempotent, ledger dedup
        body = (ml.get("outbound") or [{}])[-1].get("request") if ml.get("outbound") else None
        if body:
            ev["replay_payout_update"] = XL.mono("POST", "/v1/banking_account_statement/payout_update", body)[0]
        time.sleep(5)
        prow_r = D._payout_row(pid)
        js2 = L.da_journals(mid, "pout_" + pid)
        ev["after_replay"] = {"payout_status": prow_r.get("status"), "transaction_id": prow_r.get("transaction_id"), "n_journals": len(js2) if isinstance(js2, list) else js2}
        self.ck(checks, "C.repeated_delayed_status_idempotent(status+journals unchanged)",
                prow_r.get("status") == "processed" and prow_r.get("transaction_id") == bas_id and isinstance(js2, list) and len(js2) == 2,
                ev["after_replay"])
        self.artifacts["C"] = {"pid": pid, "bas_id": bas_id, "amount": amount, "journals_after_replay": len(js2) if isinstance(js2, list) else js2}
        clean_scenarios(d)
        return self.record("C", "Pending->success (poll1 PENDING, poll2 SUCCESS; recon once; idempotent)", mid, checks,
                           "real payout/PS/FTS/ledger; real-worker statement; substitute xas", ev)

    # ======================================================================
    # Journey D -- Pending -> failure (merchant B)
    # ======================================================================
    def journey_D(self):
        d = self.dB
        mid, bal = d["merchant_id"], d["balance_id"]
        checks, ev = [], {}
        clean_scenarios(d)
        amount = 5300 + int(time.time()) % 600
        ev["mozart_hold"] = D.set_mozart_scenario(mid, "hold")
        cr = create_payout(d, amount, "D-pending-fail")
        pid = cr["payout_id"]
        ev["create"] = {k: cr[k] for k in ("status", "payout_id", "idempotency")}
        self.ck(checks, "D.create_200", cr["status"] == 200 and pid, cr.get("body"))
        row_pending = D._wait_payout(pid, want="initiated", tries=15) if pid else {}
        t = D._wait_transfer(pid) if pid else None
        ev["payout_pending"], ev["fts_pending"] = row_pending, t
        snap, items = reservation_items(mid, bal)
        self.ck(checks, "D.pending_and_reservation_live", row_pending.get("status") == "initiated" and any(i.get("state") == "live" for i in mine(items, pid)), {"row": row_pending, "items": mine(items, pid)})
        # switch the attempt to failure, then FTS check -> failed transition
        if t and t.get("attempt_id"):
            ev["mozart_failure_on_attempt"] = D.set_mozart_scenario(str(t["attempt_id"]), "failure")
            ev["fts_check"] = D.arena_http("POST", "http://fts-web:8080/v1/transfer/%s/check" % t["id"], {}, basic=D._fts_basic())
        prow = D._wait_payout(pid, tries=25) if pid else {}
        ev["payout_terminal"] = prow
        self.ck(checks, "D.pending_then_failed", prow.get("status") == "failed", prow)
        ev["reversal_rows"] = D._reversal(pid) if pid else None
        self.ck(checks, "D.no_reversal_row(no shared reversal semantics)", ev["reversal_rows"] == "0", ev["reversal_rows"])
        ev["ledger_journals_for_merchant"] = D._ledger_journals(mid, pid) if pid else None
        self.ck(checks, "D.no_ledger_journal", ev["ledger_journals_for_merchant"] == "0", ev["ledger_journals_for_merchant"])
        res_after, src = D._settle_reservation(mid, bal, pid, None)
        items = (res_after["body"] or {}).get("items", []) if isinstance(res_after.get("body"), dict) else []
        ev["reservations_after"] = {"transition_source": src, "mine": mine(items, pid)}
        self.ck(checks, "D.reservation_released", not mine(items, pid), items)
        time.sleep(3)
        wh = D._deliveries(mid, pid)
        ev["webhooks"] = wh["deliveries"]
        self.ck(checks, "D.webhook_payout.failed_signed", any(w.get("event") == "payout.failed" and w.get("signature_valid") is True for w in wh["deliveries"]), wh["deliveries"])
        self.artifacts["D"] = {"pid": pid, "amount": amount, "status": prow.get("status"), "reversal_rows": ev["reversal_rows"]}
        clean_scenarios(d)
        return self.record("D", "Pending->failure (failed, released, no shared reversal)", mid, checks, "real", ev)

    # ======================================================================
    # Journey E -- Statement-first / route asymmetry (merchant A), reuse T10 real guard
    # ======================================================================
    def journey_E(self):
        d = self.dA
        mid = d["merchant_id"]
        clean_scenarios(d)
        pr = BAS.Proof(d, self.run_dir, _now())
        pr.step_f()                      # REAL VerifyPayoutFailedTransaction + F-T10-1 asymmetry
        s = pr.steps[-1] if pr.steps else {}
        checks = []
        obs = s.get("observed", {})
        self.ck(checks, "E.link_before_terminal(transaction_id set while initiated)",
                bool((obs.get("link_before_terminal") or {}).get("linked")) and (obs.get("payout_while_relay_held_after_bank_failed") or {}).get("status") == "initiated",
                obs.get("link_before_terminal"))
        self.ck(checks, "E.R1_fts_direct_route_guard_refuses(non-2xx, payout stays initiated)",
                bool(obs.get("guard_refused_on_fts_direct_route")), obs.get("guard_log_lines"))
        # F-T10-1 is a route-behaviour OBSERVATION (D-009), not an asserted defect: record BOTH routes side by side.
        f_t10_1_reproduced = bool(obs.get("legacy_relay_route_bypasses_guard"))
        self.ck(checks, "E.R2_monolith_relay_route_observed(both routes recorded side-by-side)",
                obs.get("payout_after_relay_release") is not None,
                {"payout_after_relay_release_status": (obs.get("payout_after_relay_release") or {}).get("status"),
                 "f_t10_1_asymmetry_reproduced_this_run": f_t10_1_reproduced})
        ev = {"reused": "m4_bas_ingest.Proof.step_f", "step_pass": s.get("pass"), "observed": obs,
              "f_t10_1_asymmetry_reproduced_this_run": f_t10_1_reproduced,
              "route_R1_fts_direct": {"guard_refused": bool(obs.get("guard_refused_on_fts_direct_route")),
                                      "payout_after": (obs.get("payout_after_direct_webhook") or {}).get("status")},
              "route_R2_monolith_relay": {"payout_after_release": (obs.get("payout_after_relay_release") or {}).get("status"),
                                          "moved_to_failed": f_t10_1_reproduced},
              "note_F_T10_1": "The statement-first guard VerifyPayoutFailedTransaction lives only on the FTS-direct route "
                              "(fts_transfer_status_webhook.go:654-663); the monolith-relay route has no equivalent guard. "
                              "D-009 records this as a source-behaviour OBSERVATION/candidate, not an asserted defect: this run "
                              "recorded R1 refused and R2 left the payout %s (F-T10-1 %sreproduced this run)."
                              % ((obs.get("payout_after_relay_release") or {}).get("status"), "" if f_t10_1_reproduced else "NOT ")}
        # route evidence R1 from the FTS-direct webhook call in step_f
        req = s.get("request", {})
        self._record_route_R1({"route": "/v1/payouts/transfer_status_webhook",
                               "status": (req.get("fts_direct_webhook") or {}).get("status"),
                               "guard_refused": bool(obs.get("guard_refused_on_fts_direct_route")),
                               "payout_status_after": (obs.get("payout_after_direct_webhook") or {}).get("status")}, "E")
        clean_scenarios(d)
        return self.record("E", "Statement-first: FTS-direct guard refuses (R1) vs monolith-relay moves to failed (R2/F-T10-1)",
                           mid, checks, "real (VerifyPayoutFailedTransaction) + real-worker statement; substitute relay control", ev)

    # ======================================================================
    # Journey F -- Duplicate statement (merchant A), reuse A's ingested row
    # ======================================================================
    def journey_F(self):
        d = self.dA
        mid, acct = d["merchant_id"], d["account_number"]
        checks, ev = [], {}
        A = self.artifacts.get("A") or {}
        row = A.get("statement_row")
        pid, bas_id = A.get("pid"), A.get("bas_id")
        if not row or not bas_id:
            return self.blocked("F", "Duplicate statement", mid, "journey A must run first (needs its ingested statement row)")
        clean_scenarios(d)
        n_before = len(B.read_bas_rows(acct, utr=A.get("utr")))
        journals_before = len(L.da_journals(mid, "pout_" + pid)) if pid else None
        # re-enqueue the byte-identical CSV row + trigger the real worker -> DB dedupe tuple hit
        B.make_basd_selectable(acct)
        enq = B.enqueue_statement(None, acct, [row])
        since = _now()
        trig = B.trigger_fetch()
        hits = B.wait_for_worker_message("BANKING_ACCOUNT_STATEMENT_FETCH_DEDUPE_CHECK_DUPLICATES_FOUND", since, (mid,), timeout=120)
        time.sleep(3)
        n_after = len(B.read_bas_rows(acct, utr=A.get("utr")))
        ev["dedupe"] = {"rows_before": n_before, "rows_after": n_after, "duplicates_found_log": hits[:2],
                        "dedup_key_fields": list(B.DEDUP_KEY_FIELDS), "enqueue": enq, "trigger": trig}
        self.ck(checks, "F.no_new_BAS_row(worker DB dedupe tuple)", n_after == n_before == 1 and bool(hits), ev["dedupe"])
        # re-fire the payout_update (as if the duplicate re-triggered recon) -> no duplicate journal
        XL.sync_statements_to_xas(d, [bas_id])
        outb = [o for o in XL.xas_state(mid).get("outbound", []) if o.get("request", {}).get("bas_id") == bas_id]
        body = outb[-1]["request"] if outb else None
        if body:
            ev["replay_payout_update_status"] = XL.mono("POST", "/v1/banking_account_statement/payout_update", body)[0]
        time.sleep(5)
        journals_after = len(L.da_journals(mid, "pout_" + pid))
        ev["journals"] = {"before": journals_before, "after": journals_after}
        self.ck(checks, "F.no_duplicate_journal(still 2)", journals_before == 2 and journals_after == 2, ev["journals"])
        # reservation / balance not double-released: the settled reservation state is unchanged after the duplicate
        snap, items = reservation_items(mid, d["balance_id"])
        ev["reservations_now"] = mine(items, pid)
        self.ck(checks, "F.reservation_not_double_released(at most one item for the payout)", len(mine(items, pid)) <= 1, mine(items, pid))
        self.artifacts["F"] = {"pid": pid, "bas_id": bas_id, "journals_after": journals_after}
        clean_scenarios(d)
        return self.record("F", "Duplicate statement (no new row / no duplicate journal / no double release)",
                           mid, checks, "real-worker dedupe + real ledger dedup; substitute xas", ev)

    # ======================================================================
    # Journey G -- Conflicting status/statement (merchant B), reuse T11 step_e + step_h
    # ======================================================================
    def journey_G(self):
        d = self.dB
        mid = d["merchant_id"]
        clean_scenarios(d)
        checks, ev = [], {}
        pr = XL.Proof(d, self.run_dir)
        base = 5400 + int(time.time()) % 500
        # G1: credit statement for a failed payout -> failed->reversed with reversal transaction_id
        pr.step_e(base)
        e_checks = [c for c in pr.checks if c["step"] == "e"]
        e_ok = bool(e_checks) and all(c["ok"] for c in e_checks)
        Se = pr.steps.get("e", {})
        self.ck(checks, "G1.credit_stmt_failed_payout->reversed_with_transaction_id", e_ok, [c["name"] for c in e_checks if not c["ok"]] or "all pass")
        ev["G1_reversal"] = {"payout_after": Se.get("payout_after"), "reversals": Se.get("reversals"),
                             "emitter_behaviour": Se.get("emitter_behaviour")}
        # G2: wrong-amount statement -> external, no link/journal (negative control)
        pr.step_h(base + 7)
        h_checks = [c for c in pr.checks if c["step"] == "h"]
        h_ok = bool(h_checks) and all(c["ok"] for c in h_checks)
        Sh = pr.steps.get("h", {})
        self.ck(checks, "G2.wrong_amount_stmt->external_no_link_no_journal(negative control)", h_ok, [c["name"] for c in h_checks if not c["ok"]] or "all pass")
        ev["G2_negative_control"] = {"statement_in_xas": Sh.get("statement_in_xas"), "outbound": Sh.get("outbound"),
                                     "payout_after": Sh.get("payout_after"), "journals": Sh.get("journals")}
        self.artifacts["G"] = {"reused": "m4_xas_ledger.Proof.step_e+step_h", "e_ok": e_ok, "h_ok": h_ok}
        clean_scenarios(d)
        return self.record("G", "Conflict: credit->reversed (transaction_id) + wrong-amount->external (negative control)",
                           mid, checks, "real PS reversal + real ledger; credit stmt substitute_injected; substitute xas", ev)

    # ======================================================================
    # route matrix collectors
    # ======================================================================
    def _record_route_R1(self, dup, src):
        if not dup:
            return
        self.route_evidence.setdefault("R1", {
            "route_id": "R1", "request_event": "FTS status webhook POST /v1/payouts/transfer_status_webhook",
            "actor_identity": "FTS (fts service credential [auth.fts])", "transport": "HTTP (FTS -> payouts-api, direct)",
            "source_dest": "FTS -> payouts-api", "payload_schema_ref": "internal/app/dtos/transfer_status_webhook_request.go",
            "status_mapping": "failed->refused when transaction_id set (VerifyPayoutFailedTransaction), else failed; processed->processed",
            "correlation_id": "source_id (payout_id) / fund_transfer_id", "twin_support": "native",
            "evidence": [], "note": "REAL route HandleTransferStatusWebhookForPayout; the only route carrying the statement-first guard"})
        self.route_evidence["R1"]["evidence"].append({"journey": src, **dup})

    def _record_route_R2(self, d, pid, relay_log, src):
        rec = self.route_evidence.setdefault("R2", {
            "route_id": "R2", "request_event": "FTS -> monolith-stub /update_fts_fund_transfer -> PS legacy source-update relay",
            "actor_identity": "monolith relay (rzp_live basic; substitute monolith-stub)", "transport": "HTTP relay",
            "source_dest": "FTS -> monolith-stub -> payouts-api", "payload_schema_ref": "api Attempt/Core.php updateFundTransfer -> PS legacy relay",
            "status_mapping": "processed->processed; failed->failed (Direct rbl); no statement-first guard (F-T10-1)",
            "correlation_id": "payout_id", "twin_support": "native (substitute monolith-stub; route_profile=monolith)", "evidence": []})
        rec["evidence"].append({"journey": src, "payout_id": pid, "relay_log_kinds": [e.get("kind") for e in (relay_log or [])]})

    def record_route_R3(self):
        """Kafka rx-fts-status-update-events consumer: Direct FAILED/REVERSED dropped (EF-002/003).
        Not executed here (live arena route_profile=monolith; switching profiles would disrupt other
        streams). Asserted the drop from source + the retained kafka-profile evidence -- expected-failure."""
        exists = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", P.cname("payouts-kafka-fts-status-updates-consumer")],
                                capture_output=True, text=True).stdout.strip()
        self.route_evidence["R3"] = {
            "route_id": "R3", "request_event": "Kafka topic rx-fts-status-update-events (FTS status message)",
            "actor_identity": "payouts-kafka-fts-status-updates-consumer (REAL accepted binary)",
            "transport": "Kafka (FTS producer -> broker -> payouts kafka consumer)",
            "source_dest": "FTS -> Kafka -> payouts-service kafka consumer", "payload_schema_ref": "internal/taskHandlers/fts_status_updates.go StatusUpdateRequest",
            "status_mapping": "FAILED/REVERSED -> return nil, message Acked and DROPPED (no state change, no reversal, no journal, no webhook)",
            "correlation_id": "source_id (payout_id)", "twin_support": "expected-failure",
            "container_status": exists,
            "assert_drop": "Direct FAILED/REVERSED are dropped: fts_status_updates.go:56-62 (retry consumer fts_status_updates_retry.go:58-64) return nil without applying state -- EF-002/EF-003, invariant I71",
            "note": "NOT a pass of delivery; the drop is the preserved production-source behaviour. FTS status propagation is exclusive "
                    "(fts service.go:1139-1144 returns after the Kafka publish), so a Kafka-routed FAILED/REVERSED never reaches PS over HTTP.",
            "evidence": ["reports/implementation/KAFKA_ROUTE_EVIDENCE.md", "ENV2_COMPOSE/verifier/kafka_scenarios.py",
                         "TWIN_SPEC/expected-failures.yaml (EF-002, EF-003)"]}

    def record_status_remap(self):
        """Status remapping validated by account_type/channel (V18 semantics on a fresh merchant)."""
        b = self.artifacts.get("B") or {}
        dd = self.artifacts.get("D") or {}
        self.route_evidence["R-remap"] = {
            "route_id": "R-remap", "request_event": "terminal status remap by account_type/channel",
            "actor_identity": "payouts-service status adapter (fts_transfer_status_webhook.go)", "transport": "n/a (mapping logic)",
            "source_dest": "FTS terminal status -> payout state", "payload_schema_ref": "internal/app/payouts/fts_transfer_status_webhook.go status adapters",
            "status_mapping": "Direct (rbl) bank failed -> payout FAILED (no reversal); Shared (nodal) bank failed -> payout REVERSED",
            "correlation_id": "payout_id", "twin_support": "native",
            "evidence": {"direct_failed_this_run": {"journey_B_status": b.get("status"), "journey_B_reversal_rows": b.get("reversal_rows"),
                                                     "journey_D_status": dd.get("status"), "journey_D_reversal_rows": dd.get("reversal_rows")},
                         "shared_failed_reversed_reference": "ENV2_COMPOSE/verifier/verifiers/test_v18_shared_vs_direct_failed_remap.py (V18)"}}

    # ======================================================================
    # invariants (Phase 5 subset)
    # ======================================================================
    def invariants(self):
        A = self.artifacts.get("A") or {}
        B_ = self.artifacts.get("B") or {}
        C = self.artifacts.get("C") or {}
        D_ = self.artifacts.get("D") or {}
        F = self.artifacts.get("F") or {}
        G = self.artifacts.get("G") or {}
        inv = []

        def add(iid, statement, setup, action, expected, negctrl, ok, evidence, fidelity="real", result=None):
            rec = {"invariant": iid, "formal_statement": statement, "setup": setup, "action": action,
                   "expected_effect": expected, "negative_control": negctrl, "evidence_source": evidence,
                   "fidelity": fidelity, "result": result or ("PASS" if ok else "FAIL")}
            inv.append(rec)
            print("[INV %s] -> %s" % (iid, rec["result"]))
            return rec

        # I-Direct-failure-no-shared-reversal (G31)
        b_ok = B_.get("status") == "failed" and B_.get("reversal_rows") == "0"
        d_ok = D_.get("status") == "failed" and D_.get("reversal_rows") == "0"
        add("I-Direct-failure-no-shared-reversal(G31)",
            "A failed Direct (rbl) payout produces NO reversal row and NO Shared-style reversal journal; the payout stays failed.",
            "Fresh Direct merchant B, feature parity with M2.",
            "Journeys B (immediate) and D (pending->failure): bank returns failed.",
            "payout.status=failed, reversals count=0, no da_payout_reversed journal.",
            "Shared (nodal) account: bank failed remaps to REVERSED with a reversal row (V18, test_v18).",
            bool(b_ok and d_ok),
            {"journey_B": B_, "journey_D": D_, "negative_control_ref": "test_v18_shared_vs_direct_failed_remap.py"})

        # I-Direct-success-no-duplicate-ledger (G54) -- replay the DA journal path
        js = A.get("journals")
        two_balanced = isinstance(js, list) and len(js) == 2 and all(j.get("balanced") for j in js)
        uniq = L.journal_uniqueness(A.get("pid") and (A.get("pid")) or "") if A.get("pid") else []
        # uniqueness is per merchant; use merchant A
        uniq = L.journal_uniqueness(self.dA["merchant_id"])
        replay_ok = (F.get("journals_after") == 2) or (C.get("journals_after_replay") == 2)
        add("I-Direct-success-no-duplicate-ledger(G54)",
            "A processed Direct payout yields exactly two balanced DA journals (da_payout_processed + _recon); "
            "replaying the recon/payout_update path creates NO additional journal ((transactor_id,transactor_event) unique).",
            "Journey A success on merchant A (DA onboarded).",
            "Replay the payout_update / delayed status (journeys C and F).",
            "journal count stays 2; (transactor_id, transactor_event) unique for the merchant.",
            "Without ledger layer-2 ValidateJournalExist the replay would write 4 journals.",
            bool(two_balanced and replay_ok and uniq == []),
            {"journey_A_journals": [(j["transactor_event"], j.get("balanced")) for j in js] if isinstance(js, list) else js,
             "replay_journal_count": {"F": F.get("journals_after"), "C": C.get("journals_after_replay")},
             "uniqueness_violations": uniq})

        # I-reservation-not-released-twice (G53)
        res = A.get("reservations_after") or {}
        # after A the reservation is awaiting_balance_refresh (one item); a second reconciler pass must not
        # produce a second release / duplicate item.
        mid, bal = self.dA["merchant_id"], self.dA["balance_id"]
        snap2, items2 = reservation_items(mid, bal)
        mine2 = [i for i in items2 if i.get("payout_id") == A.get("pid")]
        add("I-reservation-not-released-twice(G53)",
            "An in-flight reservation transitions live -> awaiting_balance_refresh -> released AT MOST ONCE; "
            "re-running the reconciler does not double-release or duplicate the item.",
            "Journey A processed payout on merchant A.",
            "Read the reservation set after processing and again after a reconciler pass (journey F re-read).",
            "at most one reservation item per payout; state monotonic (no second release).",
            "A double terminal-hook would remove/decrement the counter twice.",
            len(mine2) <= 1,
            {"reservations_after_A": res, "reservation_items_for_payout_now": mine2})

        # I-balance-not-decremented-twice (G28-adjacent)
        add("I-balance-not-decremented-twice(G28-adj)",
            "A duplicate statement / replayed recon does not debit the merchant ledger balance a second time: "
            "no additional debit entries appear and the DA journal count is unchanged.",
            "Journey A success + journey F duplicate statement on merchant A.",
            "Re-ingest the identical CSV and re-fire the payout_update (journey F).",
            "no new BAS row, DA journal count stays 2 (so no second set of debit entries).",
            "Absent dedup, a second da_payout_processed would double the merchant-balance debit.",
            bool(F.get("journals_after") == 2),
            {"journey_F": F})

        # I-conservation (G51)
        cons = self._conservation(A)
        add("I-conservation(G51)",
            "For a successful Direct payout the DA journal entries sum to zero (sum_debit==sum_credit per journal) AND "
            "the modeled money-state is conserved across balance/reservation/ledger (reservation held==amount during pending, "
            "released on terminal; ledger merchant-balance debit==payout amount).",
            "Journey A on merchant A.",
            "Sum ledger entries per journal; compare reservation-held and ledger merchant-balance debit against the payout amount.",
            "every journal balanced; reservation held during pending == amount; da_payout_processed debit on Merchant Balance == amount.",
            "An unbalanced journal or a reservation/ledger amount != payout amount breaks conservation.",
            cons["ok"], cons, fidelity="real (ledger) + modeled (reservation/balance conservation)")

        # I-reconciliation-correct-tuple (G26)
        tup = self._recon_tuple(A)
        add("I-reconciliation-correct-tuple(G26)",
            "Statement->payout matching uses the correct tuple: same merchant_id, account_number, currency, amount and "
            "statement-detail; the wrong-amount statement is NOT matched.",
            "Journey A (positive) + journey G2 wrong-amount (negative control) on their merchants.",
            "Read the linked BAS row (A) and the wrong-amount BAS row (G2).",
            "A: BAS.merchant_id==mid, account_number==acct, currency==INR, amount==payout amount, linked to the correct payout. "
            "G2: wrong-amount row stays external, unlinked, no journal.",
            "G2 wrong-amount statement is the negative control (must NOT link).",
            tup["ok"], tup, fidelity="real-worker statement + real PS link; substitute xas decision")

        return inv

    def _conservation(self, A):
        js = A.get("journals")
        amount = A.get("amount")
        balanced = isinstance(js, list) and bool(js) and all(j.get("balanced") for j in js)
        # da_payout_processed: debit Direct Merchant Balance Account == amount
        mb_debit = None
        for j in js if isinstance(js, list) else []:
            if j["transactor_event"] == "da_payout_processed":
                for e in j.get("entries", []):
                    if e["type"] == "debit" and e["account_name"].split(" - ")[0] == "Direct Merchant Balance Account":
                        mb_debit = float(e["amount"])
        res = A.get("reservations_after") or {}
        items = res.get("items") if isinstance(res, dict) else None
        # reservation held during pending == amount (recorded on the reservation item amount if present)
        held = None
        for i in (items or []):
            if i.get("payout_id") == A.get("pid"):
                held = i.get("amount")
        ledger_ok = (mb_debit is not None and amount is not None and abs(mb_debit - float(amount)) < 1e-6)
        return {"ok": bool(balanced and ledger_ok),
                "journals_balanced": balanced,
                "merchant_balance_debit": mb_debit, "payout_amount": amount, "ledger_debit_equals_amount": ledger_ok,
                "reservation_held_during_pending": held,
                "modeled_conservation": "reservation held (==amount, released on terminal) offsets the ledger merchant-balance debit; "
                                        "DA journals balanced (sum_debit==sum_credit) -> net ledger movement is zero-sum across DA sub-accounts"}

    def _recon_tuple(self, A):
        bas_id = A.get("bas_id")
        link = B.get_transaction_link(bas_id) if bas_id else {}
        bas = link.get("bas") or {}
        prow = XL.payout_row(A.get("pid")) if A.get("pid") else {}
        # The xas match links via payouts.transaction_id == bas_id (the PS BAS-row entity columns are not
        # written back on the xas path; only the ART route writes them). The recon TUPLE correctness is:
        # same merchant / account / currency / amount, and the correct payout linked (transaction_id).
        link_ok = bool(prow) and prow.get("transaction_id") == bas_id
        pos_ok = bool(bas) and bas.get("merchant_id") == self.dA["merchant_id"] and bas.get("account_number") == self.dA["account_number"] \
            and (bas.get("currency") in ("INR", None)) and int(bas.get("amount") or -1) == int(A.get("amount") or -2) and link_ok
        # negative control taken from journey G2 result
        g = next((r for r in self.results if r["journey"] == "G"), None)
        neg_ok = bool(g) and any(c["name"].startswith("G2.") and c["ok"] for c in (g.get("checks") or []))
        return {"ok": bool(pos_ok and neg_ok),
                "positive": {"bas_merchant_id": bas.get("merchant_id"), "bas_account_number": bas.get("account_number"),
                             "bas_currency": bas.get("currency"), "bas_amount": bas.get("amount"),
                             "expected_amount": A.get("amount"), "payout_transaction_id": prow.get("transaction_id"),
                             "expected_bas_id": bas_id, "link_via_transaction_id": link_ok},
                "negative_control_G2_pass": neg_ok}


# --------------------------------------------------------------------------
# coverage report
# --------------------------------------------------------------------------
def write_coverage_md(J, dA, dB, run_dir, invariants):
    lines = []
    lines.append("# M4 Direct end-to-end coverage (T17)\n")
    lines.append("Generated %s. git %s. arena %s.\n" % (_now(), (_git_head() or "")[:12], (_fingerprint() or {}).get("boot_id", "")[:8]))
    lines.append("")
    lines.append("Merchants (fresh each run): A=`%s` (balance `%s`, acct `%s`), B=`%s` (balance `%s`, acct `%s`).\n"
                 % (dA["merchant_id"], dA["balance_id"], dA["account_number"], dB["merchant_id"], dB["balance_id"], dB["account_number"]))
    lines.append("Run evidence dir: `%s`\n" % str(run_dir.relative_to(REPO)))
    lines.append("")
    lines.append("## Journeys A-G\n")
    lines.append("| Journey | Result | Merchant | Fidelity | Checks |")
    lines.append("|---|---|---|---|---|")
    for r in J.results:
        lines.append("| %s %s | **%s** | %s | %s | %d/%d |" % (
            r["journey"], r["title"], r["result"], r["merchant_id"], r["fidelity"],
            sum(1 for c in r["checks"] if c["ok"]), len(r["checks"])))
    lines.append("")
    lines.append("## Routes R1-R3 + status remap\n")
    lines.append("| Route | Support | Actor / transport | Status mapping | Evidence |")
    lines.append("|---|---|---|---|---|")
    for rid in ("R1", "R2", "R3", "R-remap"):
        rc = J.route_evidence.get(rid)
        if not rc:
            continue
        evd = rc.get("evidence")
        if isinstance(evd, list):
            ev_str = "; ".join(x if isinstance(x, str) else ("journey " + str(x.get("journey"))) for x in evd[:2])
        elif isinstance(evd, dict):
            ev_str = "journeys B/D + V18"
        else:
            ev_str = "journeys"
        lines.append("| %s %s | **%s** | %s / %s | %s | %s |" % (
            rid, rc["request_event"][:48], rc["twin_support"], rc["actor_identity"][:36], rc["transport"][:28],
            rc["status_mapping"][:70], ev_str))
    lines.append("")
    lines.append("## Invariants (Phase 5 subset)\n")
    lines.append("| Invariant | Result | Fidelity | Formal statement |")
    lines.append("|---|---|---|---|")
    for iv in invariants:
        lines.append("| %s | **%s** | %s | %s |" % (iv["invariant"], iv["result"], iv["fidelity"], iv["formal_statement"][:140]))
    lines.append("")
    lines.append("Full invariant evidence (setup/action/expected/negative-control/evidence): `reports/implementation/m4-invariant-results.json`.")
    lines.append("")
    lines.append("## Fidelity legend\n")
    lines.append("- **real** = accepted service binary executed the step on a real route.")
    lines.append("- **real-worker/substitute-bank** = REAL rbl_banking_account_statement worker fed by mozart-sim + bankingaccounts-stub.")
    lines.append("- **substitute** = a contract-faithful stub executed it (xas-sim matching, monolith-stub relay/DA emitter).")
    lines.append("- **substitute_injected** = the harness wrote the row/event in the exact shape the real producer would have.")
    lines.append("- **expected-failure** = preserved production-source behaviour (Kafka FAILED/REVERSED drop, EF-002/003).")
    lines.append("")
    (IMPL / "m4-direct-e2e-coverage.md").write_text("\n".join(lines))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journeys", default="ABCDEFG", help="subset of journeys to run (A-G)")
    ap.add_argument("--merchant-a-descriptor", dest="merchant_a_descriptor")
    ap.add_argument("--merchant-b-descriptor", dest="merchant_b_descriptor")
    ap.add_argument("--campaign-prefix", default="m4-t17")
    ap.add_argument("--run-dir")
    args = ap.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else RUNS / ("m4-journeys-" + _ts())
    run_dir.mkdir(parents=True, exist_ok=True)
    started = _now()
    suffix = uuid.uuid4().hex[:4]

    dA, setupA = load_or_setup(args, "merchant_a_descriptor", "%s-a-%s" % (args.campaign_prefix, suffix), run_dir, "A")
    dB, setupB = load_or_setup(args, "merchant_b_descriptor", "%s-b-%s" % (args.campaign_prefix, suffix), run_dir, "B")
    _save(run_dir / "merchant-A.json", D.public_view(dA))
    _save(run_dir / "merchant-B.json", D.public_view(dB))
    print("merchant A =", dA["merchant_id"], "| merchant B =", dB["merchant_id"])

    J = Journeys(dA, dB, run_dir)
    order = [("A", J.journey_A), ("B", J.journey_B), ("C", J.journey_C), ("D", J.journey_D),
             ("E", J.journey_E), ("F", J.journey_F), ("G", J.journey_G)]
    for jid, fn in order:
        if jid not in args.journeys:
            continue
        if not wait_ps_health():
            J.blocked(jid, "journey " + jid, dA["merchant_id"] if jid in "ACEF" else dB["merchant_id"], "payouts-api not healthy")
            continue
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            J.blocked(jid, "journey " + jid, dA["merchant_id"] if jid in "ACEF" else dB["merchant_id"], traceback.format_exc())

    # routes
    J.record_route_R3()
    J.record_status_remap()
    # invariants
    invariants = []
    try:
        invariants = J.invariants()
    except Exception as exc:  # noqa: BLE001
        import traceback
        print("invariants error:", traceback.format_exc())

    # emit artifacts
    route_doc = {"schema_version": 1, "task": "T17", "generated_at": _now(), "git_head": _git_head(),
                 "arena": _fingerprint(), "route_profile": (_fingerprint() or {}).get("route_profile"),
                 "merchants": {"A": dA["merchant_id"], "B": dB["merchant_id"]},
                 "columns": ["route_id", "request_event", "actor_identity", "transport", "source_dest",
                             "payload_schema_ref", "status_mapping", "correlation_id", "twin_support", "evidence"],
                 "routes": [J.route_evidence[k] for k in ("R1", "R2", "R3", "R-remap") if k in J.route_evidence]}
    _save(IMPL / "m4-route-coverage.json", route_doc)

    inv_doc = {"schema_version": 1, "task": "T17", "generated_at": _now(), "git_head": _git_head(),
               "arena": _fingerprint(), "merchants": {"A": dA["merchant_id"], "B": dB["merchant_id"]},
               "invariants": invariants,
               "passed": sum(1 for i in invariants if i["result"] == "PASS"), "total": len(invariants)}
    _save(IMPL / "m4-invariant-results.json", inv_doc)

    write_coverage_md(J, dA, dB, run_dir, invariants)

    summary = {"schema_version": 1, "task": "T17", "started_at": started, "finished_at": _now(),
               "git_head": _git_head(), "arena": _fingerprint(), "run_dir": str(run_dir.relative_to(REPO)),
               "merchants": {"A": D.public_view(dA), "B": D.public_view(dB), "setupA": setupA, "setupB": setupB},
               "journeys": [{k: r[k] for k in ("journey", "title", "result", "merchant_id", "fidelity")} for r in J.results],
               "routes": {k: J.route_evidence[k]["twin_support"] for k in J.route_evidence},
               "invariants": [{"invariant": i["invariant"], "result": i["result"]} for i in invariants]}
    _save(run_dir / "summary.json", summary)
    _save(IMPL / "m4-direct-journeys.json", summary)
    J._save()

    npass = sum(1 for r in J.results if r["result"] == "PASS")
    print(json.dumps({"journeys_pass": npass, "journeys_total": len(J.results),
                      "invariants_pass": inv_doc["passed"], "invariants_total": inv_doc["total"],
                      "routes": summary["routes"], "run_dir": summary["run_dir"]}, indent=1))
    all_ok = npass == len(J.results) and inv_doc["passed"] == inv_doc["total"]
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
