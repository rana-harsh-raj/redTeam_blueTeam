"""M4 boundary/identity/concurrency helpers (T14).

Everything the ``verifiers/test_m4_*.py`` files share:

* ``MerchantEdgeClient`` -- a *merchant* client that speaks HTTP Basic-Auth
  (API key id + secret) to kong-lite exactly as a real merchant SDK does, so
  the hardened edge (``KONG_ENFORCE_ROUTE_POLICY=1``) mints the passport.
  This is deliberately different from conftest's ``ps_public_client``, which
  mints its own passport and attaches the *service* credential (post-Kong
  contract). Boundary tests must observe the edge, so they use this client.
* ``classify_denial`` -- maps an HTTP response (or a broker stub) onto the
  four denial-layer fingerprints documented in T06 §2, so a test asserts the
  *layer* that denied, not only the status code.
* fixture overrides (``merchant_m1`` / ``merchant_m2`` / ``isolated_scenario``)
  bound to the *baseline* fixture trio. conftest's versions index the generated
  ``scenario-index.json`` by test node name; the M4 nodes are not in that
  index (seeds/ is owned by another workstream), so each ``test_m4_*`` module
  imports these overrides (pytest resolves the closest definition, conftest
  stays untouched).
* state-snapshot helpers used as the "victim rows unchanged" control.

Merchant key secrets are NOT baked into the verifier image and are not in its
compose ``secrets:`` list; ``verifier/m4_boundary.py`` (host runner) mounts
``ENV2_COMPOSE/secrets/merchant_arena_m{1,2}_secret.txt`` read-only at
``ARENA_MERCHANT_SECRETS_DIR`` (default ``/run/m4``). Missing secret -> the
test fails naming the fixture (never a fabricated credential).
"""
import base64
import json
import os
import re
import time
from pathlib import Path

import pytest

from . import db, trace
from .http_client import ArenaHTTPClient

CANNED_MARKERS = ("slitfa12345678", "10000000000000", "528226169544")
TENANT_SCOPE_DESCRIPTION = "The id provided does not exist"

# Denial-layer labels (T06 §2 "Distinguishing the three denial sources" + broker).
LAYER_BROKER = "broker_boundary_violation"        # RED_LOOP broker stub, no HTTP status
LAYER_GATEWAY = "gateway_no_route_404"            # kong-lite hardened classifier, before auth
LAYER_EDGE_AUTH = "edge_merchant_auth_401"        # kong-lite merchant key check
LAYER_SERVICE_AUTH = "service_basic_auth_401"     # payouts middleware/auth.go
LAYER_SERVICE_PASSPORT = "service_passport_rejected"  # payouts middleware/passport.go (401/500)
LAYER_SERVICE_TENANT = "service_tenant_scope_40x"  # payouts repo merchant filter
LAYER_SERVICE_VALIDATION = "service_validation_400"
LAYER_UPSTREAM_UNREACHABLE = "gateway_upstream_unreachable_502"
LAYER_ALLOWED = "allowed_2xx"
LAYER_OTHER = "other"


# --------------------------------------------------------------------------
# merchant-edge client
# --------------------------------------------------------------------------


def kong_url():
    return os.environ.get("KONG_LITE_URL", "http://kong-lite:8080")


def merchant_secret(merchant_key):
    """Resolve the API-key secret for fixture merchant M1/M2/M3.

    Order: ``ARENA_<KEY>_KEY_SECRET`` env, then
    ``$ARENA_MERCHANT_SECRETS_DIR/merchant_arena_<key lower>_secret.txt``.
    Returns None when neither exists (caller fails naming the fixture)."""
    env = os.environ.get("ARENA_%s_KEY_SECRET" % merchant_key.upper())
    if env:
        return env.strip()
    folder = Path(os.environ.get("ARENA_MERCHANT_SECRETS_DIR", "/run/m4"))
    path = folder / ("merchant_arena_%s_secret.txt" % merchant_key.lower())
    try:
        content = path.read_text().strip()
    except OSError:
        return None
    return content or None


def key_id_for(merchant, mode="live"):
    return "rzp_%s_%s" % (mode, merchant["merchant_id"])


class MerchantEdgeClient(ArenaHTTPClient):
    """kong-lite client authenticated as one fixture merchant (Basic key:secret).

    ``passport_jwt`` kwargs still work (they are *client-supplied* headers the
    edge is expected to overwrite -- G48 relies on that)."""

    def __init__(self, merchant, secret, base_url=None, mode="live"):
        super().__init__(base_url or kong_url(), basic_auth=(key_id_for(merchant, mode), secret))
        self.merchant = merchant
        self.key_id = key_id_for(merchant, mode)


def merchant_edge_or_fail(merchant, secret_override=None):
    secret = secret_override or merchant_secret(merchant["key"])
    if not secret:
        pytest.fail(
            "missing fixture: ARENA_%s_KEY_SECRET or %s/merchant_arena_%s_secret.txt "
            "(mount ENV2_COMPOSE/secrets/merchant_arena_%s_secret.txt read-only; see verifier/m4_boundary.py)"
            % (merchant["key"], os.environ.get("ARENA_MERCHANT_SECRETS_DIR", "/run/m4"),
               merchant["key"].lower(), merchant["key"].lower())
        )
    return MerchantEdgeClient(merchant, secret)


def raw_basic_client(user, password, base_url=None):
    """An arbitrary Basic-Auth client (used for wrong-secret / service-shaped credentials)."""
    return ArenaHTTPClient(base_url or kong_url(), basic_auth=(user, password))


# --------------------------------------------------------------------------
# fixture trio (baseline) + fixture overrides
# --------------------------------------------------------------------------


def _index():
    path = Path(os.environ.get("ARENA_SCENARIO_INDEX", "/fixtures/scenario-index.json"))
    if not path.is_file():
        pytest.fail("missing fixture: %s (seeds/generated not populated)" % path)
    return json.loads(path.read_text())


def baseline_merchant(key):
    idx = _index()
    fixture = dict(idx["baseline"][key])
    fixture["key"] = key
    return fixture


@pytest.fixture
def merchant_m1():
    """Baseline Shared/Lite merchant ARENAM00000001 (override of conftest's per-node fixture)."""
    return baseline_merchant("M1")


@pytest.fixture
def merchant_m2():
    """Baseline Direct/RBL merchant ARENAM00000002 (override of conftest's per-node fixture)."""
    return baseline_merchant("M2")


def make_isolated_scenario(scenario="hold"):
    """Build an autouse ``isolated_scenario`` override for a test module.

    Mirrors conftest.isolated_scenario (bank scenario set before, cleared after)
    but for the baseline trio, since the M4 node names are absent from the
    generated per-test index. ``scenario``: "hold" keeps payouts at
    ``initiated`` (boundary tests need no async movement); "success" lets
    the bank complete them (G34 duplicate-terminal test)."""

    @pytest.fixture(autouse=True)
    def isolated_scenario(request, mozart_mock_client):
        from . import payouts_flow as pf
        trace.select(request.node.name)
        fixtures = {k: baseline_merchant(k) for k in ("M1", "M2")}
        pf.CURRENT_MERCHANTS = fixtures
        marker = request.node.get_closest_marker("spec_id")
        trace.record("m4_test_meta", {
            "nodeid": request.node.nodeid,
            "spec_ids": list(marker.args) if marker else [],
            "fixture_scope": "baseline trio (ARENAM00000001 shared, ARENAM00000002 direct)",
            "bank_scenario": scenario,
            "kong_url": kong_url(),
        })
        trace.record("starting_fixture", fixtures)
        for merchant in fixtures.values():
            resp = mozart_mock_client.post("/_arena/scenario",
                                           body={"merchant_id": merchant["merchant_id"], "scenario": scenario})
            assert resp.status == 200, "bank scenario control failed: %s" % resp
        yield
        for merchant in fixtures.values():
            mozart_mock_client.post("/_arena/scenario", body={"merchant_id": merchant["merchant_id"], "clear": True})

    return isolated_scenario


@pytest.fixture(scope="session")
def hardened_edge():
    """Precondition for every M4 boundary test: the live edge is hardened.

    Observable: an internal path returns kong-lite's own 404 ``no_route`` with
    NO credentials at all (auth-independent => decided before auth). Under the
    frozen default (=0) the same probe reaches payouts-api and yields 401."""
    client = ArenaHTTPClient(kong_url())
    resp = client.get("/v1/payouts/manual_action")
    layer = classify_denial(resp)
    trace.record("m4_hardened_edge_probe", {"status": resp.status, "layer": layer, "body": resp.text[:300]})
    if layer != LAYER_GATEWAY:
        pytest.fail("kong-lite is not running with KONG_ENFORCE_ROUTE_POLICY=1 (probe got %s %s); "
                    "M4 boundary tests require the hardened edge" % (resp.status, resp.text[:200]))
    return True


# --------------------------------------------------------------------------
# denial-layer classification
# --------------------------------------------------------------------------


def _json_or_none(resp):
    try:
        return resp.json()
    except ValueError:
        return None


def classify_denial(resp):
    """Map a response onto one denial-layer label (see module constants).

    Broker results are plain dicts (``{"error": "boundary_violation", ...}``)
    with no ``status`` key; everything else is an ``HTTPResponse``."""
    if isinstance(resp, dict):
        if resp.get("error") == "boundary_violation" and "status" not in resp:
            return LAYER_BROKER
        return LAYER_OTHER
    body = _json_or_none(resp)
    www = {k.lower(): v for k, v in (resp.headers or {}).items()}.get("www-authenticate")
    if 200 <= resp.status < 300:
        return LAYER_ALLOWED
    if resp.status == 404 and isinstance(body, dict) and body.get("error") == "no_route":
        return LAYER_GATEWAY
    if resp.status == 502 and isinstance(body, dict) and body.get("error") == "upstream_unreachable":
        return LAYER_UPSTREAM_UNREACHABLE
    if resp.status == 401 and isinstance(body, dict) and body.get("error") == "unauthorized" and www:
        return LAYER_EDGE_AUTH
    err = body.get("error") if isinstance(body, dict) else None
    desc = err.get("description", "") if isinstance(err, dict) else ""
    code = err.get("code", "") if isinstance(err, dict) else ""
    if resp.status == 401 and www and isinstance(err, dict):
        return LAYER_SERVICE_AUTH
    if resp.status in (400, 404) and desc == TENANT_SCOPE_DESCRIPTION:
        return LAYER_SERVICE_TENANT
    if resp.status in (401, 500) and isinstance(err, dict) and not www:
        # passports rejected by payouts' PassportAuthentication (SDK parse failure -> 500
        # internal_server_error; missing token / unsupported auth type -> 401 bad_request_error)
        return LAYER_SERVICE_PASSPORT
    if resp.status == 400 and code:
        return LAYER_SERVICE_VALIDATION
    return LAYER_OTHER


def observe(label, resp, expected_layer=None, **extra):
    """Record one boundary observation to the trace (consumed by m4_summarize.py)
    and return the observed layer."""
    layer = classify_denial(resp)
    rec = {"label": label, "observed_layer": layer, "expected_layer": expected_layer,
           "matches_expected": (expected_layer is None or layer == expected_layer)}
    if isinstance(resp, dict):
        rec["broker_result"] = resp
    else:
        rec["status"] = resp.status
        rec["www_authenticate"] = {k.lower(): v for k, v in (resp.headers or {}).items()}.get("www-authenticate")
        rec["body"] = resp.text[:1000]
    rec.update(extra)
    trace.record("m4_observation", rec)
    return layer


def has_canned_markers(text):
    return any(m in (text or "") for m in CANNED_MARKERS)


# --------------------------------------------------------------------------
# internal-route enumeration (G49)
# --------------------------------------------------------------------------

# T06 §"Handoff instructions" probe set (explicit, always included).
T06_INTERNAL_PROBES = [
    ("GET", "/v1/payouts/fetch_multiple?id=pout_1234&auth_type=proxy"),
    ("POST", "/v1/payouts/manual_action"),
    ("POST", "/v1/payouts/rzp_fees_payout"),
    ("GET", "/v1/payouts/analytics"),
    ("POST", "/v1/payouts/payouts_internal/pout_x/approve"),
    ("GET", "/v1/payouts/payouts_internal/pout_x"),
    ("POST", "/v1/payouts/payouts_internal"),
    ("POST", "/v1/payouts/bulk"),
    ("POST", "/v1/payouts/transfer_status_webhook"),
    ("GET", "/v1/inflight_reservations?merchant_id=ARENAM00000002&balance_id=ARENABAL000002"),
    ("GET", "/v1/internal/non_terminal_payouts/ARENABAL000002"),
    ("POST", "/v1/admin/merchant-configuration"),
    ("GET", "/v1/admin/dev_admin"),
    ("POST", "/v1/workflow/state"),
    ("POST", "/v1/cron/process_queued_payouts"),
    ("POST", "/v1/banking_account_statement/payout_update"),
    ("POST", "/v1/notify/health/update"),
    ("GET", "/v1/contacts"),
    ("POST", "/twirp/ledger/x"),
]


def _regex_to_sample_path(rx):
    """Turn a registry match regex into one concrete probe path."""
    p = rx.strip("^$")
    p = p.replace("(/.*)?", "/probe").replace("[^/]+", "pout_x").replace(".*", "probe")
    p = re.sub(r"\\(.)", r"\1", p)
    return p


def registry_internal_probes():
    """Enumerate one concrete (method, path) per denied-route regex in
    RED_LOOP/registry/merchant-gateway-routes.json (mounted read-only at
    ``M4_REDLOOP_DIR``). Returns [] when the registry is not mounted."""
    root = Path(os.environ.get("M4_REDLOOP_DIR", "/redloop"))
    path = root / "registry" / "merchant-gateway-routes.json"
    if not path.is_file():
        return []
    reg = json.loads(path.read_text())
    probes = []
    for group in ("internal_denied", "admin_denied", "workflow_denied", "other_internal_denied"):
        for entry in reg.get(group, []):
            for rx in entry.get("matches", []):
                probes.append(("GET", _regex_to_sample_path(rx)))
                probes.append(("POST", _regex_to_sample_path(rx)))
    for entry in reg.get("not_implemented_as_inbound", []):
        prefix = entry.get("prefix")
        if prefix and prefix != "/v1/fund_accounts":  # validations sub-route is public; probe the CRUD root only
            probes.append(("GET", prefix))
        elif prefix:
            probes.append(("GET", prefix + "/fa_x"))
    seen, out = set(), []
    for m, p in probes:
        if (m, p) not in seen:
            seen.add((m, p))
            out.append((m, p))
    return out


def all_internal_probes():
    seen, out = set(), []
    for m, p in T06_INTERNAL_PROBES + registry_internal_probes():
        if (m, p) not in seen:
            seen.add((m, p))
            out.append((m, p))
    return out


# --------------------------------------------------------------------------
# state snapshots (victim-unchanged controls)
# --------------------------------------------------------------------------


def payout_row_state(payouts_mysql, payout_id):
    from .payouts_flow import db_id
    row = db.fetchone(payouts_mysql,
                      "SELECT id, merchant_id, balance_id, status, amount, updated_at, fts_transfer_id, utr "
                      "FROM payouts WHERE id=%s", (db_id(payout_id),))
    return dict(row) if row else None


def merchant_state(payouts_mysql, merchant, ledger_pg=None, xbalances_mysql=None):
    """Stable, comparable snapshot of one merchant's money-relevant rows."""
    mid = merchant["merchant_id"]
    counts = db.fetchall(payouts_mysql,
                         "SELECT status, COUNT(*) AS c FROM payouts WHERE merchant_id=%s GROUP BY status", (mid,))
    snap = {"merchant_id": mid,
            "payout_count": sum(int(r["c"]) for r in counts),
            "payout_status_counts": {r["status"]: int(r["c"]) for r in counts},
            "banking_account": None, "ledger_balance": None, "xbalances_balance": None}
    ba = db.fetchone(payouts_mysql,
                     "SELECT id, merchant_id, balance_id, account_number, account_type, channel "
                     "FROM banking_accounts WHERE merchant_id=%s AND balance_id=%s", (mid, merchant["balance_id"]))
    snap["banking_account"] = dict(ba) if ba else None
    if ledger_pg is not None and merchant.get("ledger_balance_account_id"):
        from .payouts_flow import get_account_balance
        bal = get_account_balance(ledger_pg, mid)
        snap["ledger_balance"] = str(bal) if bal is not None else None
    if xbalances_mysql is not None:
        try:
            xb = db.fetchone(xbalances_mysql, "SELECT * FROM balance WHERE id=%s", (merchant["balance_id"],))
            if xb:
                snap["xbalances_balance"] = {k: str(v) for k, v in xb.items()
                                             if k in ("id", "merchant_id", "balance", "account_number", "updated_at")}
        except Exception as exc:  # noqa: BLE001 -- schema differences are recorded, not fatal
            snap["xbalances_balance"] = {"error": str(exc)[:200]}
    trace.record("m4_state_snapshot", snap)
    return snap


def assert_state_unchanged(before, after, what):
    # payout_status_counts is deliberately EXCLUDED: the arena's async workers advance a merchant's
    # own held payouts (created -> initiated) during the test window, which is the victim's own
    # processing, not a cross-tenant effect. The money-relevant, attacker-invariant fields are the
    # banking account, the ledger / x-balances balance, and the total payout count (a new victim
    # payout would require the attacker to create one under B, which is exactly what must not happen).
    keys = ("payout_count", "banking_account", "ledger_balance", "xbalances_balance")
    diff = {k: (before.get(k), after.get(k)) for k in keys if before.get(k) != after.get(k)}
    assert not diff, "%s changed across the denied action: %r" % (what, diff)


# --------------------------------------------------------------------------
# passport forgery shapes (G48) -- none of these can validate against the RS256 kid set
# --------------------------------------------------------------------------


def _b64url(obj):
    raw = obj if isinstance(obj, bytes) else json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def forged_passports(victim_merchant_id, genuine_jwt=None):
    now = int(time.time())
    claims = {"iss": "https://identity.arena.invalid", "sub": "urn:arena:payouts", "iat": now - 10,
              "nbf": now - 10, "exp": now + 300, "identified": True, "authenticated": True, "mode": "live",
              "org": "ARENAORG000001", "product": "banking",
              "consumer": {"id": victim_merchant_id, "type": "merchant"}}
    out = {
        "alg_none": _b64url({"typ": "JWT", "alg": "none"}) + "." + _b64url(claims) + ".",
        "hs256_shared_secret": _b64url({"typ": "JWT", "alg": "HS256", "kid": "arena-passport-1"}) + "." +
                               _b64url(claims) + "." + _b64url(b"\x00" * 32),
        "rs256_wrong_kid": _b64url({"typ": "JWT", "alg": "RS256", "kid": "attacker-kid"}) + "." +
                           _b64url(claims) + "." + _b64url(b"\x01" * 256),
        "rs256_expired_unsigned": _b64url({"typ": "JWT", "alg": "RS256", "kid": "arena-passport-1"}) + "." +
                                  _b64url(dict(claims, exp=now - 3600, iat=now - 7200)) + "." + _b64url(b"\x02" * 256),
    }
    if genuine_jwt:
        # genuine header+signature, tampered payload (consumer swapped) -> signature no longer verifies
        head, _, sig = genuine_jwt.split(".")
        out["genuine_sig_tampered_consumer"] = head + "." + _b64url(claims) + "." + sig
    return out


def service_shaped_basic_credentials():
    """Credential *shapes* a client might guess for payouts' service identities
    (arena.toml [auth] usernames). Values are placeholders, never real secrets."""
    return [("api", "api"), ("api", "auth_api_payouts"), ("fts", "fts"), ("rzp_live", "auth_monolith_shared"),
            ("x_balances", "x_balances"), ("fast_cron", "fast_cron"), ("merchant_configuration", "x")]
