# batch-sim — HIGH-FIDELITY REPLACEMENT (not the original razorpay/batch)

A contract-faithful, clearly-labelled stand-in for the **payout batch types** of
Razorpay's Java/Spring-Boot Batch service, so that bulk payouts and bulk payout
approvals execute end-to-end against the **REAL** payouts-service in the twin.

This is **NOT** the razorpay/batch code, a port of it, or a fork. It is a
reimplementation of the parts of Batch that are observable on the wire:
the batch-type row→request mapping, the idempotency-key derivation, the
chunking, the retry/recovery rules, the batch/batch-entry state machine and
counters. Everything else (Spring Batch job engine, Postgres, S3 file store,
output-file writing, the completion e-mail tasklet) is absent.

**Consequently: any bug reproduced only because of batch-sim's behaviour is a
twin-specific lead, NOT a production finding.** Confirm against the real Batch
service before reporting anything as a production issue.

---

## 0. Provenance

Two read-only source trees were used. Every `file:line` below is from one of
them; nothing in this document is from memory.

| tag | tree |
|---|---|
| **BATCH** | `razorpay/batch` @ `3839dd7` (Java/Spring Boot) |
| **PS** | `razorpay/payouts` (Go), the same clone the twin runs |
| **MONOLITH** | `razorpay/api` @ `2d665f91` — facts supplied to this lane, restated where load-bearing |

---

## 1. The hop batch-sim collapses

In production there are **three** hops:

```
Batch (Java)  --(A)-->  api-monolith (PHP)  --(B)-->  payouts-service (Go)
```

**(A)** BATCH `BulkApiCallDataProcessorImpl.java:127` builds
`getBasePath(batchType) + endpoint`, i.e. `<api base>/payouts/bulk` (from
`payout.json:` `"endpoint": "payouts/bulk"`) or `<api base>/payouts/bulk_approve`
(`payout_approval.json:66`). Auth is Basic with username
`"rzp_" + mode + "_" + entityId` and password = the `applications.batch` secret
(`RestCallUtils.java:55-69`), plus headers `X-Batch-Id`, `X-Creator-Id`,
`X-Creator-Type`, `X-Entity-Id` (`RestCallUtils.java:38-53`, names in
`HeaderConstant.java:9-18`). MONOLITH routes: `payout_bulk_create`
POST `payouts/bulk` and `payout_bulk_approve` POST `payouts/bulk_approve`
(`Route.php:2318-2319`), `$proxy` only (`:8034-8035`), allow-listed for app
`batch` (`:18748-18749`); proxyAuth Basic user = merchant id, password =
`applications.batch` secret (`BasicAuth.php:1220, 1547-1580`).

**(B)** For PS-migrated merchants the monolith forwards shared-balance rows to
the payouts service: POST `{PS}/v1/payouts/bulk` with headers
`X-Passport-JWT-V1`, `x-batch-id`, `X-Entity-Id`, `x-creator-id`,
`x-creator-type` (`app/Services/PayoutService/BulkPayout.php:50-84`, entered
from `PayoutController.php:718-727` → `app/Models/Payout/Service.php:3051-3075`).

**In the twin there is no real monolith** — `substitutes/monolith-stub` stands
in and does not implement `payouts/bulk`. batch-sim therefore performs hop (B)
directly: it emits *the request the monolith would have forwarded*, byte-for-byte
in shape, and skips hop (A) entirely. Hop (A)'s credential
(`rzp_<mode>_<merchant>:<batch secret>`) has no verifier in the twin and is
therefore **not** minted; §5 records exactly which twin credential replaces it
and why.

Bulk **approval** never leaves the monolith in production
(`Service.php:3539-3600`, per-row `processEntryForBulkPayoutApproval` at `:3574`);
the monolith then calls PS `payouts_internal/{id}/approve` for PS-owned payouts
(`app/Services/PayoutService/Workflow.php`). batch-sim collapses that the same
way: it calls the **real** PS internal approve/reject route per row, with the
same identity the twin's `workflow-sim` already uses (see §5).

---

## 2. Batch types implemented

| batch_type_id | source config | processor | endpoint | bulkSize | failOnServerError | idempotency |
|---|---|---|---|---|---|---|
| `payout` | BATCH `src/main/resources/payout.json` | `bulkApiCallDataProcessor` / `payoutApiProcessor` | `payouts/bulk` | 5 | **false** | `batch_` + entry id |
| `payout_approval` | BATCH `src/main/resources/payout_approval.json` | `bulkApiCallDataProcessor` / `payoutApprovalProcessor` | `payouts/bulk_approve` | 5 | **true** (field absent → Java default `true`, `BulkApiCallDataProcessorImpl.java:88`) | `batch_` + entry id |
| `payouts_amazonpay_bene_details_process`<br>`payouts_amazonpay_bene_id_process`<br>`payouts_bank_transfer_bene_details_process`<br>`payouts_bank_transfer_bene_id_process`<br>`payouts_upi_bene_details_process`<br>`payouts_upi_bene_id_process` | BATCH `src/main/resources/v2/*.json` | `cacheIdempotencyEnabledBulkApiCallDataProcessor` / `payoutV2ApiProcessor` | `payouts/bulk` | 5 | **false** | SHA-1 row hash + cache (§4.2) |

The six v2 names are the exact list in
`PayoutConstant.getPayoutBatchTypes()` (`PayoutConstant.java:62-90`).
The paired `*_validate` v2 types (which drive `POST /v1/payouts/bulk/validate`)
are **not** implemented — see §9.

### 2.1 Row → PS payload mapping (`payout`)

`payout.json` `parameters` is the template; `##Column##` placeholders are filled
from the row, then `PayoutApiProcessorImpl.passSettingsIfRequired`
(`PayoutApiProcessorImpl.java:22-105`) moves the `optionalParameters` values into
their nested homes. batch-sim reproduces the *result*:

```
razorpayx_account_number  <- "RazorpayX Account Number"
payout.amount             <- "Payout Amount"              (optionalParameters payout_amount)
payout.amount_in_rupees   <- "Payout Amount (in Rupees)"  (payout_amount_rupees)
payout.currency           <- "Payout Currency"
payout.mode               <- "Payout Mode"
payout.purpose            <- "Payout Purpose"
payout.narration          <- "Payout Narration"           (payout_narration)
payout.reference_id       <- "Payout Reference Id"        (payout_reference_id)
payout.scheduled_at       <- batch.settings.scheduled_at  (PayoutApiProcessorImpl.java:31,:38)
fund.id                   <- "Fund Account Id"
fund.account_type         <- "Fund Account Type"
fund.account_name         <- "Fund Account Name"
fund.account_IFSC         <- "Fund Account Ifsc"
fund.account_number       <- "Fund Account Number"
fund.account_vpa          <- "Fund Account Vpa"
fund.account_phone_number <- "Fund Account Phone Number"   (account_phone_number)
fund.account_email        <- "Fund Account Email"          (account_email)
contact.name              <- "Contact Name"
contact.type              <- "Contact Type"                (contact_type)
contact.email             <- "Contact Email"               (contact_email)
contact.mobile            <- "Contact Mobile"              (contact_mobile)
contact.reference_id      <- "Contact Reference Id"        (contact_reference_id)
notes                     <- every "notes[...]"/"notes.*" column ("notesColumn": "notes")
idempotency_key           <- see §4
```

Column names are accepted **verbatim** (the real template headers) and, as an
arena convenience, in `snake_case` (`razorpayx_account_number`, `payout_amount`,
`fund_account_id`, …). The snake_case aliases are a batch-sim addition — the
real service reads only the header row of the uploaded file.

### 2.2 Row → request mapping (`payout_approval`)

`payout_approval.json` `parameters`:

```
razorpayx_account_number  <- "account_number (do not edit)"
payout_update_action      <- "Approve (A) / Reject (R) payout"
user_comment              <- batch.settings.user_comment, default "Bulk approved"
                             (PayoutApprovalProcessorImpl.java:22,:43,:47)
email                     <- batch.settings.email          (:44,:48)
payout.id                 <- "payout_id (do not edit)"
payout.{amount,currency,mode,purpose,narration,status}  <- the "(do not edit)" columns
fund.id                   <- "fund_account_id (do not edit)"
contact.{id,name}         <- "contact_id / contact_name (do not edit)"
```

`payout_update_action` `A` → approve, `R` → reject (case-insensitive; the first
letter is used). Anything else fails the row locally with
`BAD_REQUEST_ERROR / invalid payout_update_action`.

---

## 3. The exact outbound contracts (verbatim)

### 3.1 CREATE — `POST {PS_API_URL}/v1/payouts/bulk`

Route: PS `internal/routing/router/payout_internal_routes_with_passport.go:13-34`
— group `/v1/payouts`, endpoint `POST /bulk` →
`controllers.BulkPayoutsProcessorService.CreateBulkPayouts`.

Group middleware (`:15-20`):
`middleware.BasicAuth(cred.API, cred.Workflow)`,
`middleware.PassportAuthentication([]string{})`,
`middleware.DatabaseConnection()`.
An **empty** supported-auth list means no legacy-auth-type restriction —
`checkForSupportedAuths` returns immediately when the list is empty
(`internal/routing/middleware/passport.go:157-162`) — but a **passport JWT is
still mandatory**: a missing `X-Passport-JWT-V1` is rejected before the handler
(`passport.go:37-78`).

**Headers**

| header | value | source |
|---|---|---|
| `Authorization` | `Basic api:<auth_api_payouts>` — `cred.API` | route middleware `:16`; twin username from `docker-compose.yml` kong-lite `PS_API_AUTH_USER: "api"`; `secrets/gen-secrets.sh:59` |
| `X-Passport-JWT-V1` | RS256 passport, `consumer.id` = merchant id | MONOLITH `BulkPayout.php:50-84`; validated by `passport.go:37` |
| `x-batch-id` | the batch id | `constants/headers.go:18`; read into ctx at `middleware/request_context.go:19,39-45`; **required** — `helpers.GetBatchIDFromHeaders` 400s without it (`internal/helpers/helpers.go:61-74`, called at `controllers/bulkPayoutsController.go:73-81`) |
| `X-Entity-Id` | merchant id | `constants/headers.go:20` (`HeaderMerchantIDFromBatch`), `request_context.go:20,47-53` |
| `x-creator-id` | creator (user) id | `constants/headers.go:37`, `request_context.go:27,109-116` |
| `x-creator-type` | `user` | `constants/headers.go:38`; PS only honours `x-creator-id` when `x-creator-type == "user"` — the comment at `request_context.go:109` literally reads *"Batch service passes creator type as user and user_id as creator_id"* |
| `Content-Type` | `application/json` | BATCH `RestCallUtils.java:43` |

**Body** — a JSON **array** of `dtos.BulkPayoutCreateRequest`
(`internal/app/dtos/bulkPayoutCreateRequest.go:7-48`, bound with
`ShouldBindJSON` at `:50-57`):

```json
[
  {
    "razorpayx_account_number": "2224440041626905",
    "notes": {"k": "v"},
    "idempotency_key": "batch_<14-char entry id>",
    "payout": {"amount":"","amount_in_rupees":"","currency":"","mode":"","purpose":"",
               "narration":"","scheduled_at":"","reference_id":"","skip_workflow":""},
    "fund":   {"id":"","account_type":"","account_name":"","account_IFSC":"",
               "account_bank_identifier":"","account_number":"","account_vpa":"",
               "account_phone_number":"","account_email":""},
    "contact":{"type":"","name":"","email":"","mobile":"","reference_id":""}
  }
]
```

Every scalar is a **string** in the DTO (including `amount`).

**Limit** — `MaxBulkPayoutsLimit = 15`
(`internal/app/bulkPayoutsProcessor/constants.go:6`); >15 rows in one call is a
400 for the whole call (`service.go:48-62`). batch-sim's chunk size is 5
(`payout.json` `"bulkSize": 5`) so this ceiling is never approached, but it is
enforced locally as a hard cap.

**Response** — `dtos.BulkPayoutsAPIResponse`
(`internal/app/dtos/bulkPayoutsAPIResponse.go:9-13`), HTTP 200
(`bulkPayoutsController.go:113`):

```json
{"entity":"collection","count":2,"items":[ <payout>, ..., <error>, ... ]}
```

`items` is **successes first, then errors** (`service.go:137-146`) — *not* in
request order — so batch-sim matches results back to rows by
`idempotency_key`, which is present on **both** shapes:

* success item = `dtos.PayoutApiResponse` — carries `id`, `status`,
  `idempotency_key`, `batch_id`, `amount`, … (`payoutApiResponse.go:12-37`);
* error item = `dtos.BulkPayoutErrorResponse` —
  `{"idempotency_key":…, "batch_id":…, "http_status_code":…, "error":{"code":…,"description":…}}`
  (`bulkPayoutErrorResponse.go:9-19`).

**Duplicate-submit semantics (PS side, real).** PS keeps a
`bulk_idempotency_keys` row per (merchant, idempotency key). On a repeat with a
key it has already seen it does **not** create a second payout — it fetches and
returns the existing one, as a *success* item
(`internal/app/bulkPayoutsProcessor/core.go:352-390`,
`GetPayoutByPayoutId` at `:385`). It also takes a mutex on
`fmt.Sprintf(appConstants.ResourceCreateBulkPayout, batchID, item.IdempotencyKey)`
"to avoid duplicate payout creation if batch service retries for same payout with
same idempotency key" (`core.go:288-295`). This is what makes batch-sim's retry
and re-process paths safe, and it is the behaviour batch-sim's tests assert.

### 3.2 APPROVE / REJECT — `POST {PS_API_URL}/v1/payouts/payouts_internal/{payout_id}/{approve|reject}`

Route: PS `internal/routing/router/payout_internal_routes.go:70-80`
(`/payouts_internal/:payout_id/approve` `:71`, `…/reject` `:77`); group
middleware `BasicAuth(cred.API, cred.Workflow, …)` at `:16`.

Identical to the contract `substitutes/workflow-sim/CONTRACT.md` already
documents and exercises, and deliberately kept byte-identical to it:

* **Auth**: Basic `rzp_live` / contents of `WORKFLOW_CALLBACK_PASS_FILE`
  (default `/run/secrets/auth_workflow_payouts`) — `cred.Workflow`.
* **Headers**: `x-creator-id: <creator id>`, `X-Razorpay-Account: <merchant id>`.
  batch-sim additionally sends `x-batch-id: <batch id>`; that is **inferred**
  (it mirrors the monolith's `x-batch-id` propagation on the create path and is
  harmless — PS only reads it into the request context, `request_context.go:39-45`).
* **Body**: approve `{"queue_if_low_balance": <bool>}`; reject `{}`.
* **Success**: **200, 201 or 409**. 409 = `PayoutNotInPendingStatus` →
  `ErrorConflictAlreadyExists` / `http.StatusConflict`
  (`internal/controllers/payoutController.go:1058-1066`) and is
  idempotent-safe: the payout already left `pending`.

`queue_if_low_balance` defaults to `false`; it can be set per batch via
`settings.queue_if_low_balance`. **Inferred**: the real monolith path
(`Service.php:3574` → `Workflow.php`) chooses this value; batch-sim exposes it
as a setting rather than guessing.

---

## 4. Idempotency-key derivation

### 4.1 `payout` and `payout_approval` — `"batch_" + BatchEntry.id`

```java
protected String idempotentKeyPrefix = "batch_";            // BulkApiCallDataProcessorImpl.java:106
String idempotentKey = idempotentKeyPrefix + record.getId(); // :163
apiRequestContext.put(idempotentKey, bulkApiContextDTO);     // :166
```

`record.getId()` is the `BatchEntry` primary key
(`entity/BatchEntry.java:28-32,55-59`), generated by
`commons/CustomIdGenerator.java` as **exactly 14 characters** of base-62:
base-62 of a nanosecond timestamp plus 4 random base-62 digits
(`CustomIdGenerator.java:37-51`, `Assert.isTrue(id.length() == 14, …)`).
batch-sim mints ids the same shape (14 chars, base-62, time-ordered prefix), so
keys are `batch_` + 14 chars = 20 chars.

The key is **stable for the life of the entry**: a retry, a re-process after a
crash, or a duplicate submit of the same batch replays the *same* key, and PS
returns the already-created payout instead of making a second one (§3.1).

### 4.2 v2 `payouts_*_bene_*` — SHA-1 row hash + cache + mutex

`CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.java:104-172` with
`CacheBasedIdempotencyHelper.java:26-62`:

1. `recordCompositeKey` = the row's values for
   `PayoutConstant.getPayoutIdempotencyColumns()`
   (`PayoutConstant.java:70-79,92-105`) — the long v2 template headers —
   non-empty ones only, joined with `":"` (`CacheBasedIdempotencyHelper.java:39-53`).
2. `hash = SHA1(recordCompositeKey)` (`:30`, `HashUtils.generateSha1Hash`).
3. cache key = `"payouts:%s:%s" % (merchant_id, hash)`
   (`PayoutConstant.java:48`), TTL **24 h** (`:49`).
4. cache **miss** → take a Redis mutex `"payouts:mtx:%s:%s"` for **300 s**
   (`PayoutConstant.java:51-52`), mint a fresh id, store it, send
   `"batch_" + id` (`:154-163`). Mutex not acquired →
   `MUTEX_ACQUIRE_FAILED_ERROR_MESSAGE` (`PayoutConstant.java:56`), no API call.
5. cache **hit**, value `"<ikey>"` (in flight) → reuse that key (`:120-129`).
6. cache **hit**, value `"<ikey>:<payout_id>"` (already created) → **no API
   call**; the row fails locally with
   `DUPLICATE_PAYOUT_ERROR_MESSAGE` (`PayoutConstant.java:54`), status **400**
   (`PayoutConstant.java:59`), written to the `error.description` column
   (`:58`).
7. the same hash twice **within one chunk** → second row fails locally with
   `DUPLICATE_PAYOUT_IN_SAME_FILE_ERROR_MESSAGE` (`PayoutConstant.java:55`),
   `CacheIdempotencyEnabledBulkApiCallDataProcessorImpl.java:66-71`.

batch-sim implements all seven, with the cache in its own SQLite store instead
of Redis and the "mutex" as a process-local lock (single worker thread → the
lock can never fail to be acquired, so step 4's failure branch is reachable only
via `/_arena/fail-next`).

---

## 5. Credentials reused — and why each is the faithful choice

**No new secret is introduced.** batch-sim mounts the existing `secrets-kong`
volume read-only at `/run/secrets`, exactly as `kong-lite` and `workflow-sim`
already do (`docker-compose.yml:1614`, `:1983`).

| purpose | credential | why this one |
|---|---|---|
| Basic auth on `POST /v1/payouts/bulk` | `api` : `/run/secrets/auth_api_payouts` | The route requires `cred.API` (`payout_internal_routes_with_passport.go:16`). This is *identical* to what kong-lite injects for PS calls (`docker-compose.yml:1622-1624`, `PS_API_AUTH_USER: "api"`), and `gen-secrets.sh:59` documents `auth_api_payouts` as "username api — cred.API, inbound on payouts-api's /v1/payouts group". In production the monolith holds this credential; batch-sim stands in for the monolith on this hop, so it holds the same one. |
| `X-Passport-JWT-V1` | signed with `/run/secrets/passport_private_key` | The passport is *minted at the edge*, not carried by Batch. In production the monolith forwards the proxy passport it received (`BulkPayout.php:50-84`). The twin's only passport signer is kong-lite (`docker-compose.yml:1619`), and PS validates RS256 against the JWKS that key backs. Re-signing with the same key — using the same `_common/rsa_sign.py` helper and the same claim shape kong-lite emits (`kong-lite/server.py:152-175`) — is the only way to produce a passport the **real** PS accepts without forging or weakening auth. |
| Basic auth on `payouts_internal/{id}/approve` | `rzp_live` : `/run/secrets/auth_workflow_payouts` | `cred.Workflow`, exactly as `workflow-sim` drives the same route (`workflow-sim/CONTRACT.md`, `docker-compose.yml:1989-1990`). Reusing it keeps *one* approval identity in the twin; inventing a second would make approval-path findings ambiguous. |
| inbound auth on batch-sim's own API | none by default; optional `BATCH_SIM_INBOUND_AUTH_FILE` (`"user:pass"`) | Real Batch is behind the internal edge and authenticates callers there, not in `BatchController`. The arena's attacker broker already gates who can reach `batch-sim`; an unconfigured stub stays open, matching `_common/base_stub.py`'s documented convention. |

Nothing is written to disk that contains a secret: passwords are read from
`/run/secrets/*` at boot into memory and never logged or persisted.

### 5.1 Why batch-sim has its own Dockerfile

batch-sim is the first substitute with a **durable store**, and that exposed a
gap in the shared runtime: `substitutes/Dockerfile` runs as the unprivileged
`stub` user (uid 10001), and a Docker **named volume mounted onto a path that
does not exist in the image is created `root:root`** — so `stub` cannot write it
and `sqlite3.connect()` fails with *unable to open database file*. Reproduced by
building the shared Dockerfile and running it with a fresh volume on `/data`.

`substitutes/batch-sim/Dockerfile` is therefore the shared file **plus exactly
one line**:

```
RUN mkdir -p /data && chown stub:stub /data
```

A volume mounted onto a path that *does* exist in the image inherits that path's
ownership, so this makes `/data` writable with the container still running as
uid 10001 under a read-only root filesystem (verified: build → run with
`--read-only --tmpfs /tmp -v <fresh volume>:/data` → create a batch → restart →
the batch is re-queued and the SQLite file survives, all as `stub`).

**Preferred alternative for the coordinator:** delete
`substitutes/batch-sim/Dockerfile`, add that one line to
`substitutes/Dockerfile`, and set this service's `dockerfile: Dockerfile`. That
restores the one-shared-runtime convention and helps every future stateful stub.
This lane may not edit existing files, hence the local copy.

---

## 6. API SERVED

Primary paths are `/v1/batches*`; the real service's own prefix `/batch*`
(`BatchController.java:37` `@RequestMapping("/batch")`) is accepted as an alias
for every route.

### `POST /v1/batches`  (alias `POST /batch`)

Real: `BatchController.java:48-59` `create(@Valid BatchCreateDTO)` — a
`multipart/form-data` POST carrying `batch_type_id`, `name`, `version`,
`settings`, the file part, and the `X-Entity-Id` / `X-Creator-Id` /
`X-Creator-Type` / `mode` headers (`domain/BatchCreateDTO.java:22-69`,
`HeaderConstant.java:9-13`).

batch-sim accepts **both**:

* `Content-Type: application/json`
  ```json
  {"batch_type_id":"payout","name":"aug-run","version":"2.0",
   "entity_id":"ARENAM...","creator_id":"...","creator_type":"user","mode":"live",
   "settings":{"scheduled_at":null},
   "rows":[{"RazorpayX Account Number":"...", "...": "..."}]}
  ```
* `Content-Type: multipart/form-data` with the same fields as form parts plus a
  `file` part holding **either** a JSON array of row objects **or** a CSV whose
  first line is the header row (`payout.json` `"linesToSkip": 1`,
  `"useFileHeaderName": true`).

`entity_id` comes from the `X-Entity-Id` header, falling back to the body field;
absent → **400 `X_ENTITY_ID_NOT_PRESENT` / "X-Entity-Id is required"**
(`exception/messages/ErrorMessages.java:29`). An unknown `batch_type_id` → 400
(`@BatchTypeIdConstraint`, `BatchCreateDTO.java:40`).

Returns **200** with the `Batch` entity (§7) and enqueues it for the worker.
Rows are persisted as `batch_entries` in `CREATED` before the response is sent,
so the entry ids (and therefore the idempotency keys) exist before any PS call.

### `GET /v1/batches/{id}`  (alias `GET /batch/{id}`)
Real: `BatchController.java:103-112` `getById`. Returns the `Batch` entity.

### `GET /v1/batches/{id}/entries`
`?status=CREATED|PROCESSED|FAILED&limit=&offset=`. Returns
`{"count":N,"total":M,"entries":[…]}` of `BatchEntry` rows (§7). There is no
single equivalent public route in the real service (entries are read through
`batchEntryDataReader` inside the job and surfaced only via the S3 output file)
— **this route is a batch-sim addition** for the twin's evidence plane.

### `POST /v1/batches/{id}/process`
Re-trigger. Mirrors `BatchController.java:74-86` `triggerBatch` /
`:61-72` `process`. Re-runs the job for entries **not** already `PROCESSED`,
reusing their existing idempotency keys.

### `GET /health`, `GET /ping`
Unauthenticated `200 {"status":"ok","service":"batch-sim",...}`, per the arena
stub convention (`_common/base_stub.py`) and the shared Dockerfile healthcheck.

---

## 7. Entities and statuses

### Batch (mirrors `entity/Batch.java:38-129`)

```json
{"id":"<14 chars>","entity_id":"ARENAM…","name":"…","batch_type_id":"payout",
 "mode":"live","creator_id":"…","creator_type":"user",
 "status":"COMPLETED","total_count":7,"processed_count":7,
 "success_count":6,"failure_count":1,"attempts":1,
 "amount":700000,"processed_amount":600000,
 "settings":{…},"schedule":null,"created_at":…,"updated_at":…,
 "outcome":"partially_processed"}
```

`status` uses the **real** enum, verbatim, from `enums/BatchStatus.java:7`:

```
CREATED, SCHEDULED, STAGING, VALIDATING, PROCESSING, OUTPUT,
PAUSED, CANCELLED, FAILED, COMPLETED, VALIDATED, VALIDATION_FAILED, RESUMED
```

Lifecycle driven by batch-sim, matching the `batchStatusUpdateListener` steps
declared in `payout.json` / `payout_approval.json`:

```
CREATED ──> STAGING ──> PROCESSING ──> OUTPUT ──> COMPLETED
                            │
                            └────────────────────> FAILED
```

`STAGING` = rows persisted as entries (the `DataStaging` step,
`"batchStatus": "STAGING"`). `PROCESSING` = chunks being sent
(`"batchStatus": "PROCESSING"`). `OUTPUT` = all chunks done, results
collected (`"batchStatus": "OUTPUT"`; in the real service this step writes the
result CSV — batch-sim just records per-entry responses). `FAILED` only when a
`failOnServerError: true` type hits a persistent 5xx (§8).

> **There is no `processed` / `partially_processed` status in the real Batch
> service.** A finished batch is `COMPLETED` whether every row succeeded or not;
> the split is carried by `success_count` / `failure_count`
> (`Batch.java:81-91`). Because the twin's journeys want a single-word outcome,
> batch-sim adds a **derived, non-source** field `outcome`, computed at read
> time and never stored:
>
> | `outcome` | condition |
> |---|---|
> | `created` / `processing` | status `CREATED`/`SCHEDULED` / `STAGING`,`PROCESSING`,`OUTPUT` |
> | `processed` | `COMPLETED` and `failure_count == 0` |
> | `partially_processed` | `COMPLETED` and `0 < failure_count < total_count` |
> | `failed` | status `FAILED`, or `COMPLETED` with `failure_count == total_count` |

### BatchEntry (mirrors `entity/BatchEntry.java:26-59`)

```json
{"id":"<14 chars>","batch_id":"…","seq_number":3,
 "row_data":"{…original row…}",
 "response_data":"{…}",
 "status":"PROCESSED",
 "idempotency_key":"batch_<entry id>"}
```

`status` ∈ `CREATED, VALIDATED, FAILED, PROCESSED`
(`enums/BatchEntryStatus.java:5`), verbatim.

`response_data` holds the item PS returned for this row (a payout entity, or
the `BulkPayoutErrorResponse`), plus batch-sim's own envelope:
`{"http_status_code":…, "attempts":…, "item":{…}}`. For approvals it holds
`{"action":"approve","http_status_code":200,"response":{…}}`.

> **Fidelity note.** The real `batchEntryDataWriter` marks a row `PROCESSED`
> when the call returns one of `successStatusCode` (`[200]` in both configs) and
> writes the error object into the row otherwise — the entry's *status* tracks
> the HTTP call, and the per-row error lands in the output file's
> `error.code`/`error.description` columns
> (`payout.json` `dataWriter`, `genericFileWriter` columns). batch-sim is
> stricter and more useful for the twin: a row whose PS item is a
> `BulkPayoutErrorResponse` is marked `FAILED` (not `PROCESSED`-with-an-error),
> and counted in `failure_count`. This is a **deliberate divergence**; assert on
> `success_count`/`failure_count`, not on entry status, if you need real-Batch
> parity.

---

## 8. Chunking, retries, error handling

**Chunking.** `bulkSize: 5` in both `payout.json` and `payout_approval.json`
(`dataProcessingStepProcessor` step). batch-sim sends entries in order, 5 per PS
call, hard-capped at `MaxBulkPayoutsLimit = 15`
(`bulkPayoutsProcessor/constants.go:6`) if the size is overridden.

Approvals are one PS call **per row** (the real approval is per-row inside the
monolith, `Service.php:3574`), but the rows are still walked in chunks of 5 so
that `bulkSize` and the monolith's "max 15 rows per `bulk_approve` call"
(`Service.php:3546`) both hold.

**Retries.** BATCH retries the *whole chunk* on 5xx and on socket
timeouts/connection failures:

* retryable statuses `500, 501, 502, 503, 504`
  (`utils/StatusCodeUtil.java:19`), minus `excludedStatusFromRetry`;
* `SocketTimeoutException` and `ResourceAccessException` are always retryable
  (`ApiCallDataProcessorHelper.java:66-69`);
* fixed backoff by default (`chooseRetryTemplateTypeBasedOnInput` falls through
  to `fixedBackoffRetryTemplate`, `ApiCallDataProcessorHelper.java:96-101`);
* `resttemplate.retry.max-attempts=5`, `resttemplate.read.back-off-period=5000`
  (`src/main/resources/application.properties:201-202`) — 5 total attempts,
  5 s apart.

batch-sim uses the same numbers, overridable with
`BATCH_SIM_MAX_ATTEMPTS` / `BATCH_SIM_BACKOFF_MS` (tests set them to 2 / 1).
**4xx is never retried** — it is a per-row/per-call client error.

**Recovery** (`getRecoveryContext`, `BulkApiCallDataProcessorImpl.java:379-470`):

| exhausted with | `failOnServerError: false` (`payout`, v2) | `failOnServerError: true` (`payout_approval`) |
|---|---|---|
| timeout / connection refused | every row in the chunk gets `{"code":408,"description":<cause>}`; batch continues | same (timeouts are handled before the `failOnServerError` branch, `:384-407`) |
| persistent 5xx | every row gets `{"code":<5xx>,"description":…}`; batch continues (`:415-427`) | **`JobFailureException`** → the whole batch goes `FAILED` (`:430-431`, `ErrorMessages.API_SERVER_DOWN`) |

A non-5xx, non-2xx response (e.g. 400 from `MaxBulkPayoutsLimit`, or 401 from a
bad credential) is not retried: every row in the chunk is stamped with that
status and description.

---

## 9. Control / evidence plane (`/_arena/*`)

Everything under `/_arena/` is for the twin operator and the verifier; the arena
attacker broker denies these paths to the workload, per the convention
`workflow-sim/CONTRACT.md` and `_common/base_stub.py` establish.

| route | effect |
|---|---|
| `GET /_arena/health` | `{"status":"ok","service":"batch-sim","batches":N,"queued":N,"worker_alive":bool,"passport":"ready"\|"unavailable"}` |
| `GET /_arena/batches` | every batch with its counters and derived `outcome`; `?entity_id=`, `?status=`, `?limit=` |
| `GET /_arena/batches/{id}` | one batch **with** all its entries and their recorded PS responses |
| `POST /_arena/batches/{id}/reprocess` | resend **every** entry (including `PROCESSED` ones) with its original idempotency key. Exists to demonstrate the PS duplicate-key path (§3.1) — it must produce no new payouts. |
| `POST /_arena/fail-next` | `{"count":N,"status":503,"scope":"create"\|"approve"\|"any","reason":"…"}` — short-circuit the next N outbound PS calls with that synthetic status (or `"drop":true` to simulate a connection failure), then behave normally. `{"clear":true}` cancels. Bounded, local, and never changes the destination. |
| `GET /_arena/calls` | the last 200 outbound PS calls: method, URL, chunk size, idempotency keys, attempt number, status. |

---

## 10. Simplified vs byte-compatible

**Byte-compatible with the real wire contract**

* the PS `/v1/payouts/bulk` request array, field-for-field (§3.1);
* the PS approve/reject request, header-for-header (§3.2);
* the idempotency-key derivation for all three families (§4);
* chunk size, the 15-row ceiling, the retryable-status set, attempt count and
  backoff (§8);
* the `BatchStatus` / `BatchEntryStatus` vocabularies and the `Batch` counter
  fields (§7);
* the 14-character base-62 id shape for batches and entries.

**Simplified or absent**

1. **No S3 / file store.** Real Batch stores the uploaded file and writes a
   result CSV (`genericFileWriter`, `OutputCreation` step); the `batch/sendmail`
   completion tasklet is not implemented at all. Rows arrive as JSON (or an
   inline CSV part) and results are read back over HTTP.
2. **No Spring Batch engine.** No job repository, no step/chunk checkpointing,
   no `saveState`, no `JobExecution`. One worker thread walks chunks
   **sequentially**; `maxThreads` (10 for `payout`, 6 for `payout_approval`) is
   **not** honoured, so batch-sim cannot reproduce concurrency bugs that need
   two chunks in flight at once.
3. **No Postgres.** State is SQLite under `BATCH_SIM_DATA_DIR` (default `/data`,
   a named volume). Same columns, different engine.
4. **No Redis.** The v2 idempotency cache and mutex live in the same SQLite
   file / a process lock. TTLs (24 h / 300 s) are honoured.
5. **Monolith hop collapsed** (§1) — so the monolith's own validations are
   *not* exercised: PS-migration routing, per-merchant feature checks, the
   `applications.batch` proxyAuth check, and the monolith-side
   `processEntryForBulkPayoutApproval` (`Service.php:3574`) all vanish. A bulk
   payout that the real monolith would reject before ever reaching PS will
   succeed here.
6. **No validate step.** The `*_validate` v2 batch types and
   `POST /v1/payouts/bulk/validate` (`payout_bulk_routes.go:22-27`, which
   additionally requires `LegacyAuthTypeProxy` at `:18`) are not implemented.
7. **No staging validation.** `genericDataProcessor` / `genericDataValidator`
   column-level validation is not reproduced; a malformed row is discovered by
   PS, not by batch-sim.
8. **No scheduling.** `schedule` is stored and echoed but never fires;
   `SCHEDULED`/`PAUSED`/`CANCELLED`/`RESUMED` are accepted vocabulary, never
   entered.
9. **Entry status on a per-row PS error diverges** from the real writer — see
   the fidelity note in §7.
10. **Needs working DNS at boot.** `http.server.HTTPServer.server_bind` calls
    `socket.getfqdn()`, so the process appears to hang before "listening" on a
    network with no resolver (e.g. `docker run --network none`). Every other
    arena stub shares this; `rzp-arena` has DNS, so it is only a caveat for
    isolated smoke runs.
11. **No `X-Idempotent-Key` header.** The real code has that line commented out
    (`RestCallUtils.java:47-49`); batch-sim likewise does not send it. The
    idempotency key travels only in the body, per the PS DTO.

---

## 11. Open questions

1. **Which passport `auth_type` does the monolith actually forward for
   `payouts/bulk`?** The monolith route is `$proxy`-only
   (`Route.php:8034-8035`), so a proxy passport is the reasonable reading, and
   the sibling route `/v1/payouts/bulk/validate` pins
   `LegacyAuthTypeProxy` explicitly (`payout_bulk_routes.go:18`). But `/bulk`
   itself passes an **empty** list (`payout_internal_routes_with_passport.go:18`)
   and so does not check. batch-sim mints exactly what kong-lite mints (which
   carries no explicit legacy-auth claim) — sufficient for the twin, but it means
   batch-sim **cannot** be used to test auth-type enforcement on this route.
2. **`queue_if_low_balance` on bulk approval.** Not observed in the sources this
   lane read; defaulted to `false` and made a batch setting (§3.2).
3. **`skip_workflow`.** Present in the PS DTO (`bulkPayoutCreateRequest.go:27`)
   and set to `"false"` by the v2 templates, but absent from `payout.json`.
   batch-sim sends it only when the row or settings supply it. Whether the
   monolith injects it for the v1 `payout` type is unverified.
4. **Post-create state.** PS may leave bulk-created payouts in
   `batch_submitted`, drained by
   `POST /v1/cron/process_batch_submitted_payouts`
   (`internal/routing/router/cron_routes.go:46-47` →
   `InitiateBatchSubmittedPayouts` → `job/batch_submitted_merchants.go`). The
   twin's `cron-driver` already drives that route every 300 s
   (`scripts/cron-driver/driver.py:44`), so batch-sim does **not** poke it —
   but a journey that wants a deterministic result should call the cron route
   itself rather than wait.
5. **`amount` vs `amount_in_rupees`.** Both are strings and both are forwarded
   as given; which one PS honours when both are set was not traced.
6. **Batch's own `attempts` counter** (`Batch.java:93-95`) is incremented once
   per job run here; the real service's exact increment points were not traced.
