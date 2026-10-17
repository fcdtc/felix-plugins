#!/usr/bin/env python3
"""YouTube 下载边界：鉴权、字幕、媒体缓存与稳定错误。"""
import dataclasses
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import common


@dataclasses.dataclass(frozen=True)
class TimeRange:
    start: float = 0.0
    end: object = None

    def __post_init__(self):
        if not math.isfinite(self.start) or self.start < 0:
            raise YouTubeError('invalid_input', 'parse', 'start-seconds 必须是非负数')
        if self.end is not None and (not math.isfinite(self.end) or self.end <= self.start):
            raise YouTubeError('invalid_input', 'parse', 'end-seconds 必须大于 start-seconds')

    @property
    def key(self):
        def render(value):
            return str(int(value)) if float(value).is_integer() else f'{value:.3f}'.rstrip('0').rstrip('.')
        return f'{render(self.start)}-{render(self.end) if self.end is not None else "full"}'

    @property
    def expected_duration(self):
        return None if self.end is None else self.end - self.start


@dataclasses.dataclass(frozen=True)
class VideoContext:
    video_id: str
    url: str
    title: str
    info: dict
    auth_args: tuple
    time_range: TimeRange
    workspace: Path


@dataclasses.dataclass(frozen=True)
class SubtitleArtifact:
    text: str
    language: str
    manual: bool


@dataclasses.dataclass(frozen=True)
class AudioArtifact:
    path: Path
    duration: float
    cache_hit: bool
    selector: str


class YouTubeError(RuntimeError):
    def __init__(self, code, stage, message, retryable=False, attempts=None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.attempts = attempts or []

    def payload(self):
        return {
            'schema_version': '1.0',
            'ok': False,
            'error': {
                'code': self.code,
                'stage': self.stage,
                'message': str(self),
                'retryable': self.retryable,
                'attempts': self.attempts,
            },
        }


VIDEO_ID = re.compile(r'(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})')
SUB_LANGS = ('zh-Hans', 'zh-Hant', 'zh-CN', 'zh-TW', 'zh', 'en', 'en-US', 'en-orig')
FORMAT_SELECTORS = ('bestaudio[ext=m4a]/bestaudio', 'best[acodec!=none]/best')


def parse_video_id(value):
    match = VIDEO_ID.search(value) or re.fullmatch(r'([A-Za-z0-9_-]{11})', value)
    if not match:
        raise YouTubeError('invalid_input', 'parse', f'无法解析 YouTube 视频 ID: {value}')
    return match.group(1)


def base_args():
    return [sys.executable, '-I', '-m', 'yt_dlp', '--ignore-config', '--no-update',
            '--js-runtimes', 'deno']


def auth_attempts():
    browser = os.environ.get('YT_COOKIES_FROM_BROWSER', '').strip()
    if browser:
        return [(browser, ('--cookies-from-browser', browser))]
    return [('anonymous', ()), ('chrome', ('--cookies-from-browser', 'chrome')),
            ('safari', ('--cookies-from-browser', 'safari'))]


def _run(args, **kwargs):
    print('[cmd] ' + ' '.join(map(str, args)), flush=True)
    kwargs.setdefault('check', False)
    return subprocess.run([str(value) for value in args], **kwargs)


def _diagnostic(result):
    text = (getattr(result, 'stderr', '') or '').strip().splitlines()
    return text[-1][-500:] if text else f'exit={getattr(result, "returncode", "unknown")}'


def resolve_video(value, output_base, time_range=None, runner=_run):
    video_id = parse_video_id(value)
    url = f'https://www.youtube.com/watch?v={video_id}'
    attempts = []
    for name, auth in auth_attempts():
        try:
            result = runner(base_args() + list(auth) + ['-j', '--skip-download', url],
                            capture_output=True, text=True)
        except OSError as error:
            attempts.append({'auth': name, 'error': type(error).__name__})
            continue
        if result.returncode == 0 and (result.stdout or '').strip():
            try:
                info = json.loads(result.stdout.strip().splitlines()[-1])
            except (TypeError, ValueError):
                attempts.append({'auth': name, 'error': 'invalid_json'})
                continue
            title = (info.get('title') or video_id).strip() or video_id
            workspace = Path(output_base) / '.work' / video_id / (time_range or TimeRange()).key
            return VideoContext(video_id, url, title, info, tuple(auth),
                                time_range or TimeRange(), workspace)
        attempts.append({'auth': name, 'error': _diagnostic(result)})
    raise YouTubeError('metadata_failed', 'metadata',
                       '无法获取 YouTube 视频信息；请检查登录状态、Cookie 或网络',
                       retryable=True, attempts=attempts)


def pick_subtitle(info):
    manual = info.get('subtitles') or {}
    for language in SUB_LANGS:
        if language in manual:
            return language, True
    for language in manual:
        if language.split('-')[0] in ('zh', 'en'):
            return language, True

    automatic = info.get('automatic_captions') or {}
    original = (info.get('language') or '').strip()
    if not original:
        return None
    if original in automatic:
        return original, False
    base = original.split('-')[0]
    candidates = [language for language in automatic if language.split('-')[0] == base]
    if len(candidates) == 1:
        return candidates[0], False
    return None


def _parse_vtt(path, time_range):
    timing = re.compile(
        r'(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2})[.,](?P<sms>\d{3})\s+-->\s+'
        r'(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2})[.,](?P<ems>\d{3})')
    cues = []
    current = None
    lines = []

    def seconds(match, prefix):
        return (int(match.group(prefix + 'h')) * 3600 + int(match.group(prefix + 'm')) * 60
                + int(match.group(prefix + 's')) + int(match.group(prefix + 'ms')) / 1000)

    def flush():
        if current is None:
            return
        start, end = current
        if end <= time_range.start or (time_range.end is not None and start >= time_range.end):
            return
        text = ' '.join(re.sub(r'<[^>]+>', '', line).strip() for line in lines).strip()
        if text:
            cues.append(text)

    with open(path, encoding='utf-8', errors='ignore') as handle:
        for raw in handle:
            line = raw.strip()
            match = timing.search(line)
            if match:
                flush()
                current = (seconds(match, 's'), seconds(match, 'e'))
                lines = []
            elif current is not None and line and not line.startswith(('NOTE', 'Kind:', 'Language:')):
                lines.append(line)
        flush()

    result = []
    previous = None
    for text in cues:
        if text != previous:
            result.append(text)
        previous = text
    return ' '.join(result)


def fetch_subtitle(context, runner=_run):
    picked = pick_subtitle(context.info)
    if not picked:
        raise YouTubeError('no_usable_subtitle', 'subtitle',
                           '没有可确认原语言的人工或自动字幕')
    language, manual = picked
    with tempfile.TemporaryDirectory(prefix='video-summary-subtitle-') as directory:
        template = os.path.join(directory, 'subtitle')
        args = base_args() + list(context.auth_args) + [
            '--skip-download', '--write-subs' if manual else '--write-auto-subs',
            '--sub-langs', language, '--sub-format', 'vtt/srt/best',
            '--convert-subs', 'vtt', '-o', template, context.url,
        ]
        result = runner(args, capture_output=True, text=True)
        files = sorted(Path(directory).glob('subtitle*.vtt'))
        if result.returncode != 0 or len(files) != 1:
            raise YouTubeError('subtitle_download_failed', 'subtitle',
                               '字幕下载或转换失败', retryable=True,
                               attempts=[{'auth': context.auth_args[-1] if context.auth_args else 'anonymous',
                                          'error': _diagnostic(result)}])
        text = _parse_vtt(files[0], context.time_range)
        if not text.strip():
            raise YouTubeError('no_usable_subtitle', 'subtitle', '请求范围内没有字幕内容')
        return SubtitleArtifact(text, language, manual)


def _probe_audio(path, runner=_run):
    result = runner([
        'ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_type:format=duration', '-of', 'json', path,
    ], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
        duration = float(payload['format']['duration'])
    except (KeyError, TypeError, ValueError):
        return None
    if not payload.get('streams') or not math.isfinite(duration) or duration <= 0:
        return None
    return duration


def _valid_duration(duration, time_range):
    expected = time_range.expected_duration
    return expected is None or abs(duration - expected) <= max(3.0, expected * 0.02)


def ensure_audio(context, runner=_run):
    target = context.workspace / 'audio.m4a'
    manifest = context.workspace / 'audio.json'
    if target.is_file() and target.stat().st_size > 1024 and manifest.is_file():
        duration = _probe_audio(target, runner)
        try:
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            metadata = {}
        if (duration and _valid_duration(duration, context.time_range)
                and metadata.get('video_id') == context.video_id
                and metadata.get('range') == context.time_range.key):
            return AudioArtifact(target, duration, True, metadata.get('selector', 'cached'))

    context.workspace.mkdir(parents=True, exist_ok=True)
    attempts = []
    for selector in FORMAT_SELECTORS:
        with tempfile.TemporaryDirectory(prefix='video-summary-download-') as directory:
            output = os.path.join(directory, 'audio.%(ext)s')
            args = base_args() + list(context.auth_args) + [
                '-f', selector, '-x', '--audio-format', 'm4a', '-o', output,
            ]
            if context.time_range.start or context.time_range.end is not None:
                end = '' if context.time_range.end is None else str(context.time_range.end)
                args += ['--download-sections', f'*{context.time_range.start}-{end}']
            args.append(context.url)
            result = runner(args, capture_output=True, text=True)
            candidates = sorted(Path(directory).glob('audio.m4a'))
            candidate = candidates[0] if len(candidates) == 1 else None
            duration = _probe_audio(candidate, runner) if candidate else None
            if result.returncode == 0 and candidate and duration \
                    and _valid_duration(duration, context.time_range):
                staged_manifest = Path(directory) / 'audio.json'
                staged_manifest.write_text(json.dumps({
                    'video_id': context.video_id,
                    'range': context.time_range.key,
                    'selector': selector,
                    'duration': duration,
                }, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(candidate, target)
                os.replace(staged_manifest, manifest)
                return AudioArtifact(target, duration, False, selector)
            attempts.append({'selector': selector, 'error': _diagnostic(result)})
            diagnostic = _diagnostic(result).lower()
            if result.returncode != 0 and 'requested format is not available' not in diagnostic:
                break
    raise YouTubeError('download_failed', 'audio_download',
                       'YouTube 音频下载失败', retryable=True, attempts=attempts)


def emit_error(error):
    print('ERROR_JSON:' + json.dumps(error.payload(), ensure_ascii=False), flush=True)
