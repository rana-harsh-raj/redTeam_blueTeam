# bankingaccounts-stub

Stands in for the Banking Accounts service for the single call payouts-api makes on the Direct-account
create path: `GET /payouts/shield/merchant/{merchant_id}/details?account_number=…` →
`{"data": {"merchant_id","business_id","business_type","sales_team","account_number"}}`
(`payouts/pkg/bankingAccountService/fetch_merchant_details.go`). Values come from
`seeds/monolith/merchants.json` (optional keys `business_id`, `business_type`, `sales_team`; defaults are
synthetic). Other Banking Accounts routes (`fetch_account`, `fetch_banking_accounts`, `fetch_banking_creds`)
are not implemented — they return the object-shaped 404 from `_common/base_stub.py`.
Auth: open (arena-internal), like asv-stub; payouts still sends its `[banking_account_service.auth]` pair.

## M4 (T10) additions

- `GET /merchant/{merchant_id}/banking_account_by_account_number/{account_number}/credentials`
  (`payouts/pkg/bankingAccountService/fetch_banking_creds.go:14`, consumed by the REAL
  `rbl_banking_account_statement` worker, `processor/rbl_gateway.go GetRequestDataForMozart`) ->
  `{"data": {"id", "corp_id", "user_id", "urn": "", "credentials": {"auth_username", "auth_password",
  "client_id", "client_secret", "corp_id"}, "merchant_id", "account_number"}}`. Values are SYNTHETIC and
  fixed (`arena` / `arena-rbl-pass` / `arena-rbl-client` / `arena-rbl-secret` / `ARENACORP`, overridable per
  merchant with `corp_id`, `banking_account_id`, `bank_user_id` keys in merchants.json); mozart-sim ignores
  them. Unknown merchant -> object-shaped 404. Declared as DEV-168.
- `merchants.json` is now re-read whenever its mtime changes (previously read once at import, so a
  provisioner had to restart the container); `POST /_arena/reload` forces a re-read.
