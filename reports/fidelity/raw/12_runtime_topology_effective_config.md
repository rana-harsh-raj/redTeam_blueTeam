# Lane 12 — Runtime topology and effective (deployed) configuration

Scope: production (`cde`/`prod` k8s overlays) topology and effective config for payouts, fts,
ledger, cfa, x-balances, stork, mozart, dcs, splitz, workflows, batch, banking-account,
x-account-statements, and payout-relevant `api` (monolith) deployments; diff against the Env 2
twin (`ENV2_COMPOSE`). No secret values recorded — names only. Provenance tags: CONFIRMED (read in
source), INFERRED (derived), UNKNOWN (not derivable; artifact + owner + minimal ask given).

`REPOS=/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture`
(all `kube-manifests/…`, `payouts/…`, `fts/…`, `ledger/…`, `cfa/…`, `x-balances/…`, `spinacode/…`
paths below are relative to this root).

## Scope and sources read

- `kube-manifests/templates/{payouts,fts,ledger,cfa,x-balances,stork,mozart,dcs,splitz,workflows,
  batch,banking-account,x-account-statements,api,api-cron}/templates/*.yaml` (chart templates —
  args/env/probes/ports/volumes)
- `kube-manifests/cde/{payouts,cfa,mozart,mozart-dark,mozart-pagination,mozart-test,
  mozart-whitelisted,batch-clearing}/values.yaml` (the CDE-segregated cluster overlay)
- `kube-manifests/prod/{fts,ledger,dcs,splitz,stork,workflows,x-account-statements,x-balances,
  batch,banking-account}/values.yaml` (the non-CDE prod cluster overlay)
- `payouts/config/{default,prod}.toml`, `fts/config/env.{default,prod-live}.toml`,
  `ledger/config/{default,prod-live}.toml`, `cfa/config/{default,prod}.toml`,
  `x-balances/config/{default,prod}.toml`
- `payouts/internal/routing/router/cron_routes.go` (FastCron endpoint inventory)
- `spinacode/v3/payouts/prod/{mum-rspl,mum-rzpx,hyd,mum-dr}/*.json` (Spinnaker pipeline templates,
  canary config)
- `ENV2_COMPOSE/docker-compose.yml`, `ENV2_COMPOSE/config/templates/base/{payouts,fts,ledger,cfa,
  xbalances}/*.toml`, `ENV2_COMPOSE/seeds/localstack/init-queues.sh`, `ENV2_COMPOSE/seeds/s4/
  cron_schedule.yaml`, `ENV2_COMPOSE/scripts/cron-driver/driver.py`
- Prior reports read for correction: `reports/EFFECTIVE_CONFIG_GAPS.md` (§A4, §E3, §L),
  `reports/raw-findings/22_kube_spinnaker_alerts.md` referenced but not re-read in full (time-boxed;
  its claims are covered indirectly by the §L addendum cross-check below)

Not opened (out of lane / time-boxed): `alert-rules` (topology facts only were needed and were
already covered via kube-manifests cron/HPA values), full `devstack`, `INITIAL_PAYOUTS_ENVIRONMENT_BOM.md`
cross-check beyond the cron section, terraform for datastore engine versions (no terraform/RDS/MSK
repo is in the readable set — see §Cannot be derived).

**Key structural fact, CONFIRMED**: `kube-manifests` has two disjoint top-level prod overlays.
`cde/` (`kube-manifests/cde/`) is the RBI/PCI-segregated cluster and contains only `payouts`, `cfa`,
`mozart` (+ `mozart-dark/-pagination/-test/-whitelisted` variants), and `batch-clearing`. `prod/`
(`kube-manifests/prod/`) is the non-CDE cluster and contains `fts`, `ledger`, `dcs`, `splitz`,
`stork`, `workflows`, `x-account-statements`, `x-balances`, `batch`, `banking-account`. `payouts`
and `cfa` do **not** appear under `prod/`; `fts`/`ledger`/etc. do **not** appear under `cde/`. This
is a real network-segmentation boundary, not an artifact of naming — CONFIRMED
(`kube-manifests/cde/` vs `kube-manifests/prod/` directory listings).

Also CONFIRMED: the k8s namespace/deployment name is **`banking-account`** (singular) while its
container image is **`banking-accounts`** (plural) — `kube-manifests/prod/banking-account/values.yaml:3,31`
(`image_tag: banking-accounts:...`, `namespace: banking-account`). The lane brief's "banking-accounts"
spelling matches the image, not the k8s object name.

---

## Production behaviour

### 1. Runtime topology — production (cde/prod)

#### payouts (`kube-manifests/cde/payouts/values.yaml`, `templates/payouts/templates/*.yaml`)

CONFIRMED. Namespace `payouts`, IAM role `cde-payouts` (service-account `payouts`, IRSA arn
`arn:aws:iam::141592612890:role/cde-payouts`) — `cde/payouts/values.yaml:1,19-21`.

| deployment | args | env selecting role | replicas (live/min-max HPA) | ports | probes |
|---|---|---|---|---|---|
| `payouts` (api) | `[api]` | `APP_MODE`/`APP_ENV`=prod | 10 / 12–25 (`hpa-payouts.yaml`) | 8000 (http), 8001 (prom) | liveness `GET /commit.txt:8000`; readiness `GET /status:8000` |
| `payouts-canary` / `payouts-baseline` | `[api]` | same | 1 / 1 | same | same |
| `payouts-worker-async-dual-write-live` | `[worker, sqs-async-dual-write-live]` | `PAYOUTS_WORKER_NAME=async_dual_write` | 1 / 1–5 | 8000/8001 | same shape |
| `payouts-worker-batch-submitted-merchants-live` | `[worker, sqs-batch-submitted-merchants-live]` | `PAYOUTS_WORKER_NAME=batch_submitted_merchants` | 1 / 1–5 | | |
| `payouts-worker-bulk-payouts-live` | `[worker, sqs-bulk-payouts-live]` | `PAYOUTS_WORKER_NAME=bulk_payouts` | 1 / 1–5 | | |
| `payouts-worker-data-consistency-checker-processing-live` | `[worker, sqs-data-consistency-checker-processing-live]` | `PAYOUTS_WORKER_NAME=data_consistency_checker` | 5 / 5–12 | | |
| `payouts-worker-data-consistency-event-processing-live` | `[worker, sqs-data-consistency-event-processing-live]` | `PAYOUTS_WORKER_NAME=data_consistency_event` | 1 / 1–5 | | |
| `payouts-worker-fmp-check-live` | `[worker, sqs-fmp-check-live]` | `PAYOUTS_WORKER_NAME=fund_management_payout_check` | 1 / 1–3 | | |
| `payouts-worker-fmp-initiate-live` | `[worker, sqs-fmp-initiate-live]` | `PAYOUTS_WORKER_NAME=fund_management_payout_initiate` | 1 / 1–3 | | |
| `payouts-worker-fts-async-hv-processing-live` | `[worker, sqs-fts-async-hv-processing-live]` | `PAYOUTS_WORKER_NAME=fts_async_hv_processing` | 10 / 10–20 | | |
| `payouts-worker-fts-async-processing-live` | `[worker, sqs-fts-async-processing-live]` | `PAYOUTS_WORKER_NAME=fts_async_processing` | 30 / 30–50 | | |
| `payouts-worker-generic-processing-live` | `[worker, sqs-generic-processing-live]` | `PAYOUTS_WORKER_NAME=generic_processing` | 1 / 1–5 | | |
| `payouts-worker-mail-and-sms-event-live` | `[worker, sqs-mail-and-sms-event-live]` | `PAYOUTS_WORKER_NAME=mail_and_sms_event` | 0 / 1–5 | | (scaled to 0 live replicas but HPA min=1 — likely min enforced by HPA at runtime; static `replicas:0` in values is the last-applied baseline) |
| `payouts-worker-on-hold-processing-live` | `[worker, sqs-on-hold-processing-live]` | `PAYOUTS_WORKER_NAME=on_hold_payout` | 1 / 1–5 | | |
| `payouts-worker-partner-bank-hold-payouts-live` | `[worker, sqs-partner-bank-hold-payouts-live]` | `PAYOUTS_WORKER_NAME=partner_bank_hold_payouts` | 1 / 1–5 | | |
| `payouts-worker-payout-create-failure-handlng-live` (sic, repo typo, missing "i") | `[worker, sqs-payout-create-failure-handling-live]` | `PAYOUTS_WORKER_NAME=payout_create_failure_handling` | 1 / 1–5 | | |
| `payouts-worker-payout-update-failure-handlng-live` (same typo) | `[worker, sqs-payout-update-failure-handling-live]` | `PAYOUTS_WORKER_NAME=payout_update_failure_handling` | 1 / 1–5 | | |
| `payouts-worker-payout-usage-event-processing-live` | `[worker, sqs-payout-usage-event-processing-live]` | `PAYOUTS_WORKER_NAME=payout_usage_event_processing` | 2 / 2–5 | | |
| `payouts-worker-queued-processing-live` | `[worker, sqs-queued-processing-live]` | `PAYOUTS_WORKER_NAME=queued_payout` | 1 / 1–5 | | |
| `payouts-worker-rbl-account-statement-live` | `[worker, sqs-rbl-banking-account-statement-live]` | `PAYOUTS_WORKER_NAME=rbl_banking_account_statement` | 1 / 1–8 | | |
| `payouts-worker-schedule-processing-live` | `[worker, sqs-schedule-processing-live]` | `PAYOUTS_WORKER_NAME=schedule_payout` | 1 / 1–5 | | |
| `payouts-worker-source-updater-live` | `[worker, sqs-source-updater-live]` | `PAYOUTS_WORKER_NAME=payout_source_updater` | 0 / — | | (no min/max HPA row found for this one; `payouts_worker_source_updater_live_replicas: 0`, `cde/payouts/values.yaml:96`) |
| `payouts-worker-transaction-create-live` | `[worker, sqs-transaction-create-live]` | `PAYOUTS_WORKER_NAME=transaction_create` | 10 / 10–20 | | |
| `payouts-worker-update-source-event-live` | `[worker, sqs-update-source-event-live]` | `PAYOUTS_WORKER_NAME=update_source_event` | 0 / 1–5 | | |
| `payouts-worker-webhook-event-live` | `[worker, sqs-webhook-event-live]` | `PAYOUTS_WORKER_NAME=webhook_event` | 1 / 1–5 | | |
| `payouts-worker-x-balances-balance-refresh-live` | `[worker, sqs-x-balances-balance-refresh-live]` | `PAYOUTS_WORKER_NAME=x_balances_balance_refresh` | 2 / 2–5 | | |
| `payouts-kafka-fts-status-updates-consumer` | (kafka consumer) | `PAYOUTS_CONSUMER_TASK_NAME` env present (CONFIRMED, `templates/payouts/templates/payouts-kafka-fts-status-updates-consumer.yaml:73`) | 1 / 1–5 | | |
| `payouts-kafka-fts-status-updates-retry-consumer` | (kafka consumer) | `PAYOUTS_CONSUMER_TASK_NAME` | 1 / 1–5 | | |

That is **24 worker/consumer Deployments + 1 api Deployment (+canary+baseline)** — i.e. ~25 always-on
async processes, CONFIRMING `EFFECTIVE_CONFIG_GAPS.md` §L's "25 worker Deployments" line. All
`payouts-worker-*-live.yaml` files also render `-test` and (except a few) `-canary`/`-baseline`
sibling Deployments in the same manifest via Helm `{{ if .Values.canary_baseline_enable }}` blocks —
CONFIRMED pattern from `templates/payouts/templates/payouts-worker-async-dual-write-live.yaml:1-135`
and `payouts-deployment.yaml:128-384`. Secret: all containers `envFrom: secretRef name: payouts`
(`payouts-deployment.yaml:84-86`, same for every worker file). ConfigMap: none found for payouts —
config is baked into the image (`payouts/config/prod.toml`) + `secretRef: payouts` for env overlays
+ two `fieldRef` node-name envs (`JAEGER_HOSTNAME`, `PAYOUTS_TELEMETRY_EXPORTERHOST`). Volumes: only
a `hostPath` trace-log mount per worker (`/var/log/fluentd/{namespace}/{worker}/trace`),
`payouts-worker-async-dual-write-live.yaml:130-134`. Ingress: `traefik-internal` class,
`payouts.razorpay.com` (internal) / `payouts-ext.razorpay.com` (external, only `/v1/cron/*` and
`/commit.txt` exposed) — `cde/payouts/values.yaml:12-15`, `templates/payouts/templates/ing-v2.yaml`.

**FastCron ingress** (CONFIRMED, IPs omitted per instruction — they are FastCron's own published
ranges, not Razorpay secrets, but the brief says omit): a Traefik `Middleware`
`payouts-fastcron-ip-whitelist` gates `Host(payouts-ext) && PathPrefix(/v1/cron/)` to FastCron's
source IPs, forwarding to a `payouts-edge-redirect` Service on port 8000 —
`templates/payouts/templates/ing-v2.yaml:101-135`. This is the only externally-triggered entry into
the ~14 `/v1/cron/*` routes below; it is **not** a k8s CronJob (none found under
`kube-manifests/{cde,prod}/payouts` — the whole "cron" concept for payouts is HTTP-driven from
outside k8s).

**payouts `/v1/cron/*` route inventory** (CONFIRMED, `payouts/internal/routing/router/cron_routes.go:11-176`,
group `/v1/cron`, middleware `BasicAuth(cred.FastCron)`):
`redis_key_set`, `banking_account_statement/fetch/initiate`, `process_queued_payouts`,
`process_batch_submitted_payouts`, `process_beneficiary_bank_on_hold_payouts`,
`process_queued_low_balance_payouts`, `process_inflight_reservation_reconciliation`,
`process_scheduled_payouts`, `payouts_dual_write_failure_processing`, `reverse_dual_write`,
`load_test/run`, `merchant_configuration` (POST/PUT), `payouts_sla_breach_monitor`, `query_db`,
`tidb_data_consistency_check`, `elasticsearch/backfill_index`, `payouts/fetch_multiple` (GET),
`elasticsearch/query`, `elasticsearch/mapping` (GET/PUT), `log_sampling` (GET/PUT),
`fund_management_payouts/check`. 21 routes total, all POST unless noted. Real cadence for each is
FastCron-dashboard-only (SaaS UI, no API/export found in any cloned repo) — UNKNOWN, see
§Cannot be derived.

#### fts (`kube-manifests/prod/fts/values.yaml`, `templates/fts/templates/*.yaml`)

CONFIRMED. Namespace `fts`, service-account `fts-service-account`
(`prod/fts/values.yaml:1,509,517`). Web: `fts-live` Deployment, args `[web]`, `APP_MODE=live`,
`APP_ENV={{.Values.app_env}}` = `prod`, image `fts:{{.Values.fts_image}}` = `fts:730d8b74…`, 11
replicas (`prod/fts/values.yaml:6,8`), prom port 8081, liveness `GET /status:80`
(`templates/fts/templates/fts-live-deployment.yaml:1-80`).

FTS fans out **one Deployment per bank/rail/direction combination**, not one generic worker pool:
**157** `fts-live-worker-*-deployment.yaml` files (plus 1 `fts-live-default-worker-deployment.yaml`
and 6 `fts-test-worker-*` files) — CONFIRMED count via `ls templates/fts/templates | grep -c`. Each
file renders 3 Deployment objects in one manifest (live / canary / baseline — confirmed by 3
occurrences of `args:` at lines ~46/167/291 in every sampled file, e.g.
`fts-live-worker-rbl-direct-imps-check-transfer-status-deployment.yaml`). Role is selected purely
by a **positional CLI arg**, not an env var: `args: [worker, <queue-key>]`, e.g. `[worker, default]`,
`[worker, check_transfer_status]`, `[worker, rbl::check_transfer_status]`,
`[worker, rbl::direct::imps::check_transfer_status]`, `[worker, retry_transfer_preprocessor]`. Banks
observed with dedicated worker fan-out: axis, icici, idfc, yesbank, rbl (+ rbl direct/v4/v5
variants), slice (+v1), amazon-pay, ocbc, hdfc (bulk), batch-icici-neft. Per-queue-key concurrency
is set in application config, not k8s: `fts/config/env.prod-live.toml:45-127`
`[queue.worker_concurrency_map]` maps each `<queue-key>` string (identical to the CLI arg) to an
integer concurrency, e.g. `"icici::initiate_transfer" = 45`, `"rbl::v4::init_and_check_status" = 29`,
`"axis::initiate_transfer" = 1`, default fallback `"default" = 5`. Replica counts per worker are in
`prod/fts/values.yaml` as `fts_live_worker_<key>_replicas` (e.g. `fts_live_worker_fire_transfer_status_webhook_replicas: 7`,
`fts_live_worker_rbl_v4_init_and_check_status_replicas: 4`) with matching `_min_replicas`/`_max_replicas`
HPA pairs (`prod/fts/values.yaml:530-719` sampled). Ports/probes match the default worker pattern
(prom 8081; liveness/readiness not fully re-verified per-file but the default worker template is
shared).

#### ledger (`kube-manifests/prod/ledger/values.yaml`, `templates/ledger/templates/*.yaml`)

CONFIRMED. Namespace `ledger`, service-account `ledger-service-account`
(`prod/ledger/values.yaml:3,605`). Image tag `2d4a1cf5729219cce09dc5163336b8d1adf6b40a`
(`prod/ledger/values.yaml:20`). `ledger_node_selector: node.kubernetes.io/worker-platform-multiarch`
— ledger runs on a shared multi-tenant platform node pool (not payouts-dedicated), consistent with
ledger being a platform-wide accounting service, not payouts-only.

Role selection mechanism differs from payouts/fts: **no positional CLI arg**; role is the container
image (`ledger:worker-<tag>` for all workers, plain `ledger:<tag>`(-ish) for the API) plus env var
`LEDGER_WORKER_QUEUENAME` (SQS queue name) and `LEDGER_WORKER_MAXCONCURRENCY` — CONFIRMED
`templates/ledger/templates/balance-update-live-worker.yaml:57-65`. Every worker Deployment renders
**4 variants** in one file: live, live-baseline, live-canary, (+live-mirror if
`.Values.mirroring_enable`) — same file, lines 1-547.

| deployment (live) | env role selector | replicas | queue name (`[job]`, `ledger/config/prod-live.toml:393-399`) |
|---|---|---|---|
| `ledger` (web/api) | `APP_MODE=live`,`APP_ENV=prod-live` | — (see `deployment-live.yaml`; readiness `exec sh /app/probe.sh`, liveness `GET /commit.txt:8080`) | — |
| `ledger-account-create-worker-live` | `LEDGER_WORKER_QUEUENAME=account_create_queue_name_live` | 3 (`prod/ledger/values.yaml:54`) | `prod-ledger-account-create-live` |
| `ledger-balance-update-worker-live` | `LEDGER_WORKER_QUEUENAME=balance_update_queue_name_live` | 3 (`:67`) | `prod-ledger-balance-update-live` |
| `ledger-journal-create-worker-live` | `LEDGER_WORKER_QUEUENAME=journal_create_queue_name_live` | 2 (`:41`) | `prod-ledger-journal-create-live` |
| `ledger-entry-details-create-worker-live` (+`-pg` variant) | same pattern | — (not individually re-verified) | `prod-ledger-entry-details-create-live` / `-pg-live` |
| `ledger-makeshift-txn-worker` (per-product, kafka-driven) | Kafka `queue_name` per product | — | `makeshift_txn_cls_<product>_pg_kafka` (13+ product tenants: cb_import, growth, cps, ups, pcp, optimizer, sync_web, settlements, api, nbs, route, affordability, emandate, bts, dispute_service, offers, apm — `prod/ledger/values.yaml:612-1426`, DLQ topics `prod_ledger_makeshift_txn_{live,test}_cls_ack_dlq` / `..._event_stream_dlq`) |
| `journal-create-<product>-pg-*-worker` (15 products: affordability, api, apm, bts, cb-import, cps, dsp-svc, emandate, growth, nbs, offers, optimizer-core, pcp, route, settlements, ups) | separate PG-tenant journal queues | — | one queue per product, not payouts-specific |

Only `account_create`, `balance_update`, `journal_create`, `ledger_entry_details_create` (+ `_pg`
siblings) and the makeshift/journal-create-per-product fan-out are payouts-adjacent (payouts writes
ledger entries as one of many tenants sharing this platform). The 15 `journal-create-<product>-pg-*`
workers are **other products' tenants** on the same shared ledger service, not payouts-specific —
correctly out of scope for a payouts-focused twin, but worth noting ledger is a genuinely
multi-tenant platform, not a payouts-only microservice.

**CronJobs** (CONFIRMED, `templates/ledger/templates/*-cronjob-*.yaml`):
`ledger-split-account-balance-update-live` and `-pg-live`, schedule
`split_account_balance_update_cronjob_schedule = "*/10 * * * *"` (`prod/ledger/values.yaml:610`),
image `c.rzp.io/razorpay/ledger:scheduler-{{.Values.ledger_image_tag}}` — CONFIRMED,
`templates/ledger/templates/split-account-balance-update-cronjob-live.yaml:65`. This **is** the
"ledger-scheduler" role the twin names (`ledger-api/worker/scheduler`) — in production it is a k8s
**CronJob** (runs to completion every 10 min), not a long-running Deployment; if the twin runs it as
an always-on service that is a MECHANISM difference (see Twin comparison). Also
`ledger-verify-warm-storage-live`/`-test`, schedule `"0 * * * *"` (hourly, hardcoded, not a
`.Values` var) — `verify-warm-storage-cronjob-live.yaml:14`.

#### cfa (`kube-manifests/cde/cfa/values.yaml`, `templates/cfa/templates/*.yaml`)

CONFIRMED. In the **CDE** cluster (unlike fts/ledger). Web: 3 deployments from a `web_deployments`
loop — `web` (8 replicas), `web-canary` (1), `web-baseline` (1) —
`cde/cfa/values.yaml:19-42`, HPA min 8/max 10 @ 65% CPU (`:19-22`). Workers: a `workers` range loop
(`templates/cfa/templates/deployment-worker.yaml:1-109`) producing exactly 2 Deployments:

| deployment | env | replicas | HPA |
|---|---|---|---|
| `cfa-worker-fa` | `CFA_WORKER_QUEUE_NAME=prod-api-rx-fund-account-lazy-loading-live` | 1 | 1–3 |
| `cfa-worker-contact` | `CFA_WORKER_QUEUE_NAME=prod-api-rx-contact-lazy-loading-live` | 1 | 1–3 |

`cde/cfa/values.yaml:44-67`. This is an exact structural match to the twin's `cfa-server/
worker-contact/worker-fa` naming. CronJobs: `crons: []` — **currently disabled**, commented-out
example present but inert (`cde/cfa/values.yaml:69-71`, `templates/cfa/templates/cron.yaml`
supports them generically via a `crons` range but nothing is enabled today).

#### x-balances (`kube-manifests/prod/x-balances/values.yaml`, `templates/x-balances/templates/*.yaml`)

CONFIRMED. **Not** in CDE (runs in `prod/`). Web: `web`(4)/`web-canary`(1)/`web-baseline`(1)
(`prod/x-balances/values.yaml:28-43`). Workers: same range-loop pattern as cfa
(`templates/x-balances/templates/deployment-worker.yaml:1-106`), but here it fans out **per bank**,
not per logical queue — `x-balances-worker-rbl-balance-fetch` (8, HPA 8–20),
`x-balances-worker-icici-balance-fetch` (1, HPA 2–8), `x-balances-worker-yesbank-balance-fetch` (1,
HPA 4–8), `x-balances-worker-axis-balance-fetch` (1, HPA 2–8), `x-balances-worker-idfc-balance-fetch`
(1, …) — each with `-canary`/`-baseline` siblings, most set to 0 replicas
(`prod/x-balances/values.yaml:45-236+`). Ports (from `x-balances/config/{default,prod}.toml`):
Grpc `:8080`, Http `:8081`, Internal `:8082` (`x-balances/config/prod.toml:38-43`). This 5-bank
fan-out is **not** modeled by the twin's single `xbalances-worker`.

#### stork (`kube-manifests/prod/stork/values.yaml`, `templates/stork/templates/*.yaml`)

CONFIRMED. Web `stork-api` (min 10/max 72 HPA, `prod/stork/values.yaml:7-8`), plus two
independently-scaled worker families each with their own canary/baseline/dlq/p1/p2 variants:
`stork-sms-worker` (4 replicas base) and `stork-webhook-worker` (10 base, `-p1` variant also 10) —
`prod/stork/values.yaml:23-103` (sampled). CronJobs (`templates/stork/templates/cronjobs.yaml`):
`scheduler_cron_schedule = "*/3 * * * *"`, `webhook_disabler_cron_schedule = "*/30 * * * *"`
(`prod/stork/values.yaml:422,424`); a third `partitioner_cron_schedule` cron is templated
(`cronjobs.yaml:189`) but its schedule value was not found set in `prod/stork/values.yaml` in the
lines inspected — INFERRED it may be defined elsewhere in the same file (not confirmed) or the
CronJob is suspended by default; flagged rather than guessed.

#### mozart (`kube-manifests/cde/mozart/values.yaml`, `templates/mozart/templates/deployment.yaml`)

CONFIRMED. In CDE. Web args `[web]` (70 replicas, min 50/max 120 HPA), canary/baseline (7/7).
Worker args `[worker]` but **`mozart_worker_replicas: 0`** in the CDE prod overlay
(`cde/mozart/values.yaml:1-44`) — the mozart worker Deployment is defined but currently scaled to
zero in production. Image tag is a **Spinnaker pipeline parameter placeholder**, not a literal
commit SHA: `mozart_image_tag: c.rzp.io/razorpay/mozart:mozart-${parameters.mozart_prod_commit_id}`
(`cde/mozart/values.yaml:20`) — CONFIRMED direct evidence that image tags for at least this service
are resolved at Spinnaker bake/deploy time, not committed to `kube-manifests` as a literal (see
§Spinnaker/spinacode below).

#### dcs (`kube-manifests/templates/dcs/templates/*.yaml`, values under `prod/dcs`)

CONFIRMED. Two distinct images: `dcs-server` (the query-serving API, `deployment-live.yaml`) and
`dcs-consumer` (`dual-write-consumer.yaml`), plus a separate `dcs-dynamo-cdc-worker.yaml` with its
own image tags (`dcs_dynamo_cdc_live_image_tag` / `..._test_image_tag`) and its own env prefix
(`CDC_APP_MODE`/`CDC_APP_ENV` rather than `APP_MODE`/`APP_ENV`). `deployment-live.yaml` renders
**6** Deployment variants — `dcs-live`, `dcs-live-v2-proxy`, `dcs-live-v2-proxy-baseline`,
`dcs-live-v2-proxy-canary`, `dcs-live-baseline`, (and by pattern `dcs-live-canary`) — evidencing an
in-flight "v2 proxy" migration behind `DCS_ENABLE_V2_PROXY` (present as an env var on the v2-proxy
variants only, `templates/dcs/templates/deployment-live.yaml:218,341,463`). Ports 8081/8082
throughout.

#### splitz (`kube-manifests/prod/splitz/values.yaml`, `templates/splitz/templates/*.yaml`)

CONFIRMED. Web (50 live/10 canary/10 baseline/0 shadow, HPA 15–100,
`prod/splitz/values.yaml:10-43`), `splitz-worker` (2 replicas, `:53`), and a third role
**`splitz-mcp`** (1 replica, port **9400** — `templates/splitz/templates/splitz-mcp.yaml:57,97`,
`prod/splitz/values.yaml:60`) — Splitz exposes a Model-Context-Protocol server in production. This
third role is entirely absent from every existing report and from the twin.

#### workflows (`kube-manifests/prod/workflows/values.yaml`, `templates/workflows/templates/deployment.yaml`)

CONFIRMED. Three distinct images/roles in one `deployment.yaml`: `workflows` web (5 replicas, HPA
5–25), `worker` (5 replicas, HPA 10–40), `worker-asl` (2 replicas, HPA 2–6) — the ASL ("Amazon
States Language") worker is a distinct image tag (`worker_asl_image_tag`) from the plain `worker`,
implying a Step-Functions-style state-machine execution path separate from the generic worker
(`prod/workflows/values.yaml:7-42`).

#### batch (`kube-manifests/templates/batch/templates/*.yaml`)

CONFIRMED. `deployment-batch-web.yaml` (args `[web]`), `deployment-batch-sqs.yaml` (args `[sqs]`,
generic), `deployment-batch-ingress-sqs.yaml` (args `[ingress-sqs]`),
`deployment-batch-sqs-payment-links.yaml` (`[sqs-payment-links]`),
`deployment-batch-sqs-reconciliation.yaml` (`[sqs-reconciliation]`),
`deployment-batch-sqs-sftp-notification.yaml` (`[sqs-sftp-notification]`). **Repo naming oddity,
CONFIRMED**: `deployment-batch-sqs-art-dual-write.yaml` actually passes args `[sqs-prs]`, and
`deployment-batch-sqs-art-prs.yaml` actually passes args `[sqs-dualWrite]` — the file names for
these two are swapped relative to their args (`kube-manifests/templates/batch/templates/
deployment-batch-sqs-art-dual-write.yaml:60-61` vs `deployment-batch-sqs-art-prs.yaml:60-61`). CronJob:
`batch_shutdown_recovery_cron_schedule = "*/15 * * * *"` (`prod/batch/values.yaml:83`). None of
batch's queues are payouts-specific by name (art/prs/payment-links/reconciliation/sftp are
settlements/reconciliation domains); batch is listed in the lane brief but has no direct
payouts-named worker — INFERRED batch's payouts relevance is indirect (shared platform, possibly
touched by bulk-payout CSV ingestion) and not confirmed from these manifests alone.

#### banking-account (`kube-manifests/prod/banking-account/values.yaml`)

CONFIRMED. `banking-account` (2 replicas) + `-baseline`(1)/`-canary`(1)
(`prod/banking-account/values.yaml:17-23`). Image `banking-accounts:4b878ba0…` (`:3`) — see naming
note above.

#### x-account-statements (`kube-manifests/prod/x-account-statements/values.yaml`)

CONFIRMED. Web 2/canary 1/baseline 1. Workers (range loop, env
`X-ACCOUNT-STATEMENTS_WORKER_QUEUE_NAME`): `worker-fetch`(4, HPA 4–8), `worker-ybl-fetch`(1, HPA
1–2), `worker-statement-save`(2, HPA 2–4), `worker-enrichment`(2, HPA 2–4),
`worker-source-event`(2, HPA 2–4), `worker-statement-cdc`(10, HPA 10–15),
`worker-accounts-cdc`(10, HPA 10–…) — `prod/x-account-statements/values.yaml:42-132+`. 7 distinct
worker roles vs the twin's implicit single xas-sink substitute.

#### `api` (legacy monolith) — payouts-relevant deployments only

CONFIRMED, and this is the single largest topology gap vs the twin (see Twin comparison §1). The
legacy `api` PHP/monolith still runs **~45** distinct SQS/Kafka Deployments whose queue names are
payout/fts/cfa/x-balances/banking-account related
(`kube-manifests/templates/api/templates/workers/*.yaml`, file-name-derived list, sampled args
confirmed for two): `api-sqs-payouts-live`, `api-sqs-payouts-test`,
`api-sqs-payout-service-dual-write-live`, `api-sqs-payout-service-dual-write-direct-push-live`,
`api-sqs-approved-payout-distribution-{live,test}`, `api-sqs-approved-payout-processor-{live,test}`,
`api-sqs-batch-payouts-process-{live,test}`, `api-sqs-on-hold-payouts-process-{live,test}`,
`api-sqs-partner-bank-on-hold-payouts-process-{live,test}`,
`api-sqs-partner-bank-health-notify-{live,test}`, `api-sqs-queued-payouts-initiate-{live,test}`,
`api-sqs-scheduled-payouts-process-{live,test}`,
`api-sqs-payout-post-create-process-{live,test}` (+`-low-priority-{live,test}`),
`api-sqs-payout-source-updater-{live,test}`, `api-sqs-payouts-auto-expire-{live,test}`,
`api-sqs-payout-attachment-email-{live,test}`, `api-sqs-fund-management-payout-check-live`,
`api-sqs-fund-management-payout-initiate-live`, `api-sqs-fts-{live,test}`,
`api-sqs-fav-queue-for-fts-{live,test}`, `api-sqs-cfa-contact-dual-write-live`,
`api-sqs-cfa-fund-account-dual-write-live`, `api-sqs-x-balance-dual-write-live`,
`api-sqs-account-statement-dual-write-live`, `api-sqs-banking-account-statement-processor-live`,
`api-sqs-banking-account-statement-recon-live`, `api-sqs-banking-account-statement-source-linking-live`,
`api-sqs-rbl-banking-account-statement-{live,test}`,
`api-sqs-rbl-banking-account-statement-fetch-live`,
`api-sqs-rbl-banking-account-gateway-balance-update-{live,test}`,
`api-sqs-rbl-create-virtual-account-{live,test}`, `api-sqs-icici-banking-account-statement-fetch-live`,
`api-sqs-icici-banking-account-gateway-balance-update-live`,
`api-sqs-connected-banking-account-gateway-balance-update-live`,
`api-kafka-fts-status-update-consumer`, `api-kafka-fts-status-update-retry-consumer`,
`api-kafka-consumer-pg-ledger-{live,test}`. Confirmed args shape for two of these
(`templates/api/templates/workers/api-sqs-payouts-live.yaml:60-90`,
`api-sqs-payout-service-dual-write-live.yaml:60-90`): `args: [sqs_multi_default, <queue-name>,
"10"]` — a Laravel-artisan-style command taking the queue name and a concurrency literal as
positional args, distinct from every other service's role-selection mechanism in this lane. Readiness
here is an **exec file-touch probe** (`stat /app/ready`), with a `preStop` hook doing `rm -f
/app/ready` — a different probe idiom again (`api-sqs-payouts-live.yaml:82-91`).

### 2. Effective configuration — five core services

All five load config with a base + env-specific TOML merge; the k8s Deployment sets only
`APP_MODE`/`APP_ENV` (selects which TOML) plus `envFrom: secretRef` (injects `env|VAR` placeholders)
and two `fieldRef` node-name envs for tracing. **Mounted file, CONFIRMED from `APP_ENV` value +
Deployment `image`/env**:

| service | prod file(s) actually merged | selector |
|---|---|---|
| payouts | `config/default.toml` + `config/prod.toml` | `APP_MODE=prod` (`cde/payouts/values.yaml:2`) |
| fts | `config/env.default.toml` + `config/env.prod-live.toml` | `APP_MODE=live`,`APP_ENV=prod` → `env.{APP_ENV}-{APP_MODE}.toml` naming convention (INFERRED from file name matching `env.prod-live.toml`; loader code not read in this pass) |
| ledger | `config/default.toml` + `config/prod-live.toml` | `APP_ENV=prod`,`APP_MODE=live` → same convention |
| cfa | `config/default.toml` + `config/prod.toml` | `APP_ENV=prod` (`cde/cfa/values.yaml` `$.Values.env`) |
| x-balances | `config/default.toml` + `config/prod.toml` | `APP_ENV=prod` |

No ConfigMap was found for any of the five (`envFrom` is `secretRef` only in every Deployment file
read); config is baked into the image at build time and only secret-shaped values are injected at
runtime via `secretRef`. This CONFIRMS `EFFECTIVE_CONFIG_GAPS.md` §L's implicit assumption but makes
it explicit: **there is no ConfigMap layer for these five services** — everything non-secret in
`prod.toml`/`prod-live.toml` is a literal in the repo.

#### payouts (`payouts/config/prod.toml`, full section list read)

Key behavior-affecting values, all CONFIRMED with line numbers:

| key | value | note |
|---|---|---|
| `worker.maxconcurrency` / `waittime` / `retrydelay` | `2` / `1s` / `30s` (`:159-161`) | global default for **every** `payouts-worker-*` process (no per-worker override found in k8s env; only `PAYOUTS_WORKER_NAME` varies) |
| `consumer_task.MaxConcurrency` | `3` (`:346`) | kafka consumer concurrency |
| `kafka_consumers.status_update_consumer.config` | `RetryBackoff=1, MaxRetry=1, ConsumerGroup=rx-payouts-fts-status-update-consumer-group, Topics=[rx-fts-status-update-events], Brokers=[prod-noncde-kafka.razorpay.com:9090], EnableTLS=true` (`:313-325`) | |
| `kafka_consumers.status_update_retry_consumer.config` | `RetryBackoff=30, MaxRetry=2, ConsumerGroup=rx-payouts-fts-status-update-retry-consumer-group, Topics=[rx-fts-status-update-retry-events]` (`:326-338`) | |
| `kafka_producer` | `RetryBackoff=1, MaxRetry=10, MaxMessages=100, CompressionType=snappy, CompressionEnabled=true` (`:348-360`) | |
| `queue.driver` / `queue.sqs.region` / `prefix` | `sqs` / `ap-south-1` / `https://sqs.ap-south-1.amazonaws.com/141592612890/` (`:151-155`) | no `visibilityTimeout` key here — set on the AWS queue resource, not app config (Terraform, not in readable repos) |
| `fts.timeout` / `httpretryattempts` / resiliency | `1000ms` / `3` / `maxconcurrentrequests=100, circuitbreakersleepwindow=5000, errorpercentthreshold=50, circuitbreakertimeout=10000` (`:590-609`) | |
| `ledger.timeout` | `200ms`, `httpretryattempts=3` (`:390-406`) | |
| `mozart.timeout` | `1000ms` (`:234-250`) | |
| `cfa.timeout` | `10000ms` (`:691-710`) | notably 10s, much higher than the other client timeouts |
| `x_balances.timeout` | `30s` (`:773-792`) | |
| `banking_account_service.timeout` / retries | `2000ms` / `httpretryattempts=2, httpretrywindow=60ms` (`:252-271`) | |
| `workflow.timeout` | `100ms`, `httpretryattempts=3` (`:273-290`) | very tight timeout for the workflow-approval client |
| `job.*` | 24 named SQS queues, all prefixed `prod-payouts-*-live` or `prod-api-*-live`/`prod-x-balances-*` (`:163-188`, full list — see §3 Queues) | |
| `splitz.cache_ttl_minutes` / `experiment_cache_size_mb` / `segment_cache_size_mb` | `5` / `5` / `20` (`:442-445`) | |
| `splitz_experiment_list` | 38 named experiment IDs (`:458-495`) | feature-toggle surface, not a static boolean |
| `fmp.initiate_disabled` | `false` (kill switch, `:841`) | |
| `fmp.gateway_balance_threshold` | `1000000` (paisa = ₹10,000) (`:843`) | |
| `fmp.retrieval_threshold` | `21600` (6h, seconds) (`:845`) | |
| `payout_config.in_flight_reservation.ttl` | `6h`, `debit_visibility_lag_default=3m`, `max_items_per_balance=2000` (`:850-855`) | |
| `shadow_gateway.enabled` | `false` (`:860`) — pre-wired proxy-to-monolith gateway, disabled by config flag only | |
| `api_internal_ingress_feature.enable` | `true` (`:432`) | |

vs twin `ENV2_COMPOSE/config/templates/base/payouts/arena.toml` (1116 lines, same section set,
`:1-1116`): job-key set matches prod **except** two prod keys are absent from the twin's `[job]`
section — `rbl_banking_account_statement` and `x_account_statement_source_event` (prod
`payouts/config/prod.toml:177,188`; twin `arena.toml:397-421` has no such keys) — this tracks the
twin's own compose service list (no `rbl-account-statement` or xas-source-event worker exists in the
67-service list either). `[worker]` block values match exactly (`maxconcurrency=2, waittime=1s,
retrydelay=30s`, `arena.toml:1088-1092` vs prod `:157-161`). `[fts]` client block matches exactly
(`arena.toml:1095-1114` vs prod `:590-609`) except host (`fts-web:8080` vs `fts-live.razorpay.com`,
expected). `[kafka_consumers].status_update_retry_consumer.config.RetryBackoff` **differs**: prod
`30` vs arena `5` (`arena.toml:1065` vs prod `:329`) — a real behavior-affecting deviation (5x
faster retry backoff in the twin), likely intentional for fast local iteration but not
contract-faithful if timing-sensitive retry tests are run against it.

#### fts (`fts/config/env.prod-live.toml`, full file read, 469 lines)

CONFIRMED, all with line numbers:

| key | value |
|---|---|
| `queue.worker_concurrency_map` | ~90 entries, per-bank-per-rail (see §1 fts topology; sample: `icici::initiate_transfer=45`, `yesbank::initiate_transfer=30`, `rbl::v4::init_and_check_status=29`, default `5`) (`:45-127`) |
| `webhook.*` | 11 distinct webhook targets, each `TIMEOUT=120` except `webhook.customer_payout.transfer_status.TIMEOUT=2` (2 **seconds**, an outlier) (`:164-235`) |
| `retry_transfer_preprocessor.max_retry_count` / `retry_delay_in_secs` / `max_counter_skips` | `3` / `360` / `3` (`:415-417`) |
| `retry_transfer_preprocessor.status_update` | `INTERVAL=360, COUNT=3` (`:444-446`) |
| `kafka_producers.fire_transfer_status.conf` | `topic=rx-fts-status-update-events, retry_backoff=2, max_retry=10, brokers=[prod-noncde-kafka.razorpay.com:9090]` (`:315-331`) |
| `splitz.experiments` | 30 named experiment IDs incl. `stuck_payouts_experiment`, `neft_24_7_queuing_feature`, `slice_v2_terminal_failure_handling` (`:356-385`) |
| `splitz.feature_flags` | 5 static on/off flags, e.g. `aggressive_mar_trigger=on`, `axis_compliance_remitter_details=on` (`:386-391`) |
| `xas.timeout` | `30000` ms (`:397`) |
| `payouts_service.*` | 4 callback URLs into the payouts microservice (`update_fts_fund_transfer`, `notify_channel_status`, `notify_downtime`, `retry_source_update`), all `TIMEOUT=300`s (`:253-274`) |

`fts/config/env.default.toml` is 10336 lines (not fully read — the merge base, mostly per-bank
integration config not behavior-critical for the twin's contract).

#### ledger (`ledger/config/prod-live.toml`, full file read, 526 lines)

CONFIRMED: `queue.sqs.visibilityTimeout=120` (seconds), `waitTimeout=0`, `enableRetryer=true`, SQS
retryer `numMaxRetries=10, minRetryDelay=500ms, maxRetryDelay=25000ms` (`:353-375`). `worker.waittime=10s,
retrydelay=15s` (queueName/maxconcurrency are env-injected, `:341-345`). `workerRetrier`:
exponential backoff `initialTimeout=2s, maxTimeout=30s, exponentFactor=2` (`:347-351`).
`splitAccount.pgBatchSize=5000, maxLedgerEntryBalanceUpdateAllowed=10000` (`:422-429`). `job.*`: 6
named queues, all `prod-ledger-*-live` (`:393-399`). This is the **only** one of the five core
services whose config states an explicit SQS `visibilityTimeout` — payouts/cfa/x-balances leave it
to the AWS queue resource (Terraform, not in readable repos).

#### cfa (`cfa/config/prod.toml`, full file read, 119 lines)

CONFIRMED: `Job.ContactLazyLoad=prod-api-rx-contact-lazy-loading-live`,
`Job.FundAccountLazyLoad=prod-api-rx-fund-account-lazy-loading-live` (`:50-51`, matching the k8s
`CFA_WORKER_QUEUE_NAME` values exactly). `EventSystem.QueueConfigs` distinguishes `default` (a
different, unqueued path: `contacts`/`fund_accounts`) from `dual_write`
(`prod-api-rx-contact-dual-write-live` / `prod-api-rx-fund-account-dual-write-live`) and `lazy_load`
(`contact-lazy-load`/`fund-account-lazy-load`, short names — a **second, differently-named** pair of
queues alongside `Job.*` for the same concept) (`:56-65`). `Dcs.env=prod, mock=false, timeout=60`
(`:113-118`).

#### x-balances (`x-balances/config/prod.toml`, full file read, 185 lines)

CONFIRMED: `Server.ServerAddresses` — `Grpc=:8080, Http=:8081, Internal=:8082` (`:38-43`).
`BalanceFetch.FetchStrategy=last_attempted_at` (`:141`) — a config-selectable strategy, options
documented in a comment (`last_fetched_at` vs `last_attempted_at`). `MozartConfig.ConnPoolConfig.Timeout=220000`
ms (`:94`) — notably long (220s) vs every other service's client timeouts in this lane. `Cache`/`RateLimiter`
both point at the same Redis host but different `DB` indices (0 and 1, `:126-138`).

### 3. Queues/topics inventory

**Payouts SQS queues** (`payouts/config/prod.toml:163-188`, 24 keys) — all under prefix
`https://sqs.ap-south-1.amazonaws.com/141592612890/`, names follow `prod-payouts-<job>-live` except
`source_updater`/`api_queue_for_async_dual_write_direct_push` (`prod-api-*`) and
`x_balances_payouts_event`/`x_balances_balance_refresh` (`prod-x-balances-*`) and
`x_account_statement_source_event` (`prod-x-account-statement-source-event`). No DLQ names appear in
`payouts/config/prod.toml` — DLQ redrive policy, if any, is set on the AWS queue resource
(Terraform), not app config. **Payouts Kafka**: consumer topics `rx-fts-status-update-events`
(group `rx-payouts-fts-status-update-consumer-group`) and `rx-fts-status-update-retry-events`
(group `rx-payouts-fts-status-update-retry-consumer-group`), broker
`prod-noncde-kafka.razorpay.com:9090`, both TLS (`payouts/config/prod.toml:312-338`). Producer
topic for the same event: implicitly `rx-fts-status-update-events` again (topics section only lists
the retry one explicitly, `:362-363`) — the primary producer topic is INFERRED from the consumer
Topics list + fts's own producer config below, not independently confirmed on the payouts producer
side.

**Ledger SQS**: `prod-ledger-{journal-create,account-create,balance-update,balance-update-pg,
entry-details-create,entry-details-create-pg}-live` (`ledger/config/prod-live.toml:394-399`),
`visibilityTimeout=120s` shared setting (`:359`). **Ledger Kafka/SNS**: `events.kafka.topic=events.ledger.v2.live`
(`:291`); SNS `pubSub.journal_created` → topic `prod-ledger-x-journal-created-live`, ARN account
`141592612890` (`:456-457`); 13+ product-specific `makeshift_txn_cls_<product>_pg_kafka` topics each
with a matching `_dlq` sibling (`prod/ledger/values.yaml:612-1426`, sampled).

**FTS Kafka producer**: topic `rx-fts-status-update-events` (`fts/config/env.prod-live.toml:321`) —
this is the **same topic name** payouts consumes as `status_update_consumer.config.Topics`
(`payouts/config/prod.toml:322`), confirming the fts→payouts status-update path is Kafka, not a
direct HTTP callback, for the async case (the `webhook.*` HTTP callbacks in fts config are a
**separate, synchronous** notification path to the monolith, not to payouts-service directly, except
`payouts_service.update_fts_fund_transfer` which does call payouts directly —
`fts/config/env.prod-live.toml:269-273`). Two channels to the same conceptual event: direct HTTP
(low-latency) and async Kafka (durable/retryable) — CONFIRMED, not previously distinguished this
precisely in the existing reports.

**Twin queue seed** (`ENV2_COMPOSE/seeds/localstack/init-queues.sh:1-40`): 32 SQS queues + 2 SNS
topics (`payout-updates-test-dev`, `journal-created`), all **short, unprefixed names**
(`queued_payout`, `transaction_create`, `payouts-async-dual-write`, `payout-source-updater`) —
CONFIRMED to be exactly the twin's `[job]`/`Job.*` config values (`ENV2_COMPOSE/config/templates/base/
payouts/arena.toml:397-421`), i.e. the twin does not attempt to reproduce prod's `prod-<svc>-<job>-live`
naming convention at all; it substitutes its own short names end-to-end (config + queue creation
agree with each other, just not with prod's literal strings). Two residual **stage-flavored** names
leak into the twin from what was evidently a stage config copy: `stage-x-balances-balance-refresh-live`
and `stage-x-balances-payouts-event` (`init-queues.sh:29-30`, `arena.toml:417-418`) — cosmetic (local
queue name, no functional impact) but worth a one-line fix for hygiene. Missing entirely from the
twin's queue set (vs the 24 prod payouts job keys): `rbl_banking_account_statement`,
`x_account_statement_source_event` — consistent with §1's worker-list gap.

### 4. Cron — full inventory

| mechanism | schedule | source |
|---|---|---|
| payouts external HTTP cron (FastCron, 21 routes under `/v1/cron/*`) | FastCron-dashboard-only, UNKNOWN | `payouts/internal/routing/router/cron_routes.go:11-176`; ingress gate `templates/payouts/templates/ing-v2.yaml:101-135` |
| ledger `split-account-balance-update` (live + pg) | `*/10 * * * *` | `prod/ledger/values.yaml:610` |
| ledger `verify-warm-storage` (live + test) | `0 * * * *` (hourly, hardcoded) | `templates/ledger/templates/verify-warm-storage-cronjob-{live,test}.yaml:14` |
| stork `scheduler` | `*/3 * * * *` | `prod/stork/values.yaml:422` |
| stork `webhook_disabler` | `*/30 * * * *` | `prod/stork/values.yaml:424` |
| stork `partitioner` | schedule value not located in the lines inspected — UNKNOWN | `templates/stork/templates/cronjobs.yaml:189` (templated, value site unconfirmed) |
| cfa cron | none active (`crons: []`) | `cde/cfa/values.yaml:69-71` |
| batch `shutdown_recovery` | `*/15 * * * *` | `prod/batch/values.yaml:83` |
| dcs / splitz / workflows / x-balances / x-account-statements / banking-account | no CronJob manifest found in `templates/{dcs,splitz,workflows,x-balances,x-account-statements,banking-account}/templates/` | absence CONFIRMED by directory listing, not a schedule-value gap |

**Correction to the twin's own cron model**: `ENV2_COMPOSE/seeds/s4/cron_schedule.yaml` states
`stuck_payouts_sla_monitor: driver: internal (SLAMonitor.CheckSLABreaches, in-process not HTTP-cron)`.
Source shows this is **also** externally reachable as an HTTP FastCron route:
`POST /v1/cron/payouts_sla_breach_monitor → controllers.SLAMonitorService.CheckSLABreaches`
(`payouts/internal/routing/router/cron_routes.go:143-151`), registered under the same
`BasicAuth(cred.FastCron)` group as every other cron endpoint. Whether production actually drives it
via FastCron or purely in-process (e.g. a ticker elsewhere in the code) was not independently
re-verified in this pass, but the route's mere existence under `/v1/cron` contradicts a flat "not
HTTP-cron" claim — CORRECTION, not full resolution (see §Corrections).

Also **missing from the twin's `cron-driver` job list** (`ENV2_COMPOSE/scripts/cron-driver/driver.py:34-41`,
6 jobs: `queued_partner_bank`, `queued_low_balance`, `scheduled`, `on_hold`,
`reservation_reconcile`, `dual_write_failure`) vs the 21 real routes above:
`process_batch_submitted_payouts`, `payouts_sla_breach_monitor`, `fund_management_payouts/check`,
`banking_account_statement/fetch/initiate`, `tidb_data_consistency_check`, `reverse_dual_write`,
`redis_key_set`, `merchant_configuration` (GET/PUT), `elasticsearch/*`, `log_sampling`,
`payouts/fetch_multiple`, `query_db`. Most of these are admin/maintenance endpoints rather than
core payout-processing cadence, but `fund_management_payouts/check` (FMP) and
`process_batch_submitted_payouts` (bulk NEFT/RTGS dispatch) are payout-state-affecting and are not
driven by the twin's cron-driver at all.

### 5. Spinnaker/spinacode

CONFIRMED, `spinacode/v3/payouts/prod/{mum-rzpx,mum-rspl,hyd,mum-dr}/*.json` — four region/cell
overlays, each a Spinnaker `templatedPipeline` object whose actual stage logic is **not stored in
this repo**: `deploy-to-a-region.json` has `stages: []` and points at an opaque template by UUID —
`"template": {"reference": "spinnaker://664e59d6-943b-44e7-a3ab-fa5db69705f6:latest", "type":
"front50/pipelineTemplate"}` (`spinacode/v3/payouts/prod/mum-rzpx/deploy-to-a-region.json:17-21`).
The template itself (owned by Spinnaker's Front50 artifact store, referenced by opaque ID, shared
across many services' pipelines) is where bake/deploy/parameter-substitution logic actually lives —
**this is outside every readable repo** (see §Cannot be derived). Two concrete pieces of evidence
that config/image resolution happens at pipeline-parameter-substitution time rather than being
fully literal in `kube-manifests`:
1. `mozart_image_tag` contains a literal `${parameters.mozart_prod_commit_id}` placeholder
   (`kube-manifests/cde/mozart/values.yaml:20`) — Spinnaker interpolates the commit SHA as a pipeline
   variable at bake time, not as a value checked into `kube-manifests`.
2. `deploy-to-a-region.json` variables include `application` and `cellDeploymentPipelineId`
   (`spinacode/v3/payouts/prod/mum-rzpx/deploy-to-a-region.json:23-26`) fed into the opaque template.

Canary judging is automated, not just a static traffic-weight split: `canary-config.json` defines a
`NetflixACAJudge-v1.0` Kayenta config with PromQL metrics against a `_monolith_proxy_` histogram
(e.g. `histogram_quantile(0.95, sum(rate(api_payout_create_duration_monolith_proxy_bucket[1m])) by
(le, service))`) and pass/marginal/critical thresholds
(`spinacode/v3/payouts/prod/mum-rzpx/canary-config.json:1-45`) — CONFIRMED the canary/baseline
Deployments seen throughout §1 feed a real automated-canary-analysis pipeline, not just a manual
weighted rollout.

### 6. Engine versions — production vs twin

**Not derivable from any readable repo**: no terraform/RDS/MSK/ElastiCache infra repo is in the
`REPOS` set (`api authz terraform-kong edge kube-manifests spinacode mozart stork dcs splitz
governor recon proto goutils workflows batch payouts fts ledger cfa x-balances banking-accounts
x-account-statements x-bank-statement-sync ledger-sdk shield-sdk config-proto rpc alert-rules
knowledge-base dashboard frontend-x x admin-dashboard payout-links payments-upi devstack
error-mapping-module go-foundation-v2 python-foundation relay virtual-account vendor-payments` —
`terraform-kong` is Kong-only, no general infra/terraform repo is listed). MySQL is confirmed
`dialect="mysql"` (payouts, ledger apiDb/makeshift, cfa APIStore, x-balances Store/APIStore) and
Postgres for ledger's primary `db.rx`/`db.pg` (`ledger/config/prod-live.toml:20,76`) but no
version number for either engine appears in any application config (connection strings are
`env|...` placeholders). **Twin** (`ENV2_COMPOSE/docker-compose.yml`, grep `image:`): `mysql:8.0`
(×4 instances — payouts/fts/xbalances/one more), `postgres:15-alpine`, `mongo:6.0` (for cfa's
`Store.MongoDB`), `redis:7-alpine`, `localstack/localstack:3.8`, `apache/kafka:3.8.0`. These are
plausible representative choices but **cannot be verified** against real prod engine versions from
any repo read in this lane — flagged as UNKNOWN, not confirmed-matching.

---

## Corrections to existing reports

| report | claim | what source says | evidence |
|---|---|---|---|
| `EFFECTIVE_CONFIG_GAPS.md` §L | "`[kafka_consumers]/[task]/[topics]` live only in `config/devstack.toml`" | These three sections are **present in `payouts/config/prod.toml`** too, with real production values (`prod-noncde-kafka.razorpay.com:9090` broker, real consumer-group names, real topic names) | `payouts/config/prod.toml:312-343,362-363` (sections `[kafka_consumers]`, `[task]`, `[topics]`) |
| `EFFECTIVE_CONFIG_GAPS.md` §L | "plain `sqs` dialect ignores `Endpoint` → real AWS; `sqs_local` honours it" (stated generally, not scoped to one service) | For **payouts**, the vendored SQS broker unconditionally honours `Endpoint` regardless of driver name (`if conf.Endpoint != "" { awsConfig.Endpoint = ... }`); no `sqs_local` driver string exists anywhere in the payouts repo. The `sqs`/`sqs_local` driver-name distinction is a **ledger**-specific config convention (`ledger/config/devstack-live.toml:305,373,392` use `sqs_local`/`sns_local`), not a payouts one. | `payouts/pkg/worker/queue/broker/sqs/queue.go:61-68`; `grep -rl sqs_local payouts` → no hits; `ledger/config/devstack-live.toml:305` |
| `ENV2_COMPOSE/seeds/s4/cron_schedule.yaml` (twin's own doc, treated here as a "report" per lane brief's instruction to verify hypotheses) | `stuck_payouts_sla_monitor` is "internal, in-process not HTTP-cron" | The route `POST /v1/cron/payouts_sla_breach_monitor → SLAMonitorService.CheckSLABreaches` **is** registered under the FastCron-gated `/v1/cron` group in the real router, i.e. it is HTTP-reachable exactly like every other cron endpoint, whatever else may also trigger it in-process | `payouts/internal/routing/router/cron_routes.go:143-151` |
| Lane brief itself | names the service "banking-accounts" | The k8s namespace/Deployment/service name is **`banking-account`** (singular); only the container image tag is `banking-accounts:` (plural) | `kube-manifests/prod/banking-account/values.yaml:3,31` |
| (implicit in twin's job/queue design, `ENV2_COMPOSE/seeds/localstack/init-queues.sh:29-30`) | queue names `stage-x-balances-balance-refresh-live` / `stage-x-balances-payouts-event` | Prod uses `prod-x-balances-balance-refresh-live` / `prod-x-balances-payout-event` (singular "payout", not "payouts") — the twin's names are stage-flavored and slightly misspelled relative to both stage and prod conventions | `payouts/config/prod.toml:184-185` vs `ENV2_COMPOSE/config/templates/base/payouts/arena.toml:417-418` |

---

## Twin comparison

| component | production mechanism | twin mechanism (file) | verdict | what differs | controls |
|---|---|---|---|---|---|
| payouts api | k8s Deployment, args `[api]`, 10 replicas + canary/baseline, `envFrom secretRef payouts` | `payouts-api` compose service, single instance | CONTRACT-FAITHFUL | no canary/baseline/HPA in twin (expected for a single-node arena); replica count irrelevant to contract | routing, auth |
| payouts workers (24 roles) | one Deployment per `PAYOUTS_WORKER_NAME`, k8s HPA per role | 14 of 24 modeled 1:1 by name (see below) | CONTRACT-FAITHFUL for the 14; MISSING for 10 | twin omits `data_consistency_checker`, `data_consistency_event`, `fmp_check`, `fmp_initiate`, `batch_submitted_merchants`, `bulk_payouts`, `mail_and_sms_event`, `payout_usage_event_processing`, `rbl_banking_account_statement`, `update_source_event` | payout/transfer state (fmp_check/initiate directly move CA funds; data-consistency workers detect state drift; these are not cosmetic omissions) |
| payouts kafka consumers | 2 Deployments, `PAYOUTS_CONSUMER_TASK_NAME` env | 2 compose services (`payouts-kafka` ×2) | REAL/CONTRACT-FAITHFUL | matches 1:1 | idempotency of FTS status ingestion |
| fts | 157+1 live worker roles + 1 web, per-bank-per-rail fan-out with per-queue-key concurrency config | `fts-web` + 12 `fts-worker-*` (default, initiate-transfer, check-transfer-status, register-beneficiary, verify-beneficiary, retry-transfer(+preprocessor+source-update), fire-transfer-status-webhook, rbl::{check,direct-imps-check,initiate,direct-imps-initiate}) | REPRESENTATIVE SUBSTITUTE | twin collapses ~157 bank/rail-specific queues into ~12 generic ones; no per-bank concurrency map, no axis/icici/idfc/yesbank/slice/amazon-pay/ocbc-specific workers | routing (bank selection), throughput semantics (concurrency differs wildly per bank in prod, e.g. icici::initiate_transfer=45 vs axis::initiate_transfer=1) |
| ledger | `ledger` web + `account-create`/`balance-update`/`journal-create`/`entry-details-create` workers (each ×4 variants) + `scheduler` **CronJob** (split-account-balance-update, */10) | `ledger-api`/`ledger-worker`/`ledger-scheduler` (naming per lane brief) | CONTRACT-FAITHFUL if scheduler is itself a scheduled job in the twin; INCORRECT if twin's "scheduler" is an always-on process | prod's scheduler role is a **CronJob** (runs-to-completion every 10 min), not a long-running Deployment — mechanism, not just cadence, differs if twin runs it continuously (not independently verified which the twin does; flagged for the twin's own maintainers to check `docker-compose.yml`'s `ledger-scheduler`-equivalent service definition) | split-account balance correctness |
| ledger journal-create-per-product fan-out (15 products) | separate Deployments per non-payouts tenant | not modeled (correctly out of scope) | N/A (out of lane) | — | — |
| cfa | `cfa` web(+canary+baseline) + `cfa-worker-fa`/`cfa-worker-contact`, no active cron | `cfa-server`/`worker-contact`/`worker-fa` | REAL/CONTRACT-FAITHFUL | exact structural match; only replica/HPA counts differ (expected) | fund-account/contact resolution |
| x-balances | web(+canary+baseline) + 5 per-bank worker families (rbl/icici/yesbank/axis/idfc balance-fetch), 3 ports (grpc/http/internal) | `xbalances-server`/`xbalances-worker` (1 worker) | REPRESENTATIVE SUBSTITUTE | twin has no per-bank fan-out; a single worker cannot represent bank-specific fetch cadence/failure modes | balance/ledger correctness per bank |
| stork | `stork-api` + `stork-sms-worker` + `stork-webhook-worker` (each with p1/p2/dlq/canary/baseline), 2-3 CronJobs | `stork-capture` substitute | REPRESENTATIVE SUBSTITUTE (necessarily; stork is a shared notification platform, not payouts-specific) | twin has no worker fan-out or cron at all (by design — it's a capture stub) | merchant-visible state (webhook/SMS notification delivery) — out of payout-state-mutation critical path |
| mozart | web(70)+canary(7)+baseline(7), worker (**scaled to 0**), image tag resolved via Spinnaker parameter | `mozart-sim` (REAL clone build **CONFIRMED BLOCKED** — private module `github.com/razorpay/integrations-utils` unbuildable) | REPRESENTATIVE SUBSTITUTE (not REAL, and cannot become REAL without a private-module credential) | twin cannot exercise real Mozart bank-gateway logic at all | routing/gateway selection, transfer state — the highest-stakes substitute in the whole lane |
| dcs | `dcs-server`(v1+v2-proxy migration, 6 variants) + `dcs-consumer` + `dcs-dynamo-cdc-worker` | `dcs-stub` | REPRESENTATIVE SUBSTITUTE | no v1/v2-proxy distinction, no CDC path | merchant-config/feature-flag resolution |
| splitz | web(50)+canary(10)+baseline(10)+shadow(0) + `splitz-worker`(2) + `splitz-mcp`(1, port 9400) | `splitz-stub` | REPRESENTATIVE SUBSTITUTE | twin has no experiment-assignment logic, no MCP role at all | experiment/feature-flag bucketing across almost every service in this lane (payouts alone reads 38 experiment IDs) |
| workflows | web(5)+worker(5)+worker-asl(2, distinct image) | not in the twin's core/substitute list at all (banking-account approval flows presumably stubbed via `bankingaccounts-stub` or omitted) | MISSING | no ASL/state-machine path modeled | approval-workflow gating (payouts calls `workflow.host` with a 100ms timeout — a real approval gate) |
| banking-account | 2+1+1 replicas, image `banking-accounts:*` | `bankingaccounts-stub` | REPRESENTATIVE SUBSTITUTE | expected for a stub | banking-account provisioning/state |
| x-account-statements | web(2+1+1) + 7 distinct worker roles (fetch/ybl-fetch/statement-save/enrichment/source-event/statement-cdc/accounts-cdc) | `xas-sink` | REPRESENTATIVE SUBSTITUTE | no CDC or enrichment path | source-event correctness feeding payout reconciliation |
| batch | web + 7 sqs role variants (2 with swapped file-name/args, a repo-level bug) | not modeled (not in twin's 67-service list) | MISSING (likely acceptable — no payouts-named batch queue found) | — | — |
| `api` monolith (payout-relevant) | ~45 SQS/Kafka Deployments (dual-write, post-create, source-updater, queued/scheduled/on-hold dispatch, FMP, banking-account-statement processor/recon, fts liaison) | `monolith-stub` (one substitute) | REPRESENTATIVE SUBSTITUTE, and the least faithful mapping in the lane | one stub process cannot represent ~45 independently-scaled, independently-failing consumer roles that constitute the **other half** of every payout/fund-account/contact dual-write | auth, dual-write consistency, payout/transfer state — this is the mechanism by which the legacy monolith and the payouts microservice stay in sync; collapsing it to a single stub is the single largest fidelity gap identified in this lane |
| Queue naming | `prod-<svc>-<job>-live` convention, DLQs at the AWS resource level (not in app config except ledger's kafka DLQ topics) | short unprefixed names, config and seed script agree with each other | CONTRACT-FAITHFUL (internally consistent) but NOT literal-name-faithful | if any synthetic-config generator ever cross-checks queue names against a prod export, it will falsely flag every queue as "wrong"; the twin should document that names are deliberately re-mapped, not missing | — |
| FastCron / payouts cron | 21 HTTP routes, external SaaS-scheduled | 6 jobs on fixed 300s/900s intervals via `cron-driver` | REPRESENTATIVE SUBSTITUTE, explicitly self-flagged UNVERIFIED by the twin's own docs | twin is missing `fund_management_payouts/check`, `process_batch_submitted_payouts`, and several admin/maintenance routes; cadences are guesses, correctly labeled as such | payout/transfer state (FMP, bulk dispatch) |
| Spinnaker/bake pipeline | opaque Front50-managed `templatedPipeline`, image tags interpolated via pipeline parameters | `build.sh`/`docker compose build` with `ARENA_TAG` | N/A — not a component the twin needs to reproduce, but relevant context: **the real deploy-time config-generation step is outside every repo available to this investigation**, so no synthetic-config generator can claim full parity with "what actually gets deployed" without that Spinnaker template | — | — |

---

## Recommendation: real vs substitute

| component | run REAL or SUBSTITUTE | rationale |
|---|---|---|
| payouts | REAL (repo buildable, per prior bring-up notes) | core of the lane; twin already runs it |
| fts | REAL for the generic queue-key path; the twin's 12-worker set is a legitimate SUBSTITUTE for the ~157-worker bank fan-out — going further (modeling every bank) buys little unless bank-specific concurrency/failure-mode testing is a stated goal | fts binary itself is real; only the k8s topology (which queues get an isolated worker) is substituted |
| ledger | REAL, but the `scheduler` role should be run as a **scheduled/one-shot** process (matching the CronJob semantics), not a long-running Deployment, if fidelity to prod's split-account-balance cadence matters | mechanism (CronJob vs Deployment) affects whether a stuck run self-heals on the next tick vs needing a manual restart |
| cfa | REAL | already 1:1 |
| x-balances | REAL binary; SUBSTITUTE (single worker) for the per-bank fan-out is acceptable unless bank-specific balance-fetch cadence/failure behavior is under test | |
| stork | SUBSTITUTE (`stork-capture`) — correct choice; stork is a shared platform service, real webhook/SMS delivery is out of scope | |
| mozart | SUBSTITUTE, **cannot currently be REAL** — private Go module dependency (`github.com/razorpay/integrations-utils`) blocks building the real clone; this is a hard blocker, not a config gap. Protocol contract the substitute must implement: `gateway_auth`, `transfer_init`, `transfer_status` POST endpoints under `/fts` base path (per `fts/config/env.prod-live.toml:275-294`), each returning within the 180s timeout fts's client expects | |
| dcs | SUBSTITUTE — acceptable; if v2-proxy migration semantics matter, the substitute should expose the same two response shapes toggled by a flag mirroring `DCS_ENABLE_V2_PROXY` | |
| splitz | SUBSTITUTE — but should implement **deterministic experiment bucketing** (return a fixed variant per experiment ID from `splitz_experiment_list`/`splitz.experiments`) rather than a no-op, since 38+30 experiment IDs gate real behavior differences across payouts and fts | |
| workflows | currently MISSING from the twin; recommend SUBSTITUTE implementing the approval-gate contract payouts expects at `workflow.host`, 100ms timeout, 3 retries (`payouts/config/prod.toml:273-290`) | approval gating is a real state-transition guard, not cosmetic |
| banking-account | SUBSTITUTE — acceptable | |
| x-account-statements | SUBSTITUTE (`xas-sink`) — acceptable for ingestion; if source-event reconciliation correctness is under test, the substitute needs the `worker-source-event` contract specifically | |
| `api` monolith payout-relevant workers | currently one SUBSTITUTE (`monolith-stub`); recommend at minimum splitting into 2-3 role-differentiated substitutes matching the highest-traffic real roles — `sqs_multi_default api-payout-service-dual-write-live`, `sqs_multi_default api-payout-post-create-process-live`, `sqs_multi_default api-payout-source-updater-live` — since a single stub cannot independently fail/lag one dual-write direction without affecting all of them | this is the highest-value fidelity investment identified in this lane |

## Synthetic data

| family/table | field | source evidence | type+length | constraints | allowed values | FK/relationships | state rules | distribution matters? | generation rule | tier |
|---|---|---|---|---|---|---|---|---|---|---|
| payouts SQS queue names | `job.<key>` | `payouts/config/prod.toml:163-188` | string, `prod-payouts-<job>-live` or `prod-api-<job>-live` pattern | must match a provisioned SQS queue | 24 enumerated keys | none (config, not entity data) | n/a | no | literal copy of key list, twin may rename value only | EXACT (key names) / REPRESENTATIVE (twin's chosen queue-name strings) |
| fts worker concurrency map | `queue.worker_concurrency_map.<bank::rail::direction>` | `fts/config/env.prod-live.toml:45-127` | string key → int | key syntax is `::`-joined lowercase tokens matching the CLI arg exactly | ~90 enumerated keys, values 1–45 | key must equal a `fts-live-worker-*` deployment's `args[1]` | n/a | yes — concurrency spans 1 to 45 across banks, a flat default of 5 misrepresents load shape | if bank-specific fidelity is a goal, copy the full map; else keep `"default"=5` fallback | EXACT (structure) / ASSUMED (twin's flat value choice) |
| splitz experiment IDs | `splitz_experiment_list.<name>` (payouts), `splitz.experiments.<name>` (fts) | `payouts/config/prod.toml:458-495`; `fts/config/env.prod-live.toml:356-385` | string, Splitz-internal opaque ID (e.g. `PYD4Pq54K6uubY`) | 14-char alnum-looking token, real format not independently validated here | 38 (payouts) + 30 (fts) enumerated names | resolved at runtime by a Splitz service call, not a local constant | n/a | yes if the twin's splitz-stub returns different variants per experiment vs a single hardcoded one | twin should map each experiment name to a stub-chosen variant, not fabricate new IDs | REPRESENTATIVE (twin must invent stub IDs; real IDs are prod secrets-adjacent but not literally secret — still, treat as opaque) |
| cron FastCron schedule (all 21 routes) | interval | not in any repo — SaaS dashboard only | n/a | n/a | n/a | n/a | n/a | yes (auto-expiry sweep timing matters for merchant-visible state) | ASK owner; twin's current 300s/900s guesses are honestly self-flagged UNVERIFIED already | ASSUMED |
| DLQ names (payouts SQS) | — | not present in any payouts config file read | n/a | n/a | n/a | n/a | n/a | low (only matters under failure-injection testing) | ASK owner (Terraform) or omit | UNKNOWN |

## Cannot be derived from repositories

| missing artifact | why it matters | likely owner | minimal request |
|---|---|---|---|
| Spinnaker Front50 pipeline-template body (`spinnaker://664e59d6-943b-44e7-a3ab-fa5db69705f6:latest` and siblings referenced from every `spinacode/v3/payouts/prod/*/deploy-to-a-region.json`) | This is where the actual bake/deploy/parameter-substitution logic lives — without it we cannot confirm exactly how `${parameters.mozart_prod_commit_id}`-style placeholders get resolved into the final deployed manifest, nor whether any config keys are templated at bake time beyond image tags | Platform/Release-Engineering (owns Spinnaker) | a `spin pipeline-template get 664e59d6-…` export (or equivalent Front50 API read), sanitized of any account credentials it may embed |
| FastCron dashboard schedule export (21 `payouts /v1/cron/*` routes) | Determines real cadence for auto-expiry, stuck-payout dispatch, FMP check, batch-submitted dispatch — all merchant/fund-state-affecting sweeps | Payouts team (`#xp-payouts-service` per prior report attribution) | a schedule dump/screenshot from the FastCron dashboard for the payouts job group |
| AWS SQS queue resource definitions (visibility timeout, redrive/DLQ policy) for payouts' 24 queues, and cfa/x-balances/xas queues | Payouts' own config never sets `visibilityTimeout` (unlike ledger, which does at `:359`) — the real value is set on the AWS resource, likely via Terraform not in the readable repo set | Infra/Datastores | a `terraform show`/`aws sqs get-queue-attributes` sanitized export (names + timeout/redrive values only, no ARNs with account specifics beyond what's already public in configs) |
| Terraform/infra repo for RDS/Aurora, MSK, ElastiCache engine versions | No application config file in this lane states a DB/Kafka/Redis engine version; the twin's `mysql:8.0`/`postgres:15-alpine`/`apache/kafka:3.8.0`/`redis:7-alpine` choices are unverified against real prod versions | Infra/Datastores (`#gandalf` per prior reports) | engine-version-only export (no credentials) for the payouts-adjacent RDS/Aurora clusters, the `prod-noncde-kafka` MSK cluster, and the ElastiCache clusters named in configs (`payouts.cache.razorpay.vpc`, etc.) |
| stork `partitioner_cron_schedule` value | Templated in `templates/stork/templates/cronjobs.yaml:189` but its value was not located in `prod/stork/values.yaml` in the section inspected — either defined elsewhere in that (large) file or the CronJob is suspended | Stork/Platform team | confirm whether `partitioner_cron_schedule` is set in `prod/stork/values.yaml` (full-file grep) or intentionally absent |
| `payouts_sla_breach_monitor` actual trigger mechanism (FastCron HTTP vs in-process ticker vs both) | The route exists (`cron_routes.go:143-151`) but whether FastCron is configured to call it, or whether an in-process ticker also/instead calls the same handler internally, isn't resolvable from static route registration alone | Payouts team | confirm in FastCron dashboard and/or grep for an in-process ticker calling `SLAMonitorService.CheckSLABreaches` directly (out of this lane's time budget) |

## Fidelity tier verdicts

- payouts api + 14 modeled workers + 2 kafka consumers: **REAL** — twin runs the real binary; role-selection env vars match exactly.
- payouts' 10 unmodeled worker roles (fmp_check/initiate, data_consistency_*, batch_submitted_merchants, bulk_payouts, mail_and_sms_event, payout_usage_event_processing, rbl_banking_account_statement, update_source_event): **UNKNOWN-BLOCKED for the twin** — not a fidelity-tier question so much as a coverage gap; adding them is mechanical (copy an existing worker block + real queue key) since the real binary is already in use.
- fts: **REPRESENTATIVE SUBSTITUTE** for topology (12 of 157+ worker roles) even though the binary itself is real; reason: bank-specific concurrency/failure modes are not exercised.
- ledger: **CONTRACT-FAITHFUL**, with one open question (scheduler-as-Deployment vs scheduler-as-CronJob) that needs a one-line check against the twin's own compose file.
- cfa: **REAL** — exact structural and config match.
- x-balances: **REPRESENTATIVE SUBSTITUTE** for topology (1 of 5 bank-worker roles); binary itself real.
- stork, dcs, splitz, workflows(missing), banking-account, x-account-statements: **REPRESENTATIVE SUBSTITUTE**, appropriately so — these are supporting/platform services, not the payout state machine itself; splitz is the one exception worth upgrading (experiment bucketing affects payouts/fts behavior directly).
- mozart: **REPRESENTATIVE SUBSTITUTE, UNKNOWN-BLOCKED-from-becoming-REAL** — confirmed build blocker on a private Go module; the single most important substitute-vs-real gap in the lane because it's the actual bank-gateway execution point.
- `api` monolith payout-relevant workers: **REPRESENTATIVE SUBSTITUTE**, and the weakest one in the lane — one stub process standing in for ~45 independently-scaled real consumer roles that implement the payouts↔monolith dual-write contract; single most important place to invest additional substitute fidelity.
- Spinnaker/spinacode config-generation step: **UNKNOWN-BLOCKED** — genuinely outside every readable repo (opaque Front50 template reference); no synthetic-config generator in this program can claim to fully reproduce "what actually gets deployed" without it, though this mostly affects image-tag/parameter resolution rather than application-level config semantics.
- Engine versions (MySQL/Postgres/Kafka/Redis): **UNKNOWN-BLOCKED** — no terraform/infra repo available in this investigation's readable set.
