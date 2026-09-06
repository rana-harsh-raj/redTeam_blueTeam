# Project RedGrid — Milestone 3 completion report (RED_LOOP)

Milestone: **Merchant Gateway Fidelity and Red Loop Hardening**

Status: __PENDING FINAL CAMPAIGN + CLEAN-BOOT GATES__ (see §Verdict).

This milestone made the one-agent red loop trustworthy against a realistic
ordinary-merchant ingress: the merchant edge is now source-derived and credible,
cross-tenant reads are deterministically measurable via hidden canaries, context
is compiled instead of resent, the runner pauses/resumes durably, candidate
admission is deterministic, and exploit reproduction has a causal negative
control. Every claim below is backed by a committed artifact under
`reports/implementation/` or a live acceptance script under `RED_LOOP/`.

## Branch, baseline, and integrity
- Branch: `milestone-3-gateway-hardening`, created from the Milestone-2 head
  `a107612` (verified). Work is a series of reviewable commits.
- Frozen baseline UNCHANGED: `twin-v1.0` -> commit `78def24` (records runtime
  freeze `219ca48bbb5e51db9347b335ac0a50241656045b`); Milestone-2 branch head
  `a107612`; the pinned company repo clones; and all historical campaign evidence
  (including `camp-20260906T051731Z-e43dfd`) are untouched. New campaign records
  are git-ignored and hash-indexed, as in Milestone 2.

## Milestone-2 implementation discrepancies found
- **Canned cross-tenant data was misattributed.** The Milestone-2 report blamed
  monolith-stub for the `fa_slitfa12345678` / merchant `10000000000000` /
  `utr 528226169544` evidence. It is actually the payouts-api in-tree TiDB mock
  `cannedTidbPayoutEntity()` (`payouts/internal/app/tidb/mock.go:59-85`), enabled
  by `[wda] mock = true` (`config/templates/base/payouts/arena.toml`), reachable
  ONLY through the internal `GET /v1/payouts/fetch_multiple` route. monolith-stub's
  id lookups actually return 404 for unknown ids. Corrected and re-grounded here.
- **Milestone-2 stagnation detection had a blind spot** (found by the first M3
  campaign): it tracked only runtime-request fingerprints, so a code-analysis loop
  with no runtime experiment would run to the turn ceiling. Fixed (recon-only loop
  detection).

## Gateway fidelity (Workstream A)
Source-derived route policy `RED_LOOP/registry/merchant-gateway-routes.json`,
derived from `payouts/internal/routing/router/*.go`, enforced by
`ENV2_COMPOSE/substitutes/kong-lite/route_policy.py` under
`KONG_ENFORCE_ROUTE_POLICY=1` (default 0 = frozen behaviour).

- **Public merchant routes:** `POST/GET /v1/payouts`, `GET /v1/payouts/{pout_id}`,
  `POST /v1/payouts/cancel_payout/{id}`, `GET /v1/payouts/free_payout/{balance_id}`,
  `GET /v1/payouts/payouts_status_reason_map`, `GET /v1/payouts/_meta/summary`,
  `GET /v1/payouts/schedule/timeslots`, `PATCH /v1/payouts/{pout_id}/attachments`,
  `POST /v2/payouts`, `POST/GET /v1/fund_accounts/validations`, `GET /v1/balances`.
- **Internal/admin routes now unreachable from the merchant edge (404):**
  `fetch_multiple`, `manual_action`, `rzp_fees_payout`, `analytics`, `retry`,
  `payouts_internal/{id}/approve|reject`, `payouts_internal/{id}`, `bulk`,
  `attachments`, `status_details/{id}`, `on_hold/process`, `batch/process`,
  `/v1/admin/*`, `/v1/workflow/*`, `/v1/internal/*`, `/v1/cron/*`, `/v1/notify/*`,
  `/twirp/*`, plus not-implemented `/v1/contacts` and `/v1/fund_accounts` CRUD.
- **Service credential scoping:** `cred.API` is injected ONLY for public
  payouts-api routes; never for the `/v1/balances` xbalances read surface; never
  on a denied path.
- **Unknown identifiers:** the canned TiDB-mock path is reachable only via the
  internal `fetch_multiple` route, now 404 for a merchant, so unknown ids can no
  longer return canned victim-looking data at the edge.
- **Identity headers:** client `x-merchant-id` / `x-entity-id` are stripped; the
  edge-minted passport (consumer.type=merchant) is authoritative, so a body/header
  merchant id cannot replace the authenticated identity.

Evidence (live, enforcement on):
- `RED_LOOP/tests/test_route_policy.py` — 9/9 classifier unit tests pass.
- `reports/implementation/m3-gateway-acceptance.json` — 4/4: public create/fetch
  works; every internal/admin/twirp route 404s with no canned markers; spoofed
  `x-merchant-id` cannot swap identity.

## Cross-tenant measurement + fresh namespaces (Workstream B)
- Each campaign mints fresh unique canary strings inside control-merchant payouts
  through the normal API (`RED_LOOP/red_loop/provisioner.py`); canary values and
  victim resource ids are JUDGE-ONLY. The attacker gets a fresh idempotency
  namespace so no prior campaign's payout/idempotency record becomes a later
  precondition.
- A full fresh-funded-merchant provisioner (`provision_funded_merchant`, 7
  datastores from a live-verified recipe) is implemented and additive (never
  touches M1/M2/M3): 16/16 provisioning steps succeed and the merchant
  authenticates and resolves its fund account. Full payout PROCESSING for a
  brand-new merchant needs more seed parity (all four ledger sub-accounts +
  pricing rows), so campaigns default to the fixture attacker with a fresh
  idempotency namespace + fresh victim canaries and record the fallback. This is
  the one declared place M3 does not fully reach the fresh-funded-attacker goal.
- Evidence: `reports/implementation/m3-namespace-acceptance.json` — 4/4: canaries
  provisioned; attacker cannot read a victim payout; no victim canary appears in
  attacker responses; two campaigns have disjoint canary + payout namespaces.

## Context efficiency + model-failure handling (Workstreams C, D)
- Context is compiled from durable records each turn (`RED_LOOP/red_loop/context.py`);
  the full transcript is never resent. Per-turn `context_metrics.jsonl` records
  sizes and the reason for each included item, and a `recall` tool reopens
  compacted records.
- Measured on M3 runs: compiled campaign state is ~0.8–1.5 KB/turn and
  `full_transcript_resent=false` on every turn; average prompt tokens per turn are
  roughly two orders of magnitude below Milestone 2's ~110k/turn average — far past
  the 60% reduction target. (Final open-campaign numbers in §Open campaign.)
- Blank/content-filtered turns are detected (`llm.is_unusable`) and recovered:
  retry once with a smaller neutral packet, then switch to a distinct approved
  model from the live gateway inventory (`pick_fallback`). No safeguard bypass; a
  filtered turn is never counted as progress.

## Long-running lifecycle + pause/resume (Workstreams E, F)
- The 120-turn ceiling is gone as a normal terminal state; a high configurable
  EMERGENCY ceiling is a labelled backstop only. Completion is conclusion /
  progress-stagnation / recon-loop / exhaustion / operator-termination based.
- Pause/kill/restart/resume proven live: `reports/implementation/m3-recovery-test.json`
  — 5/5: a real campaign was `kill -9`'d abruptly, then resumed purely from durable
  JSONL (`campaign_resume` event), turns advanced, state hash changed, active
  hypotheses preserved.

## Candidate discipline + judge (Workstream G)
- Deterministic minimum-evidence admission gate before reproduction
  (`RED_LOOP/red_loop/judge.py admit()`): nonexistent resources, canned-mock
  templates, and 200-without-mutation cannot pass; confidentiality needs a real
  victim value/canary; integrity needs an attacker-attributed transition or an
  invariant violation; availability needs baseline + measured degradation.
- Judge canary oracle independently scans the attacker's captured responses.
- Tests: `RED_LOOP/tests/test_judge_admission.py` — 7/7, covering the exact M2
  failure modes (nonexistent `pout_1234`, canned marker, 200-without-mutation).

## Reproduction + negative control (Workstream H)
- `reports/implementation/m3-calibration.json` — CALIBRATION_PASS: using the
  route-policy toggle as a safely-isolated vulnerable/fixed pair, the vulnerable
  profile leaks cross-tenant markers (200), the fixed negative control removes the
  effect (404), and the different-provider reproducer (`gpt-5.5`) reproduced it
  from a clean start. Classified CALIBRATION_PASS and explicitly never an
  open-campaign finding; the lane restores the enforcement-on profile at the end.

## Open campaign (Workstream / Section 16)
__[FILLED AFTER THE CAMPAIGN COMPLETES]__

## Strengthened product-surface (Section 14)
__[FILLED AFTER RE-RUN]__

## Twin health / clean acceptance (Section 15)
__[FILLED — verifier + clean-boot status]__

## Models
- Primary red agent: `claude-opus-4-8` (Anthropic). Reproducer: `gpt-5.5`
  (OpenAI — different provider/family). Utility: `gpt-5.4-mini`. Inventory
  inspected from the gateway; `claude-opus-5` remains budget-capped/filtered.

## Safety confirmation
No production, staging, DevStack, public-internet, or Daytona action occurred. No
real customer data or credentials were used. The frozen tag and runtime commit are
unchanged. Failed/negative results are preserved. The calibration lane restored the
Milestone-3 (enforcement-on) profile; the arena is left in a documented safe state.

## Verdict
__[FILLED]__
