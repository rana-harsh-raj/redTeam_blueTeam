#!/usr/bin/env python3
"""Read-only logical snapshots of current MySQL, PostgreSQL, MongoDB and Redis."""
from common import ROOT, arguments, current_containers, run, synthetic_values, clean_known, scan_directory, classify_machinery_task_ids
import base64
import hashlib
import hmac
import json
import pathlib
import tempfile
import time
args=arguments(__doc__)
rows=current_containers(args.project)
byservice={r['Config']['Labels']['com.docker.compose.service']:r for r in rows}
known=synthetic_values()
def cleaned(data): return clean_known(data,known)
REPORT=args.output/'logical-store-secret-scan.json'
result={'schema_version':1,'started_at':time.time(),'stores':[]}
def inspect_data(service,data,method,extra=None):
 start=time.time();clean,hits=cleaned(data)
 with tempfile.TemporaryDirectory(prefix='twin-store-audit-') as td:
  td=pathlib.Path(td);files=td/'files';files.mkdir();(files/'logical-data.txt').write_bytes(clean)
  scan=scan_directory(files,td)
 entry={'service':service,'container_id':byservice[service]['Id'][:12],'method':method,'bytes_examined':len(data),'known_synthetic_secret_occurrences':hits,'scan':scan,'status':'passed' if scan['exit_code']==0 and not scan['findings'] else 'blocked','temporary_plaintext_removed':True,'elapsed_seconds':round(time.time()-start,2)}
 if extra:entry.update(extra)
 result['stores'].append(entry);REPORT.write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({'service':service,'bytes':len(data),'scan_exit':scan['exit_code'],'findings':len(scan['findings'])}),flush=True)
for service,secret in [('mysql-payouts','mysql_payouts_root_password'),('mysql-fts','mysql_fts_root_password'),('mysql-xbalances','mysql_xbalances_root_password'),('mysql-apidb-stub','mysql_apidb_root_password')]:
 command='MYSQL_PWD="$(cat /run/secrets/'+secret+')"; export MYSQL_PWD; exec mysqldump -uroot --all-databases --single-transaction --skip-lock-tables --no-tablespaces --hex-blob'
 data=run(['docker','exec',byservice[service]['Id'],'sh','-c',command])
 inspect_data(service,data,'mysqldump --all-databases single-transaction logical snapshot, including system authentication tables; no locks or mutation')
service='postgres-ledger'
command='PGPASSWORD="$(cat /run/secrets/postgres_ledger_password)"; export PGPASSWORD; exec pg_dumpall -h 127.0.0.1 -U ledger'
data=run(['docker','exec',byservice[service]['Id'],'sh','-c',command])
inspect_data(service,data,'pg_dumpall read-only logical export including cluster roles and databases')
service='mongo-cfa'
script="""const fs=require('fs');const pw=fs.readFileSync('/run/secrets/mongo_cfa_root_password','utf8').trim();const c=new Mongo('mongodb://cfa_root:'+encodeURIComponent(pw)+'@127.0.0.1:27017/?authSource=admin');const names=c.getDB('admin').runCommand({listDatabases:1}).databases.map(x=>x.name);for(const name of names){const d=c.getDB(name);for(const n of d.getCollectionNames()){try{print(EJSON.stringify({database:name,collection:n,documents:d.getCollection(n).find({}).toArray()}));}catch(e){print(EJSON.stringify({database:name,collection:n,audit_error_code:e.code,audit_error_code_name:e.codeName}));}}}"""
MONGO_EXPORT_SCRIPT=script
data=run(['docker','exec',byservice[service]['Id'],'mongosh','--quiet','--nodb','--eval',script])
inspect_data(service,data,'mongosh EJSON read of every collection returned by listDatabases, including admin auth records',{'collection_count':len(data.splitlines()),'unreadable_collections':[json.loads(x) for x in data.splitlines() if 'audit_error_code' in json.loads(x)]})
service='redis'
script="""local out={};local cur='0';repeat local scan=redis.call('SCAN',cur,'COUNT',1000);cur=scan[1];for _,key in ipairs(scan[2]) do local kind=redis.call('TYPE',key).ok;local value;if kind=='string' then value=redis.call('GET',key) elseif kind=='hash' then value=redis.call('HGETALL',key) elseif kind=='list' then value=redis.call('LRANGE',key,0,-1) elseif kind=='set' then value=redis.call('SMEMBERS',key) elseif kind=='zset' then value=redis.call('ZRANGE',key,0,-1,'WITHSCORES') elseif kind=='stream' then value=redis.call('XRANGE',key,'-','+') else value='unsupported-type' end;table.insert(out,{key=key,type=kind,value=value});end until cur=='0';return cjson.encode(out)"""
data=run(['docker','exec',byservice[service]['Id'],'redis-cli','--raw','EVAL_RO',script,'0'])
inspect_data(service,data,'Redis EVAL_RO SCAN plus type-specific read commands for all keys in DB0; read-only script',{'database':0,'key_count':len(json.loads(data))})
# Classify only identifiers tied to actual Machinery state records, retaining the raw scan above.
redis_entry=next(x for x in result['stores'] if x['service']=='redis')
redis_clean,task_proof=classify_machinery_task_ids(data)
redis_clean,_=cleaned(redis_clean)
with tempfile.TemporaryDirectory(prefix='twin-redis-reviewed-') as td:
 td=pathlib.Path(td);files=td/'files';files.mkdir();(files/'logical-data.txt').write_bytes(redis_clean);redis_reviewed=scan_directory(files,td)
redis_entry['reviewed_task_identifiers']=task_proof
redis_entry['scan_after_task_identifier_classification']=redis_reviewed
redis_entry['status']='passed' if redis_reviewed['exit_code']==0 and not redis_reviewed['findings'] else 'blocked'
result['finished_at']=time.time();REPORT.write_text(json.dumps(result,indent=2)+'\n')

# Mongo SCRAM material is classified only after deriving both stored keys from the generated password.
service='mongo-cfa';script="const fs=require('fs');const pw=fs.readFileSync('/run/secrets/mongo_cfa_root_password','utf8').trim();const c=new Mongo('mongodb://cfa_root:'+encodeURIComponent(pw)+'@127.0.0.1:27017/?authSource=admin');print(EJSON.stringify(c.getDB('admin').system.users.find({}).toArray()));"
users=json.loads(run(['docker','exec',byservice[service]['Id'],'mongosh','--quiet','--nodb','--eval',script]));password=(ROOT/'ENV2_COMPOSE/secrets/mongo_cfa_root_password.txt').read_text().strip();verified=[]
for user in users:
 assert user['user']=='cfa_root' and user['db']=='admin'
 for mechanism,credentials in user['credentials'].items():
  algorithm={'SCRAM-SHA-1':'sha1','SCRAM-SHA-256':'sha256'}[mechanism]
  secret=hashlib.md5((user['user']+':mongo:'+password).encode()).hexdigest().encode() if algorithm=='sha1' else password.encode()
  salted=hashlib.pbkdf2_hmac(algorithm,secret,base64.b64decode(credentials['salt']),credentials['iterationCount'])
  client=hmac.digest(salted,b'Client Key',algorithm);stored=hashlib.new(algorithm,client).digest();server=hmac.digest(salted,b'Server Key',algorithm)
  assert hmac.compare_digest(stored,base64.b64decode(credentials['storedKey']))
  assert hmac.compare_digest(server,base64.b64decode(credentials['serverKey']))
  known.update([credentials['storedKey'].encode(),credentials['serverKey'].encode()])
  verified.append({'user':'cfa_root','mechanism':mechanism,'stored_and_server_keys_match_generated_password':True})

entry=next(x for x in result['stores'] if x['service']=='mongo-cfa')
fullscript=MONGO_EXPORT_SCRIPT
data=run(['docker','exec',byservice['mongo-cfa']['Id'],'mongosh','--quiet','--nodb','--eval',fullscript])
clean,hits=cleaned(data)
with tempfile.TemporaryDirectory(prefix='twin-mongo-reviewed-') as td:
 td=pathlib.Path(td);files=td/'files';files.mkdir();(files/'logical-data.txt').write_bytes(clean);reviewed=scan_directory(files,td)
entry['reviewed_scram_credentials']=verified
entry['scan_after_cryptographic_generated_origin_verification']=reviewed
entry['status']='passed' if reviewed['exit_code']==0 and not reviewed['findings'] else 'blocked'
entry['caveat']='Protected config.system.sessions may remain inaccessible. Logical records do not cover every compressed, encrypted or deleted storage page.'
result['status']='passed_with_caveats' if all(x['status']=='passed' for x in result['stores']) else 'blocked'
result['finished_at']=time.time();REPORT.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'logical_stores_status':result['status'],'store_count':len(result['stores'])}))
