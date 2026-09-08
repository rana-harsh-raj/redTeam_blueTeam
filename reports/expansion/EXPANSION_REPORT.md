# RazorpayX functional-graph expansion beyond Payouts — evidence-backed recommendation

Date 2026-09-07 · Branch `graph-expansion-adjacent-domains` (worktree, base tag
`assurance-m5-autonomous-discovery` = 2613f38; historical tags untouched) · Author: RedGrid lead
session with 8 discovery agents (reports in `findings/`).

Evidence legend throughout: **OBS** observed in the pinned clones · **INF** inferred · **UNK** unknown.
Clone root and pins: `reports/ACCESS_DELTA.md`; 65 repos cloned on 2026-09-03/04 plus 2 shallow
clones made today (`x-attendance`, `x-payroll-commons`).

---

## 1. Candidate domains (assessed: 6; task minimum: 3)

| # | Domain | Connection to Payouts (evidence lines) | Source available now | Verdict |
|---|---|---|---|---|
| 1 | **Vendor Payments + Tax Payments/TDS (S2P apps)** | 4 payout source types on one SNS topic (`payouts/config/prod.toml:375-378`); internal app `vendor_payments` in PS allow-list (`payouts/internal/auth/headers.go:13`) + contact-type gate `rzp_tax_pay` (`internal/app/contact/type.go:17`); creates payouts via monolith `payouts_internal` / `internalContactPayout` (`vendor-payments/internal/payout/core.go:82-86`); 4 workflow types on the shared engine (`workflows/internal/constants/constants.go:36-40`); tag-back route (`api/app/Http/Route.php:2339`); ~150 + 40 monolith proxy routes | **vendor-payments, vendor-experience, accounting-integrations cloned**; all contracts in `proto/` | **PRIMARY** |
| 2 | **Payroll (XPayroll/Opfin)** | own SNS topic (`prod.toml:369`), contact type `rzp_xpayroll`, maker-checker bypass `skip_wf_for_payroll` (`baseHelper.go:185-191`), 11-route monolith allow-list (`Route.php:18626-18638`), status callback `POST /v2/api/merchant-payout-status` (`api/app/Services/XPayroll/Service.php:26`), dedicated FTS MID list | **none** (9 repos 404; 2 listable repos are empty templates) | **SECONDARY** (external-caller simulator) |
| 3 | Capital (corporate cards, LOC, collections, early settlement) | 5 internal apps in allow-list (`headers.go:17,24`), 3 SNS topics, `corp_card` X balance (`api/app/Models/CorpCard/Core.php:50-62`), LOC callback (`Route.php:1557-1562`), x-balances eligibility hook | none (10 repos 404; separate BU) | edges added; not replicable |
| 4 | PG Settlements consuming X (on-demand / early settlement) | `RazorpayXClient` public-API payouts (`api/app/Services/RazorpayXClient.php:131-190`), `settlements_service` posts `payouts_internal` (authz `internal.csv:8`), `StatusUpdatePayout` Twirp, FTS `SETTLEMENT` product | none (PG BU) | stub relabelled; edges added |
| 5 | Current-Account onboarding + KYC | identity spine merchant→balance→banking_account→ledger (`api/app/Models/Merchant/Balance/Ledger/Core.php:133-160`); Kong `partner_lms`; `business_id` | banking-accounts cloned (already F2); mob/bvs 404 | extend existing node |
| 6 | Accounting integrations + reporting | SNS `generic_accounting`; Kafka accounting-payouts status; `business_reporting` in allow-list (`headers.go:21`) | accounting-integrations cloned | bundled with #1 |

Also discovered and added as edges only: **Cross-border import / ICA transfers** (2 source types, allow-list entry, raised limits: `validation.go:239-247`), **Refunds/Scrooge**, **RazorpayWallet** (Issuing BU; stub relabelled), **Bill payments/BBPS** (no payouts edge found).

**Cards specifically** (the task's hint): not a standalone domain. In the monolith `CorporateCard` is a card-on-file for payouts-to-cards (`corporate_cards`, 5 routes; feature `payout_to_cards`), while the card *product* is `capital-cards` (Capital BU, `prod-capital` cluster, `@razorpay/Capital_BE`), reached from Payouts in 2 hops (payouts → cfa → bin-service/token-service, `cfa/config/prod.toml:67-86`) and via the `corp_card` balance. Its X-dashboard routes are commented out in `x` (findings/06 §8). No source. Poor replication ROI; strong edge value (added).

---

## 2. Cross-domain graph findings

1. **The adjacency shortlist is literally in the code.** `payouts/internal/auth/headers.go:11-25` `allowedInternalApps` (verified first-hand) names 13 originators; `internal/app/contact/type.go:16-21` binds four of them to internal contact types; `config/prod.toml:367-388` maps 14 source types to 10 SNS topics; the monolith `SourceUpdater/Factory.php:14-90` fans each topic to its owning service. Every candidate above appears in at least two of these four lists.
2. **Two of the existing stub nodes were wrong about what they are.** `svc:settlements` and `svc:wallet` are PG/Issuing-BU services, not X services; both now carry 5–8 independent evidence hooks and are relabelled (`GRAPH_PATCH.json` `relabel_nodes`).
3. **Ledger and Workflows are shared platforms with far more callers than the graph shows**: 36 ledger client keys across tenants X/PG/PG_US/PG_SG/X_MY (`ledger/config/prod-live.toml:174-281`); 12 workflow callers (`workflows/config/prod.toml:185-221`) incl. abacus, offers-engine, vendor-experience, growth, payments-bank-transfer.
4. **Tax/TDS never touches the Payouts Service directly**; it goes through the monolith's `internalContactPayout` and is tagged back with `tax-payment-id`. `tax-compliance-vault` is a **Payouts-side** dependency (`payouts/config/prod.toml:718-724`), not a vendor-payments one (grep negative).
5. **Payroll is edge-rich on the backend and edge-less on the frontend**: X only SSO-redirects to `payroll.razorpay.com` (`x/config.js:302`; `Routes.js:1407`) but Opfin calls 11 monolith routes as app `xpayroll`, has a dedicated maker-checker bypass, a dedicated FTS MID list, and receives status by callback.
6. **The X product catalogue is a 20-entry enum** in `config-proto/rzp/x/country/dashboard/uiconfig.proto:17-55` (insights, vendor_payments, tax_payments, cash_advance, receivables, petty_cash, accounting, line_of_credit, payout_links, cost_centers, payroll, finance_x, …) — a canonical list to reconcile future graph work against.
7. **Stork's webhook plane has 31 event families**; `payout.*` is one. Non-payout X families present: `transaction.*`, `fund_account.validation.*`, `payout_link.*`, `banking_accounts.issued`, `virtual_account.*`, `settlement.processed`, `bill_payment.*`; no `card.*`, `payroll.*`, `tax.*` families exist (`stork/internal/webhook/event_constants.go`).
8. **Code gap found**: `api/app/Models/PayoutSource/Entity.php:47-56 $validSourceTypes` rejects 5 source types that `Factory.php` still dispatches on and that have live prod SNS topics (findings/02 §15). Recorded as an unknown/anomaly for the owners, not fixed.
9. **Deployment truth**: the RazorpayX production cell `kube-manifests/cells/rzpx/prod/rzpx-inrx-pd01/` (9 CDE + 33 NCDE apps) and the payroll cell `rzpx-inpr-pd01` give the canonical deployed-service list (findings/04 §2.6, 37 rows).

Patch numbers (`GRAPH_PATCH_VALIDATION.json`): 150 new nodes, 129 new edges (73 confirmed, 46 probable, 2 inferred, 8 unknown), 3 relabels, 0 dangling, 0 id clashes. New node types: `business domain`, `business journey`. Expanded graph: `PAYOUTS_SERVICE_GRAPH.expanded.json` (base 197/229 → 347/358).

---

## 3. Ranking

Scores 1–5 (5 best for the programme). Criteria from the task.

| Domain | Connection strength | Business importance | New identities/states | Source availability | Local build feasibility | Independence from unavailable systems | Effort (5 = low) | Total |
|---|---|---|---|---|---|---|---|---|
| **S2P (Vendor + Tax/TDS)** | 5 | 4 | 5 (vendor, invoice, PO, GRN, TDS category, tax payment, vendor portal user, control policy) | 5 | 4 | 3 (ICICI rail, OCR/GST, tax-compliance stubbable) | 3 | **29** |
| **Payroll** | 4 | 5 | 4 (employee, payroll batch, compliance) | 1 | 2 (sim only) | 1 | 4 (sim) / 1 (real) | **21 / 18** |
| Capital (incl. cards) | 5 | 4 | 4 (cardholder, program, LOC account) | 1 | 1 | 1 | 1 | 17 |
| CA onboarding | 3 | 3 | 3 (business_id, application, signatory) | 3 | 3 | 2 | 3 | 20 |
| Settlements→X | 4 | 2 (for X) | 2 | 1 | 2 | 1 | 2 | 14 |
| Accounting/reporting | 3 | 2 | 2 | 4 | 4 | 3 | 4 | 22 (but thin alone) |

---

## 4. Recommendation

**Primary: Source-to-Pay apps — Vendor Payments + Tax Payments/TDS (with accounting-integrations as its downstream leg).**
Reasoning: it is the only candidate that is simultaneously strongly connected (4 source types, allow-listed app, contact-type gate, 4 workflow types), fully source-available (3 cloned Go repos + all protos), and buildable against the *existing* arena, because its payout leg lands on monolith routes the monolith substitute already implements and its approvals land on the M5 workflow engine. It introduces the most new identities and states, and it exercises internal-app authorization and idempotency from a second origin, which directly serves the assurance goal.

**Secondary: Payroll (Opfin) as a protocol-faithful external-caller twin ("opfin-sim").**
Reasoning: highest business importance and the largest adjacent deployed footprint, with a small, fully specified X-facing contract (11 routes, 1 status callback, 1 TPV callback, 1 feature flag, 1 FTS MID list) and a distinct control state (maker-checker bypass) worth modelling now. Real replication is blocked (no source), so the access request (Tier 2) runs in parallel.

Not recommended now: Capital/cards (no source, separate BU, 2-hop connection), Settlements (PG-owned), CA onboarding (mostly extends an existing node; key services 404).

---

## 5. Required repositories and missing access

See `ACCESS_MANIFEST.md` / `.csv` (24 rows across 3 tiers + artifacts). Probe result today: of 48 inferred names only `shield`, `x-attendance`, `x-payroll-commons` resolve; the two payroll repos contain only a "template" README. The rpc repo clone failed earlier on SSL; stubs can be regenerated from `proto/` with `rpc/buf.gen.twirp.yaml`.

---

## 6. Tentative replication workflow (primary)

`REPLICATION_WORKFLOW_S2P.md`: repos to ingest (3 real + contracts), build order (contracts → substitutes → data plane → vendor-payments + 11 workers → vendor-experience → accounting → SNS/Kafka wiring → optional frontend), service graph, minimum synthetic data, 10 critical journeys as acceptance gates, 9 external replacements, contract tests, clean-boot test via the existing `local-acceptance.py` pattern, and an optional 1-day bootstrap (`vp-caller-sim`) that validates the monolith-stub → payouts → contact-type-gate seam. Estimated ~2.5 engineer-weeks without new access.

---

## 7. Snapshot and refresh plan

`SNAPSHOT_REFRESH_PLAN.md`: daily inputs (pinned SHAs of 8 repos, contract files, monolith and payouts seam files, config-proto flags, workflow types, kube/kong topology, alert rules, access probe, org listing, graph validation) and an idempotent diff → classify → act → gate → record loop; payroll seam inputs listed separately.

---

## 8. Important uncertainties (facts vs assumptions)

| # | Item | Status |
|---|---|---|
| U1 | Producer of Kafka `add-tds-entry` (monolith `TdsProcessor` is the only producer-shaped code) | UNK |
| U2 | Producer of SQS `prod-tax-compliance-payout-events` (payouts or monolith?) | UNK |
| U3 | Whether `tax-payments` is still a separate service or fully folded into vendor-payments (Kong routes target the monolith; proto package `vendorpayments.taxpayments`) | INF folded |
| U4 | Whether Opfin also calls the Payouts Service directly (only monolith `payouts_internal` evidenced) | UNK |
| U5 | Which of the two settlement→payout paths (public API via internal X merchant vs `payouts_internal`) is live for which flow | UNK |
| U6 | Whether `capital-cards` writes to ledger (fund_account_type `m2p` exists; no client evidence) | UNK |
| U7 | Consumer of `prod-ledger-x-journal-created-live` SNS | UNK |
| U8 | Prod hostnames for most `applications.*` clients (bare `env()` without defaults in the monolith) | UNK |
| U9 | Ownership of `banking-bridge` / `banking-methods` (deployed in the X cell, CODEOWNERS say tech-banking) | UNK |
| U10 | Whether `x-attendance` / `x-payroll-commons` are placeholders or mis-listed | UNK |
| U11 | `$validSourceTypes` vs `Factory.php` mismatch (5 source types) — bug or intentional | UNK |
| U12 | Role→permission mapping lives in the AuthZ DB, not CSVs | assumption: CSVs are representative |
| A1 | Assumption: the arena's monolith substitute can be extended for tax-payments proxy + tag-back without changing core payouts source | design assumption |
| A2 | Assumption: vendor-experience can start as an F2 stub (Cadence deferred) | design assumption |

---

## 9. Files

| File | Content |
|---|---|
| `reports/expansion/EXPANSION_REPORT.md` | this report |
| `reports/expansion/DOMAIN_CARDS.md` | 6 source-backed domain cards |
| `reports/expansion/CROSS_DOMAIN_PATHS.md` | 8 file-cited hop-by-hop paths (A–H) |
| `reports/expansion/ACCESS_MANIFEST.md`, `ACCESS_MANIFEST.csv` | exact access requests (repos, artifacts, owners, journeys, replacements) |
| `reports/expansion/REPLICATION_WORKFLOW_S2P.md` | primary-domain replication design |
| `reports/expansion/SNAPSHOT_REFRESH_PLAN.md` | daily snapshot inputs + refresh loop |
| `reports/expansion/GRAPH_PATCH.json` | evidence-backed patch (nodes, edges, relabels, unknown edges) |
| `reports/expansion/PAYOUTS_SERVICE_GRAPH.expanded.json` | base graph + patch applied (derived) |
| `reports/expansion/GRAPH_PATCH_VALIDATION.json` | integrity check output |
| `reports/expansion/scripts/build_graph_patch.py` | generator/validator (rerunnable) |
| `reports/expansion/findings/01..08_*.md`, `parts06/` | agent evidence reports; `gh_repo_listing_2026-09-07.txt`; `00_agent_brief.md` |
