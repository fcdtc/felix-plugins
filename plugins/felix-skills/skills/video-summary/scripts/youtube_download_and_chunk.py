#!/usr/bin/env python3
"""下载 YouTube 原语言字幕，分块并输出 RESULT_JSON。"""
import argparse
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import youtube  # noqa: E402

CHARS_PER_CHUNK = 100000
OUTPUT_BASE = os.path.join(os.getcwd(), 'yt_temp')


def parser():
    value = argparse.ArgumentParser()
    value.add_argument('video')
    value.add_argument('--start-seconds', type=float, default=0.0)
    value.add_argument('--end-seconds', type=float)
    return value


def publish(context, artifact):
    out_dir = common.output_dir(OUTPUT_BASE, context.title, context.video_id)
    os.makedirs(out_dir, exist_ok=True)
    verbatim = os.path.join(out_dir, '逐字稿.md')
    summary = os.path.join(out_dir, '总结稿.md')
    chunks = []
    with tempfile.TemporaryDirectory(prefix='.video-summary-subtitle-stage-', dir=out_dir) as stage:
        staged_verbatim = os.path.join(stage, '逐字稿.md')
        with open(staged_verbatim, 'w', encoding='utf-8') as handle:
            handle.write(f'# {context.title}\n\n')
            handle.write('> 本逐字稿来源于 YouTube 原语言字幕。\n\n---\n\n')
            handle.write(artifact.text)
        staged = [(staged_verbatim, verbatim)]
        for offset in range(0, len(artifact.text), CHARS_PER_CHUNK):
            index = offset // CHARS_PER_CHUNK
            name = f'{context.video_id}_chunk_{index}.txt'
            source = os.path.join(stage, name)
            destination = os.path.join(out_dir, name)
            with open(source, 'w', encoding='utf-8') as handle:
                handle.write(artifact.text[offset:offset + CHARS_PER_CHUNK])
            staged.append((source, destination))
            chunks.append(destination)
        for source, destination in staged:
            os.replace(source, destination)
    if not os.path.exists(summary):
        with open(summary, 'x', encoding='utf-8') as handle:
            handle.write(f'# {context.title} —— 内容总结\n\n')
            handle.write('<!-- 总结内容将由后续流程生成 -->\n')
    payload = {
        'schema_version': '1.0', 'ok': True, 'video_id': context.video_id,
        'title': context.title, 'total_chars': len(artifact.text), 'source': 'subtitle',
        'subtitle': {'language': artifact.language, 'manual': artifact.manual},
        'time_range': {'start': context.time_range.start, 'end': context.time_range.end},
        'deliverables': {'verbatim': verbatim, 'summary': summary}, 'chunks': chunks,
    }
    print('RESULT_JSON:' + json.dumps(payload, ensure_ascii=False), flush=True)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        time_range = youtube.TimeRange(args.start_seconds, args.end_seconds)
        context = youtube.resolve_video(args.video, OUTPUT_BASE, time_range)
        artifact = youtube.fetch_subtitle(context)
        publish(context, artifact)
        return 0
    except youtube.YouTubeError as error:
        youtube.emit_error(error)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
