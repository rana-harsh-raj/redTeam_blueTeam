#!/usr/bin/env sh
# Runs the Env 2 verifier suite. Any args are passed through to pytest
# verbatim (e.g. `run.sh -k v01 -x`, `run.sh --collect-only`).
#
# Every verifier is written to skip cleanly -- not error -- when a fixture,
# credential or substitute it needs is missing (see conftest.py's module
# docstring and VERIFIER_SPEC.md's "Known scaffold gaps" section). A run
# against a partially-built arena is therefore expected to show a long list
# of skips, not failures; treat a FAIL as a real finding and a SKIP as "come
# back once that fixture exists."
#
# -rs prints the skip reason for every skipped test (the whole point of the
# "tolerant of missing fixtures" design is that those reasons are readable).
set -eu

cd "$(dirname "$0")"

# Datastore credentials: per-arena generated secrets mounted by compose (never baked into the image).
rs() { [ -f "/run/secrets/$1" ] && cat "/run/secrets/$1" || true; }
export PAYOUTS_MYSQL_PASSWORD="${PAYOUTS_MYSQL_PASSWORD:-$(rs mysql_payouts_root_password)}"
export FTS_MYSQL_PASSWORD="${FTS_MYSQL_PASSWORD:-$(rs mysql_fts_root_password)}"
export XBALANCES_MYSQL_PASSWORD="${XBALANCES_MYSQL_PASSWORD:-$(rs mysql_xbalances_root_password)}"
export XBALANCES_MYSQL_DB="${XBALANCES_MYSQL_DB:-rx_balances_local}"
export LEDGER_PG_USER="${LEDGER_PG_USER:-ledger}"
export LEDGER_PG_PASSWORD="${LEDGER_PG_PASSWORD:-$(rs postgres_ledger_password)}"
_mpw="$(rs mongo_cfa_root_password)"
[ -n "$_mpw" ] && export CFA_MONGO_URI="${CFA_MONGO_URI:-mongodb://cfa_root:${_mpw}@mongo-cfa:27017/cfa?authSource=admin}"
exec python3 -m pytest -rs -v --tb=short "$@" verifiers
