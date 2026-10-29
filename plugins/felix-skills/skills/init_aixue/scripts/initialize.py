#!/usr/bin/env python3
"""生成只补缺失文件的初始化计划；默认预览，--apply 才写入。"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

SKILL = Path(__file__).resolve().parents[1]
TEMPLATES = SKILL / 'references/templates'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return result.stdout.strip()


def validate_config(config, target):
    if not isinstance(config, dict) or config.get('version') != 1:
        raise ValueError('项目配置 version 必须为 1')
    if not isinstance(config.get('coordinator'), dict):
        raise ValueError('项目配置必须包含 coordinator 对象')
    coordinator = config['coordinator'].get('name')
    projects = config.get('projects')
    if not isinstance(projects, list) or not projects:
        raise ValueError('集中式必须明确登记至少一个子仓')
    names = [coordinator]
    identities = {git(target, 'rev-parse', '--path-format=absolute', '--git-common-dir')}
    for project in projects:
        if not isinstance(project, dict):
            raise ValueError('每个子仓登记必须是对象')
        name, path = project.get('name'), project.get('path')
        if not isinstance(path, str) or not path:
            raise ValueError('子仓路径必须明确')
        repo = (target / path).resolve()
        if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
            raise ValueError('子仓路径必须是 Git 仓库根')
        identity = git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir')
        if identity in identities:
            raise ValueError('每个项目必须属于独立 Git 仓库')
        identities.add(identity)
        names.append(name)
    if any(not isinstance(n, str) or not NAME.fullmatch(n) for n in names):
        raise ValueError('项目名无效')
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError('项目名必须唯一，含大小写比较')
    return config.get('development_branch', 'codex/dev')


def content_files(target, mode, branch, config):
    files = {}
    for folder in ('development', 'agents'):
        for source in sorted((TEMPLATES / folder).glob('*.md')):
            files[f'docs/{folder}/{source.name}'] = source.read_text().replace('codex/dev', branch).encode()
    if mode == 'centralized':
        for source in sorted((TEMPLATES / 'centralized').glob('*.md')):
            files[f'docs/development/{source.name}'] = source.read_text().replace('codex/dev', branch).encode()
        files['projects.json'] = (json.dumps(config, ensure_ascii=False, indent=2) + '\n').encode()
        for relative in ('scripts/workspace', 'scripts/workspace.py', 'tests/test_workspace.py'):
            files[relative] = (SKILL / 'assets/centralized' / relative).read_bytes()
        if not (target / '.gitignore').exists():
            files['.gitignore'] = b'/projects.local.json\n'
    title = config['coordinator']['name'] if config else target.name
    navigation = '\n'.join(
        f'- [{p.stem}](docs/development/{p.name})'
        for p in sorted((TEMPLATES / 'development').glob('*.md'))
    )
    if mode == 'centralized':
        navigation += '\n- [工作区](docs/development/workspaces.md)\n- [项目登记](docs/development/projects.md)'
    files['README.md'] = (f'# {title}\n\n## 开发 harness\n\n{navigation}\n\n初始化文件不代表平台已绑定或部署。实际工具链和资源从配置核实。\n').encode()
    if not any((target / name).exists() or (target / name).is_symlink() for name in ('AGENTS.md', 'CLAUDE.md')):
        rules = (
            '# 开发入口\n\n'
            f'日常开发使用 `{branch}`；开发、Git 或 worktree 操作前读取 `docs/development/environment.md`。\n\n'
            '平台操作先读 `docs/development/platform-operations.md`；构建部署读 `backend-service-release.md`，'
            'MIA Agent／Skill 读 `mia-agent-release.md`，RPC 接入读 `mia-rpc-tool-host-release.md`（均在 `docs/development/`）。\n\n'
            '需求和工单读写前读取 `docs/agents/issue-tracker.md`；分诊前读取 `docs/agents/triage-labels.md`；探索代码前读取 `docs/agents/domain.md`。\n'
        )
        if mode == 'centralized':
            rules += '\n跨仓访问前运行 `scripts/workspace check`，只使用 `projects.local.json` 已校验绑定，通过 `path/exec` 定位；绑定失效停止跨仓写入。用户在 Session 前创建完整工作区，Agent 不回退源仓路径。\n'
        files['AGENTS.md'] = rules.encode()
    return files


def initialize(target, mode, branch=None, config=None, apply=False):
    target = target.resolve()
    if Path(git(target, 'rev-parse', '--show-toplevel')).resolve() != target:
        raise ValueError('目标必须是明确 Git 仓库根')
    if mode == 'centralized':
        if config is None:
            config = json.loads((target / 'projects.json').read_text())
        configured_branch = validate_config(config, target)
        if branch and branch != configured_branch:
            raise ValueError('--branch 与集中式项目配置冲突')
        branch = configured_branch
    else:
        if config is not None:
            raise ValueError('独立式不使用集中式项目配置')
        branch = branch or 'codex/dev'
    if not isinstance(branch, str) or not branch:
        raise ValueError('开发分支必须是非空字符串')
    git(target, 'check-ref-format', '--branch', branch)
    files = content_files(target, mode, branch, config)
    plan = []
    for relative, data in files.items():
        path = target / relative
        for parent in path.parents:
            if parent == target:
                break
            if parent.exists() and not parent.is_dir():
                raise ValueError(f'目标父路径不是目录：{relative}')
        if not path.resolve().is_relative_to(target):
            raise ValueError(f'目标经过仓外符号链接：{relative}')
        if path.is_symlink():
            state = 'preserved'
        elif path.exists():
            state = 'unchanged' if path.is_file() and path.read_bytes() == data else 'preserved'
        else:
            state = 'create'
        plan.append({'path': relative, 'state': state})
    notes = ['已有代理入口、README 和 preserved 文件须由 Agent 阅读并增量合并']
    if mode == 'centralized':
        ignore = target / '.gitignore'
        if ignore.exists() and '/projects.local.json' not in ignore.read_text().splitlines():
            notes.append('需增量合并 /projects.local.json 忽略规则')
        if git(target, 'ls-files', '--', 'projects.local.json'):
            raise ValueError('已跟踪 projects.local.json，须先明确处理冲突')
        notes.append('子仓入口由 Agent 按明确授权清单合并；本工具不修改子仓')
    if apply:
        for item in plan:
            if item['state'] != 'create':
                continue
            path = target / item['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as out:
                out.write(files[item['path']])
            if item['path'] == 'scripts/workspace':
                path.chmod(0o755)
    return {'target': str(target), 'mode': mode, 'branch': branch, 'applied': apply, 'files': plan, 'notes': notes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--mode', required=True, choices=('standalone', 'centralized'))
    parser.add_argument('--branch')
    parser.add_argument('--project-config', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        config = json.loads(args.project_config.read_text()) if args.project_config else None
        print(json.dumps(initialize(args.target, args.mode, args.branch, config, args.apply), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        print(f'初始化失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
