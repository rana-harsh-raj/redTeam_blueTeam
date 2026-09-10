"""Optional isolated reader for the already-materialized unused Mozart volume."""
import hashlib
import json
import uuid
from common import run


def read_unused_mozart(record, material, files, project):
    volume = material.VOLUMES['config-mozart-mock']
    name = 'twin-audit-unused-' + uuid.uuid4().hex[:10]
    try:
        metadata = json.loads(run(['docker', 'volume', 'inspect', volume]))[0]
        labels = metadata.get('Labels', {})
        if labels.get('io.rzp-arena.generated') != '1' or labels.get('com.docker.compose.project') != project:
            raise RuntimeError('Generated volume ownership mismatch')
        run(['docker', 'image', 'inspect', material.IMAGE])
        script = (
            "import pathlib,hashlib,json,os; root=pathlib.Path('/audit'); os.listdir(root); files=list(root.rglob('*')); "
            "assert not any(p.is_symlink() for p in files); "
            "print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() "
            "for p in files if p.is_file()}))")
        actual = json.loads(run([
            'docker', 'run', '--rm', '--pull', 'never', '--name', name, '--network', 'none',
            '--read-only', '--user', '10001:10001', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '32', '--memory', '64m', '--cpus', '0.25',
            '--mount', 'type=volume,source=' + volume + ',target=/audit,readonly',
            '--entrypoint', 'python3', material.IMAGE, '-c', script]))
        expected = {key: hashlib.sha256(value).hexdigest() for key, value in files.items()}
        mismatch = sorted(key for key in actual.keys() | expected.keys()
                          if actual.get(key) != expected.get(key))
        record.update(status='passed' if not mismatch else 'blocked',
                      current_hashes_match=not mismatch, mismatched_relative_paths=mismatch,
                      consumer='isolated read-only audit helper', runtime_read_only=True,
                      inspection_network='none', inspection_only_mounted_volume=volume,
                      inspection_image=material.IMAGE, inspection_user='10001:10001', inspection_helper_removed=True)
        record.pop('reason', None)
    except Exception as exc:
        record['status'] = 'blocked'
        record['reason'] = 'Unused volume read could not complete: ' + type(exc).__name__
    finally:
        remaining = run(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$']).decode().split()
        if remaining:
            run(['docker', 'rm', '-f', *remaining])
        absent = not run(['docker', 'ps', '-aq', '--filter', 'name=^/' + name + '$']).strip()
        record['inspection_helper_removed'] = absent
        if not absent:
            record['status'] = 'blocked'
