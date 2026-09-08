#!/usr/bin/env python3
"""Loopback-only HTTP bridge to Kong on Docker's internal network.

Docker does not publish ports on an internal-only bridge. Each request crosses
Docker exec stdin to a fixed 127.0.0.1:8080 inside Kong. No runtime container gets
an internet-facing network, Docker socket mount, host-home mount or host key.
"""
import argparse
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT/'.runtime'
SELF = Path(__file__).resolve()
IO_TIMEOUT = 10
START_TIMEOUT = 10
STOP_TIMEOUT = 5
MAX_BODY = 1048576
# Request headers the loopback bridge forwards to kong-lite. The four original ones plus the
# M6 kong-lite ARENA-CONTROL headers (dashboard/proxy passport opt-in, see
# substitutes/kong-lite/CONTRACT.md); kong-lite strips the X-Arena-* headers before upstream.
FORWARDED_HEADERS=('authorization','content-type','x-payout-idempotency','x-request-id',
                   'x-arena-passport-consumer-type','x-arena-user-id','x-dashboard-user-role')
PROXY = '''import sys,json,base64,urllib.request,urllib.error
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): return None
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
def response(f):
 body=f.read(4194305)
 if len(body)>4194304: raise ValueError('Kong response too large')
 print(json.dumps({'status':f.code,'body':base64.b64encode(body).decode(),'content_type':f.headers.get('Content-Type','application/json')}))
x=json.load(sys.stdin)
r=urllib.request.Request('http://127.0.0.1:8080'+x['path'],data=base64.b64decode(x['body']) if x['body'] else None,method=x['method'],headers=x['headers'])
try:
 with opener.open(r,timeout=25) as f: response(f)
except urllib.error.HTTPError as f: response(f)
'''
COMPOSE = ['docker','compose','--env-file',str(ROOT/'.env.arena'),'-f',str(ROOT/'docker-compose.yml'),'--profile','datastores','--profile','substitutes']


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def allowed_path(target):
    if any(ord(c) < 33 or ord(c) == 127 for c in target):
        return False
    try:
        parsed = urllib.parse.urlsplit(target)
    except ValueError:
        return False
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    path = parsed.path
    if path in ('/_arena/health', '/_arena/mint'):
        return True
    # Public payout identifiers/actions use plain path segments. Reject
    # encoded separators, dot segments, and ambiguous alternate spellings.
    return re.fullmatch(r'/v1/payouts(?:/[A-Za-z0-9_-]+)*/?', path) is not None


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(IO_TIMEOUT)
        super().setup()

    def log_message(self,*args): pass
    def proxy(self):
        hosts = self.headers.get_all('Host', [])
        if len(hosts) != 1 or hosts[0] not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
            self.send_error(403,'loopback Host required');return
        origins = self.headers.get_all('Origin', [])
        if origins and (len(origins) != 1 or origins[0] != 'http://' + hosts[0]):
            self.send_error(403,'same-origin request required');return
        if self.headers.get('Sec-Fetch-Site') not in (None, 'same-origin', 'none'):
            self.send_error(403,'same-origin request required');return
        if self.path == '/_bridge/health':
            if self.command != 'GET':
                self.send_error(405);return
            raw=json.dumps({'bridge':'payouts-twin','pid':os.getpid()}).encode()
            self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
        if not allowed_path(self.path):
            self.send_error(404);return
        lengths=self.headers.get_all('Content-Length', [])
        if len(lengths)>1 or self.headers.get('Transfer-Encoding') is not None:
            self.send_error(400,'ambiguous request framing');return
        try: length=int(lengths[0]) if lengths else 0
        except ValueError: self.send_error(400);return
        if not 0 <= length <= MAX_BODY:
            self.send_error(413);return
        try: body=self.rfile.read(length)
        except OSError: self.send_error(408);return
        if len(body) != length:
            self.send_error(400,'incomplete request body');return
        request={'path':self.path,'method':self.command,'body':base64.b64encode(body).decode(),
            'headers':{k:v for k,v in self.headers.items() if k.lower() in FORWARDED_HEADERS}}
        try:
            res=subprocess.run(COMPOSE+['exec','-T','kong-lite','python3','-c',PROXY],input=json.dumps(request),text=True,capture_output=True,timeout=30,check=True)
            response=json.loads(res.stdout); raw=base64.b64decode(response['body'],validate=True)
            if not 100 <= int(response['status']) <= 599 or '\n' in response['content_type'] or '\r' in response['content_type']:
                raise ValueError('invalid Kong response')
            self.send_response(response['status']);self.send_header('Content-Type',response['content_type']);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        except (subprocess.SubprocessError,ValueError,OSError,KeyError,TypeError):
            self.send_error(502,'local Kong unavailable')
    do_GET=proxy
    do_POST=proxy
    do_PATCH=proxy
    do_DELETE=proxy


def remove_pidfile(pidfile, pid):
    try:
        if pidfile.read_text().strip() == str(pid):
            pidfile.unlink(missing_ok=True)
    except FileNotFoundError:
        pass


def probe(port, expected_pid=None):
    # A proxy setting or redirect must not move the readiness check elsewhere.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(f'http://127.0.0.1:{port}/_bridge/health',timeout=1) as response:
            data=json.loads(response.read(1024))
        return data.get('bridge') == 'payouts-twin' and (expected_pid is None or data.get('pid') == expected_pid)
    except (OSError,ValueError):
        return False


def stop_child(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=STOP_TIMEOUT)


def start(port, pidfile):
    if probe(port):
        print('Loopback bridge already running');return
    with (STATE/'ingress.log').open('a') as log:
        proc=subprocess.Popen([sys.executable,str(SELF),'serve','--port',str(port)],stdout=log,stderr=log,start_new_session=True)
    ready=False
    try:
        deadline=time.monotonic()+START_TIMEOUT
        while time.monotonic()<deadline:
            if proc.poll() is not None:
                raise SystemExit('Loopback bridge failed; see .runtime/ingress.log')
            if probe(port,proc.pid):
                ready=True
                print(f'Loopback bridge: http://127.0.0.1:{port}');return
            time.sleep(.1)
        raise SystemExit('Loopback bridge failed readiness')
    finally:
        if not ready:
            stop_child(proc)
            remove_pidfile(pidfile,proc.pid)


def owns_pid(pid):
    command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True,timeout=STOP_TIMEOUT).stdout
    return str(SELF)+' serve' in command


def stop(pidfile):
    try:
        pid=int(pidfile.read_text())
    except FileNotFoundError:
        return
    except ValueError:
        pidfile.unlink(missing_ok=True);return
    if pid<=1 or not owns_pid(pid):
        remove_pidfile(pidfile,pid);return
    try:
        os.kill(pid,signal.SIGTERM)
        deadline=time.monotonic()+STOP_TIMEOUT
        while time.monotonic()<deadline and owns_pid(pid):
            time.sleep(.1)
        if owns_pid(pid):
            os.kill(pid,signal.SIGKILL)
            deadline=time.monotonic()+STOP_TIMEOUT
            while time.monotonic()<deadline and owns_pid(pid):
                time.sleep(.1)
            if owns_pid(pid):
                raise SystemExit('Loopback bridge shutdown could not be confirmed')
    except ProcessLookupError:
        pass
    remove_pidfile(pidfile,pid)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=['start','stop','serve']);p.add_argument('--port',type=int,default=18080);args=p.parse_args()
    if not 1 <= args.port <= 65535: p.error('port must be between 1 and 65535')
    STATE.mkdir(exist_ok=True,mode=0o700); pidfile=STATE/'ingress.pid'
    if args.action=='serve':
        httpd=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        pidfile.write_text(str(os.getpid()))
        def interrupt(signum,frame): raise KeyboardInterrupt
        for signum in (signal.SIGINT,signal.SIGTERM): signal.signal(signum,interrupt)
        try: httpd.serve_forever()
        except KeyboardInterrupt: pass
        finally:
            httpd.server_close()
            remove_pidfile(pidfile,os.getpid())
    elif args.action=='stop':
        stop(pidfile)
    else:
        start(args.port,pidfile)


if __name__=='__main__': main()
