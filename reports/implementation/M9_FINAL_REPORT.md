# M9 — Isolated Twin Factory: final report

Rendered by `python3 -m twinfactory report` from the committed machine records under `reports/implementation/m9/`, the isolation proof and the acceptance evaluation. twinfactory 1.0.0.

## Result

| item | value |
|---|---|
| acceptance | **ACCEPTED** (20/20 gates, `reports/implementation/M9_ACCEPTANCE.json`, evaluated at `7c52bdc1a1` on `milestone-9-twin-factory`) |
| architecture snapshot consumed | `5b6a5dade17eba6c0a407d3d4ba8423c8bf0b8ebeb9ea2bc3706483e04e8d141` (recipe set `85f237c46431c2af`) |
| execution backend | colima (one Lima/vz virtual machine + its own dockerd per instance; only the instance's execution directory is mounted) |
| profiles supported | `full` (98 services, 5 migration jobs, Source-to-Pay overlay) and `focused:<family>` derived by graph closure — demonstrated `critical-payouts` = `focused:shared-payouts` (93 services) |
| two-instance isolation proof | **PASSED** — phases P1=PASS, P2=PASS, P3=PASS, P4=PASS, P5=PASS, P6=PASS, P7=PASS, P8=PASS, P9=PASS |

## Lifecycle operations (library `twinfactory.factory.Factory`, CLI `python3 -m twinfactory`)

create → build → start → status / health → journeys → reset → snapshot-state / restore-state → stop → destroy, plus reproduce (new instance from an existing manifest), ls, manifest, events, images export, inputs export, isolation run, evidence, acceptance, report. Every operation appends to the instance event log and updates the durable registry (`~/.twin-factory/registry.json`).

## Instances used in the proof

| instance | profile | seed → epoch | backend profile | services | port | subnet | runtime_instance_id |
|---|---|---|---|---|---|---|---|
| `alpha-full` | `full` | `seed-a` → 1754641637 | `twin-alpha-full` | 98 | 18236 | 172.28.92.0/24 | `bf19b122ab39c6dc` |
| `beta-focused` | `focused:shared-payouts` | `seed-b` → 1749805293 | `twin-beta-focused` | 93 | 18855 | 172.28.206.0/24 | `04926d624a082d27` |

Both manifests bind: architecture snapshot id, recipe set id, profile digest, seed and seed epoch, inputs hash (`86423bfb9fe8…`, identical for both — the credential-free static definition), rendered-config hash and secrets-manifest digest (instance-private), and the image id of every image (34 images for the full profile).

## Journeys

- `alpha-full`: `journey:cross-domain-s2p/success` → **PASS** (24/24 checks, 93.2 s, merchant `ARENAM91214567`), evidence attributed to the instance: True
- `beta-focused`: `journey:shared-payouts/success` → **PASS** (12/12 checks, 68.3 s, merchant `ARENAM96071357`), evidence attributed to the instance: True
- `beta-focused` while `alpha-full` was stopped: {"blocked": 0, "expected_failure": 0, "fail": 0, "pass": 1, "total": 1}

## Observed defects during boot

- none recorded in the final boots (a real binary that crashes at start is restarted at most twice by the boot orchestrator and every restart is recorded in the manifest's `boot.crash_restarts`)
- Observed once during the first P9 attempt (recorded in the proof run log, phase then re-run): `xbalances-worker` (real x-balances binary) died at start with `fatal error: concurrent map read and map write` in `pkg/worker.(*Manager).Do` (`pkg/worker/manager.go:253`) — a startup race in the pinned source; the orchestrator's bounded crash restart was added in response.

## Isolation proof (reports/implementation/m9-isolation-proof.json)

| phase | result | evidence |
|---|---|---|
| P1 separate execution boundaries (distinct Docker daemons/VMs), both running, distinct profil | PASS | daemon ids `be146de3-f74` vs `b794ae9b-af0`; sockets differ; both boundaries isolating |
| P2 no shared container/network/volume/port/subnet/secret/config between the instances | PASS | shared names: 0; B resources on A: 0; A on B: 0; ports {'a': 18236, 'b': 18855}; S2P secret instance-specific: True |
| P3 each VM mounts only its own execution directory (the other instance's files are not visibl | PASS | alpha-full_sees_beta-focused_exec_dir: NOT-VISIBLE; beta-focused_sees_alpha-full_exec_dir: NOT-VISIBLE |
| P4 cross-instance names do not resolve, other networks are not attachable, other host ports a | PASS | alpha-full: own=200 other-by-name=000 other-net=rc125 other-host-port=000; beta-focused: own=200 other-by-name=000 other-net=rc125 other-host-port=000 |
| P5 representative journeys pass in both instances and are attributed to the right instance | PASS |  |
| P6 stopping/restarting A does not affect B (B stays healthy and passes a journey while A is s | PASS | B healthy while A stopped: True; A restart 87.7 s; both healthy after |
| P7 resetting B leaves A's datastore rows, rendered configuration and secrets unchanged | PASS | A rows before == after: True; A rendered config unchanged: True; B reset 63.9 s |
| P8 snapshot-state / restore-state on B returns mutated datastore rows to the snapshot | PASS | rows at snapshot {"payouts.payouts": "8"} → after mutation {"payouts.payouts": "12"} → after restore {"payouts.payouts": "8"} |
| P9 destroying A leaves B healthy; A is reproduced from its manifest (same inputs hash, profil | PASS | B healthy after A destroyed: True; A reproduced: inputs_hash=True profile=True seed_epoch=True image ids equal 34/34; A boots again: True |

## Measurements

| instance | provision | build (images) | boot | restart | reset | snapshot / restore state | stop | destroy | containers RSS (final) | VM disk dir |
|---|---|---|---|---|---|---|---|---|---|---|
| `alpha-full` | 23.3 s | 126.2 s (100.5 s) | 122.3 s | 87.7 s | n/a | n/a / n/a | 25.5 s | 40.3 s | 6190 MiB | 10.8 GiB |
| `beta-focused` | 25.5 s | 69.4 s (41.0 s) | 83.2 s | n/a | 63.9 s | 8.3 s / 18.8 s | n/a | 37.7 s | 5310 MiB | 9.0 GiB |

Destroy of `alpha-full` (compose down -v + volumes/networks sweep + VM delete): 23.3 s; recreation from the manifest booted in 122.3 s.

## Host limits

| host | cpus | memory | free disk (home) | VMs at collection time |
|---|---|---|---|---|
| Mac17,9 (macOS-26.6.1-arm64-arm-64bit-Mach-O) | 15 | 24.0 GiB | 296 GiB | default Running cpu=4 mem=12884901888 |

Clean-checkout gate (M9-18): {'instance_id': 'm9-clean-7c52bd', 'health': {'healthy': True, 'containers': {'healthy': 97, 'not_running': ['ledger-scheduler'], 'running': 97, 'total': 98, 'unhealthy': []}}, 'journey': {'summary': {'blocked': 0, 'expected_failure': 0, 'fail': 0, 'pass': 1, 'total': 1}, 'attributed': True}, 'manifest': {'architecture_snapshot_id': '5b6a5dade17eba6c0a407d3d4ba8423c8bf0b8ebeb9ea2bc3706483e04e8d141', 'recipe_set_id': '85f237c46431c2af8b1ad9818a9e3b266080c7db829c198bf8811c8c02ee1bc0', 'profile': 'full', 'seed': 'acceptance-7c52bd', 'inputs_hash': '86423bfb9fe8fdc0deb1c633beb371cd6745190fa863c5c3

## Blockers before scaling from two instances to ten

1. **Host memory**: a full instance is sized at 10 GiB (containers ~6190 MiB at rest) and a focused one at 6 GiB; the host has 24 GiB with the historical 12 GiB M7 VM still defined. Ten full instances need ~100 GiB — a remote-docker or cloud backend (the `Backend` interface is ready; `remote-docker` is unexercised).
2. **Colima start is serialized per profile** and VM creation takes ~23.3 s; ten VMs also need ~10 × 40 GiB sparse disks and ten ssh port forwarders.
3. **Image distribution**: every daemon loads ~34 images (2 GB) from the local tar cache; at ten instances a registry (push once, pull by digest) replaces `docker load`.
4. **Journey runner concurrency**: one runner process per instance is safe (instance-scoped campaign prefixes, run dirs and reports), but the runner still drives Docker through the CLI per call; ten concurrent suites will be CLI-bound.
5. **Source-to-Pay build** is a host-side Go build (~3 min) producing one image tag per driver hash; fine for ten, but the pinned repos live in `~/.rzp-architecture-replica` (outside the factory home).
6. **Migration assets and fts config** come from the checkout's `.local/` copies or the factory-home input bundle; they are credential-free but not committed (a content-addressed input store would make them portable like images).

## Gates

| gate | result | description |
|---|---|---|
| M9-01 | PASS | Historical milestone tags unchanged (M3.1..M8) |
| M9-02 | PASS | Branch descends from the M8 tag |
| M9-03 | PASS | M7 artifacts and the accepted M8 snapshot (id recomputes, manifest ok, inputs byte-identical) unchanged |
| M9-04 | PASS | Runtime profiles are derived from the M8 snapshot + recipes (full = every recipe; focused = traced closure, no handwritten list) |
| M9-05 | PASS | Instance manifests bind snapshot id, recipe set, profile, seed, config hashes, secrets digest, image digests and a RuntimeInstance id |
| M9-06 | PASS | Every service of every instance resolved to an immutable image id (sha256) recorded in the manifest |
| M9-07 | PASS | Two instances ran concurrently on separate daemons/VMs (proof P1-P4: boundaries, namespaces, filesystem, reachability) |
| M9-08 | PASS | A full-profile journey and a focused-profile journey passed, attributed to the right instance (proof P5) |
| M9-09 | PASS | Restart, reset, state snapshot/restore and destroy isolation controls passed (proof P6-P9) |
| M9-10 | PASS | Image and configuration reproducibility: the destroyed instance was recreated from its manifest with identical inputs hash, profile digest, seed epoch and image ids |
| M9-11 | PASS | No hardcoded M7 instance names (env2_compose / rzp-arena / 18080) in twinfactory; journey runner is instance-aware through the environment |
| M9-12 | PASS | Generated secrets/config/seeds/runtime files live outside the source checkout |
| M9-13 | PASS | Source-to-Pay endpoints and secrets are instance-configurable (driver env overrides, twin overlay, per-instance secret observed) |
| M9-14 | PASS | Durable instance registry with schema, live records and destroyed history |
| M9-15 | PASS | Measured memory, disk, build, boot, reset, stop and destroy times and the host limits are recorded |
| M9-16 | PASS | twinfactory + isolation tests, archkit (M8), snapshot and RED_LOOP test suites all pass |
| M9-17 | PASS | Final report, runbook and schema present |
| M9-18 | PASS | A clean checkout (fresh git worktree, no .local, no generated state) provisions, boots and passes a journey in a FULL twin from the M8 snapshot on its own colima boundary |
| M9-19 | PASS | Hash manifest binds twinfactory, the M9 evidence, the isolation proof and the changed runtime files |
| M9-20 | PASS | Working tree clean at evaluation time (only the acceptance outputs may differ) |

