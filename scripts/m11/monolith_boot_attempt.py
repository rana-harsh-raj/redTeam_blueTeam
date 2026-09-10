#!/usr/bin/env python3
"""M11: attempt (and record) booting the REAL API monolith (razorpay/api, Laravel/PHP) with a minimal profile.

Every prerequisite is probed mechanically and the outcome is written as evidence -- the point is a precise, reproducible
blocker list, not a narrative. Probes:
  1. interpreter/tooling on this machine (php, composer)
  2. every private composer `repositories` VCS entry + every razorpay/* package in `require`: `git ls-remote` with the
     current credentials (GIT_TERMINAL_PROMPT=0) -> reachable / not found / auth
  3. the base images of the repository's own Dockerfiles (`FROM ...`): `docker manifest inspect` -> pullable / needs creds
  4. the runtime services the monolith's config expects (config/*.php env keys) -- counted, not provisioned
Output: reports/implementation/m11/monolith-boot-attempt.json + .md
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
API = Path(os.environ.get("API_CLONE", "/Users/rana.singh/rzp-payouts-clones/api"))
OUT = REPO / "reports" / "implementation" / "m11"


def sh(cmd, timeout=40, env=None):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
        return {"rc": r.returncode, "out": (r.stdout or "")[-400:], "err": (r.stderr or "")[-400:], "secs": round(time.time() - t0, 2)}
    except subprocess.TimeoutExpired:
        return {"rc": None, "out": "", "err": "timeout after %ss" % timeout, "secs": round(time.time() - t0, 2)}
    except FileNotFoundError as e:
        return {"rc": None, "out": "", "err": str(e), "secs": 0}


def classify_git(rec):
    e = (rec.get("err") or "").lower()
    if rec["rc"] == 0:
        return "reachable"
    if "not found" in e or "repository not found" in e or "does not exist" in e:
        return "not_found_or_no_access"
    if "permission denied" in e or "authentication" in e or "could not read" in e or "403" in e:
        return "auth_denied"
    if "timeout" in e:
        return "timeout"
    return "error"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    doc = {"kind": "m11_monolith_boot_attempt", "generated_at": datetime.now(timezone.utc).isoformat(), "clone": str(API),
           "clone_head": sh(["git", "-C", str(API), "rev-parse", "HEAD"])["out"].strip(), "tooling": {}, "composer": {}, "images": {}, "verdict": {}}
    if not API.is_dir():
        doc["verdict"] = {"bootable": False, "reason": "api clone missing at %s" % API}
        (OUT / "monolith-boot-attempt.json").write_text(json.dumps(doc, indent=1)); return 1
    # 1. tooling
    for tool in ("php", "composer", "docker"):
        p = shutil.which(tool)
        doc["tooling"][tool] = {"path": p, "version": (sh([tool, "--version"])["out"].strip().splitlines()[:1] if p else None)}
    # 2. composer private packages
    c = json.loads((API / "composer.json").read_text())
    require = c.get("require", {})
    repos = c.get("repositories", [])
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=10")
    vcs = []
    for r in repos:
        if r.get("type") != "vcs":
            continue
        url = r["url"]
        https = re.sub(r"^git@github\.com:", "https://github.com/", url)
        rec_ssh = sh(["git", "ls-remote", "--exit-code", url, "HEAD"], timeout=30, env=env)
        rec_https = sh(["git", "ls-remote", "--exit-code", https, "HEAD"], timeout=30, env=env)
        vcs.append({"url": url, "ssh": classify_git(rec_ssh), "https": classify_git(rec_https), "ssh_err": rec_ssh["err"][-160:], "https_err": rec_https["err"][-160:]})
    private_pkgs = sorted(k for k in require if k.startswith("razorpay/"))
    doc["composer"] = {"require_total": len(require), "razorpay_packages": private_pkgs, "vcs_repositories": vcs,
                       "php_constraint": require.get("php"), "laravel": require.get("laravel/framework"),
                       "unreachable_vcs": [v["url"] for v in vcs if v["ssh"] != "reachable" and v["https"] != "reachable"]}
    # 3. Dockerfile base images
    images = {}
    for df in sorted(API.glob("Dockerfile*")):
        froms = [l.split()[1] for l in df.read_text().splitlines() if l.strip().upper().startswith("FROM ") and len(l.split()) > 1]
        for im in froms:
            if im in images or im.lower().startswith("$"):
                continue
            rec = sh(["docker", "manifest", "inspect", im], timeout=60)
            e = (rec["err"] or "").lower()
            images[im] = {"dockerfile": df.name, "pullable": rec["rc"] == 0,
                          "reason": ("ok" if rec["rc"] == 0 else ("unauthorized_or_private_registry" if ("unauthorized" in e or "denied" in e or "401" in e or "no basic auth" in e or "harbor" in im) else e[-160:]))}
    doc["images"] = images
    # 4. runtime expectations (count env keys the config references)
    cfg = API / "config"
    envkeys = set()
    for p in cfg.glob("*.php"):
        envkeys |= set(re.findall(r"env\(\s*['\"]([A-Z0-9_]+)['\"]", p.read_text(errors="replace")))
    doc["runtime_config_env_keys"] = len(envkeys)
    blockers = []
    if not doc["tooling"]["php"]["path"] or not doc["tooling"]["composer"]["path"]:
        blockers.append("no PHP interpreter / composer on this machine (and no credential-free base image carries them: see images)")
    if doc["composer"]["unreachable_vcs"]:
        blockers.append("%d of %d private composer VCS repositories are not readable with the current identity (composer install cannot resolve %s ...)"
                        % (len(doc["composer"]["unreachable_vcs"]), len(vcs), ", ".join(Path(u).stem for u in doc["composer"]["unreachable_vcs"][:6])))
    unpullable = [k for k, v in images.items() if not v["pullable"]]
    if unpullable:
        blockers.append("%d of %d Dockerfile base images are not pullable without registry credentials (%s)" % (len(unpullable), len(images), ", ".join(unpullable[:3])))
    doc["verdict"] = {"bootable_here": not blockers, "blockers": blockers,
                      "fidelity_consequence": "the API monolith stays a CONTRACT_FAITHFUL_REPLACEMENT (api-ingress) -- its ingress responsibilities are reproduced from Route.php/BasicAuth.php/PassportUtil.php, "
                                              "never its code; every value it would read from production config is a recorded unknown (PU-M7/M11 list in the final report)"}
    (OUT / "monolith-boot-attempt.json").write_text(json.dumps(doc, indent=1))
    L = ["# M11 -- API monolith boot attempt (razorpay/api @ %s)" % doc["clone_head"][:12], "", "Generated %s by scripts/m11/monolith_boot_attempt.py" % doc["generated_at"], "",
         "| prerequisite | result |", "|---|---|",
         "| php on host | %s |" % (doc["tooling"]["php"]["path"] or "absent"), "| composer on host | %s |" % (doc["tooling"]["composer"]["path"] or "absent"),
         "| composer require (total / razorpay private) | %d / %d |" % (len(require), len(private_pkgs)),
         "| private VCS repositories readable | %d / %d |" % (len(vcs) - len(doc["composer"]["unreachable_vcs"]), len(vcs)),
         "| Dockerfile base images pullable | %d / %d |" % (len(images) - len(unpullable), len(images)),
         "| config/*.php env keys the runtime expects | %d |" % len(envkeys), "",
         "## Verdict", "", "bootable here: **%s**" % doc["verdict"]["bootable_here"], ""]
    L += ["- " + b for b in blockers]
    L += ["", "## Private VCS repositories", "", "| repository | ssh | https |", "|---|---|---|"] + ["| %s | %s | %s |" % (v["url"], v["ssh"], v["https"]) for v in vcs]
    L += ["", "## Base images", "", "| image | Dockerfile | pullable | reason |", "|---|---|---|---|"] + ["| %s | %s | %s | %s |" % (k, v["dockerfile"], v["pullable"], v["reason"]) for k, v in images.items()]
    (OUT / "monolith-boot-attempt.md").write_text("\n".join(L) + "\n")
    print(json.dumps(doc["verdict"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
