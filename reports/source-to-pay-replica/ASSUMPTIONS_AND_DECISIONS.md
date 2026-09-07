# Assumptions and decisions

This document records implementation decisions; it does not establish passing acceptance gates.

- Work begins at verified commit `d54161c2e29873eb5f53804db58af45d48a7b4f6`, on branch `architecture-replica-s2p-v1`, in `/Users/rana.singh/rzp-source-to-pay-replica`.
- The existing Payouts checkout and containers are protected. Baseline captures store file hashes and selected non-secret container identity fields, not source file contents or Docker environment variables.
- The Payouts process is active. Container start times changed during our initial read-only checks. Strict equality to the initial snapshot cannot be claimed, and snapshots alone cannot attribute concurrent changes. No baseline is reset to make this gate pass.
- The investigation's proposed journey is a hypothesis until checked against pinned source and execution. In particular, Kafka topic purpose, monthly payout initiation, tag-back and accounting propagation must be verified separately.
- Private repositories are local build inputs, not committed clones. Durable pinned snapshots and explicit build prerequisites are required; temporary investigation directories are recovery inputs only.
- Configuration uses synthetic local values. A checked-in production profile is evidence of available keys and code defaults, not evidence of actual deployment or feature-flag state.
- Architecture representation coverage is measured over mechanically extracted material items. This metric is distinct from runtime coverage and from the unmeasured completeness of any static detector. Limitations must remain visible.
- Final acceptance is evaluated against a committed implementation. A commit cannot contain its own hash, nor can an artifact hash itself. Post-commit acceptance records the evaluated commit, and an explicitly scoped integrity manifest binds the evidence it consumes.
- No integration into the shared Payouts graph or runtime is performed. Integration outputs are a separate patch and boundary contract.

- Source-lock `generated_at` is the manually authored manifest time, not a measured recovery duration. Exact recovery completion time is unavailable; each source-verification artifact records its actual UTC check time.
- Callback transport is an explicit test adapter into the actual VP handler. Production Twirp authentication and SourceUpdater transport are outside this run, as are the real Payouts balance/workflow/Passport gates.
- Sequential duplicate Kafka messages test the source's zero-delta behavior; they do not establish concurrency or exactly-once delivery guarantees.
- Final and clean-run reports are generated after commit by tracked code. Their exact commit IDs, observations and hashes live with the ignored acceptance artifacts; no fabricated self-referential commit hash is inserted into tracked files.
- The latest protected-worktree comparison also detected ongoing changes in the active Payouts checkout. Snapshot equality gates for that checkout and pre-existing restarted containers remain failed. No baseline is replaced and no repair of someone else's active work is attempted.
