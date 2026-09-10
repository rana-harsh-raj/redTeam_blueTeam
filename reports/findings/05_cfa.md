# CFA (Contacts & Fund Accounts) — Architecture Findings

Repo: `razorpay/cfa` @ `d488558e162c08e9fa04dff005f27d846ce88ad2` (branch master, "ifsc_updated_2.0.61 (#190)")
Lane: CFA — contacts, fund accounts, beneficiary validation.

Method note: this repo ships an extensive in-repo agent-skill doc set at
`.agents/skills/repo-skill/` (36 files, extracted 2026-05-04) that was used to accelerate
navigation. All load-bearing claims below (routes, auth/ACL, validation logic, migration
flags, DB indexes, outbound clients, events) were cross-checked directly against source —
file:line citations point at real code, not just the doc set. Where the docs and code
disagreed or the docs were stale (e.g. "lazy-load disabled"), the code's actual current
state is reported and flagged.

---

## Repo Summary

- **Stack:** Go, gRPC (port 8080 default) + HTTP/REST via grpc-gateway (port 8081), health on
  a separate internal port. MongoDB is the primary store; MySQL ("API DB", the API monolith's
  database) is a read fallback during migration; Elasticsearch integration exists but indexing
  is disabled (contact search falls back to MySQL).
- **Deployable processes (`cmd/`):**
  - `cmd/server/main.go` — serves gRPC+HTTP+health, publishes events to SQS.
  - `cmd/worker/main.go` — SQS worker; registers `contact_lazy_load` and
    `fund_account_lazy_load` job handlers; also boots a health-only server.
  - `cmd/migration/main.go` — standalone Mongo migration runner (`go run cmd/migration/main.go up|down`).
- **Deployment:** Spinnaker app `devstack-mum-rspl-cfa` for devstack; templated Spinnaker
  pipelines at `razorpay/spinacode/v3/cfa/` for production, with progressive canary per region
  (per `AGENTS.md`; Spinnaker manifests themselves are not in this repo). CI-devstack workflow
  (`.github/workflows/ci-devstack.yml`) builds/deploys on qualifying pushes. No Dockerfile at
  repo root; build images live under `build/docker/` (`Dockerfile.in`, `Dockerfile.ci`,
  `Dockerfile.cov`) and are driven by `Makefile`/`build/build.sh`. No k8s manifests in-repo
  (`devspace.yaml` present for local devstack workflow).
- **Proto:** consumed as a git submodule (`.gitmodules` → `github.com/razorpay/proto.git` at
  path `proto/`), not checked out in this shallow clone. Generated stubs live in `rpc/x/x-cfa/…`
  and were used directly for route/field verification.
- **No CODEOWNERS file found** in `.github/` or repo root.

---

## Findings List

| Claim | File:Symbol | Evidence | Confidence | Capability |
|---|---|---|---|---|
| CFA exposes gRPC+HTTP via grpc-gateway; HTTP verbs/paths generated from proto `google.api.http` annotations | `rpc/x/x-cfa/fund_account/v1/fund_account_api.pb.gw.go:706-718`, `rpc/x/x-cfa/contact/v1/contact_api.pb.gw.go:544-552` | `pattern_FundAccountAPI_CreateFundAccount_0 = ...NewPattern(...[]string{"v1","fund_accounts"}...)` | High | route |
| Inbound auth = HTTP Basic Auth (constant-time compare, 2 passwords/user for rotation), then per-username ACL by full gRPC method name | `internal/server/interceptor/basicauth.go:97-140`, `internal/server/interceptor/authz.go:76-101` | `subtle.ConstantTimeCompare(...Password1...) == 1 \|\| ...Password2...`; `allowedMethods := a.acl[service]` | High | authorize |
| ACL clients: `API` (full contact+FA access), `Payouts`, `XPerience`, `Validx`, `Wallet` (GetFundAccountById only), `Admin`/`Cron` (empty ACL — no methods granted), plus per-named `Dev` users scoped only to `BankIdentifierCorrection` | `internal/server/interceptors.go:17-90` | see acl map literal | High | authorize |
| Merchant isolation is enforced by filtering every CFA-store query on `merchant_id` extracted from request context (from `X-Razorpay-Account` upstream, propagated via `contextkey.MerchantIDTagKey`) — not a DB-level constraint | `internal/fund_accounts/service.go:228-231` (per repo-skill doc, verified pattern used repo-wide), `internal/contextkey/contextkey.go:29` | filter `{id: id, merchant_id: merchantID}` | High | authorize/route |
| Two open (unauthenticated) methods: liveness/readiness health checks | `internal/server/interceptors.go:11-14` | `openMethods = []string{".../ReadinessCheck", ".../LivenessCheck"}` | High | route |
| Idempotency for Create is via hash-based dedup (SHA3-256 over type-specific fields + merchant_id), not an idempotency-key header; hitting an existing hash returns the existing entity with `is_created:false` | `internal/fund_accounts/service.go` (flow doc lines 95-106, 138-178), `internal/hashlookup/repository.go` | hash_lookup collection query before insert | Medium-High (doc-derived, hash logic itself in validate/model files confirmed) | persist/route |
| **No penny-drop / live bank-account or VPA verification happens inside CFA.** Bank account validation is purely structural: IFSC format (11 chars, 5th char '0', alphanumeric) + optional static `IFSC.json` branch lookup; account number length/alphanumeric check; beneficiary-name regex. VPA validation is format-only (`user@handle` regex, length, no consecutive/leading/trailing dots). `ValidateCard()` is a no-op (`return nil`) — card correctness is delegated entirely to Vault/BIN/API steps | `internal/fund_accounts/validate.go:73-165` (bank), `:167-207` (VPA), `:265-268` (`func ValidateCard(c *card.Card) errors.IError { return nil }`) | code shown above | High | reject/transform |
| A `skip_ifsc_lookup` per-merchant DCS feature flag lets bank-account validation skip the static IFSC.json branch-code lookup entirely, deferring "IFSC authority to the banking gateway" — comment explicitly names a **"ValidX/FAV composite flow"** as the reason this flag exists, implying real bank-account verification (Fund Account Validation / penny-drop) happens in an external service (ValidX) that calls CFA, not the reverse | `internal/fund_accounts/ifsc.go:29-38`, `internal/fund_accounts/validate.go:100-129`, `internal/fund_accounts/service.go:105-118` | `s.dcsFeatures.IsFeatureEnabled(ctx, merchantID, dcsservice.FeatureSkipIFSCLookup)` | High | route/reject |
| `Validx` is a first-class registered ACL client with rights to `CreateContact`, `UpdateContact`, `GetContactById`, `CreateFundAccount`, `GetFundAccountById`, `UpdateFundAccount`, `GetFundAccountsByIds`, `FundAccountsFetchMultiple`, `ContactsFetchMultiple` — i.e. ValidX (presumably RazorpayX's Fund Account Validation service) is an **inbound caller** of CFA, not a callee | `internal/server/interceptors.go:67-79`, `internal/server/interceptors_test.go:118-120` | acl map + test asserting validx_user has CreateContact/CreateFundAccount | High | authorize |
| No fund-account "verified/unverified" state field exists in the FundAccount model; `active` (bool, soft-delete) is the only lifecycle flag CFA persists. **An unvalidated fund account is NOT structurally blocked by CFA from being used for a payout** — CFA has no verification-state gate; any downstream block on invalid/unvalidated accounts must be enforced by Payouts/ValidX, outside this repo | `internal/fund_accounts/model.go` (Active field per repo-skill constraints.md:104-121, cross-checked against `service.go:279-282` update logic) | `Active bool` field; update flow only toggles active | Medium-High | observe (gap noted) |
| Card numbers are never persisted in MongoDB/MySQL: raw PAN is sent to Vault, only `vault_token`+`global_fingerprint` are stored; raw number cleared from the request object after tokenization | `internal/fund_accounts/card/service.go:1095-1135` (setVaultTokenAndFingerPrint), `internal/fund_accounts/service.go:70-78` (sanitizeCardDetails, per flows.md) | Vault call `cardVault.GetTokenAndFingerprint()`; card fields cleared after | High | transform/persist |
| Bank account number is immutable post-creation; only IFSC and name can be updated on a bank fund account. Attempting to change `account_number` returns an error instructing the caller to create a new fund account | `internal/fund_accounts/service.go:284-351` (per flows.md/decisions.md), validated against `internal/fund_accounts/validate.go` shared `validateBankAccount()` used by both create and update paths | error path: "cannot update account number, create a new fund account instead" | Medium-High (doc-derived, validate.go confirmed as shared code path) | reject |
| `GetFundAccountById`/`GetContactById` fall back to the API-monolith MySQL DB (via a Spine ORM "API DB" store) when not found in CFA Mongo — classic strangler-fig read pattern during migration; `UpdateFundAccount` has **no** such fallback (CFA Mongo is authoritative for writes) | `internal/fund_accounts/service.go` (flows.md Flow 2 & Flow 3), config `APIStore storage.Config` in `internal/config/config.go:37` | dual-store read pattern | Medium-High (doc-derived; config field confirmed) | route/persist |
| `FundAccountsFetchMultiple` (`GET /v1/fund_accounts`) currently **always** queries the API MySQL DB directly and never queries CFA Mongo — meaning fund accounts created after migration-cutover may not appear in list/filter queries unless `FundAccountFetchMultipleConfig.EnableCFADB`/`EnableTiDB` flags are on | `internal/config/config.go:88-93` (`FundAccountFetchMultipleConfig{EnableCFADB, EnableTiDB}`) | struct + comment "controls which data sources are used ... When both are false, only API DB is used (current behavior)" | High | route |
| Dual-write: on Create/Update, CFA publishes an in-memory event (`contact.created/updated`, `fund_account.created/updated`) which is fanned out to a `DualWriteSubscriber` that pushes a JSON payload to SQS with a **30-second delay**; an external API-monolith worker (not in this repo) consumes it and writes to MySQL | `internal/eventsystem/subscribers/contact_dual_write.go`, `internal/eventsystem/subscribers/fund_account_dual_write.go` (per queues.md, cross-checked against event system wiring) | queue config keys `cfa-*-dual-write-queue`; SQS delay=30s | Medium-High (doc-derived; queue/event scaffolding files present in repo listing) | route/persist |
| Lazy-load (API MySQL → CFA MongoDB backfill) event publish is **commented out** in both `contacts/service.go` and `fund_accounts/service.go` GetByID paths — worker handlers (`internal/job/contact_lazy_load/`, `internal/job/fund_account_lazy_load/`) exist and are registered in `cmd/worker/main.go`, but are not currently triggered by normal read traffic | `cmd/worker/main.go:65-66` (`contact_lazy_load.RegisterHandlers(...)`, `fund_account_lazy_load.RegisterHandlers(...)`), lazy-load trigger commented per quick-ref.md/flows.md | RegisterHandlers calls present; trigger call commented out | High (RegisterHandlers confirmed directly; comment-out claim doc-derived) | route/persist |
| An explicit **manual dual-write endpoint** exists for backfill/migration ops: `POST /v1/fund_accounts/dual_write` / `POST /v1/contacts/dual_write` (`FundAccountsDualWrite`/`ContactsDualWrite` RPCs), restricted to the `API` ACL client, iterates given IDs and republishes create events | `internal/api_controller/fund_account/server.go:560-608` (`FundAccountsDualWrite`), route pattern `rpc/.../fund_account_api.pb.gw.go:714` (`v1/fund_accounts/dual_write`) | handler signature + gw route | High | route/persist |
| **Admin bulk-mutation endpoint** `BankIdentifierCorrection` (`POST /v1/fund_accounts/bank_identifier_correction`) lets a caller batch-correct bank identifiers (IFSC/bank code) on existing fund accounts by merchant_id+fund_account_id; access is restricted to a static set of **named developer credentials** (per-person Basic Auth, for audit traceability), not a general service account | `internal/api_controller/fund_account/server.go:478-558`, `internal/server/interceptors.go:81-86` (`for _, devUser := range auth.Dev { acl[devUser.Username] = []string{".../BankIdentifierCorrection"} }`) | handler + ACL loop | High | authorize/transform/persist |
| Card creation is a 4-external-service sequential flow: (1) Vault tokenization (`internal/vaultservice/client.go`, required), (2) BIN/IIN metadata lookup (`internal/binservice/client.go`, optional/graceful-degrade), (3) Token service for saved-card/token_id reuse (`internal/tokenservice/client.go`, conditional), (4) API-monolith beneficiary registration (`internal/apiservice/client.go`, `RegisterBeneForCardFundAccount`, required — no rollback if it fails after Vault succeeds) | `internal/fund_accounts/card/service.go:51-146` (HandleCardCreate), client files listed | function names/comments confirmed present in each client file (Config/Auth/BaseURL structs read directly) | High | route/transform/persist |
| Each of the 4 external clients (Vault, BIN, Token, API monolith) uses its own `Config{BaseURL, Auth{Key,Secret}, Mock bool}` (basic-auth style key/secret pairs) sourced from `internal/config/config.go`; `MaxRetryCount = 1` on API/BIN clients (single retry), Vault client has `RequestTimeout=20s` / `InternalRequestTimeout=5s`, Token client has `defaultRequestTimeout=30s` with `retryBackoff=100ms` | `internal/apiservice/client.go:15-42`, `internal/binservice/client.go:13-49`, `internal/vaultservice/client.go:17-56`, `internal/tokenservice/client.go:12-38` | constants/config structs shown in code excerpts above | High | route |
| DCS (Dynamic Config Service) is CFA's feature-flag gateway; CFA reuses **Payouts'** DCS namespace/credentials (`rzp/x/merchant/payouts/cfa.proto`, message `Cfa`) rather than having its own DCS namespace — a direct architectural coupling to Payouts | `internal/dcsservice/client.go:1-6` (package doc comment) | "CFA reuses the payouts DCS namespace and credentials, so feature flags live under the rzp/x/merchant/payouts/* key space" | High | route |
| Two DCS-gated feature flags identified in fund-account validation: `skip_ifsc_lookup` (per-merchant, via `dcsservice.FeatureSkipIFSCLookup`) and a config-level (not DCS) `UseRelaxedBeneficiaryNameRegex` toggle that switches beneficiary-name regex to mirror API-monolith PHP `preg_match` partial-match semantics | `internal/fund_accounts/service.go:105-118`, `internal/config/config.go:95-104`, `internal/fund_accounts/validate.go:19-27` | flag names + regex variable names shown | High | reject/transform |
| No unique DB index on `hash` in any of `contacts`, `fund_accounts`, or `hash_lookup` collections — deduplication is enforced purely by application-layer read-then-write logic, not a DB constraint (race condition possible under concurrent creates) | `internal/database/migrations/1715709457_create_contacts_collection.go:88`, `1715709459_create_fund_accounts_collection.go:78`, `1715709458_create_hash_lookup_collection.go` (no unique index block at all) | grep confirmed "non-unique" comments; no `Unique: true` option found | High | observe |
| A dedicated migration exists to scan-and-report existing duplicate contacts/fund_accounts/hash_lookup entries (introduced 2025-02-12, version `1739361599`) — implies duplicate records are a known operational issue | `internal/database/migrations/1739361599_identify_duplicates.go:1-40` | `NewIdentifyDuplicates()`, `identifyContactDuplicates`, `identifyFundAccountDuplicates` | High | observe |
| Wallet validation currently only accepts provider `amazonpay` (hardcoded allow-list of exactly one), contradicting broader "any wallet provider" assumptions in older docs | `internal/fund_accounts/validate.go:210-263` (`ValidateWallet`, `validProviders := map[string]bool{WalletProviderAmazonPay: true}`) | code excerpt above | High | reject |
| `internal/fund_accounts/tidb/fetcher.go` + `Wda wda.Config` in top-level config indicate an experimental/optional path to query the API monolith's data via **TiDB** (WDA = presumably "Warehouse Data Access" or similar internal Razorpay lib) as an alternative to the MySQL API DB, gated by `FundAccountFetchMultipleConfig.EnableTiDB` | `internal/config/config.go:67-70` (`Wda wda.Config` comment: "used when FundAccountFetchMultiple.EnableTiDB is true"), `internal/fund_accounts/tidb/fetcher.go` (file present) | comment + file existence | Medium (file existence confirmed, internal logic not read) | route |

---

## Route Table

| Method (HTTP) | Path | RPC | ACL-permitted callers | Notes |
|---|---|---|---|---|
| POST | `/v1/contacts` | `ContactAPI/CreateContact` | API, XPerience, Payouts, Validx | supports inline dedup by hash |
| PATCH | `/v1/contacts/{id}` | `ContactAPI/UpdateContact` | API, XPerience, Payouts, Validx | |
| GET | `/v1/contacts/{id}` | `ContactAPI/GetContactById` | API, XPerience, Payouts, Validx | Mongo primary, MySQL fallback (per docs) |
| GET | `/v1/contacts` | `ContactAPI/ContactsFetchMultiple` | API, XPerience, Payouts, Validx | ES search decision logic (per docs, not independently re-verified) |
| POST | `/v1/contacts/dual_write` | `ContactAPI/ContactsDualWrite` | API only | internal migration/backfill endpoint |
| POST | `/v1/fund_accounts` | `FundAccountAPI/CreateFundAccount` | API, XPerience, Payouts, Validx | card path triggers Vault/BIN/Token/API monolith calls |
| GET | `/v1/fund_accounts/{id}` | `FundAccountAPI/GetFundAccountById` | API, XPerience, Payouts, Validx, Wallet | Mongo primary, MySQL fallback |
| PATCH | `/v1/fund_accounts/{id}` | `FundAccountAPI/UpdateFundAccount` | API, Payouts, Validx | no MySQL fallback; only `active`/bank IFSC+name mutable |
| POST | `/v1/fund_accounts/fetch_by_ids` | `FundAccountAPI/GetFundAccountsByIds` | Payouts, Validx | batch fetch w/ contact hydration, Mongo+MySQL merged |
| GET | `/v1/fund_accounts` | `FundAccountAPI/FundAccountsFetchMultiple` | API, XPerience, Payouts, Validx | currently always MySQL-only unless EnableCFADB/EnableTiDB flags set |
| POST | `/v1/fund_accounts/bank_identifier_correction` | `FundAccountAPI/BankIdentifierCorrection` | named Dev users only | admin bulk-mutation, batch corrections |
| POST | `/v1/fund_accounts/dual_write` | `FundAccountAPI/FundAccountsDualWrite` | API only | internal migration/backfill endpoint |
| GET | `/ready` | `HealthService/ReadinessCheck` | open (no auth) | Mongo connectivity, goroutine/GC thresholds |
| GET | `/live` | `HealthService/LivenessCheck` | open (no auth) | basic alive check |

Note: `Admin` and `Cron` ACL entries are registered as valid Basic-Auth credentials
(`internal/server/interceptors.go:41-42`) but are granted an **empty method list**, i.e. they
currently authenticate successfully but are authorized for nothing — likely placeholders for
future admin/cron endpoints.

---

## Validation / State Machine

CFA does **not** implement a Fund Account Validation (FAV) / penny-drop state machine. There
is no `status` or `verification_state` field on the fund account model — only `active`
(bool). Validation performed inside CFA is exclusively **structural/format validation** at
create/update time:

```
Bank Account:  IFSC present → length==11 → alphanumeric → [skip_ifsc_lookup flag?]
                 → (if not skipped) static IFSC.json branch lookup
                 → account_number present → length in [Min,Max] → alphanumeric
                 → beneficiary name present → length bounds → regex (strict or relaxed
                   per UseRelaxedBeneficiaryNameRegex config)
               (all synchronous, in-process; no external bank-rail call)

VPA:           username present → handle present → total length bounds
                 → username regex → handle regex → no consecutive/leading/trailing dots
               (no @-handle whitelist; no live UPI directory check)

Wallet:        provider must be exactly "amazonpay" (hardcoded allow-list of one)
                 → phone (if present) validated via phonebook lib for amazonpay
                 → email format regex → name length/regex

Card:          ValidateCard() is a no-op — correctness delegated to the
               Vault → BIN → (Token) → API-monolith sequential pipeline;
               no Luhn check inside CFA
```

**Real bank/UPI verification (penny-drop / FAV)** is inferred to live in the external
**ValidX** service (RazorpayX Fund Account Validation), which is registered as an *inbound*
CFA client (calls `CreateContact`/`CreateFundAccount`/`UpdateFundAccount`/`GetFundAccountById`
etc.), and the `skip_ifsc_lookup` flag's own code comment names a "ValidX/FAV composite flow"
as the reason CFA sometimes defers IFSC authority to "the banking gateway." This repo does not
contain ValidX's implementation — its state machine is out of CFA's lane.

**Can an invalid/unvalidated fund account block a payout?** Not via CFA — CFA has no
verification gate; it will happily persist and return a structurally-valid-but-unverified
fund account with `active:true`. Any block on using an unvalidated account for a payout must
be enforced by the Payouts service and/or ValidX before/around the payout call, not by CFA.

---

## Outbound Client Table

| Client | File | Purpose | Config key | Auth | Timeout/Retry |
|---|---|---|---|---|---|
| Vault | `internal/vaultservice/client.go` | Card tokenization (`GetTokenAndFingerprint`) | `config.VaultService` | Key/Secret basic-auth-style (`VaultServiceAuth`) | `RequestTimeout=20s`, `InternalRequestTimeout=5s`, `MaxRetryCount=1` |
| BIN service | `internal/binservice/client.go` | IIN/BIN metadata lookup (network, issuer, type) | `config.BinService` | Key/Secret (`BinServiceAuth`) | `MaxRetryCount=1` |
| Token service | `internal/tokenservice/client.go` | Fetch token+card by token_id (saved-card flow) | `config.TokenService` | Key/Secret (`TokenServiceAuth`) | `defaultRequestTimeout=30s`, `retryBackoff=100ms`, `MaxRetryCount=1` |
| API monolith | `internal/apiservice/client.go` | Beneficiary registration for card FAs (`/v1/fund_accounts/card/register`) | `config.ApiService` | Key/Secret (`ApiServiceAuth`) | `MaxRetryCount=1` |
| DCS (feature flags) | `internal/dcsservice/client.go` | Per-merchant feature flags (`skip_ifsc_lookup`, etc.) — reuses **Payouts'** DCS namespace | `config.Dcs` (`DCSConfig{Username,Password,Env,Mock,Timeout}`) | Username/Password (Credstash-injected, shared with Payouts) | `dcsDefaultTimeoutSeconds=60` |
| MongoDB | `pkg/storage/mongodb/mongo.go` | Primary datastore | `config.Store` | connection creds | — |
| MySQL (API DB) | Spine ORM | Legacy read fallback | `config.APIStore` | connection creds | — |
| TiDB (optional/experimental) | `internal/fund_accounts/tidb/fetcher.go` | Alt API-monolith data access | `config.Wda` | via `wda.Config` | gated by `EnableTiDB` flag |
| Elasticsearch | `pkg/elasticsearch/elasticsearch.go` | Contact search index (currently disabled) | `config.Elasticsearch` | — | — |
| SQS | `github.com/razorpay/goutils/worker/v2/queue` | Dual-write / lazy-load event transport | `config.Queue` | AWS creds (region `ap-south-1` per docs) | — |

All four business-integration clients (Vault/BIN/Token/API) follow the same `Config{BaseURL,
Auth{Key,Secret}, Mock bool}` shape and expose a `Mock` toggle for local/test runs — confirmed
directly in each client file's top-of-file struct definitions.

---

## Event Table

| Event type | Producer | Consumer(subscriber) | Queue | Delay | Notes |
|---|---|---|---|---|---|
| `contact.created` | `contacts.Service.CreateContact` | `ContactDualWriteSubscriber` | `cfa-contact-dual-write-queue` (name per doc) | 30s | async write to API MySQL by external API-monolith worker |
| `contact.updated` | `contacts.Service.UpdateContact` | `ContactDualWriteSubscriber` | same | 30s | sets `is_update_dual_write:true` |
| `contact.lazy_load` | (trigger commented out) | `ContactLazyLoadSubscriber` | `cfa-contact-lazy-load-queue` | 0s | handler/worker exist but not currently triggered by read-miss path |
| `fund_account.created` | `fund_accounts.Service.CreateFundAccount` | `FundAccountDualWriteSubscriber` | `cfa-fund-account-dual-write-queue` | 30s | |
| `fund_account.updated` | `fund_accounts.Service.UpdateFundAccount` | `FundAccountDualWriteSubscriber` | same | 30s | |
| `fund_account.lazy_load` | (trigger commented out) | `FundAccountLazyLoadSubscriber` | `cfa-fund-account-lazy-load-queue` | 0s | handler registered in `cmd/worker/main.go`, not currently fired |

Event bus itself (`internal/eventsystem/events/eventbus.go`, existence and package layout
confirmed) is **in-memory, single-pod, fire-and-forget** — not a durable outbox; durability
only begins once a subscriber successfully calls SQS `PublishMessage`. No Kafka usage found in
this repo (`rg` for kafka in internal/pkg returned nothing beyond docs referencing SQS only).
CFA currently **consumes no external events** — `EventTypeContact/FundAccountLazyLoad` are
internally-generated only.

---

## Worker / Cron / Admin Table

| Name | Type | Trigger | File | Notes |
|---|---|---|---|---|
| `contact_lazy_load` | SQS-consumed worker job | `contact.lazy_load` event (currently not fired) | `internal/job/contact_lazy_load/`, registered `cmd/worker/main.go:65` | MaxRetries=3, Timeout=120s (per doc) |
| `fund_account_lazy_load` | SQS-consumed worker job | `fund_account.lazy_load` event (currently not fired) | `internal/job/fund_account_lazy_load/`, registered `cmd/worker/main.go:66` | same |
| `BankIdentifierCorrection` | Admin gRPC/HTTP mutation endpoint | manual call by named dev user | `internal/api_controller/fund_account/server.go:478-558` | batch IFSC/bank-code correction across merchant_id+fund_account_id pairs; per-user Basic Auth for traceability |
| `ContactsDualWrite` / `FundAccountsDualWrite` | Manual backfill RPC | manual call by `API` client | `internal/api_controller/fund_account/server.go:560-608`, contact equivalent | republishes create events for given IDs |
| `cmd/migration/main.go` | CLI migration runner | manual (`go run cmd/migration/main.go up/down`) | — | applies Mongo collection/index migrations |
| `identify_duplicates` migration | one-off scan | manual migration run | `internal/database/migrations/1739361599_identify_duplicates.go` | reports (does not fix) duplicate contacts/fund_accounts/hash_lookup rows |
| No separate "cron" business logic found | — | `Cron` ACL client registered but granted **zero** methods | `internal/server/interceptors.go:41` | placeholder/unused currently |

---

## Datastore Table

| Store | Role | Collections/Tables | Key indexes | Notes |
|---|---|---|---|---|
| MongoDB | Primary | `contacts`, `fund_accounts`, `hash_lookup` | `merchant_id` (non-unique), `hash` (non-unique), `contact_id`, `reference_id`+`merchant_id`, `batch_id`, per-type sub-doc id fields | migrations: `1715709457/58/59_*`; **no unique constraint on `hash` anywhere** |
| MySQL ("API DB") | Legacy/fallback (read-mostly) | `contacts`, `fund_accounts`, `bank_accounts`, `vpas`, `cards`, `wallet_accounts` | merchant_id, source_id, unique_hash+merchant_id (per docs) | owned by API monolith, not migrated in this repo; accessed via Spine ORM |
| TiDB | Experimental alt-read path | (mirrors API DB schema, presumably) | — | gated by `EnableTiDB`; `internal/fund_accounts/tidb/fetcher.go` exists but not deep-read |
| Elasticsearch | Contact search index | contact index (name per `config.Elasticsearch.Index`) | — | indexing currently disabled; non-ES filters fall through to Mongo/MySQL query |

Sensitive data handling: card PAN and CVV are **never persisted** by CFA — only
`vault_token`/`global_fingerprint`/`last4`/`iin`/`network`/`issuer`/`expiry_month`/
`expiry_year` metadata are stored in the `card` sub-document; raw card number is cleared from
in-memory request objects immediately after the Vault tokenization call
(`internal/fund_accounts/card/service.go` tokenization path; `sanitizeCardDetails` per
`internal/fund_accounts/service.go`). Bank account numbers and IFSC are stored in plaintext in
both Mongo and MySQL (no field-level encryption/tokenization found for bank details in this
repo — consistent with them being lower-sensitivity, non-PCI data).

---

## Config / Feature-Flag Table

| Flag / config | Scope | Source | Effect |
|---|---|---|---|
| `skip_ifsc_lookup` | per-merchant | DCS (`dcsservice.FeatureSkipIFSCLookup`), reuses Payouts DCS namespace | skips static IFSC.json branch lookup on bank-account validation (create+update) — used for the ValidX/FAV composite flow |
| `use_relaxed_beneficiary_name_regex` | global (static config, not DCS) | `config.FundAccountValidation.UseRelaxedBeneficiaryNameRegex`, Credstash env var `FUNDACCOUNTVALIDATION_USE_RELAXED_BENEFICIARY_NAME_REGEX` | switches beneficiary-name regex to relaxed (no `$` anchor), matching API-monolith PHP `preg_match` partial-match behaviour |
| `enable_cfa_db` / `enable_tidb` | global | `config.FundAccountFetchMultipleConfig` | controls whether `FundAccountsFetchMultiple` queries CFA Mongo/TiDB in addition to (or instead of) MySQL API DB; both false today (docs) = MySQL-only |
| `Mock` (per-client) | per-outbound-client | `VaultServiceConfig.Mock`, `BinServiceConfig.Mock`, `TokenServiceConfig.Mock`, `ApiServiceConfig.Mock`, `DCSConfig.Mock` | swaps live client for mock implementation (local/test/devstack) |
| `Dcs.Env` | global | `DCSConfig.Env` | "dev" vs "prod" DCS environment selector |

---

## Migration / Proxy Mechanisms (API Monolith ↔ CFA)

CFA is explicitly documented (`AGENTS.md`) as being in a **"dual-write migration"** state
between the API monolith's MySQL DB and CFA's own MongoDB — this is the dominant architectural
theme of the whole repo:

1. **Writes** go to MongoDB first (source of truth going forward), then asynchronously
   dual-written to MySQL via an in-memory event → SQS (30s delay) → external API-monolith
   worker (not in this repo) pipeline. No two-phase commit; no outbox table — durability
   depends on the in-memory publish succeeding before a pod restart.
2. **Reads** (`GetContactById`, `GetFundAccountById`, `GetFundAccountsByIds`) check CFA Mongo
   first, fall back to MySQL API DB on miss ("lazy" read-through), and *would* trigger an async
   backfill into Mongo via a lazy-load event — but that backfill trigger is currently commented
   out in the service layer, so backfill is not happening automatically today; the
   `contact_lazy_load`/`fund_account_lazy_load` worker code and manual `.../dual_write`
   endpoints remain as the operational levers.
3. **`FundAccountsFetchMultiple`** (list/filter) is a carve-out that still queries **only**
   MySQL API DB by default — it has not yet been cut over, gated behind
   `EnableCFADB`/`EnableTiDB` flags that default off.
4. **No experiment/Splitz usage found** — `rg` for "splitz" across `internal/` and `pkg/`
   returned nothing; feature gating for this migration is done via CFA's own config flags and
   the DCS service (which itself borrows Payouts' DCS namespace), not Splitz.
5. **No reverse proxy / shadow-traffic mechanism found** — CFA does not proxy calls to the API
   monolith for contact/fund-account CRUD; it maintains its own MongoDB copy and only calls the
   API monolith for (a) card beneficiary registration and (b) the async dual-write path.

---

## Dependencies (selected, from go.mod)

- `go.mongodb.org/mongo-driver v1.17.3`
- `google.golang.org/grpc v1.76.0`, `grpc-ecosystem/grpc-gateway/v2 v2.27.2`
- `github.com/razorpay/goutils/spine v0.12.4` (repository/ORM abstraction over Mongo/MySQL)
- `github.com/razorpay/goutils/worker/v2 v2.3.2` (SQS worker framework)
- `github.com/razorpay/goutils/dcs v1.7.1` + `github.com/razorpay/config-proto` (DCS feature flags, shared with Payouts)
- `github.com/razorpay/goutils/telemetry` (OTel 2.0 observability)
- `github.com/razorpay/goutils/wda v1.0.8` (TiDB/API-monolith data access — experimental path)
- `github.com/aws/aws-sdk-go v1.55.8` (indirect, SQS)

CI (`.github/workflows/`) includes `ci.yaml`, `ci-devstack.yml`, `e2e.yml`,
`slit-in-process.yml` (Service-Level Integration Tests, `slit/` dir), several
Claude-Code-driven review workflows (`claude-code.yml`, `claude-security-review.yml`,
`claude-tech-spec-review.yml` — security review specifically scoped to
`internal/api_controller/**`, `internal/middleware/**`, `internal/apiservice/**`, `cmd/**`),
and `akto-ci-run.yaml` (API security scanning).

---

## Unresolved Questions

1. **ValidX's actual FAV/penny-drop state machine is not in this repo.** We can confirm ValidX
   *calls* CFA (ACL entry + comment reference) and that CFA defers IFSC authority to "the
   banking gateway" for ValidX-driven flows, but the verification workflow itself (penny-drop
   execution, retry/expiry state machine, pass/fail persistence) must be investigated in the
   ValidX repo directly — out of this lane.
2. **Where does "blocking an invalid fund account from being used in a payout" actually get
   enforced?** Not in CFA. Needs confirmation from the Payouts-service lane (this task's
   sibling investigation) on whether Payouts checks a ValidX-side verification status before
   allowing payout creation against a fund account.
3. Exact current values of `EnableCFADB`/`EnableTiDB`/`skip_ifsc_lookup` in production
   (prod.toml / DCS live config) were not read — `config/prod.toml` exists but its contents
   were not inspected in this pass; flag *defaults* in Go structs are `false`.
4. `internal/fund_accounts/tidb/fetcher.go` internals were not read in detail — only its
   existence and config wiring were confirmed.
5. Spinnaker pipeline / k8s manifest details (replica counts, resource limits, canary
   percentages) live outside this repo (`razorpay/spinacode/v3/cfa/`) and were not accessible
   for verification — reported per `AGENTS.md` claim only.
6. CODEOWNERS file was not found — ownership/review-routing for this repo could not be
   determined from the repo itself.
