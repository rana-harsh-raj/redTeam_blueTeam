# M6 runbook — complete Payouts functional domain twin

All commands run from the repo root on branch `milestone-6-complete-payouts-domain`
(or a detached checkout of tag `twin-m6-complete-domain`). Source clones are read from the
path in `.local/repos-root`; the admitted build copies are `.local/twin-repos/accepted/*`.

## One-command acceptance (clean checkout, no arena needed)

```bash
make m6-acceptance          # -> reports/implementation/m6-acceptance.json
```

## Rebuild the functional graph from the discovery parts

```bash
make m6-graph               # parts/*.json (+ runtime overlay, executed journeys) -> reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.*
make m6-inventory           # REPOSITORY_INVENTORY_M6.csv + REMAINING_ACCESS_MANIFEST.md
```

## Boot the domain from empty state and run the journey suite

```bash
# images must exist at ARENA_TAG (see reports/implementation/BUILD_PROVENANCE.md for the 5 real services;
# substitutes: docker compose --env-file ENV2_COMPOSE/.env.arena -f ENV2_COMPOSE/docker-compose.yml build)
make m6-clean-boot          # down -> empty volumes -> fresh secrets/config/seeds -> up -> RED_LOOP/m6/journeys/run.py
                            # -> reports/implementation/m6-clean-boot.json + m6-journeys.json + m6-journey-coverage.md
make m6-journeys ARGS="--only journey:approval-workflow/success"   # rerun a subset on the live arena
```

Post-boot checklist: `ENV2_COMPOSE/M6_RUNTIME_CHANGES.md` §8 (expect 79 running containers +
`ledger-scheduler` exited by design, 25 payouts workers healthy, workflow-engine and batch-sim healthy).

## Daily incremental snapshot / weekly full rebuild

```bash
make m6-snapshot                       # capture -> reports/domain/snapshots/<UTC>.json (+latest.json)
make m6-snapshot-diff ARGS="<old> <new>"   # exit 3 when anything changed
make m6-affected ARGS=<diff.json>      # rebuild: [...] rerun_journeys: [...] regen_config: bool
make m6-refresh-daily  [ARGS=--execute]    # capture -> diff -> affected -> (rebuild affected, rerun affected journeys)
make m6-refresh-weekly [ARGS=--execute]    # prepare-repos -> check-inputs -> build -> clean boot -> full suite -> acceptance
make m6-recipes                        # reports/domain/recipes/<service>.yaml + SNAPSHOT_MANIFEST.json
python3 RED_LOOP/m6/refresh_demo.py --execute   # the metric-8 proof: contract change -> diff -> affected-only rerun
```

Cadence (see `scripts/snapshot/README.md` for cron/launchd lines): daily snapshot+diff+affected
validation; weekly full clean rebuild + acceptance.

## What each artefact proves

| artefact | claim |
|---|---|
| `reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json` + `GRAPH_STATS.json` | complete domain graph; critical-component representation and runtime realization percentages |
| `reports/domain/FIDELITY_CLASSIFICATION.csv` | every node: real running / mapped not running / high-fidelity replacement / behavioural placeholder / graph-only / blocked |
| `reports/domain/REPOSITORY_INVENTORY_M6.csv` | every reachable repository + deployment artefact + blocked repos |
| `reports/domain/REMAINING_ACCESS_MANIFEST.md` | what prevents the final ~1% |
| `reports/domain/recipes/*.yaml`, `SNAPSHOT_MANIFEST.json` | snapshot-aware service recipes + domain snapshot manifest |
| `reports/implementation/m6-journeys.json`, `m6-journey-coverage.md` | executed business journeys per family × variant |
| `reports/implementation/m6-clean-boot.json` | boot from empty state, fresh merchants, health, no stale local state |
| `reports/implementation/m6-refresh-demo.json` | incremental change → affected-only rerun |
| `reports/implementation/m6-acceptance.json` | the gates |
