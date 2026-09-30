"""Integration tests; use a temporary data directory, never the user's database."""
import json
from contextlib import closing
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class CoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Тору test ')
        self.storage = app.Storage(self.temp.name)
        self.server = app.make_server(self.storage, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)
        with urllib.request.urlopen(self.url) as response:
            self.html = response.read().decode('utf-8')
        self.token = re.search(r'name="tooru-token" content="([^"]+)"', self.html)[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path,
            data=None if data is None else json.dumps(data).encode('utf-8'),
            headers=headers if headers is not None else {'X-Tooru-Token': self.token})
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_persistence_unicode_delete_backup(self):
        value = self.request('/api/records', {'kind': 'memory', 'title': 'Тори 🐉', 'body': 'Дом и работа — ё'})
        reopened = app.Storage(self.temp.name)
        self.assertEqual(reopened.state()['records']['memory'][0]['body'], 'Дом и работа — ё')
        backup = self.request('/api/backup', {})['filename']
        with closing(sqlite3.connect(Path(self.temp.name) / 'backups' / backup)) as db:
            self.assertEqual(db.execute('SELECT title FROM records').fetchone()[0], 'Тори 🐉')
        self.request('/api/delete', {'id': value['id']})
        self.assertEqual(self.request('/api/state')['records']['memory'], [])
        self.assertEqual(self.request('/api/diagnostics')['database'], 'ok')

    def test_all_modules_settings_and_validation(self):
        for kind in app.KINDS:
            self.request('/api/records', {'kind':kind, 'title':'Тест'})
        self.request('/api/settings', {'name':'Аспкса', 'theme':'dark'})
        state = self.request('/api/state')
        self.assertEqual(state['settings']['name'], 'Аспкса')
        self.assertEqual(set(state['counts']), app.KINDS)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/records', {'kind':'wrong', 'title':'Тест'})
        self.assertEqual(error.exception.code, 400)

    def test_local_security_and_private_files(self):
        for headers in ({}, {'X-Tooru-Token': self.token, 'Origin':'https://example.com'},
                        {'X-Tooru-Token': self.token, 'Host':'example.com'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/records', {'kind':'memory', 'title':'forbidden'}, headers)
            self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/data/tooru.sqlite3')
        self.assertEqual(error.exception.code, 404)
        self.assertEqual(self.storage.state()['records']['memory'], [])

    def test_duplicate_launcher_and_lock(self):
        lock = app.InstanceLock(Path(self.temp.name) / 'instance.lock')
        self.assertTrue(lock.acquire())
        try:
            (Path(self.temp.name) / 'server.json').write_text(json.dumps({'port': self.server.server_port}))
            result = subprocess.run([sys.executable, '-X', 'utf8', str(app.ROOT / 'app.py'),
                '--no-browser', '--data-dir', self.temp.name], capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('уже запущена', result.stdout.decode('utf-8'))
        finally:
            lock.close()


class PortableTest(unittest.TestCase):
    def test_move_folder_keep_data_and_launch_from_other_directory(self):
        with tempfile.TemporaryDirectory(prefix='Тори перенос ') as temporary:
            base = Path(temporary)
            original = base / 'Первый диск' / 'TOORU DRAGON'
            original.mkdir(parents=True)
            shutil.copy2(app.ROOT / 'app.py', original / 'app.py')
            shutil.copy2(app.ROOT / 'StartTooruDragon.bat', original / 'StartTooruDragon.bat')
            shutil.copytree(app.ROOT / 'web', original / 'web')
            # On Windows exercise the relocated embedded interpreter as well.
            if sys.platform == 'win32' and (app.ROOT / 'python' / 'python.exe').exists():
                shutil.copytree(app.ROOT / 'python', original / 'python')
            for folder in (original, base / 'Другой носитель' / 'Тори с пробелами'):
                if folder != original:
                    folder.parent.mkdir(parents=True)
                    shutil.move(str(original), str(folder))
                interpreter = folder / 'python' / 'python.exe'
                command = [str(interpreter) if interpreter.exists() else sys.executable,
                           '-X', 'utf8', str(folder / 'app.py'), '--no-browser', '--port', '0']
                process = subprocess.Popen(command, cwd=base, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    url = None
                    for _ in range(100):
                        if process.poll() is not None:
                            self.fail(process.communicate()[1].decode('utf-8', errors='replace'))
                        try:
                            port = json.loads((folder / 'data' / 'server.json').read_text())['port']
                            candidate = f'http://127.0.0.1:{port}'
                            with urllib.request.urlopen(candidate + '/health', timeout=.3) as response:
                                if json.load(response)['app'] == 'TOORU-DRAGON':
                                    url = candidate
                                    break
                        except (OSError, ValueError, KeyError):
                            time.sleep(.1)
                    self.assertIsNotNone(url, 'Перенесённый сервер не запустился')
                    with urllib.request.urlopen(url) as response:
                        html = response.read().decode('utf-8')
                    token = re.search(r'name="tooru-token" content="([^"]+)"', html)[1]
                    headers = {'X-Tooru-Token': token}
                    if folder == original:
                        payload = json.dumps({'kind':'memory','title':'Помнить после переноса','body':'Ёж 🐉'}).encode('utf-8')
                        with urllib.request.urlopen(urllib.request.Request(url + '/api/records', payload, headers)) as response:
                            self.assertEqual(response.status, 201)
                    else:
                        with urllib.request.urlopen(urllib.request.Request(url + '/api/state', headers=headers)) as response:
                            self.assertEqual(json.load(response)['records']['memory'][0]['body'], 'Ёж 🐉')
                    if sys.platform == 'win32' and interpreter.exists():
                        # Invoke the actual BAT from an unrelated cwd, including Unicode/spaces.
                        batch = f'call "{folder / "StartTooruDragon.bat"}" --no-browser'
                        result = subprocess.run(['cmd.exe', '/d', '/c', batch], cwd=base,
                                                capture_output=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse((base / 'data').exists(), 'Данные созданы относительно cwd вместо проекта')
                finally:
                    process.terminate()
                    process.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
