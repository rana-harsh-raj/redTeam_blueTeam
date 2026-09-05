#!/usr/bin/env python3
"""Build the versioned Payouts domain graph (JSON / Mermaid / GraphML) from evidence-backed node+edge tables."""
import json, sys, html
from pathlib import Path

OUT = Path('/Users/rana.singh/rzp-payouts-architecture/reports')
SHA = {
 'payouts':'4bf3dbf9239feadea6d65ca90c893a988e116173','fts':'2a09e763116db47a2f28553c677ad73ef8133ebf',
 'ledger':'471ff4d5321b6b99a7965882d18f572a1adf5194','cfa':'d488558e162c08e9fa04dff005f27d846ce88ad2',
 'x-balances':'1a21c0f9111da14125d839dc0dfe8d980740f207','banking-accounts':'c3fc1fe8ae41a528a4a3d7ed1b0ec9dad6f98141',
 'xperience':'88729560e52b35aa9d5768d67ff807f847537ee7','x':'40b093f97ec1b8162e7f46ecee223af04982d5ba',
 'x-account-statements':'e73fd5ae271dea104cea71781f4b644e52d414c3','payout-links':'2c81a4d97f961794fa8a95ec8580311203460bdf',
 'vendor-payments':'20c4f4d59970471067388afea8b1d65ac39ee126','admin-dashboard':'e29bdf988410704c3053417e91051a0ec88cebbb',
 'ValidX':'40f181b6d874abece2ec5cdd88d6fd9e4bb533b3','charge-collections':'412816c70d569abd9a8220a4c320a994ceec2883',
 'virtual-account':'437f39807e1575c3b00b0ef57cdde804a54be5ce','frontend-x':'efe5df3d26ec5bfaef69b8cb66fb558674cd960f',
 'accounting-integrations':'fc13a0b2a38214374e80a16af3ed4465b2d84ef1','markdown-docs':'ab3bda5f11c308034b2089df532df1064db1df7c',
}

nodes = {}
edges = []

def N(id, type, label=None, repo=None, accessible=True, lifecycle='current', **props):
    nodes[id] = dict(id=id, type=type, label=label or id, repo=repo, accessible=accessible, lifecycle=lifecycle, **props)

def E(src, dst, etype, mechanism, evidence_type, repo, path, symbol='', sha=None, auth='', timeout_retry='', flags='', env='prod', slack='', confidence='confirmed', lifecycle='current', note=''):
    edges.append(dict(source=src, target=dst, type=etype, mechanism=mechanism, environment=env, auth=auth,
                      timeout_retry=timeout_retry, feature_flags=flags, evidence_type=evidence_type, repository=repo,
                      file_path=path, symbol=symbol, commit=sha or SHA.get(repo, 'n/a'), slack_or_doc_ref=slack,
                      confidence=confidence, lifecycle=lifecycle, note=note))

# ---------------- Repositories / services ----------------
N('repo:payouts','repository','razorpay/payouts',repo='payouts')
N('svc:payouts','service','Payouts Service (Go)',repo='payouts')
N('svc:payouts-workers','worker','Payouts SQS workers (cmd/workers)',repo='payouts')
N('svc:payouts-kafka','worker','Payouts Kafka consumer (cmd/kafkaConsumers)',repo='payouts')
N('repo:ledger','repository','razorpay/ledger',repo='ledger'); N('svc:ledger','service','Ledger Service (Twirp, tenant X)',repo='ledger')
N('repo:fts','repository','razorpay/fts',repo='fts'); N('svc:fts','service','FTS web',repo='fts'); N('svc:fts-workers','worker','FTS default-workers (machinery)',repo='fts')
N('repo:cfa','repository','razorpay/cfa',repo='cfa'); N('svc:cfa','service','CFA (gRPC+gateway)',repo='cfa')
N('repo:x-balances','repository','razorpay/x-balances',repo='x-balances'); N('svc:x-balances','service','X Balances',repo='x-balances'); N('svc:x-balances-workers','worker','x-balances per-bank balance-fetch workers',repo='x-balances')
N('repo:banking-accounts','repository','razorpay/banking-accounts',repo='banking-accounts'); N('svc:banking-accounts','service','Banking Account Service',repo='banking-accounts')
N('repo:xperience','repository','razorpay/xperience',repo='xperience'); N('svc:xperience','service','Xperience BFF',repo='xperience')
N('repo:x','repository','razorpay/x',repo='x'); N('fe:x','frontend','RazorpayX dashboard SPA (x.razorpay.com)',repo='x')
N('repo:xas','repository','razorpay/x-account-statements',repo='x-account-statements'); N('svc:xas','service','XAS account statements',repo='x-account-statements')
N('repo:validx','repository','razorpay/ValidX',repo='ValidX'); N('svc:validx','service','ValidX (FAV)',repo='ValidX')
N('repo:payout-links','repository','razorpay/payout-links',repo='payout-links'); N('svc:payout-links','service','Payout Links',repo='payout-links')
N('repo:vendor-payments','repository','razorpay/vendor-payments',repo='vendor-payments'); N('svc:vendor-payments','service','Vendor Payments',repo='vendor-payments')
N('repo:charge-collections','repository','razorpay/charge-collections',repo='charge-collections'); N('svc:charge-collections','service','Charge Collections (pricing)',repo='charge-collections')
N('repo:admin-dashboard','repository','razorpay/admin-dashboard',repo='admin-dashboard'); N('fe:admin-dashboard','frontend','Admin dashboard (internal)',repo='admin-dashboard')
N('repo:virtual-account','repository','razorpay/virtual-account',repo='virtual-account'); N('svc:virtual-account','service','Virtual Account',repo='virtual-account')
N('repo:frontend-x','repository','razorpay/frontend-x',repo='frontend-x'); N('fe:payout-links-customer','frontend','x-customer-payout-links (payee page)',repo='frontend-x')
N('repo:accounting-integrations','repository','razorpay/accounting-integrations',repo='accounting-integrations'); N('svc:accounting-integrations','service','Accounting Integrations',repo='accounting-integrations')
# Inaccessible
N('svc:api-monolith','service','API monolith (razorpay/api, PHP) — NOT ACCESSIBLE',repo='api',accessible=False)
N('svc:edge','gateway route','Edge / Kong (terraform-kong) — NOT ACCESSIBLE',repo='terraform-kong',accessible=False)
N('svc:authz','AuthZ policy','AuthZ service + goutils/authz enforcer — NOT ACCESSIBLE',repo='authz',accessible=False)
N('svc:stork','service','Stork webhooks/SMS/email — NOT ACCESSIBLE',repo='stork',accessible=False)
N('svc:mozart','service','Mozart bank gateway — NOT ACCESSIBLE',repo='mozart',accessible=False)
N('svc:dcs','configuration source','DCS dynamic config — NOT ACCESSIBLE',repo='dcs',accessible=False)
N('svc:splitz','configuration source','Splitz experiments — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:shield','service','Shield risk engine — NOT ACCESSIBLE',repo='shield',accessible=False)
N('svc:governor','service','Governor pricing rules — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:asv','service','Account Service (ASV gRPC) — NOT ACCESSIBLE',repo='asv',accessible=False)
N('svc:ups','service','UPI service (UPS) — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:vault','service','Vault (card tokens) / HVault (secrets) — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:workflow','service','Workflow service (approval engine) — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:batch','service','Batch service — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:recon','service','Recon / ART — NOT ACCESSIBLE',repo='recon',accessible=False)
N('svc:settlements','service','Settlements service — NOT ACCESSIBLE',repo='settlements',accessible=False)
N('svc:wallet','service','Wallet service — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:beam','service','Beam file relay — NOT ACCESSIBLE',repo='(unknown)',accessible=False)
N('svc:relay','configuration source','Relay config server',repo='relay')
N('ext:banks','external bank/provider','Partner banks via Mozart: ICICI, Axis, RBL, IDFC, Yes Bank, HDFC, Citi, M2P, MCS, OCBC, Slice, Amazon Pay, JPMC',accessible=False)
N('ext:merchant-webhook','webhook consumer','Merchant webhook endpoint',accessible=False)
N('ext:merchant-api-client','identity','Merchant API client (key_id/secret Basic Auth)',accessible=False)
N('id:dashboard-user','identity','Dashboard user (cookie session + CSRF; passport minted at edge)')
N('id:service-basic-auth','identity','Service-to-service Basic Auth credentials (per caller)')
N('role:finance-l1','role','finance_l1 (maker)'); N('role:finance-l2','role','finance_l2 (checker)'); N('role:finance-l3','role','finance_l3 (checker)'); N('role:owner-admin','role','owner/admin'); N('role:view-only','role','view_only')
# Datastores
N('db:payouts-mysql','datastore','Payouts MySQL (payouts, payout_details, idempotency_keys, reversals, workflow_state_map...)',repo='payouts')
N('db:payouts-tidb','datastore','TiDB (payouts_temp / analytical copy via WDA)',repo='payouts')
N('db:payouts-redis','datastore','Payouts Redis (idempotency mutex, in-flight reservations)',repo='payouts')
N('db:payouts-es','datastore','Payouts Elasticsearch (search index)',repo='payouts')
N('db:ledger-pg','datastore','Ledger Postgres rx (journal, accounts, ledger_entries, ledger_config, idempotency)',repo='ledger')
N('db:fts-mysql','datastore','FTS MySQL (transfers, attempts, transfer_meta, source_accounts, routing tables)',repo='fts')
N('db:fts-redis','datastore','FTS Redis (machinery broker, mutex)',repo='fts')
N('db:cfa-mongo','datastore','CFA MongoDB (contacts, fund_accounts, hash_lookup)',repo='cfa')
N('db:api-mysql','datastore','API monolith MySQL (balance, contacts, fund_accounts, bank_accounts; payouts table DELETED Aug-2026)',repo='api',accessible=False)
N('db:xbalances-mysql','datastore','x-balances MySQL (balance, sub_balance)',repo='x-balances')
N('db:bas-mysql','datastore','banking-accounts MySQL (banking_account: account_number, balance_id, fts_fund_account_id)',repo='banking-accounts')
N('db:xas-mysql','datastore','XAS MySQL (account_statements, accounts)',repo='x-account-statements')
# Queues / topics
N('q:sqs-webhook-event','queue/topic','SQS webhook_event',repo='payouts')
N('q:sqs-queued-payout','queue/topic','SQS queued_payout / schedule_payout / on_hold_payout',repo='payouts')
N('q:sqs-async-dual-write','queue/topic','SQS async_dual_write / data_consistency_checker',repo='payouts')
N('q:sqs-xbalances-refresh','queue/topic','SQS x_balances_balance_refresh (x-balances → payouts)',repo='payouts')
N('q:sqs-xas-source','queue/topic','SQS payout/reversal source events (payouts → XAS)',repo='x-account-statements')
N('q:kafka-fts-status','queue/topic','Kafka rx-fts-status-update-events (+ fts_status_updates_retry)',repo='fts')
N('q:kafka-tds','queue/topic','Kafka add-tds-entry',repo='payouts')
N('q:sns-source-updates','queue/topic','SNS source_update_topics (vendor_payments, payout_links, settlements, xpayroll, tax_payments...)',repo='payouts')
N('q:sns-journal-created','queue/topic','SNS journal-created (ledger)',repo='ledger')
N('q:sqs-ledger-balance-update','queue/topic','SQS ledger balance_update (async non-merchant accounts)',repo='ledger')
N('q:kafka-payout-cdc','queue/topic','Kafka payout-kafka-topic (CDC/Maxwell shape, consumed by charge-collections)',repo='charge-collections')
# Events
for ev in ['payout.pending','payout.rejected','payout.queued','payout.initiated','payout.processed','payout.updated','payout.reversed','payout.failed','payout.downtime.started','payout.downtime.resolved']:
    N('event:'+ev,'event',ev,repo='markdown-docs')
# Feature flags / config
N('flag:shadow_gateway.enabled','feature flag','shadow_gateway.enabled (prod=false)',repo='payouts')
N('flag:splitz:shadow-route','feature flag','Splitz per-route shadow-gateway experiment (devstack pilot TUnTsUB8Os2kX0)',repo='payouts')
N('flag:splitz:CreateTransferMetaRollout','feature flag','Splitz CreateTransferMetaRollout (FTS webhook → PS direct)',repo='fts')
N('flag:splitz:FireStatusUpdateKafka','feature flag','Splitz FireStatusUpdateKafka (FTS status via Kafka)',repo='fts')
N('flag:dcs:payout_workflows','feature flag','DCS payout_workflows / skip_approval_workflow_for_api / skip_workflow_for_* (merchant)',repo='dcs',accessible=False)
N('flag:dcs:in_flight_reservation_enabled','feature flag','DCS in_flight_reservation_enabled',repo='dcs',accessible=False)
N('flag:features.forward_dual_write','feature flag','features.forward_dual_write / reverse_dual_write / skip_ca_dual_write',repo='payouts')
N('flag:splitz:IKeyAutoEnforcementRampUp','feature flag','features.ikey_auto_enforcement + Splitz ramp-up',repo='payouts')
N('flag:merchant:payout_service_enabled','feature flag','merchant payout_service_enabled / is_payout_service',repo='api',accessible=False)
N('flag:kong:payouts-proxy-cutover','gateway route','Kong template payouts-proxy-cutover (shadow route, monolith default upstream)',repo='terraform-kong',accessible=False)
# Endpoints / handlers
N('ep:POST /v1/payouts','API endpoint','POST /v1/payouts (public; also payouts_internal, internal_contact_payout)',repo='payouts')
N('ep:POST /payouts_with_otp','API endpoint','POST /merchant/api/{mode}/payouts_with_otp (dashboard)',repo='x')
N('ep:POST /v1/payouts/{id}/approve','API endpoint','POST /payouts/{id}/approve|reject (+bulk, 2fa)',repo='x')
N('ep:POST /v1/payouts/{id}/cancel','API endpoint','POST /v1/payouts/{id}/cancel',repo='payouts')
N('ep:POST /v1/transfer','API endpoint','FTS POST /v1/transfer',repo='fts')
N('ep:Journal.Create','API endpoint','Ledger Twirp Journal.Create',repo='ledger')
N('ep:transfer_status_webhook','API endpoint','PS POST /v1/payouts/transfer_status_webhook',repo='payouts')
N('ep:update_fts_fund_transfer','API endpoint','API monolith /v1/update_fts_fund_transfer (legacy FTS webhook)',repo='api',accessible=False)
N('ep:update_payouts_with_fts','API endpoint','PS PATCH /v1/payouts/update_payouts_with_fts (internal)',repo='payouts')
N('ep:manual_action','admin action','PS POST /v1/payouts/manual_action (processed→processing, approve/reject workflow)',repo='payouts')
N('ep:admin-force-status','admin action','Admin PATCH live/payouts/{id}/manual/status (force status)',repo='admin-dashboard')
N('ep:cron','cron','PS /v1/cron/* (queued/scheduled/on_hold dispatch, reservation reconcile, dual-write failure, SLA monitor)',repo='payouts')
N('ep:fts-stuck-cron','cron','FTS stuck_payouts cron (canary instance, leader-elected)',repo='fts')
N('ep:xbalances-fetch-cron','cron','x-balances /v1/cron/balance/{channel}/fetch',repo='x-balances')
N('ep:xas-fetch','cron','XAS statement fetch jobs',repo='x-account-statements')
N('deploy:payouts','deployment','payouts Dockerfiles build/docker/prod/*, devspace.yaml; manifests external',repo='payouts')
N('deploy:fts','deployment','fts deployments/{prod,stage,func,perf}/deployment-web|default-worker.yaml',repo='fts')

# ---------------- Edges: entry & auth ----------------
E('id:dashboard-user','fe:x','calls','browser',' code','x','src/js/api/api.js','withCredentials/X-CSRF-TOKEN')
E('fe:x','svc:edge','calls','HTTPS to dashboard.razorpay.com /merchant/api/{mode}/...','code','x','src/js/api/api.js:173','config.url','','cookie session + CSRF')
E('svc:edge','svc:api-monolith','routes_to','Kong route → monolith (default upstream); dashboard authenticate (IP allowlist, merchant_users membership) runs in monolith','slack','terraform-kong','templates/payouts-proxy-cutover','',None,'basic-auth-x / upstream-jwt passport','','payouts-proxy-cutover shadow route','prod','#tech_infra_edge 2026-08-28; #platform_spine_edge_oncall thread p1787909324386769','probable','current')
E('svc:edge','svc:payouts','routes_to','Kong upstream-override → PS direct (pilot: GET /v1/payouts/schedule/timeslots)','slack+code','payouts','internal/routing/router/payout_routes.go:105-133','payoutDashboardGetRoutes',None,'ServiceBasicAuthOrPassport','', 'kong payouts-proxy-cutover; authz PR 2577','prod (shadow)','#x-payouts-reliability 2026-08-31','probable','migration')
E('ext:merchant-api-client','svc:edge','calls','HTTPS api.razorpay.com /v1/payouts (Basic Auth key:secret, X-Payout-Idempotency)','docs','markdown-docs','api/x/payouts/create/bank-account.md','',None,'Basic Auth')
E('svc:api-monolith','svc:payouts','calls','HTTP proxy to PS /v1/payouts* with service BasicAuth + X-Passport-JWT-V1 (legacy auth private/proxy/privilege)','code','payouts','internal/routing/router/payout_routes.go:17-103','BasicAuth+PassportAuthentication',None,'service BasicAuth (cred.API) + Passport JWT')
E('svc:payouts','svc:api-monolith','falls_back_to','shadow gateway proxy/shadow modes to monolith_base_url=https://prod-api-int.razorpay.com','code','payouts','internal/routing/shadowgateway/mode.go:58-117','resolveMode',None,'','', 'shadow_gateway.enabled=false in prod','prod','','confirmed','migration')
E('svc:authz','svc:payouts','authorizes','goutils/authz enforcer (Consul-synced policies; passport claims); PR 2577 pilot policies','slack','authz','PR #2577','',None,'passport roles','', 'banking::non_lms org enforcement unresolved','prod','#x-payouts-reliability 2026-08-31','probable','migration')
E('svc:authz','svc:xperience','authorizes','Casbin in-process enforcer org razorpayx origin x_platform','code','xperience','internal/interceptors/authz_interceptor.go:12-27','AuthzOrganisation')
E('svc:authz','svc:api-monolith','authorizes','dashboard role checks (finance_l1/l2/l3, view_only) — monolith side (inaccessible)','slack','api','','',None,'','','','prod','#x-production-issues 2026-06-16','inferred')
E('svc:payouts','svc:asv','calls','gRPC account service (merchant/account metadata, activation check being built)','code','payouts','config/default.toml:675-693','account_service',None,'Basic','30s, 3 retries')
E('fe:x','ep:POST /payouts_with_otp','calls','create single payout','code','x','src/js/api/outflow.js:83-90','',None,'cookie+CSRF+X-Payout-Idempotency(flagged)','','isIdempotencyForCreatePayoutsExpEnabled')
E('fe:x','ep:POST /v1/payouts/{id}/approve','calls','approve/reject (+OTP, bank 2FA)','code','x','src/js/api/outflow.js:155-194','',None,'cookie+CSRF','','',"prod","",'confirmed')
E('fe:x','svc:xperience','calls','/xperience(-edge)/bulk-payouts/* via dashboard host','code','x','src/js/api/bulkPayoutsApiSlice.ts:60-103','',None,'passport edgev1','','isXpsEdgeBulkPayoutsMigrationEnabled')
E('svc:xperience','svc:batch','calls','ProcessBatch / FetchBatch (bulk payouts)','code','xperience','internal/app/service/bulkpayouts/service.go:2887','initiateProcessing',None,'Basic','Hystrix')
E('svc:xperience','svc:api-monolith','calls','composite_payout_internal (petty cash) with X-Razorpay-Account','code','xperience','internal/app/client/apiservice/create_petty_cash_payout.go:18','CreatePettyCashPayout')
E('svc:xperience','svc:payouts','reads','PSClient GetPayoutByID','code','xperience','internal/app/client/payouts/api_caller.go','')
E('svc:xperience','svc:workflow','calls','WorkflowsClient (bulk approve/reject)','code','xperience','config/default.toml','WorkflowsClient.BaseUrl',None,'','','','prod','', 'probable')

# ---------------- Edges: payout creation controls ----------------
E('svc:payouts','db:payouts-redis','writes','idempotency mutex (idempotencyKey+merchantID, 20min)','code','payouts','internal/routing/middleware/idempotency_key.go:59-207','IdempotencyKey')
E('svc:payouts','db:payouts-mysql','writes','idempotency_keys UNIQUE(idempotency_key, merchant_id); payouts; payout_details','code','payouts','internal/database/migrations/20221004002554_create_idempotency_keys_table.go','')
E('flag:splitz:IKeyAutoEnforcementRampUp','svc:payouts','controlled_by','hard-fail on missing idempotency key only when flag+experiment on','code','payouts','internal/routing/middleware/idempotency_key.go:107','')
E('svc:payouts','svc:cfa','reads','GET fund account / contact (Basic Auth, ACL client Payouts)','code','payouts','config/default.toml:722-741','[cfa]',None,'Basic','10s, 3 retries')
E('svc:cfa','db:cfa-mongo','writes','contacts/fund_accounts (merchant_id filter = tenant isolation)','code','cfa','internal/fund_accounts/service.go:228-231','')
E('svc:cfa','db:api-mysql','reads','GetFundAccountById/GetContactById fallback; FundAccountsFetchMultiple MySQL-only','code','cfa','internal/config/config.go:37,88-93','APIStore',None,'','','EnableCFADB/EnableTiDB=false','prod','','confirmed','migration')
E('svc:cfa','svc:api-monolith','dual_writes','SQS 30s-delayed dual-write consumed by monolith worker','code','cfa','internal/eventsystem/subscribers/fund_account_dual_write.go','',None,'','','','prod','','probable','migration')
E('svc:validx','svc:cfa','calls','ValidX ACL client: Create/Update contact & fund account','code','cfa','internal/server/interceptors.go:67-79','acl')
E('svc:validx','svc:fts','calls','POST /v1/transfer penny drop (VALIDX auth)','code','fts','internal/constants/authusers.go','ValidxAuth')
E('svc:payouts','svc:validx','reads','FAV freshness gate (per ValidX tech-spec); PS has own /v1/fund_accounts/validations FAV subsystem','code+doc','ValidX','tech-spec.md:79','',None,'','','','prod','','probable')
E('svc:payouts','svc:shield','calls','risk rule evaluation → Action/TriggeredRules','code','payouts','pkg/shield/shield_payout_rule_evaluate.go:19-60','',None,'Basic','200ms, CB 30s')
E('svc:payouts','svc:governor','calls','fee rules via ccSdk','code','payouts','pkg/ccSdk/client.go:26-46','',None,'Basic','1000ms')
E('svc:payouts','svc:charge-collections','calls','fee calc via ccSdk charge_collections leg','code','payouts','config/default.toml:786-825','',None,'Basic','200ms')
E('svc:charge-collections','svc:api-monolith','calls','POST /v1/internalContactPayout (fee-recovery payout, queue_if_low_balance=true)','code','charge-collections','internal/charges/payouts_core.go:20-75','createPayout',None,'Basic payouts_key + X-Razorpay-Account')
E('svc:payouts','svc:dcs','reads','merchant feature flags (payout_workflows, skip_*; DCS namespace shared with CFA)','code','payouts','internal/config/config.go:66','DCSClientConfig')
E('flag:dcs:payout_workflows','svc:payouts','controlled_by','IsWorkflowApplicable decision','code+slack','payouts','internal/app/payouts/processor/baseHelper.go:107-155','IsWorkflowApplicable',None,'','','','prod','#x-production-issues recurring cache bug Jun–Aug 2026')
E('svc:payouts','svc:workflow','calls','workflow create/fetch (pkg/workflow; dev host = monolith stub)','code','payouts','pkg/workflow/workflow_create.go:93','',None,'Basic','100ms, 3 retries','', 'prod','','probable')
E('svc:payouts','svc:splitz','reads','experiments (client-side eval cache 5min)','code','payouts','config/default.toml:510-530','')
E('svc:payouts','svc:x-balances','reads','ListAccounts (source account selection / MAR); GET /v1/balances/:id','code','payouts','internal/app/balance/core.go:49-90','FetchActiveAccountsFromXBalance',None,'Basic','30s, 3 retries')
E('svc:x-balances','db:xbalances-mysql','writes','balance rows (polled)','code','x-balances','internal/database/model/balance.go:30-76','')
E('svc:x-balances','db:api-mysql','reads','fallback read of monolith balance table','code','x-balances','internal/balances/service.go:43-153','GetBalanceByID',None,'','','','prod','','confirmed','migration')
E('svc:x-balances','svc:ledger','reads','real-time balance for sub_balance/shared','code','x-balances','internal/enrichment/enricher.go:35-98','EnrichBatch',None,'Basic x_balances_key','10s')
E('svc:x-balances-workers','svc:mozart','polls','bank balance fetch per channel','code','x-balances','internal/balance_fetch/service.go:53-62','')
E('svc:x-balances-workers','svc:banking-accounts','reads','FetchBankingCreds','code','x-balances','internal/gateway/banking_accounts/interfaces.go','')
E('svc:x-balances','q:sqs-xbalances-refresh','publishes','BalanceRefreshEvent after gateway fetch (flag in_flight_reservation_enabled)','code','x-balances','internal/balance_fetch/service.go:63-73','')
E('q:sqs-xbalances-refresh','svc:payouts-workers','consumes','releases in-flight reservation gate','code','payouts','internal/job/x_balances_balance_refresh.go:18-45','')
E('svc:banking-accounts','svc:api-monolith','calls','legacy balance creation POST v1/bas/merchant/{id}/banking_accounts (RBL/ICICI/Axis/IDFC/Yes)','code','banking-accounts','internal/applications/onboarding/balance_creation.go:29-49','',None,'','','','prod','','confirmed','migration')
E('svc:banking-accounts','svc:x-balances','calls','POST v1/balance/create (Slice only)','code','banking-accounts','pkg/balances/client.go:38-53','')
E('svc:banking-accounts','db:bas-mysql','writes','banking_account (balance_id, fts_fund_account_id, credentials)','code','banking-accounts','internal/database/entities/banking_account.go:12-32','')

# ---------------- Edges: ledger ----------------
E('svc:payouts','ep:Journal.Create','calls','sync Twirp via ledger-sdk, tenant X: payout_initiated on created; payout_processed; payout_reversed; payout_failed','code','payouts','internal/app/payouts/status.go:52-64; pkg/ledger/ledger_journal_create.go:87-101','',None,'Basic auth.payouts','200ms, 3 retries, Hystrix CB 10s')
E('ep:Journal.Create','db:ledger-pg','writes','journal + ledger_entries + atomic UPDATE accounts SET balance WHERE balance+inc>=min_balance (insufficient ⇒ reject)','code','ledger','internal/account/repo.go:421','UpdateBalance')
E('svc:ledger','svc:payouts','authorizes','ErrInsufficientBalanceFailure at payout_initiated = balance authorization','code','ledger','internal/account/repo.go:421','')
E('svc:ledger','q:sqs-ledger-balance-update','publishes','async balance update for VendorPayable/Commission/Nodal/GST (not MerchantBalance)','code','ledger','internal/journal/config.go:27','updateBalanceModeAsyncConfigForX')
E('svc:ledger','q:sns-journal-created','publishes','journal-created for payout_initiated/failed/reversed (not processed)','code','ledger','internal/journal/config.go:87-99','')
E('svc:fts','svc:ledger','calls','Twirp AccountAPI/CreateOnEvent (ledger account create)','code','fts','internal/tasks/ledger_account_create.go','',None,'Basic auth.fts')

# ---------------- Edges: FTS ----------------
E('svc:payouts','ep:POST /v1/transfer','calls','transfer initiate (X-Origin: payouts, X-Razorpay-MerchantId); idempotent on (source_id,source_type)','code','payouts','pkg/fts/transfer_init_create.go:23; pkg/fts/base.go:37-82','',None,'Basic users.ps','1000ms, 3 retries, Hystrix')
E('svc:api-monolith','ep:POST /v1/transfer','calls','legacy transfer initiate (API auth) — wallet/settlements/IRCTC remain','code+slack','fts','internal/constants/authusers.go','APIAuth',None,'Basic users.api','','','prod','#curlec-payouts-migration 2026-06-12','confirmed','legacy')
E('ep:POST /v1/transfer','db:fts-mysql','writes','transfers/attempts/transfer_meta(origin_service defaults to API if X-Origin absent)','code','fts','internal/transfer/service.go:1889-1910','extraTransferMetaFromCtx')
E('svc:fts-workers','svc:mozart','calls','transfer_init/transfer_status/gateway_auth… (8 generic ops, Basic, 180s timeout)','code','fts','internal/providers/mozart/executor.go:17-42','',None,'Basic MOZART_AUTH','180s per op; per-bank backoff tables')
E('svc:mozart','ext:banks','calls','bank-specific protocol (REST/SOAP/SFTP) — inside Mozart (inaccessible)','code','fts','config/env.default.toml [integration.api.*]','',None,'bank creds/certs (metadata only)','','','prod','','probable')
E('svc:fts-workers','ext:banks','calls','direct integrations: Axis SFTP, ICICI OPGSP, RBL multicurrency, JPMC via Beam','code','fts','internal/integrations/axis/axis.go:143','',None,'SFTP','','','prod','','probable')
E('svc:fts-workers','svc:mozart','polls','check_transfer_status / verify_transfer_status tasks (self-republishing)','code','fts','internal/constants/worker.go','')
E('svc:fts-workers','ep:transfer_status_webhook','calls','fire_transfer_status_webhook → PS direct when origin_service==payouts && CreateTransferMetaRollout','code','fts','internal/transfer/service.go:1126-1208','FireTransferStatusWebhook',None,'Basic payouts_service.auth','3 retries × 200s then give up (log+metric, no DLQ)','CreateTransferMetaRollout','prod','#x-techsupport 2026-07-15 thread; incident pout_TVcGW8Kil6WIjj 2026-09-03')
E('svc:fts-workers','ep:update_fts_fund_transfer','calls','legacy webhook to API monolith /v1/update_fts_fund_transfer (default when X-Origin absent or rollout off)','code','fts','internal/transfer/service.go:1126-1208; config/env.prod-live.toml','',None,'Basic productConfig.Auth','3 retries × 200s','CreateTransferMetaRollout=off','prod','','confirmed','legacy/migration')
E('ep:update_fts_fund_transfer','ep:update_payouts_with_fts','calls','monolith relays status to PS (PATCH /v1/payouts/update_payouts_with_fts) — monolith side inferred','code','payouts','internal/routing/router/payout_internal_routes.go:27-35','HandlePayoutStatusUpdateViaFTS',None,'Basic cred.API','','','prod','#x-production-issues PSE Issue 4','inferred','legacy/migration')
E('svc:fts-workers','q:kafka-fts-status','publishes','rx-fts-status-update-events (instead of HTTP when FireStatusUpdateKafka on)','code','fts','config/env.prod-live.toml:321','',None,'mTLS','max_retry 10','FireStatusUpdateKafka')
E('q:kafka-fts-status','svc:payouts-kafka','consumes','fts consumer task; DROPS reversed/failed (monolith source of truth); retry topic fts_status_updates_retry','code','payouts','internal/taskHandlers/fts_status_updates.go:56-60,92-111','ProcessMessage')
E('svc:fts','svc:payouts','calls','/v1/notify/health/update, /v1/notify/downtime (bene bank health → on_hold)','code','payouts','internal/routing/router/internal_routes.go:12-30','',None,'Basic cred.FTS')
E('svc:fts-workers','svc:xas','reads','account/statement lookups in retry preprocessing','code','fts','internal/transfer/retry_preprocessor_service.go','')
E('svc:fts','svc:relay','polls','relay config poll 15s','code','fts','config/env.default.toml:9063','[relay]',None,'RELAY_AUTH','','','prod','','probable')
E('ep:fts-stuck-cron','db:fts-mysql','reconciles','StuckTransfers/StuckAttempts processors re-drive stuck state (canary instance only)','code','fts','internal/stuckpayouts/cron.go:47; internal/transfer/stuck_payouts.go','')
E('svc:recon','svc:fts','calls','ART: v1/attempts/:action, /reconcile','code','fts','internal/routing/router/route_list.go','ARTAuth',None,'Basic users.art','','','prod','','probable')
E('svc:settlements','ep:POST /v1/transfer','calls','settlement transfers (SETTLEMENT auth); twirp status webhook back','code','fts','config/env.default.toml [webhook.settlement]','',None,'Basic','','','prod','','confirmed','current(out of scope)')
E('svc:wallet','ep:POST /v1/transfer','calls','customer wallet transfers (2s webhook timeout)','code','fts','config/env.default.toml [webhook.customer_wallet]','',None,'Basic','2s','','prod','','confirmed','current(out of scope)')

# ---------------- Edges: payouts state, workers, webhooks ----------------
E('svc:payouts','db:payouts-mysql','writes','state machine transitions pkg/tranisiton (15 internal → 6 public statuses)','code','payouts','internal/app/payouts/state_machine.go','InitializeTransition')
E('svc:payouts','q:sqs-webhook-event','publishes','webhook_event job (3 retries, 120s)','code','payouts','internal/job/webhook_event.go:23-31','')
E('q:sqs-webhook-event','svc:payouts-workers','consumes','','code','payouts','internal/job/webhook_event.go','')
E('svc:payouts-workers','svc:stork','calls','Twirp ProcessEvent (payout.* webhooks; optional payload encryption); SMS/email templates','code','payouts','internal/app/payouts/core.go:5696-5720; pkg/stork/client.go','PushStorkWebhookEvent',None,'Basic','30s, 3 retries, Hystrix')
E('svc:stork','ext:merchant-webhook','calls','merchant webhook delivery; NO ordering guarantee (processed before updated observed)','slack+docs','stork','','',None,'','','','prod','#x-production-issues 2026-06-09; docs webhooks/payouts.md','confirmed')
for ev in ['payout.pending','payout.rejected','payout.queued','payout.initiated','payout.processed','payout.updated','payout.reversed','payout.failed']:
    E('svc:payouts','event:'+ev,'publishes','state Enter hook FireWebhookEventAsyncForPayout','code','payouts','internal/app/payouts/state_machine.go','')
E('svc:payouts','q:sns-source-updates','publishes','per-source-type status broadcast (source_updater job)','code','payouts','pkg/sns/sns.go:23-30; config/default.toml:496-509','')
E('q:sns-source-updates','svc:payout-links','consumes','→ inbound Twirp UpdatePayoutLinkStatus (via HTTP subscription/forwarder)','code','payout-links','internal/apiservice/server.go:364','UpdatePayoutLinkStatus',None,'','','','prod','','probable')
E('q:sns-source-updates','svc:vendor-payments','consumes','→ inbound RPC PayoutStatusChange','code','vendor-payments','internal/apiservice/VpServer.go:1178-1206','PayoutStatusChange',None,'','','','prod','','probable')
E('svc:payouts','q:kafka-tds','publishes','add-tds-entry on money transfer completion','code','payouts','internal/app/payouts/core.go:8212-8259','')
E('svc:payouts','q:sqs-xas-source','publishes','payout/reversal source events','code','x-account-statements','internal/job/source_processing/source_processing.go','',None,'','','','prod','','probable')
E('q:sqs-xas-source','svc:xas','consumes','UTR/GRN matching → statement enrichment','code','x-account-statements','internal/account_statements/enrich/service/service.go','EnrichWithStatement')
E('svc:xas','svc:mozart','calls','statement fetch ICICI/Axis/RBL/IDFC/Yes','code','x-account-statements','internal/gateway/mozart','')
E('svc:xas','svc:api-monolith','writes','EnrichmentUpdate / StatementDualWrite (statement→entity link only, no payout status)','code','x-account-statements','internal/gateway/api/interfaces.go','')
E('svc:xas','db:xas-mysql','writes','account_statements, accounts (CDC to monolith)','code','x-account-statements','config/prod.toml:83-90','')
E('svc:payouts','svc:xas','reads','xas client','code','payouts','internal/provider/xas_client.go','')
E('svc:recon','svc:xas','reads','3-way ART reconciliation input','slack','recon','','',None,'','','','prod','#rx-recon 2026-08-10','probable')
E('ep:cron','svc:payouts','calls','FastCron Basic Auth: process_queued_low_balance_payouts, process_scheduled_payouts, on_hold, inflight reservation reconciliation, dual-write failure processing, reverse_dual_write, sla monitor','code','payouts','internal/routing/router/cron_routes.go:13-128','',None,'Basic cred.FastCron','','','prod','schedule lives in external manifests (unknown)')
E('svc:payouts','q:sqs-queued-payout','publishes','queued/scheduled/on_hold dispatch jobs','code','payouts','internal/job/process_queued_payout.go','')
E('q:sqs-queued-payout','svc:payouts-workers','consumes','','code','payouts','internal/job/schedule_payout.go','')
E('svc:payouts','db:payouts-redis','writes','in-flight reservations (reconciled from DB by cron; fail-closed heartbeat)','code','payouts','internal/routing/router/cron_routes.go:62-70','ProcessReconciliationForInFlightReservations',None,'','','in_flight_reservation_enabled','prod','#x-production-issues 2026-08-29 over-holding bug')

# ---------------- Edges: migration / dual-write ----------------
E('svc:payouts','svc:api-monolith','dual_writes','POST /payouts_service/dual_write (async SQS 1min; direct-push variant 30s via Splitz) → monolith/TiDB payouts_temp; chronically failing (RZPX-144)','code+slack','payouts','pkg/api/dual_write.go:14-46; internal/app/common/dualWrite/payout.go:20-77','',None,'Basic','3 retries via cron','ForDualWriteDirectPushToAPI','prod','#x-production-issues 2026-08-21; RZPX-144','confirmed','migration')
E('svc:payouts','db:payouts-tidb','writes','payouts_temp / TiDB copy via WDA; forward-dual-write experiment removed in payouts#1946','code+slack','payouts','internal/app/tidb/core.go','',None,'','','','prod','#x-payouts-reliability 2026-08-27','probable','migration')
E('svc:api-monolith','svc:payouts','dual_writes','reverse dual-write (monolith → PS) strategies; forward API→PS dual write REMOVED Aug-2026','code+slack','payouts','internal/app/reverseDualWrite/service.go:22-38','',None,'','','features.reverse_dual_write','prod','#tech_backend 2026-08-10','confirmed','legacy/migration')
E('svc:payouts','db:api-mysql','reads','[db.api] connection; FetchAllPayoutsFromApiDb gated off (hardcoded false); payouts table deleted in API DB Aug-2026','code+slack','payouts','config/default.toml:35-45; internal/app/payouts/core_fetch_apidb_gate_test.go','',None,'','','IsApiDbEnabledForPayouts=false','prod','#api_decomposition 2026-08-06','confirmed','legacy')
E('svc:payouts','db:payouts-es','writes','ES sync/backfill (reverse dual write ES syncer)','code','payouts','internal/app/reverseDualWrite/es_syncer_adapter.go','')
E('flag:merchant:payout_service_enabled','svc:api-monolith','controlled_by','merchant-level routing to PS (monolith side)','slack','api','','',None,'','','','prod','#x-production-issues 2026-06-25','probable','migration')
E('flag:shadow_gateway.enabled','svc:payouts','controlled_by','master kill switch','code','payouts','config/prod.toml:857-878','')
E('flag:kong:payouts-proxy-cutover','svc:edge','controlled_by','route divert to PS','slack','terraform-kong','PR 10991/11033/11047','',None,'','','','prod','#platform_spine_edge_oncall 2026-09-03','probable','migration')

# ---------------- Edges: admin ----------------
E('fe:admin-dashboard','svc:api-monolith','calls','admin routes live/payouts/{id}/manual/status, /admin/payouts/{id}/reject, /payouts/retry, /payouts/manual_action, fund_transfer_attempts, fee_recovery','code','admin-dashboard','js/admin/adminActions/actionModals/*.js; js/admin/payouts/PayoutsManualActions.js','',None,'admin session + permission strings (client-side)','')
E('svc:api-monolith','ep:manual_action','calls','monolith → PS internal routes (service Basic Auth only; no passport admin check on manual_action)','code','payouts','internal/routing/router/payout_internal_routes.go:14-25; internal/controllers/adminClientController.go:267-330','ManualAction',None,'Basic cred.API')
E('ep:manual_action','db:payouts-mysql','reverses','force processed→processing, approve/reject workflow payouts','code','payouts','internal/controllers/adminClientController.go:267-330','')
E('ep:admin-force-status','svc:api-monolith','reverses','force status incl. reversed; monolith creates reversal entity BEFORE ledger journal (ordering bug)','code+slack','admin-dashboard','js/admin/adminActions/actionModals/ForceUpdatePayoutStatus.js','',None,'payout_status_update_manually','','','prod','#x-production-issues 2026-08-03','confirmed')
E('fe:admin-dashboard','svc:fts','calls','fund_transfer_attempts update/initiate via monolith','code','admin-dashboard','js/admin/adminActions/actionModals/FundTransferUpdate.js','',None,'settlement_bulk_update','','','prod','','probable')
E('svc:ledger','db:ledger-pg','reverses','ForceUpdateBalance bypasses min-balance (admin repair)','code','ledger','internal/account/core.go:838','ForceUpdateBalance')

# ---------------- Edges: variants ----------------
E('svc:payout-links','svc:api-monolith','calls','POST payouts_internal (Basic rzp_live + X-Payout-Idempotency + X-Razorpay-Account) on api.razorpay.com','code','payout-links','internal/payout/core.go:44; config/prod.toml:227-233','CreatePayoutApi',None,'Basic')
E('svc:vendor-payments','svc:api-monolith','calls','POST v1/payouts_internal / internalContactPayout / payouts/2fa/create_internal on x.razorpay.com','code','vendor-payments','internal/payout/core.go:82-116','CreatePayout',None,'Basic + X-Dashboard-User-Id')
E('svc:api-monolith','ep:POST /v1/payouts','routes_to','internal routes → PS /v1/payouts/payouts_internal, /internal_contact_payout (privilege auth, allowlisted apps)','code','payouts','internal/routing/router/payout_routes.go; internal/auth/headers.go:11-25','IsAllowedInternalApp')
E('fe:payout-links-customer','svc:edge','calls','public /v1/payout-links/{id}/initiate (OTP keyless)','code','frontend-x','x-customer-payout-links/src/shared/api/plApi.js','')
E('svc:payout-links','svc:stork','calls','payout_link.* webhooks + SMS','code','payout-links','pkg/stork/impl.go','')
E('svc:vendor-payments','svc:workflow','calls','purchase-order-approval workflows','code','vendor-payments','internal/workflows/apiclient/core.go:14-20','')
E('q:kafka-payout-cdc','svc:charge-collections','consumes','metered billing ingestion (producer unknown)','code','charge-collections','internal/job/ingestion_job.go:16-42','IngestionJob',None,'','','','prod','','probable')
E('svc:virtual-account','svc:x-balances','reads','GetBalanceIDByAccountNumber','code','virtual-account','internal/integrations/xbalance/interface.go:12-24','')
E('svc:vendor-payments','svc:accounting-integrations','publishes','Kafka prod.x.vendor-payments.accounting-payouts.status-update','code','accounting-integrations','config/prod.toml:196','')

# ---------------- Roles / identities ----------------
for r in ['role:finance-l1','role:finance-l2','role:finance-l3','role:owner-admin','role:view-only']:
    E(r,'fe:x','authorizes','<Can I={PERMISSION}> + RestrictedRoute (client-side only)','code','x','src/js/views/xtra/Can/permission.js:244-268','')
E('id:service-basic-auth','svc:fts','authorizes','per-caller users.* creds (API, PS, WALLET, SETTLEMENT, SCROOGE, ART, VALIDX, CAPITALCARDS, XPERIENCE)','code','fts','internal/routing/middleware/auth.go:31-32','BasicAuth')
E('id:service-basic-auth','svc:cfa','authorizes','Basic + per-username method ACL','code','cfa','internal/server/interceptor/authz.go:76-101','')
E('id:service-basic-auth','svc:ledger','authorizes','auth.payouts/auth.fts/auth.xbalances + Ledger-Tenant header','code','ledger','internal/boot/hooks/auth.go:18','')
E('id:service-basic-auth','svc:payouts','authorizes','cred.API/Workflow/Xperience/FTS/VendorPayments/Settlements/Irctc/FastCron','code','payouts','internal/routing/router/payout_internal_routes.go:13-90','')
E('id:service-basic-auth','svc:x-balances','authorizes','Server.Auth.{API,PS,BankingAccounts,Validx,VirtualAccount,Admin,Cron}','code','x-balances','config/default.toml','')
E('deploy:payouts','svc:payouts','deployed_by','Dockerfiles; manifests in kube-manifests/spinacode (inaccessible); cron cadence unknown','code','payouts','build/docker/prod/','',None,'','','','prod','','confirmed')
E('deploy:fts','svc:fts','deployed_by','in-repo k8s deployments; crons in-process (gocron+leader election)','code','fts','deployments/prod/deployment-web.yaml','')

def extend(path):
    """Load extra edges/nodes emitted by later waves (JSON with nodes/edges lists)."""
    p = Path(path)
    if not p.exists(): return
    d = json.loads(p.read_text())
    for n in d.get('nodes', []): nodes.setdefault(n['id'], n)
    for e in d.get('edges', []): edges.append(e)

extend('/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/graph_extra.json')

# ---------------- Emit ----------------
OUT.mkdir(parents=True, exist_ok=True)
graph = dict(version='2026-09-03.v1', generated_by='rzp payouts architecture investigation (root agent)', commits=SHA,
             node_types=sorted({n['type'] for n in nodes.values()}), edge_types=sorted({e['type'] for e in edges}),
             nodes=list(nodes.values()), edges=edges)
(OUT/'PAYOUTS_SERVICE_GRAPH.json').write_text(json.dumps(graph, indent=1))

# Mermaid: service-level view (collapse repo nodes; keep services, datastores, queues, external, flags)
def mid(s): return 'n' + str(abs(hash(s)) % (10**9))
shape = {'service':('[',']'),'worker':('[[',']]'),'frontend':('([','])'),'datastore':('[(',')]'),'queue/topic':('>',']'),
         'external bank/provider':('{{','}}'),'webhook consumer':('{{','}}'),'identity':('(',')'),'role':('(',')'),
         'feature flag':('{','}'),'gateway route':('{','}'),'configuration source':('{','}'),'AuthZ policy':('{','}'),
         'API endpoint':('[/','/]'),'admin action':('[/','/]'),'cron':('[/','/]'),'event':('(',')'),'deployment':('[',']'),'repository':('[',']')}
lines = ['%% PAYOUTS_SERVICE_GRAPH v2026-09-03.v1 — service-level view (repo/event/deploy/role nodes omitted for legibility; full graph in JSON)',
         'flowchart LR']
skip_types = {'repository','event','deployment','role'}
used = set()
for e in edges:
    s, t = nodes.get(e['source']), nodes.get(e['target'])
    if not s or not t or s['type'] in skip_types or t['type'] in skip_types: continue
    used.add(s['id']); used.add(t['id'])
    lab = e['type'] + (' [' + e['lifecycle'] + ']' if e['lifecycle'] != 'current' else '') + (' ?' if e['confidence'] != 'confirmed' else '')
    lines.append(f"  {mid(s['id'])} -->|{lab}| {mid(t['id'])}")
for nid in used:
    n = nodes[nid]; a, b = shape.get(n['type'], ('[',']'))
    label = n['label'].replace('"', "'")
    lines.insert(2, f"  {mid(nid)}{a}\"{label}\"{b}")
lines.append('  classDef inaccessible fill:#fde2e2,stroke:#b00,stroke-dasharray: 4 2;')
inacc = [mid(n) for n in used if not nodes[n]['accessible']]
if inacc: lines.append('  class ' + ','.join(inacc) + ' inaccessible;')
(OUT/'PAYOUTS_SERVICE_GRAPH.mmd').write_text('\n'.join(lines))

# GraphML
gm = ['<?xml version="1.0" encoding="UTF-8"?>','<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
nkeys = ['type','label','repo','accessible','lifecycle']
ekeys = ['type','mechanism','environment','auth','timeout_retry','feature_flags','evidence_type','repository','file_path','symbol','commit','slack_or_doc_ref','confidence','lifecycle','note']
for k in nkeys: gm.append(f'<key id="n_{k}" for="node" attr.name="{k}" attr.type="string"/>')
for k in ekeys: gm.append(f'<key id="e_{k}" for="edge" attr.name="{k}" attr.type="string"/>')
gm.append('<graph id="payouts" edgedefault="directed">')
for n in nodes.values():
    gm.append(f'<node id="{html.escape(n["id"])}">' + ''.join(f'<data key="n_{k}">{html.escape(str(n.get(k,"")))}</data>' for k in nkeys) + '</node>')
for i,e in enumerate(edges):
    gm.append(f'<edge id="e{i}" source="{html.escape(e["source"])}" target="{html.escape(e["target"])}">' + ''.join(f'<data key="e_{k}">{html.escape(str(e.get(k,"")))}</data>' for k in ekeys) + '</edge>')
gm += ['</graph>','</graphml>']
(OUT/'PAYOUTS_SERVICE_GRAPH.graphml').write_text('\n'.join(gm))
print('nodes', len(nodes), 'edges', len(edges))
