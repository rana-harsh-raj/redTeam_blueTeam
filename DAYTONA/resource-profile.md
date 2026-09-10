# Resource selection

No Daytona resources have been allocated. Local measurements now cover idle, one successful payout and a passing full verifier suite. The existing runner formula selects a **planning floor of 2 CPU / 8 GiB RAM / 16 GiB disk** from these measurements. This is not an approved allocation or a demonstrated Daytona capacity requirement: Docker daemon/VM usage and some disk components remain unmeasured.

Source: [resource-profile.json](../reports/implementation/resource-profile.json), schema version 1, updated **2026-09-05 14:06:06 UTC**. Profile SHA-256 at review: `d8ffc2909cbfb9f97ba1a61ba2c2b4f9af33ca0cf3043c74804d16b9f92175ac`.

| Phase | UTC observation window, 2026-09-05 | Samples | Sampled CPU peak, cores | Sampled RAM peak, GiB | Observed disk peak, GiB | Workload evidence |
|---|---|---:|---:|---:|---:|---|
| Idle | 14:04:20–14:04:41 | 6 | 1.0203 | 4.1791 | 4.3075 | Duration watcher; no workload outcome attested |
| Successful payout | 14:05:58–14:06:06 | 2 | 1.0022 | 4.0988 | 4.3159 | Command exit 0; [golden result](../reports/implementation/runs/final-acceptance/golden/live-result.json) passed |
| Full verifier | 14:03:19–14:04:11 | 15 | 1.4038 | 4.1444 | 4.3710 | Command exit 0; [replay20 JUnit](../reports/implementation/runs/replay20/full/junit.xml) has 26 passed, 0 failed, 0 skipped |

The profiler is [resource-profile.py](../ENV2_COMPOSE/scripts/resource-profile.py). CPU and memory cover 65 running arena containers, with 66 observed in some full-suite samples. CPU is Docker CLI percent divided by 100; RAM is its cache-adjusted usage. Configured sample intervals are 2 seconds for every phase; each sampling operation also takes time. The actual observation windows are approximately 20.4, 8.3 and 52.0 seconds. These are sampled peaks, not instantaneous maxima or workload execution durations.

Disk is the sum of arena image virtual sizes, named/anonymous volumes, writable container layers, selected bind/explicit paths and accessible container logs. Shared image layers can be counted more than once across different images; volume sizes inherit Docker CLI rounding. Host-wide used/free disk is recorded only as context and is not added to arena usage. CPU/RAM exclude the Docker daemon and VM, plus standalone packet-capture/analyzer containers launched through raw `docker run` without the Compose project label. The Compose-launched verifier is included. Disk excludes inaccessible daemon/container logs, build cache, unrelated resources and unlisted archive/staging paths; the samples report 66–67 inaccessible container logs. No image-archive staging measurement is established by this profile.

The three phases were captured in different windows of the final replay20 boot; their 65 persistent container identities agree. The separate [final replay comparison](../reports/implementation/logical-replay-final.json) validates replay19 and replay20 with 26 passing cases each and all 26 logically equivalent. Measurement and replay success do not resolve the remaining local acceptance gaps or authorize remote execution.

The existing selection formula remains unchanged:

- CPU: `max(2, ceil(1.4038 × 1.25)) = 2`.
- RAM: `max(4, ceil(4.179068359375 × 1.25 + 2)) = 8 GiB`.
- Disk: `ceil(4.371029798872769 × 1.25 + 10) = 16 GiB`.

The 25% headroom, additional 2 GiB Docker RAM and 10 GiB staging allowance are heuristics, not measurements or capacity guarantees. Before approving an allocation, account for the Docker daemon/VM and standalone packet-capture/analyzer CPU/RAM, exported image archive plus expanded images, volume growth and capture/log files. Retain at least two samples per phase, actual windows and underlying artifacts in acceptance evidence. The full dependency graph boots for smoke, so fewer selected tests do not justify a smaller datastore footprint. Select the smallest approved snapshot matching the evidence-backed dimensions; revise the profile with evidence if available platform tiers require another allocation. Do not repeatedly create sandboxes while tuning.

The schema consumed by the runner remains `schema_version: 1`, with `phases.idle`, `phases.successful_payout` and `phases.full_verifier`. Each requires `observed: true`, `sample_count >= 2` and positive numeric `peak_cpu_cores`, `peak_memory_gib` and `disk_gib`. The current file satisfies that shape. Independent checks also confirmed sample counts, per-container CPU/RAM sums, phase peak calculations and disk component sums. No schema, selection formula or acceptance gate was changed.

The default unapproved dry-run now reads the fixed workspace `reports/implementation/resource-profile.json` through the existing sizing validator and displays the measured planning floor even when the execution plan or acceptance is blocked. The [final dry-run output](../reports/implementation/offline-final/daytona-dry-run.final.json) displays 2 CPU / 8 GiB / 16 GiB with zero API calls and no sandbox. Its output binds the estimate to the profile SHA-256 and marks `execution_authorized: false`. It never follows a resource-profile path from an unvalidated plan. When a required phase is unobserved or unavailable, it retains `resource_estimate: null`, an explicit reason and the unmeasured **4 CPU / 16 GiB / 64 GiB placeholder**. The example execution plan remains unapproved; neither the estimate nor its placeholder can allocate resources.

Each actual managed run must record allocated CPU/RAM/disk, creation/deletion timestamps, test outcome, export status, verified deletion and approximate CPU-minutes, memory-GiB-minutes and disk-GiB-minutes. The creation-request-to-deletion-verification interval slightly overestimates sandbox lifetime and is not a billing invoice. Remote duration and dollar cost remain unknown. No remote allocation or billing claim follows from these local measurements.
