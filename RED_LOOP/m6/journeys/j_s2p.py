#!/usr/bin/env python3
"""family:cross-domain-s2p -- the connected Source-to-Pay TDS journey through the shared ingress into the
REAL Payouts service (M7 §7).

  fresh ARENA merchant (Payouts provisioner)
    -> vendor_payments app registers the merchant's rzp_tax_pay contact + fund account through the
       shared ingress (POST /v1/contacts_internal, POST /v1/fund_accounts_internal -- the routes vendor-payments'
       own internal_tax_contact.go:109-156 uses) -> explicit merchant-owned records
    -> a REAL classic payout (ingress -> payouts) is the originating payout of the TDS accrual
    -> Kafka add-tds-entry consumed by the pinned vendor-payments source (s2p-vp-source): tax_payments/records
       rows + PATCH /v1/payouts_internal/{id}/tax-payment-id tag-back through the ingress
    -> explicit Pay (source taxpayments.Core.Pay) after advancing the source clock across the month boundary:
       contacts_internal / fund_accounts_internal / banking_accounts_internal / verify-otp / internalContactPayout
       all served by the ingress, the last one forwarded to payouts POST /v1/payouts/internal_contact_payout
       with the vendor_payments application passport -> payouts re-resolves the beneficiary through the ingress
       (rzp_tax_pay contact type gate, payouts/internal/app/contact/type.go) -> real FTS/mozart-sim processing
    -> payouts SourceUpdater HTTP call POST /v1/payouts_service/source_update (source_type tax_payments) ->
       ingress relays PayoutStatusChange to the source driver (/_replica/status) exactly as
       api/app/Services/VendorPayments/Service.php:480 pushPayoutStatusUpdate would -> money_loading_success
    -> independently verified: vendor-payments MySQL, payouts MySQL, ingress audit/callback rows, Kafka offsets.

Nothing is fabricated beyond that: no bank settlement, challan, filing, `paid` status or accounting event.
The S2P stack is RED_LOOP/m7/s2p_stack.py (compose overlay ENV2_COMPOSE/docker-compose.s2p.yml).
"""
import csv
import io
import json
import subprocess
import time
import uuid

import framework as F
import j_ingress as I
from framework import journey

S2P = "s2p"
S2P_NET = "rzp-s2p-internal"
TAX_FA_NUMBER, TAX_FA_IFSC, TAX_FA_NAME = "000000000000001", "TEST0000001", "RZPX PRIVATE LIMITED"   # synthetic constants patched into the S2P runtime (build-runtime-patch.json)
CURL_IMAGE = "curlimages/curl:latest"


# ---------------------------------------------------------------------------
# S2P stack access (private network; the arena's curl-container transport, pointed at rzp-s2p-internal)
# ---------------------------------------------------------------------------
def s2p_http(ctx, method, path, body=None, headers=None, timeout=20):
    cmd = ["docker", "run", "--rm", "--pull", "never", "--network", S2P_NET, CURL_IMAGE, "-s", "-m", str(timeout),
           "-o", "/dev/stdout", "-w", "\n__STATUS__%{http_code}", "-X", method, "http://vp-source:8080" + path]
    for k, v in (headers or {}).items():
        cmd += ["-H", "%s: %s" % (k, v)]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "--data-binary", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    txt = out.stdout
    if "__STATUS__" not in txt:
        st, payload = "ERR", (out.stderr or txt)[:500]
    else:
        payload, code = txt.rsplit("__STATUS__", 1)
        st = int(code.strip()) if code.strip().isdigit() else "ERR"
    ctx.a._rec("http", {"transport": "s2p-internal", "method": method, "url": "http://vp-source:8080" + path, "request": body,
                        "status": st, "response": (payload or "")[:1500]})
    return st, (F.jload(payload) if payload else None)


def s2p_sql(ctx, query):
    out = subprocess.run(["docker", "exec", F.P.cname("s2p-mysql"), "env", "MYSQL_PWD=s2p", "mysql", "-B", "--raw", "-us2p", "vendor_payments", "-e", query],
                         capture_output=True, text=True, timeout=60)
    rows = list(csv.DictReader(io.StringIO(out.stdout), delimiter="\t")) if out.returncode == 0 else []
    ctx.a._rec("db", {"store": "s2p-mysql/vendor_payments", "sql": query, "rows": len(rows), "err": out.stderr[:200] if out.returncode else None})
    return rows


def s2p_publish(ctx, payload, key):
    p = subprocess.run(["docker", "exec", "-i", F.P.cname("s2p-kafka"), "rpk", "topic", "produce", "add-tds-entry", "-k", key],
                       input=json.dumps(payload, separators=(",", ":")) + "\n", capture_output=True, text=True, timeout=60)
    ctx.a._rec("db", {"store": "s2p-kafka", "sql": "rpk topic produce add-tds-entry -k " + key, "rows": 1 if p.returncode == 0 else 0, "err": p.stderr[:200]})
    return p.returncode == 0


def kafka_committed(ctx):
    return len(s2p_sql(ctx, "SELECT id FROM replica_trace WHERE kind='kafka_committed' ORDER BY id"))


def kafka_offsets(ctx):
    out = subprocess.run(["docker", "exec", F.P.cname("s2p-kafka"), "rpk", "topic", "describe", "add-tds-entry", "-p"], capture_output=True, text=True, timeout=60)
    return out.stdout


def _probe(ctx):
    probe = {}
    st, h = s2p_http(ctx, "GET", "/health")
    probe["vp_source"] = h
    probe["ingress"] = ctx.a.jhttp("GET", I.ING + "/_ingress/health", note="ingress health")[1]
    ok = st == 200 and (h or {}).get("ready") and (probe["ingress"] or {}).get("s2p_vp_source_url")
    if not ok:
        ctx.blocked("S2P stack inside the arena (RED_LOOP/m7/s2p_stack.py up) with api-ingress relaying to vp-source", probe)
    ctx.ev["probe"] = probe
    return probe


def register_tax_contact(ctx, m):
    """vendor-payments internal_tax_contact.go:130-156 getOrCreateContact + :109-121 createTaxFA, through the same
    internal routes (contact_create_internal, fund_account_create_internal; Route::$internalApps vendor_payments)."""
    vp = I.app_basic("vendor_payments")
    h = {"X-Razorpay-Account": m["merchant_id"]}
    st, contacts, _ = I.ing(ctx, "GET", "/v1/contacts_internal?type=rzp_tax_pay", basic=vp, headers=h, note="search internal tax contact")
    existing = [c for c in (contacts or {}).get("items", []) if c.get("type") == "rzp_tax_pay"]
    if existing:
        c = existing[0]
    else:
        st, c, _ = I.ing(ctx, "POST", "/v1/contacts_internal", {"name": "Razorpay Tax Payment", "type": "rzp_tax_pay"}, basic=vp, headers=h, note="create internal tax contact")
        ctx.ck("tax_contact_created_by_the_vendor_payments_app", st == 200 and (c or {}).get("type") == "rzp_tax_pay", {"status": st, "body": c})
    st, fa, _ = I.ing(ctx, "POST", "/v1/fund_accounts_internal", {"contact_id": c["id"], "account_type": "bank_account",
                      "bank_account": {"name": TAX_FA_NAME, "ifsc": TAX_FA_IFSC, "account_number": TAX_FA_NUMBER}}, basic=vp, headers=h, note="create tax fund account")
    ctx.ck("tax_fund_account_created_and_owned_by_the_merchant", st == 200 and (fa or {}).get("id", "").startswith("fa_"), {"status": st, "body": fa})
    st, reg, _ = I.ing(ctx, "GET", "/_ingress/registry/fund_accounts?id=" + (fa or {}).get("id", "fa_none"), basic=I.admin_basic(), note="ownership record")
    rec = [r for r in (reg or {}).get("resources", []) if r["kind"] == "fund_account"]
    ctx.ck("ownership_record_names_the_merchant_and_rzp_tax_pay", bool(rec) and rec[0]["merchant_id"] == m["merchant_id"] and rec[0]["contact_type"] == "rzp_tax_pay", reg)
    st, _, _ = I.ing(ctx, "POST", "/_ingress/registry/banking_account", {"merchant_id": m["merchant_id"], "account_number": m["account_number"], "balance_id": m.get("balance_id", ""), "account_type": "shared"},
                     basic=I.admin_basic(), note="register the merchant's banking account (provisioner data)")
    return c, fa


def tds_entry(m, payout_public_id, amount=12500, request_id=None):
    return {"request_id": request_id or ("m7-tds-" + uuid.uuid4().hex[:10]),
            "data": {"merchant_id": m["merchant_id"], "entity_id": payout_public_id, "entity_type": "payout", "entity_status": "processed",
                     "tds_amount": amount, "tds_category_id": 1}}


REMITTANCE_PURPOSE = "rzp_tax_pay"   # vendor-payments taxpayments/constants.go:98 DefaultPayoutPurpose; api Payout/Purpose.php:33; PS payoutPurpose InternalPurposeTypeMap.
# The standalone S2P fixture sent "tax_payment", which only the local boundary replacement accepted: the REAL Payouts service
# answers 400 "Invalid purpose: tax_payment" (recorded mismatch, M7_FINAL_REPORT.md).


def pay_request(m, tax_id, uid, otp, token, amount=12500):
    return {"merchant_id": m["merchant_id"], "purpose": REMITTANCE_PURPOSE, "mode": "IMPS", "notes": {"journey": "m7"}, "narration": "Synthetic TDS remittance",
            "account_number": m["account_number"], "user_id": uid, "otp": otp, "token": token, "tax_payment_id": tax_id,
            "queue_if_low_balance": False, "amount": amount}


def advance_to_next_month(ctx):
    """The source clock (common.TimeNow injection, D-CLOCK) lives in the running vp-source process and only moves
    forward; the accrual month is the clock's month, so remittance needs the first day of the FOLLOWING month."""
    import datetime
    st, h = s2p_http(ctx, "GET", "/health")
    cur = datetime.datetime.strptime((h or {}).get("synthetic_clock", "2026-08-20T06:30:00Z"), "%Y-%m-%dT%H:%M:%SZ")
    nxt = datetime.datetime(cur.year + (1 if cur.month == 12 else 0), 1 if cur.month == 12 else cur.month + 1, 1)
    unix = int((nxt - datetime.datetime(1970, 1, 1)).total_seconds())
    st, adv = s2p_http(ctx, "POST", "/_replica/clock", {"unix": unix})
    ctx.ck("source_clock_advanced_past_the_accrual_month", st == 200, {"from": (h or {}).get("synthetic_clock"), "to": nxt.isoformat(), "status": st, "body": adv})
    return unix


def wait_for(fn, timeout=60, interval=1.0):
    return F.wait_until(fn, timeout=timeout, interval=interval)


def connected_journey(ctx, restart_ingress_between_pay_and_callback=False):
    m = ctx.m
    _probe(ctx)
    contact, fa = register_tax_contact(ctx, m)
    # 1. a REAL originating payout (the entity the TDS accrual tags)
    ctx.a.mozart(m["merchant_id"], "success")
    st, r, rid0 = I.ing(ctx, "POST", "/v1/payouts", I.create_body(m, 250000, "s2p-origin"), basic=I.merchant_basic(m), headers={"X-Payout-Idempotency": "m7s-" + uuid.uuid4().hex[:10]}, note="originating payout")
    origin = F.bare((r or {}).get("id") or "")
    ctx.ck("originating_payout_created_through_the_ingress", st == 200 and bool(origin), {"status": st, "body": r})
    if not origin:
        return
    ctx.wait_terminal(origin, timeout=120)
    # 2. TDS accrual via Kafka into the pinned vendor-payments source
    before_committed = kafka_committed(ctx)
    before_records = s2p_sql(ctx, "SELECT * FROM records WHERE merchant_id='%s' ORDER BY id" % m["merchant_id"]) if s2p_sql(ctx, "SHOW COLUMNS FROM records LIKE 'merchant_id'") else s2p_sql(ctx, "SELECT * FROM records ORDER BY id")
    entry = tds_entry(m, "pout_" + origin)
    key = "m7-" + m["merchant_id"].lower()
    ctx.ck("tds_entry_published", s2p_publish(ctx, entry, key), entry)
    wait_for(lambda: kafka_committed(ctx) >= before_committed + 1, timeout=60)
    tax_rows = wait_for(lambda: s2p_sql(ctx, "SELECT * FROM tax_payments WHERE merchant_id='%s' ORDER BY created_at" % m["merchant_id"]) or None, timeout=30) or []
    ctx.ck("source_created_the_merchants_monthly_tax_aggregate", len(tax_rows) == 1, tax_rows)
    if not tax_rows:
        return
    tax_id = tax_rows[0]["id"]
    ctx.state["s2p"] = {"origin": origin, "tax_id": tax_id, "entry": entry, "key": key, "contact": contact, "fund_account": fa}
    trace = s2p_sql(ctx, "SELECT kind FROM replica_trace WHERE kind IN ('kafka_received','source_handler_returned','kafka_committed') ORDER BY id")
    ctx.ck("source_consumer_trace_received_handled_committed", {t["kind"] for t in trace} >= {"kafka_received", "source_handler_returned", "kafka_committed"}, trace[-3:])
    tag = wait_for(lambda: (lambda ev: [t for t in ev.get("payout_details", []) if t["payout_id"] == origin] or None)(I.evidence(ctx)), timeout=30)
    ctx.ck("tag_back_reached_the_ingress_for_the_originating_payout_with_the_tax_payment_id",
           bool(tag) and tag[0]["tax_payment_id"] == F.bare(tax_id) and tag[0]["tagged_by_app"] == "vendor_payments" and tag[0]["tagged_by_tenant"] == m["merchant_id"], tag)
    # 3. explicit Pay after the month boundary
    uid = "ARENAS2PUSER01"
    otp = I.otp_for(ctx, m, uid, action="tax_payment")
    pay = pay_request(m, tax_id, uid, otp.get("otp", ""), otp.get("token", ""))
    st, cur = s2p_http(ctx, "POST", "/_replica/pay", pay)
    ctx.ck("current_month_pay_is_rejected_by_the_source_month_guard", st == 422, {"status": st, "body": cur})
    advance_to_next_month(ctx)
    # a fresh OTP: the source consumed the first one when it verified before the month guard? (it verifies inside Pay)
    otp = I.otp_for(ctx, m, uid, action="tax_payment")
    pay = pay_request(m, tax_id, uid, otp.get("otp", ""), otp.get("token", ""))
    st, paid = s2p_http(ctx, "POST", "/_replica/pay", pay)
    ctx.ev["pay_response"] = paid
    ctx.ck("pay_succeeded_through_the_ingress_into_the_real_payouts_service", st == 200, {"status": st, "body": paid})
    if st != 200:
        return
    assoc = wait_for(lambda: s2p_sql(ctx, "SELECT * FROM vendorpayment_payout WHERE tax_payment_id='%s'" % tax_id) or None, timeout=20) or []
    ctx.ck("source_recorded_one_remittance_association", len(assoc) == 1, assoc)
    remit = F.bare((assoc[0] if assoc else {}).get("payout_id") or "")
    ctx.state["s2p"]["remittance"] = remit
    row = ctx.a.payout(remit) if remit else None
    ctx.ck("remittance_payout_exists_in_the_REAL_payouts_db_for_the_merchant", bool(row) and row.get("merchant_id") == m["merchant_id"], row)
    rc, srcs, _ = ctx.a.payouts_sql("SELECT source_id,source_type FROM payout_sources WHERE payout_id='%s'" % remit, note="payout_sources")
    ctx.ck("payouts_persisted_the_tax_payments_source_detail", "tax_payments" in (srcs or "") and F.bare(tax_id) in (srcs or ""), srcs)
    ev = I.evidence(ctx, tenant=m["merchant_id"])
    ipo = [x for x in ev.get("internal_payouts", []) if x["payout_id"] == remit]
    ctx.ck("ingress_recorded_the_internal_contact_payout_with_the_source_idempotency_key", bool(ipo) and ipo[0]["idempotency_key"] == (assoc[0] if assoc else {}).get("idempotency_key"), ipo)
    beneficiary_fetch = [x for x in ev.get("audit", []) if x["route"] == "fund_account_get_internal" and x["identity_id"] == "payouts_service" and x["tenant"] == m["merchant_id"] and x["status"] == 200]
    ctx.ck("payouts_re_resolved_the_tax_beneficiary_through_the_ingress_within_the_tenant", len(beneficiary_fetch) >= 1, beneficiary_fetch[:2])
    if restart_ingress_between_pay_and_callback:
        rec = ctx.a.restart_workers([I.INGRESS_SERVICE])
        ctx.ev["container_restarts"] = rec
        ctx.ck("api_ingress_restarted_while_the_remittance_was_in_flight", all(x["actually_restarted"] and x["status_after"] == "running" for x in rec["restarts"]), rec)
    # 4. worker-driven completion + SourceUpdater relay -> vendor-payments callback
    final = ctx.wait_terminal(remit, timeout=150)
    ctx.ck("remittance_processed_by_the_real_worker_chain", (final or {}).get("status") == "processed", final)
    cb = wait_for(lambda: (lambda e: [c for c in e.get("callbacks", []) if c["payout_id"] == remit] or None)(I.evidence(ctx)), timeout=90, interval=3) or []
    ctx.ev["ingress_callbacks"] = cb
    ctx.ck("ingress_relayed_the_source_update_to_vendor_payments", bool(cb) and cb[0]["source_type"] == "tax_payments" and cb[0]["status"] == 200, cb)
    got = wait_for(lambda: (lambda rows: rows if rows and rows[0]["internal_status"] == "money_loading_success" else None)(s2p_sql(ctx, "SELECT id,status,internal_status FROM tax_payments WHERE id='%s'" % tax_id)), timeout=60, interval=3)
    ctx.ck("source_reached_money_loading_success_and_public_processing", bool(got) and got[0]["status"] == "processing", got)
    st, observed = s2p_http(ctx, "GET", "/_replica/tax-payment?merchant_id=%s&id=%s" % (m["merchant_id"], tax_id))
    ctx.ck("source_api_agrees_with_its_database", st == 200 and (observed or {}).get("tax_payment", {}).get("internal_status") == "money_loading_success", observed)
    cbt = s2p_sql(ctx, "SELECT payload FROM replica_trace WHERE kind='callback_received' ORDER BY id DESC LIMIT 1")
    ctx.ck("source_callback_trace_carries_the_remittance_and_tax_ids", bool(cbt) and remit in cbt[0]["payload"] and F.bare(tax_id) in cbt[0]["payload"], cbt)
    ctx.ck("no_paid_status_challan_or_filing_was_fabricated", bool(got) and got[0]["status"] != "paid", got)
    ctx.ev["kafka_partitions"] = kafka_offsets(ctx)


@journey("cross-domain-s2p", "success", priority="P0", profile=S2P,
         title="connected Source-to-Pay TDS remittance: Kafka accrual -> tag-back -> Pay -> shared ingress -> real payouts -> callback -> money_loading_success",
         source_ref="vendor-payments internal/initiatetds + taxpayments/core.go Pay + apiservice PayoutStatusChange; api Payout/Service.php:330 "
                    "fundAccountPayoutOnInternalContact; api SourceUpdater/Factory.php:31 + VendorPayments/Service.php:480; payouts contact/type.go")
def s2p_success(ctx):
    connected_journey(ctx)


@journey("cross-domain-s2p", "failure", priority="P0", profile=S2P,
         title="negative controls on the connected path: no tax contact, wrong internal app, cross-merchant tax fund account, merchant credential",
         source_ref="vendor-payments internal_tax_contact.go:37-52; api Payout/Service.php:348-360; payouts contact/type.go:35-47")
def s2p_failure(ctx):
    m = ctx.m
    _probe(ctx)
    other = ctx.pool.get(I.VICTIM)
    uid = "ARENAS2PUSER02"
    otp = I.otp_for(ctx, m, uid, action="tax_payment")
    rc, tax_before, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id IN ('%s','%s') AND purpose='rzp_tax_pay'" % (m["merchant_id"], other["merchant_id"]), note="tax payouts before the negative controls")
    st, before, _ = I.ing(ctx, "GET", "/v1/contacts_internal?type=rzp_tax_pay", basic=I.app_basic("vendor_payments"), headers={"X-Razorpay-Account": other["merchant_id"]}, note="other merchant has no tax contact")
    ctx.ck("the_other_merchant_has_no_tax_contact", st == 200 and (before or {}).get("count") == 0, before)
    # an accrual for the other merchant (the tag-back target is an id-only update in the pinned source, so a synthetic
    # originating payout id is enough here), then the month boundary, then Pay with NO rzp_tax_pay contact registered
    committed = kafka_committed(ctx)
    s2p_publish(ctx, tds_entry(other, "pout_ARENA000NOCON1", amount=4200), "m7neg-" + other["merchant_id"].lower())
    wait_for(lambda: kafka_committed(ctx) >= committed + 1, timeout=60)
    rows = wait_for(lambda: s2p_sql(ctx, "SELECT id FROM tax_payments WHERE merchant_id='%s'" % other["merchant_id"]) or None, timeout=30) or []
    ctx.ck("other_merchant_has_a_tax_payment_to_remit", len(rows) == 1, rows)
    advance_to_next_month(ctx)
    pay = pay_request(other, rows[0]["id"] if rows else "txpy_00000000000000", uid, otp.get("otp", ""), otp.get("token", ""))
    st, denied = s2p_http(ctx, "POST", "/_replica/pay", pay)
    ctx.ck("pay_without_a_tax_contact_is_rejected_by_the_source", st == 422 and "contact" in json.dumps(denied).lower(), {"status": st, "body": denied})
    contact, fa = register_tax_contact(ctx, m)
    h = {"X-Razorpay-Account": m["merchant_id"], "X-Payout-Idempotency": "m7neg-" + uuid.uuid4().hex[:8]}
    body = {"account_number": m["account_number"], "fund_account_id": fa["id"], "amount": 12500, "currency": "INR", "mode": "IMPS", "purpose": "rzp_tax_pay",
            "queue_if_low_balance": False, "source_details": [{"source_id": "txpy_00000000000000", "source_type": "tax_payments", "priority": 1}]}
    st, r, _ = I.ing(ctx, "POST", "/v1/internalContactPayout", body, basic=I.app_basic("xpayroll"), headers=h, note="xpayroll app on a rzp_tax_pay fund account")
    ctx.ck("xpayroll_app_cannot_pay_a_rzp_tax_pay_contact", st == 400 and "AppNotPermitted" in json.dumps(r), {"status": st, "body": r})
    st, r, _ = I.ing(ctx, "POST", "/v1/internalContactPayout", body, basic=I.app_basic("vendor_payments"), headers={**h, "X-Razorpay-Account": other["merchant_id"]}, note="vendor_payments for another merchant using this merchant's tax fund account")
    ctx.ck("cross_merchant_tax_fund_account_is_refused", st == 400 and "does not exist" in json.dumps(r), {"status": st, "body": r})
    st, r, _ = I.ing(ctx, "POST", "/v1/internalContactPayout", body, basic=I.merchant_basic(m), headers=h, note="merchant credential on the internal-contact route")
    ctx.ck("merchant_credential_is_refused_on_the_internal_contact_route", st == 400, {"status": st, "body": r})
    st, r, _ = I.ing(ctx, "POST", "/v1/internalContactPayout", body, basic=I.app_basic("vendor_payments"), headers={"X-Razorpay-Account": m["merchant_id"]}, note="no idempotency key")
    ctx.ck("missing_idempotency_key_is_refused_for_vendor_payments", st == 400 and "Idempotency key is missing" in json.dumps(r), {"status": st, "body": r})
    rc, cnt, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id IN ('%s','%s') AND purpose='rzp_tax_pay'" % (m["merchant_id"], other["merchant_id"]), note="tax payouts after the negative controls")
    ctx.ck("no_tax_payout_was_minted_by_any_negative_control", (cnt or "").strip() == (tax_before or "").strip(), {"before": tax_before, "after": cnt})


@journey("cross-domain-s2p", "idempotency", priority="P0", profile=S2P,
         title="duplicate TDS event adds no record; the same internalContactPayout key replays the same payout; repeated Pay is refused",
         source_ref="vendor-payments initiatetds zero-delta replay; MerchantIdempotencyHandler.php; taxpayments/core.go repeated-pay guard")
def s2p_idempotency(ctx):
    st = ctx.state.get("s2p")
    if not st or not st.get("remittance"):
        ctx.blocked("journey:cross-domain-s2p/success must run first (it establishes the tax payment and remittance)")
    m = ctx.m
    before = s2p_sql(ctx, "SELECT * FROM records ORDER BY id")
    committed = kafka_committed(ctx)
    ctx.ck("duplicate_tds_entry_published", s2p_publish(ctx, st["entry"], st["key"]), st["entry"])
    wait_for(lambda: kafka_committed(ctx) >= committed + 1, timeout=60)
    after = s2p_sql(ctx, "SELECT * FROM records ORDER BY id")
    ctx.ck("exact_replay_adds_no_record_zero_delta", len(after) == len(before), {"before": len(before), "after": len(after)})
    ev = I.evidence(ctx, tenant=m["merchant_id"])
    ipo = [x for x in ev.get("internal_payouts", []) if x["payout_id"] == st["remittance"]]
    idem = [x for x in ev.get("idempotency", []) if x["route"] == "payout_create_on_internal_contact" and x["key"] == (ipo[0]["idempotency_key"] if ipo else None)]
    ctx.ck("ingress_holds_the_idempotency_record_of_the_remittance", bool(idem), idem)
    body = {"account_number": m["account_number"], "fund_account_id": st["fund_account"]["id"], "amount": 12500, "currency": "INR", "mode": "IMPS", "purpose": "rzp_tax_pay",
            "queue_if_low_balance": False, "source_details": [{"source_id": st["tax_id"], "source_type": "tax_payments", "priority": 1}]}
    st2, r2, rid = I.ing(ctx, "POST", "/v1/internalContactPayout", {"amount": 99, "fund_account_id": st["fund_account"]["id"]}, basic=I.app_basic("vendor_payments"),
                         headers={"X-Razorpay-Account": m["merchant_id"], "X-Payout-Idempotency": ipo[0]["idempotency_key"] if ipo else "none"}, note="same key different payload")
    ctx.ck("same_key_different_payload_is_refused", st2 == 400 and "Different request body" in json.dumps(r2), {"status": st2, "body": r2})
    uid = "ARENAS2PUSER01"
    otp = I.otp_for(ctx, m, uid, action="tax_payment")
    s3, again = s2p_http(ctx, "POST", "/_replica/pay", pay_request(m, st["tax_id"], uid, otp.get("otp", ""), otp.get("token", "")))
    ctx.ck("repeated_pay_is_refused_without_a_second_remittance", s3 == 422, {"status": s3, "body": again})
    assoc = s2p_sql(ctx, "SELECT * FROM vendorpayment_payout WHERE tax_payment_id='%s'" % st["tax_id"])
    ctx.ck("still_exactly_one_remittance_association", len(assoc) == 1, assoc)
    rc, cnt, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND purpose='rzp_tax_pay'" % m["merchant_id"], note="tax payouts in PS")
    ctx.ck("exactly_one_tax_remittance_payout_in_the_real_payouts_db", (cnt or "").strip() == "1", cnt)


@journey("cross-domain-s2p", "async_state", priority="P0", profile="s2p-restart",
         title="the connected remittance survives an api-ingress restart between Pay and the source callback (worker-driven completion, relayed status)",
         source_ref="api-ingress persistent state (ingress-data volume); payouts SourceUpdater retries per payout status transition")
def s2p_async_state(ctx):
    import os
    if os.environ.get("M6_ALLOW_RESTART") != "1":
        ctx.blocked("M6_ALLOW_RESTART=1 (restart of the api-ingress container)")
    connected_journey(ctx, restart_ingress_between_pay_and_callback=True)
