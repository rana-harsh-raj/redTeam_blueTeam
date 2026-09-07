# admin-dashboard — RazorpayX / business-banking surface (read-only audit)

Source clone: `/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/admin-dashboard` (React 18 SPA, `js/` is import base). All paths below are `admin-dashboard/<relative>`; `L` = line. Tags: **OBS** = observed in source, **INF** = inferred, **UNK** = unknown from clone. Searched with `rg`/`grep` over `js/`, `entry/`, `config/`, `e2e/`, `docs/`, excluding `node_modules/`, `dist/`, `public/`, `__tests__/`, `stories/`.

## 0. Transport model (how every URL below resolves)

| Fact | Evidence | Tag |
|---|---|---|
| All `adminFetch/adminPost/adminPut/adminDelete/adminPatch` calls are prefixed `/admin/api/` + `{mode}/...` (mode = `live`/`test`) | `js/common/fetch.js:89-92` (comment), `:120-125` (`reqPayload.url = \`/admin/api/${reqPayload.url}\``); `absoluteUrl` bypasses prefix `:120-121` | OBS |
| Relative URLs → same origin as the SPA (`admin-dashboard.razorpay.com`); only dev server uses `baseURL: 'https://dashboard.dev.razorpay.in'` | `js/common/axiosInstance.js:3-6`; `local.config.js:1,6`; prod host in `js/admin/merchants/constants.js:444-447` (`adminDashboard: 'https://admin-dashboard.razorpay.com'`, `x: 'https://x.razorpay.com'`), axis org variant `axis.razorpay.com` `:448-450` | OBS |
| Second proxy: `/makeapicall/{path}` (multipart form with `auth:'admin'`, `mode`, `method`, `body`) — a raw admin-API relay | `js/admin/adminActions/actionModals/MakeAdminAPICall.js:104-119`, permission `make_admin_api_call` `:143`; used by `js/admin/rxNotificationConfig/services.js:14-31` and `js/admin/adminActions/actionModals/CallLedgerServiceActions.js:119` | OBS |
| Backend behind `/admin/api/{mode}/...` is referred to as the "API monolith" | `js/admin/entities/List.js:251` (`// Append batch types not in API monolith`) | OBS |
| Path segments such as `bas/lms/...`, `ledger_service/...`, `los/admin/twirp/...`, `capital_cards/admin/v1/...`, `care/twirp/...` are service-proxy prefixes under the same `/admin/api/live/` root (`LEDGER_PROXY = 'ledger_service'`) | `js/admin/ledger/data.js:1`; `js/admin/SAV/network/baseQueryHandler.ts:18` (`SAV_OMNI_PRODUCTION_PREFIX = '/admin/api/live/care'`, non-prod hits `https://care-sav-omni.dev.razorpay.in` directly `:17,28`) | OBS (paths) / INF (monolith reverse-proxies them) |
| Direct (non-proxied) hosts: `https://ifsc.razorpay.com/{ifsc}`; Splitz to `https://api.razorpay.com` (prod) / `https://api-web.dev.razorpay.in` | `js/common/util.js:256`; `js/admin/Splitz/index.js:50-51` | OBS |
| Org-gating: `RZPRoute` only renders when `isOrgRazorpay(orgData)`; `Heimdall_restrictRoutes` hides listed menu paths for non-Razorpay orgs (FE-only) | `js/admin/Content.js:1124-1128`, `:438-461`, `:1021-1028`; 5th menu tuple = "hide-if-has-permission" `:1029` | OBS |
| Menu tuple format `[title, url, comma-separated permissions, icon, hidePermission]` | `js/admin/Content.js:268` (comment), `:1029` | OBS |

## 1. Route / menu table — RazorpayX & business-banking sections

### 1a. Top-level menu entries (getLinks, `js/admin/Content.js`)

| Menu label | Route | Permission(s) | Source folder | Evidence |
|---|---|---|---|---|
| Current Accounts (only when `ENABLE_ICICI_LMS`) | `/banking-accounts` | `view_activation_form` | `js/admin/banking_accounts/` | `Content.js:263,265`; flag `js/common/data.js:458` (=true) |
| Payouts Dashboard | `/payouts/payouts-dashboard` | `payout_manual_action` (menu) — NB component declares `payouts_manual_action` | `js/admin/payouts/` | `Content.js:326`, route `:824` (RZPRoute); `js/admin/payouts/PayoutsManualActions.js:83` |
| FTS Dashboard | `/fts/actions` | `view_fts_dashboard` | `js/admin/fts/` | `Content.js:327`, route `:774`; Heimdall-restricted `:456` |
| Ledger | `/ledger/entities/journal` | `ledger_view_dashboard`; hidden if `ledger_hide_dashboard` | `js/admin/ledger/` | `Content.js:325`, routes `:751,:756` |
| RX Downtimes | `/rxdowntimes` | `view_gateway_downtime` | `js/admin/xGatewaydowntimes/` | `Content.js:303`, route `:602` |
| Corporate Cards | `/capital/corporate-cards` | `los_cards,capital_los_create_application` | `js/admin/corporateCards/` | `Content.js:274-279`, route `:921-922`; Heimdall `:451` |
| Loans / Cash Advance / Collections / Capital / Capital Reports / Capital Risk | `/capital/loans`, `/capital/cash-advance`, `/capital/collections`, `/lending/validation`, `/capital/reports`, `CapitalRisk.path` | `loans_edit,capital_los_create_application` / `loc,capital_los_create_application` / `capital_send_payment_link` / `offline_verification_service_view_deprecated` / `add_merchant_adjustment` / `CapitalRisk.permissions` | `js/admin/loans/`, `cashadvance/`, `collections/`, `capital/` | `Content.js:271,273,282,283,284,285`; routes `:598,:908-924` |
| Actions (admin action modals; many X-specific, see §1d) | `/actions` | `view_actions` + per-modal `.permission` | `js/admin/adminActions/` | `Content.js:314`, route `:616`; gate `js/admin/adminActions/ActionsList.js:13-15` |
| Merchant Team Management | `/setup-s2p` (+ `/group-types`, `/groups`, `/team-members`) | `self_serve_workflow_config` | `js/admin/s2p-setup/` | `Content.js:356`, routes `:630-636`; paths `js/admin/s2p-setup/data.js:7-11` |
| Merchant Workflows | `s2pSetupPathname.workflows_list` | `self_serve_workflow_config` | `js/admin/s2p-setup/` (+ `js/admin/merchant-workflows/`) | `Content.js:374`, routes `:630-632` |
| Workflows / Requests (admin maker-checker) | `/workflows`, `/requests` | `view_all_workflow` / `view_workflow_requests` | `js/admin/workflows/`, `js/admin/requests/` | `Content.js:373,376`; routes `:620-621,:637-638` |
| Pricing Plans (contains `banking` product / payout features) | `/forward-pricing` | `view_pricing_list` | `js/admin/plans/` | `Content.js:291`; product map `js/admin/plans/plan.js:127,134,138,171,242` |
| Settlements (PG-side; shares FTS/nodal ops) | `/settlements` | `view_settlements_dashboard` | `js/admin/Settlements/` | `Content.js:335`, route `:674` |
| "banking dashboard" group: Upload File, BRD Parser | `/bank-file-upload`, `/brd-parser` | `admin_bank_file_upload`, `banking_vas_brd_parser_access` | `js/admin/banks/`, `js/admin/brd-parser/` | `Content.js:398-401`; route `:673`, `:654-658` |
| Recon Dashboard / FinOps (ART) | `/reconciliation/live/batches`, `/finops` | `view_recon_dashboard`, `recon_operation` | `js/admin/reconciliation/`, `FinOpsDashboard/`, `reconDashboard/` | `Content.js:319-320` (PG recon; X relevance INF-low) |

### 1b. Current Accounts sub-navigation (`js/admin/banking_accounts/NavBar.js`)

| Sub-tab label | Route | Flag | Component | Evidence |
|---|---|---|---|---|
| RBL Current Account | `/banking-accounts`, `/banking-accounts/:id` | — | `List.js`, `CurrentAccountDetails.js` | `NavBar.js:18`; `Content.js:845-846` |
| ICICI Current Account | `/icici-banking-accounts`, `/icici-banking-accounts/:businessId/:applicationId` | `ENABLE_ICICI_LMS` | `IciciCa/IciciCaList.js`, `IciciCa/IciciCaDetails.js` | `NavBar.js:21`; `Content.js:854-862` |
| IDFC Current Account | `/idfc-banking-accounts`, `/idfc-banking-accounts/:id` | Splitz `isIdfcLmsEnabled` | `IdfcCa/IdfcCaList.js`, `IdfcCa/IdfcCaDetailView.js` | `NavBar.js:25`; `Content.js:866-872`; `Content.js:247` |
| ICICI Video KYC | `/icici-video-kyc`, `/icici-video-kyc/:businessId/:applicationId` | — | `IciciCa/IciciVideoKyc/*` | `NavBar.js:29`; `Content.js:889-891` |
| Multi CA | `/multi-ca` | — | `MultiCa/MultiCa.js` | `NavBar.js:32`; `Content.js:894` |
| ICICI Account Linking | `/icici-account-linking`(+details) | `ENABLE_ICICI_ACCOUNT_LINKING` (`js/common/data.js:460`) | `IciciCa/AccountLinking/*` | `NavBar.js:36`; `Content.js:874-884` |
| Telephonic Verification | `/ca-telephonic-verifications` | `ENABLE_RBL_TELE_VERIFY` (`js/common/data.js:459`) | `TeleVerification/TeleVerification.js` | `NavBar.js:41`; `Content.js:847-849` |
| Actions (acquisition) | `/acquisition-actions` | — | `AcquisitionActions.js` → `AcquistionActions/actions/{Switchbank,Applications,TransferCA,DownloadAccountStatement,UPIActivation,DelinkBankAccount}` | `NavBar.js:58`; `Content.js:668`; `AcquistionActions/actions/index.js:1-6` |

### 1c. FTS sub-navigation (`js/admin/fts/NavBar.js:11-62`) — all `RZPRoute`

| Label | Route | Component | Content.js |
|---|---|---|---|
| Actions | `/fts/actions` | `fts/Actions.js` (+ `actionModals/*`) | `:774` |
| Pool Default Routing Rules | `/fts/routing-rules` | `RoutingRules.js` | `:767` |
| Pool Merchant Routing Rules | `/fts/merchant-rules` | `MerchantRules.js` | `:775` |
| Direct Default / Direct Merchant Routing Rules | `/fts/direct-routing-rules`, `/fts/direct-merchant-rules` | `DirectRoutingRules.js`, `DirectMerchantRules.js` | `:782`, `:777` |
| Routing Weights | `/fts/routing-weights` | `RoutingWeights.js` | `:768` |
| Account Type Mappings | `/fts/account-type-mappings` | `AccountTypeMappings.js` | `:770` |
| Fail Queued Transfer | `/fts/fail-queued-transfer` | `FailQueuedTransfer.js` | `:787` |
| Schedules | `/fts/schedules` | `Schedules.js` | `:801` |
| Channel health logs / Trigger health logs / Fail Fast Status Logs / Key Value Store Logs | `/fts/channel-information-status-logs`, `/fts/trigger-status-logs`, `/fts/fail-fast-status-logs`, `/fts/key-value-store-logs` | `ChannelInformationStatusLogs.js`, `TriggerStatusLogs.js`, `ChannelFailFastStatusLogs.js`, `KeyValueStoreLogs.js` | `:792,:797,:808,:819` |
| Merchant Configurations | `/fts/merchant-configurations` | `MerchantConfigurations.js` | `:803` |
| Channel Health Summary / Trigger Status Summary | `/fts/new-channel-health-summary`, `/fts/trigger-status-health-summary` | `NewChannelHealthSummary.js`, `TriggerStatusHealthSummary.js` | `:758,:763` |
| Cron Jobs Dashboard | `/fts/cron-jobs-dashboard` | `CronJobsDashboard.js` | `:812` |
| Merchant Multi Account Routing Rules | `/fts/merchant-multi-account-routing-rules` | `MerchantMultiAccountRoutingRules.js` | `:814` |

### 1d. Ledger tabs (`js/admin/ledger/NavBar.js:17-29`, link keys `js/admin/ledger/data.js:21-58`)

`/ledger/entities/journal`, `/ledger/entities/ledger_entries`, `/ledger/accounts/account_details`, `/ledger/accounts/account`, `/ledger/ledger_config/default`, `/ledger/journal_rejected_events/default`, `/ledger/actions/default`; detail route `/ledger/:type/:tab/:detailed_pages` (`Content.js:751`). Tenant selector options **X** and **PG** (`data.js:230-241`), default `PG` (`data.js:9`, `common.js:158-164`), sent as `Ledger-Tenant` header (`common.js:135-139`). e2e coverage: `e2e/Ledger/ledger.spec.js:11,20,29`.

### 1e. Merchant-entity "RazorpayX" group (`js/admin/merchants/entity/MerchantEntity.js:1962-2009`, gated `view_razorpayx_details`)

| Item | Permission | Route / modal | Evidence |
|---|---|---|---|
| Payout Link Settings | `edit_merchant` | modal `EditPayoutLinkModes` | `MerchantEntity.js:1966-1968`; `entityModals/EditPayoutLinkModes.js:37,133` |
| Rx Notification Configs | `edit_merchant` | modal `RxNotificationConfig` → `js/admin/rxNotificationConfig/*` | `:1973-1975`; `entityModals/RxNotificationConfig.js:3-6` |
| Low Balance Configs | `update_low_balance_config_admin` | `/merchants/:id/update_low_balance_config_admin` | `:1980-1981`; `Content.js:491`; `entity/LowBalanceConfigs/{list,entity}.js` |
| Sub Customer Identifier Configuration | `admin_sub_virtual_account` | `/merchants/:id/sub_customer_identifier_configuration` | `:1985-1986`; `Content.js:483-486`; `entity/SubVAConfig/*` |
| View Bank Account Balances | `view_account_balances` | modal `BankingBalances` ("RazorpayX Bank Account Balances") | `:1990-1991`; `entityModals/BankingBalances.js:44,58` |
| Update Free Payouts | `edit_free_payouts` | modal `UpdateFreePayout` | `:1996-1997`; `entityModals/UpdateFreePayout.js:62,121` |
| Source Fund Accounts | `edit_merchant` | `/merchants/:id/source-accounts` → `js/admin/SourceFundAccounts/` | `:2003-2004`; `Content.js:481` |
| Enable/Disable Payout (merchant action) | (risk action block) | `merchants/{id}/action` with `enable_payout` / `disable_payout` | `MerchantEntity.js:1755,1813`, action URL `:613` |
| Merchant Analytics Stats (X account_number when `business_banking`) | `view_merchant_analytics` | `/merchants/:id/stats` | `MerchantEntity.js:1105-1113`; `entity/MerchantAnalyticStats.js:44` |
| Merchant details fields "RazorpayX" (= `business_banking`), "Banking Balance" (`merchant_details.banking_account.balance`) | — | detail panel | `entity/entity-resources.js:1087-1105`; `MerchantEntity.js:490-491` |

### 1f. `/actions` modals that are RazorpayX / banking specific (`js/admin/adminActions/actionModals/`)

| Modal title | Permission | Backend path | File:line |
|---|---|---|---|
| Clear Pending Payouts | `reject_payout_bulk` | `live/admin/payouts/cancel` | `ClearPendingPayouts.js` |
| Reject Payout | `reject_payout` | `live/admin/payouts/{id}/reject` | `RejectPayout.js` |
| Retry Payouts | `retry_settlement` | `{mode}/payouts/retry` | `RetryPayout.js` |
| Force Update Payout Status (+Bulk) | `payout_status_update_manually` | `live/payouts/{id}/manual/status`, `live/payouts/manual/status_update/batch` | `ForceUpdatePayoutStatus.js`, `ForceUpdatePayoutStatusBulk.js` |
| Free Payout Migration / Add Free Payout Count | — | `live/admin/payouts/free_payout_migration`; `{mode}/balance/{balanceId}/free_payout` | `FreePayoutMigration.js`, `AddFreePayoutCount.js` |
| Rx Merchant SLA Configuration | `view_merchant` | `live/payouts/merchant_on_hold_slas` | `RxMerchantSLAConfiguration.js` |
| Rx Downtime Notifications | `manage_fund_loading_downtime` | `live/fund_loading/downtimes`, `live/fund_loading/downtime/notification/{creation,updation,resolution,cancellation}`, `live/growth/assets/enable_downtime_for_x` | `RxDowntimeNotification.js:2-6`; `js/admin/rxDowtimeNotifications/DowntimeNotificationList.js:37`, `CreateDowntimeNotification/CreateDowntimeNotification.js:73,122`, `UpdateDowntimeNotifications.js:58`, `ResolveDowntimeNotifications.js:48`, `CancelDowntimeNotifications.js:20` |
| Create / Close Customer Identifiers (X VAs) | `create_banking_virtual_accounts` | `live/virtual_accounts/banking/bulk`, `live/virtual_accounts/banking/close/bulk` | `CreateVirtualBankingAccount.js`, `CloseVirtualBankingAccount.js` |
| Process CA Fee Recovery; Create Fee Recovery / Update Schedule | `process_fee_recovery` | `live/payouts/fee_recovery`; `live/admin/fee_recovery_payout`, `.../custom_amount`, `live/admin/fee_recovery_amount`, `live/admin/fee_recovery_schedule_update` | `ProcessFeeRecovery.js`; `FeeRecovery/*` |
| Update Balance Management Config | `edit_balance_management_config` | `live/fund-management-payout/balance-config/merchants/{mid}` | `UpdateBalanceManagementConfig.js` |
| Rbl Account Statement Manual Linking | `manually_link_rbl_account_statement` | `live/banking_account_statement/source/update{,/validate}` | `RblAccountStatementManualLinking.js` |
| Tax Payments Admin / X Invoice Admin / Payout Links Admin / GAI Admin | `tax_payment_admin_auth_execute` (all four) | `/admin/api/live/tax-payments/admin`; `/admin/api/live/invoice/admin`; `live/payout-links/admin`; `live/accounting-integrations/admin/admin-actions` | `TaxPaymentAdminApi.js:50,73`; `XInvoiceAdmin.js:25,53`; `PayoutLinkAdminApi.js:25,48`; `GaiAdminApi.js:25,48` |
| Migrate Merchant Team ("XPS") / Create / Update Cost Center | `self_serve_workflow_config` | `live/abacus/admin/bulk-users`; `live/xperience-edge/admin/cost-centers[/{id}]` | `MigrateUsersToXpsAction.tsx`; `CostCenterCreateAction.js`; `CostCenterEditAction.js` |
| RazorpayX Core Banking FAQs | — | (static) | `RazorpayXCoreBankingFAQ.js:6` |
| Call Ledger Service Actions | `ledger_service_actions` | `/makeapicall/{url}` | `CallLedgerServiceActions.js:119,143` |
| Onboard/Offboard PG merchant on Ledger, Sync balances | `pg_ledger_actions` | `{mode}/pg_ledger/merchant/{onboard,offboard,sync_balances}` | `PGLedgerOnboard.js`, `PGLedgerOffboard.js`, `SyncMerchantBalanceOnPGLedger.js` |
| Capital Cards Repayment Recon | `add_merchant_adjustment` | `live/capital_cards/admin/v1/repayment-recon` | `CapitalCardRepaymentRecon.js` |
| PG-settlement ops that touch the same rails (INF not X): Fund Transfer Initiate/Update (`{mode}/fund_transfer_attempts/...`, perm `settlement_bulk_update`), Nodal Beneficiary Update / Nodal→Nodal transfer (`live/nodal_beneficiaries`, `live/nodal/transfer/admin`, perm `create_nodal_account_transfer`), Beam nodal action (`{mode}/nodal_file_upload/retry`), Process Pending Bank Transfer (`live/admin/process_pending_bank_transfer`, perm `admin_process_pending_bank_transfer`), Insert Bank Transfer (`live/bank_transfers/{...}`) | see cells | see cells | `FundTransferInitiate.js`, `FundTransferUpdate.js`, `NodalBeneficiaryUpdate.js`, `NodalMoneyTransfer.js`, `BeamNodalAction.js`, `ProcessPendingBankTransfer.js`, `InsertBankTransfer.js:25` |

### 1g. Other X touchpoints

| Where | What | Evidence |
|---|---|---|
| Offers → Fund Loading | `/offers/fund-loading`; request type "Fund Loading from CA to Escrow"; perms `offer_fund_loading_finance`, `offer_fund_loading_biz`; API `live/admin/offers/presigned-url`, `live/admin/offers/fund-loading` | `Content.js:970`; `js/admin/FundLoading/index.tsx:82-91`; `FundLoading/utils/api-service.ts:66,163,211` |
| Reports config product list includes `contacts`, `payouts`, `fund_accounts`, `vendor_payments`, `payout_links`, `tax_payments`, `vendor_payments_v2` ("RX Invoices"), `journal` ("Ledger") | `js/admin/reports/components/ReportModal/CreateConfigModal/data/index.ts:101-133`; product option `razorpayx` in `js/admin/merchants/entity/MerchantReportConfig/List.js:248`, `Create/DataDetails.js:195` |
| Pricing plan model: product `banking`; features `payout`, `fund_account_validation`, `fund_transfer` ("Payout: Fund Transfer"), `rzpx_postpaid`; `payouts_filter` {`free_payout`,`rzp_charge_collections`,`rzp_fees`}; `app_name: xpayroll` | `js/admin/plans/plan.js:127,134,138,171,242,326-335,963,1055-1057` |
| Merchant list/edit: business type `business_banking`; product `rzpx_postpaid`; risk tags `X_Risk_PayoutDisable`, `X_Risk_CI_Blocked` | `js/admin/merchants/constants.js:291,263`; `js/admin/merchants/utils.js:1119` |
| Merchant analytics ES index `payouts` filtered by `balance_account_number` | `js/admin/merchants/entity/data.js:28-36,131-145` |
| Cases/activation: purpose codes for `tax_payments` sub-category | `js/admin/cases/containers/caseChecklistV2/businesspurposecodemapping.js:139,834`; `js/admin/banking_accounts/mob/data/subCategoryMapping.js:62` |
| SAV (support agent view): "How to handle queries for RazorpayX products?" → `business_banking`; Freshdesk groups "Banking Programs- Onboarding/Service", "BankingOps_*" | `js/admin/SAV/Account/constants.ts:387-394`; `js/admin/SAV/LeftNavigation/constants.ts:2-78` |
| Generic entity browser `/entities/:mode?/:selectedEntity?` lists whatever `{mode}/admin/entities/all` returns; entity detail `/admin/api/{mode}/admin/{type}/{id}` | `Content.js:603`; `js/admin/entities/List.js:247-249,218,267-272`; `js/admin/entities/Entity.js:63-69`. Whether X entities (payout/fund_account/contact) are listable is **UNK** (server-driven; no client list) |
| Dev/QA host map pairs each admin-dashboard env with an X (`x.razorpay.com`, `x.np.razorpay.in`, `x-func*.np.razorpay.in`, `xapps-func.np.razorpay.in`) env | `js/admin/merchants/constants.js:444-499` |

## 2. Backend endpoints per section (all relative to `/admin/api/` unless noted)

### 2a. Current Accounts / CA onboarding (`js/admin/banking_accounts/`)

| Path family | Examples (file:line) |
|---|---|
| `live/bas/lms/...` (BAS = banking-accounts lead-management) | `bas/lms/admin/leads/search?application_type=ICICI_ONBOARDING_APPLICATION` `IciciCa/IciciCaList.js:67`; `...=IDFC_ONBOARDING_APPLICATION` `IdfcCa/IdfcCaList.js:78`; `...=ICICI_ACCOUNT_LINK_APPLICATION` `IciciCa/AccountLinking/IciciAccountLinking.js:61`; `...=ICICI_VIDEO_KYC_APPLICATION` `IciciCa/IciciVideoKyc/IciciVideoKyc.js:61`; `bas/lms/admin/apply` `AcquistionActions/actions/CreateNewCAApplication.js:30`; `bas/lms/allocate_lead` `AllocatePartnerBank/Form.js:58`; `bas/lms/is_serviceable` `IciciCa/utils.js:18`; `bas/lms/business/{bid}/applications/{aid}` `IciciCa/ApplicationLeadDetails.js:62`; `bas/lms/admin/business/{bid}/application/{aid}/update_status` `IciciCa/AdminApplicationStatusUpdate.js:12`; `bas/lms/admin/application_event` `IciciCa/ApplicationStatusUpdateModal.js:29`; `bas/lms/admin/business/{bid}/banking_account/{baId}/{update_details,tokenize_and_update_details}` `AcquistionActions/actions/SubmitDetailAndActivateAccount.js:24-56`; `.../ybl/activate_upi`, `.../idfc/activate_upi` `AcquistionActions/actions/UPIActivation.js:119-122`; `bas/lms/admin/transfer_onboarding_application` `AcquistionActions/actions/TransferCA.js:25`; `bas/lms/admin/account_managers`, `.../banking_account/{id}/account_manager[/unlink]` `IciciCa/IciciCaList.js:90`, `IciciCa/AccountManagerLinkModal.js:14`, `IciciCa/AccountManagerUnlinkBtn.js:7`; `bas/lms/admin/ca-applications` `MultiCa/MultiCa.js:53`; `bas/lms/rbl/credentials`, `bas/lms/rbl/get_similar_businesses` `CurrentAccountApiOnboarding/CurrentAccountApiOnboarding.js:46,73`, `.../SimilarMerchants.js:21`; `bas/lms/idfc/banking_account/{id}/{credentials,application_form,kyc_documents}` `IdfcCa/IdfcCaDocuments.js:29-84`; `bas/lms_ops/business/{bid}/composite-applications/{aid}` `IdfcCa/IdfcActivationModal.js:104`; `bas/lms/business/{bid}/applications/{aid}/{submit_credentials,retrigger_registration}` `IciciCa/AliasID.js:46,60` |
| `live/mob/...` (merchant onboarding workflow) | `mob/lms/intents`, `mob/lms/save_workflow` `IciciCa/api.js:5`, `IciciCa/InitiateCaModal.js:32,52`; `mob/admin/intents` vs `mob/admin_oneca/intents`, `mob/admin[_oneca]/save_workflow`, `mob/admin[_oneca]/get_workflow/{id}` `mob/api.js:5,21-23,40` |
| Monolith banking-account routes | `live/admin_lms/banking_accounts[?expand[]=...]` `List.js:98`, `CurrentAccountDetails.js:65,117`; `live/admin_lms/banking_accounts/bacc_{id}` `AcquistionActions/actions/DownloadAccountStatement.js:204`; `live/banking_accounts/{id}`, `.../activate`, `.../reviewers` `Entity.js:171,211,265`; `live/banking_accounts/activation/{spocs,ops_mx_pocs,{id}/comments,{id}/call_logs,{id}/status_change_log,{id}/details,mis/download,bacc_entity_proof_documents/{mid}}` `CreateAccount.js:69`, `CurrentAccountDetails.js:164-186`, `ActivationHistory.js:16`, `CompleteCaForm.js:88-100`, `List.js:464`, `IdfcCa/InitiateIdfcCaModal.js:149`; `live/banking_account/{application_id}` `IdfcCa/IdfcEntity.js:282`; `live/admin/banking_account?expand[]=...` `TeleVerification/RblTeleVerification.js:90,101`, `?account_type=current&merchant_id=` `merchants/entity/MerchantAnalyticStats.js:44`; `live/banking_accounts/customer_appointment_dates/{id}` `RBL/LeadDetails/hooks/useCustomerAppointmentDate.js:18` |
| Account statements | `live/banking_account_statement/generate-admin` `AcquistionActions/actions/DownloadAccountStatement.js:69`; `live/banking_account_statement/source/update[/validate]` `adminActions/actionModals/RblAccountStatementManualLinking.js` |
| Merchant preferences (X CA intent) | `live/admin/merchant/preferences/{mid}/x_merchant_current_accounts[/{type}]`, `live/admin/merchant/preferences/x_merchant_intent` `AllocatePartnerBank/helpers.js:17,54,100,121,172,191`; `CurrentAccountDetails.js:39`; `IdfcCa/constants.js:155` |
| Supporting | `live/cities`, `live/merchant/activation/reviewers`, `live/merchants/details`, `live_{mid}/merchants-users`, `live/admin-ufh/file/upload` `CompleteCaForm.js:63`, `CurrentAccountDetails.js:150`, `EntityNameMatchStatus/helpers.js:39,49`, `CreateAccount.js:120`; CDN `https://cdn.razorpay.com/static/assets/icici-ca/cities.json` `IciciCa/IciciCaList.js:99` |

### 2b. Payouts (`js/admin/payouts/`, merchant modals, adminActions)

| Path | Evidence |
|---|---|
| `live/payouts/manual_action` with `action` ∈ {`processed_to_processing`, `dual_write`, `approve_workflow_payouts`, `reject_workflow_payouts`, `process_bank_transfer`, `manual_smart_collect_entity_creation`, `redis_get`, `redis_set`, `onboard_collectx_merchant`, `onboard_slice_va`}; payload flag `is_payout_service` | `PayoutsManualActions.js:153`; `payouts/constants.js:2-17,45-49` |
| `live/payouts/downtimes`, `live/payouts/downtime/{id}` | `js/admin/xGatewaydowntimes/List.js:17`, `Entity.js:90` |
| `live/payouts/smart_routing_rules` | `js/admin/fts/components/CreateMultiAccountRoutingRule.js:28` |
| `live/admin/payouts/{balanceId}/free_payout`, `{mode}/balance/{balanceId}/free_payout` | `merchants/entity/entityModals/UpdateFreePayout.js:62,121` |
| `live/admin/balance?merchant_id=` | `merchants/entity/entityModals/BankingBalances.js:44` |
| `{mode}/low_balance_configs/admin/{mid}`, `{mode}/low_balance_configs/{id}/{status}/admin`, `live/low_balance_configs[/{id}]/admin` | `merchants/entity/LowBalanceConfigs/list.js:39,53`, `entity.js:25-28` |
| `live/admin/sub_virtual_accounts[/merchant/{mid}|/{id}]` | `merchants/entity/SubVAConfig/entity.js:12`, `list.js:19,34` |
| `live/payout-links/{mid}/settings`, `live/payout-links/admin` | `merchants/entity/entityModals/EditPayoutLinkModes.js:37,133`; `adminActions/actionModals/PayoutLinkAdminApi.js:25` |
| `live/fund_accounts/validations/admin`, `live/admin/merchant/{mid}/tpvs`, `live/admin/tpv/create`, `live/admin/tpv/{id}` | `js/admin/SourceFundAccounts/Form.js:12,44`, `List.js:16,24` |
| `/makeapicall/admin/merchants/{mid}/merchant_notification_configs[/{id}[/{action}]]` | `js/admin/rxNotificationConfig/services.js:29-78` |
| `live/merchants/{mid}/action` (`enable_payout`, `disable_payout`) | `merchants/entity/MerchantEntity.js:613,1755,1813` |
| See §1f for cancel/reject/retry/manual-status/fee-recovery/SLA/balance-config/VA paths | — |

### 2c. FTS (`js/admin/fts/`)

| Path family | Evidence |
|---|---|
| Read (monolith admin entity fetch): `live/admin/fts.{source_account_mappings, direct_account_routing_rules, preferred_routing_weights, account_type_mappings, merchant_configurations, transfers, schedules, channel_information_status_logs, trigger_status_logs, fail_fast_status_logs, key_value_store_logs}` | `RoutingRules.js:63`, `MerchantRules.js:111`, `DirectRoutingRules.js:77`, `RoutingWeights.js:55`, `AccountTypeMappings.js:56`, `MerchantConfigurations.js:54`, `FailQueuedTransfer.js:53`, `Schedules.js:66`, `ChannelInformationStatusLogs.js:65`, `TriggerStatusLogs.js:66`, `ChannelFailFastStatusLogs.js:64`, `KeyValueStoreLogs.js:53` |
| Write/ops: `live/fts/dashboard/{source_account_mappings, direct_account_routing_rules, preferred_routing_weights, account_type_mappings, merchant_configurations, schedules, key_value_store, fail_queued_transfer[/bulk], manual_override, manual_queries, fail_fast_status/manual_update, test_transaction/status, cron_jobs_dashboard, new_channel_health_stats, trigger_health_status, create_custom_transfer, fund_transfer/retry, fund_transfer_update, fund_transfer_status/{bulk,bulk_get,check,pending,raw}, account/fetch_balance, source_account/{create,copy,update,delete,graceful_update}}` | `data.js:250-343`; `components/*.js`; `actionModals/*.js` (e.g. `ForceRetryTransfer.js:44`, `FetchAccountBalance.js:22`, `AirwallexWalletTransfer.js:44`, `RblUpiCredsUpdate.js:20`, `AddSourceAccount.js:87`) |
| `live/mozart/gateway/action` (Gateway PVT) | `actionModals/GatewayPVT.js:71` |

### 2d. Ledger (`js/admin/ledger/`) — all POST to `{mode}/ledger_service/<op>` with `Ledger-Tenant` header

Ops observed: `fetch`, `fetch_multiple`, `fetch_filter`, `fetch_account_types`, `fetch_journal_form_field_options`, `fetch_account_form_field_options`, `fetch_ledger_config_form_field_options`, `create_journal`, `create_account`, `create_accounts_in_bulk`, `update_account`, `update_account_detail`, `activate_account`, `create_ledger_config`, `update_ledger_config`, `delete_ledger_config`, `create_accounts_on_event`, `delete_merchants`, `replay_journal_rejected_events` (`data.js:1,248-263`; `common.js:108`; `Entities/Journal/CreateJournal.js:48`; `Accounts/Account/Account.js:139,151`; `LedgerConfig/Actions/TransactionConfig.js:203,227`; `LedgerConfig/index.js:105`; `Actions/actionModals/OnboardMerchant.js:59`, `OffboardMerchant.js:51`; `JournalRejected/utils.js:55`).

### 2e. Corporate Cards / Capital (`js/admin/corporateCards/`, `js/admin/capital/`, `loans/`, `cashadvance/`, `collections/`)

| Path family | Evidence |
|---|---|
| `live/capital_cards/admin/v1/{cardholder/role, cardholder/filtered, addonapplications[/get], program/kyc, card/request-physical-card, programs/limit, limit/recommendation/{active,{id}/apply}, reward[/{id}], sendTrackingDetails, getTrackingDetails, customer/registration/prefilled-payload, repayment-recon}` | `corporateCards/constants.js:251,273`; `FounderTable.js:19`; `Founders.js:24`; `AddonCards.js:55,141`; `M2PActions.js:140,166`; `CardLimits/SpendLimit.js:58`, `CreditLimit.js:72,111`; `RewardsConfig/index.js:69-135`; `PhysicalCards.js:96,123`; `M2PRegistration/index.js:29`; `adminActions/actionModals/CapitalCardRepaymentRecon.js` |
| `live/los/admin/twirp/rzp.capital.los.{m2pdirect.v1.M2PDirectApi/{SendToM2PViaMozart,UpdateKycDocumentsDirect}, admin.v1.ProductAPI/GetProducts, origination.v1.ApplicationAPI/{ListOrSearch,GetScorecardUfhSignedUrl}, admin.v1.D2CBureauInviteAPI/{ListInvites,CreateInvite}}` | `corporateCards/M2PActions.js:64,104`; `ApplicationList.js:51,93`; `capital/CapitalRisk/CLI_CLD/helpers.js:36`; `capital/DiretcBureau.js:23,63,85` |
| `live/loc/admin/twirp/rzp.capital.loc.withdrawal.v1.*`, `live/lender/admin/{repayment/details,loan/limit}`, `live/capital_collections/admin/v1/{report_types,reports,reversals,repayments}`, `live/scorecard/admin/{matchv2,lms/entities/{filter,bulk}}`, `live/offline_verification/service/v1/{verification,order,seed_data}` | `cashadvance/*`, `loans/*` (grep summary); `capital/Reports/index.js:14,23`; `capital/CapitalRisk/Dedupe/constants.js:12`, `DedupeList/helpers.js:6-10`; `capital/ValidationList.js:54-76`; `adminActions/actionModals/RepaymentReversal.js`, `CapitalLOCManualAdjustment.js` |
| `live/admin-ufh/file/upload`, `live/admin-ufh/{mid}/files/{fileId}/get-signed-url` (UFH file service) | `corporateCards/Download.js:10,18`; `M2PActions.js:31` |

### 2f. Team management / merchant workflows (`js/admin/s2p-setup/`, `js/admin/merchant-workflows/`, `js/admin/workflows/`)

| Path | Evidence |
|---|---|
| `live/abacus/admin/{groups[/{id}], group-types, users[/{id}], users/invitations[/{id}[/resend]], bulk-users}` | `s2p-setup/network/constants.ts:3-5`; `GroupsSetup/EditGroupModal.tsx:79`; `TeamMembersSetup/AddEditTeamMemberModal.tsx:68,213,218`; `adminActions/actionModals/MigrateUsersToXpsAction.tsx` |
| `live/cac/admin/role_map` | `s2p-setup/TeamMembersSetup/AddEditTeamMemberModal.tsx:107`; `merchant-workflows/Modal.js:37`, `Entity.js:73`, `Edit.js:53` |
| `live/admin/workflow/config[/list]`, `live/features/{mid}` | `merchant-workflows/Entity.js:178,321`, `Edit.js:235`, `List.js:51,146` |
| `live/xperience-edge/admin/cost-centers[/{id}]` | `merchant-workflows/Entity.js:273`; `adminActions/actionModals/CostCenter{Create,Edit}Action.js` |
| `live/admin/batches` (bulk CSV invites/groups) | `s2p-setup/GroupsSetup/CreateGroupsModal.tsx:122`; `TeamMembersSetup/AddEditTeamMemberModal.tsx:263` |
| Admin maker-checker: `live/workflows[/{id}[/observer_data]]`, `live/roles`, `live/permissions-multiple` | `workflows/List.js:16`, `Entity.js:34-36,45`, `service.js:5` |
| CDN templates under `static/assets/razorpayx/teams-and-departments/` | `s2p-setup/constants.ts:88-90` |

### 2g. Misc X-adjacent

`live/admin/offers/{presigned-url,fund-loading}` (`FundLoading/utils/api-service.ts:66,163,211`); `live/fund_loading/downtime*`, `live/growth/assets/enable_downtime_for_x` (§1f); `/admin/api/live/tax-payments/admin`, `/admin/api/live/invoice/admin`, `live/accounting-integrations/admin/admin-actions` (§1f); `/admin/api/live/admin/files/{type}` bank file upload (`js/admin/banks/file-upload.js:21`); `{mode}/commissions/partner/{mid}/...` partner commission payouts (`merchants/entity/entityModals/CommissionPayout.js:54,67`, perm `commission_payout` `MerchantEntity.js:2317`) — INF PG partner commissions, not X.

## 3. Admin permission / role names gating these sections (OBS unless noted)

| Permission | Gates | Evidence |
|---|---|---|
| `view_activation_form` | Current Accounts menu | `Content.js:263` |
| `banking_update_account` | CA edit/activate actions | `js/admin/banking_accounts/*` (2 uses; grep `permission="banking_update_account"`) |
| `view_razorpayx_details` | Merchant "RazorpayX" group | `MerchantEntity.js:1962` |
| `update_low_balance_config_admin`, `admin_sub_virtual_account`, `view_account_balances`, `edit_free_payouts`, `edit_merchant` | items in that group | `MerchantEntity.js:1980-2003` |
| `payout_manual_action` (menu) vs `payouts_manual_action` (component static) | Payouts Dashboard — **mismatch OBS**; which one the backend actually issues is UNK | `Content.js:326`; `payouts/PayoutsManualActions.js:83` |
| `reject_payout`, `reject_payout_bulk`, `retry_settlement`, `payout_status_update_manually`, `process_fee_recovery`, `edit_balance_management_config`, `manage_fund_loading_downtime`, `create_banking_virtual_accounts`, `close_va`, `debug_virtual_account`, `manually_link_rbl_account_statement`, `admin_process_pending_bank_transfer`, `tax_payment_admin_auth_execute`, `make_admin_api_call`, `ledger_service_actions`, `pg_ledger_actions`, `settlement_bulk_update`, `create_nodal_account_transfer`, `commission_payout` | `/actions` modals (§1f) | files in §1f |
| `view_fts_dashboard`, `fts_routing_rules_update` (20 uses), `fts_force_retry_transfer`, `fts_fail_queued_transfer`, `fts_transfer_attempt_bulk_update`, `fts_source_account_update`, `fts_source_account_graceful_update`, `bulk_fts_routing_rules_csv`, `bulk_fts_routing_weights_csv`, `bulk_fts_account_type_mappings_csv` | FTS menu + modals | `Content.js:327`; `js/admin/fts/**` (grep) |
| `ledger_view_dashboard`, `ledger_hide_dashboard` | Ledger menu / hide | `Content.js:325`; `ledger/Actions/actionModals/{BulkJournalCreate,OnboardMerchant,OffboardMerchant}.js` |
| `view_gateway_downtime` | RX Downtimes | `Content.js:303` |
| `los_cards`, `capital_los_create_application`, `loans_edit`, `loc`, `capital_send_payment_link`, `offline_verification_service_view_deprecated`, `add_merchant_adjustment`, `capital_developer` | Capital/Cards | `Content.js:271-285,338-341` |
| `self_serve_workflow_config` | Team mgmt / merchant workflows / cost centres | `Content.js:356,374` |
| `view_all_workflow`, `view_workflow_requests` | admin Workflows/Requests | `Content.js:373,376` |
| `admin_bank_file_upload`, `banking_vas_brd_parser_access` | "banking dashboard" group | `Content.js:400-401` |
| `offer_fund_loading_finance`, `offer_fund_loading_biz` | Fund Loading request types | `FundLoading/index.tsx:84,91` |
| `view_pricing_list` | Pricing plans (incl. banking product) | `Content.js:291` |
| Admin roles: `external_admin_customer_success` (switches entity API prefix to `external_admin`) | `js/admin/entities/List.js:267-272` |
| Merchant-side role labels for RBL bank users: `rbl_supervisor`, `rbl_agent` | `js/rzp/utils/constants.js:73-82` |
| Permission catalogue fetched from `live/permissions-multiple`, roles from `live/roles` | `js/admin/permissions/List.js:20`; `js/admin/roles/List.js:21` |

## 4. Backend services implied

| Path prefix (under `/admin/api/{mode}/`) | Implied service | Confidence | Basis |
|---|---|---|---|
| `admin/*`, `merchants/*`, `banking_accounts/*`, `admin_lms/*`, `payouts/*`, `admin/payouts/*`, `balance/*`, `low_balance_configs/*`, `admin/sub_virtual_accounts`, `virtual_accounts/banking/*`, `fund_accounts/*`, `admin/tpv/*`, `workflows`, `roles`, `permissions-multiple`, `settlements/*`, `nodal*`, `fund_transfer_attempts/*`, `bank_transfers/*`, `admin/fts.*` | API monolith (`api`) admin routes | High (INF) | uniform `/admin/api/{mode}` prefix; "API monolith" comment `entities/List.js:251`; `is_payout_service` dual-write flag implies payouts live partly in monolith (`payouts/constants.js:4,45-49`) |
| `fts/dashboard/*`, `mozart/gateway/action` | FTS (fund transfer service) + Mozart gateway adapter | High | §2c |
| `ledger_service/*` (+ tenants X/PG) | Ledger service (multi-tenant) | High | `ledger/data.js:1,230-241` |
| `bas/lms/*`, `bas/lms_ops/*` | Banking-accounts service ("BAS") lead-management | High (name expansion INF) | §2a; folder `banking_accounts` |
| `mob/lms/*`, `mob/admin[_oneca]/*` | Merchant-onboarding (MOB) workflow service; "oneca"/"maverick" flow variant | Medium | `banking_accounts/mob/api.js:5,21,40`; `mob/constants.js:1-4` |
| `banking_account_statement/*` | Bank statement ingestion (monolith or service) | Medium | §2a |
| `payout-links/*` | Payout Links service | Medium | `EditPayoutLinkModes.js:37`; `PayoutLinkAdminApi.js:25` |
| `tax-payments/admin`, `invoice/admin`, `accounting-integrations/admin` | X Tax Payments, X Vendor Payments/Invoices, Accounting Integrations (GAI) | Medium | §1f |
| `fund-management-payout/*` | Fund-management (balance/limits) payout service | Medium | `UpdateBalanceManagementConfig.js` |
| `fund_loading/*`, `growth/assets/*` | Fund-loading downtime config (X) + Growth service | Medium | `rxDowtimeNotifications/*` |
| `abacus/admin/*`, `cac/admin/*`, `xperience-edge/admin/*` | Abacus (teams/groups/users), CAC (role map), Xperience-Edge (X BFF; cost centres) | Medium (roles INF) | §2f |
| `capital_cards/admin/v1/*`, `los/admin/twirp/rzp.capital.los.*`, `loc/admin/twirp/rzp.capital.loc.*`, `lender/admin/*`, `capital_collections/admin/v1/*`, `scorecard/admin/*`, `offline_verification/service/v1/*` | Capital: cards, LOS, LOC, lender, collections, scorecard, offline-verification; M2P card issuer via Mozart | High | §2e |
| `admin-ufh/*`, `ufh/file/*` | UFH (unified file handler) | High | §2e |
| `care/twirp/rzp.care.sav.v1.SavOmniService` | Care / SAV-Omni | High | `SAV/network/baseQueryHandler.ts:17-28` |
| `admin/offers/*` | Offers service (fund-loading offers) | Medium | §1g |
| `/makeapicall/*` | Dashboard-backend raw relay to admin API | High | `MakeAdminAPICall.js:104-119` |

## 5. Signal strength, probable repos, unknowns

### 5a. Domain signal strength (file counts from `rg -il` over `js/`, excluding tests/stories)

| Domain | Strength | Files / hits | Primary evidence |
|---|---|---|---|
| Current-account onboarding (RBL/ICICI/IDFC, BAS/MOB) | **Very strong** | `icici` 125/908, `banking_account` 58/196, `rbl` 88/186, `current_account` 12/36, `bas` 41/75 | `js/admin/banking_accounts/` (~80 files) |
| FTS routing/ops | **Very strong** | `fts` 71/210 | `js/admin/fts/` (~60 files) |
| Ledger (X/PG tenants) | **Strong** | `ledger` 66/546 | `js/admin/ledger/` (~37 files), e2e spec |
| Payouts ops (manual actions, free payouts, SLAs, status fixes, VAs) | **Strong** | `payout` 85/432, `fund_account` 33/84 | `payouts/`, `xGatewaydowntimes/`, `rxDowtimeNotifications/`, `rxNotificationConfig/`, merchant modals, ~15 `/actions` modals |
| Capital / corporate cards (M2P) | **Strong** | `capital` 116/349, `corporate card` 17/39, `m2p` 8/17 | `corporateCards/`, `capital/`, `loans/`, `cashadvance/`, `collections/` |
| Team mgmt / workflows / approvals (abacus, cac, xperience-edge) | **Medium** | `workflow` 363/3122 (mostly PG onboarding/admin), `approval` 83/236 | `s2p-setup/`, `merchant-workflows/`, `workflows/` |
| Pricing/fees for banking product | **Medium** | `pricing` 213/1682 (mostly PG) | `plans/plan.js` banking/payout entries |
| Tax payments / vendor payments / payout links / GAI | **Weak-medium** | `tax_payment` 10/13, `vendor_payment` 2/7, `payroll` 7/14 | 4 `/actions` modals + report types + pricing `xpayroll` |
| Reconciliation / statements | **Weak (X)** | `reconcil` 49/289 (PG ART), `statement` 28/79 | recon modules are PG; X statement only via `banking_account_statement/*` |
| `opfin`, `x-payroll` (as repo names) | **None** | 0 hits | — |

### 5b. Probable repo / service names NOT in the clone (INF unless noted)

| Name | Where the name came from |
|---|---|
| `api` (Razorpay API monolith; admin routes `/admin/...`) | "API monolith" comment `js/admin/entities/List.js:251`; every `/admin/api/{mode}/...` call |
| `dashboard` (Laravel backend hosting `/admin`, `/admin/api`, `/makeapicall`, `/org`, `/admin/user/logout`) | `AGENTS.md:3` ("served via the main Dashboard backend"); `js/admin/App.js:100,135`; `MakeAdminAPICall.js:119` |
| `fts` (fund transfer service) | `fts/dashboard/*` paths; folder `js/admin/fts/` |
| `ledger` / `ledger-service` (tenants X, PG) | `LEDGER_PROXY = 'ledger_service'` `ledger/data.js:1` |
| `bas` / banking-accounts-service (+ LMS, `lms_ops`) | `bas/lms/*` paths |
| `mob` / merchant-onboarding (with `admin_oneca` flow) | `mob/*` paths; `mob/constants.js:1-4` |
| `payouts` service (dual-written with monolith) | `is_payout_service`, `dual_write` `payouts/constants.js:4,45-49` |
| `payout-links` | `payout-links/*` |
| `tax-payments`, `invoice` (X vendor payments), `accounting-integrations` (GAI) | `TaxPaymentAdminApi.js:50`, `XInvoiceAdmin.js:25`, `GaiAdminApi.js:25` |
| `fund-management-payout` | `UpdateBalanceManagementConfig.js` |
| `abacus`, `cac`, `xperience-edge` | §2f paths |
| `growth` (assets), `offers` | `enable_downtime_for_x`, `admin/offers/*` |
| Capital: `capital-cards`, `los`, `loc`, `lender`, `capital-collections`, `scorecard`, `offline-verification`; M2P (external issuer) via `mozart` | §2e twirp package names `rzp.capital.los.*`, `rzp.capital.loc.*` |
| `mozart` (bank gateway adapter) | `fts/actionModals/GatewayPVT.js:71`; `MozartErrorCode.js:68` |
| `ufh` (unified file handler) | `admin-ufh/*` |
| `care` / `sav-omni` | `SAV/network/baseQueryHandler.ts:17-28` (twirp `rzp.care.sav.v1.SavOmniService`) |
| `stork` (webhooks/SMS), `templating_service`, `scrooge` (refunds), `batch.service`, `splitz`, `dcs`, `relay` (config server) | menu permissions `Content.js:343-349`; `entities/List.js:252`; `Splitz/index.js` — non-X context |
| X frontend hosts `x.razorpay.com` / `x.np.razorpay.in` / `xapps-func.np.razorpay.in` | `js/admin/merchants/constants.js:444-499` |
| Org variants: `axis` (Axis Bank white-label admin `axis.razorpay.com`), `curlec`, `jk` (J&K bank) | `merchants/constants.js:448-450`; `Content.js:459` (`ORGS_FOR_RULE_ENGINE_VISIBILITY = ['rzp','axis']`); `user.js:58-59`; `Content.js:92` (`isJKOrg`) |

### 5c. Unknowns

| Item | Why unknown |
|---|---|
| Whether `/admin/api/{mode}/<prefix>/...` is forwarded by the dashboard backend straight to `api.razorpay.com/admin/...` and whether the monolith in turn reverse-proxies `bas/`, `ledger_service/`, `los/`, `care/` etc. to separate hosts | No proxy config in clone; only `config/devstack.json` (chart commit ids) and `local.config.js` dev host |
| Expansions of `s2p`, `cac`, `abacus`, `xps`, `oneca/maverick`, `lms_ops`, `Heimdall` | Only path strings/comments (`s2p-setup/data.js:28` "s2p_TODO"; `Content.js:438`) |
| Which of `payout_manual_action` / `payouts_manual_action` the backend grants | both appear (`Content.js:326` vs `PayoutsManualActions.js:83`) |
| Whether X core entities (payout, fund_account, contact, banking_account) are exposed in the generic `/entities` browser | list is server-driven (`entities/List.js:247`) |
| Prod URL for `mozart`, `m2p`, `ledger`, `fts` hosts; auth model of `/makeapicall` beyond `auth:'admin'` | not in clone |
| Any X-Payroll ("opfin") admin surface | 0 hits; only pricing `app_name: xpayroll` (`plans/plan.js:334`) and CA lead "Payroll" product options (`banking_accounts/CreateAccount.js:721`) |
| CODEOWNERS maps only `js/admin/banking_accounts` to three individuals (`CODEOWNERS:9`); no team handle for payouts/fts/ledger | file has 9 lines only |
