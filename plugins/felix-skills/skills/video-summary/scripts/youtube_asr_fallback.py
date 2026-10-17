#!/usr/bin/env python3
"""YouTube 无字幕时下载音频并执行 mlx-whisper ASR。"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import youtube  # noqa: E402

OUTPUT_BASE = os.path.join(os.getcwd(), 'yt_temp')


def parser():
    value = argparse.ArgumentParser()
    value.add_argument('video')
    value.add_argument('lang', nargs='?', choices=tuple(common.MODELS), default='en')
    value.add_argument('--start-seconds', type=float, default=0.0)
    value.add_argument('--end-seconds', type=float)
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        time_range = youtube.TimeRange(args.start_seconds, args.end_seconds)
        context = youtube.resolve_video(args.video, OUTPUT_BASE, time_range)
        common.ensure_deps()
        artifact = youtube.ensure_audio(context)
        print(f'[audio] {"复用" if artifact.cache_hit else "下载"} {artifact.path}', flush=True)
        common.ensure_model(args.lang)
        transcript = common.transcribe(str(artifact.path), args.lang)
        out_dir = common.output_dir(OUTPUT_BASE, context.title, context.video_id)
        common.chunk_and_emit(
            context.video_id, context.title, transcript, args.lang, out_dir,
            timestamp_offset_seconds=time_range.start,
            result_fields={
                'time_range': {'start': time_range.start, 'end': time_range.end},
                'audio': {'cache_hit': artifact.cache_hit, 'selector': artifact.selector},
            })
        return 0
    except youtube.YouTubeError as error:
        youtube.emit_error(error)
        return 1
    except common.ASRQualityError as error:
        common.emit_asr_error(error)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
