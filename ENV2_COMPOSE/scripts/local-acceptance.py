#!/usr/bin/env python3
"""Derive reports/implementation/local-acceptance.json (schema_version 2) from
named run artifacts. Nothing here is attested by hand.

Every boolean is computed from a file this script read and hashed. A check it
cannot compute is FALSE with a `reason`, never absent and never optimistic. The
producer runs entirely offline and touches no container.

Two things the schema-1 hand-authored gate conflated are now separate, per
reports/claude-review/TWIN_V1_AUDIT_AND_NEXT_STEP.md section 4:

  route_fidelity        required. For every in-scope route and profile: the
                        transport was observed AND the ending state equals
                        either the normal expected state or a SOURCE-PREDICTED
                        failure declared in TWIN_SPEC/expected-failures.yaml.
  product_route_health  informational. Did the business route complete? Shared
                        Kafka is false with its EF citation. This is the number
                        a red-agent judge later consumes.

Usage:
    python3 ENV2_COMPOSE/scripts/local-acceptance.py MANIFEST.json \
        [--output reports/implementation/local-acceptance.json] [--summary PATH]

Exit codes: 0 gate passed, 1 gate blocked, 2 manifest or I/O error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

SCHEMA_VERSION = 2
ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / 'ENV2_COMPOSE'

_spec = importlib.util.spec_from_file_location(
    'arena_fingerprint', Path(__file__).with_name('fingerprint.py'))
fingerprint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fingerprint)

# Order is the report order; every one of these must be true to freeze.
REQUIRED_CHECKS = (
    'fresh_build',
    'empty_volume_boot',
    'runtime_endpoint_audit',
    'secret_scan',
    'egress_same_window',
    'verifier_suite',
    'logical_replay',
    'route_fidelity',
    'resource_profile',
    'explorer_saved_and_live',
    'staleness',
    'declared_deviations_present',
    'fixed_twin_defects_recorded',
    'no_unexplained_results',
)

VERIFIER_CASE_COUNT = 26
CORE_IMAGE_SERVICES = ('payouts', 'ledger', 'fts', 'cfa', 'x-balances')
AUDIT_OK = ('passed', 'passed_with_caveats')
PLACEHOLDER = re.compile(r'PLACEHOLDER|TODO|FIXME|XXX', re.IGNORECASE)

ROUTE_CASES = ('direct_success', 'scheduled', 'queued', 'returned', 'failed_direct', 'failed_shared')
BANK_CASES = ('hold', 'delayed_success', 'ambiguous_with_utr', 'ambiguous_without_utr',
              'duplicate', 'timeout')
KAFKA_CASES = ('direct_after_shared', 'failed_dropped_direct', 'failed_dropped_shared',
               'reversed_dropped_direct')

# (profile, scenario, manifest role, subdirectory under the role, kind)
REQUIRED_SCENARIOS = tuple(
    [('monolith', case, 'final_route', '', 'route') for case in ROUTE_CASES] +
    [('monolith', case, 'final_bank', '', 'bank') for case in BANK_CASES] +
    [('monolith', 'golden', 'final_golden', '', 'golden'),
     ('direct', 'golden', 'profile_direct_golden', '', 'golden'),
     ('direct-create-monolith-status', 'golden', 'profile_mixed_golden', '', 'golden'),
     ('kafka', 'golden-shared', 'profile_kafka', 'golden-shared', 'golden')] +
    [('kafka', case, 'profile_kafka', 'kafka', 'kafka') for case in KAFKA_CASES]
)

# Run directories whose egress capture must bracket their own scenario window.
EGRESS_TARGETS = (
    ('replay_a', 'full'), ('replay_b', 'full'),
    ('final_route', ''), ('final_bank', ''), ('final_golden', ''),
    ('profile_direct_golden', ''), ('profile_mixed_golden', ''),
    ('profile_kafka', 'golden-shared'), ('profile_kafka', 'kafka'),
)

# Roles that must carry a boot fingerprint identical to the current tree.
FINGERPRINT_ROLES = ('replay_a', 'replay_b', 'final_route', 'final_bank', 'final_golden',
                     'profile_direct_golden', 'profile_mixed_golden', 'profile_kafka')

# Which route profile each run role was booted under.
ROLE_PROFILE = {
    'replay_a': 'monolith', 'replay_b': 'monolith',
    'final_route': 'monolith', 'final_bank': 'monolith', 'final_golden': 'monolith',
    'profile_direct_golden': 'direct',
    'profile_mixed_golden': 'direct-create-monolith-status',
    'profile_kafka': 'kafka',
}


class ManifestError(Exception):
    """Manifest or I/O problem: the gate cannot even be attempted (exit 2)."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def epoch(value):
    """Accept an epoch float or an ISO-8601 timestamp; return a float or None."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value:
        text = value.replace('Z', '+00:00')
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.timestamp()
    return None


def dig(obj, *keys, default=None):
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key)
    return default if obj is None else obj


def unresolved_findings(obj) -> int:
    """Sum every `*unresolved*` counter/list anywhere in an audit report."""
    total = 0
    if isinstance(obj, dict):
        for key, value in obj.items():
            if 'unresolved' in key.lower():
                if isinstance(value, bool):
                    total += int(value)
                elif isinstance(value, (int, float)):
                    total += int(value)
                elif isinstance(value, list):
                    total += len(value)
                elif isinstance(value, dict):
                    total += sum(int(v) for v in value.values() if isinstance(v, (int, float)))
            else:
                total += unresolved_findings(value)
    elif isinstance(obj, list):
        for item in obj:
            total += unresolved_findings(item)
    return total


def state_from_layers(layers: dict) -> dict:
    """The four fields TWIN_SPEC/expected-failures.yaml predicts, from a snapshot."""
    transfers = dig(layers, 'fts', 'transfers', default=[]) or []
    journals = dig(layers, 'ledger', 'journals', default=[]) or []
    deliveries = dig(layers, 'merchant_webhooks', 'deliveries', default=[]) or []
    return {
        'payout_status': dig(layers, 'payouts', 'row', 'status'),
        'fts_status': transfers[-1].get('status') if transfers else None,
        'journal_events': sorted({j.get('transactor_event') for j in journals
                                  if j.get('transactor_event')}),
        'webhook_events': sorted({dig(d, 'body', 'event') for d in deliveries
                                  if dig(d, 'body', 'event')}),
    }


def citation_covers(cited, entry) -> bool:
    """A runner may cite the registry id or any file:line token of the entry's source_refs."""
    if not cited:
        return False
    cited = str(cited)
    if cited == entry.get('id'):
        return True
    for ref in entry.get('source_refs') or []:
        tokens = [t for t in re.split(r'[\s;,()]+', str(ref)) if ':' in t and '.' in t]
        if tokens and all(t in cited for t in tokens):
            return True
    return False


def state_matches(predicted: dict, observed: dict):
    """Compare a registry prediction against an observed state; return (bool, mismatches)."""
    mismatches = []
    for field in ('payout_status', 'fts_status'):
        want = predicted.get(field)
        if want is None:
            continue
        got = observed.get(field)
        if got != want:
            mismatches.append('%s: predicted %r, observed %r' % (field, want, got))
    for field, observed_key in (('journals', 'journal_events'), ('webhooks', 'webhook_events')):
        rule = predicted.get(field) or {}
        seen = set(observed.get(observed_key) or [])
        for name in rule.get('present') or []:
            if name not in seen:
                mismatches.append('%s: predicted present %r, not observed' % (field, name))
        for name in rule.get('absent') or []:
            if name in seen:
                mismatches.append('%s: predicted absent %r, but observed' % (field, name))
    return not mismatches, mismatches


class Gate:
    def __init__(self, manifest: dict, manifest_path: Path):
        self.manifest = manifest
        self.manifest_path = manifest_path
        self.evidence: dict[str, str] = {}
        self.checks: dict[str, bool] = {}
        self.details: dict[str, dict] = {}
        self.fingerprints: dict[str, dict] = {}   # role -> {'path','data'}
        self.scenarios: list[dict] = []
        self.unexplained: list[dict] = []
        self.registry: dict[str, dict] = {}       # EF id -> entry
        self.matched_failures: dict[str, list] = {}
        self.fixed_defects: list[dict] = []
        self.fixed_defect_runs: dict[str, str] = {}   # run path -> defect id
        self.deviations: dict = {}
        self.current_hashes = fingerprint.config_hashes(COMPOSE)
        self.current_digest = fingerprint.config_digest(self.current_hashes)

    # ---------------------------------------------------------------- paths

    def rel(self, path: Path) -> str:
        path = Path(path).resolve()
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError:
            return str(path)

    def role_path(self, role: str):
        """Resolve a manifest role to an existing path inside the checkout, or None."""
        raw = self.manifest.get(role)
        if not raw or not isinstance(raw, str):
            return None
        path = Path(raw)
        path = (path if path.is_absolute() else ROOT / path).resolve()
        if not path.is_relative_to(ROOT):
            raise ManifestError('Manifest role %r points outside the checkout: %s' % (role, raw))
        return path if path.exists() else None

    def record(self, path: Path) -> str:
        key = self.rel(path)
        if key not in self.evidence:
            self.evidence[key] = sha256_file(path)
        return key

    def read_json(self, path: Path):
        self.record(path)
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            raise ManifestError('Unreadable JSON %s: %s' % (self.rel(path), exc)) from None

    def read_yaml(self, path: Path):
        self.record(path)
        try:
            import yaml
        except ImportError:
            raise ManifestError('PyYAML is required to read %s' % self.rel(path)) from None
        try:
            return yaml.safe_load(path.read_text())
        except (OSError, ValueError) as exc:
            raise ManifestError('Unreadable YAML %s: %s' % (self.rel(path), exc)) from None

    def read_text(self, path: Path) -> str:
        self.record(path)
        return path.read_text(errors='replace')

    # --------------------------------------------------------------- checks

    def result(self, name, passed, reason=None, artifacts=(), observed=None):
        self.checks[name] = bool(passed)
        self.details[name] = {
            'passed': bool(passed),
            'reason': None if passed else (reason or 'check failed'),
            'artifacts': sorted(set(artifacts)),
            'observed': observed if observed is not None else {},
        }
        return bool(passed)

    # -------------------------------------------------------- fingerprints

    def load_fingerprints(self):
        """Every required run directory must carry the fingerprint of its boot."""
        missing = []
        for role in FINGERPRINT_ROLES:
            base = self.role_path(role)
            if base is None:
                missing.append('%s (role absent from manifest or path missing)' % role)
                continue
            candidates = [base / 'arena-fingerprint.json']
            if base.is_dir():
                candidates += sorted(p / 'arena-fingerprint.json' for p in base.iterdir()
                                     if p.is_dir())
            found = next((p for p in candidates if p.is_file()), None)
            if found is None:
                missing.append('%s (%s: no arena-fingerprint.json)' % (role, self.rel(base)))
                continue
            self.fingerprints[role] = {'path': self.rel(found), 'data': self.read_json(found)}
        return missing

    def audit_boot_match(self, audit):
        """Does the audit's recorded boot identity equal one of the replay boots?"""
        if not audit:
            return False, 'no audit-summary.json'
        recorded = {}
        for key in ('initial_service_container_ids', 'final_service_container_ids'):
            recorded.update(audit.get(key) or {})
        if not recorded:
            return False, 'audit-summary.json records no service container ids'
        for role in ('replay_a', 'replay_b', 'final_golden', 'final_route', 'final_bank'):
            entry = self.fingerprints.get(role)
            if not entry:
                continue
            boot = entry['data'].get('service_container_ids') or {}
            shared = set(boot) & set(recorded)
            if shared and all(boot[s] == recorded[s] for s in shared):
                return True, 'matches %s boot %s on %d services' % (
                    role, entry['data'].get('boot_id'), len(shared))
        return False, ('audit boot identity matches no required run fingerprint '
                       '(fingerprints available: %s)' % (sorted(self.fingerprints) or 'none'))

    # ------------------------------------------------------------ artifacts

    def find_file(self, base: Path, name: str, subdir: str = ''):
        if base is None:
            return None
        target = base / subdir if subdir else base
        candidate = target / name
        if candidate.is_file():
            return candidate
        if not subdir and target.is_dir():
            nested = sorted(p / name for p in target.iterdir() if p.is_dir())
            found = [p for p in nested if p.is_file()]
            if len(found) == 1:
                return found[0]
        return None

    def route_evidence(self, directory: Path, scenario: str, payout_id):
        """Per-case file first, then the shared one when its payout id agrees."""
        specific = directory / ('route-evidence-%s.json' % scenario)
        if specific.is_file():
            return self.read_json(specific), self.rel(specific), None
        shared = directory / 'route-evidence.json'
        if shared.is_file():
            data = self.read_json(shared)
            if payout_id and data.get('payout_id') not in (None, payout_id):
                return None, self.rel(shared), (
                    'route-evidence.json records payout %s, not %s; write '
                    'route-evidence-%s.json for this case'
                    % (data.get('payout_id'), payout_id, scenario))
            return data, self.rel(shared), None
        return None, None, ('missing evidence: no route-evidence-%s.json or route-evidence.json '
                            'in %s' % (scenario, self.rel(directory)))


def window_events(directory: Path, gate: Gate):
    """Every scenario timestamp in a run directory, for the egress-window bracket."""
    events = []
    junit = directory / 'junit.xml'
    if junit.is_file():
        try:
            suite = ET.fromstring(gate.read_text(junit)).find('testsuite')
        except ET.ParseError:
            suite = None
        if suite is not None:
            start = epoch(suite.get('timestamp'))
            if start is not None:
                events.append(('junit.start', start))
                try:
                    events.append(('junit.end', start + float(suite.get('time') or 0)))
                except ValueError:
                    pass
    for name in ('live-result.json', 'bank-effects.json', 'route-results.json',
                 'kafka-results.json'):
        path = directory / name
        if not path.is_file():
            continue
        data = gate.read_json(path)
        rows = data.get('scenarios') or data.get('cases') or [data]
        for row in ([data] + list(rows)) if isinstance(rows, list) else [data]:
            if not isinstance(row, dict):
                continue
            for key in ('started_at', 'finished_at', 'observed_at'):
                value = epoch(row.get(key))
                if value is not None:
                    events.append(('%s.%s' % (name, key), value))
            value = epoch(dig(row, 'ending_state', 'finished_at'))
            if value is not None:
                events.append(('%s.ending_state.finished_at' % name, value))
    for path in sorted(directory.glob('*.jsonl')):
        try:
            lines = [line for line in gate.read_text(path).splitlines() if line.strip()]
        except OSError:
            continue
        for label, line in (('first', lines[0]), ('last', lines[-1])) if lines else ():
            try:
                value = epoch(json.loads(line).get('at'))
            except ValueError:
                value = None
            if value is not None:
                events.append(('%s.%s' % (path.name, label), value))
    return events


def dropped_packets(egress: dict):
    """Kernel drops from both tcpdump summaries; None when a summary is absent."""
    counts = {}
    for stem in ('outside', 'control'):
        stats = egress.get(stem + '_capture_stats')
        if not isinstance(stats, str):
            return None
        match = re.search(r'(\d+) packets dropped by kernel', stats)
        if not match:
            return None
        counts[stem] = int(match.group(1))
    return counts


def junit_counts(gate: Gate, path: Path):
    suite = ET.fromstring(gate.read_text(path)).find('testsuite')
    if suite is None:
        raise ValueError('no <testsuite> element')
    counts = {key: int(suite.get(key) or 0)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    bad = []
    for case in suite.iter('testcase'):
        for kind in ('failure', 'error', 'skipped'):
            if case.find(kind) is not None:
                bad.append({'test': case.get('name'), 'outcome': kind})
    return counts, bad


# --------------------------------------------------------------------------
# Individual checks
# --------------------------------------------------------------------------

def check_fresh_build(gate: Gate):
    base = gate.role_path('build_evidence')
    if base is None:
        return gate.result('fresh_build', False,
                           'missing evidence: manifest role build_evidence is absent or its path '
                           'does not exist')
    artifacts, observed, reasons = [], {}, []
    needed = {'build-results.json': None, 'post-build-verification.json': None,
              'provenance.json': None}
    for name in list(needed):
        path = base / name
        if not path.is_file():
            reasons.append('missing evidence: %s/%s' % (gate.rel(base), name))
            continue
        needed[name] = gate.read_json(path)
        artifacts.append(gate.rel(path))
    if reasons:
        return gate.result('fresh_build', False, '; '.join(reasons), artifacts, observed)

    builds = needed['build-results.json'].get('builds') or []
    images = {}
    for row in builds:
        if row.get('exit_code') != 0:
            reasons.append('build %s exited %s' % (row.get('service'), row.get('exit_code')))
        if row.get('candidate_image'):
            images[str(row.get('service')).replace('-', '')] = row['candidate_image']
    # A recorded permission/asset rebuild supersedes the first candidate id for that
    # service; the current-boot audit applies the same override (current_boot.py).
    for service in ('payouts', 'cfa'):
        override = base / (service + '-asset-readability.json')
        if override.is_file():
            data = gate.read_json(override)
            if data.get('image_id'):
                images[service] = data['image_id']
                artifacts.append(gate.rel(override))
    if not builds:
        reasons.append('build-results.json lists no builds')
    observed['built_services'] = sorted(images)
    observed['build_tag'] = needed['build-results.json'].get('tag')

    clones = needed['post-build-verification.json'].get('source_clones') or []
    if not clones:
        reasons.append('post-build-verification.json lists no source clones')
    for clone in clones:
        if not (clone.get('commit_unchanged') and clone.get('dirty_status_unchanged')):
            reasons.append('source clone %s changed during the build' % clone.get('repository'))
    observed['source_clones_unchanged'] = len(clones)

    provenance = needed['provenance.json']
    if provenance.get('status') not in ('prepared', 'passed'):
        reasons.append('provenance status is %r' % provenance.get('status'))
    observed['provenance_status'] = provenance.get('status')
    observed['provenance_commits'] = {r.get('name'): r.get('source_commit')
                                      for r in provenance.get('repositories') or []}

    # The images the runs actually booted must be the images this build produced.
    mismatches = {}
    for role, entry in sorted(gate.fingerprints.items()):
        recorded = entry['data'].get('images') or {}
        if not recorded:
            reasons.append('%s fingerprint recorded no image ids' % role)
            continue
        artifacts.append(entry['path'])
        # Only the five core service images are gated. A compose-built substitute
        # with no local tag is recorded in the fingerprint and ignored here.
        for service in CORE_IMAGE_SERVICES:
            key = service.replace('-', '')
            name = next((n for n in recorded if n.split(':')[0] == 'rzp-arena/' + key), None)
            if name is None:
                mismatches['%s/%s' % (role, key)] = 'image not referenced by the compose file'
            elif not recorded[name]:
                mismatches['%s/%s' % (role, key)] = 'fingerprint recorded no id for this image'
            elif key not in images:
                mismatches['%s/%s' % (role, key)] = 'no build result for this service'
            elif recorded[name] != images[key]:
                mismatches['%s/%s' % (role, key)] = 'booted %s, built %s' % (
                    recorded[name], images[key])
    if not gate.fingerprints:
        reasons.append('no run fingerprints available to bind images to this build')
    if mismatches:
        reasons.append('booted image ids differ from build results: '
                       + json.dumps(mismatches, sort_keys=True))
    observed['image_mismatches'] = mismatches
    return gate.result('fresh_build', not reasons, '; '.join(reasons), artifacts, observed)


def check_empty_volume_boot(gate: Gate):
    artifacts, observed, reasons = [], {}, []
    for role in ('replay_a', 'replay_b'):
        base = gate.role_path(role)
        if base is None:
            reasons.append('missing evidence: role %s' % role)
            continue
        entry = {}
        for stage in ('down', 'up'):
            log, exit_file = base / (stage + '.log'), base / (stage + '.exit')
            if not log.is_file() or not exit_file.is_file():
                reasons.append('%s: missing %s.log or %s.exit in %s'
                               % (role, stage, stage, gate.rel(base)))
                continue
            artifacts += [gate.rel(log), gate.rel(exit_file)]
            code = gate.read_text(exit_file).strip()
            entry[stage + '_exit'] = code
            if code != '0':
                reasons.append('%s: %s.sh exited %s' % (role, stage, code or '(empty)'))
            if stage == 'up':
                text = gate.read_text(log)
                generated = re.search(r'Generated (\d+) files', text)
                entry['fixture_files_generated'] = int(generated.group(1)) if generated else 0
                entry['config_generated'] = 'config/generate.py' in text
                if not generated:
                    reasons.append('%s: up.log shows no fixture generation' % role)
                if not entry['config_generated']:
                    reasons.append('%s: up.log shows no config/generate.py step' % role)
        observed[role] = entry
    return gate.result('empty_volume_boot', not reasons, '; '.join(reasons), artifacts, observed)


def audit_reports(gate: Gate):
    """(summary, {reader: (row, report dict)}, artifacts) for the audit directory."""
    base = gate.role_path('audit_dir')
    if base is None:
        return None, {}, []
    path = base / 'audit-summary.json'
    if not path.is_file():
        return None, {}, []
    summary = gate.read_json(path)
    artifacts = [gate.rel(path)]
    reports = {}
    for row in summary.get('steps') or []:
        report_path = base / str(row.get('report'))
        data = None
        if report_path.is_file():
            data = gate.read_json(report_path)
            artifacts.append(gate.rel(report_path))
        reports[row.get('reader')] = (row, data)
    return summary, reports, artifacts


def check_audits(gate: Gate, summary, reports, artifacts, boot_matched, boot_reason):
    groups = {'runtime_endpoint_audit': ('current_boot.py',),
              'secret_scan': ('logical_stores.py', 'remaining_volumes.py')}
    for name, readers in groups.items():
        reasons, observed = [], {'boot_identity': boot_reason}
        if summary is None:
            gate.result(name, False,
                        'missing evidence: audit_dir/audit-summary.json is absent', artifacts)
            continue
        if summary.get('status') not in AUDIT_OK:
            reasons.append('audit-summary status is %r' % summary.get('status'))
        if summary.get('same_running_boot') is not True:
            reasons.append('audit did not observe a single stable boot')
        observed['audit_status'] = summary.get('status')
        for reader in readers:
            row, data = reports.get(reader, (None, None))
            if row is None:
                reasons.append('missing evidence: audit step %s' % reader)
                continue
            if row.get('exit_code') != 0 or row.get('status') not in AUDIT_OK:
                reasons.append('%s exited %s with status %r'
                               % (reader, row.get('exit_code'), row.get('status')))
            if data is None:
                reasons.append('missing evidence: report %s for %s' % (row.get('report'), reader))
                continue
            count = unresolved_findings(data)
            observed[reader] = {'status': row.get('status'), 'unresolved_findings': count}
            if count:
                reasons.append('%s reports %d unresolved finding(s)' % (reader, count))
        if not boot_matched:
            reasons.append('boot identity: ' + boot_reason)
        gate.result(name, not reasons, '; '.join(reasons), artifacts, observed)


def check_egress(gate: Gate):
    artifacts, observed, reasons = [], {}, []
    for role, subdir in EGRESS_TARGETS:
        base = gate.role_path(role)
        label = role + ('/' + subdir if subdir else '')
        if base is None:
            reasons.append('missing evidence: role %s' % role)
            continue
        path = gate.find_file(base, 'egress.json', subdir)
        if path is None:
            reasons.append('missing evidence: no egress.json under %s' % label)
            continue
        artifacts.append(gate.rel(path))
        data = gate.read_json(path)
        entry = {'status': data.get('status'), 'command_exit_code': data.get('command_exit_code'),
                 'outside_packets': data.get('outside_packets')}
        if data.get('status') != 'passed':
            reasons.append('%s: egress status %r' % (label, data.get('status')))
        if data.get('command_exit_code') != 0:
            # A golden run whose ending state is a declared, source-predicted failure exits 1
            # by design (run-golden.py returns the scenario status). Accept exactly that case,
            # and only when the registry declares it for this profile/scenario.
            live = gate.find_file(base, 'live-result.json', subdir)
            expected = (live is not None and gate.read_json(live).get('status') == 'failed'
                        and registry_for(gate, ROLE_PROFILE.get(role, ''), subdir or 'golden') is not None)
            entry['expected_failure_exit'] = bool(expected)
            if not expected:
                reasons.append('%s: audited command exited %r' % (label, data.get('command_exit_code')))
        if data.get('outside_packets') != 0:
            reasons.append('%s: %r packet(s) left the arena subnets'
                           % (label, data.get('outside_packets')))
        drops = dropped_packets(data)
        entry['dropped'] = drops
        if drops is None:
            reasons.append('%s: capture statistics missing, kernel drops unknown' % label)
        elif any(drops.values()):
            reasons.append('%s: tcpdump dropped %s packet(s); the window is incomplete'
                           % (label, drops))
        started, finished = epoch(data.get('command_started_at')), epoch(data.get('command_finished_at'))
        events = window_events(path.parent, gate)
        entry['scenario_events'] = len(events)
        if started is None or finished is None:
            reasons.append('%s: egress.json records no command window' % label)
        elif not events:
            reasons.append('%s: no scenario timestamps found to bracket' % label)
        else:
            first = min(events, key=lambda e: e[1])
            last = max(events, key=lambda e: e[1])
            entry['window'] = {'command': [data.get('command_started_at'),
                                           data.get('command_finished_at')],
                               'earliest_event': first[0], 'latest_event': last[0]}
            if first[1] < started:
                reasons.append('%s: %s happened %.3fs before the capture window opened'
                               % (label, first[0], started - first[1]))
            if last[1] > finished:
                reasons.append('%s: %s happened %.3fs after the capture window closed'
                               % (label, last[0], last[1] - finished))
        observed[label] = entry
    return gate.result('egress_same_window', not reasons, '; '.join(reasons), artifacts, observed)


def check_verifier_suite(gate: Gate):
    artifacts, observed, reasons = [], {}, []
    for role in ('replay_a', 'replay_b'):
        base = gate.role_path(role)
        if base is None:
            reasons.append('missing evidence: role %s' % role)
            continue
        path = gate.find_file(base, 'junit.xml')
        if path is None:
            reasons.append('missing evidence: no junit.xml under %s' % gate.rel(base))
            continue
        artifacts.append(gate.rel(path))
        try:
            counts, bad = junit_counts(gate, path)
        except (ET.ParseError, ValueError) as exc:
            reasons.append('%s: unreadable junit.xml (%s)' % (role, exc))
            continue
        observed[role] = counts
        if counts['tests'] != VERIFIER_CASE_COUNT:
            reasons.append('%s: %d verifier cases, expected %d'
                           % (role, counts['tests'], VERIFIER_CASE_COUNT))
        for key in ('failures', 'errors', 'skipped'):
            if counts[key]:
                reasons.append('%s: %d %s' % (role, counts[key], key))
        for item in bad:
            gate.unexplained.append({
                'kind': 'verifier_case', 'profile': 'monolith', 'scenario': item['test'],
                'status': item['outcome'], 'run_dir': gate.rel(base),
                'reason': 'verifier case did not pass and maps to no expected failure'})
    return gate.result('verifier_suite', not reasons, '; '.join(reasons), artifacts, observed)


def check_logical_replay(gate: Gate):
    path = gate.role_path('logical_replay')
    if path is None or not path.is_file():
        return gate.result('logical_replay', False,
                           'missing evidence: manifest role logical_replay is absent or not a file')
    data = gate.read_json(path)
    reasons = []
    if data.get('status') != 'passed':
        reasons.append('comparison status is %r' % data.get('status'))
    if data.get('logical_equivalence') is not True:
        reasons.append('logical_equivalence is %r' % data.get('logical_equivalence'))
    differences = data.get('differences')
    if differences:
        reasons.append('%d recorded difference(s)' % len(differences))
    for side in ('first', 'second'):
        side_data = data.get(side) or {}
        if side_data.get('valid') is not True:
            reasons.append('%s input is not valid' % side)
        if side_data.get('validation_errors'):
            reasons.append('%s reports %d validation error(s)'
                           % (side, len(side_data['validation_errors'])))
    observed = {'status': data.get('status'), 'differences': len(differences or []),
                'input_directories': data.get('input_directories'),
                'independent_directories': data.get('independent_directories')}
    return gate.result('logical_replay', not reasons, '; '.join(reasons), [gate.rel(path)], observed)


def check_resource_profile(gate: Gate):
    path = gate.role_path('resource_profile')
    if path is None or not path.is_file():
        return gate.result('resource_profile', False,
                           'missing evidence: manifest role resource_profile is absent or not a file')
    data = gate.read_json(path)
    reasons, observed = [], {}
    if data.get('schema_version') != 1:
        reasons.append('unsupported profile schema %r' % data.get('schema_version'))
    for phase in ('idle', 'successful_payout', 'full_verifier'):
        row = dig(data, 'phases', phase, default={}) or {}
        observed[phase] = {k: row.get(k) for k in
                           ('observed', 'sample_count', 'peak_cpu_cores', 'peak_memory_gib', 'disk_gib')}
        if row.get('observed') is not True:
            reasons.append('phase %s was not observed' % phase)
        if (row.get('sample_count') or 0) < 2:
            reasons.append('phase %s has %r sample(s), need >= 2' % (phase, row.get('sample_count')))
        for key in ('peak_cpu_cores', 'peak_memory_gib', 'disk_gib'):
            value = row.get(key)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                reasons.append('phase %s has no positive %s' % (phase, key))
    return gate.result('resource_profile', not reasons, '; '.join(reasons), [gate.rel(path)], observed)


def check_explorer(gate: Gate):
    artifacts, reasons, observed = [], [], {}
    path = gate.role_path('explorer_verification')
    if path is None or not path.is_file():
        reasons.append('missing evidence: manifest role explorer_verification')
    else:
        artifacts.append(gate.rel(path))
        data = gate.read_json(path)
        observed['saved'] = {'saved_status': data.get('saved_status'),
                             'static': data.get('static'), 'live_server': data.get('live_server')}
        if data.get('saved_status') != 'passed':
            reasons.append('explorer saved run status is %r' % data.get('saved_status'))
        for side in ('static', 'live_server'):
            row = data.get(side) or {}
            if row.get('payout_visible') is not True:
                reasons.append('explorer %s did not show the payout' % side)
            if (row.get('verified_steps') or 0) <= 0:
                reasons.append('explorer %s verified no steps' % side)
    golden = gate.role_path('final_golden')
    live = gate.find_file(golden, 'live-result.json') if golden else None
    if live is None:
        reasons.append('missing evidence: final_golden live-result.json')
    else:
        artifacts.append(gate.rel(live))
        data = gate.read_json(live)
        observed['live_result'] = {'status': data.get('status'), 'passed': data.get('passed'),
                                   'route_profile': data.get('route_profile')}
        if data.get('status') != 'passed' or data.get('passed') is not True:
            reasons.append('final_golden live result is %r' % data.get('status'))
    return gate.result('explorer_saved_and_live', not reasons, '; '.join(reasons), artifacts, observed)


def check_staleness(gate: Gate, missing_fingerprints, boot_matched, boot_reason):
    reasons, observed = [], {'current_config_digest': gate.current_digest,
                             'boot_identity': boot_reason}
    if missing_fingerprints:
        reasons.append('missing evidence: no arena-fingerprint.json for ' +
                       ', '.join(missing_fingerprints))
    boots, mismatched = {}, {}
    for role, entry in sorted(gate.fingerprints.items()):
        data = entry['data']
        boots[role] = {'path': entry['path'], 'boot_id': data.get('boot_id'),
                       'route_profile': data.get('route_profile'),
                       'config_digest': data.get('config_digest'),
                       'git_head': data.get('git_head')}
        recorded = data.get('config_hashes') or {}
        if data.get('config_digest') == gate.current_digest and recorded:
            continue
        differing = sorted(
            set(recorded) | set(gate.current_hashes),
            key=str)
        files = [name for name in differing
                 if recorded.get(name) != gate.current_hashes.get(name)]
        mismatched[role] = {'count': len(files), 'files': files[:40]}
        reasons.append('%s was booted from a different tree (%d file(s) differ)'
                       % (role, len(files)))
    observed['boots'] = boots
    observed['mismatched_files'] = mismatched
    if not boot_matched:
        reasons.append('boot identity: ' + boot_reason)
    return gate.result('staleness', not reasons, '; '.join(reasons),
                       [e['path'] for e in gate.fingerprints.values()], observed)


# --------------------------------------------------------------------------
# Expected failures, declared deviations, fixed twin defects
# --------------------------------------------------------------------------

def load_registry(gate: Gate):
    path = gate.role_path('expected_failures')
    if path is None or not path.is_file():
        return ['missing evidence: manifest role expected_failures is absent or not a file']
    data = gate.read_yaml(path)
    entries = (data or {}).get('expected_failures') if isinstance(data, dict) else data
    if not isinstance(entries, list) or not entries:
        return ['expected-failures registry is empty or malformed: %s' % gate.rel(path)]
    problems = []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get('id'):
            problems.append('registry entry without an id')
            continue
        if not entry.get('source_refs'):
            problems.append('%s has no source_refs' % entry['id'])
        gate.registry[entry['id']] = entry
    return problems


def registry_for(gate: Gate, profile, scenario):
    for entry in gate.registry.values():
        scope = entry.get('scope') or {}
        if scope.get('profile') and scope['profile'] != profile:
            continue
        cases = scope.get('cases') or ([scope['case']] if scope.get('case') else [])
        if scenario in cases:
            return entry
    return None


def load_fixed_defects(gate: Gate):
    path = gate.role_path('fixed_twin_defects')
    if path is None or not path.is_file():
        return gate.result('fixed_twin_defects_recorded', False,
                           'missing evidence: manifest role fixed_twin_defects is absent or not a file')
    data = gate.read_json(path)
    defects = data.get('defects') if isinstance(data, dict) else data
    reasons = []
    if not isinstance(defects, list) or not defects:
        return gate.result('fixed_twin_defects_recorded', False,
                           'fixed-twin-defects list is empty; a freeze must name the twin defects '
                           'it corrected', [gate.rel(path)])
    if isinstance(data, dict) and data.get('placeholder'):
        reasons.append('the file is still marked placeholder: true')
    for defect in defects:
        if not isinstance(defect, dict):
            reasons.append('malformed defect entry')
            continue
        gate.fixed_defects.append(defect)
        missing = [k for k in ('id', 'title', 'location', 'symptom', 'fix', 'evidence_run')
                   if not defect.get(k)]
        if missing:
            reasons.append('%s is missing %s' % (defect.get('id', '?'), ', '.join(missing)))
        incomplete = [k for k in ('fix', 'evidence_run')
                      if isinstance(defect.get(k), str) and PLACEHOLDER.search(defect[k])]
        if incomplete:
            reasons.append('%s still has placeholder %s' % (defect.get('id'), ', '.join(incomplete)))
            continue
        for run in [defect.get('evidence_run')] + [s.get('run') for s in
                                                   defect.get('supersedes') or []]:
            if isinstance(run, str) and run and not PLACEHOLDER.search(run):
                gate.fixed_defect_runs[run.strip('/')] = defect.get('id')
    return gate.result('fixed_twin_defects_recorded', not reasons, '; '.join(reasons),
                       [gate.rel(path)], {'count': len(defects),
                                          'ids': [d.get('id') for d in defects if isinstance(d, dict)]})


def load_deviations(gate: Gate):
    path = gate.role_path('declared_deviations')
    if path is None or not path.is_file():
        gate.deviations = {'count': 0, 'entries': [], 'source': None}
        return gate.result('declared_deviations_present', False,
                           'missing evidence: manifest role declared_deviations is absent or not '
                           'a file; the gate must carry the fidelity ceiling with it')
    data = gate.read_yaml(path)
    entries = data
    if isinstance(data, dict):
        for key in ('declared_deviations', 'deviations', 'entries'):
            if isinstance(data.get(key), list):
                entries = data[key]
                break
    if not isinstance(entries, list) or not entries:
        gate.deviations = {'count': 0, 'entries': [], 'source': gate.rel(path)}
        return gate.result('declared_deviations_present', False,
                           'declared-deviations registry is empty or malformed: %s' % gate.rel(path),
                           [gate.rel(path)])
    normalised, reasons = [], []
    for entry in entries:
        if not isinstance(entry, dict) or not entry.get('id'):
            reasons.append('deviation entry without an id')
            continue
        status = entry.get('status')
        if status not in ('declared', 'fixed_in_m1'):
            reasons.append('%s has unrecognised status %r' % (entry['id'], status))
        normalised.append({'id': entry['id'], 'status': status,
                           'location': entry.get('location') or entry.get('where'),
                           'fix_ref': entry.get('fix_ref')})
    gate.deviations = {'count': len(normalised),
                       'entries': sorted(normalised, key=lambda e: str(e['id'])),
                       'source': gate.rel(path)}
    return gate.result('declared_deviations_present', not reasons, '; '.join(reasons),
                       [gate.rel(path)], {'count': len(normalised)})


# --------------------------------------------------------------------------
# Route fidelity
# --------------------------------------------------------------------------

def consumer_state(evidence):
    """Read the verifier lane's `consumer_state` block, tolerating its key names."""
    block = (evidence or {}).get('consumer_state')
    if not isinstance(block, dict):
        return None, 'route-evidence has no consumer_state block'
    containers = block.get('containers') or block.get('services') or []
    if isinstance(containers, dict):
        # route-evidence.py keys the block by compose service name.
        containers = [dict(row, service=row.get('service') or name) if isinstance(row, dict) else row
                      for name, row in sorted(containers.items())]
    if not isinstance(containers, list) or not containers:
        return None, 'consumer_state lists no consumer containers'
    summary, problems = [], []
    for row in containers:
        if not isinstance(row, dict):
            problems.append('malformed consumer_state container entry')
            continue
        name = row.get('service') or row.get('name') or row.get('container')
        running = row.get('running')
        if running is None:
            running = str(row.get('state') or row.get('status') or '').lower() == 'running'
        restarts = next((row[k] for k in ('restart_count', 'restarts', 'RestartCount')
                         if k in row), None)
        summary.append({'service': name, 'running': bool(running), 'restart_count': restarts})
        if not running:
            problems.append('consumer %s is not running' % name)
        if restarts is None:
            problems.append('consumer %s reports no restart count' % name)
        elif restarts != 0:
            problems.append('consumer %s restarted %s time(s)' % (name, restarts))
    panics = next((block[k] for k in ('panic_count', 'panics', 'panic') if k in block), None)
    if panics is None and isinstance(block.get('log_markers'), dict):
        # route-evidence.py counts unfiltered crash markers in the main consumer's log.
        panics = block['log_markers'].get('panic:')
    if panics is None:
        problems.append('consumer_state reports no panic count')
    elif panics:
        problems.append('%s panic(s) in the consumer logs' % panics)
    return {'containers': summary, 'panic_count': panics}, '; '.join(problems) or None


def scenario_records(gate: Gate, role, subdir, kind):
    """(payload, path) for the results file backing a scenario kind."""
    base = gate.role_path(role)
    if base is None:
        return None, None, None
    directory = (base / subdir) if subdir else base
    name = {'route': 'route-results.json', 'bank': 'bank-effects.json',
            'kafka': 'kafka-results.json', 'golden': 'live-result.json'}[kind]
    path = gate.find_file(base, name, subdir)
    if path is None:
        return None, directory, name
    return gate.read_json(path), path.parent, path


def evaluate_scenarios(gate: Gate):
    cache = {}
    for profile, scenario, role, subdir, kind in REQUIRED_SCENARIOS:
        record = {'profile': profile, 'scenario': scenario, 'manifest_role': role,
                  'kind': kind, 'run_dir': None, 'artifacts': [],
                  'transport_observed': None, 'transport_evidence': None,
                  'scenario_status': 'missing', 'payout_id': None,
                  'observed_state': None, 'expected_failure_ref': None,
                  'matched_expected_failure': False, 'fidelity_pass': False,
                  'business_completed': False, 'reason': None}
        key = (role, subdir, kind)
        if key not in cache:
            cache[key] = scenario_records(gate, role, subdir, kind)
        payload, directory, path = cache[key]
        if directory is not None:
            record['run_dir'] = gate.rel(directory)
        if payload is None:
            record['reason'] = ('missing evidence: %s for role %s%s'
                                % (path or 'results file', role,
                                   '/' + subdir if subdir else ''))
            gate.scenarios.append(record)
            continue
        record['artifacts'].append(gate.rel(path))

        case = None
        if kind == 'golden':
            case = payload
        else:
            rows = payload.get('cases') or payload.get('scenarios') or []
            field = 'scenario' if kind == 'bank' else 'name'
            case = next((r for r in rows if r.get(field) == scenario), None)
        if case is None:
            record['reason'] = ('missing evidence: case %r not present in %s'
                                % (scenario, gate.rel(path)))
            gate.scenarios.append(record)
            continue

        record['scenario_status'] = case.get('status') or ('passed' if case.get('passed') else 'failed')
        record['payout_id'] = case.get('payout_id')

        # Observed ending state: from a snapshot when one exists, else from the
        # kafka lane's own structured observation.
        layers = dig(case, 'ending_state', 'layers')
        if layers is None and kind in ('route', 'kafka'):
            # Both runners record one scenario_snapshot (helpers/trace_snapshot) in the
            # per-case trace; the gate re-derives the ending state from that DB snapshot
            # rather than trusting the runner's own self-report.
            jsonl = directory / ('%s-%s.jsonl' % (kind, scenario))
            if jsonl.is_file():
                record['artifacts'].append(gate.rel(jsonl))
                for line in gate.read_text(jsonl).splitlines():
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get('kind') == 'scenario_snapshot':
                        layers = dig(row, 'data', 'layers')
        if layers is not None:
            record['observed_state'] = state_from_layers(layers)
        elif isinstance(case.get('observed_state'), dict):
            record['observed_state'] = case['observed_state']

        # Transport.
        evidence, evidence_path, evidence_reason = gate.route_evidence(
            directory, scenario, record['payout_id'])
        record['transport_evidence'] = evidence_path
        if evidence is None:
            record['transport_observed'] = None
            record['reason'] = evidence_reason
        else:
            record['artifacts'].append(evidence_path)
            record['transport_observed'] = evidence.get('transport_observed') is True
            record['transport_required'] = evidence.get('required')
            record['transport_observations'] = evidence.get('observed')
            if not record['transport_observed']:
                record['reason'] = ('transport not observed: required %s, observed %s'
                                    % (evidence.get('required'), evidence.get('observed')))
            if profile == 'kafka':
                state, problem = consumer_state(evidence)
                record['consumer_state'] = state
                if problem:
                    record['reason'] = '; '.join(filter(None, [record['reason'], problem]))
                    record['consumer_state_ok'] = False
                else:
                    record['consumer_state_ok'] = True

        # Ending state: a normal pass, or a declared source-predicted failure.
        normal_pass = record['scenario_status'] == 'passed'
        entry = registry_for(gate, profile, scenario)
        if not normal_pass and entry is not None:
            predicted = entry.get('predicted_state') or {}
            observed = record['observed_state']
            if observed and any(k in observed for k in
                                ('payout_status', 'fts_status', 'journal_events', 'webhook_events')):
                matched, mismatches = state_matches(predicted, observed)
            else:
                matched, mismatches = False, ['no observed state to compare']
            if kind == 'kafka':
                lane = case.get('matches_prediction')
                cited = case.get('expected_failure_ref')
                if lane is not True:
                    matched = False
                    mismatches.append('kafka-results matches_prediction is %r' % lane)
                if not citation_covers(cited, entry):
                    matched = False
                    mismatches.append('kafka-results cites %r, registry entry is %s (%s)'
                                      % (cited, entry['id'], ', '.join(entry.get('source_refs') or [])))
            record['expected_failure_ref'] = entry['id']
            record['matched_expected_failure'] = bool(matched)
            record['predicted_state'] = predicted
            if not matched:
                record['reason'] = '; '.join(filter(None, [record['reason'],
                                                           'expected failure %s did not match: %s'
                                                           % (entry['id'], '; '.join(mismatches))]))
            else:
                gate.matched_failures.setdefault(entry['id'], []).append(
                    {'profile': profile, 'scenario': scenario, 'run_dir': record['run_dir'],
                     'artifacts': sorted(set(record['artifacts']))})
        elif not normal_pass:
            record['reason'] = '; '.join(filter(None, [
                record['reason'],
                'case %r is %r and matches no entry in the expected-failures registry'
                % (scenario, record['scenario_status'])]))

        state_ok = normal_pass or record['matched_expected_failure']
        consumer_ok = record.get('consumer_state_ok', True) if profile == 'kafka' else True
        record['fidelity_pass'] = bool(record['transport_observed'] and state_ok and consumer_ok)
        record['business_completed'] = bool(normal_pass)
        if record['fidelity_pass'] and not record['reason']:
            record['reason'] = None
        gate.scenarios.append(record)


def collect_unexplained(gate: Gate):
    """Every non-passing observation in a required run that nothing explains."""
    for record in gate.scenarios:
        # A normal pass explains itself; a matched expected failure is explained by
        # its registry entry. Missing evidence is a route_fidelity failure, not an
        # unexplained *result* - there is no result to explain.
        if record['scenario_status'] in ('passed', 'missing'):
            continue
        if record['matched_expected_failure']:
            continue
        explained = gate.fixed_defect_runs.get((record['run_dir'] or '').strip('/'))
        gate.unexplained.append({
            'kind': 'required_scenario', 'profile': record['profile'],
            'scenario': record['scenario'], 'status': record['scenario_status'],
            'run_dir': record['run_dir'],
            'fixed_defect_ref': explained,
            'expected_failure_ref': record['expected_failure_ref'],
            'reason': record['reason'] or 'scenario did not pass'})

    # Any other case retained ANYWHERE in a required run that did not pass. A
    # side directory the required-scenario table does not name (the historical
    # Direct-after-Shared timeout, say) must still be explained or block.
    required = {(r['run_dir'], r['scenario']) for r in gate.scenarios}
    for role, profile in sorted(ROLE_PROFILE.items()):
        base = gate.role_path(role)
        if base is None or not base.is_dir():
            continue
        directories = [base] + sorted(p for p in base.iterdir() if p.is_dir())
        for directory in directories:
            for name, field in (('route-results.json', 'name'),
                                ('bank-effects.json', 'scenario'),
                                ('kafka-results.json', 'name')):
                path = directory / name
                if not path.is_file():
                    continue
                payload = gate.read_json(path)
                where = gate.rel(directory)
                for case in (payload.get('cases') or payload.get('scenarios') or []):
                    scenario = case.get(field)
                    status = case.get('status') or ('passed' if case.get('passed') else 'failed')
                    if status == 'passed' or (where, scenario) in required:
                        continue
                    entry = registry_for(gate, profile, scenario)
                    if entry is not None and case.get('matches_prediction') is True:
                        continue
                    gate.unexplained.append({
                        'kind': 'retained_case', 'profile': profile, 'scenario': scenario,
                        'status': status, 'run_dir': where,
                        'fixed_defect_ref': gate.fixed_defect_runs.get(where.strip('/')),
                        'expected_failure_ref': entry['id'] if entry else None,
                        'reason': 'retained case %r in %s is %r and maps to no expected failure '
                                  'and no completed fixed twin defect'
                                  % (scenario, gate.rel(path), status)})
    # A result attributed to a *completed* fixed twin defect is explained; it is
    # still listed, so the reader can see what the fix supersedes.
    unresolved = [u for u in gate.unexplained if not u.get('fixed_defect_ref')]
    explained = [u for u in gate.unexplained if u.get('fixed_defect_ref')]
    gate.unexplained = sorted(unresolved, key=lambda u: (u['profile'], str(u['scenario']))) + \
                       sorted(explained, key=lambda u: (u['profile'], str(u['scenario'])))
    return unresolved


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------

def build_report(gate: Gate, manifest_path: Path):
    registry_problems = load_registry(gate)
    load_deviations(gate)
    load_fixed_defects(gate)

    missing_fingerprints = gate.load_fingerprints()
    summary, reports, audit_artifacts = audit_reports(gate)
    boot_matched, boot_reason = gate.audit_boot_match(summary)

    check_fresh_build(gate)
    check_empty_volume_boot(gate)
    check_audits(gate, summary, reports, audit_artifacts, boot_matched, boot_reason)
    check_egress(gate)
    check_verifier_suite(gate)
    check_logical_replay(gate)
    check_resource_profile(gate)
    check_explorer(gate)
    check_staleness(gate, missing_fingerprints, boot_matched, boot_reason)

    evaluate_scenarios(gate)

    by_profile: dict[str, dict] = {}
    health: dict[str, dict] = {}
    fidelity_reasons = []
    for record in gate.scenarios:
        by_profile.setdefault(record['profile'], {})[record['scenario']] = {
            'run_dir': record['run_dir'],
            'transport_observed': record['transport_observed'],
            'transport_evidence': record['transport_evidence'],
            'scenario_status': record['scenario_status'],
            'observed_state': record['observed_state'],
            'expected_failure_ref': record['expected_failure_ref'],
            'matched_expected_failure': record['matched_expected_failure'],
            'consumer_state': record.get('consumer_state'),
            'fidelity_pass': record['fidelity_pass'],
            'reason': record['reason'],
            'artifacts': sorted(set(record['artifacts'])),
        }
        health.setdefault(record['profile'], {})[record['scenario']] = {
            'business_completed': record['business_completed'],
            'expected_failure_ref': record['expected_failure_ref'] if not record['business_completed'] else None,
            'run_dir': record['run_dir'],
            'evidence': sorted(set(record['artifacts'])),
        }
        if not record['fidelity_pass']:
            fidelity_reasons.append('%s/%s: %s' % (record['profile'], record['scenario'],
                                                   record['reason'] or 'did not pass'))
    if registry_problems:
        fidelity_reasons = registry_problems + fidelity_reasons
    gate.result('route_fidelity', not fidelity_reasons, '; '.join(fidelity_reasons),
                sorted({a for r in gate.scenarios for a in r['artifacts']}),
                {'profiles': sorted(by_profile), 'scenarios_evaluated': len(gate.scenarios)})

    unresolved = collect_unexplained(gate)
    gate.result('no_unexplained_results', not unresolved,
                '%d unexplained result(s): %s' % (
                    len(unresolved),
                    '; '.join('%s/%s=%s' % (u['profile'], u['scenario'], u['status'])
                              for u in unresolved[:12])),
                [], {'count': len(unresolved)})

    executed = {'route_profiles': sorted({r['profile'] for r in gate.scenarios
                                          if r['scenario_status'] != 'missing'}),
                'scenarios': [{'profile': r['profile'], 'scenario': r['scenario'],
                               'run_dir': r['run_dir'], 'status': r['scenario_status']}
                              for r in gate.scenarios],
                'boots': {role: {'path': e['path'], 'boot_id': e['data'].get('boot_id'),
                                 'route_profile': e['data'].get('route_profile'),
                                 'config_digest': e['data'].get('config_digest'),
                                 'git_head': e['data'].get('git_head')}
                          for role, e in sorted(gate.fingerprints.items())}}

    expected = []
    for ef_id, entry in sorted(gate.registry.items()):
        matches = gate.matched_failures.get(ef_id) or []
        expected.append({'id': ef_id, 'title': entry.get('title'),
                         'scope': entry.get('scope'),
                         'predicted_state': entry.get('predicted_state'),
                         'source_refs': entry.get('source_refs'),
                         'deployed_status': entry.get('deployed_status'),
                         'matched': bool(matches), 'matched_by': matches})

    blockers = [{'check': name,
                 'reason': gate.details[name]['reason'],
                 'evidence': gate.details[name]['artifacts']}
                for name in REQUIRED_CHECKS if not gate.checks.get(name)]
    status = 'passed' if not blockers else 'blocked'

    return {
        'schema_version': SCHEMA_VERSION,
        'status': status,
        'generated_by': 'ENV2_COMPOSE/scripts/local-acceptance.py',
        'manifest': gate.rel(manifest_path),
        'current_tree': {'config_digest': gate.current_digest,
                         'config_files_hashed': len(gate.current_hashes),
                         'git_head': fingerprint.git_head(ROOT)},
        'checks': {name: bool(gate.checks.get(name)) for name in REQUIRED_CHECKS},
        'check_details': {name: gate.details.get(name, {'passed': False,
                                                        'reason': 'check not computed',
                                                        'artifacts': [], 'observed': {}})
                          for name in REQUIRED_CHECKS},
        'route_fidelity': by_profile,
        'product_route_health': health,
        'expected_failures': expected,
        'declared_deviations': gate.deviations,
        'fixed_twin_defects': gate.fixed_defects,
        'executed': executed,
        'staleness': gate.details.get('staleness', {}).get('observed', {}),
        'unexplained_results': gate.unexplained,
        'blockers': blockers,
        'evidence_files': [{'path': path, 'sha256': gate.evidence[path]}
                           for path in sorted(gate.evidence)],
    }


def render_summary(report: dict) -> str:
    lines = ['# Local acceptance (generated)', '',
             'Produced by `ENV2_COMPOSE/scripts/local-acceptance.py` from the artifacts named in '
             '`%s`. Every boolean below is computed; none is attested.' % report['manifest'], '',
             '**Status: %s**' % report['status'].upper(), '',
             'Current tree config digest `%s` over %d hashed input files.'
             % (report['current_tree']['config_digest'][:16],
                report['current_tree']['config_files_hashed']), '',
             '## Required checks', '', '| Check | Result | Reason |', '|---|---|---|']
    for name in REQUIRED_CHECKS:
        detail = report['check_details'][name]
        reason = (detail['reason'] or '').replace('|', '\\|').replace('\n', ' ')
        lines.append('| `%s` | %s | %s |' % (name, 'PASS' if detail['passed'] else 'FAIL',
                                             reason[:400] or '-'))
    lines += ['', '## Route fidelity', '',
              '| Profile | Scenario | Transport | State | Expected failure | Fidelity |',
              '|---|---|---|---|---|---|']
    for profile in sorted(report['route_fidelity']):
        for scenario in sorted(report['route_fidelity'][profile]):
            row = report['route_fidelity'][profile][scenario]
            lines.append('| %s | %s | %s | %s | %s | %s |' % (
                profile, scenario,
                {True: 'observed', False: 'NOT observed', None: 'no evidence'}[row['transport_observed']],
                row['scenario_status'],
                row['expected_failure_ref'] or '-',
                'PASS' if row['fidelity_pass'] else 'FAIL'))
    lines += ['', '## Product route health (informational)', '',
              '| Profile | Scenario | Business completed | Citation |', '|---|---|---|---|']
    for profile in sorted(report['product_route_health']):
        for scenario in sorted(report['product_route_health'][profile]):
            row = report['product_route_health'][profile][scenario]
            lines.append('| %s | %s | %s | %s |' % (
                profile, scenario, 'yes' if row['business_completed'] else 'NO',
                row['expected_failure_ref'] or '-'))
    lines += ['', '## Expected failures matched', '']
    for entry in report['expected_failures']:
        lines.append('- **%s** %s - %s (%s)' % (
            entry['id'], 'MATCHED' if entry['matched'] else 'not matched',
            entry['title'],
            ', '.join('%s/%s' % (m['profile'], m['scenario']) for m in entry['matched_by']) or 'no run'))
    lines += ['', '## Declared deviations', '',
              '%d entries from `%s`.' % (report['declared_deviations']['count'],
                                         report['declared_deviations']['source'] or 'no registry'), '']
    lines += ['## Fixed twin defects', '']
    for defect in report['fixed_twin_defects']:
        lines.append('- **%s** %s (%s)' % (defect.get('id'), defect.get('title'),
                                           defect.get('location')))
    lines += ['', '## Unexplained results', '']
    if not report['unexplained_results']:
        lines.append('None.')
    for item in report['unexplained_results']:
        lines.append('- `%s/%s` %s in `%s`%s - %s' % (
            item['profile'], item['scenario'], item['status'], item['run_dir'],
            ' [explained by %s]' % item['fixed_defect_ref'] if item.get('fixed_defect_ref') else '',
            item['reason']))
    lines += ['', '## Blockers', '']
    if not report['blockers']:
        lines.append('None.')
    for blocker in report['blockers']:
        lines.append('- **%s**: %s' % (blocker['check'], blocker['reason']))
    lines += ['', '%d evidence files are hash-bound in the JSON gate.'
              % len(report['evidence_files']), '']
    return '\n'.join(lines)


def write_idempotent(path: Path, text: str, key=None):
    """Rewrite only when the content actually changed, ignoring `recorded_at`."""
    if path.is_file() and key is not None:
        try:
            existing = json.loads(path.read_text())
        except (OSError, ValueError):
            existing = None
        if isinstance(existing, dict):
            previous = existing.get('recorded_at')
            candidate = json.loads(text)
            candidate['recorded_at'] = previous
            if candidate == existing:
                return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text)
    temporary.replace(path)
    return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('manifest', type=Path, help='JSON manifest of artifact roles -> paths')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'reports/implementation/local-acceptance.json')
    parser.add_argument('--summary', type=Path, default=None,
                        help='Human summary; defaults to LOCAL_ACCEPTANCE_GENERATED.md beside --output')
    args = parser.parse_args(argv)

    try:
        manifest_path = args.manifest.resolve()
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise ManifestError('Manifest must be a JSON object of role -> path')
        gate = Gate(manifest, manifest_path)
        report = build_report(gate, manifest_path)
        report['recorded_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        text = json.dumps(report, indent=2, sort_keys=True) + '\n'
        summary_path = args.summary or (args.output.parent / 'LOCAL_ACCEPTANCE_GENERATED.md')
        write_idempotent(args.output.resolve(), text, key='recorded_at')
        write_idempotent(summary_path.resolve(), render_summary(report))
    except ManifestError as exc:
        print('MANIFEST/IO ERROR: %s' % exc, file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print('MANIFEST/IO ERROR: %s: %s' % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    failed = [name for name in REQUIRED_CHECKS if not report['checks'][name]]
    print('local-acceptance: %s (%d/%d checks, %d unexplained result(s)) -> %s'
          % (report['status'], len(REQUIRED_CHECKS) - len(failed), len(REQUIRED_CHECKS),
             len(report['unexplained_results']), args.output))
    for blocker in report['blockers']:
        print('  BLOCKED %s: %s' % (blocker['check'], (blocker['reason'] or '')[:300]))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
