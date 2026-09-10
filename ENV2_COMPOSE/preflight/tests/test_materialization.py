"""Generated-input scoping checks use temporary fake credentials only."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('materialize',Path(__file__).resolve().parents[2]/'secrets/materialize.py')
materialize=importlib.util.module_from_spec(spec)
spec.loader.exec_module(materialize)


class MaterializationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.root_patch=patch.object(materialize,'ROOT',self.root);self.root_patch.start();self.addCleanup(self.root_patch.stop)
        for group in materialize.CONFIG_GROUPS:
            self.write('generated/'+group+'/nested/arena.toml',b'synthetic=true\n')
            self.write('generated/'+group+'/'+group+'.env',b'HOST_ONLY=synthetic\n')
        self.write('secrets/merchant-keys/merchant_test_secret.txt',b'fake-merchant')
        self.write('secrets/passport_private_key.txt',b'fake-signer-not-a-key')
        self.write('secrets/verifier-bridge/monolith',b'fake:auth')
        for name in ('auth_api_payouts','auth_payouts_ledger','auth_monolith_payouts_db','auth_monolith_balance_db'):
            self.write('secrets/'+name+'.txt',b'fake-auth')
        self.write('secrets/unrelated_datastore_password.txt',b'must-not-be-selected')

    def write(self,path,data):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data);target.chmod(0o600)

    def test_exact_consumer_scope_and_nested_configuration(self):
        groups=materialize.source_groups()
        self.assertEqual(len(groups),8)
        for group in materialize.CONFIG_GROUPS:
            self.assertEqual(set(groups['config-'+group]),{'nested/arena.toml'})
        self.assertEqual(set(groups['secrets-kong']),{'merchants/merchant_test_secret.txt','passport_private_key','auth_api_payouts'})
        self.assertEqual(set(groups['secrets-monolith']),{'monolith_basic_auth','auth_api_payouts','auth_payouts_ledger','auth_monolith_payouts_db','auth_monolith_balance_db'})
        self.assertNotIn(b'must-not-be-selected',[value for files in groups.values() for value in files.values()])
        self.assertTrue(all(path.stat().st_mode&0o777==0o600 for path in self.root.rglob('*') if path.is_file()))

    def test_symlink_input_is_rejected_before_reading(self):
        target=self.root/'secrets/passport_private_key.txt';target.unlink()
        target.symlink_to(self.root/'secrets/unrelated_datastore_password.txt')
        with self.assertRaisesRegex(ValueError,'Symlink'):
            materialize.source_groups()


if __name__=='__main__': unittest.main()
