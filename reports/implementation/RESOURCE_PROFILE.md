# Local resource profile

Observed arena-container resource use is recorded in [resource-profile.json](resource-profile.json). These are sampled peaks across three recorded windows, not instantaneous maxima or production sizing.

| Phase | Samples | Peak CPU cores | Peak memory GiB | Disk GiB |
|---|---:|---:|---:|---:|
| idle | 6 | 1.0203 | 4.1791 | 4.3075 |
| successful_payout | 2 | 1.0022 | 4.0988 | 4.3159 |
| full_verifier | 15 | 1.4038 | 4.1444 | 4.3710 |

All phases have at least two real samples. Successful-payout and full-verifier wrappers record successful workload exit codes. All three windows belong to the final replay20 boot and contain the same 65 persistent container identities; timestamps and container identities are retained per sample. The full verifier passed all 26 cases, and the audited Kong payout passed. The full-suite measurement includes its Compose-labelled verifier. Standalone packet-capture/analyzer containers started with raw `docker run` lack the Compose project label and are excluded from CPU/RAM sampling.

CPU and memory cover Compose-labelled arena containers only; Docker daemon/VM and standalone packet-capture/analyzer overhead are unmeasured. Memory uses Docker CLI cache-adjusted values. Disk adds each distinct image's virtual size, Docker volume sizes, writable layers and measured bind paths; shared image layers may be counted more than once. Inaccessible daemon logs and unlisted upload archives/staging space are excluded. Sampling can miss brief spikes.

The unchanged Daytona sizing rule (25% headroom, integer rounding, two GiB additional Docker memory and ten GiB disk staging) produces a **planning floor of 2 CPU, 8 GiB RAM and 16 GiB disk**. This is not a remote performance guarantee. Account for standalone packet-capture/analyzer overhead as well as the actual approved bundle and snapshot before selecting a platform tier. No paid resources have been allocated. See [Daytona resource assumptions](../../DAYTONA/resource-profile.md).

Reproduction commands, phase observation requirements and accounting definitions are in [the profiler guide](../../ENV2_COMPOSE/scripts/resource-profile.md). The final profile was updated at 2026-09-05 14:06:06 UTC; SHA-256 `d8ffc2909cbfb9f97ba1a61ba2c2b4f9af33ca0cf3043c74804d16b9f92175ac`. Independent checks verified phase/sample counts, container CPU/RAM sums, phase maxima and disk component sums. The raw phase records remain authoritative if this table is regenerated after another measurement.
