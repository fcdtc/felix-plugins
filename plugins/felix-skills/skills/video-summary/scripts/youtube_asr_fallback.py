#!/usr/bin/env python3
"""
YouTube ASR 兜底：无字幕时下载音频并用 mlx-whisper 转写。

语言规则（YouTube 内容以英文为主）：
  - 默认用英文转写（whisper 对英文识别最准，英文视频占绝大多数）
  - 传 zh 则用中文模型转写（中文视频无字幕时）
  逐字稿可以保留英文原样；总结稿始终由总结流程用中文撰写。

依赖、模型下载、转写、分块交付等公共逻辑见 common.py；
本脚本只负责 YouTube 特有部分：视频 ID 解析、bot 校验降级链、音频下载。

用法:
    python3 youtube_asr_fallback.py <YouTube_URL_or_VideoID> [en|zh]

输出约定同 youtube_download_and_chunk.py:
    yt_temp/<VIDEO_ID>/ 逐字稿.md / 总结稿.md / <VIDEO_ID>_chunk_*.txt
    RESULT_JSON:{...}
"""
import os
import sys
import json
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

OUTPUT_BASE = os.path.join(os.getcwd(), 'yt_temp')


def extract_video_id(arg):
    m = (re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})', arg)
         or re.match(r'^([A-Za-z0-9_-]{11})$', arg))
    if not m:
        raise SystemExit(f'无法从输入解析 YouTube 视频 ID: {arg}')
    return m.group(1)


def ytdlp_base_args():
    args = ['yt-dlp', '--no-update', '--remote-components', 'ejs:github']
    browser = os.environ.get('YT_COOKIES_FROM_BROWSER')
    if browser:
        args += ['--cookies-from-browser', browser]
    return args


def ytdlp_attempts():
    attempts = [[]]
    for browser in ['chrome', 'safari']:
        if not os.environ.get('YT_COOKIES_FROM_BROWSER'):
            attempts.append(['--cookies-from-browser', browser])
    return attempts


def download_audio(video_id, url, out_dir):
    audio_path = os.path.join(out_dir, 'audio.m4a')
    if not os.path.exists(audio_path):
        for extra in ytdlp_attempts():
            try:
                r = common.run(ytdlp_base_args() + extra + ['-f', 'bestaudio[ext=m4a]/bestaudio',
                                                            '-o', audio_path, url])
            except Exception:
                continue
            if r.returncode == 0 and os.path.exists(audio_path):
                return audio_path
        raise RuntimeError('音频下载失败（bot 校验/地区受限/需要登录）。'
                           '可设置 YT_COOKIES_FROM_BROWSER=<浏览器名> 后重试')
    return audio_path


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 youtube_asr_fallback.py <YouTube_URL_or_VideoID> [en|zh]')
        sys.exit(1)
    video_id = extract_video_id(sys.argv[1])
    lang = sys.argv[2] if len(sys.argv) > 2 else 'en'
    if lang not in common.MODELS:
        raise SystemExit(f'不支持的语言: {lang}（可选 en/zh）')
    url = f'https://www.youtube.com/watch?v={video_id}'

    # 依赖检查
    common.ensure_deps()

    # 标题
    r = common.run(ytdlp_base_args() + ['-j', '--skip-download', url],
                   capture_output=True, text=True)
    title = video_id
    if r.returncode == 0 and r.stdout.strip():
        try:
            title = json.loads(r.stdout.strip().splitlines()[-1]).get('title', video_id)
        except Exception:
            pass

    # 产物目录以视频标题命名（拿不到标题时回退视频 ID）
    out_dir = common.output_dir(OUTPUT_BASE, title, video_id)

    print(f'[audio] 下载 {video_id} 音频轨...', flush=True)
    audio_path = download_audio(video_id, url, out_dir)

    print(f'[model] 确认模型（{lang}）...', flush=True)
    common.ensure_model(lang)

    print('[asr] 转写中...', flush=True)
    txt_path = common.transcribe(audio_path, out_dir, lang)

    common.chunk_and_emit(video_id, title, txt_path, lang)


if __name__ == '__main__':
    main()
