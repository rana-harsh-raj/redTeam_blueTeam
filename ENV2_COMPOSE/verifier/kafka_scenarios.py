#!/usr/bin/env python3
"""Kafka status-transport route cases (I70/I71), driven only by real bank outcomes.

Runs ONLY under ARENA_ROUTE_PROFILE=kafka: it asserts the generated fixture index
records ``route_profile == "kafka"`` and fails closed otherwise. There is no Kafka
client library in the verifier image and none is introduced -- every Kafka message
observed here is produced by the REAL FTS producer
(``fts/internal/transfer/service.go:1139-1144``, ``kafka_producers.fire_transfer_status``)
as a consequence of a real bank outcome driven through mozart-sim, and consumed by
the REAL payouts Kafka consumer. Nothing is published, injected or replayed by this
file.

Four of the five cases are SOURCE-PREDICTED FAILURES: the twin is expected to
reproduce a production-source defect, and the case passes when the observed ending
state equals the predicted ending state (``matches_prediction``). A case FAILS if
the payout unexpectedly completes, or if any state other than the predicted one is
observed. Predicted failures are recorded with status ``expected_failure`` and a
source citation in ``expected_failure_ref``; they are never recorded as ``passed``.

Cases and fixtures (each case owns one merchant; none is shared with another case):

  shared_source_failure   scenario:kafka          M1 (shared)  expected_failure
  direct_after_shared     scenario:kafka          M2 (direct)  passed (regression test)
  failed_dropped_direct   baseline                M2 (direct)  expected_failure
  failed_dropped_shared   scenario:direct_status  M1 (shared)  expected_failure
  reversed_dropped_direct scenario:monolith_relay M2 (direct)  expected_failure

``direct_after_shared`` MUST run after ``shared_source_failure`` in the same boot:
it is the regression test for the consumer crash that the Shared case triggers
(HandleFailedMessage republish through a nil producer,
``reports/implementation/runs/claude-audit-kafka-repro-20260905T152544Z/``). Selecting
it on its own is refused.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import time

from helpers import creds, db, payouts_flow as pf, trace
from helpers.http_client import ArenaHTTPClient
from helpers.trace_snapshot import record_snapshot
from helpers.wait import wait_until
from scenario import configure_secrets

OUT = Path(os.environ.get('ARENA_TRACE_DIR', '/results'))

CASES = ('shared_source_failure', 'direct_after_shared',
         'failed_dropped_direct', 'failed_dropped_shared', 'reversed_dropped_direct')

# case -> (fixture namespace, merchant key, required archetype)
FIXTURES = {
    'shared_source_failure': ('scenario:kafka', 'M1', 'shared'),
    'direct_after_shared': ('scenario:kafka', 'M2', 'direct'),
    'failed_dropped_direct': ('baseline', 'M2', 'direct'),
    'failed_dropped_shared': ('scenario:direct_status', 'M1', 'shared'),
    'reversed_dropped_direct': ('scenario:monolith_relay', 'M2', 'direct'),
}

CONSUMER_BOOTSTRAP_REF = ('payouts internal/boot/handler.go:154 (RegisterJobs absent from '
                          'boot_kafka_consumer.go); core.go:2472-2475')
CONSUMER_DROP_REF = 'payouts internal/taskHandlers/fts_status_updates.go:56-62'

EXPECTED_FAILURES = {
    'shared_source_failure': CONSUMER_BOOTSTRAP_REF,
    'failed_dropped_direct': CONSUMER_DROP_REF,
    'failed_dropped_shared': CONSUMER_DROP_REF,
    'reversed_dropped_direct': CONSUMER_DROP_REF,
}

TERMINAL_EVENTS = ('payout.processed', 'payout.failed', 'payout.reversed',
                   'payout.cancelled', 'payout.rejected')

AMOUNT = 10000


def deadline(*_):
    raise TimeoutError('kafka case exceeded its bounded runtime')


def setup():
    configure_secrets()
    index = json.loads(Path('/fixtures/scenario-index.json').read_text())
    profile = index.get('route_profile')
    # Fail closed: these expectations are only meaningful when FTS status propagation
    # actually goes over Kafka (fts service.go:1139-1144 is strictly exclusive).
    assert profile == 'kafka', (
        'kafka_scenarios requires ARENA_ROUTE_PROFILE=kafka; generated fixture index reports route_profile=%r'
        % (profile,))
    monolith_auth = creds.resolve_basic_auth('MONOLITH')
    assert monolith_auth, 'missing mounted synthetic service credential: MONOLITH'
    clients = {k: ArenaHTTPClient(url, basic_auth=creds.resolve_basic_auth(auth) if auth else None) for k, url, auth in [
        ('payouts', os.environ.get('PS_PUBLIC_URL', 'http://payouts-api:9400'), 'PS_SERVICE'),
        ('fts', os.environ.get('FTS_WEB_URL', 'http://fts-web:8080'), 'FTS'),
        ('ledger', os.environ.get('LEDGER_API_URL', 'http://ledger-api:8080'), 'LEDGER'),
        ('monolith', 'http://monolith-stub:8080', 'MONOLITH'),
        ('bank', os.environ.get('MOZART_MOCK_URL', 'http://mozart-sim:8085'), None),
        ('sink', 'http://merchant-webhook-sink:8080', None)]}
    # config/generate.py maps FTS users.api to the mounted synthetic monolith
    # credential; PS is not an admin-route caller (same identity route_scenarios uses).
    clients['fts_admin'] = ArenaHTTPClient(os.environ.get('FTS_WEB_URL', 'http://fts-web:8080'),
                                           basic_auth=('api_monolith', monolith_auth[1]))
    stores = {
        'payouts': db.connect_mysql('PAYOUTS_MYSQL_HOST', 'PAYOUTS_MYSQL_PORT', 'PAYOUTS_MYSQL_USER',
                                    'PAYOUTS_MYSQL_PASSWORD', 'PAYOUTS_MYSQL_DB',
                                    {'host': 'mysql-payouts', 'port': 3306, 'user': 'root', 'db': 'payouts'}),
        'fts': db.connect_mysql('FTS_MYSQL_HOST', 'FTS_MYSQL_PORT', 'FTS_MYSQL_USER',
                                'FTS_MYSQL_PASSWORD', 'FTS_MYSQL_DB',
                                {'host': 'mysql-fts', 'port': 3306, 'user': 'root', 'db': 'fts'}),
        'ledger': db.connect_postgres('LEDGER_PG_HOST', 'LEDGER_PG_PORT', 'LEDGER_PG_USER',
                                      'LEDGER_PG_PASSWORD', 'LEDGER_PG_DB',
                                      {'host': 'postgres-ledger', 'port': 5432, 'user': 'ledger', 'db': 'ledger'})}
    return index, clients, stores


def fixture(index, name):
    namespace, key, archetype = FIXTURES[name]
    if namespace == 'baseline':
        trio = index['baseline']
    else:
        matches = [n['merchants'] for n in index['namespaces'] if n['namespace'] == namespace]
        assert len(matches) == 1, 'generated namespace missing or ambiguous: ' + namespace
        trio = matches[0]
    merchant = dict(trio[key])
    assert merchant.get('archetype') == archetype, (name, namespace, key, merchant.get('archetype'))
    return namespace, key, merchant


def deliveries(clients, merchant, pid, events=TERMINAL_EVENTS):
    response = clients['sink'].get('/_arena/deliveries')
    assert response.status == 200, response
    return [d for d in response.json()['deliveries']
            if d.get('merchant') == merchant['merchant_id']
            and d.get('body', {}).get('event') in events
            and d.get('body', {}).get('payload', {}).get('payout', {}).get('entity', {}).get('id') == pid]


def journal_present(stores, pid, event):
    return pf.get_ledger_journal_row(stores['ledger'], pid, event) is not None


def hold_state(observe, predicted, seconds):
    """Sample the ending state for a bounded window; stop early on any divergence."""
    end = time.monotonic() + seconds
    observed = observe()
    held = 0.0
    started = time.monotonic()
    while observed == predicted and time.monotonic() < end:
        time.sleep(1)
        observed = observe()
        held = time.monotonic() - started
    return observed, round(held, 1)


def wait_transfer(stores, pid, status, timeout=90, require_utr=False):
    def ready():
        row = pf.transfer_metadata(stores['fts'], pid)
        if row['status'] != status:
            return None
        if require_utr and not row.get('utr'):
            return None
        return row
    return wait_until(ready, timeout=timeout, interval=.5,
                      desc='FTS transfer reaches %s%s' % (status, ' with UTR' if require_utr else ''))


def last_attempt(stores, transfer_id):
    return db.fetchone(stores['fts'], 'SELECT * FROM attempts WHERE transfer_id=%s ORDER BY id DESC LIMIT 1',
                       (transfer_id,))


def admin_return(clients, stores, pid, result):
    """The FTS admin reconciliation used by route_scenarios.py's `returned` case:
    normal polling refuses a terminal attempt, so raw verify observes the bank and
    safe_update independently re-verifies it before the PROCESSED -> REVERSED move."""
    transfer = pf.transfer_metadata(stores['fts'], pid)
    response = clients['fts'].post('/v1/transfer/%s/check' % transfer['id'], body={})
    assert response.status == 400 and response.json()['internal_error']['code'] == 'ILLEGAL_STATE', response
    attempt = last_attempt(stores, transfer['id'])
    assert attempt and attempt['status'] == 'PROCESSED', attempt
    aid = str(attempt['id'])
    response = clients['fts_admin'].post('/v1/attempts/verify', body={'attempt_ids': [aid]})
    assert response.status == 200, response
    verification = response.json()[aid]
    assert not verification.get('error'), verification
    raw = json.loads(verification['raw_status'])
    assert raw['meta']['failed'] is True and raw['meta']['error_type'] == 'BBANK', raw
    assert raw['data']['bank_status_code'] == 'RETURNED' and raw['data']['return_utr'], raw
    update = {aid: {'remarks': 'ArenaReturnReconciliation', 'bank_status_code': raw['data']['bank_status_code'],
                    'return_utr': raw['data']['return_utr'], 'meta': {'reversed': True}}}
    # Do not send status: UpdateAttemptStatus common.Fill copies request fields before
    # the state-machine event; meta selects the target.
    response = clients['fts_admin'].patch('/v1/attempts/safe_update', body=update)
    assert response.status == 200 and response.json()[aid]['status'] is True, response
    result['fts_attempt_id'] = aid
    result['observed_return_utr'] = raw['data']['return_utr']
    result['return_trigger'] = ('explicit FTS admin safe_update after raw bank verification; '
                                'safe_update independently re-verifies the bank')
    return transfer


def run_shared_source_failure(clients, stores, merchant, result, options):
    rule = clients['bank'].post('/_arena/scenario',
                                body={'merchant_id': merchant['merchant_id'], 'scenario': 'success', 'polls': 1})
    assert rule.status == 200, rule
    pid = pf.seed_payout(clients['payouts'], merchant, AMOUNT)
    result['payout_id'] = pid
    pf.wait_for_initiated(stores['payouts'], pid, timeout=60)
    transfer = wait_transfer(stores, pid, 'PROCESSED', timeout=120, require_utr=True)
    result['fts_transfer_id'] = transfer['id']
    result['fts_utr'] = transfer['utr']
    # The Shared debit itself is synchronous and unaffected: the defect is in the
    # asynchronous processed-event enqueue, not in the create path.
    assert journal_present(stores, pid, 'payout_initiated'), 'Shared payout_initiated journal missing'

    def observe():
        return {'payout_status': pf.get_payout_row(stores['payouts'], pid)['status'],
                'fts_transfer_status': pf.transfer_metadata(stores['fts'], pid)['status'],
                'payout_processed_journal': journal_present(stores, pid, 'payout_processed'),
                'terminal_webhook_count': len(deliveries(clients, merchant, pid))}

    predicted = {'payout_status': 'initiated', 'fts_transfer_status': 'PROCESSED',
                 'payout_processed_journal': False, 'terminal_webhook_count': 0}
    observed, held = hold_state(observe, predicted, options.shared_observation_seconds)
    result['predicted_state'] = predicted
    result['observed_state'] = observed
    result['matches_prediction'] = observed == predicted
    result['observation_seconds'] = options.shared_observation_seconds
    result['observation_held_seconds'] = held
    result['prediction'] = ('the Kafka consumer never registers jobs, so the Ledger processed-event enqueue fails and '
                            'core.go returns before OnEvent(EventProcessed): the payout stays initiated with the '
                            'merchant still debited, no payout_processed journal and no terminal webhook, while FTS '
                            'reports PROCESSED with a UTR')


def run_direct_after_shared(clients, stores, merchant, result, options):
    rule = clients['bank'].post('/_arena/scenario',
                                body={'merchant_id': merchant['merchant_id'], 'scenario': 'success', 'polls': 1})
    assert rule.status == 200, rule
    pid = pf.seed_payout(clients['payouts'], merchant, AMOUNT)
    result['payout_id'] = pid
    payout = pf.wait_for_status(stores['payouts'], pid, 'processed', timeout=30)
    transfer = pf.transfer_metadata(stores['fts'], pid)
    assert transfer['status'] == 'PROCESSED', transfer
    assert payout['utr'] and payout['utr'] == transfer['utr'], (payout, transfer)
    result['fts_transfer_id'] = transfer['id']
    result['utr'] = payout['utr']
    received = wait_until(lambda: deliveries(clients, merchant, pid, ('payout.processed',)) or None,
                          timeout=15, interval=.25, desc='signed payout.processed receipt')
    assert len(received) == 1, received
    assert received[0]['signature_present'] is True and received[0]['signature_valid'] is True, received
    assert received[0]['body']['account_id'] == 'acc_' + merchant['merchant_id'], received
    assert received[0]['body']['payload']['payout']['entity']['status'] == 'processed', received
    # Direct accounts return at core.go:2575-2577 before the Ledger enqueue.
    assert not journal_present(stores, pid, 'payout_initiated'), 'Direct payout must not write a Payouts journal'
    assert not journal_present(stores, pid, 'payout_processed'), 'Direct payout must not write a Payouts journal'
    assert pf.get_reversal_row(stores['payouts'], pid) is None
    result['observed_state'] = {'payout_status': payout['status'], 'fts_transfer_status': transfer['status'],
                                'payout_journals': 0, 'terminal_webhook_count': 1,
                                'terminal_webhook_signed': True}
    result['claim'] = ('the real Kafka consumer survived the Shared case, stayed in the group and delivered a Direct '
                       'terminal receipt: regression test for the nil-producer consumer crash')


def run_failed_dropped(clients, stores, merchant, result, options):
    rule = clients['bank'].post('/_arena/scenario',
                                body={'merchant_id': merchant['merchant_id'], 'scenario': 'failure', 'polls': 1})
    assert rule.status == 200, rule
    pid = pf.seed_payout(clients['payouts'], merchant, AMOUNT)
    result['payout_id'] = pid
    pf.wait_for_initiated(stores['payouts'], pid, timeout=60)
    transfer = wait_transfer(stores, pid, 'FAILED', timeout=120)
    result['fts_transfer_id'] = transfer['id']
    attempts = db.fetchall(stores['fts'], 'SELECT * FROM attempts WHERE transfer_id=%s ORDER BY id',
                           (transfer['id'],))
    assert len(attempts) == 1, attempts
    assert attempts[0]['status'] == 'FAILED', attempts
    assert attempts[0]['bank_status_code'] == 'INVALID_ACCOUNT_NUMBER', attempts
    result['bank_status_code'] = 'INVALID_ACCOUNT_NUMBER'
    result['fts_attempt_id'] = attempts[0]['id']

    def observe():
        return {'payout_status': pf.get_payout_row(stores['payouts'], pid)['status'],
                'fts_transfer_status': pf.transfer_metadata(stores['fts'], pid)['status'],
                'reversal_present': pf.get_reversal_row(stores['payouts'], pid) is not None,
                'payout_failed_journal': journal_present(stores, pid, 'payout_failed'),
                'payout_reversed_journal': journal_present(stores, pid, 'payout_reversed'),
                'terminal_webhook_count': len(deliveries(clients, merchant, pid))}

    predicted = {'payout_status': 'initiated', 'fts_transfer_status': 'FAILED', 'reversal_present': False,
                 'payout_failed_journal': False, 'payout_reversed_journal': False, 'terminal_webhook_count': 0}
    observed, held = hold_state(observe, predicted, options.observation_seconds)
    result['predicted_state'] = predicted
    result['observed_state'] = observed
    result['matches_prediction'] = observed == predicted
    result['observation_seconds'] = options.observation_seconds
    result['observation_held_seconds'] = held
    result['prediction'] = ('the consumer returns nil for FAILED before any handler runs and acknowledges the '
                            'message, so the failure is dropped permanently: the payout stays initiated with no '
                            'reversal row, no failed/reversed journal and no terminal webhook')


def run_reversed_dropped_direct(clients, stores, merchant, result, options):
    rule = clients['bank'].post('/_arena/scenario',
                                body={'merchant_id': merchant['merchant_id'], 'scenario': 'returned', 'polls': 1})
    assert rule.status == 200, rule
    pid = pf.seed_payout(clients['payouts'], merchant, AMOUNT)
    result['payout_id'] = pid
    payout = pf.wait_for_status(stores['payouts'], pid, 'processed', timeout=60)
    transfer = pf.transfer_metadata(stores['fts'], pid)
    assert transfer['status'] == 'PROCESSED' and payout['utr'] and payout['utr'] == transfer['utr'], (payout, transfer)
    result['fts_transfer_id'] = transfer['id']
    result['processed_before_return'] = True
    result['processed_webhook_count'] = len(deliveries(clients, merchant, pid, ('payout.processed',)))
    admin_return(clients, stores, pid, result)
    wait_transfer(stores, pid, 'REVERSED', timeout=90)

    def observe():
        return {'payout_status': pf.get_payout_row(stores['payouts'], pid)['status'],
                'fts_transfer_status': pf.transfer_metadata(stores['fts'], pid)['status'],
                'reversal_present': pf.get_reversal_row(stores['payouts'], pid) is not None,
                'payout_reversed_journal': journal_present(stores, pid, 'payout_reversed'),
                'reversed_webhook_count': len(deliveries(clients, merchant, pid, ('payout.reversed',)))}

    predicted = {'payout_status': 'processed', 'fts_transfer_status': 'REVERSED', 'reversal_present': False,
                 'payout_reversed_journal': False, 'reversed_webhook_count': 0}
    observed, held = hold_state(observe, predicted, options.observation_seconds)
    result['predicted_state'] = predicted
    result['observed_state'] = observed
    result['matches_prediction'] = observed == predicted
    result['observation_seconds'] = options.observation_seconds
    result['observation_held_seconds'] = held
    result['prediction'] = ('the consumer returns nil for REVERSED before any handler runs and acknowledges the '
                            'message, so the bank return is dropped permanently: the payout stays processed with no '
                            'reversal row, no payout_reversed journal and no payout.reversed webhook')


RUNNERS = {
    'shared_source_failure': run_shared_source_failure,
    'direct_after_shared': run_direct_after_shared,
    'failed_dropped_direct': run_failed_dropped,
    'failed_dropped_shared': run_failed_dropped,
    'reversed_dropped_direct': run_reversed_dropped_direct,
}


def case(name, index, clients, stores, options):
    namespace, key, merchant = fixture(index, name)
    result = {'name': name, 'fixture': merchant, 'fixture_namespace': namespace, 'fixture_key': key,
              'archetype': merchant['archetype'], 'route_profile': index['route_profile'], 'status': 'failed',
              'transport': 'real FTS Kafka producer -> real payouts Kafka consumer; no message is published by '
                           'the verifier and no Kafka client library exists in this image'}
    if name in EXPECTED_FAILURES:
        result['expected_failure_ref'] = EXPECTED_FAILURES[name]
        result['matches_prediction'] = False
    trace.select('kafka-' + name)
    trace.record('starting_fixture', {'case': name, 'namespace': namespace, 'key': key, 'merchant': merchant,
                                      'route_profile': index['route_profile']})
    try:
        starting = pf.get_account_balance(stores['ledger'], merchant['merchant_id'])
        result['starting_ledger_balance'] = str(starting) if starting is not None else None
        result['starting_payouts'] = db.fetchall(stores['payouts'],
                                                 'SELECT id,status,amount FROM payouts WHERE merchant_id=%s ORDER BY id',
                                                 (merchant['merchant_id'],))
        RUNNERS[name](clients, stores, merchant, result, options)
        if name in EXPECTED_FAILURES:
            result['status'] = 'expected_failure' if result.get('matches_prediction') is True else 'failed'
        else:
            result['status'] = 'passed'
    except BaseException as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        result['status'] = 'failed'
        if name in EXPECTED_FAILURES:
            result['matches_prediction'] = False
    finally:
        try:
            cleared = clients['bank'].post('/_arena/scenario',
                                           body={'merchant_id': merchant['merchant_id'], 'clear': True}, timeout=3)
            assert cleared.status == 200, cleared
        except Exception as exc:
            result['cleanup_error'] = {'type': type(exc).__name__, 'message': str(exc)}
            result['status'] = 'failed'
            if name in EXPECTED_FAILURES:
                result['matches_prediction'] = False
        if result.get('payout_id'):
            try:
                snapshot = record_snapshot(stores['payouts'], stores['fts'], stores['ledger'], clients['sink'],
                                           result['payout_id'], bank_client=clients['bank'],
                                           monolith_client=clients['monolith'], label='kafka_scenario_completion')
                result['snapshot_complete'] = snapshot['complete']
                if not snapshot['complete']:
                    result['status'] = 'failed'
                    result['snapshot_errors'] = snapshot['errors']
                    if name in EXPECTED_FAILURES:
                        result['matches_prediction'] = False
            except Exception as exc:
                result['status'] = 'failed'
                result['snapshot_complete'] = False
                result['snapshot_error'] = {'type': type(exc).__name__, 'message': str(exc)}
                if name in EXPECTED_FAILURES:
                    result['matches_prediction'] = False
        trace.record('kafka_result', result)
    return trace.clean(result)


def case_ok(result):
    if result['name'] in EXPECTED_FAILURES:
        return result['status'] == 'expected_failure' and result.get('matches_prediction') is True
    return result['status'] == 'passed'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=['all', *CASES], default='all')
    parser.add_argument('--output', default=str(OUT / 'kafka-results.json'))
    parser.add_argument('--observation-seconds', type=int, default=20,
                        help='bounded drop-observation window for the failed/reversed cases')
    parser.add_argument('--shared-observation-seconds', type=int, default=45,
                        help='bounded observation window for shared_source_failure')
    parser.add_argument('--case-timeout', type=int, default=240)
    options = parser.parse_args()
    if not 5 <= options.observation_seconds <= 120 or not 5 <= options.shared_observation_seconds <= 180:
        parser.error('observation windows must be 5..120 and 5..180 seconds')
    if not 60 <= options.case_timeout <= 900:
        parser.error('case timeout must be 60..900 seconds')
    selected = list(CASES) if options.case == 'all' else [options.case]
    if 'direct_after_shared' in selected and 'shared_source_failure' not in selected:
        parser.error('direct_after_shared is the regression test for the consumer crash the Shared case triggers '
                     'and must run after shared_source_failure in the same boot: use --case all')

    OUT.mkdir(parents=True, exist_ok=True)
    output = Path(options.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    import network_check
    assert network_check.main() == 0, 'runtime DNS checks failed'
    index, clients, stores = setup()
    results = []
    try:
        for name in selected:
            signal.signal(signal.SIGALRM, deadline)
            signal.alarm(options.case_timeout)
            try:
                results.append(case(name, index, clients, stores, options))
            finally:
                signal.alarm(0)
            payload = {'status': 'passed' if all(case_ok(r) for r in results) else 'failed',
                       'route_profile': index['route_profile'],
                       'cases': results}
            output.write_text(json.dumps(payload, indent=2, default=str) + '\n')
            print('%s: %s (matches_prediction=%s)'
                  % (name, results[-1]['status'], results[-1].get('matches_prediction')), flush=True)
    finally:
        for connection in stores.values():
            try:
                connection.close()
            except Exception:  # noqa: BLE001 -- connection teardown must not mask a case result
                pass
    return 0 if results and all(case_ok(r) for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
