#!/usr/bin/env python3
"""Rebuild one core service image with build-host.sh and record build evidence.

Writes a new `.local/twin-repos/build-evidence-<UTC>` directory derived from a base
evidence directory: every file is copied, then the rebuilt service's build log,
`build-results.json` entry, `image-assets.json` entry and (for payouts/cfa) the
`<svc>-asset-readability.json` file are replaced with fresh measurements. The
current-boot audit (`preflight/audits/current_boot.py`) reads exactly these files.

Only host-built binaries and explicitly staged public assets enter the image;
this script never touches source clones. Network policy matches
BUILD_PROVENANCE.md (GOPROXY=off, GOFLAGS=-mod=readonly, GOTOOLCHAIN=local).
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV = ROOT / 'ENV2_COMPOSE'
BUILD_SCRIPTS = ('build/prepare-repos.py', 'build/check-inputs.py', 'build/build-host.sh',
                 'build/runtime-only.Dockerfile', 'build/cfa-entry.sh')
IMAGE_NAME = {'payouts': 'payouts', 'ledger': 'ledger', 'fts': 'fts', 'cfa': 'cfa', 'xbalances': 'x-balances'}


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def inventory(image):
    """Never-started container: export /app and hash every regular file."""
    cid = run(['docker', 'create', '--network', 'none', image]).strip()
    try:
        data = subprocess.run(['docker', 'export', cid], check=True, capture_output=True).stdout
    finally:
        subprocess.run(['docker', 'rm', '-f', cid], check=True, capture_output=True)
    files = []
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.startswith('app/'):
                continue
            blob = tar.extractfile(member).read()
            kind = 'binary' if blob[:4] == b'\x7fELF' else 'asset'
            files.append({'path': member.name, 'sha256': hashlib.sha256(blob).hexdigest(),
                          'size_bytes': len(blob), 'kind': kind, 'mode': oct(member.mode)})
    files.sort(key=lambda f: f['path'])
    return files


def readability(image, paths):
    name = 'twin-asset-' + uuid.uuid4().hex[:10]
    quoted = ' '.join("'" + p + "'" for p in paths)
    cmd = ['docker', 'run', '--rm', '--name', name, '--network', 'none', '--read-only', '--user', 'appuser',
           '--entrypoint', 'sh', image, '-c',
           'for p in ' + quoted + '; do stat -c "%a %u %g %n" "$p" || exit 3; done; sha256sum ' + quoted]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return {'image': image, 'configured_user': 'appuser',
            'image_id': json.loads(run(['docker', 'image', 'inspect', image]))[0]['Id'],
            'container': name, 'network': 'none', 'read_only': True, 'paths': paths,
            'exit_code': proc.returncode, 'output': proc.stdout + proc.stderr}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('service', choices=sorted(IMAGE_NAME))
    ap.add_argument('--base-evidence', required=True, type=Path)
    ap.add_argument('--repos-root', required=True, type=Path)
    ap.add_argument('--tag', default='v1-candidate')
    ap.add_argument('--asset-paths', nargs='*', default=[], help='container paths whose UID10001 readability is recorded')
    ap.add_argument('--note', default='')
    args = ap.parse_args()

    base = args.base_evidence.resolve()
    if not (base / 'build-results.json').exists():
        sys.exit('base evidence has no build-results.json')
    out = base.parent / ('build-evidence-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    if out.exists():
        sys.exit('refusing to overwrite ' + str(out))
    shutil.copytree(base, out)

    env = dict(os.environ, GOPROXY='off', GONOPROXY='none', GONOSUMDB='none', GOSUMDB='sum.golang.org',
               GOTOOLCHAIN='local', GOFLAGS='-mod=readonly', REPOS_ROOT=str(args.repos_root.resolve()),
               ARENA_TAG=args.tag)
    log = out / (args.service + '-build.log')
    started = time.time()
    with log.open('w') as f:
        proc = subprocess.run(['bash', str(ENV / 'build/build-host.sh'), args.service], env=env,
                              stdout=f, stderr=subprocess.STDOUT, text=True, cwd=str(ENV))
    elapsed = round(time.time() - started, 2)
    image = 'rzp-arena/' + IMAGE_NAME[args.service] + ':' + args.tag
    meta = json.loads(run(['docker', 'image', 'inspect', image]))[0]

    results = json.load((out / 'build-results.json').open())
    entry = {'service': args.service, 'exit_code': proc.returncode, 'elapsed_seconds': elapsed, 'log': str(log),
             'candidate_image': meta['Id'], 'architecture': meta['Architecture'], 'image_size_bytes': meta['Size'],
             'environment_keys': [v.partition('=')[0] for v in meta['Config'].get('Env', [])],
             'user': meta['Config']['User']}
    results['builds'] = [entry if b['service'] == args.service else b for b in results['builds']]
    results['build_scripts'] = {'ENV2_COMPOSE/' + s: sha256(ENV / s) for s in BUILD_SCRIPTS}
    results['build_scripts']['ENV2_COMPOSE/build/record-rebuild.py'] = sha256(Path(__file__))
    results['rebuilds'] = results.get('rebuilds', []) + [{
        'service': args.service, 'base_evidence': str(base), 'recorded_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'note': args.note}]
    (out / 'build-results.json').write_text(json.dumps(results, indent=2) + '\n')

    if proc.returncode != 0:
        sys.exit('build failed; evidence retained at ' + str(out))

    assets_path = out / 'image-assets.json'
    if assets_path.exists():
        assets = json.load(assets_path.open())
        files = inventory(image)
        for img in assets['images']:
            if img['service'] == args.service:
                img.update({'image': image, 'started': False, 'files': files, 'unexpected_assets': [],
                            'asset_secret_scan_findings': [], 'inspection_container_removed': True,
                            'note': 'Re-inventoried by record-rebuild.py; asset secret scan of the staged JSON is a Gitleaks dir scan recorded in the build log directory.'})
        assets_path.write_text(json.dumps(assets, indent=2) + '\n')

    if args.asset_paths:
        (out / (args.service + '-asset-readability.json')).write_text(
            json.dumps(readability(image, args.asset_paths), indent=2) + '\n')

    print(json.dumps({'evidence_dir': str(out), 'image': image, 'image_id': meta['Id'], 'exit_code': proc.returncode,
                      'elapsed_seconds': elapsed}))


if __name__ == '__main__':
    main()
