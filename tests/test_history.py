"""Validate the machine-readable handoff and referenced local entry points."""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class HistoryTest(unittest.TestCase):
    def test_project_and_event_log(self):
        history = ROOT / 'ИСТОРИЯ'
        project = json.loads((history / 'PROJECT.json').read_text('utf-8'))
        self.assertEqual(project['schema_version'], 1)
        self.assertEqual(project['project']['version'], (ROOT / 'VERSION').read_text().strip())
        for path in project['handoff']['read_first']:
            self.assertTrue((ROOT / path).is_file(), path)
        self.assertTrue((ROOT / project['constraints']['launcher']).is_file())
        events = [json.loads(line) for line in (history / 'EVENTS.jsonl').read_text('utf-8').splitlines() if line.strip()]
        self.assertTrue(events)
        self.assertEqual(len(events), len({event['id'] for event in events}))
        for event in events:
            for key in ('id', 'date', 'type', 'summary'):
                self.assertTrue(event[key])


if __name__ == '__main__':
    unittest.main()
