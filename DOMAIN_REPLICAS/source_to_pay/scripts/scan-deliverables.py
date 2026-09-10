#!/usr/bin/env python3
"""Scan the authored domain pack, inventory and fixed proofs, excluding build caches."""
import hashlib,json,pathlib,shutil,subprocess,time
D=pathlib.Path(__file__).resolve().parents[1];ROOT=D.parents[1];A=D/'artifacts'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 stage=D/'.build'/('secret-scan-'+str(time.time_ns()));stage.mkdir(parents=True)
 files={}
 for base in (D,ROOT/'reports/source-to-pay-replica'):
  for p in sorted(base.rglob('*')):
   if not p.is_file() or p.is_symlink():continue
   rel=p.relative_to(base)
   if any(x in {'.build','.runtime','.local','__pycache__','artifacts'} for x in rel.parts) or p.suffix=='.pyc':continue
   target=stage/p.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target);files[str(p.relative_to(ROOT))]=sha(p)
 report=stage/'findings.json';argv=['gitleaks','dir',str(stage),'--redact','--config',str(D/'inventory-scan.gitleaks.toml'),'--no-banner','--report-format','json','--report-path',str(report),'--max-target-megabytes','100']
 with (A/'secret-scan.log').open('w') as out:proc=subprocess.run(argv,stdout=out,stderr=subprocess.STDOUT)
 findings=json.loads(report.read_text()) if report.exists() else None
 result={'tool':subprocess.check_output(['gitleaks','version'],text=True).strip(),'exit_code':proc.returncode,'findings':len(findings) if findings is not None else None,'scanned_files':files,'allowlist':'inventory-scan.gitleaks.toml covers only deterministic extractor ID suffixes; default secret rules remain enabled','scope':'authored domain, inventory, fixed runtime proofs and reports; generated mutable observations/build caches excluded','log_sha256':sha(A/'secret-scan.log')}
 (A/'secret-scan-summary.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n');print(json.dumps({k:result[k] for k in ('exit_code','findings','scope')}));return proc.returncode
if __name__=='__main__':raise SystemExit(main())
