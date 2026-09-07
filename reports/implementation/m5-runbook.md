# M5 runbook — autonomous discovery on maker-checker workflows

All commands run from the repo root. The model gateway env must be sourced for
any step that runs the campaign:

```bash
source RED_LOOP/llm.env      # sets LITELLM_BASE_URL + LITELLM_API_KEY (never committed)
```

## One-command acceptance (from a clean checkout, no campaign rerun)

Verifies the committed canonical evidence against every M5 gate:

```bash
make m5-acceptance           # -> reports/implementation/m5-acceptance.json, accepted=true
```

## Reproduce each layer from scratch

```bash
# 1. workflow business journey — 12 scenarios end-to-end over HTTP
make m5-scenarios            # boots engine + payout-sink, incl. real restart recovery
                            # -> reports/implementation/m5-workflow-scenarios.json (12/12)

# 2. blind benchmark integrity — fixed reference + 4 mutants, mutant isolation
make m5-benchmark            # -> reports/implementation/m5-benchmark-integrity.json

# 3. safety + egress attestation
make m5-safety              # -> reports/implementation/m5-safety-egress.json

# 4. a fresh autonomous campaign (bounded; writes to RED_LOOP/m5/campaign/.run/)
source RED_LOOP/llm.env
make m5-campaign ARGS="--rounds 8 --models kimi-k3,gpt-5.5,glm-5p2 --seed 7 --base-port 8500 --sink-port 8599"

# 5. independent clean-state replay of every accepted finding
make m5-verify              # -> reports/implementation/m5-finding-replays.json
```

## What each layer proves

| Layer | Evidence | Passing bar |
|---|---|---|
| Business journey | `m5-workflow-scenarios.json` | 12/12 scenarios, none skipped |
| Blind benchmark | `m5-benchmark-integrity.json` | fixed clean, each mutant isolated to its family |
| Campaign quality | `m5-campaign-summary.json` | ≥15 hyp, ≥30 exp, ≥10 baseline, ≥5 change, ≥3 realloc, ≥1 recovery |
| Discovery result | `m5-campaign-summary.json` | ≥3/4 defects, ≥2 families, 0 false on fixed |
| Verification | finding `verification` blocks + `m5-finding-replays.json` | model-proposed, reproduced twice, negative control, fresh-ID replay |
| Safety | `m5-safety-egress.json` | loopback-only, single external host = gateway |

## Engine (standalone)

```bash
cd ENV2_COMPOSE/substitutes/workflow-engine
WFE_ADMIN_TOKEN=admin WFE_PORT=8093 python3 run_engine.py     # durable, restart-recoverable
```

## Fidelity

`ENV2_COMPOSE/substitutes/workflow-engine/FIDELITY.md` and
`reports/implementation/m5-source-map.md` label what is real (create + callback
contract, config, state machine) vs reconstructed (the decision engine
internals). Findings state which they concern.
