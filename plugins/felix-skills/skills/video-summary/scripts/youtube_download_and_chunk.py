#!/usr/bin/env python3
"""
YouTube 字幕下载主流程：用 yt-dlp 拉取官方/自动字幕，分块并输出 RESULT_JSON。

用法:
    python3 download_and_chunk.py <YouTube_URL_or_VideoID>

输出约定（与 bilibili skill 一致）:
    yt_temp/<VIDEO_ID>/逐字稿.md      完整文字稿
    yt_temp/<VIDEO_ID>/总结稿.md      占位，由总结流程写入
    RESULT_JSON:{...}
"""
import os
import re
import sys
import json
import shutil
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

CHARS_PER_CHUNK = 100000
OUTPUT_BASE = os.path.join(os.getcwd(), 'yt_temp')

# 字幕语言优先级：中文优先，英文兜底
SUB_LANGS = 'zh-Hans,zh-Hant,zh-CN,zh-TW,zh,en,en-US,en-orig'


def run(cmd, **kw):
    print(f"[cmd] {' '.join(cmd)}", flush=True)
    return subprocess.run(cmd, check=False, **kw)


def extract_video_id(arg):
    m = (re.search(r'(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})', arg)
         or re.match(r'^([A-Za-z0-9_-]{11})$', arg))
    if not m:
        raise SystemExit(f'无法从输入解析 YouTube 视频 ID: {arg}')
    return m.group(1)


def ytdlp_base_args():
    """公共参数：JS 挑战求解器（新版 yt-dlp 必须）+ 可选浏览器 Cookie。"""
    args = ['yt-dlp', '--no-update', '--remote-components', 'ejs:github']
    browser = os.environ.get('YT_COOKIES_FROM_BROWSER')
    if browser:
        args += ['--cookies-from-browser', browser]
    return args


def ytdlp_attempts():
    """鉴权降级链：裸请求 → Chrome Cookie → Safari Cookie。返回生效的附加参数。"""
    attempts = [[]]
    for browser in ['chrome', 'safari']:
        if not os.environ.get('YT_COOKIES_FROM_BROWSER'):
            attempts.append(['--cookies-from-browser', browser])
    return attempts


def get_info(video_id):
    """取视频元信息，按鉴权降级链依次尝试。"""
    url = f'https://www.youtube.com/watch?v={video_id}'
    for extra in ytdlp_attempts():
        try:
            r = run(ytdlp_base_args() + extra + ['-j', '--skip-download', url],
                    capture_output=True, text=True)
        except Exception:
            continue
        if r.returncode == 0 and r.stdout.strip():
            return json.loads(r.stdout.strip().splitlines()[-1]), url, extra
        print(r.stderr[-500:], flush=True)
    raise SystemExit('ERROR: yt-dlp 获取视频信息失败（bot 校验/地区受限/需要登录）。'
                     '可设置环境变量 YT_COOKIES_FROM_BROWSER=<浏览器名> 后重试')


def pick_lang(info):
    """选字幕语言，绝不用 YouTube 机翻字幕（机翻质量差且会丢失原意）。

    1. 人工字幕（subtitles）：中文优先，英文兜底
    2. 自动字幕（automatic_captions）：只用视频原语言那条轨道
       （automatic_captions 里的其他语言都是 YouTube 机翻产物，跳过）
    返回 (lang, is_manual)；无字幕返回 None。
    """
    manual = info.get('subtitles') or {}
    for lang in SUB_LANGS.split(','):
        if lang in manual:
            return lang, True
    # 匹配前缀，如手动请求 zh-Hans 但视频提供 zh
    for lang in manual:
        if lang.split('-')[0] in ('zh', 'en'):
            return lang, True

    auto = info.get('automatic_captions') or {}
    if not auto:
        return None
    # 原语言轨道：视频 language 字段 > en > 字典第一个键
    orig = info.get('language') or 'en'
    if orig in auto:
        return orig, False
    first = next(iter(auto))
    return first, False


def fetch_subtitle(info, video_id, url, extra_args, out_dir):
    """下载选定的字幕轨并转纯文本。"""
    picked = pick_lang(info)
    if not picked:
        print('[sub] 该视频无人工字幕也无自动字幕', flush=True)
        return None
    lang, is_manual = picked
    kind = '人工字幕' if is_manual else f'自动字幕（原语言 {lang}）'
    print(f'[sub] 选定: {lang} —— {kind}', flush=True)

    r = run(ytdlp_base_args() + extra_args + [
        '--skip-download',
        '--write-subs' if is_manual else '--write-auto-subs',
        '--sub-langs', lang, '--sub-format', 'vtt/srt/best',
        '--convert-subs', 'vtt',
        '-o', os.path.join(out_dir, 'sub'),
        url,
    ], capture_output=True, text=True)

    sub_files = [f for f in os.listdir(out_dir) if f.endswith('.vtt')]
    if not sub_files:
        return None
    sub_path = os.path.join(out_dir, sub_files[0])
    print(f'[sub] 使用字幕文件: {sub_files[0]}', flush=True)

    return vtt_to_text(sub_path)


def vtt_to_text(path):
    """把 VTT/SRT 转成纯文本：去时间轴、去标签、去重复行（自动字幕回退特性）。"""
    lines, seen, prev = [], set(), None
    timing = re.compile(r'^\d{2}:\d{2}|\-->')
    for raw in open(path, encoding='utf-8', errors='ignore'):
        line = re.sub(r'<[^>]+>', '', raw).strip()
        if (not line or timing.match(line) or line == 'WEBVTT'
                or line.startswith(('Kind:', 'Language:', 'NOTE'))):
            continue
        if line == prev:  # 自动字幕每行重复两次
            continue
        prev = line
        if line in seen and len(line) < 20:  # 短行重复多为噪声，长句可能是自然重复
            continue
        seen.add(line)
        lines.append(line)
    return ' '.join(lines)


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 download_and_chunk.py <YouTube_URL_or_VideoID>')
        sys.exit(1)

    if not shutil.which('yt-dlp'):
        run(['pip3', 'install', '--break-system-packages', '-U', 'yt-dlp'], check=True)

    video_id = extract_video_id(sys.argv[1])
    print(f'[*] Processing {video_id}...', flush=True)

    info, url, extra_args = get_info(video_id)
    title = info.get('title', video_id)
    # 产物目录以视频标题命名（拿不到标题时回退视频 ID）
    out_dir = common.output_dir(OUTPUT_BASE, title, video_id)

    full_text = fetch_subtitle(info, video_id, url, extra_args, out_dir)
    if not full_text:
        print('ERROR: 没找到字幕（无人工字幕、也无自动字幕），请走 ASR 兜底流程。')
        sys.exit(1)

    total = len(full_text)

    verbatim_md = os.path.join(out_dir, '逐字稿.md')
    with open(verbatim_md, 'w', encoding='utf-8') as f:
        f.write(f'# {title}\n\n')
        f.write('> 本逐字稿来源于视频字幕（官方或自动生成）。\n\n---\n\n')
        f.write(full_text)

    summary_md = os.path.join(out_dir, '总结稿.md')
    if not os.path.exists(summary_md):
        with open(summary_md, 'w', encoding='utf-8') as f:
            f.write(f'# {title} —— 内容总结\n\n')
            f.write('<!-- 总结内容将由后续流程生成 -->\n')

    chunks = []
    for i in range(0, total, CHARS_PER_CHUNK):
        idx = i // CHARS_PER_CHUNK
        chunk_file = os.path.join(out_dir, f'{video_id}_chunk_{idx}.txt')
        with open(chunk_file, 'w', encoding='utf-8') as f:
            f.write(full_text[i:i + CHARS_PER_CHUNK])
        chunks.append(chunk_file)

    print(f'[*] Success. Total chunks: {len(chunks)}')
    print('RESULT_JSON:' + json.dumps({
        'video_id': video_id,
        'title': title,
        'total_chars': total,
        'deliverables': {'verbatim': verbatim_md, 'summary': summary_md},
        'chunks': chunks,
    }))


if __name__ == '__main__':
    main()
