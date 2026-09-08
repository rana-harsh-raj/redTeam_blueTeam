# Source-to-Pay architecture replica

Branch: `architecture-replica-s2p-v1`. Base: `d54161c2e29873eb5f53804db58af45d48a7b4f6`. Evaluated final commit: `b6e4976f9baebfe770dfa30794f6f61ea08ccb1b`. Worktree: `/Users/rana.singh/rzp-source-to-pay-replica`. The tree-clean gate is computed from Git in [acceptance.json](../../DOMAIN_REPLICAS/source_to_pay/artifacts/acceptance.json).

The selected source journey is executable. Overall hard acceptance remains **false** when any gate fails; current substantive failed gates: 02_protected_worktrees_unchanged, 03_preexisting_containers_unchanged. The final machine result, including artifact integrity, is authoritative. In particular, the active Payouts checkout changed and pre-existing arena container start timestamps changed during the task. Snapshot comparison cannot attribute the changes; the strict unchanged-container gate is not waived. No command in this implementation targets the arena.

## Source and builds

| Repository | Pinned SHA |
|---|---|
| vendor-payments | `20c4f4d59970471067388afea8b1d65ac39ee126` |
| vendor-experience | `df90df21bee54ce0148df43d3fc65e7ba7b60160` |
| accounting-integrations | `fc13a0b2a38214374e80a16af3ed4465b2d84ef1` |
| proto | `52682577d79d7237944e9fbb10477a1721623065` |
| api | `2d665f918b60e917ec92be648fb5816d247f72b1` |
| payouts | `4bf3dbf9239feadea6d65ca90c893a988e116173` |
| workflows | `080d71a51b777c89af586c92ac4b5f683a473a7c` |
| rpc | `27388ee6335145064774ef31440cc91dbccd8bb0` |
| config-proto | `a6b201039f22cc543efb740eba9dd72fd29474b0` |

Sources were recovered into clean, independent durable repositories from intact local packs or snapshots, then checked by HEAD, tree, tracked-file count, clean status and `git fsck`. All three selected repositories build from fresh archive staging after locked offline source code generation. The proof covers 9 vendor-payments command packages, 3 vendor-experience packages plus 2 migration packages using the `boot` tag, and 6 accounting-integrations packages; 10 named executable outputs have binary hashes. Code generation requires the documented private Go modules and locked Buf module inputs. It is not a self-contained distribution of those private dependencies.

## Runtime and corrected journey

One added Go entrypoint runs unchanged vendor-payments task, TDS/core/repository, payment/payout client, and payout callback functions. It applies all 88 source migrations to local MySQL and uses Redis and Kafka-compatible Redpanda. The original worker and server command binaries are built but not launched. Vendor-experience and accounting-integrations are built and mapped; their wider workflows and external services are outside the executed slice.

1. `add-tds-entry` is consumed by an adapter which invokes actual `InitiateTdsTask.ProcessMessage`. Actual source creates the monthly tax aggregate/record and PATCHes the **originating** payout's tax-payment ID.
2. Exact Kafka replay adds no material record because actual source computes a zero delta. This is not evidence of key-based or concurrent exactly-once processing. Invalid input is also checked against persistent state because the upstream task swallows some errors.
3. An explicit Pay request first proves missing-contact rejection and the current-month guard. The existing source clock injection then advances to the next month.
4. Actual source resolves the internal tax contact, verifies OTP/banking account through the local boundary, writes the payout association, and creates the internal-contact remittance. The source and boundary retain the same payout idempotency key.
5. A synthetic callback invokes the actual VP handler/core/state transition. SQL, source API, and pubsub trace agree on **`money_loading_success` / public `processing`**. Repeated Pay is rejected without creating another payout. No bank settlement, challan, tax filing, `paid` status, or accounting-payout event is fabricated.

## Replacements and limits

The source startup/Kafka/HTTP adapter and a stateful Python/SQLite API-monolith boundary are declared replacements. The boundary implements only the selected vendor-payments-to-internal-tax-contact seam: synthetic application/merchant identity, contact/fund account, OTP, banking account, internal-contact payout, merchant-scoped idempotency and tax-payment tag-back. Contract controls cover denial, missing resources, conflicting idempotency requests, tenant separation and malformed tax IDs. It does not implement the real Payouts balance, workflow or Passport stack. The callback adapter calls the handler without the production Twirp authentication and source-updater transport.

Exactly two bank destination constants are replaced in isolated build staging by synthetic values. No durable source file is patched. Initial IDs and configuration are synthetic. No behavioral stub replaces selected business logic. See [declared deviations](../../DOMAIN_REPLICAS/source_to_pay/spec/declared-deviations.yaml), [runtime fidelity](../../DOMAIN_REPLICAS/source_to_pay/spec/runtime-fidelity.json), [assumptions](ASSUMPTIONS_AND_DECISIONS.md), and [production unknowns](PRODUCTION_UNKNOWNS.md).

## Architecture representation

Measured repository-observable architecture representation: **100.00%**. The denominator is 22,116 mechanically detected observations, including 348 generated RPC artifacts. Every counted representation must resolve to graph and source evidence. The raw denominator is preserved separately. An additional exact runtime overlay records 13 actual-source functions and 5 adapter/boundary functions (18 mandatory nodes), excluded from that denominator. Function participation is verified; this is not line coverage.

This percentage measures representation of detected observations, **not detector recall, semantic discovery completeness or production parity**. Regex detection misses some dynamically assembled relationships; 2,226 eligible files have no detector match. Tests/mocks/fixtures remain corroborating evidence rather than executable denominator items. The mechanically emitted exclusion and file audits expose this limitation.

| Category | Detected items |
|---|---:|
| build_or_deployment_input | 108 |
| config_definition | 5457 |
| config_member_access | 1172 |
| config_or_flag | 4 |
| database_schema | 62 |
| executable_entrypoint | 83 |
| generated_rpc_artifact | 348 |
| health_or_startup | 1127 |
| http_route | 480 |
| http_route_definition | 97 |
| kafka_or_queue | 813 |
| message_contract | 155 |
| migration_artifact | 139 |
| migration_index | 213 |
| migration_sql | 250 |
| observability | 3094 |
| orm_model | 134 |
| retry_or_idempotency | 1908 |
| rpc_interface | 88 |
| service_client | 881 |
| sql_target | 565 |
| state_transition | 1594 |
| storage_cache_notification | 3203 |
| worker_or_job | 26 |
| worker_registration | 115 |

## Clean runs and controls

A prior revision compiled the repositories but failed before boot when Docker Hub timed out resolving an optional Dockerfile frontend. A later acceptance wiring error referenced an obsolete verifier check name; the mapping was corrected and a regression test added. That dependency was removed. The failed attempt is retained under `artifacts/pre-fix/fresh-checkout-tls-timeout/` and classified in the test gap matrix. The three runs below evaluate the revised implementation.

| Run | Reset (s) | Clean build (s) | Boot (s) | Journey (s) | Independent result | Fresh final checkout |
|---|---:|---:|---:|---:|---|---|
| final-1 | 10.927 | 95.622 | 14.837 | 3.905 | PASS | False |
| final-2 | 10.573 | 96.769 | 12.071 | 3.999 | PASS | False |
| final-4 | 0.597 | 192.107 | 15.481 | 4.013 | PASS | True |

The independent verifier ignores the harness `accepted` flag and recomputes consumed records, persistence, duplicate/negative controls, tag-back ordering, callback status, correlation and idempotency from raw observations. [CLEAN_RUN_RESULTS.md](CLEAN_RUN_RESULTS.md) explains reset and normalized replay semantics. [TEST_GAP_MATRIX.md](TEST_GAP_MATRIX.md) records focused upstream passes and classified generation/setup failures. Broad unrelated suites were not run.

## Reproduction and integration

Follow the exact [runbook](../../DOMAIN_REPLICAS/source_to_pay/README.md). After three `clean-run.py` runs (one in a fresh final-commit worktree), execute `./DOMAIN_REPLICAS/source_to_pay/scripts/acceptance.sh`. Reports are deliberately generated after commit from tracked generator code, so their commit field and measured results do not create self-referential Git hashes. Runtime observations and generated reports are ignored by Git and hash-bound together in [artifact-hashes.json](../../DOMAIN_REPLICAS/source_to_pay/artifacts/artifact-hashes.json); [acceptance.json](../../DOMAIN_REPLICAS/source_to_pay/artifacts/acceptance.json) binds that manifest.

The shared graph and active Payouts tree are untouched. A deterministic namespaced [graph patch](../../DOMAIN_REPLICAS/source_to_pay/integration/unified-graph-patch.json), [boundary contract](../../DOMAIN_REPLICAS/source_to_pay/integration/payouts-boundary-contract.yaml), and [future integration plan](../../DOMAIN_REPLICAS/source_to_pay/integration/future-integration-plan.md) identify the later connections and replacement removal sequence. No integration was executed.
