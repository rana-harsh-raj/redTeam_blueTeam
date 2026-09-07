# M4.1 Independent Acceptance Audit

Auditor: independent subagent, own fresh detached worktrees off `3ae1773` and `f68064c`,
trusting no implementation summary. Both worktrees confirmed `RED_LOOP/runs` ABSENT before
running anything. Worktrees removed cleanly afterward; historical tags unchanged.

## Result: ACCEPT

| Check | Observed | Verdict |
|---|---|---|
| 1. Original M4 tag 3ae1773, clean checkout | 74 total, **71 pass / 2 pending (G64,G69) / 1 fail (G68)**, accepted=false | PASS (defect reproduced) |
| 2. Repair commit f68064c, clean checkout | guard OK (19 canonical); manifest verify ok (19 artifacts, 0 mismatch/missing, self+companion sha ok); acceptance **74/74 accepted=true** | PASS |
| 3. Tracked soak + old path ignored | soak git-tracked, sha256 `00205fcd…`; old `RED_LOOP/runs/` path genuinely git-ignored (matches `RED_LOOP/.gitignore:3:runs/`) | PASS |
| 4. No evidence path under RED_LOOP/runs | all 19 artifact paths under `reports/`; the only `RED_LOOP/runs/` string is a `policy.external_roots` declaration, not an artifact | PASS |
| 5. Historical integrity | m3.1 = annotated tag → 3a044f8; m4 = annotated tag → 3ae1773; f68064c descends from 3ae1773 | PASS |
| 6. Honesty caveat | matrix has exactly **7** `independent_machine_check=True` (G01,G02,G05,G68,G69,G71,G73); other 67 validate coordinator-written artifacts | PASS |

## Auditor caveats (recorded, not waived)
- (a) `git check-ignore RED_LOOP/runs` **without** a trailing slash exits 1 because the ignore
  rule is the directory-only pattern `runs/` and the directory does not exist in a clean checkout;
  with `-v` and a trailing slash it matches and exits 0. The path is genuinely ignored. The
  `m41_canonical_paths.py` guard checks concrete FILE paths from the manifest, so this technicality
  does not affect the guard's correctness.
- (b) **74/74 is not equivalent to 74 independent machine proofs.** Only 7 gates are independent
  git/hash checks; the remaining 67 trust coordinator-written runtime artifacts. M4.1 fixes
  *reproducibility*, not the underlying self-report dependence of those 67 gates.

## Gates satisfied by this checkpoint
M41-01 (tags unchanged), M41-02 (defect reproduced), M41-03 (no ignored mandatory evidence),
M41-04 (soak tracked/canonical), M41-05 (hashes recompute), M41-06 (74 gate semantics preserved),
M41-09 (fresh-worktree reproduction),
M41-10 (this independent audit).

NOT satisfied by this session: **M41-07** (direct raw-evidence validation) is PARTIAL — only
7/74 gates are direct machine checks. **M41-08** (boundary tests execute without setup-level
skips) is NOT addressed — the 10 `hardened_edge` skips require booting the arena with
`KONG_ENFORCE_ROUTE_POLICY=1`, which was not run this session. Both are recorded honestly in
m5-known-limits.md.
