# Source-to-Pay architecture replica

This directory contains a source-backed, isolated replica of the selected Source-to-Pay TDS journey. The runtime executes pinned `vendor-payments` business code through an additive adapter. It uses a local contract replacement for the API-monolith seam and local MySQL, Redis, and Kafka-compatible infrastructure. It does not claim to reproduce the production deployment.

## What the golden journey proves

The executable journey is deliberately two phase:

1. A message on `add-tds-entry` enters the actual vendor-payments consumer path, creates or updates the merchant's monthly tax-payment aggregate, and tags the originating payout through the boundary replacement.
2. The test then calls the actual source `Pay` path explicitly after advancing the injected business clock across the month boundary. That call creates the internal-contact remittance payout through the boundary replacement.
3. A synthetic processed-payout callback reaches the actual source callback path. The observed terminal scope is external status `processing` and internal status `money_loading_success`.

The callback does not establish an accounting-payout event, government transfer, challan, filing, or paid completion. The Kafka input does not automatically cause the later remittance payout. Exact replay in this fixture has no additional record because the source calculates a zero delta; this is distinct from proving every Kafka delivery exercises the source's non-empty idempotency-key branch.

The source runtime differs from the pinned vendor-payments tree in only two declared ways: the additive `cmd/s2p-replica`/boot adapter and synthetic replacements for `TaxPaymentFundAccountNumber` and `TaxPaymentFundAccountIFSC`. The durable source snapshot itself remains unchanged. See [declared deviations](spec/declared-deviations.yaml), [fidelity map](spec/fidelity-map.yaml), and [business state machines](spec/business-state-machines.yaml).

## Prerequisites

- macOS arm64 with Docker Desktop and Docker Compose available to the current user.
- Go `1.26.6`, Git, and Python 3.
- Durable pinned repositories under `S2P_SOURCE_ROOT` (default `/Users/rana.singh/.rzp-architecture-replica/repos`) or authenticated GitHub read access to recover them.
- Private `github.com/razorpay` Go modules already present in the operator's Go module cache or downloadable with the operator's existing Git credentials.
- The pinned Buf Schema Registry modules already present in the operator's Buf cache or downloadable from `buf.build`.
- Network access for any missing public Go modules and pinned code generators during bootstrap.

Credentials, private module caches, and upstream repository contents are not copied into this project. A successful build on one machine does not make this tree a cache-free, offline-independent distribution. Full prerequisite and source pins are recorded in [source-lock.json](source-lock.json).

## Fresh-worktree acceptance run

Run from a fresh worktree. Use the unique Compose project name `s2p_final_verification` so the ownership guard and Docker resources remain separate from other replicas. Each source build must use a new, nonexistent build root; `build.sh` refuses an existing explicit staging directory. The commands below use `run-1` as that clean stage.

```sh
cd /path/to/fresh/rzp-source-to-pay-replica/DOMAIN_REPLICAS/source_to_pay
export COMPOSE_PROJECT_NAME=s2p_final_verification
export S2P_BUILD_ROOT="$PWD/.build/source-runs/run-1"

./scripts/bootstrap.sh
./scripts/build.sh
S2P_GENERATED_STAGE="$S2P_BUILD_ROOT/staging/vendor-payments" ./scripts/build-runtime.sh
./scripts/up.sh
./scripts/health.sh
./scripts/run-golden-journey.sh
./scripts/reset.sh
```

`bootstrap.sh` verifies or recovers every pinned source and installs/verifies the namespaced code-generation toolchain. `build.sh` archives clean pinned trees into the new staging directory, regenerates missing RPC code, verifies modules, and builds every declared production command package. `build-runtime.sh` verifies the generated tree against pinned source, applies the two recorded synthetic bank constants in a separate runtime stage, builds the additive source driver, and records the content-addressed local image in `.build/runtime-image.env`. `up.sh` starts only the project-scoped internal network and volumes; `health.sh` verifies the boundary, database, broker, Redis, and pinned source health. The golden runner writes `artifacts/journey-latest.json` and requires all journey assertions to pass. `reset.sh` removes only this Compose project's containers and volumes.

For the automated clean-run wrapper, use:

```sh
cd /path/to/fresh/rzp-source-to-pay-replica/DOMAIN_REPLICAS/source_to_pay
python3 scripts/clean-run.py --run-id run-1
```

The wrapper uses `COMPOSE_PROJECT_NAME` (default `s2p_architecture_replica`), verifies project ownership before resetting, removes its prior containers and volumes, and proves empty state after boot. It creates its own absent `.build/clean-runs/<run-id>` source stage and captures the build outputs, raw evidence, and independent verifier results. It leaves the resulting runtime available for inspection; the next clean run starts by resetting it. A passing clean run is one input to the 20-gate acceptance decision. It establishes deterministic behavior for the asserted local journey and declared replacements; it does not close the production unknowns below.

## Evidence and scope

Build provenance is recorded in [source verification](artifacts/source-verification.json), [production command package results](artifacts/build-command-packages.json), [build manifest](artifacts/build-manifest.json), [runtime source verification](artifacts/build-runtime-source-verification.json), [synthetic runtime patch](artifacts/build-runtime-patch.json), and [runtime image manifest](artifacts/build-runtime.json). Focused upstream results are summarized in [the test gap matrix](../../reports/source-to-pay-replica/TEST_GAP_MATRIX.md).

The boundary fidelity is limited to the `vendor-payments` ↔ `rzp_tax_pay`/API-monolith seam needed by this journey. It is a contract replacement. It does not execute or prove Payouts' real balance authorization, workflow orchestration, Passport identity, complete contact authorization, or production persistence behavior. See [production unknowns](../../reports/source-to-pay-replica/PRODUCTION_UNKNOWNS.md) and the [future integration plan](integration/future-integration-plan.md).

## Three-run proof and final acceptance

Run IDs are append-only: choose unused IDs rather than overwriting evidence. The following commands assume the implementation has been committed in `/Users/rana.singh/rzp-source-to-pay-replica` and the verification worktree path does not exist. A clean run performs a full source rebuild; prerequisites may supply previously downloaded Go/Buf dependencies, but no prior source stage or compiled output is used as the build input.

```sh
cd /Users/rana.singh/rzp-source-to-pay-replica
python3 DOMAIN_REPLICAS/source_to_pay/scripts/clean-run.py --run-id run-1
python3 DOMAIN_REPLICAS/source_to_pay/scripts/clean-run.py --run-id run-2

git worktree add --detach /Users/rana.singh/rzp-s2p-final-verification HEAD
cd /Users/rana.singh/rzp-s2p-final-verification
COMPOSE_PROJECT_NAME=s2p_final_verification python3 DOMAIN_REPLICAS/source_to_pay/scripts/clean-run.py --run-id run-3

cp -R DOMAIN_REPLICAS/source_to_pay/artifacts/clean-runs/run-3 /Users/rana.singh/rzp-source-to-pay-replica/DOMAIN_REPLICAS/source_to_pay/artifacts/clean-runs/
cd /Users/rana.singh/rzp-source-to-pay-replica
python3 DOMAIN_REPLICAS/source_to_pay/scripts/scan-deliverables.py
./DOMAIN_REPLICAS/source_to_pay/scripts/acceptance.sh
```

Acceptance exit code is nonzero if any hard gate fails, including independently observed changes to protected containers. It generates `artifacts/acceptance.json`, the complete artifact manifest, and the post-commit final/clean-run reports. This is an explicit evidence-finalization operation: hashes bind observations at evaluation time; they are not a signed chain of custody. Later integrity verification is read-only:

```sh
python3 DOMAIN_REPLICAS/source_to_pay/scripts/artifact_integrity.py --verify
```

The original build uses `run-1`, `run-2`, and `run-3`. Use `acceptance.sh --runs <first> <second> <fresh-final>` for a later proof. The designated last run must be from a different registered worktree at the evaluated final commit, with clean current Git status. Do not remove that verification worktree before acceptance.

## Regenerating the architecture artifacts

```sh
python3 DOMAIN_REPLICAS/source_to_pay/scripts/generate-inventory.py --help
python3 DOMAIN_REPLICAS/source_to_pay/scripts/generate-inventory.py --source-root /Users/rana.singh/.rzp-architecture-replica/repos --generated-root /path/to/clean/generated/staging
python3 DOMAIN_REPLICAS/source_to_pay/scripts/apply-runtime-fidelity.py --source-root /Users/rana.singh/.rzp-architecture-replica/repos
```

The inventory retains the mechanically produced raw denominator. The runtime overlay uses the committed `inventory/runtime-proof.json` and exact source ranges, so regeneration does not silently depend on a mutable latest trace. Final acceptance separately verifies all three fresh run traces against the same runtime inputs.
