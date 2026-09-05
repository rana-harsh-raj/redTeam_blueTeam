# Synthetic data compiler v1

This offline compiler expands versioned synthetic schema templates into a complete local fixture set. It never connects to a database, fetches external data, or reads a secret. Approved repository copies supply source evidence; generated datasets contain references/hashes, not private source bodies.

From `ENV2_COMPOSE`:

```bash
python3 seeds/generator/generate.py --epoch "$(date +%s)"
python3 -m unittest discover -s seeds/generator -p 'test_*.py' -v
python3 seeds/generator/schema_compare.py --write-evidence-lock
```

The default output is `seeds/generated/`. Without `--epoch`, the compiler uses `1735689600`, suitable for file reproducibility checks. Live boot must supply the current epoch because balance freshness is application behavior. Repeating an identical epoch, source/template set and verifier set yields byte-identical files. Runtime IDs/timestamps created by applications are compared logically.

`--repos-root /approved/local/copies` selects source evidence. Otherwise the compiler uses `REPOS_ROOT` or `.env.arena`. `--verify-evidence` rejects missing source files/commits; ordinary generation falls back to the checked-in `evidence-lock.json` when source copies are unavailable, explicitly marking that limitation. `--write-evidence-lock` records current local source paths, HEADs and file hashes. It does not copy source contents. Refresh schema evidence with `schema_compare.py --write-evidence-lock` when source versions change.

## Output and integration

- `s4/{payouts,fts,xbalances,ledger}.sql`: generated database rows; apply after real service migrations.
- `s4/apidb.sql`: API DB balance mirror and Direct feature rows; apply after API DB DDL/schema patches.
- `s4/cfa.js`, `cfa-entities.json`: CFA records and SHA3-256 hashes, aligned with monolith beneficiary records.
- `merchants.json`, `monolith/`, `dcs/`, `pricing.json`, `stork/`, `splitz/`, `shield/`: substitute inputs at the same relative mount paths as the original seed tree.
- `scenario-index.json`: `.verifiers[test_function_name]` and `.verifiers[relative_node_id]` return M1/M2/M3 fixture dictionaries. `fund_account_id` includes `fa_`. `.baseline` retains existing IDs. Each dictionary includes the source account, balance, Ledger balance-account ID, and archetype.
- `provenance.json`: source repositories/files/commits/hashes, input template hashes, output hashes, per-field migration declarations and constraints, generation rules, confidence and cross-service mapping.

Generation creates no key material. The secret generator must mint the `secret_file` named in every `merchants.json` entry and the gateway should mount only those key files. Baseline key filenames stay compatible; other merchants have separate key files.

For the current verifier set, generation produces 32 namespaces and 96 merchants: baseline trio, 26 test trios, and five scenario trios. Adding a top-level `test_*` function under `verifier/verifiers/test_*.py` adds another trio automatically. Parameterized cases currently share their function namespace; do not run parameterized cases concurrently without extending the namespace contract.

SQL targets empty-volume startup. Some routing tables use auto-increment IDs, so re-applying the seed package to populated stores is not a reset. Use the approved down/empty-volume/up sequence.

## Fidelity boundaries

The existing versioned `seeds/s4` and JSON fixtures are schema templates. The compiler expands them, rather than copying repository or DevStack entity records. Template changes are reflected in output and recorded by hash. It detects duplicate Ledger IDs instead of accepting `ON CONFLICT` silently.

Names and identifiers are generated locally; tax IDs are omitted; phone numbers are blank. Account numbers are synthetic values within this isolated fixture namespace, not a claim that any real bank reserves those numbers. Bank routing metadata such as an IFSC identifies a bank, not a customer. The eight Ledger system-owner IDs come from code constants and are separately listed as exceptions; all merchant fixtures are ARENA-prefixed.

The low-balance scenario has 10,000 paise; ordinary merchants have 10,000,000 paise each. Shared merchants retain Ledger as balance truth. API DB/x-balances begin with agreeing values. Ledger opening state is an arena SQL fixture, not evidence for production funding/onboarding semantics.

Direct status, monolith relay and Kafka namespace labels reserve isolated data. They do not activate those routes or prove status delivery. Workflow configuration is present but cannot establish real approval behavior. Invalid IFSC/missing-fund-account cases are request-only variants; inactive beneficiaries are valid persisted records with `active=false`.

The schema scanner locates static migration declarations for all currently generated SQL columns. It is a comparison report, not a migration executor: dynamic SQL, down paths, constraints introduced later and effective schema revisions need runtime verification. See `reports/implementation/SCHEMA_COMPARISON.md`.

## Route profiles and signed Ledger identifiers

`--route-profile monolith|direct|direct-create-monolith-status|kafka` selects Splitz route fixtures using `config/routes.py`; the default is `ARENA_ROUTE_PROFILE` or `monolith`. The choice is recorded before output hashes are computed. Base experiment variants are also expanded to every generated merchant. Effective service configuration must use the same profile.

`banking_account_id` is the bare Payouts DB key. `ledger_banking_account_id` is `bacc_` plus that key, matching Payouts Journal DTO GetSignedID. The Ledger JSON entity identifier is not a CHAR(14) database primary key. The compiler rejects mismatched signed entities and missing Shared balance accounts.
