# EXECUTIVE_SUMMARY — RazorpayX BB+ Payouts architecture and first executable environment

Date 2026-09-03. Identity used: GitHub `rana-harsh-raj` (member of org, repo-level grants only), Slack user. Method: org-wide discovery, 30 shallow clones, 16 Sonnet investigation lanes, Slack corroboration, one independent completeness audit (see `findings/17`, reconciled below).

## Current architecture in plain language

A payout today enters through Kong at the edge, is routed to the **PHP API monolith** (which authenticates dashboard users, checks roles, and hosts the `payouts_internal` routes used by payout links, vendor payments and fee recovery), and is proxied to the **Go Payouts Service**, which since August 2026 is the only system of record for payout entities (the monolith's payouts table has been deleted; the reverse sync back to the monolith and TiDB is chronically failing). Payouts Service validates input, checks idempotency, evaluates Shield risk and merchant features from DCS, decides whether an approval workflow applies (delegating the approval itself to a separate Workflow Service), selects the source account from x-balances, and then does one of two things depending on the account rail: for shared/virtual balances it posts a `payout_initiated` journal to **Ledger**, whose atomic conditional balance update is the real balance authorization; for direct current accounts it applies a buffer or a newly shipped Redis in-flight reservation against a bank balance that x-balances polls through Mozart. It then creates a transfer in **FTS**, which routes to a bank via **Mozart** and drives its own transfer state machine. The bank outcome comes back to Payouts Service through a synchronous HTTP webhook from FTS, delivered either directly or via the monolith depending on an `X-Origin` header and a Splitz rollout; FTS retries three times over ten minutes and then abandons silently. Payouts Service applies the outcome only if the payout is in an allowed state, posts the mirror ledger journal, and emits merchant webhooks to Stork, statement events to XAS, and status broadcasts to product variants. Cutover of edge routing straight to Payouts Service is fully built (a four-mode shadow gateway plus a Kong template) but switched off in production with a single pilot read route.

## Exact recommended initial scope

Five environments, in this order, all sharing one substrate:
1. **Env 2 — public API payout** (F3: payouts, ledger, fts, cfa, x-balances; F2: Kong-lite, ~15 monolith endpoints, DCS, Splitz, Shield, pricing, Stork capture, Mozart simulator, ASV). Reaches END_TO_END_CONFIRMED for flow B.
2. **Env 4 — failure, retry, reversal, async status** (adds XAS real, scripted Mozart scenarios, monolith relay + admin contract, Kafka path). Targets the dominant production incident.
3. **Env 1 — dashboard + approval** (adds x SPA, Workflow Service substitute, AuthZ bundle). Capped at BLOCKED_BY_FIDELITY_GAP for authentication/roles until `razorpay/api` and `authz` are readable.
4. **Env 3 — migration/proxy cutover** (shadow gateway on, Kong template semantics).
5. **Env 5 — bulk** (adds xperience real, Batch substitute).

P0 repositories: `payouts`, `ledger`, `fts`, `cfa`, `x-balances`, plus build-time `charge-collections-sdk` and the inaccessible shared Go modules. P1: `x`, `xperience`, `x-account-statements`, `banking-accounts`, `ValidX`, `virtual-account`, `admin-dashboard`, `payout-links`, `vendor-payments`, `charge-collections`. Investigated and excluded from execution: `vendor-experience`, `accounting-integrations`, `self-serve-analytics`, `x-bank-statement-sync` (empty), `data-mcp`/`tejas` (empty), `memoir`, `dashboard` (PG), `razorpay-mcp-server`, `relay` (FTS-only config polling).

## Most important access gaps

1. **`razorpay/api`** (monolith): dashboard authentication, role checks, legacy FTS webhook relay, ~25 endpoints Payouts Service still calls (merchant config and pricing are default paths), admin routes, FAV gate location. Without it, Env 1/3 and admin paths cannot exceed CODE_CANDIDATE.
2. **Private Go modules** (`goutils` ×313 imports, `ledger-sdk`, `config-proto`, `rpc`, `proto`, seven others) and buf.build: no F3 service builds without them.
3. **terraform-kong / edge plugins and authz**: identity minting, route cutover state, role policies.
4. **DCS and Splitz values** for synthetic merchants: approval applicability, FTS path, Kafka vs HTTP, idempotency enforcement all branch on them.
5. **Mozart contract / ITF mock-gateway fixtures** and **kube-manifests/spinacode** (cron cadence, env values).
Request path per Slack evidence: #tech_it bot for repo reads; #gandalf for Kafka; DCS via admin-dashboard update-requests; Spine-Edge channel for Kong/authz exports.

## Top architecture uncertainties

- Which merchants take the direct FTS→PS webhook versus the monolith relay, and the relay's retry semantics (unreadable). This is the mechanism behind stuck `initiated` payouts, for which Payouts Service has no polling, no monitor and no repair action.
- Whether the 30-minute merchant-config cache with a no-op refresh path is the cause of the recurring approval-bypass incidents.
- Where fund-account-validation freshness is enforced (not in any cloned repo) and whether admin permissions are enforced server-side.
- Live Splitz variants for eight experiments that select code paths, and production cron cadence.
- Ledger's dedupe and ordering invariants are application-level only.

## Independent completeness audit (reconciled)

Two fresh auditors re-derived reverse dependencies, event paths, cron/admin surfaces and tiering (`findings/17a`) and spot-checked 20 load-bearing claims against source (`findings/17b`). Result: no missing repositories or non-repository systems; no re-tiering; 19/20 claims confirmed (ValidX also exposes `/v2/internal/validations`). Five corrections were applied: Payouts Service admin routes `merchant-configuration`, `payouts_sync`, `free_payout` and FTS channel-health manual overrides added as controls C40–C41 and graph nodes; a still-firing monolith job `ApprovedPayoutProcessor` that fails against the deleted `api.payouts` table added as C42 and a required substitute behaviour; an `accounting-integrations → monolith payouts_internal` read edge added. Auditor confidence in the final closure: 88/100, discounted mainly for the unreadable monolith.

## Recommended next action

Request read access to `razorpay/api`, `terraform-kong`, `authz` and the shared Go module repos via #tech_it (one message, listing all), and in parallel ask the Payouts team in #x-payouts-reliability for a DCS/Splitz export for three synthetic merchants and the ITF mock-gateway fixtures. While waiting, stand up Env 2 substrate from the in-repo docker-compose files and migrations, and build the Mozart simulator from `fts/cmd/mock-server` plus `error_code.go`, so that flow B can be executed the day the module access arrives.

Report files: `REPOSITORY_ACCESS_MATRIX.csv`, `REPOSITORY_INVENTORY.csv`, `PAYOUTS_ARCHITECTURE.md`, `PAYOUTS_FLOW_CATALOG.md`, `PAYOUTS_SERVICE_GRAPH.{json,mmd,graphml}`, `PAYOUTS_CLOSURE.yaml`, `INITIAL_PAYOUTS_ENVIRONMENT_BOM.md`, `NON_REPOSITORY_ACCESS_MATRIX.csv`, `CONTROL_AND_INVARIANT_CATALOG.md`, `EVIDENCE_INDEX.md`, `UNRESOLVED_QUESTIONS.md` under `/Users/rana.singh/rzp-payouts-architecture/reports/`.
