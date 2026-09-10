"""Offline tests: kong-lite's passport claim shapes.

Covers the M6 I5 gateway defect -- kong-lite could only mint
consumer {type: "merchant"} passports, which goutils passport
helpers.go:207 maps to LegacyAuthTypePrivate, so payouts'
internal/app/payouts/validation.go ValidateCancel refused every cancel of a
SCHEDULED payout (CancelScheduledPayoutInvalidAuth) and
journey:scheduled-payouts/cancel_via_dashboard was permanently BLOCKED.

The dashboard/proxy shape asserted here is the one the real edge emits
(edge/kong-plugins/kong-plugin-upstream-jwt/kong/plugins/upstream-jwt/access.lua
:135-152) for the monolith's
BasicAuth::setPassportImpersonationClaims(PASSPORT_IMPERSONATION_TYPE_USER_MERCHANT,
merchantId) (api/app/Http/BasicAuth/BasicAuth.php:1246-1248, constant at :143).

Nothing here touches docker, the arena or any network.
"""
import base64
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUBSTITUTES = REPO / "ENV2_COMPOSE" / "substitutes"
KONG_LITE = SUBSTITUTES / "kong-lite"


def _load_server(tmpdir, key_file):
    """Import kong-lite's server.py in-process with arena-free env."""
    for p in (str(KONG_LITE), str(SUBSTITUTES)):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ["KONG_MERCHANTS_FILE"] = str(Path(tmpdir) / "no-such-merchants.json")
    os.environ["KONG_MERCHANT_SECRETS_DIR"] = str(tmpdir)
    os.environ["PS_API_AUTH_PASS_FILE"] = str(Path(tmpdir) / "no-such-secret")
    os.environ["PASSPORT_PRIVATE_KEY_FILE"] = str(key_file or Path(tmpdir) / "no-such-key")
    sys.modules.pop("server", None)
    return importlib.import_module("server")


def _make_key(tmpdir):
    """Throwaway 2048-bit RSA keypair for this test run only. None if no openssl."""
    if not shutil.which("openssl"):
        return None, None
    priv = Path(tmpdir) / "test_passport_private.pem"
    pub = Path(tmpdir) / "test_passport_public.pem"
    try:
        subprocess.run(["openssl", "genpkey", "-algorithm", "RSA",
                        "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(priv)],
                       check=True, capture_output=True, timeout=60)
        subprocess.run(["openssl", "rsa", "-in", str(priv), "-pubout", "-out", str(pub)],
                       check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None, None
    return priv, pub


def _b64url_decode(seg):
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def legacy_auth_type(claims):
    """A faithful port of goutils/passport helpers.go:200-215 GetLegacyAuthType,
    restricted to the branches kong-lite can produce. Asserting against this is
    what makes the claim-shape test meaningful rather than cosmetic."""
    consumer = claims.get("consumer")
    oauth = claims.get("oauth")
    impersonation = claims.get("impersonation")
    if not claims.get("identified"):
        return "direct"
    if not claims.get("authenticated"):
        return "public"
    if consumer and consumer.get("type") == "admin":
        return "admin"
    if consumer and consumer.get("type") == "application":
        return "privilege"
    if consumer and consumer.get("type") == "merchant" and oauth is None:
        return "private"
    if (consumer and consumer.get("type") == "user"
            and impersonation is not None and impersonation.get("type") == "user_merchant"):
        return "proxy"
    return None


MERCHANT = "ARENAM91043600"
USER = "ARENAUSR000001"


class KongPassportClaimsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.tmpdir = cls._tmp.name
        cls.priv, cls.pub = _make_key(cls.tmpdir)
        cls.server = _load_server(cls.tmpdir, cls.priv)

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("server", None)
        cls._tmp.cleanup()

    # -- default (unchanged) behaviour ---------------------------------------
    def test_default_shape_is_the_merchant_private_passport(self):
        c = self.server._passport_claims(MERCHANT, "live", [])
        self.assertEqual(c["consumer"], {"id": MERCHANT, "type": "merchant"})
        self.assertNotIn("impersonation", c)
        self.assertNotIn("roles", c)
        self.assertTrue(c["identified"] and c["authenticated"])
        self.assertEqual(c["mode"], "live")
        self.assertEqual(c["exp"] - c["iat"], self.server.PASSPORT_TTL_SEC)
        self.assertEqual(legacy_auth_type(c), "private")

    def test_default_shape_keeps_seed_roles(self):
        c = self.server._passport_claims(MERCHANT, "test", ["view_only"])
        self.assertEqual(c["roles"], ["view_only"])
        self.assertEqual(legacy_auth_type(c), "private")

    # -- opt-in dashboard/proxy shape ----------------------------------------
    def test_dashboard_shape_yields_LegacyAuthTypeProxy(self):
        c = self.server._passport_claims(MERCHANT, "live", ["owner"], dashboard_user_id=USER)
        self.assertEqual(c["consumer"], {"id": USER, "type": "user"})
        self.assertEqual(c["impersonation"], {
            "type": "user_merchant",
            "consumer": {"id": MERCHANT, "type": "merchant"},
        })
        self.assertEqual(c["roles"], ["owner"])
        self.assertEqual(legacy_auth_type(c), "proxy")

    def test_merchant_identity_moves_into_impersonation_not_consumer(self):
        """internal/auth/authHelper.go getMerchantIdFromPassport reads the merchant
        from impersonation.Consumer for every non-private auth type."""
        c = self.server._passport_claims(MERCHANT, "live", [], dashboard_user_id=USER)
        self.assertEqual(c["impersonation"]["consumer"]["id"], MERCHANT)
        self.assertNotEqual(c["consumer"]["id"], MERCHANT)

    # -- header handling (arena control, opt-in, fail-safe) -------------------
    def test_headers_absent_changes_nothing(self):
        self.assertEqual(self.server._dashboard_override({}), (None, []))

    def test_wrong_consumer_type_is_ignored(self):
        h = {self.server.ARENA_CONSUMER_TYPE_HEADER: "admin",
             self.server.ARENA_USER_ID_HEADER: USER}
        self.assertEqual(self.server._dashboard_override(h), (None, []))

    def test_missing_or_malformed_user_id_falls_back_to_the_merchant_passport(self):
        base = {self.server.ARENA_CONSUMER_TYPE_HEADER: "user"}
        self.assertEqual(self.server._dashboard_override(base), (None, []))
        for bad in ("", "   ", 'a"b', "x" * 41, "a b", "a\nb"):
            h = dict(base, **{self.server.ARENA_USER_ID_HEADER: bad})
            self.assertEqual(self.server._dashboard_override(h), (None, []), bad)

    def test_user_and_role_are_honoured(self):
        h = {self.server.ARENA_CONSUMER_TYPE_HEADER: "User",
             self.server.ARENA_USER_ID_HEADER: USER,
             self.server.ARENA_USER_ROLE_HEADER: "owner"}
        self.assertEqual(self.server._dashboard_override(h), (USER, ["owner"]))

    def test_control_headers_are_stripped_before_forwarding(self):
        """_proxy compares lower-cased header names against its drop-set."""
        for h in self.server.ARENA_CONTROL_HEADERS:
            self.assertEqual(h, h.lower())
        self.assertEqual(self.server.ARENA_CONTROL_HEADERS, {
            self.server.ARENA_CONSUMER_TYPE_HEADER.lower(),
            self.server.ARENA_USER_ID_HEADER.lower(),
            self.server.ARENA_USER_ROLE_HEADER.lower()})

    # -- end-to-end: decode the actual signed JWT ----------------------------
    def test_minted_jwt_decodes_to_the_dashboard_claim_shape(self):
        if self.priv is None:
            self.skipTest("openssl unavailable; claim-shape tests above still cover the fix")
        self.assertIsNotNone(self.server._PASSPORT_N, "signing key was not loaded")
        token = self.server._mint_passport_jwt(MERCHANT, "live", [], dashboard_user_id=USER)
        self.assertIsNotNone(token)
        h_b64, p_b64, s_b64 = token.split(".")
        header = json.loads(_b64url_decode(h_b64))
        payload = json.loads(_b64url_decode(p_b64))
        self.assertEqual(header, {"typ": "JWT", "alg": "RS256",
                                  "kid": self.server.PASSPORT_IDENTIFIER})
        self.assertEqual(payload["consumer"], {"id": USER, "type": "user"})
        self.assertEqual(payload["impersonation"]["type"], "user_merchant")
        self.assertEqual(payload["impersonation"]["consumer"],
                         {"id": MERCHANT, "type": "merchant"})
        self.assertEqual(legacy_auth_type(payload), "proxy")

        from _common.rsa_sign import parse_rsa_public_key_der, pem_to_der, \
            rsa_verify_pkcs1v15_sha256
        n, e = parse_rsa_public_key_der(pem_to_der(self.pub.read_text()))
        size = (n.bit_length() + 7) // 8
        self.assertTrue(rsa_verify_pkcs1v15_sha256(
            ("%s.%s" % (h_b64, p_b64)).encode(), _b64url_decode(s_b64), n, e, size))

    def test_minted_jwt_default_path_is_unchanged(self):
        if self.priv is None:
            self.skipTest("openssl unavailable")
        token = self.server._mint_passport_jwt(MERCHANT, "test", ["view_only"])
        payload = json.loads(_b64url_decode(token.split(".")[1]))
        self.assertEqual(payload["consumer"], {"id": MERCHANT, "type": "merchant"})
        self.assertNotIn("impersonation", payload)
        self.assertEqual(payload["mode"], "test")
        self.assertEqual(legacy_auth_type(payload), "private")


if __name__ == "__main__":
    unittest.main()
