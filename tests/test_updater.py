import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

import updater


class UpdaterTest(unittest.TestCase):
    def test_protected_local_data_and_python_are_never_managed(self):
        self.assertFalse(updater.is_managed(Path('data/tooru.sqlite3')))
        self.assertFalse(updater.is_managed(Path('python/python.exe')))
        self.assertFalse(updater.is_managed(Path('backups/system/file.txt')))
        self.assertFalse(updater.is_managed(Path('.git/config')))
        self.assertFalse(updater.is_managed(Path('UpdateTooruDragon.bat')))
        self.assertTrue(updater.is_managed(Path('app.py')))
        self.assertTrue(updater.is_managed(Path('web/app.js')))

    def test_safe_extract_rejects_parent_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            archive = base / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as zf:
                zf.writestr('../escape.txt', 'bad')
            with self.assertRaisesRegex(RuntimeError, 'небезопасный путь'):
                updater.safe_extract(archive, base / 'extract')
            self.assertFalse((base / 'escape.txt').exists())

    def test_apply_source_preserves_user_data_and_backs_up_changed_system_files(self):
        with tempfile.TemporaryDirectory(prefix='tooru-updater-') as temporary:
            base = Path(temporary)
            root = base / 'root'
            source = base / 'source'
            root.mkdir()
            source.mkdir()

            (root / 'data').mkdir()
            (root / 'python').mkdir()
            (root / 'data' / 'tooru.sqlite3').write_bytes(b'user-db')
            (root / 'python' / 'python.exe').write_bytes(b'portable-python')
            (root / 'app.py').write_text('old app', 'utf-8')
            (root / 'obsolete.txt').write_text('old file', 'utf-8')
            (root / updater.MANIFEST_REL).write_text(
                json.dumps(['app.py', 'obsolete.txt']), 'utf-8'
            )

            (source / 'app.py').write_text('new app', 'utf-8')
            (source / 'StartTooruDragon.bat').write_text('@echo off', 'utf-8')
            (source / 'UpdateTooruDragon.bat').write_text('@echo protected', 'utf-8')
            (source / 'VERSION').write_text('0.0.0', 'utf-8')
            (source / 'web').mkdir()
            (source / 'web' / 'app.js').write_text('new js', 'utf-8')
            (source / 'data').mkdir()
            (source / 'data' / 'should-not-copy.txt').write_text('no', 'utf-8')

            backup = updater.apply_source(root, source, 'abc123456789')

            self.assertEqual((root / 'app.py').read_text('utf-8'), 'new app')
            self.assertFalse((root / 'obsolete.txt').exists())
            self.assertEqual((root / 'data' / 'tooru.sqlite3').read_bytes(), b'user-db')
            self.assertEqual((root / 'python' / 'python.exe').read_bytes(), b'portable-python')
            self.assertFalse((root / 'data' / 'should-not-copy.txt').exists())
            self.assertFalse((root / 'UpdateTooruDragon.bat').exists())
            self.assertEqual((backup / 'app.py').read_text('utf-8'), 'old app')
            self.assertEqual((backup / 'obsolete.txt').read_text('utf-8'), 'old file')
            state = json.loads((root / updater.STATE_REL).read_text('utf-8'))
            self.assertEqual(state['revision'], 'abc123456789')

    def test_revision_changes_reports_file_status_and_protection(self):
        payload = {
            'files': [
                {'filename': 'web/app.js', 'status': 'modified'},
                {'filename': 'new.txt', 'status': 'added'},
                {'filename': 'UpdateTooruDragon.bat', 'status': 'modified'},
            ]
        }
        with patch('updater.request_json', return_value=payload):
            files = updater.revision_changes('a' * 40, 'b' * 40)
        self.assertEqual(
            [item['status_label'] for item in files],
            ['изменён', 'добавлен', 'изменён']
        )
        self.assertTrue(files[0]['will_update'])
        self.assertTrue(files[1]['will_update'])
        self.assertFalse(files[2]['will_update'])

    def test_latest_revision_separates_title_and_description(self):
        payload = {
            'commit': {
                'sha': 'c' * 40,
                'commit': {'message': 'Improve updater UI\\n\\nShow description and changed files.'},
            }
        }
        with patch('updater.request_json', return_value=payload):
            revision = updater.latest_revision()
        self.assertEqual(revision['message'], 'Improve updater UI')
        self.assertEqual(revision['description'], 'Show description and changed files.')

    def test_local_status_uses_commit_sha_not_only_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'VERSION').write_text('0.0.0', 'utf-8')
            (root / updater.STATE_REL).parent.mkdir(parents=True)
            (root / updater.STATE_REL).write_text(
                json.dumps({'revision': 'oldsha'}), 'utf-8'
            )
            with patch('updater.latest_revision', return_value={
                'sha': 'b' * 40, 'message': 'Fresh commit', 'description': 'Details'
            }), patch('updater.revision_changes', return_value=[
                {
                    'path': 'web/app.js',
                    'status': 'modified',
                    'status_label': 'изменён',
                    'will_update': True,
                    'previous_path': '',
                }
            ]):
                status = updater.local_status(root)
            self.assertTrue(status['tracked'])
            self.assertTrue(status['update_available'])
            self.assertEqual(status['version'], '0.0.0')
            self.assertEqual(status['latest_revision'], 'b' * 40)
            self.assertEqual(status['latest_description'], 'Details')
            self.assertEqual(status['files'][0]['path'], 'web/app.js')


if __name__ == '__main__':
    unittest.main()
