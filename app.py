"""TOORU · DRAGON: portable, loopback-only application. Standard library only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, closing
import hashlib
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
VERSION = '0.0.0'
KINDS = {'work', 'home', 'memory', 'knowledge', 'topic', 'chat'}


class Storage:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'tooru.sqlite3'
        for name in ('documents', 'models', 'browser-profile', 'logs', 'backups'):
            (self.directory / name).mkdir(exist_ok=True)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 1:
                raise RuntimeError('База создана более новой версией TOORU. Обновите программу.')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS records (
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                    title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'Пользователь',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS records_kind_id ON records(kind, id DESC);
                PRAGMA user_version=1;
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('name', 'Aspksa'))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('theme', 'system'))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self):
        with self.connect() as db:
            settings = dict(db.execute('SELECT key, value FROM settings').fetchall())
            counts = dict(db.execute('SELECT kind, count(*) FROM records GROUP BY kind').fetchall())
            records = {kind: [dict(row) for row in db.execute(
                'SELECT * FROM records WHERE kind=? ORDER BY id DESC LIMIT 200', (kind,)
            )] for kind in sorted(KINDS)}
        return dict(version=VERSION, settings=settings, counts=counts, records=records,
                    ai_connected=False, qwen_connected=False)

    def add(self, item):
        kind, title = item.get('kind'), item.get('title', '')
        body = item.get('body', '')
        if kind not in KINDS or not isinstance(title, str) or not title.strip() or len(title) > 300:
            raise ValueError('Укажите название длиной от 1 до 300 символов.')
        if not isinstance(body, str) or len(body) > 20000:
            raise ValueError('Текст должен быть не длиннее 20 000 символов.')
        with self.connect() as db:
            cursor = db.execute('INSERT INTO records(kind,title,body) VALUES (?,?,?)',
                                (kind, title.strip(), body.strip()))
            return cursor.lastrowid

    def backup(self):
        destination = self.directory / 'backups' / (time.strftime('tooru-%Y%m%d-%H%M%S-') + secrets.token_hex(3) + '.sqlite3')
        with self.connect() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        return destination.name

    def diagnostics(self):
        with self.connect() as db:
            check = db.execute('PRAGMA quick_check').fetchone()[0]
        return dict(database=check, sqlite=sqlite3.sqlite_version,
                    python=sys.version.split()[0], version=VERSION,
                    database_bytes=self.path.stat().st_size,
                    data_directory=str(self.directory),
                    ai='Не подключён', qwen='Не подключён',
                    access='Только этот компьютер')


class InstanceLock:
    def __init__(self, path):
        self.file = open(path, 'a+b')
        if self.file.tell() == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)

    def acquire(self):
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def close(self):
        self.file.close()


def make_server(storage, port=8765):
    token = secrets.token_urlsafe(32)
    instance = hashlib.sha256(str(storage.directory).encode('utf-8')).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logging.info(fmt, *args)

        def send(self, code, data, mime='application/json; charset=utf-8'):
            if isinstance(data, (dict, list)):
                data = json.dumps(data, ensure_ascii=False).encode('utf-8')
            elif isinstance(data, str):
                data = data.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(data)

        def allowed(self, auth=False):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != origin.removeprefix('http://'):
                self.send(403, {'error': 'Недопустимый адрес запроса.'})
                return False
            if self.headers.get('Origin') not in (None, origin):
                self.send(403, {'error': 'Запрос с другого сайта запрещён.'})
                return False
            if auth and not secrets.compare_digest(self.headers.get('X-Tooru-Token', ''), token):
                self.send(403, {'error': 'Обновите страницу приложения.'})
                return False
            return True

        def do_GET(self):
            if not self.allowed(self.path.startswith('/api/')):
                return
            try:
                if self.path == '/health':
                    self.send(200, {'app': 'TOORU-DRAGON', 'instance': instance, 'version': VERSION})
                elif self.path in ('/', '/index.html'):
                    html = (ROOT / 'web' / 'index.html').read_text('utf-8').replace('__TOKEN__', token)
                    self.send(200, html, 'text/html; charset=utf-8')
                elif self.path in ('/app.js', '/style.css'):
                    mime = 'text/javascript' if self.path.endswith('.js') else 'text/css'
                    self.send(200, (ROOT / 'web' / self.path[1:]).read_bytes(), mime + '; charset=utf-8')
                elif self.path == '/api/state':
                    self.send(200, storage.state())
                elif self.path == '/api/diagnostics':
                    self.send(200, storage.diagnostics())
                else:
                    self.send(404, {'error': 'Страница не найдена.'})
            except Exception:
                logging.exception('GET failed')
                self.send(500, {'error': 'Ошибка чтения данных. Подробности в data/logs/app.log.'})

        def do_POST(self):
            if not self.allowed(True):
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 100000:
                    raise ValueError('Недопустимый размер запроса.')
                item = json.loads(self.rfile.read(size))
                if not isinstance(item, dict):
                    raise ValueError('Ожидается объект данных.')
                if self.path == '/api/records':
                    self.send(201, {'id': storage.add(item)})
                elif self.path == '/api/delete':
                    if type(item.get('id')) is not int:
                        raise ValueError('Некорректный номер записи.')
                    with storage.connect() as db:
                        db.execute('DELETE FROM records WHERE id=?', (item['id'],))
                    self.send(200, {'ok': True})
                elif self.path == '/api/settings':
                    name, theme = item.get('name', ''), item.get('theme', 'system')
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                        raise ValueError('Имя должно содержать от 1 до 80 символов.')
                    if theme not in ('system', 'light', 'dark'):
                        raise ValueError('Неизвестная тема оформления.')
                    with storage.connect() as db:
                        db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)',
                                       [('name', name.strip()), ('theme', theme)])
                    self.send(200, {'ok': True})
                elif self.path == '/api/backup':
                    self.send(200, {'filename': storage.backup()})
                else:
                    self.send(404, {'error': 'Действие не найдено.'})
            except (ValueError, UnicodeError) as exc:
                self.send(400, {'error': str(exc)})
            except Exception:
                logging.exception('POST failed')
                self.send(500, {'error': 'Не удалось сохранить данные. Подробности в журнале.'})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = False
        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(15)
            return sock, address

    server = Server(('127.0.0.1', port), Handler)
    server.instance_id = instance
    return server


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args(argv)
    args.data_dir = args.data_dir.resolve()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    lock = InstanceLock(args.data_dir / 'instance.lock')
    state_file = args.data_dir / 'server.json'
    if not lock.acquire():
        try:
            for _ in range(30):
                try:
                    state = json.loads(state_file.read_text('utf-8'))
                    port = state['port']
                    if type(port) is not int or not 1 <= port <= 65535:
                        raise ValueError('Invalid port')
                    url = f'http://127.0.0.1:{port}'
                    with urllib.request.urlopen(url + '/health', timeout=1) as response:
                        health = json.load(response)
                    expected = hashlib.sha256(str(args.data_dir).encode('utf-8')).hexdigest()
                    if health.get('app') == 'TOORU-DRAGON' and health.get('instance') == expected:
                        if not args.no_browser:
                            webbrowser.open(url)
                        print('TOORU уже запущена:', url)
                        return 0
                except (OSError, ValueError, KeyError):
                    time.sleep(.2)
            print('Другой экземпляр запускается или не отвечает. Проверьте его окно.')
            return 1
        finally:
            lock.close()
    server = None
    try:
        storage = Storage(args.data_dir)
        logging.basicConfig(filename=storage.directory / 'logs' / 'app.log',
                            encoding='utf-8', level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')
        for port in range(args.port, min(args.port + 20, 65536)):
            try:
                server = make_server(storage, port)
                break
            except OSError:
                continue
        if server is None:
            raise RuntimeError('Не найден свободный локальный порт.')
        url = f'http://127.0.0.1:{server.server_port}'
        state_file.write_text(json.dumps({'port': server.server_port}), encoding='utf-8')
        print(f'TOORU · DRAGON {VERSION}\nАдрес: {url}\nДля остановки нажмите Ctrl+C в этом окне.', flush=True)
        def open_when_ready():
            for _ in range(40):
                try:
                    with urllib.request.urlopen(url + '/health', timeout=1) as response:
                        if response.status == 200:
                            if not webbrowser.open(url):
                                print('Откройте адрес вручную:', url, flush=True)
                            return
                except OSError:
                    time.sleep(.1)
        if not args.no_browser:
            threading.Thread(target=open_when_ready, daemon=True).start()
        server.serve_forever(poll_interval=.2)
    except KeyboardInterrupt:
        print('\nTOORU остановлена.')
    except Exception as exc:
        logging.exception('Startup failed')
        print('Ошибка запуска:', exc)
        return 1
    finally:
        if server:
            server.server_close()
        state_file.unlink(missing_ok=True)
        lock.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
