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

## Safety
No production/staging/DevStack, no real data or credentials, no internet egress,
no Daytona. The agent reaches only kong-lite; it cannot touch datastores, Kafka,
Docker, control endpoints, the judge, or another tenant's secrets.
