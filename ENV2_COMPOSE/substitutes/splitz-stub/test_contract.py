"""Remote-evaluation response contract and explicit default-off behavior."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
spec=importlib.util.spec_from_file_location('arena_splitz',Path(__file__).with_name('server.py'))
stub=importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)

class SplitzContract(unittest.TestCase):
    def test_unknown_experiment_is_off_for_both_callers(self):
        with patch.object(stub,'DEFAULT_VARIANT','off'):
            response=stub._one_evaluate({'experiment_id':'UNKNOWN','id':'M1'})
        self.assertEqual(response['variant']['name'],'off')
        self.assertEqual({v['key']:v['value'] for v in response['variant']['variables']},{'result':'off','enabled':'false'})

    def test_variant_is_merchant_scoped(self):
        record={'name':'gate','variants':{'M1':'on'}}
        with patch.object(stub,'DEFAULT_VARIANT','off'),patch.object(stub,'EXPERIMENTS_BY_ID',{'E1':record}):
            on=stub._one_evaluate({'experiment_id':'E1','id':'M1'})
            off=stub._one_evaluate({'experiment_id':'E1','id':'M2'})
        self.assertEqual(on['variant']['name'],'on')
        self.assertEqual(off['variant']['name'],'off')
        self.assertEqual({v['key']:v['value'] for v in on['variant']['variables']},{'result':'on','enabled':'true'})

    def test_name_alias_preserves_seeded_variant(self):
        with patch.object(stub,'EXPERIMENTS_BY_NAME',{'gate':{'id':'E1','name':'gate','variants':{'M1':'on'}}}):
            response=stub._one_evaluate({'experiment_name':'gate','id':'M1'})
        self.assertEqual(response['experiment']['id'],'E1')
        self.assertEqual(response['variant']['name'],'on')

if __name__=='__main__': unittest.main()
