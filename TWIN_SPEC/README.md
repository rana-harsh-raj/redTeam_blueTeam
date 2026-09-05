# TWIN_SPEC — RazorpayX Payouts Twin, authoritative specification (2026-09-05)

This directory is the evidence-backed specification for a production-representative Payouts Twin. It supersedes the
planning-era documents in `reports/` (PAYOUTS_ARCHITECTURE.md, PAYOUTS_FLOW_CATALOG.md, ARCHITECTURE_DELTA.md,
SYNTHETIC_FIXTURE_SPEC.md, EFFECTIVE_CONFIG_GAPS.md) wherever they disagree; the disagreements are listed in
`reports/fidelity/ARCHITECTURE_CORRECTIONS.md`. Nothing here was written from the earlier reports alone: every claim
was re-read in the repositories at the commits recorded in `architecture.yaml`.

| File | What it is |
|---|---|
| `architecture.yaml` | Production architecture per family (public API, dashboard, shared/ledger, direct rails, approval, queued, scheduled/on-hold, success/failure/return/reversal, PS→monolith→FTS, direct paths, Kafka, webhooks, statements/recon, cutover, bulk, admin/repair) with confidence tags |
| `components.yaml` | Every component: what it controls, tier today, target tier, real-vs-substitute decision |
| `runtime-topology.yaml` | Production deployments/workers/queues/topics/crons vs the twin's compose services |
| `route-matrix.yaml` | Which path a payout takes and the exact selectors (config keys, DCS/Splitz flags) |
| `state-machines.yaml` | Payout, FTS transfer/attempt, monolith FTA map, ledger journals, workflow, reservation |
| `configuration-manifest.yaml` | Effective production config keys that change behaviour, the twin's values, and arena generation rules |
| `synthetic-data-model.yaml` | Merchant archetypes and every record family with type/constraints/generation rule/exactness |
| `acceptance-invariants.yaml` | Invariants with production provenance and the verifier that proves each |
| `substitute-contracts/` | Exact protocol + behavioural contract for each substitute (monolith, kong-lite, mozart-sim, WFS, Batch, Stork, XAS/recon/repair, DCS/Splitz/pricing/Shield) |
| `schema/` | DDL/JSON schema per datastore derived from migrations and models (no rows) |
| `CODEX_HANDOFF.md` | Ordered implementation plan for Codex |

Companion reports: `reports/fidelity/` (CURRENT_TWIN_FIDELITY.md, PRODUCTION_VS_TWIN_MATRIX.csv, SCHEMA_PROVENANCE.md,
CONFIG_PROVENANCE.md, SYNTHETIC_DATA_PROVENANCE.md, REMAINING_INFORMATION_REQUESTS.md, ARCHITECTURE_CORRECTIONS.md,
`raw/01..12_*.md` lane evidence) and `reports/contracts/` (service API contracts).

Fidelity tiers used everywhere: **REAL**, **CONTRACT-FAITHFUL SUBSTITUTE**, **REPRESENTATIVE SUBSTITUTE**, **UNKNOWN-BLOCKED** (+ **INCORRECT**/**MISSING** for the current twin).

Constraints preserved: no real customer data; no production/staging/DevStack credentials; no runtime dependency on shared
infrastructure; repositories were read only (shallow clones, `gh api` GET for history); nothing was pushed.
