"""twinfactory offline tests: profile derivation, planning, registry, workspace rendering, manifest binding, backend
interface, image cache index, boot command construction, journey environment, CLI. No docker, no colima, no network."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="twinfactory-test-"))
os.environ["TWIN_FACTORY_HOME"] = str(TMP / "home")

from twinfactory import paths, plan as PL, profiles as P, registry as R, workspace as W, backend as B, images as I  # noqa: E402
from twinfactory.snapshot import ArchitectureInputs  # noqa: E402
from twinfactory.factory import Factory  # noqa: E402
from twinfactory.boot import Boot  # noqa: E402
from twinfactory.journeys import journey_env  # noqa: E402
from twinfactory.util import content_id  # noqa: E402

INPUTS = None


def setUpModule():
    global INPUTS
    INPUTS = ArchitectureInputs()


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


class TestProfiles(unittest.TestCase):
    def test_full_is_every_recipe(self):
        # M11: `full` = every non-job recipe of the snapshot, then rule R12 keeps ONE implementation per trust-path role
        jobs = {s for s, r in INPUTS.recipes.items() if (r.get("runtime") or {}).get("one_shot") and s != "ledger-scheduler"}
        real_set = {s for sets in P.TRUST_ROLES.values() for s in sets["real"]}
        sub_set = {s for sets in P.TRUST_ROLES.values() for s in sets["substitute"]}
        p = P.derive(INPUTS, "full", "substitute")
        self.assertEqual(sorted(p["services"]), sorted(set(INPUTS.recipes) - jobs - real_set))
        self.assertEqual(len(p["services"]), 98)                    # the M9 canonical runtime shape, unchanged
        self.assertEqual(set(p["jobs"]), set(P.MIGRATION_JOBS))
        self.assertEqual(p["trust_path"], "substitute")
        r = P.derive(INPUTS, "full", "real")
        self.assertEqual(sorted(r["services"]), sorted(set(INPUTS.recipes) - jobs - sub_set))
        self.assertTrue(real_set <= set(r["services"]) and not (sub_set & set(r["services"])))
        self.assertEqual(set(r["jobs"]), set(P.MIGRATION_JOBS) | set(P.TRUST_JOBS))
        self.assertEqual(r["trust_path"], "real")
        self.assertTrue(p["s2p"] and r["s2p"])
        self.assertEqual(p["architecture_snapshot_id"], INPUTS.snapshot_id)
        self.assertEqual(p["recipe_set_id"], INPUTS.recipe_set_id)
        self.assertNotEqual(P.derive(INPUTS, "full", "real")["services"], P.derive(INPUTS, "full", "substitute")["services"])

    def test_trust_variant_is_exclusive_per_role(self):
        for variant in ("real", "substitute"):
            for name in ("full", "critical-payouts", "focused-s2p"):
                p = P.derive(INPUTS, name, variant)
                for role, sets in P.TRUST_ROLES.items():
                    other = "substitute" if variant == "real" else "real"
                    self.assertFalse(set(sets[other]) & set(p["services"]), (variant, name, role))
                    if variant == "real" and name != "focused-s2p":
                        self.assertTrue(set(sets["real"]) <= set(p["services"]), (name, role))
        with self.assertRaises(KeyError):
            P.derive(INPUTS, "full", "hybrid")

    def test_focused_is_a_traced_subset(self):
        p = P.derive(INPUTS, "critical-payouts", "substitute")
        self.assertEqual(p["name"], "focused:shared-payouts")
        self.assertLess(len(p["services"]), len(INPUTS.recipes))
        self.assertGreater(len(p["services"]), 20)
        for s, v in p["services"].items():
            self.assertIn(s, INPUTS.recipes)
            self.assertTrue(v["reasons"], s)
            self.assertRegex(v["reasons"][0], r"^R\d+b?: ")
        self.assertIn("payouts-api", p["services"])
        self.assertIn("kong-lite", p["services"])
        self.assertIn("mysql-payouts", p["services"])
        self.assertFalse(p["s2p"])
        self.assertNotIn("s2p-vp-source", p["services"])
        self.assertIn("payouts-migrate", p["jobs"])
        r = P.derive(INPUTS, "critical-payouts", "real")
        for s in ("edge-kong", "edge-bridge", "postgres-kong", "shield-web", "banking-accounts-api", "workflows-api", "workflows-worker", "cadence"):
            self.assertIn(s, r["services"], s)
            self.assertTrue(any(x.startswith("R12:") or x.startswith("R3:") or x.startswith("R4:") or x.startswith("R2b:") or x.startswith("R11:") for x in r["services"][s]["reasons"]), s)
        for s in ("kong-lite", "shield-stub", "bankingaccounts-stub", "workflow-engine"):
            self.assertNotIn(s, r["services"])
        for j in ("edge-kong-migrate", "edge-kong-config", "shield-migrate", "shield-seed", "bas-migrate", "bas-seed", "workflows-migrate", "workflows-seed"):
            self.assertIn(j, r["jobs"])

    def test_focused_s2p_includes_overlay(self):
        p = P.derive(INPUTS, "focused-s2p")
        for s in ("s2p-vp-source", "s2p-kafka", "s2p-mysql", "s2p-redis", "api-ingress"):
            self.assertIn(s, p["services"])
        self.assertTrue(p["s2p"])

    def test_derivation_is_deterministic(self):
        a = P.derive(INPUTS, "critical-payouts"); b = P.derive(INPUTS, "critical-payouts")
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_unknown_profile(self):
        with self.assertRaises(KeyError):
            P.derive(INPUTS, "focused:no-such-family")

    def test_compose_profiles(self):
        self.assertEqual(P.compose_profiles_for(P.derive(INPUTS, "full", "substitute"), INPUTS), ["core", "datastores", "migrations", "s2p", "substitutes"])
        self.assertEqual(P.compose_profiles_for(P.derive(INPUTS, "full", "real"), INPUTS), ["core", "datastores", "migrations", "s2p", "substitutes", "trustpath", "trustpath-migrations"])


class TestPlan(unittest.TestCase):
    def test_names_are_deterministic_and_instance_scoped(self):
        a = PL.derive_names("Alpha_Full"); b = PL.derive_names("alpha-full")
        self.assertEqual(a, b)
        self.assertEqual(a["compose_project"], "twin_alpha_full")
        self.assertEqual(a["arena_network"], "rzp-arena-alpha-full")
        self.assertNotIn("env2_compose", json.dumps(a))
        self.assertTrue(18100 <= a["kong_host_port"] < 18900)

    def test_collisions_are_avoided_against_registry(self):
        a = PL.plan_instance("alpha", "full")
        b = PL.plan_instance("beta", "full", [dict(instance_id="alpha", kong_host_port=PL.derive_names("beta")["kong_host_port"], arena_subnet=PL.derive_names("beta")["arena_subnet"])])
        self.assertNotEqual(a["kong_host_port"], b["kong_host_port"]) if a["kong_host_port"] == PL.derive_names("beta")["kong_host_port"] else None
        self.assertNotEqual(b["kong_host_port"], PL.derive_names("beta")["kong_host_port"])
        self.assertNotEqual(b["arena_subnet"], PL.derive_names("beta")["arena_subnet"])
        self.assertNotIn(b["arena_subnet"], ("172.28.16.0/24", "172.28.17.0/24"))

    def test_seed_epoch(self):
        self.assertEqual(PL.seed_epoch("x"), PL.seed_epoch("x"))
        self.assertNotEqual(PL.seed_epoch("x"), PL.seed_epoch("y"))
        self.assertTrue(PL.DEFAULT_EPOCH <= PL.seed_epoch("x") < PL.DEFAULT_EPOCH + PL.EPOCH_SPAN)

    def test_sizing(self):
        self.assertEqual(PL.plan_instance("a", "full", service_count=98)["sizing"]["memory_gib"], 10)
        self.assertEqual(PL.plan_instance("a", "focused:x", service_count=30)["sizing"]["memory_gib"], 5)
        self.assertEqual(PL.plan_instance("a", "focused:x", sizing={"cpus": 2}, service_count=93)["sizing"]["cpus"], 2)
        self.assertEqual(PL.sizing_for(93)["memory_gib"], 10)


class TestRegistry(unittest.TestCase):
    def test_upsert_state_remove(self):
        r = R.Registry(TMP / "reg" / "registry.json")
        r.upsert({"instance_id": "x", "profile": "full", "state": "created"})
        r.set_state("x", "running", kong_host_port=1)
        self.assertEqual(r.get("x")["state"], "running")
        self.assertEqual(r.get("x")["kong_host_port"], 1)
        self.assertEqual(len(r.records()), 1)
        r.remove("x")
        self.assertIsNone(r.get("x"))
        self.assertEqual(r.load()["destroyed"][0]["instance_id"], "x")


class TestWorkspaceAndManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.f = Factory(TMP / "home")
        cls.ma = cls.f.create("t-alpha", "full", "seed-a", "local-docker")
        cls.mb = cls.f.create("t-beta", "critical-payouts", "seed-b", "colima")

    def test_workspace_has_no_generated_state(self):
        env2 = Path(self.ma["env2_root"])
        self.assertTrue((env2 / "docker-compose.yml").is_file())
        self.assertTrue((env2 / "docker-compose.twin.yml").is_file())
        self.assertFalse((env2 / "generated").exists())
        self.assertFalse((env2 / ".runtime").exists())
        self.assertFalse(list((env2 / "secrets").glob("*.txt")))
        self.assertFalse((env2 / "secrets" / "merchant-keys").exists())
        self.assertFalse((env2 / "seeds" / "generated").exists())
        self.assertFalse(str(env2).startswith(str(paths.REPO)))

    def test_env_arena_is_instance_scoped_and_credential_free(self):
        t = (Path(self.ma["env2_root"]) / ".env.arena").read_text()
        self.assertIn("COMPOSE_PROJECT_NAME=twin_t_alpha", t)
        self.assertIn("ARENA_SUFFIX=-t-alpha", t)
        self.assertIn("KONG_LITE_HOST_PORT=%d" % self.ma["kong_host_port"], t)
        self.assertIn("ARENA_SEED_EPOCH=%d" % PL.seed_epoch("seed-a"), t)
        self.assertNotIn("env2_compose", t)
        self.assertNotIn("PASSWORD", t)
        self.assertNotIn(".local/twin-runtime-migrations", t)
        self.assertIn(self.ma["exec_dir"], t)

    def test_subnet_defaults_rewritten_in_copy_only(self):
        t = (Path(self.ma["env2_root"]) / "docker-compose.yml").read_text()
        self.assertIn("ARENA_SUBNET:-" + self.ma["arena_subnet"], t)
        self.assertIn("ARENA_SUBNET:-172.28.16.0/24", paths.COMPOSE.read_text())

    def test_inputs_hash_is_instance_independent(self):
        self.assertEqual(self.ma["inputs_hash"], self.mb["inputs_hash"])
        self.assertGreater(self.ma["inputs_file_count"], 100)

    def test_manifest_binding(self):
        for m in (self.ma, self.mb):
            self.assertEqual(m["architecture_snapshot_id"], INPUTS.snapshot_id)
            self.assertEqual(m["recipe_set_id"], INPUTS.recipe_set_id)
            self.assertEqual(len(m["profile_digest"]), 64)
            self.assertIn(m["state"], ("created",))
            self.assertIsNone(m["image_digests"])
        self.assertNotEqual(self.ma["profile_digest"], self.mb["profile_digest"])
        self.assertNotEqual(self.ma["kong_host_port"], self.mb["kong_host_port"])
        self.assertNotEqual(self.ma["arena_subnet"], self.mb["arena_subnet"])
        self.assertTrue(self.mb["backend"]["isolation_boundary"])
        self.assertFalse(self.ma["backend"]["isolation_boundary"])
        prof = json.loads((self.f.idir("t-beta") / "profile.json").read_text())
        self.assertEqual(prof["name"], "focused:shared-payouts")

    def test_registry_and_events(self):
        ids = sorted(r["instance_id"] for r in self.f.list())
        self.assertEqual(ids, ["t-alpha", "t-beta"])
        ev = (self.f.idir("t-alpha") / "events.jsonl").read_text().splitlines()
        self.assertTrue(any('"create"' in l for l in ev))

    def test_duplicate_create_refused(self):
        with self.assertRaises(RuntimeError):
            self.f.create("t-alpha", "full", "seed-a", "local-docker")

    def test_runtime_record_id_is_content_addressed(self):
        rid = self.f._runtime_record(self.f.manifest("t-beta"))
        doc = json.loads((self.f.idir("t-beta") / "runtime_instance.json").read_text())
        self.assertEqual(doc["runtime_instance_id"], rid)
        self.assertEqual(content_id(doc["body"]), rid)
        self.assertEqual(doc["body"]["architecture_snapshot_id"], INPUTS.snapshot_id)
        self.assertIn(self.mb["arena_network"], doc["body"]["networks"])

    def test_reproduce_manifest(self):
        m = self.f.reproduce("t-beta", "t-beta-2")
        self.assertEqual(m["inputs_hash"], self.mb["inputs_hash"])
        self.assertEqual(m["seed_epoch"], self.mb["seed_epoch"])
        self.assertEqual(m["profile_digest"], self.mb["profile_digest"])
        self.assertEqual(m["reproduced_from"]["instance_id"], "t-beta")

    def test_boot_commands_and_env(self):
        m = self.f.manifest("t-beta")
        boot = Boot(m, self.f.profile(m), {"DOCKER_HOST": "unix:///nope.sock"}, TMP / "logs")
        env = boot.env()
        self.assertEqual(env["COMPOSE_PROJECT_NAME"], "twin_t_beta")
        self.assertEqual(env["DOCKER_HOST"], "unix:///nope.sock")
        self.assertNotIn("S2P_API_SECRET", env)
        self.assertNotIn("GIT_AUTHOR_EMAIL", env)
        cmd = boot.compose_cmd(s2p=True)
        self.assertIn("docker-compose.twin.yml", cmd)
        self.assertIn("--profile", cmd)
        self.assertNotIn("docker-compose.twin.yml", boot.compose_cmd(s2p=False))

    def test_journey_env(self):
        m = self.f.manifest("t-beta")
        e = journey_env(self.f, m)
        self.assertEqual(e["ARENA_ENV2_ROOT"], m["env2_root"])
        self.assertEqual(e["ARENA_COMPOSE_PROJECT"], "twin_t_beta")
        self.assertEqual(e["ARENA_NETWORK"], "rzp-arena-t-beta")
        self.assertEqual(e["TWIN_INSTANCE_ID"], "t-beta")
        self.assertTrue(e["KONG_LITE_HOST_URL"].endswith(str(m["kong_host_port"])))
        self.assertTrue(e["TWIN_RUNS_DIR"].startswith(m["exec_dir"]))
        self.assertTrue(e["DOCKER_HOST"].endswith("twin-t-beta/docker.sock"))


class TestBackendsAndImages(unittest.TestCase):
    def test_backend_interface(self):
        for name, cls in B.BACKENDS.items():
            bk = B.get_backend(name, {"docker_host": "ssh://x"})
            self.assertEqual(bk.kind, name)
            for meth in ("provision", "docker_env", "identity", "status", "stop", "start", "destroy", "describe"):
                self.assertTrue(callable(getattr(bk, meth)))
        self.assertTrue(B.ColimaBackend().isolation_boundary)
        self.assertFalse(B.LocalDockerBackend().isolation_boundary)
        inst = {"backend_profile": "twin-zz", "exec_dir": "/x", "sizing": {"cpus": 1, "memory_gib": 1, "disk_gib": 1}}
        self.assertTrue(B.ColimaBackend().docker_env(inst)["DOCKER_HOST"].endswith("/.colima/twin-zz/docker.sock"))
        self.assertEqual(B.RemoteDockerBackend({"docker_host": "ssh://u@h"}).docker_env(inst)["DOCKER_HOST"], "ssh://u@h")
        with self.assertRaises(KeyError):
            B.get_backend("nope")

    def test_image_cache_index(self):
        c = I.ImageCache(TMP / "images")
        self.assertEqual(c.index()["images"], {})
        self.assertIsNone(c.get("x:1"))
        self.assertIsNone(c.load("x:1", {}))
        self.assertEqual(I.digests_summary({"a": {"image": "i:1", "image_id": "sha256:aa"}, "b": {"image": "i:1", "image_id": "sha256:aa"}})["i:1"]["services"], ["a", "b"])


class TestCli(unittest.TestCase):
    def test_profiles_and_profile(self):
        from twinfactory.cli import main
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--home", str(TMP / "home"), "profiles"])
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertEqual(out["profiles"]["full"]["services"], len(P.derive(INPUTS, "full", "real")["services"]))
        self.assertEqual(out["profiles"]["full"]["trust_path"], "real")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--home", str(TMP / "home"), "profile", "critical-payouts", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue())["name"], "focused:shared-payouts")


if __name__ == "__main__":
    unittest.main()
