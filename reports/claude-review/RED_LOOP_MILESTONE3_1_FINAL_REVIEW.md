# RED_LOOP Milestone 3.1 — Final Review

**Verdict: NOT READY** — 20 of 21 required acceptance gates pass; the single
outstanding gate, `second_open_campaign`, is blocked by a `claude-opus-4-8`
budget cap on the LiteLLM key (HTTP 429 `budget_exceeded`), not by any harness or
twin defect. The tag `red-loop-m3.1` is **not** created.

- **Branch:** `milestone-3-1-claude-final`
- **Final commit SHA:** see `git rev-parse HEAD` on this branch (recorded at commit
  time; this report and the acceptance JSON are committed together).
- **Base:** `milestone-3-1-calibration` head `a63185f` (two Codex commits above
  `milestone-3-1-clean-parity` head `0dd9b32`).
- **Tag `red-loop-m3.1`:** NOT created (a required gate is pending). Resume the
  campaign once the opus-4-8 budget is raised/reset, then re-run acceptance and tag
  only if every gate passes.

## Historical references preserved

`twin-v1.0` (commit `78def24`), runtime freeze `219ca48`, `milestone-2-red-loop`
`a107612`, `milestone-3-gateway-hardening` `3873e96`, `milestone-3-1-clean-parity`
`0dd9b32`, and the two Codex commits (`d20adb4`, `a63185f`) are unmodified. No
frozen or historical branch/tag was moved. Company repository clones untouched.

## What Codex completed vs. what Claude completed

**Codex (`d20adb4`, `a63185f`) — verified and retained:**
- Deterministic, fail-closed pricing-ID reservation (`RED_LOOP/red_loop/pricing_ids.py`):
  preserves the bounded 14-char hashed plan id, reserves before the first seed/DB
  write, validates every constrained id against `pricing_rule_id CHAR(14)`, and
  fails a collision before seeding without mutating the registry. 8 pricing tests +
  9 route-policy tests pass. **Retained** — it meets every collision-protection
  criterion (deterministic; two unrelated merchants cannot silently share a plan
  id; schema-length validated; collision fails closed; the collision path is tested,
  including a real truncated-hash colliding pair `ARENAM90001408`/`ARENAM90001571`).

**Codex did not do / declined (completed by Claude):**
- Did not rerun the fresh-merchant 8/8 after its changes → **rerun on current head**.
- Explicitly **declined** the fresh-ID calibration (its README said "not implemented")
  → **built** the fresh-ID tenant-isolation calibration (regression/fixed pair,
  generator, oracle, ID-free semantic replay, deterministic replay, negative
  control) and the different-provider reproducer.
- No second campaign → **run** with a genuinely fresh, self-verified attacker.
- Found and fixed a provisioning defect that would have silently defeated the
  fresh-attacker campaign (see below).

## Current-head fresh-merchant 8/8 (Phase A)

Rerun on the current-head provisioner (through the new `reserve_plan_id` path), on
the live arena at `KONG_ENFORCE_ROUTE_POLICY=1`: **8/8**
(`reports/implementation/m31-fresh-merchant.json`). Fresh merchant
`ARENAM90546278`, payout `pout_TYmxm31P2ny22w` → terminal `processed`, fees 200 /
tax 36. No-static-reuse proof (`m31-fresh-merchant-noreuse.json`): own merchant id,
balance `ARENABAL546278`, 14-char plan `ARENAPLAN6C18F`, and four own Ledger
accounts `ARENA78AC0001-0004`; the payout's journal touches only those plus the
declared shared nodal pool `ARENAPOOLACC02`. No M1/M2 Ledger/balance/pricing reuse.

## Provisioning defect found and fixed

`provision_funded_merchant` never called `verify_merchant`, so it always returned
`verified=False`, and `provision_campaign(fund_fresh_attacker=True)` silently fell
back to the **M1 fixture attacker**. The first campaign launch exhibited exactly
this (`attacker_is_fresh_funded: False`, `ARENAM00000001`); it was stopped. Fix
(`provisioner.py`): self-verify by creating+fetching a payout through kong and set
`verified` from the real result — a merchant that cannot transact is never
presented as fresh. Proven: fresh merchant creates a real payout (status 200,
`verified=True`). This is a synthetic-provisioning fix; the real repository guard
and `RowsAffected` handling are untouched.

## Fresh-ID tenant-isolation calibration (private; never a finding)

Layout: `RED_LOOP/calibration/tenant-isolation/` — `service.py` (loopback
regression/fixed store, default fixed), `generator.py` (fresh
attacker/victim/resource/canary/namespace per run), `replay.py` (ID-free semantic
bundle + resolver + deterministic requester), `oracle.py` (deterministic judge +
impact certificate), `run_calibration.py`, `cross_provider.py`. It does not touch
the real Kong/Payouts/Ledger/FTS/CFA.

- **Fresh IDs proven:** two independently generated regression fixtures have fully
  disjoint identities; no `pout_1234`, no M1/M2 merchant (`ARENACALATK*` /
  `ARENACALVIC*`). Fixtures (with credentials/canaries) live under the git-ignored
  `RED_LOOP/runs/` and are never committed.
- **Regression:** a fresh attacker reads a fresh victim's resource + hidden canary
  (status 200, canary disclosed) across **two disjoint fixtures** with the same
  ID-free bundle → deterministic replay across different ids.
- **Fixed negative control:** the same semantic request returns 404 with no victim
  field, no canary → causal negative control.
- **Different-provider reproducer:** `gpt-5.5` (OpenAI family, decorrelated from the
  Anthropic primary) received only the minimal bundle + injected attacker
  credential; it obtained the disclosure under regression (200, canary) and was
  denied under fixed (404). Model-call artifacts recorded; the persisted artifact
  is redacted of the canary/credentials. Classification **CALIBRATION_REDISCOVERY**,
  never `ACCEPTED_NEW_FINDING`.
- Evidence: `reports/implementation/m31-calibration-freshid.json`,
  `m31-calibration-crossprovider.json`.

## Ordinary hardened Payouts remains fixed

The calibration runs on loopback ports 19099/191xx, disjoint from the attacker
broker (kong `127.0.0.1:18080`), and its code is outside the primary corpus roots.
Machine proof: `reports/implementation/m31-campaign-isolation.json` (calibration
not under corpus; corpus traversal to it blocked; loopback-only; ports disjoint).
The live edge stayed at `KONG_ENFORCE_ROUTE_POLICY=1` throughout, and the 66-container
arena was untouched.

## Second open campaign

Campaign `camp-20260906T150926Z-d03858`, primary model **`claude-opus-4-8`**,
reproducer **`gpt-5.5`**. The attacker was a genuinely fresh, self-verified
merchant `ARENAM91687917` (`attacker_is_fresh_funded: true`) — the fixture fallback
that the provisioning fix removed. The primary agent received only its attacker
identity and read-only source tools; it chose every target itself.

- **Duration / scale:** 30 productive turns completed (stopped entering turn 31),
  ~353 s, 30 model calls, **451,856 prompt / 7,490 completion tokens**
  (cost UNKNOWN — no gateway price table). 9 merchant requests through hardened
  Kong, **0 boundary violations**.
- **Autonomously chosen hypotheses (no steering):**
  1. create-payout trusts a body-supplied `merchant_id` over the gateway-injected
     identity (cross-tenant payout creation);
  2. `/v1/payouts/translate_account_number_to_balance_id` resolves a body
     `merchant_id` (cross-tenant balance lookup);
  3. `GET /v1/payouts/free_payout/:balance_id` returns attributes for any
     `balance_id` without ownership (IDOR).
  It probed `/v1/balances`, `/v1/fund_accounts`, `/v1/payouts` (GET/POST), specific
  payout fetches, and `translate_account_number_to_balance_id`.
- **Candidates / findings:** 0 candidates claimed, **0 accepted findings**
  (an honest zero-finding partial; the deterministic judge admitted nothing because
  nothing was claimed before the stop). Invalid-claim rate 0/0; duplicate-root-cause
  rate 0/0.
- **Human interventions:** 0 target/experiment interventions. The only operator
  action was none during the run; the run ended on its own via the model-failure
  path.
- **Exact stop reason:** `model_unrecoverable` — the LiteLLM virtual key exceeded
  its budget for `claude-opus-4-8` (HTTP 429 `budget_exceeded`) at turn 31, and the
  recovery path could not obtain a usable completion. This is **not** a
  progress-based exhaustion/stagnation/conclusion and **not** the emergency turn
  ceiling; `normal_completion: false`, `ended_on_emergency_ceiling: false`.

**Honest limitation:** because the stop was an infrastructure budget cap, this
campaign did **not** demonstrate the spec's ideal progress-based termination. The
`second_open_campaign` gate therefore fails closed. A prior launch that fell back
to the M1 fixture attacker was detected and discarded (it drove the provisioning
fix); this run used the fresh attacker but was cut short by budget. To close the
gate, raise the opus-4-8 budget and `python3 RED_LOOP/run.py resume
camp-20260906T150926Z-d03858` so the agent reaches a natural stop.

## Acceptance gates

Generated from named artifacts by `RED_LOOP/surface/m3_1_acceptance.py`
(`reports/implementation/m3-1-acceptance.json`); no pass/fail is hand-set. Gate set
(Section 17): `historical_integrity`, `evidence_archive_manifest`,
`runtime_isolation_no_overlap`, `clean_boot_a_26_0_0`, `clean_boot_b_26_0_0`,
`clean_boot_egress_clean`, `v17_resolved`, `logical_replay_material_match`,
`fresh_merchant_current_head`, `fresh_id_calibration_fixture`,
`fresh_id_deterministic_replay`, `fresh_id_negative_control`,
`fresh_id_cross_provider_replay`, `deterministic_judge`, `candidate_admission`,
`hardened_gateway`, `campaign_isolation`, `safety_egress`, `evidence_integrity`,
`second_open_campaign`, `context_efficiency_recomputed`.

Clean-boot evidence reuse: the two 26/0/0 boots, A~B logical equivalence, v17
healthy-on-clean and zero-egress gates are reused from `milestone-3-1-clean-parity`;
their artifact hashes are re-verified by the producer. The fresh-merchant 8/8 suite
was **rerun** on the current head regardless (Codex did not).

## Remaining fidelity ceilings

`pricing_rule_id` carries a plan id (no separate rule-level id is seeded) — a
declared limitation, not production rule-identity parity. Bank outcomes are
mozart-sim; RBL is the only end-to-end channel; the API monolith is a stub.
Production reachability remains **UNKNOWN** where it was UNKNOWN in M2/M3.

## Next architecture recommendation

**Recommended next slice: the Direct / current-account payout path.**

The campaign's three self-selected hypotheses all probed the *Shared* merchant
trust boundary (body-`merchant_id` trust, cross-tenant balance resolution, free-
payout IDOR) — i.e. the agent independently gravitated to authorization on the
slice we already model. It found no accepted finding there before the budget stop.
The largest un-modelled behaviour that materially changes those same trust and
accounting questions is the Direct/current-account path, which differs from Shared
in source account, balance ownership, reservation behaviour, FTS routing, failure/
reversal semantics (no reversal entity and **no** Ledger journal on a failed Direct
payout, per verifier v08), x-balances refresh (`QueuePushForXBalancesPayoutEvent…`),
and accounting treatment (`direct_account_x` vs `shared_account_x`). It is the one
next expansion; it is **not** implemented in this milestone. (If a resumed campaign
surfaces a stronger blocked hypothesis elsewhere — approval/workflow fidelity or a
service-identity actor — revisit this before building.)

## Safety and cleanup state

Arena left at `KONG_ENFORCE_ROUTE_POLICY=1`, 66 containers healthy. No disposable
calibration resources persist (loopback services are torn down per phase; fixtures
are git-ignored). The gateway credential lives only in the git-ignored
`RED_LOOP/llm.env`. No production/staging/DevStack access; no outside egress.
