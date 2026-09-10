# 27 — recon (ART), knowledge-base (RKG), payments-upi (UPS VPA contract), devstack

Repos read (read-only): `recon` (razorpay/recon), `knowledge-base` (razorpay/knowledge-base),
`payments-upi` (razorpay/payments-upi), `devstack` (razorpay/devstack), plus spot checks in
`fts` and `payouts` for cross-repo confirmation. All paths below are under
`/private/tmp/claude-502/-Users-rana-singh/cca012eb-0163-4ad1-8765-4fb6bafddfe7/scratchpad/rzp-payouts-architecture/`
unless given as an absolute `/Users/rana.singh/rzp-payouts-architecture/reports/...` path.

---

## A. recon (ART) — what it is, stack, read paths, WRITE paths, cron, ownership, Env 4 verdict

### A.1 What it is

`recon` = **ART (Automated Reconciliation Tool)**, Razorpay's centralized reconciliation
platform. It is **not** a payments/payouts execution service — it is a 3-way matcher: it
ingests bank MIS files (email/SFTP/manual upload), matches them against internal payment/payout
records with rule-based (JSONLogic) logic run on Spark/EMR, and writes a reconciliation verdict
(`recon_status`, `art_remarks`) back to itself and, via defined channels, to downstream systems.

- `recon/README.md:1-13` — "Recon Platform... recon product offering from razorpay."
- `recon/ARCHITECTURE.md:1-8` — "describes high-level overview of components present in ART."
- `recon/.agents/skills/repo-skill/core/boundaries.md:11` — "ART... is the single source of
  truth for reconciliation state across all payment methods including UPI, cards, netbanking,
  wallets, EMI, and payout products (RazorpayX)."

### A.2 Stack

- Python 3.9, **Flask** web app (Flask-RESTful/RESTX, gunicorn WSGI) —
  `recon/app/requirements.txt` (Flask==1.1.1, gunicorn==20.0.4, Flask-RESTful==0.3.9).
- SQLAlchemy ORM over **Postgres (RDS)** — `recon-postgres` — plus **recon-mysql-pg** and
  **recon-mysql-rx** (two MySQL instances, one per rail/workspace family) —
  `recon/ARCHITECTURE.md:27-31`.
- **Kafka** (confluent-kafka client) for ingestion/matcher/workflow triggers.
- **RQ** (Redis Queue) + RQ-Scheduler for async jobs and cron —
  `recon/app/requirements.txt` (`rq==2.0.0`, `Flask-And-Redis`).
- **Spark/PySpark** on **Databricks/EMR** for the actual ingestion + matching engine.
- **Elasticache** (Redis) as cache/queue backend.
- 8 deployments: `recon-file-fetch-sqs`, `recon`, `recon-workflow-kafka-consumer`,
  `recon-rq-scheduler`, `recon-rq-worker`, `recon-spark-job-orchestrator`,
  `recon-post-recon-service`, plus `recon-prs-kafka-consumer` (from `servicetree.yaml`
  `components:`) — `recon/ARCHITECTURE.md:11-19`, `recon/servicetree.yaml:44-54`.
- 5 Databricks job types: Internal File Fetcher, Ingestor, Matcher, Journal Sync, Recon Report
  Generator — `recon/ARCHITECTURE.md:20-21`.

### A.3 How it reads payouts/FTS/XAS data

- **Not** a live DB or synchronous-API reader of payouts/FTS/XAS for its core matching input.
  Internal-entity (payment/payout) data is pulled via **Spark SQL batch jobs against the data
  lake/Hive**, written to S3, and re-ingested — `recon/scripts/data_fetching/data_fetching_templates.py:1`
  (`spark.sql("""${query}""")...write.csv(f's3://.../recon/input/${workspace_name}/internal_entity/...')`).
  This matches `boundaries.md:213-218`: "API Monolith / FTS Service ... Data fetching scripts
  (Spark)".
- Direct synchronous **read/write HTTP calls to FTS** exist only in the write path (§A.4) —
  there is no separate "read" client for FTS transfer/attempt data beyond what the write-path
  workflow actions request (e.g. `ActionVerify`, `ActionProcess`).
- **No direct XAS (x-account-statements) integration found** in `recon` (`grep -rniE "\bxas\b"`
  over `app/`, `matcher/`, `scripts/` returned nothing). The XAS-adjacent write path referenced
  in our own graph (`ep:bas-recon-payout-update`, `POST /v1/payouts/banking_account_statement/payout_update`)
  is **not** called from this repo — it is XAS's/monolith's own "BAS recon" module, unrelated to
  ART. This closes part of UNRESOLVED_QUESTIONS #31's ambiguity: ART does not touch that path.
- `recon`'s primary interaction with the monolith is a **dual-write sink** (matcher batches →
  `batch` service → `pg-router` → monolith recon columns), not a read — `recon/ARCHITECTURE.md:96-101`.

### A.4 Every WRITE path into FTS, payouts, ledger, monolith — CAN it change payout/transfer status?

**Yes — confirmed, with file:line evidence on both sides (recon caller, FTS callee).**

**Mechanism (recon side) — generic, config-driven HTTP dispatcher, not hardcoded endpoints:**

1. Matcher produces an "RX Workflow" file after a 3-way match; FinOps downloads it via Slack,
   reviews/edits it, and re-uploads it through **Admin Dashboard → ART Self-Serve module**
   (`recon/ARCHITECTURE.md:82-88`, section "RX Workflows").
2. `recon` (web) turns each row into a Kafka message via
   `recon/app/post_recon_service/workflow.py:create_kafka_push_payload` (`workflow.py:106-112`):
   the message is `{end_point, service_name, method, payload}` where `end_point` is a **template
   string from a DB-stored `workflow_config`** (e.g. `/v1/internal/{0}/reconcile/`, per the
   in-code example at `workflow.py:28`), `{0}` substituted from row data
   (`generate_workflow_endpoint`, `workflow.py:114-120`), and `payload` is built + JSON-schema
   validated from the row (`get_json_payload_from_row_data`, `workflow.py:69-88`).
3. `recon-workflow-kafka-consumer` consumes this and dispatches a **fully generic HTTP request**
   — method, URL, and JSON body all taken verbatim from the Kafka payload — with retries (5x,
   backoff on 500/502/503/504) via
   `recon/app/workflow_kafka_consumer/processor/reconciliation_workflow.py:1-45` and
   `recon/app/workflow_kafka_consumer/processor/base.py:28-56` (`WorkflowProcessor.send_request`).
4. `service_name` must be one of `['FTS', 'API', 'PRS']`
   (`reconciliation_workflow.py:11,23`). For `FTS`, auth is HTTP Basic with
   `RECON_FTS_LIVE_USER` / `RECON_FTS_LIVE_SECRET` against
   `RECON_FTS_LIVE_BASE_URL = "https://fts-live.razorpay.com/v1"`
   (`recon/app/workflow_kafka_consumer/processor/consumer_service/fts.py:1-21`,
   `recon/app/config/app.env.toml:42`).
5. No literal `/v1/attempts/...` string is checked into `recon` — the actual endpoint is a
   runtime DB config (`workflow_configs` table), not visible in the repo. This is the one part
   of UNRESOLVED_QUESTIONS #32 that stays open (§ "Unknowns").

**Confirmation (FTS side) — recon is a first-class, directly-authorized caller of the
status-mutating attempts routes:**

- `fts/internal/routing/router/route_list.go:172-181`:
  ```go
  group: "v1/attempts",
  middleware: BasicAuth(constants.APIAuth, constants.ARTAuth),
  endpoints: [
    {PATCH, "/:action", controllers.AttemptBulkAction.Patch},
    {POST,  "/:action", controllers.AttemptBulkAction.Post},
    {PUT,   "/reconcile", controllers.AttemptBulkAction.Put},
  ]
  ```
- `fts/internal/constants/authusers.go:6`: `ARTAuth = "ART" // ARTAuth: Basic auth key for recon service`.
- `fts/internal/transfer/service.go:79,84,891-895` — `PerformAttemptAction`: action
  `"update"` (`ActionUpdate`) and `"safe_update"` (`ActionSafeUpdate`) both
  `service.Queue.Publish(ctx, constants.ProcessAttemptUpdate, 0, gatewayRefNo, string(bytes))`
  — i.e. **enqueue an attempt status update**, which is FTS's normal mechanism for mutating
  attempt (and, via the attempt→transfer→payout status cascade, transfer/payout) status.
  `ActionReconcile` (channel-level reconcile-from-file) and `ActionProcess` (bulk file/batch
  attempt processing) are also reachable through the same route.
- Deployment is live, not dead code: `kube-manifests/templates/recon/templates/workflow_consumer_deployment.yaml`
  and `hpa_kafka_consumer.yaml` exist, matching the `recon-workflow-kafka-consumer` component
  declared in `recon/servicetree.yaml:44-54`.

**Net effect: recon CAN and DOES have a live, authenticated write path that can change FTS
attempt status (and therefore payout/transfer status)** — gated by a human (FinOps
review-and-reupload of the RX Workflow file), not automatic on every match, but real and wired
to prod. `recon/.agents/skills/repo-skill/core/boundaries.md:238` ("Post-recon status updates
only") and its own "What ART Does NOT Do" table (`boundaries.md:150-198`, e.g. "Changing payment
status to captured/failed... Only update recon_status field") describe intent/scope for
*payments*, not a technical restriction — the boundaries doc's own Downstream Systems table
(`boundaries.md:211`) says plainly: **"FTS Service | Payout status updates | Workflow Kafka ->
FTS API"**.

**Monolith write path (separate from FTS):** dual-write of `reconciled_at` via `batch` service
→ `pg-router` (`recon/ARCHITECTURE.md:96-101`); this changes a `reconciled_at`/recon-status
column, not payout/transfer state directly. **No ledger write path found** in this repo.

### A.5 Crons

- Per-workspace ingestion/matching cadence is **DB/RQ-Scheduler-driven, not hardcoded**: any
  workspace can register an RQ cron via `recon/app/web/services/cron_job.py:9-42`
  (`CronJob.create` → `RQClient().enqueue_cron_job(cron_string, jobs.<method>, RECON_CRON_QUEUE, ...)`),
  keyed by `merchant_id`/`workspace_id`.
- Separately, whitelisted **system-wide** crons exist for internal ops:
  `monitor_long_running_emr_jobs`, `monitor_ingestion_delay`, `monitor_recon_start_delay`
  (`recon/app/web/controllers/system_cron_job_controller.py:34-40`) — these are ART's own
  pipeline-health crons, not payout-status crons.
- No FTS/payouts-status-changing cron was found in this repo (the only status-mutating write
  path is the human-gated RX-Workflow → Kafka → FTS path in §A.4).

### A.6 Ownership

`recon/servicetree.yaml:1-40`:
- `service: recon`, `type: service`, **`tier: T1`**, `lifecycle: live`.
- `domain: payments-platform/recon`; `owner.team: recon-dev`; org group/pod: "Payments
  Platform".
- Slack `#fin_infra_recon`; runbook `razorpay/rzp-oncall-agent/.../recon/alert_runbook`;
  CODEOWNERS: `recon/.github/CODEOWNERS:1` → `* @razorpay/recon-dev` (single team owns
  everything, no path-scoped owners).
- Note: `escalation_l1` (EM) is flagged in the servicetree comment as SuccessFactors
  "TERMINATED" — no active EM at time of writing; escalate via L2 (`vivek.agarwal@razorpay.com`).

### A.7 Verdict — does recon belong in the Env 4 closure, at what tier?

**Yes, it should be pulled into the Env 4 (`env4_failure_retry_reversal_async_status`) closure
list, not left at the blanket F0 the current `PAYOUTS_CLOSURE.yaml:252-261` assigns it.**

The closure_rule itself (`PAYOUTS_CLOSURE.yaml:6-11`) explicitly includes any component that
can "...reconcile or repair status..." — recon's `ActionUpdate`/`ActionSafeUpdate`/
`ActionReconcile` write path against `v1/attempts/:action` and `/reconcile` is precisely that
capability, and it is specifically material to Env 4 (failure/retry/reversal/async-status),
which is exactly where a human-triggered "repair a stuck/failed attempt from a bank file"
workflow matters most.

Recommend: **tier F2** for Env 4 only (protocol-faithful substitute of the narrow mechanism:
given an operator-approved workflow-config row, fire `PATCH/POST /v1/attempts/:action` or
`PUT /v1/attempts/reconcile` to FTS with ARTAuth Basic auth, honoring the same action
semantics) — not F3, since the full Spark/EMR ingestion-and-3-way-matching engine that produces
the RX Workflow file in the first place is not needed to exercise Env 4's failure/retry/repair
scenarios; a test harness can seed the workflow_configs row + Kafka message directly. Env 1/2/3/5
can keep recon at F0 (its old rating: "post-hoc 3-way match; drives manual repair only" is
accurate for those flows, where the human-gated repair path is out of scope).

---

## B. knowledge-base (RKG) — structure, payouts platform bundle, comparison

### B.1 Repo structure

- `specs/razorpay-knowledge-graph-spec.md` — normative RKG spec.
- `knowledge/domains/<domain>/` — one RKG "bundle" per domain (payouts is one of 27 domains:
  ai-platform, banking-programs, billme, cards-recurring, checkout, checkout-service,
  cross-border, datastores, devops-productivity, emandate, engagehq, i18n, magic-checkout,
  observability, offers, onboarding, **payouts**, payroll, pg-growth, platforms, pos-payments,
  pos-reporting, razorpayx, rewards-marketplace, spinnaker, test, upi, vishnu, wallet).
- `graphs/` — local Memgraph stack + `rkg` CLI (query/viz/doctor). `tools/rkg/` — generators
  (`generate_bundle_level3.py`), the CI gate (`validate_all.py`), freshness reporting.
- `knowledge/org/` — org hierarchy + generated `repos/repo-index.yaml` (repo-name resolution).
- Rules worth flagging: generated files (`registry/`, `LEVEL3_FOLLOWUPS.md`,
  `views/generated-view-contract.yaml`) are never hand-edited (`STRUCTURE.md` rule 3); nodes
  carry `evidence_class` (`DECLARED` vs `EXTRACTED`) and `confidence` fields that matter for
  trusting a given fact.

### B.2 The payouts platform bundle

`knowledge/domains/payouts/bundle.yaml:1-25`:
- `id: bundle.payouts`, `status: active`, `confidence: medium`, `last_verified: 2026-08-12`.
- Description: "Typed RKG bundle for the RazorpayX payouts platform services — payouts (fund
  transfers to beneficiaries), fts (fund transfer routing/dispatch), virtual-account (VA
  receivers + credits), x-balances (balance + ledger), x-account-statements (statements), cfa,
  and validx (fund account validation)."
- `org:` business_unit RazorpayX / group RazorpayX / subgroup Business Banking / pod **Payouts
  Platform**.
- The repo's git history available to this clone is shallow (single visible commit
  `70f78ef`), so PR #157 provenance could not be independently verified from `git log`; the
  bundle's content matches the task's description exactly, so treat it as the PR #157 artifact.

**Node/edge registry** (`registry/nodes.yaml`, `registry/edges.yaml`, generated by
`tools/rkg/generate_bundle_level3.py`): **143 nodes, 232 edges** (both counts self-consistent
with the file headers).

Node type distribution:

| type | count |
|---|---|
| failure_mode | 35 |
| flow | 27 |
| entity | 25 |
| invariant | 23 |
| runbook | 12 |
| service | 7 |
| decision | 7 |
| contract | 5 |
| participant | 2 |

**7 service nodes** (exactly matches task's list plus ValidX): `payouts`, `fts`, `cfa`,
`x-balances`, `x-account-statements`, `virtual-account`, `validx`. **`recon` is not a node in
this bundle at all** — RKG's payouts platform graph has no representation of ART/recon.

**12 runbooks** (oncall subtype): debug-balance-mismatch, debug-cache-not-working,
debug-fund-account-validation, debug-gateway-routing, debug-missing-statement,
debug-rpd-refund-stuck, debug-stuck-payout, debug-stuck-validation, debug-transfer-failure,
debug-va-credit, debug-validation-timeout, debug-webhook-not-processed.

**7 decisions** (adr subtype): bank-account-fund-account-fts-replication,
is-payout-service-dual-stack, payouts-changes-in-microservice-not-api,
pg-settlements-lite-payout-nodal, validx-db-driven-gateway-routing,
validx-penniless-default, validx-statemanager-strategy-split.

**5 contracts**: citi-webhook-contract, fts-webhook-contract, routing-api-contract,
va-webhook-events, validation-grpc-api.

Full node table: `findings/rkg_payouts_nodes.tsv` (143 rows: id, type, subtype, title).
Full edge table: `findings/rkg_payouts_edges.tsv` (232 rows: source, predicate, target, purpose).
Edge predicate distribution: REFERENCES 127, HAS_FAILURE_MODE 35, HAS_ENTITY 25,
HAS_INVARIANT 23, DEPENDS_ON 8, OWNED_BY 7, HAS_CONTRACT 5, HAS_PARTICIPANT 2.

**The 8 service-to-service `DEPENDS_ON` edges** (the RKG topology comparable to our graph):

| source | target | purpose |
|---|---|---|
| fts | x-account-statements | FTS retry preprocessor checks XAS to detect if a transfer already landed before retrying |
| payouts | cfa | Payouts resolves/validates beneficiary contacts and fund accounts from CFA |
| payouts | fts | Payouts initiates the bank transfer (HTTP POST /transfer) through FTS |
| payouts | x-balances | Payouts checks/validates merchant VA balance during payout creation/queued processing |
| validx | cfa | ValidX resolves the beneficiary fund account from CFA before selecting a gateway |
| validx | fts | ValidX uses FTS as a validation gateway for pennydrop/paisadrop (fallback gateway too) |
| validx | x-balances | ValidX associates the merchant balance for validation-fee charging |
| x-account-statements | payouts | XAS consumes payout/reversal source events from Payout Service via SQS |

### B.3 Comparison against `/Users/rana.singh/rzp-payouts-architecture/reports/PAYOUTS_SERVICE_GRAPH.json`

Python one-liner used to list our node labels (per task instruction):
```python
python3 -c "
import json
d = json.load(open('PAYOUTS_SERVICE_GRAPH.json'))
for n in d['nodes']:
    print(n['id'], '|', n['type'], '|', n.get('label',''))
"
```
Our graph: **153 nodes, 175 edges**, 18 node types (API endpoint, AuthZ policy, admin action,
configuration source, cron, datastore, deployment, event, external bank/provider, feature flag,
frontend, gateway route, identity, queue/topic, repository, role, service, webhook consumer,
worker) — a materially different, finer-grained vocabulary than RKG's
service/flow/entity/invariant/failure_mode/runbook/decision/contract/participant. Direct 1:1
node comparison is therefore mostly apples-to-oranges except at the **service** layer, which is
comparable (both graphs model the same 7 payouts-platform services as first-class nodes).

**Our graph's service-to-service edges among the same 7 services** (`svc:*`/`repo:*` filtered):

| source | type | target | mechanism |
|---|---|---|---|
| payouts | reads | cfa | GET fund account/contact (Basic Auth, ACL client Payouts) |
| validx | calls | cfa | ValidX ACL client: Create/Update contact & fund account |
| validx | calls | fts | POST /v1/transfer penny drop (VALIDX auth) |
| payouts | reads | validx | FAV freshness gate (PS has own /v1/fund_accounts/validations FAV subsystem) |
| payouts | reads | x-balances | ListAccounts (source account selection / MAR); GET /v1/balances/:id |
| fts | calls | payouts | /v1/notify/health/update, /v1/notify/downtime (bene bank health → on_hold) |
| payouts | reads | xas | xas client (`payouts/internal/provider/xas_client.go`) |
| virtual-account | reads | x-balances | GetBalanceIDByAccountNumber |

**(i) RKG nodes/edges absent from ours:**
- **`fts DEPENDS_ON x-account-statements`** ("FTS retry preprocessor checks account statements
  to detect whether a transfer already landed before retrying") — our graph has no `svc:fts →
  svc:xas` edge at all. This is a real gap: worth verifying against `fts/internal/transfer/retry_preprocessor_service*.go`
  and adding to our graph.
- **`x-account-statements DEPENDS_ON payouts`** (XAS consumes payout/reversal SQS events) — our
  graph only has the reverse direction (`payouts reads xas`, a different mechanism/client call).
  Both are plausibly true (XAS both consumes payouts' SQS events *and* is queried by payouts'
  xas_client), but our graph is missing the SQS-consumption edge specifically.
- RKG's 35 `failure_mode`, 27 `flow`, 23 `invariant`, 12 `runbook`, 7 `decision`, 5 `contract`
  nodes are, as a category, largely **absent from our graph's vocabulary** (our graph has no
  `flow`/`invariant`/`failure_mode`/`runbook`/`decision`/`contract` node types at all — it
  models endpoints/services/edges, not narrative flows or catalogued failure modes). This is a
  structural gap, not a factual contradiction: our `CONTROL_AND_INVARIANT_CATALOG.md` and
  `PAYOUTS_FLOW_CATALOG.md` cover some of this ground in prose form outside the graph JSON.

**(ii) Ours absent from RKG:**
- **`payouts reads xas`** via `payouts/internal/provider/xas_client.go` (code-evidenced,
  `confidence: confirmed` in our graph) — RKG has no `payouts → x-account-statements` edge in
  either direction beyond the reverse SQS one above.
- **`virtual-account → x-balances`** (`GetBalanceIDByAccountNumber`) — RKG's `virtual-account`
  service node has **zero** `DEPENDS_ON` edges of any kind (confirmed: `grep -i
  "virtual-account" rkg_payouts_edges.tsv` returns only HAS_CONTRACT/HAS_ENTITY/HAS_FAILURE_MODE/
  HAS_INVARIANT/OWNED_BY/REFERENCES rows, none to another service). virtual-account is a modeled
  but topologically disconnected node in RKG.
- **`fts calls payouts`** (`/v1/notify/health/update`, `/v1/notify/downtime`, bene-bank-health →
  on_hold) — RKG's only payouts↔fts edge is the forward `payouts DEPENDS_ON fts` (transfer
  creation); the reverse health/downtime callback direction isn't modeled in RKG at all.
- Recon/ART is entirely absent from RKG (see §A) — our graph at least has an (until now)
  "NOT ACCESSIBLE" placeholder node (`svc:recon`) that this task's evidence now fills in.

**(iii) Contradictions:** none found at the factual level — every edge present in both graphs
agrees on direction and mechanism (e.g. `payouts→cfa`, `validx→cfa`, `validx→fts`,
`payouts→x-balances`). The `payouts↔fts` "transfer creation" relationship is represented
differently in the two graphs — RKG has it as one direct `DEPENDS_ON` edge, our graph represents
the same fact through more granular nodes (`flag:splitz:fts_request_from_ps`,
`ep:update_fts_fund_transfer`, `q:kafka-fts-status`) reflecting the dual-path (direct FTS vs.
monolith-relay) routing logic — this is a granularity difference, not a disagreement.

### B.4 How RKG is generated; can graphify run locally on our clones?

- RKG bundle content is **hand-authored/curated** ("DECLARED" evidence) from source-code
  reading plus each repo's own `.agents/skills/repo-skill` modules, generated into the
  registry format by `tools/rkg/generate_bundle_level3.py`, and checked by
  `tools/rkg/validate_all.py` (the CI gate, run in `.github/workflows/graph.yml`).
- **Graphify** is a separate, external code-graph-extraction system, referenced but not
  vendored: node `source_refs`/`evidence_bindings` can point at `graphify://repo@commit/path#symbol`
  IDs (spec `specs/razorpay-knowledge-graph-spec.md:594,627-632,1761-1771`), and raw
  `graph.json` output "stays in the source repo or the runtime index (for graphify-debugger, in
  S3); they are not copied into razorpay/knowledge-base"
  (`knowledge/domains/payouts/evidence/graphify/README.md`).
- **Concretely for this bundle: graphify has not actually been run.** The only graphify
  evidence manifest present is `knowledge/domains/payouts/evidence/graphify/validx.yaml`, and
  its own content says `status: not_graphified`, `coverage_status: pending_graphify`,
  `graphify_version: null`, with an explicit warning: "Graphify has not been run for ValidX...
  ValidX bundle nodes are authored from source code... and are therefore DECLARED, not
  EXTRACTED." No manifest exists at all for payouts/fts/cfa/x-balances/xas/virtual-account —
  meaning even less verification than ValidX's placeholder.
- **Graphify cannot be run locally on our clones.** No graphify CLI/binary, and no
  `.graphify.yaml`/config file, is vendored in `knowledge-base`, `payouts`, `fts`, `cfa`, or
  `devstack`. The only trace across all our clones is `recon/.graphifyignore` — a file that
  tells a *centrally-run* graphify service what to skip when scanning that repo; it is not a
  local invocation config. Graphify is Razorpay-internal platform tooling we don't have access
  to, consistent with the RKG spec calling it a co-loaded "evidence layer" (S3-backed) rather
  than something a developer runs from a clone.

---

## C. payments-upi (UPS) — VPA validate/mapper contract used by payouts and FTS

### C.1 Route inventory (payments-upi server side)

`payments-upi` exposes 3 related RPCs on its `VpaService`
(`internal/pkg/route/route.go:6`, `internal/app/validate/server.go`):

| HTTP path | RPC | Purpose | Auth-override |
|---|---|---|---|
| `/v1/vpa/validate` | `Validate` (old) | Legacy VPA validate | none (`AuthFuncOverride` no-ops — basic-auth handled upstream) |
| `/v1/payments/validate/account` | `ValidateAccount` | "new RPC endpoint for payments/validate/account requests for VPA entity to UPS" — used as a **number→VPA mapper** | same |
| `/v1/payments/validate/vpa` | `ValidateVpa` | Real VPA-existence validation against the UPI network (paytm-handle special-cased to always-invalid) | same |

Source: `payments-upi/internal/app/validate/server.go:36-142`, `internal/pkg/constants/actions/actions.go:33`.

### C.2 The contract payouts actually uses: `pkg/upiService` → `POST /v1/payments/validate/account`

`payouts/pkg/upiService/vpa_mapper.go:18-83`:
```go
const VpaMapperUri = "/v1/payments/validate/account"

type VpaMapperRequestBody struct {
    Entity string `json:"entity"`   // e.g. "vpa"
    Value  string `json:"value"`    // the UPI number / VPA being resolved
}

type VpaMapperResponse struct {
    Vpa          string `json:"vpa"`
    Success      bool   `json:"success"`
    CustomerName string `json:"customer_name"`
}
```
- Auth: HTTP **Basic Auth** (`req.SetBasicAuth(Key, Secret)` —
  `payouts/pkg/upiService/base.go:41`), key/secret loaded from `payouts` config
  (`UpsClient` dependency, `payouts/internal/provider/ups_client.go:16-46`).
- Header `X-Origin: payouts` (`appConstants.OriginServiceHeader: appConstants.Payouts`,
  `vpa_mapper.go:69`) — this matters: `payments-upi/internal/app/validate/processor/response.go:64`
  special-cases `originService == constants.OriginServicePayouts` to **include the plaintext
  VPA** in the response (implying non-payouts callers may get it masked/omitted).
- Exposed to payouts callers via internal route `GET /mapped_vpa/:upi_number`
  (`payouts/internal/routing/router/payout_internal_routes.go:147-152`) →
  `PayoutService.FetchMappedVpa` (`payouts/internal/controllers/payoutController.go:1417-1456`)
  → `Core.FetchMappedVpa` (`payouts/internal/app/payouts/core.go:8121-8154`) →
  `provider.GetUPSClient(ctx).GetVpaMapperRequest(ctx, appConstants.VPA, upiNumber)`.
- Used for the "pay to a UPI number" flow — resolving a mobile-number-style UPI handle to the
  actual VPA + account-holder name before initiating the transfer.

### C.3 Does FTS call payments-upi/UPS directly? — No.

FTS's own `ValidateVPA` (`fts/internal/gateway/api.go:21-60`) does **not** call
`payments-upi`; it calls a config-driven `api_service.validate_vpa` endpoint, which in prod
resolves to `https://prod-api-int.razorpay.com/v1/payment/validate/vpa`
(`fts/config/env.prod-live.toml:246-250`) — i.e. the **API monolith's** VPA-validate endpoint,
authenticated with a separate `VALIDATE_VPA_INTERNAL_MERCHANT` credential
(`fts/internal/config/auth_user.go:10`, `fts/config/env.prod-live.toml` `[users.validate_vpa_internal_merchant]`).
Whether the monolith in turn proxies to UPS's `ValidateVpa` RPC is **not verifiable** without
the (NOT ACCESSIBLE) `razorpay/api` monolith repo — this is a new, narrower open question
worth folding into the existing monolith-access gap (UNRESOLVED_QUESTIONS #1-8).

### C.4 Full VPA-existence validation chain (`ValidateVpa`, for context — not the path payouts uses)

Documented in `payments-upi/.agents/skills/repo-skill/modules/domain/vpa/integration.md`
(sourced from `internal/app/validate/validate.go`): gateway module (Mozart legacy REST /
Integrations-UPI modern gRPC, selected per-gateway via Domain Config), Terminal Service (gRPC,
terminal metadata + Mindgate-terminal filtering), Splitz (`ValidateVpaGateway` experiment,
variants `upi_sbi`/`upi_icici`/`upi_airtel`/`upi_mindgate`/`upi_rzpaxis`, default fallback
Mindgate), MySQL `vpas` table (180-day cache, non-blocking on failure), Redis L1 cache
(`vpa:validate:response:{vpa}` key, TTL not found in code — flagged 👤 in the source doc
itself), Passport (merchant-ID-based cache-skip list), and a numeric-VPA AES-256 encryption key
for PII protection of mobile-number-style VPAs. Numeric VPAs are hardcoded to ICICI-only via
Mozart (no multi-gateway fallback) — `internal/app/validate/processor/validate.go:81-92`.

### C.5 Stub spec (from the repo's own ITF/slit test fixtures)

`payments-upi/tests/slit/testdata/vpa_validate.go:11-96` — usable directly as a contract stub:

```go
ValidateAccountProxyPath = "/v1/payments/validate/account"
Input:  &vpav1.ValidateAccountRequest{ Entity: "vpa", Value: "test.cust@icici" }
ExpectedResponse: `{ "vpa": "test.cust@icici", "success": true }`
ExpectedHTTPCode: 200
AuthType: PaymentsUpiTestApiUserAuth   // basic auth
```
and, for the underlying `/v1/vpa/validate` legacy path, both success and failure shapes:
```json
// success
{ "vpa": "test.cust@icici", "success": true, "customer_name": "Test Customer" }
// failure
{ "error": { "internal": {
    "code": "BAD_REQUEST_PAYMENT_UPI_INVALID_VPA",
    "identifier_code": "PGUP000078",
    "description": "GATEWAY_ERROR: received false response with status 200 from mozart",
    "metadata": { "gateway_error_code": "ZH", "gateway_error_description": "INVALID VIRTUAL ADDRESS", "http_code": "200" }
} } }
```
This is enough to stand up a **protocol-faithful mock** of the mapper endpoint payouts actually
calls (echo `{vpa, success, customer_name}` for known test VPAs, error shape for others) without
needing Mozart/Integrations-UPI/Splitz/Terminal-service/Redis/MySQL — those only matter for the
`ValidateVpa` path, which payouts does not call directly.

---

## D. devstack — what it provides, reusable for a fully local Env 2?

### D.1 What it is

Public repo (`razorpay/devstack`), Razorpay's **"cloud native development ecosystem"** —
explicitly a *client-only* tool, not a local runtime:
> "Client only, developer friendly stack for running cloud workloads... Build, Test, debug
> inside kubernetes with hot reloading capability" (`devstack/README.md:5-9`).

It is a **Helmfile + Devspace + Traefik + LocalStack + custom Helm-hooks** orchestration layer
that clones a "base application" (and its declared service-fleet dependencies) into a
per-developer/per-feature namespace on an **already-existing Kubernetes cluster**, with
header/hostname-based request routing (`uber-ctx-key: feature1` or `feature1.example.com`) so a
request can hop through a chain of feature-branch service deployments and fall back to the base
version wherever a feature isn't deployed (`devstack/docs/Architecture.md:5-46`).

Components:
- **Prerequisites it assumes are already provisioned** (not created by devstack itself):
  Kubernetes 1.15+, Traefik 2.0+ deployed on that cluster, LocalStack deployed on that cluster,
  Kube Janitor for TTL cleanup (`devstack/README.md:33-40`).
- **Custom Helm hooks** it ships: `ingressroute_configurator` (Traefik CRD wiring),
  `secret_cloner` (clones k8s Secrets from the base app's namespace into the feature namespace),
  `sqs_configurator` (provisions SQS queues via LocalStack from a helm-hook config) — all under
  `devstack/hooks/*`.
- `devstack/setup-helper.sh:19-21,219-301` explicitly: "Configure these tools with kubernetes
  cluster info... Configure these tools to use your razorpay email to login... **[Needs VPN]**
  [Spinnaker Pipeline Trigger] Provision access to the kubernetes cluster for your razorpay
  email" and programmatically writes `kubectl config set-cluster`/`set-context` entries pointing
  at a real, named, VPN-gated Razorpay cluster.

There is **no local Kubernetes bootstrap** (no kind/minikube/k3s anywhere in the repo — checked
via grep across `setup-helper.sh` and the whole tree) and **no merchant-provisioning, mock-bank-
gateway, or payments-domain content at all** — the `example/` app is a generic Go webapp + SQS
producer/consumer demo. This directly bears on UNRESOLVED_QUESTIONS #36 ("self-serve path to
obtain payouts test merchants with FTS + mock-gateway wired") — devstack does not address this;
it is orthogonal generic DevX tooling, not a merchant/gateway fixture system.

### D.2 Reusable pieces vs. pieces that call internal services

**Reusable for a fully local Env 2 (no Razorpay infra/VPN contact):**
- The **LocalStack pattern** itself (open-source, runs anywhere) — `sqs_configurator`'s Go code
  (`devstack/hooks/sqs_configurator/package/queue/sqs.go`) is a thin AWS-SDK SQS-create wrapper
  that could be re-pointed at a standalone local LocalStack container (`docker run localstack`)
  without any devstack/Kubernetes machinery, to emulate SQS/SNS for Env 2's queue-dependent
  paths.
- The **declarative service-fleet-via-Helmfile pattern** is reusable as a *design pattern* for
  our own local compose/helm setup (each of `recon`, `payouts`, `fts` etc. already ship their
  own `docker-compose*.yml` — see `recon/docker-compose.yml`, `recon/docker-compose.app.yml`,
  `recon/docker-compose.test.yml` — which is the more directly reusable primitive for a
  no-cluster local Env 2, independent of devstack).
- The **hot-reload dev-loop concept** (CompileDaemon/Devspace file-sync) is reusable if we stand
  up our own local containers; the devstack-specific wiring (Traefik IngressRoute CRDs,
  preview-URL generation) is not needed for a non-shared, single-developer local environment.

**Not reusable / hard-wired to internal services:**
- `devstack/setup-helper.sh` — VPN + Razorpay-cluster kubeconfig provisioning, not local at all.
- `devstack/hooks/ingressroute_configurator/*` — Traefik CRD management on Razorpay's shared
  cluster; meaningless without that cluster.
- `devstack/hooks/secret_cloner/*` — clones **live Kubernetes Secrets** from a base-app
  namespace on Razorpay's cluster (`controllers/kubernetes.go`); this is explicitly an internal-
  infra operation (reading real secrets), the opposite of what a local, credential-free Env 2
  wants.
- The entire premise of devstack (join an existing shared/personal Razorpay k8s cluster with
  Traefik+LocalStack already running) is incompatible with "fully local, without contacting
  Razorpay infra" — it is cloud-on-laptop (routing into real infra), not laptop-only.

**Bottom line for D:** devstack is a generic internal-cluster DevX accelerator with zero
payments-specific content; it neither solves the test-merchant/mock-gateway gap (UNRESOLVED_QUESTIONS
#36) nor gives us a Razorpay-infra-free local stack — for a truly local Env 2 we should keep
using each service repo's own `docker-compose*.yml`/Makefile local-dev targets (already present
in `recon`, and presumably in `payouts`/`fts`/`cfa` per earlier findings), not devstack.

---

## UNRESOLVED_QUESTIONS closed

- **#30** (RKG export for the payouts services, PR #157) — **Closed.** Bundle exists at
  `knowledge-base/knowledge/domains/payouts/`, 143 nodes / 232 edges, 7 service nodes exactly
  matching our closure-relevant service set (payouts, fts, cfa, x-balances,
  x-account-statements, virtual-account, validx). Cross-verified against
  `PAYOUTS_SERVICE_GRAPH.json`: no contradictions found, several complementary gaps identified
  in both directions (§B.3). Git history for the clone was too shallow to independently confirm
  the PR #157 attribution, but content matches.
- **#32** (Recon/ART repository and its write paths into FTS attempts) — **Closed** on the
  central question ("can recon alter transfer state?" — **yes**, via `PATCH/POST
  /v1/attempts/:action` and `PUT /v1/attempts/reconcile`, ARTAuth-authenticated, human-gated by
  FinOps re-upload of an RX Workflow file; see §A.4). **Not fully closed**: the literal endpoint
  template(s)/method(s) actually configured in the `workflow_configs` DB table are runtime data,
  not in the repo — we know the *mechanism* and the *authorization*, not every exact endpoint
  string in current use.
- **#36** (Self-serve path to payouts test merchants with FTS + mock-gateway) — **Closed as a
  non-answer**: devstack does not provide this. It has no merchant, gateway, or payments-domain
  functionality at all (§D.1). The gap remains open; look elsewhere (e.g. a Test Data Manager
  service, not investigated here).
- **#37** (Access to `e2e-test-orchestrator` and `qa-tools` repos) — **Still open.** Neither
  repo is present among our clones; devstack does not reference or vendor them. No new access
  was gained.

---

## Unknowns / follow-ups

1. The exact `workflow_configs` DB rows (endpoint template, method, payload schema) that recon
   currently has configured for FTS — not visible from the repo; would need a DB dump or admin
   API read against `recon-postgres`.
2. Whether the API monolith's `/v1/payment/validate/vpa` (called by FTS, `fts/config/env.prod-live.toml:246-250`)
   proxies to UPS's `ValidateVpa` RPC or has its own independent VPA-check logic — blocked by
   monolith repo inaccessibility (folds into UNRESOLVED_QUESTIONS #1-8's existing gap).
3. Redis TTL value for UPS's VPA cache (`constants.VpaValidateResponseTTL`) — flagged as
   unresolved in the source repo's own generated doc, not found in code by this pass either.
4. Whether `fts DEPENDS_ON x-account-statements` (RKG) is exercised for **payout** transfers
   specifically or only for a subset of channels — worth a follow-up read of
   `fts/internal/transfer/retry_preprocessor_service*.go` to add the missing edge to our graph
   with proper mechanism detail.
5. PR #157's actual diff/description could not be fetched (shallow clone, no GitHub API access
   used in this pass) — if precise provenance matters, a `gh pr view 157 --repo
   razorpay/knowledge-base` would close this.

---

## Evidence file index

- `findings/rkg_payouts_nodes.tsv` — full 143-row RKG payouts-bundle node table (id, type, subtype, title).
- `findings/rkg_payouts_edges.tsv` — full 232-row RKG payouts-bundle edge table (source, predicate, target, purpose).
- `findings/our_graph_nodes.txt` — full 153-row dump of `PAYOUTS_SERVICE_GRAPH.json` nodes (id, type, label).
