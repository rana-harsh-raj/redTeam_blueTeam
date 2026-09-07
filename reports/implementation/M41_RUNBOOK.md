# M4.1 Runbook — Clean-Checkout Reproducible Acceptance

Proves the corrected M4 acceptance reproduces from a completely fresh worktree with NO
git-ignored run directories, NO prior Docker volumes, and NO developer working tree.

## One-command clean reproduction
```
# from any clean checkout of the assurance-m4.1-reproducible tag:
python3 RED_LOOP/surface/m41_canonical_paths.py            # guard: no ignored evidence paths
python3 RED_LOOP/surface/m4_evidence_manifest.py verify     # committed hashes recompute (0 mismatch/missing)
python3 RED_LOOP/surface/m4_acceptance.py --dry-run         # 74/74 accepted=true from the clean checkout
# (do NOT run 'manifest build' here: it only bumps generated_at and churns the tree;
#  use 'make m41-evidence-rebuild' only when the underlying evidence actually changes.)
```

## What was broken (M41-02)
A clean checkout of red-loop-m4 (3ae1773) scored **71 pass / 2 pending (G64,G69) / 1 fail (G68)**:
`latest_soak()` and the evidence manifest globbed the git-ignored
`RED_LOOP/runs/<campaign>/m4-direct-e2e-soak.json`, absent in a fresh checkout.

## What was fixed
- `latest_soak()` prefers the TRACKED `reports/implementation/m4-direct-e2e-soak.json`
  (byte-identical sha256 00205fcd… to the accepted live run).
- Manifest hash-binds the tracked soak; `EXTERNAL_GLOBS = []`.
- `m41_canonical_paths.py` fails if any mandatory evidence path is ignored/untracked/missing.

## Gate semantics
All 74 M4 gate identities preserved. Independent machine checks (git/hash): G01,G02,G05,G68,G69,G71,G73.
The remaining gates validate coordinator-written runtime artifacts; see m41-gate-validation-matrix.csv.
