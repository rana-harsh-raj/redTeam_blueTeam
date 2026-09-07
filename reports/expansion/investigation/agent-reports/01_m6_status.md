# 01 — State of the full-Payouts (M6) expansion

Audit performed READ-ONLY on 2026-09-08 against
`/Users/rana.singh/rzp-payouts-architecture`, branch
`milestone-6-complete-payouts-domain`, HEAD `2613f383297c7c3b5c0831c26c5c7472494b6e7b`.

`git rev-parse 'assurance-m5-autonomous-discovery^{commit}'` →
`2613f383297c7c3b5c0831c26c5c7472494b6e7b` — **VERIFIED: HEAD == the M5 tag. Not one
line of M6 is committed.**

> **Live-session warning.** `ps aux` shows PID 62125
> `python3 RED_LOOP/m6/journeys/run.py` started 01:30AM and still running, and
> `RED_LOOP/m6/journeys/framework.py` / `run.py` / the run dir
> `RED_LOOP/runs/m6-journeys-20260907T200039Z` have mtimes of 01:52–01:53. The M6
> work is **in flight right now**; every number below is a snapshot of a moving target.

---

## 1. Git state (VERIFIED — raw command output)

`git log --oneline -3`
```
2613f38 M5 evidence: autonomous discovery proven — 4/4 hidden defects, 0 false on fixed
e8e0699 M5: autonomous discovery Director + campaign runner + acceptance evaluator
1a602bc M5: workflow decision engine (reconstruction) + 12-scenario suite + blind benchmark
```

`git stash list` → **empty** (no output).

`git diff HEAD --stat` → `20 files changed, 2139 insertions(+), 39 deletions(-)`.
`git diff --cached --stat` → 19 files, 2112 insertions (staged).
`git diff --stat` (unstaged) → `Makefile | 27 +++` only.

### 1.1 `git status --porcelain --untracked-files=all` — classified

**Staged modifications (`M `) — 11 files, all CONFIG/RUNTIME:**

| path | class |
|---|---|
| `ENV2_COMPOSE/.env.arena` | config (+25 lines) |
| `ENV2_COMPOSE/config/arena.yaml` | config (+12) |
| `ENV2_COMPOSE/config/generate.py` | script/config-renderer (+20/−) |
| `ENV2_COMPOSE/config/templates/base/fts/env.arena.toml` | config (48 ±) |
| `ENV2_COMPOSE/config/templates/base/payouts/arena.toml` | config (9 ±) |
| `ENV2_COMPOSE/docker-compose.yml` | config (+511) |
| `ENV2_COMPOSE/preflight/preflight.py` | script (safety-report string) |
| `ENV2_COMPOSE/scripts/up.sh` | script (+29) |
| `ENV2_COMPOSE/secrets/gen-secrets.sh` | script (+7) |
| `ENV2_COMPOSE/secrets/materialize.py` | script (+25) |
| `ENV2_COMPOSE/substitutes/workflow-engine/wfengine/{callbacks,identity,server}.py` | **substitute** (+336 net) |

**Staged additions (`A `) — 5 files:**
`ENV2_COMPOSE/M6_RUNTIME_CHANGES.md` (report, 501 lines);
`ENV2_COMPOSE/substitutes/workflow-engine/{.dockerignore,ARENA.md,Dockerfile,arena_entrypoint.py}` (substitute + report);
`reports/domain/SCHEMA.md` (report — the **only** tracked file in `reports/domain/`, `git ls-files reports/domain` returns exactly this one path).

**Unstaged (` M`) — 1 file:** `Makefile` (script; adds 7 `m6-*` snapshot/refresh targets, **no `m6-acceptance` target**).

**Untracked (`??`) — 176 paths.** Classification:

| group | count | class |
|---|---|---|
| `ENV2_COMPOSE/substitutes/batch-sim/*` (CONTRACT.md, Dockerfile, server.py, test_batch_sim.py, compose-block.yml, arena-yaml-snippet.txt) | 6 | **substitute** (+ its contract report) |
| `RED_LOOP/m6/clean_boot.py`, `RED_LOOP/m6/journeys/*.py` (framework, run, bulk_client, 12 `j_*.py`) | 16 | script (harness) |
| `RED_LOOP/surface/m6_acceptance.py` | 1 | script (evaluator) |
| `scripts/domain/*.py` (build_graph, inventory, journeys_part, runtime_overlay) | 4 | script |
| `scripts/snapshot/*` (capture, diff, affected, recipes, refresh, common, README, tests/) | 9 | script |
| `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.{json,mmd,graphml,nodes.csv,edges.csv}`, `GRAPH_STATS.json`, `FIDELITY_CLASSIFICATION.csv`, `REPOSITORY_INVENTORY_M6.csv`, `SNAPSHOT_MANIFEST.json`, `README.md`, `REMAINING_ACCESS_MANIFEST.md` | 11 | **evidence/report** |
| `reports/domain/parts/*.{json,md}` (7 lanes) | 12 | evidence |
| `reports/domain/recipes/*.yaml` | 100 | evidence |
| `reports/domain/snapshots/*.json` (6) + `refresh/*.json` (4) | 10 | evidence |
| `reports/implementation/m6-journey-coverage.md`, `m6-journeys.json` | 2 | **evidence/report** |

**INFERRED:** nothing M6 has been committed; the entire milestone lives in the
index + working tree of one uncommitted change set.

---

## 2. M6 plan / report / acceptance artefacts

### 2.1 Documents that exist

| path | tracked? | what it is |
|---|---|---|
| `ENV2_COMPOSE/M6_RUNTIME_CHANGES.md` | staged-add | Lane **I3** runtime-construction report (501 lines). Headline: *"**Nothing here was booted.**"* §1 |
| `ENV2_COMPOSE/substitutes/workflow-engine/ARENA.md` | staged-add | operator doc for the workflow-engine substitute (330 lines) |
| `ENV2_COMPOSE/substitutes/batch-sim/CONTRACT.md` | untracked | lane **I2** batch-sim contract; explicitly *"NOT the razorpay/batch code"* |
| `reports/domain/README.md`, `SCHEMA.md` | untracked / staged | lane graph docs |
| `reports/domain/REMAINING_ACCESS_MANIFEST.md` | untracked | remaining-access items |
| `reports/implementation/m6-journey-coverage.md` + `m6-journeys.json` | untracked | lane **I4** journey results |
| `scripts/snapshot/README.md` | untracked | lane snapshot/refresh runbook |

**There is no overall M6 plan document, no M6 final report, and no M6 acceptance
artefact.** `ls reports/implementation/m6-acceptance.json m6-clean-boot.json
m6-refresh-demo.json` → all three **No such file or directory** (VERIFIED).

### 2.2 The evaluator: `RED_LOOP/surface/m6_acceptance.py` (untracked, 166 lines)

It defines **15 gates** and writes `reports/implementation/m6-acceptance.json`.
`accepted = (passed == total)`.

Gate-by-gate `validation_mode` as authored (VERIFIED by reading the source):
13 gates are `"direct_artifact"` (computed by the script from an artefact on
disk); 2 gates (`M6-11`, `M6-12`) are `"direct_git"` (computed from `git
rev-parse` / `git ls-files`). **Zero gates are hand-authored / self-asserted** —
this matches the M4/M5 pattern in `m4_acceptance.py` (947 lines) and
`m5_acceptance.py` (228 lines).

Caveats on how machine-backed they really are (INFERRED from the predicates):
* `M6-08a` only checks **key presence** in `SNAPSHOT_MANIFEST.json`, not values —
  it passes although `graph_version` is `null`.
* `M6-07/07b/08b` read `m6-clean-boot.json` / `m6-refresh-demo.json`, which are
  **self-reported by the harness that produces them**, not independently recomputed.
* `M6-01`..`M6-06`, `M6-09`, `M6-10` recompute from graph/CSV/JSON artefacts and
  are genuinely derived.

### 2.3 Acceptance result (VERIFIED — I ran the evaluator with `--out` pointed at
the scratchpad, so no repo file was written; full JSON at
`…/scratchpad/inv/m6-acceptance-dryrun.json`, process exit code 1)

```
7/15 gates   accepted=False
```

| gate | mode | result | detail |
|---|---|---|---|
| M6-01 every reachable repo inventoried | direct_artifact | **PASS** | inventory=107, reachable=60, missing=[] |
| M6-01b kube-manifests inventoried | direct_artifact | **PASS** | |
| M6-02 ≥90% critical components represented | direct_artifact | **PASS** | 100.0% of 329 |
| M6-03 no major business family unmapped | direct_artifact | **PASS** | families=42, missing=[] |
| M6-04 every P0 family has ≥1 PASSing journey | direct_artifact | **FAIL** | p0=24, without_pass=**23** |
| M6-05 P0 covers success+failure+idempotency+async_state | direct_artifact | **FAIL** | 23 families missing all four |
| M6-06 P0 workers/crons executable or explicitly blocked | direct_artifact | **FAIL** | p0_workers=70, unresolved (fts hv/rbl workers, x-balances cron, …) |
| M6-07 clean boot from empty state | direct_artifact | **FAIL** | `m6-clean-boot.json` absent |
| M6-07b clean boot free of dev-local state | direct_artifact | **FAIL** | same |
| M6-08a snapshot manifest keys | direct_artifact | **PASS** | key-presence only |
| M6-08b change → affected-only rerun demo | direct_artifact | **FAIL** | `m6-refresh-demo.json` absent |
| M6-09 every node carries a fidelity class | direct_artifact | **PASS** | rows=1995, unlabelled=0 |
| M6-10 remaining-access manifest lists blocked items | direct_artifact | **FAIL** | blocked=2 (`sub:mozart-mock`, `svc:mozart-mock-mode`) absent from the manifest — its §B literally reads "*graph not built yet, or no blocked nodes*" |
| M6-11 on milestone-6 branch or twin-m6 tag | direct_git | **PASS** | branch matches |
| M6-12 graph + evidence tracked in git | direct_git | **FAIL** | `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json` and `reports/implementation/m6-journeys.json` are untracked |

The tag `twin-m6-complete-domain` that `M6-11` looks for **does not exist**
(`git tag --list` → `assurance-m4.1-reproducible, assurance-m5-autonomous-discovery,
red-loop-m3.1, red-loop-m4, twin-v1.0`).

---

## 3. What M6 ADDS over the M5 baseline (`git diff HEAD`)

### 3.1 New compose services — +12 (VERIFIED, `git diff HEAD -- docker-compose.yml | grep '^+  <name>:'`)

**10 payouts workers** (closing 15 → 25 registered jobs; `M6_RUNTIME_CHANGES.md` §3):

| service | `PAYOUTS_WORKER_NAME` | arena SQS queue |
|---|---|---|
| `payouts-worker-bulk-payouts` | `bulk_payouts` | `bulk_payouts` |
| `payouts-worker-batch-submitted-merchants` | `batch_submitted_merchants` | `batch_submitted_merchants` |
| `payouts-worker-data-consistency-checker` | `data_consistency_checker` | `data-consistency-checker` |
| `payouts-worker-data-consistency-event` | `data_consistency_event` | `data-consistency-event` |
| `payouts-worker-fund-management-payout-check` | `fund_management_payout_check` | `fmp-check` |
| `payouts-worker-fund-management-payout-initiate` | `fund_management_payout_initiate` | `fmp-initiate` |
| `payouts-worker-payout-usage-event-processing` | `payout_usage_event_processing` | `payout_usage_event_processing` |
| `payouts-worker-x-account-statement-source-event` | `x_account_statement_source_event` | `x_account_statement_source_event` |
| `payouts-worker-x-balance-payouts-event` | `x_balance_payouts_event` (singular — registered name; the plural `[job]` key would exit with `ErrorStartingUnregisteredJob`) | `stage-x-balances-payouts-event` |
| `payouts-worker-api-queue-for-async-dual-write-direct-push` | `api_queue_for_async_dual_write_direct_push` | `api-payout-service-dual-write-direct-push-live` |

**2 substitutes:** `workflow-engine` (port 8093, profile `substitutes`) and
`batch-sim` (port 8094).

**3 new named volumes:** `secrets-workflow`, `wfengine-data`, `batch-sim-data`.

### 3.2 New queues / topics / cron endpoints — **none**
`M6_RUNTIME_CHANGES.md` §3: all 25 `[job]` queues already exist in
`seeds/localstack/init-queues.sh` (0 missing, verified programmatically by that
lane); no Kafka topic bootstrap needed (`KAFKA_AUTO_CREATE_TOPICS_ENABLE=true`);
no `up.sh` core-health list change (it derives `CORE_SERVICES` by regex).
Corroborated by `git status`: **no** seed/cron files are modified.

### 3.3 Callback / status-route surface added (workflow-engine, staged diff)
* `wfengine/callbacks.py` (+69): `_target()` now resolves the **real payouts
  callback shape** `callback_details.workflow_callbacks.processed.domain_status.
  {approved,rejected}.{url_path,method,headers,payload}` joined onto `PS_API_URL`,
  with the `WfCallbackPath + entity_id + /approve|/reject` fallback and the legacy
  M5 sink shape third. Success codes `200|201|409`.
* `wfengine/identity.py` (+32): `WFE_WORKFLOW_ID_LEN=14` (payouts stores
  `CHAR(14)`), idempotent `create_org/ensure_org`.
* `wfengine/server.py` (+235): `/health`, `/ping`, `/_arena/health`,
  `/_arena/pending`, `/_arena/decide`, `/_arena/workflows`; `WFE_TENANT_KEY=owner_id`;
  payouts-shaped `CreateHttpResponse`; default-policy auto-provision
  (`WFE_AUTOPROVISION_POLICY=1`, off in the library layer so M5 is bit-for-bit intact).

### 3.4 Config / routing changes
* `.env.arena`: **`ARENA_WORKFLOW_HOST=http://workflow-engine:8093` is now the
  DEFAULT** (was unset → frozen dead address `http://127.0.0.1:1`). This flips the
  whole approval family from dead to live.
* `config/arena.yaml`: `workflow_engine{8093}`, `workflow_sim{8092}`, `batch_sim{8094}`.
* `config/templates/base/payouts/arena.toml`: `[workflow].host = {{WORKFLOW_HOST}}`,
  `timeout = "100ms"` kept (real prod value).
* `config/templates/base/fts/env.arena.toml`: prod-shaped
  `[kafka_producers.fire_transfer_status.conf]` moved into the template — **inert**;
  the FTS→payouts Kafka leg is still governed by `ARENA_ROUTE_PROFILE` and default
  `monolith` leaves it **OFF**.
* `scripts/up.sh`: now loads `.env.arena` into its own shell (compose `--env-file`
  never did), and health-gates `workflow-engine` + `batch-sim`.
* `secrets/`: new `wfe_admin_token`; new `secrets-workflow` group;
  `auth_workflow_payouts` added to `secrets-kong` for batch-sim.

### 3.5 Accounting / reconciliation
No new BAS/recon containers. `payouts-worker-rbl-banking-account-statement` and
`payouts-worker-x-balances-balance-refresh` already existed at M5 and are running.
New recon-adjacent workers are `data-consistency-checker`, `data-consistency-event`,
`x-account-statement-source-event`, `fmp-check`, `fmp-initiate`, `payout-usage-event-processing`.

### 3.6 Three latent blockers found and fixed by lane I3 (M6_RUNTIME_CHANGES.md §6)
1. `auth_workflow_payouts` was never materialized into `secrets-kong` → every
   approve/reject callback would have gone out with an empty password → 401.
2. `workflow-sim` / `workflow-engine` were missing from `NO_PROXY` → Go's
   `ProxyFromEnvironment` would have routed every `WorkflowAPI/Create` to the
   blackhole proxy `http://127.0.0.1:9`.
3. `up.sh` never sourced `.env.arena` → `ARENA_WORKFLOW_HOST` had no effect on
   rendered config.
All three are **fixed in the staged diff but unproven at runtime** (nothing booted).

### 3.7 Unchanged by M6
`ENV2_COMPOSE/generated/` is git-ignored (`ENV2_COMPOSE/.gitignore:7`) — not in
scope. `ARCHITECTURE_EXPLORER/data/{architecture.json,saved-run.json}` are
untouched (mtime 2026-09-05, absent from `git status`). No `TWIN_SPEC/` file is
modified; `TWIN_SPEC/declared-deviations.yaml` **does not exist** (the nearest
equivalents are `expected-failures.yaml` and `reports/fidelity/*`).

---

## 4. Payouts areas still absent / graph-only / mapped-not-running / substitute

Evidence sources: `reports/domain/GRAPH_STATS.json` (1995 nodes, 3438 edges,
42 families, 24 P0 families, `graph_sha256
92f12bdd39c59345cb8cdf0adfa22e8157b1a3c6830f248ab38f08427f3be424`);
`reports/domain/FIDELITY_CLASSIFICATION.csv` (1995 rows, 0 unlabelled);
`RED_LOOP/runs/m6-journeys-20260907T200039Z/results.json` (live run, 72 journeys).

Graph-wide fidelity histogram (GRAPH_STATS.json):
`real_source_running 657 · real_source_mapped_not_running 479 · graph_only 481 ·
high_fidelity_replacement 297 · behavioural_placeholder 79 · blocked_missing_access 2`.
Critical: representation 100.0% (329/329), **runtime 68.4%, executable 65.7%**.

| area (family) | pri | graph fidelity | live-run journey status | evidence |
|---|---|---|---|---|
| shared-payouts | P0 | real_source_running | 9 PASS / 1 BLOCKED (`restart`) | results.json |
| direct-payouts | P0 | real_source_running | 9 PASS | results.json |
| accounting | P0 | real_source_running | 5 PASS | results.json |
| scheduled-payouts | P0 | real_source_running | 5 PASS / 1 BLOCKED (`cancel_via_dashboard`) | results.json |
| idempotency-retries | P0 | real_source_running | 5 PASS / 1 BLOCKED (`retry`) | results.json |
| webhooks | P0 | high_fidelity_replacement | 6 PASS | results.json |
| pricing-free-payouts | P0 | behavioural_placeholder | 5 PASS / 1 EXPECTED_FAILURE | m6-journeys.json + results.json |
| **queued-low-balance** | P0 | real_source_running | **5/5 FAIL** — `merchant_ledger_balance_readable` | results.json |
| **failure-reversal-cancellation** | P0 | real_source_running | **3 FAIL** (same check) / 3 PASS / 1 BLOCKED | results.json |
| **approval-workflow** | P0 | high_fidelity_replacement | **6/6 BLOCKED** — `workflow-engine` not running | results.json + `docker ps` |
| **bulk-payouts** | P0 | high_fidelity_replacement | **5/5 BLOCKED** — `batch-sim` not running | results.json + `docker ps` |
| **on-hold** | P0 | real_source_running | **1/1 BLOCKED** | results.json |
| **async-workers** | P0 | real_source_running | **no journey at all** | GRAPH_STATS `p0_families_missing_journey` |
| **fetch-list** | P0 | real_source_running | **no journey** | idem |
| **fts-status-propagation** | P0 | high_fidelity_replacement | **no journey** | idem |
| **fund-transfer-execution** | P0 | high_fidelity_replacement | **no journey** | idem |
| **mozart-gateway** | P0 | high_fidelity_replacement | **no journey** | idem |
| **public-api-ingress** | P0 | behavioural_placeholder | **no journey** | idem |
| **source-updates** | P0 | real_source_mapped_not_running | **no journey** | idem |
| bank-channel-routing | P0 | real_source_running | 8 executable (M4/M5-era) but not in the M6 run | GRAPH_STATS family_coverage |
| statement-reconciliation (BAS/ART) | P0 | high_fidelity_replacement | 4 executable (M4-era) | GRAPH_STATS |
| balance-refresh-reservations | P0 | behavioural_placeholder | 4 executable (M4-era) | GRAPH_STATS |
| internal-service-routes | P0 | behavioural_placeholder | 6 executable (M4-era) | GRAPH_STATS |
| beneficiary-fund-accounts | P0 | real_source_running | 1 executable | GRAPH_STATS |
| **admin-ops / payout-links / fund-account-validation / observability-sla / vendor-payments** | P1–P2 | **graph_only** | 0 executable | GRAPH_STATS family_coverage |
| va-payouts, virtual-account-payouts, fund-management-payouts, data-consistency, usage-events, beneficiary-registration | P1–P2 | **real_source_mapped_not_running** | 0 executable | GRAPH_STATS |
| account-statement, risk-validation, fts-retry-repair | P1 | replacement / placeholder | 0 executable | GRAPH_STATS |
| mozart real binary (`sub:mozart-mock`, `svc:mozart-mock-mode`) | — | **blocked_missing_access** (2 nodes) | private Go modules `integrations-utils/-go/orchestrator` unreadable; arena runs `mozart-sim` | GRAPH_STATS `blocked_missing_access`; `REMAINING_ACCESS_MANIFEST.md` §A |

Cross-check against `reports/PAYOUTS_CLOSURE.yaml` (2026-09-04.v2, 31 tiered
components: **F3 7, F3-capable 2, F3-capable-but-empty 1, F3-mock 1, F2 10, F1 1,
F0 8**): the eight F0 components (charge-collections, tax-compliance, scrooge,
settlements, ufh, beam, catalyst, master-onboarding) remain the graph_only tail
and are unchanged by M6.

`reports/implementation/m4-fidelity-matrix.csv` (5.5 KB, 2026-09-07 02:25) and
`reports/fidelity/*` (all 2026-09-05) are **untouched by M6** — the M6 fidelity
view lives only in the untracked `reports/domain/FIDELITY_CLASSIFICATION.csv`.

---

## 5. Runtime (VERIFIED, `docker ps`; nothing started or stopped by this audit)

* `docker ps -q | wc -l` → **67 running**.
* `docker ps -a --filter label=com.docker.compose.project=env2_compose | wc -l` → **68**
  (67 running + 1 exited, consistent with the documented `ledger-scheduler` one-shot).
* **The arena IS up** — `env2_compose-payouts-api-1 Up 22 hours (healthy)`, all
  MySQL/Mongo/Kafka/Redis/localstack/postgres-ledger healthy, 15 payouts workers,
  11 fts workers, 5 ledger workers, 2 cfa workers, xbalances server+worker.
* **The M6 additions are NOT running.** No `workflow-engine`, no `batch-sim`, and
  none of the 10 new `payouts-worker-*` containers appear. `workflow-sim` (the old
  thin stand-in) is up. This exactly matches `M6_RUNTIME_CHANGES.md` §1:
  *"Nothing here was booted… The live arena (compose project `env2_compose`, 67
  running + 1 exited) was not started, stopped, restarted or rebuilt."*
  Expected post-boot count per §7 is **80 services (79 running + ledger-scheduler)**.
* Recently restarted by the live journey lane: `kong-lite`, `monolith-stub`
  (Up 2 minutes), `dcs-stub`, `splitz-stub`, `bankingaccounts-stub` (Up 20 minutes).
* `reports/implementation/runs/` — newest entry `audit-20260906T155953-f85ce0`
  (2026-09-06). **No 2026-09-07/08 run roots there.**
* `RED_LOOP/runs/` — 18+ M6 run roots dated 2026-09-07/08, newest
  `m6-journeys-20260907T200039Z` (mtime **2026-09-08 01:52**), plus
  `m6-journeys-20260907T195704Z` (01:30) which is the one the committed-shaped
  report points at.

---

## 6. Stale / ignored / unhashed evidence

**Verifiers (read-only, both run):**
* `python3 RED_LOOP/surface/m4_evidence_manifest.py verify` → exit 0,
  `{"ok": true, "self_sha256_ok": true, "companion_sha256_ok": true,
  "artifacts_checked": 19, "mismatches": [], "missing": []}`.
* `python3 RED_LOOP/surface/m41_canonical_paths.py` → exit 0,
  `OK: 19 mandatory evidence paths are canonical (tracked, not ignored, present).`
  (equivalent to `make m41-canonical-paths` / `m41-evidence-verify`; `make
  m41-clean-acceptance` chains both.)
  → **The M4/M4.1 evidence chain is intact and unaffected by M6.**

**No M6 manifest exists.** `m6_acceptance.py` has no manifest/hash step, and the
`Makefile` M6 block adds only snapshot/refresh/test targets — **no
`m6-acceptance`, no `m6-evidence-verify`, no `m6-canonical-paths`**.

**Ignored evidence globs (`.gitignore` + `RED_LOOP/.gitignore` +
`ENV2_COMPOSE/.gitignore`):**
`reports/implementation/runs/`, `RED_LOOP/runs/`, `ENV2_COMPOSE/generated/`,
`ENV2_COMPOSE/secrets/*.txt`, `ENV2_COMPOSE/snapshots/`, `.local/`,
`ARCHITECTURE_EXPLORER/dist/`, `**/__pycache__/`.

**Evidence referenced by M6 docs that is untracked or unhashed:**

| reference | where cited | state |
|---|---|---|
| `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json` (+ 4 sibling formats) | `reports/domain/README.md`, m6_acceptance M6-12 | **untracked** → M6-12 FAIL |
| `reports/domain/GRAPH_STATS.json`, `FIDELITY_CLASSIFICATION.csv`, `REPOSITORY_INVENTORY_M6.csv`, `SNAPSHOT_MANIFEST.json`, 100 `recipes/*.yaml`, 6 `snapshots/*.json`, 4 `refresh/*.json` | README.md | **untracked**, in no manifest |
| `reports/implementation/m6-journeys.json`, `m6-journey-coverage.md` | run.py docstring, M6-12 | **untracked** |
| `RED_LOOP/runs/m6-journeys-20260907T195704Z/**` (evidence bundle the coverage report cites) | m6-journey-coverage.md `run_dir` | **git-ignored** (`RED_LOOP/.gitignore: runs/`) and **superseded** by `…T200039Z` |
| `reports/implementation/m6-clean-boot.json`, `m6-refresh-demo.json` | m6_acceptance M6-07/07b/08b, `clean_boot.py` docstring | **do not exist** |
| `ENV2_COMPOSE/secrets/wfe_admin_token.txt` minted on host | M6_RUNTIME_CHANGES §2.5 | git-ignored by design |

**Staleness (VERIFIED):** `reports/implementation/m6-journeys.json` reports
`run_dir m6-journeys-20260907T195704Z`, `total 6, pass 5, fail 0, EF 1, blocked 0`
covering **one family only** (`family:pricing-free-payouts`), and its own
coverage table prints "*NOT RUN in this selection*" for the other 8 P0 families it
lists. The newer run `…T200039Z/results.json` has **72 journeys across 12 families:
47 PASS, 8 FAIL, 16 BLOCKED, 1 EXPECTED_FAILURE** — it has not been promoted into
`reports/implementation/`. **INFERRED: the promoted report is stale by roughly
2 hours and understates both coverage and failures.**

Two further consistency notes (INFERRED): `SNAPSHOT_MANIFEST.json` carries
`config_digest 8b131ea0…` while the journey run fingerprint carries
`839a4da8…`, and `graph_version` in the manifest is `null` although M6-08a passes
on key-presence alone.

---

## 7. Verdict (10 lines)

1. **M6 is IN PROGRESS — neither complete nor accepted.** A journey run
   (`RED_LOOP/m6/journeys/run.py`, PID 62125) was still executing during this audit.
2. **Nothing is committed.** HEAD == `2613f38` == the M5 tag; `git stash list` empty;
   no `twin-m6-complete-domain` tag exists.
3. **Staged but uncommitted:** 19 files / +2112 lines — the whole lane-I3 runtime
   change set (compose +12 services, `.env.arena` workflow default, secrets,
   up.sh, workflow-engine substitute + Dockerfile + ARENA.md + M6_RUNTIME_CHANGES.md,
   `reports/domain/SCHEMA.md`).
4. **Unstaged:** `Makefile` only (7 `m6-*` snapshot targets).
5. **Untracked:** 176 paths — batch-sim substitute, the entire `RED_LOOP/m6`
   harness, `m6_acceptance.py`, `scripts/{domain,snapshot}`, and all 133
   `reports/domain/**` + 2 `reports/implementation/m6-*` evidence files.
6. **Acceptance: 7/15 gates, `accepted=false`** (evaluator run to scratchpad,
   exit 1). All 15 gates are machine-computed (13 `direct_artifact`, 2
   `direct_git`); none are hand-authored — but M6-08a is key-presence-only and
   M6-07/08b consume harness self-reports.
7. **Blocking failures:** no `m6-clean-boot.json`, no `m6-refresh-demo.json`,
   23/24 P0 families lack a PASSing journey in the promoted report, 70 P0
   workers/crons unresolved, remaining-access manifest missing its 2 blocked
   nodes, and the graph + journey evidence are untracked.
8. **Runtime blocker:** the arena is up at the **M5 shape (67 running + 1 exited)**;
   the M6 compose additions were deliberately never booted, which is exactly why
   `approval-workflow` (6) and `bulk-payouts` (5) journeys are BLOCKED.
9. **Real observed defects, not infrastructure:** 8 FAILs in the live run —
   `queued-low-balance` 5/5 and `failure-reversal-cancellation` 3, all on
   `merchant_ledger_balance_readable`.
10. **Remaining work to accept M6:** reboot the arena on the M6 compose (→ 80
    services), rerun the full journey suite, produce `m6-clean-boot.json` and
    `m6-refresh-demo.json`, fix the ledger-balance-readable defect, regenerate
    the remaining-access manifest against the built graph, `git add` all M6
    evidence, then commit and tag.
