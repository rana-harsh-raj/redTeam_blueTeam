# Fresh build provenance

**All five real-service candidate images built successfully from fresh local Git-HEAD copies.** These are build results, not runtime/scenario acceptance. No original clone was modified; no application binary was run on the host; no external artifact was uploaded. The candidate tag is `v1-candidate`; this workstream did not change `local` tags or Compose configuration.

Prepared source root: `.local/twin-repos/accepted`. This directory name means admitted build inputs; it does not assert full local acceptance.

## Source and build results

| Service | Source commit | Admitted files | Build exit | Build time |
|---|---|---:|---:|---:|
| payouts | `4bf3dbf9239feadea6d65ca90c893a988e116173` | 1425 | 0 | 26.08 s |
| ledger | `471ff4d5321b6b99a7965882d18f572a1adf5194` | 738 | 0 | 22.29 s |
| fts | `2a09e763116db47a2f28553c677ad73ef8133ebf` | 1101 | 0 | 14.15 s |
| cfa | `d488558e162c08e9fa04dff005f27d846ce88ad2` | 475 | 0 | 14.87 s |
| x-balances | `1a21c0f9111da14125d839dc0dfe8d980740f207` | 539 | 0 | 11.74 s |

All source clones reported clean tracked/untracked status before preparation; their commit and dirty-status values remained unchanged afterward. Ignored generated RPC and migration directories were deliberately regenerated rather than copied from developer working trees. CFA and x-balances RPC code is already tracked.

All five `go mod verify` checks passed with checksum verification enabled. Application compilation used Go 1.26.6, `GOOS=linux`, `GOARCH=arm64`, `CGO_ENABLED=0`, `GOFLAGS=-mod=readonly`, `GOPROXY=off`, `GONOPROXY=none`, `GONOSUMDB=none`, `GOSUMDB=sum.golang.org`, and `GOTOOLCHAIN=local`. No application module lockfile was rewritten; post-build file-inventory and SHA-256 checks passed. The fresh repository build used the existing verified Go module cache. Cold-cache dependency acquisition is not proven. No checksum mismatch was suppressed.

## Preparation implemented

`ENV2_COMPOSE/build/prepare-repos.py` discovers source locations from `.env.arena` or an explicit `--source-root`, extracts exact Git HEAD archives, refuses to overwrite a destination, and admits copies only after a Gitleaks 8.30.1 scan. It writes a per-file provenance manifest, repository commits/dirty status, exclusions, generated inputs/outputs, generator versions/hashes, and arena-patch hashes.

`ENV2_COMPOSE/build/check-inputs.py` validates the prepared input inventory and hashes, verifies the arena patch files/script, and optionally executes `go mod verify`. It exits nonzero for missing, changed or unapproved inputs.

Excluded files include environment/credential files, upstream runtime/deployment configuration, ordinary test fixtures, CI/editor material, and a non-runtime Ledger Debezium sample. The scanner emits redacted metadata only. Two reviewed comment examples were replaced with explanatory comments, keyed to their exact source-file hash and line: Ledger `pkg/idempotency/idempotency/repo.go:44` and FTS `internal/providers/encryption/sign.go:24`. No executable code changed in these comment edits. Unrecognized source findings remain blockers.

An initial blanket test-directory exclusion was corrected because production code imports utility packages under test directories. The final copy preserves Payouts `e2e/config`, `e2e/redis`, `e2e/utils`, and FTS `e2e/config`, `e2e/constants`, `e2e/utils`, `test/mock`, following the production import closure. The FTS `service_test_data.go` globals had no non-test source references and were excluded as unused compiled fixture material. This removes unused fixture globals; it does not replace a business-logic branch.

Final admitted source scans have **0 unresolved findings across all five services**. Rejected copies from early staging attempts were removed, with their metadata-only reports retained. This scanner result does not prove absence of every possible secret in dependency binaries.

## Generated build inputs and fidelity

Payouts and Ledger generated RPC code comes from the clean `proto` repository at commit `52682577d79d7237944e9fbb10477a1721623065`. Selection follows each service’s `scripts/proto_modules`: 6 Payouts schema files and 10 Ledger schema files, plus their import closure. Public Google annotation descriptors came from cached `github.com/grpc-ecosystem/grpc-gateway@v1.16.0`; Buf supplies well-known protobuf types. Exact descriptor and generated-output hashes are recorded. No BSR dependency fetch or private Git clone occurred during generation.

| Service | Buf | protoc-gen-go | protoc-gen-twirp |
|---|---|---|---|
| Payouts | 1.32.0 | github.com/golang/protobuf 1.5.2 (protobuf API 1.26.0) | 5.10.1 |
| Ledger | 1.28.1 | google.golang.org/protobuf 1.26.0 | 8.0.0 |

These generator versions match the service Makefiles. Exact-version installed tools were identified using `go version -m`; missing tools were built from cached public modules into `.local/twin-build-tools`. Legacy Twirp’s missing go.mod is handled in an isolated tool-only module with pinned compiler dependencies, retaining checksum verification. Swagger documentation generation is omitted because the runtime build uses only Go and Twirp outputs. Offline Google descriptor package options are explicitly preserved; wire DTO fields are unchanged.

Ledger’s real RX build materialization was restored: copy tracked `internal/database/rx_migrations` into the ignored `internal/database/migrations` Go package before compiling the migration binary. Evidence is `ledger/build/docker/prod/Dockerfile.migration:58–60` and `ledger/.github/workflows/pr_workflow.yml:309`. The original developer directory contained extra PG migration files. The candidate follows the RX build selection; runtime migration acceptance must use its RX files. This is application/build wiring derived from the real build, not a newly invented schema patch.

Existing `apply-arena-patches.sh` was applied only to the copies. Its behavior-changing arena patches remain explicit: DCS routing uses `ARENA_DCS_URL` instead of real environment-derived endpoints, and Payouts can use Stork’s JSON client via `ARENA_STORK_JSON`. These can hide DCS endpoint-resolution failures and protobuf/JSON compatibility differences respectively. With their flags unset, the intended original paths remain. No other business-logic patch was introduced by this build workstream.

## Runtime image inventory

| Image | Recorded image ID | Size (MiB) |
|---|---|---:|
| `rzp-arena/payouts:v1-candidate` | `sha256:e029d63980bb76107029e49f61b41cf83b678bcc56958ac44906808cefa8baf8` | 116.9 |
| `rzp-arena/ledger:v1-candidate` | `sha256:878be96a5f2bbef6af7236660984547a420f27c58d0f7a88b6e5fdb74ada4361` | 90.8 |
| `rzp-arena/fts:v1-candidate` | `sha256:dac4c76a4686f6f5d4d65fdccfbb5505e74e8fefd55cd1012602dd36f31a930d` | 84.9 |
| `rzp-arena/cfa:v1-candidate` | `sha256:1b6cf763e003de35f69032eb1b39179a25b4dc6e76cc329a3140673712e27206` | 76.6 |
| `rzp-arena/xbalances:v1-candidate` | `sha256:980f8333a3e906a8ed51e865fdde72c9060f92774bdf551674f3c64522733b5f` | 62.5 |

All five images are Linux arm64, run as `appuser`, and have only the standard PATH key in image environment metadata. Never-started, network-none inspection containers were used to inventory `/app`; every inspection container was explicitly removed afterward. No application was started for this inventory.

Image `/app` contents are the compiled binaries, public IFSC/bank-directory JSON metadata for Payouts/CFA, and the existing CFA entry script. No source tree, upstream environment config, generated synthetic secret, GitHub token or Daytona key was staged as an image asset. Payouts/CFA non-binary assets produced zero Gitleaks findings; the other three images have no non-binary `/app` assets. ELF contents were hashed, not represented as exhaustively scanned for all embedded secrets. Public bank-directory metadata is exact reference data; it is not fake merchant/customer data. Synthetic runtime configuration and credentials must still be generated and audited at startup.

### Public asset permissions corrected after clean-boot failure

The first CFA migration attempt reported permission denied on its packaged `banks.json`: admitted source files were mode 0600 and packaging preserved that mode into a root-owned image file. `build-host.sh` now installs only the explicitly selected public IFSC JSON assets at mode 0644, binaries and the CFA entry script at 0755, and staging directories at 0755. It does not change source permissions or stage any additional files. Staging is also beneath the existing temporary-directory cleanup boundary.

CFA and Payouts candidates were rebuilt from the same admitted inputs with caller `umask 077`; the table above records the replacement image IDs. Shell-only inspection containers, using each image's configured `appuser` (asserted UID 10001), `--network none`, `--read-only`, dropped capabilities, and no mounts, successfully opened all five named files (`banks.json`, `banknames.json`, `IFSC.json`, `sublet.json`, `custom-sublets.json`). Every file was root-owned 0644 and its SHA-256 matched the source asset; CFA source files remain 0600. All required binaries and the CFA entry script were executable. Both inspection containers were removed and their absence confirmed. No application binary or arena service was started by this follow-up. Evidence: `cfa-asset-readability.json`, `payouts-asset-readability.json`, and the corresponding `*-permission-rebuild.log` files in the final build evidence directory. Host-mounted secret/config ownership is a separate runtime concern.

## Reproduction and machine-local configuration

Use a new destination for each clean preparation; existing destinations are never overwritten. Run this sequence for the next clean arena, not against an actively running stack. Set `TWIN_SOURCE_ROOT` to the approved local source-clone directory containing `payouts`, `ledger`, `fts`, `cfa`, `x-balances`, and `proto`. The workflow never clones or uploads those repositories. Cached public generator modules, Gitleaks, Go 1.26 and a local Docker/Compose installation are prerequisites.

The commands below discover the emitted provenance file exactly, verify the admitted inputs, build the five images, and update only machine-local path/tag settings. `configure-local.py` preserves all other `.env.arena` values and comments; `--check` is a read-only preview. Its six tests pass, and the preview against the current accepted tree left the active `.env.arena` hash unchanged. Compose dotenv parsing of spaces, quotes and literal dollar signs was verified separately with temporary files. The helper has **not** been applied to the running arena by this workstream.

```bash
# Start from this checkout's root; set this to your approved local clones.
export TWIN_SOURCE_ROOT=/absolute/path/to/approved/local/clones
set -euo pipefail
export GOPROXY=off GONOPROXY=none GONOSUMDB=none
export GOSUMDB=sum.golang.org GOTOOLCHAIN=local GOFLAGS=-mod=readonly
TWIN_BUILD_ID="$(date -u +%Y%m%dT%H%M%SZ)"
TWIN_ADMITTED="$PWD/.local/twin-repos/fresh-$TWIN_BUILD_ID"
TWIN_TAG="v1-candidate"
TWIN_PREP_LOG="$PWD/.local/twin-preparation-$TWIN_BUILD_ID.log"
mkdir -p "$PWD/.local"
python3 ENV2_COMPOSE/build/prepare-repos.py \
  --source-root "$TWIN_SOURCE_ROOT" --destination "$TWIN_ADMITTED" | tee "$TWIN_PREP_LOG"
TWIN_PROVENANCE="$(sed -n 's/^Provenance: //p' "$TWIN_PREP_LOG" | tail -n 1)"
test -n "$TWIN_PROVENANCE"
test -f "$TWIN_PROVENANCE"
python3 ENV2_COMPOSE/build/check-inputs.py "$TWIN_PROVENANCE" --verify-modules
REPOS_ROOT="$TWIN_ADMITTED" ARENA_TAG="$TWIN_TAG" bash ENV2_COMPOSE/build/build-host.sh all
python3 ENV2_COMPOSE/build/configure-local.py --repos-root "$TWIN_ADMITTED" --provenance "$TWIN_PROVENANCE" --tag "$TWIN_TAG" --check
python3 ENV2_COMPOSE/build/configure-local.py --repos-root "$TWIN_ADMITTED" --provenance "$TWIN_PROVENANCE" --tag "$TWIN_TAG"
```

`LEDGER_REPO_MIGRATIONS_DIR` selects a verified readable copy of the pinned source's `internal/database/rx_migrations`; it does not use the historical PG spike directory. The legacy `FTS_REPO_CONFIG_DIR` value is updated for tool compatibility, but current Compose mounts generated FTS config. The helper validates the five repository `go.mod` files and four migration directories, then checks every selected Go/SQL migration file against the passed preparation provenance. It creates a private mode 0700 stage under `.local/twin-runtime-migrations/<prepared-directory-name>`; only the four per-service subdirectories are mounted, at mode 0755 with unchanged migration files at 0444. Configs, `.env`, keys and unrelated files are excluded. Existing stages must match their inventory, content hashes and modes exactly and are never silently repaired. Original source modes and contents remain unchanged. Full input admission and module verification remain the separate `check-inputs.py` gate.

In the separate image-preparation phase, build substitutes/verifier and fetch public runtime images **before** runtime auditing. This requires public package/image access if those layers are not already available. None of these commands uploads an arena artifact:

```bash
(
  cd ENV2_COMPOSE
  docker compose --env-file .env.arena -f docker-compose.yml \
    --profile datastores --profile substitutes --profile verify build
  docker compose --env-file .env.arena -f docker-compose.yml \
    --profile datastores --profile substitutes --profile verify pull --ignore-buildable
)
docker pull nicolaka/netshoot:latest
```

The same local startup and audited verifier entrypoints then apply:

```bash
ARENA_SKIP_BUILD=1 ARENA_SOURCE_REPOS_ROOT="$TWIN_SOURCE_ROOT" bash ENV2_COMPOSE/scripts/up.sh
ARENA_SKIP_BUILD=1 bash ENV2_COMPOSE/scripts/golden-run.sh --with-egress-audit
```

This is a fresh-input/cached-module reproduction workflow, not a byte-identical image or cold-cache guarantee. Base/helper image tags are mutable; retain image IDs/digests alongside each run. Linux arm64 was built here; full native-Linux platform acceptance and amd64 builds remain separate checks. Migration-file readability is now addressed by the verified staging step. Generated secret/config mounts are separately materialized for UID 10001 without broadening host 0600 permissions.

### Migration ownership verification

The original admitted migration files are 0600. Four isolated containers using the actual images' configured `appuser` (UID 10001), `--network none`, a read-only filesystem and only their own staged migration directory successfully read and hash-verified all 118 selected assets: 34 Payouts, 32 Ledger, 51 FTS and 1 x-balances. Source files retain 0600 and their admitted SHA-256 values. Staged files are 0444; mounted directories 0755; the enclosing host stage 0700. These are approved credential-free migration/schema source assets, not published customer data or generated secret configs. All four inspection containers were removed and absence confirmed. Evidence: `reports/implementation/migration-readability-verification.json`.

This workstream created a separate verification stage and did not change active `.env.arena` paths, current migration binds, generated settings or arena containers. The next explicit `configure-local.py` invocation will select its verified stage for a fresh startup.

Current-run evidence (local, ignored artifacts; regenerate on another machine):

- `.local/twin-repos/build-evidence-20260905T113414Z/provenance.json` — final source, codegen, scan and patch manifests.
- `.local/twin-repos/build-evidence-20260905T113414Z/build-results.json` — five successful candidate builds, image IDs, script hashes and durations.
- `.local/twin-repos/build-evidence-20260905T113414Z/post-build-verification.json` — original clone checks and later narrowing of the comment-redaction guard without changing emitted source bytes.
- `.local/twin-repos/build-evidence-20260905T113414Z/image-assets.json` — hashes and inventory from never-started inspection containers.
- `.local/twin-repos/build-evidence-20260905T113121Z/input-verification.log` — all five successful module checksum checks; module lockfiles are unchanged in the final copies.
- Per-service `*-build.log`, `*-generate-rpc.log`, generator build logs and metadata-only Gitleaks reports in those evidence directories.

No remaining dependency/checksum blocker was observed for these five builds. Cold-cache retrieval, amd64 builds, live behavior of the candidate images, full secret scanning of linked dependency code, and full local acceptance remain separate checks; this report does not claim them.

## Applied local migration configuration

The parent implementation applied `configure-local.py` to the accepted source tree while the arena was down, before replay2. The subsequent clean boot therefore uses the hash-verified readable migration stage. Earlier statements about an unchanged active configuration describe the independent verification phase.

## 2026-09-05: Milestone 1 config/wiring corrections (TWIN_V1_AUDIT_AND_NEXT_STEP.md §5.2/§8) — pending rebuild

Configuration-lane pass, addressing the audit's undeclared-twin-change items that had a determinable production
value. No core source file and no file under `.local/twin-repos/accepted/` was touched; changes are confined to
`ENV2_COMPOSE/config/templates/base/payouts/arena.toml`, `ENV2_COMPOSE/config/routes.py`,
`ENV2_COMPOSE/docker-compose.yml`, and `ENV2_COMPOSE/build/build-host.sh` (+ `build/assets.Dockerfile.md`):

- `arena.toml` `[kafka_producer].Brokers`: `["broker:9092","broker1:9092"]` (non-resolvable, upstream
  `default.toml:429` placeholder) → `["kafka:9092"]`, matching `[kafka_consumers.*.config].Brokers` in the same
  file. This is the root cause reproduced/retained at
  `reports/implementation/runs/claude-audit-kafka-repro-20260905T152544Z/` (nil producer → unrecovered panic in
  `taskHandlers.(*FtsStatusUpdatesTask).HandleFailedMessage`). `EnableTLS=false` and the existing placeholder
  cert values were left as-is (already inert with TLS disabled, consistent with the consumer sections).
- `arena.toml` `[features].ikey_auto_enforcement`: `false` → `true`, matching `payouts/config/prod.toml:664`.
  Enforcement is additionally AND-gated per merchant by Splitz experiment
  `[splitz_experiment_list].ikey_auto_enforcement_ramp_up` (`internal/routing/middleware/idempotency_key.go:107-111`);
  the twin's splitz-stub fixtures (`seeds/s4/splitz_fixtures.json`, `seeds/splitz/experiments.json`,
  `seeds/generated/splitz/experiments.json`) return variant `"off"` for that experiment id (`R2Mo039vdGE3Dr`) for
  every seeded merchant (`ARENAM00000001-3`) and the stub's fallback default is also `"off"`. This flip is
  therefore currently inert against the committed fixtures/verifiers — no seed file was changed here since seeds
  are outside this lane's edit scope; the orchestrator should decide whether to turn the ramp-up experiment "on"
  for a designated test merchant to actually exercise the enforced path.
- `routes.py` `[payouts_service.update_fts_fund_transfer].TIMEOUT`: `10` → `300`, matching
  `fts/config/env.prod-live.toml:269-273`. `URL` (`http://payouts-api:9400/v1/payouts/transfer_status_webhook`)
  and `METHOD` (`POST`) were verified against the same prod block and against payouts
  `internal/routing/router/payout_internal_routes.go:12-13,153-158` (group `/v1/payouts` + `POST
  /transfer_status_webhook`) and left unchanged — confirmed correct, not touched.
- `docker-compose.yml`: added `restart: on-failure` to `payouts-kafka-fts-status-updates-consumer` and
  `payouts-kafka-fts-status-updates-retry-consumer`, citing the deployed `restartPolicy: Always` in
  `kube-manifests/templates/payouts/templates/payouts-kafka-fts-status-updates-{consumer,retry-consumer}.yaml:127`
  (also visible in the retained drift report `devstack-prod-parity/reports/batch8/drift-check-20260703.html`).
  Verified `scripts/down.sh` (`docker compose ... down [-v]`) and `network/egress-audit.sh` do not fight this —
  an intentional stop suppresses a container's restart policy. Recommendation (not applied): consider
  `restart: on-failure` more broadly for parity with `restartPolicy: Always` on other core Deployments; scoping
  that broadly was out of this lane's mandate.
- `build/build-host.sh` `stage_assets_payouts`: now also stages `$REPOS_ROOT/payouts/files/error/{payout_error,fav_error}.json`
  into the runtime image at `/app/files/error/*.json`, 0644, via the existing `stage_public_json` helper — mirrors
  prod's `build/docker/prod/Dockerfile.api:80` `COPY --from=builder /src/files/ /app/files/`. Both files were
  grepped for password/secret/api_key/private_key/token/credential value patterns (zero hits; only unrelated
  `VAULT_TOKEN_*` error-code *keys*) before staging. `build/assets.Dockerfile.md` updated with the same citation.
  **Requires an image rebuild to take effect** — no image ID change is recorded here; the orchestrator should run:
  `python3 ENV2_COMPOSE/build/record-rebuild.py payouts --base-evidence .local/twin-repos/build-evidence-20260905T113414Z --repos-root .local/twin-repos/accepted --tag v1-candidate --asset-paths /app/files/error/payout_error.json /app/files/error/fav_error.json --note "milestone 1: stage payouts/files/error/*.json"`
  (equivalently: `REPOS_ROOT=.local/twin-repos/accepted ARENA_TAG=v1-candidate GOPROXY=off GONOPROXY=none GONOSUMDB=none GOSUMDB=sum.golang.org GOTOOLCHAIN=local GOFLAGS=-mod=readonly bash ENV2_COMPOSE/build/build-host.sh payouts`)
  and fill in the resulting image ID/digest here or in the next build-evidence directory. `build-host.sh` already
  supports a per-service positional selector (`payouts|ledger|fts|cfa|xbalances|all`, used as-is by
  `record-rebuild.py`'s `args.service`), so no `ARENA_BUILD_ONLY` switch was added.

Not changed by this pass (declared, not fixed — out of this lane's file scope or requires another lane):
ledger-gate header forwarding and ledger `mutex.ttl` (ledger-gate/ledger templates, another lane's files);
`mozart`/`account_service` circuit-breaker timeouts; `source_update_topics.*` collapse and `stage-` prefixed
`job.x_balances_*` names (listed for the declared-deviations registry, not modified); all Splitz variant IDs and
route-profile selection. See `TWIN_V1_AUDIT_AND_NEXT_STEP.md` §5.2 and lane_C_patches.md §2.1/§3 for the full
inventory and citations.

Sanity checks performed after these edits: `python3 -c "import tomllib; tomllib.load(...)"` against a
placeholder-substituted temp copy of `arena.toml`; `python3 -m py_compile ENV2_COMPOSE/config/routes.py`;
`docker compose --env-file ENV2_COMPOSE/.env.arena -f ENV2_COMPOSE/docker-compose.yml config --quiet`. No
container was started, stopped, or rebuilt by this pass.
