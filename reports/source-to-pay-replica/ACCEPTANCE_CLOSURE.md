# Source-to-Pay acceptance closure (M7 Stage A, 2026-09-08)

Two results exist and both are preserved. Neither replaces the other.

| Result | Evaluated commit | Gates | accepted | Record |
|---|---|---|---|---|
| Historical, concurrent-era | `b6e4976f9baebfe770dfa30794f6f61ea08ccb1b` | 18/20 | false | `historical-b6e4976/artifacts/acceptance-18of20.json` sha256 `6deede3e9da9a4eb09ca0390cdb3914f1cacc278eeca2017af53d821e89b480e` (identical to the file committed at b6e4976's worktree; git preserves the commit itself) |
| Closure, isolated | `d87591000f359f1a58fc805c9667d84dc1871d09` | 20/20 | **true** | `DOMAIN_REPLICAS/source_to_pay/artifacts/acceptance.json` (git-ignored by design) sha256 `b3320ca96dfa974a6c0939c457a126969ec234a1a4afa89cde0d4caa2443d7db`, tracked copy `closure-m7/artifacts/acceptance.json`; artifact manifest sha256 `8977e6106afa1f97f1c034495a72a2ccf2478744a28082c5041243688500711d` |

## What changed between the two results

Only the isolation baseline and its capture method; no runtime, fixture, build or verifier input was touched
(gate 12 `same_runtime_inputs` compares every runtime-relevant file of the three runs against the tracked tree).

- The historical baseline (`historical-b6e4976/artifacts/isolation-before.json`) was captured 2026-09-07T21:07Z
  while the Payouts worktree stood at the M5 tag (2613f38) and the M6 task was still running. The M6 task
  legitimately committed to that worktree and re-created every arena container, so
  `02_protected_worktrees_unchanged` and `03_preexisting_containers_unchanged` could only fail. The report of
  that era said so; it is not waived here, it is superseded by a run whose baseline is valid.
- The closure baseline was captured with `DOMAIN_REPLICAS/source_to_pay/scripts/isolation_baseline.py` at
  `2026-09-08T06:54:45.327187+00:00` **before** any closure run, from a clean and idle set of every registered worktree
  except the two checkouts that execute the closure runs (`excluded_executing_worktrees`), and from the running
  containers (97: the 92 running arena containers plus the 5 containers of the earlier
  standalone final verification). It was not recaptured after the runs. Verification after the runs:
  protected worktrees unchanged = True (7 worktrees),
  pre-existing containers unchanged = True (97 containers).

Two earlier closure attempts on this branch are preserved as intermediate records, not as results:

1. Attempt with the preserved historical hash manifests placed directly under `reports/source-to-pay-replica/`:
   the deliverable secret scanner (gitleaks generic-api-key) flagged 211 hex digests in those copies; the
   acceptance never ran. The copies were moved under an `artifacts/` directory, which the scanner classifies
   as machine observations. No secret was involved (`artifacts/secret-scan-summary.json`: 0 findings).
2. Attempt at commit 363949a: 19/20, only `03_preexisting_containers_unchanged` failed
   (`closure-m7/artifacts/attempt1-acceptance-19of20.json`, `attempt1-isolation-after.json`): the capture tool
   had listed all containers including the arena's one-shot `ledger-scheduler`, which had exited by design
   before capture and is therefore never "running" for `scripts/isolation.py`. The tool now records running
   containers only (the historical capture's semantics) and a fresh baseline was captured before the final
   three runs. The 96 other containers and all worktrees were unchanged in that attempt too.

## Closure runs

| Run | Worktree | Commit | Independent verifier | Fresh final checkout |
|---|---|---|---|---|
| m7-iso-5 | /Users/rana.singh/rzp-s2p-m7-closure | d87591000f | PASS | False |
| m7-iso-6 | /Users/rana.singh/rzp-s2p-m7-closure | d87591000f | PASS | False |
| m7-iso-7 | /Users/rana.singh/rzp-s2p-m7-closure-final | d87591000f | PASS | True |

Runs `m7-iso-1/2/4` (attempt records) remain under `artifacts/clean-runs/` (git-ignored, hash-bound in their
own `run.json`). Every run rebuilt vendor-payments, vendor-experience and accounting-integrations from the pinned
source in a fresh staging root and produced byte-identical named binaries (`acceptance-details.json
binary_reproducibility`).

## Gate-by-gate

| Gate | Historical (b6e4976) | Closure (d87591000f) |
|---|---|---|
| 01_isolated_worktree | PASS | PASS |
| 02_protected_worktrees_unchanged | FAIL | PASS |
| 03_preexisting_containers_unchanged | FAIL | PASS |
| 04_durable_source | PASS | PASS |
| 05_source_verified | PASS | PASS |
| 06_all_three_repositories_build | PASS | PASS |
| 07_mechanical_denominator | PASS | PASS |
| 08_architecture_representation_99_percent | PASS | PASS |
| 09_mandatory_items_represented | PASS | PASS |
| 10_mandatory_runtime_fidelity | PASS | PASS |
| 11_declared_tested_replacements | PASS | PASS |
| 12_three_clean_runs_including_final_checkout | PASS | PASS |
| 13_duplicate_and_repeated_command | PASS | PASS |
| 14_negative_internal_contact | PASS | PASS |
| 15_reset_replay | PASS | PASS |
| 16_artifacts_hash_bound | PASS | PASS |
| 17_synthetic_isolated_runtime | PASS | PASS |
| 18_production_claims_scoped | PASS | PASS |
| 19_clean_final_tree | PASS | PASS |
| 20_reports_runbook_present | PASS | PASS |

## Is the shared Docker daemon sufficient for trustworthy parallel acceptance?

No. Every observation in this closure came from one colima daemon shared with the 93-container Payouts arena
and with any other task on the machine. The isolation gates can only *detect* that pre-existing containers
and worktrees did not change; they cannot prevent a concurrent task from changing them, cannot attribute a
change, and (as the historical 18/20 shows) any legitimate concurrent work makes the run unacceptable rather
than merely unsafe. Memory is also shared: a 12.5 GiB VM held the arena plus up to three S2P projects during
these runs. For simultaneous tasks use a separate Docker context backed by a separate daemon (a second colima
profile or VM per task, or a remote daemon), one per acceptance run, so that the protected set is empty by
construction and resource limits are per task. A context name alone (`docker context use`) provides nothing
unless it points at a different daemon.

## Tag

`s2p-acceptance-closure-m7` (annotated) on the evidence commit that follows the evaluated commit
`d87591000f359f1a58fc805c9667d84dc1871d09`; the tag message cites both commits and both acceptance hashes.

