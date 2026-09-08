# Concrete cross-domain paths from Payouts (file-cited, 2026-09-07)

Each path is hop-by-hop with the file and symbol that proves the hop. **OBS** = observed in the
pinned clone; **INF** = inferred; **UNK** = unknown. Longer variants with more hops are in
`findings/02_payouts_outbound_edges.md` §11–12 (paths P1–P12).

## The fan-out root shared by every path

1. Payouts Service allow-lists the internal apps that may originate payouts: `payouts/internal/auth/headers.go:11-25` `allowedInternalApps` = X_PAYROLL, VENDOR_PAYMENTS, PAYOUT_LINKS, SCROOGE, SETTLEMENTS_SERVICE, CAPITAL_COLLECTIONS_CLIENT, ChargeCollectionsInternal, FTS, XPERIENCE, ACCOUNTS_RECEIVABLE, BUSINESS_REPORTING, CROSS_BORDER_IMPORT, CAPITAL_EARLY_SETTLEMENTS (OBS, verified first-hand).
2. Internal-contact payouts are gated per app: `payouts/internal/app/contact/type.go:16-21` `internalAppToAllowedInternalContact` (vendor_payments → rzp_tax_pay; capital_collections_client → rzp_capital_collections; charge_collections_internal → rzp_charge_collections; xpayroll → rzp_xpayroll) (OBS, verified first-hand).
3. Status leaves Payouts by source type: `payouts/config/prod.toml:367-388` `[source_update_topics]` 14 source types → 10 SNS topics; `payouts/pkg/sourceupdater/event.go:89`; ARN built in `payouts/pkg/sns/sns.go:83-87` (OBS).
4. The monolith fans the SNS message to the owning service: `api/app/Models/Payout/SourceUpdater/Factory.php:14-90` (OBS); `payout_sources.source_type` enum `api/app/Models/PayoutSource/Entity.php:23-39` (14 values) (OBS).

## Path A — Vendor Payments (procurement / S2P)

| Hop | Evidence |
|---|---|
| A1 X dashboard VendorPayouts views call `/v1/vendor-payments/**` | `x/src/js/views/VendorPayouts`; Kong `terraform-kong prod vendor-payments` → `vendor-payments.razorpay.com:443` (OBS) |
| A2 Approval: vendor-payments creates workflow (`vendor-payment-v2-approval`, `purchase-order-approval`, `goods-received-note-approval`, `vendor-onboarding-approval`) | `vendor-payments/config/default.toml:536`; `workflows/internal/constants/constants.go:36-40`; callback host `workflows/config/prod.toml:173-175` (OBS) |
| A3 Payout create: `POST {api}/v1/payouts_internal/` as internal app `vendor_payments`, host `https://api-graphql.razorpay.com` | `vendor-payments/internal/payout/core.go:83,109`; `internal/rxclient/core.go:113`; `config/prod.toml:123` (OBS) |
| A4 Monolith route allow-list for app `vendor_payments`; PS allow-list | `api/app/Http/Route.php:13150+`; `payouts/internal/auth/headers.go:13` (OBS) |
| A5 Status back: SNS `payout-updates-vendor-payments` (shared by vendor_payments, tax_payments, vendor_settlements, vendor_advance) → `VendorPaymentUpdater` → `PayoutStatusChange` | `payouts/config/prod.toml:375-378`; `api/app/Models/Payout/SourceUpdater/Factory.php:29-36`; `api/app/Services/VendorPayments/Service.php:32,480-503` (OBS) |
| A6 Accounting leg: Kafka `prod.x.vendor-payments.accounting-payouts.status-update` → accounting-integrations → Zoho/Tally | `vendor-payments/config/prod.toml:272-273`; `accounting-integrations/config/prod.toml:196,204` (OBS) |
| A7 Vendor portal / onboarding: vendor-experience + BVS | `vendor-payments/config/default.toml:583,455`; `vendor-payments/go.mod` business-verification-service-sdk-go (OBS) |
| A8 Card-pay variant: capital-cards is an authenticated caller of vendor-payments | `vendor-payments/internal/boot/helpers.go:244-249`; `api/app/Models/FundAccount/Core.php:2010-2031` (OBS) |

## Path B — Tax Payments / TDS

| Hop | Evidence |
|---|---|
| B1 TDS entries arrive on Kafka `add-tds-entry` (producer UNK; monolith `TdsProcessor` is the only producer-shaped code) | `vendor-payments/config/prod.toml:336`; `internal/tasks/initiatetds.go:21,44`; `api/app/Models/Payout/TdsProcessor/Processor.php:31-86` (OBS/UNK) |
| B2 Monthly remittance RPC → lock + create payout | `vendor-payments/internal/apiservice/TaxPaymentServer.go:135`; `internal/taxpayments/core.go:1027,1929,2780` (OBS) |
| B3 `POST {api}/v1/internalContactPayout/` on the merchant's internal `rzp_tax_pay` contact/fund account | `vendor-payments/internal/payout/core.go:84,138`; `internal/taxpayments/internal_tax_contact.go:29,37,91` (OBS) |
| B4 PS gate: app `vendor_payments` may only create internal-contact payouts of type `rzp_tax_pay`; purpose `rzp_tax_pay` internal-only | `payouts/internal/app/contact/type.go:17,35-48`; `payouts/internal/app/payoutPurpose/constants.go:13,36` (OBS) |
| B5 Tag-back `PATCH v1/payouts_internal/{id}/tax-payment-id` → `payouts_details.tax_payment_id` | `vendor-payments/internal/taxpayments/apicaller.go:21`; `api/app/Http/Route.php:2339`; `api/app/Models/PayoutsDetails/Entity.php:16-17` (OBS) |
| B6 Status back on the shared vendor-payments SNS topic | `payouts/config/prod.toml:376` (OBS) |
| B7 Alternate rail with no payout: ICICI `directTaxTin2` challan APIs; DTP collects via PG checkout | `vendor-payments/config/default.toml:325-343`; `internal/directtaxpayments/core.go:298,598,653` (OBS) |
| B8 Payouts' own secret store is `tax-compliance-vault.razorpay.com` (ns `razorpayx`) | `payouts/config/prod.toml:718-724` (OBS) |
| B9 tax-compliance service consumes SQS `prod-tax-compliance-payout-events` (producer UNK) | `kube-manifests/prod/tax-compliance/values.yaml` (OBS/UNK) |

## Path C — Payroll (XPayroll / Opfin)

| Hop | Evidence |
|---|---|
| C1 Opfin crons initiate salary transfers | `kube-manifests/prod/opfin/values.yaml` run_cron `ActualBankTransfer`, `BatchBankTransfer`, `ProcessMerchantPayoutsInNonCreatedState` (OBS) |
| C2 Inbound as internal app `xpayroll` on 11 monolith routes incl. `payout_create_internal`, `payout_create_on_internal_contact`, `payout_create_2FA_internal`, `tax_payments_internal_icici_action` | `api/app/Http/Route.php:18626-18638`; `api/config/applications_v2.php:741-761` (OBS) |
| C3 PS: allow-list + contact type `rzp_xpayroll` | `payouts/internal/auth/headers.go:12`; `internal/app/contact/type.go:20`; `appConstants/constants.go:89,116` (OBS) |
| C4 Maker-checker bypass when `app == xpayroll` and merchant feature `skip_wf_for_payroll` | `payouts/internal/app/payouts/processor/baseHelper.go:185-191`; `pkg/dcs/features/features.go:47`; monolith `api/app/Models/Payout/Processor/Base.php:2614-2619` (OBS) |
| C5 FTS dedicated payroll MID handling | `fts/config/env.default.toml:6924-6925` `[payroll_mid_details]` (OBS) |
| C6 Status back: SNS `payout-updates-xpayroll` → `XPayrollUpdater` → `POST {OPFIN_SERVICE_URL}/v2/api/merchant-payout-status` | `payouts/config/prod.toml:369`; `api/app/Models/Payout/SourceUpdater/XPayrollUpdater.php:15-17`; `api/app/Services/XPayroll/Service.php:26,54-90` (OBS) |
| C7 Kong route for the callback host | `terraform-kong` route `razorpayx_rx_integrated_webhoook_routes-xpayroll` `~/v2/api/merchant-payout-status/?$` → `payroll-edge.razorpay.com` (OBS) |
| C8 Dashboard hides payroll payouts | `payouts/internal/app/payouts/core.go:9594-9596`; `config-proto/rzp/x/merchant/payouts/payroll_payouts.proto:9-15` (OBS) |
| C9 Frontend: SSO redirect only | `x/config.js:302`; `x/src/js/Routes.js:1407`; `dashboard/environment/.env.production:71` (OBS) |

## Path D — Capital (LOC / collections / corporate cards / early settlement)

| Hop | Evidence |
|---|---|
| D1 PS allow-lists `capital_collections_client`, `capital_early_settlements`; contact type `rzp_capital_collections` | `payouts/internal/auth/headers.go:17,24`; `internal/app/contact/type.go:18` (OBS) |
| D2 SNS topics `capital_cards`, `capital_line_of_credit`, `capital_collections` → `CapitalCollectionsUpdater` → capital-collections | `payouts/config/prod.toml:368-388`; `api/app/Models/Payout/SourceUpdater/Factory.php:69`; `api/app/Services/CapitalCollectionsClient.php:73-125` (OBS) |
| D3 LOC withdrawal callback | `api/app/Http/Route.php:1557-1562` `POST loc/withdrawal/update` → `LOCController@razorpayXWebhook` (OBS) |
| D4 Corporate card as X balance: `POST merchant/onboardCCCForBanking` → `balance(account_type=corp_card, channel=m2p)`; details from capital-cards | `api/app/Http/Route.php:5248`; `api/app/Models/CorpCard/Core.php:50-62`; `api/app/Services/CapitalCardsClient.php:21,76` (OBS) |
| D5 x-balances sub-balance creation affects card eligibility | `x-balances/pkg/log/trace.go:44-45` (OBS) |
| D6 Card rail: FTS channel `m2p` via mozart `capital_m2p` | `mozart/app/config/capital/capital_m2p/v1/base.json` (OBS) |
| D7 Cards to payouts: `enable_payouts_to_cards` (payouts-owned proto); CFA → bin-service + token service | `config-proto/rzp/x/merchant/payouts/cards.proto:6-20`; `cfa/config/prod.toml:67-86` (OBS) |
| D8 Early settlement keeps the payouts merchant funded | `proto/capital/es/cron/v1/cron_api.proto:9` `KeepSufficientBalanceInPayoutsMerchant` (OBS) |

## Path E — PG Settlements

| Hop | Evidence |
|---|---|
| E1 On-demand settlement creates X payouts through an internal X merchant over the public API | `api/app/Services/RazorpayXClient.php:131-190`; config `applications.razorpayx_client` (OBS) |
| E2 settlements-service posts `/v1/payouts_internal` | authz `scripts/policies/**/internal.csv:8` `app.settlements_service`; `payouts/config/prod.toml:104-106` `[auth.settlements]`; raised limits `:826` (OBS) |
| E3 Status back: SNS `payout-updates-settlements` → `SettlementsUpdater` → Twirp `rzp.settlements.transfer.v1.TransferService/StatusUpdatePayout` | `payouts/config/prod.toml:371`; `Factory.php:45`; `api/app/Services/Settlements/Payout.php:14,35`; `proto/settlements/transfer/v1/transfer_api.proto:116` (OBS) |
| E4 FTS `SETTLEMENT` product → `settlements-live.razorpay.com/twirp/.../StatusUpdate` | `fts/internal/product/product.go:15-25`; `fts/config/env.prod-live.toml:171-223` (OBS) |
| E5 Ledger: client `settlements_key`, `outbox_jobs_settlements` (PG tenant) | `ledger/config/prod-live.toml:174-281` (OBS) |

## Path F — Current-Account onboarding

| Hop | Evidence |
|---|---|
| F1 Kong `/v1/partner_lms/**` → banking-account service | `terraform-kong/templates/banking-accounts/banking-accounts.tf` (OBS) |
| F2 banking-accounts → master-onboarding, BVS | `banking-accounts/config/default.toml:103-106`; `banking-accounts/pkg/bvs` (OBS) |
| F3 Identity spine merchant → balance(type=banking) → banking_accounts(bacc, balance_id, fts_fund_account_id) → ledger account (`Ledger-Tenant: X`) | `api/app/Models/Merchant/Balance/Ledger/Core.php:133-160,865-871`; `payouts/internal/provider/ledger_client.go:19` (OBS) |
| F4 Activation event to x-balances | SQS `x-balances-account-activation-event` (kube cell values) (OBS) |

## Path G — Accounting / reporting

| Hop | Evidence |
|---|---|
| G1 SNS `generic_accounting` (default updater when no sources) | `payouts/config/prod.toml:368-388`; `Factory.php:22-25` (OBS) |
| G2 accounting-integrations → `reporting.razorpay.com` (5 report ids), Zoho, Tally | `accounting-integrations/config/prod.toml`; `internal/service/accountingtool/{tally,zoho}` (OBS) |
| G3 `business_reporting` internal app in PS allow-list; also calls vendor-payments | `payouts/internal/auth/headers.go:21`; `vendor-payments/internal/boot/helpers.go:187-269` (OBS) |

## Path H — Cross-border (newly discovered)

| Hop | Evidence |
|---|---|
| H1 `cross_border_import` in PS allow-list; raised limit alongside settlements | `payouts/internal/auth/headers.go:23`; `payouts/internal/app/payouts/validation.go:239-247` (OBS) |
| H2 source types `cross_border`/`ica_transfer` → `CrossBorderICATransferUpdater` | `payouts/config/prod.toml:368-388`; `api/app/Models/Payout/SourceUpdater/Factory.php:80` (OBS) |
