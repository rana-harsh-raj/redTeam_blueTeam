# Part 06 — Public docs / MCP server / agent-skills: RazorpayX surface map

Source root (read-only): `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/`
Repos examined: `markdown-docs/`, `razorpay-mcp-server/`, `agent-skills/`. All paths below are `repo/relative/path:line`.
Legend: **O** = OBSERVED (file:line), **I** = INFERRED, **U** = UNKNOWN.

## 0. Tree facts (O)

| Fact | Evidence |
|---|---|
| `markdown-docs/x/` = 172 files; `markdown-docs/payroll/` = 79 files | `find` on `markdown-docs/x`, `markdown-docs/payroll` |
| X API reference lives under `markdown-docs/api/x/` (79 files); index at `markdown-docs/api/x.md` | dir listing; `markdown-docs/api/x.md:1-4` |
| Gateway URL `https://api.razorpay.com/v1` (some resources on `/v2`), Basic Auth key/secret, TLS 1.2+ | `markdown-docs/api/x.md:59`, `:77-80`, `:21-22` |
| Existing PG API keys work for X; keys generated at `x.razorpay.com/settings/developer-controls` | `markdown-docs/api/x.md:90`, `:110` |
| 429 rate-limiting with exponential backoff recommended | `markdown-docs/api/x.md:113-119` |
| X webhook set-up page is separate from Payments webhooks | `markdown-docs/webhooks.md:53`, `markdown-docs/webhooks/setup-edit-payouts.md:6-10` |
| Test mode has its own dummy balance; contacts/FAs/payouts don't carry over | `markdown-docs/api/x.md:47-53` |
| `x/apis.md` is the canonical "List of APIs" index (Contacts, Fund Account, Account Validation, Payout, Payouts to Cards, Composite, Payout Link, Transaction, Fetch Balance, Amazon Payout) | `markdown-docs/x/apis.md:14-35` |

## 1. Section-by-section: doc paths, endpoints, webhook events

All endpoints are OBSERVED from `curl -X <METHOD> https://api.razorpay.com<path>` in the cited file (line shown). Events are OBSERVED from `"event": "..."` payloads or event tables.

| Section | Guide doc path(s) | API reference doc(s) | Endpoints (method path — file:line) | Webhook events (file:line) |
|---|---|---|---|---|
| **Payouts** | `x/payouts.md`, `x/payouts/api.md`, `x/payouts/states-life-cycle.md`, `x/payouts/status-details.md`, `x/payouts/queued.md`, `x/payouts/queued/balance-threshold.md`, `x/payouts/scheduled.md`, `x/payouts/downtimes.md`, `x/payouts/multi-bank-routing.md`, `x/payouts/intelligent-payouts.md`, `x/payouts/best-practices.md`, `x/payouts/reports.md`, `x/payouts/faqs.md` | `api/x/payouts.md`, `api/x/payouts/{create/bank-account,create/vpa,fetch-all,fetch-with-id,cancel,entity}.md`, `api/x/payout-idempotency/make-request.md` | `POST /v1/payouts` (`api/x/payouts/create/bank-account.md:26`, `.../create/vpa.md:26`); `GET /v1/payouts` (`api/x/payouts/fetch-all.md:27`); `GET /v1/payouts/{id}` (`api/x/payouts/fetch-with-id.md:18`); `POST /v1/payouts/{id}/cancel` (`api/x/payouts/cancel.md:18`); idempotent create via header `X-Payout-Idempotency` (`api/x/payout-idempotency/make-request.md:10,36`) | `payout.pending` (`webhooks/payouts.md:62`), `payout.rejected` (`:110`), `payout.queued` (`:156`), `payout.initiated` (`:202`), `payout.processed` (`:248`), `payout.updated` (`:318`), `payout.reversed` (`:364`), `payout.failed` (`:410`), `payout.downtime.started` (`:450-455`), `payout.downtime.resolved` (`:496`). Same table in `x/apis/subscribe.md:25-55` and `webhooks/all.md:106-128`. `payout.failed` subscription is mandatory for API users (`webhooks/payouts.md:46`) |
| **Payouts — approval (maker/checker)** | `x/manage-teams/approval-workflow.md`, `x/bulk-payouts/approval-workflow.md` | `api/x/payouts-approval.md`, `api/x/payouts-approval/{approve,reject,entity}.md` | `POST /v1/payouts/:id/approve` (`api/x/payouts-approval/approve.md:17`); `POST /v1/payouts/:id/reject` (`api/x/payouts-approval/reject.md:18`) | `payout.pending` (`webhooks/payouts-approval.md:28,95`) |
| **Payouts — composite (no pre-created contact/FA)** | `x/payouts/api.md:77-81` | `api/x/payout-composite.md`, `api/x/payout-composite/create/{bank-account,vpa,card,phone-number}.md` | `POST /v1/payouts` with nested `fund_account.contact` (`api/x/payout-composite/create/bank-account.md:49`, `vpa.md:49`, `card.md:54`, `phone-number.md:52`) | (payout.* above) |
| **Payouts — cards** | `x/payouts/cards.md` (RazorpayX Lite only, per description) | `api/x/payouts-cards.md`, `api/x/payouts-cards/{create/without-saving-card,create/save-card/external-token/{fund-account,payout},create/save-card/razorpay-tokenhq/{fund-account,payout},entity,security-risks}.md` | `POST /v1/fund_accounts` (card, `.../external-token/fund-account.md:16`, `.../razorpay-tokenhq/fund-account.md:16`); `POST /v1/payouts` (`.../without-saving-card.md:26`, `.../external-token/payout.md:26`, `.../razorpay-tokenhq/payout.md:26`) | (payout.* above) |
| **Payout Links** | `x/payout-links.md`, `x/payout-links/{api,create,life-cycle,set-expiry,bulk,reports,report-and-errors,shopify,faqs}.md` | `api/x/payout-links.md`, `api/x/payout-links/{create/use-contact-details,create/use-contact-id,fetch-all,fetch-with-id,cancel,entity}.md` | `POST /v1/payout-links` (`api/x/payout-links/create/use-contact-details.md:16`, `use-contact-id.md:16`); `GET /v1/payout-links` (`fetch-all.md:16`); `GET /v1/payout-links/{id}` (`fetch-with-id.md:16`); `POST /v1/payout-links/{id}/cancel` (`cancel.md:16`) | `payout_link.attempted` (`webhooks/payout-links.md:47`), `.issued` (`:94`), `.pending` (`:141`), `.processing` (`:190`), `.processed` (`:237`), `.rejected` (`:284`), `.cancelled` (`:331`), `.expired` (`:378`); table `webhooks/all.md:134-148`. Short links on `rzp.io` (`webhooks/payout-links.md:74`) |
| **Contacts** | `x/contacts.md`, `x/contacts/{api,bulk-uploads,faqs}.md` | `api/x/contacts.md`, `api/x/contacts/{create,update,activate-or-deactivate,fetch-all,fetch-with-id,entity}.md` | `POST /v1/contacts` (`api/x/contacts/create.md:17`); `PATCH /v1/contacts/{id}` update (`update.md:16`) and activate/deactivate (`activate-or-deactivate.md:16`); `GET /v1/contacts` (`fetch-all.md:16`); `GET /v1/contacts/{id}` (`fetch-with-id.md:16`) | none documented (O: no `contact.*` event anywhere in `webhooks/`) |
| **Fund Accounts** | `x/fund-accounts.md`, `x/fund-accounts/{api,faqs,reverse-penny-drop}.md` | `api/x/fund-accounts.md`, `api/x/fund-accounts/{create/bank-account,create/vpa,activate-or-deactivate,fetch-all,fetch-with-id,entity}.md` | `POST /v1/fund_accounts` (`create/bank-account.md:18`, `create/vpa.md:18`); `PATCH /v1/fund_accounts/{id}` (`activate-or-deactivate.md:16`); `GET /v1/fund_accounts` (`fetch-all.md:16`); `GET /v1/fund_accounts/{id}` (`fetch-with-id.md:16`) | none documented (no `fund_account.created/updated`; only validation events below) |
| **Fund Account Validation (FAV) / Account Validation / Reverse penny drop** | `x/fund-account-validation.md`, `x/fund-account-validation/api.md`, `x/fund-accounts/reverse-penny-drop.md`, `x/dashboard/allowlist-ip.md` | `api/x/account-validation.md`, `api/x/account-validation/{create-contact,bank-account,bank-account/create-fund-account,vpa,vpa/create-fund-account,reverse-penny-drop,fetch-all-transactions,fetch-transactions-with-id,balance-fetch,entity}.md`; composite variant `api/x/composite-account-validation/{bank-account,vpa,fetch-all-transactions,fetch-transactions-with-id,entity}.md` | `POST /v1/fund_accounts/validations` (`api/x/account-validation/bank-account.md:16`, `vpa.md:16`, `reverse-penny-drop.md:16`, composite `.../composite-account-validation/bank-account.md:16`, `vpa.md:16`); `GET /v1/fund_accounts/validations` (`fetch-all-transactions.md:16`); `GET /v1/fund_accounts/validations/{fav_id}` (`fetch-transactions-with-id.md:16`) | `fund_account.validation.completed` (`webhooks/account-validation.md:30,79`), `fund_account.validation.failed` (`:136`); table `webhooks/all.md:130-132` |
| **Transactions / Account statement** | `x/account-statement.md`, `x/account-statement/api.md`, `x/account-statement/sftp.md` (bank-portal/SFTP statements for RBL, YBL, ICICI, IDFC, Axis — `x/account-statement/sftp.md:22-26`) | `api/x/transactions.md`, `api/x/transactions/{fetch-all,fetch-with-id,entity}.md` | `GET /v1/transactions` (`api/x/transactions/fetch-all.md:16`); `GET /v1/transactions/{txn_id}` (`fetch-with-id.md:17`) | `transaction.created` (`webhooks/transactions.md:28,66`; scope "RazorpayX Lite" payouts/add-funds `webhooks/all.md:150-151`) |
| **Balances** | `x/payouts/queued/balance-threshold.md`, `x/dashboard/cashflow-insights.md` | `api/x/account-validation/balance-fetch.md` | `GET /v1/banking_balances` (`api/x/account-validation/balance-fetch.md:16`; returns per-`account_number` balances `:30,41,52`) | none |
| **Bulk payouts** | `x/bulk-payouts.md`, `x/bulk-payouts/{uploads,life-cycle,approval-workflow,report}.md`; `x/contacts/bulk-uploads.md`; `x/payout-links/bulk.md` | — (dashboard CSV/XLSX upload only; templates `x/bulk-payouts.md:93-199`) | none public (O: no `/v1/payouts/bulk` in docs) | (payout.* per row) |
| **Sub-accounts** | `x/payouts/sub-accounts.md` (master/sub-account limits; sub-account payout debits master bank account `:11`; NBFC use case `:17`) | — | none public | — |
| **Amazon Pay gift-card payouts (payout wallet)** | `x/payout-wallet/amazon.md`, `x/payout-wallet/amazon/{api,branding-guidelines}.md` | `api/x/payout-wallet.md`, `api/x/payout-wallet/create/{contact,fund-account,payout,payout-composite}.md`, `entity.md` | `POST /v1/contacts` (`api/x/payout-wallet/create/contact.md:16`); `POST /v1/fund_accounts` wallet type (`fund-account.md:19`); `POST /v1/payouts` (`payout.md:18`, `payout-composite.md:20`) | (payout.*) |
| **Tax payments** | `x/tax-payments.md`, `x/tax-payments/{automatic-tds,manual-tds,advance,gst,life-cycle}.md` (dashboard app at `x.razorpay.com/tax-payments` — `x/tax-payments.md:11`) | — | none public | none |
| **Vendor payments / Invoices / PO / GRN / Vendor portal** | `x/vendor-payments.md` + 30 sub-pages (`x/vendor-payments/{invoices,invoices/approval-workflow,approvals-on-invoices,approve-invoices,bulk-invoices,purchase-order,purchase-order/{bulk-import-po,reconciliation},grn,grn/bulk-import-grn,items,items/bulk-import-items,advances,advances/tds,gst-credit-checker,life-cycle,multi-branch-management,portal,portal/business,reports,vendor-payouts,vendor-payouts/{bulk,partial-payouts,scheduled-payouts},tally,tally/{set-up,bring-bills,sync-payment-vouchers,sync-purchase-vouchers,bank-statement-sync},faqs}.md`); app at `x.razorpay.com/vendor-payments` (`x/vendor-payments.md:15`), vendor portal `x.razorpay.com/vendor-portal/` (`x/vendor-payments/portal.md:18`) | — | none public | none (note: `webhooks/invoices.md` `invoice.*` events are Payments-side Invoices, not X vendor invoices) |
| **Corporate cards / Capital** | `x/capital.md`, `x/capital/{corporate-cards,corporate-cards/{docs-required,rbl-onboarding,ybl-onboarding},cards-kyc,line-of-credit,line-of-credit/{dashboard,faqs},cash-advance,working-capital-loans,b2b-checkout-financing/{buyer,seller},use-cases,faqs}.md`; cards dashboard `x.razorpay.com/cards` (`x/capital/faqs.md:52`), autopay `x.razorpay.com/settings/autopay` (`:88`) | — | none public | none |
| **Payroll (RazorpayX Payroll / Opfin)** | `payroll.md` + `payroll/` (79 files): run-payroll, FnF, RLOP, TDS/24Q, PF/VPF, ESI, PT (incl. `professional-tax/2fa-automation.md`), NPS, reimbursements, loans, bonus, attendance (biometric/CAMS), leaves, documents, approval workflows, KYC, integrations (Slack, WhatsApp, Zoho HRMS, Jibble, Zaggle, Plum, Pazcare, Ubitech, current account) | — | none documented; only third-party hook `https://api.opfin.com/api/camsunitv3` (`payroll/attendance.md:104`). Plans table says "API and Webhooks" is a feature (`payroll/plans.md:189`) but no endpoints/events are listed | none |
| **Current account / onboarding / account types** | `x/account-types.md`, `x/account-types/{current-account,razorpayx-lite,source-account-validation,escrow,escrow/use-cases/{co-lending,p2p-lending},faqs}.md`; bank KYC doc lists `x/icici-current-account/*.md` (5), `x/idfc-first/*.md` (5), `x/rbl-ybl-current-account/**` (10), `x/axis-current-account.md`; `x/set-up.md`, `x/quickstart.md` | — | none public (add-funds "cannot be performed via APIs" — `api/x.md:34`) | none |
| **Source accounts** | `x/account-types/source-account-validation.md` (validate bank accounts that fund RazorpayX Lite) | — | none | none |
| **Reports** | `x/reports.md`, `x/payouts/reports.md`, `x/payout-links/reports.md`, `x/bulk-payouts/report.md`, `x/vendor-payments/reports.md`, `x/manage-teams/billing.md`, `x/manage-teams/ca-portal.md` (`x.razorpay.com/reports` — `ca-portal.md:13`) | — | none | none |
| **Accounting integrations** | `x/accounting.md`, `x/accounting/tally/{account-statement,advances,vendor-payments,faqs}.md`, `x/accounting/zohobooks/{account-statement,payouts,vendor-payments}.md`, `x/tally-epayments.md` + `x/tally-epayments/{set-up,export-approve-payouts,reconcile-payouts}.md`, `x/vendor-payments/tally/*` | — | none | none |
| **Team / roles / 2FA / user groups / cost centers** | `x/manage-teams.md`, `x/manage-teams/{create-user-role,user-groups,approval-workflow,2fa,billing,ca-portal}.md`, `x/cost-centers.md` | — | none | none |
| **Developer / API keys / IP allowlist / test mode** | `x/dashboard/api-keys.md`, `x/dashboard/allowlist-ip.md`, `x/dashboard/test-mode.md`, `x/apis.md`, `x/apis/checklist.md`, `x/apis/subscribe.md`, `api/x.md`, `api/x/error-codes.md` (`:58` "API Error Source", `:66` "Error Reason and Next Steps") | — | — | — |
| **Webhooks (X)** | `webhooks/setup-edit-payouts.md`, `x/apis/subscribe.md` | `webhooks/{payouts,payouts-approval,payout-links,transactions,account-validation}.md`, `webhooks/all.md#payouts-webhooks` | — | Full X event list (22): `payout.pending`, `payout.rejected`, `payout.queued`, `payout.initiated`, `payout.processed`, `payout.updated`, `payout.reversed`, `payout.failed`, `payout.downtime.started`, `payout.downtime.resolved`, `fund_account.validation.completed`, `fund_account.validation.failed`, `payout_link.pending`, `payout_link.issued`, `payout_link.processing`, `payout_link.processed`, `payout_link.attempted`, `payout_link.cancelled`, `payout_link.rejected`, `payout_link.expired`, `transaction.created` (`webhooks/all.md:106-151`). Downtime payload entity `payout.downtime` id prefix `poutdown_` (`webhooks/payouts.md:461-463`); UPI not covered by downtime webhooks (`webhooks/payouts.md:52`) |
| **Dashboard misc / Insights** | `x/dashboard.md`, `x/dashboard/{cashflow-insights,global-search,keyboard-shortcuts,faqs}.md` | — | — | — |
| **Industry solutions** | `x/solutions/{bfsi,ecommerce,edtech,saas,travel}.md` | — | — | — |

### 1a. Public X endpoint inventory (deduplicated, O)

| # | Method | Path | Primary doc |
|---|---|---|---|
| 1 | POST | `/v1/contacts` | `api/x/contacts/create.md:17` |
| 2 | PATCH | `/v1/contacts/{id}` | `api/x/contacts/update.md:16`, `activate-or-deactivate.md:16` |
| 3 | GET | `/v1/contacts` | `api/x/contacts/fetch-all.md:16` |
| 4 | GET | `/v1/contacts/{id}` | `api/x/contacts/fetch-with-id.md:16` |
| 5 | POST | `/v1/fund_accounts` (bank_account / vpa / card / wallet) | `api/x/fund-accounts/create/bank-account.md:18`, `vpa.md:18`, `api/x/payouts-cards/.../fund-account.md:16`, `api/x/payout-wallet/create/fund-account.md:19` |
| 6 | PATCH | `/v1/fund_accounts/{id}` | `api/x/fund-accounts/activate-or-deactivate.md:16` |
| 7 | GET | `/v1/fund_accounts` | `api/x/fund-accounts/fetch-all.md:16` |
| 8 | GET | `/v1/fund_accounts/{id}` | `api/x/fund-accounts/fetch-with-id.md:16` |
| 9 | POST | `/v1/fund_accounts/validations` (bank/vpa/reverse-penny-drop/composite) | `api/x/account-validation/bank-account.md:16`, `vpa.md:16`, `reverse-penny-drop.md:16`, `api/x/composite-account-validation/bank-account.md:16` |
| 10 | GET | `/v1/fund_accounts/validations` | `api/x/account-validation/fetch-all-transactions.md:16` |
| 11 | GET | `/v1/fund_accounts/validations/{fav_id}` | `api/x/account-validation/fetch-transactions-with-id.md:16` |
| 12 | POST | `/v1/payouts` (bank/vpa/card/composite/wallet; header `X-Payout-Idempotency`) | `api/x/payouts/create/bank-account.md:26`, `api/x/payout-composite/create/*.md`, `api/x/payout-idempotency/make-request.md:34-36` |
| 13 | GET | `/v1/payouts` | `api/x/payouts/fetch-all.md:27` |
| 14 | GET | `/v1/payouts/{id}` | `api/x/payouts/fetch-with-id.md:18` |
| 15 | POST | `/v1/payouts/{id}/cancel` | `api/x/payouts/cancel.md:18` |
| 16 | POST | `/v1/payouts/{id}/approve` | `api/x/payouts-approval/approve.md:17` |
| 17 | POST | `/v1/payouts/{id}/reject` | `api/x/payouts-approval/reject.md:18` |
| 18 | POST | `/v1/payout-links` | `api/x/payout-links/create/use-contact-details.md:16` |
| 19 | GET | `/v1/payout-links` | `api/x/payout-links/fetch-all.md:16` |
| 20 | GET | `/v1/payout-links/{id}` | `api/x/payout-links/fetch-with-id.md:16` |
| 21 | POST | `/v1/payout-links/{id}/cancel` | `api/x/payout-links/cancel.md:16` |
| 22 | GET | `/v1/transactions` | `api/x/transactions/fetch-all.md:16` |
| 23 | GET | `/v1/transactions/{id}` | `api/x/transactions/fetch-with-id.md:17` |
| 24 | GET | `/v1/banking_balances` | `api/x/account-validation/balance-fetch.md:16` |

O: No public endpoint is documented for bulk payouts, sub-accounts, tax payments, vendor payments, corporate cards, capital, payroll, current-account onboarding, add-funds, reports, roles, or accounting sync (grep of `api.razorpay.com/v[12]/` across `x/`, `api/x/`, `payroll/` returns only the 24 above).

## 2. Product surfaces beyond core payouts (O unless flagged)

| Surface | Doc path(s) | What is described | API/journey | Notes |
|---|---|---|---|---|
| Tax Payments (TDS auto/manual, advance tax, GST challan) | `x/tax-payments.md`, `x/tax-payments/{automatic-tds,manual-tds,advance,gst,life-cycle}.md` | Dashboard "Tax Payment app"; states in `life-cycle.md`; GST challans fetched from GSTIN portal (`gst.md`) | Dashboard-only; no API | X app at `x.razorpay.com/tax-payments` (`x/tax-payments.md:11`) |
| Vendor Payments / Invoices / PO / GRN / Items / Vendor Portal / GST credit checker | `x/vendor-payments.md` + 30 sub-pages (see §1) | Invoice import/OCR, approvals via email, PO/GRN reconciliation, partial & scheduled vendor payouts, multi-branch, advances with TDS, Tally sync | Dashboard-only; no API | `gst.gov.in` referenced (`x/vendor-payments/gst-credit-checker.md:26`) |
| Corporate Cards (RBL / Yes Bank) | `x/capital/corporate-cards*.md`, `x/capital/cards-kyc.md` | Onboarding, eligibility, KYC docs | Dashboard-only | `x.razorpay.com/cards` (`x/capital/faqs.md:52`) |
| Capital (LoC, cash advance, working-capital loans, B2B checkout financing) | `x/capital.md`, `x/capital/**` | Loan products applied via X dashboard | Dashboard-only | Partners referral to Capital via `partners/capital.md:202-212` links to X dashboard |
| Payroll (XPayroll / Opfin) | `payroll.md`, `payroll/**` (79) | Full HR/payroll product: run payroll, statutory (TDS/PF/ESI/PT/NPS), reimbursements, loans, attendance, leaves, integrations | Separate product at `payroll.razorpay.com` (`payroll/quickstart.md:8`); `api.opfin.com` third-party hook (`payroll/attendance.md:104`); no public API docs | "API and Webhooks" listed as a plan feature (`payroll/plans.md:189`) but undocumented — **U** |
| Current Account (RBL/YBL/ICICI/IDFC/Axis), RazorpayX Lite, Escrow Plus | `x/account-types/**`, `x/icici-current-account/**`, `x/idfc-first/**`, `x/rbl-ybl-current-account/**`, `x/axis-current-account.md` | KYC doc lists per entity type; escrow use cases (co-lending, P2P) | Dashboard/manual | Escrow trustees `axistrustee.in`, `mitconcredentia.in` (`x/account-types/escrow.md:39-40`) |
| Source-account validation (funding RazorpayX Lite) | `x/account-types/source-account-validation.md` | Validate the bank account that funds Lite | Dashboard | — |
| Cashflow Insights | `x/dashboard/cashflow-insights.md` | Cashflow summary across accounts | Dashboard | — |
| Accounting (Tally, Zoho Books, TallyPrime e-Payments) | `x/accounting/**`, `x/tally-epayments/**`, `x/vendor-payments/tally/**` | Sync statements/payouts/vendor payments; TallyPrime e-payments export→approve→reconcile | Desktop-connector / dashboard | `help.tallysolutions.com` (`x/tally-epayments/set-up.md:42`), `labhsoftware.in` connector (`x/vendor-payments/tally/set-up.md:83`) |
| Amazon Pay gift-card payouts | `x/payout-wallet/amazon*.md`, `api/x/payout-wallet/**` | Wallet-type fund account + payout | API (§1a #5, #12) | `amazon.in`, `amazonbal.qwikcilver.com` (`x/payout-wallet/amazon.md:60-61`) |
| Shopify payout links | `x/payout-links/shopify.md` | Shopify app creating payout links | App | `apps.shopify.com` (`:17`) |
| Bulk uploads (payouts, contacts+FAs, payout links, invoices, PO, GRN, items) | `x/bulk-payouts/**`, `x/contacts/bulk-uploads.md`, `x/payout-links/bulk.md`, `x/vendor-payments/*bulk*` | CSV/XLSX templates and batch statuses | Dashboard | — |
| Sub-accounts (master/sub limits) | `x/payouts/sub-accounts.md` | NBFC/LSP use case; sub-account payouts debit master | Dashboard | RBI doc link (`:21`) |
| Multi-bank routing / intelligent payouts / downtimes | `x/payouts/multi-bank-routing.md`, `intelligent-payouts.md`, `downtimes.md` | Dynamic routing across partner banks; downtime alerts | Webhooks `payout.downtime.*` | — |
| Partner APIs | `api/partners/**`, `partners/**` | Payments-side partner onboarding; only X reference is Capital referral flow (`partners/capital.md:202-234`) | Not X | **I**: no X partner API surface documented |
| MCP server | `mcp-server.md`, `mcp-server/{remote,local,oauth,configuration,tools-reference,use-cases,faqs}.md` | Remote `https://mcp.razorpay.com/mcp` (`mcp-server/remote.md:111`), OAuth endpoints (`mcp-server/oauth.md:59-68`); 2 payout tools | See §3 | — |

### 2a. External domains referenced (first occurrence, O)

| Host | Count | First file:line | Meaning |
|---|---|---|---|
| `x.razorpay.com` | 283 | `api/x.md:95` | X dashboard; sub-paths: `/auth`, `/auth/signup`, `/demo`, `/settings/developer-controls` (`api/x.md:110`), `/settings/banking` (`api/x/payout-links/create/use-contact-id.md:76`), `/settings/autopay` (`x/capital/faqs.md:88`), `/cards` (`x/capital/faqs.md:52`), `/payout-links` (`x/payout-links/report-and-errors.md:16`), `/reports` (`x/manage-teams/ca-portal.md:13`), `/tax-payments` (`x/tax-payments.md:11`), `/vendor-payments` (`x/vendor-payments.md:15`), `/vendor-portal/` (`x/vendor-payments/portal.md:18`) |
| `payroll.razorpay.com` | 155 | `payroll.md:37` | Payroll app; `/login` (`payroll/quickstart.md:8`), `/moneyTransfer` (`:136`), `/run-payroll` (`payroll/faqs.md:405`), `/reports/tdsPayments` (`:436`), `/taxDeductions` (`payroll/employees/declarations.md:53`), `/insurance/...` (`payroll/integrations/insurance/plum.md:69,81`) |
| `api.razorpay.com` | 54 | `api/x.md:59` | API gateway |
| `mcp.razorpay.com` | 21 | `mcp-server/oauth.md:59` | Remote MCP: `/mcp`, `/sse` (`mcp-server/remote.md:111`), `/authorize`, `/token`, `/register`, `/.well-known/oauth-authorization-server` (`mcp-server/oauth.md:59-68`) |
| `api.opfin.com` | 2 | `payroll/attendance.md:104` | Legacy Opfin API host (`/api/camsunitv3` biometric webhook) |
| `dashboard.razorpay.com` | 5 | `api/x.md:110` | PG dashboard keys |
| `rzp.io` | 15 | `webhooks/payout-links.md:74` | Payout-link short URLs |
| `www.postman.com` | 13 | `api/x.md:41` | Public Postman workspace |
| Banks: `rblbank.com`, `yesbank.in`, `icicibank.com`, `idfcfirst.bank.in`, `axis.bank.in` | 1 each | `x/account-statement/sftp.md:22-26` | Statement portals |
| `gst.gov.in`, `incometax.gov.in`, `tdscpc.gov.in`, `epfindia.gov.in`, `esic.gov.in`, `k2.karnataka.gov.in` | — | `x/vendor-payments/gst-credit-checker.md:26`, `payroll/tds.md:43,28`, `payroll/provident-fund.md:175`, `payroll/esi.md:16`, `payroll/professional-tax.md:208` | Statutory portals |
| `help.tallysolutions.com`, `labhsoftware.in` | 6, 1 | `x/tally-epayments/set-up.md:42`, `x/vendor-payments/tally/set-up.md:83` | Tally connectors |
| `apps.shopify.com`, `amazon.in`, `amazonbal.qwikcilver.com` | 1, 8, 1 | `x/payout-links/shopify.md:17`, `x/payout-wallet/amazon.md:60-61` | Channel partners |
| `camsunit.com`, `developer.camsunit.com`, `jibble.io`, `app.plumhq.com`, `app.pazcare.com`, `icicilombard.com` | — | `payroll/attendance.md:61,86`, `payroll/integrations/jibble.md:47`, `.../plum.md:77,109`, `.../pazcare.md:46` | Payroll integrations |
| `axistrustee.in`, `mitconcredentia.in`, `rbidocs.rbi.org.in` | 1 each | `x/account-types/escrow.md:39-40`, `x/payouts/sub-accounts.md:21` | Escrow / regulatory |

## 3. razorpay-mcp-server tools (O)

Tool registry: `razorpay-mcp-server/pkg/razorpay/tools.go:18-130`; toolsets: `payments`, `payment_links`, `orders`, `refunds`, `payouts`, `qr_codes`, `registration_links`, `settlements`, `checkout_integration` (`tools.go:21,35-36,49,60,72,78,91-92,98,116-117`). SDK: `github.com/razorpay/razorpay-go v1.4.0` (`go.mod:9`). Remote host `https://mcp.razorpay.com/mcp` (`README.md:118`).

| Toolset | Tool (code name) | Go file:line | API doc mapped (README) | X-related? |
|---|---|---|---|---|
| **payouts** | `fetch_payout_with_id` | `pkg/razorpay/payouts.go:58-60` (calls `client.Payout.Fetch` `:45`) | `api/x/payouts/fetch-with-id` (`README.md:56`) → `GET /v1/payouts/{id}` | **YES** |
| **payouts** | `fetch_all_payouts` (requires `account_number`) | `pkg/razorpay/payouts.go:121-123` (`client.Payout.All` `:112`; param `:73`) | `api/x/payouts/fetch-all` (`README.md:55`) → `GET /v1/payouts` | **YES** |
| payments | `fetch_payment`, `fetch_payment_card_details`, `update_payment`, `capture_payment`, `fetch_all_payments`, `initiate_payment`, `resend_otp`, `submit_otp` | `pkg/razorpay/payments.go:62,118,180,254,330,853,953,1027` | Payments APIs (`README.md:17-24`) | no |
| payment_links | `create_payment_link`, `payment_link_upi_create`, `fetch_payment_link`, `payment_link_notify`, `update_payment_link`, `fetch_all_payment_links` | `pkg/razorpay/payment_links.go:148,291,344,408,495,557` | (`README.md:25-30`) | no (Payment Links, not Payout Links) |
| orders | `create_order`, `fetch_order`, `fetch_all_orders`, `fetch_order_payments`, `update_order` | `pkg/razorpay/orders.go:154,224,325,381,443` | (`README.md:31-35`) | no |
| refunds | `create_refund`, `fetch_refund`, `update_refund`, `fetch_multiple_refunds_for_payment`, `fetch_specific_refund_for_payment`, `fetch_all_refunds` | `pkg/razorpay/refunds.go:85,138,197,269,330,393` | (`README.md:36-41`) | no |
| qr_codes | `create_qr_code`, `fetch_qr_code`, `fetch_all_qr_codes`, `fetch_qr_codes_by_customer_id`, `fetch_qr_codes_by_payment_id`, `fetch_payments_for_qr_code`, `close_qr_code` | `pkg/razorpay/qr_codes.go:131,182,258,310,363,451,502` | (`README.md:42-48`) | no |
| settlements | `fetch_settlement_with_id`, `fetch_settlement_recon_details`, `fetch_all_settlements`, `create_instant_settlement`, `fetch_all_instant_settlements`, `fetch_instant_settlement_with_id` | `pkg/razorpay/settlements.go:58,134,209,285,372,427` | (`README.md:49-54`) | no |
| payments (tokens) | `fetch_tokens`, `revoke_token` | `pkg/razorpay/tokens.go:131,232` | (`README.md:57-58`) | no |
| registration_links | `create_registration_link` | `pkg/razorpay/registration_links.go:183` | (`README.md:59`) | no |
| checkout_integration | `detect_stack`, `integrate_razorpay_checkout` | `pkg/razorpay/integrations/detect_stack_tool.go:77`, `checkout_tool.go:121` | helpers (`README.md:60-61`) | no |

Findings:
- **Naming drift (O):** README/docs advertise `fetch_payout_by_id` (`razorpay-mcp-server/README.md:56`, `markdown-docs/mcp-server/tools-reference.md:99`) but the registered tool name is `fetch_payout_with_id` (`pkg/razorpay/payouts.go:59`). Similar drift for `create_payment_link_upi` vs code `payment_link_upi_create` (`payment_links.go:291`) and `send_payment_link` vs `payment_link_notify` (`:408`).
- Payouts toolset is read-only (only `AddReadTools`, `tools.go:72-75`); no create/approve/cancel payout, no contacts/fund-accounts/FAV/transactions/payout-links tools. Payouts tests use `constants.PAYOUT_URL` from razorpay-go (`pkg/razorpay/payouts_test.go:18,117`).
- Docs claim "35+ tools" and "Payouts (2 tools)" (`markdown-docs/mcp-server/tools-reference.md:12,20`).

## 4. agent-skills: RazorpayX-domain skills (O)

| Path | Gist | Repo / service / team / Slack names cited |
|---|---|---|
| `agent-skills/domain/payouts/SKILL.md` (name `payouts-domain`, v2.0.0) | Full payouts platform: 10 services, CA & VA payout paths, FTS transfer flow, reversal detection via XAS, balances, bulk, sub-accounts | Repos: `~/git/api` (`:52,168`), `~/git/payouts` (`:64,169`), `~/git/fts` (`:84,170-171`), `~/git/cfa` (`:172`), `~/git/x-balances` (`:173`), `~/git/x-account-statements` (`:174`) |
| `domain/payouts/modules/services/api-monolith.md` | PHP Laravel entry layer; routes migrated merchants to Payout Service; dual-write via SQS; endpoints incl. `POST /payouts`, `/validate_payouts`, `/payouts/{id}/approve|reject|cancel|retry`, `/payouts/bulk`, `/payouts_with_otp`, `/payouts/2fa/create`, `/payouts/partner-bank/status` (`:14-27`); outbound to Payout Service `env('PAYOUTS_URL')` Basic Auth `payout_key/payout_secret` (`:34-35`) | Repo `~/git/api`; internal paths `/v1/payouts/payouts_internal`, `/v1/payouts/internal_contact_payout`, `/v1/payouts/cancel_payout/{id}`, `/v1/payouts/retry`, `/v1/payouts/bulk` (`:40-46`); `PayoutOutbox` retry table (`:93`) |
| `domain/payouts/modules/services/payout-service.md` | Go core payout entity/lifecycle for migrated merchants | Repo `~/git/payouts` (`:4`) |
| `domain/payouts/modules/services/fts.md` | Fund Transfer Service + FTS Workers; routes via Mozart | Repo `~/git/fts` (`:4`) |
| `domain/payouts/modules/services/cfa.md` | Contact Fund Account service (contacts + fund accounts) | Repo `~/git/cfa` (`:4`) |
| `domain/payouts/modules/services/x-balance.md` | Per-merchant per-bank balance; live balance via Mozart | Repo `~/git/x-balances` (`:4`) |
| `domain/payouts/modules/services/x-account-statements.md` | XAS: bank statement fetch/store; reversal detection | Repo `~/git/x-account-statements` (`:4`) |
| `domain/payouts/modules/services/{batch-service,ledger,mozart,bank-gateways}.md` | Bulk file processing (S3, 5 splits); balance deduction (PHP monolith for VA, moving to Ledger service `ledger.md:6`); bank gateway abstraction; bank matrix (YesBank VA, Slice CA Apr-2026 `bank-gateways.md:15-17`) | — |
| `domain/payouts/guides/{new-joiner-onboarding,on-call-debugging}.md`, `references/{glossary,incidents-and-gotchas}.md` | Onboarding + on-call; "PSE (Payout Support Engineering) team runs a daily triage" (`new-joiner-onboarding.md:136`, `incidents-and-gotchas.md:326`) | Team: **PSE** |
| `domain/source-to-pay/SKILL.md` (name `source-to-pay-domain`) | RazorpayX Source-to-Pay: vendor onboarding/KYC, invoices (manual/OCR/email), approvals, vendor payouts, PO/GRN, TDS/tax, Zoho/Tally sync | Services `vendor-payments`, `accounting-integrations`, `vendor-experience` (`:4-9`); knowledge files `knowledge/service_{vendor_payments,vendor_experience,accounting_integrations,api_monolith}.md` |
| `domain/source-to-pay/knowledge/service_vendor_payments.md` | `github.com/razorpay/vendor-payments` (Go, MySQL, Kafka, Twirp); `vdpm_` invoices state machine draft→in_approval→unpaid→processing→paid (`:3-12`); PO `po_`, GRN `grn_`, line items `item_` | Repo `razorpay/vendor-payments` |
| `domain/source-to-pay/knowledge/service_vendor_experience.md` | `github.com/razorpay/vendor-experience` (Go); vendor application lifecycle; on approval creates contact+FA in API monolith (`:3,18`); BVS doc verification | Repo `razorpay/vendor-experience` |
| `domain/source-to-pay/support/triage_guide.md` | Slack: `#x-vendor-payments` (`:116`), `#x-vendor-onboarding` (`:119`), `#x-vendor-experience-alerts`, `#x-vp-sentry-alerts`, `#x-vp-kafka-alerts` (`:110,119-120`) | — |
| `domain/post-payments-domain/.../refund/modules/services/payout-x.md` | "Service: Payout / RazorpayX" — fund-transfer execution for instant refunds, webhook back to Scrooge (`:1-6`) | Scrooge (refunds) |
| `domain/post-payments-domain/.../instant-settlements/modules/services/payouts-service.md` | Payouts Service for instant-settlement fund transfer (`:1-8`) | — |
| `teams/payouts/skills/fav-engineer/SKILL.md` | FAV Engineer agent: bank/VPA validation flows, gateways Citi (via Mozart), FTS, Slice, API gateway; ValidX (`:3,16`) | Slack `#x-support`, `#x-production-issues` (`evals/eval-set.md:3`) |
| `teams/payouts/skills/payout-fee-recovery-debugger/SKILL.md` | Debug `queued_reason=fee_recovery_pending`, fee-recovery cron (`:3-6`); team `payouts` | `github.com/razorpay/payouts` (`references/README.md:104`); Slack `#payouts-oncall` (`references/ADMIN_ACTIONS.md:233`) |
| `teams/banking/skills/ca-onboarding-oncall-debugger/SKILL.md` | X Current Account onboarding on-call: `master-onboarding` orchestrator + `banking-accounts` (BAS, partner-bank state machines) (`:5,11,21`) | `github.com/razorpay/banking-accounts` (`references/queries.md:334`); Slack `#x-onboarding-eng` (`references/mcp-installation-guide.md:21`), `#x-onboarding-oncall` (`teams/banking/README.md:17`) |
| `teams/razorpayx/skills/x-ao-*/SKILL.md` (classify, repro, local-repro, rca, solution, solution-reviewer, implement, pr-monitor) | "X-AO" autonomous bug triage/fix pipeline for RazorpayX Dashboard bugs from DevRev (`x-ao-classify/SKILL.md:5-9`) | `github.com/razorpay/x` (`x-ao-pr-monitor/references/pr-monitor-contract.md:115`) |
| `teams/razorpayx/skills/x-marvin-interview-synthesis/SKILL.md` | Customer-interview synthesis for RazorpayX & Payroll | Slack `#x-customers` (`:58`) |
| `teams/x-payroll/skills/*` (payroll-migration-validator, prd-to-tech-spec, build-orchestrator, codebase-explorer, e2e-test-harness, test-*) | `payroll-migration-validator`: diffs between **monolith (Opfin)** and **rearch** payroll engines (`:3`); devstack services `x-payroll` (PHP monolith, ns `opfin`), `x-payroll-compute`, `x-salary-structure` (`test-suite-debugger/references/devstack-recipes.md:32-36`) | Repos/services: `opfin`, `x-payroll-compute`, `x-salary-structure`; DataHub owner group `team-xpayroll-analytics` (`analytics/.../razorpayx/x-payroll/x-payroll.yml:5`), source tables `realtime_prod_opfin.*` |
| `teams/banking-vas/skills/{catalyst-uat-deploy,csw-uat-deploy}/SKILL.md` | Banking-VAS UAT deploys: `razorpay/catalyst`, `razorpay/custom-solutions-web`, external-bank UAT `solutions.ext.dev.razorpay.in` | Slack `#banking_vas_oncall_alerts` (`catalyst-uat-deploy/SKILL.md:54`) |
| `observability/skills/banking-opex/SKILL.md` | Weekly OPEX summary for Banking team: catalyst, custom-solutions-web, banking-bridge (`:3-6`) | Services `catalyst`, `custom-solutions-web`, `banking-bridge` |
| `support/skills/banking-invoice/SKILL.md` | Banking invoice/payout/FAV/GST support reconciliation | Slack `#payout-file-reconciliation-workflow` (`:194`), `#payout-calculation-and-gst` (`references/support-faq.md:9`) |
| `support/skills/banking-org-onboarding/SKILL.md` | Multi-tenant banking ORG onboarding PRs across `kube-manifests`, `terraform-kong`, `vishnu`, `pg-onboarding-service`, `api`, `dashboard` (`:252-260`) | — |
| `analytics/skills/self-serve-analytics/datahub/metadata/glossary/razorpayx/*.yml` | DataHub glossaries: X BB Plus, BB+ Payouts, BB+ FAV, BB+ Smart Collect, X Payroll; owner `neobanking` (`x-bb-plus-payouts.yml:4-5`) | Team `neobanking`, `team-xpayroll-analytics` |
| `data/skills/data-org-api-monolith-table-drop-validator/SKILL.md` | Datalake DB names e.g. `payouts` (`:25`) | — |

## 5. Signal-strength table, probable repo/service names, unknowns

### 5a. Domain signal strength (public docs → internal skills)

| Domain | Public API docs | Public webhooks | MCP tools | agent-skills coverage | Signal |
|---|---|---|---|---|---|
| Payouts (create/fetch/cancel/approve/composite/cards/idempotency) | Strong (§1a #12-17) | 10 events | 2 read tools | Strong (`domain/payouts`, `teams/payouts`) | **HIGH** |
| Contacts + Fund Accounts | Strong (#1-8) | none | none | `cfa.md` | HIGH |
| FAV / reverse penny drop / composite validation | Strong (#9-11) | 2 events | none | `fav-engineer`, ValidX | HIGH |
| Payout Links | Strong (#18-21) | 8 events | none | none found | MEDIUM |
| Transactions / statements / balances | #22-24 | 1 event | none | `x-account-statements.md`, `x-balance.md` | MEDIUM-HIGH |
| Bulk payouts | dashboard only | — | — | `batch-service.md`, `bulk-payout-flow.md` | MEDIUM |
| Sub-accounts | dashboard only | — | — | `sub-account-payout-flow.md` | MEDIUM |
| Vendor payments / S2P / accounting | dashboard only | — | — | Strong (`domain/source-to-pay`) | MEDIUM (internal-only APIs) |
| Tax payments | dashboard only | — | — | mentioned in S2P (`TDS/tax payments`) | LOW-MEDIUM |
| Current account onboarding | docs lists only | — | — | `ca-onboarding-oncall-debugger` (master-onboarding, banking-accounts) | MEDIUM |
| Corporate cards / Capital | dashboard only | — | — | none (Capital ES only via instant settlements `capital-es.md`) | LOW |
| Payroll (Opfin) | none | none | none | `teams/x-payroll` (opfin monolith + rearch services) | MEDIUM (internal only) |
| Banking VAS / Smart Collect | none in x/ | `virtual_account.*` are Payments-side (`webhooks/smart-collect.md`) | none | `teams/banking-vas`, glossary `x-bb-plus-smart-collect` | LOW-MEDIUM |

### 5b. Probable repo / service names (source of name)

| Name | Type | Source | Confidence |
|---|---|---|---|
| `api` (API monolith, PHP Laravel) | repo | `agent-skills/domain/payouts/modules/services/api-monolith.md:4-6`; `support/skills/banking-org-onboarding/SKILL.md:257` | O |
| `payouts` (Payout Service, Go) | repo | `domain/payouts/modules/services/payout-service.md:4`; `teams/payouts/.../references/README.md:104` | O |
| `fts` (Fund Transfer Service + workers) | repo | `domain/payouts/modules/services/fts.md:4` | O |
| `cfa` (Contact Fund Account) | repo | `domain/payouts/modules/services/cfa.md:4` | O |
| `x-balances` | repo | `domain/payouts/modules/services/x-balance.md:4` | O |
| `x-account-statements` (XAS) | repo | `domain/payouts/modules/services/x-account-statements.md:4` | O |
| `mozart` (bank gateway) | service | `domain/payouts/modules/services/mozart.md:1-6` | O (repo path not given) |
| `ledger` (standalone Ledger service, in progress) | service | `domain/payouts/modules/services/ledger.md:6` | O |
| Batch Service | service | `domain/payouts/modules/services/batch-service.md:1-6` | O (repo name **U**) |
| `vendor-payments`, `vendor-experience`, `accounting-integrations` | repos | `domain/source-to-pay/knowledge/service_vendor_payments.md:3`, `service_vendor_experience.md:3`, `domain/source-to-pay/SKILL.md:9` | O |
| `banking-accounts` (BAS), `master-onboarding` | repos/services | `teams/banking/skills/ca-onboarding-oncall-debugger/SKILL.md:5`, `references/queries.md:334` | O |
| `x` (RazorpayX dashboard frontend) | repo | `teams/razorpayx/skills/x-ao-pr-monitor/references/pr-monitor-contract.md:115` | O |
| `opfin` (Payroll PHP monolith), `x-payroll-compute`, `x-salary-structure` | services/namespaces | `teams/x-payroll/skills/test-suite-debugger/references/devstack-recipes.md:32-36` | O |
| `catalyst`, `custom-solutions-web`, `banking-bridge` | repos/services (Banking VAS) | `teams/banking-vas/skills/catalyst-uat-deploy/SKILL.md:3-4`, `csw-uat-deploy/SKILL.md:3-5`, `observability/skills/banking-opex/SKILL.md:4` | O |
| ValidX | service (FAV) | `teams/payouts/skills/fav-engineer/SKILL.md:3` | O (repo name **U**) |
| Scrooge (refunds → payouts) | service | `domain/post-payments-domain/.../payout-x.md:6` | O |
| `kube-manifests`, `terraform-kong`, `vishnu`, `pg-onboarding-service`, `dashboard` | infra/onboarding repos | `support/skills/banking-org-onboarding/SKILL.md:252-260` | O |
| Teams: PSE (Payout Support Engineering), `payouts`, `banking`, `banking-vas`, `razorpayx`, `x-payroll`, `neobanking` (DataHub owner) | teams | `domain/payouts/guides/new-joiner-onboarding.md:136`; `agent-skills/teams/` dir names; `analytics/.../x-bb-plus-payouts.yml:5` | O |
| Slack: `#payouts-oncall`, `#x-support`, `#x-production-issues`, `#x-customers`, `#x-onboarding-eng`, `#x-onboarding-oncall`, `#x-vendor-payments`, `#x-vendor-onboarding`, `#x-vendor-experience-alerts`, `#x-vp-sentry-alerts`, `#x-vp-kafka-alerts`, `#banking_vas_oncall_alerts`, `#payout-file-reconciliation-workflow`, `#payout-calculation-and-gst` | channels | §4 rows | O |

### 5c. Unknowns / gaps

| Item | Status |
|---|---|
| Public API for bulk payouts, sub-accounts, tax payments, vendor payments, cards, capital, payroll, add-funds, reports | **U** — not in public docs; monolith exposes `/payouts/bulk`, `/payouts_with_otp`, `/payouts/2fa/create` internally per `api-monolith.md:23-26` (I: dashboard-only routes) |
| Payroll "API and Webhooks" plan feature (`payroll/plans.md:189`) | **U** — no endpoints or events documented anywhere in `payroll/` |
| Whether `payout.downtime.*` covers UPI | O: explicitly not (`webhooks/payouts.md:52`) |
| Payout-links / tax / vendor internal repo owning teams | **U** (S2P Slack channels known; payout-links repo not named in agent-skills) |
| Batch Service, ValidX, Mozart repo names | **U** (services named, repo paths absent) |
| MCP `fetch_payout_by_id` naming: docs vs code | O drift (`README.md:56` vs `payouts.go:59`) — which is authoritative at runtime = code |
| `_manifest.json` / `_url-md-map.json` semantics | not inspected (large); **U** |
| Partner (aggregator/OAuth) X scopes | **U** — `partners/` mentions X only for Capital referral (`partners/capital.md:202-212`) |
