# kube-manifests / spinacode / alert-rules — payouts platform architecture

Investigated repos:
- `kube-manifests` — fully cloned (HEAD `9226a892f`, commit dated 2026-09-04). Helm-chart-per-service layout: `templates/<service>/templates/*.yaml` = chart source (Values placeholders), `<env>/<service>/values.yaml` = one resolved-values file per environment (per `kube-manifests/AGENTS.md`).
- `spinacode` — fully cloned (`da46a5239`). Legacy pipelines live at repo root (`fts/`, `ledger/`, `x-balances/`); the current **v3 templated-pipeline** generation lives under `spinacode/v3/<service>/<env>/<region>/*.json` (Spinnaker Managed Pipeline Templates, one JSON stage-definition per file).
- `alert-rules` — **NOT AVAILABLE**. This repo was never cloned into the scratchpad (absent from both `clone_results.log` and `clone_delta.log`), and does not exist anywhere under `/Users/rana.singh` either. No VMRule/PrometheusRule CRDs for payouts/fts/ledger metrics were found embedded in `kube-manifests` (only 2 VMRule/PrometheusRule files exist repo-wide, both under `templates/keda-upstream/`, unrelated to our services). **Item 3 of the task and UNRESOLVED_QUESTIONS #20 remain open** — see "Remaining unknowns."

---

## 1. kube-manifests — per-service manifest inventory

Env used as "prod": `prod/` for most services; **`cde/` is the actual prod-equivalent env for payouts, cfa, validx, and mozart** (no `prod/<service>/values.yaml` exists for these four — the CDE/cardholder-data-environment cluster is where they run). Non-prod column is `stage/` where present, else `dev/`.

| Service | Manifest dir | Prod env values | Non-prod env values | API/web replicas (prod) | HPA min/max | IRSA role ARN (prod) | NetworkPolicy |
|---|---|---|---|---|---|---|---|
| payouts | `templates/payouts/templates/` | `cde/payouts/values.yaml` | `stage/payouts/values.yaml` | 10 (base) + 1 canary + 1 baseline | 12 / 25 | `arn:aws:iam::141592612890:role/cde-payouts` | none |
| fts | `templates/fts/templates/` | `prod/fts/values.yaml` (+ `fts1`, `fts-dark` variants, not deep-dived) | `stage/fts/values.yaml` | 11 live + 1 canary + 1 baseline | 18 / 18 (fixed) | `arn:aws:iam::141592612890:role/prod-fts` | none |
| ledger | `templates/ledger/templates/` | `prod/ledger/values.yaml` | `stage/ledger/values.yaml` | live 2, live-pg 2, mirror 2, pg-mirror 2 | live 5/30, pg 10/40, mirror 2/6, pg-mirror 2/6, canary-baseline 2/10 | `arn:aws:iam::141592612890:role/prod-ledger` (SA `ledger-service-account`) | none |
| cfa | `templates/cfa/templates/` | **`cde/cfa/values.yaml`** (no prod/) | `dev/cfa/values.yaml` | 8 (min) / 10 (max) web + web-canary 1-3 | 8/10 | `arn:aws:iam::141592612890:role/cde-cfa` | none |
| x-balances | `templates/x-balances/templates/` | `prod/x-balances/values.yaml` | `stage/x-balances/values.yaml` | 2/10 web, many per-bank workers (rbl/icici/yesbank balance-fetch, canary variants) each with own HPA (2-20 range) | 2/10 (web) | `arn:aws:iam::141592612890:role/prod-x-balances` | none |
| banking-account (= "banking-accounts") | `templates/banking-account/templates/` | `prod/banking-account/values.yaml` | `stage/banking-account/values.yaml` | (not captured this pass) | — | `arn:aws:iam::141592612890:role/prod-banking-account` | none |
| xperience | `templates/xperience/templates/` | `prod/xperience/values.yaml` | `stage/xperience/values.yaml` | web 2/4 | 2/4 | (not captured) | none |
| x-account-statements | `templates/x-account-statements/templates/` | `prod/x-account-statements/values.yaml` | `stage/.../values.yaml` | 2/8 web + several worker HPAs (1-8) | 2/8 | `arn:aws:iam::141592612890:role/prod-x-account-statements` | none |
| validx | `templates/validx/templates/` | **`cde/validx/values.yaml`** (no prod/) | `stage/validx/values.yaml` (role ARN there is `stage-payouts` — validx-stage runs under the **payouts** IAM role, i.e. shares identity with payouts in staging) | (not captured) | 1/2 (stage) | (cde value not captured) | none |
| workflows | `templates/workflows/templates/` | `prod/workflows/values.yaml` | `stage/workflows/values.yaml` | web 5/25, worker 10/40, worker-asl (Step-Functions-style?) 2/6 | as above | (not captured) | none |
| batch | `templates/batch/templates/` | `prod/batch/values.yaml` | `stage/batch/values.yaml` | sqs-worker 8/15 | HPA **disabled** (`is_hpa_enabled: false`) | `arn:aws:iam::141592612890:role/prod-batch` | none |
| stork | `templates/stork/templates/` | `prod/stork/values.yaml` | `stage/stork/values.yaml` | huge worker fan-out (webhook/sms/email/whatsapp/ticker workers), webhook-worker 20/54 | per-worker HPA | `arn:aws:iam::141592612890:role/prod-stork` | none |
| dcs | `templates/dcs/templates/` | `prod/dcs/values.yaml` | `stage/dcs/values.yaml` | live 15/100, test 3/100, canary 2/100 | aggressive autoscale | (not captured) | none |
| splitz | `templates/splitz/templates/` | `prod/splitz/values.yaml` | `stage/splitz/values.yaml` | 15/100 | 15/100 | `arn:aws:iam::141592612890:role/prod-splitz` | none |
| mozart | `templates/mozart/templates/` | **`cde/mozart/values.yaml`** (no prod/; also `mozart-dark`, `mozart-whitelisted`, `mozart-pagination`, `credithub-gateway-mozart`, `acs-mozart` variants exist, not deep-dived) | `stage/mozart/values.yaml` | (not captured) | — | (not captured) | none |

**Cross-cutting findings:**
- **No `NetworkPolicy` resource exists for any of the 15 services** anywhere in `kube-manifests` (checked `templates/<service>/templates/`). Namespace isolation, if any, is enforced elsewhere (CNI default-deny at cluster level, or not at all) — not visible in this repo.
- **No sidecars** (no vault-agent, envoy/istio-proxy, datadog-agent, fluentbit container) were found injected via these Helm templates for payouts/fts/ledger/cfa/x-balances — pods are single-container (`web`/`worker`/`scheduler`) plus a `hostPath` trace-log volume mount on some payouts workers (`/var/log/fluentd/<ns>/<worker>/trace`), implying log shipping is done by a **node-level** fluentd DaemonSet, not a pod sidecar.
- **Secret injection mechanism**: no `ExternalSecret` CRDs and no `vault.hashicorp.com/agent-inject*` annotations exist for payouts/fts/cfa/x-balances (only `ledger` additionally uses `configMapRef` for non-secret config, e.g. `configmap_name_live: ledger-live`). All five core services pull credentials via a single `secretRef`/`secretKeyRef` naming the service's plain k8s `Secret` (e.g. `secretRef: {name: payouts}`, `{name: ledger-live}/{name: ledger-test}`). Repo-wide, `kube-manifests/tools/hooks/secret_cloner/` (Go, `controllers/dynamodb.go`) is the mechanism that populates these k8s Secrets from a **DynamoDB-backed secret store** (Credstash-style: DynamoDB + presumably KMS) — this is the closest thing to "how secrets are injected" visible in this repo. No Vault is used for these services.
- **Image registry**: all images pull from `c.rzp.io/razorpay/<repo>:<tag>` (internal container registry mirror), e.g. `c.rzp.io/razorpay/ledger:scheduler-<tag>`, `c.rzp.io/razorpay/workflows:web-<sha>`.
- **Public hostnames** (not internal-VPC DNS) configured per service, useful as the "dependency URL patterns" a local config generator must replace (see §5).

### workflows → GitHub repo identity (partial answer to UNRESOLVED #12)
`prod/workflows/values.yaml` resolves `workflows_image_tag: c.rzp.io/razorpay/workflows:web-ce27ea2fd189403b89b5a47a77e43a648c615224` (and `worker_image_tag`/`worker_asl_image_tag` from the same `razorpay/workflows` image repo). Every other service in this table has an image-repo name identical to its GitHub repo name, so this is **strong circumstantial evidence the workflow/approval-engine service's GitHub repo is `razorpay/workflows`** — not proven (no README/CODEOWNERS reference was found), but consistent with the naming convention observed everywhere else in this audit.

---

## 2. CronJobs — exact schedules and target paths

**Payouts `/v1/cron/*` is NOT a k8s CronJob.** `templates/payouts/templates/ing-v2.yaml` defines a Traefik `Middleware` (`payouts-fastcron-ip-whitelist`) and an `IngressRoute` rule `Host(payouts_external_host) && PathPrefix(/v1/cron/)` routed to a `payouts-edge-redirect` service, IP-restricted to FastCron's published source ranges (comment cites `https://help.fastcron.com/posts/FastCron-IP-addresses-list-42`). **The actual cron cadence for each `/v1/cron/*` endpoint is configured inside the FastCron SaaS dashboard, which is outside every cloned repo** — kube-manifests only proves the delivery mechanism (external SaaS cron → whitelisted HTTP POST), not the schedule values. This is a genuine, not-further-resolvable-from-repos gap (see Remaining unknowns).

No other service in the 15 has a payouts-style external-cron ingress pattern.

### Real k8s `CronJob` resources found (only in ledger, cfa, batch, stork; none in payouts/fts/x-balances/banking-account/xperience/x-account-statements/validx/workflows/dcs/splitz/mozart)

| Service | CronJob name | Schedule (resolved, prod/cde) | Schedule (stage) | Command / purpose | suspend | concurrencyPolicy |
|---|---|---|---|---|---|---|
| ledger | `verify-warm-storage-cronjob-live` | **not rendered in prod** (`verify_warm_storage_cronjob_enabled` unset in `prod/ledger/values.yaml` → template's `{{- if }}` guard renders nothing) | disabled (`verify_warm_storage_cronjob_enabled: false` in stage) | `LEDGER_SCHEDULER_COMMAND=verify-warm-storage`, TTL 30m | `true` (hardcoded in template) | Forbid |
| ledger | `verify-warm-storage-cronjob-test` | same as above (not rendered in prod) | disabled in stage | same, test tenant | `true` | Forbid |
| ledger | `<name>-split-account-balance-update-cronjob-live` | **`*/10 * * * *`** (every 10 min) | `*/30 * * * *` (stage) | `LEDGER_SCHEDULER_COMMAND` = `split_account_balance_update_cronjob_scheduler_cmd` value, tenant = X | `true` (hardcoded) | Allow, parallelism 2 |
| ledger | `<name>-split-account-balance-update-cronjob-pg-live` | `*/10 * * * *` (shares `split_account_balance_update_cronjob_schedule` value) | (enabled: true in prod) | same command family, tenant = PG | `true` (hardcoded) | Forbid |
| cfa | (chart is a `range $cron := .Values.crons` loop — 0..N crons per env) | **`crons: []` in `cde/cfa/values.yaml`** — CFA currently ships **zero active CronJobs in prod**; only a commented-out example remains (`# schedule: "0 6,18 * * *"`, `command: fetch-cron`) | same, empty | n/a | n/a | Forbid |
| batch | `<name>-shutdown-recovery-cronjob` | `*/15 * * * *` | (not captured) | `bash batch_deployment.sh shutdown-recovery`, basic-auth creds via secretKeyRef | `false` (always active) | Forbid |
| stork | `stork-scheduler` | `*/3 * * * *` | (not captured — stage values didn't show this key; may inherit chart default) | scheduler binary, `CONFIG_FILE` env | `{{ .Values.scheduler_cron_suspended }}` (value not captured) | Forbid |
| stork | `stork-webhook-disabler` | `*/30 * * * *` | (not captured) | webhook-disabler binary | templated | Forbid |
| stork | `stork-partitioner` | `partitioner_enabled: true` in prod; schedule value not captured this pass | — | partitioner binary | templated | Forbid |

**Important caveat on `suspend: true`**: the ledger CronJob templates hardcode `suspend: true` directly in the chart (not a `.Values` placeholder), meaning as committed to this repo, every ledger CronJob renders suspended in every environment. No kustomize/patch mechanism un-suspending them was found in `kube-manifests/scripts/`, `automation/`, `tools/`, or `fluxcd/`. Either these jobs are toggled on manually via `kubectl patch` outside of GitOps (untracked), or they are genuinely dormant in the current snapshot. This should be verified operationally, not assumed from the repo.

### Why "cron cadence unknown" is now resolved for payouts's queue processing (closes most of UNRESOLVED #14)
`templates/payouts/templates/` contains **~25 permanently-running worker `Deployment`s**, one per SQS/Kafka consumer (`payouts-worker-queued-processing-live`, `-on-hold-processing-live`, `-schedule-processing-live`, `-data-consistency-checker-processing-live`, `-fts-async-processing-live`, `-bulk-payouts-live`, `-webhook-event-live`, `-kafka-fts-status-updates-consumer`, etc.), each with `args: [worker, <queue-name>]`, its own HPA, and its own `PAYOUTS_WORKER_NAME` env value. **"queued"/"scheduled"/"on_hold"/"reservation" timing is NOT driven by a cron cadence at all** — it is continuous SQS/Kafka queue-draining by always-on worker pods; the "cadence" is really SQS visibility-timeout / delay-seconds / consumer poll interval, which lives in the payouts app config (TOML), not in kube-manifests. The only real cron-like surface for payouts is the externally-scheduled `/v1/cron/*` FastCron hits (schedule values not in-repo, per above).

---

## 3. alert-rules

**Repo not available** — never cloned (confirmed absent from `clone_results.log`/`clone_delta.log` and from a filesystem-wide search). No VMRule/PrometheusRule CRDs referencing payouts/fts/ledger metrics (`TransferWebhookUpdateFailureCount`, `payout_stuck`, dual-write failures, ledger insufficient-balance) were found embedded in `kube-manifests` either — the only VMRule/PrometheusRule files in the whole repo are two generic KEDA-upstream ones, unrelated to this platform. **Item 3 cannot be completed and UNRESOLVED_QUESTIONS #20 stays open** (see below).

---

## 4. Env "2 process topology" table (for local repro)

| Service | Process | Binary args | Port(s) | Health endpoint | Required env NAMEs (synthetic value suggestion) | Cadence to reproduce |
|---|---|---|---|---|---|---|
| payouts | api | `api` | 8000 (http), 8001 (metrics) | liveness `GET /commit.txt:8000`, readiness `GET /status:8000` | `APP_MODE`, `APP_ENV` (=`prod`), `JAEGER_HOSTNAME` (node), `PAYOUTS_TELEMETRY_EXPORTERHOST` (node), plus everything in k8s Secret `payouts` (DB creds, PAYOUTS_DB_API_* — names only, values are secret) | always-on |
| payouts | worker (×~25, e.g. `sqs-queued-processing-live`) | `worker <queue-name>` | 8000/8001 (same probes) | same as api | `PAYOUTS_WORKER_NAME` (per-worker, e.g. `queued_payout`) + same base set | always-on; per-queue "cadence" = SQS/Kafka consumer poll, config in app TOML not k8s |
| payouts | cron endpoints | n/a (HTTP handler inside `api` binary, hit externally) | 8000 via `/v1/cron/*` | — | — | **schedule set in FastCron SaaS — not in-repo** |
| fts | `live` (api, arg `web`) — huge worker fan-out, ~1 Deployment per bank×rail connector (`fts-live-default-worker`, `-worker-check-transfer-status`, `-worker-fire-transfer-status-webhook`, `-worker-axis-{neft,rtgs,upi,imps-hv,direct-upi,file-based}-{initiate-transfer,check-transfer-status}`, `-worker-icici-{,direct-,imps-hv-}{initiate,check}-transfer*`, `-worker-hdfc-{bulk-transfer-status,initiate-attempt-bulk}`, `-worker-amazon-pay-*`, `-worker-batch-icici-neft-*`, `-worker-check-recon-status`, …) | api: 80 (http), 8081 (metrics) | liveness/readiness `GET /status:80` | `APP_MODE` (=`live`), `APP_ENV`, `JAEGER_HOSTNAME` (node), `TELEMETRY_EXPORTERHOST` (node), + `fts_extra_env` block (arbitrary extra vars per env) + secret `fts-live` | always-on workers per connector |
| ledger | live / live-pg / mirror / pg-mirror (deployment container names not individually captured; SA `ledger-service-account`, role `arn:aws:iam::141592612890:role/prod-ledger`) + `scheduler` (CronJob-only container) | (deployment ports not captured) | (not captured) | `APP_ENV`, `LEDGER_SCHEDULER_COMMAND`, `LEDGER_SCHEDULER_TTL`, `LEDGER_TRACING_SERVICENAME`, `LEDGER_TRACING_HOST`, `LEDGER_TELEMETRY_EXPORTER_HOST/PORT`, `TENANT` (`tenant_x`/`tenant_pg`); also uses `configMapRef` (e.g. `ledger-live`) unlike the other 4 core services | scheduler CronJobs: split-account-balance-update every 10 min (prod) |
| cfa | `web` (+ `web-canary`), separate `deployment-worker.yaml` (worker container not detailed) | web: 8081 (app), 8082 (metrics) | readiness `GET /ready:8081`, liveness `GET /live:8081`, metrics scraped at `prometheus.io/path: /metrics` | `APP_ENV`, `JAEGER_HOSTNAME` (node), `TELEMETRY_EXPORTERHOST` (node), `USER` | no active crons in prod (crons: []) |
| x-balances | `web` (+ canary), separate `deployment-worker.yaml` (per-bank workers: rbl/icici/yesbank balance-fetch, not individually detailed) | web: 8081 (app), 8082 (metrics) | readiness `GET /ready:8081`, liveness `GET /live:8081` | (same cfa-style pattern presumed; not individually enumerated) | always-on workers |

Note: cfa and x-balances share an identical health/port convention (8081 app + `/ready`/`/live`, 8082 Prometheus `/metrics`) distinct from payouts (8000/8001, `/status`+`/commit.txt`) and fts (80/8081, `/status`). Worker-container env/ConfigMap enumeration for the fts per-connector workers, ledger's live/live-pg/mirror deployments, and the cfa/x-balances `deployment-worker.yaml` files was not individually completed this pass (only the `web`/`live` API containers were read in full) — a subagent-concurrency ceiling (20 active subagents cluster-wide) blocked the planned parallel deep-dive passes that would have covered this exhaustively. See Remaining unknowns.

---

## 5. Dependency URL patterns to replace for local repro

| Pattern | Example values found | Notes |
|---|---|---|
| Public prod hostnames (`*.razorpay.com`) | `payouts.razorpay.com` (internal), `payouts-ext.razorpay.com` (external/FastCron), `fts-live.razorpay.com`, `fts-test.razorpay.com`, `cfa-live.razorpay.com`, `cfa-live-ext.razorpay.com`, `cfa-live-int.razorpay.com`, `x-balances.razorpay.com`, `x-balances-ext.razorpay.com`, `x-balances-int.razorpay.com`, `ledger-live-statuscake.razorpay.com`, `ledger-test-statuscake.razorpay.com` (+ pg variants) | These are Ingress/IngressRoute `Host()` match values — a local generator should substitute `localhost`/synthetic hostnames here. |
| Internal k8s service DNS | `edge.edge.svc.cluster.local` (target of the payouts FastCron ingress's `edge-redirect` service) | `*.svc.cluster.local` pattern — not reproducible outside a real cluster; local repro should stub or bypass. |
| Container registry | `c.rzp.io/razorpay/<repo>:<tag>` | Internal registry mirror; local repro needs public/local image builds instead. |
| No `*.razorpay.vpc`-style internal DNS names were found | — | Searched explicitly across payouts/fts/ledger/cfa/x-balances prod values files; none matched. Downstream service-to-service calls are presumably configured in app-level TOML config (not visible in kube-manifests), consistent with the minimal env-var footprint observed (secretRef only, no per-dependency host env vars). |

---

## 6. spinacode — v3 templated pipelines

The current pipeline generation lives at `spinacode/v3/<service>/<env>/<region>/*.json` (Spinnaker Managed Pipeline Templates). Legacy/root-level dirs (`spinacode/fts/`, `spinacode/ledger/`, `spinacode/x-balances/`) still exist (Jsonnet, older generation) but **`spinacode/v3/{payouts,cfa,fts,ledger,x-balances}` all exist** and are the authoritative current pipelines — this closes the earlier concern that payouts/cfa had no spinacode footprint at the repo root; they simply live under `v3/`.

### payouts (`spinacode/v3/payouts/`)
- **Regions/env matrix**: `prod/mum-rspl`, `prod/mum-rzpx`, `prod/hyd`, `prod/mum-dr`, `dev-serve/mum-rspl`. Each region dir contains stage JSONs: `deploy-to-a-region.json` (`type: templatedPipeline` — references a shared reusable template rather than inlining logic), `progressive-canary-deployment-through-ingressroute-to-blue-and-green-clusters.json`, `scale-pods/…`, `rollback-pods/rollback-release-in-a-region.json` and `…-on-blue-and-green-clusters.json`, `rotate-pods/…`.
- **Canary**: `canary-config.json` per region defines a full Netflix ACA (`NetflixACAJudge-v1.0`) analysis — P0 group (90% weight) checks Latency P95 on Payout Create / Fetch-by-id / Fetch-multiple and 4xx/5xx error-rate deltas (`allowedIncrease 1.1`, `criticalIncrease 1.3`, marginal/pass thresholds 10/5); P1 group (10% weight) checks CPU/memory usage deltas (`allowedIncrease 1.2/1.35`). All queries are inline PromQL against `payouts_service_*` and `api_payout_create_duration_monolith_proxy_bucket` metrics.
- **Approval gating**: `app.json` restricts pipeline EXECUTE to `deploy-emergency-approvers`, `deploy-hotfix-approvers`, `deploy-leads`, `deploy-managers`, `devops-leads` — i.e. every prod pipeline run requires membership in one of these groups (Spinnaker-level RBAC, not necessarily a manual-judgment stage inline in the pipeline JSON).
- **Config-render / Kafka provisioning**: no `render-config`/`.toml`/`config-render` stage and no Kafka-topic or SQS-queue provisioning stage found anywhere under `v3/payouts`. Config appears to be baked into the image or sourced from the k8s Secret/ConfigMap at pod start (per §1), not templated by Spinnaker.

### cfa, fts, ledger, x-balances (`spinacode/v3/<service>/`)
Same `v3/<service>/<env>/<region>/*.json` shape confirmed to exist for all four (`cfa`, `fts`, `ledger`, `x-balances` all present under `v3/`). `fts/prod` additionally has legacy root-level Jsonnet (`fts/default.jsonnet`, `fts/prod/`) enumerating the full worker fleet per prod deploy target (`fts-live`, `fts-live-default-worker`, `fts-live-worker-check-transfer-status`, `-fire-transfer-status-webhook`, `-initiate-transfer`, `-register-beneficiary`, `-verify-beneficiary`, `-icici-check-transfer-status`, `-icici-initiate-transfer`, `-icici-register-beneficiary`, `-retry-transfer`, …) — useful as an authoritative fts worker-process list, though stage/canary detail per-worker was not deep-dived this pass.

---

## UNRESOLVED_QUESTIONS — status after this pass

- **#14 (production cron cadence for every `/v1/cron/*` endpoint) — PARTIALLY CLOSED.** Mechanism fully resolved: payouts cron endpoints are hit by the external FastCron SaaS via an IP-whitelisted ingress, not a k8s CronJob; there is no in-repo schedule to read (FastCron's own dashboard, outside all cloned repos, holds the actual per-endpoint cadence). Separately, the "queued/scheduled/on_hold/reservation timing" part of this question is now answered: those are continuous SQS/Kafka worker-Deployments, not cron-timed at all. Real k8s CronJobs (ledger, cfa, batch, stork) now have exact, resolved schedules (table in §2). **Remaining gap**: the literal FastCron schedule strings for each `/v1/cron/*` path — genuinely inaccessible from any of these repos.
- **#20 (what alerts on `TransferWebhookUpdateFailureCount`) — STILL OPEN.** The `alert-rules` repo was never cloned and doesn't exist locally; no VMRule/PrometheusRule in `kube-manifests` references this or any other payouts/fts/ledger metric. Cannot be answered without access to that repo (or to the Prometheus/VictoriaMetrics alerting config it presumably holds, or Grafana/Alertmanager directly).
- **#6 (current values of `PAYOUTS_FEATURES_FORWARD_DUAL_WRITE` / `REVERSE_DUAL_WRITE`) — NOT RESOLVABLE FROM kube-manifests, but the search itself is informative.** These flag names do not appear anywhere in `kube-manifests` (payouts env vars are limited to `APP_MODE`, `APP_ENV`, two node-derived tracing vars, plus everything in the opaque `payouts` k8s Secret). This confirms the flags are **not static k8s-level config** — they're runtime/DB- or app-config-driven (consistent with the working hypothesis that they live in payouts' own MySQL-backed feature-flag/config store or a Splitz experiment, not an env var). Still needs the payouts app-config/DB or Splitz to actually resolve current values.

## Remaining unknowns / gaps from this pass specifically
1. **alert-rules repo inaccessible** — item 3 of this task is fully unanswerable without it.
2. Full Deployment/ConfigMap/env-var enumeration for **fts, ledger, cfa, x-balances** was not completed to the same depth as payouts/ledger-cron/cfa-cron/batch/stork — a concurrent-subagent ceiling (20 active subagents cluster-wide) blocked the planned parallel deep-dive passes; only summary-level replica/HPA/role-ARN facts were captured for these four plus all 10 secondary services (§1 table). A follow-up pass reading `templates/{fts,ledger,cfa,x-balances}/templates/*deployment*.yaml` + resolving against `prod`/`cde` values.yaml would close this.
3. `ledger` CronJobs are hardcoded `suspend: true` in the chart with no visible un-suspend mechanism in-repo — needs operational confirmation (kubectl-level manual toggle?) rather than repo-only inference.
4. `stork-scheduler`/`webhook-disabler`/`partitioner` `_cron_suspended` boolean values and the stage-env stork cron schedules were not resolved (file exists but wasn't fully read this pass).
5. IRSA role ARNs for xperience, validx (cde), mozart (cde), banking-account worker set, and DCS/mozart replica counts were not fully captured (ledger's role ARN — `prod-ledger` — was resolved in a follow-up pass).
6. spinacode config-render mechanism for TOML/env overlays: concluded "not found as an explicit Spinnaker stage" — but this is an absence finding, not a confirmed alternative (didn't trace where payouts' TOML config actually does get assembled — likely inside the Docker image build or a startup init step in the app repo itself, out of scope for kube-manifests/spinacode).
