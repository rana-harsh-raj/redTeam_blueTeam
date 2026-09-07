# 02 — Payouts outbound edges: connections LEAVING the current graph

Pass: 2026-09-07. Clone root (READ-ONLY):
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture`
All paths below are relative to that CLONE ROOT (e.g. `payouts/config/prod.toml:369`).

Legend: **O** = OBSERVED in file · **I** = INFERRED · **U** = UNKNOWN.
Target ids: `svc:*`/`repo:*` = already a graph node; `NEW:*` = not currently a node (or only a stub).

## 0. Summary (10 lines)

1. The single widest leak is the payout `source_type` fan-out: **prod has 14 source types / 10 SNS topics**, five
   more than the dev config the earlier pass saw — adding `refund`, `charge_collections`, `capital_collections`,
   `cross_border` + `ica_transfer`, `petty_cash`, `vendor_settlements`, `vendor_advance`.
2. Ledger is the second: it is **multi-tenant (X, PG, PG_US, PG_SG, X_MY)** with **36 client identities**;
   payouts is 1 of only 3 on the X tenant. Its `fund_account_type` list is a complete product taxonomy.
3. Workflows is a **shared approval platform with 12 callers**, including 4 not in the graph (abacus,
   offers-engine, vendor-experience, growth, payments-bank-transfer) and 3 procurement workflow types.
4. FTS has **9 products** and **11 auth users**; two products leave the RazorpayX perimeter entirely —
   `SETTLEMENT` -> a Twirp settlements service, `CUSTOMER_PAYOUT` -> `api.razorpaywallet.com`.
5. **Cards is 2 hops away**: payouts -> cfa -> bin-service + token-service; plus `enable_payouts_to_cards`
   proto owned by payouts and a `corp_card` banking-account type.
6. **Capital is the densest cluster**: 5 distinct internal apps, 6 Ledger fund-account types, its own SNS topic,
   contact type, and an x-balances eligibility side effect.
7. **Cross-border** is a domain the earlier passes missed entirely (2 source types, RBI OPGSP purpose codes and
   a `JPMC_INTL_BANK_TRANSFER` channel living inside payouts).
8. Tax/TDS runs through **vendor-payments -> API monolith** (`v1/internalContactPayout/`), never the Payouts
   service; it also has a direct-to-ICICI challan rail that bypasses FTS.
9. New shared-infrastructure edges: `tax-compliance-vault`, `wda-service`, `reporting.razorpay.com`, `asv-grpc`,
   `metro`, `mob`, `bvs`, `partner-lms`, plus 3rd-party Zoho/Tally/MastersIndia/Veryfi/Hubspot/Segment.
10. Strongest promotions from stub to real node: **settlements**, **wallet**. Strongest brand-new nodes:
   **capital-collections**, **capital-cards**, **opfin/xpayroll**, **cross-border-import-service**,
   **scrooge**, **reporting**, **abacus**, **vendor-experience**.

---

---

## 1. EDGES table

### 1.1 Payout `source_type` fan-out — the single widest cross-domain surface

Payouts (Go) publishes one SNS message per `source_type` on payout status change; the monolith does the
equivalent synchronously via `SourceUpdater\Factory::getUpdaters()`. The **prod** topic map is the ground
truth for which domains subscribe — it lists **14 source types / 10 distinct topics**, five more than the
dev/default map.

| # | source_type | SNS topic (prod) | Consuming service | Monolith updater -> app binding | Domain |
|---|---|---|---|---|---|
| 1 | `xpayroll` | `payout-updates-xpayroll` | Opfin/XPayroll | `XPayrollUpdater.php:15` -> `app['xpayroll']` -> `OPFIN_SERVICE_URL` | **Payroll** |
| 2 | `payout_links` | `payout-updates-payout-links` | payout-links | `PayoutLinkUpdater.php:16` -> `app['payout-links']` | Payouts (in graph) |
| 3 | `settlements` | `payout-updates-settlements` | settlements service | `SettlementsUpdater.php:14` -> `app['settlements_payout']` -> `SETTLEMENTS_LIVE_URL` | **Settlements** |
| 4 | `refund` | `payout-updates-refunds` | **Scrooge** (refunds svc) | `RefundsUpdater.php:14` -> `app['scrooge']` -> `SCROOGE_URL` | **Refunds/PG** |
| 5 | `vendor_payments` | `payout-updates-vendor-payments` | vendor-payments | `VendorPaymentUpdater.php:14` -> `app['vendor-payment']` | Vendor payments (in graph) |
| 6 | `tax_payments` | `payout-updates-vendor-payments` (shared) | vendor-payments (tax module) | same updater (`Factory.php:31`) | **Tax payments** |
| 7 | `vendor_settlements` | `payout-updates-vendor-payments` (shared) | vendor-payments | `Factory.php:32` | Vendor payments |
| 8 | `vendor_advance` | `payout-updates-vendor-payments` (shared) | vendor-payments | `Factory.php:33` | Vendor payments |
| 9 | `charge_collections` | `payout-updates-charge-collections` | charge-collections | `ChargeCollections.php:14` -> `app['charge_collections']` | Fees (in graph) |
| 10 | `capital_collections` | `payout-updates-capital-collections` | **capital-collections** | `CapitalCollectionsUpdater.php:14` -> `app['capital_collections']` -> `APP_CAPITAL_COLLECTIONS_URL` | **Capital / lending** |
| 11 | `cross_border` | `payout-updates-cross-border` | **cross-border-import-service** | (topic only; no monolith case) | **Cross-border** |
| 12 | `ica_transfer` | `payout-updates-cross-border` (shared) | **cross-border-import-service** | `CrossBorderICATransferUpdater.php:14` -> `app['cross_border_import_service']` | **Cross-border** |
| 13 | `petty_cash` | `payout-updates-petty-cash` | **xperience** (petty-cash module) | `PettyCashUpdater.php:14` -> `app['xperience']` | **Petty cash / expense** |
| 14 | `generic_accounting` | `payout-updates-generic-accounting` | accounting-integrations | `GenericAccountingUpdater.php:41` -> `app['accounting-integration-service']` | Accounting (in graph) |

Evidence, all OBSERVED:
- `payouts/config/prod.toml:368-388` `[source_update_topics]` (all 14 keys + the "same as" comments).
- `payouts/config/default.toml:499-509` dev map (only 9 keys, all -> `payout-updates-test-dev`).
- `payouts/internal/config/config.go:374-376` `type SourceUpdateTopics map[string]string` ("adding new sources without code changes - just update config file").
- `payouts/pkg/sourceupdater/publisher.go:161-206` `publishToTopic()` — SNS message attributes `source_type`, `status`, `payout_id`, `previous_status`; `publisher.go:88-115` `publishToTopics()` routing (Route 1 source topics, Route 2 GAI vanilla, Route 3 skip).
- `payouts/pkg/sourceupdater/publisher.go:177` `const genericAccountingSourceType = "generic_accounting"`.
- `api/app/Models/PayoutSource/Entity.php:23-36` source-type consts; `:47-56` `$validSourceTypes` (only 8 accepted on create — see gap note below).
- `api/app/Models/Payout/SourceUpdater/Factory.php:28-85` `getUpdaters()` switch.
- `api/app/Services/ApiServiceProvider.php:261,276,2509,2553,2561,2903,3226` singleton bindings.
- `api/config/applications.php:215` xpayroll/OPFIN, `:222` scrooge, `:696` settlements_service, `:1075` capital_cards, `:1082` capital_collections, `:1090` capital_bnpl, `:1168` vendor_payments, `:1195` accounting_integrations, `:1817` cross_border_import_service, `:2342` charge_collections.

**Gap (O):** `Entity.php:47-56 $validSourceTypes` accepts `payout_links, vendor_payments, tax_payments, settlements,
xpayroll, capital_collections, petty_cash, ica_transfer` — it does **not** list `refund`, `charge_collections`,
`vendor_settlements`, `vendor_advance`, `generic_accounting_integration`, even though `Factory.php` dispatches on
them and prod has topics for them. Those rows are therefore written by non-API paths or by direct DB/other service.

### 1.2 Payouts service — outbound HTTP / gRPC clients (prod hosts)

| source | target | mechanism | evidence file:line + symbol | domain | O/I |
|---|---|---|---|---|---|
| svc:payouts | NEW:tax-compliance-vault | HTTPS (HashiCorp-Vault API, namespace `razorpayx`, mount `secrets`) | `payouts/config/prod.toml:718-724` `[hvault] host="https://tax-compliance-vault.razorpay.com"`; client `payouts/pkg/hvault/` | **Tax compliance** | O |
| svc:payouts | NEW:wda-service (Warehouse Data Access) | HTTPS + `db_namespace`/`db_cluster` | `payouts/config/prod.toml:726-732` `[wda] default_http_client_url="https://wda-service.razorpay.com"`; `payouts/pkg/wda/` | **Reporting / data warehouse** | O |
| svc:payouts | NEW:raven | HTTPS | `payouts/config/prod.toml:754-755` `[raven] host="https://raven.razorpay.com"`; `payouts/pkg/raven/` | Notifications (SMS/email) | O |
| svc:payouts | svc:governor | HTTPS | `payouts/config/default.toml:790` `hostname="https://governor.razorpay.com"` (inside `[ccsdk]`) | Rules/ops | O |
| svc:payouts | svc:ups (payments-upi) | HTTPS | `payouts/config/prod.toml:292-293` `[ups] host="https://payments-upi.razorpay.com"` | UPI/PG | O |
| svc:payouts | NEW:asv (account-service) | gRPC `asv-grpc.razorpay.com:443` | `payouts/config/prod.toml:670-671` `[account_service] host="asv-grpc.razorpay.com:443"`; `payouts/pkg/account/` | **Shared merchant/org identity** | O |
| svc:payouts | svc:mozart | HTTPS | `payouts/config/prod.toml:234-235` `[mozart]` | Bank gateway | O |
| svc:payouts | svc:api-monolith | HTTPS `prod-api-int.razorpay.com/v1` (internal ingress) + `api.razorpay.com/v1` | `payouts/config/prod.toml:111-113`, `internalIngressHost` | Monolith | O |
| svc:payouts | svc:charge-collections | HTTPS + gRPC (`prod-charge-collections-service.razorpay.vpc`) | `payouts/config/prod.toml:634,641` `[ccsdk]` | Fees | O |
| svc:payouts | svc:elasticsearch (payout index) | ES | `payouts/config/prod.toml:734-753` `[elasticsearch]`, `[es_indexes].payouts` | Reporting/search | O |
| svc:payouts | NEW:shadow-gateway | HTTPS | `payouts/config/prod.toml:857` `[shadow_gateway]` | Testing | O |

### 1.3 Payouts service — SQS queues that name OTHER services (prod)

`payouts/config/prod.toml:163-188` `[job]` — every queue name string:

| queue name | direction | other service in the name | domain | O/I |
|---|---|---|---|---|
| `prod-api-payout-source-updater-live` | producer (`source_updater`) | **api** monolith owns the consumer | Source fan-out | O |
| `prod-api-payout-service-dual-write-direct-push-live` | producer | **api** monolith | Dual-write / migration | O |
| `prod-x-balances-payout-event` | producer (`x_balances_payouts_event`) | x-balances | Balances (in graph) | O |
| `prod-x-balances-balance-refresh-live` | producer | x-balances | Balances | O |
| `prod-x-account-statement-source-event` | producer (`x_account_statement_source_event`) | x-account-statements | Statements (in graph) | O |
| `prod-payouts-rbl-banking-account-statement-live` | consumer | RBL bank statement feed | Current account | O |
| `prod-payouts-fmp-check-live` / `prod-payouts-fmp-initiate-live` | both | **Fund Management Payouts** (`internal/app/fundManagement`) | **Treasury / fund mgmt** | O |
| `prod-payouts-payout-usage-event-processing-live` | producer | usage/billing events | Pricing/usage | O |
| `prod-payouts-partner-bank-on-hold-payouts-live` | both | partner-bank hold | Current account | O |

### 1.4 Payouts Kafka (prod)

- `payouts/config/prod.toml:312-338` `[kafka_consumers]` — consumes `rx-fts-status-update-events` (group `rx-payouts-fts-status-update-consumer-group`) and `rx-fts-status-update-retry-events`; brokers `prod-noncde-kafka.razorpay.com:9090`. **Producer of `rx-fts-status-update-events` is FTS** — `fts/config/env.prod-live.toml:315-331` `[kafka_producers.fire_transfer_status] topic="rx-fts-status-update-events"` (O).
- `payouts/config/prod.toml:362-363` `[topics] fts_status_updates_retry = "rx-fts-status-update-retry-events"` (payouts is the producer of the retry topic).

### 1.5 Payouts — internal-app allowlists (who is allowed to originate payouts). Inbound, but each names a domain.

`payouts/internal/auth/headers.go:11-25` `var allowedInternalApps` — 13 apps:
`xpayroll`, `vendor_payments`, `payout_links`, `scrooge`, `settlements_service`, `capital_collections_client`,
`charge_collections_internal`, `fts`, `xperience`, **`accounts_receivable`**, **`business_reporting`**,
**`cross_border_import_service`**, **`capital_early_settlements`**. (O)

`payouts/internal/app/common/appConstants/constants.go:89-103` app-name consts (adds `accounting_integrations`, `batch`).
`payouts/internal/app/common/appConstants/constants.go:598-619` `IsInternalApp()` — a *different*, 10-item list
(includes `accounting_integrations`, `batch`; omits `accounts_receivable`, `business_reporting`,
`cross_border_import_service`, `capital_early_settlements`). Two divergent allowlists (O).

`payouts/internal/app/contact/type.go:16-21` `internalAppToAllowedInternalContact` — which internal app may pay
which internal contact type: `vendor_payments -> rzp_tax_pay`, `capital_collections_client -> rzp_capital_collections`,
`charge_collections_internal -> rzp_charge_collections`, `xpayroll -> rzp_xpayroll` (O).
`payouts/internal/app/contact/type.go:23-28` `internalContactTypes` = `rzp_fees, rzp_tax_pay,
rzp_capital_collections, rzp_charge_collections, rzp_xpayroll`.

`payouts/internal/app/payouts/validation.go:239-247` — `settlements_service` and `cross_border_import_service`
get `MaxSettlementPayoutLimit` instead of the normal max payout limit (O). Settlements/cross-border are the only
two apps with a raised amount ceiling.

### 1.6 Payouts — purpose / workflow-skip enums that encode other domains

`payouts/internal/app/payoutPurpose/constants.go:4-18` purposes: `refund, cashback, salary, utility bill,
vendor bill, vendor advance, petty cash, payout, rzp_fees, rzp_tax_pay, rzp_charge_collections,
RZP Fund Management, settlement, rzp_fund_management, ica_transfer` (O).
`:21-32` `DefaultPurposeTypeMap`, `:34-38` `InternalPurposeTypeMap` (rzp_fees / rzp_tax_pay /
rzp_charge_collections are internal-only purposes).

`payouts/internal/app/payouts/workflow_feature.go:3-54` workflow-skip features, one per calling domain:
`skip_for_pg_payout`, `skip_for_internal_payout`, `payout_workflows`, `skip_workflow_for_api`,
**`skip_wf_for_payroll`**, `skip_wf_at_payouts`, `skip_wf_for_payout_link`, **`skip_wf_for_petty_cash`**
(Go const `SKIP_WF_FOR_XPERIENCE`), `bulk_payout_workflow`; `WorkflowFeatureToInt` 1..9 (O).
Applied in `payouts/internal/app/payouts/processor/baseHelper.go:140-145` (payout-links),
`:147-151` (xperience/petty cash), `:155-159` (batch bulk), `:185-191` (`app == xpayroll` AND merchant feature
`skip_wf_for_payroll` -> skip maker-checker entirely) (O).
Note also `payouts/internal/app/common/appConstants/features.go:50` `hide_rx_payroll_payouts` — payroll payouts
are hidden from the X dashboard listing (O).

`payouts/internal/app/common/appConstants/constants.go:60-72` **channels**: `axis, yesbank, icici, rbl, citi,
m2p, rzpx, ocbc, amz_pay, idfc, slice, JPMC_INTL_BANK_TRANSFER` — `m2p` and `slice` are card-program partner
banks, `JPMC_INTL_BANK_TRANSFER` is the cross-border channel (O).
`:78-86` fund-account types `bank_account, vpa, wallet, wallet_account` (+ card public name) (O).
`:594-595` `IECRequiredPurposeCodes = ["P0103","P0807","P0102","P0109","P1505"]` — RBI OPGSP purpose codes, i.e.
cross-border remittance compliance lives inside payouts (O).

---

## 2. FTS — every product, auth user, and downstream webhook (the widest FTS fan-out)

### 2.1 FTS `product` enum -> webhook target (prod)

`fts/internal/product/product.go:15-25` consts, `:27-37` `products` slice, `:65-92` `GetProductConfig()` switch.
`fts/internal/config/webhook.go:7-18` `type Webhook` struct — 10 toml keys.

| FTS product | prod webhook URL | owning service | domain | O/I |
|---|---|---|---|---|
| `SETTLEMENT` | `https://settlements-live.razorpay.com/twirp/rzp.settlements.transfer.v1.TransferService/StatusUpdate` | **settlements service (Twirp)** | **Settlements** | O |
| `CUSTOMER_PAYOUT` | `https://api.razorpaywallet.com/v1/update_fts_fund_transfer` | **RazorpayWallet** (separate product/domain, own TLD) | **Wallet** | O |
| `PAYOUT` | `https://prod-api-int.razorpay.com/v1/update_fts_fund_transfer` | api monolith | Payouts | O |
| `PAYOUT_REFUND` | same monolith endpoint | api monolith | Payouts | O |
| `REFUND` | same monolith endpoint | api monolith -> Scrooge | **Refunds/PG** | O |
| `PENNY_TESTING` | same monolith endpoint | api monolith (FAV) | Beneficiary validation | O |
| `ES_ON_DEMAND` | same monolith endpoint | api monolith | **Capital early settlement** | O |
| `CA_PAYOUT` | same monolith endpoint | api monolith | **Current account** | O |
| `CUSTOMER_WALLET` | same monolith endpoint | api monolith | Wallet | O |
| `batch` (`batch_status`) | `https://prod-api-int.razorpay.com/v1/payouts/2fa/batch/fts_status` | api monolith batch/2FA | Batch | O |
| `payout.channel_status` / `payout_refund.channel_status` | `https://prod-api-int.razorpay.com/v1/fts/channel/notify` | api monolith | Downtime | O |

Evidence: `fts/config/env.prod-live.toml:165-232` `[webhook.*]`; `fts/config/env.default.toml:597-706`
(dev shows `[webhook.customer_payout.transfer_status].URL = "https://wallet.dev.razorpay.in/v1/update_fts_fund_transfer"`
at `:637-641`, confirming the wallet service identity).

### 2.2 FTS basic-auth users (who calls FTS)

`fts/config/env.default.toml:5505-5537` `[users]` — `api`, `alert`, `scrooge`, `settlement`, `art`,
**`capitalcards`**, `validate_vpa_internal_merchant`, `xperinece` [sic], `validx`, `ps`, **`wallet`** (O).
So FTS is directly called by: api monolith, alerting, **Scrooge (refunds)**, **Settlements**, ART
(automated recon/testing), **Capital Cards**, ValidX, PS (payments service), **Wallet**, xperience.

### 2.3 FTS other outbound

- `fts/config/env.default.toml:591-595` `[beam] BASE_URL = "https://beam.razorpay.com/push"` — bulk file push to banks (O).
- `fts/config/env.default.toml:9063-9068` `[relay]` — polls Relay service (`POLL_DATA_FROM_RELAY_SERVICE = true`, `fts/config/env.default.toml:5552`) (O).
- `fts/config/env.default.toml:710-712` `[lumberjack]` — analytics/event sink (O).
- `fts/config/env.prod-live.toml:333-334` `[test_transactions_config] url = "https://prod-api-int.razorpay.com/v1/payouts_internal"` — FTS creates test payouts back through the monolith's internal payout API (O).
- `fts/config/env.default.toml:6924-6925` `[payroll_mid_details] MERCHANT_LIST = ["E9Tn8ZMAG3w0Ul"]` — a hard-coded **payroll merchant id** given special routing (O).
- `fts/config/env.default.toml:8467-8474` `[restricted_routing]` header comment: "Currently used for internal **Settlements, Capital and PG** merchants ... payouts need to be made from PG accounts only" (O) — direct evidence that Settlements/Capital/PG share the FTS routing layer with X payouts.

---

## 3. Workflows — the shared approval platform (12 callers, only 2 of which are in the graph)

`workflows/config/prod.toml:185-221` `[auth.*]` — services allowed to CALL workflows:
`api`, **`payouts`**, `relay`, **`growth`**, `payout_links`, `splitz`, `vendor_payments`, `xperience`,
**`abacus`**, **`vendor_experience`**, **`offers_engine`**, **`payments_bank_transfer`** (O).

`workflows/config/prod.toml:128-183` `[clients.*]` — services workflows CALLS BACK (callback/approval-executed):
`rx_live`/`rx_test` (`https://api.razorpay.com/v1`), `payouts_live`/`payouts_test`
(`https://payouts.razorpay.com`), `relay` (`https://relay.razorpay.com/v1`), **`growth`**
(`https://growth.razorpay.com`), `splitz`, `payout_links` (`https://payout-links-ms-internal.razorpay.com`),
`vendor_payments` (`https://vendor-payments.razorpay.com`), `xperience` (`https://xperience.int.razorpay.com`),
**`abacus`** (`https://abacus.razorpay.com`), `dcs`, **`vendor_experience`**
(`https://vendor-experience.razorpay.com`), **`offers_engine`** (`https://offers-engine-live.razorpay.com`) (O).

`workflows/internal/constants/constants.go:36-40` workflow **config types** — the approvable entity kinds:
`payout-approval`, **`purchase-order-approval`**, **`goods-received-note-approval`**,
`vendor-payment-v2-approval`, **`vendor-onboarding-approval`** (O).
`workflows/internal/client/manager/helper/factory.go:16-28` `GetWorkflowClient()` — two engine types
`approval` and `asl` (Amazon States Language state machine) (`constants.go:30-31`).
`workflows/internal/client/types/approval/sample_configs/` — `payout_approval_1.json`,
`payout_approval_with_owner_action.json`, `vendor_payment_approval.json`, `vendor_payment_multiple_approval.json` (O).
Entity type is a free-form `VARCHAR(255)` column (`workflows/internal/database/migrations/20200712201722_create_workflows.go:21`,
`workflows/internal/dto/workflow_model.go:25`), so the set of approvable domains is open-ended (O).

| source | target | mechanism | evidence | domain | O/I |
|---|---|---|---|---|---|
| svc:workflow | NEW:abacus | HTTPS callback, basic auth `workflows` | `workflows/config/prod.toml:169-172` `[clients.abacus]` | **Pricing / billing** (name) | O host, I domain |
| svc:workflow | NEW:offers-engine | HTTPS callback | `workflows/config/prod.toml:180-183` `[clients.offers_engine]` `offers-engine-live.razorpay.com` | **Offers/promotions** | O |
| svc:workflow | NEW:vendor-experience | HTTPS callback | `workflows/config/prod.toml:176-179`; repo `vendor-experience/` exists in clone root | **Vendor portal / procurement** | O |
| svc:workflow | NEW:growth | HTTPS callback | `workflows/config/prod.toml:149-152` | **Growth/CRM** | O |
| NEW:payments-bank-transfer | svc:workflow | inbound basic auth | `workflows/config/prod.toml:219-221` `[auth.paymentsBankTransfer]` | **PG bank transfer** | O |
| svc:workflow | svc:relay | HTTPS callback | `workflows/config/prod.toml:145-148` | Relay (stub node) | O |

---

## 4. CFA (Contacts & Fund Accounts) — callers and the CARD stack

`cfa/config/default.toml:14-46` `[Server.Auth.*]` — callers: `API`, `Cron`, `Admin`, **`XPerience`**,
`Payouts`, `Validx`, **`Wallet`**, `Dev` (O). Wallet is a caller of CFA but is only a stub node today.

CFA outbound (all `cfa/config/prod.toml`):

| source | target | mechanism | evidence | domain | O/I |
|---|---|---|---|---|---|
| svc:cfa | NEW:bin-service | HTTPS, key/secret | `cfa/config/prod.toml:67-72` `[BinService] BaseURL="https://bin-service.razorpay.com"` | **Cards** (card BIN lookup) | O |
| svc:cfa | NEW:token-service | HTTPS | `cfa/config/prod.toml:81-86` `[TokenService] BaseURL="https://tokens-live-int.razorpay.com"` | **Cards** (network tokenisation) | O |
| svc:cfa | svc:vault | HTTPS | `cfa/config/prod.toml:74-79` | Tokenisation | O |
| svc:cfa | NEW:wda-service | HTTPS | `cfa/config/prod.toml:101-103` | **Reporting / warehouse** | O |
| svc:cfa | svc:api-monolith | HTTPS `prod-api-int` | `cfa/config/prod.toml:88-92` `[APIService]` | Monolith | O |
| svc:cfa | svc:dcs | HTTPS, **username `payouts`** (shared credential) | `cfa/config/prod.toml:108-118` `[Dcs] username="payouts"` (section header `:113`) + the comment "CFA reuses the payouts DCS namespace and credentials" | Config | O |
| svc:cfa | q:sqs | SQS | `cfa/config/prod.toml:49-51` `[Job] ContactLazyLoad="prod-api-rx-contact-lazy-loading-live"`, `FundAccountLazyLoad="prod-api-rx-fund-account-lazy-loading-live"`; `:56-65` `[EventSystem.QueueConfigs]` dual-write queues `prod-api-rx-contact-dual-write-live`, `prod-api-rx-fund-account-dual-write-live` | Migration/dual-write with monolith | O |

**Cards is reachable from Payouts in 2 hops** (payouts -> cfa -> bin-service/token-service) — the fund-account
"card" concept: `payouts/internal/app/common/appConstants/constants.go:86` `CARD_PUBLIC_NAME = "Card"` (O).

---

## 5. x-balances / banking-accounts — accounts, wallets, cards, onboarding

`x-balances/config/prod.toml:45-73` `[Server.Auth.*]` callers: `API`, `Cron`, `Admin`, **`PS`** (payments
service), `BankingAccounts`, `Validx`, `VirtualAccount` (O). Note: **payouts is NOT an auth user of x-balances**;
payouts talks to it over SQS (`prod-x-balances-payout-event`) and via `[x_balances] host` reads.
`x-balances/config/prod.toml:75-90` -> banking-accounts; `:91-106` -> mozart; `:150-163` -> AccountService (ASV);
`:164-172` -> Ledger; `:178+` -> PayoutService (O).

**Capital Cards edge:** `x-balances/internal/merchant/service.go:2` doc comment "DCS operations (SubAccountRoles,
`block_va_payouts`, **capital cards eligibility**)"; `x-balances/pkg/log/trace.go:44-45`
`CreateSubBalanceCapitalCardsError` — "logged when DCS `RemoveCapitalCardsEligibility` fails";
`x-balances/internal/sub_balances/service.go:59` "injects the merchant service for SubAccountRoles and
**capital-cards DCS flags**" (O). => Creating an X sub-balance mutates a **Capital Cards** eligibility flag.

banking-accounts outbound (config/default.toml unless noted):

| source | target | mechanism | evidence | domain | O/I |
|---|---|---|---|---|---|
| svc:banking-accounts | NEW:mob (master-onboarding) | HTTPS | `banking-accounts/config/prod.toml:1-4` `[mob] host="https://master-onboarding.razorpay.com"` | **Shared merchant onboarding identity** | O |
| svc:banking-accounts | NEW:bvs (business verification) | HTTPS | `banking-accounts/config/default.toml:134-136` `[bvs]`; SDK repo `business-verification-service-sdk-go/` in clone root | **KYC/verification** | O |
| svc:banking-accounts | NEW:metro | gRPC `metro-grpc.razorpay.com` | `banking-accounts/config/prod.toml:6-8` `[metro]` | **Org-wide pub/sub bus** | O |
| svc:banking-accounts | NEW:zoho CRM | HTTPS `accounts.zoho.com` | `banking-accounts/config/prod.toml:54-56`, `default.toml:49-51` | **Sales/CRM** | O |
| svc:banking-accounts | NEW:hubspot | HTTPS `api.hubapi.com/contacts/v1` | `banking-accounts/config/default.toml:174-176` | **Sales/CRM** | O |
| svc:banking-accounts | NEW:segment | HTTPS `api.segment.io/v1` | `banking-accounts/config/default.toml:179-181` | **Product analytics** | O |
| svc:banking-accounts | NEW:ufh (file handling) | HTTPS | `banking-accounts/config/default.toml:35` `[ufh]` | Documents | O |
| svc:banking-accounts | k:events.banking-account.v0.live | Kafka producer | `banking-accounts/config/default.toml:184-196` `[events.KafkaProducerConfig] topic="events.banking-account.v0.live"` brokers `prod-noncde-kafka` | **Event stream (org-wide)** | O |
| svc:banking-accounts | NEW:partner-lms | HTTPS | `banking-accounts/internal/services/partner_lms_service.go`; monolith key `api/config/applications.php:1204` `bank_lms_banking_service_url` default `https://partner-lms.razorpay.com` | **Bank partner LMS portal** | O |
| svc:banking-accounts | NEW:slice (card-issuer bank) | Mozart | `banking-accounts/internal/enums/partner_bank.go:16` `Slice PartnerBank = "SLICE"`; `internal/enums/application_type.go:21` `SLICE_ACCOUNT_LINK_APPLICATION`; `internal/config/config.go:92,118` `Slice` struct | **Cards/neobank partner** | O |

`banking-accounts/internal/constants/constants.go:193-199` + `internal/enums/account_type.go:13,40-41`
`AccountType` = `current` \| **`rx_wallet`** — the Wallet product is a first-class account type inside the
banking-account service (O). `banking-accounts/config/prod.toml:111-113` `[features] onboard_merchant_to_ps = true`
— banking-accounts onboards merchants into **PS (payments service)** (O).

---

## 6. Ledger — the largest single leak out of the graph (36 client identities, 2 BUs)

Ledger is a **multi-tenant** platform. Payouts is one of only **three** clients on the X tenant.

`ledger/internal/common/constant.go:351-358` tenants: `X`, `PG`, `PG_US`, `PG_SG`, `TEST`, `X_MY` (O).
`ledger/internal/common/constant.go:886` — "A BU is considered as a Tenant... ex - X, PG, **Capital** etc." (O).
`ledger/internal/boot/hooks/headers.go:15,42` — tenant is carried on the `Ledger-Tenant` HTTP header, not in the
URL; caller identity is the HTTP Basic-Auth username (O).

`ledger/config/prod-live.toml:174-281` `[auth.*]` — **36 calling services**, of which only `payouts`, `fts`,
`x_balances` are current graph nodes:
`api_key`, `dash_key`, `fts_key`, `pg_router_key`, `payouts_key`, `x_balances_key`, **`settlements_key`**,
**`scrooge_key`**, **`capital_collections_key`**, `disputes_key`, `pgos_key`, **`growth_key`**, `cps_key`,
`ups_key`, `nbplus_key`, `charge_collections_key`, `pcp_key`, `partnerships_key`, `optimizer_key`, `care_key`,
`route_key`, `emandate_key`, **`cross_border_import_key`**, **`cross_border_experience_key`**,
**`capital_es_key`**, `affordability_key`, **`payments_bank_transfer_key`**, `batch_key`, `dispute_service_key`,
**`offers_engine_key`**, `mes_key`, `apm_key`, **`wallet_key`**, `catalyst_key`, **`bill_payments_key`** (O).
Wired at `ledger/internal/boot/handler.go:505-548` (`initCommonConfig` -> `authUsernameToClientConfig`) and
`:552-600` (`twirpHooks()` -> `hooks.Auth(creds...)`) (O).
`ledger/internal/common/constant.go:1094-1096` X-DB clients (`fts`, `payouts`, `x_balances`);
`:1101-1139` PG-DB clients (the other ~35) (O).

`ledger/internal/common/constant.go:221-322` **`fund_account_type` -> product** — Ledger's account taxonomy is
the cleanest map of adjacent products:
`merchant_va`, `merchant_va_vendor`, `nodal` (X/Payouts) · `merchant_balance`, `merchant_fee_credits`,
`merchant_refund_credits` (PG) · `merchant_plan_principal/interest/charges`, `vendor_plan_*`, `capital_gateway`
(**Capital**) · `customer_wallet`, `customer_refund` (**Wallet**) · `settlement` (**Settlements**) ·
`offers_pool`, `offers_bank_cash`, `offers_brand_cash` (**Offers**) · `merchant_bbps_balance`, `bbps_pool`,
`merchant_bbps_convenience_fee`, `bbps_expense` (**BBPS / Bill payments**) · `merchant_da`, `vendor_da`,
`gst_da`, `merchant_cash_da` (**Digital Accounts / GST**) (O).
`ledger/internal/journal/config.go:26-84` — `updateBalanceModeAsyncConfigForX` vs
`updateBalanceModeSyncConfigForPG` (X is async, PG is sync) (O).

### Ledger event streams

| topic / queue | direction | other service | evidence | O/I |
|---|---|---|---|---|
| `arn:aws:sns:ap-south-1:141592612890:prod-ledger-x-journal-created-live` | producer (outbox) | **consumer not found in clone root** | `ledger/config/prod-live.toml:434-450` `[snsOutboxer.pubSub.journal_created.sns]`; also sync path `:452-474` `[pubSub.journal_created.sns]`; `ledger/internal/journal/core.go:1521-1567` `WriteJournalCreatedJobToOutbox`/`publishJournalCreatedEventToSNS`; key `ledger/internal/journal/config.go:16` `JournalCreatedSNSTopic` | O topic / **U consumer** |
| `events.ledger.v2.live` | producer (Kafka/MSK) | org-wide event bus | `ledger/config/prod-live.toml:289-299` `[events.kafka]` | O |
| `journal_create_<client>_pg_kafka` × 18 | consumer | settlements, api, cps, growth, ups, pcp, emandate, optimizer, route, sync-web, nbs, affordability, cb-import, bts, offers, dispute-service, partnerships, apm | `ledger/internal/common/constant.go:1010-1028`; mapping `ledger/internal/common/config.go:41-83` `kafkaTopicToClientConfig` | O |
| `MakeshiftTxnWorkerQueueCLSAck*` / `...ES*` / `...DLQ*` | consumer | per-client CLS-ack + ES-recon queues | `ledger/internal/common/constant.go:1033-1054` | O |
| `prod-ledger-journal-create-live`, `-account-create-`, `-balance-update-`, `-balance-update-pg-`, `-entry-details-create-`, `-entry-details-create-pg-` | self | internal work queues | `ledger/config/prod-live.toml:393-399` `[job]` | O |

Twirp surface (no tenant in path; all callers share it):
`rzp.ledger.account.v1.AccountAPI`, `rzp.ledger.account_detail.v1.AccountDetailAPI`,
`rzp.ledger.journal.v1.JournalAPI`, `rzp.ledger.ledger_entry.v1.LedgerEntryAPI`,
`rzp.ledger.ledger_config.v1.LedgerConfigAPI`, `rzp.ledger.dashboard.v1.DashboardAPI`
(`ledger/rpc/ledger/*/v1/*.twirp.go` `*PathPrefix`; protos `ledger/proto/platform/ledger/*/v1/*.proto:5,9`) (O).
Cross-service protos vendored into ledger: `ledger/proto/platform/art/dual_write/v1/dual_write.proto:4`
`package rzp.art.dual_write.v1` (**ART**) and `ledger/proto/platform/nss/transaction/v1/transaction.proto:4`
`package rzp.nss.transaction.v1` (**NSS**) (O) — two more services not in the graph.

---

## 7. Webhooks (stork) — payout.* is one of 31 event families

`stork/internal/webhook/event_constants.go:5-7` products: `PRIMARY`, `BANKING`, **`ISSUING`** (O).
Event-name prefixes present in that file (O):
`account, agent_studio, banking_accounts, beneficiary, bill_payment, customer, engage, escrow_pool,
fund_account, giftcard, invoice, issuing, kyc, load, order, payment, payment_link, payout, payout_link,
product, qr_code, refund, settlement, shiprocket, subscription, terminal, token, transaction, transfer,
virtual_account, withdrawal, zapier`.

Directly adjacent to Payouts on the same webhook plane:
- `:132-138` BANKING-only: `transaction.created/updated`, `payout.queued`, `payout.failed`,
  `payout.downtime.started/resolved`, `payout.creation.failed`.
- `:149-155` PRIMARY+BANKING: `fund_account.validation.completed/failed`, `payout.processed`, `payout.reversed`,
  `payout.initiated`, `payout.updated`, `payout.rejected`.
- `:42` `settlement.processed` (PRIMARY) — **Settlements**.
- `:127-130` `bill_payment.*` — **BBPS/Bill payments**.
- `:159-192` ISSUING family: `load.*`, `withdrawal.*`, `kyc.*`, `beneficiary.*`, `issuing.transaction.created`,
  `issuing.mandate.*`, `issuing.transfer.*`, `issuing.fastag.*` — **Cards / Issuing / FASTag**, and note it reuses
  the same `beneficiary.*` names Payouts' FAV flow uses.

Payouts' own stork identity: `payouts/config/default.toml:156-159` `[stork] serviceName = "rx-live"`,
`[stork.sms] namespace = "razorpayx_payouts_core"` (O).

---

## 8. Shared config plane (DCS + config-proto) — cross-domain feature flags

`config-proto/rzp/` top-level BU dirs: **`capital`**, **`digital_billing`**, `domain_hosting`, `engage`,
`in_store_payments`, `nocode`, **`pg`**, `platform`, `unclaimed`, `x` (O).
`config-proto/rzp/x/merchant/` domains: `account_statement`, `account_validation`, `accounting`, `banking`,
`dashboard_experience`, **`issuing`**, `onboarding`, `payouts`, `validx`, `vendor_experience`, `workflows` (O).

Cross-domain protos that Payouts owns or reads:
| proto | message / field | domain | O/I |
|---|---|---|---|
| `config-proto/rzp/x/merchant/payouts/cards.proto:6-20` | `message Cards { enable_non_saved_cards; enable_payouts_to_cards }`, "Owner Application: payouts" | **Cards** (payout to card, RBI tokenisation) | O |
| `config-proto/rzp/x/merchant/payouts/payroll_payouts.proto:9-15` | `message PayrollPayouts { hide_rx_payroll_payouts }` | **Payroll** | O |
| `config-proto/rzp/x/merchant/account_validation/payroll_validation.proto` | payroll bene validation | **Payroll** | O |
| `config-proto/rzp/x/merchant/issuing/wallet/features.proto` | issuing wallet features | **Cards/Issuing + Wallet** | O |
| `config-proto/rzp/x/merchant/onboarding/pricing_tiers.proto` | pricing tiers | **Pricing** | O |
| `config-proto/rzp/x/merchant/onboarding/fee_recovery.proto` | fee recovery | **Pricing/fees** | O |
| `config-proto/rzp/x/merchant/onboarding/collectx/eligibility.proto` | CollectX eligibility | **Collections** | O |
| `config-proto/rzp/x/merchant/dashboard_experience/budgets/budgets_petty_cash.proto` | petty-cash budgets | **Petty cash / expense mgmt** | O |
| `config-proto/rzp/x/merchant/accounting/integration_settings.proto` | GAI `sync_payouts` | Accounting | O |
| `config-proto/rzp/x/merchant/vendor_experience/vendor_experience.proto` | vendor portal | **Vendor experience** | O |
| `config-proto/rzp/x/merchant/payouts/business_wallet.proto` | business wallet | **Wallet** | O |
| `config-proto/rzp/x/merchant/payouts/fund_loading.proto` | fund loading | **Wallet/prepaid load** | O |

`payouts/pkg/dcs/features/features.go:11-25` DCS storage keys (payouts reads
`rzp/x/merchant/accounting/IntegrationSettings`, i.e. **another domain's** config namespace) and `:28-73`
feature names including `skip_workflow_for_payroll`, `sync_payouts`, `assume_sub_account`,
`in_flight_reservation_enabled` (O).
`payouts/pkg/sourceupdater/publisher.go:7` imports
`github.com/razorpay/config-proto/rpc/go/rzp/x/merchant/accounting` — the payouts source-updater is compiled
against the **accounting** domain's proto (O).

---

## 9. Accounting-integrations — the accounting/reporting leg

| source | target | mechanism | evidence | domain | O/I |
|---|---|---|---|---|---|
| svc:accounting-integrations | NEW:reporting-service | HTTPS `https://reporting.razorpay.com/v1`, key `accounting_integrations` | `accounting-integrations/config/prod.toml:351-360` `[ReportingConfig]` (+ 5 `*ReportConfigId` fields) | **Reporting** | O |
| svc:accounting-integrations | NEW:Zoho Books (external SaaS) | OAuth, redirect `https://api.razorpay.com/v1/direct/accounting-integrations/callback` | `accounting-integrations/config/prod.toml:146-152` `[Zoho]`; queue `:378-392` `prod-zoho-invoice-fetch` | **External accounting SaaS** | O |
| svc:accounting-integrations | NEW:Tally (external) | client | `accounting-integrations/config/prod.toml:187-188` `[Tally]` | **External accounting SaaS** | O |
| svc:vendor-payments | svc:accounting-integrations | **Kafka** `prod.x.vendor-payments.accounting-payouts.status-update` | `accounting-integrations/config/prod.toml:190-204` `[VendorPaymentUpdateListener]` (Driver="kafka") | Accounting | O |
| svc:accounting-integrations | q:kafka | consumer of `prod-vendor-advance-event`, `prod-file-handler-event`, `prod-items-update-event`, `prod-vendor-entity-updates-event`, `prod-initiate-report`, `prod-zoho-invoice-fetch` | `accounting-integrations/config/prod.toml:214-392` (six `*Listener` blocks) | Accounting/procurement | O |
| svc:accounting-integrations | svc:vendor-experience | HTTPS | `accounting-integrations/config/prod.toml:321-326` `[VendorExperienceClientConfig]` | Vendor portal | O |
| svc:accounting-integrations | NEW:ufh | HTTPS | `accounting-integrations/config/prod.toml:292-296` `[UfhConfig]` | Files | O |

`accounting-integrations/config/prod.toml:86-114` `[auth.*]` callers: `api`, `vendor_payments`,
**`businessReporting`**, `api_admin`, `api_direct`, `api_internal`, `gai_batch`, `custom_fields`, `gai_cron` (O).
`accounting-integrations/internal/boot/constants.go:4` `RxPayoutUpdatesKafkaTopic = "rx-payout-updates-kafka-topic"`
— a *Kafka* payout-updates path alongside the SNS one (O). Kafka topic `stage-rx-payout-updates` is declared in
`kube-manifests/stage/kafka-entity-razorpayx/values.yaml:129,338` and `kube-manifests/stage/kafka-entity/values.yaml:160,5673` (O).

---

## 10. Event-stream inventory (every topic/queue/ARN string found), producer/consumer marked

### SNS (all built as `arn:aws:sns:<region>:<acct>:<topic>` by `payouts/pkg/sns/sns.go:83-87` `getTopicArn`)

| topic | producer | consumer(s) | evidence |
|---|---|---|---|
| `payout-updates-xpayroll` | payouts | XPayroll/Opfin | `payouts/config/prod.toml:369` |
| `payout-updates-payout-links` | payouts | payout-links | `:370` |
| `payout-updates-settlements` | payouts | settlements | `:371` |
| `payout-updates-refunds` | payouts | Scrooge | `:372` |
| `payout-updates-vendor-payments` | payouts | vendor-payments (vendor_payments, tax_payments, vendor_settlements, vendor_advance) | `:375-378` |
| `payout-updates-charge-collections` | payouts | charge-collections | `:380` |
| `payout-updates-capital-collections` | payouts | capital-collections | `:381` |
| `payout-updates-cross-border` | payouts | cross-border-import-service (cross_border, ica_transfer) | `:384-385` |
| `payout-updates-petty-cash` | payouts | xperience | `:387` |
| `payout-updates-generic-accounting` | payouts | accounting-integrations | `:388` |
| `arn:aws:sns:ap-south-1:141592612890:prod-ledger-x-journal-created-live` | ledger | **UNKNOWN — no consumer found in clone root** | `ledger/config/prod-live.toml:439,456` |

### Kafka

| topic | producer | consumer | evidence |
|---|---|---|---|
| `rx-fts-status-update-events` | fts | payouts | `fts/config/env.prod-live.toml:321`; `payouts/config/prod.toml:322` |
| `rx-fts-status-update-retry-events` | payouts | payouts | `payouts/config/prod.toml:335,363` |
| `events.payout-event.v2.live` | payouts (datalake) | data platform | `payouts/pkg/events/event_details.go:13` `EventTopicPayoutPropertiesEvent` |
| `events.ledger.v2.live` | ledger | data platform | `ledger/config/prod-live.toml:289-299` |
| `events.banking-account.v0.live` | banking-accounts | data platform | `banking-accounts/config/default.toml:184-196` |
| `prod.x.vendor-payments.accounting-payouts.status-update` | vendor-payments | accounting-integrations | `accounting-integrations/config/prod.toml:196,204` |
| `prod-vendor-advance-event`, `prod-file-handler-event`, `prod-items-update-event`, `prod-vendor-entity-updates-event`, `prod-initiate-report`, `prod-zoho-invoice-fetch` | vendor-payments / ufh / reporting / zoho | accounting-integrations | `accounting-integrations/config/prod.toml:220,244,268,303,333,384` |
| `journal_create_<client>_pg_kafka` × 18 | 18 PG-tenant services | ledger | `ledger/internal/common/constant.go:1010-1028` |
| `stage-rx-payout-updates` / `rx-payout-updates-kafka-topic` | payouts (planned/parallel) | accounting-integrations | `accounting-integrations/internal/boot/constants.go:4`; `kube-manifests/stage/kafka-entity-razorpayx/values.yaml:129,338` |
| `mysql_cdc_events_prod_rx_account_statements_account_statements` / `..._accounts` | x-account-statements (Debezium CDC) | dual-write consumer | `x-account-statements/config/prod.toml:86,105-106` |

### SQS (cross-service queues only)

`payouts/config/prod.toml:163-188` (see §1.3), plus:
`x-account-statements/config/prod.toml:99-113` `[Job]` — 13 queues, per partner bank:
`prod-x-account-statements-fetch-sqs`, `-fetch-ybl-sqs`, `-fetch-idfc-sqs`, `-fetch-axis-sqs`,
`-fetch-icici-sqs`, **`-fetch-slice-sqs`**, `-save-and-enrich-sqs`, `-enrichment-sqs`,
`prod-x-account-statement-source-event` (shared with payouts), `prod-x-account-statements-migration`,
`prod-x-account-statements-account-activation-event` (O).
`cfa/config/prod.toml:49-65` — `prod-api-rx-contact-lazy-loading-live`, `prod-api-rx-fund-account-lazy-loading-live`,
`prod-api-rx-contact-dual-write-live`, `prod-api-rx-fund-account-dual-write-live` (O).
`ledger/config/prod-live.toml:393-399` — 6 internal ledger queues (O).

---

## 11. Concrete cross-domain paths from Payouts (hop-by-hop, file-cited)

The monolith's per-internal-app route allowlist (`api/app/Http/Route.php`, `$internalAppRoutes`) is the
authoritative "which domain may enter Payouts" table. Relevant entries (O):
`xpayroll` `:18626-18638` · `settlements_service` `:18863-18883` · `capital_collections_client` `:18477-18496` ·
`capital_early_settlements` `:18506+` · `scrooge` `:18556+` · `vendor_payments` `:13150+` · `loc` `:18497-18504`.
Internal payout entry points: `api/app/Http/Route.php:2313` `payout_create_internal` -> `POST /v1/payouts_internal`
-> `PayoutController@postFundAccountPayout`; `:2314` `payout_create_internal_direct`; `:2339`
`payout_update_tax_payment_id` -> `PATCH /v1/payouts_internal/{id}/tax-payment-id`; `:2420-2421`
`payout_approve_internal`/`payout_reject_internal`.

### P1 — PAYROLL (XPayroll / Opfin)
1. `api/app/Http/Route.php:18626-18638` app `xpayroll` is allowed `payout_create_internal`,
   `payout_create_on_internal_contact`, `payout_create_2FA_internal`, `contact_create_internal`,
   `fund_account_create_internal`, `user_details_for_payroll`, **`tax_payments_internal_icici_action`**.
2. Contact type gate: `payouts/internal/app/contact/type.go:20` `"xpayroll": {appConstants.XPayroll}` where
   `appConstants.XPayroll = "rzp_xpayroll"` (`constants.go:116`); enforced by
   `ValidateInternalAppAllowedToCreatePayoutsOnType` (`type.go:35-48`) and
   `ValidateContactTypeForPayouts` (`type.go:50-87`, sets `payout.SetInternal(true)`).
3. Maker-checker bypass: `payouts/internal/app/payouts/processor/baseHelper.go:185-191` —
   `app == appConstants.X_PAYROLL` (`"xpayroll"`) AND merchant feature `skip_wf_for_payroll`
   (`appConstants/features.go:30`, DCS name `skip_workflow_for_payroll` `pkg/dcs/features/features.go:47`)
   -> `SetWorkflowFeature(SKIP_WF_FOR_PAYROLL)` and workflow is **not** created.
4. FTS routing: `fts/config/env.default.toml:6924-6925` `[payroll_mid_details] MERCHANT_LIST=["E9Tn8ZMAG3w0Ul"]`
   gives the payroll MID dedicated handling.
5. Status back: `payouts/config/prod.toml:369` `xpayroll = "payout-updates-xpayroll"` SNS ->
   `api/app/Models/Payout/SourceUpdater/XPayrollUpdater.php:15-17` `app['xpayroll']->pushPayoutStatusUpdate()` ->
   `api/app/Services/XPayroll/Service.php:26,54-57` `POST {OPFIN_SERVICE_URL}/v2/api/merchant-payout-status`
   (payload built at `Service.php:66-90`: id, amount, status, utr, mode, channel, fees, tax, failure_reason).
   Config: `api/config/applications.php:215-220` (`OPFIN_SERVICE_URL`, `OPFIN_SERVICE_SECRET`).
6. Dashboard visibility: `config-proto/rzp/x/merchant/payouts/payroll_payouts.proto:9-15`
   `hide_rx_payroll_payouts` / `payouts/internal/app/common/appConstants/features.go:50`.
Target repo NOT in clone root: **Opfin / XPayroll** (name from `OPFIN_SERVICE_URL` env key and
`api/app/Services/XPayroll/Service.php` namespace). Also `/v2/api/validate-source-account` (TPV) at
`Service.php:28` — payroll validates the RazorpayX source account.

### P2 — CARDS / CAPITAL CARDS (corp card)
1. Fund-account type: `payouts/internal/app/common/appConstants/constants.go:86` `CARD_PUBLIC_NAME = "Card"`;
   feature `config-proto/rzp/x/merchant/payouts/cards.proto:6-20`
   `Cards { enable_non_saved_cards, enable_payouts_to_cards }`, "Owner Application: payouts".
2. Card data path: payouts -> `svc:cfa` (`payouts/config/prod.toml:691-692` `[cfa] host`) -> cfa ->
   `cfa/config/prod.toml:67-72` `[BinService]` (BIN lookup) and `:81-86` `[TokenService]`
   (`tokens-live-int.razorpay.com`, network tokenisation) — Cards is **2 hops** from Payouts.
3. Corp-card as a banking account: `api/app/Models/BankingAccount/AccountType.php:11` `const CORP_CARD =
   "corp_card"`; `api/app/Http/Route.php:5248` `corp_card_banking_account_create` ->
   `POST merchant/onboardCCCForBanking` -> `CorpCardController@onboardCapitalCorpCardForRzpX`, exposed to the
   internal app `capital_cards_client` (`Route.php:18463-18471`); guard
   `api/app/Http/BasicAuth/BasicAuth.php:2192` `getInternalApp() === 'capital_cards_client'`.
4. Capital-cards client: `api/app/Services/CapitalCardsClient.php:19-21`
   `GET {APP_CAPITAL_CARDS_URL}/v1/vendorpayment/account_details` (`GET_CORP_CARD_ACCOUNT_DETAILS_ENDPOINT`);
   config `api/config/applications.php:1075-1080`.
5. Vendor payment paid by corp card: `api/app/Models/FundAccount/Core.php:2010-2031`
   `isVendorPaymentViaCorporateCardEnabled()` (splitz `app.vendor_payment_via_corp_card_experiment_id`).
6. X-Balances side effect: `x-balances/internal/sub_balances/service.go:59` +
   `x-balances/pkg/log/trace.go:44-45` `CreateSubBalanceCapitalCardsError` / DCS
   `RemoveCapitalCardsEligibility` — creating an X sub-balance removes capital-cards eligibility.
7. FTS is called directly by capital cards: `fts/config/env.default.toml:5522-5524` `[users.capitalcards]`.
8. Card program partner banks appear as payout channels: `constants.go:66,70` `M2P`, `SLICE`.
Target repos NOT in clone root: **capital-cards** (`APP_CAPITAL_CARDS_URL`), **bin-service**, **token-service**.

### P3 — SETTLEMENTS
1. Settlements creates payouts: `api/app/Http/Route.php:18863-18883` app `settlements_service` allowed
   `payout_create_internal`, `payout_create_2FA_internal`, `payout_fetch_by_id_internal`.
2. Raised limit: `payouts/internal/app/payouts/validation.go:239-247` — `settlements_service` gets
   `MaxSettlementPayoutLimit` instead of the normal cap.
3. Auth: `payouts/config/prod.toml:106-108` `[auth.settlements]`; allowlist
   `payouts/internal/auth/headers.go:16` `SETTLEMENTS_SERVICE`.
4. Purpose: `payouts/internal/app/payoutPurpose/constants.go:16` `Settlement = "settlement"`.
5. FTS treats settlement as its own product: `fts/internal/product/product.go:16` `Settlement = "SETTLEMENT"`;
   webhook `fts/config/env.prod-live.toml:171-175` ->
   `https://settlements-live.razorpay.com/twirp/rzp.settlements.transfer.v1.TransferService/StatusUpdate`;
   FTS auth user `fts/config/env.default.toml:5514-5516` `[users.settlement]`;
   `fts/config/env.default.toml:8467-8474` restricted routing for "internal Settlements, Capital and PG merchants".
6. Payout status back: `payouts/config/prod.toml:371` `settlements = "payout-updates-settlements"` ->
   `api/app/Models/Payout/SourceUpdater/SettlementsUpdater.php:14-16` -> `api/app/Services/Settlements/Payout.php:14`
   `POST {SETTLEMENTS_LIVE_URL}/twirp/rzp.settlements.transfer.v1.TransferService/StatusUpdatePayout`.
7. Ledger: `ledger/config/prod-live.toml:194` `settlements_key`; fund account type
   `ledger/internal/common/constant.go:266` `FundAccountTypeSettlement = "settlement"`;
   Kafka `journal_create_settlements_pg_kafka` (`constant.go:1011`).
Target repo NOT in clone root: **settlements** (Twirp `rzp.settlements.transfer.v1.TransferService`,
`SETTLEMENTS_LIVE_URL`, `api/config/applications.php:696-712`). Currently only a stub node.

### P4 — CAPITAL: collections, LOC, early settlement
1. `capital_collections_client` creates payouts on an internal contact:
   `api/app/Http/Route.php:18477-18496` — allowed `contact_create_internal`, `fund_account_create_internal`,
   `payout_create_on_internal_contact`, `merchant_balance_create`, `credit_repayment_transaction_create`,
   `capital_transaction_create`, `capital_multiple_transaction_create`.
2. Contact-type gate: `payouts/internal/app/contact/type.go:18`
   `"capital_collections_client": {appConstants.CapitalCollections}` where `CapitalCollections =
   "rzp_capital_collections"` (`constants.go:114`); allowlist `payouts/internal/auth/headers.go:17`.
3. Status back: `payouts/config/prod.toml:381` `capital_collections = "payout-updates-capital-collections"` ->
   `api/app/Models/Payout/SourceUpdater/CapitalCollectionsUpdater.php:14-16` ->
   `api/app/Services/CapitalCollectionsClient.php:20,80-83`
   `POST {APP_CAPITAL_COLLECTIONS_URL}/v1/repayments/payout-webhook` (header `X-Auth-Type: direct`);
   config `api/config/applications.php:1082-1089`.
   Same client also has `:22` `v1/process_ondemand_settlement` (`LEDGER_IS_ENDPOINT`).
4. **LOC (line of credit)** is a distinct internal app: `api/app/Http/Route.php:18497-18504` `'loc' => [...]`.
   `capital_bnpl` at `api/config/applications.php:1090-1095`; `capital_early_settlements` app
   `api/app/Http/Route.php:18506` and `api/config/applications.php:397-399`; both are payouts
   internal-app allowlist members (`payouts/internal/auth/headers.go:24` `CAPITAL_EARLY_SETTLEMENTS`).
5. Ledger capital accounts: `ledger/internal/common/constant.go:247-255`
   `merchant_plan_principal/interest/charges`, `vendor_plan_*`, `capital_gateway`;
   auth `ledger/config/prod-live.toml:200` `capital_collections_key`, `:248` `capital_es_key`.
6. FTS `ES_ON_DEMAND` product (`fts/internal/product/product.go:21`) is the capital early-settlement rail.
Target repos NOT in clone root: **capital-collections**, **capital-bnpl**, **capital-early-settlement**, **loc**.

### P5 — WALLET (RazorpayWallet, `api.razorpaywallet.com`)
1. FTS product `CUSTOMER_PAYOUT` (`fts/internal/product/product.go:24`) posts status to
   `https://api.razorpaywallet.com/v1/update_fts_fund_transfer`
   (`fts/config/env.prod-live.toml:219-223`; dev proves the identity:
   `fts/config/env.default.toml:637-641` `https://wallet.dev.razorpay.in/v1/update_fts_fund_transfer`).
   `CUSTOMER_WALLET` (`product.go:23`) is the second wallet product.
2. Wallet is a direct FTS caller: `fts/config/env.default.toml:5535-5537` `[users.wallet]`.
3. Wallet is a direct CFA caller: `cfa/config/default.toml:39-42` `[Server.Auth.Wallet]` — it uses the same
   contact/fund-account store Payouts uses.
4. Wallet is a Ledger client with its own accounts: `ledger/config/prod-live.toml:273` `wallet_key`;
   `ledger/internal/common/constant.go:264-265` `customer_wallet`, `customer_refund`.
5. Wallet is an account type inside banking-accounts: `banking-accounts/internal/enums/account_type.go:13,40-41`
   `RX_WALLET`/`"rx_wallet"`; `banking-accounts/internal/constants/constants.go:197` `RxWallet`;
   monolith TODO `api/app/Models/BankingAccount/AccountType.php:13` "RX Wallet Payout Use Case".
6. Payouts already has wallet fund-account types: `constants.go:80-81` `WALLET`, `WALLET_ACCOUNT`; and a
   config-proto surface `config-proto/rzp/x/merchant/payouts/business_wallet.proto`,
   `.../payouts/fund_loading.proto`, `.../issuing/wallet/features.proto`.
Target repo NOT in clone root: **wallet** (`api.razorpaywallet.com`). Currently a stub node.

### P6 — ACCOUNTING (Generic Accounting Integration + external SaaS)
1. Vanilla payout with GAI on: `payouts/pkg/sourceupdater/publisher.go:98-107` Route 2 — only `processed`/
   `reversed`, gated by `isGAIEnabled()` (`:226-260`) which reads DCS
   `rzp/x/merchant/accounting/IntegrationSettings` field `sync_payouts`
   (`payouts/pkg/dcs/features/features.go:18,68-69`).
2. SNS `payout-updates-generic-accounting` (`payouts/config/prod.toml:388`) ->
   `api/app/Models/Payout/SourceUpdater/GenericAccountingUpdater.php:17,41-43`
   -> `app['accounting-integration-service']` -> `api/config/applications.php:1195-1201`
   (`ACCOUNTING_INTEGRATIONS_HOST_URL`).
3. accounting-integrations then fans out to external SaaS: `accounting-integrations/config/prod.toml:146-152`
   `[Zoho]` (OAuth redirect through `api.razorpay.com/v1/direct/accounting-integrations/callback`),
   `:187-188` `[Tally]`, and queue `:378-392` `prod-zoho-invoice-fetch`.
4. It also consumes vendor-payments' Kafka status stream: `accounting-integrations/config/prod.toml:190-204`
   topic `prod.x.vendor-payments.accounting-payouts.status-update`.

### P7 — REPORTING
1. `accounting-integrations/config/prod.toml:351-360` `[ReportingConfig] url =
   "https://reporting.razorpay.com/v1"`, key `accounting_integrations`, with
   `ItemReportConfigId`, `VendorReportConfigId`, `VendorAdvanceReportConfigId`, `InvoiceReportConfigId`,
   `InvoicePaymentReportConfigId` — five payout-adjacent report definitions.
2. Monolith reporting binding: `api/config/applications.php:834` `'reporting'`;
   scheduler type `api/app/Models/Schedule/Task/Type.php:12` `const REPORTING = 'reporting'; // Reporting Service`.
3. Payouts' own warehouse path: `payouts/config/prod.toml:726-732` `[wda]
   default_http_client_url="https://wda-service.razorpay.com"` with `db_namespace`/`db_cluster`
   (client `payouts/pkg/wda/`); CFA has the same at `cfa/config/prod.toml:101-103`.
4. `business_reporting` is an internal app allowed to originate payouts:
   `payouts/internal/auth/headers.go:22` `BUSINESS_REPORTING`; `api/config/applications.php:1187-1193`
   (`BUSINESS_REPORTING_HOST_URL`); it is also a caller of accounting-integrations
   (`accounting-integrations/config/prod.toml:94-96` `[auth.businessReporting]`).
5. Datalake stream: `payouts/pkg/events/event_details.go:13` `events.payout-event.v2.live`.
Target repos NOT in clone root: **reporting**, **business-reporting**, **wda-service**.

### P8 — CURRENT ACCOUNT (onboarding + statements)
1. FTS product `CA_PAYOUT` (`fts/internal/product/product.go:22`, webhook
   `fts/config/env.prod-live.toml:201-205`) — direct-current-account payouts are a separate FTS product from
   `PAYOUT`.
2. Payouts direct-account config: `config-proto/rzp/x/merchant/payouts/direct_accounts/configs.proto` and
   `.../payout_mode_config.proto`; DCS key `payouts/pkg/dcs/features/features.go:12`
   `rzp/x/merchant/payouts/direct_accounts/PayoutModeConfig`, `:24` `.../direct_accounts/Configs`
   (`in_flight_reservation_enabled`), features `:56-62` `rbl_ca_upi`, `queue_payout_bal_buffer`.
3. Statement ingestion: `payouts/config/prod.toml:176` queue
   `prod-payouts-rbl-banking-account-statement-live`; `x-account-statements/config/prod.toml:99-113`
   per-bank fetch queues (YBL, IDFC, Axis, ICICI, **Slice**).
4. Onboarding: `banking-accounts/config/prod.toml:1-4` `[mob] https://master-onboarding.razorpay.com`,
   `[bvs]`, `[metro]`, `[zoho]`, `[hubspot]`, `[segment]`;
   `banking-accounts/internal/enums/partner_bank.go:16` `SLICE`; `internal/enums/application_type.go:21`
   `SLICE_ACCOUNT_LINK_APPLICATION`; `banking-accounts/config/prod.toml:111-113`
   `[features] onboard_merchant_to_ps = true`.
5. Partner-bank LMS portal: `banking-accounts/internal/services/partner_lms_service.go`;
   `api/config/applications.php:1204` `https://partner-lms.razorpay.com`.
Target repos NOT in clone root: **master-onboarding (mob)**, **bvs**, **metro**, **partner-lms**, **ps**.

### P9 — CROSS-BORDER (discovered, not in the brief's list)
1. `payouts/internal/auth/headers.go:23` `CROSS_BORDER_IMPORT` (`"cross_border_import_service"`) is an internal
   app allowed to originate payouts, with the raised settlement limit
   (`payouts/internal/app/payouts/validation.go:239-247`).
2. Purpose `ica_transfer` (`payouts/internal/app/payoutPurpose/constants.go:18`), source types `cross_border` +
   `ica_transfer` sharing `payout-updates-cross-border` (`payouts/config/prod.toml:384-385`), updater
   `api/app/Models/Payout/SourceUpdater/CrossBorderICATransferUpdater.php:14-16`, config
   `api/config/applications.php:1817-1826` (`CROSS_BORDER_IMPORT_SERVICE_LIVE_URL`).
3. Compliance data inside payouts: `payouts/internal/app/common/appConstants/constants.go:594-595`
   `IECRequiredPurposeCodes` (RBI OPGSP codes) and channel `:72` `JPMC_INTL_BANK_TRANSFER`;
   FTS `fts/config/env.prod-live.toml:336-337` `[meta] RzpOpgspBeneFundAccountId`.
4. Ledger clients `cross_border_import_key`, `cross_border_experience_key`
   (`ledger/config/prod-live.toml:242,245`); Kafka `journal_create_cb_import_pg_kafka`
   (`ledger/internal/common/constant.go:1023`).
Target repos NOT in clone root: **cross-border-import-service**, **cross-border-experience**.

### P10 — REFUNDS / PG (Scrooge)
1. `scrooge` is in the payouts internal-app allowlist (`payouts/internal/auth/headers.go:15`), has a monolith
   route allowlist (`api/app/Http/Route.php:18556+`), and is an FTS auth user
   (`fts/config/env.default.toml:5511-5513` `[users.scrooge]`) and a Ledger client
   (`ledger/config/prod-live.toml:197` `scrooge_key`).
2. `refund` source type -> `payout-updates-refunds` (`payouts/config/prod.toml:372`) ->
   `api/app/Models/Payout/SourceUpdater/RefundsUpdater.php:14-16` -> `app['scrooge']` ->
   `api/config/applications.php:222-235` (`SCROOGE_URL`).
3. FTS product `REFUND` and `PAYOUT_REFUND` (`fts/internal/product/product.go:18-19`), purpose
   `refund`/`cashback` (`payouts/internal/app/payoutPurpose/constants.go:4-5,22-23`).
Target repo NOT in clone root: **scrooge**.

---

## 12. Vendor-payments — the Tax/TDS and procurement (S2P) sub-domains

### 12.1 Modules under `vendor-payments/internal/` (O)
`taxpayments` (TDS/GST remittance payouts), `directtaxpayments` (customer-initiated tax payment via PG
checkout — **not** payouts), `initiatetds` (Kafka consumer), `entitytax` (per-entity TDS metadata),
`tdscategory` (194C/194J section master), `inputtaxcredit` (GSTR-2A/2B input credit).

Key entry points (O):
`internal/taxpayments/core.go:1027` `InitiateMonthlyPayouts` · `:777` `CancelQueuedPayouts` ·
`:1929` `lockTaxPaymentAndCreatePayout` · `:1543` `payoutWorker` ·
`internal/taxpayments/internal_tax_contact.go:29,37,91` `ITaxContact`/`.FundAccount`/`.Create` (creates the
merchant's internal `rzp_tax_pay` contact + fund account used as payee) ·
`internal/taxpayments/entity_type.go:13` `BuildEntity`/`PayoutTdsEntityType` ·
`internal/taxpayments/adminactions.go:20` `AdminActions` (retry, manual challan upload, migrate tax FA,
disable auto-pay) · `internal/initiatetds/core.go:26,28` `HandleInitiateTdsJob` -> `taxpayments.InitiateTds`.
Cron-shaped admin RPCs (external scheduler, not in repo):
`internal/apiservice/TaxPaymentServer.go:123` `CancelQueuedPayoutCron`, `:131` `EmailCron`,
`:135` `InitiateMonthlyPayouts`, `:185` `AddPenaltyCron` (O).

### 12.2 How vendor-payments creates payouts — **monolith, not the Payouts service** (O)
`vendor-payments/internal/payout/core.go:82-86`:
`CreatePayoutPath = "v1/payouts_internal/"`, **`CreatePayoutOnInternalContact = "v1/internalContactPayout/"`**,
`CancelPayoutPath = "v1/payouts_internal/%s/cancel"`, `Create2FaPayoutPath = "v1/payouts/2fa/create_internal"`.
`internal/payout/core.go:138` `CreatePayoutOnInternalContact` -> `rxclient.GetCore().MakeCall(...)`;
`:109` `CreatePayout`. TDS call sites: `internal/taxpayments/core.go:2780` (create), `:876` (cancel).
Host: `internal/rxclient/core.go:113` `url = ApiConfig.Url + "/" + path`, wired at
`internal/boot/helpers.go:316` `rxclient.SetConfig(&config.AppConfig.Api)`;
`vendor-payments/config/prod.toml:123` `[api] url = "https://api-graphql.razorpay.com"`
(dev `config/default.toml:77` `http://api.razorpay.in`).
Tag-back: `internal/taxpayments/apicaller.go:21` `UpdateTaxPaymentIdOnPayout =
"v1/payouts_internal/%s/tax-payment-id"` — matches monolith route `api/app/Http/Route.php:2339`.
Also `internal/taxpayments/apicaller.go:20` `GetBankingAccount = "v1/banking_accounts_internal/"`,
`internal/fundaccount/core.go:35` `"v1/fund_accounts_internal/"`,
`internal/contact/apicaller.go:16` `"v1/contacts_internal/"`.

**Conclusion (O):** the Tax/TDS payout path goes API-monolith-only. It does not call `payouts.razorpay.com`.

### 12.3 `tax-compliance` client — NEGATIVE result (O)
Grep for `tax-compliance` / `taxcompliance` / `tax_compliance` / `hvault` across `vendor-payments/` returns
**zero matches**. The `tax-compliance-vault.razorpay.com` host is referenced only by **payouts**
(`payouts/config/prod.toml:718-724` `[hvault]`, namespace `razorpayx`, mount `secrets`). So the tax-compliance
vault is a Payouts-side dependency, not a vendor-payments one — a genuinely new edge into the Tax domain.

### 12.4 Direct-to-bank tax rails (O) — bypasses FTS entirely
`vendor-payments/config/default.toml:325-343` `[IciciConfig]`: `PaymentUrl`, `VerifyUrl`, `ChallanUrl`
(`/api/v1/directTaxTin2/paymentProcess|verification|debitAdvice` on
`apibankingone.icicibank.com` in prod), `GenerateTokenUrl`, plus `[IciciConfig.GstConfig]`, and hard-coded
`CorpId="ICICISHOPMALL"`, `UserId="TAXMAKER"`, `MajorHead="0021"`, `MinorHead="200"`, `ChallanNo="281"`,
`DebitAccount`, `DebitBankCode="ICI"`, an embedded `CertPem`. The monolith exposes
`tax_payments_internal_icici_action` -> `POST tax-payments/internal/icici_actions` ->
`TaxPaymentController@internalIciciAction` (`api/app/Http/Route.php:2673`) to the `xpayroll` app (`:18635`).
`internal/directtaxpayments/core.go:653,598,298` uses `paymentgateway.GetClient().CreateOrder/GetSettlements/
Refund` — DTP collects via the **PG checkout**, not payouts.

### 12.5 vendor-payments Kafka (O)
| topic | direction | evidence |
|---|---|---|
| `add-tds-entry` | **consumer** (+ self-republish on failure) | `config/prod.toml:336`, `config/default.toml:528`, `config/stage.toml:326` `[InitiateTdsConfig] Topic`; `internal/tasks/initiatetds.go:21` `RegisterTask`, `:44` handler, `:59` `producer.Publish` retry. **Producer not found in clone root — U.** |
| `prod.x.vendor-payments.accounting-payouts.status-update` | producer | `config/prod.toml:272-273` `VendorPaymentSourceUpdaterConfig.AIQueueName` -> consumed by accounting-integrations (`accounting-integrations/config/prod.toml:196,204`) |
| `prod-vendor-advance-event` | producer | `config/prod.toml:514` `VendorAdvanceUpdateConfig.QueueName` |
| `prod-items-update-event` | producer | `config/prod.toml:517` `ItemUpdateConfig.QueueName` |
| `prod-vendor-entity-updates-event` | producer | `config/prod.toml:549` `VendorEntityUpdatesConfig.QueueName` |
| `prod-tds`, `prod-email-int` | consumer | `config/prod.toml:270` `TdsTaskConfig.QueueName = "prod-tds"`; `internal/tasks/tds.go:50,188,192` |
| `prod-auto-invoice-processing`, `contact-entity-update-live`, `prod-email-send`, `prod-pdf-generate`, `prod-po-status-update`, `prod-grn-status-update`, `prod-vp-status-update`, `prod-elastic-data-ingestion`, `prod-fetch-gstr-2a`, `prod-vp-gstr-2b-recon`, `prod-fetch-gstr-2b`, `prod-fund-account-verification`, `prod-ocr` | mixed | `config/prod.toml` + `internal/tasks/*.go` `RegisterTask`; `internal/vendorfundaccounts/core.go:406` (FAV producer) |

No SNS topics anywhere in vendor-payments (grep negative) (O).

### 12.6 vendor-payments outbound HTTP — 20 external clients (`config/default.toml`, prod noted)

| target | host (prod) | domain | O/I |
|---|---|---|---|
| api monolith | `https://api-graphql.razorpay.com` (`prod.toml:123`) | Monolith | O |
| **ICICI direct tax** | `apibankingone.icicibank.com` (`default.toml:325-329`) | **Tax payments** | O |
| **Razorpay PG** | `[PaymentGateway]` `default.toml:350` | **PG / direct tax collect** | O |
| **MastersIndia** | `https://api-platform.mastersindia.co` (`default.toml:745`) | **GST data (3rd party)** | O |
| **Veryfi** | `https://api.veryfi.com/api/v8/partner/documents/` (`default.toml:598`) | **OCR (3rd party)** | O |
| **Sandbox.co.in** | `https://api.sandbox.co.in` (`default.toml:545`) | Tax API sandbox | O |
| **Abacus** | `https://abacus.razorpay.com` (`default.toml:569`) | **Risk/limits engine** | O |
| **Metro** | `https://metro-web.razorpay.com/` (`default.toml:211`) | **Orchestration/bulk** | O |
| **Reminders** | `https://reminders.razorpay.com` (`default.toml:433`) | Nudges/scheduling | O |
| **Gimli** | `default.toml:85` | Internal doc svc | O |
| **Mailgun** | `default.toml:90` | Email (3rd party) | O |
| **UFH** | `https://ufh.razorpay.com` (`default.toml:117`) | Files/challans | O |
| **accounting-integrations** | `https://accounting-integrations.int.razorpay.com` (`default.toml:752` `[CustomFieldClientConfig]`) | Accounting | O |
| vendor-experience | `https://vendor-experience.razorpay.com` (`default.toml:583`) | Vendor portal | O |
| xperience | `https://xperience.int.razorpay.com` (`default.toml:562`) | X BFF | O |
| workflows | `https://workflows.razorpay.com` (`default.toml:536`) | Approvals | O |
| stork | `https://stork.razorpay.com/` (`default.toml:514`); TDS alert mails at `internal/taxpayments/core.go:1313,1318,1350,1354` | Notifications | O |
| bvs | `https://bvs.razorpay.com` (`default.toml:455`) | Verification | O |
| mozart / splitz / batch / passport / authz / dcs / elasticsearch | `default.toml:439,497,576,590,593` + `prod.toml:618`, `default.toml:716` | Platform | O |

Dead config (not bound in `internal/config/config.go`): `[PayoutConfig] :264`, `[ContactConfig] :274`,
`[FundAccountConfig] :284`, `[MerchantConfig] :294`, `[MailConfig] :319` — all `127.0.0.1:8080` placeholders,
superseded by the generic `rxclient` (O).

### 12.7 Who calls vendor-payments (`internal/boot/helpers.go:187-269` `setupInternalAuthBasicCredentials`) (O)
`api`, `emailInt`, `metro`, `autoInvoiceProcessing`, `payoutLinks`, **`businessReporting`**,
**`capitalCards`** (`:244-249` — Capital Cards calls vendor-payments directly),
`accountingIntegrations` (`:252-257`), `workflows` (`:260-265`), `batch` (`:268-269`), plus
`config/default.toml:207-209` `[auth.vendorExperience]` gRPC creds.

### P11 — TAX PAYMENTS / TDS (concrete path)
1. External producer publishes to Kafka `add-tds-entry` (`vendor-payments/config/prod.toml:336`) ->
   `internal/tasks/initiatetds.go:21,44` -> `internal/initiatetds/core.go:26-28` `HandleInitiateTdsJob` ->
   `taxpayments.InitiateTds`.
2. Monthly remittance: `internal/apiservice/TaxPaymentServer.go:135` `InitiateMonthlyPayouts` ->
   `internal/taxpayments/core.go:1027` -> `:1929` `lockTaxPaymentAndCreatePayout` ->
   `:2780` `payout.GetCore().CreatePayoutOnInternalContact(...)`.
3. HTTP hop: `internal/payout/core.go:84,138` `POST {api}/v1/internalContactPayout/` (basic auth as
   internal app `vendor_payments`).
4. Payouts-side gate: `payouts/internal/app/contact/type.go:17`
   `"vendor_payments": {appConstants.TaxPayment}` where `TaxPayment = "rzp_tax_pay"`
   (`payouts/internal/app/common/appConstants/constants.go:113`); purpose
   `payouts/internal/app/payoutPurpose/constants.go:13,36` `RzpTaxPayment = "rzp_tax_pay"` (internal-only).
5. Tag-back: `internal/taxpayments/apicaller.go:21` `PATCH v1/payouts_internal/{id}/tax-payment-id`
   (monolith route `api/app/Http/Route.php:2339`).
6. Status return: `payouts/config/prod.toml:376` `tax_payments = "payout-updates-vendor-payments"` (shared
   topic) -> `api/app/Models/Payout/SourceUpdater/Factory.php:31` -> `VendorPaymentUpdater.php:14-16`.
7. Alternate rail (no payout at all): `vendor-payments/config/default.toml:325-329` `[IciciConfig]` direct
   ICICI `directTaxTin2` challan APIs; and `internal/directtaxpayments/core.go:298,598,653` via the PG.
8. Payouts' own tax-compliance secret store: `payouts/config/prod.toml:718-724`
   `[hvault] https://tax-compliance-vault.razorpay.com` namespace `razorpayx`.
Target service NOT in clone root: **tax-compliance-vault**; **producer of `add-tds-entry`**.

### P12 — VENDOR PAYMENTS / procurement (S2P)
1. Payout create: `vendor-payments/internal/payout/core.go:83,109` `POST {api}/v1/payouts_internal/`;
   allowlist `api/app/Http/Route.php:13150+` app `vendor_payments`;
   payouts-side `payouts/internal/auth/headers.go:13` `VENDOR_PAYMENTS`.
2. Approvals: vendor-payments -> `workflows` (`config/default.toml:536`) with config types
   `vendor-payment-v2-approval`, `purchase-order-approval`, `goods-received-note-approval`,
   `vendor-onboarding-approval` (`workflows/internal/constants/constants.go:36-40`) and callbacks back to
   `https://vendor-payments.razorpay.com` (`workflows/config/prod.toml:173-175`).
3. Status out: 4 source types share `payout-updates-vendor-payments`
   (`payouts/config/prod.toml:375-378`: `vendor_payments`, `tax_payments`, `vendor_settlements`,
   `vendor_advance`) -> `Factory.php:30-37` -> `VendorPaymentUpdater.php`.
4. To accounting: Kafka `prod.x.vendor-payments.accounting-payouts.status-update`
   (`vendor-payments/config/prod.toml:272-273` producer; `accounting-integrations/config/prod.toml:196,204` consumer)
   -> accounting-integrations -> `reporting.razorpay.com` / Zoho / Tally.
5. Paid-by-card variant: `api/app/Models/FundAccount/Core.php:2010-2031`
   `isVendorPaymentViaCorporateCardEnabled()`; Capital Cards is an auth caller of vendor-payments
   (`vendor-payments/internal/boot/helpers.go:244-249`); corp-card account details
   `api/app/Services/CapitalCardsClient.php:21` `v1/vendorpayment/account_details`.
6. Vendor portal: `vendor-experience` (`vendor-payments/config/default.toml:583`,
   `accounting-integrations/config/prod.toml:321-326`, `workflows/config/prod.toml:176-179`) — a three-way
   edge into a repo present in the clone root but **not in the graph**.

---

## 13. Candidate domain signals (ranked by evidence strength)

| # | Domain | Signal strength | Independent evidence lines |
|---|---|---|---|
| 1 | **Settlements** | **Very strong** | Own SNS topic + Twirp service name + FTS product + FTS auth user + Ledger client + Ledger fund-account type + raised payout limit in payouts code + restricted-routing comment. 8 independent hooks. Currently only a **stub node**. |
| 2 | **Capital (collections / LOC / BNPL / early settlement / cards)** | **Very strong** | 5 distinct internal apps (`capital_collections_client`, `loc`, `capital_bnpl`, `capital_early_settlements`, `capital_cards_client`), own SNS topic, own contact type `rzp_capital_collections`, 6 Ledger fund-account types, 2 Ledger auth keys, FTS auth user `capitalcards`, x-balances eligibility flag, `corp_card` banking-account type. |
| 3 | **Payroll (XPayroll/Opfin)** | **Strong** | Own SNS topic, own contact type, dedicated workflow-skip feature, dedicated FTS merchant list, dedicated dashboard-hide flag, dedicated config-proto message, 11-route monolith allowlist. One external repo (Opfin). |
| 4 | **Tax payments / TDS** | **Strong** | 6 modules in vendor-payments, own Kafka consumer topic, own contact type, own purpose, dedicated monolith route (`tax-payment-id`), a dedicated **secret vault host**, and a direct-to-ICICI rail bypassing FTS. |
| 5 | **Wallet (RazorpayWallet)** | **Strong** | Separate TLD `api.razorpaywallet.com`, 2 FTS products, FTS auth user, CFA auth user, Ledger client + 2 fund-account types, `rx_wallet` banking-account type, 3 config-protos. Currently a **stub node**. |
| 6 | **Cards / Issuing** | **Strong** | `enable_payouts_to_cards` proto owned by payouts, CFA -> bin-service + token-service, `corp_card` account type, `m2p`/`slice` channels, stork `ISSUING` product with 34 event names, capital-cards client. |
| 7 | **Cross-border (import / ICA transfer)** | **Strong** | 2 source types + shared SNS topic, own updater, internal-app allowlist entry, raised payout limit, RBI OPGSP purpose codes inside payouts, `JPMC_INTL_BANK_TRANSFER` channel, 2 Ledger clients. Not previously identified. |
| 8 | **Accounting + Reporting** | **Strong** | GAI SNS topic, DCS namespace crossover, accounting-integrations -> `reporting.razorpay.com` with 5 report ids, Zoho/Tally, `business_reporting` internal app, WDA warehouse client in both payouts and cfa. |
| 9 | **Vendor experience / procurement (S2P)** | **Medium-strong** | `purchase-order-approval` + `goods-received-note-approval` + `vendor-onboarding-approval` workflow types, `vendor-experience` repo present but not a node, 3 services call it. |
| 10 | **Refunds / PG (Scrooge)** | **Medium-strong** | `refund` source type + SNS topic, FTS `REFUND`/`PAYOUT_REFUND` products, FTS auth user, Ledger client, payouts internal-app allowlist. |
| 11 | **Current-account onboarding** | **Medium-strong** | banking-accounts -> mob/bvs/metro/zoho/hubspot/segment/partner-lms, FTS `CA_PAYOUT` product, per-bank statement queues, `direct_accounts` protos. Partly already in the graph via banking-accounts. |
| 12 | **Pricing / fees** | **Medium** | `abacus` (workflows + vendor-payments clients), `onboarding/pricing_tiers.proto`, `onboarding/fee_recovery.proto`, `rzp_fees` purpose, charge-collections (already a node), `payout_usage_event_processing` queue. Weakest of the "named" candidates — no dedicated repo found. |
| 13 | **Petty cash / expense management** | **Medium** | `petty_cash` source type + SNS topic + `PettyCashUpdater` -> xperience, `skip_wf_for_petty_cash` feature, `budgets_petty_cash.proto`. Runs inside xperience (already a node). |
| 14 | **Bill payments / BBPS** | **Medium** | 4 Ledger fund-account types, Ledger auth key `bill_payments_key`, stork `bill_payment.*` events. No direct payouts edge found. |
| 15 | **Offers / Growth** | **Weak-medium** | Ledger `offers_engine_key` + 3 fund-account types + `journal_create_offers_pg_kafka`; workflows client+auth for `offers_engine` and `growth`. No payouts edge. |

## 14. Probable repo/service names NOT in the clone root

| probable repo/service | where the name came from | likely domain |
|---|---|---|
| `opfin` / `xpayroll` | `OPFIN_SERVICE_URL`/`OPFIN_SERVICE_SECRET` (`api/config/applications.php:217-219`); PHP namespace `RZP\Services\XPayroll` | Payroll |
| `settlements` (Twirp `rzp.settlements.transfer.v1.TransferService`) | `fts/config/env.prod-live.toml:172`; `api/app/Services/Settlements/Payout.php:14`; `SETTLEMENTS_LIVE_URL` | Settlements |
| `scrooge` | `SCROOGE_URL` (`api/config/applications.php:224`); FTS `[users.scrooge]`; Ledger `scrooge_key` | Refunds |
| `capital-collections` | `APP_CAPITAL_COLLECTIONS_URL` (`api/config/applications.php:1083`) | Capital |
| `capital-cards` | `APP_CAPITAL_CARDS_URL` (`api/config/applications.php:1076`) | Cards |
| `capital-bnpl` | `APP_CAPITAL_BNPL_URL` (`api/config/applications.php:1091`) | Capital |
| `capital-early-settlement` | `CAPITAL_ES_PASSWORD` (`:398`); Ledger `capital_es_key` | Capital |
| `loc` | `api/app/Http/Route.php:18497` internal app `'loc'` | Capital LOC |
| `cross-border-import-service` | `CROSS_BORDER_IMPORT_SERVICE_LIVE_URL` (`api/config/applications.php:1822`) | Cross-border |
| `cross-border-experience` | Ledger `auth.crossBorderExperience` (`ledger/config/prod-live.toml:245`) | Cross-border |
| `wallet` (`api.razorpaywallet.com`) | `fts/config/env.prod-live.toml:220`; dev `wallet.dev.razorpay.in` `:639` | Wallet |
| `bin-service` | `cfa/config/prod.toml:68` | Cards |
| `token-service` (`tokens-live-int.razorpay.com`) | `cfa/config/prod.toml:82` | Cards |
| `tax-compliance-vault` | `payouts/config/prod.toml:719` `[hvault]` | Tax |
| `wda-service` | `payouts/config/prod.toml:727`, `cfa/config/prod.toml:102` | Reporting/warehouse |
| `reporting` | `accounting-integrations/config/prod.toml:352`; `api/config/applications.php:834` | Reporting |
| `business-reporting` | `BUSINESS_REPORTING_HOST_URL` (`api/config/applications.php:1188`) | Reporting |
| `accounts-receivable` | `ACCOUNTS_RECEIVABLE_HOST_URL` (`api/config/applications.php:1180`); payouts allowlist `headers.go:21` | AR/collections |
| `abacus` | `workflows/config/prod.toml:169-172`; `vendor-payments/config/default.toml:569` | Pricing/risk |
| `offers-engine` | `workflows/config/prod.toml:180-183`; Ledger `offers_engine_key` | Offers |
| `growth` | `workflows/config/prod.toml:149-152`; Ledger `growth_key` | Growth |
| `metro` | `banking-accounts/config/prod.toml:6-8` gRPC; `vendor-payments/config/default.toml:211` | Event bus / orchestration |
| `mob` (master-onboarding) | `banking-accounts/config/prod.toml:3` | Merchant identity |
| `bvs` | `banking-accounts/config/default.toml:134-136`; SDK repo `business-verification-service-sdk-go/` | KYC |
| `partner-lms` | `api/config/applications.php:1204`; `banking-accounts/internal/services/partner_lms_service.go` | Bank partner |
| `ps` (payments service) | `payouts/config/default.toml:578-581` `[configs.fts_request_from_ps]`; FTS `[users.ps]`; x-balances `[Server.Auth.PS]`; `banking-accounts/config/prod.toml:112` `onboard_merchant_to_ps` | PG |
| `pg-router`, `pgos`, `cps`, `nbplus`, `pcp`, `optimizer`, `care`, `route`, `emandate`, `affordability`, `payments-bank-transfer`, `dispute-service`, `disputes`, `partnerships`, `mes`, `apm`, `catalyst`, `bill-payments` | Ledger `[auth.*]` (`ledger/config/prod-live.toml:185-280`) + `ledger/internal/common/constant.go:1101-1139` `Client*` | PG platform |
| `art` (`rzp.art.dual_write.v1`), `nss` (`rzp.nss.transaction.v1`) | `ledger/proto/platform/art/dual_write/v1/dual_write.proto:4`; `.../nss/transaction/v1/transaction.proto:4`; FTS `[users.art]` | Recon / test automation |
| `shiprocket`, `zapier`, `agent_studio`, `engage`, `giftcard`, `escrow_pool` | stork event families (`stork/internal/webhook/event_constants.go`) | Partner/GTM |
| `Zoho Books`, `Tally`, `MastersIndia`, `Veryfi`, `Sandbox.co.in`, `Hubspot`, `Segment`, `Mailgun`, `ICICI directTaxTin2` | accounting-integrations + vendor-payments + banking-accounts configs | 3rd-party SaaS |

## 15. Unknowns / open questions

1. **U — consumer of `prod-ledger-x-journal-created-live`** (`ledger/config/prod-live.toml:439,456`). No
   subscriber found anywhere in the clone root. This is Ledger's only outbound event on the X tenant.
2. **U — producer of Kafka `add-tds-entry`** (`vendor-payments/config/prod.toml:336`). vendor-payments only
   consumes and self-republishes on failure (`internal/tasks/initiatetds.go:59`).
3. **U — who writes `payout_sources` rows with `source_type` in {`refund`, `charge_collections`,
   `vendor_settlements`, `vendor_advance`, `generic_accounting_integration`}**, since
   `api/app/Models/PayoutSource/Entity.php:47-56 $validSourceTypes` rejects them on create yet
   `Factory.php:28-85` dispatches on them and prod topics exist (`payouts/config/prod.toml:372-380`).
4. **U — the `cross_border` source type has a prod topic (`:384`) but no updater case** in
   `Factory.php`; only `ica_transfer` has one. Either dead config or handled by the Go service alone.
5. **U — subscription mechanism for the `payout-updates-*` SNS topics.** `payouts/config/default.toml:501-502`
   comment says "consumers subscribe via HTTP endpoints"; the actual SQS/HTTPS subscriptions are AWS-side and
   not in any repo. Whether the SNS path is live in prod or the monolith `SourceUpdater` synchronous path is
   still authoritative is not determinable from source (both exist).
6. **U — `abacus`'s actual domain.** Named as a client by both `workflows` and `vendor-payments`; described in
   neither. "Pricing/risk/limits" is INFERRED from the name and its co-location with approval flows.
7. **U — divergence between the two payouts internal-app allowlists**
   (`payouts/internal/auth/headers.go:11-25` = 13 apps vs
   `payouts/internal/app/common/appConstants/constants.go:602-612 IsInternalApp()` = 10 apps). Which one gates
   which code path was not traced.
8. **U — `irctc` auth user** in payouts (`payouts/config/prod.toml:107-109` `[auth.irctc]`) — an external
   partner identity with no other reference anywhere in the clone root.
9. **U — Kafka `rx-payout-updates-kafka-topic` / `stage-rx-payout-updates`**
   (`accounting-integrations/internal/boot/constants.go:4`; `kube-manifests/stage/kafka-entity*/values.yaml`) —
   a Kafka payout-updates path parallel to the SNS one; which is authoritative is unknown.
10. **U — the ~18 PG-tenant Ledger clients** (`cps`, `pcp`, `mes`, `apm`, `catalyst`, ...) are name-only;
    their repos and owning teams were not resolved (no CODEOWNERS visible for them in the clone root).
