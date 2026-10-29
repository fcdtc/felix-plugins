#!/usr/bin/env python3
"""配置驱动的集中式工作区管理；仅使用 Python 标准库和 Git。

复制到协调仓 scripts/ 后运行，不支持在 skill assets 中直接使用。
projects.json version=1：coordinator.name、非空 projects[{name,path}]，
可选 development_branch（默认 codex/dev）。manifest version=1 必须含
coordinator_name/development_branch；不兼容缺少这些字段的历史清单。
协调仓所选开发基线必须已提交 projects.json、scripts/workspace(.py)、
包含 projects.local.json 定位规则的 AGENTS.md 或 CLAUDE.md，以及
/projects.local.json 忽略规则；代理入口支持仓内相对符号链接。
"""

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time


COORDINATOR = Path(__file__).resolve().parents[1]
MANIFEST = "workspace.json"
BINDING = "projects.local.json"
PROJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
DEFAULT_DEVELOPMENT_BRANCH = "codex/dev"


def validate_execution_location():
    script = Path(__file__).resolve()
    if script.parent.name != "scripts" or ("skills" in script.parts and "assets" in script.parts):
        raise WorkspaceError("请先将集中式资产复制到协调仓 scripts/，禁止从 skill assets 操作仓库")
    if top_level(COORDINATOR) != COORDINATOR:
        raise WorkspaceError("workspace 必须位于协调仓根目录下的 scripts/ 中")


def validate_branch(branch):
    if (not isinstance(branch, str) or not branch or branch.startswith("-")
            or branch == "HEAD"
            or subprocess.run(["git", "check-ref-format", "refs/heads/" + branch],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).returncode):
        raise WorkspaceError("development_branch 开发分支名无效")
    return branch


def development_branch(config):
    return validate_branch(config.get("development_branch", DEFAULT_DEVELOPMENT_BRANCH))


def canonical_base(ref, branch):
    if ref in (branch, "refs/heads/" + branch):
        return "refs/heads/" + branch
    if ref in ("origin/" + branch, "refs/remotes/origin/" + branch):
        return "refs/remotes/origin/" + branch
    raise WorkspaceError(f"所有项目必须以 {branch} 本地或 origin 分支为开发基线")


class WorkspaceError(Exception):
    pass


def git(repo, *args, allow_failure=False):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if result.returncode and not allow_failure:
        raise WorkspaceError(f"Git 操作失败（{repo}）：{result.stderr.strip()}")
    return result


def baseline_rules(repo, commit):
    def read_rule(path, visited):
        if path in visited:
            raise WorkspaceError("主仓代理入口符号链接成环")
        entry = git(repo, "ls-tree", commit, "--", path).stdout.strip()
        if not entry:
            return ""
        mode, kind, _ = entry.split("\t", 1)[0].split()
        if kind != "blob":
            raise WorkspaceError("主仓代理入口不是文件")
        content = git(repo, "show", commit + ":" + path).stdout
        if mode != "120000":
            return content
        target = content.strip()
        if not target or Path(target).is_absolute():
            raise WorkspaceError("主仓代理入口必须使用仓内相对符号链接")
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        if resolved == ".." or resolved.startswith("../"):
            raise WorkspaceError("主仓代理入口符号链接越界")
        return read_rule(resolved, visited | {path})

    rules = [read_rule(path, set()) for path in ("AGENTS.md", "CLAUDE.md")]
    if not any(BINDING in text for text in rules):
        raise WorkspaceError("主仓基线未包含工作区定位规则；先提交/合入规则或指定包含规则的协调仓基线")


def common_dir(repo):
    return Path(git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()).resolve()


def top_level(repo):
    return Path(git(repo, "rev-parse", "--show-toplevel").stdout.strip()).resolve()


def worktrees(repo):
    records = []
    for block in git(repo, "worktree", "list", "--porcelain", "-z").stdout.split("\0\0"):
        record = {}
        for field in block.split("\0"):
            if field:
                key, _, value = field.partition(" ")
                record[key] = value
        if record:
            records.append(record)
    return records


def read_json(path):
    if path.name in (MANIFEST, BINDING) and path.is_symlink():
        raise WorkspaceError(f"工作区清单或本地绑定不能经过符号链接：{path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceError(f"无法读取配置 {path}：{exc}") from exc


def write_json(path, value):
    # 同目录原子替换；中断不会留下半份绑定。
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def default_root():
    primary = Path(worktrees(COORDINATOR)[0]["worktree"]).resolve()
    return primary.parent / ".workspaces"


def validate_name(name):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise WorkspaceError("工作区名称只允许字母、数字、下划线、连字符，长度 1–64")


@contextmanager
def workspace_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".workspace.lock"
    if lock_path.is_symlink():
        raise WorkspaceError("工作区锁文件不能经过符号链接")
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WorkspaceError("此工作区根目录正在创建工作区或移除工作区，请稍后重试") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def validate_project_config(config):
    try:
        if not isinstance(config, dict) or config["version"] != 1:
            raise WorkspaceError("projects.json 格式无效")
        coordinator = config["coordinator"]["name"]
        projects = config["projects"]
        if not isinstance(projects, list) or not projects:
            raise WorkspaceError("projects.json 至少登记一个服务项目")
        if any(not isinstance(entry, dict) or not isinstance(entry["name"], str)
               or not isinstance(entry["path"], str) or not entry["path"] for entry in projects):
            raise WorkspaceError("projects.json 格式无效")
        names = [coordinator, *(entry["name"] for entry in projects)]
    except (KeyError, TypeError) as exc:
        raise WorkspaceError("projects.json 格式无效") from exc
    if any(not isinstance(name, str) for name in names):
        raise WorkspaceError("projects.json 包含无效项目名")
    if len(names) != len(set(names)):
        raise WorkspaceError("projects.json 必须唯一登记协调仓和各子项目")
    if len({name.casefold() for name in names}) != len(names):
        raise WorkspaceError("projects.json 项目名不能仅有大小写差异")
    if any(not PROJECT_NAME.fullmatch(name) for name in names):
        raise WorkspaceError("projects.json 包含无效项目名")
    development_branch(config)
    return tuple(names), {entry["name"]: entry for entry in projects}


def project_config():
    config = read_json(COORDINATOR / "projects.json")
    names, entries = validate_project_config(config)
    return names, entries, config


def project_config_at(repo, commit):
    result = git(repo, "show", commit + ":projects.json")
    try:
        config = json.loads(result.stdout)
    except ValueError as exc:
        raise WorkspaceError("协调仓基线中的 projects.json 格式无效") from exc
    validate_project_config(config)
    return config


def refs_from_args(values, names, branch):
    result = {}
    for value in values:
        name, separator, ref = value.partition("=")
        if not separator or name not in names or not ref or name in result:
            raise WorkspaceError("--base 使用已登记项目名=Git引用，每个项目最多一次")
        result[name] = canonical_base(ref, branch)
    return result


def development_ref(repo, branch):
    for ref in ("refs/heads/" + branch, "refs/remotes/origin/" + branch):
        if not git(repo, "rev-parse", "--verify", "--quiet", "--end-of-options", ref + "^{commit}", allow_failure=True).returncode:
            return ref
    raise WorkspaceError(f"仓库 {repo} 缺少 {branch} 和 origin/{branch}；请先获取该分支")


def resolve_commit(repo, ref):
    return git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").stdout.strip()


def source_projects(names, entries):
    sources = {names[0]: COORDINATOR}
    if ((COORDINATOR / BINDING).exists() or (COORDINATOR / BINDING).is_symlink()
            or (COORDINATOR.parent / MANIFEST).exists()):
        manifest = load_workspace()
        check_workspace(manifest)
        if set(manifest["repositories"]) != set(names):
            raise WorkspaceError("当前工作区项目集合与 projects.json 不一致；请用户创建新的完整工作区")
        sources.update({name: Path(manifest["repositories"][name]["path"]) for name in names[1:]})
    else:
        sources.update({name: (COORDINATOR / entries[name]["path"]).resolve() for name in names[1:]})
    for name, repo in sources.items():
        if top_level(repo) != repo.resolve():
            raise WorkspaceError(f"{name} 的登记路径不是仓库根目录")
    if len({str(common_dir(repo)) for repo in sources.values()}) != len(names):
        raise WorkspaceError("已登记项目必须属于相互独立的 Git 仓库")
    return sources


def binding_for(manifest):
    repositories = manifest["repositories"]
    return {
        "version": 1,
        "workspace_id": manifest["workspace_id"],
        "workspace_root": manifest["root"],
        "coordinator_name": manifest["coordinator_name"],
        "development_branch": manifest["development_branch"],
        "coordinator": repositories[manifest["coordinator_name"]],
        "projects": [dict(entry, name=name) for name, entry in repositories.items()
                     if name != manifest["coordinator_name"]],
    }


def validate_manifest(manifest, expected_root):
    try:
        if manifest["version"] != 1 or manifest["state"] not in ("preparing", "ready"):
            raise WorkspaceError("工作区版本或状态不支持")
        validate_name(manifest["workspace_id"])
        root = Path(manifest["root"])
        if root != root.resolve() or root != expected_root.resolve():
            raise WorkspaceError("工作区被移动或路径绑定不一致；请恢复原路径")
        if root.name != manifest["workspace_id"]:
            raise WorkspaceError("工作区名称与清单不一致")
        coordinator_name = manifest["coordinator_name"]
        validate_name(coordinator_name)
        branch = validate_branch(manifest["development_branch"])
        repositories = manifest["repositories"]
        if not isinstance(repositories, dict) or coordinator_name not in repositories or len(repositories) < 2:
            raise WorkspaceError("工作区必须包含协调仓和至少一个子仓库")
        if len({name.casefold() for name in repositories}) != len(repositories):
            raise WorkspaceError("工作区项目名不能仅有大小写差异")
        if any(not isinstance(name, str) or not PROJECT_NAME.fullmatch(name) for name in repositories):
            raise WorkspaceError("工作区包含无效项目名")
        common = set()
        for name, entry in repositories.items():
            path = Path(entry["path"])
            if path != root / name or path.resolve() != path:
                raise WorkspaceError(f"{name} 路径越界或经过符号链接")
            source = Path(entry["source"])
            actual_common = common_dir(source)
            if str(actual_common) != entry["git_common_dir"]:
                raise WorkspaceError(f"{name} 的源仓库身份变化")
            common.add(actual_common)
            if not re.fullmatch(r"[0-9a-f]{40,64}", entry["base_commit"]):
                raise WorkspaceError(f"{name} 基线提交无效")
            if entry["branch"] != branch:
                raise WorkspaceError(f"{name} 分支与清单开发分支策略不一致")
            canonical_base(entry["base_ref"], branch)
        if len(common) != len(repositories):
            raise WorkspaceError("工作区仓库身份重复")
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkspaceError("工作区配置格式无效") from exc


def verify_entry(name, entry):
    path = Path(entry["path"])
    registered = [w for w in worktrees(entry["source"]) if Path(w["worktree"]).resolve() == path]
    if len(registered) != 1 or "prunable" in registered[0] or "locked" in registered[0]:
        raise WorkspaceError(f"{name} 不是可用的已登记 worktree")
    if not path.is_dir() or top_level(path) != path or str(common_dir(path)) != entry["git_common_dir"]:
        raise WorkspaceError(f"{name} 的 worktree 路径或仓库身份不匹配")
    branch = git(path, "symbolic-ref", "--quiet", "--short", "HEAD", allow_failure=True)
    if branch.returncode or branch.stdout.strip() != entry["branch"]:
        raise WorkspaceError(f"{name} 当前分支与绑定不一致，要求 {entry['branch']}")
    if git(path, "merge-base", "--is-ancestor", entry["base_commit"], "HEAD", allow_failure=True).returncode:
        raise WorkspaceError(f"{name} 当前提交不再基于登记的起始提交")


def check_policy(manifest, names, config):
    if set(manifest["repositories"]) != set(names):
        raise WorkspaceError("当前工作区项目集合与 projects.json 不一致；请用户创建新的完整工作区")
    if manifest["coordinator_name"] != names[0]:
        raise WorkspaceError("工作区 coordinator 协调仓策略与 projects.json 不一致")
    if manifest["development_branch"] != development_branch(config):
        raise WorkspaceError("工作区 development_branch 开发分支策略与 projects.json 不一致")
    coordinator = manifest["repositories"][names[0]]
    if str(common_dir(COORDINATOR)) != coordinator["git_common_dir"]:
        raise WorkspaceError("当前协调仓与工作区源仓身份不一致")


def check_workspace(manifest):
    validate_manifest(manifest, Path(manifest["root"]))
    names, _, config = project_config()
    check_policy(manifest, names, config)
    if manifest["state"] != "ready":
        raise WorkspaceError("工作区尚未创建完整，请用户重新执行原 create 命令")
    for name, entry in manifest["repositories"].items():
        verify_entry(name, entry)
    binding = read_json(Path(manifest["repositories"][manifest["coordinator_name"]]["path"]) / BINDING)
    if binding != binding_for(manifest):
        raise WorkspaceError("projects.local.json 与工作区记录不一致")


def load_workspace(name=None, root=None):
    if name:
        validate_name(name)
        workspace = (root or default_root()).resolve() / name
    else:
        if root:
            raise WorkspaceError("指定 --root 时也必须指定工作区名称")
        local = read_json(COORDINATOR / BINDING)
        try:
            workspace = Path(local["workspace_root"])
        except (KeyError, TypeError) as exc:
            raise WorkspaceError("projects.local.json 缺少工作区根目录") from exc
    manifest = read_json(workspace / MANIFEST)
    validate_manifest(manifest, workspace)
    if not name and Path(manifest["repositories"][manifest["coordinator_name"]]["path"]) != COORDINATOR:
        raise WorkspaceError("当前主仓与工作区绑定不一致")
    return manifest


def create_workspace(args):
    validate_name(args.name)
    names, entries, config = project_config()
    branch = development_branch(config)
    coordinator_name = names[0]
    if args.branch and args.branch != branch:
        raise WorkspaceError(f"开发分支由配置固定为 {branch}")
    refs = refs_from_args(args.base, names, branch)
    root = (args.root or default_root()).resolve()
    workspace = root / args.name
    sources = source_projects(names, entries)
    coordinator_commit = resolve_commit(sources[coordinator_name], refs.get(coordinator_name, "HEAD"))
    baseline_config = project_config_at(sources[coordinator_name], coordinator_commit)
    if baseline_config != config:
        if coordinator_name in refs:
            raise WorkspaceError("协调仓基线中的 projects.json 与当前正式配置不一致")
        raise WorkspaceError("projects.json 尚未提交；先提交正式项目配置再创建工作区")
    # 工作区必须在所有现有 worktree 之外，避免把工作区放进另一个任务。
    for repo in sources.values():
        for existing in worktrees(repo):
            path = Path(existing["worktree"]).resolve()
            if root == path or path in root.parents:
                raise WorkspaceError(f"工作区根目录位于已有检出内：{path}")
    with workspace_lock(root):
        if workspace.exists():
            manifest = read_json(workspace / MANIFEST)
            validate_manifest(manifest, workspace)
            if set(manifest["repositories"]) != set(names):
                raise WorkspaceError("同名工作区项目集合与 projects.json 不一致；请用户创建新的完整工作区")
            check_policy(manifest, names, config)
            for name, entry in manifest["repositories"].items():
                if common_dir(sources[name]) != Path(entry["git_common_dir"]):
                    raise WorkspaceError(f"同名工作区 {name} 来自其他仓库")
                if entry["branch"] != branch:
                    raise WorkspaceError(f"已有工作区 {name} 使用 {entry['branch']}；删除后从 {branch} 重新创建")
                if args.branch and args.branch != entry["branch"]:
                    raise WorkspaceError(f"开发分支固定为 {branch}")
                if name in refs and resolve_commit(sources[name], refs[name]) != entry["base_commit"]:
                    raise WorkspaceError(f"已有工作区 {name} 的基线不能被重新指定")
            if manifest["state"] == "ready":
                check_workspace(manifest)
                return manifest
        else:
            repositories = {}
            for name, repo in sources.items():
                if git(repo, "check-ref-format", "--branch", branch, allow_failure=True).returncode:
                    raise WorkspaceError("开发分支名无效")
                ref = refs.get(name) or development_ref(repo, branch)
                commit = resolve_commit(repo, ref)
                branch_ref = f"refs/heads/{branch}"
                branch_exists = not git(repo, "show-ref", "--verify", "--quiet", branch_ref, allow_failure=True).returncode
                if branch_exists and resolve_commit(repo, branch) != commit:
                    raise WorkspaceError(f"{name} 本地 {branch} 与所选基线不同；请先核对分支")
                if any(record.get("branch") == branch_ref for record in worktrees(repo)):
                    raise WorkspaceError(f"{name} 的 {branch} 已在其他 worktree 使用；同一开发分支同一时间只能绑定一个工作区")
                repositories[name] = {
                    "path": str(workspace / name), "source": str(common_dir(repo)),
                    "source_checkout": str(repo),
                    "git_common_dir": str(common_dir(repo)), "branch": branch,
                    "base_ref": ref, "base_commit": commit,
                }
            coordinator = repositories[coordinator_name]
            if project_config_at(sources[coordinator_name], coordinator["base_commit"]) != config:
                raise WorkspaceError("协调仓基线中的 projects.json 与当前正式配置不一致")
            # 新 Session 必须能读取工作区定位规则和工具。旧基线不能静默放行。
            baseline_rules(sources[coordinator_name], coordinator["base_commit"])
            for file in ("scripts/workspace", "scripts/workspace.py"):
                git(sources[coordinator_name], "cat-file", "-e", coordinator["base_commit"] + ":" + file)
            ignore = git(sources[coordinator_name], "show", coordinator["base_commit"] + ":.gitignore").stdout
            if "/projects.local.json" not in {line.strip() for line in ignore.splitlines()}:
                raise WorkspaceError("主仓基线缺少 /projects.local.json 忽略规则；先提交该规则")
            if git(sources[coordinator_name], "ls-tree", "--name-only", coordinator["base_commit"], BINDING).stdout.strip():
                raise WorkspaceError("主仓基线已跟踪 projects.local.json；本地绑定必须由创建脚本生成")
            manifest = {"version": 1, "workspace_id": args.name, "root": str(workspace),
                        "coordinator_name": coordinator_name, "development_branch": branch,
                        "state": "preparing", "repositories": repositories}
            workspace.mkdir()
            write_json(workspace / MANIFEST, manifest)
        for name, entry in manifest["repositories"].items():
            destination = Path(entry["path"])
            registered = [w for w in worktrees(entry["source"]) if Path(w["worktree"]).resolve() == destination]
            if registered or destination.exists():
                verify_entry(name, entry)
                continue
            ref = "refs/heads/" + entry["branch"]
            branch_exists = not git(entry["source"], "show-ref", "--verify", "--quiet", ref, allow_failure=True).returncode
            if branch_exists:
                if resolve_commit(entry["source"], ref) != entry["base_commit"]:
                    raise WorkspaceError(f"{name} 遗留分支不在记录的基线；保留现场，请人工检查")
                if any(record.get("branch") == ref for record in worktrees(entry["source"])):
                    raise WorkspaceError(f"{name} 的 {entry['branch']} 已在其他 worktree 使用；请先关闭该工作区")
                git(entry["source"], "worktree", "add", str(destination), entry["branch"])
            else:
                git(entry["source"], "worktree", "add", "-b", entry["branch"], str(destination), entry["base_commit"])
            verify_entry(name, entry)
        coordinator_path = Path(manifest["repositories"][manifest["coordinator_name"]]["path"])
        if git(coordinator_path, "check-ignore", "--quiet", BINDING, allow_failure=True).returncode:
            raise WorkspaceError("主仓基线未忽略 projects.local.json；保留工作区，先补齐忽略规则")
        binding = binding_for(manifest)
        if (coordinator_path / BINDING).exists() and read_json(coordinator_path / BINDING) != binding:
            raise WorkspaceError("已有本地绑定与创建记录不一致；保留文件，请人工检查")
        write_json(coordinator_path / BINDING, binding)
        manifest["state"] = "ready"
        write_json(workspace / MANIFEST, manifest)
        check_workspace(manifest)
        return manifest


def remove_workspace(args):
    validate_name(args.name)
    root = (args.root or default_root()).resolve()
    workspace = root / args.name
    with workspace_lock(root):
        remove_workspace_locked(args, root, workspace)


def remove_workspace_locked(args, root, workspace):
    manifest_path = workspace / MANIFEST
    if not manifest_path.is_file():
        raise WorkspaceError(f"工作区不存在：{workspace}")
    manifest = read_json(manifest_path)
    validate_manifest(manifest, workspace)
    if manifest["workspace_id"] != args.name:
        raise WorkspaceError("工作区名称与清单不一致")
    names, _, config = project_config()
    check_policy(manifest, names, config)
    binding_path = Path(manifest["repositories"][manifest["coordinator_name"]]["path"]) / BINDING
    if binding_path.exists() or binding_path.is_symlink():
        if read_json(binding_path) != binding_for(manifest):
            raise WorkspaceError("projects.local.json 与工作区记录不一致；停止删除")
    allowed_children = {MANIFEST, *manifest["repositories"]}
    extra_children = [child for child in workspace.iterdir() if child.name not in allowed_children]

    entries = manifest["repositories"]
    worktree_paths = {}
    ignored_by_project = {}
    for name, entry in entries.items():
        source = Path(entry["source"])
        path = Path(entry["path"])
        if path != workspace / name or not path.is_absolute():
            raise WorkspaceError(f"{name} worktree 路径与清单不一致")
        records = [w for w in worktrees(source) if Path(w["worktree"]).resolve() == path]
        if path.exists():
            if len(records) != 1 or top_level(path) != path or str(common_dir(path)) != entry["git_common_dir"]:
                raise WorkspaceError(f"{name} worktree 身份异常；停止删除并人工检查")
            verify_entry(name, entry)
            status = git(path, "status", "--porcelain", "--untracked-files=all").stdout
            if status:
                raise WorkspaceError(f"{name} 有未提交或未跟踪文件；请先提交、备份或清理后重试")
            ignored = git(path, "ls-files", "--others", "--ignored", "--exclude-standard", "-z").stdout.split("\0")
            ignored_by_project[name] = [item for item in ignored if item]
            worktree_paths[name] = (source, path)
        elif records:
            raise WorkspaceError(f"{name} worktree 已登记但目录不存在；请先人工恢复或修复 Git 记录")
        else:
            ignored_by_project[name] = []

    ignored_count = sum(len(items) for items in ignored_by_project.values())
    archive = None
    if ignored_count or extra_children:
        archive_root = root / ".workspace-archive"
        if archive_root.is_symlink() or archive_root.resolve() != archive_root:
            raise WorkspaceError("备份目录不能经过符号链接")
        archive_root.mkdir(parents=True, exist_ok=True)
        archive = archive_root / f"{args.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        if archive.exists():
            raise WorkspaceError(f"备份目录已存在：{archive}")
        staging = Path(tempfile.mkdtemp(prefix=f".{args.name}-", dir=archive_root))
        try:
            for name, ignored in ignored_by_project.items():
                source_path = Path(entries[name]["path"])
                for relative in ignored:
                    relative_path = Path(relative)
                    if relative_path.is_absolute() or ".." in relative_path.parts:
                        raise WorkspaceError(f"{name} 忽略文件路径越界")
                    original = source_path / relative_path
                    # 文件自身可为符号链接（原样备份），父目录不可越界。
                    if not original.parent.resolve().is_relative_to(source_path):
                        raise WorkspaceError(f"{name} 忽略文件父目录经过越界符号链接")
                    destination = staging / name / relative_path
                    if not original.exists() and not original.is_symlink():
                        continue
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if original.is_dir() and not original.is_symlink():
                        shutil.copytree(original, destination, symlinks=True, dirs_exist_ok=True)
                    else:
                        shutil.copy2(original, destination, follow_symlinks=False)
            for extra in extra_children:
                destination = staging / "_workspace" / extra.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                if extra.is_dir() and not extra.is_symlink():
                    shutil.copytree(extra, destination, symlinks=True)
                else:
                    shutil.copy2(extra, destination, follow_symlinks=False)
            os.replace(staging, archive)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    removed = []
    try:
        for name, (source, path) in worktree_paths.items():
            git(source, "worktree", "remove", "--force", str(path))
            removed.append(name)
        shutil.rmtree(workspace)
    except Exception as exc:
        detail = f"；忽略文件备份位于 {archive}" if archive else ""
        raise WorkspaceError(f"工作区仅部分移除，已移除项目：{', '.join(removed) or '无'}{detail}；请检查 Git worktree 状态后重试：{exc}") from exc

    if archive:
        print(f"忽略文件和清单外内容已备份：{archive}")
    print(f"工作区已移除：{workspace}；本地 Git 分支保留")


def add_selection(parser, positional=False):
    if positional:
        parser.add_argument("name", nargs="?", help="省略时使用当前主仓绑定")
    else:
        parser.add_argument("--workspace", dest="name", help="从默认目录选择工作区")
    parser.add_argument("--root", type=Path, help="工作区集合的根目录")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("create", help="用户创建/继续创建已登记项目工作区")
    create.add_argument("name")
    create.add_argument("--root", type=Path)
    create.add_argument("--branch", help="兼容参数，只允许 projects.json 的 development_branch 配置值")
    create.add_argument("--base", action="append", default=[], help="项目名=配置开发分支；默认优先本地分支，否则 origin 同名分支")
    remove = commands.add_parser("remove", help="检查并移除工作区；自动备份忽略文件，保留本地分支")
    remove.add_argument("name")
    remove.add_argument("--root", type=Path, help="工作区集合的根目录")
    check = commands.add_parser("check", help="核验各仓路径、仓库、分支和绑定")
    add_selection(check, positional=True)
    check.add_argument("--json", action="store_true")
    path = commands.add_parser("path", help="输出已校验的项目绝对路径")
    add_selection(path)
    path.add_argument("project")
    execute = commands.add_parser("exec", help="在已校验的项目目录执行命令")
    add_selection(execute)
    execute.add_argument("project")
    execute.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        validate_execution_location()
        if args.action == "create":
            manifest = create_workspace(args)
            print(f"项目工作区已就绪：{manifest['root']}")
            for name, entry in manifest["repositories"].items():
                print(f"  {name}: {entry['branch']}，起始提交 {entry['base_commit'][:12]}")
            print("请在以下目录开启 Agent Session：")
            print(manifest["repositories"][manifest["coordinator_name"]]["path"])
            return 0
        if args.action == "remove":
            remove_workspace(args)
            return 0
        manifest = load_workspace(args.name, args.root)
        check_workspace(manifest)
        if args.action == "check":
            print(json.dumps(manifest, ensure_ascii=False, indent=2) if args.json else f"项目工作区校验通过：{manifest['workspace_id']}")
        elif args.project not in manifest["repositories"]:
            raise WorkspaceError(f"项目未在当前工作区登记：{args.project}")
        elif args.action == "path":
            print(manifest["repositories"][args.project]["path"])
        else:
            command = args.command
            if command and command[0] == "--":
                command = command[1:]
            if not command:
                raise WorkspaceError("exec 需要命令，例如 exec api -- go test ./...")
            # 不通过 shell 拼接命令；每次执行前重新校验，显式固定 cwd。
            return subprocess.run(command, cwd=manifest["repositories"][args.project]["path"]).returncode
        return 0
    except (WorkspaceError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"工作区操作失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
