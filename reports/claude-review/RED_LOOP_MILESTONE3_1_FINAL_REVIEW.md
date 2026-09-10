# RED_LOOP Milestone 3.1 — Final Review

**Verdict: READY FOR NEXT ARCHITECTURE EXPANSION** — all **21 of 21** required
acceptance gates pass (`reports/implementation/m3-1-acceptance.json`, `accepted:
true`). The one previously-pending gate, `second_open_campaign`, now passes on a
new context-clean campaign that reached a genuine progress-based stop.

READY means only: the current Shared-payout slice is reproducible; fresh campaign
merchants are fully operational; the one-agent loop runs honestly; false claims are
rejected; a fresh-ID calibration proves clean independent reproduction; the second
campaign completed without target steering; all current M3.1 gates pass. It does
**not** mean the Payouts slice is free of vulnerabilities, that production
reachability is proven, or that broader scale/Direct/Daytona are ready. Zero
accepted open-campaign findings is compatible with READY.

- **Branch:** `milestone-3-1-claude-final`
- **Final commit SHA:** recorded at tag time (this report + acceptance are committed
  together; the annotated tag `red-loop-m3.1` points at that commit).
- **Base:** `milestone-3-1-calibration` head `a63185f` (two Codex commits above
  `milestone-3-1-clean-parity` head `0dd9b32`).
- **Tag `red-loop-m3.1`:** **created** (annotated) — every required gate passes.

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

### Model selection (Claude Code 2.1.263)

Per the ordered process, the Opus 4.8 subagent path was tested **first**. A fresh
non-fork Opus 4.8 subagent smoke test **passed** — model + quota available (through
Claude Code credentials, a different pool than the exhausted LiteLLM opus-4-8
budget), tool invocation and structured-response handling worked
(`{"tool_invocation":"success","parsed_n":42,"model_self_report":"claude-opus-4-8"}`).
**It was not selected as primary.** Claude Code 2.1.263 exposes no mechanism to
(a) restrict an Agent-tool subagent to only the RED_LOOP typed broker + corpus
tools, (b) deny it shell/filesystem access to answer-key material (reports, RED_LOOP
internals, calibration, known_gaps, judge), or (c) route its turns through the
RED_LOOP deterministic judge, context compiler, packet-metrics and stagnation
logic — all of which live only in the Python `run_campaign` loop that drives the
model via an HTTP LiteLLM call. Using it would have required an ad-hoc unlogged
campaign path that bypasses the RED_LOOP evidence format and the isolation
requirements, which is explicitly forbidden. **Fallback (per spec): Kimi K3
(`kimi-k3`)** through the existing LiteLLM adapter; its campaign-agent smoke passed
(structured tool calling, source retrieval, broker, context-compiler compatibility,
serialization — no adapter changes). Reproducer stays `gpt-5.5` (a third provider).
Record: `reports/implementation/m31-model-selection-campaign2.json`.

**I must not claim** a Claude Code subagent was context-clean without documenting
its loaded context, nor that Opus subagents create a separate quota pool as fact —
the smoke merely demonstrated the subagent ran. This is why the auditable,
structurally-isolated LiteLLM primary was used for the accepted campaign.

### Accepted campaign — `camp-20260906T154844Z-23100f`

Primary model **`kimi-k3`** (Moonshot, via LiteLLM), reproducer **`gpt-5.5`**.
Context isolation is structural: the LiteLLM primary receives only the system
mandate, the compiled state from a **blank** hypothesis ledger, tool schemas and a
bounded raw window — no parent conversation, no prior campaign, no calibration, no
reports/judge/known-gaps, no filesystem. It can act only through the typed broker
(kong `127.0.0.1:18080` only) and the corpus retrieval restricted to the five
accepted source roots. Provenance digests (mandate/tool-schema/corpus) recorded in
the model-selection artifact.

- **Fresh, fully operational attacker:** `ARENAM92110277`
  (`attacker_is_fresh_funded: true`), self-verified through a real payout at
  provisioning; startup would have **aborted** (not fallen back to M1) had it failed
  (the new fail-closed guard). Victim/control merchants carry fresh per-campaign
  canaries the primary never sees.
- **Scale:** 61 turns, ~530 s, 61 model calls, **869,434 prompt / 11,350 completion
  tokens** (cost UNKNOWN — no gateway price table). 15 merchant requests admitted
  through hardened Kong.
- **Autonomous target selection (no steering):** 12 distinct probe routes
  (`/v1/account`, `/v1/balance`, `/v1/fund_accounts`, `/v1/inflight_reservations`,
  `/v1/payouts` GET/POST, `/v1/payouts/fetch_multiple`, `/v1/transactions`,
  `/v1/virtual_accounts`, `/v1/payouts/translate_account_number_to_balance_id`,
  specific payout fetches). Self-formed hypothesis: the create-payout DTO exposes a
  `SkipWorkflow` field (a workflow-bypass idea). 0 parent/human interventions.
- **Boundary held:** 5 attempts on non-allow-listed surfaces
  (`/v1/inflight_reservations` ×2, `/v1/balance`, `/v1/virtual_accounts`,
  `/v1/account`) were **denied** by the broker (recorded boundary violations). The
  hardened gateway returned `404 no_route` for `fetch_multiple` and `translate_*`
  (internal routes unreachable) — **zero canned-mock markers** appeared in any
  response.
- **Candidates / findings:** **0 candidates, 0 accepted findings** — an honest
  zero-finding campaign. Invalid-claim rate 0/0; duplicate-root-cause rate 0/0.
  Deterministic judge active (nothing claimed to adjudicate); the post-campaign
  step re-confirmed the reproduce-and-judge machinery (`calibration_machinery_ok:
  true`).
- **Exact normal stop reason:** `stagnation_pause` — the harness detected a
  recon-only loop, issued **one final strategic replan**, the replan produced no new
  runtime experiment, and it stopped (`reason: recon_only_loop`). This is a genuine
  progress-based termination: `normal_completion: true`,
  `ended_on_emergency_ceiling: false`, not a fixed-turn ceiling and not a
  provider-budget error.
- **Same-window egress:** audited under live merchant payout traffic (34,729
  internal packets captured), **0 outside packets**, `status: passed`
  (`RED_LOOP/runs/camp-20260906T154844Z-23100f/egress/egress.json`).
- **Provider errors:** none in the accepted run.

### Preserved interrupted campaign — `camp-20260906T150926Z-d03858`

Retained unchanged as `interrupted_provider_budget`: primary `claude-opus-4-8`,
fresh attacker `ARENAM91687917`, 30 productive turns, **0 accepted findings**, then
`stop_reason: model_unrecoverable` (LiteLLM opus-4-8 budget `budget_exceeded`) at
turn 31 — `normal_completion: false`. It is **not** relabelled as the accepted
campaign and remains in reliability reporting as an honest provider-budget
interruption. (An even earlier launch that fell back to the M1 fixture attacker was
detected and discarded; it drove the self-verify provisioning fix.)

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

Arena left at `KONG_ENFORCE_ROUTE_POLICY=1`, **66/66 containers healthy**. During
the egress setup one arena stub (`stork-capture`) was briefly removed by an
over-broad cleanup match and immediately restored via compose (verified healthy,
count back to 66) — noted here for full transparency. No disposable calibration
resources persist (loopback services torn down per phase; fixtures git-ignored).
The egress capture container and its analyzers were removed after the audit. The
gateway credential lives only in the git-ignored `RED_LOOP/llm.env`. No
production/staging/DevStack access; same-window egress audit shows 0 outside
packets.

## Reliability summary (both campaigns)

- Accepted: `camp-20260906T154844Z-23100f` (Kimi K3) — normal `stagnation_pause`,
  0 findings, 0/0 invalid-claim, 0/0 duplicate-root-cause.
- Interrupted: `camp-20260906T150926Z-d03858` (Opus 4.8) — `model_unrecoverable`
  (provider budget), 0 findings, preserved unchanged.
- Discarded (pre-fix): one launch fell back to the M1 fixture attacker; detected
  and abandoned, driving the self-verify provisioning fix + fail-closed guard.
