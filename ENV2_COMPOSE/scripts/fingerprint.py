#!/usr/bin/env python3
"""Record what this arena boot actually is, so a later gate can prove that
every retained run came from the same tree, the same images and the same boot.

Written by scripts/up.sh at the end of a successful boot to
ENV2_COMPOSE/.runtime/arena-fingerprint.json (git-ignored). Every
run-producing command copies that file into its run directory as
`arena-fingerprint.json`.

Contents are deliberately non-secret: file digests of the *inputs* (compose
file, config templates and generators, seeds, substitutes, verifier, cron
driver), the route profile, the rzp-arena image ids referenced by the compose
file, the checkout's git HEAD, the running container ids (boot identity), and a
boot id plus timestamp. Rendered configuration under config/generated/ and
seeds/generated/ and anything under secrets/ is never read: those embed
generated credentials.

`config_hashes` is the staleness-relevant part and is reproducible from the
working tree alone (no Docker): scripts/local-acceptance.py imports
`config_hashes()` and `config_digest()` to recompute it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

SCHEMA_VERSION = 1
COMPOSE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = COMPOSE_ROOT.parent

# Directory trees hashed in full, with the generated (credential-bearing or
# derived) subtrees excluded.
TREES = (
    ('config/templates', ()),
    ('seeds', ('seeds/generated',)),
    ('substitutes', ()),
    ('verifier', ()),
)
# Trees restricted to a suffix; None means "every file".
TREE_SUFFIX = {'substitutes': '.py', 'verifier': '.py'}
SINGLE_FILES = (
    'docker-compose.yml',
    'config/routes.py',
    'config/generate.py',
    'scripts/cron-driver/driver.py',
)
SKIP_DIR_NAMES = {'__pycache__', '.git', 'node_modules', '.pytest_cache'}
SKIP_SUFFIXES = {'.pyc', '.pyo'}
SKIP_NAMES = {'.DS_Store'}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _eligible(path: Path) -> bool:
    if path.name in SKIP_NAMES or path.suffix in SKIP_SUFFIXES:
        return False
    return not any(part in SKIP_DIR_NAMES for part in path.parts)


def config_hashes(compose_root: Path | str = COMPOSE_ROOT) -> dict:
    """sha256 of every fingerprinted input file, keyed by ENV2_COMPOSE-relative path.

    Pure filesystem: identical for the same tree on any machine, at any time.
    """
    root = Path(compose_root).resolve()
    hashes: dict[str, str] = {}
    for relative in SINGLE_FILES:
        path = root / relative
        if path.is_file():
            hashes[relative] = sha256_file(path)
    for tree, excluded in TREES:
        base = root / tree
        if not base.is_dir():
            continue
        suffix = TREE_SUFFIX.get(tree)
        for path in sorted(base.rglob('*')):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(root).as_posix()
            if any(relative == e or relative.startswith(e + '/') for e in excluded):
                continue
            if suffix is not None and path.suffix != suffix:
                continue
            if not _eligible(path.relative_to(root)):
                continue
            hashes[relative] = sha256_file(path)
    return dict(sorted(hashes.items()))


def config_digest(hashes: dict) -> str:
    """One digest over the whole input set; order-independent and stable."""
    payload = json.dumps(dict(sorted(hashes.items())), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _docker(args, timeout=60):
    try:
        result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def compose_image_names(compose_root: Path) -> list:
    """rzp-arena/* images named by the compose file, with ${ARENA_TAG} resolved."""
    text = (compose_root / 'docker-compose.yml').read_text()
    tag = os.environ.get('ARENA_TAG')
    if not tag:
        # scripts/up.sh always passes --env-file .env.arena, so the booted tag is the one
        # recorded there; the compose default (:local) is never what actually runs.
        env_file = compose_root / '.env.arena'
        if env_file.is_file():
            for line in env_file.read_text().splitlines():
                m = re.match(r'\s*ARENA_TAG\s*=\s*([^#\s]+)', line)
                if m:
                    tag = m.group(1).strip('"\'')
        tag = tag or 'local'
    names = set()
    for raw in re.findall(r'^\s*image:\s*(\S+)\s*$', text, re.MULTILINE):
        if not raw.startswith('rzp-arena/'):
            continue
        names.add(re.sub(r'\$\{ARENA_TAG:-[^}]*\}|\$\{ARENA_TAG\}', tag, raw))
    return sorted(names)


def image_ids(compose_root: Path) -> dict:
    ids = {}
    for name in compose_image_names(compose_root):
        out = _docker(['image', 'inspect', '--format', '{{.Id}}', name])
        ids[name] = out.strip() if out and out.strip() else None
    return ids


def service_container_ids(project: str) -> dict:
    """service -> container Id for the running boot; the audit records the same map."""
    out = _docker(['ps', '-aq', '--no-trunc', '--filter', 'label=com.docker.compose.project=' + project])
    if out is None:
        return {}
    ids = out.split()
    if not ids:
        return {}
    template = ('{"id":{{json .Id}},"service":{{json (index .Config.Labels '
                '"com.docker.compose.service")}},"oneoff":{{json (index .Config.Labels '
                '"com.docker.compose.oneoff")}}}')
    out = _docker(['inspect', '--format', template, *ids])
    if out is None:
        return {}
    mapping = {}
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get('oneoff') == 'True' or not row.get('service'):
            continue
        mapping[row['service']] = row['id']
    return dict(sorted(mapping.items()))


def git_head(repo_root: Path):
    try:
        result = subprocess.run(['git', '-C', str(repo_root), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None if result.returncode == 0 else None


def build(compose_root: Path, repo_root: Path, project: str, with_docker: bool = True) -> dict:
    hashes = config_hashes(compose_root)
    payload = {
        'schema_version': SCHEMA_VERSION,
        'boot_id': str(uuid.uuid4()),
        'recorded_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'route_profile': os.environ.get('ARENA_ROUTE_PROFILE') or 'monolith',
        'compose_project': project,
        'git_head': git_head(repo_root),
        'config_digest': config_digest(hashes),
        'config_hashes': hashes,
        'images': image_ids(compose_root) if with_docker else {},
        'service_container_ids': service_container_ids(project) if with_docker else {},
        'note': ('Input digests, image ids and boot container ids only. Rendered config '
                 '(config/generated, seeds/generated) and secrets/ are never read.'),
    }
    # Compose-built substitutes may have no local tag at boot time; the gate binds
    # the five core service images, so an unresolved substitute is recorded, not fatal.
    payload['images_unresolved'] = sorted(k for k, v in payload['images'].items() if not v)
    payload['images_available'] = bool(payload['images']) and not payload['images_unresolved']
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=COMPOSE_ROOT / '.runtime/arena-fingerprint.json')
    parser.add_argument('--project', default=os.environ.get('COMPOSE_PROJECT_NAME') or 'env2_compose')
    parser.add_argument('--no-docker', action='store_true',
                        help='Hash the working tree only; do not read image or container ids.')
    parser.add_argument('--print-digest', action='store_true',
                        help='Print only the config digest of the working tree and exit.')
    args = parser.parse_args()
    if args.print_digest:
        print(config_digest(config_hashes(COMPOSE_ROOT)))
        return 0
    payload = build(COMPOSE_ROOT, REPO_ROOT, args.project, with_docker=not args.no_docker)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + '.tmp')
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    temporary.replace(args.output)
    print('arena fingerprint: boot_id=%s route_profile=%s config_digest=%s files=%d -> %s'
          % (payload['boot_id'], payload['route_profile'], payload['config_digest'][:12],
             len(payload['config_hashes']), args.output))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
