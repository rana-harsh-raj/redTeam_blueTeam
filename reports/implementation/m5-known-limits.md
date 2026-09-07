# M5 known limits

Honest scope boundaries for "Autonomous discovery on maker-checker workflows".

## Fidelity

- **workflow-engine is a RECONSTRUCTION, not the original Workflows/Cadence
  service.** It reproduces observable maker-checker semantics grounded in real
  payouts source (`m5-source-map.md`), but the real engine's internal storage
  schema, policy DSL, Cadence timer/heartbeat mechanics, and production
  RBAC/permission service are UNKNOWN and were not reconstructed. Findings are
  explicitly labelled as concerning the reconstruction.

## Integration scope

- The maker-checker business journey and the campaign run against the
  **standalone** engine + a `payout-sink` callback receiver. The create and
  approve/reject callback surfaces are byte-compatible with the real payouts
  routes (`m5-source-map.md`), so the engine can be dropped into the arena, but
  a full end-to-end run through the live 66-container arena (payouts → FTS →
  Ledger → x-balances → CFA → BAS, with the real Direct/Shared path, accounting
  and webhook) is **not executed in the standalone campaign**. Reason: the
  running arena is memory-constrained (host ~11.65 GiB, ~67 containers) and the
  standalone benchmark is the honest, reproducible substrate for the blind
  capability test. Wiring the engine into `ENV2_COMPOSE` compose + the
  `PAYOUT_WORKFLOWS` flag is the documented next step.

## Blind benchmark

- Four seeded defects across four invariant families (org-boundary,
  maker/checker, approval-integrity, state-integrity). Real production defects
  may be subtler or cross families; this benchmark measures autonomous discovery
  capability on realistic single-property defects, not the full space.

## Autonomous campaign

- Workers are model calls through the internal LiteLLM gateway. Discovery
  quality depends on the models (primary kimi-k3, reproducer gpt-5.5, plus
  glm-5p2). A defect a model never probes in its round budget will not be found;
  the reported score is the honest observed result, not forced.
- The campaign is bounded (rounds × envs × models). Wall-clock duration is
  reported as measured; it is not padded to a target.

## Current-architecture lane

- The open-ended campaign against the **ordinary unmodified** architecture is
  the fixed-reference engine (and, by extension, the real arena assessed in M4).
  Finding nothing new there is acceptable and is NOT forced. The M4 finding
  F-M4-001 (free_payout IDOR) remains the standing current-architecture result;
  no new current-architecture finding is invented here.

## Not addressed from earlier milestones

- M4.1's residual limits are unchanged and out of M5 scope (M5 did not reopen
  them): 67/74 M4 gates still trust coordinator-written artifacts, and the 10
  hardened-edge boundary skips remain.
