#!/usr/bin/env python3
"""Generate a deterministic, evidence-addressable S2P architecture inventory.

The detector is deliberately recall-oriented. It scans every tracked file in each
pinned repository, records how each file was handled, emits every match (including
duplicates at distinct evidence locations), and keeps residual eligible files in a
separate audit. A match proves that an architecture-relevant construct was observed;
it does not prove that no construct was missed.
"""
from __future__ import annotations

import argparse, hashlib, json, re, subprocess
from collections import Counter, defaultdict
from pathlib import Path

REPOS = ("vendor-payments", "vendor-experience", "accounting-integrations")
TEXT_EXT = {".go", ".proto", ".sql", ".toml", ".yaml", ".yml", ".json", ".sh", ".py", ".md", ".graphql", ".gql"}
SPECIAL = {"Dockerfile", "Makefile", "go.mod", "go.sum", "buf.yaml", "buf.gen.yaml"}
EXCLUDED_PARTS = {"vendor", "node_modules", ".git", "testdata", "examples", "example"}

# category, pattern, symbol/value capture. Patterns intentionally overlap: distinct
# categories are separate architecture observations at the same source line.
DETECTORS = [
    ("build_or_deployment_input", r"(?i)(?:^module\s+|^go\s+[0-9]|^FROM\s+|^ENTRYPOINT|^CMD\s+|^build:|^run:)", None),
    ("executable_entrypoint", r"^\s*func\s+main\s*\(", None),
    ("http_route", r"(?i)(?:HandleFunc|Handle|Methods|GET|POST|PUT|PATCH|DELETE)\s*\(\s*[`\"]([^`\"]+)", 1),
    ("http_route_definition", r"\bHTTPUri\s*:\s*[`\"]([^`\"]+)", 1),
    ("rpc_interface", r"^\s*rpc\s+([A-Za-z0-9_]+)\s*\(", 1),
    ("rpc_interface", r"^\s*(?:service)\s+([A-Za-z0-9_]+)\s*\{", 1),
    ("message_contract", r"^\s*message\s+([A-Za-z0-9_]+)\s*\{", 1),
    ("kafka_or_queue", r"(?i)(?:topic|queue|consumer|producer|listener).{0,80}?[`\"]([^`\"]{3,})[`\"]", 1),
    ("worker_or_job", r"(?i)(?:Register|Add|New)(?:Worker|Job|Task|Cron|Consumer)\s*\(", None),
    ("worker_registration", r"\b(?:RegisterTask|RegisterWorkflow|RegisterActivity(?:WithOptions)?|RegisterTasks)\s*\(", None),
    ("database_schema", r"(?i)^\s*(?:CREATE\s+(?:TABLE|INDEX)|ALTER\s+TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([A-Za-z0-9_.-]+)", 1),
    ("migration_sql", r"(?i)\b(?:CREATE\s+TABLE|ALTER\s+TABLE|DROP\s+TABLE(?:\s+IF\s+EXISTS)?)\s+[`\"]?([A-Za-z_][A-Za-z0-9_.-]*)", 1),
    ("migration_index", r"(?i)(?:CREATE\s+(?:UNIQUE\s+)?INDEX\s+|(?:PRIMARY\s+)?KEY\s+)[`\"]?([A-Za-z_][A-Za-z0-9_.-]*)", 1),
    ("orm_model", r"^\s*func\s+\([^)]*\)\s*TableName\s*\(\)\s*string", None),
    ("sql_target", r"(?i)\b(?:FROM|JOIN|INTO|UPDATE)\s+[`\"]?([A-Za-z_][A-Za-z0-9_.-]*)", 1),
    ("state_transition", r"(?i)\b(?:status|state)\s*(?::=|=|:)[^=].{0,100}", None),
    ("service_client", r"(?i)(?:New[A-Za-z0-9_]*(?:Client|Service)|ClientConfig|BaseURL|HostURL)", None),
    ("config_or_flag", r"(?i)(?:Get(?:String|Bool|Int|Duration)|LookupEnv|Splitz|FeatureFlag|Config\.Get)\s*\(\s*[`\"]([^`\"]+)", 1),
    ("config_member_access", r"\b((?:[A-Za-z_][A-Za-z0-9_]*Config|Config|AppConfig)(?:\.[A-Z][A-Za-z0-9_]*)+|GetConfig\(\)(?:\.[A-Z][A-Za-z0-9_]*)+)", 1),
    ("config_definition", r"^\s*([A-Za-z][A-Za-z0-9_.-]*)\s*=\s*.+$", 1),
    ("retry_or_idempotency", r"(?i)\b(?:idempotenc|dedup|retry|backoff)\w*\b", None),
    ("health_or_startup", r"(?i)\b(?:health|readiness|liveness|startup|ping)\b", None),
    ("storage_cache_notification", r"(?i)\b(?:redis|dynamo|s3|cache|stork|mailgun|notification)\b", None),
    ("observability", r"(?i)\b(?:trace[_ -]?id|span[_ -]?id|correlation[_ -]?id|prometheus|metrics)\b", None),
]

def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

def stable_id(repo: str, category: str, path: str, line: int, text: str) -> str:
    digest = hashlib.sha256(f"{repo}\0{category}\0{path}\0{line}\0{text}".encode()).hexdigest()[:16]
    return f"s2p:{repo}:{category}:{digest}"

def graph_id(kind: str, repository: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}\0{repository}\0{value}".encode()).hexdigest()[:16]
    return f"s2p-graph:{kind}:{digest}"

def edge_id(kind: str, source: str, target: str, evidence_id: str = "") -> str:
    digest = hashlib.sha256(f"{kind}\0{source}\0{target}\0{evidence_id}".encode()).hexdigest()[:16]
    return f"s2p-edge:{kind}:{digest}"

def go_module(repo: Path) -> str | None:
    mod = repo / "go.mod"
    if not mod.exists(): return None
    match = re.search(r"(?m)^module\s+(\S+)", mod.read_text(errors="replace"))
    return match.group(1) if match else None

def go_imports(lines: list[str]) -> list[tuple[int, str]]:
    """Return literal Go imports and their evidence line; generated/runtime parsing is out of scope."""
    result=[]; in_block=False
    for lineno, line in enumerate(lines, 1):
        stripped=line.strip()
        if re.match(r"^import\s*\($", stripped): in_block=True; continue
        if in_block and stripped == ")": in_block=False; continue
        candidate = stripped if in_block else re.sub(r"^import\s+", "", stripped)
        if not in_block and candidate == stripped: continue
        match=re.search(r'(?:^|\s)"([^"]+)"', candidate)
        if match: result.append((lineno, match.group(1)))
    return result

def generated_rpc_files(root: Path):
    """Yield generated protobuf Go files only; never scan generated implementation bodies."""
    if not root.exists(): return
    for repository in REPOS:
        repo_root=root/repository
        if not repo_root.exists(): continue
        for full in sorted(repo_root.rglob("*.go")):
            try: head="\n".join(full.read_text(errors="replace").splitlines()[:12])
            except OSError: continue
            if "Code generated by protoc" not in head: continue
            source=re.search(r"(?m)^// source:\s*(\S+)", head)
            yield repository, full.relative_to(repo_root).as_posix(), full, source.group(1) if source else None

def classify(path: str) -> tuple[str, str]:
    p = Path(path)
    if p.name in SPECIAL: return "eligible", "build_or_text_detector_input"
    if any(part in {"testdata", "fixtures"} for part in p.parts) or p.name.endswith(("_test.go", "_mock.go")): return "corroborating", "test_fixture_or_mock_evidence"
    if any(part in EXCLUDED_PARTS for part in p.parts): return "excluded", "excluded_path_component"
    if p.suffix in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".sum"}: return "excluded", "binary_or_dependency_lock"
    if p.suffix in TEXT_EXT or p.name in SPECIAL or p.name.startswith("Dockerfile"): return "eligible", "text_detector_input"
    return "excluded", "unsupported_non_architecture_extension"

def is_go_migration(rel: str) -> bool:
    p=Path(rel)
    return p.suffix == ".go" and any(part == "migrations" or part.endswith("_migrations") for part in p.parts)

def detector_applies(category: str, rel: str, line: str) -> bool:
    p=Path(rel); ext=p.suffix
    if category == "sql_target":
        return ext == ".sql" or bool(re.search(r"(?i)(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM).*(FROM|INTO|SET|WHERE)", line))
    if category == "config_definition": return ext == ".toml"
    if category == "orm_model": return ext == ".go"
    if category in {"migration_sql", "migration_index"}: return is_go_migration(rel)
    if category in {"config_member_access", "worker_registration", "http_route_definition"}: return ext == ".go"
    if category == "build_or_deployment_input": return p.name in SPECIAL or p.name.startswith("Dockerfile") or ext in {".yaml", ".yml"}
    return True

def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-root", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, default=Path("DOMAIN_REPLICAS/source_to_pay"))
    ap.add_argument("--generated-root", type=Path, help="Optional staging root containing generated protobuf Go artifacts")
    ns = ap.parse_args(); items=[]; evidence=[]; supporting=[]; exclusions=[]; file_audit=[]; repo_meta={}
    graph_nodes={}; graph_edges={}; package_ids={}; file_ids={}
    modules={name:go_module(ns.source_root/name) for name in REPOS}
    for name in REPOS:
        repo=ns.source_root/name
        if not (repo/".git").exists(): raise SystemExit(f"missing git repository: {repo}")
        sha=git(repo,"rev-parse","HEAD"); tree=git(repo,"rev-parse","HEAD^{tree}")
        repo_meta[name]={"sha":sha,"tree":tree,"tracked_files":0}
        service_id=graph_id("service",name,name)
        graph_nodes[service_id]={"id":service_id,"type":"service","label":name,"repository":name,"evidence_ids":[]}
        paths=git(repo,"ls-files","-z").split("\0") if git(repo,"ls-files","-z") else []
        for rel in sorted(x for x in paths if x):
            repo_meta[name]["tracked_files"] += 1
            disposition, reason=classify(rel); full=repo/rel
            audit={"repository":name,"path":rel,"disposition":disposition,"reason":reason,"matches":0}
            if disposition == "excluded":
                exclusions.append({"repository":name,"path":rel,"rule":reason}); file_audit.append(audit); continue
            try: lines=full.read_text(errors="replace").splitlines()
            except OSError:
                audit.update(disposition="unreadable", reason="tracked_file_unreadable"); file_audit.append(audit); continue
            file_items=[]
            # A versioned Go migration is material even when its SQL is assembled
            # across string fragments and no individual line matches a SQL detector.
            if disposition == "eligible" and is_go_migration(rel):
                value=Path(rel).name; iid=stable_id(name,"migration_artifact",rel,1,value)
                first_line=lines[0] if lines else ""
                ev={"evidence_id":f"ev:{iid}","repository":name,"sha":sha,"path":rel,"line_start":1,"line_end":1,"excerpt_sha256":hashlib.sha256(first_line.encode()).hexdigest()}
                item={"id":iid,"category":"migration_artifact","repository":name,"pinned_sha":sha,"path":rel,"symbol_or_value":value,"evidence_ids":[ev["evidence_id"]],"callers":[],"consumers":[],"representation_status":"SOURCE_MAPPED_NOT_RUNNING","mandatory_journey":False,"potential_journey_relevance":False,"confidence":"EXPLICIT","runtime_verification_artifact":None,"not_executable_reason":"Migration file is materially represented; execution is not inferred from source presence.","associated_deviation_or_unknown":None}
                items.append(item); file_items.append(item); evidence.append(ev); audit["matches"]+=1
            for lineno,line in enumerate(lines,1):
                for category,pattern,group in DETECTORS:
                    if not detector_applies(category, rel, line): continue
                    m=re.search(pattern,line)
                    if not m: continue
                    value=(m.group(group) if group else line.strip())[:240]
                    iid=stable_id(name,category,rel,lineno,value)
                    ev={"evidence_id":f"ev:{iid}","repository":name,"sha":sha,"path":rel,"line_start":lineno,"line_end":lineno,"excerpt_sha256":hashlib.sha256(line.encode()).hexdigest()}
                    critical=bool(re.search(r"(?i)(tds|tax.?payment|internal.?contact|remittance|tag.?back|payout.?status)", rel+" "+line))
                    if disposition == "corroborating":
                        supporting.append({"repository":name,"sha":sha,"path":rel,"line":lineno,"category":category,"value":value,"excerpt_sha256":ev["excerpt_sha256"]}); audit["matches"]+=1; continue
                    items.append({"id":iid,"category":category,"repository":name,"pinned_sha":sha,"path":rel,"symbol_or_value":value,"evidence_ids":[ev["evidence_id"]],"callers":[],"consumers":[],"representation_status":"SOURCE_MAPPED_NOT_RUNNING","mandatory_journey":False,"potential_journey_relevance":critical,"confidence":"EXPLICIT" if category in {"executable_entrypoint","http_route","rpc_interface","message_contract","database_schema"} else "WEAKLY_INFERRED","runtime_verification_artifact":None,"not_executable_reason":"Runtime verification is owned by acceptance artifacts and was not inferred from source scanning.","associated_deviation_or_unknown":None})
                    file_items.append(items[-1])
                    evidence.append(ev); audit["matches"]+=1
            # Deterministic containment graph for every scanned material file.
            if disposition == "eligible":
                package=Path(rel).parent.as_posix(); package="." if package == "." else package
                package_key=(name,package); package_id=package_ids.setdefault(package_key,graph_id("package",name,package))
                file_id=graph_id("file",name,rel); file_ids[(name,rel)]=file_id
                graph_nodes.setdefault(package_id,{"id":package_id,"type":"package","label":package,"repository":name,"evidence_ids":[]})
                graph_nodes[file_id]={"id":file_id,"type":"file","label":rel,"repository":name,"evidence_ids":[]}
                for kind,source,target in (("service_contains_package",service_id,package_id),("package_contains_file",package_id,file_id)):
                    eid=edge_id(kind,source,target); graph_edges[eid]={"id":eid,"type":kind,"source":source,"target":target,"evidence_ids":[]}
                for item in file_items:
                    eid=edge_id("file_contains_observation",file_id,item["id"],item["evidence_ids"][0]); graph_edges[eid]={"id":eid,"type":"file_contains_observation","source":file_id,"target":item["id"],"evidence_ids":item["evidence_ids"]}
                if Path(rel).suffix == ".go" and modules[name]:
                    for lineno, imported in go_imports(lines):
                        for target_repo,module in modules.items():
                            if module and (imported == module or imported.startswith(module+"/")):
                                target_package=imported[len(module):].lstrip("/") or "."
                                target_id=package_ids.setdefault((target_repo,target_package),graph_id("package",target_repo,target_package))
                                graph_nodes.setdefault(target_id,{"id":target_id,"type":"package","label":target_package,"repository":target_repo,"evidence_ids":[]})
                                ev_id=f"ev:{stable_id(name,'go_import',rel,lineno,imported)}"
                                import_ev={"evidence_id":ev_id,"repository":name,"sha":sha,"path":rel,"line_start":lineno,"line_end":lineno,"excerpt_sha256":hashlib.sha256(lines[lineno-1].encode()).hexdigest()}
                                evidence.append(import_ev)
                                eid=edge_id("go_imports_package",file_id,target_id,ev_id); graph_edges[eid]={"id":eid,"type":"go_imports_package","source":file_id,"target":target_id,"evidence_ids":[ev_id],"import_path":imported}
                                break
            file_audit.append(audit)
    generated=[]
    if ns.generated_root:
        proto_sha=git(ns.source_root/"proto","rev-parse","HEAD") if (ns.source_root/"proto"/".git").exists() else None
        for repository,rel,full,proto_source in generated_rpc_files(ns.generated_root):
            digest=hashlib.sha256(full.read_bytes()).hexdigest(); iid=stable_id(repository,"generated_rpc_artifact",rel,1,digest)
            source_repository=None; source_path=None; source_sha=None
            if proto_source:
                candidates=(("proto",proto_source,proto_sha),(repository,f"proto/{proto_source}",repo_meta[repository]["sha"]),(repository,proto_source,repo_meta[repository]["sha"]))
                for candidate_repo,candidate_path,candidate_sha in candidates:
                    if (ns.source_root/candidate_repo/candidate_path).is_file():
                        source_repository,candidate_path,source_sha=candidate_repo,candidate_path,candidate_sha; source_path=candidate_path; break
            provenance={"kind":"generated_rpc_artifact","generated_path":rel,"artifact_sha256":digest,"proto_source":proto_source,"proto_source_path":source_path,"proto_repository":source_repository,"proto_pinned_sha":source_sha,"generation_script":"build/codegen.py","generation_manifest":"artifacts/build-manifest.json","source_lock":"source-lock.json","body_scanned":False}
            ev_id=f"ev:{iid}"; evidence.append({"evidence_id":ev_id,"repository":repository,"sha":digest,"path":rel,"line_start":1,"line_end":1,"excerpt_sha256":hashlib.sha256(full.read_text(errors="replace").splitlines()[0].encode()).hexdigest(),"origin":"generated_staging","provenance":provenance})
            items.append({"id":iid,"category":"generated_rpc_artifact","repository":repository,"pinned_sha":repo_meta[repository]["sha"],"path":rel,"symbol_or_value":proto_source or rel,"evidence_ids":[ev_id],"callers":[],"consumers":[],"representation_status":"SOURCE_MAPPED_NOT_RUNNING","mandatory_journey":False,"potential_journey_relevance":bool(re.search(r"(?i)(tds|tax.?payment|internal.?contact|remittance|tag.?back|payout.?status)",rel+" "+(proto_source or ""))),"confidence":"EXPLICIT","runtime_verification_artifact":None,"not_executable_reason":"Generated RPC metadata is inventoried without interpreting generated runtime internals.","associated_deviation_or_unknown":None,"provenance":provenance})
            service_id=graph_id("service",repository,repository); package=Path(rel).parent.as_posix()
            package_id=package_ids.setdefault((repository,package),graph_id("package",repository,package)); file_id=graph_id("generated_file",repository,rel)
            graph_nodes.setdefault(package_id,{"id":package_id,"type":"package","label":package,"repository":repository,"evidence_ids":[]})
            graph_nodes[file_id]={"id":file_id,"type":"generated_rpc_file","label":rel,"repository":repository,"evidence_ids":[ev_id]}
            for kind,source,target,edge_evidence in (("service_contains_package",service_id,package_id,[]),("package_contains_generated_file",package_id,file_id,[ev_id]),("generated_file_contains_observation",file_id,iid,[ev_id])):
                eid=edge_id(kind,source,target,ev_id if edge_evidence else ""); graph_edges[eid]={"id":eid,"type":kind,"source":source,"target":target,"evidence_ids":edge_evidence}
            generated.append(provenance)
    # Exact denominator is all detected material observations. This measures representation,
    # while the file audit and residuals expose extraction completeness separately.
    items.sort(key=lambda x:x["id"]); evidence.sort(key=lambda x:x["evidence_id"])
    represented=sum(bool(i["evidence_ids"]) and i["representation_status"] in {"ACTUAL_SOURCE_RUNNING","SOURCE_MAPPED_NOT_RUNNING","CONTRACT_FAITHFUL_REPLACEMENT","BEHAVIORAL_STUB","GRAPH_ONLY","PRODUCTION_STATE_UNKNOWN"} for i in items)
    bycat=Counter(i["category"] for i in items); residual=[x for x in file_audit if x["disposition"]=="eligible" and x["matches"]==0]
    summary={"metric":"repository_observable_architecture_representation_coverage","denominator_rule":"all non-test material observations emitted by the versioned, file-type-scoped detectors plus metadata-only generated RPC artifacts when --generated-root is supplied, without deduplication across evidence locations","denominator":len(items),"represented":represented,"percentage":round(100*represented/len(items),3) if items else 0,"extraction_completeness_is_not_measured_by_percentage":True,"tracked_file_audit":{"total":len(file_audit),"eligible":sum(x["disposition"]=="eligible" for x in file_audit),"corroborating":sum(x["disposition"]=="corroborating" for x in file_audit),"excluded":sum(x["disposition"]=="excluded" for x in file_audit),"unreadable":sum(x["disposition"]=="unreadable" for x in file_audit),"eligible_with_no_detector_match":len(residual)},"items_by_category":dict(sorted(bycat.items())),"supporting_evidence_observations":len(supporting),"generated_rpc_artifacts":len(generated),"limitations":["Regex detectors cannot prove semantic completeness or detect dynamically constructed registrations.","Configuration structs and indirect read chains are only partially captured; config definitions are broad and require semantic review.","Generated RPC files are included only when --generated-root is supplied, and their implementation bodies are intentionally not interpreted.","Go dependency edges cover literal imports that resolve to one of the three pinned repository modules; dynamic and external dependencies remain outside this graph.","Callers and consumers require semantic or runtime analysis and remain empty unless separately curated.","Test, fixture, and mock observations are retained as corroborating evidence but excluded from the material executable denominator.","A 100% representation percentage means every detected denominator item has an evidence-backed graph representation; it does not mean all production architecture was discovered."]}
    inv=ns.output_root/"inventory"
    dump(inv/"architecture-inventory.raw.json", {"schema_version":1,"repositories":repo_meta,"items":items})
    dump(inv/"architecture-inventory.json", {"schema_version":1,"coverage":summary,"items":items})
    dump(inv/"evidence-index.json", {"schema_version":1,"evidence":evidence})
    dump(inv/"supporting-evidence.json", {"schema_version":1,"role":"corroborating_only","observations":supporting})
    dump(inv/"mandatory-journey-source-closure.json", {"schema_version":1,"selection_rule":"potential_journey_relevance: case-insensitive source path or line match for tds, tax-payment, internal-contact, remittance, tag-back, or payout-status","warning":"Recall-oriented candidate closure; mandatory_journey remains false unless separately established by verified journey evidence.","items":[x for x in items if x["potential_journey_relevance"]],"corroborating_observations":[x for x in supporting if re.search(r"(?i)(tds|tax.?payment|internal.?contact|remittance|tag.?back|payout.?status)",x["path"]+" "+x["value"])]})
    dump(inv/"generated-artifact-provenance.json", {"schema_version":1,"generated_root":str(ns.generated_root) if ns.generated_root else None,"selection_rule":"*.go with Code generated by protoc in the first 12 lines","artifacts":generated})
    dump(inv/"exclusions.json", {"schema_version":1,"rules":sorted({x["rule"] for x in exclusions}),"files":exclusions})
    dump(inv/"detector-coverage-audit.json", {"schema_version":1,"repositories":repo_meta,"files":file_audit,"residual_eligible_files":residual,"summary":summary})
    md=["# Architecture inventory summary","",f"Detected denominator: **{len(items)}** observations; represented: **{represented}** ({summary['percentage']}%).","","> This is representation coverage of detected observations, not proof of extraction or production completeness.","","## Detector audit","",f"Tracked files: {len(file_audit)}; eligible: {summary['tracked_file_audit']['eligible']}; corroborating test/fixture/mock files: {summary['tracked_file_audit']['corroborating']}; excluded: {summary['tracked_file_audit']['excluded']}; unreadable: {summary['tracked_file_audit']['unreadable']}; eligible residuals: {len(residual)}.",f"Corroborating observations: {len(supporting)}.","","## Category counts",""]+[f"- `{k}`: {v}" for k,v in sorted(bycat.items())]+["","## Known blind spots",""]+[f"- {x}" for x in summary["limitations"]]
    (inv/"architecture-inventory-summary.md").write_text("\n".join(md)+"\n")
    for i in items:
        graph_nodes[i["id"]]={"id":i["id"],"type":i["category"],"label":i["symbol_or_value"],"status":i["representation_status"],"repository":i["repository"],"evidence_ids":i["evidence_ids"]}
    dump(ns.output_root/"integration"/"unified-graph-patch.json", {"schema_version":1,"namespace":"source_to_pay","operation":"add_namespaced_subgraph","nodes":sorted(graph_nodes.values(),key=lambda x:x["id"]),"edges":sorted(graph_edges.values(),key=lambda x:x["id"]),"edge_semantics":{"service_contains_package":"repository service owns source or generated package","package_contains_file":"package contains tracked material file","file_contains_observation":"file contains detector observation at cited evidence","package_contains_generated_file":"package contains generated RPC artifact with provenance evidence","generated_file_contains_observation":"generated file is represented by metadata-only inventory observation","go_imports_package":"Go source file has literal import resolving to pinned repository module"},"source_inventory":"../inventory/architecture-inventory.json","shared_graph_modified":False})
    print(json.dumps(summary, indent=2, sort_keys=True)); return 0
if __name__ == "__main__": raise SystemExit(main())
