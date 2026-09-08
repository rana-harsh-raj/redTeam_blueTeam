# Lane D1 — payouts-core (M6 functional discovery)

Source of truth: pinned read-only clone `razorpay/payouts @ 4bf3dbf9` (plus `kube-manifests`
for prod deployments and `ENV2_COMPOSE/docker-compose.yml` for twin runtime state).
Part file: `reports/domain/parts/payouts-core.json` — **403 nodes, 1076 edges, 25 families,
0 schema violations, 0 dangling edge endpoints** (`scripts/domain/build_graph.py --strict`).

| kind | count |
|---|---|
| route | 113 |
| flag (28 DCS fields + 8 DCS objects + 34 splitz + 1 route-prefetch) | 71 |
| table | 32 |
| worker (25 SQS jobs + 2 Kafka consumers) | 27 |
| queue | 25 |
| ext | 24 |
| family | 25 |
| identity | 19 |
| event | 17 |
| state | 15 |
| cron | 13 |
| topic (2 Kafka + 10 SNS) | 12 |
| svc / db / repo / sub | 4 / 4 / 1 / 1 |

---

## 1. Processes

| process | entry point | twin |
|---|---|---|
| `svc:payouts-api` | `cmd/api` (build tag `boot`), prod `:8000`, default `:9400`, metrics `:8001` | **running** (`payouts-api`) |
| `svc:payouts-worker` | `cmd/workers`, one process per `PAYOUTS_WORKER_NAME` | **running** ×15 |
| `svc:payouts-kafka-consumer` | `cmd/kafkaConsumers`, `PAYOUTS_CONSUMER_TASK_NAME` | **running** ×2 |
| `svc:payouts-migrate` | `cmd/migration`, 34 migrations | **running** |

Build: `make go-build-api | go-build-worker | go-build-kafka-consumer | go-build-migration`
(`Makefile:146-171`, all `go build -tags boot`). Health: `GET /status`, `GET /commit.txt`
(`internal/routing/router/status_routes.go:12-30`); Prometheus + pprof on a second Gin engine
(`router.go:126-129`).

`pkg/worker/manager.go:149-152` — `Manager.Start` refuses to start unless a job with the
configured `[worker] name` is registered, so exactly one job runs per worker pod.

## 2. Workers

Queue names are the prod SQS names (`config/prod.toml:163-189`); the default-env name is in
brackets where it differs (`config/default.toml:370-392`).

| worker (`PAYOUTS_WORKER_NAME`) | queue | retries / timeout | twin |
|---|---|---|---|
| `queued_payout` | `prod-payouts-queued-processing-live` [`queued_payout`] | 5 / 120s | running |
| `transaction_create` | `prod-payouts-transaction-create-live` [`transaction_create`] | 5 / 120s | running |
| `schedule_payout` | `prod-payouts-schedule-processing-live` [`schedule_payout`] | 5 / 300s | running |
| `on_hold_payout` | `prod-payouts-on-hold-processing-live` [`on_hold_payout`] | 5 / 120s | running |
| `partner_bank_hold_payouts` | `prod-payouts-partner-bank-on-hold-payouts-live` | 5 / 120s | running |
| `webhook_event` | `prod-payouts-webhook-event-live` [`webhook_event`] | 3 / 120s | running |
| `fts_async_processing` | `prod-payouts-fts-async-processing-live` [`fts-async-processing`] | 5 / 120s | running |
| `fts_async_hv_processing` | `prod-payouts-fts-async-hv-processing-live` | 5 / 120s | running |
| `payout_create_failure_handling` | `prod-payouts-payout-create-failure-handling-live` | 3 / 120s | running |
| `payout_update_failure_handling` | `prod-payouts-payout-update-failure-handling-live` | 3 / 120s | running |
| `generic_processing` | `prod-payouts-generic-processing-live` [`generic-processing`] | 5 / 120s | running |
| `async_dual_write` | `prod-payouts-async-dual-write-live` | 5 / 120s | running |
| `payout_source_updater` | `prod-api-payout-source-updater-live` | 5 / 120s | running |
| `rbl_banking_account_statement` | `prod-payouts-rbl-banking-account-statement-live` | 7 / **1800s** | running |
| `x_balances_balance_refresh` | `prod-x-balances-balance-refresh-live` | 3 / 30s | running |
| `bulk_payouts` | `prod-payouts-bulk-payouts-live` [`bulk_payouts`] | 5 / 120s | **mapped only** |
| `batch_submitted_merchants` | `prod-payouts-batch-submitted-merchants-live` | 5 / 120s | **mapped only** |
| `data_consistency_checker` | `prod-payouts-data-consistency-checker-processing-live` | 3 / 30s | **mapped only** |
| `data_consistency_event` | `prod-payouts-data-consistency-event-processing-live` | 5 / 120s | **mapped only** |
| `payout_usage_event_processing` | `prod-payouts-payout-usage-event-processing-live` | 5 / 120s | **mapped only** |
| `api_queue_for_async_dual_write_direct_push` | `prod-api-payout-service-dual-write-direct-push-live` | 5 / 120s | **mapped only** (no prod Deployment either) |
| `x_balance_payouts_event` | `prod-x-balances-payout-event` | 5 / 120s | **mapped only** (no prod Deployment either) |
| `x_account_statement_source_event` | `prod-x-account-statement-source-event` | 5 / 120s | **mapped only** (no prod Deployment either) |
| `fund_management_payout_check` | `prod-payouts-fmp-check-live` [`fmp-check`] | 3 / 300s | **mapped only** |
| `fund_management_payout_initiate` | `prod-payouts-fmp-initiate-live` [`fmp-initiate`] | 3 / 100s | **mapped only** |
| Kafka `fts_status_updates` | topic `rx-fts-status-update-events`, group `rx-payouts-fts-status-update-consumer-group`, MaxRetry 1 | — | running |
| Kafka `fts_status_updates_retry` | topic `rx-fts-status-update-retry-events`, MaxRetry 2 / backoff 30s | — | running |

SQS driver: `visibilityTimeout 120`, `waitTimeout 30`, `maxRetries 5`
(`config/default.toml:393-410`); prod worker `maxconcurrency 2` (`config/prod.toml:157-161`).

## 3. Routes (113)

| group | prefix | auth | n |
|---|---|---|---|
| status | `/status`, `/commit.txt` | none | 2 |
| metrics engine | `/metrics` (+ pprof), port `:8001` | none | 1 |
| merchant payouts | `/v1/payouts` | BasicAuth(api, workflow) + passport(private, proxy, privilege) | 10 |
| dashboard GET (shadow-gateway cutover) | `/v1/payouts/schedule/timeslots` | `ServiceBasicAuthOrPassport` | 1 |
| v2 payouts | `/v2/payouts` | BasicAuth(api) + passport | 3 |
| bulk | `/v1/payouts/bulk*`, `/v1/payouts/attachments` | BasicAuth(api[, workflow]) + passport(proxy \| none) | 3 |
| internal payouts | `/v1/payouts/*` | BasicAuth(api, workflow, xperience, fts, vendorpayments, settlements, irctc) | 28 |
| in-flight reservations | `GET /v1/inflight_reservations` | same 7-cred allow-list | 1 |
| cron | `/v1/cron/*` | BasicAuth(fastcron) | 24 |
| admin | `/v1/admin/*` | BasicAuth(api) + passport(admin) | 6 |
| workflow callbacks | `/v1/workflow/state[/:id]` | BasicAuth(workflow, api) | 2 |
| status details / proxy attachments | `/v1/payouts/status_details/:id`, `PATCH /v1/payouts/:payout_id/attachments` | BasicAuth(api, workflow, xperience, fts) / BasicAuth(api)+proxy | 2 |
| notify (FTS) | `/v1/notify/{health/update,downtime}` | BasicAuth(fts) | 2 |
| payloadcrypt | `/v1/payloadcrypt/{encrypt,decrypt}` | BasicAuth(api) | 2 |
| BAS | `/v1/banking_account_statement*`, `/v1/dev_admin/...` | BasicAuth(api, workflow) / BasicAuth(fts) / BasicAuth(api) | 3 |
| elasticsearch | `/v1/elasticsearch/*` | BasicAuth(api) | 5 |
| fund-account validation | `/v1/fund_accounts/validations*` | BasicAuth(api, workflow) [+ passport private] | 4 |
| FMP admin | `/v1/fund-management-payout/balance-config/...` | BasicAuth(api, fastcron) | 2 |
| merchant config / onboarding / merchant | `/v1/admin/merchant-configuration`, `/v1/merchant_onboarding`, `/v1/merchant/*` | BasicAuth(merchantconfiguration) / (bankingaccounts, fastcron) / (api) | 5 |
| x-balances internal | `/v1/internal/*` | BasicAuth(xbalances) | 2 |
| idempotency-key exclusions | `/v1/idempotency-keys/check-excluded` | BasicAuth(api) | 1 |
| test email/sms | `/test/email/*`, `/test/sms/send` | BasicAuth(fastcron) | 4 |

Every route in the twin is served by the real `payouts-api` binary → `real_source_running`.

**Cron endpoints driven in the twin** (`ENV2_COMPOSE/scripts/cron-driver/driver.py:38-49`, FastCron
in prod): `process_queued_payouts` (partner-bank), `process_queued_low_balance_payouts`,
`process_scheduled_payouts`, `process_beneficiary_bank_on_hold_payouts`,
`process_inflight_reservation_reconciliation`, `payouts_dual_write_failure_processing`,
`process_batch_submitted_payouts`, `fund_management_payouts/check`,
`banking_account_statement/fetch/initiate`. The other 15 `/v1/cron/*` endpoints have no twin caller.

## 4. State machine (`internal/app/payouts/state_machine.go`)

15 statuses; 14 registered states + 13 events → 66 allowed transitions (`transitions` edges).

`create_request_submitted` (initial) → `pending` | `scheduled` | `queued` | `batch_submitted` |
`on_hold` | `ledger_response_awaited` | `created` | `failed` | `reversed`;
`created` → `initiated` (self-loop allowed, for FTS async retries) → `processed`;
`reversed` reachable from 11 states including `processed` and `failed`;
`cancelled` only from `queued` | `scheduled` | `on_hold`; `rejected` only from `pending`.
`pending_on_otp` is a declared constant with **no** state or event — the 2FA/OTP hold is not
implemented in PS at this SHA.

Webhook side effects fire from `Enter` hooks: `payout.initiated | processed | reversed | failed |
queued | rejected | pending` (and `on_hold` maps to `payout.queued`).

## 5. Families (25)

P0 (17): `shared-payouts`, `direct-payouts`, `bulk-payouts`, `approval-workflow`,
`failure-reversal-cancellation`, `queued-low-balance`, `scheduled-payouts`, `on-hold`,
`pricing-free-payouts`, `idempotency-retries`, `webhooks`, `internal-service-routes`, `admin-ops`,
`async-workers`, `source-updates`, `fetch-list`, `beneficiary-fund-accounts`.

P1 (7): `fund-management-payouts`, `va-payouts`, `data-consistency`, `account-statement`,
`usage-events`, `risk-validation`, `merchant-onboarding-config`.

P2 (1): `observability-sla` (TiDB SLA-breach monitor, stuck-payout monitor, log sampling — no
twin representation, hence `graph_only`).

Variants present in source for the P0 payout families: success, failure, pending, retry, duplicate,
idempotency, cancel_or_reverse, concurrency, accounting, webhook, restart.

Processor variants that decide the family at create time
(`internal/app/payouts/processorFactory.go:112-142`, `constants.go:37-39`):
`fund_account_payout_shared`, `fund_account_payout_direct`, `fund_account_payout_sub_balance`
(the last two registered in `processor/fundAccountPayoutDirect.go:34` and
`processor/fundAccountPayoutShared.go:156-157`).

## 6. Tables (31 created + 3 alter/cleanup migrations)

`payouts`, `payouts_temp`, `payout_details`, `payout_status_details`, `payout_sources`,
`payout_logs`, `payout_purpose`, `payout_attempts`, `payout_meta_temporary`,
`payout_meta_permanent`, `state_change_logs`, `reversals`, `banking_accounts`, `bank_accounts`,
`fund_accounts`, `fund_transfer_attempts_temp`, `counters`, `counter_transaction`, `settings`,
`workflow_config`, `workflow_entity_map`, `workflow_state_map`, `idempotency_keys`,
`idempotency_key_exclusions`, `bulk_idempotency_keys`, `duplicate_prevention_config`,
`banking_account_statement`, `banking_account_statement_details`, `event_outbox`,
`merchant_configurations`, `source_request_id_mapping`.
All are created by `payouts-migrate` in the twin.

## 7. Flags

**DCS** (`pkg/dcs/features/features.go`) — 8 storage objects
(`rzp/x/merchant/payouts/{ApiInterface,FundTransfer,Workflows,IntelligentPayouts,SubAccountRoles,
direct_accounts/PayoutModeConfig,direct_accounts/Configs}` + `rzp/x/merchant/accounting/
IntegrationSettings`) and 28 fields. Highest-leverage: `payout_service_enabled` (PS vs monolith
routing), `enable_payouts`, `enable_payout_workflow` + `skip_*` (maker-checker),
`payouts_to_fts_async_processing`, `payout_idem_key_required`, `in_flight_reservation_enabled`,
`enable_payouts_queue_buffer` / `queue_payout_bal_buffer`, `fmp_config`.

**Splitz** — 34 experiments in `[splitz_experiment_list]` (`config/default.toml:532-565`,
prod ids in `config/prod.toml:458-496`), plus route-scoped pre-evaluation for `POST /v1/payouts`
(`internal/experiments/route_mapping.go:44-56`: duplicate, merchant_configuration,
performance_optimization, partner_bank_on_hold).

## 8. Identities

Service credentials (names only, no values) `[auth.*]`: `api`, `fts`, `workflow`, `fastcron`,
`merchantconfiguration`, `xperience`, `vendorpayments`, `bankingaccounts`, `xbalances`,
`settlements`, `irctc`, plus declared-but-unused `dashboard` and `reminder`.
Passport v3 JWT (`apiv1` from the monolith, `edgev1`/`edgev2` from Edge) with legacy auth types
`private`, `proxy`, `privilege`, `admin`; `ServiceBasicAuthOrPassport` on one route.

## 9. Queues / topics

25 SQS queues (one per job). Kafka: `rx-fts-status-update-events`,
`rx-fts-status-update-retry-events`. SNS source-update topics (10 distinct):
`payout-updates-{xpayroll,payout-links,settlements,refunds,vendor-payments,charge-collections,
capital-collections,cross-border,petty-cash,generic-accounting}`.

## 10. Open questions

1. Two prod worker Deployments name jobs that do not exist in `internal/job` at this SHA:
   `PAYOUTS_WORKER_NAME=mail_and_sms_event` and `=update_source_event`
   (`kube-manifests/templates/payouts/templates/payouts-worker-{mail-and-sms-event,update-source-event}-live.yaml`).
   `Manager.Start` refuses unregistered names, so those pods either crash-loop or run a different
   image tag. Not resolvable read-only.
2. Three registered jobs have **no** prod Deployment: `api_queue_for_async_dual_write_direct_push`,
   `x_balance_payouts_event`, `x_account_statement_source_event` (their prod queue names do exist).
3. `config/default.toml [job]` omits `source_updater`, `async_dual_write`,
   `rbl_banking_account_statement`, `x_account_statement_source_event` — those workers get an empty
   queue name outside prod-like configs.
4. `[auth.dashboard]` / `[auth.reminder]` are declared but referenced by no route group.
5. `pending_on_otp` is declared but unreachable — no 2FA/OTP hold state in PS.
6. The shadow gateway (`router.go:59`, `[shadow_gateway]` in `prod.toml:857`) can honour upstream
   responses ahead of every PS middleware; its rollout state was not verified and no twin journey
   exercises it.
7. SNS source-update fan-out is named in config but not published or asserted anywhere in the twin;
   the legacy (non-SNS) source-update transport was not traced end to end.
8. Elasticsearch and TiDB back six routes and three crons with no twin representation.
