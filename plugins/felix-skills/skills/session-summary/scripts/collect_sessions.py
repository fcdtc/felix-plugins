#!/usr/bin/env python3
"""Read-only user-input collector, Python 3.9+, runnable with python3 -I.

stdout is one UTF-8 JSON object. Ranges are [start, end), with date-only ends
including the whole local day and an exclusive --until cutoff (scan start by default).
Calendar ranges and naive ISO8601 values use Beijing time (UTC+08:00).
The CLI does not depend on the system timezone.
No transcript content is executed. zstd is optional and invoked without a shell.
"""
import argparse
from collections import Counter
from datetime import date, datetime, time as datetime_time, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
AUTO_PREFIXES = (
    '<environment_context', '<environment_details', '<permissions instructions',
    '<system-reminder', '<system_instructions', '<developer_instructions',
    '<skill>', '<skills>', '<available_skills', '<task-notification',
    '<local-command-stdout', '<local-command-stderr', '<local-command-caveat',
    '[Request interrupted by user', 'This session is being continued from a previous',
    'The conversation so far has been summarized', 'You are a Guardian',
    'You are an agent named Guardian', '# AGENTS.md instructions for ',
    '<subagent_notification', '<inherited_context', '<guardian',
    '<turn_aborted', '<user_instructions>', '<INSTRUCTIONS>',
    '<external_codex_apps_open_page>',
    'Side conversation boundary.\n\nEverything before this boundary is inherited history',
)
COMMAND = re.compile(r'<command-name>(.*?)</command-name>', re.S)
ARGS = re.compile(r'<command-args>(.*?)</command-args>', re.S)


def parse_timestamp(value):
    if not isinstance(value, str):
        raise ValueError('timestamp must be an ISO8601 string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('log timestamp has no UTC offset')
    return parsed.astimezone(UTC)


class Clock:
    """Beijing by default; explicit zones are retained for internal regression tests."""
    def __init__(self, name=None):
        self.name = name or 'Asia/Shanghai'
        self.zone = ZoneInfo(name) if name else timezone(timedelta(hours=8), '北京时间')

    def local(self, instant):
        return instant.astimezone(self.zone)

    def wall(self, wall):
        return wall.replace(tzinfo=self.zone).astimezone(UTC)


def extract_text(content):
    if isinstance(content, str):
        return content, False, []
    if not isinstance(content, list):
        return '', True, []
    texts, unknown, types = [], False, []
    for block in content:
        if not isinstance(block, dict):
            unknown = True
            continue
        kind = block.get('type')
        types.append(kind)
        if kind in ('text', 'input_text') and isinstance(block.get('text'), str):
            texts.append(block['text'])
        elif kind not in ('tool_result',):
            unknown = True
    return '\n'.join(texts), unknown, types


def clean_text(text):
    """Strip only known standalone injected blocks, never keyword-match prose."""
    command = COMMAND.search(text)
    if command and text.lstrip().startswith(('<command-message>', '<command-name>')):
        args = ARGS.search(text)
        return (command.group(1).strip() +
                (' ' + args.group(1).strip() if args and args.group(1).strip() else '')), None
    if text.lstrip().startswith(AUTO_PREFIXES):
        return '', 'automatic_content'
    if text.lstrip().startswith('Base directory for this skill:'):
        return '', 'skill_body'
    return text, None


def subagent_metadata(meta):
    source = meta.get('source')
    return ((isinstance(source, dict) and 'subagent' in source)
            or meta.get('thread_source') in ('subagent', 'guardian')
            or (isinstance(source, str) and source.lower() in ('subagent', 'guardian')))


def read_lines(path, report):
    """Stream decompression; only stderr is buffered, never decompressed to disk."""
    if path.name.endswith('.zst'):
        executable = shutil.which('zstd')
        if not executable:
            report['errors'].append({'kind': 'zstd_missing', 'file': str(path)})
            return
        # PIPE stderr could deadlock; a bounded-memory spool would write to disk.
        # A separate thread drains stderr while stdout is processed incrementally.
        import threading
        process = subprocess.Popen([executable, '-dc', '--', str(path)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stderr_chunks = []
        def drain():
            while True:
                chunk = process.stderr.read(4096)
                if not chunk:
                    break
                if sum(map(len, stderr_chunks)) < 8192:
                    stderr_chunks.append(chunk)
        worker = threading.Thread(target=drain, daemon=True)
        worker.start()
        try:
            for line in process.stdout:
                yield line
        finally:
            process.stdout.close()
            worker.join()
            status = process.wait()
            process.stderr.close()
            if status:
                report['errors'].append({'kind': 'zstd_failed', 'file': str(path),
                                         'returncode': status,
                                         'detail': b''.join(stderr_chunks).decode('utf-8', 'replace')})
    else:
        with path.open('rb') as handle:
            yield from handle


def discover(directory, platform, report):
    counts = report['platforms'][platform]
    if not directory.is_dir():
        report['errors'].append({'kind': 'missing_directory', 'platform': platform,
                                 'directory': str(directory)})
        return []
    paths = []
    def onerror(error):
        report['errors'].append({'kind': 'directory_read_failed', 'platform': platform,
                                 'file': error.filename, 'detail': str(error)})
    for parent, dirs, files in os.walk(directory, onerror=onerror, followlinks=False):
        dirs.sort()
        for filename in sorted(files):
            if filename.endswith(('.jsonl', '.jsonl.zst')):
                paths.append(Path(parent) / filename)
    counts['files_discovered'] += len(paths)
    return paths


def human_evidence(record):
    origin = record.get('origin')
    return origin == 'human' or (isinstance(origin, dict) and origin.get('kind') == 'human') \
        or record.get('turnOrigin') == 'human' or record.get('promptSource') == 'typed'


def parse_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('date must be YYYY-MM-DD')
    return date.fromisoformat(value)


def parse_boundary(value, clock, inclusive_date_end=False):
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        day = parse_date(value) + timedelta(days=int(inclusive_date_end))
        return clock.wall(datetime.combine(day, datetime_time()))
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return parsed.astimezone(UTC) if parsed.tzinfo else clock.wall(parsed)


def resolve_range(target_date, period, start, end, clock, started):
    if sum((target_date is not None, period is not None,
            start is not None or end is not None)) > 1:
        raise ValueError('--date, --period and --start/--end are mutually exclusive')
    if (start is None) != (end is None):
        raise ValueError('--start and --end must be supplied together')
    day = None
    if start is not None:
        label = 'custom'
        window_start = parse_boundary(start, clock)
        window_end = parse_boundary(end, clock, inclusive_date_end=True)
    else:
        label = target_date if target_date is not None else (period or 'today')
        if target_date is not None:
            first_day = parse_date(target_date)
            days = 1
        else:
            if label not in ('today', 'yesterday', 'this-week', 'last-week'):
                raise ValueError('period must be today, yesterday, this-week or last-week')
            today = clock.local(started).date()
            first_day = today
            days = 1
            if label == 'yesterday':
                first_day -= timedelta(days=1)
            elif label in ('this-week', 'last-week'):
                first_day -= timedelta(days=today.weekday() + (7 if label == 'last-week' else 0))
                days = 7
        if days == 1:
            day = first_day.isoformat()
        window_start = clock.wall(datetime.combine(first_day, datetime_time()))
        window_end = clock.wall(datetime.combine(first_day + timedelta(days=days), datetime_time()))
    if window_start >= window_end:
        raise ValueError('range start must be before end_exclusive')
    return label, window_start, window_end, day


def collect(target_date=None, timezone_name=None, until=None, claude_root=None,
            codex_root=None, now=None, *, period=None, start=None, end=None):
    started = now or datetime.now(UTC)
    clock = Clock(timezone_name)
    label, window_start, window_end, day = resolve_range(
        target_date, period, start, end, clock, started)
    cutoff = parse_boundary(until, clock) if until is not None else started
    cr = Path(claude_root or os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude').expanduser().absolute()
    xr = Path(codex_root or os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser().absolute()
    report = {
        'schema_version': 2, 'timezone': clock.name,
        'scan_started_at': started.isoformat(), 'until': cutoff.isoformat(),
        'range': {'label': label, 'start': window_start.isoformat(),
                  'end_exclusive': window_end.isoformat()},
        'window': {'start': window_start.isoformat(), 'end_exclusive': window_end.isoformat()},
        'effective_cutoff_exclusive': min(window_end, cutoff).isoformat(),
        'directories': {'claude_root': str(cr), 'claude_projects': str(cr / 'projects'),
                        'codex_root': str(xr), 'codex_sessions': str(xr / 'sessions'),
                        'codex_archived_sessions': str(xr / 'archived_sessions'),
                        'codex_history': {'path': str(xr / 'history.jsonl'),
                                          'exists': (xr / 'history.jsonl').exists(), 'scanned': False}},
        'platforms': {p: {'files_discovered': 0, 'files_scanned': 0,
                          'files_completed': 0, 'lines_read': 0, 'messages': 0,
                          'uncertainties': 0} for p in ('claude', 'codex')},
        'filters': {}, 'errors': [], 'messages': [], 'uncertainties': [], 'sessions': [],
        'notices': [],
    }
    if day is not None:
        report['date'] = day
    if window_start >= started:
        report['notices'].append('目标范围尚未发生；仅保留固定截止时间之前的输入。')
    elif window_end > started:
        report['notices'].append('目标范围包含尚未发生的时间；仅保留固定截止时间之前的输入。')
    if window_start >= cutoff:
        report['notices'].append('目标范围起点不早于固定截止时间；该范围没有可抽取输入。')
    filters = Counter()
    sessions = {}
    claude_seen = {}
    for platform, directories in [('claude', [cr / 'projects']),
                                  ('codex', [xr / 'sessions', xr / 'archived_sessions'])]:
        for directory in directories:
            for path in discover(directory, platform, report):
                counts = report['platforms'][platform]
                counts['files_scanned'] += 1
                before_errors = len(report['errors'])
                meta, candidates = {}, []
                turn = None
                active_turn = False
                try:
                    for number, raw in enumerate(read_lines(path, report), 1):
                        counts['lines_read'] += 1
                        source = {'file': str(path), 'line': number}
                        try:
                            record = json.loads(raw)
                            if not isinstance(record, dict):
                                raise ValueError('record is not an object')
                        except (ValueError, UnicodeError) as error:
                            report['errors'].append(dict(source, kind='malformed_line', detail=str(error)))
                            continue
                        payload = record.get('payload')
                        payload = payload if isinstance(payload, dict) else {}
                        kind = record.get('type')
                        if platform == 'codex' and kind == 'session_meta':
                            meta.update(payload)
                            continue
                        if platform == 'codex' and kind == 'turn_context':
                            meta['cwd'] = payload.get('cwd', meta.get('cwd'))
                            turn = payload.get('turn_id', turn)
                            continue
                        if platform == 'codex' and kind == 'event_msg' and payload.get('type') == 'task_started':
                            turn = payload.get('turn_id', 'line-' + str(number))
                            active_turn = True
                            continue
                        if platform == 'claude':
                            if kind != 'user':
                                continue
                            message = record.get('message')
                            if not isinstance(message, dict):
                                message = {}
                            if record.get('isSidechain') or 'subagents' in path.parts or record.get('agentId'):
                                filters['claude_subagent'] += 1
                                continue
                            if record.get('isMeta') or record.get('sourceToolAssistantUUID'):
                                filters['claude_meta_or_tool'] += 1
                                continue
                            text, unknown, types = extract_text(message.get('content'))
                            if 'tool_result' in types:
                                filters['claude_tool_result'] += 1
                                if not text:
                                    continue
                            event_type = 'user'
                            sid = record.get('sessionId') or record.get('session_id') or path.stem
                            cwd = record.get('cwd')
                            evidence = human_evidence(record)
                        else:
                            is_response = kind == 'response_item' and payload.get('role') == 'user'
                            is_event = kind == 'event_msg' and payload.get('type') == 'user_message'
                            if not (is_response or is_event):
                                continue
                            event_type = 'response_item' if is_response else 'user_message'
                            text, unknown, types = extract_text(payload.get('content') if is_response else payload.get('message'))
                            sid = meta.get('id') or meta.get('session_id') or path.stem
                            cwd = meta.get('cwd')
                            evidence = is_event
                        # Individual injected text blocks may accompany actual Codex input.
                        if isinstance((message if platform == 'claude' else payload).get('content'), list):
                            kept = []
                            for block in (message if platform == 'claude' else payload)['content']:
                                if isinstance(block, dict) and isinstance(block.get('text'), str):
                                    cleaned, reason = clean_text(block['text'])
                                    if reason:
                                        filters[reason] += 1
                                    elif cleaned:
                                        kept.append(cleaned)
                            text = '\n'.join(kept)
                            reason = None if text else 'empty_or_injected_content'
                        else:
                            text, reason = clean_text(text)
                        if (reason and not (unknown and reason == 'empty_or_injected_content')) or (not text and not unknown):
                            filters[reason or 'empty_content'] += 1
                            continue
                        reasons = ['unsupported_content_blocks'] if unknown else []
                        if not text:
                            reasons.append('non_text_user_input')
                        try:
                            instant = parse_timestamp(record.get('timestamp'))
                        except ValueError as error:
                            instant = None
                            reasons.append('missing_or_invalid_timestamp')
                            report['errors'].append(dict(source, kind='invalid_timestamp', detail=str(error)))
                        if instant and not (window_start <= instant < window_end and instant < cutoff):
                            filters['outside_window'] += 1
                            continue
                        if record.get('is_inherited') or record.get('inherited') or payload.get('is_inherited'):
                            filters['inherited'] += 1
                            continue
                        origin = record.get('origin')
                        if not evidence and origin and origin not in ('human', {'kind': 'human'}):
                            reasons.append('nonhuman_or_unknown_origin')
                        candidates.append({'platform': platform, 'timestamp': instant.isoformat() if instant else None,
                                           'local_timestamp': clock.local(instant).isoformat() if instant else None,
                                           'session_id': sid, 'cwd': cwd, 'text': text,
                                           'sources': [dict(source, event_type=event_type)],
                                           'reasons': reasons, '_instant': instant, '_turn': turn,
                                           '_active_turn': active_turn,
                                           '_ordinal': record.get('ordinal'), '_evidence': evidence,
                                           '_uuid': record.get('uuid')})
                except (OSError, EOFError) as error:
                    report['errors'].append({'kind': 'file_read_failed', 'file': str(path), 'detail': str(error)})
                if len(report['errors']) == before_errors:
                    counts['files_completed'] += 1
                if platform == 'codex':
                    if subagent_metadata(meta):
                        filters['codex_subagent'] += len(candidates)
                        candidates = []
                    else:
                        candidates = classify_fork(candidates, meta, filters)
                        candidates = pair_codex(candidates, filters)
                for candidate in candidates:
                    sid = candidate['session_id']
                    key = (platform, sid)
                    uuid = candidate.get('_uuid')
                    stable_key = (sid, uuid) if platform == 'claude' and isinstance(uuid, str) and uuid else None
                    if stable_key and stable_key in claude_seen:
                        previous = claude_seen[stable_key]
                        if previous['text'] == candidate['text'] and previous['timestamp'] == candidate['timestamp']:
                            previous['sources'].extend(candidate['sources'])
                            if str(path) not in sessions[key]['files']:
                                sessions[key]['files'].append(str(path))
                            filters['claude_duplicate_uuid'] += 1
                            continue
                        candidate['reasons'].append('conflicting_stable_uuid')
                    elif stable_key:
                        claude_seen[stable_key] = candidate
                    session = sessions.setdefault(key, {'platform': platform, 'session_id': sid,
                                                       'cwd': candidate['cwd'], 'files': [],
                                                       'message_count': 0, 'uncertainty_count': 0,
                                                       'first_timestamp': None, 'last_timestamp': None})
                    if str(path) not in session['files']:
                        session['files'].append(str(path))
                    uncertain = bool(candidate['reasons'])
                    collection = 'uncertainties' if uncertain else 'messages'
                    session['uncertainty_count' if uncertain else 'message_count'] += 1
                    timestamp = candidate['timestamp']
                    if timestamp:
                        session['first_timestamp'] = min(session['first_timestamp'] or timestamp, timestamp)
                        session['last_timestamp'] = max(session['last_timestamp'] or timestamp, timestamp)
                    for key in list(candidate):
                        if key.startswith('_'):
                            del candidate[key]
                    if not uncertain:
                        del candidate['reasons']
                    report[collection].append(candidate)
                    counts[collection] += 1
    for collection in ('messages', 'uncertainties'):
        report[collection].sort(key=lambda m: (m['timestamp'] or '', m['platform'], m['sources'][0]['file'], m['sources'][0]['line']))
    report['sessions'] = sorted(sessions.values(), key=lambda s: (s['platform'], s['session_id']))
    report['filters'] = dict(sorted(filters.items()))
    report['complete'] = not report['errors']
    report['needs_review'] = bool(report['errors'] or report['uncertainties'])
    report['coverage'] = {
        'scope': '递归扫描全部会话文件，不按目录日期或文件修改时间过滤；history 不计入消息来源。',
        'status': '存在扫描或解析缺口' if report['errors'] else
                  ('扫描完成，但存在需要人工核对的疑似输入' if report['uncertainties'] else '扫描及已知结构解析完成'),
        'limitations': '仅识别已知日志结构；complete 表示扫描无已知错误，不保证未来或未知结构全量识别。',
    }
    reason_labels = {
        'conflicting_stable_uuid': '同会话相同 UUID 存在不同文本或时间，需人工核对',
        'unsupported_content_blocks': '含未知或非文本内容块',
        'non_text_user_input': '用户输入没有可提取文本',
        'missing_or_invalid_timestamp': '时间缺失或无效，无法确认是否属于目标范围',
        'nonhuman_or_unknown_origin': '来源不是已知 human，需人工核对',
        'possible_inherited_fork_input': 'fork 会话中可能继承的历史输入，缺少新回合证据',
    }
    for candidate in report['uncertainties']:
        candidate['reason_details'] = [reason_labels.get(r, r) for r in candidate['reasons']]
    return report


def classify_fork(candidates, meta, filters):
    fork = meta.get('forked_from_id')
    boundary = meta.get('subagent_history_start_ordinal')
    try:
        created = parse_timestamp(meta.get('timestamp'))
    except ValueError:
        created = None
    kept = []
    for candidate in candidates:
        ordinal = candidate['_ordinal']
        if isinstance(boundary, int) and isinstance(ordinal, int) and ordinal < boundary:
            filters['inherited_ordinal'] += 1
            continue
        if fork and created and candidate['_instant'] and candidate['_instant'] < created:
            filters['inherited_before_fork'] += 1
            continue
        if fork and not candidate['_active_turn'] and not candidate['_evidence']:
            candidate['reasons'].append('possible_inherited_fork_input')
        kept.append(candidate)
    return kept


def pair_codex(candidates, filters):
    """Pair representations one-to-one, not global text deduplication.

Same-turn identifiers or a five-second timestamp window link opposite event
kinds. Repeated representations are paired FIFO, preserving real repetitions.
"""
    result = []
    for candidate in candidates:
        kind = candidate['sources'][0]['event_type']
        matches = []
        for previous in reversed(result):
            if len(previous['sources']) != 1 or previous['sources'][0]['event_type'] == kind:
                continue
            if previous['text'] != candidate['text'] or previous['session_id'] != candidate['session_id']:
                continue
            a, b = previous['_turn'], candidate['_turn']
            if a and b and a != b:
                continue
            same_turn = bool(a and a == b)
            near = previous['_instant'] and candidate['_instant'] and abs((previous['_instant'] - candidate['_instant']).total_seconds()) <= 5
            if same_turn or near:
                matches.append(previous)
        if matches:
            # FIFO pairing keeps N identical response inputs + N events as N turns.
            previous = matches[-1]
            previous['sources'].extend(candidate['sources'])
            previous['reasons'] = sorted(set(previous['reasons'] + candidate['reasons']))
            if previous['_evidence'] or candidate['_evidence']:
                previous['reasons'] = [r for r in previous['reasons'] if r != 'possible_inherited_fork_input']
            filters['codex_duplicate_representation'] += 1
        else:
            result.append(candidate)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--date', help='YYYY-MM-DD; one local day')
    selection.add_argument('--period', choices=('today', 'yesterday', 'this-week', 'last-week'),
                           help='local calendar period; default: today; weeks start Monday')
    selection.add_argument('--start', help='paired with --end; YYYY-MM-DD or ISO8601, inclusive')
    parser.add_argument('--end', help='paired with --start; date includes whole day, ISO8601 is exclusive')
    parser.add_argument('--until', help='ISO8601 exclusive cutoff; default: scan start')
    parser.add_argument('--claude-root')
    parser.add_argument('--codex-root')
    args = parser.parse_args(argv)
    try:
        result = collect(args.date, None, args.until, args.claude_root, args.codex_root,
                         period=args.period, start=args.start, end=args.end)
    except (ValueError, ZoneInfoNotFoundError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
