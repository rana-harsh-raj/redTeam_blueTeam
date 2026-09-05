# Lane 06 — FTS routing/status/Mozart — fidelity verification

Scope per `00_LANE_BRIEF.md`. Repos read: `fts` (commit as cloned, matches prior `03_fts.md` baseline
`2a09e763`), `mozart`. Twin: `ENV2_COMPOSE/`. Prior reports treated as hypotheses and checked against
source; corrections recorded in §3. All line numbers read directly unless marked INFERRED/UNKNOWN.

## 1. Scope and sources read

- `fts/internal/routing/router/route_list.go`, `internal/routing/middleware/auth.go`,
  `internal/controllers/{transfer.go,validation.go,attempt_bulk_action.go,routing.go,health.go,alert_manager.go}`
- `fts/internal/transfer/{service.go (full CreateNewTransfer/createTransfer/FireTransferStatusWebhook/GetMapFromTransfer
  region 322-1360, 1791-1930), transfer.go, transfer_meta.go, state_machine.go, attempt.go,
  attempts_unique_keys.go, attempt_processor.go (grepped, selected regions), transfer_processor.go (grepped)}`
- `fts/internal/account/{source_account_selector.go (full read to line ~1210, function inventory to 1829),
  service.go (targeted regions: fetchAllSourceAccountMappingForMerchant, repo query builders,
  selectDirectSourceAccountByMerchantRoutingRule/selectDirectSourceAccByDefaultRoutingRule,
  filterSourceAccountMappingByChannelHealth), repo.go}`
- `fts/internal/channel/{service.go (GetChannelHealthMapForTransfer* family), cron.go, test_transactions.go,
  channel_information_status.go}`
- `fts/internal/downtime/notification.go`, `fts/internal/downtimeV2/publisher.go`
- `fts/internal/providers/mozart/{provider.go (grepped), request.go, response.go, error_code.go
  (structure + ~140-entry map spot-checked), executor.go (grepped)}`, `fts/internal/config/mozart.go`
- `fts/internal/migrations/{00005_transfers,00006_attempts,00007_source_accounts,00011_source_account_mappings,
  00017_preferred_routing_weights,00018_account_type_mappings,00020_direct_account_routing_rules,
  00022_channel_information_status,00045_transfer_meta}_table_create.go`
- `fts/config/env.default.toml` (queue/worker map §60-166, mozart §761-816, RBL IMPS/UPI/RTGS/NEFT
  §953-1310, cron §9370-9515, gateway_error_handling §8399-8436), `fts/config/env.prod-live.toml` (spot-checked,
  no RBL threshold overrides found)
- `mozart/app/mock/mappings.go` (full, 156 lines), `mozart/app/testdata/fts/**` (directory census),
  `mozart/go.mod` (private deps)
- Twin: `ENV2_COMPOSE/config/templates/base/fts/env.arena.toml` (full), `ENV2_COMPOSE/seeds/s4/fts.sql` (full),
  `ENV2_COMPOSE/substitutes/mozart-sim/server.py` (full), `ENV2_COMPOSE/docker-compose.yml` (fts-* and
  mozart-* service blocks), `reports/raw-findings/31_env2_bringup_notes.md` (FTS/mozart sections)
- Prior reports re-verified: `reports/raw-findings/03_fts.md`, `13_fts_payouts_status_path.md`,
  `25_stork_mozart.md` §B/§C, `ARCHITECTURE_DELTA.md` C57

Not re-read line-by-line (time-boxed, flagged where it matters): `internal/account/source_account_selector.go`
lines 1210-1829 (NEFT-24x7/backup-detail/congestion internals — function inventory only), full
`attempt_processor.go` (6000+ lines — targeted greps only), full `error_code.go` (818 lines — ~140-entry map
spot-checked, not every entry enumerated), `mozart/app/routes/available_routes.go` (2955 lines — gateway/version
inventory taken from prior finding 25, not re-walked).

---

## 2. Production behaviour

### 2.1 `POST /v1/transfer` — auth, request, response

**Route**: `fts/internal/routing/router/route_list.go:148-159` — group `/v1/transfer`, `POST ""` →
`controllers.Transfer.Post`. Auth middleware on the group: `middleware.BasicAuth(...)` with allowed apps
`API, SETTLEMENT, VALIDX, PS, WALLET` (route_list.go, group-level `middleware` slice; confirmed same group
also carries `PUT ""`→`Transfer.Put` no-op, `GET ""`→`Transfer.Get`).

**Auth mechanism** (`fts/internal/routing/middleware/auth.go:16-96`):
- `ctx.Request.BasicAuth()` (HTTP Basic only, no JWT/mTLS at Gin layer).
- App derivation from username: `getAppNameFromUsername` = `strings.SplitN(userName, "_", 2)[0]`
  (auth.go:93-96) — e.g. username `ps_payouts` → app `ps`. `isAuthorized` lower-cases the route's
  configured app key and compares (auth.go:83-89).
- Credential lookup: `getUserCredentialsForRoute` reflects the route-app string onto
  `config.GetConfig().Users` struct field by name (`reflect.ValueOf(...).FieldByName(routeApp)`,
  auth.go:63-70) → `config.Credential{Username,Password}`, sourced from `[users.<app>]` TOML
  (`USERS_<APP>_USERNAME/PASSWORD` env vars, per `03_fts.md` §2, not re-read this pass).
  Confirmed by twin bringup: fts derives app from username prefix before first `_`
  (`31_env2_bringup_notes.md:68`; PS identity must be `ps_<anything>`).
- Comparison: constant-time `secureCompare` via `crypto/subtle` (auth.go:73-80).
- 401 on failure: `respondWithUnauthorizedStatus`, sets `WWW-Authenticate: Basic` header (auth.go:48-59).

**Request struct** — full field list, `fts/internal/controllers/validation.go`:

`transferCreateRequestValidation` (validation.go:230-241) — top level:
| Field | Required | Type/validator |
|---|---|---|
| `product` | Yes | `product.IsValidProduct` |
| `merchant_id` | Yes | `validator.IsValidRZPID` |
| `transfer` | Yes (struct) | see `transferRequestValidation` below |
| `account` | Yes (struct) | see below |
| `2fa` | No (struct) | `otp` required, `validator.IsOTPString` |
| `merchant_category` | No (struct) | `category`,`sub_category`,`mcc` (basic string), `onboarded_time` (integer) |

`transferRequestValidation` (validation.go:254-270), nested under `transfer`:
| Field | Required | Type/validator |
|---|---|---|
| `amount` | Yes | integer, positive |
| `preferred_mode` | No | one of `channel.GetAllSupportedModes()` (IMPS/NEFT/RTGS/IFT/UPI/CT/DUITNOW/IBG per DDL enum) |
| `source_id` | Yes | non-empty |
| `source_type` | Yes | non-empty |
| `source_account_id` | forbidden | must be empty (`validator.IsEmpty`) — cannot be client-supplied |
| `channel` (top common attr) | forbidden | must be empty |
| `transfer_by` | No | valid timestamp |
| `mode` | No | one of supported modes |
| `preferred_channel` | No | `channel.IsValidChannel` |
| `preferred_source_account_id` | No | integer — **presence marks the transfer DIRECT** (confirmed in twin bringup notes: `fts/internal/transfer/service.go:549`, not independently re-read this pass but corroborated by `metricsForTransferCreated` at service.go:549-552 using presence of `AttributePreferredSourceAccountID` to classify `TypeDirect` vs `TypePool`) |
| `narration` | No | alpha string |
| `initiate_at` | No | integer (epoch; future-dated allowed per DDL comment) |
| `is_batch` | No | bool string |
| `request_meta` | No | map (stored in `transfers.request_meta` JSON column — "contact_notes/payout_notes for OPGSP", DDL comment `00005:44`) |

`account` (validation.go:155-174), a `OneOf` discriminated union — exactly one of:
`bank_account | vpa | card | non_saved_card | wallet | fund_account_id` (integer). Sub-validators
(`bankAccountCreateRequestValidation`, `vpaCreateRequestValidation`, etc.) each carry their own required
fields (bank account: `account_type∈{saving,current,nodal}`, `ifsc_code` valid IFSC, `account_number`
required, beneficiary name/city/state/country/mobile/email/address with length/format constraints —
validation.go:33-56).

**`source_id`/`source_type` uniqueness**: enforced at two layers —
1. DB: `UNIQUE KEY unique_source (source_type, source_id)` on `transfers`
   (`fts/internal/migrations/00005_transfers_table_create.go:62`).
2. App: `CreateNewTransfer` (`internal/transfer/service.go:322-436`) takes a distributed mutex
   `mutex.Provider.AcquireAndRelease(ctx, MutexFundTransferCreateResource(source_type,source_id), ...)`
   (service.go:354-380), then `repo.FindIfExist(...,{source_id,source_type})` (service.go:362-365); if found,
   **no new row is created** — `exist=true` is returned to the controller, which responds **HTTP 200** (not
   201) with the existing transfer's `id/fund_account_id/status`
   (`internal/controllers/transfer.go:139-146`, `httpStatus=http.StatusOK` at line 141 when `exist`).
   Special case: if the existing transfer is `USER_PENDING` and the retry payload carries `otp`,
   `HandleUserActionForTransfer` re-triggers processing (service.go:367-373, 391-397, 455-478) instead of
   creating a duplicate.

**`transfer_meta` fields** (`fts/internal/transfer/transfer_meta.go:9-17`, table `transfer_meta`,
migration `00045_transfer_meta_table_create.go:14-27`):
| Column | Type | Constraint |
|---|---|---|
| `transfer_id` | int(11) | required, FK-by-convention to `transfers.id` (no DB FK constraint) |
| `request_id` | varchar(255) | required, alphanumeric |
| `origin_service` | varchar(255) | required, one of `"API"`/`"payouts"` only (transfer_meta.go:29-34, `validator.IsIn(false, APIService, PayoutsService)`) |
| `source_id` | varchar(255) | required, alphanumeric |

`origin_service` is derived from the inbound `X-Origin` HTTP header captured into gin context
(`extraTransferMetaFromCtx`, service.go:1889-1910): reads `ctx.Get(constants.XOrigin)`; if absent/empty,
falls through — `03_fts.md` claims this silently defaults to `"API"` (constants.APIAuth); **this pass did
not re-read the fallback assignment line itself** (only lines 1889-1906 were read, which show the "if
present" branch; the else-branch default was not re-confirmed with a line number this pass — treat the
"defaults to API" claim as **INFERRED, carried from prior finding**, not independently re-verified).
`transfer_meta` row is only written **conditionally**, gated by Splitz experiment
`Splitz.Experiments.CreateTransferMetaRollout` (service.go:399-418) — if the experiment is disabled for the
merchant, **no `transfer_meta` row is created at all** for that transfer.

`preferred_source_account_id`: column on `transfers` (migration 00005:21,
`Transfer.PreferredSourceAccountID` transfer.go:42) — "if provided then source account selection is ignored
and will use this source account" (DDL comment). Drives DIRECT-vs-POOL routing classification in metrics
(service.go:549-552: presence → `account.TypeDirect`, absence → `account.TypePool`).

**Response struct** (`internal/controllers/transfer.go:139-146`):
```json
{"fund_transfer_id": <int64>, "fund_account_id": <int64>, "status": "CREATED"}
```
No `id` key (confirmed by twin bringup note `31_env2_bringup_notes.md:84`: "fts `POST /v1/transfer`
responds `{fund_account_id, fund_transfer_id, status:"CREATED"}` (no `id`)" — matches source exactly, the
map key is `AttributeFundTransferID` not `AttributeID`).

**Other create-adjacent routes on the same group**: `PUT /v1/transfer` is a **no-op stub**
(`Transfer.Put`, transfer.go:169-179 — sets span attributes only, returns `nil, nil, http.StatusOK`, does
nothing else). `GET /v1/transfer` = `Transfer.Get`, validated by `transferStatusRequestValidation`
(validation.go:315-333: `id`,`product`,`status`(one of `transfer.GetAllStatus()`),`merchant_id`,`source_id`,
`source_type`,`fund_account_id`,`preferred_channel`,`count`,`skip`,`from`,`to`,`bank_status_code`,`action`,
`channel`).

CONFIRMED (route table, auth mechanics, request/response structs, DB unique key, mutex/dedupe logic — all
read directly at cited lines). One item (X-Origin-absent default) is INFERRED/carried, not re-read.

### 2.2 Route selection — shared (pool) vs direct (current account)

**Top-level split signal**: presence of `transfers.preferred_source_account_id` on the create request
marks a transfer DIRECT (§2.1); absence routes through the POOL/shared selection path
(`fetchMerchantPreferredDetail`, `internal/account/source_account_selector.go:1031`). This pass did not
locate the single top-level dispatcher function that branches on this flag by name+line (only inferred from
the metrics classification at service.go:549-552 and the existence of two parallel selector families
`fetchMerchantPreferredDetail` vs `FetchPreferredDetailForDirectAccounts[V2]` — **INFERRED** wiring, not a
directly-read call site).

**Shared/pool path** — `fetchMerchantPreferredDetail` (source_account_selector.go:1031-1284, only read to
~1284; full function is 1031-1284+ region continues past what was read):
1. `fetchAllSourceAccountMappingForMerchant(ctx, product, merchantId, "")` (source_account_selector.go:1069)
   → `internal/account/service.go:2795-2816`: builds `conditions = {product, merchant_id, account_type:"POOL"
   (default when accountType arg empty)}`, calls `repo.fetchSourceAccountMappingOrderByPriority`
   (service.go:2814).
2. Query (`internal/account/repo.go:281-289`):
   ```go
   database.Provider.Client(ctx,false).Where("priority IS NOT NULL").Order("priority").Find(models, conditions)
   ```
   i.e. `SELECT * FROM source_account_mappings WHERE priority IS NOT NULL AND product=? AND merchant_id=? AND
   account_type='POOL' ORDER BY priority ASC` (soft-delete scope applied by GORM default, not `Unscoped`).
3. Filters applied in order (source_account_selector.go:1074-1096):
   a. `filterSourceAccountMappingAsPerMerchantCategory` (merchant-category/gaming exclusion, →
      `CodeGamingMerchantError` if it empties the set, line 1085)
   b. `FilterSourceAccountMappingByOnboardingTime` (→ `CodeNewMerchantError` if emptied, line 1089)
   c. `FilterSourceAccountByBeneficiaryBank` — only if `config.GetConfig().Feature.SkipChannelForBene`
      (→ `CodeNoAvailableSourceAccountsForIBKL` if emptied, line 1093)
   d. `filterSourceAccountMappingByAllowedModes(sourceAccountMappings, allowedModes)` (line 1096)
4. Sort: `sortSourceAccountMappingByMode(mappings, modePriority, preferredMode, currentTimeUTC, merchantId)`
   (line 1107-1112) — `modePriority` from `config.GetSupportedModesPriority()[0]`; comment states NEFT
   ordering is instead driven by channel timing, not static priority (lines 1103-1106).
5. First element after sort = `preferredDetail.sourceAccountMapping` (line 1116-1117).
6. **NEFT-only branch** (lines 1119-1192): if selected mapping's mode is NEFT, checks
   `HolidayService.IsRefTimeWithinWorkingWindow(product, mode, channel, mozartIdentifier, now, loc)`
   (line 1120-1126) against the bank's NEFT working-hour config (§2.2.1 below); if outside the regular
   window, evaluates NEFT-24x7 eligibility (`MerchantsService.IsMerchantEligibleForNEFT24x7`,
   `GetSANEFT24x7EligibilityMap`, `FilterSourceAccountMappingsForNEFT24x7`) — not fully read this pass
   (function bodies beyond line 1210 not opened; **INFERRED** shape from names/comments only).
7. **Routing-disabled short-circuit** (lines 1195-1208): if the *first* selected mapping (pre-health-filter)
   has `routing_enabled=0`, it is returned as-is (down) rather than falling back — explicit comment: "if
   there are multiple rules returned but the first rule is down but has routing_enabled=false... we need to
   return the first rule even if we have backup channels available."
8. (Not re-read this pass, INFERRED from function inventory only): subsequent channel-health filtering via
   `filterSourceAccountMappingByChannelHealth(mappings, channelHealthStatusMap)`
   (`internal/account/service.go:4139`, body not opened this pass) and `fetchBackupPreferredDetail`
   (source_account_selector.go:1284) for failover across channels when the primary is unhealthy.

**Direct/current-account path** — two selector functions, confirmed by source read:
- `selectDirectSourceAccountByMerchantRoutingRule` (service.go:5385-5410): conditions
  `{product, merchant_id, mode, channel}` → `repo.fetchDirectAccountRoutingRule(ctx, conditions)`
  (`internal/account/repo.go` — the query itself, `fetchDirectAccountRoutingRule`, was not opened this
  pass; comment at repo.go:291-296 documents the analogous merchant-scoped query as
  `SELECT * FROM source_account_mappings WHERE merchant_id=? AND account_type='DIRECT' AND mode=? AND
  product=? ORDER BY priority ASC, created_at DESC`, `Unscoped()` — i.e. **soft-deleted rows are visible**
  to this specific direct-routing query, unlike the shared-pool query in item 2 above). If a matching rule
  row exists, its `source_account_id` is matched against the caller-supplied `sourceAccounts` slice
  (service.go:5395-5398); if the matched id isn't in that slice, logs
  `TraceInvalidMerchantDirectAccountRoutingRule` + emits
  `metrics.FtsInternalErrorCodeMediumSeverityCount(...,FtsInvalidMerchantDirectAccountRoutingRule)`
  (service.go:5401-5407) and returns `nil` (falls through to default rule).
- `selectDirectSourceAccByDefaultRoutingRule` (service.go:5413-5439): same query shape but
  `merchant_id: nil` (the **default/fallback rule** row, not merchant-specific), matched by
  `MozartIdentifier` string-equality instead of numeric id (service.go:5424-5428) — because default rules
  are keyed to a gateway/version, not a specific `source_account_id`.
- Table `direct_account_routing_rules` (`00020_direct_account_routing_rules_table_create.go:13-31`):
  `merchant_id, product, channel, mozart_identifier, source_account_id, mode` — **no `priority` or
  `routing_enabled` column** (unlike `source_account_mappings`) — a direct routing rule is either present
  (deterministic single source account) or absent (falls to default rule, then to
  `selectDirectSourceAccByOldestOrHighestPriority`-style fallback per source_account_selector.go:5370-5382,
  "fallback older direct source account selected", not fully traced).

**Channel health** (`internal/channel/service.go:590-622` `GetChannelHealthMapForTransfer`):
- Query: `repo.FindMultiple` (via `FetchCurrentChannelHealths`, cached under key
  `ChannelHealthQueryCacheOperationKey(OperationTransfer)`) with `conditions={operation:"TRANSFER"}` against
  `channel_information_status`.
- Table shape (`00022_channel_information_status_table_create.go:14-28`):
  `mode, status (tinyint), channel, account_type, mozart_identifier, integration_type, source_id,
  source_account_type` — `UNIQUE KEY (channel, mode, mozart_identifier, integration_type, account_type,
  source_account_type)`.
- Map built as `channel → mode → mozart_identifier → integration_type → bool(status==100)`
  (service.go:606-619) — **`100` is the only "up" value**; any other integer is treated as down.
- Twin seed comment confirms the query keys on `(account_type = bank account type, source_account_type =
  POOL/DIRECT)` (`seeds/s4/fts.sql:110-111,119`) and that **an empty table filters every mapping out**
  (`31_env2_bringup_notes.md:104`).
- **Who writes `channel_information_status`**: not a scheduled FTS cron. Writers found:
  1. `POST /v1/alert_manager` → `AlertManagerController.HandleAlert`
     (`internal/controllers/alert_manager.go:27`) — external Prometheus/Alertmanager webhook, auth
     `API, ALERT` (route_list.go, `[users.alert]`); test cases confirm downtime/uptime/fail-fast/bene-health
     alert types are all dispatched through this single handler
     (`internal/controllers/alert_manager_test.go:83,128,173,227,295,344` — Downtime/Uptime/FailFastDowntime/
     FailFastUptime/BeneDowntime/BeneUptime scenarios).
  2. `POST/PATCH/DELETE /v1/channel_health_events/*` (`internal/controllers/health.go`,
     `route_list.go:186-197`) — `alert_action`, `schedules` (planned-downtime schedule CRUD),
     `manual_override` (ManualUptimeTriggerOverride — ops-triggered), `fail_fast_status/manual_update`,
     `test_transaction/status` (`UpdateTestTransactionsStatus`) — same auth group (`API, ALERT`).
  3. `internal/channel/test_transactions.go` — a **synthetic canary-transaction** mechanism
     (`HandleTestTransactionsAction`, `TestTransactionsStatusUpdate`, lines 35-131) that presumably drives
     real tiny bank transfers and feeds their outcome back via `test_transaction/status`; this pass did not
     trace who *initiates* the test transaction (cron vs external caller) — **UNKNOWN**, flagged in §7.
  - The only in-process **cron** touching channel health is
    `CronExecutorForBankDegradationAlert`/`RunCron` (`internal/channel/cron.go:35-169`) — this is a **read-only
    alerting cron** (fires a Prometheus metric `LongerBankDegradationMetric` for channels stuck in downtime
    beyond a threshold, config `[longer_bank_degradation_alert_cron]` env.default.toml:9370-9377:
    `enable=true, start_time=60, execution_time=900, threshold=1800, schedule_threshold=43200,
    direct_threshold=1800`), gated to `INSTANCE_TYPE=canary` (cron.go:152). It does **not** write
    `channel_information_status` — it only reads `StatusDowntime` rows to raise alerts.
  - CORRECTION vs `03_fts.md` §5: that report said "these feed into routing decisions" without identifying
    the writer; this pass identifies the writer precisely as the alert-manager webhook (external
    Prometheus rule + `channel_health_events` admin/ops routes), **not** an FTS-internal health-check cron.

**Amount caps / time windows / retry backoff per mode+channel** — RBL v1 (the arena's only seeded channel),
`fts/config/env.default.toml`:

| Mode | Section | Amount lower | Amount upper | Time window | Retry SLA | TPS |
|---|---|---|---|---|---|---|
| IMPS | `[integration.api.rbl.v1.imps]:958-985` | 1 | 200,000 | none (24×7) | 7200s | 10 |
| UPI | `[integration.api.rbl.v1.upi]:1024-1032` | 1 | 200,000 | none | 14,400s | 10 |
| RTGS | `[integration.api.rbl.v1.rtgs]:1165-1212` | 200,000 | 100,000,000 | 08:00–17:30 (`START_HOUR=8,END_HOUR=17,END_MINUTE=30`), `WORKING_HOUR_APPLICABLE=true`, `WEEKEND_WORKING_HOUR_ENABLED=false` | 43,200s | 10 |
| NEFT | `[integration.api.rbl.v1.neft]:1214-1244` | 1 | 100,000,000 | 02:00–18:00 regular window; NEFT-24×7 "slot2" 18:00–02:00 (`neft.slot2:1234-1238`) with a non-working-window amount cap `NON_WORKING_WINDOW_AMOUNT_THRESHOLD=1,500,000,000` (paise, i.e. ₹15,00,000) at `neft.slot2.current:1240-1242` | 43,200s | 10 |
| ICICI IMPS | `[integration.api.icici.v1.imps]:896-901` | 1 | 200,000 | none | 7,200s | 10 |
| amazon_pay wallet | `[integration.api.amazon_pay.v1.wallet_transfer]:858-863` | 1 | 10,000 | none | (`STUCK_TRANSFER_STATUS_CHECK_COUNT=2`, no RETRY_SLA key) | 3 |

These are **generic-config values** (`env.default.toml`), confirmed **not overridden** for the RBL
mode/channel keys checked in `env.prod-live.toml` (grep found zero `rbl.v1.{neft,rtgs,imps,upi}` section
headers there) — treat as production-representative for RBL but not independently re-derived per-bank
beyond RBL/ICICI-IMPS/amazon_pay (time-boxed).

Per-mode **retry backoff** is config-driven, staged (`[[integration.api.<bank>.<version>.<mode>.retry.backoff]]`
arrays of `{INTERVAL,COUNT}`), e.g. RBL IMPS: 20s×2, 60s×4, 900s×6, 3600s×16, 10800s×10, 21600s×6, 86400s×3
(env.default.toml:965-985); RBL RTGS: 300s×3, 600s×6, 1800s×8, 3600s×20, 10800s×16, 21600s×8, 86400s×2
(env.default.toml:1182-1202) plus a per-error-code override block for `SUCCESS` (deemed-success recheck,
86400s×7, lines 1203-1208). Per-bank-error-code overrides exist under
`[integration.api.<bank>.<version>.<mode>.pool|direct.transfer_retry.<ERROR_CODE>.retry]` — e.g. RBL IMPS
pool: `BANK_CBS_OFFLINE_FAILURE`(1200s delay), `FROZEN_ACCOUNT`, `INSUFFICIENT_FUND`, `TXN_LIMIT_EXCEEDED`,
`TXN_REJECTED`, `TXN_REJECTED_BENE_BANK[_RETRIABLE]`, `TECHNICAL_ERROR[_FAILURE]` (env.default.toml:999-1021);
RBL UPI direct/pool sub-blocks additionally cover `INVALID_VPA`, `NPCI_TIMEOUT_FAILURE`,
`BBANK_GATEWAY_THROTTLED_FAILURE`, `BENE_PSP_OFFLINE`, `BBANK_OFFLINE`,
`REQUEST_NOT_FOUND_RETRIABLE`, `AUTHORIZATION_FAILED_RETRIABLE`, `AUTHENTICATION_FAILED`
(env.default.toml:1091-1162).

**Decision table** (shared/pool vs direct):

| Merchant class | Table(s) consulted | Key predicate | Fallback on empty/down |
|---|---|---|---|
| Shared (pool) | `source_account_mappings` (priority-ordered), `channel_information_status`, `preferred_routing_weights`, `account_type_mappings` | `product+merchant_id+account_type='POOL'`, sorted by `priority ASC` then filtered by allowed modes/category/onboarding/beneficiary-bank, then channel-health | if first-selected mapping has `routing_enabled=0` it is still returned (down); channel-health filtering + `fetchBackupPreferredDetail` for cross-channel failover (not re-read this pass) |
| Direct (current a/c) | `direct_account_routing_rules` (merchant-scoped, `Unscoped()` — sees soft-deleted rows), then default (`merchant_id IS NULL`) rule, then oldest/highest-priority direct source account | `product+merchant_id+channel+mode` exact match on merchant rule; `product+channel+mode` (merchant_id NULL) on default rule, matched by `mozart_identifier` | if merchant rule's `source_account_id` isn't in the caller's live `sourceAccounts` slice, metric emitted and falls to default rule; final fallback is oldest/highest-priority pre-fetched direct source account (service.go:5370-5382, not fully traced) |

CONFIRMED for items with cited line numbers; INFERRED/partial for NEFT-24x7 internals (lines 1210+ of
source_account_selector.go not opened), `fetchBackupPreferredDetail` body, and
`selectDirectSourceAccByOldestOrHighestPriority`-style final fallback (name inferred from log message only).

### 2.3 Transfer / attempt state machines

Source: `fts/internal/transfer/state_machine.go:1-131` (generic FSM engine `pkg/transition`). Confirms
`03_fts.md` §3 **exactly** — no corrections. Reproduced with line numbers:

**States** (state_machine.go:7-15): `StateLess="NEW"`, `StateCreated="CREATED"`,
`StateInitiated="INITIATED"`, `StateProcessed="PROCESSED"`, `StateFailed="FAILED"`,
`StateReversed="REVERSED"`, `StateRetry="RETRY"`, `StateUserPending="USER_PENDING"`,
`StateFailedInternal="FAILED_INTERNAL"` (attempt-only).

**Attempt transitions** (state_machine.go:37-75):
| From | Event | To |
|---|---|---|
| NEW | event_created | CREATED |
| CREATED, RETRY | event_initiated | INITIATED |
| INITIATED | event_processed | PROCESSED |
| INITIATED, CREATED | event_failed | FAILED |
| PROCESSED | event_reversed | REVERSED |
| CREATED | `StateFailed`-literal event (line 65-69, a second FAILED transition keyed on the literal string `"FAILED"` rather than `EventFailed` — code smell, but present) | FAILED |
| FAILED | event_retry | RETRY |
| CREATED | event_failed_internal | FAILED_INTERNAL |

**Transfer transitions** (state_machine.go:80-114):
| From | Event | To |
|---|---|---|
| NEW | event_created | CREATED |
| CREATED, RETRY, USER_PENDING | event_initiated | INITIATED |
| INITIATED | event_processed | PROCESSED |
| INITIATED, CREATED, RETRY, USER_PENDING | event_failed | FAILED |
| PROCESSED | event_reversed | REVERSED |
| FAILED, INITIATED | event_retry | RETRY |
| INITIATED | event_user_pending | USER_PENDING |

`getEventToTrigger` (referenced, not re-read this pass — carried from `03_fts.md:116`) maps Mozart-response
meta booleans (`Processed/Failed/Reversed/Pending`, computed by FTS per §2.6) directly to these FSM events.

**Retry policy for attempts** (`internal/transfer/attempt_processor.go`, grepped):
- `MaxReInitiateAttemptCount = 2` (attempt_processor.go:144) — an attempt may be **re-initiated to the bank
  at most twice** after the first initiation (checked at lines 1985, 2040, 2936, 3991).
- `HighVolumeMerchantRetrySLA = 86400` (attempt_processor.go:115) — used by `getRetrySLA` (line 6169-6193)
  as an override when the mode's config is high-volume; else falls back to per-merchant config
  `TransferRetry.MerchantConfig[merchantID][product][mode].RetrySLA` (line 6192-6193), else the
  channel/mode's `RETRY_SLA` TOML key (§2.2 table above).
- `attempts_unique_keys` table (`AttemptsUniqueKey{GatewayRefNo, CreatedAt}`,
  `internal/transfer/attempts_unique_keys.go:7-18`) — a dedupe-support table keyed on `gateway_ref_no`,
  written at `attempt_processor.go:7194` (not opened this pass); used to detect a duplicate
  `gateway_ref_no` coming back from the bank, which triggers `FailTransfer(FtsDuplicateGatewayRefNo,...)`
  at `transfer_processor.go:610` (per `03_fts.md:123`, not re-read this pass).
- **Skip-update-for-indeterminate-response guard**: `skipAttemptUpdateForIndeterminateResponses`
  (referenced at `03_fts.md:119`, name/line not independently re-confirmed this pass) — if
  `ReInitiateCount >= MaxReInitiateAttemptCount` and the last transfer-init bank status was
  `StatusDuplicateTxn` while the current status-check says failed, the failure update is **skipped**
  (comment: avoids double-paying merchant on a slight chance the earlier attempt still processes).

**Reconcile/verify jobs** (task registry, `internal/constants/worker.go`, cross-checked against
`env.default.toml` queue_map — see §2.5): `check_transfer_status(.v2)`, `verify_transfer_status(.v2)`,
`check_bulk_transfer_status(.v2)`, `check_recon_status` — these are **self-republishing async tasks**, not
k8s cron: a task handler calls Mozart's `transfer_status` operation, and on a non-terminal result
re-publishes itself with a computed delay (`getInitiateAtAndCheckRetrySLAForAttempts`,
attempt_processor.go:6088-6178) bounded by the mode's `RETRY_SLA` (table in §2.2) — i.e. polling continues
**until `RETRY_SLA` elapses since attempt creation**, at which point the mode-specific
`gateway_error_handling` cutoff rules (below) or the generic retry-exhaustion path force a terminal state.

**Ambiguous-response cutoff config** (`[gateway_error_handling]`, env.default.toml:8399-8436, confirmed
present, not re-read differently from `25_stork_mozart.md` §B.2 which already quoted it):
```toml
[gateway_error_handling.channel.idfc.status."CBS:188"]
    cut_off_time = 8100
    final_status = 'FAILED'
[gateway_error_handling.channel.yesbank.status."ns:E404"]
    cut_off_time = 5400
    final_status = 'FAILED'
```
Semantics (code-side guards `isCBS188WithUTR`/`isNSE404WithoutUTR`, not re-read this pass): if the raw bank
code is present **and UTR is null**, the attempt is only marked FAILED after `cut_off_time` seconds since
creation; if UTR is present, treated as pending regardless of cutoff.

### 2.4 Status propagation — `internal/transfer/service.go:1071-1275` (verbatim re-read)

`FireTransferStatusWebhook(ctx, transferID, attemptCount)`:

1. `repo.FindByID(transferID, &transferModel)` (line 1083). **Skip condition**: if
   `transferModel.Status == StateRetry`, log `TraceTransferWebhookJobSkipped` and `return nil` — **no
   webhook fires while a transfer sits in RETRY** (lines 1104-1111).
2. `transferMap, ierr := service.GetMapFromTransfer(ctx, &transferModel)` (line 1113) — builds the outbound
   body (see field list below).
3. `productConfig, _ := tProduct.GetProductConfig(ctx)`; `transferStatusConfig := productConfig.TransferStatus`
   (lines 1119-1126) — this is the **legacy per-product** `[webhook.<product>.transfer_status]` URL/auth
   (`product` = `transferModel.Product`, e.g. `payout`, `refund`, `settlement`, ...).
4. **AlternateURLFlow override** (lines 1132-1134): if `transferModel.MerchantID` is in
   `config.AlternateURLFlow.EnabledMerchants`, `transferStatusConfig.URL` is replaced with
   `AlternateURLFlow.AlternateBaseURL` (separate API instance for high-volume/critical merchants).
5. **Kafka branch** (lines 1136-1144): `splitZGet(ctx, merchantID, Splitz.Experiments.FireStatusUpdateKafka)`
   — if enabled, `go kafka.PushMessageWithKey(ctx, producerName, transferMap, key=source_id)` and
   **`return ierr` immediately** (no HTTP call at all in this branch; `ierr` is `nil` at this point since no
   error occurred, so effectively `return nil`).
6. **Origin-routing branch** (lines 1146-1208), gated by
   `isWebhookForOriginEnabled = splitZGet(ctx, merchantID, Splitz.Experiments.CreateTransferMetaRollout)`:
   - If enabled: fetch `transfer_meta` row for the transfer (`GetTransferMetaForTransfer`, line 1151); if
     `transferMeta.OriginService == "payouts"` (`PayoutsService` constant) **and**
     `config.PayoutsService.UpdateFtsFundTransfer` config is non-empty (line 1161) → build request against
     `psWebhookConfig.URL` with `SetAuth(config.PayoutsService.Auth)` (lines 1173-1179) — this is the
     **direct-to-PS** path.
   - Else (origin not `"payouts"`, or PS config empty, or the rollout experiment itself is disabled) →
     build request against `transferStatusConfig.URL` with `SetAuth(productConfig.Auth)` (lines 1181-1208)
     — the **legacy per-product/monolith** path.
   - `isShadowHeaderExperimentEnabled` (`shadowHeaderEnabled`, line 1148) optionally injects
     `x-shadow-merchant-id` header on either branch (lines 1162-1171, 1190-1198) — shadow-testing a new
     consumer without switching traffic.
7. `req.MakeRequest()` (line 1213) — synchronous HTTP call; timing captured, a **hang detector** logs/emits
   `metrics.WorkerTaskStuckCount` if execution time exceeds `3× req.Timeout()` (lines 1219-1235).
8. **Retry-on-failure** (lines 1253-1268): if `responseStatusCode/100 != 2` (covers `0` = connection
   failure) **and** `attemptCount < WebhookRetryMaxCount` → re-publish the same task with
   `WebhookRetryInterval*time.Second` delay and `attemptCount+1`
   (`WebhookRetryInterval=200` service.go:98, comment "the mutex lock at API<>PS integration is 180
   seconds"; `WebhookRetryMaxCount=3` service.go:99) — **3 retries spaced 200s apart, ≈10 minutes total**.
   Also selects a **high-volume queue variant** (`utils.ScopedTaskName(FireTransferStatusWebhook,
   constants.HighVolume)`) if `service.shouldUseHighVolumeQueue(transferModel)` (line 1257-1259, function
   body not opened this pass).
9. After exhausting retries: logs `TraceTransferWebhookUpdateFailure`, increments
   `metrics.TransferWebhookUpdateFailureCount(status, product)` (lines 1262-1266). **No DLQ, no alert call,
   no compensating persistence** in this function.
10. `return nil` unconditionally at the end (line 1274, with an explicit code comment: HTTP failures are
    intentionally not propagated as task errors — confirmed verbatim, matches `03_fts.md:155`).

**`GetMapFromTransfer` body fields** (service.go:1306-1346, partial read to line 1346): built from
`utils.ConvertStructToStringMap(transferModel)` (i.e. every `Transfer` struct field, §2.1's model — `amount,
attempts, channel, extra_info, request_meta, bank_status_code, failure_reason, fund_account_id, id,
initiate_at, transfer_by, merchant_id, mode, narration, bank_processed_time, preferred_mode,
preferred_channel, preferred_source_account_id, product, remarks, source_account_id,
source_account_primary_id, source_id, source_type, status, utr, return_utr, retry, gateway_error_code,
is_batch, type, batch_id, credited_at, is_credited, is_debited`), **plus** overrides:
`extra_info` (recomputed via `GetExtraInfo`), `fund_transfer_id = id`, `gateway_ref_no` (from last attempt),
`status_details = nil` (default), `status` (externalized via `getExternalTransferStatus`). For terminal
states (`PROCESSED,FAILED,REVERSED,USER_PENDING`) `bank_account_type` is added from the resolved source
account; for non-terminal states `source_account_id` is deleted from the map and
`setStatusDetailsInTransferMap` populates `status_details` instead (lines 1337-1346) — matches the twin
bringup's observed field list (`31_env2_bringup_notes.md:107`: `source_id, source_type, fund_transfer_id,
status, utr, gateway_ref_no, bank_status_code, failure_reason, mode, channel, return_utr, remarks,
narration`).

**Decision table — which of (a) webhook-to-monolith, (b) direct-to-PS, (c) Kafka fires**:

| `FireStatusUpdateKafka` (Splitz) | `CreateTransferMetaRollout` (Splitz) | `transfer_meta.origin_service` | `PayoutsService.UpdateFtsFundTransfer` config | Result |
|---|---|---|---|---|
| enabled | — | — | — | **(c) Kafka only** — `rx-fts-status-update-events`, function returns immediately, no HTTP call |
| disabled | disabled | — | — | **(a) legacy webhook** to `[webhook.<product>.transfer_status]` (monolith for `payout`/`refund`/etc.) |
| disabled | enabled | `"payouts"` | non-empty | **(b) direct to PS** — `payouts_service.update_fts_fund_transfer` |
| disabled | enabled | `"API"` or transfer_meta row missing | — | **(a) legacy webhook** (falls through to the else branch) |
| disabled | enabled | `"payouts"` | empty | **(a) legacy webhook** (PS config-empty guard forces fallback, line 1161's `&& !utils.IsEmpty(psWebhookConfig)`) |

**No other consumer found this pass**: `x-account-statements`/stork were not re-checked for a Kafka
consumer of `rx-fts-status-update-events` in this lane (out of scope — see Lane 13's payouts-side finding
that `internal/taskHandlers/fts_status_updates.go` in the `payouts` repo is the actual Kafka consumer, and
drops `reversed`/`failed` with "api is source of truth" — **cross-repo, not re-verified from the fts side
this pass**).

### 2.5 Workers

Queue engine: `RichardKnop/machinery` over Redis (`internal/providers/queue`), `CONCURRENCY=1` default
(env.default.toml:65), no SQS/visibility-timeout concept (Redis broker, not queue-visibility-based — a
"worker" is a machinery consumer bound to exactly one named queue).

**`[queue.worker_queue_map]`** (env.default.toml:106-166, partial excerpt — full list is ~40 entries):
worker-name (the `-command=` CLI arg) → queue-name (the redis machinery queue). Confirmed exact mechanic
from twin bringup note (`31_env2_bringup_notes.md:95`): "FTS is one machinery worker process per
`[queue.worker_queue_map]` KEY: `-command=<key>`... a worker started with the value name silently consumes
the default queue while tasks pile up". Sample entries:
`initiate_transfer→"initiate_transfer"`, `check_transfer_status→"check_transfer_status"`,
`fire_transfer_status_webhook→"fire_transfer_status_webhook"`, `"rbl::imps::initiate_transfer"→
"rbl_imps_initiate_transfer"`, `"rbl::direct::imps::initiate_transfer"→"rbl_direct_imps_initiate_transfer"`,
`check_recon_status→"check_recon_status"`, `retry_transfer→"retry_transfer"`,
`retry_transfer_preprocessor→"retry_transfer_preprocessor"`, `retry_transfer_source_update→
"retry_transfer_source_update"`, `process_attempt→"process_attempt"`, `one_off_db_processing→
"one_off_db_processing"` — bank-specific worker names exist per gateway/mode combination (icici, axis,
amazon_pay, mcs, ocbc — env.default.toml:130-166) reflecting per-bank isolation of transfer-init/status-check
throughput.

**Twin's fts worker set** (`ENV2_COMPOSE/docker-compose.yml:1000-1300`, 13 processes): `fts-worker-default`,
`-initiate-transfer`, `-check-transfer-status`, `-fire-transfer-status-webhook`, `-retry-transfer`,
`-retry-transfer-preprocessor`, `-retry-transfer-source-update`, `-rbl-initiate-transfer`,
`-rbl-check-transfer-status`, `-rbl-imps-initiate-transfer`, `-rbl-imps-check-transfer-status`,
`-rbl-direct-imps-initiate-transfer`, `-rbl-direct-imps-check-transfer-status` — each
`entrypoint: ["/app/fts-worker","-env=arena","-base_path=/app","-command=<name>"]`. This is a
**representative subset** of the ~40 real worker-queue-map keys (no ICICI/Axis/amazon_pay/mcs/ocbc/m2p/citi
worker processes in the twin, since the arena only seeds an RBL channel).

**Cron jobs found in config** (env.default.toml, in-process gocron via `internal/cron` +
`internal/leaderelection`, not k8s CronJob):
| Cron | Config section | Enable | Cadence |
|---|---|---|---|
| `fetch_healthy_balance_cron` | `[cron.fetch_healthy_balance_cron]:9507-9510` | true | `execution_interval=300s, start_time=0` |
| `stuck_payouts_cron` | `[cron.stuck_payouts_cron]:9511-9514` | true | `execution_interval=60s, start_time=0` |
| `longer_bank_degradation_alert_cron` | `[longer_bank_degradation_alert_cron]:9370-9377` | true | `start_time=60s, execution_time=900s`, `threshold=1800s` (regular), `direct_threshold=1800s`, `schedule_threshold=43200s` |

Stuck-payouts cron (`internal/stuckpayouts/cron.go`, `internal/transfer/stuck_payouts.go` — not re-read this
pass, carried from `03_fts.md:300-303`): gated to `INSTANCE_TYPE=canary`, leader-elected, two independently
toggleable handlers (`StuckTransfersProcessor`, `StuckAttemptsProcessor`).

**Downtime notification callback** (`internal/downtime/notification.go:440-519`, re-read this pass):
`SendPartnerBankHealthNotification` builds a `PartnerBankHealthPayload{mode, channel, begin, account_type,
status, source, instrument, include_merchants, exclude_merchants}` (struct at lines 58-67) and sends it
**to two destinations in the same call**:
- monolith: `productConfig.ChannelStatus` URL/auth (`[webhook.payout.notify_channel_status]`-equivalent,
  `product.Payout` config, `SetAuth(productConfig.Auth)`, lines 458-464)
- Payouts Service: `config.PayoutsService.NotifyChannelStatus` (`[payouts_service.notify_channel_status]`,
  `SetAuth(psConfig.Auth)`, lines 466-472) — **only sent if
  `config.GetConfig().Feature.PSDowntimeNotificationOnHold` is true** (line 479-481, feature-flag gated).
- Retry: if either call's status isn't 2xx **and** `notificationCount < PartnerBankWebhookRetryCount` (=3,
  line 33) → re-publish `send_notification` task with `PartnerBankWebhookRetryInterval` (=10s, line 34)
  delay (lines 500-516).
- A separate, DowntimeV2-specific notifier (`internal/downtimeV2/publisher.go:14-59`) does **not** call any
  HTTP endpoint directly — `NotifyDowntimeStarted/SeverityChanged/Ended` all funnel into
  `pushToQueue`→`queue.Provider.Publish(constants.SendNotification, ..., common.NotificationDowntimeV2,
  eventType)` (lines 42-51) — i.e. the actual HTTP delivery for V2 downtime events happens inside the
  generic `send_notification` task handler, not inspected this pass (**UNKNOWN** — which URL/body
  `send_notification` uses for `NotificationDowntimeV2` vs `NotificationPartnerBankDowntime` was not traced
  to a single shared or divergent implementation).

Also confirmed: `[payouts_service.notify_downtime]` config block exists (env.default.toml:842-846,
`URL="http://localhost:8080/"` placeholder in default config, real value per-env) —consistent with, but not
proof of, the V1 `notification.go` call path being the one that uses it (the V1 code read this pass calls
`config.PayoutsService.NotifyChannelStatus`, a **different** config key name than
`notify_downtime` — this is a discrepancy worth flagging: **CORRECTION candidate** — the task brief's
phrasing "downtime notification (route and body of the downtime callback)" maps to
`notify_channel_status` in the code actually read, while a separately-named `notify_downtime` config key
also exists; this pass could not confirm which callers use `notify_downtime` specifically — UNKNOWN, see §7).

### 2.6 Mozart — 8 operations, envelopes, error classification

**FTS's 8 operations** (`fts/internal/config/mozart.go:4-13`, confirmed against `internal/providers/mozart/executor.go`
dispatch table per `03_fts.md:191`, not independently re-read this pass): `login`, `gateway_auth`,
`gateway_session`, `transfer_init`, `transfer_status`, `beneficiary_verify`, `beneficiary_register`,
`account_balance` (`fetch_account_balance`), `create_otp`.

**Request envelope FTS sends** (`fts/internal/providers/mozart/request.go:43-61`, `Request` struct):
```go
type Request struct {
    Contains []string `json:"contains"`
    Entities struct {
        Auth        map[string]interface{}
        FundAccount struct {
            BankAccount, Vpa, Card, NonSavedCard, Wallet, RemitterAccount map[string]interface{}
            PaymentInstrument string
        } `json:"fund_account"`
        Attempt           map[string]interface{} `json:"attempt"`
        SourceAccount     map[string]interface{} `json:"source_account"`
        BeneficiaryStatus map[string]interface{} `json:"beneficiary_status"`
        GatewayAuth       map[string]interface{} `json:"gateway_auth"`
        GatewaySession    map[string]interface{} `json:"gateway_session"`
    } `json:"entities"`
}
```
i.e. `POST /{namespace}/{gateway}/{version}/{action}` body = `{"contains":[...],"entities":{"fund_account":
{...},"attempt":{...},"source_account":{...},"beneficiary_status":{...},"gateway_auth":{...},
"gateway_session":{...}}}`. This matches Mozart's own scenario-key field paths exactly (§2.6 fixture table
below uses `entities.attempt.amount`, `entities.fund_account.bank_account.beneficiary_mobile`,
`entities.source_account.credentials.bcagent_username`).

**Response envelope Mozart returns** (`fts/internal/providers/mozart/response.go:3-101`, `Response` struct
— quoted verbatim, all fields):
```go
type Response struct {
    Data struct {
        Raw, BeneficiaryName string; ID int64; Remarks, Utr, BankStatusCode, GatewayErrorCode,
        CmsRefNo, FailureReason, BankRequestID, BankProcessedTime string
        GatewayAuth, GatewaySession gatewayAuth   // {token, token_type, validity_duration}
        ReturnUtr, Ponum, Balance, AccountBalance string  // json:"accountBalanceAmount"
        CreditedAt string; IsCredited, IsDebited bool
        BeneficiaryCode, BeneficiaryIfsc, BeneficiaryAccNo, BeneficiaryBank,
        BeneficiaryAccType, BeneficiaryValidationMode string
    } `json:"data"`
    Error mozartError `json:"error"`  // {description, gateway_error_code, gateway_error_description, gateway_status_code, internal_error_code}
    ExternalTraceID, MozartID string
    Success bool
    Meta meta `json:"meta"`  // {error_type, merchant_error, critical_error, retriable_error, pending, processed, failed, reversed}
    ExtraInfo ExtraInfo       // {beneficiary_name, ponum, deemed_success{gateway_error_code,creation_time}, bank_status_codes{transfer_init,transfer_status}, is_pending, payee_ifsc, beneficiary_acc_no/bank/acc_type/validation_mode}
    HTTPStatusCode int        // not serialized (no json tag) — set locally by NewResponse()
}
```
**Confirming C57**: `meta.{pending,processed,failed,reversed,error_type,...}` is **not on Mozart's wire
response** — it is computed by FTS itself post-hoc from `Data.BankStatusCode` via the two maps below. This
was re-confirmed structurally this pass (the `meta` fields have no evidence of being populated from an
inbound field named `meta`/`processed`/etc.; the map lookup mechanism (`defaultErrorCodes`/
`transferErrorCodes`) is the only place `meta.{Pending,Processed,Failed,Reversed}` are set).

**`defaultErrorCodes`/`transferErrorCodes` map shape** (`fts/internal/providers/mozart/error_code.go:217-230`
declaration, ~140 entries in `defaultErrorCodes`, spot-checked not exhaustively enumerated):
```go
type meta struct {
    ErrorType ErrorType; MerchantError, CriticalError, RetriableError, Pending, Processed, Failed, Reversed bool
}
var transferErrorCodes = map[string]meta{ /* transfer_init-only overrides, e.g. */
    StatusGatewayErrorVaultFailure: {Failed: true, ErrorType: INTERNAL},   // vs Pending in defaultErrorCodes — transfer_init treats it as failed, status-check treats it as still-pending
    StatusBadRequestValidationFailure: { /* ... */ },
}
var defaultErrorCodes = map[string]meta{ /* general lookup, keyed by normalized bank_status_code */
    StatusSuccess:                  {Processed: true, ErrorType: UNKNOWN},
    StatusDuplicateTxn:             {Pending: true, ErrorType: PBANK},
    StatusInsufficientFund:         {RetriableError: true, Failed: true, ErrorType: INTERNAL},
    StatusMerchantInsufficientFund: {RetriableError: true, Failed: true, ErrorType: MERCHANT},
    StatusReturned:                 {Failed: true, ErrorType: BBANK},
    StatusInvalidVPA:               {Failed: true, MerchantError: true, ErrorType: MERCHANT},
    StatusInvalidBeneficiaryDetails:{Failed: true, ErrorType: MERCHANT},
    StatusFrozenAccount:            {Failed: true, ErrorType: MERCHANT},
    StatusInvalidAccount:           {Failed: true, ErrorType: MERCHANT},
    StatusTxnRejectedBeneBank:      {Failed: true, ErrorType: BBANK},
    // ...~130 more entries, error_code.go:229-952
}
```
**Important correction/clarification vs the task framing**: this classification map is **not per-bank** —
it is a single generic lookup keyed on a **normalized `bank_status_code` string** (e.g. `SUCCESS`,
`DUPLICATE_TXN`, `INSUFFICIENT_FUND`, `RETURNED`, `INVALID_VPA`, `FROZEN_ACCOUNT`) shared across every
gateway/bank. There is **no separate "RBL error codes" / "ICICI error codes" table** in this file — any bank
integration that normalizes its raw response to one of these ~140 strings gets the same
Pending/Processed/Failed/Reversed classification. Bank-specific *raw, unnormalized* codes are handled
**outside** this map as special-case string guards, e.g. `RawCBS188BankCode="CBS:188"` (IDFC),
`RawNSE404BankCode="ns:E404"` (Yesbank), `RawProxy401BankCode`, `RawCBS7161BankCode` (error_code.go:126-130,
per `25_stork_mozart.md` §B.2, not re-read this pass) — those are the only bank-*specific* codes in FTS's
codebase; RBL/ICICI/Axis do not have their own named constant sets in `error_code.go`, only the shared
`Status*` vocabulary. Requesting "the codes for RBL, ICICI, Yes, Axis" as if bank-specific tables exist is
therefore not directly satisfiable from this file — the closest per-bank artifact is the
`[integration.api.<bank>.<version>.<mode>.pool|direct.transfer_retry.<ERROR_CODE>]` retry-override
sections in `env.default.toml` (§2.2), which *do* name specific codes per bank+mode (e.g. RBL UPI direct
names `INVALID_VPA`, `NPCI_TIMEOUT_FAILURE`; RBL IMPS pool names `BANK_CBS_OFFLINE_FAILURE`,
`INSUFFICIENT_FUND`) but these are retry-backoff overrides, not a separate classification table.

Classification examples relevant to the task (all from `defaultErrorCodes`, generic across banks):
| bank_status_code | Pending | Processed | Failed | Reversed | ErrorType |
|---|---|---|---|---|---|
| `SUCCESS` | — | ✓ | — | — | UNKNOWN |
| `DUPLICATE_TXN` | ✓ | — | — | — | PBANK |
| `INSUFFICIENT_FUND` | — | — | ✓ (retriable) | — | INTERNAL |
| `MERCHANT_INSUFFICIENT_FUND` | — | — | ✓ (retriable) | — | MERCHANT |
| `RETURNED` | — | — | ✓ | (reversed handling is separate — surfaced via a distinct meta flag path elsewhere, not this map) | BBANK |
| `INVALID_VPA` | — | — | ✓ (merchant_error) | — | MERCHANT |
| `INVALID_BENEFICIARY_DETAILS` | — | — | ✓ | — | MERCHANT |
| `FROZEN_ACCOUNT` | — | — | ✓ | — | MERCHANT |
| unmapped code | ✓ (default) | — | — | — | UNMAPPED |

`transferErrorCodes` (transfer_init-only overrides, error_code.go:217-227): only entries confirmed this
pass are `StatusGatewayErrorVaultFailure` (Failed during transfer_init vs Pending in the default map) and
`StatusBadRequestValidationFailure` (not fully read).

**`mozart -mock` mode** (`mozart/app/mock/mappings.go`, full 156-line file read):
- Scenario selection: exact route string → JSON field path in the (flattened) request body, exact-string
  match on that field's value = scenario ID (no pattern/suffix matching). Full `fts`-namespace table:

  | Route | Match field |
  |---|---|
  | `fts/icici_imps/v1/transfer_init` | `entities.attempt.amount` |
  | `fts/icici_imps/v1/transfer_status` | `entities.attempt.amount` |
  | `fts/rbl/v1/transfer_init` | `entities.fund_account.bank_account.beneficiary_mobile` |
  | `fts/rbl/v1/transfer_status` | `entities.fund_account.bank_account.beneficiary_mobile` |
  | `fts/rbl/v1/gateway_session` | `entities.source_account.credentials.bcagent_username` |
  | `fts/rbl/v1/gateway_auth` | `entities.source_account.credentials.bcagent_username` |
  | `fts/rbl/v2/transfer_init` | `entities.attempt.gateway_ref_no` |
  | `fts/rbl/v2/transfer_status` | `entities.attempt.gateway_ref_no` |
  | `fts/citi/v1/gateway_auth` | `entities.source_account_credentials.username` |
  | `fts/citi/v1/transfer_init` / `transfer_status` | `entities.attempt.amount` |
  | `fts/yesbank/v1/beneficiary_register` / `beneficiary_verify` | `entities.beneficiary_status.beneficiary_code` |
  | `fts/yesbank/v1/transfer_init` | `entities.attempt.amount` |
  | `fts/yesbank/v1/transfer_status` / `account_balance` | `entities.attempt.gateway_ref_no` |
  | `fts/yesbank_upi/v1/transfer_init` | `entities.attempt.amount` |
  | `fts/yesbank_upi/v1/transfer_status` | `entities.attempt.gateway_ref_no` |
  | `fts/m2p/v1/transfer_init` | `entities.attempt.amount` |
  | `fts/m2p/v1/transfer_status` | `entities.attempt.gateway_ref_no` |
  | `fts/icici/v2/registration` | `entities.source_account.credentials.CrpUsr` |
  | `fts/icici/v2/beneficiary_register` | `entities.beneficiary_status.gateway_ref_no` |
  | `fts/icici/v2/transfer_init` | `entities.attempt.amount` |
  | `fts/icici/v2/transfer_status` / `account_balance` | `entities.attempt.gateway_ref_no` / `entities.source_account.account_number` |
  | `fts/icici/v1/transfer_init` | `entities.attempt.amount` |
  | `fts/icici/v1/transfer_status` | `entities.attempt.gateway_ref_no` |
  | `razorpayx/rbl/v1/account_balance` | `entities.source_account.account_number` |

  **CORRECTION vs `25_stork_mozart.md` §B.4/B.1**: that report implies Axis and IDFC gateways have
  `-mock`-reachable scenarios ("hundreds already exist under `mozart/app/testdata/fts/**` for RBL v1-v5,
  ICICI v1-v3/imps, Yes Bank v1/v2/UPI, **Axis v1, IDFC UPI**, M2P, Kotak, Citi, Amazon Pay"). This pass
  confirms `mozart/app/testdata/fts/{axis,idfc_upi,kotak}/v1/**` directories **do exist** (fixture files are
  present — `.golden`/`.gatewayResp` pairs, used by Mozart's own Go integration-test suite), but
  **`mock/mappings.go` has zero entries for `axis`, `idfc`, or `kotak`** — grepped the full 156-line file,
  no matches. This means those gateways' fixtures are reachable by Mozart's ITF/unit tests (which load
  fixtures directly by path) but **not** by the runtime `-mock` HTTP scenario-selection mechanism (which
  needs a `mappings.go` entry to know which request field selects the scenario). A synthetic Axis/IDFC/Kotak
  fixture set therefore cannot be driven through `-mock`'s HTTP interface without first adding a
  `mappings.go` entry (a **repo modification**, out of scope for a read-only substitute).

- Fixture directory layout confirmed (`mozart/app/testdata/fts/rbl/v1/transfer_init/9999999100/`):
  `9999999100.gatewayReqGolden`, `9999999100.gatewayResp`, `9999999100.golden`, `9999999100.vaultResp`,
  `TestTransferInitIFTWithSuccess.input` — matches `25_stork_mozart.md` §B.4 exactly (`.golden`=final Mozart
  response, `.gatewayResp`=raw bank response, `.vaultResp`=canned Vault token response, plus a `.input` test
  file and a `.gatewayReqGolden` request-golden not previously listed).
- 2,657 files total under `mozart/app/testdata/fts/**` (all gateways/versions/actions combined).

**Can the real `mozart -mock` be built now?** — **NO, confirmed blocked**, per `mozart/go.mod`:
```
github.com/razorpay/orchestrator v1.0.151        (go.mod:28)
github.com/razorpay/integrations-go v0.0.0-...   (go.mod:54)
github.com/razorpay/integrations-utils v0.3.14   (go.mod:55)
```
None of `orchestrator`, `integrations-go`, `integrations-utils` appear in the lane brief's readable-clones
list, and `ls` on the clones root confirms none of the three exist as a sibling clone (checked: only
`accounting-integrations` partially matches the substring "integrations", not the actual module). This
matches the twin bringup note verbatim: "CONFIRMED BLOCKED during the Env 2 substitutes pass: `go build` of
a real `../mozart` clone fails on the private module `github.com/razorpay/integrations-utils` (404 for the
build identity used)" (`31_env2_bringup_notes.md`, quoted in `docker-compose.yml` mozart-mock comment block).
The `mozart-mock` compose service exists but is `profiles: ["mozart-real"]` (opt-in only, not part of
`core`/`substitutes`), and its build `context` defaults to a placeholder path
(`./build/_mozart_context_not_configured`) that does not exist. **No path to a working real Mozart `-mock`
instance without a private-registry credential this lane does not have.**

### 2.7 Recon/ART and admin routes

`PATCH/POST /v1/attempts/:action`, `PUT /v1/attempts/reconcile` (route_list.go:174-184) — auth
`API, ART` only (not `API, ALERT` as one might assume from route naming; re-confirmed by direct read of the
group's middleware, route_list.go:177-178).

`AttemptBulkAction.Patch` (`internal/controllers/attempt_bulk_action.go:57-140`):
- Input: a map keyed by **`gateway_ref_no`** (not attempt id), each value an object validated by
  `bulkUpdateAttemptRequestValidation` (validation.go, fields: `return_utr` (optional, basic string),
  `remarks` (**required**, alpha string), `mozart_meta` (nested, see below), `bank_status_code`
  (**required**, basic string), `utr` (optional), `status` (optional), `failure_reason` (optional, alpha
  string)). If the value carries `return_utr`, an **additional** stricter validator
  `bulkUpdateAttemptReversalRequestValidation` also runs, requiring `mozart_meta` to contain exactly the key
  `reversed` (`validator.OneOf(transfer.AttributeMozartStatusReversed)`, a bool).
- `mozart_meta` shape (used by both PATCH bodies, validation.go `mozartMetaValidation`): a struct whose
  single required top-level key must be **exactly one of** `processed`/`failed`/`reversed`
  (`validator.OneOf(...)`), each optionally a bool — i.e. the recon caller directly injects the same
  `meta.{Processed,Failed,Reversed}` flags that FTS itself normally computes from Mozart responses (§2.6),
  letting ART force a specific FSM event without going through the bank-code classification maps.
- `input[gatewayRefNo][common.AttributeAdminEmail] = utils.GetUserEmail(ctx)` (line 117) — every recon write
  is tagged with the calling admin/service identity from context.
- Per-key result: `{status: bool, message: string}`; a global size cap
  `config.GetConfig().Meta.AttemptsPatchActionLimit` rejects the whole batch with 400 if `len(input)`
  exceeds it (lines 67,74-95).
- Dispatch: `controller.service.PerformAttemptAction(ctx, action, gatewayRefNo, value)` (line 118) — the
  `:action` path param selects the operation (not enumerated this pass — `PerformAttemptAction`'s internal
  switch was not opened; **UNKNOWN** exact action name vocabulary beyond what the route implies).
- `PUT /reconcile` (`AttemptBulkAction.Put`, line 219 in the same file, body not opened this pass —
  **UNKNOWN** exact contract).

---

## 3. Corrections to existing reports

| Report | Claim | What source says | Evidence |
|---|---|---|---|
| `03_fts.md` §5 | "these [channel health tables] feed into routing decisions" — writer left unidentified | The writer is the external Prometheus/Alertmanager webhook `POST /v1/alert_manager` → `HandleAlert`, plus ops routes under `/v1/channel_health_events/*` (`manual_override`, `test_transaction/status`, `schedules`) — **not** an FTS-internal scheduled health-check cron. The only channel-health-adjacent cron (`CronExecutorForBankDegradationAlert`) is read-only (alerting metric), not a writer. | `fts/internal/controllers/alert_manager.go:27`, `health.go` route wiring, `route_list.go:186-197`, `internal/channel/cron.go:35-169` |
| `25_stork_mozart.md` §B.1/§B.4 | "hundreds already exist under `app/testdata/fts/**` for RBL v1-v5, ICICI v1-v3/imps, Yes Bank v1/v2/UPI, **Axis v1, IDFC UPI**, M2P, Kotak, Citi, Amazon Pay" — implies these are all usable via `mozart -mock` | `mozart/app/mock/mappings.go` (full file, 156 lines) has **zero** scenario-selection entries for `axis`, `idfc`, or `kotak` gateways — their testdata directories exist (used by Mozart's own Go integration tests, which load fixtures by direct path) but are **not reachable through the runtime `-mock` HTTP scenario-matching mechanism**. Only RBL (v1/v2 confirmed in mappings.go — v3-v5 present as directories but also absent from mappings.go), ICICI (v1/v2), ICICI-IMPS, Citi, Yesbank, Yesbank-UPI, M2P have `-mock`-reachable scenarios. | `mozart/app/mock/mappings.go:1-156` (full read) vs `mozart/app/testdata/fts/{axis,idfc_upi,kotak}/v1/**` (directories present) |
| `03_fts.md` §2 | Table lists `v1/attempts/:action` auth as "API, ART" — correct, but grouped ambiguously next to `channel_health_events` rows suggesting shared auth | Re-confirmed as its own distinct group with its own middleware line: `middleware.BasicAuth(constants.APIAuth, constants.ARTAuth)` (route_list.go:177-178), separate from the `channel_health_events` group's `API, ALERT` (route_list.go:189-190). No change to the auth set itself, just confirming it is not shared with the alert group. | `fts/internal/routing/router/route_list.go:174-198` |
| `13_fts_payouts_status_path.md` (KNOWN baseline referenced, not itself a claim to check) | N/A | This lane independently re-derived the exact `FireTransferStatusWebhook` code (lines 1071-1275) that `13_...md` treated as a given "KNOWN baseline" — **fully confirmed**, no discrepancy found in webhook retry count (3), interval (200s), skip-on-RETRY condition, or the unconditional `return nil`. | `fts/internal/transfer/service.go:1071-1275` |
| Task brief (Lane brief §6) | Asks for "bank_status_code → FTS classification... quote the maps' shape and the codes for RBL, ICICI, Yes, Axis at least" — implies bank-specific classification tables | `defaultErrorCodes`/`transferErrorCodes` in `error_code.go` are **bank-agnostic** — one shared map keyed on normalized `bank_status_code` strings, used by every gateway. There is no per-bank classification table; the only per-bank artifacts are (a) raw/unnormalized special-case codes (`CBS:188` for IDFC, `ns:E404` for Yesbank) handled by string guards outside the map, and (b) per-bank+mode retry-backoff override sections in `env.default.toml` (`[integration.api.<bank>.<version>.<mode>.pool\|direct.transfer_retry.<CODE>]`). | `fts/internal/providers/mozart/error_code.go:217-952`; `fts/config/env.default.toml:999-1162` |
| `ARCHITECTURE_DELTA.md` C57 | "Mozart's wire response envelope carries no meta/processed/failed flags" | **Confirmed exactly**, re-derived independently this pass from `response.go:3-101` structurally (the `meta` struct's only population site is the two error-code maps, not any inbound JSON key literally named `meta`). No correction — restated with fresh line numbers. | `fts/internal/providers/mozart/response.go:3-101`, `error_code.go:217-230` |

No other corrections found — the remainder of `03_fts.md`, `13_fts_payouts_status_path.md`, and
`25_stork_mozart.md` §B/§C checked against source in this pass (route table, state machine, webhook
mechanics, Mozart operation list, mock-mode mechanics, private-dep blocker) **matched source exactly**.

---

## 4. Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Capability |
|---|---|---|---|---|---|
| `/v1/transfer` create | Gin route, Basic Auth by username-prefix reflection, `source_id+source_type` unique + mutex dedupe | Same binary (`rzp-arena/fts` image built from real `fts` repo), `fts.toml` overrides only infra hosts (`env.arena.toml`) | **REAL** | none functionally — config-only substitution | authz, idempotency, transfer/payout state |
| Route selection (shared) | `source_account_mappings` priority-ordered query + channel-health filter | Same code; seed rows in `seeds/s4/fts.sql:64-72` (2 merchants × IMPS/NEFT → pool account 900001), `channel_information_status` seeded with 10 explicit UP rows (`fts.sql:112-125`) covering all `(account_type, source_account_type)` combinations the health query can hit | **REAL** (code) / **REPRESENTATIVE** (seed breadth — only 1 channel/bank (RBL) seeded vs 12 in prod) | no failover scenario seeded (only one healthy channel exists, so cross-channel failover logic is never exercised) | routing |
| Route selection (direct) | `direct_account_routing_rules` merchant+default rule | Same code; 2 rows seeded (`fts.sql:100-104`, M2 IMPS/NEFT → account 900002) | **REAL** (code) / **REPRESENTATIVE** (only merchant-scoped rule, no default/fallback rule seeded, so the `merchant_id IS NULL` fallback path is never exercised) | fallback-rule path untested | routing |
| Transfer/attempt FSM | `pkg/transition` in-process | Same binary | **REAL** | none | transfer/payout state |
| Status webhook (a/b/c) | 3-way Kafka/PS-direct/monolith-legacy branch, Splitz-gated | Same code; twin **disables Kafka** entirely (`env.arena.toml:155-158`, `kafka_producers.fire_transfer_status.enabled=false` — no local broker stood up) and points `[webhook.payout.transfer_status]` at `monolith-stub` (line 172) | **REAL** for (a)/(b) code path; **MISSING** for (c) — Kafka branch cannot be exercised at all in this twin | Splitz itself is a stub (`splitz_stub`, `env.arena.toml:138`) returning presumably static/config-driven experiment results — so which of (a)/(b)/(c) fires depends on the stub's canned answer, not real per-merchant Splitz state | routing (destination), payout state propagation |
| Mozart (bank gateway) | Real Mozart binary talks to actual banks; `-mock` mode available for non-prod | `mozart-sim` (Python stdlib HTTP server, `substitutes/mozart-sim/server.py`, 266 lines) is the **default/primary** substitute; `mozart-mock` (real binary) service defined but `profiles:["mozart-real"]`, inert by default and **cannot currently be built** (private-module blocker, §2.6) | **REPRESENTATIVE SUBSTITUTE** (mozart-sim), real Mozart **UNKNOWN-BLOCKED** | mozart-sim implements the same envelope shape (`{data,error,external_trace_id,mozart_id,success,next}`, matches `response.go` field-for-field on the subset it populates) but scenario selection is **amount-keyed** (own convention: 100100→INITIATED, 100200→INSUFFICIENT_FUND, etc., `server.py:74-111`) and **gateway_ref_no-keyed for status** (`server.py:114-175`) — this is a *third* convention, distinct from both real Mozart's exact-field-match (`mappings.go`) and FTS's own `cmd/mock-server`'s amount convention; it does **not** replicate per-bank gateway/version routing (all `{gateway}/{version}` values are accepted identically — see `parts = path.split("/"); _namespace,_gateway,_version,action = parts` at server.py:244-249, meaning **the sim ignores which bank/version was requested entirely** and dispatches purely on `action`) | bank/transfer outcome truth |
| Mozart bank-error classification | FTS-side `defaultErrorCodes`/`transferErrorCodes` maps (818-line file, real code) | Same code (fts binary is real) — only the **inputs** (Mozart's `bank_status_code`) are synthetic, from mozart-sim | **REAL** (classification logic) / **REPRESENTATIVE** (input coverage — mozart-sim only emits `SUCCESS`, `INSUFFICIENT_FUND`, `DUPLICATE_TXN`, `PENDING`, `CBS:188`, `RETURNED` scenarios, a small subset of the ~140-entry map) | codes not exercised: `FROZEN_ACCOUNT`, `INVALID_VPA`, `TXN_REJECTED`, `AUTHORIZATION_FAILED`, and all `transferErrorCodes` overrides | bank error semantics coverage |
| Worker set | ~40 `worker_queue_map` keys, per-bank×mode×operation granularity in prod | 13 workers seeded (default + initiate/check/webhook/retry×3 + RBL×2 variants×2 ops) | **REPRESENTATIVE** (subset) | no ICICI/Axis/Yesbank/amazon_pay/m2p/citi/ocbc worker processes exist in the twin — any test scenario needing a non-RBL channel cannot be driven end-to-end without adding workers+config | worker coverage for non-RBL channels |
| Recon/ART write route | `PATCH/POST /v1/attempts/:action`, `PUT /v1/attempts/reconcile`, auth API+ART | Not found wired in `docker-compose.yml`'s fts service list as a distinct caller (no `art-stub` or recon caller service was found in the grep of compose fts-adjacent lines) | **MISSING** (no caller exercises this route in the twin) | recon/repair flows into FTS untested end-to-end | recon/repair |
| Downtime notification callback | `PartnerBankHealthPayload` → monolith `notify_channel_status` + PS `notify_channel_status` (feature-flag gated), 3×10s retry | Not traced in twin config this pass (`payouts_service.notify_channel_status`/`notify_downtime` URLs point at arena placeholders per template inheritance from `env.default.toml`, not overridden in `env.arena.toml`) | **UNKNOWN** (not verified whether monolith-stub/PS in the twin implement a receiver for this callback) | — | downtime/merchant-visible state |

---

## 5. Recommendation: real vs substitute

- **FTS**: run **REAL** — the repo builds (per twin bringup notes, the arena already runs the real `fts`
  binary as `fts-web`/`fts-worker-*`). No blocking private dependency was found in `fts/go.mod` for the
  routes/logic this lane covers (contrast with Mozart, below). Recommendation: keep as-is.
- **Mozart**: run **SUBSTITUTE** — real `mozart -mock` is **blocked** (private modules
  `integrations-utils`, `integrations-go`, `orchestrator` unavailable, confirmed via `go.mod` + absence from
  clones root, §2.6). The substitute (`mozart-sim`) must implement:
  - Protocol: `POST /{namespace}/{gateway}/{version}/{action}`, HTTP Basic auth (any credential accepted is
    fine for a sandbox — real Mozart's per-client credential distinction is not security-relevant to a test
    twin).
  - Body: accept FTS's exact `Request` envelope (`{contains, entities:{fund_account,attempt,source_account,
    beneficiary_status,gateway_auth,gateway_session}}`) — mozart-sim already parses `entities.attempt.amount`
    and `entities.attempt.gateway_ref_no` correctly via its own `_dig` helper (server.py:36-42).
  - Response: `{data, error, external_trace_id, mozart_id, success, next}` — already matched.
  - **Gap to close**: mozart-sim currently ignores `{gateway}/{version}` entirely (dispatches on `action`
    only). If the twin ever needs to exercise per-bank differences (e.g. RBL vs ICICI amount caps, or a
    bank-specific raw code like `CBS:188` for IDFC vs `ns:E404` for Yesbank), the sim needs to branch on
    `gateway` too — currently it only implements the RBL-flavored scenario set, silently reusing it for any
    gateway string.
  - **Gap to close**: no per-bank_status_code exhaustive coverage — recommend adding scenario keys for at
    least one representative code per FTS classification bucket (`MERCHANT`/`BBANK`/`PBANK`/`INTERNAL`
    `ErrorType`s) so downstream retry/ambiguous-handling logic gets exercised.
- **Splitz** (feeds the a/b/c webhook decision and NEFT-24x7/routing-restriction flags): twin substitute
  (`splitz-stub`) behavior not verified this pass — flagged in §7, since it silently determines whether the
  Kafka/PS-direct/legacy-webhook branch fires.
- **Recon/ART**: no substitute exists in the twin for `/v1/attempts/:action`/`reconcile` callers — if Env4's
  scope (per `ARCHITECTURE_DELTA.md`'s closure table, "Recon/ART F2 for Env4_failure_retry_reversal_async_status")
  needs this, a minimal ART-stub client (or a verifier test step) driving these two routes directly would
  suffice — the routes themselves are real (REAL fidelity for the FTS side), only the caller is missing.

---

## 6. Synthetic data

| Family/table | Field | Source evidence | Type+length | Constraints | Allowed values | FK/relationships | State rules | Distribution matters? | Generation rule | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| `transfers` | `source_id`+`source_type` | migration 00005:17-18,62 | varchar(14)/varchar(50) | `UNIQUE(source_type,source_id)` | any string ≤14 chars for source_id | logical FK to caller's own entity (payout id) | write-once at create; re-POST with same pair returns existing row | no | 14-char alphanumeric id matching caller's id format | EXACT (DDL-derived) |
| `transfers` | `preferred_source_account_id` | migration 00005:21; transfer.go:42 | int(11), nullable | none enforced at DB; app-level marks DIRECT when present | any valid `source_accounts.id` | soft FK → `source_accounts.id` | present ⇒ direct routing skip; absent ⇒ pool selection | yes (drives which code path is tested) | omit for shared merchants, set to the merchant's direct `source_accounts.id` for direct merchants | EXACT |
| `transfers` | `status` | migration 00005:26 | enum | `CREATED,INITIATED,PROCESSED,FAILED,REVERSED,RETRY,USER_PENDING` | as listed | — | FSM per §2.3 | yes | driven by processing, not seeded directly (except for fixture/replay scenarios) | EXACT |
| `source_accounts` | `channel` | migration 00007:16 | enum(9) | `AXIS,CITI,HDFC,ICICI,RBL,YESBANK,M2P,MCS,OCBC` | — | — | — | yes (which bank/rail is exercised) | pick per test scenario | EXACT |
| `source_accounts` | `mozart_identifier` | migration 00007:18; seed comment `fts.sql:45-48` | varchar(40) | "identifier based on which mozart integration will be used" | must match a `[integration.api.<channel>.<VERSION>]` TOML section's version key (e.g. `v1`) | consulted by `channel.GetConfig(version, operation)` | wrong value ⇒ config lookup miss, transfer fails silently or with a config error | yes | use the exact version string configured for the chosen channel (`v1` for RBL in the arena) | EXACT (confirmed by twin bringup note `31_env2_bringup_notes.md:96`) |
| `source_accounts` | `credentials`,`configuration` | migration 00007:20-21 | TEXT, must be JSON | unmarshalled at gateway-call time | any JSON object; seed uses fake `{"client_id":"arena-rbl-client",...}` | — | empty/null causes unmarshal panic/error (seed explicitly sets `{}` where null, `fts.sql:107`) | no (structure only matters) | JSON object with plausible bank-credential key names, fake values | REPRESENTATIVE (structure real, values fake) |
| `source_account_mappings` | `priority` | migration 00011:27 | int, nullable | query filters `WHERE priority IS NOT NULL` (repo.go:282) | any int, lower = higher priority | — | omit to exclude a mapping from selection entirely | yes | ascending int per desired precedence; arena uses `priority=1` uniformly (no precedence tested) | EXACT rule, REPRESENTATIVE arena value (no multi-priority scenario) |
| `source_account_mappings` | `mode` | migration 00011:24 | enum | `IMPS,NEFT,RTGS,IFT,UPI,CT,DUITNOW,IBG` | — | — | — | yes | one row per (merchant, mode) needed | EXACT |
| `source_account_mappings` | `account_type` | migration 00011:23 | varchar, comment "POOL, DIRECT" | not DB-enforced (varchar not enum) despite comment | conventionally `POOL`/`DIRECT` | consulted by `fetchAllSourceAccountMappingForMerchant` default `TypePool` | mismatched string ⇒ silently filtered out (no DB constraint to catch a typo) | yes | must exactly match code's string constants (`account.TypePool`,`account.TypeDirect` — likely `"POOL"`/`"DIRECT"`, not independently re-read this pass) | REPRESENTATIVE (exact string values not re-confirmed from a Go const this pass) |
| `direct_account_routing_rules` | (no `priority`/`routing_enabled` columns) | migration 00020:13-31 | — | table has no down-ranking mechanism, unlike `source_account_mappings` | — | `source_account_id`→`source_accounts.id` (soft FK) | a rule either exists (deterministic) or doesn't (falls to default/fallback) | yes | one row per (merchant,product,channel,mode) needing direct routing | EXACT |
| `channel_information_status` | `status` | migration 00022:17 | tinyint(1) despite storing 0/100 | app treats `100` as the only "up" value (service.go:609) | `100`=up, anything else=down (0 conventionally) | `UNIQUE(channel,mode,mozart_identifier,integration_type,account_type,source_account_type)` | absence of a row for a given key tuple = mapping filtered out entirely (fail-closed) | yes (health gating is load-bearing) | seed one row per `(mode,channel,account_type,mozart_identifier,integration_type,source_account_type)` combination the health query can address — arena seeds 10 rows covering IMPS/NEFT × RBL × {POOL,DIRECT,CURRENT,NODAL} account_type/source_account_type crosses (`fts.sql:112-125`) | EXACT rule, REPRESENTATIVE coverage (only RBL, only 2 modes, no DOWN row ever seeded — failover/downtime scenarios cannot be exercised) |
| `attempts` | `gateway_ref_no` | attempt.go:36 | varchar, nullable | dedupe support via `attempts_unique_keys` | bank-assigned ref, opaque string | — | duplicate detection ⇒ `FailTransfer(FtsDuplicateGatewayRefNo)` | yes for dedupe-testing | synthetic unique string per attempt, e.g. `ARENAUTR000000N` (matches mozart-sim's own `ARENAUTR...` convention, `server.py:75,85,87,94`) | EXACT (real mechanism) / synthetic values obviously fake |
| `attempts` | `re_initiate_count` | attempt.go:47 | int, default 0 | compared against `MaxReInitiateAttemptCount=2` | 0,1,2 (capped) | — | ≥2 ⇒ `CodeAttemptReInitiateCountExhausted` | yes for retry-exhaustion testing | 0 for fresh attempts; set to 2 to test exhaustion path | EXACT |
| `transfer_meta` | `origin_service` | transfer_meta.go:11,29-34 | varchar(255) | `IsIn("API","payouts")` | exactly those two strings | soft FK → `transfers.id` | selects webhook destination branch (§2.4) | yes (drives the a/b/c decision) | `"payouts"` for PS-originated test transfers requiring direct-webhook coverage, `"API"` (or omit the row entirely, since creation is Splitz-gated) for monolith-relay coverage | EXACT |
| Mozart bank fixtures | `bank_status_code` | error_code.go (defaultErrorCodes keys) | string enum, ~140 values | must be a key FTS recognizes or it defaults to `{Pending:true,ErrorType:UNMAPPED}` | any of the ~140 `Status*` constants | — | drives FSM event via `getEventToTrigger` | yes (this is the entire point of a bank fixture) | pick codes spanning `MERCHANT/BBANK/PBANK/INTERNAL/UNKNOWN` `ErrorType`s and `Pending/Processed/Failed/Reversed` combinations for adequate coverage | EXACT vocabulary (real codes), mozart-sim currently covers ~6 of ~140 |

---

## 7. Cannot be derived from repositories

1. **Which action names `PerformAttemptAction` (the `:action` param in `PATCH /v1/attempts/:action`)
   actually supports**, and the full contract of `PUT /v1/attempts/reconcile`
   (`attempt_bulk_action.go:219+`, body not opened this pass — time-boxed, not a missing-artifact problem,
   just not read). *Why it matters*: this is the recon/repair write surface Env4 needs. *Owner*: FTS team.
   *Minimal request*: not actually missing — a follow-up read of `attempt_bulk_action.go:142-260` and
   `transfer.Service.PerformAttemptAction`'s switch statement would close this; flagging here only because
   it wasn't completed in this pass, not because it's unavailable.
2. **Real NEFT-24×7 eligibility internals** (`IsMerchantEligibleForNEFT24x7`, `GetSANEFT24x7EligibilityMap`,
   `FilterSourceAccountMappingsForNEFT24x7`, `fetchBackupPreferredDetail` — `source_account_selector.go`
   lines 1284-1829) — not opened this pass (time-boxed, large file). *Why it matters*: cross-channel
   failover and NEFT off-hours queuing behavior cannot be fully specified without it. *Owner*: FTS team.
   *Minimal request*: none needed — repo is readable, just requires a follow-up pass.
3. **`send_notification` task handler body** — which URL/auth/body it uses for
   `common.NotificationDowntimeV2` vs `common.NotificationPartnerBankDowntime`, and whether it's the same
   code path as the synchronous `SendPartnerBankHealthNotification` in `notification.go` or a separate
   implementation. *Why it matters*: the exact downtime-callback contract (route+body) the task asked for
   is only half-confirmed (V1/`notify_channel_status` path); V2's actual HTTP delivery is unconfirmed.
   *Owner*: FTS team. *Minimal request*: read `internal/tasks/send_notification.go` (exists per task
   registry in `03_fts.md`, not opened this pass).
4. **Real per-merchant Splitz experiment state** for `FireStatusUpdateKafka`, `CreateTransferMetaRollout`,
   `NewMaintainNeftFlow`, and NEFT-24x7-related experiments — these are runtime DB rows in Splitz's own
   store, not derivable from any cloned repo (Splitz repo itself has zero experiment definitions per
   `ARCHITECTURE_DELTA.md`'s closure table). *Why it matters*: without this, the twin's `splitz-stub` answer
   for these specific experiment keys is unknown/unverified, and it silently determines which of (a)/(b)/(c)
   fires for a given test transfer. *Owner*: Payouts/FTS platform team (Splitz admin). *Minimal request*:
   a sanitized export of the current enabled/disabled state (and rollout %) for these ~5 experiment keys —
   schema-only (experiment_id, is_enabled, rollout_percentage) is sufficient, no merchant PII needed.
5. **Production `env.<prod>.toml` overlay for non-RBL banks' amount caps/time windows** — this pass
   confirmed RBL values are not overridden in `env.prod-live.toml`, but ICICI/Axis/Yesbank/IDFC caps beyond
   what's in `env.default.toml` were not cross-checked against every prod overlay file (9+ env-specific
   TOMLs exist). *Why it matters*: if any bank's real prod cap differs from the default-config value quoted
   in §2.2, a fixture/seed built from the default value would misrepresent prod. *Owner*: FTS team.
   *Minimal request*: none needed structurally — just a wider grep pass across all `config/env.*.toml`
   files, time-boxed out of this pass.
6. **Whether `/v1/transfer`'s `X-Origin` header, if absent, truly defaults to `"API"`** — this pass could
   not re-locate the specific default-assignment line (only the "if present" branch of
   `extraTransferMetaFromCtx` was read, lines 1889-1906); the claim is carried from `03_fts.md` as
   INFERRED, not independently re-verified. *Why it matters*: this is the single most incident-relevant
   fact in the whole lane (per `03_fts.md`'s own framing). *Owner*: FTS team. *Minimal request*: none — a
   30-second follow-up read of `service.go` lines ~1906-1930 would close this; not done here due to
   time-boxing, not unavailability.

---

## 8. Fidelity tier verdicts

| Component | Tier | Reason |
|---|---|---|
| FTS `POST /v1/transfer` (route, auth, validation, dedupe) | **REAL** | Twin runs the actual `fts` binary; no divergence found in code read |
| FTS route selection (shared/pool) | **REAL** (code) | Same binary; DB-driven, seed data is the only non-prod element |
| FTS route selection (direct/current-account) | **REAL** (code), **REPRESENTATIVE** (seed breadth) | Only merchant-scoped rule seeded, no default-rule fallback path exercised |
| Transfer/attempt FSM | **REAL** | In-process code, twin runs it unmodified |
| Status propagation (a/b/c decision) | **REAL** (code) / **CONTRACT-FAITHFUL** (env) | Kafka branch structurally disabled in twin (no broker); Splitz-stub answer for the two gating experiments not independently verified |
| Mozart (real `-mock`) | **UNKNOWN-BLOCKED** | Private Go modules (`integrations-utils`, `integrations-go`, `orchestrator`) unavailable; confirmed absent from clones root and not buildable |
| Mozart (mozart-sim substitute) | **REPRESENTATIVE SUBSTITUTE** | Correct envelope shape and a subset of bank_status_code scenarios; ignores gateway/version entirely, ~6 of ~140 classified codes covered |
| Mozart bank-error classification (FTS-side maps) | **REAL** | FTS binary is real; only the upstream bank_status_code inputs are synthetic (from mozart-sim) |
| Workers (queue map) | **REPRESENTATIVE SUBSTITUTE** | 13 of ~40 real worker-queue-map keys present, RBL-only |
| Cron (stuck-payouts, healthy-balance, bank-degradation-alert) | **UNKNOWN** (twin) | Not verified whether the twin runs with `INSTANCE_TYPE=canary` on any pod to activate leader-elected crons — not checked this pass |
| Recon/ART write route (`/v1/attempts/:action`, `reconcile`) | **MISSING** (twin) | Route is real on the FTS side, but no caller/stub exercises it in the twin |
| Downtime notification callback | **UNKNOWN** | V1 code path identified (`notify_channel_status`), V2's actual HTTP delivery (`send_notification` task body) not traced; twin-side receiver not verified |
