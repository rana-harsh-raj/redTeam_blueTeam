# 06 — Source-to-Pay (S2P) implementation readiness

Investigation date: 2026-09-08. Every line is labelled **VERIFIED** (I ran the command / read
the exact file line) or **INFERRED** (reasoning from verified facts).

Clone root (from `/Users/rana.singh/rzp-payouts-architecture/.local/repos-root`):
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture`
(referred to below as `$CLONES`).

Nothing under `$CLONES` was modified. All builds ran on copies restored into
`/private/tmp/claude-502/.../scratchpad/inv/src/<repo>` (referred to as `$INV/src`).

---

## 0. HEADLINE FINDING — the pinned clone worktrees are ~96 % empty (VERIFIED)

`$CLONES/{vendor-payments,vendor-experience,accounting-integrations}` are **not usable as
checkouts today**. Their `.git` directories were pruned (no `HEAD`, no `config`, empty
`refs/`, empty `logs/`) — `git -C <repo> rev-parse HEAD` returns
`fatal: not a git repository`. Their worktrees have been almost entirely deleted:

| Repo | Files in HEAD tree | Files present in `$CLONES` worktree |
|---|---:|---:|
| vendor-payments | 2207 | **85** |
| vendor-experience | 1201 | **28** |
| accounting-integrations | 1712 | **55** |

`go.mod`, `Makefile`, `Dockerfile`, `config/*.toml`, all migrations and nearly all Go
packages are gone from the worktrees. (`$CLONES/payouts` and `$CLONES/x` have the same
pruned `.git`; `payouts` still has 2583 worktree files, `x` only 80. `api`, `workflows`,
`proto`, `rpc` are intact and `rev-parse`-able.) Surviving files are exactly the ones an
earlier agent had open — the deletion looks like a cleanup sweep dated `8 Sep 00:00`.

**Recovery is possible and I did it** (VERIFIED): each `.git/objects/pack/*.pack` is intact
and holds one depth-1 commit plus the full tree. I copied each `.git` to the scratchpad,
wrote a synthetic `HEAD`/`config`, and ran `git checkout-index -a` into `$INV/src/<repo>`,
restoring 2207 / 1201 / 1712 files respectively. All numbers below come from those
restored trees. **Any real S2P work must first restore or re-clone these three repos** —
this is the single highest-priority action item.

A separate full clone of vendor-payments only exists at
`/Users/rana.singh/Desktop/github-hack/vendor-payments` (branch `master`, HEAD
`4d509e0066eb0e43f344f929b9749890cfe5f395`, clean) — this is the **parent** of the pinned
commit, i.e. one commit behind. vendor-experience and accounting-integrations have no
second copy on disk.

---

## 1. Source access

### vendor-payments — VERIFIED
| Field | Value |
|---|---|
| Path (pinned) | `$CLONES/vendor-payments` (pruned, see §0) |
| Usable path | `$INV/src/vendor-payments` (restored from packfile) |
| Commit | `20c4f4d59970471067388afea8b1d65ac39ee126` (parent `4d509e00…`), authored/committed 2026-08-27T18:41:57Z by Harshit Sidhwa, GitHub-signed merge |
| `rev-parse HEAD` in clone root | **fails** — `fatal: not a git repository` |
| go.mod | `module github.com/razorpay/vendor-payments`, `go 1.26` |
| Private deps | 20 `github.com/razorpay/*` requirements: `business-verification-service-sdk-go v0.1.3`, `config-proto v1.4.3`, `error-mapping-module v1.0.32`, `goutils/{authz v1.0.1, dcs v1.5.2, errors v0.3.0, grpcserver v0.4.3, hystrix-prom v0.0.2, itf v0.3.0, kafka/v2 v2.1.2, logger v1.4.3, logger/v2 v2.0.0, matchengine v1.0.3, passport/v4 v4.2.0, request v1.2.1-…, security v0.2.4, spine v0.2.6, tracing v1.3.2, uniqueid v1.0.1, worker/v2 v2.3.2}` |
| Resolution | **All resolve.** `go list -m all` lists all 21 razorpay modules; `go mod verify` → `all modules verified`. They come from the pre-populated module cache `/Users/rana.singh/go/pkg/mod/github.com/razorpay/…`. With `GOPROXY=off` the build fails on *public* deps (`cloud.google.com/go@v0.97.0`), not private ones — so the local cache is razorpay-complete but not public-complete; `GOPROXY=https://proxy.golang.org,direct` fills the gap (network was available). |
| `rpc/` or `internal/protobufclient` | **Neither exists.** Stubs live in `generated_endpoints/rpc/` and `genericAI/rpc/`, both **gitignored** (`.gitignore` lines: `generated_endpoints/{proto,docs,rpc}`, `genericAI/{proto,docs,rpc}`, `*_mock.go`, `mock-accounting-payouts`). Only `buf.gen.yaml`/`buf.yaml`/`buf.lock` are tracked. |

### vendor-experience — VERIFIED
| Field | Value |
|---|---|
| Commit | `df90df21bee54ce0148df43d3fc65e7ba7b60160`, 2026-08-05T10:48:24Z (Himanshu Tekchandani) |
| go.mod | `module github.com/razorpay/vendor-experience`, `go 1.26`, one `replace` (`github.com/apache/thrift => v0.0.0-20161221203622-…`, Cadence/tchannel requirement) |
| Private deps | 21 razorpay modules incl. `goutils/dcs v1.7.0-beta2`, `goutils/spine v0.7.1`, `bvs-sdk-go v0.3.3` |
| Resolution | `go mod verify` → `all modules verified` |
| Stubs | `rpc/` gitignored; **but the `.proto` sources are in-repo** at `proto/vendor-experience/{vendors,workflows,common,admin_actions,vendor_bills,batch,dcs,documents,key_value,vendor_application}/v1` + `proto/common/health/v1`. This matters: `$CLONES/proto/vendor-experience` only has `dcs key_value vendors`, so vendor-experience codegen **must** use its own in-repo protos, not the `proto` monorepo. |

### accounting-integrations — VERIFIED
| Field | Value |
|---|---|
| Commit | `fc13a0b2a38214374e80a16af3ed4465b2d84ef1`, 2026-09-03T02:48:48Z (`rzp-slash[bot]`) |
| go.mod | `module github.com/razorpay/accounting-integrations`, `go 1.26`, same thrift `replace` |
| Private deps | 17 razorpay modules (older pins: `goutils/dcs v1.5.0`, `passport/v4 v4.0.1`, `config-proto v1.3.3-0.2023…`) |
| Resolution | `go mod verify` → `all modules verified` |
| Stubs | `rpc/` gitignored; protos come from `$CLONES/proto/accounting_integrations/**` (26 packages, all present) per `scripts/proto_modules`. |

### tax-payments / tax-compliance — ABSENT (VERIFIED)
No `tax-payments` or `tax-compliance` directory exists anywhere under `$CLONES` or on
`~/Desktop/github-hack`. `ACCESS_MANIFEST.md:18` classifies `razorpay/tax-compliance` as
tier-1 no-access and proposes an SQS sink + Vault dev-mode substitute. **Tax payments is
not a separate service** — it is a package *inside* vendor-payments
(`internal/taxpayments/`, `internal/apiservice/TaxPaymentServer.go`, proto
`proto/tax-payments/service.proto`). So "tax-payments absent" only means the *monolith
route group* and the downstream `tax-compliance` filing consumer are absent; the TDS/tax
business logic **is** available.

### Supporting repos (VERIFIED)
`proto` = `52682577d79d7237944e9fbb10477a1721623065`; `workflows` = `080d71a51b777c89af586c92ac4b5f683a473a7c`;
`api` = `2d665f918b60e917ec92be648fb5816d247f72b1`; `rpc` = `27388ee6335145064774ef31440cc91dbccd8bb0`.
All four match the commits cited in `REPLICATION_WORKFLOW_S2P.md` §1 — **that table is accurate.**
Note the report says "rpc clone failed earlier"; in fact `$CLONES/rpc` is present with 2699 files —
but it contains **no** vendor-payments / vendor-experience / accounting-integrations stubs, so the
conclusion (regenerate with buf) still holds.

---

## 2. Do they build today?

### Result: **NO out of the box; YES after offline codegen** (VERIFIED end-to-end)

**Step 1 — plain `go build ./...` with `GOFLAGS=-mod=readonly GOPRIVATE=github.com/razorpay`:
all three fail, and the *only* failures are missing generated protobuf/Twirp packages.**

- vendor-payments: 21 errors, all `cannot find module providing package
  github.com/razorpay/vendor-payments/generated_endpoints/rpc/…` (+ `…/mock-accounting-payouts`).
  Examples: `internal-accounting-payouts/accountingpayout/core.go:18` (`rpc/tax-payments`),
  `internal/apiservice/VendorPortalServer.go:10` (`rpc/vendor-portal`),
  `internal/boot/helpers.go:31-32` (`rpc/vendor-payments/{bulkimport,callback}/v1`).
- vendor-experience: 11 errors, all `…/vendor-experience/rpc/…` (e.g.
  `internal/errorclass/errors.go:8`, `pkg/health/server.go:8`).
- accounting-integrations: 29 errors, all `…/accounting-integrations/rpc/…` (e.g.
  `internal/service/sync_entries/factory.go:26`, `internal/workflow_manager/server.go:15`).

**No dependency, toolchain, Go-version or private-module error appeared in any repo.**

**Step 2 — I generated the stubs offline and rebuilt.** Method (VERIFIED, reproducible):
copy the needed proto trees into the repo's buf root (`generated_endpoints/proto/` ←
`$CLONES/proto/{accounting-payouts,vendor-payments,vendor-portal,tax-payments}`;
`genericAI/proto/` ← `proto/accounting_integrations`; accounting-integrations `proto/` ←
same; vendor-experience already has `proto/`), drop only the `openapiv2` plugin (docs
only), and run `buf generate` with:
`buf` = `.local/twin-build-tools/payouts-buf` (1.32.0),
`protoc-gen-go` = `.local/twin-build-tools/payouts-protoc-gen-go`,
`protoc-gen-go-grpc@v1.3.0`, `protoc-gen-grpc-gateway@v2.11.3`,
`protoc-gen-twirp@v8.1.2+incompatible` (`go install`ed into `$INV/bin`).
The two BSR deps (`buf.build/googleapis/googleapis`, `buf.build/grpc-ecosystem/grpc-gateway`)
resolved from the **local buf module cache** — no BSR fetch was needed.
Gotchas found: `protoc-gen-twirp` **v5.10.1 and v8.1.0 both fail** with
`plugin "twirp" does not support feature "proto3 optional"` on
`vendor-payments/customfield/v1/api.proto`; only the Makefile's own pin
(`v8.1.2+incompatible`, `Makefile:113`) works.

Generated file counts: vendor-payments 59 (`generated_endpoints/rpc`) + 124 (`genericAI/rpc`);
vendor-experience 41; accounting-integrations 124.

**Step 3 — build results after codegen (VERIFIED):**

| Repo | `go build ./...` | Production binaries |
|---|---|---|
| vendor-payments | 1 error left: `internal-accounting-payouts/tests/basic_mock_util.go:15:2: cannot find module providing package github.com/razorpay/vendor-payments/mock-accounting-payouts` (gitignored mockgen output, generated by `scripts/generate-mocks-accounting-payouts.sh`) | **all 8 build OK**: `cmd/{vendor-payments,worker,workerv2,migration,ap-worker,ocr-worker,accounting-payouts-cron,migration-accounting-payouts}` |
| vendor-experience | **clean, rc=0** | `cmd/api` OK; `cmd/cadence/{activity-worker,workflow-worker}` OK; `cmd/{migration,vp_migration}` need `-tags boot` (`//go:build boot`, matches `Makefile:181-182`) — OK with the tag |
| accounting-integrations | 1 error left: `internal/service/tests/testhelper/utils.go:153,164: undefined: metrics.MockIMetrics / NewMockIMetrics` (gitignored `*_mock.go`) | **all build OK**: `cmd/{api,migration,task,worker,custom_fields_migration}`, `cmd/cadence/worker` |

**`go vet` for vendor-payments (VERIFIED):**
- `go vet ./internal/payout/...` → non-test package compiles; the only diagnostic is
  `internal/payout/core_test.go:26:27: undefined: rxclient.NewMockIRxClient` (mockgen artifact).
- `go vet ./internal/taxpayments/...` (before codegen) → the `generated_endpoints/rpc/{tax-payments,vendor-payments}`
  import errors above; after codegen it reduces to the same class of mockgen-only gap.

**Conclusion (VERIFIED):** the three repos are **buildable today from the pinned commits with
no source edits and no new access** — the prerequisites are (a) restoring the pruned
worktrees, (b) `buf generate` with the pinned plugin versions, (c) `mockgen` for the test
packages. This directly corrects `REPLICATION_WORKFLOW_S2P.md` §2.1's "module already
vendors stubs — confirm": it does **not** vendor them; they are gitignored and must be
generated. The good news is the generation is fully offline-able.

**Dockerfiles / Makefiles (VERIFIED):**
- vendor-payments has `Dockerfile`, `Dockerfile.devstack`, `Dockerfile.itf`, plus its own
  `docker-compose.yml`. Makefile: `proto-fetch` (sparse-checkout of `razorpay/proto` per
  `scripts/{vp_proto_modules,ai_proto_modules}` — needs `GIT_TOKEN` or public https),
  `proto-generate` (`buf generate` in `generated_endpoints/` and `genericAI/`),
  `proto-omitempty` (`scripts/twirp-omitempty.sh`), `mock-gen` = `mock-gen-vp` + `mock-gen-ap`,
  `build: build-info pre-build`, `pre-build: clean deps proto-refresh mock-refresh`.
  Pinned tool versions: `BUF_VERSION 1.5.0`, `PROTOC_GEN_GO v1.28.1`,
  `PROTOC_GEN_TWIRP v8.1.0` (but line 113 installs `v8.1.2+incompatible`),
  grpc-gateway/openapiv2 `v2.11.2`.
- vendor-experience: **no Dockerfile at repo root**; images are built from `build/docker/{migration,vp_migration,…}`;
  `devspace.yaml` present. Makefile targets `go-build-api`, `go-build-migration`,
  `go-build-cadence-activity-worker`, `go-build-cadence-workflow-worker`.
- accounting-integrations: **no root Dockerfile** (`build/` dir); Makefile
  `go-build-{api,cadence-worker,worker,task,migration}`.

---

## 3. Required infra from config (all VERIFIED from the restored `config/*.toml`)

### vendor-payments (`config/default.toml` 812 lines, `config/prod.toml`)
- **DB**: MySQL. `default.toml:20-31` `[db] name = "api_live"` (!), `[dbtest] api_live_test`,
  `[dbreplicalive]/[dbreplicatest] name = "vendor-payments"`. `prod.toml:33-43` `[db] name = "vendor-payments"`,
  `url = "stage-8.db.np.razorpay.vpc"`. Second datastore for the accounting-payouts module:
  `config-accounting-payouts/default.toml:29` also `api_live`.
- **Redis**: `[cache] driver="redis"`, `default.toml:183-190` `localhost:6379 db 0`;
  `prod.toml:85` `qa.cache.np.razorpay.vpc`.
- **Kafka**: two brokers blocks — `[Queue]` (OCR, `prod-ocr`) and `[KafkaQueue]` (`prod-tds`,
  `prod-email-int`), brokers `prod-noncde-kafka.razorpay.com:9090`, `EnableTLS = true`,
  mTLS via `UserCertificate`/`UserKey`/`CACertificate`, consumer group `vendor-payments`.
  Producer/consumer topics found in `prod.toml`: `prod-tds`, `prod-email-int`, `prod-ocr`,
  `prod-auto-invoice-processing`, `prod-fund-account-verification`, `contact-entity-update-live`,
  `add-tds-entry`, `prod-pdf-generate`, `prod-email-send`, `prod-po-status-update`,
  `prod-grn-status-update`, `prod-vp-status-update`, `prod-elastic-data-ingestion`,
  `prod-fetch-gstr-2a`, `prod-fetch-gstr-2b`, `prod-vp-gstr-2b-recon`, and the cross-domain
  `[VendorPaymentSourceUpdaterConfig] AIQueueName = "prod.x.vendor-payments.accounting-payouts.status-update"`
  (`prod.toml:272-273`).
- **Elasticsearch**: `prod.toml:550-552` `[ElasticConfig] Url = "https://prod-s2p.es.razorpay.vpc"`,
  `RetryOnConflict = 4`; ingestion via `[ElasticDataIngestionConfig]` Kafka topic
  `prod-elastic-data-ingestion`.
- **SNS/SQS**: **none in vendor-payments config** (grep for `sqs|sns` in `prod.toml` → 0 hits).
  Payout status reaches vendor-payments via the monolith SourceUpdater, not directly.
- **External hosts** (`prod.toml`): `api-graphql.razorpay.com` (`[api]:123`),
  `ufh.razorpay.com:118`, `metro-web.razorpay.com:24`, `x.razorpay.com:14`,
  `apibankingone.icicibank.com` + `ixpress.icicibank.com` (`:166-190`),
  `api.veryfi.com:243`, `api-platform.mastersindia.co:555`, `api.sandbox.co.in:502`,
  `bvs.razorpay.com:322`, `abacus.razorpay.com:526`, `splitz.razorpay.com:295`,
  `stork.razorpay.com:312`, `workflows.razorpay.com:343`, `reminders.razorpay.com:276`,
  `mozart.razorpay.com:282`, `xperience.int.razorpay.com:519`,
  `vendor-experience.razorpay.com:533`, `accounting-integrations.int.razorpay.com:562`,
  `edge-admin-internal.razorpay.com:540`, `invoices-x.razorpay.com:341`.
  `default.toml` adds `[gimli]:85-88` (`127.0.0.1:8082/v1/`), `[mailgun]:90-93`, `[raven]:71-75`,
  `[MailConfig]:315-320`.
- **Migrations**: `internal/migrations/` = **88** goose Go migrations (`20200331180015_…` →
  `20260513140632_1033_tds_category_2026.go`); `internal-accounting-payouts/migrations/` = **9**.
  `cmd/migration` and `cmd/migration-accounting-payouts` are the runners.
  **67 tables** in the main schema: `vendors, vendor_payments, vendorpayment_payout,
  vendorpayment_advance, vendor_advances, vendor_settlements, vendor_settlement_invoices,
  vendor_fund_accounts, vendor_payment_metadata, vendor_portal_invites, tax_payments,
  direct_tax_payments, tds, tds_categories, entity_taxes, control_policies, approvals,
  approval_details, purchase_orders, goods_received_notes, line_items, items, item_mappings,
  addresses, branches, attachments, batches, bulk_imports, business_infos, records, settings,
  state_change_logs, entity_actions, entity_mappers, entity_group_mappers,
  entity_custom_field_info, entity_reporting, mails, merchant_email_mappings, ocr_requests,
  auto_processed_invoices, penalties, pg_payments, gst, gstr_reports, gstr2a_reports,
  gstr_report_sync_status, gst_input_credit_integrations, icici_tax_pay_requests,
  icici_retry_requests, recon_logs, recon_alert_logs, recon_po_aggregates, downtime_schedules,
  vp_file_uploads, accounting_*` (8 accounting tables shared with the accounting-payouts module).
- **Env vars at boot**: `APP_MODE` (`internal/boot/env.go:30` — selects `config/<mode>.toml`),
  `WORKDIR` (`pkg/config/config.go:29`), `JAEGER_HOSTNAME` (`internal/boot/constants.go:4`),
  plus Kafka mTLS material bound via struct tags `env:"UserCertificate"`, `env:"UserKey"`,
  `env:"CACertificate"` (`internal/config/config.go:266-268, 287-289`). `prod.toml` also uses
  the `"env|VAR"` indirection (e.g. `VP_BVSCONFIG_KEY`, `VP_BVSCONFIG_SECRET`).

### vendor-experience
- **DB**: two MySQL connections — `[db] name = "vendor_experience"` and
  `[vpdb] name = "vendor_payment"` (`default.toml:15-45`) — i.e. it reads the
  **vendor-payments schema directly** (see §7). `prod.toml` binds both through
  `env|VENDOR_EXPERIENCE_{DB,VPDB}_CONNECTIONCONFIG_{USERNAME,NAME,…}`.
- **Redis**: `[cache] beta.cache.np.razorpay.vpc:6379` (default) /
  `rzp-razorpayx-prod-common.razorpay.vpc` (prod:54).
- **Queue**: `[queue] driver = "sqs"` (`default.toml:61-79`) — **the only SQS user of the three**;
  region from `env|VENDOR_EXPERIENCE_QUEUE_SQS_REGION`.
- **Kafka**: `[KafkaQueue]` producer only, topic `[KafkaQueue.Topics] ContactUpdate =
  "stage-vendor-entity-updates-event"` (prod: `prod-vendor-entity-updates-event`).
- **Cadence**: `[CadenceAdapter] HostPort = "cadence-frontend.cadence.svc.cluster.local:7933"`,
  `Domain = "vendor-experience-domain"`, `CreateNonExistentDomain = true`, `Stub = false`;
  `[Workflows] StickyCacheSize = 3500`.
- **Elasticsearch**: none.
- **External hosts** (prod): `api-graphql.razorpay.com/v1`, `stork.razorpay.com`,
  `ufh.razorpay.com`, `bvs.razorpay.com`, `workflows.razorpay.com`,
  `vendor-payments.razorpay.com`, `batch.razorpay.com`, `edge-admin-internal.razorpay.com` (JWKS).
  Inbound basic-auth consumers declared at `prod.toml:241-256`: `api`, `batch`, `workflows`,
  `vendor_payments`, `accounting_integration`, `ve-admin`.
- **Migrations**: `internal/database/migrations` = **20**, `internal/database/vp_migrations` = **4**.
  Tables: `vendors, vendor_applications, vendor_bills, application_details, application_logs,
  change_requests, comments, documents, entity_changes, key_value_store, status_change_logs, workflows`.
- **Binaries**: `cmd/{api,cadence/activity-worker,cadence/workflow-worker,migration,vp_migration}`.

### accounting-integrations
- **DB**: MySQL `[db] name = "accounting_integrations"`; plus `[dbOld]`, `[dbreplica]`,
  **`[VendorPaymentsDB]`** (again a direct read of the vendor-payments schema), and
  `[CustomFieldsDB]`.
- **Redis**: `[Redis]` + `[cache] localhost:6379`.
- **Cadence**: `[CadenceConfig] HostPort = cadence-frontend.cadence.svc.cluster.local:7933`,
  `Domain = "prod-accounting-integrations"` (`prod.toml:135-137`).
- **Kafka listeners** (prod.toml): `[VendorPaymentUpdateListener] Topic =
  "prod.x.vendor-payments.accounting-payouts.status-update"` (:196), `[VendorAdvanceUpdateListener]
  prod-vendor-advance-event` (:220), `[FileHandlerListener] prod-file-handler-event` (:244),
  `[VendorEntityUpdateListener] prod-vendor-entity-updates-event` (:303),
  `[ItemUpdateListener] prod-items-update-event` (:268),
  `[InitiateReportListener] prod-initiate-report` (:333),
  `[ZohoInvoiceFetchListener] prod-zoho-invoice-fetch` (:384).
- **External hosts**: `api-graphql.razorpay.com`, `vendor-payments.razorpay.com`,
  `vendor-experience.razorpay.com`, `mozart.razorpay.com`, `batch.razorpay.com`,
  `ufh.razorpay.com`, `reporting.razorpay.com/v1`, Zoho OAuth redirect
  `https://api.razorpay.com/v1/direct/accounting-integrations/callback`.
- **Elasticsearch**: none.
- **Migrations**: `internal/database/migrations` = **26**;
  `internal/custom_fields/database/migrations` = **3**. Tables: `integrations,
  integration_metadatas, sync_entries, sync_entry_relationship, sync_entry_reports,
  sync_entry_state_change_logs, sync_cursors, accounting_groups, accounting_ledger,
  accounting_mapper, accounting_vouchers, accounting_integration_taxes,
  accounting_integrations_bank_accounts, cost_centres, preferred_groups, items, invitations,
  report_details, report_mappings, rules, settings, workflows, zoho_invoice_sync_tracking`.

---

## 4. Minimum synthetic entities

Derived from the migrations + the tax-payment/payout flows (**INFERRED** unless noted).

**Monolith / payouts side (must exist before vendor-payments can do anything):**
1. 1 `merchant` (Direct RX account) — reuse the twin's existing fixture
   `ENV2_COMPOSE/seeds/merchants.json` + `seeds/monolith/merchants.json` (VERIFIED present).
2. 1 banking account for that merchant — served by
   `v1/banking_accounts_internal/` (`internal/taxpayments/apicaller.go:20`, VERIFIED); the
   response must contain a matching `account_number`, else
   `apicaller.go:64-68` errors "invalid account number".
3. 1 internal contact of type `rzp_tax_pay` (`payouts/internal/app/common/appConstants/constants.go:113`,
   VERIFIED) + its fund account, reachable through `v1/internalContactPayout/`.
4. 3 users with dashboard roles (maker `finance_l1`, checker `finance_l2`, `owner`) — the twin
   already has `passport_fixtures.json` identities per `monolith-stub/CONTRACT.md` (VERIFIED).

**vendor-payments side (minimum rows):**
5. `vendors` (≥1, ideally 2 so one can fail fund-account verification) + `vendor_fund_accounts`.
6. `vendor_payments` (the invoice) + `line_items` + `items` (≥1 each).
7. `tds_categories` — **already seeded by the migration itself**
   (`internal/migrations/20200402152150_create_and_insert_into_tds_categories_table.go`
   INSERTs 194J/194H/194A/194C×2/194I×2/no-TDS/193/194B/194 …, VERIFIED). No fixture needed.
8. `entity_taxes` row per invoice (TDS applicability), `tds` row for the monthly batch.
9. `control_policies` — approval thresholds; `internal/controlpolicy/default_policies.go`
   exists in-repo and can seed defaults (VERIFIED file present).
10. `settings` row (merchant-level tax-payment enablement — consumed by
    `v1/tax-payments/enabledMerchantSettings`, `apicaller.go:18`).
11. `tax_payments` / `direct_tax_payments` rows are produced by the flow, not seeded.
12. `vendorpayment_payout` links the invoice to the created payout id.

**accounting-integrations side:** 1 `integrations` row (Zoho org mapping) + `settings`;
`sync_entries` are produced.

**Where seed/fixture code already exists in-repo (VERIFIED):**
- `vendor-payments/tests/e2e/` is a **complete e2e harness**: `database/db.go`, 25+ row
  builders in `repo/` (`control_policies.go`, `entity_tax.go`, `item.go`, `line_item.go`,
  `purchase_order.go`, `goods_received_note.go`, `branch.go`, `batches.go`, …), service
  clients in `services/` (`payouts/`, `fund-accounts/`, `contacts/`, `banking-accounts/`,
  `tax-payments/`, `vendor-payments/`, `controlpolicy/`, `stork/`, `ufh/`), configs in
  `configs/{ApiConfig,MerchantsConfig,VendorPaymentsConfig,CronConfig}`, and suites under
  `tests/{vendor-payments,tax-payments,controlpolicy,purchase-order,goods-received-note,…}`.
  This is the single largest reusable asset for building the journey — it is *not* mentioned
  in the expansion reports.
- `vendor-payments/internal/tests/{basic_mock_util.go,functional_test.go,taxpayments_core_test.go,
  direct_tp_test.go,vp_core_test.go,vendor_portal_test.go}` — in-process functional suite.
- `accounting-integrations/e2e/tests/{bill_test.go,bill_v2_test.go,advance_sync_test.go,
  vendor_payment_sync_workflowv2_test.go}`.
- `vendor-experience/e2e/` and `tests/functional/testsuite` (Makefile `-tags functional`).

---

## 5. Reusable components from the Payouts twin (`ENV2_COMPOSE`)

**monolith-stub — much less coverage than the expansion reports claim (VERIFIED).**
`substitutes/monolith-stub/server.py` `ROUTES` (lines 1080-1112) contains **32** routes.
Grep for the S2P surface returns only:
- `GET /fund_accounts_internal/` (`server.py:264`, `ROUTES` line, `CONTRACT.md:30`) — **present**.
- `payouts_internal` → **absent**. `internalContactPayout` → **absent**.
  `contacts_internal` → **absent**. `banking_accounts_internal` → **absent**.
  `tax-payment-id` → **absent**. `vendor-payments` / `tax-payments` route groups → **absent**.

What *is* there and reusable: `GET /internal/merchants/{id}`, the whole `payouts_service/*`
callback group (`fetch_pricing_info`, `deduct_credits`, `reverse_credits`, `source_update`,
`status_details_source_update`, `mail_and_sms`, `dual_write`, `decrement_free_payouts`,
`create_ledger`, `free_payout_rollback`, `create`, `create_fta`), `merchant/on_hold_slas_internal`,
`actor_info_internal`, `users_internal`, `update_fts_fund_transfer` relay,
`banking_account_statement/payout_update`, `internal_balances_queued`, and the `/_arena/*`
introspection endpoints (`log`, `ledger_emits`, `merchant_features`, `balance-sync`, `relay*`).
Auth is HTTP Basic from `STUB_BASIC_AUTH_FILE` / `secrets/auth_monolith_shared.txt`, user `rzp_live`.

⇒ **Correction to the expansion reports (VERIFIED):**
`REPLICATION_WORKFLOW_S2P.md:6`, `DOMAIN_CARDS.md:28` and `EXPANSION_REPORT.md:64` all state
the monolith substitute "already implements/serves `payouts_internal`, `internalContactPayout`,
contacts/fund-accounts internal". Only `fund_accounts_internal` is implemented. The three
routes that carry the entire S2P payout leg must be **built**, not reused. This materially
raises the cost of the "buildable against the existing arena" argument that drove the domain
selection in `EXPANSION_REPORT.md:64`.

**workflow-engine (VERIFIED).** `substitutes/workflow-engine/wfengine/server.py` serves
`POST /twirp/rzp.workflows.workflow.v1.WorkflowAPI/Create` (:171), `POST /v1/workflows` (:256),
`GET /admin/workflows` (:147), `POST /admin/{orgs,actors,policies,expire-sweep}` (:234-251),
and arena hooks `/_arena/{pending,workflows,decide}` (:132-231).
`_payouts_create_view` (:46) and the literal `"type": "payout-approval"` (:58) are the only
payouts-specific pieces. **The callback path is already generic**: `callbacks.py:107-155`
resolves the terminal callback from the *caller-supplied* create body
(`callback_details.domain_status.{approved,rejected}.{url_path,method,headers,payload}`),
falling back to a default sink. vendor-payments supplies exactly that shape —
`internal/workflows/core_test.go:1821,1835,1844` show `UrlPath:
"/twirp/vendorpayments.Vendorpayments/{SendApprovalNotification,WorkFlowStatusUpdate}"`
(VERIFIED). So supporting `vendor-payment-v2-approval` is mostly a matter of accepting the new
`config_type` string and pointing `ps_api_url` at vendor-payments — **not** a new callback
mechanism. (INFERRED, but grounded in the two files above.)

**Datastores/infra already in `docker-compose.yml` (87 services, VERIFIED):**
`kafka` (apache/kafka:3.8.0, KRaft, `KAFKA_AUTO_CREATE_TOPICS_ENABLE: "true"` — new S2P
topics need no provisioning), `localstack/localstack:3.8` (SQS/SNS), `redis:7-alpine`,
four `mysql:8.0` instances (`mysql-payouts`, `mysql-fts`, `mysql-xbalances`,
`mysql-apidb-stub`), `mongo:6.0` (cfa), `postgres-ledger`.
**Not present: Elasticsearch, Cadence, Vault** — all three are new containers for S2P.

**Other reusable substitutes (VERIFIED):** `stork-capture` (Twirp `WebhookAPI/{Create,List,ProcessEvent}`,
`SMSAPI/Send`, `EmailAPI/Send`, plus `GET /_captured/events` and `/_arena/events?merchant=` for
assertions — directly reusable for vendor-payments' `[StorkConfig]`), `kong-lite` (host-facing
ingress, route table via `KONG_LITE_ROUTES_JSON` prefix→upstream, RS256 passport minting via
`POST /_arena/mint`; adding `/v1/vendor-payments` and `/v1/tax-payments` prefixes is a
config-only change), `mozart-sim`/`mozart-mock` (accounting-integrations and vendor-payments
both have `[mozart]` config — direct reuse), `dcs-stub`, `splitz-stub`, `shield-stub`,
`bankingaccounts-stub`, `merchant-webhook-sink`, `batch-sim` (new in M6; vendor-payments has
`[BatchConfig]` and vendor-experience `services.Batch` → likely reusable), `pricing-stub`,
`xas-sim`, `ledger-gate`, `verifier`, `cron-driver` (for the tax-payment crons).

**Seeds:** `ENV2_COMPOSE/seeds/{merchants.json,pricing.json,monolith/{merchants,fund_accounts,misc}.json,
mysql,mongo,postgres,localstack,dcs,splitz,shield,stork,schema-patches,generator}` (VERIFIED).

---

## 6. Components requiring substitution (config keys cited, all VERIFIED)

| External | Config key(s) | Existing twin asset | Verdict |
|---|---|---|---|
| ICICI direct tax + GST | vendor-payments `prod.toml:[IciciConfig]` `PaymentUrl/VerifyUrl/ChallanUrl/GenerateTokenUrl` = `apibankingone.icicibank.com/api/v1/directTaxTin2/*` (:166-169); GST leg `GstEnquiryUrl/GstReFetchUrl/GstPaymentUrl/GstVerificationUrl` = `ixpress.icicibank.com/apibanking/live/corpapi/v2/gst/*` (:187-190). Code: `internal/icici/{core.go,apicaller_mocked.go}` | none | **New sim required.** `apicaller_mocked.go` exists in-repo → a mock seam already exists, cheapest path. |
| MastersIndia (GST OCR) | `prod.toml:554-559 [MastersIndia] Host = api-platform.mastersindia.co`; code `internal/ocr/tasks/mastersindia` | none | new stub |
| Veryfi (invoice OCR) | `prod.toml:242-247 [VeryfiConfig] Endpoint = api.veryfi.com/api/v8/partner/documents/`; `[VeryfiConfig.OcrAllowedQueues]` | none | new stub |
| Sandbox.co.in (GST portal) | `prod.toml:502 [SandboxConfig] BaseUrl = https://api.sandbox.co.in` | none | new stub |
| BVS | `prod.toml:321-326 [BVSConfig] baseUrl = bvs.razorpay.com, clientId = X_VENDOR_PAYMENTS, key/secret = env|VP_BVSCONFIG_*`; vendor-experience `services.BVS.baseURL` (:179) | none (SDK `business-verification-service-sdk-go` cloned) | new stub, contract from SDK |
| abacus | `prod.toml:525-530 [AbacusConfig] BaseUrl = abacus.razorpay.com, Key = vendor_payments` | none | new sink |
| metro | `prod.toml:23-31 [Metro] BaseUrl = metro-web.razorpay.com/, ProjectName = vendor-payments, ProjectAuthKey = vendor-payments__aec456` | none | new sink |
| ufh | `prod.toml:117-120 [UfhConfig] url = ufh.razorpay.com, storeType = "s3"`; AI `prod.toml:293`; VE `services.UFH.baseURL:158` | LocalStack S3 exists | thin stub over LocalStack |
| reminders | `prod.toml:275-279 [RemindersConfig] BaseUrl = reminders.razorpay.com, ApiBaseUrl = tax-payments/reminders` | none | new sink |
| gimli | `default.toml:85-88 [gimli] baseUrl = http://127.0.0.1:8082/v1/, mockGimli = false` | none | new sink (short-URL) |
| mailgun | `default.toml:90-93 [mailgun] domainName/privateKey`; `[MailConfig]:315-320`; `[raven]:71-75` | `stork-capture` captures email | sink; reuse stork-capture pattern |
| Cadence (vendor-experience **and** accounting-integrations) | VE `default.toml:281-285 [CadenceAdapter] HostPort/Domain/CreateNonExistentDomain`, `[Workflows] StickyCacheSize`; AI `prod.toml:134-137 [CadenceConfig]` | none | **new container** (or `Stub = true` for VE first pass). Note AI *also* needs it — the expansion reports only flag VE. |
| Elasticsearch | `prod.toml:550-552 [ElasticConfig] Url = prod-s2p.es.razorpay.vpc`; `[ElasticDataIngestionConfig]` topic `prod-elastic-data-ingestion` | none | new single-node container |
| External cron | `cmd/accounting-payouts-cron`, `tests/e2e/configs/CronConfig`, `[InitiateTdsConfig]`, `[CurrentTask]` | `cron-driver` service exists | reuse `cron-driver` |
| Zoho / Tally / QuickBooks | AI `prod.toml:147-152 [Zoho]` + redirect URI; `[Tally]:191` | none | sinks |
| tax-compliance + Vault | not in these three repos; payouts `[hvault]`, SQS `prod-tax-compliance-payout-events` | none | out of scope for the minimal journey |
| Xperience / workflows / splitz / stork / batch / vendor-experience / accounting-integrations HTTP clients | `[XperienceConfig]:519`, `[WorkflowConfig]:343`, `[SplitzConfig]:295`, `[StorkConfig]:312`, `[BatchConfig]`, `[VendorExperienceConfig]:533`, `[CustomFieldClientConfig]:562` | `splitz-stub`, `stork-capture`, `workflow-engine`, `batch-sim` | **reuse** |

---

## 7. Fidelity risks

1. **Cross-schema direct DB reads (VERIFIED, highest risk).** vendor-experience opens a second
   MySQL connection `[vpdb] name = "vendor_payment"` and accounting-integrations opens
   `[VendorPaymentsDB]` — both read the vendor-payments schema directly, bypassing any RPC.
   A twin that only wires the RPC seams will silently diverge. vendor-payments' own
   `config/default.toml:28` points `[db]` at **`api_live`** (the monolith schema) in dev,
   switching to a dedicated `vendor-payments` schema only in `prod.toml:40` — so "which schema
   is authoritative" is environment-dependent and must be pinned deliberately.
   (The commonly-repeated "vendor-payments reads api_live directly" is true of the *dev/default*
   profile only — VERIFIED by grep: `api_live` appears in `config/default.toml` and
   `config-accounting-payouts/default.toml` and nowhere else.)
2. **Twirp/HTTP proxy through the monolith (VERIFIED).** Every payout vendor-payments creates
   goes `vendor-payments → api (monolith) → payouts`, via `v1/payouts_internal/`,
   `v1/internalContactPayout/`, `v1/payouts_internal/%s/cancel`, `v1/payouts/2fa/create_internal`
   (`internal/payout/core.go:83-86`) and `v1/payouts_internal/%s/tax-payment-id`
   (`internal/taxpayments/apicaller.go:21`), all through `internal/rxclient` with
   `X-Razorpay-Account` + basic auth `rzp_live`/`rzp_test` (`rxclient/core.go:27-32`) and
   `X-Dashboard-User-Id` / `X-Payout-Idempotency` headers (`payout/core.go:114-117`).
   The twin has **no** substitute for this hop (see §5) — building it wrong (auth, header
   propagation, idempotency-key handling, error mapping) silently changes the semantics the
   whole journey is meant to assure.
3. **Secrets in tracked config (VERIFIED).** `config/prod.toml` has ~60 `password/secret/key`
   entries; `config/default.toml:93` contains a real-format Mailgun private key, and
   `default.toml:196-210 [auth]` carries placeholder inter-service passwords. Any prepared
   copy must go through the existing Gitleaks admission gate
   (`ENV2_COMPOSE/build/prepare-repos.py`, per `BUILD_PROVENANCE.md`) before it is staged.
4. **Cron-driven RPCs are external (VERIFIED).** `cmd/accounting-payouts-cron`,
   `[InitiateTdsConfig]`, `[TdsTaskConfig] QueueName = "tds"`, `CancelQueuedPayoutCron` and the
   `[CurrentTask]` topic-selection variable mean much of the tax flow only fires on a schedule
   invoked from outside the process. Without wiring `cron-driver`, journeys will look "green"
   because nothing ran.
5. **mTLS Kafka in prod config vs PLAINTEXT in the arena (VERIFIED).** All three repos ship
   `EnableTLS = true` + `UserCertificate/UserKey/CACertificate`; the arena broker is
   `PLAINTEXT://kafka:9092`. This is an arena-patch-shaped divergence (same class as the
   existing `ARENA_DCS_URL` / `ARENA_STORK_JSON` patches called out in `BUILD_PROVENANCE.md`)
   and must be recorded as such, not silently configured away.
6. **Generated code is not pinned (VERIFIED).** Because `rpc/`, `generated_endpoints/rpc/`,
   `genericAI/rpc/` and all `*_mock.go` are gitignored, the wire DTOs are a *function of the
   generator version*. I proved twirp v5.10.1/v8.1.0 fail and v8.1.2 works; `buf` 1.32.0 was
   used instead of the pinned 1.5.0. Byte-level DTO parity with prod is therefore **not**
   established by a successful build.
7. **Two Cadence domains, not one (VERIFIED).** vendor-experience (`vendor-experience-domain`)
   and accounting-integrations (`prod-accounting-integrations`) both need Cadence; the
   expansion reports treat Cadence as a vendor-experience-only cost.
8. **Elasticsearch is on the write path, not just search (VERIFIED).** `[ElasticDataIngestionConfig]`
   consumes `prod-elastic-data-ingestion`; if ES is absent that consumer will fail rather than
   degrade, which can mask or cause journey failures.

---

## 8. Verification of six specific claims from the expansion reports

| # | Claim | Result | Evidence |
|---|---|---|---|
| a | `vendor-payments/internal/payout/core.go` contains `v1/payouts_internal/` and `v1/internalContactPayout/` | **VERIFIED** | `internal/payout/core.go:83` `CreatePayoutPath = "v1/payouts_internal/"`; `:84` `CreatePayoutOnInternalContact = "v1/internalContactPayout/"`; also `:85` `CancelPayoutPath = "v1/payouts_internal/%s/cancel"` and `:86` `Create2FaPayoutPath = "v1/payouts/2fa/create_internal"` (bonus, not in the reports) |
| b | `vendor-payments/config/prod.toml` has `[api] url = "https://api-graphql.razorpay.com"` | **VERIFIED** | `config/prod.toml:122` `[api]`, `:123` `url = "https://api-graphql.razorpay.com"`; also `:124` `key = "rzp_live"`, `:126` `dashboardurl`, `:127` `reportingdashboardurl` |
| c | `internal/taxpayments/apicaller.go` contains `tax-payment-id` | **VERIFIED** | `internal/taxpayments/apicaller.go:21` `UpdateTaxPaymentIdOnPayout = "v1/payouts_internal/%s/tax-payment-id"`; neighbours `:18` `v1/tax-payments/enabledMerchantSettings`, `:19` `v1/tax-payments/sendMail`, `:20` `v1/banking_accounts_internal/` |
| d | `workflows/internal/constants/constants.go` declares vendor workflow types | **VERIFIED** | `$CLONES/workflows/internal/constants/constants.go:35-41`: `:36` `payout-approval`, `:37` `purchase-order-approval`, `:38` `goods-received-note-approval`, `:39` `vendor-payment-v2-approval`, `:40` `vendor-onboarding-approval` (5 types, the reports say "4 new" — correct, 4 are new relative to `payout-approval`) |
| e | `payouts/internal/app/contact/type.go` mapping | **VERIFIED** | `:16-21` `internalAppToAllowedInternalContact = {"vendor_payments": [TaxPayment], "capital_collections_client": …, "charge_collections_internal": …, "xpayroll": …}`; `:23-28` `internalContactTypes = [RzpFees, TaxPayment, CapitalCollections, ChargeCollections, XPayroll]`; gate function `ValidateInternalAppAllowedToCreatePayoutsOnType` at `:35-48`. `TaxPayment = "rzp_tax_pay"` at `payouts/internal/app/common/appConstants/constants.go:113` |
| f | `payouts/config/prod.toml` `[source_update_topics]` | **VERIFIED** | `:368` `[source_update_topics]`; `:375` `vendor_payments = "payout-updates-vendor-payments"`; `:376` `tax_payments`, `:377` `vendor_settlements`, `:378` `vendor_advance` all share that topic; siblings `xpayroll:369`, `payout_links:370`, `settlements:371`, `refund:372`, `charge_collections:380`, `capital_collections:381` |

**6 / 6 VERIFIED.** Two *other* claims in the same reports are **FALSE** (see §5 and §2):
`REPLICATION_WORKFLOW_S2P.md:6` / `DOMAIN_CARDS.md:28` / `EXPANSION_REPORT.md:64`
("monolith substitute already implements `payouts_internal`, `internalContactPayout`,
contacts internal") and `REPLICATION_WORKFLOW_S2P.md:24` ("module already vendors stubs").
A third is stale: "rpc clone failed earlier" — `$CLONES/rpc` exists at `27388ee6…`.

---

## 9. Smallest end-to-end journey with real cross-domain value

### Proposal: **J-TDS-min — "monthly TDS remittance on an internal contact, with tag-back"**

**Why this one and not the invoice/maker-checker journey.** J1 (invoice → approval → payout)
needs the full monolith `payouts_internal` proxy *plus* control policies, workflow-engine
extension, and the vendor/fund-account/invoice fixture set. J-TDS-min exercises the one thing
no existing twin journey can: **a second origin service creating a payout on an internal
contact type through the internal-app allow-list, and writing back a cross-domain
correlation id.** That is exactly the assurance surface `payouts/internal/app/contact/type.go`
guards, and it is currently untested from a real caller (M4's `F-M4-001` free_payout IDOR came
from the payouts side only).

**Service and code path (all VERIFIED file:line):**
1. `vendor-payments` `cmd/workerv2` / `cmd/accounting-payouts-cron` → `internal/taxpayments`
   (`core.go`, `validators.go`, `repo.go`) — triggered by the `add-tds-entry` Kafka topic
   (`config/prod.toml:336`) or the cron task, not by a public API. Use the Kafka trigger: it
   needs no new ingress.
2. `internal/taxpayments/apicaller.go:20` → `GET v1/banking_accounts_internal/` (monolith)
   to resolve the debit account.
3. `internal/payout/core.go:84,138` `CreatePayoutOnInternalContact` →
   `POST v1/internalContactPayout/` (monolith) with headers `X-Razorpay-Account`,
   `X-Dashboard-User-Id`, `X-Payout-Idempotency` (`core.go:114-117`), basic auth `rzp_live`
   (`rxclient/core.go:27-32`).
4. monolith → `payouts` create with `contact_type = rzp_tax_pay` and internal app
   `vendor_payments` → gate `ValidateInternalAppAllowedToCreatePayoutsOnType`
   (`payouts/internal/app/contact/type.go:35-48`).
5. `internal/taxpayments/apicaller.go:21` → `POST v1/payouts_internal/{id}/tax-payment-id`
   (tag-back) → payouts writes `payout_details.tax_payment_id`.
6. payouts → FTS → processed → `[source_update_topics] vendor_payments =
   "payout-updates-vendor-payments"` (`payouts/config/prod.toml:375`) → monolith SourceUpdater
   → vendor-payments `PayoutStatusChange`.
7. vendor-payments produces to `prod.x.vendor-payments.accounting-payouts.status-update`
   (`vendor-payments/config/prod.toml:273`) → **accounting-integrations**
   `[VendorPaymentUpdateListener]` (`accounting-integrations/config/prod.toml:196`) → a
   `sync_entries` row. *This step is the real cross-domain value: it is a single Kafka topic
   name shared by two independently-built services, verifiable by string equality today and by
   a live message tomorrow.*

**Twin components used:**
- Reuse as-is: `kafka` (auto-create topics), `mysql-payouts`, `mysql-apidb-stub` (new schema
  `vendor-payments`), `redis`, `localstack`, `payouts-api` + FTS/ledger/cfa workers,
  `kong-lite` (no new route needed for the Kafka-triggered variant), `stork-capture`,
  `cron-driver`, `mozart-sim`, `dcs-stub`, `splitz-stub`, `verifier`.
- **Build (3 new monolith-stub routes only):** `POST /v1/internalContactPayout/`,
  `POST /v1/payouts_internal/{id}/tax-payment-id`, `GET /v1/banking_accounts_internal/`.
  All three are additive entries in `monolith-stub/server.py::ROUTES` next to the existing
  `fund_accounts_internal` handler, with seeds in `ENV2_COMPOSE/seeds/monolith/`.
  (`POST /v1/payouts_internal/` and `/cancel` can be deferred to J1/J9.)
- **Build (1 new container):** `vendor-payments` image — `cmd/workerv2` + `cmd/migration` only
  (both **VERIFIED building**). The web binary, OCR worker, ES, Cadence, ICICI, OCR/GST stubs
  and vendor-experience are all **not needed** for this journey — that is what makes it minimal.
- **Optional 5th assertion:** `accounting-integrations` `cmd/worker` (VERIFIED building) with
  a Zoho sink; if that is too much, assert only the Kafka message shape against
  `accounting-integrations/internal/service/job/vp_update_listener_test.go`.

**Assertions (each maps to a verified artefact):**
- A1 payouts row exists with `contact_type = rzp_tax_pay`, `source_type = tax_payments`.
- A2 `payout_details.tax_payment_id` equals the vendor-payments `tax_payments.id` (tag-back).
- A3 **negative control**: replay step 3 with internal app `xpayroll` → rejected by
  `type.go:36-40` with `AppNotPermittedToCreatePayoutOnThisContactType`. (Directly exercises
  the gate; no extra fixtures.)
- A4 idempotency: replay step 3 with the same `X-Payout-Idempotency` → one payout, not two.
- A5 status propagation: after FTS processed, vendor-payments `tax_payments.status` is terminal
  and exactly one message lands on `…accounting-payouts.status-update`.
- A6 topic-name equality gate (static, cheap, catches drift):
  `vendor-payments prod.toml:273` == `accounting-integrations prod.toml:196`, and
  `payouts prod.toml:375-378` maps all four vendor source types to one topic.

**Honest scope statement (why this isn't a misleading demo):** it does *not* prove the
invoice/maker-checker/control-policy surface, ICICI challan generation, OCR, GST, the vendor
portal, or the accounting push to Zoho. It proves one internal-app authorization boundary, one
idempotency contract, one correlation-id write-back and one cross-service topic contract, using
**real vendor-payments code** — three things the current twin cannot assert at all.

### Overlap with unfinished full-Payouts (M6) work

`git -C /Users/rana.singh/rzp-payouts-architecture status --porcelain` (VERIFIED) shows an
in-flight M6 lane, documented in `ENV2_COMPOSE/M6_RUNTIME_CHANGES.md` ("Nothing here was booted").
Staged/modified files that S2P would also touch:

| File | M6 change | S2P collision |
|---|---|---|
| `ENV2_COMPOSE/docker-compose.yml` | +511 lines: 10 new payouts workers, `workflow-engine` service, `batch-sim`, new volumes; 68 → 80 containers | **Direct.** Adding `vendor-payments` + `accounting-integrations` + Cadence + ES containers must rebase on this. Memory budget is already at ≈ +0.5 GiB against ~7.3 GiB free. |
| `ENV2_COMPOSE/config/arena.yaml` | +12 lines (`batch-sim` registration + health gate) | **Direct.** New S2P services register in the same list. |
| `ENV2_COMPOSE/substitutes/workflow-engine/{server,callbacks,identity}.py` | +336 lines: real `callback_details`, merchant-scoped tenancy, 14-char ids, `/health`, `/_arena/*`, default policy binding | **Direct.** Extending config types to `vendor-payment-v2-approval` edits the same three files. **J-TDS-min deliberately avoids the workflow leg**, which removes this collision entirely for the minimal journey. |
| `ENV2_COMPOSE/{.env.arena,config/generate.py,config/templates/base/payouts/arena.toml,scripts/up.sh,secrets/gen-secrets.sh,secrets/materialize.py,preflight/preflight.py}` | `ARENA_WORKFLOW_HOST` default, new `wfe_admin_token` secret, `auth_workflow_payouts` wiring | Indirect: S2P needs new secrets (`auth_vendorpayments_*`, monolith↔VP basic auth) through the same generator. |
| `ENV2_COMPOSE/substitutes/monolith-stub/**` | **not modified by M6** | **No collision.** The three new routes J-TDS-min needs can land independently. |
| `RED_LOOP/m6/`, `RED_LOOP/surface/m6_acceptance.py`, `reports/implementation/m6-journey{s,-coverage}.{json,md}` (untracked) | M6 acceptance surface | An `s2p` profile should be a sibling, not an edit. |

**Recommended sequencing (INFERRED):** land M6 first (it is staged and self-contained), then
build J-TDS-min on top — its only shared-file edits are `docker-compose.yml` and `arena.yaml`
(additive service entries), and `monolith-stub/server.py`, which M6 does not touch.

---

## Appendix — commands that produced the build results

```bash
INV=/private/tmp/claude-502/-Users-rana-singh-rzp-payouts-architecture/97164fe3-.../scratchpad/inv
# 1. restore pruned worktrees from the packfiles (clone root untouched)
cp -R $CLONES/<repo>/.git $INV/src/<repo>.git
printf 'ref: refs/heads/master\n' > $INV/src/<repo>.git/HEAD
printf '[core]\n\trepositoryformatversion = 0\n\tbare = false\n' > $INV/src/<repo>.git/config
git --git-dir=$INV/src/<repo>.git --work-tree=$INV/src/<repo> checkout-index -a -f

# 2. codegen (offline; BSR deps from the local buf cache)
export PATH=$INV/bin:$PATH   # protoc-gen-go, -go-grpc@v1.3.0, -grpc-gateway@v2.11.3, -twirp@v8.1.2+incompatible
BUF=/Users/rana.singh/rzp-payouts-architecture/.local/twin-build-tools/payouts-buf
(cd <buf-root> && $BUF generate)     # openapiv2 plugin removed (docs only)

# 3. build
export GOFLAGS=-mod=readonly GOPRIVATE=github.com/razorpay GOTOOLCHAIN=local
go build ./...            # + `-tags boot` for vendor-experience cmd/{migration,vp_migration}
go mod verify             # all three: "all modules verified"
```

Go toolchain: `go1.26.6 darwin/arm64`. Module cache `/Users/rana.singh/go/pkg/mod`
(razorpay modules pre-populated). Build env matches `reports/implementation/BUILD_PROVENANCE.md`
except `GOPROXY` (`off` there; `https://proxy.golang.org,direct` here, because the public
half of these three repos' dependency sets is not yet in the local cache — a cold-cache
offline build is **not** proven for S2P).
