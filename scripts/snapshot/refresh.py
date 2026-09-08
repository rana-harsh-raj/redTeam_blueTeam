#!/usr/bin/env python3
"""Daily / weekly refresh driver for the Payouts functional-architecture twin.

  python3 scripts/snapshot/refresh.py daily   [--execute] [--no-docker]
  python3 scripts/snapshot/refresh.py weekly  [--execute] [--repos-root DIR] [--tag TAG]
  python3 scripts/snapshot/refresh.py status

daily  -- capture a fresh snapshot, diff it against the previous `latest.json`,
          map the diff to affected components (affected.py) and write the plan to
          reports/domain/refresh/<UTC>-daily.json.
          WITHOUT --execute nothing outside reports/domain/ is touched.
          WITH --execute the plan's rebuild steps are run for AFFECTED CORE
          SERVICES ONLY (via ENV2_COMPOSE/build/record-rebuild.py, which drives
          build-host.sh and records fresh build evidence), then the affected
          journeys via `python3 RED_LOOP/m6/journeys/run.py --only <ids>`.
          Steps that need docker are recorded as "skipped: no docker" when the
          daemon is unreachable; a missing journey runner is recorded, not faked.

weekly -- the full re-pin chain, in this order:
            1. ENV2_COMPOSE/build/prepare-repos.py   (fresh copies from clone HEADs)
            2. ENV2_COMPOSE/build/check-inputs.py    (verify the new provenance)
            3. ENV2_COMPOSE/build/build-host.sh all  (rebuild all 5 core images)
            4. RED_LOOP/m6/clean_boot.py             (clean boot from empty state)
            5. RED_LOOP/m6/journeys/run.py           (full journey suite)
            6. RED_LOOP/surface/m6_acceptance.py     (M6 gates)
          WITHOUT --execute this is a plan only. WITH --execute the steps run in
          order and stop at the first failure; every step is recorded.

status -- what exists right now: latest snapshot + age, recipe/manifest freshness,
          graph presence, docker reachability, last daily/weekly plans.

Safety: snapshots are never deleted or overwritten (capture.py allocates a fresh
name); the live arena is never touched unless --execute is passed; secrets are
never read or printed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import capture as CAP  # noqa: E402
import diff as D  # noqa: E402
import affected as A  # noqa: E402

PLAN_SCHEMA_VERSION = 1
OUTPUT_TAIL = 4000
# record-rebuild.py accepts exactly these service ids (== build-host.sh targets).
REBUILDABLE = sorted(set(C.CORE_REPOS.values()))
JOURNEY_RUNNER = 'RED_LOOP/m6/journeys/run.py'
CLEAN_BOOT = 'RED_LOOP/m6/clean_boot.py'
M6_ACCEPTANCE = 'RED_LOOP/surface/m6_acceptance.py'


# --------------------------------------------------------------------------- helpers
def _step(sid: str, kind: str, command, note: str = '', **extra) -> dict:
    return {'id': sid, 'kind': kind, 'command': command if isinstance(command, str) else ' '.join(command),
            'argv': command if isinstance(command, list) else None, 'status': 'planned', 'note': note,
            'rc': None, 'seconds': None, 'stdout_tail': None, 'stderr_tail': None, **extra}


def run_step(step: dict, root: Path = C.ROOT, timeout: int = 5400) -> dict:
    """Execute one planned step in-place; never raises."""
    argv = step.get('argv')
    if not argv:
        step['status'] = 'skipped: no executable argv (informational step)'
        return step
    started = time.time()
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True, timeout=timeout)
        step['rc'] = proc.returncode
        step['stdout_tail'] = proc.stdout[-OUTPUT_TAIL:]
        step['stderr_tail'] = proc.stderr[-OUTPUT_TAIL:]
        step['status'] = 'ok' if proc.returncode == 0 else 'failed'
    except subprocess.TimeoutExpired:
        step['status'] = 'failed: timeout after %ds' % timeout
    except OSError as exc:
        step['status'] = 'failed: %s' % exc
    step['seconds'] = round(time.time() - started, 1)
    return step


def latest_build_evidence(root: Path = C.ROOT) -> Path | None:
    dirs = sorted((Path(root) / '.local/twin-repos').glob('build-evidence-*')) if (Path(root) / '.local/twin-repos').is_dir() else []
    dirs = [d for d in dirs if (d / 'provenance.json').is_file()]
    return dirs[-1] if dirs else None


def _rel(path, root: Path = C.ROOT) -> str:
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except (ValueError, OSError):
        return str(path)


def write_plan(plan: dict, out_dir: Path = C.REFRESH_DIR) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = C.utc_stamp()
    path = out_dir / ('%s-%s.json' % (stamp, plan['mode']))
    n = 1
    while path.exists():
        path = out_dir / ('%s-%d-%s.json' % (stamp, n, plan['mode']))
        n += 1
    plan['_path'] = str(path)
    C.write_json(path, plan, overwrite=False)
    return path


def _plan_status(plan: dict) -> str:
    statuses = [s['status'] for s in plan['steps']]
    if any(s.startswith('failed') for s in statuses):
        return 'failed'
    if all(s == 'planned' for s in statuses):
        return 'planned'
    if any(s == 'planned' for s in statuses):
        return 'partial'
    return 'ok'


# --------------------------------------------------------------------------- daily
def plan_daily(root: Path = C.ROOT, with_docker: bool = True, snapshot_dir: Path | None = None,
               refresh_dir: Path | None = None, graph_path: Path | None = None) -> dict:
    root = Path(root)
    snapshot_dir = Path(snapshot_dir) if snapshot_dir else C.SNAPSHOT_DIR
    refresh_dir = Path(refresh_dir) if refresh_dir else C.REFRESH_DIR
    graph_path = Path(graph_path) if graph_path else root / 'reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json'

    previous_path = CAP.latest_snapshot_path(snapshot_dir)          # read BEFORE capture rewrites latest.json
    previous = D.load_snapshot(previous_path) if previous_path else None

    snapshot = CAP.build_snapshot(root=root, graph_path=graph_path, with_docker=with_docker)
    snapshot_path = CAP.write_snapshot(snapshot, snapshot_dir)
    snapshot['_path'] = str(snapshot_path)

    steps = [_step('capture', 'capture', 'python3 scripts/snapshot/capture.py',
                   'snapshot written; the previous snapshot is never overwritten', artefact=_rel(snapshot_path))]
    steps[0]['status'] = 'ok'

    if previous is None:
        the_diff = {'changed': False, 'summary': ['baseline snapshot: no previous snapshot to diff against'],
                    'repos': [], 'contracts': {'changed': False, 'proto': {}}, 'schemas': {}, 'flags': {},
                    'config': {'changed': False}, 'compose': {'changed': False}, 'images': {'changed': False},
                    'graph': {'changed': False}, 'old': None, 'new': {'path': str(snapshot_path)}}
    else:
        the_diff = D.diff_snapshots(previous, snapshot)
    diff_path = refresh_dir / (Path(snapshot_path).stem + '-diff.json')
    C.write_json(diff_path, the_diff)
    steps.append(_step('diff', 'diff', 'python3 scripts/snapshot/diff.py %s %s' % (
        _rel(previous_path) if previous_path else '<none>', _rel(snapshot_path)),
        'exit 3 == changed', artefact=_rel(diff_path)))
    steps[-1]['status'] = 'ok'
    steps[-1]['changed'] = bool(the_diff.get('changed'))

    graph = C.load_graph(graph_path)
    compose = C.load_compose(root / 'ENV2_COMPOSE/docker-compose.yml') if (root / 'ENV2_COMPOSE/docker-compose.yml').is_file() else {}
    affected = A.compute_affected(the_diff, graph, compose)
    steps.append(_step('affected', 'affected', 'python3 scripts/snapshot/affected.py ' + _rel(diff_path),
                       'graph_used=%s fallback_used=%s' % (affected['graph_used'], affected['fallback_used'])))
    steps[-1]['status'] = 'ok'

    if affected.get('regen_config'):
        steps.append(_step('regen-config', 'config', 'python3 ENV2_COMPOSE/config/generate.py',
                           'planned only: config is regenerated by ENV2_COMPOSE/scripts/up.sh on the next boot; '
                           'daily --execute never renders config (it reads secrets/)'))

    evidence = latest_build_evidence(root)
    core_targets = [t for t in affected.get('rebuild', []) if t in REBUILDABLE]
    skipped_rebuilds = [t for t in affected.get('rebuild', []) if t not in REBUILDABLE]
    for target in core_targets:
        argv = ['python3', 'ENV2_COMPOSE/build/record-rebuild.py', target,
                '--base-evidence', _rel(evidence) if evidence else '<no build-evidence dir found>',
                '--repos-root', '.local/twin-repos/accepted']
        steps.append(_step('rebuild:' + target, 'rebuild', argv,
                           'rebuilds rzp-arena/%s from the admitted copy and records fresh build evidence'
                           % C.CORE_IMAGE.get(A.TARGET_REPO.get(target, target), target),
                           executable=evidence is not None))
    if skipped_rebuilds:
        steps.append(_step('rebuild:non-core', 'note',
                           'cd ENV2_COMPOSE && docker compose build ' + ' '.join(skipped_rebuilds),
                           'affected non-core components (substitutes); daily --execute rebuilds core services only'))

    journeys = affected.get('rerun_journeys', [])
    runner = root / JOURNEY_RUNNER
    if journeys:
        argv = ['python3', JOURNEY_RUNNER, '--only', ','.join(journeys)]
        step = _step('journeys', 'journeys', argv,
                     'runner present' if runner.is_file() else 'runner %s not present yet: command recorded, not run' % JOURNEY_RUNNER,
                     journeys=journeys, runner_present=runner.is_file())
        if not runner.is_file():
            step['argv'] = None
            step['drivers'] = affected.get('journey_drivers', {})
        steps.append(step)
    else:
        steps.append(_step('journeys', 'journeys', 'python3 %s --only <none>' % JOURNEY_RUNNER,
                           'no journeys affected', journeys=[], runner_present=runner.is_file()))
        steps[-1]['argv'] = None
        steps[-1]['status'] = 'skipped: no journeys affected'

    plan = {
        'schema_version': PLAN_SCHEMA_VERSION, 'mode': 'daily', 'generated_at': C.utc_now(),
        'root': str(root), 'executed': False, 'status': 'planned',
        'previous_snapshot': _rel(previous_path) if previous_path else None,
        'snapshot': _rel(snapshot_path), 'snapshot_digest': snapshot['digest'],
        'diff_artefact': _rel(diff_path), 'changed': bool(the_diff.get('changed')),
        'diff_summary': the_diff.get('summary', []),
        'affected': {k: affected[k] for k in ('rebuild', 'rerun_journeys', 'regen_config', 'regen_recipes', 'restart',
                                              'reseed', 'rerun_migrations', 'affected_services', 'affected_substitutes',
                                              'affected_families', 'graph_used', 'fallback_used')},
        'affected_reasons': affected.get('reasons', []),
        'docker': {'available': None, 'reason': snapshot.get('images_reason')},
        'steps': steps,
        'note': 'plan only; rerun with --execute to run the rebuild and journey steps',
    }
    plan['status'] = _plan_status(plan)
    return plan


def execute_daily(plan: dict, root: Path = C.ROOT) -> dict:
    docker_ok, docker_reason = C.docker_available()
    plan['docker'] = {'available': docker_ok, 'reason': docker_reason}
    plan['executed'] = True
    plan['note'] = 'executed: rebuild steps for affected core services + affected journeys'
    for step in plan['steps']:
        if step['status'] != 'planned':
            continue
        if step['kind'] == 'rebuild':
            if not docker_ok:
                step['status'] = 'skipped: no docker (%s)' % docker_reason
                continue
            if step.get('executable') is False:
                step['status'] = 'skipped: no build-evidence directory to derive from'
                continue
            run_step(step, root)
        elif step['kind'] == 'journeys':
            if step.get('argv'):
                run_step(step, root)
            else:
                step['status'] = 'recorded: %s' % (step['note'] or 'nothing to run')
        else:
            step['status'] = 'recorded: %s' % (step['note'] or 'informational step')
    plan['status'] = _plan_status(plan)
    return plan


# --------------------------------------------------------------------------- weekly
def plan_weekly(root: Path = C.ROOT, repos_root: Path | None = None, tag: str | None = None,
                destination: Path | None = None) -> dict:
    root = Path(root)
    repos_root = Path(repos_root) if repos_root else C.repos_root(root)
    tag = tag or _arena_tag(root)
    stamp = C.utc_stamp()
    destination = Path(destination) if destination else root / ('.local/twin-repos/refresh-' + stamp)
    evidence_glob = '.local/twin-repos/build-evidence-*'

    steps = [
        _step('prepare-repos', 'prepare',
              ['python3', 'ENV2_COMPOSE/build/prepare-repos.py', '--source-root', str(repos_root) if repos_root else '<repos-root missing>',
               '--destination', _rel(destination)],
              'fresh admitted copies from the pinned clone HEADs; writes a sibling build-evidence-<UTC>/provenance.json. '
              'Refuses to overwrite an existing destination.',
              executable=repos_root is not None),
        _step('check-inputs', 'verify',
              ['python3', 'ENV2_COMPOSE/build/check-inputs.py', '<new-build-evidence>/provenance.json', '--verify-modules'],
              'verifies the new copies against their provenance manifest (resolved from %s after step 1)' % evidence_glob,
              resolve='latest_build_evidence'),
        _step('build-host', 'build',
              ['bash', 'ENV2_COMPOSE/build/build-host.sh', 'all'],
              'REPOS_ROOT=%s ARENA_TAG=%s; builds all 5 core images on the host' % (_rel(destination), tag),
              env={'REPOS_ROOT': _rel(destination), 'ARENA_TAG': tag}),
        _step('clean-boot', 'boot', ['python3', CLEAN_BOOT],
              'down.sh (volumes+secrets) -> assert empty state -> up.sh -> health; touches the live arena',
              executable=(root / CLEAN_BOOT).is_file()),
        _step('journeys', 'journeys', ['python3', JOURNEY_RUNNER],
              'full journey suite' if (root / JOURNEY_RUNNER).is_file() else '%s not present yet: command recorded, not run' % JOURNEY_RUNNER,
              executable=(root / JOURNEY_RUNNER).is_file()),
        _step('m6-acceptance', 'acceptance', ['python3', M6_ACCEPTANCE],
              'evaluate the M6 gates', executable=(root / M6_ACCEPTANCE).is_file()),
    ]
    return {
        'schema_version': PLAN_SCHEMA_VERSION, 'mode': 'weekly', 'generated_at': C.utc_now(),
        'root': str(root), 'executed': False, 'status': 'planned',
        'repos_root': str(repos_root) if repos_root else None, 'destination': _rel(destination), 'arena_tag': tag,
        'base_build_evidence': _rel(latest_build_evidence(root)) if latest_build_evidence(root) else None,
        'docker': {'available': None, 'reason': None},
        'steps': steps,
        'note': 'plan only; rerun with --execute to run every step in order (stops at the first failure)',
    }


def _arena_tag(root: Path = C.ROOT) -> str:
    env_file = Path(root) / 'ENV2_COMPOSE/.env.arena'
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith('ARENA_TAG'):
                return line.split('=', 1)[1].strip().strip('"\'')
    return os.environ.get('ARENA_TAG', 'v1-candidate')


def execute_weekly(plan: dict, root: Path = C.ROOT) -> dict:
    docker_ok, docker_reason = C.docker_available()
    plan['docker'] = {'available': docker_ok, 'reason': docker_reason}
    plan['executed'] = True
    stopped = False
    for step in plan['steps']:
        if stopped:
            step['status'] = 'not_run: a previous step failed'
            continue
        if step.get('executable') is False:
            step['status'] = 'skipped: %s' % (step['note'] or 'prerequisite missing')
            continue
        if step['kind'] in ('build', 'boot') and not docker_ok:
            step['status'] = 'skipped: no docker (%s)' % docker_reason
            stopped = True
            continue
        if step['id'] == 'check-inputs':
            evidence = latest_build_evidence(root)
            if not evidence:
                step['status'] = 'skipped: no build-evidence directory produced by step 1'
                stopped = True
                continue
            step['argv'] = ['python3', 'ENV2_COMPOSE/build/check-inputs.py',
                            _rel(evidence / 'provenance.json'), '--verify-modules']
            step['command'] = ' '.join(step['argv'])
        env_extra = step.get('env') or {}
        if env_extra:
            saved = {k: os.environ.get(k) for k in env_extra}
            os.environ.update({k: str(v) for k, v in env_extra.items()})
            try:
                run_step(step, root)
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        else:
            run_step(step, root)
        if str(step['status']).startswith('failed'):
            stopped = True
    plan['status'] = _plan_status(plan)
    plan['stopped_early'] = stopped
    return plan


# --------------------------------------------------------------------------- status
def status(root: Path = C.ROOT) -> dict:
    root = Path(root)
    snap_dir = root / 'reports/domain/snapshots'
    snaps = sorted(p for p in snap_dir.glob('*.json') if p.name != 'latest.json' and not p.name.endswith('-diff.json')) if snap_dir.is_dir() else []
    latest_path = CAP.latest_snapshot_path(snap_dir)
    latest = None
    if latest_path:
        try:
            latest = C.read_json(latest_path)
        except (OSError, ValueError):
            latest = None
    recipes_dir = root / 'reports/domain/recipes'
    manifest_path = root / 'reports/domain/SNAPSHOT_MANIFEST.json'
    manifest = C.read_json(manifest_path) if manifest_path.is_file() else None
    refresh_dir = root / 'reports/domain/refresh'
    plans = sorted(refresh_dir.glob('*.json')) if refresh_dir.is_dir() else []
    docker_ok, docker_reason = C.docker_available()
    graph_path = root / 'reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json'
    return {
        'generated_at': C.utc_now(), 'root': str(root),
        'snapshots': {'count': len(snaps), 'latest': _rel(latest_path) if latest_path else None,
                      'captured_at': (latest or {}).get('captured_at'), 'digest': (latest or {}).get('digest'),
                      'repos': len((latest or {}).get('repos', {})),
                      'compose_services': len(((latest or {}).get('compose') or {}).get('services', []))},
        'recipes': {'count': len(list(recipes_dir.glob('*.yaml'))) if recipes_dir.is_dir() else 0,
                    'dir': _rel(recipes_dir)},
        'manifest': {'path': _rel(manifest_path) if manifest_path.is_file() else None,
                     'generated_at': (manifest or {}).get('generated_at'),
                     'graph_version': (manifest or {}).get('graph_version'),
                     'recipes': len((manifest or {}).get('recipes', {}))},
        'graph': {'present': graph_path.is_file(), 'path': _rel(graph_path),
                  'version': C.graph_version(graph_path)},
        'docker': {'available': docker_ok, 'reason': docker_reason},
        'journey_runner': {'path': JOURNEY_RUNNER, 'present': (root / JOURNEY_RUNNER).is_file()},
        'refresh_plans': {'count': len(plans), 'last': [_rel(p) for p in plans[-3:]]},
        'build_evidence': _rel(latest_build_evidence(root)) if latest_build_evidence(root) else None,
    }


def format_status(st: dict) -> str:
    lines = ['twin refresh status @ ' + st['generated_at'],
             '  snapshots      : %d (latest %s, captured %s)' % (st['snapshots']['count'], st['snapshots']['latest'], st['snapshots']['captured_at']),
             '  recipes        : %d in %s' % (st['recipes']['count'], st['recipes']['dir']),
             '  manifest       : %s (generated %s, %d recipes)' % (st['manifest']['path'], st['manifest']['generated_at'], st['manifest']['recipes']),
             '  functional graph: %s%s' % ('present' if st['graph']['present'] else 'ABSENT (affected.py uses the conservative fallback map)',
                                           ' ' + (st['graph']['version'] or '')[:12] if st['graph']['present'] else ''),
             '  journey runner : %s (%s)' % (st['journey_runner']['path'], 'present' if st['journey_runner']['present'] else 'not present yet'),
             '  docker         : %s' % ('available' if st['docker']['available'] else 'unavailable -- %s' % st['docker']['reason']),
             '  build evidence : %s' % st['build_evidence'],
             '  refresh plans  : %d (%s)' % (st['refresh_plans']['count'], ', '.join(st['refresh_plans']['last']) or 'none')]
    return '\n'.join(lines)


def format_plan(plan: dict) -> str:
    head = '%s refresh (%s) -- %s' % (plan['mode'], plan['status'], 'EXECUTED' if plan['executed'] else 'plan only')
    lines = [head]
    if plan['mode'] == 'daily':
        lines.append('  snapshot : %s (changed=%s vs %s)' % (plan['snapshot'], plan['changed'], plan['previous_snapshot']))
        for s in plan['diff_summary'][:12]:
            lines.append('    | ' + s)
        aff = plan['affected']
        lines.append('  rebuild  : %s' % json.dumps(aff['rebuild']))
        lines.append('  journeys : %s' % json.dumps(aff['rerun_journeys']))
        lines.append('  regen_config: %s   graph_used: %s' % (aff['regen_config'], aff['graph_used']))
    for s in plan['steps']:
        lines.append('  [%s] %-16s %s' % (s['status'], s['id'], s['command']))
        if s.get('note') and s['status'] == 'planned':
            lines.append('        note: ' + s['note'])
    return '\n'.join(lines)


# --------------------------------------------------------------------------- cli
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='mode', required=True)

    d = sub.add_parser('daily', help='capture -> diff -> affected -> plan (--execute to run it)')
    d.add_argument('--execute', action='store_true')
    d.add_argument('--no-docker', action='store_true')
    d.add_argument('--out-dir', type=Path, default=C.REFRESH_DIR)
    d.add_argument('--snapshot-dir', type=Path, default=C.SNAPSHOT_DIR)

    w = sub.add_parser('weekly', help='full re-pin chain plan (--execute to run it in order)')
    w.add_argument('--execute', action='store_true')
    w.add_argument('--repos-root', type=Path, default=None)
    w.add_argument('--tag', default=None)
    w.add_argument('--destination', type=Path, default=None)
    w.add_argument('--out-dir', type=Path, default=C.REFRESH_DIR)

    s = sub.add_parser('status', help='what exists right now')
    s.add_argument('--json', action='store_true')

    args = ap.parse_args(argv)

    if args.mode == 'status':
        st = status()
        print(json.dumps(st, indent=2, sort_keys=True) if args.json else format_status(st))
        return 0

    if args.mode == 'daily':
        plan = plan_daily(with_docker=not args.no_docker, snapshot_dir=args.snapshot_dir)
        if args.execute:
            execute_daily(plan)
        path = write_plan(plan, args.out_dir)
        print(format_plan(plan))
        print('wrote ' + _rel(path))
        return 1 if plan['status'] == 'failed' else 0

    plan = plan_weekly(repos_root=args.repos_root, tag=args.tag, destination=args.destination)
    if args.execute:
        execute_weekly(plan)
    path = write_plan(plan, args.out_dir)
    print(format_plan(plan))
    print('wrote ' + _rel(path))
    return 1 if plan['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
