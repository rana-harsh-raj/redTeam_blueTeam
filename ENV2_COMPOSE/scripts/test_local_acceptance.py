"""Offline tests for the generated acceptance gate.

Every test builds a complete fake run tree in a temporary directory and points
the producer at it: no Docker, no network, no reads of the real checkout apart
from the two scripts under test. `unittest.TestCase` classes are collected by
pytest as-is, so this runs under either

    python3 -m pytest ENV2_COMPOSE/scripts/test_local_acceptance.py -q
    python3 -m unittest discover -s ENV2_COMPOSE/scripts -p 'test_local_acceptance.py'
"""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    'local_acceptance', Path(__file__).with_name('local-acceptance.py'))
gate_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate_module)

T = 1_700_000_000.0                 # scenario base time
WINDOW = (T - 10.0, T + 200.0)      # egress capture window that brackets it
IMAGES = {'rzp-arena/%s:local' % name: 'sha256:image-' + name
          for name in ('payouts', 'ledger', 'fts', 'cfa', 'xbalances')}
BUILD_IMAGES = {'payouts': 'sha256:image-payouts', 'ledger': 'sha256:image-ledger',
                'fts': 'sha256:image-fts', 'cfa': 'sha256:image-cfa',
                'x-balances': 'sha256:image-xbalances'}
CONTAINERS = {'payouts-api': 'container-payouts-api', 'fts-web': 'container-fts-web'}

ROUTE_CASES = gate_module.ROUTE_CASES
BANK_CASES = gate_module.BANK_CASES
KAFKA_CASES = gate_module.KAFKA_CASES

EXPECTED_FAILURES = """
schema_version: 1
expected_failures:
  - id: EF-001
    title: Shared Kafka processed
    scope: {profile: kafka, cases: [shared_source_failure, golden-shared]}
    predicted_state:
      payout_status: initiated
      fts_status: PROCESSED
      journals: {present: [payout_initiated], absent: [payout_processed]}
      webhooks: {absent: [payout.processed]}
    source_refs: ['payouts internal/boot/handler.go:154']
    deployed_status: consumer deployed with 3 replicas
  - id: EF-002
    title: Kafka FAILED dropped
    scope: {profile: kafka, cases: [failed_dropped_direct, failed_dropped_shared]}
    predicted_state:
      payout_status: initiated
      fts_status: FAILED
      journals: {absent: [payout_failed]}
      webhooks: {absent: [payout.failed]}
    source_refs: ['payouts internal/taskHandlers/fts_status_updates.go:56-62']
    deployed_status: consumer deployed with 3 replicas
  - id: EF-003
    title: Kafka REVERSED dropped
    scope: {profile: kafka, cases: [reversed_dropped_direct]}
    predicted_state:
      payout_status: processed
      fts_status: REVERSED
      journals: {absent: [payout_reversed]}
      webhooks: {absent: [payout.reversed]}
    source_refs: ['payouts internal/taskHandlers/fts_status_updates.go:56-62']
    deployed_status: consumer deployed with 3 replicas
"""

DECLARED_DEVIATIONS = """
declared_deviations:
  - id: DD-001
    status: declared
    location: ENV2_COMPOSE/config/templates/base/ledger/arena.toml
  - id: DD-002
    status: fixed_in_m1
    location: ENV2_COMPOSE/substitutes/ledger-gate/server.py
    fix_ref: FD-002
"""

CAPTURE_STATS = ('tcpdump: listening on any\n12 packets captured\n'
                 '12 packets received by filter\n0 packets dropped by kernel')


def layers(payout_status='processed', fts_status='PROCESSED',
           journals=('payout_initiated', 'payout_processed'),
           webhooks=('payout.initiated', 'payout.processed')):
    return {
        'payouts': {'row': {'status': payout_status}},
        'fts': {'transfers': ([{'status': fts_status}] if fts_status else [])},
        'ledger': {'journals': [{'transactor_event': e} for e in journals]},
        'merchant_webhooks': {'deliveries': [{'body': {'event': e}} for e in webhooks]},
    }


def kafka_observed(payout_status, fts_status, journals, webhooks):
    return {'payout_status': payout_status, 'fts_status': fts_status,
            'journal_events': sorted(journals), 'webhook_events': sorted(webhooks)}


KAFKA_CASE_STATE = {
    'direct_after_shared': ('processed', 'PROCESSED',
                            ['payout_initiated'], ['payout.initiated', 'payout.processed'], None),
    'failed_dropped_direct': ('initiated', 'FAILED',
                              ['payout_initiated'], ['payout.initiated'], 'EF-002'),
    'failed_dropped_shared': ('initiated', 'FAILED',
                              ['payout_initiated'], ['payout.initiated'], 'EF-002'),
    'reversed_dropped_direct': ('processed', 'REVERSED',
                                ['payout_initiated', 'payout_processed'],
                                ['payout.initiated', 'payout.processed'], 'EF-003'),
}


class Fixture:
    """A complete, passing fake checkout; mutate it to exercise a failure."""

    def __init__(self, root: Path):
        self.root = root
        self.compose = root / 'ENV2_COMPOSE'
        self.runs = root / 'reports/implementation/runs'
        self.hashes = {}
        self.digest = None      # both set by _write_tree once the tree exists
        self._write_tree()

    # ------------------------------------------------------------- helpers

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if isinstance(content, str)
                        else json.dumps(content, indent=2) + '\n')
        return path

    def read_json(self, relative):
        return json.loads((self.root / relative).read_text())

    def patch_json(self, relative, mutate):
        data = self.read_json(relative)
        mutate(data)
        self.write(relative, data)

    # -------------------------------------------------------------- pieces

    def fingerprint(self, **overrides):
        data = {
            'schema_version': 1, 'boot_id': 'boot-0001',
            'recorded_at': '2026-01-01T00:00:00+00:00', 'route_profile': 'monolith',
            'compose_project': 'env2_compose', 'git_head': None,
            'config_digest': self.digest, 'config_hashes': dict(self.hashes),
            'images': dict(IMAGES), 'service_container_ids': dict(CONTAINERS),
            'images_available': True,
        }
        data.update(overrides)
        return data

    def egress(self, **overrides):
        data = {'schema_version': 1, 'status': 'passed', 'command_exit_code': 0,
                'command_started_at': dt.datetime.fromtimestamp(WINDOW[0], dt.timezone.utc).isoformat(),
                'command_finished_at': dt.datetime.fromtimestamp(WINDOW[1], dt.timezone.utc).isoformat(),
                'outside_packets': 0, 'destinations': [],
                'outside_capture_stats': CAPTURE_STATS, 'control_capture_stats': CAPTURE_STATS}
        data.update(overrides)
        return data

    def evidence(self, directory, scenario, payout_id, profile='monolith', consumer=False):
        data = {'schema_version': 1, 'profile': profile, 'payout_id': payout_id,
                'scenario_status': 'passed', 'observed': {}, 'required': [],
                'transport_observed': True, 'records': []}
        if consumer:
            data['consumer_state'] = {
                'containers': [
                    {'service': 'payouts-kafka-fts-status-updates-consumer',
                     'running': True, 'restart_count': 0},
                    {'service': 'payouts-kafka-fts-status-updates-retry-consumer',
                     'running': True, 'restart_count': 0}],
                'panic_count': 0}
        self.write(directory + '/route-evidence-%s.json' % scenario, data)

    def junit(self, tests=26, failures=0, errors=0, skipped=0, skip_case=False):
        cases = ''.join('<testcase classname="v" name="test_%d" time="0.1" />' % i
                        for i in range(tests - (1 if skip_case else 0)))
        if skip_case:
            cases += '<testcase classname="v" name="test_skipped" time="0.0"><skipped/></testcase>'
        return ('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" '
                'errors="%d" failures="%d" skipped="%d" tests="%d" time="10.0" timestamp="%s">'
                '%s</testsuite></testsuites>'
                % (errors, failures, skipped, tests,
                   dt.datetime.fromtimestamp(T, dt.timezone.utc).isoformat(), cases))

    # ---------------------------------------------------------------- tree

    def _write_tree(self):
        # A minimal ENV2_COMPOSE whose files the fingerprint hashes.
        self.write('ENV2_COMPOSE/docker-compose.yml', 'services: {}\n')
        self.write('ENV2_COMPOSE/config/generate.py', '# generator\n')
        self.write('ENV2_COMPOSE/config/routes.py', '# routes\n')
        self.write('ENV2_COMPOSE/config/templates/base/payouts/arena.toml', 'x = 1\n')
        self.write('ENV2_COMPOSE/seeds/pricing.json', '{}\n')
        self.write('ENV2_COMPOSE/substitutes/kong-lite/server.py', '# kong\n')
        self.write('ENV2_COMPOSE/verifier/conftest.py', '# conftest\n')
        self.write('ENV2_COMPOSE/scripts/cron-driver/driver.py', '# driver\n')

        self.hashes = gate_module.fingerprint.config_hashes(self.compose)
        self.digest = gate_module.fingerprint.config_digest(self.hashes)

        self.write('TWIN_SPEC/expected-failures.yaml', EXPECTED_FAILURES)
        self.write('ENV2_COMPOSE/config/declared-deviations.yaml', DECLARED_DEVIATIONS)
        self.write('reports/implementation/fixed-twin-defects.json', {
            'schema_version': 1,
            'defects': [{'id': 'FD-001', 'title': 'placeholder brokers',
                         'location': 'ENV2_COMPOSE/config/templates/base/payouts/arena.toml',
                         'symptom': 'consumer panics on republish',
                         'fix': 'Brokers set to kafka:9092 in commit abc1234',
                         'evidence_run': 'reports/implementation/runs/kafka-final'}]})

        # Build evidence.
        base = '.local/twin-repos/build-evidence-test'
        self.write(base + '/build-results.json', {
            'schema_version': 1, 'tag': 'v1-candidate',
            'builds': [{'service': service, 'exit_code': 0, 'candidate_image': image}
                       for service, image in sorted(BUILD_IMAGES.items())]})
        self.write(base + '/post-build-verification.json', {
            'source_clones': [{'repository': name, 'commit_unchanged': True,
                               'dirty_status_unchanged': True}
                              for name in sorted(BUILD_IMAGES)]})
        self.write(base + '/provenance.json', {
            'schema_version': 1, 'status': 'prepared',
            'repositories': [{'name': name, 'source_commit': 'c' + name}
                             for name in sorted(BUILD_IMAGES)]})

        # Audit.
        audit = 'reports/implementation/audits/test-boot'
        self.write(audit + '/audit-summary.json', {
            'schema_version': 1, 'status': 'passed_with_caveats',
            'initial_service_container_ids': dict(CONTAINERS),
            'final_service_container_ids': dict(CONTAINERS),
            'same_running_boot': True,
            'steps': [{'reader': 'current_boot.py', 'exit_code': 0,
                       'report': 'secret-and-endpoint-audit.json', 'status': 'passed'},
                      {'reader': 'logical_stores.py', 'exit_code': 0,
                       'report': 'logical-store-secret-scan.json', 'status': 'passed'},
                      {'reader': 'remaining_volumes.py', 'exit_code': 0,
                       'report': 'remaining-volume-secret-scan.json', 'status': 'passed'}]})
        for name in ('secret-and-endpoint-audit', 'logical-store-secret-scan',
                     'remaining-volume-secret-scan'):
            self.write(audit + '/%s.json' % name,
                       {'schema_version': 1, 'status': 'passed', 'unresolved_findings': 0})

        self.write('reports/implementation/resource-profile.json', {
            'schema_version': 1,
            'phases': {phase: {'observed': True, 'sample_count': 3, 'peak_cpu_cores': 2.0,
                               'peak_memory_gib': 4.0, 'disk_gib': 8.0}
                       for phase in ('idle', 'successful_payout', 'full_verifier')}})
        self.write('reports/implementation/logical-replay-final.json', {
            'schema_version': 3, 'status': 'passed', 'logical_equivalence': True,
            'differences': [], 'independent_directories': True,
            'input_directories': {'first': 'replay_a', 'second': 'replay_b'},
            'first': {'valid': True, 'validation_errors': []},
            'second': {'valid': True, 'validation_errors': []}})
        self.write('reports/implementation/explorer-verification.json', {
            'schema_version': 1, 'saved_status': 'passed',
            'static': {'payout_visible': True, 'verified_steps': 5},
            'live_server': {'payout_visible': True, 'verified_steps': 5}})

        self._write_replays()
        self._write_monolith()
        self._write_profiles()
        self._write_kafka()

    def _write_replays(self):
        for name in ('replay_a', 'replay_b'):
            self.write('reports/implementation/runs/%s/down.log' % name, 'teardown ok\n')
            self.write('reports/implementation/runs/%s/down.exit' % name, '0\n')
            self.write('reports/implementation/runs/%s/up.log' % name,
                       'Generated 18 files; 32 isolated namespaces; epoch=1\n'
                       '# 2/8  config/generate.py\nwrote generated/payouts/arena.toml\n')
            self.write('reports/implementation/runs/%s/up.exit' % name, '0\n')
            self.write('reports/implementation/runs/%s/arena-fingerprint.json' % name,
                       self.fingerprint())
            self.write('reports/implementation/runs/%s/full/junit.xml' % name, self.junit())
            self.write('reports/implementation/runs/%s/full/egress.json' % name, self.egress())
            self.write('reports/implementation/runs/%s/full/arena-fingerprint.json' % name,
                       self.fingerprint())

    def _write_monolith(self):
        route = 'reports/implementation/runs/final/route'
        self.write(route + '/route-results.json', {
            'status': 'passed',
            'cases': [{'name': case, 'status': 'passed', 'route_profile': 'monolith',
                       'payout_id': 'pout_route_' + case,
                       'ending_state': {'layers': layers()}}
                      for case in ROUTE_CASES]})
        for case in ROUTE_CASES:
            self.write(route + '/route-%s.jsonl' % case,
                       json.dumps({'kind': 'starting_fixture', 'at': T}) + '\n' +
                       json.dumps({'kind': 'route_result', 'at': T + 5}) + '\n')
            self.evidence(route, case, 'pout_route_' + case)
        self.write(route + '/egress.json', self.egress())
        self.write(route + '/arena-fingerprint.json', self.fingerprint())

        bank = 'reports/implementation/runs/final/bank'
        self.write(bank + '/bank-effects.json', {
            'status': 'passed', 'passed': True,
            'scenarios': [{'scenario': case, 'status': 'passed', 'passed': True,
                           'route_profile': 'monolith', 'payout_id': 'pout_bank_' + case,
                           'started_at': T, 'finished_at': T + 6,
                           'ending_state': {'layers': layers()}}
                          for case in BANK_CASES]})
        for case in BANK_CASES:
            self.evidence(bank, case, 'pout_bank_' + case)
        self.write(bank + '/egress.json', self.egress())
        self.write(bank + '/arena-fingerprint.json', self.fingerprint())

        golden = 'reports/implementation/runs/final/golden'
        self.write(golden + '/live-result.json', {
            'status': 'passed', 'passed': True, 'route_profile': 'monolith',
            'payout_id': 'pout_golden', 'started_at': T,
            'ending_state': {'layers': layers(), 'finished_at': T + 4}})
        self.evidence(golden, 'golden', 'pout_golden')
        self.write(golden + '/egress.json', self.egress())
        self.write(golden + '/arena-fingerprint.json', self.fingerprint())

    def _write_profiles(self):
        for directory, profile in (('reports/implementation/runs/direct/golden', 'direct'),
                                   ('reports/implementation/runs/mixed/golden',
                                    'direct-create-monolith-status')):
            self.write(directory + '/live-result.json', {
                'status': 'passed', 'passed': True, 'route_profile': profile,
                'payout_id': 'pout_' + profile, 'started_at': T,
                'ending_state': {'layers': layers(), 'finished_at': T + 4}})
            self.evidence(directory, 'golden', 'pout_' + profile, profile=profile)
            self.write(directory + '/egress.json', self.egress())
            self.write(directory + '/arena-fingerprint.json',
                       self.fingerprint(route_profile=profile))

    def _write_kafka(self):
        base = 'reports/implementation/runs/kafka-final'
        self.write(base + '/arena-fingerprint.json', self.fingerprint(route_profile='kafka'))

        shared = base + '/golden-shared'
        self.write(shared + '/live-result.json', {
            'status': 'failed', 'passed': False, 'route_profile': 'kafka',
            'payout_id': 'pout_kafka_shared', 'started_at': T,
            'ending_state': {'layers': layers(payout_status='initiated', fts_status='PROCESSED',
                                              journals=['payout_initiated'],
                                              webhooks=['payout.initiated']),
                             'finished_at': T + 30}})
        self.evidence(shared, 'golden-shared', 'pout_kafka_shared',
                      profile='kafka', consumer=True)
        self.write(shared + '/egress.json', self.egress())

        kafka = base + '/kafka'
        cases = []
        for case in KAFKA_CASES:
            status, fts, journals, webhooks, ref = KAFKA_CASE_STATE[case]
            observed = kafka_observed(status, fts, journals, webhooks)
            cases.append({'name': case, 'status': 'passed' if ref is None else 'failed',
                          'payout_id': 'pout_kafka_' + case,
                          'predicted_state': observed if ref else None,
                          'observed_state': observed,
                          'matches_prediction': ref is not None,
                          'expected_failure_ref': ref})
            self.evidence(kafka, case, 'pout_kafka_' + case, profile='kafka', consumer=True)
        self.write(kafka + '/kafka-results.json',
                   {'status': 'passed', 'started_at': T, 'finished_at': T + 40, 'cases': cases})
        self.write(kafka + '/egress.json', self.egress())

    # ------------------------------------------------------------ manifest

    def manifest(self, **overrides):
        data = {
            'replay_a': 'reports/implementation/runs/replay_a',
            'replay_b': 'reports/implementation/runs/replay_b',
            'final_route': 'reports/implementation/runs/final/route',
            'final_bank': 'reports/implementation/runs/final/bank',
            'final_golden': 'reports/implementation/runs/final/golden',
            'profile_direct_golden': 'reports/implementation/runs/direct/golden',
            'profile_mixed_golden': 'reports/implementation/runs/mixed/golden',
            'profile_kafka': 'reports/implementation/runs/kafka-final',
            'logical_replay': 'reports/implementation/logical-replay-final.json',
            'audit_dir': 'reports/implementation/audits/test-boot',
            'resource_profile': 'reports/implementation/resource-profile.json',
            'build_evidence': '.local/twin-repos/build-evidence-test',
            'explorer_verification': 'reports/implementation/explorer-verification.json',
            'declared_deviations': 'ENV2_COMPOSE/config/declared-deviations.yaml',
            'expected_failures': 'TWIN_SPEC/expected-failures.yaml',
            'fixed_twin_defects': 'reports/implementation/fixed-twin-defects.json',
        }
        data.update(overrides)
        path = self.write('manifest.json', data)
        return path


class GateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        patches = [mock.patch.object(gate_module, 'ROOT', self.root),
                   mock.patch.object(gate_module, 'COMPOSE', self.root / 'ENV2_COMPOSE'),
                   mock.patch.object(gate_module.fingerprint, 'git_head', lambda _root: None)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self._tmp.cleanup)
        self.fixture = Fixture(self.root)

    def run_gate(self, **manifest_overrides):
        manifest = self.fixture.manifest(**manifest_overrides)
        output = self.root / 'out/local-acceptance.json'
        code = gate_module.main([str(manifest), '--output', str(output),
                                 '--summary', str(self.root / 'out/summary.md')])
        return code, json.loads(output.read_text())

    def reasons(self, report, check):
        return report['check_details'][check]['reason'] or ''

    # ---------------------------------------------------------------- pass

    def test_complete_evidence_passes_and_exits_zero(self):
        code, report = self.run_gate()
        self.assertEqual(report['status'], 'passed',
                         json.dumps(report['blockers'], indent=2))
        self.assertEqual(code, 0)
        self.assertEqual(report['schema_version'], 2)
        self.assertTrue(all(report['checks'].values()))
        self.assertEqual(report['unexplained_results'], [])
        self.assertTrue(report['evidence_files'])
        self.assertTrue((self.root / 'out/summary.md').is_file())

    def test_output_is_idempotent_apart_from_recorded_at(self):
        code, first = self.run_gate()
        self.assertEqual(code, 0)
        _, second = self.run_gate()
        first.pop('recorded_at'), second.pop('recorded_at')
        self.assertEqual(first, second)

    def test_evidence_files_are_sorted_and_hashed(self):
        _, report = self.run_gate()
        paths = [e['path'] for e in report['evidence_files']]
        self.assertEqual(paths, sorted(paths))
        for entry in report['evidence_files']:
            self.assertEqual(len(entry['sha256']), 64)
            self.assertTrue((self.root / entry['path']).is_file())

    # ------------------------------------------------------- expected fail

    def test_expected_failure_passes_fidelity_and_fails_product_health(self):
        _, report = self.run_gate()
        shared = report['route_fidelity']['kafka']['golden-shared']
        self.assertTrue(shared['fidelity_pass'])
        self.assertTrue(shared['matched_expected_failure'])
        self.assertEqual(shared['expected_failure_ref'], 'EF-001')
        self.assertEqual(shared['observed_state']['payout_status'], 'initiated')
        self.assertEqual(shared['observed_state']['fts_status'], 'PROCESSED')
        self.assertNotIn('payout_processed', shared['observed_state']['journal_events'])

        health = report['product_route_health']['kafka']['golden-shared']
        self.assertFalse(health['business_completed'])
        self.assertEqual(health['expected_failure_ref'], 'EF-001')

        matched = {e['id']: e['matched'] for e in report['expected_failures']}
        self.assertEqual(matched, {'EF-001': True, 'EF-002': True, 'EF-003': True})
        self.assertTrue(report['checks']['route_fidelity'])
        self.assertTrue(report['checks']['no_unexplained_results'])

    # ---------------------------------------------------------- staleness

    def test_missing_fingerprint_blocks_the_gate(self):
        (self.root / 'reports/implementation/runs/replay_a/arena-fingerprint.json').unlink()
        (self.root / 'reports/implementation/runs/replay_a/full/arena-fingerprint.json').unlink()
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertEqual(report['status'], 'blocked')
        self.assertFalse(report['checks']['staleness'])
        self.assertIn('no arena-fingerprint.json', self.reasons(report, 'staleness'))
        self.assertIn('replay_a', self.reasons(report, 'staleness'))

    def test_mismatched_fingerprint_lists_the_differing_files(self):
        path = 'reports/implementation/runs/final/golden/arena-fingerprint.json'
        stale = self.fixture.read_json(path)
        stale['config_hashes']['config/routes.py'] = '0' * 64
        stale['config_digest'] = gate_module.fingerprint.config_digest(stale['config_hashes'])
        self.fixture.write(path, stale)
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertFalse(report['checks']['staleness'])
        self.assertIn('booted from a different tree', self.reasons(report, 'staleness'))
        mismatched = report['staleness']['mismatched_files']['final_golden']
        self.assertEqual(mismatched['files'], ['config/routes.py'])

    # ------------------------------------------------------------- kafka

    def test_kafka_case_that_does_not_match_its_prediction_is_unexplained(self):
        def mutate(data):
            for case in data['cases']:
                if case['name'] == 'failed_dropped_direct':
                    case['matches_prediction'] = False
        self.fixture.patch_json(
            'reports/implementation/runs/kafka-final/kafka/kafka-results.json', mutate)
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertFalse(report['checks']['no_unexplained_results'])
        self.assertFalse(report['checks']['route_fidelity'])
        unexplained = [u for u in report['unexplained_results']
                       if u['scenario'] == 'failed_dropped_direct']
        self.assertEqual(len(unexplained), 1)
        self.assertEqual(unexplained[0]['profile'], 'kafka')
        self.assertIsNone(unexplained[0]['fixed_defect_ref'])

    def test_missing_consumer_state_blocks_kafka_fidelity(self):
        path = ('reports/implementation/runs/kafka-final/golden-shared/'
                'route-evidence-golden-shared.json')
        self.fixture.patch_json(path, lambda data: data.pop('consumer_state'))
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertFalse(report['checks']['route_fidelity'])
        self.assertIn('consumer_state', self.reasons(report, 'route_fidelity'))

    def test_restarted_consumer_blocks_kafka_fidelity(self):
        path = ('reports/implementation/runs/kafka-final/kafka/'
                'route-evidence-direct_after_shared.json')
        self.fixture.patch_json(
            path, lambda data: data['consumer_state']['containers'][0].update(
                {'running': False, 'restart_count': 2}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['route_fidelity'])
        reason = self.reasons(report, 'route_fidelity')
        self.assertIn('is not running', reason)
        self.assertIn('restarted 2 time(s)', reason)

    def test_missing_kafka_results_is_a_clear_missing_evidence_failure(self):
        (self.root / 'reports/implementation/runs/kafka-final/kafka/'
                     'kafka-results.json').unlink()
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertIn('missing evidence', self.reasons(report, 'route_fidelity'))
        self.assertIn('kafka-results.json', self.reasons(report, 'route_fidelity'))

    # ----------------------------------------------------------- verifier

    def test_a_skipped_verifier_case_fails_the_suite(self):
        self.fixture.write('reports/implementation/runs/replay_b/full/junit.xml',
                           self.fixture.junit(skipped=1, skip_case=True))
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertFalse(report['checks']['verifier_suite'])
        self.assertIn('1 skipped', self.reasons(report, 'verifier_suite'))
        self.assertFalse(report['checks']['no_unexplained_results'])
        self.assertTrue(any(u['scenario'] == 'test_skipped'
                            for u in report['unexplained_results']))

    def test_wrong_verifier_case_count_fails_the_suite(self):
        self.fixture.write('reports/implementation/runs/replay_a/full/junit.xml',
                           self.fixture.junit(tests=25))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['verifier_suite'])
        self.assertIn('25 verifier cases', self.reasons(report, 'verifier_suite'))

    # ------------------------------------------------------------- egress

    def test_a_single_outside_packet_fails_the_egress_check(self):
        self.fixture.patch_json('reports/implementation/runs/final/route/egress.json',
                                lambda data: data.update({'outside_packets': 1,
                                                          'destinations': ['203.0.113.7']}))
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertFalse(report['checks']['egress_same_window'])
        self.assertIn('left the arena subnets', self.reasons(report, 'egress_same_window'))

    def test_dropped_capture_packets_fail_the_egress_check(self):
        self.fixture.patch_json(
            'reports/implementation/runs/final/bank/egress.json',
            lambda data: data.update({'control_capture_stats':
                                      CAPTURE_STATS.replace('0 packets dropped', '9 packets dropped')}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['egress_same_window'])
        self.assertIn('dropped', self.reasons(report, 'egress_same_window'))

    def test_scenario_outside_the_capture_window_fails(self):
        self.fixture.patch_json(
            'reports/implementation/runs/final/golden/egress.json',
            lambda data: data.update({'command_started_at':
                                      dt.datetime.fromtimestamp(T + 60, dt.timezone.utc).isoformat()}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['egress_same_window'])
        self.assertIn('before the capture window opened',
                      self.reasons(report, 'egress_same_window'))

    # -------------------------------------------------------- other gates

    def test_boot_image_ids_must_match_the_build(self):
        path = 'reports/implementation/runs/final/golden/arena-fingerprint.json'
        self.fixture.patch_json(
            path, lambda data: data['images'].update({'rzp-arena/fts:local': 'sha256:other'}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['fresh_build'])
        self.assertIn('booted image ids differ', self.reasons(report, 'fresh_build'))

    def test_unresolved_substitute_image_does_not_block_the_build_check(self):
        path = 'reports/implementation/runs/final/golden/arena-fingerprint.json'
        self.fixture.patch_json(
            path, lambda data: data['images'].update({'rzp-arena/ledger-gate:local': None,
                                                     'rzp-arena/mozart:local': None}))
        code, report = self.run_gate()
        self.assertEqual(code, 0, self.reasons(report, 'fresh_build'))
        self.assertTrue(report['checks']['fresh_build'])

    def test_missing_core_image_id_blocks_the_build_check(self):
        path = 'reports/implementation/runs/final/golden/arena-fingerprint.json'
        self.fixture.patch_json(
            path, lambda data: data['images'].update({'rzp-arena/payouts:local': None}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['fresh_build'])
        self.assertIn('recorded no id for this image', self.reasons(report, 'fresh_build'))

    def test_audit_boot_identity_must_match_a_run(self):
        self.fixture.patch_json(
            'reports/implementation/audits/test-boot/audit-summary.json',
            lambda data: data.update({'initial_service_container_ids': {'payouts-api': 'other'},
                                      'final_service_container_ids': {'payouts-api': 'other'}}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['runtime_endpoint_audit'])
        self.assertFalse(report['checks']['secret_scan'])
        self.assertFalse(report['checks']['staleness'])
        self.assertIn('boot identity', self.reasons(report, 'runtime_endpoint_audit'))

    def test_unresolved_audit_findings_fail_the_secret_scan(self):
        self.fixture.write(
            'reports/implementation/audits/test-boot/logical-store-secret-scan.json',
            {'schema_version': 1, 'status': 'passed',
             'stores': [{'service': 'mysql-payouts', 'unresolved_findings': ['leaked-token']}]})
        _, report = self.run_gate()
        self.assertFalse(report['checks']['secret_scan'])
        self.assertIn('1 unresolved finding', self.reasons(report, 'secret_scan'))

    def test_empty_fixed_twin_defects_blocks_the_gate(self):
        self.fixture.write('reports/implementation/fixed-twin-defects.json',
                           {'schema_version': 1, 'defects': []})
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertFalse(report['checks']['fixed_twin_defects_recorded'])
        self.assertIn('empty', self.reasons(report, 'fixed_twin_defects_recorded'))

    def test_placeholder_fixed_defect_cannot_explain_a_failure(self):
        self.fixture.patch_json('reports/implementation/fixed-twin-defects.json',
                                lambda data: data['defects'][0].update(
                                    {'fix': 'PLACEHOLDER - to be completed'}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['fixed_twin_defects_recorded'])
        self.assertIn('placeholder', self.reasons(report, 'fixed_twin_defects_recorded'))

    def test_missing_declared_deviations_blocks_the_gate(self):
        (self.root / 'ENV2_COMPOSE/config/declared-deviations.yaml').unlink()
        code, report = self.run_gate()
        self.assertEqual(code, 1)
        self.assertFalse(report['checks']['declared_deviations_present'])
        self.assertEqual(report['declared_deviations']['count'], 0)

    def test_declared_deviations_are_embedded(self):
        _, report = self.run_gate()
        self.assertEqual(report['declared_deviations']['count'], 2)
        self.assertEqual([e['id'] for e in report['declared_deviations']['entries']],
                         ['DD-001', 'DD-002'])

    def test_failed_up_exit_fails_the_empty_volume_boot(self):
        self.fixture.write('reports/implementation/runs/replay_b/up.exit', '1\n')
        _, report = self.run_gate()
        self.assertFalse(report['checks']['empty_volume_boot'])
        self.assertIn('up.sh exited 1', self.reasons(report, 'empty_volume_boot'))

    def test_up_log_without_fixture_generation_fails(self):
        self.fixture.write('reports/implementation/runs/replay_a/up.log', 'arena is up\n')
        _, report = self.run_gate()
        self.assertFalse(report['checks']['empty_volume_boot'])
        self.assertIn('no fixture generation', self.reasons(report, 'empty_volume_boot'))

    def test_logical_replay_difference_blocks_the_gate(self):
        self.fixture.patch_json('reports/implementation/logical-replay-final.json',
                                lambda data: data.update({'differences': [{'test': 'v06'}]}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['logical_replay'])
        self.assertIn('1 recorded difference', self.reasons(report, 'logical_replay'))

    def test_unobserved_resource_phase_blocks_the_gate(self):
        self.fixture.patch_json('reports/implementation/resource-profile.json',
                                lambda data: data['phases']['full_verifier'].update(
                                    {'observed': False, 'sample_count': 1}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['resource_profile'])
        self.assertIn('full_verifier', self.reasons(report, 'resource_profile'))

    def test_explorer_needs_both_saved_and_live(self):
        self.fixture.patch_json('reports/implementation/explorer-verification.json',
                                lambda data: data['live_server'].update({'payout_visible': False}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['explorer_saved_and_live'])
        self.assertIn('live_server', self.reasons(report, 'explorer_saved_and_live'))

    def test_route_evidence_for_the_wrong_payout_is_refused(self):
        route = 'reports/implementation/runs/final/route'
        (self.root / route / 'route-evidence-queued.json').unlink()
        self.fixture.write(route + '/route-evidence.json',
                           {'schema_version': 1, 'profile': 'monolith',
                            'payout_id': 'pout_someone_else', 'transport_observed': True})
        _, report = self.run_gate()
        self.assertFalse(report['checks']['route_fidelity'])
        self.assertIn('records payout pout_someone_else',
                      self.reasons(report, 'route_fidelity'))

    def test_transport_not_observed_fails_fidelity_even_when_the_case_passed(self):
        self.fixture.patch_json(
            'reports/implementation/runs/direct/golden/route-evidence-golden.json',
            lambda data: data.update({'transport_observed': False,
                                      'required': ['direct_status_http'],
                                      'observed': {'direct_status_http': False}}))
        _, report = self.run_gate()
        self.assertFalse(report['checks']['route_fidelity'])
        self.assertIn('transport not observed', self.reasons(report, 'route_fidelity'))
        self.assertTrue(
            report['product_route_health']['direct']['golden']['business_completed'])

    # --------------------------------------------------------- manifest IO

    def test_a_missing_manifest_exits_two(self):
        code = gate_module.main([str(self.root / 'nope.json'),
                                 '--output', str(self.root / 'out.json')])
        self.assertEqual(code, 2)

    def test_a_manifest_that_is_not_an_object_exits_two(self):
        path = self.fixture.write('bad-manifest.json', '[1, 2, 3]')
        code = gate_module.main([str(path), '--output', str(self.root / 'out.json')])
        self.assertEqual(code, 2)

    def test_a_role_outside_the_checkout_exits_two(self):
        path = self.fixture.manifest(resource_profile='../../etc/passwd')
        code = gate_module.main([str(path), '--output', str(self.root / 'out.json')])
        self.assertEqual(code, 2)

    def test_absent_role_is_a_missing_evidence_failure_not_a_crash(self):
        data = copy.deepcopy(json.loads(self.fixture.manifest().read_text()))
        data.pop('profile_kafka')
        path = self.fixture.write('manifest.json', data)
        code = gate_module.main([str(path), '--output', str(self.root / 'out/gate.json'),
                                 '--summary', str(self.root / 'out/gate.md')])
        report = json.loads((self.root / 'out/gate.json').read_text())
        self.assertEqual(code, 1)
        self.assertIn('missing evidence', self.reasons(report, 'route_fidelity'))
        self.assertIn('role absent from manifest', self.reasons(report, 'staleness'))
        self.assertIn('profile_kafka', self.reasons(report, 'staleness'))


if __name__ == '__main__':
    unittest.main()
