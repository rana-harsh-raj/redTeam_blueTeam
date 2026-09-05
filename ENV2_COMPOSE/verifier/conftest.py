"""Live Env2 fixtures. Missing required services or generated fixtures fail visibly.
Each node receives isolated synthetic merchants and explicit bank behavior.
"""
import os
import json
from pathlib import Path

import pytest

from helpers import creds, db
from helpers.http_client import ArenaHTTPClient

# --------------------------------------------------------------------------
# marker registration
# --------------------------------------------------------------------------


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "status(level): the VERIFIER_SPEC.md validation-status ceiling this test is capable of reaching "
        "when every substitute it names is real/live and its assertions pass "
        "(CODE_CANDIDATE|CROSS_SERVICE_CONFIRMED|END_TO_END_CONFIRMED|PRODUCTION_REPRESENTATIVE|"
        "BLOCKED_BY_FIDELITY_GAP).",
    )
    config.addinivalue_line("markers", "spec_id(id): the VERIFIER_SPEC.md verifier id this test implements, e.g. 'V1'.")


# --------------------------------------------------------------------------
# service endpoint fixtures (no reachability check here -- individual
# fixtures/tests that need a live call check .health_ok()/catch
# ServiceUnreachable and fail explicitly, so a verifier reports the
# specific dependency it actually touches, not the whole topology)
# --------------------------------------------------------------------------


def _url(env_name, default):
    return os.environ.get(env_name, default)


@pytest.fixture(scope="session")
def ps_service_auth():
    auth = creds.resolve_basic_auth("PS_SERVICE")
    if not auth:
        pytest.fail("missing fixture: PS_SERVICE_BASIC_AUTH (payouts-api cred.API/cred.Workflow pair)")
    return auth


@pytest.fixture(scope="session")
def ps_fastcron_auth():
    auth = creds.resolve_basic_auth("PS_FASTCRON")
    if not auth:
        pytest.fail("missing fixture: PS_FASTCRON_BASIC_AUTH (payouts-api cred.FastCron pair)")
    return auth


@pytest.fixture(scope="session")
def ledger_auth():
    auth = creds.resolve_basic_auth("LEDGER")
    if not auth:
        pytest.fail("missing fixture: LEDGER_BASIC_AUTH (ledger-api [auth] service credential)")
    return auth


@pytest.fixture(scope="session")
def fts_auth():
    auth = creds.resolve_basic_auth("FTS")
    if not auth:
        pytest.fail("missing fixture: FTS_BASIC_AUTH (fts-web PayoutsService/\"PS\" identity)")
    return auth


@pytest.fixture(scope="session")
def monolith_auth():
    auth = creds.resolve_basic_auth("MONOLITH")
    if not auth:
        pytest.fail("missing fixture: MONOLITH_BASIC_AUTH (monolith-stub secrets/monolith_basic_auth)")
    return auth


@pytest.fixture(scope="session")
def kong_client():
    return ArenaHTTPClient(_url("KONG_LITE_URL", "http://kong-lite:8000"))


@pytest.fixture(scope="session")
def ps_public_client(kong_client, ps_service_auth):
    """/v1/payouts* via kong-lite, with the PS_SERVICE credential attached --
    see VERIFIER_SPEC.md gap #9 for why this stands in for the missing
    Kong/monolith passport-minting translation."""
    # PS_PUBLIC_URL (arena: payouts-api directly) -- the verifier mints its own passport and attaches
    # the PS service credential, i.e. it speaks the post-Kong contract; kong-lite's own API-key ->
    # passport path is exercised separately (kong-lite /_arena/mint + merchant keys).
    public_url = os.environ.get("PS_PUBLIC_URL")
    if public_url:
        return ArenaHTTPClient(public_url, basic_auth=ps_service_auth)
    kong_client.basic_auth = ps_service_auth
    return kong_client


@pytest.fixture(scope="session")
def ps_internal_client(ps_service_auth):
    """Direct-to-payouts-api client for internal routes (transfer_status_webhook,
    inflight_reservations, update_payouts_with_fts) -- these are never routed
    through kong-lite's route table in production either (Flow I: FTS calls
    payouts-api directly)."""
    return ArenaHTTPClient(_url("PAYOUTS_API_URL", "http://payouts-api:9400"), basic_auth=ps_service_auth)


@pytest.fixture(scope="session")
def ps_fastcron_client(ps_fastcron_auth):
    return ArenaHTTPClient(_url("PAYOUTS_API_URL", "http://payouts-api:9400"), basic_auth=ps_fastcron_auth)


@pytest.fixture(scope="session")
def ledger_client(ledger_auth):
    return ArenaHTTPClient(_url("LEDGER_API_URL", "http://ledger-api:8080"), basic_auth=ledger_auth)


@pytest.fixture(scope="session")
def fts_client(fts_auth):
    return ArenaHTTPClient(_url("FTS_WEB_URL", "http://fts-web:80"), basic_auth=fts_auth)


@pytest.fixture(scope="session")
def cfa_client():
    return ArenaHTTPClient(_url("CFA_URL", "http://cfa-server:8081"))


@pytest.fixture(scope="session")
def xbalances_client():
    return ArenaHTTPClient(_url("XBALANCES_URL", "http://xbalances-server:8080"))


@pytest.fixture(scope="session")
def monolith_stub_client(monolith_auth):
    return ArenaHTTPClient(_url("MONOLITH_STUB_URL", "http://monolith-stub:8080"), basic_auth=monolith_auth)


@pytest.fixture(scope="session")
def dcs_stub_client():
    return ArenaHTTPClient(_url("DCS_STUB_URL", "http://dcs-stub:8080"))


@pytest.fixture(scope="session")
def splitz_stub_client():
    return ArenaHTTPClient(_url("SPLITZ_STUB_URL", "http://splitz-stub:8080"))


@pytest.fixture(scope="session")
def shield_stub_client():
    return ArenaHTTPClient(_url("SHIELD_STUB_URL", "http://shield-stub:8080"))


@pytest.fixture(scope="session")
def pricing_stub_client():
    return ArenaHTTPClient(_url("PRICING_STUB_URL", "http://pricing-stub:8080"))


@pytest.fixture(scope="session")
def stork_capture_client():
    return ArenaHTTPClient(_url("STORK_CAPTURE_URL", "http://stork-capture:8080"))


@pytest.fixture(scope="session")
def asv_stub_client():
    return ArenaHTTPClient(_url("ASV_STUB_URL", "http://asv-stub:8080"))


@pytest.fixture(scope="session")
def mozart_mock_client():
    """Explicit synthetic bank endpoint with merchant/attempt controls."""
    return ArenaHTTPClient(_url("MOZART_MOCK_URL", "http://mozart-mock:8085"))


def require_reachable(client, name):
    """Call from inside a test (not a fixture) right before a hard dependency
    on a stub's liveness -- e.g. shield-stub's latency-injection probe in
    V22. Fails with a clear reason if the health check fails or the service
    is entirely unreachable."""
    if not client.health_ok():
        pytest.fail("missing fixture: %s unreachable at %s" % (name, client.base_url))


# --------------------------------------------------------------------------
# datastore fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def payouts_mysql():
    try:
        return db.connect_mysql(
            "PAYOUTS_MYSQL_HOST", "PAYOUTS_MYSQL_PORT", "PAYOUTS_MYSQL_USER",
            "PAYOUTS_MYSQL_PASSWORD", "PAYOUTS_MYSQL_DB",
            {"host": "mysql-payouts", "port": 3306, "user": "root", "password": "", "db": "payouts"},
        )
    except db.ConnectionUnavailable as exc:
        pytest.fail("missing fixture: payouts MySQL (%s)" % exc)


@pytest.fixture(scope="session")
def ledger_pg():
    try:
        return db.connect_postgres(
            "LEDGER_PG_HOST", "LEDGER_PG_PORT", "LEDGER_PG_USER", "LEDGER_PG_PASSWORD", "LEDGER_PG_DB",
            {"host": "postgres-ledger", "port": 5432, "user": "postgres", "password": "", "db": "ledger"},
        )
    except db.ConnectionUnavailable as exc:
        pytest.fail("missing fixture: ledger Postgres (%s)" % exc)


@pytest.fixture(scope="session")
def fts_mysql():
    try:
        return db.connect_mysql(
            "FTS_MYSQL_HOST", "FTS_MYSQL_PORT", "FTS_MYSQL_USER", "FTS_MYSQL_PASSWORD", "FTS_MYSQL_DB",
            {"host": "mysql-fts", "port": 3306, "user": "root", "password": "", "db": "fts"},
        )
    except db.ConnectionUnavailable as exc:
        pytest.fail("missing fixture: fts MySQL (%s)" % exc)


@pytest.fixture(scope="session")
def xbalances_mysql():
    try:
        return db.connect_mysql(
            "XBALANCES_MYSQL_HOST", "XBALANCES_MYSQL_PORT", "XBALANCES_MYSQL_USER",
            "XBALANCES_MYSQL_PASSWORD", "XBALANCES_MYSQL_DB",
            {"host": "mysql-xbalances", "port": 3306, "user": "root", "password": "", "db": "xbalances"},
        )
    except db.ConnectionUnavailable as exc:
        pytest.fail("missing fixture: x-balances MySQL (%s)" % exc)


@pytest.fixture(scope="session")
def cfa_mongo():
    try:
        return db.connect_mongo("CFA_MONGO_URI", "mongodb://mongo-cfa:27017/cfa")
    except db.ConnectionUnavailable as exc:
        pytest.fail("missing fixture: cfa Mongo (%s)" % exc)


@pytest.fixture(scope="session")
def payouts_redis():
    try:
        return db.connect_redis("PAYOUTS_REDIS_URL", "redis://redis:6379/0")
    except db.ConnectionUnavailable as exc:
        pytest.fail("missing fixture: payouts Redis (%s)" % exc)


@pytest.fixture(scope="session")
def fts_redis():
    try:
        return db.connect_redis("FTS_REDIS_URL", "redis://redis:6379/1")
    except db.ConnectionUnavailable as exc:
        pytest.fail("missing fixture: fts Redis (%s)" % exc)


# --------------------------------------------------------------------------
# merchant fixtures (dcs-stub/CONTRACT.md: ARENA_M1 shared, ARENA_M2 direct/RBL
# in_flight_reservation_enabled=on, ARENA_M3 workflow-enabled)
# --------------------------------------------------------------------------


def _merchant(key, request=None):
    index_path = Path(os.environ.get("ARENA_SCENARIO_INDEX", "/fixtures/scenario-index.json"))
    if request is not None and index_path.is_file():
        index = json.loads(index_path.read_text())
        fixture = dict(index["verifiers"][request.node.name][key])
        fixture["key"] = key
        return fixture

    merchant_id = os.environ.get("ARENA_%s_MERCHANT_ID" % key)
    if not merchant_id:
        pytest.fail("missing fixture: ARENA_%s_MERCHANT_ID (seeds/ not populated yet)" % key)
    return {
        "key": key,
        "merchant_id": merchant_id,
        "balance_id": os.environ.get("ARENA_%s_BALANCE_ID" % key),
        "fund_account_id": os.environ.get("ARENA_%s_FUND_ACCOUNT_ID" % key),
        "account_number": os.environ.get("ARENA_%s_ACCOUNT_NUMBER" % key),
    }


@pytest.fixture
def merchant_m1(request):
    """Shared/Lite balance, ledger-backed."""
    return _merchant("M1", request)


@pytest.fixture
def merchant_m2(request):
    """Direct/current account, RBL, in_flight_reservation_enabled=on."""
    return _merchant("M2", request)


@pytest.fixture
def merchant_m3(request):
    """Workflow-enabled (enable_payout_workflow=on) -- not exercised by the
    Env 2 verifiers in this package (workflow/approval is Env 1 scope), kept
    here for forward compatibility."""
    return _merchant("M3", request)


# Passport JWT resolution lives in helpers.payouts_flow.passport_or_skip --
# merchant identity varies per test even within one file (see V20), so it is
# called from inside test bodies rather than wired as a fixture here.


@pytest.fixture(autouse=True)
def isolated_scenario(request, mozart_mock_client):
    """Explicit bank state is configured before any payout is created."""
    from helpers import trace, payouts_flow as pf
    trace.select(request.node.name)
    index_path = Path(os.environ.get("ARENA_SCENARIO_INDEX", "/fixtures/scenario-index.json"))
    if not index_path.is_file():
        pytest.fail("isolated synthetic scenario-index.json is required")
    fixtures = json.loads(index_path.read_text())["verifiers"][request.node.name]
    pf.CURRENT_MERCHANTS = fixtures
    trace.record("starting_fixture", fixtures)
    scenario = "success" if request.node.name == "test_payout_processed_journal_mirrors_ledger" else "hold"
    if request.node.name in ("test_payout_failed_direct_account_no_reversal_no_ledger", "test_payout_reversed_journal_and_reversal_entity", "test_failed_webhook_remaps_differently_by_account_type"): scenario = "failure"
    for merchant in fixtures.values():
        resp = mozart_mock_client.post("/_arena/scenario", body={"merchant_id":merchant["merchant_id"],"scenario":scenario})
        assert resp.status == 200, "bank scenario control failed: %s" % resp
    yield
    # Each fixture has a unique namespace; pending work cannot spend another
    # test's balance. Clear only the controls owned by this test.
    for merchant in fixtures.values():
        mozart_mock_client.post("/_arena/scenario", body={"merchant_id":merchant["merchant_id"],"clear":True})


def pytest_runtest_logreport(report):
    from helpers import trace
    trace.record("test_result", {"nodeid":report.nodeid,"when":report.when,"outcome":report.outcome,
                                  "duration":report.duration,"reason":str(report.longrepr) if report.failed or report.skipped else None})


@pytest.fixture(scope="session")
def ledger_gate_client():
    return ArenaHTTPClient(_url("LEDGER_GATE_URL", "http://ledger-gate:8080"))


@pytest.fixture(scope="session")
def merchant_sink_client():
    return ArenaHTTPClient(_url("MERCHANT_SINK_URL", "http://merchant-webhook-sink:8080"))
