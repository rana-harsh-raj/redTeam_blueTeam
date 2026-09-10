# 05 — PHP monolith (`api`) business modules adjacent to RazorpayX Payouts

Source root: clone `api` (Laravel). All paths below are relative to `api/`. Routes live in a single 23k-line
array `app/Http/Route.php` (`protected static $apiRoutes`, entries `name => [method, path, Controller@action]`),
NOT in `routes/*.php` (that dir does not exist). Tables are mapped in `app/Constants/Table.php`.
Legend: **OBS** = observed in file, **INF** = inferred, **UNK** = unknown.

## 1) Summary

1. X in the monolith is a *product* (`Product::BANKING`, `app/Constants/Product.php:24`) layered on the PG data model: a merchant gets a `balance` row of `type=banking` (`app/Models/Merchant/Balance/Type.php:35`) and `merchant_users.product='banking'` role rows (`app/Models/Merchant/MerchantUser/Entity.php:25`).
2. Payouts are the *universal money-out primitive*: 14 `payout_sources.source_type` values (`app/Models/PayoutSource/Entity.php:23-39`) — payout_links, vendor_payments, tax_payments, vendor_settlements, vendor_advance, settlements, xpayroll, refund, ica_transfer, charge_collections, capital_collections, generic_accounting_integration, petty_cash — and each has a status "SourceUpdater" that calls the owning microservice back (`app/Models/Payout/SourceUpdater/Factory.php:14-90`).
3. The X ledger in the monolith is the shared `transactions` table (`Table.php:118`); `Transaction\Type::BANKING_TYPE` = payout, bank_transfer, reversal, adjustment, external, fund_account_validation, credit_transfer (`app/Models/Transaction/Type.php:59-67`); X's statement API is `Transaction\Statement` (routes `transactions_banking`, `Route.php:4007`).
4. Settlement is PG-only for X balances: `Balance\Type::$settleableBalanceTypes` excludes `banking` (`Balance/Type.php:64-70`); but X *is consumed by* settlements: on-demand settlement creates payouts through an internal X merchant via the public X API (`app/Services/RazorpayXClient.php:156,190`, config `applications.razorpayx_client.live.razorpayx_url`).
5. Pricing for payouts is the PG `pricing` table with `product='banking'`, `feature='payout'`, plus `account_type`, `channel`, `payouts_filter` columns; default banking plan `BTo98voDY05ueB` and fallback rule injection (`app/Models/Pricing/Fee.php:56,445-461,560-610`); Payouts Service pulls fees via `POST payouts_service/fetch_pricing_info` (`Route.php:4818`).
6. TaxPayments, VendorPayments, VendorPortal, AccountingPayouts all proxy to the **vendor-payments** service (`applications.vendor_payments.url` = env `VENDOR_PAYMENT_URL`), twirp namespace `razorpay.vendorpayments.taxpayments.Taxpayments` (`app/Services/TaxPayments/Service.php:14,88`); TDS entries are emitted on Kafka topic `add-tds-entry` (`app/Models/Payout/TdsProcessor/Constants.php:7`).
7. XPayroll = external "Opfin" service (`applications.xpayroll.baseUrl` = env `OPFIN_SERVICE_URL`, `config/applications.php:215-220`), authenticated as an internal app `app.xpayroll` (`config/applications_v2.php:741-761`) allowed to call `payout_create_internal` etc. (`Route.php:18626-18638`). Not an OAuth app (OBS: no hits in `app/Models/OAuthApplication`).
8. Corporate cards for X = Capital "corp_card" balance (`Balance\AccountType::CORP_CARD`, `Balance/AccountType.php:29`) onboarded via `POST merchant/onboardCCCForBanking` (`Route.php:5248`) with channel `m2p`; card account details fetched from **capital-cards** service (`app/Services/CapitalCardsClient.php:21,76`). Capital collections/LOC use payouts as source `capital_collections` and an X webhook proxy (`Route.php:1561`).
9. Identity: `users` / `merchants` / `merchant_users(merchant_id,user_id,role,product)` / `orgs` / custom roles in `access_control_roles` + `role_access_policy_map.authz_roles` (`Table.php:19,148,221,480-482`); X roles are `User\BankingRole` (owner/admin/finance_l1..l3/maker/checker_l1..l3/cc_admin/...) validated via `Roles\Service::getRoleUsingExperiment` (`app/Models/User/Role.php:218-231`).
10. ~90 external hosts configured in `config/applications.php` (list in §5); X-relevant: payouts, fts, ledger, x_balances, x_account_statements, xperience, banking_account_service, master_onboarding, vendor_payments, vendor_experience, accounting_integrations, payout_links, xpayroll(opfin), capital_cards, capital_collections, line_of_credit, settlements_service, charge_collections, workflows, authz, dcs, splitz, razorx, beam, wallet.

## 2) Module directory scan (OBS)

`ls app/Models` (144 dirs). X-adjacent flagged: Adjustment, AccessControlPrivileges, AccessPolicyAuthzRolesMap, AppStore, BankTransfer, BankingAccount, BankingAccountService, BankingAccountStatement, BankingAccountTpv, BankingConfig, Card, CorpCard, CorporateCard, CapitalTransaction, CapitalVirtualCards, ChargeCollections, Contact, CreditRepayment, CreditTransfer, FeeRecovery, FundAccount, FundTransfer, Invoice, Ledger, LedgerOutbox, Merchant (Balance, MerchantUser, Invoice, Product), Nodal, NodalBeneficiary, Payout, PayoutDowntime, PayoutLink, PayoutMeta, PayoutOutbox, PayoutSource, PayoutsDetails, PayoutsStatusDetails, Plan, Pricing, Report, Reversal, RoleAccessPolicyMap, Roles, Settlement, Transaction, User, VirtualAccount, WalletAccount, Workflow, Admin/Org. Not present as model dirs: `TaxPayment`, `Payroll`/`XPayroll`, `CreditLine` (empty dir), `Vendor*` — these are service proxies only (`app/Services/TaxPayments`, `app/Services/XPayroll`, `app/Services/VendorPayments`, `app/Services/VendorPortal`, `app/Services/VendorExperience.php`).

## 3) Mini-cards (priority modules)

### 3.1 Payout (anchor) — `app/Models/Payout/`
| Item | Evidence |
|---|---|
| Table | `payouts` (`app/Constants/Table.php:31`); child tables `payout_sources` (`:68`), `payouts_details` (`:83`) |
| Key fields | `fund_account_id`, `balance_id`, `purpose`, `purpose_type`, `fees`, `tax`, `transaction_id`, `channel`, `mode`, `payout_link_id`, `pricing_rule_id`, `fee_type`, `workflow_feature`, `origin`, `is_payout_service` (`app/Models/Payout/Entity.php:90-139`) |
| Origin | `origin` ∈ {api=1, dashboard=2} (`Entity.php:365-378`); `WEBHOOK='webhook'` const also (`:367`) |
| Purpose | `refund, cashback, salary, utility bill, vendor bill, vendor advance, petty cash, business disbursal, credit card bill, payout, inter_account_payout, rzp_fees, rzp_charge_collections, rzp_tax_pay, RZP Fund Management, ica_transfer` (`app/Models/Payout/Purpose.php:20-36`); internal purposes map to FTA purpose SETTLEMENT/REFUND/RZP_FUND_MANAGEMENT (`:53-77`) |
| Relations | `merchant()`, `payoutLink()`, `fundAccount()`, `reversal()`, `workflowActions()`, `transaction()`, `user()`, `batch()`, `payoutSources()` (`Entity.php:996-1101`) |
| Routes | private: `POST payouts` (`Route.php:2311`), internal: `POST payouts_internal` (`:2313`), `POST payouts_internal_direct` (`:2314`), `PATCH payouts_internal/{id}/tax-payment-id` (`:2339`), `GET payouts_internal[/{id}]` (`:2352,2356`), `POST payouts_internal/{id}/approve|reject|cancel` (`:2420,2421,2770`), `POST payouts_service/fetch_pricing_info` (`:4818`); banking route allow-list `PRIVATE_BANKING_ROUTES` (`:20890-20961`) |
| PS gate | `DirectToPayoutsServiceGate::shouldDivert()` decides classic vs Payouts Service (`app/Models/Payout/DirectToPayoutsServiceGate.php:57-78`), live-mode only |
| Queues | `queued_payouts_initiate, approved_payout_distribute, approved_payout_processor, batch_payouts_process, queued_payouts, payout_service_dual_write(_direct_push), payout_usage_event_processing, payout_service_data_migration, scheduled_payouts_process, payout_post_create_process(_low_priority), on_hold_payouts_process, partner_bank_on_hold_payouts_process, payouts_auto_expire, x_balances_payout_event, payout_source_updater, ledger_x_journal, ledger_transactions, ledger_status` (`config/queue.php:49-160,231,500`) |
| Jobs | `app/Jobs/{ApprovedPayoutProcessor,QueuedPayouts,ScheduledPayoutsProcess,PayoutPostCreateProcess,PayoutSourceUpdaterJob,PayoutServiceDualWrite,FreePayoutMigrationForPayoutsService,PayoutsAutoExpire,OnHoldPayoutsProcess,FundManagementPayouts/...}` |
| Webhooks | `payout.processed/reversed/failed/queued/initiated/updated/rejected/pending/creation.failed`, `transaction.created`, `fund_account.validation.completed/failed`, `payout.downtime.started/resolved` (`app/Models/Merchant/Webhook/Event.php:68-81,132-138`) |
| Ledger events | `payout_initiated/processed/reversed/failed`, `inter_account_payout_*`, `va_to_va_payout_*`, `nodal_fund_loading(_reverse)`, `da_payout_*`, `da_ext_*`, `da_fee_payout_*`, `cc_debit_*` (`app/Models/Transaction/Processor/Ledger/Payout.php:45-78`) |
| TDS | `TdsProcessor` publishes `{payout_id, tds_category_id, tds_amount}` to Kafka/Metro topic `add-tds-entry` (`app/Models/Payout/TdsProcessor/Processor.php:44-86`, `Constants.php:7`) |
| Flags | `payout`, `payouts_batch`, `payout_workflows`, `skip_wf_for_payroll`, `skip_wf_for_payout_link`, `skip_wf_at_payouts`, `bulk_payout_workflow`, `payout_service_enabled`, `schedule_payout_via_ps`, `fetch_va_payouts_via_ps`, `workflow_via_payouts_ms`, `payout_async_ingress`, `high_tps_*`, `payouts_on_hold`, `payout_to_cards`, `rx_show_payout_source`, `hide_rx_payroll_payouts`, `razorpayx_flows_via_oauth` (`app/Models/Feature/Constants.php:53-54,183,602-631,938,1138-1153,1503,1643-1666,1904,2302`) |

### 3.2 PayoutSource — `app/Models/PayoutSource/`
Table `payout_sources` (`payout_id, source_id, source_type, priority`; `Entity.php:17-21`). `source_type` values and handler (SourceUpdater, `app/Models/Payout/SourceUpdater/Factory.php`):

| source_type | Owner / updater | Evidence |
|---|---|---|
| `payout_links` | PayoutLinkUpdater + GenericAccountingUpdater | Factory.php:38-42 |
| `vendor_payments`, `tax_payments`, `vendor_settlements`, `vendor_advance` | VendorPaymentUpdater → vendor-payments svc `PayoutStatusChange` | Factory.php:29-36; `app/Services/VendorPayments/Service.php:32,480-503` |
| `settlements` | SettlementsUpdater → settlements svc twirp `rzp.settlements.transfer.v1.TransferService/StatusUpdatePayout` | Factory.php:45; `app/Services/Settlements/Payout.php:14,35` |
| `xpayroll` | XPayrollUpdater → Opfin `POST /v2/api/merchant-payout-status` | Factory.php:51; `app/Services/XPayroll/Service.php:26,59` |
| `refund` | RefundsUpdater | Factory.php:57 |
| `charge_collections` | ChargeCollections updater | Factory.php:63 |
| `capital_collections` | CapitalCollectionsUpdater → `app['capital_collections']->pushPayoutStatusUpdate` | Factory.php:69; `app/Services/CapitalCollectionsClient.php:73` |
| `petty_cash` | PettyCashUpdater | Factory.php:75 |
| `ica_transfer` | CrossBorderICATransferUpdater | Factory.php:80 |
| `generic_accounting_integration` | GenericAccountingUpdater (default when no sources) | Factory.php:22-25 |
| `payout` (self) | const `PAYOUT='payout'` | PayoutSource/Entity.php:39 |

Dashboard hides payroll rows via `source_type_exclude=xpayroll` when `hide_rx_payroll_payouts` (`app/Models/Payout/Service.php:2235-2237,2498-2507`).

### 3.3 Transaction / Statement (X ledger in monolith) — `app/Models/Transaction/`
| Item | Evidence |
|---|---|
| Table | `transactions` (`Table.php:118`); polymorphic `source()` = `morphTo('source','type','entity_id')` (`Transaction/Entity.php:237-239`) |
| Fields | `entity_id, type, amount, debit, credit, fee, mdr, tax, pricing_rule_id, balance, fee_credits, channel, fee_model, fee_bearer, credit_type, on_hold, settled, settled_at, settlement_id, balance_id, balance_updated, posted_at` (`Entity.php:37-68`) |
| Types | banking set: `payout, bank_transfer, reversal, adjustment, external, fund_account_validation, credit_transfer` (`Type.php:59-67`); capital: `repayment_breakup, installment, charge, interest_waiver` (`:69-74`); PG set (`:76-87`) |
| Processors | one per source type: `Processor/{Payout,Reversal,Adjustment,BankTransfer,External,FundAccountValidation,CreditTransfer,CapitalTransaction,CreditRepayment,Settlement,SettlementOndemand,...}.php`; payout processor updates `balance.balance` under lock (`Processor/Payout.php:83-102,350`) and fee breakup (`:306`) |
| Ledger shadow | `Processor/Ledger/Payout.php::pushTransactionToLedger` (`:107`), queues `ledger_x_journal` etc.; flags `ledger_journal_writes/reads`, `ledger_reverse_shadow`, `da_ledger_journal_writes` (`Feature/Constants.php:1459-1466`) |
| X statement | `Transaction\Statement\Entity extends Transaction\Entity` adds `account_number, contact_*, payout_id, payout_purpose, mode, fund_account_id, utr, adjustment_id` (`Statement/Entity.php:21-50`); routes `GET transactions/{id}`, `GET transactions`, `GET transactions_banking(_internal)` → `StatementController@listForBanking` (`Route.php:4004-4008`); `Statement/DirectAccount`, `Statement/Ledger` subdirs |
| Reversal | table `reversals`; `entity_id/entity_type` (payout), `balance_id, fee, tax, transaction_id, utr` (`app/Models/Reversal/Entity.php:33-51`) |
| Adjustment | table `adjustment`; `balance_id, settlement_id, type, source/destination_balance_id` (`Adjustment/Entity.php:17-40`); banking/`reserve_banking` adjustments (`Adjustment/Core.php:112-113,689-697`) |
| Fee invoice | `merchant_invoice` types `rx_transactions`, `rx_adjustments`, `x_charge_collections` (`app/Models/Merchant/Invoice/Type.php:17-23`), computed per banking balance (`Invoice/Processor.php:171-175,261-271`) |

### 3.4 Merchant\Balance — `app/Models/Merchant/Balance/`
| Item | Evidence |
|---|---|
| Table | `balance` (`Table.php:45`); fields `merchant_id, type, currency, balance, locked_balance, on_hold, credits, fee_credits, reward_fee_credits, refund_credits, account_number, account_type, channel` (`Balance/Entity.php:37-68`) |
| `type` | PG: `primary, commission, prefund_withdrawal, fee_credits, refund_credits, amount_credits, reserve_primary, in_person, cb_export_moneysaver`; X: `banking`, `reserve_banking`; Capital: `principal, interest, charge` (`Type.php:13-43`) |
| `account_type` (X only) | `direct` (CA at partner bank), `shared` (RazorpayX nodal/VA), `corp_card`, `rx_wallet` (`AccountType.php:19-34`) |
| Product map | `Type::getTypeForProduct(banking)=banking` (`Type.php:83-96`) |
| Settleable | `$settleableBalanceTypes = primary,in_person,cb_export_moneysaver,commission,prefund_withdrawal` — banking NOT included (`Type.php:64-70`) |
| Free payouts | slab-based free payout counts per channel (RBL/ICICI 500 direct, 300 shared) (`FreePayout.php:43-103`) |
| Routes | `GET banking_balances` → `MerchantController@getBankingAccountBalances` (`Route.php:654`), `POST capital_balances` (`:663`) |
| Jobs | `MerchantBalanceUpdate*, XBalanceDualWrite, LowBalanceConfigAlert, ConnectedBankingAccountGatewayBalanceUpdate, Rbl/IciciBankingAccountGatewayBalanceUpdate` (`app/Jobs/`) |
| External | x-balances svc (`applications.x_balances.url`=`APP_X_BALANCES_URL`, `config/applications.php:1229`) |

### 3.5 Settlement — `app/Models/Settlement/`
| Item | Evidence |
|---|---|
| Table | `settlements`: `merchant_id, bank_account_id, batch_fund_transfer_id, amount, fees, tax, status, transaction_id, channel, utr, fts_transfer_id, balance_id, is_new_service, journal_id, balance_type` (`Settlement/Entity.php:19-47`) |
| X relation | PG-only for X balances (see 3.4); on-demand settlement (`Settlement/Ondemand`, tables `settlement_ondemands`, `settlement_ondemand_payouts` etc., `Table.php:348-360`) creates *X payouts* via `RazorpayXClient::makePayoutRequest` → `POST {razorpayx_url}/payouts` as configured ondemand X merchant (`app/Services/RazorpayXClient.php:131-190`; `config/applications.php:40-57` keys `ondemand_x_merchant.id/username/secret/account_number/webhook_key`, `ondemand_contact.fund_account_id`); status consumed from `payout.processed` webhook (`Settlement/OndemandPayout/Core.php:307-350`) |
| Settlements service | `applications.settlements_service.url.live/test` = `SETTLEMENTS_LIVE_URL/TEST_URL` (`config/applications.php:697-699`), twirp client `app/Services/Settlements/{Api,Payout,Dashboard,MerchantDashboard,Reminder}.php`; payouts with source `settlements` push status back (`Settlements/Payout.php:14`) |
| Queues | `settlement_create, settlement_bucket, settlement_initiate, settlement_service_txns, transfer_settlement` (`config/queue.php:279,369-389`) |
| Jobs | `app/Jobs/Settlement/*, SettlementOndemand/*, ProcessSettlementServiceTxns, CommissionTdsSettlement` |

### 3.6 Pricing / Plan — `app/Models/Pricing/`
| Item | Evidence |
|---|---|
| Table | `pricing` (`Table.php:46`); rule fields `plan_id, plan_name, product, procurer, feature, gateway, payment_method, auth_type, payment_method_type/subtype, payment_network, international, fee_bearer, payouts_filter, fee_model, app_name, type, percent_rate, fixed_rate, min_fee, max_fee, amount_range_*, account_type, channel, org_id` (`Pricing/Entity.php:25-93`) |
| Product | `product ∈ {primary, banking}` (`Entity.php:168,674-679`, `app/Constants/Product.php:19-24`) |
| Feature | `payment, payout, recurring, transfer, emi, esautomatic, fund_account_validation, refund, settlement_ondemand, optimizer, ...` (`Feature.php:9-30`) |
| Payout fee calc | `Pricing\Fee::calculateMerchantFees` (`Fee.php:135`) → `PayoutFee` (tax recompute 18%, zero-pricing plan `EDoLfqMMBHVYGR` for `rzp_fees` payouts) (`PayoutFee.php:12-66`); called from `Payout/Core.php:3868` |
| Banking fallback | `DEFAULT_BANKING_PLAN_ID='BTo98voDY05ueB'` (`Fee.php:56`); `addBankingFallbackRulesIfApplicable` only when entity=payout and `merchant.business_banking=1` (`Fee.php:445-461`); default rules injected per `account_type` shared/direct × `payouts_filter=free_payout` × channel (RBL/ICICI/AXIS/YESBANK/IDFC) (`Fee.php:560-610`); product chosen by balance type (`Fee.php:790-798`) |
| PS integration | `POST payouts_service/fetch_pricing_info` → `PayoutController@fetchPricingInfoForPayoutService` → `Payout\Service::fetchPricingInfoForPayoutService` → `Core::fetchPricingInfoForPayoutService` → processor `fund_account_payout` (`Route.php:4818`; `PayoutController.php:71-78`; `Payout/Service.php:298`; `Payout/Core.php:8635-8638`) |
| Routes | `POST pricing/rules/bulk`, `POST internal/pricing/rules/bulk`, `POST merchants/pricing/bulk` (`Route.php:41,1108,636`) |
| Plan module | `app/Models/Plan` is *subscription plans* (Cycle/Subscription), not pricing (OBS dir listing) |

### 3.7 TaxPayments / TDS — `app/Services/TaxPayments/` (no model dir)
| Item | Evidence |
|---|---|
| Purpose | Proxy to vendor-payments service twirp `twirp/razorpay.vendorpayments.taxpayments.Taxpayments` (`Service.php:14`), config `applications.vendor_payments` (`Service.php:88`), auth basic `api:<VENDOR_PAYMENT_INTERNAL_APP_SECRET>` + headers `X-Merchant-Id/X-User-Id/X-Org-Id/X-App-Mode` (`Service.php:577-620`) |
| RPCs | `PayTaxPayment, BulkPayTaxPayments, InitiateMonthlyPayouts, CancelQueuedPayoutCron, AddPenaltyCron, CreateDirectTaxPayment, WebHookHandler, GetTdsCategories, FetchPendingGst, InternalIciciAction, ...` (`Service.php:15-45`) |
| Routes | 40 routes `tax-payments/*` incl. `POST tax-payments/{id}/pay`, `POST tax-payments/bulk-pay`, `POST tax-payments/initiateMonthlyPayouts` (cron), `POST tax-payments/direct` (public DTP), `POST tax-payments/direct/pg-webhook` (`Route.php:2646-2684`) |
| Payout path (INF) | vendor-payments service creates payouts on `POST payouts_internal` with `purpose=rzp_tax_pay` and `payout_sources.source_type=tax_payments`; monolith then `PATCH payouts_internal/{id}/tax-payment-id` stores `payouts_details.tax_payment_id` (`Route.php:2339`; `Payout/Service.php:6241-6250`; `PayoutsDetails/Entity.php:16-17`). Tax-pay payouts are cancellable unlike other internal purposes (`Payout/Core.php:3087-3090`) |
| TDS | `payouts_details.tds_category_id`, `additional_info.tds_amount`; Kafka topic `add-tds-entry` (`TdsProcessor/Processor.php:31-86`); TDS categories via `GET vendor-payments/tds-categories` (`Route.php:2495`) |
| Flags | none X-specific found beyond `Permission::PAY_TAX_PAYMENTS, VIEW/UPDATE_TAX_PAYMENT_SETTINGS(_AUTO), TAX_PAYMENT_ADMIN_AUTH_EXECUTE` (`Route.php:11615,12401-12405`) |

### 3.8 XPayroll — `app/Services/XPayroll/Service.php`
| Item | Evidence |
|---|---|
| Host | `applications.xpayroll.baseUrl` = env `OPFIN_SERVICE_URL`, secret `OPFIN_SERVICE_SECRET` (comment: "secret used by the Opfin to call apis under internal auth") (`config/applications.php:215-220`) |
| Outbound | `POST /v2/api/merchant-payout-status` (payout status push), `POST /v2/api/validate-source-account` (payroll TPV for fund loading) (`Service.php:26-28,59,216-264`) |
| Inbound | internal app `app.xpayroll` (`config/applications_v2.php:741-761`) may call `user_details, user_all_roles, payout_create_internal, payout_create_on_internal_contact, payout_create_2FA_internal, contact_create_internal, fund_account_create_internal, banking_accounts_list_internal, tax_payments_internal_icici_action, user_details_for_payroll, bulk_payout_purpose_post` (`Route.php:18626-18638`); `GET users_fetch_for_payroll` (`:2909`) |
| Workflow | `skip_wf_for_payroll` feature skips maker-checker when caller `isXPayrollApp()` (`Payout/Processor/Base.php:2614-2619`; `Payout/Service.php:1078`) |
| Flags | `skip_wf_for_payroll`, `payroll_sav`, `hide_rx_payroll_payouts` (`Feature/Constants.php:614,2297,2302`) |
| OAuth? | OBS: zero matches for payroll in `app/Models/OAuthApplication`, `MerchantApplications`, `Application` → not an OAuth/partner app in monolith; it is a basic-auth internal app. Payroll product hostname UNK from monolith (only env name `OPFIN_SERVICE_URL`). |

### 3.9 Cards — `Card`, `CorporateCard`, `CorpCard`, `CapitalVirtualCards`
| Item | Evidence |
|---|---|
| `Card` | PG card entity/vault/IIN (`app/Models/Card/*`), not X |
| `CorporateCard` | table `corporate_cards` (`Table.php:85`): `name, holder_name, expiry_*, is_active, network, issuer, billing_cycle, vault_token` (`CorporateCard/Entity.php:22-33`); routes `POST corporate_cards/token/{token}`, `PATCH/GET corporate_cards/{id}`, `GET corporate_cards`, `GET corporate_cards/iframe/form` (`Route.php:3996-4000`). Purpose INF: merchant's own credit card saved for X "credit card bill" payouts (Purpose `credit_card_bill`, `Purpose.php:29`; feature `payout_to_cards`, `Feature/Constants.php:183`). No ledger/balance link OBS. |
| `CorpCard` | `POST merchant/onboardCCCForBanking` → `CorpCardController@onboardCapitalCorpCardForRzpX` (`Route.php:5248`) creates `balance` row `account_type=corp_card, channel=m2p, type=banking` (`CorpCard/Core.php:50-62`; `Balance/Core.php:273,314-321`) — i.e. Capital corporate card spend line exposed as an X balance; card account details from capital-cards `GET v1/vendorpayment/account_details` (`app/Services/CapitalCardsClient.php:21,76`, host `applications.capital_cards.url`=`APP_CAPITAL_CARDS_URL`) |
| `CapitalVirtualCards` | proxy routes `capital_cards/*`, `cards/{token,number,otp,cvv,session}`, `virtual-card` (`Route.php:1563-1575`) |
| Flags | `capital_cards`, `capital_cards_eligible`, `capital_cards_collections`, `cards_transaction_limit_1/2`, `cash_on_card` (`Feature/Constants.php:344,709-759`) |

### 3.10 Capital — `CapitalTransaction`, `CreditRepayment`, `CreditTransfer`, LOC/collections proxies
| Item | Evidence |
|---|---|
| `CapitalTransaction` | table `capital_transaction` (`Table.php:419`): `type, merchant_id, amount, currency, transaction_id, balance_id` (`Entity.php:13-18`), `belongsTo Balance` (`:57`); posts to `transactions` via `Transaction/Processor/CapitalTransaction.php` on capital balances (`principal/interest/charge`) (`:13-71`); routes `POST capital_balances/transaction`, `POST capital_balances/multi_transactions`, `POST credit_repayments/transaction` (`Route.php:4010-4012`) |
| `CreditTransfer` | table `credit_transfers` (`Table.php:86`): `balance_id, amount, transaction_id, utr, entity_id/type, mode, channel, payer_*` (`CreditTransfer/Entity.php:22-43`); is a BANKING txn type; banking-balance path (`CreditTransfer/Core.php:513`); route `POST credit_transfer/create_async` (`Route.php:4805`) — INF: fund-loading credits into X balances |
| Collections | `applications.capital_collections.url`=`APP_CAPITAL_COLLECTIONS_URL` (`config/applications.php:1083`); client pushes payout status and settlement-ondemand→ledger updates (`CapitalCollectionsClient.php:73-125`); payout source `capital_collections`; routes `capital_collections/service|admin|orders` (`Route.php:1576-1579`) |
| LOC | `applications.line_of_credit.url`=`APP_LINE_OF_CREDIT_URL`; routes `loc/service|admin|cron`, `POST loc/withdrawal/update` → `LOCController@razorpayXWebhook` forwards X payout callback (`xPayoutCallback`) (`Route.php:1557-1562`; `LOCController.php:~86`) — INF: LOC withdrawals are disbursed as X payouts |
| Partner→X | Capital partnership sub-merchants get `product=banking` user rows (`Merchant/Core.php:6163-6169`) |

### 3.11 Vendor payments / Invoices / Accounting
| Item | Evidence |
|---|---|
| VendorPayments | `app/Services/VendorPayments/Service.php` (config `applications.vendor_payments`, `:12`): twirp RPCs `CreateVendorPayment, ExecuteVendorPayment(2fa/Bulk), PayoutStatusChange, GetTdsCategory, SearchContacts, ...` (`:30-50`); ~110 routes `vendor-payments/*` incl. `POST vendor-payments/{id}/execute`, `POST vendor-payments/settlements/single|multiple` (vendor settlements), `GET vendor-payments/contacts/{id}/vendor-balance` (`Route.php:2468-2518`) |
| VendorPortal | `app/Services/VendorPortal/Service.php`; routes `vendor-portal/*` (`Route.php:2560`); roles `vendor` |
| VendorExperience | `applications.vendor_experience.url`=`APP_VENDOR_EXPERIENCE_URL` (`config/applications.php:1262`) |
| Accounting | `accounting-payouts/*` routes → `AccountingPayoutsController` (`Route.php:2593-2601`); `applications.accounting_integrations.url`=`ACCOUNTING_INTEGRATIONS_HOST_URL`, `banking_service_url`=`https://x.razorpay.com`, `bank_lms_banking_service_url`=`https://partner-lms.razorpay.com` (`config/applications.php:1196-1204`); Tally routes in `PRIVATE_BANKING_ROUTES` (`Route.php:20936-20957`) |
| `Invoice` model | PG payment-links/invoices (`Invoice/Type.php:9-18`: ecod, invoice, link, dcc_inv, nca_invoice…) — NOT X vendor invoices; no Payout refs OBS |
| Merchant fee invoice | see 3.3; `Report/Types/BankingInvoiceReport.php` |

### 3.12 BankingAccount / CurrentAccount — `app/Models/BankingAccount*/`
| Item | Evidence |
|---|---|
| Table | `banking_accounts` (`Table.php:316`): `channel, account_number, account_ifsc, status, sub_status, bank_internal_status, fts_fund_account_id, balance_id, gateway_balance, account_type, beneficiary_*, username/password (vault ns `banking_accounts_creds`)` (`BankingAccount/Entity.php:34-126`) |
| Channels | `yesbank, idfc, rbl, icici, kotak, axis, m2p, hdfc, slice` (`Channel.php:14-22`); account types `nodal, savings, current, direct, corp_card` (`AccountType.php:7-11`) |
| Statement | table `banking_account_statement` (`Table.php:321`): `bank_transaction_id, amount, type, category, balance, entity_id/type, transaction_id, utr, bas_details_id` (`BankingAccountStatement/Entity.php:20-64`); routes `banking_account_statement/{generate,process,pool/process,process/{channel},insert_missing,...}` (`Route.php:4180-4193`); jobs `Rbl/IciciBankingAccountStatement, BankingAccountStatementRecon(Neo), ...SourceLinking` |
| External | banking-account-service `applications.banking_account_service.url`=`APP_BANKING_ACCOUNT_SERVICE_URL` (`config/applications.php:1966`; client `app/Services/BankingAccountService.php:98`), master-onboarding `APP_MASTER_ONBOARDING_SERVICE_URL` (`:1974`), x-account-statements `APP_X_ACCOUNT_STATEMENTS_URL` (`:1221`) |
| Fee recovery | RazorpayX fee accounts per channel (`config/banking_account.php:12-38`); `FeeRecovery` creates `rzp_fees` payouts (`FeeRecovery/Core.php:65-224`) |
| Flags | `banking_accounts_issued`, `rbl_ca_upi`, `virtual_accounts_banking` (`Feature/Constants.php:68,991,1208`) |

### 3.13 Wallet / Beam
| Item | Evidence |
|---|---|
| `WalletAccount` | table `wallet_accounts` (`Table.php:436`): `entity_id/type, phone, email, provider(amazonpay), fts_fund_account_id` (`WalletAccount/Entity.php:17-26`, `Provider.php:7`) — fund-account destination type for payouts to AmazonPay (`FundAccount/Core.php:160,364`); flag `disable_x_amazonpay` (`Feature/Constants.php:1191`) |
| `rx_wallet` | balance `account_type=rx_wallet` (`Balance/AccountType.php:34`), payout `destination_type in rx_wallet,direct,lite` (`Payout/Validator.php:740`) — INF: RazorpayX Lite/wallet product |
| `wallet` (PG) | `applications.wallet.url.live/test`=`APP_WALLET_LIVE_URL` (`config/applications.php:1059-1066`) and `wallet_service` (`:2148`) — PG customer wallet, not X |
| Beam | file-push service to banks: `app/Services/Beam/Service.php::beamPush` (`:87`), config `applications.beam.url`=`BEAM_URL`, `chota_beam`=`CHOTABEAM_URL` (`config/applications.php:981-987`); used by `FundTransfer/{Axis2,Icici}/{NodalAccount,Beneficiary}.php` |

### 3.14 Identity: Merchant / Org / User / MerchantUser / Roles
| Item | Evidence |
|---|---|
| Tables | `users` (`Table.php:19`), `merchant_users` (`:148`), `orgs` (`:221`), `access_control_roles` (custom roles; `:480,517`), `access_policy_authz_roles_map` (`:481`), `role_access_policy_map` (`:482`) |
| MerchantUser | `merchant_id, user_id, role, product` (`MerchantUser/Entity.php:22-25`); X membership = `product='banking'` (`User/Entity.php:474-495`, `MerchantUser/Repository.php:168-465`) |
| Merchant | `org_id`, `parent_id`, `partner_type`, `business_banking` (X enabled flag), `business_banking_signup_at` (`Merchant/Entity.php:110-337`; `isBusinessBankingEnabled()` `:3483`) |
| Org | `orgs`: `auth_type, business_name, email_domains, default_pricing_plan_id, cross_org_access, *_second_factor_auth, merchant_session_timeout_in_seconds` (`Admin/Org/Entity.php:27-56`); Razorpay org id `100000razorpay` (`Roles/Entity.php:48-49`) |
| PG roles | `owner, admin, manager, operations, finance, support, sellerapp, view_only, linked_account_*, rbl_supervisor/agent, ...` (`User/Role.php:14-63`); `BANKING_ROLES=[owner,pseudo_owner,admin]` (`:154`) |
| X roles | `User\BankingRole`: `owner, admin, view_only, operations, chartered_accountant, vendor, petty_cash_employee, banking_readonly, finance_l1/l2/l3, finance, authorised_signatory, cc_admin, maker, maker_admin, checker_l1/l2/l3, bank_mid_office_poc/manager` (`BankingRole.php:20-49`); custom roles `access_control_roles(name,type=custom|standard,merchant_id,product,org_id)` mapped to authz roles (`Roles/Entity.php:28-49`, `RoleAccessPolicyMap/Entity.php:16-18`) |
| Assignment | `Role::validateProductRoleForMerchant(role, banking)` accepts LMS roles or any `Roles\Service::getRoleUsingExperiment(role)` (`User/Role.php:218-231`); ownership transfer / sub-merchant attach for `Product::BANKING` (`Merchant/Core.php:4281-4286,6163-6169,11158-11160`); CAC routes `cac/roles, cac/role_map, cac/privileges, cac/privileges_authz, cac/privilege_role_mappings` (57 routes, `Route.php:5223-5236`) |
| External | authz `AUTHZ_BASE_URL`, `AUTHZ_XPLATFORM_ENFORCER_BASE_URL`, `AUTHZ_XPLATFORM_ADMIN_BASE_URL` (`config/applications.php:2201-2222`); user-service `USER_SERVICE_CLIENT_URL` (`config/services.php:134`); `USER_SERVICE_ROUTES` (`Route.php:21428`) |

## 4) Answers to §3 questions (with cites)

(a) **Payout source/origin/product**: `origin ∈ {api, dashboard}` (`Payout/Entity.php:365-378`); `product` is a *filter/serialization key* (`:234`) resolved from balance type → `banking|primary` (`Pricing/Fee.php:790-798`); `purpose` list `Purpose.php:20-36`; `payout_sources.source_type` 14 values with owners in §3.2.
(b) **Transaction links**: `transactions.type/entity_id` morph to payout/reversal/adjustment/bank_transfer/external/fund_account_validation/credit_transfer (`Transaction/Type.php:59-67`, `Entity.php:237-239`); fees on the payout row (`fee, tax, pricing_rule_id, fee_credits`) and `fee_breakup` (`Transaction/FeeBreakup/*`); balance pointer `balance_id`; "banking balance" = `balance.type='banking'` (+`reserve_banking`), with `account_type ∈ {shared, direct, corp_card, rx_wallet}` (`Balance/Type.php:35-36`, `AccountType.php:19-34`).
(c) **Settlement vs X**: X balances are not settleable (`Balance/Type.php:64-70`); PG on-demand/instant settlement *uses* X payouts via an internal X merchant (`RazorpayXClient.php:131-190`, `config/applications.php:40-57`); settlements-service payouts carry `source_type=settlements` (`PayoutSource/Entity.php:28`).
(d) **Pricing**: see §3.6 — rows keyed by `product=banking, feature=payout, payment_method(mode), account_type, channel, payouts_filter(free_payout)`; PS fetches via `payouts_service/fetch_pricing_info`.
(e) **Cards/Capital**: `CorpCard` creates a banking balance of `account_type=corp_card` (ledger via `transactions`), `CorporateCard` is a card-on-file for payouts-to-cards, Capital LOC/collections create X payouts (`source_type=capital_collections`, `loc/withdrawal/update` webhook) (§3.9-3.10).
(f) **TaxPayment/TDS**: §3.7 — payouts created by vendor-payments service with `purpose=rzp_tax_pay`, linked via `payouts_details.tax_payment_id`; TDS via Kafka `add-tds-entry`.
(g) **XPayroll**: §3.8 — Opfin service (`OPFIN_SERVICE_URL`), internal basic-auth app `app.xpayroll`, not OAuth.
(h) **Identity**: §3.14.

## 5) External hosts (config key → env var → service) — OBS `config/applications.php` unless noted

X/banking core: `payouts_service.url`→`PAYOUTS_URL` (:1752); `payouts_shadow_router`→`PAYOUTS_SHADOW_ROUTER_URL` (:1293); `fts.url`→`FTS_URL_LIVE/TEST` (:1275-1282); `ledger.url.live/test[,pg]`→`LEDGER_LIVE_URL/LEDGER_TEST_URL/LEDGER_LIVE_PG_URL` (:1934-1938); `x_balances`→`APP_X_BALANCES_URL` (:1229); `x_account_statements`→`APP_X_ACCOUNT_STATEMENTS_URL` (:1221); `xperience`→`APP_XPERIENCE_URL` (:1215); `banking_account_service`→`APP_BANKING_ACCOUNT_SERVICE_URL` (:1966), `..._WITHOUT_VERSION` (:1583); `master_onboarding`→`APP_MASTER_ONBOARDING_SERVICE_URL` (:1974); `payout_links`→`APP_PAYOUT_LINKS_URL`, `PAYOUT_LINKS_MICRO_SERVICE_URL` (:1207-1209); `vendor_payments`→`VENDOR_PAYMENT_URL` (:1169); `vendor_experience`→`APP_VENDOR_EXPERIENCE_URL` (:1262); `accounting_integrations`→`ACCOUNTING_INTEGRATIONS_HOST_URL`, `BANKING_SERVICE_URL`=`https://x.razorpay.com`, `BANK_LMS_BANKING_SERVICE_URL`=`https://partner-lms.razorpay.com` (:1196-1204); `accounts_receivable`→`ACCOUNTS_RECEIVABLE_HOST_URL` (:1180); `business_reporting`→`BUSINESS_REPORTING_HOST_URL` (:1188); `xpayroll.baseUrl`→`OPFIN_SERVICE_URL` (:219); `razorpayx_client.live.razorpayx_url`→`RAZORPAYX_URL_LIVE` (:47); `charge_collections.url`→`CHARGE_COLLECTIONS_LIVE/TEST_URL` (:2353-2354); `workflows`→`WORKFLOWS_URL` (:1721); `relay`→`RELAY_URL_LIVE/TEST` (:1303-1308); `batch`→`BATCH_SERVICE_URL` (:1317); `beam`/`chota_beam`→`BEAM_URL`/`CHOTABEAM_URL` (:982-987); `nodal`→`NODAL_BASE_URL` (:831), RBL nodal `RBL_NODAL_URL` (`config/nodal.php:13`); `virtual_accounts_service`→`VIRTUAL_ACCOUNTS_SERVICE_URL` (:1235); `abacus`→`APP_ABACUS_URL` (:1256); `settlements_service`→`SETTLEMENTS_LIVE/TEST_URL` (:698-699); `merchant_invoice`→`MERCHANT_INVOICE_SERVICE_BASE_URL` (:2362); `stork`→`STORK_URL` (`config/stork.php:9`).
Capital: `capital_cards`→`APP_CAPITAL_CARDS_URL` (:1076); `capital_collections`→`APP_CAPITAL_COLLECTIONS_URL` (:1083); `line_of_credit`→`APP_LINE_OF_CREDIT_URL` (:1023); `loan_origination_system`→`APP_LOAN_ORIGINATION_SYSTEM_URL` (:1016); `capital_marketplace/scorecard/lender/es/bnpl`→`APP_MARKETPLACE_URL/APP_SCORECARD_URL/APP_LENDER_URL/APP_ES_URL/APP_CAPITAL_BNPL_URL` (:1030-1091); `financial_data_service`→`APP_FINANCIAL_DATA_SERVICE_URL` (:1704).
Platform: `authz`→`AUTHZ_BASE_URL`, `AUTHZ_XPLATFORM_ENFORCER_BASE_URL`, `AUTHZ_XPLATFORM_ADMIN_BASE_URL` (:2201-2222); `dcs`→`DCS_LIVE/TEST_URL` (:1423-1428); `splitz`→`SPLITZ_URL` (:903); `razorx`→`RAZORX_URL` (:886); `shield`→`SHIELD_BASE_URL` (:868); `mozart`→`MOZART_URL/MOZART_LIVE_URL/...` (:137-161); `card_vault`→`CARD_VAULT_URL` (:262); `ufh`→`UFH_BASE_URL` (:843); `reporting`→`REPORTING_BASE_URL` (:836); `reminders`→`REMINDERS_URL` (:192); `raven`→`RAVEN_URL` (:186); `governor`→`GOVERNOR_LIVE_URL` (:303); `lumberjack`→`LUMBERJACK_URL` (:409); `harvester(_v2)`→`HARVESTER_URL` (:418-426); `elfin`→`GIMLI_BASE_URL` (:441); `auth_service`→`AUTH_SERVICE_URL` (:821); `dashboard`→`APP_DASHBOARD_URL` (:7); `recon`→`RECON_SERVICE_URL/RECON_PRS_SERVICE_URL` (:2092-2093); `acs`→`ASV_HOST`=`https://acs-web.razorpay.com`, `asv_v2.grpc_host`=`asv-grpc.razorpay.com` (:1990-2004); `activation_service`→`ACTIVATION_SERVICE_URL` (:1902); `care`→`CARE_SERVICE_HOST` (:1880); `cmma`→`CASE_MANAGEMENT_SERVICE_BASE_URL` (:1897); `developer_console`→`DEVELOPER_CONSOLE_HOST` (:1958); `templating`/`media_service` (:1914-1920); `pspx`→`PSPX_SERVICE_URL` (:1831); `salesforce(_converge)`, `superleap_converge`, `hubspot`, `freshdesk.urlx` (X helpdesk) (:462-475,1613-1635,1353).
`config/services.php`: `credcase`→`CREDCASE_HOST`=`https://credcase.razorpay.com` (:79); `bvs`→`BVS_HOST`=`https://bvs.razorpay.com` (:113); `user_service`→`USER_SERVICE_CLIENT_URL` (:134); `workflow_guard`→`WORKFLOW_GUARD_SERVICE_HOST` (:149); `merchant_risks`→`MERCHANT_RISKS_URL` (:156); `ocr`→`OCR_HOST` (:168); `cds`→`CDS_HOST` (:271); `idp`→`IDP_SERVICE_HOST` (:294); `edge_throttler`, `rate_limiter`, `pgos`, `merchant_experience`, `druid`, `pinot`, `presto`, `segment`, `appsflyer`, `sumo_logic` (:61-265).
`config/edge_proxy.php`: proxies to `METRO_HOST_URL, ACCOUNTS_RECEIVABLE_HOST_URL, BUSINESS_REPORTING_HOST_URL, ACCOUNTING_INTEGRATIONS_HOST_URL, APP_WALLET_LIVE_URL, DISPUTES_BASE_URL, PARTNERSHIPS_LIVE_URL, BARRICADE_SERVICE_BASE_URL, MOZART_LIVE_URL` (:34-105).
PG-only (for completeness): `cps, bin_service, scrooge, pg_router, card_payment_service, nbplus_payment_service, upi_payment_service, terminals_service, payment_methods_service, smart_routing, doppler, subscriptions, mandate_hq, emandate_service, payment_links, nca_invoices, no_code_apps, smart_collect, qr_code_service, wallet/wallet_service, checkout_service, affordability, dispute_service, disputes, cyber_crime_helpdesk, shipping/rto/address, optimizer_core_service, downtime_manager, kms, cms, store_service, bbps`.
Actual hostnames: only env names are in the repo except literal defaults: `https://x.razorpay.com`, `https://partner-lms.razorpay.com`, `https://dispute-service.razorpay.com`, `https://acs-web.razorpay.com`, `asv-grpc.razorpay.com`, `https://bvs.razorpay.com`, `https://ocr.razorpay.com`, `https://cds.razorpay.com`, `https://credcase.razorpay.com`, `https://virtual-account-base.dev.razorpay.in`, `https://merchant-invoice-base.dev.razorpay.in/`, `https://user-service.dev.razorpay.in` (grep `razorpay\.(com|in)` over `config/*.php`).

## 6) Candidate domain signals (strength)

| Domain | Strength | Signal |
|---|---|---|
| Vendor payments / Tax payments / TDS / Vendor portal / Accounting payouts | **Strong** | ~150 routes, 4 payout source types, dedicated service clients, `payouts_details.tax_payment_id/tds_category_id`, Kafka `add-tds-entry`, invoice type `rx_transactions` |
| Current-account onboarding / statements (banking_accounts, BAS, BAS recon, fee recovery) | **Strong** | 2 tables, 9 channels, ~20 statement routes, 15+ jobs/queues, banking-account-service + master-onboarding hosts |
| Settlements (on-demand/instant settlement consuming X payouts; settlements-service source) | **Strong** | RazorpayXClient, ondemand X merchant config, `source_type=settlements`, 5 ondemand tables |
| Capital (LOC withdrawals, collections, corp-card balance) | **Medium-strong** | balance `account_type=corp_card`, `capital_transaction`, `credit_transfers`, LOC X webhook, `source_type=capital_collections`, 5 service hosts |
| Pricing / fee invoicing | **Medium** | banking rules in `pricing`, PS `fetch_pricing_info`, `merchant_invoice` RX types, free-payout slabs |
| Payroll (Opfin) | **Medium** | internal app + 11 allowed routes + 3 flags + status/TPV callbacks; no model/tables in monolith |
| Cards (corporate card-on-file; capital virtual cards) | **Weak-medium** | small entity + 5 routes; card lifecycle lives in capital-cards service |
| Wallet (AmazonPay destinations, rx_wallet/Lite) | **Weak** | 1 table, fund-account subtype, `rx_wallet` account type |
| Accounting integrations (Tally) | **Medium** | 17 Tally routes in PRIVATE_BANKING_ROUTES, host + edge proxy |
| Identity / CAC roles | **Medium** | 3 tables, 57 `cac_*` routes, authz hosts |

## 7) Probable repo/service names not in clone root (from config keys / class names)

| Service (config key) | Env var | Probable repo | Source of name |
|---|---|---|---|
| Opfin / XPayroll | `OPFIN_SERVICE_URL` | `opfin` (UNK exact) | `config/applications.php:215-219`, `app/Services/XPayroll` |
| capital-cards | `APP_CAPITAL_CARDS_URL` | `capital-cards` | `config/applications.php:1076`, `CapitalCardsClient.php` |
| capital-collections | `APP_CAPITAL_COLLECTIONS_URL` | `capital-collections` | `:1083` |
| line-of-credit / LOC | `APP_LINE_OF_CREDIT_URL` | `line-of-credit` | `:1023`, `LOCController` |
| loan-origination-system | `APP_LOAN_ORIGINATION_SYSTEM_URL` | `los` | `:1016` |
| settlements service | `SETTLEMENTS_LIVE_URL` | `settlements` (twirp `rzp.settlements.transfer.v1`) | `:698`, `Services/Settlements/Payout.php:14` |
| vendor-experience | `APP_VENDOR_EXPERIENCE_URL` | `vendor-experience` (IN clone) | `:1262` |
| master-onboarding | `APP_MASTER_ONBOARDING_SERVICE_URL` | `master-onboarding` | `:1974` |
| accounts-receivable | `ACCOUNTS_RECEIVABLE_HOST_URL` | `accounts-receivable` | `:1180` |
| business-reporting | `BUSINESS_REPORTING_HOST_URL` | `business-reporting` | `:1188` |
| abacus | `APP_ABACUS_URL` | `abacus` | `:1256` |
| merchant-invoice | `MERCHANT_INVOICE_SERVICE_BASE_URL` | `merchant-invoice` | `:2362` |
| beam / chota-beam | `BEAM_URL`/`CHOTABEAM_URL` | `beam` | `:982-987` |
| user-service | `USER_SERVICE_CLIENT_URL` | `user-service` | `config/services.php:134` |
| workflow-guard | `WORKFLOW_GUARD_SERVICE_HOST` | `workflow-guard` | `config/services.php:149`, `app/Services/WorkflowGuard` |
| reminders, raven, ufh, lumberjack, harvester, mozart(IN clone), scrooge, nodal, credcase, bvs, ocr, cds, idp | various | same names | `config/applications.php`, `config/services.php` |

## 8) Unknowns

- Actual hostnames for nearly all services (only env var names in repo; k8s/terraform not inspected here).
- Whether vendor-payments creates tax/vendor payouts via `payouts_internal` (monolith) or directly on Payouts Service — INF from route allow-lists; not verified in vendor-payments code in this pass.
- Which Payouts-Service-side entity mirrors `payout_sources`/`payouts_details` (`balance_id_on_payout_service` hint `Payout/Entity.php:277`).
- Exact set of X custom roles in prod (`access_control_roles` is data, standard ones seeded under merchant `100000Razorpay`, `Roles/Entity.php:48`).
- `Plan` module relation to X (appears PG subscriptions only); `Report` module usage for X beyond `BankingInvoiceReport`.
- Payroll product public hostname (`payroll.razorpay.com`?) — no literal in monolith.
