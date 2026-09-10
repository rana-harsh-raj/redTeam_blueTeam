# 22b — alert-rules addendum (root agent; corrects L3's "alert-rules unavailable")

Repo: razorpay/alert-rules @ c372beabedf833a2a2c7aec862a4f78ce19e5d00 (cloned; L3 looked in the wrong path).

| Rule file | Alerts | Relevant alerts |
|---|---|---|
| `rules/prod-rules/fts_rules.yaml` | 164 | `[X] [BT] [FTS] FTS to Payouts Transfer Update webhook failure` — two thresholds on `sum(increase(fts_transfer_webhook_update_failure_count{status=~"PROCESSED|FAILED|REVERSED"}[15m])) by (status)` > 50 and > 10, `for: 10m`, notifies subteam SQPJD9JQ1 (lines ~2330-2375); `[X] [BT] [FTS] ICICI V2 UPI Processing Time downtime detection`; AXIS2 IRCTC recon alerts; `sum(payouts_service_stuck_payouts_count{channel="IDFC",mode="IMPS",status="non_terminal"}) > 5` (line 4252) |
| `rules/prod-rules/payouts_rules.yaml` | 101 | `[X] [Payouts-Service] Payout SLA Breach Percentage High for IMPS/UPI` (1894), `… for NEFT` (1920), settlements variants (1948, 1974), `Payout SLA TiDB Query Failures` (2002), `Stuck Payouts IDFC IMPS > 5 in 15m` (2224), `Payroll Payouts Stuck in Non-Terminal State > 1hr` (2247, `payouts_service_stuck_payouts_count{source="xpayroll",status="non_terminal"} > 0`), `[P1] Payout Source Updater job failures rate greater than 50 for last 15 min` |
| `rules/prod-rules/shadow_payouts_rules.yaml` | 79 | shadow-environment copies of payouts rules |
| `rules/prod-rules/ledger_rules_app.yaml` | 214 | `[MY] [X] [Ledger] JOURNAL_CREATE_FAILED`, `ACCOUNT_DISCOVERY_ACCOUNT_NOT_FOUND` (X tenant) and PG equivalents |

UNRESOLVED_QUESTIONS #20 (what alerts on `TransferWebhookUpdateFailureCount`) — **closed**: FTS abandonment after 3 retries is alerted only in aggregate (>10 or >50 failures per 15 min by status); a single stuck payout does not page. Stuck-payout alerts exist only for the IDFC/IMPS channel and xpayroll source; there is no generic "initiated older than N" alert, consistent with the code finding that the SLA monitor excludes `initiated`.
