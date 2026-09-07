#!/usr/bin/env python3
"""Read-only verification of protected checkouts and pre-existing containers.

Concurrent changes are reported as changes; this cannot attribute their author.
No Docker inspect environment variables or file contents are recorded.
"""
import hashlib
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

DOMAIN = pathlib.Path(__file__).resolve().parents[1]


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify():
    baseline_path = DOMAIN / 'artifacts/isolation-before.json'
    baseline = json.loads(baseline_path.read_text())
    results = []
    for before in baseline['worktrees']:
        path = before['path']
        files = {}
        for name in command('git', '-C', path, 'ls-files', '--cached', '--others', '--exclude-standard').splitlines():
            target = pathlib.Path(path) / name
            if target.is_file():
                files[name] = digest(target.read_bytes())
        changed = sorted(k for k in files.keys() | before['files'].keys()
                         if files.get(k) != before['files'].get(k))
        head = command('git', '-C', path, 'rev-parse', 'HEAD')
        status = command('git', '-C', path, 'status', '--porcelain=v1')
        diff_hash = digest(subprocess.check_output(['git', '-C', path, 'diff', 'HEAD', '--binary']))
        results.append({'path': path, 'head_before': before['head'], 'head_after': head,
                        'changed_files': changed, 'status_unchanged': status == before['status'],
                        'diff_unchanged': diff_hash == before['diff_hash'],
                        'unchanged': not changed and head == before['head'] and
                        status == before['status'] and diff_hash == before['diff_hash']})
    containers = []
    for before in baseline['containers']:
        proc = subprocess.run(['docker', 'inspect', before['Id']], capture_output=True, text=True)
        if proc.returncode:
            containers.append({'id': before['Id'], 'name': before['Name'], 'unchanged': False,
                               'reason': 'pre-existing container no longer inspectable'})
            continue
        value = json.loads(proc.stdout)[0]
        after = {k: value[k] for k in ['Id', 'Name', 'Image', 'Created']}
        after.update(StartedAt=value['State']['StartedAt'], RestartCount=value['RestartCount'])
        containers.append({'before': before, 'after': after,
                           'running': value['State']['Running'],
                           'unchanged': before == after and value['State']['Running']})
    result = {'captured_at': datetime.now(timezone.utc).isoformat(),
              'baseline_sha256': digest(baseline_path.read_bytes()),
              'worktrees': results, 'containers': containers,
              'protected_worktrees_unchanged': all(x['unchanged'] for x in results),
              'preexisting_containers_unchanged': all(x['unchanged'] for x in containers),
              'limitation': 'Concurrent external changes cannot be attributed by snapshot comparison.'}
    (DOMAIN / 'artifacts/isolation-after.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ['protected_worktrees_unchanged', 'preexisting_containers_unchanged']}))
    return result


if __name__ == '__main__':
    r = verify()
    sys.exit(0 if r['protected_worktrees_unchanged'] and r['preexisting_containers_unchanged'] else 1)
