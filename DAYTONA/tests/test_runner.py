"""Behavioral checks for cost/privacy gates and failure cleanup; never use a real SDK."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

spec = importlib.util.spec_from_file_location('daytona_runner', Path(__file__).parents[1] / 'scripts/daytona_runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class FakeRun:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []
        self.state = {}

    def operation(self, name):
        self.calls.append(name)
        if name == self.failure:
            raise RuntimeError(name + ' failed')

    def create(self):
        self.state = {'sandbox_name': 'fake-owned-name'}
        self.operation('create')

    def upload(self):
        self.operation('upload')

    def test(self, profile):
        self.operation(profile)

    def export(self):
        self.operation('export')

    def cleanup(self):
        self.operation('cleanup')

    def save(self):
        pass


class RunnerTests(unittest.TestCase):
    def test_no_approval_makes_no_sdk_or_api_calls(self):
        for action in ('run', 'create', 'upload', 'smoke', 'full', 'export', 'cleanup'):
            with self.subTest(action=action), patch.dict(os.environ, {}, clear=True), \
                 patch('sys.argv', ['runner', action]), patch.object(runner, 'load_sdk') as sdk, \
                 patch.object(runner.urllib.request, 'urlopen') as api, contextlib.redirect_stdout(io.StringIO()) as output:
                runner.main()
                self.assertEqual(json.loads(output.getvalue())['mode'], 'dry-run')
                sdk.assert_not_called()
                api.assert_not_called()

    def test_approval_alone_does_not_enable_execution(self):
        with patch.dict(os.environ, {'DAYTONA_APPROVED': '1'}), \
             patch('sys.argv', ['runner', 'run', '--plan', '/nonexistent/twin-plan.json']), \
             patch.object(runner, 'load_sdk') as sdk:
            with self.assertRaises(FileNotFoundError):
                runner.main()
            sdk.assert_not_called()

    def test_all_workflow_failures_export_then_cleanup(self):
        for failure in ('create', 'upload', 'smoke', 'full', 'export'):
            with self.subTest(failure=failure), patch.object(runner.signal, 'signal'):
                fake = FakeRun(failure)
                with self.assertRaises(RuntimeError):
                    runner.run_managed(fake, 'full')
                self.assertEqual(fake.calls[-2:], ['export', 'cleanup'])
                if failure == 'smoke':
                    self.assertNotIn('full', fake.calls)

    def test_interrupt_also_exports_then_deletes(self):
        fake = FakeRun()
        def interrupted_upload():
            raise KeyboardInterrupt()
        fake.upload = interrupted_upload
        with patch.object(runner.signal, 'signal'), self.assertRaises(KeyboardInterrupt):
            runner.run_managed(fake, 'smoke')
        self.assertEqual(fake.calls[-2:], ['export', 'cleanup'])

    def test_cleanup_error_is_visible(self):
        with patch.object(runner.signal, 'signal'):
            fake = FakeRun('cleanup')
            with self.assertRaisesRegex(RuntimeError, 'Cleanup unverified'):
                runner.run_managed(fake, 'smoke')

    def test_successful_full_starts_with_smoke(self):
        with patch.object(runner.signal, 'signal'):
            fake = FakeRun()
            runner.run_managed(fake, 'full')
            self.assertEqual(fake.calls, ['create', 'upload', 'smoke', 'full', 'export', 'cleanup'])

    def test_only_http_404_proves_absence(self):
        for code in (404, 401, 403, 429, 500):
            with self.subTest(code=code), patch.dict(os.environ, {'DAYTONA_API_KEY': 'synthetic-controller-key'}), \
                 patch.object(runner.urllib.request, 'urlopen', side_effect=urllib.error.HTTPError('https://example.invalid', code, 'test', {}, None)):
                if code == 404:
                    self.assertFalse(runner.exists_via_api('fake-owned-id'))
                else:
                    with self.assertRaises(RuntimeError):
                        runner.exists_via_api('fake-owned-id')

    def passing_gate(self, directory, **overrides):
        """A minimal schema-2 gate as ENV2_COMPOSE/scripts/local-acceptance.py emits it."""
        evidence = Path(directory) / 'evidence.md'
        evidence.write_text('observed results')
        data = {'schema_version': runner.ACCEPTANCE_SCHEMA_VERSION, 'status': 'passed',
                'checks': {k: True for k in runner.CHECKS},
                'unexplained_results': [],
                'evidence_files': [{'path': 'evidence.md', 'sha256': runner.digest(evidence)}]}
        data.update(overrides)
        gate = Path(directory) / 'gate.json'
        runner.write(gate, data)
        return gate, evidence

    def test_evidence_tampering_blocks_acceptance(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'ROOT', Path(directory).resolve()):
            gate, evidence = self.passing_gate(directory)
            runner.acceptance(gate)
            evidence.write_text('changed results')
            with self.assertRaisesRegex(RuntimeError, 'changed'):
                runner.acceptance(gate)

    def test_hand_authored_schema_one_gate_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'ROOT', Path(directory).resolve()):
            gate, _ = self.passing_gate(directory, schema_version=1)
            with self.assertRaisesRegex(RuntimeError, 'schema_version 2'):
                runner.acceptance(gate)

    def test_every_generated_check_must_be_true(self):
        for name in runner.CHECKS:
            with self.subTest(check=name), tempfile.TemporaryDirectory() as directory, \
                 patch.object(runner, 'ROOT', Path(directory).resolve()):
                checks = {k: True for k in runner.CHECKS}
                checks[name] = False
                gate, _ = self.passing_gate(directory, checks=checks)
                with self.assertRaisesRegex(RuntimeError, 'missing: ' + name):
                    runner.acceptance(gate)

    def test_route_fidelity_and_no_unexplained_results_replace_scenario_assertions(self):
        self.assertIn('route_fidelity', runner.CHECKS)
        self.assertIn('no_unexplained_results', runner.CHECKS)
        self.assertIn('staleness', runner.CHECKS)
        self.assertNotIn('scenario_assertions', runner.CHECKS)

    def test_an_unexplained_result_blocks_acceptance(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'ROOT', Path(directory).resolve()):
            gate, _ = self.passing_gate(directory, unexplained_results=[
                {'profile': 'kafka', 'scenario': 'direct_after_shared', 'status': 'timeout',
                 'fixed_defect_ref': None}])
            with self.assertRaisesRegex(RuntimeError, 'unexplained results'):
                runner.acceptance(gate)

    def test_a_result_attributed_to_a_fixed_twin_defect_does_not_block(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner, 'ROOT', Path(directory).resolve()):
            gate, _ = self.passing_gate(directory, unexplained_results=[
                {'profile': 'kafka', 'scenario': 'direct_after_shared', 'status': 'timeout',
                 'fixed_defect_ref': 'FD-001'}])
            self.assertEqual(runner.acceptance(gate)['status'], 'passed')

    def test_unmeasured_profile_cannot_allocate(self):
        with self.assertRaisesRegex(RuntimeError, 'Missing measured'):
            runner.resource_selection({'schema_version': 1, 'phases': {}})

    def measured_profile(self,root):
        profile={'schema_version':1,'updated_at':'2026-01-01T00:00:00Z','phases':{
            phase:{'observed':True,'sample_count':2,'peak_cpu_cores':2,'peak_memory_gib':4,'disk_gib':8}
            for phase in ('idle','successful_payout','full_verifier')}}
        path=root/'reports/implementation/resource-profile.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(profile))
        return path

    def test_unapproved_dry_run_estimates_fixed_workspace_profile_without_plan(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(runner,'ROOT',Path(directory).resolve()), \
             patch.dict(os.environ,{},clear=True),patch('sys.argv',['runner','run']), \
             patch.object(runner,'load_sdk') as sdk,patch.object(runner.urllib.request,'urlopen') as api, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            profile=self.measured_profile(Path(directory))
            runner.main()
            result=json.loads(output.getvalue())
            self.assertEqual(result['resource_estimate'],{'cpu':3,'memory_gib':7,'disk_gib':20})
            self.assertEqual(result['resource_estimate_source']['sha256'],runner.digest(profile))
            self.assertFalse(result['resource_estimate_source']['execution_authorized'])
            self.assertFalse(result['sandbox_created']);self.assertEqual(result['remote_api_calls'],0)
            self.assertTrue(result['blockers']);self.assertNotIn('provisional_planning_envelope',result)
            sdk.assert_not_called();api.assert_not_called()

    def test_unvalidated_plan_cannot_redirect_resource_estimate(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(runner,'ROOT',Path(directory).resolve()), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            root=Path(directory);self.measured_profile(root)
            (root/'acceptance.json').write_text(json.dumps(
                {'schema_version':runner.ACCEPTANCE_SCHEMA_VERSION,'status':'pending'}))
            plan=root/'plan.json';plan.write_text(json.dumps({'local_acceptance':'acceptance.json','resource_profile':'../../unapproved-private-file.json'}))
            with patch.object(runner,'read',wraps=runner.read) as reads:runner.dry_run('run',plan)
            result=json.loads(output.getvalue())
            self.assertEqual(result['resource_estimate'],{'cpu':3,'memory_gib':7,'disk_gib':20})
            self.assertEqual(result['resource_estimate_source']['profile'],'reports/implementation/resource-profile.json')
            self.assertIn('Local acceptance has not passed',result['blockers'])
            self.assertFalse(any('unapproved-private-file' in str(call.args[0]) for call in reads.call_args_list))

    def test_unobserved_workspace_phase_does_not_produce_measured_estimate(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(runner,'ROOT',Path(directory).resolve()), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            root=Path(directory);path=self.measured_profile(root)
            profile=json.loads(path.read_text());profile['phases']['full_verifier']['observed']=False;path.write_text(json.dumps(profile))
            runner.dry_run('run',root/'missing-plan.json')
            result=json.loads(output.getvalue())
            self.assertIsNone(result['resource_estimate'])
            self.assertIn('provisional_planning_envelope',result)
            self.assertTrue(any('Missing measured resource phase: full_verifier' in message for message in result['blockers']))


if __name__ == '__main__':
    unittest.main()
