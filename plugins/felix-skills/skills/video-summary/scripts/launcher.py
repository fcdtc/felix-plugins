#!/usr/bin/env python3
"""video-summary 的统一入口：创建并复用专用虚拟环境。"""
import fcntl
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = SKILL_DIR / 'requirements' / 'runtime.lock'
ACTIONS = {
    'setup': 'setup_whisper.py',
    'bilibili-subtitles': 'bilibili_download_and_chunk.py',
    'bilibili-asr': 'bilibili_asr_fallback.py',
    'bilibili-cheese': 'bilibili_cheese_downloader.py',
    'youtube-subtitles': 'youtube_download_and_chunk.py',
    'youtube-asr': 'youtube_asr_fallback.py',
    'validate-delivery': 'validate_delivery.py',
}
DIRECT_IMPORTS = ('requests', 'bilibili_api', 'aiohttp', 'qrcode', 'yt_dlp', 'mlx_whisper')
SCHEMA_VERSION = 1


def cache_root(env=None):
    env = os.environ if env is None else env
    base = env.get('XDG_CACHE_HOME') or str(Path(env.get('HOME', str(Path.home()))) / '.cache')
    return Path(base).expanduser() / 'felix-skills' / 'video-summary'


def runtime_identity(requirements=REQUIREMENTS):
    digest = hashlib.sha256(requirements.read_bytes())
    fields = [platform.python_implementation(), platform.python_version(),
              sys.implementation.cache_tag or '', sys.platform, platform.machine()]
    digest.update('\0'.join(fields).encode())
    return digest.hexdigest()[:20], fields


def venv_python(path):
    return Path(path) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def clean_python_env():
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['PYTHONNOUSERSITE'] = '1'
    return env


def environment_smoke(python):
    code = '; '.join(f'import {name}' for name in DIRECT_IMPORTS)
    try:
        result = subprocess.run([str(python), '-I', '-c', code], timeout=60,
                                env=clean_python_env(), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def ready_payload(runtime_hash, fields):
    return {'schema_version': SCHEMA_VERSION, 'runtime_hash': runtime_hash, 'runtime': fields,
            'requirements_sha256': hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()}


def is_ready(path, runtime_hash):
    marker = Path(path) / '.ready.json'
    python = venv_python(path)
    if not marker.is_file() or not python.is_file():
        return False
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return (payload.get('schema_version') == SCHEMA_VERSION
            and payload.get('runtime_hash') == runtime_hash
            and environment_smoke(python))


def run_checked(args, **kwargs):
    print('[env] ' + ' '.join(map(str, args)), flush=True)
    subprocess.run([str(item) for item in args], check=True, **kwargs)


def build_environment(stage, runtime_hash, fields):
    print(f'[env] 首次创建 video-summary 专用环境: {stage}', flush=True)
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(stage)
    python = venv_python(stage)
    env = clean_python_env()
    run_checked([python, '-I', '-m', 'pip', 'install', '--disable-pip-version-check',
                 '-r', REQUIREMENTS], env=env)
    run_checked([python, '-I', '-m', 'pip', 'check'], env=env)
    if not environment_smoke(python):
        raise RuntimeError('专用环境依赖导入失败')
    marker = Path(stage) / '.ready.json'
    with marker.open('w', encoding='utf-8') as handle:
        json.dump(ready_payload(runtime_hash, fields), handle, ensure_ascii=False, indent=2)
        handle.flush(); os.fsync(handle.fileno())


def ensure_environment(root=None):
    root = cache_root() if root is None else Path(root)
    runtime_hash, fields = runtime_identity()
    final = root / 'venvs' / runtime_hash
    if is_ready(final, runtime_hash):
        return final
    (root / 'venvs').mkdir(parents=True, exist_ok=True)
    (root / 'locks').mkdir(parents=True, exist_ok=True)
    (root / 'staging').mkdir(parents=True, exist_ok=True)
    with (root / 'locks' / f'{runtime_hash}.lock').open('a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if is_ready(final, runtime_hash):
            return final
        if final.exists():
            raise SystemExit(f'专用环境损坏: {final}。请确认没有任务运行后删除该目录再重试。')
        stage = Path(tempfile.mkdtemp(prefix=f'{runtime_hash}.', dir=root / 'staging'))
        try:
            build_environment(stage, runtime_hash, fields)
            os.replace(stage, final)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return final


def check_external_tools(action):
    required = ['ffmpeg', 'ffprobe', 'curl'] if action in {'setup', 'bilibili-asr', 'youtube-asr'} \
        else ['ffmpeg'] if action == 'youtube-subtitles' else []
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        raise SystemExit('缺少外部命令: ' + ', '.join(missing) + '。请先安装后重试。')


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ACTIONS:
        raise SystemExit('用法: launcher.py <' + '|'.join(ACTIONS) + '> [args...]')
    action, *args = argv
    check_external_tools(action)
    environment = ensure_environment()
    python = venv_python(environment)
    env = clean_python_env()
    env['VIDEO_SUMMARY_VENV'] = str(environment)
    env['PATH'] = str(python.parent) + os.pathsep + env.get('PATH', '')
    return subprocess.run([str(python), str(SKILL_DIR / 'scripts' / ACTIONS[action]), *args],
                          env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
