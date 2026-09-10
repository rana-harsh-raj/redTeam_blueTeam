#!/usr/bin/env python3
"""Capture a stable local Compose boot without running application scenarios."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
from common import current_containers


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def boot_ids(project):
    return {r['Config']['Labels']['com.docker.compose.service']: r['Id']
            for r in current_containers(project)
            if r['Config']['Labels'].get('com.docker.compose.oneoff') != 'True'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New directory; existing paths are refused')
    parser.add_argument('--build-evidence', type=Path, required=True)
    parser.add_argument('--project', default='env2_compose')
    parser.add_argument('--read-unused-mozart', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error('--output must be a new directory to preserve prior captures')
    if not args.build_evidence.is_dir():
        parser.error('--build-evidence must name existing local build evidence')
    initial = boot_ids(args.project)
    output.mkdir(parents=True)
    summary = {'schema_version': 1, 'started_at': now(), 'status': 'in_progress',
               'project': args.project, 'scope': 'Current boot only; immutable binary scans are separate evidence',
               'initial_service_container_ids': initial, 'steps': []}
    path = output / 'audit-summary.json'
    def save():
        path.write_text(json.dumps(summary, indent=2) + '\n')
    save()
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    current = None
    try:
        for module, filename in [('current_boot.py', 'secret-and-endpoint-audit.json'),
                                 ('logical_stores.py', 'logical-store-secret-scan.json'),
                                 ('remaining_volumes.py', 'remaining-volume-secret-scan.json')]:
            command = [sys.executable, str(Path(__file__).with_name(module)),
                       '--output', str(output), '--project', args.project]
            if module == 'current_boot.py':
                command += ['--build-evidence', str(args.build_evidence.resolve())]
                if args.read_unused_mozart:
                    command += ['--read-unused-mozart']
            # Reader output is metadata only; keep exceptions/raw command output out of the report.
            current = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            current.communicate(timeout=600)
            code = current.returncode
            current = None
            report_path = output / filename
            row = {'reader': module, 'exit_code': code, 'report': filename, 'status': 'blocked'}
            if report_path.exists():
                report = json.loads(report_path.read_text())
                row['status'] = report.get('status', 'blocked') if code == 0 else 'blocked'
                row['sha256'] = hashlib.sha256(report_path.read_bytes()).hexdigest()
            summary['steps'].append(row)
            save()
            print(module + ': ' + row['status'], flush=True)
        final = boot_ids(args.project)
        summary['final_service_container_ids'] = final
        summary['same_running_boot'] = final == initial
        okay = final == initial and all(r['status'] in ('passed', 'passed_with_caveats')
                                       for r in summary['steps'])
        summary['status'] = 'passed_with_caveats' if okay else 'blocked'
    except (Exception, KeyboardInterrupt) as exc:
        summary['status'] = 'blocked'
        summary['error_type'] = type(exc).__name__
        if current is not None:
            current.send_signal(signal.SIGINT)
            try:
                current.communicate(timeout=20)
            except subprocess.TimeoutExpired:
                current.terminate()
                try:
                    current.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    current.kill()
                    current.communicate()
    finally:
        summary['finished_at'] = now()
        save()
    print('current_boot_audit: ' + summary['status'], flush=True)
    return 0 if summary['status'] == 'passed_with_caveats' else 1


if __name__ == '__main__':
    raise SystemExit(main())
