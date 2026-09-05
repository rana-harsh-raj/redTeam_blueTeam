#!/usr/bin/env python3
"""Run an arena command and retain a log with generated credentials redacted."""
import base64
from pathlib import Path
import re
import subprocess
import sys

ARENA=Path(__file__).resolve().parents[1]
if len(sys.argv)<3:raise SystemExit('Usage: capture-command.py LOG COMMAND [ARG ...]')
log=Path(sys.argv[1]).resolve();log.parent.mkdir(parents=True,exist_ok=True)
values=set();seen={}
def refresh():
    for path in (ARENA/'secrets').rglob('*'):
        if not path.is_file() or path.suffix in ('.py','.sh','.json','.md','.toml'):continue
        stat=path.stat()
        if seen.get(path)==stat.st_mtime_ns:continue
        seen[path]=stat.st_mtime_ns
        value=path.read_text(errors='replace').strip()
        if len(value)>12:
            values.add(value)
            if ':' in value and '\n' not in value:values.add(base64.b64encode(value.encode()).decode())
def redact(text):
    for value in sorted(values,key=len,reverse=True):text=text.replace(value,'[REDACTED_SYNTHETIC_SECRET]')
    return re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[REDACTED_JWT]',text)
refresh()
process=subprocess.Popen(sys.argv[2:],cwd=ARENA,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
with log.open('w') as output:
    for line in process.stdout:
        refresh();output.write(redact(line));output.flush()
status=process.wait();log.with_suffix('.exit').write_text(str(status)+'\n')
print(str(log)+': exit '+str(status),flush=True)
raise SystemExit(status)
