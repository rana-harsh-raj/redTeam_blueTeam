#!/usr/bin/env python3
"""Mechanically derive the shared-ingress contract inventory from the pinned sources.

  python3 scripts/m7/derive_contract.py [--api <api clone>] [--payouts <payouts clone>]
                                        [--vendor-payments <vp clone>] [--out routes.json]

For every route the shared ingress implements (SELECTED below, keyed by the API monolith's
Route.php route NAME), the script reads the pinned api/app/Http/Route.php and records:
  method, path, controller, the auth groups the name appears in (private/proxy/internal/admin/
  userWhitelist/idempotentRoutesConfig), the internal applications allowed to call it
  (Route::$internalApps), and the line numbers of every observation.
It then records the Payouts-service route each selected route proxies to (from
payouts/internal/routing/router/*.go), and the vendor-payments call sites that exercise the
internal routes (rxclient/apicaller.go path literals). Nothing is invented: a route whose
evidence cannot be located is emitted with `evidence_missing: true` and the ingress contract
test refuses to serve it.
"""
import argparse, hashlib, json, re, subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROOT = Path((ROOT / '.local' / 'repos-root').read_text().strip()) if (ROOT / '.local' / 'repos-root').exists() else None

# route name -> (ingress role, PS upstream route or None, note)
SELECTED = {
    'payout_create':                      ('merchant',   'POST /v1/payouts',                                   'classic + Direct/current-account create; PS resolves account_number ownership'),
    'payout_fetch_by_id':                 ('merchant',   'GET /v1/payouts/:id',                                ''),
    'payout_fetch_multiple':              ('merchant',   'GET /v1/payouts',                                    ''),
    'payout_cancel':                      ('merchant',   'POST /v1/payouts/cancel_payout/:payout_id',          ''),
    'payout_approve':                     ('dashboard',  'POST /v1/payouts/payouts_internal/:payout_id/approve','Workflow.php WORKFLOW_PAYOUT_SERVICE_URI'),
    'payout_reject':                      ('dashboard',  'POST /v1/payouts/payouts_internal/:payout_id/reject', 'Workflow.php WORKFLOW_PAYOUT_SERVICE_URI'),
    'payout_create_with_otp':             ('dashboard',  'POST /v1/payouts',                                   'proxy auth + OTP, PS proxy passport'),
    'payout_bulk_create':                 ('batch',      'POST /v1/payouts/bulk',                              'Batch service via proxy auth; X-Entity-Id merchant'),
    'payout_create_internal':             ('internal',   'POST /v1/payouts/payouts_internal',                  'Create.php CREATE_PAYOUT_INTERNAL_SERVICE_URI'),
    'payout_create_on_internal_contact':  ('internal',   'POST /v1/payouts/internal_contact_payout',           'Create.php CREATE_INTERNAL_CONTACT_PAYOUT_SERVICE_URI'),
    'payout_fetch_by_id_internal':        ('internal',   'GET /v1/payouts/payouts_internal/:payout_id',        ''),
    'payout_update_tax_payment_id':       ('internal',   None,                                                 'PayoutsDetails/Core.php updateTaxPayment: id-only update, no merchant scoping in pinned source'),
    'payout_approve_internal':            ('internal',   'POST /v1/payouts/payouts_internal/:payout_id/approve','workflows app'),
    'payout_reject_internal':             ('internal',   'POST /v1/payouts/payouts_internal/:payout_id/reject', 'workflows app'),
    'payout_cancel_internal':             ('internal',   'POST /v1/payouts/cancel_payout/:payout_id',          ''),
    'fund_account_get_internal':          ('internal',   None,                                                 'FundAccount/Service.php fetch: findByPublicIdAndMerchant (merchant-scoped)'),
    'fund_account_list_internal':         ('internal',   None,                                                 'merchant-scoped'),
    'fund_account_create_internal':       ('internal',   None,                                                 'merchant-owned record'),
    'contact_get_internal':               ('internal',   None,                                                 'merchant-scoped'),
    'contact_list_internal':              ('internal',   None,                                                 'merchant-scoped'),
    'contact_create_internal':            ('internal',   None,                                                 'merchant-owned record'),
    'banking_accounts_list_internal':     ('internal',   None,                                                 'merchant-scoped'),
    'vendor_payment_verify_otp':          ('internal',   None,                                                 'VendorPaymentController@verifyOtp -> UserCore verifyOtp (Raven); synthetic OTP store'),
    'admin_get_free_payouts_attributes':  ('admin',      'GET /v1/admin/payouts/:balance_id/free_payout',      'admin passport'),
    'payout_reject_admin_bulk':           ('admin',      'POST /v1/payouts/payouts_internal/:payout_id/reject', 'bulkRejectFundAccountPayout -> per-payout reject'),
}
GROUPS = ['private', 'proxy', 'internal', 'admin', 'userWhitelist', 'idempotentRoutesConfig', 'direct', 'session']


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def git_head(p):
    try:
        return subprocess.check_output(['git', '-C', str(p), 'rev-parse', 'HEAD'], text=True).strip()
    except Exception:
        return None


def array_body(src, name):
    m = re.search(r'(?:public|protected) static \$' + name + r'\s*=\s*\[', src)
    if not m:
        return None, None
    start = m.end(); depth = 1; i = start
    while depth and i < len(src):
        c = src[i]; depth += (c == '['); depth -= (c == ']'); i += 1
    return src[start:i], src[:m.start()].count('\n') + 1


def line_of(src, pos):
    return src[:pos].count('\n') + 1


def derive_api(api):
    route_php = api / 'app/Http/Route.php'
    src = route_php.read_text(errors='replace')
    out = {}
    api_routes, _ = array_body(src, 'apiRoutes')
    rx = re.compile(r"'(?P<name>[\w\-]+)'\s*=>\s*\[\s*'(?P<method>\w+)'\s*,\s*'(?P<path>[^']+)'\s*,\s*'(?P<ctl>[^']+)'\s*\]")
    api_start = src.find(api_routes)
    for m in rx.finditer(api_routes):
        if m.group('name') in SELECTED:
            out[m.group('name')] = {'method': m.group('method').upper(), 'path': '/v1/' + m.group('path'),
                                    'controller': m.group('ctl'), 'groups': {}, 'internal_apps': [],
                                    'source_refs': ['api/app/Http/Route.php:%d' % line_of(src, api_start + m.start())]}
    for g in GROUPS:
        body, base_line = array_body(src, g)
        if body is None:
            continue
        body_start = src.find(body)
        for name, rec in out.items():
            mm = re.search(r"'" + re.escape(name) + r"'\s*(,|=>)", body)
            if mm:
                ln = line_of(src, body_start + mm.start())
                rec['groups'][g] = ln
                rec['source_refs'].append('api/app/Http/Route.php:%d' % ln)
    body, _ = array_body(src, 'internalApps')
    body_start = src.find(body)
    for app in re.finditer(r"'([\w\-]+)'\s*=>\s*\[(.*?)\n\s*\],", body, re.S):
        for name, rec in out.items():
            mm = re.search(r"'" + re.escape(name) + r"'", app.group(2))
            if mm:
                rec['internal_apps'].append(app.group(1))
                rec['source_refs'].append('api/app/Http/Route.php:%d' % line_of(src, body_start + app.start(2) + mm.start()))
    for name in SELECTED:
        out.setdefault(name, {'evidence_missing': True, 'groups': {}, 'internal_apps': [], 'source_refs': []})
    return out, sha(route_php)


def derive_payouts(payouts):
    routes = {}
    rdir = payouts / 'internal/routing/router'
    for f in sorted(rdir.glob('*.go')):
        if f.name.endswith('_test.go'):
            continue
        src = f.read_text(errors='replace')
        for var in re.finditer(r'var (\w+) = Route\{', src):
            start = var.end(); depth = 1; i = start
            while depth and i < len(src):
                c = src[i]; depth += (c == '{'); depth -= (c == '}'); i += 1
            body = src[start:i]
            gm = re.search(r'group:\s*"([^"]*)"', body)
            group = gm.group(1) if gm else ''
            mw = re.search(r'middleware:\s*\[\]gin\.HandlerFunc\{(.*?)\n\s*\},', body, re.S)
            auth = re.findall(r'(BasicAuth|ServiceBasicAuthOrPassport|PassportAuthentication)\(([^)]*)\)', mw.group(1) if mw else '')
            for ep in re.finditer(r'http\.Method(\w+),\s*\n?\s*"([^"]*)"', body):
                path = (group + ep.group(2)).replace('//', '/')
                key = '%s %s' % (ep.group(1).upper(), path if path.startswith('/') else '/' + path)
                routes[key] = {'file': 'payouts/internal/routing/router/' + f.name,
                               'line': line_of(src, start + ep.start()),
                               'auth': [{'middleware': a, 'args': b.strip()} for a, b in auth]}
    return routes


def derive_vendor_payments(vp):
    sites = {}
    if not vp or not vp.is_dir():
        return sites
    for f in sorted((vp / 'internal').rglob('*.go')):
        if f.name.endswith('_test.go'):
            continue
        src = f.read_text(errors='replace')
        for m in re.finditer(r'"(v1/[\w\-/]+/?)"', src):
            lit = m.group(1)
            if any(k in lit for k in ('payouts_internal', 'internalContactPayout', 'contacts_internal', 'fund_accounts_internal', 'banking_accounts_internal', 'verify-otp')):
                sites.setdefault(lit, []).append('vendor-payments/' + str(f.relative_to(vp)) + ':%d' % line_of(src, m.start()))
    return sites


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--api', default=str(DEFAULT_ROOT / 'api') if DEFAULT_ROOT else None)
    ap.add_argument('--payouts', default=str(DEFAULT_ROOT / 'payouts') if DEFAULT_ROOT else None)
    ap.add_argument('--vendor-payments', default=str(DEFAULT_ROOT / 'vendor-payments') if DEFAULT_ROOT else None)
    ap.add_argument('--out', default=str(ROOT / 'ENV2_COMPOSE/substitutes/api-ingress/contract/routes.json'))
    a = ap.parse_args()
    api, payouts, vp = Path(a.api), Path(a.payouts), Path(a.vendor_payments) if a.vendor_payments else None
    routes, route_php_sha = derive_api(api)
    ps = derive_payouts(payouts)
    vp_sites = derive_vendor_payments(vp)
    inventory = {}
    for name, (role, upstream, note) in SELECTED.items():
        rec = dict(routes[name]); rec.update(ingress_role=role, ps_upstream=upstream, note=note)
        if upstream:
            rec['ps_upstream_evidence'] = ps.get(upstream) or {'evidence_missing': True}
        rec['vendor_payments_call_sites'] = {k: v for k, v in vp_sites.items() if rec.get('path', '').lstrip('/').rstrip('/') in k.rstrip('/') or k.rstrip('/').replace('v1/', '/v1/') == rec.get('path', '').rstrip('/').replace('{id}', '')}
        inventory[name] = rec
    doc = {'schema_version': 1, 'generated_at': datetime.now(timezone.utc).isoformat(),
           'derivation': 'scripts/m7/derive_contract.py (mechanical: api Route.php arrays, payouts router files, vendor-payments path literals)',
           'sources': {'api': {'path': str(api), 'sha': git_head(api), 'route_php_sha256': route_php_sha},
                       'payouts': {'path': str(payouts), 'sha': git_head(payouts)},
                       'vendor-payments': {'path': str(vp) if vp else None, 'sha': git_head(vp) if vp else None}},
           'routes': inventory,
           'evidence_missing': sorted(n for n, r in inventory.items() if r.get('evidence_missing') or (r.get('ps_upstream_evidence') or {}).get('evidence_missing'))}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'routes': len(inventory), 'evidence_missing': doc['evidence_missing'], 'out': a.out}))


if __name__ == '__main__':
    main()
