#!/usr/bin/env python3
"""Map a snapshot diff to the services, substitutes and journeys it affects.

  affected.py <diff.json> [--graph PATH] [--json OUT]

Uses reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json when present:
  repo change      -> svc/worker/cron nodes with that `repo`          -> their families' journeys
  table / schema   -> nodes owning `table:<svc>/...` / owner_domain   -> journeys
  contract         -> service owning the proto module / sub node     -> implemented svc -> journeys
  flag             -> nodes whose `feature_flags` name it, gated_by  -> families -> journeys
  substitute       -> `implements` edges                              -> svc -> journeys
Without the graph a conservative hardcoded map (repo -> compose services ->
existing journey drivers) is used and `fallback_used` is set.

Prints and (with --json) writes:
  rebuild: [...]          build-host.sh targets / substitute compose services to rebuild
  rerun_journeys: [...]   journey ids (graph) or driver ids (fallback)
  regen_config: bool      config/generate.py must be rerun before the next boot
plus restart / reseed / rerun_migrations lists and the reasons for every entry.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

# ---- conservative fallback map (graph absent) -------------------------------
# Journey *driver* ids that exist today; RED_LOOP/m6/journeys/<name>.py ids are
# added by the graph once it lands.
DRIVERS = {
    'verifier-golden-run': 'ENV2_COMPOSE/scripts/golden-run.sh',
    'm4-boundary': 'ENV2_COMPOSE/verifier/m4_boundary.py --no-build',
    'm4-direct-provision': 'RED_LOOP/surface/m4_direct_provision.py all',
    'm4-direct-journeys': 'RED_LOOP/surface/m4_direct_journeys.py',
    'm4-bas-ingest': 'RED_LOOP/surface/m4_bas_ingest.py',
    'm4-xas-ledger': 'RED_LOOP/surface/m4_xas_ledger.py',
    'm5-scenario-suite': 'RED_LOOP/m5/scenario_suite.py',
}
ALL_DRIVERS = sorted(DRIVERS)
FALLBACK_REPO_JOURNEYS = {
    'payouts': ALL_DRIVERS,
    'ledger': ['verifier-golden-run', 'm4-direct-journeys', 'm4-xas-ledger'],
    'fts': ['verifier-golden-run', 'm4-boundary', 'm4-direct-journeys'],
    'cfa': ['verifier-golden-run', 'm4-direct-provision'],
    'x-balances': ['verifier-golden-run', 'm4-direct-journeys'],
    'proto': ['verifier-golden-run', 'm4-direct-journeys', 'm4-xas-ledger'],
    'workflows': ['m5-scenario-suite'],
    'api': ['verifier-golden-run', 'm4-direct-journeys', 'm4-direct-provision'],
    'mozart': ['m4-direct-journeys', 'verifier-golden-run'],
    'stork': ['verifier-golden-run', 'm4-direct-journeys'],
    'dcs': ['verifier-golden-run', 'm4-direct-journeys'],
    'splitz': ['verifier-golden-run', 'm4-direct-journeys'],
    'x-account-statements': ['m4-xas-ledger', 'm4-direct-journeys'],
    'banking-accounts': ['m4-bas-ingest', 'm4-direct-journeys'],
    'batch': [], 'kube-manifests': [], 'config-proto': [], 'goutils': [], 'edge': ['m4-boundary', 'verifier-golden-run'],
}
FALLBACK_SUB_SERVICE = {  # substitute -> real service family it implements (+ journeys)
    'kong-lite': ('edge', ['m4-boundary', 'verifier-golden-run']),
    'ledger-gate': ('ledger', ['verifier-golden-run', 'm4-xas-ledger']),
    'monolith-stub': ('api', FALLBACK_REPO_JOURNEYS['api']),
    'dcs-stub': ('dcs', FALLBACK_REPO_JOURNEYS['dcs']),
    'splitz-stub': ('splitz', FALLBACK_REPO_JOURNEYS['splitz']),
    'shield-stub': ('shield', ['verifier-golden-run']),
    'pricing-stub': ('api', ['verifier-golden-run', 'm4-direct-journeys']),
    'asv-stub': ('asv', ['verifier-golden-run', 'm4-direct-provision']),
    'bankingaccounts-stub': ('banking-accounts', FALLBACK_REPO_JOURNEYS['banking-accounts']),
    'stork-capture': ('stork', FALLBACK_REPO_JOURNEYS['stork']),
    'merchant-webhook-sink': ('merchant-webhook', ['verifier-golden-run', 'm4-direct-journeys']),
    'xas-sim': ('x-account-statements', FALLBACK_REPO_JOURNEYS['x-account-statements']),
    'xas-sink': ('x-account-statements', FALLBACK_REPO_JOURNEYS['x-account-statements']),
    'mozart-sim': ('mozart', FALLBACK_REPO_JOURNEYS['mozart']),
    'mozart-mock': ('mozart', FALLBACK_REPO_JOURNEYS['mozart']),
    'workflow-sim': ('workflows', FALLBACK_REPO_JOURNEYS['workflows']),
    'cron-driver': ('fastcron', ['m4-direct-journeys']),
    'verifier': ('verifier', ['verifier-golden-run']),
}
FALLBACK_FLAG_JOURNEYS = {'dcs': FALLBACK_REPO_JOURNEYS['dcs'], 'splitz': FALLBACK_REPO_JOURNEYS['splitz']}
CORE_TARGET = dict(C.CORE_REPOS)  # repo -> build-host target
TARGET_REPO = {v: k for k, v in CORE_TARGET.items()}


class Plan:
    def __init__(self, graph: dict | None, compose: dict | None):
        self.graph = graph
        self.compose_services = C.compose_services(compose or {})
        self.rebuild, self.restart, self.reseed, self.migrations = set(), set(), set(), set()
        self.journeys, self.services, self.subs, self.families = set(), set(), set(), set()
        self.regen_config = False
        self.regen_recipes = False
        self.reasons = []
        self.fallback_used = False
        # graph indexes
        self.nodes = {n['id']: n for n in (graph or {}).get('nodes', []) if n.get('id')}
        self.edges = (graph or {}).get('edges', [])
        self.fams = (graph or {}).get('families', [])

    # ---- graph lookups -------------------------------------------------------
    def _runtime_nodes(self, pred) -> list:
        return [n for n in self.nodes.values() if n.get('kind') in ('service', 'worker', 'cron', 'sub') and pred(n)]

    def svc_nodes_for_repo(self, repo: str) -> list:
        return self._runtime_nodes(lambda n: n.get('repo') == 'repo:' + repo)

    def svc_nodes_for_domain(self, domain: str) -> list:
        return self._runtime_nodes(lambda n: n.get('owner_domain') == domain or (n.get('id', '').split(':', 1)[-1].split('/')[0].split('-')[0] == domain.replace('x-balances', 'xbalances')))

    def svc_nodes_for_table_prefix(self, svc: str) -> list:
        prefix = 'table:%s/' % svc
        return self._runtime_nodes(lambda n: any(str(t).startswith(prefix) for t in (n.get('tables') or [])))

    def svc_nodes_for_flag(self, flag_id: str) -> list:
        """flag ids look like dcs/<ns>/<flag> or splitz/<name>; graph ids are flag:<source>/<name>."""
        parts = flag_id.split('/')
        source, leaf = parts[0], parts[-1]
        candidates = {'flag:' + flag_id, 'flag:%s/%s' % (source, leaf)}
        hits = self._runtime_nodes(lambda n: any(f in candidates or str(f).endswith('/' + leaf) for f in (n.get('feature_flags') or [])))
        for e in self.edges:
            if e.get('type') == 'gated_by' and (e.get('to') in candidates or str(e.get('to', '')).endswith('/' + leaf)):
                node = self.nodes.get(e.get('from'))
                if node:
                    hits.append(node)
        return hits

    def svc_nodes_for_sub(self, sub: str) -> list:
        out = []
        for e in self.edges:
            if e.get('from') == 'sub:' + sub and e.get('type') == 'implements':
                node = self.nodes.get(e.get('to'))
                if node:
                    out.append(node)
        node = self.nodes.get('sub:' + sub)
        if node:
            out.append(node)
        return out

    def families_for(self, node_ids: set) -> list:
        out = []
        for fam in self.fams:
            comps = set(fam.get('components') or [])
            if comps & node_ids:
                out.append(fam)
        # families that name a journey node whose family prefix matches a changed journey node
        return out

    def journeys_for_nodes(self, nodes: list) -> set:
        ids = {n['id'] for n in nodes}
        out = set()
        for fam in self.families_for(ids):
            self.families.add(fam['id'])
            out.update(C.journeys_for_family(self.graph, fam['id']))
        for n in nodes:
            if n.get('kind') == 'journey':
                out.add(n['id'])
        # journeys that declare a direct dependency on a changed node (journey -depends_on-> sub:*/svc:*)
        for e in self.edges:
            if e.get('type') == 'depends_on' and e.get('to') in ids and str(e.get('from', '')).startswith('journey:'):
                out.add(e['from'])
        # only real journey ids (family.journeys_existing may carry document references)
        return {j for j in out if isinstance(j, str) and j.startswith('journey:')}

    # ---- effects ---------------------------------------------------------------
    def add(self, cause: str, nodes: list | None = None, rebuild=(), journeys=(), restart=(), reseed=(), migrations=(),
            regen_config=False, subs=(), services=()):
        effect = {'cause': cause, 'rebuild': sorted(rebuild), 'journeys': sorted(journeys), 'restart': sorted(restart),
                  'reseed': sorted(reseed), 'migrations': sorted(migrations), 'regen_config': regen_config,
                  'services': sorted(services or [n['id'] for n in (nodes or [])]), 'substitutes': sorted(subs)}
        self.rebuild.update(rebuild)
        self.journeys.update(journeys)
        self.restart.update(restart)
        self.reseed.update(reseed)
        self.migrations.update(migrations)
        self.services.update(effect['services'])
        self.subs.update(subs)
        self.regen_config = self.regen_config or regen_config
        self.reasons.append(effect)

    def compose_services_of(self, repo: str) -> list:
        image = 'rzp-arena/%s:' % C.CORE_IMAGE.get(repo, repo)
        return sorted(n for n, s in self.compose_services.items() if str((s or {}).get('image', '')).startswith(image))

    def journeys_for_repo(self, repo: str) -> tuple:
        if self.graph:
            nodes = self.svc_nodes_for_repo(repo) or self.svc_nodes_for_domain(repo)
            if nodes:
                return nodes, self.journeys_for_nodes(nodes)
        self.fallback_used = True
        return [], set(FALLBACK_REPO_JOURNEYS.get(repo, ALL_DRIVERS if repo in C.CORE_REPOS else []))

    def journeys_for_sub(self, sub: str) -> tuple:
        if self.graph:
            nodes = self.svc_nodes_for_sub(sub)
            if nodes:
                return nodes, self.journeys_for_nodes(nodes)
        self.fallback_used = True
        real, journeys = FALLBACK_SUB_SERVICE.get(sub, (sub, ['verifier-golden-run']))
        return [], set(journeys)

    # ---- diff sections -----------------------------------------------------------
    def apply_repo(self, entry: dict):
        repo = entry['name']
        nodes, journeys = self.journeys_for_repo(repo)
        services = [n['id'] for n in nodes] or self.compose_services_of(repo)
        if repo in C.CORE_REPOS:
            self.add('repo %s %s' % (repo, entry.get('commit_range') or entry.get('change')), nodes,
                     rebuild=[CORE_TARGET[repo]], journeys=journeys, services=services)
        elif repo == 'proto':
            self.add('repo proto %s (contract source for payouts/ledger)' % (entry.get('commit_range') or entry.get('change')),
                     rebuild=['payouts', 'ledger'], journeys=journeys, services=services or self.compose_services_of('payouts') + self.compose_services_of('ledger'))
        else:
            subs = self._subs_for_repo(repo)
            self.add('repo %s %s (not built in twin; review substitutes %s)' % (repo, entry.get('commit_range') or entry.get('change'), ','.join(subs) or 'none'),
                     nodes, journeys=journeys, subs=subs, services=services)

    def _subs_for_repo(self, repo: str) -> list:
        subs = set()
        for e in self.edges:
            if e.get('type') == 'implements' and str(e.get('from', '')).startswith('sub:'):
                target = self.nodes.get(e.get('to')) or {}
                if target.get('repo') == 'repo:' + repo:
                    subs.add(e['from'].split(':', 1)[1])
        if not subs:
            import recipes as R
            subs.update(s for s in R.REPO_SUBSTITUTES.get(repo, []) if s in self.compose_services or not self.compose_services)
        return sorted(subs)

    def apply_contracts(self, part: dict):
        for svc, body in (part.get('proto') or {}).items():
            nodes, journeys = self.journeys_for_repo(svc)
            self.add('proto contract for %s changed (%s)' % (svc, ', '.join((body['files']['modified'] + body['files']['added'] + body['files']['removed'])[:4])),
                     nodes, rebuild=[CORE_TARGET.get(svc, svc)], journeys=journeys, services=[n['id'] for n in nodes] or self.compose_services_of(svc))
        subs = part.get('substitutes') or {}
        for sub in subs.get('modified', []) + subs.get('added', []) + subs.get('removed', []):
            nodes, journeys = self.journeys_for_sub(sub)
            self.add('substitute contract %s/CONTRACT.md changed' % sub, nodes, rebuild=[sub] if sub in self.compose_services or not self.compose_services else [],
                     journeys=journeys, subs=[sub])
        bt = part.get('batch_types') or {}
        if any(bt.get(k) for k in ('added', 'removed', 'modified')):
            nodes, journeys = self.journeys_for_repo('batch')
            self.add('batch payout type JSON changed', nodes, journeys=journeys)

    def apply_schemas(self, part: dict):
        for svc, body in part.items():
            nodes = self.svc_nodes_for_table_prefix(svc) if self.graph else []
            if nodes:
                journeys = self.journeys_for_nodes(nodes)
            else:
                nodes, journeys = self.journeys_for_repo(svc)
            target = CORE_TARGET.get(svc)
            self.add('schema %s: +%d -%d ~%d migrations' % (svc, len(body['added']), len(body['removed']), len(body['modified'])), nodes,
                     rebuild=[target] if target else [], migrations=[target + '-migrate'] if target else [],
                     journeys=journeys, services=[n['id'] for n in nodes] or self.compose_services_of(svc))

    def apply_flags(self, part: dict):
        for source, body in part.items():
            items = body.get('items') or {}
            changed = items.get('modified', []) + items.get('added', []) + items.get('removed', [])
            stub = source + '-stub'
            for flag in changed or ['%s/*' % source]:
                nodes = self.svc_nodes_for_flag(flag) if self.graph else []
                if nodes:
                    journeys = self.journeys_for_nodes(nodes)
                else:
                    self.fallback_used = True
                    journeys = set(FALLBACK_FLAG_JOURNEYS.get(source, ALL_DRIVERS))
                self.add('flag %s changed' % flag, nodes, journeys=journeys, reseed=[stub] if stub in self.compose_services or not self.compose_services else [],
                         restart=[stub] if stub in self.compose_services else [], subs=[stub])

    def apply_config(self, part: dict):
        paths = part.get('modified', []) + part.get('added', []) + part.get('removed', [])
        for path in paths:
            if path.startswith('config/templates/base/'):
                target = path.split('/')[3]
                repo = TARGET_REPO.get(target, target)
                nodes, journeys = self.journeys_for_repo(repo)
                self.add('config template %s' % path, nodes, restart=self.compose_services_of(repo), journeys=journeys, regen_config=True)
            elif path.startswith('config/') or path == 'docker-compose.yml':
                self.add('config input %s' % path, regen_config=True, restart=['<all>'] if path == 'docker-compose.yml' else [])
                if path == 'docker-compose.yml':
                    self.regen_recipes = True
            elif path.startswith('substitutes/'):
                sub = path.split('/')[1]
                nodes, journeys = self.journeys_for_sub(sub)
                self.add('substitute source %s' % path, nodes, rebuild=[sub] if sub in self.compose_services else [], journeys=journeys, subs=[sub])
            elif path.startswith('seeds/dcs') or path.startswith('seeds/splitz'):
                continue  # handled by apply_flags
            elif path.startswith('seeds/'):
                stub = self._seed_owner(path)
                self.add('seed %s' % path, reseed=[stub] if stub else [], journeys=['verifier-golden-run'] if not self.graph else [])
            elif path.startswith('verifier/'):
                self.add('verifier source %s' % path, journeys=['verifier-golden-run'] if not self.graph else [], rebuild=['verifier'] if 'verifier' in self.compose_services else [])
            elif path.startswith('scripts/cron-driver'):
                self.add('cron driver %s' % path, restart=['cron-driver'] if 'cron-driver' in self.compose_services else [])
            else:
                self.add('config input %s' % path, regen_config=True)

    def _seed_owner(self, path: str) -> str | None:
        seg = path.split('/')[1]
        return {'mysql': 'mysql-payouts', 'postgres': 'postgres-ledger', 'mongo': 'mongo-cfa', 'localstack': 'localstack',
                'stork': 'stork-capture', 'shield': 'shield-stub', 'monolith': 'monolith-stub', 'schema-patches': 'mysql-payouts',
                'generator': 'mysql-payouts'}.get(seg)

    def apply_compose(self, part: dict):
        for svc in part.get('added', []) + part.get('modified', []):
            repo = C.core_repo_for_service(svc)
            if repo and str((self.compose_services.get(svc) or {}).get('image', '')).startswith('rzp-arena/'):
                nodes, journeys = self.journeys_for_repo(repo)
                self.add('compose service %s changed' % svc, nodes, restart=[svc], journeys=journeys, regen_config=True)
            elif svc in self.compose_services and ((self.compose_services.get(svc) or {}).get('build')):
                nodes, journeys = self.journeys_for_sub(svc)
                self.add('compose service %s changed' % svc, nodes, rebuild=[svc], journeys=journeys, regen_config=True, subs=[svc])
            else:
                self.add('compose service %s changed' % svc, restart=[svc], regen_config=True)
        for svc in part.get('removed', []):
            self.add('compose service %s removed' % svc, regen_config=True)
        if part.get('added') or part.get('removed') or part.get('modified'):
            self.regen_recipes = True
        recipes = part.get('recipes') or {}
        for rid in recipes.get('modified', []) + recipes.get('added', []):
            self.regen_recipes = True
            if rid.startswith('repo-'):
                continue
            if rid not in {e['cause'].split(' ')[2] for e in self.reasons if e['cause'].startswith('compose service ')}:
                self.reasons.append({'cause': 'recipe %s changed (inputs moved; see other reasons)' % rid, 'rebuild': [], 'journeys': [],
                                     'restart': [], 'reseed': [], 'migrations': [], 'regen_config': False, 'services': [], 'substitutes': []})

    def apply_images(self, part: dict):
        for name in part.get('modified', []):
            short = name.split('/')[-1].split(':')[0]
            repo = TARGET_REPO.get(short, short)
            nodes, journeys = (self.journeys_for_repo(repo) if repo in C.CORE_REPOS else self.journeys_for_sub(short))
            self.add('image %s id changed' % name, nodes, restart=self.compose_services_of(repo) if repo in C.CORE_REPOS else [short], journeys=journeys)

    def _executable(self, ids: set) -> set:
        """Journeys RED_LOOP/m6/journeys/run.py can rerun (graph twin_ref); others belong to older harnesses."""
        if not self.nodes:
            return set(ids)
        other = ('ENV2_COMPOSE/verifier', 'RED_LOOP/surface', 'RED_LOOP/m5', 'ARCHITECTURE_EXPLORER')
        ex = set()
        for j in ids:
            ref = str((self.nodes.get(j) or {}).get('twin_ref') or (self.nodes.get(j) or {}).get('evidence') or '')
            if not any(o in ref for o in other):
                ex.add(j)
        return ex

    def apply_graph(self, part: dict):
        if not part.get('changed'):
            return
        self.regen_recipes = True
        touched = set(part.get('nodes_added', [])) | set(part.get('nodes_removed', [])) | set(part.get('nodes_modified', []))
        for eid in part.get('edges_added', []) + part.get('edges_removed', []):
            touched.update(eid.split('|')[::2])
        nodes = [self.nodes[i] for i in touched if i in self.nodes]
        journeys = self.journeys_for_nodes(nodes) if self.graph else set()
        journeys.update(i for i in touched if i.startswith('journey:'))
        self.add('graph changed (%s -> %s): %d nodes / %d edges touched' % ((part.get('old_version') or 'none')[:12], (part.get('new_version') or 'none')[:12],
                                                                            len(touched), len(part.get('edges_added', [])) + len(part.get('edges_removed', []))),
                 nodes, journeys=journeys)

    def result(self, diff: dict) -> dict:
        return {
            'schema_version': 1, 'generated_at': C.utc_now(), 'diff': {'old': diff.get('old'), 'new': diff.get('new'), 'changed': diff.get('changed')},
            'graph_used': self.graph is not None, 'fallback_used': self.fallback_used or self.graph is None,
            'rebuild': sorted(self.rebuild), 'rerun_journeys': sorted(self._executable(self.journeys)),
            'rerun_other_harness': sorted(self.journeys - self._executable(self.journeys)), 'regen_config': self.regen_config,
            'regen_recipes': self.regen_recipes, 'restart': sorted(self.restart), 'reseed': sorted(self.reseed),
            'rerun_migrations': sorted(self.migrations), 'affected_services': sorted(self.services),
            'affected_substitutes': sorted(self.subs), 'affected_families': sorted(self.families),
            'journey_drivers': {j: DRIVERS[j] for j in sorted(self.journeys) if j in DRIVERS},
            'reasons': self.reasons,
        }


def compute_affected(diff: dict, graph: dict | None, compose: dict | None) -> dict:
    plan = Plan(graph, compose)
    if not diff.get('changed'):
        return plan.result(diff)
    for entry in diff.get('repos', []):
        plan.apply_repo(entry)
    plan.apply_contracts(diff.get('contracts') or {})
    plan.apply_schemas(diff.get('schemas') or {})
    plan.apply_flags(diff.get('flags') or {})
    plan.apply_config(diff.get('config') or {})
    plan.apply_compose(diff.get('compose') or {})
    plan.apply_images(diff.get('images') or {})
    plan.apply_graph(diff.get('graph') or {})
    return plan.result(diff)


def format_plan(plan: dict) -> str:
    lines = ['rebuild: %s' % json.dumps(plan['rebuild']),
             'rerun_journeys: %s' % json.dumps(plan['rerun_journeys']),
             'regen_config: %s' % json.dumps(plan['regen_config']),
             'restart: %s' % json.dumps(plan['restart']),
             'reseed: %s' % json.dumps(plan['reseed']),
             'rerun_migrations: %s' % json.dumps(plan['rerun_migrations']),
             'graph_used: %s  fallback_used: %s' % (plan['graph_used'], plan['fallback_used'])]
    for r in plan['reasons']:
        lines.append('  - %s -> rebuild=%s journeys=%d' % (r['cause'], r['rebuild'], len(r['journeys'])))
    return '\n'.join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('diff', type=Path)
    ap.add_argument('--graph', type=Path, default=C.GRAPH_PATH)
    ap.add_argument('--compose', type=Path, default=C.ENV2 / 'docker-compose.yml')
    ap.add_argument('--json', type=Path, help='write the plan here')
    args = ap.parse_args(argv)
    diff = C.read_json(args.diff)
    graph = C.load_graph(args.graph)
    compose = C.load_compose(args.compose) if args.compose.is_file() else {}
    plan = compute_affected(diff, graph, compose)
    print(format_plan(plan))
    if args.json:
        C.write_json(args.json, plan)
        print('wrote ' + str(args.json))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
