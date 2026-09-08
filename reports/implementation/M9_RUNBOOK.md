# M9 runbook — Isolated Twin Factory (`twinfactory`)

Everything below runs from the repository root. The factory home (`$TWIN_FACTORY_HOME`, default `~/.twin-factory`)
holds the registry, the image cache, the credential-free input bundles and every instance's execution directory;
nothing generated ever lands in the checkout.

## One-time seeding of the factory home

```bash
# images the profiles need, exported (docker save) from a daemon that already has them (e.g. the M7 arena's daemon):
python3 -m twinfactory images export --from unix://$HOME/.colima/default/docker.sock --profile full
# credential-free migration assets + fts config from this checkout's accepted copies (.local/, not committed):
python3 -m twinfactory inputs export
```
Digest-pinned public images (`name:tag@sha256:...`, the Source-to-Pay overlay) are pulled by digest per instance; a
`rzp-arena/*` substitute missing from the cache is built with `docker compose build` on the instance daemon and then
exported into the cache so every later instance loads the identical image id.

## Profiles

```bash
python3 -m twinfactory profiles                 # full, critical-payouts, focused-s2p + every derivable family
python3 -m twinfactory profile critical-payouts # the service set with the rule that pulled each service in
```
`full` = every canonical ServiceRecipe of the current M8 snapshot (98) + migration jobs + the Source-to-Pay overlay.
`focused:<family>` = closure over the architecture relationships (rules R1–R11 in `twinfactory/profiles.py`).

## Lifecycle

```bash
python3 -m twinfactory create alpha-full   --profile full             --seed seed-a --backend colima
python3 -m twinfactory create beta-focused --profile critical-payouts --seed seed-b --backend colima
python3 -m twinfactory build  alpha-full      # provision the VM, generate seeds/secrets/config, resolve + record image ids
python3 -m twinfactory start  alpha-full      # boot from empty volumes (datastores -> migrations -> seeds -> substitutes -> core -> S2P)
python3 -m twinfactory health alpha-full      # exit 0 iff every container healthy and the loopback bridge answers
python3 -m twinfactory journeys alpha-full --only journey:cross-domain-s2p/success
python3 -m twinfactory reset alpha-full       # redis flush, SQS purge, re-seed fixtures, ingress reset, fresh S2P stack
python3 -m twinfactory snapshot-state alpha-full --label before ; python3 -m twinfactory restore-state alpha-full --label before
python3 -m twinfactory stop alpha-full [--boundary]   # stop containers (and optionally the VM); start resumes
python3 -m twinfactory destroy alpha-full [--purge]   # compose down -v, sweep, delete the VM, registry history
python3 -m twinfactory ls | manifest <id> | events <id> | status <id>
python3 -m twinfactory reproduce alpha-full alpha-2   # new instance bound to the same snapshot/recipes/profile/seed/images
```
Make targets: `make m9-create ID=.. PROFILE=.. SEED=..`, `m9-build`, `m9-start`, `m9-status`, `m9-journeys`, `m9-reset`,
`m9-stop`, `m9-destroy`, `m9-ls`, `m9-images FROM=..`, `m9-test`, `m9-isolation A=.. B=..`, `m9-acceptance`, `m9-report`.

Per-instance layout, manifest and RuntimeInstance fields: `twinfactory/SCHEMA.md`. Every boot/reset/stop/destroy step
is logged under `<instance>/logs/NN-<step>.log`; evidence (fingerprint, boot steps, journey attribution, reset,
destroy) under `<instance>/evidence/`.

## Backends

`colima` (default, the isolation boundary used in the proof: one profile `twin-<id>` = one vz VM with its own
dockerd, sized from the profile, mounting ONLY the instance's execution directory), `local-docker` (the caller's daemon,
development only, reports `isolation_boundary=false`), `remote-docker` (`--docker-host ssh://...`, interface-compatible,
not exercised). The factory never reads `DOCKER_HOST` from the caller for a colima/remote instance.

## Isolation proof and acceptance

```bash
python3 -m twinfactory isolation run --a alpha-full --b beta-focused [--resume]   # P1..P9, checkpointed
python3 -m twinfactory evidence --ids alpha-full,beta-focused --proof reports/implementation/m9-isolation-proof.json
python3 -m twinfactory acceptance    # 20 gates incl. a live clean-checkout FULL provisioning; -> M9_ACCEPTANCE.json
python3 -m twinfactory report        # -> M9_FINAL_REPORT.md
```
The acceptance's live gate creates a fresh `git worktree`, provisions `m9-clean-<sha>` on its own colima profile,
boots the full profile, runs a journey and destroys it (allow ~10 minutes and ~10 GiB of memory).

## Gotchas

- `create --force` on an existing id clears the execution directory's contents but keeps the directory inode: a kept
  VM mounts it by path and a new inode would leave a stale mount inside the VM.
- Colima's default `--activate` switches the caller's docker context; the factory always passes `--activate=false`.
- `colima delete` keeps the container-runtime data disk (`~/.colima/_lima/_disks/colima-<profile>`) unless `--data` is
  given; the factory always passes it, otherwise a re-provisioned instance inherits stale volumes and containers.
- A VM is sized from the profile's service count (`twinfactory/plan.py sizing_for`): a 93-service instance on a 6 GiB
  VM starves (kafka/mongo unhealthy); override with `--memory` only upwards.
- The M6/M7 scripts (`ENV2_COMPOSE/scripts/up.sh`, `RED_LOOP/m7/*`) still address the historical arena; the factory
  never calls them. The journey runner is shared and instance-aware through `ARENA_ENV2_ROOT`, `ARENA_COMPOSE_PROJECT`,
  `ARENA_SUFFIX`, `ARENA_NETWORK`, `ARENA_S2P_NETWORK`, `KONG_LITE_HOST_URL`, `TWIN_RUNS_DIR`, `TWIN_REPORTS_DIR`,
  `TWIN_INSTANCE_ID` and `DOCKER_HOST`; with none of them set it behaves exactly as before.
