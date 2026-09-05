# Configuration provenance — payouts / fts / ledger / cfa / x-balances / DCS / Splitz

Synthesis of `reports/fidelity/raw/12_runtime_topology_effective_config.md` (primary),
`04_payouts_service_core.md` §6/§8, `06_fts_routing_status_mozart.md` §2.5,
`07_ledger_balances_cfa_banking.md` §5-7, `09_stork_webhooks_kafka.md` §4,
`10_dcs_splitz_pricing_shield_asv.md` §A-B, cross-checked directly against the current
`ENV2_COMPOSE/` twin and, where the compact notes didn't cover it, against the real repo config
files under the `REPOS` root the raw reports define. Every claim below carries a `path:line`.
No secret values recorded (names/classes only); no real internal IPs recorded.

## 1. Provenance classes, per config family

| Family | Class | Where the real value lives | Twin's answer |
|---|---|---|---|
| payouts/fts/ledger/cfa/x-balances timeouts, retries, resiliency, `[worker]`, `[job]`/queue-name literals, `[fmp]`, `[payout_config]`, `[shadow_gateway]` | **repo literal** | `config/prod.toml` / `config/prod-live.toml` / `config/env.prod-live.toml`, checked in and readable | Copied into `config/templates/base/<svc>/arena.toml` (mostly correctly — see §2 for the 6 confirmed exceptions) |
| `[features].forward_dual_write` / `reverse_dual_write` / `db_route_label_enabled` / `skip_ca_dual_write` | **env-injected (k8s secretRef), real value UNKNOWN** | `payouts/config/prod.toml:663-668` sets each to `env\|PAYOUTS_FEATURES_*` — the literal string in the repo is a placeholder, not the value | Twin hardcodes `reverse_dual_write=true, forward_dual_write=false` (arena.toml:751-753) — matches only `default.toml`'s code-level fallback, not a confirmed prod runtime value |
| `[splitz].client_side_eval`, 38 payouts / 30 fts Splitz experiment IDs | **repo literal for the flag itself; SaaS/MySQL-only for the 38/30 real IDs** | `payouts/config/prod.toml:442` (flag); Splitz's own `experiments`/`variants` MySQL tables (IDs — no repo carries them, confirmed by exhaustive search per `EFFECTIVE_CONFIG_GAPS.md` A3) | Twin gets the **flag wrong** (see §2.1) but ships a fully worked stub-ID scheme for the IDs (acceptable — real IDs are opaque and DB-only regardless) |
| `[dcs].Mock` | **dead/vestigial field, not a provenance gap at all** | Never read outside `goutils/dcs`'s own tests, any SDK version (`goutils/dcs/config/config.go:10`) | Twin sets `true` everywhere; harmless because inert |
| FastCron `/v1/cron/*` cadences (21 routes) | **SaaS-only** (FastCron dashboard, no API/export in any cloned repo) | FastCron dashboard | Twin's `cron-driver` uses fixed 300s/900s guesses, self-flagged UNVERIFIED |
| DCS per-merchant flag values (real merchant cohort) | **DCS-only** (DynamoDB + Aurora mirror) | DCS `KVService.Get`, `#tech_infra_dcs` | Twin's `dcs-stub` seed (`seeds/dcs/merchants.json`) is schema-correct and byte-exact for the one field this lane traced (`in_flight_reservation_enabled`) but has no real-merchant-distribution basis |
| Splitz experiment definitions/variants (the 8+ named IDs) | **Splitz-MySQL-only** | Splitz `experiments`/`variants` tables | Twin's `splitz-stub` implements a deterministic seed scheme (adequate substitute) |
| SQS visibility timeout / DLQ / redrive policy (payouts, cfa, x-balances) | **AWS-resource-only** (Terraform, not in any readable repo) | Terraform / `aws sqs get-queue-attributes` | Twin sets its own values in LocalStack (`queue.sqs.visibilityTimeout=120` for ledger only is app-config; the rest is app-config-silent, matching prod) |
| MySQL/Postgres/Kafka/Redis engine versions | **infra-repo-only, none readable** | Terraform/RDS/MSK/ElastiCache repo — not in the `REPOS` set at all | Twin picks plausible versions (`mysql:8.0`, `postgres:15-alpine`, `apache/kafka:3.8.0`, `redis:7-alpine`), unverified against real prod |
| ConfigMap key lists for the 5 core services | **n/a — none exist** | CONFIRMED: no ConfigMap was found for payouts/fts/ledger/cfa/x-balances in any Deployment manifest (`envFrom` is `secretRef` only everywhere read); config is baked into the image | n/a |
| k8s Secret env-var **names** (not values) for the 5 core services | **repo literal** (names only) | `kube-manifests/templates/{payouts,fts,ledger,cfa,x-balances}/templates/*.yaml` `secretKeyRef`/`envFrom` references | Fully enumerated already (`EFFECTIVE_CONFIG_GAPS.md` D3); twin's `secrets/gen-secrets.sh` generates its own values under matching names |

## 2. Confirmed corrections to `reports/EFFECTIVE_CONFIG_GAPS.md` and to the twin's own prior claims

### 2.1 `[splitz].client_side_eval` — the single highest-impact finding in this pass

`EFFECTIVE_CONFIG_GAPS.md` A3 and the L10 raw report both correctly identify that payouts runs
client-side Splitz evaluation and that `splitz-stub`'s `FetchDecisionContext` always returns
`{"experiments": []}`, but **neither states that this flag is actually FALSE in production**
(client-side eval OFF, server-side `Evaluate`/`EvaluateBulk` ON). Verified directly:

```
payouts/config/default.toml:515   client_side_eval = true
payouts/config/prod.toml:442      client_side_eval = false   # OVERRIDES default.toml
ENV2_COMPOSE/config/templates/base/payouts/arena.toml:546   client_side_eval = true
```

The twin's `arena.toml` matches `default.toml`'s value, **not** `prod.toml`'s override. Net effect:
in prod, every Splitz call goes over the wire `Evaluate`/`EvaluateBulk` RPC, which `splitz-stub`'s
per-merchant seed lookup *does* correctly answer. In the twin as currently configured, every call
instead goes through `evaluateClientSide()` → `FetchDecisionContext` (`goutils/splitz/client.go:1089-1112`),
which `splitz-stub` always answers empty → every experiment resolves to the code-level `off` default
(`payouts/pkg/splitz/splitz.go:91-98`). This silently disables all 38 payouts + 30 fts Splitz-gated
behaviors regardless of how carefully `splitz-stub`'s seed data is built. **One-line fix**: flip
`client_side_eval` to `false` in `config/templates/base/payouts/arena.toml:546`.

### 2.2 Ledger's `ledger-scheduler` runs the wrong job entirely (new finding, not in any prior report)

Neither `EFFECTIVE_CONFIG_GAPS.md` nor the L07/L12 raw reports flag this — it required reading
`ledger/internal/boot/boot.go` and the real k8s CronJob manifest side by side.

- Prod: `kube-manifests/templates/ledger/templates/split-account-balance-update-cronjob-live.yaml:40-41`
  sets env var `LEDGER_SCHEDULER_COMMAND={{.Values.split_account_balance_update_cronjob_scheduler_cmd}}`,
  consumed at `ledger/internal/boot/boot.go:693` (`Execute(ctx, Config.Scheduler.Command, ...)`).
  The real command values are `split-account-balance-update` / `pg-split-account-balance-update`
  (`ledger/internal/common/constant.go:909-910`), run every 10 minutes as a **CronJob** (run-to-completion).
- Twin: `ENV2_COMPOSE/config/templates/base/ledger/arena.toml:438-439` sets `[scheduler].command =
  "journal-created-sns-publish"` (same as the TOML default, `ledger/config/default.toml:425`), and
  `ENV2_COMPOSE/docker-compose.yml`'s `ledger-scheduler` service sets **no** `LEDGER_SCHEDULER_COMMAND`
  env var to override it.

**The twin's `ledger-scheduler` container perpetually runs the SNS journal-created outbox publisher,
never the split-account-balance-update reconciliation.** Split-account balance correctness cannot be
exercised in the current arena at all — not a partial-fidelity gap, a complete absence of the
mechanism. Fix: set `LEDGER_SCHEDULER_COMMAND=split-account-balance-update` on the compose service.

### 2.3 Ledger's `ledger-worker` only ever consumes one of four real queues (new finding)

`ledger/config/{default,arena}.toml:333-334`(twin)/`331-332`(real default) hardcode
`[worker].queueName = "account_create"`, and the twin's compose `ledger-worker` service sets no
`LEDGER_WORKER_QUEUENAME` override (prod uses 4 separate Deployments, one per queue, each with its
own env var — `kube-manifests/templates/ledger/templates/balance-update-live-worker.yaml:57-65`).
Result: messages published to `balance_update`, `journal_create`, `ledger_entry_details_create(+pg)`
(all created by `seeds/localstack/init-queues.sh:8,18-20`) are never consumed by anything in the twin.

### 2.4 `EFFECTIVE_CONFIG_GAPS.md` §L, `[kafka_consumers]/[task]/[topics]` claim — already corrected by L12, re-verified

L12 already corrected the prior claim that these sections "live only in `config/devstack.toml`" —
they are present in `payouts/config/prod.toml:312-343,362-363` with real production values. Re-verified
directly; no further correction needed here. One residual, real deviation not previously stated as a
number: `kafka_consumers.status_update_retry_consumer.config.RetryBackoff` is `30` in prod
(`payouts/config/prod.toml:329`) vs `5` in the twin (`arena.toml:1065`) — a 6x-faster retry cadence,
not just "differs," now quantified.

### 2.5 The twin's own `seeds/s4/cron_schedule.yaml` — corrected count

The twin's own cadence doc is self-marked `_status: UNVERIFIED`, which is appropriate for the
*cadences*. What it does not flag is that its **endpoint paths themselves are wrong** — verified
directly against `payouts/internal/routing/router/cron_routes.go:11-176`:

| `cron_schedule.yaml` entry | its endpoint | real route |
|---|---|---|
| `queued_low_balance_dequeue` | `/v1/cron/queued/dequeue` | `/v1/cron/process_queued_low_balance_payouts` |
| `scheduled_payouts_dispatch` | `/v1/cron/scheduled/dispatch` | `/v1/cron/process_scheduled_payouts` |
| `on_hold_release` | `/v1/cron/on_hold/release` | `/v1/cron/process_beneficiary_bank_on_hold_payouts` |
| `in_flight_reservation_reconcile` | `/v1/cron/reservation/reconcile` | `/v1/cron/process_inflight_reservation_reconciliation` |
| `dual_write_failure_processing` | `POST /v1/payouts/dual_write/failure_processing` | `/v1/cron/payouts_dual_write_failure_processing` |

All 5 listed jobs have invented paths. **This is 5 wrong paths, not "3"** — the "3 wrong paths"
figure carried in an earlier synthesis pass undercounts what is actually in this file. Separately,
and more importantly: `scripts/cron-driver/driver.py` (the *executable* that actually drives the
arena) already uses the **correct** 6 real paths (verified line-by-line against `cron_routes.go`) —
so the doc drift does not affect arena behavior today, only its own documentation's accuracy. Both
`driver.py` and `cron_schedule.yaml` are still missing `process_batch_submitted_payouts` (bulk
NEFT/RTGS dispatch — payout-state-affecting, HIGH severity) as an actual job.

### 2.6 `payouts_sla_breach_monitor` — re-confirms L12's existing correction

L12 already corrected `cron_schedule.yaml`'s claim that this monitor is "internal, in-process not
HTTP-cron": the route `POST /v1/cron/payouts_sla_breach_monitor` is registered under the same
FastCron-gated `/v1/cron` group as every other cron endpoint (`cron_routes.go:143-151`). Whether
FastCron is actually configured to call it, vs. an in-process ticker also/instead calling the same
handler, remains genuinely unresolved (needs a FastCron dashboard check or a ticker grep not done in
this pass).

### 2.7 `ikey_auto_enforcement` and `api_internal_ingress_feature.enable` — new findings, both fixable (not secrets)

Neither is env-injected in prod — both are plain literal overrides of `default.toml`'s fallback,
so both are directly correctable in the twin without any external ask:

| Key | prod (`prod.toml`) | `default.toml` fallback | twin `arena.toml` |
|---|---|---|---|
| `features.ikey_auto_enforcement` | `true` (`:664`) | `false` (`:714`) | `false` (`:750` — inherited the fallback, not prod's override) |
| `api_internal_ingress_feature.enable` | `true` (`:432`) | `false` (`:439`) | `false` (`:470` — same pattern) |

### 2.8 The `ARENA_DCS_URL` patch — scope verified (supersedes L10 §A.4's inference)

The source-level patch in `build/apply-arena-patches.sh:18-34` injects `ARENA_DCS_URL` only into the boot-time
context of `payouts/pkg/dcs/client.go`, `cfa/internal/dcsservice/client.go` and `x-balances/pkg/dcs/client.go`.
That alone would leave per-request `Get`/`EnabledFeatures` calls on the hardcoded hostname table. However the
**module replace** `build/arena-patches/goutils-dcs-{v1.7.3,v1.7.1,v1.5.2}/dcs.go:180-186` already makes
`GetContextUrl(ctx)` return `os.Getenv("ARENA_DCS_URL")` whenever the context carries no URL, and both
`getLoginURI` (`:111`) and `getURLAndMode` (`:192`) consult `GetContextUrl` first. Net: every DCS call from all three
services reaches `dcs-stub`; the earlier golden run logged 89 `kv/get` hits on the stub. L10's "per-request calls
unreachable" inference is withdrawn; the source-level context injection is redundant but harmless. The
arena-only nature of the patch (no-op when `ARENA_DCS_URL` is unset) must stay documented.

### 2.9 fts's Kafka producer leg — profile-dependent, not a flat "MISSING" (refines L09)

L09 states the FTS producer leg is "MISSING (enabled=false + splitz off) -> config-only fix".
Verified more precisely: `enabled` is driven by `config/routes.py`'s `PROFILES` table, keyed off
`ARENA_ROUTE_PROFILE` (default `"monolith"`, under which `kafka=False`). It is **not** a single
static `false` — it is profile-gated, and a `kafka` profile value already exists and produces
`enabled=true` when selected. The "config-only fix" is real, but it's a one-env-var flip
(`ARENA_ROUTE_PROFILE=kafka`), not a template edit — a materially cheaper fix than the L09 framing
implies. What was previously undocumented: `[payouts_service.update_fts_fund_transfer]` is *also*
injected by this same `routes.py` mechanism, post-render (not visible in the static
`env.arena.toml` template, and entirely absent from `env.default.toml`) — confirmed present with the
correct URL in the actual `generated/fts/env.arena.toml:232-236` output.

## 3. Exact minimal external artifacts still needed

| Family | Artifact needed | Minimal ask | Owner |
|---|---|---|---|
| FastCron | Schedule export for the 21 `payouts /v1/cron/*` routes (dashboard has no API) | A dashboard screenshot/export of cadence per route for the payouts job group | Payouts team, `#x-payouts-reliability` |
| Splitz | Variant/rule export for the 8+ named real experiment IDs (`ROyjRWsBowWdWY`, `PYGTRQEzO39PfB`, and the 8 in `EFFECTIVE_CONFIG_GAPS.md` A3) — DB-only, no admin API found | Direct MySQL read on Splitz's `experiments`/`variants` tables, or Twirp `Evaluate`/`FetchDecisionContext` reads for a named synthetic-merchant set | No single owning Slack channel identified; ask `#x-payouts-reliability` who owns the platform |
| DCS | Sanitized per-merchant flag values for 2-3 representative archetypes (Direct/RBL, Shared/current, a third contrast case) — DynamoDB + Aurora mirror, no bulk-read API found beyond scoped `Get` | `POST {DCS_HOST}/v1/kv/get`, scoped to the merchant IDs used in the arena's seed set | Common Platforms, `#tech_infra_dcs` |
| ConfigMap key lists | n/a — confirmed none exist for the 5 core services; nothing to request | — | — |
| Terraform/infra | Engine versions only (MySQL/Postgres/MSK/ElastiCache) for the payouts-adjacent clusters | Version-only export, no credentials | Infra/Datastores, `#gandalf` |
| AWS SQS resources | visibilityTimeout/redrive policy for payouts' 24 queues + cfa/x-balances/xas queues (payouts' own config never sets this, unlike ledger) | `aws sqs get-queue-attributes` sanitized export (names + timeout/redrive only) | Infra/Datastores, `#gandalf` |
| Kafka | Topic/ACL inventory beyond `rx-fts-status-update-events`/`-retry-events` (still open per `EFFECTIVE_CONFIG_GAPS.md` E1/architecture-delta #29) | `#gandalf` ticket: cluster, region, env, topic, consumer group, DESCRIBE-only | Datastores, `#gandalf` |
| Spinnaker | Front50 pipeline-template body (`spinnaker://664e59d6-943b-44e7-a3ab-fa5db69705f6:latest`) — where bake/deploy parameter substitution actually happens | `spin pipeline-template get 664e59d6-…` export, sanitized | Platform/Release-Engineering |

## 4. What does NOT need an external ask (already resolved by repo + this pass)

- Every outbound-client timeout/retry/circuit-breaker value for payouts' 9 downstream clients (fts,
  ledger, mozart, cfa, x_balances, banking_account_service, workflow, shield, splitz) — all repo
  literals, all now diffed against the twin in `TWIN_SPEC/configuration-manifest.yaml`.
- `[shadow_gateway].enabled` — both prod and twin are `false`, CONTRACT-FAITHFUL, confirmed.
- `[fmp].*`, `[payout_config.in_flight_reservation].*` — exact literal matches confirmed.
- The `[splitz].client_side_eval` mismatch (§2.1), the `ledger-scheduler` wrong-command bug (§2.2),
  and the `ledger-worker` single-queue bug (§2.3) are all fixable directly in
  `ENV2_COMPOSE/config/templates/base/` with no external data needed — they are twin bugs, not
  provenance gaps.
- `ikey_auto_enforcement` and `api_internal_ingress_feature.enable` (§2.7) — plain literals, fixable
  without an ask.
- The DCS `Mock` field and the `ARENA_DCS_URL` per-request-context gap (§2.8) — both resolvable by
  editing the vendored patch script; no external data needed, only an SDK-level patch relocation.

## 5. Sources

`reports/fidelity/raw/12_runtime_topology_effective_config.md` (primary); `04_payouts_service_core.md`
§6,§8; `06_fts_routing_status_mozart.md` §2.5; `07_ledger_balances_cfa_banking.md` §5-7;
`09_stork_webhooks_kafka.md` §4; `10_dcs_splitz_pricing_shield_asv.md` §A-B; `reports/EFFECTIVE_CONFIG_GAPS.md`;
direct reads of `payouts/config/{default,prod}.toml`, `fts/config/env.{default,prod-live}.toml`,
`ledger/config/{default,prod-live}.toml`, `ledger/internal/boot/boot.go`, `ledger/internal/common/constant.go`,
`cfa/config/{default,prod}.toml`, `kube-manifests/templates/ledger/templates/split-account-balance-update-cronjob-live.yaml`,
`kube-manifests/templates/ledger/templates/balance-update-live-worker.yaml`; and, on the twin side,
`ENV2_COMPOSE/config/templates/base/{payouts,fts,ledger,cfa,xbalances}/*.toml`, `config/generate.py`,
`config/routes.py`, `build/apply-arena-patches.sh`, `docker-compose.yml`, `seeds/localstack/init-queues.sh`,
`seeds/s4/cron_schedule.yaml`, `scripts/cron-driver/driver.py`, and `generated/fts/env.arena.toml`
(the actual rendered output, checked directly to resolve the `payouts_service.update_fts_fund_transfer`
question).
