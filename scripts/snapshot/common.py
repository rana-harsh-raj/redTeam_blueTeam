#!/usr/bin/env python3
"""Shared helpers for the M6 snapshot / refresh tooling (scripts/snapshot/*).

Everything here is pure filesystem + git + (optional, read-only) docker. No
secret material is ever read: ENV2_COMPOSE/secrets/*, config/generated/*,
seeds/generated/*, .env* files and .runtime/ are skipped everywhere.

Path layout (all resolved from this file, so the tooling is cwd-independent):

  ROOT              twin repo checkout
  ENV2              ROOT/ENV2_COMPOSE
  ACCEPTED          ROOT/.local/twin-repos/accepted   (admitted build copies)
  repos_root()      pristine source clones, from ROOT/.local/repos-root
  GRAPH_PATH        reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json (may not exist)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV2 = ROOT / 'ENV2_COMPOSE'
ACCEPTED = ROOT / '.local/twin-repos/accepted'
REPORTS_DOMAIN = ROOT / 'reports/domain'
GRAPH_PATH = REPORTS_DOMAIN / 'PAYOUTS_FUNCTIONAL_GRAPH.json'
SNAPSHOT_DIR = REPORTS_DOMAIN / 'snapshots'
RECIPES_DIR = REPORTS_DOMAIN / 'recipes'
REFRESH_DIR = REPORTS_DOMAIN / 'refresh'
MANIFEST_PATH = REPORTS_DOMAIN / 'SNAPSHOT_MANIFEST.json'

# Core services: real binaries built by ENV2_COMPOSE/build/build-host.sh from the
# admitted copies. Keys are repo names; values are the build-host.sh targets.
CORE_REPOS = {'payouts': 'payouts', 'ledger': 'ledger', 'fts': 'fts', 'cfa': 'cfa', 'x-balances': 'xbalances'}
# Image name per core repo (rzp-arena/<image>).
CORE_IMAGE = {'payouts': 'payouts', 'ledger': 'ledger', 'fts': 'fts', 'cfa': 'cfa', 'x-balances': 'xbalances'}
# Every source repo of the Payouts domain that the snapshot pins. Core repos come
# from the accepted copies (plus their pristine clone); the rest from clones only.
DOMAIN_REPOS = ('payouts', 'ledger', 'fts', 'cfa', 'x-balances',
                'workflows', 'batch', 'api', 'stork', 'mozart', 'dcs', 'splitz',
                'proto', 'kube-manifests', 'banking-accounts', 'x-account-statements',
                'governor', 'shield-sdk', 'payout-links', 'vendor-payments', 'virtual-account',
                'recon', 'edge', 'config-proto', 'goutils')
# Migration directories (relative to the repo copy) per service, in the order the
# twin's *-migrate compose services apply them.
MIGRATION_DIRS = {
    'payouts': ['internal/database/migrations'],
    'ledger': ['internal/database/migrations', 'internal/database/pg_migrations', 'internal/database/rx_migrations'],
    'fts': ['internal/migrations'],
    'cfa': ['internal/database/migrations'],
    'x-balances': ['internal/database/migrations'],
    'workflows': ['internal/database/migrations'],
    'batch': ['src/main/resources/db', 'src/main/resources/db/migration'],
}
SECRET_PATH_PARTS = ('secrets', 'generated', '.runtime', '.git', 'node_modules', 'vendor', '__pycache__')
SECRET_NAME_PREFIXES = ('.env', '.netrc', '.npmrc', '.pypirc')
SECRET_SUFFIXES = ('.pem', '.key', '.p12', '.pfx', '.keystore', '.pyc', '.db')


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def utc_stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def sha256_json(obj) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str).encode())


def digest_of_map(mapping: dict) -> str:
    """Order-independent digest of a {key: sha} map (same shape fingerprint.config_digest uses)."""
    return sha256_json(dict(sorted(mapping.items())))


def is_secret_path(path: Path) -> bool:
    parts = Path(path).parts
    if any(p in SECRET_PATH_PARTS for p in parts):
        return True
    name = Path(path).name
    return name.startswith(SECRET_NAME_PREFIXES) or name.endswith(SECRET_SUFFIXES) or name == '.DS_Store'


def hash_tree(base: Path, suffixes: tuple | None = None, relative_to: Path | None = None) -> dict:
    """{relative path: sha256} for every eligible regular file under base (sorted)."""
    base = Path(base)
    out = {}
    if not base.is_dir():
        return out
    rel_root = relative_to or base
    for path in sorted(base.rglob('*')):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(rel_root)
        if is_secret_path(rel):
            continue
        if suffixes and path.suffix not in suffixes:
            continue
        out[rel.as_posix()] = sha256_file(path)
    return out


def read_json(path: Path):
    return json.loads(Path(path).read_text())


def write_json(path: Path, payload, overwrite: bool = True) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise FileExistsError('refusing to overwrite ' + str(path))
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + '\n')
    tmp.replace(path)
    return path


def repos_root(root: Path = ROOT) -> Path | None:
    """The pristine clone directory named in .local/repos-root (None if absent)."""
    pointer = Path(root) / '.local/repos-root'
    candidates = []
    if pointer.is_file():
        candidates.append(Path(pointer.read_text().strip()))
    env = os.environ.get('REPOS_ROOT') or os.environ.get('TWIN_SOURCE_ROOT')
    if env:
        candidates.insert(0, Path(env))
    for c in candidates:
        if c.is_dir():
            return c.resolve()
    return None


def _git(path: Path, *args, timeout: int = 30) -> str | None:
    try:
        r = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def git_info(path: Path) -> dict:
    """sha/dirty/head_date/remote for a git work tree at exactly `path`.

    A copy without .git that lives INSIDE another repo (the accepted copies live
    under the twin checkout) must not report the enclosing repo's HEAD, so the
    toplevel is checked first.
    """
    path = Path(path)
    info = {'sha': None, 'dirty': None, 'head_date': None, 'remote': None, 'branch': None, 'is_git': False}
    if not path.is_dir():
        return info
    top = _git(path, 'rev-parse', '--show-toplevel')
    if not top or Path(top).resolve() != path.resolve():
        return info
    info['is_git'] = True
    info['sha'] = _git(path, 'rev-parse', 'HEAD')
    info['head_date'] = _git(path, 'log', '-1', '--format=%cI')
    status = _git(path, 'status', '--porcelain', '--untracked-files=normal')
    info['dirty'] = bool(status) if status is not None else None
    info['dirty_files'] = len(status.splitlines()) if status else 0
    info['branch'] = _git(path, 'rev-parse', '--abbrev-ref', 'HEAD')
    remote = _git(path, 'remote', 'get-url', 'origin')
    if remote:
        m = re.search(r'[:/]([^/:]+/[^/]+?)(?:\.git)?$', remote)
        info['remote'] = m.group(1) if m else remote
    return info


def latest_provenance(root: Path = ROOT) -> dict | None:
    """The newest build-evidence provenance.json whose destination is the accepted tree."""
    base = Path(root) / '.local/twin-repos'
    if not base.is_dir():
        return None
    best = None
    for prov in sorted(base.glob('build-evidence-*/provenance.json')):
        try:
            data = read_json(prov)
        except (OSError, ValueError):
            continue
        dest = Path(data.get('destination', '')).name
        if dest == 'accepted' or best is None:
            best = data
            best['_path'] = str(prov)
    return best


def load_graph(path: Path = GRAPH_PATH) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        graph = read_json(path)
    except ValueError:
        return None
    graph.setdefault('nodes', [])
    graph.setdefault('edges', [])
    graph.setdefault('families', [])
    return graph


def graph_version(path: Path = GRAPH_PATH) -> str | None:
    path = Path(path)
    return sha256_file(path) if path.is_file() else None


def edge_id(edge: dict) -> str:
    return '%s|%s|%s' % (edge.get('from'), edge.get('type'), edge.get('to'))


def import_fingerprint():
    """Import ENV2_COMPOSE/scripts/fingerprint.py (config_hashes/config_digest/...)."""
    scripts = ENV2 / 'scripts'
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import fingerprint  # noqa: E402
    return fingerprint


def load_compose(path: Path | None = None) -> dict:
    import yaml
    path = path or ENV2 / 'docker-compose.yml'
    return yaml.safe_load(Path(path).read_text()) or {}


def docker_available() -> tuple[bool, str | None]:
    try:
        r = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'], capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return False, 'docker binary not on PATH'
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, 'docker unavailable: %s' % exc
    if r.returncode != 0:
        return False, 'docker daemon not reachable: ' + (r.stderr.strip().splitlines() or ['unknown'])[-1][:200]
    return True, None


def docker_image_ids(names: list) -> dict:
    """{image name: id or None}; read-only `docker image inspect`."""
    ids = {}
    for name in names:
        try:
            r = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Id}}', name],
                               capture_output=True, text=True, timeout=30)
            ids[name] = r.stdout.strip() or None if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            ids[name] = None
    return ids


def compose_services(compose: dict) -> dict:
    return compose.get('services') or {}


def core_repo_for_service(service: str) -> str | None:
    """Compose service -> core repo (payouts-worker-x -> payouts, xbalances-server -> x-balances)."""
    head = service.split('-', 1)[0]
    if head == 'xbalances':
        return 'x-balances'
    return head if head in CORE_REPOS else None


def journeys_for_family(graph: dict, family_id: str) -> list:
    out = set()
    for fam in graph.get('families', []):
        if fam.get('id') == family_id:
            out.update(fam.get('journeys_existing') or [])
    prefix = 'journey:' + family_id.split(':', 1)[-1] + '/'
    for node in graph.get('nodes', []):
        if node.get('kind') == 'journey' and str(node.get('id', '')).startswith(prefix):
            out.add(node['id'])
    return sorted(out)
