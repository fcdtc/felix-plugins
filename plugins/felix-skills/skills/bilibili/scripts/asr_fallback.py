#!/usr/bin/env python3
"""
ASR 兜底脚本：当 Bilibili 视频没有 CC 字幕时，下载音频并用 mlx-whisper 转写。

完整流程（已被实测验证）：
  1. yt-dlp 下载音频轨（m4a，无需会员的清晰度即可）
  2. 安装 mlx-whisper（如未安装）
  3. 下载 large-v3 模型（用 hf-mirror 镜像 + curl 逐文件下载，
     绕开 huggingface_hub / hf-cli 在国内网络下大文件 0B 卡死的问题）
  4. mlx_whisper 转写为 txt
  5. 按 download_and_chunk.py 的约定分块并输出 RESULT_JSON

用法:
    python3 asr_fallback.py <BV_ID> [P_NUM]

输出约定与 download_and_chunk.py 一致：
    bili_temp/<BV_ID>/<BV_ID>_chunk_0.txt ...
    RESULT_JSON:{...}
"""
import os
import re
import sys
import json
import shutil
import subprocess

# ---------- 配置 ----------
CHARS_PER_CHUNK = 100000
COOKIE_FILE = os.path.expanduser('~/.openclaw/workspace/bilibili_cookie.txt')
NETSCAPE_COOKIE = '/tmp/bili_asr_cookies.txt'
MODEL = 'mlx-community/whisper-large-v3-mlx'
MODEL_CACHE = os.path.expanduser(
    '~/.cache/huggingface/hub/models--mlx-community--whisper-large-v3-mlx'
)
MODEL_SNAPSHOT = os.path.join(MODEL_CACHE, 'snapshots', 'main')
MODEL_FILES = ['config.json', 'tokenizer.json', 'vocabulary.json', 'weights.npz']
HF_MIRROR = 'https://hf-mirror.com'

def resolve_output_base():
    """输出基目录：优先 BILI_OUTPUT_DIR 环境变量，否则用当前工作目录下的 bili_temp。
    这样可保证产物落在「项目目录」而非 skill 目录。"""
    env = os.environ.get('BILI_OUTPUT_DIR')
    if env:
        return os.path.join(env, 'bili_temp')
    return os.path.join(os.getcwd(), 'bili_temp')


OUTPUT_BASE = resolve_output_base()


def run(cmd, **kw):
    """执行命令，实时输出。"""
    print(f"[cmd] {' '.join(cmd) if isinstance(cmd, list) else cmd}", flush=True)
    kw.setdefault('check', False)
    return subprocess.run(cmd, **kw)


def ensure_cmd(cmd, install_cmd):
    """确保某命令可用，否则安装。"""
    if shutil.which(cmd):
        return
    print(f"[setup] 未找到 {cmd}，安装中...", flush=True)
    run(install_cmd, check=True)


def sanitize_filename(name):
    return re.sub(r'[\\/:*?"<>|]', '_', name)


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
    out_dir = os.path.join(OUTPUT_BASE, bv_id)
    os.makedirs(out_dir, exist_ok=True)
    url = f'https://www.bilibili.com/video/{bv_id}/'
    # 分P视频：yt-dlp 会把多P当作 playlist，用 --playlist-items 选指定的 P
    page_args = ['--playlist-items', str(p_num)] if p_num > 0 else []

    # 取标题 + 选音频轨（偏好高码率 m4a）
    cookie_args = ['--cookies', NETSCAPE_COOKIE] if write_netscape_cookie() else []
    info = run([
        'yt-dlp', '--no-update', *cookie_args,
        '-f', 'ba', '-j', *page_args, url
    ], capture_output=True, text=True)
    title = bv_id
    if info.returncode == 0 and info.stdout.strip():
        try:
            title = json.loads(info.stdout.strip().splitlines()[-1]).get('title', bv_id)
        except Exception:
            pass
    safe_title = sanitize_filename(title)

    audio_path = os.path.join(out_dir, f'{safe_title}.m4a')
    if not os.path.exists(audio_path):
        run([
            'yt-dlp', '--no-update', *cookie_args,
            '-f', '30280/30232/30216/bestaudio',
            '-o', audio_path,
            *page_args, url
        ], check=True)
    return audio_path, title


def ensure_model():
    """确保 large-v3 模型已下载完整；若不完整则用 hf-mirror + curl 逐文件下载。

    为什么不用 huggingface_hub / hf download：
      实测在受限网络下，官方库的多连接/ xet 下载会在大文件处 0B 卡死，
      而 curl 走 hf-mirror.com 单连接能稳定下载（约 3MB/s，~15min 下完 2.9G）。
    """
    if all(os.path.exists(os.path.join(MODEL_SNAPSHOT, f)) for f in MODEL_FILES):
        weights = os.path.join(MODEL_SNAPSHOT, 'weights.npz')
        # weights.npz 应约 2.9G；过小说明是残留的 incomplete
        if os.path.exists(weights) and os.path.getsize(weights) > 2_000_000_000:
            print(f'[model] 已存在完整模型: {MODEL_SNAPSHOT}', flush=True)
            return
        print('[model] weights.npz 体积异常，重新下载...', flush=True)

    os.makedirs(MODEL_SNAPSHOT, exist_ok=True)
    base = f'{HF_MIRROR}/mlx-community/whisper-large-v3-mlx/resolve/main'
    for f in MODEL_FILES:
        dst = os.path.join(MODEL_SNAPSHOT, f)
        if os.path.exists(dst) and f != 'weights.npz':
            continue
        # -C - 断点续传；--retry 重试
        run(['curl', '-L', '--retry', '5', '--retry-delay', '3', '-C', '-',
             '-o', dst, f'{base}/{f}'], check=True)
    print('[model] 全部下载完成', flush=True)


def transcribe(audio_path, out_dir):
    """用 mlx_whisper 转写，返回 txt 路径。"""
    env = os.environ.copy()
    # 即使本地缓存已就绪，设上镜像也无害；模型缺失时走镜像下载
    env['HF_ENDPOINT'] = HF_MIRROR
    r = run([
        'mlx_whisper', audio_path,
        '--model', MODEL,
        '--language', 'zh',
        '--output-dir', out_dir,
        '--output-format', 'txt',
    ], env=env, check=True)
    # mlx_whisper 输出文件名 = 音频名(去扩展) + .txt
    base = os.path.splitext(os.path.basename(audio_path))[0]
    txt = os.path.join(out_dir, f'{base}.txt')
    if not os.path.exists(txt):
        raise RuntimeError(f'转写后未找到输出文件: {txt}')
    return txt


def chunk_and_emit(bv_id, title, txt_path):
    """整理最终交付产物，并输出 RESULT_JSON。

    交付两份文件（均放在 bili_temp/<BV_ID>/ 下）：
      逐字稿.md  —— 完整文字稿（视频标题 + 正文）
      总结稿.md  —— 由本脚本留空占位，调用方（总结子流程）填充内容
    同时保留原始 txt 供分块总结使用。
    """
    out_dir = os.path.dirname(txt_path)
    full_text = open(txt_path, encoding='utf-8').read()
    total = len(full_text)

    # 1) 逐字稿.md：完整文字稿
    verbatim_md = os.path.join(out_dir, '逐字稿.md')
    with open(verbatim_md, 'w', encoding='utf-8') as f:
        f.write(f'# {title}\n\n')
        f.write('> 本逐字稿由 mlx-whisper(large-v3) 语音转写生成，仅供内容参考。\n\n---\n\n')
        f.write(full_text)

    # 2) 总结稿.md：留占位标题，由后续总结流程写入正文
    summary_md = os.path.join(out_dir, '总结稿.md')
    if not os.path.exists(summary_md):
        with open(summary_md, 'w', encoding='utf-8') as f:
            f.write(f'# {title} —— 内容总结\n\n')
            f.write('<!-- 总结内容将由后续流程生成 -->\n')

    # 3) 内部分块（供子智能体并行总结使用），约定同 download_and_chunk.py
    chunks = []
    for i in range(0, total, CHARS_PER_CHUNK):
        idx = i // CHARS_PER_CHUNK
        chunk_file = os.path.join(out_dir, f'{bv_id}_chunk_{idx}.txt')
        with open(chunk_file, 'w', encoding='utf-8') as f:
            f.write(full_text[i:i + CHARS_PER_CHUNK])
        chunks.append(chunk_file)

    print(f"[ASR] Success. Total chunks: {len(chunks)}", flush=True)
    print("RESULT_JSON:" + json.dumps({
        "bv_id": bv_id,
        "title": title,
        "total_chars": total,
        "source": "asr",
        "deliverables": {
            "verbatim": verbatim_md,
            "summary": summary_md,
        },
        "chunks": chunks,
    }), flush=True)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 asr_fallback.py <BV_ID> [P_NUM]")
        sys.exit(1)
    bv_id = sys.argv[1]
    p_num = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    # 1. 依赖检查
    ensure_cmd('yt-dlp',
               ['pip3', 'install', '--break-system-packages', '-U', 'yt-dlp'])
    ensure_cmd('ffmpeg', ['brew', 'install', 'ffmpeg'])
    # mlx_whisper 是 python 模块，单独处理
    try:
        import mlx_whisper  # noqa
    except ImportError:
        run(['pip3', 'install', '--break-system-packages', 'mlx-whisper'], check=True)

    # 2. 下载音频
    print(f'[audio] 下载 {bv_id} (P{p_num}) 的音频轨...', flush=True)
    audio_path, title = download_audio(bv_id, p_num)

    # 3. 模型
    print('[model] 确认 large-v3 模型...', flush=True)
    ensure_model()

    # 4. 转写
    out_dir = os.path.join(OUTPUT_BASE, bv_id)
    print(f'[asr] 转写中（首次较慢，之后同语言会快）...', flush=True)
    txt_path = transcribe(audio_path, out_dir)

    # 5. 分块 + 输出
    chunk_and_emit(bv_id, title, txt_path)


if __name__ == '__main__':
    main()
