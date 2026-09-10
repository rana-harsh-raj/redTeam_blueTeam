# Substitute contracts — account statements (XAS), recon/ART, and repair surface

Sources: `reports/fidelity/raw/11_statements_recon_repair.md`, `06_*.md` §2.7. Repos `x-account-statements@e73fd5a`, `recon@6974fec`, `api@2d665f9`, `payouts@4bf3dbf`, `fts@2a09e76`.

## 1. x-account-statements (XAS) — REPRESENTATIVE → CONTRACT-FAITHFUL SUBSTITUTE
Real XAS (Go, 5 bank adapters via Mozart, MySQL, SQS, Kafka CDC, Elasticsearch) is buildable but its only payout-relevant behaviour is matching + one outbound call. Substitute:
- **Inputs**: synthetic statement rows `{account_number, posted_date, type credit|debit, bank_serial_number, amount (paise), currency, channel RBL|AXIS|ICICI|YESBANK|IDFC, bank_transaction_id (CMS ref), utr, gateway_reference_number, balance}`; dedupe key `account_number+posted_date+type+bank_serial_number+amount+channel+bank_transaction_id` (`fetch/service/service.go:255-286`). Source events (payout/reversal) consumed from PS's `x_account_statement_source_event` queue (`XasEventDetails{entity_id, entity_type, utr, event_created_timestamp, event_id, gateway_ref_number, cms_ref_number, status, mode, amount, balance_id, fund_account_id, contact_id, name, contact, email, payout_purpose, contact_type}`).
- **Matching**: UTR → GatewayRefNumber → CmsRefNumber (`enrich/service/service.go:198-217`); `>2` statements for one UTR → no-op; both matched rows same type → no-op; debit → `entity_type=payout`, credit → `payout_reversal`; unmatched → `external`.
- **Outbound**: on a match, `POST <monolith>/v1/banking_account_statement/payout_update` Basic (in prod XAS presents under an existing app credential; only `payouts_service` is allowlisted) body `{bas_id, entity_id, entity_type payout|payout_reversal, merchant_id, transaction_date, converted_from_external, utr, grn, cms_ref_no}` (`gateway/api/service/service.go:34-35,108-127`).
- **Monolith/PS handler semantics to reproduce** (`api Core.php:10286-10379`; `payouts core.go:7203-7395`): `payout` → set transaction_id (idempotent); `payout_reversal` and payout `failed` → create reversal + `reversed` (PS EventReversed) + mail/SMS; else stamp existing reversal; PS-owned → monolith forwards to PS `POST /v1/payouts/banking_account_statement/payout_update` (Basic API). PS guard `shouldSkipUpdateDueToMismatch` (Splitz `xas_source_event_utr_and_grn_match_experiment`) skips when none of utr/grn/cms match the payout.
- **Read API for FTS**: `GET /v1/account_statements?entity_ids=&entity_type=` and fetch-by-multiple-references, used by FTS `retry_preprocessor_service.go` — return debit/credit presence so FTS can apply: both or credit-only → retry allowed; debit-only → `CodeDebitEntryExistInStatement`; none → `CodeMissingDebitAndCreditEntryInStatement`.
- x-bank-statement-sync: empty in production too; do not build.

## 2. recon / ART — protocol-faithful driver (Env 4)
Consume `{end_point, service_name ∈ {FTS, API, PRS}, method, payload}` (from a `workflow_config` row: `file_type_ids, is_last_recon_pair, is_automatic_workflow, END_POINT template, WORKFLOW_PAYLOAD_KEY, WORKFLOW_PAYLOAD_SCHEMA_KEY`, `recon/app/post_recon_service/workflow.py:9-60`), human-gated. For FTS issue `PATCH|POST /v1/attempts/{action}` or `PUT /v1/attempts/reconcile` with Basic `ART` credential, 5 retries on 5xx.
- Body (PATCH, keyed by `gateway_ref_no`): `{return_utr?, remarks (required, alpha), bank_status_code (required), utr?, status?, failure_reason?, mozart_meta{processed|failed|reversed}}`; with `return_utr` also `mozart_meta.status=="reversed"` and `mozart_meta.reversed=true` (`fts validation.go:264-294`). Admin email stamped into the transition (`ModeManual`).
- Actions (`fts service.go:864-975`): `update`, `safe_update` (Kafka `ProcessAttemptUpdate`, `is_safe_update`), `verify` (read-only bank check), `process` (bulk enqueue), `reconcile`.
- Safe-update matrix (`manual_update_handlers_factory.go:19-90`): INITIATED→PROCESSED direct; INITIATED→FAILED verify (refuse if bank PROCESSED); DEEMED_SUCCESS variants gated by verify/SLA; PROCESSED→REVERSED verify then apply; INITIATED→REVERSED not offered.

## 3. Repair surface (routes that must exist in the twin's real/substitute components)
| Owner | Route | Body | Precondition | Effect |
|---|---|---|---|---|
| PS (REAL) | `POST /v1/payouts/manual_action` | `{action, bulk_input[{payout_id}], reason, queue_if_low_balance}` | action ∈ processed_to_processing (payout AND latest status_details `processed`), approve_workflow_payouts, reject_workflow_payouts | processed→initiated (deletes processed status_details + log) / approve / reject |
| PS (REAL) | `POST /v1/payouts/retry` | `{payout_ids[]}` | none at call site | `ExecutePostCreate` re-run |
| PS (REAL) | `POST /v1/cron/payouts_sla_breach_monitor` | optional `{action}` | — | metrics only (TiDB); excludes `initiated` |
| monolith-stub | `PATCH /v1/payouts/{id}/manual/status` | `{status final, failure_reason?, fts_fund_account_id?, fts_account_type?}` | shared: initiated→processed / processed→reversed need fts ids; initiated→reversed alerts; direct: no guard | PROCESSED/REVERSED/FAILED via PS `update_payouts_with_fts`; REJECTED/CANCELLED no-op |
| monolith-stub | `PATCH /v1/payouts/manual/status_update/batch` | `{payout_ids[], status,...}` | same | batched |
| monolith-stub | `POST /v1/admin/payouts/cancel` | `{payout_ids[] (public ids), force_reject?, user_comment?}` | validatePayoutStatusForApproveOrReject | per-payout reject → PS `/payouts_internal/{id}/reject` |
| monolith-stub | `POST /v1/payouts/manual_action` | `{action, bulk_input, reason}` | allowlist 12 actions | forward 3 payout actions to PS |
| monolith-stub | `POST /v1/payouts/{id}/retry` | — | local lookup only | `processReversedPayout` |
| monolith-stub | `POST /v1/payouts/auto_expire` (cron app) | `{excluded_merchant_ids?, merchant_ids?}` | age ≥ 3 months (IST start of day) | PENDING→rejected; QUEUED/CREATE_REQUEST_SUBMITTED→failed |
| FTS (REAL) | `/v1/attempts/*` | §2 | — | attempt/transfer FSM via safe-update chain |

## 4. Observability invariants to preserve (not to "fix")
- FTS webhook failure alerts are aggregate only (>50 / >10 per 15 min, `alert-rules/rules/prod-rules/fts_rules.yaml:2332-2375`).
- The "Stuck Payouts IDFC IMPS" and "Stuck Payroll Payouts" rules query labels (`channel`, `mode`, `source`, `status="non_terminal"`) that `payouts_service_stuck_payouts_count` (single label `status` ∈ batch_submitted|create_request_submitted|scheduled) never emits → effectively dead (`payouts_rules.yaml:2222-2269`; `payouts/internal/metric/metric.go:1014-1021`).
- No production job monitors or repairs `initiated`; verifier V16 tests a stronger invariant than production enforces and must be labelled as such.
