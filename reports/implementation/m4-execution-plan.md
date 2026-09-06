# Milestone 4 — Direct Current-Account, Reconciliation, and Autonomous Architecture Assurance Expansion

Branch: `milestone-4-direct-reconciliation` (based on accepted M3.1 tag `red-loop-m3.1` → commit `3a044f83ed0eda6ead71585f07c450af2ed7978f`).
Coordinator: Claude Fable 5.1 session (2026-09-06). This file is a living execution plan; every statement below is
either linked to a source symbol / artifact / commit or marked `[TODO]`.

## Environment facts (verified 2026-09-06)
- Host: darwin, 4 CPU exposed to Docker, Docker Desktop memory **12,513,980,416 B (11.65 GiB)** → only ONE 66-container
  arena boots at a time (same constraint recorded for M3.1). Consequence: "isolated execution contexts" (G63) are
  implemented as merchant/artifact/webhook-sink namespaces inside one arena, NOT four concurrent arenas. See m4-decisions.md D-002.
- Live arena `env2_compose` was running (66 containers) at session start; tag `twin-v1.0` freeze commit 219ca48.
- Pristine company clones: path in `.local/repos-root` (includes `payouts`, `fts`, `ledger`, `x-balances`, `api`,
  `x-account-statements`, `x-bank-statement-sync`, `banking-accounts`, `mozart`, `recon`, `cfa`). Admitted build copies in
  `.local/twin-repos/accepted/{cfa,fts,ledger,payouts,x-balances}`.
- Existing Direct modelling in the twin: fixture merchant M2 (`ARENAM00000002`, Direct/RBL, in_flight_reservation_enabled)
  with verifiers V08/V18/V19 and Kafka cases `failed_dropped_direct`, `reversed_dropped_direct` (EF-002/EF-003).
- `xas-sink` is a health-only placeholder (`ENV2_COMPOSE/substitutes/xas-sink/CONTRACT.md`): no BAS/XAS path exists yet.
- Runtime provisioner: `RED_LOOP/red_loop/provisioner.py:provision_funded_merchant` (Shared/pool only, source 900001).

## Phases
| Phase | Scope | Status |
|---|---|---|
| 0 | Baseline preservation & reverification (hash recompute, clean boot, baseline report) | DONE (BASELINE_HOLDS, m4-base-a 26/0/0) |
| 1 | Source-grounded Direct map (payouts/api/fts/x-balances/ledger/XAS/BAS) | DONE (T02-T07,T12; source-map+TWIN_SPEC v2) |
| 2 | Fresh Direct merchant provisioner + self-check | DONE (T09: 2 merchants, 29/29 self-check, 15/15 proofs) |
| 3 | Direct payout + statement + reconciliation + Ledger path (A-G journeys) | in progress (T10 ingestion, T11 xas/DA) |
| 4 | Status-return route matrix | pending |
| 5 | Invariants and boundary tests | in progress (T14) |
| 6 | Hypothesis-lifecycle hardening in RED_LOOP | code DONE (T13, 83 tests); wiring in progress (T15) |
| 7 | Multi-context + soak | in progress (T15) |
| 8 | Evidence, replay, acceptance, tag | pending |

## Workstreams (subagent IDs in m4-task-ledger.jsonl)
See ledger. Handoffs land in `reports/implementation/m4-subagent-handoffs/<task-id>.md`.
