# Domain cards — RazorpayX domains adjacent to Payouts (2026-09-07)

Evidence legend: **OBS** seen in a file of the pinned clones; **INF** deduced; **UNK** not resolvable.
All paths are `repo/path:line` under the clone root (pins in `reports/ACCESS_DELTA.md` and
`findings/gh_repo_listing_2026-09-07.txt`). Full evidence trails: `findings/01..08_*.md`.

Six domains were assessed (task asked for at least three). Cards was assessed and found to be a
sub-product of Capital rather than a standalone domain; Settlements was assessed and found to be
a PG-side domain that consumes X.

---

## Card 1 — Vendor Payments + Tax Payments / TDS ("Source-to-Pay apps", S2P)

| Field | Content |
|---|---|
| Business purpose | Accounts-payable for X merchants: vendor invoices, purchase orders, GRNs, GST input credit, vendor advances/settlements, and statutory tax remittance (TDS/GST) paid out of the X current account. OBS `vendor-payments/internal/` module list (taxpayments, directtaxpayments, initiatetds, tdscategory, inputtaxcredit, purchaseorders, goodsreceivednotes, vendoradvances, vendorsettlement, vendorportal…). |
| Important user journeys | (1) Invoice upload/OCR → approval workflow → payout → status back → accounting sync. (2) Monthly TDS remittance: `add-tds-entry` → `InitiateMonthlyPayouts` → `internalContactPayout` (contact type `rzp_tax_pay`) → tag-back `tax-payment-id`. (3) PO → GRN → invoice matching with `purchase-order-approval` / `goods-received-note-approval` workflows. (4) Vendor invite → vendor portal (vendor-experience) → BVS verification → fund account. (5) Pay vendor by Capital corporate card. OBS `vendor-payments/internal/taxpayments/core.go:1027,1929,2780`; `workflows/internal/constants/constants.go:36-40`; `vendor-payments/internal/boot/helpers.go:244-249`. |
| Repositories | **In clone root:** `razorpay/vendor-payments` (Go), `razorpay/vendor-experience` (Go + Cadence), `razorpay/accounting-integrations` (Go), `razorpay/x-bank-statement-sync` (listable, F0). **Probable, not accessible:** `razorpay/tax-compliance` (TDS filing; go_package OBS `proto/tax_compliance/.../tds_payments.proto:5`), `razorpay/tax-payments` (GitHub snapshot in knowledge-base; proto package `vendorpayments.taxpayments` suggests it is folded into vendor-payments — INF), `razorpay/accounts-receivable`, `razorpay/budgets`, `razorpay/x-bill-payments`, frontends `x-vendor-portal`, `x-invoice-approval`, `x-accounts-receivable`. |
| Runtime services and workers | vendor-payments web + 11 Kafka worker deployments (tds, initiate-tds, ocr, bank-statement-fetch, accounting-payouts, auto-invoice, email-int, zoho-statement-sync, contact-updated…) OBS `kube-manifests/prod/vendor-payments/values.yaml`; vendor-experience api + Cadence workflow/activity workers OBS `kube-manifests/prod/vendor-experience/values.yaml:17-20`; accounting-integrations; tax-compliance web + SQS worker + Temporal + own Vault (NOT accessible). Cron-shaped RPCs (`InitiateMonthlyPayouts`, `CancelQueuedPayoutCron`, `AddPenaltyCron`) are called by an external scheduler OBS `vendor-payments/internal/apiservice/TaxPaymentServer.go:123-185`. |
| APIs and events | Twirp `vendorpayments` (131 rpcs) + gRPC `rzp.vendor_payments.*` OBS `proto/vendor-payments/service.proto:15`; Twirp `razorpay.vendorpayments.taxpayments` (34 rpcs) OBS `proto/tax-payments/service.proto:12`; monolith proxies `/v1/vendor-payments/*` (~150 routes) and `/v1/tax-payments/*` (40 routes) OBS `api/app/Http/Route.php:2646-2684`; Kong direct routes `api.razorpay.com/v1/vendor-payments/**` OBS `terraform-kong prod vendor-payments`. Outbound to monolith: `POST v1/payouts_internal/`, `POST v1/internalContactPayout/`, `PATCH v1/payouts_internal/{id}/tax-payment-id`, contacts/fund-accounts/banking-accounts internal routes OBS `vendor-payments/internal/payout/core.go:82-86`, `internal/taxpayments/apicaller.go:20-21`. Events: SNS `payout-updates-vendor-payments` (4 source types) OBS `payouts/config/prod.toml:375-378`; Kafka `add-tds-entry` (consumer), `prod.x.vendor-payments.accounting-payouts.status-update` (producer → accounting-integrations), ~15 internal topics OBS `vendor-payments/config/prod.toml`. |
| Databases and major tables | vendor-payments MySQL, 88 migrations (invoices, line items, purchase_orders, goods_received_notes, tax_payments, tds_categories, entity_tax, input_tax_credit, vendor_advances, vendor_settlements, control policies, custom fields, state change logs) OBS `vendor-payments/internal/migrations`; Elasticsearch for search OBS `internal/elasticservice`; also reads monolith `api_live` (INF from config). Monolith side: `payout_sources`, `payouts_details.tax_payment_id/tds_category_id` OBS `api/app/Models/PayoutSource/Entity.php:17-21`, `PayoutsDetails/Entity.php:16-17`. vendor-experience DB (Cadence-backed); accounting-integrations DB (sync_status). |
| Identities and roles | Internal app `vendor_payments` (basic auth; Payouts allow-list + contact-type gate `rzp_tax_pay`) OBS `payouts/internal/auth/headers.go:13`, `internal/app/contact/type.go:17`; ~43 X permission-roles for vendor payments/PO/GRN/ITC + tax roles `PAY_TAX_PAYMENTS`, `VIEW/UPDATE_TAX_PAYMENT_SETTINGS(_AUTO)`, `TAX_PAYMENT_ADMIN_AUTH_EXECUTE` OBS authz `xplatform*.csv`, `api/app/Http/Route.php:11615,12401-12405`; new identity **Vendor** (portal user; `vendor` banking role) and **vendor fund account**; callers of vendor-payments: api, emailInt, metro, autoInvoiceProcessing, payoutLinks, businessReporting, capitalCards, accountingIntegrations, workflows, batch OBS `vendor-payments/internal/boot/helpers.go:187-269`. |
| Dependencies on Payouts and shared platforms | Payouts via monolith `payouts_internal` / `internalContactPayout` (NOT the Payouts Service directly) OBS `vendor-payments/config/prod.toml:123` host `api-graphql.razorpay.com`; Workflows (4 workflow types + callback) OBS `workflows/config/prod.toml:173-175`; CFA via monolith contacts/fund-accounts internal routes; Stork; Batch (bulk imports); DCS/Splitz; Passport/AuthZ; Elasticsearch. Payouts itself depends on `tax-compliance-vault` OBS `payouts/config/prod.toml:718-724`. |
| External systems | ICICI direct-tax challan APIs (`apibankingone.icicibank.com /api/v1/directTaxTin2/*`, bypasses FTS) OBS `vendor-payments/config/default.toml:325-343`; Razorpay PG (direct tax collection via checkout); MastersIndia GST API; Veryfi OCR; Sandbox.co.in; Mailgun; Zoho/Tally/QuickBooks via accounting-integrations; abacus, metro, reminders, ufh, gimli, bvs. |
| Available source | vendor-payments `20c4f4d5`, vendor-experience `df90df21`, accounting-integrations `fc13a0b2` (all pinned in clone root); protos for all Twirp/gRPC contracts in `proto/`; monolith proxy + updater code in `api`; Payouts-side gate code in `payouts`. |
| Missing source or artifacts | `tax-compliance` (TDS filing), `tax-compliance-vault`, `accounts-receivable`, `budgets`, `x-bill-payments`, vendor/invoice frontends; producer of Kafka `add-tds-entry` (UNK); producer of SQS `prod-tax-compliance-payout-events` (UNK); external cron schedule for `InitiateMonthlyPayouts` (FastCron-like, UNK); prod hostnames of ICICI/MastersIndia credentials (secrets). |
| Expected difficulty | **Medium.** Go services with gorm/MySQL/Kafka like the existing twin; payout leg lands on monolith routes the arena's monolith substitute already implements (`payouts_internal`, `internalContactPayout`, contacts/fund-accounts internal). New substitutes needed: ICICI direct-tax mock (Mozart-style), OCR/GST stubs, Cadence for vendor-experience (or workflow-engine reuse), Elasticsearch. |
| Value of adding it to the functional graph | **High.** Adds 4 payout source types, the widest set of payout-originating internal apps, PO/GRN approval workflows on the shared Workflow platform, statutory tax rails, and the accounting leg. Closes the "who creates internal-contact payouts" gap in the current graph and exercises maker-checker, idempotency and internal-app authorization from a second origin. |

---

## Card 2 — Payroll (XPayroll / Opfin)

| Field | Content |
|---|---|
| Business purpose | Salary, reimbursement and statutory (PF/ESI/PT/TDS) processing for X merchants; net-pay disbursement executed as X payouts from the merchant's current account. OBS RKG payroll bundle `knowledge-base/knowledge/domains/payroll/nodes/services/*.yaml`; `x/src/js/views/Payroll` (marketing + SSO only). |
| Important user journeys | (1) Payroll run finalize → `ActualBankTransfer` / `BatchBankTransfer` crons → `payout_create_internal` as app `xpayroll` (maker-checker bypassed when `skip_wf_for_payroll`) → SNS `payout-updates-xpayroll` → `POST /v2/api/merchant-payout-status`. (2) Fund-loading TPV: `POST /v2/api/validate-source-account`. (3) Employee flexible benefits / attendance / salary structure (gRPC services). (4) Compliance filings via Temporal. OBS `kube-manifests/prod/opfin/values.yaml` (run_cron jobs), `payouts/internal/app/payouts/processor/baseHelper.go:185-191`, `api/app/Services/XPayroll/Service.php:26-28,54-90`. |
| Repositories | **None accessible with content.** `razorpay/x-payroll` (deployable `opfin`; OBS `spinacode/v3/opfin/prod/mum-rspl/app.json "repo": "x-payroll"`) 404; `x-payroll-compute`, `x-payroll-compliance`, `x-payroll-hris`, `x-payroll-flexible-benefits`, `x-salary-structure`, `x-payroll-salary` 404 (names OBS from spinacode images and proto `go_package`); `x-attendance` and `x-payroll-commons` are listable INTERNAL repos but contain only a `README.md` saying "template" (OBS shallow clones 2026-09-07). |
| Runtime services and workers | opfin (PHP web + queue workers main/low-priority/custom-reports/payouts-creation + 3 CronJobs), opfin-compliance (Temporal), opfin-demo, x-payroll-compute (gRPC + Temporal + Kafka SDI consumer), x-payroll-flexible-benefits (Kafka `employee-settings-sync`), x-payroll-hris, x-salary-structure, x-tna (Temporal Cloud) OBS `kube-manifests/prod/{opfin,opfin-compliance,x-payroll-*,x-salary-structure,x-tna}/values.yaml`; dedicated alert cell `rzpx-inpr-pd01` OBS `alert-rules/rules/cells/rzpx/prod/rzpx-inpr-pd01/...`. |
| APIs and events | Inbound to X: 11 monolith routes allow-listed to `app.xpayroll` OBS `api/app/Http/Route.php:18626-18638`; outbound from X: `/v2/api/merchant-payout-status`, `/v2/api/validate-source-account` on `payroll.razorpay.com` OBS Kong route `razorpayx_rx_integrated_webhoook_routes-xpayroll`; protos `rzp.x_payroll.*` (28 services) OBS `proto/x-payroll/**`; no Stork webhook family, no rzpconv entry. SNS `payout-updates-xpayroll` OBS `payouts/config/prod.toml:369`. |
| Databases and major tables | Aurora Postgres `prod-aurora-postgres-opfin-1a/1b`, `prod-aurora-postgres-xpayroll-1b` OBS `alert-rules/rules/prod-rules/rds_generated_alerts_*`; CDC lake `realtime_prod_opfin`, `realtime_prod_x_payroll_compute` OBS `self-serve-analytics`; table names UNK. |
| Identities and roles | Internal app `xpayroll` (basic auth `OPFIN_SERVICE_SECRET`; contact type `rzp_xpayroll`) OBS `api/config/applications_v2.php:741-761`, `payouts/internal/app/contact/type.go:20`; dedicated FTS payroll MID list OBS `fts/config/env.default.toml:6924-6925`; **Employee** identity (employee_id) OBS protos; company/org mapping to merchant_id UNK. |
| Dependencies on Payouts and shared platforms | Monolith `payouts_internal` routes, contacts/fund-accounts internal, banking_accounts_list_internal, `tax_payments_internal_icici_action` (payroll pays TDS through the ICICI rail exposed by tax payments) OBS `Route.php:18635`; feature flags `skip_wf_for_payroll`, `hide_rx_payroll_payouts`, `payroll_sav` OBS `payouts/internal/app/common/appConstants/features.go:30,50`; FTS; Stork (email); Temporal Cloud; Kafka MSK. |
| External systems | Temporal Cloud (`ap-south-1.aws.api.temporal.io`), Kafka `employee-settings-sync`, statutory portals (EPFO/ESIC/TRACES — INF from `tax_compliance` protos `esic_registration`, `tds_filing`), Slack app, CAMS (`/api/camsunitv3`). |
| Available source | Only the X-side contract: monolith routes + XPayroll client, Payouts gate/feature code, config-proto `payroll_payouts.proto`, protos in `proto/x-payroll`, deployment manifests, RKG runbooks. |
| Missing source or artifacts | Every payroll service repo; opfin DB schema; the payroll→X request payloads (only route names known); cron cadence; salary-batch semantics (batch vs single payouts) UNK. |
| Expected difficulty | **High for a real twin (blocked); Low for a protocol-faithful external-caller simulator.** The X-facing surface is small and fully specified from the X side (11 routes, 1 status callback, 1 TPV callback, 1 feature flag). |
| Value of adding it to the functional graph | **Very high business value** (largest adjacent footprint, money-movement crons, P0 alerts) but the graph can only gain an *external product* node with well-evidenced edges until source is granted. The maker-checker bypass path is a distinct control state worth modelling now. |

---

## Card 3 — Capital (corporate cards, LOC, collections, early settlement, BNPL)

| Field | Content |
|---|---|
| Business purpose | Lending and card products sold to X merchants: line of credit withdrawals disbursed as X payouts, repayments collected via `capital_collections` payouts, corporate card spend line exposed as an X balance (`account_type=corp_card`, channel `m2p`), on-demand settlement (`capital-es`). OBS `api/app/Models/CorpCard/Core.php:50-62`, `api/app/Services/CapitalCollectionsClient.php:73-125`, `api/app/Http/Route.php:1557-1562`. |
| Important user journeys | LOC withdrawal → X payout → `/loc/withdrawal/update` callback; repayment collection payout (`source_type=capital_collections`); corporate card onboarding as X balance + spends via xperience `CorporateCardsAPI`; pay vendor by card (capital-cards calls vendor-payments); early settlement keeps payouts merchant funded (`KeepSufficientBalanceInPayoutsMerchant`). OBS `proto/capital/es/cron/v1/cron_api.proto:9`; `proto/xperience/corporatecards/v1/corporatecards_api.proto:18`. |
| Repositories | All 404 to this identity: `capital-cards`, `capital-collections`, `capital-loc`, `capital-los`, `capital-lender`, `capital-scorecard`, `capital-bnpl`, `capital-es`, `financial-data-service`, `credithub` (names OBS `kube-manifests/prod-capital/*`, `spinacode/capital-*`, `api/config/applications.php:1016-1083`). Frontend `x/src/js/views/Capital/*` is in the clone root (federated bundles from `cdn.razorpay.com/capital/*`). |
| Runtime services and workers | Own cluster `prod-capital` with 11 apps (api/worker/console each; Camunda for LOS; capital-gateway = mozart image) OBS `kube-manifests/prod-capital/`. |
| APIs and events | 122 proto services under `proto/capital` (largest adjacent contract surface); monolith proxies `capital_cards/*`, `cards/{token,number,otp,cvv,session}`, `loc/*`, `capital_collections/*`, `capital_balances/transaction` OBS `api/app/Http/Route.php:1557-1579,4010-4012`; SNS topics for `capital_cards`, `capital_line_of_credit`, `capital_collections` OBS `payouts/config/prod.toml:368-388`; Kong webhooks (aftership, karza, myoperator IVR) OBS `terraform-kong/templates/capital-cards/capital-cards.tf:16-34`. |
| Databases and major tables | Monolith `capital_transaction`, `credit_transfers`, `corporate_cards`, `balance(account_type=corp_card)` OBS `api/app/Constants/Table.php:85,86,419`; ledger fund-account types `merchant_plan_principal/interest/charges`, `capital_gateway`, `m2p` (Capital uses PG tenant) OBS `ledger/internal/common/constant.go:221-322`; RDS `prod-aurora-mysql-capital-es-1a`; CDC `realtime_capital_cards`, `realtime_capital_los`, `realtime_prod_capital_es`. |
| Identities and roles | Internal apps `capital_collections_client`, `capital_cards_client`, `loc`, `capital_bnpl`, `capital_early_settlements` OBS `payouts/internal/auth/headers.go:17,24`, authz `internal.csv`; cards RBAC `card:*`, `repayment:*`, `program:limit` for X dashboard roles OBS `authz .../20221212154546_cards_RBAC.csv`, `capital.csv:4-99`; `cardholder_id/card_id/program_id` (protos); monolith features `capital_cards*`, `cash_on_card`. |
| Dependencies on Payouts and shared platforms | Payouts (allow-listed apps, contact type `rzp_capital_collections`, SNS), x-balances (sub-balance creation mutates card eligibility OBS `x-balances/pkg/log/trace.go:44-45`), ledger (PG tenant), FTS channel `m2p` via mozart `capital_m2p`, vendor-payments (card-pay), xperience (card UI). |
| External systems | M2P card issuer, aftership, karza, myoperator IVR, Camunda, lenders. |
| Available source | Only X-side: monolith proxies/entities, payouts gates, x-balances hook, xperience proto, mozart `capital_m2p` gateway config, frontend views. |
| Missing source or artifacts | All Capital backends; card-issuer protocol; ledger posting rules for cards (UNK whether capital-cards writes to ledger). |
| Expected difficulty | **Very high.** Ten unavailable services, an issuer rail, and a separate BU ownership (`@razorpay/Capital_BE`; org map "UNMAPPED" for RazorpayX). |
| Value of adding it to the functional graph | Medium-high as *edges* (already added in the patch), low as a replicable domain until access exists. Cards specifically: 2 hops from payouts (payouts → cfa → bin-service/token-service) and deprecated routes in `x` (commented out) — poor replication ROI. |

---

## Card 4 — PG Settlements consuming X (on-demand / early settlement)

| Field | Content |
|---|---|
| Business purpose | Payment-gateway settlements are a PG BU product (`@razorpay/TechSettlements`); X is touched when on-demand / early settlement money is pushed to merchants as X payouts through an internal X merchant, and when settlements-service posts payouts directly. OBS `api/app/Services/RazorpayXClient.php:131-190`; `api/app/Models/Merchant/Balance/Type.php:64-70` (banking balances are not settleable). |
| Important user journeys | On-demand settlement → capital-es → X public API payout; settlements-service → `POST /v1/payouts_internal`; status back via SNS `payout-updates-settlements` → `TransferService/StatusUpdatePayout`; FTS `SETTLEMENT` product transfers with Twirp status webhook to `settlements-live.razorpay.com`. |
| Repositories | `razorpay/settlements`, `razorpay/capital-es` — 404. Protos OBS `proto/settlements/**` (25 packages), `proto/capital/es/**`. |
| Runtime services and workers | settlements (~100 worker/cron templates), settlement-service, capital-es (cron API) OBS `kube-manifests/templates/settlements`, `prod-capital/capital-es`. |
| APIs and events | `TransferService.StatusUpdatePayout`, `BankAccountService.MigrateToPayout` OBS `proto/settlements/transfer/v1/transfer_api.proto:116`; admin route `settlements/migration/migrate_to_payout` OBS terraform-kong; Stork `settlement.processed`; ledger client `settlements_key` + `outbox_jobs_settlements`; DCS `rzp/pg/merchant/settlements` (48 keys). |
| Databases and major tables | Monolith `settlements`, `settlement_ondemand_*` (17 migrations), `nodal_*` OBS `api/database/migrations`; ledger PG tenant; RDS `capital-es`. |
| Identities and roles | Internal app `settlements_service` with `[auth.settlements]` creds and raised limits for 6 settlement merchants OBS `payouts/config/prod.toml:104-106,826`; internal X merchant for on-demand settlements (config `applications.razorpayx_client`). |
| Dependencies on Payouts and shared platforms | Payouts (2 entry paths), FTS (own product + auth user), Ledger (PG tenant), Stork. |
| External systems | Banks via FTS/mozart. |
| Available source | X-side only (monolith client/updater, payouts allow-list, FTS product config, protos). |
| Missing source or artifacts | settlements, capital-es, settlement-service repos; which of the two payout paths is live for which flow (UNK). |
| Expected difficulty | High (PG-side platform with ~100 workers). |
| Value of adding it to the functional graph | Medium: it is a strong *edge* set (now in patch, stub relabelled) but not an X domain to replicate. |

---

## Card 5 — Current-Account onboarding + KYC

| Field | Content |
|---|---|
| Business purpose | Opening the X current account (RBL/ICICI/Axis/Yes/IDFC/Kotak/HDFC/M2P/Slice) and virtual accounts, KYC, activation, and the identity spine merchant → balance → banking_account → ledger account. OBS `agent-skills/teams/banking/skills/ca-onboarding-oncall-debugger/SKILL.md:41-88`; `api/app/Models/Merchant/Balance/Ledger/Core.php:133-160`. |
| Important user journeys | CA application → master-onboarding intent → BVS document/KYC → banking-accounts activation (partner LMS) → balance + fts fund account + ledger account; VA KYC; fee recovery / low-balance flags. |
| Repositories | **In clone root:** `banking-accounts` (already F2 in graph), `x-balances`, `api` (banking_account* modules), `business-verification-service-sdk-go`. **404:** `master-onboarding`, `onboarding`, `business-verification-service` (bvs), `x-ai-onboarding-agent`, `x-partner-kit`. |
| Runtime services and workers | master-onboarding (gRPC), onboarding + 4 workers, bvs + 12 workers, banking-account web OBS `kube-manifests/prod/{master-onboarding,onboarding,bvs,banking-account}/values.yaml`. |
| APIs and events | Kong `/v1/partner_lms/**`, `/v1/banking_account/{id}` OBS `terraform-kong/templates/banking-accounts/*.tf`; gRPC `rzp.master_onboarding.intent.v1.IntentAPI`; Stork `banking_accounts.issued`; SQS `x-balances-account-activation-event`; DCS `rzp/x/merchant/onboarding/*` (fee_recovery, pricing_tiers). |
| Databases and major tables | banking-accounts DB (`businesses`, `people`, `signatories`, `partner_bank_applications`, `rbl_credentials`); monolith `banking_accounts`, `banking_account_statement*` (16 migrations); CDC `realtime_prod_banking_accounts` (3612 refs); RDS bvs. |
| Identities and roles | `business_id` (distinct from merchant_id), `create_banking`/`view_banking` pseudo-owner roles OBS authz `2023_06_14_rx_xplatform_merchant_tpv.csv`; internal app `banking_account_service`. |
| Dependencies on Payouts and shared platforms | Mozart, FTS, api-graphql, DCS, Splitz, BVS, metro, Zoho/Hubspot/Segment (CRM). |
| External systems | Bank LMS/partner APIs, KYC vendors (credence, karza), CRM. |
| Available source | banking-accounts + monolith modules + x-balances; BVS SDK. |
| Missing source or artifacts | master-onboarding, onboarding, bvs. |
| Expected difficulty | Medium-high (KYC vendors and bank LMS need substitutes; state machine partly in unavailable services). |
| Value of adding it to the functional graph | Medium: mostly extends an existing F2 node; adds identity provenance (`business_id`) and the activation → balance → ledger chain the twin currently seeds by hand. |

---

## Card 6 — Accounting integrations + business reporting

| Field | Content |
|---|---|
| Business purpose | Sync X transactions/payouts to Zoho Books/Tally/QuickBooks and feed FinanceX/business reports. OBS `accounting-integrations/internal/service/accountingtool/{tally,zoho}`; `accounting-integrations/config/prod.toml:196,204`. |
| Important user journeys | Payout status → SNS `generic_accounting` / Kafka accounting-payouts status → accounting-integrations → external tool; bank statement sync (x-bank-statement-sync); FinanceX reports (`financex_report_*` roles). |
| Repositories | **In clone root:** `accounting-integrations`, `x-bank-statement-sync` (no source content per inventory). **404:** `business-reporting`, `reporting`. |
| Runtime services and workers | accounting-integrations (GAI) workers; business-reporting web + Kafka worker (image OBS kube-manifests). |
| APIs and events | 30 proto services `accounting_integrations`; Kafka `prod.x.vendor-payments.accounting-payouts.status-update`; SNS generic_accounting; 17 Tally routes in `PRIVATE_BANKING_ROUTES`; `reporting.razorpay.com` 5 report ids. |
| Databases and major tables | accounting-integrations DB (sync_status); CDC `realtime_prod_accounting_integrations`. |
| Identities and roles | OAuth scope `tally_read_write`; internal app `business_reporting` (in Payouts allow-list) OBS `payouts/internal/auth/headers.go:21`; roles `reporting_config`, `financex_report_create/view`. |
| Dependencies on Payouts and shared platforms | Payouts SNS, vendor-payments Kafka, reporting service, WDA warehouse client (payouts + cfa). |
| External systems | Zoho, Tally, QuickBooks. |
| Available source | accounting-integrations. |
| Missing source or artifacts | business-reporting, reporting; external tool sandboxes. |
| Expected difficulty | Low-medium (single Go service; externals stubbed). |
| Value of adding it to the functional graph | Medium; naturally bundled with Card 1 as its downstream leg. |
