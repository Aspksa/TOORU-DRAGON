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
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
VERSION = '0.0.0'
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
            if version > 6:
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
        return dict(version=VERSION, settings=settings, counts=counts, records=records,
                    ai_connected=learning['configured'] and learning['last_success'] > 0,
                    learning=learning, chat=chat)

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
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           ('secret.yandex_api_key', api_key.strip()))
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
        }

    def chat_send(self, item):
        text = item.get('text', '')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 12000:
            raise ValueError('Сообщение должно содержать от 1 до 12 000 символов.')
        use_context = item.get('use_context', True) is not False
        analyze = item.get('analyze', True) is not False
        clean = text.strip()
        self.ensure_budget()
        self.add_ai_message('chat', 'user', clean)
        history = self.ai_messages('chat', 14)
        transcript = []
        for message in history[:-1]:
            role = {'user': 'Пользователь', 'tori': 'Тори'}.get(message['role'])
            if role:
                transcript.append(f"{role}: {message['text']}")

        context = self.relevant_context(clean) if use_context else []
        sections = []
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
                "('learning_mode','ai_folder_id','ai_model','ai_last_success','secret.yandex_api_key')"
            ).fetchall())
            queue = [dict(row) for row in db.execute(
                'SELECT id,topic,question,status,attempts,substr(response_text,1,1200) AS response_text,'
                'last_error,input_tokens,output_tokens,created_at,updated_at '
                'FROM learning_queue ORDER BY id DESC LIMIT 100'
            )]
            memory_count = db.execute(
                "SELECT count(*) FROM records WHERE kind='memory'"
            ).fetchone()[0]
            knowledge_count = db.execute(
                "SELECT count(*) FROM records WHERE kind='knowledge'"
            ).fetchone()[0]
        return {
            'mode': settings.get('learning_mode', 'stopped'),
            'configured': bool(settings.get('secret.yandex_api_key')),
            'folder_id': settings.get('ai_folder_id', DEFAULT_YANDEX_FOLDER),
            'model': settings.get('ai_model', DEFAULT_YANDEX_MODEL),
            'last_success': float(settings.get('ai_last_success', '0') or 0),
            'queue': queue,
            'messages': self.ai_messages('learning', 120),
            'memory_count': memory_count,
            'knowledge_count': knowledge_count,
            'stale_seconds': STALE_SECONDS,
            'usage': self.usage_summary(),
        }

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

    def complete_learning(self, queue_id, text, input_tokens=0, output_tokens=0):
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
                "input_tokens=?,output_tokens=?,updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE id=?",
                (clean[:50000], int(input_tokens or 0), int(output_tokens or 0), queue_id)
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
        self.enforce_learning_budget()
        return True

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
    elif purpose == 'reflection':
        instructions = (
            'Ты внутренний аналитический модуль Тори. '
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
    text = extract_response_text(data)
    if not text:
        raise ValueError('AI Studio вернула ответ без текста.')
    usage = data.get('usage') if isinstance(data.get('usage'), dict) else {}
    return {
        'text': text,
        'input_tokens': int(usage.get('input_tokens', 0) or 0),
        'output_tokens': int(usage.get('output_tokens', 0) or 0),
    }


class LearningWorker(threading.Thread):
    def __init__(self, storage):
        super().__init__(name='tooru-learning', daemon=True)
        self.storage = storage
        self.stopping = threading.Event()

    def run(self):
        while not self.stopping.wait(1.5):
            item = self.storage.claim_learning()
            if not item:
                continue
            try:
                result = call_yandex_ai(self.storage, item['question'], item['topic'], purpose='learning')
                self.storage.complete_learning(
                    item['id'], result['text'],
                    result['input_tokens'], result['output_tokens']
                )
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
                elif self.path == '/api/learning/status':
                    self.send(200, storage.learning_state())
                elif self.path == '/api/chat/status':
                    self.send(200, storage.chat_state())
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
                elif self.path == '/api/backup':
                    self.send(200, {'filename': storage.backup()})
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
