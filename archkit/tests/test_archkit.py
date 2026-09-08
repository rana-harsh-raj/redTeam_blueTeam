"""archkit tests: compiler determinism, static/volatile separation, M7 import, recipes, query library, HTTP service.
Runs offline in a temp store; the real repository inputs are used (they are committed). No docker."""
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

import archkit
from archkit import paths, canon
from archkit.compile import compile_snapshot, write_snapshot
from archkit.store import SnapshotStore
from archkit.import_m7 import run_import
from archkit.recipes import build_recipes, write_recipes
from archkit.query import Query, QueryError, CAPABILITIES
from archkit.service import serve

TMP = Path(tempfile.mkdtemp(prefix="archkit-test-"))
STORE = SnapshotStore(TMP / "store")
SID = None
BODY = None


def setUpModule():
    global SID, BODY
    SID, BODY = compile_snapshot()
    write_snapshot(SID, BODY, STORE.root)
    STORE.register(SID, kind="architecture_snapshot")
    q = Query(STORE, SID)
    write_recipes(STORE, SID, build_recipes(BODY, q.ix))
    run_import(STORE, SID)
    reg = STORE.registry(); reg["current"] = SID; STORE.write_registry(reg)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class TestCanon(unittest.TestCase):
    def test_content_id_is_order_independent_and_stable(self):
        a = {"b": 1, "a": [1, 2, {"z": 0, "y": None}]}
        b = {"a": [1, 2, {"y": None, "z": 0}], "b": 1}
        self.assertEqual(canon.content_id(a), canon.content_id(b))
        import hashlib
        self.assertEqual(canon.content_id({}), hashlib.sha256(b"{}\n").hexdigest())

    def test_volatile_scan_catches_runtime_fields(self):
        hits = canon.scan_volatile({"x": {"generated_at": "2026-09-08T09:33:30Z"}, "s": "container is Up 2 hours (healthy)", "p": "/Users/someone/repo/x"})
        kinds = {h[1] for h in hits}
        self.assertTrue(any(k.startswith("forbidden-key") for k in kinds))
        self.assertIn("docker-status", kinds)
        self.assertIn("home-path", kinds)
        self.assertEqual(canon.scan_volatile({"label": "example 2024-01-15T10:30:00Z"}), [])

    def test_normalize_ref_strips_host_prefixes(self):
        self.assertEqual(canon.normalize_ref("/private/tmp/claude-502/x/y/rzp-payouts-clones/payouts/a.go:1"), "payouts/a.go:1")
        self.assertEqual(canon.normalize_ref("/Users/who/rzp-payouts-architecture/ENV2_COMPOSE/x"), "ENV2_COMPOSE/x")


class TestCompiler(unittest.TestCase):
    def test_repeated_compile_is_byte_identical(self):
        sid2, body2 = compile_snapshot()
        self.assertEqual(sid2, SID)
        self.assertEqual(canon.canonical_bytes(body2), canon.canonical_bytes(BODY))
        d = TMP / "again"
        write_snapshot(sid2, body2, d)
        self.assertEqual((d / sid2 / "snapshot.json").read_bytes(), (STORE.root / SID / "snapshot.json").read_bytes())

    def test_snapshot_id_recomputes_from_body(self):
        doc = json.loads((STORE.root / SID / "snapshot.json").read_bytes())
        self.assertEqual(canon.content_id(doc["body"]), SID)
        self.assertEqual(doc["snapshot_id"], SID)
        v = STORE.verify(SID)
        self.assertTrue(v["id_recomputes"] and v["manifest_ok"])

    def test_body_has_no_volatile_content(self):
        self.assertEqual(canon.scan_volatile(BODY), [])
        txt = canon.canonical_bytes(BODY).decode()
        for bad in ("generated_at", "captured_at", "boot_id", "/Users/", "/private/tmp", "is Up "):
            self.assertNotIn(bad, txt)

    def test_static_semantics_and_no_journey_results(self):
        js = [n for n in BODY["graph"]["nodes"] if n["kind"] == "journey"]
        self.assertEqual(len(js), 202)
        for n in js:
            self.assertNotIn("result", n); self.assertNotIn("merchant_id", n); self.assertNotIn("evidence", n)
        subs = {n["id"]: n for n in BODY["graph"]["nodes"] if n["kind"] == "substitute"}
        self.assertEqual(subs["sub:monolith-stub"]["fidelity"], "behavioural_placeholder")  # lane class, not the runtime overlay upgrade
        self.assertNotIn("runtime", subs["sub:monolith-stub"])
        self.assertEqual(subs["sub:monolith-stub"]["compose_service"], "monolith-stub")
        api = [n for n in BODY["graph"]["nodes"] if n["id"] == "svc:payouts-api"][0]
        self.assertEqual(api["fidelity_basis"], "compose_definition")
        self.assertEqual(api["runtime_definition"]["service"], "payouts-api")

    def test_coverage_measures_are_explicit(self):
        for k, v in BODY["coverage"].items():
            if isinstance(v, dict):
                for f in ("numerator", "denominator", "population", "pct", "definition"):
                    self.assertIn(f, v, k)
                self.assertIn(v["population"], BODY["populations"])
                self.assertEqual(v["denominator"], BODY["populations"][v["population"]]["count"])
        self.assertEqual(BODY["populations"]["p0_critical_kinds"]["count"], 364)
        self.assertEqual(BODY["populations"]["p0_non_journey"]["count"], 525)

    def test_unknown_registry_consolidated_with_origins(self):
        reg = BODY["production_unknowns"]
        ids = [e["id"] for e in reg["entries"]]
        self.assertEqual(ids[:10], ["PU-%d" % i for i in range(1, 11)])
        self.assertEqual(len([i for i in ids if i.startswith("S2P:")]), 4)
        for e in reg["entries"]:
            self.assertIn("file", e["origin"]); self.assertIn("original_id", e["origin"])
            for a in e["affects"]:
                self.assertIn(a, BODY["labels"])
        self.assertEqual([e for e in reg["entries"] if e["id"] == "PU-9"][0]["affects"], ["route:api-monolith/POST payouts/{id}/approve", "route:api-monolith/POST payouts/{id}/reject"])

    def test_source_lock_has_ids_and_explicit_unknown_shas(self):
        lock = BODY["source_lock"]
        self.assertEqual(canon.content_id({"kind": "source_lock", "schema_version": "m8.1", "repositories": lock["repositories"]}), BODY["source_lock_id"])
        self.assertEqual(lock["repositories"]["payouts"]["sha"], "4bf3dbf9239feadea6d65ca90c893a988e116173")
        self.assertEqual(lock["repositories"]["vendor-payments"]["sha"], "20c4f4d59970471067388afea8b1d65ac39ee126")
        for r in lock["repositories"].values():
            if not r["sha"]:
                self.assertTrue(r["sha_status"].startswith("UNKNOWN"))


class TestImportM7(unittest.TestCase):
    def test_projection_matches_m7_and_classifies_every_delta(self):
        proj = STORE.imports(SID)["m7"]["projection"]
        self.assertTrue(proj["node_ids_equal"])
        self.assertEqual(proj["edges_only_in_m7"], [])
        self.assertEqual(len(proj["edges_only_in_static"]), 1)
        self.assertTrue(all(d["reason"] != "unclassified delta" for d in proj["fidelity_deltas"]))
        self.assertEqual(proj["m7_files"]["reports/architecture/M7_CANONICAL_SNAPSHOT.json"], "d88a26342e10eea4c18971f2765ecc2e05364d7716cc113733f7d76233709651")

    def test_m7_files_untouched(self):
        self.assertEqual(canon.sha256_file(paths.M7_ACCEPTANCE), "13d7ef0b83512429dc8a1b59c96305b99fd2155b65cfc7d3f8a8ad5e40b203d6")

    def test_runtime_evidence_acceptance_records_are_separate_and_id_bound(self):
        m7 = STORE.imports(SID)["m7"]
        inst, bundle, rec = m7["runtime_instance"], m7["evidence_bundle"], m7["acceptance_record"]
        self.assertEqual(inst["boot_id"], "6cdf3d73-c39c-4ff7-aafb-856774ed2fd0")
        self.assertEqual(bundle["runtime_instance_id"], inst["runtime_instance_id"])
        self.assertEqual(rec["evidence_bundle_id"], bundle["evidence_bundle_id"])
        self.assertEqual(bundle["result_histogram"], {"EXPECTED_FAILURE": 1, "PASS": 110})
        self.assertTrue(rec["accepted"] and rec["gates_passed"] == 22)
        for doc, key in ((inst, "runtime_instance_id"), (bundle, "evidence_bundle_id"), (rec, "acceptance_record_id")):
            body = {k: v for k, v in doc.items() if k != key}
            self.assertEqual(canon.content_id(body), doc[key])


class TestRecipes(unittest.TestCase):
    def test_98_recipes_with_explicit_unknowns(self):
        idx = STORE.recipes(SID)
        self.assertEqual(idx["count"], 98)
        for r in idx["recipes"]:
            self.assertIn("runtime.image_digest", r["unknowns"])
        r = STORE.recipe("payouts-api", SID)
        self.assertEqual(r["source"]["sha"], "4bf3dbf9239feadea6d65ca90c893a988e116173")
        self.assertEqual(r["build"]["kind"], "build-host")
        self.assertEqual(r["networks"][0]["name"], "rzp-arena${ARENA_SUFFIX:-}")
        self.assertIn("journey:shared-payouts/success", r["journeys"])
        self.assertEqual(r["runtime"]["image_digest"]["status"], "UNKNOWN")
        ing = STORE.recipe("api-ingress", SID)
        self.assertEqual(ing["graph_node"], "sub:api-ingress")
        self.assertIn("POST /_ingress/reset", ing["reset"])
        s2p = STORE.recipe("s2p-vp-source", SID)
        self.assertEqual(s2p["source"]["sha"], "20c4f4d59970471067388afea8b1d65ac39ee126")
        self.assertEqual(canon.content_id({k: v for k, v in r.items() if k != "recipe_id"}), r["recipe_id"])

    def test_recipes_contain_no_secret_values_or_host_paths(self):
        txt = json.dumps([STORE.recipe(r["id"], SID) for r in STORE.recipes(SID)["recipes"]])
        self.assertNotIn("/Users/", txt); self.assertNotIn("/private/tmp", txt)
        self.assertNotIn("local-vendor-payments-secret", txt)


class TestQuery(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.q = Query(STORE, SID)

    def _env(self, r):
        for k in ("snapshot_id", "query", "params", "count", "truncated", "items", "fidelity", "source_refs", "production_unknowns", "detail_namespace_loaded"):
            self.assertIn(k, r)
        self.assertEqual(r["snapshot_id"], SID)

    def test_every_capability_answers_with_an_envelope(self):
        calls = {"snapshot": {}, "node": {"id": "svc:payouts-api"}, "edges": {"id": "svc:payouts-api", "limit": 3}, "search": {"q": "ingress"},
                 "service_card": {"id": "sub:api-ingress"}, "neighbors": {"id": "svc:fts-web", "depth": 2, "limit": 10},
                 "dependencies": {"id": "svc:payouts-api", "transitive": "true"}, "shortest_path": {"a": "identity:merchant-api-key", "b": "table:payouts/payouts"},
                 "paths": {"a": "sub:api-ingress", "b": "svc:fts-web", "max_hops": 5, "limit": 3}, "identity_reachability": {"identity": "identity:ingress-app-secret"},
                 "data_flow": {"id": "table:payouts/payouts"}, "families": {}, "family": {"id": "family:cross-domain-s2p"}, "journeys": {"family": "family:shared-ingress"},
                 "journey": {"id": "journey:cross-domain-s2p/success"}, "fidelity_gaps": {}, "unknowns": {"node": "sub:batch-sim"}, "uncovered_trust_boundaries": {},
                 "evidence": {}, "imports": {}, "recipe": {"service": "payouts-api"}, "recipes": {}, "compare": {"other": SID}, "affected": {"nodes": "sub:api-ingress"},
                 "context_packet": {"subject": "svc:payouts-api", "budget": 3000}, "verify": {}}
        for cap in CAPABILITIES:
            if cap in ("snapshots", "s2p_detail"):
                continue
            r = getattr(self.q, cap)(**calls[cap])
            self._env(r)
            self.assertFalse(r["detail_namespace_loaded"], cap)
        self.assertEqual(STORE.s2p_detail_loads, 0)

    def test_paths_and_reachability_are_bounded_and_deterministic(self):
        a = self.q.paths("sub:api-ingress", "svc:fts-web", max_hops=6, limit=5)
        b = self.q.paths("sub:api-ingress", "svc:fts-web", max_hops=6, limit=5)
        self.assertEqual(a["items"], b["items"])
        self.assertLessEqual(a["count"], 5)
        sp = self.q.shortest_path("identity:merchant-api-key", "table:payouts/payouts")
        self.assertTrue(sp["found"]); self.assertEqual(sp["items"][0]["hops"], 6)
        n = self.q.neighbors("svc:payouts-api", depth=3, limit=20)
        self.assertEqual(n["count"], 20); self.assertTrue(n["truncated"])

    def test_context_packet_respects_budget(self):
        r = self.q.context_packet("svc:payouts-api", budget=2500)
        p = r["items"][0]
        self.assertLessEqual(p["chars"], 2500)
        self.assertTrue(p["truncated"])
        self.assertIn("subject", p["sections"])

    def test_evidence_is_separate_from_static_graph(self):
        j = self.q.journey("journey:cross-domain-s2p/success")["items"][0]
        self.assertEqual(j["fidelity_declared"], "real")
        self.assertEqual(j["evidence"]["result"], "PASS")
        self.assertEqual(j["evidence"]["evidence_bundle_id"], STORE.imports(SID)["m7"]["evidence_bundle"]["evidence_bundle_id"])

    def test_unknown_node_is_a_query_error(self):
        with self.assertRaises(QueryError):
            self.q.node("svc:does-not-exist")

    def test_affected_uses_graph_impact(self):
        r = self.q.affected(nodes="sub:api-ingress")["items"][0]
        self.assertIn("journey:shared-ingress/success", r["rerun_journeys"])
        self.assertIn("family:cross-domain-s2p", r["affected_families"])

    def test_uncovered_trust_boundaries_reports_each_boundary(self):
        r = self.q.uncovered_trust_boundaries()
        self.assertEqual(r["count"], 4)
        # the payouts-service boundary node has no gated routes / identities in the discovery lanes: a real gap, reported not hidden
        self.assertEqual(r["uncovered"], ["identity:trust-boundary:payouts-service"])
        self.assertTrue(all(i["covered"] for i in r["items"] if i["trust_boundary"] != "identity:trust-boundary:payouts-service"))

    def test_s2p_detail_is_explicit_and_lazy(self):
        st = SnapshotStore(STORE.root)
        q = Query(st, SID)
        q.node("svc:s2p/vendor-payments")
        self.assertEqual(st.s2p_detail_loads, 0)
        r = q.s2p_detail(id="state:s2p/vp.pay", expand="true", limit=3)
        self.assertEqual(st.s2p_detail_loads, 1)
        self.assertTrue(r["detail_namespace_loaded"])
        self.assertEqual(r["items"][0]["detail_id"], "vp.pay")
        self.assertEqual(r["detail_nodes_total"], 27994)
        q.s2p_detail(prefix="vp.", limit=2)
        self.assertEqual(st.s2p_detail_loads, 1)


class TestService(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = serve(STORE, 0)
        cls.port = cls.httpd.server_address[1]
        cls.t = threading.Thread(target=cls.httpd.serve_forever, daemon=True); cls.t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _get(self, path, host=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path))
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_routes(self):
        st, root = self._get("/")
        self.assertEqual(st, 200); self.assertEqual(root["capabilities"], CAPABILITIES)
        st, r = self._get("/v1/latest/node?id=svc:payouts-api")
        self.assertEqual(st, 200); self.assertEqual(r["snapshot_id"], SID)
        st, r = self._get("/v1/%s/shortest_path?a=identity:merchant-api-key&b=table:payouts/payouts" % SID[:12])
        self.assertEqual(st, 200); self.assertTrue(r["found"])
        st, r = self._get("/v1/latest/node?id=nope")
        self.assertEqual(st, 400)
        st, r = self._get("/v1/latest/nonsense")
        self.assertEqual(st, 404)
        st, r = self._get("/", host="evil.example")
        self.assertEqual(st, 403)

    def test_read_only(self):
        req = urllib.request.Request("http://127.0.0.1:%d/v1/latest/node" % self.port, data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("POST accepted")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 405)


if __name__ == "__main__":
    unittest.main()
