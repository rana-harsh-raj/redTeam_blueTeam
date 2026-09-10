    # Lane 01 — API monolith (`api`) internal contracts consumed by Payouts Service (PS) and FTS

Verifies/corrects `reports/raw-findings/20_api_monolith.md`, `14_monolith_dependency_migration_state.md`,
`ARCHITECTURE_DELTA.md` §(a)"API monolith" and W4/W7, against `monolith-stub/server.py` and
`seeds/monolith/*.json`. Scope is deliberately narrower than finding 20: only routes PS/FTS call, or
that the monolith calls into PS, not the merchant-facing `/v1/payouts` create/approve surface (already
covered exhaustively by finding 20 §1-3, cited here only where load-bearing for PS/FTS).

## Scope and sources read

Repo commit: `api` clone at `/private/tmp/.../scratchpad/rzp-payouts-architecture/api` (same clone finding
20 used). Files opened in full or by targeted grep+read (line ranges noted inline throughout):

- `app/Http/Route.php` (grep: `payouts_service`, `update_fts_fund_transfer`, `internal/merchants`,
  `fund_accounts_internal`, `contacts_internal`, `internal_balances_queued`, `on_hold_slas_internal`,
  `actor_info_internal`, `users_internal`, `bas_recon`, `banking_account_statement/payout_update`,
  `balance_update`, `wf-service`, `create_ledger`; full read of `$internalApps['payouts_service']` block
  Route.php:19379-19426)
- `app/Http/Controllers/PayoutController.php:1-120,870-895,1258-1300`
- `app/Http/Controllers/MerchantController.php:2212-2300`, `UserController.php:413-435,847-855`
- `app/Models/Payout/Core.php` (createFTAForPayoutService 8273-8286, getAPIModelPayoutFromPayoutService
  8461-8530, updateStatusAfterFtaRecon 933-1060, handlePayoutProcessed(Base) 4617-4766,
  handlePayoutProcessedForPayoutService/handlePayoutReversedForPayoutService 8866-8916,
  updateWithDetailsBeforeFtaRecon 1370-1418, updateWithDetailsBeforeFtaReconForPayoutService 8769-8864,
  payoutSourceUpdate/statusDetailsSourceUpdate 7786-7819, decrementFreePayoutsForPayoutsService
  7408-7436, createPayoutServiceTransaction/deductCreditsViaPayoutService 8619-8639,
  payoutServiceDualWrite 10219-10274, payoutUpdateByBASRecon 10286-10370, payoutServiceMailAndSms
  10538-10572, dispatchBalanceIdsForQueuedPayoutsToPayoutsService 4083-4104)
- `app/Models/Payout/Processor/Base.php` (createFTAForPayoutService 4654-4740, syncFTSFundTransfer
  1024-1132, deductCreditsViaPayoutService 4884-4988, fetchPricingInfoForPayoutService 4996-5022,
  FTS_TRANSFER_TIMEOUT const 235)
- `app/Models/Payout/Repository.php:5290-5460` (`getPayoutServicePayout`, `getPayoutServicePayoutsByIds`,
  `getPayoutServiceSettings`, `getPayoutServicePayoutIds`, `getPayoutServicePayoutMetaDataForDualWrite`,
  `getPayoutServicePayoutMetaPermanent`, `getPayoutServicePayoutLogs(SortedByIDDesc)`)
- `app/Models/Payout/Status.php:1-100,240-414` (full status maps)
- `app/Models/FundTransfer/Attempt/Core.php:480-545,820-875` (`updateFundTransfer`, `sourceReconByFta`)
- `app/Services/FTS/Base.php` (const URIs 1-160, ctor/auth 160-260, generateRequest/setHeaders/
  sendFtsRequest 260-350)
- `app/Services/FTS/FundTransfer.php:200-560,2050-2075` (`requestFundTransfer`, `makeRequestUsingType`,
  `addTransferBlock`, `shouldFetchSourceFtsFundAccountId`)
- `app/Services/FTS/Constants.php` (full file)
- `app/Services/PayoutService/Status.php`, `Details.php` (full files), and one-line `const …URI` grep
  across all 34 files in `app/Services/PayoutService/*.php` (full outbound-call inventory, §5)
- `app/Models/Reversal/Entity.php:36-59,474-482` (`getPayoutIdAttribute` accessor), `Reversal/Core.php:
  1616-1670`, `Reversal/Validator.php:37-44`
- `app/Models/Merchant/Balance/Entity.php:39-68,95,322,394,732-738` (Type::PRIMARY vs column)
- `app/Models/Payout/Validator.php:127-146,300-420,643-690` (all `payouts_service/*` rule arrays)
- `database/migrations/2014_07_12_083930_create_balance.php`, `2016_10_24_081731_create_features_table.php`,
  `2020_08_13_104459_create_workflow_entity_mapping_table.php`, `2016_12_19_110546_create_reversals.php`,
  `2017_02_20_135839_create_fund_transfer_attempts_table.php`, `2021_06_08_195420_create_payouts_details_table.php`,
  `2021_11_22_093451_create_payouts_status_details_table.php` (full files); `grep -rl Table::BALANCE` /
  `Table::FUND_TRANSFER_ATTEMPT` across all migrations
- `config/applications.php:1271-1290` (`applications.fts` config block)
- `payouts` repo: `pkg/api/fts_create.go` (full), `pkg/api/merchant_config.go` (full), `pkg/api/base.go:1-60`,
  `internal/app/payouts/processor/payoutFTS.go` (full)
- `ENV2_COMPOSE/substitutes/monolith-stub/server.py` (full, 487 lines), `seeds/monolith/{merchants,
  fund_accounts,misc}.json` (full), `seeds/schema-patches/apidb.sql` (full), `seeds/mysql/apidb-ddl/00_init.sql`
  (head), `seeds/mysql/apidb_seed.json` (head), `docker-compose.yml` (grep for `mysql-apidb-stub`,
  `MONOLITH_MERCHANTS_FILE`), `scripts/up.sh:56-60`
- Prior reports read in full: `raw-findings/20_api_monolith.md`, `14_monolith_dependency_migration_state.md`,
  `ARCHITECTURE_DELTA.md:1-80`

Not opened (out of budget, listed in §7): `Adapter\Base::getActorInfo()` body, `Initiator::
sendFTSFundTransferRequest` (async FTS fallback queue consumer), `PayoutsBankingAccount\Core` migration-flag
methods, `CounterHelper` free-payout counter internals, FundAccountController/ContactController bodies.

---

## Production behaviour

### 1. FTA creation — `POST /payouts_service/create_fta/{payout_id}`

**Route**: `create_FTA_payout_service`, internal+admin auth, allowlisted only under `$internalApps['payouts_service']`
(Route.php:19379-19426, cross-checked against finding 20 §1). Controller
`PayoutController@createFTAForPayoutService(string $payoutId)` (PayoutController.php:46-51) → `Service::
createFTAForPayoutService` (Service.php:283-286, pass-through) → `Core::createFTAForPayoutService` (Core.php:8273-8286).

**Request**: no JSON body — PS's client (`payouts/pkg/api/fts_create.go:66-77`, `GetFTSCreateRequest`) builds
`RequestBody{}` with **no `Data` field set**; the payout id travels only in the URL path
(`FTSCreate + payout.GetID()`, `fts_create.go:16` `FTSCreate = "/payouts_service/create_fta/"`).

**How the monolith loads the payout — CONFIRMED CORRECTION to the twin's design**: the monolith does **not**
call PS's HTTP API to fetch the payout. `Core::createFTAForPayoutService` (Core.php:8280) calls
`getAPIModelPayoutFromPayoutService($payoutId)` (Core.php:8461-8530), which calls
`Repository::getPayoutServicePayout($id)` (Repository.php:5313-5323):
```php
return \DB::connection($this->getPayoutsServiceConnection())
    ->select("select * from $tableName where id = '$id' limit 1");
```
i.e. the monolith holds a **live, second MySQL connection directly into PS's own database**
(`Connection::PAYOUT_SERVICE_DATABASE`, cross-referenced in finding 20 §2/§8) and does a raw `SELECT … WHERE
id='<payout_id>'` — a **deterministic, O(1) lookup by primary key**, not a merchant-scoped API call and not a
brute-force search. The same pattern (`getPayoutServicePayout`) backs `payoutSourceUpdate`,
`statusDetailsSourceUpdate`, `payoutServiceMailAndSms`, `createPayoutServiceTransaction`, and (for reads of
prior status) the dual-write path — this is the **single mechanism** the monolith uses everywhere it needs
"the current PS payout row," not a per-endpoint special case.

Merchant resolution: `getAPIModelPayoutFromPayoutService` (Core.php:8474-8480) takes `merchant_id` straight off
the fetched PS row (`$psPayout->merchant_id`) and does `(new Merchant\Repository)->findOrFail($merchantId)`
against the monolith's own local `merchants` table — i.e. **merchant identity is derived from the PS row's own
`merchant_id` column, not from any header or any brute-force credential probing.**

Account type / mode / channel: none of these are read from the PS row directly by this handler — they come
from the monolith's own `banking_account`/`balance` entities associated to the payout
(`optional($payout->balance)->getAccountType()`, Processor/Base.php:4674) once the reconstructed `Entity`
model is hydrated and `$payout->reload()`'d against local tables via the FTA's `source` relation.

**Body**: after loading, `Core::FundAccountForFTSRequestFromContactAndFundAccountService($payout)`
(Core.php:8289-8330) optionally reloads fund-account attributes from CFA (per-merchant migration flag
`merchantMigratedToPayoutServiceByMerchantIdAndBalanceIdOnMonolithProxyWithSplitzFailure`), then
`Processor\Base::createFTAForPayoutService(Entity $payout)` (Base.php:4654-4740) runs under a mutex
(`FTA_CREATION_PAYOUT_SERVICE.$payoutId`), and:
1. Guard: only proceeds if `status ∈ {CREATED, INITIATED}` AND no existing FTA for this source AND
   (`transaction_id` is set OR `balance.account_type === DIRECT`) (Base.php:4687-4690). Otherwise, if
   `transaction_id` is null, returns `{"status": <status>, "error": "Ledger entry not found in payout."}`
   without creating anything (Base.php:4708-4714) — a distinct, silent no-FTA-created success response the
   twin does not model.
2. `DownstreamProcessor::processCreateFundTransferAttempt()` — creates the local `fund_transfer_attempt` row.
3. `makeFeeRecoveryOfTypeDebitForPSCAPayout($payout)` — best-effort, swallowed on error (Base.php:4742-4773).
4. `syncFTSFundTransfer($payout)` (Base.php:1024-1132) — the actual FTS call, described below.
5. Returns `{Entity::STATUS => $status, Entity::ERROR => null}` — `Entity::STATUS='status'`,
   `Entity::ERROR='error'` (Payout/Entity.php:108,160) — **this response shape is exactly what the stub
   returns**, CONTRACT-FAITHFUL.
6. On any `\Exception` (including from `syncFTSFundTransfer`, which itself swallows its own exceptions —
   see below — so this outer catch mostly guards mutex/DB errors): `{"status": null, "error": <exception message>}`.

**`syncFTSFundTransfer` → the actual FTS request** (Base.php:1024-1132, `FundTransfer.php:204-217,277-356,
424-551`):
- `$transferService->setRequestTimeout(self::FTS_TRANSFER_TIMEOUT)` — `FTS_TRANSFER_TIMEOUT = 1`
  (**one second**, Processor/Base.php:235). This is a hard 1s HTTP timeout for the *synchronous* leg only.
- `$ftsResponse = $transferService->requestFundTransfer($otp)` → `makeRequestUsingType()` builds the body,
  then POSTs `FUND_TRANSFER_CREATE_URI = '/transfer'` (FTS/Base.php:56).
- **Auth to FTS**: HTTP Basic, `key`/`secret` = `config('applications.fts')[mode]['fts_key'/'fts_secret']`
  (FTS/Base.php:178-183; `config/applications.php:1271-1290`) — a **static monolith-wide service credential**,
  the same for every merchant/payout, not merchant-scoped.
- **Exact request body** (`FundTransfer.php:313-356,424-551`):

| Field | Path | Type | Notes |
|---|---|---|---|
| `product` | top | string | `payout`/`payout_refund`/`ES_ON_DEMAND`/`customer_wallet`/`penny_testing` per payout subtype (313-311) |
| `merchant_id` | top | string(14) | `$this->fta->merchant->getId()` |
| `transfer.preferred_mode` | transfer | string | `$fta->getMode()` |
| `transfer.amount` | transfer | int | `$source->getAmount()` (or `getBaseAmount()` for refunds) |
| `transfer.narration` | transfer | string | `$fta->getNarration()` |
| `transfer.source_id` | transfer | string | `$fta->getSourceId()` = payout id (bare, no `pout_` prefix) |
| `transfer.source_type` | transfer | string | `payout` |
| `transfer.initiate_at` | transfer | int (epoch) | `$fta->getInitiateAt()` |
| `transfer.preferred_channel` | transfer | string | FTA channel, with 2 hardcoded swaps: YESBANK→ICICI for non-DIRECT balances (line 438-443), AMAZONPAY→`amazon_pay_fts` (448-451) |
| `transfer.preferred_source_account_id` | transfer | int, **conditional** | present only when `shouldFetchSourceFtsFundAccountId()` returns true (472-481) — see below |
| `transfer.request_meta.*` | transfer | object, conditional | populated for RX-wallet purpose, MasterCard-send, merchant-detail block, only when feature/flags apply (486-541) |
| `transfer.is_batch` | transfer | bool | `$fta->source->hasBatch()`, if the source type implements it (543-548) |
| `merchant_category.category`/`mcc` | top | string | `merchant->getCategory2()`/`getCategory()` |
| `merchant_category.onboarded_time` | top | int, conditional | from `banking_account` (local or BAS-sourced) creation time |
| `account.*` | top | object | bank_account/vpa/card/wallet block per `accountType` |
| `2fa.otp` | top | string, conditional | only if an OTP is supplied (ICICI CA 2FA path) |

**No `origin_service`, `is_ps`, or any "this came from Payouts Service" marker field exists anywhere in this
request** (exhaustive grep of `FTS/Constants.php` and `FundTransfer.php`) — **CORRECTION to the lane brief's
own framing**: the monolith uses the *identical* FTS-request-building code path (`FundTransfer::
makeRequestUsingType`) for PS-triggered FTA creation and classic monolith-native payouts; FTS cannot and does
not distinguish PS-origin transfers from classic ones by any field in this request.

**"Direct" (current-account) variant** — `shouldFetchSourceFtsFundAccountId($source, $channel)`
(FundTransfer.php:2053-2073):
```php
if ($balanceAccountType === DIRECT and in_array($channel, Channel::getNonTransactionChannels())) return [true, false];
if ($balanceAccountType === SHARED and (isSubMerchantOnDirectMasterMerchant() or isSubAccountPayout())) return [true, true];
return [false, false];
```
When true, `transfer.preferred_source_account_id = (int) $source->getSourceFtsFundAccountId($isSubAccountPayout)`
is added (line 478-481) — this is what makes FTS treat the transfer as DIRECT (matches the stub's comment,
confirmed CONTRACT-FAITHFUL on the *mechanism*, though the twin's trigger condition — "account_type==direct"
only — is a simplification of the real two-branch condition above, notably missing the SHARED sub-merchant
case entirely).

**Failure propagation to PS**: `syncFTSFundTransfer` wraps the whole FTS call in try/catch
(Base.php:1048-1131); on **any** throwable (including the 1s timeout almost certainly firing routinely for a
real bank round-trip) it does **not** rethrow — it falls back to `(new Initiator)->sendFTSFundTransferRequest($fta, $otp)`,
which queues the FTA for **async** retry (queue not read in this pass — flagged §7), and `createFTAForPayoutService`'s
outer return is still `{"status": <payout's current status, still CREATED/INITIATED>, "error": null}` — i.e.
**a same-request FTS timeout is invisible to PS**: PS gets a success-shaped response even though FTS was never
actually reached synchronously, and the transfer creation completes later, asynchronously, out of band. **The
stub does not reproduce this fallback at all** — its `_create_fta` treats any non-200/201 FTS response as a
hard `{"status": payout.status, "error": "fts transfer create failed: …"}}`, which is *more* honest about
failure than production, but not contract-faithful to the "swallow and requeue" behaviour real code has.

**On the PS side** (`payouts/internal/app/payouts/processor/payoutFTS.go:18-83`): PS moves the payout to
`initiated` (`payout.OnEvent(EventInitiated)` + `repo.Update`) **before** calling `create_fta`
(payoutFTS.go:25-39) — confirms the stub's comment that "PS then moves the payout to initiated itself." If
`FTSCreateResponse.Error` is non-empty, PS wraps it as `ErrorPayoutFTSCreateFailure` and only logs/Slack-alerts
(payoutFTS.go:67-80) — **no rollback of the `initiated` state is performed by PS on this path**; the payout
waits for the FTS status webhook (§2) or a later reconciliation/retry mechanism (not traced) to resolve it.

---

### 2. FTS webhook relay — `POST /update_fts_fund_transfer`

Route: `update_fts_fund_transfer` → `FundTransferAttemptController@updateSource` (Route.php:4021,
allowlisted **only** for `fts` credential, Route.php:18714-18719 per finding 20 — re-cross-checked, still
holds). Confirmed unchanged from finding 20 §4; the additions below are line-level verification plus the
exact status maps and call ordering the brief asked to have quoted.

**`Attempt\Core::updateFundTransfer($input)`** (Attempt/Core.php:487-560):
1. Validate `fts_status_update`, lowercase `status`.
2. Look up FTA by `fund_transfer_id`, else by `(source_id, source_type)` (503-522).
3. `AttemptStatus::isValidStateTransition($fta->getStatus(), $input['status'])` — if false, **return early**
   with `{"message": "webhook update skipped due to invalid state transition"}`, no further processing
   (524-528).
4. `updateFtaWithInput()` + `$this->repo->fund_transfer_attempt->saveOrFail($fta)` — **the FTA row commit
   happens before any PS/source call** (530-537).
5. `updateSourceEntityByFta($fta, $input)` → `sourceReconByFta($source, $ftaData)` (Attempt/Core.php:835-875):
   ```php
   if (method_exists($sourceCore, 'updateWithDetailsBeforeFtaRecon'))  { $sourceCore->updateWithDetailsBeforeFtaRecon($source, $ftaData); }
   if (method_exists($sourceCore, 'updateStatusAfterFtaRecon'))       { $sourceCore->updateStatusAfterFtaRecon($source, $ftaData); }
   ```
   **CONFIRMED ordering: details-before-status, always, for every source type that implements both methods** —
   directly matches the stub's `_update_fts_fund_transfer` sequencing (`update_payouts_details_with_fts` POST,
   then `update_payouts_with_fts` PATCH). Exceptions are only rethrown for `source_type === PAYOUT`
   (Attempt/Core.php:870-873); all other source types swallow.

**Details call** — `Payout\Core::updateWithDetailsBeforeFtaRecon` (Core.php:1370-1409) → (per-merchant
migration-mutex wrapper, gated by Splitz `NON_TERMINAL_MIGRATION_HANDLING`, same 180s
`PAYOUT_MUTEX_LOCK_TIMEOUT`) → `updateWithDetailsBeforeFtaReconBase` (1418+) → for PS-owned payouts →
`updateWithDetailsBeforeFtaReconForPayoutService` (Core.php:8769-8850), which builds:

| Field | Source | Required at PS? |
|---|---|---|
| `source_id` | added by `Details::updatePayoutDetailsViaFTS` itself (Details.php:25-27) | yes |
| `failure_reason` | `ftaData[FAILURE_REASON]` | no |
| `remarks` | `ftaData[REMARKS]` | no |
| `fund_transfer_id` | `ftaData[FTS_TRANSFER_ID]` | **required** per PS DTO (finding 20/stub comment) |
| `mode`, `channel`, `utr`, `bank_status_code`, `return_utr`, `gateway_ref_no`, `narration` | `ftaData[*]`, only when non-empty | no |
| `status_details.{beneficiary_bank,processed_by_time,reason}` | derived | no |

then `$this->payoutDetailsServiceClient->updatePayoutDetailsViaFTS($payout, $input)` → **POST
`/payouts/update_payouts_details_with_fts`** (`Details.php:12,25-31`) — matches the stub's field list closely;
CONFIRMED CONTRACT-FAITHFUL.

**Status call** — `updateStatusAfterFtaRecon(Entity $payout, array $ftaData)` (Core.php:933-1058):
```php
$status = Status::getPayoutStatusFromFtaStatus($payout, $ftaStatus);   // Status.php:415-421
```
```php
public static $ftaToPayoutStatusMap = [
  Entity::DEFAULT => [ Entity::DEFAULT => [CREATED=>created, INITIATED=>initiated,
                        REVERSED=>reversed, FAILED=>reversed, PROCESSED=>processed] ],
  AccountType::SHARED => [ Entity::DEFAULT => [ ...same as above... ], Channel::YESBANK => [] ],
  AccountType::DIRECT => [
      Entity::DEFAULT => [],   // no default for DIRECT — must hit an explicit channel branch
      Channel::RBL   => [CREATED=>created, INITIATED=>initiated, REVERSED=>reversed, FAILED=>failed, PROCESSED=>processed],
      Channel::ICICI => [ same as RBL ],
      Channel::AXIS  => [ same as RBL ],
      Channel::YESBANK => [ same as RBL ],
      Channel::IDFC  => [ same as RBL ],
  ],
];
// lookup: $map[$accountType][$channel][$ftaStatus] ?? $map[DEFAULT][DEFAULT][$ftaStatus]
```
(Status.php:243-309,415-421, quoted verbatim). **Precise correction to the stub's simplification** ("direct
⇒ failed, else ⇒ reversed"): the FAILED→FAILED remap applies **only** to DIRECT accounts on channel ∈
{RBL, ICICI, AXIS, YESBANK, IDFC}. A DIRECT-account payout on any *other* channel falls through the `??`
to the DEFAULT/DEFAULT map and gets **REVERSED**, not FAILED — the stub's binary `"failed" if account_type==
"direct" else "reversed"` (server.py:403-410) is right for every channel actually seeded in the arena (`rbl`)
but is not the general rule.

Then, by resolved status (933-1058):
- `PROCESSED` → `handlePayoutProcessed(...)` → (Splitz-gated 180s mutex, `NON_TERMINAL_MIGRATION_HANDLING`) →
  `handlePayoutProcessedBase` → for PS-owned, non-IRCTC payouts: `handlePayoutProcessedForPayoutService`
  (Core.php:8866-8891) → `payoutStatusServiceClient->updatePayoutStatusViaFTS($id, 'processed', "", "",
  {fts_status, fts_fund_account_id?, fts_account_type?})`.
- `REVERSED` → (unless high-TPS-with-sub-balance) `handlePayoutReversed(...)` → for PS-owned:
  `handlePayoutReversedForPayoutService` → `reversePayoutService($payout, ...)` (Core.php:8641-8700ish) →
  same `payoutStatusServiceClient->updatePayoutStatusViaFTS($id, 'reversed', $failureReason, $bankStatusCode, {...})`.
- `FAILED` → `handlePayoutFailed(...)` (not fully traced this pass, same client per finding 20 §4/8875-ref).
- `CREATED` / `INITIATED` → **no-op**, no PS call at all (Core.php:1050-1052).
- **unknown status** → `$this->trace->warning(UNKNOWN_FTA_STATUS_SENT_TO_PAYOUT, $ftaData)` and **falls out
  of the switch with no PS call** (Core.php:1054-1057) — CONFIRMS the stub's behaviour of only handling the
  known set, though the stub always relays *something* for any status it receives; real code relays **nothing**
  for an unrecognized FTA status.

**Exact PS body** — `Services\PayoutService\Status::updatePayoutStatusViaFTS()` (Status.php:19-58, quoted):
```php
$request = ['source_id'=>$payoutId, 'status'=>$status, 'failure_reason'=>$failureReason, 'bank_status_code'=>$bankStatusCode];
if (isset($ftsInfo[FTS_FUND_ACCOUNT_ID])) $request['fts_fund_account_id'] = strval(...);
if (isset($ftsInfo[FTS_ACCOUNT_TYPE]))    $request['fts_account_type']    = ...;
if (isset($ftsInfo[FTS_STATUS]))          $request['fts_status']          = ...;
// PATCH /payouts/update_payouts_with_fts
```
— **exact field-for-field match with the stub's `ps_body`** (server.py:411-422). CONTRACT-FAITHFUL.

**Retry/timeout**: `updatePayoutStatusViaFTS`/`updatePayoutDetailsViaFTS` share `Base::makeRequestAndGetContent`,
which — per finding 20 §4 (not independently re-read line-by-line this pass, cross-checked against the calling
code's outer try/catch at `updateFundTransfer:560-583` which re-throws) — makes a single HTTP call and
propagates any failure back to the FTS webhook caller (i.e. **FTS's own webhook-delivery retry is what retries
this**, the monolith adds none). Matches the stub's single-attempt PATCH with no retry loop (server.py:446-459).

**Idempotency / ordering guard**: `repeatedFtsStatusUpdateForTerminalStatePayout($payout, $status)`
(Core.php:941-944) short-circuits if the payout is already in a terminal state matching the incoming status —
not modeled by the stub at all (the stub always relays, even for a payout the arena's PS already terminalized).

---

### 3. Other `payouts_service/*` internal routes

| Route | Handler | Request required fields (validator rule, file:line) | What it does | Response | Stub verdict |
|---|---|---|---|---|---|
| `payouts_service/source_update` | `Core::payoutSourceUpdate` (Core.php:7786-7803) | `payout_id, source_details[], previous_status, expected_current_status` (`$payoutsSourceUpdateRules`, Validator.php:395-400) | Loads PS payout via direct-DB fetch (§1 mechanism), then `SourceUpdater::dispatchToQueue($mode, $payout, $previousStatus, $expectedCurrentStatus)` — **async, queue-backed** (queue `payout_source_updater` per finding 20 §12) | `{"sources_updated": true}` unconditionally | stub's static `{"sources_updated": True}` matches the *field name and unconditional-true value*; the real side-effect (queue dispatch to update downstream source subscribers) has no stub analogue — REPRESENTATIVE, response-shape-faithful only |
| `payouts_service/status_details_source_update` | `Core::statusDetailsSourceUpdate` (Core.php:7805-7819) | `payout_id, status_details{}, source_details[]` (`$statusDetailsSourceUpdateRules`, Validator.php:402-406) | Sets attributes on the (reloaded) payout, then `PayoutsStatusDetails\Core::statusDetailsSourceUpdate($payout)` (PayoutsStatusDetails/Core.php:136-157): iterates `Payout\SourceUpdater\Factory::getUpdaters($payout,$mode)`, calls `.update()` on each **whitelisted** subscriber, and returns the list of **subscriber/source *types* notified** (e.g. workflow/webhook-class source types), via `$this->subscriberClassSourceTypeMap` | `{"sources_updated": [<source type strings for each notified subscriber>]}` | **INCORRECT in the stub**: `_status_details_source_update` (server.py:150-157) returns `[sd["source_id"] for sd in req["source_details"]]` — i.e. it **echoes the request's own source ids back**. Real code never echoes input; it returns which downstream *subscriber types* were notified, a value the server has no way to derive from the request alone. |
| `payouts_service/deduct_credits` | `Processor\Base::deductCreditsViaPayoutService` (Base.php:4884-4988) | `payout_id, fees:int, tax:int, status, merchant_id, balance_id` (`$deductCreditsViaPayoutServiceRules`, Validator.php:653-660) | **Stateful, idempotent**: checks `Credits\Transaction\Core::checkIfCreditsDeductedForSource($payoutId, PAYOUT)` first — if already deducted, returns the *original* fee/tax with `tax` zeroed and `credits_used:true` without re-deducting (4904-4917); else runs `DownstreamProcessor::processAdjustFeeAndTaxesIfCreditsAvailable()`, which can **reduce `fees`/`tax`** if merchant reward-fee credits cover them | `{"fees": <possibly reduced>, "tax": <possibly reduced or 0>, "credits_used": bool}` | **INCORRECT/no-op in the stub**: `_deduct_credits` (server.py:120-126) always echoes the request's `fees`/`tax` back verbatim and always returns `credits_used: False` — the twin cannot reproduce a credit-covered (free) payout fee waiver at all |
| `payouts_service/reverse_credits` | `Reversal\Core::reverseCreditsViaPayoutService` (Reversal/Core.php:1616-1670+) | `entity_type in:payout,reversal`, `payout_id, merchant_id, balance_id` required, `reversal_id` required_if entity_type=reversal, `fee_type` present (`$reverseCreditsViaPayoutServiceRules`, Reversal/Validator.php:37-44) | Idempotent: checks `Credits\Transaction\Core::getReverseCreditTransactionsForSource` first, skips if already reversed, else `reverseCreditsForSource(...)` | not fully traced past 1670 (out of budget) — response shape not confirmed | Stub (`_reverse_credits`, server.py:130-136) is a pure log-sink returning `{"success": true}` always — no idempotency, no field validation; REPRESENTATIVE at best |
| `payouts_service/fetch_pricing_info` | `Processor\Base::fetchPricingInfoForPayoutService` (Base.php:4996+) | **7 required fields**: `payout_id, merchant_id, balance_id, amount:int≥1, method, mode, channel` (all required — `channel` and `method` and `balance_id` are NOT optional in production), `purpose/user_id/fee_type` optional (`$payoutServiceFetchPricingInfoRules`, Validator.php:666-679) | Builds a real `Payout\Entity` with method/amount/mode/channel/merchant/balance and routes through the actual pricing/charge-collections engine | fees/tax/pricing_rule_id (exact shape not fully traced past entity build) | **Materially incomplete in the stub**: `_fetch_pricing_info` (server.py:95-116) ignores `balance_id`, `amount`, `method`, `channel` entirely and keys only on `(merchant_id, mode)` (plus purpose/fee_type overrides) against a static seeds/pricing.json plan — production pricing can and does vary by channel/method/amount and the twin cannot reproduce that variance |
| `payouts_service/decrement_free_payouts` | `Core::decrementFreePayoutsForPayoutsService` (Core.php:7408-7436) | `merchant_id, balance_id, payout_id` (`$decrementFreePayoutForPayoutsServiceRules`, Validator.php:397-401, cross-referenced) | Loads the **real balance** by `balance_id`; **returns `null` if `balance.type !== Merchant\Balance\Type::BANKING`** (7416-7425, an unmodeled early-exit); else `CounterHelper::fetchCounterAndDecreaseFreePayoutsConsumed($balance)` — counter keyed by **balance**, not merchant | `CounterHelper`'s return shape (not traced) | Stub keys its `FREE_PAYOUT_COUNTERS` dict by **`merchant_id`** (server.py:192-200), not `balance_id` as production does, and never models the non-banking-balance null-return path; REPRESENTATIVE only |
| `payouts_service/free_payout_rollback` | `PayoutController@freePayoutRollback` (PayoutController.php:873-880) → `Service::postFreePayoutRollback` (not traced past entry) | not traced | Admin/migration rollback tool (per finding 20, "Migration-only" lifecycle) | not traced | **MISSING from the stub entirely** — no route registered in `ROUTES` (server.py:466-484); low materiality (migration-only tool, not exercised by golden create/status flows) |
| `payouts_service/mail_and_sms` | `Core::payoutServiceMailAndSms` (Core.php:10538-10572+) | `entity in:payout,transaction`, `type` required, `entity_id` required\|size:14, `metadata` array (`$payoutServiceMailAndSmsInputRules`, Validator.php:300-305) | Loads PS payout via direct-DB fetch (§1 mechanism) for `entity=payout`, then dispatches a real notifier (`Notifications\Factory::getNotifier($type, $payout, $metadata)->notify()`) — for `entity=transaction`, validates a second nested rule set (`$payoutServiceTxnMailDataRules`: `payout_id, amount, created_at:epoch`) | `{}` (not traced past dispatch, assume 200 empty) | Stub is a pure log-sink (server.py:161-167), no validation, no actual notification dispatch — REPRESENTATIVE |
| `payouts_service/create` | `PayoutController@createPayoutEntry` → `Service::createPayoutEntry` → `Core::createPayoutEntry` (Core.php:8197+) | not traced this pass (finding 20 does not cover it either) | not traced | not traced | not modeled by the stub; **UNKNOWN**, flagged §7 |
| `payouts_service/create_ledger` | `PayoutController@createPayoutServiceTransaction` → `Core::createPayoutServiceTransaction` (Core.php:8619-8625) → `Processor::createPayoutServiceTransaction` (Processor/Base.php:4781+) | `id:size14, queue_if_low_balance:bool?` (`$payoutServiceTransactionCreateRules`, Validator.php:646-649) | Creates the local ledger/transaction entry for a PS payout, using the same `getAPIModelPayoutFromPayoutService` fetch pattern (Base.php:4796-4799) | not traced past entity build | not modeled by the stub at all — no route in `ROUTES`; **MISSING** |
| `payouts_service/dual_write` | `Core::payoutServiceDualWrite` (Core.php:10219-10274) | `payout_id:size14, timestamp:epoch` (`$payoutServiceDualWriteInputRules`, Validator.php:325-328) plus `entity_type` branch (`account_statement_bas` / `accounts_bas` / default-payout) | Default (payout) branch: `PayoutServiceDualWrite::dispatch($mode, $input)` — **queue-backed** (queue `payout_service_dual_write`, `MAX_RETRY_ATTEMPT=5` per finding 20 §8, cross-checked, still accurate) | **`['status' => 'success']`** (Core.php:10273) | **CORRECTION to the twin**: `_dual_write` (server.py:175-183) returns `{"status": "queued"}`, not `"success"` as production does. Field name matches, literal value does not. `PS_DUAL_WRITE_MODE=fail` (server.py:171-177, 500 error) reproduces the 2026-09-03 "table doesn't exist" incident (ARCHITECTURE_DELTA W4) as an *optional* mode — this is a reasonable substitute-only behaviour since the queue-based async retry (5 attempts) inside the real handler is not otherwise reproducible synchronously in an HTTP stub |

---

### 4. `GET /internal/merchants/{id}` and sibling internal-lookup routes

**Route**: `internal_merchant_fetch` → `MerchantController@internalGetMerchant($merchantId)`
(Route.php:619, MerchantController.php:2261-2266) → `Merchant\Service::internalGetMerchant($merchantId)`
(Service.php:7271-7360+, read in full to line ~7360).

**CORRECTION — the twin over-models this response, but harmlessly.** PS's Go client
(`payouts/pkg/api/merchant_config.go:18-42`) declares `MerchantConfigFetchResponse{ Merchant, MerchantDetails
}` with **only** these two nested structs — Go's default `encoding/json` unmarshal silently ignores every
other field in the response. So although the **real** monolith response (Service.php:7271-7360) additionally
includes `merchant_document` (per-document-type metadata), `support_details` (payout-link merchant support
settings), `merchant.org_feature`/`org_details`, `merchant.is_transacted`, `merchant.is_submerchant`,
`merchant.methods`, and a separately-fetched `business_details` block — **PS never reads any of it**. PS's
consumption is limited to exactly:

| Field (JSON path) | Go tag | Source (real) |
|---|---|---|
| `merchant.id/name/email/activated/live/billing_label/business_banking/hold_funds` | as named | `$merchant->toArrayPublic()` (Service.php:7283) |
| `merchant.feature` (**singular** key) | `feature` | `$merchant->getEnabledFeatures()` (Service.php:7329) — assigned *after* `toArrayPublic()`, overwriting whatever `toArrayPublic` put there |
| `merchant.org_id/created_at/category(mcc)/country_code/category2(mcc2)/pricing_plan_id/purpose_code` | as named | merchant row / `toArrayPublic` |
| `merchant_detail.business_type/business_name/company_pan/business_registered_*/iec_code` | as named | `getMerchantDetailForInternalGetMerchant($merchantDetail)` (Service.php:7311, body not traced) |

This exactly matches the stub's `seeds/monolith/merchants.json` `merchant`/`merchant_detail` blocks field-for-
field (server.py:83-90, `_get_merchant`) — **CONTRACT-FAITHFUL for what PS actually parses.** The stub's
extra top-level keys (`account_type`, `channel`, `balance_id`) are **not part of the real production response
schema at all** — they are private bookkeeping the arena's own Python handlers (`_create_fta`,
`_internal_balances_queued`) read off the same in-memory dict, never serialized to PS over this route in
production. **Because PS's JSON unmarshal ignores unknown fields, this extra data is inert** — it does not
change PS's behaviour, but an engineer reading the stub's HTTP response as "the production contract" would be
misled into thinking PS gets `account_type`/`channel`/`balance_id` from this route. **Per finding 14
(`merchant/core.go:189`, `bankingAccount/core.go:152,286,603`), PS actually gets banking-account/balance/
channel data from CFA and XBalances fallbacks, never from `/internal/merchants/{id}`.**

**`fund_accounts_internal/fa_{id}`** (GET, `fund_account_get_internal`, Route.php:3983, allowlisted for
`payouts_service` — confirmed at Route.php:19386): controller/response body not read this pass (out of
budget); stub's seed shape (server.py:203-210, `seeds/monolith/fund_accounts.json`) matches the Go
`FundAccountResponse` field names cited in finding 14 (`fetch_fund_account.go`) but was not independently
re-verified against the monolith's own serializer in this pass — **INFERRED**, not CONFIRMED, unlike
`/internal/merchants/{id}` above.

**`contacts_internal`**: routes exist (`contact_get_internal` etc., Route.php:3945-3951 per finding 20) but
**are not present in the `payouts_service` allowlist block** read in full above (Route.php:19379-19426) —
only `contact_create_internal` appears there. **CORRECTION/OPEN QUESTION**: if PS ever needs to *fetch* (not
just create) a contact from the monolith directly, no code path was found granting it that credential; this
may mean contact-fetch always goes through CFA in practice for PS, with the monolith's `contacts_internal`
GET/list/update reachable only by other allowlisted apps (cross-border-import, vendor-payments, etc., per
finding 20's app-list). The stub has no `contacts_internal` route at all (`ROUTES` dict, server.py:466-484) —
consistent with this being unused by PS. Flagged UNKNOWN/low-priority in §7.

**`internal_balances_queued`** (GET, allowlisted `payouts_service`, Route.php:19407): `Merchant\Balance\
Service::fetchBalancesForBalanceIds()` (Service.php:93, per finding 20 §9, not independently re-read this
pass) does `$this->repo->balance->getBalancesForBalanceIds($balanceIds)` against the **monolith's own local
`balance` table** — i.e. in production this is a plain local-DB batch SELECT, no ledger call. **CORRECTION to
the stub's design note**: the stub's own comment (server.py:329-334) already flags this accurately — "In
production the monolith's `balance` table is kept in step with the ledger by its journal-created consumer; the
arena has no such consumer, so this stub reads … from ledger-api instead" — this is a deliberate, documented
substitution, not a silent drift; verdict REPRESENTATIVE (by the stub's own admission).

**`merchant/on_hold_slas_internal`** (POST, allowlisted `payouts_service`, Route.php:19406) →
`PayoutController@getOnHoldMerchantSlas` (Route.php:3410) — body not traced past controller entry; stub
returns a static `merchant_slas` map from `seeds/monolith/misc.json` — REPRESENTATIVE, response-shape only
(field name `merchant_slas` matches finding 14's `FetchMerchantSlasResponse` DTO).

**`actor_info_internal/{user_id}`** (GET) — **CORRECTION to `seeds/monolith/misc.json`'s own uncertainty
note** ("route not confirmed in findings/20"): the route **does exist** — `fetch_actor_info_internal` →
`UserController@getActorInfo` (Route.php:2963) → `User\Service::getActorInfo` (Service.php:3461-3469) →
`User\Core::getActorInfo` (Core.php:5118-5133), and **is** allowlisted for `payouts_service`
(Route.php:19405, confirmed by direct read of the full allowlist block). Its real implementation sets the
looked-up user onto `basicauth` and delegates to `Adapter\Base::getActorInfo()` (body not traced) — almost
certainly deriving `actor_property_key/value` (role) dynamically from the user↔merchant relationship, not a
static per-user role field as the stub hardcodes (`seeds/monolith/misc.json`'s fixed
`finance_l1`/`finance_l2`/`owner` rows) — REPRESENTATIVE, not CONTRACT-FAITHFUL on the derivation mechanism,
though the *response field names* (`actor_id, actor_type, actor_property_key, actor_property_value`) could not
be independently confirmed (out of budget) beyond finding 14's DTO citation.

**`users_internal`** (POST, bulk) — **same correction**: the route exists —
`multiple_users_fetch_internal` → `UserController@getMultipleUsers` (Route.php:2896) → `User\Service::
getMultipleUsers` (Service.php:5373-5397+, read in full) — **and is** allowlisted for `payouts_service`
(Route.php:19404). Real response is `{<user_id_or_email>: <full user->toArray()> + is_password_set: bool}`,
keyed by whichever identifier (`user_ids`/`user_emails`/`user_contacts`) was supplied — a materially richer,
full-user-entity shape than the stub's trimmed `{id, name, email, role}` per user (server.py:227-235,
`seeds/monolith/misc.json`). REPRESENTATIVE only.

**`banking_account_statement/payout_update`** and **`bas_recon_payout_update`**: cross-checked, confirmed
unchanged from finding 20 §9 (caller = PS only, via `payouts_service` credential, Route.php:19382). Body
(`Validator::PAYOUT_UPDATE_BY_BAS_RECON`, `$payoutUpdateByBasReconRules`, Payout/Validator.php:308-315):
`bas_id, entity_id, entity_type(in:payout,payout_reversal), merchant_id, transaction_date` required;
`converted_from_external, utr, grn, cms_ref_no` optional. `Core::payoutUpdateByBASRecon`
(Core.php:10286-10370, read in full): for `entity_type=payout` sets `transaction_id`/`transaction_type` on the
local payout; for `entity_type=payout_reversal`, if the local payout's status is `FAILED`, calls
`reversePayout(...)` and dispatches `api.payout.reversed`, else attaches to the existing reversal — **for any
PS-owned (or not-locally-found) payout, forwards instead to
`payoutServiceBankingAccountStatementClient->updatePayoutAfterBASRecon($input)`**, POST
`/payouts/banking_account_statement/payout_update` (`PayoutService/BankingAccountStatement.php:12`, confirmed
by direct grep this pass). Not modeled by the stub at all (no route in `ROUTES`) — **MISSING**, low-materiality
unless BAS-recon flows are in scope for the golden runs.

---

### 5. Monolith → PS outbound calls (beyond the FTS-relay path)

Full inventory from `const … URI` grep across all 34 files in `app/Services/PayoutService/*.php`
(one call site per file; base URL is PS's own host, same as the FTS-relay target
`payouts-api:9400` in the arena):

| Client class | URI | Trigger (traced where possible) |
|---|---|---|
| `Create` | `/payouts`, `/payouts/payouts_internal`, `/payouts/internal_contact_payout`, `/payouts/rzp_fees_payout`, `/payouts/set_pricing_rule_info` | classic-flow payout-create proxy (finding 20 §2) |
| `Status` | `/payouts/update_payouts_with_fts` | §2 above |
| `Details` | `/payouts/update_payouts_details_with_fts` | §2 above |
| `Fetch` / `FetchMultiple` / `Get` | `/payouts`, `/payouts/fetch_multiple`, `/payouts/{id}`, `/payouts/analytics`, `/payouts/_meta/summary`, `/admin/payouts/{id}`, `/payouts/free_payout/{id}` | GET/fetch-multiple proxying for merchant/admin/dashboard reads |
| `Cancel` / `Retry` | `/payouts/cancel_payout/{id}`, `/payouts/retry` | admin cancel/retry actions |
| `BulkPayout` | `/payouts/bulk/validate`, `/payouts/bulk`, `/payouts/batch/process` | Batch service's bulk-create flow (per finding 23 §B, cross-referenced — `payout.json`/`payout_approval.json` batch types call PS directly, but the monolith *also* has its own proxy path for the same operations) |
| `Schedule` | `/payouts/scheduled/process` | scheduled-payout dispatch |
| `ManualAction` | `/payouts/manual_action` | admin manual-action route (`payouts_dashboard_manual_actions`, finding 20 §1) |
| `OnHoldBeneEvent` | `/payouts/bene_bank_status_update` | beneficiary-bank-down event propagation |
| `OnHoldCron` / `OnHoldSLAUpdate` | `/payouts/on_hold/process`, `/merchant/update_on_hold_slas` | on-hold SLA cron |
| **`QueuedInitiate`** | **`/payouts/balance_update_event`** | **this is the exact `balance_update_event` call the lane brief asked about.** `dispatchQueuedPayoutBalanceIdToMicroservice({"balance_ids":[...]})` (QueuedInitiate.php:10-30) is called from `Payout\Core::dispatchBalanceIdsForQueuedPayoutsToPayoutsService()` (Core.php:4083-4104), itself called from `Payout\Service` (Service.php:2892-2916) **only in live mode, and only when Splitz experiment `PAYOUTS_SERVICE_CRON_TRIGGER_FOR_QUEUED_PAYOUTS` is disabled** for that dispatch — the code's own comment (Service.php:2892-2896) says this monolith-proxy path is being **phased out** in favour of a dedicated cron hitting PS directly, "eliminat[ing] the use of the Monolith as a proxy for the cron requests." So this is a **migration-transitional** call, not a permanent contract. |
| `FreePayout` | `/admin/free_payout/{id}`, `/payouts/free_payout_migration` | admin free-payout migration tool |
| `ProcessStuckPayouts` | `/payouts/process_stuck_payouts` | stuck-payout admin cron |
| `Redis` | `/cron/redis_key_set` | cache-priming cron |
| `Workflow` | `/workflow/state` | workflow callback forwarding (separate from the *inbound* `wf-service/state/callback` route — this is monolith→PS, not workflows→monolith) |
| `Shield` | `/payouts/shield/evaluate` | Shield risk-evaluation proxy |
| `MerchantConfig` | `/merchant/update_merchant_feature_cache` | cache-invalidation push after a monolith-side feature change |
| `TranslateAccountNumberToBalanceId`, `DuplicatePayoutEvaluate`, `StatusReasonMap`, `UpdateAttachments`, `CreditTransferPayoutUpdate`, `DashboardScheduleTimeSlots`, `AdminFetch`, `DataConsistencyChecker`, `*FailureProcessingCron` (create/update/dual-write) | various | assorted admin/consistency/cron tooling, not traced further (out of budget) |

None of these outbound calls are modeled by the stub (which only implements the *inbound*, PS/FTS-facing
routes — correctly, since the stub's job is to stand in for the monolith as a callee, not to originate calls
into PS). `balance_update_event` specifically is worth flagging to the twin's owner because it is the
**only** mechanism by which a "balance topped up → dequeue this merchant's queued payouts" signal reaches PS
via the monolith path, and its own code comments say it is being retired — a golden-run scenario that depends
on queued-payout dequeue via this path may already be testing dead weight in production, independent of the
twin.

---

### 6. Merchant/tenant identity propagation

**Monolith → FTS** (§1): HTTP Basic Auth, static per-mode `fts_key`/`fts_secret` (one credential for the whole
monolith, not merchant-scoped); `merchant_id` and payout/contact/bank-account details travel in the JSON
body, not headers.

**Monolith → PS** (§5, and the FTS-relay's forwarding leg in §2): confirmed absent of any merchant-identity
header in these specific calls — `Base::makeRequestAndGetContent` (not re-read this pass, cross-referenced
against finding 20 §2/3's general `PayoutService\Base` header description) is understood, per finding 20, to
attach Passport JWT + `X-Payout-Actor-Id/Type` for the **merchant-facing payout-create proxy** (§2a/§2b in
finding 20); it is **not confirmed** whether these same headers are attached on the `payouts_service/*`-relay
calls covered here (Status/Details/QueuedInitiate/etc.) — these are service-to-service calls originated by a
cron/webhook-handler context, not a live merchant request, so there may be no merchant passport to attach at
all. **Flagged UNKNOWN** — worth a follow-up read of `PayoutService\Base::getHeadersWithJwt()`'s call sites
specifically from within `Status.php`/`Details.php`/`QueuedInitiate.php` (none of the three files reference
`Base::addActorsToHeaders` or similar by name, suggesting these particular clients may send **no** actor
identity at all beyond the static Basic-Auth credential — INFERRED, not confirmed).

**PS → monolith** (all `payouts_service/*` routes in §1/§3/§4): CONFIRMED via `payouts/pkg/api/base.go:29-33`
(`req.SetBasicAuth(b.Client.GetAuth().Key, b.Client.GetAuth().Secret)`) — **plain HTTP Basic Auth against a
single static service credential, with no per-merchant or per-request identity header anywhere in the PS→
monolith direction.** This is the load-bearing fact behind item 6 of the brief: **the monolith does NOT need
to identify the payout's merchant from a header or from probing multiple merchant credentials — every route
that needs the merchant/payout resolves it deterministically from the payout id alone**, via the direct
PS-database SELECT described in §1 (`getPayoutServicePayout`/`getAPIModelPayoutFromPayoutService`, used by
`create_fta`, `source_update`, `status_details_source_update`, `mail_and_sms`, `create_ledger`, and the
dual-write read path). **This directly falsifies the premise behind the stub's `_ps_get_payout()` design**
(server.py:276-293, "the payout id does not reveal the merchant, so we mint a passport for each seeded
merchant until one answers 200"): in production there is no such ambiguity to resolve, because the monolith
never asks PS over HTTP for the payout at all — it has a standing DB connection into PS's own schema and does
`SELECT * FROM payouts WHERE id = ?`. The stub's brute-force-mint workaround is a deliberate, well-documented
substitution for a capability (cross-service DB access) the arena's Docker Compose topology cannot cheaply
grant — it produces the *same answer* for the arena's 3 seeded merchants, but the underlying mechanism, cost
profile (N mint+HTTP round-trips vs. one SQL SELECT), and failure mode (a payout id absent from all 3 seeded
merchants times out/loops rather than 404s promptly) all differ from production.

---

### 7. API-DB tables PS also reads directly — migration vs. twin patch drift

| Table | Real migration (file:line) | Twin (`apidb.sql`) | Drift |
|---|---|---|---|
| `balance` | `2014_07_12_083930_create_balance.php:19-79`: `id, merchant_id, type, currency, name, balance, locked_balance, on_hold, credits(`Balance::AMOUNT_CREDITS`='credits'), fee_credits, reward_fee_credits, refund_credits, account_number, account_type, channel, created_at, updated_at` | Adds `type, primary, name, on_hold, credits, fee_credits, refund_credits, account_number, account_type, channel, locked_balance, created_at, updated_at` via idempotent `ALTER…ADD COLUMN IF NOT EXISTS` pattern | **CONFIRMED DRIFT**: the twin adds a `primary` TINYINT column that **does not exist in production**. `Balance\Entity` has no `IS_PRIMARY`/`PRIMARY` column constant at all (grepped in full) — "primary vs. secondary balance" is a **value of the existing `type` column** (`Type::PRIMARY`, `Balance/Entity.php:95,322,394,732-738`), not a separate flag. A query written against the twin as `WHERE \`primary\`=1` would not translate to any real production query, which would instead be `WHERE type='primary'`. Also missing `reward_fee_credits` (production has it, twin doesn't) — low materiality (not read anywhere in the traced payout paths). |
| `features` | `2016_10_24_081731_create_features_table.php:19-33`: `id, name VARCHAR(25), entity_id, entity_type VARCHAR(255), created_at, updated_at`, **UNIQUE(name, entity_id)** | `id, entity_id, entity_type VARCHAR(32), name VARCHAR(255), created_at, updated_at`, plain `KEY(entity_id, entity_type)`, no unique constraint | Minor: `name` column length inverted (real 25 vs twin 255; twin is more permissive, harmless), `entity_type` length inverted (real 255 vs twin 32 — could truncate a long entity_type in the twin, low risk since all entity_type values here are short strings), **twin is missing the `UNIQUE(name, entity_id)` constraint** — a duplicate-feature-row bug that exists in production code paths (which rely on the DB to reject dupes) would silently succeed against the twin. |
| `workflow_entity_map` | `2020_08_13_104459_create_workflow_entity_mapping_table.php:19-38`: `id, workflow_id, config_id, entity_id, entity_type VARCHAR(255), merchant_id, org_id (nullable), created_at, updated_at` | matches column set; `entity_type VARCHAR(64)` (real 255) | Minor length-only drift, low risk. |
| `reversals` | `2016_12_19_110546_create_reversals.php:22-70`: `id, merchant_id, customer_id(nullable), entity_id, entity_type, balance_id(nullable), amount, fee, tax, currency, channel(nullable), utr(nullable), notes(text), transaction_id(nullable), transaction_type(nullable), initiator_id(nullable), customer_refund_id(nullable), created_at, updated_at` — **no `payout_id` column** | `id, merchant_id, entity_id, entity_type, payout_id, balance_id, channel, currency, notes, fee, fees, tax, transaction_id, amount, utr, created_at, updated_at` | **CONFIRMED, material DRIFT**: `Reversal\Entity::PAYOUT_ID` (`Entity.php:59`) is a **computed Eloquent accessor** (`getPayoutIdAttribute()`, `Entity.php:474-482`): `return ($this->getEntityType()===PAYOUT) ? Payout\Entity::getSignedIdOrNull($this->getEntityId()) : null;` — there is **no physical `payout_id` column in production**. The polymorphic link is always `entity_id + entity_type='payout'`. The twin's schema materializes a `payout_id` column that doesn't exist, plus a spurious duplicate `fees` column (real only has `fee`), and is **missing** `customer_id`, `transaction_type`, `initiator_id`, `customer_refund_id`. Any query the twin's seed/verifier tooling writes against `reversals.payout_id` will not be a faithful stand-in for the real `entity_id`/`entity_type` polymorphic pattern PS/monolith code actually uses. |
| `payouts_details` | `2021_06_08_195420_create_payouts_details_table.php:21-40`: PK is `payout_id` itself (no separate `id`), `queue_if_low_balance_flag, created_at, updated_at, tax_payment_id(nullable), tds_category_id(nullable, unsigned int), additional_info(json, nullable)` — **no `beneficiary_bank_code` column** | `id CHAR(14) PK, payout_id, queue_if_low_balance_flag, tds_category_id, tax_payment_id, additional_info, beneficiary_bank_code, created_at, updated_at` | **Drift**: twin invents a separate `id` primary key column (production's PK *is* `payout_id`, 1:1 with the payout) and a `beneficiary_bank_code` column not present in this migration (may have been added by a later, unread migration — flagged, not confirmed either way). |
| `payouts_status_details` | `2021_11_22_093451_create_payouts_status_details_table.php:22-40`: `id`(PK, own id, not payout_id), `payout_id, status, reason(nullable), description(nullable), mode, triggered_by(nullable), created_at, updated_at` | matches column set closely | No material drift found. |
| `fund_transfer_attempt` | `2017_02_20_135839_create_fund_transfer_attempts_table.php:26-90`: has `is_fts TINYINT default 0` (**twin omits this column entirely**); `version VARCHAR(3)` (twin: `VARCHAR(8)`); **no `source_account_id` or `bank_account_type` columns in this base migration** (may exist via a later, un-inventoried migration — genuinely unconfirmed either way, not asserted as drift) | `id, source_id, source_type, merchant_id, purpose, bank_account_id, vpa_id, card_id, wallet_account_id, batch_fund_transfer_id, channel, source_account_id, bank_account_type, version VARCHAR(8), bank_status_code, bank_response_code, mode, status, utr, narration, remarks, date_time, cms_ref_no, failure_reason, fts_transfer_id, created_at, updated_at` | Twin is missing `is_fts` (low materiality — not read in any traced path this pass); `source_account_id`/`bank_account_type` presence is plausible (these field names appear used elsewhere, e.g. `Attempt\Entity::SOURCE_ACCOUNT_ID`/`BANK_ACCOUNT_TYPE` referenced in `updateStatusAfterFtaRecon`, Core.php:950-951) but their DDL origin (a later ALTER migration) was not located in this pass — genuinely UNKNOWN, not a confirmed drift. |

**Separately-noted twin artifact issue (not a migration-drift finding, a build/config issue)**: `ENV2_COMPOSE/
seeds/mysql/apidb_seed.json` (merchants/contacts/fund_accounts, `ARENA_M1`-style ids) is **not loaded by
anything** — `scripts/up.sh:56-60` only executes `.sql` files against `mysql-apidb-stub`
(`00_init.sql` + `apidb.sql`), and `00_init.sql`'s own header comment confirms it is DDL-only, "no data."
Meanwhile `monolith-stub/server.py` reads its merchant/fund-account data from **`seeds/monolith/*.json`**
(different id scheme, `ARENAM00000001`-style) via plain Python dict lookups, never touching
`mysql-apidb-stub` at all. **Net effect: the `mysql-apidb-stub` MySQL instance's `merchants`/`contacts`/
`fund_accounts` tables exist (DDL-only) but are permanently empty**, and `apidb_seed.json`'s carefully-written
seed rows are dead weight — any component that actually does a direct-SQL fallback read against
`mysql-apidb-stub` (i.e. reproducing cfa's/x-balances'/payouts' own `[APIStore.Sql]`/`[db.api]` fallback
behaviour, per finding 14 §4/§8) will find nothing there, silently diverging from what the JSON seed author
evidently intended. Whether this matters depends on whether the arena's golden runs actually exercise those
direct-DB fallback paths (`FindMultipleWithPaginationAPI`, `IsApiDbEnabledForPayoutStatusDetails`) — flagged
for the twin owner regardless.

---

## Corrections to existing reports

| Report | Claim | Source says | Evidence |
|---|---|---|---|
| `raw-findings/20_api_monolith.md` §8, `ARCHITECTURE_DELTA.md` W7 | (implicitly, by omission) dual-write's `payoutServiceDualWrite` HTTP response is unspecified | Response is literally `['status' => 'success']` | `api/app/Models/Payout/Core.php:10273` |
| `monolith-stub/server.py` (self, not a prior report but load-bearing) | `_create_fta`'s doc-comment states the monolith "reads its own DB instead" of minting passports, implying the design is a *substitution* for something the monolith does differently but still remotely | The monolith reads PS's payout via a **direct second MySQL connection into PS's own database** (`getPayoutsServiceConnection()`), not any remote call at all — confirming the stub's own framing is directionally right but the *mechanism* (SQL SELECT by primary key vs. brute-force merchant HTTP mint-and-GET) is materially different in cost and determinism | `api/app/Models/Payout/Repository.php:5313-5323`, `Core.php:8461-8530` |
| `seeds/monolith/misc.json` (twin's own provenance note) | "`users_internal`/`actor_info_internal`: NOT FOUND as monolith routes in findings/20 … best-effort placeholder responses, not independently confirmed against a real route" | Both routes exist (`multiple_users_fetch_internal`, `fetch_actor_info_internal`, Route.php:2896,2963) and are both explicitly allowlisted for the `payouts_service` credential (Route.php:19404-19405) | `api/app/Http/Route.php:2896,2963,19379-19426`; `UserController.php:413-418,847-854`; `User/Service.php:3461-3469,5373-5397` |
| `raw-findings/14_monolith_dependency_migration_state.md` §1 (`fetch_pricing.go` row) | Describes `fetch_pricing_info` only as "Pricing/fee calc when Redis-cached rule misses" without listing required fields | Production requires `payout_id, merchant_id, balance_id, amount, method, mode, channel` — `channel` and `method` are mandatory, not incidental | `api/app/Models/Payout/Validator.php:666-679` |
| `ARCHITECTURE_DELTA.md` §(a), finding 20 §9 | `internal_balances_queued` "confirms internal callers (most plausibly PS) batch-resolve balances from the monolith" — framed as a live-DB read with no further caveat | Same mechanism confirmed; additionally the *production* freshness of that local `balance` table depends on a "journal-created consumer" keeping it in sync with the ledger, which is itself unread/unconfirmed by any report so far — the arena substitutes ledger-api directly instead, which the stub's own comment (server.py:329-334) already discloses | `ENV2_COMPOSE/substitutes/monolith-stub/server.py:329-338`; finding 20 §9 (`Merchant\Balance\Service::fetchBalancesForBalanceIds`, not independently re-read this pass) |

No corrections were needed to finding 20 §1-§9's *structural* claims (route names, auth groups, controller
actions) — every one cross-checked in this pass (Route.php line numbers, `$internalApps['payouts_service']`
block) matched exactly. The corrections above are all at the level of exact field names/values and response
shapes that finding 20 did not go deep enough into (by its own admission, e.g. §4's "not read in full depth
this pass" caveats), which this lane's narrower PS/FTS-only scope allowed going deeper on.

---

## Twin comparison

| Component | Production mechanism | Twin mechanism (file) | Verdict | What differs | Controls |
|---|---|---|---|---|---|
| `create_fta/{id}` payout lookup | Direct 2nd-MySQL-connection SELECT into PS's own DB by payout id (`Repository.php:5313-5323`) | Brute-force: mint a passport per seeded merchant, GET PS's `/v1/payouts/pout_{id}` until one answers 200 (server.py:276-293) | **REPRESENTATIVE** | Cost (O(n) mint+HTTP vs O(1) SQL), determinism (loops/404s on an unseeded merchant vs. always resolves), and the auth path exercised (Passport-JWT mint machinery is invoked in the twin but never in production for this route) | tenant, routing |
| `create_fta/{id}` FTS request body | Full `FundTransfer::makeRequestUsingType` — product/purpose branching, merchant_category, request_meta, MCC, onboarded_time, 2FA block, is_batch | Minimal fixed body: `product, merchant_id, transfer.{amount,source_id,source_type,preferred_mode[,preferred_source_account_id]}, account.fund_account_id` (server.py:306-320) | **REPRESENTATIVE** | Missing narration, initiate_at, preferred_channel, merchant_category, request_meta, 2FA — none of which matter for the arena's golden-path status transitions, but would matter for any FTS-side routing/channel-selection test | routing (channel selection would differ), not auth/tenant/balance |
| `create_fta` sync-then-async-fallback on FTS timeout | 1s sync timeout, swallow-and-requeue via `Initiator`, response still success-shaped | Stub treats any non-200/201 FTS response as a hard visible error in its own return | **INCORRECT** (safer-than-production, not faithful) | Twin surfaces failures production hides from PS; a test asserting "PS sees an error when FTS is slow" would pass in the twin and fail against real behaviour | payout/transfer state, error visibility |
| `update_fts_fund_transfer` status/detail relay | Details-then-status ordering, exact field sets (§2), single-attempt no-retry, per-account-type/channel FAILED map | Same ordering, same field sets, single-attempt PATCH+POST, same simplified FAILED map (direct→failed else→reversed) | **CONTRACT-FAITHFUL** (with the DIRECT-channel nuance noted in §2 as the one gap) | Only channels outside {RBL,ICICI,AXIS,YESBANK,IDFC} on a DIRECT balance would diverge (arena only ever seeds RBL) | payout/transfer state, reversal |
| `dual_write` | Queue-backed, 5 retries, response `{"status":"success"}`, PS→monolith-local-table upsert direction | Synchronous log-sink, response `{"status":"queued"}`, optional forced-fail mode | **REPRESENTATIVE** (response literal wrong, no async/retry semantics, but optional fail-mode is a deliberate incident-reproduction feature) | Response field value; no actual local-table write exists to observe divergence against (twin has no monolith `payouts` table at all) | idempotency, repair (fail-mode) |
| `source_update` | Async queue dispatch to downstream source-subscribers, unconditional `{"sources_updated":true}` | Synchronous log-sink, same literal response | **CONTRACT-FAITHFUL** (response shape); no async side-effect modeled | — | none (response-shape only, no state mutated) |
| `status_details_source_update` | Returns list of **subscriber/source types notified**, not request echo | Echoes the request's own `source_id` values back | **INCORRECT** | Field values are semantically wrong, not just simplified | none (used for logging/telemetry downstream, not control-plane) |
| `deduct_credits` | Stateful, idempotent, can reduce fees/tax via real credit ledger | Static echo, `credits_used` always false | **INCORRECT** for any credit-based-fee-waiver test path | fees/tax computation, idempotency | balance/ledger (credits) |
| `reverse_credits` | Idempotent, validated (5 required fields) | Unconditional success, no validation | **REPRESENTATIVE** | Idempotency/validation | balance/ledger |
| `fetch_pricing_info` | 7 required fields incl. `channel`/`method`/`amount`, real pricing engine | 4 fields read, static per-(merchant,mode) plan lookup | **REPRESENTATIVE** | Channel/method/amount-sensitive pricing not reproducible | balance/ledger (fee amount) |
| `decrement_free_payouts` | Keyed by `balance_id`, null-return for non-banking balances | Keyed by `merchant_id`, no balance-type branch | **REPRESENTATIVE** | Counter identity key differs; could double-count across a merchant's multiple balances | balance/ledger (free-payout counter) |
| `free_payout_rollback`, `create` (`payouts_service/create`), `create_ledger` | Exist, not modeled | No route registered | **MISSING** | Entire endpoints absent | low (migration/ledger-entry tooling, not on golden create/status path per current evidence) |
| `internal/merchants/{id}` | Rich response, but PS parses only `merchant`+`merchant_detail` (2 nested objects, ~20 fields total) | Seed JSON matches those ~20 fields exactly; adds extra top-level keys PS ignores | **CONTRACT-FAITHFUL** (for what PS actually consumes) | Extra unused fields are inert, not a real drift for PS | tenant (merchant config) |
| `internal_balances_queued` | Local `balance` table, itself synced from ledger by an unread consumer | Reads ledger-api directly (documented substitution) | **REPRESENTATIVE** (self-disclosed) | Freshness/consistency semantics of the real journal-consumer are unknown and unmodeled either way | balance |
| `actor_info_internal`, `users_internal` | Exist, allowlisted for PS, dynamic role/entity derivation | Exist in stub, static fixed-role seed data | **REPRESENTATIVE** | Role derivation mechanism, not just data volume | none directly (audit/attribution enrichment, not control-plane) |
| `banking_account_statement/payout_update` (inbound BAS recon) | Exists, PS-only caller, FAILED-local→reverse / PS-owned→forward | No route in stub | **MISSING** | Entire endpoint absent | reversal |
| `balance_update_event` (monolith→PS) | Exists, migration-transitional, being retired per code comment | Not applicable (this is an outbound call FROM the monolith; nothing for the stub to implement as a callee) | **N/A** (correctly out of stub's scope as a route) | — | — |
| Identity/auth on all `payouts_service/*` routes | Static per-service HTTP Basic Auth, no merchant header | Twin also uses no merchant header for these routes' auth (only for the `create_fta` PS-lookup workaround does it mint a passport, which production never does at all here) | **CONTRACT-FAITHFUL on the auth mechanism**, **INCORRECT on why a passport mint appears in the flow** | The mint step is pure arena workaround machinery, not a production auth step | auth, tenant |

---

## Recommendation: real vs. substitute

**Recommend: remain SUBSTITUTE, not real monolith.** Assessment:

- **Buildability**: `api` is a large PHP/Laravel monolith (`Route.php` alone is 23k+ lines) with dozens of
  first-class subsystems (Merchant, Balance, Feature, Workflow, FundAccountValidation, Admin, etc.) tightly
  interwoven via a single Eloquent `RepositoryManager`; even the narrow PS/FTS-facing slice touches Merchant,
  Balance, Feature, Reversal, FundTransferAttempt, PayoutsStatusDetails, Credits, Notifications, and Splitz/
  DCS/Razorx client code. There is no existing "trimmed monolith" build target visible in this repo (no
  `Dockerfile.payouts-service-facade` or similar); building a runnable slice would mean either (a) running the
  entire monolith with most business-logic paths dead (huge surface, huge onboarding cost, needs the monolith's
  own MySQL+Redis+every downstream client mocked anyway) or (b) hand-picking and reimplementing ~15 controller
  methods and their transitive model graph, which is functionally a rewrite, not a "build real."
- **DB dependency depth**: the PS/FTS-facing paths alone touch at minimum `balance`, `features`,
  `workflow_entity_map`, `reversals`, `payouts_details`, `payouts_status_details`, `fund_transfer_attempt`,
  `merchants`, `merchant_detail`, plus (per finding 20) the monolith's own `payouts` table for approve/reject/
  BAS-fallback/dual-write, and PS's *own* database reached via a second live connection (`getPayoutsServiceConnection`)
  — i.e. running "real" requires provisioning **two** MySQL schemas wired together, one of which is a live
  cross-service dependency on PS's own internal schema (a coupling the arena would have to fake anyway, since
  the arena's PS is a different codebase/schema-version than whatever the monolith's raw SQL (`select * from
  payouts where id = ?`) expects).
- **External deps**: Splitz (experiment gating appears on nearly every non-trivial handler:
  `NON_TERMINAL_MIGRATION_HANDLING`, `PAYOUTS_SERVICE_CRON_TRIGGER_FOR_QUEUED_PAYOUTS`,
  `ACCOUNT_STATEMENTS_DUAL_WRITE_CUTOFF`, the direct-proxy-cutover experiment), DCS, CFA, Notifications/Stork,
  Credits ledger, Admin/AuthZ — a "real" monolith would need all of these live or mocked regardless, which is
  exactly the mock/stub surface the arena already builds for its *other* substitutes.
- **Value of going real is low for this lane's actual production-fidelity gaps**: none of the CORRECTION-level
  findings above (dual_write's response literal, status_details_source_update's field semantics,
  deduct_credits' statefulness, fetch_pricing_info's channel/method-sensitivity, the direct-DB `create_fta`
  lookup mechanism) require running real PHP — they are all fixable by editing `server.py`'s literal values
  and adding a few conditionals, once known.

**If substitute (current path), the complete contract the stub must implement** (consolidating this report's
§1-4 as the authoritative field-level contract, superseding `monolith-stub/CONTRACT.md` wherever it disagrees):
see the per-route tables in §1-4 above for routes, exact request/response fields, status codes (all routes
return HTTP 200 with a body-level `error`/`status` field on failure, per every handler read this pass — no
4xx/5xx was observed except the generic 404 the stub itself returns for an unseeded id, `internal/merchants`
and `fund_accounts_internal` only), timing (`create_fta`'s FTS leg: 1s sync timeout then async-fallback,
**not** modeled by the stub — recommend adding a configurable delay+fallback mode analogous to
`PS_RELAY_MODE=drop`), ordering (details-before-status on the FTS relay — already correct), and error
behaviour (swallow-on-timeout for `create_fta`, hard-fail visible in the stub — recommend a
`FTS_CREATE_MODE=swallow|error` env var mirroring `PS_RELAY_MODE`'s pattern).

---

## Synthetic data

| family/table | field | source evidence | type+length | constraints | allowed values | FK/relationships | state rules | distribution matters? | generation rule | EXACT/REPRESENTATIVE/ASSUMED |
|---|---|---|---|---|---|---|---|---|---|---|
| `merchants` (stub JSON) | `merchant.feature[]` | `api/app/Models/Feature/Constants.php` (46 PAYOUT/FUND_ACCOUNT constants, finding 20 §11); Go tag confirmed `merchant_config.go:32` | array of strings | none enforced (free-form array) | any `Feature\Constants::*` value; `payout_service_enabled`/`banking`/`enable_approval_via_oauth` are the only ones this arena's seed uses | none (array column, no FK) | merchant-level, static per seed | yes — presence of `enable_approval_via_oauth` (M3) selects a different approval-flow test path | manually curated per golden scenario | REPRESENTATIVE (real feature list is 46-wide, seed uses 3) |
| `merchants` (stub JSON) | `account_type` / `channel` / `balance_id` | not part of the real `/internal/merchants/{id}` response schema at all (§4) — arena-internal bookkeeping only | string / string / string(14) | — | `shared`\|`direct`; `""`\|`rbl`\|... ; `ARENABAL0000NN` | `balance_id` conceptually maps to the real `balance.id` PK (`Balance::ID_LENGTH`, `database/migrations/2014_07_12_083930_create_balance.php:22-23`) | static | yes — drives §1's direct/shared FTA branching in the stub | manually curated | ASSUMED (never validated against a real serialized response since it's never actually returned by production over this route) |
| `reversals` (twin apidb.sql) | `payout_id` | `Reversal\Entity::getPayoutIdAttribute()` (Entity.php:474-482) | CHAR(14) | — | valid payout id | **none in production** — this column doesn't exist; real linkage is `entity_id`+`entity_type='payout'` | computed, not stored | n/a | schema should be corrected to drop this column and add `entity_id`/`entity_type` semantics if any seed/verifier query relies on it | **INCORRECT** (not simply representative — this column shouldn't exist) |
| `balance` (twin apidb.sql) | `primary` | `Balance/Entity.php` — no such column; "primary" is `type='primary'` | TINYINT | — | n/a | n/a | n/a | n/a | drop the column; encode "is this the primary balance" as `type='primary'` vs. `'banking'`/other `Type::*` values | **INCORRECT** |
| `fund_transfer_attempt` (twin apidb.sql) | `source_account_id`, `bank_account_type` | referenced in production code (`Attempt\Entity::SOURCE_ACCOUNT_ID`/`BANK_ACCOUNT_TYPE`, used at `Core.php:950-951`) but DDL origin not located in the base 2017 migration | CHAR(14) / VARCHAR(32) | — | — | — | — | possibly | keep as-is; flag for confirmation against a later ALTER migration | ASSUMED (plausible, unconfirmed) |
| `payouts_service/fetch_pricing_info` request | `channel`, `method`, `amount`, `balance_id` | `Payout\Validator::$payoutServiceFetchPricingInfoRules` (Validator.php:666-679) | string / string / int≥1 / CHAR(14) | all `required` | channel ∈ Settlement\Channel::* (e.g. rbl/icici/axis/yesbank/idfc); method ∈ Payout\Method::* | balance_id → `balance.id` | — | **yes** — production pricing genuinely varies by these fields; the stub ignores all four | stub should be extended to accept and branch on these fields even if the branching logic stays a lookup table | **INCORRECT by omission** in the current stub (fields not read at all) |
| `payouts_service/deduct_credits` request | `merchant_id`, `balance_id`, `status` | `Payout\Validator::$deductCreditsViaPayoutServiceRules` (Validator.php:653-660) | CHAR(14)/CHAR(14)/string | all `required` | — | — | idempotency keyed by `(payout_id, source_type=PAYOUT)` via `Credits\Transaction` | yes, for any credits-idempotency test | stub should track deducted-payout-ids and branch its response on repeat calls | **INCORRECT by omission** |
| `payouts_service/decrement_free_payouts` request | `payout_id` | `$decrementFreePayoutForPayoutsServiceRules` (Validator.php, cross-referenced) | CHAR(14) | required | — | — | counter keyed by `balance_id`, not merchant | yes if a merchant has >1 balance in a golden scenario | key the stub's counter dict by `balance_id` instead of `merchant_id` | **INCORRECT** (wrong key) |

---

## Cannot be derived from repositories

1. **`Initiator::sendFTSFundTransferRequest`'s async queue and its consumer's retry/backoff cadence** — the
   fallback path every `create_fta` sync-timeout (1s) or exception routes through. Not read this pass (out of
   budget, not "inaccessible"). Matters for: whether the twin's `create_fta` should model an eventual
   asynchronous FTS transfer creation after an initial "no error" response. Likely owner: API monolith team.
   Minimal request: a read of `app/Models/Payout/Initiator.php` (or wherever that class lives) plus its queue
   worker — this is fully derivable from the same `api` clone, just not opened this pass; **not a genuine repo
   gap**, a budget gap. Recommend the twin owner re-run a targeted grep (`grep -rn
   "sendFTSFundTransferRequest\|class Initiator" api/app/Models/Payout/`) before treating this as blocked.
2. **`CounterHelper::fetchCounterAndDecreaseFreePayoutsConsumed` exact response shape** for
   `decrement_free_payouts` — same situation, in-repo but unread this pass
   (`api/app/Models/Payout/**/CounterHelper.php` or similar, not located by name this pass).
3. **Later ALTER migrations to `fund_transfer_attempt` and `payouts_details`** (for `is_fts`'s twin-omission
   correction, and confirming/denying `source_account_id`/`bank_account_type`/`beneficiary_bank_code` DDL
   origin) — a full `grep -rl Table::FUND_TRANSFER_ATTEMPT database/migrations/` returned only the one base
   migration in this pass; either these columns were added via a raw `DB::statement` (not a Schema-builder
   migration, hence invisible to this grep pattern) or via a migration file that doesn't reference the
   `Table::` constant literally. Minimal request: schema-only DDL export of `fund_transfer_attempt` and
   `payouts_details` from a real (non-prod) monolith DB, or a wider grep pattern
   (`grep -rn "fund_transfer_attempt" database/migrations/*.php`, not just the `Table::` constant form).
4. **Whether `payouts_service/*` outbound-facing PS→monolith calls (Status/Details/QueuedInitiate/etc.) carry
   any actor/passport header** (§6) — genuinely ambiguous from the files read; would need
   `PayoutService\Base::makeRequestAndGetContent`'s full body (not opened this pass) cross-referenced against
   each of the ~30 client classes' constructors to see which extend a header-adding trait vs. plain `Base`.
5. **`contacts_internal` GET/list/update's actual allowlisted caller set** (§4) — Route.php confirms the route
   exists and confirms `payouts_service` is NOT in its allowlist by the one credential-block read in full this
   pass, but a repo-wide grep for `contact_get_internal`/`contact_list_internal`/`contact_update_internal`
   across *all* `$internalApps` blocks (not just `payouts_service`'s) was not run — could still resolve
   in-repo with 10 more minutes of grep.
6. **Real feature-flag rollout weights / Credstash-sourced Splitz experiment ids** referenced throughout §1-5
   (`PAYOUTS_SERVICE_CRON_TRIGGER_FOR_QUEUED_PAYOUTS`, `NON_TERMINAL_MIGRATION_HANDLING`, the direct-proxy
   cutover experiment) — architecturally outside any repo (Splitz's own MySQL, admin-managed), per
   `ARCHITECTURE_DELTA.md`'s own prior finding. Owner: Payouts/Growth-infra team running Splitz. A schema-only
   export of the experiment's current rollout percentage would resolve which code branch is actually live in
   prod today for each gate.

---

## Fidelity tier verdicts

- **`create_fta/{id}` payout/merchant resolution**: REPRESENTATIVE SUBSTITUTE — correct end result for the 3
  seeded merchants, wrong mechanism (HTTP brute-force mint vs. direct-DB SELECT by id); the one item most worth
  fixing if the twin ever needs >3 merchants or a payout id that "shouldn't" resolve.
- **`create_fta` FTS request body**: REPRESENTATIVE SUBSTITUTE — minimal-but-correct-enough for status-transition
  golden paths; not routing/channel-selection-faithful.
- **`create_fta` sync-timeout/fallback behaviour**: REPRESENTATIVE SUBSTITUTE, leaning INCORRECT — hides a
  real "swallow and requeue" behaviour behind a visible error the twin invented.
- **`update_fts_fund_transfer` relay (ordering, field sets, status maps)**: CONTRACT-FAITHFUL SUBSTITUTE — the
  strongest-verified component in this lane; only the DIRECT-channel-outside-{RBL,ICICI,AXIS,YESBANK,IDFC}
  edge case is unmodeled, and the arena never seeds such a channel.
- **`dual_write`**: CONTRACT-FAITHFUL SUBSTITUTE on mechanism/lifecycle intent (including the deliberate
  failure-mode reproduction), one-field INCORRECT on the literal success-response value (`"queued"` vs
  `"success"`) — trivial fix.
- **`source_update`**: CONTRACT-FAITHFUL SUBSTITUTE (response shape only; no side-effect exists to diverge on).
- **`status_details_source_update`**: INCORRECT — the returned field's semantics don't match production at all
  (echoes request ids instead of reporting notified-subscriber types).
- **`deduct_credits`**: INCORRECT for any scenario depending on credit-covered fees or repeat-call idempotency;
  REPRESENTATIVE-adjacent for scenarios that never trigger credits.
- **`reverse_credits`, `mail_and_sms`, `on_hold_slas_internal`, `actor_info_internal`, `users_internal`**:
  REPRESENTATIVE SUBSTITUTE across the board — right shape, no state/validation/derivation logic.
- **`fetch_pricing_info`**: REPRESENTATIVE SUBSTITUTE, degrading toward INCORRECT for any channel/method/amount-
  sensitive pricing test (currently structurally impossible to trigger — the fields aren't read).
- **`decrement_free_payouts`**: REPRESENTATIVE SUBSTITUTE, wrong counter key (merchant vs. balance) — a latent
  bug for any multi-balance-merchant scenario.
- **`internal/merchants/{id}`**: CONTRACT-FAITHFUL SUBSTITUTE for exactly what PS parses; extraneous but inert
  fields present.
- **`internal_balances_queued`**: REPRESENTATIVE SUBSTITUTE, self-disclosed by the stub's own comments.
- **`banking_account_statement/payout_update` (inbound BAS recon)**, **`free_payout_rollback`**,
  **`payouts_service/create`**, **`payouts_service/create_ledger`**: MISSING — no route implemented at all.
  Materiality is low-to-unknown without confirmation that the arena's golden runs exercise BAS reconciliation
  or ledger-entry creation via these specific PS-facing paths (as opposed to PS's own direct ledger client).
- **Monolith→PS outbound calls generally (§5)**, notably `balance_update_event`: UNKNOWN-BLOCKED as a twin
  concern only in the sense that nothing needs to *implement* them (they're outbound from a component the
  twin doesn't run as "real"); their existence and migration-transitional status is now CONFIRMED and should
  inform whether any golden run's "queued payout dequeues after balance top-up" scenario is asserting against
  a code path production itself is phasing out.
- **DB-table schema patch (`apidb.sql`) vs. real migrations**: three CONFIRMED, material drifts
  (`reversals.payout_id` shouldn't exist, `balance.primary` shouldn't exist, `decrement_free_payouts`'s
  conceptual counter key mismatch) plus one confirmed-dead artifact (`apidb_seed.json`, never loaded) — worth
  a dedicated small cleanup pass independent of any single route's fidelity tier.
