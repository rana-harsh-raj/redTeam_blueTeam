"""Runtime image resolution with a content-addressed factory image cache and recorded immutable digests.

Resolution order per image reference needed by the profile (from the rendered compose definition):
  1. already present in the instance daemon with the cached image id       -> "present"
  2. factory cache tar (FACTORY_HOME/images/<sha256 of image id>.tar)      -> docker load   -> "cache"
  3. public image (no rzp-arena/ or s2p-architecture-replica/ prefix)      -> docker pull (by digest when the cache
     index already knows one) then export into the cache                   -> "pull"
  4. rzp-arena substitute (compose build block)                            -> docker compose build in the instance daemon,
     then export into the cache so every later instance loads the identical image -> "build"
  5. rzp-arena core / S2P runtime image with no cache entry                -> explicit failure (source build needs the
     accepted repository copies + toolchain; the recipe's build command is reported, never guessed)

The recorded digest of an image is its Docker image id (sha256 of the image config = content address of the image);
RepoDigests are recorded too when the registry manifest digest is known (pulled images)."""
import json
import os
import re
from pathlib import Path

from . import paths
from .util import sh, CommandError, sha256_file, write_json, read_json, now, hash_tree, digest_of

PRIVATE_PREFIXES = ("rzp-arena/", "s2p-architecture-replica/")
ALWAYS = ["curlimages/curl:latest", "python:3.12-alpine"]   # journey transport + materializer/cron-driver


class ImageCache:
    def __init__(self, root=None):
        self.root = Path(root) if root else paths.images_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"

    def index(self):
        return read_json(self.index_path, {"kind": "twin_image_cache", "schema_version": "m9.1", "images": {}})

    def _save(self, idx):
        write_json(self.index_path, idx)

    def get(self, ref):
        return self.index()["images"].get(ref)

    def export(self, ref, env, source_label, meta=None):
        """docker save <ref> into the cache keyed by its image id; idempotent. `meta` (e.g. the build-context
        fingerprint of a compose-built image) is recorded on the index entry."""
        insp = inspect(ref, env)
        if not insp:
            raise RuntimeError("image %s not present on daemon %s" % (ref, env.get("DOCKER_HOST", "default")))
        iid = insp["Id"]
        tar = self.root / (iid.replace("sha256:", "") + ".tar")
        if not tar.is_file():
            tmp = tar.with_suffix(".tar.part")
            sh(["docker", "save", "-o", str(tmp), ref], env=env, timeout=1800)
            tmp.replace(tar)
        idx = self.index()
        idx["images"][ref] = {"image_id": iid, "repo_digests": insp.get("RepoDigests") or [], "tar": tar.name, "tar_sha256": sha256_file(tar),
                              "size_bytes": insp.get("Size"), "created": insp.get("Created"), "architecture": insp.get("Architecture"),
                              "exported_from": source_label, "exported_at": now()}
        idx["images"][ref].update(meta or {})
        self._save(idx)
        return idx["images"][ref]

    def load(self, ref, env):
        entry = self.get(ref)
        if not entry:
            return None
        tar = self.root / entry["tar"]
        if not tar.is_file():
            return None
        sh(["docker", "load", "-i", str(tar)], env=env, timeout=1800)
        insp = inspect(ref, env)
        if not insp or insp["Id"] != entry["image_id"]:
            raise RuntimeError("cache load of %s produced id %s, expected %s" % (ref, (insp or {}).get("Id"), entry["image_id"]))
        return entry


def build_fingerprint(env2, build):
    """Content fingerprint of what a compose `build:` block bakes into its image: the Dockerfile plus every COPY/ADD
    source it names (files or trees, relative to the build context). A cache entry whose fingerprint differs from the
    current sources is stale and is rebuilt -- an image tag alone (rzp-arena/<svc>:<ARENA_TAG>) says nothing about the
    substitute source it was built from (M11 finding: the M9 cache served a pre-M11 api-ingress under the same tag).
    Falls back to the whole context tree when no COPY/ADD source can be parsed."""
    if not isinstance(build, dict):
        return None
    ctx = (Path(env2) / (build.get("context") or ".")).resolve()
    dockerfile = ctx / (build.get("dockerfile") or "Dockerfile")
    if not dockerfile.is_file():
        return None
    parts = {"Dockerfile": sha256_file(dockerfile)}
    srcs = []
    for line in dockerfile.read_text().splitlines():
        m = re.match(r"^\s*(COPY|ADD)\s+(.*)$", line.strip())
        if not m:
            continue
        toks = [x for x in m.group(2).split() if not x.startswith("--")]
        if any(x.startswith("--from=") for x in m.group(2).split()):
            continue
        srcs += toks[:-1]
    for s in srcs:
        sp = ctx / s.rstrip("/")
        if sp.is_file():
            parts[s] = sha256_file(sp)
        elif sp.is_dir():
            parts[s] = digest_of(hash_tree(sp))
        else:
            parts[s] = "missing"
    if not srcs:
        parts["context"] = digest_of(hash_tree(ctx))
    return digest_of(parts)


def inspect(ref, env):
    r = sh(["docker", "image", "inspect", ref, "--format", "{{json .}}"], env=env, check=False, timeout=60)
    if r["rc"] != 0:
        return None
    try:
        return json.loads(r["stdout"])
    except ValueError:
        return None


def compose_images(env2, env, compose_profiles, extra_files=()):
    """image ref per compose service from the rendered definition (docker compose config), no daemon needed."""
    cmd = ["docker", "compose", "--env-file", ".env.arena", "-f", "docker-compose.yml"]
    for f in extra_files:
        cmd += ["-f", f]
    for p in compose_profiles:
        cmd += ["--profile", p]
    cmd += ["config", "--format", "json"]
    r = sh(cmd, cwd=env2, env=env, timeout=120)
    doc = json.loads(r["stdout"])
    return {name: spec.get("image") for name, spec in doc["services"].items()}, doc


def resolve(inst, profile, env2, env, cache, log=None, builder=None):
    """Ensure every image the profile needs is present in the instance daemon; return {service: digest record}."""
    from .profiles import compose_profiles_for  # noqa
    files = ["docker-compose.s2p.yml", "docker-compose.twin.yml"] if profile["s2p"] else []
    if profile.get("trust_path") == "real" and "trustpath" in (inst.get("compose_profiles") or []):
        files = files + ["docker-compose.m11.yml"]
    images, doc = compose_images(env2, env, inst["compose_profiles"], files)
    needed = {}
    for svc in list(profile["services"]) + list(profile["jobs"]):
        if svc in images and images[svc]:
            needed[svc] = images[svc]
    for ref in ALWAYS:
        needed["_" + ref.split(":")[0].replace("/", "-")] = ref
    out = {}
    for svc in sorted(needed):
        ref = needed[svc]
        rec = {"image": ref}
        cached = cache.get(ref)
        present = inspect(ref, env)
        pinned = "@sha256:" in ref
        build = doc["services"].get(svc, {}).get("build") if svc in doc["services"] else None
        fp = build_fingerprint(env2, build) if build else None
        stale = bool(fp) and bool(cached) and cached.get("build_fingerprint") != fp
        if fp:
            rec["build_fingerprint"] = fp
        if stale:
            # the cached image was built from different substitute sources (or predates fingerprinting): rebuild
            cmd = ["docker", "compose", "--env-file", ".env.arena", "-f", "docker-compose.yml"] + sum([["-f", f] for f in files], []) + \
                  sum([["--profile", p] for p in inst["compose_profiles"]], []) + ["build", svc]
            sh(cmd, cwd=env2, env=env, timeout=1800, log=log)
            entry = cache.export(ref, env, "compose-build:%s" % svc, meta={"build_fingerprint": fp})
            rec.update({"source": "build-stale-cache", "image_id": entry["image_id"], "repo_digests": entry["repo_digests"],
                        "replaced_image_id": cached.get("image_id")})
        elif pinned and present:
            rec.update({"source": "present-pinned", "image_id": present["Id"], "repo_digests": present.get("RepoDigests") or [ref.split("@", 1)[1]]})
        elif pinned:
            sh(["docker", "pull", ref], env=env, timeout=1800, log=log)
            present = inspect(ref, env)
            rec.update({"source": "pull-pinned", "image_id": present["Id"], "repo_digests": present.get("RepoDigests") or [ref.split("@", 1)[1]]})
        elif present and cached and present["Id"] == cached["image_id"]:
            rec.update({"source": "present", "image_id": present["Id"], "repo_digests": present.get("RepoDigests") or []})
        elif cached and cache.load(ref, env):
            rec.update({"source": "cache", "image_id": cached["image_id"], "repo_digests": cached.get("repo_digests") or []})
        elif not ref.startswith(PRIVATE_PREFIXES):
            pull_ref = ref
            if cached and cached.get("repo_digests"):
                pull_ref = cached["repo_digests"][0]
            sh(["docker", "pull", pull_ref], env=env, timeout=1800, log=log)
            if pull_ref != ref:
                sh(["docker", "tag", pull_ref, ref], env=env, timeout=60)
            entry = cache.export(ref, env, "pull:%s" % pull_ref)
            rec.update({"source": "pull", "image_id": entry["image_id"], "repo_digests": entry["repo_digests"]})
        elif present and not cached:
            entry = cache.export(ref, env, "present-on-instance-daemon")
            rec.update({"source": "present-uncached", "image_id": entry["image_id"], "repo_digests": entry["repo_digests"]})
        elif svc in doc["services"] and doc["services"][svc].get("build"):
            cmd = ["docker", "compose", "--env-file", ".env.arena", "-f", "docker-compose.yml"] + sum([["-f", f] for f in files], []) + \
                  sum([["--profile", p] for p in inst["compose_profiles"]], []) + ["build", svc]
            sh(cmd, cwd=env2, env=env, timeout=1800, log=log)
            entry = cache.export(ref, env, "compose-build:%s" % svc, meta={"build_fingerprint": fp})
            rec.update({"source": "build", "image_id": entry["image_id"], "repo_digests": entry["repo_digests"]})
        elif builder:
            rec.update(builder(svc, ref, env, cache, log))
        else:
            raise RuntimeError("image %s for %s is neither cached nor buildable here; seed the cache with "
                               "`python3 -m twinfactory images export --from <docker host>` or build per the recipe" % (ref, svc))
        out[svc] = rec
    return out


def digests_summary(image_records):
    by_image = {}
    for svc, rec in image_records.items():
        by_image.setdefault(rec["image"], {"image_id": rec.get("image_id"), "repo_digests": rec.get("repo_digests") or [], "services": []})["services"].append(svc)
    for v in by_image.values():
        v["services"].sort()
    return dict(sorted(by_image.items()))
