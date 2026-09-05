# Synthetic data implementation

Implemented `ENV2_COMPOSE/seeds/generator/generate.py`, version 1.0.0, with no third-party Python dependencies. It compiles the versioned, synthetic fixture templates into a complete isolated dataset consumed through `seeds/generated/`. It does not read production/staging/DevStack databases, real credentials, bank payloads or repository entity records.

## Generated coverage

| Dataset | Starting state | Boundary |
|---|---|---|
| Baseline Shared M1 | Existing IDs, Ledger balance10,000,000 paise | SQL opening state is representative |
| Baseline Direct/RBL M2 | Current account, x-balances10,000,000; DCS and API DB reservation feature | Real reservation behavior requires runtime verification |
| Baseline workflow M3 | Shared balance and workflow configuration | Approval execution is not established by fixtures |
| Per-verifier trios |26 functions each receive3 distinct merchants, balances, beneficiaries and source mappings | Parameterized invocations of the same function share that namespace |
| Low balance | Separate trio at10,000 paise; matching Ledger journal/account seed amounts | Dequeue still needs real balance synchronization and trigger |
| Direct status / monolith relay / Kafka | Separate trio for each route family | Data labels do not activate delivery routes |
| Inactive / invalid beneficiary | Inactive persisted FA per namespace; invalid IFSC and missing FA request variants | Malformed persisted banking rows are not inserted |

Current total:32 namespaces,96 merchants,128 CFA fund accounts. Standard fees200 paise and tax36 paise are representative fixture pricing, not a production tariff.

Each verifier's namespace is addressed by its function name and relative pytest node ID in `scenario-index.json`. Its fixture dictionaries expose merchant ID, banking-account ID, balance ID, source account number, public FA ID, FTS numeric source/fund IDs and Ledger balance-account ID. Ordinary merchant headroom cannot be exhausted by a different function's payout. Core system parent accounts remain shared because source Ledger account-discovery semantics require them.

## Evidence and generation rules

`provenance.json` contains source repository, file, commit and SHA-256 for each explicitly inspected evidence path; hash-addressed input templates; hashes of generated output;202 SQL field declarations/constraints across20 seeded tables; generation rules and confidence classifications; and every cross-service mapping. The static migration inventory covers119 local migration files. No private source-code body is copied into the manifest.

Source constraints include fourteen-character service identity columns, FTS integer identities, the channel case difference (`rbl` in Payouts and `RBL` in FTS), pool/shared/direct account-type translation, the legacy API DB `features(entity_id, entity_type, name)` representation and the DCS Direct Configs protobuf flag. Generated IDs use an ARENA prefix and deterministic SHA-256 namespace derivation. API-only `fa_`/`cont_` prefixes are separated from persisted IDs.

CFA contact hashes follow Go's sorted compact JSON and SHA3-256 algorithm; FA hashes follow the inspected merchant/contact/account-type/bank-details concatenation. CFA and monolith now derive their beneficiary details from the same generated record, and every inactive account retains the simulator's blocked suffix9999. This fixes conflicting legacy CFA versus monolith beneficiary fixtures without importing an external record.

The eight Ledger owner identifiers for merchant balance, vendor payable, commission, GST, nodal receivable/payable and current receivable/payable are hardcoded `ledger/internal/common/constant.go` values. They are explicitly listed as code-constant exceptions in the manifest; they are neither customer fixture IDs nor secrets. PAN is omitted, optional phone numbers are blank, UTR is not preseeded and key material is generated separately. Bank-routing metadata is distinguished from customer identity.

## Reproducibility and verification

```bash
cd /Users/rana.singh/rzp-payouts-architecture/ENV2_COMPOSE
python3 seeds/generator/generate.py --epoch 1735689600 --verify-evidence
python3 -m unittest discover -s seeds/generator -p 'test_*.py' -v
python3 seeds/generator/schema_compare.py --write-evidence-lock
```

**18 generator tests pass**, within the **136 passing offline checks** in [the final offline summary](offline-final/summary.json) (zero failures, errors or skips). The [generator log](offline-final/ENV2_COMPOSE-seeds-generator.log) covers: byte-identical repeated generation; per-verifier unique identities; cross-service SQL/HTTP references; CFA identity/hash consistency; synthetic/secret constraints; output hash integrity; FTS routing primary-key uniqueness; duplicate Ledger account rejection; inactive-bank suffix preservation; API feature IDs fitting CHAR(14); the 19-table API subset and foreign-key order; five Ledger worker queue selections; XBalances lowercase versus FTS uppercase channel values; and nullable Shared beneficiary account type preserving the source default on direct creation. A live startup caught an overlong baseline feature ID; the template identifier was corrected and a rejecting constraint check added. A discovered Ledger parent-ID collision was corrected in the source fixture by the contracts workstream and is now rejected by the compiler if reintroduced.

Live startup supplies the current epoch so `last_fetched_at` and balance timestamps satisfy freshness-sensitive application behavior. Files are identical for equal explicit epoch and input/template/verifier versions; application-generated timestamps and auto-increment IDs are compared logically. This report does not claim that offline generation establishes live scenario acceptance.

## Remaining limits and precise evidence requests

- Static source declaration inventory does not prove final effective DDL; see `SCHEMA_COMPARISON.md` and its JSON companion.
- `beneficiary_bank_code` is a Payouts model string absent from its migration set. `api/database/migrations/2022_06_29_143810_create_ps_payout_details.php` declares the sibling column as CHAR(4). Request schema-only `SHOW CREATE TABLE payout_details` and schema revision to establish exact service nullability/default/type; no rows or production database access are required.
- x-balances sub-balance models and `queries.sql` have no corresponding wired migrations in the inspected directory. Request approved migration registration/configuration before enabling that scope.
- API DB DDL now implements the source-aligned 19-table subset with per-object confidence labels. Remaining schema evidence concerns the assumed `features.name` width and explicitly representative subsets; see [schema comparison](SCHEMA_COMPARISON.md) and [P0.5 reconciliation](IMPLEMENTATION_SPEC_RECONCILIATION.md). Successful local loading does not establish complete production DDL parity.
- Direct HTTP and mixed creation/status profiles have separate passing runtime evidence. Kafka Direct terminal delivery passes, while Kafka Shared accounting is blocked by missing process-local job registration; its DTO mismatch remains a secondary source risk. See [route coverage](SCENARIO_COVERAGE.md) and [Kafka evidence](KAFKA_ROUTE_EVIDENCE.md). Production bank-catalog completeness, recurring cron cadence and real workflow approvals remain outside these results. Seed labels alone are still not route evidence.

## Live-discovered identifier corrections

The first live run exposed two additional fixture defects that offline structural checks had not covered: the baseline API feature ID exceeded CHAR(14), and Ledger account discovery received `bacc_`-signed banking-account identifiers while the generated Ledger entities held bare IDs. The generator now enforces feature-ID width, derives `ledger_banking_account_id` separately from the persisted key, and verifies every Shared Ledger entity matches Payouts `pkg/ledger/ledger_journal_create.go` GetSignedID semantics. A read-only query and actual Journal request confirmed the mismatch before correction. Subsequent clean replay 3 and replay 6 each passed all 26 live assertions, including Shared initiated/processed/reversal journals and synchronous merchant debit; see [replay 6 JUnit](runs/replay6/full/junit.xml). These completed results are separate from the final repeatability gate described below.

The generator also supports `--route-profile` (or `ARENA_ROUTE_PROFILE`) and merges `config/routes.py` experiments before output hashing. All existing Splitz variants are expanded to every isolated merchant; the selected global profile is recorded in the provenance and scenario index. This selects configuration; a dataset label alone still does not prove route execution.

## Current root acceptance evidence

The offline result is **18 generator tests / 136 total checks**, distinct from the **26 live verifier assertions**. Clean replay 3 and replay 6 each passed all 26 with no skips; replay 6's [empty-volume startup log](runs/replay6/up.log) records schema/seed application and healthy core processes, and its [audited full run](runs/replay6/full/egress.json) exited 0 with zero outside packets.

The [final supplemental route result](runs/final-supplemental/route/route-results.json) passes all six cases: Direct success, scheduled, queued, explicit returned reconciliation, Direct failure and Shared failure. Its [egress audit](runs/final-supplemental/route/egress.json) also passed with command exit 0. The [bank result](runs/final-supplemental/bank/bank-effects.json) passes hold, delayed success, both ambiguous outcomes, duplicate and timeout. These are real scenario results with snapshots, not fixture-label claims. The [Explorer verification](EXPLORER_VERIFICATION.md) covers all 128 illustrative steps plus its authenticated live action and saved portable trace.

Final acceptance is still in progress. [Replay 7](runs/replay7/full/junit.xml) recorded 25 passes and a processed-journal wait timeout. The root investigation observed real mutex contention followed by the configured queue retry creating the journal about 31 seconds later; the verifier's wait is being aligned to that source timing without patching core behavior. The next clean repeatability pair and final runtime secret/endpoint refresh remain pending. Earlier successful captures are retained as successful evidence, and the replay 7 timeout is retained as a diagnostic result.
