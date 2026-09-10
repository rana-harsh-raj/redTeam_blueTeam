#!/usr/bin/env sh
# Runs the Env 2 verifier suite. Any args are passed through to pytest
# verbatim (e.g. `run.sh -k v01 -x`, `run.sh --collect-only`).
#
# Missing required fixtures and failed invariants are visible failures.
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
python3 network_check.py
exec python3 -m pytest -p no:cacheprovider -rs -v --tb=short "$@" verifiers
