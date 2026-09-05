# 31 — Env 2 arena bring-up notes (2026-09-04, after the config-derivation fix)

Raw log of what it took to get every core process of the arena to `healthy`, in the order it was hit. Each item names the evidence in the repo clones (read-only) and the arena change made in `ENV2_COMPOSE/`.

## payouts workers: one job per process, selected by env

- `internal/boot/handler.go:808` → `provider.GetWorker(ctx).Start(ctx)` panics `can not start worker to handle unregistered job` when `worker.Config.Name` is not one of the registered job names. `config/devstack.toml:139-143` carries `[worker] name = "payouts"` (placeholder); the real deployments in `kube-manifests/templates/payouts/templates/*.yaml` set `PAYOUTS_WORKER_NAME` per Deployment.
- Real worker Deployments found (25 names): async_dual_write, batch_submitted_merchants, bulk_payouts, data_consistency_checker, data_consistency_event, fts_async_hv_processing, fts_async_processing, fund_management_payout_check, fund_management_payout_initiate, generic_processing, mail_and_sms_event, on_hold_payout, partner_bank_hold_payouts, payout_create_failure_handling, payout_source_updater, payout_update_failure_handling, payout_usage_event_processing, queued_payout, rbl_banking_account_statement, schedule_payout, transaction_create, update_source_event, webhook_event, x_balances_balance_refresh (+ a hyphenated `partner-bank-hold-payouts` variant).
- viper `AutomaticEnv` (prefix `PAYOUTS`, `.`→`_`) only overrides keys that exist in the loaded TOML, so `[worker]` (name/maxconcurrency/waittime/retrydelay) had to be present in `arena.toml` for `PAYOUTS_WORKER_NAME` to apply.
- Arena: 14 compose services `payouts-worker-<job>` (the payout-path subset), each `PAYOUTS_WORKER_NAME=<job>`, `PAYOUTS_WORKER_MAXCONCURRENCY=2`, health `GET :9400/status` (the worker process also serves the HTTP app on `App.Port`).

## payouts SQS: plain `sqs` driver dials real AWS

- `goutils/worker@v1.0.0/queue/broker/sqs/queue.go`: dialect `"sqs"` → `session.NewSession` with region only (real AWS endpoint); dialect `"sqs_local"` → `NewWithEndpoint` honouring `Endpoint`. Pristine `[queue] driver="sqs"` + `prefix=http://localstack:4566/…` therefore still resolved the AWS SQS endpoint and failed with `NoCredentialProviders`.
- Arena: `[queue] driver = "sqs_local"`, `[queue.sqs] endpoint = "http://localstack:4566"`; synthetic `AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION` in the shared env anchor (LocalStack accepts any value; the SDK refuses to sign without them).
- LocalStack has no queue auto-creation: `seeds/localstack/init-queues.sh` (ready.d hook) creates the 32 SQS queues named in the generated payouts `[job]`, cfa dual-write/lazy-load, ledger `[job]` and x-balances configs, plus the SNS `source_update_topics` topic. 33 objects created on boot.

## payouts Kafka consumer: task selected by env, config sections absent from default.toml

- `internal/provider/consumer_provider.go:56` does `m[ConsumerTask.Name].(string)` over the `[task]` struct; with `[consumer_task] name = "fts"` (the value in `config/default.toml:420`) there is no such key → nil-interface panic. Production sets `PAYOUTS_CONSUMER_TASK_NAME=fts_status_updates` / `fts_status_updates_retry` (`kube-manifests/templates/payouts/templates/payouts-kafka-fts-status-updates{,-retry}-consumer.yaml`, also `payouts/deployment/dev/docker-compose.yml:237,269`).
- `[kafka_consumers]`, `[task]`, `[topics]` exist only in `config/devstack.toml:275-330`; copied into the arena template with `Brokers = ["kafka:9092"]`, `EnableTLS=false`.
- Arena: two services `payouts-kafka-fts-status-updates-consumer` and `…-retry-consumer`; both joined their consumer groups and claimed partition 0 of `rx-fts-status-update-events` / `rx-fts-status-update-retry-events` on the arena Kafka.

## Compose mechanics

- Compose profiles: `depends_on` crosses profiles, so every `run`/`up` must carry `--profile datastores` (+ `substitutes` for core). `scripts/up.sh` now does.
- Networks: without `name:` compose created `env2_compose_rzp-arena`; `network/*.sh` and the Stork registration step reference `rzp-arena`. Fixed with explicit `name:` on both networks; the leftover host-spike containers/network of the same name were removed.
- `secrets/gen-secrets.sh` always overwrites; `up.sh` now keeps existing secrets on re-run (set `REGEN_SECRETS=1` after `down.sh`).
- Note for later: `payouts/deployment/dev/docker-compose.yml` is the upstream developer compose file and is the best single reference for the per-process env contract (worker names, consumer task names, ports).

## LocalStack region and queue-URL semantics (second round)

- LocalStack resolves the **region of a path-style queue URL (`http://localstack:4566/<account>/<name>`) from the SigV4 `Authorization` header**. Anonymous/unsigned requests fall back to `us-east-1`, so every queue created in `ap-south-1` answered `AWS.SimpleQueueService.NonExistentQueue` (payouts with `credentials = "anonymous"`, cfa with `UseAnonymousCredentials = true`, ledger with `region = "us-east-1"`). Fix: synthetic `AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION` in the shared compose env anchor, `region = "ap-south-1"` everywhere, anonymous flags off.
- `goutils/worker@v1.0.0` (cfa) and payouts' vendored client both build `QueueUrl = "<Prefix>/<queueName>"`; `Prefix` must therefore be the full `http://localstack:4566/000000000000` URL, not a name prefix.
- payouts' own `pkg/worker/queue/broker/sqs` honours `[queue.sqs].endpoint`; `goutils/worker` only does so under the `sqs_local` dialect (cfa: `QUEUE_DRIVER=sqs_local` on the worker only, because cfa-server rejects any driver other than `sqs`).
- ledger's `journal_created` SNS publisher (`sns_local`) now points at the arena LocalStack with an `ap-south-1` ARN; topic `journal-created` is created by the ready-hook. Earlier analysis said this path does not run in the arena app mode; the change only removes a dangling `127.0.0.1:4100` endpoint.

## cfa worker: queue chosen per Deployment by `CFA_WORKER_QUEUE_NAME`

- `cfa/pkg/worker/worker.go:13-24` overrides `Worker.QueueName` from `CFA_WORKER_QUEUE_NAME`; `config/default.toml` ships `QueueName = ""`, so a worker started without the env polls an empty queue name (LocalStack: `NonExistentQueue`, ~17k errors/30 s).
- Production (`kube-manifests/cde/cfa/values.yaml:45-60`) runs exactly two workers: `cfa-worker-fa` on the fund-account lazy-loading queue and `cfa-worker-contact` on the contact lazy-loading queue (queue names are prod-specific `prod-api-rx-*-lazy-loading-live`; arena uses the generated `contact-lazy-load` / `fund-account-lazy-load`). The `*-dual-write-queue` queues are produced by cfa and consumed elsewhere (not by cfa).
- Arena: services `cfa-worker-contact` and `cfa-worker-fa`.

## Status after these fixes (2026-09-04 ~15:30 UTC)

All 30 core containers healthy; ledger-worker and the 14 payouts workers poll their queues with HTTP 200 from LocalStack; both Kafka consumers joined their groups.

## Credential pairing (verifier round 1-3)

The first verifier runs returned 401 from payouts-api ("The api key provided is invalid") and fts-web. Root cause was systematic, not a single typo: `config/generate.py` inlined the generated `auth_*` secrets only on the **client** side of each pair (payouts `[ledger]`, `[cfa]`, `[x_balances]`, `[dcs]`, … ; x-balances `LedgerConfig.Auth`, `PayoutService.Auth`), while every **server** side still carried the template placeholders (`api_secret`, `payouts`, `arena_ps_pass1`, "password"). Fixed by pointing the inbound sections at the same secret tokens:

| Callee | Inbound section | Now equals |
|---|---|---|
| payouts-api | `[auth.api]` (api), `[auth.fts]` (fts), `[auth.workflow]`, `[auth.fastcron]` (fast_cron), `[auth.xbalances]` (x_balances) | `auth_api_payouts`, `auth_fts_payouts`, `auth_workflow_payouts`, `auth_fastcron_payouts`, `auth_xbalances_payouts` |
| ledger-api | `[auth.payouts]` payouts_key, `[auth.fts]` fts_key, `[auth.xbalances]` x_balances_key | `auth_payouts_ledger`, `auth_fts_ledger`, `auth_xbalances_ledger` |
| cfa-server | `[Server.Auth.Payouts]` payouts | `auth_cfa_payouts` |
| xbalances-server | `[Server.Auth.PS]` x_balances | `auth_xbalances_payouts` |
| fts-web | `[users.ps]` (env `USERS_PS_*`) payouts, `[users.api]` rzp_live | `auth_fts_payouts`, `auth_monolith_shared` |

Other facts surfaced on the way:
- fts resolves inbound Basic-Auth users by reflecting the route's auth key (`API`, `PS`, …) onto `config.Users` (`internal/routing/middleware/auth.go:64`); the values come from `env|USERS_<KEY>_USERNAME/PASSWORD` placeholders in `config/env.default.toml:5505+`.
- payouts' `[fts]` client block (host + `env|PAYOUTS_FTS_AUTH_*`) exists only in env-specific TOMLs (`config/devstack.toml`), not `default.toml`; the arena template now carries it with `host = http://fts-web:8080/v1`.
- fts' payout status webhook (`[webhook.payout.transfer_status]`) targets the **monolith** `/v1/update_fts_fund_transfer` in devstack (`env.devstack.toml:292`), authenticated with `[webhook.payout.auth]` = the monolith identity; the arena points it at `monolith-stub:8080/update_fts_fund_transfer`, whose relay PATCHes payouts-api `/v1/payouts/update_payouts_with_fts` with the `api` family (payouts `payout_internal_routes.go:16` accepts API/Workflow/Xperience/FTS/…).
- fts-web binds `LISTEN_IP = 127.0.0.1` by default (production fronts it with nginx); the arena sets `0.0.0.0`.
- cron-driver had no credential at all; it now reads `auth_fastcron_payouts` from a mounted secret (user `fast_cron`).
- kong-lite gained `POST /_arena/mint` (arena-only passport minting for the verifier; refuses non-`ARENA*` merchant ids); the verifier's `ps_public_client` speaks the post-Kong contract directly to payouts-api via `PS_PUBLIC_URL`.
- verifier bridge identities corrected: `ps-service` = `api`/`auth_api_payouts`, `fts` = `payouts`/`auth_fts_payouts`.
- fts Basic-Auth **username convention**: `middleware/auth.go:87-96` derives the app from the username prefix before the first `_` and compares it with the route's auth key (`ps`, `api`, `settlement`, …). A PS identity must therefore be named `ps_<anything>`; the arena uses `ps_payouts` (payouts `[fts.auth]`, fts `USERS_PS_USERNAME`, verifier bridge) and `api_monolith` for the monolith identity.
- payouts-api validates the passport JWT against `[passport.<x>].identifier/publicKey` by `kid`; the arena template now carries `[passport.edge] identifier = "arena-passport-1"` and the per-arena public key (derived token `SECRET.passport_public_key_toml`), matching kong-lite's signer.

## Golden-run debugging (verifier rounds 4-9)

- payouts `POST /v1/payouts` (`internal/app/dtos/payoutCreate.go`) requires `merchant_id` (len 14), `purpose`, `amount`, `currency`, `fund_account_id`; the monolith supplies `merchant_id` server-side. The verifier now derives it from the passport it minted (`helpers/payouts_flow.create_payout`).
- `fund_account_id` in the create request is the **monolith/API** fund-account id (payouts fetches `GET /v1/fund_accounts_internal/fa_<id>` from the monolith, cached in redis); the seeded mapping is `ARENAFAX00000N` (monolith/cfa) vs `ARENAFA000000N` (payouts-local row). Verifier fixtures now use `ARENAFAX…`.
- payouts addresses the monolith as `/v1/<route>` (`/v1/internal/merchants/<id>`, `/v1/fund_accounts_internal/fa_<id>`, `/v1/payouts_service/*`); the substitutes' route tables were registered without the version prefix → shared `base_stub._dispatch` now also tries the path with `/v1` stripped, and every error body is object-shaped (`{"error":{"code","description"}}`) because payouts' `api.ErrorResponse` unmarshals `error` into a struct.
- payouts → monolith identity is `[api.auth]` (`rzp_live` / `auth_monolith_shared`), not the `api` family (which is the monolith → payouts direction).
- The `ARENA_DCS_URL` context override only covers calls whose context carries it; `goutils/dcs` falls back to the hardcoded host map for every other call (`dcs.go: getLoginURI/getURLAndMode → GetContextUrl`). payouts' HTTP-encryption feature check hit `https://dcs-live.dev.razorpay.in/v1/kv/get` and was stopped by the proxy blackhole (`proxyconnect tcp 127.0.0.1:9: connection refused`) inside the internal network — no packet left the arena, but the call is wrong. Fix: build-time `replace github.com/razorpay/goutils/dcs => <arena-patches/goutils-dcs-vX>` in the three repo copies, where `GetContextUrl` returns `ARENA_DCS_URL` when the context has none (no-op when unset).
- fts `POST /v1/transfer` body shape (`controllers/validation.go:230`): `product`, `merchant_id`, `transfer{amount, source_id, source_type, preferred_mode…}`, `account{fund_account_id:int | bank_account | vpa | …}`.

## Schema drift: repo migrations do not produce the schema the code writes

- After a clean run of all 34 payouts goose migrations (max version `20260720000001`), `payout_details` has 8 columns; `internal/app/payoutDetails/model.go` writes `beneficiary_bank_code` (used by `repo.go:412` bene-bank-down filtering and `payoutDetails/core.go:222`). Every create failed with MySQL 1054 `Unknown column 'beneficiary_bank_code'`. No migration file mentions the column, so production carries it from out-of-band DDL (gh-ost/manual). Arena: `seeds/schema-patches/payouts.sql` (idempotent, applied by `up.sh` after migrations) adds it as `VARCHAR(50) NULL`; the type is inferred from usage, not from production.
- Same class of drift is likely elsewhere (other tables/services); only the first blocker is patched. The migration-count rows in ENV2_BUILD_STATUS therefore mean "migrations apply", not "schema equals production".
- fts `transfers.source_id` is `varchar(14)`; a 15-char synthetic id caused MySQL 1406 — the verifier now uses 14-char ids. fts `POST /v1/transfer` responds `{"fund_account_id","fund_transfer_id","status":"CREATED"}` (no `id`).
- payouts scheduled payouts: `scheduled_at` must be strictly after the end of the current IST hour, within `SCHEDULED_AT_MONTHS_ALLOWED`, and fall in IST hours {9, 13, 17, 21}; stored value is truncated to the start of the hour (`internal/app/payouts/schedule.go`). The verifier's cron-dequeue test now picks a real slot and moves the row's `scheduled_at` into the past (arena-only time travel) before calling the cron.
- The rebuilt runtime images initially lost the hand-layered IFSC assets and cfa entrypoint (`build/assets.Dockerfile.md` described manual steps); `build-host.sh` now stages them (`stage_assets_payouts/cfa`) and the runtime image carries `socat`.
- The alpine runtime image had no `tzdata`; payouts' free-payout slab, IST timestamp and scheduled-slot code all fail with `unknown time zone Asia/Kolkata` (surfacing as HTTP 500 `internal_server_error` on create). `tzdata` is now in `build/runtime-only.Dockerfile` (all five images rebuilt).
- Direct-account (M2/RBL) creates call the Banking Accounts service `GET /payouts/shield/merchant/{id}/details` (`payouts/pkg/bankingAccountService/fetch_merchant_details.go`); the arena template had `[banking_account_service] host = http://127.0.0.1:1` (blackhole). New substitute `bankingaccounts-stub` answers it from `seeds/monolith/merchants.json`.
- `PAYOUT_META_PERMANENT_ENTITY_FETCH_FAILED record_not_found` on create is benign: it is the ES-sync path looking for a `payout_meta_permanent` row that only exists after FTS webhook data arrives (`payouts/core.go:10029`).

## Golden run, second half (FTS, ledger, monolith relay)

- **Payouts → FTS goes through the monolith.** PS' `fts_async_processing` worker calls the monolith `POST /v1/payouts_service/create_fta/{id}` (`payouts/pkg/api/fts_create.go`); the monolith reads the PS payout (`GET /v1/payouts/pout_<id>`, needs a merchant passport), creates the FTA and calls fts `POST /v1/transfer` (`api/app/Models/Payout/Processor/Base.php createFTAForPayoutService`), and answers `{"status","error"}`. PS then moves the payout to `initiated` itself (`processor/payoutFTS.go CreateFTS`) and learns the terminal state only from the FTS status webhook relayed by the monolith. `monolith-stub` now implements both legs; because a payout id does not reveal its merchant, the stub mints a passport per seeded merchant until PS answers 200 (the real monolith reads its own DB).
- **Monolith FTA→payout status map** (`api/app/Models/Payout/Status.php $ftaToPayoutStatusMap`): DEFAULT and SHARED map FTA `FAILED` → payout `REVERSED`; DIRECT (RBL/ICICI/AXIS) maps `FAILED` → `FAILED`. PS itself refuses initiated→failed for shared accounts (`payouts/core.go HandlePayoutFailed`). The stub relay applies the map using the merchant's account type.
- **FTS is one machinery worker process per `[queue.worker_queue_map]` KEY**: `-command=<key>` (e.g. `rbl::imps::initiate_transfer`), not the value; a worker started with the value name silently consumes the default queue while tasks pile up in `fts_<queue>`. The arena runs 13 fts workers (default, initiate_transfer, check_transfer_status, fire_transfer_status_webhook, retry_*, rbl::*). fts-web registers tasks; transfers are enqueued on create (`enqueueTransferForProcessing`) — no cron needed for initiation.
- fts `source_accounts.mozart_identifier` must be the channel **version key** (`v1`), because `channel.GetConfig(version, operation)` indexes `[integration.api.<channel>.<version>]`; `credentials`/`configuration` must be JSON objects (unmarshalled at gateway-call time). fts `[mozart] BASE_URL` → `http://mozart-sim:8085/fts` (path `/{namespace}/{gateway}/{version}/{action}`).
- Direct accounts route through `direct_account_routing_rules` (merchant + product + channel + mode → source account); without rows the transfer fails `UNABLE_TO_DETERMINE_TRANSFER_SOURCE_ACCOUNT`.
- **Ledger**: entries are built from `ledger_config.config.ledger_entries[*].account_discovery_config` (identifiers like `banking_account_id: "$banking_account_id"` resolved against `account_details.entities`); PS sends `identifiers.banking_account_id = bacc_<id>` (signed), so seeded entities must carry the signed id. The hand-transcribed X configs (`{"entries": [...]}` shape) matched the `payout_initiated` rule first and produced journals with **zero entries**; the real set is now loaded through ledger's own `LedgerConfigAPI/CreateInBulk` (`shared_account_x`, `direct_account_x`; 52 configs). Ledger composes the SNS topic as `topicArn:topicName`, so `topicArn` is the account prefix.
- ledger-api binds `[app] port = "127.0.0.1:8080"` by default (like fts) → `0.0.0.0:8080` in the arena.
- Verifier: waits are scaled by `ARENA_WAIT_SCALE` (default 6×) because the arena's cron-driven status checks are slower than the spec's production windows.

## Golden run, third round (queued dequeue, direct accounts, details sync)

- fts keeps a source-account mapping **only if `channel_information_status` has an UP row** (status 100) for `(channel, mode, mozart_identifier, integration_type)` (`internal/account/service.go filterSourceAccountMappingByChannelHealth`, map built in `internal/channel/service.go GetChannelHealthMapForTransfer`); an empty table filters every mapping out. Seeded RBL × IMPS/NEFT × POOL/DIRECT rows.
- payouts' low-balance dequeue cron (`ProcessInitiateForQueuedPayouts`) filters balance ids by API-DB `balance.updated_at` within 6 h and then fetches VA balances from the **monolith** (`GET /v1/internal_balances_queued`, body `{"balance_ids":[…]}` → `{"balances":{id: paise}}`, `payouts/pkg/api/fetch_balances.go`). In production the monolith's `balance` table follows the ledger through its journal-created consumer; the arena has no such consumer, so `monolith-stub` answers from ledger-api (`AccountAPI/FetchByMerchantID`, the MerchantBalance account) for shared merchants and a synthetic constant for direct ones.
- payouts-api rejects unknown JSON fields on cron routes (`json: unknown field`), and an empty body is a 400 `EOF`; the cron routes take `{}` (the `balance_ids` DTO belongs to the queued-payouts *initiate* request, optional there).
- The monolith syncs FTA details into PS before the status (`PATCH /v1/payouts/update_payouts_details_with_fts`: `source_id`, `fund_transfer_id` required, `utr`/`mode`/`channel`/`bank_status_code`/`failure_reason`/`remarks`/`return_utr`/`gateway_ref_no`); without it a processed payout has no `utr`. The relay now does both PATCHes. The FTS webhook payload is `GetMapFromTransfer` (transfer columns: `source_id`, `source_type`, `fund_transfer_id`, `status`, `utr`, `gateway_ref_no`, `bank_status_code`, `failure_reason`, `mode`, `channel`, `return_utr`, `remarks`, `narration`, …).
- For transfers whose `transfer_meta.origin_service` is Payouts Service, fts posts the status webhook **directly to PS** (`[payouts_service.update_fts_fund_transfer]`); FTAs created by the monolith carry the monolith origin and go through the monolith relay (the arena path).
- The verifier treats a healthy `MOZART_MOCK_URL` as the live bank path; it now points at `mozart-sim` (which gained `GET /health`), so processed/reversed outcomes come from the real FTS → Mozart-sim → webhook chain instead of synthetic PS webhooks.

## Golden run, fourth round

- fts marks a transfer DIRECT only when the create request carries `transfer.preferred_source_account_id` (`internal/transfer/service.go:549`); the monolith sends it for current-account merchants. Without it the transfer is POOL-routed and a direct merchant fails `UNABLE_TO_DETERMINE_TRANSFER_SOURCE_ACCOUNT`. The channel-health query for the transfer keys on `(account_type = bank account type e.g. CURRENT, source_account_type = POOL|DIRECT)`, so the seeded `channel_information_status` matrix covers both.
- PS internal routes: `/v1/payouts/update_payouts_details_with_fts` is **POST**, `/v1/payouts/update_payouts_with_fts` is PATCH (`payout_internal_routes.go:28-35`); the details sync carries the utr.
- PS cron `/v1/cron/process_queued_payouts` takes `{"type": …}` (`dtos/v2/process_queued_payouts_request.go`) and the factory only registers `partner_bank_downtime` (`queued/queued_payouts_factory.go`); the low-balance dequeue is `/v1/cron/process_queued_low_balance_payouts` with `{}`.
- ledger caches account balances: a verifier that tops a merchant up with SQL on `accounts` is invisible to ledger-api and to payouts' VA-balance check. Top-ups now go through a real journal (`positive_adjustment_processed`, X config: debit payable/adjustment, credit the merchant's MerchantBalance by `banking_account_id`); the arena seeds the adjustment counter-account (`ARENAPRACC0007`).
- `config/generate.py` rewrote every `arn:aws` to `arn:local` (anti-real-ARN rule) and preflight refused `arn:aws`; LocalStack only accepts `arn:aws:…:000000000000:…`. Both now exempt exactly the synthetic LocalStack account `000000000000`; every other ARN is still rewritten/refused. Ledger's SNS topic is composed as `topicArn:topicName`, so `topicArn` holds the account prefix in both the pubSub and snsOutboxer blocks.
