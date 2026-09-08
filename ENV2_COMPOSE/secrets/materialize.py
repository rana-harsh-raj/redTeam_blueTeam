#!/usr/bin/env python3
"""Copy generated synthetic credentials into narrow UID-10001 runtime volumes.

Host sources stay mode 0600. Only explicit consumer groups are streamed over
stdin; no host directory, Docker socket, or source credential is mounted into
the initializer. Named volumes are declared in Compose and removed by down -v.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'python:3.12-alpine'
CONFIG_GROUPS = ('payouts', 'ledger', 'fts', 'cfa', 'xbalances', 'mozart-mock')
_ARENA_SUFFIX = os.environ.get('ARENA_SUFFIX', '')  # M3.1: honour disposable-instance namespacing
VOLUMES = {**{'config-' + group: 'rzp-arena-config-' + group + _ARENA_SUFFIX for group in CONFIG_GROUPS},
           'secrets-kong': 'rzp-arena-secrets-kong' + _ARENA_SUFFIX,
           'secrets-monolith': 'rzp-arena-secrets-monolith' + _ARENA_SUFFIX,
           # M6: the two Workflow-engine substitutes run as uid 10001 and so
           # cannot read compose file-based secrets (host files stay 0600 and
           # are owned by the host user, which is how the root-running
           # cron-driver/verifier consume theirs). They get their own narrow
           # materialized volume instead -- deliberately NOT secrets-kong, so
           # the engine's admin-plane token never lands on the ingress
           # container's filesystem.
           'secrets-workflow': 'rzp-arena-secrets-workflow' + _ARENA_SUFFIX}
OWNER_LABEL = 'io.rzp-arena.generated'

INSTALL = '''import base64,hashlib,json,os,pathlib,shutil,sys
root=pathlib.Path('/output')
items=json.load(sys.stdin)
for item in root.iterdir():
 if item.is_dir() and not item.is_symlink(): shutil.rmtree(item)
 else: item.unlink()
os.chown(root,0,0)
os.chmod(root,0o700)
for item in items:
 relative=pathlib.PurePosixPath(item['path'])
 if relative.is_absolute() or '..' in relative.parts: raise ValueError('invalid materialized path')
 target=root.joinpath(*relative.parts)
 target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
 data=base64.b64decode(item['data'],validate=True)
 if hashlib.sha256(data).hexdigest()!=item['sha256']: raise ValueError('input checksum mismatch')
 descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
 with os.fdopen(descriptor,'wb') as output: output.write(data)
 os.chown(target,10001,10001)
for directory,dirs,files in os.walk(root,topdown=False):
 os.chmod(directory,0o500)
 os.chown(directory,10001,10001)
print(json.dumps({'installed_files':len(items)}))
'''

VERIFY = '''import hashlib,json,os,pathlib,sys
assert os.getuid()==10001
root=pathlib.Path('/input')
expected=json.load(sys.stdin)
actual={str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()}
assert actual==set(expected)
for relative,digest in expected.items():
 path=root/relative
 stat=path.stat()
 assert stat.st_uid==10001 and stat.st_gid==10001 and stat.st_mode&0o777==0o400
 assert hashlib.sha256(path.read_bytes()).hexdigest()==digest
for path in [root,*[p for p in root.rglob('*') if p.is_dir()]]:
 assert path.stat().st_mode&0o777==0o500
print(json.dumps({'verified_files':len(actual),'uid':os.getuid()}))
'''


def run(args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=60, **kwargs)


def read_source(path):
    path=Path(path)
    relative=path.relative_to(ROOT)
    current=ROOT
    for part in relative.parts:
        current=current/part
        if current.is_symlink():
            raise ValueError('Symlink is not an admitted generated input: '+str(relative))
    if not path.is_file():
        raise ValueError('Missing generated input: '+str(relative))
    # Only generated synthetic files under these two roots are admitted.
    if relative.parts[0] not in ('secrets','generated'):
        raise ValueError('Input outside generated credential roots')
    return path.read_bytes()


def source_groups():
    groups={}
    for service in CONFIG_GROUPS:
        directory=ROOT/'generated'/service
        files={}
        for path in sorted(directory.rglob('*')):
            if path.is_symlink():
                raise ValueError('Generated config symlink refused')
            if path.is_file() and path.suffix=='.toml':
                files[str(path.relative_to(directory))]=read_source(path)
        if not files:
            raise ValueError('No generated TOML files for '+service)
        groups['config-'+service]=files
    merchant_dir=ROOT/'secrets/merchant-keys'
    merchant_files={}
    for path in sorted(merchant_dir.iterdir()):
        if path.suffix != '.txt' or not path.stem.replace('_','').isalnum():
            raise ValueError('Unexpected merchant secret filename')
        merchant_files['merchants/'+path.name]=read_source(path)
    if not merchant_files:
        raise ValueError('No generated merchant secrets')
    groups['secrets-kong']={**merchant_files,
        'passport_private_key':read_source(ROOT/'secrets/passport_private_key.txt'),
        'auth_api_payouts':read_source(ROOT/'secrets/auth_api_payouts.txt'),
        # M6: batch-sim mounts secrets-kong and needs cred.Workflow
        # (payouts [auth.workflow], username rzp_live) to drive the REAL
        # payouts_internal/{id}/approve|reject routes -- see
        # substitutes/batch-sim/CONTRACT.md 5. The workflow-engine ADMIN token
        # is deliberately NOT here; it lives only in secrets-workflow.
        'auth_workflow_payouts':read_source(ROOT/'secrets/auth_workflow_payouts.txt')}
    groups['secrets-monolith']={
        'monolith_basic_auth':read_source(ROOT/'secrets/verifier-bridge/monolith'),
        **{name:read_source(ROOT/'secrets'/(name+'.txt')) for name in
           ('auth_api_payouts','auth_payouts_ledger','auth_monolith_payouts_db','auth_monolith_balance_db')}}
    # M6: workflow-engine + workflow-sim. auth_workflow_payouts is the shared
    # payouts [auth.workflow] password used on the approve/reject callback;
    # wfe_admin_token guards workflow-engine's admin plane only.
    groups['secrets-workflow']={name:read_source(ROOT/'secrets'/(name+'.txt')) for name in
        ('auth_workflow_payouts','wfe_admin_token')}
    return groups


def materialize(group, files, volume, project, image=IMAGE):
    run(['docker','volume','create','--label',OWNER_LABEL+'=1',
         '--label','com.docker.compose.project='+project,
         '--label','com.docker.compose.volume='+group,volume])
    metadata=json.loads(run(['docker','volume','inspect',volume]).stdout)[0]
    labels=metadata.get('Labels') or {}
    if labels.get(OWNER_LABEL)!='1' or labels.get('com.docker.compose.project')!=project or labels.get('com.docker.compose.volume')!=group:
        raise ValueError('Refusing volume without matching generated ownership labels: '+volume)
    if run(['docker','ps','-q','--filter','volume='+volume]).stdout.strip():
        raise ValueError('Generated volume is used by a running container; stop the arena before materializing: '+volume)
    names=[]
    evidence={'group':group,'volume':volume,'files':sorted(files),'status':'failed'}
    def interrupted(signum,frame):
        raise KeyboardInterrupt
    old_handlers={signum:signal.signal(signum,interrupted) for signum in (signal.SIGINT,signal.SIGTERM)}
    try:
        for action,script,uid,target,payload in (
            ('install',INSTALL,'0:0','/output',[{'path':path,'data':base64.b64encode(data).decode(),'sha256':hashlib.sha256(data).hexdigest()} for path,data in files.items()]),
            ('verify',VERIFY,'10001:10001','/input',{path:hashlib.sha256(data).hexdigest() for path,data in files.items()})):
            name='twin-materialize-'+uuid.uuid4().hex[:12]
            names.append(name)
            command=['docker','run','--rm','--pull','never','--name',name,'--network','none',
                     '--read-only','--user',uid,'--cap-drop','ALL','--security-opt','no-new-privileges']
            if action=='install':
                command+=['--cap-add','CHOWN','--cap-add','DAC_OVERRIDE']
            command+=['-i','-v',volume+':'+target+('' if action=='install' else ':ro'),image,'python3','-c',script]
            evidence[action]=json.loads(run(command,input=json.dumps(payload).encode()).stdout)
        evidence['status']='passed'
    finally:
        for signum in old_handlers:
            signal.signal(signum,signal.SIG_IGN)
        cleanup=True
        for name in names:
            try:
                subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
                remaining=run(['docker','ps','-aq','--filter','name=^/?'+name+'$']).stdout.strip()
                cleanup=cleanup and not remaining
            except (subprocess.SubprocessError,OSError):
                cleanup=False
        evidence['temporary_containers_removed']=cleanup
        for signum,handler in old_handlers.items():
            signal.signal(signum,handler)
        if not cleanup:
            raise RuntimeError('Materialization container cleanup could not be confirmed')
    return evidence


def main():
    os.umask(0o077)
    groups=source_groups()  # validate all host inputs before creating volumes
    run(['docker','image','inspect',IMAGE])  # refuse runtime image pulls
    config=json.loads(run(['docker','compose','--env-file',str(ROOT/'.env.arena'),
        '-f',str(ROOT/'docker-compose.yml'),'--profile','*','config','--format','json']).stdout)
    for group,volume in VOLUMES.items():
        if config.get('volumes',{}).get(group,{}).get('name')!=volume:
            raise ValueError('Compose volume declaration missing or inconsistent: '+group)
    results=[]
    for group,files in groups.items():
        results.append(materialize(group,files,VOLUMES[group],config['name']))
    output=ROOT/'.runtime/materialization.json'
    output.parent.mkdir(exist_ok=True,mode=0o700)
    output.write_text(json.dumps({'schema_version':1,'status':'passed','groups':results},indent=2)+'\n')
    output.chmod(0o600)
    print('Synthetic runtime materialization: %d groups verified as UID 10001; host modes unchanged' % len(results))


def destroy():
    failed=[]
    for volume in VOLUMES.values():
        try:
            if not run(['docker','volume','ls','-q','--filter','name=^'+volume+'$']).stdout.strip():
                continue
            metadata=json.loads(run(['docker','volume','inspect',volume]).stdout)[0]
            if (metadata.get('Labels') or {}).get(OWNER_LABEL)!='1':
                raise ValueError('Generated ownership label missing')
            run(['docker','volume','rm',volume])
            if run(['docker','volume','ls','-q','--filter','name=^'+volume+'$']).stdout.strip():
                raise ValueError('Volume removal not confirmed')
        except (subprocess.SubprocessError,OSError,ValueError):
            failed.append(volume)
    if failed:
        raise RuntimeError('Generated volume destruction could not be confirmed: '+', '.join(failed))
    print('Generated synthetic runtime volumes destroyed')


if __name__=='__main__':
    try:
        if sys.argv[1:]==['--destroy']:
            destroy()
        elif sys.argv[1:]:
            raise SystemExit('usage: materialize.py [--destroy]')
        else:
            main()
    except subprocess.SubprocessError:
        # Subprocess args/stdin may refer to generated credentials. Never log
        # payloads or captured output on failure.
        raise SystemExit('Synthetic runtime materialization failed during Docker execution')
    except (OSError,ValueError,RuntimeError) as exc:
        raise SystemExit(str(exc))
    except KeyboardInterrupt:
        raise SystemExit('Synthetic runtime materialization interrupted; temporary containers cleaned up')
