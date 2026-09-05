"""Arena-only dependency faults. No arbitrary destinations or code execution.

Rules match one synthetic merchant or transactor; control state is local to
one substitute process. Effects are observed on actual downstream requests.
"""
import copy
import json
import threading
import time

_LOCK = threading.RLock()
_RULES = {}
_EVENTS = []


def identifiers(body):
    result = set()
    def visit(value):
        if isinstance(value, dict):
            for key, val in value.items():
                if key.lower().replace('_', '') in ('merchantid', 'payoutid', 'entityid', 'transactorid', 'sourceid', 'ownerid') and isinstance(val, (str,int)):
                    result.add(str(val))
                    if str(val).startswith('pout_'): result.add(str(val)[5:])
                visit(val)
        elif isinstance(value, list):
            for item in value: visit(item)
    visit(body)
    return result


def control(method, raw):
    if method == 'GET':
        with _LOCK: return 200, {'rules': copy.deepcopy(_RULES), 'events': copy.deepcopy(_EVENTS)}
    try:
        body = json.loads(raw or b'{}')
        scope = body['scope']
        if not isinstance(scope,str) or not 1 <= len(scope) <= 100:
            raise ValueError('scope required')
        with _LOCK:
            if body.get('clear'):
                _RULES.pop(scope,None)
                return 200, {'cleared':scope}
            fault = {k:body[k] for k in ('status','delay_ms','drop','path') if k in body}
            if not fault or set(body) - {'scope','status','delay_ms','drop','path'}:
                raise ValueError('unsupported fault fields')
            if 'status' in fault and fault['status'] not in (400,429,500,502,503,504):
                raise ValueError('unsupported status')
            if not isinstance(fault.get('delay_ms',0),int) or not 0 <= fault.get('delay_ms',0) <= 30000:
                raise ValueError('delay_ms must be 0..30000')
            if not isinstance(fault.get('drop',False),bool): raise ValueError('drop must be boolean')
            if 'path' in fault and (not isinstance(fault['path'],str) or not fault['path'].startswith('/')):
                raise ValueError('path must be an absolute route prefix')
            _RULES[scope] = fault
            return 200, {'scope':scope,'fault':fault}
    except (ValueError,TypeError,KeyError):
        return 400, {'error': 'invalid fault control; scope and bounded effects required'}


def apply(handler, raw):
    try: body = json.loads(raw or b'{}')
    except ValueError: return False
    ids = identifiers(body)
    with _LOCK:
        found = [(scope,copy.deepcopy(rule)) for scope,rule in _RULES.items()
                 if scope in ids and handler.path.startswith(rule.get('path','/'))]
        if not found: return False
        # Most recent matching registration wins, deterministic insertion order.
        scope, rule = found[-1]
        _EVENTS.append({'scope':scope,'path':handler.path,'effect':rule,'at':time.time()})
        del _EVENTS[:-1000]
    if rule.get('delay_ms'): time.sleep(rule['delay_ms']/1000)
    if rule.get('drop'):
        handler.close_connection = True
        handler.connection.close()
        return True
    if rule.get('status'):
        handler._send_json(rule['status'], {'error': {'code':'ARENA_INJECTED_DEPENDENCY_FAILURE',
                                                       'description':'synthetic dependency fault'}})
        return True
    return False
