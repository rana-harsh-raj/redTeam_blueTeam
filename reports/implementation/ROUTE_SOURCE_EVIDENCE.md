# Route families and verified configuration contracts

Source revisions: Payouts `4bf3dbf9239feadea6d65ca90c893a988e116173`; FTS `2a09e763116db47a2f28553c677ad73ef8133ebf`; API `2d665f918b60e917ec92be648fb5816d247f72b1`. Findings below are source-confirmed, not evidence that a local scenario passed.

## Selecting existing branches

`ENV2_COMPOSE/config/routes.py` supplies `apply_route_profile(rendered_toml, service, profile)` for `payouts`/`fts`; call it **after rendering** and merge `route_experiments(profile, merchant_ids)` into the generated Splitz seed. Unknown profile rejects. The module validates resulting TOML and changes only route-specific fields. Application images must restart for config changes; Splitz seed must reload/restart too.

| Profile | Payouts transfer creation | FTS status delivery | Confidence / limit |
|---|---|---|---|
| monolith | Payouts → monolith create_fta → FTS | FTS → monolith details/status → Payouts | Source-supported; substitute FTA persistence/retries remain representative. |
| direct | Payouts async worker → FTS | FTS → Payouts transfer_status_webhook | Source-supported; must assert transfer_meta.origin_service=payouts and actual HTTP target. |
| direct-create-monolith-status | Payouts async worker → FTS | FTS → monolith → Payouts | Independently exercises direct creation with legacy return route. |
| kafka | Payouts async worker → FTS | FTS Kafka producer → Payouts Kafka consumer | Source-supported transport selection. **Source schema and terminal-state limitations below remain visible.** |

Payouts `internal/app/payouts/core.go:5931` uses `Configs.FtsRequestFromPs`. Its TOML is `[configs.fts_request_from_ps]` with **string** keys `enabled_env`, `whitelist_env`, `blacklist_env` (`internal/config/merchant_list_config.go:11`). Literal enabled_env="true" activates deterministic local list selection; empty lists allow everyone (`pkg/utils/utils.go:629`). A nonmatching whitelist disables direct creation without falling back to Splitz. False enabled_env does not itself mean legacy: it falls through to Splitz, whose variant name must equal `fts_request_from_payouts_service` (`core.go:5959`). A generic on variant does not enable this branch.

Direct async selection calls `FundTransferServiceProcessing` (`core.go:5847`), eventually `pkg/fts/transfer_init_create.go:154`. Its HTTP client sets `X-Origin: payouts` (`pkg/fts/base.go:95`). Normal Direct dispatch always enqueues FTS async work; if queue push fails, `processor/fundAccountPayoutDirect.go:225` falls back to monolith CreateFTS. A direct-route assertion must verify the actual hop, not infer it solely from account type or final state. Shared async creation likewise needs the real worker to execute its branch.

FTS `internal/transfer/service.go:399` writes transfer_meta only if CreateTransferMetaRollout is enabled. `service.go:1138` checks FireStatusUpdateKafka first and, when enabled, publishes with key source_id and returns before any HTTP relay. Otherwise `service.go:1147–1178` requires metadata origin=payouts plus a nonempty `[payouts_service.update_fts_fund_transfer]` endpoint for direct HTTP. Metadata absent/disabled or a non-Payouts origin uses the ordinary product webhook.

FTS Splitz uses `variant.variables` key **enabled**, value **true** (`internal/providers/splitz/splitz.go:248–256`). The old substitute emitted only result=on; that could never satisfy FTS's check. The substitute now supplies both variables. Route profiles use explicit synthetic experiment IDs `arena_fts_meta` and `arena_fts_kafka` with a variant for every generated merchant. The production experiment rollout is not inferred or reproduced.

The FTS direct endpoint is POST `http://payouts-api:9400/v1/payouts/transfer_status_webhook`, using `[payouts_service.auth]` matched to Payouts `cred.FTS`. Legacy product status endpoint stays on monolith. Kafka uses `kafka:9092`, TLS disabled inside the isolated local network, topic `rx-fts-status-update-events`; the existing Payouts consumer/retry processes and consumer group configuration must both remain running.

## Source mismatch preserved for Kafka

FTS `GetMapFromTransfer` (`internal/transfer/service.go:1306–1356`) emits source_account_id and bank_account_type, and filters the map to an explicit field set. It does **not** emit fts_fund_account_id, fts_account_type or fts_status. The Kafka branch publishes that map unchanged (`service.go:1140`).

Payouts `internal/taskHandlers/fts_status_updates.go:42–53` unmarshals the raw message into `StatusUpdateRequest` and `DetailsUpdateRequest`. The status DTO (`internal/app/dtos/payoutUpdate.go:8–16`) expects fts_fund_account_id, fts_account_type, fts_status, so those fields remain empty. Unlike direct HTTP, this consumer does not call the transfer-webhook adapter that translates source metadata. A Shared processed payout requiring FTS Ledger discovery may therefore fail; actual runtime behavior must be measured and reported, not corrected by injecting a translated message while claiming raw Kafka route fidelity.

The same Kafka handler explicitly returns nil for failed and reversed statuses (`fts_status_updates.go:57–62`), with a comment retaining API as reversal truth. These events can be consumed successfully without changing the payout. This is a product/source limitation, not a reason to declare a terminal scenario passed or to patch core behavior silently.

## Verifier contract corrections

- Direct HTTP DTO `internal/app/dtos/transfer_status_webhook_request.go:7`: fund_transfer_id:int64 and status:string required; source_id/source_type are a required pair; source_account_id:int64 and bank_account_type:string carry the Ledger source metadata. Tests must use the real transfer ID and source-account mapping. Default fund_transfer_id=1 and empty source metadata are not representative.
- In-flight inspector `internal/app/dtos/inflightReservationInspectResponse.go:19`: `{store_trusted,merchant_id,balance_id,reserved_total,items:[{payout_id,amount,dispatched_at,state}]}`. Both merchant_id/balance_id query fields required. No reservations or total field exists. It is a read-only GET; no force-release API exists. Source route auth matches internal API/FTS identity (`payout_internal_routes.go:198`).
- The existing wait_for_initiated helper accepted processed/failed/reversed states and swallowed a timeout. For pre-terminal assertions, require exactly initiated while the bank is explicitly held; separately require actual FTS transfer/details when the test depends on them. Failure to observe the state is a failure, not missing-data skip.
- Ledger journal assertions must use Decimal/exact integer minor units, verify expected account roles and amounts, and check both entries and terminal state. A sum-to-zero alone also accepts an empty entry set.
- Shared low-balance top-up must update Ledger through a real journal, then invoke the mirror-sync control before the event/cron assertion. Writing only accounts.balance bypasses Ledger's cache; writing only Ledger leaves API balance.updated_at stale for the real six-hour eligibility guard.

## Verification performed

All four profiles were applied to existing rendered Payouts and FTS TOML in memory. Parsing, repeat-application idempotence and route-control values passed. No configuration files or running processes were mutated by this validation. This is configuration contract validation, not route execution proof.
