# Payouts Twin Daytona package

Status: **local dry-run tooling implemented; remote execution unverified and blocked**. No Daytona CLI, Python SDK, or Daytona connector was available on the implementation host. No SDK was installed, private artifact uploaded, snapshot built, or sandbox created. Local acceptance must pass before a remote run.

From the project root, the safe default is:

```bash
bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke
```

This prints a deployment plan, resource-estimate status, and blockers. It imports no SDK and makes no remote API calls. The dry-run reads only the fixed workspace resource-profile file for a measured planning estimate, even when the execution plan or acceptance is blocked. If a phase is missing or unobserved, it reports the reason and an **unmeasured 4 CPU / 16 GiB / 64 GiB placeholder** instead. See [resource measurements and accounting limits](resource-profile.md). A displayed estimate does not authorize allocation; a validated execution plan and all acceptance/authorization gates are still required.

After company permission for the exact transfer, completed local acceptance, offline rehearsal, and provision of an approved empty Docker snapshot, use:

```bash
DAYTONA_APPROVED=1 bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke --plan DAYTONA/execution-plan.json
```

Use `--profile full` to run smoke first and full only on smoke success in the same sandbox. The controller uses a unique run directory, prints no API key, and leaves lifecycle metadata in `DAYTONA/runs/<run>/state.json`. Manual retry after interrupted cleanup:

```bash
DAYTONA_APPROVED=1 bash DAYTONA/scripts/cleanup.sh --state DAYTONA/runs/<run>/state.json
```

Keep `DAYTONA_API_KEY` only in the local controller environment. Set it through the organization's approved credential mechanism; never put it in this file, the execution plan, snapshot, image, or remote environment. Neither a GitHub token nor repository cloning is needed remotely.

## Gates and inputs

Copy `execution-plan.example.json` to `execution-plan.json` only when supplying real approval/evidence. The example intentionally fails. `DAYTONA_APPROVED=1` is necessary and does not override failed local acceptance. An explicit `company_policy_reference` and `transfer_approved: true` describe permission for the exact hash-bound files, including confidential binaries and derived artifacts.

`reports/implementation/local-acceptance.json` is **generated**, never hand-written. Produce it with

```bash
python3 ENV2_COMPOSE/scripts/local-acceptance.py <manifest.json>
```

where the manifest names the artifact roles (`replay_a`, `replay_b`, `final_route`, `final_bank`, `final_golden`, `profile_direct_golden`, `profile_mixed_golden`, `profile_kafka`, `logical_replay`, `audit_dir`, `resource_profile`, `build_evidence`, `explorer_verification`, `declared_deviations`, `expected_failures`, `fixed_twin_defects`). The producer derives every boolean from those files, refuses to write a check it cannot compute, and exits 1 when blocked. The runner requires:

```json
{
  "schema_version": 2,
  "status": "passed",
  "checks": {
    "fresh_build": true,
    "empty_volume_boot": true,
    "runtime_endpoint_audit": true,
    "secret_scan": true,
    "egress_same_window": true,
    "verifier_suite": true,
    "logical_replay": true,
    "route_fidelity": true,
    "resource_profile": true,
    "explorer_saved_and_live": true,
    "staleness": true,
    "declared_deviations_present": true,
    "fixed_twin_defects_recorded": true,
    "no_unexplained_results": true
  },
  "unexplained_results": [],
  "evidence_files": [{"path": "reports/implementation/LOCAL_ACCEPTANCE_GENERATED.md", "sha256": "actual SHA-256"}]
}
```

Schema 1 is **rejected**. It was hand-authored and its single `scenario_assertions` boolean conflated two different questions, so a missing test could not surface as a false boolean. Schema 2 splits them:

- `route_fidelity` (required) — for every in-scope route and profile, the transport was observed AND the ending state equals either the normal expected state or a source-predicted failure declared in `TWIN_SPEC/expected-failures.yaml` with a source citation.
- `product_route_health` (informational, not gated) — did the business route complete? Shared Kafka is `false` with its EF-001 citation; that is a faithfully reproduced production-source defect, not a twin regression.

`staleness` binds each run directory's `arena-fingerprint.json` (written by `ENV2_COMPOSE/scripts/fingerprint.py` at the end of every successful `up.sh`) to the current working tree, so a gate cannot be assembled from runs of different trees or different boots. `no_unexplained_results` is true only when every failed, timed-out or skipped case in the retained run set maps to an expected-failure entry or to a completed entry in `reports/implementation/fixed-twin-defects.json`; entries carrying a `fixed_defect_ref` remain listed for the reader. The report also embeds `declared_deviations` (the fidelity ceiling), `expected_failures` with the run artifact that matched each, and `executed` (which route profiles and scenarios actually ran, with their boot ids).

The runner still checks the `evidence_files` hashes; it cannot determine whether the underlying runs were fabricated, only that the producer read these exact bytes. Bind the measured resource profile in the execution plan's `evidence_files` too; its schema and selection formula are in `resource-profile.md`.

The execution plan `files` array is an explicit allowlist; the runner never recursively uploads a checkout. Each entry has `source` (path relative to this checkout), `destination` (relative path inside `/tmp/payouts-twin`), and `sha256`. Required destinations include `images.tar` (all prebuilt core, substitute, datastore, helper and verifier images), `ENV2_COMPOSE/docker-compose.yml`, `ENV2_COMPOSE/.env.arena`, and `DAYTONA/scripts/remote-run.sh`. Include every necessary script, template, migration, and seed. Update synthetic mount paths for the remote directory and rehearse **this exact bundle** offline locally. Never upload git history, rendered config, secret files, private tokens, or seeded database volumes. Generated synthetic secrets are produced at startup and destroyed at teardown.

The plan also attests `secret_scan_passed` and `offline_compose_rehearsal_passed`; attach their exact reports as hashed evidence. The file hash gate validates bytes, not the semantic absence of confidential credentials inside a Docker archive. Run the approved scanner over image layers, files, logs, and runtime volumes before attesting.

## Runtime and cleanup

`snapshot-definition.json` is a specification, **not a created or validated snapshot**. Select an approved snapshot with exactly the measured CPU/RAM/disk dimensions. This SDK's snapshot create parameters do not take a resource override, so the controller checks the actual allocation immediately after creation and deletes on mismatch before uploading. Verify Docker Compose, nested bridge networking and host packet capture capability in the approved snapshot. Installed SDK model fields and method signatures are inspected before any creation; an old/incompatible SDK fails closed.

The selected package mode is fully offline: setup domains are the empty allowlist. Build dependencies and images locally in an approved build environment; the remote controller only uploads files and loads images. It creates the sandbox with external networking blocked and reconfirms that block before each test. The runtime uses `ARENA_SKIP_BUILD=1`, the existing preflight, DNS check and test-window packet audit. Attempts to fetch a missing image fail; do not open runtime egress to repair a missing bundle. Smoke selects verifier v01 (idempotency) and v06 (processed Ledger accounting). This subset reduces test duration; it still boots the complete core arena.

The managed Python runner uses `finally` to attempt result export followed by sandbox deletion for startup, test, network, timeout and interruption failures. The shell adds an EXIT trap for cleanup retry. Creation's unique sandbox name is saved before the request, covering an uncertain create response. Results are retained locally before deleting the remote sandbox; an export failure is recorded and does not prevent deletion. A failure to export must not be reported as a successful run.

Deletion uses `delete(wait=True)` and then an authenticated API lookup requiring HTTP 404. Authentication errors, connection failures and server errors never count as proof of deletion. Cleanup does not depend on current local acceptance or upload hashes. The hard provider wall-clock TTL is sent **at creation** and confirmed through `auto_destroy_at`; stopped-time auto-delete alone is insufficient. No shared volumes, archive operations, or runtime snapshots are used. A hard kill or host crash can prevent immediate cleanup and result export; the server TTL is the remaining backstop. Cleanup must still be verified afterward.

The named step scripts support dry-run use. Creating or testing outside `run-and-cleanup.sh` is blocked unless an explicit managed controller supplies `DAYTONA_MANAGED_RUN=1`; prefer the managed command so failure cleanup remains guaranteed by the controller.

## Evidence and limits

Official documentation checked on 2026-09-05: [CLI flags](https://www.daytona.io/docs/en/tools/cli/), [Python create/get/delete](https://www.daytona.io/docs/python-sdk/sync/daytona/), [sandbox network settings and TTL](https://www.daytona.io/docs/en/python-sdk/sync/sandbox/), [Docker/Compose snapshots](https://www.daytona.io/docs/snapshots/), [file transfer](https://www.daytona.io/docs/en/python-sdk/sync/file-system/), and [process execution](https://www.daytona.io/docs/en/python-sdk/sync/process/). Documentation pages expose different version labels; installed capability checks take precedence. The controller has not been exercised against a real installed SDK or service. Local tests validate the default gate and failure/cleanup control flow with fakes; they do not prove remote platform behavior.

Run the local checks without credentials:

```bash
python3 -m unittest discover -s DAYTONA/tests -v
```
