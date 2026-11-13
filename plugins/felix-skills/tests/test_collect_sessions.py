"""Standard-library tests; no real transcripts are read."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/session-summary/scripts/collect_sessions.py'
spec = importlib.util.spec_from_file_location('collect_sessions', SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def claude(text, timestamp='2026-03-08T16:00:00Z', **fields):
    record = {'type': 'user', 'timestamp': timestamp, 'sessionId': 'c1', 'cwd': '/demo',
              'message': {'role': 'user', 'content': text}}
    record.update(fields)
    return record


def response(text, timestamp='2026-03-08T16:00:00Z', **fields):
    record = {'type': 'response_item', 'timestamp': timestamp,
              'payload': {'type': 'message', 'role': 'user',
                          'content': [{'type': 'input_text', 'text': text}]}}
    record.update(fields)
    return record


def event(text, timestamp='2026-03-08T16:00:01Z'):
    return {'type': 'event_msg', 'timestamp': timestamp,
            'payload': {'type': 'user_message', 'message': text}}


def meta(**fields):
    payload = {'id': 'x1', 'cwd': '/demo', 'source': 'cli', 'thread_source': 'user',
               'timestamp': '2026-03-08T15:00:00Z'}
    payload.update(fields)
    return {'type': 'session_meta', 'payload': payload}


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cr, self.xr = self.root / 'claude', self.root / 'codex'
        for path in (self.cr / 'projects', self.xr / 'sessions', self.xr / 'archived_sessions'):
            path.mkdir(parents=True)

    def write(self, relative, records, platform='claude'):
        path = (self.cr if platform == 'claude' else self.xr) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in records) + '\n', encoding='utf-8')
        return path

    def collect(self, **kwargs):
        options = dict(target_date='2026-03-08', timezone_name='America/New_York',
                       until='2026-03-09T04:00:00Z', claude_root=self.cr, codex_root=self.xr)
        options.update(kwargs)
        return collector.collect(**options)

    def test_dst_boundaries_until_and_old_directories(self):
        self.write('projects/1999/old.jsonl', [
            claude('before', '2026-03-08T04:59:59Z'),
            claude('start', '2026-03-08T05:00:00Z'),
            claude('cutoff', '2026-03-08T18:00:00Z'),
            claude('after cutoff', '2026-03-08T18:00:00.001Z'),
            claude('last', '2026-03-09T03:59:59Z'),
            claude('next day', '2026-03-09T04:00:00Z')])
        result = self.collect(until='2026-03-08T18:00:00Z')
        self.assertEqual([m['text'] for m in result['messages']], ['start'])
        self.assertEqual(result['effective_cutoff_exclusive'], '2026-03-08T18:00:00+00:00')
        self.assertEqual(result['window'], {'start': '2026-03-08T05:00:00+00:00',
                                          'end_exclusive': '2026-03-09T04:00:00+00:00'})
        self.assertEqual(self.collect()['platforms']['claude']['files_scanned'], 1)
        self.assertEqual(len(self.collect()['messages']), 4)
        fall = self.collect(target_date='2026-11-01')
        a = collector.parse_timestamp(fall['window']['start'])
        b = collector.parse_timestamp(fall['window']['end_exclusive'])
        self.assertEqual((b-a).total_seconds(), 25*3600)

    @unittest.skipUnless(hasattr(time, 'tzset'), 'requires libc tzset')
    def test_default_beijing_ignores_system_timezone(self):
        old = os.environ.get('TZ')
        try:
            os.environ['TZ'] = 'America/New_York'
            time.tzset()
            result = self.collect(timezone_name=None)
            self.assertEqual(result['timezone'], 'Asia/Shanghai')
            self.assertEqual(result['window']['start'], '2026-03-07T16:00:00+00:00')
            self.assertEqual(result['window']['end_exclusive'], '2026-03-08T16:00:00+00:00')
            today = self.collect(target_date=None, timezone_name=None, until=None,
                                 now=datetime(2026, 3, 8, 16, 30, tzinfo=timezone.utc))
            self.assertEqual(today['date'], '2026-03-09')
            with patch.object(collector, 'ZoneInfo', side_effect=collector.ZoneInfoNotFoundError):
                self.assertEqual(self.collect(timezone_name=None)['window'], result['window'])
        finally:
            if old is None:
                os.environ.pop('TZ', None)
            else:
                os.environ['TZ'] = old
            time.tzset()

    def test_default_cutoff_and_local_today(self):
        now = datetime(2026, 3, 9, 2, 0, tzinfo=timezone.utc)
        self.write('projects/a.jsonl', [claude('yes', '2026-03-09T01:59:59Z'),
                                      claude('boundary', '2026-03-09T02:00:00Z'),
                                      claude('no', '2026-03-09T02:00:01Z')])
        result = self.collect(target_date=None, until=None, now=now)
        self.assertEqual(result['date'], '2026-03-08')
        self.assertEqual(result['until'], now.isoformat())
        self.assertEqual([m['text'] for m in result['messages']], ['yes'])

    def test_default_today_and_positional_compatibility(self):
        now = datetime(2026, 3, 9, 2, tzinfo=timezone.utc)
        default = collector.collect(None, 'America/New_York', None, self.cr, self.xr, now)
        explicit = self.collect(target_date=None, until=None, now=now, period='today')
        self.assertEqual(default, explicit)
        self.assertEqual(default['schema_version'], 2)
        self.assertEqual(default['range'], dict(default['window'], label='today'))
        legacy = collector.collect('2026-03-08', 'America/New_York',
                                   '2026-03-09T04:00:00Z', self.cr, self.xr, now)
        self.assertEqual(legacy['window'], default['window'])
        self.assertEqual(legacy['date'], '2026-03-08')

    def test_yesterday_across_month_and_year(self):
        for now, expected in [('2026-03-01T12:00:00Z', '2026-02-28'),
                              ('2026-01-01T12:00:00Z', '2025-12-31')]:
            with self.subTest(now=now):
                result = self.collect(target_date=None, period='yesterday', timezone_name='UTC',
                                      now=collector.parse_timestamp(now), until=None)
                self.assertEqual(result['date'], expected)
                self.assertEqual(result['range']['label'], 'yesterday')
                self.assertEqual(result['window']['start'], expected + 'T00:00:00+00:00')

    def test_this_week_across_year_and_monday(self):
        for now, first, last in [('2026-01-01T12:00:00Z', '2025-12-29', '2026-01-05'),
                                 ('2026-01-05T12:00:00Z', '2026-01-05', '2026-01-12')]:
            with self.subTest(now=now):
                result = self.collect(target_date=None, period='this-week', timezone_name='UTC',
                                      now=collector.parse_timestamp(now), until=None)
                self.assertEqual(result['window'], {'start': first + 'T00:00:00+00:00',
                                                   'end_exclusive': last + 'T00:00:00+00:00'})
                self.assertEqual(result['effective_cutoff_exclusive'], now.replace('Z', '+00:00'))
                self.assertNotIn('date', result)

    def test_last_week_is_previous_calendar_week(self):
        result = self.collect(target_date=None, period='last-week', timezone_name='UTC',
                              now=collector.parse_timestamp('2026-01-01T12:00:00Z'), until=None)
        self.assertEqual(result['window'], {'start': '2025-12-22T00:00:00+00:00',
                                           'end_exclusive': '2025-12-29T00:00:00+00:00'})
        self.assertEqual(result['effective_cutoff_exclusive'], result['window']['end_exclusive'])
        self.assertEqual(result['range']['label'], 'last-week')
        self.assertNotIn('date', result)
        monday = self.collect(target_date=None, period='last-week', timezone_name='UTC',
                              now=collector.parse_timestamp('2026-01-05T00:00:00Z'), until=None)
        self.assertEqual(monday['window'], {'start': '2025-12-29T00:00:00+00:00',
                                           'end_exclusive': '2026-01-05T00:00:00+00:00'})

    def test_dst_weeks_and_date_ranges(self):
        for now, first, last, hours in [
                ('2026-03-08T18:00:00Z', '2026-03-02', '2026-03-08', 167),
                ('2026-11-01T18:00:00Z', '2026-10-26', '2026-11-01', 169)]:
            with self.subTest(now=now):
                week = self.collect(target_date=None, period='this-week',
                                    now=collector.parse_timestamp(now), until=None)
                custom = self.collect(target_date=None, start=first, end=last,
                                      now=collector.parse_timestamp(now), until=None)
                self.assertEqual(week['window'], custom['window'])
                a = collector.parse_timestamp(week['window']['start'])
                b = collector.parse_timestamp(week['window']['end_exclusive'])
                self.assertEqual((b-a).total_seconds(), hours*3600)
                self.assertNotIn('date', custom)
        same_day = self.collect(target_date=None, start='2026-03-08', end='2026-03-08')
        self.assertEqual(same_day['window'], self.collect()['window'])

    def test_date_range_filters_message_times_across_old_directories(self):
        self.write('projects/1999/a.jsonl', [
            claude('before', '2026-03-06T04:59:59Z'),
            claude('start', '2026-03-06T05:00:00Z'),
            claude('middle', '2026-03-07T12:00:00Z'),
            claude('last', '2026-03-09T03:59:59.999999Z'),
            claude('end', '2026-03-09T04:00:00Z')])
        result = self.collect(target_date=None, start='2026-03-06', end='2026-03-08')
        self.assertEqual([m['text'] for m in result['messages']], ['start', 'middle', 'last'])
        self.assertEqual(result['filters']['outside_window'], 2)
        self.assertEqual(result['range']['label'], 'custom')

    def test_iso_exact_end_and_mixed_boundaries(self):
        self.write('projects/a.jsonl', [claude('before', '2026-03-08T15:59:59.999999Z'),
                                      claude('start', '2026-03-08T16:00:00Z'),
                                      claude('last', '2026-03-08T16:00:00.999999Z'),
                                      claude('end', '2026-03-08T16:00:01Z')])
        result = self.collect(target_date=None, start='2026-03-08T12:00:00-04:00',
                              end='2026-03-08T12:00:01')
        self.assertEqual([m['text'] for m in result['messages']], ['start', 'last'])
        self.assertEqual(result['window']['end_exclusive'], '2026-03-08T16:00:01+00:00')
        for start, end, expected_start, expected_end in [
                ('2026-03-08', '2026-03-08T16:00:01Z', '2026-03-08T05:00:00+00:00',
                 '2026-03-08T16:00:01+00:00'),
                ('2026-03-08T12:00:00', '2026-03-08', '2026-03-08T16:00:00+00:00',
                 '2026-03-09T04:00:00+00:00')]:
            with self.subTest(start=start, end=end):
                result = self.collect(target_date=None, start=start, end=end)
                self.assertEqual(result['window'], {'start': expected_start, 'end_exclusive': expected_end})

    def test_invalid_ranges_and_collect_selection_conflicts(self):
        invalid = [dict(period='unknown'), dict(start='2026-03-08'), dict(end='2026-03-08'),
                   dict(start='2026-03-09', end='2026-03-08'),
                   dict(start='2026-03-08T16:00:00Z', end='2026-03-08T16:00:00Z'),
                   dict(start='2026-03-08T16:00:01Z', end='2026-03-08T16:00:00Z'),
                   dict(start='bad', end='2026-03-08'),
                   dict(start='2026-02-30', end='2026-03-08'),
                   dict(period='today', start='2026-03-08', end='2026-03-08')]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.collect(target_date=None, **options)
        for options in [dict(period='today'), dict(start='2026-03-08', end='2026-03-08')]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.collect(**options)

    def test_future_ranges_and_independent_cutoff(self):
        now = collector.parse_timestamp('2026-03-08T18:00:00Z')
        self.write('projects/a.jsonl', [claude('yes', '2026-03-08T17:59:59Z'),
                                      claude('cutoff', '2026-03-08T18:00:00Z'),
                                      claude('later', '2026-03-09T16:00:00Z')])
        result = self.collect(target_date=None, start='2026-03-08', end='2026-03-10',
                              now=now, until=None)
        self.assertEqual([m['text'] for m in result['messages']], ['yes'])
        self.assertTrue(any('尚未发生' in n for n in result['notices']))
        self.assertEqual(result['effective_cutoff_exclusive'], now.isoformat())
        future = self.collect(target_date=None, start='2026-03-09', end='2026-03-10',
                              now=now, until=None)
        self.assertEqual(future['messages'], [])
        self.assertTrue(any('尚未发生' in n for n in future['notices']))
        explicit_future_cutoff = self.collect(
            target_date=None, start='2026-03-09', end='2026-03-10', now=now,
            until='2026-03-10T00:00:00Z')
        self.assertEqual([m['text'] for m in explicit_future_cutoff['messages']], ['later'])
        self.assertEqual(explicit_future_cutoff['until'], '2026-03-10T00:00:00+00:00')
        self.assertEqual(explicit_future_cutoff['effective_cutoff_exclusive'],
                         '2026-03-10T00:00:00+00:00')
        self.assertTrue(any('尚未发生' in n for n in explicit_future_cutoff['notices']))
        historical = self.collect(target_date=None, start='2026-03-07', end='2026-03-08',
                                  now=now, until='2026-03-07T00:00:00Z')
        self.assertEqual(historical['messages'], [])
        self.assertTrue(any('起点不早于' in n for n in historical['notices']))
        week = self.collect(target_date=None, period='this-week', now=now,
                            until='2026-03-08T17:00:00Z')
        self.assertEqual(week['messages'], [])
        self.assertEqual(week['effective_cutoff_exclusive'], '2026-03-08T17:00:00+00:00')

    def test_cli_range_selection_and_errors(self):
        base = [sys.executable, '-I', str(SCRIPT), '--claude-root', str(self.cr),
                '--codex-root', str(self.xr)]
        valid = [[], ['--period', 'today'], ['--period', 'yesterday'],
                 ['--period', 'this-week'], ['--period', 'last-week'],
                 ['--start', '2026-03-01', '--end', '2026-03-08'],
                 ['--start', '2026-03-01T01:00:00Z', '--end', '2026-03-08']]
        for options in valid:
            with self.subTest(options=options):
                proc = subprocess.run(base + options, capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)['schema_version'], 2)
        invalid = [
            ['--date', '2026-03-08', '--period', 'today'],
            ['--date', '2026-03-08', '--start', '2026-03-01', '--end', '2026-03-08'],
            ['--period', 'today', '--start', '2026-03-01', '--end', '2026-03-08'],
            ['--date', '2026-03-08', '--end', '2026-03-08'],
            ['--period', 'today', '--end', '2026-03-08'],
            ['--start', '2026-03-01'], ['--end', '2026-03-08'],
            ['--start'], ['--end'], ['--period'], ['--period', 'unknown'],
            ['--date', '2026-3-8'], ['--date', '2026-02-30'],
            ['--start', '2026-03-09', '--end', '2026-03-08'],
            ['--start', 'bad', '--end', '2026-03-08'],
            ['--start', '2026-03-08T00:00:00Z', '--end', '2026-03-08T00:00:00Z']]
        for options in invalid:
            with self.subTest(options=options):
                proc = subprocess.run(base + options, capture_output=True)
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, b'')

    def test_claude_command_and_filters_without_origin(self):
        self.write('projects/a.jsonl', [
            claude('中文真实输入'), claude('origin human', origin={'kind': 'human'}),
            claude('<command-message>skill</command-message><command-name>/review</command-name>'
                   '<command-args>中文参数\n第二行</command-args>\nBase directory for this skill: huge body'),
            claude([{'type': 'tool_result', 'content': 'tool output'}]),
            claude('injected', isMeta=True), claude('skill body', sourceToolAssistantUUID='u'),
            claude('background', isSidechain=True),
            claude('<task-notification>done</task-notification>'),
            claude('[Request interrupted by user]'),
            claude('This session is being continued from a previous conversation.'),
            claude('<environment_context>cwd</environment_context>'),
            claude('Base directory for this skill: body'),
        ])
        self.write('projects/p/subagents/agent-a.jsonl', [claude('subagent')])
        result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']],
                         ['中文真实输入', 'origin human', '/review 中文参数\n第二行'])
        self.assertTrue(result['complete'])
        self.assertEqual(result['messages'][0]['sources'][0]['line'], 1)
        self.assertEqual(result['messages'][0]['cwd'], '/demo')

    def test_claude_stable_uuid_across_files_not_text_deduplication(self):
        original = claude('同一文字', uuid='u1')
        self.write('projects/a.jsonl', [original])
        second = self.write('projects/b.jsonl', [original, claude('同一文字', uuid='u2')])
        result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']], ['同一文字', '同一文字'])
        self.assertEqual(len(result['messages'][0]['sources']), 2)
        self.assertIn(str(second), result['sessions'][0]['files'])
        self.assertEqual(result['filters']['claude_duplicate_uuid'], 1)
        self.assertEqual(result['platforms']['claude']['messages'], 2)
        self.assertEqual(result['sessions'][0]['message_count'], 2)

    def test_codex_known_application_and_side_boundary_injections(self):
        self.write('sessions/a.jsonl', [meta(forked_from_id='parent'),
                   response('<external_codex_apps_open_page>{"url":"example"}</external_codex_apps_open_page>'),
                   response('Side conversation boundary.\n\nEverything before this boundary is inherited history from the parent.'),
                   event('实际侧会话问题')], 'codex')
        result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']], ['实际侧会话问题'])
        self.assertFalse(result['uncertainties'])
        self.assertEqual(result['filters']['automatic_content'], 2)

    def test_codex_pairs_and_real_repeated_inputs(self):
        self.write('sessions/2000/01/01/a.jsonl', [meta(), response('重复'), event('重复'),
                   response('重复', '2026-03-08T16:00:02Z'),
                   event('重复', '2026-03-08T16:00:03Z'),
                   event('event only', '2026-03-08T16:01:00Z'),
                   response('response only', '2026-03-08T16:02:00Z')], 'codex')
        (self.xr / 'history.jsonl').write_text('not a source')
        result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']],
                         ['重复', '重复', 'event only', 'response only'])
        self.assertEqual([len(m['sources']) for m in result['messages']], [2, 2, 1, 1])
        self.assertEqual(result['filters']['codex_duplicate_representation'], 2)
        self.assertTrue(result['directories']['codex_history']['exists'])
        self.assertFalse(result['directories']['codex_history']['scanned'])

    def test_same_type_repeats_and_distinct_turns_not_deduplicated(self):
        records = [meta(), response('same'), response('same'), event('same'), event('same')]
        for turn in ('a', 'b'):
            records.extend([{'type': 'turn_context', 'payload': {'turn_id': turn}},
                            response('different turns') if turn == 'a' else event('different turns')])
        self.write('sessions/a.jsonl', records, 'codex')
        result = self.collect()
        self.assertEqual(len(result['messages']), 4)

    def test_codex_environment_skill_guardian_and_mixed_content(self):
        mixed = response('placeholder')
        mixed['payload']['content'] = [
            {'type': 'input_text', 'text': '<environment_context>cwd</environment_context>'},
            {'type': 'input_text', 'text': '# AGENTS.md instructions for /demo\nbody'},
            {'type': 'input_text', 'text': '真正的问题'}]
        self.write('sessions/a.jsonl', [meta(), mixed, response('<skill>body</skill>'),
                    response('You are a Guardian tasked with checking'),
                    response('<system-reminder>injected</system-reminder>')], 'codex')
        result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']], ['真正的问题'])

    def test_subagents_forks_independent_side_conversation_and_inheritance(self):
        self.write('sessions/sub.jsonl', [meta(source={'subagent': {'thread_spawn': {}}}),
                                         response('not human'), event('not human')], 'codex')
        self.write('sessions/fork.jsonl', [
            meta(id='side', forked_from_id='parent', parent_thread_id='parent',
                 subagent_history_start_ordinal=10),
            response('inherited ordinal', ordinal=1),
            response('inherited old', '2026-03-08T14:00:00Z', ordinal=12),
            response('ambiguous', ordinal=12),
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'new'}},
            response('new side conversation', ordinal=14), event('new side conversation'),
            response('explicit inherited', is_inherited=True)], 'codex')
        result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']], ['new side conversation'])
        self.assertEqual([m['text'] for m in result['uncertainties']], ['ambiguous'])
        self.assertEqual(result['messages'][0]['session_id'], 'side')
        self.assertEqual(result['filters']['codex_subagent'], 2)

    def test_archived_zstd_missing_and_mock_stream(self):
        self.write('archived_sessions/a.jsonl', [meta(), event('archived')], 'codex')
        path = self.xr / 'archived_sessions/b.jsonl.zst'
        path.write_bytes(b'compressed')
        with patch.object(collector.shutil, 'which', return_value=None):
            result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']], ['archived'])
        self.assertIn('zstd_missing', [e['kind'] for e in result['errors']])
        self.assertFalse(result['complete'])
        fake = unittest.mock.Mock()
        fake.stdout = io.BytesIO((json.dumps(meta()) + '\n' + json.dumps(event('zstd')) + '\n').encode())
        fake.stderr = io.BytesIO(b'')
        fake.wait.return_value = 0
        with patch.object(collector.shutil, 'which', return_value='/usr/bin/zstd'), \
                patch.object(collector.subprocess, 'Popen', return_value=fake) as popen:
            result = self.collect()
        self.assertEqual([m['text'] for m in result['messages']], ['archived', 'zstd'])
        self.assertEqual(popen.call_args.args[0], ['/usr/bin/zstd', '-dc', '--', str(path)])

    def test_bad_lines_missing_roots_and_file_failure(self):
        path = self.write('projects/a.jsonl', [claude('good'), claude('bad time', 'not-time')])
        with path.open('ab') as f:
            f.write(b'{broken\n\xff\n[]\n')
        result = self.collect(codex_root=self.root / 'missing')
        kinds = [e['kind'] for e in result['errors']]
        self.assertEqual(kinds.count('malformed_line'), 3)
        self.assertEqual(kinds.count('missing_directory'), 2)
        self.assertEqual([m['text'] for m in result['messages']], ['good'])
        self.assertEqual(result['uncertainties'][0]['text'], 'bad time')
        self.assertFalse(result['complete'])
        with patch.object(collector, 'read_lines', side_effect=PermissionError('denied')):
            self.assertIn('file_read_failed', [e['kind'] for e in self.collect()['errors']])

    def test_unknown_user_schema_is_uncertain(self):
        self.write('projects/a.jsonl', [claude([{'type': 'image', 'source': {}}]),
                                      claude('unrecognized origin', origin={'kind': 'future'})])
        result = self.collect()
        self.assertEqual(len(result['uncertainties']), 2)
        self.assertTrue(result['needs_review'])

    def test_future_date_notice_and_zstd_failure(self):
        result = self.collect(target_date='2099-01-01', until=None)
        self.assertIn('尚未发生', result['notices'][0])
        path = self.xr / 'sessions/fail.jsonl.zst'
        path.write_bytes(b'broken')
        fake = unittest.mock.Mock()
        fake.stdout = io.BytesIO(b'')
        fake.stderr = io.BytesIO(b'invalid compressed data')
        fake.wait.return_value = 1
        with patch.object(collector.shutil, 'which', return_value='/opt/homebrew/bin/zstd'), \
                patch.object(collector.subprocess, 'Popen', return_value=fake):
            result = self.collect()
        self.assertEqual(result['errors'][0]['kind'], 'zstd_failed')
        self.assertFalse(result['complete'])
        self.assertEqual(result['platforms']['codex']['files_completed'], 0)

    def test_literal_command_tag_in_prose_is_not_rewritten(self):
        text = '请解释 <command-name>/foo</command-name> 这个标签'
        self.write('projects/a.jsonl', [claude(text)])
        self.assertEqual(self.collect()['messages'][0]['text'], text)

    def test_cli_isolated_utf8_and_validation(self):
        self.write('projects/a.jsonl', [claude('中文', '2026-03-08T15:59:59Z')])
        args = [sys.executable, '-I', str(SCRIPT), '--claude-root', str(self.cr),
                '--codex-root', str(self.xr), '--date', '2026-03-08',
                '--until', '2026-03-09T00:00:00Z']
        result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('中文', result.stdout)
        self.assertEqual(json.loads(result.stdout)['messages'][0]['text'], '中文')
        self.assertEqual(json.loads(result.stdout)['timezone'], 'Asia/Shanghai')
        unsupported = subprocess.run(args + ['--timezone', 'UTC'], capture_output=True)
        self.assertEqual(unsupported.returncode, 2)
        invalid = subprocess.run(args + ['--date', 'bad'], capture_output=True)
        self.assertEqual(invalid.returncode, 2)


if __name__ == '__main__':
    unittest.main()
