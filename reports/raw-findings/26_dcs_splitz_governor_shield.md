# 26. DCS, Splitz, Governor, Shield — API contracts, schema models, local-run/stub specs

Scope: cross-reference of the Payouts architecture's four external decision/config dependencies —
DCS (dynamic config), Splitz (experimentation), Governor (fee rule engine, via Charge Collections
SDK), and Shield (fraud/risk rule evaluation) — covering wire contracts, storage/schema, local-run
recipes, and deterministic stub specs for local testing with 3 synthetic merchants
(`merchant_test_001/002/003`).

Repos used: `dcs`, `config-proto`, `splitz`, `governor`, `governor-executor`, `charge-collections`,
`shield-sdk`, `proto` (separate `razorpay/proto` clone), `goutils/splitz`, `goutils/dcs`, and the
consuming clients `payouts/pkg/{dcs,splitz,shield,ccSdk}`, `cfa/internal/dcsservice`, `x-balances`.
The full `shield` service repo (~6.7GB) is not cloned; Section D relies on `payouts/pkg/shield` +
`shield-sdk` per the task's fallback instruction. The `charge-collections-sdk` Go module (vendored
dependency of `payouts/pkg/ccSdk`) is not readable from this sandbox (go module cache path outside
the permitted filesystem tree) — Section C relies on `payouts/pkg/ccSdk`'s own source, Governor's own
repo, and Charge Collections' proto/service surface instead.

---

## A. DCS

DCS ("Dynamic Config Service") is a generic protobuf-typed key-value configuration store, not a payouts-specific
service. It stores arbitrary proto messages (defined in the `config-proto` repo, e.g. under
`rzp/x/merchant/payouts/*`) keyed by `(namespace, entity, entity_id, domain, object_name)`. Payouts, CFA and other
consumers read/write their merchant-level config objects (workflow flags, CFA flags, direct-account configs, etc.)
through this generic key-value contract.

### Stack

- Language/framework: **Go 1.25** (`dcs/go.mod:1-3`). Uses `gin-gonic/gin` for the admin-server HTTP layer,
  standard `google.golang.org/grpc` for gRPC, and `grpc-ecosystem/grpc-gateway/v2` for REST/JSON-to-gRPC
  transcoding.
- Binaries (`dcs/cmd/*`, `dcs/Makefile:22` `BINS := server admin-server consumer migrator`):
  - `server` — the main KV/Auth/User/Proxy/Health API (`dcs/cmd/server/main.go`)
  - `admin-server` — the DCS admin dashboard backend (onboarding, config-management, MCP) (`dcs/cmd/admin-server`)
  - `consumer` — Kafka consumer (`dcs/cmd/consumer`)
  - `migrator` — DB migration/backfill runner (`dcs/cmd/migrator`)
- Transport: **both gRPC and HTTP/JSON, on separate ports, from the same proto definitions**. Confirmed in
  `dcs/internal/server/server.go:12-21`:
  - `DefaultGRPCAddress = "0.0.0.0:8080"` (native gRPC)
  - `DefaultHTTPAddress = "0.0.0.0:8081"` (grpc-gateway REST/JSON — this is what most callers use)
  - `DefaultInternalAddress = "0.0.0.0:8082"` (health, presumably metrics/admin)
  - Registration for both transports happens in `dcs/internal/server/grpc_handler.go` (`grpcHandlerFunc`) and
    `dcs/internal/server/http_handler.go` (`httpHandlerFunc`, which calls `RegisterXxxServiceHandlerFromEndpoint`
    for Health/Auth/User/KV/Proxy(+ProxyV2) services — `dcs/internal/server/http_handler.go:35-119`).
  - Not Twirp. It is classic protoc-gen-go-grpc + protoc-gen-grpc-gateway with openapiv2 swagger annotations.
- Docker: `dcs/Dockerfile`, `dcs/Dockerfile.devstack`, `dcs/docker-compose.e2e.yaml` (DynamoDB-local + MySQL +
  Kafka/Zookeeper for e2e).
- Build: standard multi-arch Makefile (`dcs/Makefile`) that builds the four binaries via a containerized
  golang:1.25-alpine build image.

### API contract (get/patch config)

Proto source of truth: `proto/dcs/kv/v1/kv.proto` (pulled from the separate `razorpay/proto` repo at build time —
see `dcs/Makefile` `PROTO_GIT_URL := "https://github.com/razorpay/proto"`). Generated Go/gateway code lives in
`dcs/rpc/dcs/kv/v1/` (`kv.pb.go`, `kv_grpc.pb.go`, `kv.pb.gw.go`, `kv.swagger.json`).

Service: `dcs.kv.v1.KVService`. All RPCs are `POST` with `body: "*"` (grpc-gateway):

| RPC | HTTP path | Purpose | proto file:line |
|---|---|---|---|
| `Put` | `POST /v1/kv/put` | Replace whole object at a key (idempotent create/replace) | `proto/dcs/kv/v1/kv.proto:255-260` |
| `Get` | `POST /v1/kv/get` | Batched "GraphQL-like" read of multiple keys, with per-key field masks | `proto/dcs/kv/v1/kv.proto:263-270` |
| `Evaluate` | `POST /v1/kv/evaluate` | Like Get, but resolves each field across the levels it's registered at (level-combining expressions) | `proto/dcs/kv/v1/kv.proto:274-282` |
| `Patch` | `POST /v1/kv/patch` | Partial update of specific fields via a fieldmask | `proto/dcs/kv/v1/kv.proto:286-293` |
| `GetAuditHistory` | `POST /v1/kv/audit` | Audit trail for a key | `proto/dcs/kv/v1/kv.proto:296-303` |
| `GetEntityAggregate` | `POST /v1/kv/entities` | Paginated scan of all entity_ids matching a partial key (offset/limit) | `proto/dcs/kv/v1/kv.proto:307-314` |

**Key model** (`proto/dcs/kv/v1/kv.proto:38-63`, message `Key`):
```
Key = /namespace/entity/entity_id/domain/object_name
example: rzp/pg/merchant/mid_swiggy/upi/switch/Features
```
Fields: `namespace` (1, string, e.g. `rzp/x`), `entity` (2, string, e.g. `merchant`), `entity_id` (3, string, the
merchant id), `domain` (4, string, e.g. `payouts` or `payouts/direct_accounts`), `object_name` (5, string, the
proto message name, e.g. `Workflows`, `Cfa`, `ApiInterface`, `FundTransfer`, `PayoutModeConfig`, `Configs`).

**Request/response schemas** (from `dcs/rpc/dcs/kv/v1/kv.swagger.json` and `kv.proto`):
- `GetRequest{ queries: []Query }`, `Query{ key: Key, fieldmasks: []Field }`, `Field{ name: string }`.
- `GetResponse{ kvs: []KeyValue }`, `KeyValue{ key: Key, value: bytes }` — **`value` is the raw marshalled
  protobuf bytes of the object** (the object named by `Key.object_name`), base64-encoded over JSON transport.
- `PutRequest{ key: Key, value: bytes, audit_log: AuditLog }` → `PutResponse{ key: Key }`.
- `PatchRequest{ key: Key, value: bytes (partial object), fieldmasks: []Field, audit_log: AuditLog }` →
  `PatchResponse{ key: Key, count: int64 }` (`proto/dcs/kv/v1/kv.proto:199-224`).
- `AuditLog{ change_by, change_reason, change_approved_by, change_value (server-set), service_name (server-set),
  action (server-set enum PUT/PATCH/GET/GET_AUDIT), created_time }` (`proto/dcs/kv/v1/kv.proto:71-98`).
- Auth: every KV RPC requires `Authorization: bearer <token>` — declared via
  `openapiv2_swagger.security_definitions` in `proto/dcs/kv/v1/kv.proto:20-34`.

Handler wiring: `dcs/internal/server/grpc_handler.go:55-64` registers `kvrpc.RegisterKVServiceServer(server,
kv.NewServer(...))`, gated on `AuthzService` and `ACLEnforcer` both being non-nil.

### Schema model (full field-by-field table)

Field values come directly from the `.proto` source in `config-proto` (`config-proto/rzp/x/merchant/payouts/*`),
cross-checked against `config-proto/index/object_index.json` and `field_index.json` for the Key
(`namespace/entity/domain/object_name`) each message resolves to.

| Message (Key: namespace/entity/domain/object_name) | Field | Proto type | # | Default (from proto comment / zero-value) | file:line |
|---|---|---|---|---|---|
| `Workflows` (`rzp/x/merchant/payouts/Workflows`) | `enable_approval_via_oauth` | bool | 1 | false (proto3 zero-value) | `config-proto/rzp/x/merchant/payouts/workflows.proto:15` |
| " | `skip_workflow_for_dashboard` | bool | 2 | **false**, documented default | `config-proto/rzp/x/merchant/payouts/workflows.proto:20` |
| " | `skip_workflow_for_payroll` | bool | 3 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/workflows.proto:25` |
| " | `skip_approval_workflow_for_api` | bool | 4 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/workflows.proto:30` |
| " | `enable_payout_workflow` | bool | 5 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/workflows.proto:35` |
| `FundTransfer` (`rzp/x/merchant/payouts/FundTransfer`) | `enable_payouts` | bool | 1 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/fund_transfer.proto:9` |
| " | `payouts_to_fts_async_processing` | bool | 2 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/fund_transfer.proto:14` |
| " | `increase_per_payout_amount_limit` | bool | 3 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/fund_transfer.proto:19` |
| " | `payouts_blocked_via_lite_account` | bool | 4 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/fund_transfer.proto:24` |
| " | `enable_payouts_queue_buffer` | bool | 5 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/fund_transfer.proto:29` |
| " | `queue_payout_bal_buffer` | int64 | 6 | **0** for all merchants, documented default | `config-proto/rzp/x/merchant/payouts/fund_transfer.proto:34-40` |
| " | `block_va_payouts` | bool | 7 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/fund_transfer.proto:44` |
| `FundLoading` (`rzp/x/merchant/payouts/FundLoading`) | `skip_whitelisted_source_accounts` | bool | 1 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/fund_loading.proto:12` |
| `ApiInterface` (`rzp/x/merchant/payouts/ApiInterface`) | `enable_beneficiary_name_in_response` | bool | 1 | **false**, documented default | `config-proto/rzp/x/merchant/payouts/api_interface.proto:11` |
| " | `enable_null_narration` | bool | 2 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/api_interface.proto:17` |
| " | `payout_idem_key_required` | bool | 3 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/api_interface.proto:21` |
| " | `enable_http_encryption` ("encryption" flag) | bool | 4 | **false**, documented default (unencrypted) | `config-proto/rzp/x/merchant/payouts/api_interface.proto:26-30` |
| " | `below_rupee_payouts` | bool | 5 | **false**, documented default | `config-proto/rzp/x/merchant/payouts/api_interface.proto:35` |
| " | `payout_service_enabled` | bool | 6 | false (zero-value); false = routed via API Monolith, true = routed via Payouts Service | `config-proto/rzp/x/merchant/payouts/api_interface.proto:38-42` |
| " | `fmp_config` | map<string,string> | 7 | empty map (zero-value) | `config-proto/rzp/x/merchant/payouts/api_interface.proto:47-51` |
| " | `bene_name_in_payout` | bool | 8 | **false**, documented default | `config-proto/rzp/x/merchant/payouts/api_interface.proto:56` |
| " | `rbl_ca_upi` | bool | 9 | **false**, documented default | `config-proto/rzp/x/merchant/payouts/api_interface.proto:61` |
| " | `enable_ip_whitelist_fetch` | bool | 10 | **false**, documented default | `config-proto/rzp/x/merchant/payouts/api_interface.proto:66` |
| `Cfa` (`rzp/x/merchant/payouts/Cfa`) | `skip_ifsc_lookup` | bool | 1 | false (zero-value) | `config-proto/rzp/x/merchant/payouts/cfa.proto:15` |
| `direct_accounts.Configs` (`rzp/x/merchant/payouts/direct_accounts/Configs`) | `in_flight_reservation_enabled` | bool | 1 | **false**, documented default (legacy mirror-vs-buffer compare) | `config-proto/rzp/x/merchant/payouts/direct_accounts/configs.proto:15` |
| `direct_accounts.PayoutModeConfig` (`rzp/x/merchant/payouts/direct_accounts/PayoutModeConfig`) | `allowed_upi_channels` | repeated string | 1 | **empty list**, documented default | `config-proto/rzp/x/merchant/payouts/direct_accounts/payout_mode_config.proto:13` |

Notes on the requested keys:
- `payout_workflows` as a bare name does not exist; the actual field is `enable_payout_workflow` on `Workflows`.
- `skip_*` flags found: `skip_workflow_for_dashboard`, `skip_workflow_for_payroll`, `skip_approval_workflow_for_api`
  (all on `Workflows`), `skip_whitelisted_source_accounts` (on `FundLoading`), `skip_ifsc_lookup` (on `Cfa`).
- "encryption" flag = `enable_http_encryption` on `ApiInterface`.
- `AllowedUpiChannels` (Go field name) = `allowed_upi_channels` proto field on `direct_accounts.PayoutModeConfig`.
- All defaults are proto3 zero-values (`false` for bool, `0` for int64, empty for repeated/map) unless a comment
  explicitly documents an intended default — no separate `default_config.go`/yaml file was found defining
  overrides; the DCS server itself does not appear to inject non-zero defaults (see Unknowns).

There is also a Go-generated marshal/unmarshal registry in `config-proto/gen/rpc/go/...` (package `gen`, used as
`github.com/razorpay/config-proto/gen`) that DCS calls via `gen.FetchMarshalFn(key)` /
`gen.FetchUnMarshalFn(key)` to marshal/unmarshal the `bytes value` for a given flattened key path — this is how
DCS resolves "object_name" to a concrete proto message type without a hardcoded switch (confirmed used in
`dcs/internal/kv/dualwrite.go:149` and in the client-side `payouts/pkg/dcs/client.go:71-78` /
`cfa/internal/dcsservice/client.go:136-142`).

### Storage (DB/table model)

**Primary store: AWS DynamoDB**, single-table design. Confirmed by `dcs/config/default.toml:7` (`[db] choice =
"dynamodb"`) and `dcs/internal/storage/storage.go:28-33` (`Choice` enum: `DbMemory`/`DbDynamoDB`).

Table schema (`dcs/internal/storage/dynamodb/key_builder.go` and `dcs/scripts/init-dynamodb.sh:109-138`):
- Partition key `PK` (string), sort key `SK` (string), plus a GSI (`GSIPK1`/`GSISK1`) for secondary access
  patterns. Table name is environment-specific, e.g. `prod-dcs` / `stage-dcs` (`dcs/config/default.toml:31-32`,
  `dcs/config/e2e.toml:24-29`).
- For KV config objects, `GeneratePKAndSK` (`dcs/internal/storage/dynamodb/key_builder.go:63-116`) derives:
  - `PK = "entity#" + Entity + "#" + EntityId"` (e.g. `entity#merchant#merchant_test_001`)
  - `SK = "kv/" + Namespace + "/" + Entity + "/" + Domain + "/" + ObjectName"` (e.g.
    `kv/rzp/x/merchant/payouts/direct_accounts/PayoutModeConfig`)
  - i.e. it **is** effectively keyed by `(namespace, merchant_id, key)` — one item per
    `(entity, entity_id, domain, object_name)` tuple — a generic KV/blob table, not fully relational per field.
- Item attributes (`dcs/internal/storage/dynamodb/key_builder.go:14-61`): `PK`, `SK`, `CreatedAt`, `UpdatedAt`,
  `Value` (marshalled protobuf bytes of the object — the actual config blob), `Config`/`JSONConfig` (a JSON mirror
  of the proto message, presumably for readability/queries in the AWS console or admin tooling), `AuditDetails`.
  Audit rows use the same table with `PK = "audit#entity#..."` / `SK` prefixed `audit/...`
  (`dcs/internal/storage/dynamodb/key_builder.go:74-79,182-184`). User rows: `PK = "user#<username>"`, `SK =
  "user"` (`:80-84`). Proxy feature-mapping blob: `PK = "proxy#mapping"`, `SK = "api_name_mapping"` (`:85-88`).

**Secondary store: Aurora MySQL** (via GORM, `dcs/internal/storage/aurora/aurora.go:12-16` — uses
`gorm.io/driver/mysql`, i.e. **MySQL-compatible Aurora, not Postgres**), used for two distinct purposes, not as
the primary config store:
1. **Legacy "Api" DB** (`[Api.Write]`/`[Api.Read]` in `dcs/config/default.toml`) — the API Monolith's own
   pre-existing MySQL feature-flag table (`api_db`), which the "Proxy" services read/write directly as the legacy
   leg of the migration (see Export path below).
2. **New "Dcs" Aurora DB** (`[Dcs.Write]`/`[Dcs.Read]` in `dcs/config/default.toml`) — used by "Proxy v2"
   (`dcs/internal/proxy/features/v2`) as its own feature-flag mirror table (`Feature{ID, Name, EntityID,
   EntityType, CreatedAt, UpdatedAt}`, `dcs/internal/proxy/features/dualwrite/syncer.go:130-169`), and as the
   destination of the "dual-write" sync from DynamoDB (see below).
3. Admin-server config-management state (onboarding PR tracking, `ConfigState`) also persists via Aurora/GORM —
   `dcs/internal/adminserver/configmanagement/repository.go`.

In-memory storage (`dcs/internal/storage/memory/memory.go`) is available for tests/local dev (`choice =
"inmemory"`), and the admin-server explicitly documents it uses in-memory storage for its own e2e tests
(`dcs/Makefile:388-390`, "adminserver uses in-memory storage, no external infra needed").

Migrations: `dcs/internal/migration/` (`runner.go`, `sql_backfill.go`) + versioned SQL files under
`dcs/scripts/migrations/*.sql` (e.g. `2026_07_14_change_requests_entity_id_and_status.sql`,
`2026_08_24_change_requests_mode.sql`) — these are for the Aurora-side tables (change-request / admin tracking),
not for DynamoDB (which is schemaless).

### Export path (admin API / dashboard update-requests / maker-checker)

DCS itself is fundamentally a **pull model**: consumers (Payouts, CFA, x-Balances) call the KV `Get`/`Evaluate`
HTTP/gRPC API directly (via the shared `goutils/dcs` client SDK) whenever they need a merchant's config — there is
no push/export of merchant config values out of DCS to consumers in the general case.

There *is* a maker-checker admin flow, but it is for **onboarding new config schema fields** (creating new
proto fields via a config-proto PR), not for exporting merchant *values*:
`dcs/internal/adminserver/configmanagement/{service.go,handlers.go}` — `POST /create` opens a GitHub PR against
config-proto with generated proto content, `POST /:id/approve` and `POST /:id/reject` are checker actions gated by
`OnboardingAuthorizer` (super-admin or resource-path ownership), and PR webhook/workflow events
(`RegisterWebhookRoutes`, `dcs/internal/adminserver/configmanagement/handlers.go:56-59`) drive the state machine
(`pending → validated → approved/cancelled → merged`). This is schema governance, not a data-export mechanism.

**The actual "export"/legacy-interop mechanism is the Proxy service + dual-write**, which exists because DCS is
mid-migration away from the API Monolith's own MySQL feature-flag table. This is where the task's "two flag
names" question resolves — **there are in fact two separate flag pairs, at two different points in the pipeline**:

1. **`DCSFeature.write_via_client` and `DCSFeature.read_enabled_via_dcs`** — per-legacy-feature-flag config,
   defined in `proto/dcs/proxy/v1/proxy.proto:40-49` (fields 3 and 4 of message `DCSFeature`), set via
   `POST /v1/proxy/features/config/update` (`UpdateAPIKeyMapRequest{ keys: map<api_key, DCSFeature> }`). These
   gate **which system is authoritative for a given legacy API-monolith feature name**, on the read/write side of
   the *old* `ProxyService` (v1) API that the API Monolith calls to read/write "features" (its historical
   flag mechanism):
   - `read_enabled_via_dcs` (`bool`, field 4): if false, `EnabledFeaturesByIDsAndName` /
     `EnabledFeaturesByIDAndNames` **refuse the read** with an explicit `"Read is not enabled for <name>"` error
     (`dcs/internal/proxy/features/service.go:552-556,607-611`) — i.e. this flag is the on/off switch for whether
     a given flag's value is served ("exported") out of DCS at all to legacy Proxy-API callers.
   - `write_via_client` (`bool`, field 3): if true (and the transactional flow is active), a write coming through
     the legacy `EditFeatures` bulk-edit path is **skipped and treated as a no-op success**
     (`dcs/internal/proxy/features/service.go:340-352`, comment: *"Skipping this flag as write by client"*) — the
     implication is that for such flags the actual write-of-record now happens via the DCS KV client directly
     (i.e. writes are no longer accepted through the legacy Proxy bulk-edit API for that flag).
   - These are visible/settable from the DCS MCP tool surface too: `dcs/internal/adminserver/mcp/mcp_dcs.go:41-42,76-77`.

2. **`DCSFeature.dual_write_enabled`** (field 8, `proto/dcs/proxy/v1/proxy.proto:48`) plus a **Splitz experiment
   named `dcs_kv_dual_write_enabled`** (`dcs/internal/kv/dualwrite.go:27`) — these gate the *reverse* direction:
   propagating a boolean flag write that lands on the new KV `Put`/`Patch` API back out to the legacy Aurora
   `Feature` table, so legacy consumers still reading Aurora directly see the update. Both gates must pass:
   - `dual_write_enabled` must be true for that specific mapped field (`dcs/internal/kv/dualwrite.go:120-131`,
     `dcs/internal/kv/dualwrite_cache.go:37-38,152`).
   - the Splitz experiment `dcs_kv_dual_write_enabled` must evaluate to variant `"true"` for the entity
     (per-entity rollout percentage; fails closed — no evaluator, error, or any non-"true" variant all mean "off",
     `dcs/internal/kv/dualwrite.go:194-233`).
   - On success this upserts/deletes a row in the Aurora `Feature` table
     (`dcs/internal/proxy/features/dualwrite/syncer.go:130-169`); on failure the DynamoDB write is compensatingly
     reverted (`dcs/internal/kv/dualwrite.go:276-343`) so the two stores never diverge silently.

So: if the task's "two flags" refers to a **single conceptual gate with two names**, the best match is
`write_via_client` / `read_enabled_via_dcs` (both live on the same `DCSFeature` message, both per-flag, both
directly gate whether DCS is the system of record vs. the legacy monolith for that flag — this is the closest
thing to an "export enablement" pair). `dual_write_enabled` is a related but distinct third flag (reverse-sync,
not export-gating), paired with the Splitz experiment for rollout percentage rather than a second static flag.
This is flagged as not 100%-resolved in Unknowns below since the task's phrasing is ambiguous about which
direction "export" means.

No Kafka/CDC export of config values to external consumers was found in `dcs/internal` (Kafka/`consumer` binary
exists — `dcs/internal/consumer` — but grep found no evidence it publishes KV config changes; it appears to be an
inbound consumer, not an outbound publisher — see Unknowns).

### Local run mode

Docker-compose exists and is the documented recipe:
```
cd dcs
make local-e2e-infra-up      # docker compose -f docker-compose.e2e.yaml up -d --wait
                              # + ./scripts/init-dynamodb.sh (creates prod-dcs table + prod-index-dcs GSI)
                              # + ./scripts/init-aurora.sh, ./scripts/init-dcs-aurora.sh, ./scripts/init-kafka.sh
```
`docker-compose.e2e.yaml` brings up: `amazon/dynamodb-local:latest` (port 8000), `mysql:8.0` (port 3306, db
`dcs`, user `dcs`/`dcs_password`), `wurstmeister/zookeeper` (2181) + `wurstmeister/kafka` (9092)
(`dcs/docker-compose.e2e.yaml:1-46`).

Then run the server against that infra with `APP_ENV=dev` (or `e2e`) config:
```
make build-server && APP_ENV=dev ./bin/<os>_<arch>/server     # make dev-server (Makefile:356-357)
```
Config layering: `dcs/config/default.toml` is the base, overridden per-`APP_ENV` (`dev`/`e2e`/`stage`/`prod`/
`func`/`bvt`/`perf`/`automation` + their `_test` variants) via `env|VAR|default` interpolation
(`dcs/internal/config/config.go`). `dcs/config/e2e.toml` shows `DYNAMODB_ENDPOINT=http://localhost:8000` and the
MySQL/Aurora envs (`DCS_API_HOST`, `DCS_AURORA_WRITE_HOST`, etc.) pointing at localhost.

Alternative in-memory mode: `[db] choice = "inmemory"` uses `dcs/internal/storage/memory/memory.go` — no external
infra needed at all. The admin-server's own e2e suite runs this way (`make adminserver-e2e`,
`dcs/Makefile:388-395`, comment: *"adminserver uses in-memory storage, no external infra needed"*).

Full end-to-end test runner: `make local-e2e` (tears down/spins up the compose stack, seeds it, runs
`./build/e2e.sh`, tears down again) or `make local-e2e-keep` to leave the server running after tests for manual
poking.

Seed data: `dcs/internal/seed/seed.go` seeds example DCS **users** (`username=example`, `password=example`,
`roles=[example_pg_merchant_admin]`) for local login — it does not seed merchant config values; those would need
to be `Put` via the KV API after boot (or via `scripts/init-dcs-aurora.sh` / `init-dynamodb.sh` for infra-level
seeding, which only creates tables, not KV rows).

### Auth

Two schemes, dispatched per-RPC by `dcs/internal/server/interceptor/auth.go`:

1. **JWT bearer token**, for normal (non-admin) RPCs, including `KVService`. Flow:
   - Client calls `POST /v1/auth/login` (`dcs/rpc/dcs/auth/v1/auth.swagger.json` → path `/v1/auth/login`) with
     `{username, password}` (`AuthService.Login`, implemented in `dcs/internal/auth/service.go:47-89`), gets back
     a JWT `access_token` valid ~6h (`goutils/dcs/login/login.go:98`, `l.SetExpiration(time.Now().Add(6*time.Hour), ...)`).
   - Subsequent calls send `Authorization: Bearer <token>` (`goutils/dcs/httpclient/httpclient.go:134-140`, `dcs/
     internal/server/interceptor/auth.go:115-135` reads it via `grpcauth.AuthFromMD(ctx, "bearer")` and verifies
     with `authService.Verify` → JWT claims → `{username, roles}` stashed into gRPC ctx tags).
   - Non-prod environments also accept a shared `DCS_BOT_PASSWORD` env var in place of the real per-user password,
     for CI/automation bots (`dcs/internal/auth/service.go:15-16,70-79`).
2. **HTTP Basic auth**, for a fixed set of "admin methods" (`adminMethods` list passed into
   `NewAuthInterceptor`) — static `username:password` compared against `server.auth.admin.{username,password}`
   config (`dcs/internal/server/interceptor/auth.go:100-113`, `GetAdminBasicAuth`). Default dev/e2e creds are
   `admin-user`/`admin-pass` (`dcs/config/default.toml:14-16`) or `admin`/`admin` (`dcs/config/e2e.toml`).
   The admin-server's own dashboard middleware additionally has a separate "passport" auth layer
   (`dcs/internal/adminserver/middleware/passport_auth.go`) for human dashboard logins, and MCP-specific auth
   (`dcs/internal/adminserver/middleware/mcp_auth.go`) for the DCS MCP tool surface — not investigated in depth
   here (out of scope: consumer-facing KV API, not the admin dashboard).
3. `KVService` additionally requires `AuthzService`/`ACLEnforcer` at boot (role→resource-permission checks happen
   inside the kv layer itself, not the shared interceptor, "since kv requests are not REST-like and have the
   resource in the body, not the URL" — `dcs/internal/server/interceptor/auth.go:92-94`).

Client-side (Payouts / CFA), both use the shared `goutils/dcs` SDK with **username/password credentials**
(exchanged transparently for a cached JWT via the login flow above):
- Payouts: `payouts/pkg/dcs/client.go:26-49` — `config.UserCredentials{Username, Password}` from `Config`
  (`payouts/pkg/dcs/config.go`), `dcs.WithConfig(...)`, `dcs.WithHTTPClient(...)`.
- CFA: `cfa/internal/dcsservice/client.go:37-133` — identical shape, `Config{Username, Password, Env, Mock,
  Timeout}`, notes in its package doc that CFA "reuses the payouts DCS namespace and credentials" (`cfa/internal/
  dcsservice/client.go:1-4`) — i.e. CFA and Payouts share the same DCS service-account credentials and both read
  under `rzp/x/merchant/payouts/*`.

No mTLS or RSA-signed internal-service headers were found in the DCS auth path — it is JWT bearer + Basic auth
over the internal network, not the "X-Razorpay-*"-style signed-header scheme used elsewhere at Razorpay (not
found anywhere under `dcs/internal/auth` or `dcs/internal/server/interceptor`).

### Stub spec: deterministic DCS stub

Real local run is feasible (see Local run mode above) and is the recommended path for integration testing since
the on-the-wire format is protobuf bytes requiring `config-proto`'s generated marshal/unmarshal registry — a
hand-rolled JSON stub cannot easily fabricate valid `value` bytes without that registry. That said, here is a
stub spec sufficient for testing consumer code paths that only need `KVService.Get`/`Evaluate` semantics:

**Endpoints to stub** (all `POST`, JSON body, mirrors the real grpc-gateway contract):
- `POST /v1/auth/login` → `{access_token: string}` (accept any username/password in the stub; real payloads are
  in `dcs/rpc/dcs/auth/v1/auth.swagger.json`)
- `POST /v1/kv/get` → request `{queries: [{key: {namespace, entity, entity_id, domain, object_name}, fieldmasks:
  [{name}]}]}`, response `{kvs: [{key: Key, value: <base64 protobuf-or-JSON-bytes>}]}`
- `POST /v1/kv/patch` → request `{key: Key, value: <base64 bytes>, fieldmasks: [{name}], audit_log: {...}}`,
  response `{key: Key, count: int64}`
- `POST /v1/kv/put` → request `{key: Key, value: <base64 bytes>, audit_log: {...}}`, response `{key: Key}`

**Seed data** — three synthetic merchants, keyed by `(namespace=rzp/x, entity=merchant, entity_id, domain,
object_name)` as derived above from `config-proto/index/object_index.json`. Values shown as the logical JSON
shape of each proto message (a stub server can accept/return JSON directly if it isn't validating real protobuf
wire bytes):

```json
{
  "merchant_test_001": {
    "rzp/x/merchant/payouts/Workflows":                       { "enable_payout_workflow": false, "skip_workflow_for_dashboard": false, "skip_workflow_for_payroll": false, "skip_approval_workflow_for_api": false, "enable_approval_via_oauth": false },
    "rzp/x/merchant/payouts/Cfa":                              { "skip_ifsc_lookup": false },
    "rzp/x/merchant/payouts/direct_accounts/Configs":          { "in_flight_reservation_enabled": false },
    "rzp/x/merchant/payouts/FundTransfer":                     { "queue_payout_bal_buffer": 0, "enable_payouts": true, "block_va_payouts": false },
    "rzp/x/merchant/payouts/ApiInterface":                     { "enable_http_encryption": false, "payout_service_enabled": false },
    "rzp/x/merchant/payouts/direct_accounts/PayoutModeConfig": { "allowed_upi_channels": [] }
  },
  "merchant_test_002": {
    "rzp/x/merchant/payouts/Workflows":                       { "enable_payout_workflow": true, "skip_workflow_for_dashboard": true, "skip_workflow_for_payroll": false, "skip_approval_workflow_for_api": false, "enable_approval_via_oauth": true },
    "rzp/x/merchant/payouts/Cfa":                              { "skip_ifsc_lookup": true },
    "rzp/x/merchant/payouts/direct_accounts/Configs":          { "in_flight_reservation_enabled": true },
    "rzp/x/merchant/payouts/FundTransfer":                     { "queue_payout_bal_buffer": 50000, "enable_payouts": true, "block_va_payouts": false },
    "rzp/x/merchant/payouts/ApiInterface":                     { "enable_http_encryption": true, "payout_service_enabled": true },
    "rzp/x/merchant/payouts/direct_accounts/PayoutModeConfig": { "allowed_upi_channels": ["icici", "axis"] }
  },
  "merchant_test_003": {
    "rzp/x/merchant/payouts/Workflows":                       { "enable_payout_workflow": true, "skip_workflow_for_dashboard": false, "skip_workflow_for_payroll": true, "skip_approval_workflow_for_api": true, "enable_approval_via_oauth": false },
    "rzp/x/merchant/payouts/Cfa":                              { "skip_ifsc_lookup": false },
    "rzp/x/merchant/payouts/direct_accounts/Configs":          { "in_flight_reservation_enabled": false },
    "rzp/x/merchant/payouts/FundTransfer":                     { "queue_payout_bal_buffer": 100000, "enable_payouts": true, "block_va_payouts": true },
    "rzp/x/merchant/payouts/ApiInterface":                     { "enable_http_encryption": false, "payout_service_enabled": true },
    "rzp/x/merchant/payouts/direct_accounts/PayoutModeConfig": { "allowed_upi_channels": ["yesbank"] }
  }
}
```

`GET` example the stub must satisfy (Payouts calling for `merchant_test_002`'s workflow config):
```
POST /v1/kv/get
{
  "queries": [{
    "key": {"namespace":"rzp/x","entity":"merchant","entity_id":"merchant_test_002","domain":"payouts","object_name":"Workflows"},
    "fieldmasks": [{"name":"enable_payout_workflow"}]
  }]
}
```

### Unknowns (things not fully determined)

- **Exact resolution of "two flag names" for export gating is ambiguous.** Found three candidate booleans on
  `DCSFeature` (`write_via_client`, `read_enabled_via_dcs`, `dual_write_enabled` — `proto/dcs/proxy/v1/proxy.proto:
  40-49`) plus a Splitz experiment name (`dcs_kv_dual_write_enabled`, `dcs/internal/kv/dualwrite.go:27`). I
  believe `write_via_client`/`read_enabled_via_dcs` is the best match for "which export path is used" (they gate
  DCS-vs-legacy-monolith authority per flag), but could not find a single doc/comment in the repo explicitly
  calling out "these are the two flags" — this is inferred from code behavior, not a stated design doc. Searched:
  all of `dcs/internal` and `dcs/docs/specs/*` for "export"/"sync"/"publish"/"propagate"; no `docs/specs` entry
  covered this precisely (only `2026-05-18-authz-dependency-removal` and `admin-ux-role-management` exist there).
- **Whether the `consumer` binary (`dcs/cmd/consumer`, Kafka) publishes or only consumes KV change events** was
  not conclusively determined — grep found no outbound Kafka publish call tied to KV writes in
  `dcs/internal/consumer` or `dcs/internal/kv`; it may exist but wasn't located in the time available. If it
  exists, it would be a fourth export mechanism (CDC-style) not covered above.
- **Whether DCS injects any non-zero-value defaults server-side** (vs. proto3 zero-values) for the payouts fields
  was not confirmed — no `default_config.go`/yaml/json overrides file was found in `dcs/internal`; all documented
  defaults above come from proto comments, which for booleans are `false` and match the proto3 zero-value anyway.
- Did not deeply investigate `dashboard`/`admin-dashboard` repos' DCS-related UI (out of scope per task framing —
  the admin-server backend's maker-checker for schema onboarding was covered, but the actual dashboard frontend
  code was not read).
- `x-balances`' own `dcs` client directory was not separately examined in depth (time-boxed); its usage pattern
  is expected to mirror `payouts/pkg/dcs` and `cfa/internal/dcsservice` (same `goutils/dcs` SDK) but this was not
  verified line-by-line.

---

## B. Splitz

Repos inspected: `splitz/` (service), `payouts/pkg/splitz/` + `payouts/internal/provider/splitz_client.go` (Payouts client wiring), `goutils/splitz/` (the actual SDK Payouts calls — cloned locally at `goutils/splitz`), `proto/splitz/` (a separate `razorpay/proto` clone found at BASE, which is where Splitz's `.proto` sources actually live and get fetched from at Splitz build time), `fts/`, `ledger/`, and `xperience/` (found while grepping, also a Splitz consumer) for experiment-ID cross-references. `config-proto/` was checked and does **not** contain Splitz protos (only unrelated PG/merchant protos matched a text grep).

### API contract (Evaluate / GetVariant)

**Important clarification up front:** there is no RPC literally named `GetVariant`. `GetVariant` is a **client-SDK method name** in `goutils/splitz` (`goutils/splitz/client.go:127`) that wraps the wire-level `Evaluate` Twirp RPC. The actual Twirp service/RPC names are `Evaluate`, `EvaluateBulk`, `AllowCors`, and `FetchDecisionContext`.

**Proto source (ground truth):** `proto/splitz/evaluate/v1/evaluate_api.proto` (fetched into Splitz's own repo at build time — see "Local run mode" below for how `splitz/Makefile` pulls this from `github.com/razorpay/proto` into a gitignored `proto/` + generated `rpc/` dir; that's why no `.proto` or `rpc/` files exist inside the `splitz/` clone itself).

```proto
// proto/splitz/evaluate/v1/evaluate_api.proto
package rzp.splitz.evaluate.v1;

service EvaluateAPI {
  rpc Evaluate(EvaluateExperimentRequest) returns (EvaluateExperimentResponse);
  rpc EvaluateBulk(BulkEvaluateExperimentRequest) returns (BulkEvaluateExperimentResponse);
  rpc AllowCors(AllowCorsRequest) returns (AllowCorsResponse);
  rpc FetchDecisionContext(FetchDecisionContextRequest) returns (FetchDecisionContextResponse);
}

message EvaluateExperimentRequest {
  string id = 1;                // entity/merchant id being evaluated (bucketing key)
  string experiment_id = 2;     // either this OR experiment_name required
  string request_data = 3;      // JSON string, audience-rule attributes (e.g. merchant_id, criticality)
  bool   track_impression = 4;
  string experiment_name = 5;
}

message EvaluateExperimentResponse {
  string id = 1;
  string project_id = 2;
  message Experiment {
    string id = 1;
    string name = 2;
    string exclusion_group_id = 4;
    google.protobuf.Timestamp updated_at = 5;
  }
  Experiment experiment = 3;
  rzp.splitz.experiment.v1.Variant variant = 4;
  string Reason = 5;
  repeated string steps = 6;
}

message BulkEvaluateExperimentRequest { repeated EvaluateExperimentRequest bulk_evaluate = 1; }
message BulkEvaluateExperimentResponse { repeated EvaluateExperimentResponse bulk_evaluate_response = 1; }

message FetchDecisionContextRequest  { repeated ExperimentIdentifier identifiers = 1; }
message FetchDecisionContextResponse { repeated rzp.splitz.experiment.v1.Experiment experiments = 1; }
message ExperimentIdentifier { string id = 1; string name = 2; }
```

**HTTP/Twirp wire paths** (confirmed both server-side registration and client-side construction match):
- Server registration: `splitz/internal/routes/routes.go:141,163` — `evaluatorHandler := evaluatev1.NewEvaluateAPIServer(...)`, mounted at `evaluatev1.EvaluateAPIPathPrefix`.
- Server implementation: `splitz/internal/experiment/evaluator/server.go` — `func (s *Server) Evaluate(...)` (line 62), `EvaluateBulk` (line 123), `FetchDecisionContext` (line 336), `AllowCors` (line 49).
- Client-side path constants, confirming the standard Twirp convention `/twirp/<package>.<Service>/<Method>`, in `goutils/splitz/client.go:56-63`:
  - `EvaluateEndpoint = "/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate"`
  - `ExperimentFetchEndpoint = "/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/FetchDecisionContext"`
  - `BulkEvaluateEndpoint = "/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/EvaluateBulk"`
  - `SegmentLookUpEndpoint = "/twirp/rzp.splitz.segment.v1.SegmentAPI/Lookup"`
  - Auth: HTTP Basic (`reqObj.SetBasicAuth(s.config.Auth.Key, s.config.Auth.Secret)`, `goutils/splitz/client.go:897,1285`), matching server hooks `hooks.BasicOrPassportAuth()` (`splitz/internal/routes/routes.go:141`).

**Client-side request construction** confirming the contract (`payouts/pkg/splitz/client.go:62-65`):
```go
evaluateRequest := splitz.EvaluateRequest{
    Id:           ownerId,        // merchant/entity id
    ExperimentId: experimentId,
}
evaluateRequest.RequestData = string(requestDataJson)
```
which maps 1:1 onto `EvaluateExperimentRequest{id, experiment_id, request_data}`.

`Variant` message (`proto/splitz/experiment/v1/experiment.proto:83-90`):
```proto
message Variant {
  string id = 1;
  string name = 2;
  repeated rzp.splitz.common.variable.v1.Variable variables = 3;
  string experiment_id = 4;
  int32 weight = 5;
  string region = 6;
}
message Variable { string key = 1; string value = 2; }
```
`goutils/splitz/client.go:155-167` mirrors this in the Go SDK's `Variant`/`Variable` structs (JSON-tagged, used by `EvaluateExperimentResponse.variant`).

### Experiment/variant model

Proto model: `proto/splitz/experiment/v1/experiment.proto`. DB model + migrations: `splitz/internal/experiment/model.go`, `splitz/internal/database/migrations/20200612171458_create_experiments.go`, `.../20200612171941_create_variations.go`.

Key fields of `Experiment` (proto lines 14-74 / DB columns):
- `id` — `CHAR(14)` primary key (this is why all 8 target IDs, which are 14-char base62-ish strings, are shaped like Splitz IDs).
- `name`, `description`, `project_id`, `type` (`char(40)`)
- `sampling_percentage` (`int32` / `INT`) — % of traffic sampled into the experiment before bucketing.
- `status` — enum `unknown|created|activated|terminated|scheduled` (proto lines 22-28); DB stores `INT`.
- `default_variant` — fallback `Variant` if no bucket/rule matches.
- `exclusion_group` (`traffic_allocation.v1.TrafficAllocation`) — mutual-exclusion groups so a merchant isn't double-bucketed across conflicting experiments.
- `audience` — free-text/JSON rule string evaluated against `request_data` (audience targeting, e.g. by merchant_id/criticality/region — see `payouts` baggage enrichment below).
- `whitelisting` — `repeated EntityToIds` (JSON) — explicit merchant/entity whitelists that bypass sampling.
- `used_segments` — references to Splitz "Segments" (separate reusable audience-membership sets, looked up via `SegmentAPI/Lookup`).
- `evaluation_strategy` — `unspecified|default_strategy (sample-then-audience)|sampling_on_audience (audience-then-sample)`.
- `variants` — `repeated Variant{id, name, variables[], experiment_id, weight, region}` — `weight` (int32) is the relative traffic-allocation weight among variants (variant selection is by deterministic hash, not literal percentage, see below).
- `rules` — `repeated Rule{name, audience, sampling_percentage, variants[]}` — per-rule sub-targeting with its own audience/sampling/variant set, evaluated after the experiment-level gate passes (`splitz/pkg/coreevaluator/bucketer_evaluator.go:20-60`).
- `region_scope` — `["GLOBAL"]` or explicit region list; `ExperimentRegionConfig` (experiment.proto:105-129, migration `20250825175422_create_experiment_region_configs.go`) allows per-region overrides of almost every field above.
- `schedule.{starts_at, ends_at}`, `auto_terminate_at`, `to_be_terminated` — scheduled activation/termination.
- `metadata.{last_evaluated_at, additional_owners}`, `mentions` (Slack notify list).

**Bucketing/evaluation mechanics** (`splitz/pkg/coreevaluator/rule_evaluator.go`, `bucketer_evaluator.go`):
- Deterministic hashing: `bucketingId := req.Id + req.ExperimentId` (entity id concatenated with experiment id), hashed via `helper.MurmurHash(bucketingId, seed)`.
  - Sampling-percentage gate uses `DefaultHashSeed = 113`.
  - Variant-bucket assignment uses `BucketHashSeed = 919`.
- Evaluation order (`coreevaluator/model.go:16-24`): `whitelisting → sampler → exclusion → audience → assign_bucket`, gated per experiment then per matching rule.
- Decision reason is one of `whitelisting` / `bucketer` (`coreevaluator/model.go:9-14`).

### Experiment ID/name search results

Search covered: entire `splitz/` repo (Go source, migrations, `test/slit_tests` + `test/e2e` fixtures, `specs/`, `.github/`), `proto/splitz/`, `config-proto/`, plus `payouts/`, `fts/`, `ledger/` (as instructed) and `xperience/` (found incidentally as another Splitz consumer). **None of the 8 IDs/names appear anywhere in the `splitz/` repo itself** — no migration, seed script, fixture, or test data defines them. They only appear as **config values in consumer repos**, referencing experiments that live purely in Splitz's MySQL DB (not in any git-tracked file).

| ID/name | Found in splitz repo? | Found in consumer repos (file:line) | What it gates |
|---|---|---|---|
| `TUnTsUB8Os2kX0` | **Not found** | `payouts/config/devstack.toml:720` (`[shadow_gateway.routes."get /v1/payouts/schedule/timeslots"] experiment_id = "TUnTsUB8Os2kX0"`) | Dev-only. Comment at `devstack.toml:711-719` identifies it as Splitz *dev* experiment `payouts_shadow_gateway_scheduled_time_slots` (project `RQDeYaM0u0QPxe`), keyed on merchant_id, variant variable `mode = proxy\|shadow_api\|shadow_ps\|native`; gates the shadow-gateway pilot cutover for the timeslots route. Used via `payouts/internal/routing/shadowgateway/{mode,middleware,executor,monolith_client,parity_logger}.go`. |
| `Qnc6b4fg1Hi36k` | **Not found** | `payouts/config/prod.toml:479` (`merchant_config_via_asv_and_dcs_experiment = "Qnc6b4fg1Hi36k"`) | Payouts prod. Gates `IsMerchantConfigViaAsvAndDcsEnabled` (`payouts/internal/app/merchant/core.go:1077-1079`), consumed at `payouts/internal/app/merchant/core.go:114` and `payouts/internal/app/payouts/core.go:993` — controls whether merchant config is fetched via ASV+DCS vs legacy path. |
| `Sg7tEnMoxCbTzM` | **Not found** | `payouts/config/prod.toml:493` (`payout_workflows_dcs_name_experiment = "Sg7tEnMoxCbTzM"`) | Payouts prod. Gates `IsPayoutWorkflowsDcsNameExperimentEnabled` (`payouts/internal/app/payouts/service.go:1477-1483`), consumed at `payouts/internal/app/balance/core.go:310`, `payouts/internal/app/payouts/core.go:1037`, `payouts/internal/app/payouts/processor/baseHelper.go:126` — controls whether payout workflows are named/looked-up via DCS. |
| `TMp5VcIFYOHK2m` | **Not found** | `payouts/config/prod.toml:490` (`use_api_db_for_payout_status_details_experiment = "TMp5VcIFYOHK2m"`); also documented in `payouts/.agents/skills/repo-skill/modules/integration/external-deps.md:120` | Payouts prod. Gates reading `payout_status_details` from the API-DB vs legacy source — `payouts/internal/helpers/apidb.go:29,47`, `payouts/internal/app/payoutStatusDetails/fetch_orchestrator/core_adapter.go:38`. |
| `PEVE4sUaVG6Drw` | **Not found** | `xperience/config/prod.toml:197` (`BulkPayoutsExperiment = "PEVE4sUaVG6Drw"`); pre-existing project findings note `findings/10_infra_config_flags_tests.md:78` | Xperience prod (not payouts/fts/ledger, but a Splitz consumer in the same clone set). Gates a paise-rounding/amount-handling behavior in bulk payouts processing — `xperience/internal/app/client/splitz/experiment_evaluator.go`, `xperience/internal/app/service/bulkpayouts/service.go:2847`. |
| `ORHtoFyuKLWUSw` | **Not found** | `ledger/config/prod-live.toml:524`, `prod-live-makeshift.toml:362`, `prod-test.toml:495` (`ledgerMakeshiftDualWriteExperiment = "ORHtoFyuKLWUSw"`); non-prod envs use a different ID `OLmdiifT69WGrd` (per `findings/10_infra_config_flags_tests.md:79`) | Ledger prod. **The RX↔PG ledger dual-write migration switch** — drives `MakeshiftTransactionDualWrite{Create,Update}{Request,Success,Failed}` paths in `ledger/internal/makeshift/transactions/core.go`, traced in `ledger/internal/trace/{trace,error}.go`, metered via `MakeshiftTransactionDualWriteErrorCount` (`ledger/internal/provider/metric/metric.go`). |
| `CreateTransferMetaRollout` | **Not found** (this is a config-field/experiment *name*, not a raw Splitz ID — the actual Splitz experiment ID it resolves to differs per env) | `fts/internal/config/splitz.go:42` (`CreateTransferMetaRollout string toml:"create_transfer_meta_rollout"`); env values: most envs use the literal string `"create_transfer_meta_rollout"` as both key and value (e.g. `fts/config/env.default.toml:9784`), but **prod** (`fts/config/env.prod-live.toml:380`) resolves it to real Splitz ID `"ROyjRWsBowWdWY"` (not one of the 8 IDs in scope, so also not found in splitz) | FTS prod. Gates `createTransferMeta` / `extraTransferMetaFromCtx` and origin-based webhook routing — `fts/internal/transfer/service.go:399,401,1147`, documented at `fts/.agents/skills/repo-skill/modules/domain/transfer/integration.md:243`. |
| `FireStatusUpdateKafka` | **Not found** (same pattern as above — a config-field name) | `fts/internal/config/splitz.go:22` (`toml:"fire_status_update_kafka"`); most envs use literal `"fire_status_update_kafka"`, **prod** (`fts/config/env.prod-live.toml:364`) resolves to real Splitz ID `"PYGTRQEzO39PfB"` (also not one of the 8 IDs in scope) | FTS prod. Gates whether transfer-status-update events are pushed to Kafka instead of the legacy path — `fts/internal/transfer/service.go:1138-1140`, documented at `fts/.agents/skills/repo-skill/modules/domain/transfer/flows.md:327` and `.../integration.md:228`. |

**Bottom line:** Splitz stores all experiment definitions exclusively in its MySQL `experiments`/`variants` tables (see migrations above); nothing in the `splitz` git repo — no seed script, fixture, YAML/JSON config, or test data — defines any of the 8 given IDs/names, or in fact any concrete experiment at all. This is architecturally consistent with Splitz being an admin-managed experimentation platform (experiments are created/edited via its Twirp `ExperimentAPI` / MCP tools / admin UI at runtime, not via code deploys). This resolves the prior OPEN QUESTION definitively: **experiment definitions are DB-only, not present in this or any cloned repo.**

### Local run mode

Splitz has no single `make run`/`make dev` target; local dev is docker-compose-based, with a **separate**, lighter compose file for its "SLIT" (Splitz-in-process/integration test) suite:

- **Full local stack** — `splitz/deployments/dev/docker-compose.yml`:
  - `api` service (build `deployments/dev/Dockerfile.api`, port `49400:9400`, `APP_MODE=dev_docker`)
  - `worker` service (build `deployments/dev/Dockerfile.worker`, port `9500:9500`)
  - `db` — `mysql:5.7`, database `splitz`, port `43306:3306`
  - `migration` — one-shot container running migrations (`MIGRATION_CMD=up`)
  - `redis` — `grokzen/redis-cluster:5.0.9`, ports `26389:6379`, `7000-7050`, `5000-5010`
  - Separate Kafka compose: `splitz/deployments/dev/docker-compose-kafka.yml`
- **SLIT (in-process integration tests)** — `splitz/Makefile:191-224`:
  - `SLIT_COMPOSE_FILE := docker-compose.slit.yml` (MySQL + Redis only, no Kafka)
  - `make slit-infra-up` → `docker compose -f docker-compose.slit.yml up -d --wait`
  - `make migrate` → `go run ./cmd/migration/ up` (uses `APP_MODE` from environment)
  - `make slit` → `APP_MODE=slit go test -tags=slit ... ./test/slit_tests/...`
  - `make slit-local` → full lifecycle: infra up → migrate → test → infra down (`Makefile:217-224`)
- **Proto fetch/generate** (required before any build): `Makefile:82-123`
  - `make fetch-proto` — sparse-checkouts `github.com/razorpay/proto` (branch configurable via `PROTO_BRANCH`) into a gitignored `proto/` dir, using `scripts/proto_modules` as the sparse-checkout list (includes `splitz/*` and `splitz/mcpauth/v1` explicitly, plus `common/health/v1`, `workflows/*`).
  - `make proto-generate` — compiles into gitignored `rpc/` dir via `protoc-gen-go` + `protoc-gen-twirp`.
  - `make proto-refresh` = `clean fetch-proto proto-generate`; `make proto-all` = `deps fetch-proto proto-generate` (CI entrypoint).
  - This is why **no `.proto` and no generated `rpc/` files exist inside the `splitz/` clone** — `.gitignore` lines 35/38 exclude `proto` and `rpc`. They were only discoverable because a full `razorpay/proto` clone happened to also be present at `BASE/proto/splitz/...` in this investigation's checkout set.
- No explicit seed script was found for local experiment data — `pkg/helper/generateSeed.go` is unrelated (a random `uint32` seed generator for ID generation, not experiment fixtures).

### Client-side evaluation cache format

Confirmed at two layers: the SDK (`goutils/splitz`, cloned locally — this is what Payouts actually calls) and Payouts' own wiring (`payouts/internal/provider/splitz_client.go`, `payouts/config/default.toml:510-518`).

**Cache is in-memory only (no Redis)** — backed by `github.com/allegro/bigcache/v3`, one instance for experiments and a separate one for segments:

- **Storage interface**: `Storage{Get(key) ([]byte,error); Set(key, value) error; Delete(key) error; Capacity() int}` (`goutils/splitz/storage.go:8-21`).
- **Cache key = experiment identifier only** — `request.ExperimentId` if set, else `request.ExperimentName` (`goutils/splitz/client.go:955-980, 1024-1034`). **It is NOT `experiment_id + merchant_id`.** The cached value is the whole `Experiment` (definition, rules, variants) — evaluation-per-merchant happens locally against that cached definition via the deterministic hash bucketer described above, so one cache entry serves every merchant/entity for that experiment.
- **Cached value shape** (`goutils/splitz/client.go:709-718`):
  ```go
  type CachedExperiment struct {
      Experiment  *Experiment
      LastUpdated time.Time
      MetaData    *CachedMetaData   // {IsNotFound bool} — negative-caching for unknown experiment IDs
  }
  ```
  Stored as JSON via `Storage.Set` (`storage_bigcache.go:136-147`).
- **TTL**:
  - `config.Ttl` (default `DefaultTTL = 5 * time.Minute`, `goutils/splitz/client.go:91`) — freshness window checked manually in `getExperimentFromCache` (`time.Since(cachedExp.LastUpdated) < s.config.Ttl`, `client.go:960,973`); BigCache's own `LifeWindow` is set to the same value but its background janitor (`CleanWindow`) is deliberately **disabled** (`cfg.CleanWindow = 0`, `storage_bigcache.go:71`) so stale-but-expired entries remain physically retrievable for the Hystrix fallback below.
  - Not-found (negative cache) entries use `DefaultNotFoundExperimentTTL = 5 * time.Minute` (`client.go:96,960,973`).
  - `config.FallbackTTL` (default `DefaultFallbackTTL = 10 * time.Minute`, `client.go:94`) — how long stale cache entries may be served as a Hystrix circuit-breaker fallback when live fetch to Splitz fails (`hystrixWrapper.fetchFallbackFromCache`, `client.go:370-427`; sleep window for the `splitz_experiment_fetch` Hystrix command is derived from this, `client.go:222-233`).
- **Cache sizing** (BigCache config defaults, `experimentBigCacheConfig`, `storage_bigcache.go:69-80`): `MaxEntriesInWindow=1500`, `MaxEntrySize=10KB`, `Shards=16`, `HardMaxCacheSize=5MB`. Segment cache defaults (`segmentBigCacheConfig`, lines 87-101): `MaxEntriesInWindow=1000`, `MaxEntrySize=5KB`, `Shards=8`, `HardMaxCacheSize=5MB`, with its janitor `CleanWindow` derived as `LifeWindow/10` (segments purge actively, unlike experiments).
- **Segment cache key** (separate from experiment cache): `fmt.Sprintf("%s:%s", segmentId, key)` (`storage_bigcache.go:191,208`; `segment.go:132,146,173`) — used for `used_segments` membership lookups (`SegmentAPI/Lookup`), not for experiment/variant results.
- **Concurrency**: fetches deduped via `singleflight.Group` keyed on `request.ExperimentId + "\x00" + request.ExperimentName` (`client.go:1039-1086`) to avoid stampedes on cold start/TTL expiry.
- **Payouts' actual config** (`payouts/config/default.toml:510-518`, wired in `payouts/internal/provider/splitz_client.go:44-52`):
  ```toml
  [splitz]
      host = "https://splitz.dev.razorpay.in"
      client_side_eval         = true
      cache_ttl_minutes        = 5
      experiment_cache_size_mb = 5
      segment_cache_size_mb    = 20
  ```
  i.e. Payouts runs with client-side evaluation **enabled**, 5-minute experiment TTL, 5MB experiment cache, 20MB segment cache — matching the SDK defaults except a larger segment cache.
- Payouts additionally layers a **per-request Go `context.Context` cache** on top of the SDK's process-wide BigCache, for bulk-evaluate results within a single request: `StoreBulkResultsInContext`/`getBulkResultsFromContext` (`payouts/pkg/splitz/client.go:22-32`), keyed by `experimentId` in a `map[string]*splitz.Variant` stashed in `ctx`.

### Stub spec: concrete endpoint(s), request/response schema, seed data

**Endpoint to stub**: `POST {SPLITZ_HOST}/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/Evaluate` (Twirp JSON transport — `Content-Type: application/json`, HTTP Basic Auth). Also stub `EvaluateBulk` at `/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/EvaluateBulk` for payouts' bulk callers (`payouts/pkg/splitz/splitz.go` bulk methods), and `FetchDecisionContext` at `/twirp/rzp.splitz.evaluate.v1.EvaluateAPI/FetchDecisionContext` since client-side eval (which Payouts has enabled) fetches full experiment definitions through that RPC rather than `Evaluate` — see "Local run mode"/cache section above. A stub server for Payouts' dev/local use should implement all three.

**Request JSON schema** (`Evaluate`):
```json
{
  "id": "string (merchant/entity id, required)",
  "experiment_id": "string (optional if experiment_name set)",
  "experiment_name": "string (optional if experiment_id set)",
  "request_data": "string (JSON-encoded object, audience attributes)",
  "track_impression": false
}
```

**Response JSON schema** (`Evaluate`):
```json
{
  "id": "string",
  "project_id": "string",
  "experiment": {
    "id": "string",
    "name": "string",
    "exclusion_group_id": "string",
    "updated_at": "RFC3339 timestamp"
  },
  "variant": {
    "id": "string",
    "name": "string",
    "variables": [{"key": "string", "value": "string"}],
    "experiment_id": "string",
    "weight": 0,
    "region": ""
  },
  "Reason": "whitelisting|bucketer",
  "steps": ["whitelisting", "sampler", "exclusion", "audience", "assign_bucket"]
}
```
(An empty/unmatched result is `{"id": "<req.id>", "experiment": {"id": "<req.experiment_id>"}}` with no `variant` — mirrors `splitz/internal/experiment/evaluator/server.go:74-77,97-98`.)

**`FetchDecisionContext` request/response** (needed for client-side eval, which fetches whole experiments):
```json
// request
{"identifiers": [{"id": "TUnTsUB8Os2kX0", "name": ""}, ...]}
// response
{"experiments": [ { "id": "...", "name": "...", "status": "activated", "sampling_percentage": 100,
                     "variants": [{"id":"...", "name":"on", "variables":[{"key":"result","value":"on"}], "weight":100}],
                     "default_variant": {...}, "rules": [] } ]}
```

**Seed data** — deterministic, keyed by `(experiment_id, merchant_id)`, for the 8 IDs/names across 3 synthetic merchants. Since these IDs have no real definitions in the repo (per the search above), the seed below is **synthesized** for stub purposes, using each ID's known semantics (from the consumer-repo usages in the table above) to pick a sane on/off/variant pattern per merchant — not reverse-engineered from any real Splitz data (none exists in-repo):

```json
{
  "experiments": {
    "TUnTsUB8Os2kX0": {
      "name": "payouts_shadow_gateway_scheduled_time_slots",
      "status": "activated",
      "variants": [
        {"id": "var_proxy",  "name": "proxy",     "weight": 100, "variables": [{"key": "mode", "value": "proxy"}]},
        {"id": "var_shadow", "name": "shadow_api", "weight": 0,   "variables": [{"key": "mode", "value": "shadow_api"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_proxy",
        "merchant_test_002": "var_proxy",
        "merchant_test_003": "var_shadow"
      }
    },
    "Qnc6b4fg1Hi36k": {
      "name": "merchant_config_via_asv_and_dcs_experiment",
      "status": "activated",
      "variants": [
        {"id": "var_on",  "name": "on",  "weight": 100, "variables": [{"key": "result", "value": "on"}]},
        {"id": "var_off", "name": "off", "weight": 0,   "variables": [{"key": "result", "value": "off"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_on",
        "merchant_test_002": "var_off",
        "merchant_test_003": "var_on"
      }
    },
    "Sg7tEnMoxCbTzM": {
      "name": "payout_workflows_dcs_name_experiment",
      "status": "activated",
      "variants": [
        {"id": "var_on",  "name": "on",  "weight": 100, "variables": [{"key": "result", "value": "on"}]},
        {"id": "var_off", "name": "off", "weight": 0,   "variables": [{"key": "result", "value": "off"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_off",
        "merchant_test_002": "var_on",
        "merchant_test_003": "var_on"
      }
    },
    "TMp5VcIFYOHK2m": {
      "name": "use_api_db_for_payout_status_details_experiment",
      "status": "activated",
      "variants": [
        {"id": "var_on",  "name": "on",  "weight": 100, "variables": [{"key": "result", "value": "on"}]},
        {"id": "var_off", "name": "off", "weight": 0,   "variables": [{"key": "result", "value": "off"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_on",
        "merchant_test_002": "var_on",
        "merchant_test_003": "var_off"
      }
    },
    "PEVE4sUaVG6Drw": {
      "name": "BulkPayoutsExperiment",
      "status": "activated",
      "variants": [
        {"id": "var_on",  "name": "on",  "weight": 100, "variables": [{"key": "result", "value": "on"}]},
        {"id": "var_off", "name": "off", "weight": 0,   "variables": [{"key": "result", "value": "off"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_off",
        "merchant_test_002": "var_off",
        "merchant_test_003": "var_on"
      }
    },
    "ORHtoFyuKLWUSw": {
      "name": "ledgerMakeshiftDualWriteExperiment",
      "status": "activated",
      "variants": [
        {"id": "var_on",  "name": "on",  "weight": 100, "variables": [{"key": "result", "value": "on"}]},
        {"id": "var_off", "name": "off", "weight": 0,   "variables": [{"key": "result", "value": "off"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_on",
        "merchant_test_002": "var_off",
        "merchant_test_003": "var_on"
      }
    },
    "CreateTransferMetaRollout": {
      "note": "config-field name, not a raw Splitz ID; prod resolves to Splitz ID ROyjRWsBowWdWY",
      "name": "create_transfer_meta_rollout",
      "status": "activated",
      "variants": [
        {"id": "var_on",  "name": "on",  "weight": 100, "variables": [{"key": "result", "value": "on"}]},
        {"id": "var_off", "name": "off", "weight": 0,   "variables": [{"key": "result", "value": "off"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_on",
        "merchant_test_002": "var_on",
        "merchant_test_003": "var_off"
      }
    },
    "FireStatusUpdateKafka": {
      "note": "config-field name, not a raw Splitz ID; prod resolves to Splitz ID PYGTRQEzO39PfB",
      "name": "fire_status_update_kafka",
      "status": "activated",
      "variants": [
        {"id": "var_on",  "name": "on",  "weight": 100, "variables": [{"key": "result", "value": "on"}]},
        {"id": "var_off", "name": "off", "weight": 0,   "variables": [{"key": "result", "value": "off"}]}
      ],
      "assignments": {
        "merchant_test_001": "var_off",
        "merchant_test_002": "var_on",
        "merchant_test_003": "var_on"
      }
    }
  }
}
```
A stub `Evaluate` handler should look up `assignments[experiment_id][id]`, return the matching `variants[]` entry as `variant`, and default to the first `weight:100` variant (or empty) for merchant IDs not in `assignments`, to stay deterministic across re-runs. This matches the real service's `on`/`off` convention used pervasively in these repos: `checkIfVariantEnabled` in `payouts/pkg/splitz/client.go:149-161` treats a variant as "enabled" iff it has a `Variable{Key:"result", Value:"on"}`.

### Unknowns

- **No real experiment definitions exist for any of the 8 target IDs/names in any cloned repo.** Splitz's `experiments`/`variants` tables are the sole source of truth and are DB-only — confirmed by exhaustive grep across `splitz/`, `proto/splitz/`, and `config-proto/` (migrations, `test/slit_tests`, `test/e2e`, `specs/`, `.github/`). This is not a gap in the clone; it's architectural (experiments are runtime/admin-managed, not code-deployed).
- Real Splitz IDs for `CreateTransferMetaRollout` and `FireStatusUpdateKafka` are only known for FTS **prod** (`ROyjRWsBowWdWY`, `PYGTRQEzO39PfB`); other envs use the literal placeholder string as the "ID" (e.g. `"create_transfer_meta_rollout"`), which only works because `EvaluateExperimentRequest` also accepts lookup by `experiment_name` — implying Splitz experiments in non-prod envs may be looked up by name rather than ID, or those envs' Splitz instances have experiments seeded with matching names. Could not confirm which, since no non-prod Splitz DB/seed is in-repo.
- Could not determine why `PEVE4sUaVG6Drw` appears only in `xperience/`, not `payouts/fts/ledger` as the task description implied it might — confirmed via grep it genuinely does not appear in those three repos at all.
- The exact `MurmurHash` variant/implementation (`splitz/pkg/helper` — referenced as `helper.MurmurHash` in `rule_evaluator.go` but the helper package itself wasn't opened) was not verified in detail; the seed constants (113, 919) and `bucketingId` formula are confirmed, but exact output-to-percentile mapping wasn't traced, so the "seed data" section above is a plausible stub, not a bit-exact reproduction of real Splitz bucketing.
- Did not verify whether `fts` or `ledger` also use `goutils/splitz` client-side eval (only confirmed Payouts' config explicitly enables it); a full audit of `fts`/`ledger` Splitz client config was out of scope beyond the ID grep requested.

---

## C. Governor

### Rule engine API (used by payouts ccSdk)

Call topology (confirmed from code, not inferred):

```
payouts (internal/app/payouts) --[in-process call]--> payouts/pkg/ccSdk.CalculatePayoutFee()
    -> ccSdk.feeCalculator.GetFees()  (either MockPayoutCalculator or PayoutCalculatorAdapter)
    -> ccSdkPayout.FeeCalculator (vendored SDK: github.com/razorpay/charge-collections-sdk@v1.27.1,
       pkg/calculators/payout) which internally holds THREE clients:
         - apiClient          (payouts/pkg/ccSdk: existing API-monolith client, for merchant/plan lookups)
         - governorClient     (payouts/pkg/ccSdk/client.go:178-194, always created)
         - chargeCollectionsClient (optional — only created if config.ChargeCollections.Host != "")
    -> if chargeCollectionsClient present: SDK fetches PricingPlan/Rules from Charge Collections
       service (HTTP, grpc-gateway) and ALSO evaluates Governor rule chains for routing/selection
    -> if chargeCollectionsClient absent: "falls back to Governor-only pricing"
       (payouts/pkg/ccSdk/client.go:100 log message)
```
Evidence: `payouts/pkg/ccSdk/client.go:74-106` (`initializeClients`), `:108-152` (`NewPayoutFeeCalculator`).

Governor itself is NOT called via a payouts-specific proto/Twirp contract — `payouts/pkg/ccSdk` never
imports a governor proto package; it only imports the vendored `charge-collections-sdk`'s
`ccSdkClients.IGovernorClient` interface and constructs it via
`ccSdkClients.NewGovernorClient(ctx, ccSdkClients.GovernorConfig{Hostname, Username, Password,
Timeout, AppMode, AppName, NamespaceId})` (`payouts/pkg/ccSdk/client.go:178-193`). The
charge-collections-sdk module itself is not present in the local clone (go mod cache path is outside
the sandboxed filesystem and could not be read), so the exact wire-level request Governor receives
from the SDK could not be directly inspected from payouts' side. However, Governor's OWN repo
(`governor/`) exposes its rule-evaluation surface directly, and this is what the SDK's
`IGovernorClient` talks to over HTTP with Basic Auth (`GovernorConfig.Username/Password` — see
`payouts/config/default.toml` for actual creds env var names, not read here to avoid secrets).

**Governor's own HTTP API** (gin router, `governor/app/routing/router/router.go`):
- Base auth group `ruleEngineGroup` = `route.Group("", middleware.BasicAuth(routeApps), ...)` where
  `routeApps` includes `constants.PricingAuthUserFieldName` and `constants.CommissionsAuthUserFieldName`
  (governor/app/routing/router/router.go:122-138) — i.e. Charge-Collections/Pricing-class callers are
  provisioned as a distinct Basic-Auth "app" identity, consistent with ccSdk's
  `GovernorConfig.Username/Password`.
- **Rule chain execution (the actual "evaluate" call)**:
  `POST /v1/namespaces/:namespace_id/execute` → `controllers.RuleEngine.ExecuteChainsV2`
  (router.go:157; handler at `governor/app/controllers/governor.go:371-414`).
  - Request: raw JSON body bound into `map[string]interface{}` (no fixed proto/struct — Governor's
    domain model is dynamic per namespace). Query params: `debug` (summary type), `rule_chain_ids`
    (repeatable, filters which rule chains to run). Test fixture shows the expected shape:
    ```json
    { "evaluating_entity": { "name": "test", "type": "string", "sub_type": "" } }
    ```
    (`governor/app/controllers/governor_test.go:1016-1038`). The underlying executor's real
    input contract (`governor-executor/modals/external.go:3-16`) is:
    ```go
    type EvaluatingEntity struct { Name string; Data []map[string]interface{} }
    type SupportingEntity struct { Name string; Data map[string]interface{} }
    type EvaluatingParams struct { EvaluatingEntity EvaluatingEntity; SupportingEntities []SupportingEntity }
    ```
  - Response: `models.ExecutionResponse` (`governor/app/models/response.go:63-92`):
    ```go
    type ExecutionResponse struct {
        ExecutionSummary []modals.ExecutionSummary `json:"execution_summary"`
        Success bool `json:"success"`
        Error   string `json:"error"`
    }
    ```
    where (`governor-executor/modals/external.go:18-40`):
    ```go
    type ExecutionSummary struct {
        EntityInformation EntityInfo      `json:"entity_info"`      // {entity_name, entity_id}
        ChainsExecuted    []ExecutedChain `json:"chains_executed"`
    }
    type ExecutedChain struct {
        RulesetName     string         `json:"ruleset_name"`
        ExecutedRules   []ExecutedRule `json:"executed_rules"`
        ErrorRules      []ExecutedRule `json:"error_rules"`
        CumulativeScore int            `json:"cumulative_score"`
    }
    type ExecutedRule struct {
        Rulename   string            `json:"rulename"`
        Evaluation bool              `json:"evaluation"`
        Score      int               `json:"score"`
        Meta       map[string]string `json:"meta"`
    }
    ```
  For the "debug=individual" summary mode, `ExecutionResponse.MarshalJSON` (response.go:69-91)
  re-shapes the payload into `{entity_info, chains_executed:[{ruleset_name, selected_rules,
  cumulative_score}]}`.

- **Rule-chain identity used by ccSdk** — `payouts/pkg/ccSdk/config.go:22-29`:
  ```go
  type PayoutRuleChainCollection struct {
      BasicRuleChainID          string `mapstructure:"basic"`
      ProductRuleChainID        string `mapstructure:"product"`
      BankingAccountRuleChainID string `mapstructure:"bank"`
      PayoutModeRuleChainID     string `mapstructure:"mode"`
      AmountRangeRuleChainID    string `mapstructure:"amount"`
  }
  ```
  i.e. Payouts configures FIVE separate Governor rule-chain IDs (basic / product / bank / mode /
  amount-range), one per fee-rule dimension, converted 1:1 to the SDK's
  `ccSdkPayout.RuleChainCollection` (config.go:88-97) — this directly answers "fee rules by
  product/bank/mode/amount": each dimension is its own Governor rule chain, evaluated together.

- **CRUD/admin API** (all under `adminGroup`, Basic Auth with a wider "admin" app list —
  router.go:161-236): namespace CRUD, domain-model CRUD+publish, rule CRUD+publish
  (`POST/PUT/GET /v1/rule_engine/rule/:namespace[/...]`), rule-chain CRUD+publish, rule-group CRUD,
  template CRUD, config CRUD (`/v1/namespaces/:namespace_id/config`), and a parallel
  "R-Canary" nested-namespace tree (`/v1/namespaces/:namespace_id/rule_chains/:rule_chain_id/
  rule_groups/:rule_group_id/rules`). A `passportGroup` variant of the RaaS (Rules-as-a-Service)
  merchant-scoped endpoints exists using Razorpay's internal Passport auth instead of Basic Auth
  (router.go:187-195).

### Rule format (data model)

Storage: MySQL table `rules` (migration `governor/migrations/00003_rules_table_create.go`):
```sql
create table rules (
  namespace_id int not null,
  rulename varchar(50),
  rule JSON not null,
  version int,
  state varchar(30),
  created_at timestamp NULL DEFAULT NULL,
  updated_at timestamp NULL DEFAULT NULL,
  unique(namespace_id, rulename, version),
  key rule_namespace_index (namespace_id),
  key rule_nsid_rulename_index (namespace_id, rulename),
  key rule_created_at_index (created_at),
  key rule_updated_at_index (updated_at)
);
```
i.e. each rule is a namespaced, versioned JSON blob (not fully relational) with a `state`
column driving Enabled/Disabled(awaiting-migrate) lifecycle, plus later migrations add
`indexable`, `parent_public_id`, `rule_simulation_id`/`rule_simulation_status` (canary/simulation
support) and a soft-delete `deleted_at` column
(`governor/migrations/20191118183649_add_column_indexable.go`,
`202604071200_add_column_rule_simulation_status_rules_table.go`, etc.).

Rule JSON shape, from the API DTOs (`governor/app/dtos/rules.go:9-64`):
```go
type RuleRequest struct {
    Name                string                      // rule name
    Description         string
    Score               int                         // contribution to CumulativeScore if matched
    Mode                *string                     // e.g. shadow/live-style mode gating
    SkipOnFailure       bool                        // continue chain if this rule errors
    CreatedBy           string
    AdditionalAttribute []AdditionalAttributeOfRule  // [{name, value}] free-form metadata
    DefaultExpression   *Expression                  // fallback condition
    Expression          *Expression `binding:"required"` // the actual condition (older/typed format)
    TemplateId          string
    UseCanary           bool         // RCanary: partial-traffic rollout of a rule change
    RampPercent         interface{}  // canary ramp %
    Indexable           *bool        // whether rule participates in Governor's in-memory index
}
// V2 / "router" S2S format (used for bulk creation, newer flow):
type RuleRequestV2 struct {
    BaseRuleRequest
    RuleGroupName     string
    DefaultExpression string `json:"govaluate_default_expression"`
    Expression        string `json:"govaluate_expression" binding:"required"` // govaluate expression string, e.g. "product == \"banking\" && mode == \"IMPS\" && amount > 25000"
    Indexable         bool
    UseCanary         bool
    RampPercent       interface{}
}
```
Condition language: **govaluate** expression strings (confirmed by field name
`govaluate_expression`/`govaluate_default_expression`, and by
`governor-executor/executor_benchmark_govaluate_test.go` vs.
`executor_benchmark_grule_test.go` — the executor benchmarks two candidate expression engines,
govaluate and grule, against each other; govaluate is the one wired into the JSON field names
actually used by the API, so it is the live engine). The engine execution (per-namespace rule
chain → rule group → individual rule) lives in the separate `governor-executor` module
(`governor-executor/executor.go`, `worker.go`, `job.go`) which Governor imports as
`github.com/razorpay/governor-executor` (see `governor/app/models/response.go:5-6`).
Output/fee fields are NOT part of Governor's own rule schema — Governor only returns
boolean `Evaluation` + `Score`/`CumulativeScore` + free-form `Meta map[string]string` per matched
rule (`governor-executor/modals/external.go:35-40`); it is a **selection/scoring engine**, not a fee
calculator. The actual fee amount/percentage/slab lives in **Charge Collections' Pricing `Rule`**
message (see below) — Governor's role in the payouts fee path is to pick *which* pricing rule
applies (via rule-chain evaluation keyed on product/bank/mode/amount), while Charge Collections
(or the SDK's local pricing-plan cache) holds the actual fee/tax numbers.

`migrations/rules.json` exists as a filename but is **0 bytes** in this clone — no seed rule
examples are available from it.

### Local run/seed

- `governor/docker-compose.slit.yml` — brings up only a MySQL 5.7 container (`mysql:5.7`,
  db=`governor`, user/pass=`governor`/`governor`, exposed on host port `13306`), used for Governor's
  own "SLIT" (presumably "service-level integration test") suite, not a full local Governor server.
- Makefile targets (`governor/Makefile`): `make slit-infra-up` (docker-compose up + migrate wait),
  `make slit-migrate` (run `goose`-based migrations against that MySQL), `make slit-test`
  (run tests against the infra), `make slit-infra-down`, and the composite `make slit`. There is
  no single `make run`/`make dev` target that starts the actual `governor` gin server locally in
  this Makefile excerpt — the app is started via `governor/main.go` → `router.Initialize()`
  (`governor/app/routing/router/router.go:33-66`), reading `APP_MODE`/`base_path` env, and in
  containers via `governor/dockerconf/entrypoint-devstack.sh` → `init_default_devstack.sh`'s
  `start_webserver`/`start_governor` (invokes the compiled `governor` binary), or
  `run_migrations` when `MIGRATION_CMD=up_with_db_migrations` is set (runs `./migrate -env=$APP_MODE up`
  then starts the server). `governor/Dockerfile.devstack` + `devspace.yaml` suggest a devspace/k8s-based
  local dev workflow rather than a bare docker-compose full-stack.
- `governor-executor` ships its own test/benchmark harness (`executor_test.go`,
  `executor_benchmark_govaluate_test.go`, `executor_benchmark_grule_test.go`, `run_tests.sh`,
  `build_engine.sh`) but no server — it's a library, invoked in-process by Governor.

### Charge Collections pricing contract (endpoint, request/response, call topology)

Proto source: `proto/charge_collections/pricing/v1/pricing_api.proto` (gRPC service exposed via
grpc-gateway HTTP mux in `charge-collections/internal/server/http_handler.go:153-159`,
`pricingv1.RegisterPricingServiceHandlerFromEndpoint`).

Key RPCs (service `PricingService`, `pricing_api.proto:317+`):
| RPC | HTTP | Purpose |
|---|---|---|
| `GetPricingRule` | `GET /v1/mdr/pricing/rule/{id}` | fetch one rule |
| `GetPricingPlan` | `GET /v1/mdr/pricing/plans/{id}` | fetch a plan (all its rules) |
| `GetPricingPlanForFeesCalculation` | `GET /v1/mdr/pricing/plans/fees_calculation/{id}` | the fee-calc-oriented plan fetch — this is the one the calculator SDK is expected to use to pull applicable rules for a merchant's pricing plan |
| `CreatePlan` | `POST /v1/mdr/pricing` | admin: create plan |
| `AddRuleInPlan` | `POST /v1/mdr/pricing/{plan_id}/rule` | admin: add rule |
| `UpdatePlanRule` | `PATCH /v1/mdr/pricing/{plan_id}/rule/{rule_id}` | admin: update rule |
| `AddBulkPlanRules` | `POST /v1/mdr/pricing/rules/bulk` | admin: bulk add |
| `GetPricingPlansSummary` | `GET /v1/mdr/pricing/plans_summary` | admin listing |
| `GetSupportedNetworks` | `GET /v1/mdr/pricing/networks` | reference data |

`Rule` message (`pricing_api.proto:14-49`) — the actual fee schema, full field list:
```protobuf
message Rule {
  string id = 1;
  string plan_id = 2;
  string plan_name = 3;
  string product = 4;
  string feature = 5;
  string type = 6;
  google.protobuf.StringValue app_name = 7;
  google.protobuf.StringValue gateway = 8;
  google.protobuf.StringValue procurer = 9;
  google.protobuf.StringValue payment_method = 10;
  google.protobuf.StringValue payment_method_type = 11;
  google.protobuf.StringValue payment_method_subtype = 12;
  google.protobuf.StringValue auth_type = 13;
  google.protobuf.StringValue payment_network = 14;
  google.protobuf.StringValue payment_issuer = 15;
  google.protobuf.UInt32Value emi_duration = 16;
  bool international = 17;
  string fee_bearer = 18;                              // e.g. "platform" / "customer"
  google.protobuf.StringValue receiver_type = 19;
  bool amount_range_active = 20;
  google.protobuf.UInt64Value amount_range_min = 21;
  google.protobuf.UInt64Value amount_range_max = 22;
  uint32 percent_rate = 23;
  uint32 fixed_rate = 24;
  uint32 min_fee = 25;
  google.protobuf.UInt32Value max_fee = 26;
  google.protobuf.StringValue account_type = 27;
  google.protobuf.StringValue channel = 28;
  google.protobuf.StringValue payouts_filter = 29;      // payouts-specific matcher
  string org_id = 30;
  int64 created_at = 31;
  int64 updated_at = 32;
  google.protobuf.UInt64Value expired_at = 33;
  google.protobuf.StringValue audit_id = 34;
  google.protobuf.StringValue fee_model = 35;            // e.g. "prepaid" (matches ccSdk mock's FeeModel)
  uint32 percent_rate_scale_factor = 36;
  google.protobuf.StringValue currency = 37;
}
```
`GetPricingPlanRequest` (line 55-73) filters on: `id, plan_name, org_id, feature, payment_method,
app_name, payouts_filter, account_type, payment_method_type/subtype, payment_network,
international, amount_range_active, receiver_type, procurer, fee_bearer, product, type, channels[],
payment_method_default_fallback` — i.e. the same product/bank(channel)/mode/amount-range dimensions
Governor's rule chains gate on, confirming Governor (rule *selection*) and Charge Collections
(rule *content*/fee numbers) are two halves of one pricing decision.

**Call topology (confirmed, not assumed)**: Payouts never calls Charge Collections or Governor
directly by HTTP itself — it calls the vendored `charge-collections-sdk`'s
`ccSdkPayout.FeeCalculator.GetFees()` in-process (`payouts/pkg/ccSdk/client.go:269-271`), which is
handed three already-constructed clients (API-monolith client, Governor client, optional
Charge-Collections client) and internally decides whether to enrich Governor's rule-chain result
with a live Charge-Collections pricing-plan lookup or fall back to "Governor-only pricing" if
`config.ChargeCollections.Host == ""` (`payouts/pkg/ccSdk/client.go:96-102`, log line at :100).
So: **Payouts → ccSdk (in-process) → [Governor (always) + Charge-Collections (optional)]**, not a
strict linear chain — Governor and Charge Collections are both direct dependencies of the SDK, not
Charge-Collections → Governor or vice versa (no evidence either service calls the other from this
clone).

### Stub spec

A deterministic local stub needs two fake servers (Governor + Charge Collections) OR — simpler and
already built into the codebase — **use the SDK's own mock mode**: set `CCSDKConfig.Mock = true`
(`payouts/pkg/ccSdk/client.go:55-56` and `mock.go`) to get `MockPayoutCalculator.GetFees` deterministic
responses with zero external calls. This mock (already shipped in-repo, `payouts/pkg/ccSdk/mock.go:20-98`)
is a complete, deterministic fee stub keyed on `request.Payout.{Purpose, Method, Mode, Amount, FeeType}`:
- `purpose == "rzp_fees"` or `"refund"` → fee=0, tax=0
- `purpose == "vendor_payments"` and `amount > 25000` (paisa) → fee=354, tax=64
- `method == "fund_transfer" && mode == "IMPS"` and fee>0 → fee overridden to 500, tax=90
- `fee_type == "free_payout"` → fee=0, tax=0
- otherwise, amount > 25000 → fee=354, tax=64 (default)

For a from-scratch HTTP stub (if bypassing the SDK mock), implement:

`POST /v1/namespaces/{namespace_id}/execute` (Governor `ExecuteChainsV2` contract) — request/response
JSON schema as documented above under "Rule engine API". Seed data — 3 rule chains keyed by
namespace `payouts_fees`, each returning a deterministic `ExecutionSummary`:

```json
// seed: namespace "payouts_fees", rule_chain "product" (basic/product/bank/mode/amount all identical shape)
{
  "merchant_test_001": {"selected_rules": [{"rulename": "vendor_payments_default", "evaluation": true, "score": 10, "meta": {"pricing_rule_id": "rule_vendor_default"}}], "cumulative_score": 10},
  "merchant_test_002": {"selected_rules": [{"rulename": "rzp_fees_zero", "evaluation": true, "score": 10, "meta": {"pricing_rule_id": "rule_rzp_fees_zero"}}], "cumulative_score": 10},
  "merchant_test_003": {"selected_rules": [{"rulename": "imps_flat_fee", "evaluation": true, "score": 10, "meta": {"pricing_rule_id": "rule_imps_flat"}}], "cumulative_score": 10}
}
```

`GET /v1/mdr/pricing/plans/fees_calculation/{id}` (Charge Collections contract) seed — one
`GetPricingPlanForFeesCalculationResponse` per synthetic merchant's `PricingPlanId`, each holding
one `Rule` (from the proto schema above) with concrete `percent_rate`/`fixed_rate`/`min_fee`/`max_fee`
values, e.g.:
```json
{
  "plans": [{
    "id": "plan_test_001", "org_id": "org_test_001", "count": 1,
    "rules": [{"id": "rule_vendor_default", "plan_id": "plan_test_001", "product": "banking",
               "feature": "payout", "payouts_filter": "vendor_payments", "fee_bearer": "platform",
               "percent_rate": 0, "fixed_rate": 354, "min_fee": 354, "currency": "INR",
               "fee_model": "prepaid"}]
  }]
}
```

### Unknowns
- The exact wire request the SDK's `IGovernorClient` sends to Governor's `/v1/namespaces/:id/execute`
  (i.e. how `Payout`/`Balance`/`Merchant`/`Meta`/`FundAccount` from `ccSdkPayout.Request` are mapped
  into the `EvaluatingEntity`/`SupportingEntities` shape) could not be confirmed — the
  `charge-collections-sdk` module source is not present in this clone or the sandboxed filesystem
  (go module cache path exists per `find` but is unreadable inside this sandbox — likely outside the
  permitted directory tree). This is the same limitation noted for the DCS/Splitz Governor client
  packages (`governor/app/providers/dcs`, `governor/app/providers/splitz` were seen to exist but
  not investigated — out of scope for this task).
- Whether Charge Collections' `PricingService` itself ever calls back into Governor was not found in
  either direction inside `charge-collections/` in the time available — only the `pricingv1`
  grpc-gateway registration was inspected, not its full internal implementation.
- Actual production values of the five `PayoutRuleChainCollection` IDs (basic/product/bank/mode/amount)
  are config-injected (`mapstructure:"basic","product","bank","mode","amount"` under
  `[ccSdkConfig.governor.rules]` presumably in `payouts/config/*.toml` — not located/read in this pass)
  and were not resolved to real Governor namespace/rule-chain IDs.
- No sample of a real `rule` JSON blob (the MySQL `rules.rule` column contents) was found — the
  govaluate expression syntax used in production is inferred from field names only, not an example.

---

## D. Shield

The full `shield` service repo is not cloned locally (~6.7GB, excluded). This section is built from
`payouts/pkg/shield/` (the Payouts-side client) and `shield-sdk/` (a separate, smaller Shield SDK repo
that IS cloned) — the latter had not been checked in the prior pass that produced
`findings/15_validation_risk_limits.md` unresolved item #5, and closes it (see below).

### Evaluate contract (request/response, from shield_payout_rule_evaluate.go)

Request construction, `payouts/pkg/shield/shield_payout_rule_evaluate.go:32-139` — two builders,
`PrepareShieldPayoutEvaluateRequest` (payout-object path) and
`PrepareShieldPayoutEvaluateRequestForApiPayout` (API-monolith payout path), both populate the same
`shieldSdkModels.PayoutEvaluateRequest` (imported from `github.com/razorpay/shield-sdk/models/evaluate`,
defined at `shield-sdk/models/evaluate/payout_evaluate.go:3-53`):

```go
type PayoutEvaluateRequest struct {
    MerchantID           string      `json:"merchant_id" binding:"required"`
    EntityType           string      `json:"entity_type" binding:"required"` // constants.EntityTypePayout = "payout"
    EntityID             string      `json:"entity_id" binding:"required"`   // payout ID
    RuleSets             []string    `json:"rulesets"`                       // always nil from payouts (default ruleset)
    SkipDefaultExecution bool        `json:"skip_default_execution"`         // always false from payouts
    Input                PayoutInput `json:"input" binding:"required"`
}

type PayoutInput struct {
    PayoutID, Mode, Narration, Currency, SourceAccountTypes, SourceChannel,
    SourceAccountNumber, Purpose, PurposeType, MerchantID, MerchantMCC,
    BeneficiaryName, BeneficiaryEmail, BeneficiaryMobile, VPA, AccountNumber, IFSC,
    UserID, SourceIP, SourceDevice, SourcePlatform, OS, PaymentProduct,
    MerchantType, ConstitutionType, EntityType, CardLength string
    PayoutAmount, MerchantCreatedAt, CreatedAt, FundAccountCreatedAt int64
    Balance                                                          float64
    EvaluationUniqueness, International                             bool
}
```
Fields Payouts actually populates (`shield_payout_rule_evaluate.go:40-68`, `:93-121`): PayoutID,
PayoutAmount, Mode, Narration, Currency, SourceAccountTypes/Channel/Number, Purpose, PurposeType,
MerchantID, MerchantCreatedAt, MerchantMCC, Beneficiary{Name,Email,Mobile}, VPA, AccountNumber, IFSC,
UserID, SourceIP (from context), CreatedAt, plus "NEW PARAMETERS" MerchantType,
FundAccountCreatedAt, ConstitutionType. `SourceDevice`, `SourcePlatform`, `OS`, `PaymentProduct`,
`Balance`, `EvaluationUniqueness`, `International`, `CardLength` are declared in the shared model but
NOT set by the payout path (payment-evaluation-only fields).

Response, `payouts/pkg/shield/shield_payout_rule_evaluate.go:24-30` (payouts' local mirror of
`shieldSdkModels.PayoutEvaluateResponse`, same shape as the SDK's own
`shield-sdk/models/evaluate/payout_evaluate.go:15-19`):
```go
type CreateShieldEvaluateResponse struct {
    Action         string                 `json:"action"`
    TriggeredRules map[string]interface{} `json:"triggered_rules"`
    StatusCode     int64                  `json:"status_code"`
}
```

Wire call: `req.Client.EvaluatePayout(ctx, requestBody)` (shield_payout_rule_evaluate.go:150) →
SDK's `rule_evaluation.Client.EvaluatePayout` (`shield-sdk/rule_evaluation/service.go:88-119`), which
POSTs to `getShieldEvaluateEndpoint("payout")` = `fmt.Sprintf("%s/%s", constants.PaymentRuleEvaluationEndpoint, entityType)`
= **`/v1/rules/evaluate/payout`** (`shield-sdk/rule_evaluation/service.go:121-126`,
`constants/rule_evaluation.go:4`: `PaymentRuleEvaluationEndpoint = "/v1/rules/evaluate"`). Plain
JSON-over-HTTP POST (not Twirp/gRPC) — `models.HttpRequestParams{Method: "POST", Endpoint, Payload}`
via `shield-sdk/common/http_client`.

### Possible action values (definitive list from shield-sdk if found; payouts-handled subset; fail-open/fail-closed default)

**Definitive list — found in shield-sdk**, `shield-sdk/constants/http.go:13-15`:
```go
const (
    ActionAllow  = "allow"
    ActionBlock  = "block"
    ActionReview = "review"
)
```
This is authoritative and **resolves the "beyond block" question**: Shield's contract defines three
actions — `allow`, `block`, `review` — not just `block`.

**Payouts-handled subset**: Payouts code only explicitly branches on one value. In
`payouts/internal/app/payouts/core.go:6883`:
```go
if res.GetStatusCode() == 200 && res.GetAction() == ValueBlock {
    return true   // block the payout
}
return false      // proceed
```
where `ValueBlock = "block"` (`payouts/internal/app/payouts/core.go:223`). There is no `case "review":`
or any other branch anywhere in `payouts/pkg/shield/*.go` or the call site — confirmed by
`grep -rn "\"allow\"\|\"review\"\|\"hold\"" payouts/pkg/shield/*.go` returning nothing beyond the
one `"block"` test fixture (`shield_payout_rule_evaluate_test.go:140,154`).

**Fail-open confirmed at two levels**:
1. On a client/HTTP error (`err != nil` from `req.Create()`), the caller
   (`payouts/internal/app/payouts/core.go` around line 6858-6867, function evaluating shield rules)
   logs and `return false` — i.e. **does not block**. Errors fail open.
2. On any successful response whose `Action` is `"allow"`, `"review"`, or any unrecognized string
   (empty, future enum values, etc.), the `== ValueBlock` check is false, so the payout also
   **proceeds** — i.e. **`review` is silently treated identically to `allow`** by Payouts today.
   There is no distinct "hold for manual review" code path on the Payouts side; if Shield returns
   `review`, Payouts currently just lets the payout continue exactly as if Shield had said `allow`.

Also note the very short configured timeout (`payouts/config/default.toml:312-328`, `[shield]
timeout = 200` / `[shield.httpclient.httpclient] timeout = 200`, i.e. 200ms) makes fail-open the
practically dominant outcome under any latency pressure — see Timeout section.

### Rule definitions relevant to payouts

- No named Shield rule-set/rule-ID constants for payouts were found in `payouts/pkg/shield/` or
  `shield-sdk/` — `RuleSets []string` is always sent as `nil` from Payouts
  (`shield_payout_rule_evaluate.go:74,127`), meaning Payouts always asks Shield to run its
  **default** rule set for `entity_type=payout`; it never asks for a named subset.
  `SkipDefaultExecution` is always `false`, i.e. Payouts never opts out of the default rules either.
  The actual rule definitions (which named rules exist, their conditions) live server-side in
  Shield's own DB, not in either cloned repo.
- Cross-checking `findings/15_validation_risk_limits.md`'s open items:
  - `for_duplicate_payout_evaluate` is **not a Shield construct** — it is a **Splitz** experiment
    identifier, confirmed at `payouts/internal/app/payouts/processor/base.go:1554-1567`
    (`appConstants.AttributeSplitzExperimentIdentifier, "for_duplicate_payout_evaluate"`). So
    duplicate-payout detection enforce/shadow mode is Splitz-gated, not a Shield rule — this
    re-scopes (not resolves) findings/15 unresolved item #6.
  - `exclude_payout.parameter` — confirmed live in `payouts/config/default.toml:330-333`:
    ```toml
    [shield_payout_evaluate.exclude_payout.parameter]
        purpose = ["rzp_fees"]
    ```
    consumed by `shouldSkipPayoutForShieldEvaluation` (`payouts/internal/app/payouts/core.go:8079-8110`):
    it converts the `PayoutInput` struct to a map, and for each configured key (here only `purpose`)
    checks whether the request's value is in the configured allow-list of *values to exclude*; if so,
    Shield evaluation is skipped entirely for that payout (logged, `return false` = not blocked,
    Shield never called). Confirms findings/15 item #7: only `purpose=rzp_fees` is excluded in the
    default config; this is a purpose-keyed map, so any other `PayoutInput` field (e.g. `mode`,
    `merchant_id` is not itself a field of `PayoutInput`... actually `MerchantID` IS a field, so a
    merchant-level exclusion IS mechanically possible by adding `merchant_id = ["..."]` to this same
    TOML map) — no such merchant allowlist exists in `default.toml` today.

### Timeout expectations (fail-open vs fail-closed)

- `payouts/config/default.toml:312-328`:
  ```toml
  [shield]
      host = "https://shield-payout-int.razorpay.com"
      timeout = 200                # ms — connection-pool-level timeout
      keepAliveTimeout = 30000
      maxIdleConnections = 10
      httpClientName = "shield"
      [shield.auth]
          username = "PAYOUTS_SHIELD_AUTH_USERNAME"   # env var name, not the secret
          password = "PAYOUTS_SHIELD_AUTH_PASSWORD"
      [shield.httpclient.resiliency]
          maxconcurrentrequests = 100
          requestvolumethreshold = 20
          CircuitBreakerSleepWindow = 5000
          errorpercentthreshold = 50
          circuitbreakertimeout = 30000
      [shield.httpclient.httpclient]
          timeout = 200             # ms — per-request timeout (duplicated, confirms 200ms)
  ```
  So the effective per-call Shield timeout in Payouts is **200 milliseconds**, with a Hystrix-style
  circuit breaker (opens after ≥20 requests with ≥50% error rate in the window, 5s sleep window,
  30s breaker timeout).
- `shield-sdk`'s own `DefaultHTTPConfig` (`shield-sdk/common/http_client/config.go:18-35`) ships a
  1000ms default `ConnPoolConfig.Timeout` and a 30000ms `CircuitBreakerTimeout` — Payouts overrides
  the connection timeout down to 200ms in its own config, i.e. Payouts is intentionally more
  aggressive/fail-fast than the SDK default.
- **Behavior on timeout**: identical to the generic error path above — `req.Create()` returns a
  non-nil `err`, the caller logs `trace.PayoutShieldEvaluateRequestFailed` and `return false`
  (`payouts/internal/app/payouts/core.go` ~6858-6867). **Fail-open**: a Shield timeout never blocks a
  payout; it is treated exactly like Shield saying "allow".

### UNRESOLVED_QUESTIONS closed

**raw-findings/15 unresolved item #5 ("Shield semantics beyond `block`") is now RESOLVED** at the
API-contract level, and additionally clarified at the Payouts-integration level:
1. Shield's contract (per `shield-sdk/constants/http.go:13-15`) defines three actions:
   `allow`, `block`, `review`. This was previously "unknown" because shield-sdk had not been
   checked (or wasn't available) in the prior pass; it is fully vendored in this clone and readable.
2. Payouts' payout-evaluation integration (`payouts/internal/app/payouts/core.go:6883`) only branches
   on `"block"`. `review` is real (Shield can return it) but Payouts has **no distinct handling** for
   it — a `review` verdict is currently indistinguishable, from the payout's perspective, from
   `allow`. This is a meaningful residual gap worth flagging to the payouts team (not a further
   "unknown" — it's a confirmed behavior, just possibly not the intended one), since a rule author
   configuring Shield to return `review` for a payout use case would have no effect in Payouts today.

### Stub spec

Endpoint: `POST /v1/rules/evaluate/payout` (per `getShieldEvaluateEndpoint`, entityType="payout"
concatenated onto the payment base path — see contract above). Auth: HTTP Basic
(`shield.auth.username`/`password` from Payouts config). Content-Type: `application/json`.

Request schema: `PayoutEvaluateRequest` as documented above (merchant_id, entity_type="payout",
entity_id, rulesets, skip_default_execution, input:{PayoutInput fields}).

Response schema:
```json
{ "action": "allow|block|review", "triggered_rules": { "...": "..." }, "status_code": 200 }
```

Deterministic seed for 3 synthetic merchants (stub server: match on `input.merchant_id`, fall back to
`allow` for anything unmatched):

```json
{
  "merchant_test_001": {
    "match": {"purpose": "vendor_payments"},
    "response": {"action": "block", "triggered_rules": {"velocity_check": "exceeded_daily_limit"}, "status_code": 200}
  },
  "merchant_test_002": {
    "match": {},
    "response": {"action": "allow", "triggered_rules": {}, "status_code": 200}
  },
  "merchant_test_003": {
    "match": {},
    "response": {"action": "review", "triggered_rules": {"new_beneficiary_high_amount": "flagged"}, "status_code": 200}
  }
}
```
Note for stub consumers: because Payouts' current integration treats `review` identically to
`allow` (see above), a test asserting "review implies payout paused" against real Payouts code will
fail — the stub faithfully reproduces Shield's contract, but exercising `review` will not currently
change Payouts' behavior. A fourth optional seed entry can simulate the exclude-list bypass
(`purpose=rzp_fees` payouts never call Shield at all per `payouts/config/default.toml:330-333`) — the
stub should never receive a request for such a payout if that config path is exercised correctly.

### Unknowns
- Shield's actual named rule catalogue (rule IDs/conditions relevant to payouts) lives server-side
  in the un-cloned `shield` repo — not determinable from `payouts/pkg/shield` or `shield-sdk` alone.
- Whether any Payouts code path OTHER than `core.go:6883`/`EvaluatePayoutShieldRulesForApiPayout`
  reads the `Action` field differently (e.g. dashboard-side review queues consuming the persisted
  `MetaNameShieldEvaluateResponse` payout-meta record written at `core.go` ~6869-6878) was not
  traced — the meta blob IS persisted per payout even when the action isn't `block`, so a downstream
  consumer (ops dashboard?) could theoretically act on `review` later; this was not confirmed either
  way in the time available.
- `PaymentEvaluateRequest`/`PaymentEvaluateResponse` (the payment, not payout, evaluate path) were
  not investigated — out of scope, payouts-only per task.

---

## UNRESOLVED_QUESTIONS closed (summary across A-D)

- **Item 15 (Splitz live variants — repo may hold experiment definitions/seeds): CLOSED, definitively
  "no".** Exhaustive search of `splitz/`, `proto/splitz/`, and `config-proto/` (source, migrations,
  test fixtures, specs) found zero experiment definitions for any of the 8 requested IDs/names, and
  in fact zero concrete experiment definitions of any kind. Splitz experiments live exclusively in
  its MySQL `experiments`/`variants` tables, managed at runtime via the admin UI/Twirp
  `ExperimentAPI`/MCP tools — this is architectural, not a gap in the clone. See Section B for the
  full per-ID trace of where each ID is *referenced* (as opposed to *defined*) across
  payouts/fts/ledger/xperience.
- **Item 25 (DCS export path and both flag names): PARTIALLY CLOSED.** The export/legacy-interop
  mechanism is confirmed to be the Proxy service's dual-write/authority-gating system, not a
  push/Kafka export. The best-match "two flag names" are `DCSFeature.write_via_client` and
  `DCSFeature.read_enabled_via_dcs` (`proto/dcs/proxy/v1/proxy.proto:40-49`), which jointly gate
  whether DCS or the legacy API-monolith MySQL table is authoritative for a given legacy feature
  flag's read/write path. A third, related flag `dual_write_enabled` (paired with Splitz experiment
  `dcs_kv_dual_write_enabled`) governs the reverse-sync from the new DCS KV store back to the legacy
  Aurora table. Left open: whether the task's "two flags" phrase specifically meant the
  `write_via_client`/`read_enabled_via_dcs` pair or something else — no single doc in the repo names
  a canonical pair, so this determination is inferred from code behavior (see Section A, Export path,
  and Unknowns).
- **raw-findings/15 unresolved item #5 (Shield semantics beyond `block`): CLOSED.**
  `shield-sdk/constants/http.go:13-15` definitively lists three actions — `allow`, `block`, `review`.
  Payouts' payout-evaluation code (`payouts/internal/app/payouts/core.go:6883`) only branches on
  `"block"`; both `"allow"` and `"review"` (and any HTTP/timeout error) are treated identically as
  "do not block" (fail-open). This is a confirmed, cited finding — not merely narrowed uncertainty —
  though see Section D's Unknowns for one adjacent unconfirmed thread (whether a downstream
  dashboard/ops consumer reads the persisted Shield-response payout-meta differently).

## Consolidated unknowns (cross-cutting, not fully resolved by this pass)

1. DCS: whether the `write_via_client`/`read_enabled_via_dcs` pair is truly "the" two flags the task
   had in mind, vs. some other candidate; whether the `consumer` Kafka binary publishes KV changes
   (a possible fourth export mechanism); whether DCS injects non-zero server-side defaults.
2. Splitz: exact non-prod ID-vs-name lookup behavior; exact MurmurHash bucketing implementation
   (stub seed assignments are plausible, not bit-exact); why `PEVE4sUaVG6Drw` is xperience-only.
3. Governor: the exact wire shape `charge-collections-sdk` sends to Governor's `/execute` endpoint
   (SDK source unreadable from this sandbox — go module cache outside permitted filesystem);
   whether Charge Collections calls back into Governor; real production rule-chain IDs; no real
   `rules.rule` JSON sample was found (`migrations/rules.json` is 0 bytes).
4. Shield: the real Shield rule catalogue (server-side, repo not cloned); whether any Payouts
   consumer other than the block-check reads `review`/`triggered_rules` from the persisted payout
   meta record.
