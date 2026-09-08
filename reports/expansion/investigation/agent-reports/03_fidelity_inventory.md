# RedGrid Payouts Twin — Consolidated Fidelity Inventory

**Scope:** every material component and business journey in the RedGrid Payouts twin
(`ENV2_COMPOSE` arena, compose project `env2_compose`).
**Repo:** `/Users/rana.singh/rzp-payouts-architecture`, branch `milestone-6-complete-payouts-domain`,
HEAD `2613f38` ("M5 evidence: autonomous discovery proven").
**Produced:** 2026-09-08, read-only. No file in the repo was modified; no container was
started, stopped, restarted or exec'd. Runtime facts come from `docker ps`,
`docker inspect`, `docker logs` (read), and loopback `curl` against the host ingress bridge.

Every line is marked **[V]** = VERIFIED in this session (I ran the command / opened the
cited file and read the cited lines) or **[I]** = INFERRED (derived from a document that
asserts it, or from indirect evidence; the basis is named).

---

## 0. How to read this document

### 0.1 Classification vocabulary

Each component is classified as **exactly one** of:

| class | meaning |
|---|---|
| `real source running` | the pinned upstream Go/PHP binary, built from a pinned SHA, is running as a container in `env2_compose` right now |
| `source mapped but not running` | the real source is cloned/mapped (and often buildable), but no container of it is up in the live arena — either never wired, or defined-but-not-started |
| `high-fidelity replacement` | a purpose-written substitute whose wire contract, state machine and failure taxonomy were reconstructed from the real source and are documented in a CONTRACT.md / FIDELITY.md |
| `behavioral stub` | a substitute that answers the shape of the call but does not reproduce the production decision logic (fixed/flat/seeded answers, no state machine) |
| `graph-only representation` | present only as a node in the architecture/functional graph or spec; nothing runs and nothing is substituted |
| `production configuration or reachability unknown` | the twin runs something, but whether the production system behaves this way (or is reachable at all on this path) could not be determined from any readable artifact |

This is deliberately the same vocabulary the in-flight M6 lane used in
`reports/domain/FIDELITY_CLASSIFICATION.csv` (`real_source_running`,
`real_source_mapped_not_running`, `high_fidelity_replacement`,
`behavioural_placeholder`, `graph_only`, `blocked_missing_access`), so the two are
cross-walkable. **[V]** — header at `reports/domain/FIDELITY_CLASSIFICATION.csv:1`;
1,995 classified rows; distribution: 657 `real_source_running`, 481 `graph_only`,
479 `real_source_mapped_not_running`, 297 `high_fidelity_replacement`,
79 `behavioural_placeholder`, 2 `blocked_missing_access`.

### 0.2 The single most important caveat in this document

**The live arena is NOT a boot of the current working tree.** Three states must be
kept apart:

| state | what it is | evidence |
|---|---|---|
| **LIVE** | 67 running + 1 exited container, booted `2026-09-06T13:40:40Z` at git_head `39fe41e5`, `route_profile: monolith`, `config_digest 839a4da8…` | **[V]** `ENV2_COMPOSE/.runtime/arena-fingerprint.json` (`boot_id 06330f7e-3099-4299-ab0c-9b9d50c43899`); `docker ps` |
| **COMMITTED** | HEAD `2613f38`; workflow host still defaults to the frozen dead address | **[V]** `git show HEAD:ENV2_COMPOSE/config/generate.py:234` → `tokens["WORKFLOW_HOST"] = os.environ.get("ARENA_WORKFLOW_HOST", "http://127.0.0.1:1")` |
| **WORKING TREE (uncommitted M6, another session)** | +10 payouts workers, `workflow-engine`, `batch-sim`, `ARENA_WORKFLOW_HOST=http://workflow-engine:8093` — **built and validated, never booted** | **[V]** `git status --porcelain`; `ENV2_COMPOSE/M6_RUNTIME_CHANGES.md:7-11` "**Nothing here was booted.**" |

Consequences that matter for every claim made against this twin:

* **[V]** The live arena drifted *after* its own fingerprint was taken. The fingerprint
  records a service `xas-sink` (image `rzp-arena/xas-sink:v1-candidate`,
  container `e0c2d422…`); `docker ps` shows `env2_compose-xas-sim-1` created
  `2026-09-07 01:27:26`, and `env2_compose-payouts-worker-rbl-banking-account-statement-1`
  created `2026-09-07 00:45:14`, i.e. the M4 statement/XAS lane was grafted onto a
  running arena by recreating individual services.
* **[V]** `payouts-api` (created `2026-09-06 19:10:23`) therefore carries a **pre-XAS
  NO_PROXY list**: `docker inspect env2_compose-payouts-api-1` → `NO_PROXY` contains
  `xas-sink`, `mozart-mock`, `mozart-sim` but **not** `xas-sim`, `workflow-sim`,
  `workflow-engine`, `batch-sim`, nor any of the 10 M6 workers. With
  `HTTP_PROXY=http://127.0.0.1:9` / `HTTPS_PROXY=http://127.0.0.1:9` (the deliberate
  blackhole, DEV-127) every payouts→host-not-in-NO_PROXY HTTP call is dead by
  construction.
* **[V]** Compose defines 87 services; 67 have a running container; 20 do not, and they
  fall into four different categories that must not be conflated:
  **(a)** 5 one-shot `*-migrate` jobs, run to completion and removed — *by design*;
  **(b)** `ledger-scheduler`, `Exited (0)` — a real binary that ran once at boot;
  **(c)** `mozart-mock` (profile `mozart-real`, build-blocked) and `verifier`
  (profile `verify`, a harness) — *never* meant to be long-lived;
  **(d)** the 12 uncommitted-M6 services — 10 payouts workers, `workflow-engine`,
  `batch-sim` — built and validated but **never booted**.
* **[V]** `reports/domain/FIDELITY_CLASSIFICATION.csv` classifies several M6 queues as
  `real_source_running` "(added by the M6 I3 worker pass)" — e.g.
  `queue:bulk_payouts`, `queue:batch_submitted_merchants`. **Those worker containers
  do not exist.** Treat every M6-lane `real_source_running` row as *intended after the
  next controlled reboot*, not as current.

### 0.3 Runtime shape (measured)

**[V]** `docker ps --format '{{.Names}}\t{{.Status}}\t{{.Image}}'`, 2026-09-08:

* 67 running containers, all `(healthy)` except `cron-driver` (no healthcheck) —
  1 exited: `env2_compose-ledger-scheduler-1`, `Exited (0) 22 hours ago`.
* 5 real service images, all tag `v1-candidate`: `rzp-arena/payouts`,
  `rzp-arena/fts`, `rzp-arena/ledger`, `rzp-arena/cfa`, `rzp-arena/xbalances`.
* Datastores: `mysql:8.0` ×4, `postgres:15-alpine`, `mongo:6.0`, `redis:7-alpine`,
  `localstack/localstack:3.8`, `apache/kafka:3.8.0`.
* **[V]** **No container publishes a port.** `docker inspect env2_compose-kong-lite-1`
  → `NetworkSettings.Ports = {"8080/tcp": null}` despite
  `HostConfig.PortBindings = {"8080/tcp":[{"HostIp":"127.0.0.1","HostPort":"18080"}]}`.
  The only host entry point is a **host-side loopback bridge**: `ENV2_COMPOSE/scripts/ingress.py`
  (PID 76231, `serve --port 18080`), which pipes each request over `docker exec` stdin to
  `127.0.0.1:8080` inside kong-lite (`ENV2_COMPOSE/scripts/ingress.py:1-8,28-41`).
* **[V]** Health probe through that bridge:
  `curl http://127.0.0.1:18080/_arena/health` → `200`
  `{"status": "ok", "service": "kong-lite", "routes": 8, "merchants_loaded": 161}`.
* **[V]** Networks: `rzp-arena` (`internal: true`, no egress) carries every container;
  `rzp-ingress` is the only network with a host path and kong-lite is its sole member
  (`ENV2_COMPOSE/docker-compose.yml:86,93`; `TWIN_SPEC/runtime-topology.yaml:351`).

### 0.4 Deviation registry

**[V]** `ENV2_COMPOSE/config/declared-deviations.yaml` — schema at lines 1-35,
`version: 1`, `generated: '2026-09-05'`, **89 entries** `DEV-001 … DEV-172`
(7 `fixed_in_m1`, 1 `fixed_in_m4`, 81 `declared`). Counts by `service` over the 88
schema-conforming entries: payouts 30, ledger 15, arena 12, fts 10, monolith 7,
cfa 5, x-balances 5, x-account-statements 3, ledger-gate 1.
(`RED_LOOP/registry/known_gaps.yaml:20` still says "76 entries" — stale by 13;
**[V]** counted programmatically.) Cited per component below as
`DEV-nnn @ declared-deviations.yaml:LINE`.

**⚠ [V] Registry defect — DEV-172 is malformed and silently truncated.** The file
header (`declared-deviations.yaml:1-33`) pins a strict YAML subset that
`ENV2_COMPOSE/config/_miniyaml.py` parses, and a fixed field contract
(`service, location, twin_value, deployed_value, deployed_source, classification,
reason, could_conceal, status, fix_ref`). `DEV-172` (`:1129-1143`, the dcs-stub
fieldmask-redaction fix) instead uses unquoted scalars, a **different** field set
(`title, kind, detail`), and a `>-` block scalar the subset forbids. Running the
project's own parser:

```
python3 -c "import _miniyaml; d=_miniyaml.load(open('declared-deviations.yaml').read())"
→ parsed OK, 89 entries
→ DEV-172 record: {'id':'DEV-172', 'title':'…', 'kind':'representative_substitute', 'detail': '>-'}
```

The entire `detail` body is **lost — reduced to the literal string `'>-'`**, and the
entry carries no `service`, `location`, `classification`, `could_conceal` or
`fix_ref`. Since "the acceptance-gate producer embeds this file verbatim"
(`:5-6`) and the RED_LOOP judge "loads that file to match by service+location"
(`known_gaps.yaml` `KG-DEV-REGISTRY`), DEV-172 can never be matched by the judge
and its content never reaches the gate. Everything the entry documents — the
substitute bug that crashed `payouts-api` with `cannot convert int64 to bool` on
`/v1/payouts/_meta/summary` (M4 limit L-018) — is effectively undeclared.

**Gap [V]:** the registry has **no entry for `workflow-engine`, `batch-sim`, or the 10
M6 workers**. `git status` shows `config/declared-deviations.yaml` is *not* among the
uncommitted M6 edits, while `docker-compose.yml`, `.env.arena`, `config/arena.yaml`,
`config/generate.py` and `substitutes/workflow-engine/**` are. If M6 boots as-is, the
declared-deviation registry under-declares the approval and bulk lanes.

**[V]** Judge-side gap registry (what a finding may *not* be attributed to):
`RED_LOOP/registry/known_gaps.yaml` — `KG-KAFKA-EF001/2/3` (calibration rediscoveries),
`KG-SUB-MINT` (kong-lite `POST /_arena/mint` is an unauthenticated passport oracle;
broker hard-denies `/_arena/*`), `KG-SUB-MOZART-SCENARIO`, `KG-SUB-WORKFLOW-DECIDE`,
`KG-SUB-STORK-DUP`, `KG-FID-BANK`, `KG-FID-MONOLITH`, `KG-DEV-REGISTRY`.

---

## 1. Build provenance (pinned source SHAs)

**[V]** `reports/implementation/BUILD_PROVENANCE.md:9-15` — the five real services, admitted-file counts, all builds exit 0:

| service | source commit SHA | admitted files | image tag | image ID recorded at `BUILD_PROVENANCE.md:52-56` | image ID **running now** |
|---|---|---|---|---|---|
| payouts | `4bf3dbf9239feadea6d65ca90c893a988e116173` | 1425 | `rzp-arena/payouts:v1-candidate` | `sha256:e029d639…` | **`sha256:915299ab…`** ⚠ |
| ledger | `471ff4d5321b6b99a7965882d18f572a1adf5194` | 738 | `rzp-arena/ledger:v1-candidate` | `sha256:878be96a…` | `sha256:878be96a…` ✓ |
| fts | `2a09e763116db47a2f28553c677ad73ef8133ebf` | 1101 | `rzp-arena/fts:v1-candidate` | `sha256:dac4c76a…` | `sha256:dac4c76a…` ✓ |
| cfa | `d488558e162c08e9fa04dff005f27d846ce88ad2` | 475 | `rzp-arena/cfa:v1-candidate` | `sha256:1b6cf763…` | `sha256:1b6cf763…` ✓ |
| x-balances | `1a21c0f9111da14125d839dc0dfe8d980740f207` | 539 | `rzp-arena/xbalances:v1-candidate` | `sha256:980f8333…` | `sha256:980f8333…` ✓ |

* **[V]** proto repo for generated RPC: `52682577d79d7237944e9fbb10477a1721623065` (6 payouts + 10 ledger schema files + import closure) — `BUILD_PROVENANCE.md:35`.
* **[V]** Toolchain: Go 1.26.6, `GOOS=linux GOARCH=arm64 CGO_ENABLED=0 GOFLAGS=-mod=readonly GOPROXY=off`, all 5 `go mod verify` passed — `BUILD_PROVENANCE.md:19`. Cold-cache dependency acquisition is explicitly **not** proven (same line).
* ⚠ **[V]** The payouts image the arena is running (`915299ab`, built `2026-09-05 21:32:45`) is **not** the ID recorded in `BUILD_PROVENANCE.md:52` (`e029d639`) — the tag was rebuilt again after that document was written (the DEV-004 `stage_assets_payouts` fix, `BUILD_PROVENANCE.md:176-183`). Provenance for payouts is one rebuild stale. `docker inspect env2_compose-payouts-api-1 --format '{{.Image}}'` and `docker images` agree with each other, so the running binary *is* the current tag; only the document lags.

### 1.1 ⚠ Substitute-image drift (previously unrecorded)

**[V]** For **all 14** running substitute containers, the image the container runs is a *dangling* image ID that no longer matches the `:v1-candidate` tag of the same name. Measured by comparing `docker inspect <container> --format '{{.Image}}'` against `docker images --no-trunc`:

| substitute | running image | current `:v1-candidate` tag |
|---|---|---|
| monolith-stub | `2e6b755c4f78` | `f557bd8052f7` |
| kong-lite | `0ff91a193035` | `16176c424d35` |
| dcs-stub | `f219fc333a2c` | `9be735d15256` |
| splitz-stub | `c010d0de300c` | `4fe02f7e933f` |
| mozart-sim | `c35f21f8a231` | `cb9ef32ebddd` |
| xas-sim | `2955088dea9a` | `7a3a1307cf2e` |
| stork-capture | `1d931d3ce2bc` | `4f64515ff7ec` |
| shield-stub | `9599d70120c4` | `6be4ab588d75` |
| pricing-stub | `423803e8b1e8` | `8c3a9ab8c7f1` |
| asv-stub | `46396a7e128b` | `aa2363dae4c5` |
| bankingaccounts-stub | `1f4999713a8f` | `d6db8d41da61` |
| workflow-sim | `35d33c683504` | `81780de2a0dc` |
| merchant-webhook-sink | `9f06e1a16cab` | `75aa185ba591` |
| ledger-gate | `86df4e9f9820` | `2803dd3e4864` |

Consequence: **no substitute in the live arena is byte-identical to what the working tree builds today.** Any statement of the form "the twin behaves this way because `substitutes/<x>/server.py:NN` says so" is an **[I]** inference about the live arena unless the running container is re-created. Corrected substitutes documented in the tree (e.g. the dcs-stub fieldmask redaction fix dated 2026-09-07, `substitutes/dcs-stub/CONTRACT.md:133-175`) may or may not be in the running container.

---

## 2. Component inventory

### 2.1 Payouts (the system under test)

| field | value |
|---|---|
| **Class** | **real source running** |
| SHA / path | `razorpay/payouts` @ `4bf3dbf9239feadea6d65ca90c893a988e116173`; admitted copy `.local/twin-repos/accepted/payouts`; image `rzp-arena/payouts:v1-candidate` = `915299ab` **[V]** |
| Runtime | `payouts-api` (`docker-compose.yml:470`, entrypoint `/app/payouts-api`, `/status` on 9400), **15 of 25** `payouts-worker-*` running, 2 `payouts-kafka-fts-status-updates-*consumer` running **[V]** |
| Config | `config-payouts:/app/config:ro` (`docker-compose.yml:477`), `APP_ENV=arena`, `ARENA_DCS_URL=http://dcs-stub:8080`, `ARENA_STORK_JSON=1` **[V]** |

**Worker roster [V]** (`PAYOUTS_WORKER_NAME` grepped from `docker-compose.yml`; 25 defined, matching the 25 registered jobs per `M6_RUNTIME_CHANGES.md:290`):

*Running (15):* `webhook_event`, `queued_payout`, `schedule_payout`, `on_hold_payout`, `fts_async_processing`, `fts_async_hv_processing`, `payout_create_failure_handling`, `payout_update_failure_handling`, `transaction_create`, `generic_processing`, `async_dual_write`, `x_balances_balance_refresh`, `rbl_banking_account_statement`, `payout_source_updater`, `partner_bank_hold_payouts`.

*Defined but **not running** (10, uncommitted M6, `docker-compose.yml:936-1294`):* `bulk_payouts`, `batch_submitted_merchants`, `data_consistency_checker`, `data_consistency_event`, `fund_management_payout_check`, `fund_management_payout_initiate`, `payout_usage_event_processing`, `x_account_statement_source_event`, `x_balance_payouts_event`, `api_queue_for_async_dual_write_direct_push`. Classify these **source mapped but not running**.

**Kafka consumers [V]:** both carry `restart: on-failure`, added as the DEV-007 fix citing prod `restartPolicy: Always` (`docker-compose.yml:1310-1319`, `declared-deviations.yaml:114`). Healthcheck is `pgrep -f payouts-kafka-consumer` — a liveness proxy, **not** a consumer-group-membership check.

**Journeys using it:** every one. See §3.

**Auth/trust represented [V]:**
* Inbound merchant: RS256 `X-Passport-JWT-V1` minted by kong-lite (`substitutes/kong-lite/server.py:152-176`), claims `iss/sub/jti/iat/nbf/exp(+300s)/identified/authenticated/mode/org/product/consumer{id,type:"merchant"}/roles`, `kid` = `arena-passport-1`.
* Inbound service: HTTP Basic per route group — `cred.API/Workflow/Xperience/FTS/VendorPayments/Settlements/Irctc/FastCron` (control C4, `reports/CONTROL_AND_INVARIANT_CATALOG.md:12`). Secrets `ENV2_COMPOSE/secrets/auth_*_payouts.txt`.
* Outbound service: Basic to ledger/fts/cfa/x-balances/monolith/stork/shield/splitz/dcs/pricing; passport never re-minted downstream.
* `Authorization` from the merchant is **dropped** at kong-lite, never forwarded (`kong-lite/server.py:272`).

**Synthetic data required [V]:** `seeds/generated/merchants.json` — **162 merchants**, each `{key_id_live: rzp_live_ARENA…, key_id_test: rzp_test_ARENA…, roles: [], secret_file: merchant_…}`; kong-lite reports `merchants_loaded: 161`. Plus `seeds/mysql/apidb-ddl/00_init.sql`, `seeds/schema-patches/payouts.sql`, `seeds/generated/monolith/*`, `seeds/generated/pricing.json`, `seeds/generated/dcs/merchants.json`, `seeds/generated/splitz/experiments.json`, `seeds/generated/shield/rules.json`, `seeds/generated/stork/subscriptions.json`, `seeds/localstack/init-queues.sh` (36 SQS queues + 2 SNS topics: `payout-updates-test-dev`, `journal-created`). Real migrations mounted read-only into the one-shot `payouts-migrate` job.

**Deviations [V]:** 30 `DEV-*` entries with `service: payouts`. Load-bearing ones —
`DEV-003` `[features].ikey_auto_enforcement false→true` (fixed, `:66`);
`DEV-004` `/app/files` error-mapping assets were missing so every bank-error → `failure_reason` mapping degraded to the fallback (fixed, `:78`);
`DEV-005` kafka producer brokers were `broker:9092` placeholders → nil producer panic → consumer exit (fixed, `:90`);
`DEV-010/011` every host/credential substituted (`:129`, `:141`);
`DEV-014` `[events].enabled`, `DEV-015` `[ccsdk].mock`, `DEV-016` `[wda].mock`+`[elasticsearch].Mock`, `DEV-017` `[dcs].Mock`, `DEV-019` dual-write flags, `DEV-025` 43 Splitz ids + `client_side_eval`, `DEV-029` worker/consumer max-concurrency, `DEV-030` `APP_MODE`, `DEV-126` entrypoint, `DEV-171` `[sns]`/`[configs.account_statement_source_event]`.
Also `DEV-121` (`payouts/pkg/stork/client.go:107-110` patched to JSON Twirp) and `DEV-122` (`go.mod` replace → local goutils/dcs copy) are **source patches to the real binary**, so the running payouts is *the pinned SHA plus three named arena patches*, not a pristine build.

**CAN support:** idempotency (C5/I01; V1, V2, G52 8-way concurrent), tenant isolation on merchant routes (C6; V20, G46 both directions, G47 body-override, G49 internal-route unreachability), payout state-machine legality (I10; V14), balance authorization on Shared (C15/I20; V9, V10, V11), reservation gate on Direct (C16; V12, V13, G53), reversal ordering and Ledger-outage recovery (C24; V17a/b), failed-status safety guard (C25; V19, M4 journey E), webhook completeness and duplicate suppression (C36; V21, G34/G45), forged-header/forged-passport rejection at both edge and SDK (G48), fee fail-closed (C27; V23, M6 pricing/failure).

**CANNOT support:** dashboard/OTP/session identity (C1 — no dashboard, no `consumer.type=user`, no impersonation claims: kong-lite hard-codes `{"type":"merchant"}`, `kong-lite/server.py:156-172`, which is exactly why `journey:scheduled-payouts/cancel_via_dashboard` is BLOCKED); role/permission gating (C3 — merchants seed carries `roles: []`); real Kong plugin behaviour, rate limiting (C43 — correctly absent) and the `authz-enforcer` shadow-mode nuance (C44); prod RBAC; real bank protocol; the `[workflow]` approval path (dead address in the live arena, §2.11); anything depending on the 10 unstarted workers.

---

### 2.2 FTS (fund transfer service)

| field | value |
|---|---|
| **Class** | **real source running** |
| SHA | `2a09e763116db47a2f28553c677ad73ef8133ebf`; image `dac4c76a` (matches provenance) **[V]** |
| Runtime | `fts-web` (`docker-compose.yml:1526`) + **13** `fts-worker-*`, each `-command=<worker_queue_map key>`: `default`, `initiate_transfer`, `check_transfer_status`, `fire_transfer_status_webhook`, `retry_transfer`, `retry_transfer_preprocessor`, `retry_transfer_source_update`, `rbl::initiate_transfer`, `rbl::check_transfer_status`, `rbl::imps::initiate_transfer`, `rbl::imps::check_transfer_status`, `rbl::direct::imps::initiate_transfer`, `rbl::direct::imps::check_transfer_status` (`docker-compose.yml:1560-1836`) **[V]** |

**Topological ceiling [V]:** `TWIN_SPEC/runtime-topology.yaml:120-121` — "~145 of ~157 bank/rail-specific worker roles have no container (icici, idfc, yesbank, slice, amazon-pay, ocbc, hdfc, axis, batch-icici-neft all entirely unmodeled)"; and, importantly, the *config* is correct — `env.arena.toml` does not override `[queue.worker_queue_map]`, so all ~90 real mappings are inherited; "The gap is purely topological."

**Auth [V]:** per-caller Basic users (`users.*`) — `identity:fts-basic-auth:{api,art,alert,…}` classified `real_source_running` in `reports/domain/FIDELITY_CLASSIFICATION.csv` ("same middleware runs in fts-web; credentials supplied by arena env"). The `returned` and `reversed_dropped_direct` scenarios use the **admin** identity `api_monolith` against `/v1/attempts/verify` + `/v1/attempts/safe_update`.

**Seeds [V]:** `seeds/mysql/fts_seed.sql` + `seeds/s4/fts.sql`. `components.yaml:56` records the seed limits: RBL only, no DOWN rows, one source account per merchant (prod has v1+v2), and open item C-005 (`source_accounts.account_type` uppercase `'DIRECT'` vs code lowercase `'direct'`).

**Deviations [V]:** 10 `DEV-*`. `DEV-041` `[kafka_producers.fire_transfer_status].enabled` is **false** under the default `monolith` route profile (`:408`) — the Kafka status leg is off in the live arena; `DEV-042` 9 webhook URLs substituted; `DEV-044` `[ledger].BASE_URL` points at `ledger-gate` not `ledger-api` (representative substitute); `DEV-045` `worker_concurrency_map` absent; `DEV-046` **202 prod-only keys undeclared**; `DEV-047` entrypoint; `DEV-048` 31 splitz ids; `DEV-006` `update_fts_fund_transfer` TIMEOUT `10→300` (fixed — a 10 s timeout inverted a production behaviour by turning slow-but-successful into retried).

**CAN support:** transfer idempotency on `(source_type, source_id)` (I02; V3), FTS FSM legality (I13), status-code classification incl. UNMAPPED/ambiguous-with-and-without-UTR/DUPLICATE_TXN/MOZART_INDETERMINATE (6 bank cases), retry-preprocessor XAS gating (I81), ART repair routes (real routes, exercised directly by the verifier since `recon` has no runtime).

**CANNOT support:** ~145 bank/rail worker families; channel-health/downtime emitter to PS (EF-008 — "FTS partner-bank health notification to PS (route H) is never emitted in the twin", `expected-failures.yaml:368`); multi-channel failover; per-bank TPS/thresholds under load.

**⚠ Contradiction to resolve [V]:** `m6-journey-coverage.md` blocks `journey:idempotency-retries/retry` on the grounds that the FTS retry workers "run with `REDIS_QUEUE_HOST="arena-dummy-redis-queue-host"` … so the Redis-backed retry queue FTS schedules re-attempts on does not exist". I confirmed the env vars are indeed dummies (`docker inspect env2_compose-fts-worker-retry-transfer-1`), **but** `ENV2_COMPOSE/generated/fts/env.arena.toml:60-66` sets `[queue] DIALECT="redis"` and `[queue.redis] HOST="redis"`, which overrides the `env|REDIS_QUEUE_HOST` indirection that only appears in `env.default.toml:97-99`. The dummy env vars are decoys for the default layer. The retry blocker is therefore **not established** by that evidence; the real cause is unproven. **[I]** — flagged, not resolved.

---

### 2.3 Ledger

| field | value |
|---|---|
| **Class** | **real source running** (API + 5 workers) / **real source running, one-shot** (scheduler) |
| SHA | `471ff4d5321b6b99a7965882d18f572a1adf5194`; image `878be96a` (matches provenance) **[V]** |
| Runtime | `ledger-api` (`:1357`), `ledger-worker` (`:1381`), `ledger-worker-balance-update` (`:1405`), `-journal-create` (`:1429`), `-entry-details-create` (`:1453`), `-entry-details-create-pg` (`:1477`) — all up; `ledger-scheduler` (`:1501`) **Exited (0)** **[V]** |

**[V] `ledger-scheduler` is a run-to-completion job, not a daemon.** `docker logs env2_compose-ledger-scheduler-1` ends with `SCHEDULER_EXECUTE_REQUEST {"cmd":"split-account-balance-update","ttl":600000000000}` then an `account_details` query returning `[rows:0]`, then exit 0. Production runs this as a CronJob every 10 min (`runtime-topology.yaml:330`: `split-account-balance-update-live(+pg-live)`, `*/10 * * * *`, `suspend: false`). **In the twin it fired once, at boot, and nothing re-triggers it.** Classify: real source running (one-shot); production cadence **not** represented.

**Outbox/SNS [V]:** `DEV-068` `[outboxJob].useOutboxJobsSync` and `DEV-069` `[queue].driver` + `pubSub/snsOutboxer journal_created` drivers are both declared deviations (`:603`, `:615`). SNS topic `journal-created` exists in LocalStack (`seeds/localstack/init-queues.sh:38`).

**ledger-gate [V]:** `high-fidelity replacement` — a scoped transport-fault proxy on the payouts→ledger leg only (`substitutes/ledger-gate/server.py`, 74 lines, `docker-compose.yml:2021`). `DEV-001` (`:42`) is the single most instructive fixed deviation in the registry: its header allow-list silently dropped `idempotency-key`, so **every duplicate-journal result before M1 measured one fewer defence than production**. Now forwards everything except hop-by-hop headers. Note `reports/domain/FIDELITY_CLASSIFICATION.csv` records `ext:ledger` as "ledger-api reached directly (declared deviation: bypasses ledger-gate)" — i.e. the gate is on the path only when deliberately inserted.

**Deviations [V]:** 15 `DEV-*`. `DEV-002` `[mutex].ttl 500→60000` (fixed; a 120× shorter lease that could let two concurrent journal creates both acquire the lock — stacked on DEV-001 it left ledger with *no* effective duplicate suppression on the payouts leg); `DEV-062` authz/splitz/apiService `mock=true`; `DEV-067` 51 prod-only keys (whole `db.pg.reader`, `db.rx.reader`, `db.rx.warm` blocks) absent; `DEV-124` generated RPC + migrations regenerated rather than copied.

**Seeds [V]:** `seeds/postgres/ledger_seed.sql`, `seeds/s4/ledger.sql`, `seeds/s4/ledger_accounts_via_api.sh`. `components.yaml:72` — `ledger_config` is REAL via `CreateInBulk` (`shared_account_x` + `direct_account_x`, 54 rows). Direct-account (DA) parent + per-merchant sub-accounts are provisioned by `RED_LOOP` (`sub:ledger-da-onboarding` in the M6 CSV = `scripts/up.sh` + `RED_LOOP/red_loop/ledger_da.py`), not by the real onboarding flow.

**CAN support:** ledger consistency / accounting conservation (I20-I24; V4-V8, V11, M4 `I-conservation` G51, `I-Direct-success-no-duplicate-ledger` G54), the *deliberately weak* dedupe invariant I03 ("duplicates prevented only by app-level check under a Redis mutex; the twin MUST NOT add a unique index" — V04 asserts the absence of the index **and** guards against a vacuous pass by first checking `pg_indexes` is non-empty), balance authorization as the money authority.

**CANNOT support:** the read-replica/warm-storage topology (DEV-067), the periodic split-account scheduler cadence, tenant-`X` multi-tenancy beyond one tenant, MySQL-dialect `apiDb`/`makeshift` tenants (`runtime-topology.yaml:276`).

---

### 2.4 CFA (contacts & fund accounts)

| field | value |
|---|---|
| **Class** | **real source running** |
| SHA | `d488558e162c08e9fa04dff005f27d846ce88ad2`; image `1b6cf763` (matches) **[V]** |
| Runtime | `cfa-server` (`:1853`, gRPC 8080 / HTTP 8081 / internal 8082), `cfa-worker-contact` (`:1879`), `cfa-worker-fa` (`:1905`) **[V]** |

* **[V]** Store engine differs from production: `mongo:6.0` in the twin vs **DocumentDB** in prod (`components.yaml:88`, `runtime-topology.yaml:277`).
* **[V]** `DEV-094` (`:777`) — `build/cfa-entry.sh` runs a **socat forward `127.0.0.1:27017 → mongo-cfa:27017` inside the cfa containers**, because cfa's Mongo client only skips TLS when the endpoint is `localhost` (`runtime-topology.yaml:277`, `config/generate.py:358-364`). Classified `representative_substitute`.
* **[V]** `DEV-091` `[FundAccountFetchMultiple].enable_cfa_db/enable_tidb`; `DEV-092` ElasticSearch/Dcs/Wda/VaultService/BinService/ApiService all `Mock`; `DEV-093` queue + relaxed-beneficiary-name-regex settings.
* Seeds **[V]:** `seeds/mongo/cfa_seed.js`, `seeds/s4/cfa.js`, `seeds/s4/cfa_via_api.sh`, `seeds/generated/cfa-entities.json` (204 KB).
* **CAN support:** beneficiary ownership / tenant scoping (CFA filters every query on `merchant_id`, control C6), SHA3-256 dedupe **without** unique indexes (a deliberately preserved production weakness, `components.yaml:86`).
* **CANNOT support:** DocumentDB-specific behaviour, ES-backed search, Vault/BIN/Token enrichment, ValidX-driven fund-account validation (C29 — the gate does not exist in the cloned code at all).

---

### 2.5 x-balances

| field | value |
|---|---|
| **Class** | **real source running (server) / real source running but functionally inert (worker)** |
| SHA | `1a21c0f9111da14125d839dc0dfe8d980740f207`; image `980f8333` (matches) **[V]** |
| Runtime | `xbalances-server` (`:1931`), `xbalances-worker` (`:1957`) — 1 generic worker vs 5 per-bank families in prod (`runtime-topology.yaml:176`) **[V]** |

**The worker is inert, and this is declared, not accidental [I from documents, V on config]:** `components.yaml:80` — "`[Queue] Driver=inmemory`, Mozart/banking-accounts `Mock=true`, no fetch caller, seed status `'activated'` vs code `'active'` (C-008) and seed `account_type 'pool'` vs code `'shared'` (C-007) — so **no BalanceRefreshEvent, no dual-write to the monolith**". Backed by `DEV-081` `[Queue].Driver` (`:678`) and `DEV-082` (`PayoutService/APIService/AccountService/BankingAccounts/Mozart/DCS` all `Mock=true`, `:690`). This is `EF-007`: "x-balances BalanceRefreshEvent (reservation release trigger A) is unreachable in the twin without event injection" (`expected-failures.yaml:314`). M4 works around it by injecting the event / bumping `last_fetched_at`.

* `GET /v1/balances/{id}` and `/v1/accounts` do work and are the twin's Direct balance authority (V12/V13).
* Seeds: `seeds/s4/xbalances.sql`; balance rows for Direct merchants are SQL-seeded by the provisioner in place of the real banking-accounts activation event (`components.yaml:97`).
* **CAN support:** Direct balance reads, reservation-gate arithmetic. **CANNOT support:** the balance-refresh event loop, per-bank fetch cadence/failure modes, the reverse dual-write to the monolith.

---

### 2.6 API monolith → `monolith-stub`

| field | value |
|---|---|
| **Class** | **high-fidelity replacement** for the PS/FTS-facing surface; **graph-only** for the entire ingress/dashboard/admin surface. The real `razorpay/api` is **source mapped but not running** (`svc:api-monolith` = `real_source_mapped_not_running`, "source clone readable in `.local/repos-root`… no real binary runs in the twin") **[V]** |
| Path | `ENV2_COMPOSE/substitutes/monolith-stub/server.py` (1115 lines), route table `:1080-1112` (26 routes), own `Dockerfile` (adds `PyMySQL==1.1.1`) |
| Runtime | `monolith-stub` (`docker-compose.yml:2037`), port 8080 |

**Why "high-fidelity" and not "stub":** it is the only base_stub-family substitute that **enforces inbound Basic auth** (`STUB_BASIC_AUTH_FILE=/run/secrets/monolith_basic_auth`, `docker-compose.yml:2058`) and the only one that reads **real MySQL** — two read-only DSNs, `mysql-payouts` as `monolith_reader` and `mysql-apidb-stub` as `monolith_balance`, with an id regex guard `re.fullmatch(r"[A-Za-z0-9]{14}")` before `SELECT * FROM payouts WHERE id = %s` (`server.py:334-358`). That directly implements `TWIN_SPEC/substitute-contracts/monolith-stub.md:12`.

**Real behaviours reproduced [V]:** `create_fta` guard chain with per-payout mutex, 1 s FTS timeout, and the five distinct outcomes (`server.py:361-430`); `update_fts_fund_transfer` relay that always POSTs details **before** PATCHing status (`:650-676`) and returns 200 with a skip message on unknown status or invalid transition; the DA-ledger journal emitter reproducing `processLedgerPayoutForDirect`; fault knobs `PS_RELAY_MODE=relay|drop`, `PS_DUAL_WRITE_MODE=ok|fail` (which reproduces the 2026-09-03 production incident), `FTS_CREATE_MODE=swallow|error`.

**Auth [V]:** inbound Basic `rzp_live`; outbound Basic `api_monolith:<secret>` to FTS and `api:<auth_api_payouts>` to PS. **No passport is minted or verified anywhere in this stub** — so nothing about monolith-side authorization (C48 `AdminAccess::policyChecker`) is represented.

**Seeds [V]:** `seeds/generated/monolith/{merchants,fund_accounts,misc}.json` + `seeds/generated/pricing.json`.

**Deviations [V]:** `DEV-150` no Kafka consumer (`:954`); `DEV-163` DA-journal trigger relocated to XAS `payout_update` arrival; `DEV-164` `_da_journal_payload` field shape (`notes.transaction_id` = `bas_<id>` not `txn_<api txn>`; `transaction_date` = PS `payouts.updated_at`); `DEV-165` `_publish_journal`/`_ensure_ledger_topic` infra wiring; `DEV-166` `_merchant_features` is a per-merchant stub feature, not a Splitz evaluation. Plus `DEV-140`/`DEV-142` apidb DDL drift and `DEV-141` the out-of-band `payout_details.beneficiary_bank_code` column.

**Known-incorrect and missing [V]:**
* `dual_write` still returns `{"status":"queued"}` where `TWIN_SPEC/substitute-contracts/monolith-stub.md:30` says it must be `{"status":"success"}` — flagged INCORRECT and still true.
* Pricing is a flat ₹2 + 18% GST for every mode and merchant (`monolith-stub/CONTRACT.md:61-63`) — **no plan model at all**.
* Two routes (`actor_info_internal`, `users_internal`) are documented as **not found in the real `Route.php`** and are flagged unconfirmed rather than presented as verified (`CONTRACT.md:42-54`).
* The entire ingress family is **MISSING** (`monolith-stub.md:66-73`): classic `POST /v1/payouts` with `MerchantIdempotencyHandler`, `payouts_with_otp`, `payouts/{id}/approve|reject`, `payouts/approve|reject/bulk`, `bulk_approve`, `admin/payouts/cancel`, `manual_action`, `manual/status`, `retry`, the 3-month auto-cancel cron, `wf-service/state/callback`. `xas-recon-repair.md:26-31` lists six repair-surface routes it should own — all absent.
* **Preserved production gap, not a bug:** `da_ledger_skipped_ps_recon` — the monolith defers to PS "which already does this", but PS has that call commented out (`payouts/core.go:7284`); the stub keeps the gap (`monolith-stub/CONTRACT.md:120-122`).
* **[V] Live-arena defect currently blocking a family:** `_get_merchant` (`GET /internal/merchants/{id}`) returns `merchants.json` `merchant.feature` verbatim and never merges the `FEATURE_OVERRIDES` that `POST /_arena/merchant_features` records, so `merchantConfig.IsFeatureEnabled(PayoutsOnHold)` always sees `{banking, payout_service_enabled}` — 3 of the 4 `family:on-hold` journeys are BLOCKED on this (`m6-journey-coverage.md`, blocked-dependency table).

**CAN support:** the FTS-status relay transport (route R2) incl. its single-attempt no-retry semantics (C50), `create_fta` ordering, pricing fail-closed at the *response* boundary, DA-journal emission for Direct reconciliation, merchant-config fetch shape.
**CANNOT support:** monolith-side authorization (C48), dashboard/OTP (C1), admin repair (C37), the real pricing plan model (C27), `ApprovedPayoutProcessor`'s live failure (C42), Kafka CDC.

---

### 2.7 Edge / Kong / passport → `kong-lite`

| field | value |
|---|---|
| **Class** | **high-fidelity replacement** for the merchant-API-key shape; **graph-only** for the other three passport shapes. Real `razorpay/edge` is **source mapped but not running** (`svc:edge-kong` = `real_source_mapped_not_running`) **[V]** |
| Path | `substitutes/kong-lite/server.py` (329) + `route_policy.py` (86) + `CONTRACT.md` (118) |
| Runtime | `kong-lite` (`docker-compose.yml:1985`); **the only dual-homed container** (`rzp-arena` + `rzp-ingress`, `:1995`); healthy, `routes: 8`, `merchants_loaded: 161` **[V]** |

**What it really does [V]:** longest-prefix upstream table (`server.py:35-44`) → **404 before auth** so an unrouted path cannot leak auth-vs-not-found (`:243-251`) → Basic-Auth against `seeds/generated/merchants.json` keyed on `rzp_{test,live}_ARENAM…` with per-merchant secret files → mode from key prefix → **RS256 passport mint** (`_mint_passport_jwt` `:152-176`, stdlib DER+PKCS#1v1.5 signer in `_common/rsa_sign.py` because no crypto library was available) → drop `authorization` → inject `X-Passport-JWT-V1` and, when configured, `Authorization: Basic api:<auth_api_payouts>`. **Fails closed** with 500 `passport_signing_key_unavailable` if the key is missing (`:279-281`).

**M3 route policy [V]:** `route_policy.py` — explicit DENY list (payouts internal, bulk, admin/workflow/cron, `/v1/contacts*`, `/twirp/*`), method-bound ALLOW list with per-row `inject_cred_api` (notably **False** for `/v1/balances`→xbalances, `:67`), `STRIP_IDENTITY_HEADERS = {x-merchant-id, x-entity-id}`, default-deny `classify_request`. Gated by `KONG_ENFORCE_ROUTE_POLICY`, **default `"0"`** in compose (`docker-compose.yml:2015`) so the frozen verifier stays behaviour-neutral. The M4 boundary gates (G46-G52) run with it set to `1` (`verifier/helpers/m4_boundary.py:183`).

**Arena-only surface [V]:** `POST /_arena/mint` — an **unauthenticated passport-signing oracle** for any `ARENA*` merchant id with arbitrary roles (`server.py:214-229`). Registered as `KG-SUB-MINT` in `RED_LOOP/registry/known_gaps.yaml`, and the red-agent broker hard-denies `/_arena/*`, so a candidate that depends on it is a twin artifact, never a product finding.

**CANNOT support [V]:** three of the five production auth inputs are unimplemented (`kong-lite.md:9-15`) — partner impersonation (`impersonation{Type,Consumer}`), dashboard-user session (`consumer{type:"user"}` + roles), and admin `X-Admin-Token` → `consumer{type:"admin"}`. The full claim schema (`domain`, `oauth{}`, `credential{}`, `additional_identities`) is not minted. No Lua plugins, no rate limiting (correctly — C43 says prod has none attached either), no `authz-enforcer` shadow mode, no `edgev2` kid semantics. Fixed `timeout=10` per upstream call, no per-route budget.

---

### 2.8 Bank gateway → `mozart-sim` (and the blocked `mozart-mock`)

| field | value |
|---|---|
| **Class** | `mozart-sim`: **high-fidelity replacement** for RBL v1 / **behavioral stub** for every other bank. `mozart-mock` (the real binary): **production configuration or reachability unknown → build-blocked** |
| Path | `substitutes/mozart-sim/server.py` (519) + `CONTRACT.md` (156) + `test_scenarios.py` |
| Runtime | `mozart-sim` (`docker-compose.yml:2327`, port 8085, profile `substitutes`); `mozart-mock` (`:2285`, profile `mozart-real`) **not created** **[V]** |

* **[V] The block is confirmed, not assumed:** `go build` of a real `../mozart` clone fails on the private module `github.com/razorpay/integrations-utils` (404 for this build identity) — `ENV2_COMPOSE/README.md:130-134`, `mozart-sim/CONTRACT.md:3-13`, `docker-compose.yml:2286-2296`. The M6 CSV records `sub:mozart-mock` and `svc:mozart-mock-mode` as `blocked_missing_access` — the only two such rows in 1,995.
* Response envelope is exact: `{data, error, external_trace_id, mozart_id, success, next}` with **no `meta` field**, matching real Mozart (`CONTRACT.md:40-44`) — which matters because control C57 says FTS computes processed/failed locally from `bank_status_code` via two Go maps, and an unmapped code defaults to `{Pending:true, ErrorType:UNMAPPED}`.
* Deliberate protective deviation **[V]**: "The substitute NEVER returns `Status=Success` with zero rows: `rbl_gateway.go ParseBankResponse` indexes `records[len(records)-1]` unconditionally … an empty successful file would panic the real worker" (`CONTRACT.md:153-156`).
* **CANNOT [V]:** it **ignores `{gateway}/{version}`** entirely (`mozart-sim.md:25`) — Direct and Pool are indistinguishable at the bank boundary; ~6 of ~140 status codes; amount-keyed scenario selection is explicitly "non-production"; no per-scenario latency/hold knobs; credentials ignored (so `bankingaccounts-stub`'s synthetic RBL creds, DEV-168, are never checked). `KG-FID-BANK` in `known_gaps.yaml` classifies any finding that needs real bank behaviour as `NEEDS_HIGHER_FIDELITY`.
* Arena control plane `POST /_arena/scenario` is `KG-SUB-MOZART-SCENARIO` — forcing a bank outcome is never a finding.
* **[V] Auth is open:** compose sets no `STUB_BASIC_AUTH_FILE`, so `_common/base_stub.py:60` fails open; even the `/_arena/*` endpoints' `_authorized()` call is a no-op.

---

### 2.9 Bank-statement ingestion & reconciliation → `payouts-worker-rbl-banking-account-statement` + `xas-sim` + `bankingaccounts-stub`

This is the one journey family where a **real payouts worker** drives a **substituted** matcher.

| piece | class | evidence |
|---|---|---|
| `payouts-worker-rbl-banking-account-statement` | **real source running** | `docker-compose.yml:844`; container created `2026-09-07 00:45:14` (grafted post-boot) **[V]** |
| `bankingaccounts-stub` (credentials + shield details) | **behavioral stub** | `substitutes/bankingaccounts-stub/server.py` (108 lines, 3 routes); credentials are hardcoded synthetics `arena`/`arena-rbl-pass`/`arena-rbl-client`/`arena-rbl-secret`/`ARENACORP` (`server.py:71-77`), **DEV-168** (`:1081`); mozart-sim ignores them anyway |
| `mozart-sim` `POST /razorpayx/rbl/v2/account_statement` | **high-fidelity replacement** | base64 CSV, `RBL_CSV_HEADER` at `server.py:304`, options `no_records|failure|http_error|next_key|page_size|duplicate|sticky`; **DEV-167** (`:1069`) |
| `xas-sim` (x-account-statements) | **high-fidelity replacement** | `substitutes/xas-sim/{server.py 692, awslite.py 140, CONTRACT.md 82, test_contract.py 291}`, `docker-compose.yml:2258`. Consumes the **real** PS SQS queue `x_account_statement_source_event` via its own stdlib SigV4 client, matches utr→grn→cms + balance/account + amount, POSTs the 9-field `payout_update` to monolith-stub, serves `GET /v1/account_statements/fetch_multiple_by_reference_numbers` |
| `xas-sink` (the M1 predecessor) | **graph-only** | `substitutes/xas-sink/server.py:14` — `ROUTES = {}`; **no compose service exists**; only referenced by a comment at `docker-compose.yml:2253` and still declared in `config/arena.yaml:66` as `xas_sink` **[V]** |
| `recon` / ART | **graph-only** | `svc:recon` = `graph_only`, "no runtime, no substitute; FTS ART routes real but caller absent". Control C47 describes the real FinOps-gated Kafka→HTTP dispatcher; the verifier calls FTS's ART routes directly instead |

**Deviations [V]:** `DEV-160` (statement store fed by `POST /_arena/statements/sync`, `:981`), `DEV-161` (`_enrich_statement_with_event → dual_write_update` made **synchronous**, replacing Kafka CDC/Maxwell, `:993`), `DEV-162` (`handle_source_event`, `:1005`), `DEV-169` (`ensure_basd_row` — BASD rows inserted by the RED_LOOP provisioner, not by a real onboarding flow, `:1093`), `DEV-170` (cron-driver `bas_fetch_initiate` cadence, `:1105`), `DEV-171` (`[configs.account_statement_source_event]` + `[sns]`, `:1117`).

**EF-005 is deliberately preserved [V]:** "PS source-event field name does not match the XAS consumer schema (`event_created_timestamp` vs `event_create_timestamp`)" — `expected-failures.yaml:241`; `components.yaml:180` requires xas-sim to *preserve* the mismatch.

**CAN support:** statement→payout matching on the correct tuple (M4 `I-reconciliation-correct-tuple` G26 + a wrong-amount negative control), duplicate-statement idempotency at the real worker's DB dedupe and the real ledger dedupe (M4 journey F, `I-balance-not-decremented-twice`), statement-first ordering asymmetry between the FTS-direct route (real `VerifyPayoutFailedTransaction` refuses) and the monolith relay (moves to failed) — M4 journey E, finding F-T10-1; bank-return credit turning a failed payout into reversed (I82, M4 journey G).
**CANNOT support:** real Kafka-CDC (Maxwell) dual-write, the monolith's own `saveAccountStatementV2` matcher (one of the three matchers, C-013), the real x-account-statements Go binary (`svc:x-account-statements` = `graph_only`, repo not readable for this identity), ART/recon as an actor.

---

### 2.10 Webhooks → `stork-capture` + `merchant-webhook-sink`

| field | value |
|---|---|
| **Class** | both **high-fidelity replacement**. Real `razorpay/stork` is **source mapped but not running** |
| Path | `substitutes/stork-capture/server.py` (269); `substitutes/merchant-webhook-sink/server.py` (101) |
| Runtime | `stork-capture` (`:2196`), `merchant-webhook-sink` (`:2227`) **[V]** |

* **[V]** Wire format is **JSON** Twirp, not protobuf; the real client is protobuf (`payouts/pkg/stork/client.go:105`), so the twin patches payouts (`ARENA_STORK_JSON=1`, **DEV-121**). A substitute that wanted to drop the patch would have to decode protobuf.
* Signature contract is the load-bearing correctness detail: delivered body = `event["payload"]` (the raw JSON string), `X-Razorpay-Signature = HMAC-SHA256(secret, body)` — an earlier version delivered the whole envelope while signing only the payload, "which made every real HMAC verification fail by construction" (`stork-capture/CONTRACT.md:59-69`).
* **No dedupe on `event.id` — deliberately**, matching real Stork's at-least-once-per-channel guarantee (`CONTRACT.md:71-74`). Max 30 webhooks per owner; secret never echoed.
* Fault hooks: `STORK_REORDER`, `STORK_DUPLICATE_TERMINAL` (`KG-SUB-STORK-DUP`; proven byte-identical to baseline when off, `test_stork_duplicate.py`).
* **⚠ [V] Code contradicts both its own contract and TWIN_SPEC:** `merchant-webhook-sink/server.py:74` returns **401** on a bad/absent signature, while `merchant-webhook-sink/CONTRACT.md:30-35` and `TWIN_SPEC/substitute-contracts/stork-capture.md:27` both specify an unconditional 200. Any assertion about merchant-endpoint ACK semantics is measuring the code, not the contract.
* **[V]** Both write "durable" `.jsonl` logs to `/data`, which compose mounts as **tmpfs** (`docker-compose.yml:2220-2222`, `2245-2247`) — not restart-durable.
* **CANNOT [V]:** the real retry schedule (exponential, max 15 attempts / 24 h → `EXCEEDED`) — single attempt only; `Update`/`Delete` RPCs; `STORK_DELAY_MS` / per-event drop; the async queue (delivery is inline). There is no Stork/ValidX queue in the twin at all (`queue:prod-api-validation-dual…` = `graph_only`).

---

### 2.11 Workflow / approvals — the sharpest fidelity split in the twin

Three distinct things share this name. **[V]**

| thing | class | state |
|---|---|---|
| `razorpay/workflows` (+ Cadence) — the real service | **source mapped but not running** (`svc:workflows` = `real_source_mapped_not_running`); `svc:workflows-workers` and `ext:cadence` = **graph-only** | never built |
| `workflow-sim` — thin control-plane stand-in | **behavioral stub** | **running** (`docker-compose.yml:2352`, port 8092) |
| `workflow-engine` — the M5 reconstruction | **high-fidelity replacement** | **built but NOT running** (`docker-compose.yml:2389`, port 8093; image `rzp-arena/workflow-engine:v1-candidate` = `2ff1a64c85a4` built 2026-09-07 22:54) |

**And neither is reachable in the live arena. [V]** `git show HEAD:ENV2_COMPOSE/config/generate.py:234` defaults `WORKFLOW_HOST` to `http://127.0.0.1:1` — the *frozen dead address*; `m6-journey-coverage.md` states plainly that "The live arena's payouts config has `[workflow] host = "http://127.0.0.1:1"`". Independently corroborated: `payouts-api`'s `NO_PROXY` does not list `workflow-sim`, so even repointing the config without recreating the container would send the call into the `127.0.0.1:9` blackhole. **All 6 `family:approval-workflow` journeys and `journey:failure-reversal-cancellation/pending` are BLOCKED for exactly this reason** — `pkg/workflow/workflow_create.go WfCreate` never returns a workflow id and no payout can reach `pending`.

**`workflow-sim` (running) — behavioral stub.** In-memory `PENDING = {}` only (`server.py:67`); approvals are never automatic; no rule evaluation, no multi-level approval, no expiry, no config resolution, single callback attempt, no persistence (`CONTRACT.md:86-106`). Its `POST /_arena/decide` has no auth *on purpose* — `KG-SUB-WORKFLOW-DECIDE`. Its own compose comment records that it previously mounted `secrets-kong`, which never contained `auth_workflow_payouts`, so "every approve/reject callback into payouts would have been 401'd. It was never noticed because `ARENA_WORKFLOW_HOST` defaulted to the frozen dead address" (`docker-compose.yml:2370-2375`).

**`workflow-engine` (built, not running) — high-fidelity replacement, and explicitly a reconstruction.** `substitutes/workflow-engine/FIDELITY.md:3-5`: "**This is a high-fidelity RECONSTRUCTION of the Razorpay Workflows/Cadence approval decision-engine, not the original service.** The real engine repository was unavailable for this milestone."
* Three separate auth planes (`wfengine/server.py:126-287`): **service** (Basic `workflow:workflow` → `/twirp/…WorkflowAPI/Create`), **actor** (Bearer `wtk_<urlsafe24>`, stored only as sha256, returned once at creation), **admin** (Bearer from `/run/secrets/wfe_admin_token`; **the engine refuses to boot without it** rather than serve an open admin plane, `ARENA.md:89-90`).
* Real enforced invariants, individually marked in `wfengine/core.py`: `[CHECK:dup-request]`, `[CHECK:terminal]`, `[CHECK:org]`, `[CHECK:role]`, `[CHECK:eligible]`, `[CHECK:separation]`, `[CHECK:stale]`, `[CHECK:dup-approval]`, `[CHECK:org-read]`.
* Durable SQLite (WAL, FK on) on the `wfengine-data` volume; `approvals PRIMARY KEY(workflow_id, approver_id)` makes duplicate approval idempotent at the storage layer; `callbacks UNIQUE(workflow_id, kind)` + stable `Idempotency-Key: cbk_<wfid>_<kind>`; success set `(200,201,409)`.
* **Tenancy decision that matters:** `WFE_TENANT_KEY=owner_id`, because payouts sends `org_id` = the Razorpay org id **which every merchant shares** — keying on it "would collapse all merchants into one tenant and destroy cross-tenant isolation" (`ARENA.md:212-215`, `M6_RUNTIME_CHANGES.md:151-180`). Auto-provisions the org and a default policy (1 distinct approval, role `approver`, maker≠checker, 24 h expiry) on first Create; **actors are never auto-created**.
* Workflow id pinned to 14 chars because payouts stores it in `CHAR(14)`.
* **CANNOT [V]:** real Cadence timers/heartbeats, the production RBAC/permission service, the exact production policy DSL, multi-region durability (`FIDELITY.md:49-53`). `ActionAPI/CreateWithEntityId` and `ConfigAPI/CreateV2` (`workflow-service.md:16,19`) are implemented by **neither** substitute. Two production gaps the contract says to *preserve* — expiry fires no callback (C51) and self-approval is not prevented upstream (C52) — are **not** preserved: the engine enforces expiry as a terminal state and enforces maker≠checker, i.e. it is **stronger than production** on both. That must be stated whenever an approval-family result is reported.
* **[V]** `RED_LOOP/m5/FIDELITY.md` does **not** exist; the 15 `FIDELITY.md` hits under `RED_LOOP/m5/campaign/.run/…/engine-*/` are per-mutant build copies, byte-identical to `substitutes/workflow-engine/FIDELITY.md`.
* **[V] Undeclared:** no `DEV-*` entry covers workflow-engine, and `ARENA.md:169` warns "**A freshly provisioned merchant is NOT workflow-applicable**" because `RED_LOOP/red_loop/provisioner_direct.py::_register_dcs` copies `ARENAM00000002`, whose DCS fixture has `enable_payout_workflow: false`.

---

### 2.12 Feature flags, experiments, risk, pricing, accounts

| component | class | runtime | why |
|---|---|---|---|
| **DCS** (`dcs-stub`) | **high-fidelity replacement** on the wire, **but functionally unreachable by a pristine binary** | `dcs-stub` (`:2073`) | Real proto3 on the wire — hand-rolled stdlib codec, `KeyValue.value` = base64(protobuf) of the object named by `Key.object_name`, proto3 zero-value omission reproduced (`CONTRACT.md:29-44`). **The gap** (`CONTRACT.md:81-122`): payouts/cfa build the client `WithModes([Live])` and never set `ServerURL`, so `getLoginURI()` uses a hardcoded env→real-hostname map and `[dcs] ServerURL` is **never consulted**. `ENV2_COMPOSE/README.md:156-169` therefore records that DCS flags resolve to Go zero-values. **DEV-120/DEV-122 supersede that README**: a one-line `GetContextUrl → ARENA_DCS_URL` patch was applied to three goutils/dcs module copies plus `go.mod` replaces (`declared-deviations.yaml:792,816`), verified at `build/arena-patches/goutils-dcs-v1.7.3/dcs.go:111,180-186,192` (`components.yaml:134`). Treat the README §3 paragraph as **stale**. Login returns a constant token that is never validated. `put`/`patch` are in-memory only — a seed edit needs `docker compose restart dcs-stub`. A **substitute bug was found and fixed 2026-09-07**: ignoring per-slot fieldmasks over-returned `queue_payout_bal_buffer` (int64) into a `.Bool()` call and **crashed the pristine payouts-api** (`CONTRACT.md:133-175`) — but see §1.1: the running container predates that fix. |
| **Splitz** (`splitz-stub`) | **behavioral stub** | `splitz-stub` (`:2094`) | Mirrors `goutils/splitz` `evaluateResponse` field-for-field and carries both `result` (payouts) and `enabled` (fts) variables. But `FetchDecisionContext` returns `{"experiments": []}` and the arena template historically set `client_side_eval = true`, so **every payouts experiment resolves to its code default** (`dcs-splitz-pricing-shield.md:13`; `components.yaml:142` calls this "INCORRECT for payouts"). 8 experiments seeded vs ~38 payouts + ~30 fts in production; every seeded rollout value is **ASSUMED** — no Splitz export exists. No bucketing (the `steps` array is static decoration). Code default is `"off"`, `CONTRACT.md:43-44` still says `"on"` — the seed arbitrates. **DEV-025** (43 ids) and **DEV-048** (31 fts ids). |
| **Shield** (`shield-stub`) | **behavioral stub** | `shield-stub` (`:2115`) | Top-to-bottom first-match rule table from `seeds/generated/shield/rules.json`, matcher supports `eq/ends_with/starts_with/contains` + one `and` level. Scripted fixture only (acct ends `9999` → block; M1+vendor_payments → block; M3 → review; else allow). `SHIELD_LATENCY_MS` knob drives V22. Faithfully reflects control C55 — payouts branches only on `"block"`, so `review` ≡ `allow`. Fail-open is payouts-side, not stub-side. |
| **Pricing** (`pricing-stub`) | **behavioral stub, and documented dead** | `pricing-stub` (`:2137`) | `CONTRACT.md:3-5`: "**No, not by any client config rendered in this arena**". `[ccSdk].mock=true` (DEV-015) means payouts builds only `NewMockPayoutFeeCalculatorClient()`; `payouts.toml.tmpl` renders no `[ccSdk]` host at all. `monolith-stub` is the real pricing source. It even returns the wrong key — `"fee"` vs the contract's `"fees"`. `svc:governor` and `svc:charge-collections` are **graph-only** (governor blackholed to `127.0.0.1:1`). |
| **ASV** (`asv-stub`) | **behavioral stub, protocol-incompatible** | `asv-stub` (`:2158`) | `CONTRACT.md:3`: "## Confirmed protocol-level gap: this stub CANNOT serve the pristine payouts binary's real ASV traffic" — payouts' ASV client is **native gRPC** and calls `EstablishConnectionWithConfig` unconditionally at construction; a stdlib JSON `http.server` "cannot answer as a peer (wrong wire protocol entirely, not just a schema mismatch)". Unreachable while the `MerchantConfigViaAsvAndDcs` experiment is off. It usefully flags that `[banking_account_service]` is a *different*, plain-HTTP client — which is `bankingaccounts-stub`. |
| **banking-accounts** (`bankingaccounts-stub`) | **behavioral stub** | `bankingaccounts-stub` (`:2176`) | 3 routes: `GET /payouts/shield/merchant/…/details`, `GET /merchant/{mid}/banking_account_by_account_number/{acct}/credentials`, `POST /_arena/reload`. Credentials are fixed synthetics (**DEV-168**). Hot-reloads `merchants.json` on mtime. Onboarding/activation (which in production creates the FTS source account and publishes the x-balances activation event) is substituted by the provisioner's SQL seed. `svc:banking-accounts` = **graph-only** (repo not readable for this identity). |
| **ValidX** | **graph-only** | — | `svc:validx` = `real_source_mapped_not_running`, "no ValidX substitute exists in ENV2_COMPOSE". Control C29 notes the fund-account-validation gate **does not exist in PS's cloned code at all**, so its absence is contract-faithful. |
| **UPS / Raven / Beam / Slack / settlements / wallet / payout-links / vendor-payments / virtual-account / accounting-integrations / xperience / x-dashboard / admin-dashboard / DynamoDB / ES / TiDB / S3 / Trino / Databricks / Spinnaker / consul-authz / kafka-cdc-maxwell** | **graph-only** | — | All blackholed to `127.0.0.1:1`/`:9` or simply absent. `db:payouts/tidb` and `db:payouts/elasticsearch` graph-only — "SLA-breach + tidb consistency crons inert in twin". |

---

### 2.13 Batch / bulk

| field | value |
|---|---|
| **Class** | `batch-sim`: **high-fidelity replacement**, **built but not running**. Real `razorpay/batch`: **graph-only** in the live arena (`svc:batch` is classified `high_fidelity_replacement` in the M6 CSV, but that classification presumes the unbooted service) |
| Path | `substitutes/batch-sim/{server.py 1429, CONTRACT.md 652, test_batch_sim.py 911}`; image `rzp-arena/batch-sim:v1-candidate` = `c05c61d28a61` built 2026-09-07 23:04 **[V]** |
| Runtime | `batch-sim` (`docker-compose.yml:2451`, port 8094) — **no container exists** **[V]** |

* It **mints three real credentials** outbound: Basic `api:<auth_api_payouts>` for `POST /v1/payouts/bulk`; a **real RS256 `X-Passport-JWT-V1`** signed with the same `passport_private_key` and kid as kong-lite; Basic `rzp_live:<auth_workflow_payouts>` for per-row approve/reject. Rationale (`CONTRACT.md:330`): re-signing with the same key "is the only way to produce a passport the **real** PS accepts without forging or weakening auth."
* SQLite on `batch-sim-data`; `batch_` idempotency-key derivation; chunking; retryable set `(500,501,502,503,504)`; `MAX_ATTEMPTS=5`, `BACKOFF_MS=5000`; `MAX_BULK_PAYOUTS_LIMIT=15`; v2 SHA-1 idempotency cache 24 h + 300 s mutex.
* **CANNOT [V]:** no Spring Batch engine — one worker thread walks chunks **sequentially**, `maxThreads` (10 payout / 6 approval) is not honoured, "so batch-sim cannot reproduce concurrency bugs that need two chunks in flight at once" (`CONTRACT.md:586-590`) — which is precisely the shape of production control **C53** (the classic-bulk race window) and **C54** (the stale "distributed mutex" claim). No Postgres, no Redis, no S3, no validate step, no scheduling. **Monolith hop collapsed**: "A bulk payout that the real monolith would reject before ever reaching PS will succeed here" (`:595-600`). Entry-status vocabulary deliberately diverges — assert on `success_count`/`failure_count`, not entry status.
* Carries the standard warning: "any bug reproduced only because of batch-sim's behaviour is a twin-specific lead, NOT a production finding" (`CONTRACT.md:14-16`).
* Blocks all 5 `family:bulk-payouts` journeys today **[V]**.

---

### 2.14 Crons

| field | value |
|---|---|
| **Class** | **behavioral stub** (`cron-driver`); production cadence **unknown** |
| Path | `ENV2_COMPOSE/scripts/cron-driver/driver.py`; image `python:3.12-alpine`, no healthcheck **[V]** |
| Runtime | `cron-driver` (`docker-compose.yml:2503`), `Up 22 hours` **[V]** |

* Hits payouts' `/v1/cron/*` with Basic `fast_cron`, default 300 s per job (`driver.py:37-40`), overridable per job via `CRON_INTERVAL_<KEY>`.
* **[V]** `ext:fastcron` is classified `behavioural_placeholder` with the reason: "cadence is ASSUMED — the real FastCron export is an external artefact not available to this identity". Control **C59** is the underlying fact: payouts' `/v1/cron/*` is **not** a k8s CronJob — an IP-allowlisted Traefik IngressRoute fronts the external FastCron SaaS, "which alone holds per-endpoint cadence (not in any cloned repo)". `DEV-170` declares the `bas_fetch_initiate` cadence specifically.
* `components.yaml:192` records the M1-era state ("3 wrong paths; batch_submitted missing"); `runtime-topology.yaml:326-341` catalogues the real k8s CronJobs (ledger `*/10`, stork `*/3` and `*/30`, batch `*/15`, cfa none) and FTS's **in-process** schedulers (`fetch_healthy_balance` 300 s; `stuck_payouts_cron` 60 s, **canary/leader-gated** — and `INSTANCE_TYPE=canary` is not set in the twin, so canary-gated crons never fire).
* **CAN support:** dequeue-after-cron journeys (V24a/b, route cases `queued`/`scheduled`), and the *stronger-than-production* no-repair assertion V16 (drives all 8 cron routes at a stuck payout and asserts nothing repairs it — labelled explicitly as stronger than what production enforces, `xas-recon-repair.md:37`).
* **CANNOT support:** real cadence/jitter, k8s CronJob semantics, canary-gated jobs, the ledger split-account 10-minute loop.

---

### 2.15 Queues, topics, brokers, datastores

| component | class | runtime | notes |
|---|---|---|---|
| **SQS/SNS** → LocalStack | **high-fidelity replacement** | `localstack` (`localstack/localstack:3.8`, `:304`) | **[V]** `seeds/localstack/init-queues.sh` creates **36 queues + 2 SNS topics** (`payout-updates-test-dev`, `journal-created`). Real prod DLQ/visibility-timeout live on the AWS resource in Terraform, "not in any readable repo" (`runtime-topology.yaml:282`) — only ledger states a `visibilityTimeout` (120 s) in app config. `DEV-024` substitutes all 19 payouts queue names. **[V]** Of the 25 `[job]` queues, 10 have **no consumer running** in the live arena (§2.1). |
| **Kafka** | **real source running (broker + PS consumers); producer leg OFF** | `kafka` (`apache/kafka:3.8.0`, `:325`) | **[V]** Topics `rx-fts-status-update-events` / `-retry-events`. The FTS producer is `enabled=false` under the default `monolith` route profile (`DEV-041`); `ARENA_ROUTE_PROFILE=kafka` turns the whole leg on coherently (`config/routes.py PROFILES`, `M6_RUNTIME_CHANGES.md:226-258`). Behaviour note for whoever flips it: with the leg on, `fts/internal/transfer/service.go:1136-1144` publishes and **returns early** — Kafka *replaces* the HTTP webhook for that merchant. |
| **Redis** | **real source running** | `redis` (`redis:7-alpine`, `:295`) | **[V]** Shared by payouts (idempotency mutex, in-flight reservation Lua, bene-bank downtime map), fts (machinery queues, scope `arena` vs prod `fts_live`), ledger (mutex), x-balances. |
| **MySQL** ×4 | **real source running** (engine); schema from the repos' own migration binaries | `mysql-payouts`, `mysql-fts`, `mysql-xbalances` (`mysql:8.0`), `mysql-apidb-stub` | **[V]** `db:api-monolith/mysql` is classified **`behavioural_placeholder`**, not real — its DDL is a hand-written skeleton (`seeds/mysql/apidb-ddl/00_init.sql`) with declared drift `DEV-140` (`features.name`) and `DEV-142`. `DEV-141` patches `payout_details.beneficiary_bank_code`, a column production creates out-of-band with no migration. Prod engine **versions are UNKNOWN-BLOCKED** — "no terraform/RDS/MSK/ElastiCache infra repo is in the readable REPOS set" (`runtime-topology.yaml:284`). |
| **Postgres** | **real source running** | `postgres-ledger` (`postgres:15-alpine`, `:265`) | **[V]** Prod uses postgres for `db.rx`/`db.pg` and a **MySQL dialect** for the `apiDb`/`makeshift` tenants — the latter is unrepresented. Ledger RX migrations require a CI-only materialization step (`BUILD_PROVENANCE.md:44`). |
| **Mongo** | **real source running (different engine)** | `mongo-cfa` (`mongo:6.0`, `:280`) | **[V]** Prod is DocumentDB; plus the socat localhost forward, DEV-094. |
| **Elasticsearch / TiDB / DynamoDB** | **graph-only** | — | **[V]** No service; `[elasticsearch].Mock` and `[wda].mock` (DEV-016). |
| **`*-migrate` jobs** ×5 | **real source running (one-shot, completed)** | `payouts-migrate`, `ledger-migrate`, `fts-migrate`, `cfa-migrate`, `xbalances-migrate` | **[V]** "real migration binary from the pinned SHA; container removed after a successful run". 118 migration assets read-verified under UID 10001 with `--network none` (`BUILD_PROVENANCE.md:122`). |
| **`verifier`** | **high-fidelity replacement (test harness)** | profile `verify`, not a long-lived container | **[V]** Carries the 26-check suite, the 8 M4 boundary gates, route/bank/kafka scenarios. Image `rzp-arena/verifier:v1-candidate`. |

---

## 3. Business journeys currently exercised

Four suites exercise the twin. They were written at different milestones against
different arena states, so their component reach differs — read them together, not
interchangeably.

### 3.1 Verifier suite (`ENV2_COMPOSE/verifier`) — the frozen golden suite

**[V]** Run by `verifier/run.sh`; result **26 passed / 0 failed / 0 skipped** on clean
replays 3, 19 and 20 (`reports/implementation/SCENARIO_COVERAGE.md:3`,
`ROUTE_COVERAGE.md:5`). Every test is wrapped by `conftest.py:311-331`'s
`isolated_scenario` fixture, which programs per-merchant bank behaviour in mozart-sim
and clears it afterwards.

| id | test (file:line) | components touched |
|---|---|---|
| G0 | golden `shared_success_via_kong` (`scenario.py:31`) | kong-lite → payouts-api → mysql-payouts → ledger-api/postgres-ledger → fts-web/fts-worker-* /mysql-fts → mozart-sim → payouts-worker-transaction-create/-webhook-event → stork-capture → merchant-webhook-sink → monolith-stub (snapshot) |
| V1/V2 | idempotency same/different body (`test_v01…:13`, `test_v02…:13`) | kong-lite, payouts-api, redis, mysql-payouts, mozart-sim(hold) |
| V3 | FTS dedupe on `(source_type, source_id)` (`test_v03…:17`) | fts-web, mysql-fts |
| V4 | ledger journal dedupe, **and the absence of a unique index** (`test_v04…:9`) | ledger-api, postgres-ledger |
| V5/V6/V7/V8 | initiated / processed / reversed / failed-Direct accounting (`test_v05…:8`, `test_v06…:71`, `test_v07…:9`, `test_v08…:7`) | payouts-api, ledger-api/postgres-ledger, fts-web(+rbl workers), mozart-sim, pricing via monolith-stub |
| V9/V10/V11 | balance authorization: queued / 400-reject / synchronous debit | payouts-api, ledger-api, postgres-ledger, mysql-payouts |
| V12/V13 | in-flight reservation total; release on terminal event | payouts-api (`/v1/inflight_reservations`), redis, dcs-stub, mysql-fts |
| V14 | no illegal `payout_logs` transition (`test_v14…:64`) | payouts-api, mysql-payouts, fts-web |
| V15 | FTS webhook on a cancelled payout is a no-op | payouts-api internal, mysql-payouts |
| V16 | **stronger-than-production**: drives all 8 cron routes at a stuck `initiated` payout and asserts no repair (`test_v16…:52`) | payouts-api FastCron, cron-driver route list, mysql-payouts, fts-web, mozart-sim |
| V17a/V17b | reversal row before ledger call; reversal survives a real Ledger outage via async retry | payouts-api, **ledger-gate (fault injection)**, ledger-api, payouts-worker-payout-update-failure-handling, postgres-ledger |
| V18 | Shared→`reversed` vs Direct/RBL→`failed` remap | payouts-api, fts-web, mozart-sim(failure), mysql-payouts |
| V19 | `transaction_id` set blocks the `failed` transition | payouts-api internal, mysql-payouts (explicit SQL fixture, recorded in the trace) |
| V20 | tenant isolation: another merchant can neither fetch nor cancel | kong-lite (two passports), payouts-api, mysql-payouts |
| V21 | terminal webhook fires exactly once | payouts-api, payouts-worker-webhook-event, stork-capture, merchant-webhook-sink |
| V22 | Shield latency → payout fails open | payouts-api, shield-stub |
| V23 | pricing 500 rejects the create — fee path fails closed | payouts-api, monolith-stub (pricing fault), mysql-payouts/postgres-ledger/mysql-fts (all negative) |
| V24a/V24b | queued dequeues after top-up + cron; scheduled dequeues after IST slot + cron | payouts-api, ledger-api, cron routes, payouts-worker-queued-payout / -schedule-payout, fts-web, mozart-sim |

**Route scenarios** (`route_scenarios.py:16`, 6 cases, all passing at boot20 with a
passing egress audit): `direct_success`, `scheduled`, `queued`, `returned`,
`failed_direct`, `failed_shared`. The `returned` case is the one that proves the twin
can drive an admin reconciliation: ordinary `POST /v1/transfer/{id}/check` is refused
`ILLEGAL_STATE`, so it uses FTS **admin** identity `api_monolith` against
`/v1/attempts/verify` then `PATCH /v1/attempts/safe_update` — and the file is explicit
that this is **not** an automatic-return-polling claim (`route_scenarios.py:166`).

**Bank scenarios** (`bank_scenarios.py:22`, 6 cases, all passing): `hold`,
`delayed_success`, `ambiguous_with_utr`, `ambiguous_without_utr`, `duplicate`,
`timeout`. Known limit: `timeout` is a gateway-timeout *response*, **not** proof the
FTS 180-second HTTP-client deadline fired (`BANK_SCENARIOS.md:12`).

**Kafka scenarios** (`kafka_scenarios.py:49`, 5 cases, `ARENA_ROUTE_PROFILE=kafka`
only — fails closed otherwise; **not the live arena's profile**): `shared_source_failure`
(EF-001), `direct_after_shared` (**the only `passed` case** — a regression test proving
the consumer survived the nil-producer crash), `failed_dropped_direct` (EF-002),
`failed_dropped_shared` (EF-002), `reversed_dropped_direct` (EF-003). An EF case is
"ok" only when `status == expected_failure` **and** `matches_prediction is True`
(`kafka_scenarios.py:407`) — it is never recorded as `passed`.

### 3.2 M4 boundary gates — the authz/tenant-isolation suite

**[V]** `reports/implementation/m4-boundary-results.json`, generated 2026-09-06T19:26:04Z:
**10 passed, 0 failed, 1 xfailed, 0 skipped**. These run with the **hardened edge**
(`KONG_ENFORCE_ROUTE_POLICY=1`), which the live arena does **not** default to.

| gate | test | what it proves |
|---|---|---|
| G34+G45 | `test_m4_g34_duplicate_terminal_webhook.py:20` | replaying a terminal FTS webhook twice is a no-op for status, `payout_logs` and sink deliveries; documented exception: `updated_at` is re-touched |
| G46 | `test_m4_g46_cross_merchant.py:22` (both directions) | A cannot read/change B's payout, balance, banking account or reservation set; 8 probes; each denial's **layer** is recorded (`service_tenant_scope_40x`, `gateway_no_route_404`, `service_validation_400`, `gateway_upstream_unreachable_502`) |
| G47 | `test_m4_g47_body_identity_override.py:17` | body `merchant_id`/`account_number` cannot override the edge-minted passport consumer |
| G48 | `test_m4_g48_forged_service_headers.py:16` | forged `X-Service-Name`/`X-Task-Id`/`X-Passport-Actor-Type`/`X-Razorpay-Account`/`x-merchant-id`/`X-Entity-Id`, plus **five forged passport shapes** (alg=none, HS256, wrong-kid RS256, expired-unsigned RS256, tampered payload), confer no privilege — rejected by the SDK, not only the edge |
| G49 | `test_m4_g49_internal_routes_unreachable.py:16` | every internal route group returns gateway 404 `no_route`; proven not-vacuous by a service-credential call from inside the arena returning 200 |
| G50 | `test_m4_g50_denial_layer_distinction.py:39` | the four denial layers (broker / gateway / service-auth / service-tenant) are deliberately triggered and distinguishable |
| G52 | `test_m4_g52_concurrent_idempotency.py:34` | N=8 simultaneous POSTs with one key collapse to one payout and ≤1 FTS transfer, on both a Direct and a Shared merchant |
| **H-D3** | `test_m4_hd3_free_payout_ownership.py:20` (`xfail(strict=False)`) | **open candidate**: `GET /v1/payouts/free_payout/{balance_id}` resolves the banking account by `balance_id` with **no merchant scoping**. This is finding **F-M4-001 (free_payout IDOR)** — carried forward as the standing current-architecture result; **production reachability unknown** |

### 3.3 M4 Direct journeys A–G

**[V]** `reports/implementation/m4-direct-journeys.json`, run `m4-journeys-20260906T202456Z`,
git `8965ec25a2c4`, arena boot `06330f7e`, profile `monolith`. Merchants
`ARENAD83881351` / `ARENAD86313421`, fresh Direct/RBL with `in_flight_reservation_enabled`.
All 7 pass (12/12, 7/7, 7/7, 7/7, 3/3, 3/3, 2/2).

A success · B immediate failure · C pending→success · D pending→failure ·
E **statement-first ordering** · F duplicate statement · G conflict.

Chain for A: kong-lite → payouts-api → redis(reservation) → mysql-payouts →
fts-web/fts-worker-rbl-* → mozart-sim → bankingaccounts-stub →
**payouts-worker-rbl-banking-account-statement (real)** → **xas-sim (substitute)** →
payouts-api `/banking_account_statement/payout_update` (real
`UpdatePayoutAfterBASRecon`) → monolith-stub DA emitter (substitute) → ledger-api /
postgres-ledger (real, two balanced journals `da_payout_processed` + `_recon`) →
stork-capture → merchant-webhook-sink.

Journey E produced finding **F-T10-1**: on the FTS-direct route (R1) the real
`VerifyPayoutFailedTransaction` **refuses** the `failed` transition when a statement
link exists, while the monolith relay (R2) still moves the payout to `failed` — a real
asymmetry between two production transports.

Six phase-5 invariants all PASS: `I-Direct-failure-no-shared-reversal` (G31),
`I-Direct-success-no-duplicate-ledger` (G54), `I-reservation-not-released-twice` (G53),
`I-balance-not-decremented-twice` (G28-adj), `I-conservation` (G51, real for the ledger
leg + **modeled** for reservation/balance), `I-reconciliation-correct-tuple` (G26,
real-worker statement + real PS link but **substitute xas decision**).

Route R3 (Kafka) was **not executed** in this run — the live arena was on
`route_profile=monolith` and switching would disrupt other streams.

### 3.4 M5 workflow scenarios — against the standalone reconstruction

**[V]** `RED_LOOP/m5/scenario_suite.py`; `reports/implementation/m5-workflow-scenarios.json`
= **12/12 passed, `all_passed: true`**. Components: a real `workflow-engine` **host
process** + a `payout_sink.py` callback receiver — **not** the live arena.
`approval_success`, `rejection`, `cancellation`, `expiry`, `duplicate_approval`,
`duplicate_callback`, `stale_decision`, `concurrent_decisions` (one 200, one 409),
`restart_recovery` (**a genuine SIGTERM + relaunch on the same SQLite DB**),
`cross_org_denial`, `maker_checker_separation`, `callback_identity`.

**Blind benchmark [V]:** 4 surgical mutants of the engine, one per invariant family
(org-boundary, maker-checker, approval-integrity, state-integrity); an
`integrity_check` asserts the fixed reference violates none and each mutant violates
exactly its own; the autonomous Director sees only an opaque public manifest, never the
answer key. Result carried in memory: **4/4 hidden defects found, 0 false positives on
the fixed reference.**

**Scope caveat [V]** (`m5-known-limits.md`): the campaign runs against the standalone
engine, **not** through the 67-container arena — reason given is host RAM
(~11.65 GiB; limit **L-001**: only one arena at a time).

### 3.5 M6 business-journey matrix — the current, broadest picture

**[V]** `reports/implementation/m6-journeys.json` + `m6-journey-coverage.md`, run
`RED_LOOP/runs/m6-journeys-20260907T200039Z`, git `2613f383`, arena boot `06330f7e`,
profile `monolith`, generated 2026-09-07T20:27:58Z. **75 journeys — 47 PASS, 9 FAIL,
1 EXPECTED_FAILURE, 18 BLOCKED**, across 12 families × 16 variants.

> **[V] Both files were rewritten at 2026-09-08 01:57, mid-session, by the concurrent
> M6 session.** An earlier snapshot in this same session showed only the 6-journey
> `pricing-free-payouts` run (`m6-journeys-20260907T195704Z`). The 75-journey run above
> is the state as of this document; expect further churn.

| family | P0 gate | outcome |
|---|---|---|
| `shared-payouts` | **MET** | 9 PASS, 1 BLOCKED (`restart` — needs a controlled reboot this lane may not perform) |
| `direct-payouts` | **MET** | 9 PASS (7 delegate to M4 journeys A–G) |
| `queued-low-balance` | **NOT MET** | **5 FAIL**, all `merchant_ledger_balance_readable` |
| `scheduled-payouts` | **MET** | 5 PASS, 1 BLOCKED (`cancel_via_dashboard`) |
| `failure-reversal-cancellation` | **NOT MET** | 3 FAIL (same balance-readability cause), 3 PASS, 1 BLOCKED (`pending`) |
| `pricing-free-payouts` | — | 5 PASS + 1 **EXPECTED_FAILURE** |
| `webhooks` | **MET** | 6 PASS |
| `approval-workflow` | — | **6 BLOCKED** (dead workflow host) |
| `bulk-payouts` | — | **5 BLOCKED** (no batch-sim container) |
| `idempotency-retries` | **MET** | 5 PASS, 1 BLOCKED (`retry`) |
| `accounting` | **MET** | 5 PASS |
| `on-hold` | — | 1 FAIL, 3 BLOCKED (monolith-stub feature-override gap) |

**The 18 BLOCKED journeys and their exact missing dependency [V]:**

| blocker | journeys | root cause |
|---|---|---|
| **dead workflow host** | 6 approval-workflow + `failure-reversal-cancellation/pending` | `[workflow] host = "http://127.0.0.1:1"`; `WfCreate` never returns a workflow id, so **no payout can reach `pending` at all** |
| **no batch-sim container** | 5 bulk-payouts | service defined in the uncommitted tree, never booted; `import bulk_client` also fails |
| **monolith-stub `_get_merchant` ignores FEATURE_OVERRIDES** | 3 on-hold | `payouts_on_hold` never visible to `IsFeatureEnabled`, so `HoldPayoutIfBeneBankDown` short-circuits. Everything else this family needs is already proven live (Redis `{bene_bank_status}` accepts `{"RATN":"started"}`, the fund account is a bank_account, the payout is IMPS) |
| **no proxy-auth passport** | `scheduled-payouts/cancel_via_dashboard` | kong-lite mints only `consumer{type:"merchant"}`; goutils passport v4 `helpers.go:116` returns `LegacyAuthTypeProxy` only for `ConsumerTypeUser` + `user_merchant` impersonation |
| **FTS retry scheduling** | `idempotency-retries/retry` | the bank did return the retryable `INSUFFICIENT_FUND` and FTS persisted it, but no second attempt is created. **See §2.2 — the stated cause (dummy `REDIS_QUEUE_HOST`) is contradicted by the arena TOML; unresolved.** |
| **controlled reboot** | `shared-payouts/restart` | lane boundary, not a fidelity gap |

**The 9 FAILs are one cause [V]:** every one is `merchant_ledger_balance_readable`, on
merchant `ARENAM98299494`. That is a **provisioning/readability** failure, not nine
independent product defects — but it does mean the `queued-low-balance` P0 gate has no
evidence at all right now.

**The 1 EXPECTED_FAILURE is a genuine, uncatalogued source defect [V]:**
`pricing-free-payouts/cancel_or_reverse` — `freePayout/core.go:1009
DecreaseFreePayoutsConsumedIfApplicable` → `decrementFreePayoutsConsumed` writes through
gorm `Updates(struct)`, which **skips zero-valued fields**, so a decrement whose result
is 0 never reaches the DB, while the decrement `counter_transaction` row **is** written
and `fee_type` **is** cleared. Reproduced differentially (1→0 does not persist, 2→1
does). It is **not yet in `TWIN_SPEC/expected-failures.yaml`** — the catalogue is one
finding behind.

**Two honest observations recorded rather than hidden [V]:**
* `webhooks/async_state` — the webhook job was executed by
  `payouts-worker-fts-async-processing` and `-payout-update-failure-handling`, **not** by
  the dedicated `payouts-worker-webhook-event` container.
* `idempotency-retries/duplicate` — the terminal guard absorbs the duplicate for state,
  ledger and webhooks, but payouts still rewrites the row: `updated_at` moved with no
  material field change.
* `pricing-free-payouts/free_payout` — `counters.free_payouts_consumed_last_reset_at`
  had to be aligned to `utils.CurrentMonthTimestamp()`; the **provisioner defect was
  reported, not hidden**.

### 3.6 Production flows with **no** twin representation

**[V]** From `reports/PAYOUTS_FLOW_CATALOG.md:338-351` and the M6 CSV: payout links,
vendor payments / tax payments (TDS), fee-recovery `RzpFees` payouts, petty-cash
`composite_payout_internal`, VA-to-VA / inter-account / reward payouts, IRCTC / wallet /
settlements, partner-OAuth and mobile-number payouts, multi-currency. Flow **A**'s
dashboard/OTP/monolith-authz front half and Flow **G**'s shadow-gateway cutover are
likewise unrepresented.

---

## 4. Security-claim support matrix

Cross-walk of the production control catalogue (`reports/CONTROL_AND_INVARIANT_CATALOG.md`,
C1-C59) against what this twin can actually decide. **[V]** for every row's evidence
pointer; the verdicts are **[I]** judgements built on them.

| claim type | supported? | strongest evidence | ceiling |
|---|---|---|---|
| **Idempotency** (C5, I01/I02/I04) | **YES, strong** | V1, V2, V3, G52 (8-way concurrent, Direct+Shared), M6 `idempotency-retries` 5/6, tenant-scoped keys | `ikey_auto_enforcement=true` is **AND-gated by Splitz `R2Mo039vdGE3Dr`, which every twin fixture returns `"off"` for** — so the *enforced* path is still inert (`BUILD_PROVENANCE.md` M1 notes). Bulk idempotency (I04) untested — batch-sim not running |
| **Authz boundary / tenant isolation** (C6) | **YES, strong** | V20, G46 (both directions, 8 probes, denial layer recorded per probe), G47, G48 (5 forged passport shapes, rejected at the SDK), G49, G50, CFA per-merchant filtering | Only the **merchant API-key** identity exists. Nothing about dashboard sessions (C1), roles (C3), impersonation, or admin tokens can be decided. G46-G50 require the hardened edge, which is **not** the live arena's default |
| **Ledger consistency / accounting** (C15, C18, C19, I20-I24) | **YES, strong** | V4-V8, V11, V17, M4 `I-conservation`/`I-Direct-success-no-duplicate-ledger`, M6 `accounting` 5/5 | Opening balances, `ledger_config` coverage (2 of 34 seeded verbatim; rest via `CreateInBulk`) and account topology are synthetic. DEV-002's mutex fix is **config-level only — never exercised**, because all five ledger workers run `LEDGER_WORKER_MAXCONCURRENCY=1` |
| **Maker-checker / approval** (C12, C14, C51, C52) | **NO in the arena; YES in isolation** | 12/12 M5 scenarios + 4/4 blind-defect discovery, 0 false positives | The engine is a **reconstruction**; the arena's workflow host is dead so **all 6 approval journeys are BLOCKED**; and the engine is **stronger than production** on two controls it was told to preserve (C51 expiry-fires-no-callback, C52 approver-identity-trusted-verbatim) |
| **State-machine legality** (I10, I11, I13) | **YES** | V14, V15, M4 journeys, M6 `async_state` variants across 8 families | — |
| **Webhook delivery / merchant-visible state** (C36, C23) | **YES** | V21, G34/G45, M6 `webhooks` 6/6 | Single-attempt delivery only; no retry schedule, no delay/drop knobs; sink returns 401 where the contract says 200 |
| **Fee / pricing** (C27, I90) | **PARTIAL** | V23 + M6 `pricing` 5/5 prove fail-closed and that fees reach the ledger entries | Flat ₹2 + 18% GST for every mode/merchant. **No plan model, no slabs, no channel/method keying.** Governor + charge-collections are graph-only. I90 per-mode fee assertions are marked NEW/pending |
| **Reversal & failure safety** (C24, C25, I81, I82) | **YES** | V7, V8, V17a/b, V18, V19, M4 journeys B/D/E/G, route case `returned` | Statement matching is `xas-sim`, not the real XAS; the monolith's own matcher (one of three, C-013) is unmodelled; ART/recon has no runtime |
| **Balance / reservation gate** (C16, C17) | **PARTIAL** | V9-V13, G53, G28-adj, M4 reservation FSM | **EF-007**: `BalanceRefreshEvent` is unreachable — release trigger A is injected, not produced. Invariant I-F (release only when `dispatched_at + lag < balance.last_fetched_at`) is asserted by **no** verifier |
| **Bank / rail behaviour** (C20, C21, C57) | **PARTIAL, hard ceiling** | 6 bank cases; FTS FSM and code classification are real | `KG-FID-BANK`: RBL only, ~6 of ~140 codes, amount-keyed, gateway/version ignored, ~145 worker families absent, credentials ignored. **DEV-012 is DECLARED AND NOT FIXED**: mozart circuit-breaker timeout 220000 vs prod 10000 (**22× looser**), and `account_service` runs the other way (1000 vs 10000) manufacturing false trips — "no breaker or resiliency finding on the mozart leg is production-representative" |
| **Async status transport** (C22) | **PARTIAL** | HTTP webhook (R1) and monolith relay (R2) fully exercised; Kafka (R3) only under a non-default profile and only as expected-failures | **DEV-150**: production runs a **second** Kafka consumer inside the api monolith whose handler compensates the DTO gap for monolith-created FTAs. The twin's kafka profile pairs PS-direct create with Kafka status — **precisely the uncompensated combination**, so its Shared-Kafka failure is the worst case, not the typical one. Whether any real merchant is in that combination is an open owner question |
| **Bulk / batch** (C31, C53, C54) | **NO** | — | batch-sim not running; and even when it runs it walks chunks **sequentially**, so the exact concurrency race C53 describes is unreproducible by construction |
| **Kong plugin behaviour / rate limiting** (C34, C43, C44, C45) | **NO** | — | kong-lite is a Python reverse proxy. C43/C45 are *absences* the twin correctly reproduces by having nothing; C44's shadow-mode nuance and C34's cutover cannot be exercised |
| **Prod RBAC / admin repair** (C3, C37, C48) | **NO** | — | No dashboard, no admin-dashboard, no monolith authz; PS `manual_action` and admin routes exist but have no authenticated caller path |
| **Cron cadence** (C39, C59) | **NO** | — | FastCron holds the cadence externally and is not in any cloned repo; `ledger-scheduler` fired once and never again; canary-gated FTS crons never fire |
| **Observability / alerting** (C58) | **NO** | — | 481 `graph_only` rows include every Prometheus rule — "nothing in the twin evaluates Prometheus rules; these are inventory only" |
| **Safety / containment** (S01) | **YES** | `preflight/preflight.py`, `network/egress-audit.sh`, `rzp-arena` is `internal: true`, all egress blackholed to `127.0.0.1:9` (DEV-127); M5 safety check asserts loopback-only with a single external host (the LLM gateway) | — |

### 4.1 Closure-tier cross-walk

**[V]** `reports/PAYOUTS_CLOSURE.yaml` (879 lines, `version: 2026-09-04.v2`) uses its own
scale — `F3` real service+schema, `F2` protocol-faithful substitute, `F1` behavioural
placeholder, `F0` graph-only, `F3-mock` real binary in mock mode — and `:8-17` names nine
things that **must not be mocked simplistically**: `authorization, ledger_state,
idempotency, final_payout_state, reversal, tenant_isolation, routing, approval,
beneficiary_validation`.

Measured against the live arena, **two of those nine are not held**:
* **`approval`** — Workflow Service is declared `F3-capable` (`:378`) but the arena's
  workflow host is dead and the engine is an unbooted reconstruction.
* **`beneficiary_validation`** — ValidX is `F0` (`:244`); control C29 confirms no
  FAV-freshness gate exists in PS's cloned code, so the absence is contract-faithful but
  the property is untestable.

Also note the closure YAML's env-2 row declares **"mozart real binary `-mock` mode"** —
which the build has since **confirmed impossible**. Treat that line as superseded.

---

## 5. Contradictions and stale claims found while assembling this

Every item **[V]** unless marked.

1. **DEV-172 is malformed and its content is silently dropped by the project's own parser** — §0.4. Highest-value fix in this list.
2. **Substitute-image drift**: all 14 running substitutes are dangling images that no longer match their `:v1-candidate` tags — §1.1. No statement about substitute behaviour in the live arena is currently reproducible from the tree.
3. **`BUILD_PROVENANCE.md:52` is one rebuild stale for payouts** (`e029d639` recorded vs `915299ab` running) — §1.
4. **`ENV2_COMPOSE/README.md:156-169` ("DCS is effectively inert") is superseded** by DEV-120/DEV-122 and `components.yaml:134`, which record the `GetContextUrl → ARENA_DCS_URL` patch that makes dcs-stub reachable. The README still tells a reader flags resolve to Go zero-values.
5. **The FTS-retry blocker is not established** — the cited dummy `REDIS_QUEUE_HOST` env vars are overridden by `generated/fts/env.arena.toml:60-66` — §2.2.
6. **`merchant-webhook-sink` returns 401 where its own CONTRACT.md and `TWIN_SPEC/substitute-contracts/stork-capture.md:27` both specify 200.**
7. **`splitz-stub` default variant**: code says `"off"` (`server.py:29`), `CONTRACT.md:43-44` says `"on"`; the seed file arbitrates.
8. **`monolith-stub` `dual_write` still returns `"queued"`** where `monolith-stub.md:30` says `"success"` — flagged INCORRECT and unfixed.
9. **`xas-sink` is dead but still declared**: no compose service, `ROUTES = {}`, yet `config/arena.yaml:66` still lists `xas_sink: {host: xas-sink, port: 8080}` and the running `payouts-api`'s `NO_PROXY` names `xas-sink` but **not** `xas-sim`.
10. **`mozart_identifier` value conflict**: `SYNTHETIC_FIXTURE_SPEC.md:280` says `'rbl_v1'`, its own addendum `:584` says `'v1'`; the format itself is `TODO(confirm)`. **[I]** from the reports.
11. **`features.name` length conflict** (`VARCHAR(255)` vs `VARCHAR(25)`) and the **`x-balances.sub_balances` code-vs-DDL conflict** are both still open (`SYNTHETIC_DATA_PROVENANCE.md:78-79`). **[I]**
12. **`reports/domain/FIDELITY_CLASSIFICATION.csv` reports the intended post-M6 arena, not the live one** — queues served by the 10 unstarted workers are marked `real_source_running` — §0.2.
13. **`components.yaml` is dated 2026-09-05** and its `today:` column is M1-era throughout (e.g. it still says payouts has 14 workers and that `rbl_banking_account_statement` is missing; both changed in M4). Read `reports/domain/FIDELITY_CLASSIFICATION.csv` + this document for current state.
14. **`known_gaps.yaml:20` says "76 entries"** for a registry that now holds 89.
15. **Source SHAs for payouts / fts / cfa / x-balances are not re-derivable from disk** — `.git/HEAD` and refs are stripped in all four clones under `.local/repos-root`; only **ledger** has a surviving reflog line corroborating `471ff4d5…`. `x-account-statements` (`e73fd5a`) and `banking-accounts` (`c3fc1fe8`) have **no** second source. **[I]** (from the provenance agent's direct `git` probes.)
16. **67 of the 74 M4 acceptance gates validate coordinator-written artifacts.** Only **G01, G02, G05, G68, G69, G71, G73** are independent machine checks (git/hash) — `M41_RUNBOOK.md`, `m41-gate-validation-matrix.csv`. The "74/74" figure should always be reported with that split.
17. **Several M1 fixes are config-level and never re-observed on the wire**: DEV-001 (header passthrough — 6 offline contract tests pass, but "no boot has re-observed the header on the wire"), DEV-003 (V01/V02 must be re-run), DEV-004 (needs a rebuild + one bank-failure case), DEV-005 (fix not re-observed under the kafka profile), DEV-006 (retry/duplicate not re-measured), DEV-007 (never observed restarting a crashed consumer).
18. **`TWIN_SPEC/expected-failures.yaml` is one finding behind** — the M6 free-payout decrement-to-zero defect is reproduced but uncatalogued.

---

## 6. Where the twin is strongest and weakest, in one table

| | strongest | weakest |
|---|---|---|
| **Real binary coverage** | 5 real Go services from pinned SHAs; 15/25 payouts workers, 13 FTS workers, 6 ledger processes, 3 CFA, 2 x-balances — all healthy for 22 h | 10 payouts workers, ~145 FTS worker families, 4/5 x-balances bank workers, `ledger-scheduler` cadence |
| **Identity** | merchant API-key → RS256 passport, forged-shape rejection proven at the SDK layer, cross-tenant denial proven in both directions with the denial layer recorded | no dashboard/user/impersonation/admin passport shape at all; the hardened edge that the boundary gates need is **off** by default |
| **Money** | real Ledger + real balance authorization + conservation invariants; the deliberately weak dedupe invariant preserved rather than "fixed" | synthetic opening balances; 2 of 34 `ledger_config` rows seeded verbatim; `BalanceRefreshEvent` unreachable |
| **Bank edge** | exact Mozart envelope, real FTS classification, 6 grounded scenarios, a deviation that *protects* the real worker from a real crash | RBL only, ~6/140 codes, gateway/version ignored, credentials ignored, breaker 22× looser and **declared unfixed** |
| **Approval** | a durable engine with three auth planes, real optimistic concurrency, genuine restart recovery, and a blind benchmark it passed 4/4 with 0 false positives | it is a **reconstruction**, it is **not wired into the arena**, and it is **stronger than production** on two controls it was asked to preserve |
| **Honesty of the record** | 89 declared deviations, 8 expected-failures, a judge-side known-gaps registry that refuses to count substitute artifacts as findings, provisioner defects reported rather than hidden | the record has drifted from the running system in at least 4 material ways (§5 items 1-4, 12-14) |

---

*End of inventory. Produced read-only on 2026-09-08 against branch
`milestone-6-complete-payouts-domain` @ `2613f38`, live arena boot
`06330f7e-3099-4299-ab0c-9b9d50c43899`.*
