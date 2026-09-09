# controlplane durable schema (m10.1)

Home: `$CAMPAIGN_CONTROL_HOME/campaigns/<campaign_id>/`.

- `manifest.json` — immutable, content-addressed. `campaign_id = "camp-" + content_id[:12]`.
  Pins: mode, mandate, architecture_snapshot_id, twins[], model_pool[], budgets, safety, human.
- `control_state.json` — mutable single doc: phase, planning_cycles, twin_cursor, started_epoch, stop_reason.
- Append-only JSONL ledgers (base + `_update` deltas folded last-writer-wins by id; every
  record carries `ts` and a durable monotonic `seq`):
  - `theses` (thesis_id) — Director's self-generated lines of inquiry; `fingerprint` for dedup.
  - `tasks` (task_id) — bounded work: thesis_id, twin, cell_id, kind (probe|replicate|verify),
    bounded{max_actions,max_duration_s}, replication_group, replica_index, model_role, status, attempts.
  - `cells` (cell_id) — dynamic groupings on a twin; status active|reallocated|retired.
  - `workers` (worker_id) — worker registry/heartbeat.
  - `twins` (instance_id) — assigned twins and their current (possibly reproduced) instance id.
  - `leases` / `handoffs` — RED_LOOP LeaseManager (task leases keyed by task_id).
  - `hypotheses_v2` (hypothesis_id) — RED_LOOP HypothesisManager; campaign-wide semantic dedup.
  - `candidates` (candidate_id) — worker claims; status unverified|verifying|verified|rejected|invalid.
  - `verifications` (verification_id) — decorrelated, non-claimant verdicts.
  - `events` — the durable event log (narrative source).
  - `model_calls` / `tool_calls` — every model and tool action, tagged with task/twin/cell/thesis.
  - `planning` / `budget` / `recoveries` / `narrative`.
- `evidence/` — content-addressed blobs (context packets, etc).
- `checkpoints/` — periodic control-plane snapshots (`state_hash`, counts, control_state).
- `workers/<worker_id>/` — per-worker task.json (+ precomputed packet), result.json, RED_LOOP trail.
- Control files: `STOP` (kill switch), `PAUSE` (graceful pause).
