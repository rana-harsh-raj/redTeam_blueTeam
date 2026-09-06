# xas-sim — x-account-statements (XAS) substitute contract (Milestone 4, T11)

Replaces `xas-sink` (health-only placeholder, see its CONTRACT.md) with a
contract-faithful substitute for the parts of x-account-statements the twin's
real services actually talk to. Source of every rule: pristine clone
`x-account-statements@e73fd5a` (paths below are relative to it), the proto
tree `proto@5268257`, and the callers `payouts@4bf3dbf` / `fts@2a09e76`.

Classification: **substitute** (stdlib Python, `_common/base_stub.py`
skeleton, in-memory state). Fidelity labels used by the M4 proof
(`RED_LOOP/surface/m4_xas_ledger.py`): `real` (accepted binaries), `substitute`
(this process), `substitute_injected` (rows/events the harness put in).

## What is reproduced

| Area | XAS source | Reproduced behaviour |
|---|---|---|
| Source-event consumer | `internal/job/source_processing/source_processing.go:25-116`, `internal/source_events/service/service.go:50-160`, `internal/source_events/model/dto.go` | Long-polls SQS `x_account_statement_source_event` on `localstack:4566` (the queue payouts' `[job].x_account_statement_source_event` names; created idempotently at boot if absent). Body is the bare JSON of payouts' `job.XasEventDetails` (`PerformWithoutSerialization`). Read with XAS's field names: `entity_id, entity_type, utr, event_create_timestamp, event_id, gateway_ref_no, cms_ref_no, status, mode, amount, balance_id`. `Validate()`: at least one of utr / gateway_ref_no / cms_ref_no else `SourceEventInvalid` (message deleted, nothing stored). Persistence key `UNIQUE(event_id, entity_type)`; a duplicate reuses the stored row (`getEventForRetry`) and `_replays` is incremented. |
| **Schema mismatch (kept)** | payouts `internal/job/x_account_statement_source_event.go:41` emits `event_created_timestamp`; XAS `dto.go:15` reads `event_create_timestamp` | Reproduced, not fixed: the stored `event_create_timestamp` is `0` for every PS event; both spellings are logged per event (`source_event schema: event_created_timestamp(PS)=… event_create_timestamp(XAS)=…`) and recorded on the row under `_schema`. |
| Statement store | `internal/database/model/account_statements` (same columns as PS `banking_account_statement` + `fetched_at_nano`), RBL dedupe tuple `fetch/channels/banks/rbl/rbl.go:740-750` | Rows arrive through `POST /_arena/statements/sync` (PS-table mirror, see below); dedupe tuple `{account_number, posted_date, type, bank_serial_number, amount, channel, bank_transaction_id}`; the PS `id` is kept so `bas_id` is the same identifier in PS and XAS (as the PS↔XAS migration keeps it). One statement-driven enrichment per NEW row (fetch service `:265-290` enqueues one `statement_enrichment` job per saved row). |
| Accounts | `accounts` table `UNIQUE(account_number, channel)`, `balance_id` | `POST /_arena/accounts` (prod: `new_merchant_onboarding` job / `POST /v1/accounts`, not modelled). Sync rows carrying `balance_id` register the account implicitly. |
| Matching keys and order | `enrich/service/service.go:190-218` (`FetchEntityForEnrichment`), `repo/repo.go:268-341` | Identifiers tried **utr → gateway_ref_number (UPPER, case-insensitive) → cms_ref_number (= statement `bank_transaction_id`)**; the first key that returns ≥1 row wins. Statements are filtered by `account_number AND amount`, source events by `balance_id AND amount`. No merchant_id, currency, mode or date window. |
| Statement-driven enrichment | `EnrichWithStatement` `:44-91` | account by (account_number, channel) → balance_id; no source event → `entity_type = external`; else enrich with `events[0]`. |
| Event-driven enrichment | `EnrichWithSource` `:93-148`, guards `:343-357` | accounts by balance_id (none → `AccountNotFound`, no-op); statements by key order; 0 → no-op; **>2 → `TooManyStmtsForUtr` no-op**; **two of the same `type` → `UnexpectedStmtType` no-op**; else enrich each (debit + credit). |
| PayoutEnricher | `enrich/service/enrichers/payout_enricher.go:38-63` | credit → `payout_reversal`, debit → `payout`; `entity_id = event.entity_id`. **Relink refusal**: a statement with a non-empty, non-`external` `entity_type` and a different `entity_id` is left untouched (`StatementAlreadyLinkedError` logged). Same entity → re-set, no change. |
| Outbound dual-write | `dual_write/service/service.go:113-170,173-190,266-290`, `gateway/api/params.go:38-49`, `gateway/api/service/service.go:34-35,108-127` | After an enrichment write whose row changed and whose `entity_type ∈ {payout, payout_reversal}` (the CDC `Update` handler), `POST {XAS_API_BASE_URL}/v1/banking_account_statement/payout_update` with exactly the 9 fields `{bas_id, entity_id, entity_type, merchant_id, transaction_date, converted_from_external, utr, grn, cms_ref_no}`, HTTP Basic from `XAS_API_AUTH_FILE` (`user:pass`, the monolith-stub inbound credential). `converted_from_external = true` iff the previous `entity_type` was `external`. `POST /v1/statement/dual_write {id}` re-sends unconditionally (`DualWriteForStatement`) — even for `external` rows, which the monolith then rejects (400), as it would. |
| `GET /v1/account_statements/fetch_multiple_by_reference_numbers` | `get/service/service.go:425-480`, `get/validate/validate.go:174-215`, `proto x/x-account-statements/statement/v1/statement.proto` | Query params `type, account_number, amount, channel, utr, gateway_reference_number, cms_reference_number`. Validation: ≥1 reference else 400; `account_number, amount, channel` required else 400 (Foundation-style `{"error":{code, description, …}}`). Rows fetched by the same key order with `account_number + amount`, then filtered by `type` (if given) and `channel` (always). Response `GetAccountStatementsResponse` `{statements:[AccountStatementData…], page, limit, total_pages, total_count, has_more}` — proto `int64` fields (`amount, balance, transaction_date, posted_date, created_at, updated_at, fetched_at_nano, total_count`) are serialised as **JSON strings** exactly as grpc-gateway does, which is what `payouts/pkg/xAccountStatement/fetch_by_multiple_references.go` and `fts/internal/providers/xas/fetch_by_multiple_references.go` unmarshal (`Amount string`, `TotalCount string`). `total_count` is the unfiltered count (`len(accountStatements)` in the handler). Every call is recorded in `/_arena/state.fetch_log` (caller, Basic user, request, matched key, returned ids and entity types) — this is the evidence for the REAL PS `VerifyPayoutFailedTransaction` (`fts_transfer_status_webhook.go:654-720`) and FTS retry-preprocessor gates. |
| `GET /v1/account_statements` | `statement_api.proto:16` | Minimal listing with equality filters (harness convenience; pagination not modelled). |
| `POST /v1/statement/enrich_statements {id}` | `dual_write/service.go EnrichStatement :198-220` | Re-runs statement-driven enrichment for an unlinked/external row. |
| `POST /v1/source_event/fetch` | `proto …/source_event/v1/fetch_api.proto:12` | **501** — the pull fallback needs the monolith `GET /v1/payouts_internal/{id}/source_event_info`, which monolith-stub does not serve. Reported, not faked. |

## Arena control plane (not XAS routes)

| Route | Purpose |
|---|---|
| `POST /_arena/statements/sync {statements:[PS banking_account_statement rows], label?, enrich?}` | Mirror REAL PS rows into the store (the harness reads `payouts.banking_account_statement` and posts them verbatim). Label defaults to `substitute_injected`; the M4 proof uses `ps_table_mirror`. |
| `POST /_arena/accounts {merchant_id, account_number, channel, balance_id, account_type?}` | XAS `accounts` row. |
| `POST /_arena/source_events/enqueue <PS XasEventDetails JSON>` | `SendMessage` on the REAL queue so the consumer path runs (label `substitute_injected`; only for merchants whose PS producer gate is off). |
| `POST /_arena/source_events <JSON>` | Handle in-process (bypasses SQS). |
| `GET /_arena/state[?merchant_id=]` | accounts, statements, source_events, links (enrichment audit), outbound (payout_update calls), fetch_log, consumer counters. |
| `POST /_arena/reset?merchant_id=` | Per-merchant reset only (400 without `merchant_id`). |
| `GET|POST /_arena/faults` | Shared scoped fault registry (`_common/faults.py`): delay / drop / status per merchant or entity id. |
| `GET /health` | 200, unauthenticated. |

## Declared deviations (also in `config/declared-deviations.yaml`, DEV-160..)

1. **Ingestion not modelled**: XAS fetch workers (Mozart RBL/AXIS/ICICI/IDFC/YESBANK/SLICE) are absent; the store is a mirror of the REAL PS `banking_account_statement` table. Rows reach PS either from the real `rbl_banking_account_statement` worker (T10) or, in the M4 proof, from the harness in the worker's exact output shape (`substitute_injected`).
2. **Kafka CDC dual-write not reproduced** (Maxwell topics `mysql_cdc_events_*`): the outbound `payout_update` is issued synchronously right after the enrichment write. Ordering/latency of the CDC hop is therefore absent; the `dual_write` API hook (`POST /v1/statement/dual_write`) is the documented alternative and is served.
3. **Elasticsearch sync** (`FeatureFlags.SyncElasticsearchDoc`) absent.
4. **Inbound auth open**: no `STUB_BASIC_AUTH_FILE` is configured; the Basic user PS (`[xas.auth] username = "payouts"`) and FTS (`[xas.auth] key/secret`) send is recorded in `fetch_log.auth_user` but not verified (XAS validates it against `Server.Auth`).
5. **Retry semantics**: XAS `source_processing` has MaxRetries 3 / 60 s; this consumer processes each message once and deletes it (invalid events are dropped and logged, as XAS would after its retries).
6. **State is in-memory** (lost on restart; `/_arena/state` is the evidence surface). The real XAS keeps MySQL tables.
7. **Migration/legacy fallback** (`banking_account_statement` read-only copy, `fetchFromAPIDB`) absent — XAS itself has the API-DB fallback commented out (`get/service.go:475-478`).

## Runtime (dev-run and compose transcription)

Build (generic substitutes Dockerfile; `awslite.py` is copied by `${STUB_DIR}/*.py`):

```
docker build -f ENV2_COMPOSE/substitutes/Dockerfile --build-arg STUB_DIR=xas-sim --build-arg STUB_NAME=xas-sim --build-arg STUB_PORT=8080 \
  -t rzp-arena/xas-sim:${ARENA_TAG:-local} ENV2_COMPOSE/substitutes
```

Dev run used on the live arena (exact command; healthcheck is the image's `wget /health`):

```
docker run -d --name env2_compose-xas-sim-1 --network rzp-arena --network-alias xas-sim \
  --read-only --tmpfs /tmp --security-opt no-new-privileges:true \
  -v rzp-arena-secrets-monolith:/run/secrets:ro \
  -e STUB_PORT=8080 -e STUB_NAME=xas-sim \
  -e XAS_API_AUTH_FILE=/run/secrets/monolith_basic_auth -e XAS_API_BASE_URL=http://monolith-stub:8080 \
  rzp-arena/xas-sim:v1-candidate
```

Environment: `XAS_SOURCE_EVENT_QUEUE` (default `x_account_statement_source_event`), `XAS_SQS_ENDPOINT` (`http://localstack:4566`), `XAS_CONSUME_SOURCE_EVENTS` (`1`), `XAS_API_BASE_URL` (`http://monolith-stub:8080`), `XAS_API_AUTH_FILE` (`/run/secrets/monolith_basic_auth`), `XAS_POLL_WAIT_SECONDS` (`5`). Depends on: `localstack` (queue), `monolith-stub` (outbound). Callers: payouts (`[xas].host`), fts (`[xas].host`) — both set to `http://xas-sim:8080` by T10.

`awslite.py`: LocalStack does not verify SigV4 signatures (verified live with `Signature=deadbeef`), so a SigV4-shaped `Authorization` header with a constant signature is sent; queue URLs are built as `http://localstack:4566/000000000000/<name>` because LocalStack's own `GetQueueUrl` answer uses a host-only hostname.

## Validated

- Offline: `substitutes/xas-sim/test_contract.py` — 23 stdlib unittest cases (schema mismatch, validation, `(event_id, entity_type)` dedup, key order utr→grn→cms, amount/account/balance guards, external, >2 and dual-type guards, relink refusal, idempotent same-entity relink, 9-field outbound body, `dual_write` API, dedupe tuple, `fetch_multiple` validation/filtering/string-int64 serialisation/key order, per-merchant reset).
- Live (M4 proof, `reports/implementation/m4-xas-ledger.json`): REAL PS producer → SQS → consumer (mismatch logged, `event_create_timestamp=0`), match → 9-field `payout_update` → monolith-stub → REAL PS `UpdatePayoutAfterBASRecon`; REAL PS `VerifyPayoutFailedTransaction` consulting `fetch_multiple_by_reference_numbers` (see `fetch_log`).
