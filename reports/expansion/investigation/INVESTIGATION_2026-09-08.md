# RedGrid repository-state investigation — 2026-09-08

Read-only investigation. No code, config, or container was modified. All commands were run from
`/Users/rana.singh/rzp-payouts-architecture` (main checkout) unless noted. Labels: **[V]** verified by
command/file read in this investigation; **[I]** inferred; **[U]** unknown.
Evidence files (copied here from the scratchpad, which is subject to `/private/tmp` purging):
`agent-reports/01_m6_status.md`, `03_fidelity_inventory.md`, `04_automation.md`, `06_s2p_readiness.md`,
`cmd_*.txt`, `m4-acceptance-dryrun.json`, `m5-acceptance-dryrun.json`, `m6-acceptance-dryrun.json`.

One disclosure: while probing `ENV2_COMPOSE/config/generate.py --help` (it has no argparse) the
automation agent executed it, re-rendering the git-ignored `ENV2_COMPOSE/generated/` from the M6
session's current *uncommitted* inputs. No tracked file changed; `scripts/up.sh` regenerates that
directory on every boot; but the on-disk rendering now reflects M6's edited config, not the config
the running containers were booted with. The running arena was not touched.

---

## 0. Two findings that change the picture (read first)

**F0.1 — The pinned clone root is decaying [V].** `.local/repos-root` points at
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-…/scratchpad/rzp-payouts-architecture`. Every repository
cloned in the 2026-09-03 batch (`clone_results.log`: payouts, cfa, ledger, fts, x-balances, banking-accounts,
xperience, x, frontend-x, x-account-statements, payout-links, vendor-payments, vendor-experience,
accounting-integrations, admin-dashboard, relay, virtual-account, ValidX, charge-collections, …) now has a
`.git` with 3 files and `git rev-parse HEAD` fails; working trees are mostly gone (vendor-payments 85 files,
vendor-experience 28, accounting-integrations 55, x 80, fts 111, ledger 167, cfa 626; payouts keeps 2583
files but no HEAD). Every repository from the 2026-09-04 batch (`clone_delta.log`: api, authz, workflows,
proto, rpc, kube-manifests, spinacode, stork, mozart, dcs, splitz, governor, batch, edge, …) is intact
(29 `.git` files, HEAD resolves). Command: per-repo `find … | wc -l` + `git rev-parse --short HEAD`
(output in §4.6). Pattern matches macOS purging `/private/tmp` entries not accessed for 3 days [I].
The five admitted build copies in `.local/twin-repos/accepted/{cfa,fts,ledger,payouts,x-balances}` live
inside the project directory and are intact (475/1101/738/1425/539 files) [V]. The S2P agent recovered
full trees for vendor-payments/vendor-experience/accounting-integrations from the still-intact depth-1
packfiles into the scratchpad (also `/private/tmp`, so also perishable).
Consequence: any future "refresh" that starts from `.local/repos-root` will silently see an empty repo.

**F0.2 — A load-bearing claim in the 2026-09-07 expansion reports is false [V].**
`REPLICATION_WORKFLOW_S2P.md:6`, `DOMAIN_CARDS.md:28` and `EXPANSION_REPORT.md:64` state that the arena's
monolith substitute already serves `payouts_internal`, `internalContactPayout`, contacts/fund-accounts
internal routes. `ENV2_COMPOSE/substitutes/monolith-stub/server.py` `ROUTES` (lines 1080-1112, 32 routes)
contains only `GET /fund_accounts_internal/` (`server.py:264`); `payouts_internal`, `internalContactPayout`,
`contacts_internal`, `banking_accounts_internal`, `tax-payment-id` are absent. See `ERRATA.md`. The
Source-to-Pay recommendation survives (§6) but its "lands on existing routes" argument does not; three
routes must be added.

---

## 1. Full-Payouts (M6) expansion status

| Item | Value | Evidence |
|---|---|---|
| Repository / worktree | `/Users/rana.singh/rzp-payouts-architecture` (main checkout) | `git worktree list` [V] |
| Branch | `milestone-6-complete-payouts-domain` | `git status -sb` [V] |
| HEAD | `2613f38` = `assurance-m5-autonomous-discovery^{commit}` | `git log --oneline -1`; `git rev-parse assurance-m5-autonomous-discovery^{commit}` [V] |
| Base | same commit; **zero M6 commits exist** | `git rev-list --count assurance-m5-autonomous-discovery..HEAD` = 0 [V] |
| Tree clean? | **No.** 19 staged (+2112/−39, 20 files incl. `Makefile` unstaged), 176 untracked paths (191 total) | `git status --porcelain --untracked-files=all | wc -l` = 191; `git diff HEAD --stat` [V] |
| Stash / tag | stash empty; tag `twin-m6-complete-domain` (expected by gate M6-11) does not exist | `git stash list`; `git tag -l` [V] |
| Final report | none promoted. Working docs: `ENV2_COMPOSE/M6_RUNTIME_CHANGES.md` (staged, 501 lines), `reports/domain/README.md`, `reports/implementation/m6-journey-coverage.md` (untracked) | [V] |
| Acceptance artifact | none on disk. Dry run executed by this investigation: `python3 RED_LOOP/surface/m6_acceptance.py --out <scratchpad>` → **7/15 gates, `accepted=false`, exit 1** | `agent-reports/m6-acceptance-dryrun.json` [V] |
| Machine-backed gates | all 15 computed (13 `direct_artifact`, 2 `direct_git`); none hand-authored. Caveats: M6-08a checks key presence only; M6-07/07b/08b read harness self-reports (`m6-clean-boot.json`, `m6-refresh-demo.json`) that do not exist | `01_m6_status.md` §gates [V] |
| Live process | `RED_LOOP/m6/journeys/run.py` was executing (PID 62125) during the audit; files mtime 01:52-01:53 IST 2026-09-08 | [V] |

Gate detail (dry run): PASS M6-01, 01b, 02 (100% of 329 critical components), 03 (42 families), 08a, 09
(1995 nodes labelled), 11. FAIL M6-04 (23/24 P0 families without a PASSing journey in the *promoted*
report), 05, 06 (70 P0 workers/crons unresolved), 07/07b (clean-boot artifact absent), 08b (refresh demo
absent), 10 (2 `blocked_missing_access` nodes missing from `REMAINING_ACCESS_MANIFEST.md`, whose §B reads
"graph not built yet"), 12 (graph + journeys untracked).

**What M6 added (staged/untracked, never booted)** [V, `M6_RUNTIME_CHANGES.md` §1; `git diff HEAD --stat`]:
- 10 payouts worker containers so all 25 registered jobs have a container (previously 15); no new queues
  (all SQS queues already existed).
- `workflow-engine` as a compose service (port 8093, own Dockerfile, `arena_entrypoint.py`, secret
  `wfe_admin_token`, volumes `secrets-workflow`, `wfengine-data`); `ARENA_WORKFLOW_HOST` default flipped to it.
- `batch-sim` substitute (port 8094; `substitutes/batch-sim/{server.py,CONTRACT.md,test_batch_sim.py}`).
- Three latent blockers fixed in the diff (unmaterialised `auth_workflow_payouts`, workflow hosts missing
  from `NO_PROXY`, `up.sh` not sourcing `.env.arena`) — unproven at runtime.
- Journey framework `RED_LOOP/m6/journeys/*.py` (14 families: accounting, approval, bulk, direct, failure,
  idempotency, onhold, pricing, queued, scheduled, shared, webhooks), 31 run dirs
  `RED_LOOP/runs/m6-journeys-2026090[78]T*` (git-ignored).
- Functional graph `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.*` (1995 nodes / 3438 edges / 42 families;
  `GRAPH_STATS.json`), `FIDELITY_CLASSIFICATION.csv`, `REPOSITORY_INVENTORY_M6.csv`, 100 service recipes,
  `SNAPSHOT_MANIFEST.json`, `scripts/domain/*`, `scripts/snapshot/*`, 5 snapshots + 4 refresh plans.
- FTS Kafka producer leg documented, not flipped.

**Journey evidence** [V]: promoted `reports/implementation/m6-journeys.json` (run `…T195704Z`) covers 6
journeys / 1 family (`pricing-free-payouts`: 5 pass, 1 expected failure). The newer git-ignored run
`RED_LOOP/runs/m6-journeys-20260907T200039Z/results.json` (rewritten 01:57) has 75 journeys / 12 families:
47 PASS, 9 FAIL, 1 EXPECTED_FAILURE, 18 BLOCKED. All 9 FAILs share one cause
(`merchant_ledger_balance_readable` on merchant `ARENAM98299494`, a provisioning failure). 18 BLOCKED
reduce to: dead workflow host (7), no batch-sim container (5), monolith-stub `_get_merchant` ignoring
`FEATURE_OVERRIDES` (3), no proxy-auth passport shape (1), FTS retry scheduling (1), reboot boundary (1).
The journey fingerprint's `git_head` is `39fe41e5` (an M3.1 commit, ancestor of HEAD) — the live arena was
booted 2026-09-06T13:40Z from that era's config (`ENV2_COMPOSE/.runtime/arena-fingerprint.json`) [V].

**Still absent / graph-only / mapped-not-running / substituted** (from `FIDELITY_CLASSIFICATION.csv` and
`03_fidelity_inventory.md`) [V]: 9 P0 families have no journey at all (async-workers, fetch-list,
fts-status-propagation, fund-transfer-execution, mozart-gateway, public-api-ingress, on-hold,
source-updates, bulk-payouts); admin-ops, payout-links, fund-account-validation (ValidX) are graph-only;
real `api`, `edge`, `stork`, `workflows`, `mozart` are source-mapped-not-running; real Mozart `-mock` is the
only `blocked_missing_access` pair (cannot be built: private deps `integrations-utils`, `integrations-go`,
`orchestrator`). Histogram: real_source_running 657, mapped_not_running 479, high_fidelity_replacement
297, behavioural_placeholder 79, graph_only 481, blocked 2.

**Stale / ignored / unhashed evidence** [V]:
- All M6 evidence is untracked (M6-12 FAIL); `RED_LOOP/runs/` and `reports/implementation/runs/` are
  git-ignored (`.gitignore`), so every M6 journey run is unhashed and outside any manifest. There is no M6
  evidence manifest or verifier (M4's `m4_evidence_manifest.py verify` → ok, 19 artifacts, 0 mismatches;
  `m41_canonical_paths.py` → 19/19).
- Promoted `m6-journeys.json` is one run behind the live results.
- All 14 running substitute containers use *dangling* images that no longer match their `:v1-candidate`
  tags (including the dcs-stub fieldmask fix of 2026-09-07) — `docker inspect` vs `docker images`
  (`03_fidelity_inventory.md` §1.1).
- `reports/implementation/BUILD_PROVENANCE.md:52` records payouts image `e029d639`; running is `915299ab`
  (one rebuild stale); the other four match.
- `TWIN_SPEC` deviation `DEV-172` is malformed (block scalar in a strict-subset YAML) so the acceptance
  gate and the RED_LOOP judge cannot read it; registry has 89 entries, `known_gaps.yaml:20` claims 76.
- `reports/PAYOUTS_CLOSURE.yaml` env-2 row still claims "mozart real binary -mock mode", contradicted by the
  build result.

**Verdict:** M6 is in progress, not accepted, and unrecorded in git. Its acceptance script is machine-backed
but 8 of 15 gates fail on missing artifacts and untracked evidence.

---

## 2. Branch, tag and merge topology

`git log --graph --oneline --decorate --all` shows a single linear mainline; no merges [V]:

```
d99aed4 (graph-expansion-adjacent-domains)                       worktree …-expansion
2613f38 (HEAD milestone-6-complete-payouts-domain, tag assurance-m5-autonomous-discovery,
         milestone-5-finish-autonomous-discovery)
e8e0699, 1a602bc  (M5)
ef4ba72 (tag assurance-m4.1-reproducible, milestone-5-autonomous-discovery-workflows)
2bc692a, f68064c  (M4.1)
0cb90eb (milestone-4-direct-reconciliation)
3ae1773 (tag red-loop-m4)
… 3a044f8 (tag red-loop-m3.1, milestone-3-1-claude-final) … 78def24 (tag twin-v1.0, twin-v1)
```

| Pair | merge-base | Relationship |
|---|---|---|
| milestone-6 ↔ graph-expansion | `2613f38` | expansion = 1 commit ahead (d99aed4, 25 files, +16709, all under `reports/expansion/`); M6 = 0 commits ahead + dirty tree |
| red-loop-m3.1 ↔ red-loop-m4 | `3a044f8` | linear; 60 commits from m3.1 to HEAD |
| red-loop-m4 ↔ assurance-m4.1 | `3ae1773` | linear; 7 commits m4→HEAD |
| milestone-4-direct-reconciliation (0cb90eb) ↔ m4.1 | `0cb90eb` | ancestor of ef4ba72 |
| assurance-m4.1 ↔ HEAD | `ef4ba72` | 3 commits |
| worktree-agent-a00e097ab93ddcb76 (5300617 "M4 harness: T13 handoff") ↔ HEAD | `5300617` | already an ancestor of 2613f38; stale agent worktree, nothing unmerged |
| side branches milestone-2-red-loop (a107612), milestone-3-1-calibration (a63185f), milestone-3-1-clean-parity (0dd9b32), milestone-3-gateway-hardening (3873e96), twin-v1 (78def24) | each is its own merge-base with m3.1 | 0 commits ahead of HEAD — all fully contained |

Unreported commits: none on branches. `git fsck --unreachable` lists 5 unreachable commits
(66671b6, a19ad0d, c39a1f3, 085cca8, 7d9cf2e) — M5 amend/reset leftovers per `git reflog` [V]. No remote is
configured (`git remote -v` empty); no `main`/`master` ref exists [V]. All five milestone tags are
annotated tag objects (d3f9a16, 2f11824, 05557e1, ee17aab, e83cd02) and every tagged commit is an ancestor
of HEAD (`git merge-base --is-ancestor` = yes for each) [V].

**Overlapping files / conflicts** [V]:
- M6 dirty set ∩ expansion branch files = ∅ (`comm -12` of the two path lists is empty). Textual merge of
  d99aed4 onto M6 is conflict-free.
- Conceptual duplication: M6 `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json` (1995 nodes, 6-class
  fidelity vocabulary, lane parts) vs expansion `reports/expansion/GRAPH_PATCH.json` (150 nodes overlaying
  the older `reports/PAYOUTS_SERVICE_GRAPH.json` 197-node schema); M6 `REMAINING_ACCESS_MANIFEST.md`
  (payouts-scoped) vs expansion `ACCESS_MANIFEST.csv` (adjacent-domain-scoped); M6 `scripts/snapshot/` vs
  expansion `SNAPSHOT_REFRESH_PLAN.md` (plan only). The graph schemas differ; the expansion patch should be
  re-expressed as lane parts for `scripts/domain/build_graph.py` rather than merged as a second graph.
- Future S2P work would touch `ENV2_COMPOSE/docker-compose.yml`, `config/arena.yaml` (M6 +511/+12 lines,
  staged) and `substitutes/monolith-stub/server.py` (M6 does not touch it) — additive, low conflict if M6
  lands first.

**Generated artifacts not to merge blindly:** `reports/expansion/PAYOUTS_SERVICE_GRAPH.expanded.json`
(derived; regenerate with `scripts/build_graph_patch.py`), `GRAPH_PATCH_VALIDATION.json`; M6
`reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.*`, `GRAPH_STATS.json`, `FIDELITY_CLASSIFICATION.csv`, `recipes/`,
`SNAPSHOT_MANIFEST.json`, `snapshots/`, `refresh/` (all outputs of `scripts/domain|snapshot/*`; rebuild
after merge); `ENV2_COMPOSE/generated/` (git-ignored); `RED_LOOP/runs/` (git-ignored).

**Immutable:** tags `twin-v1.0`, `red-loop-m3.1`, `red-loop-m4`, `assurance-m4.1-reproducible`,
`assurance-m5-autonomous-discovery` and the evidence they point to (`m4_evidence_manifest.py verify` must
stay ok:true).

**Most complete trustworthy state:** committed truth = `2613f38` (M5 tag; `m5_acceptance --dry-run` 38/38
accepted=true today; `m4_acceptance --dry-run` 73/74 with only G71 "clean tree" pending because of the M6
dirty tree). Richest *uncommitted* state = the M6 tree (graph, recipes, snapshots, journeys), unverified.
Expansion branch d99aed4 is committed and self-validating but documentation-only.

**Recommended integration sequence (not performed):**
1. M6 session commits its work in ≥2 commits (runtime: compose/config/substitutes; evidence: reports/domain,
   RED_LOOP/m6, m6_acceptance) and promotes the latest journey results; then reboots the arena from the
   committed shape and produces `m6-clean-boot.json` so M6-07/12 can pass. Do not tag until
   `m6_acceptance.py` returns accepted=true.
2. Fast-forward-merge `graph-expansion-adjacent-domains` (d99aed4) onto the M6 branch (zero path overlap),
   apply `ERRATA.md`, and convert `GRAPH_PATCH.json` into a `reports/domain/parts/adjacent-domains.json`
   lane so a single graph builder owns both.
3. Re-clone the decayed repositories into a durable location and repoint `.local/repos-root` before any
   refresh or S2P build (F0.1).
4. Only then start S2P (§6), branching from the merged tip.

---

## 3. Consolidated fidelity inventory (live arena, 2026-09-08)

Runtime shape [V]: compose project `env2_compose`, 67 running + 1 exited (`ledger-scheduler`, one-shot exit
0), booted 2026-09-06T13:40Z at git_head 39fe41e5, route profile `monolith`. Full 800-line inventory with
per-component tables, deviations and claim matrix: `agent-reports/03_fidelity_inventory.md`. Summary:

| Component | Class | SHA / path | Runtime name(s) | Journeys | Auth/trust represented | Synthetic data | Key deviations | Security claims supported / not |
|---|---|---|---|---|---|---|---|---|
| Payouts API | real source running | payouts `4bf3dbf9`; `.local/twin-repos/accepted/payouts`; image `915299ab` | `payouts-api` (compose :470) | all | passport JWT (merchant key shape via kong-lite), internal basic-auth users from `[auth.*]`, internal headers | merchants/users/balances seeds `ENV2_COMPOSE/seeds/` | `[ccSdk].mock=true` (DEV-015), ASV unreachable, Shield/Splitz stubs | ✔ idempotency, authz boundary, state machine, ledger consistency; ✘ pricing plans, prod RBAC |
| Payouts workers | real source running (15/25 live; +10 in M6 diff, not booted) | same | `payouts-worker-*` | queued/scheduled/on-hold/reversal families | same | same | 10 jobs had no container until M6 (uncommitted) | ✔ async state legality (partial) |
| Payouts Kafka consumer | real source running | same | `payouts-kafka-fts-status-updates-*consumer` (2) | FTS status propagation | — | — | FTS Kafka producer leg not flipped (M6 doc) | ✘ Kafka route until flipped |
| API monolith | high-fidelity replacement (PS/FTS-facing) / graph-only (dashboard/admin); real `api` mapped-not-running | `substitutes/monolith-stub/server.py` (1115 lines, 32 routes) | `monolith-stub` (:2037) | create via proxy, merchant config, fund_accounts_internal | basic-auth app creds, `X-Razorpay-Account` headers | `seeds/monolith/` | no `payouts_internal`/`internalContactPayout`; `_get_merchant` ignores `FEATURE_OVERRIDES` (3 M6 blocks) | ✔ merchant-config gating; ✘ OTP/2FA, admin repair, dashboard auth |
| Kong / edge | high-fidelity replacement for merchant-API-key passport shape; graph-only for 3 other shapes; real `edge`/`terraform-kong` mapped-not-running | `substitutes/kong-lite/{server.py,route_policy.py,CONTRACT.md}` | `kong-lite` (:1985; dual-homed) | ingress for all public-API journeys | mints passport JWT for merchant key; 5 forged shapes rejected at SDK (M4 boundary suite) | 161 merchants loaded | no plugin engine, no rate limiting | ✔ tenant isolation at PS; ✘ Kong plugin semantics, rate limits |
| CFA | real source running | cfa `d488558e`; image `1b6cf763` | `cfa-server`, `cfa-worker-contact`, `cfa-worker-fa` | contact/fund-account create, payout | gRPC internal + basic-auth users | contacts/FAs seeds | Mongo real; bin-service/token-service absent (card FAs) | ✔ beneficiary identity/tenant filter; ✘ card tokens |
| x-balances | real source running (server) / running but inert (worker) | x-balances `1a21c0f9`; image `980f8333` | `xbalances-server`, `xbalances-worker` | balance read, direct-account flows | basic-auth users | balances seed | 1 generic worker vs 5 per-bank families | ✔ balance authorisation path; ✘ per-bank refresh cadence |
| Ledger | real source running (API + 5 workers) / one-shot scheduler | ledger `471ff4d5`; image `878be96a` | `ledger-api`, `ledger-worker*` (5), `ledger-scheduler` (exited 0) | every money movement | basic-auth client keys, `Ledger-Tenant: X` | ledger accounts seeded on merchant onboarding | scheduler CronJobs `suspend: true` in prod too | ✔ ledger consistency, insufficient-balance rejection; ✘ PG tenant flows |
| FTS | real source running | fts `2a09e763`; image `dac4c76a` | `fts-web` + 13 `fts-worker-*` | transfer execution, status webhooks, retries | basic-auth users (PS, ART…) | bank accounts seeds | RBL-only channel; circuit breaker 22× looser (DEV-012, unfixed) | ✔ attempt state machine, stuck-payout paths; ✘ real bank protocol, multi-bank routing |
| Bank integrations | `mozart-sim`: high-fidelity replacement (RBL v1) / behavioural stub (other banks); real Mozart `-mock`: build-blocked | `substitutes/mozart-sim/` | `mozart-sim` (:2327); `mozart-mock` not created | transfer, account statement | — | scenario options | ~6/140 bank codes | ✔ status-code branching for RBL; ✘ bank wire fidelity |
| Bank-statement ingestion | real source running (`payouts-worker-rbl-banking-account-statement`, grafted post-boot) + `xas-sim` (HFR) + `bankingaccounts-stub` (stub) | `substitutes/xas-sim/`, `bankingaccounts-stub` | as named | BAS ingestion, XAS matching | hardcoded synthetic bank creds (DEV-168) | RBL CSV fixtures (DEV-167) | `xas-sink` predecessor is graph-only (no compose service; still in `arena.yaml:66`) | ✔ UTR matching, balance/account events; ✘ multi-bank statement formats |
| Reconciliation | xas-sim + ledger DA (M4, 36/36) running; `recon`/ART graph-only | `RED_LOOP/m4` | — | M4 reconciliation | ART Basic creds in FTS real | — | verifier calls FTS ART routes directly (no FinOps gate) | ✔ 3-way match logic; ✘ human-gated ART flow |
| Webhooks | high-fidelity replacement (`stork-capture`, `merchant-webhook-sink`); real `stork` mapped-not-running | `substitutes/stork-capture`, `merchant-webhook-sink` | as named | webhook completeness family | Stork Twirp shape | subscriptions seeds | no retry/backoff schedule of real Stork | ✔ event completeness/order observation; ✘ delivery backoff, dedupe absence |
| Workflow | real `workflows` (080d71a) **mapped-not-running**; `workflow-sim` behavioural stub **running**; `workflow-engine` high-fidelity **reconstruction, built not running**; live payouts `[workflow].host` = `127.0.0.1:1` | `.local/repos-root/workflows`; `substitutes/workflow-sim`; `substitutes/workflow-engine` (image `2ff1a64c85a4`) | `workflow-sim` (:2352); `workflow-engine` (:2389, not up) | approval family — all 6 journeys BLOCKED live | actor tokens/roles (engine); none (sim) | orgs/actors/policies (engine) | engine stronger than prod on C51 expiry, C52 approver identity | ✔ maker-checker/N-of-M/terminal-state (engine, standalone M5); ✘ Cadence timers, prod RBAC |
| Feature flags / config | `dcs-stub` HFR on the wire (real proto3 codec) but protocol gap for pristine binary; `splitz-stub`, `shield-stub`, `pricing-stub` (dead), `asv-stub` (gRPC-incompatible) behavioural stubs | `substitutes/*` + `CONTRACT.md` | as named | gating in all journeys | — | `seeds/generated/shield/rules.json` | pricing unreachable (`[ccSdk].mock=true`); ASV native gRPC unserved | ✔ flag-gated branch coverage; ✘ Splitz experiment decisions, real pricing |
| Batch / bulk | `batch-sim` behavioural stub (M6, not booted); real `batch` mapped-not-running | `substitutes/batch-sim/` | `batch-sim` (:8094, not up) | bulk family — 5 journeys BLOCKED | X-Batch-Id headers | — | sequential chunk walk (no concurrency) | ✘ bulk concurrency/idempotency tier |
| Crons | `cron-driver` substitute for FastCron SaaS | compose service | `cron-driver` | queued/scheduled/on-hold | IP-allowlist not modelled | — | cadence not prod's | ✘ cadence claims |
| Queues/brokers/DBs | real: Kafka, LocalStack (SQS/SNS), Redis, MySQL ×4, Mongo, Postgres | compose | `kafka`, `localstack`, `redis`, `mysql-*`, `mongo-cfa`, `postgres-*` | all | — | — | TiDB → MySQL stand-in; ES/Dynamo absent | ✔ queue semantics; ✘ TiDB/ES behaviour |
| ValidX, UPS, Raven, Beam, Vault/HVault, settlements, wallet, payout-links, vendor-payments, xperience, dashboards, admin | graph-only | — | blackholed `127.0.0.1:1` | — | — | — | — | none |

Closure-YAML cross-check [V]: two of nine "must not be mocked simplistically" properties are not held live
(`approval`, `beneficiary_validation`). On the M4 74/74 figure: 7 gates (G01, G02, G05, G68, G69, G71, G73)
are independent machine checks; 67 validate coordinator-written artifacts (`M41_RUNBOOK.md`). Config-level
M1 fixes DEV-001/003/004/005/006/007 were never re-observed on the wire.

---

## 4. Snapshot, graph-refresh and environment automation

**4.1 What exists** [V] (`agent-reports/04_automation.md` §1):
- Approved snapshots: `ENV2_COMPOSE/build/prepare-repos.py` (git-archive HEAD + Gitleaks) →
  `.local/twin-repos/accepted/` + `.local/build-evidence-<UTC>/provenance.json`; narrative
  `reports/implementation/BUILD_PROVENANCE.md` (5 repo SHAs, 5 image sha256).
- Pinning: `RED_LOOP/surface/m4_evidence_manifest.py build|verify` (19 artifacts + `.sha256`);
  `fingerprint.py` → per-boot `ENV2_COMPOSE/.runtime/arena-fingerprint.json` (boot_id, git_head,
  config_digest, image digests). M6 (uncommitted) `scripts/snapshot/capture.py` → `reports/domain/snapshots/<UTC>.json`
  with 38 repo SHAs, contracts, schemas, flags, config + image digests, `latest.json`.
- Graph rebuild: four builders — clone-root `build_graph.py` (hand-authored literals), `RED_LOOP/surface/m4_world_model.py`
  (re-derivable, committed), M6 `scripts/domain/build_graph.py --strict` (8-lane merge, uncommitted),
  expansion `reports/expansion/scripts/build_graph_patch.py` (overlay). `ARCHITECTURE_EXPLORER/data/architecture.json` is hand-authored.
- Change detection: only M6 `scripts/snapshot/diff.py` (exit 3 = changed) → `affected.py`
  (rebuild / rerun_journeys / regen_config). Committed tree has `contract-source-check.json` (static tally) only.
- Recipes: `ENV2_COMPOSE/config/generate.py` (19 rendered files from `arena.yaml` + secrets); M6 `recipes.py`
  → 100 `reports/domain/recipes/*.yaml` + `SNAPSHOT_MANIFEST.json`.
- Ingestion: proto compiled + hashed; **no parser for Kong, Kubernetes, IAM/authz** (existence + SHA only).
- Recompile: `ENV2_COMPOSE/build/*`, per-service Dockerfiles, `.local/twin-build-tools`.
- Baseline journeys: `ENV2_COMPOSE/verifier` golden-run, `scripts/m4.sh test`, `RED_LOOP/m5/scenario_suite.py`,
  M6 `RED_LOOP/m6/journeys/run.py`.
- Snapshot IDs (real): run roots `reports/implementation/runs/20260905T112903Z-11943`, `m1-20260905T165255Z`;
  campaigns `camp-20260906T150926Z-d03858`; `build-evidence-20260905T160213Z`; domain snapshots
  `20260907T174026Z.json`; boot_id UUIDs; 5 tags.
- Disposable twins: `scripts/m4.sh boot <instance>|clean <instance>`; DAYTONA/* proposal.

**4.2 Demonstrated (read-only/dry-run, outputs in `agent-reports/cmd_*.txt`)** [V]:
`make help` → 0; `make m41-canonical-paths` → 19 canonical; `make m41-evidence-verify` →
`ok:true, artifacts_checked:19, mismatches:[]`; `python3 RED_LOOP/surface/m4_acceptance.py --dry-run` →
73/74 pass, 1 pending (G71 clean tree); `python3 RED_LOOP/surface/m5_acceptance.py --dry-run` → 38/38
accepted=true; `python3 scripts/snapshot/refresh.py status` → 5 snapshots / 100 recipes / graph `92f12bdd39c5`;
`docker ps` → 67 healthy.

**4.3 Missing for event-triggered incremental refresh + periodic full rebuild** [V, absence by search]:
no `.github/` or any CI file; no crontab/launchd entry (cadence exists only as commented README lines);
no `git fetch`/`ls-remote` anywhere, so pinned SHAs never move; no webhook/watcher; contract diff never
regenerates recipes; graph builders are outside the refresh chain; the M6 pipeline itself is untracked;
`.local/repos-root` is a perishable `/private/tmp` path (F0.1); no Kong/Kube/IAM parsers; no M6 evidence
manifest; substitute images drift from tags with no check.

**4.4 Measured numbers**
- Clean boot: **not instrumented** (no `boot_duration|elapsed` anywhere). Timestamp proxy [I]: warm
  clean-boot 63–68 s; cold with image build 435–969 s. Verified suite times: 47.8 s (26 tests), 56.1 s (37).
- Reset/teardown: **no measurement exists** [V].
- Max parallel twins tested: **1** (7 instance ids, all sequential; `m4-known-limits.md` L-001: one arena per
  host at 11.65 GiB) [V].
- Host: single Mac, 4 CPU / 11.65 GiB docker VM, no remote; peak 1.40 cores / 4.18 GiB [V].
- Daytona: **proposed, not operational** — `DAYTONA_RUN_REPORT.md` shows `Sandbox ID | None`,
  `remote_api_calls: 0`, `sandbox_created: false`; SDK not installed; no API key [V].

---

## 5. Missing access and real-world interfaces (ordered by impact)

1. **Durable pinned source** (not an access issue but the top limiter now): re-clone the 2026-09-03 batch
   to a non-`/tmp` path (F0.1).
2. Missing repositories (all 404 to `rana-harsh-raj` on 2026-09-07; `reports/expansion/ACCESS_MANIFEST.csv`):
   for Payouts fidelity — `integrations-utils`, `integrations-go`, `orchestrator` (unblock real `mozart -mock`),
   `asv`/`account-service`, `kong-plugin-user-auth`, `kong-plugin-consumer-identifier`, `kong-utils`,
   `charge-collections-sdk`, `raven`, `wda-service`, `vault`, `hvault`, `beam`, `razorx`
   (`reports/domain/REMAINING_ACCESS_MANIFEST.md` §A); for adjacent domains — `tax-compliance`,
   `accounts-receivable`, `budgets`, `x-bill-payments`, all `x-payroll*`, all `capital-*`, `settlements`,
   `master-onboarding`, `business-verification-service`.
3. API/OpenAPI/protobuf contracts: present for everything in `proto/` (intact). Missing: Kong plugin
   contracts, ASV gRPC prod behaviour, Mozart bank envelopes beyond RBL v1.
4. Event/topic schemas: producers unknown for Kafka `add-tds-entry` and SQS `prod-tax-compliance-payout-events`;
   SNS subscription list for the 10 `payout-updates-*` topics; MSK ACLs.
5. DB migrations / synthetic schema: real for the 5 core + vendor-payments (88 migrations, recoverable);
   missing for `api` runtime tables (monolith not running), Opfin, capital.
6. Kubernetes/Helm/IaC: `kube-manifests`, `spinacode`, `terraform-kong`, `edge` are intact and readable;
   no parser ingests them (§4.3).
7. Service identity / IAM: `authz` policy CSVs readable; the role→permission mapping lives in the AuthZ DB
   (`role_policy_mapping`) — export needed. Passport JWT shapes beyond merchant-key are graph-only.
8. Non-secret configuration / flags: DCS effective values (`skip_workflow_for_payroll`, `enable_payouts_to_cards`,
   pricing tiers), prod `applications.*` hosts (bare `env()` in monolith).
9. Service catalogue / dependencies: the RazorpayX cell list (`kube-manifests/cells/rzpx/prod/...`) is the
   canonical catalogue; devstack declares no `link_services` for X.
10. Third-party / bank contracts: RBL beyond v1, ICICI direct-tax `directTaxTin2`, M2P, Temporal Cloud.
11. Owner validation required: Payouts core (source-type topic subscriptions; `$validSourceTypes` mismatch),
    RX Apps (tax-payments status), Payroll (Opfin route payloads), Capital (ledger usage for cards).

**Workflow specifically** [V]:
- The real repository is **`razorpay/workflows`**, present and intact at `.local/repos-root/workflows`
  (HEAD `080d71a`, cloned 2026-09-04 17:33; M5 reconstruction commits are 2026-09-07 17:17/18:07). The M5
  source map's premise "the Workflows/Cadence decision engine repository was unavailable"
  (`reports/implementation/m5-source-map.md:3`) and the memory note "real workflow engine not cloned" are
  **wrong**: the repo was already on disk. The genuine blocker is its runtime dependency on a **Cadence
  server** (`go.mod` `go.uber.org/cadence v0.13.4`; `config/default.toml [cadenceWorker]` `hostPort
  localhost:7933`, domains `payouts`, `relay`, `asl`, `spr`; prod Cadence on Cassandra per
  `kube-manifests/prod/cadence/values.yaml:34-40`) plus MySQL (`[db] dialect mysql`; migrations as Go files in
  `internal/database/migrations/`, e.g. `20200704083621_create_states.go`, `20200712201722_create_workflows.go`).
  Dev artefacts exist: `deployment/dev/docker-compose.yml` (mysql), `build/docker/{dev,prod}/Dockerfile.{api,worker,migration}`,
  README §3 "Setup Cadence" (uber/cadence docker). Related helper repo: `workflow-guard` (404).
- Interface contract the real service must satisfy to replace the reconstruction (from the payouts side,
  `payouts/pkg/workflow/workflow_create.go` and `internal/routing/router/{workflow_routes,payout_internal_routes}.go`,
  and `proto/workflows/**`):
  1. Twirp `POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create` (`WorkflowTriggerRequest` → `Workflow`)
     with `callback_details` = `{state_callbacks:{created,processed}, workflow_callbacks:{approved,rejected}}`
     each `{url_path, method, headers, payload}` (`workflow_create.go:42-92`; workflows `internal/dto/workflow_model.go:85-94`);
     service name prefix `payouts_` (`:21`), callback base `/v1/payouts/payouts_internal/` (`:25`).
  2. WorkflowAPI `Recreate, Get, List, ListPending, ListByIds, Terminate, AddAssignee, RemoveAssignee, AdminAction`
     (`proto/workflows/workflow/v1/workflow_api.proto:14-35`); ActionAPI `Create, CreateDirectOnWorkflow,
     CreateWithEntityId, Get, List` (`action_api.proto:16-24`) for approve/reject; ConfigAPI, CommentAPI.
  3. Outbound callbacks: `POST /payouts_internal/{id}/approve|reject` (PS returns 200 even on error) and
     state-map create/update on `/v1/workflow/...` → PS forwards to monolith `/wf-service/state/callback`.
  4. Auth: basic-auth caller `workflow` (rzp_live) in payouts `[auth.*]`; callers in workflows
     `config/prod.toml:185-221`.
  5. Semantics the engine reconstruction encoded and the real service must be checked for: maker≠checker,
     N-of-M distinct approvers, terminal-state protection, idempotent retried callbacks, expiry
     (real: Cadence `ExecutionStartToCloseTimeout` only, no expiry callback — E59), approver identity
     (real: zero Passport/AuthZ in approve path — E58).
- Benchmark validity after replacement: **remain valid** — the Director/campaign machinery results
  (hypothesis dedup, reallocation, interruption recovery, cross-model reproduction), the hidden-verifier
  method, and the 12-scenario *contract* suite as a spec. **Must be rerun** — the blind benchmark itself
  (4 mutants are patches to `wfengine/core.py`, not to the real service), the "0 false findings on fixed"
  claim (the fixed reference is the reconstruction), and any finding about C51/C52 where the reconstruction
  is stronger than production. Findings must be re-labelled from "concerning the reconstruction" to
  "concerning workflows@080d71a".

---

## 6. Source-to-Pay execution readiness

Full detail: `agent-reports/06_s2p_readiness.md`.

| Repo | Source access | Builds today | Infra | Fixtures | Reuse from twin | Substitutes needed |
|---|---|---|---|---|---|---|
| vendor-payments `20c4f4d5` (2026-08-27) | clone decayed (85 files); **recoverable from intact packfile** [V]; 21 private modules resolve, `go mod verify` ok | **No** out of the box (missing git-ignored generated stubs); **Yes** after offline `buf`/`protoc-gen-twirp v8.1.2` codegen: all 8 binaries build [V] | MySQL (88 migrations), Redis, Kafka (~15 topics), Elasticsearch, external hosts (§12.6 of 02) | `tests/e2e/` harness with 25+ row builders + service clients (undocumented asset); `tds_categories` self-seed from migration `20200402152150` | kafka, localstack, redis, mysql, kong-lite, stork-capture, mozart-sim, cron-driver, seeds | ICICI direct tax, MastersIndia, Veryfi, BVS, abacus, metro, ufh, reminders, gimli, mailgun, ES |
| vendor-experience `df90df21` | decayed (28 files); recoverable | Yes after codegen (all cmds) | MySQL, **Cadence**, reads vendor-payments DB `[vpdb]` directly | — | same | Cadence (also needed by real `workflows`), portal stub |
| accounting-integrations `fc13a0b2` | decayed (55 files); recoverable | Yes after codegen (all cmds) | DB, Kafka consumer, reads vendor-payments DB `[VendorPaymentsDB]` directly | — | kafka | Zoho/Tally sinks |
| tax-payments / tax-compliance | absent (404) | — | — | — | — | SQS sink + Vault dev |

Claims re-verified with line numbers [V]: `vendor-payments/internal/payout/core.go:83-84`;
`config/prod.toml:122-123`; `internal/taxpayments/apicaller.go:21`; `workflows/internal/constants/constants.go:35-41`;
`payouts/internal/app/contact/type.go:16-21,35-48`; `payouts/config/prod.toml:368-378`. Claims corrected:
monolith-stub routes (F0.2); "module already vendors stubs" (false — git-ignored); "rpc clone failed"
(stale — `rpc` exists at `27388ee6`, but holds no S2P stubs).

Fidelity risks [V]: two services read the vendor-payments schema directly; the entire payout leg proxies
through the monolith (basic auth + idempotency headers) with no substitute today; mTLS Kafka vs arena
PLAINTEXT; generated DTOs unpinned; tracked config contains real-format secrets (`default.toml:93`).

**Smallest journey with real cross-domain value — J-TDS-min** [V, code-tied]:
Kafka `add-tds-entry` → `vendor-payments cmd/workerv2` (`internal/initiatetds`, `internal/taxpayments`) →
`GET v1/banking_accounts_internal/` (`apicaller.go:20`) → `POST v1/internalContactPayout/`
(`internal/payout/core.go:84,138`, headers `X-Razorpay-Account`, `X-Dashboard-User-Id`, `X-Payout-Idempotency`,
basic auth `rzp_live` `rxclient/core.go:27-32`) → payouts gate `type.go:35-48` (`vendor_payments` →
`rzp_tax_pay`) → `POST v1/payouts_internal/{id}/tax-payment-id` (`apicaller.go:21`) → FTS → SNS
`payout-updates-vendor-payments` (`payouts/config/prod.toml:375`) → monolith SourceUpdater →
`PayoutStatusChange` → Kafka `prod.x.vendor-payments.accounting-payouts.status-update`
(`vendor-payments/config/prod.toml:273`) → accounting-integrations `[VendorPaymentUpdateListener]`
(`config/prod.toml:196`). Build cost: 3 additive monolith-stub routes + 1 container (`cmd/workerv2` +
`cmd/migration`, both verified building); no ES, Cadence, ICICI, OCR, or workflow engine. Assertions: A1 payout
row `contact_type=rzp_tax_pay`, `source_type=tax_payments`; A2 `tax_payment_id` tag-back equality; A3 negative
control with app `xpayroll` → `AppNotPermittedToCreatePayoutOnThisContactType`; A4 topic-name equality across
two independently built services (string today, live message later). This is the assurance surface no
current journey exercises (M4's F-M4-001 came from the payouts side only).

**Overlap with unfinished M6** [V]: M6 stages `docker-compose.yml` (+511), `config/arena.yaml` (+12) and
`substitutes/workflow-engine/wfengine/*.py`; it does not touch `monolith-stub`. J-TDS-min avoids the workflow
leg; land M6 first and the only shared edits are additive compose/arena entries.

**Readiness verdict:** Source-to-Pay is executable after three preconditions: (1) re-clone or recover the
three repos to a durable path; (2) run the offline codegen step once and pin the generated DTOs; (3) add
the three monolith-stub routes. It is not "ready today" as the 2026-09-07 report implied.

---

## 7. Decision input (what this means for "continue Payouts vs merge S2P vs build refresh pipeline")

Observed facts only: M6 is unaccepted and uncommitted with 8 failing gates that are mostly evidence
hygiene (boot artifact, tracking, manifest) rather than new fidelity; the refresh pipeline exists only in
that same uncommitted M6 tree and has no trigger, no fetch, no CI, and a perishable source root; S2P's
first journey costs 3 routes + 1 container once source is durable. The cheapest step that de-risks all
three paths is the same: commit and re-clone (§2 sequence steps 1 and 3).
