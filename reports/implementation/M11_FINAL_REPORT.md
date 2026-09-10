# M11 — Critical Trust-Path Fidelity Upgrade — Final Report

Generated 2026-09-09T19:23:10Z by scripts/m11/final_report.py from the artifacts named below. Branch `milestone-11-trust-path-fidelity` from tag `campaign-m10-control-plane`; git head `8e4b45a00798`.

## Acceptance

| accepted | gates | evaluated | snapshot | record |
|---|---|---|---|---|
| **True** | 17/17 | 2026-09-09T19:20:08.932880+00:00 | `8a52779f05275ea3` | reports/implementation/M11_ACCEPTANCE.json |

- PASS **M11-01** real edge gateway (razorpay/edge Kong + terraform-kong prod-api routes) is the DEFAULT ingress of every derived profile
- PASS **M11-02** highest feasible monolith auth path: boot attempt recorded with exact blockers; the monolith replacement verifies the gateway passport (kid edgev2) end to end
- PASS **M11-03** real Shield (razorpay/shield, APP_MODE=rzpxprod) replaces shield-stub and decides payouts
- PASS **M11-04** banking-accounts real where feasible (Direct merchants); account-service blocked with an exact reason
- PASS **M11-05** real Workflow service (razorpay/workflows + Cadence) on LOCAL infrastructure drives maker-checker
- PASS **M11-06** every remaining substitute in the real variant carries a precise external blocker
- PASS **M11-07** canonical journey suite passes on the real variant (0 FAIL; every BLOCKED names its dependency)
- PASS **M11-08** differential run (same suite on the substitute variant) exists and every difference is investigated (0 OPEN)
- PASS **M11-09** snapshot, fidelity labels, recipes (pinned SHAs), locks and profiles updated: current snapshot verifies and carries the trust-path recipes
- PASS **M11-10** clean checkout reproduces the real-variant twin (create + build + start from a fresh clone)
- PASS **M11-11** resource usage measured (local CPU/memory/disk/build/boot; remote recorded as not used with the reason)
- PASS **M11-12** historical tags unchanged (every pre-M11 tag still points at its recorded commit)
- PASS **M11-13** clean working tree at evaluation (only this acceptance record may differ)
- PASS **M11-14** secrets hygiene: no generated secret values tracked; instance secrets materialized uid 10001 / 0400
- PASS **M11-15** immutable images: every trust-path image is recorded by image id in the instance manifest and the factory cache
- PASS **M11-16** twinfactory / archkit / api-ingress contract / controlplane suites green (controlplane package unchanged)
- PASS **M11-17** controlplane package unchanged since campaign-m10-control-plane

## What is real, adapted, replaced, still substituted, unknown

| component | category | what runs | blocker / note |
|---|---|---|---|
| Edge gateway | **REAL SOURCE RUNNING** | Kong 3.4.2-ubuntu (edge/Dockerfile base) + the 42 razorpay/edge Lua plugins @ da9ff5b, kong.conf verbatim (headers=off, db=postgres); route table derived from terraform-kong @ 8ffe965b prod-api authenticated_paths (17 routes) + payouts-ext (1 route, production hosts); consumer per merchant + basic-auth-x credentials hashed by the plugin; upstream-jwt kid edgev2 signs the passport | production merchant-key sync into Kong is not in any granted repository -> seeded by trustpath/edge/provision_kong.py (PU-M11-1); global observability/rate-limit plugins not provisioned (PU-M11-2) |
| API monolith (auth / merchant identity / ownership / approval routes) | **CONTRACT-FAITHFUL REPLACEMENT (api-ingress)** | the M7 ingress, now verifying the REAL gateway passport (RS256, kid edgev2, PassportUtil validations) and publishing /jwks; Route.php / BasicAuth.php / FundAccount ownership semantics reproduced from source | razorpay/api cannot be booted here: no php/composer, 18/23 private composer VCS repos unreadable, all base images on harbor.razorpay.com need credentials (reports/implementation/m11/monolith-boot-attempt.md) |
| Shield (risk rules) | **REAL SOURCE RUNNING** | razorpay/shield @ 514516f, APP_MODE=rzpxprod (setupProvidersForRzpx), MySQL + single-node Redis cluster, the repository's own goose migrations (run once under APP_MODE=default because rzpxprod skips them by source), rules through the rule API with skip_canary | github.com/razorpay/fingerprint-sdk is unreadable -> 4-symbol build stub via -modfile (build-time only, never on the payout path); production FRM merchant list / rule set unknown (PU-M11-3) |
| banking-accounts (BAS) | **REAL SOURCE RUNNING (adapted deployment)** | razorpay/banking-accounts, APP_ENV=arena overlay, goose migrations, Api-Token auth, businesses/banking_accounts rows for Direct merchants | its DCS client hardcodes https://dcs-*.dev.razorpay.in (goutils/dcs uri.go, WithMock(false)) -> the twin answers those hostnames with dcs-stub over TLS (twin CA via SSL_CERT_FILE); production business data unknown (PU-M11-4) |
| account-service (ASV) | **REMAINING SUBSTITUTE (asv-stub)** | unchanged M6 stub | razorpay/account-service clone returns 404 for this identity -- no source |
| Workflow service (maker-checker) | **REAL SOURCE RUNNING (adapted deployment)** | razorpay/workflows Twirp API + Cadence worker (WORKFLOW_TYPE=approval, domain payouts) + ubercadence/server:v1.4.1-auto-setup on MySQL; goose migrations; payout-approval Config per merchant through ConfigAPI/Create | runs as APP_ENV=dev so the real DCS client resolves to the twin-answered dcs-live.dev.razorpay.in (stub mode leaves the worker's DCS server nil and the approval workflow panics -- source); [auth.vendorPayments] slot filled (F-M11-1); production configs / DCS features unknown (PU-M11-5) |
| Dashboard session identity | **CONTRACT-FAITHFUL REPLACEMENT (api-ingress proxy auth)** | BasicAuth::proxyAuth semantics at the monolith replacement | the api-dashboard Kong service + user-session plugin front the dashboard app, which is not a granted repository (PU-M11 dashboard) |
| Internal application ingress | **REAL PATH SHAPE (api-internal not provisioned)** | internal apps call the monolith replacement directly with rzp_live + app secret + X-Razorpay-Account | production api-internal Kong service carries plain paths (no edge authentication) -- not provisioned; equivalent to the direct call |
| External banks / SMS / OTP | **SYNTHETIC BY MANDATE** | mozart-sim, xas-sim/xas-sink, synthetic OTP | allowed to stay synthetic |

Remaining substitutes in the real variant, each with its external blocker: see gate M11-06 in the acceptance record (15 listed).

## Trust path as run

`merchant (host bridge) -> edge-kong (17 prod-api routes + 1 payouts-ext route, hosts {"prod-api": null, "payouts-ext": ["payouts-ext.razorpay.com"]}) -> api-ingress (monolith replacement; passport kid edgev2 verified) -> payouts-api -> shield-web (rzpxprod) / banking-accounts-api (Direct) / workflows-api + workflows-worker + cadence`

Placement: everything local (this machine: 15 CPU / 24.0 GiB; twin VM {"cpus": 4, "disk_gib": 40, "memory_gib": 10}). Remote compute: not used -- remote compute not available in this session (gcloud needs an interactive re-auth); every service fits the local machine.

## Fidelity numbers (before -> after)

| population | label | M10 baseline (`5b6a5dade17e`) | M11 (`8a52779f0527`) |
|---|---|---|---|
| p0_non_journey | ACTUAL_SOURCE_RUNNING | 242 | 276 |
| p0_non_journey | BEHAVIORAL_STUB | 20 | 19 |
| p0_non_journey | CONTRACT_FAITHFUL_REPLACEMENT | 141 | 141 |
| p0_non_journey | GRAPH_ONLY | 72 | 70 |
| p0_non_journey | PRODUCTION_STATE_UNKNOWN | 11 | 11 |
| p0_non_journey | SOURCE_MAPPED_NOT_RUNNING | 39 | 37 |
| p0_critical_kinds | ACTUAL_SOURCE_RUNNING | 161 | 191 |
| p0_critical_kinds | BEHAVIORAL_STUB | 14 | 14 |
| p0_critical_kinds | CONTRACT_FAITHFUL_REPLACEMENT | 98 | 98 |
| p0_critical_kinds | GRAPH_ONLY | 51 | 49 |
| p0_critical_kinds | PRODUCTION_STATE_UNKNOWN | 11 | 11 |
| p0_critical_kinds | SOURCE_MAPPED_NOT_RUNNING | 29 | 27 |
| all_nodes | ACTUAL_SOURCE_RUNNING | 797 | 850 |
| all_nodes | BEHAVIORAL_STUB | 86 | 85 |
| all_nodes | CONTRACT_FAITHFUL_REPLACEMENT | 346 | 346 |
| all_nodes | GRAPH_ONLY | 423 | 417 |
| all_nodes | PRODUCTION_STATE_UNKNOWN | 42 | 42 |
| all_nodes | SOURCE_MAPPED_NOT_RUNNING | 1371 | 1368 |

Runtime profile (snapshot-derived, `full`): real variant = 105 services + 13 one-shot jobs; substitute variant = 98 services + 5 jobs. In the real variant kong-lite, shield-stub, bankingaccounts-stub and workflow-engine are not started; edge-kong, shield-web, banking-accounts-api, workflows-api, workflows-worker, cadence and their datastores are.

Domain graph (reports/domain/GRAPH_STATS.json): 3108 nodes, fidelity histogram {"graph_only": 466, "real_source_running": 839, "real_source_mapped_not_running": 1369, "high_fidelity_replacement": 355, "behavioural_placeholder": 77, "blocked_missing_access": 2}.

## Journeys and differential

| variant | instance | run dir | total | PASS | FAIL | BLOCKED | EXPECTED_FAILURE |
|---|---|---|---|---|---|---|---|
| real | m11-real | `/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T173309Z,/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T182343Z,/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T184745Z,/Users/rana.singh/.twin-factory/instances/m11-real/journeys/run-20260909T185350Z` | 125 | 113 | 0 | 10 | 2 |
| substitute | m11-sub | `/Users/rana.singh/.twin-factory/instances/m11-sub/journeys/run-20260909T173302Z,/Users/rana.singh/.twin-factory/instances/m11-sub/journeys/run-20260909T182343Z` | 125 | 107 | 5 | 12 | 1 |

Differential (reports/implementation/M11_DIFFERENTIAL.md): 125 journeys compared, 108 identical, 17 with differences, 0 OPEN.

Blocked journeys on the real variant (each names its dependency): journey:bulk-payouts/success -> batch-sim is not reachable on the arena network -- compose service absent (docker ps: (no batch-sim container in the com; journey:bulk-payouts/failure -> batch-sim is not reachable on the arena network -- compose service absent (docker ps: (no batch-sim container in the com; journey:bulk-payouts/idempotency -> batch-sim is not reachable on the arena network -- compose service absent (docker ps: (no batch-sim container in the com; journey:bulk-payouts/cancel_or_reverse -> batch-sim is not reachable on the arena network -- compose service absent (docker ps: (no batch-sim container in the com; journey:bulk-payouts/async_state -> batch-sim is not reachable on the arena network -- compose service absent (docker ps: (no batch-sim container in the com; journey:shared-ingress/batch -> batch-sim reachable on the arena network (compose service `batch-sim`, profile `substitutes`; the M9 critical-payouts / ; journey:cross-domain-s2p/success -> S2P stack inside the arena (RED_LOOP/m7/s2p_stack.py up) with api-ingress relaying to vp-source; journey:cross-domain-s2p/failure -> S2P stack inside the arena (RED_LOOP/m7/s2p_stack.py up) with api-ingress relaying to vp-source; journey:cross-domain-s2p/idempotency -> journey:cross-domain-s2p/success must run first (it establishes the tax payment and remittance); journey:cross-domain-s2p/async_state -> S2P stack inside the arena (RED_LOOP/m7/s2p_stack.py up) with api-ingress relaying to vp-source

The 14 trust-path journeys (RED_LOOP/m6/journeys/j_trustpath.py) cover: merchant API-key auth, identity propagation (gateway-signed passport, forgery attempt), dashboard/session identity (as far as source permits), internal app auth, route-level authorization, cross-merchant denial, beneficiary/fund-account ownership, service-to-service identity (Shield/BAS/Workflow/PS), maker-checker create/approve/reject/separation, restart & cache invalidation, duplicate/idempotency, Shield rules, BAS lookup, payouts-ext.

## Findings (real components, source-explained)

- **F-M11-1** (razorpay/workflows): cmd/api/main.go accepts credentials.VendorPayments on every Twirp service while config/default.toml has no [auth.vendorPayments] block; the zero-value slot matches an anonymous request in internal/boot/hooks/auth.go (isClientAllowed("") and "" == ""). Observed: anonymous WorkflowAPI/List -> 200 until the twin filled the slot; then 401. Production value of that slot is unknown. _Observed in: trust-path/s2s_identity (first run) + the twin config fix._
- **F-M11-2** (razorpay/edge kong-plugin-basic-auth-x @ da9ff5b on Kong 3.4.2): a credential deleted through the Admin API keeps authenticating: access.lua caches by its own keys (v2:basicauth_x_consumer_id:<username>, v2:basicauth_x_by_hash:<hash>:<consumer>) that no invalidation targets, and the DAO's cache_key(username) override (basicauth_x_credentials.lua:72) crashes on the CRUD event entity ('attempt to concatenate a table value' in the gateway log) so the core hook aborts; db_cache_ttl is the default 0. Production behaviour depends on the same code unless a different cache configuration is deployed (unknown). _Observed in: trust-path/restart_cache_invalidation._
- **F-M11-3** (razorpay/workflows): ConfigAPI/List without a `config` filter object dereferences req.Config (internal/entities/config/server.go:300) -> HTTP 500 panic. _Observed in: workflow config seed (first run)._
- **F-M11-4** (razorpay/payouts + terraform-kong payouts-ext): the payouts-ext Kong service forwards the merchant's own Basic credential to the Payouts service, whose /v1/payouts group requires the API service credential first (internal/routing/router/payout_routes.go: BasicAuth(cred.API, cred.Workflow) before PassportAuthentication) -> 401 'The api key provided is invalid'. Either production carries a header rewrite not present in the granted terraform, or the route is dark; recorded as PU-M11-7. _Observed in: trust-path/payouts_ext_direct._

## Twin-side corrections made during the promotion (no real-service code touched)

- twinfactory image cache: compose-built substitute images are now fingerprinted by their build context (a stale api-ingress was being served from the M9 cache under the same tag).
- twinfactory start --resume-from <stage>; secrets/materialize.py --only <group>; host bridge relays gateway response headers, forwards production hosts (payouts-ext) and identity attack headers.
- RED_LOOP/red_loop/ledger_da.py read the main checkout's secrets on a factory twin (401 from the ledger, DA onboarding skipped -> every Direct journey failed on twins); now ARENA_ENV2_ROOT-aware.
- journey beneficiary-fund-accounts read the record through monolith-stub, whose fund_accounts_internal route was retired in M7; it now reads the shared-ingress route payouts actually reads (payouts_service identity).
- journey fetch-list expected the substitute's leniency for unsigned ids; the monolith path refuses them (PublicEntity::stripSignOrFail) -- asserted per variant.
- journey shared-payouts/restart restarts the M6 worker trio only (edge-kong/shield-web have their own journey); shared-ingress/batch and the bulk family BLOCK when the profile carries no batch-sim.
- approval / trust-path drivers: separation, repeat approvals, eligible sets are asserted per variant (real service semantics vs the M5 reconstruction), recorded in M11_DIFFERENTIAL.md.
- Shield seed: payout rules live in ruleset payout_primary (ruler.go addRulesets) and are created with skip_canary (otherwise a 0%-rollout canary rule); `purpose` is not a Shield parameter (M1 rule re-expressed on payout_amount).
- Workflow seed: template type `approval`, allowed_actions keyed by actor type, ConfigAPI/List filters inside `config`; every action carries X-User-Email and 14-char RZP actor ids.

## Measured resources (local)

| instance | label | VM | containers | CPU% sum | mem MiB sum | docker data used GiB | image cache GiB | build s | boot s |
|---|---|---|---|---|---|---|---|---|---|
| m11-real | real variant, canonical suite running | {"cpus": 4, "disk_gib": 40, "memory_gib": 10} | 99 | 137.7 | 7204.4 | 11.2 | 2.66 | 59.7 | 32.1 |
| m11-sub | substitute variant, canonical suite running | {"cpus": 4, "disk_gib": 40, "memory_gib": 8} | 92 | 171.7 | 5072.7 | 8.1 | 2.14 | 64.8 | 65.6 |

Trust-path images (MiB): {"rzp-arena/banking-accounts:v1-candidate": 42, "ubercadence/server:v1.4.1-auto-setup": 181, "rzp-arena/edge-kong:v1-candidate": 312, "rzp-arena/shield:v1-candidate": 73, "rzp-arena/workflows:v1-candidate": 30}. Boot time of a full real-variant boot from empty volumes: see the clean-checkout record below (the m11-real row's boot figure is the resumed stage only).

## Monolith boot attempt

bootable here: **False** -- no PHP interpreter / composer on this machine (and no credential-free base image carries them: see images); 18 of 23 private composer VCS repositories are not readable with the current identity (composer install cannot resolve spine, upi-clients, trace, oauth, ufh-sdk-php, dcs-php-sdk ...); 3 of 3 Dockerfile base images are not pullable without registry credentials (harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-api-curl-v4, harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-php-8.2-opencensus, harbor.razorpay.com/razorpay/rzp-docker-image-inventory-multi-arch:rzp-legacy-image-php-8.2-api-web-v4) (reports/implementation/m11/monolith-boot-attempt.md)

## Clean-checkout reproduction

{"ok": true, "checkout": "/var/folders/4j/3__3lvhx6mn9gyc9pgr91fgc0000gp/T/m11-clean-c5uiuljh", "instance_id": "m11-clean", "state": "running", "trust_path": "real", "healthy": true, "secs": 241.8, "note": "factory image cache shared (content-addressed); config/secrets/seeds/Kong provisioning/migrations produced from the fresh checkout"}

## Production unknowns (explicit)

- **PU-M11-1**: how merchant API keys are synchronised into Kong's basicauth_x_credentials in production (Credcase / secret_ref_id flow; no granted repository) -- the twin seeds consumers + credentials from its own merchant seed
- **PU-M11-2**: production values of the global Kong plugins (rate limits, Redis cluster, lake-events, geo-router) -- not provisioned
- **PU-M11-3**: Shield production rule set, FRM merchant ids (FRM_MERCHANT_IDS), shieldAuthUser pairs -- the twin seeds its own rules (ruleset payout_primary, the one ruler.go addRulesets selects for payouts; stable rules via skip_canary) and credentials
- **PU-M11-4**: banking-accounts production business / banking-account rows and the [api].token value -- twin-seeded
- **PU-M11-5**: Workflow service production configs (per-merchant approval templates), DCS feature values (DcsSkipApprovalForCreator, wf config dimensions), every [auth.*] and [clients.*] credential -- twin secrets
- **PU-M11-6**: Shield schema provisioning in RzpX environments (migrations skip under rzpxprod by source) -- the twin runs the repository migrations once under APP_MODE=default
- **PU-M11-7**: how payouts-ext traffic satisfies the Payouts service's service BasicAuth (see F-M11-4)
- **PU-M11-8**: the monolith's /wf-service/state/callback route (payouts reverse dual write of workflow state maps) -- not in the api-ingress contract; payouts logs the 404 as a non-fatal RDW error
- **PU-M11-9**: the API monolith itself: passport registrations, session policies, route flags and everything Route.php reads from production config -- api-ingress reproduces the contract, never the values

## Paths

- `reports/implementation/M11_FINAL_REPORT.md`
- `reports/implementation/M11_RUNBOOK.md`
- `reports/implementation/M11_ACCEPTANCE.json`
- `reports/implementation/M11_ARTIFACT_HASHES.json`
- `reports/implementation/M11_DIFFERENTIAL.md`
- `reports/implementation/m11/`
- `ENV2_COMPOSE/docker-compose.m11.yml`
- `ENV2_COMPOSE/trustpath/`
- `ENV2_COMPOSE/build/m11/`
- `RED_LOOP/m6/journeys/j_trustpath.py`
- `RED_LOOP/m6/journeys/trustpath_adapter.py`
- `scripts/m11/`
