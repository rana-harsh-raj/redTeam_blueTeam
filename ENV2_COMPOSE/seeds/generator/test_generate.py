"""Offline observable constraints, without database or network access."""
import json
import re
import unittest
from pathlib import Path
import generate

class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.output, _ = generate.compile_dataset(repos_root='/nonexistent')
        cls.index = json.loads(cls.output['scenario-index.json'])

    def test_same_inputs_are_byte_identical(self):
        again, _ = generate.compile_dataset(repos_root='/nonexistent')
        self.assertEqual(self.output, again)

    def test_each_test_has_isolated_ids_and_three_archetypes(self):
        groups = self.index['namespaces']
        self.assertEqual(len(groups), len(generate.node_names()) + 6)
        for field in ['merchant_id', 'balance_id', 'banking_account_id', 'fund_account_id']:
            ids = [m[field] for group in groups for m in group['merchants'].values()]
            self.assertEqual(len(ids), len(set(ids)), field)
        for node, function in generate.node_names():
            self.assertEqual(self.index['verifiers'][node], self.index['verifiers'][function])
        for group in groups:
            self.assertEqual(['shared', 'direct', 'workflow'], [m['archetype'] for m in group['merchants'].values()])

    def test_sql_and_http_references_agree(self):
        merchants = json.loads(self.output['monolith/merchants.json'])['merchants']
        fas = json.loads(self.output['monolith/fund_accounts.json'])['fund_accounts']
        gateway = json.loads(self.output['merchants.json'])['merchants']
        dcs = json.loads(self.output['dcs/merchants.json'])['merchants']
        for group in self.index['namespaces']:
            for key, m in group['merchants'].items():
                mid = m['merchant_id']
                self.assertEqual(merchants[mid]['balance_id'], m['balance_id'])
                self.assertIn(m['fund_account_id'][3:], fas)
                self.assertIn(mid, gateway)
                for service in ['payouts', 'xbalances', 'apidb', 'fts']:
                    self.assertIn("'" + mid + "'", self.output['s4/' + service + '.sql'])
                self.assertIn("'" + m['account_number'] + "'", self.output['s4/fts.sql'])
                self.assertIn(str(m['fts_source_account_id']), self.output['s4/fts.sql'])
                if key == 'M2':
                    self.assertIn('in_flight_reservation_enabled', merchants[mid]['merchant']['feature'])
                    self.assertTrue(dcs[mid]['dcs']['rzp/x/merchant/payouts/direct_accounts/Configs']['in_flight_reservation_enabled'])
                    self.assertIn("'" + mid + "','merchant','in_flight_reservation_enabled'", self.output['s4/apidb.sql'])

    def test_cfa_and_monolith_beneficiaries_agree_and_hashes_are_fresh(self):
        cfa = json.loads(self.output['cfa-entities.json'])
        fas = json.loads(self.output['monolith/fund_accounts.json'])['fund_accounts']
        contacts = {c['id']: c for c in cfa['contacts']}
        for fa in cfa['fund_accounts']:
            http = fas[fa['id']]
            self.assertEqual(http['contact_id'], 'cont_' + fa['contact_id'])
            self.assertEqual(http['active'], fa['active'])
            self.assertEqual(http[fa['account_type']].get('account_number'), fa[fa['account_type']].get('account_number'))
            self.assertEqual(generate.fa_hash(fa), fa['hash'])
            self.assertIn(fa['contact_id'], contacts)
            mutated = dict(fa, merchant_id='ARENAOTHER00001')
            self.assertNotEqual(generate.fa_hash(mutated), fa['hash'])
        for contact in contacts.values():
            self.assertEqual(contact['hash'], generate.contact_hash(contact))
        self.assertEqual(len(cfa['hash_lookup']), len(cfa['contacts']) + len(cfa['fund_accounts']))

    def test_xbalances_and_fts_use_their_distinct_channel_enums(self):
        # XBalances returns channel verbatim to Payouts' case-sensitive validator;
        # FTS routing uses its own uppercase enum. Validate all expanded fixtures.
        channels = re.findall(r"'(?:pool|direct)'\s*,\s*'([^']+)'\s*,\s*'INR'", self.output['s4/xbalances.sql'])
        self.assertEqual(len(channels), 3 * len(self.index['namespaces']))
        self.assertEqual(set(channels), {'rbl'})
        self.assertIn("'RBL'", self.output['s4/fts.sql'])
        self.assertNotIn("'rbl'", self.output['s4/fts.sql'])

    def test_nullable_api_beneficiary_type_uses_source_fts_default(self):
        accounts=json.loads(self.output['monolith/fund_accounts.json'])['fund_accounts']
        for fund_account in accounts.values():
            if fund_account['account_type']!='bank_account': continue
            value=fund_account['bank_account']['account_type']
            # API BankAccount Validator accepts nullable API-domain values;
            # Payouts defaults an unspecified type to saving. FTS validates
            # saving/current/nodal case-insensitively, not plural savings.
            self.assertIn(value,(None,'savings','current','cc','nre','nro'))
            self.assertIn((value or 'saving').lower(),('saving','current','nodal'))

    def test_ids_and_sensitive_fields(self):
        for group in self.index['namespaces']:
            for merchant in group['merchants'].values():
                self.assertRegex(merchant['merchant_id'], r'^ARENA[A-Z0-9]{9}$')
                self.assertEqual(merchant['opening_balance_paise'], 10000 if group['namespace'] == 'scenario:low_balance' else 10000000)
        merchants = json.loads(self.output['monolith/merchants.json'])['merchants']
        self.assertTrue(all('company_pan' not in m['merchant_detail'] for m in merchants.values()))
        for value in json.loads(self.output['merchants.json'])['merchants'].values():
            self.assertNotIn('secret', value)
            self.assertIn('secret_file', value)

    def test_ledger_duplicate_fixture_ids_are_rejected(self):
        mutated = dict(self.output)
        mutated['s4/ledger.sql'] += "\nINSERT INTO accounts (id) VALUES ('ARENAPRACC0001');\n"
        with self.assertRaisesRegex(ValueError, 'Duplicate Ledger'):
            generate.validate(mutated)

    def test_inactive_beneficiary_keeps_shield_suffix(self):
        fas = json.loads(self.output['monolith/fund_accounts.json'])['fund_accounts']
        for fa in fas.values():
            if not fa['active']:
                self.assertTrue(fa['bank_account']['account_number'].endswith('9999'))

    def test_api_feature_ids_fit_char14_schema(self):
        ids = re.findall(r"INSERT INTO features .*?VALUES \('([^']+)'", self.output['s4/apidb.sql'])
        self.assertEqual(len(ids), len(self.index['namespaces']))
        self.assertTrue(all(len(value) <= 14 for value in ids))
        self.assertEqual(len(ids), len(set(ids)))
        mutated = dict(self.output)
        mutated['s4/apidb.sql'] = mutated['s4/apidb.sql'].replace('ARENAFEAT00002', 'ARENAFEAT000002')
        with self.assertRaisesRegex(ValueError, r'CHAR\(14\)'):
            generate.validate(mutated)

    def test_ledger_banking_identity_is_signed_like_journal_dto(self):
        signed = set(re.findall(r'"banking_account_id"\s*:\s*\[\s*"([^"]+)"', self.output['s4/ledger.sql']))
        expected = {m['ledger_banking_account_id'] for ns in self.index['namespaces'] for m in ns['merchants'].values() if m['ledger_balance_account_id']}
        self.assertEqual(signed, expected)
        mutated = dict(self.output)
        mutated['s4/ledger.sql'] = mutated['s4/ledger.sql'].replace('bacc_', '')
        with self.assertRaisesRegex(ValueError, 'signed Payouts Journal DTO'):
            generate.validate(mutated)

    def test_splitz_base_variants_expand_to_every_merchant(self):
        data = json.loads(self.output['splitz/experiments.json'])
        expected = {m['merchant_id'] for ns in self.index['namespaces'] for m in ns['merchants'].values()}
        for section in ('experiments', '_by_name_alias'):
            for experiment in data[section].values():
                if isinstance(experiment, dict) and 'variants' in experiment:
                    self.assertEqual(set(experiment['variants']), expected)

    def test_all_route_profiles_are_recorded_and_hashed(self):
        for profile, flags in generate.ROUTE_PROFILES.items():
            rendered, _ = generate.compile_dataset(repos_root='/nonexistent', route_profile=profile)
            variants = json.loads(rendered['splitz/experiments.json'])['experiments']
            self.assertEqual(set(variants['arena_fts_meta']['variants'].values()), {'on' if flags['direct_status'] else 'off'})
            self.assertEqual(set(variants['arena_fts_kafka']['variants'].values()), {'on' if flags['kafka'] else 'off'})
            provenance = json.loads(rendered['provenance.json'])
            self.assertEqual(provenance['route_profile'], profile)
            self.assertEqual(provenance['output_sha256']['splitz/experiments.json'], generate.digest(rendered['splitz/experiments.json']))
        with self.assertRaises(ValueError):
            generate.compile_dataset(route_profile='unknown')

    def test_api_schema_names_keys_and_absent_service_only_fields(self):
        ddl = (generate.SEEDS / 'mysql/apidb-ddl/00_init.sql').read_text()
        tables = {}
        for statement in generate.sql_statements(ddl):
            match = re.match(r'CREATE TABLE IF NOT EXISTS `([^`]+)`', statement)
            if match:
                tables[match[1]] = statement
        expected = {'payouts', 'payouts_details', 'payouts_status_details', 'reversals',
                    'workflow_entity_map', 'fund_transfer_attempts', 'idempotency_keys',
                    'features', 'balance', 'banking_accounts', 'contacts', 'fund_accounts',
                    'bank_accounts', 'vpas', 'merchants', 'merchant_details', 'merchant_users',
                    'keys', 'payout_sources'}
        self.assertEqual(set(tables), expected)
        self.assertNotIn('`id`', tables['payouts_details'])
        self.assertNotIn('beneficiary_bank_code', tables['payouts_details'])
        self.assertRegex(tables['payouts_details'], r'`payout_id`\s+CHAR\(14\) NOT NULL PRIMARY KEY')
        self.assertNotIn('`payout_id`', tables['reversals'])
        self.assertNotIn('`fees`', tables['reversals'])
        self.assertIn('`fee`', tables['reversals'])
        self.assertIn('UNIQUE KEY `features_name_entity_id_unique` (`name`,`entity_id`)', tables['features'])
        self.assertNotIn('`primary`', tables['balance'])
        order = list(tables)
        self.assertLess(order.index('merchants'), order.index('idempotency_keys'))
        self.assertLess(order.index('merchants'), order.index('balance'))

    def test_generated_api_columns_exist_and_foreign_key_parents_precede_balances(self):
        ddl = (generate.SEEDS / 'mysql/apidb-ddl/00_init.sql').read_text()
        tables = {}
        for statement in generate.sql_statements(ddl):
            match = re.match(r'CREATE TABLE IF NOT EXISTS `([^`]+)`', statement)
            if match:
                tables[match[1]] = set(re.findall(r'`([a-z_]+)`\s+(?:CHAR|VARCHAR|BIGINT|INT|TINYINT|JSON|TEXT)', statement))
        seen_merchants = set()
        for statement in generate.sql_statements(self.output['s4/apidb.sql']):
            match = re.match(r'INSERT INTO `?([a-z_]+)`? \(([^)]+)\)', statement)
            self.assertIsNotNone(match)
            table, columns = match.groups()
            self.assertTrue({c.strip(' `') for c in columns.split(',')}.issubset(tables[table]), table)
            values = re.findall(r"'([^']*)'", statement.split('VALUES', 1)[1])
            if table == 'merchants':
                seen_merchants.add(values[0])
            elif table == 'balance':
                self.assertIn(values[1], seen_merchants)
        self.assertEqual(len(seen_merchants), 3 * len(self.index['namespaces']))
        self.assertEqual(json.loads(self.output['splitz/experiments.json'])['default_variant'], 'off')

    def test_ledger_queue_processes_and_scheduler_override(self):
        compose = (generate.ROOT / 'docker-compose.yml').read_text()
        expected = {'ledger-worker':'account_create', 'ledger-worker-balance-update':'balance_update',
                    'ledger-worker-journal-create':'journal_create',
                    'ledger-worker-entry-details-create':'ledger_entry_details_create',
                    'ledger-worker-entry-details-create-pg':'ledger_entry_details_create_pg'}
        for service, queue in expected.items():
            block = compose.split('  ' + service + ':\n', 1)[1].split('\n  ', 1)[0]
            # Select until the next two-space service key, not nested keys.
            block = re.split(r'\n  [a-z][a-z0-9-]+:\n', compose.split('  ' + service + ':\n', 1)[1], maxsplit=1)[0]
            self.assertIn('LEDGER_WORKER_QUEUENAME: "' + queue + '"', block)
            self.assertIn('--queue-name ' + queue, (generate.SEEDS / 'localstack/init-queues.sh').read_text())
        scheduler = compose.split('  ledger-scheduler:\n', 1)[1].split('  fts-web:\n', 1)[0]
        self.assertIn('LEDGER_SCHEDULER_COMMAND: "split-account-balance-update"', scheduler)

    def test_output_integrity_and_evidence_records(self):
        manifest = json.loads(self.output['provenance.json'])
        for path, expected in manifest['output_sha256'].items():
            self.assertEqual(generate.digest(self.output[path]), expected)
        self.assertEqual(set(generate.SYSTEM_IDS), {x['id'] for x in manifest['system_identifier_exceptions']})
        self.assertTrue(all(x['commit'] and x['sha256'] for x in manifest['sources'] if x['status'] == 'available'))
        with self.assertRaises(ValueError):
            generate.read_sources('/nonexistent', verify=True)

    def test_direct_routing_rule_primary_keys_are_unique(self):
        ids = []
        for statement in generate.sql_statements(self.output['s4/fts.sql']):
            if statement.startswith('INSERT IGNORE INTO direct_account_routing_rules'):
                values = statement.split('VALUES', 1)[1]
                ids.extend(int(v) for v in re.findall(r'\((\d+),', values))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 2 * len(self.index['namespaces']))

if __name__ == '__main__':
    unittest.main()
