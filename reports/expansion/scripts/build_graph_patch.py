#!/usr/bin/env python3
"""Build the evidence-backed graph patch for the RazorpayX adjacent-domain expansion.

Writes:
  reports/expansion/GRAPH_PATCH.json                 -- nodes/edges to ADD or RELABEL (base graph untouched)
  reports/expansion/PAYOUTS_SERVICE_GRAPH.expanded.json -- base graph + patch applied (derived artifact)
  reports/expansion/GRAPH_PATCH_VALIDATION.json      -- id uniqueness / dangling-edge / evidence checks

Every node and edge carries `evidence` (repo + file:line + symbol) and `confidence` in
{confirmed, probable, inferred, unknown}. Nodes/edges with confidence=unknown are the "unknown edges"
requested by the task: they are recorded so they can be resolved, not asserted as fact.

Source reports: reports/expansion/findings/01..08_*.md (2026-09-07 discovery pass).
"""
import json, pathlib, collections, sys

HERE = pathlib.Path(__file__).resolve().parent.parent          # reports/expansion
REPORTS = HERE.parent                                            # reports/
BASE = REPORTS / "PAYOUTS_SERVICE_GRAPH.json"

def N(id, type, label, **kw):
    d = {"id": id, "type": type, "label": label}
    d.setdefault("accessible", kw.pop("accessible", None))
    d.update(kw)
    return d

def E(source, target, type, mechanism, repository, file_path, symbol, confidence="confirmed", **kw):
    d = {"source": source, "target": target, "type": type, "mechanism": mechanism,
         "repository": repository, "file_path": file_path, "symbol": symbol,
         "confidence": confidence, "evidence_type": "code", "environment": "prod"}
    d.update(kw)
    return d

# ----------------------------------------------------------------------------------------------
# RELABELS of existing stub nodes (id kept; label/note replaced). Base nodes are NOT deleted.
# ----------------------------------------------------------------------------------------------
relabel = [
    N("svc:settlements", "service", "Settlements service (PG-side; Twirp rzp.settlements.transfer.v1) — NOT ACCESSIBLE",
      repo="settlements", accessible=False, lifecycle="current", domain="settlements",
      note="Was an unexplained stub. Now evidenced by 8 independent hooks: own SNS topic payout-updates-settlements, "
           "TransferService.StatusUpdatePayout, FTS product SETTLEMENT + auth user, ledger client settlements_key, "
           "monolith source_type=settlements. Owner @razorpay/TechSettlements (PG BU), not RazorpayX.",
      evidence="payouts/config/prod.toml:371; api/app/Services/Settlements/Payout.php:14,35; fts/config/env.prod-live.toml:171-223; "
               "proto/settlements/transfer/v1/transfer_api.proto:116; ledger/config/prod-live.toml:174-281"),
    N("svc:wallet", "service", "RazorpayWallet (api.razorpaywallet.com; Issuing/gift-card BU) — NOT ACCESSIBLE",
      repo="wallet", accessible=False, lifecycle="current", domain="wallet",
      note="Was an unexplained stub. FTS products CUSTOMER_PAYOUT/CUSTOMER_WALLET, FTS+CFA auth user wallet, ledger client + "
           "fund_account_types amazonpay/rx_wallet, banking-accounts rx_wallet account type, 33 protos under proto/wallet. "
           "Owner @razorpay/gc_issuing — NOT a RazorpayX product.",
      evidence="fts/config/env.prod-live.toml:171-223; cfa/config/prod.toml; ledger/internal/common/constant.go:221-322; "
               "terraform-kong/.github/CODEOWNERS:76-77"),
    N("svc:beam", "service", "Beam / chotabeam — bank file relay (py; crons) — NOT ACCESSIBLE",
      repo="beam", accessible=False, lifecycle="current", domain="shared-infra",
      evidence="kube-manifests/prod/beam/values.yaml:19-20; kube-manifests/prod/chotabeam/values.yaml:7-55; api/config/applications.php:982-987"),
]

# ----------------------------------------------------------------------------------------------
# NEW NODES
# ----------------------------------------------------------------------------------------------
nodes = []
add = nodes.append

# --- Domain / journey nodes (new node type) ---
for did, label, ev in [
    ("domain:s2p", "Vendor Payments + Tax Payments/TDS (Source-to-Pay apps)", "api/app/Models/PayoutSource/Entity.php:23-39; vendor-payments/internal/"),
    ("domain:payroll", "Payroll (XPayroll / Opfin)", "payouts/internal/auth/headers.go:12; api/app/Services/XPayroll/Service.php"),
    ("domain:capital", "Capital (LOC, collections, early settlement, BNPL, corporate cards)", "proto/capital; api/app/Services/CapitalCollectionsClient.php"),
    ("domain:settlements", "PG Settlements consuming X payouts (on-demand / early settlement)", "api/app/Services/RazorpayXClient.php:131-190"),
    ("domain:ca-onboarding", "Current-Account onboarding + KYC (master-onboarding, BVS, banking-accounts)", "agent-skills/teams/banking/skills/ca-onboarding-oncall-debugger/SKILL.md:41-88"),
    ("domain:accounting", "Accounting integrations + business reporting", "accounting-integrations/config/prod.toml:196,204"),
    ("domain:cross-border", "Cross-border import / ICA transfers", "api/app/Models/Payout/SourceUpdater/Factory.php:80"),
]:
    add(N(did, "business domain", label, accessible=None, evidence=ev))

# --- Repositories (probable names; accessible=False unless cloned) ---
repos = [
    ("repo:vendor-experience", "razorpay/vendor-experience", True, "s2p", "clone root; gh_repos.txt"),
    ("repo:x-payroll", "razorpay/x-payroll (deployable `opfin`) — 404", False, "payroll", "spinacode/v3/opfin/prod/mum-rspl/app.json \"repo\": \"x-payroll\""),
    ("repo:x-payroll-compute", "razorpay/x-payroll-compute — 404", False, "payroll", "spinacode/x-payroll-compute/default.jsonnet:4,11,18"),
    ("repo:x-payroll-compliance", "razorpay/x-payroll-compliance (deployable opfin-compliance) — 404", False, "payroll", "spinacode/v3/opfin-compliance/prod/mum-rspl/*"),
    ("repo:x-payroll-hris", "razorpay/x-payroll-hris — 404", False, "payroll", "proto/x-payroll/hris/v1/employee_custom_fields_api.proto:6 go_package"),
    ("repo:x-payroll-flexible-benefits", "razorpay/x-payroll-flexible-benefits — 404", False, "payroll", "proto/x-payroll/flexible-benefits/v2/assignment_service.proto:5 go_package"),
    ("repo:x-salary-structure", "razorpay/x-salary-structure — 404", False, "payroll", "kube-manifests/prod/x-salary-structure/values.yaml:2-3"),
    ("repo:x-attendance", "razorpay/x-attendance (image x-tna) — accessible, EMPTY template", True, "payroll", "gh api repos/razorpay/x-attendance (internal, 6KB); README.md = 'template'"),
    ("repo:x-payroll-commons", "razorpay/x-payroll-commons — accessible, EMPTY template", True, "payroll", "gh api repos/razorpay/x-payroll-commons (internal, 7KB); README.md = 'template'"),
    ("repo:tax-compliance", "razorpay/tax-compliance — 404", False, "s2p", "proto/tax_compliance/compliance/tds_payments/v1/tds_payments.proto:5 go_package; spinacode/tax-compliance"),
    ("repo:tax-payments", "razorpay/tax-payments (GitHub snapshot; likely folded into vendor-payments) — 404", False, "s2p", "knowledge-base GitHub snapshot; proto/tax-payments/service.proto:10 package vendorpayments.taxpayments"),
    ("repo:capital-cards", "razorpay/capital-cards — 404", False, "capital", "kube-manifests/prod-capital/capital-cards; api/app/Services/CapitalCardsClient.php"),
    ("repo:capital-collections", "razorpay/capital-collections — 404", False, "capital", "kube-manifests/prod-capital/capital-collections; api/config/applications.php:1083"),
    ("repo:capital-loc", "razorpay/capital-loc (line-of-credit) — 404", False, "capital", "api/config/applications.php:1023; proto/capital/loc"),
    ("repo:capital-es", "razorpay/capital-es (early/on-demand settlement) — 404", False, "settlements", "rzpconv/rzpconv/core.yaml; proto/capital/es/cron/v1/cron_api.proto:9"),
    ("repo:settlements", "razorpay/settlements — 404", False, "settlements", "kube-manifests/templates/settlements; proto/settlements"),
    ("repo:master-onboarding", "razorpay/master-onboarding — 404", False, "ca-onboarding", "kube-manifests/prod/master-onboarding/values.yaml:6,12; banking-accounts/config/default.toml:103-106"),
    ("repo:business-verification-service", "razorpay/business-verification-service (bvs) — 404", False, "ca-onboarding", "spinacode/bvs (local repo); kube-manifests/prod/bvs/values.yaml:6"),
    ("repo:accounts-receivable", "razorpay/accounts-receivable (X Invoices backend) — 404", False, "s2p", "kube-manifests/prod/accounts-receivable/values.yaml:9,16,19; api/config/applications.php:1180"),
    ("repo:business-reporting", "razorpay/business-reporting — 404", False, "accounting", "api/config/applications.php:1188; payouts/internal/auth/headers.go:21"),
    ("repo:budgets", "razorpay/budgets (Budgets & PettyCash) — 404", False, "s2p", "proto/budgets/budgets/v1/budgets_api.proto:14; terraform-kong/templates/budgets"),
    ("repo:x-bill-payments", "razorpay/x-bill-payments — 404", False, "s2p", "kube-manifests/templates/x-bill-payments; proto/x_bill_payments"),
    ("repo:x-customer-payout-links", "razorpay/x-customer-payout-links (payee frontend) — 404", False, "payout-links", "kube-manifests/prod/x-customer-payout-links/values.yaml:6"),
    ("repo:abacus", "razorpay/abacus (ABAC / limits) — 404", False, "shared-infra", "goutils abac client abacus-grpc-int.razorpay.com; vendor-payments/config/default.toml:569"),
]
for rid, label, acc, dom, ev in repos:
    add(N(rid, "repository", label, accessible=acc, lifecycle="current", domain=dom, evidence=ev))

# --- Services ---
services = [
    ("svc:vendor-experience", "Vendor experience (vendor portal/onboarding; api + 2 Cadence workers)", True, "s2p", "kube-manifests/prod/vendor-experience/values.yaml:8-10,17-20; vendor-payments/config/default.toml:583"),
    ("svc:opfin", "Opfin / XPayroll core (PHP; payroll.razorpay.com; queues main/low/custom-reports/payouts-creation; crons ActualBankTransfer, BatchBankTransfer, ProcessMerchantPayoutsInNonCreatedState) — NOT ACCESSIBLE", False, "payroll", "kube-manifests/prod/opfin/values.yaml; terraform-kong prod payroll routes; api/config/applications.php:215-220"),
    ("svc:opfin-compliance", "Opfin compliance (PF/ESI/PT; Temporal) — NOT ACCESSIBLE", False, "payroll", "kube-manifests/prod/opfin-compliance/values.yaml:3,65-"),
    ("svc:x-payroll-compute", "x-payroll-compute (gRPC + Temporal + Kafka SDI consumer) — NOT ACCESSIBLE", False, "payroll", "kube-manifests/prod/x-payroll-compute/values.yaml; proto/x-payroll/x-payroll-compute"),
    ("svc:x-payroll-hris", "x-payroll-hris (gRPC) — NOT ACCESSIBLE", False, "payroll", "kube-manifests/prod/x-payroll-hris/values.yaml:1-2,14-15"),
    ("svc:x-salary-structure", "x-salary-structure (gRPC) — NOT ACCESSIBLE", False, "payroll", "kube-manifests/prod/x-salary-structure/values.yaml:2-3,15-16"),
    ("svc:x-payroll-flexible-benefits", "x-payroll-flexible-benefits (Kafka employee-settings-sync consumer) — NOT ACCESSIBLE", False, "payroll", "kube-manifests/prod/x-payroll-flexible-benefits/values.yaml"),
    ("svc:x-tna", "x-tna time & attendance (Temporal Cloud) — NOT ACCESSIBLE", False, "payroll", "kube-manifests/prod/x-tna/values.yaml:2-3,18-20,39-46"),
    ("svc:tax-compliance", "tax-compliance (TDS filing/payments; SQS payout-events consumer; Temporal; own Vault) — NOT ACCESSIBLE", False, "s2p", "kube-manifests/prod/tax-compliance/values.yaml (SQS prod-tax-compliance-payout-events); proto/tax_compliance"),
    ("svc:tax-compliance-vault", "tax-compliance-vault (HashiCorp Vault used by PAYOUTS, ns razorpayx) — NOT ACCESSIBLE", False, "s2p", "payouts/config/prod.toml:718-724 [hvault]; kube-manifests/prod/tax-compliance-vault/values.yaml:2,22-24"),
    ("svc:capital-cards", "capital-cards (corporate cards; prod-capital cluster) — NOT ACCESSIBLE", False, "capital", "kube-manifests/prod-capital/capital-cards; api/app/Services/CapitalCardsClient.php:21,76"),
    ("svc:capital-collections", "capital-collections — NOT ACCESSIBLE", False, "capital", "api/app/Services/CapitalCollectionsClient.php:73-125"),
    ("svc:capital-loc", "capital-loc / line-of-credit — NOT ACCESSIBLE", False, "capital", "api/app/Http/Route.php:1557-1562 LOCController@razorpayXWebhook"),
    ("svc:capital-es", "capital-es early/on-demand settlements (creates X payouts via public API; KeepSufficientBalanceInPayoutsMerchant cron) — NOT ACCESSIBLE", False, "settlements", "proto/capital/es/cron/v1/cron_api.proto:9; api/app/Services/RazorpayXClient.php:131-190; payouts/internal/auth/headers.go:24"),
    ("svc:master-onboarding", "master-onboarding (MOB; gRPC rzp.master_onboarding.intent.v1) — NOT ACCESSIBLE", False, "ca-onboarding", "kube-manifests/prod/master-onboarding/values.yaml:6,12; alert-rules/rules/prod-rules/master_onboarding_rules.yaml:6,13"),
    ("svc:bvs", "Business Verification Service (KYC; 12 workers) — NOT ACCESSIBLE", False, "ca-onboarding", "kube-manifests/prod/bvs/values.yaml; vendor-payments/config/default.toml:455"),
    ("svc:onboarding", "onboarding (obs) platform — NOT ACCESSIBLE", False, "ca-onboarding", "kube-manifests/prod/onboarding/values.yaml:6-7,10,30-42"),
    ("svc:accounts-receivable", "accounts-receivable (X Invoices; hosted invoice + payout webhook) — NOT ACCESSIBLE", False, "s2p", "terraform-kong/templates/backend-accounts-receivable/backend-accounts-receivable.tf:15-4x; payouts/internal/auth/headers.go:20"),
    ("svc:business-reporting", "business-reporting (ledger balance fetch; Kafka worker) — NOT ACCESSIBLE", False, "accounting", "api/config/applications.php:1188; payouts/internal/auth/headers.go:21"),
    ("svc:reporting", "reporting.razorpay.com (5 report ids used by accounting-integrations) — NOT ACCESSIBLE", False, "accounting", "accounting-integrations/config/prod.toml"),
    ("svc:abacus", "abacus (ABAC / limits; gRPC) — NOT ACCESSIBLE", False, "shared-infra", "vendor-payments/config/default.toml:569; workflows/config/prod.toml:185-221"),
    ("svc:metro", "metro (orchestration/bulk queue; metro-web.razorpay.com) — NOT ACCESSIBLE", False, "shared-infra", "vendor-payments/config/default.toml:211"),
    ("svc:ufh", "UFH unified file handler — NOT ACCESSIBLE", False, "shared-infra", "vendor-payments/config/default.toml:117"),
    ("svc:scrooge", "Scrooge refunds (PG) — NOT ACCESSIBLE", False, "pg", "payouts/internal/auth/headers.go:15; api/app/Models/Payout/SourceUpdater/Factory.php:57"),
    ("svc:cross-border-import", "Cross-border import (XBI; ICA transfers) — NOT ACCESSIBLE", False, "cross-border", "payouts/internal/auth/headers.go:23; api/app/Models/Payout/SourceUpdater/Factory.php:80"),
]
for sid, label, acc, dom, ev in services:
    add(N(sid, "service", label, accessible=acc, lifecycle="current", domain=dom, evidence=ev))

# --- Workers / deployments ---
for wid, label, ev in [
    ("worker:vendor-payments-kafka", "vendor-payments Kafka workers (tds, initiate-tds, ocr, bank-statement-fetch, accounting-payouts, auto-invoice, email-int, zoho-statement-sync, contact-updated…; 11 deployments)", "kube-manifests/prod/vendor-payments/values.yaml; vendor-payments/internal/tasks/*.go RegisterTask"),
    ("worker:vendor-experience-cadence", "vendor-experience Cadence workflow + activity workers", "kube-manifests/prod/vendor-experience/values.yaml:17-20"),
    ("worker:opfin-crons", "opfin CronJobs ActualBankTransfer / BatchBankTransfer / ProcessMerchantPayoutsInNonCreatedState", "kube-manifests/prod/opfin/values.yaml (run_cron jobs)"),
    ("worker:tax-compliance-sqs", "tax-compliance SQS worker on prod-tax-compliance-payout-events", "kube-manifests/prod/tax-compliance/values.yaml SQS_WORKER_Q*"),
]:
    add(N(wid, "worker", label, accessible=False, evidence=ev))

# --- Frontends ---
for fid, label, ev in [
    ("fe:x-vendor-payments", "X dashboard views VendorPayouts / TaxPayments / Accounting / Receivables / Budgets", "x/src/js/views/{VendorPayouts,TaxPayments,Accounting,Receivables,Budgets}"),
    ("fe:x-vendor-portal", "x-vendor-portal + x-invoice-approval (frontend-x lerna packages)", "frontend-x packages; spinacode/x-vendor-portal, x-invoice-approval"),
    ("fe:x-accounts-receivable", "x-accounts-receivable hosted-invoice frontend (invoices-x.razorpay.com)", "terraform-kong/templates/frontend-accounts-receivable/frontend-accounts-receivable.tf:16-51"),
    ("fe:payroll-web", "payroll.razorpay.com (Opfin web; X reaches it only by SSO redirect /sso?integraterx=1)", "x/config.js:302; x/src/js/Routes.js:1407; dashboard/environment/.env.production:71 OPFIN_REDIRECT_URL"),
    ("fe:x-capital", "X Capital views (CorporateCards, LineOfCredit, BNPL, Loans, CashAdvance) via federated bundles cdn.razorpay.com/capital/*", "x/src/js/views/Capital/*; x/src/js/bootstrap.ts:45-53"),
]:
    add(N(fid, "frontend", label, accessible=None, evidence=ev))

# --- Datastores ---
for did, label, ev, acc in [
    ("db:vendor-payments-mysql", "vendor-payments MySQL (88 migrations: invoices, purchase_orders, grns, tax_payments, tds_categories, vendor_advances…) + reads api_live", "vendor-payments/internal/migrations; findings/07 §2.4", True),
    ("db:vendor-payments-es", "vendor-payments Elasticsearch (elasticservice; prod-elastic-data-ingestion)", "vendor-payments/internal/elasticservice; config/default.toml:716", True),
    ("db:vendor-experience-db", "vendor-experience DB (Cadence-backed; schema in repo)", "vendor-experience/", True),
    ("db:accounting-integrations-db", "accounting-integrations DB (sync_status etc.)", "accounting-integrations/", True),
    ("db:opfin-pg", "prod-aurora-postgres-opfin-1a/1b, prod-aurora-postgres-xpayroll-1b", "alert-rules/rules/prod-rules/rds_generated_alerts_prod-aurora-postgres-opfin-*.yaml", False),
    ("db:tax-compliance-pg", "prod-aurora-postgres-tax-compliance-1a/1b", "alert-rules/rules/prod-rules/rds_generated_alerts_prod-aurora-postgres-tax-compliance-1*.yaml", False),
    ("db:xperience-pg", "prod-aurora-postgres-xperience-1a/1b (budgets, petty cash, cost centers, groups)", "alert-rules rds files; kube-manifests/prod/xperience/values.yaml", True),
    ("db:capital-es-mysql", "prod-aurora-mysql-capital-es-1a", "alert-rules rds file", False),
    ("db:bvs-pg", "prod-auroa-postgres-bvs-live-1b", "alert-rules rds file", False),
    ("db:monolith-x-tables", "monolith api_live X-adjacent tables: payout_sources, payouts_details(tax_payment_id, tds_category_id), corporate_cards, capital_transaction, credit_transfers, settlement_ondemand_*, banking_accounts, banking_account_statement, transactions", "api/app/Constants/Table.php; api/app/Models/PayoutSource/Entity.php:17-21; PayoutsDetails/Entity.php:16-17", True),
]:
    add(N(did, "datastore", label, accessible=acc, evidence=ev))

# --- Queues / topics ---
sns_topics = [
    ("q:sns-payout-updates-vendor-payments", "SNS payout-updates-vendor-payments (source_types vendor_payments, tax_payments, vendor_settlements, vendor_advance)", "payouts/config/prod.toml:375-378"),
    ("q:sns-payout-updates-xpayroll", "SNS payout-updates-xpayroll", "payouts/config/prod.toml:369"),
    ("q:sns-payout-updates-settlements", "SNS payout-updates-settlements", "payouts/config/prod.toml:371"),
    ("q:sns-payout-updates-payout-links", "SNS payout-updates-payout-links", "payouts/config/prod.toml:370"),
    ("q:sns-payout-updates-capital", "SNS topics for capital_cards / capital_line_of_credit / capital_collections", "payouts/config/prod.toml:368-388"),
    ("q:sns-payout-updates-cross-border", "SNS topic for cross_border / ica_transfer", "payouts/config/prod.toml:368-388"),
    ("q:sns-payout-updates-generic-accounting", "SNS payout-updates generic_accounting / refund / charge_collections / petty_cash", "payouts/config/prod.toml:368-388"),
]
for qid, label, ev in sns_topics:
    add(N(qid, "queue/topic", label, accessible=None, evidence=ev))
for qid, label, ev in [
    ("q:kafka-add-tds-entry", "Kafka add-tds-entry (consumer: vendor-payments initiate-tds; producer NOT FOUND in clone root)", "vendor-payments/config/prod.toml:336; internal/tasks/initiatetds.go:21,44; api/app/Models/Payout/TdsProcessor/Processor.php:31-86 (monolith side)"),
    ("q:kafka-vp-accounting-payouts-status", "Kafka prod.x.vendor-payments.accounting-payouts.status-update (producer vendor-payments; consumer accounting-integrations)", "vendor-payments/config/prod.toml:272-273; accounting-integrations/config/prod.toml:196,204"),
    ("q:kafka-vp-internal", "vendor-payments internal Kafka families: prod-tds, prod-email-int, prod-auto-invoice-processing, prod-po-status-update, prod-grn-status-update, prod-vp-status-update, prod-fetch-gstr-2a/2b, prod-vp-gstr-2b-recon, prod-ocr, prod-fund-account-verification, prod-elastic-data-ingestion, prod-vendor-advance-event, prod-items-update-event, prod-vendor-entity-updates-event", "vendor-payments/config/prod.toml; internal/tasks/*.go"),
    ("q:sqs-tax-compliance-payout-events", "SQS prod-tax-compliance-payout-events (consumer tax-compliance; producer UNKNOWN — likely payouts/monolith)", "kube-manifests/prod/tax-compliance/values.yaml"),
    ("q:kafka-employee-settings-sync", "Kafka employee-settings-sync (x-payroll-flexible-benefits consumer)", "kube-manifests/prod/x-payroll-flexible-benefits/values.yaml"),
    ("q:sqs-x-balances-payout-event", "SQS x-balances-payout-event / x-balances-account-activation-event", "kube-manifests cell x-balances values"),
]:
    add(N(qid, "queue/topic", label, accessible=None, evidence=ev))

# --- API endpoints ---
for eid, label, ev in [
    ("ep:monolith-internalContactPayout", "POST /v1/internalContactPayout (monolith; internal apps vendor_payments/xpayroll/charge_collections/capital_collections)", "api/app/Http/Route.php (payout_create_on_internal_contact); vendor-payments/internal/payout/core.go:84,138"),
    ("ep:monolith-payouts-internal-tax-payment-id", "PATCH /v1/payouts_internal/{id}/tax-payment-id", "api/app/Http/Route.php:2339; vendor-payments/internal/taxpayments/apicaller.go:21"),
    ("ep:monolith-tax-payments", "monolith /v1/tax-payments/* (40 routes; Twirp proxy to vendor-payments razorpay.vendorpayments.taxpayments)", "api/app/Http/Route.php:2646-2684; api/app/Services/TaxPayments/Service.php:14-45"),
    ("ep:monolith-vendor-payments-proxy", "monolith /v1/vendor-payments/* (~150 routes proxied to vendor-payments; Kong also routes api.razorpay.com/v1/vendor-payments/** direct)", "api/app/Http/Route.php; terraform-kong prod vendor-payments"),
    ("ep:vp-twirp", "vendor-payments Twirp `vendorpayments` (131 rpcs) + gRPC rzp.vendor_payments.*", "proto/vendor-payments/service.proto:15; rpc/buf.gen.twirp.yaml"),
    ("ep:vp-taxpayments-twirp", "vendor-payments Twirp razorpay.vendorpayments.taxpayments (34 rpcs: PayTaxPayment, InitiateMonthlyPayouts, CancelQueuedPayoutCron, AddPenaltyCron, InternalIciciAction…)", "proto/tax-payments/service.proto:12; vendor-payments/internal/apiservice/TaxPaymentServer.go:123-185"),
    ("ep:vp-workflow-callback", "vendor-payments CallbackApi.WorkflowStateCallback (from workflows)", "proto/vendor-payments/callback/v1/service.proto:9; workflows/config/prod.toml:173-175"),
    ("ep:opfin-merchant-payout-status", "POST payroll.razorpay.com/v2/api/merchant-payout-status (payout status push to Opfin)", "api/app/Services/XPayroll/Service.php:26,54-90; terraform-kong razorpayx_rx_integrated_webhoook_routes-xpayroll"),
    ("ep:opfin-validate-source-account", "POST /v2/api/validate-source-account (payroll TPV of X source account)", "api/app/Services/XPayroll/Service.php:28,216-264"),
    ("ep:monolith-xpayroll-allowlist", "monolith routes allowed to app.xpayroll: payout_create_internal, payout_create_on_internal_contact, payout_create_2FA_internal, contact_create_internal, fund_account_create_internal, banking_accounts_list_internal, tax_payments_internal_icici_action, user_details_for_payroll, bulk_payout_purpose_post, user_details, user_all_roles", "api/app/Http/Route.php:18626-18638; api/config/applications_v2.php:741-761"),
    ("ep:settlements-StatusUpdatePayout", "Twirp rzp.settlements.transfer.v1.TransferService/StatusUpdatePayout", "api/app/Services/Settlements/Payout.php:14,35; proto/settlements/transfer/v1/transfer_api.proto:116"),
    ("ep:capital-es-KeepSufficientBalance", "capital-es CronAPI.KeepSufficientBalanceInPayoutsMerchant / DetectStuckOndemandSettlements", "proto/capital/es/cron/v1/cron_api.proto:9"),
    ("ep:monolith-loc-withdrawal-update", "POST /v1/loc/withdrawal/update (LOCController@razorpayXWebhook forwards X payout callback)", "api/app/Http/Route.php:1557-1562"),
    ("ep:monolith-onboardCCCForBanking", "POST /v1/merchant/onboardCCCForBanking → balance account_type=corp_card channel=m2p", "api/app/Http/Route.php:5248; api/app/Models/CorpCard/Core.php:50-62"),
    ("ep:capital-cards-account-details", "capital-cards GET v1/vendorpayment/account_details", "api/app/Services/CapitalCardsClient.php:21,76"),
    ("ep:accounts-receivable-payout-webhook", "accounts-receivable POST /v1/invoices/payout/handleWebhook", "terraform-kong/templates/backend-accounts-receivable/backend-accounts-receivable.tf"),
    ("ep:xperience-corporate-cards", "xperience /v1/xperience-edge/corporate-cards/{page-view,spends-summary} (CorporateCardsAPI)", "proto/xperience/corporatecards/v1/corporatecards_api.proto:18; terraform-kong xperience routes"),
    ("ep:kong-partner-lms", "Kong /v1/partner_lms/** → banking-account.razorpay.com (CA onboarding)", "terraform-kong/templates/banking-accounts/*.tf"),
    ("ep:mob-intent-api", "master-onboarding gRPC rzp.master_onboarding.intent.v1.IntentAPI", "alert-rules/rules/prod-rules/master_onboarding_rules.yaml:72-94"),
    ("ep:icici-direct-tax", "ICICI apibankingone.icicibank.com /api/v1/directTaxTin2/{paymentProcess,verification,debitAdvice} (bypasses FTS)", "vendor-payments/config/default.toml:325-343 [IciciConfig]"),
]:
    add(N(eid, "API endpoint", label, accessible=None, evidence=ev))

# --- Events (stork families) ---
for evid, label, ev in [
    ("event:transaction.created", "stork transaction.created / transaction.updated (producer api monolith)", "stork/internal/webhook/event_constants.go:132-133; api/app/Models/Transaction/Core.php"),
    ("event:settlement.processed", "stork settlement.processed (producer api monolith)", "stork/internal/webhook/event_constants.go:42"),
    ("event:banking_accounts.issued", "stork banking_accounts.issued", "stork/internal/webhook/event_constants.go"),
    ("event:payout_link.*", "stork payout_link.* (8 events; producer payout-links)", "stork/internal/webhook/event_constants.go"),
    ("event:bill_payment.*", "stork bill_payment.* (4 events; feature enable_bbps; producer UNKNOWN)", "stork/internal/webhook/event_constants.go:127-130"),
]:
    add(N(evid, "event", label, accessible=None, evidence=ev))

# --- Feature flags ---
for fid, label, ev in [
    ("flag:skip_wf_for_payroll", "skip_wf_for_payroll (DCS skip_workflow_for_payroll): maker-checker bypass when caller app == xpayroll", "payouts/internal/app/payouts/processor/baseHelper.go:185-191; pkg/dcs/features/features.go:47; api/app/Models/Payout/Processor/Base.php:2614-2619"),
    ("flag:hide_rx_payroll_payouts", "hide_rx_payroll_payouts: dashboard excludes source_type=xpayroll", "payouts/internal/app/payouts/core.go:9594-9596; config-proto/rzp/x/merchant/payouts/payroll_payouts.proto:9-15"),
    ("flag:enable_payouts_to_cards", "enable_payouts_to_cards (config-proto owned by payouts)", "config-proto/rzp/x/merchant/payouts/cards.proto:6-20"),
    ("flag:pricing_tiers", "banking_plus_core/pro, source_to_pay_core/pro (X pricing tiers; paywall)", "config-proto/rzp/x/merchant/onboarding/pricing_tiers.proto:12-27"),
    ("flag:uiconfig-xproducts", "X product catalogue (20 products: insights, vendor_payments, tax_payments, cash_advance, receivables, petty_cash, accounting, line_of_credit, payout_links, cost_centers, payroll, finance_x, …)", "config-proto/rzp/x/country/dashboard/uiconfig.proto:17-55"),
    ("flag:capital_cards", "capital_cards / capital_cards_eligible / capital_cards_collections / cash_on_card", "api/app/Models/Feature/Constants.php:344,709-759"),
]:
    add(N(fid, "feature flag", label, accessible=None, evidence=ev))

# --- Identities / roles ---
for iid, type_, label, ev in [
    ("id:app-vendor_payments", "identity", "internal app vendor_payments (basic auth; monolith + Payouts allow-list; contact type rzp_tax_pay)", "payouts/internal/auth/headers.go:13; payouts/internal/app/contact/type.go:17; authz internal.csv:8"),
    ("id:app-xpayroll", "identity", "internal app xpayroll (basic auth; contact type rzp_xpayroll; skip-wf feature)", "payouts/internal/auth/headers.go:12; payouts/internal/app/contact/type.go:20; api/config/applications_v2.php:741-761"),
    ("id:app-settlements_service", "identity", "internal app settlements_service (posts /v1/payouts_internal)", "payouts/internal/auth/headers.go:16; authz internal.csv:8; payouts/config/prod.toml:104-106"),
    ("id:app-capital", "identity", "internal apps capital_collections_client / capital_cards_client / loc / capital_bnpl / capital_early_settlements", "payouts/internal/auth/headers.go:17,24; authz internal.csv"),
    ("id:app-accounts_receivable", "identity", "internal app accounts_receivable", "payouts/internal/auth/headers.go:20"),
    ("id:app-business_reporting", "identity", "internal app business_reporting", "payouts/internal/auth/headers.go:21"),
    ("id:app-cross_border_import", "identity", "internal app cross_border_import", "payouts/internal/auth/headers.go:23"),
    ("id:ledger-clients", "identity", "ledger 36 client keys (X tenant: payouts, x_balances, + PG: settlements, scrooge, capital_collections, capital_es, bill_payments, offers_engine…)", "ledger/config/prod-live.toml:174-281"),
    ("id:workflow-callers", "identity", "workflows 12 callers (api, payouts, relay, growth, payout_links, splitz, xperience, abacus, vendor_experience, offers_engine, payments_bank_transfer, vendor_payments)", "workflows/config/prod.toml:185-221"),
    ("id:vendor", "identity", "Vendor (vendor-experience vendor user / portal OTP; BVS-verified)", "vendor-experience/; authz roles vendor"),
    ("id:employee", "identity", "Employee (payroll employee_id; petty_cash_employee role in X)", "api/app/Models/User/BankingRole.php:10-35; proto/x-payroll"),
    ("id:business_id", "identity", "business_id (banking-accounts CA onboarding: businesses/people/signatories/partner_bank_applications)", "banking-accounts migrations; findings/07 §2.2"),
    ("id:cardholder", "identity", "cardholder_id / card_id / program_id (capital-cards protos)", "proto/capital/cards/*"),
    ("role:vendor-payments-roles", "role", "~43 vendor-payments/PO/GRN/ITC permission-roles; tax/TDS roles PAY_TAX_PAYMENTS, VIEW/UPDATE_TAX_PAYMENT_SETTINGS(_AUTO), TAX_PAYMENT_ADMIN_AUTH_EXECUTE", "authz/scripts/policies/**/xplatform*.csv; api/app/Http/Route.php:11615,12401-12405"),
    ("role:cards-rbac", "role", "cards RBAC card:*/repayment:*/program:limit (capital resource group) for owner/admin/finance/finance_l1-l3/operations/view_only", "authz/scripts/policies/**/20221212154546_cards_RBAC.csv; capital.csv:4-99"),
    ("role:banking-roles", "role", "BankingRole set incl. maker/checker_l1-3, cc_admin, chartered_accountant, vendor, petty_cash_employee, banking_readonly", "api/app/Models/User/BankingRole.php:10-35"),
]:
    add(N(iid, type_, label, accessible=None, evidence=ev))

# --- External systems ---
for xid, label, ev in [
    ("ext:icici-direct-tax", "ICICI direct-tax challan APIs (apibankingone.icicibank.com)", "vendor-payments/config/default.toml:325-343"),
    ("ext:mastersindia", "MastersIndia GST API", "vendor-payments/config/default.toml:745"),
    ("ext:veryfi", "Veryfi OCR", "vendor-payments/config/default.toml:598"),
    ("ext:zoho-tally", "Zoho Books / Tally / QuickBooks (accounting tools)", "accounting-integrations/internal/service/accountingtool/{tally,zoho}"),
    ("ext:temporal-cloud", "Temporal Cloud (x-payroll-compliance, x-tna, x-payroll-compute)", "kube-manifests/prod/opfin-compliance/values.yaml; prod/x-tna/values.yaml:39-46"),
    ("ext:m2p", "M2P card issuer rail (fts channel m2p; mozart capital_m2p)", "mozart/app/config/capital/capital_m2p/v1/base.json; api/app/Models/CorpCard/Core.php:50-62"),
]:
    add(N(xid, "external bank/provider", label, accessible=None, evidence=ev))

# --- Business journeys ---
for jid, label, ev in [
    ("journey:vp-invoice-to-payout", "Vendor invoice → approval workflow → payout via payouts_internal → status back via SNS → accounting sync", "vendor-payments/internal/payout/core.go:83,109; workflows/internal/constants/constants.go:36-40; payouts/config/prod.toml:375"),
    ("journey:tds-monthly-remittance", "Monthly TDS remittance: add-tds-entry → InitiateMonthlyPayouts → internalContactPayout (rzp_tax_pay) → tag-back tax-payment-id", "vendor-payments/internal/taxpayments/core.go:1027,1929,2780; apicaller.go:21"),
    ("journey:po-grn-approval", "Purchase order / GRN approval (workflow types purchase-order-approval, goods-received-note-approval)", "workflows/internal/constants/constants.go:36-40"),
    ("journey:vendor-onboarding", "Vendor invite → portal (vendor-experience) → BVS verification → fund account", "vendor-experience/; vendor-payments/config/default.toml:455,583"),
    ("journey:payroll-salary-disbursement", "Opfin cron ActualBankTransfer/BatchBankTransfer → payout_create_internal (app xpayroll, skip_wf_for_payroll) → SNS xpayroll → merchant-payout-status", "kube-manifests/prod/opfin/values.yaml; payouts/internal/app/payouts/processor/baseHelper.go:185-191; api/app/Services/XPayroll/Service.php:26"),
    ("journey:ondemand-settlement-to-x", "PG on-demand settlement → capital-es → X public API payout (internal X merchant) → status via SNS settlements / StatusUpdatePayout", "api/app/Services/RazorpayXClient.php:131-190; proto/capital/es/cron/v1/cron_api.proto:9"),
    ("journey:loc-withdrawal", "Capital LOC withdrawal → X payout → callback /loc/withdrawal/update", "api/app/Http/Route.php:1557-1562"),
    ("journey:corp-card-balance", "Onboard Capital corporate card as X balance (account_type corp_card, channel m2p) → spends via xperience corporate-cards", "api/app/Http/Route.php:5248; api/app/Models/CorpCard/Core.php:50-62"),
    ("journey:ca-onboarding", "CA application → master-onboarding intent → BVS KYC → banking-accounts activation → balance + ledger account", "agent-skills/teams/banking/skills/ca-onboarding-oncall-debugger/SKILL.md:41-88; api/app/Models/Merchant/Balance/Ledger/Core.php:133-160"),
]:
    add(N(jid, "business journey", label, accessible=None, evidence=ev))

# ----------------------------------------------------------------------------------------------
# NEW EDGES
# ----------------------------------------------------------------------------------------------
edges = []
e = edges.append
# --- S2P: vendor-payments / tax ---
e(E("svc:vendor-payments", "ep:monolith-payouts-internal", "calls", "HTTP basic-auth internal app vendor_payments; POST v1/payouts_internal/ (host api-graphql.razorpay.com)", "vendor-payments", "internal/payout/core.go:83,109; config/prod.toml:123", "CreatePayoutPath / rxclient.MakeCall"))
e(E("svc:vendor-payments", "ep:monolith-internalContactPayout", "calls", "HTTP POST v1/internalContactPayout/ for TDS remittance (contact type rzp_tax_pay)", "vendor-payments", "internal/payout/core.go:84,138; internal/taxpayments/core.go:2780", "CreatePayoutOnInternalContact"))
e(E("svc:vendor-payments", "ep:monolith-payouts-internal-tax-payment-id", "writes", "PATCH v1/payouts_internal/{id}/tax-payment-id", "vendor-payments", "internal/taxpayments/apicaller.go:21", "UpdateTaxPaymentIdOnPayout"))
e(E("ep:monolith-internalContactPayout", "svc:payouts", "routes_to", "monolith forwards internal-contact payouts to Payouts Service; PS gate ValidateInternalAppAllowedToCreatePayoutsOnType", "payouts", "internal/app/contact/type.go:17,35-48", "internalAppToAllowedInternalContact[vendor_payments]=rzp_tax_pay", confidence="probable"))
e(E("svc:payouts", "id:app-vendor_payments", "authorizes", "allowedInternalApps", "payouts", "internal/auth/headers.go:13", "appConstants.VENDOR_PAYMENTS"))
e(E("svc:payouts", "q:sns-payout-updates-vendor-payments", "publishes", "SNS source updater; 4 source types share one topic", "payouts", "config/prod.toml:375-378; pkg/sourceupdater/event.go:89; pkg/sns/sns.go:83-87", "[source_update_topics]"))
e(E("q:sns-payout-updates-vendor-payments", "svc:api-monolith", "delivers_to", "SourceUpdater Factory → VendorPaymentUpdater", "api", "app/Models/Payout/SourceUpdater/Factory.php:29-36; VendorPaymentUpdater.php:14-16", "VendorPaymentUpdater"))
e(E("svc:api-monolith", "svc:vendor-payments", "calls", "PayoutStatusChange callback to vendor-payments", "api", "app/Services/VendorPayments/Service.php:32,480-503", "PayoutStatusChange"))
e(E("svc:api-monolith", "ep:vp-taxpayments-twirp", "calls", "Twirp proxy razorpay.vendorpayments.taxpayments; 40 tax-payments/* routes", "api", "app/Services/TaxPayments/Service.php:14-45,577-620; app/Http/Route.php:2646-2684", "TaxPayments\\Service"))
e(E("svc:vendor-payments", "svc:workflow", "calls", "workflow create for vendor-payment-v2-approval, purchase-order-approval, goods-received-note-approval, vendor-onboarding-approval", "vendor-payments", "config/default.toml:536; workflows/internal/constants/constants.go:36-40", "workflows client"))
e(E("svc:workflow", "ep:vp-workflow-callback", "calls", "WorkflowStateCallback to vendor-payments.razorpay.com", "workflows", "config/prod.toml:173-175; proto/vendor-payments/callback/v1/service.proto:9", "CallbackApi.WorkflowStateCallback"))
e(E("svc:vendor-payments", "q:kafka-vp-accounting-payouts-status", "publishes", "Kafka accounting-payouts status update", "vendor-payments", "config/prod.toml:272-273", "VendorPaymentSourceUpdaterConfig.AIQueueName"))
e(E("q:kafka-vp-accounting-payouts-status", "svc:accounting-integrations", "delivers_to", "Kafka consumer", "accounting-integrations", "config/prod.toml:196,204", "consumer config"))
e(E("q:kafka-add-tds-entry", "svc:vendor-payments", "delivers_to", "Kafka consumer initiate-tds; producer unknown (monolith TdsProcessor is the likely producer)", "vendor-payments", "internal/tasks/initiatetds.go:21,44; api/app/Models/Payout/TdsProcessor/Processor.php:31-86", "HandleInitiateTdsJob", confidence="probable"))
e(E("svc:vendor-payments", "ext:icici-direct-tax", "calls", "direct ICICI challan rail (bypasses FTS)", "vendor-payments", "config/default.toml:325-343", "[IciciConfig]"))
e(E("svc:vendor-payments", "svc:vendor-experience", "calls", "vendor portal / onboarding", "vendor-payments", "config/default.toml:583", "vendor-experience host"))
e(E("svc:vendor-payments", "svc:bvs", "calls", "vendor verification via BVS SDK", "vendor-payments", "config/default.toml:455; go.mod business-verification-service-sdk-go", "bvs client"))
e(E("svc:vendor-payments", "svc:abacus", "calls", "limits/ABAC", "vendor-payments", "config/default.toml:569", "abacus host"))
e(E("svc:vendor-payments", "svc:metro", "calls", "orchestration/bulk", "vendor-payments", "config/default.toml:211", "metro host"))
e(E("svc:vendor-payments", "svc:ufh", "calls", "file handling (challans)", "vendor-payments", "config/default.toml:117", "ufh host"))
e(E("svc:vendor-payments", "svc:stork", "calls", "TDS alert mails", "vendor-payments", "config/default.toml:514; internal/taxpayments/core.go:1313-1354", "stork client"))
e(E("svc:vendor-payments", "ext:mastersindia", "calls", "GST data", "vendor-payments", "config/default.toml:745", "MastersIndia"))
e(E("svc:vendor-payments", "ext:veryfi", "calls", "invoice OCR", "vendor-payments", "config/default.toml:598", "Veryfi"))
e(E("svc:capital-cards", "svc:vendor-payments", "calls", "internal basic-auth caller capitalCards (pay vendor via corporate card)", "vendor-payments", "internal/boot/helpers.go:244-249", "setupInternalAuthBasicCredentials"))
e(E("svc:business-reporting", "svc:vendor-payments", "calls", "internal basic-auth caller businessReporting", "vendor-payments", "internal/boot/helpers.go:187-269", "setupInternalAuthBasicCredentials"))
e(E("svc:payouts", "svc:tax-compliance-vault", "reads", "HashiCorp Vault tax-compliance-vault.razorpay.com ns razorpayx (secrets)", "payouts", "config/prod.toml:718-724", "[hvault]"))
e(E("q:sqs-tax-compliance-payout-events", "svc:tax-compliance", "delivers_to", "SQS worker; producer unknown", "kube-manifests", "prod/tax-compliance/values.yaml", "SQS_WORKER_Q*", confidence="unknown"))
e(E("svc:vendor-payments", "db:vendor-payments-mysql", "writes", "gorm", "vendor-payments", "internal/migrations", "migrations"))
e(E("svc:vendor-payments", "db:api-mysql", "reads", "reads monolith api_live directly", "vendor-payments", "config/prod.toml", "[api_live] db config", confidence="probable"))
e(E("svc:accounts-receivable", "svc:payouts", "calls", "internal app accounts_receivable in Payouts allow-list; MetroAPI.InitiatePayout", "payouts", "internal/auth/headers.go:20; proto/accounts_receivable/metro_api.proto:12", "ACCOUNTS_RECEIVABLE"))
e(E("svc:payouts", "ep:accounts-receivable-payout-webhook", "calls", "payout status webhook to accounts-receivable", "terraform-kong", "templates/backend-accounts-receivable/backend-accounts-receivable.tf", "/v1/invoices/payout/handleWebhook", confidence="probable"))
e(E("fe:x-vendor-payments", "ep:monolith-vendor-payments-proxy", "calls", "browser → api-dashboard-merchant / api.razorpay.com /v1/vendor-payments/** (Kong → vendor-payments)", "x", "src/js/views/VendorPayouts; terraform-kong prod vendor-payments", "VendorPayouts views"))
e(E("fe:x-vendor-payments", "ep:monolith-tax-payments", "calls", "browser → /v1/tax-payments/*", "x", "src/js/views/TaxPayments", "TaxPayments views"))
e(E("svc:accounting-integrations", "svc:reporting", "calls", "reporting.razorpay.com (5 report ids)", "accounting-integrations", "config/prod.toml", "reporting client"))
e(E("svc:accounting-integrations", "ext:zoho-tally", "calls", "Zoho / Tally sync", "accounting-integrations", "internal/service/accountingtool/{tally,zoho}", "accountingtool"))
e(E("svc:payouts", "id:app-business_reporting", "authorizes", "allowedInternalApps", "payouts", "internal/auth/headers.go:21", "BUSINESS_REPORTING"))

# --- Payroll ---
e(E("svc:opfin", "ep:monolith-xpayroll-allowlist", "calls", "basic-auth internal app xpayroll (secret OPFIN_SERVICE_SECRET); 11 allowed routes incl. payout_create_internal", "api", "app/Http/Route.php:18626-18638; config/applications_v2.php:741-761", "app.xpayroll"))
e(E("svc:payouts", "id:app-xpayroll", "authorizes", "allowedInternalApps + contact type rzp_xpayroll", "payouts", "internal/auth/headers.go:12; internal/app/contact/type.go:20", "X_PAYROLL"))
e(E("flag:skip_wf_for_payroll", "svc:payouts", "controlled_by", "maker-checker bypass when app==xpayroll and feature enabled", "payouts", "internal/app/payouts/processor/baseHelper.go:185-191", "SKIP_WF_FOR_PAYROLL"))
e(E("svc:payouts", "q:sns-payout-updates-xpayroll", "publishes", "SNS", "payouts", "config/prod.toml:369", "xpayroll topic"))
e(E("q:sns-payout-updates-xpayroll", "svc:api-monolith", "delivers_to", "XPayrollUpdater", "api", "app/Models/Payout/SourceUpdater/XPayrollUpdater.php:15-17", "XPayrollUpdater"))
e(E("svc:api-monolith", "ep:opfin-merchant-payout-status", "calls", "POST {OPFIN_SERVICE_URL}/v2/api/merchant-payout-status", "api", "app/Services/XPayroll/Service.php:26,54-90", "pushPayoutStatusUpdate"))
e(E("svc:api-monolith", "ep:opfin-validate-source-account", "calls", "payroll TPV of X source account", "api", "app/Services/XPayroll/Service.php:28,216-264", "validateSourceAccount"))
e(E("svc:fts", "svc:opfin", "configures", "[payroll_mid_details] MERCHANT_LIST dedicated payroll MID handling", "fts", "config/env.default.toml:6924-6925", "payroll_mid_details", confidence="probable"))
e(E("fe:x", "fe:payroll-web", "routes_to", "SSO redirect /sso?integraterx=1 (no embedded module)", "x", "config.js:302; src/js/Routes.js:1407", "payroll redirect"))
e(E("svc:opfin", "worker:opfin-crons", "implements", "", "kube-manifests", "prod/opfin/values.yaml", "run_cron ActualBankTransfer/BatchBankTransfer/ProcessMerchantPayoutsInNonCreatedState"))
e(E("svc:opfin", "db:opfin-pg", "writes", "Aurora Postgres", "alert-rules", "rules/prod-rules/rds_generated_alerts_prod-aurora-postgres-opfin-1a.yaml", "rds alerts", confidence="probable"))
e(E("svc:opfin", "svc:x-payroll-compute", "calls", "gRPC x-payroll-compute-grpc-concierge (payroll compute)", "kube-manifests", "prod/x-payroll-compute/values.yaml", "grpc host", confidence="inferred"))
e(E("svc:opfin", "svc:tax-compliance", "calls", "TDS filing/payments (tax_compliance protos owned by payroll team)", "proto", "tax_compliance/compliance/tds_payments/v1/tds_payments.proto:5,11", "TDSPaymentsService", confidence="inferred"))
e(E("svc:opfin", "ext:temporal-cloud", "uses", "opfin-compliance / x-tna Temporal Cloud", "kube-manifests", "prod/opfin-compliance/values.yaml; prod/x-tna/values.yaml:39-46", "TEMPORAL_*", confidence="probable"))

# --- Settlements / capital-es ---
e(E("svc:capital-es", "ep:POST /v1/payouts", "calls", "on-demand settlement creates X payout via public API using internal X merchant (RazorpayXClient)", "api", "app/Services/RazorpayXClient.php:131-190", "RazorpayXClient", confidence="probable"))
e(E("id:app-settlements_service", "ep:monolith-payouts-internal", "calls", "settlements_service posts /v1/payouts_internal", "authz", "scripts/policies/**/internal.csv:8", "app.settlements_service"))
e(E("svc:payouts", "id:app-settlements_service", "authorizes", "allowedInternalApps; [auth.settlements] creds; raised limits for settlements merchants", "payouts", "internal/auth/headers.go:16; config/prod.toml:104-106,826", "SETTLEMENTS_SERVICE"))
e(E("svc:payouts", "q:sns-payout-updates-settlements", "publishes", "SNS", "payouts", "config/prod.toml:371", "settlements topic"))
e(E("q:sns-payout-updates-settlements", "svc:api-monolith", "delivers_to", "SettlementsUpdater", "api", "app/Models/Payout/SourceUpdater/Factory.php:45", "SettlementsUpdater"))
e(E("svc:api-monolith", "ep:settlements-StatusUpdatePayout", "calls", "Twirp StatusUpdatePayout", "api", "app/Services/Settlements/Payout.php:14,35", "TransferService/StatusUpdatePayout"))
e(E("ep:settlements-StatusUpdatePayout", "svc:settlements", "implements", "", "proto", "settlements/transfer/v1/transfer_api.proto:116", "StatusUpdatePayout"))
e(E("svc:settlements", "ep:POST /v1/transfer", "calls", "FTS product SETTLEMENT; status webhook to settlements-live.razorpay.com/twirp/.../TransferService/StatusUpdate", "fts", "config/env.prod-live.toml:171-223; internal/product/product.go:15-25", "SETTLEMENT product"))
e(E("svc:capital-es", "ep:capital-es-KeepSufficientBalance", "exposes", "cron keeps payouts merchant balance topped up", "proto", "capital/es/cron/v1/cron_api.proto:9", "KeepSufficientBalanceInPayoutsMerchant"))
e(E("svc:payouts", "id:app-capital", "authorizes", "capital_collections_client + capital_early_settlements in allow-list", "payouts", "internal/auth/headers.go:17,24", "CAPITAL_*"))
e(E("svc:settlements", "svc:ledger", "calls", "ledger client settlements_key; outbox_jobs_settlements (PG tenant)", "ledger", "config/prod-live.toml:174-281", "settlements_key"))

# --- Capital / cards ---
e(E("svc:payouts", "q:sns-payout-updates-capital", "publishes", "SNS capital_cards / capital_line_of_credit / capital_collections", "payouts", "config/prod.toml:368-388", "capital topics"))
e(E("q:sns-payout-updates-capital", "svc:api-monolith", "delivers_to", "CapitalCollectionsUpdater", "api", "app/Models/Payout/SourceUpdater/Factory.php:69", "CapitalCollectionsUpdater"))
e(E("svc:api-monolith", "svc:capital-collections", "calls", "pushPayoutStatusUpdate; settlement-ondemand ledger updates", "api", "app/Services/CapitalCollectionsClient.php:73-125", "CapitalCollectionsClient"))
e(E("svc:api-monolith", "ep:capital-cards-account-details", "calls", "corp card account details for X balance", "api", "app/Services/CapitalCardsClient.php:21,76", "CapitalCardsClient"))
e(E("ep:monolith-onboardCCCForBanking", "db:api-mysql", "writes", "balance row account_type=corp_card channel=m2p type=banking", "api", "app/Models/CorpCard/Core.php:50-62; Balance/Core.php:273,314-321", "onboardCapitalCorpCardForRzpX"))
e(E("svc:capital-loc", "ep:monolith-loc-withdrawal-update", "calls", "LOC withdrawal disbursed as X payout; callback forwarded", "api", "app/Http/Route.php:1557-1562", "LOCController@razorpayXWebhook", confidence="probable"))
e(E("svc:x-balances", "svc:capital-cards", "calls", "creating X sub-balance mutates capital-cards eligibility", "x-balances", "pkg/log/trace.go:44-45", "capital cards eligibility", confidence="probable"))
e(E("svc:cfa", "svc:vault", "calls", "bin-service + tokens-live-int for card fund accounts", "cfa", "config/prod.toml:67-86", "bin-service / token service"))
e(E("flag:enable_payouts_to_cards", "svc:payouts", "controlled_by", "payouts-owned config-proto", "config-proto", "rzp/x/merchant/payouts/cards.proto:6-20", "enable_payouts_to_cards"))
e(E("svc:xperience", "ep:xperience-corporate-cards", "exposes", "CorporateCardsAPI page-view/spends-summary/UpsertCardEntitlement/IngestFeedFile", "proto", "xperience/corporatecards/v1/corporatecards_api.proto:18", "CorporateCardsAPI"))
e(E("svc:fts", "ext:m2p", "calls", "FTS channel m2p via mozart capital_m2p", "mozart", "app/config/capital/capital_m2p/v1/base.json", "capital_m2p", confidence="probable"))
e(E("fe:x-capital", "svc:capital-loc", "calls", "rzp.capital.loc.account.v1.AccountAPI via x/src/js/api/capital.js", "x", "src/js/api/capital.js:33", "AccountAPI"))

# --- CA onboarding ---
e(E("svc:banking-accounts", "svc:master-onboarding", "calls", "mob host", "banking-accounts", "config/default.toml:103-106", "master-onboarding client"))
e(E("svc:banking-accounts", "svc:bvs", "calls", "pkg/bvs", "banking-accounts", "pkg/bvs", "bvs client"))
e(E("ep:kong-partner-lms", "svc:banking-accounts", "routes_to", "Kong /v1/partner_lms/** → banking-account.razorpay.com", "terraform-kong", "templates/banking-accounts/banking-accounts.tf", "partner_lms routes"))
e(E("svc:master-onboarding", "ep:mob-intent-api", "exposes", "gRPC IntentAPI", "alert-rules", "rules/prod-rules/master_onboarding_rules.yaml:72-94", "rzp.master_onboarding.intent.v1.IntentAPI"))
e(E("svc:api-monolith", "svc:master-onboarding", "calls", "APP_MASTER_ONBOARDING_SERVICE_URL", "api", "config/applications.php:1974", "master_onboarding client"))

# --- Cross-border / refunds ---
e(E("svc:payouts", "id:app-cross_border_import", "authorizes", "allowedInternalApps; raised payout limit", "payouts", "internal/auth/headers.go:23; internal/app/payouts/validation.go:239-247", "CROSS_BORDER_IMPORT"))
e(E("svc:payouts", "q:sns-payout-updates-cross-border", "publishes", "cross_border / ica_transfer source types", "payouts", "config/prod.toml:368-388", "cross_border topics"))
e(E("q:sns-payout-updates-cross-border", "svc:cross-border-import", "delivers_to", "CrossBorderICATransferUpdater", "api", "app/Models/Payout/SourceUpdater/Factory.php:80", "CrossBorderICATransferUpdater", confidence="probable"))
e(E("svc:scrooge", "svc:payouts", "calls", "refund source type; internal app scrooge", "payouts", "internal/auth/headers.go:15; api/app/Models/Payout/SourceUpdater/Factory.php:57", "SCROOGE"))

# --- Shared platforms with many external callers ---
e(E("id:ledger-clients", "svc:ledger", "calls", "36 client keys across X and PG tenants", "ledger", "config/prod-live.toml:174-281", "auth clients"))
e(E("id:workflow-callers", "svc:workflow", "calls", "12 configured callers", "workflows", "config/prod.toml:185-221", "clients"))
e(E("svc:stork", "event:transaction.created", "publishes", "producer api monolith", "stork", "internal/webhook/event_constants.go:132-133", "transaction.created"))
e(E("svc:stork", "event:settlement.processed", "publishes", "producer api monolith", "stork", "internal/webhook/event_constants.go:42", "settlement.processed"))
e(E("svc:stork", "event:payout_link.*", "publishes", "producer payout-links", "stork", "internal/webhook/event_constants.go", "payout_link.*"))
e(E("svc:stork", "event:bill_payment.*", "publishes", "producer unknown", "stork", "internal/webhook/event_constants.go:127-130", "bill_payment.*", confidence="unknown"))
e(E("flag:uiconfig-xproducts", "fe:x", "configures", "X product catalogue drives dashboard nav", "config-proto", "rzp/x/country/dashboard/uiconfig.proto:17-55", "XProducts"))

# --- Journey membership edges (journey -> primary service) ---
for j, s in [("journey:vp-invoice-to-payout", "svc:vendor-payments"), ("journey:tds-monthly-remittance", "svc:vendor-payments"),
             ("journey:po-grn-approval", "svc:vendor-payments"), ("journey:vendor-onboarding", "svc:vendor-experience"),
             ("journey:payroll-salary-disbursement", "svc:opfin"), ("journey:ondemand-settlement-to-x", "svc:capital-es"),
             ("journey:loc-withdrawal", "svc:capital-loc"), ("journey:corp-card-balance", "svc:capital-cards"),
             ("journey:ca-onboarding", "svc:master-onboarding")]:
    e(E(j, s, "executes", "journey owner", "findings", "reports/expansion/findings/02_payouts_outbound_edges.md §11-12", "journey", confidence="probable"))
for d, members in [("domain:s2p", ["svc:vendor-payments", "svc:vendor-experience", "svc:tax-compliance", "svc:accounts-receivable", "svc:accounting-integrations"]),
                   ("domain:payroll", ["svc:opfin", "svc:opfin-compliance", "svc:x-payroll-compute", "svc:x-payroll-hris", "svc:x-salary-structure", "svc:x-payroll-flexible-benefits", "svc:x-tna"]),
                   ("domain:capital", ["svc:capital-cards", "svc:capital-collections", "svc:capital-loc"]),
                   ("domain:settlements", ["svc:settlements", "svc:capital-es"]),
                   ("domain:ca-onboarding", ["svc:master-onboarding", "svc:bvs", "svc:onboarding", "svc:banking-accounts"]),
                   ("domain:accounting", ["svc:accounting-integrations", "svc:business-reporting", "svc:reporting"]),
                   ("domain:cross-border", ["svc:cross-border-import"])]:
    for m in members:
        e(E(d, m, "uses", "domain membership", "findings", "reports/expansion/EXPANSION_REPORT.md", "domain card", confidence="probable"))

# --- UNKNOWN edges (recorded, not asserted) ---
unknown = [
    E("svc:api-monolith", "q:kafka-add-tds-entry", "publishes", "monolith TdsProcessor is the only producer-shaped code found; not confirmed", "api", "app/Models/Payout/TdsProcessor/Processor.php:31-86", "TdsProcessor", confidence="unknown"),
    E("svc:payouts", "q:sqs-tax-compliance-payout-events", "publishes", "no producer found in clone root", "kube-manifests", "prod/tax-compliance/values.yaml", "prod-tax-compliance-payout-events", confidence="unknown"),
    E("svc:opfin", "svc:payouts", "calls", "whether Opfin also calls Payouts Service directly (not only monolith payouts_internal) — no source", "payouts", "internal/auth/headers.go:12", "X_PAYROLL", confidence="unknown"),
    E("svc:capital-es", "ep:monolith-payouts-internal", "calls", "capital_early_settlements in PS allow-list; whether it uses payouts_internal or public API is not confirmed", "payouts", "internal/auth/headers.go:24", "CAPITAL_EARLY_SETTLEMENTS", confidence="unknown"),
    E("svc:capital-cards", "svc:ledger", "calls", "no ledger client evidence for capital-cards despite m2p fund_account_type", "ledger", "internal/common/constant.go:221-322", "m2p", confidence="unknown"),
    E("q:sns-journal-created", "svc:business-reporting", "delivers_to", "no consumer found for prod-ledger-x-journal-created-live", "ledger", "config/prod-live.toml", "journal-created SNS", confidence="unknown"),
]
edges.extend(unknown)

patch = {
    "version": "2026-09-07.expansion.v1",
    "base_graph": "reports/PAYOUTS_SERVICE_GRAPH.json (version 2026-09-04.v2)",
    "generated_by": "reports/expansion/scripts/build_graph_patch.py",
    "new_node_types": ["business domain", "business journey"],
    "new_edge_types": ["executes"],
    "confidence_scale": ["confirmed", "probable", "inferred", "unknown"],
    "relabel_nodes": relabel,
    "nodes": nodes,
    "edges": edges,
}

def main():
    base = json.loads(BASE.read_text())
    base_ids = {n["id"] for n in base["nodes"]}
    new_ids = [n["id"] for n in nodes]
    dup_new = [i for i, c in collections.Counter(new_ids).items() if c > 1]
    clash = sorted(set(new_ids) & base_ids)
    relabel_missing = [n["id"] for n in relabel if n["id"] not in base_ids]
    all_ids = base_ids | set(new_ids)
    dangling = [(ed["source"], ed["target"]) for ed in edges if ed["source"] not in all_ids or ed["target"] not in all_ids]
    no_ev = [n["id"] for n in nodes if not n.get("evidence")] + [f'{ed["source"]}->{ed["target"]}' for ed in edges if not ed.get("file_path")]
    validation = {
        "base_nodes": len(base["nodes"]), "base_edges": len(base["edges"]),
        "patch_nodes": len(nodes), "patch_edges": len(edges), "relabels": len(relabel),
        "duplicate_new_ids": dup_new, "new_ids_clashing_with_base": clash,
        "relabel_ids_missing_in_base": relabel_missing, "dangling_edges": dangling,
        "nodes_or_edges_without_evidence": no_ev,
        "unknown_edges": len([ed for ed in edges if ed["confidence"] == "unknown"]),
        "node_type_counts": dict(collections.Counter(n["type"] for n in nodes)),
        "edge_confidence_counts": dict(collections.Counter(ed["confidence"] for ed in edges)),
        "ok": not (dup_new or clash or relabel_missing or dangling or no_ev),
    }
    (HERE / "GRAPH_PATCH.json").write_text(json.dumps(patch, indent=2, ensure_ascii=False) + "\n")
    # expanded copy
    exp = json.loads(json.dumps(base))
    exp["version"] = base["version"] + "+expansion.v1"
    exp["node_types"] = sorted(set(base["node_types"]) | set(patch["new_node_types"]))
    exp["edge_types"] = sorted(set(base["edge_types"]) | set(patch["new_edge_types"]))
    by_id = {n["id"]: n for n in exp["nodes"]}
    for r in relabel:
        by_id[r["id"]].update(r)
    exp["nodes"].extend(nodes)
    exp["edges"].extend(edges)
    (HERE / "PAYOUTS_SERVICE_GRAPH.expanded.json").write_text(json.dumps(exp, indent=2, ensure_ascii=False) + "\n")
    (HERE / "GRAPH_PATCH_VALIDATION.json").write_text(json.dumps(validation, indent=2) + "\n")
    print(json.dumps(validation, indent=2))
    return 0 if validation["ok"] else 1

if __name__ == "__main__":
    sys.exit(main())
