#!/usr/bin/env python3
"""Inspect source, graph evidence, clean-run raw observations and current Git state."""
import argparse, hashlib, importlib.util, json, os, pathlib, re, subprocess, sys
from datetime import datetime, timezone
D=pathlib.Path(__file__).resolve().parents[1]; ROOT=D.parents[1]; A=D/'artifacts'
BASE='d54161c2e29873eb5f53804db58af45d48a7b4f6'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text())
def cmd(*args):return subprocess.check_output(args,text=True).strip()
def module(name,p):
 s=importlib.util.spec_from_file_location(name,p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m);return m
verify_journey=module('verify_evidence',D/'tests/integration/verify_evidence.py').verify

TRACE_REQUIREMENT_CHECKS={'kafka_ingest':['broker_input','source_consumer'],'tds_persisted':['record_persistence','tagback'],'pay_requested':['pay_controls','negative_contact'],'remittance_boundary':['idempotency_boundary','tagback_before_remittance'],'remittance_created':['remittance_persistence'],'money_loading_initiated':['source_pubsub_states'],'callback_received':['callback'],'money_loading_success':['state_scope','api_agrees','source_pubsub_states']}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--runs',nargs='+',default=['final-1','final-2','final-3']);args=ap.parse_args()
 gates=[]; details={};lock=load(D/'source-lock.json'); source=pathlib.Path(os.getenv('S2P_SOURCE_ROOT',lock['source_root'])); head=cmd('git','-C',str(ROOT),'rev-parse','HEAD')
 def gate(n,name,value,evidence):gates.append({'id':f'{n:02d}_{name}','passed':bool(value),'evidence':evidence})
 isolated=subprocess.run(['git','-C',str(ROOT),'merge-base','--is-ancestor',BASE,'HEAD']).returncode==0
 baseline=load(A/'isolation-before.json'); protected={x['path'] for x in baseline['worktrees']}
 gate(1,'isolated_worktree',isolated and str(ROOT) not in protected,[str(ROOT),BASE,head])
 isolation=module('isolation',D/'scripts/isolation.py').verify()
 gate(2,'protected_worktrees_unchanged',isolation['protected_worktrees_unchanged'],['artifacts/isolation-after.json'])
 gate(3,'preexisting_containers_unchanged',isolation['preexisting_containers_unchanged'],['artifacts/isolation-after.json'])
 gate(4,'durable_source',source.is_dir() and not str(source.resolve()).startswith(('/private/tmp','/tmp')), [str(source)])
 p=subprocess.run(['python3',str(D/'scripts/source_verify.py'),'--source-root',str(source),'--output',str(A/'acceptance-source-verification.json')],capture_output=True,text=True)
 gate(5,'source_verified',p.returncode==0 and load(A/'acceptance-source-verification.json').get('ok'),['artifacts/acceptance-source-verification.json'])
 inv=load(D/'inventory/architecture-inventory.json'); ev=load(D/'inventory/evidence-index.json')['evidence']; graph=load(D/'integration/unified-graph-patch.json'); nodes={n['id'] for n in graph['nodes']}; evidence={e['evidence_id']:e for e in ev}; valid=set(); failures=[]; cache={}
 generated_stage=pathlib.Path((D/'.build/current-generated-stage').read_text().strip()) if (D/'.build/current-generated-stage').exists() else D/'.build/staging'
 for e in ev:
  try:
   origin=e.get('origin'); repo=e.get('repository')
   if origin=='raw_runtime_trace': ok=sha(D/e['path'])==e['artifact_sha256']
   else:
    path=(generated_stage/repo/e['path'] if origin=='generated_staging' else (D if repo=='replica' else source/repo)/e['path'])
    if path not in cache:cache[path]=path.read_text(errors='replace').splitlines()
    lines=cache[path];text='\n'.join(lines[e['line_start']-1:e['line_end']])+('\n' if origin=='exact_source_range' else '')
    ok=hashlib.sha256(text.encode()).hexdigest()==e['excerpt_sha256']
    if origin=='generated_staging':ok=ok and sha(path)==e['provenance']['artifact_sha256']
   if ok:valid.add(e['evidence_id'])
   else:failures.append(e['evidence_id'])
  except Exception as exc:failures.append({'id':e['evidence_id'],'error':str(exc)})
 material=[i for i in inv['items'] if not i.get('runtime_fidelity_overlay')]; mandatory=[i for i in inv['items'] if i.get('mandatory_journey')]
 allowed={'ACTUAL_SOURCE_RUNNING','SOURCE_MAPPED_NOT_RUNNING','CONTRACT_FAITHFUL_REPLACEMENT','BEHAVIORAL_STUB','GRAPH_ONLY','PRODUCTION_STATE_UNKNOWN'}
 represented=[i for i in material if i['id'] in nodes and i['representation_status'] in allowed and i['evidence_ids'] and set(i['evidence_ids'])<=valid]
 coverage=100*len(represented)/len(material) if material else 0
 details['inventory_evidence']={'invalid_evidence':failures,'valid_count':len(valid),'edge_integrity':all(e['source'] in nodes and e['target'] in nodes and set(e['evidence_ids'])<=valid for e in graph['edges'])}
 gate(7,'mechanical_denominator',len(material)==inv['coverage']['denominator'] and (D/'scripts/generate-inventory.py').is_file() and (D/'inventory/exclusions.json').is_file() and bool(inv['coverage']['tracked_file_audit']) and len({i['id'] for i in material})==len(material),['scripts/generate-inventory.py','inventory/architecture-inventory.raw.json','inventory/exclusions.json'])
 gate(8,'architecture_representation_99_percent',coverage>=99 and details['inventory_evidence']['edge_integrity'],['inventory/architecture-inventory.json','inventory/evidence-index.json','integration/unified-graph-patch.json'])
 gate(9,'mandatory_items_represented',bool(mandatory) and all(i['id'] in nodes and set(i['evidence_ids'])<=valid for i in mandatory),['inventory/runtime-fidelity-overlay.json'])
 gate(10,'mandatory_runtime_fidelity',bool(mandatory) and all(i['representation_status'] in {'ACTUAL_SOURCE_RUNNING','CONTRACT_FAITHFUL_REPLACEMENT'} for i in mandatory),['spec/runtime-fidelity.json','spec/declared-deviations.yaml'])
 run_results=[]
 current_inputs=module('clean_run',D/'scripts/clean-run.py').inputs()
 runtime_prefix=('runtime/','fixtures/','build/','tests/integration/','tests/contracts/')
 relevant={k:v for k,v in current_inputs.items() if (not k.endswith('.md') and k.startswith(runtime_prefix)) or k in ('source-lock.json','docker-compose.yml','scripts/build.sh','scripts/build-runtime.sh','scripts/clean-run.py','scripts/up.sh','scripts/reset.sh','scripts/_common.sh')}
 for name in args.runs:
  path=A/'clean-runs'/name
  try:
   run=load(path/'run.json'); j=load(path/'journey.json'); independent=verify_journey(j)
   required_files={'runtime-source.bin','journey.json','runtime.log','build-runtime.json','build-runtime-source-verification.json','build-command-packages.json','build-manifest.json','source-verification.json'}|{n+'.log' for n in ('reset','bootstrap','build','build-runtime','boot','health','journey','contracts','independent-verification','verifier-tamper-tests')}
   bound=required_files<=run['file_hashes'].keys() and all((path/k).is_file() and sha(path/k)==v for k,v in run['file_hashes'].items()) and all(run['file_hashes'].get(n+'.log')==step['log_sha256'] for n,step in run['steps'].items())
   same_inputs=all(run['inputs'].get(k)==v for k,v in relevant.items())
   builds=load(path/'build-command-packages.json'); build_ok=all(x['build_rc']==x['list_rc']==0 and x['resolved_packages'] for x in builds['checks']) and {x['repository'] for x in builds['checks']}=={'vendor-payments','vendor-experience','accounting-integrations'}
   binaries=load(path/'build-manifest.json'); binary_root=path/'binaries'; binary_ok={b['name'] for b in binaries['binaries']}=={'vendor-payments','vendor-payments-worker','vendor-payments-workerv2','vendor-payments-migration','vendor-experience-api','vendor-experience-activity-worker','vendor-experience-workflow-worker','accounting-integrations-api','accounting-integrations-worker','accounting-integrations-cadence-worker'} and all((binary_root/b['name']).is_file() and sha(binary_root/b['name'])==b['sha256'] for b in binaries['binaries'])
   clean=not any(run['empty_business_state'].values()) and not run['containers_after_reset'] and not run['volumes_after_reset']
   runtime_build=load(path/'build-runtime.json'); build_ok=build_ok and sha(path/'runtime-source.bin')==runtime_build['binary']['sha256'] and runtime_build['image']['id'] in {c['image'] for c in run['containers']}
   build_ok=build_ok and 'go=go version go1.26.6 ' in (path/'build.log').read_text()
   build_ok=build_ok and run['build_root_absent_before'] and load(path/'build-runtime.json')['generated_stage']==str(pathlib.Path(run['build_root'])/'staging/vendor-payments')
   fresh_path=pathlib.Path(run['worktree']); registered=('worktree '+str(fresh_path)+'\n') in cmd('git','-C',str(ROOT),'worktree','list','--porcelain')
   fresh_verified=registered and fresh_path.exists() and cmd('git','-C',str(fresh_path),'rev-parse','HEAD')==head and not cmd('git','-C',str(fresh_path),'status','--porcelain=v1') and cmd('git','-C',str(fresh_path),'rev-parse','HEAD^{tree}')==run['git_tree']
   steps=all(x['returncode']==0 for x in run['steps'].values()) and {'reset','bootstrap','build','build-runtime','boot','health','journey','contracts','independent-verification'}<=run['steps'].keys()
   result={'id':name,'passed':bound and same_inputs and independent['passed'] and clean and steps and build_ok and binary_ok,'hash_bound':bound,'same_runtime_inputs':same_inputs,'recomputed':independent,'empty_after_reset':clean,'steps_passed':steps,'build_ok':build_ok and binary_ok,'fresh_final_checkout':fresh_verified and run['commit']==head and not run['git_status_before'] and run['worktree']!=str(ROOT),'named_binary_hashes':{b['name']:b['sha256'] for b in binaries['binaries']},'source_runtime_binary_hash':runtime_build['binary']['sha256'],'run':run}
  except Exception as exc:result={'id':name,'passed':False,'error':str(exc)}
  run_results.append(result)
 details['binary_reproducibility']={'named_outputs_equal':bool(run_results) and all(r.get('named_binary_hashes')==run_results[0].get('named_binary_hashes') for r in run_results),'source_runtime_equal':bool(run_results) and all(r.get('source_runtime_binary_hash')==run_results[0].get('source_runtime_binary_hash') for r in run_results)}
 details['runs']=run_results; successes=sum(r['passed'] for r in run_results); fresh=bool(run_results) and run_results[-1].get('fresh_final_checkout',False) and run_results[-1]['passed']
 gate(6,'all_three_repositories_build',bool(run_results) and all(r.get('build_ok') for r in run_results),['artifacts/clean-runs/*/build-command-packages.json','artifacts/clean-runs/*/build-manifest.json'])
 declared=set(re.findall(r'^\s*- id:\s*(\S+)',(D/'spec/declared-deviations.yaml').read_text(),re.M)); replacements=[i for i in mandatory if i['representation_status']=='CONTRACT_FAITHFUL_REPLACEMENT']
 contracts=bool(run_results) and all(r.get('run',{}).get('steps',{}).get('contracts',{}).get('returncode')==0 for r in run_results)
 requirement_checks=TRACE_REQUIREMENT_CHECKS
  # Bind declared runtime participation to independent controls in every clean run.
 for g in gates:
  if g['id'].startswith('10_'):g['passed']=g['passed'] and len(run_results)==3 and all(all(all(r.get('recomputed',{}).get('checks',{}).get(c) for c in requirement_checks.get(req,['unknown_requirement'])) for req in item['trace_requirements']) for item in mandatory for r in run_results)
 gate(11,'declared_tested_replacements',bool(replacements) and all(set(i['deviation_ids'])<=declared for i in replacements) and contracts,['spec/declared-deviations.yaml','tests/contracts/test_monolith_boundary.py','artifacts/clean-runs/*/contracts.log'])
 gate(12,'three_clean_runs_including_final_checkout',len(run_results)==3 and successes==3 and fresh,['artifacts/clean-runs/*/run.json'])
 def control(key):return len(run_results)==3 and all(r.get('recomputed',{}).get('checks',{}).get(key) for r in run_results)
 gate(13,'duplicate_and_repeated_command',control('duplicate_zero_delta') and control('pay_controls') and control('idempotency_boundary'),['artifacts/clean-runs/*/journey.json'])
 gate(14,'negative_internal_contact',control('negative_contact'),['artifacts/clean-runs/*/journey.json'])
 unique_ids={r['run']['containers'][0]['id'] for r in run_results if 'run' in r}
 outcomes=[r.get('recomputed',{}).get('business_outcome') for r in run_results]
 gate(15,'reset_replay',len(unique_ids)==3 and all(r.get('empty_after_reset') for r in run_results) and all(x==outcomes[0] for x in outcomes),['artifacts/clean-runs/*/run.json'])
 runtime_isolated=bool(run_results) and all(len(r.get('run',{}).get('containers',[]))==5 and all(not c['ports'] for c in r['run']['containers']) and all(n['internal'] for n in r['run']['networks']) for r in run_results)
 secret_scan=load(A/'secret-scan-summary.json') if (A/'secret-scan-summary.json').exists() else {}
 scan_bound=bool(secret_scan.get('scanned_files')) and all(secret_scan['scanned_files'].get(str((D/k).relative_to(ROOT)))==v for k,v in relevant.items() if k.startswith(('runtime/','fixtures/','build/')))
 gate(17,'synthetic_isolated_runtime',runtime_isolated and scan_bound and secret_scan.get('exit_code')==0 and secret_scan.get('findings')==0,['docker-compose.yml','runtime/source-driver/boot.go','artifacts/secret-scan-summary.json','artifacts/clean-runs/*/run.json'])
 reports=ROOT/'reports/source-to-pay-replica'; reports.mkdir(exist_ok=True)
 module('render_reports',D/'scripts/render-reports.py').render(ROOT,head,BASE,lock,coverage,inv,run_results,gates)
 docs='\n'.join(p.read_text() for p in reports.glob('*.md'))
 prohibited=re.findall(r'(?im)^.*(?:is identical to production|matches production exactly|production parity: true).*$' ,docs)
 gate(18,'production_claims_scoped',not prohibited and (reports/'PRODUCTION_UNKNOWNS.md').is_file() and 'extraction_completeness_is_not_measured_by_percentage' in inv['coverage'],['reports/source-to-pay-replica/PRODUCTION_UNKNOWNS.md','inventory/architecture-inventory-summary.md'])
 dirty=cmd('git','-C',str(ROOT),'status','--porcelain=v1'); gate(19,'clean_final_tree',not dirty,[head,dirty or 'git status --porcelain empty'])
 required=['FINAL_REPORT.md','TEST_GAP_MATRIX.md','PRODUCTION_UNKNOWNS.md','CLEAN_RUN_RESULTS.md','ASSUMPTIONS_AND_DECISIONS.md']
 gate(20,'reports_runbook_present',all((reports/f).is_file() for f in required) and (D/'README.md').is_file(),required+['DOMAIN_REPLICAS/source_to_pay/README.md'])
 (A/'acceptance-details.json').write_text(json.dumps(details,indent=2,sort_keys=True)+'\n')
 integrity=module('artifact_integrity',D/'scripts/artifact_integrity.py');integrity.create(); bound=integrity.verify()
 gate(16,'artifacts_hash_bound',bound and all(r.get('hash_bound') for r in run_results),['artifacts/artifact-hashes.json'])
 gates.sort(key=lambda g:g['id']); result={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'accepted':all(g['passed'] for g in gates),'base_commit':BASE,'final_commit':head,'source_lock_hash':sha(D/'source-lock.json'),'repository_observable_architecture_coverage':coverage,'coverage_limitation':'Representation of mechanically detected observations, not measured extraction recall or production parity.','inventory':{'total_material_items':len(material),'represented_items':len(represented),'critical_items':len(mandatory),'critical_items_executable':sum(i['representation_status'] in {'ACTUAL_SOURCE_RUNNING','CONTRACT_FAITHFUL_REPLACEMENT'} for i in mandatory)},'clean_runs':{'attempts':len(run_results),'successes':successes,'fresh_final_checkout_verified':fresh},'gates':gates,'deviations':sorted(declared),'production_unknowns':['deployed topology and flag values','real API/Payouts balance/workflow/Passport','bank settlement and tax filing'],'artifact_hashes':load(A/'artifact-hashes.json')['files'],'artifact_manifest_sha256':sha(A/'artifact-hashes.json')}
 (A/'acceptance.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps({'accepted':result['accepted'],'coverage':coverage,'clean_runs':result['clean_runs'],'failed_gates':[g['id'] for g in gates if not g['passed']]}));return 0 if result['accepted'] else 1
if __name__=='__main__':raise SystemExit(main())
