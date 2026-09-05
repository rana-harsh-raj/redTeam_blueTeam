# Current-boot audit

These readers refresh the secret/endpoint evidence for an already running local twin. They do not start payout scenarios, rebuild images, regenerate active fixtures/configuration, restart application services, join Kafka consumer groups, receive SQS messages or write database records. Run them after startup and functional verification have finished, while the boot remains stable.

From the workspace root:

```bash
python3 ENV2_COMPOSE/preflight/audits/audit.py \
  --build-evidence .local/twin-repos/build-evidence-YYYYMMDDTHHMMSSZ \
  --output reports/implementation/audits/current-boot-YYYYMMDDTHHMMSSZ \
  --read-unused-mozart
```

Replace the two timestamped directory names with the existing build-evidence directory and a new output directory. `--output` must not already exist; this preserves older captures. `--project` defaults to `env2_compose`. The readers check that the project belongs to this checkout. Paths are derived from the package location, not a developer's home directory. Use the same configuration-selection environment as startup, including `ARENA_ROUTE_PROFILE` if it was explicitly set; a rerender difference blocks acceptance.

Prerequisites:

- Python 3.11 or later; the audit code uses the standard library.
- Local Docker/Compose access and an already prepared/running twin. No network or remote access is needed by the audit.
- Gitleaks with the default rule set (`8.30.1` was used for the original audit). The required `dir`/`stdin`, JSON-report and redaction flags must be supported. A missing scanner report fails closed.
- Existing source/build metadata: `provenance.json` and `build-results.json` from the admitted local build. Optional `image-assets.json`, post-build verification and Payouts/CFA readability rebuild metadata are included when present. This rechecks identities/hashes against existing admission evidence; it does not rerun the original source or immutable-binary secret scans.
- Existing generated secrets and configurations in the ordinary workspace locations, and the configured mounted secret files in each datastore. Datastore CLI authentication reads passwords inside the existing containers; they are not placed in Docker command arguments or reports.
- Datastore-image tools: `mysqldump`, `pg_dumpall`, `mongosh`, `redis-cli`, LocalStack's `openssl`, and Kafka's offline `kafka-dump-log.sh`; host `strings` is used for binary volume-file coverage.
- For `--read-unused-mozart`, the already installed `python:3.12-alpine` helper image and the existing generated volume. Pulls are prohibited. Its only explicit mount is that volume, read-only; the helper uses no network, a read-only root filesystem, dropped capabilities, no new privileges, and CPU/memory/PID limits. The helper runs as the materialized owner UID/GID 10001; enumeration errors fail. It is removed and absence checked. If unavailable or mismatched, the requested check stays blocked. Without the flag, the unused-volume caveat remains explicit.

The runner executes three readers sequentially:

| Reader | Coverage |
|---|---|
| `current_boot.py` | Existing admitted-source/build hashes, five core image identities, all current service image identities (including substitutes), independently rerendered configuration, explicit runtime environment, generated-volume hashes and all available logs since each current container start. |
| `logical_stores.py` | Four MySQL logical database exports including authentication tables, PostgreSQL databases/roles, all accessible MongoDB collections and configured Redis DB0 keys. MongoDB SCRAM matches are classified only after both stored keys are derived from the generated local password and stored salt. Redis task IDs are classified only when the key equals a valid Machinery TaskState.TaskUUID record; the raw scan remains recorded. |
| `remaining_volumes.py` | LocalStack named-volume files and its matching self-signed localhost test key/certificate, decoded current Kafka `.log` segments, auxiliary volume inventories, and application file-log inventories under `/tmp` and `/var/log`. Kafka's decoder has a 64 MiB heap and never commits offsets. |

Each reader can also be invoked directly with `--output` and `--project`; `current_boot.py` additionally requires `--build-evidence` and accepts `--read-unused-mozart`. Direct readers can replace their own report in the supplied directory, so use a fresh directory when preserving evidence matters. Prefer the runner: it checks running service/container identities before and after the capture and returns nonzero if the boot changed or any reader is blocked. Timeouts and interrupts are failures, not successful partial audits.

Outputs contain timestamps, counts, hashes, IDs, sanitized classifications and limits. Raw logs, database dumps, keys and scanner matches are held in memory/private temporary directories and removed. The capture is not a distributed atomic snapshot; ordinary application workers can continue processing during it. The console and summary never include raw reader errors. `audit-summary.json` records report hashes, reader exits and the boot identity check. A `passed_with_caveats` result means only that the scoped checks passed.

Public diagnostic URLs in Redis overcommit (including its jemalloc issue #1328 help link) and MongoDB startup warnings are retained as raw pattern matches and separately classified with narrow service/context/host rules. This does not relax the effective-configuration endpoint gate or prove that no network request occurred; simultaneous DNS/packet verification is separate evidence. Any other URL/secret finding remains blocked for review. Known generated secrets in runtime logs are an exposure finding even though their origin is synthetic.

Limits: rotated-away/future logs, unreadable MongoDB protected session metadata, Redis databases outside configured DB0, in-memory SQS messages, physical/deleted/compressed/encrypted database pages, nonprintable secret encodings, and datastore/substitute image root filesystems remain outside these readers. Nonempty application file-log inventories block the aggregate rather than being silently treated as scanned. A self-signed localhost test certificate identifies a local test purpose; it is not independent proof of random key generation. Immutable core binary scans and source admission remain separate, reusable evidence only while their identities/hashes match. No absolute “no real secrets anywhere” claim is made.

Offline checks (no Docker or application I/O):

```bash
python3 -m unittest discover -s ENV2_COMPOSE/preflight/audits -p 'test_*.py' -v
```
