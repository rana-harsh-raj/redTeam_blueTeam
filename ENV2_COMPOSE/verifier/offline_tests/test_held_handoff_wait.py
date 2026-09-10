"""Hold completion must follow the bank response and the real details callback."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from helpers import payouts_flow as pf, wait


class HeldHandoffWait(unittest.TestCase):
    def run_clock(self, bank_at=1, handoff_at=2, channel='rbl'):
        now = [0.0]
        def sleep(seconds):
            now[0] += seconds
        def transfer(*_):
            return {'id': 8, 'status': 'INITIATED' if now[0] >= bank_at else 'CREATED', 'channel': 'RBL'}
        def attempt(*_):
            return {'id': 9, 'transfer_id': 8, 'status': 'INITIATED',
                    'bank_status_code': 'INITIATED' if now[0] >= bank_at else '',
                    'bank_response_received': True}  # SetDefaults may set this before response
        def payout(*_):
            return {'status': 'initiated', 'fts_transfer_id': 8 if now[0] >= handoff_at else 0,
                    'channel': channel if now[0] >= handoff_at else 'yesbank'}
        with patch.dict('os.environ', {'ARENA_WAIT_SCALE': '1'}), \
             patch.object(wait.time, 'monotonic', side_effect=lambda: now[0]), \
             patch.object(wait.time, 'sleep', side_effect=sleep), \
             patch.object(pf, 'transfer_metadata', side_effect=transfer), \
             patch.object(pf.db, 'fetchone', side_effect=attempt), \
             patch.object(pf, 'get_payout_row', side_effect=payout):
            result = pf.wait_for_held_handoff(None, None, 'pout_HELD')
            return result, now[0]

    def test_attempt_existence_and_initiated_state_are_insufficient(self):
        row, elapsed = self.run_clock()
        self.assertEqual(elapsed, 2)
        self.assertEqual(row['fts_transfer_id'], 8)

    def test_no_bank_response_fails_at_bound(self):
        with self.assertRaisesRegex(wait.WaitTimeout, 'held bank response'):
            self.run_clock(bank_at=float('inf'))

    def test_no_details_callback_fails_at_bound(self):
        with self.assertRaisesRegex(wait.WaitTimeout, 'actual held FTS transfer and channel'):
            self.run_clock(handoff_at=float('inf'))

    def test_mismatched_channel_is_not_accepted(self):
        with self.assertRaisesRegex(wait.WaitTimeout, 'actual held FTS transfer and channel'):
            self.run_clock(channel='yesbank')


if __name__ == '__main__':
    unittest.main()
