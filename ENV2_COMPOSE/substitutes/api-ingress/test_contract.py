"""api-ingress contract tests: identity separation, tenant authority, ownership, idempotency, routing.

Runs on the host with no docker: the ingress serves from a temp SQLite + temp seeds/secrets, and a
fake Payouts upstream records exactly what the ingress forwarded (headers, passport claims, body).
Every route the ingress serves must exist in contract/routes.json (mechanically derived from the
pinned api Route.php); a served route without evidence is a test failure.
"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBS = HERE.parent
sys.path.insert(0, str(SUBS))
from _common import rsa_sign  # noqa: E402

M_A, M_B = "ARENAM00000TA1", "ARENAM00000TB2"
FA_A, FA_B = "ARENAFAX00A001", "ARENAFAX00B001"
FA_ORPHAN = "ARENAFAXORPHAN"


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def b64(u, p):
    return "Basic " + base64.b64encode(("%s:%s" % (u, p)).encode()).decode()


def jwt_payload(token):
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


class FakePS(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *a):
        pass

    def _do(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        rec = {"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()},
               "body": json.loads(raw) if raw else None}
        FakePS.calls.append(rec)
        status, body = 200, {"id": "pout_ARENAFAKE00001", "entity": "payout", "status": "processing",
                             "echo_path": self.path, "merchant_id": (rec["body"] or {}).get("merchant_id")}
        if self.path.startswith("/v1/payouts/payouts_internal/") and self.command == "GET":
            body = {"id": self.path.rsplit("/", 1)[-1], "merchant_id": self.headers.get("x-merchant-id")}
        if self.path.startswith("/v1/admin/"):
            body = {"balance_id": self.path.split("/")[4], "free_payouts_count": 100}
        raw = json.dumps(body).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)

    do_GET = do_POST = do_PATCH = _do


class IngressContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); root = Path(cls.tmp.name)
        # --- fake PS upstream ---
        cls.ps_port = free_port()
        cls.ps = ThreadingHTTPServer(("127.0.0.1", cls.ps_port), FakePS)
        threading.Thread(target=cls.ps.serve_forever, daemon=True).start()
        # --- seeds + secrets ---
        (root / "seed").mkdir(); (root / "secrets" / "kong" / "merchants").mkdir(parents=True); (root / "secrets" / "ingress").mkdir()
        (root / "seed" / "merchants.json").write_text(json.dumps({"merchants": {
            M_A: {"key_id_live": "rzp_live_" + M_A, "secret_file": "m_a", "roles": []},
            M_B: {"key_id_live": "rzp_live_" + M_B, "secret_file": "m_b", "roles": []}}}))
        (root / "secrets" / "kong" / "merchants" / "m_a.txt").write_text("secret-a\n")
        (root / "secrets" / "kong" / "merchants" / "m_b.txt").write_text("secret-b\n")
        (root / "seed" / "monolith_merchants.json").write_text(json.dumps({"merchants": {M_A: {"merchant": {"id": M_A}}, M_B: {"merchant": {"id": M_B}}}}))
        fa = lambda fid, mid, ctype="employee": {"id": "fa_" + fid, "entity": "fund_account", "account_type": "bank_account", "active": True,
                                                 "bank_account": {"ifsc": "RATN0000001", "account_number": "1112220099", "name": "Bene"},
                                                 "contact_id": "cont_" + fid, "contact": {"id": "cont_" + fid, "type": ctype, "name": "Bene", "active": True},
                                                 "created_at": 1700000000, **({"merchant_id": mid} if mid else {})}
        (root / "seed" / "fund_accounts.json").write_text(json.dumps({"fund_accounts": {
            FA_A: fa(FA_A, M_A), FA_B: fa(FA_B, M_B), FA_ORPHAN: fa(FA_ORPHAN, None), "ARENAFAXTAX001": fa("ARENAFAXTAX001", M_A, "rzp_tax_pay")}}))
        # passport key: generate a throwaway RSA key with openssl (host tool; the arena's gen-secrets does the same)
        pem = root / "secrets" / "kong" / "passport_private_key"
        subprocess.run(["openssl", "genrsa", "-out", str(pem), "2048"], check=True, capture_output=True)
        subprocess.run(["openssl", "rsa", "-in", str(pem), "-traditional", "-out", str(pem)], check=True, capture_output=True)
        for name, val in (("auth_api_payouts", "ps-api-secret"), ("auth_workflow_payouts", "wf-secret")):
            (root / "secrets" / "kong" / name).write_text(val + "\n")
        for name, val in (("app_vendor_payments", "vp-secret"), ("app_xpayroll", "xp-secret"), ("app_batch", "batch-secret"),
                          ("app_workflows", "wf-app-secret"), ("auth_monolith_shared", "ps-to-api-secret"), ("admin_token", "admin-secret"),
                          ("monolith_basic_auth", "rzp_live:stub-secret")):
            (root / "secrets" / "ingress" / name).write_text(val + "\n")
        cls.port = free_port()
        env = {**os.environ, "STUB_PORT": str(cls.port), "INGRESS_DB": str(root / "ingress.sqlite"),
               "PS_API_URL": "http://127.0.0.1:%d" % cls.ps_port, "MONOLITH_STUB_URL": "http://127.0.0.1:1",
               "KONG_MERCHANTS_FILE": str(root / "seed" / "merchants.json"), "MONOLITH_MERCHANTS_FILE": str(root / "seed" / "monolith_merchants.json"),
               "MONOLITH_FUND_ACCOUNTS_FILE": str(root / "seed" / "fund_accounts.json"), "SECRETS_KONG_DIR": str(root / "secrets" / "kong"),
               "SECRETS_INGRESS_DIR": str(root / "secrets" / "ingress"), "INGRESS_CONTRACT_FILE": str(HERE / "contract" / "routes.json")}
        cls.proc = subprocess.Popen([sys.executable, str(HERE / "server.py")], env=env, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/health" % cls.port, timeout=1); break
            except Exception:
                time.sleep(0.05)
        cls.contract = json.loads((HERE / "contract" / "routes.json").read_text())["routes"]

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(); cls.ps.shutdown(); cls.tmp.cleanup()

    def setUp(self):
        FakePS.calls.clear()

    def call(self, method, path, body=None, auth=None, headers=None):
        h = {"Content-Type": "application/json", **(headers or {})}
        if auth:
            h["Authorization"] = auth
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), json.dumps(body).encode() if body is not None else None, h, method=method)
        try:
            r = urllib.request.urlopen(req, timeout=5); return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    A = b64("rzp_live_" + M_A, "secret-a")
    B = b64("rzp_live_" + M_B, "secret-b")
    VP = b64("rzp_live", "vp-secret")
    PSVC = b64("rzp_live", "ps-to-api-secret")
    ADMIN = b64("admin", "admin-secret")

    # ---- inventory --------------------------------------------------------
    def test_every_served_route_is_in_the_derived_contract_with_matching_group(self):
        st, served = self.call("GET", "/_ingress/contract")
        self.assertEqual(st, 200)
        self.assertGreaterEqual(len(served["served"]), 24)
        for r in served["served"]:
            self.assertIn(r["name"], self.contract, r["name"])
            groups = set(self.contract[r["name"]]["groups"])
            ctxs = set(r["contexts"])
            if "application" in ctxs:
                self.assertTrue(groups & {"internal", "proxy"}, r["name"])        # bulk: proxy group served to the batch app
            if "merchant" in ctxs:
                self.assertIn("private", groups, r["name"])
            if "admin" in ctxs:
                self.assertIn("admin", groups, r["name"])
            self.assertTrue(r["source_refs"], r["name"])

    # ---- identity separation ---------------------------------------------
    def test_merchant_credential_is_rejected_on_internal_routes(self):
        st, body = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_A, auth=self.A)
        self.assertEqual(st, 400); self.assertIn("not found", body["error"]["description"])
        st, _ = self.call("POST", "/v1/internalContactPayout", {"fund_account_id": "fa_" + FA_A}, auth=self.A)
        self.assertEqual(st, 400)
        self.assertEqual(FakePS.calls, [])

    def test_application_credential_is_rejected_on_merchant_routes_and_needs_account_scope(self):
        st, _ = self.call("POST", "/v1/payouts", {"amount": 1}, auth=self.VP, headers={"X-Razorpay-Account": M_A})
        self.assertEqual(st, 400)
        st, body = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_A, auth=self.VP)
        self.assertEqual(st, 400); self.assertEqual(body["error"]["field"], "X-Razorpay-Account")
        st, _ = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_A, auth=self.VP, headers={"X-Razorpay-Account": "ARENAM000NOPE99"})
        self.assertEqual(st, 400)

    def test_admin_routes_require_the_admin_identity(self):
        for auth in (self.A, self.VP, b64("admin", "wrong")):
            st, _ = self.call("GET", "/v1/admin/payouts/bal_0001/free_payout", auth=auth, headers={"X-Razorpay-Account": M_A})
            self.assertEqual(st, 400, auth)
        st, body = self.call("GET", "/v1/admin/payouts/bal_0001/free_payout", auth=self.ADMIN)
        self.assertEqual(st, 200); self.assertEqual(body["free_payouts_count"], 100)
        claims = jwt_payload(FakePS.calls[-1]["headers"]["x-passport-jwt-v1"])
        self.assertEqual(claims["consumer"]["type"], "admin")
        st, _ = self.call("POST", "/v1/payouts", {"amount": 1}, auth=self.ADMIN)
        self.assertEqual(st, 400)

    def test_bad_merchant_key_is_401_and_app_not_in_internalApps_is_route_not_found(self):
        st, _ = self.call("GET", "/v1/payouts/pout_ARENA000000001", auth=b64("rzp_live_" + M_A, "nope"))
        self.assertEqual(st, 401)
        # xpayroll may create internal-contact payouts but may not fetch fund accounts (Route::$internalApps)
        st, _ = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_A, auth=b64("rzp_live", "xp-secret"), headers={"X-Razorpay-Account": M_A})
        self.assertEqual(st, 400)

    # ---- tenant authority ------------------------------------------------
    def test_client_supplied_identity_headers_and_body_merchant_are_ignored(self):
        st, body = self.call("POST", "/v1/payouts", {"amount": 100, "merchant_id": M_B, "fund_account_id": "fa_" + FA_A},
                             auth=self.A, headers={"X-Payout-Idempotency": "k1", "x-merchant-id": M_B, "X-Entity-Id": M_B,
                                                   "X-Razorpay-Account": M_B, "X-Passport-JWT-V1": "forged.token.x",
                                                   "X-Payout-Actor-Id": "attacker", "X-Payouts-Service-Proxy": "1"})
        self.assertEqual(st, 200)
        fwd = FakePS.calls[-1]
        self.assertEqual(fwd["body"]["merchant_id"], M_A)
        for h in ("x-merchant-id", "x-entity-id", "x-razorpay-account", "x-payouts-service-proxy"):
            self.assertNotIn(h, fwd["headers"], h)
        self.assertEqual(fwd["headers"]["x-payout-actor-id"], M_A)
        self.assertEqual(fwd["headers"]["authorization"], b64("api", "ps-api-secret"))
        claims = jwt_payload(fwd["headers"]["x-passport-jwt-v1"])
        self.assertEqual(claims["consumer"], {"id": M_A, "type": "merchant"})
        self.assertEqual(fwd["headers"]["x-payout-idempotency"], "k1")

    def test_internal_route_tenant_comes_only_from_x_razorpay_account(self):
        st, body = self.call("POST", "/v1/payouts_internal", {"amount": 5, "merchant_id": M_B}, auth=self.VP,
                             headers={"X-Razorpay-Account": M_A, "X-Payout-Idempotency": "vp1", "x-merchant-id": M_B})
        self.assertEqual(st, 200)
        fwd = FakePS.calls[-1]
        self.assertEqual(fwd["body"]["merchant_id"], M_A); self.assertEqual(fwd["headers"]["x-merchant-id"], M_A)
        claims = jwt_payload(fwd["headers"]["x-passport-jwt-v1"])
        self.assertEqual(claims["consumer"], {"id": "vendor_payments", "type": "application", "meta": {"name": "vendor_payments"}})
        self.assertEqual(fwd["headers"]["x-payout-actor-type"], "application")

    # ---- ownership -------------------------------------------------------
    def test_fund_account_ownership_is_merchant_scoped(self):
        ok, own = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_A, auth=self.PSVC, headers={"X-Razorpay-Account": M_A})
        self.assertEqual(ok, 200); self.assertEqual(own["id"], "fa_" + FA_A); self.assertNotIn("merchant_id", own)
        self.assertEqual(own["contact"]["type"], "employee")
        st, body = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_B, auth=self.PSVC, headers={"X-Razorpay-Account": M_A})
        self.assertEqual(st, 400); self.assertEqual(body["error"]["description"], "The id provided does not exist")
        st, _ = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_ORPHAN, auth=self.PSVC, headers={"X-Razorpay-Account": M_A})
        self.assertEqual(st, 400, "a record without an owner is usable by nobody")
        st, reg = self.call("GET", "/_ingress/registry/fund_accounts?id=fa_" + FA_B, auth=self.ADMIN)
        self.assertEqual(reg["resources"][0]["merchant_id"], M_B)

    def test_internal_contact_payout_gates(self):
        h = {"X-Razorpay-Account": M_A, "X-Payout-Idempotency": "tax-1"}
        st, body = self.call("POST", "/v1/internalContactPayout", {"amount": 12500}, auth=self.VP, headers=h)
        self.assertEqual((st, body["error"]["description"]), (400, "fund_account_id is required"))
        st, body = self.call("POST", "/v1/internalContactPayout", {"amount": 12500, "fund_account_id": "fa_" + FA_A}, auth=self.VP, headers=h)
        self.assertEqual(body["error"]["description"], "Please send fund accounts of internal type contacts only")
        st, body = self.call("POST", "/v1/internalContactPayout", {"amount": 12500, "fund_account_id": "fa_ARENAFAXTAX001"}, auth=self.VP, headers={**h, "X-Razorpay-Account": M_B})
        self.assertEqual(body["error"]["description"], "The id provided does not exist")
        st, body = self.call("POST", "/v1/internalContactPayout", {"amount": 12500, "fund_account_id": "fa_ARENAFAXTAX001"}, auth=b64("rzp_live", "xp-secret"), headers=h)
        self.assertIn("AppNotPermitted", body["error"]["description"])
        st, body = self.call("POST", "/v1/internalContactPayout", {"amount": 12500, "fund_account_id": "fa_ARENAFAXTAX001"}, auth=self.VP, headers=h)
        self.assertEqual(st, 200); self.assertEqual(FakePS.calls[-1]["path"], "/v1/payouts/internal_contact_payout")
        self.assertEqual(FakePS.calls[-1]["body"]["merchant_id"], M_A)
        st, body = self.call("POST", "/v1/internalContactPayout", {"amount": 12500, "fund_account_id": "fa_ARENAFAXTAX001"}, auth=self.VP, headers={"X-Razorpay-Account": M_A})
        self.assertEqual(st, 400); self.assertIn("Idempotency key is missing", body["error"]["description"])

    # ---- idempotency -----------------------------------------------------
    def test_idempotency_replays_same_request_and_rejects_different_payload(self):
        h = {"X-Payout-Idempotency": "idem-1"}
        st1, b1 = self.call("POST", "/v1/payouts", {"amount": 7, "fund_account_id": "fa_" + FA_A}, auth=self.A, headers=h)
        n = len(FakePS.calls)
        st2, b2 = self.call("POST", "/v1/payouts", {"fund_account_id": "fa_" + FA_A, "amount": 7}, auth=self.A, headers=h)
        self.assertEqual((st1, st2), (200, 200)); self.assertEqual(b1, b2); self.assertEqual(len(FakePS.calls), n, "replayed without an upstream call")
        st3, b3 = self.call("POST", "/v1/payouts", {"amount": 8, "fund_account_id": "fa_" + FA_A}, auth=self.A, headers=h)
        self.assertEqual(st3, 400); self.assertIn("Different request body", b3["error"]["description"])
        st4, _ = self.call("POST", "/v1/payouts", {"amount": 7, "fund_account_id": "fa_" + FA_A}, auth=self.B, headers=h)
        self.assertEqual(st4, 200); self.assertEqual(len(FakePS.calls), n + 1, "keys are merchant-scoped")

    # ---- tag-back and OTP ------------------------------------------------
    def test_tax_payment_tagback_follows_source_semantics(self):
        h = {"X-Razorpay-Account": M_A}
        st, body = self.call("PATCH", "/v1/payouts_internal/pout_ARENA000000001/tax-payment-id", {"tax_payment_id": "txpy_short"}, auth=self.VP, headers=h)
        self.assertEqual(st, 400)
        st, body = self.call("PATCH", "/v1/payouts_internal/pout_ARENA000000001/tax-payment-id", {"tax_payment_id": "badp_00000000000001"}, auth=self.VP, headers=h)
        self.assertEqual(st, 400)
        st, body = self.call("PATCH", "/v1/payouts_internal/pout_ARENA000000001/tax-payment-id", {"tax_payment_id": "txpy_00000000000001"}, auth=self.VP, headers=h)
        self.assertEqual((st, body), (200, {"status": "SUCCESS"}))
        st, ev = self.call("GET", "/_ingress/evidence", auth=self.ADMIN)
        tag = [t for t in ev["payout_details"] if t["payout_id"] == "ARENA000000001"][0]
        self.assertEqual((tag["tagged_by_app"], tag["tagged_by_tenant"]), ("vendor_payments", M_A))

    def test_synthetic_otp_and_dashboard_session(self):
        st, s = self.call("POST", "/_ingress/session", {"merchant_id": M_A, "user_id": "ARENAUSER00001"}, auth=self.ADMIN)
        self.assertEqual(st, 200)
        st, o = self.call("POST", "/_ingress/otp", {"merchant_id": M_A, "user_id": "ARENAUSER00001", "action": "create_payout"}, auth=self.ADMIN)
        dash = b64("dashboard", s["session_token"])
        st, body = self.call("POST", "/v1/payouts_with_otp", {"amount": 9, "otp": "000000", "token": o["token"]}, auth=dash, headers={"X-Payout-Idempotency": "d1"})
        self.assertEqual(st, 400)
        st, body = self.call("POST", "/v1/payouts_with_otp", {"amount": 9, "otp": o["otp"], "token": o["token"]}, auth=dash, headers={"X-Payout-Idempotency": "d2"})
        self.assertEqual(st, 200)
        claims = jwt_payload(FakePS.calls[-1]["headers"]["x-passport-jwt-v1"])
        self.assertEqual(claims["consumer"]["type"], "user"); self.assertEqual(claims["impersonation"]["consumer"]["id"], M_A)
        self.assertEqual(FakePS.calls[-1]["headers"]["app-user-id"], "ARENAUSER00001")
        st, body = self.call("POST", "/v1/vendor-payments/verify-otp", {"user_id": "ARENAUSER00001", "otp": o["otp"], "token": o["token"]}, auth=self.VP, headers={"X-Razorpay-Account": M_A})
        self.assertEqual(body, {"success": False}, "an OTP is single use")

    def test_reset_removes_mutable_state_but_keeps_seed_ownership(self):
        st, _ = self.call("POST", "/_ingress/reset", auth=self.ADMIN)
        self.assertEqual(st, 200)
        st, ev = self.call("GET", "/_ingress/evidence", auth=self.ADMIN)
        self.assertEqual((ev["payout_details"], ev["idempotency"]), ([], []))
        st, own = self.call("GET", "/v1/fund_accounts_internal/fa_" + FA_A, auth=self.PSVC, headers={"X-Razorpay-Account": M_A})
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
