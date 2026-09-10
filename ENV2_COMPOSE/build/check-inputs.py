#!/usr/bin/env python3
"""Verify admitted build copies and checksums before invoking build-host.sh."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[2]


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('provenance', type=Path)
    parser.add_argument('--verify-modules', action='store_true', help='Also run go mod verify in each copy (no binaries executed)')
    args = parser.parse_args()
    provenance = json.loads(args.provenance.read_text())
    errors = []
    if provenance['status'] != 'prepared':
        errors.append('Source staging has not passed preparation and secret scanning')
    for repo in provenance['repositories']:
        target = Path(provenance['destination']) / repo['name']
        expected = repo.get('patched_copy_manifest', {}).get('files', [])
        if not expected:
            errors.append(repo['name'] + ': no admitted patched input manifest')
            continue
        expected_paths = {f['path'] for f in expected}
        actual_paths = {p.relative_to(target).as_posix() for p in target.rglob('*') if p.is_file()}
        if actual_paths != expected_paths:
            errors.append(repo['name'] + ': file inventory changed')
        for entry in expected:
            path = target / entry['path']
            if path.is_symlink() or not path.is_file() or sha(path) != entry['sha256']:
                errors.append(repo['name'] + '/' + entry['path'] + ': input hash differs')
        if args.verify_modules and target.is_dir():
            result = subprocess.run(['go', 'mod', 'verify'], cwd=target, text=True, capture_output=True)
            print(repo['name'] + ' module verification exit ' + str(result.returncode))
            print(result.stdout.strip())
            if result.returncode:
                errors.append(repo['name'] + ': ' + result.stderr.strip())
    for entry in provenance.get('patch_files', []):
        path = PROJECT / 'ENV2_COMPOSE/build/arena-patches' / entry['path']
        if not path.is_file() or sha(path) != entry['sha256']:
            errors.append('Arena module patch changed: ' + entry['path'])
    if provenance.get('patch_script_sha256') != sha(PROJECT / 'ENV2_COMPOSE/build/apply-arena-patches.sh'):
        errors.append('Arena patch script changed after preparation')
    if errors:
        print(json.dumps({'status': 'blocked', 'errors': errors}, indent=2))
        raise SystemExit(1)
    print(json.dumps({'status': 'passed', 'repositories': len(provenance['repositories']),
                      'source': provenance['destination']}, indent=2))


if __name__ == '__main__':
    main()
