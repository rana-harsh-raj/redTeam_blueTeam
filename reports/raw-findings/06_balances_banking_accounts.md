# Lane: Balance Sources and Source Accounts — x-balances / banking-accounts / virtual-account

Commits inspected: x-balances @ `1a21c0f9111da14125d839dc0dfe8d980740f207`, banking-accounts @ `c3fc1fe8ae41a528a4a3d7ed1b0ec9dad6f98141`, virtual-account @ `437f39807e1575c3b00b0ef57cdde804a54be5ce` (fully cloned, has `.git`).

## Per-repo summary

### x-balances (`razorpay/x-balances`)
- **Stack**: Go, gRPC (buf/proto-generated `rpc/x/x-balances/*`) + REST gateway (grpc-gateway `.pb.gw.go`), GORM/MySQL, Redis (cache + ratelimiter), SQS queue, OpenTelemetry.
- **Layout**: `cmd/{server,worker,migration}/main.go`; `internal/{balances,sub_balances,balance_fetch,accounts,adminactions,enrichment,events,gateway,job,database}`; `proto/`, `rpc/` (generated).
- **Deployables**: `cmd/server` (gRPC :8080 / HTTP :8081 / internal :8082), `cmd/worker` (SQS-driven job worker: balance-fetch per bank, account activation, payout events, pocworker), `cmd/migration` (DB migrations).
- **Docker/CI**: `build/docker/{Dockerfile.in,Dockerfile.ci,local/Dockerfile,dev/Dockerfile.dev}`; devstack helm-chart-template refs under `.agents/skills`; `.github/workflows/{ci,ci-devstack,e2e,gatekeeper,central_security_checks,rcore-integration,slit-in-process,generate-docs,openai_pr_review}.yml`.
- **Ownership**: `.github/repo_owners.json` — business_unit RazorpayX, sub_group/pod "Business Banking", EM `kumar.ayush@razorpay.com`. No CODEOWNERS file found.
- Young service: single DB migration file (`20250127224555_balance.go`) — consistent with being a newly-built extraction service.

### banking-accounts (`razorpay/banking-accounts`)
- **Stack**: Go, Gin HTTP (no gRPC found), GORM/MySQL, Kafka producer (`pkg/events/kafka_producer.go`), outbox pattern (`internal/config.OutboxConfig`), integrations with Mozart/Zoho/UFH/BVS/Hubspot/Segment/Metro.
- **Layout**: `internal/{bootstrap (route registration), database/{entities,repos,migrations}, applications/onboarding/{rbl,axis,icici,idfc,yesbank,yesbank_pobo,slice}, services, resources, dcs}`; `pkg/{balances,fts,payouts,mozart,api,mob,accountstatements,bvs,...}` (outbound service clients).
- **Deployables**: single `main.go` HTTP service; `deployment/{dev,devserve}/k8s/{deployment,service,ingress,pdb,db}.yaml`, `deployment/dev/docker-compose.yml`.
- **CI**: `.github/workflows/{ci,pr_workflow,pr_approval_workflow,e2e,master_e2e,akto-ci-run,rcore-integration,central_security_checks,jira_and_test_case_workflow,stale}.yaml/.yml`.
- **CODEOWNERS**: `.github/CODEOWNERS` → `* @harshitsidhwa @flanker-23`. Ownership JSON: sub_group/pod "Business Banking", EM `sagar.gupta@razorpay.com`.

### virtual-account (`razorpay/virtual-account`)
- Fully cloned (has `.git`, HEAD `437f398...`). Go, gRPC (`rpc/x/x-balances`-style structure under its own `rpc/`), GORM/MySQL.
- **Layout**: `internal/{account,virtualaccount,receiver,allocator,credit,offlinechallan,migration}`; `internal/integrations/{xbalance,ledger,mozart,asv,apisync,terminal,bankingaccount,dcs,cms,splitz,stork,router,api,paymentmethods}`.
- **Ownership**: sub_group RazorpayX/Business Banking, pod "Co-brand + Onboarding", EM `apurva.shivkumar@razorpay.com`.
- Not deeply explored beyond the balance/ledger integration boundary (out of primary lane focus, included for the cross-reference in Q4/Q10).

---

## Findings

| Claim | Repo | File path + symbol | Evidence excerpt | Confidence | Capability |
|---|---|---|---|---|---|
| x-balances persists a `balance` row per source account (banking/current, pool/shared, sub_balance) with account_number, channel, currency, balance, priority, FTS fund account id | x-balances | `internal/database/model/balance.go:30-76` (`Balance` struct) | `AccountType string ... AccountNumber string ... FTSFundAccountID string` | High | persist |
| x-balances falls back to reading the **API monolith's own `balance` table** directly (via a second DB connection `APIStore`) when a balance_id is not found in its own DB — dual source-of-truth during migration | x-balances | `internal/balances/service.go:43-153` `BalanceService.GetBalanceByID` / `BatchGetBalances`; `internal/database/model/balance.go:192-251` `APIBalance` | `// Try to fetch from API database if not found in primary database` + `"type": "banking", // NEVER RETURN PRIMARY BALANCE"` | High | route / persist |
| x-balances connects directly to the API monolith's MySQL DB (`api_local`/`api_rx`) as a fallback read path, separate from its own `Store` | x-balances | `config/default.toml:60-75` `[APIStore]` block; `internal/config/config.go:34` `APIStore storage.Config` | `[APIStore.Sql] ... Name = "api_local"` | High | persist |
| For `sub_balance` and `shared` (Lite VA) account types, the **real-time balance number is fetched live from Ledger**, not read from x-balances' own stored `balance` column | x-balances | `internal/enrichment/enricher.go:35-98,184-223` `BalanceEnricher.EnrichBatch`/`applyLedgerBatch` | `// 2. Ledger balance (sub_balance + shared): fetches the real-time balance from Ledger.` ; `ledgerBal := int64(parsed)` overwrites `balances[i].Balance` | High | authorize/observe |
| x-balances exposes gRPC+REST `BalancesAPI`: `GET /v1/balances/{id}`, `POST /v1/balances` (batch) | x-balances | `internal/api_controller/balances/server/server.go` (`GetBalanceById`, `BatchGetBalances`); `rpc/x/x-balances/balance/v1/balances_api.swagger.json` | paths `"/v1/balances"`, `"/v1/balances/{id}"` | High | observe |
| x-balances exposes `POST /v1/accounts` and `POST /v1/balance/create` (called by banking-accounts to create a new balance row on account activation) | x-balances | `rpc/x/x-balances/accounts/v1/accounts_api.swagger.json` | paths `"/v1/accounts"`, `"/v1/balance/create"` | High | persist |
| x-balances exposes sub_balance CRUD + limits (`/v1/sub_balance`, `/v1/sub_balance/{id}`, `/v1/sub_balance/create_limit`, `/v1/sub_balance/limits`) used for sub-merchant balances funded by a master merchant | x-balances | `rpc/x/x-balances/sub_balance/v1/sub_balance_api.swagger.json`; `internal/database/model/balance.go:21-24` `AccountTypeSubBalance` | swagger paths list | High | persist/authorize |
| x-balances runs periodic per-bank balance-refresh workers (RBL/ICICI/IDFC/Axis/Yesbank/Slice) that poll bank balance via Mozart (banking gateway abstraction), after fetching bank creds from banking-accounts | x-balances | `internal/job/balanceFetch/{rbl,icici,idfc,axis,yesbank,slice}/balance_fetch.go`; `internal/balance_fetch/service.go:53-62` (`Bas banking_accounts.Service`, `Mozart mozartService.Service`); `internal/gateway/banking_accounts/interfaces.go` (`FetchBankingCreds`) | `Bas banking_accounts.Service` + `Mozart mozartService.Service` fields | High | observe/route |
| x-balances also exposes cron-triggered fetch endpoints and stale-entry reporting | x-balances | `rpc/x/x-balances/balance_fetch/v1/balance_fetch_api.swagger.json` | paths `"/v1/balance/fetch"`, `"/v1/cron/balance/{channel}/fetch"`, `"/v1/cron/balance/stale-entries/report"` | High | route/observe |
| x-balances consumes payout-created events off an SQS queue to bump per-merchant transacting-count (used to prioritize which merchants get more frequent balance refresh) | x-balances | `internal/job/payout_events/payout_events.go` (`Job.Constructor`, job name `"payout_events_worker"`); `internal/events/payout/service/service.go:34-87` `ProcessEvent`/`incrementCountForBalance` (Redis `ZIncrBy`) | `key := fmt.Sprintf(events.TransactingMerchantCacheKeyPrefix, ...)` | High | observe |
| x-balances gates a "refresh events published" behavior on a per-merchant DCS flag `in_flight_reservation_enabled` — same flag payouts uses for in-flight balance reservation | x-balances | `internal/balance_fetch/service.go:63-73` (`dcs internaldcs.Service`, `reservationFlagCache`) | `// the same DCS field payouts gates the feature on -- so refresh events are published only for merchants that actually use them` | High | route |
| x-balances admin-actions endpoint executes raw SQL queries against its DB for a list of merchant IDs, gated by action_type/priority — used for data fixes | x-balances | `internal/api_controller/admin_actions/server.go:47-83`; `rpc/x/x-balances/admin_actions/v1/admin_actions.swagger.json` path `/v1/admin/admin-actions` | `Queries: req.SqlQueries, UseTransaction: req.UseTransaction` | High | persist (admin/mutation) |
| x-balances outbound clients and auth: APIService (API monolith, basic auth `rzp_live`), PayoutService (basic auth `x_balances`, used for non-terminal-payout check during SubBalance create), LedgerConfig (`https://ledger-live.dev.razorpay.in`, basic auth `x_balances_key`, "mandatory dependency"), AccountServiceConfig/ASV (gRPC `asv.grpc.conc.dev.razorpay.in:443`), BankingAccountsConfig, MozartConfig, DCSConfig | x-balances | `config/default.toml:107-201`; `internal/config/config.go:44-176` | see config excerpts below | High | route |
| banking-accounts owns onboarding/activation for current accounts across RBL/ICICI/Axis/IDFC/Yesbank(+POBO)/Slice, storing `account_number`, `balance_id`, `fts_fund_account_id`, encrypted `credentials` JSON per banking account | banking-accounts | `internal/database/entities/banking_account.go:12-32` `BankingAccount` struct | `AccountNumber ... BalanceID string ... FtsFundAccountID string ... Credentials datatypes.JSON` | High | persist |
| banking-accounts exposes a **data-fix-only, CAUTION-flagged** admin route to overwrite the `balance_id` link on a banking account | banking-accounts | `internal/bootstrap/routes.go:283-303` | `// CAUTION: Use this route only as a last resort for data fixes` `r.PATCH("/banking_account/:banking_account_id/balance_id", ...)` | High | persist (admin) |
| banking-accounts has a **new** direct x-balances client (`pkg/balances`) that calls `POST v1/balance/create`, used only by the **Slice** bank onboarding path today | banking-accounts | `pkg/balances/client.go:38-53`; `internal/applications/onboarding/slice/pre_activation.go:163` | `response, err := c.SendRequest(ctx, "POST", "v1/balance/create", nil, request)` | High | persist |
| banking-accounts has a **legacy** balance-creation path used by RBL/ICICI/Axis/IDFC/Yesbank(+POBO) that calls the **API monolith** (not x-balances) at activation time, via `POST v1/bas/merchant/{merchantId}/banking_accounts` | banking-accounts | `internal/applications/onboarding/balance_creation.go:29-49` `CreateBalanceAndRelatedEntities`; called from `internal/applications/onboarding/{idfc,ICICI,yesbank,axis,rbl,yesbank_pobo}/pre_activation.go` | `balanceCreateurl := fmt.Sprintf("v1/bas/merchant/%s/banking_accounts", merchantId)` | High | persist / route |
| → This is direct evidence of a **dual write path / in-progress migration**: only Slice writes new balances into x-balances directly; every other legacy bank still creates the balance record via the API monolith's `bas` (banking-account-service) module | banking-accounts | (synthesis of the two rows above) | n/a | Medium (inference from code, not confirmed by design doc) | route |
| banking-accounts emits onboarding-application state-machine / enrichment events onto a datalake Kafka topic (not a payout-balance event stream) | banking-accounts | `pkg/events/kafka_producer.go`, `pkg/events/events_dto.go:11-19` | `StateChangeEvent`, `EnrichmentCreatedEvent`, `EnrichmentCompleteEvent` etc. | Medium | observe |
| virtual-account resolves the `balance_id` of a banking VA's parent current account by calling x-balances (`ListAccounts`) | virtual-account | `internal/integrations/xbalance/interface.go:12-24` `GetBalanceIDByAccountNumber`; `pkg/clients/xbalance` | `// GetBalanceIDByAccountNumber resolves balance_id for a banking VA's parent account.` | High | route |
| virtual-account separately integrates with Ledger for journal entries (VA-linked ledger balance), independent of x-balances | virtual-account | `internal/integrations/ledger/interface.go` | `GetJournalEntry(ctx, journalID) (common.IJournal, ...)` | Medium | observe |
| **Payouts selects the source account/balance for a payout by calling x-balances' `ListAccounts` (Merchant Account Routing / MAR)**, not by reading a local/API-DB balance table, for the live code path | payouts (cross-ref) | `internal/app/balance/core.go:49-90` `FetchActiveAccountsFromXBalance`; `internal/app/balance/selection.go:58` (only call site of the two fetch functions besides `metaSummary.go:40`) | `xBalancesClient := provider.GetXBalanceClient(ctx)` … `xBalancesResponse, err := xBalancesClient.ListAccounts(ctx, xBalancesQuery)` | High | route |
| A legacy `FetchActiveAccountsFromApi` (reads payouts' own DB, `GetBankingBalanceByMerchantIDFromAPI`) exists in the same interface but has **no live call site** outside its own definition and the interface — i.e., dead/fallback code, not the active path | payouts (cross-ref) | `internal/app/balance/core.go:92-120`; `internal/app/interfaces/IBalance.go:40-41` | grep shows only definition + interface decl, no caller | Medium | route |
| **Actual balance-sufficiency checks at payout-processing time go through Ledger** (`InsufficientBalance` handling), not through x-balances' stored `balance` column | payouts (cross-ref) | `internal/app/fundAccountValidation/fav_ledger.go`, `internal/app/payouts/processor/payoutViaLedgerService.go`, `pkg/ledger/client.go`, `pkg/ledger/error.go` (all contain `InsufficientBalance`) | filenames + grep hits | Medium (not read in full — in FTS/payouts lane, flagged for owning lane to confirm) | authorize/reject |
| payouts also directly uses x-balances in `fundManagement/core.go` to fetch the direct account for a channel and to fetch Lite VA balance from Ledger | payouts (cross-ref) | `internal/app/fundManagement/core.go:54,617-660` `xBalancesClient xbalances.IClient`, `fetchDirectAccount`, `fetchLiteVABalance` | `resp, xErr := c.xBalancesClient.ListAccounts(ctx, xbalances.ListAccountsQuery{...})` | High | route |
| No repo in this lane's tree contains `queue_if_low_balance`/`QueueIfLowBalance` — that flag/logic likely lives in payouts under a different name (e.g. in-flight reservation / queueing strategy) | x-balances, banking-accounts, virtual-account | n/a (negative grep result across whole checkout) | grep of `queue_if_low_balance` across all cloned repos hit only unrelated repos (payout-links, xperience, vendor-payments, charge-collections) — not this triad | Medium | n/a |

---

## Answer to Q10 — balance source-of-truth for payout validation, today

Based on this lane's evidence (payouts repo cross-referenced, not exhaustively reviewed — that's the payouts lane's job):

1. **Account/balance *selection* for a payout** (which `account_number`/`balance_id` a payout should draw from) is done by calling **x-balances' `ListAccounts` API** (`payouts/internal/app/balance/core.go:FetchActiveAccountsFromXBalance`, the only live call site). The parallel `FetchActiveAccountsFromApi` method (reading payouts' own/API DB copy) exists but has no active caller found — consistent with x-balances being the newer selection-time source of truth, with the API-DB path kept as unused/legacy code.
2. **x-balances itself is not a single source of truth** for the `balance` number: 
   - For `banking`/direct current-account balances, it stores its own polled value (refreshed by per-bank Mozart-polling workers) **and** falls back to reading the API monolith's `balance` table directly via a second DB connection (`APIStore`) when its own row is missing — i.e., x-balances is mid-migration off the legacy API monolith `balance` table, and both stores are live today.
   - For `sub_balance` and `shared` (Lite VA) account types, x-balances does **not** trust its own stored number — it overwrites it in real time from **Ledger** (`internal/enrichment/enricher.go`).
3. **Actual balance-sufficiency validation at payout-processing/debit time appears to run through Ledger** (`fav_ledger.go`, `payoutViaLedgerService.go`, `ledger/client.go` all reference `InsufficientBalance`), not through x-balances' stored `balance` column — though this file content was not read in full in this lane (belongs to the payouts/FTS lane) and should be confirmed there.
4. **banking-accounts is only mid-migration itself**: new bank (Slice) onboarding creates the x-balances balance row directly via `pkg/balances` client; all older/legacy banks (RBL, ICICI, Axis, IDFC, Yesbank, Yesbank POBO) still create the balance record via a call **into the API monolith** (`POST v1/bas/merchant/{id}/banking_accounts`), not into x-balances.

**Conclusion**: there is a live three-way dual/triple source-of-truth situation during an in-flight migration — API monolith `balance` table (legacy, still written by most banks' onboarding and read as x-balances' fallback), x-balances' own `balance` table (new system of record for account selection/routing, but with unreliable freshness for banking-type balances since it's polling-based), and Ledger (real-time balance for sub_balance/shared/Lite VA, and apparently the actual authority for balance-sufficiency checks in payouts). This is an inference from code structure across the three repos in this lane plus grep-level cross-reference into payouts — it should be validated against a design doc/ADR (x-balances README links a tech-spec Google Doc, not fetched: `https://docs.google.com/document/d/1WdVTb3ocj19oQRPH-DOTt__L-XYqsBSOd3AiOpGb4uM`) and against the payouts/FTS lane's own findings.

---

## Route tables

### x-balances (gRPC service + REST gateway paths, from swagger)
| Path | Methods | Controller | Purpose |
|---|---|---|---|
| `/v1/balances`, `/v1/balances/{id}` | GET | `internal/api_controller/balances/server` | Get/BatchGet balance by id(s) |
| `/v1/accounts` | POST | `internal/api_controller/accounts/server` | Create/list accounts |
| `/v1/balance/create` | POST | `internal/api_controller/accounts/server` | Create a new balance (called by banking-accounts for Slice) |
| `/v1/sub_balance`, `/v1/sub_balance/{id}` | GET/POST | `internal/api_controller/sub_balances/server` | Sub-balance CRUD |
| `/v1/sub_balance/create_limit`, `/v1/sub_balance/limits` | POST/GET | `internal/api_controller/sub_balances/server` | Sub-balance spend limits |
| `/v1/admin/admin-actions` | POST | `internal/api_controller/admin_actions` | Raw-SQL / priority / feature-flag admin mutations |
| `/v1/balance/fetch` | POST | `internal/api_controller/balance_fetch/server` | On-demand balance fetch |
| `/v1/cron/balance/{channel}/fetch`, `/v1/cron/balance/stale-entries/report` | POST/GET | `internal/api_controller/balance_fetch/server` | Cron-triggered bank polling + staleness report |

### banking-accounts (Gin routes, selected — full list is large, see `internal/bootstrap/*.go`)
| Path | Method | File | Purpose |
|---|---|---|---|
| `/banking_account/:banking_account_id/balance_id` | PATCH | `internal/bootstrap/routes.go:283` | **Admin data-fix**: overwrite balance_id link |
| `/banking_account/:banking_account_id/update_details` | POST | `internal/bootstrap/routes.go:199` | Update account details |
| `/banking_account/:banking_account_id/{idfc,ybl}/activate_upi` | POST | `internal/bootstrap/routes.go:224,249` | UPI activation per bank |
| `/business/:business_id/applications/:application_id/activate_account` | POST | `internal/bootstrap/admin_routes.go:81` | Admin: activate account (onboarding → active, triggers balance creation) |
| `/rbl/credentials`, `/rbl/.../credentials/download`, `/rbl/migrate/credentials` | POST/PUT/GET | `internal/bootstrap/rbl_credential_routes.go` | RBL credential metadata management |
| `/apply`, `/applications`, `/applications/:application_id` | POST/GET/PATCH | `internal/bootstrap/routes.go` | Onboarding application CRUD |
| `/document`, `/person`, `/business` | POST/GET/PATCH/DELETE | `internal/bootstrap/*_routes.go` | Onboarding entity CRUD |

---

## Outbound client tables

### x-balances → downstream
| Client | Config key | Base URL / Host (dev) | Auth | Timeout/Retry |
|---|---|---|---|---|
| API monolith (HTTP) | `APIService` | `https://api-web.dev.razorpay.in` | Basic `rzp_live` / env pwd | 30s timeout, Hystrix circuit breaker (100 concurrent, 20 vol threshold, 50% err, 30s CB timeout) |
| API monolith DB (direct MySQL, fallback read) | `APIStore` | `api_local` DB via TCP:3306 | DB user `api_rx` | n/a (GORM pool) |
| Payout Service (HTTP) | `PayoutService` | `https://payouts.dev.razorpay.in` | Basic `x_balances` / env pwd | same Hystrix pattern as APIService; used for non-terminal-payouts check during SubBalance CREATE |
| Ledger (HTTP) | `LedgerConfig` | `https://ledger-live.dev.razorpay.in` | Basic `x_balances_key` / env pwd | 10s timeout, 60s keepalive, "mandatory dependency — server fails to start if misconfigured" |
| Account Service / ASV (gRPC) | `AccountServiceConfig` | `asv.grpc.conc.dev.razorpay.in:443` | Basic `xBalances` / env pwd | gRPC + local cache (15s eviction, 8MB, 16 shards) |
| Banking Accounts (HTTP) | `BankingAccountsConfig` | (BaseURL in config, not printed) | Basic, `Server.Auth.BankingAccounts` is *inbound* mirror | `FetchBankingCreds(accountNumber, merchantID, channelType)` |
| Mozart (banking gateway abstraction, HTTP) | `MozartConfig` | (BaseURL in config) | `MozartConfig.Auth` | `MakeRequest` — used to poll real bank balance per channel |
| DCS (feature flags) | `DCSConfig` | n/a (env-based) | Basic, env user/pwd | Used for `in_flight_reservation_enabled` per-merchant flag |

### banking-accounts → downstream
| Client | Config type | Purpose |
|---|---|---|
| x-balances (new) | `pkg/balances` / `BalanceConfig` | `CreateBalance` → `POST v1/balance/create` (Slice onboarding only) |
| API monolith | `pkg/api` / `ApiConfig` | Legacy `CreateBalanceAndRelatedEntities` → `POST v1/bas/merchant/{id}/banking_accounts` (RBL/ICICI/Axis/IDFC/Yesbank/Yesbank POBO) |
| Mozart | `pkg/mozart` / `MozartConfig` | Banking gateway calls (account opening, statement, etc.) |
| Dark Mozart | `DarkMozartConfig` | Shadow/staging gateway calls |
| FTS | `pkg/fts` / `FtsConfig` | Fund transfer system integration |
| Payouts | `pkg/payouts` / `PayoutsConfig` | Payouts integration |
| Account Statements | `pkg/accountstatements` / `AccountStatementsConfig` | Statement fetch |
| BVS, UFH, Zoho, Hubspot, Segment, Metro | respective `pkg/*` | KYC/document verification, ticketing, CRM, analytics, topic routing |

### virtual-account → downstream (from `internal/integrations/*`)
| Client | Purpose |
|---|---|
| `internal/integrations/xbalance` | Resolve `balance_id` for VA's parent CA (`GetBalanceIDByAccountNumber`, `ListAccounts`) |
| `internal/integrations/ledger` | Journal entry lookups |
| `internal/integrations/mozart`, `bankingaccount`, `asv`, `apisync`, `terminal`, `dcs`, `cms`, `splitz`, `stork`, `router`, `api`, `paymentmethods` | Not deeply reviewed in this lane |

---

## Event tables

| Producer | Transport | Topic/Queue | Consumer | Payload | Purpose |
|---|---|---|---|---|---|
| payouts (inferred) | SQS | (queue prefix `QUEUE_SQS_PREFIX`) | x-balances `cmd/worker` `job/payout_events` | `events.EventInput{BalanceID}` | Bump transacting-merchant count in Redis → prioritize balance refresh for active merchants |
| x-balances `job/account_activation` | SQS (same queue infra) | — | x-balances worker | account activation event | Trigger post-activation balance bookkeeping |
| banking-accounts | Kafka (`pkg/events/kafka_producer.go`) | topic from `EventsConfig.KafkaProducerConfig.Topic` | Datalake (analytics), not payouts/x-balances | `DataLakeEvent{STATE_CHANGE_EVENT, ENRICHMENT_*}` | Onboarding application state-machine analytics events — not a payout-balance signal |

No Kafka topics were found produced/consumed by x-balances itself (it uses SQS + `pkg/worker/queue`, not Kafka) — outbox pattern config (`OutboxConfig`) exists in banking-accounts, not confirmed wired to a payout-relevant topic in this pass.

---

## Worker / cron / admin tables

| Repo | Worker/cron | File | Trigger | Effect |
|---|---|---|---|---|
| x-balances | Per-bank balance fetch (RBL/ICICI/IDFC/Axis/Yesbank/Slice) | `internal/job/balanceFetch/{bank}/balance_fetch.go` | SQS worker job, strategy configurable (`last_fetched_at` / `last_attempted_at` via `[BalanceFetch] FetchStrategy`) | Polls bank balance via Mozart, updates `balance` row |
| x-balances | `job/account_activation` | `internal/job/account_activation/account_activation.go` | SQS event | Post-activation balance setup |
| x-balances | `job/payout_events` | `internal/job/payout_events/payout_events.go` | SQS event (`payout_events_worker`) | Increment transacting-merchant cache |
| x-balances | Cron balance fetch/stale report | `/v1/cron/balance/{channel}/fetch`, `/v1/cron/balance/stale-entries/report` | External cron caller (HTTP) | Same fetch pipeline, on-demand |
| x-balances | `admin_actions` API | `internal/api_controller/admin_actions/server.go` | Internal HTTP, `Server.Auth.Admin` basic auth | Raw SQL execution, priority change, feature toggle, per-merchant balance_id ops |
| banking-accounts | Admin routes (`/business/:id/applications/.../activate_account`, `apply`, `submit_account_linking_details`, `update_merchant_id`, `tokenize_via_api`, `bulk_assign_account_manager`) | `internal/bootstrap/admin_routes.go` | Internal HTTP, admin auth middleware | Onboarding lifecycle admin actions |
| banking-accounts | Data-fix routes (`applications/update_sub_status`, `applications/:id/purge`, `.../balance_id` PATCH) | `internal/bootstrap/{data_fix_routes,routes}.go` | Internal HTTP, CAUTION-flagged | Manual data correction, incl. balance_id relink |

---

## Datastore tables

| Repo | Store | Config | Key tables/entities |
|---|---|---|---|
| x-balances | MySQL (own) | `[Store]` `rx_balances_local` / user `rx_balances_rw` | `balance` (model `Balance`), `sub_balance`, `sub_balance_limit` |
| x-balances | MySQL (API monolith, read-only fallback) | `[APIStore]` `api_local` / user `api_rx` | `balance` (mapped via `APIBalance` struct — Type/OnHold/Credits/FeeCredits/RefundCredits/LockedBalance fields specific to API DB schema) |
| x-balances | Redis | `[Cache]`, `[RateLimiter]` | Transacting-merchant sorted sets, rate limiter counters |
| banking-accounts | MySQL (own, GORM) | not fully dumped | `banking_account` (AccountNumber, Status, AccountType, PartnerBank, BalanceID, FtsFundAccountID, Credentials JSON, BeneficiaryDetails JSON), `banking_account_application`, `banking_account_signatory`, `banking_account_account_manager`, `rbl_credentials`, `partner_bank_applications`, `application_status_log`, `person`, `business`, `document`, `comments` |
| virtual-account | MySQL (own) | not dumped | account/virtualaccount/receiver/allocator/credit/offlinechallan models (not read in detail — out of lane depth) |

Migrations: x-balances has exactly one migration (`internal/database/migrations/20250127224555_balance.go`) — confirms a young/recently-bootstrapped schema. banking-accounts and virtual-account migrations not enumerated in this pass (large dirs, time-boxed).

---

## Config / feature-flag tables

| Flag/config | Repo | Where read | Effect |
|---|---|---|---|
| `in_flight_reservation_enabled` (per-merchant, via DCS) | x-balances | `internal/balance_fetch/service.go` (`reservationFlagCache`, `dcs internaldcs.Service`) | Gates whether balance-refresh events are published for a merchant — mirrors the flag payouts uses for in-flight reservation |
| `[BalanceFetch].FetchStrategy` (`last_fetched_at` \| `last_attempted_at`) | x-balances | `internal/config/config.go` `BalanceFetch` | Controls which merchants are prioritized for bank-balance polling |
| `PayoutsBlockedOnLite` (merchant feature) | payouts (cross-ref) | `payouts/internal/app/balance/core.go:66` | If set, payouts' x-balances `ListAccounts` query is filtered to `Direct` accounts only (Lite/shared balances excluded from routing) |
| `Server.Auth.{API,Cron,Admin,PS,BankingAccounts,Validx,VirtualAccount}` | x-balances | `config/default.toml` | Per-caller inbound basic-auth credential sets — implies distinct trusted callers: API monolith, cron, admin tooling, Payout Service, banking-accounts, ValidX, virtual-account |

The `Server.Auth.*` block is itself useful evidence for "who calls x-balances": there are dedicated inbound auth credentials for **API (monolith)**, **Cron**, **Admin**, **PS (Payout Service)**, **BankingAccounts**, **Validx**, and **VirtualAccount** — i.e. x-balances is called by the API monolith, a cron system, admin tooling, the Payout Service, banking-accounts, ValidX, and virtual-account.

---

## Dependencies (this lane's repos, summarized)

- x-balances depends on: API monolith (HTTP + direct DB read), Payout Service (HTTP), Ledger (HTTP, mandatory), ASV/Account Service (gRPC), banking-accounts (HTTP, creds fetch), Mozart (HTTP, bank gateway), DCS (feature flags), Redis, SQS, MySQL (own + API monolith).
- banking-accounts depends on: x-balances (HTTP, new path — Slice only), API monolith (HTTP, legacy balance-creation path for all other banks), Mozart/Dark Mozart, FTS, Payouts, Account Statements, BVS, UFH, Zoho, Hubspot, Segment, Metro, Kafka, MySQL.
- virtual-account depends on: x-balances (HTTP, balance_id resolution), Ledger, Mozart, ASV, API monolith sync, Terminal, DCS, CMS, Splitz, Stork, Router, Payment Methods, MySQL.
- Reverse: payouts depends on x-balances (`pkg/xbalances` client, `ListAccounts`) for source-account selection (MAR) and on Ledger for balance-sufficiency validation.

---

## Unresolved questions (for other lanes / follow-up)

1. Full mechanics of `queue_if_low_balance` / in-flight-reservation balance sufficiency check — file names (`fav_ledger.go`, `payoutViaLedgerService.go`, `processor/queueingstrategy/strategy_test.go`) were located via grep but not read in full; this belongs in the payouts/FTS lane's report — confirm whether Ledger, x-balances, or payouts' own DB is the actual sufficiency-check source.
2. Whether the legacy `FetchActiveAccountsFromApi` path in payouts is truly dead code or reachable via a feature flag not caught by static grep (e.g. reflection/config-driven dispatch).
3. Exact BaseURL values for `BankingAccountsConfig`/`MozartConfig` in x-balances `config/default.toml` were declared as structs but the URL strings themselves weren't located in the grepped config section — worth a targeted look at `config/prod.toml`/`stage.toml` if precise endpoints matter.
4. banking-accounts' full migrations/entities list and Kafka topic name(s) (`EventsConfig.KafkaProducerConfig.Topic` value) not enumerated — time-boxed out of this pass.
5. virtual-account's own balance-relevant tables/migrations and its `internal/allocator`/`internal/receiver` logic (VA-to-payout fund flow) not explored in depth — flagged for a dedicated VA lane if one exists.
6. The x-balances tech-spec Google Doc linked in its README (`https://docs.google.com/document/d/1WdVTb3ocj19oQRPH-DOTt__L-XYqsBSOd3AiOpGb4uM`) likely has the authoritative migration plan/target architecture — not fetched (external network access excluded by task scope).
