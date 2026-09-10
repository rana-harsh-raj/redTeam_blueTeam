"""python3 -m archkit <command>

  compile [--store DIR]                 compile the ArchitectureSnapshot from committed inputs (no docker)
  recipes [--snapshot ID]               normalized service recipes for the 98 canonical services
  import-m7 [--snapshot ID]             import the accepted M7 milestone (projection + runtime/evidence/acceptance records)
  build [--store DIR]                   compile + recipes + import-m7 + set current
  ls | verify [--snapshot ID]
  q <capability> [key=value ...]        run a query (see `q` with no capability for the list)
  serve [--port N]                      read-only loopback HTTP service
  bench [--snapshot ID]                 timing/memory measurements -> JSON on stdout
  acceptance                            M8 gates -> reports/implementation/M8_ACCEPTANCE.json
"""
import json
import sys
from pathlib import Path
from . import paths


def _store(args):
    from .store import SnapshotStore
    return SnapshotStore(Path(args.store) if getattr(args, "store", None) else None)


def cmd_compile(args):
    from .compile import compile_snapshot, write_snapshot
    sid, body = compile_snapshot()
    st = _store(args)
    d = write_snapshot(sid, body, st.root)
    st.register(sid, kind="architecture_snapshot", source_lock_id=body["source_lock_id"], compiler=body["compiler"])
    print(json.dumps({"snapshot_id": sid, "dir": paths.rel(d), "counts": body["counts"]}))
    return sid


def cmd_recipes(args):
    from .query import Query
    from .recipes import build_recipes, write_recipes
    st = _store(args)
    q = Query(st, args.snapshot)
    rec = build_recipes(q.ix.body, q.ix)
    idx = write_recipes(st, q.sid, rec)
    print(json.dumps({"snapshot_id": q.sid, "recipes": idx["count"], "recipe_set_id": idx["recipe_set_id"], "by_category": idx["by_category"]}))


def cmd_import(args):
    from .import_m7 import run_import
    st = _store(args)
    print(json.dumps(run_import(st, args.snapshot), indent=1))


def cmd_build(args):
    sid = cmd_compile(args)
    args.snapshot = sid
    cmd_recipes(args)
    cmd_import(args)
    st = _store(args)
    reg = st.registry(); reg["current"] = sid; st.write_registry(reg)
    print(json.dumps({"current": sid}))


def cmd_ls(args):
    from .query import Query
    st = _store(args)
    print(json.dumps(Query(st).snapshots() if st.ids() else {"items": []}, indent=1))


def cmd_verify(args):
    st = _store(args)
    r = st.verify(args.snapshot)
    print(json.dumps(r))
    return 0 if r["id_recomputes"] and r["manifest_ok"] else 1


def cmd_q(args):
    from .query import Query, CAPABILITIES, QueryError
    if not args.capability:
        print("\n".join(CAPABILITIES)); return 0
    if args.capability not in CAPABILITIES:
        print("unknown capability; one of: " + ", ".join(CAPABILITIES), file=sys.stderr); return 2
    params = {}
    for kv in args.params:
        k, _, v = kv.partition("=")
        params[k] = v
    q = Query(_store(args), args.snapshot)
    try:
        out = getattr(q, args.capability)(**params)
    except QueryError as e:
        print(json.dumps({"error": str(e)})); return 1
    print(json.dumps(out, indent=None if args.compact else 1, sort_keys=True))
    return 0


def cmd_serve(args):
    from .service import serve
    httpd = serve(_store(args), args.port)
    print("archkit query service on http://127.0.0.1:%d/  (read-only, loopback)" % args.port, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def cmd_bench(args):
    from .bench import run
    print(json.dumps(run(_store(args), args.snapshot), indent=1))


def cmd_acceptance(args):
    from .acceptance import main as acc
    return acc([])


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="archkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", default=None, help="snapshot store directory (default reports/architecture/snapshots)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("compile", "build", "ls", "acceptance"):
        sub.add_parser(name)
    for name in ("recipes", "import-m7", "verify", "bench"):
        p = sub.add_parser(name); p.add_argument("--snapshot", default=None)
    p = sub.add_parser("q"); p.add_argument("capability", nargs="?"); p.add_argument("params", nargs="*"); p.add_argument("--snapshot", default=None); p.add_argument("--compact", action="store_true")
    p = sub.add_parser("serve"); p.add_argument("--port", type=int, default=18790)
    a = ap.parse_args(argv)
    fn = {"compile": cmd_compile, "recipes": cmd_recipes, "import-m7": cmd_import, "build": cmd_build, "ls": cmd_ls, "verify": cmd_verify,
          "q": cmd_q, "serve": cmd_serve, "bench": cmd_bench, "acceptance": cmd_acceptance}[a.cmd]
    r = fn(a)
    return r if isinstance(r, int) else 0
