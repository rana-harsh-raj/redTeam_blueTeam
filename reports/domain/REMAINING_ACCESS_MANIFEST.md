# Remaining-access manifest — what prevents the final ~1% of Payouts fidelity

Generated 2026-09-08T02:27:35.550843+00:00 by scripts/domain/inventory.py.
Every item is a specific export, schema, config value, or repository the twin cannot derive from the
readable clones. Nothing here asks for production access.

## A. Repositories not readable by this identity

| repository | role for payouts | fallback in twin |
|---|---|---|
| razorpay/charge-collections-sdk | Client library imported by payouts (pkg/ccSdk) | see graph node fidelity (graph_only / substitute) |
| razorpay/tax-compliance | TDS/compliance (payouts publishes add-tds-entry only) | substitute: F0 | see graph node fidelity (graph_only / substitute) |
| razorpay/master-onboarding | merchant onboarding; not on payout execution path | substitute: F0 (seed data) | see graph node fidelity (graph_only / substitute) |
| razorpay/catalyst | IRCTC settlement flow (out of scope) | substitute: F0 | see graph node fidelity (graph_only / substitute) |
| razorpay/qa-tools | QA tooling | substitute: own fixtures | see graph node fidelity (graph_only / substitute) |
| razorpay/e2e-test-orchestrator | DevStack E2E orchestrator | substitute: own scripts | see graph node fidelity (graph_only / substitute) |
| razorpay/frontend-graphql | seed hypothesis; not referenced by any payouts client | substitute: F0 | see graph node fidelity (graph_only / substitute) |
| razorpay/asv | Account Service gRPC (merchant activated/hold_funds); `account-service` name also 404 | substitute: ASV stub (activated=true, hold_funds=false) from goutils/account-service contract | see graph node fidelity (graph_only / substitute) |
| razorpay/account-service | see asv | substitute: — | see graph node fidelity (graph_only / substitute) |
| razorpay/kong-plugin-user-auth | passport/user auth Kong plugin (edge repo has references) | substitute: Kong-lite from terraform-kong + edge | see graph node fidelity (graph_only / substitute) |
| razorpay/kong-plugin-consumer-identifier | consumer identification plugin | substitute: same | see graph node fidelity (graph_only / substitute) |
| razorpay/kong-utils | Kong plugin utils | substitute: same | see graph node fidelity (graph_only / substitute) |
| razorpay/scrooge | settlement/recon engine (FTS caller SCROOGE) | substitute: F0 | see graph node fidelity (graph_only / substitute) |
| razorpay/settlements | settlements service (FTS caller) | substitute: F0 | see graph node fidelity (graph_only / substitute) |
| razorpay/raven | SMS service (payouts pkg/raven) | substitute: Mock=true / capture stub | see graph node fidelity (graph_only / substitute) |
| razorpay/ufh | file service (xperience) | substitute: F0 (Env 5) | see graph node fidelity (graph_only / substitute) |
| razorpay/beam | file relay (FTS JPMC) | substitute: F0 | see graph node fidelity (graph_only / substitute) |
| razorpay/razorx | FTS flag engine | substitute: stub returning defaults | see graph node fidelity (graph_only / substitute) |
| razorpay/wda-service | TiDB data access service | substitute: MySQL stand-in for TiDB | see graph node fidelity (graph_only / substitute) |
| razorpay/vault | card token vault | substitute: Mock=true (card mode out of Env 2) | see graph node fidelity (graph_only / substitute) |
| razorpay/hvault | secrets vault (payload encryption keys) | substitute: Mock=true (payload encryption off) | see graph node fidelity (graph_only / substitute) |
| razorpay/workflow-service | name does not exist; the repo is `workflows` | substitute: use `workflows` | see graph node fidelity (graph_only / substitute) |
| razorpay/integrations-utils | Go dependency of `mozart` | substitute: Mozart binary cannot be built ⇒ `mozart -mock` is unavailable; arena uses the Python `mozart-sim` substitute instead | see graph node fidelity (graph_only / substitute) |
| razorpay/integrations-go | Go dependency of `mozart` | substitute: same | see graph node fidelity (graph_only / substitute) |
| razorpay/orchestrator | Go dependency of `mozart` | substitute: same | see graph node fidelity (graph_only / substitute) |

## A2. Clone-root decay (2026-09-08)

The 2026-09-03 clone batch lived in a macOS /tmp scratchpad and was partially purged (git objects lost, working
files partial). Surviving files were copied to the durable path in `.local/repos-root`; the five core services are
unaffected (admitted build copies `.local/twin-repos/accepted/*` are complete and provenance-verified). Re-fetching
these repositories at their pinned SHAs (recorded in `reports/domain/SNAPSHOT_MANIFEST.json` / ACCESS_DELTA) restores
full source; until then their graph nodes rest on what the discovery lanes read before the purge.

| repository | pinned sha | working files left |
|---|---|---|
| razorpay/razorpay-mcp-server | ? | 5 |

## B. Graph nodes classified `blocked_missing_access`

| node | why blocked (evidence) |
|---|---|
| `sub:mozart-mock` | container env2_compose-mozart-mock-1 = NOT CREATED. BLOCKED: `go build` of the mozart clone 404s on the private module github.com/razorpay/integrations-utils for this build identity. Image rzp-arena/mozart:v1-candidate i |
| `svc:mozart-mock-mode` | same build blocker as svc:mozart; fixtures readable (mozart/app/testdata/fts/**, 53 gateway/version/action dirs, 546 scenarios) |

## C. Non-repository artefacts (runtime values, schemas, exports)

| category | artefact | why needed | minimum request | fidelity without it |
|---|---|---|---|---|
| A. Effective runtime configuration | Per-merchant DCS feature values for test merchants | Approval applicability, reservation gating, IFSC lookup and payload encryption all branch on these; recurring prod bug = | read-only DCS query for a named merchant set (or export) | Approval decisions unverifiable; workflow-bypass bug class not reproducible |
| A. Effective runtime configuration | Splitz experiment definitions + variant assignments | Routing (monolith vs PS), FTS webhook destination, Kafka vs HTTP status path, idempotency enforcement all depend on per- | read access to experiment definitions + evaluate API for test merchants | Cannot reproduce which of two status paths (direct webhook vs monolith relay) a merchant is on = the |
| A. Effective runtime configuration | Production TOML overlays + env-var values (non-secret) for each service | Timeouts/retries/hosts are in-repo, but env overrides, cron schedules and vault overlay values are not | read on rendered manifests / config maps for one env (stage or func) | Cron cadence (queued/scheduled/on_hold dispatch, reservation reconcile, dual-write failure) unknown  |
| A. Effective runtime configuration | Merchant migration flag state | Determines whether a merchant's payouts execute in PS vs legacy; balance authority; FTS origin | read of feature table for synthetic merchants | Legacy-path behaviour unverifiable without monolith |
| B. Gateway and edge configuration | Effective Kong routes/plugins for payouts and dashboard-proxy routes | Decides which upstream serves a route and what identity claims reach PS; the identification-only-passport problem lives  | read on terraform-kong repo (or rendered Kong decK export for stage) | Edge→PS cutover path (flow G) cannot be reproduced; passport claim shapes must be guessed |
| B. Gateway and edge configuration | Passport JWT signing/JWKS for edgev1 and apiv1 identities | PS/xperience/ledger validate X-Passport-JWT-V1; env needs a signer + JWKS | JWKS for stage + ability to mint test passports (or a test signing key) | Auth-type allowlists (private/proxy/privilege/admin) unverifiable if claim shape wrong |
| C. Identity and authorization | Deployed AuthZ policies for X dashboard roles | Maker/checker and role gating; open question whether banking::non_lms org is enforced for dashboard traffic | read on authz repo or policy export for org razorpayx | Role enforcement is client-side only in x SPA; server-side enforcement cannot be validated |
| C. Identity and authorization | Service-to-service Basic Auth credential inventory (names only) and monolith→PS  | Env needs matching credential pairs on both sides; ACLs are per-username | none — generate synthetic creds | None if usernames preserved |
| C. Identity and authorization | Admin-dashboard permission → server-side enforcement mapping | Server-side revalidation of admin permissions is in the monolith (inaccessible); PS manual_action has no passport-admin  | read on api repo admin middleware | Admin blast radius unverifiable |
| C. Identity and authorization | Kubernetes ServiceAccounts / IAM roles for SQS/SNS/Kafka access | Workers consume SQS/Kafka; env needs equivalent permissions | list of queue/topic names + consumer groups (no creds) | None material if names preserved |
| D. Data definitions | Effective DB schemas + indexes for all F3 services | Migrations can rebuild own DBs; monolith DB and TiDB schemas needed for fallback-read paths (cfa APIStore, x-balances AP | schema-only dump (no rows) of api DB tables referenced + TiDB payouts_temp DDL | Fallback-read paths (merchant not yet in x-balances/cfa) degrade |
| D. Data definitions | CDC/outbox topology | Determines downstream observers; none mutate payout state in core flows | topic inventory | Analytics/billing not represented |
| E. Build and deployment | Private Go modules | Cannot compile ANY F3 service without them | read on the module repos (or GOPROXY/GONOSUMDB access) + BSR read | go.mod scan; 10 §6 |
| E. Build and deployment | Rendered manifests / Spinnaker pipelines / image digests | Reproduce process topology and cron cadence; base images for builds | read on kube-manifests + spinacode; pull on Harbor for base images | Timing behaviour unverified |
| F. Event infrastructure | Topic/queue inventory with consumer groups and DLQs | Must recreate for workers; ordering/DLQ semantics affect status propagation | DESCRIBE on topics; queue list | Low if names/partitions preserved |
| G. Bank and third-party integrations | Mozart API contract for FTS's 8 operations + per-bank response/error-code catalo | F2 bank simulator must reproduce the state transitions FTS keys on | read on mozart repo OR the request/response schema per gateway | Bank edge cases (delayed success, ambiguous) only as good as recorded fixtures |
| G. Bank and third-party integrations | Bank sandbox availability / certificate metadata | Only if real bank connectivity desired (not for env 1) | none for env 1 | n/a |
| H. Runtime evidence | Sanitized traces/logs of successful + failed payout journeys | To confirm which path (direct vs relay, Kafka vs HTTP) real traffic takes and timings | read-only Coralogix for stage/prod payouts, fts, ledger | PRODUCTION_REPRESENTATIVE status unattainable |
| I. Testing | DevStack / ITF E2E setup, test-data manager merchants, mock gateway | Reuse golden flows and fixtures; real payouts→FTS→Mozart→mock-gateway chain already exists in ITF | devstack namespace + orchestrator access; e2e-test-orchestrator & qa-tools repo  | Slower; risk of drift from golden flows |
| I. Testing | Fault injection / bank failure simulation | Flow F/I coverage | access to mock-gateway config | Reduced edge-case coverage |
| J. Service catalog and ownership | Service→repo→team→on-call mapping | Route access requests and unresolved questions | read on razorpay/knowledge-base | Ownership gaps for fts, cfa |
| J. Service catalog and ownership | API monolith source for payout/proxy/auth/admin/workflow modules | Monolith is in-path for dashboard/API/variant/admin flows and legacy FTS relay; cannot be substituted by F1 | read on razorpay/api | Flows A, C (legacy), G, admin paths remain CODE_CANDIDATE at best |

## D. Consolidated P0/P1 information requests

See `reports/fidelity/REMAINING_INFORMATION_REQUESTS.md` (24 items, owners + minimal request each).
Top items still open after M6:

1. Splitz experiment variant/rollout values (runtime, Splitz MySQL only) — twin stub seeds recorded assignments.
2. FastCron per-endpoint cadence for payouts `/v1/cron/*` — twin cron-driver cadence is assumed.
3. Real DCS values per merchant segment — twin seeds illustrative profiles.
4. recon `workflow_configs` rows — ART repair substitute is contract-level only.
5. Cadence server version + production WFS config templates — approval engine is a reconstruction.
6. Private Go module `integrations-utils` — real Mozart binary unbuildable; mozart-sim substitute.
7. `asv` (account service) repo — ASV stub from goutils contract.
