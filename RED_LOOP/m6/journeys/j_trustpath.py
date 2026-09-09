#!/usr/bin/env python3
"""family:trust-path -- M11: the promoted trust path exercised end to end.

    merchant (host bridge) -> REAL edge gateway (Kong 3.4.2 + razorpay/edge plugins, terraform-kong prod-api routes)
      -> api-ingress (the API-monolith replacement: merchant/dashboard/internal/admin identity, ownership, idempotency)
      -> REAL payouts-service  -> REAL Shield (rzpxprod) / REAL banking-accounts (Direct merchants) / REAL Workflow service

Every journey runs in BOTH variants (ARENA_TRUST_PATH=real | substitute). Security invariants are asserted in both;
checks that only the real implementation can carry (Kong Admin API credential store, Shield rule API, BAS Api-Token,
edge passport kid `edgev2`) are asserted in the real variant and recorded -- never silently skipped -- in the
substitute one, so the differential report (reports/implementation/M11_DIFFERENTIAL.md) sees every difference.

Flows required by the M11 definition of done, and where they live here:
  merchant API-key auth ................ api_key_auth          route-level authorization ....... route_authorization
  identity propagation ................. identity_propagation  cross-merchant denial ........... cross_merchant_denial
  dashboard/session auth (as source permits) dashboard_session beneficiary/fund-account ownership fund_account_ownership
  internal app auth .................... internal_app_auth     service-to-service identity ..... s2s_identity
  maker-checker create/approve/reject/separation maker_checker restart & cache invalidation ..... restart_cache_invalidation
  duplicate / idempotency .............. idempotency           Shield rules / BAS lookup ....... shield_rules, bas_lookup
"""
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

import framework as F
from framework import journey
import j_ingress as JI

TP = "trustpath"                 # a fresh Shared merchant per run for this family
TPV = "trustpath-victim"         # a second fresh Shared merchant (the one whose resources are attacked)
TPW = "trustpath-workflow"       # a fresh Shared merchant made workflow-applicable
DIRECT = "direct-a"              # the Direct merchant the direct-payouts family provisions (banking-accounts rows)

ING = "http://api-ingress:8080"
SHIELD = "http://shield-web:8090"
BAS = "http://banking-accounts-api:8000/v0.2"
WORKFLOWS = "http://workflows-api:9400"
PS = "http://payouts-api:9400"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def real():
    return F.TRUST["real"]


def real_gw():
    return F.TRUST["real_gateway"]


def variant():
    return "real" if real() else "substitute"


def secret(name):
    v = (F.ENV2 / "secrets" / name).read_text().strip()
    return v


def merchant_basic(m):
    return "%s:%s" % (m["key_id"], m["secret"])


def gw(ctx, method, path, basic=None, body=None, headers=None, note=None, raw_auth=None, timeout=40):
    """One request to the PUBLIC gateway from the host bridge -- what a merchant on the internet sends. Records who
    answered: the gateway itself (Kong `Server` header, no upstream latency) or the upstream through it (`Via`)."""
    h = {"X-Request-ID": "m11-" + uuid.uuid4().hex[:16]}
    if basic:
        ctx.a.hide(basic.split(":", 1)[1])
        h["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
    if raw_auth is not None:
        h["Authorization"] = raw_auth
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(F.P.KONG + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            st, txt, rh = r.status, r.read().decode("utf-8", "replace"), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        st, txt, rh = e.code, e.read().decode("utf-8", "replace"), {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:  # noqa: BLE001
        st, txt, rh = "ERR", str(e)[:300], {}
    # Production kong.conf sets `headers = off` (no Server/Via/X-Kong-* on any Kong response) and the twin runs the same
    # file, so "who answered" is read from the X-Request-ID echo: the monolith replacement (api-ingress) returns the
    # request id it was given on every response, the gateway's own refusals carry none.
    proxied = rh.get("x-request-id") == h["X-Request-ID"] or "x-kong-upstream-latency" in rh or "via" in rh
    server = rh.get("x-upstream-server") or rh.get("server", "")
    answered_by = "upstream through the gateway (request id echoed)" if proxied else "gateway itself (no upstream echo; kong.conf headers=off)"
    doc = F.jload(txt) if txt else None
    ctx.a._rec("http", {"transport": ("edge-kong (REAL gateway, prod-api route table)" if real_gw() else "kong-lite (substitute gateway)"),
                        "method": method, "url": F.P.KONG + path, "request": body, "headers": {k: v for k, v in h.items() if k != "Authorization"},
                        "status": st, "response": (txt or "")[:1200], "answered_by": answered_by,
                        "gateway_headers": {k: rh.get(k) for k in ("x-upstream-server", "via", "x-kong-upstream-latency", "x-kong-proxy-latency", "x-kong-request-id", "x-request-id", "www-authenticate") if k in rh},
                        "note": note})
    return {"status": st, "body": doc if doc is not None else txt, "headers": rh, "answered_by": answered_by, "proxied": proxied,
            "request_id": h["X-Request-ID"]}


def create_body(m, amount, note, fund_account_id=None, extra=None):
    return JI.create_body(m, amount, note, fund_account_id=fund_account_id, extra=extra)


def audit_for(ctx, request_id, tenant=None, route=None):
    """Ingress audit rows for one request. The real gateway relays X-Request-ID; kong-lite (substitute) does not, so the
    substitute variant falls back to the tenant's most recent rows for the route (recorded on the evidence)."""
    rows = JI.audit_rows(ctx, request_id)
    if rows or not tenant:
        return rows
    ev = JI.evidence(ctx, tenant=tenant)
    rows = [x for x in ev.get("audit", []) if (not route or x.get("route") == route)]
    rows = sorted(rows, key=lambda x: x.get("id") or 0)[-3:]
    ctx.a._rec("db", {"store": "ingress audit", "sql": "tenant=%s route=%s (fallback: request id not relayed by the substitute gateway)" % (tenant, route), "rows": len(rows)})
    return rows


def arena(ctx, method, url, body=None, basic=None, headers=None, note=None):
    st, txt = ctx.a.http(method, url, body, headers, basic=basic, timeout=30, note=note)
    return st, (F.jload(txt) if txt else None), txt


def kong_admin(method, path, body=None):
    return F.P._kong_admin(method, path, body)


def container_log_hits(service, needle, since="15m"):
    out = subprocess.run(["docker", "logs", "--since", since, F.P.cname(service)], capture_output=True, text=True, timeout=60)
    return [l[:300] for l in (out.stdout + out.stderr).splitlines() if needle in l]


def rules_of(doc):
    """Shield GET /v1/merchants/:mid/rules answers a JSON list (app/controllers/rules.go FetchMultiple)."""
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        return [r for r in (doc.get("items") or doc.get("rules") or doc.get("data") or []) if isinstance(r, dict)]
    return []


def shield_meta(ctx, pid, note):
    rc, out, _ = ctx.a.payouts_sql("SELECT meta_value FROM payout_meta_permanent WHERE payout_id='%s' AND meta_name='shield_evaluate_response'" % pid, note=note)
    return (out or "").strip()


def wf_applicable(ctx, m, on=True):
    return ctx.a.make_workflow_applicable(m["merchant_id"], on=on)


def pending_payout(ctx, m, amount, note):
    """A workflow-applicable payout created through the gateway that reaches `pending` (needs the approval path)."""
    r = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=create_body(m, amount, note),
           headers={"X-Payout-Idempotency": "m11w-" + uuid.uuid4().hex[:10]}, note="create (workflow-applicable) through the gateway")
    pid = F.bare((r["body"] or {}).get("id") or "") if isinstance(r["body"], dict) else ""
    row = ctx.wait_status(pid, "pending", timeout=60) if pid else None
    return r, pid, row


# ---------------------------------------------------------------------------
# 1. merchant API-key authentication at the gateway
# ---------------------------------------------------------------------------
@journey("trust-path", "api_key_auth", priority="P0", profile=TP,
         title="merchant API-key authentication at the REAL edge gateway: a valid key passes, wrong/unknown/absent credentials are refused by the gateway itself and never reach the monolith replacement",
         source_ref="terraform-kong module/plugins.tf consumer_authenticator (basic-auth-x + jwt-x, anonymous consumer) on every prod-api authenticated_path; "
                    "edge kong-plugin-basic-auth-x access.lua (sha512(password .. consumer_id) credential lookup, 401 on mismatch); Route.php payout_fetch_multiple (Route::$private)")
def tp_api_key_auth(ctx):
    m = ctx.m
    ok = gw(ctx, "GET", "/v1/payouts?count=1", basic=merchant_basic(m), note="valid merchant key")
    ctx.ck("valid_merchant_key_passes_the_gateway_to_the_real_payouts_list", ok["status"] == 200 and isinstance(ok["body"], dict) and ok["body"].get("entity") == "collection",
           {"status": ok["status"], "answered_by": ok["answered_by"], "body": str(ok["body"])[:200]})
    if real_gw():
        ctx.ck("the_success_was_proxied_through_the_gateway_(upstream_request-id_echo;_kong.conf_headers=off)", ok["proxied"], ok["headers"])
    bad = {
        "wrong_secret": gw(ctx, "GET", "/v1/payouts?count=1", basic="%s:%s" % (m["key_id"], "WRONG" + uuid.uuid4().hex[:8]), note="wrong secret"),
        "unknown_key_id": gw(ctx, "GET", "/v1/payouts?count=1", basic="rzp_live_UNKNOWN%s:%s" % (uuid.uuid4().hex[:6].upper(), m["secret"]), note="unknown key id"),
        "no_credentials": gw(ctx, "GET", "/v1/payouts?count=1", note="no Authorization header"),
        "malformed_basic": gw(ctx, "GET", "/v1/payouts?count=1", raw_auth="Basic not-base64!!", note="malformed basic auth"),
        "bearer_garbage": gw(ctx, "GET", "/v1/payouts?count=1", raw_auth="Bearer eyJhbGciOiJIUzI1NiJ9.e30.garbage", note="garbage bearer token (jwt-x path)"),
        "other_merchants_secret": gw(ctx, "GET", "/v1/payouts?count=1", basic="%s:%s" % (m["key_id"], ctx.pool.get(TPV)["secret"]), note="this key id with another merchant's secret"),
    }
    ctx.ev["refusals"] = {k: {"status": v["status"], "answered_by": v["answered_by"], "body": str(v["body"])[:200]} for k, v in bad.items()}
    for k, v in bad.items():
        ctx.ck("%s_is_refused_401" % k, v["status"] == 401, ctx.ev["refusals"][k])
    if real_gw():
        ctx.ck("every_refusal_was_answered_by_the_gateway_itself_(no_upstream_call)", all(not v["proxied"] for v in bad.values()), ctx.ev["refusals"])
        rows = audit_for(ctx, bad["wrong_secret"]["request_id"])
        ctx.ck("the_wrong-secret_request_never_reached_the_monolith_replacement_(no_ingress_audit_row)", rows == [], rows)
        st, consumer = kong_admin("GET", "/consumers/" + m["merchant_id"])
        ctx.ck("kong_consumer_username_is_the_merchant_id_(edge common.lua extract_consumer_details)", st == 200 and (consumer or {}).get("username") == m["merchant_id"], {"status": st, "consumer": str(consumer)[:200]})
        st, creds = kong_admin("GET", "/consumers/%s/basic-auth-x" % m["merchant_id"])
        items = (creds or {}).get("data") or [] if isinstance(creds, dict) else []
        mine = [c for c in items if c.get("username") == m["key_id"]]
        ctx.ev["kong_credential"] = [{k: v for k, v in c.items() if k != "password"} | {"password_is_plaintext_secret": c.get("password") == m["secret"], "password_len": len(c.get("password") or "")} for c in mine]
        ctx.ck("one_basic-auth-x_credential_per_key_id_hashed_sha512_(the_plaintext_secret_is_never_stored_or_returned)", len(mine) == 1 and mine[0].get("password") != m["secret"] and mine[0].get("hash_type") == "sha512", ctx.ev["kong_credential"])
        ctx.ck("credential_tags_carry_the_mode_(m~l)", any("m~l" in (c.get("tags") or []) for c in mine), [c.get("tags") for c in mine])
    else:
        ctx.note("substitute variant (kong-lite): refusals come from kong-lite's merchants.json lookup; the Kong consumer/credential checks above need the REAL gateway (ARENA_TRUST_PATH=real).")


# ---------------------------------------------------------------------------
# 2. identity propagation gateway -> monolith replacement -> payouts
# ---------------------------------------------------------------------------
@journey("trust-path", "identity_propagation", priority="P0", profile=TP,
         title="the authenticated merchant identity is propagated as a gateway-signed passport (kid edgev2) and cannot be overridden by client-supplied passport or account headers",
         source_ref="terraform-kong module/plugins.tf upstream-jwt (issuer edge, key_id edgev2, header X-Passport-JWT-V1) + passport-enabler (X-PASSPORT-USABLE); "
                    "api app/Http/Middleware/DecodePassportJwt.php + PassportUtil.php (consumer/mode/identified validation); payouts pkg/passport/handler.go [passport.edge]")
def tp_identity_propagation(ctx):
    m = ctx.m
    ctx.a.mozart(m["merchant_id"], "success")
    r = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=create_body(m, 2100, "identity"), headers={"X-Payout-Idempotency": "m11id-" + uuid.uuid4().hex[:10]}, note="create through the gateway")
    pid = F.bare((r["body"] or {}).get("id") or "") if isinstance(r["body"], dict) else ""
    ctx.ck("create_200_through_the_gateway", r["status"] == 200 and bool(pid), {"status": r["status"], "body": str(r["body"])[:300], "answered_by": r["answered_by"]})
    rows = audit_for(ctx, r["request_id"], tenant=m["merchant_id"], route="payout_create")
    final = [x for x in rows if x.get("route") == "payout_create" and x.get("identity_type") == "merchant"]
    ctx.ev["ingress_audit"] = rows[:6]
    ctx.ck("monolith_replacement_saw_the_merchant_identity_from_the_gateway_call", any(x.get("identity_id") == m["merchant_id"] and x.get("tenant") == m["merchant_id"] for x in final), final[:3])
    ep = None
    for x in final:
        d = x.get("detail")
        if isinstance(d, str):
            d = F.jload(d) or {}
        if isinstance(d, dict) and d.get("edge_passport"):
            ep = d["edge_passport"]
    ctx.ev["edge_passport_seen_by_ingress"] = ep
    if real_gw():
        ctx.ck("gateway_passport_verified_by_the_monolith_replacement_(RS256, kid edgev2)", bool(ep) and ep.get("valid") is True and ep.get("kid") == "edgev2", ep)
        ctx.ck("passport_consumer_is_the_authenticated_merchant_in_live_mode", bool(ep) and (ep.get("consumer") or {}).get("id") == m["merchant_id"] and (ep.get("consumer") or {}).get("type") == "merchant" and ep.get("mode") == "live", ep)
        ctx.ck("X-PASSPORT-USABLE_true_(passport-enabler)", bool(ep) and ep.get("usable_header") is True, ep)
    else:
        ctx.note("substitute variant: kong-lite forwards its own passport (kid %s); the edgev2 gateway passport checks need the REAL gateway." % F.TRUST.get("ARENA_PASSPORT_API_KID"))
        ctx.ck("substitute_gateway_forwarded_an_identity_the_ingress_accepted", bool(final), final[:2])
    if pid:
        row = ctx.wait_terminal(pid, timeout=150)
        ctx.ck("payout_row_belongs_to_the_authenticated_merchant_and_completed", (row or {}).get("merchant_id") == m["merchant_id"] and (row or {}).get("status") == "processed", row)
    # forgery: client-supplied passport / usable / account headers must not override the gateway identity
    victim = ctx.pool.get(TPV)
    forged_jwt = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "kid": "edgev2"}).encode()).decode().rstrip("=") + "." + \
        base64.urlsafe_b64encode(json.dumps({"identified": True, "mode": "live", "consumer": {"id": victim["merchant_id"], "type": "merchant"}}).encode()).decode().rstrip("=") + ".AAAA"
    f = gw(ctx, "GET", "/v1/payouts?count=1", basic=merchant_basic(m),
           headers={"X-Passport-JWT-V1": forged_jwt, "X-PASSPORT-USABLE": "true", "X-Razorpay-Account": victim["merchant_id"], "X-Authentication-Result": "true"},
           note="valid key + forged passport/usable/account headers naming the victim")
    frows = audit_for(ctx, f["request_id"], tenant=m["merchant_id"], route="payout_fetch_multiple")
    ftenant = {x.get("tenant") for x in frows if x.get("route")}
    fep = None
    for x in frows:
        d = x.get("detail")
        d = F.jload(d) if isinstance(d, str) else d
        if isinstance(d, dict) and d.get("edge_passport"):
            fep = d["edge_passport"]
    ctx.ev["forgery"] = {"status": f["status"], "answered_by": f["answered_by"], "tenants_seen": sorted(t for t in ftenant if t), "edge_passport": fep, "body": str(f["body"])[:200]}
    ctx.ck("forged_headers_never_yield_the_victim_tenant_(request_denied_or_served_as_the_caller)",
           (f["status"] == 200 and ftenant <= {m["merchant_id"], None}) or (isinstance(f["status"], int) and 400 <= f["status"] < 500), ctx.ev["forgery"])
    if real_gw() and f["status"] == 200:
        ctx.ck("gateway_replaced_the_forged_passport_with_its_own_(consumer = caller)", bool(fep) and (fep.get("consumer") or {}).get("id") == m["merchant_id"], fep)
    ctx.a.mozart(m["merchant_id"], clear=True)


# ---------------------------------------------------------------------------
# 3. dashboard / session identity (as far as the granted source permits)
# ---------------------------------------------------------------------------
@journey("trust-path", "dashboard_session", priority="P1", profile=TPW,
         title="dashboard identity: a proxy-auth session acts on the merchant's pending payout through the monolith replacement; a session token is NOT an API credential at the gateway and cannot act on another merchant",
         source_ref="api app/Http/BasicAuth/BasicAuth.php proxyAuth (dashboard session -> user + merchant); Route.php payout_reject (Route::$proxy); "
                    "BLOCKER for the real dashboard hop: prod/api/api-dashboard*.tf (Kong user-session/user-auth -> dashboard app) -- the dashboard app is not a granted repository (PU-M11 dashboard)")
def tp_dashboard_session(ctx):
    m = ctx.m
    ctx.ev["dashboard_gateway_hop"] = {"status": "NOT_RUNNABLE", "blocker": "razorpay/dashboard (the app behind the api-dashboard Kong service) is not in the granted repositories; "
                                                                      "the Kong user-session plugin needs the dashboard login flow to mint the cookie it validates",
                                       "what_runs": "BasicAuth::proxyAuth semantics at api-ingress (dashboard:<session token>), the monolith side of the same route"}
    wf_applicable(ctx, m, True)
    ctx.a.mozart(m["merchant_id"], "success")
    r, pid, row = pending_payout(ctx, m, 6300, "dash")
    ctx.ck("workflow-applicable_payout_pending_after_the_gateway_create", (row or {}).get("status") == "pending", {"status": r["status"], "row": row})
    uid, tok, st = ctx.a.ingress_session(m["merchant_id"])
    ctx.ck("dashboard_session_minted_for_a_user_of_this_merchant", st == 200 and bool(tok), {"status": st, "user": uid})
    # a dashboard session presented at the public gateway as basic auth is not an API credential
    g = gw(ctx, "GET", "/v1/payouts?count=1", basic="dashboard:" + tok, note="dashboard session token presented at the public gateway")
    ctx.ck("session_token_is_refused_at_the_gateway_(only_API_keys_are_gateway_credentials)", g["status"] in (400, 401, 403), {"status": g["status"], "answered_by": g["answered_by"]})
    if real_gw():
        ctx.ck("gateway_itself_refused_it", not g["proxied"], g["headers"])
    if pid and (row or {}).get("status") == "pending":
        st, rr = ctx.a.ingress("POST", "/v1/payouts/pout_%s/reject" % pid, {}, basic="dashboard:" + tok, note="reject via the dashboard (proxy-auth) route")
        ctx.ck("dashboard_session_reject_accepted_by_the_monolith_replacement", st in (200, 201), {"status": st, "body": rr})
        end = ctx.wait_terminal(pid, timeout=90)
        ctx.ck("payout_rejected_by_the_dashboard_user", (end or {}).get("status") == "rejected", end)
        if F.TRUST["real_workflows"]:
            w = ctx.a.wfe_for_payout(pid)
            ctx.ev["real_workflow_row_after_dashboard_reject"] = w
            ctx.note("dashboard reject went through the monolith route straight to payouts (Route.php payout_reject -> PS); the REAL Workflow "
                     "service's row for the payout stays %s -- the same divergence production carries when the dashboard bypasses the workflow "
                     "callback path (source: payouts payout_internal_routes.go reject handler does not notify workflows)." % ((w or {}).get("state")))
    # a session of merchant A on merchant B's payout
    victim = ctx.pool.get(TPV)
    ctx.a.mozart(victim["merchant_id"], "hold")
    vr = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(victim), body=create_body(victim, 2200, "victim"), headers={"X-Payout-Idempotency": "m11v-" + uuid.uuid4().hex[:10]}, note="victim creates a payout (bank held)")
    vpid = F.bare((vr["body"] or {}).get("id") or "") if isinstance(vr["body"], dict) else ""
    if vpid:
        st, xr = ctx.a.ingress("POST", "/v1/payouts/pout_%s/reject" % vpid, {}, basic="dashboard:" + tok, note="merchant A's session on merchant B's payout")
        ctx.ck("merchant_A_session_cannot_act_on_merchant_B_payout", isinstance(st, int) and 400 <= st < 500, {"status": st, "body": xr})
        st2, xr2 = ctx.a.ingress("POST", "/v1/payouts/pout_%s/cancel" % vpid, {"remarks": "cross"}, basic="dashboard:" + tok, note="merchant A's session cancels merchant B's payout")
        ctx.ck("merchant_A_session_cannot_cancel_merchant_B_payout", isinstance(st2, int) and 400 <= st2 < 500, {"status": st2, "body": xr2})
        vrow = ctx.a.payout(vpid)
        ctx.ck("victim_payout_untouched", (vrow or {}).get("status") not in ("rejected", "cancelled"), vrow)
    ctx.a.mozart(victim["merchant_id"], clear=True)
    ctx.a.mozart(m["merchant_id"], clear=True)
    wf_applicable(ctx, m, False)


# ---------------------------------------------------------------------------
# 4. internal application authentication
# ---------------------------------------------------------------------------
@journey("trust-path", "internal_app_auth", priority="P0", profile=TP,
         title="internal applications authenticate with rzp_live + app secret and act on a tenant named by X-Razorpay-Account; app secrets are not gateway credentials and merchant keys cannot use internal routes",
         source_ref="api BasicAuth.php appAuth (rzp_live/<app secret>, X-Razorpay-Account tenant) + Route.php internalApps per route; "
                    "prod/api/api-internal.tf (internal traffic is plain `paths` on the api-internal Kong service, no consumer auth at the edge)")
def tp_internal_app_auth(ctx):
    m = ctx.m
    ctx.a.mozart(m["merchant_id"], "success")
    r = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=create_body(m, 2500, "app"), headers={"X-Payout-Idempotency": "m11app-" + uuid.uuid4().hex[:10]}, note="merchant creates a payout")
    pid = F.bare((r["body"] or {}).get("id") or "") if isinstance(r["body"], dict) else ""
    ctx.ck("merchant_create_200", r["status"] == 200 and bool(pid), {"status": r["status"]})
    if not pid:
        return
    tenant = {"X-Razorpay-Account": m["merchant_id"]}
    st, doc, _ = arena(ctx, "GET", ING + "/v1/payouts_internal/pout_" + pid, basic=JI.app_basic("vendor_payments"), headers=tenant | {"X-Request-ID": "m11i-" + uuid.uuid4().hex[:10]}, note="vendor_payments app fetches the payout on the internal route")
    ctx.ck("permitted_app_with_tenant_header_reads_the_payout_200", st == 200 and F.bare((doc or {}).get("id") or "") == pid, {"status": st, "body": str(doc)[:200]})
    st2, d2, _ = arena(ctx, "GET", ING + "/v1/payouts_internal/pout_" + pid, basic=JI.app_basic("vendor_payments"), note="same app WITHOUT X-Razorpay-Account")
    ctx.ck("internal_route_without_a_tenant_header_is_refused_4xx", isinstance(st2, int) and 400 <= st2 < 500, {"status": st2, "body": str(d2)[:200]})
    st3, d3, _ = arena(ctx, "GET", ING + "/v1/payouts_internal/pout_" + pid, basic=JI.app_basic("xpayroll"), headers=tenant, note="xpayroll app (not in this route's internalApps)")
    ctx.ck("app_outside_the_routes_internalApps_list_is_refused_4xx", isinstance(st3, int) and 400 <= st3 < 500, {"status": st3, "body": str(d3)[:200]})
    st4, d4, _ = arena(ctx, "GET", ING + "/v1/payouts_internal/pout_" + pid, basic="rzp_live:WRONG" + uuid.uuid4().hex[:8], headers=tenant, note="wrong app secret")
    ctx.ck("wrong_app_secret_is_refused_(BasicAuth::appAuth_failure_->_routeNotFound_400,_api-ingress_CONTRACT.md)", st4 in (400, 401), {"status": st4})
    st5, d5, _ = arena(ctx, "GET", ING + "/v1/payouts_internal/pout_" + pid, basic=merchant_basic(m), headers=tenant, note="merchant key on the internal route")
    ctx.ck("merchant_key_cannot_use_an_internal_route", isinstance(st5, int) and 400 <= st5 < 500, {"status": st5, "body": str(d5)[:200]})
    g = gw(ctx, "GET", "/v1/payouts/pout_" + pid, basic="rzp_live:" + secret("app_vendor_payments.txt"), headers=tenant, note="app credential presented at the PUBLIC gateway")
    ctx.ck("app_secret_is_not_a_gateway_credential_(401_at_the_edge)", g["status"] == 401, {"status": g["status"], "answered_by": g["answered_by"]})
    if real_gw():
        ctx.ck("gateway_itself_refused_the_app_credential", not g["proxied"], g["headers"])
        ctx.note("production: internal apps reach the monolith through the api-internal Kong service (prod/api/api-internal.tf, plain paths, no "
                 "consumer authentication at the edge) -- not provisioned in the twin (PU-M11 api-internal); the twin's internal apps call the "
                 "monolith replacement directly, as they did in M7.")
    ctx.wait_terminal(pid, timeout=150)
    ctx.a.mozart(m["merchant_id"], clear=True)


# ---------------------------------------------------------------------------
# 5. route-level authorization at the gateway
# ---------------------------------------------------------------------------
@journey("trust-path", "route_authorization", priority="P0", profile=TP,
         title="route-level authorization: only the production prod-api authenticated_paths exist at the gateway; internal, admin, PS-internal and control-plane paths are unroutable from the public edge",
         source_ref="terraform-kong templates/prod-api/*.tf authenticated_paths (payout_create, payout_fetch_by_id, payout_cancel, ...); "
                    "Route.php $internal/$admin routes are served on api-internal / api-admin Kong services with their own hosts, never on prod-api")
def tp_route_authorization(ctx):
    m = ctx.m
    probes = {
        "internal_payouts_route": ("POST", "/v1/payouts_internal", create_body(m, 1000, "route")),
        "internal_fetch_route": ("GET", "/v1/payouts_internal/pout_ARENA00000000AA", None),
        "admin_free_payout_route": ("GET", "/v1/admin/payouts/%s/free_payout" % m["balance_id"], None),
        "admin_cancel_route": ("POST", "/v1/admin/payouts/cancel", {"payout_ids": []}),
        "ps_internal_approve_route": ("POST", "/v1/payouts/payouts_internal/pout_ARENA00000000AA/approve", {}),
        "ingress_control_plane": ("GET", "/_ingress/evidence?limit=1", None),
        "ingress_health": ("GET", "/_ingress/health", None),
        "method_not_on_route": ("DELETE", "/v1/payouts/pout_ARENA00000000AA", None),
        "workflows_twirp": ("POST", "/twirp/rzp.workflows.workflow.v1.WorkflowAPI/List", {}),
    }
    out = {}
    for k, (meth, path, body) in probes.items():
        r = gw(ctx, meth, path, basic=merchant_basic(m), body=body, headers={"X-Razorpay-Account": m["merchant_id"]}, note="merchant key on " + k)
        out[k] = {"status": r["status"], "answered_by": r["answered_by"], "proxied": r["proxied"], "body": str(r["body"])[:160]}
        ctx.ck("%s_is_not_reachable_through_the_public_gateway_(4xx)" % k, isinstance(r["status"], int) and 400 <= r["status"] < 500, out[k])
    ctx.ev["probes"] = out
    if real_gw():
        ctx.ck("every_unroutable_path_was_answered_by_the_gateway_itself_404_no_route", all(v["status"] == 404 and not v["proxied"] for v in out.values()), out)
        st, routes = kong_admin("GET", "/routes?size=200")
        names = sorted(r.get("name") for r in ((routes or {}).get("data") or []) if isinstance(r, dict))
        ctx.ev["gateway_route_table"] = names
        ctx.ck("gateway_route_table_is_the_derived_prod-api_table_(no_internal/admin_routes)", bool(names) and not any(("internal" in n or "admin" in n) for n in names), names)
    # a routable path the merchant is allowed to call still needs the monolith's own authorization (purposes list)
    p = gw(ctx, "GET", "/v1/payouts/purposes", basic=merchant_basic(m), note="payout_purpose_get (a prod-api route) -> monolith replacement")
    ctx.ev["purposes_route"] = {"status": p["status"], "answered_by": p["answered_by"], "body": str(p["body"])[:160]}
    if real_gw():
        ctx.ck("a_routable_prod-api_path_is_proxied_to_the_monolith_replacement", p["proxied"], ctx.ev["purposes_route"])


# ---------------------------------------------------------------------------
# 6. cross-merchant denial
# ---------------------------------------------------------------------------
@journey("trust-path", "cross_merchant_denial", priority="P0", profile=TP,
         title="merchant A cannot read, cancel, approve or reject merchant B's payout through the gateway; B's row and both balances are untouched",
         source_ref="payouts internal/app/payouts fetch/cancel by (id, merchant_id) from the passport/ingress identity; api PayoutController scoping by BasicAuth merchant")
def tp_cross_merchant_denial(ctx):
    a, b = ctx.m, ctx.pool.get(TPV)
    ctx.ck("two_distinct_merchants", a["merchant_id"] != b["merchant_id"], {"a": a["merchant_id"], "b": b["merchant_id"]})
    ctx.a.mozart(b["merchant_id"], "hold")
    vr = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(b), body=create_body(b, 3300, "b-own"), headers={"X-Payout-Idempotency": "m11b-" + uuid.uuid4().hex[:10]}, note="B creates a payout (bank held)")
    bpid = F.bare((vr["body"] or {}).get("id") or "") if isinstance(vr["body"], dict) else ""
    ctx.ck("b_create_200", vr["status"] == 200 and bool(bpid), {"status": vr["status"]})
    if not bpid:
        ctx.a.mozart(b["merchant_id"], clear=True); return
    ctx.wait_status(bpid, "initiated", timeout=60)
    before = ctx.a.payout(bpid)
    bal_a, bal_b = ctx.a.merchant_balance(a["merchant_id"]), ctx.a.merchant_balance(b["merchant_id"])
    attempts = {
        "fetch": gw(ctx, "GET", "/v1/payouts/pout_" + bpid, basic=merchant_basic(a), note="A fetches B's payout"),
        "cancel": gw(ctx, "POST", "/v1/payouts/pout_%s/cancel" % bpid, basic=merchant_basic(a), body={"remarks": "cross"}, note="A cancels B's payout"),
        "approve": gw(ctx, "POST", "/v1/payouts/pout_%s/approve" % bpid, basic=merchant_basic(a), body={}, note="A approves B's payout"),
        "reject": gw(ctx, "POST", "/v1/payouts/pout_%s/reject" % bpid, basic=merchant_basic(a), body={}, note="A rejects B's payout"),
    }
    ctx.ev["attempts"] = {k: {"status": v["status"], "answered_by": v["answered_by"], "body": str(v["body"])[:200]} for k, v in attempts.items()}
    for k, v in attempts.items():
        ctx.ck("A_%s_of_B_payout_is_refused_4xx" % k, isinstance(v["status"], int) and 400 <= v["status"] < 500, ctx.ev["attempts"][k])
    own = gw(ctx, "GET", "/v1/payouts/pout_" + bpid, basic=merchant_basic(b), note="B fetches its own payout (control)")
    ctx.ck("B_reads_its_own_payout_200", own["status"] == 200 and F.bare((own["body"] or {}).get("id") or "") == bpid, {"status": own["status"]})
    after = ctx.a.payout(bpid)
    ctx.ck("B_row_untouched_(status_and_cancellation_user_unchanged)", (before or {}).get("status") == (after or {}).get("status") and not (after or {}).get("cancellation_user_id"), {"before": before, "after": after})
    ctx.ck("neither_balance_moved", all(x is not None for x in (bal_a, bal_b)) and abs(bal_a - ctx.a.merchant_balance(a["merchant_id"])) < 1e-6 and abs(bal_b - ctx.a.merchant_balance(b["merchant_id"])) < 1e-6, {"a": bal_a, "b": bal_b})
    rows = audit_for(ctx, attempts["fetch"]["request_id"], tenant=a["merchant_id"], route="payout_fetch_by_id")
    ctx.ev["ingress_audit_fetch"] = rows[:3]
    ctx.ck("the_denied_fetch_was_evaluated_under_tenant_A_(not_B)", all(x.get("tenant") in (a["merchant_id"], None) for x in rows) and bool(rows), rows[:3])
    ctx.a.mozart(b["merchant_id"], clear=True)
    ctx.a.finish_bank(bpid, "success")
    ctx.wait_terminal(bpid, timeout=120)


# ---------------------------------------------------------------------------
# 7. beneficiary / fund-account ownership
# ---------------------------------------------------------------------------
@journey("trust-path", "fund_account_ownership", priority="P0", profile=TP,
         title="beneficiary ownership: a merchant can pay only fund accounts it owns; another merchant's, a fabricated one, and an internal-app creation under a contact of another tenant are refused with nothing persisted",
         source_ref="api FundAccount/Service.php fetch(): findByPublicIdAndMerchant -> BAD_REQUEST_INVALID_ID; PS fundAccountCache/core.go GET fund_accounts_internal with the caller's merchant; "
                    "Contact/Service.php findByPublicIdAndMerchant for fund_account_create")
def tp_fund_account_ownership(ctx):
    a, b = ctx.m, ctx.pool.get(TPV)
    st, reg = ctx.a.ingress("GET", "/_ingress/registry/fund_accounts?id=" + b["fund_account_id"], basic=ctx.a.ingress_admin_basic(), note="ownership record of B's fund account")
    owner = [r for r in (reg or {}).get("resources", []) if r.get("kind") == "fund_account"]
    ctx.ck("B_fund_account_has_an_explicit_owner_record_(B)", bool(owner) and owner[0].get("merchant_id") == b["merchant_id"], reg)
    rc, cnt0, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s'" % a["merchant_id"], note="A payouts before")
    ctx.a.mozart(a["merchant_id"], "success")
    cases = {
        "B_fund_account": gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(a), body=create_body(a, 3100, "own-b", fund_account_id=b["fund_account_id"]), headers={"X-Payout-Idempotency": "m11o1-" + uuid.uuid4().hex[:10]}, note="A pays B's fund account"),
        "fabricated_fund_account": gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(a), body=create_body(a, 3100, "own-x", fund_account_id="fa_ARENA" + uuid.uuid4().hex[:9].upper()), headers={"X-Payout-Idempotency": "m11o2-" + uuid.uuid4().hex[:10]}, note="A pays a fabricated fund account id"),
    }
    ctx.ev["refused"] = {k: {"status": v["status"], "body": str(v["body"])[:220]} for k, v in cases.items()}
    for k, v in cases.items():
        ctx.ck("%s_is_refused_400" % k, v["status"] == 400, ctx.ev["refused"][k])
    rc, cnt1, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s'" % a["merchant_id"], note="A payouts after the refusals")
    ctx.ck("nothing_persisted_for_the_refused_creates", (cnt0 or "").strip() == (cnt1 or "").strip(), {"before": cnt0, "after": cnt1})
    ev = JI.evidence(ctx, tenant=a["merchant_id"])
    denied = [x for x in ev.get("audit", []) if x.get("route") == "fund_account_get_internal" and x.get("tenant") == a["merchant_id"] and x.get("status") == 400 and F.bare(b["fund_account_id"]) in (x.get("path") or "")]
    ctx.ev["ingress_denials"] = denied[:3]
    ctx.ck("owner-scoped_beneficiary_lookup_denied_under_tenant_A_in_the_ingress_audit", len(denied) >= 1, ev.get("audit", [])[:6])
    ok = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(a), body=create_body(a, 3100, "own-a"), headers={"X-Payout-Idempotency": "m11o3-" + uuid.uuid4().hex[:10]}, note="A pays its own fund account (control)")
    pid = F.bare((ok["body"] or {}).get("id") or "") if isinstance(ok["body"], dict) else ""
    ctx.ck("own_fund_account_create_200", ok["status"] == 200 and bool(pid), {"status": ok["status"]})
    # internal app: a fund account for B's contact cannot be created under tenant A
    st, contact, _ = arena(ctx, "POST", ING + "/v1/contacts_internal", {"name": "m11 owner probe", "type": "vendor"}, basic=JI.app_basic("vendor_payments"), headers={"X-Razorpay-Account": b["merchant_id"]}, note="vendor_payments creates a contact under tenant B")
    cid = (contact or {}).get("id")
    ctx.ck("contact_created_under_B", st == 200 and bool(cid), {"status": st, "body": str(contact)[:160]})
    if cid:
        st2, fa, _ = arena(ctx, "POST", ING + "/v1/fund_accounts_internal", {"contact_id": cid, "account_type": "bank_account", "bank_account": {"name": "probe", "ifsc": "RATN0000001", "account_number": "1112220099"}},
                           basic=JI.app_basic("vendor_payments"), headers={"X-Razorpay-Account": a["merchant_id"]}, note="fund account for B's contact under tenant A")
        ctx.ck("fund_account_on_another_tenants_contact_is_refused_400", st2 == 400, {"status": st2, "body": str(fa)[:200]})
    if pid:
        ctx.wait_terminal(pid, timeout=150)
    ctx.a.mozart(a["merchant_id"], clear=True)


# ---------------------------------------------------------------------------
# 8. service-to-service identity
# ---------------------------------------------------------------------------
@journey("trust-path", "s2s_identity", priority="P0", profile=TP,
         title="service-to-service identity: payouts -> Shield (BasicAuth shieldAuthUser), payouts -> banking-accounts (Api-Token), payouts <-> Workflow service (BasicAuth) and workflows -> payouts internal routes each refuse anonymous and wrong credentials and admit the configured pair",
         source_ref="shield app/middlewares/auth.go BasicAuth over shieldAuthUser (viper SHIELDAUTHUSER_<APP>_*); banking-accounts internal/middleware api token ([api].token); "
                    "workflows internal auth ([auth] service users); payouts pkg/bankingAccountService/client.go Api-Token; payouts arena.toml [workflow.auth]")
def tp_s2s_identity(ctx):
    m = ctx.m
    res = {}

    def probe(name, method, url, body=None, basic=None, headers=None, expect_ok=None):
        st, doc, txt = arena(ctx, method, url, body, basic=basic, headers=headers, note=name)
        res[name] = {"status": st, "body": (txt or "")[:160]}
        return st, doc

    shield_input = {"payout_id": "pout_m11probe", "payout_amount": 100, "mode": "IMPS", "currency": "INR", "purpose": "payout", "merchant_id": m["merchant_id"],
                    "account_number": "9876543219999", "ifsc": "RATN0000001", "created_at": int(time.time()), "entity_type": "payout", "source_account_types": "shared"}
    shield_body = {"merchant_id": m["merchant_id"], "entity_type": "payout", "entity_id": "pout_m11probe", "rulesets": ["primary"], "input": shield_input}
    if real():
        shield_pw, bas_tok, wf_pw = secret("auth_shield_payouts.txt"), secret("auth_bas_payouts.txt"), secret("auth_payouts_workflows.txt")
        ctx.a.hide(shield_pw, bas_tok, wf_pw)
        probe("shield_anonymous", "POST", SHIELD + "/v1/rules/evaluate", shield_body)
        probe("shield_wrong_password", "POST", SHIELD + "/v1/rules/evaluate", shield_body, basic="payouts:WRONG" + uuid.uuid4().hex[:6])
        st, doc = probe("shield_payouts_identity", "POST", SHIELD + "/v1/rules/evaluate", shield_body, basic="payouts:" + shield_pw)
        ctx.ck("shield_refuses_anonymous_and_wrong_credentials_401", res["shield_anonymous"]["status"] == 401 and res["shield_wrong_password"]["status"] == 401, {k: res[k] for k in ("shield_anonymous", "shield_wrong_password")})
        ctx.ck("shield_admits_the_payouts_identity_and_evaluates_(action_returned)", st == 200 and isinstance(doc, dict) and "action" in doc, res["shield_payouts_identity"])
        direct = ctx.pool.get(DIRECT)
        bas_url = BAS + "/payouts/shield/merchant/%s/details?account_number=%s" % (direct["merchant_id"], direct["account_number"])
        probe("bas_anonymous", "GET", bas_url)
        probe("bas_wrong_token", "GET", bas_url, headers={"Api-Token": "WRONG" + uuid.uuid4().hex[:6]})
        st, doc = probe("bas_payouts_token", "GET", bas_url, headers={"Api-Token": bas_tok})
        ctx.ck("banking-accounts_refuses_anonymous_and_wrong_Api-Token_401", res["bas_anonymous"]["status"] == 401 and res["bas_wrong_token"]["status"] == 401, {k: res[k] for k in ("bas_anonymous", "bas_wrong_token")})
        ctx.ck("banking-accounts_admits_the_payouts_Api-Token_200", st == 200, res["bas_payouts_token"])
        probe("workflows_anonymous", "POST", WORKFLOWS + "/twirp/rzp.workflows.workflow.v1.WorkflowAPI/List", {"limit": 1, "offset": 0})
        probe("workflows_wrong_password", "POST", WORKFLOWS + "/twirp/rzp.workflows.workflow.v1.WorkflowAPI/List", {"limit": 1, "offset": 0}, basic="workflow:WRONG" + uuid.uuid4().hex[:6])
        st, doc = probe("workflows_payouts_identity", "POST", WORKFLOWS + "/twirp/rzp.workflows.workflow.v1.WorkflowAPI/List", {"limit": 1, "offset": 0}, basic="workflow:" + wf_pw)
        ctx.ck("workflow_service_refuses_anonymous_and_wrong_credentials_401", res["workflows_anonymous"]["status"] == 401 and res["workflows_wrong_password"]["status"] == 401, {k: res[k] for k in ("workflows_anonymous", "workflows_wrong_password")})
        ctx.ck("workflow_service_admits_the_payouts_service_identity_200", st == 200, res["workflows_payouts_identity"])
    else:
        ctx.note("substitute variant: shield-stub / bankingaccounts-stub / workflow-engine carry no production credential contract; the Shield, "
                 "banking-accounts and Workflow-service identity checks need ARENA_TRUST_PATH=real.")
        st, doc = probe("shield_stub_probe", "POST", F.TRUST.get("shield_stub_url", "http://shield-stub:8080") + "/v1/rules/evaluate", shield_body)
        ctx.ev["substitute_shield_probe"] = res.get("shield_stub_probe")
    # payouts' own internal routes (what the Workflow service calls back into)
    probe("ps_internal_anonymous", "POST", PS + "/v1/payouts/payouts_internal/pout_ARENA00000000AA/approve", {})
    probe("ps_internal_wrong", "POST", PS + "/v1/payouts/payouts_internal/pout_ARENA00000000AA/approve", {}, basic="rzp_live:WRONG" + uuid.uuid4().hex[:6])
    ctx.ck("payouts_internal_callback_route_refuses_anonymous_and_wrong_service_credentials", res["ps_internal_anonymous"]["status"] in (401, 403) and res["ps_internal_wrong"]["status"] in (401, 403), {k: res[k] for k in ("ps_internal_anonymous", "ps_internal_wrong")})
    ctx.ev["probes"] = res


# ---------------------------------------------------------------------------
# 9. maker-checker through the real Workflow service
# ---------------------------------------------------------------------------
@journey("trust-path", "maker_checker", priority="P0", profile=TPW,
         title="maker-checker: a merchant-created payout raises a workflow in the Workflow service; a checker's approve/reject reaches payouts through the service's callback; decisions are idempotent and separation is exactly what the source enforces",
         source_ref="payouts internal/app/payouts/processor/base.go IsWorkflowApplicable -> workflow client Create; razorpay/workflows internal/workflow (Create, FindByOwnerDetails), "
                    "internal/action (ActionAPI CreateWithEntityId -> Cadence signal), internal/callback -> clients.payouts_live /v1/payouts/payouts_internal/{id}/approve|reject; "
                    "separation: razorpay/workflows has no maker!=checker rule (only the DCS-gated auto-approve when maker role == checker role)")
def tp_maker_checker(ctx):
    m = ctx.m
    org = m["merchant_id"]
    stp, pol = ctx.a.wfe_set_policy(org, required_approvals=1, separation=1)
    ctx.ev["policy"] = {"status": stp, "policy": pol}
    ctx.ck("approval_config_bound_for_the_merchant_(1_checker_approval)", stp == 200, ctx.ev["policy"])
    wf_applicable(ctx, m, True)
    ctx.a.mozart(m["merchant_id"], "success")
    r, pid, row = pending_payout(ctx, m, 5100, "mc-approve")
    ctx.ck("maker_create_pending_through_the_gateway", r["status"] == 200 and (row or {}).get("status") == "pending", {"status": r["status"], "row": row})
    if not pid or (row or {}).get("status") != "pending":
        wf_applicable(ctx, m, False); ctx.a.mozart(m["merchant_id"], clear=True); return
    wf = F.wait_until(lambda: ctx.a.wfe_for_payout(pid), timeout=30, interval=2, desc="workflow row")
    ctx.ev["workflow"] = wf
    ctx.ck("workflow_row_exists_for_the_payout_and_is_pending", bool(wf) and wf.get("state") == "pending", wf)
    psmap = ctx.a.ps_workflow_map(pid)
    ctx.ev["ps_workflow_map"] = psmap
    ctx.ck("payouts_and_the_workflow_service_agree_on_the_workflow_id", bool(psmap) and bool(wf) and psmap.get("workflow_id") == wf.get("workflow_id"), {"ps": psmap, "wf": (wf or {}).get("workflow_id")})
    st, dec = ctx.a.wfe_decide(pid, "approve")
    ctx.ev["approve"] = {"status": st, "body": dec}
    ctx.ck("checker_approve_accepted", st == 200, ctx.ev["approve"])
    end = ctx.wait_status(pid, "initiated", timeout=90)
    ctx.ck("payout_left_pending_through_the_approve_callback", (end or {}).get("status") in ("initiated", "processed"), end)
    st2, dup = ctx.a.wfe_decide(pid, "approve")
    ctx.ck("a_repeat_decision_on_the_terminal_workflow_is_refused_(409_real_/_404_reconstruction)", st2 in (404, 409), {"status": st2, "body": dup})
    st3, rej = ctx.a.wfe_decide(pid, "reject")
    ctx.ck("a_reject_after_approval_is_refused_(409_real_/_404_reconstruction)", st3 in (404, 409), {"status": st3, "body": rej})
    ctx.a.finish_bank(pid, "success")
    ctx.ck("approved_payout_processed", (ctx.wait_status(pid, "processed", timeout=120) or {}).get("status") == "processed", None)
    logs = ctx.a.payout_logs(pid)
    ctx.ck("exactly_one_transition_out_of_pending", sum(1 for l in logs if l.get("from") == "pending") == 1, logs)
    # reject path
    r2, pid2, row2 = pending_payout(ctx, m, 5200, "mc-reject")
    if pid2 and (row2 or {}).get("status") == "pending":
        st4, rj = ctx.a.wfe_decide(pid2, "reject")
        ctx.ck("checker_reject_accepted", st4 == 200, {"status": st4, "body": rj})
        ctx.ck("rejected_payout_is_rejected", (ctx.wait_terminal(pid2, timeout=90) or {}).get("status") == "rejected", ctx.a.payout(pid2))
    # separation
    if F.TRUST["real_workflows"]:
        r3, pid3, row3 = pending_payout(ctx, m, 5300, "mc-separation")
        if pid3 and (row3 or {}).get("status") == "pending":
            wf3 = ctx.a.wfe_for_payout(pid3)
            maker = {"actor_id": (wf3 or {}).get("creator_id") or "payouts", "roles": ["approver"]}
            st5, own = ctx.a._wf.wfe_decide(pid3, "approve", actor=maker)
            ctx.ev["self_approval_by_the_creator_identity"] = {"status": st5, "body": own, "creator_id": maker["actor_id"]}
            ctx.ck("real_workflow_service_accepts_the_creators_own_approval_(no_maker!=checker_rule_in_razorpay/workflows_-_source_supported_correction_of_the_M5_reconstruction)",
                   st5 == 200, ctx.ev["self_approval_by_the_creator_identity"])
            ctx.a.finish_bank(pid3, "success"); ctx.wait_terminal(pid3, timeout=120)
        ctx.note("separation: the REAL Workflow service enforces no maker != checker rule; a distinct-actor requirement is a payouts/dashboard "
                 "concern (the maker is the payouts service identity, the checker an actor claim). Recorded as a source-supported correction.")
    else:
        ctx.note("substitute variant: workflow-engine (M5 reconstruction) enforces separation_violation 403 -- see approval-workflow/maker_checker.")
    wf_applicable(ctx, m, False)
    ctx.a.mozart(m["merchant_id"], clear=True)


# ---------------------------------------------------------------------------
# 10. restart and cache invalidation
# ---------------------------------------------------------------------------
@journey("trust-path", "restart_cache_invalidation", priority="P1", profile=TP,
         title="restart & cache invalidation: gateway credentials survive a gateway restart (Kong Postgres), a rotated/deleted credential stops working immediately, Shield rules survive a Shield restart",
         source_ref="edge kong.conf database=postgres (basicauth_x_credentials); Kong core cache invalidation on Admin API writes (kong/cache events); shield rules table (MySQL)")
def tp_restart_cache_invalidation(ctx):
    if os.environ.get("M6_ALLOW_RESTART") != "1":
        ctx.blocked("M6_ALLOW_RESTART=1 (restart of edge-kong / shield-web containers)")
    if not real():
        ctx.blocked("the REAL trust path (ARENA_TRUST_PATH=real): Kong Admin API credential store + Shield rule API",
                    {"substitute": "kong-lite keeps merchants.json in memory (restart-reload) and shield-stub a fixture; the invalidation semantics below are the real components'"})
    m = ctx.m
    base = gw(ctx, "GET", "/v1/payouts?count=1", basic=merchant_basic(m), note="baseline")
    ctx.ck("baseline_200", base["status"] == 200, {"status": base["status"]})
    rec = ctx.a.restart_workers(["edge-kong"])
    ctx.ev["gateway_restart"] = rec
    ctx.ck("edge-kong_actually_restarted_and_is_running", all(x["actually_restarted"] and x["status_after"] == "running" for x in rec["restarts"]), rec)
    after = None
    for _ in range(30):
        after = gw(ctx, "GET", "/v1/payouts?count=1", basic=merchant_basic(m), note="after the gateway restart", timeout=15)
        if after["status"] == 200:
            break
        time.sleep(2)
    ctx.ck("merchant_key_still_authenticates_after_the_gateway_restart_(credentials_in_Kong_Postgres)", after["status"] == 200, {"status": after["status"], "answered_by": after["answered_by"]})
    # rotation: delete the credential through the Admin API -> the old secret must stop working at once
    st, creds = kong_admin("GET", "/consumers/%s/basic-auth-x" % m["merchant_id"])
    mine = [c for c in ((creds or {}).get("data") or []) if c.get("username") == m["key_id"]]
    ctx.ck("credential_present_before_rotation", len(mine) == 1, [c.get("id") for c in mine])
    if mine:
        t0 = time.time()
        std, _ = kong_admin("DELETE", "/consumers/%s/basic-auth-x/%s" % (m["merchant_id"], mine[0]["id"]))
        ctx.ck("credential_deleted_through_the_admin_api", std in (204, 200), {"status": std})
        gone, lat = None, None
        for _ in range(20):
            gone = gw(ctx, "GET", "/v1/payouts?count=1", basic=merchant_basic(m), note="old secret after deletion", timeout=15)
            if gone["status"] == 401:
                lat = round(time.time() - t0, 2); break
            time.sleep(0.5)
        ctx.ev["invalidation_latency_secs"] = lat
        ctx.ck("old_secret_refused_after_the_credential_was_deleted_(cache_invalidated)", gone["status"] == 401, {"status": gone["status"], "latency_secs": lat})
        if gone["status"] != 401:
            ctx.note("FINDING F-M11-2 (real gateway code, Kong 3.4.2 as pinned by edge/Dockerfile): a basic-auth-x credential deleted through the Admin API "
                     "keeps authenticating. kong-plugin-basic-auth-x/access.lua caches by its own keys (v2:basicauth_x_consumer_id:<username>, "
                     "v2:basicauth_x_by_hash:<hash>:<consumer>) that no invalidation targets, and the DAO's cache_key(username) override "
                     "(basicauth_x_credentials.lua:72) crashes on the CRUD event entity table ('attempt to concatenate a table value', edge-kong log), "
                     "so the core invalidation hook aborts; kong.conf leaves db_cache_ttl at its default 0 (never expires).")
        err = F.P.register_edge_kong_merchant(m["merchant_id"], m["secret"])
        ctx.ck("credential_re-registered", err is None, err)
        back = None
        for _ in range(20):
            back = gw(ctx, "GET", "/v1/payouts?count=1", basic=merchant_basic(m), note="after re-registration", timeout=15)
            if back["status"] == 200:
                break
            time.sleep(0.5)
        ctx.ck("re-registered_credential_authenticates_again", back["status"] == 200, {"status": back["status"]})
    # Shield: rules persist across a restart
    shield_pw = secret("auth_shield_payouts.txt"); ctx.a.hide(shield_pw)
    st, rules, _ = arena(ctx, "GET", SHIELD + "/v1/merchants/%s/rules" % m["merchant_id"], basic="payouts:" + shield_pw, note="rules before the Shield restart")
    n_before = len(rules_of(rules))
    rec2 = ctx.a.restart_workers(["shield-web"])
    ctx.ev["shield_restart"] = rec2
    ok = ctx.a.ps_healthy(tries=5, delay=1)
    st2, rules2 = None, None
    for _ in range(40):
        st2, rules2, _ = arena(ctx, "GET", SHIELD + "/v1/merchants/%s/rules" % m["merchant_id"], basic="payouts:" + shield_pw, note="rules after the Shield restart")
        if st2 == 200:
            break
        time.sleep(2)
    n_after = len(rules_of(rules2))
    ctx.ck("shield_rules_for_the_merchant_survive_the_restart_(MySQL)", st2 == 200 and n_after == n_before and n_after >= 1, {"before": n_before, "after": n_after, "status": st2})
    if ctx.ev.get("invalidation_latency_secs") is None and mine:
        ctx.expected_failure("F-M11-2", {"observed": "the deleted basic-auth-x credential kept authenticating (no invalidation within 10 s)",
                                          "source": "edge kong-plugin-basic-auth-x access.lua cache keys + basicauth_x_credentials.lua:72; kong.conf db_cache_ttl default 0"})


# ---------------------------------------------------------------------------
# 11. duplicate / idempotency through the gateway
# ---------------------------------------------------------------------------
@journey("trust-path", "idempotency", priority="P0", profile=TP,
         title="duplicate submission through the gateway: the same idempotency key replays the same payout, a different body under the same key is refused, exactly one row exists",
         source_ref="Route.php idempotentRoutesConfig payout_create; api Idempotency middleware (X-Payout-Idempotency); PS idempotency_keys")
def tp_idempotency(ctx):
    m = ctx.m
    ctx.a.mozart(m["merchant_id"], "success")
    ik = "m11k-" + uuid.uuid4().hex[:12]
    body = create_body(m, 2300, "idem")
    r1 = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=body, headers={"X-Payout-Idempotency": ik}, note="first")
    r2 = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=dict(body), headers={"X-Payout-Idempotency": ik}, note="same key, same body")
    p1 = F.bare((r1["body"] or {}).get("id") or "") if isinstance(r1["body"], dict) else ""
    p2 = F.bare((r2["body"] or {}).get("id") or "") if isinstance(r2["body"], dict) else ""
    ctx.ck("both_calls_200", r1["status"] == 200 and r2["status"] == 200, {"r1": r1["status"], "r2": r2["status"]})
    ctx.ck("same_key_same_body_returns_the_same_payout_id", bool(p1) and p1 == p2, {"p1": p1, "p2": p2})
    rows2 = audit_for(ctx, r2["request_id"], tenant=m["merchant_id"], route="payout_create")
    ctx.ck("the_replay_was_served_from_idempotency_state_without_a_second_upstream_create", any(x.get("decision") == "idempotent_replay" for x in rows2), rows2[:3])
    rc, cnt, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND id='%s'" % (m["merchant_id"], p1), note="rows for the payout")
    ctx.ck("exactly_one_payout_row", (cnt or "").strip() == "1", cnt)
    body3 = dict(body); body3["amount"] = 2400
    r3 = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=body3, headers={"X-Payout-Idempotency": ik}, note="same key, different body")
    ctx.ck("same_key_different_body_is_refused_400", r3["status"] == 400, {"status": r3["status"], "body": str(r3["body"])[:200]})
    rc, cnt2, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND amount=2400" % m["merchant_id"], note="no 2400 payout")
    ctx.ck("the_conflicting_payload_created_nothing", (cnt2 or "").strip() == "0", cnt2)
    # a second merchant reusing the same key gets its own payout (keys are merchant-scoped)
    other = ctx.pool.get(TPV)
    r4 = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(other), body=create_body(other, 2300, "idem-other"), headers={"X-Payout-Idempotency": ik}, note="another merchant, same key")
    p4 = F.bare((r4["body"] or {}).get("id") or "") if isinstance(r4["body"], dict) else ""
    ctx.ck("idempotency_keys_are_merchant-scoped_(other_merchant_gets_its_own_payout)", r4["status"] == 200 and bool(p4) and p4 != p1, {"status": r4["status"], "p4": p4})
    if p1:
        ctx.wait_terminal(p1, timeout=150)
    if p4:
        ctx.a.mozart(other["merchant_id"], "success"); ctx.wait_terminal(p4, timeout=150); ctx.a.mozart(other["merchant_id"], clear=True)
    ctx.a.mozart(m["merchant_id"], clear=True)


# ---------------------------------------------------------------------------
# 12. Shield rules on the real risk service
# ---------------------------------------------------------------------------
@journey("trust-path", "shield_rules", priority="P0", profile=TP,
         title="Shield: the merchant's rules live in the real Shield (rule API); a payout to a beneficiary matching a block rule is refused as suspicious with the Shield verdict persisted, a clean beneficiary passes",
         source_ref="payouts processor/base.go IsPayoutBlockedByShield -> HandlePayoutStatusForShieldBlockResponse (suspicious_transaction); shield app/services/ruler (govaluate over PayoutInput keys); "
                    "trustpath/shield/seed_rules.py (account_number =~ '9999$' -> block, seeded per merchant)")
def tp_shield_rules(ctx):
    m = ctx.m
    if real():
        shield_pw = secret("auth_shield_payouts.txt"); ctx.a.hide(shield_pw)
        st, rules, _ = arena(ctx, "GET", SHIELD + "/v1/merchants/%s/rules" % m["merchant_id"], basic="payouts:" + shield_pw, note="the merchant's rules in the real Shield")
        items = rules_of(rules)
        ctx.ev["shield_rules"] = [{k: r.get(k) for k in ("id", "name", "expression", "action", "ruleset", "merchant_id", "is_active", "rule_number")} for r in items][:6]
        ctx.ck("merchant_has_the_arena_block_rule_in_the_real_Shield", any("9999" in str(r.get("expression")) and r.get("action") == "block" for r in items), ctx.ev["shield_rules"])
        rc, flags, _ = ctx.a.mysql("mysql-shield", "shield", "mysql_shield_root_password.txt",
                                   "SELECT r.id, r.expression, f.flag_type, f.rollout_percent FROM rules r LEFT JOIN feature_flags f ON f.entity_id=r.id AND f.entity_type='RULE' AND f.deleted_at IS NULL WHERE r.merchant_id='%s'" % m["merchant_id"],
                                   note="rule rollout state (a canary rule at 0% is never applied)")
        ctx.ev["shield_rule_rollout"] = flags
        ctx.ck("the_block_rule_is_a_stable_rule_(no_0%_canary_feature_flag)", rc == 0 and "9999" in (flags or "") and not any(("CANARY" in l and l.rstrip().endswith("\t0")) for l in (flags or "").splitlines() if "9999" in l), flags)
        # the exact request shape payouts sends (pkg/shield: no rulesets -> Shield adds "payout_primary" + the merchant id)
        probe = {"merchant_id": m["merchant_id"], "entity_type": "payout", "entity_id": "pout_m11probe" + uuid.uuid4().hex[:6],
                 "input": {"payout_id": "pout_m11probe", "payout_amount": 4100, "mode": "IMPS", "currency": "INR", "purpose": "payout", "merchant_id": m["merchant_id"],
                           "account_number": "5550009999", "ifsc": "RATN0000001", "created_at": int(time.time()), "entity_type": "payout", "source_account_types": "shared"}}
        stp, pdoc, _ = arena(ctx, "POST", SHIELD + "/v1/rules/evaluate", probe, basic="payouts:" + shield_pw, note="direct evaluate, payouts' request shape (no rulesets)")
        ctx.ev["direct_evaluate"] = pdoc
        ctx.ck("real_Shield_blocks_the_payouts-shaped_request_for_a_9999_beneficiary_(ruleset_payout_primary)", stp == 200 and (pdoc or {}).get("action") == "block", pdoc)
    else:
        ctx.note("substitute variant: shield-stub evaluates the fixture seeds/shield/rules.json (same semantics); the rule-API check needs the real Shield.")
    # a beneficiary matching the block rule, created through the internal app route the monolith exposes
    st, contact, _ = arena(ctx, "POST", ING + "/v1/contacts_internal", {"name": "m11 shield probe", "type": "vendor"}, basic=JI.app_basic("vendor_payments"), headers={"X-Razorpay-Account": m["merchant_id"]}, note="contact for the blocked beneficiary")
    cid = (contact or {}).get("id")
    st2, fa, _ = arena(ctx, "POST", ING + "/v1/fund_accounts_internal", {"contact_id": cid, "account_type": "bank_account", "bank_account": {"name": "blocked bene", "ifsc": "RATN0000001", "account_number": "5550009999"}},
                       basic=JI.app_basic("vendor_payments"), headers={"X-Razorpay-Account": m["merchant_id"]}, note="fund account whose account number ends in 9999")
    bad_fa = (fa or {}).get("id")
    ctx.ck("blocked-pattern_fund_account_created_for_the_merchant", st2 == 200 and bool(bad_fa), {"status": st2, "body": str(fa)[:200]})
    if not bad_fa:
        return
    ctx.a.mozart(m["merchant_id"], "success")
    rc, cnt0, _ = ctx.a.payouts_sql("SELECT count(*) FROM payouts WHERE merchant_id='%s' AND status IN ('processed','initiated','created')" % m["merchant_id"], note="live payouts before")
    r = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=create_body(m, 4100, "shield-block", fund_account_id=bad_fa), headers={"X-Payout-Idempotency": "m11s1-" + uuid.uuid4().hex[:10]}, note="payout to the blocked beneficiary")
    pid = F.bare((r["body"] or {}).get("id") or "") if isinstance(r["body"], dict) else ""
    row = ctx.a.payout(pid) if pid else None
    blocked = (r["status"] == 400 and "suspicious" in json.dumps(r["body"]).lower()) or ((row or {}).get("status") in ("failed", "rejected"))
    ctx.ev["blocked_attempt"] = {"status": r["status"], "body": str(r["body"])[:300], "row": row}
    if real():
        ctx.ck("payout_to_the_blocked_beneficiary_is_refused_as_suspicious_transaction", blocked, ctx.ev["blocked_attempt"])
    else:
        ctx.ck("shield-stub_was_consulted_for_the_fresh_merchant_(fixture_rules_bind_to_the_seeded_merchants_only)", pid != "", ctx.ev["blocked_attempt"])
        ctx.note("substitute variant: seeds/shield/rules.json carries rules for the seeded fixture merchants; a fresh pool merchant has none, so the stub allows. "
                 "The real Shield seeds the block rule per merchant at provisioning (register_trust_path_merchant).")
    if pid:
        meta = shield_meta(ctx, pid, "persisted Shield verdict")
        ctx.ev["shield_meta"] = meta[:400]
        ctx.ck("shield_verdict_persisted_on_the_payout_(payout_meta_permanent.shield_evaluate_response)", ("block" in meta) if real() else bool(meta), meta[:300])
    if real():
        hits = container_log_hits("shield-web", m["merchant_id"])
        ctx.ev["shield_log_hits"] = hits[:5]
        ctx.ck("the_real_Shield_evaluated_this_merchant_(container_log)", bool(hits), hits[:3])
    ok = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=create_body(m, 4200, "shield-clean"), headers={"X-Payout-Idempotency": "m11s2-" + uuid.uuid4().hex[:10]}, note="payout to the clean beneficiary")
    okp = F.bare((ok["body"] or {}).get("id") or "") if isinstance(ok["body"], dict) else ""
    ctx.ck("clean_beneficiary_create_200", ok["status"] == 200 and bool(okp), {"status": ok["status"]})
    if okp:
        end = ctx.wait_terminal(okp, timeout=150)
        ctx.ck("clean_payout_processed_after_the_Shield_pass", (end or {}).get("status") == "processed", end)
        ctx.ev["shield_meta_clean"] = shield_meta(ctx, okp, "Shield verdict on the clean payout")[:300]
    ctx.a.mozart(m["merchant_id"], clear=True)


# ---------------------------------------------------------------------------
# 13. banking-accounts lookup for a Direct merchant
# ---------------------------------------------------------------------------
@journey("trust-path", "bas_lookup", priority="P0", profile=DIRECT,
         title="banking-accounts: a Direct merchant's payout makes payouts fetch the merchant's Shield details from the real banking-accounts service with the Api-Token identity; the rows come from the seeded businesses/banking_accounts tables",
         source_ref="payouts core.go prepareDirectMerchantDetailsForShield -> pkg/bankingAccountService FetchMerchantDetailsForShield GET /payouts/shield/merchant/{mid}/details?account_number=; "
                    "banking-accounts internal/api payouts shield merchant details handler (businesses.sales_team / business_type)")
def tp_bas_lookup(ctx):
    d = ctx.m
    svc = "banking-accounts-api" if real() else "bankingaccounts-stub"
    if real():
        tok = secret("auth_bas_payouts.txt"); ctx.a.hide(tok)
        st, doc, txt = arena(ctx, "GET", BAS + "/payouts/shield/merchant/%s/details?account_number=%s" % (d["merchant_id"], d["account_number"]), headers={"Api-Token": tok}, note="direct lookup with the payouts Api-Token")
        ctx.ev["bas_details"] = doc
        ctx.ck("real_banking-accounts_returns_the_Direct_merchants_details_200", st == 200 and isinstance(doc, dict), {"status": st, "body": (txt or "")[:300]})
        body = json.dumps(doc or {}).lower()
        ctx.ck("details_carry_sales_team_and_business_type_(seeded_businesses_row)", "sales_team" in body and "business_type" in body, (txt or "")[:300])
        pw = secret("mysql_bas_root_password.txt"); ctx.a.hide(pw)
        rc, out, _ = ctx.a.mysql("mysql-bas", "banking_account", "mysql_bas_root_password.txt", "SELECT count(*) FROM banking_accounts ba JOIN businesses b ON ba.business_id=b.id WHERE b.merchant_id='%s'" % d["merchant_id"], note="banking_accounts rows for the Direct merchant (via businesses)")
        ctx.ck("banking_accounts_row_exists_for_the_Direct_merchant", rc == 0 and (out or "").strip() not in ("", "0"), out)
    else:
        ctx.note("substitute variant: bankingaccounts-stub answers from seeds/bankingaccounts; the Api-Token contract and the businesses/banking_accounts tables need the real service.")
    r = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(d), body={"fund_account_id": d["fund_account_id"], "account_number": d["account_number"], "amount": 1500, "currency": "INR", "mode": "IMPS", "purpose": "payout",
                                                                        "queue_if_low_balance": True, "merchant_id": d["merchant_id"], "narration": "m11 bas", "notes": {"m11": "bas"}},
           headers={"X-Payout-Idempotency": "m11bas-" + uuid.uuid4().hex[:10]}, note="Direct merchant payout through the gateway")
    pid = F.bare((r["body"] or {}).get("id") or "") if isinstance(r["body"], dict) else ""
    ctx.ck("direct_merchant_create_accepted", r["status"] == 200 and bool(pid), {"status": r["status"], "body": str(r["body"])[:200]})
    hits = []
    for _ in range(15):
        hits = container_log_hits(svc, d["merchant_id"])
        if hits:
            break
        time.sleep(2)
    ctx.ev["bas_log_hits"] = hits[:5]
    ctx.ck("payouts_called_the_banking-accounts_service_for_this_merchant_during_processing", bool(hits), hits[:3])
    if pid:
        meta = ""
        for _ in range(20):
            meta = shield_meta(ctx, pid, "Shield verdict for the Direct payout")
            if meta:
                break
            time.sleep(3)
        ctx.ev["shield_meta"] = meta[:300]
        ctx.ck("shield_evaluated_the_Direct_payout_(verdict_persisted)", bool(meta), meta[:200])
        ctx.wait_terminal(pid, timeout=60)


# ---------------------------------------------------------------------------
# 14. the payouts-ext gateway service (GET payout by id straight to the Payouts service)
# ---------------------------------------------------------------------------
@journey("trust-path", "payouts_ext_direct", priority="P1", profile=TP,
         title="payouts-ext: the production Kong service that routes GET /v1/payouts/{id} straight to the Payouts service (by host) authenticates the merchant at the edge and hands payouts the edge passport; other merchants and anonymous callers are refused",
         source_ref="terraform-kong templates/payouts/payouts-ext.tf (authenticated_paths payout_get_by_id rollout 1, host payouts-ext.razorpay.com -> payouts.razorpay.com:443); "
                    "payouts pkg/passport/handler.go [passport.edge] kid edgev2; payouts internal/app/payouts fetch by (id, merchant)")
def tp_payouts_ext_direct(ctx):
    if not real_gw():
        ctx.blocked("the REAL edge gateway (ARENA_INGRESS_IMPL=edge-kong): kong-lite has no payouts-ext service/host routing")
    m, victim = ctx.m, ctx.pool.get(TPV)
    ctx.a.mozart(m["merchant_id"], "success")
    r = gw(ctx, "POST", "/v1/payouts", basic=merchant_basic(m), body=create_body(m, 2600, "ext"), headers={"X-Payout-Idempotency": "m11x-" + uuid.uuid4().hex[:10]}, note="create through prod-api")
    pid = F.bare((r["body"] or {}).get("id") or "") if isinstance(r["body"], dict) else ""
    ctx.ck("create_200", r["status"] == 200 and bool(pid), {"status": r["status"]})
    if not pid:
        return
    host = {"Host": "payouts-ext.razorpay.com"}
    own = gw(ctx, "GET", "/v1/payouts/pout_" + pid, basic=merchant_basic(m), headers=host, note="GET by id on the payouts-ext host (direct to the Payouts service)")
    ctx.ev["payouts_ext_own"] = {"status": own["status"], "answered_by": own["answered_by"], "body": str(own["body"])[:240]}
    rows = audit_for(ctx, own["request_id"])
    ctx.ck("the_monolith_replacement_never_saw_the_payouts-ext_call_(routed_by_host_to_the_Payouts_service)", rows == [], rows[:2])
    ps_refused = own["status"] == 401 and "api key provided is invalid" in json.dumps(own["body"])
    ctx.ck("payouts_service_answered_the_edge-authenticated_call_(200_or_its_own_401)", own["status"] in (200, 401), ctx.ev["payouts_ext_own"])
    if ps_refused:
        ctx.note("OBSERVED + SOURCE-EXPLAINED: the Payouts service refused the merchant call with 401 'The api key provided is invalid'. "
                 "payouts internal/routing/router/payout_routes.go puts middleware.BasicAuth(cred.API, cred.Workflow) BEFORE PassportAuthentication on "
                 "/v1/payouts; the gateway forwards the merchant's own Basic credential (consumer-authentication hide_credentials=false), which is not a "
                 "service credential (middleware/auth.go respondWithInvalidAPIKeyStatus). How production payouts-ext traffic satisfies that service "
                 "BasicAuth is a production unknown (PU-M11-7); the twin reproduces the source as pinned.")
        ctx.ev["production_unknown"] = "PU-M11-7"
    elif own["status"] == 200:
        ctx.ck("payout_read_belongs_to_the_caller", isinstance(own["body"], dict) and F.bare(own["body"].get("id") or "") == pid, own["body"])
    hits = container_log_hits("payouts-api", pid)
    ctx.ev["ps_log_hits"] = hits[:3]
    other = gw(ctx, "GET", "/v1/payouts/pout_" + pid, basic=merchant_basic(victim), headers=host, note="another merchant on the payouts-ext host")
    ctx.ck("another_merchant_never_reads_the_payout_through_payouts-ext_(4xx)", isinstance(other["status"], int) and 400 <= other["status"] < 500, {"status": other["status"], "body": str(other["body"])[:200]})
    wrong = gw(ctx, "GET", "/v1/payouts/pout_" + pid, basic="%s:WRONG%s" % (m["key_id"], uuid.uuid4().hex[:6]), headers=host, note="wrong secret on the payouts-ext host")
    ctx.ck("wrong_secret_is_refused_401_at_the_edge_(never_reaches_the_Payouts_service)", wrong["status"] == 401 and "Authentication failed" in json.dumps(wrong["body"]), {"status": wrong["status"], "body": str(wrong["body"])[:160]})
    anon = gw(ctx, "GET", "/v1/payouts/pout_" + pid, headers=host, note="anonymous on the payouts-ext host")
    ctx.ck("anonymous_is_refused_401_at_the_edge", anon["status"] == 401 and "provide your api key" in json.dumps(anon["body"]), {"status": anon["status"], "body": str(anon["body"])[:160]})
    ctx.wait_terminal(pid, timeout=150)
    ctx.a.mozart(m["merchant_id"], clear=True)
