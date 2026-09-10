# M5 execution — as built and run

Branch: `milestone-5-finish-autonomous-discovery` (base: tag
`assurance-m4.1-reproducible` = ef4ba72). No remote. Historical tags
`red-loop-m3.1` (3a044f8), `red-loop-m4` (3ae1773), `assurance-m4.1-reproducible`
(ef4ba72) are frozen — never retagged.

This supersedes the earlier planned version of this file. It records what was
actually built and executed.

## What was built

1. **Workflow decision engine** (`ENV2_COMPOSE/substitutes/workflow-engine`) — a
   durable, restart-recoverable, high-fidelity RECONSTRUCTION of the Razorpay
   Workflows/Cadence approval engine (the real repo was unavailable). Orgs,
   actor identities/roles, approval policies, eligible approvers, maker/checker
   separation, N-of-M approvals, cancel/expiry, stale-version, duplicate-request,
   duplicate-approval, terminal-state protection, audit history, idempotent
   retried callbacks. Create + callbacks byte-compatible with real payouts
   routes. Labelled in `FIDELITY.md`; grounded in `m5-source-map.md`.

2. **Business journey** — `RED_LOOP/m5/scenario_suite.py`: 12/12 scenarios pass
   end-to-end over HTTP against live engine + payout-sink processes, including a
   real process restart mid-flow and an approve/reject race.

3. **Blind benchmark** — `RED_LOOP/m5/benchmark/`: fixed reference + 4 physical
   mutants (cross_org, separation, counting, terminal) across 4 invariant
   families, each a single surgical source patch. Worker-facing manifest is
   strictly separated from the control-plane answer key.

4. **Autonomous Director** — `RED_LOOP/m5/campaign/`: model-driven workers
   establish valid baselines, form falsifiable hypotheses, run bounded
   experiments, dedup hypotheses + experiment signatures, learn from validation
   errors, reallocate strategy, recover an injected worker interruption, and
   raise model-originated candidates to a hidden verifier.

## What was run

A full campaign over all 5 environments × 3 models (kimi-k3 primary, gpt-5.5
reproducer, glm-5p2) × 8 rounds. Result: **all 4 hidden defects discovered
across all 4 invariant families, 0 false findings on the fixed reference**;
every finding independently verified (reproduced twice, fresh IDs, negative
control) and cross-model reproduced. Metrics far exceed thresholds (see
`m5-campaign-summary.json`, `m5-acceptance.json`).

## Verdict discipline

A completed platform with no live model-originated verified finding would be
PLATFORM_COMPLETE_DISCOVERY_NOT_PROVEN — never "accepted". That contingency did
not arise: discovery was proven. See `m5-known-limits.md` for honest scope
boundaries (standalone vs full-arena integration; reconstruction fidelity).
