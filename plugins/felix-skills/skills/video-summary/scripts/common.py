#!/usr/bin/env python3
"""
公共模块：whisper 初始化、转写、分块交付。Bilibili / YouTube 平台脚本共享。

设计原则：平台脚本只负责「拿音频 + 拿标题」，其余（模型准备、转写、
逐字稿/总结稿占位、分块、RESULT_JSON 输出）全部走这里，保证两条产品线
输出约定完全一致。
"""
import json
import os
import re
import shutil
import subprocess

# ---------- 配置 ----------
HF_MIRROR = 'https://hf-mirror.com'
CHARS_PER_CHUNK = 100000

# 英文转写用 turbo 模型（快且准）；中文用 large-v3
MODELS = {
    'en': 'mlx-community/whisper-turbo',
    'zh': 'mlx-community/whisper-large-v3-mlx',
}
# turbo 用 vocab.json，large-v3 用 vocabulary.json，两个都探测
MODEL_FILE_CANDIDATES = [
    'config.json', 'tokenizer.json', 'vocab.json', 'vocabulary.json', 'weights.npz',
]


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


def ensure_deps():
    """依赖检查：yt-dlp / ffmpeg / mlx-whisper。"""
    ensure_cmd('yt-dlp',
               ['pip3', 'install', '--break-system-packages', '-U', 'yt-dlp'])
    ensure_cmd('ffmpeg', ['brew', 'install', 'ffmpeg'])
    try:
        import mlx_whisper  # noqa
    except ImportError:
        run(['pip3', 'install', '--break-system-packages', 'mlx-whisper'], check=True)


def model_snapshot(model):
    cache = os.path.expanduser(f'~/.cache/huggingface/hub/models--{model.replace("/", "--")}')
    return os.path.join(cache, 'snapshots', 'main')


def model_cache_dir(lang):
    """本地模型目录（含权重则可用），否则 None。"""
    d = model_snapshot(MODELS[lang])
    if os.path.exists(os.path.join(d, 'weights.npz')):
        return d
    return None


def ensure_model(lang):
    """确认模型完整；不完整则走 hf-mirror + curl 逐文件下载。

    为什么不用 huggingface_hub / hf download：
      实测在受限网络下，官方库的多连接 / xet 下载会在大文件处 0B 卡死，
      而 curl 走 hf-mirror.com 单连接能稳定下载（约 3MB/s，~15min 下完 2.9G）。
    """
    model = MODELS[lang]
    snapshot = model_snapshot(model)
    files = [f for f in os.listdir(snapshot) if f != 'weights.npz'] if os.path.isdir(snapshot) else []
    weights = os.path.join(snapshot, 'weights.npz')
    # weights.npz 应 >= 1G；过小说明是残留的 incomplete
    need_weights = not os.path.exists(weights) or os.path.getsize(weights) < 1_000_000_000
    if files and not need_weights and any(f.endswith('.json') for f in files):
        print(f'[model] 已存在完整模型: {snapshot}', flush=True)
        return
    os.makedirs(snapshot, exist_ok=True)
    base = f'{HF_MIRROR}/{model}/resolve/main'
    for f in MODEL_FILE_CANDIDATES:
        dst = os.path.join(snapshot, f)
        if os.path.exists(dst):
            continue
        r = run(['curl', '-L', '--retry', '5', '--retry-delay', '3',
                 '-o', dst, '-w', '%{http_code}', f'{base}/{f}'],
                capture_output=True, text=True)
        code = (r.stdout or '').strip()[-3:]
        if code == '404':
            os.remove(dst)  # 该模型没有这个文件（如 turbo 没有 vocabulary.json）
            print(f'[model] 跳过不存在的 {f}', flush=True)
    run(['curl', '-L', '--retry', '5', '--retry-delay', '3', '-C', '-',
         '-o', weights, f'{base}/weights.npz'], check=True)
    print('[model] 下载完成', flush=True)


def ensure_whisper_ready(lang):
    """一键初始化：依赖 + 模型。平台脚本入口和 setup 脚本都调用它。"""
    ensure_deps()
    ensure_model(lang)


def transcribe(audio_path, out_dir, lang):
    """用 mlx_whisper 转写，返回 txt 路径。"""
    env = os.environ.copy()
    # 即使本地缓存已就绪，设上镜像也无害；模型缺失时走镜像下载
    env['HF_ENDPOINT'] = HF_MIRROR
    # 优先用已下载到本地缓存目录的模型，避免 mlx_whisper 再走 HF Hub 拉取
    local_model = model_cache_dir(lang)
    model_arg = local_model if local_model else MODELS[lang]
    run([
        'mlx_whisper', audio_path,
        '--model', model_arg,
        '--language', lang,
        '--output-dir', out_dir,
        '--output-format', 'txt',
    ], env=env, check=True)
    # mlx_whisper 输出文件名 = 音频名(去扩展) + .txt
    base = os.path.splitext(os.path.basename(audio_path))[0]
    txt = os.path.join(out_dir, f'{base}.txt')
    if not os.path.exists(txt):
        raise RuntimeError(f'转写后未找到输出文件: {txt}')
    return txt


def chunk_and_emit(video_id, title, txt_path, lang, id_field='video_id'):
    """整理最终交付产物，并输出 RESULT_JSON。

    交付两份文件（与字幕主流程输出约定一致）：
      逐字稿.md  —— 完整文字稿（视频标题 + 正文，原文语言保留）
      总结稿.md  —— 由本函数留空占位，调用方（总结子流程）填充内容
    同时保留内部分块文件供子智能体并行总结使用。
    """
    out_dir = os.path.dirname(txt_path)
    full_text = open(txt_path, encoding='utf-8').read()
    total = len(full_text)

    # 1) 逐字稿.md：完整文字稿
    verbatim_md = os.path.join(out_dir, '逐字稿.md')
    with open(verbatim_md, 'w', encoding='utf-8') as f:
        f.write(f'# {title}\n\n')
        f.write(f'> 本逐字稿由 mlx-whisper({MODELS[lang]}) 语音转写生成（语言: {lang}），'
                '原文语言保留，仅供内容参考。\n\n---\n\n')
        f.write(full_text)

    # 2) 总结稿.md：留占位标题，由后续总结流程写入正文
    summary_md = os.path.join(out_dir, '总结稿.md')
    if not os.path.exists(summary_md):
        with open(summary_md, 'w', encoding='utf-8') as f:
            f.write(f'# {title} —— 内容总结\n\n')
            f.write('<!-- 总结内容将由后续流程生成 -->\n')

    # 3) 内部分块（供子智能体并行总结使用）
    chunks = []
    for i in range(0, total, CHARS_PER_CHUNK):
        idx = i // CHARS_PER_CHUNK
        chunk_file = os.path.join(out_dir, f'{video_id}_chunk_{idx}.txt')
        with open(chunk_file, 'w', encoding='utf-8') as f:
            f.write(full_text[i:i + CHARS_PER_CHUNK])
        chunks.append(chunk_file)

    print(f"[ASR] Success. Total chunks: {len(chunks)}", flush=True)
    print('RESULT_JSON:' + json.dumps({
        id_field: video_id,
        'title': title,
        'total_chars': total,
        'source': 'asr',
        'transcript_lang': lang,
        'deliverables': {'verbatim': verbatim_md, 'summary': summary_md},
        'chunks': chunks,
    }), flush=True)
