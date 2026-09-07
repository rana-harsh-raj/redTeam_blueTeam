# Source-to-Pay upstream test gap matrix

Focused upstream tests passed after repository-native mock generation in isolated copies under `.build/test-staging`. These results prove the selected source packages compile and their checked-in unit tests pass; they do not prove the composed runtime journey or production integration behavior.

| Repository | Scope | Result | Evidence | Journey relevance |
|---|---|---:|---|---|
| vendor-payments | `internal/initiatetds` | PASS, 4 top-level tests | `vp-initiatetds.log` | Kafka payload delegation |
| vendor-payments | `internal/tasks -run InitiateTds` | PASS, 3 | `vp-tasks-initiatetds.log` | Worker success/error/requeue behavior |
| vendor-payments | `internal/payout` | PASS, 18 | `vp-payout.log` | API-monolith payout boundary |
| vendor-payments | focused `internal/taxpayments` | PASS, 3 | `vp-taxpayments-focused.log` | TDS creation, optional iKey behavior, payout tag-back |
| accounting-integrations | `internal/service/job` | PASS, 71 | `ai-job-listener.log` | Downstream VP status listener and workflow dispatch |

## Setup failures and classifications

| Repository | Failure | Classification | Blocking? |
|---|---|---|---:|
| vendor-payments | Initial compilation referenced absent generated mocks such as `cache.MockRedisMethods`, `payout.NewMockIPayout`, and `rxclient.NewMockIRxClient`. The pinned repository generator produced the needed mocks. It also reported an undefined `fundAccountVerification` shell function after that target was invoked. | `INTERNAL_BUILD_SYSTEM_DEPENDENCY` | No for selected packages |
| accounting-integrations | Initial compilation referenced absent `metrics.MockIMetrics`, `integration.MockICore`, and `settings.MockICore`. Mockgen package loading defaulted to absent `config/dev.toml`; rerunning with `APP_MODE=test` generated the required mocks. One unrelated generator target still reported `Loading input failed`. | `INTERNAL_BUILD_SYSTEM_DEPENDENCY` | No for `internal/service/job` |

## Remaining gaps

- The broad `make test` suites were not run. The selected packages were prioritized because they cover the mandatory journey and its immediate accounting consumer.
- Passing mocked unit tests does not validate Kafka broker wiring, database migrations, API-monolith replacement fidelity, or cross-process persistence.
- `TestCore_InitiateTdsIKey` covers source behavior with an idempotency key, but does not establish that every real Kafka message supplies a non-empty key.
- The accounting job package covers multiple listener branches; it does not prove a production topic subscription or Cadence execution.
- Mock generator warnings remain build-system gaps even though they did not prevent the focused packages from compiling and passing.

Machine-readable commands, exit codes, classifications, and log paths are in `DOMAIN_REPLICAS/source_to_pay/artifacts/upstream-tests.json`.

## Pre-fix clean-checkout infrastructure failure

The first fresh-checkout attempt at commit `3f8bddc` compiled all three repositories, then failed before runtime boot because Docker Hub timed out resolving the optional `docker/dockerfile:1.7` frontend. Classification: `MISSING_EXTERNAL_DEPENDENCY`. This was a build infrastructure failure, not a passing journey. The unused frontend directive was removed because the runtime image uses only standard Dockerfile instructions. The final three-run proof evaluates that revised implementation. The failed attempt is retained under `artifacts/pre-fix/fresh-checkout-tls-timeout/`; it is not relabeled or overwritten.
