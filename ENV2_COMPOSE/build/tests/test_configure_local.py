import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('configure_local',Path(__file__).resolve().parents[1]/'configure-local.py')
configure=importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ConfigureLocalTests(unittest.TestCase):
    def migration_fixture(self, root):
        repositories={}
        for relative in configure.MIGRATIONS.values():
            repository=Path(relative).parts[0]
            directory=root/relative;directory.mkdir(parents=True)
            entries=[]
            for name,content in [('0001_table.go',b'package migrations\n'),('0002_table.sql',b'CREATE TABLE synthetic(id INT);\n')]:
                path=directory/name;path.write_bytes(content);path.chmod(0o600)
                entries.append({'path':path.relative_to(root/repository).as_posix(),'sha256':hashlib.sha256(content).hexdigest()})
            (directory/'.env').write_text('EXCLUDED=fake\n')
            repositories[repository]={'name':repository,'patched_copy_manifest':{'files':entries}}
        provenance=root/'provenance.json'
        provenance.write_text(json.dumps({'status':'prepared','destination':str(root),'repositories':list(repositories.values())}))
        return provenance

    def test_preserves_unknown_settings_and_comments_and_normalizes_managed_duplicates(self):
        original='# retain comment\nCUSTOM="keep me"\nARENA_TAG=old\nARENA_TAG=older\nPORT=18080\n'
        result=configure.render(original,{'ARENA_TAG':'candidate','FTS_REPO_CONFIG_DIR':'/new path/config'})
        self.assertIn('# retain comment\nCUSTOM="keep me"\n',result)
        self.assertIn('PORT=18080\n',result)
        self.assertEqual(result.count('ARENA_TAG='),1)
        self.assertIn("FTS_REPO_CONFIG_DIR='/new path/config'",result)
        self.assertEqual(configure.render(result,{'ARENA_TAG':'candidate','FTS_REPO_CONFIG_DIR':'/new path/config'}),result)

    def test_atomic_write_preserves_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'.env.arena';target.write_text('PORT=18080\n');target.chmod(0o600)
            configure.write(target,'PORT=18080\nARENA_TAG=test\n')
            self.assertEqual(target.stat().st_mode&0o777,0o600)
            self.assertEqual(target.read_text(),'PORT=18080\nARENA_TAG=test\n')
            self.assertEqual([p.name for p in Path(directory).iterdir()],['.env.arena'])

    def test_rejects_symlink_target(self):
        with tempfile.TemporaryDirectory() as directory:
            original=Path(directory)/'original';original.write_text('unchanged')
            target=Path(directory)/'.env.arena';target.symlink_to(original)
            with self.assertRaisesRegex(ValueError,'symlink'): configure.write(target,'changed')
            self.assertEqual(original.read_text(),'unchanged')

    def test_rejects_missing_input_and_multiline_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'Missing prepared repository'): configure.settings(Path(directory),'candidate')
        with self.assertRaisesRegex(ValueError,'Multiline'): configure.quote('/path\nINJECT=value')

    def test_stages_only_admitted_schema_assets_without_changing_source_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);provenance=self.migration_fixture(root)
            plan=configure.migration_plan(root,provenance)
            destination=root/'runtime-migrations'
            configure.stage_migrations(destination,plan)
            configure.stage_migrations(destination,plan)  # verified idempotent reuse
            self.assertEqual(len(plan['files']),8)
            self.assertFalse(any(path.name=='.env' for path in destination.rglob('*')))
            self.assertEqual(destination.stat().st_mode&0o777,0o700)
            for item in plan['files']:
                self.assertEqual(Path(item['source']).stat().st_mode&0o777,0o600)
                self.assertEqual((destination/item['path']).stat().st_mode&0o777,0o444)

    def test_rejects_unadmitted_source_and_never_repairs_a_changed_existing_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);provenance=self.migration_fixture(root)
            plan=configure.migration_plan(root,provenance);destination=root/'runtime-migrations'
            configure.stage_migrations(destination,plan)
            target=destination/plan['files'][0]['path'];target.chmod(0o644)
            with self.assertRaisesRegex(ValueError,'content/mode'): configure.stage_migrations(destination,plan)
            self.assertEqual(target.stat().st_mode&0o777,0o644)
            Path(plan['files'][0]['source']).write_text('changed source')
            with self.assertRaisesRegex(ValueError,'hash-matched'): configure.migration_plan(root,provenance)


if __name__=='__main__': unittest.main()
