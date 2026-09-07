# Milestone 4.1 + 5 — Execution Plan

Branch: `milestone-5-autonomous-discovery-workflows` (base: M4 evidence line, HEAD 0cb90eb ← tag red-loop-m4 3ae1773).
No remote. Historical tags red-loop-m3.1 (3a044f8) / red-loop-m4 (3ae1773) frozen — never retagged.

## Phase 0 — M4.1 Reproducible Evidence Repair  [IN PROGRESS]
Defect (independently reproduced, M41-02): a clean checkout of red-loop-m4 (3ae1773) scores
**71 pass / 2 pending (G64,G69) / 1 fail (G68)** because G64/G68/G69 keyed off the git-ignored
`RED_LOOP/runs/<campaign>/m4-direct-e2e-soak.json` instead of the tracked canonical copy.
Repair (this branch only, M4 tag untouched):
  1. `latest_soak()` now prefers tracked `reports/implementation/m4-direct-e2e-soak.json`
     (byte-identical sha256 00205fcd… to the accepted live run) — honest relocation, not a weakening.
  2. Evidence manifest: soak moved into COMMITTED_EVIDENCE; EXTERNAL_GLOBS emptied.
  3. New active guard `RED_LOOP/surface/m41_canonical_paths.py` fails if any mandatory evidence
     path is git-ignored / untracked / outside checkout / missing.
Proof target (M41-09): a fresh worktree at the M5 tip reproduces 74/74 with empty RED_LOOP/runs.

## Phases 1–8 — M5 Autonomous Discovery on Maker-Checker Workflows  [PLANNED]
Infra-heavy (≈66-container arena + external LLM gateway, multi-hour campaign). Sequenced after
M4.1 passes clean-checkout reproduction. Honest verdict discipline: a completed platform with no
live model-originated finding is PLATFORM_COMPLETE_DISCOVERY_NOT_PROVEN, never ACCEPTED.

## Verdict discipline
Flagship M5 gates (M5-41..M5-52) are NOT waived if the campaign finds nothing.
