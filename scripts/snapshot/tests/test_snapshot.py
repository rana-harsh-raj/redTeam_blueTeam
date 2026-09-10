#!/usr/bin/env python3
"""Tests for the M6 snapshot / refresh tooling.

  python3 -m unittest discover -s scripts/snapshot/tests

Most tests run against a synthetic mini-twin built in a temp directory
(`_build_fixture_twin`) so they are fast, hermetic and independent of what the
real arena happens to contain; the functional graph comes from
scripts/snapshot/tests/fixtures/graph.json (shaped exactly like
reports/domain/SCHEMA.md). One test additionally captures the REAL repository
without docker, because "capture must run without docker" is a property of the
real tree, not of a fixture.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SNAPSHOT_DIR = HERE.parent
ROOT = SNAPSHOT_DIR.parents[1]
sys.path.insert(0, str(SNAPSHOT_DIR))

import common as C            # noqa: E402
import capture as CAP         # noqa: E402
import diff as D              # noqa: E402
import affected as A          # noqa: E402
import recipes as R           # noqa: E402
import refresh as RF          # noqa: E402

FIXTURE_GRAPH = HERE / 'fixtures/graph.json'

COMPOSE = """\
services:
  payouts-api:
    image: rzp-arena/payouts:${ARENA_TAG:-local}
    profiles: [core]
    entrypoint: ["/app/payouts-api"]
    environment:
      APP_ENV: arena
      ARENA_DCS_URL: http://dcs-stub:9000
    depends_on:
      mysql-payouts:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O /dev/null http://127.0.0.1:9400/status"]
      interval: 5s
      retries: 20
  payouts-migrate:
    image: rzp-arena/payouts:${ARENA_TAG:-local}
    profiles: [migrations]
    entrypoint: ["/app/payouts-migration"]
    command: ["up"]
  payouts-worker-queued-payout:
    image: rzp-arena/payouts:${ARENA_TAG:-local}
    profiles: [core]
    entrypoint: ["/app/payouts-workers"]
  ledger-api:
    image: rzp-arena/ledger:${ARENA_TAG:-local}
    profiles: [core]
    entrypoint: ["/app/ledger-api"]
  kong-lite:
    image: rzp-arena/kong-lite:${ARENA_TAG:-local}
    profiles: [substitutes]
    build:
      context: ./substitutes
      dockerfile: Dockerfile
      args:
        STUB_DIR: kong-lite
        STUB_PORT: "8080"
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/_arena/health"]
      interval: 5s
  dcs-stub:
    image: rzp-arena/dcs-stub:${ARENA_TAG:-local}
    profiles: [substitutes]
    build:
      context: ./substitutes
      dockerfile: Dockerfile
      args:
        STUB_DIR: dcs-stub
  mysql-payouts:
    image: mysql:8.0.36
    profiles: [core]
    healthcheck:
      test: ["CMD-SHELL", "mysqladmin ping -h 127.0.0.1"]
"""

ARENA_YAML = """\
services:
  dcs:
    host: dcs-stub
    port: 9000
"""

BUILD_HOST = """\
#!/usr/bin/env bash
build_go() { :; }
package_runtime() { :; }
build_payouts() {
  build_go "$REPOS_ROOT/payouts" payouts-api ./cmd/api boot
  build_go "$REPOS_ROOT/payouts" payouts-workers ./cmd/workers boot
  package_runtime payouts payouts-api payouts-workers
}
build_ledger() {
  build_go "$REPOS_ROOT/ledger" ledger-api ./cmd/api
  package_runtime ledger ledger-api
}
"""

M4_MATRIX = """\
component,layer,fidelity_label,evidence_pointer,production_reachability,deviation_ids
payouts-api (PS core v1-candidate),app,real,fixture-evidence.json,unknown,
kong-lite (hardened edge),edge,substitute,fixture-boundary.json,unknown,
"""

DCS_SEED = {'merchants': {'10000000000000': {'dcs': {'rzp/x/payouts': {'enable_x': True, 'below_rupee': False}}}}}
SPLITZ_EXP = {'experiments': {'exp_1': {'name': 'payout_workflows', 'variant': 'on'}}}
SPLITZ_VT = {'payout_workflows': {'10000000000000': 'on'}}


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _git_repo(path: Path, message: str = 'fixture') -> str | None:
    """Make `path` a one-commit git repo; returns the sha (None if git is unusable)."""
    env = dict(os.environ, GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_SYSTEM='/dev/null',
               GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
    try:
        for args in (['init', '-q', '-b', 'main'], ['add', '-A'], ['commit', '-q', '-m', message]):
            r = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, env=env)
            if r.returncode != 0:
                return None
        r = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], capture_output=True, text=True, env=env)
        return r.stdout.strip() or None
    except OSError:
        return None


def _build_fixture_twin(base: Path) -> Path:
    """A miniature twin checkout: clones + accepted copies + ENV2_COMPOSE + graph."""
    root = base / 'twin'
    clones = base / 'clones'
    env2 = root / 'ENV2_COMPOSE'

    # --- pristine clones -----------------------------------------------------
    for repo in ('payouts', 'ledger'):
        _write(clones / repo / 'go.mod', 'module github.com/razorpay/%s\n' % repo)
        _write(clones / repo / 'cmd/api/main.go', 'package main\n')
        _write(clones / repo / 'scripts/proto_modules', '# modules\nstork/webhook/v1\n')
        _write(clones / repo / 'internal/database/migrations/20200101000000_init.go', '// %s init\n' % repo)
        _git_repo(clones / repo)
    _write(clones / 'proto' / 'stork/webhook/v1/webhook.proto', 'syntax = "proto3";\n')
    _git_repo(clones / 'proto')
    _write(clones / 'batch' / 'src/main/resources/payout_types.json', '{"type": "payout"}\n')
    _write(clones / 'batch' / 'build.gradle', 'plugins {}\n')
    _git_repo(clones / 'batch')
    _write(clones / 'workflows' / 'go.mod', 'module github.com/razorpay/workflows\n')
    _write(clones / 'workflows' / 'internal/database/migrations/20200101000000_wf.go', '// wf\n')
    _git_repo(clones / 'workflows')

    # --- admitted build copies ----------------------------------------------
    accepted = root / '.local/twin-repos/accepted'
    for repo in ('payouts', 'ledger'):
        _write(accepted / repo / 'internal/database/migrations/20200101000000_init.go', '// %s init\n' % repo)
        _write(accepted / repo / 'scripts/proto_modules', '# modules\nstork/webhook/v1\n')
    _write(root / '.local/repos-root', str(clones) + '\n')
    _write(root / '.local/twin-repos/build-evidence-20260101T000000Z/provenance.json', json.dumps({
        'schema_version': 1, 'destination': str(accepted), 'status': 'prepared',
        'repositories': [{'name': 'payouts', 'source_commit': 'a' * 40, 'patched_copy_manifest': {'files': []}},
                         {'name': 'ledger', 'source_commit': 'b' * 40, 'patched_copy_manifest': {'files': []}}],
    }))

    # --- ENV2_COMPOSE --------------------------------------------------------
    _write(env2 / 'docker-compose.yml', COMPOSE)
    _write(env2 / 'config/arena.yaml', ARENA_YAML)
    _write(env2 / 'config/generate.py', '# fixture generator\n')
    _write(env2 / 'config/routes.py', '# fixture routes\n')
    _write(env2 / 'config/templates/base/payouts/arena.toml', 'dcs_url = "http://{{SVC.dcs}}:9000"\n')
    _write(env2 / 'config/templates/base/ledger/arena.toml', 'listen = ":8080"\n')
    _write(env2 / 'build/build-host.sh', BUILD_HOST)
    for sub in ('kong-lite', 'dcs-stub'):
        _write(env2 / 'substitutes' / sub / 'CONTRACT.md', '# %s contract\n' % sub)
        _write(env2 / 'substitutes' / sub / 'server.py', 'print("%s")\n' % sub)
    _write(env2 / 'seeds/dcs/merchants.json', json.dumps(DCS_SEED, indent=2))
    _write(env2 / 'seeds/splitz/experiments.json', json.dumps(SPLITZ_EXP, indent=2))
    _write(env2 / 'seeds/splitz/variant_table.json', json.dumps(SPLITZ_VT, indent=2))
    _write(env2 / 'seeds/schema-patches/payouts.sql', 'SELECT 1;\n')
    # secrets + generated trees exist so the tests can prove they are never read
    _write(env2 / 'secrets/mysql_root.txt', 'SUPER-SECRET-VALUE-DO-NOT-CAPTURE\n')
    _write(env2 / 'config/generated/payouts/arena.toml', 'password = "SECRET-RENDERED-VALUE"\n')
    _write(env2 / 'seeds/generated/merchants.json', '{"key": "SECRET-SEEDED-VALUE"}\n')
    _write(env2 / '.env.arena', 'ARENA_TAG=fixture-tag\n')
    # real fingerprint.py so config_hashes() is exercised, not stubbed
    (env2 / 'scripts').mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'ENV2_COMPOSE/scripts/fingerprint.py', env2 / 'scripts/fingerprint.py')

    # --- reports -------------------------------------------------------------
    shutil.copy2(FIXTURE_GRAPH, _write(root / 'reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json', '{}'))
    _write(root / 'reports/implementation/m4-fidelity-matrix.csv', M4_MATRIX)
    return root


class FixtureTwinCase(unittest.TestCase):
    """Shared mini-twin: built once, captured once (both are pure filesystem work)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix='m6-snapshot-test-')
        cls.root = _build_fixture_twin(Path(cls._tmp.name))
        cls.graph_path = cls.root / 'reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json'
        cls.ctx = R.build_context(root=cls.root)
        cls.snapshot = CAP.build_snapshot(root=cls.root, graph_path=cls.graph_path, with_docker=False, ctx=cls.ctx)
        cls.recipes = R.all_recipes(cls.ctx)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


# --------------------------------------------------------------------------- 1. capture
class TestCaptureWithoutDocker(FixtureTwinCase):
    def test_snapshot_shape(self):
        snap = self.snapshot
        self.assertEqual(snap['schema_version'], CAP.SNAPSHOT_SCHEMA_VERSION)
        for key in ('repos', 'recipes', 'config', 'schemas', 'contracts', 'flags', 'images', 'compose', 'graph'):
            self.assertIn(key, snap)
        self.assertTrue(snap['digest'])

    def test_no_docker_records_reason_and_null_images(self):
        self.assertEqual(self.snapshot['images_reason'], 'skipped: --no-docker')
        self.assertTrue(all(v is None for v in self.snapshot['images'].values()))

    def test_repos_pinned_with_paths_and_shas(self):
        repos = self.snapshot['repos']
        for name in ('payouts', 'ledger', 'proto', 'batch', 'workflows'):
            self.assertIn(name, repos, name)
            self.assertIn('path', repos[name])
            self.assertIn('head_date', repos[name])
        self.assertTrue(repos['payouts']['sha'], 'clone sha must be pinned')
        self.assertEqual(repos['payouts']['kind'], 'clone+accepted')
        self.assertEqual(repos['batch']['kind'], 'clone')

    def test_schema_contract_flag_and_config_sections(self):
        snap = self.snapshot
        self.assertIn('payouts', snap['schemas'])
        self.assertEqual(snap['schemas']['payouts']['count'], 1)
        self.assertTrue(snap['contracts']['proto']['payouts']['files'], 'proto files must be hashed')
        self.assertIn('kong-lite', snap['contracts']['substitutes'])
        self.assertTrue(snap['contracts']['batch_types'], 'batch payout*.json must be hashed')
        self.assertIn('dcs/rzp/x/payouts/enable_x', snap['flags']['dcs']['items'])
        self.assertIn('splitz/payout_workflows', snap['flags']['splitz']['items'])
        self.assertIn('docker-compose.yml', snap['config']['hashes'])
        self.assertTrue(snap['config']['digest'])
        self.assertEqual(sorted(snap['compose']['services'])[:2], ['dcs-stub', 'kong-lite'])

    def test_graph_version_and_ids(self):
        graph = self.snapshot['graph']
        self.assertTrue(graph['present'])
        self.assertEqual(graph['version'], C.sha256_file(self.graph_path))
        self.assertIn('svc:payouts-api', graph['node_ids'])
        self.assertIn('sub:kong-lite|implements|svc:edge-gateway', graph['edge_ids'])
        self.assertEqual(graph['node_count'], len(graph['node_ids']))

    def test_never_reads_secrets_or_generated(self):
        blob = json.dumps(self.snapshot)
        for secret in ('SUPER-SECRET-VALUE-DO-NOT-CAPTURE', 'SECRET-RENDERED-VALUE', 'SECRET-SEEDED-VALUE'):
            self.assertNotIn(secret, blob)
        for path in self.snapshot['config']['hashes']:
            self.assertFalse(path.startswith('secrets/'), path)
            self.assertFalse(path.startswith('config/generated/'), path)
            self.assertFalse(path.startswith('seeds/generated/'), path)

    def test_write_never_overwrites_and_updates_latest(self):
        out = self.root / 'reports/domain/snapshots'
        first = CAP.write_snapshot(dict(self.snapshot), out)
        second = CAP.write_snapshot(dict(self.snapshot), out)
        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file() and second.is_file())
        pointer = C.read_json(out / 'latest.json')
        self.assertEqual(pointer['latest'], second.name)
        self.assertEqual(CAP.latest_snapshot_path(out), second)

    def test_real_repository_captures_without_docker(self):
        """The real tree must snapshot with docker switched off (property of the real repo)."""
        snap = CAP.build_snapshot(root=ROOT, with_docker=False)
        self.assertEqual(snap['images_reason'], 'skipped: --no-docker')
        self.assertTrue(all(v is None for v in snap['images'].values()))
        self.assertIn('payouts', snap['repos'])
        self.assertGreater(len(snap['compose']['services']), 10)
        self.assertNotIn('secrets/', json.dumps(sorted(snap['config']['hashes'])))


# --------------------------------------------------------------------------- 2. diff
class TestDiff(FixtureTwinCase):
    def _mutate(self, fn) -> dict:
        new = json.loads(json.dumps(self.snapshot))
        fn(new)
        return D.diff_snapshots(json.loads(json.dumps(self.snapshot)), new)

    def test_identical_snapshots_report_no_change(self):
        d = D.diff_snapshots(json.loads(json.dumps(self.snapshot)), json.loads(json.dumps(self.snapshot)))
        self.assertFalse(d['changed'])
        self.assertEqual(d['summary'], ['no change'])

    def test_repo_sha_change_is_detected_with_a_commit_range(self):
        d = self._mutate(lambda s: s['repos']['payouts'].update({'sha': 'f' * 40}))
        self.assertTrue(d['changed'])
        entry = next(r for r in d['repos'] if r['name'] == 'payouts')
        self.assertEqual(entry['change'], 'modified')
        self.assertIn('sha', entry['reasons'])
        self.assertTrue(entry['commit_range'].endswith('..ffffffffffff'))
        self.assertTrue(any(line.startswith('repo payouts') for line in d['summary']))

    def test_flag_change_is_detected(self):
        key = 'dcs/rzp/x/payouts/enable_x'
        d = self._mutate(lambda s: s['flags']['dcs']['items'].update({key: 'deadbeef'}))
        self.assertTrue(d['changed'])
        self.assertEqual(d['flags']['dcs']['items']['modified'], [key])

    def test_contract_change_is_detected_for_substitutes_and_proto(self):
        d = self._mutate(lambda s: s['contracts']['substitutes'].update({'kong-lite': 'cafebabe'}))
        self.assertTrue(d['contracts']['changed'])
        self.assertEqual(d['contracts']['substitutes']['modified'], ['kong-lite'])

        def bump_proto(s):
            files = s['contracts']['proto']['payouts']['files']
            files[sorted(files)[0]] = '0' * 64
        d2 = self._mutate(bump_proto)
        self.assertTrue(d2['contracts']['changed'])
        self.assertIn('payouts', d2['contracts']['proto'])

    def test_schema_config_compose_and_graph_changes(self):
        d = self._mutate(lambda s: s['schemas']['payouts']['files'].update({'internal/database/migrations/x.go': '1' * 64}))
        self.assertEqual(d['schemas']['payouts']['added'], ['internal/database/migrations/x.go'])
        d = self._mutate(lambda s: s['config']['hashes'].update({'docker-compose.yml': '2' * 64}))
        self.assertTrue(d['config']['changed'])
        d = self._mutate(lambda s: s['compose']['services'].remove('kong-lite'))
        self.assertTrue(d['compose']['changed'])
        self.assertEqual(d['compose']['removed'], ['kong-lite'])
        d = self._mutate(lambda s: s['graph'].update({'version': '3' * 64, 'node_ids': s['graph']['node_ids'] + ['svc:new']}))
        self.assertTrue(d['graph']['changed'])
        self.assertEqual(d['graph']['nodes_added'], ['svc:new'])

    def test_null_image_ids_on_both_sides_are_not_a_change(self):
        d = D.diff_snapshots(json.loads(json.dumps(self.snapshot)), json.loads(json.dumps(self.snapshot)))
        self.assertFalse(d['images']['changed'])

    def test_cli_exit_codes(self):
        snaps = self.root / 'cli-snaps'
        snaps.mkdir(exist_ok=True)
        old = C.write_json(snaps / 'old.json', self.snapshot)
        new_snap = json.loads(json.dumps(self.snapshot))
        new_snap['repos']['payouts']['sha'] = 'e' * 40
        new = C.write_json(snaps / 'new.json', new_snap)
        self.assertEqual(D.main([str(old), str(old), '--quiet']), 0)
        self.assertEqual(D.main([str(old), str(new), '--quiet']), D.EXIT_CHANGED)


# --------------------------------------------------------------------------- 3. affected
class TestAffectedWithGraph(FixtureTwinCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.graph = C.load_graph(cls.graph_path)
        cls.compose = C.load_compose(cls.root / 'ENV2_COMPOSE/docker-compose.yml')

    def _plan(self, diff: dict) -> dict:
        return A.compute_affected(diff, self.graph, self.compose)

    def test_no_change_yields_an_empty_plan(self):
        plan = self._plan({'changed': False})
        self.assertEqual(plan['rebuild'], [])
        self.assertEqual(plan['rerun_journeys'], [])
        self.assertFalse(plan['regen_config'])

    def test_repo_change_maps_to_build_target_and_family_journeys(self):
        plan = self._plan({'changed': True, 'repos': [{'name': 'payouts', 'change': 'modified',
                                                       'commit_range': 'aaaa..bbbb', 'reasons': ['sha']}]})
        self.assertTrue(plan['graph_used'])
        self.assertFalse(plan['fallback_used'])
        self.assertEqual(plan['rebuild'], ['payouts'])
        self.assertEqual(plan['rerun_journeys'], ['journey:direct-payouts/failure', 'journey:direct-payouts/success'])
        self.assertIn('svc:payouts-api', plan['affected_services'])
        self.assertIn('worker:payouts/queued_payout', plan['affected_services'])
        self.assertIn('family:direct-payouts', plan['affected_families'])

    def test_schema_change_maps_to_table_owners_and_migrations(self):
        plan = self._plan({'changed': True, 'schemas': {'payouts': {'added': ['m.go'], 'removed': [], 'modified': []}}})
        self.assertEqual(plan['rebuild'], ['payouts'])
        self.assertEqual(plan['rerun_migrations'], ['payouts-migrate'])
        self.assertIn('journey:direct-payouts/success', plan['rerun_journeys'])
        self.assertIn('svc:payouts-api', plan['affected_services'])

    def test_flag_change_maps_through_gated_by(self):
        plan = self._plan({'changed': True, 'flags': {'dcs': {
            'items': {'added': [], 'removed': [], 'modified': ['dcs/rzp/x/payouts/enable_x']},
            'files': {'added': [], 'removed': [], 'modified': []}}}})
        self.assertIn('journey:direct-payouts/success', plan['rerun_journeys'])
        self.assertIn('dcs-stub', plan['reseed'])
        self.assertEqual(plan['rebuild'], [])

    def test_substitute_contract_change_maps_to_the_service_it_implements(self):
        plan = self._plan({'changed': True, 'contracts': {
            'proto': {}, 'substitutes': {'added': [], 'removed': [], 'modified': ['kong-lite']},
            'batch_types': {'added': [], 'removed': [], 'modified': []}, 'changed': True}})
        self.assertEqual(plan['rerun_journeys'], ['journey:edge/policy'])
        self.assertEqual(plan['rebuild'], ['kong-lite'])
        self.assertIn('kong-lite', plan['affected_substitutes'])

    def test_proto_contract_change_rebuilds_the_owning_service(self):
        plan = self._plan({'changed': True, 'contracts': {
            'proto': {'payouts': {'files': {'added': [], 'removed': [], 'modified': ['stork/webhook/v1/webhook.proto']},
                                  'modules': {'added': [], 'removed': [], 'modified': []}}},
            'substitutes': {'added': [], 'removed': [], 'modified': []},
            'batch_types': {'added': [], 'removed': [], 'modified': []}, 'changed': True}})
        self.assertEqual(plan['rebuild'], ['payouts'])
        self.assertIn('journey:direct-payouts/success', plan['rerun_journeys'])

    def test_config_template_change_sets_regen_config(self):
        plan = self._plan({'changed': True, 'config': {'added': [], 'removed': [],
                                                       'modified': ['config/templates/base/payouts/arena.toml'], 'changed': True}})
        self.assertTrue(plan['regen_config'])
        self.assertIn('payouts-api', plan['restart'])

    def test_graph_change_reruns_the_touched_journeys(self):
        plan = self._plan({'changed': True, 'graph': {'changed': True, 'old_version': 'a' * 64, 'new_version': 'b' * 64,
                                                      'nodes_added': ['journey:direct-payouts/failure'], 'nodes_removed': [],
                                                      'nodes_modified': ['svc:ledger-api'], 'edges_added': [], 'edges_removed': []}})
        self.assertIn('journey:direct-payouts/failure', plan['rerun_journeys'])
        self.assertTrue(plan['regen_recipes'])

    def test_fallback_map_is_used_when_the_graph_is_absent(self):
        plan = A.compute_affected({'changed': True, 'repos': [{'name': 'payouts', 'change': 'modified',
                                                               'commit_range': 'a..b', 'reasons': ['sha']}]},
                                  None, self.compose)
        self.assertFalse(plan['graph_used'])
        self.assertTrue(plan['fallback_used'])
        self.assertEqual(plan['rebuild'], ['payouts'])
        self.assertEqual(plan['rerun_journeys'], A.ALL_DRIVERS)
        self.assertTrue(plan['journey_drivers'])

    def test_cli_writes_a_plan(self):
        diff_path = C.write_json(self.root / 'affected-diff.json',
                                 {'changed': True, 'repos': [{'name': 'ledger', 'change': 'modified',
                                                              'commit_range': 'a..b', 'reasons': ['sha']}]})
        out = self.root / 'affected-plan.json'
        with contextlib.redirect_stdout(io.StringIO()):
            rc = A.main([str(diff_path), '--graph', str(self.graph_path),
                         '--compose', str(self.root / 'ENV2_COMPOSE/docker-compose.yml'), '--json', str(out)])
        self.assertEqual(rc, 0)
        plan = C.read_json(out)
        self.assertEqual(plan['rebuild'], ['ledger'])
        self.assertIn('journey:direct-payouts/success', plan['rerun_journeys'])


# --------------------------------------------------------------------------- 4. recipes
class TestRecipes(FixtureTwinCase):
    REQUIRED = ('repository', 'sha', 'build_command', 'runtime', 'health_check', 'dependencies',
                'schemas_and_contracts', 'configuration_hash', 'database_migrations',
                'synthetic_seed_generator', 'external_replacements', 'fidelity_label')

    def test_one_recipe_per_compose_service_and_source_repo(self):
        for service in C.compose_services(self.ctx['compose']):
            self.assertIn(service, self.recipes, service)
        for repo in ('payouts', 'ledger', 'workflows', 'batch', 'proto'):
            self.assertIn('repo-' + repo, self.recipes, repo)

    def test_required_keys_present_on_every_recipe(self):
        for rid, recipe in self.recipes.items():
            for key in self.REQUIRED:
                self.assertIn(key, recipe, '%s missing %s' % (rid, key))

    def test_core_service_recipe(self):
        r = self.recipes['payouts-api']
        self.assertEqual(r['kind'], 'compose_service')
        self.assertEqual(r['repository'], 'razorpay/payouts')
        self.assertTrue(r['sha'])
        self.assertIn('build-host.sh payouts', r['build_command']['canonical'])
        self.assertTrue(any('./cmd/api' in s for s in r['build_command']['steps']),
                        'build steps must come from build-host.sh')
        self.assertEqual(r['runtime']['image'], 'rzp-arena/payouts:${ARENA_TAG:-local}')
        self.assertIn('9400/status', r['health_check'])
        self.assertIn('mysql-payouts', r['dependencies']['compose_depends_on'])
        self.assertIn('svc:ledger-api (calls)', r['dependencies']['graph_edges'])
        self.assertEqual(r['database_migrations']['compose_service'], 'payouts-migrate')
        self.assertEqual(r['schemas_and_contracts']['migration_count'], 1)
        self.assertTrue(r['configuration_hash'])
        self.assertIn('dcs-stub', r['external_replacements'])
        self.assertTrue(r['synthetic_seed_generator'])
        self.assertEqual(r['fidelity_label'], 'real_source_running')
        self.assertIn('graph node svc:payouts-api', r['fidelity_reason'])

    def test_substitute_recipe(self):
        r = self.recipes['kong-lite']
        self.assertTrue(r['repository'].startswith('twin-local:'))
        self.assertIsNone(r['sha'])
        self.assertIn('docker compose build kong-lite', r['build_command']['canonical'])
        self.assertTrue(r['schemas_and_contracts']['contract'].endswith('kong-lite/CONTRACT.md'))
        self.assertTrue(r['schemas_and_contracts']['contract_sha256'])
        # union of the documented replacement text and the graph's `implements` edge
        self.assertEqual(r['external_replacements'], ['edge (Kong gateway + route policy)', 'svc:edge-gateway'])
        self.assertEqual(r['replaced_source_repo'], 'razorpay/edge')
        self.assertEqual(r['fidelity_label'], 'high_fidelity_replacement')
        self.assertTrue(r['configuration_hash'])

    def test_infrastructure_recipe(self):
        r = self.recipes['mysql-payouts']
        self.assertEqual(r['repository'], 'upstream image mysql:8.0.36')
        self.assertIn('docker pull mysql:8.0.36', r['build_command']['canonical'])
        self.assertIsNone(r['sha'])
        self.assertEqual(r['fidelity_label'], 'real_source_running')
        self.assertTrue(r['synthetic_seed_generator'])

    def test_source_repo_recipe_for_a_repo_that_is_not_built(self):
        r = self.recipes['repo-workflows']
        self.assertEqual(r['kind'], 'source_repo')
        self.assertEqual(r['repository'], 'razorpay/workflows')
        self.assertIn('not built in twin', r['build_command']['canonical'])
        self.assertIsNone(r['runtime']['image'])
        self.assertIn('workflow-sim', r['external_replacements'])
        self.assertEqual(r['fidelity_label'], 'real_source_mapped_not_running')

    def test_yaml_output_and_manifest(self):
        out = self.root / 'reports/domain/recipes'
        paths = R.write_recipes(self.recipes, out)
        self.assertEqual(len(paths), len(self.recipes))
        import yaml
        text = (out / 'payouts-api.yaml').read_text()
        self.assertNotIn('*id001', text, 'recipes must not contain yaml aliases')
        body = yaml.safe_load(text)
        self.assertEqual(body['id'], 'payouts-api')
        self.assertTrue(body['recipe_hash'])

        manifest = R.build_manifest(self.ctx, self.recipes, self.snapshot)
        for key in ('repositories', 'config_hashes', 'schema_versions', 'image_digests', 'graph_version',
                    'recipes', 'generated_at'):
            self.assertIn(key, manifest, key)
        self.assertEqual(manifest['graph_version'], C.sha256_file(self.graph_path))
        self.assertEqual(len(manifest['recipes']), len(self.recipes))
        self.assertIn('payouts', manifest['repositories'])
        self.assertIn('payouts', manifest['schema_versions'])
        self.assertIn('docker-compose.yml', manifest['config_hashes'])
        self.assertNotIn('SUPER-SECRET-VALUE-DO-NOT-CAPTURE', json.dumps(manifest))

    def test_recipe_hash_is_stable_across_generated_at(self):
        a = dict(self.recipes['payouts-api'], generated_at='2020-01-01T00:00:00Z')
        b = dict(self.recipes['payouts-api'], generated_at='2030-01-01T00:00:00Z')
        self.assertEqual(R.recipe_hash(a), R.recipe_hash(b))


# --------------------------------------------------------------------------- 5. refresh
class TestRefresh(FixtureTwinCase):
    def test_daily_plan_is_read_only_and_records_every_stage(self):
        snap_dir = self.root / 'reports/domain/refresh-snaps'
        refresh_dir = self.root / 'reports/domain/refresh'
        plan = RF.plan_daily(root=self.root, with_docker=False, snapshot_dir=snap_dir,
                             refresh_dir=refresh_dir, graph_path=self.graph_path)
        self.assertEqual(plan['mode'], 'daily')
        self.assertFalse(plan['executed'])
        ids = [s['id'] for s in plan['steps']]
        self.assertEqual(ids[:3], ['capture', 'diff', 'affected'])
        self.assertIn('journeys', ids)
        self.assertIsNone(plan['previous_snapshot'], 'first run has no baseline')
        self.assertFalse(plan['changed'])

        # second run diffs against the first and never overwrites it
        plan2 = RF.plan_daily(root=self.root, with_docker=False, snapshot_dir=snap_dir,
                              refresh_dir=refresh_dir, graph_path=self.graph_path)
        self.assertIsNotNone(plan2['previous_snapshot'])
        snaps = [p for p in snap_dir.glob('*.json') if p.name != 'latest.json']
        self.assertEqual(len(snaps), 2, 'each run writes a new snapshot and overwrites none')
        self.assertEqual(len(list(refresh_dir.glob('*-diff.json'))), 2)
        self.assertEqual(RF.latest_build_evidence(self.root).name, 'build-evidence-20260101T000000Z')

        path = RF.write_plan(plan2, refresh_dir)
        self.assertTrue(path.is_file())

    def test_weekly_plan_lists_the_chain_in_order(self):
        plan = RF.plan_weekly(root=self.root, tag='fixture-tag')
        self.assertEqual([s['id'] for s in plan['steps']],
                         ['prepare-repos', 'check-inputs', 'build-host', 'clean-boot', 'journeys', 'm6-acceptance'])
        self.assertTrue(all(s['status'] == 'planned' for s in plan['steps']))
        self.assertEqual(plan['arena_tag'], 'fixture-tag')
        self.assertIn('prepare-repos.py', plan['steps'][0]['command'])
        self.assertIn('m6_acceptance.py', plan['steps'][-1]['command'])

    def test_status_reports_the_real_tree(self):
        st = RF.status(root=ROOT)
        for key in ('snapshots', 'recipes', 'manifest', 'graph', 'docker', 'journey_runner', 'refresh_plans'):
            self.assertIn(key, st)
        self.assertIsInstance(st['docker']['available'], bool)
        self.assertIn('twin refresh status', RF.format_status(st))


if __name__ == '__main__':
    unittest.main()
