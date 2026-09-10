# 01 — Cross-repo domain scan (adjacent business domains)

Scope: read-only sweep of the pristine clone root
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture` (72 repos + prior-pass `findings/`).
All paths below are **relative to that clone root** unless stated otherwise.
Excluded from every scan: `vendor/`, `node_modules/`, `dist/`, `*.lock`, `*.pb.go`, `*.min.js`, `*.map`, and the prior-pass `findings/`.
Repos `agent-skills`, `knowledge-base`, `markdown-docs` are marked **[DOC]** — they are documentation/skill corpora, so hits there are
*naming evidence* (a domain exists and is talked about), never *wiring evidence*. `self-serve-analytics` is a dbt/DataHub metadata repo —
its hits are **warehouse-table** evidence (a domain has productionised fact tables), which is a distinct and useful signal.

## 1) Summary (10 lines)
1. The single strongest, non-string, first-party edge is **`payouts/internal/auth/headers.go:11-25`** — the payouts service's own allow-list
   of internal callers. It names X_PAYROLL, VENDOR_PAYMENTS, SETTLEMENTS_SERVICE, CAPITAL_COLLECTIONS_CLIENT, CAPITAL_EARLY_SETTLEMENTS,
   ACCOUNTS_RECEIVABLE, BUSINESS_REPORTING, CROSS_BORDER_IMPORT, SCROOGE, CHARGE_COLLECTIONS, FTS, XPERIENCE, PAYOUT_LINKS.
2. That list is the ranked shortlist. Every adjacent domain that *actually creates payouts today* is in it.
3. `payouts/internal/app/contact/type.go:16-28` refines it into a payout **contact-type** map: vendor_payments→tax_payment,
   capital_collections_client→capital_collections, charge_collections_internal→charge_collections, xpayroll→xpayroll.
4. Domains with real code in-clone: Vendor payments + Tax payments (`vendor-payments`, 1.4k-line `internal/taxpayments/core.go`),
   Accounting integrations (Tally/Zoho), Expense/Budgets/PettyCash (`xperience/internal/app/core/budgets`), Pricing (`charge-collections`).
5. Domains referenced but whose service repo is **NOT cloned**: Settlements, Capital (LOS/LOC/cards/collections/ES/BNPL), X-Payroll (opfin),
   Wallet, Business-reporting, Accounts-receivable, Master-onboarding, BBPS/bill-payments, Merchant-invoice, Tax-compliance.
6. `proto/` is the best name registry: 130+ top-level packages incl. `settlements/`, `capital/{loc,los,lender,cards,collections,es,bnpl}`,
   `x-payroll/{x-payroll-compute,x-salary-structure,x-tna,hris,flexible-benefits}`, `wallet/`, `tax-payments/`, `tax_compliance/`,
   `business_reporting/`, `accounts_receivable/`, `budgets/`, `x_bill_payments/`, `merchant_invoice/`, `master_onboarding/`.
7. `kube-manifests/templates/` (≈900 dirs) and `terraform-kong/templates/` (≈190 dirs) confirm these as *deployed services with Kong upstreams*.
8. The PHP monolith `api` is the hub: `app/Services/` has 148 client classes + 49 client dirs, each bound to an `applications.<key>` config
   block in `api/config/applications.php` — this is the cleanest one-line-per-service edge list in the whole estate.
9. Slack/CODEOWNERS confirm distinct owning teams per domain (`@razorpay/TechSettlements`, `@razorpay/Capital_BE`, `@razorpay/TechCards`,
   `@razorpay/pricing-team`, `@razorpay/mandatoryxreviewers` for the X/vendor-payments family).
10. Ranking (section 7): **Settlements > Capital > Vendor-payments/Tax-payments > X-Payroll > Current-account onboarding > Accounting >
    Expense/Budgets > Pricing > Wallet > Reporting/Insights > Cards > Forex/Cross-border > Invoices/BBPS**.

---

## 2) Domain keyword families — hits per repo

Method: one `rg -i --count-matches` per family over the clone root with the exclusions above; counts aggregated per repo in python.
**Raw counts are noisy** (e.g. `settle_` matches `settled_at` in every payments fixture; `dashboard/graphify-out/graph.json` is a
generated 33k-hit artefact). Read the "top files" column, not the count.

### 2a. cards — total 19,393 hits / 52 repos
| repo | hits | top files (line/symbol) |
|---|---:|---|
| api | 2,084 | `api/app/Models/FundTransfer/M2P/M2PConfigs.php` (419) — M2P card-issuer fund-transfer config class; `api/app/Http/Route.php` (91) — card routes |
| x | 2,683 | `x/src/js/views/Capital/CorporateCards/CorporateCards.js` (47), `.../Components/__tests__/CardSettings.test.js` (58) — RX corporate-cards UI lives **under Capital** |
| dashboard | 5,169 | `dashboard/apps/capital/src/views/corporate-cards/styles.ts` (88) — again nested under the `capital` app |
| mozart | 997 | `mozart/app/config/capital/capital_m2p/v1/base.json` (22), `mozart/app/config/fts/m2p/v1/base.json` (23) — **M2P gateway integration for capital cards** |
| fts | 380 | `fts/e2e/constants/constants.go:87: M2P = "M2P"`; `fts/config/env.default.toml:3618 [integration.api.m2p.v1.ct]`, `:7391 # M2P - visa direct`, `:7393 channel = "m2p"` |
| terraform-kong | 408 | `terraform-kong/templates/capital-cards/capital-cards.tf:13` — Kong upstream `capital-cards` |
| self-serve-analytics | 1,690 | `datahub/metadata/business_glossary_merged.yml` (183) — glossary terms only |
| agent-skills [DOC] | 1,840 | glossary + `gift-cards-issuance` skill |
| markdown-docs [DOC] | 566 | `markdown-docs/x/capital/corporate-cards.md`, `markdown-docs/payments/payment-methods/cards/corporate-cards.md` |
| charge-collections | 362 | `test/functional/plananalysis/card_data.go` — pricing plan card data, not issuing |

**Verdict: "Cards" is not a standalone adjacent domain — it is a sub-product of Capital** (`corporate cards` / `capital-cards`), and its
payout leg goes through `fts` channel `m2p` → `mozart` gateway config `capital/capital_m2p`.

### 2b. payroll — total 46,973 / 51 repos
| repo | hits | top files |
|---|---:|---|
| proto | 1,889 | `proto/x-payroll/x-payroll-compute/payroll_finalize/v1/payroll_finalize_api.proto` (168), `proto/x-payroll/x-salary-structure/salary_structure/v1/salary_structure_api.proto` (153) — **real gRPC service contracts** |
| alert-rules | 1,789 | `alert-rules/rules/prod-rules/xpayroll_rules.yaml` (487); `alert-rules/rules/prod-rules/prod_x_payroll_compute_rules.yaml` (174) |
| kube-manifests | 5,190 | `kube-manifests/dev/opfin/helmfile-rendered.yaml` (246) — service deployed as **`opfin`** |
| api | 1,405 | `api/app/Services/XPayroll/Service.php:38: $this->config = $app['config']['applications.xpayroll']` |
| recon | 1,923 | `recon/app/tests/data/sample1.csv` — fixture noise |
| self-serve-analytics | 11,469 | `datahub/metadata/glossary/razorpayx/x-payroll/x-payroll.yml` (599) + certified queries — payroll has a full warehouse domain |
| markdown-docs [DOC] | 5,584 | `markdown-docs/payroll/run-payroll.md`, `/faqs.md`, `/tds.md` |
| knowledge-base [DOC] | 4,578 | `knowledge-base/knowledge/domains/payroll/registry/{nodes,edges}.yaml` (909/1349) + `edges/payroll-topology.yaml` — a **pre-built payroll graph already exists** |
| dashboard | 1,135 | `dashboard/apps/payroll-app/` (micro-frontend) |
| x | 785 | `x/src/js/views/Payroll/` |

### 2c. settlements — total 160,708 / 48 repos (highest raw count)
| repo | hits | top files |
|---|---:|---|
| api | 17,029 | `api/tests/Functional/Settlement/SettlementOndemand/SettlementOndemandTest.php` (844); `api/app/Services/Settlements/{Api,Base,Dashboard,MerchantDashboard,Payout,Reminder,Validator}.php`; `api/app/Http/Route.php` (694) |
| kube-manifests | 12,197 | `kube-manifests/prod/settlements/values.yaml` (536), `templates/settlements/`, `templates/settlement-service/` |
| config-proto | 4,257 | `config-proto/gen/registry.go` (482) |
| admin-dashboard | 2,836 | `admin-dashboard/js/admin/Settlements/MerchantConfiguration.js`, `.../components/Preferences.js`, `js/admin/SAV/Settlements/utils.js` |
| alert-rules | 2,325 | `alert-rules/rules/prod-rules/settlements-rules.yaml` (759) |
| fts | — | `fts/config/env.perf.toml:89 URL = "https://settlements-test.perf.razorpay.in/twirp/rzp.settlements.transfer.v1.TransferService/StatusUpdate"` — **FTS posts transfer status webhooks straight into the settlements twirp service** |
| dashboard | 48,156 | mostly `graphify-out/graph.json` noise + `libs/settlement-banner/` |
| proto | — | `proto/settlements/` has 26 sub-packages: `transfer`, `execution`, `schedule`, `ledger_recon`, `inter_nodal_transfer`, `org_bank_account`, `holiday`, `file_generation`, `merchant_config`, `optimizer_settlements`, `ds_settlements`, `entity_scheduler` |

### 2d. capital / lending — total 41,026 / 48 repos
| repo | hits | top files |
|---|---:|---|
| proto | 1,705 | `proto/capital/lender/loan_agreement/v1/loan_agreement_api.proto` (84), `proto/capital/lender/loan/v1/loan_api.proto` (58), `proto/capital/collections/plan/v1/plan_api.proto` (57); sub-pkgs: bnpl, cards, collections, dedupe, es, lender, loc, los, marketplace, policyExecutor, scorecard, transactions |
| api | 4,121 | `api/app/Services/CapitalCardsClient.php → applications.capital_cards`; `CapitalCollectionsClient.php → applications.capital_collections`; `CapitalEarlySettlementClient.php → applications.capital_es`; `CapitalLineOfCreditClient.php → applications.line_of_credit`; `LOSService.php → applications.loan_origination_system` |
| alert-rules | 4,883 | `alert-rules/rules/capital-prod-rules/capital_loc_sloth_rules.yaml` (817), `capital_collections_sloth_rules.yaml` (587) |
| spinacode | 1,200 | `spinacode/capital-bnpl/default.jsonnet`, `capital-scorecard/`, `capital-collections/` — **deploy pipelines** |
| terraform-kong | 1,669 | `terraform-kong/us/prod/api/capital-api-proxy.tf`, `templates/{capital-bnpl,capital-cards,capital-collections,capital-es,capital-lender}` |
| x / dashboard | 3,276 / 6,796 | `x/src/js/views/Capital/`, `x/src/js/views/Routes/__tests__/routes/capital.tsx`; `dashboard/apps/capital/`, `dashboard/web/js/merchant/views/Capital/Loans/index.js` |
| kube-manifests | 3,900 | `kube-manifests/dev/capital-es/prod-rendered.yaml`; templates for capital-{bnpl,cards,collections,edge,es,ftf,gateway,lender,loc,los,marketplace,scorecard} + `camunda-platform-capital-los` |

### 2e. tax (TDS/GST/challan) — total 150,731 / 61 repos (most repos of any family)
| repo | hits | top files |
|---|---:|---|
| **vendor-payments** | 35,619 | `vendor-payments/internal/taxpayments/core.go` (1,396 hits) — **tax payments is implemented inside the vendor-payments service**; `internal/tests/taxpayments_core_test.go` (4,303) |
| x | 42,258 | `x/src/js/views/TaxPayments/`, `x/src/js/views/VendorPayouts/CreateVendorPayout/Step3/index.js` (449) |
| api | 12,455 | `api/app/Services/TaxPayments/Service.php → applications.vendor_payments` (same upstream as vendor payments); `api/config/applications.php:1174 'tax_payment_lite_fe_endpoint'` |
| accounting-integrations | 8,043 | `accounting-integrations/internal/service/tax/` |
| payouts | — | `payouts/internal/database/migrations/20210902153752_create_payout_details_table.go:20: tax_payment_id varchar(30)` — **payouts carries a tax_payment_id FK column** |
| markdown-docs [DOC] | 1,928 | `markdown-docs/x/tax-payments/automatic-tds.md`, `markdown-docs/payroll/tds.md` |
| proto | — | `proto/tax-payments/`, `proto/tax_compliance/` (two distinct packages) |
| kube-manifests | — | `templates/tax-compliance`, `templates/tax-compliance-vault`, `templates/tax-service` |

### 2f. invoices / vendor payments — total 197,777 / 50 repos
| repo | hits | top files |
|---|---:|---|
| vendor-payments | 32,085 | `vendor-payments/internal/vendorpayments/core.go` (2,109), `internal/apiservice/vpserver_test.go`, `internal-accounting-payouts/apiservice/AccountingPayoutsServer.go` (264) |
| x | 35,573 | `x/src/js/views/VendorPayouts/utils.js` (559), `.../InvoiceDetailsView/VendorInvoiceDetailsView.js` (520) |
| api | 21,270 | `api/app/Services/VendorPayments/Service.php`, `VendorPortal/Service.php`, `MerchantInvoiceClient.php → applications.merchant_invoice`, `NcaInvoiceService.php → applications.nca_invoices`, `Bbps/Service.php → applications.bbps`, `EInvoice.php → applications.einvoice` |
| accounting-integrations | 5,607 | `internal/service/job/vp_update_listener_test.go` (263) — listens to vendor-payment updates |
| frontend-x | — | packages `x-invoice-approval/`, `x-vendor-portal/`, `x-accounts-receivable/`, `x-customer-payout-links/` |
| kube-manifests | 2,567 | `kube-manifests/dev/invoices/helmfile-rendered.yaml`, `devpod/merchant-invoice/devpod.yaml`, `templates/vendor-payments/templates/worker-auto-processed-invoice.yaml` |

### 2g. current account / onboarding / KYC — total 172,131 / 57 repos
| repo | hits | top files |
|---|---:|---|
| api | 29,477 | `api/tests/Functional/BankingAccount/Bank/Rbl/BankingAccountTest.php` (2,548); `api/app/Services/BankingAccountService.php → applications.banking_account_service`; `MasterOnboardingService.php → applications.master_onboarding` |
| banking-accounts | 13,406 | `banking-accounts/internal/services/application_aggregate_service.go` (1,144); `banking-accounts/pkg/bvs`, `pkg/balances`, `pkg/accountstatements` |
| business-verification-service-sdk-go | 6,645 | `internalmodules/interfacehandler/clientbvs/bvs_client_test.go` (469) — BVS SDK |
| alert-rules | 9,718 | `alert-rules/rules/prod-rules/bvs_sloth_rules.yaml` (3,424), `bvs_critical_alerts.yaml` (2,776) |
| x | 13,539 | `x/src/js/views/MasterOnboarding/OneCaOnboarding/saga.ts`, `.../CurrentAccounts/ICICIOnboarding/ICICIOnboardingKYCForm.js` |
| payouts | 10,975 | `payouts/internal/app/bankingAccount/core_test.go`, `internal/app/bankingAccountStatement/core_test.go` |

### 2h. forex / cross-border — total 83,471 / 50 repos
| repo | hits | top files |
|---|---:|---|
| api | 9,825 | `api/app/Services/CrossBorderExperienceClient.php → applications.cross_border_experience_service`; `CrossBorderImportServiceClient.php → applications.cross_border_import_service`; `PaymentsCrossBorderClient.php → applications.payments_cross_border_service` |
| payments-upi | 2,080 | `payments-upi/pkg/forex/`, `pkg/crossbordersdk/`, `internal/pkg/clients/cross_border_import/` |
| payouts | — | referenced only as `appConstants.CROSS_BORDER_IMPORT` in the internal-app allow-list (`internal/auth/headers.go:23`) |
| alert-rules | 3,861 | `crossborder_card_warning_alerts.yaml`, `crossborder_card_merchant_alerts.yaml` |
| knowledge-base [DOC] | 8,767 | `knowledge/domains/cross-border/nodes/capabilities/{dcc-mcc-forex,international-onboarding}.content.md` |

*Caveat: this family's regex includes the bare word `international`, which inflates counts heavily in `api` business-category metadata and
in docs. The Payouts-adjacent surface is narrow (one allow-list constant).*

### 2i. insights / reporting — total 34,000 / 50 repos
| repo | hits | top files |
|---|---:|---|
| api | 1,068 | `api/app/Services/Reporting.php → applications.reporting` (82 hits); `api/app/Services/Reporting/` dir |
| terraform-kong | 1,101 | `terraform-kong/templates/reporting-service/reporting-service.tf` (280) — Kong upstream `reporting-service` |
| kube-manifests | 4,141 | `templates/reporting/templates/deployment.yaml`, plus `business-reporting`, `report-generator`, `report-studio`, `reporting-scheduler`, `reporting-applications`, `reporting-assistant` |
| x | 2,197 | `x/src/js/views/FinanceX/store/duck.ts`, `x/src/js/views/Insights/`, `x/src/js/views/Reports/` |
| recon | 4,300 | `recon/matcher/tests/test_reporting_spark_job.py` (173) |
| payouts | — | `appConstants.BUSINESS_REPORTING` in the internal-app allow-list (`internal/auth/headers.go:22`) → **business-reporting service creates/reads payouts** |

### 2j. expense / reimbursement / budgets — total 28,354 / 39 repos
| repo | hits | top files |
|---|---:|---|
| xperience | 6,450 | `xperience/internal/app/core/budgets/core.go` (378), `internal/app/service/budgets/service.go` (358); also `core/pettycash`, `core/expensecategories`, `core/costcenters`, `core/budgetmappings` |
| x | 7,057 | `x/src/js/views/Budgets/Create/CreateOrEditBudget.tsx`, `x/src/js/views/Budgets/PettyCashExpense/`, `x/src/js/views/CostCenters/` |
| accounting-integrations | 1,391 | `internal/workflow_manager/workflows/rx_payout_to_zoho_expense/workflow_test.go` — **payout → Zoho expense workflow** |
| goutils | 813 | `goutils/networkclient/policy/config.go` "retry budget" — false positive |
| proto | — | `proto/budgets/` package exists |

**This domain is already 90% inside the graph** (`xperience` is a graph node). It is depth, not breadth.

### 2k. pricing — total 89,458 / 45 repos
| repo | hits | top files |
|---|---:|---|
| api | 18,886 | `api/app/Models/Pricing/Repository.php` (543), `api/app/Models/Pricing/ChargeCollections/CCRouter.php` (535) |
| charge-collections | 17,732 | `charge-collections/test/integration/pricing_test.go` (2,189), `internal/mdr/pricing/repo_test.go` (833) |
| payouts | 2,474 | `payouts/internal/app/payouts/processor/payout_pricing.go` (219) — **payouts computes its own pricing** |
| payments-upi | 5,389 | `payments-upi/pkg/pricingsdk/`, `internal/app/payment/pricing/` |
| pg-sdk | — | `pg-sdk/method-platform/platform/clients/pricing/` |
| admin-dashboard | 2,401 | `js/admin/costWatch/features/backwardPricingPlans/`, `js/admin/merchants/entity/entityModals/AssignPricingPlan.js` |
| proto | — | `proto/pricing_bundle/`, `proto/charge_collections/`, `proto/charge_collections_sdk/` |

### 2l. accounting integrations — total 29,139 / 45 repos
| repo | hits | top files |
|---|---:|---|
| accounting-integrations | 12,743 | `internal/service/accountingtool/{tally,zoho,flat_file}`, `internal/workflow_manager/workflows/rx_vendor_payment_to_bill/`, `internal/service/zoho_invoice_sync/` |
| x | 6,856 | `x/src/js/views/Accounting/CategorisationFlow/SyncEntryCategorisationFlows/TallyBillCategorisationFlow` |
| vendor-payments | 2,970 | `vendor-payments/internal-accounting-payouts/apiservice/AccountingPayoutsServer.go` (264) |
| api | 620 | `api/app/Services/AccountingPayouts/Service.php → applications.vendor_payments`; `app/Services/GenericAccountingIntegration/` |
| terraform-kong | 1,226 | `templates/accounting-integrations/accounting-integrations-dashboard.tf` |
| dashboard | 390 | `web/js/merchant/views/PartnerAppStore/data/content/{zoho,intuit-quickbooks}.js` |

### 2m. wallet / escrow — total 2,049 / 25 repos (weakest family by volume, but real)
| repo | hits | top files |
|---|---:|---|
| api | 382 | `api/app/Services/Wallet/{Api,Base,Validator}.php → applications.wallet`; `app/Models/Merchant/Webhook/Event.php` |
| admin-dashboard | 189 | `js/admin/FinOpsDashboard/EscrowTagging/{Layout.tsx,customHook/useEscrowData.ts}` |
| stork | 84 | `stork/internal/webhook/event_constants.go` (55), `internal/webhook/escrow_pool_events_test.go` — **escrow pool webhook events** |
| kube-manifests | 99 | `helmfile/charts/settlements/templates/settlements-escrow-fund-check-cronjob-live.yaml` |
| markdown-docs [DOC] | 110 | `markdown-docs/x/account-types/escrow.md`, `escrow/use-cases/{p2p-lending,co-lending}.md` |
| proto | — | `proto/wallet/` has 39 sub-packages (giftcard, fastag, netc, recharge, kyc, program, pool, issuer, …) — this is the **Razorpay Wallet/prepaid-issuing product**, largely disjoint from RazorpayX |

---

## 3) Go module harvest (`go.mod` require + replace)

273 `go.mod` files scanned; 1,608 `github.com/razorpay/<name>` occurrences.

### 3a. Modules referenced but **NOT present in the clone root** (16)
| module | consumers | consuming repos |
|---|---:|---|
| `github.com/razorpay/govaluate` | 7 | ValidX, charge-collections, governor, governor-executor, payments-upi, payouts, pg-sdk |
| `github.com/razorpay/charge-collections-sdk` | 5 | ValidX, charge-collections, payments-upi, payouts, pg-sdk |
| `github.com/razorpay/ifsc` | 3 | charge-collections, payments-upi, payouts |
| `github.com/razorpay/razorpay-go` | 2 | mozart, razorpay-mcp-server |
| `github.com/razorpay/i18nify` | 2 | goutils, mozart |
| `github.com/razorpay/foundation` | 2 | go-foundation-v2, goutils |
| `github.com/razorpay/cross-border-sdk` | 1 | payments-upi |
| `github.com/razorpay/rate-limiter` | 1 | edge |
| `github.com/razorpay/orchestrator` | 1 | mozart |
| `github.com/razorpay/integrations-utils`, `integrations-go`, `govalidator` | 1 each | mozart |
| `github.com/razorpay/devstack-deployment-api` | 1 | kube-manifests |
| `github.com/razorpay/validx`, `kube-manifest`, `gorm` | 1 each | ValidX / kube-manifests / stork |

**Note:** these are all *libraries/SDKs*, not business-domain services. **No adjacent-domain Go service is consumed as a Go module.**
Cross-service calls in this estate go over HTTP/Twirp/gRPC with config-file endpoints, not Go imports — so `go.mod` is a weak discovery
channel for domains and a strong one for shared infra. (OBSERVED.)

### 3b. Modules present in root (42, by consumer count)
`goutils(36)`, `error-mapping-module(21)`, `config-proto(17)`, `rzpconv(9)`, `ledger-sdk(9)`, `rpc(7)`, `governor-executor(7)`, `pg-sdk(5)`,
`shield-sdk(4)`, `business-verification-service-sdk-go(3)`, `relay(2)`, and 31 self-references.

### 3c. Go client packages naming an **external service**
| package path | names external service | domain |
|---|---|---|
| `payouts/pkg/charge-collections` | charge-collections | pricing/fees |
| `payouts/pkg/ledger` | ledger | ledger |
| `payouts/pkg/xbalances`, `payouts/pkg/xAccountStatement` | x-balances, x-account-statements | balances/statements |
| `banking-accounts/pkg/{bvs,balances,accountstatements}` | BVS, x-balances, XAS | onboarding/KYC |
| `virtual-account/pkg/clients/{ledger,xbalance}` | ledger, x-balances | ledger |
| `x-balances/pkg/ledger` | ledger | ledger |
| `vendor-experience/pkg/vendor_payments` + `internal/providers/vendor_payments` | vendor-payments | vendor payments |
| `vendor-experience/internal/providers/{bvs,workflows,batch,stork,ufh,dcs,authz}` | BVS, workflows, batch, stork, ufh | onboarding + platform |
| `accounting-integrations/internal/service/{vendorpayment/vpclient, ledger, mozart, rxclient, ufh, vendor_experience, tax}` | vendor-payments, ledger, mozart, RX, ufh | accounting ↔ vendor payments |
| `accounting-integrations/internal/service/accountingtool/{tally,zoho,zoho/zohoclient,flat_file}` | Tally, Zoho | accounting SaaS |
| `charge-collections/internal/provider/{ledger,api,accountservice,pgrouter,routerservice,subscription,paymentmethods,splitz,stork,ufh}` | ledger, api-monolith, ASV, pg-router | pricing |
| `payments-upi/internal/pkg/clients/{apiledger,ledger,cross_border_import}`, `internal/services/{pricing,recon}`, `pkg/{forex,crossbordersdk,pricingsdk}` | ledger, cross-border-import | forex/pricing |
| `pg-sdk/method-platform/platform/clients/{crossborder,pricing}` | cross-border, pricing | forex/pricing |
| `ledger/internal/provider/{wda,splitz,authz,snsoutboxer,pagerduty}` | WDA, splitz | platform |

**No Go client package anywhere in the clone root names `settlements`, `capital`, `payroll`, `wallet` or `cards`.**
Those edges exist only in the PHP monolith and in config/infra (INFERRED: those domains integrate via `api` or via Kong, not peer-to-peer Go).

---

## 4) PHP monolith `api` — composer packages and service clients

### 4a. `api/composer.json` razorpay/* packages (19 + 23 VCS repositories)
`razorpay/razorpay`, `spine`, `ifsc`, `upi-clients`, `password-strength`, `slack-laravel`, `ufh-sdk-php`, `oauth`, `hodor`, `trace`,
`lqext`, `edge-passport-php`, `outbox-php`, `wda-php-sdk`, `dcs-php-sdk`, `account-service-php-sdk`, `config-proto`,
`redis-counting-semaphore`, `no-leaks` (dev).
Extra VCS-only repos declared: `metrics-php`, `opencensus-php`, `opencensus-php-exporter-jaeger`, `thrift`, `TestDummy`,
`TrustedProxy`, `laravel-tagging`, `PasswordStrengthPackage`.
**None of these 27 packages is in the clone root.** All are platform/SDK, not domain.

### 4b. `app/Services/*` → `applications.<key>` → domain  (complete map, 194 bindings)

Format: `Class` → `applications.<key>` → domain. Config block lives in `api/config/applications.php`.

**Adjacent-domain clients (the ones this pass cares about):**
| class (`api/app/Services/…`) | config key | env var / host (from `api/config/applications.php`) | domain |
|---|---|---|---|
| `Settlements/Base.php` | `applications.settlements_service` | L696 `SETTLEMENTS_LIVE_URL` / `SETTLEMENTS_TEST_URL` + `SETTLEMENTS_DASHBOARD_*` | **settlements** |
| `CapitalCardsClient.php` | `applications.capital_cards` | L1075 `APP_CAPITAL_CARDS_URL` | capital/cards |
| `CapitalCollectionsClient.php` | `applications.capital_collections` | L1082 `APP_CAPITAL_COLLECTIONS_URL` + `..._WEBHOOK_SECRET` | capital |
| `CapitalEarlySettlementClient.php` | `applications.capital_es` | L1050 `APP_ES_URL` | capital |
| `CapitalLineOfCreditClient.php` | `applications.line_of_credit` | L1022 `APP_LINE_OF_CREDIT_URL` | capital |
| `LOSService.php` | `applications.loan_origination_system` | L1014 `APP_LOAN_ORIGINATION_SYSTEM_URL` | capital/lending |
| *(config-only, no client class)* | `applications.capital_marketplace` | L1029 `APP_MARKETPLACE_URL` | capital |
| *(config-only)* | `applications.capital_bnpl` | L1089 `APP_CAPITAL_BNPL_URL` | capital |
| `XPayroll/Service.php` | `applications.xpayroll` | L215 `OPFIN_SERVICE_URL` / `OPFIN_SERVICE_SECRET` | **payroll (opfin)** |
| `VendorPayments/Service.php`, `VendorPortal/Service.php`, `TaxPayments/Service.php`, `AccountingPayouts/Service.php` | `applications.vendor_payments` | L1168 `VENDOR_PAYMENT_URL`, `TAX_PAYMENT_LITE_FE_ENDPOINT`, `VENDOR_PORTAL_MERCHANT_ID` | **vendor payments + tax payments + accounting payouts (one upstream)** |
| `VendorExperience.php` | `applications.vendor_experience` | L1261 | vendor onboarding |
| `MerchantInvoiceClient.php` | `applications.merchant_invoice` | L2360 `MERCHANT_INVOICE_SERVICE_BASE_URL=https://merchant-invoice-base.dev.razorpay.in/` | invoices |
| `NcaInvoiceService.php` | `applications.nca_invoices` | L1110 `NCA_INVOICE_SERVICE_URL` | invoices |
| `EInvoice.php` | `applications.einvoice` | L771 | invoices/GST |
| `Bbps/Service.php` | `applications.bbps` | L2102 provider `setu` (`SETU_CLIENT_ID`, `SETU_IMPERSONATE_IFRAME_URL`) | bill payments |
| `Wallet/Base.php` | `applications.wallet` | L1057 `APP_WALLET_LIVE_URL` / `APP_WALLET_TEST_URL` | wallet |
| `Reporting.php`, `Reporting/` | `applications.reporting` | L834 | reporting |
| `BankingAccountService.php` | `applications.banking_account_service` | L1965 `APP_BANKING_ACCOUNT_SERVICE_URL` | current account |
| `MasterOnboardingService.php` | `applications.master_onboarding` | L1973 `APP_MASTER_ONBOARDING_SERVICE_URL` | onboarding |
| `ChargeCollections.php` | `applications.charge_collections` | L2342 `CHARGE_COLLECTIONS_{TEST,LIVE}_URL` | pricing |
| `CrossBorderExperienceClient.php` / `CrossBorderImportServiceClient.php` / `PaymentsCrossBorderClient.php` | `applications.cross_border_*` | — | forex |
| `SuperleapClient.php` | `applications.superleap_converge` | L1633 `SUPERLEAP_CONVERGE_URL=https://razorpay.superleap.com` | third-party (HR/lending) |
| *(config-only)* | `applications.accounts_receivable` | L1178 | receivables |

**Payouts-core clients already in the graph** (listed for completeness):
`PayoutService/Base.php→applications.payouts_service` (`PAYOUTS_URL`), `PayoutService/PayoutShadowService.php→applications.payouts_shadow_router`,
`PayoutLinks.php→applications.payout_links` + `applications.banking_service_url`, `XBalances.php→applications.x_balances`,
`XAccountStatements.php→applications.x_account_statements` + `applications.banking_account_statement_xas_fallback`,
`FTS/Base.php→applications.fts`, `Ledger.php→applications.ledger`, `VirtualAccountService/Base.php→applications.virtual_accounts_service`
(`VIRTUAL_ACCOUNTS_SERVICE_URL=https://virtual-account-base.dev.razorpay.in`), `Beam/Service.php→applications.beam` + `applications.chota_beam`,
`ReconService.php→applications.recon`, `WorkflowService.php→applications.workflows`, `Batch/BatchMicroService.php→applications.batch`,
`CFAService.php→applications.cfa_service` (`https://cfa.dev.razorpay.in`), `Xperience.php`, `Relay/Base.php→applications.relay`.

**Platform / payments clients (the remaining ~120, one line each):**
`ActivationService/Client.php→activation_service` (`http://activation-service.activation-service.svc.cluster.local:8080`) ·
`AccountService/VpaAsvClient.php→asv_v2` (`asv-grpc.razorpay.com`) · `AffordabilityService.php→affordability` ·
`ApiServiceProvider.php→affordability_service|authz|banking_account_service|batch|beam|card_payment_service|card_vault|charge_collections|checkout_service|cps|credcase|cross_border_experience_service|cross_border_import_service|dcs|developer_console|doppler|drip|elfin|exchange|ezetap|ezetap_device_gatway|freshdesk|fts|gateway_downtime|governor|growth|harvester|hyper_verge|ledger|loan_origination_system|mailgun|mandate_hq|master_onboarding|maxmind|merchant_invoice|mozart|myoperator|nbplus_payment_service|offers_engine|otpelf|partnerships|payments_bank_transfer_service|payments_cross_border_service|payments_mandate|pg_router|pincodesearch|pspx|qr_code_service|raven|razorflow|razorpayx_client|redisdualwrite|reminders|route|salesforce|scrooge|settlements_service|shield|smart_collect|smart_routing|sns|splitz|store_service|terminals_service|tokens|ufh|upi_payment_service|workflows` (central binder) ·
`AuthService.php→auth_service` · `BillMe.php→bill` · `BinService.php→bin_service` · `CMS/Service.php→cms` ·
`CardPaymentService.php→card_payment_service` · `CardVault.php→card_vault` · `CareServiceClient.php→care` ·
`CaseManagementServiceClient.php→case` · `CheckoutAffordabilityApiClient.php→affordability` · `CheckoutService.php→checkout_service` ·
`CorePaymentService.php→cps` · `CyberHelpdeskClient.php→cyber_crime_helpdesk` · `Dcs/ExternalService/Service.php→dcs_service_integrations` ·
`Dcs/Features/Base.php→dcs` · `DeveloperConsole.php→developer_console` · `Device/{Api,Base}.php→ezetap_device_gatway` ·
`DisputesClient.php→disputes` · `DisputeServiceClient.php→dispute_service` (`https://disputes.int.stage.razorpay.in/api/`) ·
`Doppler.php→doppler` · `DowntimeSlackNotification.php`/`Phonepe.php→gateway_downtime` · `Drip.php→drip` · `Elfin/Service.php→elfin` ·
`EmandateService.php→emandate_service` · `EventTrackerClient.php→lumberjack` · `Exchange.php→exchange` ·
`EzetapNotification/EzetapNotification.php→ezetap` · `FavService/Base.php→fav_service` · `FreshchatClient.php→freshchat` ·
`FreshdeskTicketClient.php→freshdesk` · `GoogleMapsClient.php`/`PincodeSearch.php→pincodesearch` · `GovernorService.php→governor` ·
`GrowthService.php→growth` · `Harvester/{EsClient,HarvesterClient}.php→harvester|harvester_v2` · `HubspotClient.php→hubspot` ·
`HyperVerge.php→hyper_verge` · `KubernetesClient.php→kubernetes_client` · `Mailgun.php→mailgun` · `MandateHQ.php→mandate_hq` ·
`MaxMind.php→maxmind` · `Media.php→media_service` · `Mock/Authz*Client.php→authz` · `Mozart.php→mozart` ·
`NbPlus/Service.php→nbplus_payment_service` · `NoCodeAppsService.php→no_code_apps` · `Nodal.php→nodal` ·
`NonBlockingHttp.php→non_blocking_http` · `OffersEngine.php→offers_engine` · `OptimizerCore/Client.php→optimizer_core_service` ·
`OtpElf.php→otpelf` · `Partnerships/PartnershipsService.php→partnerships` · `PaymentLinkService.php→payment_links` ·
`PaymentsBankTransferClient.php→payments_bank_transfer_service` · `PaymentsMandate.php→payments_mandate` ·
`PGRouter.php→pg_router|pg_router_test` · `Pspx/Service.php→pspx` · `QrCodes/Service.php→qr_code_service` · `Raven.php→raven` ·
`Razorflow.php→razorflow` · `RazorpayLabs/SlackApp.php→rzp_labs` · `RazorpayXClient.php→razorpayx_client` · `RazorXClient.php→razorx` ·
`RedisDualWrite.php→redisdualwrite` · `Reminders.php→reminders` · `Route/Base.php→route` ·
`RzpKms/KeyManagementService.php→key_management_service` · `SalesForceClient.php→salesforce` · `Scrooge.php→scrooge` ·
`Shield*.php→shield` · `SmartCollect.php→smart_collect` · `SmartRouting.php→smart_routing` · `SplitzService.php→splitz` ·
`Templating.php→templating` · `TerminalsService.php→terminals_service` · `Tokens.php→tokens` · `Ufh{Client,Service}.php→ufh` ·
`UpiPayment/Service.php→upi_payment_service` · `WDAService.php→wda` · `WhatCmsClient.php→whatcms`.
Directories without an `applications.` binding (internal helpers or AWS/K8s SDK wrappers): `Aws/`, `CircuitBreaker/`, `DashboardUI/`,
`DE/`, `Edge/`, `Geolocation/`, `IDP/`, `Kafka/`, `Mock/`, `Pagination/`, `RazorpayLabs/`, `Segment/`, `SumoLogic/`, `Throttle/`,
`WorkflowGuard/`, `AutoGenerateApiDocs/`.

---

## 5) JS repos — `@razorpay/*` deps and backend hostnames

### 5a. `@razorpay/*` packages
Only design-system / tooling packages exist; **no domain SDKs**:
`@razorpay/blade`, `blade-old`, `blade-old-for-new-auth`, `dashboard-core`, `i18nify-js`, `i18nify-react`, `razor-analytics`,
`razor-analytics-plugins`, `universe-cli`, `universe-doctor`, `universe-utils`, `frontend-care`, `form-renderer`, `form-renderer-blade`,
`passport-node`. `xperience` (Go BFF) has no `package.json` deps.
**Domain information in JS comes from the micro-frontend directory names, not the deps.** (OBSERVED.)

### 5b. Domain surfaces by directory (the useful signal)
- `dashboard/apps/` (42 micro-frontends): **capital, invoices, payroll-app, bbps, bill-payments, wallet, insights, reports, digital-bills,
  smart-collect, recon-saas, money-saver, subscriptions, offers, route, gcms, pos, affordability, partnership, nocode, domains,
  onboarding-experience, entp-analytics, customer-trust, razorpay-ads, rize, ticket-support, magic-checkout, checkout-studio,
  merchant-reviews, agent-marketplace, agentic-dashboard, ecosystem-health, datasync, developers, customers, account-settings,
  payments-core, payments-home, one-home, shell, usl-auth**; plus `dashboard/libs/settlement-banner/`.
- `x/src/js/views/` (RazorpayX dashboard): **Capital (incl. `Capital/CorporateCards`), Payroll, TaxPayments, VendorPayouts,
  Accounting, AccountingPayments, BillPayments, Budgets, CostCenters, Receivables, ReceivablesCollections, Insights, Reports,
  FinanceX, Ledger, MasterOnboarding, CaApplication, CAPortal, ICICIOnboarding, RblLeadCollection, SourceAccounts, SubAccountLimits,
  Payouts, PayoutLinks, Contacts, Workflow, xtra, ConnectedX, AppStore, RxReferral**.
- `frontend-x/` lerna packages: **x-accounts-receivable, x-customer-payout-links, x-invoice-approval, x-vendor-portal, partner-lms**.
- `xperience/internal/app/{core,service}/`: **budgets, budgetmappings, pettycash, expensecategories, costcenters, groups, grouptypes,
  bulkpayouts, collectionagent, paywall, plan, transactions, statements, reports, payoutdowntime, userdetails**.
- `admin-dashboard/js/admin/`: **Settlements (MerchantConfiguration, Preferences), SAV/Settlements, FinOpsDashboard/EscrowTagging,
  costWatch/backwardPricingPlans, plans/Subscription, merchants/entity/AssignPricingPlan**.

### 5c. Distinct backend hostnames found in JS repos (`x`, `xperience`, `frontend-x`, `admin-dashboard`)
| hostname | count | maps to |
|---|---:|---|
| `x.razorpay.com`, `x.dev.razorpay.in`, `x.np.razorpay.in`, `x-func*.np.razorpay.in`, `x-qa.np.razorpay.in` | 79/39/9/… | RazorpayX dashboard |
| `api.razorpay.com`, `api-web.dev.razorpay.in`, `api-web.{qa,int.dev}.razorpay.in`, `beta-api.stage.razorpay.in` | 39/21/… | **api monolith** |
| `dashboard.razorpay.com`, `beta-dashboard.stage.razorpay.in`, `dashboard-rx.{func,qa}.razorpay.in`, `dashboard-axis.dev.razorpay.in` | 963/52/… | merchant dashboard |
| `admin-dashboard.razorpay.com`, `api-dashboard-admin.razorpay.com`, `beta-admin-dashboard.stage.razorpay.in` | 16/4/6 | admin dashboard |
| **`payroll.razorpay.com`** | 3 | **X-Payroll / opfin — service not in clone root** |
| `invoice.razorpay.com`, `invoices.razorpay.in`, `invoices.np.razorpay.in` | 8/4/10 | **invoices service — not in clone root** |
| `accounts-receivable.concierge.stage.razorpay.in` | 4 | **accounts-receivable — not in clone root** |
| `partner-lms.dev.razorpay.in` | 9 | **partner LMS (lending) — not in clone root** |
| `master-onboarding.dev.razorpay.in` | 2 | **master-onboarding — not in clone root** |
| `banking-account.dev.razorpay.in` | 2 | banking-accounts (in root) |
| `x-balances.dev.razorpay.in`, `x-account-statements.dev.razorpay.in`, `payouts.dev.razorpay.in`, `cfa.dev.razorpay.in`, `batch.dev.razorpay.in`, `stork.dev.razorpay.in`, `splitz.dev.razorpay.in`, `raven.dev.razorpay.in`, `workflows.int.dev.razorpay.in`, `edge-base.dev.razorpay.in` | 2-4 each | already-graphed services |
| `xperience.razorpay.com`, `xperience-edge.razorpay.com`, `xperience.dev.razorpay.in`, `xproduct.razorpay.com` | 2/2/1/2 | xperience BFF |
| `accounts.razorpay.com`, `accounts.np.razorpay.in` | 4/4 | identity/account service |
| `lumberjack.{razorpay.com,stage.razorpay.in}` | 3/2 | events pipeline |
| `axis.razorpay.com`, `vajra.razorpay.com`, `sftp.razorpay.com`, `ifsc.razorpay.com`, `deploy.razorpay.com`, `registry.razorpay.com` | 2-9 | infra/partner |

*No `*-service` or `*.svc.cluster.local` hostnames appear in the JS repos — the browser talks to Kong/edge only.* (OBSERVED.)

---

## 6) Ownership — CODEOWNERS teams and Slack channels

### 6a. CODEOWNERS (45 files) — teams relevant to adjacent domains
| repo | team handles (domain-relevant subset) |
|---|---|
| `api/.github/CODEOWNERS` | **`@razorpay/TechSettlements`**, **`@razorpay/Capital_BE`**, **`@razorpay/TechCards`**, **`@razorpay/pricing-team`**, `@razorpay/Payouts-core`, `@razorpay/tech-banking`, `@razorpay/TechCrossBorder`, `@razorpay/pg-onboarding-service`, `@razorpay/TechOnlinePaymentsOnboarding`, `@razorpay/identity-core`, `@razorpay/nocode`, `@razorpay/partnership-be`, `@razorpay/scrooge`, `@razorpay/spine-edge`, `@razorpay/TechRulesRouting`, `@razorpay/techpaymentscore`, `@razorpay/techupicore`, `@razorpay/enterprise-apps`, `@razorpay/onesupport-dev`, `@razorpay/qr-code-devs`, `@razorpay/cdp`, `@razorpay/ZeroTouchWarriors`, `@razorpay/devstack-devs`, `@razorpay/ob-infra-backend-pr-reviewers`, `@razorpay/checkout-backend-leads` |
| `config-proto/CODEOWNERS` | `@razorpay/capital_be`, `@razorpay/capital-loc-reviewers`, `@razorpay/capital-onboarding-be`, `@razorpay/techsettlements`, `@razorpay/techcards`, `@razorpay/wallet`, `@razorpay/vendor-onboarding-devs`, `@razorpay/ledger-team`, `@razorpay/payouts-core`, `@razorpay/x-acquisition-be`, `@razorpay/digital_billing`, `@razorpay/reporting-team`, `@razorpay/tech-banking`, `@razorpay/pricing-team` (+40 more) |
| `terraform-kong/.github/CODEOWNERS` | `@razorpay/capital_be`, `@razorpay/capital-bnpl-reviewers`, `@razorpay/credit-on-upi-engineering`, `@razorpay/techsettlements`, `@razorpay/techcards`, `@razorpay/gc_issuing`, `@razorpay/razorpayx_be`, `@razorpay/razorpayx_payouts_be`, `@razorpay/vendor-onboarding-devs`, `@razorpay/recon-saas-devs`, `@razorpay/x-cbu-devs`, `@razorpay/pricing-team`, `@razorpay/reporting-team`, `@razorpay/coe-abac-workflows` (+130 more) |
| `payouts/.github/CODEOWNERS` | `@razorpay/razorpayx_payouts_be` |
| `x/.github/CODEOWNERS` | `@razorpay/mandatoryxreviewers`, **`@razorpay/capital_fe`**, `@razorpay/tech-banking` |
| `vendor-payments`, `accounting-integrations`, `frontend-x` | `@razorpay/mandatoryxreviewers` (single X-wide reviewer group) |
| `vendor-experience/.github` | `@razorpay/vendor-onboarding-devs` |
| `charge-collections/.github` | `@razorpay/pricing-team` |
| `xperience/.github` | `@razorpay/xperience-devs` |
| `x-account-statements/.github` | `@razorpay/xas-recon-devs` |
| `banking-accounts/.github` | `@flanker-23`, `@harshitsidhwa` (individuals only — no team handle) |
| `recon`, `virtual-account`, `ValidX`, `workflows` | `@razorpay/recon-dev`, `@razorpay/virtual-account-dev`, `@razorpay/validx-devs`, `@coe-abac-workflows` |
| `ledger/.github` | `@razorpay/ledger-team` |
| `admin-dashboard` | `@razorpay/Reporting-Team`, `@razorpay/sme-dashboard`, `@razorpay/ssab-fe` |
| `mozart/.github` | `@razorpay/Capital_BE`, `@razorpay/TechCards`, `@razorpay/RazorpayX_BE`, `@razorpay/tech-banking` |
| `proto/.github` | `@razorpay/capital_be`, `@razorpay/checkout-service-cards`, `@razorpay/tech-banking`, `@razorpay/ledger-team`, `@razorpay/payments-sav` |
| `spinacode/.github` | `@razorpay/pricing-team`, `@razorpay/ledger-team`, `@razorpay/identity-core` |
| `kube-manifests` (2 files) | `@razorpay/pricing-team`, `@razorpay/ledger-team`, `@razorpay/techcards`, `@razorpay/DevOps`, `@razorpay/dataplatform-team` |
| `authz/CODEOWNERS` | `@razorpay/wallet-dev`, `@razorpay/techcards`, `@razorpay/techcommonplatforms` |

### 6b. Slack channels (from alert-rules / docs / code) — domain-owning teams
| domain | channels |
|---|---|
| **payroll** | `#xpayroll-alerts` (180), `#execute-payroll` |
| **capital** | `#capital_los_alerts` (53), `#capital_risk_alerts` (41), `#capital_cards_alerts` (19), `#capital_collections_alerts` (18), `#capital_new_los_alerts` (13), `#capital_lending_oncall_alerts` (12), `#capital-lender-stage-alerts` (9) |
| **settlements** | `#settlement-alerts` (24), `#settlements_critical_p0_alerts` (16), `#settlement-cycle` (14), `#settlement-alerts-test` (22) |
| **cards** | `#tech_payments_cards_alerts` (162), `#pos-cards-alerts` (117), `#card_recurring_alerts` (51), `#payments_cards_l0_alerts` (28), `#payments_cards` (15), `#cards` (18), `#payments_cards_authentication` (30) |
| **vendor payments / accounting** | `#vendor_payments` (63), `#x-accounting-alerts` (52), `#x-vendor-experience-alerts` (11), `#x-vendor-payments-oncall` (9) |
| **invoices / bills** | `#invoices-core-alerts` (36), `#bill_payments_bbps_alerts` (33) |
| **onboarding / KYC** | `#master-kyc-alerts` (74), `#onboarding-infra-alerts` (150), `#i18n-onboarding-alerts` (207), `#magic-onboarding-alerts` (48), `#p0_alerts_payments_onboarding` (69), `#onboarding-observability` (17) |
| **cross-border/forex** | `#cross-border-import-alerts` (205), `#cross-border-alerts` (148), `#payments_cross_border` (63), `#cross-border-p0-alerts` (63), `#sg-cross-border-alerts` (34) |
| **reporting** | `#cxp_reporting_p0_alerts` (40), `#cxp_reporting_p1_alerts` (20), `#reporting-crons` (8), `#reports` (8) |
| **banking / X core** | `#banking-devops-alerts` (259), `#banking_dev_alerts` (152), `#banking_vas_oncall_alerts` (93), `#tech_banking_program_vas` (61), `#x-alerts` (201) |
| **pricing** | `#charge-collections-alerts` (258) |
| **ledger** | `#platform-ledger-alerts` (682) |

---

## 7) Ranked candidate domains by evidence strength

Score = (# DISTINCT repos with **real code/config paths**, not doc strings) × (does the payouts service itself name it?) × (does a
deployable service + Kong upstream + proto package + alert channel + owning team all exist?).

| # | domain | payouts allow-list? | proto pkg | k8s svc | kong upstream | api client | own alerts+team | repos w/ real paths | strength |
|---:|---|:--:|:--:|:--:|:--:|:--:|:--:|---:|---|
| 1 | **Settlements** | ✅ `SETTLEMENTS_SERVICE` | ✅ `proto/settlements/` (26 sub-pkgs) | ✅ `settlements`, `settlement-service`, `settlements-dark` | ✅ `settlements` | ✅ `Settlements/{Api,Base,Dashboard,MerchantDashboard,Payout,Reminder}` | ✅ `#settlement-alerts` / `@razorpay/TechSettlements` | api, kube-manifests, terraform-kong, alert-rules, admin-dashboard, config-proto, fts, dashboard, proto (9) | **VERY STRONG** |
| 2 | **Capital / lending** (LOS, LOC, lender, collections, ES, BNPL, marketplace, scorecard, corporate cards) | ✅ ×2 (`CAPITAL_COLLECTIONS_CLIENT`, `CAPITAL_EARLY_SETTLEMENTS`) | ✅ `proto/capital/` (13 sub-pkgs) | ✅ 12 `capital-*` charts + `camunda-platform-capital-los` | ✅ 5 `capital-*` | ✅ 5 clients + 2 config-only | ✅ 7 channels / `@razorpay/Capital_BE`, `@razorpay/capital_fe` | api, proto, alert-rules, spinacode, terraform-kong, kube-manifests, x, dashboard, mozart, config-proto (10) | **VERY STRONG** |
| 3 | **Vendor payments + Tax payments** | ✅ `VENDOR_PAYMENTS` (+contact-type `tax_payment`) | ✅ `proto/vendor-payments`, `vendor-portal`, `tax-payments`, `tax_compliance` | ✅ `vendor-payments`, `tax-compliance`, `tax-service` | ✅ `vendor-payments`, `vendor-experience` | ✅ 4 classes on one key | ✅ `#vendor_payments` / `@razorpay/mandatoryxreviewers`, `@razorpay/vendor-onboarding-devs` | vendor-payments, vendor-experience, accounting-integrations, x, frontend-x, api, payouts (7) — **source already cloned** | **VERY STRONG (and already in-clone)** |
| 4 | **X-Payroll (opfin)** | ✅ `X_PAYROLL` (+contact-type `xpayroll`) | ✅ `proto/x-payroll/` (8 sub-pkgs) | ✅ `opfin`, `opfin-{compliance,demo,dev,edge}`, `x-payroll-compute`, `x-salary-structure`, `x-tna`, `x-payroll-hris`, `x-payroll-flexible-benefits` | ✅ `opfin` | ✅ `XPayroll/Service.php` (`OPFIN_SERVICE_URL`) | ✅ `#xpayroll-alerts` | proto, kube-manifests, alert-rules, api, dashboard, x, self-serve-analytics, knowledge-base[DOC] (7) | **STRONG** |
| 5 | **Current-account onboarding / KYC / BVS** | ➖ (upstream of payouts, not a caller) | ✅ `master_onboarding`, `banking-bridge` | ✅ `master-onboarding`, `banking-account`, `banking-bridge`, `bvs`, `bvs-2`, `bvs-edge` | ✅ `banking-accounts`, `banking-bridge` | ✅ `BankingAccountService`, `MasterOnboardingService` | ✅ `#master-kyc-alerts` | banking-accounts, business-verification-service-sdk-go, api, x, alert-rules, payouts, kube-manifests (7) — **partially cloned** | **STRONG (mostly already graphed)** |
| 6 | **Accounting integrations (Tally/Zoho/QuickBooks)** | ➖ (calls payouts via api) | ✅ `accounting_integrations`, `accounting-payouts` | ✅ `accounting-integrations` | ✅ `accounting-integrations` | ✅ `AccountingPayouts/Service.php`, `GenericAccountingIntegration/` | ✅ `#x-accounting-alerts` | accounting-integrations, vendor-payments, x, api, terraform-kong, alert-rules (6) — **source cloned** | **STRONG (in-clone; depth work)** |
| 7 | **Expense / Budgets / Petty cash / Cost centres** | ➖ (inside xperience) | ✅ `proto/budgets/` | ✅ `budgets` (kong) | ✅ `budgets` | ➖ | ➖ (rides `#x-alerts`) | xperience, x, accounting-integrations, proto (4) — **source cloned** | **MEDIUM-STRONG (depth, not breadth)** |
| 8 | **Pricing / fees / charge collections** | ✅ `ChargeCollectionsInternal` | ✅ `charge_collections`, `charge_collections_sdk`, `pricing_bundle` | ✅ `charge-collections` | ✅ `charge-collections`, `charge-collections-admin-dashboard` | ✅ `ChargeCollections.php` | ✅ `#charge-collections-alerts` / `@razorpay/pricing-team` | charge-collections, api, payouts, payments-upi, pg-sdk, admin-dashboard (6) — **source cloned** | **MEDIUM-STRONG (partly graphed)** |
| 9 | **Reporting / business-reporting / insights / FinanceX** | ✅ `BUSINESS_REPORTING` | ✅ `business_reporting`, `merchant_analytics` | ✅ `reporting`, `reporting-service`, `business-reporting`, `report-generator`, `report-studio`, `reporting-scheduler` | ✅ `reporting-service`, `reporting-assistant`, `business-reporting` | ✅ `Reporting.php` + `Reporting/` | ✅ `#cxp_reporting_p0_alerts` / `@razorpay/Reporting-Team` | api, terraform-kong, kube-manifests, x, alert-rules, recon (6) | **MEDIUM** |
| 10 | **Accounts receivable / invoices / bill payments (BBPS)** | ✅ `ACCOUNTS_RECEIVABLE` | ✅ `accounts_receivable`, `invoices`, `merchant_invoice`, `bbps`, `x_bill_payments`, `digital_billing` | ✅ `accounts-receivable`, `accounts-receivable-edge`, `invoices`, `merchant-invoice`, `bill-payments`, `x-bill-payments` | ✅ `invoices`, `merchant_invoice`, `bill-payments`, `backend/frontend-accounts-receivable` | ✅ `MerchantInvoiceClient`, `NcaInvoiceService`, `EInvoice`, `Bbps/Service` | ✅ `#invoices-core-alerts`, `#bill_payments_bbps_alerts` | api, frontend-x, x, dashboard, kube-manifests, terraform-kong, proto (7) | **MEDIUM** |
| 11 | **Cards (corporate cards, card issuing)** | ➖ (only via capital) | ✅ `proto/capital/cards`, `payments-card` | ✅ `capital-cards`, `cardholder-portal*`, `card-tokenizer`, `issuer-transaction-processor` | ✅ `capital-cards` | ✅ `CapitalCardsClient` | ✅ `#capital_cards_alerts`, `#tech_payments_cards_alerts` / `@razorpay/TechCards` | api, mozart, fts, x, dashboard, terraform-kong, kube-manifests (7) | **MEDIUM** — sub-domain of Capital; issuing side (`cardholder-portal`, tokenization) belongs to Payments, not X |
| 12 | **Forex / cross-border** | ✅ `CROSS_BORDER_IMPORT` | ✅ `cross_border_experience`, `cross_border_import`, `payments_cross_border` | ✅ `cross-border-import`, `cross-border-experience`, `payments-cross-border` | ✅ same | ✅ 3 clients | ✅ 5 channels / `@razorpay/TechCrossBorder` | api, payments-upi, pg-sdk, alert-rules, terraform-kong, kube-manifests (6) | **MEDIUM** — large domain but its payouts touchpoint is one constant |
| 13 | **Wallet / escrow** | ➖ | ✅ `proto/wallet/` (39 sub-pkgs), `wallet_amazonpay` | ✅ `wallet`, `wallet-edge`, `shopify-wallet` | ✅ `wallet`, `wallet-test` | ✅ `Wallet/{Api,Base}` | ➖ `@razorpay/wallet-dev` | api, admin-dashboard, stork, kube-manifests, proto (5) | **WEAK-MEDIUM** — `proto/wallet` is the prepaid/gift-card issuing product, largely disjoint from RazorpayX payouts |

---

## 8) Probable repo / service names NOT in the clone root

Ordered by how directly they touch Payouts. "Where the name came from" is the evidence channel.

| probable repo/service | evidence source (path:line / dir) | likely owning team |
|---|---|---|
| `settlements` (a.k.a. `settlement-service`) | `kube-manifests/prod/settlements/values.yaml`; `terraform-kong/templates/settlements/`; `fts/config/env.perf.toml:89` twirp URL `rzp.settlements.transfer.v1.TransferService`; `proto/settlements/` | `@razorpay/TechSettlements`, `#settlement-alerts` |
| `capital-los` / `capital-loc` / `capital-lender` / `capital-collections` / `capital-es` / `capital-bnpl` / `capital-cards` / `capital-marketplace` / `capital-scorecard` / `capital-ftf` / `capital-gateway` / `capital-edge` | `kube-manifests/templates/capital-*`; `terraform-kong/templates/capital-*`; `spinacode/capital-{bnpl,scorecard,collections}/default.jsonnet`; `proto/capital/{los,loc,lender,collections,es,bnpl,cards,marketplace,scorecard}` | `@razorpay/Capital_BE`, `@razorpay/capital-loc-reviewers`, `@razorpay/capital-bnpl-reviewers`, `#capital_*_alerts` |
| `opfin` / `xpayroll` (+ `x-payroll-compute`, `x-salary-structure`, `x-tna`, `x-payroll-hris`, `x-payroll-flexible-benefits`, `opfin-compliance`) | `kube-manifests/dev/opfin/helmfile-rendered.yaml`; `terraform-kong/templates/opfin/`; `api/config/applications.php:215 OPFIN_SERVICE_URL`; `proto/x-payroll/*`; hostname `payroll.razorpay.com` | `#xpayroll-alerts` |
| `wallet` | `terraform-kong/templates/wallet/`, `kube-manifests/templates/wallet*`, `proto/wallet/`, `api/config/applications.php:1057` | `@razorpay/wallet`, `@razorpay/wallet-dev` |
| `business-reporting` | `payouts/internal/auth/headers.go:22 appConstants.BUSINESS_REPORTING`; `kube-manifests/templates/business-reporting`; `terraform-kong/templates/business-reporting`; `proto/business_reporting/` | `@razorpay/reporting-team` |
| `accounts-receivable` | `payouts/internal/auth/headers.go:21 appConstants.ACCOUNTS_RECEIVABLE`; `kube-manifests/templates/accounts-receivable{,-edge}`; `terraform-kong/templates/{backend,frontend}-accounts-receivable`; `proto/accounts_receivable/`; hostname `accounts-receivable.concierge.stage.razorpay.in` | (unlisted; adjacent to `@razorpay/mandatoryxreviewers`) |
| `master-onboarding` | `api/app/Services/MasterOnboardingService.php` → `applications.master_onboarding`; `kube-manifests/templates/master-onboarding`; `proto/master_onboarding/`; hostname `master-onboarding.dev.razorpay.in` | `#master-kyc-alerts` |
| `banking-bridge` | `proto/banking-bridge/`; `kube-manifests/templates/banking-bridge{,-dark}`; `terraform-kong/templates/banking-bridge{,-dark}` | `@razorpay/tech-banking` |
| `reporting-service` / `report-generator` / `report-studio` / `reporting-scheduler` | `terraform-kong/templates/reporting-service/reporting-service.tf`; `kube-manifests/templates/report*`; `api/app/Services/Reporting.php` | `@razorpay/Reporting-Team`, `#cxp_reporting_p0_alerts` |
| `invoices` / `merchant-invoice` / `nca-invoice-service` | `kube-manifests/dev/invoices/helmfile-rendered.yaml`, `devpod/merchant-invoice/devpod.yaml`; `terraform-kong/templates/{invoices,merchant_invoice}`; `api/config/applications.php:2360 MERCHANT_INVOICE_SERVICE_BASE_URL`, `:1110 NCA_INVOICE_SERVICE_URL`; `proto/{invoices,merchant_invoice}/` | `#invoices-core-alerts` |
| `bill-payments` / `x-bill-payments` (BBPS) | `kube-manifests/templates/{bill-payments,x-bill-payments}`; `terraform-kong/templates/bill-payments`; `api/app/Services/Bbps/Service.php`; `proto/{bbps,x_bill_payments}/`; provider `setu` | `#bill_payments_bbps_alerts` |
| `tax-compliance` / `tax-compliance-vault` / `tax-service` | `kube-manifests/templates/tax-compliance*`, `templates/tax-service`; `proto/tax_compliance/` (distinct from `proto/tax-payments/` which is served by the cloned `vendor-payments`) | (unlisted) |
| `loan-origination-system` (LOS, also `camunda-platform-capital-los`) | `api/app/Services/LOSService.php` → `applications.loan_origination_system` (`APP_LOAN_ORIGINATION_SYSTEM_URL`); `kube-manifests/templates/capital-los`, `camunda-platform-capital-los`; hostname `partner-lms.dev.razorpay.in` | `@razorpay/Capital_BE`, `#capital_los_alerts` |
| `scrooge` | `payouts/internal/auth/headers.go:15 appConstants.SCROOGE`; `api/app/Services/Scrooge.php`; `kube-manifests/templates/scrooge*`; `proto/scrooge/` | `@razorpay/scrooge` |
| `cross-border-import` / `cross-border-experience` | `payouts/internal/auth/headers.go:23`; `api/app/Services/CrossBorder*Client.php`; `terraform-kong/templates/cross-border-*`; `proto/cross_border_*` | `@razorpay/TechCrossBorder`, `@razorpay/cross-border-reviewers` |
| `charge-collections-sdk` (Go module) | `go.mod` require in ValidX, charge-collections, payments-upi, payouts, pg-sdk | `@razorpay/pricing-team` |
| `credithub`, `flash-credit`, `credit-on-upi` | `kube-manifests/templates/{credithub,credithub-gateway-mozart,flash-credit}`; `proto/credithub/`; `terraform-kong` CODEOWNERS `@razorpay/credit-on-upi-engineering` | (lending adjacent) |
| `accrual-engine`, `commissions`, `digital-billing-service`, `store-management-service`, `merchant-product-service` | `proto/{accrual_engine,commissions,digital_billing,store_management,merchant_product_service}/`; matching `kube-manifests/templates/*` and `terraform-kong/templates/*` | (unlisted) |
| SDK/lib modules (non-domain) | `govaluate`, `ifsc`, `razorpay-go`, `i18nify`, `foundation`, `cross-border-sdk`, `rate-limiter`, `orchestrator`, `integrations-utils`, `integrations-go`, `govalidator`, `devstack-deployment-api` — all from `go.mod` | platform teams |
| PHP composer packages (non-domain) | `spine`, `trace`, `hodor`, `oauth`, `lqext`, `ufh-sdk-php`, `dcs-php-sdk`, `wda-php-sdk`, `account-service-php-sdk`, `edge-passport-php`, `outbox-php`, `metrics-php`, `opencensus-php*`, `thrift`, `slack-laravel`, `redis-counting-semaphore`, `no-leaks`, `TrustedProxy`, `laravel-tagging`, `TestDummy`, `PasswordStrengthPackage` | platform teams |

---

## 9) Unknowns / caveats

1. **UNKNOWN — direction of the Settlements↔Payouts edge.** `payouts/internal/auth/headers.go:16` proves settlements is allowed to call
   payouts, and `fts/config/env.perf.toml:89` proves FTS webhooks *into* settlements. Whether settlements creates payouts, or only reads
   them, needs the `settlements` repo (not cloned) or a Kong route dump.
2. **UNKNOWN — whether `capital_es` (early settlement) is a payouts producer or a settlements producer.** It appears in *both* the payouts
   allow-list (`headers.go:24`) and in settlement-named config. Two plausible readings; not resolvable in-clone.
3. **UNKNOWN — the payout contact-type edges are one-way asserted.** `payouts/internal/app/contact/type.go:16-21` shows what each internal
   app is *permitted* to do; it is not evidence of current traffic volume. No metrics were available in-clone.
4. **UNKNOWN — actual hostnames.** Nearly every `applications.*` block resolves to a bare `env('…')` with no default, so the only concrete
   hosts recovered are dev/stage defaults (§4b, §5c). Prod topology must come from `kube-manifests` values or Kong.
5. **CAVEAT — regex noise.** The `settlements` family regex includes `settle_`/`settled_at`, and `forex` includes bare `international`;
   both inflate `api`, `dashboard` and `self-serve-analytics` counts substantially. `dashboard/graphify-out/graph.{json,html}` and
   `kube-manifests/devstack-prod-parity/reports/*.html` are generated artefacts that dominate several rows and were not de-weighted.
6. **CAVEAT — `knowledge-base` already contains a payroll graph** (`knowledge/domains/payroll/registry/{nodes,edges}.yaml`, 909/1349 hits).
   That is a *prior modelling artefact*, not independent evidence of wiring; it should be reconciled, not counted twice.
7. **UNKNOWN — `proto/wallet/` ownership.** 39 sub-packages (giftcard, fastag, netc, vkyc, program, issuer) suggest a prepaid-issuing
   product line distinct from RazorpayX. Whether `applications.wallet` in `api` points at the same service is not verifiable in-clone.
8. **NOT SCANNED — `payments-upi`, `mozart`, `edge`, `stork`, `dcs`, `governor`, `splitz`, `relay`, `memoir`, `tejas`, `devstack`,
   `data-mcp`, `razorpay-mcp-server`, `security-action`, `alert-rules`** were included in the keyword sweep but not read for
   route/table-level detail; only aggregate counts and top files are reported for them.
