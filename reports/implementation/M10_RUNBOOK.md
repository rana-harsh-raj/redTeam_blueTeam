# M10 Runbook — Durable Autonomous Campaign Control Plane

Start tag: `twin-m9-isolated-factory`. Package: `controlplane/` (stdlib + PyYAML;
reuses `archkit`, `twinfactory`, and the RED_LOOP substrate under `RED_LOOP/red_loop`).

Durable home (outside the checkout): `$CAMPAIGN_CONTROL_HOME` (default
`~/.campaign-control`). One campaign per `campaigns/<campaign_id>/` with
append-only JSONL ledgers, content-addressed evidence, checkpoints, and per-worker
subdirs. Nothing authoritative lives in memory; a killed Director resumes from disk.

## Deterministic proof (no Docker, no model provider)
```
make m10-test            # control-plane unit + recovery tests
make m10-proof           # one reproducible model-free/docker-free campaign
make m10-acceptance      # evaluate all M10 gates -> reports/implementation/M10_ACCEPTANCE.json
```
`m10-acceptance` also runs the archkit/twinfactory/snapshot/RED_LOOP suites and
writes `M10_ARTIFACT_HASHES.json`.

## Lifecycle (CLI)
```
python3 -m controlplane create  --mandate "<broad mandate, no target>" --snapshot <snap id> \
    --twin alpha:full:seed-a:merchant_ordinary:full \
    --twin beta:critical-payouts:seed-b:merchant_fresh:focused \
    --model claude-opus-4-8 --model gpt-5.5 --model gemini-2.5-pro \
    --mode broad_autonomous --budget planning_cycles_min=3 --budget max_actions=4000
python3 -m controlplane start   <campaign_id>            # live: real twins + gateway
python3 -m controlplane start   <campaign_id> --fake-model   # real twins, deterministic model policy (declared action budget)
python3 -m controlplane start   <campaign_id> --fake         # fully fake (no docker/gateway)
python3 -m controlplane pause    <campaign_id>
python3 -m controlplane resume   <campaign_id> [--fake]
python3 -m controlplane drain     <campaign_id>
python3 -m controlplane terminate <campaign_id> --reason ...
python3 -m controlplane status|narrative|coverage|allocation|cost|results <campaign_id>
python3 -m controlplane manifest|timeline|report <campaign_id>
python3 -m controlplane ls
```
Kill switch: `touch $CAMPAIGN_CONTROL_HOME/campaigns/<id>/STOP`. Graceful pause:
`touch .../PAUSE` (or `pause`), then `resume`.

## Live run (two isolated M9 twins)
1. Provision two M9 twins on separate colima VMs (see M9 runbook), e.g.
   `python3 -m twinfactory create alpha full seed-a` then `build`/`start`; likewise
   `beta critical-payouts seed-b`.
2. `create` a campaign pinned to the current snapshot and both instance ids.
3. `source RED_LOOP/llm.env` (gateway) for a model run, or `start --fake-model` for
   the reproducible declared-action-budget run.
4. Workers run as twin-scoped subprocesses (`controlplane.worker_main`); each reaches
   only its assigned twin's kong-lite through the RED_LOOP typed broker. Verification
   runs `controlplane.verify_main` in a twin-scoped subprocess (independent replay by a
   different merchant + the deterministic judge). Neither the Director nor a worker
   verifies its own claim.

## Injected-failure drills (recovery)
- worker: kill a `controlplane.worker_main` subprocess; the lease expires and the task
  is requeued.
- Director: kill the `start` process; re-run `resume` (or `start --resume`); state
  continues from the last durable records.
- model: a gateway timeout/refusal triggers fallback to a different pool model, then
  requeue; injected deterministically in the proof.
- twin: stop a twin; the engine health-checks it, reproduces a replacement through
  twinfactory, re-homes its cells and requeues its tasks.

## Honesty
A campaign may end with zero verified findings. A candidate is promoted only when an
independent, decorrelated replay reproduces it AND the deterministic judge confirms it
over authoritative twin state. Inconclusive evidence never promotes a candidate.
