# Arena resource profiler

`resource-profile.py` records read-only Docker measurements for `idle`, `successful_payout`, and `full_verifier`. Run it from the repository root after the full arena passes its health checks. It never starts the arena or a workload unless a command is explicitly supplied after `--`.

The default output is `reports/implementation/resource-profile.json`, using the schema consumed by the Daytona resource gate. Each invocation replaces only its named phase under a file lock. Other phases survive. At least two complete samples are required; missing measurements, interrupted/timed-out runs, failed wrapped commands, and insufficient samples keep `observed` false.

## Invocations

Idle, after the complete arena is healthy:

```sh
python3 ENV2_COMPOSE/scripts/resource-profile.py --phase idle --duration 60 --interval 2
```

Full verifier, using the existing run script and preserving its JUnit, trace and egress artifacts:

```sh
ARENA_SKIP_BUILD=1 python3 ENV2_COMPOSE/scripts/resource-profile.py \
  --phase full_verifier --duration 2400 --interval 2 -- \
  bash ENV2_COMPOSE/scripts/golden-run.sh --with-egress-audit
```

For a successful payout through Kong, use the fixed Explorer runner, which supplies its scoped credential and result mounts:

```sh
python3 ENV2_COMPOSE/scripts/resource-profile.py \
  --phase successful_payout --duration 180 --interval 2 -- \
  python3 ARCHITECTURE_EXPLORER/run-golden.py --with-egress-audit
```

Alternatively, start a duration watcher immediately before running that command in another terminal. Keep its sampling window around the actual workload and retain the independently written successful result as acceptance evidence:

```sh
python3 ENV2_COMPOSE/scripts/resource-profile.py \
  --phase successful_payout --duration 180 --interval 2
```

`--duration` is the watcher duration or wrapped-command timeout. Docker's collection can take longer than `--interval`; actual start/end timestamps are retained. Commands that finish too quickly for two samples cannot satisfy the gate. A watcher only attests that resources were measured, not that the named workload ran or succeeded. A command wrapper records its exit code and returns nonzero when it fails. On timeout or interruption it terminates the process group it created; do not wrap an unrelated long-lived service.

Pass `--project NAME` for a nondefault Compose project. Every selected container must also have this checkout's Compose working-directory label, preventing another project with the same name from being measured. Both persistent and transient verifier containers in that project are included when present. Container membership changes that prevent a complete sample are explicit errors; rerun the phase if `observed` remains false.

## What is measured

- CPU is the sum of `docker stats` percentages divided by 100: 100% equals one CPU core. Memory is summed Docker CLI usage, including its Linux cache adjustment. Per-container service names and measurements are retained.
- Disk sums unique running/stopped project image IDs at Docker's reported virtual size, arena-mounted volume sizes from `docker system df -v`, container writable layers, allocated bytes for accessible bind mounts, the `reports/implementation` tree, and accessible container logs. Overlapping host paths and duplicate image IDs are counted once.
- Shared image layers can appear in several images, so the image contribution is a conservative measured upper bound rather than exact exclusive storage. Docker rounds its reported volume sizes. Inaccessible daemon/VM logs are counted and disclosed, not invented.
- Host filesystem used/free space is context only and is never added to arena disk. Docker daemon/VM CPU and memory, build cache, unrelated containers/images/volumes, and unlisted archive staging are excluded. Daytona's configured overhead/headroom remains a planning allowance, not a measured daemon quantity.

Include actual image archives or other staging paths with repeatable `--disk-path /absolute/existing/path`. Their allocated sizes are measured using `du`; contents are not read. Do not add an entire host home directory. Additional archives that do not exist yet remain unmeasured and require the documented staging allowance or a later measurement.

The script prints only a short phase/status summary. It does not save command arguments, environment, raw Docker inspect/config, or wrapped-command stdout/stderr. Existing acceptance scripts must write their normal sanitized evidence files themselves. Hash the completed resource profile alongside those artifacts in the Daytona execution plan. These are sampled peaks; short spikes and workload changes require further capacity validation.
