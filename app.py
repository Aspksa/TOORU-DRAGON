"""TOORU · DRAGON: portable, loopback-only application. Standard library only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, closing
import hashlib
import json
import logging
import os
import re
from pathlib import Path
import secrets
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
import urllib.parse
import webbrowser
import updater
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
try:
    VERSION = (ROOT / 'VERSION').read_text('utf-8').strip() or '0.0.0'
except OSError:
    VERSION = '0.0.0'

def release_manifest():
    try:
        data = json.loads((ROOT / 'RELEASE.json').read_text('utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}

def web_asset_revision():
    digest = hashlib.sha256()
    for relative in ('web/app.js', 'web/style.css'):
        try:
            digest.update((ROOT / relative).read_bytes())
        except OSError:
            digest.update(relative.encode('utf-8'))
    return digest.hexdigest()[:12]
KINDS = {'work', 'home', 'memory', 'knowledge', 'topic', 'chat'}
YANDEX_AI_URL = 'https://ai.api.cloud.yandex.net/v1/responses'
DEFAULT_YANDEX_FOLDER = 'b1gpcfme4j9b9bv37hqb'
DEFAULT_YANDEX_MODEL = 'qwen3.6-35b-a3b/latest'
DEFAULT_INPUT_RUB_PER_1K = 0.2
DEFAULT_OUTPUT_RUB_PER_1K = 0.3
DEFAULT_MONTHLY_BUDGET_RUB = 1000.0
STALE_SECONDS = 600


class Storage:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'tooru.sqlite3'
        for name in ('documents', 'models', 'logs', 'backups'):
            (self.directory / name).mkdir(exist_ok=True)
        with self.connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > 12:
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
                CREATE TABLE IF NOT EXISTS learning_queue (
                    id INTEGER PRIMARY KEY,
                    topic TEXT NOT NULL,
                    question TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    response_text TEXT NOT NULL DEFAULT '',
                    last_error TEXT NOT NULL DEFAULT '',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    review_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS learning_queue_status_id ON learning_queue(status, id);
                CREATE TABLE IF NOT EXISTS ai_messages (
                    id INTEGER PRIMARY KEY,
                    channel TEXT NOT NULL,
                    role TEXT NOT NULL,
                    text TEXT NOT NULL,
                    queue_id INTEGER,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS ai_messages_channel_id ON ai_messages(channel, id DESC);
                CREATE TABLE IF NOT EXISTS brain_suggestions (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    topic TEXT NOT NULL DEFAULT '',
                    question TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending',
                    source_message_id INTEGER,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    decided_at TEXT
                );
                CREATE INDEX IF NOT EXISTS brain_suggestions_status_id
                    ON brain_suggestions(status, id DESC);
                CREATE TABLE IF NOT EXISTS brain_goals (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    plan_json TEXT NOT NULL DEFAULT '[]',
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS brain_goals_status_id
                    ON brain_goals(status, id DESC);
                CREATE TABLE IF NOT EXISTS brain_reasoning (
                    id INTEGER PRIMARY KEY,
                    problem TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    context_json TEXT NOT NULL DEFAULT '[]',
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS brain_reasoning_id ON brain_reasoning(id DESC);
                CREATE TABLE IF NOT EXISTS work_context (
                    id INTEGER PRIMARY KEY CHECK (id=1),
                    area TEXT NOT NULL DEFAULT '',
                    active_task TEXT NOT NULL DEFAULT '',
                    last_decision TEXT NOT NULL DEFAULT '',
                    next_step TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE TABLE IF NOT EXISTS brain_experiments (
                    id INTEGER PRIMARY KEY,
                    hypothesis TEXT NOT NULL,
                    experiment_type TEXT NOT NULL DEFAULT 'knowledge_check',
                    plan TEXT NOT NULL DEFAULT '',
                    expected_result TEXT NOT NULL DEFAULT '',
                    actual_result TEXT NOT NULL DEFAULT '',
                    verdict TEXT NOT NULL DEFAULT 'planned',
                    confidence_before REAL NOT NULL DEFAULT 0,
                    confidence_after REAL NOT NULL DEFAULT 0,
                    lesson TEXT NOT NULL DEFAULT '',
                    reasoning_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'planned',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS brain_experiments_status_id
                    ON brain_experiments(status, id DESC);
                CREATE TABLE IF NOT EXISTS dragon_notifications (
                    id INTEGER PRIMARY KEY,
                    level TEXT NOT NULL DEFAULT 'info',
                    category TEXT NOT NULL DEFAULT 'important',
                    group_key TEXT NOT NULL DEFAULT '',
                    title TEXT NOT NULL,
                    body TEXT NOT NULL DEFAULT '',
                    action TEXT NOT NULL DEFAULT '',
                    is_read INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS dragon_notifications_read_id
                    ON dragon_notifications(is_read, id DESC);
                CREATE TABLE IF NOT EXISTS dragon_actions (
                    id INTEGER PRIMARY KEY,
                    capability TEXT NOT NULL,
                    target TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'done',
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS dragon_actions_id ON dragon_actions(id DESC);
                CREATE TABLE IF NOT EXISTS dragon_tasks (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    plan_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'pending',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    source_kind TEXT NOT NULL DEFAULT 'manual',
                    source_message_id INTEGER,
                    requires_decision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                );
                CREATE INDEX IF NOT EXISTS dragon_tasks_status_id
                    ON dragon_tasks(status, id);
            ''')
            if version < 3:
                old_queue = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='qwen_queue'"
                ).fetchone()
                if old_queue:
                    for row in db.execute(
                        'SELECT id,topic,question,status,created_at,updated_at FROM qwen_queue ORDER BY id'
                    ).fetchall():
                        mapped = {'pending': 'pending', 'sent': 'stale', 'done': 'done'}.get(
                            row['status'], 'stale'
                        )
                        db.execute(
                            'INSERT OR IGNORE INTO learning_queue'
                            '(id,topic,question,status,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                            (row['id'], row['topic'], row['question'], mapped,
                             row['created_at'], row['updated_at'])
                        )
                db.execute('DROP TABLE IF EXISTS qwen_events')
                db.execute('DROP TABLE IF EXISTS qwen_queue')
                db.execute("DELETE FROM settings WHERE key IN "
                           "('qwen_mode','qwen_owner','qwen_last_seen','secret.qwen_bridge')")
                db.execute('PRAGMA user_version=3')
            if version < 4:
                existing_messages = db.execute(
                    "SELECT 1 FROM ai_messages WHERE channel='learning' LIMIT 1"
                ).fetchone()
                if not existing_messages:
                    for row in db.execute(
                        'SELECT id,question,status,response_text,input_tokens,output_tokens,created_at,updated_at '
                        'FROM learning_queue ORDER BY id'
                    ).fetchall():
                        db.execute(
                            'INSERT INTO ai_messages(channel,role,text,queue_id,created_at) '
                            'VALUES (?,?,?,?,?)',
                            ('learning', 'tori', row['question'], row['id'], row['created_at'])
                        )
                        if row['status'] == 'done' and row['response_text']:
                            db.execute(
                                'INSERT INTO ai_messages(channel,role,text,queue_id,input_tokens,output_tokens,created_at) '
                                'VALUES (?,?,?,?,?,?,?)',
                                ('learning', 'qwen', row['response_text'], row['id'],
                                 row['input_tokens'], row['output_tokens'], row['updated_at'])
                            )
                db.execute('PRAGMA user_version=4')
            if version < 5:
                columns = {row['name'] for row in db.execute('PRAGMA table_info(ai_messages)').fetchall()}
                if 'context_json' not in columns:
                    db.execute("ALTER TABLE ai_messages ADD COLUMN context_json TEXT NOT NULL DEFAULT '[]'")
                db.execute('PRAGMA user_version=5')
            if version < 6:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS brain_suggestions (
                        id INTEGER PRIMARY KEY,
                        kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        body TEXT NOT NULL DEFAULT '',
                        topic TEXT NOT NULL DEFAULT '',
                        question TEXT NOT NULL DEFAULT '',
                        reason TEXT NOT NULL DEFAULT '',
                        confidence REAL NOT NULL DEFAULT 0,
                        status TEXT NOT NULL DEFAULT 'pending',
                        source_message_id INTEGER,
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                        decided_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS brain_suggestions_status_id
                        ON brain_suggestions(status, id DESC);
                """)
                db.execute('PRAGMA user_version=6')
            if version < 7:
                columns = {row['name'] for row in db.execute('PRAGMA table_info(learning_queue)').fetchall()}
                if 'review_json' not in columns:
                    db.execute("ALTER TABLE learning_queue ADD COLUMN review_json TEXT NOT NULL DEFAULT '{}'")
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS brain_goals (
                        id INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'active',
                        plan_json TEXT NOT NULL DEFAULT '[]',
                        progress_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    );
                    CREATE INDEX IF NOT EXISTS brain_goals_status_id
                        ON brain_goals(status, id DESC);
                """)
                db.execute('PRAGMA user_version=7')
            if version < 8:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS brain_reasoning (
                        id INTEGER PRIMARY KEY,
                        problem TEXT NOT NULL,
                        result_json TEXT NOT NULL DEFAULT '{}',
                        context_json TEXT NOT NULL DEFAULT '[]',
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    );
                    CREATE INDEX IF NOT EXISTS brain_reasoning_id
                        ON brain_reasoning(id DESC);
                """)
                db.execute('PRAGMA user_version=8')
            if version < 9:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS dragon_notifications (
                        id INTEGER PRIMARY KEY,
                        level TEXT NOT NULL DEFAULT 'info',
                        title TEXT NOT NULL,
                        body TEXT NOT NULL DEFAULT '',
                        action TEXT NOT NULL DEFAULT '',
                        is_read INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    );
                    CREATE INDEX IF NOT EXISTS dragon_notifications_read_id
                        ON dragon_notifications(is_read, id DESC);
                    CREATE TABLE IF NOT EXISTS dragon_actions (
                        id INTEGER PRIMARY KEY,
                        capability TEXT NOT NULL,
                        target TEXT NOT NULL DEFAULT '',
                        summary TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'done',
                        details_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    );
                    CREATE INDEX IF NOT EXISTS dragon_actions_id ON dragon_actions(id DESC);
                """)
                db.execute('PRAGMA user_version=9')
            if version < 10:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS dragon_tasks (
                        id INTEGER PRIMARY KEY,
                        title TEXT NOT NULL,
                        action_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'pending',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    );
                    CREATE INDEX IF NOT EXISTS dragon_tasks_status_id
                        ON dragon_tasks(status, id);
                """)
                db.execute('PRAGMA user_version=10')
            if version < 11:
                notification_columns = {row['name'] for row in db.execute(
                    'PRAGMA table_info(dragon_notifications)'
                ).fetchall()}
                if 'category' not in notification_columns:
                    db.execute("ALTER TABLE dragon_notifications ADD COLUMN category TEXT NOT NULL DEFAULT 'important'")
                if 'group_key' not in notification_columns:
                    db.execute("ALTER TABLE dragon_notifications ADD COLUMN group_key TEXT NOT NULL DEFAULT ''")
                task_columns = {row['name'] for row in db.execute(
                    'PRAGMA table_info(dragon_tasks)'
                ).fetchall()}
                if 'plan_json' not in task_columns:
                    db.execute("ALTER TABLE dragon_tasks ADD COLUMN plan_json TEXT NOT NULL DEFAULT '[]'")
                if 'source_kind' not in task_columns:
                    db.execute("ALTER TABLE dragon_tasks ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'manual'")
                if 'source_message_id' not in task_columns:
                    db.execute("ALTER TABLE dragon_tasks ADD COLUMN source_message_id INTEGER")
                if 'requires_decision' not in task_columns:
                    db.execute("ALTER TABLE dragon_tasks ADD COLUMN requires_decision INTEGER NOT NULL DEFAULT 0")
                db.execute('PRAGMA user_version=11')
            if version < 12:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS work_context (
                        id INTEGER PRIMARY KEY CHECK (id=1),
                        area TEXT NOT NULL DEFAULT '',
                        active_task TEXT NOT NULL DEFAULT '',
                        last_decision TEXT NOT NULL DEFAULT '',
                        next_step TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT 'manual',
                        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    );
                    CREATE TABLE IF NOT EXISTS brain_experiments (
                        id INTEGER PRIMARY KEY,
                        hypothesis TEXT NOT NULL,
                        experiment_type TEXT NOT NULL DEFAULT 'knowledge_check',
                        plan TEXT NOT NULL DEFAULT '',
                        expected_result TEXT NOT NULL DEFAULT '',
                        actual_result TEXT NOT NULL DEFAULT '',
                        verdict TEXT NOT NULL DEFAULT 'planned',
                        confidence_before REAL NOT NULL DEFAULT 0,
                        confidence_after REAL NOT NULL DEFAULT 0,
                        lesson TEXT NOT NULL DEFAULT '',
                        reasoning_id INTEGER,
                        status TEXT NOT NULL DEFAULT 'planned',
                        created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
                    );
                    CREATE INDEX IF NOT EXISTS brain_experiments_status_id
                        ON brain_experiments(status, id DESC);
                """)
                db.execute('PRAGMA user_version=12')
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('name', 'Aspksa'))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('theme', 'system'))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                       ('ai_folder_id', DEFAULT_YANDEX_FOLDER))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                       ('ai_model', DEFAULT_YANDEX_MODEL))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('learning_mode', 'stopped'))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('ai_last_success', '0'))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                       ('ai_input_rub_per_1k', str(DEFAULT_INPUT_RUB_PER_1K)))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                       ('ai_output_rub_per_1k', str(DEFAULT_OUTPUT_RUB_PER_1K)))
            db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)',
                       ('ai_monthly_budget_rub', str(DEFAULT_MONTHLY_BUDGET_RUB)))
            for key, value in (
                ('dragon_name', 'Дракончик Тоору'),
                ('dragon_project_read', '1'),
                ('dragon_project_write', '1'),
                ('dragon_data_manage', '1'),
                ('dragon_brain_auto', '1'),
                ('dragon_update_check', '1'),
                ('dragon_notifications', '1'),
                ('dragon_delete_files', '0'),
                ('dragon_mode', 'suggest'),
            ):
                db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, value))
            db.execute(
                "INSERT OR IGNORE INTO work_context(id,area,active_task,last_decision,next_step,source) "
                "VALUES (1,?,?,?,?,?)",
                ('TOORU · DRAGON / развитие Дракончика',
                 'Связать чат, задачи, Разум и проверку результата',
                 'Широкие изменения требуют подтверждения',
                 'Проверять гипотезы безопасными экспериментами',
                 'system')
            )

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
            settings = dict(db.execute(
                "SELECT key, value FROM settings WHERE key NOT LIKE 'secret.%'"
            ).fetchall())
            counts = dict(db.execute('SELECT kind, count(*) FROM records GROUP BY kind').fetchall())
            records = {kind: [dict(row) for row in db.execute(
                'SELECT * FROM records WHERE kind=? ORDER BY id DESC LIMIT 200', (kind,)
            )] for kind in sorted(KINDS)}
        learning = self.learning_state()
        chat = self.chat_state()
        reasoning = self.reasoning_state()
        dragon = self.dragon_status()
        brain_lab = self.brain_lab_state()
        return dict(version=VERSION, release=release_manifest(), asset_revision=web_asset_revision(),
                    settings=settings, counts=counts, records=records,
                    ai_connected=learning['configured'] and learning['last_success'] > 0,
                    learning=learning, chat=chat, reasoning=reasoning, dragon=dragon,
                    brain_lab=brain_lab)

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

    def ai_config(self, include_secret=False):
        with self.connect() as db:
            rows = dict(db.execute(
                "SELECT key,value FROM settings WHERE key IN "
                "('ai_folder_id','ai_model','ai_last_success','ai_auth_type','secret.yandex_api_key',"
                "'ai_input_rub_per_1k','ai_output_rub_per_1k','ai_monthly_budget_rub')"
            ).fetchall())
        result = {
            'folder_id': rows.get('ai_folder_id', DEFAULT_YANDEX_FOLDER),
            'model': rows.get('ai_model', DEFAULT_YANDEX_MODEL),
            'configured': bool(rows.get('secret.yandex_api_key')),
            'auth_type': rows.get('ai_auth_type', 'api_key'),
            'last_success': float(rows.get('ai_last_success', '0') or 0),
            'input_rub_per_1k': float(rows.get('ai_input_rub_per_1k', DEFAULT_INPUT_RUB_PER_1K) or 0),
            'output_rub_per_1k': float(rows.get('ai_output_rub_per_1k', DEFAULT_OUTPUT_RUB_PER_1K) or 0),
            'monthly_budget_rub': float(rows.get('ai_monthly_budget_rub', DEFAULT_MONTHLY_BUDGET_RUB) or 0),
        }
        if include_secret:
            result['api_key'] = rows.get('secret.yandex_api_key', '')
        return result

    def save_ai_config(self, item):
        folder = item.get('folder_id', '')
        model = item.get('model', '')
        api_key = item.get('api_key', '')
        auth_type = item.get('auth_type', 'api_key')
        if auth_type not in {'api_key', 'iam_token'}:
            raise ValueError('Неизвестный тип авторизации AI Studio.')
        input_rate = item.get('input_rub_per_1k', DEFAULT_INPUT_RUB_PER_1K)
        output_rate = item.get('output_rub_per_1k', DEFAULT_OUTPUT_RUB_PER_1K)
        monthly_budget = item.get('monthly_budget_rub', DEFAULT_MONTHLY_BUDGET_RUB)
        try:
            input_rate = float(input_rate)
            output_rate = float(output_rate)
            monthly_budget = float(monthly_budget)
        except (TypeError, ValueError):
            raise ValueError('Проверь тарифы и месячный бюджет.') from None
        if not 0 <= input_rate <= 1000 or not 0 <= output_rate <= 1000:
            raise ValueError('Тариф должен быть от 0 до 1000 ₽ за 1000 токенов.')
        if not 0 <= monthly_budget <= 10000000:
            raise ValueError('Месячный бюджет должен быть от 0 до 10 000 000 ₽.')
        if not isinstance(folder, str) or not 10 <= len(folder.strip()) <= 64 or not folder.strip().isalnum():
            raise ValueError('Проверь идентификатор каталога Yandex Cloud.')
        allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._@/-')
        if not isinstance(model, str) or not 3 <= len(model.strip()) <= 120 or any(c not in allowed for c in model.strip()):
            raise ValueError('Проверь имя модели AI Studio.')
        if not isinstance(api_key, str) or len(api_key) > 500:
            raise ValueError('Некорректный API-ключ.')
        previous = self.ai_config(include_secret=True)
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', [
                ('ai_folder_id', folder.strip()),
                ('ai_model', model.strip()),
                ('ai_auth_type', auth_type),
                ('ai_input_rub_per_1k', str(input_rate)),
                ('ai_output_rub_per_1k', str(output_rate)),
                ('ai_monthly_budget_rub', str(monthly_budget)),
            ])
            if api_key.strip():
                secret_changed = api_key.strip() != previous.get('api_key', '')
                connection_changed = (
                    folder.strip() != previous.get('folder_id')
                    or model.strip() != previous.get('model')
                    or auth_type != previous.get('auth_type')
                )
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           ('secret.yandex_api_key', api_key.strip()))
                if secret_changed or connection_changed:
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('ai_last_success', '0'))
            if item.get('clear_key') is True:
                db.execute("DELETE FROM settings WHERE key='secret.yandex_api_key'")
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('ai_last_success', '0'))
        return self.learning_state()

    def usage_summary(self):
        config = self.ai_config()
        input_rate = config['input_rub_per_1k']
        output_rate = config['output_rub_per_1k']
        budget = config['monthly_budget_rub']
        now = time.time()
        day_cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now - 86400))
        month_start = time.strftime('%Y-%m-01T00:00:00Z', time.gmtime(now))

        def totals_since(cutoff, channel=None):
            where = "created_at>=? AND (input_tokens>0 OR output_tokens>0)"
            params = [cutoff]
            if channel:
                where += ' AND channel=?'
                params.append(channel)
            with self.connect() as db:
                row = db.execute(
                    f'SELECT COALESCE(sum(input_tokens),0),COALESCE(sum(output_tokens),0) '
                    f'FROM ai_messages WHERE {where}',
                    params
                ).fetchone()
            incoming = int(row[0] or 0)
            outgoing = int(row[1] or 0)
            cost = incoming / 1000 * input_rate + outgoing / 1000 * output_rate
            return {'input_tokens': incoming, 'output_tokens': outgoing, 'cost_rub': round(cost, 4)}

        day = totals_since(day_cutoff)
        month = totals_since(month_start)
        chat_month = totals_since(month_start, 'chat')
        learning_month = totals_since(month_start, 'learning')
        remaining = max(0.0, budget - month['cost_rub']) if budget > 0 else None
        blocked = budget > 0 and month['cost_rub'] >= budget
        return {
            'day': day,
            'month': month,
            'chat_month': chat_month,
            'learning_month': learning_month,
            'input_rub_per_1k': input_rate,
            'output_rub_per_1k': output_rate,
            'monthly_budget_rub': budget,
            'remaining_rub': None if remaining is None else round(remaining, 4),
            'blocked': blocked,
            'period_note': '24 часа и календарный месяц UTC',
        }

    def ensure_budget(self):
        usage = self.usage_summary()
        if usage['blocked']:
            raise ValueError(
                f"Месячный лимит AI достигнут: {usage['month']['cost_rub']:.2f} ₽ "
                f"из {usage['monthly_budget_rub']:.2f} ₽. Измени лимит в Настройках."
            )
        return usage

    def enforce_learning_budget(self):
        usage = self.usage_summary()
        if usage['blocked']:
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           ('learning_mode', 'paused'))
        return usage

    def mark_ai_success(self):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       ('ai_last_success', str(time.time())))

    def ai_messages(self, channel, limit=100):
        if channel not in {'chat', 'learning', 'brain'}:
            raise ValueError('Неизвестный канал диалога.')
        limit = max(1, min(int(limit), 200))
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT id,channel,role,text,queue_id,input_tokens,output_tokens,context_json,created_at '
                'FROM ai_messages WHERE channel=? ORDER BY id DESC LIMIT ?',
                (channel, limit)
            )]
        rows.reverse()
        for row in rows:
            try:
                row['context'] = json.loads(row.pop('context_json') or '[]')
                if not isinstance(row['context'], list):
                    row['context'] = []
            except (ValueError, TypeError):
                row['context'] = []
        return rows

    def add_ai_message(self, channel, role, text, queue_id=None,
                       input_tokens=0, output_tokens=0, context=None):
        if channel not in {'chat', 'learning', 'brain'}:
            raise ValueError('Неизвестный канал диалога.')
        if role not in {'user', 'tori', 'qwen', 'system'}:
            raise ValueError('Неизвестный автор сообщения.')
        if not isinstance(text, str) or not text.strip():
            return None
        clean = text.strip()[:50000]
        with self.connect() as db:
            cursor = db.execute(
                'INSERT INTO ai_messages(channel,role,text,queue_id,input_tokens,output_tokens,context_json) '
                'VALUES (?,?,?,?,?,?,?)',
                (channel, role, clean, queue_id, int(input_tokens or 0), int(output_tokens or 0),
                 json.dumps(context or [], ensure_ascii=False))
            )
        return cursor.lastrowid

    @staticmethod
    def _search_terms(text):
        stop = {
            'это','как','что','где','когда','кто','для','про','или','она','они','оно','его','ее','её',
            'мне','меня','мой','моя','мои','твой','твоя','ты','вы','мы','же','бы','ли','на','в','во',
            'с','со','по','из','за','до','от','у','к','и','а','но','не','да','есть','был','была'
        }
        return {
            word for word in re.findall(r'[0-9A-Za-zА-Яа-яЁё_-]{2,}', text.lower())
            if word not in stop
        }

    def relevant_context(self, query, limit=6, char_budget=6000):
        terms = self._search_terms(query)
        if not terms:
            return []
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id,kind,title,body,source,created_at FROM records "
                "WHERE kind IN ('memory','knowledge') ORDER BY id DESC LIMIT 1000"
            )]
        ranked = []
        for row in rows:
            title = row['title'].lower()
            body = row['body'].lower()
            score = 0
            matched = []
            for term in terms:
                title_hits = title.count(term)
                body_hits = body.count(term)
                if title_hits or body_hits:
                    score += title_hits * 5 + min(body_hits, 4)
                    matched.append(term)
            if score:
                ranked.append((score, row['id'], matched, row))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

        result = []
        used = 0
        for score, _row_id, matched, row in ranked[:max(limit * 3, limit)]:
            allowance = max(0, char_budget - used)
            if allowance < 200:
                break
            excerpt = row['body'].strip()[:min(1800, allowance)]
            result.append({
                'id': row['id'],
                'kind': row['kind'],
                'title': row['title'],
                'source': row['source'],
                'excerpt': excerpt,
                'matched': matched[:8],
                'score': score,
            })
            used += len(excerpt) + len(row['title']) + 80
            if len(result) >= limit:
                break
        return result

    @staticmethod
    def _parse_json_object(text):
        if not isinstance(text, str):
            return {}
        clean = text.strip()
        if clean.startswith('```'):
            clean = re.sub(r'^```(?:json)?\\s*|\\s*```$', '', clean, flags=re.I | re.S).strip()
        try:
            data = json.loads(clean)
        except (ValueError, TypeError):
            start, end = clean.find('{'), clean.rfind('}')
            if start < 0 or end <= start:
                return {}
            try:
                data = json.loads(clean[start:end + 1])
            except (ValueError, TypeError):
                return {}
        return data if isinstance(data, dict) else {}

    def work_context(self):
        with self.connect() as db:
            row = db.execute(
                'SELECT area,active_task,last_decision,next_step,source,updated_at '
                'FROM work_context WHERE id=1'
            ).fetchone()
        if not row:
            return {
                'area':'','active_task':'','last_decision':'','next_step':'',
                'source':'system','updated_at':''
            }
        return dict(row)

    def save_work_context(self, item, source='manual'):
        current = self.work_context()
        fields = {}
        for key, limit in (
            ('area', 500), ('active_task', 1000),
            ('last_decision', 1200), ('next_step', 1200)
        ):
            value = item[key] if key in item else current.get(key, '')
            if not isinstance(value, str):
                raise ValueError('Рабочий контекст должен быть текстом.')
            fields[key] = value.strip()[:limit]
        if not any(fields.values()):
            raise ValueError('Заполни хотя бы одно поле рабочего контекста.')
        with self.connect() as db:
            db.execute(
                "INSERT INTO work_context(id,area,active_task,last_decision,next_step,source,updated_at) "
                "VALUES (1,?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now')) "
                "ON CONFLICT(id) DO UPDATE SET area=excluded.area,active_task=excluded.active_task,"
                "last_decision=excluded.last_decision,next_step=excluded.next_step,"
                "source=excluded.source,updated_at=excluded.updated_at",
                (fields['area'], fields['active_task'], fields['last_decision'],
                 fields['next_step'], str(source)[:80])
            )
        self.dragon_log_action(
            'work_context', 'brain', 'Обновлён рабочий контекст',
            {'area': fields['area'], 'active_task': fields['active_task'],
             'next_step': fields['next_step']}
        )
        return self.work_context()

    def _work_context_text(self):
        ctx = self.work_context()
        values = [
            ('Сейчас мы работаем над', ctx.get('area')),
            ('Активная задача', ctx.get('active_task')),
            ('Последнее решение', ctx.get('last_decision')),
            ('Следующий шаг', ctx.get('next_step')),
        ]
        lines = [f'{label}: {value}' for label, value in values if value]
        return '\n'.join(lines)

    def brain_experiments(self, limit=30):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,hypothesis,experiment_type,plan,expected_result,actual_result,verdict,'
                'confidence_before,confidence_after,lesson,reasoning_id,status,created_at,updated_at '
                'FROM brain_experiments ORDER BY id DESC LIMIT ?',
                (max(1, min(int(limit), 100)),)
            )]

    def create_brain_experiment(self, item):
        hypothesis = item.get('hypothesis', '')
        experiment_type = item.get('experiment_type', 'knowledge_check')
        plan = item.get('plan', '')
        expected = item.get('expected_result', '')
        reasoning_id = item.get('reasoning_id')
        try:
            confidence = max(0.0, min(float(item.get('confidence_before', 0.5)), 1.0))
        except (TypeError, ValueError):
            confidence = 0.5
        if not isinstance(hypothesis, str) or not 3 <= len(hypothesis.strip()) <= 3000:
            raise ValueError('Гипотеза должна содержать от 3 до 3000 символов.')
        if experiment_type not in {'knowledge_check','project_scan'}:
            raise ValueError('Неизвестный безопасный тип эксперимента.')
        if not isinstance(plan, str) or not isinstance(expected, str):
            raise ValueError('План и ожидаемый результат должны быть текстом.')
        if type(reasoning_id) is not int:
            reasoning_id = None
        with self.connect() as db:
            duplicate = db.execute(
                "SELECT id FROM brain_experiments WHERE status IN ('planned','running') "
                "AND lower(hypothesis)=lower(?) LIMIT 1",
                (hypothesis.strip(),)
            ).fetchone()
            if duplicate:
                return {'id': duplicate['id'], 'status': 'planned', 'duplicate': True}
            cursor = db.execute(
                'INSERT INTO brain_experiments('
                'hypothesis,experiment_type,plan,expected_result,confidence_before,reasoning_id'
                ') VALUES (?,?,?,?,?,?)',
                (hypothesis.strip()[:3000], experiment_type, plan.strip()[:4000],
                 expected.strip()[:3000], confidence, reasoning_id)
            )
            experiment_id = cursor.lastrowid
        self.dragon_log_action(
            'experiment', str(experiment_id), 'Запланирован эксперимент Разума',
            {'hypothesis': hypothesis.strip()[:500], 'type': experiment_type}
        )
        return {'id': experiment_id, 'status': 'planned'}

    def run_brain_experiment(self, experiment_id=None):
        with self.connect() as db:
            if experiment_id is None:
                row = db.execute(
                    "SELECT * FROM brain_experiments WHERE status='planned' ORDER BY id LIMIT 1"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM brain_experiments WHERE id=? AND status IN ('planned','error')",
                    (experiment_id,)
                ).fetchone()
            if not row:
                return None
            row = dict(row)
            db.execute(
                "UPDATE brain_experiments SET status='running',"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (row['id'],)
            )
        try:
            evidence = ''
            if row['experiment_type'] == 'project_scan':
                files = self.dragon_project_tree(600)
                sample = [x['path'] for x in files[:180]]
                evidence = (
                    f"Проект содержит {len(files)} доступных файлов. "
                    "Примеры путей:\n" + '\n'.join(sample)
                )
            else:
                ctx = self.relevant_context(row['hypothesis'], limit=6, char_budget=7000)
                if ctx:
                    evidence = '\n\n'.join(
                        f"{entry['kind']}: {entry['title']}\n{entry['excerpt']}" for entry in ctx
                    )
            work = self._work_context_text()
            prompt = (
                'Ты — проверяющий модуль Лаборатории Разума. Не показывай скрытую цепочку рассуждений. '
                'Проверь гипотезу только по доступным данным и верни JSON: '
                '{"verdict":"confirmed|refuted|inconclusive","actual_result":"...",'
                '"confidence_after":0.0,"lesson":"..."}. '
                'confirmed — только если данных достаточно. refuted — если есть конкретное противоречие. '
                'Иначе inconclusive. lesson должен быть коротким проверяемым правилом, а не догадкой.\n\n'
                f"Рабочий контекст:\n{work or 'не задан'}\n\n"
                f"Гипотеза:\n{row['hypothesis']}\n\n"
                f"План эксперимента:\n{row['plan']}\n\n"
                f"Ожидаемый результат:\n{row['expected_result']}\n\n"
                f"Доступные данные:\n{evidence or 'нет дополнительных данных'}"
            )
            result = call_yandex_ai(
                self, prompt, max_output_tokens=850, purpose='critic'
            )
            data = self._parse_json_object(result['text'])
            verdict = data.get('verdict')
            if verdict not in {'confirmed','refuted','inconclusive'}:
                verdict = 'inconclusive'
            actual = str(data.get('actual_result') or '').strip()[:5000]
            lesson = str(data.get('lesson') or '').strip()[:3000]
            try:
                after = max(0.0, min(float(data.get('confidence_after', row['confidence_before'])), 1.0))
            except (TypeError, ValueError):
                after = float(row['confidence_before'] or 0)
            with self.connect() as db:
                db.execute(
                    "UPDATE brain_experiments SET status='done',actual_result=?,verdict=?,"
                    "confidence_after=?,lesson=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                    "WHERE id=?",
                    (actual, verdict, after, lesson, row['id'])
                )
                if lesson and verdict in {'confirmed','refuted'} and after >= 0.8:
                    title = ('Проверено' if verdict == 'confirmed' else 'Опровергнуто') +                             ' · ' + row['hypothesis'][:180]
                    exists = db.execute(
                        "SELECT 1 FROM records WHERE kind='knowledge' AND lower(title)=lower(?) LIMIT 1",
                        (title,)
                    ).fetchone()
                    if not exists:
                        db.execute(
                            'INSERT INTO records(kind,title,body,source) VALUES (?,?,?,?)',
                            ('knowledge', title, lesson[:20000], 'Лаборатория Разума · эксперимент')
                        )
            self.add_ai_message(
                'brain', 'system', 'Эксперимент Разума: ' + verdict,
                input_tokens=result['input_tokens'], output_tokens=result['output_tokens']
            )
            self.mark_ai_success()
            self.dragon_log_action(
                'experiment', str(row['id']), 'Эксперимент Разума завершён',
                {'verdict': verdict, 'lesson': lesson[:500], 'confidence_after': after}
            )
            self.save_work_context({
                'last_decision': (
                    ('Гипотеза подтверждена: ' if verdict == 'confirmed' else
                     'Гипотеза опровергнута: ' if verdict == 'refuted' else
                     'Гипотеза пока не доказана: ') + row['hypothesis'][:900]
                ),
                'next_step': lesson or 'Нужны дополнительные данные для следующей проверки.',
            }, source='experiment')
            if verdict == 'refuted':
                self.dragon_notify(
                    'warning', 'Гипотеза опровергнута', row['hypothesis'][:300],
                    action='experiment', category='important',
                    group_key='experiment:' + str(row['id'])
                )
            return {'id': row['id'], 'status': 'done', 'verdict': verdict}
        except Exception as exc:
            with self.connect() as db:
                db.execute(
                    "UPDATE brain_experiments SET status='error',actual_result=?,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (str(exc)[:3000], row['id'])
                )
            logging.warning('Brain experiment %s failed: %s', row['id'], exc)
            return {'id': row['id'], 'status': 'error', 'error': str(exc)}

    def brain_lab_state(self):
        return {
            'context': self.work_context(),
            'experiments': self.brain_experiments(40),
        }

    def reasoning_state(self, limit=30):
        config = self.ai_config()
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT id,problem,result_json,context_json,input_tokens,output_tokens,created_at '
                'FROM brain_reasoning ORDER BY id DESC LIMIT ?',
                (max(1, min(int(limit), 100)),)
            )]
        for row in rows:
            try:
                row['result'] = json.loads(row.pop('result_json') or '{}')
                if not isinstance(row['result'], dict):
                    row['result'] = {}
            except (ValueError, TypeError):
                row['result'] = {}
            try:
                row['context'] = json.loads(row.pop('context_json') or '[]')
                if not isinstance(row['context'], list):
                    row['context'] = []
            except (ValueError, TypeError):
                row['context'] = []
        return {
            'configured': config['configured'],
            'model': config['model'],
            'last_success': config['last_success'],
            'items': rows,
            'usage': self.usage_summary(),
        }

    def reason_problem(self, item):
        problem = item.get('problem', '')
        use_context = item.get('use_context', True) is not False
        if not isinstance(problem, str) or not 3 <= len(problem.strip()) <= 12000:
            raise ValueError('Задача для Разума должна содержать от 3 до 12 000 символов.')
        clean = problem.strip()
        self.ensure_budget()
        context = self.relevant_context(clean, limit=6, char_budget=6500) if use_context else []
        context_text = ''
        work_context_text = self._work_context_text()
        if work_context_text:
            context_text += (
                '\n\nТекущий рабочий контекст TOORU:\n' + work_context_text
            )
        if context:
            blocks = []
            for index, entry in enumerate(context, 1):
                label = 'Память' if entry['kind'] == 'memory' else 'Знание'
                blocks.append(
                    f"[{index}] {label}: {entry['title']}\n"
                    f"Источник: {entry['source']}\n{entry['excerpt']}"
                )
            context_text += (
                '\n\nЛокальный контекст TOORU. Считай его входными данными, а не доказанной истиной:\n'
                + '\n\n'.join(blocks)
            )
        prompt = (
            'Ты — модуль логического анализа Тори. Не показывай скрытую цепочку рассуждений. '
            'Верни только JSON следующего вида: '
            '{"summary":"краткая формулировка задачи","facts":["..."],"assumptions":["..."],'
            '"options":[{"title":"...","pros":["..."],"cons":["..."]}],'
            '"contradictions":["..."],"decision":"...","confidence":0.0,"next_step":"..."}. '
            'Отделяй факты от допущений. Не выдумывай факты. Если данных мало — явно укажи это. '
            'options — максимум 4, каждый список pros/cons максимум 4 пункта. '
            'contradictions — только реальные логические конфликты во входных данных. '
            'decision — практичный вывод, confidence от 0 до 1.\n\n'
            'Задача:\n' + clean + context_text
        )
        result = call_yandex_ai(self, prompt, max_output_tokens=1400, purpose='reasoning')
        data = self._parse_json_object(result['text'])
        normalized = {
            'summary': str(data.get('summary') or '').strip()[:2000],
            'facts': [],
            'assumptions': [],
            'options': [],
            'contradictions': [],
            'decision': str(data.get('decision') or '').strip()[:4000],
            'confidence': 0.0,
            'next_step': str(data.get('next_step') or '').strip()[:2000],
        }
        for key in ('facts', 'assumptions', 'contradictions'):
            raw = data.get(key, [])
            if isinstance(raw, list):
                normalized[key] = [str(x).strip()[:1000] for x in raw[:8] if str(x).strip()]
        raw_options = data.get('options', [])
        if isinstance(raw_options, list):
            for raw in raw_options[:4]:
                if not isinstance(raw, dict):
                    continue
                normalized['options'].append({
                    'title': str(raw.get('title') or '').strip()[:500],
                    'pros': [str(x).strip()[:700] for x in (raw.get('pros') or [])[:4] if str(x).strip()]
                            if isinstance(raw.get('pros'), list) else [],
                    'cons': [str(x).strip()[:700] for x in (raw.get('cons') or [])[:4] if str(x).strip()]
                            if isinstance(raw.get('cons'), list) else [],
                })
        try:
            normalized['confidence'] = max(0.0, min(float(data.get('confidence', 0)), 1.0))
        except (TypeError, ValueError):
            normalized['confidence'] = 0.0

        # Второй независимый проход: критик ищет слабые места и может скорректировать вывод.
        critic_prompt = (
            'Проверь структурированный разбор другой модели. Не показывай скрытую цепочку рассуждений. '
            'Верни только JSON: {"weaknesses":["..."],"missing_evidence":["..."],'
            '"revised_decision":"...","revised_confidence":0.0,"next_step":"...",'
            '"learning_gaps":[{"topic":"...","question":"...","reason":"...","confidence":0.0}]}. '
            'Не соглашайся автоматически. Ищи логические скачки, неподтверждённые предположения и недостающие данные. '
            'learning_gaps — максимум 2 темы, которые реально могут улучшить решение.\n\n'
            'Исходная задача:\n' + clean + '\n\nРазбор:\n'
            + json.dumps(normalized, ensure_ascii=False)
        )
        critic = call_yandex_ai(self, critic_prompt, max_output_tokens=900, purpose='critic')
        critique = self._parse_json_object(critic['text'])
        weaknesses = critique.get('weaknesses', []) if isinstance(critique.get('weaknesses'), list) else []
        missing = critique.get('missing_evidence', []) if isinstance(critique.get('missing_evidence'), list) else []
        normalized['critique'] = {
            'weaknesses': [str(x).strip()[:1000] for x in weaknesses[:6] if str(x).strip()],
            'missing_evidence': [str(x).strip()[:1000] for x in missing[:6] if str(x).strip()],
        }
        revised_decision = str(critique.get('revised_decision') or '').strip()[:4000]
        if revised_decision:
            normalized['decision'] = revised_decision
        revised_next = str(critique.get('next_step') or '').strip()[:2000]
        if revised_next:
            normalized['next_step'] = revised_next
        try:
            revised_confidence = max(0.0, min(float(critique.get('revised_confidence', normalized['confidence'])), 1.0))
        except (TypeError, ValueError):
            revised_confidence = normalized['confidence']
        normalized['confidence'] = revised_confidence

        learning_gaps = []
        raw_gaps = critique.get('learning_gaps', []) if isinstance(critique.get('learning_gaps'), list) else []
        for raw in raw_gaps[:2]:
            if not isinstance(raw, dict):
                continue
            try:
                gap_confidence = max(0.0, min(float(raw.get('confidence', 0)), 1.0))
            except (TypeError, ValueError):
                gap_confidence = 0.0
            gap = {
                'topic': str(raw.get('topic') or '').strip()[:300],
                'question': str(raw.get('question') or '').strip()[:8000],
                'reason': str(raw.get('reason') or '').strip()[:1000],
                'confidence': gap_confidence,
            }
            if gap['topic'] and gap['question']:
                learning_gaps.append(gap)
        normalized['learning_gaps'] = learning_gaps

        visible_context = [
            {'id': entry['id'], 'kind': entry['kind'], 'title': entry['title'], 'source': entry['source']}
            for entry in context
        ]
        total_input = int(result['input_tokens'] or 0) + int(critic['input_tokens'] or 0)
        total_output = int(result['output_tokens'] or 0) + int(critic['output_tokens'] or 0)
        with self.connect() as db:
            cursor = db.execute(
                'INSERT INTO brain_reasoning(problem,result_json,context_json,input_tokens,output_tokens) '
                'VALUES (?,?,?,?,?)',
                (
                    clean,
                    json.dumps(normalized, ensure_ascii=False),
                    json.dumps(visible_context, ensure_ascii=False),
                    total_input,
                    total_output,
                )
            )
            reasoning_id = cursor.lastrowid
            for gap in learning_gaps:
                duplicate = db.execute(
                    "SELECT 1 FROM brain_suggestions WHERE status='pending' AND kind='learning' "
                    "AND lower(topic)=lower(?) AND lower(question)=lower(?) LIMIT 1",
                    (gap['topic'], gap['question'])
                ).fetchone()
                if duplicate:
                    continue
                db.execute(
                    "INSERT INTO brain_suggestions(kind,title,topic,question,reason,confidence) "
                    "VALUES ('learning','',?,?,?,?)",
                    (gap['topic'], gap['question'], gap['reason'], gap['confidence'])
                )
        experiment_id = None
        missing_evidence = normalized.get('critique', {}).get('missing_evidence', [])
        if missing_evidence or normalized.get('confidence', 0) < 0.85:
            hypothesis = normalized.get('decision') or normalized.get('summary') or clean
            project_words = ('проект','код','интерфейс','файл','модуль','ошибк','тест')
            experiment_type = (
                'project_scan' if any(word in clean.lower() for word in project_words)
                else 'knowledge_check'
            )
            expected = (
                'Найти данные, которые подтверждают или опровергают вывод. '
                + ('Недостаёт: ' + '; '.join(missing_evidence[:3]) if missing_evidence else
                   'Повысить уверенность только при наличии новых проверяемых данных.')
            )
            created = self.create_brain_experiment({
                'hypothesis': hypothesis,
                'experiment_type': experiment_type,
                'plan': normalized.get('next_step') or 'Провести минимальную безопасную проверку.',
                'expected_result': expected,
                'confidence_before': normalized.get('confidence', 0),
                'reasoning_id': reasoning_id,
            })
            experiment_id = created.get('id')
        self.save_work_context({
            'last_decision': normalized.get('decision') or normalized.get('summary') or clean,
            'next_step': normalized.get('next_step') or 'Проверить результат Разума.',
        }, source='reasoning')
        self.add_ai_message(
            'brain', 'system', 'Разум v2: анализ + критическая проверка',
            input_tokens=total_input, output_tokens=total_output
        )
        self.mark_ai_success()
        state = self.reasoning_state()
        state['created_id'] = reasoning_id
        state['experiment_id'] = experiment_id
        return state

    def brain_goals(self, status='active', limit=20):
        if status not in {'active', 'done', 'archived', 'all'}:
            raise ValueError('Неизвестный статус целей.')
        query = 'SELECT id,title,description,status,plan_json,progress_json,created_at,updated_at FROM brain_goals'
        params = []
        if status != 'all':
            query += ' WHERE status=?'
            params.append(status)
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(max(1, min(int(limit), 100)))
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(query, params)]
        for row in rows:
            try:
                row['plan'] = json.loads(row.pop('plan_json') or '[]')
                if not isinstance(row['plan'], list):
                    row['plan'] = []
            except (ValueError, TypeError):
                row['plan'] = []
            try:
                row['progress'] = json.loads(row.pop('progress_json') or '{}')
                if not isinstance(row['progress'], dict):
                    row['progress'] = {}
            except (ValueError, TypeError):
                row['progress'] = {}
        return rows

    def create_goal(self, item):
        title = item.get('title', '')
        description = item.get('description', '')
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300:
            raise ValueError('Цель должна содержать от 1 до 300 символов.')
        if not isinstance(description, str) or len(description) > 8000:
            raise ValueError('Описание цели должно быть не длиннее 8 000 символов.')
        self.ensure_budget()
        prompt = (
            'Разбей цель пользователя на короткий практичный план. Верни только JSON: '
            '{"summary":"...","steps":[{"title":"...","type":"action|learning",' 
            '"topic":"...","question":"...","reason":"..."}]}. '
            'Максимум 7 шагов. type=learning используй только если действительно не хватает знаний; '
            'для learning обязательно заполни topic и question. Ничего не запускай сам.\n\n'
            'Цель: ' + title.strip() + '\nОписание: ' + description.strip()
        )
        result = call_yandex_ai(self, prompt, max_output_tokens=900, purpose='planning')
        self.add_ai_message('brain', 'system', 'Планирование цели',
                            input_tokens=result['input_tokens'], output_tokens=result['output_tokens'])
        data = self._parse_json_object(result['text'])
        raw_steps = data.get('steps', []) if isinstance(data.get('steps'), list) else []
        steps = []
        for raw in raw_steps[:7]:
            if not isinstance(raw, dict):
                continue
            step_type = raw.get('type')
            if step_type not in {'action', 'learning'}:
                continue
            step = {
                'title': str(raw.get('title') or '').strip()[:300],
                'type': step_type,
                'topic': str(raw.get('topic') or '').strip()[:300],
                'question': str(raw.get('question') or '').strip()[:8000],
                'reason': str(raw.get('reason') or '').strip()[:1000],
                'status': 'pending',
            }
            if not step['title']:
                continue
            if step_type == 'learning' and (not step['topic'] or not step['question']):
                continue
            steps.append(step)
        if not steps:
            steps = [{'title': 'Уточнить следующий шаг', 'type': 'action',
                      'topic': '', 'question': '', 'reason': 'План модели оказался пустым.',
                      'status': 'pending'}]
        summary = str(data.get('summary') or '').strip()[:2000]
        with self.connect() as db:
            cursor = db.execute(
                'INSERT INTO brain_goals(title,description,plan_json,progress_json) VALUES (?,?,?,?)',
                (title.strip(), description.strip(), json.dumps(steps, ensure_ascii=False),
                 json.dumps({'summary': summary}, ensure_ascii=False))
            )
        return {'id': cursor.lastrowid, 'goals': self.brain_goals('active', 20)}

    def goal_action(self, item):
        goal_id = item.get('id')
        action = item.get('action')
        step_index = item.get('step')
        if type(goal_id) is not int:
            raise ValueError('Некорректная цель.')
        with self.connect() as db:
            row = db.execute('SELECT * FROM brain_goals WHERE id=?', (goal_id,)).fetchone()
        if not row:
            raise ValueError('Цель не найдена.')
        row = dict(row)
        try:
            plan = json.loads(row['plan_json'] or '[]')
        except (ValueError, TypeError):
            plan = []
        if action in {'done', 'archive'}:
            new_status = 'done' if action == 'done' else 'archived'
            with self.connect() as db:
                db.execute(
                    "UPDATE brain_goals SET status=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (new_status, goal_id)
                )
            return {'ok': True, 'status': new_status}
        if type(step_index) is not int or not 0 <= step_index < len(plan):
            raise ValueError('Шаг цели не найден.')
        step = plan[step_index]
        if action == 'complete_step':
            step['status'] = 'done'
        elif action == 'queue_learning':
            if step.get('type') != 'learning':
                raise ValueError('Этот шаг не является обучением.')
            queued = self.learning_enqueue({'topic': step.get('topic', ''), 'question': step.get('question', '')})
            step['status'] = 'queued'
            step['queue_id'] = queued['id']
        else:
            raise ValueError('Неизвестное действие цели.')
        with self.connect() as db:
            db.execute(
                "UPDATE brain_goals SET plan_json=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (json.dumps(plan, ensure_ascii=False), goal_id)
            )
        return {'ok': True, 'goals': self.brain_goals('active', 20)}

    def brain_suggestions(self, status='pending', limit=30):
        if status not in {'pending', 'accepted', 'rejected', 'all'}:
            raise ValueError('Неизвестный статус предложений.')
        query = ('SELECT id,kind,title,body,topic,question,reason,confidence,status,'
                 'source_message_id,created_at,decided_at FROM brain_suggestions')
        params = []
        if status != 'all':
            query += ' WHERE status=?'
            params.append(status)
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(max(1, min(int(limit), 100)))
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params)]

    @staticmethod
    def _parse_brain_json(text):
        if not isinstance(text, str):
            return []
        clean = text.strip()
        if clean.startswith('```'):
            clean = re.sub(r'^```(?:json)?\s*|\s*```$', '', clean, flags=re.I | re.S).strip()
        try:
            data = json.loads(clean)
        except (ValueError, TypeError):
            start, end = clean.find('{'), clean.rfind('}')
            if start < 0 or end <= start:
                return []
            try:
                data = json.loads(clean[start:end + 1])
            except (ValueError, TypeError):
                return []
        suggestions = data.get('suggestions', []) if isinstance(data, dict) else []
        return suggestions if isinstance(suggestions, list) else []

    def brain_reflect(self, user_text, assistant_text, source_message_id):
        if self.usage_summary()['blocked']:
            return []
        prompt = (
            'Проанализируй только этот обмен пользователя с Тори. '
            'Верни только JSON вида {"suggestions":[...]}, максимум 3 элемента. '
            'Допустимые kind: memory, knowledge, learning. '
            'memory — только устойчивый личный факт или предпочтение, явно сказанное пользователем; '
            'knowledge — полезный долговременный материал из ответа, который стоит сохранить; '
            'learning — пробел или тема, которую стоит дополнительно изучить. '
            'Для memory/knowledge нужны title и body. Для learning нужны topic и question. '
            'Для всех нужны reason и confidence от 0 до 1. Не предлагай пустые или дублирующие вещи.\n\n'
            'Пользователь:\n' + user_text[:6000] + '\n\nТори:\n' + assistant_text[:8000]
        )
        try:
            result = call_yandex_ai(self, prompt, max_output_tokens=500, purpose='reflection')
        except Exception as exc:
            logging.warning('Brain reflection failed: %s', exc)
            return []
        self.add_ai_message('brain', 'system', 'Самоанализ диалога',
                            input_tokens=result['input_tokens'], output_tokens=result['output_tokens'])
        parsed = self._parse_brain_json(result['text'])
        created = []
        with self.connect() as db:
            for raw in parsed[:3]:
                if not isinstance(raw, dict):
                    continue
                kind = raw.get('kind')
                if kind not in {'memory', 'knowledge', 'learning'}:
                    continue
                title = str(raw.get('title') or '').strip()[:300]
                body = str(raw.get('body') or '').strip()[:20000]
                topic = str(raw.get('topic') or '').strip()[:300]
                question = str(raw.get('question') or '').strip()[:8000]
                reason = str(raw.get('reason') or '').strip()[:1000]
                try:
                    confidence = max(0.0, min(float(raw.get('confidence', 0)), 1.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                if kind in {'memory', 'knowledge'} and (not title or not body):
                    continue
                if kind == 'learning' and (not topic or not question):
                    continue
                duplicate = db.execute(
                    "SELECT 1 FROM brain_suggestions WHERE status='pending' AND kind=? "
                    "AND lower(title)=lower(?) AND lower(topic)=lower(?) AND lower(question)=lower(?) LIMIT 1",
                    (kind, title, topic, question)
                ).fetchone()
                if duplicate:
                    continue
                if kind in {'memory', 'knowledge'}:
                    exists = db.execute(
                        'SELECT 1 FROM records WHERE kind=? AND lower(title)=lower(?) LIMIT 1',
                        (kind, title)
                    ).fetchone()
                    if exists:
                        continue
                cursor = db.execute(
                    'INSERT INTO brain_suggestions(kind,title,body,topic,question,reason,confidence,source_message_id) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (kind, title, body, topic, question, reason, confidence, source_message_id)
                )
                created.append(cursor.lastrowid)
        return created

    def decide_brain_suggestion(self, item):
        suggestion_id = item.get('id')
        action = item.get('action')
        if type(suggestion_id) is not int or action not in {'accept', 'reject'}:
            raise ValueError('Некорректное действие с предложением.')
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM brain_suggestions WHERE id=? AND status='pending'",
                (suggestion_id,)
            ).fetchone()
            if not row:
                raise ValueError('Предложение уже обработано или не найдено.')
            row = dict(row)
            if action == 'reject':
                db.execute(
                    "UPDATE brain_suggestions SET status='rejected',decided_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (suggestion_id,)
                )
                return {'ok': True, 'status': 'rejected'}
        if row['kind'] in {'memory', 'knowledge'}:
            with self.connect() as db:
                db.execute(
                    'INSERT INTO records(kind,title,body,source) VALUES (?,?,?,?)',
                    (row['kind'], row['title'], row['body'], 'Тори · предложение из чата')
                )
        else:
            self.learning_enqueue({'topic': row['topic'], 'question': row['question']})
        with self.connect() as db:
            db.execute(
                "UPDATE brain_suggestions SET status='accepted',decided_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (suggestion_id,)
            )
        return {'ok': True, 'status': 'accepted'}

    def chat_state(self):
        config = self.ai_config()
        return {
            'configured': config['configured'],
            'model': config['model'],
            'last_success': config['last_success'],
            'messages': self.ai_messages('chat', 100),
            'usage': self.usage_summary(),
            'suggestions': self.brain_suggestions('pending', 20),
            'goals': self.brain_goals('active', 20),
        }

    @staticmethod
    def _looks_like_dragon_command(text):
        if not isinstance(text, str):
            return False
        clean = text.lower().strip()
        if len(clean) < 4:
            return False
        patterns = (
            r'\b(сделай|проверь|исправь|создай|обнови|запусти|прочитай|открой|'
            r'посмотри|проанализируй|подготовь|сохрани|собери|найди|добавь|'
            r'измени|удали|перепиши|проведи|выполни)\b',
            r'\b(резервн\w*\s+копи\w*|провер\w*\s+проект|провер\w*\s+обновлен\w*)\b',
        )
        return any(re.search(pattern, clean, re.I) for pattern in patterns)

    def dragon_capture_chat_task(self, user_text, assistant_text, source_message_id):
        if not self._looks_like_dragon_command(user_text):
            return None
        prompt = (
            'Определи, является ли сообщение пользователя реальным поручением Дракончику Тоору, '
            'которое стоит связать с очередью действий. Верни только JSON: '
            '{"is_task":true,"title":"...","action_type":"note|project_scan|file_read|file_write|'
            'database_backup|update_check","payload":{},"requires_decision":true,'
            '"plan":["шаг 1","шаг 2"]}. '
            'is_task=false для вопроса, обсуждения, просьбы объяснить или идеи без поручения выполнить. '
            'project_scan — проверить структуру/состояние проекта. database_backup — сделать резервную копию. '
            'update_check — проверить обновления. file_read — прочитать конкретный файл. '
            'file_write — только если пользователь прямо просит изменить конкретный файл; '
            'для file_write всегда requires_decision=true. '
            'Для широких задач вроде "проверь проект и исправь ошибки" используй project_scan, '
            'requires_decision=true и план следующих шагов: сначала проверка, затем анализ, затем предлагаемые исправления. '
            'Для точных безопасных задач backup/update_check requires_decision=false. '
            'План максимум 6 коротких шагов. Ничего не выполняй сам.\n\n'
            'Пользователь:\n' + user_text[:5000] + '\n\n'
            'Ответ помощника для контекста:\n' + assistant_text[:5000]
        )
        try:
            result = call_yandex_ai(self, prompt, max_output_tokens=650, purpose='planning')
        except Exception as exc:
            logging.warning('Dragon chat task detection failed: %s', exc)
            return None
        self.add_ai_message(
            'brain', 'system', 'Связь чата с задачами Дракончика',
            input_tokens=result['input_tokens'], output_tokens=result['output_tokens']
        )
        data = self._parse_json_object(result['text'])
        if data.get('is_task') is not True:
            return None
        action_type = data.get('action_type')
        allowed = {'note','project_scan','file_read','file_write','database_backup','update_check'}
        if action_type not in allowed:
            action_type = 'note'
        payload = data.get('payload') if isinstance(data.get('payload'), dict) else {}
        plan = data.get('plan') if isinstance(data.get('plan'), list) else []
        requires_decision = data.get('requires_decision') is True
        if action_type == 'file_write':
            requires_decision = True
        if action_type in {'note','project_scan'} and len(plan) > 1:
            requires_decision = True
        title = str(data.get('title') or user_text).strip()[:300]
        if not title:
            return None
        return self.dragon_add_task({
            'title': title,
            'action_type': action_type,
            'payload': payload,
            'plan': plan,
            'source_kind': 'chat',
            'source_message_id': source_message_id,
            'requires_decision': requires_decision,
        })

    def dragon_diary(self, days=14):
        days = max(1, min(int(days), 31))
        cutoff = time.strftime(
            '%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - (days - 1) * 86400)
        )
        events = []
        with self.connect() as db:
            for row in db.execute(
                'SELECT id,capability,target,summary,status,details_json,created_at '
                'FROM dragon_actions WHERE created_at>=? ORDER BY created_at DESC LIMIT 250',
                (cutoff,)
            ).fetchall():
                events.append({
                    'kind': 'action',
                    'title': row['summary'],
                    'detail': row['target'],
                    'status': row['status'],
                    'created_at': row['created_at'],
                })
            for row in db.execute(
                "SELECT id,topic,status,review_json,updated_at FROM learning_queue "
                "WHERE status='done' AND updated_at>=? ORDER BY updated_at DESC LIMIT 100",
                (cutoff,)
            ).fetchall():
                try:
                    review = json.loads(row['review_json'] or '{}')
                except (ValueError, TypeError):
                    review = {}
                events.append({
                    'kind': 'learning',
                    'title': 'Изучено: ' + row['topic'],
                    'detail': str(review.get('summary') or '')[:500],
                    'status': 'done',
                    'created_at': row['updated_at'],
                })
            for row in db.execute(
                'SELECT id,title,status,progress_json,updated_at FROM brain_goals '
                'WHERE updated_at>=? ORDER BY updated_at DESC LIMIT 100',
                (cutoff,)
            ).fetchall():
                try:
                    progress = json.loads(row['progress_json'] or '{}')
                except (ValueError, TypeError):
                    progress = {}
                events.append({
                    'kind': 'goal',
                    'title': 'Цель: ' + row['title'],
                    'detail': str(progress.get('summary') or progress.get('auto_assessment') or '')[:500],
                    'status': row['status'],
                    'created_at': row['updated_at'],
                })
        events.sort(key=lambda item: item.get('created_at', ''), reverse=True)
        grouped = []
        by_day = {}
        for event in events:
            day = str(event.get('created_at') or '')[:10]
            if not day:
                continue
            if day not in by_day:
                group = {'day': day, 'events': []}
                by_day[day] = group
                grouped.append(group)
            if len(by_day[day]['events']) < 30:
                by_day[day]['events'].append(event)
        return grouped[:days]

    def chat_send(self, item):
        text = item.get('text', '')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000:
            raise ValueError('Сообщение должно содержать от 1 до 12 000 символов.')
        use_context = item.get('use_context', True) is not False
        analyze = item.get('analyze', True) is not False
        clean = text.strip()
        self.ensure_budget()
        user_message_id = self.add_ai_message('chat', 'user', clean)
        history = self.ai_messages('chat', 14)
        transcript = []
        for message in history[:-1]:
            role = {'user': 'Пользователь', 'tori': 'Тори'}.get(message['role'])
            if role:
                transcript.append(f"{role}: {message['text']}")

        context = self.relevant_context(clean) if use_context else []
        sections = []
        work_context = self._work_context_text()
        if work_context:
            sections.append(
                'Текущий рабочий контекст TOORU. Используй его как ориентацию текущей работы, '
                'но не считай автоматически доказанным фактом:\n' + work_context
            )
        if transcript:
            sections.append('Недавняя история диалога:\n' + '\n\n'.join(transcript[-10:]))
        if context:
            blocks = []
            for index, entry in enumerate(context, 1):
                label = 'Память' if entry['kind'] == 'memory' else 'Знание'
                blocks.append(
                    f"[{index}] {label}: {entry['title']}\n"
                    f"Источник: {entry['source']}\n{entry['excerpt']}"
                )
            sections.append(
                'Релевантный локальный контекст TOORU. Используй его только если он относится к вопросу. '
                'Не выдумывай отсутствующие сведения:\n\n' + '\n\n'.join(blocks)
            )
        sections.append('Текущий вопрос пользователя:\n' + clean)
        prompt = '\n\n---\n\n'.join(sections)

        try:
            result = call_yandex_ai(self, prompt, purpose='chat')
        except Exception as exc:
            self.add_ai_message('chat', 'system', 'Ошибка ответа: ' + str(exc)[:700])
            raise

        visible_context = [
            {
                'id': entry['id'],
                'kind': entry['kind'],
                'title': entry['title'],
                'source': entry['source'],
            }
            for entry in context
        ]
        tori_message_id = self.add_ai_message(
            'chat', 'tori', result['text'],
            input_tokens=result['input_tokens'], output_tokens=result['output_tokens'],
            context=visible_context
        )
        self.mark_ai_success()
        if analyze:
            self.brain_reflect(clean, result['text'], tori_message_id)
        try:
            self.dragon_capture_chat_task(clean, result['text'], user_message_id)
        except Exception as exc:
            logging.warning('Chat → Dragon task link failed: %s', exc)
        return self.chat_state()

    def learning_state(self):
        cutoff = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - STALE_SECONDS))
        with self.connect() as db:
            if not db.execute(
                "SELECT 1 FROM ai_messages WHERE channel='learning' LIMIT 1"
            ).fetchone():
                for old in db.execute(
                    'SELECT id,question,status,response_text,input_tokens,output_tokens,created_at,updated_at '
                    'FROM learning_queue ORDER BY id'
                ).fetchall():
                    db.execute(
                        'INSERT INTO ai_messages(channel,role,text,queue_id,created_at) VALUES (?,?,?,?,?)',
                        ('learning', 'tori', old['question'], old['id'], old['created_at'])
                    )
                    if old['status'] == 'done' and old['response_text']:
                        db.execute(
                            'INSERT INTO ai_messages(channel,role,text,queue_id,input_tokens,output_tokens,created_at) '
                            'VALUES (?,?,?,?,?,?,?)',
                            ('learning', 'qwen', old['response_text'], old['id'],
                             old['input_tokens'], old['output_tokens'], old['updated_at'])
                        )
            db.execute(
                "UPDATE learning_queue SET status='stale', "
                "last_error='Ответ не завершён вовремя. Нажми «Повторить».', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE status='running' AND updated_at<?",
                (cutoff,)
            )
            settings = dict(db.execute(
                "SELECT key,value FROM settings WHERE key IN "
                "('learning_mode','ai_folder_id','ai_model','ai_last_success','ai_auth_type','secret.yandex_api_key',"
                "'brain_auto_learning','brain_auto_min_confidence','brain_auto_daily_limit','brain_auto_chain_limit','brain_auto_day','brain_auto_count')"
            ).fetchall())
            queue = [dict(row) for row in db.execute(
                'SELECT id,topic,question,status,attempts,substr(response_text,1,1200) AS response_text,'
                'last_error,input_tokens,output_tokens,review_json,created_at,updated_at '
                'FROM learning_queue ORDER BY id DESC LIMIT 100'
            )]
            memory_count = db.execute(
                "SELECT count(*) FROM records WHERE kind='memory'"
            ).fetchone()[0]
            knowledge_count = db.execute(
                "SELECT count(*) FROM records WHERE kind='knowledge'"
            ).fetchone()[0]
        for row in queue:
            try:
                row['review'] = json.loads(row.pop('review_json') or '{}')
                if not isinstance(row['review'], dict):
                    row['review'] = {}
            except (ValueError, TypeError):
                row['review'] = {}
        return {
            'mode': settings.get('learning_mode', 'stopped'),
            'configured': bool(settings.get('secret.yandex_api_key')),
            'folder_id': settings.get('ai_folder_id', DEFAULT_YANDEX_FOLDER),
            'model': settings.get('ai_model', DEFAULT_YANDEX_MODEL),
            'auth_type': settings.get('ai_auth_type', 'api_key'),
            'last_success': float(settings.get('ai_last_success', '0') or 0),
            'queue': queue,
            'messages': self.ai_messages('learning', 120),
            'memory_count': memory_count,
            'knowledge_count': knowledge_count,
            'stale_seconds': STALE_SECONDS,
            'usage': self.usage_summary(),
            'suggestions': self.brain_suggestions('pending', 20),
            'automation': {
                'enabled': settings.get('brain_auto_learning', '0') == '1',
                'min_confidence': float(settings.get('brain_auto_min_confidence', '0.75') or 0.75),
                'daily_limit': int(settings.get('brain_auto_daily_limit', '5') or 5),
                'chain_limit': int(settings.get('brain_auto_chain_limit', '3') or 3),
                'today_count': (
                    int(settings.get('brain_auto_count', '0') or 0)
                    if settings.get('brain_auto_day', '') == time.strftime('%Y-%m-%d', time.gmtime())
                    else 0
                ),
            },
        }

    def brain_automation_config(self, item):
        enabled = item.get('enabled')
        if type(enabled) is not bool:
            raise ValueError('Укажите, включать ли автоматическое самообучение.')
        try:
            min_confidence = float(item.get('min_confidence', 0.75))
            daily_limit = int(item.get('daily_limit', 5))
            chain_limit = int(item.get('chain_limit', 3))
        except (TypeError, ValueError):
            raise ValueError('Некорректные параметры самообучения.') from None
        if not 0.5 <= min_confidence <= 0.95:
            raise ValueError('Порог уверенности должен быть от 0.50 до 0.95.')
        if not 1 <= daily_limit <= 20:
            raise ValueError('Дневной лимит должен быть от 1 до 20 задач.')
        if not 1 <= chain_limit <= 6:
            raise ValueError('Глубина одной учебной цепочки должна быть от 1 до 6.')
        with self.connect() as db:
            db.executemany(
                'INSERT OR REPLACE INTO settings VALUES (?,?)',
                [
                    ('brain_auto_learning', '1' if enabled else '0'),
                    ('learning_mode', 'running' if enabled else 'paused'),
                    ('brain_auto_min_confidence', str(min_confidence)),
                    ('brain_auto_daily_limit', str(daily_limit)),
                    ('brain_auto_chain_limit', str(chain_limit)),
                ]
            )
        return self.learning_state()

    def promote_autonomous_learning(self):
        if self.enforce_learning_budget()['blocked']:
            return None
        today = time.strftime('%Y-%m-%d', time.gmtime())
        queued_message = None
        with self.connect() as db:
            settings = dict(db.execute(
                "SELECT key,value FROM settings WHERE key IN "
                "('learning_mode','brain_auto_learning','brain_auto_min_confidence',"
                "'brain_auto_daily_limit','brain_auto_chain_limit','brain_auto_day','brain_auto_count')"
            ).fetchall())
            if settings.get('learning_mode') != 'running' or settings.get('brain_auto_learning') != '1':
                return None
            try:
                threshold = max(0.5, min(float(settings.get('brain_auto_min_confidence', '0.75')), 0.95))
                daily_limit = max(1, min(int(settings.get('brain_auto_daily_limit', '5')), 20))
                chain_limit = max(1, min(int(settings.get('brain_auto_chain_limit', '3')), 6))
            except (TypeError, ValueError):
                threshold, daily_limit, chain_limit = 0.75, 5, 3
            count = int(settings.get('brain_auto_count', '0') or 0) if settings.get('brain_auto_day') == today else 0
            if count >= daily_limit:
                return None
            active = db.execute(
                "SELECT count(*) FROM learning_queue WHERE status IN ('pending','running')"
            ).fetchone()[0]
            if active >= 2:
                return None

            # Сначала продвигаем учебные шаги активных целей: это связывает самообучение с намерениями пользователя.
            goals = db.execute(
                "SELECT id,plan_json FROM brain_goals WHERE status='active' ORDER BY id DESC LIMIT 20"
            ).fetchall()
            for goal in goals:
                try:
                    plan = json.loads(goal['plan_json'] or '[]')
                except (ValueError, TypeError):
                    plan = []
                changed = False
                for index, step in enumerate(plan):
                    if not isinstance(step, dict) or step.get('type') != 'learning' or step.get('status') != 'pending':
                        continue
                    topic = str(step.get('topic') or '').strip()[:300]
                    question = str(step.get('question') or '').strip()[:8000]
                    if not topic or not question:
                        continue
                    duplicate = db.execute(
                        "SELECT id,status FROM learning_queue WHERE lower(topic)=lower(?) AND lower(question)=lower(?) "
                        "AND status IN ('pending','running','done') ORDER BY id DESC LIMIT 1",
                        (topic, question)
                    ).fetchone()
                    if duplicate:
                        step['queue_id'] = duplicate['id']
                        step['status'] = 'done' if duplicate['status'] == 'done' else 'queued'
                        step['auto'] = True
                        changed = True
                        continue
                    cursor = db.execute(
                        'INSERT INTO learning_queue(topic,question) VALUES (?,?)',
                        (topic, question)
                    )
                    queue_id = cursor.lastrowid
                    step['status'] = 'queued'
                    step['queue_id'] = queue_id
                    step['auto'] = True
                    changed = True
                    db.execute(
                        "UPDATE brain_goals SET plan_json=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                        (json.dumps(plan, ensure_ascii=False), goal['id'])
                    )
                    db.executemany(
                        'INSERT OR REPLACE INTO settings VALUES (?,?)',
                        [('brain_auto_day', today), ('brain_auto_count', str(count + 1))]
                    )
                    queued_message = ('goal', queue_id, topic, question, goal['id'])
                    break
                if changed and not queued_message:
                    db.execute(
                        "UPDATE brain_goals SET plan_json=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                        (json.dumps(plan, ensure_ascii=False), goal['id'])
                    )
                if queued_message:
                    break

            if not queued_message:
                rows = db.execute(
                    "SELECT id,topic,question,reason,confidence FROM brain_suggestions "
                    "WHERE status='pending' AND kind='learning' AND confidence>=? "
                    "ORDER BY confidence DESC,id ASC LIMIT 20",
                    (threshold,)
                ).fetchall()
                row = None
                for candidate in rows:
                    # Ограничиваем глубину одной автоматической цепочки по теме в течение суток.
                    topic_count = db.execute(
                        "SELECT count(*) FROM brain_suggestions WHERE kind='learning' AND status='accepted' "
                        "AND lower(topic)=lower(?) AND decided_at>=?",
                        (candidate['topic'], today + 'T00:00:00Z')
                    ).fetchone()[0]
                    if topic_count >= chain_limit:
                        continue
                    row = candidate
                    break
                if not row:
                    return None
                duplicate = db.execute(
                    "SELECT id FROM learning_queue WHERE lower(topic)=lower(?) AND lower(question)=lower(?) "
                    "AND status IN ('pending','running','done') LIMIT 1",
                    (row['topic'], row['question'])
                ).fetchone()
                if duplicate:
                    db.execute(
                        "UPDATE brain_suggestions SET status='accepted',"
                        "decided_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                        (row['id'],)
                    )
                    return None
                cursor = db.execute(
                    'INSERT INTO learning_queue(topic,question) VALUES (?,?)',
                    (row['topic'], row['question'])
                )
                queue_id = cursor.lastrowid
                db.execute(
                    "UPDATE brain_suggestions SET status='accepted',"
                    "decided_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (row['id'],)
                )
                db.executemany(
                    'INSERT OR REPLACE INTO settings VALUES (?,?)',
                    [('brain_auto_day', today), ('brain_auto_count', str(count + 1))]
                )
                queued_message = ('suggestion', queue_id, row['topic'], row['question'], row['id'])

        if not queued_message:
            return None
        source, queue_id, topic, question, source_id = queued_message
        prefix = 'Цель → самообучение: ' if source == 'goal' else 'Самообучение: '
        self.add_ai_message('learning', 'tori', prefix + question, queue_id=queue_id)
        self.add_ai_message(
            'brain', 'system',
            ('Автоматически отправлен учебный шаг цели' if source == 'goal'
             else 'Автоматически продолжена учебная цепочка')
            + ' по теме «' + topic + '».'
        )
        return {'id': queue_id, 'source': source, 'source_id': source_id}

    def learning_control(self, action):
        modes = {'start': 'running', 'pause': 'paused', 'stop': 'stopped'}
        if action not in modes:
            raise ValueError('Неизвестная команда управления обучением.')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                       ('learning_mode', modes[action]))
        return self.learning_state()

    def learning_enqueue(self, item):
        topic = item.get('topic', '')
        question = item.get('question', '')
        if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 300:
            raise ValueError('Тема должна содержать от 1 до 300 символов.')
        if not isinstance(question, str) or not 1 <= len(question.strip()) <= 8000:
            raise ValueError('Вопрос должен содержать от 1 до 8 000 символов.')
        clean_topic = topic.strip()
        clean_question = question.strip()
        with self.connect() as db:
            cursor = db.execute(
                'INSERT INTO learning_queue(topic,question) VALUES (?,?)',
                (clean_topic, clean_question)
            )
            queue_id = cursor.lastrowid
        self.add_ai_message('learning', 'tori', clean_question, queue_id=queue_id)
        return {'id': queue_id}

    def learning_action(self, item):
        queue_id = item.get('id')
        action = item.get('action')
        if type(queue_id) is not int:
            raise ValueError('Некорректный номер задачи.')
        with self.connect() as db:
            row = db.execute('SELECT status FROM learning_queue WHERE id=?', (queue_id,)).fetchone()
            if not row:
                raise ValueError('Задача обучения не найдена.')
            status = row['status']
            if action == 'cancel':
                if status == 'done':
                    raise ValueError('Готовое знание уже сохранено.')
                db.execute(
                    "UPDATE learning_queue SET status='cancelled',last_error='',"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (queue_id,)
                )
            elif action == 'skip':
                if status == 'done':
                    raise ValueError('Готовое знание уже сохранено.')
                db.execute(
                    "UPDATE learning_queue SET status='skipped',last_error='',"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (queue_id,)
                )
            elif action == 'retry':
                if status not in {'error', 'stale', 'cancelled', 'skipped'}:
                    raise ValueError('Повтор доступен только для остановленной или ошибочной задачи.')
                db.execute(
                    "UPDATE learning_queue SET status='pending',response_text='',last_error='',"
                    "input_tokens=0,output_tokens=0,"
                    "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (queue_id,)
                )
                question = db.execute(
                    'SELECT question FROM learning_queue WHERE id=?', (queue_id,)
                ).fetchone()['question']
            else:
                raise ValueError('Неизвестное действие с задачей.')
        if action == 'retry':
            self.add_ai_message('learning', 'tori', 'Повтор: ' + question, queue_id=queue_id)
        return self.learning_state()

    def claim_learning(self):
        if self.enforce_learning_budget()['blocked']:
            return None
        with self.connect() as db:
            mode = db.execute(
                "SELECT value FROM settings WHERE key='learning_mode'"
            ).fetchone()
            key = db.execute(
                "SELECT value FROM settings WHERE key='secret.yandex_api_key'"
            ).fetchone()
            if not mode or mode[0] != 'running' or not key:
                return None
            row = db.execute(
                "SELECT id,topic,question FROM learning_queue "
                "WHERE status='pending' ORDER BY id LIMIT 1"
            ).fetchone()
            if not row:
                return None
            changed = db.execute(
                "UPDATE learning_queue SET status='running',attempts=attempts+1,"
                "last_error='',updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE id=? AND status='pending'",
                (row['id'],)
            ).rowcount
            return dict(row) if changed else None

    def review_learning_answer(self, topic, question, answer):
        if self.usage_summary()['blocked']:
            return {}
        prompt = (
            'Проверь качество учебного ответа. Верни только JSON: '
            '{"verdict":"good|partial|uncertain","confidence":0.0,"quality_score":0.0,'
            '"summary":"...","gaps":[{"topic":"...","question":"...","reason":"...","confidence":0.0}]}. '
            'Оцени полноту, внутреннюю непротиворечивость и полезность ответа. Не придумывай внешнюю проверку источников, '
            'если её не было. quality_score — общая полезность от 0 до 1. gaps — максимум 2 действительно полезных '
            'следующих вопроса; confidence у gap означает уверенность, что этот следующий вопрос действительно нужен.\n\n'
            'Тема: ' + topic + '\nВопрос: ' + question + '\nОтвет:\n' + answer[:12000]
        )
        try:
            result = call_yandex_ai(self, prompt, max_output_tokens=600, purpose='review')
        except Exception as exc:
            logging.warning('Learning review failed: %s', exc)
            return {}
        self.add_ai_message('brain', 'system', 'Самопроверка знания',
                            input_tokens=result['input_tokens'], output_tokens=result['output_tokens'])
        data = self._parse_json_object(result['text'])
        verdict = data.get('verdict') if data.get('verdict') in {'good','partial','uncertain'} else 'uncertain'
        try:
            confidence = max(0.0, min(float(data.get('confidence', 0)), 1.0))
        except (TypeError, ValueError):
            confidence = 0.0
        try:
            quality_score = max(0.0, min(float(data.get('quality_score', confidence)), 1.0))
        except (TypeError, ValueError):
            quality_score = confidence
        review = {
            'verdict': verdict,
            'confidence': confidence,
            'quality_score': quality_score,
            'summary': str(data.get('summary') or '').strip()[:2000],
            'gaps': [],
        }
        gaps = data.get('gaps', []) if isinstance(data.get('gaps'), list) else []
        for raw in gaps[:2]:
            if not isinstance(raw, dict):
                continue
            try:
                gap_confidence = max(0.0, min(float(raw.get('confidence', confidence)), 1.0))
            except (TypeError, ValueError):
                gap_confidence = confidence
            gap = {
                'topic': str(raw.get('topic') or topic).strip()[:300],
                'question': str(raw.get('question') or '').strip()[:8000],
                'reason': str(raw.get('reason') or '').strip()[:1000],
                'confidence': gap_confidence,
            }
            if gap['topic'] and gap['question']:
                review['gaps'].append(gap)
        return review

    def create_review_suggestions(self, review):
        gaps = review.get('gaps', []) if isinstance(review, dict) else []
        created = 0
        with self.connect() as db:
            for gap in gaps[:2]:
                duplicate = db.execute(
                    "SELECT 1 FROM brain_suggestions WHERE status='pending' AND kind='learning' "
                    "AND lower(topic)=lower(?) AND lower(question)=lower(?) LIMIT 1",
                    (gap['topic'], gap['question'])
                ).fetchone()
                if duplicate:
                    continue
                db.execute(
                    "INSERT INTO brain_suggestions(kind,title,topic,question,reason,confidence) "
                    "VALUES ('learning','',?,?,?,?)",
                    (gap['topic'], gap['question'], gap.get('reason',''),
                     float(gap.get('confidence', review.get('confidence', 0)) or 0))
                )
                created += 1
        return created

    def finish_goal_learning(self, queue_id):
        matched_goal = None
        with self.connect() as db:
            goals = db.execute(
                "SELECT id,plan_json,progress_json FROM brain_goals WHERE status='active' ORDER BY id DESC"
            ).fetchall()
            for goal in goals:
                try:
                    plan = json.loads(goal['plan_json'] or '[]')
                except (ValueError, TypeError):
                    plan = []
                changed = False
                for step in plan:
                    if not isinstance(step, dict):
                        continue
                    if (step.get('queue_id') == queue_id and step.get('type') == 'learning'
                            and step.get('status') in {'queued','pending'}):
                        step['status'] = 'done'
                        changed = True
                        matched_goal = goal['id']
                        break
                if changed:
                    try:
                        progress = json.loads(goal['progress_json'] or '{}')
                        if not isinstance(progress, dict):
                            progress = {}
                    except (ValueError, TypeError):
                        progress = {}
                    progress['last_learning_queue_id'] = queue_id
                    progress['last_learning_completed_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                    db.execute(
                        "UPDATE brain_goals SET plan_json=?,progress_json=?,"
                        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                        (json.dumps(plan, ensure_ascii=False),
                         json.dumps(progress, ensure_ascii=False), goal['id'])
                    )
                if matched_goal is not None:
                    break
        return matched_goal

    def auto_rethink_goal(self, goal_id):
        if type(goal_id) is not int or self.usage_summary()['blocked']:
            return None
        with self.connect() as db:
            settings = dict(db.execute(
                "SELECT key,value FROM settings WHERE key IN ('brain_auto_learning','learning_mode')"
            ).fetchall())
            row = db.execute(
                "SELECT id,title,description,plan_json,progress_json FROM brain_goals "
                "WHERE id=? AND status='active'", (goal_id,)
            ).fetchone()
        if not row or settings.get('brain_auto_learning') != '1' or settings.get('learning_mode') != 'running':
            return None
        try:
            plan = json.loads(row['plan_json'] or '[]')
            if not isinstance(plan, list):
                plan = []
        except (ValueError, TypeError):
            plan = []
        try:
            progress = json.loads(row['progress_json'] or '{}')
            if not isinstance(progress, dict):
                progress = {}
        except (ValueError, TypeError):
            progress = {}
        completed = [
            {'title': step.get('title',''), 'topic': step.get('topic',''), 'question': step.get('question','')}
            for step in plan if isinstance(step, dict) and step.get('status') == 'done'
        ][-6:]
        prompt = (
            'Переоцени активную цель после завершённого обучения. Верни только JSON: '
            '{"summary":"...","assessment":"continue|ready|blocked",'
            '"new_steps":[{"title":"...","type":"learning|action","topic":"...","question":"...","reason":"..."}]}. '
            'Учитывай уже завершённые шаги. Не повторяй существующие шаги. '
            'Если для следующего решения не хватает знаний — добавь learning. '
            'action означает действие пользователя/системы и никогда не выполняется автоматически. '
            'Максимум 4 новых шага.\n\n'
            'Цель: ' + row['title'] + '\nОписание: ' + row['description']
            + '\nТекущий план: ' + json.dumps(plan, ensure_ascii=False)
            + '\nЗавершённое обучение: ' + json.dumps(completed, ensure_ascii=False)
        )
        result = call_yandex_ai(self, prompt, max_output_tokens=900, purpose='planning')
        self.add_ai_message(
            'brain', 'system', 'Автопереоценка цели после обучения',
            input_tokens=result['input_tokens'], output_tokens=result['output_tokens']
        )
        data = self._parse_json_object(result['text'])
        existing_keys = {
            (
                str(step.get('type') or ''),
                str(step.get('topic') or '').strip().lower(),
                str(step.get('question') or '').strip().lower(),
                str(step.get('title') or '').strip().lower(),
            )
            for step in plan if isinstance(step, dict)
        }
        added = 0
        raw_steps = data.get('new_steps', []) if isinstance(data.get('new_steps'), list) else []
        for raw in raw_steps[:4]:
            if not isinstance(raw, dict):
                continue
            step_type = raw.get('type')
            if step_type not in {'learning','action'}:
                continue
            step = {
                'title': str(raw.get('title') or '').strip()[:300],
                'type': step_type,
                'topic': str(raw.get('topic') or '').strip()[:300],
                'question': str(raw.get('question') or '').strip()[:8000],
                'reason': str(raw.get('reason') or '').strip()[:1000],
                'status': 'pending',
                'auto_generated': True,
            }
            if not step['title']:
                continue
            if step_type == 'learning' and (not step['topic'] or not step['question']):
                continue
            key = (step_type, step['topic'].lower(), step['question'].lower(), step['title'].lower())
            if key in existing_keys:
                continue
            plan.append(step)
            existing_keys.add(key)
            added += 1
        assessment = data.get('assessment')
        if assessment not in {'continue','ready','blocked'}:
            assessment = 'continue'
        progress.update({
            'summary': str(data.get('summary') or progress.get('summary') or '').strip()[:2000],
            'auto_assessment': assessment,
            'auto_rethought_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'auto_added_steps': added,
        })
        with self.connect() as db:
            db.execute(
                "UPDATE brain_goals SET plan_json=?,progress_json=?,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (json.dumps(plan, ensure_ascii=False), json.dumps(progress, ensure_ascii=False), goal_id)
            )
        return {'goal_id': goal_id, 'assessment': assessment, 'added_steps': added}

    def complete_learning(self, queue_id, text, input_tokens=0, output_tokens=0, review=None):
        clean = text.strip()
        if not clean:
            raise ValueError('AI Studio вернула пустой ответ.')
        with self.connect() as db:
            row = db.execute(
                'SELECT topic,status FROM learning_queue WHERE id=?', (queue_id,)
            ).fetchone()
            if not row or row['status'] != 'running':
                return False
            model = db.execute("SELECT value FROM settings WHERE key='ai_model'").fetchone()[0]
            db.execute(
                "UPDATE learning_queue SET status='done',response_text=?,last_error='',"
                "input_tokens=?,output_tokens=?,review_json=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE id=?",
                (clean[:50000], int(input_tokens or 0), int(output_tokens or 0),
                 json.dumps(review or {}, ensure_ascii=False), queue_id)
            )
            db.execute(
                'INSERT INTO records(kind,title,body,source) VALUES (?,?,?,?)',
                ('knowledge', 'Qwen · ' + row['topic'], clean[:20000],
                 'Yandex AI Studio · ' + model)
            )
        self.add_ai_message(
            'learning', 'qwen', clean, queue_id=queue_id,
            input_tokens=input_tokens, output_tokens=output_tokens
        )
        self.mark_ai_success()
        if review:
            self.create_review_suggestions(review)
        goal_id = self.finish_goal_learning(queue_id)
        self.enforce_learning_budget()
        return {'ok': True, 'goal_id': goal_id}

    def fail_learning(self, queue_id, error):
        message = str(error).strip()[:1000] or 'Неизвестная ошибка AI Studio.'
        with self.connect() as db:
            db.execute(
                "UPDATE learning_queue SET status='error',last_error=?,"
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE id=? AND status='running'",
                (message, queue_id)
            )

        self.add_ai_message('learning', 'system', message, queue_id=queue_id)

    def backup(self):
        destination = self.directory / 'backups' / (time.strftime('tooru-%Y%m%d-%H%M%S-') + secrets.token_hex(3) + '.sqlite3')
        with self.connect() as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
        return destination.name

    def dragon_permissions(self):
        keys = (
            'dragon_project_read','dragon_project_write','dragon_data_manage',
            'dragon_brain_auto','dragon_update_check','dragon_notifications','dragon_delete_files'
        )
        with self.connect() as db:
            values = dict(db.execute(
                "SELECT key,value FROM settings WHERE key IN (" + ",".join("?" for _ in keys) + ")",
                keys
            ).fetchall())
        return {
            'project_read': values.get('dragon_project_read', '1') == '1',
            'project_write': values.get('dragon_project_write', '1') == '1',
            'data_manage': values.get('dragon_data_manage', '1') == '1',
            'brain_auto': values.get('dragon_brain_auto', '1') == '1',
            'update_check': values.get('dragon_update_check', '1') == '1',
            'notifications': values.get('dragon_notifications', '1') == '1',
            'delete_files': values.get('dragon_delete_files', '0') == '1',
        }

    def save_dragon_permissions(self, item):
        allowed = {
            'project_read':'dragon_project_read',
            'project_write':'dragon_project_write',
            'data_manage':'dragon_data_manage',
            'brain_auto':'dragon_brain_auto',
            'update_check':'dragon_update_check',
            'notifications':'dragon_notifications',
            'delete_files':'dragon_delete_files',
        }
        values = []
        for public, key in allowed.items():
            value = item.get(public)
            if type(value) is not bool:
                raise ValueError('Все разрешения Дракончика Тоору должны быть включены или выключены явно.')
            values.append((key, '1' if value else '0'))
        with self.connect() as db:
            db.executemany('INSERT OR REPLACE INTO settings VALUES (?,?)', values)
        self.dragon_notify(
            'success', 'Права обновлены',
            'Разрешения Дракончика Тоору сохранены.', action='permissions'
        )
        return self.dragon_status()

    @staticmethod
    def _dragon_project_path(relative):
        if not isinstance(relative, str) or not relative.strip() or len(relative) > 500:
            raise ValueError('Некорректный путь проекта.')
        relative = relative.replace('\\', '/').strip('/')
        candidate = (ROOT / relative).resolve()
        try:
            candidate.relative_to(ROOT)
        except ValueError:
            raise ValueError('Путь выходит за пределы проекта.') from None
        protected = {'.git', 'python', 'data', 'backups'}
        parts = candidate.relative_to(ROOT).parts
        if parts and (parts[0] in protected or parts[0].startswith('.update-') or parts[0].startswith('.setup-')):
            raise ValueError('Это защищённая служебная зона проекта.')
        return candidate

    def dragon_project_tree(self, limit=1200):
        if not self.dragon_permissions()['project_read']:
            raise ValueError('У Дракончика Тоору отключено чтение проекта.')
        rows = []
        skip = {'.git','python','data','backups','__pycache__'}
        for base, dirs, files in os.walk(ROOT):
            base_path = Path(base)
            rel_base = base_path.relative_to(ROOT)
            dirs[:] = [name for name in dirs if name not in skip and not name.startswith(('.update-','.setup-'))]
            for name in files:
                path = base_path / name
                rel = path.relative_to(ROOT).as_posix()
                try:
                    size = path.stat().st_size
                except OSError:
                    size = 0
                rows.append({'path': rel, 'size': size})
                if len(rows) >= max(1, min(int(limit), 5000)):
                    return rows
        return rows

    def dragon_read_project_file(self, relative):
        if not self.dragon_permissions()['project_read']:
            raise ValueError('У Дракончика Тоору отключено чтение проекта.')
        path = self._dragon_project_path(relative)
        if not path.is_file():
            raise ValueError('Файл проекта не найден.')
        if path.stat().st_size > 300000:
            raise ValueError('Файл слишком большой для чтения через помощника.')
        try:
            text = path.read_text('utf-8')
        except UnicodeDecodeError:
            raise ValueError('Этот файл не является UTF-8 текстом.') from None
        self.dragon_log_action('project_read', path.relative_to(ROOT).as_posix(), 'Прочитан файл проекта')
        return {'path': path.relative_to(ROOT).as_posix(), 'text': text}

    def dragon_write_project_file(self, item):
        if not self.dragon_permissions()['project_write']:
            raise ValueError('У Дракончика Тоору отключено изменение проекта.')
        relative = item.get('path', '')
        content = item.get('content', '')
        if not isinstance(content, str) or len(content.encode('utf-8')) > 500000:
            raise ValueError('Текст файла слишком большой.')
        path = self._dragon_project_path(relative)
        allowed_ext = {'.py','.js','.css','.html','.md','.json','.jsonl','.bat','.yml','.yaml','.txt'}
        if path.suffix.lower() not in allowed_ext:
            raise ValueError('Этот тип файла нельзя менять через помощника.')
        path.parent.mkdir(parents=True, exist_ok=True)
        old = path.read_text('utf-8') if path.exists() else None
        backup_name = ''
        if old is not None:
            backup_dir = self.directory / 'backups' / 'dragon-project'
            backup_dir.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(relative.encode('utf-8')).hexdigest()[:12]
            backup = backup_dir / (time.strftime('%Y%m%d-%H%M%S-') + digest + path.suffix)
            backup.write_text(old, 'utf-8')
            backup_name = str(backup.relative_to(self.directory))
        temp = path.with_name(path.name + '.dragon-new')
        temp.write_text(content, 'utf-8')
        os.replace(temp, path)
        self.dragon_log_action(
            'project_write', path.relative_to(ROOT).as_posix(),
            'Изменён файл проекта', {'backup': backup_name, 'bytes': len(content.encode('utf-8'))}
        )
        self.dragon_notify(
            'success', 'Проект изменён',
            path.relative_to(ROOT).as_posix(), action='project_write'
        )
        return {'ok': True, 'path': path.relative_to(ROOT).as_posix(), 'backup': backup_name}

    def dragon_log_action(self, capability, target, summary, details=None, status='done'):
        with self.connect() as db:
            cursor = db.execute(
                'INSERT INTO dragon_actions(capability,target,summary,status,details_json) VALUES (?,?,?,?,?)',
                (str(capability)[:80], str(target)[:500], str(summary)[:1000],
                 str(status)[:40], json.dumps(details or {}, ensure_ascii=False))
            )
            return cursor.lastrowid

    def dragon_notify(self, level, title, body='', action='', category='', group_key=''):
        if not self.dragon_permissions().get('notifications', True):
            return None
        if level not in {'info','success','warning','error'}:
            level = 'info'
        if not category:
            category = {'success':'completed','error':'error','warning':'decision'}.get(level, 'important')
        if category not in {'important','completed','error','decision'}:
            category = 'important'
        group_key = str(group_key or action or category)[:160]
        clean_title = str(title)[:300]
        clean_body = str(body)[:2000]
        with self.connect() as db:
            previous = db.execute(
                'SELECT id,title,body FROM dragon_notifications '
                'WHERE is_read=0 AND category=? AND group_key=? ORDER BY id DESC LIMIT 1',
                (category, group_key)
            ).fetchone()
            if previous and previous['title'] == clean_title and previous['body'] == clean_body:
                return previous['id']
            cursor = db.execute(
                'INSERT INTO dragon_notifications(level,category,group_key,title,body,action) '
                'VALUES (?,?,?,?,?,?)',
                (level, category, group_key, clean_title, clean_body, str(action)[:120])
            )
            return cursor.lastrowid

    def dragon_notifications(self, unread_only=False, limit=30):
        query = ('SELECT id,level,category,group_key,title,body,action,is_read,created_at '
                 'FROM dragon_notifications')
        params = []
        if unread_only:
            query += ' WHERE is_read=0'
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(max(1, min(int(limit), 100)))
        with self.connect() as db:
            return [dict(row) for row in db.execute(query, params)]

    def dragon_mark_notifications(self, ids=None):
        with self.connect() as db:
            if ids is None:
                db.execute('UPDATE dragon_notifications SET is_read=1 WHERE is_read=0')
            else:
                clean = [value for value in ids if type(value) is int][:100]
                if clean:
                    db.executemany('UPDATE dragon_notifications SET is_read=1 WHERE id=?',
                                   [(value,) for value in clean])
        return {'ok': True}

    def dragon_mode(self):
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='dragon_mode'").fetchone()
        value = row[0] if row else 'suggest'
        return value if value in {'observe','suggest','execute'} else 'suggest'

    def save_dragon_mode(self, mode):
        if mode not in {'observe','suggest','execute'}:
            raise ValueError('Неизвестный режим Дракончика Тоору.')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', ('dragon_mode', mode))
        self.dragon_notify('success', 'Режим изменён', {
            'observe':'Дракончик только наблюдает.',
            'suggest':'Дракончик предлагает действия, но ждёт запуска.',
            'execute':'Дракончик может выполнять разрешённые задачи из очереди.'
        }[mode], action='mode')
        return self.dragon_status()

    def dragon_skills(self):
        permissions = self.dragon_permissions()
        return [
            {'id':'project_scan','title':'Проверка проекта','description':'Просматривает доступную структуру проекта.','available':permissions['project_read']},
            {'id':'file_read','title':'Чтение файлов','description':'Читает разрешённые UTF-8 файлы проекта.','available':permissions['project_read']},
            {'id':'file_write','title':'Изменение файлов','description':'Меняет текстовые файлы с резервной копией и аудитом.','available':permissions['project_write']},
            {'id':'database_backup','title':'Резервная копия','description':'Создаёт копию локальной базы TOORU.','available':permissions['data_manage']},
            {'id':'update_check','title':'Проверка обновлений','description':'Сравнивает локальную ревизию с GitHub main.','available':permissions['update_check']},
            {'id':'brain','title':'Мозг и обучение','description':'Использует Разум, цели и автообучение.','available':permissions['brain_auto']},
        ]

    def dragon_tasks(self, limit=50):
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT * FROM dragon_tasks ORDER BY id DESC LIMIT ?',
                (max(1, min(int(limit), 200)),)
            )]
        for row in rows:
            for field, fallback in (('payload_json', {}), ('result_json', {}), ('plan_json', [])):
                try:
                    parsed = json.loads(row.pop(field) or ('[]' if isinstance(fallback, list) else '{}'))
                    row[field[:-5]] = parsed if isinstance(parsed, type(fallback)) else fallback
                except (ValueError, TypeError):
                    row[field[:-5]] = fallback
            row['requires_decision'] = bool(row.get('requires_decision'))
        return rows

    def dragon_add_task(self, item):
        title = item.get('title', '')
        action_type = item.get('action_type', 'note')
        payload = item.get('payload', {})
        plan = item.get('plan', [])
        source_kind = item.get('source_kind', 'manual')
        source_message_id = item.get('source_message_id')
        requires_decision = item.get('requires_decision', False) is True
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300:
            raise ValueError('Название задачи должно содержать от 1 до 300 символов.')
        allowed = {'note','project_scan','file_read','file_write','database_backup','update_check'}
        if action_type not in allowed:
            raise ValueError('Такой навык пока нельзя ставить в очередь.')
        if not isinstance(payload, dict):
            raise ValueError('Параметры задачи должны быть объектом.')
        if not isinstance(plan, list):
            plan = []
        normalized_plan = []
        for step in plan[:8]:
            if isinstance(step, str):
                text = step.strip()
            elif isinstance(step, dict):
                text = str(step.get('title') or step.get('step') or '').strip()
            else:
                text = ''
            if text:
                normalized_plan.append({'title': text[:300], 'status': 'pending'})
        if source_kind not in {'manual','chat','brain','system'}:
            source_kind = 'manual'
        if type(source_message_id) is not int:
            source_message_id = None
        mode = self.dragon_mode()
        status = 'suggested' if mode in {'observe','suggest'} or requires_decision else 'pending'
        with self.connect() as db:
            cursor = db.execute(
                'INSERT INTO dragon_tasks(title,action_type,payload_json,plan_json,status,'
                'source_kind,source_message_id,requires_decision) VALUES (?,?,?,?,?,?,?,?)',
                (title.strip(), action_type, json.dumps(payload, ensure_ascii=False),
                 json.dumps(normalized_plan, ensure_ascii=False), status,
                 source_kind, source_message_id, 1 if requires_decision else 0)
            )
            task_id = cursor.lastrowid
        details = {'title': title.strip(), 'action_type': action_type, 'status': status,
                   'source_kind': source_kind, 'requires_decision': requires_decision,
                   'plan_steps': len(normalized_plan)}
        self.dragon_log_action('task', str(task_id), 'Создана задача Дракончика', details)
        context_update = {'active_task': title.strip()}
        if normalized_plan:
            context_update['next_step'] = normalized_plan[0]['title']
        self.save_work_context(context_update, source='dragon_task')
        if requires_decision:
            self.dragon_notify(
                'warning', 'Требуется решение', title.strip(),
                action='task', category='decision', group_key=f'task:{task_id}'
            )
        elif source_kind == 'chat':
            self.dragon_notify(
                'info', 'Поручение добавлено', title.strip(),
                action='task', category='important', group_key=f'task:{task_id}'
            )
        return {'id': task_id, 'status': status, 'requires_decision': requires_decision}

    def dragon_task_action(self, item):
        task_id, action = item.get('id'), item.get('action')
        if type(task_id) is not int or action not in {'run','cancel','retry'}:
            raise ValueError('Некорректное действие с задачей.')
        with self.connect() as db:
            row = db.execute('SELECT status FROM dragon_tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise ValueError('Задача не найдена.')
            if action == 'cancel':
                db.execute(
                    "UPDATE dragon_tasks SET status='cancelled',updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (task_id,)
                )
            else:
                db.execute(
                    "UPDATE dragon_tasks SET status='pending',result_json='{}',updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (task_id,)
                )
        if action in {'run','retry'}:
            self.run_next_dragon_task(task_id)
        return self.dragon_status()

    def run_next_dragon_task(self, force_id=None):
        if force_id is None and self.dragon_mode() != 'execute':
            return None
        with self.connect() as db:
            if force_id is None:
                row = db.execute(
                    "SELECT * FROM dragon_tasks WHERE status='pending' AND requires_decision=0 ORDER BY id LIMIT 1"
                ).fetchone()
            else:
                row = db.execute(
                    "SELECT * FROM dragon_tasks WHERE id=? AND status='pending'",
                    (force_id,)
                ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE dragon_tasks SET status='running',updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (row['id'],)
            )
        task = dict(row)
        try:
            payload = json.loads(task.get('payload_json') or '{}')
            if not isinstance(payload, dict):
                payload = {}
        except (ValueError, TypeError):
            payload = {}
        result = {}
        try:
            if task['action_type'] == 'note':
                result = {'message':'Заметка не требует выполнения.'}
            elif task['action_type'] == 'project_scan':
                files = self.dragon_project_tree()
                result = {'files': len(files), 'bytes': sum(int(x.get('size',0)) for x in files)}
            elif task['action_type'] == 'file_read':
                result = self.dragon_read_project_file(payload.get('path',''))
                result = {'path': result['path'], 'chars': len(result['text'])}
            elif task['action_type'] == 'file_write':
                result = self.dragon_write_project_file(payload)
            elif task['action_type'] == 'database_backup':
                result = {'filename': self.backup()}
            elif task['action_type'] == 'update_check':
                status = updater.local_status(ROOT)
                result = {
                    'update_available': status.get('update_available', False),
                    'installed_revision': status.get('installed_revision',''),
                    'latest_revision': status.get('latest_revision',''),
                }
            else:
                raise ValueError('Неизвестный навык задачи.')
            with self.connect() as db:
                db.execute(
                    "UPDATE dragon_tasks SET status='done',result_json=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (json.dumps(result, ensure_ascii=False), task['id'])
                )
            self.dragon_log_action(task['action_type'], str(task['id']), 'Задача Дракончика выполнена', result)
            self.dragon_notify('success', 'Задача выполнена', task['title'], action='task',
                               category='completed', group_key='task:' + str(task['id']))
            return {'id': task['id'], 'status': 'done'}
        except Exception as exc:
            with self.connect() as db:
                db.execute(
                    "UPDATE dragon_tasks SET status='error',result_json=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (json.dumps({'error': str(exc)}, ensure_ascii=False), task['id'])
                )
            self.dragon_log_action(task['action_type'], str(task['id']), 'Ошибка задачи Дракончика',
                                   {'error': str(exc)}, status='error')
            self.dragon_notify('error', 'Ошибка задачи', str(exc), action='task',
                               category='error', group_key='task:' + str(task['id']))
            return {'id': task['id'], 'status': 'error'}

    def dragon_activity(self, days=14):
        days = max(1, min(int(days), 31))
        with self.connect() as db:
            rows = db.execute(
                "SELECT substr(created_at,1,10) AS day,count(*) AS count "
                "FROM dragon_actions WHERE created_at>=datetime('now',?) "
                "GROUP BY substr(created_at,1,10) ORDER BY day",
                (f'-{days-1} days',)
            ).fetchall()
        counts = {row['day']: row['count'] for row in rows}
        result = []
        now = time.time()
        for offset in range(days-1, -1, -1):
            day = time.strftime('%Y-%m-%d', time.gmtime(now - offset*86400))
            result.append({'day': day, 'count': int(counts.get(day, 0))})
        return result

    def dragon_status(self):
        with self.connect() as db:
            name = db.execute("SELECT value FROM settings WHERE key='dragon_name'").fetchone()
            actions = [dict(row) for row in db.execute(
                'SELECT id,capability,target,summary,status,details_json,created_at '
                'FROM dragon_actions ORDER BY id DESC LIMIT 20'
            )]
            unread = db.execute(
                'SELECT count(*) FROM dragon_notifications WHERE is_read=0'
            ).fetchone()[0]
        for row in actions:
            try:
                row['details'] = json.loads(row.pop('details_json') or '{}')
            except (ValueError, TypeError):
                row['details'] = {}
        tasks = self.dragon_tasks(50)
        running = next((task for task in tasks if task['status'] == 'running'), None)
        notifications = self.dragon_notifications(False, 60)
        category_counts = {'important':0,'completed':0,'error':0,'decision':0}
        unread_category_counts = {'important':0,'completed':0,'error':0,'decision':0}
        for note in notifications:
            category = note.get('category') or 'important'
            if category in category_counts:
                category_counts[category] += 1
                if not note.get('is_read'):
                    unread_category_counts[category] += 1
        return {
            'name': name[0] if name else 'Дракончик Тоору',
            'permissions': self.dragon_permissions(),
            'mode': self.dragon_mode(),
            'unread_notifications': unread,
            'notification_counts': category_counts,
            'unread_notification_counts': unread_category_counts,
            'notifications': notifications,
            'actions': actions,
            'tasks': tasks,
            'skills': self.dragon_skills(),
            'activity': self.dragon_activity(14),
            'diary': self.dragon_diary(14),
            'current': running,
        }

    def diagnostics(self):
        with self.connect() as db:
            check = db.execute('PRAGMA quick_check').fetchone()[0]
        return dict(database=check, sqlite=sqlite3.sqlite_version,
                    python=sys.version.split()[0], version=VERSION,
                    database_bytes=self.path.stat().st_size,
                    data_directory=str(self.directory),
                    ai='Настроен' if self.ai_config()['configured'] else 'Не настроен',
                    qwen='Yandex AI Studio · Qwen3.6 35B',
                    access='Только этот компьютер')


def extract_response_text(data):
    if not isinstance(data, dict):
        return ''
    output_text = data.get('output_text')
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    parts = []
    output = data.get('output')
    if not isinstance(output, list):
        return ''

    for item in output:
        if not isinstance(item, dict):
            continue
        content_items = item.get('content')
        if not isinstance(content_items, list):
            continue
        for content in content_items:
            if not isinstance(content, dict):
                continue
            if content.get('type') not in (None, 'output_text'):
                continue
            value = content.get('text')
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return '\n\n'.join(parts).strip()


def call_yandex_ai(storage, question, topic='', max_output_tokens=1500, purpose='learning'):
    config = storage.ai_config(include_secret=True)
    if not config.get('api_key'):
        raise ValueError('Сначала добавь API-ключ Yandex AI Studio.')
    if purpose == 'chat':
        instructions = (
            'Ты Тори — личный помощник пользователя в TOORU · DRAGON. '
            'Отвечай по-русски, естественно, полезно и без лишних повторов. '
            'Не утверждай, что помнишь данные, которых нет в переданной истории. '
            'Если информации недостаточно, скажи об этом прямо.'
        )
    elif purpose in {'reflection', 'planning', 'review', 'reasoning', 'critic'}:
        labels = {
            'reflection': 'внутренний аналитический модуль Тори',
            'planning': 'планировщик целей Тори',
            'review': 'модуль самопроверки знаний Тори',
            'reasoning': 'модуль структурированного логического анализа Тори',
            'critic': 'модуль критической проверки решения Тори',
        }
        instructions = (
            'Ты ' + labels[purpose] + '. '
            'Не разговаривай с пользователем. Возвращай только валидный JSON без Markdown.'
        )
    else:
        instructions = (
            'Ты Qwen — источник знаний для личного помощника Тори. '
            'Отвечай по-русски, точно и структурированно. '
            'Не выдумывай факты. Если в вопросе не хватает данных, явно укажи это. '
            'Дай итог, который можно сохранить в базу знаний. '
            + (('Тема: ' + topic + '.') if topic else '')
        )
    payload = json.dumps({
        'model': f"gpt://{config['folder_id']}/{config['model']}",
        'temperature': 0.3,
        'instructions': instructions,
        'input': question,
        'reasoning': {'effort': 'none'},
        'max_output_tokens': max(16, min(int(max_output_tokens), 8000)),
    }, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(
        YANDEX_AI_URL,
        data=payload,
        headers={
            'Authorization': ('Bearer ' if config.get('auth_type') == 'iam_token' else 'Api-Key ') + config['api_key'],
            'Content-Type': 'application/json',
            'User-Agent': 'TOORU-DRAGON/' + VERSION,
        },
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.load(response)
        storage.mark_ai_success()
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            parsed = json.loads(exc.read().decode('utf-8', errors='replace'))
            detail = parsed.get('error', {}).get('message', '') if isinstance(parsed.get('error'), dict) else ''
        except Exception:
            pass
        if exc.code == 401:
            auth_name = 'IAM-токен' if config.get('auth_type') == 'iam_token' else 'API-ключ'
            hint = (
                f'AI Studio отклонила авторизацию ({auth_name}). '
                'Проверь тип авторизации, срок действия и права ключа/токена.'
            )
            if detail:
                hint += ' Yandex: ' + detail[:300]
            raise ValueError(hint) from None
        raise ValueError(
            f'AI Studio вернула HTTP {exc.code}' + ((': ' + detail[:300]) if detail else '.')
        ) from None
    except urllib.error.URLError as exc:
        raise ValueError('Нет связи с Yandex AI Studio. Проверь интернет.') from exc
    usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
    input_tokens = int(usage.get('input_tokens', 0) or 0)
    output_tokens = int(usage.get('output_tokens', 0) or 0)
    text = extract_response_text(data)
    if not text:
        details = data.get('incomplete_details') if isinstance(data.get('incomplete_details'), dict) else {}
        reason = details.get('reason', '')
        if input_tokens or output_tokens:
            storage.add_ai_message(
                'brain', 'system', 'AI Studio вернула неполный ответ без финального текста.',
                input_tokens=input_tokens, output_tokens=output_tokens
            )
        if reason == 'max_output_tokens':
            raise ValueError(
                'AI Studio израсходовала лимит генерации до финального текста. '
                'TOORU отключил скрытый reasoning для следующих запросов; повтори действие.'
            )
        raise ValueError('AI Studio ответила успешно, но не вернула финальный текст. Повтори действие.')
    return {
        'text': text,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
    }


class LearningWorker(threading.Thread):
    def __init__(self, storage):
        super().__init__(name='tooru-learning', daemon=True)
        self.storage = storage
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.wait(1.5):
            try:
                self.storage.run_next_dragon_task()
            except Exception as exc:
                logging.warning('Dragon task cycle failed: %s', exc)
            try:
                self.storage.promote_autonomous_learning()
            except Exception as exc:
                logging.warning('Brain automation cycle failed: %s', exc)
            try:
                automation = self.storage.learning_state().get('automation', {})
                if automation.get('enabled') and not self.storage.usage_summary().get('blocked'):
                    self.storage.run_brain_experiment()
            except Exception as exc:
                logging.warning('Brain experiment cycle failed: %s', exc)
            item = self.storage.claim_learning()
            if not item:
                continue
            try:
                work = self.storage._work_context_text()
                question = item['question']
                if work:
                    question += (
                        '\n\nТекущий рабочий контекст TOORU. Используй только если относится к теме:\n'
                        + work
                    )
                result = call_yandex_ai(
                    self.storage, question, item['topic'],
                    max_output_tokens=2600, purpose='learning'
                )
                review = self.storage.review_learning_answer(
                    item['topic'], item['question'], result['text']
                )
                completed = self.storage.complete_learning(
                    item['id'], result['text'],
                    result['input_tokens'], result['output_tokens'], review
                )
                goal_id = completed.get('goal_id') if isinstance(completed, dict) else None
                if goal_id is not None:
                    try:
                        self.storage.auto_rethink_goal(goal_id)
                    except Exception as exc:
                        logging.warning('Goal rethink %s failed: %s', goal_id, exc)
            except Exception as exc:
                logging.warning('Learning task %s failed: %s', item['id'], exc)
                self.storage.fail_learning(item['id'], exc)

    def stop(self):
        self.stopping.set()


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
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
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
            path = urllib.parse.urlsplit(self.path).path
            if not self.allowed(path.startswith('/api/')):
                return
            try:
                if path == '/health':
                    self.send(200, {'app': 'TOORU-DRAGON', 'instance': instance, 'version': VERSION})
                elif path in ('/', '/index.html'):
                    asset_revision = web_asset_revision()
                    html = (ROOT / 'web' / 'index.html').read_text('utf-8')
                    html = html.replace('__TOKEN__', token)
                    html = html.replace('__VERSION__', VERSION)
                    html = html.replace('__ASSET_REV__', asset_revision)
                    self.send(200, html, 'text/html; charset=utf-8')
                elif path in ('/app.js', '/style.css'):
                    mime = 'text/javascript' if path.endswith('.js') else 'text/css'
                    self.send(200, (ROOT / 'web' / path[1:]).read_bytes(), mime + '; charset=utf-8')
                elif path == '/api/state':
                    self.send(200, storage.state())
                elif path == '/api/diagnostics':
                    self.send(200, storage.diagnostics())
                elif path == '/api/learning/status':
                    self.send(200, storage.learning_state())
                elif path == '/api/chat/status':
                    self.send(200, storage.chat_state())
                elif path == '/api/brain/reason/status':
                    self.send(200, storage.reasoning_state())
                elif path == '/api/brain/lab':
                    self.send(200, storage.brain_lab_state())
                elif path == '/api/dragon/status':
                    self.send(200, storage.dragon_status())
                elif path == '/api/dragon/project':
                    self.send(200, {'files': storage.dragon_project_tree()})
                elif path == '/api/release':
                    self.send(200, {'version': VERSION, 'asset_revision': web_asset_revision(),
                                    'release': release_manifest()})
                elif path == '/api/update/status':
                    self.send(200, updater.local_status(ROOT))
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
                elif self.path == '/api/ai/config':
                    self.send(200, storage.save_ai_config(item))
                elif self.path == '/api/ai/test':
                    result = call_yandex_ai(storage, 'Ответь только словом: OK.', 'Проверка связи', 32)
                    storage.mark_ai_success()
                    self.send(200, {'ok': True, 'answer': result['text'][:120]})
                elif self.path == '/api/learning/control':
                    self.send(200, storage.learning_control(item.get('action')))
                elif self.path == '/api/learning/queue':
                    self.send(201, storage.learning_enqueue(item))
                elif self.path == '/api/learning/action':
                    self.send(200, storage.learning_action(item))
                elif self.path == '/api/chat/send':
                    self.send(200, storage.chat_send(item))
                elif self.path == '/api/brain/action':
                    self.send(200, storage.decide_brain_suggestion(item))
                elif self.path == '/api/brain/automation':
                    self.send(200, storage.brain_automation_config(item))
                elif self.path == '/api/brain/reason':
                    self.send(200, storage.reason_problem(item))
                elif self.path == '/api/brain/context':
                    self.send(200, storage.save_work_context(item))
                elif self.path == '/api/brain/experiment':
                    self.send(201, storage.create_brain_experiment(item))
                elif self.path == '/api/brain/experiment/action':
                    experiment_id = item.get('id')
                    if type(experiment_id) is not int:
                        raise ValueError('Некорректный номер эксперимента.')
                    result = storage.run_brain_experiment(experiment_id)
                    if not result:
                        raise ValueError('Эксперимент не найден или уже выполняется.')
                    self.send(200, result)
                elif self.path == '/api/brain/goal':
                    self.send(201, storage.create_goal(item))
                elif self.path == '/api/brain/goal/action':
                    self.send(200, storage.goal_action(item))
                elif self.path == '/api/dragon/permissions':
                    self.send(200, storage.save_dragon_permissions(item))
                elif self.path == '/api/dragon/mode':
                    self.send(200, storage.save_dragon_mode(item.get('mode')))
                elif self.path == '/api/dragon/task':
                    self.send(201, storage.dragon_add_task(item))
                elif self.path == '/api/dragon/task/action':
                    self.send(200, storage.dragon_task_action(item))
                elif self.path == '/api/dragon/project/read':
                    self.send(200, storage.dragon_read_project_file(item.get('path', '')))
                elif self.path == '/api/dragon/project/write':
                    self.send(200, storage.dragon_write_project_file(item))
                elif self.path == '/api/dragon/notifications/read':
                    self.send(200, storage.dragon_mark_notifications(item.get('ids')))
                elif self.path == '/api/backup':
                    self.send(200, {'filename': storage.backup()})
                elif self.path == '/api/update/start':
                    status = updater.local_status(ROOT)
                    if status['tracked'] and not status['update_available']:
                        self.send(200, {'ok': True, 'already_current': True})
                    else:
                        database_backup = storage.backup()
                        updater.start_background_update(ROOT, os.getpid(), status['latest_revision'])
                        self.send(202, {
                            'ok': True,
                            'restarting': True,
                            'database_backup': database_backup,
                            'latest_revision': status['latest_revision'],
                        })
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                else:
                    self.send(404, {'error': 'Действие не найдено.'})
            except (ValueError, UnicodeError) as exc:
                self.send(400, {'error': str(exc)})
            except Exception:
                logging.exception('POST failed')
                self.send(500, {'error': 'Не удалось выполнить действие. Подробности в журнале.'})

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
    try:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        probe = args.data_dir / ('.write-check-' + secrets.token_hex(8))
        with probe.open('xb') as file:
            file.write(b'ok')
        probe.unlink()
        lock = InstanceLock(args.data_dir / 'instance.lock')
    except OSError as exc:
        print('Нет доступа для записи в папку данных:', args.data_dir)
        print('Распакуйте проект на доступный для записи диск или флешку.')
        print('Подробности:', exc)
        return 1
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
    worker = None
    try:
        storage = Storage(args.data_dir)
        logging.basicConfig(filename=storage.directory / 'logs' / 'app.log',
                            encoding='utf-8', level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')
        worker = LearningWorker(storage)
        worker.start()
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
        if worker:
            worker.stop()
            worker.join(timeout=2)
        if server:
            server.server_close()
        state_file.unlink(missing_ok=True)
        lock.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
