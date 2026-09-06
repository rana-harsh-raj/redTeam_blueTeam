#!/usr/bin/env python3
"""Capture the full lifetime of a command on the Docker host; fail closed.

Only the capture container gets host-network visibility. Analysis containers
use --network none. PCAPs may contain short payload prefixes (96-byte snaplen), remain local, and
are deleted with the temporary volume after metadata is exported.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get('ARENA_AUDIT_IMAGE', 'nicolaka/netshoot:latest')


def run(args, **kwargs):
    kwargs.setdefault("timeout", 60)
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, **kwargs).stdout.strip()


def audit(command, output, duration, command_container=None):
    output.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex[:12]
    name, volume = 'twin-audit-' + token, 'twin-audit-' + token
    result = {'schema_version': 1, 'status': 'failed', 'command_exit_code': None,
              'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'capture_ready_before_command': False, 'capture_alive_after_command': False}
    process = None
    analyzers = []

    def analyze(args):
        analyzer_name = name + "-analyze-" + str(len(analyzers))
        analyzers.append(analyzer_name)
        return run(args[:3] + ["--name", analyzer_name] + args[3:])
    capture_started = False
    interrupted = False
    t0 = time.monotonic()

    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = True
        if process and process.poll() is None:
            process.terminate()
        raise KeyboardInterrupt

    old_handlers = {s: signal.signal(s, interrupt) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        run(['docker', 'image', 'inspect', IMAGE])  # never pull during runtime
        _sfx = os.environ.get('ARENA_SUFFIX', '')  # M3.1: audit the instance's own networks
        networks = json.loads(run(['docker', 'network', 'inspect', 'rzp-arena' + _sfx, 'rzp-ingress' + _sfx]))
        if any(n.get('EnableIPv6') for n in networks):
            raise RuntimeError('IPv6 capture policy is not implemented; refusing incomplete audit')
        if any(not n.get('Internal') for n in networks):
            raise RuntimeError('Both runtime networks must be internal before capture')
        subnets = [c['Subnet'] for n in networks for c in n['IPAM']['Config']]
        import ipaddress
        for subnet in subnets:
            ipaddress.ip_network(subnet)  # reject unexpected BPF input
        source = '(' + ' or '.join('src net ' + s for s in subnets) + ')'
        internal = '(' + ' or '.join('dst net ' + s for s in subnets) + ')'
        outside = source + ' and not ' + internal
        result['subnets'] = subnets
        result['filter'] = outside
        run(['docker', 'volume', 'create', volume])
        # Both captures start together. tcpdump's listening messages, not an
        # empty file or a sleep, establish that packet collection is ready.
        script = '''set -eu
trap 'kill -INT "$outside_pid" "$control_pid" 2>/dev/null || :; wait "$outside_pid" || :; wait "$control_pid" || :; exit 0' TERM INT
tcpdump -U -s 96 -i any -n -w /capture/outside.pcap "$1" 2>/capture/outside.log &
outside_pid=$!
tcpdump -U -s 96 -C 4 -W 1 -i any -n -w /capture/control.pcap "$2" 2>/capture/control.log &
control_pid=$!
while kill -0 "$outside_pid" && kill -0 "$control_pid"; do sleep 1 & wait $! || :; done
exit 1
'''
        # Register the name before launch: a timeout can occur after Docker
        # created the container but before the client received its response.
        capture_started = True
        run(['docker', 'run', '-d', '--name', name, '--network', 'host',
             '--cap-add', 'NET_RAW', '--cap-add', 'NET_ADMIN', '-v', volume + ':/capture',
             IMAGE, 'sh', '-c', script, 'capture', outside, source])
        for _ in range(100):
            ready = subprocess.run(['docker', 'exec', name, 'sh', '-c',
                "grep -q 'listening on' /capture/outside.log && grep -q 'listening on' /capture/control.log"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
            if ready:
                break
            if run(['docker', 'inspect', '-f', '{{.State.Running}}', name]) != 'true':
                raise RuntimeError('capture exited before readiness')
            time.sleep(.1)
        else:
            raise RuntimeError('capture never became ready')
        result['capture_ready_before_command'] = True
        result['command_started_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        if command:
            process = subprocess.Popen(command, cwd=ROOT)
            result['command_exit_code'] = process.wait(timeout=duration)
        else:
            # Standalone audit; traffic still must be observed to establish coverage.
            until = time.monotonic() + duration
            while time.monotonic() < until:
                time.sleep(min(.25, max(0, until - time.monotonic())))
            result['command_exit_code'] = 0
        result['command_finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        result['capture_alive_after_command'] = run(['docker', 'inspect', '-f', '{{.State.Running}}', name]) == 'true'
        if not result['capture_alive_after_command']:
            raise RuntimeError('capture stopped before command completion')
        run(['docker', 'stop', '-t', '10', name])
        # Rotation can leave control.pcap or control.pcap0, depending on tcpdump.
        analyzer = '''set -eu
cap=$(find /capture -name 'control.pcap*' -type f | head -1)
test -n "$cap"
tshark -r "$cap" -T fields -e frame.number | wc -l
'''
        count = int(analyze(['docker', 'run', '--rm', '--network', 'none', '-v', volume + ':/capture:ro',
                         IMAGE, 'sh', '-c', analyzer]))
        destinations = analyze(['docker', 'run', '--rm', '--network', 'none', '-v', volume + ':/capture:ro',
                            IMAGE, 'tshark', '-r', '/capture/outside.pcap', '-T', 'fields',
                            '-e', 'ip.src', '-e', 'ip.dst', '-e', 'tcp.dstport', '-e', 'udp.dstport'])
        result['control_packets'] = count
        result['outside_packets'] = len(destinations.splitlines()) if destinations else 0
        result['destinations'] = sorted(set(destinations.splitlines()))
        for stem in ('outside','control'):
            result[stem + '_capture_stats'] = analyze(['docker', 'run', '--rm', '--network', 'none',
                '-v', volume + ':/capture:ro', IMAGE, 'cat', '/capture/' + stem + '.log'])
        if count <= 0:
            raise RuntimeError('No control traffic: capture coverage unproven')
        if result['outside_packets']:
            raise RuntimeError('External destination observed during command')
        for stem in ('outside','control'):
            stats = result[stem + '_capture_stats']
            drop = re.search(r'(\d+) packets dropped by kernel', stats)
            if not drop or int(drop.group(1)) != 0:
                raise RuntimeError('Missing capture statistics or dropped packets')
        result['status'] = 'passed'
    except subprocess.TimeoutExpired:
        result['error'] = 'Command exceeded audit wall-clock deadline'
        if process:
            process.terminate()
    except KeyboardInterrupt:
        result['error'] = 'Interrupted; audit incomplete'
    except Exception as exc:
        # Do not serialize subprocess arguments or env: may include synthetic credentials.
        result['error'] = str(exc) if not isinstance(exc, subprocess.CalledProcessError) else 'Docker capture/analysis command failed'
    finally:
        def cleanup_signal(signum, frame):
            nonlocal interrupted
            interrupted = True
            result['status'] = 'failed'
            result['error'] = 'Interrupted during cleanup'
        for signum in old_handlers:
            signal.signal(signum, cleanup_signal)
        if process and process.poll() is None:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    result['status'] = 'failed'
                    result['error'] = 'Command process shutdown could not be confirmed'
        def remove(kind, resource):
            # --rm often removed an exited container already. A failed rm is
            # harmless only if a successful listing confirms exact absence;
            # a daemon error must never be interpreted as absence.
            try:
                args = ['docker', 'rm', '-f', resource] if kind == 'container' else ['docker', 'volume', 'rm', resource]
                subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            except (subprocess.SubprocessError, OSError):
                pass
            try:
                args = ['docker', kind, 'ls'] + (['--all'] if kind == 'container' else [])
                check = subprocess.run(args + ['--format', '{{.Names}}' if kind == 'container' else '{{.Name}}',
                    '--filter', 'name=^' + ('/?' if kind == 'container' else '') + re.escape(resource) + '$'],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=10)
                return check.returncode == 0 and not check.stdout.strip()
            except (subprocess.SubprocessError, OSError):
                return False
        result['analyzer_containers_removed'] = True
        for analyzer in analyzers:
            if not remove('container', analyzer):
                result['analyzer_containers_removed'] = False
                result['status'] = 'failed'
                result['error'] = 'Analyzer container cleanup could not be confirmed: ' + analyzer
        if command_container:
            result['command_container_removed'] = remove('container', command_container)
            if not result['command_container_removed']:
                result['status'] = 'failed'
                result['error'] = 'Verifier container cleanup failed: ' + command_container
        if capture_started and not remove('container', name):
            result['status'] = 'failed'
            result['error'] = 'Capture container cleanup failed: ' + name
        cleanup_ok = remove('volume', volume)
        result['temporary_capture_removed'] = cleanup_ok
        if capture_started and not cleanup_ok:
            result['status'] = 'failed'
            result['error'] = 'Could not remove capture volume ' + volume
        result['elapsed_seconds'] = round(time.monotonic() - t0, 3)
        result['finished_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
        (output/'egress.json').write_text(json.dumps(result, indent=2) + '\n')
        (ROOT/'EGRESS_AUDIT.md').write_text('# Runtime egress audit\n\n' +
            result['status'].upper() + ': ' + result.get('error', 'No external packets in the complete command window.') +
            '\n\nEvidence: `' + str(output/'egress.json') + '`\n')
        for s, handler in old_handlers.items():
            signal.signal(s, handler)
    print('Egress audit: ' + result['status'] + '; evidence ' + str(output/'egress.json'), flush=True)
    return 130 if interrupted else (result['command_exit_code'] or 0) if result['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--duration', type=int, default=1800, help='hard command deadline, seconds')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--command-container')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error('duration must be positive')
    cmd = args.command[1:] if args.command[:1] == ['--'] else args.command
    out = args.output or ROOT.parent/'reports/implementation/runs'/('audit-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:6])
    sys.exit(audit(cmd, out, args.duration, args.command_container))
