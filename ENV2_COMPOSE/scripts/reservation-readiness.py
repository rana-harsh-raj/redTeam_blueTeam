#!/usr/bin/env python3
"""Verify the real reservation heartbeat after the startup reconciliation.

The monolith container already holds the scoped Payouts API credential needed
for this read. No credential is added to cron-driver, passed on a command line,
or returned to the host. This never writes Redis or a payout row.
"""
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
READ = r'''
import base64,json,os,sys,urllib.request
from pathlib import Path
mid,bid=sys.argv[1:]
password=Path(os.environ['PS_RELAY_AUTH_PASS_FILE']).read_text().strip()
token=base64.b64encode((os.environ['PS_RELAY_AUTH_USER']+':'+password).encode()).decode()
url='http://payouts-api:9400/v1/inflight_reservations?merchant_id='+mid+'&balance_id='+bid
try:
    req=urllib.request.Request(url,headers={'Authorization':'Basic '+token})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req,timeout=10) as response:
        state=json.load(response)
    if state.get('store_trusted') is not True:
        raise RuntimeError('reservation heartbeat is absent')
except Exception:
    print('Reservation readiness failed: real API did not confirm a trusted store.',file=sys.stderr)
    sys.exit(1)
print(json.dumps({'reservation_store_trusted':True,'merchant_id':mid,'balance_id':bid}))
'''


def main():
    index = json.loads((ROOT / 'seeds/generated/scenario-index.json').read_text())
    merchants = [group['merchants']['M2'] for group in index['namespaces']]
    merchant = next(m for m in merchants if m['archetype'] == 'direct')
    command = ['docker', 'compose', '--env-file', '.env.arena', '-f', 'docker-compose.yml',
               '--profile', 'substitutes', 'exec', '-T', 'monolith-stub', 'python3', '-c', READ,
               merchant['merchant_id'], merchant['balance_id']]
    return subprocess.run(command, cwd=ROOT, timeout=30).returncode


if __name__ == '__main__':
    raise SystemExit(main())
