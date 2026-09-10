# Daily snapshot and incremental-refresh plan — S2P domain (primary) and Payroll seam (secondary)

## Snapshot inputs (what is captured every day)

| Input | Source | Capture method | Why |
|---|---|---|---|
| Pinned commits of vendor-payments, vendor-experience, accounting-integrations, proto, workflows, api, payouts, x | `gh api repos/razorpay/<repo>/commits/master` (or `git ls-remote`) | commit SHA + `--depth 1` refetch only when SHA changes | source drift |
| Contract surface | `proto/vendor-payments/**`, `proto/tax-payments/service.proto`, `proto/vendor-portal`, `proto/accounting-payouts`, `proto/vendor-payments/callback/v1` | buf generate; diff generated stubs | breaking rpc changes |
| Monolith seam files | `api/app/Http/Route.php` (tax-payments, vendor-payments, payouts_internal, internalContactPayout, applications allow-lists), `api/app/Models/Payout/SourceUpdater/*`, `api/app/Models/PayoutSource/Entity.php`, `api/app/Services/{VendorPayments,TaxPayments,XPayroll}/Service.php`, `api/config/applications*.php` | file hash + diff | route/updater changes invalidate the monolith substitute |
| Payouts seam files | `payouts/internal/auth/headers.go`, `internal/app/contact/type.go`, `config/prod.toml [source_update_topics] [auth.*] [hvault]`, `pkg/sourceupdater/event.go`, `internal/app/payouts/processor/baseHelper.go` | file hash + diff | allow-list / gate / topic changes |
| Feature flags | `config-proto/rzp/x/**` (payouts, onboarding, dashboard uiconfig, vendor_experience) | proto diff | new gates |
| Workflow config types | `workflows/internal/constants/constants.go`, `workflows/config/prod.toml` clients | diff | new callers/types |
| Deployment topology | `kube-manifests/prod/{vendor-payments,vendor-experience,accounting-integrations,tax-compliance,opfin*}/values.yaml`, `terraform-kong/prod/{vendor-payments,vendor-experience,payroll,backend-accounts-receivable}` | diff | new workers/topics/routes |
| Alerts | `alert-rules/rules/prod-rules/{vendor_payments_sloth_rules,vp_api_4xx_5xx_rules,x_accounting_integration*,xpayroll_rules}.yaml` | diff | SLO/metric names |
| Access state | `gh api repos/razorpay/<name>` for the 48 manifest names | status table | detect newly granted repos (re-run `ACCESS_MANIFEST.csv` probe) |
| Org listing | `gh repo list razorpay --limit 2000` | diff vs `findings/gh_repo_listing_2026-09-07.txt` | new/renamed repos (e.g. `tax-payments` reappearing) |
| Graph | `reports/expansion/GRAPH_PATCH.json` rebuilt by `scripts/build_graph_patch.py` | validation JSON must stay `ok: true` | integrity |

## Incremental refresh procedure (daily, idempotent)

1. `snapshot`: record SHAs + file hashes above into `reports/expansion/snapshots/YYYY-MM-DD.json`.
2. `diff`: compare with previous day; classify each change as `contract`, `seam`, `config`, `topology`, `access`, `none`.
3. `act`:
   - `contract` → regenerate stubs, rerun contract tests (§7 of `REPLICATION_WORKFLOW_S2P.md`).
   - `seam` → re-derive monolith-stub/workflow-engine handlers from the diff; rerun J1–J10.
   - `config` → update arena config overlays; rerun boundary tests (J4).
   - `topology` → update compose profile (new worker/topic); rerun clean boot.
   - `access` → clone newly granted repo to the clone root, append to inventory, raise tier in `ACCESS_MANIFEST.csv`, open a graph-patch task.
   - `none` → run the smoke subset (J1, J3) only.
4. `gate`: `make s2p-acceptance` (to be added) must reproduce the previous gate JSON unless a classified change explains the delta.
5. `record`: append to `reports/expansion/snapshots/CHANGELOG.md` with the evidence lines that changed.

## Payroll seam (secondary) snapshot inputs

`api/app/Http/Route.php:18626-18638` allow-list, `api/config/applications_v2.php` app `xpayroll`,
`api/app/Services/XPayroll/Service.php`, `payouts/.../baseHelper.go:185-191`, `pkg/dcs/features/features.go`,
`config-proto/rzp/x/merchant/payouts/payroll_payouts.proto`, `fts/config/env.default.toml [payroll_mid_details]`,
`terraform-kong` payroll routes, `kube-manifests/prod/opfin*/values.yaml` cron names, `alert-rules/.../xpayroll_rules.yaml`,
and the access probe for the 9 payroll repo names. Any change re-generates the `opfin-sim` contract.

## Retention

Daily snapshots kept 30 days; weekly kept 1 year; every snapshot that changed a gate result is tagged.
