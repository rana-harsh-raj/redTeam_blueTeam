#!/usr/bin/env python3
"""Prepare fresh local build copies from exact Git HEADs; never edit source clones."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import re
import subprocess
import tarfile
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[2]
REPOS = ('payouts', 'ledger', 'fts', 'cfa', 'x-balances')
# These exact files were reviewed: each hit is a source-code comment example,
# not a runtime key. Changed files or any other hit fail closed for review.
COMMENT_EXAMPLES = {
    ('ledger', 'pkg/idempotency/idempotency/repo.go'): ('91baa22f8e1a640a9bd87808dccfc7ba3bb60c9a75097c5d4cbc5cfb66d19fa2', 44),
    ('fts', 'internal/providers/encryption/sign.go'): ('ef807ab63acf5663c097f8015cb1f775e84e105b4ba76352f59f15155c3c2b1e', 24),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True).stdout


def discover():
    values = {}
    for line in (PROJECT / 'ENV2_COMPOSE/.env.arena').read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip()
    return Path(values['FTS_REPO_CONFIG_DIR']).parent.parent.resolve()


def omitted(path):
    p = PurePosixPath(path)
    if any(part in ('.git', '.github', '.circleci', '.buildkite', '.idea', '.vscode',
                    'test', 'tests', 'testdata', 'e2e', 'itf', 'func', 'functional_tests') for part in p.parts):
        return 'non-build CI/editor/test material'
    if p.name.endswith('_test.go'):
        return 'Go test source is not part of go build'
    if p.name.endswith(('_test_data.go', '_testdata.go')):
        return 'unused test-fixture globals excluded from compiled source'
    if len(p.parts) >= 2 and p.parts[:2] == ('templates', 'debezium'):
        return 'deployment event samples are not runtime build assets'
    if p.name.startswith(('.env', '.netrc', '.npmrc', '.pypirc')) or p.suffix in ('.pem', '.key', '.p12', '.pfx', '.keystore'):
        return 'credential/environment material excluded before copying'
    if p.suffix in ('.toml', '.yaml', '.yml') and ('config' in p.parts or 'configs' in p.parts or p.name.startswith('.')):
        return 'upstream runtime/deployment configuration; arena generates its own config'
    if p.name in ('.gitleaksignore', '.gitleaks.toml'):
        return 'source scanner exceptions must not suppress the fresh-copy scan'
    return None


def production_utilities(files, module):
    """Preserve utility packages imported by production, even under e2e/test paths."""
    required = set()
    pending = [p for p in files if p.endswith('.go') and omitted(p) is None]
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        text = files[path].decode(errors='replace')
        for imported in re.findall(r'"(' + re.escape(module) + r'/[^"\s]+)"', text):
            directory = imported[len(module) + 1:]
            if directory in required:
                continue
            required.add(directory)
            pending.extend(p for p in files if str(PurePosixPath(p).parent) == directory and
                           p.endswith('.go') and not p.endswith(('_test.go', '_test_data.go', '_testdata.go')))
    return required


def generate_rpc(source, target, name, report_dir):
    """Regenerate ignored RPC outputs from the clean proto HEAD and pinned local tools."""
    if name not in ('payouts', 'ledger'):
        return None
    proto = source / 'proto'
    commit = git(proto, 'rev-parse', 'HEAD').decode().strip()
    dirty = git(proto, 'status', '--porcelain').decode().splitlines()
    if dirty:
        raise RuntimeError('Proto source checkout is dirty; cannot claim exact fresh generation')
    modules = [line.strip() for line in (target / 'scripts/proto_modules').read_text().splitlines()
               if line.strip() and not line.lstrip().startswith('#') and line.strip() != 'buf.yaml']
    descriptors = {}
    with tarfile.open(fileobj=io.BytesIO(git(proto, 'archive', '--format=tar', 'HEAD'))) as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('.proto'):
                descriptors[member.name] = archive.extractfile(member).read()
    selected = [path for path in descriptors if any(path.startswith(prefix + '/') for prefix in modules)]
    if not selected:
        raise RuntimeError('No proto schemas selected for ' + name)
    work = report_dir / (name + '-proto-inputs')
    work.mkdir(mode=0o700)
    gomodcache = Path(subprocess.run(['go', 'env', 'GOMODCACHE'], text=True, capture_output=True, check=True).stdout.strip())
    google_root = gomodcache / 'github.com/grpc-ecosystem/grpc-gateway@v1.16.0/third_party/googleapis'
    pending = list(selected)
    dependencies = []
    seen = set()
    while pending:
        path = pending.pop()
        if path in seen or path.startswith('google/protobuf/'):
            continue  # Buf supplies its version-pinned well-known descriptors.
        seen.add(path)
        if path in descriptors:
            content = descriptors[path]
        else:
            dependency = google_root / path
            if not dependency.is_file():
                raise RuntimeError('Missing offline proto import: ' + path)
            content = dependency.read_bytes()
            dependencies.append({'path': path, 'source_module': 'github.com/grpc-ecosystem/grpc-gateway@v1.16.0',
                                 'sha256': hashlib.sha256(content).hexdigest()})
        output = work / path
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
        pending.extend(re.findall(r'import\s+"([^"]+)"', content.decode()))
    # Build tool versions match the service Makefile. Never mutate module cache,
    # fetch a plugin remotely, or execute an application binary on this host.
    tools_dir = PROJECT / '.local/twin-build-tools'
    tools_dir.mkdir(exist_ok=True)
    tools = {'buf': ('github.com/bufbuild/buf', 'v1.32.0' if name == 'payouts' else 'v1.28.1', './cmd/buf'),
             'protoc-gen-go': ('github.com/golang/protobuf', 'v1.5.2', './protoc-gen-go') if name == 'payouts' else
                              ('google.golang.org/protobuf', 'v1.26.0', './cmd/protoc-gen-go'),
             'protoc-gen-twirp': ('github.com/twitchtv/twirp', 'v5.10.1+incompatible' if name == 'payouts' else 'v8.0.0+incompatible', './protoc-gen-twirp')}
    tool_manifest = {}
    environment = dict(os.environ, GOPROXY='off', GOTOOLCHAIN='local', GOSUMDB='sum.golang.org',
                       GONOPROXY='none', GONOSUMDB='none', GOPRIVATE='', GOFLAGS='-mod=readonly')
    for tool, (module, version, package) in tools.items():
        binary = tools_dir / (name + '-' + tool)
        module_path = gomodcache / (module + '@' + version)
        if not module_path.is_dir():
            raise RuntimeError('Required codegen module unavailable offline: ' + str(module_path))
        if not binary.exists():
            installed = Path.home() / 'go/bin' / tool
            metadata = subprocess.run(['go', 'version', '-m', str(installed)], text=True, capture_output=True).stdout if installed.exists() else ''
            if ('\tmod\t' + module + '\t' + version + '\t') in metadata:
                shutil.copy2(installed, binary)
                built = subprocess.CompletedProcess([], 0, 'Reused installed exact-version generator after go version -m verification\n', '')
            elif (module_path / 'go.mod').exists():
                built = subprocess.run(['go', 'build', '-trimpath', '-o', str(binary), package],
                                       cwd=module_path, env=environment, text=True, capture_output=True)
            else:
                # Legacy Twirp releases predate go.mod. Pin their compiler API
                # dependencies in an isolated tools-only module, preserving sums.
                build_dir = tools_dir / (name + '-' + tool + '-module')
                build_dir.mkdir(exist_ok=True)
                (build_dir / 'go.mod').write_text('module twin.local/buildtool\n\ngo 1.26.0\n\nrequire (\n' +
                    module + ' ' + version + '\ngithub.com/pkg/errors v0.9.1\n' +
                    'github.com/golang/protobuf v1.5.2\ngoogle.golang.org/protobuf v1.26.0\n)\n')
                built = subprocess.run(['go', 'build', '-mod=mod', '-trimpath', '-o', str(binary), module + package[1:]],
                                       cwd=build_dir, env=environment, text=True, capture_output=True)
            (report_dir / (name + '-' + tool + '-build.log')).write_text(built.stdout + built.stderr)
            if built.returncode:
                raise RuntimeError('Pinned codegen tool build failed offline: ' + tool)
        tool_manifest[tool] = {'module': module, 'version': version, 'binary_sha256': sha(binary)}
    config = {'version': 'v1', 'plugins': [
        {'plugin': 'go', 'path': str(tools_dir / (name + '-protoc-gen-go')), 'out': str(target / 'rpc')},
        {'plugin': 'twirp', 'path': str(tools_dir / (name + '-protoc-gen-twirp')), 'out': str(target / 'rpc')}]}
    if name == 'payouts':
        google_packages = {}
        for dependency in dependencies:
            text = (work / dependency['path']).read_text()
            option = re.search(r'option\s+go_package\s*=\s*"([^"]+)"', text)
            if option:
                google_packages[dependency['path']] = option.group(1)
        # Vendored Google descriptors are local input rather than BSR modules;
        # preserve their own package options explicitly (the repository's BSR
        # "except" list otherwise cannot identify these offline dependencies).
        config['managed'] = {'enabled': True, 'go_package_prefix': {'default': 'github.com/razorpay/payouts/rpc'},
                             'override': {'GO_PACKAGE': google_packages}}
        for plugin in config['plugins']:
            plugin['opt'] = ['paths=source_relative']
    command = [str(tools_dir / (name + '-buf')), 'generate', str(work), '--config', '{"version":"v1"}',
               '--template', json.dumps(config)]
    for path in selected:
        command += ['--path', str(work / path)]
    generated = subprocess.run(command, cwd=report_dir, text=True, capture_output=True, env=environment)
    (report_dir / (name + '-generate-rpc.log')).write_text(generated.stdout + generated.stderr)
    if generated.returncode:
        raise RuntimeError('Offline RPC generation failed for ' + name + '; see generation log')
    return {'source_repository': 'proto', 'source_commit': commit, 'source_dirty_status': dirty,
            'selected_paths': sorted(selected), 'inputs': manifest(work), 'public_dependencies': dependencies,
            'tool_versions': tool_manifest, 'outputs': manifest(target / 'rpc'),
            'note': 'Go/Twirp outputs only; Swagger docs are not application build dependencies'}


def manifest(root):
    files = []
    for path in sorted(root.rglob('*')):
        if path.is_file() and not path.is_symlink():
            files.append({'path': path.relative_to(root).as_posix(), 'sha256': sha(path)})
    packed = json.dumps(files, separators=(',', ':'), sort_keys=True).encode()
    return {'sha256': hashlib.sha256(packed).hexdigest(), 'files': files}


def scan(root, report_dir, label):
    config = report_dir / 'scanner.toml'
    config.write_text('[extend]\nuseDefault = true\n')
    empty_ignore = report_dir / '.gitleaksignore'
    empty_ignore.write_text('')
    report = report_dir / (label + '.gitleaks.json')
    command = ['gitleaks', 'dir', str(root), '--config', str(config), '--gitleaks-ignore-path',
               str(empty_ignore), '--ignore-gitleaks-allow', '--redact=100', '--no-banner',
               '--report-format', 'json', '--report-path', str(report), '--timeout', '120']
    result = subprocess.run(command, text=True, capture_output=True)
    # Raw scanner output can contain source context. Persist only explicitly redacted
    # machine output and a metadata-only summary, never print matching lines/secrets.
    if result.returncode not in (0, 1):
        raise RuntimeError('Gitleaks scan did not complete: ' + label + ' exit ' + str(result.returncode))
    findings = json.loads(report.read_text()) if report.exists() else []
    safe = [{'rule': x.get('RuleID'), 'file': x.get('File'), 'line': x.get('StartLine'), 'end_line': x.get('EndLine'),
             'fingerprint': x.get('Fingerprint')} for x in findings]
    report.write_text(json.dumps(safe, indent=2) + '\n')
    report.chmod(0o600)
    return safe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path)
    parser.add_argument('--destination', type=Path, default=PROJECT / '.local/twin-repos')
    args = parser.parse_args()
    source = (args.source_root or discover()).resolve()
    destination = args.destination.resolve()
    if not destination.is_relative_to((PROJECT / '.local').resolve()):
        raise SystemExit('Destination must remain under this checkout .local directory')
    if destination.exists():
        raise SystemExit('Refusing to overwrite an existing staging directory: ' + str(destination))
    if not shutil.which('gitleaks'):
        raise SystemExit('gitleaks is required; no copy will be admitted without a scan')
    destination.parent.mkdir(parents=True, exist_ok=True)
    report_dir = destination.parent / ('build-evidence-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    report_dir.mkdir(mode=0o700)
    result = {'schema_version': 1, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'source_root': str(source), 'destination': str(destination), 'status': 'preparing',
              'scanner_version': subprocess.run(['gitleaks', 'version'], text=True, capture_output=True, check=True).stdout.strip(),
              'repositories': [], 'patch_files': [], 'classification': 'fresh HEAD archives plus documented arena build patches'}
    partial = Path(tempfile.mkdtemp(prefix='twin-repos-preparing-', dir=destination.parent))
    try:
        for name in REPOS:
            repo = source / name
            commit = git(repo, 'rev-parse', 'HEAD').decode().strip()
            dirty = git(repo, 'status', '--porcelain').decode().splitlines()
            entry = {'name': name, 'source_commit': commit, 'source_dirty_status': dirty,
                     'copied_worktree_changes': False, 'excluded': []}
            result['repositories'].append(entry)
            target = partial / name
            target.mkdir(mode=0o700)
            with tarfile.open(fileobj=io.BytesIO(git(repo, 'archive', '--format=tar', 'HEAD'))) as archive:
                files = {m.name: archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
                module = re.search(r'(?m)^module\s+(\S+)', files['go.mod'].decode()).group(1)
                utility_dirs = production_utilities(files, module)
                entry['production_imported_utility_directories'] = sorted(p for p in utility_dirs if any(x in PurePosixPath(p).parts for x in ('test', 'e2e', 'tests', 'testdata')))
                for member in archive:
                    path = PurePosixPath(member.name)
                    if path.is_absolute() or '..' in path.parts:
                        raise RuntimeError('Unsafe Git archive path')
                    reason = omitted(member.name)
                    if reason == 'non-build CI/editor/test material' and str(path.parent) in utility_dirs and path.suffix == '.go' and not path.name.endswith(('_test.go', '_test_data.go', '_testdata.go')):
                        reason = None
                    if not member.isfile():
                        if not member.isdir():
                            entry['excluded'].append({'path': member.name, 'reason': 'symlink/special archive member not copied'})
                        continue
                    if reason:
                        entry['excluded'].append({'path': member.name, 'reason': reason})
                        continue
                    output = target / member.name
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.extractfile(member) as incoming, output.open('wb') as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    output.chmod(0o700 if member.mode & 0o111 else 0o600)
            entry['rpc_generation'] = generate_rpc(source, target, name, report_dir)
            if name == 'ledger':
                # The real RX migration image materializes this ignored package
                # from MIGRATION_SOURCE before compilation. Use the RX source
                # selected by .github/workflows/pr_workflow.yml, not an ignored
                # mixed PG/RX folder left in a developer checkout.
                migration_source = target / 'internal/database/rx_migrations'
                migration_target = target / 'internal/database/migrations'
                if migration_target.exists():
                    raise RuntimeError('Unexpected pre-existing generated Ledger migration package')
                shutil.copytree(migration_source, migration_target)
                entry['migration_materialization'] = {'source': 'internal/database/rx_migrations',
                    'destination': 'internal/database/migrations', 'classification': 'application/runtime wiring; repository RX build step',
                    'evidence': ['build/docker/prod/Dockerfile.migration', '.github/workflows/pr_workflow.yml:309'],
                    'files': manifest(migration_target)}
            findings = scan(target, report_dir, name)
            # Findings in non-build docs may be removed; a hit in source or an asset
            # is a blocker, not silently replaced or allowlisted as a "test key".
            quarantined = []
            for finding in findings:
                path = Path(finding['file'])
                if not path.is_absolute():
                    path = target / path
                if path.suffix.lower() in ('.md', '.txt') and path.is_file() and path.is_relative_to(target):
                    relative = path.relative_to(target).as_posix()
                    path.unlink()
                    quarantined.append(relative)
                    entry['excluded'].append({'path': relative, 'reason': 'gitleaks finding in non-build documentation; not retained'})
                elif path.suffix == '.go' and path.is_file() and path.is_relative_to(target):
                    lines = path.read_text().splitlines(keepends=True)
                    start = finding['line'] - 1
                    end = start + 1
                    # Remove scanner-hit line comments only. Never redact an executable
                    # string or private key used by application code to force a pass.
                    reviewed = COMMENT_EXAMPLES.get((name, path.relative_to(target).as_posix()))
                    if reviewed == (sha(path), start + 1) and start >= 0 and end <= len(lines) and lines[start].lstrip().startswith('//'):
                        relative = path.relative_to(target).as_posix()
                        before = sha(path)
                        lines[start:end] = ['// ARENA BUILD: credential-pattern comment omitted; see build provenance.\n'] * (end - start)
                        path.write_text(''.join(lines))
                        entry.setdefault('comment_only_safety_edits', []).append({'path': relative, 'line': start + 1,
                            'before_sha256': before, 'after_sha256': sha(path), 'classification': 'safety patch; comments only; no executable behavior change'})
                        quarantined.append(relative)
            if quarantined:
                findings = scan(target, report_dir, name + '-rescan')
            entry['scanner_findings'] = findings
            entry['pristine_copy_manifest'] = manifest(target)
            print(name + ': HEAD ' + commit + ', ' + str(len(entry['pristine_copy_manifest']['files'])) +
                  ' files admitted, ' + str(len(entry['excluded'])) + ' excluded, ' + str(len(findings)) + ' unresolved scanner findings', flush=True)
        if any(r['scanner_findings'] for r in result['repositories']):
            result['status'] = 'blocked-secret-scan'
            raise RuntimeError('Necessary source/assets contain unresolved scanner findings; copies are quarantined and not build-approved')
        patches = PROJECT / 'ENV2_COMPOSE/build/arena-patches'
        result['patch_files'] = manifest(patches)['files']
        script = PROJECT / 'ENV2_COMPOSE/build/apply-arena-patches.sh'
        result['patch_script_sha256'] = sha(script)
        patch_result = subprocess.run(['bash', str(script), str(partial)], text=True, capture_output=True)
        (report_dir / 'apply-patches.log').write_text(patch_result.stdout + patch_result.stderr)
        if patch_result.returncode:
            raise RuntimeError('Documented arena patches failed; see metadata report')
        for entry in result['repositories']:
            entry['patched_copy_manifest'] = manifest(partial / entry['name'])
            original = {f['path']: f['sha256'] for f in entry['pristine_copy_manifest']['files']}
            entry['changed_by_arena_patch'] = [f['path'] for f in entry['patched_copy_manifest']['files']
                                               if original.get(f['path']) != f['sha256']]
        # Recheck all originals: this preparation must never mutate them.
        for entry in result['repositories']:
            repo = source / entry['name']
            if git(repo, 'rev-parse', 'HEAD').decode().strip() != entry['source_commit'] or \
               git(repo, 'status', '--porcelain').decode().splitlines() != entry['source_dirty_status']:
                raise RuntimeError('Original repository changed during preparation')
        partial.rename(destination)
        result['status'] = 'prepared'
        result['prepared_tree'] = str(destination)
    except Exception as exc:
        result['error'] = str(exc)
        result['quarantined_tree'] = str(partial)
        if result['status'] == 'preparing':
            result['status'] = 'blocked'
        raise
    finally:
        path = report_dir / 'provenance.json'
        path.write_text(json.dumps(result, indent=2) + '\n')
        path.chmod(0o600)
        print('Provenance: ' + str(path), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc))
