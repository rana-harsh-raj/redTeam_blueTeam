# Access manifest — repositories and artifacts needed for the expansion (2026-09-07)

Identity probed: GitHub `rana-harsh-raj` (org member, repository-level grants). Method: `gh api
repos/razorpay/<name>` for every inferred name (48 names), plus `gh repo list razorpay --limit 2000`
(252 listable). Result: **3 of 48 resolve** — `shield` (private, 6.6 GB, out of scope),
`x-attendance` and `x-payroll-commons` (INTERNAL, cloned, each contains only `README.md` = "template").
Everything else returns 404 (GitHub returns 404 both for "no access" and "does not exist").
Machine-readable copy: `ACCESS_MANIFEST.csv`.

Owner handles come from CODEOWNERS/READMEs/alert channels in the clones (`findings/01` §5,
`findings/04` §2.5, `findings/08` §6). "Replacement possible" says whether a protocol-faithful
substitute can be built from X-side contracts while access is pending.

## Tier 1 — needed for the PRIMARY domain (S2P: Vendor Payments + Tax Payments/TDS)

| # | Repository / artifact | Likely owner | Why needed | Journey unlocked | Replacement possible meanwhile |
|---|---|---|---|---|---|
| 1 | `razorpay/tax-compliance` (+ `tax-compliance-vault` chart values) | Payroll/Opfin team (alerts `#opfin-app-test-bugs`, dashboard `opfin-infra`; INF) | Consumer of SQS `prod-tax-compliance-payout-events`; TDS filing/payment protos `rzp.tax_compliance.*`; payouts reads its Vault | TDS remittance → filing close-loop; payouts secret provisioning | YES — SQS sink + Vault (HashiCorp dev mode) with the `rzp.tax_compliance.compliance.tds_payments.v1` proto |
| 2 | Producer of Kafka `add-tds-entry` (confirm: monolith `Payout/TdsProcessor` or a job repo) | Payouts core / RX Apps (`@rxapps-vendorpayouts`) | The TDS journey starts here; producer not found in clone root | Monthly TDS remittance (B1) | YES — replay fixture publisher; needs one prod sample message schema |
| 3 | `razorpay/accounts-receivable` (+ `x-accounts-receivable`, `x-invoice-approval`, `x-vendor-portal` frontends) | RX Apps (INF; `#x-alerts`) | Internal app `accounts_receivable` is in the Payouts allow-list; `MetroAPI.InitiatePayout`; hosted-invoice payout webhook | Receivables → payout webhook | PARTIAL — Kong route + proto `accounts_receivable/metro_api.proto` give the contract; payload semantics UNK |
| 4 | External scheduler config for `InitiateMonthlyPayouts`, `CancelQueuedPayoutCron`, `AddPenaltyCron`, `EmailCron` (FastCron/cron repo) | RX Apps | Cron cadence and merchant selection are not in the repo | Monthly TDS run timing | YES — parametrised local cron |
| 5 | Prod sample payloads (redacted) for: `payout-updates-vendor-payments` SNS message, `WorkflowStateCallback`, `PayoutStatusChange`, ICICI `directTaxTin2` request/response | RX Apps / Payouts core | Byte-level contract tests | All S2P journeys | YES for SNS/workflow (schemas in code); NO for ICICI without samples |
| 6 | `razorpay/budgets`, `razorpay/x-bill-payments` | `@razorpay/x-cbu-devs` (budgets, Kong CODEOWNERS:358-360); RX Apps | Petty-cash/budgets source type `petty_cash`; BBPS bill pay payouts (`BillAPI.PayoutStatusChange`) | Petty cash payouts; bill payments | YES — xperience already implements budgets/petty cash in the clone root |
| 7 | vendor-payments / vendor-experience / accounting-integrations **prod values** (`kube-manifests` already has charts; need secrets-free `prod.toml` diffs, MSK topic list, Elasticsearch index names) | RX Apps | Effective config for clean-boot fidelity | Clean boot | YES — defaults in repo suffice for a first boot |

## Tier 2 — needed for the SECONDARY domain (Payroll / Opfin) as a real twin

| # | Repository / artifact | Likely owner | Why needed | Journey unlocked | Replacement possible meanwhile |
|---|---|---|---|---|---|
| 8 | `razorpay/x-payroll` (deployable `opfin`; OBS `spinacode/v3/opfin/prod/mum-rspl/app.json`) | X Payroll (EMs per `knowledge-base` org map; `#xpayroll-alerts`, `#opfin-app-bugs`) | The only caller of `payout_create_internal` as app `xpayroll`; crons `ActualBankTransfer`/`BatchBankTransfer` | Salary disbursement with maker-checker bypass | YES — "opfin-sim": external caller replaying the 11 allow-listed monolith routes + receiving `merchant-payout-status`/`validate-source-account` |
| 9 | `razorpay/x-payroll-compute`, `x-salary-structure`, `x-payroll-hris`, `x-payroll-flexible-benefits`, `x-payroll-compliance`, `x-tna` (image name; repo listed as `x-attendance`, empty) | X Payroll | Payroll compute → payout batch composition; protos exist in `proto/x-payroll` | Payroll run → net pay batch | PARTIAL — gRPC stubs from protos; business rules UNK |
| 10 | Opfin DB schema / CDC table list (`realtime_prod_opfin`, `realtime_prod_x_payroll_compute`) | X Payroll / Data | Synthetic payroll data shape | Payroll journeys | NO |
| 11 | Populate or point to real contents of `x-attendance` and `x-payroll-commons` (currently template READMEs) | X Payroll | Confirms whether these are placeholders or mis-listed | — | n/a |
| 12 | `razorpay/frontend-graphql` (payroll BFF per RKG) | X Payroll | Payroll UI ↔ opfin contract | Payroll UI | NO (redirect-only from X) |

## Tier 3 — needed to promote strong edges into replicable domains later

| # | Repository / artifact | Likely owner | Why needed | Journey unlocked | Replacement possible meanwhile |
|---|---|---|---|---|---|
| 13 | `razorpay/capital-cards`, `capital-collections`, `capital-loc`, `capital-es`, `capital-los`, `financial-data-service`, `credithub` | `@razorpay/Capital_BE`, `@razorpay/capital_fe`, `capital-loc-reviewers` (`#capital_cards_alerts`, `#capital_los_alerts`) | 5 allow-listed internal apps, 3 SNS topics, corp-card balance, LOC callback | LOC withdrawal, collections, corp card | PARTIAL — monolith proxies + protos define the X-facing contract; issuer rail (M2P) needs mozart `capital_m2p` mock |
| 14 | `razorpay/settlements`, `settlement-service` | `@razorpay/TechSettlements` (`#settlements`, `#settlement-alerts`) | `StatusUpdatePayout`, `MigrateToPayout`, FTS `SETTLEMENT` product | On-demand settlement → X payout | YES — Twirp receiver from `proto/settlements/transfer/v1` |
| 15 | `razorpay/master-onboarding`, `onboarding`, `business-verification-service` | X acquisition (`#x-acquisition-alerts`, `#x-onboarding-oncall`); BVS `@razorpay/identity-core` | CA activation chain; `business_id` identity | CA onboarding | PARTIAL — banking-accounts (cloned) + BVS SDK contract; mob gRPC from alert-rule service names only |
| 16 | `razorpay/business-reporting`, `reporting` | Reporting team (DCS `reporting-team`) | `business_reporting` internal app; report ids | FinanceX/report journeys | YES — HTTP stub with 5 report ids |
| 17 | `razorpay/abacus`, `metro`, `ufh` | Platform (`@razorpay/tech-infra-common-platforms` for ufh) | vendor-payments hard dependencies | Limits, bulk orchestration, files | YES — simple stubs |
| 18 | `razorpay/scrooge`, `cross-border-import` | PG refunds; XBI | `refund`, `cross_border`/`ica_transfer` source types | Refund/ICA payouts | YES — source-type replay |
| 19 | `razorpay/rpc` (clone failed on SSL earlier; generated stubs) | Platform | Twirp/gRPC stubs for `tax-payments`, `vendor-payments`, `vendor-portal`, `accounting-payouts` | Contract tests | YES — regenerate with buf from `proto/` (`rpc/buf.gen.twirp.yaml` present) |

## Non-repository artifacts

| # | Artifact | Owner | Why |
|---|---|---|---|
| 20 | Kafka MSK topic ACL list for `vendor-payments` and `x-payroll-*` namespaces | Platform/Kafka | Producer/consumer confirmation for `add-tds-entry`, `employee-settings-sync` |
| 21 | SNS subscription list for the 10 `payout-updates-*` topics | Payouts core | Confirms which service (monolith vs direct) subscribes per source type |
| 22 | AuthZ DB `role_policy_mapping` export for `x_platform`, `capital` resource groups | AuthZ team | Role→permission mapping is data, not CSV |
| 23 | DCS effective values for `rzp/x/merchant/payouts/*` incl. `skip_workflow_for_payroll`, `enable_payouts_to_cards`, pricing tiers | Payouts core / DCS | Feature-state fidelity |
| 24 | Grafana/alert dashboards `opfin-infra`, `xpayroll_rules.yaml` metric names | Payroll | Confirms payroll→payouts SLOs |

## Request text (copy-ready)

> Requesting read access for the RazorpayX architecture-twin programme (RedGrid) to the repositories listed in Tier 1 and Tier 2 of `reports/expansion/ACCESS_MANIFEST.csv`. Purpose: build a local, protocol-faithful replica of the Source-to-Pay (vendor payments, tax payments/TDS) and Payroll → Payouts flows for autonomous assurance testing. No production data is required; redacted sample payloads for the five contracts listed in row 5 are requested. Contact: rana.singh@razorpay.com.
