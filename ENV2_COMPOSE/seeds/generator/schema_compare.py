#!/usr/bin/env python3
"""Inventory every seeded SQL column against repository migration text.

This is a source comparison, not a substitute for executing migrations. It does
not infer deployed schemas or collect schema/data from a remote service.
"""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import subprocess
import generate

MIGRATIONS = {'payouts': 'internal/database/migrations', 'fts': 'internal/migrations',
              'ledger': 'internal/database/rx_migrations', 'x-balances': 'internal/database/migrations'}
MODEL_CHECKS = [
    ('payouts', 'payout_details', 'beneficiary_bank_code', 'internal/app/payoutDetails/model.go',
     'String field in service model; absent from service migrations. Monolith ps_payout_details sibling migration explicitly declares CHAR(4); that does not establish the service physical schema.'),
    ('x-balances', 'sub_balance', None, 'internal/database/model/sub_balance.go',
     'Model and standalone queries.sql exist; no wired migration creates this table. Not seeded or declared supported.'),
    ('x-balances', 'sub_balance_limits', None, 'internal/database/model/sub_balance_limit.go',
     'Model and standalone queries.sql exist; no wired migration creates this table. Not seeded or declared supported.'),
]

def catalog(repos_root):
    tables, evidence = defaultdict(lambda: defaultdict(list)), []
    for repo, directory in MIGRATIONS.items():
        root = Path(repos_root) / repo
        commit = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
        for file in sorted((root / directory).glob('*.go')):
            text = file.read_text()
            rel = str(file.relative_to(root))
            evidence.append({'repository': repo, 'file': rel, 'commit': commit, 'sha256': generate.digest(text)})
            # x-balances interpolates its EntityBalance constant into raw SQL.
            text = text.replace('`+"`"+model.EntityBalance+"`"+`', 'balance')
            text = re.sub(r'//[^\n]*', '', text)
            for match in re.finditer(r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?["`]?([\w]+)["`]?\s*\((.*?)\)\s*(?:;|ENGINE|`)', text, re.I | re.S):
                table, body = match.groups()
                for line in body.splitlines():
                    field = re.match(r'\s*["`]?([\w]+)["`]?\s+((?:VAR)?CHAR\s*\([^)]*\)|(?:BIG|TINY|SMALL)?INT(?:EGER)?(?:\s*\([^)]*\))?|NUMERIC\s*\([^)]*\)|DECIMAL\s*\([^)]*\)|JSONB?|TEXT|BOOLEAN|TIMESTAMP[^,]*|ENUM\s*\([^)]*\))([^\n]*)', line, re.I)
                    if field:
                        name, kind, rest = field.groups()
                        tables[(repo, table)][name].append({'file': rel, 'commit': commit, 'type': kind.strip(),
                                                          'constraints': rest.rstrip(' ,')})
            for match in re.finditer(r'ALTER\s+TABLE\s+["`]?([\w]+)["`]?\s+ADD\s+(?:COLUMN\s+)?["`]?([\w]+)["`]?\s+([^;`\n]+)', text, re.I):
                table, field, kind = match.groups()
                tables[(repo, table)][field].append({'file': rel, 'commit': commit, 'type': kind.strip(),
                                                    'constraints': 'ADD declaration; later alterations may exist'})
            for match in re.finditer(r'ALTER\s+TABLE\s+([\w]+)\s+RENAME\s+COLUMN\s+([\w]+)\s+TO\s+([\w]+)', text, re.I):
                table, original, renamed = match.groups()
                if tables[(repo, table)].get(original):
                    declaration = tables[(repo, table)][original][0]
                    tables[(repo, table)][renamed].append({'file': rel, 'commit': commit, 'type': declaration['type'], 'constraints': 'renamed from ' + original + '; ' + declaration['constraints']})
    return tables, evidence

def compare(repos_root, generated):
    tables, evidence = catalog(repos_root)
    fields = []
    for name, repo in [('payouts', 'payouts'), ('fts', 'fts'), ('ledger', 'ledger'), ('xbalances', 'x-balances')]:
        columns = defaultdict(set)
        for match in re.finditer(r'INSERT\s+(?:IGNORE\s+)?INTO\s+["`]?([\w]+)["`]?\s*\(([^)]+)\)', generated['s4/' + name + '.sql'], re.I):
            table, names = match.groups()
            columns[table].update(x.strip(' `"\n') for x in names.split(','))
        for table, names in sorted(columns.items()):
            for name in sorted(names):
                declarations = tables[(repo, table)].get(name, [])
                fields.append({'repository': repo, 'table': table, 'column': name,
                               'migration_declarations': declarations,
                               'status': 'migration declaration located' if declarations else 'not resolved by source scanner',
                               'generation_rule': 'namespace-expanded synthetic schema template',
                               'classification': 'exact declaration reference; representative generated value' if declarations else 'assumed until reviewed'})
    models = [{'repository': repo, 'table': table, 'field': field, 'model_file': file,
               'model_present': (Path(repos_root)/repo/file).exists(),
               'migration_table_found': bool(tables.get((repo, table))),
               'migration_column_found': bool(tables.get((repo, table), {}).get(field)) if field else None,
               'finding': finding} for repo, table, field, file, finding in MODEL_CHECKS]
    return {'method': 'Static CREATE/ALTER declaration inventory for every generated INSERT column. Dynamic SQL, down migrations and later ALTER semantics require effective-schema validation; not a proof of final physical DDL.',
            'fields': fields, 'migration_sources': evidence, 'model_discrepancies': models}

def markdown(report):
    lines = ['# Schema comparison', '', report['method'], '',
             f"Compared {len(report['fields'])} seeded columns across {len({(x['repository'], x['table']) for x in report['fields']})} tables, using {len(report['migration_sources'])} migration files.", '',
             '| Service | Table | Located columns | Unresolved columns |', '|---|---|---:|---|']
    groups = defaultdict(list)
    for field in report['fields']:
        groups[(field['repository'], field['table'])].append(field)
    for (repo, table), fields in sorted(groups.items()):
        missing = [x['column'] for x in fields if not x['migration_declarations']]
        lines.append(f"| {repo} | {table} | {len(fields)-len(missing)}/{len(fields)} | {', '.join(missing) or 'none'} |")
    lines.extend(['', '## Model and migration discrepancies', ''])
    for item in report['model_discrepancies']:
        lines.append(f"- `{item['repository']}/{item['model_file']}`: {item['finding']}")
    lines += ['', '## Information required', '',
              '- Schema-only `SHOW CREATE TABLE payout_details` from the approved service schema revision, including column nullability/default/index metadata and migration revision. No rows or credentials are needed. The monolith sibling CHAR(4) declaration narrows the question, but does not prove the service schema.',
              '- Approved migration registration/configuration for x-balances sub-balance tables if those paths enter scope; the unwired queries.sql file alone cannot establish deployment behavior.',
              '- API table identities are already source-confirmed and aligned in the 19-table local subset: `fund_transfer_attempts` (plural), `payouts_details`, `payouts_status_details`, `balance`, `features`, `reversals` and `workflow_entity_map`. API `payouts_details` is distinct from the service `payout_details` above. See [the P0.5 reconciliation](IMPLEMENTATION_SPEC_RECONCILIATION.md) and `ENV2_COMPOSE/seeds/mysql/apidb-ddl/00_init.sql` for per-table source and fidelity. This API subset is separate from the service INSERT-column inventory in this report.',
              '- Remaining API schema evidence: schema-only `SHOW CREATE TABLE features` from the approved effective revision to resolve its name width (the source migration declares 25, while the arena uses an explicitly assumed 255 to accommodate the current 29-character reservation flag). Effective DDL for the other explicitly representative API subsets is needed only if broader physical-schema parity enters scope. No rows, credentials or production database access are requested.',
              '', 'The JSON companion contains every compared column, located type/constraints, repository commit and source hash. Unresolved scanner entries are visible limitations, not silently inferred columns.', '']
    return '\n'.join(lines)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repos-root', default=generate.discover_repos())
    parser.add_argument('--output', type=Path, default=generate.ROOT.parent / 'reports/implementation')
    parser.add_argument('--write-evidence-lock', action='store_true')
    args = parser.parse_args()
    generated, _ = generate.compile_dataset(repos_root=args.repos_root)
    report = compare(args.repos_root, generated)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output/'SCHEMA_COMPARISON.json').write_text(generate.dump(report))
    (args.output/'SCHEMA_COMPARISON.md').write_text(markdown(report))
    if args.write_evidence_lock:
        Path(__file__).with_name('schema-evidence.json').write_text(generate.dump(report))
    print(f"Compared {len(report['fields'])} seeded fields")

if __name__ == '__main__':
    main()
