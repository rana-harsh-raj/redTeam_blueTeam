# Owner request packet: Payouts Twin, Milestone 1

Prepared for: internal system owners contacted on behalf of Project RedGrid.
Source: `reports/claude-review/TWIN_V1_AUDIT_AND_NEXT_STEP.md` (audit date 2026-09-05) and
`reports/implementation/REMAINING_BLOCKERS.md`.

## 1. Context

The Payouts Twin is an offline, fully local execution environment (five real core binaries built from pinned
source, plus bounded substitutes for Kafka, Splitz, Stork, Kong and the API monolith) used to reproduce and study
production behaviour without touching production, staging or DevStack. It has no network egress and carries no
merchant, customer or credential data; every fixture is synthetic. A small number of open questions cannot be
answered from the pinned source clones or from the twin's own runtime evidence because they depend on facts that
only exist in live deployed configuration or production logs: per-merchant feature-flag rollout state, effective
production config values, and observed frequency of a code path. This packet lists exactly those questions.

Constraints that apply to every request below, without exception:
- No production customer data, payloads, or database rows.
- No merchant identifiers. Where a request needs segmentation, respond with segment names, cohort sizes, or
  percentages only, never merchant IDs.
- No credentials, tokens, connection strings, or access grants of any kind.
- No DevStack, staging, or production access for the twin team. All artifacts are pulled by the owning team and
  handed over as files.
- Every artifact should be schema-only, config-only (secret values redacted or withheld), or count-only, as marked
  per request.

None of these requests block the Milestone 1 freeze (tagging `twin-v1.0`). They inform Milestone 2 (the red-agent
loop) and the freeze's declared-exclusions list. See the summary table in section 4.

---

## 2. PRIORITY requests

### P1: Effective rollout of Splitz experiment `PYGTRQEzO39PfB` (`fire_status_update_kafka`)

- **Question.** Is Splitz experiment `PYGTRQEzO39PfB` (feature `fire_status_update_kafka`) on for any production
  merchant, and if so what is the rollout percentage, which variants exist, and how large is the affected
  segment (by count, not by merchant list)?
- **Why it matters.** FTS gates the entire Kafka status-publish leg on this experiment
  (`fts/config/env.prod-live.toml:364`, cited in `TWIN_V1_AUDIT_AND_NEXT_STEP.md:136`), fail-closed to false. The
  twin has reproduced a real production-source defect on this leg (consumer job registration is never wired up,
  `TWIN_V1_AUDIT_AND_NEXT_STEP.md:114-126`), but whether that defect is currently reachable by any live merchant, or
  is dormant because the experiment is at 0 percent, changes how the twin's verdict should be reported and whether
  Milestone 2's red-agent loop should treat the Kafka route as a live-risk finding or a latent one.
- **Exact artifact required.**
  - Format: a small JSON or CSV export, schema: `experiment_id, variant_name, rollout_percentage, segment_description, cohort_size, revision_date`.
  - One row per variant. `segment_description` is a free-text label (e.g. "enterprise banking partners"), never a
    merchant list.
- **Explicit exclusions.** No merchant IDs or lists. No payloads. No credentials or Splitz API tokens. No
  merchant-level assignment export, only variant-level aggregate rollout.
- **Likely owner team.** FTS platform / Payouts routing owners (per `TWIN_V1_AUDIT_AND_NEXT_STEP.md:395`, Q1).
- **What the twin does with each answer.**
  - 0 percent / no merchants on: the audit's "reproduced defect" is recorded as latent-in-production; Milestone 2's
    known-gaps list keeps it as a calibration item, not a live-risk flag.
  - Nonzero: recorded as an active production risk in the freeze's `declared_deviations` and surfaced explicitly to
    Payouts on-call, independent of the twin's own remediation timeline.

### P2: Combination of PS-direct FTS creation with the Kafka status route

- **Question.** Does any merchant combine Payouts-direct FTS creation (`configs.fts_request_from_ps`
  whitelist/blacklist, or Splitz `ExperimentForFtsRequestFromPayoutsService` / config key
  `fts_request_from_payouts_service_experiment`, experiment id `QTwJ8uQKns0pOE`) with the Kafka status route
  (`fire_status_update_kafka`, `PYGTRQEzO39PfB`)? A yes/no answer plus a description of the affected segment is
  sufficient.
- **Why it matters.** This is the one combination with no compensating path. When FTS is enabled and the Kafka
  experiment is on, `fts_status_propagation` is strictly exclusive of the HTTP webhook
  (`fts/internal/transfer/service.go:1139-1144`, cited `TWIN_V1_AUDIT_AND_NEXT_STEP.md:150-152`). For a transfer
  created directly by Payouts (`X-Origin: payouts`), the monolith has no FTA row and cannot relay status on the
  monolith's own Kafka consumer, unlike a monolith-created transfer (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:140-146`,
  section 3.2). If this combination exists in production, the twin's reproduced Shared-Kafka defect
  (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:110-126`) is not just latent risk, it describes an active gap with no fallback.
- **Exact artifact required.**
  - Format: one of (a) the same Splitz export as P1 for `fts_request_from_payouts_service_experiment` (id
    `QTwJ8uQKns0pOE`), cross-referenced against the P1 export by segment overlap, described in prose or a small
    overlap table `segment_description, in_ps_direct (y/n), in_kafka_route (y/n), overlap_cohort_size`; or (b) a
    direct written yes/no answer with an approximate cohort size and segment description if a full cross-tab export
    is not feasible.
  - Plus: the config-side whitelist/blacklist state for `configs.fts_request_from_ps` in prod, as counts only (e.g.
    "N merchants whitelisted, list-mode enabled") not as a merchant list.
- **Explicit exclusions.** No merchant IDs, no whitelist/blacklist file contents, no payloads, no secrets.
- **Likely owner team.** Payouts service owners (per `TWIN_V1_AUDIT_AND_NEXT_STEP.md:396`, Q2).
- **What the twin does with each answer.**
  - No overlap: the audit's "combination with no compensation" stays a theoretical worst case documented in the
    fidelity matrix, not escalated further.
  - Overlap exists: escalated as a production finding outside the twin's scope (this is a live incident risk, not a
    twin defect) and referenced in Milestone 2's known-gaps list as a real, not hypothetical, gap the red-agent
    loop should be calibrated against.

### P3: Sanitized 30-day production log counts for the payouts Kafka consumer pods

- **Question.** Over the last 30 days, how many times did each of the following message names appear in the logs
  of `payouts-kafka-fts-status-updates-consumer` and its retry counterpart
  (`payouts-kafka-fts-status-updates-retry-consumer`)?
  - `PAYOUT_DUAL_WRITE_DISPATCH_TO_QUEUE_FAILURE`
  - `QUEUE_PUSH_FAILED_FOR_LEDGER_PROCESSED_EVENT`
  - `KAFKA_FTS_STATUS_UPDATE_TASK_FAILED`
  - `KAFKA_FTS_STATUS_UPDATE_TASK_INIT`
  - `KAFKA_FTS_STATUS_UPDATE_TASK_FINISHED`
- **Why it matters.** The twin has reproduced, with full mechanism, a consumer-side defect where job registration is
  never wired for the Kafka boot path (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:114-120`), and separately a crash caused by a
  twin-only placeholder broker config that will not occur in production because production dials real brokers
  (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:182-209`, section 3.7). `PAYOUT_DUAL_WRITE_DISPATCH_TO_QUEUE_FAILURE` and
  `QUEUE_PUSH_FAILED_FOR_LEDGER_PROCESSED_EVENT` are the exact log lines the twin's own reproduction emitted
  (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:191, 179`). A nonzero production count confirms the underlying bootstrap defect
  is live and firing today; a zero count across all five names suggests the Kafka route is not currently exercised
  in production (consistent with P1 returning 0 percent). The `INIT`/`FINISHED` counts establish the baseline
  message volume the failure counts should be read against.
- **Exact artifact required.**
  - Format: a small table or CSV, schema: `pod_name, message_name, count_30d`. Ten rows total (five messages times
    two pods). Counts only, no timestamps of individual events, no sample log lines, no payload fields.
  - Sanitization: message-name match counts from the existing log pipeline (e.g. a saved search / dashboard query
    result), not raw log export.
- **Explicit exclusions.** No raw log lines, no payloads, no payout or merchant identifiers, no timestamps beyond
  the 30-day window boundary, no credentials or log-system access grants.
- **Likely owner team.** Payouts on-call / observability (per `TWIN_V1_AUDIT_AND_NEXT_STEP.md:397`, Q3).
- **What the twin does with each answer.**
  - All zero: strengthens the case that the reproduced defect is dormant; the freeze documents the route as
    "source-faithful expected failure, not currently observed in production" (extends the language planned for
    `expected_failures` in `TWIN_V1_AUDIT_AND_NEXT_STEP.md:228-229`).
  - `QUEUE_PUSH_FAILED_FOR_LEDGER_PROCESSED_EVENT` nonzero: confirms Shared-account Kafka status updates are
    actively failing to reach Ledger in production today; escalated immediately to Payouts on-call as a live
    incident, independent of twin timelines.
  - `PAYOUT_DUAL_WRITE_DISPATCH_TO_QUEUE_FAILURE` nonzero without the above: narrows the defect to the dual-write
    path specifically (`dualWrite/payout.go:47-54`, cited `TWIN_V1_AUDIT_AND_NEXT_STEP.md:179`) and is noted as a
    secondary but related finding.

---

## 3. SECONDARY requests

Same fields as above, condensed to one line each. Exclusions common to all nine: no merchant IDs, no payloads, no
credentials, no raw rows, schema/config/count only as stated.

### S1 (audit Q4): Topic partition counts and alert topic-name mismatch
Question: partition counts of `rx-fts-status-update-events` and `rx-fts-status-update-retry-events`, and whether
consumer-lag alerts (which reference `prod-fts-status-update-events`, matching neither repo config) fire on the
real topic. Why it matters: a label mismatch could mean a stuck consumer, like the one the twin reproduced, pages
nobody (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:398`). Artifact: `kafka-topics --describe` output (name, partitions,
replication factor) for both topics, plus the alert rule's topic label field. Exclusions: no broker credentials,
no full alert-manager config. Owner: Kafka platform / alert-rules owners. Use: confirms or refutes a monitoring
gap; recorded in declared-deviations either way; does not block Milestone 1.

### S2 (audit Q5): `PAYOUTS_FEATURES_*DUAL_WRITE` effective values
Question: effective values of the `PAYOUTS_FEATURES_*DUAL_WRITE` keys in the prod secret. Why it matters: governs
forward/reverse dual-write behaviour; the twin's fixture is representative, not confirmed
(`TWIN_V1_AUDIT_AND_NEXT_STEP.md:399`). Artifact: names-and-values export for those keys only, or a statement of
which are true/false/percentage-gated. Exclusions: no other secret keys. Owner: Payouts service owners. Use:
confirms fixture representativeness; recorded as a declared deviation if it diverges; does not block Milestone 1.

### S3 (audit Q6): Schema-only DDL for `payouts.payout_details` and `api.features`
Question: `SHOW CREATE TABLE` for `payout_details` (`beneficiary_bank_code` nullability/default/indexes) and
`features` (`name` width), at the deployed migration revision. Why it matters: no located service migration for
`beneficiary_bank_code`; `features.name` is 25 chars in the located migration but a real feature name needs 29,
and the twin's 255-char width is an explicit representative extension (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:400`,
`REMAINING_BLOCKERS.md:52-53`). Artifact: `SHOW CREATE TABLE` output, schema only, no row data. Exclusions: no
table contents, no other tables. Owner: Payouts DBA / API DBA. Use: corrects the schema fixture if it diverges;
recorded in `SCHEMA_COMPARISON.md`; does not block Milestone 1.

### S4 (audit Q7, marked not needed for v1.0): Cadence version and `workflow_configs` rows
Question: Cadence server version and `workflow_configs` rows for `payout-approval`. Why it matters: only needed
when promoting from the thin workflow stub to a real Workflow Service integration; explicitly not needed for the
v1.0 freeze (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:401`, `:307-309`). Artifact: non-secret version manifest plus the
config rows (policy identifiers, timeouts), schema and values only. Exclusions: no Cadence admin access, no other
workflow rows. Owner: Workflows service owners. Use: deferred to whichever milestone promotes the real Workflow
Service; not needed for v1.0.

### S5 (blockers list): x-balances `sub_balance` / `sub_balance_limit` migrations
Question: is there an approved, wired migration for `sub_balance` / `sub_balance_limit`, and their effective
schema if these paths enter scope. Why it matters: standalone SQL/models exist but no wired migration was
established; unseeded today (`REMAINING_BLOCKERS.md:54`). Artifact: migration registration/order listing plus
schema-only DDL, if this family enters scope. Exclusions: no row data, no other tables. Owner: x-balances service
owners. Use: only relevant if a future milestone promotes sub-balance behaviour; not needed for Milestones 1-2.

### S6 (blockers list): Sanitized Splitz/DCS assignments for selected synthetic segments
Question: for the twin's own synthetic segment names (not real merchants), what would the revisioned Splitz
assignment and DCS policy look like for the relevant experiments. Why it matters: source defaults cannot
reconstruct deployed experiment weights or per-merchant policy (`REMAINING_BLOCKERS.md:56`). Artifact: redacted,
revision-stamped export scoped to the named synthetic segments: experiment id, variant, rollout percentage,
nonsecret policy values. Exclusions: no real merchant IDs, no unrelated experiments. Owner: Splitz platform team /
experiment owners. Use: improves Milestone 2 episode fixture realism; does not block Milestone 1.

### S7 (blockers list): FastCron / scheduler cadence export
Question: production cadence (interval, trigger type) for the FastCron/scheduler jobs the twin currently runs as
a single manual trigger. Why it matters: the one-shot Ledger scheduler execution does not establish recurring
cadence (`REMAINING_BLOCKERS.md:58`). Artifact: non-secret cadence/schedule export (job name, interval, trigger
type) for the relevant jobs only. Exclusions: no credentials, no unrelated jobs. Owner: Payouts / scheduling
platform owners. Use: informs Milestone 2 episode runner design; does not block Milestone 1.

### S8 (blockers list): SQS visibility timeout and DLQ semantics
Question: effective visibility timeout, retry/backoff, and DLQ semantics for the queues the twin models with a
single-node representative engine. Why it matters: local intervals and triggers are representative approximations
(`REMAINING_BLOCKERS.md:58`). Artifact: non-secret queue config export (visibility timeout, max receive count, DLQ
target, backoff policy) for the relevant queues only. Exclusions: no queue contents, no credentials. Owner:
Payouts / queueing platform owners. Use: informs fidelity of the queue substitute; does not block Milestone 1.

### S9 (blockers list): Bank/mock dependency versions (Mozart)
Question: approved exact revisions of the orchestrator, `integrations-go`, and `integrations-utils` artifacts a
real Mozart build/mock would depend on. Why it matters: a prior private-module build attempt failed for lack of
these revisions; this does not establish infeasibility (`REMAINING_BLOCKERS.md:36`). Artifact: a version/revision
manifest for the three named artifacts, names and pinned versions only. Exclusions: no credentials, no unrelated
private modules, no bank-integration secrets. Owner: Mozart / bank-integration platform owners. Use: only relevant
if a future milestone promotes real Mozart gateway/version branching (`TWIN_V1_AUDIT_AND_NEXT_STEP.md:323-326`);
does not block Milestones 1-2.

---

## 4. Summary table

| id | question (short) | artifact | owner | blocks v1.0 freeze? | unblocks which later milestone |
|---|---|---|---|---|---|
| P1 | Rollout of `fire_status_update_kafka` (`PYGTRQEzO39PfB`) | Splitz variant/rollout export | FTS platform / Payouts routing | no | Milestone 2 red-agent calibration; freeze declared-deviations accuracy |
| P2 | PS-direct create + Kafka status combo | Splitz export overlap + config counts | Payouts service owners | no | Milestone 2 known-gaps calibration |
| P3 | 30-day log counts, 5 message names, 2 pods | Sanitized count table | Payouts on-call / observability | no | Milestone 2 known-gaps calibration; live-incident triage if nonzero |
| S1 | Topic partitions + alert name mismatch | `kafka-topics --describe` + alert rule | Kafka platform / alert-rules | no | Observability hardening, not tied to a milestone |
| S2 | `PAYOUTS_FEATURES_*DUAL_WRITE` values | Names/values export | Payouts service owners | no | Fixture accuracy for future milestones |
| S3 | `payout_details` / `features` DDL | `SHOW CREATE TABLE` output | Payouts DBA / API DBA | no | Fixture accuracy for future milestones |
| S4 | Cadence version + `workflow_configs` | Version manifest + config rows | Workflows service owners | no | Milestone 2+ real Workflow Service promotion (not needed for v1.0) |
| S5 | x-balances sub_balance migrations | Migration listing + schema DDL | x-balances service owners | no | Future sub-balance family promotion |
| S6 | Splitz/DCS assignments, synthetic segments | Redacted assignment export | Splitz platform / experiment owners | no | Milestone 2 episode fixture realism |
| S7 | FastCron/scheduler cadence | Cadence export | Payouts / scheduling platform | no | Milestone 2 episode runner design |
| S8 | SQS visibility/DLQ semantics | Queue config export | Payouts / queueing platform | no | Milestone 2 fidelity of queue substitute |
| S9 | Mozart/bank mock dependency versions | Version/revision manifest | Mozart / bank-integration platform | no | Future real-Mozart promotion (not needed for Milestones 1-2) |

None of the twelve requests blocks the Milestone 1 freeze. Milestone 1 (tagging `twin-v1.0`) proceeds on twin-side
fixes alone, per `TWIN_V1_AUDIT_AND_NEXT_STEP.md:280-289`. These requests inform the accuracy of declared
deviations and the design of Milestone 2's red-agent loop.

---

## 5. Handling notes

- **Return format.** Every artifact above should come back as one of: a schema-only DDL export
  (`SHOW CREATE TABLE`), a redacted/aggregated configuration export (names and values, or names and value-classes
  where the value itself is sensitive), or a count-only table (message name and count, no underlying events). No
  request in this packet asks for raw rows, live access, or credentials.
- **Segment naming.** Wherever a request needs to describe which merchants are affected, respond with a synthetic
  segment label and a cohort size (e.g. "segment A, ~40 merchants"), never a merchant ID or list.
- **Delivery.** Files or pasted text are sufficient. No shared database access, no read replica grants, no VPN or
  bastion access is required or wanted.
- **What the twin team will not request or accept:** raw production database rows, production payloads or webhook
  bodies, credentials or access tokens of any kind, or access to DevStack, staging, or production environments.
  If an owner team believes an answer requires granting such access, the correct response is to say so and the
  twin team will either narrow the request further or mark the question permanently ASSUMED rather than accept the
  access.
- **Point of contact.** Route responses to the Payouts Twin / Project RedGrid engineering lead; questions about
  scope can reference the specific request id (P1-P3, S1-S9) from this packet.
