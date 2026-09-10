# RED_LOOP

Autonomous code-aware red agent against the frozen Payouts Twin. See `DESIGN.md`
for architecture and `reports/claude-review/RED_LOOP_MILESTONE2_COMPLETION.md`
for the milestone report.

## Prerequisites
- The twin must be running (65 containers, compose project `env2_compose`).
- Access to the Razorpay LiteLLM gateway. Put credentials in an env file that is
  NEVER committed (already git-ignored):

  ```
  # RED_LOOP/llm.env   (or your session scratchpad)
  export LITELLM_BASE_URL=https://llm-gateway.razorpay.com
  export LITELLM_API_KEY=sk-...   # keep secret; never logged or shown to the agent
  ```

## Run
```bash
source RED_LOOP/llm.env
python3 RED_LOOP/run.py preflight     # boundary + evidence + corpus health
python3 RED_LOOP/run.py models        # write registry/model-selection.json
python3 RED_LOOP/run.py campaign      # one sustained campaign (default caps)
python3 RED_LOOP/run.py campaign --turns 120 --wall 6000
python3 RED_LOOP/run.py campaign --dry-run   # allocate + preflight, no model calls
```

Outputs land in `RED_LOOP/runs/<campaign_id>/` (git-ignored; hash-indexed in
`evidence-index.json`, summarized in `final-ledger.json`).

## Kill switch
`touch RED_LOOP/runs/<campaign_id>/STOP` — the loop stops before its next turn.

## Layout
- `red_loop/config.py`   — paths, model routing, the attacker boundary allow/deny lists
- `red_loop/llm.py`      — credential-safe OpenAI-schema gateway client (stdlib)
- `red_loop/broker.py`   — the attacker boundary (kong-lite only, fixed identity, /_arena denied)
- `red_loop/corpus.py`   — read-only source corpus (allow/deny, path-escape guard)
- `red_loop/tools.py`    — agent tool schemas + dispatcher
- `red_loop/state.py`    — durable append-only campaign records + evidence store
- `red_loop/campaign.py` — the loop (checkpoint, stagnation, coarse candidate feedback)
- `red_loop/judge.py`    — deterministic evidence reader + invariant scan + verdicts
- `red_loop/reproducer.py` — independent clean-state reproducer (different model) + calibration
- `red_loop/allocator.py`— per-campaign attacker/victim namespace allocation
- `registry/`            — model-selection, access-policy, known_gaps (judge-only)
- `prompts/primary_mandate.txt` — the exact broad mandate (no hints)
- `surface/`             — proposed compose blocks + `channel_control.py` (control-plane)

## M4 lifecycle, contexts, soak, gate, recovery

Gateway-free utilities (no model calls, read/verify durable records only):

```
# Closing gate over a campaign's hypotheses (exit 1 if it fails closed)
python3 RED_LOOP/run.py lifecycle-gate <campaign_id>

# Export the full lifecycle ledger (writes m4-hypothesis-lifecycle.json)
python3 RED_LOOP/run.py lifecycle-export <campaign_id> [--out PATH]

# Failure-recovery matrix (docker scenarios gated behind M4_DOCKER_RECOVERY=1)
python3 RED_LOOP/run.py recovery-matrix [--out PATH]
```

- **Contexts** (`red_loop/contexts.py`): four exploration policies
  (`broad_coverage`, `deep_direct_accounting`, `identity_tenant_boundary`,
  `concurrency_event_order`). `ContextSupervisor(store, runner=...)` runs them
  sequentially with per-context budgets; the coordinator supplies fresh Direct
  merchants as `MerchantDescriptor`s per policy and injects a gateway-backed
  runner. Only `concurrency_event_order` is granted `merchant_request_concurrent`.
- **Soak** (`red_loop/soak.py`): `Soak(store, cycle_runner).run(wall_seconds=…,
  max_cycles=…)` loops over contexts with checkpoints, scenario rotation, dup
  suppression, stagnation detection and expired-lease reassignment; writes
  `m4-direct-e2e-soak.json`. Stops honestly (`no_productive_work`) rather than
  idle-looping.
- **Lifecycle gate** (`red_loop/lifecycle_gate.py`): importable
  `lifecycle_gate(campaign_id)` / `export_lifecycle(campaign_id)` — the hook the
  M4 acceptance script calls.

Tests: `python3 -m unittest discover RED_LOOP/tests` (host Python 3, stdlib only,
in-process fake gateway, zero network).

## Safety
No production/staging/DevStack, no real data or credentials, no internet egress,
no Daytona. The agent reaches only kong-lite; it cannot touch datastores, Kafka,
Docker, control endpoints, the judge, or another tenant's secrets.
