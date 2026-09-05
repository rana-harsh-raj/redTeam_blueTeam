# Safety lifecycle review and corrections

Updated 2026-09-05. Reviewed the egress audit, golden-run wrapper, loopback ingress, generated credential lifecycle, and Compose mounts. All initial findings have implementations addressing them; full arena startup and full-window runtime acceptance are owned by the main workstream. This follow-up did not start or restart arena services or run a live verifier suite. No host private keys were read.

## Finding status

| Initial finding | Current status and evidence |
|---|---|
| Verifier removal failure could return success | Corrected. Cleanup now confirms exact container absence using a successful Docker listing. A missing `--rm` container is accepted only after that check; daemon/listing failures fail closed. Unit tests cover both cases. |
| Untracked analysis containers | Corrected. Every analyzer name is registered before launch and removed during finalization; failed analyzer cleanup marks the audit failed. Unit tests cover names and cleanup failure. |
| Signal interrupted finalization | Corrected. Cleanup installs a handler that records interruption without raising, completes bounded cleanup, writes evidence, and returns nonzero. A signal-in-cleanup regression passes. |
| Unbounded Docker lifecycle calls | Corrected for inspected operations. Audit setup/analysis calls have 60-second limits, readiness calls 10 seconds, cleanup removal 20 seconds and absence checks 10 seconds. Post-kill process wait is bounded. `--duration` remains the verifier-command deadline; bounded setup/analysis/cleanup add overhead and are not included in that command timeout. |
| Host-owned 0600 inputs unreadable to UID 10001 | Implemented and verified using isolated fake inputs. Eight narrow generated volumes contain UID/GID 10001 files at 0400 and directories at 0500. Host files remain unchanged. Compose maps 51 read-only consumer mounts; Kong alone gets the signer/merchant bundle, and Monolith alone gets its five service credentials. Full startup integration remains to be exercised by the main workstream. |
| Credential files created before restrictive chmod | Corrected. `gen-secrets.sh` and the config generator set umask 077 before writing; generated per-test merchant secrets rotate on generation. Source-scope tests confirm materialization does not broaden modes or select unrelated credentials. |
| Teardown stopped before destruction / failed readiness leaked child | Corrected. `down.sh` attempts ingress, Compose, and credential cleanup independently, accumulating failure status. Destruction removes generated runtime volumes even with `--keep-volumes` for datastores. Ingress readiness failure terminates and waits for its own child, escalating with a bounded wait if needed. Unit tests cover readiness failure, escalation, and malformed PID files. |
| 96-byte capture described as headers-only | Corrected. Documentation states captures may contain short payload prefixes; PCAPs stay local in a temporary volume with verified cleanup. |

An additional audit gap found during follow-up is corrected: the capture name is registered before `docker run`, so an uncertain launch timeout still triggers its cleanup. The corresponding regression passes.

## Loopback ingress protections

The bridge binds only 127.0.0.1. It requires one exact loopback Host header and, when supplied, one Origin matching that Host and port. Cross-site or same-site fetch metadata is rejected; CLI requests without browser metadata remain supported. Public payout paths use strict segment boundaries, and arena health/mint paths match exactly. Encoded path separators, dot segments, alternate origins, duplicate framing headers, unsupported transfer encoding, and incomplete request bodies are rejected.

The socket has a 10-second IO timeout, the fixed Kong subprocess has a 30-second deadline, and the in-container request has a 25-second timeout and a 4 MiB response limit. Readiness probes and in-container HTTP requests disable proxies and redirects. Child startup and shutdown are bounded. Readiness confirms the spawned process PID, and stale PID files cannot direct arbitrary process termination.

## Synthetic volume boundary

`secrets/materialize.py` admits TOML files from six explicit generated config families, a Kong bundle of merchant keys/synthetic signer/API credential, and a Monolith bundle of five named service credentials. It refuses symlink inputs and running volume consumers. Initialization uses a locally installed image with `--pull never`, `--network none`, a read-only container filesystem, narrowly scoped capabilities, and only the output volume. File contents cross stdin and are never logged or included in image layers. A second container, UID 10001 with all capabilities dropped and a read-only volume, checks file inventory, ownership, modes, traversal, and SHA-256 equality.

Runtime volumes have explicit generated ownership and Compose labels. They are declared nonexternal in Compose, and both ordinary `down -v` and explicit generated-secret destruction remove them. Official datastore bootstrap, root cron, and root verifier still use their existing individually declared Compose file-based secrets; the new volume mechanism addresses UID 10001 consumers. The new Compose services.volumes entries contain no raw secret bind mounts.

## Verification

```text
python3 -m unittest discover -s ENV2_COMPOSE/preflight/tests -p 'test_*.py'
Ran 27 tests: all passed (13 audit, 12 ingress, 2 materialization scope).
```

These unit tests replace subprocesses, signals, and request IO with fakes and use temporary generated-input directories. They do not contact Docker or Kong.

The separate real Docker test used one uniquely named throwaway volume containing only explicit fake test strings. Two initialization/verification passes succeeded, including a nested configuration path, changed contents, deletion of an obsolete file, mode 0400, UID/GID 10001, and mode 0500 directory traversal. All four temporary containers were removed, and the volume was removed with absence confirmed. Evidence: `reports/implementation/materialization-isolated-verification.json`.

All-profile Compose validation succeeded without resolving host env files: eight named generated volumes, 51 read-only consumer mounts, and exact Kong/Monolith scoping. Sanitized mount evidence: `reports/implementation/compose-materialization-validation.json`. Shell syntax checks, Python compilation, and focused diff whitespace checks passed. A workspace-wide whitespace check found pre-existing whitespace in the other workstream's API seed DDL; those files were not modified by this review.

Full runtime packet coverage, real startup under the new generated mounts, and no leftover resources following a live interrupted verifier run still require the main acceptance run. The isolated evidence does not claim those results.

## Final real-runtime follow-up

The main implementation subsequently completed repeated empty-volume boots with these narrow generated mounts and passed full-window packet audits during the original26 and supplemental route/bank workloads. Current acceptance evidence is linked from LOCAL_ACCEPTANCE.md; the earlier section above records the scope of the original isolated review.

A real Docker negative test deliberately exceeded a two-second command deadline. The audit returned nonzero, retained `status: failed`, removed its actual command/capture containers and temporary PCAP volume, and an independent Docker listing confirmed absence. Evidence: [timeout cleanup](runs/audit-timeout-negative/cleanup-verification.json) and its intentionally failed egress.json. This validates cleanup, not a successful network-invariant run.

A second real Docker negative test sent SIGTERM only after its command container was running. The audit returned130, retained failed/interrupted status, and removed the command, capture and PCAP resources with absence independently confirmed. Evidence: [interrupt cleanup](runs/audit-interrupt-negative/cleanup-verification.json). Neither negative test is counted as a passing egress workload.
