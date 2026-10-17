#!/usr/bin/env python3
"""video-summary 统一入口：选择 CPython 3.12 并复用专用虚拟环境。"""
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
DIRECT_IMPORTS = ('requests', 'bilibili_api', 'aiohttp', 'qrcode', 'yt_dlp', 'yt_dlp_ejs',
                  'mlx_whisper')
SCHEMA_VERSION = 2


def cache_root(env=None):
    env = os.environ if env is None else env
    base = env.get('XDG_CACHE_HOME') or str(Path(env.get('HOME', str(Path.home()))) / '.cache')
    return Path(base).expanduser() / 'felix-skills' / 'video-summary'


def clean_python_env():
    env = os.environ.copy()
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    env['PYTHONNOUSERSITE'] = '1'
    return env


def probe_runtime(executable):
    code = ('import json,platform,sys; print(json.dumps({'
            '"implementation":platform.python_implementation(),'
            '"version":list(sys.version_info[:3]),"cache_tag":sys.implementation.cache_tag or "",'
            '"platform":sys.platform,"machine":platform.machine()}))')
    try:
        result = subprocess.run([str(executable), '-I', '-c', code], capture_output=True,
                                text=True, env=clean_python_env(), timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout.strip())
    except ValueError:
        return None
    version = tuple(payload.get('version', ()))
    if payload.get('implementation') != 'CPython' or version[:2] != (3, 12) \
            or payload.get('platform') != 'darwin' or payload.get('machine') != 'arm64':
        return None
    return {'executable': str(Path(executable).resolve()), 'implementation': 'CPython',
            'version': version, 'cache_tag': payload['cache_tag'],
            'platform': payload['platform'], 'machine': payload['machine']}


def select_runtime(env=None):
    env = os.environ if env is None else env
    configured = env.get('VIDEO_SUMMARY_PYTHON', '').strip()
    candidates = [configured] if configured else [
        sys.executable, shutil.which('python3.12'),
        '/opt/homebrew/bin/python3.12', '/usr/local/bin/python3.12']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            runtime = probe_runtime(candidate)
            if runtime:
                return runtime
    raise SystemExit('video-summary 需要 macOS Apple Silicon 上的 CPython 3.12。请安装 '
                     'python@3.12，或设置 VIDEO_SUMMARY_PYTHON=/path/to/python3.12')


def runtime_identity(requirements=REQUIREMENTS, runtime=None):
    runtime = runtime or select_runtime()
    digest = hashlib.sha256(requirements.read_bytes())
    fields = [runtime['implementation'], '.'.join(map(str, runtime['version'])),
              runtime['cache_tag'], runtime['platform'], runtime['machine']]
    digest.update('\0'.join(fields).encode())
    digest.update(str(SCHEMA_VERSION).encode())
    return digest.hexdigest()[:20], fields


def venv_python(path):
    return Path(path) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


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


def build_environment(stage, runtime_hash, fields, runtime):
    print(f'[env] 首次创建 video-summary 专用环境: {stage}', flush=True)
    env = clean_python_env()
    run_checked([runtime['executable'], '-I', '-m', 'venv', stage], env=env)
    python = venv_python(stage)
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
    runtime = select_runtime()
    runtime_hash, fields = runtime_identity(runtime=runtime)
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
            build_environment(stage, runtime_hash, fields, runtime)
            os.replace(stage, final)
        except BaseException:
            shutil.rmtree(stage, ignore_errors=True)
            raise
    return final


def check_external_tools(action):
    required = ['ffmpeg', 'ffprobe', 'curl'] if action in {'setup', 'bilibili-asr', 'youtube-asr'} \
        else ['ffmpeg'] if action == 'youtube-subtitles' else []
    if action.startswith('youtube-'):
        required.append('deno')
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
