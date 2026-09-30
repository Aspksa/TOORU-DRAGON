"""Integration tests; use a temporary data directory, never the user's database."""
import json
import io
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

    def test_ai_config_is_secret_and_learning_queue_controls(self):
        state = self.request('/api/state')
        self.assertEqual(state['learning']['folder_id'], app.DEFAULT_YANDEX_FOLDER)
        self.assertEqual(state['learning']['model'], app.DEFAULT_YANDEX_MODEL)
        self.assertFalse(state['learning']['configured'])

        configured = self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        self.assertTrue(configured['configured'])
        self.assertNotIn('api_key', configured)
        public_state = self.request('/api/state')
        self.assertNotIn('secret.yandex_api_key', public_state['settings'])
        self.assertNotIn('secret-test-key-value', json.dumps(public_state, ensure_ascii=False))

        first = self.request('/api/learning/queue', {'topic': 'Python', 'question': 'Что такое WAL?'})
        second = self.request('/api/learning/queue', {'topic': 'SQLite', 'question': 'Что такое FTS5?'})
        queue = self.request('/api/learning/status')['queue']
        self.assertEqual({row['id'] for row in queue}, {first['id'], second['id']})

        self.request('/api/learning/action', {'id': first['id'], 'action': 'skip'})
        skipped = next(x for x in self.request('/api/learning/status')['queue'] if x['id'] == first['id'])
        self.assertEqual(skipped['status'], 'skipped')
        self.request('/api/learning/action', {'id': first['id'], 'action': 'retry'})
        retried = next(x for x in self.request('/api/learning/status')['queue'] if x['id'] == first['id'])
        self.assertEqual(retried['status'], 'pending')
        self.request('/api/learning/action', {'id': second['id'], 'action': 'cancel'})
        cancelled = next(x for x in self.request('/api/learning/status')['queue'] if x['id'] == second['id'])
        self.assertEqual(cancelled['status'], 'cancelled')

    def test_learning_claim_complete_and_stale_detection(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        queued = self.request('/api/learning/queue', {'topic': 'Python', 'question': 'Что такое WAL?'})
        self.request('/api/learning/control', {'action': 'start'})
        claimed = self.storage.claim_learning()
        self.assertEqual(claimed['id'], queued['id'])
        self.assertTrue(self.storage.complete_learning(queued['id'], 'WAL — журнал предзаписи.', 100, 40))
        state = self.request('/api/state')
        task = next(x for x in state['learning']['queue'] if x['id'] == queued['id'])
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['input_tokens'], 100)
        self.assertEqual(task['output_tokens'], 40)
        self.assertEqual(state['records']['knowledge'][0]['source'],
                         'Yandex AI Studio · qwen3.6-35b-a3b/latest')
        messages = state['learning']['messages']
        self.assertEqual([m['role'] for m in messages[-2:]], ['tori', 'qwen'])
        self.assertIn('Что такое WAL?', messages[-2]['text'])
        self.assertIn('WAL', messages[-1]['text'])

        stale = self.request('/api/learning/queue', {'topic': 'Сбой', 'question': 'Зависни'})
        with self.storage.connect() as db:
            db.execute(
                "UPDATE learning_queue SET status='running',updated_at='2000-01-01T00:00:00Z' WHERE id=?",
                (stale['id'],)
            )
        stale_task = next(x for x in self.request('/api/learning/status')['queue'] if x['id'] == stale['id'])
        self.assertEqual(stale_task['status'], 'stale')
        self.assertIn('Повторить', stale_task['last_error'])

    def test_yandex_auth_type_selects_header_and_explains_401(self):
        self.storage.save_ai_config({
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'iam-token-test',
            'auth_type': 'iam_token',
        })
        with patch('app.urllib.request.urlopen',
                   return_value=io.BytesIO(b'{"output_text":"OK","usage":{}}')) as call:
            app.call_yandex_ai(self.storage, 'test')
        request = call.call_args.args[0]
        self.assertEqual(request.headers['Authorization'], 'Bearer iam-token-test')

        self.storage.save_ai_config({
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'bad-api-key',
            'auth_type': 'api_key',
        })
        error = urllib.error.HTTPError(
            app.YANDEX_AI_URL, 401, 'Unauthorized', {},
            io.BytesIO(b'{"error":{"message":"Unknown api key"}}')
        )
        with patch('app.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'отклонила авторизацию'):
                app.call_yandex_ai(self.storage, 'test')
        self.assertEqual(self.storage.ai_config()['auth_type'], 'api_key')

    def test_ai_test_endpoint_uses_model_without_exposing_key(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        with patch('app.call_yandex_ai', return_value={'text': 'OK', 'input_tokens': 2, 'output_tokens': 1}) as call:
            result = self.request('/api/ai/test', {})
        self.assertTrue(result['ok'])
        self.assertEqual(result['answer'], 'OK')
        call.assert_called_once()
        self.assertGreater(self.request('/api/learning/status')['last_success'], 0)

    def test_real_tori_chat_persists_user_and_assistant_messages(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        with patch('app.call_yandex_ai', side_effect=[
            {'text': 'Привет! Я Тори.', 'input_tokens': 12, 'output_tokens': 6},
            {'text': '{"suggestions":[]}', 'input_tokens': 5, 'output_tokens': 3},
        ]) as call:
            chat = self.request('/api/chat/send', {'text': 'Привет'})
        self.assertEqual([m['role'] for m in chat['messages'][-2:]], ['user', 'tori'])
        self.assertEqual(chat['messages'][-1]['text'], 'Привет! Я Тори.')
        self.assertEqual(chat['messages'][-1]['input_tokens'], 12)
        self.assertEqual(chat['messages'][-1]['output_tokens'], 6)
        self.assertEqual(call.call_args_list[0].kwargs['purpose'], 'chat')
        self.assertEqual(call.call_args_list[1].kwargs['purpose'], 'reflection')
        public = self.request('/api/state')
        self.assertEqual(public['chat']['messages'][-1]['role'], 'tori')

    def test_chat_rag_uses_only_relevant_memory_and_knowledge(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        self.request('/api/records', {
            'kind': 'memory', 'title': 'Любимый язык',
            'body': 'Пользователь предпочитает Python для автоматизации.'
        })
        self.request('/api/records', {
            'kind': 'knowledge', 'title': 'Python и SQLite',
            'body': 'SQLite хорошо подходит для локальных приложений на Python.'
        })
        self.request('/api/records', {
            'kind': 'knowledge', 'title': 'Сад',
            'body': 'Помидоры любят солнечное место.'
        })
        with patch('app.call_yandex_ai', return_value={
            'text': 'Используй Python и SQLite.', 'input_tokens': 30, 'output_tokens': 8
        }) as call:
            chat = self.request('/api/chat/send', {
                'text': 'Что использовать для Python приложения с SQLite?',
                'use_context': True,
            })
        prompt = call.call_args_list[0].args[1]
        self.assertIn('Любимый язык', prompt)
        self.assertIn('Python и SQLite', prompt)
        self.assertNotIn('Помидоры', prompt)
        context = chat['messages'][-1]['context']
        self.assertEqual({item['title'] for item in context}, {'Любимый язык', 'Python и SQLite'})
        self.assertNotIn('excerpt', context[0])

    def test_chat_context_can_be_disabled(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        self.request('/api/records', {
            'kind': 'memory', 'title': 'Секретный контекст',
            'body': 'Эта запись не должна попасть в отключённый RAG.'
        })
        with patch('app.call_yandex_ai', return_value={
            'text': 'Ответ без памяти.', 'input_tokens': 4, 'output_tokens': 4
        }) as call:
            chat = self.request('/api/chat/send', {
                'text': 'Секретный контекст',
                'use_context': False,
            })
        self.assertNotIn('Эта запись не должна', call.call_args.args[1])
        self.assertEqual(chat['messages'][-1]['context'], [])

    def test_usage_costs_and_monthly_budget_blocking(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
            'input_rub_per_1k': 0.2,
            'output_rub_per_1k': 0.3,
            'monthly_budget_rub': 0.01,
        })
        self.storage.add_ai_message('chat', 'tori', 'Ответ', input_tokens=1000, output_tokens=1000)
        usage = self.storage.usage_summary()
        self.assertAlmostEqual(usage['month']['cost_rub'], 0.5, places=3)
        self.assertTrue(usage['blocked'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/api/chat/send', {'text': 'Не отправлять'})
        self.assertEqual(error.exception.code, 400)
        with self.storage.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('learning_mode','running')")
        self.assertIsNone(self.storage.claim_learning())
        self.assertEqual(self.storage.learning_state()['mode'], 'paused')

    def test_zero_budget_disables_blocking(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
            'input_rub_per_1k': 0.2,
            'output_rub_per_1k': 0.3,
            'monthly_budget_rub': 0,
        })
        self.storage.add_ai_message('chat', 'tori', 'Ответ', input_tokens=500000, output_tokens=500000)
        self.assertFalse(self.storage.usage_summary()['blocked'])

    def test_reasoning_engine_structures_decision_and_persists_visible_summary(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        self.request('/api/records', {
            'kind': 'knowledge',
            'title': 'TOORU updater',
            'body': 'Обновлятор работает через GitHub ZIP и сохраняет data/.',
        })
        payload = {
            'summary': 'Нужно выбрать следующий модуль.',
            'facts': ['Обновлятор уже работает.'],
            'assumptions': ['Нужен следующий приоритет.'],
            'options': [
                {'title': 'Логика', 'pros': ['Повышает качество решений'], 'cons': ['Тратит токены']},
                {'title': 'Мобильный доступ', 'pros': ['Удобство'], 'cons': ['Нужна авторизация']},
            ],
            'contradictions': ['Нельзя открывать сеть без авторизации.'],
            'decision': 'Сначала развивать локальную логику.',
            'confidence': 0.84,
            'next_step': 'Добавить структурированный модуль анализа.',
        }
        with patch('app.call_yandex_ai', return_value={
            'text': json.dumps(payload, ensure_ascii=False),
            'input_tokens': 50,
            'output_tokens': 40,
        }) as call:
            result = self.request('/api/brain/reason', {
                'problem': 'Что развивать дальше в TOORU?',
                'use_context': True,
            })
        self.assertEqual(call.call_args.kwargs['purpose'], 'reasoning')
        self.assertEqual(result['items'][0]['result']['decision'], 'Сначала развивать локальную логику.')
        self.assertAlmostEqual(result['items'][0]['result']['confidence'], 0.84, places=2)
        self.assertEqual(result['items'][0]['context'][0]['title'], 'TOORU updater')
        state = self.request('/api/state')
        self.assertEqual(state['reasoning']['items'][0]['problem'], 'Что развивать дальше в TOORU?')
        with self.storage.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 8)

    def test_reasoning_engine_handles_invalid_model_json_without_hidden_trace(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        with patch('app.call_yandex_ai', return_value={
            'text': 'не json',
            'input_tokens': 5,
            'output_tokens': 2,
        }):
            result = self.request('/api/brain/reason', {
                'problem': 'Проверить неизвестную задачу',
                'use_context': False,
            })
        item = result['items'][0]
        self.assertEqual(item['result']['facts'], [])
        self.assertEqual(item['result']['assumptions'], [])
        self.assertEqual(item['result']['decision'], '')
        self.assertNotIn('analysis', json.dumps(item, ensure_ascii=False).lower())

    def test_brain_suggestions_require_user_approval(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        reflection = {
            'suggestions': [
                {
                    'kind': 'memory',
                    'title': 'Любит краткие ответы',
                    'body': 'Пользователь предпочитает краткие ответы.',
                    'reason': 'Пользователь сказал это явно.',
                    'confidence': 0.95,
                },
                {
                    'kind': 'knowledge',
                    'title': 'WAL',
                    'body': 'WAL — журнал предзаписи SQLite.',
                    'reason': 'Полезный материал из ответа.',
                    'confidence': 0.8,
                },
                {
                    'kind': 'learning',
                    'topic': 'SQLite',
                    'question': 'Когда WAL лучше rollback journal?',
                    'reason': 'Есть полезный пробел для изучения.',
                    'confidence': 0.75,
                },
            ]
        }
        with patch('app.call_yandex_ai', side_effect=[
            {'text': 'Поняла.', 'input_tokens': 10, 'output_tokens': 4},
            {'text': json.dumps(reflection, ensure_ascii=False), 'input_tokens': 20, 'output_tokens': 30},
        ]):
            chat = self.request('/api/chat/send', {
                'text': 'Я люблю краткие ответы. Расскажи про WAL.',
                'analyze': True,
            })
        self.assertEqual(len(chat['suggestions']), 3)
        state = self.request('/api/state')
        self.assertEqual(state['records']['memory'], [])
        self.assertEqual(state['records']['knowledge'], [])
        self.assertEqual(state['learning']['queue'], [])

        by_kind = {item['kind']: item for item in chat['suggestions']}
        self.request('/api/brain/action', {'id': by_kind['memory']['id'], 'action': 'accept'})
        self.request('/api/brain/action', {'id': by_kind['knowledge']['id'], 'action': 'accept'})
        self.request('/api/brain/action', {'id': by_kind['learning']['id'], 'action': 'accept'})
        state = self.request('/api/state')
        self.assertEqual(state['records']['memory'][0]['title'], 'Любит краткие ответы')
        self.assertEqual(state['records']['knowledge'][0]['title'], 'WAL')
        self.assertEqual(state['learning']['queue'][0]['topic'], 'SQLite')

    def test_brain_suggestion_can_be_rejected_and_analysis_disabled(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        with patch('app.call_yandex_ai', return_value={
            'text': 'Ответ без самоанализа.', 'input_tokens': 8, 'output_tokens': 4
        }) as call:
            chat = self.request('/api/chat/send', {'text': 'Тест', 'analyze': False})
        self.assertEqual(len(call.call_args_list), 1)
        self.assertEqual(chat['suggestions'], [])

        with self.storage.connect() as db:
            cursor = db.execute(
                "INSERT INTO brain_suggestions(kind,title,body,reason,confidence) VALUES ('memory','Тест','Тело','Причина',0.5)"
            )
            suggestion_id = cursor.lastrowid
        result = self.request('/api/brain/action', {'id': suggestion_id, 'action': 'reject'})
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(self.storage.brain_suggestions('pending'), [])

    def test_brain_usage_is_counted_in_budget(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
            'input_rub_per_1k': 1,
            'output_rub_per_1k': 1,
            'monthly_budget_rub': 100,
        })
        self.storage.add_ai_message('brain', 'system', 'Самоанализ',
                                    input_tokens=100, output_tokens=50)
        usage = self.storage.usage_summary()
        self.assertEqual(usage['month']['input_tokens'], 100)
        self.assertEqual(usage['month']['output_tokens'], 50)
        self.assertAlmostEqual(usage['month']['cost_rub'], 0.15, places=3)

    def test_goal_planner_requires_explicit_step_actions(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        plan = {
            'summary': 'Сначала изучить архитектуру, затем реализовать.',
            'steps': [
                {
                    'title': 'Изучить очереди',
                    'type': 'learning',
                    'topic': 'Очереди задач',
                    'question': 'Как надёжно проектировать очередь задач?',
                    'reason': 'Нужно знание перед реализацией.',
                },
                {
                    'title': 'Сделать прототип',
                    'type': 'action',
                    'topic': '',
                    'question': '',
                    'reason': 'Практический шаг.',
                },
            ],
        }
        with patch('app.call_yandex_ai', return_value={
            'text': json.dumps(plan, ensure_ascii=False),
            'input_tokens': 40, 'output_tokens': 60
        }) as call:
            result = self.request('/api/brain/goal', {
                'title': 'Улучшить мозги Тори',
                'description': 'Сделать планирование и обучение.'
            })
        self.assertEqual(call.call_args.kwargs['purpose'], 'planning')
        self.assertEqual(len(result['goals']), 1)
        goal = result['goals'][0]
        self.assertEqual(len(goal['plan']), 2)
        self.assertEqual(self.request('/api/learning/status')['queue'], [])

        self.request('/api/brain/goal/action', {
            'id': goal['id'], 'action': 'queue_learning', 'step': 0
        })
        queue = self.request('/api/learning/status')['queue']
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['topic'], 'Очереди задач')

        self.request('/api/brain/goal/action', {
            'id': goal['id'], 'action': 'complete_step', 'step': 1
        })
        state = self.request('/api/state')
        goal = state['chat']['goals'][0]
        self.assertEqual(goal['plan'][0]['status'], 'queued')
        self.assertEqual(goal['plan'][1]['status'], 'done')

    def test_brain_automation_promotes_only_learning_suggestions_with_limits(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        self.request('/api/learning/control', {'action': 'start'})
        state = self.request('/api/brain/automation', {
            'enabled': True,
            'min_confidence': 0.8,
            'daily_limit': 2,
        })
        self.assertTrue(state['automation']['enabled'])
        with self.storage.connect() as db:
            db.execute(
                "INSERT INTO brain_suggestions(kind,title,body,reason,confidence) "
                "VALUES ('memory','Имя','Антон','Личный факт',0.99)"
            )
            db.execute(
                "INSERT INTO brain_suggestions(kind,title,topic,question,reason,confidence) "
                "VALUES ('learning','','SQLite','Что такое WAL?','Учебный пробел',0.85)"
            )
            db.execute(
                "INSERT INTO brain_suggestions(kind,title,topic,question,reason,confidence) "
                "VALUES ('learning','','Python','Что такое GIL?','Ниже порога',0.70)"
            )
        promoted = self.storage.promote_autonomous_learning()
        self.assertIsNotNone(promoted)
        state = self.request('/api/learning/status')
        active = [x for x in state['queue'] if x['status'] in {'pending','running'}]
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['topic'], 'SQLite')
        self.assertEqual(state['automation']['today_count'], 1)
        pending = state['suggestions']
        self.assertTrue(any(x['kind'] == 'memory' for x in pending))
        self.assertTrue(any(x['kind'] == 'learning' and x['topic'] == 'Python' for x in pending))

    def test_brain_automation_respects_daily_limit(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        self.request('/api/learning/control', {'action': 'start'})
        self.request('/api/brain/automation', {
            'enabled': True,
            'min_confidence': 0.5,
            'daily_limit': 1,
        })
        with self.storage.connect() as db:
            for index in range(2):
                db.execute(
                    "INSERT INTO brain_suggestions(kind,title,topic,question,reason,confidence) "
                    "VALUES ('learning','',?,?,?,0.9)",
                    (f'Topic {index}', f'Question {index}', 'test')
                )
        self.assertIsNotNone(self.storage.promote_autonomous_learning())
        with self.storage.connect() as db:
            db.execute("UPDATE learning_queue SET status='done'")
        self.assertIsNone(self.storage.promote_autonomous_learning())
        self.assertEqual(self.request('/api/learning/status')['automation']['today_count'], 1)

    def test_learning_self_review_is_saved_and_gaps_need_approval(self):
        self.request('/api/ai/config', {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        queued = self.request('/api/learning/queue', {
            'topic': 'SQLite',
            'question': 'Когда использовать WAL?'
        })
        self.request('/api/learning/control', {'action': 'start'})
        item = self.storage.claim_learning()
        self.assertEqual(item['id'], queued['id'])

        review_json = {
            'verdict': 'partial',
            'confidence': 0.72,
            'summary': 'Ответ полезный, но не разобраны ограничения WAL.',
            'gaps': [{
                'topic': 'SQLite WAL',
                'question': 'Какие ограничения и недостатки есть у WAL?',
                'reason': 'Нужно понять границы применения.'
            }]
        }
        with patch('app.call_yandex_ai', return_value={
            'text': json.dumps(review_json, ensure_ascii=False),
            'input_tokens': 25, 'output_tokens': 30
        }) as call:
            review = self.storage.review_learning_answer(
                'SQLite', 'Когда использовать WAL?', 'WAL полезен при параллельном чтении.'
            )
        self.assertEqual(call.call_args.kwargs['purpose'], 'review')
        self.assertEqual(review['verdict'], 'partial')

        self.assertTrue(self.storage.complete_learning(
            queued['id'], 'WAL полезен при параллельном чтении.', 100, 50, review
        ))
        state = self.request('/api/learning/status')
        task = next(x for x in state['queue'] if x['id'] == queued['id'])
        self.assertEqual(task['review']['verdict'], 'partial')
        self.assertAlmostEqual(task['review']['confidence'], 0.72, places=2)

        suggestions = [x for x in state['suggestions'] if x['kind'] == 'learning']
        self.assertEqual(len(suggestions), 1)
        self.assertIn('ограничения', suggestions[0]['question'].lower())
        # Самопроверка только предлагает следующий вопрос, не ставит его в очередь сама.
        self.assertEqual(len(state['queue']), 1)

    def test_successful_http_marks_connection_and_disables_reasoning(self):
        self.storage.save_ai_config({
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        payload = {
            'output': [{'type':'message','content':[{'type':'output_text','text':'OK'}]}],
            'usage': {'input_tokens': 7, 'output_tokens': 3},
        }
        with patch('app.urllib.request.urlopen',
                   return_value=io.BytesIO(json.dumps(payload).encode('utf-8'))) as call:
            result = app.call_yandex_ai(self.storage, 'test', purpose='chat')
        request = call.call_args.args[0]
        body = json.loads(request.data.decode('utf-8'))
        self.assertEqual(body['reasoning']['effort'], 'none')
        self.assertEqual(result['text'], 'OK')
        self.assertGreater(self.storage.ai_config()['last_success'], 0)

    def test_incomplete_empty_response_counts_usage_and_keeps_connection_verified(self):
        self.storage.save_ai_config({
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
        })
        payload = {
            'status': 'incomplete',
            'incomplete_details': {'reason': 'max_output_tokens'},
            'output': [],
            'usage': {'input_tokens': 20, 'output_tokens': 500},
        }
        with patch('app.urllib.request.urlopen',
                   return_value=io.BytesIO(json.dumps(payload).encode('utf-8'))):
            with self.assertRaisesRegex(ValueError, 'лимит генерации'):
                app.call_yandex_ai(self.storage, 'test', purpose='reflection')
        self.assertGreater(self.storage.ai_config()['last_success'], 0)
        usage = self.storage.usage_summary()
        self.assertEqual(usage['month']['input_tokens'], 20)
        self.assertEqual(usage['month']['output_tokens'], 500)

    def test_resaving_same_credentials_keeps_verified_status(self):
        config = {
            'folder_id': 'b1gpcfme4j9b9bv37hqb',
            'model': 'qwen3.6-35b-a3b/latest',
            'api_key': 'secret-test-key-value',
            'auth_type': 'api_key',
        }
        self.storage.save_ai_config(config)
        self.storage.mark_ai_success()
        before = self.storage.ai_config()['last_success']
        self.storage.save_ai_config(config)
        self.assertEqual(self.storage.ai_config()['last_success'], before)

    def test_extract_response_text_handles_null_content(self):
        response = {
            'output_text': None,
            'output': [
                None,
                {'type': 'reasoning', 'content': None},
                {'type': 'message', 'content': [
                    None,
                    {'type': 'reasoning_text', 'text': 'служебное'},
                    {'type': 'output_text', 'text': 'Ответ модели'},
                ]},
            ],
        }
        self.assertEqual(app.extract_response_text(response), 'Ответ модели')
        self.assertEqual(app.extract_response_text({'output': None}), '')
        self.assertEqual(app.extract_response_text(None), '')

    def test_learning_ui_uses_ai_studio_and_queue_actions(self):
        ui = (app.ROOT / 'web' / 'app.js').read_text('utf-8')
        self.assertIn('Чат с Тори', ui)
        self.assertIn('Тори ↔ Qwen', ui)
        self.assertIn('Разговор обучения', ui)
        self.assertIn('AI Studio', ui)
        self.assertIn('Qwen3.6 35B', ui)
        self.assertIn('Память и знания', ui)
        self.assertIn('Использовано:', ui)
        self.assertIn('Месяц', ui)
        self.assertIn('Всего AI за месяц', ui)
        self.assertIn("usageStrip(q.usage,'learning')", ui)
        self.assertIn('Лимит в месяц', ui)
        self.assertIn('0,2 ₽ вход / 0,3 ₽ выход', ui)
        self.assertIn('Мозг Тори', ui)
        self.assertIn('Мозг: предложения', ui)
        self.assertIn('Принять', ui)
        self.assertIn('Отклонить', ui)
        self.assertIn('Цели Тори', ui)
        self.assertIn('Составить план', ui)
        self.assertIn('Самопроверка:', ui)
        self.assertIn('В обучение', ui)
        self.assertIn('Повторить', ui)
        self.assertIn('Пропустить', ui)
        self.assertIn('Отменить', ui)
        self.assertNotIn('Тори ещё не подключена', ui)
        self.assertNotIn('Открыть Браузер Тори', ui)
        self.assertNotIn('qwen/bridge', ui)

    def test_schema_v3_learning_history_migrates_to_conversation(self):
        with tempfile.TemporaryDirectory(prefix='Тори v3 ') as temporary:
            path = Path(temporary) / 'tooru.sqlite3'
            with closing(sqlite3.connect(path)) as db:
                db.executescript("""
                    PRAGMA user_version=3;
                    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE records (
                        id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
                        title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT 'Пользователь',
                        created_at TEXT NOT NULL DEFAULT '2026-09-30T00:00:00Z'
                    );
                    CREATE TABLE learning_queue (
                        id INTEGER PRIMARY KEY,
                        topic TEXT NOT NULL,
                        question TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        attempts INTEGER NOT NULL DEFAULT 0,
                        response_text TEXT NOT NULL DEFAULT '',
                        last_error TEXT NOT NULL DEFAULT '',
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                db.execute(
                    "INSERT INTO learning_queue(id,topic,question,status,response_text,input_tokens,output_tokens,created_at,updated_at) "
                    "VALUES (1,'Сети','Как работает роутер?','done','Маршрутизатор пересылает пакеты.',20,10,"
                    "'2026-09-30T00:00:00Z','2026-09-30T00:01:00Z')"
                )
                db.commit()
            migrated = app.Storage(temporary)
            messages = migrated.learning_state()['messages']
            self.assertEqual([m['role'] for m in messages], ['tori', 'qwen'])
            self.assertIn('роутер', messages[0]['text'])
            self.assertIn('пакеты', messages[1]['text'])

    def test_schema_v7_has_reasoning_tables(self):
        with self.storage.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 7)
            columns = {row[1] for row in db.execute('PRAGMA table_info(ai_messages)').fetchall()}
        self.assertTrue({'channel','role','text','queue_id','input_tokens','output_tokens','context_json'} <= columns)
        with self.storage.connect() as db:
            brain_columns = {row[1] for row in db.execute('PRAGMA table_info(brain_suggestions)').fetchall()}
        self.assertTrue({'kind','title','body','topic','question','reason','confidence','status'} <= brain_columns)
        with self.storage.connect() as db:
            queue_columns = {row[1] for row in db.execute('PRAGMA table_info(learning_queue)').fetchall()}
            goal_columns = {row[1] for row in db.execute('PRAGMA table_info(brain_goals)').fetchall()}
        self.assertIn('review_json', queue_columns)
        self.assertTrue({'title','description','status','plan_json','progress_json'} <= goal_columns)

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
            shutil.copy2(app.ROOT / 'updater.py', original / 'updater.py')
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
