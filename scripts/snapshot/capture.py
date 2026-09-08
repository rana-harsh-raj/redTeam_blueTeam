#!/usr/bin/env python3
"""Capture a domain snapshot: reports/domain/snapshots/<UTC>.json (+ latest.json pointer).

The snapshot pins everything the twin is built from, so a later diff can say
exactly what moved: graph version, every source repo (pristine clone + admitted
copy), per-service recipe hashes, config hashes (from
ENV2_COMPOSE/scripts/fingerprint.py, imported not duplicated), schema versions
(migration files), contract hashes (proto modules, substitute CONTRACT.md,
batch payout types), feature-flag fixtures, image digests (docker image inspect
when docker is reachable, else null + reason) and the compose service list.

Runs without docker. Never reads ENV2_COMPOSE/secrets/*, config/generated/*,
seeds/generated/*, .env* or .runtime/*; records hashes and ids only.

Usage:
  python3 scripts/snapshot/capture.py [--no-docker] [--out-dir DIR] [--print]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import recipes as R  # noqa: E402

SNAPSHOT_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- pieces
def snapshot_repos(ctx: dict) -> dict:
    out = {}
    prov = ctx.get('provenance') or {}
    prov_by_name = {e.get('name'): e for e in prov.get('repositories', [])}
    for name, rec in sorted(ctx['repo_info'].items()):
        if not rec.get('present'):
            continue
        entry = {'name': name, 'repository': rec.get('repository'), 'path': rec.get('clone_path'), 'sha': rec.get('sha'),
                 'dirty': rec.get('dirty'), 'head_date': rec.get('head_date'),
                 'kind': 'clone' if rec.get('clone_path') else 'accepted-only',
                 'accepted_path': rec.get('accepted_path'), 'accepted_source_commit': rec.get('accepted_source_commit'),
                 'accepted_matches_provenance': None, 'accepted_tree_digest': None}
        acc = rec.get('accepted_path')
        if acc and Path(acc).is_dir():
            entry['kind'] = 'clone+accepted' if rec.get('clone_path') else 'accepted-only'
            manifest = ((prov_by_name.get(name) or {}).get('patched_copy_manifest') or {}).get('files') or []
            expected = {f['path']: f['sha256'] for f in manifest if 'path' in f and 'sha256' in f}
            actual = {}
            for p in sorted(Path(acc).rglob('*')):
                if p.is_file() and not p.is_symlink():
                    actual[p.relative_to(acc).as_posix()] = C.sha256_file(p)
            entry['accepted_tree_digest'] = C.digest_of_map(actual)
            entry['accepted_file_count'] = len(actual)
            entry['accepted_matches_provenance'] = (actual == expected) if expected else None
            if rec.get('sha') and rec.get('accepted_source_commit') and rec['sha'] != rec['accepted_source_commit']:
                entry['clone_ahead_of_accepted'] = True
        out[name] = entry
    return out


def snapshot_graph(graph_path: Path) -> dict:
    graph = C.load_graph(graph_path)
    out = {'path': str(graph_path), 'version': C.graph_version(graph_path), 'present': graph is not None,
           'node_count': 0, 'edge_count': 0, 'node_ids': [], 'edge_ids': [], 'family_ids': [], 'node_digests': {}}
    if graph is None:
        return out
    out['node_ids'] = sorted(n['id'] for n in graph.get('nodes', []) if n.get('id'))
    out['edge_ids'] = sorted(C.edge_id(e) for e in graph.get('edges', []))
    out['family_ids'] = sorted(f['id'] for f in graph.get('families', []) if f.get('id'))
    out['node_count'] = len(out['node_ids'])
    out['edge_count'] = len(out['edge_ids'])
    out['node_digests'] = {n['id']: C.sha256_json(n) for n in graph.get('nodes', []) if n.get('id')}
    out['ids_digest'] = C.sha256_json({'nodes': out['node_ids'], 'edges': out['edge_ids']})
    return out


def snapshot_schemas(ctx: dict) -> dict:
    out = {}
    for repo, dirs in C.MIGRATION_DIRS.items():
        repo_dir = R._repo_dir(repo, ctx)
        if not repo_dir:
            continue
        files = R._migration_files(repo_dir, dirs)
        out[repo] = {'source': str(repo_dir), 'dirs': [d for d in dirs if (repo_dir / d).is_dir()],
                     'files': files, 'digest': C.digest_of_map(files), 'count': len(files)}
    return out


def _proto_files(proto_root: Path, modules: list) -> dict:
    files = {}
    for module in modules:
        target = proto_root / module
        if target.is_file():
            files[module] = C.sha256_file(target)
        elif target.is_dir():
            for p in sorted(target.rglob('*.proto')):
                files[p.relative_to(proto_root).as_posix()] = C.sha256_file(p)
    return files


def snapshot_contracts(ctx: dict) -> dict:
    proto_root = Path(ctx['repos_root']) / 'proto' if ctx.get('repos_root') else None
    proto = {}
    for repo in ('payouts', 'ledger'):
        repo_dir = R._repo_dir(repo, ctx)
        modules = R._proto_modules(repo_dir) if repo_dir else []
        files = _proto_files(proto_root, modules) if proto_root and proto_root.is_dir() else {}
        proto[repo] = {'modules': modules, 'proto_root': str(proto_root) if proto_root else None,
                       'files': files, 'digest': C.digest_of_map(files)}
    subs = {}
    for contract in sorted((ctx['env2'] / 'substitutes').glob('*/CONTRACT.md')):
        subs[contract.parent.name] = C.sha256_file(contract)
    batch_types = {}
    batch = R._clone_dir('batch', ctx)
    if batch and (batch / 'src/main/resources').is_dir():
        for p in sorted((batch / 'src/main/resources').rglob('payout*.json')):
            batch_types[p.relative_to(batch).as_posix()] = C.sha256_file(p)
    out = {'proto': proto, 'substitutes': subs, 'batch_types': batch_types,
           'batch_types_digest': C.digest_of_map(batch_types)}
    out['digest'] = C.sha256_json({'proto': {k: v['digest'] for k, v in proto.items()}, 'substitutes': subs,
                                   'batch_types': out['batch_types_digest']})
    return out


def _flag_items_dcs(data: dict) -> dict:
    """flag id -> sha of its per-merchant values: dcs/<namespace>/<flag>."""
    values: dict = {}
    for merchant, body in (data.get('merchants') or {}).items():
        for namespace, flags in ((body or {}).get('dcs') or {}).items():
            if not isinstance(flags, dict):
                continue
            for flag, value in flags.items():
                values.setdefault('dcs/%s/%s' % (namespace, flag), {})[merchant] = value
    return {k: C.sha256_json(v) for k, v in values.items()}


def _flag_items_splitz(experiments: dict, variant_table: dict) -> dict:
    items = {}
    for exp_id, body in (experiments.get('experiments') or {}).items():
        name = (body or {}).get('name') or exp_id
        items['splitz/%s' % name] = C.sha256_json({'id': exp_id, **(body or {})})
    for key, value in (variant_table or {}).items():
        if not key.startswith('_'):
            items['splitz/variant_table/%s' % key] = C.sha256_json(value)
    return items


def snapshot_flags(ctx: dict) -> dict:
    seeds = ctx['env2'] / 'seeds'
    out = {}
    dcs_file = seeds / 'dcs/merchants.json'
    files = C.hash_tree(seeds / 'dcs', relative_to=ctx['env2'])
    items = {}
    if dcs_file.is_file():
        try:
            items = _flag_items_dcs(C.read_json(dcs_file))
        except ValueError:
            items = {}
    out['dcs'] = {'files': files, 'items': items, 'digest': C.digest_of_map(files)}
    files = C.hash_tree(seeds / 'splitz', relative_to=ctx['env2'])
    items = {}
    try:
        exp = C.read_json(seeds / 'splitz/experiments.json') if (seeds / 'splitz/experiments.json').is_file() else {}
        vt = C.read_json(seeds / 'splitz/variant_table.json') if (seeds / 'splitz/variant_table.json').is_file() else {}
        items = _flag_items_splitz(exp, vt)
    except ValueError:
        items = {}
    out['splitz'] = {'files': files, 'items': items, 'digest': C.digest_of_map(files)}
    return out


def snapshot_images(ctx: dict, with_docker: bool) -> tuple:
    fp = C.import_fingerprint() if (ctx['env2'] / 'scripts/fingerprint.py').is_file() else None
    names = fp.compose_image_names(ctx['env2']) if fp and (ctx['env2'] / 'docker-compose.yml').is_file() else []
    if not with_docker:
        return {n: None for n in names}, 'skipped: --no-docker'
    ok, reason = C.docker_available()
    if not ok:
        return {n: None for n in names}, 'skipped: ' + str(reason)
    return C.docker_image_ids(names), None


def snapshot_compose(ctx: dict) -> dict:
    services = C.compose_services(ctx['compose'])
    defs = {name: C.sha256_json(svc or {}) for name, svc in services.items()}
    profiles = sorted({p for s in services.values() for p in ((s or {}).get('profiles') or [])})
    return {'file': 'ENV2_COMPOSE/docker-compose.yml', 'file_sha256': ctx.get('compose_sha'),
            'services': sorted(services), 'service_digests': defs, 'profiles': profiles,
            'digest': C.sha256_json(sorted(services))}


# --------------------------------------------------------------------------- snapshot
def build_snapshot(root: Path = C.ROOT, repos_root: Path | None = None, accepted: Path | None = None,
                   graph_path: Path | None = None, with_docker: bool = True, ctx: dict | None = None) -> dict:
    root = Path(root)
    graph_path = Path(graph_path) if graph_path else root / 'reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json'
    ctx = ctx or R.build_context(root=root, repos_root=repos_root, accepted=accepted)
    twin = C.git_info(root)
    images, images_reason = snapshot_images(ctx, with_docker)
    snap = {
        'schema_version': SNAPSHOT_SCHEMA_VERSION,
        'captured_at': C.utc_now(),
        'twin': {'git_head': twin.get('sha'), 'branch': twin.get('branch'), 'dirty': twin.get('dirty'), 'root': str(root)},
        'repos_root': str(ctx['repos_root']) if ctx.get('repos_root') else None,
        'accepted_root': str(ctx['accepted']) if ctx.get('accepted') and Path(ctx['accepted']).is_dir() else None,
        'provenance': (ctx.get('provenance') or {}).get('_path'),
        'graph': snapshot_graph(graph_path),
        'repos': snapshot_repos(ctx),
        'recipes': R.recipe_hashes(ctx),
        'config': {'digest': None, 'hashes': ctx.get('config_hashes', {})},
        'schemas': snapshot_schemas(ctx),
        'contracts': snapshot_contracts(ctx),
        'flags': snapshot_flags(ctx),
        'images': images,
        'images_reason': images_reason,
        'compose': snapshot_compose(ctx),
        'note': 'Hashes, ids and commit SHAs only. secrets/, config/generated, seeds/generated, .env* and .runtime/ are never read.',
    }
    fp = C.import_fingerprint() if (ctx['env2'] / 'scripts/fingerprint.py').is_file() else None
    snap['config']['digest'] = fp.config_digest(snap['config']['hashes']) if fp else C.digest_of_map(snap['config']['hashes'])
    snap['graph_version'] = snap['graph']['version']
    snap['digest'] = C.sha256_json({k: v for k, v in snap.items() if k not in ('captured_at', 'images_reason', 'digest')})
    return snap


def write_snapshot(snapshot: dict, out_dir: Path = C.SNAPSHOT_DIR) -> Path:
    """Write <UTC>.json (never overwriting an existing file) and update latest.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = C.utc_stamp()
    path = out_dir / (stamp + '.json')
    n = 1
    while path.exists():
        path = out_dir / ('%s-%d.json' % (stamp, n))
        n += 1
    snapshot = dict(snapshot)
    snapshot['_path'] = str(path)
    C.write_json(path, snapshot, overwrite=False)
    C.write_json(out_dir / 'latest.json', {'latest': path.name, 'path': str(path), 'captured_at': snapshot['captured_at'],
                                           'digest': snapshot['digest'], 'graph_version': snapshot['graph_version']})
    return path


def latest_snapshot_path(out_dir: Path = C.SNAPSHOT_DIR) -> Path | None:
    pointer = Path(out_dir) / 'latest.json'
    if pointer.is_file():
        try:
            data = C.read_json(pointer)
            path = Path(out_dir) / data.get('latest', '')
            if path.is_file():
                return path
        except ValueError:
            pass
    candidates = sorted(p for p in Path(out_dir).glob('*.json')
                        if p.name != 'latest.json' and not p.name.endswith('-diff.json')) if Path(out_dir).is_dir() else []
    return candidates[-1] if candidates else None


def summary_line(snapshot: dict) -> str:
    return ('snapshot: repos=%d schemas=%d contracts(proto=%d subs=%d batch=%d) flags(dcs=%d splitz=%d) config_files=%d '
            'compose_services=%d images=%d/%d graph=%s digest=%s' % (
                len(snapshot['repos']), len(snapshot['schemas']),
                sum(len(v['files']) for v in snapshot['contracts']['proto'].values()),
                len(snapshot['contracts']['substitutes']), len(snapshot['contracts']['batch_types']),
                len(snapshot['flags']['dcs']['items']), len(snapshot['flags']['splitz']['items']),
                len(snapshot['config']['hashes']), len(snapshot['compose']['services']),
                sum(1 for v in snapshot['images'].values() if v), len(snapshot['images']),
                (snapshot['graph_version'] or 'absent')[:12], snapshot['digest'][:12]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--no-docker', action='store_true', help='do not call docker (image ids recorded as null)')
    ap.add_argument('--out-dir', type=Path, default=C.SNAPSHOT_DIR)
    ap.add_argument('--print', action='store_true', help='print the snapshot JSON to stdout instead of writing it')
    args = ap.parse_args(argv)
    snap = build_snapshot(with_docker=not args.no_docker)
    if args.print:
        print(json.dumps(snap, indent=2, sort_keys=True))
        return 0
    path = write_snapshot(snap, args.out_dir)
    print(summary_line(snap))
    print('wrote %s (latest.json updated)' % path)
    if snap['images_reason']:
        print('images: ' + snap['images_reason'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
