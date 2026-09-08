#!/usr/bin/env python3
"""Diff two domain snapshots: `diff.py <old.json> <new.json>`.

Prints a human summary (stdout) and, with --json PATH, the machine diff that
affected.py consumes. Exit 0 when nothing changed, 3 when something did.

Sections: repos (with commit ranges), contracts, schemas, flags, config,
compose services / recipes, images, graph (version + node/edge id sets).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

EXIT_CHANGED = 3


def _map_diff(old: dict, new: dict) -> dict:
    old, new = old or {}, new or {}
    return {'added': sorted(set(new) - set(old)), 'removed': sorted(set(old) - set(new)),
            'modified': sorted(k for k in set(old) & set(new) if old[k] != new[k])}


def _any(d: dict) -> bool:
    return any(d.get(k) for k in ('added', 'removed', 'modified'))


def diff_repos(old: dict, new: dict) -> list:
    out = []
    for name in sorted(set(old) | set(new)):
        o, n = old.get(name), new.get(name)
        if o is None or n is None:
            out.append({'name': name, 'change': 'added' if o is None else 'removed', 'old_sha': (o or {}).get('sha'),
                        'new_sha': (n or {}).get('sha'), 'commit_range': None})
            continue
        entry = {'name': name, 'change': None, 'old_sha': o.get('sha'), 'new_sha': n.get('sha'), 'commit_range': None,
                 'dirty': [o.get('dirty'), n.get('dirty')], 'reasons': []}
        if o.get('sha') != n.get('sha'):
            entry['reasons'].append('sha')
            entry['commit_range'] = '%s..%s' % ((o.get('sha') or 'none')[:12], (n.get('sha') or 'none')[:12])
        if bool(o.get('dirty')) != bool(n.get('dirty')):
            entry['reasons'].append('dirty:%s->%s' % (o.get('dirty'), n.get('dirty')))
        if o.get('accepted_tree_digest') != n.get('accepted_tree_digest'):
            entry['reasons'].append('accepted_tree')
        if o.get('accepted_source_commit') != n.get('accepted_source_commit'):
            entry['reasons'].append('accepted_source_commit')
        if o.get('accepted_matches_provenance') != n.get('accepted_matches_provenance'):
            entry['reasons'].append('provenance_match:%s->%s' % (o.get('accepted_matches_provenance'), n.get('accepted_matches_provenance')))
        if entry['reasons']:
            entry['change'] = 'modified'
            out.append(entry)
    return out


def diff_contracts(old: dict, new: dict) -> dict:
    old, new = old or {}, new or {}
    out = {'proto': {}, 'substitutes': _map_diff(old.get('substitutes'), new.get('substitutes')),
           'batch_types': _map_diff(old.get('batch_types'), new.get('batch_types'))}
    for svc in sorted(set(old.get('proto', {})) | set(new.get('proto', {}))):
        o, n = old.get('proto', {}).get(svc, {}), new.get('proto', {}).get(svc, {})
        d = _map_diff(o.get('files'), n.get('files'))
        modules = _map_diff({m: m for m in o.get('modules', [])}, {m: m for m in n.get('modules', [])})
        if _any(d) or _any(modules):
            out['proto'][svc] = {'files': d, 'modules': modules}
    out['changed'] = bool(out['proto']) or _any(out['substitutes']) or _any(out['batch_types'])
    return out


def diff_schemas(old: dict, new: dict) -> dict:
    old, new = old or {}, new or {}
    out = {}
    for svc in sorted(set(old) | set(new)):
        d = _map_diff(old.get(svc, {}).get('files'), new.get(svc, {}).get('files'))
        if _any(d):
            out[svc] = d
    return out


def diff_flags(old: dict, new: dict) -> dict:
    old, new = old or {}, new or {}
    out = {}
    for source in sorted(set(old) | set(new)):
        items = _map_diff(old.get(source, {}).get('items'), new.get(source, {}).get('items'))
        files = _map_diff(old.get(source, {}).get('files'), new.get(source, {}).get('files'))
        if _any(items) or _any(files):
            out[source] = {'items': items, 'files': files}
    return out


def diff_config(old: dict, new: dict) -> dict:
    d = _map_diff((old or {}).get('hashes'), (new or {}).get('hashes'))
    d['digest'] = [(old or {}).get('digest'), (new or {}).get('digest')]
    d['changed'] = _any(d)
    return d


def diff_compose(old: dict, new: dict, old_recipes: dict, new_recipes: dict) -> dict:
    o_services, n_services = set((old or {}).get('services', [])), set((new or {}).get('services', []))
    defs = _map_diff((old or {}).get('service_digests'), (new or {}).get('service_digests'))
    recipes = _map_diff(old_recipes, new_recipes)
    return {'added': sorted(n_services - o_services), 'removed': sorted(o_services - n_services),
            'modified': defs['modified'], 'recipes': recipes,
            'changed': bool(n_services ^ o_services) or bool(defs['modified']) or _any(recipes)}


def diff_images(old: dict, new: dict) -> dict:
    d = _map_diff(old or {}, new or {})
    # null -> null (docker unavailable both times) is not a change; null -> id is informational.
    d['modified'] = [k for k in d['modified'] if (old or {}).get(k) and (new or {}).get(k)]
    d['resolved'] = sorted(k for k in set(old or {}) & set(new or {}) if not (old or {}).get(k) and (new or {}).get(k))
    d['changed'] = _any(d)
    return d


def diff_graph(old: dict, new: dict) -> dict:
    old, new = old or {}, new or {}
    o_nodes, n_nodes = set(old.get('node_ids', [])), set(new.get('node_ids', []))
    o_edges, n_edges = set(old.get('edge_ids', [])), set(new.get('edge_ids', []))
    node_digest = _map_diff(old.get('node_digests'), new.get('node_digests'))
    out = {'old_version': old.get('version'), 'new_version': new.get('version'),
           'version_changed': old.get('version') != new.get('version'),
           'nodes_added': sorted(n_nodes - o_nodes), 'nodes_removed': sorted(o_nodes - n_nodes),
           'nodes_modified': node_digest['modified'],
           'edges_added': sorted(n_edges - o_edges), 'edges_removed': sorted(o_edges - n_edges)}
    out['changed'] = out['version_changed'] or bool(out['nodes_added'] or out['nodes_removed'] or out['edges_added'] or out['edges_removed'])
    return out


def diff_snapshots(old: dict, new: dict) -> dict:
    d = {
        'schema_version': 1, 'generated_at': C.utc_now(),
        'old': {'path': old.get('_path'), 'captured_at': old.get('captured_at'), 'digest': old.get('digest')},
        'new': {'path': new.get('_path'), 'captured_at': new.get('captured_at'), 'digest': new.get('digest')},
        'repos': diff_repos(old.get('repos', {}), new.get('repos', {})),
        'contracts': diff_contracts(old.get('contracts'), new.get('contracts')),
        'schemas': diff_schemas(old.get('schemas'), new.get('schemas')),
        'flags': diff_flags(old.get('flags'), new.get('flags')),
        'config': diff_config(old.get('config'), new.get('config')),
        'compose': diff_compose(old.get('compose'), new.get('compose'), old.get('recipes', {}), new.get('recipes', {})),
        'images': diff_images(old.get('images'), new.get('images')),
        'graph': diff_graph(old.get('graph'), new.get('graph')),
        'twin': {'old_head': (old.get('twin') or {}).get('git_head'), 'new_head': (new.get('twin') or {}).get('git_head')},
    }
    d['changed'] = bool(d['repos']) or d['contracts']['changed'] or bool(d['schemas']) or bool(d['flags']) \
        or d['config']['changed'] or d['compose']['changed'] or d['images']['changed'] or d['graph']['changed']
    d['summary'] = summarize(d)
    return d


def summarize(d: dict) -> list:
    lines = []
    if not d['changed']:
        return ['no change']
    for r in d['repos']:
        lines.append('repo %s: %s %s' % (r['name'], r['change'], r.get('commit_range') or ','.join(r.get('reasons', [])) or ''))
    for svc, body in d['contracts']['proto'].items():
        lines.append('contract proto %s: +%d -%d ~%d files' % (svc, len(body['files']['added']), len(body['files']['removed']), len(body['files']['modified'])))
    for k in ('substitutes', 'batch_types'):
        part = d['contracts'][k]
        if _any(part):
            lines.append('contract %s: +%s -%s ~%s' % (k, part['added'], part['removed'], part['modified']))
    for svc, part in d['schemas'].items():
        lines.append('schema %s: +%d -%d ~%d migrations' % (svc, len(part['added']), len(part['removed']), len(part['modified'])))
    for src, part in d['flags'].items():
        items = part['items']
        lines.append('flags %s: +%s -%s ~%s' % (src, items['added'], items['removed'], items['modified']))
    if d['config']['changed']:
        lines.append('config: +%d -%d ~%d files (%s)' % (len(d['config']['added']), len(d['config']['removed']), len(d['config']['modified']),
                                                          ', '.join((d['config']['modified'] + d['config']['added'] + d['config']['removed'])[:6])))
    c = d['compose']
    if c['changed']:
        lines.append('compose: +%s -%s ~%s; recipes ~%s' % (c['added'], c['removed'], c['modified'], c['recipes']['modified']))
    if d['images']['changed']:
        lines.append('images: +%s -%s ~%s' % (d['images']['added'], d['images']['removed'], d['images']['modified']))
    g = d['graph']
    if g['changed']:
        lines.append('graph: version %s -> %s; nodes +%d -%d ~%d; edges +%d -%d' % (
            (g['old_version'] or 'none')[:12], (g['new_version'] or 'none')[:12], len(g['nodes_added']), len(g['nodes_removed']),
            len(g['nodes_modified']), len(g['edges_added']), len(g['edges_removed'])))
    return lines


def load_snapshot(path: Path) -> dict:
    snap = C.read_json(path)
    snap.setdefault('_path', str(path))
    return snap


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('old', type=Path)
    ap.add_argument('new', type=Path)
    ap.add_argument('--json', type=Path, help='write the machine-readable diff here')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)
    d = diff_snapshots(load_snapshot(args.old), load_snapshot(args.new))
    if args.json:
        C.write_json(args.json, d)
    if not args.quiet:
        print('diff %s -> %s: %s' % (args.old.name, args.new.name, 'CHANGED' if d['changed'] else 'no change'))
        for line in d['summary']:
            print('  ' + line)
        if args.json:
            print('wrote ' + str(args.json))
    return EXIT_CHANGED if d['changed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
