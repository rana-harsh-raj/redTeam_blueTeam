#!/usr/bin/env python3
"""Read-only arena resource measurements; optional explicitly supplied workload wrapper."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / 'ENV2_COMPOSE'
PHASES = ('idle', 'successful_payout', 'full_verifier')
GIB = 1024 ** 3


class MeasurementError(Exception):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def run(args, timeout=60):
    # Never expose docker errors, commands, environment, config or workload output.
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise MeasurementError('Measurement command unavailable or timed out') from None
    if result.returncode:
        raise MeasurementError('Read-only measurement command failed')
    return result.stdout


def size_bytes(value):
    match = re.fullmatch(r'\s*([0-9]+(?:\.[0-9]+)?)\s*([kKMGTPE]?i?B)\s*', value)
    if not match:
        raise MeasurementError('Docker returned an unsupported size unit')
    amount, unit = match.groups()
    exponent = 'BKMGTPE'.index(unit[0].upper()) if unit != 'B' else 0
    return float(amount) * (1024 if 'i' in unit else 1000) ** exponent


def empty_profile():
    return {'schema_version': 1, 'phases': {
        phase: {'observed': False, 'sample_count': 0, 'peak_cpu_cores': None,
                'peak_memory_gib': None, 'disk_gib': None} for phase in PHASES}}


def save_phase(output, phase, result):
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(str(output) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        profile = json.loads(output.read_text()) if output.exists() else empty_profile()
        if profile.get('schema_version') != 1:
            raise MeasurementError('Existing profile has an unsupported schema')
        profile['phases'][phase] = result
        profile['updated_at'] = now()
        temporary = output.with_name(output.name + '.' + str(os.getpid()) + '.tmp')
        temporary.write_text(json.dumps(profile, indent=2) + '\n')
        temporary.replace(output)


class Sampler:
    def __init__(self, project, disk_paths):
        self.project = project
        self.disk_paths = disk_paths

    def containers(self):
        ids = run(['docker', 'ps', '-aq', '--no-trunc', '--filter',
                   'label=com.docker.compose.project=' + self.project]).split()
        if not ids:
            raise MeasurementError('No arena containers found; start the arena separately')
        template = ('{"id":{{json .Id}},"image":{{json .Image}},'
                    '"running":{{json .State.Running}},"size_rw":{{json .SizeRw}},'
                    '"mounts":{{json .Mounts}},"log_path":{{json .LogPath}},'
                    '"service":{{json (index .Config.Labels "com.docker.compose.service")}},'
                    '"working_dir":{{json (index .Config.Labels "com.docker.compose.project.working_dir")}}}')
        rows = [json.loads(line) for line in run(
            ['docker', 'inspect', '--size', '--format', template, *ids]).splitlines()]
        for row in rows:
            if not row['working_dir'] or Path(row['working_dir']).resolve() != COMPOSE.resolve():
                raise MeasurementError('Compose project contains a container from another workspace')
        if not any(row['running'] for row in rows):
            raise MeasurementError('No running arena containers to measure')
        return rows

    def disk(self, rows):
        image_ids = sorted({r['image'] for r in rows})
        image_bytes = sum(int(x) for x in run(
            ['docker', 'image', 'inspect', '--format', '{{.Size}}', *image_ids]).splitlines())
        volumes = {m['Name'] for r in rows for m in r['mounts'] if m['Type'] == 'volume'}
        volume_rows = json.loads(run(['docker', 'system', 'df', '-v', '--format', '{{json .Volumes}}'])) or []
        sizes = {v['Name']: size_bytes(v['Size']) for v in volume_rows if v['Name'] in volumes}
        if volumes - sizes.keys():
            raise MeasurementError('Arena volume size is unavailable')
        paths = {Path(m['Source']).resolve() for r in rows for m in r['mounts'] if m['Type'] == 'bind'}
        paths.update(p.resolve() for p in self.disk_paths)
        # Avoid counting a mounted file twice when its parent is already included.
        paths = sorted(p for p in paths if not any(q != p and q in p.parents for q in paths))
        if any(not p.exists() for p in paths):
            raise MeasurementError('A measured bind or explicit disk path is inaccessible')
        bind_bytes = sum(int(run(['du', '-sk', str(p)]).split()[0]) * 1024 for p in paths)
        writable_bytes = sum(max(0, int(r['size_rw'])) for r in rows)
        # Logs live inside the Docker VM on Desktop; count only logs actually readable.
        logs = {Path(r['log_path']) for r in rows if r['log_path']}
        accessible_logs = [p for p in logs if p.is_file() and os.access(p, os.R_OK)]
        log_bytes = sum(p.stat().st_size for p in accessible_logs)
        host = shutil.disk_usage(ROOT)
        total = image_bytes + sum(sizes.values()) + writable_bytes + bind_bytes + log_bytes
        return {
            'disk_gib': total / GIB,
            'components_bytes': {'image_virtual_size_sum': image_bytes,
                                 'named_and_anonymous_volumes': sum(sizes.values()),
                                 'container_writable_layers': writable_bytes,
                                 'binds_and_explicit_paths_allocated': bind_bytes,
                                 'accessible_container_logs': log_bytes},
            'image_count': len(image_ids), 'volume_count': len(volumes),
            'inaccessible_log_count': len(logs) - len(accessible_logs),
            'host_filesystem_used_gib_context_only': host.used / GIB,
            'host_filesystem_free_gib_context_only': host.free / GIB,
        }

    def sample(self):
        start = now()
        rows = self.containers()
        running = {r['id']: r for r in rows if r['running']}
        stat_rows = [json.loads(line) for line in run([
            'docker', 'stats', '--no-stream', '--no-trunc', '--format', '{{json .}}',
            *sorted(running)]).splitlines()]
        measured = []
        for row in stat_rows:
            identifier = row.get('ID', row.get('Container', ''))
            if identifier not in running:
                raise MeasurementError('Stats returned an unexpected container')
            cpu = float(row['CPUPerc'].rstrip('%')) / 100
            memory = size_bytes(row['MemUsage'].split('/')[0])
            if not math.isfinite(cpu) or cpu < 0 or not math.isfinite(memory) or memory < 0:
                raise MeasurementError('Invalid CPU or memory measurement')
            measured.append({'container_id': identifier[:12], 'service': running[identifier]['service'],
                             'cpu_cores': cpu, 'memory_gib': memory / GIB})
        if len(measured) != len(running):
            raise MeasurementError('Running-container membership changed during stats collection')
        disk = self.disk(rows)
        return {'started_at': start, 'finished_at': now(), 'container_count': len(measured),
                'cpu_cores': sum(r['cpu_cores'] for r in measured),
                'memory_gib': sum(r['memory_gib'] for r in measured),
                'containers': measured, 'disk': disk}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', required=True, choices=PHASES)
    parser.add_argument('--project', default=os.environ.get('COMPOSE_PROJECT_NAME', 'env2_compose'))
    parser.add_argument('--output', type=Path, default=ROOT / 'reports/implementation/resource-profile.json')
    parser.add_argument('--duration', type=float, default=60, help='Watcher seconds; command wrapper timeout when -- is supplied')
    parser.add_argument('--interval', type=float, default=2, help='Seconds between sample starts; Docker collection can take longer')
    parser.add_argument('--disk-path', type=Path, action='append', default=[], help='Additional existing archive/staging path; size only is read')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='Optional workload argv after --; stdout/stderr suppressed')
    args = parser.parse_args(argv)
    if args.interval < 0.25 or args.duration < args.interval * 2:
        parser.error('duration must be at least twice interval, and interval at least 0.25 seconds')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    samples, errors, child = [], [], None
    started = now()
    interrupted = False
    timed_out = False
    sampler = Sampler(args.project, [ROOT / 'reports/implementation', *args.disk_path])
    result = {'observed': False, 'sample_count': 0, 'peak_cpu_cores': None,
              'peak_memory_gib': None, 'disk_gib': None, 'started_at': started,
              'project': args.project, 'interval_seconds': args.interval,
              'mode': 'command_wrapper' if command else 'duration_watcher',
              'accounting': {
                  'cpu_memory': 'Arena containers only. Docker stats CPU percent / 100 gives cores; memory is Docker CLI cache-adjusted usage.',
                  'disk': 'Measured component sum; each image counted once at virtual size, so shared layers are conservatively double-counted across images. Docker volume sizes are rounded by its CLI.',
                  'excluded': ['Docker daemon and VM CPU/RAM', 'build cache and unrelated containers/images/volumes',
                               'inaccessible daemon logs', 'unlisted archive/staging paths'],
                  'host_filesystem': 'Used/free context is host-wide, never added to arena disk_gib.',
                  'sampling': 'Sampled peaks may miss sub-interval spikes. Profile observation does not attest acceptance success.'}}
    try:
        # Verify target before starting the explicit workload. This baseline is not a phase sample.
        sampler.containers()
        if command:
            child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline and (child is None or child.poll() is None):
            tick = time.monotonic()
            try:
                samples.append(sampler.sample())
            except MeasurementError as exc:
                errors.append({'at': now(), 'message': str(exc)})
            remaining = min(args.interval - (time.monotonic() - tick), deadline - time.monotonic())
            if remaining > 0:
                if child:
                    try:
                        child.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        pass
                else:
                    time.sleep(remaining)
        if child and child.poll() is None:
            timed_out = True
    except KeyboardInterrupt:
        interrupted = True
    except (MeasurementError, OSError, ValueError, KeyError) as exc:
        errors.append({'at': now(), 'message': str(exc) if isinstance(exc, MeasurementError) else 'Measurement could not be parsed or executed'})
    finally:
        if child and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    result.update({'finished_at': now(), 'sample_count': len(samples), 'samples': samples, 'errors': errors,
                   'interrupted': interrupted, 'timed_out': timed_out,
                   'workload_exit_code': child.returncode if child else None,
                   'workload_status': ('passed' if child.returncode == 0 else 'failed') if child else 'not_attested'})
    result['observed'] = (len(samples) >= 2 and not errors and not interrupted and not timed_out
                          and (child is None or child.returncode == 0))
    if samples:
        result.update({'peak_cpu_cores': max(s['cpu_cores'] for s in samples),
                       'peak_memory_gib': max(s['memory_gib'] for s in samples),
                       'disk_gib': max(s['disk']['disk_gib'] for s in samples)})
    save_phase(args.output, args.phase, result)
    print(json.dumps({'phase': args.phase, 'observed': result['observed'],
                      'sample_count': len(samples), 'workload_status': result['workload_status'],
                      'profile': str(args.output)}))
    if child and child.returncode:
        return child.returncode if child.returncode > 0 else 128 - child.returncode
    return 0 if result['observed'] else 2


if __name__ == '__main__':
    sys.exit(main())
