# bankingaccounts-stub

Stands in for the Banking Accounts service for the single call payouts-api makes on the Direct-account
create path: `GET /payouts/shield/merchant/{merchant_id}/details?account_number=…` →
`{"data": {"merchant_id","business_id","business_type","sales_team","account_number"}}`
(`payouts/pkg/bankingAccountService/fetch_merchant_details.go`). Values come from
`seeds/monolith/merchants.json` (optional keys `business_id`, `business_type`, `sales_team`; defaults are
synthetic). Other Banking Accounts routes (`fetch_account`, `fetch_banking_accounts`, `fetch_banking_creds`)
are not implemented — they return the object-shaped 404 from `_common/base_stub.py`.
Auth: open (arena-internal), like asv-stub; payouts still sends its `[banking_account_service.auth]` pair.
