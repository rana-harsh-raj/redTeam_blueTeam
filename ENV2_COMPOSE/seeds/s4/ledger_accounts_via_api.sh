#!/usr/bin/env bash
# Recommended (real production mechanism) way to bootstrap Env2's ledger accounts: call
# AccountAPI.CreateOnEvent against the booted ledger `api` binary, exactly as payouts/fts do in
# production. Prefer this over ledger.sql's raw-SQL fallback -- it exercises the same
# account-discovery/matching logic real traffic uses, so accounts are guaranteed to resolve.
#
# Source: ledger/internal/account/server.go:137-186 (CreateOnEvent handler + Ledger-Tenant header),
# ledger/rpc/ledger/account/v1/account_api.pb.go:171-186 (request shape), ledger/scripts/oncall/
# create_merchant_account/main.go and create_fts_account/main.go (real request bodies), ledger/
# test/functional/testdata/account.go:1211-1262 (worked request/response example).
#
# Usage: LEDGER_BASE_URL=http://ledger-api:8080 LEDGER_AUTH="Basic <base64 payouts_key:secret>" \
#        ./ledger_accounts_via_api.sh

set -euo pipefail

LEDGER_BASE_URL="${LEDGER_BASE_URL:?set LEDGER_BASE_URL, e.g. http://ledger-api:8080}"
LEDGER_AUTH="${LEDGER_AUTH:?set LEDGER_AUTH, e.g. 'Basic base64(payouts_key:secret)'}"
ENDPOINT="${LEDGER_BASE_URL}/twirp/rzp.ledger.account.v1.AccountAPI/CreateOnEvent"

call() {
  local body="$1"
  curl -sS -X POST "$ENDPOINT" \
    -H "Content-Type: application/json" \
    -H "Ledger-Tenant: X" \
    -H "Authorization: ${LEDGER_AUTH}" \
    -d "$body"
  echo
}

echo "== M1 (ARENAM00000001, Shared) -- MerchantBalance/VendorPayable/CommissionIncome/OutputGST via shared_merchant_onboarding =="
call '{
  "merchant_id": "ARENAM00000001",
  "merchant_balance_opening_balance": "10000000",
  "currency": "INR",
  "events": [{
    "name": "shared_merchant_onboarding",
    "description": "Env2 arena onboarding for ARENAM00000001",
    "entities": { "banking_account_id": ["ARENABA0000001"] }
  }]
}'

echo "== M3 (ARENAM00000003, Shared/workflow) -- same event =="
call '{
  "merchant_id": "ARENAM00000003",
  "merchant_balance_opening_balance": "10000000",
  "currency": "INR",
  "events": [{
    "name": "shared_merchant_onboarding",
    "description": "Env2 arena onboarding for ARENAM00000003",
    "entities": { "banking_account_id": ["ARENABA0000003"] }
  }]
}'

echo "== Shared RBL pool nodal account (fts_fund_account_id=900001, used by M1+M3) via nodal_pool_account_onboarding =="
call '{
  "merchant_id": "ARENAM00000000",
  "account_name": "ARENA RZPX POOL - RBL - 900001",
  "currency": "INR",
  "events": [{
    "name": "nodal_pool_account_onboarding",
    "description": "Env2 arena shared RBL pool nodal account",
    "entities": { "fts_fund_account_id": ["900001"] }
  }]
}'

echo "== M2 (ARENAM00000002, Direct) dedicated current account (fts_fund_account_id=900002) =="
echo "NOTE: per findings/13_fts_payouts_status_path.md finding #6, Direct/CA payouts normally skip"
echo "ledger's MerchantBalance path entirely -- this current_pool_account_onboarding call is included"
echo "for completeness/parity with M1/M3's FtsPayable/FtsReceivable pattern, not because production"
echo "is confirmed to exercise it for every Direct merchant. TODO(confirm) before relying on it."
call '{
  "merchant_id": "ARENAM00000002",
  "account_name": "ARENA RZPX DIRECT - RBL - 900002",
  "currency": "INR",
  "events": [{
    "name": "current_pool_account_onboarding",
    "description": "Env2 arena M2 dedicated RBL account",
    "entities": { "fts_fund_account_id": ["900002"] }
  }]
}'

echo "Done. Verify with: SELECT id, merchant_id, status, balance FROM accounts WHERE merchant_id LIKE 'ARENAM%';"
