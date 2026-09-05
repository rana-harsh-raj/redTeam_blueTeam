#!/usr/bin/env python3
"""Set machine-local admitted-source paths and image tag, preserving other settings.

Run after prepare-repos.py and check-inputs.py. This changes no repository
contents, images, generated config, or containers. --check prints the proposed
managed settings without writing; --env-file can target a separate preview.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import shutil

ROOT=Path(__file__).resolve().parents[1]
REPOSITORIES=('payouts','ledger','fts','cfa','x-balances')
MIGRATIONS={
    'PAYOUTS_REPO_MIGRATIONS_DIR':'payouts/internal/database/migrations',
    'LEDGER_REPO_MIGRATIONS_DIR':'ledger/internal/database/rx_migrations',
    'FTS_REPO_MIGRATIONS_DIR':'fts/internal/migrations',
    'XBALANCES_REPO_MIGRATIONS_DIR':'x-balances/internal/database/migrations',
}


def settings(repos_root, tag, migration_root=None):
    repos_root=Path(repos_root).resolve(strict=True)
    if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}',tag):
        raise ValueError('Invalid Docker image tag')
    for repository in REPOSITORIES:
        if not (repos_root/repository/'go.mod').is_file():
            raise ValueError('Missing prepared repository: '+repository)
    values={'ARENA_TAG':tag}
    for key,relative in MIGRATIONS.items():
        path=repos_root/relative
        if not path.is_dir():
            raise ValueError('Missing prepared migration directory: '+relative)
        values[key]=str(Path(migration_root)/Path(relative).parts[0]) if migration_root else str(path)
    # Kept for legacy build tooling; current Compose reads generated FTS
    # config, never upstream runtime settings. This directory may be empty.
    values['FTS_REPO_CONFIG_DIR']=str(repos_root/'fts/config')
    return values


def migration_plan(repos_root, provenance_path):
    """Select only scanner-admitted migration source, excluding all configs."""
    repos_root=Path(repos_root).resolve(strict=True)
    provenance_path=Path(provenance_path)
    provenance=json.loads(provenance_path.read_text())
    if provenance.get('status')!='prepared' or Path(provenance['destination']).resolve()!=repos_root:
        raise ValueError('Provenance does not admit this prepared source directory')
    admitted={repository['name']:{item['path']:item['sha256'] for item in
              repository.get('patched_copy_manifest',{}).get('files',[])} for repository in provenance['repositories']}
    files=[]
    for relative in MIGRATIONS.values():
        repository=Path(relative).parts[0]
        source=repos_root/relative
        count=0
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ValueError('Migration symlink refused')
            if not path.is_file() or path.suffix not in ('.go','.sql'):
                continue
            repository_path=path.relative_to(repos_root/repository).as_posix()
            if path.resolve()!=path or path.is_symlink():
                raise ValueError('Migration path must not traverse a symlink')
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            if admitted.get(repository,{}).get(repository_path)!=digest:
                raise ValueError('Migration asset is not hash-matched to admitted source: '+repository_path)
            files.append({'source':str(path),'path':repository+'/'+path.relative_to(source).as_posix(),
                          'sha256':digest,'mode':'0444'})
            count+=1
        if not count:
            raise ValueError('No admitted Go/SQL migration assets in '+relative)
    return {'schema_version':1,'source_root':str(repos_root),
            'source_provenance_sha256':hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
            'classification':'approved credential-free migration assets; source content unchanged',
            'files':files}


def verify_migrations(destination, plan):
    destination=Path(destination)
    if destination.is_symlink() or stat.S_IMODE(destination.stat().st_mode)!=0o700:
        raise ValueError('Runtime migration root must be a private mode0700 directory')
    expected={item['path'] for item in plan['files']}
    observed=set()
    for path in destination.rglob('*'):
        if path.is_symlink(): raise ValueError('Runtime migration symlink refused')
        if path.is_dir():
            if stat.S_IMODE(path.stat().st_mode)!=0o755:
                raise ValueError('Runtime migration directories must be mode0755')
        elif path.name!='manifest.json' or path.parent!=destination:
            observed.add(path.relative_to(destination).as_posix())
    if observed!=expected:
        raise ValueError('Runtime migration inventory differs from admitted plan')
    if json.loads((destination/'manifest.json').read_text())!=plan:
        raise ValueError('Runtime migration manifest differs; use a fresh destination')
    for item in plan['files']:
        path=destination/item['path']
        if stat.S_IMODE(path.stat().st_mode)!=0o444 or hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:
            raise ValueError('Runtime migration content/mode differs: '+item['path'])


def stage_migrations(destination, plan):
    destination=Path(destination)
    if destination.exists():
        verify_migrations(destination,plan)
        return
    destination.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    destination.mkdir(mode=0o700)
    complete=False
    try:
        for item in plan['files']:
            source=Path(item['source'])
            data=source.read_bytes()
            if hashlib.sha256(data).hexdigest()!=item['sha256']:
                raise ValueError('Migration source changed after planning')
            target=destination/item['path']
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'wb') as output: output.write(data)
            target.chmod(0o444)
        for path in destination.rglob('*'):
            if path.is_dir(): path.chmod(0o755)
        descriptor=os.open(destination/'manifest.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w') as output: json.dump(plan,output,indent=2);output.write('\n')
        verify_migrations(destination,plan)
        complete=True
    finally:
        if not complete:
            shutil.rmtree(destination)


def quote(value):
    if any(character in value for character in ('\n','\r','\0')):
        raise ValueError('Multiline paths are not supported')
    if re.fullmatch(r'[A-Za-z0-9_./:@-]+',value):
        return value
    # Compose dotenv single-quoted values are literal, including dollar signs.
    return "'"+value.replace("'", "\\'")+"'"


def render(existing, values):
    result=[]
    seen=set()
    assignment=re.compile(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=')
    for line in existing.splitlines(keepends=True):
        match=assignment.match(line)
        key=match.group(1) if match else None
        if key in values:
            if key not in seen:
                result.append(key+'='+quote(values[key])+'\n')
                seen.add(key)
        else:
            result.append(line)
    missing=[key for key in values if key not in seen]
    if missing:
        if result and not result[-1].endswith('\n'): result[-1]+='\n'
        result.append('\n# Machine-local prepared build inputs\n')
        result.extend(key+'='+quote(values[key])+'\n' for key in missing)
    return ''.join(result)


def write(path, content):
    path=Path(path)
    if path.is_symlink():
        raise ValueError('Refusing a symlink configuration target')
    mode=stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor,temporary=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary,mode)
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repos-root',required=True,type=Path)
    parser.add_argument('--tag',default='v1-candidate')
    parser.add_argument('--provenance',required=True,type=Path,help='Passed preparation provenance.json for --repos-root')
    parser.add_argument('--runtime-migrations-root',type=Path,help='New private stage under this checkout .local; existing stages must match exactly')
    parser.add_argument('--env-file',default=ROOT/'.env.arena',type=Path)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    repos_root=args.repos_root.resolve(strict=True)
    migration_root=(args.runtime_migrations_root or ROOT.parent/'.local/twin-runtime-migrations'/repos_root.name).resolve()
    if not migration_root.is_relative_to((ROOT.parent/'.local').resolve()):
        raise ValueError('Runtime migration stage must remain under this checkout .local')
    values=settings(repos_root,args.tag,migration_root)
    plan=migration_plan(repos_root,args.provenance)
    existing=args.env_file.read_text() if args.env_file.exists() else ''
    content=render(existing,values)
    if args.check:
        print('\n'.join(key+'='+quote(value) for key,value in values.items()))
    else:
        stage_migrations(migration_root,plan)
        write(args.env_file,content)
        print('Updated '+str(args.env_file)+'; unrelated settings preserved. Restart is a separate action.')


if __name__=='__main__':
    try:
        main()
    except (OSError,ValueError) as error:
        raise SystemExit(str(error))
