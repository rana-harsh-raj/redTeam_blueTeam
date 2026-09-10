# PAYOUTS_ARCHITECTURE — RazorpayX Business Banking Plus Payouts (as of 2026-09-04, v2)

> v2 note: on 2026-09-04 `razorpay/api`, `edge`, `terraform-kong`, `authz`, `workflows`, `batch`, `stork`, `mozart`, `dcs`, `splitz`, `governor`, `recon`, `kube-manifests`, `alert-rules`, `spinacode`, `knowledge-base` and the shared Go modules became readable. Nodes marked ⚠ below were inferred on 2026-09-03; `ARCHITECTURE_DELTA.md` records what changed and which assumptions were wrong (8). Still not readable: ASV/account-service, Kong plugin repos, scrooge, settlements, raven, ufh, beam, razorx, wda-service, vault, hvault, tax-compliance, master-onboarding, catalyst, qa-tools, e2e-test-orchestrator.

Companion files: `PAYOUTS_FLOW_CATALOG.md` (sequence diagrams per flow), `PAYOUTS_SERVICE_GRAPH.{json,mmd,graphml}` (versioned graph with per-edge evidence), `EVIDENCE_INDEX.md`, `CONTROL_AND_INVARIANT_CATALOG.md`.

Nodes marked ⚠ are repositories/systems this identity cannot read; their behaviour is inferred from client code in accessible repos plus Slack.

## 1. System context

```mermaid
flowchart TB
    subgraph initiators["Initiators"]
        DU[Dashboard user<br/>cookie session + CSRF<br/>roles finance_l1/l2/l3, owner, view_only]
        MA[Merchant API client<br/>key_id:secret Basic Auth]
        PL[payout-links / vendor-payments / charge-collections / xperience<br/>internal Basic Auth + X-Razorpay-Account]
        AD[Admin dashboard operator<br/>permission strings]
        BS[Batch service ⚠<br/>bulk rows]
    end
    subgraph edge["Edge"]
        K[Kong / terraform-kong ⚠<br/>basic-auth-x, upstream-jwt (passport), ip-restriction, rate-limit,<br/>payouts-proxy-cutover shadow route]
    end
    subgraph legacy["Legacy core"]
        API[API monolith (PHP) ⚠<br/>dashboard authenticate, payouts proxy, payouts_internal routes,<br/>workflow/approval UI paths, admin routes, balance/bas, FTS legacy webhook receiver]
    end
    subgraph payouts["Payouts platform (Business Banking pod)"]
        PS[Payouts Service<br/>Go/Gin · MySQL · Redis · SQS · Kafka · ES · TiDB]
        CFA[CFA<br/>Mongo (+API MySQL fallback)]
        XB[x-balances<br/>MySQL (+API balance fallback)]
        BAS[banking-accounts]
        VX[ValidX (FAV)]
        FTS[FTS<br/>MySQL · Redis/machinery]
        XAS[x-account-statements]
        VA[virtual-account]
        XP[Xperience BFF]
    end
    subgraph shared["Shared platform"]
        LG[Ledger (tenant X)<br/>Postgres · Twirp]
        WFS[Workflow Service ⚠]
        ST[Stork ⚠ webhooks/SMS]
        SH[Shield ⚠ risk]
        GV[Governor + Charge Collections pricing]
        DCS[DCS ⚠ merchant config]
        SP[Splitz ⚠ experiments]
        AZ[AuthZ ⚠ policies]
        ASV[Account Service ⚠]
        MZ[Mozart ⚠ bank gateway]
        RC[Recon / ART ⚠]
    end
    BK[(Partner banks: ICICI, Axis, RBL, IDFC, Yes, HDFC, Citi, M2P, MCS, OCBC, Slice, Amazon Pay, JPMC)]
    MW[Merchant webhook endpoint]

    DU --> K; MA --> K; PL --> K; AD --> API; BS --> PS
    K --> API; K -.pilot.-> PS
    API --> PS; API --> WFS; API --> AZ
    PS --> CFA; PS --> XB; PS --> LG; PS --> FTS; PS --> SH; PS --> GV; PS --> DCS; PS --> SP; PS --> WFS; PS --> ST; PS --> ASV; PS --> XAS
    XP --> BS; XP --> API; XP --> PS; XP --> WFS
    FTS --> MZ --> BK; FTS --> PS; FTS --> API; FTS --> LG
    XB --> MZ; XB --> LG; XB --> BAS; XAS --> MZ; XAS --> API
    VX --> CFA; VX --> FTS
    ST --> MW; XAS --> RC
```

## 2. Service / container view (deployables and stores)

| Service | Repo | Processes | Stores | Inbound auth | Owner (repo_owners.json / Slack) |
|---|---|---|---|---|---|
| Payouts Service | razorpay/payouts | cmd/api, cmd/workers (≈25 SQS jobs), cmd/kafkaConsumers (fts task), cmd/migration; cron endpoints hit by external scheduler | MySQL master/replica, Redis (mutex, reservations, merchant-config cache), Kafka, SQS, SNS, ES, TiDB via WDA, `[db.api]` (gated off) | service BasicAuth (cred.API/Workflow/Xperience/FTS/VendorPayments/Settlements/Irctc/FastCron) + Passport JWT (private/proxy/privilege/admin) on merchant routes | Business Banking, EM kumar.ayush@; @razorpay/razorpayx_payouts_be; #x-payouts-reliability, #payout-service-alerts |
| Ledger | razorpay/ledger | api (Twirp), worker (SQS), scheduler, outbox_relay, worker_kafka (PG tenant only), makeshift workers | Postgres rx + pg, SQS, SNS journal-created, Kafka (PG CDC) | BasicAuth per client (auth.payouts, auth.fts, auth.xbalances) + Passport/authz for others | Platforms/Payments Platform/Recon, EM aditya.jalan@; @razorpay/ledger-team; #fin_infra_ledger, T1 |
| FTS | razorpay/fts | web (nginx+Go), default-workers (machinery), migration; in-process gocron with leader election | MySQL, Redis (broker/mutex), Kafka producer | BasicAuth only (users.api/ps/wallet/settlement/scrooge/art/validx/capitalcards/xperience/alert) | Business Banking, EM kumar.ayush@; path CODEOWNERS only; no servicetree |
| CFA | razorpay/cfa | server (gRPC 8080 + HTTP 8081), worker (SQS dual-write/lazy-load), migration | MongoDB, API MySQL (read fallback), SQS | BasicAuth + per-username method ACL (API, Payouts, XPerience, Validx, Wallet, Dev) | Business Banking, EM kumar.ayush@ |
| x-balances | razorpay/x-balances | server (gRPC/HTTP), worker (per-bank balance fetch, account activation, payout events) | MySQL, API MySQL (fallback), Redis, SQS | BasicAuth (API, PS, BankingAccounts, Validx, VirtualAccount, Admin, Cron) | Business Banking, EM kumar.ayush@ |
| banking-accounts | razorpay/banking-accounts | single HTTP service | MySQL, Kafka (datalake) | Passport + JWT bank claims; admin auth | Business Banking, EM sagar.gupta@ |
| ValidX | razorpay/ValidX | server, worker, migration | MySQL, Redis, SQS | BasicAuth (inferred) | ValidX pod, BU RazorpayX Payouts |
| Xperience | razorpay/xperience | api, migration | MySQL/Postgres | Passport (apiv1/edgev1) + Casbin authz org razorpayx | Payouts Platform, EM abhishek.sk@; @razorpay/xperience-devs |
| XAS | razorpay/x-account-statements | server, worker (12 SQS queues), migration | MySQL (+CDC topic to monolith), ES | BasicAuth | Payouts Platform, EM abhishek.sk@; @razorpay/xas-recon-devs; #rx-recon |
| x dashboard | razorpay/x | static SPA (S3 + nginx) | — | cookie session + CSRF | tech-banking; #x-payouts-experience |
| API monolith ⚠ | razorpay/api | PHP | MySQL (balance, contacts, fund_accounts, merchants, features; payouts table deleted), TiDB payouts_temp | — | Payouts team (app/Models/Payout) + core API |

Deployment (v2): `kube-manifests` is readable — payouts/cfa/validx/mozart run in the `cde/` environment; no NetworkPolicy or sidecars; secrets are plain k8s Secrets from a DynamoDB-backed store; PS `/v1/cron/*` is a Traefik IngressRoute IP-whitelisted for the external **FastCron SaaS** (cadence lives only there); queued/scheduled/on_hold processing is continuous SQS/Kafka worker draining; real CronJobs exist only for ledger (suspended in chart), cfa (none active), batch, stork. Spinnaker pipelines in `spinacode/v3/<service>/` with canary analysis.

## 3. Trust boundaries

```mermaid
flowchart LR
    subgraph B0["Boundary 0 — Internet"]
        DU[Dashboard user]:::ext; MA[Merchant API key]:::ext; MW[Merchant webhook]:::ext
    end
    subgraph B1["Boundary 1 — Edge/Kong ⚠ (identity minting)"]
        K[basic-auth-x → consumer; upstream-jwt → X-Passport-JWT-V1 claims authenticated/identified/mode/roles/consumer/impersonation; IP allowlist; rate limits]
    end
    subgraph B2["Boundary 2 — API monolith ⚠ (dashboard auth of record)"]
        API[authenticate: merchant_users membership, dashboard IP allowlist, OTP; role/permission checks via AuthZ; proxies to PS with service BasicAuth + passport]
    end
    subgraph B3["Boundary 3 — Payouts Service"]
        PSM[PassportAuthentication allowlist per route group; ServiceBasicAuthOrPassport (cutover); internal routes BasicAuth-only + unscoped FindByID (TODO in code); admin routes passport admin; cron FastCron]
    end
    subgraph B4["Boundary 4 — Money/transfer services"]
        LG[Ledger: BasicAuth client creds + Ledger-Tenant header]; FTS[FTS: BasicAuth per caller]; CFA[CFA: BasicAuth + method ACL + merchant_id filter]; XB[x-balances BasicAuth]
    end
    subgraph B5["Boundary 5 — Bank gateway ⚠"]
        MZ[Mozart: service BasicAuth + per-bank gateway_auth/session tokens; certs (metadata only)]
    end
    subgraph B6["Boundary 6 — Ops"]
        ADM[Admin dashboard permissions (client-side) → monolith admin routes → PS internal routes; PS query_db SELECT-only; ledger ForceUpdateBalance; x-balances raw SQL admin actions; FTS manual_queries]
    end
    DU --> K --> API --> PSM --> LG; PSM --> FTS --> MZ; PSM --> CFA; PSM --> XB; MA --> K; ADM --> API
    classDef ext fill:#eee
```

Header/claim rewrite points: Edge mints passport; monolith forwards `X-Dashboard-Ip`, sets service BasicAuth; PS derives merchant from `consumer.id` (private) or `impersonation.consumer` (proxy/privilege) with `x-merchant-id` header fallback; outbound PS/xperience/variants set `X-Razorpay-Account`; PS sets `X-Origin: payouts` to FTS (FTS defaults to "API" when absent). Cross-cell/region: ledger has in/sg/us pipelines; RBL multicurrency and Curlec (MYR) exist; not otherwise traced.

Classification of controls (declared where): see `CONTROL_AND_INVARIANT_CATALOG.md`.

## 4. Data ownership map

| Data | Owner (source of truth) | Store | Copies / eventual | Notes |
|---|---|---|---|---|
| Payout entity, status, status_details, payout_logs, workflow_state_map, idempotency_keys, reversals | Payouts Service | PS MySQL | TiDB `payouts_temp` (dual-write, failing), ES index, monolith read models via reverse sync, data lake | monolith `payouts` table deleted ~12 Aug 2026 |
| Merchant balance (Shared / VA / Lite) | Ledger (tenant X) `accounts.balance` | Ledger Postgres | x-balances enriches from ledger | atomic conditional debit at payout_initiated |
| Direct/current-account balance | Bank (via Mozart) | x-balances `balance` (polled) and API monolith `balance` (legacy) | Redis cache in PS | three-way source during migration |
| Source account ↔ balance_id ↔ fts_fund_account_id | banking-accounts | BAS MySQL (+ monolith bas for legacy banks) | x-balances rows; FTS source_accounts; ledger account_details | FTS mapping can drift (Sept-2026 misroute incident) |
| Contacts, fund accounts | CFA | Mongo | API MySQL (dual-write 30 s; still authoritative for list queries) | no unique hash index; ValidX validation status held in ValidX |
| Fund-account validation results | ValidX | ValidX MySQL | — | PS gates on freshness |
| Transfer, attempts, UTR, bank status | FTS | FTS MySQL | payout.utr copied via webhook | write-once guard fixed (#1872) |
| Ledger journals / entries / ledger_config | Ledger | Postgres | SNS journal-created | ledger_config admin-editable |
| Bank statements, enrichment links | XAS | XAS MySQL → CDC to monolith | monolith BAS tables | |
| Workflow state | Workflow Service ⚠ + PS workflow_state_map | unknown + PS MySQL | forwarded to monolith best-effort | |
| Merchant features/config | DCS ⚠ (+ monolith features) | — | PS Redis 30-min cache | dual-named flags |
| Merchant webhooks delivery state | Stork ⚠ | — | — | no ordering |

## 5. Current vs legacy / decomposition map

| Capability | Current owner | Legacy / migration state | Flag / lever | Evidence |
|---|---|---|---|---|
| Public + dashboard request routing | Edge → API monolith → PS (prod fully undiverted; `payouts-proxy-cutover` instantiated only in devstack `base/payouts-proxy-cutover`) | Cutover to Edge → PS per route (Group 1–3 schedule Jul–Sep 2026); devstack: `/v1/payouts*` diverted for one consumer, GET timeslots 100%; no Edge loop guard exists (C45); no rate limiting on payout routes (C43) | Kong template, PS `shadow_gateway.enabled` (prod false), Splitz per-route | 01; 11; 21 |
| Payout entity | PS MySQL | Monolith code still targets its local `payouts` table (approve/reject, BAS recon, manual status, dual-write PS→monolith upsert) while the physical table is dropped in prod ⇒ `ApprovedPayoutProcessor` fails (C42/C49) | `features.forward_dual_write`, `reverse_dual_write`, `ForDualWriteDirectPushToAPI` | 02; 11; 20 §8 |
| Dashboard/user authentication | Kong upstream-jwt mints passport (RS256, 300 s, no authType claim); monolith `MerchantIpFilter` IP allowlist + mandatory OTP for `payouts_with_otp`; merchant_users membership NOT re-checked in api | Must move for full cutover; merchant_activation_check being built in PS via ASV | Kong ctx whitelist gap `impersonation.consumer.username` | 20 §3; 21 §2 |
| Authorization (roles) | Kong `authz-enforcer` enforced on dashboard-merchant routes, shadow + whitelisted on public API; admin permissions local DB-backed (`AdminAccess::policyChecker`) | PS to enforce directly (PR 2577 pilot); prod `watchResourceGroups` excludes `x_platform` (C46); `non_lms` string absent from authz repo | authz policies (casbin, Consul-synced) | 20 §7; 21 §3 |
| Approval workflow | Workflow Service (Go/Twirp on Uber Cadence; no passport/AuthZ check; callbacks retried up to 30 d; **no expiry callback**) + PS `IsWorkflowApplicable`; OTP enforced in monolith | Bulk approvals: Batch `payout_approval` → PS `payouts/bulk_approve` directly (WFS has no bulk RPC); 3-month auto-reject is a monolith cron | DCS flags (dual names), Splitz `PayoutWorkflowsDcsNameExperiment` | 12; 20 §5; 23 |
| FTS call origin | PS direct (`X-Origin: payouts`) for enabled merchants | Monolith client path still live when `IsFTSRequestFromPayoutsService` false | config allow/deny + Splitz `fts_request_from_payouts_service_experiment`; FTS `CreateTransferMetaRollout`, `FireStatusUpdateKafka` | 13; 03 |
| FTS status receipt | PS `/v1/payouts/transfer_status_webhook` | Monolith `/v1/update_fts_fund_transfer` (`FundTransferAttemptController@updateSource`: FTA committed first, single-attempt relay, no retry) → PS `/update_payouts_with_fts`; Kafka consumer drops reversed/failed; aggregate-only alerting (C58) | same | 13; 20 §4; 22b |
| Balance | Ledger (Shared/VA), x-balances (Direct selection) | API `balance` table fallback; legacy banks create balances via monolith bas | `PayoutsBlockedOnLite`, `in_flight_reservation_enabled` | 06 |
| Contacts / fund accounts | CFA Mongo | API MySQL fallback; FetchMultiple MySQL-only | `EnableCFADB`, `EnableTiDB` | 05 |
| Bulk payouts | Xperience + Batch service | Dashboard batch CRUD still via monolith; 4 API generations in x | `isXpsEdgeBulkPayoutsMigrationEnabled` | 07; 11 H |
| Webhooks | PS → Stork direct | none | payload encryption feature | 02 |
| Remaining on monolith | wallet, settlements, IRCTC (settlement/recon via Catalyst in build), partner-OAuth & mobile-number payouts (deprecating), onboarding/merchant invoices/fee recovery write cutoff Q3 | — | — | 11 A |

## 6. Payout state machine (PS internal → public)

| Internal | Public | Reachable from | Trigger |
|---|---|---|---|
| create_request_submitted | processing | initial | create |
| pending | pending | create_request_submitted | workflow applicable |
| scheduled | scheduled | create_request_submitted, pending | scheduled_at |
| batch_submitted | processing | create_request_submitted, pending, scheduled | bulk NEFT/RTGS |
| queued | queued | create_request_submitted, ledger_response_awaited, pending, scheduled, on_hold | low balance / fee recovery / gateway degraded |
| on_hold | queued | create_request_submitted, pending, batch_submitted, scheduled | bene/partner bank down |
| ledger_response_awaited | processing | create_request_submitted, pending, scheduled, queued, on_hold, batch_submitted | before Journal.Create |
| created | processing | create_request_submitted, pending, scheduled, queued, on_hold, ledger_response_awaited, batch_submitted | ledger ok → payout_initiated |
| initiated | processing | created, initiated | FTS transfer created |
| processed | processed | initiated | FTS webhook processed |
| failed | failed | most non-terminal incl. initiated | FTS failed (Direct) / on_hold SLA cancel |
| reversed | reversed | almost all incl. processed, failed | FTS reversed / Shared failed remap / admin |
| cancelled | cancelled | queued, scheduled, on_hold | merchant cancel |
| rejected | rejected | pending | checker reject / scheduled-while-pending |

Source: `payouts/internal/app/payouts/state_machine.go`, `appConstants/states.go`, `status.go`. FTS transfer FSM and attempt FSM: `fts/internal/transfer/state_machine.go`.
