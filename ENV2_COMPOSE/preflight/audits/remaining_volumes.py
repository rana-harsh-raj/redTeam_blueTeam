#!/usr/bin/env python3
"""Inspect current LocalStack/Kafka volumes and application file-log inventories."""
from common import ROOT, arguments, current_containers, run, synthetic_values, clean_known, scan_directory
import json
import pathlib
import re
import subprocess
import tarfile
import tempfile
import time
args=arguments(__doc__)
rows=current_containers(args.project)
byservice={r['Config']['Labels']['com.docker.compose.service']:r for r in rows}
known=synthetic_values()
def cleaned(data): return clean_known(data,known)
import io
result={'schema_version':1,'started_at':time.time(),'surfaces':[]}
# LocalStack volume contains a locally generated self-signed test TLS pair and cached metadata.
row=byservice['localstack'];name='/var/lib/localstack'
with tempfile.TemporaryDirectory(prefix='twin-localstack-volume-') as td:
 td=pathlib.Path(td);files=td/'files';files.mkdir();data=run(['docker','cp',row['Id']+':'+name,'-']);inventory=[]
 with tarfile.open(fileobj=io.BytesIO(data)) as archive:
  for m in archive:
   if not m.isfile():continue
   relative=pathlib.PurePosixPath(m.name)
   if relative.is_absolute() or '..' in relative.parts:raise RuntimeError('Unsafe volume archive path')
   payload=archive.extractfile(m).read();target=files/m.name;target.parent.mkdir(parents=True,exist_ok=True)
   if b'\0' in payload[:8192]:payload=run(['strings','-a','-n','8'],input=payload)
   target.write_bytes(payload);inventory.append({'path':m.name,'bytes':m.size})
 rawscan=scan_directory(files,td)
 # Public certificate metadata and matching public key establish the private key's local test-server purpose.
 cert=run(['docker','exec',row['Id'],'openssl','x509','-in',name+'/cache/server.test.pem.crt','-noout','-subject','-issuer','-dates']).decode()
 pubcert=run(['docker','exec',row['Id'],'openssl','x509','-in',name+'/cache/server.test.pem.crt','-pubkey','-noout'])
 pubkey=run(['docker','exec',row['Id'],'openssl','pkey','-in',name+'/cache/server.test.pem.key','-pubout'])
 assert pubcert==pubkey and 'LocalStack Org' in cert and 'OU = Testing' in cert and 'CN = localhost' in cert
 expected_key=run(['docker','exec',row['Id'],'cat',name+'/cache/server.test.pem.key']).strip()
 replaced=0
 for path in files.rglob('*'):
  if path.is_file():
   payload=path.read_bytes()
   # Classify only bytes identical to the verified test-server key; any other key stays visible to Gitleaks.
   for match in list(re.finditer(rb'-----BEGIN (?:RSA )?PRIVATE KEY-----.*?-----END (?:RSA )?PRIVATE KEY-----',payload,re.S)):
    if match.group(0).strip()==expected_key:
     payload=payload.replace(match.group(0),b'local-test-private-key-redacted',1);replaced+=1
   path.write_bytes(payload)
 reviewed=scan_directory(files,td)
 result['surfaces'].append({'service':'localstack','volume':'env2_compose_localstack-data','file_count':len(inventory),'files':inventory,'raw_scan':rawscan,'scan_after_classifying_local_test_tls_key':reviewed,'classified_private_key_copies':replaced,'test_certificate_public_metadata':cert.strip().splitlines(),'test_certificate_key_matches':pubcert==pubkey,'status':'passed' if reviewed['exit_code']==0 and not reviewed['findings'] else 'blocked','caveat':'Volume files were inspected. In-memory SQS payloads are outside filesystem coverage; queue receipt APIs were not invoked because they change visibility state.','temporary_plaintext_removed':True})
# Kafka's offline segment reader decodes all current .log batches without joining a consumer group or committing offsets.
row=byservice['kafka'];mounts=[m for m in row['Mounts'] if m['Type']=='volume'];paths=[];inventory=[]
for mount in mounts:
 files=run(['docker','exec',row['Id'],'find',mount['Destination'],'-type','f']).decode().splitlines()
 inventory.append({'volume':mount['Name'],'file_count':len(files),'purpose':'Kafka data and index files' if mount['Destination']=='/var/lib/kafka/data' else 'empty auxiliary mount' if not files else 'auxiliary mount'})
 paths.extend(p for p in files if p.endswith('.log'))
data=run(['docker','exec','-e','KAFKA_HEAP_OPTS=-Xmx64m -Xms16m',row['Id'],'/opt/kafka/bin/kafka-dump-log.sh','--deep-iteration','--print-data-log','--files',','.join(paths)])
with tempfile.TemporaryDirectory(prefix='twin-kafka-volume-') as td:
 td=pathlib.Path(td);files=td/'files';files.mkdir();clean,hits=cleaned(data);(files/'decoded-segments.txt').write_bytes(clean);scan=scan_directory(files,td)
result['surfaces'].append({'service':'kafka','mounted_volumes':inventory,'data_segment_files_decoded':len(paths),'decoded_bytes_examined':len(data),'known_synthetic_secret_occurrences':hits,'scan':scan,'status':'passed' if scan['exit_code']==0 and not scan['findings'] else 'blocked','method':'Offline Kafka DumpLogSegments reader over current .log files; no consumer group, offset commit or topic mutation','caveat':'Index/offset metadata file names are inventoried; their numeric storage pages are not represented as logical application payloads. Concurrent segment rotation limits snapshot consistency.','temporary_plaintext_removed':True})
# Runtime app writable locations: list files only; full process stdout/stderr is scanned by the main runtime audit.
filelogs=[]
for service in ('payouts-api','ledger-api','fts-web','cfa-server','xbalances-server'):
 row=byservice[service];p=subprocess.run(['docker','exec',row['Id'],'find','/tmp','/var/log','-type','f'],capture_output=True)
 paths=p.stdout.decode().splitlines();filelogs.append({'service':service,'paths':paths,'enumeration_exit_code':p.returncode})
result['application_file_log_inventory']=filelogs
result['finished_at']=time.time();out=args.output/'remaining-volume-secret-scan.json';out.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'surfaces':[(x['service'],x['status']) for x in result['surfaces']],'app_file_counts':[(x['service'],len(x['paths'])) for x in filelogs]}))
result['status']='passed_with_caveats' if all(x['status']=='passed' for x in result['surfaces']) and all(x['enumeration_exit_code']==0 and not x['paths'] for x in result['application_file_log_inventory']) else 'blocked'
out.write_text(json.dumps(result,indent=2)+'\n')
