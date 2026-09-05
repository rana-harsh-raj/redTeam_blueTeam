# Payouts Twin v1: audit verdict and next step

Audit date: 2026-09-05. Auditor: Claude (independent technical lead, Project RedGrid).
Scope: the Codex v1 candidate in this checkout (branch `twin-v1`, working tree uncommitted on top of `a5917f9`), the pinned
pristine clones (payouts `4bf3dbf9`, fts `2a09e763`, ledger `471ff4d5`, api `2d665f91`, kube-manifests `9226a892`), the
admitted build copies under `.local/twin-repos/accepted/`, the generated configuration, the verifier code, and every retained
run under `reports/implementation/runs/`. One new bounded runtime experiment was executed and retained at
`reports/implementation/runs/claude-audit-kafka-repro-20260905T152544Z/`. No historical run directory, company clone, or
core source file was modified. No Daytona action was taken.

Evidence vocabulary used throughout: **SOURCE** = read in the pinned pristine clone; **DEPLOYED** = read in kube-manifests
(templates, overlays, or the retained `kubectl diff` drift report); **TWIN** = observed in the twin's files or a retained run;
**ASSUMED** = not derivable from any available artifact.

---

## 1. Executive verdict: NOT READY (to freeze as Twin v1), by a short and bounded margin

The candidate is a real, reproducible execution environment: five real core binaries built from pinned source, two
independent from-empty boots that pass 26/26 verifier cases with matching recorded observations, twelve supplemental
route and bank cases, and same-window egress audits with zero outside packets. Those claims were re-verified against the
raw traces and hold.

It cannot be frozen as v1 today for five reasons, each of which is correctable in days, not weeks:

1. **The v1 implementation is uncommitted.** Every file Codex produced is a working-tree modification or an untracked file
   on top of the scaffold commit, and `reports/`, `runs/` and `.local/` are git-ignored. There is nothing to tag.
2. **Undeclared twin changes weaken production controls.** The ledger fault proxy silently strips the `idempotency-key`
   header on every Payouts-to-Ledger call, disabling Ledger's request-idempotency layer for that leg only; the ledger mutex
   lease is 500 ms instead of the production 60 s; API-key auto-enforcement is off where production has it on; the payouts
   error-mapping files are not staged into the image. None of these is in the fidelity matrix.
3. **A twin configuration defect crashes the real Kafka consumer.** The payouts producer config still carries the
   upstream placeholder brokers, so the consumer's failed-message republish dereferences a nil producer and the process
   exits. This is what the "inconclusive" Direct-after-Shared timeout was. It is now reproduced and explained (section 3).
4. **The acceptance gate is hand-authored.** No script computes `local-acceptance.json`; the Daytona runner only checks that
   ten booleans are literally true and that 133 evidence hashes match. A missing test cannot surface as a false boolean.
5. **P1 of the handoff is not complete and P0 is partial**, and several verifiers are weaker than the invariant they cite
   (section 2). The handoff's own acceptance tests 4.2, 4.3, 4.4 and 4.6 are not implemented.

None of the five requires touching core source behaviour. The Shared Kafka failure itself is faithful to source and must
be preserved (section 3). The right next move is a bounded correct-and-reaccept pass, then a freeze with declared
exclusions and a lean red loop (section 8).

---

## 2. Completion matrix (evidence-backed, condensed)

Classification key: **PR** proven through runtime execution; **CS** confirmed from source or twin files but not executed;
**BS** implemented through a bounded substitute; **AS** assumed; **BL** blocked; **NI** not implemented.
Every row was checked against the twin's current files and a named run artifact, not against Codex's prose.

### 2.1 CODEX_HANDOFF P0

| Item | Class | Evidence and weakness |
|---|---|---|
| P0.1 Splitz server-side eval, default off, seeded gates | PR / NI | `arena.toml:546 client_side_eval=false`; splitz-stub default `off`; 78 experiments × 3 merchants seeded; 1804 Evaluate calls in the replay20 window. **NI:** the acceptance "toggle MerchantConfigViaAsvAndDcs" cannot be executed, the stub has no runtime control. Three duplicate experiment names make by-name and by-id evaluation return opposite variants (stub log: `-2 by-name alias(es)`). |
| P0.2 cron-driver routes | PR | All 8 routes match `cron_routes.go` verbatim; live driver log 100 × HTTP 200, zero 404; reservation heartbeat key present. Two added routes return 200 but no worker consumes the jobs (P1.5). |
| P0.3 Ledger M2 Current seeds | CS | Seeds and live Postgres rows correct. Acceptance unmet: zero `ledger_entries` on any `FTS Current*` account; all 7 processed journals credit Nodal pools. The Current-parent discovery branch has never executed. |
| P0.4 monolith-stub corrections | PR / CS / BS | DB read via `monolith_reader` DSN proven; response shapes proven by 18 offline contract tests; `FTS_CREATE_MODE=error` never exercised in any run; V18 traverses the real relay but the Shared/Direct remap it checks is the stub's own table (`server.py:614-615`). |
| P0.5 API-DB DDL | PR | Live `SHOW TABLES` identical to `TWIN_SPEC/schema/apidb-subset.mysql.sql`. `features.name` widened 25→255 (labelled ASSUMED). |
| P0.6 queued balance mirror | BS | Implemented as arena control `POST /_arena/balance-sync`, not as `internal_balances_queued` behaviour; emitted only when a test asks. Route name corrected to the real `/v1/payouts/balance_update_event` (handoff prose was wrong). |
| P0.7 verifier corrections | PR with gaps | V04 asserts no unique index but has no non-empty guard on the index list; V06 asserts none of I21's named fields and never waits on `payout_update_failure_handling`; V10 matches substring `balance`, not the error code; V16 uses per-payout `/_arena/relay drop` (equivalent) with a 10 s window and is labelled weaker where the invariant asks for stronger; V20/V21 correct. |
| P0.8 Ledger processes | PR / CS | Five queue workers run and non-merchant balances advance (proven). Scheduler command correct but executed against zero split accounts (`rows:0`), so the behaviour never ran. |

**P0 verdict: PARTIAL.** Complete: P0.1a-c, P0.2, P0.4a/c/d, P0.5, P0.7a/c/d/f/g/h, P0.8b/c. Incomplete: P0.1d, P0.3, P0.4b/e, P0.6, P0.7b/e, P0.8a.

### 2.2 CODEX_HANDOFF P1

| Item | Class | Evidence and weakness |
|---|---|---|
| P1.1 mozart-sim | BS | Gateway/version parsed and discarded; outcomes driven by `/_arena/scenario`; 9 of ~18 FTS codes present, 7 exercised; hold is poll-count based; latency is a fixed 30 s sleep; RETURNED/return_utr proven (`route-returned.jsonl`). |
| P1.2 Kafka leg | PR / NI | Producer enabled only under `ARENA_ROUTE_PROFILE=kafka` (a generation-time variable, not a compose profile; broker and both consumers start on every boot). Per-merchant designation NI (all 96 merchants flip together). **I71 Kafka injection verifier NI.** |
| P1.3 Direct FTS→PS leg | PR | `profile-direct2` shows `direct_origin_metadata` and `direct_status_http` true. Achieved by config allow-list for all merchants, not the named Splitz experiment (seeded off). Evidence exists only in a side profile run, not in final-acceptance. |
| P1.4 FTS second channel / DOWN / default rule | NI | Live FTS DB: 10 `channel_information_status` rows, all RBL, all status 100; no `merchant_id IS NULL` rule; RBL-only workers; every transfer ever made is RBL/IMPS. |
| P1.5 ten missing workers | NI | 14 workers, unchanged since the scaffold commit; 11 still missing (`bulk_payouts`, `batch_submitted_merchants`, `fund_management_payout_check/initiate`, `data_consistency_*`, `payout_usage_event_processing`, `rbl_banking_account_statement`, `x_account_statement_source_event`, `x_balance_payouts_event`, `api_queue_for_async_dual_write_direct_push`). Eight have non-zero live replicas in `cde/payouts/values.yaml`. |
| P1.6 fault knobs | NI / BS | `STORK_DUPLICATE` absent; a generic `/_arena/faults` registry supplies delay/drop/status for shield and pricing (V22, V23 proven) but the contract header names do not exist and Stork faults are never exercised. |
| P1.7 kong-lite | NI / BS | No impersonation, admin or identification-only shapes; `X-Razorpay-Account` is forwarded unstripped; keys-table indirection is a no-op (key id equals merchant id). The entire 26-case suite bypasses Kong via `PS_PUBLIC_URL`; exactly one Kong request exists in the final-acceptance set (the golden run). |

**P1 verdict: NOT COMPLETE.**

### 2.3 CODEX_HANDOFF section 4 acceptance tests

| Test | Class | Note |
|---|---|---|
| 4.1 P0 verifiers pass | PR | 26/0/0 on replay19 and replay20; corrected expectations only partly delivered (above). |
| 4.2 per-mode fee assertion (I90) | NI | Plan seeded with distinct IMPS/NEFT/RTGS/UPI fees; no verifier asserts any fee against an expected number; UPI never created. |
| 4.3 Splitz toggle test | NI | No runtime toggle exists. |
| 4.4 Direct `payout_processed` journal | NI | The criterion contradicts I21 (Shared-only). Twin implements I21. The Current-parent path is unexercised either way. |
| 4.5 lost callback | BS | Equivalent drop through a different knob; 10 s window. |
| 4.6 Kafka injection proves reversed/failed drop | NI | No producer anywhere in the verifier. |
| 4.7 preflight and egress in the same window | PR | All final windows: exit 0, zero outside packets, in-window DNS control fails to resolve `example.com`. |

### 2.4 Invariants with no verifier at all

I04 (bulk), I12, I13, I24b (`transaction.created`), I31 (heartbeat fail-closed), I40c, I41b, I42 (on-hold), I50/I51/I52
(approval), I61b (unscoped internal route as documented characteristic), I70b, I71 (Kafka drop), I72 (relay ordering),
I80b, I81/I82 (XAS, blocked on a missing substitute), I90b/c.

### 2.5 Runtime evidence validity (independent check)

The replay19/20 traces show real HTTP calls to payouts-api, fts-web and ledger-api and real MySQL/Postgres reads scoped
to freshly generated payout ids. The egress capture starts before the command on the only two networks that exist and
is confirmed alive after it. The route/bank final-acceptance cases have unique payout ids cross-referenced into their
traces. The replay comparator never normalises statuses, amounts or journal entries, but it compares final state only, not
trajectory. Two caveats: V12/V13 trust payouts-api's own reservation self-report and never read Redis directly; the full
suite shares one database across 26 tests (assertions remain id-scoped).

---

## 3. Kafka: root cause and deployment status

### 3.1 The Shared-account failure is faithful to source

- **SOURCE.** `job.RegisterJobs()` has exactly one call site, `internal/boot/handler.go:154`, reached only from
  `boot_api.go:47` and `boot_worker.go:37`. The Kafka chain (`cmd/kafkaConsumers/main.go:18` →
  `boot_kafka_consumer.go:17-29` → `registerDefaultHandlersForKafkaConsumer` → `handleKafkaConsumer`) never calls it.
  `provider.GetWorker` lazily builds an empty manager (`pkg/worker/manager.go:111`), and `Perform` fails its registration
  guard (`manager.go:186-189`) with the exact string retained in the logs. Job `init()` functions populate only a map that
  `RegisterJobs()` would copy. No build tag, Makefile target, Dockerfile, devspace or slit compose entry registers jobs
  in the consumer; the pod's `args: [kafkaConsumer]` is consumed by `entrypoint.sh` and the binary parses no flags.
- **SOURCE.** For Shared accounts `core.go:2589-2595` propagates the enqueue error and `core.go:2472-2475` returns before
  `OnEvent(EventProcessed)`, so the payout stays `initiated`. No config or Splitz flag short-circuits this. Direct accounts
  return at `core.go:2575-2577` before the enqueue, which is why the Direct Kafka case passes.
- **TWIN.** The twin launches the same package with the same build tag, same task names, same `MAXCONCURRENCY=1`, and
  consumer config identical to `prod.toml:312-346` except brokers/TLS and the retry consumer backoff (5 s vs 30 s).
  The failure is not a wiring artefact. Verdict: **production-source bootstrap defect, reproduced faithfully. Preserve it.**

### 3.2 The consumer is deployed in production

- **DEPLOYED.** `kube-manifests/templates/payouts/templates/payouts-kafka-fts-status-updates-consumer.yaml` (plus the
  retry consumer) with no enable gate; `cde/payouts/values.yaml` sets 1/1 replicas. The retained drift report
  `devstack-prod-parity/reports/batch8/drift-check-20260703.html` shows the live object in namespace `payouts`,
  application `prod-payouts`, created 2022-02-17, revision 418, **live replicas 3**, `restartPolicy: Always`, HTTP
  liveness and readiness probes.
- **DEPLOYED.** FTS `env.prod-live.toml:318` has `[kafka_producers.fire_transfer_status] enabled = true` with the prod
  broker and topic `rx-fts-status-update-events`. The per-merchant gate is Splitz experiment `PYGTRQEzO39PfB`
  (`env.prod-live.toml:364`), fail-closed to false. **No fixture for that experiment exists in the splitz clone.** Whether
  any production merchant is actually on the Kafka route is **not determinable from repository evidence.**
- **DEPLOYED.** The API monolith consumes the same topic under its own group (`cde/api/values.yaml:315`, 8 replicas;
  `app/Services/Kafka/Consumers/FtsStatusUpdateConsumer/Consumer.php`), and its handler is the same
  `updateFundTransfer` used by the HTTP relay (`Attempt/Core.php:487-560`). For a transfer whose FTA row was created
  by the monolith, production therefore has a second consumer that relays status to Payouts over HTTP, where the DTO
  adapter exists. For a transfer created directly by Payouts (X-Origin `payouts`), the monolith has no FTA row and cannot
  compensate. The twin models neither the monolith consumer nor this asymmetry; its Kafka profile combines PS-direct
  create with Kafka status, the exact combination with no compensation. Whether that combination is enabled for any real
  merchant is an owner question (section 9).

### 3.3 Reachability

`fts_status_propagation` is strictly exclusive (`service.go:1139-1144` returns after the Kafka publish). Reachable when
FTS `enabled=true` (prod-live yes) AND Splitz `FireStatusUpdateKafka` on for the merchant (unknown). Account type does not
gate delivery; it gates only what the consumer does afterwards (Shared → Ledger enqueue, Direct → skip).

### 3.4 The DTO mismatch is real and independent

FTS publishes the flat transfer map with `source_account_id` and `bank_account_type`; the consumer unmarshals into
`fts_fund_account_id`/`fts_account_type`/`fts_status` tags with no alias or adapter (`dtos/payoutUpdate.go:8-16`,
`taskHandlers/fts_status_updates.go:42-54`). The HTTP path has the adapter three times
(`fts_transfer_status_webhook.go:515-525, 575-585, 630-638`). With jobs registered, the Shared processed journal would
be requested with empty account identifiers (`ledger_journal_create.go:216-217`), so Ledger source-account discovery
would still be wrong. The retained capture shows the empty `fts_info` meta being written. Two independent defects.

### 3.5 Product behaviour per status over Kafka (SOURCE)

| FTS status | Consumer branch | End state | Republish on error | Dropped |
|---|---|---|---|---|
| INITIATED | `HandlePayoutInitiated` | `initiated` (no-op if already) | yes | no |
| PROCESSED | `HandlePayoutProcessed` | Direct: `processed` + webhook. Shared: blocked at Ledger enqueue | yes | no |
| FAILED | `fts_status_updates.go:56-62` return nil | none | no (Ack) | yes, permanently |
| REVERSED | same | none | no (Ack) | yes, permanently |

Because the FTS choice is exclusive, a Kafka-routed merchant's FAILED/REVERSED never reach payouts-service unless the
monolith's own consumer relays them for monolith-created FTAs.

### 3.6 What the Direct Kafka success proves

That the real consumer boots, joins the group, receives the raw FTS message, updates details and status, and delivers a
signed terminal receipt for Direct accounts. It does not prove job registration (the same capture logs two
`PAYOUT_DUAL_WRITE_DISPATCH_TO_QUEUE_FAILURE` errors, swallowed by `dualWrite/payout.go:47-54`), any Shared accounting,
harmlessness of the DTO gap, or anything about FAILED/REVERSED.

### 3.7 Historical Direct-after-Shared timeout: reproduced and explained

A bounded reproduction (fresh kafka-profile boot, Shared golden then Direct immediately, with container inspection,
unfiltered consumer logs and consumer-group offsets captured throughout) is retained at
`reports/implementation/runs/claude-audit-kafka-repro-20260905T152544Z/`.

| Time (UTC) | Observation |
|---|---|
| 15:26:59 | Both consumer containers `running`, healthy, restarts 0. |
| 15:27:03 | Shared PROCESSED received; `QUEUE_PUSH_FAILED_FOR_LEDGER_PROCESSED_EVENT`; Nack; retry; `KAFKA_FTS_STATUS_UPDATE_TASK_FAILED` at 15:27:04. |
| 15:27:07.741 | `panic: runtime error: invalid memory address or nil pointer dereference` in `taskHandlers.(*FtsStatusUpdatesTask).HandleFailedMessage` (`fts_status_updates.go:105`), from `pkg/kafka/worker.go:85`. |
| 15:27:46 | Main consumer container `exited`, exit code 2, restarts 0, health unhealthy. Retry consumer still running. |
| 15:27:47 | Direct payout created; FTS PROCESSED with UTR within 2 s. |
| 15:28:18 | Direct scenario times out; `kafka_consumer_message=false`; consumer group has no member; partition lag 2. |
| 15:30:19 | Payout still `initiated` 120 s later. Retry topic log-end offset 0 (republish never happened). |

Mechanism: `HandleFailedMessage` calls `provider.GetKafkaProducerProvider(ctx).PublishWithKey(...)`. The getter returns a
nil interface when the container cannot build the producer (`getter.go:161-166`, `container.go:110-113`). The build
fails because the twin's payouts `[kafka_producer].Brokers` is `["broker:9092","broker1:9092"]`, the upstream
`default.toml:429` placeholder carried unchanged into `arena.toml:460`; the route profile rewrites only the FTS producer
(`routes.py:58-63`). The nil is not checked, and the `processMessages` goroutine has no recover, so the process dies.
The twin's compose file has no `restart:` policy on any service and the healthcheck is a bare `pgrep`.

Classification: **twin configuration defect** (placeholder brokers) exposing a **source robustness gap** (unchecked nil
provider, unrecovered goroutine). In production the producer builds against real brokers, so this specific crash would not
occur; production also has `restartPolicy: Always`. Fixture contamination and timing noise are ruled out (distinct
merchants, balances, accounts; other workers progressed during the gap). The historical run's evidence could not see this
because `route-evidence.py` keeps only log lines containing the payout id.

---

## 4. Recommended acceptance-gate treatment

The current gate conflates two different things, and it also has no producer. Recommendation: **a specific twin defect
must first be fixed, and then fidelity acceptance passes while product-route health for Shared Kafka remains failed.**
Do not exclude Shared Kafka from the declared v1 scope: it is the one route where the twin demonstrably reproduces a
production-source defect, which is exactly what a red twin is for. Do not weaken it into green.

Concrete changes to the machine-readable gate:

1. **Generate it.** Add `ENV2_COMPOSE/scripts/local-acceptance.py` that derives every boolean from named artifacts
   (junit counts, `route-results.json`, `egress.json`, `logical-replay-final.json`, audit summaries) and refuses to write
   a check it cannot compute. Keep the hash-bound evidence list. The Daytona runner keeps consuming the same file.
2. **Split `scenario_assertions` into two keys.**
   - `route_fidelity` (required): for every in-scope route and profile, transport observed AND the ending state equals
     the source-predicted state, where a source-predicted failure counts as a pass when it is declared in a new
     `expected_failures` list with a source citation. Entry for Shared Kafka: expected `payouts=initiated,
     fts=PROCESSED, no processed journal`, cite `handler.go:154` absence and `core.go:2472-2475`.
   - `product_route_health` (informational, not required for freeze): the business route completed. Shared Kafka is
     `false` with the same citation. This is the number a red-agent judge later consumes.
3. **`no_unexplained_results`** becomes computable: true only when every failed or timed-out case in the retained run set
   maps to an entry in `expected_failures` or in a `twin_defects_fixed` list with the fixing commit. After the producer
   broker fix and a kafka-profile rerun, the historical timeout maps to a fixed twin defect and this check turns true.
4. **Add `declared_deviations`** (section 5) so the gate carries the fidelity ceiling with it.
5. **Record the route profile the run set covers.** The committed `generated/` is the monolith profile; the gate must say
   which profiles were executed (monolith, direct, mixed, kafka) and which scenarios ran under each.

---

## 5. Core patch and fidelity-risk summary

### 5.1 Source-level changes (admitted copies vs pristine clones)

Only 8 files differ across the five services; nothing was added.

| Change | Location | Why | Class | Could conceal | Blocks freeze |
|---|---|---|---|---|---|
| `ARENA_DCS_URL` context override | payouts `pkg/dcs/client.go:54-57`, cfa `internal/dcsservice/client.go:72-75`, x-balances `pkg/dcs/client.go:47-50`, plus one-line `GetContextUrl` patch in three `goutils/dcs` module copies | SDK derives host from a hardcoded env map; pristine binaries would dial a real host | behaviour-changing, env-gated, declared | DCS endpoint-resolution failures | No |
| `ARENA_STORK_JSON=1` Twirp JSON client | payouts `pkg/stork/client.go:107-110` | avoid Protobuf codegen for the Stork substitute | behaviour-changing, declared | Protobuf wire regressions; the env var is set on every payouts container so the Protobuf path is never exercised | No, state it |
| `go.mod` replace to local dcs copy | payouts/cfa/x-balances `go.mod` | carry the patch | configuration-only | none (absolute host path hurts reproducibility) | No |
| Two comment redactions | fts `encryption/sign.go:24`, ledger `idempotency/repo.go:44` | sanitisation | safety-only; code tokens verified identical | none | No |

Ledger RPC regeneration changes only descriptor path prefixes (all `protobuf:`/`json:` tags identical); ledger migrations are
byte-identical to the pristine `rx_migrations`. No production gap was silently fixed: no unique index on the ledger journal,
no workflow expiry, no stuck-initiated repair, no self-approval check, no Kafka job registration, no DTO translation on the
Kafka path, no FAILED/REVERSED handling, no savings/saving normalisation. The savings gap is preserved in code but the
default fixture routes around it (null account type), so v1 does not observe it.

### 5.2 Undeclared twin changes that weaken production controls (must fix or declare before freeze)

| Change | Location | Class | Conceals | Verdict |
|---|---|---|---|---|
| ledger-gate forwards only 5 headers and drops `idempotency-key`, `Request-ID`, `Trace-ID`, `Country-Code` | `substitutes/ledger-gate/server.py:15`; ledger `pkg/idempotency/interceptor.go:115-118` short-circuits on empty key; payouts routes Ledger through the gate (`generated/payouts/arena.toml:319`) | behaviour-changing (removes a control) | Ledger request-idempotency on the payouts leg; the app-level transactor dedupe (I03) still applies, so V04 is measuring one fewer layer than production. FTS and the monolith reach ledger-api directly and keep it | **Fix the substitute** (forward the SDK header set). Not a core patch. |
| Ledger `mutex.ttl` 500 ms (inherits `default.toml`) vs `prod-live.toml:326` 60000 | `arena.toml:294` | behaviour-changing (concurrency) | lock expiry under any concurrency probe | Set 60000 or declare |
| payouts `features.ikey_auto_enforcement=false` vs `prod.toml:664 true` | `arena.toml:758` | behaviour-changing | production idempotency-key enforcement path untested by V01/V02 | Set true |
| `payouts/files/error/*.json` not staged; `/app/files` absent in the running container | `build-host.sh` asset staging | silent degradation | bank error-code to failure-reason mapping | Stage the files |
| `[kafka_producer].Brokers` placeholder | `arena.toml:460` | wiring defect | crashes the consumer on any failed message (section 3.7) | Set `["kafka:9092"]` |
| `mozart.httpclient.resiliency.circuitbreakertimeout` 220000 vs prod 10000 | `arena.toml:267` | behaviour-changing (timing) | bank-gateway timeout failure mode; note the 30 s gateway-timeout bank case relies on it | Declare, or align and re-express the bank timeout case |
| FTS `payouts_service.update_fts_fund_transfer.TIMEOUT` 10 vs prod 300 | `routes.py:53` | behaviour-changing (timing) | slow-but-successful becomes retried | Align to 300 |
| No `restart:` policy on any container; consumer healthcheck is `pgrep` | `docker-compose.yml` | deployment parity | a crashed consumer stays dead where production restarts it | Add `restart: on-failure` to the two consumers, declare |

Other configuration differences are large in count (hundreds of keys) but are endpoint or credential substitutions, or
declared representative choices (ledger `appMode=test`, split accounts off, x-balances in-memory queue, all Splitz variants
ASSUMED, FTS worker concurrency map undeclared). They belong in `declared_deviations`, not in a fix list.
`in_flight_reservation.*` and `client_side_eval` match production exactly.

---

## 6. Freeze decision: NO FREEZE NOW; freeze after milestone 1 with declared exclusions

What blocks the freeze is entirely twin-side and bounded: commit and tag; fix the five wiring/config items in 5.2 that
have production values (brokers, ledger-gate headers, mutex ttl, ikey enforcement, error files) and declare the rest;
replace the hand-written gate with a generated one; close the four cheap verifier holes (V04 guard, V06 I21 fields, I71
injection, V16 label); rerun two from-empty replays plus the kafka profile. Expected effort is days.

Declared exclusions at freeze: workflow/approval (blackhole), XAS/statements, bulk caller, real Stork lifecycle,
second channel and DOWN routing, the 11 missing workers, kong-lite impersonation/admin shapes, per-mode fee assertions,
and Shared Kafka product-route health (source-faithful failure).

---

## 7. Minimum requirements before the first limited red-agent experiment

The harness is closer than the product coverage: a stable programmatic interface exists (Kong merchant keys and minted
passports, create/fetch/cancel, mozart-sim outcome control, real Ledger top-up, DB ground truth via the verifier helpers),
usable oracles exist (`ledger_entries_sum_to_zero`, journal counts, transfer/payout state joins, reservation reads,
webhook capture), and a from-empty boot takes about one minute warm. What must change is scoped by tier.

**Must resolve before freezing Twin v1** (section 6): commit/tag; the five config/wiring fixes; generated gate; verifier
hole closures; kafka-profile rerun with the crash gone.

**Must resolve before the first red-agent experiment** (hours to a day each, all substitute-side):

1. **Approval/workflow.** Today any workflow-applicable create returns 400 because `[workflow] host` is a loopback
   blackhole, so the canonical approval-bypass and self-approval surface is unreachable. Implement the already-specified
   `workflow-service.md` contract as a thin Twirp stub (Create returns initiated, holds pending state, exposes an approve/
   reject control that calls the real PS internal callbacks). Do not stand up Cadence yet.
2. **Identity boundaries.** No new code: the real PS internal routes (`payout_internal_routes.go:12-18,71-80`) accept seven
   shared service credentials with no tenant scoping; this is a documented production characteristic (I61b), not a
   finding. Record it as a known-gap baseline so the judge does not credit its rediscovery, and give the agent only the
   merchant-key actor in episode 1; the service-credential actor is a separate, later actor class.
3. **Stork duplicate delivery knob** (`STORK_DUPLICATE`, ~10 lines next to `STORK_REORDER`) so webhook-replay idempotency
   can be exercised against the merchant sink.
4. **One DOWN `channel_information_status` row and one second-channel route set** (SQL only) so partner-bank-down,
   on-hold and default-routing paths are reachable.
5. **Harness pieces:** an invariant-checker module assembled from the existing helpers, scoped to public routes for the
   tenant invariant; per-episode merchant namespace allocation from the generator (32 isolated namespaces already exist);
   a judge that reads the trace and DB, not service logs; a known-gaps list the judge consults (I03 race, I52, I61b,
   I70b, Kafka drop) so rediscoveries are scored as calibration, not findings; logging or gating of the unauthenticated
   `dcs-stub PUT /v1/kv/put` so an agent cannot silently rewrite its own guardrails.

**Can be promoted later, when an agent finds a promising path:** XAS statement ingestion and UTR matching (the
monolith-side `payout_update` relay already exists; the matcher is about a day), bulk approve via Batch (bulk create is
already reachable through Kong), real Stork lifecycle, gateway/version branching in mozart-sim, the remaining workers,
kong-lite impersonation/admin passports, real Workflow Service with Cadence, ASV gRPC, UPS.

---

## 8. Next two milestones

### Milestone 1: correct and re-accept the existing twin (freeze candidate v1.0)

- **Goal.** A tagged commit whose generated acceptance gate passes `route_fidelity` on all four route profiles with
  Shared Kafka recorded as a source-faithful expected failure, with every deviation in section 5.2 fixed or declared.
- **Why first.** Every blocker is twin-side, small, and would otherwise contaminate red-agent results (a crashing consumer,
  a missing idempotency layer, a hand-set gate). Correcting first costs days and removes the largest sources of false
  findings.
- **Boundaries.** ENV2_COMPOSE, verifier and reports only. No core source change. No new product family. No Daytona.
  Preserve all historical run directories.
- **Completion criteria.**
  1. `git tag twin-v1.0` on a commit that includes ENV2_COMPOSE, TWIN_SPEC, reports/implementation (runs excluded by
     policy but hash-listed in the gate).
  2. `arena.toml` payouts `[kafka_producer].Brokers=["kafka:9092"]`; kafka-profile rerun: Shared golden fails at the Ledger
     enqueue as before, consumer container remains `running` with restarts 0, Direct-after-Shared passes, retry topic
     receives the republished Shared message.
  3. ledger-gate forwards `idempotency-key`, `Request-ID`, `Trace-ID`, `Country-Code`; ledger `mutex.ttl=60000`;
     `ikey_auto_enforcement=true`; `/app/files/error` present in the payouts image; FTS PS webhook `TIMEOUT=300`;
     `restart: on-failure` on both consumers. Each recorded in `declared_deviations` or removed.
  4. `local-acceptance.json` produced by a script from artifacts; `route_fidelity=true`, `product_route_health` lists
     Shared Kafka false with citation; `no_unexplained_results=true` because the historical timeout maps to the fixed
     defect.
  5. Verifier: V04 asserts the index list is non-empty before the negative; V06 asserts `fts_fund_account_id` and
     `fts_account_type` on the processed journal identifiers and the `account_details.fund_account_type` under the
     matching parent; new I71 test publishes FAILED and REVERSED messages with the topic's key convention and asserts no
     payout change; V16 label matches the invariant. Two from-empty replays 26/0/0 plus the I71 test, logically equivalent.
  6. Documentation: FIDELITY_IMPLEMENTATION_MATRIX gains rows for each item in 5.2; KAFKA_ROUTE_EVIDENCE gains the
     crash finding and the monolith-consumer asymmetry.

### Milestone 2: freeze v1.0 and run a lean autonomous red loop (first limited experiment)

- **Goal.** One red agent, one merchant actor, running N bounded episodes against a warm twin, with an automated judge,
  producing a scored findings ledger in which known documented gaps are recognised as calibration.
- **Why second.** The twin's reachable surface (create, cancel, idempotency, balance authorisation, reservations, bank
  outcomes, webhooks, plus the thin workflow stub) is already large enough to test the loop mechanics: interface, reset,
  oracle, judge, evidence retention. Expanding a family first would delay learning what the loop needs.
- **Boundaries.** Substitute-level additions only (workflow stub, Stork duplicate knob, DOWN/second-channel seeds,
  invariant-checker module, judge, episode runner). No production, staging or DevStack access. No Daytona. Merchant-key
  actor only; service-credential actor deferred. Agent traffic through kong-lite, not `PS_PUBLIC_URL`.
- **Completion criteria.**
  1. Episode runner boots or resets, allocates an isolated namespace, runs the agent for a fixed budget, executes the
     invariant checker, and retains trace, DB snapshot and judge verdict per episode; 20 consecutive episodes complete
     without harness failure.
  2. Calibration: the known-gaps list (I03 race, I52 no expiry, I61b, Kafka FAILED/REVERSED drop, stuck-initiated no
     repair) is presented to the judge; at least two are rediscovered by the agent and scored as calibration, zero are
     scored as new findings.
  3. False-positive gate: every judge-flagged finding is reproducible by replaying the episode's recorded requests
     against a fresh boot; findings that do not replay are discarded automatically.
  4. Workflow stub: a payout reaches `pending`, an approve callback moves it to `processed`, a reject to `rejected` with a
     `payout.rejected` webhook; the self-approval path (same actor property) is exercised and recorded.
  5. Duplicate webhook delivery is exercised once and the merchant sink's handling recorded.
  6. A written decision on which family to promote next, driven by the episode ledger (expected candidates: XAS
     statement replay, bulk approve, service-credential actor).

Daytona portability is deliberately third: it requires company transfer approval and adds nothing to fidelity or to the
loop design. Expanding an architecture family first is deferred because section 7 shows the reachable surface is already
sufficient for a first loop and the cheap additions unlock the highest-value families.

---

## 9. OWNER QUESTIONS (not answerable from available source or runtime evidence)

| # | Question | Exact artifact required | Likely owner |
|---|---|---|---|
| Q1 | Is Splitz experiment `PYGTRQEzO39PfB` (`fire_status_update_kafka`) on for any production merchant, and which segments? | Redacted Splitz experiment export (variants and rollout %) for that experiment id, revision-stamped | FTS platform / Payouts routing owners |
| Q2 | Is any merchant simultaneously on PS-direct create (`configs.fts_request_from_ps` whitelist or `ExperimentForFtsRequestFromPayoutsService`) and on the Kafka status route? This is the combination with no monolith-side compensation. | Same Splitz export plus the DCS/config values for `fts_request_from_ps` whitelist/blacklist in prod | Payouts service owners |
| Q3 | Has `PAYOUT_DUAL_WRITE_DISPATCH_TO_QUEUE_FAILURE` or `QUEUE_PUSH_FAILED_FOR_LEDGER_PROCESSED_EVENT` ever appeared in the production Kafka consumer pod logs? A non-zero count confirms the bootstrap defect is live; zero suggests the route is not exercised. | Sanitized log count by message name for `payouts-kafka-fts-status-updates-consumer` over 30 days (no payloads) | Payouts on-call / observability |
| Q4 | Partition count of `rx-fts-status-update-events` and `rx-fts-status-update-retry-events` in prod, and whether the consumer-lag alerts (which reference `prod-fts-status-update-events`, a name matching neither repo config) fire on the real topic | `kafka-topics --describe` output for both topics; the effective alert rule with its topic label | Kafka platform / alert-rules owners |
| Q5 | Effective values of `PAYOUTS_FEATURES_*DUAL_WRITE` in the prod secret | Names-and-values export for those keys only, or a statement of which are true | Payouts service owners |
| Q6 | Physical DDL for `payout_details.beneficiary_bank_code` and `features.name` width | `SHOW CREATE TABLE` for `payouts.payout_details` and `api.features`, schema only | Payouts DBA / API DBA |
| Q7 | Cadence server version and the `workflow_configs` rows for `payout-approval` (needed only when promoting the real Workflow Service; not needed for the thin stub) | Non-secret config export and version manifest | Workflows service owners |

Everything else raised by Codex's blocker list (FastCron cadence, SQS visibility/DLQ, engine versions, pricing breadth)
remains ASSUMED but does not block milestones 1 or 2 and is not repeated here.

---

## Appendix A: audit inputs and provenance

- Six investigation lanes (source Kafka chain and deployment; P0/P1 completion; core patches and configuration diff;
  historical timeout; red-agent readiness; runtime-trace validity), each independently re-reading source and artifacts.
  Lane notes are retained in the session scratchpad; every claim above was spot-verified by the auditor before inclusion,
  including the ledger-gate header allowlist, the ledger prod `mutex.ttl`, the payouts `ikey_auto_enforcement` values, the
  absent `/app/files`, the drift-report deployment object, the monolith Kafka consumer handler, and the vendored Kafka
  library's unrecovered panic path.
- New runtime evidence: `reports/implementation/runs/claude-audit-kafka-repro-20260905T152544Z/` (NOTES.md inside). The
  arena was returned to the monolith profile after the experiment.
- Corrections to Codex's Kafka evidence document: the FAILED/REVERSED branch is at `fts_status_updates.go:56-62` and
  `fts_status_updates_retry.go:58-64` (one-line drift); the DTO gap is an adapter present on HTTP and absent on Kafka,
  not merely a missing field; FAILED/REVERSED on Kafka have no fallback to payouts-service; the "inconclusive" timeout is
  a consumer crash caused by the twin's placeholder producer brokers.
