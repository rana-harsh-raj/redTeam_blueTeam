"""Shared read-only audit helpers. Raw secret-bearing material stays temporary."""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
ENV = ROOT / 'ENV2_COMPOSE'
os.umask(0o077)


def arguments(description, *, build_evidence=False):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--output', type=Path, required=True, help='Directory for metadata-only JSON reports')
    parser.add_argument('--project', default='env2_compose')
    if build_evidence:
        parser.add_argument('--build-evidence', type=Path, required=True,
                            help='Existing five-service build-evidence directory; never builds images')
        parser.add_argument('--read-unused-mozart', action='store_true',
                            help='Hash the unused generated volume with an isolated read-only helper')
    args = parser.parse_args()
    def interrupted(*_):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, interrupted)
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    return args


def run(args, **kwargs):
    result = subprocess.run(args, capture_output=True, timeout=180, **kwargs)
    if result.returncode:
        # Never echo stdout/stderr: a failed reader can contain credentials or rows.
        raise RuntimeError('Audit command failed: ' + args[0])
    return result.stdout


def current_containers(project, *, include_stopped=False):
    ids = run(['docker', 'ps', '-aq' if include_stopped else '-q', '--filter',
               'label=com.docker.compose.project=' + project]).decode().split()
    if not ids:
        raise RuntimeError('No containers found for the requested project')
    rows = json.loads(run(['docker', 'inspect', *ids]))
    for row in rows:
        labels = row['Config'].get('Labels', {})
        if Path(labels.get('com.docker.compose.project.working_dir', '')).resolve() != ENV.resolve():
            raise RuntimeError('Compose project belongs to a different workspace')
    return rows


def synthetic_values():
    values = set()
    for path in (ENV / 'secrets').rglob('*'):
        if path.is_file() and path.stat().st_size < 20000:
            value = path.read_bytes().strip()
            if len(value) >= 12:
                values.add(value)
            if path.parent.name == 'verifier-bridge' and b':' in value:
                values.add(value.split(b':', 1)[1])
    return values


def clean_known(data, known):
    count = 0
    for value in sorted(set(known), key=len, reverse=True):
        if len(value) >= 12 and value in data:
            count += data.count(value)
            data = data.replace(value, b'redacted')
    return data, count


def scan_directory(directory, output_dir):
    config = output_dir / 'scanner.toml'
    config.write_text('[extend]\nuseDefault = true\n')
    ignore = output_dir / '.gitleaksignore'
    ignore.write_text('')
    report = output_dir / 'scan.json'
    process = subprocess.run(
        ['gitleaks', 'dir', str(directory), '--config', str(config),
         '--gitleaks-ignore-path', str(ignore), '--ignore-gitleaks-allow', '--redact=100',
         '--no-banner', '--report-format', 'json', '--report-path', str(report), '--timeout', '120'],
        capture_output=True, timeout=135)
    if not report.exists():
        return {'exit_code': 2, 'findings': [], 'error': 'scanner omitted its required report'}
    raw = json.loads(report.read_text())
    findings = []
    for finding in raw:
        name = Path(finding['File'])
        relative = str(name.relative_to(directory)) if name.is_absolute() else str(name)
        findings.append({'rule': finding.get('RuleID'), 'file': relative,
                         'line': finding.get('StartLine'), 'end_line': finding.get('EndLine'),
                         'match_redacted': True})
    return {'exit_code': process.returncode, 'findings': findings}


def log_endpoints(text, service, forbidden_patterns):
    """Retain all raw patterns; distinguish only narrowly known startup-help URLs."""
    raw = collections.Counter()
    unresolved = collections.Counter()
    classified = []
    for line in text.splitlines():
        for name, pattern in forbidden_patterns:
            for match in pattern.finditer(line):
                raw[name] += 1
                help_url = False
                if name == 'third_party_host':
                    if service == 'redis' and match.group(0).lower() in ('http://github.com', 'https://github.com') and 'overcommit' in line.lower():
                        help_url = bool(re.match(r'https?://github\.com/(?:redis/redis/(?:issues|discussions)/\d+|jemalloc/jemalloc/issues/1328)(?:[./\s]|$)', line[match.start():]))
                    elif service == 'mongo-cfa' and match.group(0).lower() in ('http://dochub.mongodb.org', 'https://dochub.mongodb.org') and 'startupWarnings' in line:
                        help_url = bool(re.match(r'https?://dochub\.mongodb\.org/core/[A-Za-z0-9_/-]+', line[match.start():]))
                if help_url:
                    classified.append({'rule': name, 'service': service,
                                       'classification': 'public startup diagnostic help URL; not effective service configuration',
                                       'matched_host': match.group(0)})
                else:
                    unresolved[name] += 1
    return {'raw_pattern_counts': dict(raw), 'unresolved_pattern_counts': dict(unresolved),
            'reviewed_diagnostic_matches': classified}


def classify_machinery_task_ids(data):
    """Recognize IDs only through Machinery's persisted TaskState/key relationship."""
    identifiers = set()
    for record in json.loads(data):
        if record.get('type') != 'string' or not isinstance(record.get('value'), str):
            continue
        try:
            state = json.loads(record['value'])
        except (ValueError, TypeError):
            continue
        if not isinstance(state, dict):
            continue
        value = state.get('TaskUUID')
        if (isinstance(value, str) and record.get('key') == value
                and re.fullmatch(r'(?:task_)?[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value)
                and state.get('State') in ('PENDING', 'RECEIVED', 'STARTED', 'RETRY', 'SUCCESS', 'FAILURE')
                and isinstance(state.get('TaskName'), str) and state['TaskName']
                and 'CreatedAt' in state and 'Results' in state and 'Error' in state):
            identifiers.add(value.encode())
    cleaned, occurrences = clean_known(data, identifiers)
    return cleaned, {'classification': 'Machinery task identifiers; persisted Redis key equals TaskState.TaskUUID',
                     'verified_task_identifier_count': len(identifiers), 'occurrences': occurrences,
                     'source': ['FTS go.mod: github.com/RichardKnop/machinery v1.9.1',
                                'machinery v1.9.1/v1/tasks/state.go:21',
                                'machinery v1.9.1/v1/backends/redis/redis.go:319-326',
                                'machinery v1.9.1/v1/server.go:170']}
