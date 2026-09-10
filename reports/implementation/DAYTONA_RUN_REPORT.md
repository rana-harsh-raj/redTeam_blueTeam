# Daytona packaging and run report

Status: **not run; local acceptance and company authorization gates remain closed**.

No private source, binaries or derived artifacts were uploaded. No snapshot, sandbox or shared volume was created. No Daytona resources were consumed by this workstream. Sandbox deletion is **not applicable**, not a verified deletion claim.

| Item | Result |
|---|---|
| Installed CLI | `command -v daytona`: not found |
| Installed Python SDK | `python3 -m pip show daytona daytona-sdk`: neither package installed |
| Callable Daytona connector | None exposed in this session |
| SDK/platform execution | Not attempted; current documentation inspected |
| Local acceptance | Blocked by Shared Kafka accounting dispatch and the historically inconclusive Direct-after-Shared timeout; [machine-readable gate](local-acceptance.json) remains closed. |
| Local resource profile | All three phases observed; existing formula gives planning floor 2 CPU / 8 GiB RAM / 16 GiB disk; accounting gaps remain |
| Company transfer authorization | Not supplied; `DAYTONA_APPROVED=1` not used for a real run |
| Sandbox ID | None |
| Creation/deletion time | Not applicable |
| Allocated CPU/RAM/disk | None |
| Approximate resource-minutes | 0 from this workstream |
| Remote smoke/full tests | Not run |
| Cleanup verification | Not applicable; no resource created |

`DAYTONA/` contains all requested named scripts, an empty-snapshot specification, explicit execution-plan example, resource-profile contract, README and local failure-path tests. Unapproved actions print a dry-run plan. The fixed workspace profile supplies the measured planning floor of 2 CPU / 8 GiB RAM / 16 GiB disk while preserving execution blockers; the clearly labelled unmeasured placeholder appears only when valid measurements are unavailable. Approval alone still fails on missing local acceptance; it cannot create or upload.

The supported packaging mode builds images locally and uploads an explicit hash-bound manifest into an approved empty Docker-in-Docker snapshot. Its remote setup domain allowlist is empty. Outbound networking is blocked at sandbox creation and checked again before runtime. The runner calls the same preflight, DNS and packet audit, skips runtime builds, runs smoke before full, exports designated results, deletes in a Python `finally`, adds shell cleanup retry, and requires authenticated API HTTP 404 for deletion verification. A true wall-clock TTL is passed at creation and checked through the returned deadline. No stopped-only auto-delete is presented as a hard TTL. An uncertain creation response with no ID is left unverified rather than treating one missing-name response as proof that a late creation cannot occur.

Local validation repeated on 2026-09-05: **12 tests passed, 0 failed, 0 skipped** for the Daytona package (not the Payouts scenario suite). They exercise absent approval, approval without evidence, create/upload/test/export failures, interrupt cleanup, smoke-before-full ordering, cleanup failure visibility, evidence tampering, unmeasured-resource refusal, and HTTP 404-only verification. Approval cases use in-process fakes in the unit tests; no approved operational invocation was made in this review. All 8 shell scripts passed `bash -n`. The separate package-wide [offline aggregate](offline-final/README.md) records 143 passed, 0 failures, 0 errors and 0 skipped.

The [recorded final default command](offline-final/daytona-dry-run.final.metadata.json), `env -u DAYTONA_APPROVED bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke`, returned exit 0 with `mode: dry-run`, `remote_api_calls: 0`, `sandbox_created: false` and `resource_minutes_consumed: 0`. Its [JSON output](offline-final/daytona-dry-run.final.json) displays 2 CPU / 8 GiB RAM / 16 GiB disk, binds the estimate to profile SHA-256 `d8ffc2909cbfb9f97ba1a61ba2c2b4f9af33ca0cf3043c74804d16b9f92175ac`, and explicitly records `execution_authorized: false`. It reported two blockers: absent `DAYTONA/execution-plan.json` and no installed Daytona Python SDK. The CLI is also absent. No remote capability is claimed from these checks.

The measured [resource profile](resource-profile.json), updated 2026-09-05 14:06:06 UTC, contains 6 idle, 2 successful-payout and 15 full-verifier samples from the final replay20 boot. Their maximum sampled use is **1.4038 CPU cores / 4.1791 GiB RAM / 4.3710 GiB disk**. The audited [golden result](runs/final-acceptance/golden/live-result.json) passed; replay19 and replay20 each record **26 passed, 0 failed, 0 skipped**, and the [final strict comparison](logical-replay-final.json) reports all 26 logically equivalent. Schema version, phase sample counts, common persistent container identities, per-container sums, phase maxima and disk component sums were independently checked; the unchanged runner function calculates **2 CPU / 8 GiB RAM / 16 GiB disk** after its fixed allowances. See [resource selection and accounting limits](../../DAYTONA/resource-profile.md).

CPU/RAM cover Compose-labelled arena containers (including the verifier) and exclude Docker daemon/VM overhead and standalone packet-capture/analyzer containers without that label. Disk omits inaccessible logs and unlisted archive/staging paths. All three phase windows share the same 65 persistent container IDs; full-suite samples additionally capture the Compose verifier. The calculated floor therefore remains planning evidence; it is not approved remote sizing or a capacity guarantee. The display-only runner change exposes this estimate from the fixed workspace profile without requiring execution approval or altering execution gates, the example plan, fallback placeholder or snapshot specification.

Remaining prerequisites: resolve full local acceptance, including the Shared Kafka gap and the historically inconclusive Direct-after-Shared timeout, despite the passing replay pair and current supplemental checks; resolve the measured profile’s daemon/VM, standalone capture/analyzer, log and archive/staging accounting gaps for allocation; approved exact transfer manifest and scan reports; an approved snapshot with matching allocation and working nested Docker/Compose/packet capture; an installed SDK supporting the inspected TTL/network/delete parameters; the exact bundle's local offline rehearsal. The SDK adapter is documentation-derived and remains unverified against an installed SDK. Build/snapshot creation is intentionally not performed by this package.

Commands from the project root:

```bash
bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke
# Only after the prerequisites and company approval are complete:
DAYTONA_APPROVED=1 bash DAYTONA/scripts/run-and-cleanup.sh --profile smoke --plan DAYTONA/execution-plan.json
# Cleanup retry of the particular owned run:
DAYTONA_APPROVED=1 bash DAYTONA/scripts/cleanup.sh --state DAYTONA/runs/<run>/state.json
```

Documentation checked on 2026-09-05: [current CLI](https://www.daytona.io/docs/en/tools/cli/), [Python SDK lifecycle](https://www.daytona.io/docs/python-sdk/sync/daytona/), [network controls and TTL](https://www.daytona.io/docs/en/python-sdk/sync/sandbox/), [Docker/Compose support](https://www.daytona.io/docs/snapshots/). Locally installed model fields and signatures are checked before execution because current documentation has differing SDK version labels.
