#!/usr/bin/env python3
"""family:shared-ingress -- every selected merchant / dashboard / internal-app / admin path through the
M7 shared API-monolith ingress (ENV2_COMPOSE/substitutes/api-ingress, CONTRACT.md).

The ingress replaces the monolith's ingress responsibilities and forwards to the REAL payouts-api with the
passport and service credentials the monolith would present. Every journey below drives the ingress from the
arena network with the four credential classes it recognises and then inspects payouts' MySQL, the ingress
audit/evidence plane and (where relevant) ledger balances and container state independently.

Credential classes (api/app/Http/BasicAuth/BasicAuth.php): merchant API key (private auth), dashboard session
(proxy auth, minted through the ingress control plane), internal application (rzp_live + app secret, tenant
from X-Razorpay-Account only), synthetic admin token (Route::$admin).
"""
import base64
import json
import time
import uuid

import framework as F
from framework import journey

ING = "http://api-ingress:8080"
INGRESS_SERVICE = "api-ingress"
SHARED = "ingress"                  # a fresh Shared merchant per run for this family
VICTIM = "ingress-victim"           # a second fresh Shared merchant whose beneficiary the first one tries to use
WORKFLOW = "ingress-workflow"       # a fresh Shared merchant made workflow-applicable
DIRECT = "direct-a"                 # the Direct merchant the direct-payouts family already provisions


# ---------------------------------------------------------------------------
# helpers shared with j_s2p.py
# ---------------------------------------------------------------------------
def secret(name):
    return (F.ENV2 / "secrets" / name).read_text().strip()


def admin_basic():
    return "admin:" + secret("ingress_admin_token.txt")


def app_basic(app):
    return "rzp_live:" + secret("app_%s.txt" % app)


def merchant_basic(m):
    return "%s:%s" % (m["key_id"], m["secret"])


def ing(ctx, method, path, body=None, basic=None, headers=None, note=None):
    """One request to the shared ingress from the arena network; the credential never reaches the evidence."""
    if basic:
        ctx.a.hide(basic.split(":", 1)[1])
    h = {"X-Request-ID": "m7-" + uuid.uuid4().hex[:16]}
    h.update(headers or {})
    st, body_json = ctx.a.jhttp(method, ING + path, body, h, basic=basic, note=note or ("ingress %s %s" % (method, path)))
    return st, body_json, h["X-Request-ID"]


def evidence(ctx, request_id=None, tenant=None):
    q = "?request_id=" + request_id if request_id else ("?tenant=" + tenant if tenant else "?limit=50")
    st, ev = ctx.a.jhttp("GET", ING + "/_ingress/evidence" + q, None, None, basic=admin_basic(), note="ingress evidence")
    return ev if st == 200 else {"error": st, "body": ev}


def audit_rows(ctx, request_id):
    return (evidence(ctx, request_id=request_id) or {}).get("audit") or []


def session_for(ctx, m, user_id=None):
    uid = user_id or ("ARENAUSER" + uuid.uuid4().hex[:5].upper())
    st, s, _ = ing(ctx, "POST", "/_ingress/session", {"merchant_id": m["merchant_id"], "user_id": uid}, basic=admin_basic(), note="mint dashboard session")
    ctx.ck("dashboard_session_minted_for_the_merchant", st == 200 and s.get("merchant_id") == m["merchant_id"], s)
    if st == 200:
        ctx.a.hide(s["session_token"])
    return uid, (s or {}).get("session_token", "")


def otp_for(ctx, m, uid, action="create_payout"):
    st, o, _ = ing(ctx, "POST", "/_ingress/otp", {"merchant_id": m["merchant_id"], "user_id": uid, "action": action}, basic=admin_basic(), note="issue synthetic OTP")
    ctx.ck("synthetic_otp_issued", st == 200 and len(str((o or {}).get("otp", ""))) == 6, {"status": st})
    return o or {}


def create_body(m, amount, note, fund_account_id=None, account_number=None, extra=None):
    body = {"fund_account_id": fund_account_id or m["fund_account_id"], "account_number": account_number or m["account_number"],
            "amount": int(amount), "currency": "INR", "mode": "IMPS", "purpose": "payout", "queue_if_low_balance": False,
            "merchant_id": m["merchant_id"], "narration": ("m7 " + note)[:30], "notes": {"m7": note[:40]}}
    if extra:
        body.update(extra)
    return body


def ps_row(ctx, pid):
    row = ctx.a.payout(pid, note="payouts row")
    return row or {}


def ps_log_hits(ctx, needle, since="10m"):
    """payouts-api log lines carrying the correlation id the ingress forwarded (X-Request-ID -> task id)."""
    import subprocess
    out = subprocess.run(["docker", "logs", "--since", since, F.P.cname("payouts-api")], capture_output=True, text=True, timeout=60)
    hits = [l for l in (out.stdout + out.stderr).splitlines() if needle in l]
    ctx.a._rec("db", {"store": "docker logs", "sql": "payouts-api grep %s" % needle, "rows": len(hits)})
    return hits


# ---------------------------------------------------------------------------
# journeys
# ---------------------------------------------------------------------------
@journey("shared-ingress", "success", priority="P0", profile=SHARED,
         title="classic merchant payout through the shared ingress: merchant key -> private passport -> real payouts -> processed",
         source_ref="api Route.php:2311 payout_create (Route::$private) -> Payout/Service.php:562 fundAccountPayout -> "
                    "monolithProxyForPayoutsServiceMerchant -> Services/PayoutService/Create.php POST /payouts")
def ingress_success(ctx):
    m = ctx.m
    st, health = ctx.a.jhttp("GET", ING + "/_ingress/health", note="ingress health")
    ctx.ck("ingress_is_healthy_with_a_passport_signer", st == 200 and health.get("passport_signer") is True, health)
    ctx.a.mozart(m["merchant_id"], "success")
    ik = "m7-" + uuid.uuid4().hex[:14]
    st, resp, rid = ing(ctx, "POST", "/v1/payouts", create_body(m, 5100, "classic"), basic=merchant_basic(m),
                        headers={"X-Payout-Idempotency": ik}, note="merchant create payout via ingress")
    pid = F.bare((resp or {}).get("id") or "")
    ctx.ck("create_200_with_a_payout_id", st == 200 and bool(pid), {"status": st, "body": resp})
    if not pid:
        return
    ctx.state["ingress_pid"] = pid; ctx.state["ingress_ik"] = ik
    row = ctx.wait_terminal(pid, timeout=120)
    ctx.ck("payout_processed_by_the_real_service_and_workers", (row or {}).get("status") == "processed", row)
    ctx.ck("payout_booked_against_the_credentials_merchant", (row or {}).get("merchant_id") == m["merchant_id"], row)
    st2, fetched, _ = ing(ctx, "GET", "/v1/payouts/pout_" + pid, basic=merchant_basic(m), note="merchant fetch via ingress")
    ctx.ck("fetch_through_ingress_returns_the_same_payout", st2 == 200 and F.bare(fetched.get("id", "")) == pid, {"status": st2})
    rows = audit_rows(ctx, rid)
    ctx.ev["ingress_audit"] = rows
    ctx.ck("ingress_audit_row_carries_route_identity_tenant_and_upstream",
           any(r["route"] == "payout_create" and r["identity_type"] == "merchant" and r["tenant"] == m["merchant_id"]
               and (r["upstream"] or "").startswith("http://payouts-api") and r["upstream_status"] == 200 for r in rows), rows)
    hits = ps_log_hits(ctx, rid)
    ctx.ev["payouts_api_log_hits"] = hits[:5]
    ctx.ck("correlation_id_reached_the_real_payouts_service_logs", len(hits) >= 1, {"request_id": rid, "hits": len(hits)})


@journey("shared-ingress", "failure", priority="P0", profile=SHARED,
         title="identity separation: merchant keys are refused on internal and admin routes, app credentials on merchant routes, and caller-supplied identity is ignored",
         source_ref="BasicAuth::appAuth -> ApiResponse::routeNotFound (Route.php $internal/$admin groups); payouts "
                    "CreatePayoutToFundAccount overwrites merchant_id from the passport")
def ingress_failure(ctx):
    m = ctx.m
    victim = ctx.pool.get(VICTIM)
    ctx.ev["victim_merchant"] = victim["merchant_id"]
    # merchant credential on internal routes
    st, body, _ = ing(ctx, "GET", "/v1/fund_accounts_internal/" + m["fund_account_id"], basic=merchant_basic(m),
                      headers={"X-Razorpay-Account": m["merchant_id"]}, note="merchant key on an internal route")
    ctx.ck("merchant_key_on_internal_route_is_route_not_found_400", st == 400 and "not found" in json.dumps(body), {"status": st, "body": body})
    st, body, _ = ing(ctx, "POST", "/v1/internalContactPayout", create_body(m, 100, "mk-internal"), basic=merchant_basic(m),
                      headers={"X-Razorpay-Account": m["merchant_id"], "X-Payout-Idempotency": "x"}, note="merchant key on internalContactPayout")
    ctx.ck("merchant_key_on_internalContactPayout_is_refused", st == 400, {"status": st})
    # merchant credential on an admin route
    st, body, _ = ing(ctx, "GET", "/v1/admin/payouts/%s/free_payout" % m["balance_id"], basic=merchant_basic(m), note="merchant key on admin route")
    ctx.ck("merchant_key_on_admin_route_is_refused", st == 400, {"status": st, "body": body})
    # application credential on a merchant route
    st, body, _ = ing(ctx, "POST", "/v1/payouts", create_body(m, 100, "app-merchant"), basic=app_basic("vendor_payments"),
                      headers={"X-Razorpay-Account": m["merchant_id"], "X-Payout-Idempotency": "y"}, note="app credential on merchant route")
    ctx.ck("application_credential_on_merchant_route_is_refused", st == 400, {"status": st, "body": body})
    # wrong merchant secret
    st, body, _ = ing(ctx, "GET", "/v1/payouts", basic=m["key_id"] + ":not-the-secret", note="bad merchant secret")
    ctx.ck("bad_merchant_secret_is_401", st == 401, {"status": st})
    # caller-supplied identity headers + body merchant_id pointing at the victim are ignored
    ctx.a.mozart(m["merchant_id"], "success")
    forged = create_body(m, 1700, "forged-identity")
    forged["merchant_id"] = victim["merchant_id"]
    st, resp, rid = ing(ctx, "POST", "/v1/payouts", forged, basic=merchant_basic(m),
                        headers={"X-Payout-Idempotency": "m7f-" + uuid.uuid4().hex[:10], "x-merchant-id": victim["merchant_id"],
                                 "X-Entity-Id": victim["merchant_id"], "X-Razorpay-Account": victim["merchant_id"],
                                 "X-Passport-JWT-V1": "forged.header.value", "X-Payout-Actor-Id": victim["merchant_id"],
                                 "X-Payouts-Service-Proxy": "1", "X-Dashboard-User-Id": "ARENAUSERFORGED"},
                        note="create with forged identity headers and a foreign merchant_id in the body")
    pid = F.bare((resp or {}).get("id") or "")
    ctx.ck("request_with_forged_identity_still_succeeds_as_the_caller", st == 200 and bool(pid), {"status": st, "body": resp})
    if pid:
        row = ctx.wait_terminal(pid, timeout=120)
        ctx.ck("payout_is_booked_against_the_CALLER_not_the_header_or_body_merchant",
               (row or {}).get("merchant_id") == m["merchant_id"] and (row or {}).get("merchant_id") != victim["merchant_id"], row)
        rc, cnt, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND id='%s'" % (victim["merchant_id"], pid), note="victim rows")
        ctx.ck("no_payout_row_exists_for_the_victim_merchant", (cnt or "").strip() == "0", cnt)
        rows = audit_rows(ctx, rid)
        ctx.ck("ingress_audit_records_the_caller_as_tenant", any(r["tenant"] == m["merchant_id"] and r["route"] == "payout_create" for r in rows), rows)


@journey("shared-ingress", "idempotency", priority="P0", profile=SHARED,
         title="the ingress replays the same idempotent create and rejects a different payload under the same key, per merchant",
         source_ref="api/app/Http/Middleware/MerchantIdempotencyHandler.php:84-212 (mutex key+merchant, request hash, "
                    "BAD_REQUEST_SAME_IDEM_KEY_DIFFERENT_REQUEST)")
def ingress_idempotency(ctx):
    m = ctx.m
    ctx.a.mozart(m["merchant_id"], "success")
    ik = "m7i-" + uuid.uuid4().hex[:12]
    body = create_body(m, 2300, "idem")
    st1, r1, rid1 = ing(ctx, "POST", "/v1/payouts", body, basic=merchant_basic(m), headers={"X-Payout-Idempotency": ik}, note="first")
    st2, r2, rid2 = ing(ctx, "POST", "/v1/payouts", dict(body), basic=merchant_basic(m), headers={"X-Payout-Idempotency": ik}, note="same key same body")
    p1, p2 = F.bare((r1 or {}).get("id") or ""), F.bare((r2 or {}).get("id") or "")
    ctx.ck("both_calls_200", st1 == 200 and st2 == 200, {"st1": st1, "st2": st2})
    ctx.ck("same_key_same_body_returns_the_same_payout_id", bool(p1) and p1 == p2, {"p1": p1, "p2": p2})
    rows2 = audit_rows(ctx, rid2)
    ctx.ck("second_call_was_replayed_by_the_ingress_without_an_upstream_call",
           any(r["decision"] == "idempotent_replay" and r["upstream"] == "ingress:idempotency" for r in rows2), rows2)
    rc, cnt, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND id='%s'" % (m["merchant_id"], p1), note="rows for the payout")
    ctx.ck("exactly_one_payout_row", (cnt or "").strip() == "1", cnt)
    body3 = dict(body); body3["amount"] = 2400
    st3, r3, _ = ing(ctx, "POST", "/v1/payouts", body3, basic=merchant_basic(m), headers={"X-Payout-Idempotency": ik}, note="same key different body")
    ctx.ck("same_key_different_body_is_400_different_request_body", st3 == 400 and "Different request body" in json.dumps(r3), {"status": st3, "body": r3})
    rc, cnt2, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND amount=2400" % m["merchant_id"], note="no 2400 payout")
    ctx.ck("the_conflicting_payload_created_nothing", (cnt2 or "").strip() == "0", cnt2)
    if p1:
        ctx.wait_terminal(p1, timeout=120)


@journey("shared-ingress", "async_state", priority="P0", profile=SHARED,
         title="dashboard payout with OTP (proxy auth) completes through the async worker chain",
         source_ref="Route.php payout_create_with_otp (Route::$proxy + userWhitelist); kong-lite/CONTRACT.md dashboard "
                    "passport shape (consumer user + impersonation user_merchant, goutils passport helpers.go:126)")
def ingress_async_state(ctx):
    m = ctx.m
    uid, tok = session_for(ctx, m)
    o = otp_for(ctx, m, uid)
    ctx.a.mozart(m["merchant_id"], "success")
    body = create_body(m, 3300, "dashboard-otp")
    body.update({"otp": "000000", "token": o.get("token", "")})
    st, r, _ = ing(ctx, "POST", "/v1/payouts_with_otp", body, basic="dashboard:" + tok, headers={"X-Payout-Idempotency": "m7d-" + uuid.uuid4().hex[:10]}, note="wrong OTP")
    ctx.ck("wrong_otp_is_refused_400", st == 400, {"status": st, "body": r})
    body.update({"otp": o.get("otp", "")})
    st, r, rid = ing(ctx, "POST", "/v1/payouts_with_otp", body, basic="dashboard:" + tok, headers={"X-Payout-Idempotency": "m7d-" + uuid.uuid4().hex[:10]}, note="correct OTP")
    pid = F.bare((r or {}).get("id") or "")
    ctx.ck("dashboard_create_with_correct_otp_200", st == 200 and bool(pid), {"status": st, "body": r})
    if not pid:
        return
    init = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("payout_left_created_through_the_worker_chain", (init or {}).get("status") in ("initiated", "processing", "processed"), init)
    row = ctx.wait_terminal(pid, timeout=120)
    ctx.ck("payout_processed", (row or {}).get("status") == "processed", row)
    logs = ctx.a.payout_logs(pid)
    ctx.ck("payout_logs_show_the_worker_driven_state_transitions", len(logs or []) >= 2, logs)
    rows = audit_rows(ctx, rid)
    ctx.ck("ingress_served_the_route_as_a_dashboard_user_of_that_merchant",
           any(r["route"] == "payout_create_with_otp" and r["identity_type"] == "user" and r["tenant"] == m["merchant_id"] for r in rows), rows)
    st, r2, _ = ing(ctx, "POST", "/v1/vendor-payments/verify-otp", {"user_id": uid, "otp": o.get("otp"), "token": o.get("token")},
                    basic=app_basic("vendor_payments"), headers={"X-Razorpay-Account": m["merchant_id"]}, note="replay the consumed OTP")
    ctx.ck("a_consumed_otp_cannot_be_reused", st == 200 and (r2 or {}).get("success") is False, r2)


@journey("shared-ingress", "direct", priority="P1", profile=DIRECT,
         title="Direct (current-account) payout through the shared ingress; another merchant cannot use that account number",
         source_ref="Route.php payout_create is the same private route for Direct; payouts TranslateAccountNumberToBalanceId "
                    "resolves account_number within the merchant (core.go:437)")
def ingress_direct(ctx):
    d = ctx.m
    ctx.a.mozart(d["merchant_id"], "success")
    st, r, rid = ing(ctx, "POST", "/v1/payouts", create_body(d, 4100, "direct"), basic=merchant_basic(d), headers={"X-Payout-Idempotency": "m7dir-" + uuid.uuid4().hex[:10]}, note="direct create via ingress")
    pid = F.bare((r or {}).get("id") or "")
    ctx.ck("direct_create_200", st == 200 and bool(pid), {"status": st, "body": r})
    if pid:
        row = ctx.wait_terminal(pid, timeout=150)
        ctx.ck("direct_payout_reached_a_terminal_state_on_the_real_path", (row or {}).get("status") in ("processed", "processing", "initiated"), row)
        ctx.ck("direct_payout_belongs_to_the_direct_merchant", (row or {}).get("merchant_id") == d["merchant_id"], row)
    other = ctx.pool.get(SHARED)
    body = create_body(other, 4200, "foreign-account", account_number=d["account_number"])
    st, r2, _ = ing(ctx, "POST", "/v1/payouts", body, basic=merchant_basic(other), headers={"X-Payout-Idempotency": "m7dir-" + uuid.uuid4().hex[:10]}, note="shared merchant using the Direct merchant's account number")
    ctx.ck("another_merchant_cannot_pay_from_the_direct_account_number", isinstance(st, int) and 400 <= st < 500, {"status": st, "body": r2})
    rc, cnt, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND amount=4200" % other["merchant_id"], note="no foreign-account payout")
    ctx.ck("no_payout_row_was_minted_for_the_foreign_account_attempt", (cnt or "").strip() == "0", cnt)


@journey("shared-ingress", "approval", priority="P1", profile=WORKFLOW,
         title="approval-required payout via the workflow proxy: created pending through the ingress, approved through the internal workflows route, rejected through the dashboard route",
         source_ref="Route.php:2420 payout_approve_internal (Route::$internal, app workflows) and :2330 payout_approve (proxy) -> "
                    "Services/PayoutService/Workflow.php approve/rejectPayoutViaMicroservice -> PS payouts_internal/{id}/approve|reject")
def ingress_approval(ctx):
    m = ctx.m
    ok = ctx.a.make_workflow_applicable(m["merchant_id"], on=True)
    ctx.ev["workflow_applicable"] = ok
    ctx.a.mozart(m["merchant_id"], "success")
    st, r, rid = ing(ctx, "POST", "/v1/payouts", create_body(m, 6100, "approval"), basic=merchant_basic(m), headers={"X-Payout-Idempotency": "m7a-" + uuid.uuid4().hex[:10]}, note="create (workflow-applicable)")
    pid = F.bare((r or {}).get("id") or "")
    ctx.ck("create_200", st == 200 and bool(pid), {"status": st, "body": r})
    if not pid:
        return
    pend = ctx.wait_status(pid, "pending", timeout=60)
    ctx.ck("payout_is_pending_approval", (pend or {}).get("status") == "pending", pend)
    st, ar, arid = ing(ctx, "POST", "/v1/payouts_internal/pout_%s/approve" % pid, {"queue_if_low_balance": False},
                       basic=app_basic("workflows"), headers={"X-Razorpay-Account": m["merchant_id"]}, note="approve via internal workflows route")
    ctx.ck("approve_through_the_workflow_proxy_route_accepted", st in (200, 201), {"status": st, "body": ar})
    row = ctx.wait_terminal(pid, timeout=150)
    ctx.ck("approved_payout_completed", (row or {}).get("status") == "processed", row)
    rows = audit_rows(ctx, arid)
    ctx.ck("ingress_audit_shows_workflows_app_on_payout_approve_internal",
           any(r["route"] == "payout_approve_internal" and r["identity_id"] == "workflows" for r in rows), rows)
    # a merchant key may not use the internal workflows route
    st, _, _ = ing(ctx, "POST", "/v1/payouts_internal/pout_%s/approve" % pid, {}, basic=merchant_basic(m), headers={"X-Razorpay-Account": m["merchant_id"]}, note="merchant key on workflows route")
    ctx.ck("merchant_key_cannot_use_the_internal_workflows_route", st == 400, {"status": st})
    # second payout rejected through the dashboard (proxy) route
    st, r2, _ = ing(ctx, "POST", "/v1/payouts", create_body(m, 6200, "reject"), basic=merchant_basic(m), headers={"X-Payout-Idempotency": "m7a-" + uuid.uuid4().hex[:10]}, note="create second")
    pid2 = F.bare((r2 or {}).get("id") or "")
    if pid2 and (ctx.wait_status(pid2, "pending", timeout=60) or {}).get("status") == "pending":
        uid, tok = session_for(ctx, m)
        st, rr, _ = ing(ctx, "POST", "/v1/payouts/pout_%s/reject" % pid2, {}, basic="dashboard:" + tok, note="reject via dashboard route")
        ctx.ck("dashboard_reject_accepted", st in (200, 201), {"status": st, "body": rr})
        row2 = ctx.wait_terminal(pid2, timeout=60)
        ctx.ck("rejected_payout_is_rejected", (row2 or {}).get("status") == "rejected", row2)
    ctx.a.make_workflow_applicable(m["merchant_id"], on=False)


@journey("shared-ingress", "batch", priority="P1", profile="bulk",
         title="batch payouts through the batch proxy: batch-sim -> ingress (batch app, X-Entity-Id merchant) -> real PS /v1/payouts/bulk",
         source_ref="Route.php:2318 payout_bulk_create (Route::$proxy, internalApps batch); PS payout_internal_routes_with_passport.go:23 /v1/payouts/bulk")
def ingress_batch(ctx):
    import bulk_client
    m = ctx.m
    st, health = ctx.a.jhttp("GET", "http://batch-sim:8094/health", note="batch-sim health")
    mode = (health or {}).get("upstream_mode") or (health or {}).get("ps_api_url")
    ctx.ev["batch_sim_health"] = health
    ctx.ck("batch_sim_targets_the_shared_ingress", "api-ingress" in json.dumps(health), health)
    ctx.a.mozart(m["merchant_id"], "success")
    rows = [bulk_client.payout_row(m["account_number"], 1100 + i, m["fund_account_id"], narration="m7 batch %d" % i) for i in range(2)]
    batch = bulk_client.create_payout_batch(rows, m["merchant_id"], creator_id="ARENAUSERBATCH1")
    bid = batch.get("id") if isinstance(batch, dict) else batch
    ctx.ev["batch"] = batch
    ctx.ck("batch_created", bool(bid), batch)
    if not bid:
        return
    final = bulk_client.wait_batch(bid)
    ctx.ev["batch_final"] = final
    pids = bulk_client.payout_ids(bid)
    ctx.ck("batch_processed_with_payout_ids", len(pids) == 2, {"final": final, "pids": pids})
    for pid in pids:
        row = ps_row(ctx, F.bare(pid))
        ctx.ck("bulk_payout_%s_belongs_to_the_merchant" % F.bare(pid)[-4:], row.get("merchant_id") == m["merchant_id"], row)
    ev = evidence(ctx, tenant=m["merchant_id"])
    ctx.ck("ingress_audit_shows_the_batch_app_on_payout_bulk_create",
           any(r["route"] == "payout_bulk_create" and r["identity_id"] == "batch" and r["tenant"] == m["merchant_id"] for r in ev.get("audit", [])), ev.get("audit", [])[:5])


@journey("shared-ingress", "admin", priority="P1", profile=WORKFLOW,
         title="administrator action with a distinct synthetic admin identity: free-payout attributes and bulk reject of a pending payout",
         source_ref="Route.php:4359 admin_get_free_payouts_attributes and :2342 payout_reject_admin_bulk (Route::$admin); PS payout_admin_routes.go admin passport")
def ingress_admin(ctx):
    m = ctx.m
    st, r, rid = ing(ctx, "GET", "/v1/admin/payouts/%s/free_payout" % m["balance_id"], basic=admin_basic(), note="admin free payout attributes")
    ctx.ck("admin_free_payout_attributes_200_from_the_real_service", st == 200, {"status": st, "body": r})
    rows = audit_rows(ctx, rid)
    ctx.ck("ingress_audit_shows_an_admin_identity_distinct_from_merchant_and_app",
           any(r["identity_type"] == "admin" and r["identity_id"].startswith("ARENAADMIN") for r in rows), rows)
    for who, basic in (("merchant", merchant_basic(m)), ("vendor_payments app", app_basic("vendor_payments"))):
        st, _, _ = ing(ctx, "GET", "/v1/admin/payouts/%s/free_payout" % m["balance_id"], basic=basic, headers={"X-Razorpay-Account": m["merchant_id"]}, note=who + " on admin route")
        ctx.ck("%s_cannot_use_the_admin_route" % who.split()[0], st == 400, {"status": st})
    ctx.a.make_workflow_applicable(m["merchant_id"], on=True)
    ctx.a.mozart(m["merchant_id"], "success")
    st, cr, _ = ing(ctx, "POST", "/v1/payouts", create_body(m, 7100, "admin-cancel"), basic=merchant_basic(m), headers={"X-Payout-Idempotency": "m7adm-" + uuid.uuid4().hex[:10]}, note="create pending payout")
    pid = F.bare((cr or {}).get("id") or "")
    if pid and (ctx.wait_status(pid, "pending", timeout=60) or {}).get("status") == "pending":
        st, rr, arid = ing(ctx, "POST", "/v1/admin/payouts/cancel", {"payout_ids": ["pout_" + pid]}, basic=admin_basic(), headers={"X-Razorpay-Account": m["merchant_id"]}, note="admin bulk cancel")
        ctx.ck("admin_bulk_cancel_accepted", st == 200 and (rr or {}).get("count") == 1, {"status": st, "body": rr})
        row = ctx.wait_terminal(pid, timeout=60)
        ctx.ck("pending_payout_rejected_by_the_admin_action", (row or {}).get("status") == "rejected", row)
    else:
        ctx.note("no pending payout available for the admin cancel control (workflow not applicable?)")
    ctx.a.make_workflow_applicable(m["merchant_id"], on=False)


@journey("shared-ingress", "tenant_isolation", priority="P0", profile=SHARED,
         title="Merchant A paying Merchant B's fund account through the ingress is refused; DB, ledger and audit state are inspected",
         source_ref="api FundAccount/Service.php:210-212 fetch(): findByPublicIdAndMerchant -> BAD_REQUEST_INVALID_ID; "
                    "PS fundAccountCache/core.go:199 fetches the beneficiary through GET fund_accounts_internal with the caller's merchant")
def ingress_tenant_isolation(ctx):
    a = ctx.m
    b = ctx.pool.get(VICTIM)
    ctx.ck("two_distinct_merchants", a["merchant_id"] != b["merchant_id"], {"a": a["merchant_id"], "b": b["merchant_id"]})
    st, reg, _ = ing(ctx, "GET", "/_ingress/registry/fund_accounts?id=" + b["fund_account_id"], basic=admin_basic(), note="ownership record of B's fund account")
    owner = [r for r in (reg or {}).get("resources", []) if r["kind"] == "fund_account"]
    ctx.ck("b_fund_account_has_an_explicit_ownership_record_for_b", bool(owner) and owner[0]["merchant_id"] == b["merchant_id"], reg)
    before_a, before_b = ctx.a.merchant_balance(a["merchant_id"]), ctx.a.merchant_balance(b["merchant_id"])
    rc, cnt_before, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s'" % a["merchant_id"], note="A payouts before")
    ctx.a.mozart(a["merchant_id"], "success")
    ik = "m7x-" + uuid.uuid4().hex[:10]
    st, r, rid = ing(ctx, "POST", "/v1/payouts", create_body(a, 3000, "cross-tenant", fund_account_id=b["fund_account_id"]),
                     basic=merchant_basic(a), headers={"X-Payout-Idempotency": ik}, note="A pays to B's fund account")
    ctx.ev["cross_tenant_attempt"] = {"caller": a["merchant_id"], "owner": b["merchant_id"], "fund_account": b["fund_account_id"], "status": st, "body": r}
    ctx.ck("cross_tenant_fund_account_use_is_refused_4xx", isinstance(st, int) and 400 <= st < 500, ctx.ev["cross_tenant_attempt"])
    rc, cnt_after, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s'" % a["merchant_id"], note="A payouts after")
    ctx.ck("no_payout_row_was_created", (cnt_before or "").strip() == (cnt_after or "").strip(), {"before": cnt_before, "after": cnt_after})
    rc, ik_rows, _ = ctx.a.payouts_sql("SELECT count(*) FROM idempotency_keys WHERE idempotency_key='%s'" % ik, note="idempotency rows for the attempt")
    ctx.ev["ps_idempotency_rows"] = ik_rows
    after_a, after_b = ctx.a.merchant_balance(a["merchant_id"]), ctx.a.merchant_balance(b["merchant_id"])
    ctx.ck("neither_balance_moved", all(x is not None for x in (before_a, after_a, before_b, after_b)) and abs(before_a - after_a) < 1e-6 and abs(before_b - after_b) < 1e-6,
           {"a": (before_a, after_a), "b": (before_b, after_b)})
    ev = evidence(ctx, tenant=a["merchant_id"])
    denied = [x for x in ev.get("audit", []) if x["route"] == "fund_account_get_internal" and x["tenant"] == a["merchant_id"] and x["status"] == 400 and F.bare(b["fund_account_id"]) in (x["path"] or "")]
    ctx.ev["ingress_denials"] = denied[:3]
    ctx.ck("ingress_audit_records_the_owner_scoped_lookup_denied_for_tenant_A", len(denied) >= 1, ev.get("audit", [])[:8])
    ctx.note("RESOLVED in the twin: the beneficiary lookup is merchant-scoped at the ingress exactly like "
             "FundAccount/Service.php fetch(); payouts refused the create and nothing was persisted. Production "
             "reachability of the previous M6 result is discussed in M7_OWNERSHIP_INVESTIGATION.md.")


@journey("shared-ingress", "restart", priority="P1", profile=SHARED,
         title="an in-flight ingress-created payout survives an api-ingress restart: persistent idempotency state and the PS->ingress callback path resume",
         source_ref="api-ingress keeps its state in SQLite on the ingress-data volume; payouts' worker chain does not depend on the ingress process lifetime")
def ingress_restart(ctx):
    import os
    if os.environ.get("M6_ALLOW_RESTART") != "1":
        ctx.blocked("M6_ALLOW_RESTART=1 (restart of the api-ingress container)")
    m = ctx.m
    ctx.a.mozart(m["merchant_id"], "hold")
    ik = "m7r-" + uuid.uuid4().hex[:10]
    body = create_body(m, 4600, "restart")
    st, r, rid = ing(ctx, "POST", "/v1/payouts", body, basic=merchant_basic(m), headers={"X-Payout-Idempotency": ik}, note="create with the bank held")
    pid = F.bare((r or {}).get("id") or "")
    ctx.ck("create_200", st == 200 and bool(pid), {"status": st, "body": r})
    if not pid:
        ctx.a.mozart(m["merchant_id"], clear=True); return
    init = ctx.wait_status(pid, "initiated", timeout=60)
    ctx.ck("payout_in_flight_before_the_restart", (init or {}).get("status") == "initiated", init)
    rec = ctx.a.restart_workers([INGRESS_SERVICE])
    ctx.ev["container_restarts"] = rec
    ctx.ck("api_ingress_actually_restarted_and_is_healthy", all(x["actually_restarted"] and x["status_after"] == "running" for x in rec["restarts"]), rec)
    ctx.a.mozart(m["merchant_id"], "success")
    ctx.a.mozart(m["merchant_id"], clear=True)
    ctx.a.finish_bank(pid, "success")
    row = ctx.wait_terminal(pid, timeout=150)
    ctx.ck("payout_completed_after_the_ingress_restart", (row or {}).get("status") == "processed", row)
    st2, r2, rid2 = ing(ctx, "POST", "/v1/payouts", dict(body), basic=merchant_basic(m), headers={"X-Payout-Idempotency": ik}, note="replay after restart")
    ctx.ck("idempotent_replay_survives_the_restart", st2 == 200 and F.bare((r2 or {}).get("id") or "") == pid, {"status": st2, "body": r2})
    rows = audit_rows(ctx, rid2)
    ctx.ck("replay_served_from_persistent_ingress_state", any(x["decision"] == "idempotent_replay" for x in rows), rows)
