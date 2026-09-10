# M10 — Durable Autonomous Campaign Control Plane — Final Report

Start tag `twin-m9-isolated-factory`. Branch `milestone-10-control-plane`.
New package `controlplane/` promotes the RED_LOOP substrate (durable state,
hypotheses, leases, model routing, typed broker) into a reusable platform on top
of the M8 archkit world model and the M9 Twin Factory. No new milestone-specific
campaign script was written; the existing components were promoted.

## Acceptance
Machine-computed acceptance is `reports/implementation/M10_ACCEPTANCE.json`:
**21/21 gates, accepted=true**, reproducible from a clean checkout with no Docker
and no model provider. It runs the deterministic proof campaign plus the
kill-switch and budget micro-campaigns, evaluates every gate over their durable
ledgers, and runs the control-plane and existing suites
(archkit / twinfactory / snapshot / RED_LOOP) — all green. Artifact hashes in
`M10_ARTIFACT_HASHES.json`.

## What ran
Two proofs, both honest (zero verified findings — no finding was manufactured):

1. **Two isolated live twins (model-free, declared action budget)** —
   `camp-57440dd26812`. Two M9 twins provisioned on **separate colima VMs /
   docker daemons**: `alpha-m10` (profile `full`, seed `m10-alpha-seed`, access
   `merchant_ordinary`, 97/98 healthy) and `beta-m10` (profile
   `critical-payouts`, seed `m10-beta-seed`, access `merchant_fresh`, 92/93
   healthy). Workers act only through the reused typed broker on their own twin
   (each twin's own kong-lite host port and docker socket).

2. **Gateway (real-model) campaign** — `camp-a091dbf89296`, against `alpha-m10`
   using the approved LiteLLM gateway. Genuine autonomous reasoning: an archkit
   thesis, white-box code-corpus exploration, real broker experiments, a recorded
   hypothesis + defined experiment, and a **live model fallback**
   (`claude-opus-4-8` errored on the adversarial mandate → fell back to
   `gpt-5.5`), ending honestly with no candidate.

## Campaign manifests and IDs
- Both manifests are immutable and content-addressed: `campaign_id = "camp-" +
  manifest_content_id[:12]`. Each pins mode, mandate, `architecture_snapshot_id`
  = `5b6a5dade17e…` (the M8 current snapshot), the assigned twins (id, profile,
  seed, starting-access profile), the model pool, budgets and safety/tool policy.
- Two-twin run: `camp-57440dd26812`. Gateway run: `camp-a091dbf89296`.
- The human supplied only the broad mandate ("assess whatever most weakens the
  payouts money-movement boundary; you are given no target, vulnerability class or
  attack path"), mode, budgets and policy — no target, class or path.

## Duration and action counts
| run | wall (s) | model calls | broker/tool actions | planning cycles |
|---|---|---|---|---|
| two-twin (model-free) | 457 | 120 | 120 | 4 |
| gateway (real model) | 178 | 12 | 32 | 1 |

## Twins, models, theses, cells, hypotheses (two-twin run)
- theses: 16 (self-generated from archkit families and the uncovered
  `payouts-service` trust boundary), over 4 planning cycles; 16 distinct subjects.
- tasks: 32 (30 done), split evenly `alpha-m10`:16 / `beta-m10`:16.
- deliberate independent replicas: 16; dynamic cells: 32 (created, reallocated on
  twin recovery, retired on exhaustion).
- hypotheses: proposed via the campaign-wide lifecycle with semantic dedup —
  **15 duplicate hypotheses suppressed** across cells.
- models: deterministic worker policy (declared action budget); routing recorded.
  Gateway run used `gpt-5.5` (after the `claude-opus-4-8` fallback).

## Recovery events (live)
- **Injected worker failure**: a worker subprocess was SIGKILLed mid-task; its
  lease expired and the task was requeued and completed (worker-kill →
  lease_expired → task_recovered).
- **Director kill + restart**: the Director objects were dropped and rebuilt from
  the durable ledgers; the campaign resumed and finished (campaign_resumed).
- **Model fallback**: demonstrated live on the gateway run (claude → gpt-5.5) and
  deterministically in acceptance.
- **Twin failure**: `beta-m10` was stopped; the engine detected it unhealthy,
  attempted an in-place restart (core datastores/services recovered to 75/93 but
  the async worker fleet exited 0 on restart — a known M9 restart limitation), so
  it **escalated to reproduce**, creating `beta-m10-r` from the immutable
  manifest. The reproduce was interrupted mid-build to conserve host memory;
  reproduce-to-healthy plus cell re-homing is proven in the deterministic
  acceptance (gate M10-10).

## Tool and model usage
- Two-twin run: 120 tool calls (real merchant provisioning + `merchant_request`
  reads through each twin's kong), 120 model calls; every call recorded and tagged
  with campaign/twin/cell/task/thesis.
- Gateway run: 32 tool calls (`code_search` 12, `code_read` 8, `merchant_request`
  4, `code_find_symbol` 2, `code_list` 3, `whoami` 1, `record_hypothesis` 1,
  `define_experiment` 1); 12 model calls; ~211k prompt / 2.1k completion tokens;
  ≈ $1.09 estimated.

## Candidate and verified results
- **Verified findings: 0** in both live runs. **Candidates: 0** — the honest
  outcome; no finding was manufactured.
- Verification mechanics (independent replay + deterministic judge, decorrelated
  model, never the claimant) are proven in acceptance (gates M10-13, M10-14): a
  candidate is promoted only when an independent decorrelated verifier reproduces
  it AND the judge confirms it over authoritative state; inconclusive evidence
  never promotes.

## Duplicate and invalid-candidate counts
- duplicate hypotheses suppressed (two-twin run): 15; duplicate theses suppressed:
  tracked per run in the coverage view.
- invalid candidates: 0 (no candidates claimed live).

## Human intervention
- Human task assignments: **0**. The human set only mode, budgets, safety/tool
  policy and the broad mandate. Operator control actions (pause/resume/terminate)
  are logged as such and are not task assignments.

## Host limits
- Host: Apple M-series, 15 CPU, 24 GiB. Two twins (`full` 9 GiB-cap + focused
  8 GiB-cap VMs) ran **concurrently** alongside the untouched M7 arena on the
  default daemon; free memory held at ~36% (colima/vz balloons rather than
  reserving). A third VM (the interrupted reproduce) pushed the host, so the twin
  reproduce drill was stopped before completion.

## Remaining work before the capability / evidence plane
- a generalized capability graph and a cross-campaign finding registry;
- an executive dashboard over campaigns;
- scale beyond two twins toward ten;
- richer live verification against authoritative twin state (the live judge is
  best-effort and returns inconclusive on error, keeping candidates as candidates).

## Paths
- package: `controlplane/` (+ `controlplane/SCHEMA.md`, `controlplane/tests/`)
- runbook: `reports/implementation/M10_RUNBOOK.md`
- acceptance: `reports/implementation/M10_ACCEPTANCE.json`, hashes
  `reports/implementation/M10_ARTIFACT_HASHES.json`
- campaign timelines + views: `reports/implementation/m10/camp-57440dd26812-*.json`
  (two-twin), `…/camp-a091dbf89296-*.json` (gateway),
  `…/camp-986dc89e81b7-*.json` (deterministic proof)
