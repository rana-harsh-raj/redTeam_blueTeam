"""Snapshot transport-type regressions; no service or database is started."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from helpers import trace_snapshot


class Response:
    status=200
    def json(self): return {'deliveries':[],'events':[]}


class Client:
    def get(self,*_,**__): return Response()


class SnapshotTests(unittest.TestCase):
    def test_mysql_tuple_rows_include_empty_reversals_and_full_attempt_chain(self):
        payout={'id':'ARENAPAYOUT001','merchant_id':'ARENAM00000001','transaction_id':None}
        def fetchall(conn,sql,params):
            self.assertTrue(sql.endswith(' LIMIT %s'))
            if 'FROM transfers ' in sql: return ({'id':101},)
            if 'FROM attempts ' in sql: return ({'id':201,'transfer_id':101},{'id':202,'transfer_id':101})
            if 'FROM payout_logs ' in sql: return ({'id':'ARENALOG000001'},)
            return ()  # Actual PyMySQL empty fetchall return type.
        with patch.object(trace_snapshot.db,'fetchone',return_value=payout), \
             patch.object(trace_snapshot.db,'fetchall',side_effect=fetchall), \
             patch.object(trace_snapshot.trace,'record') as record:
            result=trace_snapshot.record_snapshot(object(),object(),object(),Client(),
                                                  'pout_'+payout['id'],bank_client=Client())
        self.assertTrue(result['complete'],result['errors'])
        self.assertEqual(result['layers']['payouts']['reversals'],[])
        self.assertEqual([a['id'] for a in result['layers']['fts']['attempt_chain']],[201,202])
        self.assertEqual(result['layers']['ledger']['transactor_ids'],['pout_'+payout['id']])
        self.assertEqual(result['layers']['ledger']['journals'],[])
        record.assert_called_once()


if __name__=='__main__': unittest.main()
