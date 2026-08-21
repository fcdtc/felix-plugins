#!/usr/bin/env python3
"""
Bilibili ASR 兜底：视频无 CC 字幕时，下载音频并用 mlx-whisper 转写。

依赖、模型下载、转写、分块交付等公共逻辑见 common.py；
本脚本只负责 Bilibili 特有部分：Cookie 复用、分P选择、音频轨下载。

用法:
    python3 bilibili_asr_fallback.py <BV_ID> [P_NUM]

输出约定与 bilibili_download_and_chunk.py 一致：
    bili_temp/<BV_ID>/ 逐字稿.md / 总结稿.md / <BV_ID>_chunk_*.txt
    RESULT_JSON:{...}
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

COOKIE_FILE = os.path.expanduser('~/.openclaw/workspace/bilibili_cookie.txt')
NETSCAPE_COOKIE = '/tmp/bili_asr_cookies.txt'


def resolve_output_base():
    """输出基目录：优先 BILI_OUTPUT_DIR 环境变量，否则用当前工作目录下的 bili_temp。
    这样可保证产物落在「项目目录」而非 skill 目录。"""
    env = os.environ.get('BILI_OUTPUT_DIR')
    if env:
        return os.path.join(env, 'bili_temp')
    return os.path.join(os.getcwd(), 'bili_temp')


def write_netscape_cookie():
    """把浏览器风格的 Cookie 字符串转成 yt-dlp 能用的 Netscape 格式。"""
    if not os.path.exists(COOKIE_FILE):
        return False
    raw = open(COOKIE_FILE).read().strip()
    parts = [p.strip() for p in raw.split(';') if '=' in p]
    if not parts:
        return False
    lines = ['# Netscape HTTP Cookie File', '']
    for p in parts:
        k, _, v = p.partition('=')
        lines.append(f'.bilibili.com\tTRUE\t/\tFALSE\t0\t{k.strip()}\t{v.strip()}')
    open(NETSCAPE_COOKIE, 'w').write('\n'.join(lines))
    return True


def download_audio(bv_id, p_num=0):
    """用 yt-dlp 下载音频，返回 (音频路径, 标题)。"""
    out_dir = os.path.join(resolve_output_base(), bv_id)
    os.makedirs(out_dir, exist_ok=True)
    url = f'https://www.bilibili.com/video/{bv_id}/'
    # 分P视频：yt-dlp 会把多P当作 playlist，用 --playlist-items 选指定的 P
    page_args = ['--playlist-items', str(p_num)] if p_num > 0 else []

    # 取标题 + 选音频轨（偏好高码率 m4a）
    cookie_args = ['--cookies', NETSCAPE_COOKIE] if write_netscape_cookie() else []
    info = common.run([
        'yt-dlp', '--no-update', *cookie_args,
        '-f', 'ba', '-j', *page_args, url
    ], capture_output=True, text=True)
    title = bv_id
    if info.returncode == 0 and info.stdout.strip():
        try:
            title = json.loads(info.stdout.strip().splitlines()[-1]).get('title', bv_id)
        except Exception:
            pass
    safe_title = common.sanitize_filename(title)

    audio_path = os.path.join(out_dir, f'{safe_title}.m4a')
    if not os.path.exists(audio_path):
        common.run([
            'yt-dlp', '--no-update', *cookie_args,
            '-f', '30280/30232/30216/bestaudio',
            '-o', audio_path,
            *page_args, url
        ], check=True)
    return audio_path, title


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 bilibili_asr_fallback.py <BV_ID> [P_NUM]')
        sys.exit(1)
    bv_id = sys.argv[1]
    p_num = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    lang = 'zh'  # Bilibili 内容以中文为主

    # 1. 依赖检查
    common.ensure_deps()

    # 2. 下载音频
    print(f'[audio] 下载 {bv_id} (P{p_num}) 的音频轨...', flush=True)
    audio_path, title = download_audio(bv_id, p_num)

    # 3. 模型
    print('[model] 确认 large-v3 模型...', flush=True)
    common.ensure_model(lang)

    # 4. 转写
    out_dir = os.path.join(resolve_output_base(), bv_id)
    print('[asr] 转写中（首次较慢，之后同语言会快）...', flush=True)
    txt_path = common.transcribe(audio_path, out_dir, lang)

    # 5. 分块 + 输出
    common.chunk_and_emit(bv_id, title, txt_path, lang, id_field='bv_id')


if __name__ == '__main__':
    main()
