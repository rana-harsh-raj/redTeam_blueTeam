# Payouts Twin v1

This checkout contains a synthetic local execution environment, source-derived fixtures, an architecture explorer, and a gated Daytona package. Start with [the implementation status](reports/implementation/TWIN_V1_STATUS.md) and [the fidelity matrix](reports/implementation/FIDELITY_IMPLEMENTATION_MATRIX.csv). Source-confirmed behavior and representative substitutions are deliberately distinguished.

## Prerequisites and fresh build

Use approved local copies of the referenced private repositories; this project does not retrieve or upload them. Docker/Compose, Python 3.11+, Go 1.26, Gitleaks, protoc and the already-approved Go/module generation caches are required by the fresh-input build. The pinned source commits, sanitization rules, image IDs and exact preparation/build/configuration commands are in [BUILD_PROVENANCE.md](reports/implementation/BUILD_PROVENANCE.md). The admitted source tree is disposable; original clones are preserved. Cold-cache and amd64 builds have not been established by the arm64 local build evidence.

Set `TWIN_SOURCE_ROOT` to the approved clone parent, follow that build sequence, and run all following commands from this checkout's root. Preparation emits a provenance manifest; `check-inputs.py` verifies it and `configure-local.py` selects hash-verified readable migration assets. Do not use a stale path copied from another engineer's `.env.arena`.

## Local execution

```sh
ARENA_SKIP_BUILD=1 ARENA_SOURCE_REPOS_ROOT="$TWIN_SOURCE_ROOT" bash ENV2_COMPOSE/scripts/up.sh
ARENA_SKIP_BUILD=1 bash ENV2_COMPOSE/scripts/golden-run.sh --with-egress-audit
bash ENV2_COMPOSE/scripts/scenarios.sh --with-egress-audit route
bash ENV2_COMPOSE/scripts/scenarios.sh --with-egress-audit bank
python3 ARCHITECTURE_EXPLORER/run-golden.py --with-egress-audit
```

These commands use the images prepared in the fresh-build sequence. `ENV2_COMPOSE/scripts/golden-run.sh` runs the full 26-case verifier through the post-Kong service contract with independently isolated merchants. `ARCHITECTURE_EXPLORER/run-golden.py` creates one Shared payout through Kong using a generated merchant key. Supplemental route and bank cases retain their own results and complete/error-marked state snapshots. `ARENA_RUN_DIR=/absolute/new/path` chooses an evidence directory. Keep failed runs; do not reuse a directory or relabel an old result as current.

A third supplemental family covers Kafka status transport, and runs only under the `kafka` route profile:

```sh
bash ENV2_COMPOSE/scripts/scenarios.sh --with-egress-audit kafka [--case CASE]
```

`kafka_scenarios.py` fails closed unless the generated fixture index reports `route_profile=kafka`. Its five cases (`shared_source_failure`, `direct_after_shared`, `failed_dropped_direct`, `failed_dropped_shared`, `reversed_dropped_direct`) are driven only by real bank outcomes through mozart-sim, published by the real FTS Kafka producer and consumed by the real payouts Kafka consumer; nothing is injected. `direct_after_shared` is the regression test for a fixed consumer-crash defect (FD-001, see [KAFKA_ROUTE_EVIDENCE.md](reports/implementation/KAFKA_ROUTE_EVIDENCE.md)) and refuses to run unless `shared_source_failure` ran first in the same boot. Four of the five cases are source-predicted failures declared in [TWIN_SPEC/expected-failures.yaml](TWIN_SPEC/expected-failures.yaml) (EF-001/EF-002/EF-003); they are recorded as `expected_failure`, never as `passed`.

### Route evidence, fingerprint and the generated acceptance gate

```sh
python3 ENV2_COMPOSE/scripts/route-evidence.py /absolute/run-dir --case direct_success
python3 ENV2_COMPOSE/scripts/route-evidence.py /absolute/run-dir --bank-case hold
python3 ENV2_COMPOSE/scripts/route-evidence.py /absolute/run-dir --kafka-case shared_source_failure
```

`route-evidence.py` reads existing logs only (no publish, no injected callback) and writes `route-evidence-<case>.json` into the run directory. With `--kafka-case` it additionally writes a `consumer_state` block -- container status and restart counts for both payouts Kafka consumers, each consumer group's own broker-side view, and unfiltered crash-marker counts from the main consumer's log -- the evidence class the historical Direct-after-Shared timeout could not produce, because the old evidence extraction filtered log lines by payout id and the process that actually died never touched that id.

Every boot now writes `ENV2_COMPOSE/.runtime/arena-fingerprint.json` (via `scripts/fingerprint.py`, invoked by `scripts/up.sh`): file digests of compose/config/generator/seed/substitute/verifier/cron-driver inputs, the route profile, image ids, git HEAD and the running container ids. Every run-producing command (`scenarios.sh`, `golden-run.sh`) copies that file into its own run directory as `arena-fingerprint.json`, binding the run to the exact boot that produced it. `config_hashes()`/`config_digest()` from `fingerprint.py` are re-imported by `local-acceptance.py` to recompute staleness from the working tree alone, with no Docker access required.

The machine-readable freeze gate is generated, not hand-authored:

```sh
python3 ENV2_COMPOSE/scripts/local-acceptance.py MANIFEST.json \
  [--output reports/implementation/local-acceptance.json] [--summary reports/implementation/LOCAL_ACCEPTANCE_GENERATED.md]
```

`local-acceptance.py` computes every boolean from a run artifact it actually read and hashed; a check it cannot compute is `false` with a `reason`, never absent or optimistic. It writes `local-acceptance.json` (schema 2) and the human-readable `LOCAL_ACCEPTANCE_GENERATED.md`. Schema 2 separates what schema 1 conflated: **`route_fidelity`** (required for freeze -- for every in-scope route/profile, transport was observed and the ending state matches either the normal expected state or a source-predicted failure in `TWIN_SPEC/expected-failures.yaml`) from **`product_route_health`** (informational -- did the business route actually complete; Shared Kafka is `false` here with its EF citation, and this is the number a later red-agent judge consumes). The manifest's roles bind named run directories to what each one is evidence for: `replay_a`/`replay_b` (the two from-empty logical replays), `final_route`/`final_bank`/`final_golden` (the monolith-profile supplemental families), `profile_direct_golden`, `profile_mixed_golden`, and `profile_kafka` (which itself carries a `golden-shared` and a `kafka` subdirectory). The gate also embeds and requires non-placeholder content from `ENV2_COMPOSE/config/declared-deviations.yaml` and `reports/implementation/fixed-twin-defects.json` (`declared_deviations_present`, `fixed_twin_defects_recorded`), and requires `no_unexplained_results`: any observed failure that matches neither an expected-failure entry nor a fixed-twin-defect entry blocks the freeze.

`ENV2_COMPOSE/build/record-rebuild.py` rebuilds one core service image with `build-host.sh` and records fresh build evidence (build log, `build-results.json` entry, `image-assets.json` entry, and for payouts/cfa the asset-readability file) into a new `.local/twin-repos/build-evidence-<UTC>` directory derived from a base evidence directory, without touching any source clone -- used to re-attest the payouts image after the `files/error/*.json` asset-staging fix (DEV-004).

`ENV2_COMPOSE/config/declared-deviations.yaml` is the machine-readable registry of every known twin/production difference (`DEV-nnn`, with `status: declared` or `fixed_in_m1`). `TWIN_SPEC/expected-failures.yaml` lists route outcomes the twin is *required* to reproduce as failures because pinned production source produces them (`EF-nnn`); an observed failure matching neither that file nor `reports/implementation/fixed-twin-defects.json` (twin-side defects corrected during Milestone 1, `FD-nnn`) is an unexplained result and blocks the freeze.

### What v1.0 declares out of scope

- Full Workflow/approval (the workflow host is blackholed; no approve/reject/expiry substitute)
- XAS/BAS/statement reconciliation (no general statement ingestion, UTR matching or dispatcher)
- Bulk caller and bulk approval
- Full production Stork lifecycle (retry/disable/subscription/queue semantics beyond the JSON/Twirp contract used here)
- Broad bank/channel/gateway/version fleet (the arena runs one bank channel with a bounded mozart-sim response set)
- The missing worker fleet outside what this twin's process model runs
- Complete dashboard/admin/impersonation behaviour
- Shared Kafka product-route health (`product_route_health` for Shared Kafka is `false` by design, per EF-001) -- **route fidelity on the Kafka leg is in scope**: the twin must keep faithfully reproducing the source bootstrap defect, not paper over it

`up.sh` generates customer/merchant fixtures, credentials and effective config, validates config, materializes narrowly scoped UID10001 files, starts databases, applies migrations and schema patches, seeds data, and starts substitutes/core workers. It then invokes the real reservation reconciler and requires the real API to confirm a trusted store before declaring readiness; Ledger configuration HTTP failures also abort startup. Both runtime networks are internal. The host entrypoint at [localhost:18080](http://127.0.0.1:18080) is a loopback Docker-exec bridge. The runtime packet audit starts before the verifier command, checks internal traffic was observed, records outside destinations and dropped packets, and confirms removal of its capture resources. DNS and peer-listener checks execute inside that same command window.

Select a route profile only on a clean boot:

```sh
bash ENV2_COMPOSE/scripts/down.sh
ARENA_SKIP_BUILD=1 ARENA_ROUTE_PROFILE=direct ARENA_SOURCE_REPOS_ROOT="$TWIN_SOURCE_ROOT" bash ENV2_COMPOSE/scripts/up.sh
```

Profiles: `monolith`, `direct`, `direct-create-monolith-status`, `kafka`. Pass the same `ARENA_ROUTE_PROFILE` value to the later current-boot audit, which independently rerenders configuration. Configuration selects existing code branches. It does not repair source Kafka limitations or imply that every selected route satisfies every product invariant; see [route coverage](reports/implementation/ROUTE_COVERAGE.md).

## Explorer

```sh
python3 ARCHITECTURE_EXPLORER/manage.py install
python3 ARCHITECTURE_EXPLORER/manage.py build
python3 ARCHITECTURE_EXPLORER/server.py
```

Open [Payouts Twin Explorer](http://127.0.0.1:18765). The server runs in the foreground; use another terminal for verification commands while it is open. The static export is `ARCHITECTURE_EXPLORER/dist`. See [its README](ARCHITECTURE_EXPLORER/README.md) for saved/live behavior and screenshots. Illustrative playback examples are labelled and separated from captured runtime traces.

## Replay and resource evidence

Use two distinct from-empty run directories. Always run `down.sh` between them. The replay comparator requires the complete passing 26-case inventory and compares material observed state rather than wall clocks:

```sh
python3 ENV2_COMPOSE/scripts/compare-replays.py /absolute/run-one /absolute/run-two \
  --output reports/implementation/logical-replay.json
```

The reproducible [current-boot audit](ENV2_COMPOSE/preflight/audits/README.md) refreshes config, endpoint, log and logical-store evidence after workloads finish; it preserves earlier captures and checks that the boot did not change.

Resource measurement commands and sampled-peak limitations are in [resource-profile.md](ENV2_COMPOSE/scripts/resource-profile.md). Preserve a redacted command log when wrapping a suite:

```sh
ARENA_SKIP_BUILD=1 ARENA_RUN_DIR=/absolute/new/run \
python3 ENV2_COMPOSE/scripts/resource-profile.py --phase full_verifier --duration 2400 --interval 2 -- \
  python3 ENV2_COMPOSE/scripts/capture-command.py /absolute/new/run.log \
  bash scripts/golden-run.sh --with-egress-audit
```

`capture-command.py` runs its child from `ENV2_COMPOSE`, so the final `scripts/golden-run.sh` path is relative to that directory. The profiler itself does not change the working directory.

## Daytona and cleanup

Without approval, this is a local dry run with zero API calls:

```sh
bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke
```

Actual execution requires passed local acceptance plus an explicit company-approved, hash-bound transfer plan. `DAYTONA_APPROVED=1` is mandatory and cannot bypass the other gates:

```sh
DAYTONA_APPROVED=1 bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke --plan DAYTONA/execution-plan.json
```

The [Daytona README](DAYTONA/README.md) describes the required installed SDK capability checks, snapshot, approved artifacts, resource inputs, hard TTL, result export and delete/absence confirmation. Never transfer private source or binaries based on the example plan.

Local cleanup removes runtime containers, data/config/secret volumes and generated credentials; it leaves images, admitted source copies and evidence for review:

```sh
bash ENV2_COMPOSE/scripts/down.sh
```

If an approved Daytona run created a sandbox, its exact fallback cleanup command is:

```sh
DAYTONA_APPROVED=1 bash DAYTONA/scripts/cleanup.sh --state DAYTONA/runs/<run>/state.json
```

Do not claim deletion for a sandbox that was never created. Resource usage and actual remote status are recorded in [DAYTONA_RUN_REPORT.md](reports/implementation/DAYTONA_RUN_REPORT.md).
