# Milestone 3.1 — partial defensive engineering result

Status: **NOT READY. Requested calibration deliverables remain incomplete.**

Branch: `milestone-3-1-calibration`.
Implementation commit: `d20adb41ff7f8f0f6726b471268987ef488b84f0`.
The subsequent evidence commit contains this report and the generated acceptance
manifest; the manifest's HEAD identifies the implementation it assesses.

Starting state was clean on `milestone-3-1-clean-parity`, HEAD
`0dd9b3292df6bbaef8b9e25a068bbd3c9ecb7d13`. Historical references preserved:

- `twin-v1.0` annotated tag object: `d3f9a16e26aee401c377fa299b1b0a49fa9a012b`;
  peeled commit: `78def24eb57112c0dc39a6ae9062b1f0d1c711fb`.
- Runtime freeze reference: `219ca48bbb5e51db9347b335ac0a50241656045b` (historical manifest).
- M2: `a107612ed11344a8096e74dbf68616679855465e`.
- M3: `3873e96ff9b8d510059bbccb9ab3553bf5c5c671`.

## Verified implementation and bounded changes

The starting provisioner contains the bounded pricing-ID fix. The checked-in
schema declares `payouts.pricing_rule_id CHAR(14)`. The substitute returns the plan
ID when no rule-level ID exists. Direct character counts confirm the old example
`ARENAPLAN962226` has 15 characters and `ARENAPLANC456A` has 14. Historical commit
`39fe41e` changed runtime provisioning and reports, not core repository source.
The historical live SQL diagnosis was not independently rerun in this task.

Added persistent, run-scoped pricing reservations with cross-run ownership checks,
existing-seed collision checks, locking, atomic replacement, and pre-seeding
validation. Collisions fail before secret reads or database inserts. Existing
bounded IDs are preserved. Explicit rule IDs in the pricing seed are also length
validated; generated rules contain no separate IDs. Plan-as-rule identity remains
a declared fidelity limitation. Core service source and `RowsAffected` are unchanged.

Verification performed locally:

- Pricing unit tests: **8 passed**, including real truncated-hash collision input
  (`ARENAM90001408` and `ARENAM90001571`), forced collisions within/across runs,
  persistence, invalid lengths, corrupt registry, and failure before DB access.
- Existing merchant gateway route-policy unit tests: **9 passed**.
- Acceptance generator check: five missing-evidence gates remain pending and
  `accepted` is false. Generator exit code 1 is the expected unmet-gate result.
- `git diff --check`: passed.

## Evidence and remaining scope

Historical `m31-fresh-merchant.json` has eight individually passing checks,
including processed success, Shared reversal, insufficient balance, and
idempotency. It is **historical 8/8 evidence, not a post-change runtime result**.
The existing runner hard-codes the shared live arena; it was not run against that
current state and relabeled as clean evidence. A disposable-arena rerun and fresh
datastore ownership verification remain outstanding.

The disclosure regression and semantic replay workflow was declined and was not
implemented or executed. Consequently there is no new fixture, fresh-ID proof,
victim-resource disclosure proof, paired denial proof, or deterministic replay
result. No calibration isolation boundary was deployed. The ordinary twin's
authorization code is unchanged and its route-policy unit tests pass; runtime
tenant isolation has not been re-proven here.

The generated manifest now distinguishes:

| Gate | Status |
| --- | --- |
| `fresh_id_calibration_fixture` | pending |
| `fresh_id_deterministic_replay` | pending |
| `fresh_id_cross_provider_replay` | pending |
| `fresh_id_negative_control` | pending |
| `second_open_campaign` | pending |

All other generated passes refer to retained historical artifacts. No model was
invoked. No second open campaign was run. The milestone is not tagged because its
required evidence is incomplete; no `red-loop-m3.1` tag was created.

## Safety and cleanup

No production, staging, DevStack, Daytona, external service, or company clone was
modified or accessed. No public-internet requests were made. Docker use was a
read-only container-name inventory. No calibration containers, networks, volumes,
credentials, or capture resources were created, and no live arena resources were
removed. Unit-test temporary directories were cleaned automatically. No runtime
calibration tests occurred, so there is no same-window runtime egress claim.
Existing evidence directories and lifecycle artifacts were preserved.
