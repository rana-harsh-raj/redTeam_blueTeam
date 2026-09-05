#!/usr/bin/env python3
"""Loopback-only static Explorer and a fixed, bounded synthetic payout action."""
import argparse
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parent
ARENA=ROOT.parent/'ENV2_COMPOSE'
COMPOSE=['docker','compose','--env-file',str(ARENA/'.env.arena'),'-f',str(ARENA/'docker-compose.yml')]
for p in ('datastores','migrations','substitutes','core','verify'): COMPOSE+=['--profile',p]
LOCK=threading.Lock()
RUN=None
REQUIRED=('payouts-api','fts-web','ledger-api','cfa-server','xbalances-server','kong-lite','monolith-stub','mozart-sim','stork-capture','merchant-webhook-sink')

def health():
    try:
        p=subprocess.run(COMPOSE+['ps','--format','json'],capture_output=True,text=True,timeout=12,check=True)
        raw=p.stdout.strip()
        rows=json.loads(raw) if raw.startswith('[') else [json.loads(x) for x in raw.splitlines() if x]
        by={x['Service']:x for x in rows}
        services=[]
        for name in REQUIRED:
            row=by.get(name,{})
            ok=row.get('State')=='running' and row.get('Health','') in ('','healthy')
            services.append({'name':name,'ok':ok,'status':row.get('Health') or row.get('State') or 'offline'})
        return {'ready':all(s['ok'] for s in services),'services':services}
    except (OSError,ValueError,subprocess.SubprocessError):
        return {'ready':False,'services':[],'message':'Local arena is offline. Saved exploration is available.'}

def golden():
    global RUN
    stamp=time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())
    result_dir=Path(os.environ.get('ARENA_LIVE_RUN_DIR',str(ROOT.parent/'reports'/'implementation'/'runs'/('explorer-'+stamp+'-'+uuid.uuid4().hex[:8]))))
    container=os.environ.get('ARENA_LIVE_CONTAINER','twin-explorer-'+uuid.uuid4().hex[:16])
    tmp=None
    owns_container=False
    result={'status':'failed','message':'Local runner did not complete.'}
    try:
        result_dir.mkdir(parents=True,exist_ok=True)
        if not re.fullmatch(r'twin-(?:explorer|live)-[a-zA-Z0-9-]{6,64}',container):
            raise ValueError('invalid scoped runner name')
        existing=subprocess.run(['docker','container','ls','--all','--filter','name=^/'+container+'$','--format','{{.Names}}'],capture_output=True,text=True,timeout=10,check=True)
        if existing.stdout.strip():raise ValueError('runner name already exists')
        if any((result_dir/name).exists() for name in ('live-result.json','live-golden-shared.jsonl')):
            raise ValueError('result directory already contains a run')
        index=json.loads((ARENA/'seeds/generated/scenario-index.json').read_text())
        mid=index['baseline']['M1']['merchant_id']
        rec=json.loads((ARENA/'seeds/generated/merchants.json').read_text())['merchants'][mid]
        secret=(ARENA/'secrets/merchant-keys'/(rec['secret_file']+'.txt')).read_text().strip()
        runtime=ARENA/'.runtime';runtime.mkdir(exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix='explorer-key-',dir=runtime)
        with os.fdopen(fd,'w') as f:f.write(rec['key_id_live']+':'+secret+'\n')
        command=COMPOSE+['run','--name',container,'--no-deps','--entrypoint','python3','-v',str(result_dir)+':/results','-v',tmp+':/run/golden-merchant-key:ro','-e','ARENA_TRACE_DIR=/results','verifier','/verifier/scenario.py']
        owns_container=True
        completed=subprocess.run(command,cwd=ARENA,capture_output=True,text=True,timeout=125)
        path=result_dir/'live-result.json'
        if path.is_file():result=json.loads(path.read_text())
        else:result={'status':'failed','message':'Runner returned no result; confirm the verifier image was built.','exit_code':completed.returncode}
        if not isinstance(result,dict):raise ValueError('invalid result')
        if result.get('status')=='passed' and result.get('passed') is not True:raise ValueError('inconsistent result')
        if completed.returncode and result.get('status')=='passed':result={'status':'failed','message':'Result and runner exit disagree.'}
        # Only the allowlisted structured result is exposed. Raw command output
        # and runtime credentials never enter the browser or static export.
        captured=result_dir/'live-golden-shared.jsonl'
        if captured.is_file() and captured.stat().st_size<=4000000:
            result['trace']=[json.loads(line) for line in captured.read_text().splitlines() if line][:500]
            for event in result['trace']:
                body=event.get('data',{}).get('body',{})
                if isinstance(body,dict) and isinstance(body.get('deliveries'),list):
                    body['deliveries']=[d for d in body['deliveries'] if d.get('body',{}).get('payload',{}).get('payout',{}).get('entity',{}).get('id')==result.get('payout_id')]
    except subprocess.TimeoutExpired:
        result={'status':'failed','message':'Local scenario exceeded its 125-second runner limit.'}
    except (OSError,ValueError,KeyError,TypeError,subprocess.SubprocessError) as exc:
        result={'status':'failed','message':'Local scenario setup failed. Check generated fixtures and images.','error_type':type(exc).__name__}
    finally:
        try:
            if owns_container:
                subprocess.run(['docker','rm','-f',container],capture_output=True,timeout=20)
                check=subprocess.run(['docker','container','ls','--all','--filter','name=^/'+container+'$','--format','{{.Names}}'],capture_output=True,text=True,timeout=10)
                if check.returncode or check.stdout.strip():raise OSError('cleanup not confirmed')
        except (OSError,subprocess.SubprocessError):result={'status':'failed','message':'Runner cleanup could not be confirmed.'}
        if tmp:
            try:Path(tmp).unlink(missing_ok=True)
            except OSError:result={'status':'failed','message':'Runtime credential cleanup could not be confirmed.'}
        if result.get('status')=='passed' and result.get('passed') is True:
            try:
                saved=ROOT/'data/saved-run.json'
                fd,staged=tempfile.mkstemp(prefix='.saved-run-',dir=saved.parent)
                try:
                    with os.fdopen(fd,'w') as stream:stream.write(json.dumps(result,indent=2)+'\n')
                    os.replace(staged,saved)
                finally:Path(staged).unlink(missing_ok=True)
            except OSError:result={'status':'failed','message':'Successful run could not be saved.'}
        with LOCK:RUN={'status':result.get('status','failed'),'result':result,'evidence_directory':result_dir.name}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def setup(self):
        super().setup()
        self.connection.settimeout(5)
    def log_message(self,*args):pass
    def handle(self):
        try:super().handle()
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
    def valid_host(self):
        return len(self.headers.get_all('Host',[]))==1 and self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
    def respond(self,status,payload):
        body=json.dumps(payload).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def end_headers(self):
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
        super().end_headers()
    def do_GET(self):
        if not self.valid_host():return self.respond(403,{'message':'Loopback Host required.'})
        path=urlsplit(self.path).path
        if path=='/api/health':return self.respond(200,health())
        if path=='/api/run':
            with LOCK:result=RUN or {'status':'idle'}
            return self.respond(200,result)
        allowed=('/', '/index.html','/app.js','/style.css','/data/architecture.json','/data/saved-run.json')
        if path not in allowed:return self.respond(404,{'message':'No such Explorer resource.'})
        return super().do_GET()
    def do_HEAD(self):
        if not self.valid_host():return self.respond(403,{'message':'Loopback Host required.'})
        if urlsplit(self.path).path not in ('/','/index.html','/app.js','/style.css','/data/architecture.json','/data/saved-run.json'):
            return self.respond(404,{'message':'No such Explorer resource.'})
        return super().do_HEAD()
    def do_POST(self):
        global RUN
        if not self.valid_host():return self.respond(403,{'message':'Loopback Host required.'})
        origin=self.headers.get('Origin')
        expected='http://'+self.headers['Host']
        if origin!=expected or self.headers.get('X-Twin-Action')!='golden' or self.headers.get('Sec-Fetch-Site','same-origin')!='same-origin':return self.respond(403,{'message':'Same-origin Explorer action required.'})
        if self.path!='/api/run':return self.respond(404,{'message':'Unknown action.'})
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length',[]))!=1 or self.headers.get('Content-Type')!='application/json':return self.respond(400,{'message':'JSON request required.'})
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:return self.respond(400,{'message':'Invalid request length.'})
        if length<0 or length>1024:return self.respond(413,{'message':'Request too large.'})
        if self.rfile.read(length).strip()!=b'{}':return self.respond(400,{'message':'This action takes no parameters.'})
        with LOCK:
            if RUN and RUN.get('status')=='running':return self.respond(409,{'message':'A synthetic payout is already running.'})
            RUN={'status':'running','message':'Creating a Shared synthetic payout through Kong.'}
        threading.Thread(target=golden,daemon=False).start()
        return self.respond(202,RUN)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--port',type=int,default=18765);a=p.parse_args()
    # Non-daemon action threads finish their bounded runner and cleanup when
    # the UI server is stopped. SIGTERM follows the same shutdown path as Ctrl-C.
    def stop_server(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop_server)
    server=ThreadingHTTPServer(('127.0.0.1',a.port),Handler)
    print(f'Payouts Twin Explorer: http://127.0.0.1:{a.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
