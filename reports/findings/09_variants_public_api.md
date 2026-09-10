# Payout Product Variants (payout-links, vendor-payments, vendor-experience) + Adjacent Validation/Pricing Services + Public API Contract

Repos (commits as pinned in task):
- `razorpay/payout-links` @ `2c81a4d97f961794fa8a95ec8580311203460bdf`
- `razorpay/vendor-payments` @ `20c4f4d59970471067388afea8b1d65ac39ee126`
- `razorpay/vendor-experience` @ `df90df21bee54ce0148df43d3fc65e7ba7b60160`
- `razorpay/charge-collections` @ `412816c70d569abd9a8220a4c320a994ceec2883` (`charge-collections-sdk` not actually present in the clone set — directory listed but empty/missing, skipped)
- `razorpay/ValidX` @ `40f181b6d874abece2ec5cdd88d6fd9e4bb533b3`
- `razorpay/razorpay-mcp-server` (public) @ `7950d51d118ca164c32b7cf0cfaa14f34f24849f`
- `razorpay/markdown-docs` (public) @ `ab3bda5f11c308034b2089df532df1064db1df7c`

All local paths below are relative to
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/<repo>/`.

---

## 1. Per-repo summary and relevance classification

| Repo | What it is | Relevance to Payouts |
|---|---|---|
| payout-links | RazorpayX/Apps-owned Go/Twirp microservice; merchants create a hosted "payout link" a contact fills in themselves; on submission it creates a real payout via the internal payouts API | **Core payout product variant** — a payout-creation front-end |
| vendor-payments | "Source to pay" system: invoice mgmt, PO mgmt, vendor payouts/settlements, TDS. Go microservice, owned by `@rxapps-vendorpayouts` | **Core payout product variant** — creates payouts as vendor bill settlement, with invoice-approval workflow + TDS deduction on top |
| vendor-experience | Vendor portal/onboarding microservice (vendor self-serve app for invoices, KYC/BVS docs) | **Upstream/adjacent** — does NOT call the payouts API directly; calls **vendor-payments** (`CreateInvoice`, `GetInvoiceByIDs`, OCR status) which in turn triggers payouts |
| charge-collections | "Platform Pricing" — generic fee-collection/pricing engine (MDR, Buy pricing, Metered pricing) for all RZP products | **Adjacent but payout-creating**: it itself calls `POST /v1/internalContactPayout` to **debit its own fee** from a merchant's X balance (self-referential fee-collection payout), and separately **consumes a Kafka/CDC topic (`payout-kafka-topic`, Maxwell outbox pattern)** for metered-usage ingestion |
| charge-collections-sdk | Not present in the clone (directory exists but is empty — likely a client SDK package, not separately investigatable here) | Unclassified — no code cloned |
| ValidX | **Fund Account Validation (FAV) service** — "BU: RazorpayX Payouts" per its own tech-spec header. Validates bank account/VPA/mobile-number-derived-VPA via Penny Drop/Paisa Drop/Penniless/RPD across FTS, Citi/Mozart, Slice, API gateways | **In-scope core payouts control** — implements the public `/v1/fund_accounts/validations` (FAV) contract; explicitly gates payouts (NPCI OC-215 compliance: mapper lookup must pair with real money movement) |
| razorpay-mcp-server | Public MCP server exposing Razorpay API as LLM tools, via the official `razorpay-go` SDK | **Thin public-API consumer** — only 2 read-only payout tools (fetch by id, fetch all); no create/cancel/approve tool exists |
| markdown-docs | Public API docs source (raw markdown, mirrors razorpay.com/docs) | Source of the public contract below |

---

## 2. Findings

### payout-links

| Claim | File + symbol | Evidence | Confidence | Capability |
|---|---|---|---|---|
| Creates payouts by calling an **internal** payouts route on the API host, not the public `/v1/payouts` path | `internal/payout/core.go:44` `Core.CreatePayoutApi`; `internal/payout/config.go` (`Config.CreatePayout` route) | `modifiedCreatePayoutRoute := common.SetRouteKeyForRequestToApiByMode(...); common.CallInternalAPI(ctx, payoutConfig.Base, modifiedCreatePayoutRoute, body, &response, MerchantId, "", extraHeaders)` | confirmed | authorize/persist |
| Route path resolved is `payouts_internal` (POST), base host `https://api.razorpay.com/v1/` in prod | `config/prod.toml:227-233` (`[PayoutConfig.Base] hostname="https://api.razorpay.com/v1/"`); `config/routes/routesstage.toml:3-8` (`[PayoutConfig.CreatePayout] routePath="payouts_internal"`) | see excerpt above | confirmed | route |
| Idempotency via `X-Payout-Idempotency` header, generated per-call by the caller | `internal/payout/core.go:23-24` (`const XPayoutIkey = "X-Payout-Idempotency"`); used at `core.go:47` | `extraHeaders := map[string]string{XPayoutIkey: iKey}` | confirmed | authorize |
| Auth to the internal payouts route is **HTTP Basic Auth** with a per-route key/secret pair (`routeKey`/`routeSecret` from TOML, e.g. `rzp_live` / `RANDOM_PAYOUT_LINKS_INTERNAL_PASSWORD`), plus `X-Razorpay-Account` header carrying the merchant id | `internal/common/httpservice.go:28-70` (`CallInternalAPI`) | `headers := {ContentType, XRxAccount: MerchantId}; httpReq.SetBasicAuthorization(ctx, key, Route.RouteSecret)` | confirmed | authorize |
| Also calls internal routes for contacts (`contacts_internal`), fund accounts (`fund_accounts_internal`), merchants (`merchants_internal`), banking accounts (`banking_accounts_internal`), purpose validation (`payouts/purpose/validate`) — all same basic-auth/host pattern | `config/routes/routesstage.toml:1-77` | see file | confirmed | route |
| Payout status is **received** via an inbound Twirp RPC (`UpdatePayoutLinkStatus`) exposed by payout-links itself and invoked by an external caller (api monolith/webhook-forwarder) — no Kafka consumer for payout status found in this repo | `rpc/payout-links/service.proto:22`; `internal/apiservice/server.go:364` (`Server.UpdatePayoutLinkStatus`); `internal/payoutlinks/core.go:1360` (`UpdatePayoutLinkStatus`) | RPC signature `rpc updatePayoutLinkStatus (updatepayoutlinkstatusrequest) returns (payoutlinkresponse);` | confirmed (mechanism = inbound RPC callback, not Kafka — searched, no `kafka` imports in repo) | observe |
| Internal payout status → payout-link status mapping table | `internal/payoutlinks/status.go:52-61` (`PAYOUT_TO_PAYOUT_LINK_STATUSES`) | `"FAILED":ATTEMPTED, "REVERSED":ATTEMPTED, "REJECTED":ATTEMPTED, "CREATED":PROCESSING, "INITIATED":PROCESSING, "PROCESSING":PROCESSING, "QUEUED":PROCESSING, "PENDING":PROCESSING, "PROCESSED":PROCESSED, "CANCELLED":ATTEMPTED` | confirmed | transform |
| payout-link's own merchant-visible states + own webhook event names (distinct from `payout.*` events) | `internal/payoutlinks/status.go:11-49` | States: `pending, rejected, issued, processing, attempted, processed, cancelled, expired`; events: `payout_link.pending/rejected/issued/processing/attempted/processed/cancelled/expired` | confirmed | observe |
| Approval-workflow interplay: dedicated workflow-service callback RPCs | `rpc/payout-links/service.proto:44-53`; `internal/apiservice/server.go:913-1006` (`WorkflowStateCallback`, `ApprovedWorkflowCallback`, `RejectedWorkflowCallback`, `ApproveAction`, `RejectAction`, `ApproveBulkPayoutLinks`, `RejectBulkPayoutLinks`) | function list | confirmed | authorize/route |
| Outbound webhook/SMS delivery goes through `pkg/stork` (Stork = Razorpay's shared webhook+SMS delivery service) | `pkg/stork/impl.go`, `internal/payoutlinks/core.go:2582-2730` (`GetPLWebhookPayload`, `ProcessEvent`), `:3873-3885` (`SendSms`) | `stork.Stork.StorkServiceInterface.ProcessEvent(ctx, eventName, payoutLink.MerchantId, payload)` | confirmed | observe |
| Attachment updates on payouts flow the other direction: payout-links → payouts service, via `BulkUpdateAttachments` route (`payouts_internal/attachments`, PATCH) | `internal/payout/apicaller.go` (`ApiImpl.UpdateAttachments`); `config/routes/routesstage.toml:24-28` | see file | confirmed | persist |
| Datastore/queue: MySQL (goose migrations), Redis (`pkg/cache`, distributed lock `pkg/distlock`), SQS+Redis-backed queue (`pkg/queue/sqs.go`, `pkg/queue/redis.go`) — **no Kafka client anywhere in repo** | `internal/migrations/`, `pkg/queue/{sqs,redis}.go` | dir listing | confirmed | persist |
| CODEOWNERS: `kumar.ayush@`, `amjad.ali@`, `vivek.s@razorpay.com` for all of cmd/internal/pkg/rpc | `.github/CODEOWNERS` | file content | confirmed | — |

### vendor-payments

| Claim | File + symbol | Evidence | Confidence | Capability |
|---|---|---|---|---|
| Creates payouts through an internal RX client hitting `x.razorpay.com` (not `api.razorpay.com`) on internal payout paths | `internal/rxclient/config.go`; `config/prod.toml:14` (`rxBaseUrl = "https://x.razorpay.com/"`); `internal/payout/core.go:83-113` (`CreatePayoutPath = "v1/payouts_internal/"`) | see excerpts | confirmed | authorize/persist |
| Three payout creation paths: normal contact payout, "internal contact" payout, and a 2FA-gated payout | `internal/payout/core.go:82-85` | `CreatePayoutPath="v1/payouts_internal/"`, `CreatePayoutOnInternalContact="v1/internalContactPayout/"`, `Create2FaPayoutPath="v1/payouts/2fa/create_internal"` | confirmed | authorize |
| Cancel payout path | `internal/payout/core.go:84` | `CancelPayoutPath = "v1/payouts_internal/%s/cancel"` | confirmed | reverse |
| Idempotency header identical to payout-links (`X-Payout-Idempotency`), plus `X-Dashboard-User-Id` carrying the acting dashboard user | `internal/payout/core.go:110-116` (`CreatePayout`) | `extraHeaders := {"X-Dashboard-User-Id": UserId, "X-Payout-Idempotency": iKey}` | confirmed | authorize |
| Auth = HTTP Basic Auth, key selected by live/test mode (`rzp_live`/`rzp_test`), secret from config; merchant scoping via `X-Razorpay-Account` | `internal/rxclient/core.go:28-30` (`modeToAPIKey`), `:171-174` (`setAuthParams`: `httpRequest.SetBasicAuth(modeToAPIKey[mode], ApiConfig.Secret)`) | see excerpt | confirmed | authorize |
| Full internal payout status list understood by vendor-payments | `internal/payout/core.go:15-42` (`statusList`) | `failed, reversed, cancelled, rejected, created, initiated, processing, queued, pending, processed, scheduled, on_hold` | confirmed | observe |
| Payout status → payment (vendor-payment line-item) status mapping | `internal/payments/payouthandler.go:16-27` | `payoutToPaymentStatus` map (Processing/Failed/Processing/Failed/Failed/Failed/Processing/Processing/Processing/Paid/Scheduled/Processing) | confirmed | transform |
| **Status-receipt mechanism = inbound Twirp/gRPC RPC `PayoutStatusChange`**, exposed by vendor-payments and invoked externally (api monolith / webhook forwarder), which then calls internal `payments.PayoutStatusUpdater` and branches to tax-payment vs vendor-payment handling | `internal/apiservice/VpServer.go:1178-1206` (`VPServer.PayoutStatusChange`); `internal/payments/core.go:74,93` (`PayoutStatusUpdater`) | `payment, err := payments.GetCore().PayoutStatusUpdater(ctx, request.PayoutId, request.PayoutStatus, request.SourceId, request.SourceType)` then `taxpayments.GetCore().PayoutStatusUpdated(...)` | confirmed | observe/transform |
| Internal Kafka topics found (`vendor_payment_status_updated`, `VendorPaymentCreated`, `VendorPaymentUpdated`) are **self-published fan-out**, not the channel by which payout status arrives from outside — publisher is `internal/vendorpayments/syncer.go:72` (`publishVendorPaymentStatusUpdate`), consumed by `internal/tasks/vpstatusupdate.go` and `internal/vendorportal/core.go` | `internal/pubsub/core.go:29,39,45`; `internal/vendorpayments/syncer.go:72-84` | topic const `topicVendorPaymentStatusUpdated = "vendor_payment_status_updated"` | confirmed | route |
| Approval workflow: dedicated `internal/workflows` + `internal/workflows/apiclient` package calling an **external workflow service** (`CreateWorkflow`, `CreateConfig`, `ActionOnEntityByEmail`, `RejectWorkflowById`) keyed by config type e.g. `"purchase-order-approval"` | `internal/workflows/apiclient/core.go:14-20`; test fixture `internal/workflows/apiclient/core_test.go:33-37` | `Type: "purchase-order-approval"` in `ConfigReq` | confirmed | authorize |
| Also separate inbound callback RPCs for workflow state changes (distinct from payout status) | `internal/callbacks/server.go:26-49` (`WorkflowStateCallback`, `WorkflowCallback`) | see file | confirmed | authorize |
| TDS handling is a first-class module (`internal/initiatetds`, `internal/taxpayments`, `internal/directtaxpayments`), separate payout path for tax payments | dir listing `internal/initiatetds`, `internal/taxpayments`, `internal/directtaxpayments` | — | probable (not read line-by-line, but package structure + `taxpayments.GetCore().PayoutStatusUpdated` call from `VpServer.go` confirms tax payments ride the same payout status pipe) | authorize |
| Workers/crons: `cmd/accounting-payouts-cron`, `cmd/ap-worker`, `cmd/ocr-worker`, `cmd/worker`, `cmd/workerv2` — separate process types for cron vs Kafka-driven workers | `cmd/` listing; `internal/boot/boot_ap_cron.go` | `ApCron` boot component | confirmed | route |
| Datastore: MySQL + Redis; queue via `commonkafka`/`pubsub` abstraction (Kafka), `internal/batch` for bulk/CSV imports | dir listing | — | probable | persist |
| Feature flags: none of the common patterns (`featureflag`, `growthbook`, `splitio`) found by grep in `internal` | grep result (no hits) | — | probable (absence of evidence, not conclusive — may be under a different name) | — |
| CODEOWNERS is a wildcard team, not named individuals | `.github/CODEOWNERS` → `* @razorpay/mandatoryxreviewers` | file content | confirmed | — |

### vendor-experience

| Claim | File + symbol | Evidence | Confidence | Capability |
|---|---|---|---|---|
| **Does not call the payouts API at all.** Grep for `Payout`/`payout` across `internal/` and `pkg/` (excluding tests/mocks) returns no hits except an unrelated stub | grep result | (no output) | confirmed | — |
| Talks to **vendor-payments** as its downstream, via `pkg/vendor_payments` HTTP client, for invoice creation/fetch and OCR status — vendor-payments is what actually creates the payout | `pkg/vendor_payments/vendor_payments.go:17-20,33-54` | `CreateInvoice`, `GetInvoiceByIDs`, `GetOCRsByReferenceIDs` interface + impl calling `s.handlePostRequest`/`handleGetRequest` | confirmed | route |
| Vendor identity/document verification via **BVS** ("Bank/Business Validation Service"?), a Twirp API distinct from ValidX | `pkg/bvssdk/constants.go:5-7` | `BvsServiceName="bvs"`; `CreateValidationEndpoint = "twirp/platform.bvs.validation.v1.ValidationAPI/CreateValidation"` | confirmed | authorize |
| Auth stack includes `authz` (authorization/RBAC), `passportv4` (JWT identity), suggesting user-session-based dashboard/portal auth rather than merchant API-key basic auth used by payout-links/vendor-payments | `pkg/authz/authz.go`, `pkg/passportv4/passport.go` | dir/file presence | probable | authorize |
| Uses Uber Cadence (workflow orchestration engine) for long-running onboarding workflows, separate from the "workflow service" used for payout approvals elsewhere | `pkg/cadence`, `cmd/cadence/workflow-worker`, `cmd/cadence/activity-worker` | dir listing | probable | route |
| Uses DCS (`pkg/dcs`, `pkg/dcs/features`) for dynamic config/feature flags | dir listing | — | probable | — |
| CODEOWNERS: single team `@razorpay/vendor-onboarding-devs` | `.github/CODEOWNERS` | file content | confirmed | — |

### charge-collections (and ValidX cross-reference)

| Claim | File + symbol | Evidence | Confidence | Capability |
|---|---|---|---|---|
| "Platform Pricing" — generic fee-collection engine for MDR/Buy/Metered pricing across all RZP products, including Payouts | `README.md:11-13`; `AGENTS.md:5-11` | "This service in intended to handle the pricing for all products across Razorpay"; "Metered Pricing: Usage-based pricing for Razorpay products (Affordability, **Payouts**, Reports, etc.)" | confirmed | — |
| It **creates its own payout** to collect a computed charge/fee from the merchant's X balance — calls `POST {api_host}/v1/internalContactPayout` (same internal path used by vendor-payments' `CreatePayoutOnInternalContact`) with `Mode: IMPS`, `Purpose: PayoutPurposeCC`, `QueueIfLowBalance: true` | `internal/charges/payouts_core.go:20-75` (`Core.createPayout`); `internal/provider/api/client.go:36` (`createPayoutsEndpoint = "/v1/internalContactPayout"`), `:508-522` (`ServiceClient.CreatePayout`) | ```go\ncreatePayoutRequest := api.CreatePayout{\n  AccountNumber: merchantAccountNumber, FundAccountID: fundAccountID,\n  Amount: int64(amount), Currency: charge.Currency, Mode: common.IMPS,\n  Purpose: common.PayoutPurposeCC, QueueIfLowBalance: true, ...}\n``` | confirmed | authorize/persist |
| Auth to that call = HTTP Basic Auth (`username="payouts_key"`) + `X-Razorpay-Account` header, same pattern as all other clients | `config/default.toml:60-61` (`[auth.payouts] username="payouts_key"`); `internal/provider/api/client.go:514-518` | `jsonRequest.SetBasicAuth(...); jsonRequest.SetHeaders({xRazorpayAccountHeader: mid})` | confirmed | authorize |
| Consumes a Kafka topic named `payout-kafka-topic` via a CDC/Maxwell-outbox-shaped ingestion job (`IngestionJob`), for metered-usage billing ingestion — a genuinely different status-receipt mechanism (Kafka, not RPC callback) vs. payout-links/vendor-payments | `config/dev.toml:117-134` (`[queue.kafka] topic="payout-kafka-topic"`; `[jobs.ingestionJob] queueName="payout-kafka-topic"`); `internal/job/ingestion_job.go:16-42` (`IngestionJob`, `CDCOutboxMaxwellEntry`/`CDCOutboxEntry` structs) | see excerpts | confirmed (topic exists and is wired to a job); the exact upstream producer (api monolith vs. Payouts Service) not confirmed from this repo alone | observe |
| `internal/charges/**` docs itself as bridging "X→Payout, PG→Ledger" namespaces | `AGENTS.md:142` | `| internal/charges/** | charges-domain.md | Namespace branching (X→Payout, PG→Ledger), INR only |` | confirmed | route |
| `charge-collections-sdk` — not present in clone set, cannot classify from code; name suggests a Go/other-language client SDK library for calling charge-collections (not itself payout-related) | dir listing (empty) | — | unresolved | — |
| ValidX self-identifies as **"BU: RazorpayX Payouts"** in its own tech-spec doc header, and its AGENTS.md architecture diagram explicitly lists **Charge Collections** (fee calc), **CFA** (fund account mgmt), **Ledger** (journal entries), **Banking Accounts**, **Stork** (webhooks), **X-Balances**, **ASV**, **DCS**, **Splitz** as its internal-service dependencies | `tech-spec.md:6` (`**Team/Pod:** ValidX | **BU:** RazorpayX Payouts`); `AGENTS.md:72-84` | see architecture diagram block | confirmed | — |
| ValidX implements the public FAV endpoint `POST /v1/fund_accounts/validations` (multiple `account_type`s: `bank_account`, `vpa`, and a new `mobile_number` type being added per the tech-spec) — mandatory NPCI-compliance rule that a VPA mapper lookup must always pair with a real money movement (paisa drop) | `README.md:5` ("Fund account validation service ... Penny Drop, Paisa Drop, Penniless, RPD"); `tech-spec.md:68-78` | "NPCI compliance is non-negotiable: every mapper lookup must be paired with real money movement (paisa drop); VPA must never be exposed in any response or webhook" | confirmed | authorize |
| ValidX stack: gRPC:8080 + HTTP gateway:8081 + metrics:8082; MySQL primary, Redis cache/locks, AWS SQS (9+ queues) for async job processing (initiate/inquiry/webhook/timeout jobs); server pod, worker pod, migration job (goose) as separate `cmd/` binaries | `AGENTS.md:25-105` | architecture diagram + "Key Components" list | confirmed | persist/route |
| ValidX explicitly separate from the **Payouts Service** — its own tech-spec describes "Payouts Service adds a freshness check to gate payouts against recently validated fund accounts", i.e. Payouts Service (not ValidX) is the payout-money-movement authority; ValidX is upstream validation only | `tech-spec.md:79` | "Payouts Service adds a freshness check to gate payouts against recently validated fund accounts." | confirmed | authorize |

### razorpay-mcp-server

| Claim | File + symbol | Evidence | Confidence | Capability |
|---|---|---|---|---|
| Wraps the official `razorpay-go` SDK (`rzpsdk "github.com/razorpay/razorpay-go"`), which itself calls the **public** `api.razorpay.com` REST API with HTTP Basic Auth using `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET` | `pkg/razorpay/payouts.go:1-9`; `cmd/razorpay-mcp-server/main.go:52-53`; `cmd/razorpay-mcp-server/stdio.go:46` (`rzpsdk.NewClient(key, secret)`) | see excerpts | confirmed | route |
| Only **two** payout-related MCP tools registered, both **read-only**: `fetch_payout_with_id` (`client.Payout.Fetch`) and `fetch_all_payouts` (`client.Payout.All`, params `account_number`, `count`, `skip`) — **no create/cancel/approve/reject tool exists in this repo** | `pkg/razorpay/payouts.go:12-59` (`FetchPayout`), `:63-121` (`FetchAllPayouts`) | `mcpgo.NewTool("fetch_payout_with_id", ...)`, `mcpgo.NewTool("fetch_all_payouts", ...)` (name for the second inferred from tool file — grep confirmed only these two `NewTool(` calls in `payouts.go`) | confirmed | observe |
| `settlements.go` also references payouts (instant settlement fetch-with-id-payout doc correlation) but is a distinct settlements domain, not further explored (out of lane depth budget) | `pkg/razorpay/settlements.go` | grep hit | unresolved (not read) | — |

---

## 3. Outbound payout-creation client table

| Repo | Client file | Base host (prod) | Path | Method | Auth | Idempotency header | Extra headers |
|---|---|---|---|---|---|---|---|
| payout-links | `internal/payout/core.go` (`CreatePayoutApi`) | `https://api.razorpay.com/v1/` | `payouts_internal` | POST | Basic (routeKey/routeSecret e.g. `rzp_live`) | `X-Payout-Idempotency` | `X-Razorpay-Account` |
| payout-links | `internal/payout/core.go` (`CancelPayout` — n/a, not found as separate route in this repo; cancellation not implemented here) | — | — | — | — | — | — |
| vendor-payments | `internal/payout/core.go` (`CreatePayout`) | `https://x.razorpay.com/` | `v1/payouts_internal/` | POST | Basic (`rzp_live`/`rzp_test` + secret) | `X-Payout-Idempotency` | `X-Dashboard-User-Id`, `X-Razorpay-Account` |
| vendor-payments | `internal/payout/core.go` (`CreatePayoutOnInternalContact`) | `https://x.razorpay.com/` | `v1/internalContactPayout/` | POST | Basic | `X-Payout-Idempotency` | `X-Dashboard-User-Id`, `X-Razorpay-Account` |
| vendor-payments | `internal/payout/core.go` (`CancelPayout`) | `https://x.razorpay.com/` | `v1/payouts_internal/%s/cancel` | POST | Basic | — | `X-Razorpay-Account` |
| vendor-payments | `internal/payout/core.go` (2FA flow) | `https://x.razorpay.com/` | `v1/payouts/2fa/create_internal` | POST | Basic | `X-Payout-Idempotency` | `X-Dashboard-User-Id`, `X-Razorpay-Account` |
| charge-collections | `internal/provider/api/client.go` (`CreatePayout`) | config-driven `apiClientConfig.Host` | `/v1/internalContactPayout` | POST | Basic (`payouts_key`) | none observed | `X-Razorpay-Account` |
| vendor-experience | — (no payout client) | n/a | n/a | n/a | n/a | n/a | n/a |
| razorpay-mcp-server | `razorpay-go` SDK (external dep, not in clone) | `api.razorpay.com` (SDK default) | `/v1/payouts*` | GET only (fetch/list) | Basic (`RAZORPAY_KEY_ID`/`SECRET`) | n/a (read-only) | n/a |

Note: `payouts_internal`, `v1/internalContactPayout`, and `v1/payouts/2fa/create_internal` are **internal, non-public** route names distinct from the public `POST /v1/payouts` documented in markdown-docs — i.e. these clients bypass the public contract and hit an internal-auth surface directly (basic auth with an internal shared secret, not a merchant's own API key).

---

## 4. Status-receipt mechanism table

| Repo | Mechanism | Entry point | Trigger source (inferred, not in clone set) |
|---|---|---|---|
| payout-links | Inbound Twirp RPC | `internal/apiservice/server.go:364` `Server.UpdatePayoutLinkStatus` | api monolith / a webhook-forwarder service calling this RPC synchronously |
| vendor-payments | Inbound Twirp/gRPC RPC | `internal/apiservice/VpServer.go:1178` `VPServer.PayoutStatusChange` | api monolith / webhook-forwarder, same pattern as payout-links |
| vendor-payments (internal fan-out only) | Kafka (self-published) | `internal/vendorpayments/syncer.go:72`, consumed by `internal/tasks/vpstatusupdate.go`, `internal/vendorportal/core.go` | vendor-payments itself, after its own `PayoutStatusChange` RPC handler runs — not an external feed |
| charge-collections | Kafka consumer, CDC/Maxwell outbox shape | `config/dev.toml` topic `payout-kafka-topic`; `internal/job/ingestion_job.go` | Likely a Maxwell/Debezium-style binlog-CDC stream off the payouts DB, or a dedicated payout-events Kafka topic — producer not in clone set |
| vendor-experience | none (no payout awareness) | — | — |
| razorpay-mcp-server | Polling only (`fetch_payout_with_id`/`fetch_all_payouts` on demand) | `pkg/razorpay/payouts.go` | caller-initiated, not push |

---

## 5. Public payouts API contract (from markdown-docs)

All under `api.razorpay.com`, HTTP Basic Auth (`key_id:key_secret`).

| Endpoint | Method | Doc path | Key request fields | Key response fields |
|---|---|---|---|---|
| `/v1/payouts` | POST | `api/x/payouts/create/bank-account.md`, `api/x/payouts/create/vpa.md` | `account_number`, `fund_account_id`, `amount`, `currency`, `mode` (`NEFT`\|`RTGS`\|`IMPS`\|`card`, uppercase), `purpose`, `queue_if_low_balance` (bool), `reference_id` (≤40 chars), `narration` (≤30 chars), `notes`. Header: `X-Payout-Idempotency` (required per docs warning) | `id` (`pout_...`), `entity`, `fund_account_id`, `amount`, `fees`, `tax`, `status`, `utr`, `mode`, `purpose`, `reference_id`, `narration`, `batch_id`, `status_details{description,source,reason}`, `created_at` |
| `/v1/payouts/{id}` | GET | `api/x/payouts/fetch-with-id.md` | — | full payout entity |
| `/v1/payouts` | GET | `api/x/payouts/fetch-all.md` | query filters (account_number, etc.) | collection of payout entities |
| `/v1/payouts/{id}/cancel` | POST | `api/x/payouts/cancel.md` | none (only queued payouts cancellable) | payout entity with `status` after cancel (may go to `failed`) |
| `/v1/payouts/{id}/approve` | POST | `api/x/payouts-approval.md` | — (requires OAuth Technology Partner integration; feature must be enabled) | payout entity |
| `/v1/payouts/{id}/reject` | POST | `api/x/payouts-approval.md` | — | payout entity |
| `/v1/contacts` | POST | `api/x/account-validation/create-contact.md` | contact details | contact entity |
| `/v1/fund_accounts` | POST | `api/x/account-validation/bank-account/create-fund-account.md`, `.../vpa/create-fund-account.md` | `account_type` (`bank_account`\|`vpa`), contact_id, bank/vpa details | fund_account entity |
| `/v1/fund_accounts/validations` | POST | `api/x/account-validation.md`, `api/x/account-validation/bank-account.md`, `.../vpa.md`, `.../reverse-penny-drop.md` | `fund_account.id` or full fund-account object, `amount` (for penny/paisa drop) | validation entity with status |
| `/v1/fund_accounts/validations` | GET | `api/x/account-validation/fetch-all-transactions.md` | `account_number` query param | collection |
| `/v1/fund_accounts/validations/{id}` | GET | `api/x/account-validation/fetch-transactions-with-id.md` | — | validation entity |
| `/v1/payout-links` | POST | `api/x/payout-links/create/use-contact-id.md`, `.../use-contact-details.md` | contact_id or contact details, amount, currency, purpose, description, send_sms/send_email, expire_by | payout_link entity |
| `/v1/payout-links/{id}` | GET | `api/x/payout-links/fetch-with-id.md` | — | payout_link entity |
| `/v1/payout-links` | GET | `api/x/payout-links/fetch-all.md` | filters | collection |
| `/v1/payout-links/{id}/cancel` | POST | `api/x/payout-links/cancel.md` | — | payout_link entity |
| (Bulk payouts) | — | `x/bulk-payouts.md` | **Dashboard-only feature (CSV/XLSX upload), no public bulk-create API endpoint documented** | — |
| Payouts-idempotency semantics | — | `api/x/payout-idempotency.md`, `errors/x.md:180-210` | Same `X-Payout-Idempotency` + identical body required for safe retry within 7 calendar days; different body ⇒ `BAD_REQUEST` | — |

Fund Account Validation is explicitly gated: **"not available in test mode and is possible only for RazorpayX Lite"** (`api/x/account-validation.md`).

---

## 6. Status list (public, from `x/payouts/states-life-cycle.md`)

`pending → {queued | scheduled | processing | rejected}`, `queued → {processing | cancelled}`, plus terminal states `processed`, `reversed`, `failed`. Full enumerated list:
`pending`, `queued`, `scheduled`, `processing`, `processed`, `reversed`, `cancelled`, `rejected`, `failed`.

(`pending`/`rejected` only exist if Approval Workflow is enabled on the merchant account. Payouts stuck in `pending` >3 months auto-`rejected`; stuck in `queued` >3 months auto-`failed`.)

---

## 7. Webhook event table (from `webhooks/payouts.md`)

| Event | Fired when |
|---|---|
| `payout.pending` | Payout enters approval-required `pending` state |
| `payout.rejected` | Approver rejects the payout |
| `payout.queued` | Insufficient funds or beneficiary/NPCI/partner-bank down (RazorpayX Lite only) |
| `payout.initiated` | Moves to `processing` (from creation or from `queued` once funded) |
| `payout.processed` | Contact's bank confirms credit |
| `payout.updated` | Any entity field changes (e.g. UTR received) — NEFT within 90s, IMPS/UPI near-immediate |
| `payout.reversed` | Payout failed and funds returned to business account |
| `payout.failed` | Terminal failure (beneficiary/NPCI/partner bank down beyond SLA) — **docs say subscribing to this is mandatory** |
| `payout.downtime.started` | Beneficiary-bank-down downtime window begins (not supported for UPI) |
| `payout.downtime.resolved` | Downtime window resolved |

Related: `webhooks/payout-links.md` (payout_link.* events, matches internal `STATUS_TO_WEBHOOK_EVENT` map found in payout-links code), `webhooks/payouts-approval.md`, `webhooks/setup-edit-payouts.md`.

---

## 8. Error / status-details reason codes (from `errors/x/payout-status-details.md`, `errors/x.md`)

Top-level error type: `BAD_REQUEST_ERROR`, `BAD_REQUEST_AUTHENTICATION_ERROR`, plus generic 5xx with idempotent-retry guidance.

`status_details.source` enum: `gateway` (partner bank tech error), `beneficiary_bank`, `business` (merchant action required), `internal` (Razorpay server).

Reversed/failed reasons: `bank_account_closed`, `bank_account_frozen`, `bank_account_invalid`, `beneficiary_account_dormant`, `beneficiary_bank_failure`, `beneficiary_bank_offline`, `beneficiary_bank_rejected`, `beneficiary_bank_technical_error`, `beneficiary_psp_offline`, `imps_not_allowed`, `invalid_ifsc_code`, `npci_beneficiary_timeout`, `transaction_limit_exceeded`, `amount_limit_exhausted_neft`, `beneficiary_account_invalid`, `beneficiary_vpa_invalid`, `insufficient_funds`, `invalid_beneficiary`, `gateway_down`, `gateway_technical_error`, `gateway_timeout`, `server_error`, `server_error_temporary`.

Processing reasons: `beneficiary_bank_confirmation_pending`, `bank_window_closed`, `payout_bank_processing`, `amount_limit_exhausted`, `partner_bank_pending`.

Processed reason: `payout_processed`. Pending reason: `pending_approval`. Queued reasons: `gateway_degraded`, `beneficiary_bank_down`, `low_balance`, `syncing_balance`, `fee_recovery_pending`.

Approval-specific 400 error: reason `payout_approval_not_allowed` — "Payout is not in pending state and cannot be approved or rejected."

---

## 9. References to services NOT in the clone set (inferred from imports/config/docs, not independently verified)

- **api monolith / "Payouts Service"** — the actual money-movement authority behind `api.razorpay.com`/`x.razorpay.com`'s `/v1/payouts*` and `/v1/payouts_internal*`/`/v1/internalContactPayout` routes. (A separate `razorpay/payouts` repo clone exists per sibling finding `01_payouts_core.md` in this same investigation, with a "shadow gateway" proxy/cutover layer between the API monolith and this Payouts Service — see that file for detail; not re-verified here.)
- **Stork** — shared webhook/SMS delivery service, used directly by payout-links (`pkg/stork`) and listed as a ValidX dependency.
- **Workflow service** — external approval-workflow engine called by vendor-payments (`internal/workflows/apiclient`) and payout-links (workflow callback RPCs); config type strings like `"purchase-order-approval"` suggest a generic multi-tenant workflow product, not payout-specific.
- **BVS** (`platform.bvs.validation.v1.ValidationAPI`) — a Twirp validation API used by vendor-experience for vendor KYC/document validation; name overlaps conceptually with ValidX (fund account validation) but is a **different Twirp package/namespace** (`platform.bvs.validation.v1` vs ValidX's own gRPC service) — likely a distinct, broader "Business Validation Service" not specific to fund accounts. **Not confirmed whether BVS and ValidX are the same service under different names or genuinely separate** (see unresolved questions).
- **CFA** (fund account management), **Ledger** (journal entries), **Banking Accounts**, **ASV** (merchant metadata), **DCS** (dynamic config service, used by ValidX, vendor-experience, charge-collections), **Splitz** (A/B testing/experiments, used by ValidX and the sibling `payouts` repo's shadow gateway), **X-Balances** (balance lookup) — all named as ValidX dependencies in its architecture diagram; none cloned here.
- **FTS**, **Citi/Mozart**, **Slice** — external/internal banking gateways ValidX routes validation calls to.
- **Master-onboarding, notifications** — not referenced by name in any of the 7 repos investigated in this lane; no evidence found either way.

---

## 10. Unresolved questions

1. Is `BVS` (`platform.bvs.validation.v1`, used by vendor-experience) the same underlying service as `ValidX`, an older/renamed predecessor, or a genuinely separate "business validation" service (e.g. GSTIN/PAN/company-KYC checks vs. bank-account/VPA checks)? Not resolved from code — the Twirp package names differ and no shared repo/README cross-reference was found.
2. Who is the actual **producer** of charge-collections' `payout-kafka-topic`? The CDC/Maxwell-outbox shape suggests a binlog-capture off a payouts-domain MySQL table, but the producing service (api monolith payouts module vs. the separate `razorpay/payouts` "Payouts Service") is not identifiable from charge-collections' code alone.
3. payout-links has no visible payout-cancel client method (only vendor-payments and charge-collections' target endpoint expose cancel) — confirm whether payout-links genuinely never cancels underlying payouts (only cancels its own payout-link before a payout is created) or whether this was simply not found by grep.
4. `charge-collections-sdk` was not present in the clone set at all (empty directory) — its relationship to charge-collections/payouts is entirely unclassified.
5. The public docs describe Bulk Payouts as Dashboard-only (CSV/XLSX); could not confirm whether an undocumented/partner-only Bulk Payouts REST endpoint exists, since none of the 7 repos in this lane call one directly (payout-links has `createBatchPayoutLinks` for **payout links**, not raw payouts, per its proto).
6. Scheduled payouts (`x/payouts/intelligent-payouts.md`, `scheduled` status) and TDS/compliance field wiring (`vendor-payments` `internal/initiatetds`) were located but not read in full depth — only structurally confirmed to exist, not verified end-to-end.
