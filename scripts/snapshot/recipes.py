#!/usr/bin/env python3
"""Generate reports/domain/recipes/<service>.yaml for every compose service and
every source repo of the Payouts domain, plus reports/domain/SNAPSHOT_MANIFEST.json.

A recipe answers "how is this component built, run, checked, seeded and what
does it replace" from the real build inputs: docker-compose.yml, build-host.sh,
the Dockerfiles, the admitted copies / pristine clones, the config templates,
the substitutes' CONTRACT.md files and (when present) the functional graph.

Nothing here reads secrets (ENV2_COMPOSE/secrets, config/generated,
seeds/generated, .env*), and compose `environment` values are never copied --
only variable names.

Usage:
  python3 scripts/snapshot/recipes.py                # write recipes + manifest
  python3 scripts/snapshot/recipes.py --out DIR      # elsewhere (tests)
  python3 scripts/snapshot/recipes.py --list         # print recipe ids only
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402

RECIPE_SCHEMA_VERSION = 1
VOLATILE_KEYS = ('generated_at',)

# Substitute compose service -> real system(s) it stands in for, and the source
# repo (when one exists in the pinned clones) that describes the real thing.
SUB_REPLACES = {
    'kong-lite': (['edge (Kong gateway + route policy)'], 'edge'),
    'ledger-gate': (['ledger transport gate (twin-only guard in front of ledger-api)'], 'ledger'),
    'monolith-stub': (['api (Razorpay monolith: merchant config, fund_account_validation, create_fta relay, DA emitter)'], 'api'),
    'dcs-stub': (['dcs (config store)'], 'dcs'),
    'splitz-stub': (['splitz (experiments)'], 'splitz'),
    'shield-stub': (['shield (risk rules)'], 'shield-sdk'),
    'pricing-stub': (['api pricing plans'], 'api'),
    'asv-stub': (['account-service-validation (ASV)'], None),
    'bankingaccounts-stub': (['banking-accounts (RBL credentials route)'], 'banking-accounts'),
    'stork-capture': (['stork (webhook dispatcher; captured, not delivered)'], 'stork'),
    'merchant-webhook-sink': (['merchant webhook endpoint'], None),
    'xas-sim': (['x-account-statements (XAS enrichment / matcher)'], 'x-account-statements'),
    'xas-sink': (['x-account-statements source-event sink'], 'x-account-statements'),
    'mozart-sim': (['mozart (bank gateway: RBL v2 / IMPS)'], 'mozart'),
    'mozart-mock': (['mozart real binary in built-in -mock mode (opt-in profile; build blocked on private module)'], 'mozart'),
    'workflow-sim': (['workflows (Cadence-backed maker-checker engine)'], 'workflows'),
    'cron-driver': (['fastcron (external cron trigger)'], None),
    'verifier': ([], None),
}
# Source repo -> substitute(s) implementing it in the twin.
REPO_SUBSTITUTES = {
    'api': ['monolith-stub', 'pricing-stub'], 'dcs': ['dcs-stub'], 'splitz': ['splitz-stub'],
    'stork': ['stork-capture'], 'mozart': ['mozart-sim', 'mozart-mock'],
    'workflows': ['workflow-sim', 'substitutes/workflow-engine (M5 reconstructed engine, not a compose service)'],
    'x-account-statements': ['xas-sim', 'xas-sink'], 'banking-accounts': ['bankingaccounts-stub'],
    'edge': ['kong-lite'], 'shield-sdk': ['shield-stub'], 'batch': [], 'proto': [], 'kube-manifests': [],
}
# Datastore compose service -> seed inputs.
SEEDS_FOR = {
    'mysql-payouts': ['ENV2_COMPOSE/seeds/schema-patches/payouts.sql', 'ENV2_COMPOSE/seeds/generator/generate.py'],
    'mysql-apidb-stub': ['ENV2_COMPOSE/seeds/mysql/apidb-ddl/00_init.sql', 'ENV2_COMPOSE/seeds/schema-patches/apidb.sql',
                         'ENV2_COMPOSE/seeds/generator/generate.py'],
    'mysql-fts': ['ENV2_COMPOSE/seeds/mysql/fts_seed.sql', 'ENV2_COMPOSE/seeds/generator/generate.py'],
    'mysql-xbalances': ['ENV2_COMPOSE/seeds/generator/generate.py'],
    'postgres-ledger': ['ENV2_COMPOSE/seeds/postgres/ledger_seed.sql', 'ENV2_COMPOSE/seeds/generator/generate.py'],
    'mongo-cfa': ['ENV2_COMPOSE/seeds/mongo/cfa_seed.js', 'ENV2_COMPOSE/seeds/generator/generate.py'],
    'localstack': ['ENV2_COMPOSE/seeds/localstack/init-queues.sh'],
    'dcs-stub': ['ENV2_COMPOSE/seeds/dcs/merchants.json'],
    'splitz-stub': ['ENV2_COMPOSE/seeds/splitz/experiments.json', 'ENV2_COMPOSE/seeds/splitz/variant_table.json'],
    'stork-capture': ['ENV2_COMPOSE/seeds/stork/subscriptions.json'],
    'shield-stub': ['ENV2_COMPOSE/seeds/shield/rules.json'],
    'monolith-stub': ['ENV2_COMPOSE/seeds/monolith/merchants.json', 'ENV2_COMPOSE/seeds/monolith/fund_accounts.json',
                      'ENV2_COMPOSE/seeds/monolith/misc.json'],
    'mozart-sim': ['ENV2_COMPOSE/seeds/mozart_scenarios.json'],
}
CORE_SEEDS = {
    'payouts': SEEDS_FOR['mysql-payouts'] + ['ENV2_COMPOSE/seeds/merchants.json', 'RED_LOOP/surface/m4_direct_provision.py (fresh Direct merchant provisioner)'],
    'ledger': SEEDS_FOR['postgres-ledger'],
    'fts': SEEDS_FOR['mysql-fts'],
    'cfa': SEEDS_FOR['mongo-cfa'] + ['ENV2_COMPOSE/seeds/s4/cfa_via_api.sh'],
    'x-balances': SEEDS_FOR['mysql-xbalances'],
}
# Fidelity vocabulary mapping for the M4/M5 matrices (conservative).
MATRIX_LABEL_MAP = {
    'real': 'real_source_running', 'executed': 'real_source_running', 'real-worker/substitute-bank': 'real_source_running',
    'substitute': 'high_fidelity_replacement', 'implemented': 'high_fidelity_replacement',
    'byte-compatible': 'high_fidelity_replacement', 'real-config': 'high_fidelity_replacement',
    'faithful': 'high_fidelity_replacement', 'reconstructed': 'behavioural_placeholder',
    'deliberately_simplified': 'behavioural_placeholder', 'source_inferred': 'behavioural_placeholder',
    'unresolved': 'graph_only', 'out-of-scope': 'graph_only', 'independently_reproduced': 'high_fidelity_replacement',
}


# --------------------------------------------------------------------------- context
def build_context(root: Path = C.ROOT, repos_root: Path | None = None, accepted: Path | None = None,
                  graph: dict | None = None, compose: dict | None = None, env2: Path | None = None) -> dict:
    """Everything the recipe writers need, computed once (cheap; no docker)."""
    root = Path(root)
    env2 = Path(env2) if env2 else root / 'ENV2_COMPOSE'
    repos_root = Path(repos_root) if repos_root else C.repos_root(root)
    accepted = Path(accepted) if accepted else root / '.local/twin-repos/accepted'
    compose = compose if compose is not None else (C.load_compose(env2 / 'docker-compose.yml') if (env2 / 'docker-compose.yml').is_file() else {})
    if graph is None:
        graph = C.load_graph(root / 'reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json')
    provenance = C.latest_provenance(root)
    fp = None
    config_hashes = {}
    if (env2 / 'scripts/fingerprint.py').is_file():
        fp = C.import_fingerprint()
        config_hashes = fp.config_hashes(env2)
    build_host = env2 / 'build/build-host.sh'
    ctx = {
        'root': root, 'env2': env2, 'repos_root': repos_root, 'accepted': accepted, 'compose': compose,
        'graph': graph, 'provenance': provenance, 'config_hashes': config_hashes,
        'build_host_text': build_host.read_text() if build_host.is_file() else '',
        'compose_sha': C.sha256_file(env2 / 'docker-compose.yml') if (env2 / 'docker-compose.yml').is_file() else None,
        'arena_services': _arena_services(env2),
        'matrix': _load_fidelity_matrices(root),
        'repo_info': {},
    }
    for repo in sorted(set(C.DOMAIN_REPOS) | set(graph_repos(graph))):
        ctx['repo_info'][repo] = repo_record(repo, ctx)
    return ctx


def graph_repos(graph: dict | None) -> list:
    if not graph:
        return []
    return sorted({n['id'].split(':', 1)[1] for n in graph.get('nodes', []) if n.get('kind') == 'repository' and ':' in n.get('id', '')})


def _arena_services(env2: Path) -> dict:
    """arena.yaml services block: token key -> host (compose service name)."""
    path = env2 / 'config/arena.yaml'
    if not path.is_file():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:  # pragma: no cover - arena.yaml is trusted input
        return {}
    out = {}
    for key, val in (data.get('services') or {}).items():
        if isinstance(val, dict) and val.get('host'):
            out[key] = val['host']
    return out


def _load_fidelity_matrices(root: Path) -> list:
    rows = []
    for name in ('m4-fidelity-matrix.csv', 'm5-fidelity-matrix.csv'):
        path = root / 'reports/implementation' / name
        if not path.is_file():
            continue
        with path.open(newline='') as fh:
            for row in csv.DictReader(fh):
                label = row.get('fidelity_label') or row.get('fidelity') or row.get('implementation_kind') or ''
                rows.append({'source': 'reports/implementation/' + name, 'component': row.get('component', ''),
                             'label': label,
                             'evidence': row.get('evidence_pointer') or row.get('real_anchor') or row.get('local_implementation') or ''})
    return rows


def repo_record(repo: str, ctx: dict) -> dict:
    """Pinned identity of one source repo: clone sha (+ accepted copy state for core repos)."""
    rec = {'name': repo, 'repository': 'razorpay/' + repo, 'clone_path': None, 'sha': None, 'dirty': None,
           'head_date': None, 'accepted_path': None, 'accepted_source_commit': None, 'accepted_matches_provenance': None,
           'present': False}
    rr = ctx.get('repos_root')
    if rr and (Path(rr) / repo).is_dir():
        info = C.git_info(Path(rr) / repo)
        rec.update({'clone_path': str(Path(rr) / repo), 'sha': info['sha'], 'dirty': info['dirty'],
                    'head_date': info['head_date'], 'present': True})
        if info.get('remote'):
            rec['repository'] = info['remote']
    acc = Path(ctx['accepted']) / repo if ctx.get('accepted') else None
    if acc and acc.is_dir():
        rec['accepted_path'] = str(acc)
        rec['present'] = True
        prov = ctx.get('provenance') or {}
        for entry in prov.get('repositories', []):
            if entry.get('name') == repo:
                rec['accepted_source_commit'] = entry.get('source_commit')
                rec['accepted_admitted_files'] = len((entry.get('patched_copy_manifest') or {}).get('files', []))
        if rec['sha'] is None:
            rec['sha'] = rec['accepted_source_commit']
    return rec


# --------------------------------------------------------------------------- helpers
def _build_host_steps(target: str, ctx: dict) -> list:
    """Expand build_<target>() from build-host.sh into the go build steps it runs."""
    text = ctx.get('build_host_text') or ''
    m = re.search(r'build_%s\(\)\s*\{(.*?)\n\}' % re.escape(target), text, re.S)
    steps = []
    if not m:
        return steps
    for line in m.group(1).splitlines():
        line = line.strip()
        bm = re.match(r'build_go\s+"\$REPOS_ROOT/([^"]+)"\s+(\S+)\s+(\S+)(?:\s+(\S+))?', line)
        if bm:
            repo, out, pkg, tags = bm.groups()
            steps.append('cd $REPOS_ROOT/%s && GOOS=linux GOARCH=${ARENA_ARCH:-host} CGO_ENABLED=0 go build -trimpath '
                         '-ldflags="-s -w"%s -o %s %s' % (repo, ' -tags ' + tags if tags else '', out, pkg))
        pm = re.match(r'package_runtime\s+(\S+)\s+(.*)', line)
        if pm:
            steps.append('docker build -f ENV2_COMPOSE/build/runtime-only.Dockerfile -t rzp-arena/%s:${ARENA_TAG} <staged: %s>'
                         % (pm.group(1), pm.group(2)))
    return steps


def _health(svc_def: dict, node: dict | None) -> str | None:
    hc = (svc_def or {}).get('healthcheck') or {}
    test = hc.get('test')
    if isinstance(test, list):
        test = ' '.join(str(t) for t in test if t not in ('CMD', 'CMD-SHELL'))
    if test:
        extra = ' '.join('%s=%s' % (k, hc[k]) for k in ('interval', 'timeout', 'retries', 'start_period') if k in hc)
        return (test + (' (' + extra + ')' if extra else '')).strip()
    if node and node.get('health'):
        return node['health']
    return None


def _graph_node_for(service: str, ctx: dict) -> dict | None:
    graph = ctx.get('graph')
    if not graph:
        return None
    best = None
    for node in graph.get('nodes', []):
        ref = node.get('twin_ref')
        if ref and (ref == service or ref.split('/')[-1] == service or service in str(ref).split(',')):
            if node.get('kind') in ('service', 'worker', 'cron', 'sub') or best is None:
                best = node
        elif node.get('id') in ('svc:' + service, 'sub:' + service, 'worker:' + service):
            best = best or node
    return best


def _graph_dependencies(node: dict | None, ctx: dict) -> list:
    if not node:
        return []
    out = set()
    for edge in ctx['graph'].get('edges', []):
        if edge.get('from') == node.get('id') and edge.get('type') in ('calls', 'depends_on', 'reads', 'writes', 'consumes', 'produces'):
            out.add('%s (%s)' % (edge.get('to'), edge.get('type')))
    return sorted(out)


def _graph_fidelity(node: dict | None) -> tuple | None:
    if node and node.get('fidelity'):
        return node['fidelity'], 'graph node %s: %s' % (node.get('id'), node.get('fidelity_evidence') or 'no evidence text')
    return None


def _matrix_fidelity(service: str, ctx: dict) -> tuple | None:
    for row in ctx.get('matrix', []):
        comp = row['component'].lower()
        if comp.startswith(service.lower() + ' ') or comp.startswith(service.lower() + '(') or comp == service.lower():
            label = row['label'].split(';')[0].strip()
            mapped = MATRIX_LABEL_MAP.get(label)
            if mapped:
                return mapped, '%s row "%s" label "%s" (%s)' % (row['source'], row['component'], row['label'], row['evidence'])
    return None


def fidelity_for(service: str, kind: str, ctx: dict, node: dict | None) -> dict:
    hit = _graph_fidelity(node) or _matrix_fidelity(service, ctx)
    if hit:
        return {'fidelity_label': hit[0], 'fidelity_reason': hit[1]}
    defaults = {
        'core': ('real_source_running', 'conservative default: real binary built by build-host.sh from the admitted copy (BUILD_PROVENANCE.md) and booted by docker-compose.yml; no graph node / matrix row names this service'),
        'substitute': ('behavioural_placeholder', 'conservative default: substitute with CONTRACT.md but no graph node / matrix row proving contract fidelity'),
        'infra': ('real_source_running', 'third-party datastore/broker image pulled as-is (not a Razorpay component)'),
        'harness': ('graph_only', 'test/driver harness, not a domain component'),
        'mozart-real': ('real_source_mapped_not_running', 'ENV2_COMPOSE/build/mozart.Dockerfile: build blocked on private module github.com/razorpay/integrations-utils; opt-in profile only'),
        'repo': ('real_source_mapped_not_running', 'source clone readable and pinned; not built or booted in the twin'),
        'repo-unmapped': ('graph_only', 'source clone pinned for reference only; no twin runtime and no substitute'),
    }
    label, reason = defaults.get(kind, defaults['repo'])
    return {'fidelity_label': label, 'fidelity_reason': reason}


def _config_hash_for(prefixes: list, ctx: dict) -> str | None:
    subset = {k: v for k, v in ctx.get('config_hashes', {}).items() if any(k.startswith(p) for p in prefixes)}
    return C.digest_of_map(subset) if subset else None


def _external_replacements_for_core(family: str, ctx: dict) -> list:
    """Substitutes the family's arena.toml template points at ({{SVC.<key>}} -> host)."""
    tmpl_dir = ctx['env2'] / 'config/templates/base' / family
    subs = set()
    compose_services = C.compose_services(ctx['compose'])
    for tmpl in sorted(tmpl_dir.glob('*.tmpl')) + sorted(tmpl_dir.glob('*.toml')):
        for key in set(re.findall(r'\{\{SVC\.([a-z0-9_]+)', tmpl.read_text())):
            host = ctx['arena_services'].get(key)
            if host and host in compose_services and _is_substitute(compose_services[host]):
                subs.add(host)
    return sorted(subs)


def _is_substitute(svc_def: dict) -> bool:
    build = svc_def.get('build')
    if isinstance(build, dict):
        return str(build.get('context', '')).rstrip('/').endswith('substitutes') or 'mozart.Dockerfile' in str(build.get('dockerfile', ''))
    return False


def _migration_files(repo_dir: Path, dirs: list) -> dict:
    files = {}
    for d in dirs:
        base = Path(repo_dir) / d
        if base.is_dir():
            for p in sorted(base.iterdir()):
                if p.is_file() and not p.name.startswith('.'):
                    files[(Path(d) / p.name).as_posix()] = C.sha256_file(p)
    return files


def _proto_modules(repo_dir: Path) -> list:
    path = Path(repo_dir) / 'scripts/proto_modules'
    if not path.is_file():
        return []
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith('#')]


def _repo_dir(repo: str, ctx: dict) -> Path | None:
    rec = ctx['repo_info'].get(repo) or {}
    for key in ('accepted_path', 'clone_path'):
        if rec.get(key) and Path(rec[key]).is_dir():
            return Path(rec[key])
    return None


def _clone_dir(repo: str, ctx: dict) -> Path | None:
    rec = ctx['repo_info'].get(repo) or {}
    return Path(rec['clone_path']) if rec.get('clone_path') else None


def describe_repo_build(repo: str, ctx: dict) -> dict:
    """How a non-built repo WOULD be built, from its own Makefile/Dockerfile/go.mod."""
    path = _clone_dir(repo, ctx)
    if not path or not path.is_dir():
        return {'summary': 'not built in twin; clone not present under repos-root', 'commands': []}
    cmds, evidence = [], []
    if (path / 'go.mod').is_file():
        cmd_dirs = sorted(p.name for p in (path / 'cmd').iterdir() if p.is_dir()) if (path / 'cmd').is_dir() else []
        for c in cmd_dirs:
            cmds.append('cd <clone>/%s && GOOS=linux CGO_ENABLED=0 go build -o bin/%s ./cmd/%s' % (repo, c, c))
        if not cmd_dirs:
            cmds.append('cd <clone>/%s && go build ./...' % repo)
        evidence.append('go.mod')
    if (path / 'build.gradle').is_file():
        cmds.append('cd <clone>/%s && ./gradlew clean build -x test' % repo)
        evidence.append('build.gradle')
    if (path / 'composer.json').is_file():
        cmds.append('cd <clone>/%s && composer install --no-dev' % repo)
        evidence.append('composer.json')
    dockerfiles = sorted(p.name for p in path.glob('Dockerfile*'))
    for d in dockerfiles[:4]:
        cmds.append('docker build -f <clone>/%s/%s <clone>/%s' % (repo, d, repo))
    if dockerfiles:
        evidence.append(','.join(dockerfiles))
    mk = path / 'Makefile'
    if mk.is_file():
        targets = re.findall(r'^((?:docker-)?build[\w-]*):', mk.read_text(), re.M)
        if targets:
            cmds.append('cd <clone>/%s && make %s' % (repo, ' '.join(sorted(set(targets))[:4])))
            evidence.append('Makefile targets ' + ','.join(sorted(set(targets))[:6]))
    if not cmds:
        cmds.append('no build recipe detected (no go.mod/build.gradle/composer.json/Dockerfile/Makefile build target)')
    return {'summary': 'not built in twin; would build with: ' + '; '.join(evidence) if evidence else 'not built in twin',
            'commands': cmds}


# --------------------------------------------------------------------------- recipes
def recipe_for_compose_service(name: str, svc_def: dict, ctx: dict) -> dict:
    image = svc_def.get('image')
    build = svc_def.get('build')
    core_repo = C.core_repo_for_service(name) if str(image or '').startswith('rzp-arena/') else None
    node = _graph_node_for(name, ctx)
    profiles = svc_def.get('profiles') or []
    deps = svc_def.get('depends_on') or {}
    deps = sorted(deps.keys()) if isinstance(deps, dict) else sorted(deps)
    env_keys = svc_def.get('environment')
    env_keys = sorted(env_keys.keys()) if isinstance(env_keys, dict) else sorted(str(e).split('=', 1)[0] for e in (env_keys or []))
    entry = svc_def.get('entrypoint')
    command = svc_def.get('command')
    recipe = {
        'schema_version': RECIPE_SCHEMA_VERSION, 'id': name, 'kind': 'compose_service',
        'compose_profiles': profiles, 'graph_node': node.get('id') if node else None,
        'twin_ref': 'ENV2_COMPOSE/docker-compose.yml#services.' + name,
        'runtime': {'image': image, 'entrypoint': entry, 'command': command, 'environment_keys': env_keys,
                    'volumes': [str(v).split(':')[1] if ':' in str(v) else str(v) for v in (svc_def.get('volumes') or [])],
                    'secrets_referenced': [str(s) if not isinstance(s, dict) else s.get('source') for s in (svc_def.get('secrets') or [])]},
        'health_check': _health(svc_def, node),
        'dependencies': {'compose_depends_on': deps, 'graph_edges': _graph_dependencies(node, ctx)},
        'external_replacements': [], 'synthetic_seed_generator': SEEDS_FOR.get(name, []),
        'database_migrations': None,
    }
    services = C.compose_services(ctx['compose'])
    if core_repo:
        rec = ctx['repo_info'].get(core_repo, {})
        target = C.CORE_REPOS[core_repo]
        repo_dir = _repo_dir(core_repo, ctx)
        migrate_svc = services.get(target + '-migrate') or services.get(core_repo + '-migrate')
        recipe.update({
            'repository': rec.get('repository', 'razorpay/' + core_repo), 'sha': rec.get('sha'),
            'source': {'accepted_copy': rec.get('accepted_path'), 'pristine_clone': rec.get('clone_path'),
                       'accepted_source_commit': rec.get('accepted_source_commit'), 'admitted_files': rec.get('accepted_admitted_files')},
            'build_command': {
                'canonical': 'REPOS_ROOT=.local/twin-repos/accepted ARENA_TAG=<tag> bash ENV2_COMPOSE/build/build-host.sh ' + target,
                'record': 'python3 ENV2_COMPOSE/build/record-rebuild.py %s --base-evidence <.local/twin-repos/build-evidence-*> --repos-root .local/twin-repos/accepted' % target,
                'steps': _build_host_steps(target, ctx),
                'env': 'GOPROXY=off GONOPROXY=none GONOSUMDB=none GOSUMDB=sum.golang.org GOTOOLCHAIN=local GOFLAGS=-mod=readonly',
                'dockerfile': 'ENV2_COMPOSE/build/runtime-only.Dockerfile',
                'alternative_docker_build': 'ENV2_COMPOSE/build/%s.Dockerfile via ENV2_COMPOSE/build/build.sh' % target,
            },
            'schemas_and_contracts': {
                'migration_dirs': [str(Path(rec.get('accepted_path') or '<accepted>/' + core_repo) / d) for d in C.MIGRATION_DIRS.get(core_repo, [])],
                'migration_count': len(_migration_files(repo_dir, C.MIGRATION_DIRS.get(core_repo, []))) if repo_dir else None,
                'proto_modules': _proto_modules(repo_dir) if repo_dir else [],
                'proto_source': str(Path(ctx['repos_root']) / 'proto') if ctx.get('repos_root') else None,
                'config_template_dir': 'ENV2_COMPOSE/config/templates/base/' + target,
            },
            'configuration_hash': _config_hash_for(['config/templates/base/%s/' % target, 'docker-compose.yml'], ctx),
            'external_replacements': _external_replacements_for_core(target, ctx),
            'synthetic_seed_generator': CORE_SEEDS.get(core_repo, []),
            'database_migrations': {
                'compose_service': (target + '-migrate') if (target + '-migrate') in services else None,
                'command': ' '.join([*(migrate_svc.get('entrypoint') or []), *(migrate_svc.get('command') or [])]) if migrate_svc else None,
                'image': migrate_svc.get('image') if migrate_svc else None,
                'run': 'cd ENV2_COMPOSE && docker compose --profile migrations run --rm %s-migrate' % target,
            },
        })
        recipe.update(fidelity_for(name, 'core', ctx, node))
    elif _is_substitute(svc_def or {}) or name in SUB_REPLACES:
        replaces, repo = SUB_REPLACES.get(name, ([], None))
        sub_dir = ctx['env2'] / 'substitutes' / name
        contract = sub_dir / 'CONTRACT.md'
        dockerfile = build.get('dockerfile') if isinstance(build, dict) else None
        context = build.get('context') if isinstance(build, dict) else None
        rec = ctx['repo_info'].get(repo, {}) if repo else {}
        graph_impl = []
        if node and ctx.get('graph'):
            graph_impl = sorted(e['to'] for e in ctx['graph'].get('edges', []) if e.get('from') == node.get('id') and e.get('type') == 'implements')
        recipe.update({
            'repository': 'twin-local: ENV2_COMPOSE/substitutes/' + name if sub_dir.is_dir() else ('twin-local: ' + str(dockerfile)),
            'sha': None, 'replaced_source_repo': rec.get('repository') if repo else None, 'replaced_source_sha': rec.get('sha') if repo else None,
            'build_command': {
                'canonical': 'cd ENV2_COMPOSE && docker compose build ' + name,
                'dockerfile': ('ENV2_COMPOSE/%s/%s' % (str(context).lstrip('./'), dockerfile)) if context and dockerfile else dockerfile,
                'build_args': {k: str(v) for k, v in ((build.get('args') or {}).items() if isinstance(build, dict) else [])},
                'steps': [],
            },
            'schemas_and_contracts': {
                'contract': 'ENV2_COMPOSE/substitutes/%s/CONTRACT.md' % name if contract.is_file() else None,
                'contract_sha256': C.sha256_file(contract) if contract.is_file() else None,
                'server': 'ENV2_COMPOSE/substitutes/%s/server.py' % name if (sub_dir / 'server.py').is_file() else None,
            },
            'configuration_hash': _config_hash_for(['substitutes/%s/' % name, 'docker-compose.yml'], ctx) if sub_dir.is_dir() else _config_hash_for(['docker-compose.yml'], ctx),
            'external_replacements': sorted(set(replaces) | set(graph_impl)),
        })
        recipe.update(fidelity_for(name, 'mozart-real' if name == 'mozart-mock' else ('harness' if name == 'verifier' else 'substitute'), ctx, node))
    elif name == 'cron-driver':
        replaces, _ = SUB_REPLACES['cron-driver']
        recipe.update({
            'repository': 'twin-local: ENV2_COMPOSE/scripts/cron-driver', 'sha': None,
            'build_command': {'canonical': 'none (python:3.12-alpine with ENV2_COMPOSE/scripts/cron-driver/driver.py mounted)', 'steps': []},
            'schemas_and_contracts': {'schedule': 'ENV2_COMPOSE/scripts/cron-driver/driver.py'},
            'configuration_hash': _config_hash_for(['scripts/cron-driver/driver.py', 'docker-compose.yml'], ctx),
            'external_replacements': replaces,
        })
        recipe.update(fidelity_for(name, 'substitute', ctx, node))
    else:
        recipe.update({
            'repository': 'upstream image ' + str(image), 'sha': None,
            'build_command': {'canonical': 'not built in twin; pulled: docker pull ' + str(image), 'steps': []},
            'schemas_and_contracts': {'init_scripts': SEEDS_FOR.get(name, [])},
            'configuration_hash': _config_hash_for(['docker-compose.yml'], ctx),
            'external_replacements': [],
        })
        recipe.update(fidelity_for(name, 'infra', ctx, node))
    return recipe


def recipe_for_repo(repo: str, ctx: dict) -> dict:
    rec = ctx['repo_info'].get(repo) or repo_record(repo, ctx)
    node = None
    if ctx.get('graph'):
        for n in ctx['graph'].get('nodes', []):
            if n.get('id') == 'repo:' + repo:
                node = n
                break
    services = C.compose_services(ctx['compose'])
    core = repo in C.CORE_REPOS
    image = 'rzp-arena/%s' % C.CORE_IMAGE[repo] if core else None
    compose_users = sorted(n for n, s in services.items() if str(s.get('image', '')).startswith(image + ':')) if image else []
    clone = _clone_dir(repo, ctx)
    repo_dir = _repo_dir(repo, ctx)
    migrations = _migration_files(repo_dir, C.MIGRATION_DIRS.get(repo, [])) if repo_dir else {}
    recipe = {
        'schema_version': RECIPE_SCHEMA_VERSION, 'id': 'repo-' + repo, 'kind': 'source_repo',
        'repository': rec.get('repository', 'razorpay/' + repo), 'sha': rec.get('sha'), 'head_date': rec.get('head_date'),
        'dirty': rec.get('dirty'), 'graph_node': node.get('id') if node else None,
        'source': {'pristine_clone': rec.get('clone_path'), 'accepted_copy': rec.get('accepted_path'),
                   'accepted_source_commit': rec.get('accepted_source_commit')},
        'twin_ref': compose_users or None,
        'schemas_and_contracts': {
            'migration_dirs': [str((repo_dir or Path('<clone>/' + repo)) / d) for d in C.MIGRATION_DIRS.get(repo, []) if repo_dir is None or (repo_dir / d).is_dir()],
            'migration_count': len(migrations) if repo_dir else None,
            'migrations_digest': C.digest_of_map(migrations) if migrations else None,
            'proto_modules': _proto_modules(repo_dir) if repo_dir else [],
        },
        'dependencies': {'compose_depends_on': [], 'graph_edges': _graph_dependencies(node, ctx)},
        'synthetic_seed_generator': CORE_SEEDS.get(repo, []),
    }
    if core:
        target = C.CORE_REPOS[repo]
        recipe.update({
            'build_command': {'canonical': 'REPOS_ROOT=.local/twin-repos/accepted ARENA_TAG=<tag> bash ENV2_COMPOSE/build/build-host.sh ' + target,
                              'prepare': 'python3 ENV2_COMPOSE/build/prepare-repos.py --source-root <repos-root> --destination .local/twin-repos/<new>',
                              'verify_inputs': 'python3 ENV2_COMPOSE/build/check-inputs.py <build-evidence>/provenance.json --verify-modules',
                              'steps': _build_host_steps(target, ctx)},
            'runtime': {'image': image + ':${ARENA_TAG:-local}', 'compose_services': compose_users},
            'health_check': _health(services.get(compose_users[0], {}), None) if compose_users else None,
            'configuration_hash': _config_hash_for(['config/templates/base/%s/' % target], ctx),
            'database_migrations': {'compose_service': target + '-migrate' if (target + '-migrate') in services else None,
                                    'run': 'cd ENV2_COMPOSE && docker compose --profile migrations run --rm %s-migrate' % target},
            'external_replacements': _external_replacements_for_core(target, ctx),
        })
        recipe.update(fidelity_for(repo, 'core', ctx, _graph_node_for(compose_users[0], ctx) if compose_users else None))
    else:
        how = describe_repo_build(repo, ctx)
        subs = REPO_SUBSTITUTES.get(repo, [])
        recipe.update({
            'build_command': {'canonical': how['summary'], 'steps': how['commands']},
            'runtime': {'image': None, 'executable': None, 'note': 'not booted in the twin' + (('; replaced by ' + ', '.join(subs)) if subs else '')},
            'health_check': node.get('health') if node else None,
            'configuration_hash': None,
            'database_migrations': {'dirs': recipe['schemas_and_contracts']['migration_dirs'], 'run': ('cd <clone>/%s && go run ./cmd/migration up' % repo) if repo_dir and (repo_dir / 'cmd/migration').is_dir() else None},
            'external_replacements': subs,
        })
        if repo == 'batch':
            types = sorted(p.relative_to(clone).as_posix() for p in (clone / 'src/main/resources').rglob('payout*.json')) if clone and (clone / 'src/main/resources').is_dir() else []
            recipe['schemas_and_contracts']['batch_payout_types'] = types
        if repo == 'proto':
            recipe['schemas_and_contracts']['consumers'] = {r: _proto_modules(_repo_dir(r, ctx)) for r in ('payouts', 'ledger') if _repo_dir(r, ctx)}
        recipe.update(fidelity_for(repo, 'repo' if (subs or rec.get('present')) else 'repo-unmapped', ctx, node))
        if not subs and repo in ('kube-manifests', 'config-proto', 'goutils'):
            recipe.update(fidelity_for(repo, 'repo-unmapped', ctx, node))
    return recipe


def all_recipes(ctx: dict) -> dict:
    out = {}
    for name, svc_def in sorted(C.compose_services(ctx['compose']).items()):
        out[name] = recipe_for_compose_service(name, svc_def or {}, ctx)
    for repo in sorted(ctx['repo_info']):
        out['repo-' + repo] = recipe_for_repo(repo, ctx)
    return out


def recipe_hash(recipe: dict) -> str:
    stable = {k: v for k, v in recipe.items() if k not in VOLATILE_KEYS}
    return C.sha256_json(stable)


def recipe_hashes(ctx: dict) -> dict:
    return {rid: recipe_hash(r) for rid, r in all_recipes(ctx).items()}


def _no_alias_dumper():
    """SafeDumper that never emits &anchors/*aliases (recipes must be readable stand-alone)."""
    import yaml

    class _Dumper(yaml.SafeDumper):
        def ignore_aliases(self, data):  # noqa: D401 - yaml hook
            return True

    return _Dumper


def write_recipes(recipes: dict, out_dir: Path, generated_at: str | None = None) -> list:
    import yaml
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or C.utc_now()
    dumper = _no_alias_dumper()
    paths = []
    for rid, recipe in sorted(recipes.items()):
        body = dict(recipe)
        body['recipe_hash'] = recipe_hash(recipe)
        body['generated_at'] = generated_at
        path = out_dir / (rid + '.yaml')
        header = '# Twin recipe for %s -- generated by scripts/snapshot/recipes.py; do not edit by hand.\n' % rid
        path.write_text(header + yaml.dump(body, Dumper=dumper, sort_keys=True, default_flow_style=False,
                                           width=120, allow_unicode=True))
        paths.append(path)
    return paths


def build_manifest(ctx: dict, recipes: dict, snapshot: dict | None = None) -> dict:
    """The domain snapshot manifest: repo SHAs, config hashes, schema versions, images, graph, recipes index."""
    from capture import build_snapshot  # local import: capture imports recipes
    snap = snapshot or build_snapshot(root=ctx['root'], repos_root=ctx['repos_root'], accepted=ctx['accepted'],
                                      graph_path=ctx['root'] / 'reports/domain/PAYOUTS_FUNCTIONAL_GRAPH.json',
                                      with_docker=True, ctx=ctx)
    return {
        'schema_version': 1, 'generated_at': C.utc_now(), 'twin': snap['twin'],
        'graph_version': snap['graph']['version'],
        'graph_counts': {'nodes': snap['graph']['node_count'], 'edges': snap['graph']['edge_count']},
        # --- required manifest keys ------------------------------------------------
        'repositories': {n: {k: r.get(k) for k in ('repository', 'path', 'sha', 'dirty', 'head_date', 'kind',
                                                   'accepted_path', 'accepted_source_commit', 'accepted_matches_provenance')}
                         for n, r in snap['repos'].items()},
        'config_hashes': snap['config']['hashes'],
        'config_digest': snap['config']['digest'],
        'schema_versions': {s: {'digest': v['digest'], 'count': len(v['files']), 'dirs': v['dirs'],
                                'files': sorted(v['files'])} for s, v in snap['schemas'].items()},
        'image_digests': snap['images'],
        'image_digests_reason': snap.get('images_reason'),
        'recipes': {rid: {'path': 'reports/domain/recipes/%s.yaml' % rid, 'recipe_hash': recipe_hash(r), 'kind': r['kind'],
                          'fidelity_label': r.get('fidelity_label')} for rid, r in sorted(recipes.items())},
        # --- supporting detail ------------------------------------------------------
        'contracts': {'digest': snap['contracts']['digest'], 'proto': {s: v['digest'] for s, v in snap['contracts']['proto'].items()},
                      'substitutes': snap['contracts']['substitutes'], 'batch_types_digest': snap['contracts']['batch_types_digest']},
        'flags': {k: v['digest'] for k, v in snap['flags'].items()},
        'compose': {'digest': snap['compose']['digest'], 'service_count': len(snap['compose']['services']),
                    'services': snap['compose']['services']},
        'fidelity_counts': _fidelity_counts(recipes),
        'snapshot': snap.get('_path'),
        'note': snap.get('note'),
    }


def _fidelity_counts(recipes: dict) -> dict:
    counts: dict = {}
    for r in recipes.values():
        label = r.get('fidelity_label') or 'unknown'
        counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items()))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', type=Path, default=C.RECIPES_DIR, help='recipe output directory')
    ap.add_argument('--manifest', type=Path, default=None, help='manifest path (default: SNAPSHOT_MANIFEST.json next to the tracked recipes, or inside --out when --out is given)')
    ap.add_argument('--no-manifest', action='store_true')
    ap.add_argument('--no-docker', action='store_true', help='do not inspect images for the manifest')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args(argv)
    if args.manifest is None:  # M8: --out must never overwrite the tracked manifest
        args.manifest = C.MANIFEST_PATH if Path(args.out).resolve() == C.RECIPES_DIR.resolve() else Path(args.out) / 'SNAPSHOT_MANIFEST.json'
    ctx = build_context()
    recipes = all_recipes(ctx)
    if args.list:
        print('\n'.join(sorted(recipes)))
        return 0
    paths = write_recipes(recipes, args.out)
    kinds = {}
    for r in recipes.values():
        kinds[r['kind']] = kinds.get(r['kind'], 0) + 1
    print('recipes: %d written to %s (%s)' % (len(paths), args.out, ', '.join('%s=%d' % kv for kv in sorted(kinds.items()))))
    if not args.no_manifest:
        from capture import build_snapshot
        snap = build_snapshot(root=ctx['root'], repos_root=ctx['repos_root'], accepted=ctx['accepted'],
                              with_docker=not args.no_docker, ctx=ctx)
        manifest = build_manifest(ctx, recipes, snap)
        C.write_json(args.manifest, manifest)
        print('manifest: %s (repos=%d recipes=%d graph=%s)' % (args.manifest, len(manifest['repositories']), len(manifest['recipes']),
                                                              (manifest['graph_version'] or 'absent')[:12]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
