#!/usr/bin/env python3
"""Bounded DNS controls executed inside the same runtime verifier/capture window."""
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def lookup(name):
    code='import json,socket,sys; print(json.dumps(sorted(set(x[4][0] for x in socket.getaddrinfo(sys.argv[1],None)))))'
    try:
        p=subprocess.run([sys.executable,'-c',code,name],capture_output=True,text=True,timeout=8)
        return {'resolved':p.returncode==0,'addresses':json.loads(p.stdout) if p.returncode==0 else [],'timed_out':False}
    except subprocess.TimeoutExpired:
        return {'resolved':False,'addresses':[],'timed_out':True}


def main():
    checks={name:lookup(name) for name in ('payouts-api','ledger-api','example.com')}
    private=lambda name:checks[name]['resolved'] and all(ipaddress.ip_address(x).is_private for x in checks[name]['addresses'])
    peers={}
    for host,port in [('payouts-api',9400),('ledger-api',8080),('fts-web',8080),('xbalances-server',8081),('cfa-server',8081)]:
        try:
            with socket.create_connection((host,port),timeout=3):peers[host]=True
        except OSError:peers[host]=False
    ok=private('payouts-api') and private('ledger-api') and not checks['example.com']['resolved'] and all(peers.values())
    result={'status':'passed' if ok else 'failed','checks':checks,'peer_listeners':peers,'note':'DNS controls run in the verifier container; packet capture independently checks runtime destinations.'}
    out=Path(os.environ.get('ARENA_TRACE_DIR','/results'));out.mkdir(parents=True,exist_ok=True)
    (out/'dns.json').write_text(json.dumps(result,indent=2)+'\n')
    print('Runtime DNS controls: '+result['status'],flush=True)
    return 0 if ok else 1

if __name__=='__main__':sys.exit(main())
