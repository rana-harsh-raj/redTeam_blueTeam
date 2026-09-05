"""Offline checks for audit boundaries; no Docker or application calls."""
import json
from pathlib import Path
import re
import tempfile
import types
import unittest
from unittest import mock
import common


class AuditBoundaries(unittest.TestCase):
    def test_diagnostic_url_does_not_allow_other_host_on_same_line(self):
        patterns = [('third_party_host', re.compile(r'https?://[a-z0-9.-]+\.(?:com|org)\b'))]
        result = common.log_endpoints(
            'overcommit: https://github.com/redis/redis/issues/12397 https://unexpected.example.com',
            'redis', patterns)
        self.assertEqual(result['raw_pattern_counts'], {'third_party_host': 2})
        self.assertEqual(result['unresolved_pattern_counts'], {'third_party_host': 1})
        self.assertEqual(len(result['reviewed_diagnostic_matches']), 1)
        untrusted_context = common.log_endpoints('https://dochub.mongodb.org/core/numa', 'mongo-cfa', patterns)
        self.assertEqual(untrusted_context['unresolved_pattern_counts'], {'third_party_host': 1})
        jemalloc = common.log_endpoints(
            'Memory overcommit: https://github.com/jemalloc/jemalloc/issues/1328 https://unexpected.example.com',
            'redis', patterns)
        self.assertEqual(jemalloc['unresolved_pattern_counts'], {'third_party_host': 1})
        different_issue = common.log_endpoints(
            'overcommit: https://github.com/jemalloc/jemalloc/issues/1329', 'redis', patterns)
        self.assertEqual(different_issue['unresolved_pattern_counts'], {'third_party_host': 1})

    def test_machinery_task_id_requires_persisted_key_relationship(self):
        identifier = '00000000-1111-2222-3333-444444444444'
        state = {'TaskUUID': identifier, 'TaskName': 'arena-task', 'State': 'SUCCESS',
                 'CreatedAt': '2026-09-05T00:00:00Z', 'Results': None, 'Error': ''}
        valid = {'type': 'string', 'key': identifier, 'value': json.dumps(state)}
        cleaned, proof = common.classify_machinery_task_ids(json.dumps([valid]).encode())
        self.assertEqual(proof['verified_task_identifier_count'], 1)
        self.assertNotIn(identifier.encode(), cleaned)
        invalid = dict(valid, key='unrelated-key')
        cleaned, proof = common.classify_machinery_task_ids(json.dumps([invalid]).encode())
        self.assertEqual(proof['verified_task_identifier_count'], 0)
        self.assertIn(identifier.encode(), cleaned)

    def test_machinery_classification_keeps_unknown_candidate(self):
        identifier = '00000000-1111-2222-3333-444444444444'
        unknown = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
        state = {'TaskUUID': identifier, 'TaskName': 'arena-task', 'State': 'SUCCESS',
                 'CreatedAt': '2026-09-05T00:00:00Z', 'Results': unknown, 'Error': ''}
        records = [{'type': 'string', 'key': identifier, 'value': json.dumps(state)}]
        cleaned, proof = common.classify_machinery_task_ids(json.dumps(records).encode())
        self.assertEqual(proof['verified_task_identifier_count'], 1)
        self.assertIn(unknown.encode(), cleaned)

    def test_workspace_mismatch_blocks_inspection(self):
        rows = [{'Config': {'Labels': {'com.docker.compose.project.working_dir': '/different/workspace'}}}]
        with mock.patch.object(common, 'run', side_effect=[b'container-id', json.dumps(rows).encode()]):
            with self.assertRaisesRegex(RuntimeError, 'different workspace'):
                common.current_containers('env2_compose')

    def test_known_redaction_preserves_novel_candidate(self):
        cleaned, count = common.clean_known(b'known-arena-value novel-unknown-value known-arena-value', {b'known-arena-value'})
        self.assertEqual(count, 2)
        self.assertNotIn(b'known-arena-value', cleaned)
        self.assertIn(b'novel-unknown-value', cleaned)

    def test_missing_scanner_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            files = directory / 'files'
            files.mkdir()
            with mock.patch.object(common.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
                scan = common.scan_directory(files, directory)
            self.assertEqual(scan['exit_code'], 2)
            self.assertIn('required report', scan['error'])


if __name__ == '__main__':
    unittest.main()
