# Substitute contract — Batch service (bulk create / bulk approve)

Tier: **CONTRACT-FAITHFUL SUBSTITUTE** (thin driver). Real Batch (Java/Spring, 7 deployments, Postgres, S3, SQS)
adds no behaviour beyond envelope construction (lane 08 §Batch, lane 02 §5). Source `razorpay/batch@3839dd7`.

## Bulk create (`payout.json`)
- Input: CSV (20 named columns, header row) → rows chunked into groups of **5** (`bulkSize:5`, `maxThreads:10`, `chunkSize:1`).
- Target: `POST https://<api base>/v1/payouts/bulk` (public API domain, i.e. via Kong → monolith → PS in prod; twin: PS `POST /v1/payouts/bulk`).
- Auth: Basic `rzp_<mode>_<entity_id>` : `BATCH_API_SECRET` (authType PROXY). Headers `X-Batch-Id`, `X-Creator-Id`, `X-Creator-Type`, `X-Entity-Id`, `Content-Type: application/json`.
- Body: raw JSON array, one element per row; `idempotency_key = "batch_" + BatchEntry.id` (14-char `CustomIdGenerator` id); no Redis mutex, no cross-attempt coordination.
- Response: `{items: [{idempotency_key, id?, http_status_code, error{code, description}}]}` matched back by `idempotency_key`; whole group retried on non-2xx or any item `http_status_code==500`, fixed backoff **5 attempts × 5000 ms**.
- PS limits: `MaxBulkPayoutsLimit=15` rows per request; `bulk_idempotency_keys UNIQUE(idempotency_key, merchant_id)`; per-`(batch_id, idempotency_key)` mutex; Splitz `BulkPayoutConcurrencyExperiment` selects concurrent vs sequential.

## Bulk approve (`payout_approval.json`) — lands on the MONOLITH, not PS
- Target: `POST https://<api base>/v1/payouts/bulk_approve` (`api/app/Http/Route.php:2319`), same auth/headers; `X-Batch-Id` mandatory (400 otherwise); ≤15 rows.
- Row: `{payout_update_action: "A"|"R", idempotency_key, user_comment, payout{id, amount, currency, mode, purpose, narration, status}, fund{id}, contact{name, id}, razorpayx_account_number}`.
- Monolith behaviour: per row `Core::approvePayout/rejectPayout` → WFS `ActionAPI` when `workflow_entity_map` row exists, else legacy checker; **no OTP**; fetch via local `payouts` table (dropped in prod → likely failing today, UNKNOWN).
- Response: single JSON accumulating per-row results (not the `items[]` envelope of create).

## Entities (Postgres) for a fuller substitute
`Batch{id, entityId, name, batchTypeId, mode, creatorId, creatorType, scheduled, uploadCount, processedCount, failureCount, totalCount, successCount, attempts, status, settings jsonb, amount}`;
`BatchEntry{id, batchId, seqNumber, rowData, responseData, status}`; `BatchStatus{CREATED,SCHEDULED,STAGING,VALIDATING,PROCESSING,OUTPUT,PAUSED,CANCELLED,FAILED,COMPLETED,VALIDATED,VALIDATION_FAILED,RESUMED}`; `BatchEntryStatus{CREATED,VALIDATED,FAILED,PROCESSED}`.
Dashboard polls Batch `GET /batch/{id}`; Batch never polls PS.
