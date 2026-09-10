"""Read-only loopback HTTP service over the query library.

    python3 -m archkit serve [--port 18790] [--store DIR]
    GET /                                  -> capabilities
    GET /snapshots                         -> snapshot list
    GET /v1/<snapshot_id|latest>/<capability>?param=value...
Binds 127.0.0.1 only; refuses non-loopback Host headers; GET only; every response is the query envelope.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from . import __version__
from .query import Query, QueryError, CAPABILITIES
from .store import SnapshotStore


class _State:
    def __init__(self, store):
        self.store = store
        self.queries = {}
        self.lock = threading.Lock()

    def query(self, sid):
        sid = self.store.resolve(sid)
        with self.lock:
            q = self.queries.get(sid)
            if q is None:
                q = Query(self.store, sid)
                self.queries[sid] = q
        return q


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        server_version = "archkit/" + __version__

        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, sort_keys=True).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost", "::1", ""):
                return self._send(403, {"error": "loopback only"})
            u = urlsplit(self.path)
            parts = [p for p in u.path.split("/") if p]
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if not parts:
                    return self._send(200, {"service": "archkit", "version": __version__, "capabilities": CAPABILITIES, "snapshots": state.store.ids(),
                                            "usage": "/v1/<snapshot_id|latest>/<capability>?param=value"})
                if parts == ["snapshots"]:
                    return self._send(200, Query(state.store).snapshots() if state.store.ids() else {"items": []})
                if len(parts) == 3 and parts[0] == "v1":
                    sid, cap = parts[1], parts[2]
                    if cap not in CAPABILITIES:
                        return self._send(404, {"error": "unknown capability", "capabilities": CAPABILITIES})
                    q = state.query(sid)
                    return self._send(200, getattr(q, cap)(**params))
                return self._send(404, {"error": "not found"})
            except (QueryError, KeyError) as e:
                return self._send(400, {"error": str(e)})
            except TypeError as e:
                return self._send(400, {"error": "bad parameters: %s" % e})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": type(e).__name__ + ": " + str(e)[:300]})

        def do_POST(self):
            self._send(405, {"error": "read-only service"})
        do_PUT = do_DELETE = do_PATCH = do_POST
    return Handler


def serve(store=None, port=18790, host="127.0.0.1"):
    state = _State(store if isinstance(store, SnapshotStore) else SnapshotStore(store))
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    return httpd


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=18790)
    ap.add_argument("--store", default=None)
    a = ap.parse_args(argv)
    httpd = serve(a.store, a.port)
    print("archkit query service on http://127.0.0.1:%d/  (read-only, loopback)" % a.port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0
