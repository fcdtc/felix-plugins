#!/usr/bin/env python3
"""公共模块：模型准备、抗幻觉 ASR、质量门禁和交付物生成。"""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import Counter

HF_MIRROR = 'https://hf-mirror.com'
CHARS_PER_CHUNK = 100000
ASR_SEGMENT_SECONDS = 600.0
ASR_REPAIR_SECONDS = 120.0
MAX_REPAIR_INTERVALS = 8
QUALITY_POLICY_VERSION = 1

MODELS = {
    'en': 'mlx-community/whisper-turbo',
    'zh': 'mlx-community/whisper-large-v3-mlx',
}
MODEL_FILE_CANDIDATES = [
    'config.json', 'tokenizer.json', 'vocab.json', 'vocabulary.json', 'weights.npz',
]


class ASRQualityError(RuntimeError):
    def __init__(self, report):
        super().__init__('ASR 结果未通过质量门禁')
        self.report = report


def run(cmd, **kw):
    """执行命令，实时输出。"""
    print(f"[cmd] {' '.join(cmd) if isinstance(cmd, list) else cmd}", flush=True)
    kw.setdefault('check', False)
    return subprocess.run(cmd, **kw)


def require_cmd(cmd):
    """验证外部命令存在；Python 依赖由 launcher 管理。"""
    path = shutil.which(cmd)
    if not path:
        raise RuntimeError(f'缺少外部命令 {cmd}，请先安装后重试')
    return path


def sanitize_filename(name):
    return re.sub(r'[\/:*?"<>|]', '_', name)


def output_dir(base, title, video_id):
    """按净化后的视频标题创建产物目录。"""
    name = sanitize_filename((title or '').strip())[:80].strip() or video_id
    path = os.path.join(base, name)
    os.makedirs(path, exist_ok=True)
    return path


def ensure_deps():
    """验证 launcher 已准备 Python 环境及 ASR 外部命令。"""
    if not os.environ.get('VIDEO_SUMMARY_VENV'):
        raise RuntimeError('请通过 scripts/launcher.py 启动 video-summary')
    import mlx_whisper  # noqa: F401
    import yt_dlp  # noqa: F401
    require_cmd('ffmpeg')
    require_cmd('ffprobe')
    require_cmd('curl')


def huggingface_hub_roots():
    roots = []
    if os.environ.get('HF_HUB_CACHE'):
        roots.append(os.path.expanduser(os.environ['HF_HUB_CACHE']))
    if os.environ.get('HF_HOME'):
        roots.append(os.path.join(os.path.expanduser(os.environ['HF_HOME']), 'hub'))
    xdg = os.environ.get('XDG_CACHE_HOME')
    if xdg:
        roots.append(os.path.join(os.path.expanduser(xdg), 'huggingface', 'hub'))
    roots.append(os.path.expanduser('~/.cache/huggingface/hub'))
    return list(dict.fromkeys(roots))


def model_snapshots(model, root):
    cache = os.path.join(root, f'models--{model.replace("/", "--")}')
    candidates = []
    ref = os.path.join(cache, 'refs', 'main')
    if os.path.isfile(ref):
        try:
            revision = open(ref, encoding='utf-8').read().strip()
        except OSError:
            revision = ''
        if revision:
            candidates.append(os.path.join(cache, 'snapshots', revision))
    candidates.append(os.path.join(cache, 'snapshots', 'main'))
    snapshots = os.path.join(cache, 'snapshots')
    if os.path.isdir(snapshots):
        candidates.extend(os.path.join(snapshots, name) for name in os.listdir(snapshots))
    return list(dict.fromkeys(candidates))


def model_snapshot(model, root=None):
    root = root or huggingface_hub_roots()[0]
    return model_snapshots(model, root)[0]


def model_cache_dir(lang):
    """按 HF_HOME、XDG、旧默认缓存顺序返回完整模型。"""
    for root in huggingface_hub_roots():
        for path in model_snapshots(MODELS[lang], root):
            weights = os.path.join(path, 'weights.npz')
            config = os.path.join(path, 'config.json')
            if os.path.exists(config) \
                    and os.path.exists(weights) and os.path.getsize(weights) >= 1_000_000_000:
                return path
    return None


def ensure_model(lang):
    """确认模型完整；不完整则通过 hf-mirror 下载。"""
    model = MODELS[lang]
    snapshot = model_snapshot(model, huggingface_hub_roots()[0])
    files = [f for f in os.listdir(snapshot) if f != 'weights.npz'] if os.path.isdir(snapshot) else []
    weights = os.path.join(snapshot, 'weights.npz')
    need_weights = not os.path.exists(weights) or os.path.getsize(weights) < 1_000_000_000
    if files and not need_weights and any(f.endswith('.json') for f in files):
        print(f'[model] 已存在完整模型: {snapshot}', flush=True)
        return
    os.makedirs(snapshot, exist_ok=True)
    base = f'{HF_MIRROR}/{model}/resolve/main'
    for filename in MODEL_FILE_CANDIDATES:
        dst = os.path.join(snapshot, filename)
        if os.path.exists(dst):
            continue
        result = run([
            'curl', '-L', '--retry', '5', '--retry-delay', '3', '-o', dst,
            '-w', '%{http_code}', f'{base}/{filename}',
        ], capture_output=True, text=True)
        code = (result.stdout or '').strip()[-3:]
        if code == '404':
            os.remove(dst)
            print(f'[model] 跳过不存在的 {filename}', flush=True)
    run(['curl', '-L', '--retry', '5', '--retry-delay', '3', '-C', '-',
         '-o', weights, f'{base}/weights.npz'], check=True)
    print('[model] 下载完成', flush=True)


def ensure_whisper_ready(lang):
    ensure_deps()
    if not model_cache_dir(lang):
        ensure_model(lang)


def probe_audio_duration(audio_path):
    """返回音频秒数；探测失败或时长非法时抛错。"""
    result = run([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nw=1:nk=1', audio_path,
    ], capture_output=True, text=True, check=True)
    duration = float(result.stdout.strip())
    if not math.isfinite(duration) or duration <= 0:
        raise RuntimeError(f'无法取得有效音频时长: {duration!r}')
    return duration


def transcribe_clip(audio_path, lang):
    """识别一个独立音频片段；此函数是唯一的模型调用边界。"""
    import mlx_whisper

    model = model_cache_dir(lang) or MODELS[lang]
    return mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=model,
        language=lang,
        task='transcribe',
        temperature=(0.0, 0.2, 0.4, 0.6, 0.8, 1.0),
        condition_on_previous_text=False,
        word_timestamps=True,
        hallucination_silence_threshold=2.0,
        compression_ratio_threshold=2.4,
        logprob_threshold=-1.0,
        no_speech_threshold=0.6,
        verbose=False,
    )


def _extract_clip(source, destination, start, duration):
    run([
        'ffmpeg', '-v', 'error', '-xerror', '-y', '-ss', f'{start:.3f}',
        '-t', f'{duration:.3f}', '-i', source, '-vn', '-ac', '1', '-ar', '16000',
        '-c:a', 'flac', destination,
    ], check=True)


def _globalize_segments(result, offset):
    segments = []
    for raw in result.get('segments', []):
        segment = dict(raw)
        segment['start'] = float(segment.get('start', 0)) + offset
        segment['end'] = float(segment.get('end', 0)) + offset
        words = []
        for raw_word in segment.get('words', []):
            word = dict(raw_word)
            word['start'] = float(word.get('start', 0)) + offset
            word['end'] = float(word.get('end', 0)) + offset
            words.append(word)
        segment['words'] = words
        segments.append(segment)
    return segments


def _recognize_intervals(audio_path, lang, intervals, temp_dir):
    segments = []
    attempts = []
    for index, (start, end) in enumerate(intervals):
        clip = os.path.join(temp_dir, f'clip-{index:03d}.flac')
        _extract_clip(audio_path, clip, start, end - start)
        try:
            result = transcribe_clip(clip, lang)
            clip_segments = _globalize_segments(result, start)
            segments.extend(clip_segments)
            attempts.append({'start': start, 'end': end, 'segment_count': len(clip_segments)})
        finally:
            if os.path.exists(clip):
                os.remove(clip)
    segments.sort(key=lambda item: (item['start'], item['end']))
    return segments, attempts


def _normalize(text):
    return re.sub(r'[\W_]+', '', text or '', flags=re.UNICODE).lower()


def _violation(code, observed, limit, start=None, end=None):
    value = {'code': code, 'observed': observed, 'limit': limit}
    if isinstance(start, (int, float)) and math.isfinite(start):
        value['start'] = round(max(0.0, start), 3)
    if isinstance(end, (int, float)) and math.isfinite(end):
        value['end'] = round(max(0.0, end), 3)
    return value


def assess_transcript(transcript, audio_duration):
    """确定性评估结构化转写；不修改文本。"""
    segments = transcript.get('segments') or []
    violations = []
    normalized = [_normalize(item.get('text', '')) for item in segments]
    text = ''.join(item.get('text', '') for item in segments).strip()

    if not segments:
        violations.append(_violation('empty_segments', 0, 1, 0, audio_duration))
    if not _normalize(text):
        violations.append(_violation('empty_transcript', 0, 1, 0, audio_duration))

    previous_start = -1.0
    previous_end = -1.0
    valid_segments = []
    for index, segment in enumerate(segments):
        start = segment.get('start')
        end = segment.get('end')
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) \
                or not math.isfinite(start) or not math.isfinite(end) \
                or start < 0 or end <= start or end > audio_duration + 2:
            violations.append(_violation('invalid_segment_timestamp', index, 'valid',
                                         start if isinstance(start, (int, float)) else 0,
                                         end if isinstance(end, (int, float)) else 0))
            continue
        if start + 1 < previous_start or end + 1 < previous_end:
            violations.append(_violation('non_monotonic_segments',
                                         {'start': start, 'end': end},
                                         {'start': previous_start, 'end': previous_end},
                                         start, end))
        previous_start = max(previous_start, start)
        previous_end = max(previous_end, end)
        if _normalize(segment.get('text', '')):
            valid_segments.append(segment)
        words = segment.get('words') or []
        if _normalize(segment.get('text', '')) and not words:
            violations.append(_violation('missing_word_timestamps', index, 0, start, end))
        word_previous = start - 1
        for word in words:
            word_start = word.get('start')
            word_end = word.get('end')
            if not isinstance(word_start, (int, float)) or not isinstance(word_end, (int, float)) \
                    or not math.isfinite(word_start) or not math.isfinite(word_end) \
                    or word_start < start - 1 or word_end > end + 1 or word_end < word_start \
                    or word_start + 0.2 < word_previous:
                violations.append(_violation('invalid_word_timestamp', index, 'monotonic', start, end))
                break
            word_previous = max(word_previous, word_start)

    run_start = 0
    for index in range(1, len(normalized) + 1):
        if index < len(normalized) and normalized[index] == normalized[run_start]:
            continue
        count = index - run_start
        if len(normalized[run_start]) >= 8 and count >= 4:
            first = segments[run_start]
            last = segments[index - 1]
            violations.append(_violation('consecutive_repetition', count, 3,
                                         first.get('start'), last.get('end')))
        run_start = index

    for index in range(0, len(normalized) - 7):
        pair = normalized[index:index + 2]
        if min(map(len, pair), default=0) < 6:
            continue
        repeats = 1
        while index + (repeats + 1) * 2 <= len(normalized) \
                and normalized[index + repeats * 2:index + (repeats + 1) * 2] == pair:
            repeats += 1
        if repeats >= 4:
            violations.append(_violation('alternating_repetition', repeats, 3,
                                         segments[index].get('start'),
                                         segments[index + repeats * 2 - 1].get('end')))
            break

    for index, value in enumerate(normalized):
        for width in range(6, min(80, len(value) // 4) + 1):
            tail = value[-width:]
            repeats = 1
            cursor = len(value) - width * 2
            while cursor >= 0 and value[cursor:cursor + width] == tail:
                repeats += 1
                cursor -= width
            if repeats >= 4:
                segment = segments[index]
                violations.append(_violation('intra_segment_repetition', repeats, 3,
                                             segment.get('start'), segment.get('end')))
                break

    counts = Counter(value for value in normalized if len(value) >= 8)
    nonempty_segment_count = sum(bool(value) for value in normalized)
    if counts and nonempty_segment_count:
        value, count = counts.most_common(1)[0]
        ratio = count / nonempty_segment_count
        if count >= 8 and ratio >= 0.25:
            indices = [i for i, item in enumerate(normalized) if item == value]
            violations.append(_violation('dominant_repetition', round(ratio, 4), 0.25,
                                         segments[indices[0]].get('start'),
                                         segments[indices[-1]].get('end')))

    if valid_segments:
        first_start = valid_segments[0]['start']
        last_end = valid_segments[-1]['end']
        edge_limit = min(120.0, max(30.0, audio_duration * 0.25))
        if first_start > edge_limit:
            violations.append(_violation('long_edge_gap', first_start, edge_limit, 0, first_start))
        if audio_duration - last_end > edge_limit:
            violations.append(_violation('long_edge_gap', audio_duration - last_end, edge_limit,
                                         last_end, audio_duration))
        for left, right in zip(valid_segments, valid_segments[1:]):
            gap = right['start'] - left['end']
            if gap > 60:
                violations.append(_violation('long_internal_gap', gap, 60, left['end'], right['start']))

    for window_start in range(0, max(1, int(audio_duration)), 30):
        window_end = min(audio_duration, window_start + 60)
        if window_end - window_start < 50:
            continue
        chars = sum(len(_normalize(segment.get('text', ''))) for segment in valid_segments
                    if segment['start'] < window_end and segment['end'] > window_start)
        density = chars / (window_end - window_start)
        if density > 25:
            violations.append(_violation('excessive_text_density', round(density, 3), 25,
                                         window_start, window_end))
            break

    return {
        'ok': not violations,
        'policy_version': QUALITY_POLICY_VERSION,
        'metrics': {
            'segment_count': len(segments),
            'text_chars': len(_normalize(text)),
            'covered_until_seconds': round(valid_segments[-1]['end'], 3) if valid_segments else 0,
        },
        'violations': violations,
    }


def _repair_intervals(violations, audio_duration):
    ranges = []
    for violation in violations:
        if 'start' not in violation or 'end' not in violation:
            continue
        start = max(0.0, violation['start'] - 2)
        end = min(audio_duration, violation['end'] + 2)
        if end > start:
            ranges.append((start, end))
    ranges.sort()
    merged = []
    for start, end in ranges:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    intervals = []
    for start, end in merged:
        cursor = start
        while cursor < end:
            intervals.append((cursor, min(end, cursor + ASR_REPAIR_SECONDS)))
            cursor += ASR_REPAIR_SECONDS
    return intervals


def _align_repair_intervals(intervals, segments, audio_duration):
    aligned = []
    for start, end in intervals:
        for segment in segments:
            segment_start = segment.get('start')
            segment_end = segment.get('end')
            if not isinstance(segment_start, (int, float)) \
                    or not isinstance(segment_end, (int, float)):
                continue
            if segment_start < end and segment_end > start:
                start = min(start, segment_start)
                end = max(end, segment_end)
        aligned.append((max(0.0, start), min(audio_duration, end)))
    aligned.sort()
    merged = []
    for start, end in aligned:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    result = []
    for start, end in merged:
        cursor = start
        while cursor < end:
            result.append((cursor, min(end, cursor + ASR_REPAIR_SECONDS)))
            cursor += ASR_REPAIR_SECONDS
    return result


def _replace_intervals(segments, replacements, intervals):
    kept = [segment for segment in segments
            if isinstance(segment.get('start'), (int, float))
            and isinstance(segment.get('end'), (int, float))
            and math.isfinite(segment['start']) and math.isfinite(segment['end'])
            and not any(segment['start'] < end and segment['end'] > start
                        for start, end in intervals)]
    kept.extend(replacements)
    kept.sort(key=lambda item: (item['start'], item['end']))
    return kept


def transcribe(audio_path, lang):
    """分段识别、质量评估并执行至多一轮局部修复。"""
    audio_duration = probe_audio_duration(audio_path)
    intervals = []
    start = 0.0
    while start < audio_duration:
        intervals.append((start, min(audio_duration, start + ASR_SEGMENT_SECONDS)))
        start += ASR_SEGMENT_SECONDS

    with tempfile.TemporaryDirectory(prefix='video-summary-asr-') as temp_dir:
        segments, attempts = _recognize_intervals(audio_path, lang, intervals, temp_dir)
        transcript = {'text': ''.join(item.get('text', '') for item in segments),
                      'segments': segments, 'language': lang, 'audio_duration': audio_duration,
                      'attempts': attempts, 'repair_rounds': 0}
        quality = assess_transcript(transcript, audio_duration)
        if not quality['ok']:
            repairs = _repair_intervals(quality['violations'], audio_duration)
            repairs = _align_repair_intervals(repairs, segments, audio_duration)
            if not repairs or len(repairs) > MAX_REPAIR_INTERVALS:
                transcript['quality'] = quality
                raise ASRQualityError(quality)
            replacement_segments, repair_attempts = _recognize_intervals(
                audio_path, lang, repairs, temp_dir)
            segments = _replace_intervals(segments, replacement_segments, repairs)
            transcript.update({
                'text': ''.join(item.get('text', '') for item in segments),
                'segments': segments,
                'attempts': attempts + repair_attempts,
                'repair_rounds': 1,
            })
            quality = assess_transcript(transcript, audio_duration)
        transcript['quality'] = quality
        if not quality['ok']:
            raise ASRQualityError(quality)
        return transcript


def render_transcript_text(transcript):
    """把通过门禁的结构化结果渲染为带时间戳的纯文本。"""
    if not transcript.get('quality', {}).get('ok'):
        raise ASRQualityError(transcript.get('quality', {'ok': False, 'violations': []}))
    lines = []
    for segment in transcript['segments']:
        seconds = max(0, int(round(segment['start'])))
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        text = segment.get('text', '').strip()
        if text:
            lines.append(f'[{hours:02d}:{minutes:02d}:{seconds:02d}] {text}')
    return '\n\n'.join(lines) + '\n'


def chunk_and_emit(video_id, title, transcript, lang, out_dir, id_field='video_id'):
    """可回滚地发布通过门禁的 ASR 交付物。"""
    quality = transcript.get('quality', {})
    if not quality.get('ok'):
        raise ASRQualityError(quality)
    os.makedirs(out_dir, exist_ok=True)
    full_text = render_transcript_text(transcript)
    total = len(full_text)

    transcript_json = os.path.join(out_dir, 'asr_transcript.json')
    quality_json = os.path.join(out_dir, 'asr_quality.json')
    verbatim_md = os.path.join(out_dir, '逐字稿.md')
    summary_md = os.path.join(out_dir, '总结稿.md')
    persisted_transcript = {key: value for key, value in transcript.items() if key != 'out_dir'}

    staged = []
    chunks = []
    with tempfile.TemporaryDirectory(prefix='.video-summary-stage-', dir=out_dir) as stage:
        staged_transcript = os.path.join(stage, 'asr_transcript.json')
        staged_quality = os.path.join(stage, 'asr_quality.json')
        staged_verbatim = os.path.join(stage, '逐字稿.md')
        with open(staged_transcript, 'w', encoding='utf-8') as handle:
            json.dump(persisted_transcript, handle, ensure_ascii=False, indent=2)
        with open(staged_quality, 'w', encoding='utf-8') as handle:
            json.dump(quality, handle, ensure_ascii=False, indent=2)
        with open(staged_verbatim, 'w', encoding='utf-8') as handle:
            handle.write(f'# {title}\n\n')
            handle.write(f'> 本逐字稿由 mlx-whisper({MODELS[lang]}) 分段转写并通过质量门禁'
                         f'（语言: {lang}），原文语言保留，仅供内容参考。\n\n---\n\n')
            handle.write(full_text)
        staged.extend([(staged_transcript, transcript_json),
                       (staged_quality, quality_json), (staged_verbatim, verbatim_md)])
        for offset in range(0, total, CHARS_PER_CHUNK):
            index = offset // CHARS_PER_CHUNK
            filename = f'{video_id}_chunk_{index}.txt'
            staged_chunk = os.path.join(stage, filename)
            chunk_file = os.path.join(out_dir, filename)
            with open(staged_chunk, 'w', encoding='utf-8') as handle:
                handle.write(full_text[offset:offset + CHARS_PER_CHUNK])
            staged.append((staged_chunk, chunk_file))
            chunks.append(chunk_file)
        active_chunks = set(chunks)
        stale_chunks = []
        for filename in os.listdir(out_dir):
            path = os.path.join(out_dir, filename)
            if re.fullmatch(re.escape(video_id) + r'_chunk_\d+\.txt', filename) \
                    and os.path.isfile(path) and path not in active_chunks:
                stale_chunks.append(path)

        backups = []
        committed = []
        try:
            for index, (source, destination) in enumerate(staged):
                backup = os.path.join(stage, f'backup-{index:03d}')
                if os.path.exists(destination):
                    os.replace(destination, backup)
                    backups.append((backup, destination))
                os.replace(source, destination)
                committed.append(destination)
            for index, path in enumerate(stale_chunks, start=len(staged)):
                backup = os.path.join(stage, f'backup-{index:03d}')
                os.replace(path, backup)
                backups.append((backup, path))
        except BaseException:
            for destination in reversed(committed):
                if os.path.exists(destination):
                    os.remove(destination)
            for backup, destination in reversed(backups):
                if os.path.exists(backup):
                    os.replace(backup, destination)
            raise

    summary_temp = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='w', encoding='utf-8', dir=out_dir,
                prefix='.summary-placeholder-', delete=False) as handle:
            summary_temp = handle.name
            handle.write(f'# {title} —— 内容总结\n\n')
            handle.write('<!-- 总结内容将由后续流程生成 -->\n')
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(summary_temp, summary_md)
        except FileExistsError:
            pass
    finally:
        if summary_temp and os.path.exists(summary_temp):
            os.remove(summary_temp)

    payload = {
        'schema_version': '1.0',
        'ok': True,
        id_field: video_id,
        'title': title,
        'total_chars': total,
        'source': 'asr',
        'transcript_lang': lang,
        'quality': {
            'ok': True,
            'policy_version': quality['policy_version'],
            'repair_rounds': transcript.get('repair_rounds', 0),
            'report': quality_json,
        },
        'deliverables': {'verbatim': verbatim_md, 'summary': summary_md},
        'chunks': chunks,
    }
    print(f'[ASR] Success. Total chunks: {len(chunks)}', flush=True)
    print('RESULT_JSON:' + json.dumps(payload, ensure_ascii=False), flush=True)
    return payload


def emit_asr_error(error):
    """输出稳定的机器可读 ASR 失败协议。"""
    report = error.report if isinstance(error, ASRQualityError) else {}
    payload = {
        'schema_version': '1.0',
        'ok': False,
        'error': {'code': 'asr_quality_gate_failed', 'message': str(error), 'quality': report},
    }
    print('ASR_ERROR_JSON:' + json.dumps(payload, ensure_ascii=False), flush=True)
