# Kafka route: observed bootstrap failure and separate source limitations

The first Shared Kafka run proves that FTS published and the real Payouts Kafka consumer received the status messages. It does **not** pass terminal/accounting acceptance: Payouts stayed `initiated` while FTS was `PROCESSED`. The observed blocking error is an unregistered Payouts job in the Kafka process. The raw Kafka payload also lacks the internal DTO fields needed downstream, but this run did not reach the corresponding Ledger operation.

Source revisions: Payouts `4bf3dbf9239feadea6d65ca90c893a988e116173`; FTS `2a09e763116db47a2f28553c677ad73ef8133ebf`. Source paths below are relative to their respective repositories. No core source was changed for this diagnosis, and no translated Kafka message was injected.

## First Shared run: runtime evidence

Retained artifacts: [scenario result and complete ending snapshot](runs/profile-kafka/golden/live-result.json), [sanitized transport and consumer records](runs/profile-kafka/golden/route-evidence.json), [egress audit](runs/profile-kafka/golden/egress.json).

Payout `pout_TYMdRe1BjalW9s` was created through Kong and received its balanced initiated journal. The ending snapshot records Payouts `initiated`, FTS transfer `1` as `PROCESSED`, and no processed Ledger journal. The route artifact separately records `transport_observed=true` and `scenario_status=failed`.

The consumer records establish this order:

1. `12:59:49.899Z`: the actual `INITIATED` FTS message was received.
2. `12:59:49.914Z`: `PAYOUT_DUAL_WRITE_DISPATCH_TO_QUEUE_FAILURE`, with `job should be registered before performing`.
3. `12:59:49.959Z`: the actual `PROCESSED` FTS message was received, including `source_account_id=900001` and `bank_account_type=NODAL`.
4. `12:59:49.963Z`: the same dual-write job-registration error.
5. `12:59:49.969Z`: `QUEUE_PUSH_FAILED_FOR_LEDGER_PROCESSED_EVENT`, with `internal_server_error: job should be registered before performing`.
6. The processed message was retried, and the same failures recurred at `12:59:50.978Z` and `12:59:50.982Z`.

These are the timestamps preserved in the capture, not an inferred queue history. The Shared scenario timed out after its bounded 45-second wait for `processed`.

## Source cause: jobs are not registered in Kafka startup

| Boundary | Source evidence | Consequence |
|---|---|---|
| Kafka executable | Payouts `cmd/kafkaConsumers/main.go:18` calls `(&boot.KafkaConsumer{}).Init(ctx)`. | The intended Kafka-specific bootstrap executes. |
| Kafka boot | `internal/boot/boot_kafka_consumer.go:17–29` initializes providers, then calls `registerDefaultHandlersForKafkaConsumer(ctx)`. | It does not call the ordinary handler-registration function. |
| Kafka registration | `internal/boot/handler.go:112–136` initializes the Kafka task handler through `taskHandlers.Initialise(ctx)`; `internal/taskHandlers/base.go:43–49` registers that handler with the Kafka consumer provider. | This registers a Kafka message handler, not ordinary Payouts jobs. |
| Ordinary API/worker registration | `internal/boot/handler.go:139–161` calls `job.RegisterJobs()` at line 154. `internal/boot/boot_worker.go:37` invokes this ordinary registration path. | API/worker processes populate their job registries. Kafka startup omits this call. |
| Job registry | `internal/job/base.go:204–218` loops over `JobConfigs` and calls `provider.GetWorker(...).Register(...)`. `pkg/worker/manager.go:105–114` constructs an empty `handlers` map. | The registry is in the current process's memory. Other running workers cannot populate it. |
| Enqueue guard | `pkg/worker/manager.go:186–199` checks registration in `Perform` before serialization and `queue.Enqueue`. | This error occurs before a queue transport operation. Changing Redis/SQS endpoints cannot cure it. |
| Shared processed event | `internal/app/payouts/core.go:2532–2597` stores FTS metadata, checks the banking-account type, and queues `LedgerProcessedEventFailureType` for Shared accounts. `internal/job/payout_update_failure_handling.go:81–98` calls `GetWorker(ctx).Perform` and returns its failure. | The observed Shared Ledger enqueue fails inside the Kafka process. |
| Payout status ordering | `internal/app/payouts/core.go:2449–2489` calls `ledgerProcessingForPayoutProcessed` before `payout.OnEvent(EventProcessed)` and the payout update. | A Shared enqueue failure returns before the payout transitions to processed. This matches the retained snapshot. |

`handleKafkaConsumer` (`internal/boot/handler.go:776–785`) initializes services, registers the payout service as the Kafka handler, and launches Kafka workers; it does not register ordinary jobs later. The retry consumer uses the same executable/bootstrap, so selecting the retry task does not resolve the omission.

The local launch matches the source entrypoint: `ENV2_COMPOSE/build/build-host.sh:93` builds `./cmd/kafkaConsumers` with the `boot` tag, and `ENV2_COMPOSE/docker-compose.yml:875–923` runs `/app/payouts-kafka-consumer` for both `fts_status_updates` and `fts_status_updates_retry`, with the corresponding `PAYOUTS_CONSUMER_TASK_NAME`. No supported command or environment switch was found that calls the missing job registration. Starting an additional ordinary worker would create a different process registry. Adding `job.RegisterJobs()` to Kafka startup would be a **core bootstrap change**, not a fixture or Compose wiring correction; it was not made.

## Separate downstream payload and status limitations

FTS `internal/transfer/service.go:1306–1356` constructs the transfer map with `source_account_id` and `bank_account_type`, and its Kafka branch (`service.go:1138–1144`) publishes that map. It does not add `fts_fund_account_id`, `fts_account_type`, or `fts_status`.

Payouts `internal/taskHandlers/fts_status_updates.go:42–53` unmarshals the raw message directly into `StatusUpdateRequest` and `DetailsUpdateRequest`. `internal/app/dtos/payoutUpdate.go:8–16` expects those internal `fts_*` fields, so they remain empty. The consumer uses `json.Unmarshal`, which ignores unknown fields; this is distinct from the strict HTTP details DTO rejection of `narration` fixed in the monolith substitute.

For a processed Shared payout, `core.go:2534–2542` copies the empty internal values to temporary FTS metadata. The downstream asynchronous handler would later use the stored fund-account ID and account type in `CreateTransactionViaLedger` (`internal/app/payouts/asyncFailureHandlingHelper.go:498–512`). **The first run's enqueue failure prevents that job from being dispatched. Therefore, the DTO mismatch is source-confirmed, but a Ledger-discovery failure from it was not observed in this run.**

Both Kafka task implementations return without applying `FAILED` or `REVERSED` (`internal/taskHandlers/fts_status_updates.go:57–63` and `fts_status_updates_retry.go:60–66`), retaining API as reversal truth in their source comment. Consumption alone does not establish terminal failure/reversal processing.

## Direct-account observation limit

The later Direct-account attempt in the first Kafka environment was run after the failing Shared message. The parent observed Payouts still initiated and FTS processed, with no matching consumer call during the 30-second observation. Backoff or blocking by the earlier message is a possible explanation, not an established cause. That attempt cannot isolate Direct-account behavior. The clean Direct-first run below supplies independent branch evidence; it does not retroactively diagnose the earlier timeout.

The source does skip Shared Ledger processing for Direct accounts (`core.go:2575–2577`), but other asynchronous work still uses the ordinary job registry. That branch difference alone does not establish successful terminal webhook delivery.

## Clean Kafka2 run: Direct account passes

The arena was recreated, and `direct_success` was run first, before any failing Shared Kafka case. [The result](runs/profile-kafka2/direct-account/route-results.json) passes for payout `pout_TYMiKD1RmGs5Ps`: Payouts `processed`, FTS `PROCESSED`, UTR present, and exactly one signed `payout.processed` merchant receipt. [The complete trace and ending snapshot](runs/profile-kafka2/direct-account/route-direct_success.jsonl) record no payout Ledger journals or entries, consistent with this Direct-account flow; it is not Shared Ledger accounting coverage.

[The transport capture](runs/profile-kafka2/direct-account/route-evidence.json) confirms the real Kafka consumer received the raw FTS message and records a direct-create response. It records neither a direct HTTP status callback nor monolith create/status delivery. Both `transport_observed` and scenario acceptance are true. [The egress audit](runs/profile-kafka2/direct-account/egress.json) passed with command exit 0 and zero outside packets.

The same capture retains two `PAYOUT_DUAL_WRITE_DISPATCH_TO_QUEUE_FAILURE` messages for this payout (`13:04:27.860Z` and `13:04:27.873Z`), each reporting the unregistered job. Thus Direct terminal success does not fix or disprove the process bootstrap gap, and it does not establish successful asynchronous dual-write. It verifies that this fresh Direct success branch can finish and deliver its signed terminal receipt while the Shared branch remains blocked at Ledger enqueue.

The earlier Shared failure and Direct-after-Shared timeout remain retained. The source payload mismatch and ignored FAILED/REVERSED statuses also remain; this passing Direct success case does not exercise their downstream failure or reversal behavior.

## Milestone 1 findings and corrections (2026-09-05)

This section is appended, not a rewrite: everything above stands as originally recorded. It documents what the Milestone 1 audit (`reports/claude-review/TWIN_V1_AUDIT_AND_NEXT_STEP.md`, sections 3, 5 and 8) found when it went back over the historical Direct-after-Shared timeout, and the twin-side corrections made as a result.

### (a) Direct-after-Shared timeout: root cause found and reproduced

The historical inconclusive timeout (section "Direct-account observation limit" above) was never explained at the time because `route-evidence.py` only retained log lines containing the payout id, and the process that actually failed had exited before it ever touched that payout's id. A bounded, independent reproduction was run and retained at `reports/implementation/runs/claude-audit-kafka-repro-20260905T152544Z/` (see its `NOTES.md`), with unfiltered consumer logs, `docker inspect` before/after each step and `kafka-consumer-groups --describe` polled throughout. It reproduces the mechanism exactly (audit section 3.7):

1. The Shared PROCESSED enqueue fails as always (`job should be registered before performing`); the message is retried and fails again; `HandleFailedMessage` runs.
2. `HandleFailedMessage` calls `provider.GetKafkaProducerProvider(ctx).PublishWithKey(...)` to republish to the retry topic. The getter returns a **nil interface** because the producer client cannot be built (`getter.go:161-166`, `container.go:110-113`): the twin's `[kafka_producer].Brokers` was `["broker:9092","broker1:9092"]`, the upstream `default.toml:429` placeholder carried unchanged into `arena.toml:460` -- neither name is a resolvable compose service. `config/routes.py:58-63` rewrites only the FTS producer, not this one.
3. The nil is never checked, and the vendored Kafka worker's `processMessages` goroutine has no `recover`, so the process **panics**: `taskHandlers.(*FtsStatusUpdatesTask).HandleFailedMessage` at `fts_status_updates.go:105`, from `pkg/kafka/worker.go:85`. The main consumer container exits code 2.
4. The twin's compose file had **no `restart:` policy** on any service (healthcheck was a bare `pgrep`), so the container stayed dead. The next scenario (Direct-after-Shared) then found no consumer-group member and timed out with the payout stuck `initiated` -- a crashed process, not a product-timeout or an ordering interference.

Classification (audit section 3.7): a **twin configuration defect** (placeholder brokers, no restart policy) exposing a **source robustness gap** (unchecked nil producer, unrecovered goroutine). In production the producer builds against real brokers, so this specific crash would not occur there, and production runs `restartPolicy: Always` regardless.

### (b) The fix: FD-001, and what remains unchanged (EF-001)

Recorded as twin defect **FD-001** (`reports/implementation/fixed-twin-defects.json`) and declared deviations **DEV-005** / **DEV-007** (`ENV2_COMPOSE/config/declared-deviations.yaml`, both `status: fixed_in_m1`):

- `ENV2_COMPOSE/config/templates/base/payouts/arena.toml` `[kafka_producer].Brokers` is now `["kafka:9092"]` (was `["broker:9092","broker1:9092"]`), matching the two Kafka consumers' `[kafka_consumers.*.config].Brokers`, which already resolved correctly.
- `ENV2_COMPOSE/docker-compose.yml` adds `restart: on-failure` to both `payouts-kafka-fts-status-updates-consumer` and its retry consumer, matching the deployed object's `restartPolicy: Always` (`kube-manifests` drift report, namespace `payouts`, application `prod-payouts`, revision 418, live replicas 3).

This fix corrects the twin-side crash only. **It does not, and must not, change the Shared-account source failure itself**: with jobs still not registered in the Kafka consumer's bootstrap (`internal/boot/handler.go:154` never reached from `boot_kafka_consumer.go:17-29`), the Shared PROCESSED enqueue still fails, the payout still stays `initiated`, and this remains a **production-source bootstrap defect, reproduced faithfully** -- recorded as expected failure **EF-001** in `TWIN_SPEC/expected-failures.yaml`. A kafka-profile rerun after the fix is expected to show: Shared golden fails at the Ledger enqueue exactly as before; the consumer container stays `running` with restart count 0; Direct-after-Shared passes; the retry topic receives the republished Shared message. Fresh-run outcome: as predicted. Fresh kafka-profile run `reports/implementation/runs/m1-20260905T165255Z/profile-kafka/golden-shared` (2026-09-05T17:06Z): payout initiated, FTS PROCESSED, no processed journal, no terminal webhook, `KAFKA_FTS_STATUS_UPDATE_TASK_FAILED` after the two source-configured attempts; both consumer containers `running` with restart count 0; the retry consumer group consumed the two republished Shared messages (retry topic offsets 2/2); `direct_after_shared` then reached `processed` with one signed receipt.

### (c) Monolith-side Kafka consumer asymmetry (DEV-150)

Production's API monolith consumes the same topic, `rx-fts-status-update-events`, under its **own** consumer group with **8 replicas** (`kube-manifests/cde/api/values.yaml:315`), and its handler (`api/app/Services/Kafka/Consumers/FtsStatusUpdateConsumer/Consumer.php:67-108`) calls the same `updateFundTransfer`/`FtaService::updateFundTransferAttempt` path used by the monolith's own HTTP relay (`Attempt/Core.php:487-560`).

That means production has a second, independent path to compensate the payouts-side Kafka DTO gap and the FAILED/REVERSED drop below -- but **only for a transfer whose FTA row was created by the monolith**. For a transfer created directly by Payouts (`X-Origin: payouts`, the PS-direct create path), the monolith holds no FTA row and cannot relay anything; there is no compensation at all.

The twin does not model this monolith-side Kafka consumer -- `monolith-stub` serves HTTP only. Its Kafka route profile therefore combines **PS-direct create with Kafka status delivery**, which is exactly the uncompensated combination: the twin's Shared/Direct Kafka failures are the worst case production can produce on this leg, not necessarily the typical one. This asymmetry is declared as **DEV-150** (`ENV2_COMPOSE/config/declared-deviations.yaml`, `status: declared`). Whether that PS-direct-plus-Kafka combination is actually enabled for any real merchant is not determinable from repository evidence (audit section 3.2, owner question Q2).

### (d) FAILED/REVERSED dropped with no fallback over Kafka

Both Kafka task implementations return `nil` on FAILED and REVERSED without applying any state change (`internal/taskHandlers/fts_status_updates.go:56-62` in the currently-referenced line range; see "(e)" below for the corrected numbering, and `fts_status_updates_retry.go:58-64`). The message is Acked and permanently dropped: no payout state change, no reversal row, no journal, no webhook.

The HTTP path has the equivalent adapter logic present three times -- `fts_transfer_status_webhook.go:515-525, 575-585, 630-638` -- so a monolith-relayed or direct-HTTP FAILED/REVERSED is handled; the Kafka consumer has no equivalent branch at all. Because FTS's own status-propagation choice is exclusive (`fts/internal/transfer/service.go:1139-1144` returns after the Kafka publish), a Kafka-routed merchant's FAILED/REVERSED **never reaches payouts-service over HTTP either**, unless the monolith's own consumer relays it for a monolith-created FTA (see (c)). This is recorded as expected failures **EF-002** (FAILED) and **EF-003** (REVERSED) in `TWIN_SPEC/expected-failures.yaml`, and as invariant **I71**.

### (e) Corrected line numbers

The FAILED/REVERSED return-nil branch in `internal/taskHandlers/fts_status_updates.go` is at **lines 56-62** (previously cited elsewhere in this project's working notes as 58-64); the retry-consumer equivalent in `fts_status_updates_retry.go` is at **58-64**. `TWIN_SPEC/expected-failures.yaml` and `ENV2_COMPOSE/verifier/kafka_scenarios.py` use the corrected numbering.

### (f) New kafka scenario family (I70/I71)

`ENV2_COMPOSE/verifier/kafka_scenarios.py` adds a family of five cases, run only under `ARENA_ROUTE_PROFILE=kafka` via `scripts/scenarios.sh --with-egress-audit kafka [--case ...]`. Every Kafka message observed is produced by the real FTS producer as a consequence of a real bank outcome through mozart-sim, and consumed by the real payouts Kafka consumer; nothing is published, injected or replayed by the test file itself.

| Case | Fixture | Asserts | Disposition |
|---|---|---|---|
| `shared_source_failure` | `scenario:kafka` / M1 (shared) | Shared PROCESSED over Kafka cannot enqueue the processed Ledger journal (EF-001) | expected_failure |
| `direct_after_shared` | `scenario:kafka` / M2 (direct) | regression test for the FD-001 consumer-crash fix: run immediately after `shared_source_failure` in the same boot, the Direct case must still complete (consumer stayed alive) | passed |
| `failed_dropped_direct` | baseline / M2 (direct) | FTS FAILED over Kafka is Acked and dropped, no payout state change (EF-002) | expected_failure |
| `failed_dropped_shared` | `scenario:direct_status` / M1 (shared) | same FAILED drop on a Shared-account transfer (EF-002) | expected_failure |
| `reversed_dropped_direct` | `scenario:monolith_relay` / M2 (direct) | FTS REVERSED over Kafka is Acked and dropped after a Direct payout has processed (EF-003) | expected_failure |

`direct_after_shared` refuses to run standalone: it must follow `shared_source_failure` in the same boot, because it is specifically the regression test for the consumer-crash mechanism in (a). Fresh kafka-profile run outcome for this family: all five cases as predicted in `reports/implementation/runs/m1-20260905T165255Z/profile-kafka/kafka/kafka-results.json` (shared_source_failure EF-001, failed_dropped_direct and failed_dropped_shared EF-002, reversed_dropped_direct EF-003, direct_after_shared passed), consumer state healthy for every case, egress audit passed with zero outside packets.
