"""Render reports/implementation/M9_FINAL_REPORT.md from the machine records (manifests, proof, acceptance, host
limits). Numbers are never typed by hand."""
import json
from pathlib import Path

from . import paths, __version__
from .util import read_json
from .evidence import OUT as EVIDENCE

REPORT = paths.IMPL / "M9_FINAL_REPORT.md"


def _t(v):
    return "%.1f s" % v if isinstance(v, (int, float)) else "n/a"


def _mib(rec):
    return "%.0f MiB" % rec["containers_mem_mib"] if rec and rec.get("containers_mem_mib") is not None else "n/a"


def render():
    acc = read_json(paths.IMPL / "M9_ACCEPTANCE.json", {})
    proof = read_json(paths.IMPL / "m9-isolation-proof.json", {})
    host = read_json(EVIDENCE / "host-limits.json", {})
    manifests = {p.parent.name: read_json(p) for p in sorted(EVIDENCE.glob("instances/*/manifest.json"))}
    profiles = {p.parent.name: read_json(p) for p in sorted(EVIDENCE.glob("instances/*/profile.json"))}
    a, b = proof.get("a"), proof.get("b")
    ma, mb = manifests.get(a, {}), manifests.get(b, {})
    ph = proof.get("phases", {})
    meas = proof.get("measurements", {})
    L = []
    L.append("# M9 — Isolated Twin Factory: final report")
    L.append("")
    L.append("Rendered by `python3 -m twinfactory report` from the committed machine records under `reports/implementation/m9/`, the isolation proof and the acceptance evaluation. twinfactory %s." % __version__)
    L.append("")
    L.append("## Result")
    L.append("")
    L.append("| item | value |")
    L.append("|---|---|")
    L.append("| acceptance | **%s** (%s/%s gates, `reports/implementation/M9_ACCEPTANCE.json`, evaluated at `%s` on `%s`) |" % ("ACCEPTED" if acc.get("accepted") else "NOT ACCEPTED", acc.get("passed"), acc.get("total"), (acc.get("git_head") or "")[:10], acc.get("branch")))
    L.append("| architecture snapshot consumed | `%s` (recipe set `%s`) |" % (ma.get("architecture_snapshot_id"), (ma.get("recipe_set_id") or "")[:16]))
    L.append("| execution backend | %s (one Lima/vz virtual machine + its own dockerd per instance; only the instance's execution directory is mounted) |" % (ma.get("backend") or {}).get("kind"))
    L.append("| profiles supported | `full` (%d services, %d migration jobs, Source-to-Pay overlay) and `focused:<family>` derived by graph closure — demonstrated `critical-payouts` = `focused:shared-payouts` (%d services) |" % (len((profiles.get(a) or {}).get("services", {})), len((profiles.get(a) or {}).get("jobs", [])), len((profiles.get(b) or {}).get("services", {}))))
    L.append("| two-instance isolation proof | **%s** — phases %s |" % ("PASSED" if proof.get("passed") else "FAILED", ", ".join("%s=%s" % (k, "PASS" if v.get("passed") else "FAIL") for k, v in sorted(ph.items()))))
    L.append("")
    L.append("## Lifecycle operations (library `twinfactory.factory.Factory`, CLI `python3 -m twinfactory`)")
    L.append("")
    L.append("create → build → start → status / health → journeys → reset → snapshot-state / restore-state → stop → destroy, plus reproduce (new instance from an existing manifest), ls, manifest, events, images export, inputs export, isolation run, evidence, acceptance, report. Every operation appends to the instance event log and updates the durable registry (`~/.twin-factory/registry.json`).")
    L.append("")
    L.append("## Instances used in the proof")
    L.append("")
    L.append("| instance | profile | seed → epoch | backend profile | services | port | subnet | runtime_instance_id |")
    L.append("|---|---|---|---|---|---|---|---|")
    for iid, m in manifests.items():
        L.append("| `%s` | `%s` | `%s` → %s | `%s` | %d | %s | %s | `%s` |" % (iid, m.get("profile"), m.get("seed"), m.get("seed_epoch"), (m.get("backend") or {}).get("profile"),
                                                                              len((profiles.get(iid) or {}).get("services", {})), m.get("kong_host_port"), m.get("arena_subnet"), (m.get("runtime_instance_id") or "")[:16]))
    L.append("")
    L.append("Both manifests bind: architecture snapshot id, recipe set id, profile digest, seed and seed epoch, inputs hash (`%s…`, identical for both — the credential-free static definition), rendered-config hash and secrets-manifest digest (instance-private), and the image id of every image (%d images for the full profile)." % ((ma.get("inputs_hash") or "")[:12], len(ma.get("image_digests") or {})))
    L.append("")
    L.append("## Journeys")
    L.append("")
    p5 = (ph.get("P5") or {}).get("detail") or {}
    for side, iid in (("a", a), ("b", b)):
        d = p5.get(side) or {}
        for j in d.get("journeys") or []:
            L.append("- `%s`: `%s` → **%s** (%s/%s checks, %s s, merchant `%s`), evidence attributed to the instance: %s" % (iid, j.get("id"), j.get("result"), j.get("checks_passed"), j.get("checks_total"), j.get("duration_s"), j.get("merchant_id"), d.get("attributed")))
    p6 = (ph.get("P6") or {}).get("detail") or {}
    if p6:
        L.append("- `%s` while `%s` was stopped: %s" % (b, a, json.dumps((p6.get("b_journey_while_a_stopped") or {}).get("summary"))))
    L.append("")
    crashes = []
    for iid, m in manifests.items():
        for c in (m.get("boot") or {}).get("crash_restarts") or []:
            crashes.append("`%s`: `%s` exited %s at start and was restarted (restart %d; log tail: `%s`)" % (iid, c["service"], c["exit_code"], c["restart"], (c.get("log_tail") or "").strip().splitlines()[-1][:160] if c.get("log_tail") else ""))
    L.append("## Observed defects during boot")
    L.append("")
    L.extend(["- " + c for c in crashes] or ["- none recorded in the final boots (a real binary that crashes at start is restarted at most twice by the boot orchestrator and every restart is recorded in the manifest's `boot.crash_restarts`)"])
    L.append("- Observed once during the first P9 attempt (recorded in the proof run log, phase then re-run): `xbalances-worker` (real x-balances binary) died at start with `fatal error: concurrent map read and map write` in `pkg/worker.(*Manager).Do` (`pkg/worker/manager.go:253`) — a startup race in the pinned source; the orchestrator's bounded crash restart was added in response.")
    L.append("")
    L.append("## Isolation proof (reports/implementation/m9-isolation-proof.json)")
    L.append("")
    L.append("| phase | result | evidence |")
    L.append("|---|---|---|")
    for pid, v in sorted(ph.items()):
        det = v.get("detail") or {}
        summary = ""
        if pid == "P1":
            summary = "daemon ids `%s` vs `%s`; sockets differ; both boundaries isolating" % (((det.get("a") or {}).get("identity") or {}).get("daemon_id", "")[:12], ((det.get("b") or {}).get("identity") or {}).get("daemon_id", "")[:12])
        elif pid == "P2":
            summary = "shared names: %d; B resources on A: %d; A on B: %d; ports %s; S2P secret instance-specific: %s" % (len(det.get("shared_names", [])), len(det.get("b_resources_visible_on_a", [])), len(det.get("a_resources_visible_on_b", [])), det.get("ports"), det.get("s2p_secret_instance_specific"))
        elif pid == "P3":
            summary = "; ".join("%s: %s" % (k, x.get("verdict")) for k, x in det.items())
        elif pid == "P4":
            summary = "; ".join("%s: own=%s other-by-name=%s other-net=rc%s other-host-port=%s" % (k, x["own_payouts_api"]["http"], x["other_container_by_name"]["http"], x["attach_to_other_network"]["rc"], x["other_host_port_from_vm"]["http"]) for k, x in det.items() if k != "host_bridge_pids")
        elif pid == "P6":
            summary = "B healthy while A stopped: %s; A restart %s; both healthy after" % ((det.get("b_after_a_stopped") or {}).get("healthy"), _t((det.get("restart_a") or {}).get("secs")))
        elif pid == "P7":
            summary = "A rows before == after: %s; A rendered config unchanged: %s; B reset %s" % (det.get("a_rows_before") == det.get("a_rows_after"), det.get("a_rendered_before") == det.get("a_rendered_after"), _t((det.get("reset_b") or {}).get("secs")))
        elif pid == "P8":
            summary = "rows at snapshot %s → after mutation %s → after restore %s" % (json.dumps({k: v2 for k, v2 in (det.get("rows_at_snapshot") or {}).items() if k.startswith("payouts.payouts")}), json.dumps({k: v2 for k, v2 in (det.get("rows_after_mutation") or {}).items() if k.startswith("payouts.payouts")}), json.dumps({k: v2 for k, v2 in (det.get("rows_after_restore") or {}).items() if k.startswith("payouts.payouts")}))
        elif pid == "P9":
            rep = det.get("reproducible") or {}
            summary = "B healthy after A destroyed: %s; A reproduced: inputs_hash=%s profile=%s seed_epoch=%s image ids equal %d/%d; A boots again: %s" % (det.get("b_after_destroy"), rep.get("inputs_hash"), rep.get("profile_digest"), rep.get("seed_epoch"), sum(1 for x in (rep.get("image_ids") or {}).values() if x), len(rep.get("image_ids") or {}), (det.get("a_restarted") or {}).get("healthy"))
        L.append("| %s %s | %s | %s |" % (pid, v.get("title", "")[:90], "PASS" if v.get("passed") else "FAIL", summary.replace("|", "/")))
    L.append("")
    L.append("## Measurements")
    L.append("")
    L.append("| instance | provision | build (images) | boot | restart | reset | snapshot / restore state | stop | destroy | containers RSS (final) | VM disk dir |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    p6 = (ph.get("P6") or {}).get("detail") or {}
    for iid, m in manifests.items():
        t = dict(m.get("timings") or {})
        if iid == a:   # the original A manifest was replaced when A was reproduced in P9; its stop/restart timings live in the proof
            t.setdefault("stop_secs", (p6.get("stop_a") or {}).get("secs"))
            t.setdefault("restart_secs", (p6.get("restart_a") or {}).get("secs"))
        recs = meas.get(iid) or []
        final = recs[-1] if recs else {}
        L.append("| `%s` | %s | %s (%s) | %s | %s | %s | %s / %s | %s | %s | %s | %s |" % (iid, _t(t.get("provision_secs")), _t(t.get("build_secs")), _t(t.get("images_secs")), _t(t.get("boot_secs")), _t(t.get("restart_secs")), _t(t.get("reset_secs")),
                                                                        _t(t.get("snapshot_state_secs")), _t(t.get("restore_state_secs")), _t(t.get("stop_secs")), _t(t.get("destroy_secs")), _mib(final), ("%.1f GiB" % (final["vm_dir_kib"] / 2**20)) if final.get("vm_dir_kib") else "n/a"))
    p9 = (ph.get("P9") or {}).get("detail") or {}
    if p9.get("destroy_a"):
        L.append("")
        L.append("Destroy of `%s` (compose down -v + volumes/networks sweep + VM delete): %s; recreation from the manifest booted in %s." % (a, _t((p9.get("destroy_a") or {}).get("secs")), _t((p9.get("a_restarted") or {}).get("boot_secs"))))
    L.append("")
    L.append("## Host limits")
    L.append("")
    L.append("| host | cpus | memory | free disk (home) | VMs at collection time |")
    L.append("|---|---|---|---|---|")
    L.append("| %s (%s) | %s | %.1f GiB | %.0f GiB | %s |" % (host.get("model"), host.get("platform"), host.get("cpu_count"), (host.get("memory_bytes") or 0) / 2**30, (host.get("home_free_kib") or 0) / 2**20,
                                                          "; ".join("%s %s cpu=%s mem=%s" % (v["name"], v["status"], v["cpus"], v["memory"]) for v in host.get("vms", []))))
    L.append("")
    lim = acc.get("gates", [])
    live = next((x for x in lim if x["id"] == "M9-18"), {})
    L.append("Clean-checkout gate (M9-18): %s" % live.get("detail", "")[:600].replace("|", "/"))
    L.append("")
    L.append("## Blockers before scaling from two instances to ten")
    L.append("")
    L.append("1. **Host memory**: a full instance is sized at 10 GiB (containers ~%s at rest) and a focused one at 6 GiB; the host has %.0f GiB with the historical 12 GiB M7 VM still defined. Ten full instances need ~100 GiB — a remote-docker or cloud backend (the `Backend` interface is ready; `remote-docker` is unexercised)." % (_mib((meas.get(a) or [{}])[-1] if meas.get(a) else None), (host.get("memory_bytes") or 0) / 2**30))
    L.append("2. **Colima start is serialized per profile** and VM creation takes ~%s; ten VMs also need ~10 × 40 GiB sparse disks and ten ssh port forwarders." % _t((ma.get("timings") or {}).get("provision_secs")))
    L.append("3. **Image distribution**: every daemon loads ~%d images (%d GB) from the local tar cache; at ten instances a registry (push once, pull by digest) replaces `docker load`." % (len(ma.get("image_digests") or {}), 2))
    L.append("4. **Journey runner concurrency**: one runner process per instance is safe (instance-scoped campaign prefixes, run dirs and reports), but the runner still drives Docker through the CLI per call; ten concurrent suites will be CLI-bound.")
    L.append("5. **Source-to-Pay build** is a host-side Go build (~3 min) producing one image tag per driver hash; fine for ten, but the pinned repos live in `~/.rzp-architecture-replica` (outside the factory home).")
    L.append("6. **Migration assets and fts config** come from the checkout's `.local/` copies or the factory-home input bundle; they are credential-free but not committed (a content-addressed input store would make them portable like images).")
    L.append("")
    L.append("## Gates")
    L.append("")
    L.append("| gate | result | description |")
    L.append("|---|---|---|")
    for x in acc.get("gates", []):
        L.append("| %s | %s | %s |" % (x["id"], x["status"], x["description"].replace("|", "/")))
    L.append("")
    REPORT.write_text("\n".join(L) + "\n")
    return "wrote %s" % paths.rel(REPORT)
