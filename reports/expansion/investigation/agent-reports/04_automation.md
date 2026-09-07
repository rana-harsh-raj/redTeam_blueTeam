# 04 — Automation machinery for twin refresh

Investigation date: 2026-09-08. Repo: `/Users/rana.singh/rzp-payouts-architecture`,
branch `milestone-6-complete-payouts-domain`, HEAD `2613f38`.
Working tree is **dirty**: another session holds uncommitted M6 edits (20 staged/modified
tracked files + 21 untracked paths). Nothing in this investigation modified a tracked file.

Labels: **VERIFIED** = a command was run or a file on disk was read and quoted.
**INFERRED** = derived (e.g. from filesystem timestamps) or asserted only in prose.

> **Side effect disclosure (VERIFIED).** While probing `ENV2_COMPOSE/config/generate.py --help`,
> the script has no `argparse` (`grep -n "argparse\|sys.argv" ENV2_COMPOSE/config/generate.py`
> → only `clean = "--no-clean" not in sys.argv` at line 466), so the unknown flag was ignored
> and the generator **ran**, re-rendering `ENV2_COMPOSE/generated/` (19 files, 6 services).
> That directory is git-ignored (`git check-ignore -v ENV2_COMPOSE/generated/payouts/arena.toml`
> → `ENV2_COMPOSE/.gitignore:7:generated/`) and `git status --short ENV2_COMPOSE/generated`
> is empty, so **no tracked file changed**. The render is deterministic from
> `config/arena.yaml` + `secrets/*.txt` + `config/templates/base/**` (all untouched), the
> files are bind-mounted read-only and are not re-read by running containers, and
> `scripts/up.sh` regenerates them on every boot anyway. Impact assessed as nil; the only
> lost state is the previous mtimes. Flagged here rather than buried.

---

## 0. Two generations of machinery

There are **two overlapping systems** in the tree, and conflating them causes wrong conclusions:

| | M1–M5 (committed, `HEAD 2613f38`) | M6 (UNCOMMITTED, another session) |
|---|---|---|
| snapshot/pinning | `reports/implementation/BUILD_PROVENANCE.md`, `acceptance-manifest.json`, `m4-direct-e2e-evidence-manifest.json` (+`.sha256`) | `scripts/snapshot/capture.py` → `reports/domain/snapshots/<UTC>.json` |
| change detection | none (manual) | `scripts/snapshot/diff.py` (exit 3 = changed) |
| blast radius | none | `scripts/snapshot/affected.py` |
| recipes | none | `scripts/snapshot/recipes.py` → 100 `reports/domain/recipes/*.yaml` |
| refresh driver | none | `scripts/snapshot/refresh.py daily\|weekly\|status` |
| graph | hand-authored `build_graph.py` in the clone root | `scripts/domain/build_graph.py --strict` (parts→merge) |
| journeys | verifier golden-run + `m4_direct_journeys.py` | `RED_LOOP/m6/journeys/run.py` (12 j_*.py drivers) |
| acceptance | `m4_acceptance.py` (74 gates), `m5_acceptance.py` (38 gates) | `RED_LOOP/surface/m6_acceptance.py` (untracked) |

`git status --short` (VERIFIED) shows `?? scripts/snapshot/`, `?? scripts/domain/`,
`?? RED_LOOP/m6/`, `?? RED_LOOP/surface/m6_acceptance.py`, `?? reports/domain/{snapshots,refresh,recipes,parts}/`,
` M Makefile`. **The entire M6 refresh subsystem is untracked / uncommitted.**

---

## 1. Machinery inventory

### 1.1 Capturing approved repository snapshots

| Path | What it is | Status |
|---|---|---|
| `.local/repos-root` | one-line file naming the pristine clone root: `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture` | VERIFIED (`cat .local/repos-root`) |
| `ENV2_COMPOSE/build/prepare-repos.py` (379 lines) | *"Prepare fresh local build copies from exact Git HEADs; never edit source clones."* Extracts `git archive` of HEAD, Gitleaks 8.30.1 scan, refuses to overwrite a destination, writes per-file provenance | VERIFIED |
| `ENV2_COMPOSE/build/check-inputs.py` | *"Verify admitted build copies and checksums before invoking build-host.sh."* `--verify-modules` runs `go mod verify`. Exits nonzero on missing/changed/unapproved input | VERIFIED |
| `.local/twin-repos/accepted/` | the admitted source root (5 core repos) | VERIFIED |
| `.local/twin-repos/build-evidence-<UTC>/` | 6 dirs on disk. Latest `build-evidence-20260905T160213Z` holds `provenance.json` (1.97 MB), `build-results.json`, `image-assets.json`, per-service `*.gitleaks.json`, `*-build.log`, `*-asset-readability.json`, `post-build-verification.json` | VERIFIED (`ls -la`) |
| `.local/twin-build-tools/` | pinned codegen binaries built from cached modules: `payouts-buf` (54 MB), `ledger-buf` (51 MB), `{payouts,ledger}-protoc-gen-go`, `{payouts,ledger}-protoc-gen-twirp`, `ledger-protoc-gen-twirp-module/` | VERIFIED |
| `reports/implementation/BUILD_PROVENANCE.md` (22.8 KB) | narrative + tables: per-service source commit, admitted file count, build exit, build time; generator versions; image IDs + sizes; reproduction script | VERIFIED |
| `reports/implementation/acceptance-manifest.json` | 17-key pointer map binding an acceptance to its run roots and evidence dirs | VERIFIED (quoted §3) |
| `scripts/snapshot/capture.py` (M6, untracked) | pins the **whole** twin in one JSON | VERIFIED |

`provenance.json` for the accepted tree (VERIFIED):
```
status = prepared   destination = .../.local/twin-repos/accepted
keys: schema_version, created_at, source_root, destination, status, scanner_version,
      repositories, patch_files, classification, patch_script_sha256, prepared_tree
payouts 1425 files | ledger 738 | fts 1101 | cfa 475 | x-balances 539
```

`BUILD_PROVENANCE.md` source pins (VERIFIED, verbatim):
```
payouts    4bf3dbf9239feadea6d65ca90c893a988e116173   1425 files  exit 0  26.08 s
ledger     471ff4d5321b6b99a7965882d18f572a1adf5194    738 files  exit 0  22.29 s
fts        2a09e763116db47a2f28553c677ad73ef8133ebf   1101 files  exit 0  14.15 s
cfa        d488558e162c08e9fa04dff005f27d846ce88ad2    475 files  exit 0  14.87 s
x-balances 1a21c0f9111da14125d839dc0dfe8d980740f207    539 files  exit 0  11.74 s
proto (RPC codegen source)  52682577d79d7237944e9fbb10477a1721623065
```

### 1.2 Pinning repo SHAs and image/config hashes

- **Evidence manifest (M4):** `RED_LOOP/surface/m4_evidence_manifest.py {build,verify}`.
  Emits `reports/implementation/m4-direct-e2e-evidence-manifest.json` + a companion
  `.json.sha256`. VERIFIED head:
  ```json
  {"schema_version":"1.0","manifest_kind":"m4-direct-e2e-evidence",
   "generated_at":"2026-09-07T11:00:06Z",
   "git":{"branch":"milestone-5-autonomous-discovery-workflows",
          "commit":"0cb90eb5a5b152698086de06e7fc44d4f94aef9a","worktree_clean":false},
   "counts":{"artifacts":19,"committed":19,"external":0,"total_bytes":479325},
   "policy":{"external_roots":["RED_LOOP/runs/"],
             "note":"Only SHA-256 + size are recorded, never contents; ..."}}
  ```
  companion: `15462fa61b317c1a06555ba8411d35780020c0b3a99118fb10080fca66bbdf57  m4-direct-e2e-evidence-manifest.json`
- Only **3** `*.sha256` files exist tree-wide (`find . -name "*.sha256" -not -path "./.git/*"`):
  the M4 manifest companion, `RED_LOOP/archive/evidence-manifest.json.sha256`, and the
  same file inside a `.claude/worktrees/` agent worktree. VERIFIED.
- **Arena boot fingerprint:** `ENV2_COMPOSE/scripts/fingerprint.py`, written by `up.sh` to
  the git-ignored `ENV2_COMPOSE/.runtime/arena-fingerprint.json`. Every run-producing
  script copies it into its run dir. VERIFIED keys:
  `boot_id, compose_project, config_digest, config_hashes, git_head, images,
   images_available, images_unresolved, note, recorded_at, route_profile, schema_version,
   service_container_ids`. Live value: `boot_id=06330f7e-3099-4299-ab0c-9b9d50c43899`,
  `git_head=39fe41e54d7ee7063ebdbac546334023b1aea5d5`, `route_profile=monolith`,
  `config_digest=839a4da8c312...`, plus per-image `sha256:` digests.
  **Note (VERIFIED):** the file's mtime is `6 Sep 19:10` while several containers show
  `Up 5 minutes` — the fingerprint is **not** rewritten on partial restarts, so it can
  drift from the live arena.
- **`reports/implementation/compose-materialization-validation.json`** (6.4 KB): `status: passed`,
  `project: env2_compose`, then per-named-volume (`config-cfa`, `config-fts`, …) the list of
  consuming services and mount targets. It validates that every generated config volume is
  materialized into the right containers — it is a **compose wiring check, not a hash pin**. VERIFIED.
- **M6 snapshot** (`reports/domain/snapshots/20260907T174026Z.json`, 738 KB) — the real
  pinning artifact. VERIFIED top-level keys:
  `_path, accepted_root, captured_at, compose, config, contracts, digest, flags, graph,
   graph_version, images, images_reason, note, provenance, recipes, repos, repos_root,
   schema_version, schemas, twin` — with **`repos` = 38 entries**. Sample:
  ```json
  "accounting-integrations": {"kind":"clone","repository":"razorpay/accounting-integrations",
    "sha":"fc13a0b2a38214374e80a16af3ed4465b2d84ef1","dirty":false,
    "head_date":"2026-09-03T08:18:48+05:30",
    "accepted_matches_provenance":null,"accepted_source_commit":null,"accepted_tree_digest":null}
  ```
  `latest.json` is a 351-byte pointer: `{"captured_at","digest","graph_version","latest","path"}`
  → `digest b003882c5c9c…`, `graph_version 92f12bdd39c5…`, `latest 20260907T174026Z.json`.

### 1.3 Rebuilding the architecture graph — four separate builders

1. **`<clone-root>/build_graph.py`** (342 lines) — path from `.local/repos-root`.
   Emits `reports/PAYOUTS_SERVICE_GRAPH.{json,mmd,graphml}` (181 KB / 19 KB / 228 KB on disk).
   **No `argparse`**; `OUT` is a hardcoded `Path('/Users/rana.singh/rzp-payouts-architecture/reports')`.
   Nodes/edges are ~300 hand-authored `N(...)`/`E(...)` literals, each citing
   `repository/file_path/symbol/commit` from a hardcoded `SHA` dict. Only runtime inputs are
   optional `graph_extra.json` / `graph_delta.json` siblings. **Deterministic but not
   source-derived** — new evidence requires editing the file. VERIFIED.
2. **`RED_LOOP/surface/m4_world_model.py`** (55,672 bytes, 1127 lines) — M4 task **T21
   "Stretch H"**. Docstring: *"A generated (not hand-drawn) typed graph… PARSES committed repo
   artifacts and emits three views… so re-running after a source change re-derives it."*
   Parses `reports/implementation/m4-source-map.md`, `m4-fidelity-matrix.csv`,
   `m4-route-coverage.json`, `m4-invariant-results.json`, `m4-direct-journeys.json`,
   `m4-direct-e2e-replay.json`, `TWIN_SPEC/{route-matrix,components,acceptance-invariants}.yaml`,
   `ENV2_COMPOSE/docker-compose.yml`, `ENV2_COMPOSE/seeds/localstack/init-queues.sh`
   (via stdlib `ENV2_COMPOSE/config/_miniyaml.py`). Emits
   `reports/implementation/m4-world-model.{json,graphml,mmd}` with per-input sha256 provenance.
   No CLI args; determinism via `M4_WM_GENERATED_AT` / `SOURCE_DATE_EPOCH`; self-validates
   (exit 1). **This is the only genuinely re-derivable graph in the committed tree.** VERIFIED.
3. **`scripts/domain/build_graph.py`** (311 lines, M6, untracked) — the real pipeline:
   `python3 scripts/domain/build_graph.py [--parts reports/domain/parts] [--out reports/domain] [--strict]`.
   Merges 8 lanes from `reports/domain/parts/*.json` (`payouts-core`, `fts-mozart`,
   `money-statements`, `identity-ingress`, `external-config-deploy`, `twin-inventory`,
   `m6-journeys`, `zz-runtime-overlay`), validates ids/kinds/fidelity against
   `reports/domain/SCHEMA.md`, resolves conflicts by `LANE_RANK` (twin-inventory 100 <
   m6-journeys 150 < zz-runtime-overlay 200). Emits `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.{json,
   nodes.csv,edges.csv,mmd,graphml}` (2.7 MB JSON on disk), `GRAPH_STATS.json`,
   `FIDELITY_CLASSIFICATION.csv`. Feeders: `scripts/domain/inventory.py`,
   `scripts/domain/journeys_part.py`, `scripts/domain/runtime_overlay.py`
   (the last one `yaml.safe_load`s `docker-compose.yml` + live `docker ps`). VERIFIED.
4. **`/Users/rana.singh/rzp-payouts-architecture-expansion/reports/expansion/scripts/build_graph_patch.py`**
   (470 lines, git worktree on branch `graph-expansion-adjacent-domains`, HEAD `d99aed4`).
   Additive/relabel-only overlay on `PAYOUTS_SERVICE_GRAPH.json` (base never mutated).
   Emits `reports/expansion/GRAPH_PATCH.json`, `PAYOUTS_SERVICE_GRAPH.expanded.json`,
   `GRAPH_PATCH_VALIDATION.json`. No CLI args; exits 1 on validation failure. VERIFIED.

**`ARCHITECTURE_EXPLORER/`** is *not* a graph builder. `manage.py {install,build}` only
validates and copies to `dist/`; `data/architecture.json` (16,679 lines) is hand-authored and
**nothing in the repo writes it**. `server.py` + `run-golden.py` drive live golden runs against
the arena and write sanitized traces to `reports/implementation/runs/explorer-*`. VERIFIED.

### 1.4 Detecting changed services / contracts

- `reports/implementation/contract-source-check.json` (285 bytes) is a **static tally, not a diff**.
  VERIFIED verbatim:
  ```json
  {"cron_routes_verified":6,"ledger_distinct_primary_keys":44,
   "dcs_bool_field_number_verified":1,"fts_source_account_mapping_verified":true,
   "scope":"source contract and seed structure only; no live core assertions",
   "route_profiles_rendered_config_validation":4}
  ```
- `reports/implementation/CONTRACT_FIXES.md` (14.2 KB) is a **manual** evidence document —
  a table of "Change / classification | Confirmed source contract | Local implementation /
  limits", each row citing `api/app/Models/Payout/Repository.php:5313`,
  `payouts/internal/routing/router/cron_routes.go:56,77,94`, etc. It pins source revisions
  (payouts `4bf3dbf9…`, api `2d665f91…`, ledger `471ff4d5…`, fts `2a09e763…`,
  config-proto `a6b20103…`). Written by hand, not generated. VERIFIED.
- **Real diff machinery exists only in M6 (untracked):**
  `python3 scripts/snapshot/diff.py <old.json> <new.json> [--json PATH]`
  *"Exit 0 when nothing changed, 3 when something did. Sections: repos (with commit ranges),
  contracts, schemas, flags, config, compose services / recipes, images, graph (version +
  node/edge id sets)."* VERIFIED via `--help`.
- Blast radius: `python3 scripts/snapshot/affected.py <diff.json> [--graph PATH] [--json OUT]`
  emits `rebuild[]`, `rerun_journeys[]`, `regen_config: bool`, plus `restart`/`reseed`/
  `rerun_migrations` with a reason per entry. Uses `PAYOUTS_FUNCTIONAL_GRAPH.json` when
  present; otherwise a hardcoded map and sets `fallback_used`. VERIFIED via `--help`.

### 1.5 Updating service recipes

- **Config render:** `ENV2_COMPOSE/config/generate.py` (24.8 KB) reads
  `config/arena.yaml` (topology/flags, no credentials), `secrets/*.txt` (values),
  `config/templates/base/<svc>/*.toml` (verbatim from the proven-to-boot build spike, with
  `{{TOKEN}}` placeholders) → writes `ENV2_COMPOSE/generated/<svc>/*.toml` + `<svc>.env`,
  bind-mounted read-only, never baked into an image. VERIFIED output (19 files, 6 services):
  ```
  wrote .../generated/payouts/{default,arena,e2e}.toml, payouts.env (0 vars)
  wrote .../generated/ledger/{default,arena}.toml, ledger.env (4 vars)
  wrote .../generated/cfa/…, xbalances/…, fts/{env.default,holiday,env.arena,env.arena-migration}.toml,
        fts.env (78 vars), mozart-mock/env.arena.toml
  config/generate.py: OK, rendered 6 service configs
  ```
  Both `arena.yaml` and `generate.py` are **modified-uncommitted** in this session's tree.
- **Build/run/seed recipes (M6, untracked):** `python3 scripts/snapshot/recipes.py [--out DIR]
  [--list] [--no-docker]` → **100 files** in `reports/domain/recipes/*.yaml`
  (`asv-stub`, `bankingaccounts-stub`, `cfa-migrate`, `cfa-server`, `cron-driver`, `dcs-stub`, …)
  + `reports/domain/SNAPSHOT_MANIFEST.json` (84 KB). Each recipe carries `repository, sha,
  build_command, runtime, health_check, dependencies, schemas_and_contracts,
  configuration_hash, database_migrations, synthetic_seed_generator, external_replacements,
  fidelity_label, fidelity_reason`. VERIFIED (`ls reports/domain/recipes | wc -l` → 100).

### 1.6 Ingesting API / event / schema / IAM / deployment info

| Domain | Parser | Emits | Verdict |
|---|---|---|---|
| proto | `ENV2_COMPOSE/build/prepare-repos.py` — extracts `.proto` from `git archive`, resolves `import` closure offline from `GOMODCACHE`/grpc-gateway googleapis, runs pinned `buf`/`protoc-gen-go`/`protoc-gen-twirp` from `.local/twin-build-tools` | generated Go RPC stubs, `<repo>-proto-inputs/`, build logs | real build-time compiler |
| proto (fingerprint) | `scripts/snapshot/capture.py::_proto_files` — `rglob('*.proto')` under `<repos_root>/proto/<module>` for payouts+ledger, sha256 each | `snapshots/<UTC>.json.contracts` | real hashing, no semantics |
| Kong / gateway | **none.** `terraform-kong/base/api/api.tf`, `prod/edge/global-plugins.tf` appear only as hand-typed `file:line` citations inside `build_graph.py` `E(...)` calls. `scripts/domain/inventory.py` records `("terraform-kong","", "Edge routes/plugins incl. payouts-proxy-cutover")` — existence + SHA only. `ENV2_COMPOSE/substitutes/kong-lite/server.py` is a *substitute*, not a parser | — | **GAP** |
| Kubernetes | **none semantic.** `scripts/domain/inventory.py` `DEPLOY_ARTIFACTS` (lines 21–35) is a hardcoded list of `kube-manifests/cde/<service>` paths (payouts, fts, ledger, cfa, x-balances, banking-accounts, x-account-statements, workflows, batch, stork, validx, mozart) + `templates/payouts`, `helmfile/charts/payouts` — existence + `git rev-parse HEAD` only. Real YAML parsing exists only for the twin's own `docker-compose.yml` (`scripts/domain/runtime_overlay.py`) | `REPOSITORY_INVENTORY_M6.csv`, `parts/zz-runtime-overlay.json` | **GAP** |
| IAM / authz | **none.** `authz/scripts/policies/*.csv`, `api/app/Models/User/BankingRole.php`, `UserRolePermissionsMap.php` are hand-cited in `build_graph.py` and in prose lane `reports/domain/parts/identity-ingress.md` | `PAYOUTS_SERVICE_GRAPH.json`, `parts/identity-ingress.json` | **GAP** |
| Kafka / events | **none schema-level.** `runtime_overlay.py` maps compose names `payouts-kafka-*` → `worker:payouts/kafka-*` (structural). `ENV2_COMPOSE/seeds/localstack/init-queues.sh` (SQS/SNS) is parsed by `m4_world_model.py` | `m4-world-model.json`, `recipes/kafka.yaml` | partial |

### 1.7 Recompiling affected services

- `ENV2_COMPOSE/build/build-host.sh` — `REPOS_ROOT=… ./build/build-host.sh [all|payouts|…]`.
  Builds Go binaries on the **host** (go 1.26, toolchain directive covers 1.24–1.26), copies
  only compiled binaries into a credential-free runtime image
  (`build/runtime-only.Dockerfile`). Installs public IFSC/bank JSON at 0644, binaries + CFA
  entry script at 0755. VERIFIED.
- Per-service Dockerfiles: `payouts.Dockerfile`, `ledger.Dockerfile`, `fts.Dockerfile`,
  `cfa.Dockerfile`, `xbalances.Dockerfile`, `mozart.Dockerfile`, `runtime-only.Dockerfile`,
  plus `build/arena-patches/` (`goutils-dcs-v1.5.2 / v1.7.1 / v1.7.3`) applied by
  `build/apply-arena-patches.sh` — **to the copies only**. VERIFIED.
- `ENV2_COMPOSE/build/record-rebuild.py` — *"Rebuild one core service image with build-host.sh
  and record build evidence. Writes a new `.local/twin-repos/build-evidence-<UTC>` derived
  from a base evidence directory; every file is copied, then the rebuilt service's build log,
  `build-results.json` entry, `image-assets.json` entry and (for payouts/cfa) the
  `<svc>-asset-readability.json` are replaced with fresh measurements."*
  Network policy `GOPROXY=off GOFLAGS=-mod=readonly GOTOOLCHAIN=local`. This is the
  **incremental-rebuild primitive** the M6 `refresh.py daily --execute` calls. VERIFIED.
- Image inventory pinned in `BUILD_PROVENANCE.md` (VERIFIED):
  `rzp-arena/payouts:v1-candidate sha256:e029d639… 116.9 MiB`, `ledger sha256:878be96a… 90.8`,
  `fts sha256:dac4c76a… 84.9`, `cfa sha256:1b6cf763… 76.6`, `xbalances sha256:980f8333… 62.5`
  — all linux/arm64, run as `appuser`.

### 1.8 Running baseline journeys after refresh

- `ENV2_COMPOSE/scripts/golden-run.sh` — healthcheck kong-lite (60 s), optional verifier
  build, `preflight/preflight.py`, then a `docker compose run` of the verifier with
  `--junitxml=/results/junit.xml`. Creates
  `RUN_DIR=${ARENA_RUN_DIR:-…/reports/implementation/runs/$(date -u +%Y%m%dT%H%M%SZ)-$$}`
  and copies `.runtime/arena-fingerprint.json` into it — *"the acceptance gate refuses a run
  with no fingerprint or with a fingerprint that disagrees with the tree."* VERIFIED.
- `ENV2_COMPOSE/scripts/scenarios.sh {route|bank|kafka}` — supplemental suites, same
  fingerprint binding, run dir `runs/$family-<UTC>-$$`. VERIFIED.
- `ENV2_COMPOSE/scripts/local-acceptance.py` (70 KB) + `test_local_acceptance.py` (39 KB);
  imports `config_hashes()` / `config_digest()` from `fingerprint.py` so staleness is
  recomputable from the working tree with no Docker. VERIFIED.
- `scripts/m4.sh test` chains 4 drivers: `golden-run.sh` (26/0/0 baseline) →
  `verifier/m4_boundary.py --no-build` → `RED_LOOP/surface/m4_bas_ingest.py` →
  `m4_xas_ledger.py`. VERIFIED (read from `scripts/m4.sh`).
- M6 (untracked): `RED_LOOP/m6/journeys/run.py` with 12 drivers — `j_direct`, `j_shared`,
  `j_bulk`, `j_approval`, `j_queued`, `j_scheduled`, `j_onhold`, `j_failure`, `j_pricing`,
  `j_webhooks`, `j_idempotency`, `j_accounting` (+ `framework.py`, `bulk_client.py`).
  Emits `reports/implementation/m6-journeys.json` (untracked, present). VERIFIED.

### 1.9 Immutable snapshot IDs — the schemes

| Scheme | Pattern | Real examples on disk (VERIFIED) |
|---|---|---|
| verifier run root | `reports/implementation/runs/<UTC>-<PID>` (`date -u +%Y%m%dT%H%M%SZ`+`$$`, from `golden-run.sh`) | `20260905T112903Z-11943` |
| milestone run root | `runs/m1-<UTC>` | `m1-20260905T161108Z`, `m1-20260905T161523Z`, `m1-20260905T163551Z`, `m1-20260905T165255Z` |
| named/profile run | free-form | `replay1`…`replay20`, `profile-direct`, `profile-kafka`, `final-acceptance`, `golden-monolith-final`, `bank-monolith` |
| audit / explorer run | `<kind>-<UTC>[-<hex6>]` | `audit-20260906T155953-f85ce0`, `explorer-20260905T131651Z-bab19350`, `claude-audit-kafka-repro-20260905T152544Z` |
| red-loop campaign | `RED_LOOP/runs/camp-<UTC>-<hex6>` (each with `manifest.json`) | `camp-20260906T150926Z-d03858`, `camp-20260906T200337Z-7511c5`, `m4lifeval-20260906T193754Z-b94c76`, `calib-20260906T095604Z` |
| clean-boot evidence | `RED_LOOP/runs/cleanboot-<instance-id>` | `cleanboot-m31-clean-a`, `cleanboot-m4-final2-b`, `cleanboot-m31-clean-a-pollutionproof` |
| build evidence | `.local/twin-repos/build-evidence-<UTC>` | `…-20260905T112910Z`, `…-113009Z`, `…-113121Z`, `…-113414Z`, `…-160213Z` |
| domain snapshot (M6) | `reports/domain/snapshots/<UTC>.json` (+ `<UTC>-1.json` on collision) + `latest.json` pointer | `20260907T171829Z.json`, `…171833Z`, `…171954Z`, `…171958Z`, `20260907T174026Z.json` |
| refresh plan (M6) | `reports/domain/refresh/<UTC>-{daily,weekly,diff}.json` | `20260907T171833Z-daily.json`, `20260907T171846Z-weekly.json`, `20260907T171958Z-diff.json` |
| boot identity | `boot_id` UUID4 in `arena-fingerprint.json` | `06330f7e-3099-4299-ab0c-9b9d50c43899` (live), `232e25bf-f908-4476-877f-f9c69b5465fd` (m4-final2-b) |
| content digest | sha256 | snapshot `digest b003882c5c9c…`, `graph_version 92f12bdd39c5…`, manifest self `15462fa6…` |
| git tags | 5 (`git tag -l`) | `twin-v1.0`, `red-loop-m3.1`, `red-loop-m4`, `assurance-m4.1-reproducible`, `assurance-m5-autonomous-discovery` |

`acceptance-manifest.json` binds an acceptance to exactly these ids (VERIFIED verbatim):
```json
{"replay_a":"reports/implementation/runs/m1-20260905T165255Z/replay-a",
 "replay_b":"reports/implementation/runs/m1-20260905T165255Z/replay-b",
 "final_golden":"reports/implementation/runs/m1-20260905T165255Z/replay-a/golden",
 "profile_kafka":"reports/implementation/runs/m1-20260905T165255Z/profile-kafka",
 "audit_dir":"reports/implementation/audits/m1-current-boot-20260905T165742Z",
 "build_evidence":".local/twin-repos/build-evidence-20260905T160213Z",
 "declared_deviations":"ENV2_COMPOSE/config/declared-deviations.yaml",
 "expected_failures":"TWIN_SPEC/expected-failures.yaml", …}
```

### 1.10 Provisioning / resetting disposable twins

- `scripts/m4.sh boot <instance>` → `ENV2_COMPOSE/scripts/clean-boot.sh <instance-id> [workdir]`.
  *"Boots a fully isolated arena instance from EMPTY volumes in a separate working copy (so
  host-side generated secrets/config/.runtime never touch the live tree), runs the original
  26-test verifier in frozen-baseline mode (route policy OFF) with the same-window egress
  audit… Teardown is a separate step so results can be examined first."*
  Default work dir `.local/m31-clean/<instance>/ENV2_COMPOSE`,
  evidence `RED_LOOP/runs/cleanboot-<instance>`. Step 1 is a **fail-closed** preflight:
  `instance-plan.py <id> --json` then abort unless `safe_to_launch`. VERIFIED.
- `scripts/m4.sh boot --live` → `ENV2_COMPOSE/scripts/up.sh` (in-place; 8 numbered phases per
  `up.log`, ends by writing the arena fingerprint).
- `ENV2_COMPOSE/scripts/instance-plan.py` — deterministic derivation from `sha256(instance_id)`:
  `ARENA_SUFFIX=-{id}`, `COMPOSE_PROJECT_NAME=env2c_{id}`, two consecutive `/24` subnets from
  `172.28.[32..230].0/24`, host port `18100 + (h % 800)` (range 18100–18899). Live default is
  project `env2_compose`, port `18080`, subnets `172.28.16.0/24` + `172.28.17.0/24`.
  Fails closed on any shared network/volume/container/port. VERIFIED.
- `scripts/m4.sh clean <id>` → `ENV2_COMPOSE/scripts/instance-cleanup.py`, which
  *"refuses the live default plan (project env2_compose / empty suffix)"*. Also
  `reset.sh`, `down.sh`, `restore.sh`, `snapshot.sh`. VERIFIED.
- `RED_LOOP/m6/clean_boot.py` (untracked) is the M6 weekly-chain boot step.
- `DAYTONA/` — remote provisioning: **not operational**, see §4.5.

---

## 2. Demonstrated workflow (commands actually run, output trimmed)

All outputs saved under
`/private/tmp/claude-502/.../scratchpad/inv/`.

### 2.1 `make help`
```
$ cd /Users/rana.singh/rzp-payouts-architecture && make help          # exit 0
scripts/m4.sh — Milestone 4 one-command entry-point dispatcher.
  boot / self-check / test / assurance-run / replay / acceptance / evidence-verify / clean
```
The Makefile itself (uncommitted, ` M Makefile`) exposes 24 targets in 4 groups: `m4-*` (8),
`m41-*` (4), `m5-*` (7), `m6-*` (6 — `m6-snapshot`, `m6-snapshot-diff`, `m6-affected`,
`m6-recipes`, `m6-refresh-daily`, `m6-refresh-weekly`, `m6-snapshot-test`).

### 2.2 `make m41-canonical-paths` (exit 0)
```
OK: 19 mandatory evidence paths are canonical (tracked, not ignored, present).
```

### 2.3 `make m41-evidence-verify` (exit 0)
```json
{"ok": true,
 "manifest": ".../reports/implementation/m4-direct-e2e-evidence-manifest.json",
 "self_sha256_ok": true, "companion_sha256_ok": true,
 "artifacts_checked": 19, "mismatches": [], "missing": [], "missing_expected_at_build": []}
```

### 2.4 M4 acceptance, dry-run (redirected to scratchpad so the tracked JSON is untouched)
```
$ python3 RED_LOOP/surface/m4_acceptance.py --dry-run --out <scratchpad>/m4-acceptance-dryrun.json
{"tested_commit":"2613f383297c7c3b5c0831c26c5c7472494b6e7b",
 "counts":{"total":74,"pass":73,"pending":1,"fail":0},
 "accepted":false,"pending_gates":["G71"],"unmet_gates":[]}
```
The single pending gate is an artifact of the other session's dirty tree, not a regression:
```json
{"gate_id":"G71","group":"REPLAY_EVIDENCE","title":"Final working tree is clean",
 "status":"pending","evidence_pointer":"(git status --porcelain)",
 "notes":"PENDING: working tree dirty during active implementation; must be clean at the evidence commit."}
```
(The recorded canonical run in `reports/implementation/m4-direct-e2e-acceptance.json` is 74/74.)

### 2.5 M5 acceptance — confirmed verify-only before running
```
$ grep -n "subprocess\|docker\|compose\|Popen\|os.system" RED_LOOP/surface/m5_acceptance.py
18:import subprocess
49:        return subprocess.run(["git", "-C", str(REPO)] + args, ...)
```
Only `git`. No docker, no boot. Run with `--out` to scratchpad (exit 0):
```
[PASS] M5-03 workflow scenario suite all-passed (12/12)
[PASS] M5-20 >=15 unique hypotheses (106)
[PASS] M5-21 >=30 nonduplicate experiments reached service (88)
[PASS] M5-22 >=10 valid baseline/control operations (189)
[PASS] M5-27 >=3 of 4 hidden defects discovered (4 defects: ['counting','cross_org','separation','terminal'])
[PASS] M5-29 zero accepted findings against the fixed reference (0)
[PASS] M5-37 annotated tag assurance-m5-autonomous-discovery exists (2613f383297c)
38/38 gates  accepted=True
```

### 2.6 M6 refresh status (read-only; the current, richest workflow)
```
$ python3 scripts/snapshot/refresh.py status                          # exit 0
twin refresh status @ 2026-09-07T20:26:19Z
  snapshots       : 5 (latest reports/domain/snapshots/20260907T174026Z.json, captured 2026-09-07T17:40:25Z)
  recipes         : 100 in reports/domain/recipes
  manifest        : reports/domain/SNAPSHOT_MANIFEST.json (generated 2026-09-07T17:19:50Z, 100 recipes)
  functional graph: present 92f12bdd39c5
  journey runner  : RED_LOOP/m6/journeys/run.py (present)
  docker          : available
  build evidence  : .local/twin-repos/build-evidence-20260905T160213Z
  refresh plans   : 4 (…20260907T171846Z-weekly.json, …20260907T171958Z-daily.json, …-diff.json)
```

### 2.7 Live arena state
```
$ docker ps -q | wc -l          →  67
$ docker ps --format '{{.Names}}\t{{.Status}}' | head
env2_compose-payouts-api-1                       Up 22 hours (healthy)
env2_compose-kong-lite-1                         Up 5 minutes (healthy)
env2_compose-monolith-stub-1                     Up 5 minutes (healthy)
env2_compose-fts-web-1                           Up 22 hours (healthy)
…67 containers, all (healthy)
```

### 2.8 The intended end-to-end refresh (documented in `scripts/snapshot/README.md`)
```
daily  : refresh.py daily [--execute]
         capture.py → diff.py vs latest.json → affected.py → refresh/<UTC>-daily.json
         --execute: record-rebuild.py per affected CORE service, then
                    RED_LOOP/m6/journeys/run.py --only <ids>
weekly : refresh.py weekly [--execute]
         1 prepare-repos.py --destination .local/twin-repos/refresh-<UTC>
         2 check-inputs.py <new build-evidence>/provenance.json --verify-modules
         3 REPOS_ROOT=… ARENA_TAG=… build-host.sh all
         4 RED_LOOP/m6/clean_boot.py          ← only step that touches the live arena
         5 RED_LOOP/m6/journeys/run.py        (full suite)
         6 RED_LOOP/surface/m6_acceptance.py
```

---

## 3. What is MISSING for event-triggered incremental refresh + periodic full rebuild

Each gap cites the search that establishes the absence.

### 3.1 No CI system of any kind — VERIFIED
```
$ ls -la .github
ls: .github: No such file or directory
$ find . -not -path "./.git/*" \( -name ".gitlab-ci.yml" -o -name "Jenkinsfile" \
      -o -name "buildkite*" -o -name ".circleci" \)
(no output)
$ find . -maxdepth 4 -name "*.yml" -path "*workflow*" -not -path "./.git/*"
./RED_LOOP/surface/workflow-sim.compose-block.yml     ← a compose block, not a CI workflow
```
There is **no** GitHub Actions / GitLab CI / Jenkins / Buildkite / CircleCI configuration
anywhere in the repo. Nothing runs on push, PR, or merge.

### 3.2 No installed schedule — VERIFIED
```
$ crontab -l
crontab: no crontab for rana.singh
$ ls ~/Library/LaunchAgents/
com.razorpay.endpoint-sbom-scan.plist          ← unrelated corp endpoint scanner
$ ls ~/Library/LaunchAgents/ | grep -i "twin\|refresh\|payout"
(no output)
$ grep -rn "launchd\|launchctl\|crontab" scripts/ RED_LOOP/m6/ ENV2_COMPOSE/build/
scripts/snapshot/README.md:135:# crontab -e  --  daily plan at 07:15 local, weekly plan Mondays at 03:00
scripts/snapshot/README.md:142:<!-- ~/Library/LaunchAgents/com.razorpay.twin.refresh-daily.plist … -->
```
The daily/weekly cadence exists **only as commented-out crontab lines and a commented-out
launchd plist inside a README**. Neither is installed. Nothing fires automatically.

### 3.3 No repo-SHA watcher — VERIFIED
```
$ grep -rn "git fetch\|git pull\|ls-remote\|git clone" scripts/ ENV2_COMPOSE/build/ \
      ENV2_COMPOSE/scripts/ RED_LOOP/m6/
ENV2_COMPOSE/build/arena-patches/goutils-dcs-v1.7.3/Makefile:46:  git fetch origin $(PROTO_BRANCH) …
ENV2_COMPOSE/build/arena-patches/goutils-dcs-v1.5.2/Makefile:46:  (same, vendored patch)
ENV2_COMPOSE/build/arena-patches/goutils-dcs-v1.7.1/Makefile:46:  (same, vendored patch)
```
The only `git fetch` hits are inside **vendored upstream Makefiles** in arena patches.
`scripts/snapshot/common.py` line 153 runs `git -C <path> …` **read-only against local
clones**. Consequence: `capture.py` pins 38 repo SHAs but **can never see a new upstream
commit** — the clone root at `/private/tmp/claude-502/…/scratchpad/rzp-payouts-architecture`
is frozen at whatever was cloned on 2026-09-03/04. A diff will report "no change" forever
regardless of what lands in `razorpay/payouts`. **This is the single largest gap.**

### 3.4 No filesystem/webhook event source — VERIFIED
```
$ grep -rn "fswatch\|inotify\|watchman\|polling\|poll_interval" scripts/ RED_LOOP/m6/ ENV2_COMPOSE/build/
(no matches)
```
No file watcher, no webhook receiver, no polling loop. "Event-triggered" has no trigger.

### 3.5 Contract diff → recipe regeneration is not wired — VERIFIED
`diff.py` emits a `contracts` section and `affected.py` maps `contract → service owning the
proto module → rebuild + journeys`, but **nothing regenerates a recipe from a diff**:
`recipes.py` always rebuilds all 100 recipes from scratch and takes no diff input
(`recipes.py --help` → options are only `--out/--manifest/--no-manifest/--no-docker/--list`).
Likewise `regen_config` is reported as a boolean and, per the README, *"never executed here:
config is rendered from `secrets/` by `ENV2_COMPOSE/scripts/up.sh` on the next boot."*

### 3.6 No Kong / K8s / IAM ingestion (see §1.6) — VERIFIED
Changes to `terraform-kong`, `kube-manifests/cde/*` or `authz/scripts/policies/*.csv` can only
be detected as a **repo SHA change** (and only if §3.3 is fixed); nothing parses their contents,
so nothing can compute the blast radius of a route, deployment or permission change. The
`PAYOUTS_SERVICE_GRAPH.json` edges for those domains are hand-typed and go stale silently.

### 3.7 The graph builders are not in the refresh chain — VERIFIED
Neither `refresh.py daily` nor `weekly` calls `scripts/domain/build_graph.py`, the clone-root
`build_graph.py`, or `m4_world_model.py`. `refresh.py weekly` steps are 1–6 as listed in §2.8;
graph rebuild is absent. `affected.py` *consumes* `PAYOUTS_FUNCTIONAL_GRAPH.json` but nothing
refreshes it, so a graph change registers only as `graph_version` drifting when a human
re-runs the builder by hand.

### 3.8 The whole M6 subsystem is uncommitted — VERIFIED
`git status --short` marks `scripts/snapshot/`, `scripts/domain/`, `RED_LOOP/m6/`,
`RED_LOOP/surface/m6_acceptance.py` and all of `reports/domain/{snapshots,refresh,recipes,parts}`
as untracked (`??`), and `Makefile` as modified. A clean checkout of `2613f38` has **none** of it:
`make m6-refresh-daily` would fail. Nothing in it is covered by
`m41_canonical_paths.py` (19 tracked paths) or by any evidence manifest.

### 3.9 No teardown/reset timing, no parallelism, no remote — VERIFIED
See §4.2, §4.3, §4.5. There is no capacity to run a refresh *while* the live arena serves
traffic (one arena per host), so any full rebuild is an outage.

### 3.10 What already exists and should NOT be rebuilt
For fairness: `capture → diff → affected → rebuild → journeys → acceptance` is **fully
implemented and demonstrably runnable** (§2.6). `record-rebuild.py` gives correct incremental
per-service rebuilds with fresh evidence. `instance-plan.py` gives fail-closed isolation.
The gap is trigger + freshness + commit, not the pipeline body.

---

## 4. Measured numbers

### 4.1 Clean boot time
**No tool anywhere records a boot duration** — VERIFIED:
```
$ grep -rn "boot_duration\|duration_s\|elapsed_s\|wall_clock" --include=*.py --include=*.sh \
       --include=*.json --include=*.md . | grep -v "\.git/"
```
returns only red-loop campaign/journey fields (`RED_LOOP/red_loop/campaign.py:306
elapsed_seconds`, `RED_LOOP/m6/journeys/framework.py:791 duration_s`) — **nothing about boot**.
`clean-boot.sh` (read in full) never captures `SECONDS` or `date +%s`.
`reports/implementation/m4-direct-provision.json` top-level keys are
`milestone, generated_at, tested_commit, arena_fingerprint, container_prefix, arena_network,
gateway_profile, run_dir, merchants, summary` — no timing.
`reports/implementation/m3-acceptance.json:22 "elapsed_seconds": 274` is a **red-loop campaign**
duration, not a boot. `reports/ENV2_BUILD_STATUS.md:5` only claims *"zero error lines in a 40 s
steady-state scan"* — INFERRED, qualitative.

**INFERRED (timestamp proxy, computed by me):** `stat -f "%B" <dir>/instance-plan.json` (birth,
≈ clean-boot start) vs `stat -f "%m" <dir>/up.log` (≈ boot complete) and `verifier.log`:

| instance (`RED_LOOP/runs/cleanboot-*`) | boot ≈ s | boot+verifier ≈ s |
|---|---:|---:|
| `m31-clean-a` | 969 | 1022 |
| `m31-clean-b` | 63 | 148 |
| `m4-base-a` | 65 | 120 |
| `m4-final-a` | 451 | 511 |
| `m4-final-b` | 64 | 122 |
| `m4-final2-a` | 435 | 493 |
| `m4-final2-b` | 68 | 129 |

Pattern: `-a` (first of a pair, image build included) 435–969 s; `-b` (cached images)
**63–68 s**. Treat ~65 s as the warm clean-boot figure and ~7–16 min as cold. Proxy only —
`cleanboot-m31-clean-a-pollutionproof` reuses `m31-clean-a`'s instance id and ports, so its
`instance-plan.json` birth time is not trustworthy; that row is excluded.

**VERIFIED (tool-measured) suite durations** from pytest JUnit inside the clean-boot evidence:
```
$ grep -o 'testsuite[^>]*time="[0-9.]*"' RED_LOOP/runs/cleanboot-m31-clean-a/verifier-run/junit.xml
  tests="26" failures="0" errors="0" skipped="0" time="47.832"
$ grep -o … RED_LOOP/runs/cleanboot-m4-final2-b/verifier-run/junit.xml
  tests="37" failures="0" errors="0" skipped="10" time="56.098"
```
`up.log` tail for `m4-final2-b` confirms a complete boot (VERIFIED):
```
# LocalStack queues: 38 created by seeds/localstack/init-queues.sh
Loopback bridge: http://127.0.0.1:18368
arena fingerprint: boot_id=232e25bf-… route_profile=monolith config_digest=72412d603e43 files=140
# Env 2 arena is up. Entrypoint: http://localhost:18368
```

### 4.2 Reset / teardown time — **no measurement exists** (VERIFIED)
`grep -n -iE "time|duration|elapsed|SECONDS"` over `instance-cleanup.py`, `reset.sh`, `down.sh`
yields only `subprocess.run(..., timeout=120)` at `instance-cleanup.py:31` — a ceiling, not a
measurement. `find . -iname "*m4-clean*"` → empty. Nothing to report.

### 4.3 Maximum tested parallel twins — **1** (VERIFIED)
Instance ids ever booted (from `.local/m31-clean/` and `RED_LOOP/runs/cleanboot-*`):
`m31-clean-a`, `m31-clean-b`, `m4-base-a`, `m4-final-a`, `m4-final-b`, `m4-final2-a`,
`m4-final2-b` — **7 instances, all sequential** (each `-b` plan starts after its `-a`
verifier finishes; see the §4.1 timestamps). Documented constraint:
`reports/implementation/m4-known-limits.md` **L-001**: *"Only one arena at a time on this host
(11.65 GiB Docker memory)"*, evidence `docker info MemTotal=12513980416`; **L-011** repeats it.
`grep -riE "parallel twin|simultaneous|concurrent instance|two arenas|multiple arena"` over the
implementation reports and scripts → nothing.
The *isolation model* supports up to ~800 concurrent instances by construction
(`instance-plan.py`: ports 18100–18899, subnets `172.28.[32..230].0/24`) — but **memory, not
naming, is the binding constraint** and multi-instance has never been exercised.
`reports/implementation/m31-campaign-isolation.json` (771 bytes) documents campaign-level, not
arena-level, isolation.

### 4.4 Host / resource arrangement — one local Mac, no remote (VERIFIED)
```
$ docker info | grep -iE "total memory|cpus"
 CPUs: 4
 Total Memory: 11.65GiB
$ docker ps -q | wc -l   →  67
```
Matches `m4-known-limits.md` L-001 (`MemTotal=12513980416` = 11.6546 GiB) and
`reports/implementation/m5-decisions.md:16` ("host ~11.65 GiB, ~67 containers").
`reports/implementation/m5-known-limits.md:23` — the M5 maker-checker campaign was run as a
**standalone** engine, deliberately not inside the 66-container arena, *"Reason: the running
arena is memory-constrained (host ~11.65 GiB, ~67 containers)."*
Sampled profile (`reports/implementation/RESOURCE_PROFILE.md` + `resource-profile.json`,
real samples — VERIFIED):

| phase | samples | peak CPU cores | peak mem GiB | disk GiB |
|---|---:|---:|---:|---:|
| idle | 6 | 1.0203 | 4.1791 | 4.3075 |
| successful_payout | 2 | 1.0022 | 4.0988 | 4.3159 |
| full_verifier | 15 | 1.4038 | 4.1444 | 4.3710 |

Derived planning floor (`DAYTONA/resource-profile.md`, INFERRED and self-labelled
*"not an approved allocation"*): **2 CPU / 8 GiB RAM / 16 GiB disk** per twin.
`grep -riE "remote host|another mac|cloud|aws|gcp|azure"` over the limits/resource docs →
nothing. Everything runs on this one Mac.

### 4.5 DAYTONA — **proposed, not operational** (VERIFIED)
Contents of `DAYTONA/`: `README.md`, `resource-profile.md`, `snapshot-definition.json`,
`execution-plan.example.json`, `.gitignore`, `scripts/{create.sh, remote-run.sh, run-full.sh,
run-smoke.sh, run-and-cleanup.sh, export-results.sh, upload-or-sync.sh, cleanup.sh,
daytona_runner.py}`, `tests/test_runner.py`.

Decisive evidence:
- `DAYTONA/README.md:1` — *"Status: **local dry-run tooling implemented; remote execution
  unverified and blocked**. No Daytona CLI, Python SDK, or Daytona connector was available on
  the implementation host. No SDK was installed, private artifact uploaded, snapshot built, or
  sandbox created."*
- `reports/implementation/DAYTONA_RUN_REPORT.md:16` — `| Sandbox ID | None |`
- `DAYTONA_RUN_REPORT.md:5` — *"No snapshot, sandbox or shared volume was created. No Daytona
  resources were consumed by this workstream."*
- `DAYTONA_RUN_REPORT.md:29` — the final recorded command
  `env -u DAYTONA_APPROVED bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke`
  *"returned exit 0 with `mode: dry-run`, `remote_api_calls: 0`, `sandbox_created: false`
  and `resource_minutes_consumed: 0`."*
- `reports/implementation/offline-final/daytona-dry-run.final.json` on disk:
  `"mode":"dry-run", "remote_api_calls":0, "sandbox_created":false,
  "resource_minutes_consumed":0`, blockers `"[Errno 2] … DAYTONA/execution-plan.json"` and
  `"Daytona Python SDK is not installed; execution capability unverified"`.
- `DAYTONA/scripts/daytona_runner.py:206-210` — the real API path
  (`from daytona import Daytona, CreateSandboxFromSnapshotParams, Sandbox`) sits inside a
  `try/except ImportError` that raises
  `RuntimeError('Daytona SDK unavailable; no automatic installation or sandbox creation')`.
  Code exists; it has never successfully executed.
- No credential present: `daytona_runner.py:226-227` reads `os.environ.get('DAYTONA_API_KEY')`
  — the variable name only; no key value anywhere in the tree.
- `DAYTONA/execution-plan.json` (the non-example plan the runner requires) **does not exist**;
  only `execution-plan.example.json`, which the README says intentionally fails.

**Verdict: zero sandbox IDs, zero remote timestamps, zero API responses. DAYTONA is a designed
and unit-tested remote-execution harness that has never run remotely.**

---

## 5. Bottom line

The twin has **excellent immutability and provenance** (sha256-pinned evidence manifests, boot
fingerprints binding every run to a tree+image set, fail-closed instance isolation, 74/38-gate
acceptance evaluators that run from a clean checkout) and, in the uncommitted M6 work, a
**complete and working refresh pipeline body** (`capture → diff → affected → record-rebuild →
journeys → acceptance`, 100 recipes, 38 pinned repo SHAs, exit-3 diff semantics).

What it does not have is **any trigger, any freshness, and any parallelism**: nothing fetches
from the upstream remotes, so the pinned SHAs can never move; there is no CI, no cron, no
launchd, no watcher, so nothing ever runs unattended; Kong/K8s/IAM content is hand-transcribed
and cannot be diffed; the graph builders sit outside the refresh chain; a single 11.65 GiB Mac
allows exactly one arena, so a full rebuild is an outage; and remote execution (DAYTONA) is
written but never executed. The entire M6 subsystem is also still untracked, so none of it
survives a clean checkout.
