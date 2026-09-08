# M6 snapshot / refresh tooling

Keeps the Payouts functional-architecture twin **honest over time**: what it is built
from, what changed since yesterday, what that change invalidates, and how every
component is built, run, checked and seeded.

Everything here is read-only unless you pass `--execute`. Nothing in
`ENV2_COMPOSE/secrets/`, `ENV2_COMPOSE/config/generated/`, `ENV2_COMPOSE/seeds/generated/`,
`.env*` or `.runtime/` is ever read, hashed or printed. `capture.py` runs without docker.

| script | what it does | writes |
|---|---|---|
| `capture.py` | pin the whole twin: repos, recipes, config, schemas, contracts, flags, images, compose, graph | `reports/domain/snapshots/<UTC>.json` + `latest.json` |
| `diff.py <old> <new>` | what moved between two snapshots (exit **0** = no change, **3** = changed) | `--json <path>` |
| `affected.py <diff.json>` | `rebuild` / `rerun_journeys` / `regen_config` from the diff | `--json <path>` |
| `recipes.py` | one build/run/seed recipe per compose service and per source repo | `reports/domain/recipes/*.yaml`, `reports/domain/SNAPSHOT_MANIFEST.json` |
| `refresh.py daily\|weekly\|status` | the runbook below, as a machine-readable plan | `reports/domain/refresh/<UTC>-{daily,weekly}.json` |
| `common.py` | shared path/git/hash/graph helpers (imports `ENV2_COMPOSE/scripts/fingerprint.py`, never duplicates it) | — |

Python 3.11+, stdlib + PyYAML. All paths are resolved from the script location, so
the commands below work from any working directory.

---

## Daily runbook (~1 min, no docker required)

```bash
# 1. plan only: capture -> diff vs latest -> affected -> reports/domain/refresh/<UTC>-daily.json
python3 scripts/snapshot/refresh.py daily
make m6-refresh-daily                       # same thing

# 2. read the plan
python3 scripts/snapshot/refresh.py status
cat reports/domain/refresh/$(ls -t reports/domain/refresh | head -1)

# 3. act on it (rebuilds ONLY affected core services, then the affected journeys)
python3 scripts/snapshot/refresh.py daily --execute
```

`daily` never deletes or overwrites a snapshot: `capture.py` allocates a fresh
`<UTC>.json` (and `<UTC>-1.json`, ... on a collision) and only the `latest.json`
pointer is rewritten. Without `--execute` nothing outside `reports/domain/` is touched.

With `--execute`:

* every affected **core** service (`payouts`, `ledger`, `fts`, `cfa`, `xbalances`)
  is rebuilt through `ENV2_COMPOSE/build/record-rebuild.py <target> --base-evidence
  <latest .local/twin-repos/build-evidence-*> --repos-root .local/twin-repos/accepted`,
  which drives `build-host.sh` and records fresh build evidence. Affected
  substitutes are listed but **not** rebuilt by `daily`;
* if the docker daemon is unreachable, those steps are recorded as
  `skipped: no docker (<reason>)` — never silently dropped;
* affected journeys run as `python3 RED_LOOP/m6/journeys/run.py --only <comma,separated,ids>`.
  While that runner does not exist yet, the exact command is *recorded* in the plan
  instead of being run (and, in graph-less fallback mode, the concrete driver
  commands for each id are recorded under `steps[].drivers`);
* `regen_config` is reported but never executed here: config is rendered from
  `secrets/` by `ENV2_COMPOSE/scripts/up.sh` on the next boot.

Running the stages by hand is equivalent:

```bash
python3 scripts/snapshot/capture.py                                   # make m6-snapshot
python3 scripts/snapshot/diff.py <old.json> <new.json> --json /tmp/d.json   # make m6-snapshot-diff ARGS="<old> <new>"
python3 scripts/snapshot/affected.py /tmp/d.json --json /tmp/a.json   # make m6-affected ARGS=/tmp/d.json
```

## Weekly runbook (full re-pin; rebuilds and reboots the arena)

```bash
python3 scripts/snapshot/refresh.py weekly            # plan only  (make m6-refresh-weekly)
python3 scripts/snapshot/refresh.py weekly --execute  # run it, stopping at the first failure
```

Order (each step recorded with rc / duration / output tails):

1. `python3 ENV2_COMPOSE/build/prepare-repos.py --source-root <repos-root> --destination .local/twin-repos/refresh-<UTC>`
2. `python3 ENV2_COMPOSE/build/check-inputs.py <new build-evidence>/provenance.json --verify-modules`
3. `REPOS_ROOT=<destination> ARENA_TAG=<tag> bash ENV2_COMPOSE/build/build-host.sh all`
4. `python3 RED_LOOP/m6/clean_boot.py`  ← the only step that touches the live arena
5. `python3 RED_LOOP/m6/journeys/run.py` (full suite)
6. `python3 RED_LOOP/surface/m6_acceptance.py`

`--repos-root`, `--tag` and `--destination` override the defaults (`.local/repos-root`,
`ARENA_TAG` from `ENV2_COMPOSE/.env.arena`, a fresh `.local/twin-repos/refresh-<UTC>`).
`prepare-repos.py` refuses to overwrite an existing destination, so a weekly run can
never clobber `.local/twin-repos/accepted`.

## Recipes and the manifest

```bash
python3 scripts/snapshot/recipes.py          # make m6-recipes
python3 scripts/snapshot/recipes.py --list   # recipe ids only
```

One `reports/domain/recipes/<id>.yaml` per compose service (`payouts-api`, `kong-lite`,
`mysql-payouts`, ...) and per source repo (`repo-payouts`, `repo-workflows`,
`repo-batch`, `repo-proto`, ...), each carrying `repository`, `sha`, `build_command`,
`runtime`, `health_check`, `dependencies`, `schemas_and_contracts`,
`configuration_hash`, `database_migrations`, `synthetic_seed_generator`,
`external_replacements` and a `fidelity_label` + `fidelity_reason` (from the functional
graph when it exists, else the M4/M5 fidelity matrices, else a conservative default
that states why). `reports/domain/SNAPSHOT_MANIFEST.json` indexes them alongside
`repositories`, `config_hashes`, `schema_versions`, `image_digests` and `graph_version`.

## Functional graph

`affected.py` uses `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json` (schema:
`reports/domain/SCHEMA.md`) when it exists:

```
repo change      -> svc/worker/cron nodes with that repo -> their families' journeys
schema change    -> nodes owning table:<svc>/*            -> journeys + <svc>-migrate
proto contract   -> the service owning the module         -> rebuild + journeys
flag change      -> feature_flags / gated_by edges        -> journeys + reseed the stub
substitute       -> `implements` edges                    -> the svc it stands in for
```

Until the graph lands, a conservative hardcoded map (repo -> existing journey drivers
in `RED_LOOP/`) is used and every plan is marked `fallback_used: true`.

## Tests

```bash
python3 -m unittest discover -s scripts/snapshot/tests      # 36 tests
```

They build a synthetic mini-twin in a temp directory (plus the fixture graph in
`scripts/snapshot/tests/fixtures/graph.json`) and additionally capture the real
repository with docker switched off.

## Scheduling

```
# crontab -e  --  daily plan at 07:15 local, weekly plan Mondays at 03:00
# 15 7 * * *   cd /Users/rana.singh/rzp-payouts-architecture && /usr/bin/python3 scripts/snapshot/refresh.py daily  >> .local/refresh-daily.log 2>&1
#  0 3 * * 1   cd /Users/rana.singh/rzp-payouts-architecture && /usr/bin/python3 scripts/snapshot/refresh.py weekly >> .local/refresh-weekly.log 2>&1
# add --execute only on a machine that is allowed to rebuild images and reboot the arena.
```

```xml
<!-- ~/Library/LaunchAgents/com.razorpay.twin.refresh-daily.plist  (launchctl load -w <path>)
<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>Label</key><string>com.razorpay.twin.refresh-daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/rana.singh/rzp-payouts-architecture/scripts/snapshot/refresh.py</string>
    <string>daily</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/rana.singh/rzp-payouts-architecture</string>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>15</integer></dict>
  <key>StandardOutPath</key><string>/Users/rana.singh/rzp-payouts-architecture/.local/refresh-daily.log</string>
  <key>StandardErrorPath</key><string>/Users/rana.singh/rzp-payouts-architecture/.local/refresh-daily.log</string>
</dict></plist>
-->
```
