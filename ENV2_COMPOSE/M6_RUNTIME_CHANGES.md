# M6 runtime construction (lane I3) — every change, and how to boot it

Scope: the arena's runtime shape only — `docker-compose.yml`, `config/**`,
`.env.arena`, `secrets/{gen-secrets.sh,materialize.py}`, `scripts/up.sh`,
`substitutes/workflow-engine/**`, plus integrating lane I2's `batch-sim`.

**Nothing here was booted.** The live arena (compose project `env2_compose`,
67 running + 1 exited) was not started, stopped, restarted or rebuilt. Every
validation below ran against `docker compose config`, throwaway
`docker run --rm` containers on a throwaway network, or host processes.

Headline: **three latent blockers were found and fixed** (§6). Any one of them
would have made the approval family silently fail the moment it was switched on.

---

## 1. Summary of changes

| # | Change | Files |
|---|---|---|
| A1 | `workflow-engine` is buildable as a container (package-aware Dockerfile + arena entrypoint) | `substitutes/workflow-engine/{Dockerfile,.dockerignore,arena_entrypoint.py}` |
| A1 | Engine gained arena integration: real `callback_details`, `/health`, `/_arena/*`, token files, 14-char ids, merchant-scoped tenancy, default policy binding | `substitutes/workflow-engine/wfengine/{server,callbacks,identity}.py` |
| A1 | New generated secret `wfe_admin_token`; `auth_workflow_payouts` now actually reaches its consumers | `secrets/gen-secrets.sh`, `secrets/materialize.py`, `docker-compose.yml` |
| A2 | `workflow-engine` compose service (profile `substitutes`) + `secrets-workflow` and `wfengine-data` volumes | `docker-compose.yml` |
| A3 | `ARENA_WORKFLOW_HOST=http://workflow-engine:8093` is the **default** | `.env.arena`, `config/generate.py`, `config/templates/base/payouts/arena.toml`, `scripts/up.sh` |
| A4 | Operator documentation | `substitutes/workflow-engine/ARENA.md` |
| B | 10 missing payouts workers added — all 25 registered jobs now have a container | `docker-compose.yml` |
| B | FTS Kafka producer leg: documented, **not flipped**; already an `ARENA_*` switch | `config/templates/base/fts/env.arena.toml`, `.env.arena` |
| C | `batch-sim` (lane I2) integrated: compose service, volume, `arena.yaml`, health gate, image built | `docker-compose.yml`, `config/arena.yaml`, `scripts/up.sh` |
| — | Safety report string kept accurate | `preflight/preflight.py` |

Container count goes from **68 → 80** (79 long-running + `ledger-scheduler`,
which is a one-shot that exits 0): +10 workers, +`workflow-engine`, +`batch-sim`.
Memory: each payouts worker is ≈41 MiB and the two substitutes are Python stubs,
so ≈ +0.5 GiB against ~7.3 GiB free on the docker VM.

---

## 2. A — workflow-engine into the arena

### 2.1 Why this engine at all

`substitutes/workflow-engine` is the durable maker/checker approval engine
reconstructed and validated in M5 (`reports/implementation/m5-source-map.md`,
`m5-runbook.md`, the 12-scenario suite behind `make m5-scenarios`, and the blind
benchmark). Until M6 it existed only as host processes for that suite. Wiring it
into the arena turns "an approver" into a first-class arena identity — orgs,
actors, roles, approval policies, distinct-approver counting, maker≠checker
separation, optimistic concurrency, expiry, an append-only audit trail, durable
retrying callbacks — instead of the out-of-band control-plane poke that
`workflow-sim` offers.

`workflow-sim` is **kept and still selectable**; nothing about it was removed.

### 2.2 Buildability (Dockerfile)

The shared `substitutes/Dockerfile` does `COPY ${STUB_DIR}/*.py /app/`, which
copies only top-level modules. The engine is a package (`wfengine/` +
`schema.sql`), so it needs its own package-aware Dockerfile:
`substitutes/workflow-engine/Dockerfile`, build context
`./substitutes/workflow-engine`.

Everything else follows the shared runtime's conventions: `python:3.12-alpine`,
`adduser -D -u 10001 stub`, stdlib-only (no pip, no network at build or run),
`HEALTHCHECK` on `/health`. One addition: `RUN mkdir -p /data && chown
10001:10001 /data`, so a fresh named volume mounted at `/data` inherits uid-10001
ownership and the non-root process can write its SQLite db under a read-only
root filesystem. (Lane I2 hit and documented the identical problem for
`batch-sim`; both would go away if `substitutes/Dockerfile` gained that one line
— see that Dockerfile's header. Left alone here: it is a shared file and not in
this lane's ownership.)

`arena_entrypoint.py` is the container entry. `run_engine.py` is **untouched**
and remains the standalone entry used by `RED_LOOP/m5/*`. The wrapper differs
only in how configuration arrives — credentials from `*_FILE` paths rather than
inline env, `PS_API_URL` pointed at the real payouts-api, arena-specific
defaults. It **fails closed** with no admin token rather than serving an
unauthenticated admin plane.

### 2.3 Engine code changes (finishing the interrupted pass)

An earlier interrupted attempt had already written most of this; it was read,
judged sound, kept, and completed. What is there now:

* **`callbacks.py`** — `_target()` resolves the callback in three priority
  layers: (1) the REAL payouts shape, i.e. `callback_details.workflow_callbacks.
  processed.domain_status.{approved,rejected}.{url_path,method,headers,payload}`
  used **verbatim** and joined onto `PS_API_URL` (`pkg/workflow/workflow_create.go`
  `getCallbackDetails` :265-320, `getCallbackHeaders` :333-338); (2) a fallback
  of `WfCallbackPath + entity_id + /approve|/reject` (`workflow_create.go:25`);
  (3) the legacy `{kind:{url}}` / `WFE_CALLBACK_SINK` shape the M5 suite uses.
  Method and `x-creator-id` / `X-Razorpay-Account` now come from what payouts
  supplied. Success codes stay `200|201|409` (`workflow_create.go:320-325`).
  *Completed in this pass:* the `queue_if_low_balance` default. In arena mode it
  now defaults to whatever payouts put in `callback_details` (matching
  `workflow-sim`'s `_fire_callback`, default `false`); the legacy standalone path
  keeps its historical `true` bit-for-bit.
* **`identity.py`** — `WFE_WORKFLOW_ID_LEN=14` produces 14-char workflow ids,
  because payouts stores them in `CHAR(14)` columns
  (`workflow_entity_map.workflow_id`, `workflow_state_map.workflow_id`). Default
  `0` preserves the M5 `wfl_<16 hex>` form. `create_org(org_id=...)` /
  `ensure_org()` make org creation idempotent and explicitly-addressable.
* **`server.py`** — `/health` (+`/ping`, `/_arena/health`); the `/_arena/*`
  control plane; `WFE_TENANT_KEY`; a payouts-shaped `Create` response
  (`CreateHttpResponse`, `workflow_create.go:119-135`, read by payouts at
  :217-220) layered over the engine's own view so M5 clients still read
  `id`/`state`/`version`; `entity_id` required on Create.
  *Completed in this pass:* the default-policy binding (§2.6), and
  `/_arena/workflows`.

Backward compatibility is the hard constraint and is proven: `make m5-scenarios`
is **12/12** (§5).

### 2.4 Compose service

`workflow-engine`, profile `substitutes`, network `rzp-arena`, no host port.
`<<: *runtime-security` (read-only root, tmpfs `/tmp`, no-new-privileges), uid
10001. Volumes `secrets-workflow:/run/secrets:ro` and `wfengine-data:/data`.
No `depends_on` on `payouts-api` — deliberately, for the same reason
`cron-driver` has none: a `depends_on` across the `substitutes`/`core` profile
boundary breaks `docker compose config` whenever both profiles are not enabled
in one invocation.

Environment (all documented inline in the block): `WFE_SERVICE_USER/PASS =
workflow/workflow` (matching payouts' rendered `[workflow.auth]`,
`arena.toml:308-310`), `PS_API_URL=http://payouts-api:9400`,
`WFE_CALLBACK_USER=rzp_live` + `WFE_CALLBACK_PASS_FILE=/run/secrets/auth_workflow_payouts`
(payouts' `[auth.workflow]`), `WFE_ADMIN_TOKEN_FILE=/run/secrets/wfe_admin_token`,
`WFE_WORKFLOW_ID_LEN=14`, `WFE_TENANT_KEY=owner_id`, `WFE_AUTOPROVISION_POLICY=1`.

### 2.5 Secrets

* `secrets/gen-secrets.sh` — new `wfe_admin_token` (same `rand_password`
  recipe). The `auth_workflow_payouts` comment was corrected: its username is
  `rzp_live` (payouts `[auth.workflow]`), and there IS a Workflow substitute now.
* `secrets/materialize.py` — new group/volume **`secrets-workflow`** →
  `rzp-arena-secrets-workflow${ARENA_SUFFIX}`, carrying `auth_workflow_payouts`
  and `wfe_admin_token` as uid-10001 mode-0400. `auth_workflow_payouts` was also
  added to the existing `secrets-kong` group, because `batch-sim` mounts that
  volume and needs `cred.Workflow`. The **admin token is deliberately not in
  `secrets-kong`** — it must not sit on the ingress container's filesystem; that
  admin/actor plane separation is the property M5 established. The final message
  now counts groups instead of hard-coding "8".
* Compose file-based `secrets:` was **not** used for these: those bind host files
  at mode 0600 owned by the host user, which only works for the root-running
  `cron-driver`/`verifier`. Both workflow substitutes run as uid 10001.
* `secrets/wfe_admin_token.txt` was minted on the host now (additive only —
  no existing secret was rotated or regenerated), so a boot works whether or not
  `REGEN_SECRETS=1` is passed. `.gitignore` already covers `secrets/*.txt`.

### 2.6 Policy binding decision (documented, as asked)

The real engine already knows every merchant and has a workflow config bound to
it by an ops flow; nothing in payouts' `Create` carries one. A brand-new arena
merchant would therefore have no org row (FK violation) and no policy.

**Decision: auto-provision on first Create** (`WFE_AUTOPROVISION_POLICY=1`).
The first `Create` for an unknown `owner_id` creates the org (idempotent) and,
if none exists, binds the default policy: **1 distinct approval, any actor in
that org holding role `approver`, maker≠checker enforced, 24h expiry**. It is a
real persisted `policies` row, so `POST /admin/policies` overrides it at any
time. `WFE_TENANT_KEY=owner_id` makes the engine's org **be** the payouts
merchant — payouts sends `org_id` = the Razorpay org id, which *every* merchant
shares, so keying on it would collapse all merchants into one tenant and destroy
cross-tenant isolation. Actors are **not** auto-created: an approver is an
identity and must be provisioned.

The flag defaults to **off** in the library layer, so `make m5-scenarios`, the
blind benchmark and the M5 campaign are bit-for-bit unaffected; only the arena
compose block turns it on.

Full operator documentation — DCS flags, provisioning, the approver journey,
health/evidence endpoints, the `/_arena/*` compatibility plane and its two
deliberate differences from `workflow-sim` — is in
**`substitutes/workflow-engine/ARENA.md`**.

---

## 3. B — the 10 missing payouts workers

Source of truth: `internal/job/*.go` `init()` registrations into
`internal/job/base.go:198 JobConfigs`, selected at
`pkg/worker/manager.go:149-152` by `config.Worker.Name`, overridden by
`PAYOUTS_WORKER_NAME` (env prefix set at `pkg/config/config.go:104`). There is no
switch in `cmd/workers/main.go` — it is a map keyed by the job's `Name` field.

**25 jobs are registered. The twin had 15. All 25 now have a worker.** Each new
block copies the `payouts-worker-queued-payout` shape with
`PAYOUTS_WORKER_MAXCONCURRENCY: "1"`.

| service | `PAYOUTS_WORKER_NAME` | `[job]` key | arena queue |
|---|---|---|---|
| `payouts-worker-bulk-payouts` | `bulk_payouts` | `bulk_payouts` | `bulk_payouts` |
| `payouts-worker-batch-submitted-merchants` | `batch_submitted_merchants` | `batch_submitted_merchants` | `batch_submitted_merchants` |
| `payouts-worker-data-consistency-checker` | `data_consistency_checker` | `data_consistency_checker` | `data-consistency-checker` |
| `payouts-worker-data-consistency-event` | `data_consistency_event` | `data_consistency_event` | `data-consistency-event` |
| `payouts-worker-fund-management-payout-check` | `fund_management_payout_check` | `fund_management_payout_check` | `fmp-check` |
| `payouts-worker-fund-management-payout-initiate` | `fund_management_payout_initiate` | `fund_management_payout_initiate` | `fmp-initiate` |
| `payouts-worker-payout-usage-event-processing` | `payout_usage_event_processing` | `payout_usage_event_processing` | `payout_usage_event_processing` |
| `payouts-worker-x-account-statement-source-event` | `x_account_statement_source_event` | `x_account_statement_source_event` | `x_account_statement_source_event` |
| `payouts-worker-x-balance-payouts-event` | **`x_balance_payouts_event`** | `x_balances_payouts_event` | `stage-x-balances-payouts-event` |
| `payouts-worker-api-queue-for-async-dual-write-direct-push` | `api_queue_for_async_dual_write_direct_push` | `api_queue_for_async_dual_write_direct_push` | `api-payout-service-dual-write-direct-push-live` |

**Name asymmetry, verified in source and worth flagging:** the registered job
name is `appConstants.XBalancePayoutsEvent = "x_balance_payouts_event"`
(*singular* "balance", `internal/app/common/appConstants/worker.go:15`), while
its config key under `[job]` is `x_balances_payouts_event` (*plural*). The task
brief listed the plural form. Using it as `PAYOUTS_WORKER_NAME` would make the
container exit with `ErrorStartingUnregisteredJob`. The compose block uses the
singular, registered form.

**Queues: no additions were needed.** All 25 `[job]` queue names in
`config/templates/base/payouts/arena.toml:404-432` were cross-checked against
`seeds/localstack/init-queues.sh` programmatically — zero missing. (The reverse
check lists 11 queues in the bootstrap that no payouts job consumes; they belong
to ledger/cfa/monolith.)

**Kafka: no topic bootstrap needed.** The only Kafka consumers are the two FTS
status-update ones, already present. The compose `kafka` service sets
`KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"`.

**`up.sh` core health list: no change needed.** It derives `CORE_SERVICES`
dynamically from `compose config --services | grep -E '^(payouts|ledger|fts|cfa|
xbalances)-'`, so all ten are picked up automatically.

### 3.1 The FTS Kafka producer leg — documented, NOT flipped

It turned out **not** to need a new switch: it is already one.
`ARENA_ROUTE_PROFILE` (`config/generate.py:90`, `config/routes.py PROFILES`)
drives both halves coherently from `PROFILES[p]["kafka"]` — the fts
`[kafka_producers.fire_transfer_status].enabled` **and** the per-merchant Splitz
variants of `arena_fts_kafka`. The default profile `monolith` leaves it off,
which is the arena's current behaviour and is left untouched.
`ARENA_ROUTE_PROFILE=kafka` turns the whole leg on.

Adding a second `ARENA_FTS_KAFKA_PRODUCER` switch was tried and then
**reverted**: it could only ever disagree with the route profile, which rewrites
`enabled` after token substitution.

One inert improvement was kept: the `[kafka_producers.fire_transfer_status.conf]`
block now lives in the template with prod-shaped
`retry_backoff/max_retry/max_messages/compression` values, instead of being
appended by `routes.py` with only `topic`/`brokers`/`enable_tls` (which left the
rest at Go zero values whenever the `kafka` profile was selected). It is inert
while `enabled = false` — FTS only builds a producer for an enabled entry
(`internal/providers/kafka/provider.go:64`) — so the default profile is
unchanged; it only makes `ARENA_ROUTE_PROFILE=kafka` behave like prod. Verified:
both profiles render valid, non-duplicated TOML.

Behaviour note for whoever does flip it: with the leg on,
`fts/internal/transfer/service.go:1136-1144` publishes to Kafka and **returns
early** — Kafka *replaces* the HTTP webhook to payouts for that merchant. The
payouts consumer side is already fully wired (`kafka:9092`, topic
`rx-fts-status-update-events`).

---

## 4. C — batch-sim (lane I2) integration

Landed last, as instructed, once `substitutes/batch-sim/compose-block.yml`
existed. Integrated verbatim from that file:

* `batch-sim` service under `services:` (profile `substitutes`, its own
  `batch-sim/Dockerfile`, `secrets-kong:/run/secrets:ro` + `batch-sim-data:/data`).
* `batch-sim-data` named volume (`rzp-arena-batch-sim-data${ARENA_SUFFIX}`).
* `batch_sim: {host: batch-sim, port: 8094}` in `config/arena.yaml`, per
  `arena-yaml-snippet.txt`.
* Added to `up.sh`'s substitutes health list and to `NO_PROXY`.
* Image built: `rzp-arena/batch-sim:v1-candidate`.

One dependency it declared had to be satisfied: it reads
`/run/secrets/auth_workflow_payouts` from `secrets-kong`, which
`materialize.py` never put there (§6.1). Now it does.

Not adopted: its Dockerfile header's "preferred alternative" of adding the
`mkdir /data` line to the shared `substitutes/Dockerfile`. That is a shared file
outside this lane's ownership, and both stateful stubs work as they stand.

---

## 5. D — validation performed (nothing booted)

| check | result |
|---|---|
| `docker compose --env-file .env.arena -f docker-compose.yml config` | **passes** |
| `python3 config/generate.py` (default profile) | **passes**, 6 service configs |
| `python3 config/generate.py` with `ARENA_ROUTE_PROFILE=kafka` | **passes**, valid TOML, no duplicate table |
| `python3 preflight/preflight.py` | **PREFLIGHT PASSED** |
| `secrets/materialize.py` source-group + compose-volume consistency | **9/9 groups consistent** (dry-run; not executed against the live arena) |
| all 25 registered job names have a worker | **exact match**, verified by diff |
| all 25 `[job]` queues exist in the SQS bootstrap | **0 missing**, verified programmatically |
| `docker compose build workflow-engine` | **built** `rzp-arena/workflow-engine:v1-candidate` |
| `docker compose build batch-sim` | **built** `rzp-arena/batch-sim:v1-candidate` |
| `make m5-scenarios` | **12/12 passed** |

### Standalone engine proof (`docker run --rm`, throwaway network, then removed)

Run as uid 10001, read-only root, secrets volume shaped exactly like
`materialize.py`'s output (0400 uid-10001 files in a 0500 dir):

* **fails closed** with no admin token (`FATAL: ... Refusing to start`);
* `/health` → `{"status":"ok","service":"workflow-engine","pending":N}`;
* `/data` writable as uid 10001 under `--read-only` (`wfe.db`, `-wal`, `-shm`);
* `Create` with `Basic workflow:workflow` → **14-char** id (`wfl5751abcb2cf`),
  `config_id` 14 chars, `domain_status: pending`; org == merchant id;
* `Create` without auth → **401**; `/admin/*` without the token → **403**;
  with it → 200;
* admin-provisioned `approver` actor → `POST /v1/workflows/{id}/approve` →
  `state: approved`, and a fake payouts-api received exactly:
  `POST /v1/payouts/payouts_internal/pout_.../approve`, `authorized: true`
  (Basic `rzp_live:<secret>`), `x-creator-id`, `X-Razorpay-Account`,
  `Idempotency-Key: cbk_<wfid>_approve`, body `{"queue_if_low_balance": true}`;
* `POST /_arena/decide {reject}` → callback fired to `.../reject` with body `{}`,
  response `{"ok":true,"status_code":200,"attempts":1,...}`;
* `docker restart` → all state and callback outcomes recovered from the volume;
* `/_arena/pending` and `/_arena/workflows` return workflow-sim-shaped records.

All smoke containers, volumes and the throwaway network were removed; `docker ps
-a` shows no leftovers.

---

## 6. The three latent blockers found (each would have broken the approval family)

### 6.1 `auth_workflow_payouts` was never materialized
`workflow-sim` mounts `secrets-kong:/run/secrets:ro` and reads
`/run/secrets/auth_workflow_payouts`, but `materialize.py`'s `secrets-kong` group
only ever contained `merchants/*`, `passport_private_key` and
`auth_api_payouts` — confirmed by inspecting the live volume. Every
approve/reject callback into payouts would have gone out with an **empty
password and been 401'd**. Invisible until now only because
`ARENA_WORKFLOW_HOST` defaulted to the frozen dead address, so `workflow-sim`
was never actually wired to payouts. Fixed: the file is now materialized into
both `secrets-workflow` (workflow-engine, workflow-sim) and `secrets-kong`
(batch-sim); `workflow-sim`'s mount was switched to `secrets-workflow`.

### 6.2 The workflow hosts were missing from `NO_PROXY`
`x-proxy-env` sets `HTTP_PROXY=http://127.0.0.1:9` (a closed port) as a
belt-and-suspenders egress block, with `NO_PROXY` listing every arena hostname.
Neither `workflow-sim` nor `workflow-engine` was in that list. Go's `net/http`
honours `HTTP_PROXY` via `ProxyFromEnvironment`, so the instant payouts pointed
at either of them, **every `WorkflowAPI/Create` would have been routed to the
blackhole proxy and failed**. Both are now listed, as are `batch-sim` and the ten
new workers.

### 6.3 `up.sh` never loaded `.env.arena` into its own environment
`docker compose --env-file` feeds compose's `${VAR}` interpolation only — never
the shell. But `config/generate.py` reads `ARENA_WORKFLOW_HOST`,
`ARENA_MOZART_IMPL` and `ARENA_XAS_SOURCE_EVENT_WHITELIST` from the **process
environment**, and its comments already (incorrectly) claimed up.sh sourced the
file. Setting `ARENA_WORKFLOW_HOST` in `.env.arena` would have had **no effect on
rendered config**. `up.sh` now loads the file with compose's own precedence — a
value already exported in the caller's shell still wins — and echoes the two
switches it resolved.

---

## 7. Boot sequence for the coordinator

The documented command **still applies, unchanged**:

```bash
bash ENV2_COMPOSE/scripts/down.sh
REGEN_SECRETS=1 ARENA_SKIP_BUILD=1 ARENA_SOURCE_REPOS_ROOT=<repos-root> \
  bash ENV2_COMPOSE/scripts/up.sh
```

No new environment variable is required. Notes:

* **`ARENA_SKIP_BUILD=1` skips `compose build`.** The two new/changed substitute
  images were already built by this lane and are present as
  `rzp-arena/workflow-engine:v1-candidate` and `rzp-arena/batch-sim:v1-candidate`
  (the tag comes from `ARENA_TAG` in `.env.arena`). If you tear down and change
  `ARENA_TAG`, or want the images rebuilt, drop `ARENA_SKIP_BUILD=1` (or run
  `docker compose --env-file .env.arena -f docker-compose.yml build
  workflow-engine batch-sim` first).
* `REGEN_SECRETS=1` regenerates `secrets/*.txt` including the new
  `wfe_admin_token`. Without it, up.sh keeps existing secrets — which also works,
  because `secrets/wfe_admin_token.txt` was minted on the host by this lane.
* `down.sh`'s `down -v` removes the three new volumes (`secrets-workflow`,
  `wfengine-data`, `batch-sim-data`) along with every other one, and
  `secrets/destroy.sh` removes the new secret file.
* `ARENA_WORKFLOW_HOST` now defaults to `http://workflow-engine:8093` in
  `.env.arena`. To boot with workflows disabled exactly as before, set it to
  `http://127.0.0.1:1`; to use the thin stand-in, `http://workflow-sim:8092`.

Expected outcome: **80 compose services — 79 running containers plus
`ledger-scheduler`, a one-shot that runs and exits 0** (was 67 running + 1
exited).

---

## 8. Post-boot checklist

```bash
cd ENV2_COMPOSE
C() { docker compose --env-file .env.arena -f docker-compose.yml "$@"; }
```

**1. Container count**
```bash
docker ps --filter label=com.docker.compose.project=env2_compose --format '{{.Names}}' | wc -l   # expect 79
C ps --status exited --format '{{.Name}}'                                                        # expect only ledger-scheduler
```

**2. Every new worker is healthy** (each block's healthcheck is
`GET 127.0.0.1:9400/status`, same as the existing 15)
```bash
for w in bulk-payouts batch-submitted-merchants data-consistency-checker \
         data-consistency-event fund-management-payout-check \
         fund-management-payout-initiate payout-usage-event-processing \
         x-account-statement-source-event x-balance-payouts-event \
         api-queue-for-async-dual-write-direct-push; do
  printf '%-46s %s\n' "$w" \
    "$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}' \
       "$(C ps -q payouts-worker-$w)")"
done
```
All ten must read `running/healthy`. A worker that exits immediately with
`ErrorStartingUnregisteredJob` means a `PAYOUTS_WORKER_NAME` typo — check
`C logs payouts-worker-<name>`.

**3. Workflow engine health**
```bash
C exec -T workflow-engine wget -qO- http://127.0.0.1:8093/health
# {"status": "ok", "service": "workflow-engine", "pending": 0}
C logs workflow-engine | tail -3   # "listening on 0.0.0.0:8093 ... tenant_key=owner_id ..."
```
There must be **no** `WARNING: no callback password` line (that would mean §6.1
regressed).

**4. Payouts is actually pointed at the engine**
```bash
C exec -T payouts-api sh -c 'grep -A1 "^\[workflow\]" /app/config/arena.toml | tail -1'
# host = "http://workflow-engine:8093"
```

**5. SQS queues present** (all 25 job queues)
```bash
C exec -T localstack awslocal sqs list-queues --region ap-south-1 | grep -c QueueUrl   # >= 36
for q in bulk_payouts batch_submitted_merchants data-consistency-checker \
         data-consistency-event fmp-check fmp-initiate payout_usage_event_processing \
         x_account_statement_source_event stage-x-balances-payouts-event \
         api-payout-service-dual-write-direct-push-live; do
  C exec -T localstack awslocal sqs get-queue-url --queue-name "$q" --region ap-south-1 >/dev/null \
    && echo "ok   $q" || echo "MISSING $q"
done
```

**6. batch-sim health**
```bash
C exec -T batch-sim wget -qO- http://127.0.0.1:8094/_arena/health
# {"status":"ok","service":"batch-sim",...,"passport":"available",...}
```
`passport` must be **`available`** — `unavailable` means the `secrets-kong`
mount did not pick up `passport_private_key`.

**7. End-to-end: a workflow-applicable create reaches `pending`**

Full procedure in `substitutes/workflow-engine/ARENA.md` §2. Short form, using
the M3 fixture (which ships `enable_payout_workflow: true` but also
`skip_approval_workflow_for_api: true`, so the API path is skipped until you
flip it):

```bash
# a. make ARENAM00000003 workflow-applicable via the public API
python3 - <<'PY'
import json, pathlib
p = pathlib.Path("seeds/dcs/merchants.json"); d = json.loads(p.read_text())
w = d["merchants"]["ARENAM00000003"]["dcs"]["rzp/x/merchant/payouts/Workflows"]
w["skip_approval_workflow_for_api"] = False
d["merchants"]["ARENAM00000003"].setdefault("legacy", {})["skip_workflow_for_api"] = False
p.write_text(json.dumps(d, indent=1) + "\n")
PY
C restart dcs-stub && sleep 5

# b. create a payout for ARENAM00000003 through kong-lite (usual RED_LOOP path)
#    -> the payout should come back status "pending"

# c. it must be parked in the engine
C exec -T workflow-engine wget -qO- http://127.0.0.1:8093/_arena/pending
#    count >= 1; owner_id == ARENAM00000003; workflow_id is 14 chars

# d. approve it (operator plane) and confirm the callback landed
C exec -T workflow-engine wget -qO- --header='Content-Type: application/json' \
  --post-data='{"payout_id":"pout_XXXXXXXXXXXXXX","decision":"approve"}' \
  http://127.0.0.1:8093/_arena/decide
#    callback.ok == true, callback.status_code in (200, 201, 409)

# e. the payout must have left `pending` on the PS side
```

If (c) returns `count: 0` while the payout is `pending`, payouts reached a
*different* workflow host — re-check step 4 and §6.2/§6.3.
If (d) reports `status_code: 401`, the callback credential is wrong — §6.1.

**8. Nothing regressed**
```bash
make m5-scenarios        # 12/12
python3 preflight/preflight.py
```

## 9. Coordinator follow-ups after the first M6 clean boot (2026-09-08)

| change | file | why (evidence) |
|---|---|---|
| monolith-stub applies runtime feature overrides to the merchant record PS parses | `substitutes/monolith-stub/server.py` `_get_merchant` | on-hold journeys: `IsFeatureEnabled(payouts_on_hold)` could never observe `POST /_arena/merchant_features` (journey lane I4 blocker #3) |
| FTS machinery retry queue pointed at the arena redis | `config/generate.py` `build_env_overrides("fts")` → `REDIS_QUEUE_HOST/PORT` | `fts-worker-retry-transfer*` ran with a dummy host, so retryable bank errors never produced a second attempt (I4 blocker #4); journey `idempotency-retries/retry` now PASS |
| host loopback bridge forwards the kong-lite arena-control headers | `scripts/ingress.py` `FORWARDED_HEADERS` | the bridge allow-listed 4 headers, silently dropping `X-Arena-Passport-Consumer-Type` / `X-Arena-User-Id` / `X-Dashboard-User-Role` |
| kong-lite opt-in dashboard/proxy passport (consumer type `user` + `user_merchant` impersonation) | `substitutes/kong-lite/server.py`, `CONTRACT.md` (ARENA CONTROL section) | proxy-auth-only PS routes (cancel of a scheduled payout) were unreachable; claim shape from goutils passport helpers.go + upstream-jwt access.lua |
| fresh-merchant ledger account ids: 9,000,000 namespaces, fail-closed ownership check | `RED_LOOP/red_loop/provisioner.py`, `provisioner_direct.py`, `RED_LOOP/tests/test_ledger_ids.py` | the old 2-digit prefix (100 namespaces) silently produced merchants with zero ledger accounts (I4 defect #1) |
| EF-009 free-payout counter revert lost when it would empty the counter | `TWIN_SPEC/expected-failures.yaml` | differential proof in `journey:pricing-free-payouts/cancel_or_reverse` |
| `payout.cancelled` non-event documented as source-faithful | `TWIN_SPEC/README.md` | `state_machine.go:83-86`, `webhooks.go:3-24` |

Source-faithful OFF, deliberately not flipped: `[features] source_updater_sns_enabled = false`
(payouts `config/default.toml:718`, no `prod.toml` override) — the SNS source-update leg is
disabled in the pinned production config; the twin asserts the legacy HTTP transport instead.

## 10. FTS worker topology (lane I7)

Production runs **158** `fts-live-worker-*` Deployments (`kube-manifests/templates/fts/templates/`,
all `replicas >= 1` in `kube-manifests/prod/fts/values.yaml`). The twin ran **13**. The functional graph
flagged 15 of the missing ones as P0. This lane added **13 of those 15**; the remaining 2 are deferred
with a hard blocker. Full matrix and per-worker evidence: `ENV2_COMPOSE/FTS_WORKER_TOPOLOGY.md`;
machine-readable gaps for `scripts/domain/worker_gaps.py`: `ENV2_COMPOSE/fts-worker-gaps.json` (132 entries).

### 10.1 Added (13 new `core`-profile services in `docker-compose.yml`)

Each is the same block as the 13 pre-existing `fts-worker-*` services — same image
`rzp-arena/fts:${ARENA_TAG:-local}`, same `entrypoint: ["/app/fts-worker", "-env=arena", "-base_path=/app",
"-command=<worker_name>"]`, same `config-fts` volume, `generated/fts/fts.env`, `*proxy-env`,
`*runtime-security`, and `depends_on: {mysql-fts: healthy, redis: healthy}`. **No config change was needed:**
every one of the 13 already has its `[queue.worker_queue_map]` key in
`config/templates/base/fts/env.default.toml` (byte-identical to the FTS clone's copy), so
`config/templates/base/fts/env.arena.toml` was NOT touched.

| compose service | `-command` (= k8s `args[1]`) | machinery queue | queue-map key | prod replicas |
| --- | --- | --- | --- | --- |
| `fts-worker-hv-fire-transfer-status-webhook` | `hv::fire_transfer_status_webhook` | `hv_fire_transfer_status_webhook` | `env.default.toml:201` | 1 |
| `fts-worker-hv-initiate-transfer` | `hv::initiate_transfer` | `hv_initiate_transfer` | `env.default.toml:265` | 1 |
| `fts-worker-rbl-direct-check-transfer-status` | `rbl::direct::check_transfer_status` | `rbl_direct_check_transfer_status` | `env.default.toml:151` | 1 |
| `fts-worker-rbl-direct-imps-hv-initiate-transfer` | `rbl::direct::imps::hv::initiate_transfer` | `rbl_direct_imps_hv_initiate_transfer` | `env.default.toml:141` | 2 |
| `fts-worker-rbl-direct-initiate-transfer` | `rbl::direct::initiate_transfer` | `rbl_direct_initiate_transfer` | `env.default.toml:150` | 2 |
| `fts-worker-rbl-direct-slow-lane-initiate-transfer` | `rbl::direct::slow_lane::initiate_transfer` | `rbl_direct_slow_lane_initiate_transfer` | `env.default.toml:289` | 2 |
| `fts-worker-rbl-direct-upi-check-transfer-status` | `rbl::direct::upi::check_transfer_status` | `rbl_direct_upi_check_transfer_status` | `env.default.toml:149` | 4 |
| `fts-worker-rbl-direct-upi-initiate-transfer` | `rbl::direct::upi::initiate_transfer` | `rbl_direct_upi_initiate_transfer` | `env.default.toml:148` | 5 |
| `fts-worker-rbl-upi-check-transfer-status` | `rbl::upi::check_transfer_status` | `rbl_upi_check_transfer_status` | `env.default.toml:146` | 4 |
| `fts-worker-rbl-upi-hv-check-transfer-status` | `rbl::upi::hv::check_transfer_status` | `rbl_upi_hv_check_transfer_status` | `env.default.toml:279` | 1 |
| `fts-worker-rbl-upi-hv-initiate-transfer` | `rbl::upi::hv::initiate_transfer` | `rbl_upi_hv_initiate_transfer` | `env.default.toml:278` | 1 |
| `fts-worker-rbl-upi-initiate-transfer` | `rbl::upi::initiate_transfer` | `rbl_upi_initiate_transfer` | `env.default.toml:145` | 6 |
| `fts-worker-slow-lane-initiate-transfer` | `slow_lane::initiate_transfer` | `slow_lane_initiate_transfer` | `env.default.toml:176` | 1 |

The 13 new service names were also appended to the shared `x-proxy-env` `NO_PROXY` allow-list
(`docker-compose.yml:83`), which enumerates every arena hostname — consistent with how the existing
`fts-worker-*` names are listed there.

### 10.2 Expected new container count

| | before | after |
| --- | --- | --- |
| `fts-worker-*` services | 13 | **26** |
| services in `datastores` + `substitutes` + `core` | 80 | **93** |
| services in `up.sh`'s derived core health list | 54 | **67** |

**+13 containers**, ~45 MiB RSS each ⇒ **≈ 585 MiB** extra, inside the ~6.8 GiB headroom.
`scripts/up.sh` needed no edit: `CORE_SERVICES` at `scripts/up.sh:170-173` is *derived*
(`compose … config --services | grep -E '^(payouts|ledger|fts|cfa|xbalances)-'`), not enumerated, so the
new services are health-gated by step 8/8 automatically.

### 10.3 Per-worker health command

Identical to the pre-existing fts workers (the process is a queue consumer with no HTTP listener):

```yaml
healthcheck:
  test: ["CMD", "pgrep", "-f", "fts-worker"]
  interval: 5s ; timeout: 3s ; retries: 20 ; start_period: 10s   # *healthcheck-defaults
```

Manual check for any one of them, e.g.:

```sh
docker compose --env-file .env.arena -f docker-compose.yml ps fts-worker-rbl-direct-upi-initiate-transfer
docker compose --env-file .env.arena -f docker-compose.yml exec fts-worker-rbl-direct-upi-initiate-transfer pgrep -f fts-worker
# the -command it is actually consuming (one machinery queue per process):
docker compose --env-file .env.arena -f docker-compose.yml exec fts-worker-rbl-direct-upi-initiate-transfer \
  pgrep -af fts-worker   # -> /app/fts-worker -env=arena -base_path=/app -command=rbl::direct::upi::initiate_transfer
```

### 10.4 Deferred: `worker:fts/rbl::v5::initiate_transfer`, `worker:fts/rbl::v5::check_transfer_status`

Both run in production (`kube-manifests/prod/fts/values.yaml:383-384`, replicas 1) but **no
`[queue.worker_queue_map]` entry for `rbl::v5::*` exists in any cloned repo** — not in
`fts/config/env.default.toml` and not in the twin's byte-identical copy
(`config/templates/base/fts/env.default.toml:106-289`, 155 keys, zero `rbl::v5` hits). Per that map's own
contract (`env.default.toml:113`) an unmapped `-command` falls back to `DEFAULT_QUEUE`, which the arena sets
to `"default"` (`env.arena.toml:61`) — so adding these two would put two extra consumers on the shared
`default` queue and steal tasks from `fts-worker-default`. That is a regression, not added fidelity.

Production gets the mapping from the `fts-app-config` ConfigMap that every fts Deployment mounts at
`/config/` but which is **defined nowhere in `kube-manifests`**
(`fts-live-worker-rbl-v5-initiate-transfer-deployment.yaml:100-102,118-119`). Inventing
`rbl_v5_initiate_transfer` by naming convention would not be a pure copy of a real value, so per the lane rule
`env.arena.toml` was left untouched and both workers were recorded in `fts-worker-gaps.json`.
`[integration.api.rbl.v5.*]` gateway config *is* present (`env.default.toml:1826-1990`) — the only missing
thing is the queue name.

### 10.5 Added ≠ exercised

All 13 boot and block on their own queue, which completes the topology. Seven of them
(`hv::*`, `slow_lane::initiate_transfer`, `rbl::direct::initiate_transfer`,
`rbl::direct::check_transfer_status`, `rbl::direct::imps::hv::initiate_transfer`,
`rbl::direct::slow_lane::initiate_transfer`) are reachable from the arena's own payout flow. The six
`rbl::upi::*` / `rbl::direct::upi::*` workers stay idle until a UPI routing row exists:
`seeds/generated/s4/fts.sql:57-69` seeds `channel_information_status` for **RBL / `mozart_identifier` v1 /
IMPS + NEFT only**. `mozart-sim` is not the blocker — `substitutes/mozart-sim/server.py:496-506` dispatches on
`{action}` and ignores the `{gateway}/{version}` path segments.

### 10.6 Validation (no arena boot, no restart — a journey run was live)

```
$ docker compose --env-file .env.arena -f docker-compose.yml config                      -> exit 0
$ docker compose … --profile datastores --profile substitutes --profile core config --services | wc -l   -> 93
$ …                                                                        | grep -c '^fts-worker-'      -> 26
$ python3 config/generate.py                                               -> OK, rendered 6 service configs
$ python3 preflight/preflight.py                                           -> PREFLIGHT PASSED
```

## 11. cfa-server gateway loopback dial (coordinator, after lane I6's D-8)

`config/templates/base/cfa/arena.toml` `[Server.ServerAddresses] Grpc = "0.0.0.0:8080"` (was `":8080"`)
and `0.0.0.0` added to the shared `NO_PROXY` anchor in `docker-compose.yml`. The grpc-gateway dials its
own gRPC backend at that address; a hostless target was routed through the egress-blackhole proxy, so every
`/v1/contacts` and `/v1/fund_accounts` gateway call answered 503 (`journey:beneficiary-fund-accounts/cfa_api`).
Listening semantics are unchanged (`0.0.0.0:8080` ≡ `:8080`).
