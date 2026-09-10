#!/usr/bin/env python3
"""Refresh current-boot metadata without starting application workloads."""
import base64,collections,contextlib,datetime,hashlib,importlib.util,io,json,os,pathlib,re,subprocess,sys,tempfile
from common import ROOT, ENV, arguments, current_containers, log_endpoints
args=arguments('Read-only current-boot config, environment, volume and log audit',build_evidence=True)
EVID=args.build_evidence.resolve()
OUT=args.output/'secret-and-endpoint-audit.json'
os.umask(0o077)
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def run(args,**kwargs):
 p=subprocess.run(args,capture_output=True,timeout=90,**kwargs)
 if p.returncode:raise RuntimeError('read-only command failed: '+args[0])
 return p.stdout

def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
def load(p):return json.loads(pathlib.Path(p).read_text())
def imp(name,p):
 spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
sys.path.insert(0,str(ENV/'config'))
config=imp('arena_config_audit',ENV/'config/generate.py')
material=imp('arena_material_audit',ENV/'secrets/materialize.py')
preflight=imp('arena_preflight_audit',ENV/'preflight/preflight.py')
report={'schema_version':1,'started_at':stamp(),'status':'in_progress','scope':'Current workspace Compose boot and existing five-service build evidence; read-only measurements','sections':{},'evidence_files':[]}
def evidence(p):report['evidence_files'].append({'path':str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else p.name,'sha256':sha(p)})
for n in ('provenance.json','build-results.json'):evidence(EVID/n)
for n in ('image-assets.json','post-build-verification.json','cfa-asset-readability.json','payouts-asset-readability.json'):
 if (EVID/n).exists():evidence(EVID/n)
prov=load(EVID/'provenance.json')
checks=json.loads(run(['python3',str(ENV/'build/check-inputs.py'),str(EVID/'provenance.json')]))
report['sections']['source_copies']={'status':'passed' if checks['status']=='passed' and all(not r['scanner_findings'] for r in prov['repositories']) else 'blocked','scanner_version':prov['scanner_version'],'current_inventory_hashes_verified':checks['status']=='passed','repositories':[{'service':r['name'],'commit':r['source_commit'],'admitted_files':len(r['patched_copy_manifest']['files']),'unresolved_findings':len(r['scanner_findings']),'excluded_files':len(r['excluded']),'arena_patch_files':r['changed_by_arena_patch']} for r in prov['repositories']],'caveats':['Gitleaks scans admitted first-party source and generated RPC before documented arena patches; patch hashes are separately verified. It does not prove absence of arbitrary secrets or exhaustively inspect linked dependency code.','Fresh credentials have generation-code and file-consistency evidence, not an external attestation of randomness or absence of preexisting values.']}
rows=current_containers(args.project,include_stopped=True)
images={r['Image']:json.loads(run(['docker','image','inspect',r['Image']]))[0] for r in rows}
expected={b['service']:b['candidate_image'] for b in load(EVID/'build-results.json')['builds']}
for svc in ('payouts','cfa'):
 if (EVID/(svc+'-asset-readability.json')).exists():expected[svc]=load(EVID/(svc+'-asset-readability.json'))['image_id']
imagechecks=[]
for svc,imageid in expected.items():
 actual=json.loads(run(['docker','image','inspect','rzp-arena/'+svc+':v1-candidate']))[0]
 imagechecks.append({'service':svc,'expected_id':imageid,'current_id':actual['Id'],'id_matches_build_evidence':actual['Id']==imageid,'runtime_container_count':sum(r['Image']==imageid for r in rows),'environment_keys':[v.partition('=')[0] for v in actual['Config'].get('Env',[])],'configured_user':actual['Config']['User']})
report['sections']['images']={'status':'passed' if all(x['id_matches_build_evidence'] and x['environment_keys']==['PATH'] and x['configured_user']=='appuser' for x in imagechecks) else 'blocked','images':imagechecks,'asset_scan_unresolved_findings':sum(len(x['asset_secret_scan_findings']) for x in load(EVID/'image-assets.json')['images']) if (EVID/'image-assets.json').exists() else None,'caveats':['Pass covers immutable image identity, metadata and existing build asset evidence. This command does not export or rescan immutable binaries; any earlier full-filesystem result is separate evidence.','Datastore and substitute image contents are not covered by the five-service source admission scan.']}
if report['sections']['images']['asset_scan_unresolved_findings'] not in (None,0):report['sections']['images']['status']='blocked'
secrets={}
for p in (ENV/'secrets').rglob('*'):
 if p.is_file() and (p.suffix in ('.txt','.json') or p.name in ('ledger','fts','monolith','ps-fastcron','ps-service','.env.secrets')):
  value=p.read_text().strip()
  if value:secrets[str(p.relative_to(ENV/'secrets'))]=value
known=set(secrets.values())
# Expand only locally generated scalar fields, bridge passwords and env secret values for comparison.
for name,value in secrets.items():
 if name.startswith('verifier-bridge/') and ':' in value:known.add(value.split(':',1)[1])
 if name=='.env.secrets':known.update(v.partition('=')[2] for v in value.splitlines())
known.update(v.replace('\n','\\n') for v in list(known) if '\n' in v)
known.update(base64.b64encode(v.encode()).decode() for k,v in secrets.items() if k.startswith('verifier-bridge/'))
known={v for v in known if len(v)>=12}
def sanitize(text):
 hits=0
 for value in sorted(known,key=len,reverse=True):
  if value in text:hits+=text.count(value);text=text.replace(value,'redacted')
 return text,hits

def gitleaks(text):
 with tempfile.TemporaryDirectory(prefix='twin-audit-scan-') as td:
  td=pathlib.Path(td);rp=td/'redacted.json';cf=td/'config.toml';cf.write_text('[extend]\nuseDefault = true\n');ig=td/'.gitleaksignore';ig.write_text('')
  p=subprocess.run(['gitleaks','stdin','--config',str(cf),'--gitleaks-ignore-path',str(ig),'--ignore-gitleaks-allow','--redact=100','--no-banner','--report-format','json','--report-path',str(rp),'--timeout','30'],input=text.encode(),capture_output=True,timeout=40)
  if not rp.exists():return {'exit_code':2,'findings':[],'error':'scanner omitted its required report'}
  findings=load(rp)
  return {'exit_code':p.returncode,'findings':[{'rule':f.get('RuleID'),'line':f.get('StartLine')} for f in findings]}

def endpoints(text):
 return dict(collections.Counter(name for name,pattern in preflight.FORBIDDEN_PATTERNS for _ in pattern.finditer(text)))

# Re-render to private temporary directory; never alter current generated files or runtime volumes.
with tempfile.TemporaryDirectory(prefix='twin-config-expected-') as td:
 old=config.GENERATED_DIR;config.GENERATED_DIR=td
 with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):config.main()
 rendered={str(p.relative_to(td)):p.read_bytes() for p in pathlib.Path(td).rglob('*') if p.is_file()}
 config.GENERATED_DIR=old
 current={str(p.relative_to(ENV/'generated')):p.read_bytes() for p in (ENV/'generated').rglob('*') if p.is_file()}
 differences=sorted(k for k in set(rendered)|set(current) if rendered.get(k)!=current.get(k))
configtext='\n'.join(v.decode(errors='replace') for v in current.values())
san,synthetichits=sanitize(configtext)
configscan=gitleaks(san)
compose=json.loads(run(['docker','compose','--env-file',str(ENV/'.env.arena'),'-f',str(ENV/'docker-compose.yml'),'--profile','*','config','--format','json']))
envmismatch=[];runtimeenv=[]
for row in rows:
 service=row['Config']['Labels'].get('com.docker.compose.service')
 desired=compose['services'].get(service,{}).get('environment',{})
 inherited={v.partition('=')[0]:v.partition('=')[2] for v in images[row['Image']]['Config'].get('Env',[])}
 actual={v.partition('=')[0]:v.partition('=')[2] for v in row['Config'].get('Env',[])}
 expectedenv={**inherited,**{k:str(v) for k,v in desired.items() if v is not None}}
 # One-off verifier commands deliberately add trace destinations; record all drift without values.
 different=sorted(k for k in set(actual)|set(expectedenv) if expectedenv.get(k)!=actual.get(k))
 if different:envmismatch.append({'service':service,'keys':different,'oneoff':row['Config']['Labels'].get('com.docker.compose.oneoff')=='True'})
 runtimeenv.extend(k+'='+v for k,v in actual.items())
envtext='\n'.join(runtimeenv);sanenv,envhits=sanitize(envtext);envscan=gitleaks(sanenv)
report['sections']['generated_config']={'status':'passed' if not differences and not configscan['findings'] and configscan['exit_code']==0 and not endpoints(configtext) else 'blocked','generated_files':len(current),'independent_rerender_byte_match':not differences,'different_file_names':differences,'known_synthetic_value_occurrences':synthetichits,'gitleaks_after_known_synthetic_redaction':configscan,'forbidden_endpoint_pattern_counts':endpoints(configtext),'host_secret_files_reviewed':len(secrets),'host_secret_modes_not_0600':[n for n in secrets if ((ENV/'secrets'/n).stat().st_mode&0o777)!=0o600],'caveats':['Current files match generation from local synthetic secret files and sanitized templates. Random-looking values alone are not evidence that no real credential ever existed.','Gitleaks findings matching current generated secrets are classified as synthetic; novel scanner findings remain unresolved.']}
report['sections']['runtime_environment']={'status':'passed' if not envscan['findings'] and envscan['exit_code']==0 and not endpoints(envtext) and not [x for x in envmismatch if not x['oneoff']] else 'blocked','containers_reviewed':len(rows),'known_synthetic_value_occurrences':envhits,'gitleaks_after_known_synthetic_redaction':envscan,'forbidden_endpoint_pattern_counts':endpoints(envtext),'compose_environment_differences':envmismatch,'caveats':['Inspection is point-in-time. One-off verifier overrides are listed by key only. Runtime state can change after capture.']}
# Hash existing mounted materialized files using existing consumers; no new helper container or write.
groups=material.source_groups();volresults=[]
for group,files in groups.items():
 name=material.VOLUMES[group]
 metadata=json.loads(run(['docker','volume','inspect',name]))[0]
 consumers=[(r,m) for r in rows if r['State']['Running'] for m in r['Mounts'] if m.get('Name')==name]
 labelok=metadata.get('Labels',{}).get('io.rzp-arena.generated')=='1'
 record={'group':group,'volume':name,'ownership_label_matches':labelok,'expected_file_count':len(files),'status':'blocked','runtime_read_only':all(not m['RW'] for _,m in consumers)}
 if consumers:
  row,mount=consumers[0];base=mount['Destination'];paths=[base+'/'+f for f in sorted(files)]
  output=run(['docker','exec',row['Id'],'sha256sum',*paths]).decode()
  actual={line.split(None,1)[1].strip():line.split(None,1)[0] for line in output.splitlines()}
  mismatch=[p for p in sorted(files) if actual.get(base+'/'+p)!=hashlib.sha256(files[p]).hexdigest()]
  record.update({'consumer':row['Config']['Labels']['com.docker.compose.service'],'current_hashes_match':not mismatch,'mismatched_relative_paths':mismatch,'status':'passed' if labelok and not mismatch and record['runtime_read_only'] else 'blocked'})
 else:record['reason']='No running consumer mounts this unused generated volume; installation verification exists but current content not independently reread.'
 volresults.append(record)
if args.read_unused_mozart:
 from unused_volume import read_unused_mozart
 record=next(r for r in volresults if r['group']=='config-mozart-mock')
 read_unused_mozart(record,material,groups['config-mozart-mock'],args.project)
report['sections']['volumes']={'status':'passed' if all(r['status']=='passed' for r in volresults if r['group']!='config-mozart-mock' or args.read_unused_mozart) else 'blocked','groups':volresults,'unused_group':'config-mozart-mock','prior_materialization_status':load(ENV/'.runtime/materialization.json')['status'],'caveats':['Generated config/secret volumes were byte-compared with synthetic inputs. Logical datastore snapshots and other named volumes are covered by companion commands.','Volume metadata and generated-input provenance support synthetic origin; arbitrary secrets inside every datastore page cannot be ruled out by this check.']}
report['finished_at']=stamp();OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'sections':{k:v['status'] for k,v in report['sections'].items()},'output':str(OUT)}))

# Classify scanner matches only with exact local origin/type evidence, retaining all novel matches.
classified_values=set()
configclass=[]
unresolved=[]
for finding in configscan['findings']:
 line=san.splitlines()[finding['line']-1];key,_,value=line.partition('=');key=key.strip();value=value.strip().strip('"\'')
 origin=[]
 for p in (ENV/'config/templates/base').rglob('*.toml'):
  for number,source_line in enumerate(p.read_text().splitlines(),1):
   if value and value in source_line and key in source_line:origin.append({'path':str(p.relative_to(ROOT)),'line':number})
 kind=None
 if key in ('Password1','Password2') and value.startswith(('arena-','arena_')) and origin:kind='explicit arena fixture credential in checked-in template'
 if key=='forDualWriteDirectPushToAPI' and re.fullmatch(r'[A-Za-z0-9]{14}',value) and origin:kind='Splitz experiment identifier, not an authentication credential'
 if kind:configclass.append({**finding,'field':key,'classification':kind,'origins':origin});classified_values.add(value)
 else:unresolved.append(finding)
report['sections']['generated_config']['reviewed_scanner_matches']=configclass
report['sections']['generated_config']['unresolved_findings']=unresolved
if not unresolved and configscan['exit_code'] in (0,1) and not differences and not endpoints(configtext):report['sections']['generated_config']['status']='passed'
envclass=[];envunresolved=[]
for finding in envscan['findings']:
 line=sanenv.splitlines()[finding['line']-1];key,_,value=line.partition('=')
 inherited=any('GPG_KEY='+value in image['Config'].get('Env',[]) for image in images.values())
 if key=='GPG_KEY' and re.fullmatch(r'[A-Fa-f0-9]{40}',value) and inherited:
  envclass.append({**finding,'field':key,'classification':'public GPG signing-key fingerprint inherited unchanged from base image metadata'});classified_values.add(value)
 else:envunresolved.append(finding)
report['sections']['runtime_environment']['reviewed_scanner_matches']=envclass
report['sections']['runtime_environment']['unresolved_findings']=envunresolved
if not envunresolved and envscan['exit_code'] in (0,1) and not endpoints(envtext) and not [x for x in envmismatch if not x['oneoff']]:report['sections']['runtime_environment']['status']='passed'
# All available logs since current start; raw content remains in memory only.
logrecords=[];combined=[];offset=0;logerrors=[];endpoint_reviews=[]
for row in rows:
 service=row['Config']['Labels'].get('com.docker.compose.service')
 p=subprocess.run(['docker','logs','--since',row['State']['StartedAt'],'--timestamps',row['Id']],capture_output=True,timeout=30)
 if p.returncode:logerrors.append({'service':service,'reason':'log retrieval failed'});continue
 data=(p.stdout+p.stderr).decode(errors='replace');clean,hits=sanitize(data)
 for value in classified_values:clean=clean.replace(value,'redacted')
 public_metadata_count=0
 for match in re.finditer(r'"(?:idempotency_key|request_hash)"\s*:\s*"([A-Fa-f0-9-]+)"',clean):
  value=match.group(1)
  if re.fullmatch(r'[A-Fa-f0-9]{64}|[A-Fa-f0-9]{8}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{4}-[A-Fa-f0-9]{12}',value):
   clean=clean.replace(value,'public-request-metadata');public_metadata_count+=1
 endpoint_review=log_endpoints(data,service,preflight.FORBIDDEN_PATTERNS);endpoint_reviews.append(endpoint_review)
 lines=clean.splitlines();combined.extend(lines)
 logrecords.append({'service':service,'container_id':row['Id'][:12],'bytes_examined':len(data.encode()),'lines_examined':len(lines),'log_scope':'all available stdout/stderr logs since current container start','known_generated_secret_occurrences':hits,'classified_idempotency_uuid_or_request_hash_occurrences':public_metadata_count,'forbidden_endpoint_pattern_counts':endpoints(data),'unresolved_endpoint_pattern_counts':endpoint_review['unresolved_pattern_counts'],'reviewed_diagnostic_urls':endpoint_review['reviewed_diagnostic_matches'],'combined_start_line':offset+1,'combined_end_line':offset+len(lines)})
 offset+=len(lines)
logscan=gitleaks('\n'.join(combined))
for finding in logscan['findings']:
 source=next((x for x in logrecords if x['combined_start_line']<=finding['line']<=x['combined_end_line']),{})
 finding['service']=source.get('service');finding['container_id']=source.get('container_id')
 # Debug-safe key classification only; never emit the matching log line or value.
 line=combined[finding['line']-1]
 finding['contains_arena_identity']='ARENA' in line
 finding['contains_auth_field']=bool(re.search(r'(?i)password|secret|authorization|api.?key',line))
report['sections']['logs']={'status':'passed' if not logscan['findings'] and logscan['exit_code']==0 and not logerrors and not any(x['unresolved_endpoint_pattern_counts'] for x in logrecords) and not sum(x['known_generated_secret_occurrences'] for x in logrecords) else 'blocked','containers_reviewed':len(logrecords),'total_bytes_examined':sum(x['bytes_examined'] for x in logrecords),'known_generated_secret_occurrences':sum(x['known_generated_secret_occurrences'] for x in logrecords),'gitleaks_after_classified_synthetic_and_public_redaction':logscan,'containers':logrecords,'errors':logerrors,'caveats':['All available stdout/stderr logs since each current container start were inspected. Rotated-away and later entries remain unexamined; application-file locations are separately inventoried.','Any exposure of generated arena credentials is disclosed separately; synthetic origin does not imply ideal log hygiene. Endpoint string matches are evidence of text, not proof of a network connection.']}
kong=next(r for r in rows if r['Config']['Labels'].get('com.docker.compose.service')=='kong-lite')
kongenv=dict(v.split('=',1) for v in kong['Config'].get('Env',[]))
report['runtime_image_inventory']=[{'service':r['Config']['Labels']['com.docker.compose.service'],'container_id':r['Id'][:12],'image_id':r['Image'],'started_at':r['State']['StartedAt']} for r in rows]
report['sections']['effective_issuer']={'status':'passed' if kongenv.get('PASSPORT_ISS')=='https://identity.arena.invalid' else 'blocked','running_override_present':'PASSPORT_ISS' in kongenv,'desired_issuer':'https://identity.arena.invalid','old_runtime_default_hostname':'edge.razorpay.com','observed_log_field':'context.passport.passport.iss','classification':'Signed identity claim, not a URL dereferenced by the inspected Passport verification code. This capture requires the local .invalid issuer in the running environment.','config_fix_applied':True,'restart_performed_by_audit':False,'source_evidence':['ENV2_COMPOSE/substitutes/kong-lite/server.py:53','payouts/pkg/passport/handler.go:63','github.com/razorpay/goutils/passport/v3@v3.4.0/handler.go:52','github.com/razorpay/goutils/passport/v3@v3.4.0/passport.go:27','github.com/golang-jwt/jwt@v3.2.2+incompatible/claims.go:32'],'fidelity_difference':'Local issuer identity differs from production. Existing configured kid, generated signing key, RS256 algorithm and validity timestamps remain unchanged.'}
report['sections']['origin_claim']={'status':'not_claimed','reason':'An absolute no-real-secrets-anywhere or fully-synthetic-all-bytes claim is not provable from these bounded scans. Public bank metadata and service source/dependencies intentionally are real reference inputs.','covered_claim':'No unresolved known-real-secret pattern in the inspected, provenance-matched surfaces, if all bounded section checks pass.','unverified':['immutable binary scan is separate existing evidence','logical stores and other volumes have separate companion reports','rotated-away and future log entries','nonprintable, encrypted and compressed secret encodings']}
report['status']='passed_with_caveats' if all(v['status']=='passed' for k,v in report['sections'].items() if k!='origin_claim') else 'blocked'
report['finished_at']=stamp();OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'final_sections':{k:v['status'] for k,v in report['sections'].items()},'log_unresolved_count':len(logscan['findings'])}))
