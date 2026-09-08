# Preserved historical Source-to-Pay acceptance (commit b6e4976, 2026-09-08)

Verbatim copies of the concurrent-era result: 18/20 gates, `accepted=false`, with exactly
`02_protected_worktrees_unchanged` and `03_preexisting_containers_unchanged` failing because the
baseline (captured 2026-09-07T21:07Z against the M5-era Payouts worktree at 2613f38) was compared
after the M6 task had legitimately changed that worktree and re-created the arena containers.
These files are never overwritten by the closure run; the closure result is recorded separately.
