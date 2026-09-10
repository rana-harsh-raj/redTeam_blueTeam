#!/usr/bin/env python3
"""Hash-bind domain artifacts and reports without a self-referential commit hash.

The generated manifest excludes itself and acceptance.json. Acceptance includes
the manifest hash. The evaluated source commit is recorded by acceptance, which
is emitted after commit into ignored local evidence, then copied for delivery.
"""
import argparse
import hashlib
import json
from pathlib import Path

DOMAIN = Path(__file__).resolve().parents[1]
ROOT = DOMAIN.parents[1]
MANIFEST = DOMAIN / 'artifacts/artifact-hashes.json'
EXCLUDE = {'artifacts/artifact-hashes.json', 'artifacts/acceptance.json'}


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def files():
    for directory in [DOMAIN, ROOT / 'reports/source-to-pay-replica']:
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(directory)
            if any(part in {'.local', '__pycache__', '.runtime', '.build', '.git'} for part in rel.parts):
                continue
            if directory == DOMAIN and str(rel) in EXCLUDE:
                continue
            yield path


def create():
    payload = {'algorithm': 'sha256',
               'exclusions': sorted(EXCLUDE),
               'files': {str(p.relative_to(ROOT)): sha256(p) for p in files()}}
    MANIFEST.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'files': len(payload['files']), 'manifest_sha256': sha256(MANIFEST)}))


def verify():
    payload = json.loads(MANIFEST.read_text())
    failures = []
    for relative, expected in payload['files'].items():
        path = ROOT / relative
        if not path.is_file() or sha256(path) != expected:
            failures.append(relative)
    actual = {str(p.relative_to(ROOT)) for p in files()}
    unbound = sorted(actual - payload['files'].keys())
    result = {'passed': not failures and not unbound,
              'changed_or_missing': failures, 'unbound': unbound,
              'manifest_sha256': sha256(MANIFEST)}
    print(json.dumps(result))
    return result['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if args.verify:
        raise SystemExit(0 if verify() else 1)
    create()
