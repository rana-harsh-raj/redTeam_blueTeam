# Source-to-Pay production unknowns

The replica provides source-backed evidence for a bounded local TDS flow. It does not convert repository source or checked-in configuration into claims about the live deployment. The machine-readable baseline is [unresolved-production-unknowns.yaml](../../DOMAIN_REPLICAS/source_to_pay/spec/unresolved-production-unknowns.yaml); the items below state the operational consequences.

## Deployment and configuration

- The deployed process set, versions, replica counts, regions, routing, autoscaling, and failure domains are unknown.
- Live feature-flag and dynamic-configuration values are unknown. Checked-in production profiles show supported keys and source defaults only.
- Production Kafka partitioning, retention, consumer-group topology, retry/dead-letter behavior, ordering, and the upstream producer of `add-tds-entry` are unknown.
- Production database topology, migrations already applied, isolation levels, retention, backup/restore behavior, and Redis topology are unknown.
- Live secrets, identities, authorization grants, account/contact/fund-account records, and bank destination values are intentionally unavailable.

## Build and distribution

The pinned repositories and source commits are verified, and all declared production command packages build in an isolated generated stage. That evidence depends on operator-held prerequisites: private Razorpay modules in `GOMODCACHE` or authenticated Git access, pinned Buf modules in cache or access to `buf.build`, and downloadable public modules/tools where absent. Therefore the repository is not claimed to be a cache-free or offline-independent source distribution. Broad repository test suites were not run; the focused upstream suite and remaining generated-test gaps are documented in [TEST_GAP_MATRIX.md](TEST_GAP_MATRIX.md).

## Boundary fidelity

The local monolith boundary implements only the API calls and evidence controls needed for the `vendor-payments` ↔ `rzp_tax_pay` journey. It does not run Payouts. In particular, the clean-run result does not prove:

- Payouts' real merchant balance authorization, reservation, queueing, reversal, or terminal-state behavior;
- production workflow/Cadence orchestration or repair behavior;
- Passport or equivalent caller identity and service-to-service authorization;
- the full production internal-contact eligibility and ownership policy;
- all API-monolith validation, payout-ID validation, tax-payment-ID prefix/length validation, error mapping, or transaction behavior;
- live payout persistence, bank/beneficiary validation, webhook delivery, or reconciliation.

The replacement is useful for verifying the source caller's request shape, correlation headers, idempotency-key continuity, payout tag-back, missing-contact negative path, and callback handling within the declared fixture. It must remain classified as a contract replacement, as recorded in [declared-deviations.yaml](../../DOMAIN_REPLICAS/source_to_pay/spec/declared-deviations.yaml) and [payouts-boundary-contract.yaml](../../DOMAIN_REPLICAS/source_to_pay/integration/payouts-boundary-contract.yaml).

## Journey limits

The observed causality is:

1. `add-tds-entry` is consumed by actual vendor-payments source code.
2. The source persists the monthly tax aggregate and calls the boundary to tag the originating payout.
3. The test explicitly invokes the later source `Pay` operation after crossing the source's month boundary.
4. The source calls the internal-contact payout boundary.
5. The test explicitly supplies a processed-payout callback; source state reaches external `processing` and internal `money_loading_success`.

No evidence shows that the Kafka event itself automatically initiates remittance. No accounting-payout status event is emitted by this executed flow. `money_loading_success` is not government remittance, challan generation, tax filing, or paid completion. Vendor-experience, accounting-integrations, and the unselected vendor-payments processes are built or source-mapped but are not running in the composed journey.

The fixture replaces exactly two source constants, `TaxPaymentFundAccountNumber` and `TaxPaymentFundAccountIFSC`, with synthetic values in the isolated runtime stage. It also uses synthetic merchant, contact, beneficiary, account, authentication, clock, OTP, and token values. These establish deterministic local inputs and do not infer production values.

Exact replay produces no additional record in the tested fixture because the source computes a zero delta for the repeated aggregate input. The Kafka runner calls the source without a non-empty application idempotency key, so this result does not prove the separate non-empty-key duplicate lookup for all production messages.

## What would close these unknowns

Promotion beyond the current fidelity requires evidence from the owning production systems: deployed manifests and configuration snapshots, live contract/version ownership, authenticated integration tests against the real API-monolith/Payouts boundary, representative identity and authorization policy tests, broker and datastore operating parameters, and traces through payout accounting, government transfer, filing, and terminal completion. Until then, the precise local acceptance claim is the one in [the replica README](../../DOMAIN_REPLICAS/source_to_pay/README.md), bounded by [fidelity-map.yaml](../../DOMAIN_REPLICAS/source_to_pay/spec/fidelity-map.yaml).
