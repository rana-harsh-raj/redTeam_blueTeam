#!/usr/bin/env python3
"""Record a fresh, immutable isolation baseline in the exact schema the Source-to-Pay
acceptance (scripts/isolation.py) verifies: every registered worktree of the Payouts
repository (path, HEAD, porcelain status, sha256 of every tracked+untracked non-ignored
file, sha256 of `git diff HEAD --binary`) and every pre-existing Docker container
(Id, Name, Image, Created, StartedAt, RestartCount), plus the network and volume listings.

  isolation_baseline.py --repo <payouts-repo> --out <baseline.json>

Nothing is modified. No file contents, environment variables or secrets are recorded.
"""
import argparse, hashlib, json, pathlib, subprocess
from datetime import datetime, timezone

def cmd(*a):
    return subprocess.check_output(a, text=True).strip()

def digest(b):
    return hashlib.sha256(b).hexdigest()

def worktrees(repo, exclude=()):
    out = []
    listing = cmd('git', '-C', repo, 'worktree', 'list', '--porcelain')
    for block in listing.split('\n\n'):
        lines = [l for l in block.splitlines() if l.strip()]
        if not lines or not lines[0].startswith('worktree '):
            continue
        path = lines[0][len('worktree '):]
        if path in exclude:
            continue   # the checkout(s) executing the closure runs are NOT protected; everything else is
        files = {}
        for name in cmd('git', '-C', path, 'ls-files', '--cached', '--others', '--exclude-standard').splitlines():
            target = pathlib.Path(path) / name
            if target.is_file():
                files[name] = digest(target.read_bytes())
        out.append({'path': path, 'head': cmd('git', '-C', path, 'rev-parse', 'HEAD'),
                    'status': cmd('git', '-C', path, 'status', '--porcelain=v1'), 'files': files,
                    'diff_hash': digest(subprocess.check_output(['git', '-C', path, 'diff', 'HEAD', '--binary']))})
    return listing, out

def containers():
    # RUNNING containers only: scripts/isolation.py accepts a pre-existing container only while it is still running,
    # so a one-shot container that had already exited before capture (the arena's ledger-scheduler) can never
    # satisfy the gate and must not be part of the protected set. Matches the historical 2026-09-07 capture.
    ids = cmd('docker', 'ps', '-q').split()
    if not ids:
        return []
    out = []
    for v in json.loads(cmd('docker', 'inspect', *ids)):
        rec = {k: v[k] for k in ['Id', 'Name', 'Image', 'Created']}
        rec.update(StartedAt=v['State']['StartedAt'], RestartCount=v['RestartCount'])
        out.append(rec)
    return out

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--repo', required=True); ap.add_argument('--out', required=True); ap.add_argument('--exclude', action='append', default=[], help='worktree path executing the runs (not protected)')
    a = ap.parse_args()
    listing, wts = worktrees(a.repo, tuple(a.exclude))
    baseline = {'captured_at': datetime.now(timezone.utc).isoformat(), 'worktree_list': listing, 'worktrees': wts,
                'containers': containers(),
                'networks': cmd('docker', 'network', 'ls', '--format', '{{json .}}'),
                'volumes': cmd('docker', 'volume', 'ls', '--format', '{{json .}}'),
                'capture_tool': 'scripts/m7/isolation_baseline.py', 'excluded_executing_worktrees': a.exclude,
                'note': 'Captured BEFORE the runs it protects; running containers only (verifier semantics).'}
    pathlib.Path(a.out).write_text(json.dumps(baseline, indent=2) + '\n')
    print(json.dumps({'worktrees': [(w['path'], w['head'][:8], len(w['files']), bool(w['status'])) for w in wts],
                      'containers': len(baseline['containers']), 'out': a.out}, indent=1))

if __name__ == '__main__':
    main()
