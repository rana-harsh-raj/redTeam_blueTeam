"""Isolated audit lifecycle tests: no Docker, network, or real signals are used."""
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[2] / 'network' / 'egress_audit.py'
spec = importlib.util.spec_from_file_location('egress_audit', MODULE_PATH)
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class FakeProcess:
    def __init__(self, fixture):
        self.fixture = fixture
        self.done = False

    def poll(self):
        return self.fixture.command_code if self.done else None

    def wait(self, timeout=None):
        self.fixture.events.append('command_wait')
        if self.fixture.command_timeout and not self.done:
            raise subprocess.TimeoutExpired(['fake-command'], timeout)
        self.done = True
        return self.fixture.command_code

    def terminate(self):
        self.fixture.events.append('command_terminate')
        self.done = True

    def kill(self):
        self.fixture.events.append('command_kill')
        self.done = True


class AuditLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'evidence'
        self.events = []
        self.calls = []
        self.command_code = 0
        self.command_timeout = False
        self.control_count = 12
        self.alive = True
        self.failed_cleanup = None
        self.fail_analysis = False
        self.signal_on_cleanup = False
        self.handlers = {}
        self.remaining = set()
        self.launch_timeout = False
        self.cleanup_listing_fails = False
        self.remove_returns_missing = False

    def fake_signal(self, signum, handler):
        old = self.handlers.get(signum, signal.SIG_DFL)
        self.handlers[signum] = handler
        return old

    def fake_popen(self, *args, **kwargs):
        self.events.append('command_start')
        return FakeProcess(self)

    def fake_run(self, args, **kwargs):
        self.calls.append(args)
        code, stdout = 0, ''
        if args[1:3] == ['network', 'inspect']:
            stdout = json.dumps([{'Internal': True, 'EnableIPv6': False,
                                 'IPAM': {'Config': [{'Subnet': '172.24.0.0/16'}]}}])
        elif args[1:3] == ['run', '-d']:
            self.events.append('capture_start')
            if self.launch_timeout:
                raise subprocess.TimeoutExpired(args, 60)
        elif args[1] == 'exec':
            self.events.append('capture_ready')
        elif args[1] == 'inspect':
            self.events.append('capture_check')
            stdout = 'true' if self.alive else 'false'
        elif args[1] == 'stop':
            self.events.append('capture_stop')
        elif args[1] == 'run':
            self.events.append('analysis')
            if self.fail_analysis:
                raise subprocess.CalledProcessError(1, args)
            if 'tshark' in args:
                stdout = ''
            elif 'cat' in args:
                stdout = '12 packets captured\n0 packets dropped by kernel\n'
            else:
                stdout = str(self.control_count)
        elif args[1:3] == ['rm', '-f']:
            self.events.append('remove_' + ('command' if args[-1] == 'fake-verifier' else 'capture'))
            if self.signal_on_cleanup:
                self.signal_on_cleanup = False
                self.handlers[signal.SIGINT](signal.SIGINT, None)
            if self.failed_cleanup == args[-1] or (self.failed_cleanup == 'analyzers' and '-analyze-' in args[-1]):
                code = 1
                self.remaining.add(args[-1])
            elif self.remove_returns_missing:
                code = 1
        elif args[1:3] == ['volume', 'rm']:
            self.events.append('remove_volume')
        elif args[1:3] in (['container', 'ls'], ['volume', 'ls']):
            if self.cleanup_listing_fails:
                code = 1
            elif any(resource in args[-1].replace('\\', '') for resource in self.remaining):
                stdout = next(iter(self.remaining))
        return subprocess.CompletedProcess(args, code, stdout=stdout, stderr='')

    def execute(self):
        with patch.object(audit_module, 'ROOT', self.root), \
             patch.object(audit_module.subprocess, 'run', self.fake_run), \
             patch.object(audit_module.subprocess, 'Popen', self.fake_popen), \
             patch.object(audit_module.signal, 'signal', self.fake_signal):
            code = audit_module.audit(['fake-command'], self.output, 60, 'fake-verifier')
        return code, json.loads((self.output / 'egress.json').read_text())

    def test_capture_covers_complete_command_and_cleans_up(self):
        code, evidence = self.execute()
        self.assertEqual(code, 0)
        self.assertEqual(evidence['status'], 'passed')
        self.assertLess(self.events.index('capture_ready'), self.events.index('command_start'))
        self.assertLess(self.events.index('command_wait'), self.events.index('capture_check'))
        self.assertLess(self.events.index('capture_check'), self.events.index('capture_stop'))
        self.assertEqual(self.events[-3:], ['remove_command', 'remove_capture', 'remove_volume'])

    def test_failed_verifier_exit_is_propagated(self):
        self.command_code = 9
        code, evidence = self.execute()
        self.assertEqual(code, 9)
        self.assertEqual(evidence['command_exit_code'], 9)

    def test_analysis_failure_is_closed_and_cleans_up(self):
        self.fail_analysis = True
        code, evidence = self.execute()
        self.assertEqual(code, 1)
        self.assertEqual(evidence['status'], 'failed')
        self.assertEqual(self.events[-3:], ['remove_command', 'remove_capture', 'remove_volume'])

    def test_missing_control_traffic_is_not_passed(self):
        self.control_count = 0
        code, evidence = self.execute()
        self.assertEqual(code, 1)
        self.assertEqual(evidence['status'], 'failed')

    def test_capture_dying_before_command_finish_is_not_passed(self):
        self.alive = False
        code, evidence = self.execute()
        self.assertEqual(code, 1)
        self.assertFalse(evidence['capture_alive_after_command'])

    def test_timeout_terminates_command_and_cleans_up(self):
        self.command_timeout = True
        code, evidence = self.execute()
        self.assertEqual(code, 1)
        self.assertIn('command_terminate', self.events)
        self.assertEqual(evidence['status'], 'failed')
        self.assertEqual(self.events[-3:], ['remove_command', 'remove_capture', 'remove_volume'])

    def test_verifier_cleanup_failure_cannot_report_success(self):
        self.failed_cleanup = 'fake-verifier'
        code, evidence = self.execute()
        self.assertNotEqual(code, 0)
        self.assertEqual(evidence['status'], 'failed')

    def test_analysis_containers_have_names_for_interrupt_cleanup(self):
        self.execute()
        for args in self.calls:
            if args[1] == 'run' and '-d' not in args:
                self.assertIn('--name', args)

    def test_signal_during_cleanup_still_removes_resources_and_writes_evidence(self):
        self.signal_on_cleanup = True
        try:
            code, evidence = self.execute()
        except KeyboardInterrupt:
            self.fail('Signal escaped cleanup before resources were removed and evidence written')
        self.assertNotEqual(code, 0)
        self.assertIn('remove_volume', self.events)
        self.assertEqual(evidence['status'], 'failed')

    def test_uncertain_capture_launch_still_removes_registered_container(self):
        self.launch_timeout = True
        code, evidence = self.execute()
        self.assertEqual(code, 1)
        self.assertIn('remove_capture', self.events)
        self.assertIn('remove_volume', self.events)

    def test_failed_cleanup_listing_is_not_treated_as_absence(self):
        self.cleanup_listing_fails = True
        code, evidence = self.execute()
        self.assertEqual(code, 1)
        self.assertFalse(evidence['command_container_removed'])

    def test_already_removed_containers_are_accepted_only_after_absence_check(self):
        self.remove_returns_missing = True
        code, evidence = self.execute()
        self.assertEqual(code, 0)
        self.assertTrue(evidence['command_container_removed'])

    def test_analyzer_cleanup_failure_is_not_ignored(self):
        self.failed_cleanup = 'analyzers'
        code, evidence = self.execute()
        self.assertEqual(code, 1)
        self.assertFalse(evidence['analyzer_containers_removed'])


if __name__ == '__main__':
    unittest.main()
