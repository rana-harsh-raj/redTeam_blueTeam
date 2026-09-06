# Milestone 2 fidelity statement and declared additions

Layered on top of the frozen Twin v1.0 (`twin-v1.0`, runtime freeze
`219ca48bbb5e51db9347b335ac0a50241656045b`). The 76 base deviations in
`ENV2_COMPOSE/config/declared-deviations.yaml` and the Kafka expected failures in
`TWIN_SPEC/expected-failures.yaml` still hold. The additions below are new and
declared; each is additive and default-off so the frozen declared scope is
behaviour-neutral unless explicitly activated.

## M2 additions and their fidelity

- **M2-DEV-001 workflow-sim** (`ENV2_COMPOSE/substitutes/workflow-sim/`). A thin
  stand-in for the Cadence/Workflow approval engine. It faithfully uses the REAL
  Payouts contract: it serves `WorkflowAPI/Create` (a non-empty id flips the
  payout to `pending`) and drives the REAL
  `POST /v1/payouts/payouts_internal/{id}/approve|reject` callbacks with
  cred.Workflow. Fidelity limits: approvals are control-plane-triggered (never
  automatic, no rule/level/timeout evaluation); it does not implement the
  `/v1/workflow/state` map-writes; single callback attempt (no retry);
  in-memory. Activation: `[workflow] host` = `{{WORKFLOW_HOST}}`, default
  `http://127.0.0.1:1` (frozen dead address); set
  `ARENA_WORKFLOW_HOST=http://workflow-sim:8092`. A bug reproduced only because
  of these limits is a TWIN_SPECIFIC_LEAD, not a production finding.

- **M2-DEV-002 Stork duplicate control** (`stork-capture` env
  `STORK_DUPLICATE_TERMINAL`, default `"0"`). When set, a terminal payout webhook
  is delivered twice with identical event_id and byte-identical body, to exercise
  merchant-side dedupe. Default off is byte-identical to frozen behaviour. A
  merchant cannot set this; it is control-plane only.

- **M2-DEV-003 Channel health control** (`RED_LOOP/surface/channel_control.py`).
  Drives the REAL `POST /v1/notify/health/update` internal route (cred.FTS) to
  set a channel `downtime`/`uptime` in the `partner_bank_health` Redis hash;
  direct payouts for a down channel enter `on_hold` via the real `IsChannelDown`
  path. Control-plane only (docker exec + host secret), never attacker-reachable.
  No application code changes.

- **M2-DEV-004 Second channel**. The frozen seeds already model shared (M1/M3)
  vs direct/rbl (M2) as two routing configurations; `channel_control.py
  provision-icici` can add an icici direct account at RUNTIME (not a seed-file
  edit, so the frozen fingerprint is preserved). The icici provisioning is
  best-effort: `fts_seed.sql` is itself a self-declared unverified skeleton, so
  the script warns rather than silently guessing the FTS schema.

- **M2-DEV-005 Model routing**. Primary red agent is `claude-opus-4-8` because
  `claude-opus-5` is budget-capped on this key and content-filtered on the
  adversarial mandate; reproducer is `gpt-5.5` (different provider). See
  `registry/model-selection.json`.

## Correlated-model / campaign-scope limitations
- Only the three named fixtures (M1/M2/M3) are fully funded, so campaigns share
  those actors; isolation is by time-window + opening-balance snapshot rather
  than fresh funded identities. Fresh generated merchants are identity-only.
- The judge reads state via `docker exec` (DBs are docker-internal); it is a
  control-plane reader, distinct from the frozen verifier container path, and
  reuses the same tables the verifier helpers document.
- Bank outcomes are mozart-sim (amount-keyed / forced), RBL is the only
  end-to-end channel, and the API monolith is a stub — findings that hinge on
  real bank or monolith internals hit a fidelity ceiling (see known_gaps.yaml).
