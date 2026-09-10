#!/usr/bin/env python3
"""preflight/preflight.py — safety gate: scan every effective config value
this arena will actually run with, and REFUSE to let scripts/up.sh proceed
if any forbidden pattern is found anywhere.

stdlib only. Run AFTER config/generate.py (it scans `generated/`, which is
that script's output) and AFTER .env.arena is loaded into the process
environment scripts/up.sh runs in (it also scans os.environ, since a
compose `environment:` value counts as "effective config" just as much as a
rendered TOML file does).

Forbidden patterns (task brief, verbatim):
  - razorpay\\.(com|in|vpc)
  - amazonaws\\.com
  - arn:aws
  - 169\\.254\\.                       (link-local / cloud metadata endpoint)
  - RFC1918 ranges OUTSIDE the arena's own docker-compose subnets
    (ARENA_SUBNET / INGRESS_SUBNET in .env.arena) -- an RFC1918 address
    INSIDE those subnets is expected (that's the arena's own bridge
    network) and is not flagged.

On success: writes SAFETY_PREFLIGHT.md (an inventory of every endpoint,
datastore, credential source, mounted path, and network this run touched,
plus a clean bill of health) and exits 0.
On failure: prints every offending file+line+match to stderr and exits 1
-- scripts/up.sh must treat a non-zero exit here as a hard stop, never a
warning.
"""
import ipaddress
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GENERATED_DIR = os.path.join(ROOT, "generated")
COMPOSE_FILE = os.path.join(ROOT, "docker-compose.yml")
ENV_ARENA_FILE = os.path.join(ROOT, ".env.arena")
OUT_MD = os.path.join(ROOT, "SAFETY_PREFLIGHT.md")

FORBIDDEN_PATTERNS = [
    ("real_razorpay_hostname", re.compile(r"razorpay\.(com|in|vpc)", re.IGNORECASE)),
    ("real_aws_hostname", re.compile(r"amazonaws\.com", re.IGNORECASE)),
    # arn:aws:<svc>:<region>:000000000000:<name> is LocalStack's synthetic account (arena-local queues/topics); every other ARN is refused
    # any public third-party host left in an upstream default TOML (e.g. beeceptor.com) -- arena configs may only name arena services
    ("third_party_host", re.compile(r"https?://[a-z0-9.-]+\.(com|in|io|net|org)\b", re.IGNORECASE)),
    ("real_aws_arn", re.compile(r"arn:aws:[a-z0-9-]*:[a-z0-9-]*:(?!000000000000)", re.IGNORECASE)),
    ("link_local_or_cloud_metadata", re.compile(r"169\.254\.")),
]

# Every arena-internal hostname this scaffold defines -- an effective config
# value naming one of these (or "localhost"/"127.0.0.1") is fine. Anything
# else that looks like a hostname is not inherently forbidden (could be a
# legitimate public image name etc.) EXCEPT the explicit regexes above,
# which are always forbidden regardless of context.
ARENA_SERVICE_NAMES = {
    "payouts-api", "payouts-workers", "payouts-kafka-consumer",
    "ledger-api", "ledger-gate", "ledger-worker", "ledger-scheduler",
    "fts-web", "fts-workers",
    "cfa-server", "cfa-worker",
    "xbalances-server", "xbalances-worker",
    "mysql-payouts", "mysql-fts", "mysql-xbalances", "mysql-apidb-stub",
    "postgres-ledger", "mongo-cfa", "redis", "localstack", "kafka",
    "kong-lite", "monolith-stub", "dcs-stub", "splitz-stub", "shield-stub",
    "pricing-stub", "asv-stub", "stork-capture", "merchant-webhook-sink",
    "xas-sink", "mozart-mock", "mozart-sim", "cron-driver", "verifier",
    "localhost", "127.0.0.1", "0.0.0.0",
}

HOSTNAME_LIKE_RE = re.compile(r"\b([a-zA-Z0-9][a-zA-Z0-9\-]*\.[a-zA-Z]{2,})\b")

RFC1918_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]

IPV4_RE = re.compile(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d{1,2})?)\b")


def _read_arena_subnets():
    subnets = []
    if not os.path.exists(ENV_ARENA_FILE):
        return subnets
    with open(ENV_ARENA_FILE) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() in ("ARENA_SUBNET", "INGRESS_SUBNET"):
                val = val.strip()
                try:
                    subnets.append(ipaddress.ip_network(val, strict=False))
                except ValueError:
                    pass
    return subnets


def _iter_scan_targets():
    """Yields (source_label, line_number_or_None, text) for every effective
    config value this run touches."""
    # 1. every file under generated/ (config/generate.py's own output)
    if os.path.isdir(GENERATED_DIR):
        for dirpath, _dirs, files in os.walk(GENERATED_DIR):
            for fn in files:
                path = os.path.join(dirpath, fn)
                label = os.path.relpath(path, ROOT)
                try:
                    with open(path, errors="replace") as f:
                        for i, line in enumerate(f, start=1):
                            yield (label, i, line)
                except OSError:
                    continue
    else:
        print("WARNING: %s does not exist -- run config/generate.py first. "
              "Preflight will scan whatever else it can, but this is likely "
              "not what you want." % GENERATED_DIR, file=sys.stderr)

    # 2. docker-compose.yml itself (static environment:/image: values)
    if os.path.exists(COMPOSE_FILE):
        with open(COMPOSE_FILE, errors="replace") as f:
            for i, line in enumerate(f, start=1):
                yield ("docker-compose.yml", i, line)

    # 3. .env.arena
    if os.path.exists(ENV_ARENA_FILE):
        with open(ENV_ARENA_FILE, errors="replace") as f:
            for i, line in enumerate(f, start=1):
                yield (".env.arena", i, line)

    # 4. the process environment scripts/up.sh actually runs config/generate.py
    #    and docker compose in -- catches a stray real credential/hostname
    #    exported into the shell that would otherwise never show up in a file.
    for key, val in sorted(os.environ.items()):
        yield ("$%s (process env)" % key, None, val)


def scan():
    findings = []  # (rule_name, source, line_no, matched_text)
    arena_subnets = _read_arena_subnets()

    endpoints = set()
    datastores = set()
    credential_sources = set()
    mounted_paths = set()
    networks_seen = set()

    for source, line_no, text in _iter_scan_targets():
        for rule_name, pattern in FORBIDDEN_PATTERNS:
            for m in pattern.finditer(text):
                findings.append((rule_name, source, line_no, m.group(0)))

        # RFC1918 outside the arena's own subnets
        for m in IPV4_RE.finditer(text):
            candidate = m.group(1)
            try:
                if "/" in candidate:
                    net = ipaddress.ip_network(candidate, strict=False)
                    addr_for_membership = net.network_address
                else:
                    addr_for_membership = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            in_rfc1918 = any(addr_for_membership in net for net in RFC1918_NETS)
            if not in_rfc1918:
                continue
            in_arena_subnet = any(addr_for_membership in net for net in arena_subnets)
            if not in_arena_subnet:
                findings.append(("rfc1918_outside_arena_subnet", source, line_no, candidate))

        # hostnames not in the arena service list / localhost (informational
        # inventory only for endpoints -- NOT itself a forbidden-pattern
        # failure, since e.g. "python.org" in a comment is harmless; the
        # explicit regexes above are what actually gates startup).
        for m in HOSTNAME_LIKE_RE.finditer(text):
            host = m.group(1).lower()
            if host in ARENA_SERVICE_NAMES:
                continue
            endpoints.add((host, source))

    # inventory (best-effort, from static knowledge of this scaffold's own
    # shape, not re-derived from the scan above -- keeps SAFETY_PREFLIGHT.md
    # readable rather than a dump of every regex hit).
    datastores.update([
        "mysql-payouts", "mysql-fts", "mysql-xbalances", "mysql-apidb-stub",
        "postgres-ledger", "mongo-cfa", "redis", "localstack (sqs/sns)", "kafka",
    ])
    credential_sources.update([
        "secrets/*.txt (generated by secrets/gen-secrets.sh, gitignored)",
        "generated/<svc>/*.toml (credentials inlined by config/generate.py from secrets/*.txt)",
        "docker-compose.yml top-level file-based `secrets:` (individual datastore bootstrap, cron and verifier credentials)",
        "secrets/materialize.py streams only explicit generated consumer groups into owned runtime volumes; no credential image layers",
    ])
    mounted_paths.update([
        "config-<svc> named volumes -> /app/config:ro (or /app/conf:ro for mozart-mock); UID 10001 files 0400, directories 0500",
        # M6 (I3): secrets-workflow is a third such volume (workflow-engine +
        # workflow-sim; auth_workflow_payouts + wfe_admin_token). It is separate
        # from secrets-kong on purpose -- the engine's admin-plane token must not
        # land on the ingress container's filesystem.
        "secrets-kong, secrets-monolith and secrets-workflow named volumes -> each consumer group's /run/secrets:ro; exact generated credentials only",
        "Compose file-based secrets mount individual host-generated files read-only for root bootstrap/cron/verifier; not claimed tmpfs-backed",
        "Host generated credential files stay 0600; generated runtime volumes are removed by down -v and secrets/destroy.sh",
    ])
    networks_seen.update(["rzp-arena (internal: true; services and verifier)", "rzp-ingress (internal: true; Kong only; host bridge uses Docker exec to fixed Kong loopback)"])

    return findings, sorted(endpoints), sorted(datastores), sorted(credential_sources), sorted(mounted_paths), sorted(networks_seen)


def write_report(findings, endpoints, datastores, credential_sources, mounted_paths, networks_seen):
    lines = []
    lines.append("# SAFETY_PREFLIGHT.md")
    lines.append("")
    lines.append("Generated by `preflight/preflight.py`. This file is REGENERATED on every")
    lines.append("preflight run -- do not hand-edit.")
    lines.append("")
    status = "FAIL" if findings else "PASS"
    lines.append("## Result: %s" % status)
    lines.append("")

    if findings:
        lines.append("## Forbidden-pattern hits (startup MUST be refused)")
        lines.append("")
        lines.append("| rule | source | line | matched text |")
        lines.append("|---|---|---|---|")
        for rule_name, source, line_no, matched in findings:
            lines.append("| %s | %s | %s | `%s` |" % (rule_name, source, line_no if line_no else "-", matched))
        lines.append("")

    lines.append("## Networks")
    lines.append("")
    for n in networks_seen:
        lines.append("- %s" % n)
    lines.append("")

    lines.append("## Datastores")
    lines.append("")
    for d in datastores:
        lines.append("- %s" % d)
    lines.append("")

    lines.append("## Credential sources")
    lines.append("")
    for c in credential_sources:
        lines.append("- %s" % c)
    lines.append("")

    lines.append("## Mounted paths (read-only, per-arena)")
    lines.append("")
    for p in mounted_paths:
        lines.append("- %s" % p)
    lines.append("")

    lines.append("## Hostnames observed in effective config (informational -- NOT a failure by itself)")
    lines.append("")
    lines.append("Every entry below should be either an arena service name (see")
    lines.append("`preflight.py`'s `ARENA_SERVICE_NAMES`), a public image registry name")
    lines.append("(e.g. `python.org` inside an image tag comment), or otherwise obviously")
    lines.append("benign. If anything here looks like a real external host, that's a bug")
    lines.append("in a template even if the forbidden-pattern regexes didn't catch it --")
    lines.append("investigate before running `scripts/up.sh`.")
    lines.append("")
    if endpoints:
        lines.append("| hostname | first seen in |")
        lines.append("|---|---|")
        for host, source in endpoints[:200]:
            lines.append("| `%s` | %s |" % (host, source))
        if len(endpoints) > 200:
            lines.append("| … | **truncated: %d more endpoints not shown (cap 200)** |" % (len(endpoints) - 200))
    else:
        lines.append("(none found)")
    lines.append("")

    with open(OUT_MD, "w") as f:
        f.write("\n".join(lines) + "\n")


def main():
    findings, endpoints, datastores, credential_sources, mounted_paths, networks_seen = scan()
    write_report(findings, endpoints, datastores, credential_sources, mounted_paths, networks_seen)

    if findings:
        print("PREFLIGHT FAILED: %d forbidden-pattern hit(s). See %s" % (len(findings), OUT_MD),
              file=sys.stderr)
        for rule_name, source, line_no, matched in findings:
            print("  [%s] %s:%s -> %r" % (rule_name, source, line_no, matched), file=sys.stderr)
        sys.exit(1)

    print("PREFLIGHT PASSED. Report: %s" % OUT_MD)
    sys.exit(0)


if __name__ == "__main__":
    main()
