"""Local synthetic trace recorder; credentials/authorization never serialized."""
import datetime
import decimal
import json
import os
from pathlib import Path
import re
import threading
import time

_LOCK = threading.RLock()
_CURRENT = 'session'
_COUNTER = 0


def select(name):
    global _CURRENT, _COUNTER
    with _LOCK:
        _CURRENT = re.sub('[^a-zA-Z0-9_.-]', '_', name)
        _COUNTER = 0


def clean(value):
    if isinstance(value, dict):
        return {k:('[REDACTED]' if any(s in k.lower() for s in ('password','secret','authorization','token','jwt')) or ('signature' in k.lower() and k not in ('signature_present','signature_valid')) else clean(v)) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [clean(v) for v in value]
    if isinstance(value, (datetime.datetime,datetime.date,decimal.Decimal)): return str(value)
    if isinstance(value,bytes): return value.decode(errors='replace')
    if isinstance(value,str):
        value = re.sub(r'eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+','[REDACTED_JWT]',value)
        return value
    return value


def record(kind, data):
    global _COUNTER
    folder = os.environ.get('ARENA_TRACE_DIR')
    if not folder: return
    with _LOCK:
        _COUNTER += 1
        path = Path(folder)/(_CURRENT+'.jsonl')
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('a') as f:
            f.write(json.dumps({'sequence':_COUNTER,'at':time.time(),'kind':kind,'data':clean(data)},default=str)+'\n')
