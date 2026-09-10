#!/usr/bin/env python3
"""One reset/build/boot/empty-state/journey proof. Evidence is append-only by run ID."""
import argparse, hashlib, importlib.util, json, os, pathlib, subprocess, time, shutil
from datetime import datetime, timezone
D=pathlib.Path(__file__).resolve().parents[1]; ROOT=D.parents[1]

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def capture(*args,env=None):return subprocess.check_output(args,text=True,env=env).strip()
def inputs():
 paths=[D/'source-lock.json',D/'docker-compose.yml']
 for name in ('runtime','fixtures','build','scripts','tests'):
  paths.extend(p for p in (D/name).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
 return {str(p.relative_to(D)):sha(p) for p in sorted(paths)}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--run-id',required=True); args=ap.parse_args()
 if not all(c.isalnum() or c in '-_' for c in args.run_id):raise SystemExit('invalid run ID')
 out=D/'artifacts/clean-runs'/args.run_id; out.mkdir(parents=True,exist_ok=False)
 env=dict(os.environ); env.setdefault('COMPOSE_PROJECT_NAME','s2p_architecture_replica')
 if not env['COMPOSE_PROJECT_NAME'].startswith('s2p_'):raise SystemExit('requires s2p_ project')
 build_root=D/'.build/clean-runs'/args.run_id
 if build_root.exists():raise SystemExit('clean build root already exists')
 env['S2P_BUILD_ROOT']=str(build_root); env['S2P_GENERATED_STAGE']=str(build_root/'staging/vendor-payments')
 result={'schema_version':1,'run_id':args.run_id,'started_at':datetime.now(timezone.utc).isoformat(),'worktree':str(ROOT),'commit':capture('git','-C',str(ROOT),'rev-parse','HEAD'),'git_status_before':capture('git','-C',str(ROOT),'status','--porcelain=v1'),'project':env['COMPOSE_PROJECT_NAME'],'fixture_seed':'s2p-golden-20260820-001','inputs':inputs(),'durations':{},'steps':{},'passed':False}
 result['git_worktree_list']=capture('git','-C',str(ROOT),'worktree','list','--porcelain'); result['git_tree']=capture('git','-C',str(ROOT),'rev-parse','HEAD^{tree}'); result['build_root']=str(build_root); result['build_root_absent_before']=not build_root.exists()
 def step(name,argv):
  start=time.monotonic()
  with (out/(name+'.log')).open('w') as log: p=subprocess.run(argv,env=env,stdout=log,stderr=subprocess.STDOUT)
  result['durations'][name]=round(time.monotonic()-start,6); result['steps'][name]={'command':argv,'returncode':p.returncode,'log_sha256':sha(out/(name+'.log'))}
  if p.returncode:raise RuntimeError(f'{name} failed; see {out/name}.log')
 try:
  step('reset',[str(D/'scripts/reset.sh')])
  result['containers_after_reset']=capture('docker','ps','-aq','--filter','label=com.docker.compose.project='+env['COMPOSE_PROJECT_NAME'])
  result['volumes_after_reset']=capture('docker','volume','ls','-q','--filter','label=com.docker.compose.project='+env['COMPOSE_PROJECT_NAME'])
  if result['containers_after_reset'] or result['volumes_after_reset']:raise RuntimeError('project mutable state survived reset')
  step('bootstrap',[str(D/'scripts/bootstrap.sh')]); step('build',[str(D/'scripts/build.sh')]); step('build-runtime',[str(D/'scripts/build-runtime.sh')])
  build=json.loads((D/'artifacts/build-runtime.json').read_text()); env['S2P_SOURCE_IMAGE']=build['image']['tag']
  for name in ('build-runtime.json','build-runtime-source-verification.json','build-command-packages.json','build-manifest.json','source-verification.json'):(out/name).write_bytes((D/'artifacts'/name).read_bytes())
  shutil.copyfile(build['binary']['path'],out/'runtime-source.bin')
  manifest=json.loads((out/'build-manifest.json').read_text()); (out/'binaries').mkdir()
  for binary in manifest['binaries']:shutil.copyfile(pathlib.Path(manifest['output'])/binary['name'],out/'binaries'/binary['name'])
  if build['generated_stage']!=env['S2P_GENERATED_STAGE']:raise RuntimeError('runtime used a different source stage')
  step('boot',[str(D/'scripts/up.sh')]); step('health',[str(D/'scripts/health.sh')])
  os.environ.update(env)
  spec=importlib.util.spec_from_file_location('journey',D/'tests/integration/golden_journey.py'); journey=importlib.util.module_from_spec(spec); spec.loader.exec_module(journey)
  before={t:journey.sql('SELECT * FROM '+t+' ORDER BY id') for t in ('tax_payments','records','tds','vendorpayment_payout')}; before['boundary_payouts']=journey.boundary('/_evidence')['body']['payouts']
  result['empty_business_state']=before
  if any(before.values()):raise RuntimeError('business state not empty after clean boot')
  ids=capture('docker','ps','-q','--filter','label=com.docker.compose.project='+env['COMPOSE_PROJECT_NAME']).splitlines()
  inspected=json.loads(capture('docker','inspect',*ids))
  result['containers']=[{'id':v['Id'],'name':v['Name'],'image':v['Image'],'created':v['Created'],'health':v['State'].get('Health',{}).get('Status'),'networks':list(v['NetworkSettings']['Networks']),'ports':v['HostConfig']['PortBindings']} for v in inspected]
  network_ids=sorted({n for v in result['containers'] for n in v['networks']}); result['networks']=[{'name':v['Name'],'internal':v['Internal']} for v in json.loads(capture('docker','network','inspect',*network_ids))]
  env['EVIDENCE_OUT']=str(out/'journey.json'); step('journey',[str(D/'scripts/run-golden-journey.sh')])
  (D/'artifacts/journey-latest.json').write_bytes((out/'journey.json').read_bytes())
  (out/'runtime.log').write_bytes((D/'artifacts/journey-latest.log').read_bytes())
  step('independent-verification',['python3',str(D/'tests/integration/verify_evidence.py'),str(out/'journey.json')])
  step('contracts',['python3','-m','unittest','discover','-s',str(D/'tests/contracts'),'-p','test_*.py','-v'])
  env['S2P_JOURNEY_EVIDENCE']=str(out/'journey.json')
  step('verifier-tamper-tests',['python3','-m','unittest','discover','-s',str(D/'tests/reproducibility'),'-p','test_*.py','-v'])
  result['passed']=True
 except Exception as e:result['error']=str(e)
 finally:
  result['finished_at']=datetime.now(timezone.utc).isoformat(); result['file_hashes']={str(p.relative_to(out)):sha(p) for p in sorted(out.rglob('*')) if p.is_file()}; (out/'run.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
 print(json.dumps({'passed':result['passed'],'run':str(out),'durations':result['durations'],'error':result.get('error')}))
 raise SystemExit(0 if result['passed'] else 1)
if __name__=='__main__':main()
