#!/usr/bin/env python3
"""Versioned, offline synthetic fixture compiler. Never reads a database or secrets.

Checked-in s4 fixtures are schema templates, not external records. This compiler
expands a merchant trio into isolated namespaces and recomputes derived data.
"""
import argparse
import ast
import copy
import hashlib
import json
import importlib.util
import os
from pathlib import Path
import re
import subprocess

VERSION = '1.0.0'
ROOT = Path(__file__).resolve().parents[2]
SEEDS = ROOT / 'seeds'
DEFAULT_EPOCH = 1735689600
_route_spec = importlib.util.spec_from_file_location('arena_route_profiles', ROOT / 'config/routes.py')
_route_module = importlib.util.module_from_spec(_route_spec)
_route_spec.loader.exec_module(_route_module)
ROUTE_PROFILES = _route_module.PROFILES
JSON_INPUTS = ['merchants.json', 'monolith/merchants.json', 'monolith/fund_accounts.json',
               'monolith/misc.json', 'dcs/merchants.json', 'pricing.json',
               'stork/subscriptions.json', 'splitz/experiments.json', 'shield/rules.json']
SQL_INPUTS = ['payouts.sql', 'fts.sql', 'xbalances.sql', 'ledger.sql']
SYSTEM_IDS = {
    'Gg614JldVI2nJi': 'Merchant Balance parent', 'Gg6I8KieFph8kj': 'Vendor Payable parent',
    'Gg6I8IF5vrmp6k': 'Commission Income parent', 'Gg6I8GsKzz6CBl': 'Output GST parent',
    'Gh0YfwUewxUqdm': 'FTS Nodal Receivable owner', 'Gh0YfqKiXRdkCo': 'FTS Nodal Payable owner',
    'Gh0YfsxpRlykwn': 'FTS Current Receivable owner', 'Gh0Yfp7SMMBIRp': 'FTS Current Payable owner',
}
SOURCE_FILES = {
    'payouts': ['internal/database/migrations/20200922102712_create_banking_accounts_table.go',
                'internal/app/payoutDetails/model.go', 'internal/app/merchant/feature.go',
                'pkg/api/merchant_config.go', 'pkg/api/fetch_fund_account.go', 'pkg/ledger/ledger_journal_create.go',
                'internal/app/common/appConstants/constants.go'],
    'ledger': ['internal/account/seed_data/shared_account_x.go',
               'internal/journal/ledger_config/seed_data/shared_account_x.go',
               'internal/common/constant.go'],
    'fts': ['internal/gateway/service.go', 'internal/account/service.go'],
    'cfa': ['internal/fund_accounts/model.go', 'internal/contacts/service.go'],
    'x-balances': ['internal/database/migrations/20250127224555_balance.go',
                   'internal/database/model/balance.go', 'queries.sql'],
    'api': ['database/migrations/2022_06_29_143810_create_ps_payout_details.php',
            'database/migrations/2021_06_08_195420_create_payouts_details_table.php',
            'database/migrations/2016_10_24_081731_create_features_table.php',
            'database/migrations/2014_07_12_083930_create_balance.php',
            'database/migrations/2017_02_20_135839_create_fund_transfer_attempts_table.php',
            'database/migrations/2016_12_19_110546_create_reversals.php',
            'app/Models/Payout/DualWrite/PayoutDetails.php'],
    'config-proto': ['rzp/x/merchant/payouts/direct_accounts/configs.proto',
                     'rzp/x/merchant/payouts/workflows.proto'],
}

def digest(data):
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()

def dump(data):
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=True) + '\n'

def synthetic_id(namespace, original):
    if namespace == 'baseline' or original.startswith('ARENAPR') or original == 'ARENAM00000000':
        return original
    return 'ARENA' + digest(namespace + ':' + original)[:9].upper()

def node_names():
    found = []
    for file in sorted((ROOT / 'verifier/verifiers').glob('test_*.py')):
        tree = ast.parse(file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test_'):
                found.append(('verifiers/' + file.name + '::' + node.name, node.name))
    return sorted(found)

def read_sources(repos_root, verify=False):
    lock = Path(__file__).with_name('evidence-lock.json')
    if not repos_root or not Path(repos_root).is_dir():
        if verify:
            raise ValueError('Approved repository copies are required by --verify-evidence')
        if not lock.exists():
            raise ValueError('No source copies and no checked-in evidence-lock.json')
        evidence = json.loads(lock.read_text())
        return evidence, 'recorded evidence; source copies not present for this generation'
    root = Path(repos_root)
    evidence = []
    for repo, files in SOURCE_FILES.items():
        rp = root / repo
        try:
            commit = subprocess.check_output(['git', '-C', str(rp), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
        except subprocess.CalledProcessError:
            commit = None
        for rel in files:
            path = rp / rel
            evidence.append({'repository': repo, 'file': rel, 'commit': commit,
                             'sha256': digest(path.read_bytes()) if path.exists() else None,
                             'status': 'available' if path.exists() else 'missing'})
    if verify and any(x['status'] != 'available' or not x['commit'] for x in evidence):
        raise ValueError('One or more source evidence paths/commits are missing')
    return evidence, 'source file hashes and repository HEAD observed locally'

def discover_repos():
    if os.environ.get('REPOS_ROOT'):
        return os.environ['REPOS_ROOT']
    for line in (ROOT / '.env.arena').read_text().splitlines():
        if line.startswith('PAYOUTS_REPO_MIGRATIONS_DIR='):
            return str(Path(line.split('=', 1)[1]).parents[3])
    return None

def sql_statements(text):
    """Split SQL outside strings/comments; comments may themselves contain /* paths."""
    statements, buf = [], []
    pos, quote = 0, None
    while pos < len(text):
        char = text[pos]
        pair = text[pos:pos+2]
        if quote:
            buf.append(char)
            if char == quote:
                if pos + 1 < len(text) and text[pos+1] == quote:
                    buf.append(text[pos+1])
                    pos += 1
                else:
                    quote = None
        elif pair == '--':
            end = text.find('\n', pos)
            pos = len(text) if end < 0 else end
            buf.append('\n')
            continue
        elif pair == '/*':
            end = text.find('*/', pos + 2)
            if end < 0:
                raise ValueError('Unclosed SQL template comment')
            pos = end + 2
            buf.append(' ')
            continue
        elif char in ("'", '"', '`'):
            quote = char
            buf.append(char)
        elif char == ';':
            statement = ''.join(buf).strip()
            if statement and statement not in ('BEGIN', 'COMMIT'):
                statements.append(statement)
            buf = []
        else:
            buf.append(char)
        pos += 1
    if quote:
        raise ValueError('Unclosed SQL template string')
    tail = ''.join(buf).strip()
    if tail and tail not in ('BEGIN', 'COMMIT'):
        statements.append(tail)
    return statements


def transform(text, namespace, slot, epoch):
    text = re.sub(r'ARENA[A-Z0-9]{5,}', lambda m: synthetic_id(namespace, m.group()), text)
    if slot:
        # Numeric FTS identities are separate from the 14-character merchant ID space.
        text = re.sub(r'\b90000([12])\b', lambda m: str(900000 + slot * 10 + int(m[1])), text)
        text = re.sub(r'232323\d{10}', lambda m: '232323' + f'{slot:04d}' + m.group()[-6:], text)
        text = re.sub(r'111222(\d{4})', lambda m: '232324' + f'{slot:06d}' + m[1], text)
    text = text.replace('UNIX_TIMESTAMP()', str(epoch)).replace('extract(epoch from now())::int', str(epoch))
    return text

def render_sql(template, namespace, slot, epoch):
    output = []
    for statement in sql_statements(template):
        # Global category parents and channel health are shared once. Merchant
        # account IDs and FTS source IDs are namespace-specific everywhere else.
        has_specific_id = any(not x.startswith('ARENAPR') and x != 'ARENAM00000000'
                              for x in re.findall(r'ARENA[A-Z0-9]{5,}', statement))
        is_specific = has_specific_id or bool(re.search(r'\b90000[12]\b', statement))
        if slot and not is_specific:
            continue
        statement = transform(statement, namespace, slot, epoch)
        if namespace == 'scenario:low_balance':
            statement = re.sub(r'\b10000000\b', '10000', statement)
        if slot and re.match(r'INSERT IGNORE INTO direct_account_routing_rules', statement):
            statement = re.sub(r'\((1|2),', lambda m: '(' + str(slot * 10 + int(m[1])) + ',', statement)
        output.append(statement + ';')
    return '\n\n'.join(output) + '\n'

def contact_hash(contact):
    fields = {k: contact[src] for k, src in [('contact', 'contact'), ('email', 'email'),
              ('merchantID', 'merchant_id'), ('name', 'name'), ('referenceID', 'reference_id'), ('type', 'type')]}
    # Go encoding/json sorts map keys and escapes these HTML characters.
    wire = json.dumps(fields, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    wire = wire.replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e')
    return hashlib.sha3_256(wire.encode()).hexdigest()

def fa_hash(fa):
    if fa['account_type'] == 'bank_account':
        bank = fa['bank_account']
        strip = lambda value: re.sub(r'[^a-zA-Z0-9]', '', value)
        name = re.sub(r"[^a-zA-Z0-9\-&'._()/]", '', bank['name'])
        detail = '|'.join([strip(bank['account_number']), strip(bank['ifsc']).upper(), name])
    else:
        vpa = fa['vpa']
        detail = (vpa['username'] + '|' + re.sub(r'[^a-zA-Z0-9]', '', vpa['handle'])).lower()
    return hashlib.sha3_256(f"{fa['merchant_id']}|contact|{fa['contact_id']}|{fa['account_type']}|{detail}".encode()).hexdigest()

def build_cfa(fund_accounts, owner_map, epoch):
    contacts, accounts, lookups = [], [], []
    for key, response in sorted(fund_accounts.items()):
        merchant = owner_map[key]
        contact = response['contact']
        c = {'id': response['contact_id'].removeprefix('cont_'), 'merchant_id': merchant,
             'name': contact['name'], 'email': contact['email'], 'contact': '', 'type': contact['type'],
             'reference_id': 'SYNTHETIC-' + key, 'notes': {}, 'active': True,
             'created_at': epoch * 1000, 'updated_at': epoch * 1000}
        c['hash'] = contact_hash(c)
        contacts.append(c)
        fa = {'id': key, 'merchant_id': merchant, 'contact_id': c['id'],
              'account_type': response['account_type'], 'active': response['active'],
              'created_at': epoch * 1000, 'updated_at': epoch * 1000}
        kind = fa['account_type']
        fa[kind] = copy.deepcopy(response[kind])
        fa[kind]['id'] = fa[kind]['id'].split('_', 1)[-1]
        fa['hash'] = fa_hash(fa)
        accounts.append(fa)
    for collection, rows in [('contacts', contacts), ('fund_accounts', accounts)]:
        for row in rows:
            lookups.append({'id': 'ARENA' + digest(collection + row['id'])[:9].upper(), 'hash': row['hash'],
                            'entity_id': row['id'], 'entity_type': collection,
                            'created_at': epoch * 1000, 'updated_at': epoch * 1000})
    return {'contacts': contacts, 'fund_accounts': accounts, 'hash_lookup': lookups}

def compile_dataset(epoch=DEFAULT_EPOCH, repos_root=None, verify_evidence=False, route_profile='monolith'):
    if route_profile not in ROUTE_PROFILES:
        raise ValueError('Unknown route profile: ' + str(route_profile))
    inputs = {rel: json.loads((SEEDS / rel).read_text()) for rel in JSON_INPUTS}
    sql_inputs = {rel: (SEEDS / 's4' / rel).read_text() for rel in SQL_INPUTS}
    result = copy.deepcopy(inputs)
    result['splitz/experiments.json']['default_variant'] = 'off'
    result['splitz/experiments.json']['_assignment_confidence'] = 'ASSUMED synthetic rollout decisions; seeded per experiment and merchant, not production variants'
    for path, key in [('merchants.json', 'merchants'), ('monolith/merchants.json', 'merchants'),
                      ('monolith/fund_accounts.json', 'fund_accounts'), ('dcs/merchants.json', 'merchants')]:
        result[path] = {'_generator': VERSION, key: {}}
    result['pricing.json']['plans'] = {}
    result['pricing.json']['free_payout_counters'] = {}
    result['stork/subscriptions.json']['webhooks'] = []
    sql = {name: [] for name in SQL_INPUTS}
    apidb = []
    mappings, verifier_index, owner_map = [], {}, {}
    namespaces = [('baseline', None)] + [('verifier:' + node, function) for node, function in node_names()]
    namespaces += [('scenario:' + name, None) for name in ['low_balance', 'direct_status', 'monolith_relay', 'kafka', 'invalid_beneficiary']]
    for slot, (namespace, function) in enumerate(namespaces):
        tr = lambda text: transform(text, namespace, slot, epoch)
        trio = {}
        for name, template in sql_inputs.items():
            rendered_sql = render_sql(template, namespace, slot, epoch)
            if name == 'ledger.sql':
                # Payouts Journal DTO uses bankingAccount.GetSignedID(); the
                # string identity in Ledger JSON is not a CHAR(14) DB key.
                rendered_sql = re.sub(r'("banking_account_id"\s*:\s*\[\s*")((?:bacc_)?ARENA[A-Z0-9]+)(")',
                                      lambda m: m[1] + 'bacc_' + m[2].removeprefix('bacc_') + m[3], rendered_sql)
            sql[name].append('-- namespace ' + namespace + '\n' + rendered_sql)
        for path, key in [('merchants.json', 'merchants'), ('monolith/merchants.json', 'merchants'),
                          ('monolith/fund_accounts.json', 'fund_accounts'), ('dcs/merchants.json', 'merchants')]:
            data = json.loads(tr(dump(inputs[path][key])))
            result[path][key].update(data)
        splitz = json.loads(tr(dump(inputs['splitz/experiments.json'])))
        for section in ('experiments', '_by_name_alias'):
            for name, experiment in splitz.get(section, {}).items():
                if isinstance(experiment, dict) and 'variants' in experiment:
                    result['splitz/experiments.json'][section][name]['variants'].update(experiment['variants'])
        price = json.loads(tr(dump(inputs['pricing.json'])))
        result['pricing.json']['plans'].update(price['plans'])
        result['pricing.json']['free_payout_counters'].update({k: v for k, v in price['free_payout_counters'].items() if not k.startswith('_')})
        result['stork/subscriptions.json']['webhooks'].extend(json.loads(tr(dump(inputs['stork/subscriptions.json']['webhooks']))))
        for i in range(1, 4):
            mid, bid, baid, faid = (tr(x) for x in [f'ARENAM0000000{i}', f'ARENABAL00000{i}', f'ARENABA000000{i}', f'ARENAFAX00000{i}'])
            source = tr('900002' if i == 2 else '900001')
            account = tr('2323230000000002' if i == 2 else '2323230099999999')
            opening = 10000 if namespace == 'scenario:low_balance' else 10000000
            item = {'key': f'M{i}', 'merchant_id': mid, 'balance_id': bid, 'banking_account_id': baid,
                    'ledger_banking_account_id': 'bacc_' + baid,
                    'fund_account_id': 'fa_' + faid, 'account_number': account, 'fts_source_account_id': int(source),
                    'fts_fund_account_id': int(source), 'opening_balance_paise': opening,
                    'archetype': ['shared', 'direct', 'workflow'][i-1],
                    'ledger_balance_account_id': tr(f'ARENAM{i}ACC0001') if i != 2 else None,
                    'requested_status_route': namespace.split(':')[-1] if namespace.startswith('scenario:') else 'monolith_relay',
                    'route_activation': 'fixture label only; runtime route needs separate wiring and verification'}
            trio[f'M{i}'] = item
            owner_map[faid] = mid
            if i == 1:
                owner_map[tr('ARENAFAX000004')] = mid
            merchant = result['monolith/merchants.json']['merchants'][mid]
            # No PAN is required by these payout DTO paths; omission is safer
            # than fabricating a syntactically real-looking tax identity.
            merchant['merchant_detail'].pop('company_pan', None)
            if i == 2 and 'in_flight_reservation_enabled' not in merchant['merchant']['feature']:
                merchant['merchant']['feature'].append('in_flight_reservation_enabled')
            result['merchants.json']['merchants'][mid]['secret_file'] = (
                f'merchant_arena_m{i}_secret' if slot == 0 else 'merchant_' + mid.lower())
            # API DB is a legacy mirror; numeric starting balance agrees across stores.
            q = lambda v: "'" + str(v).replace("'", "''") + "'"
            apidb.append('INSERT INTO merchants (id,name,live,activated,created_at) VALUES (' + ','.join([q(mid),q(merchant['merchant']['name']),'1','1',str(epoch)]) + ') ON DUPLICATE KEY UPDATE id=id;')
            apidb.append('INSERT INTO `balance` (id,merchant_id,balance,currency,type,name,on_hold,credits,fee_credits,refund_credits,account_number,account_type,channel,locked_balance,created_at,updated_at) VALUES (' +
                         ','.join([q(bid), q(mid), str(opening), "'INR'", "'banking'", q('SYNTHETIC '+item['archetype']), '0','0','0','0',q(account),q('direct' if i == 2 else 'shared'),q('rbl') if i == 2 else 'NULL','0',str(epoch - 90*86400),str(epoch)]) + ') ON DUPLICATE KEY UPDATE balance=VALUES(balance),updated_at=VALUES(updated_at);')
            if i == 2:
                fid = synthetic_id(namespace, 'ARENAFEAT00002')
                apidb.append(f"INSERT INTO features (id,entity_id,entity_type,name,created_at,updated_at) VALUES ('{fid}','{mid}','merchant','in_flight_reservation_enabled',{epoch},{epoch}) ON DUPLICATE KEY UPDATE name=VALUES(name);")
            if opening != 10000000:
                sql['xbalances.sql'].append(f"UPDATE balance SET balance={opening} WHERE id='{bid}';")
                if i != 2:
                    sql['ledger.sql'].append(f"UPDATE accounts SET balance={opening} WHERE id='{item['ledger_balance_account_id']}';")
        mapping = {'namespace': namespace, 'merchants': trio}
        mappings.append(mapping)
        if function:
            verifier_index[function] = trio
            verifier_index[namespace.removeprefix('verifier:')] = trio
        # Correct legacy fixture inconsistency: each FA has its own contact and
        # CFA/monolith publish the exact same generated beneficiary identity.
        for i in range(1, 5):
            faid = tr(f'ARENAFAX00000{i}')
            fa = result['monolith/fund_accounts.json']['fund_accounts'][faid]
            cid = tr(f'ARENACO000000{i}')
            fa['contact_id'] = 'cont_' + cid
            fa['contact']['id'] = 'cont_' + cid
            fa['contact']['contact'] = ''
    result['splitz/experiments.json']['experiments'].update(
        _route_module.route_experiments(route_profile, sorted(result['merchants.json']['merchants'])))
    cfa = build_cfa(result['monolith/fund_accounts.json']['fund_accounts'], owner_map, epoch)
    result['s4/cfa.js'] = "// Generated; synthetic data only.\ndb = db.getSiblingDB('cfa');\n" + '\n'.join(
        'const ' + collection + ' = ' + dump(rows) + ';\n' + collection + '.forEach(row => db.' + collection + '.updateOne({id:row.id}, {$setOnInsert:row}, {upsert:true}));'
        for collection, rows in cfa.items()) + '\n'
    result['cfa-entities.json'] = cfa
    for name, chunks in sql.items():
        result['s4/' + name] = '\n'.join(chunks)
    result['s4/apidb.sql'] = '\n'.join(apidb) + '\n'
    result['scenario-index.json'] = {'generator_version': VERSION, 'epoch': epoch, 'route_profile': route_profile, 'verifiers': verifier_index,
                                     'namespaces': mappings, 'baseline': mappings[0]['merchants'],
                                     'invalid_requests': {'missing_fund_account_id': 'fa_ARENAMISSING01', 'invalid_ifsc': 'INVALID',
                                                          'meaning': 'request-only invalid variants; do not insert malformed persisted bank rows'}}
    evidence, evidence_status = read_sources(repos_root, verify_evidence)
    provenance = {'generator_version': VERSION, 'epoch': epoch, 'route_profile': route_profile, 'evidence_status': evidence_status,
                  'sources': evidence, 'source_templates': [], 'cross_service_id_mapping': mappings,
                  'system_identifier_exceptions': [{'id': k, 'meaning': v, 'classification': 'exact code constant, not merchant fixture data',
                                                   'source': 'ledger/internal/common/constant.go'} for k, v in SYSTEM_IDS.items()],
                  'field_rules': field_rules(),
                  'limitations': ['Route labels do not activate direct or Kafka delivery.',
                                  'Workflow seeds establish configuration only; approval fidelity needs service contract validation.',
                                  'Opening Ledger balances use arena SQL fixture initialization, not a production funding workflow.',
                                  'Bank numbers use a deterministic local namespace; no bank claims that namespace is globally reserved.',
                                  'Generated SQL is for empty-volume startup. Runtime reset must clear state, not append these rows.']}
    for rel in JSON_INPUTS + ['s4/' + x for x in SQL_INPUTS]:
        provenance['source_templates'].append({'file': 'ENV2_COMPOSE/seeds/' + rel, 'sha256': digest((SEEDS / rel).read_bytes()),
                                               'classification': 'versioned synthetic schema template; not copied DevStack records'})
    schema_lock = Path(__file__).with_name('schema-evidence.json')
    if schema_lock.exists():
        provenance['schema_evidence'] = json.loads(schema_lock.read_text())
        provenance['schema_evidence_status'] = 'recorded static migration declarations; refresh with schema_compare.py --write-evidence-lock'
    provenance['related_schema_inputs'] = [{'file':'ENV2_COMPOSE/seeds/mysql/apidb-ddl/00_init.sql', 'sha256':digest((SEEDS/'mysql/apidb-ddl/00_init.sql').read_bytes()), 'classification':'TWIN_SPEC reconciled API schema subset; per-object limitations remain explicit'}]
    result['provenance.json'] = provenance
    rendered = {k: v if isinstance(v, str) else dump(v) for k, v in result.items()}
    provenance['output_sha256'] = {k: digest(v) for k, v in sorted(rendered.items()) if k != 'provenance.json'}
    rendered['provenance.json'] = dump(provenance)
    validate(rendered)
    return rendered, evidence

def field_rules():
    return [
        {'fields': ['merchant_id', 'balance_id', 'banking_account_id', 'fund_account_id'], 'type': 'ASCII alphanumeric string <=14 chars; fa_ is HTTP-only', 'constraints': 'unique by namespace and entity; references agree across services', 'generation_rule': 'baseline IDs retained; ARENA + 9 hex SHA256(namespace:template ID) otherwise', 'classification': 'representative values, exact cross-service reference constraints', 'source': 'payouts/internal/database/migrations/20200922102712_create_banking_accounts_table.go'},
        {'fields': ['ledger.account_details.entities.banking_account_id'], 'type': 'string array containing bacc_ + persisted banking-account ID', 'constraints': 'must match Payouts Journal DTO GetSignedID()', 'generation_rule': 'signed HTTP/domain identity derived from the same generated bare banking-account ID', 'classification': 'exact source DTO mapping; live mismatch confirmed before correction', 'source': 'payouts/pkg/ledger/ledger_journal_create.go:179'},
        {'fields': ['fts.source_accounts.id', 'fts.fund_accounts.id'], 'type': 'positive SQL integer', 'constraints': 'not a Razorpay string ID; Payouts stores decimal string', 'generation_rule': '900000 + namespace ordinal*10 + 1 (pool) or 2 (direct)', 'classification': 'representative values', 'source': 'fts/internal/migrations'},
        {'fields': ['account_type', 'channel'], 'type': 'enum/string', 'constraints': 'payouts shared/direct and lowercase rbl; x-balances pool/direct; FTS POOL/DIRECT and uppercase RBL', 'generation_rule': 'explicit archetype mapping inherited from source-aligned templates', 'classification': 'exact enum mapping', 'source': 'x-balances/internal/database/model/balance.go'},
        {'fields': ['balance', 'fees', 'tax'], 'type': 'integer paise; Ledger numeric', 'constraints': 'nonnegative starting funds', 'generation_rule': '10,000,000 paise per ordinary merchant; 10,000 low balance; fee200/tax36 representative', 'classification': 'representative amounts', 'source': 'payouts/pkg/api/fetch_pricing.go'},
        {'fields': ['cfa.contacts.hash', 'cfa.fund_accounts.hash'], 'type': '64 lowercase hex SHA3-256', 'constraints': 'hash reflects generated identity and bank fields', 'generation_rule': 'Go sorted compact JSON for contacts; merchant|contact|contactID|accountType|normalized details for FA', 'classification': 'exact hash algorithm for generated ASCII bank/VPA cases', 'source': 'cfa/internal/fund_accounts/model.go; cfa/internal/contacts/service.go'},
        {'fields': ['company_pan', 'contact.phone', 'account_number', 'ifsc', 'utr'], 'type': 'optional strings', 'constraints': 'no real records or tax identities', 'generation_rule': 'PAN omitted; phone blank; account generated locally; IFSC routing code from synthetic template; UTR not preseeded', 'classification': 'representative; IFSC is bank reference metadata, not customer identity', 'source': 'payouts/pkg/api/fetch_fund_account.go'},
        {'fields': ['created_at', 'updated_at', 'last_fetched_at'], 'type': 'Unix seconds SQL; milliseconds CFA', 'constraints': 'timestamp freshness can affect live processing', 'generation_rule': 'explicit --epoch; repeat same epoch for deterministic files; startup supplies current epoch', 'classification': 'representative wall clock', 'source': 'x-balances/internal/database/model/balance.go'},
        {'fields': ['in_flight_reservation_enabled'], 'type': 'DCS bool and legacy API features row', 'constraints': 'both representations true for Direct', 'generation_rule': 'DCS Configs key plus features(entity_id,entity_type=merchant,name)', 'classification': 'exact representation; runtime behavior requires verification', 'source': 'payouts/internal/app/merchant/feature.go; config-proto/rzp/x/merchant/payouts/direct_accounts/configs.proto'},
    ]

def validate(rendered):
    index = json.loads(rendered['scenario-index.json'])
    seen = set()
    for group in index['namespaces']:
        for item in group['merchants'].values():
            mid = item['merchant_id']
            if not re.fullmatch(r'ARENA[A-Z0-9]{8,9}', mid) or mid in seen:
                raise ValueError('Invalid or colliding synthetic merchant ID: ' + mid)
            seen.add(mid)
    for path, content in rendered.items():
        if 'company_pan' in content and path != 'provenance.json':
            raise ValueError('PAN field unexpectedly emitted in ' + path)
    for table in ('accounts', 'account_details'):
        ids = []
        for statement in sql_statements(rendered['s4/ledger.sql']):
            if re.match(r'INSERT INTO ' + table + r'\s', statement):
                ids.extend(re.findall(r"\(\s*'([^']+)'", statement.split('VALUES', 1)[1]))
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate Ledger ' + table + ' seed IDs; check system-parent allocation')
        if table == 'accounts':
            required = {m['ledger_balance_account_id'] for ns in index['namespaces'] for m in ns['merchants'].values() if m['ledger_balance_account_id']}
            if not required.issubset(ids):
                raise ValueError('Missing Ledger merchant balance account rows')
    actual_banking_ids = set(re.findall(r'"banking_account_id"\s*:\s*\[\s*"([^"]+)"', rendered['s4/ledger.sql']))
    required_banking_ids = {m['ledger_banking_account_id'] for ns in index['namespaces'] for m in ns['merchants'].values() if m['ledger_balance_account_id']}
    if actual_banking_ids != required_banking_ids:
        raise ValueError('Ledger banking_account_id must match signed Payouts Journal DTO')
    feature_ids = re.findall(r"INSERT INTO features .*?VALUES \('([^']+)'", rendered['s4/apidb.sql'])
    if any(len(value) > 14 for value in feature_ids) or len(feature_ids) != len(set(feature_ids)):
        raise ValueError('API DB feature ID exceeds CHAR(14) or collides')
    contacts = json.loads(rendered['cfa-entities.json'])['contacts']
    if len({c['id'] for c in contacts}) != len(contacts):
        raise ValueError('Duplicate CFA contact IDs')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=SEEDS / 'generated')
    parser.add_argument('--epoch', type=int, default=DEFAULT_EPOCH)
    parser.add_argument('--repos-root', default=discover_repos())
    parser.add_argument('--verify-evidence', action='store_true')
    parser.add_argument('--write-evidence-lock', action='store_true')
    parser.add_argument('--route-profile', choices=sorted(ROUTE_PROFILES), default=os.environ.get('ARENA_ROUTE_PROFILE', 'monolith'))
    args = parser.parse_args()
    rendered, evidence = compile_dataset(args.epoch, args.repos_root, args.verify_evidence, args.route_profile)
    for rel, content in rendered.items():
        path = args.output / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    if args.write_evidence_lock:
        Path(__file__).with_name('evidence-lock.json').write_text(dump(evidence))
    print(f'Generated {len(rendered)} files; {len(json.loads(rendered["scenario-index.json"])["namespaces"])} isolated namespaces; epoch={args.epoch}')

if __name__ == '__main__':
    main()
