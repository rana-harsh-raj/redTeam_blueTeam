#!/usr/bin/env python3
"""Run the same fixed local golden payout used by the Explorer."""
import argparse
import json
import os
import signal
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
import server

# Ensure a terminated command unwinds golden() and removes its scoped runner.
def terminate(signum, _frame):
    raise SystemExit(128+signum)
signal.signal(signal.SIGTERM, terminate)
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--with-egress-audit',action='store_true');a=p.parse_args()
if a.with_egress_audit:
    name='twin-live-'+uuid.uuid4().hex[:12]
    folder=Path(os.environ.get('ARENA_RUN_DIR',str(server.ROOT.parent/'reports/implementation/runs'/('golden-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())))))
    env=dict(os.environ,ARENA_LIVE_CONTAINER=name,ARENA_LIVE_RUN_DIR=str(folder))
    cmd=[sys.executable,str(server.ARENA/'network/egress_audit.py'),'--duration','180','--output',str(folder),'--command-container',name,'--',sys.executable,str(Path(__file__).resolve())]
    process=subprocess.Popen(cmd,env=env)
    try:
        code=process.wait()
    finally:
        if process.poll() is None:
            # Let the audit wrapper terminate its runner and finish its own
            # scoped, bounded capture/network cleanup before this CLI exits.
            process.terminate()
            process.wait()
    sys.exit(code)
# Pin the run directory here so the boot fingerprint can be copied beside the
# result; server.golden() would otherwise pick this same default internally.
folder=Path(os.environ.get('ARENA_LIVE_RUN_DIR',str(server.ROOT.parent/'reports/implementation/runs'/('explorer-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8]))))
os.environ['ARENA_LIVE_RUN_DIR']=str(folder)
folder.mkdir(parents=True,exist_ok=True)
fingerprint=server.ARENA/'.runtime/arena-fingerprint.json'
if fingerprint.is_file():shutil.copyfile(fingerprint,folder/'arena-fingerprint.json')
server.golden()
print(json.dumps({k:v for k,v in server.RUN.items() if k!='result'},indent=2))
sys.exit(0 if server.RUN.get('status')=='passed' else 1)
