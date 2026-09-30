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
from unittest.mock import patch
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

    def test_qwen_profile_launch_and_security(self):
        executable = Path(self.temp.name) / 'Browser with spaces.exe'
        with patch('app.find_browser', return_value=executable), patch('app.subprocess.Popen') as launch:
            for headers in ({}, {'X-Tooru-Token': self.token, 'Origin': 'https://example.com'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('/api/qwen/open', {}, headers)
                self.assertEqual(error.exception.code, 403)
            launch.assert_not_called()
            result = self.request('/api/qwen/open', {})
            self.assertIn('Браузер Тори открыт', result['message'])
            args = launch.call_args.args[0]
            profile = Path(self.temp.name).resolve() / 'browser-profile' / 'chrome'
            self.assertTrue(profile.is_dir())
            self.assertEqual(args[0], str(executable))
            self.assertEqual(args[1], '--user-data-dir=' + str(profile))
            self.assertTrue(args[2].startswith('--load-extension='))
            self.assertEqual(args[3], '--new-window')
            self.assertTrue(args[4].startswith('https://chat.qwen.ai/#'))
            self.assertFalse(launch.call_args.kwargs.get('shell', False))
            self.assertEqual(self.request('/api/state')['qwen']['mode'], 'observe')
            self.assertFalse(self.request('/api/state')['qwen_connected'])
            launch.side_effect = OSError('failure')
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/qwen/open', {})
            self.assertEqual(error.exception.code, 400)
        with patch('app.find_browser', side_effect=ValueError('Браузер не найден')):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/api/qwen/open', {})
            self.assertEqual(error.exception.code, 400)

    def test_browser_discovery_and_invalid_selection(self):
        with patch('app.sys.platform', 'win32'), patch.dict('app.os.environ',
                {'LOCALAPPDATA': self.temp.name}, clear=True):
            for invalid in ('firefox', '../chrome', 'edge', [], None):
                with self.assertRaises(ValueError):
                    app.find_browser(invalid)
            with self.assertRaises(ValueError):
                app.find_browser('chrome')
            executable = Path(self.temp.name) / 'Google/Chrome/Application/chrome.exe'
            executable.parent.mkdir(parents=True)
            executable.touch()
            self.assertEqual(app.find_browser('chrome'), executable)


    def test_qwen_bridge_queue_handoff_and_knowledge(self):
        self.request('/api/qwen/control', {'action': 'start'})
        self.request('/api/qwen/control', {'action': 'handoff'})
        queued = self.request('/api/qwen/queue', {'topic': 'Python', 'question': 'Что такое WAL?'})
        bridge_headers = {
            'X-Tooru-Bridge': self.storage.bridge_token(),
            'Origin': 'chrome-extension://tooru-test',
        }
        poll = self.request('/api/qwen/bridge', {'action': 'poll'}, bridge_headers)
        self.assertEqual(poll['owner'], 'tori')
        self.assertEqual(poll['item']['id'], queued['id'])
        self.request('/api/qwen/bridge', {'action': 'claim', 'queue_id': queued['id']}, bridge_headers)
        event = {
            'action': 'event', 'role': 'assistant', 'text': 'WAL — журнал предзаписи.',
            'source_url': 'https://chat.qwen.ai/c/test', 'queue_id': queued['id'],
        }
        first = self.request('/api/qwen/bridge', event, bridge_headers)
        second = self.request('/api/qwen/bridge', event, bridge_headers)
        self.assertTrue(first['inserted'])
        self.assertFalse(second['inserted'])
        state = self.request('/api/state')
        self.assertTrue(state['qwen_connected'])
        status = self.request('/api/qwen/status')
        self.assertTrue(status['connected'])
        self.assertEqual(status['memory_count'], 0)
        self.assertEqual(status['knowledge_count'], 1)
        self.assertEqual(state['qwen']['queue'][0]['status'], 'done')
        self.assertEqual(state['records']['knowledge'][0]['source'], 'Qwen · https://chat.qwen.ai/c/test')
        self.assertIn('WAL', state['records']['knowledge'][0]['body'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/qwen/bridge', {'action': 'heartbeat'},
                         {'X-Tooru-Bridge': 'wrong', 'Origin': 'chrome-extension://tooru-test'})
        self.assertEqual(error.exception.code, 403)

    def test_qwen_extension_files_are_valid(self):
        extension = app.ROOT / 'browser' / 'qwen-bridge'
        manifest = json.loads((extension / 'manifest.json').read_text('utf-8'))
        self.assertEqual(manifest['manifest_version'], 3)
        self.assertIn('https://chat.qwen.ai/*', manifest['content_scripts'][0]['matches'])
        self.assertTrue((extension / 'background.js').is_file())
        self.assertTrue((extension / 'content.js').is_file())

    def test_learning_ui_uses_tori_browser_language(self):
        ui = (app.ROOT / 'web' / 'app.js').read_text('utf-8')
        self.assertIn('Открыть Браузер Тори', ui)
        self.assertIn('Текущая сессия', ui)
        self.assertIn('Журнал обучения', ui)
        self.assertNotIn('Открыть Chrome Тори', ui)
        self.assertNotIn('data/browser-profile/chrome', ui)

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
            shutil.copytree(app.ROOT / 'browser', original / 'browser')
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
                        # cmd.exe needs its own quoting, not list2cmdline's C-runtime escapes.
                        result = subprocess.run('cmd.exe /d /c ' + batch, cwd=base,
                                                capture_output=True, timeout=20)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse((base / 'data').exists(), 'Данные созданы относительно cwd вместо проекта')
                finally:
                    process.terminate()
                    process.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
