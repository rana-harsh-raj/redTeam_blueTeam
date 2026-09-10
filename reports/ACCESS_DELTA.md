# ACCESS_DELTA — 2026-09-04 vs 2026-09-03 baseline

Identity: GitHub `rana-harsh-raj` (org member; repository-level grants). Method: `gh repo list razorpay --limit 2000`, `gh api repos/razorpay/<name>`, shallow clones (`--depth 1`), `go mod download` for private modules with the `gh` git credential helper.

| Metric | 2026-09-03 | 2026-09-04 |
|---|---|---|
| Listable repositories | 221 | 252 |
| Private visible | 29 | 60 |
| Internal visible | 15 | 15 |
| Public | 177 | 177 |

## Newly readable and cloned (all default branch `master` unless noted)

| Repository | Description | Clone | Commit inspected | Role for Payouts |
|---|---|---|---|---|
| razorpay/actions | host for all centralized github actions | not attempted |  |  |
| razorpay/alert-rules | Prometheus alert rules repo | OK | c372beabedf8 | Prometheus alert rules |
| razorpay/api | The backbone of Razorpay | OK | 2d665f918b60 | API monolith — in-path proxy, dashboard auth, legacy FTS relay, admin, pricing/merchant config |
| razorpay/authz | Razorpay's Authorization framework | OK | 8687f48f0b76 | Authorization policies (X roles) |
| razorpay/batch | Razorpay Batch service | OK | 3839dd77d061 | Batch service (bulk payouts) |
| razorpay/business-verification-service-sdk-go | Go sdk for business verification service | OK | fdc41fa46fd6 | BVS SDK |
| razorpay/config-proto | Proto Repository for Razorpay's Dynamic Configurations. | OK | a6b201039f22 | DCS schema (rzp/x/merchant/payouts/*) |
| razorpay/dcs | Razorpay's Solution for Dynamic Configurations. | OK | fca59e3e6830 | Dynamic config service |
| razorpay/edge | Razorpay's API Gateway at the Edge | OK | da9ff5b22b7d | Edge gateway code |
| razorpay/error-mapping-module | Error Code Mapping Library | OK | 53ce867663a1 | Error mapping lib |
| razorpay/go-foundation-v2 | Best in class - golang service - refer it and make it better! | OK | ce72f28adabd | Foundation service template |
| razorpay/goutils | Repository of Shared Golang Packages - telemetry, storage, configloader, validation and also contains SDKs of services: account-service, authz, dcs, splitz, bin-service, and likes | OK | efc794188bbd | Shared Go libs: passport, authz, dcs, splitz, worker, kafka, spine, wda, telemetry |
| razorpay/governor | Razorpay Rule Engine Service | OK | 875eabaf2d5f | Rule engine (pricing) |
| razorpay/governor-executor | Rules execution library of Governor | OK | 1bd640c2a277 | Governor rules executor |
| razorpay/knowledge-base | Git-backed Curated Knowledge Base for Agents | OK | 70f78efa8b26 | RKG / Graphify knowledge graphs (payouts platform PR #157) |
| razorpay/kube-manifests |  | OK | 9226a892fd76 | Rendered manifests, CronJobs, env vars |
| razorpay/ledger-sdk | Ledger SDK | OK | 2eae8920c73c | Ledger client used by payouts |
| razorpay/mozart | Razorpay Gateway Integration Framework | OK | bf4688300d48 | Bank gateway framework (FTS/x-balances/XAS/ValidX target) |
| razorpay/payments-upi | Razorpay UPI Payments Service | OK | b23ec7ea9126 | UPS (VPA validation) |
| razorpay/pg-sdk | PG-Router SDK | OK | 9dab0077947a | PG router SDK |
| razorpay/proto | Protobuf definitions for Razorpay services | OK | 52682577d79d | Protobuf contracts (Stork, ledger, payouts, x-*, validx) |
| razorpay/recon | Recon Product | OK | 6974fec90f78 | Recon/ART |
| razorpay/rpc | description: repo to keep all generated rpc packages | OK | 27388ee63351 | Generated RPC packages |
| razorpay/rzpconv | Razorpay semantic conventions   | OK | 3a9e5712d8e5 | Semantic conventions (rzpconv/payouts used by XAS) |
| razorpay/shield | Razorpay's Risk Engine Service | not attempted |  |  |
| razorpay/shield-sdk | SDK for Shield (Razorpay's Risk Engine Service) | OK | 1a4cbf769173 | Shield risk client |
| razorpay/spinacode | Spinnaker Pipelines As Code | OK | da46a5239144 | Spinnaker pipelines |
| razorpay/splitz | A/B testing Backend | OK | fa6f4c7ee13d | Experimentation backend |
| razorpay/stork | Stork - Razorpay's Notification service | OK | 800b719030aa | Merchant webhook/SMS delivery |
| razorpay/terraform-kong | Terraform repo to configure Kong running on Razorpay's edge | OK | 8ffe965bce78 | Edge routes/plugins incl. payouts-proxy-cutover |
| razorpay/workflows | Razorpay Workflow Service | OK | 080d71a51b77 | Workflow Service (approvals) |

Note: `shield` (6.7 GB) was granted but not cloned; `payouts/pkg/shield` + `shield-sdk` are used instead. `rpc` needed `-c core.ignorecase=false` because of case-colliding paths on APFS.

## Still not accessible (404 to this identity)

| Repository | Why it matters | Substitute |
|---|---|---|
| razorpay/tax-compliance | TDS/compliance (payouts publishes add-tds-entry only) | F0 |
| razorpay/master-onboarding | merchant onboarding; not on payout execution path | F0 (seed data) |
| razorpay/catalyst | IRCTC settlement flow (out of scope) | F0 |
| razorpay/qa-tools | QA tooling | own fixtures |
| razorpay/e2e-test-orchestrator | DevStack E2E orchestrator | own scripts |
| razorpay/frontend-graphql | seed hypothesis; not referenced by any payouts client | F0 |
| razorpay/asv | Account Service gRPC (merchant activated/hold_funds); `account-service` name also 404 | ASV stub (activated=true, hold_funds=false) from goutils/account-service contract |
| razorpay/account-service | see asv | — |
| razorpay/kong-plugin-user-auth | passport/user auth Kong plugin (edge repo has references) | Kong-lite from terraform-kong + edge |
| razorpay/kong-plugin-consumer-identifier | consumer identification plugin | same |
| razorpay/kong-utils | Kong plugin utils | same |
| razorpay/scrooge | settlement/recon engine (FTS caller SCROOGE) | F0 |
| razorpay/settlements | settlements service (FTS caller) | F0 |
| razorpay/raven | SMS service (payouts pkg/raven) | Mock=true / capture stub |
| razorpay/ufh | file service (xperience) | F0 (Env 5) |
| razorpay/beam | file relay (FTS JPMC) | F0 |
| razorpay/razorx | FTS flag engine | stub returning defaults |
| razorpay/wda-service | TiDB data access service | MySQL stand-in for TiDB |
| razorpay/vault | card token vault | Mock=true (card mode out of Env 2) |
| razorpay/hvault | secrets vault (payload encryption keys) | Mock=true (payload encryption off) |
| razorpay/workflow-service | name does not exist; the repo is `workflows` | use `workflows` |

## Private Go modules

`go mod download` succeeded for payouts, ledger, fts, cfa and x-balances with `GOPRIVATE=github.com/razorpay/*` and the `gh auth git-credential` helper (see `findings/28_build_spike.md` for compile results). The previous hard build blocker is removed.

## Host tooling changes made in this pass (assumptions)

- Installed via Homebrew: `colima`, `docker` (CLI), `docker-compose`, `docker-buildx`; started a Colima VM (4 CPU / 12 GB / 60 GB, Virtualization.framework, Linux arm64). Docker server 29.5.2 runs inside the VM, which is the disposable Linux VM the safety gate requires.
- `go env -w GOPRIVATE=github.com/razorpay/* GONOSUMDB=github.com/razorpay/*`.
- `gh auth setup-git` (credential helper already present).

## Discovered during the build spike (2026-09-04 evening)

| Repository | Why it matters | Effect |
|---|---|---|
| razorpay/integrations-utils | Go dependency of `mozart` | Mozart binary cannot be built ⇒ `mozart -mock` is unavailable; arena uses the Python `mozart-sim` substitute instead |
| razorpay/integrations-go | Go dependency of `mozart` | same |
| razorpay/orchestrator | Go dependency of `mozart` | same |

Private modules that resolved fine via `go mod download`: goutils (all sub-modules, incl. per-package tags), ledger-sdk, config-proto, rpc, error-mapping-module, governor-executor, pg-sdk, shield-sdk, rzpconv, business-verification-service-sdk-go, charge-collections-sdk. Public module `razorpay/ifsc/v2@v2.0.43` has a stale go.sum hash in `payouts` (upstream tag re-pointed); the arena builds payouts from a repo copy with the two `go.sum` lines re-recorded (`GOFLAGS=-mod=mod`).
