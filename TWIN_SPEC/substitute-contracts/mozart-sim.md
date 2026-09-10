# Substitute contract — Mozart bank gateway (`mozart-sim`)

Tier: **REPRESENTATIVE SUBSTITUTE** today; target **CONTRACT-FAITHFUL** for the RBL/ICICI operations FTS uses.
Real `mozart -mock` is UNKNOWN-BLOCKED: private modules `orchestrator v1.0.151`, `integrations-go`,
`integrations-utils v0.3.14` (`mozart/go.mod:28,54,55`) are not readable. Keep the `mozart-real` compose
profile as opt-in for when those modules become available.

## Protocol
`POST /{namespace}/{gateway}/{version}/{action}` (e.g. `fts/rbl/v1/transfer_init`), HTTP Basic (any arena pair),
JSON request envelope (`fts/internal/providers/mozart/request.go:43-61`):
```
{ "contains": [...],
  "entities": { "auth": {}, "fund_account": {bank_account|vpa|card|non_saved_card|wallet|remitter_account, payment_instrument},
                "attempt": {amount, gateway_ref_no, ...}, "source_account": {credentials{...}, configuration{...}, account_number, ...},
                "beneficiary_status": {}, "gateway_auth": {}, "gateway_session": {} } }
```
Response envelope (`response.go:3-101`): `{ data{...}, error{description, gateway_error_code, gateway_error_description,
gateway_status_code, internal_error_code}, external_trace_id, mozart_id, success, next }`. **No `meta` on the wire** —
FTS classifies from `data.bank_status_code` (`error_code.go:217-952`).

Operations (`fts/internal/config/mozart.go:4-13`): `login, gateway_auth, gateway_session, transfer_init, transfer_status,
beneficiary_verify, beneficiary_register, account_balance, create_otp`.

## Required behaviour
1. **Branch on `{gateway}/{version}`** (RBL v1, ICICI v1/v2, ICICI-IMPS, Yesbank, M2P at minimum); today the sim ignores them.
2. Scenario selection must mirror real mock mode (`mozart/app/mock/mappings.go`): RBL v1 `transfer_init/status` keyed by
   `entities.fund_account.bank_account.beneficiary_mobile`; RBL v2 by `entities.attempt.gateway_ref_no`; ICICI/ICICI-IMPS/Yesbank
   `transfer_init` by `entities.attempt.amount`; `transfer_status`/`account_balance` by `gateway_ref_no`/`account_number`.
   Keep the arena's amount-keyed convention as an alias but document it as non-production.
3. Emit `data.bank_status_code` values from the real vocabulary, at least one per FTS classification bucket:
   `SUCCESS` (processed), `DUPLICATE_TXN` (pending, PBANK), `INSUFFICIENT_FUND` (failed retriable INTERNAL),
   `MERCHANT_INSUFFICIENT_FUND` (failed MERCHANT), `RETURNED` (failed BBANK → transfer REVERSED via return_utr), `INVALID_VPA`,
   `INVALID_BENEFICIARY_DETAILS`, `FROZEN_ACCOUNT`, `TXN_REJECTED_BENE_BANK`, `INVALID_ACCOUNT`, plus raw specials
   `CBS:188` (IDFC) and `ns:E404` (Yesbank) and an unmapped code (→ Pending/UNMAPPED).
4. `transfer_status` must be able to return pending N times then terminal (scenario `100300` style), and return
   `utr`, `return_utr`, `bank_processed_time`, `is_credited/is_debited`, `credited_at` fields.
5. `gateway_auth/gateway_session` return `{token, token_type, validity_duration}`; `account_balance` returns
   `accountBalanceAmount`; `beneficiary_register/verify` return `beneficiary_code`, `beneficiary_name`, ifsc/acc/bank/type.
6. Timing knobs: per-scenario latency and a "hold pending" duration (needed by V15/V16/V17/V19/V21 preconditions).

## Fixtures
Synthetic only. Structure from `mozart/app/testdata/fts/<gateway>/<version>/<action>/<scenario>/{.golden,.gatewayResp,.vaultResp,.gatewayReqGolden,.input}`;
never copy real bank payload values. 2,657 fixture files exist upstream; Axis/IDFC/Kotak have fixtures but no `mappings.go`
entry (not reachable via `-mock`).
