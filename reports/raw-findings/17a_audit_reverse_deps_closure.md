# Independent completeness audit — Payouts closure & graph (2026-09-03)

Method: own greps across all clones (excl. `_test.go`, vendor, node_modules) for 12 reverse-dep
terms; GitHub org-wide code search (8 queries); grep for shared-DB/CDC/outbox terms; grep for
cron/admin/manual/force/fix route files in payouts, fts, ledger, x-balances, xas, cfa; read of
`PAYOUTS_CLOSURE.yaml`, `CONTROL_AND_INVARIANT_CATALOG.md`, `REPOSITORY_INVENTORY.csv`,
`PAYOUTS_SERVICE_GRAPH.json`; 4 Slack searches (`payouts` + incident/stuck/RCA/outage,
after:2026-08-15).

## 1. Missing repositories

None. GitHub org-wide search for 8 payout-internal-specific terms (`transfer_status_webhook`,
`payouts_internal`, `rx-fts-status-update-events`, `x_balances_balance_refresh`,
`payouts_service/dual_write`, `update_fts_fund_transfer`, `PAYOUTS_SERVICE_AUTH`,
`payout_service_enabled`) returned only repos already present in `REPOSITORY_INVENTORY.csv`
(payouts, fts, agent-skills, accounting-integrations, payout-links, vendor-payments,
x-account-statements, xperience, x). No org-wide hits pointed at an uncloned/unlisted repo.
`x_balances_balance_refresh` term itself only appears inside `payouts` (not inside `x-balances`,
which likely names it differently) — naming mismatch, not a missing repo.

## 2. Missing services / components

- **API monolith `ApprovedPayoutProcessor` background job** (legacy write path): a Slack thread
  in `#potential_outages` (2026-09-03, Preetham M R) shows a background job in the API monolith
  still executing `UPDATE payouts SET status='rejected'` against the monolith's own local
  `payouts` table and **failing live in production** with `Table 'api.payouts' doesn't exist`
  (the table was deleted Aug-2026, as `db:api-mysql`'s node label already notes). The closure's
  `db:api-mysql` note anticipates the deletion but no node/control names this specific still-firing
  legacy mutator. A faithful API-monolith F2 substitute must reproduce this job's *failure* mode
  (silently erroring, not simply omitting the code path), since it is evidence for `closure_rule`'s
  "create or mutate payout state" criterion even though it now always errors.
- **FTS channel-health manual-override surface**: `fts/internal/routing/router/route_list.go`
  exposes `POST /manual_override` (`HealthController.ManualUptimeTriggerOverride`) and
  `POST /fail_fast_status/manual_update` — these directly mutate bank-channel health state that
  feeds routing/downtime (catalog C21, C33) but are not a distinct node in the graph or a row in
  `CONTROL_AND_INVARIANT_CATALOG.md`; currently folded anonymously into the generic `svc:fts` node.

## 3. Missing event paths / edges

- `svc:accounting-integrations` is **not connected as a caller** anywhere in the graph. Code
  (`accounting-integrations/internal/service/payout/core.go`, confirmed against
  `core_test.go` fixtures hitting `api-web.dev.razorpay.in`) shows it makes live HTTP GETs to
  `v1/payouts_internal?account_number=...` and `v1/payouts_internal/{id}` on the API monolith
  (`GetLastNDaysPayouts`, `GetPayoutWithExpandParams`). The graph only has
  `svc:vendor-payments -> svc:accounting-integrations` (Kafka topic consumption); the missing edge
  is `svc:accounting-integrations -> svc:api-monolith` (read-only pull, not a mutation — doesn't
  change its F0 tier, but the graph is incomplete).
- `svc:x-account-statements -> svc:payouts_internal (source_event_info)` HTTP call
  (`x-account-statements/internal/gateway/api/service/service.go`, `PayoutEventFetchURI`) was
  checked and resolves to the same `BaseURL` (`prod-api-int.razorpay.com` in prod) already covered
  by the existing `svc:xas -> svc:api-monolith` edge — **not** a gap, self-corrected after tracing
  the config.
- `x-balances` payout-events consumer (`internal/job/payout_events/payout_events.go`) matches the
  existing `q:sqs-xbalances-refresh -> svc:x-balances`-style edges; no new gap found there.

## 4. Missing non-repository systems

None found beyond the inventory's already-flagged NOT ACCESSIBLE list. Slack sampling turned up
only systems/flags already represented: `fts_request_from_payouts_service` experiment (matches
`flag:splitz:fts_request_from_ps`), `is_payout_service` / `payout_service_enabled` merchant flag
(matches `flag:merchant:payout_service_enabled`), and XPayroll (already present only implicitly,
as one of the named producers in `q:sns-source-updates`'s label — not a dedicated node, but not a
service Payouts calls into either, so not a graph gap). One new merchant flag surfaced —
`block_credit_self_serve` (settlements auto-pay bypass, `#fin_ops_banking`) — but it governs
Settlements (already `svc:settlements | NOT ACCESSIBLE`, out of Payouts' closure), not Payouts
itself; not adding as a gap.

## 5. Cron / admin coverage table

| Route | Repo | Mutates | In graph (`ep:`)? | In `CONTROL_AND_INVARIANT_CATALOG.md`? |
|---|---|---|---|---|
| `POST /v1/admin/payouts_sync` (`PayoutDetailsSync`) | payouts | payout state | No | No — only generically under C37 |
| `POST /v1/admin/free_payout/:balance_id` | payouts | free-payout attribute state | No | No |
| `POST/PUT /v1/admin/merchant-configuration` | payouts | merchant behavior config | No | No — closure_rule explicitly includes "change behaviour through configuration" |
| `POST /v1/admin/internal_actions/query_db` | payouts | none (SELECT-only) | No | Yes (C37 names PS `query_db SELECT-only`) |
| `POST /v1/dev_admin/banking_account_statement/link` | payouts | BAS statement link (dev-gated) | No | No (low materiality — dev-only) |
| `POST /v1/manual_override`, `/fail_fast_status/manual_update` | fts | bank channel health/routing | No | No — folded into C21/C33 generically |
| `/v1/manual_queries`, `/v1/cron_jobs_dashboard` | fts | cron visibility/manual query | No | No |
| `ep:cron-*`, `ep:admin-force-status`, `ep:manual_action`, `ep:workflow-state`, `ep:payouts_internal-approve`, `ep:bas-recon-payout-update` | payouts | payout/transfer/ledger state | Yes | Yes (C12, C17, C24, C33, C37, C39) |

Verdict: the already-catalogued cron/admin surface (queued/scheduled/on-hold/reservation crons,
manual_action, admin-force-status, workflow-state, bas-recon) is well represented. The gap is a
**second, PS-native admin surface** (`/v1/admin/*`) that is distinct from the monolith-fronted
admin-dashboard flow the catalog documents under C37 — most materially
`/v1/admin/merchant-configuration`, a direct config-mutation route matching the closure_rule
inclusion criterion, absent from both the graph and the control catalog.

## 6. Closure tiering verdicts

| Component | Tier | Verdict | Reason | Evidence |
|---|---|---|---|---|
| Payouts Service | F3 | Keep | Confirmed system of record; owns cron/admin/webhook mutation surface directly (§5) | `payouts/internal/routing/router/*.go` |
| Ledger | F3 | Keep | Synchronous atomic balance authorization; only source of ledger state | `CONTROL_AND_INVARIANT_CATALOG.md` C15/C18/C19 |
| FTS | F3 | Keep | Transfer FSM + bank routing + health/downtime mutation (§2 adds new surface, same tier) | route_list.go |
| CFA | F3 | Keep | Beneficiary system of record, merchant-scoped | closure evidence 05 |
| x-balances | F3 | Keep | Source-account selection + reservation release | closure evidence 06 |
| banking-accounts | F2 | Keep | Read-only linkage/creds in payout path; onboarding out of scope | unchanged |
| x dashboard SPA | F3 | Keep | Merchant entrypoint, client-side gating, e2e coverage exists | unchanged |
| Xperience | F0 (env1) | Keep | Not on single-payout path (bulk/petty-cash only); its `payouts_internal` call (`fetch_payout_by_id.go`) is a read used for bulk-row status, not a create/authorize path | grep confirms only read routes |
| XAS | F2 | Keep | Both its `payouts_internal` HTTP calls resolve to the same monolith host as the existing edge; no new mutation path found | §3 |
| ValidX | F0 (env1) | Keep | Confirmed no FAV-freshness gate calls into it from PS/FTS in any cloned repo; called by monolith only | grep + prior finding 15 |
| API monolith | F2 (high-fidelity) | Keep, strengthen substitute spec | Must additionally simulate `ApprovedPayoutProcessor`'s failing legacy write (§2) | Slack 2026-09-03 |
| Edge/Kong, AuthZ, Workflow Service, DCS, Splitz, Shield, Governor+CC, Stork, Mozart, ASV | F2 | Keep | No contradicting evidence found | — |
| Batch service | F0 (env1) | Keep | Bulk-only | unchanged |
| Admin dashboard + monolith admin routes | F0 (env1) | Keep, but scope note | The newly found `/v1/admin/*` PS-native routes are a *separate* privileged surface from what this component covers; they're already inside Payouts Service's F3 tier, so no re-tier needed — but they should be added to C37 and to admin-dashboard's `dependencies` since they are plausible admin-dashboard call targets | §5 |
| Recon/ART | F0 (env1) | Keep | Post-hoc only | unchanged |
| payout-links / vendor-payments / vendor-experience / CC fee-recovery / frontend-x | F0 (env1) | Keep | Distinct variant closures confirmed by code (all resolve to `svc:api-monolith`, not direct PS) | §1 grep |
| virtual-account | F0 (env1) | Keep | VA-only path, env1 is Shared+Direct only | unchanged |
| Observers (accounting-integrations, self-serve-analytics, x-bank-statement-sync, data-mcp, tejas, memoir, razorpay-mcp-server, dashboard PG) | F0 | Keep | accounting-integrations' HTTP pull (§3) is read-only — doesn't cross into mutation criteria despite being an undocumented edge | core.go read-only methods |
| Relay | F1 | Keep | Only FTS polls it behind a flag; no payout logic found | unchanged |
| Shared Go libraries | F3 (build prereq) | Keep, strictly needed | Every F3 service imports passport/authz/splitz/dcs/ledger clients from them; cannot build without | go.mod scan (unchanged) |
| Infrastructure (MySQL/PG/Mongo/Redis/SQS/SNS/Kafka/ES/TiDB) | F3 | Keep, strictly needed | Schemas/queues are the actual state; no substitute possible | unchanged |

All F3 components reviewed are strictly needed — each is either the sole system of record for a
piece of state the `closure_rule` requires (authorize/persist/reverse/route) or a build
prerequisite with no substitute. No F3 downgrade candidates found. No F0/F1 component was found to
secretly influence Env 1 or Env 2 flows; the two new findings (§2, §5) are **additions to** F2/F3
components already in the closure, not new material components requiring their own tier.

## 7. Confidence in closure: 88/100

Reasoning: the core F3 tiering (Payouts, Ledger, FTS, CFA, x-balances, x SPA) is strongly
evidenced by direct route/controller code across all 6 repos and holds up under independent
re-derivation. GitHub org-wide search found zero repos outside the inventory. Four Slack incident
searches spanning Aug 15–Sep 3 2026 surfaced only services/flags/experiments already represented
in the graph (`fts_request_from_payouts_service`, `is_payout_service`, FTS↔Payouts sync gaps,
Ledger auth) — no unknown service caused an incident in this window. Deductions: (a) a second,
PS-native admin-mutation surface (`/v1/admin/merchant-configuration`, `/v1/admin/payouts_sync`,
`/v1/admin/free_payout`) is absent from both the graph and the control catalog despite matching
the closure_rule's own inclusion criteria (§5); (b) a live, still-firing legacy monolith write
path (`ApprovedPayoutProcessor` against the deleted `api.payouts` table) needs to be named
explicitly so the API-monolith F2 substitute reproduces its (broken) behavior rather than silently
omitting it (§2); (c) one graph edge (`accounting-integrations -> api-monolith`) is missing,
though it's read-only and doesn't change any tier.
