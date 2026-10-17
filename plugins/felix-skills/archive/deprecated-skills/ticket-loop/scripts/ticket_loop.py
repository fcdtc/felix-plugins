#!/usr/bin/env python3
"""Deterministic planning and single-ticket execution for Ticket Loop."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time
from typing import Iterable
import uuid


SCHEMA_VERSION = "1.0"
LEDGER_SCHEMA_VERSION = "1.1"
MANIFEST_SCHEMA_VERSION = "1.1"
LEGACY_SCHEMA_VERSION = "1.0"
TERMINAL_GIT_ERRORS = {
    "repository-drift",
    "branch-drift",
    "history-rewritten",
    "git-operation-in-progress",
    "run-lock-missing",
    "run-lock-mismatch",
    "recovery-context-drift",
    "frontier-empty",
    "external-blocker-modified",
    "max-cost-reached",
    "cost-unknown",
    "claude-session-mismatch",
}
MAX_PHASE_RESUMES = 2
DEFAULT_IMPLEMENTATION_TIMEOUT_SECONDS = 90 * 60
DEFAULT_CLOSEOUT_TIMEOUT_SECONDS = 30 * 60
DEFAULT_RESUME_TIMEOUT_SECONDS = 30 * 60
PERMISSION_MODE = "bypassPermissions"
UNATTENDED_CONTEXT_VERSION = "ticket-loop-unattended-v1"
UNATTENDED_CONTEXT = """这是由 Ticket Loop 启动的无人值守 Task Session。工单、引用规格中的 Testing Decisions、验收标准和既有公共测试边界视为用户已预先同意的实施范围与测试 seam。对于未明确列出、但可从既有公共接口、相邻测试惯例和验收标准确定的纯本地、可逆、低风险测试 seam，采用推荐默认，记录假设并继续，不要等待用户确认。用户已授权完成当前工单所需的本地源码、测试、工单状态修改和本地提交。仅当决策不可逆、涉及凭据或生产环境、产生网络发布等外部副作用、需要 push 或创建 PR、改写 Git 历史、超范围删除，或不同选择会实质改变交付结果且无法安全回退时，停止并清楚报告。不得开始其他工单。"""
INTERRUPT_GRACE_SECONDS = 1.0
INCOMPLETE_STATES = {"running", "interrupted", "terminal-failure"}
FILENAME_RE = re.compile(r"^(?P<id>0[1-9]|[1-9]\d)-.+\.md$")
FIELD_RE = re.compile(
    r"^\s*(?:\*\*)?(?P<name>What to build|Blocked by|Status|Type)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
WAYFINDER_TYPES = {"research", "prototype", "grilling", "task"}
WAYFINDER_STATUSES = {"claimed", "resolved"}
BLOCKER_RE = re.compile(r"^\s*(\d+)(?:\s*(?:/|[-–—])\s*\S.*)?\s*$")
NO_BLOCKERS_RE = re.compile(
    r"^\s*None(?:\s+(?:\([^\n]*\)|[-–—/]\s*[^\s.!?。！？](?:[^\n]*?[^\s.!?。！？])?))?[.!?。！？]?\s*$",
    re.IGNORECASE,
)
DONE_RE = re.compile(r"^done\s*\([^()]+\)$", re.IGNORECASE)
DONE_DETAILS_RE = re.compile(
    r"^done\s*\(\s*(\d{4}-\d{2}-\d{2}),\s*([0-9a-fA-F]{7,40})\s*\)$", re.IGNORECASE
)
CHECKLIST_RE = re.compile(r"^\s*-\s*\[(?P<mark>[ xX])\]\s+\S", re.MULTILINE)
EVIDENCE_HEADING_RE = re.compile(r"^##\s+Completion evidence\s*$", re.MULTILINE | re.IGNORECASE)
EVIDENCE_ITEM_RE = re.compile(
    r"^\s*-\s*(Implementation|Typecheck|Tests|Review)\s*:\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
FAILURE_WORD_RE = re.compile(r"\b(fail(?:ed|ure)?|error|broken)\b", re.IGNORECASE)
NOT_APPLICABLE_RE = re.compile(r"^Not applicable\s*\([^()]+\)$", re.IGNORECASE)
CLOSEOUT_PROMPT = """Close out the current Task Ticket only. Verify every acceptance checklist item and mark only satisfied items complete. Set Status to done (<today>, <implementation commit>). Add exactly one ## Completion evidence section with non-empty Implementation, Typecheck, Tests, and Review entries; any Not applicable entry must include a reason in parentheses. Commit any remaining closeout changes with message chore(ticket-loop): close <ticket-id>. If no changes remain, do not create an empty commit. Do not amend, rebase, rewrite history, push, create a PR, switch branches, or start another ticket."""


class PlanError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


class ControlledInterrupt(Exception):
    def __init__(self, signum: int):
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


@dataclass(frozen=True)
class Ticket:
    ticket_id: str
    path: Path
    kind: str
    status: str
    blocked_by: tuple[str, ...]

    @property
    def done(self) -> bool:
        if self.kind == "task":
            return bool(DONE_RE.fullmatch(self.status))
        return self.status.lower() == "resolved"


def normalized_id(value: str) -> str:
    return str(int(value)).zfill(len(value))


def ticket_id_from_path(path: Path) -> str:
    match = FILENAME_RE.fullmatch(path.name)
    if not match:
        raise PlanError(
            "invalid-ticket-filename",
            f"工单文件名不符合 <编号>-<标题>.md 约定: {path}",
        )
    return normalized_id(match.group("id"))


def extract_fields(text: str, path: Path) -> dict[str, str]:
    values: dict[str, list[str]] = {
        "what to build": [],
        "blocked by": [],
        "status": [],
        "type": [],
    }
    header = text.split("\n## Comments", 1)[0]
    for line in header.splitlines():
        match = FIELD_RE.fullmatch(line)
        if match:
            values[match.group("name").lower()].append(match.group("value").strip())

    for name, occurrences in values.items():
        if len(occurrences) > 1:
            raise PlanError("duplicate-field", f"字段 {name} 在工单中重复: {path}")
    return {name: occurrences[0] for name, occurrences in values.items() if occurrences}


def parse_blockers(raw: str, path: Path) -> tuple[str, ...]:
    if NO_BLOCKERS_RE.fullmatch(raw):
        return ()
    if not raw.strip():
        raise PlanError("invalid-blocked-by", f"Blocked by 字段为空: {path}")
    if re.match(r"^\s*None\b", raw, re.IGNORECASE):
        raise PlanError(
            "invalid-blocked-by",
            "Blocked by 的 None 格式无效；请使用 None、"
            "None (can start immediately). 或编号列表 01, 02，且 None 不能与编号 blocker 混用: "
            f"{path}",
        )

    blockers: list[str] = []
    for part in re.split(r"[,，;；]", raw):
        match = BLOCKER_RE.match(part)
        if not match:
            raise PlanError("invalid-blocked-by", f"无法解析 blocker: {part.strip()} ({path})")
        blocker = normalized_id(match.group(1))
        if blocker in blockers:
            raise PlanError("duplicate-blocker", f"重复 blocker {blocker}: {path}")
        blockers.append(blocker)
    return tuple(blockers)


def parse_ticket(path: Path, *, require_task: bool = False) -> Ticket:
    if not path.is_file():
        raise PlanError("ticket-not-found", f"工单不存在: {path}")
    ticket_id = ticket_id_from_path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as error:
        raise PlanError("invalid-ticket-encoding", f"工单不是 UTF-8: {path}") from error
    except OSError as error:
        raise PlanError("ticket-read-failed", f"无法读取工单: {path}: {error}") from error

    fields = extract_fields(text, path)
    ticket_type = fields.get("type")
    if ticket_type is not None and ticket_type.lower() not in WAYFINDER_TYPES:
        raise PlanError("invalid-wayfinder-type", f"Wayfinder Ticket 类型无效: {ticket_type} ({path})")
    kind = "task" if ticket_type is None else "wayfinder"
    if require_task and kind != "task":
        raise PlanError("invalid-task-ticket", f"所选文件是 Wayfinder Ticket: {path}")

    if kind == "task":
        required = {"what to build", "blocked by", "status"}
        if not required.issubset(fields) or not fields.get("what to build"):
            raise PlanError("invalid-task-ticket", f"Task Ticket 缺少必要字段: {path}")
        status = fields["status"]
        if status != "ready-for-agent" and not DONE_RE.fullmatch(status):
            raise PlanError("invalid-status", f"Task Ticket 状态无效: {status} ({path})")
    else:
        if "status" not in fields or "blocked by" not in fields:
            raise PlanError("invalid-wayfinder-ticket", f"Wayfinder Ticket 缺少必要字段: {path}")
        status = fields["status"]
        if status.lower() not in WAYFINDER_STATUSES:
            raise PlanError("invalid-wayfinder-status", f"Wayfinder Ticket 状态无效: {status} ({path})")

    return Ticket(ticket_id, path.resolve(), kind, status, parse_blockers(fields["blocked by"], path))


def markdown_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise PlanError("issues-directory-not-found", f"issues 目录不存在: {directory}")
    return sorted(
        (path for path in directory.iterdir() if path.is_file() and FILENAME_RE.fullmatch(path.name)),
        key=lambda path: (int(FILENAME_RE.fullmatch(path.name).group("id")), path.name),
    )


def parse_range(raw: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", raw)
    if not match or int(match.group(1)) > int(match.group(2)):
        raise PlanError("invalid-range", f"编号范围无效: {raw}")
    return int(match.group(1)), int(match.group(2))


def ensure_unique(tickets: Iterable[Ticket]) -> dict[str, Ticket]:
    by_id: dict[str, Ticket] = {}
    for ticket in tickets:
        if ticket.ticket_id in by_id:
            raise PlanError(
                "duplicate-ticket-id",
                f"编号 {ticket.ticket_id} 同时用于 {by_id[ticket.ticket_id].path} 和 {ticket.path}",
            )
        by_id[ticket.ticket_id] = ticket
    return by_id


def dependency_cycle(selected: dict[str, Ticket]) -> list[str] | None:
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(ticket_id: str) -> list[str] | None:
        if ticket_id in visiting:
            start = visiting.index(ticket_id)
            return visiting[start:] + [ticket_id]
        if ticket_id in visited:
            return None
        visiting.append(ticket_id)
        for blocker in selected[ticket_id].blocked_by:
            if blocker in selected:
                cycle = visit(blocker)
                if cycle:
                    return cycle
        visiting.pop()
        visited.add(ticket_id)
        return None

    for ticket_id in selected:
        cycle = visit(ticket_id)
        if cycle:
            return cycle
    return None


def build_plan(
    issues_directory: Path,
    selected_paths: list[Path],
    *,
    explicit_selection: bool,
) -> dict[str, object]:
    domain_paths = markdown_files(issues_directory)
    domain = ensure_unique(parse_ticket(path) for path in domain_paths)
    selected_tickets = [
        ticket
        for path in selected_paths
        for ticket in [parse_ticket(path, require_task=explicit_selection)]
        if ticket.kind == "task"
    ]
    selected = ensure_unique(selected_tickets)

    for ticket in selected.values():
        for blocker in ticket.blocked_by:
            if blocker == ticket.ticket_id:
                raise PlanError("self-dependency", f"工单 {ticket.ticket_id} 依赖自身")
            if blocker not in domain:
                raise PlanError("missing-blocker", f"工单 {ticket.ticket_id} 的 blocker {blocker} 不存在")

    reachable: dict[str, Ticket] = {}

    def include_dependencies(ticket_id: str) -> None:
        if ticket_id in reachable:
            return
        reachable[ticket_id] = domain[ticket_id]
        for blocker in domain[ticket_id].blocked_by:
            if blocker not in domain:
                raise PlanError("missing-blocker", f"工单 {ticket_id} 的 blocker {blocker} 不存在")
            if blocker == ticket_id:
                raise PlanError("self-dependency", f"工单 {ticket_id} 依赖自身")
            include_dependencies(blocker)

    for ticket_id in selected:
        include_dependencies(ticket_id)

    cycle = dependency_cycle(reachable)
    if cycle:
        raise PlanError("dependency-cycle", "依赖环: " + " -> ".join(cycle))

    if not selected:
        raise PlanError("empty-manifest", "没有选中任何 Task Ticket")

    ordered = sorted(selected.values(), key=lambda ticket: (int(ticket.ticket_id), ticket.path.name))
    frontier = next(
        (
            ticket.ticket_id
            for ticket in ordered
            if ticket.status == "ready-for-agent"
            and all(blocker not in selected or selected[blocker].done for blocker in ticket.blocked_by)
            and all(blocker in selected or domain[blocker].done for blocker in ticket.blocked_by)
        ),
        None,
    )
    external_blockers = {
        ticket_id: {
            "id": ticket.ticket_id,
            "path": str(ticket.path),
            "kind": ticket.kind,
            "status": ticket.status,
            "sha256": hashlib.sha256(ticket.path.read_bytes()).hexdigest(),
        }
        for ticket_id, ticket in reachable.items()
        if ticket_id not in selected
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "issues_directory": str(issues_directory.resolve()),
        "tickets": [
            {
                "id": ticket.ticket_id,
                "path": str(ticket.path),
                "status": ticket.status,
                "blocked_by": list(ticket.blocked_by),
            }
            for ticket in ordered
        ],
        "external_blockers": external_blockers,
        "frontier": frontier,
    }


def plan_command(args: argparse.Namespace) -> dict[str, object]:
    if args.tickets:
        selected_paths = [Path(path).expanduser().resolve() for path in args.tickets]
        parents = {path.parent for path in selected_paths}
        if len(parents) != 1:
            raise PlanError("multiple-issues-directories", "显式工单必须位于同一个 issues 目录")
        issues_directory = parents.pop()
        if args.issues_dir or args.ticket_range:
            raise PlanError("invalid-selection", "显式工单不能与 issues 目录或编号范围组合")
    else:
        if not args.issues_dir:
            raise PlanError("invalid-selection", "必须提供 --issues-dir 或 --tickets")
        issues_directory = Path(args.issues_dir).expanduser().resolve()
        selected_paths = markdown_files(issues_directory)
        if args.ticket_range:
            start, end = parse_range(args.ticket_range)
            selected_paths = [
                path
                for path in selected_paths
                if start <= int(FILENAME_RE.fullmatch(path.name).group("id")) <= end
            ]

    if not selected_paths:
        raise PlanError("empty-manifest", "没有选中任何工单")
    return build_plan(issues_directory, selected_paths, explicit_selection=bool(args.tickets))


def run_git(repository: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        text=True,
        capture_output=True,
    )
    if check and result.returncode:
        raise PlanError("git-command-failed", result.stderr.strip() or "Git 命令失败")
    return result


def repository_location(cwd: Path) -> tuple[Path, Path]:
    root_result = run_git(cwd, "rev-parse", "--show-toplevel", check=False)
    if root_result.returncode:
        raise PlanError("not-a-git-repository", "当前目录不在 Git 仓库中")
    root = Path(root_result.stdout.strip()).resolve()
    common_raw = run_git(root, "rev-parse", "--git-common-dir").stdout.strip()
    common = Path(common_raw)
    if not common.is_absolute():
        common = (root / common).resolve()
    return root, common


def repository_identity(cwd: Path) -> tuple[Path, Path, str, str]:
    root, common = repository_location(cwd)
    branch = run_git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if branch.returncode or not branch.stdout.strip():
        raise PlanError("detached-head", "必须在命名分支上启动 Loop Run")
    head = run_git(root, "rev-parse", "HEAD").stdout.strip()
    return root, common, branch.stdout.strip(), head


def ensure_no_git_operation(repository: Path) -> None:
    git_dir = Path(run_git(repository, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repository / git_dir).resolve()
    operations = {
        "merge": "MERGE_HEAD",
        "cherry-pick": "CHERRY_PICK_HEAD",
        "revert": "REVERT_HEAD",
        "rebase": "rebase-merge",
        "rebase-apply": "rebase-apply",
    }
    active = [name for name, marker in operations.items() if (git_dir / marker).exists()]
    if active:
        raise PlanError("git-operation-in-progress", f"存在未完成的 Git 操作: {', '.join(active)}")


def worktree_status(repository: Path) -> list[str]:
    return run_git(
        repository,
        "-c",
        "core.quotepath=false",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout.splitlines()


def ensure_clean(repository: Path) -> None:
    ensure_no_git_operation(repository)
    if worktree_status(repository):
        raise PlanError("dirty-worktree", "工作区、index 或 untracked 文件不干净")


def ensure_repository_matches(ledger: dict[str, object]) -> tuple[Path, str]:
    try:
        repository, common, branch, head = repository_identity(Path.cwd())
    except PlanError as error:
        if error.code == "detached-head":
            raise PlanError("branch-drift", "当前分支与 Run Ledger 不一致") from error
        raise
    if str(repository) != ledger["repository"] or str(common) != ledger["git_common_directory"]:
        raise PlanError("repository-drift", "当前 Git 仓库与 Run Ledger 不一致")
    if branch != ledger["branch"]:
        raise PlanError("branch-drift", "当前分支与 Run Ledger 不一致")
    return repository, head


def ledger_path(repository: Path, run_id: str) -> Path:
    return repository / ".ticket-loop" / "runs" / f"{run_id}.json"


def lock_path(common: Path) -> Path:
    return common / "ticket-loop" / "lock.json"


def command_lock_path(common: Path) -> Path:
    return common / "ticket-loop" / "command.lock"


@contextmanager
def command_lock(common: Path, *, blocking: bool = False):
    path = command_lock_path(common)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        operation = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
        try:
            fcntl.flock(descriptor, operation)
        except BlockingIOError as error:
            raise PlanError("run-active", "Loop Run 正在执行另一个 mutation 命令") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def acquire_run_lock(common: Path, lock: dict[str, object]) -> None:
    path = lock_path(common)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(lock, ensure_ascii=False, indent=2) + "\n").encode()
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise PlanError("repository-locked", f"仓库已有活动 Loop Run: {path}") from error
    try:
        os.write(descriptor, payload)
    except BaseException:
        os.close(descriptor)
        path.unlink(missing_ok=True)
        raise
    os.close(descriptor)


def read_run_lock(common: Path) -> dict[str, object]:
    path = lock_path(common)
    try:
        lock = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PlanError("run-lock-missing", "Loop Run 的仓库锁不存在") from error
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError("run-lock-mismatch", "Loop Run 的仓库锁无效") from error
    if not isinstance(lock, dict):
        raise PlanError("run-lock-mismatch", "Loop Run 的仓库锁无效")
    return lock


def ensure_run_lock(ledger: dict[str, object]) -> None:
    lock = read_run_lock(Path(str(ledger["git_common_directory"])))
    if lock.get("run_id") != ledger["run_id"] or lock.get("lock_id") != ledger.get("lock_id"):
        raise PlanError("run-lock-mismatch", "仓库锁不属于当前 Loop Run")


def set_lock_activity(
    ledger: dict[str, object],
    pid: int | None,
    *,
    child_pid: int | None = None,
    child_pgid: int | None = None,
) -> None:
    common = Path(str(ledger["git_common_directory"]))
    lock = read_run_lock(common)
    if lock.get("run_id") != ledger["run_id"] or lock.get("lock_id") != ledger.get("lock_id"):
        raise PlanError("run-lock-mismatch", "仓库锁不属于当前 Loop Run")
    lock["active_pid"] = pid
    lock["child_pid"] = child_pid
    lock["child_pgid"] = child_pgid
    path = lock_path(common)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def set_lock_active_pid(ledger: dict[str, object], pid: int | None) -> None:
    set_lock_activity(ledger, pid)


def release_run_lock(ledger: dict[str, object]) -> None:
    path = lock_path(Path(str(ledger["git_common_directory"])))
    try:
        lock = read_run_lock(Path(str(ledger["git_common_directory"])))
    except PlanError as error:
        if error.code == "run-lock-missing":
            return
        raise
    same_identity = lock.get("run_id") == ledger["run_id"]
    if ledger.get("schema_version") != LEGACY_SCHEMA_VERSION:
        same_identity = same_identity and lock.get("lock_id") == ledger.get("lock_id")
    if not same_identity:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        return


def write_ledger(path: Path, ledger: dict[str, object]) -> None:
    ledger.pop("_legacy", None)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        payload = (json.dumps(ledger, ensure_ascii=False, indent=2) + "\n").encode()
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def incomplete_run_ids(repository: Path) -> list[str]:
    directory = repository / ".ticket-loop" / "runs"
    if not directory.is_dir():
        return []
    candidates: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            ledger = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            isinstance(ledger, dict)
            and ledger.get("schema_version") == LEDGER_SCHEMA_VERSION
            and ledger.get("run_id") == path.stem
            and ledger.get("state") in INCOMPLETE_STATES
        ):
            candidates.append(path.stem)
    return candidates


def resolve_run_id(run_id: str | None) -> str:
    if run_id:
        return run_id
    repository, _ = repository_location(Path.cwd())
    candidates = incomplete_run_ids(repository)
    if not candidates:
        raise PlanError("run-not-found", "当前仓库没有未完成的 Loop Run")
    if len(candidates) > 1:
        commands = ", ".join(f"--run {candidate}" for candidate in candidates)
        raise PlanError("multiple-incomplete-runs", f"存在多个未完成 Loop Run，请精确指定: {commands}")
    return candidates[0]


def validate_manifest(manifest: object, path: Path, *, legacy: bool = False) -> None:
    expected_version = LEGACY_SCHEMA_VERSION if legacy else MANIFEST_SCHEMA_VERSION
    if not isinstance(manifest, dict) or manifest.get("schema_version") != expected_version:
        raise PlanError("invalid-run-ledger", f"Run Manifest 不兼容: {path}")
    runtime = manifest.get("runtime")
    if not isinstance(runtime, dict):
        raise PlanError("invalid-run-ledger", f"Run Manifest 缺少冻结运行配置: {path}")
    required = {"implement_command", "model", "permission_mode", "max_cost_usd", "timeouts"}
    if not legacy:
        required.add("unattended_context_version")
    if (
        set(runtime) != required
        or runtime.get("permission_mode") != PERMISSION_MODE
        or (not legacy and runtime.get("unattended_context_version") != UNATTENDED_CONTEXT_VERSION)
    ):
        raise PlanError("invalid-run-ledger", f"Run Manifest 运行配置不兼容: {path}")
    command = runtime.get("implement_command")
    model = runtime.get("model")
    maximum = runtime.get("max_cost_usd")
    timeouts = runtime.get("timeouts")
    if (
        not isinstance(command, str)
        or not command
        or (model is not None and not isinstance(model, str))
        or (
            maximum is not None
            and (
                not isinstance(maximum, (int, float))
                or isinstance(maximum, bool)
                or not math.isfinite(float(maximum))
                or maximum <= 0
            )
        )
        or not isinstance(timeouts, dict)
        or set(timeouts) != {"implementation", "closeout", "resume"}
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or value <= 0
            for value in timeouts.values()
        )
    ):
        raise PlanError("invalid-run-ledger", f"Run Manifest 运行配置无效: {path}")


def load_ledger(run_id: str | None) -> tuple[Path, dict[str, object]]:
    run_id = resolve_run_id(run_id)
    repository, _ = repository_location(Path.cwd())
    path = ledger_path(repository, run_id)
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PlanError("run-not-found", f"Loop Run 不存在: {run_id}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError("invalid-run-ledger", f"无法读取 Run Ledger: {path}") from error
    if not isinstance(ledger, dict):
        raise PlanError("invalid-run-ledger", f"Run Ledger 不兼容: {path}")
    version = ledger.get("schema_version")
    if version not in {LEDGER_SCHEMA_VERSION, LEGACY_SCHEMA_VERSION} or ledger.get("run_id") != run_id:
        raise PlanError("invalid-run-ledger", f"Run Ledger 不兼容: {path}")
    validate_manifest(ledger.get("manifest"), path, legacy=version == LEGACY_SCHEMA_VERSION)
    ledger["_legacy"] = version == LEGACY_SCHEMA_VERSION
    return path, ledger


def maintain_local_exclude(repository: Path, common: Path) -> None:
    tracked = run_git(repository, "ls-files", "--error-unmatch", ".ticket-loop", check=False)
    tracked_children = run_git(repository, "ls-files", ".ticket-loop").stdout.strip()
    if tracked.returncode == 0 or tracked_children:
        raise PlanError("tracked-run-directory", ".ticket-loop 已被 Git 跟踪，拒绝占用")
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    content = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
    if ".ticket-loop/" not in content.splitlines():
        separator = "" if not content or content.endswith("\n") else "\n"
        exclude.write_text(content + separator + ".ticket-loop/\n", encoding="utf-8")


def allowed_actions(ledger: dict[str, object]) -> list[str]:
    state = ledger["state"]
    if ledger.get("_legacy"):
        if state in {"completed", "aborted"}:
            return ["status", "clean"]
        return ["status", "abort"]
    if state == "completed":
        return ["status", "clean"]
    if state == "aborted":
        return ["status", "clean"]
    if state in {"interrupted", "terminal-failure"}:
        if ledger.get("terminal_reason") == "claude-session-mismatch":
            return ["status", "abort"]
        return ["reopen", "status", "abort"]
    if ledger.get("recovery"):
        return ["resume", "status", "abort"]
    return ["step", "status", "abort"]


def public_run(ledger: dict[str, object], command: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "command": command,
        "run_id": ledger["run_id"],
        "state": ledger["state"],
        "phase": ledger["phase"],
        "active_ticket": ledger["active_ticket"],
        "session_id": ledger["session_id"],
        "allowed_actions": allowed_actions(ledger),
        "git": {
            "repository": ledger["repository"],
            "branch": ledger["branch"],
            "run_start_head": ledger["run_start_head"],
            "ticket_start_head": ledger["ticket_start_head"],
            "implementation_head": ledger.get("implementation_head"),
        },
        "attempts": ledger.get("attempts", {}),
        "claude_calls": ledger.get("claude_calls", []),
        "cost": ledger.get("cost", {"total_usd": 0.0, "unknown": True, "max_usd": None}),
        "gate_failures": ledger.get("gate_failures", []),
    }


def blocker_satisfied(
    blocker: str,
    completed: set[str],
    external: dict[str, dict[str, object]],
) -> bool:
    if blocker in completed:
        return True
    dependency = external.get(blocker)
    if dependency is None:
        return False
    if dependency["kind"] == "wayfinder":
        return str(dependency["status"]).lower() == "resolved"
    return bool(DONE_RE.fullmatch(str(dependency["status"])))


def runnable_ticket(ledger: dict[str, object]) -> dict[str, object] | None:
    completed = set(ledger.get("completed_tickets", []))
    external = ledger["manifest"].get("external_blockers", {})
    return next(
        (
            ticket
            for ticket in ledger["manifest"]["tickets"]
            if ticket["id"] not in completed
            and ticket["status"] == "ready-for-agent"
            and all(blocker_satisfied(blocker, completed, external) for blocker in ticket["blocked_by"])
        ),
        None,
    )


def blocked_manifest_details(ledger: dict[str, object]) -> list[dict[str, object]]:
    completed = set(ledger.get("completed_tickets", []))
    external = ledger["manifest"].get("external_blockers", {})
    return [
        {
            "ticket": ticket["id"],
            "unsatisfied_blockers": [
                blocker
                for blocker in ticket["blocked_by"]
                if not blocker_satisfied(blocker, completed, external)
            ],
        }
        for ticket in ledger["manifest"]["tickets"]
        if ticket["id"] not in completed
    ]


def ensure_external_blockers_unchanged(ledger: dict[str, object]) -> None:
    for blocker, dependency in ledger["manifest"].get("external_blockers", {}).items():
        path = Path(str(dependency["path"]))
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise PlanError("external-blocker-modified", f"Manifest 外 blocker {blocker} 无法读取") from error
        repository = Path(str(ledger["repository"]))
        relative_path = str(path.relative_to(repository))
        touched_commits = run_git(
            repository,
            "rev-list",
            f"{ledger['run_start_head']}..HEAD",
            "--",
            relative_path,
        ).stdout.splitlines()
        if digest != dependency["sha256"] or touched_commits:
            raise PlanError("external-blocker-modified", f"Manifest 外 blocker {blocker} 在运行期间被修改")


def activate_ticket(ledger: dict[str, object], ticket: dict[str, object], head: str) -> None:
    ticket_path = Path(str(ticket["path"]))
    session_id = str(uuid.uuid4())
    ledger.update(
        {
            "phase": "implementation",
            "active_ticket": ticket["id"],
            "ticket_path": str(ticket_path),
            "ticket_start_head": head,
            "implementation_head": None,
            "implementation_prompt": f"/{ledger['manifest']['runtime']['implement_command']} @{ticket_path}",
            "closeout_prompt": CLOSEOUT_PROMPT.replace("<today>", date.today().isoformat()).replace(
                "<ticket-id>", str(ticket["id"])
            ),
            "session_id": session_id,
            "attempts": {
                "implementation": {"initial": 0, "resumes": 0},
                "closeout": {"initial": 0, "resumes": 0},
            },
            "recovery": None,
            "gate_failures": [],
        }
    )
    ledger.setdefault("task_sessions", {})[str(ticket["id"])] = session_id


def advance_after_closeout(ledger: dict[str, object], head: str) -> None:
    completed = ledger.setdefault("completed_tickets", [])
    completed.append(ledger["active_ticket"])
    unfinished = [
        ticket
        for ticket in ledger["manifest"]["tickets"]
        if ticket["status"] == "ready-for-agent" and ticket["id"] not in completed
    ]
    if not unfinished:
        ledger["state"] = "completed"
        ledger["phase"] = None
        return
    ticket = runnable_ticket(ledger)
    if ticket is None:
        details = blocked_manifest_details(ledger)
        ledger["active_ticket"] = None
        ledger["phase"] = None
        raise PlanError(
            "frontier-empty",
            "Manifest 仍有未完成工单，但没有满足依赖的可执行 Frontier: "
            + json.dumps(details, ensure_ascii=False, separators=(",", ":")),
        )
    activate_ticket(ledger, ticket, head)


def plugin_name(plugin_root: Path) -> str | None:
    metadata = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    name = payload.get("name") if isinstance(payload, dict) else None
    return name if isinstance(name, str) and name else None


def detect_implement_command() -> str:
    roots: list[Path] = []
    configured = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if configured:
        roots.append(Path(configured).expanduser().resolve())
    installed = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        payload = json.loads(installed.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    plugins = payload.get("plugins", {}) if isinstance(payload, dict) else {}
    if isinstance(plugins, dict):
        for installations in plugins.values():
            if not isinstance(installations, list):
                continue
            for installation in installations:
                path = installation.get("installPath") if isinstance(installation, dict) else None
                if isinstance(path, str):
                    roots.append(Path(path).expanduser().resolve())

    candidates: list[str] = []
    for root in roots:
        name = plugin_name(root)
        if not name:
            continue
        for skill in root.glob("skills/**/SKILL.md"):
            if skill.parent.name == "implement":
                candidates.append(f"{name}:implement")
    return sorted(set(candidates))[0] if candidates else "implement"


def resolve_implement_command(raw: str | None) -> str:
    command = (raw or detect_implement_command()).lstrip("/")
    if not command or any(character.isspace() for character in command):
        raise PlanError("invalid-implement-command", "实现命令必须是单个 slash command 名称")
    return command


def start_command_locked(args: argparse.Namespace) -> dict[str, object]:
    repository, common, branch, head = repository_identity(Path.cwd())
    ensure_clean(repository)
    manifest = plan_command(args)
    for ticket in manifest["tickets"]:
        try:
            Path(str(ticket["path"])).relative_to(repository)
        except ValueError as error:
            raise PlanError("ticket-outside-repository", "Task Ticket 必须位于当前仓库中") from error

    maintain_local_exclude(repository, common)
    run_id = str(uuid.uuid4())
    implement_command = resolve_implement_command(args.implement_command)
    runtime = {
        "implement_command": implement_command,
        "model": args.model,
        "permission_mode": PERMISSION_MODE,
        "unattended_context_version": UNATTENDED_CONTEXT_VERSION,
        "max_cost_usd": args.max_cost_usd,
        "timeouts": {
            "implementation": args.implementation_timeout_seconds,
            "closeout": args.closeout_timeout_seconds,
            "resume": args.resume_timeout_seconds,
        },
    }
    manifest["runtime"] = runtime
    ledger: dict[str, object] = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "run_id": run_id,
        "lock_id": str(uuid.uuid4()),
        "state": "running",
        "phase": "implementation",
        "repository": str(repository),
        "git_common_directory": str(common),
        "branch": branch,
        "run_start_head": head,
        "ticket_start_head": None,
        "implementation_head": None,
        "manifest": manifest,
        "completed_tickets": [
            ticket["id"]
            for ticket in manifest["tickets"]
            if DONE_RE.fullmatch(str(ticket["status"]))
        ],
        "task_sessions": {},
        "active_ticket": None,
        "ticket_path": None,
        "implementation_prompt": None,
        "closeout_prompt": None,
        "session_id": None,
        "claude_executable": str(Path(args.claude_executable).expanduser().resolve()) if os.sep in args.claude_executable else args.claude_executable,
        "attempts": {
            "implementation": {"initial": 0, "resumes": 0},
            "closeout": {"initial": 0, "resumes": 0},
        },
        "recovery": None,
        "in_flight": None,
        "claude_calls": [],
        "cost": {"total_usd": 0.0, "unknown": False, "max_usd": args.max_cost_usd},
        "gate_failures": [],
    }
    first_ticket = runnable_ticket(ledger)
    if first_ticket is None:
        raise PlanError(
            "frontier-empty",
            "Manifest 当前没有满足依赖的可执行 Frontier: "
            + json.dumps(blocked_manifest_details(ledger), ensure_ascii=False, separators=(",", ":")),
        )
    activate_ticket(ledger, first_ticket, head)
    acquire_run_lock(common, lifecycle_lock_payload(ledger))
    path = ledger_path(repository, run_id)
    try:
        write_ledger(path, ledger)
    except BaseException:
        release_run_lock(ledger)
        raise
    return public_run(ledger, "start")


def start_command(args: argparse.Namespace) -> dict[str, object]:
    _, common = repository_location(Path.cwd())
    with command_lock(common):
        return start_command_locked(args)


def persist_claude_output(
    ledger: dict[str, object],
    phase: str,
    invocation: str,
    timeout_seconds: float,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    timed_out: bool,
) -> tuple[dict[str, object], Path]:
    call_number = len(ledger["claude_calls"]) + 1
    directory = Path(str(ledger["repository"])) / ".ticket-loop" / "logs" / str(ledger["run_id"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_path = directory / f"{call_number:02d}-{phase}.json"
    stderr_path = directory / f"{call_number:02d}-{phase}.stderr.log"
    raw_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    os.chmod(raw_path, 0o600)
    os.chmod(stderr_path, 0o600)
    call: dict[str, object] = {
        "phase": phase,
        "invocation": invocation,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "log_path": str(raw_path),
        "stderr_log_path": str(stderr_path),
        "result": None,
    }
    ledger["claude_calls"].append(call)
    return call, raw_path


def runtime_config(ledger: dict[str, object]) -> dict[str, object]:
    return ledger["manifest"]["runtime"]


def ensure_cost_allows_call(ledger: dict[str, object]) -> None:
    maximum = runtime_config(ledger).get("max_cost_usd")
    if maximum is None:
        return
    cost = ledger.get("cost")
    if not isinstance(cost, dict) or cost.get("unknown") is True:
        raise PlanError("cost-unknown", "累计费用未知，无法在成本上限下安全启动下一次 Claude 调用")
    total = cost.get("total_usd")
    if not isinstance(total, (int, float)) or isinstance(total, bool):
        raise PlanError("cost-unknown", "累计费用无效，无法在成本上限下安全启动下一次 Claude 调用")
    if float(total) >= float(maximum):
        raise PlanError("max-cost-reached", f"累计费用 ${float(total):g} 已达到上限 ${float(maximum):g}")


def child_launcher() -> int:
    control_fd = int(sys.argv[2])
    command = sys.argv[3:]
    try:
        allowed = os.read(control_fd, 1)
    finally:
        os.close(control_fd)
    if allowed != b"1":
        return 125
    try:
        os.execvp(command[0], command)
    except OSError as error:
        print(f"ticket-loop-exec-failed: {error}", file=sys.stderr)
        return 126


def invoke_claude(
    ledger: dict[str, object],
    phase: str,
    prompt: str,
    *,
    resume: bool,
    recovery_attempt: bool,
    timeout_seconds: float,
) -> dict[str, object]:
    ensure_cost_allows_call(ledger)
    command = [
        str(ledger["claude_executable"]),
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        PERMISSION_MODE,
        "--dangerously-skip-permissions",
        "--append-system-prompt",
        UNATTENDED_CONTEXT,
    ]
    model = runtime_config(ledger).get("model")
    if model:
        command.extend(["--model", str(model)])
    command.extend(["--resume" if resume else "--session-id", str(ledger["session_id"])])
    command.append(prompt)
    invocation = "resume" if resume else "initial"
    control_read, control_write = os.pipe()
    launcher_command = [sys.executable, str(Path(__file__).resolve()), "__child-launcher", str(control_read), *command]
    try:
        process = subprocess.Popen(
            launcher_command,
            cwd=str(ledger["repository"]),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            pass_fds=(control_read,),
        )
    except OSError as error:
        os.close(control_read)
        os.close(control_write)
        ledger["in_flight"] = None
        write_ledger(ledger_path(Path(str(ledger["repository"])), str(ledger["run_id"])), ledger)
        raise PlanError("claude-launch-failed", f"无法启动 Claude launcher: {error}") from error
    os.close(control_read)
    pgid = os.getpgid(process.pid)
    attempts = ledger["attempts"][phase]
    attempts["resumes" if recovery_attempt else "initial"] += 1
    in_flight = ledger.get("in_flight")
    if isinstance(in_flight, dict):
        in_flight.update({
            "state": "spawned",
            "child_pid": process.pid,
            "child_pgid": pgid,
            "spawned_at": datetime.now(timezone.utc).isoformat(),
        })
    set_lock_activity(ledger, os.getpid(), child_pid=process.pid, child_pgid=pgid)
    write_ledger(ledger_path(Path(str(ledger["repository"])), str(ledger["run_id"])), ledger)
    try:
        os.write(control_write, b"1")
    finally:
        os.close(control_write)

    def note_interrupt(signum: int, _frame: object) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        raise ControlledInterrupt(signum)

    previous_handlers = {
        signum: signal.signal(signum, note_interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except ControlledInterrupt as interrupt:
        try:
            stdout, stderr = process.communicate(timeout=INTERRUPT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        call, raw_path = persist_claude_output(
            ledger, phase, invocation, timeout_seconds, stdout, stderr, process.returncode, False
        )
        call["interrupted_by"] = signal.Signals(interrupt.signum).name
        raise PlanError(
            "run-interrupted",
            f"Loop Run 收到 {call['interrupted_by']}；日志: {raw_path}",
            exit_code=130,
        ) from interrupt
    except subprocess.TimeoutExpired as error:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=INTERRUPT_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        _, raw_path = persist_claude_output(
            ledger, phase, invocation, timeout_seconds, stdout, stderr, None, True
        )
        raise PlanError("claude-timeout", f"Claude 执行超过 {timeout_seconds:g} 秒；日志: {raw_path}") from error
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)

    call, raw_path = persist_claude_output(
        ledger,
        phase,
        invocation,
        timeout_seconds,
        stdout,
        stderr,
        process.returncode,
        False,
    )
    if process.returncode == 126 and stderr.startswith("ticket-loop-exec-failed:"):
        attempts = ledger["attempts"][phase]
        counter = "resumes" if recovery_attempt else "initial"
        attempts[counter] = max(0, int(attempts[counter]) - 1)
        ledger["in_flight"] = None
        raise PlanError("claude-launch-failed", f"无法启动 Claude；日志: {raw_path}")
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise PlanError("invalid-claude-json", f"Claude 未返回有效 JSON；日志: {raw_path}") from error
    if not isinstance(parsed, dict):
        raise PlanError("invalid-claude-json", f"Claude JSON 顶层必须是对象；日志: {raw_path}")
    active_ticket = ledger.get("active_ticket")
    sessions = ledger.get("task_sessions")
    expected_session = ledger.get("session_id")
    ticket_session = sessions.get(active_ticket) if isinstance(sessions, dict) else None
    reported_session = parsed.get("session_id")
    call["expected_session_id"] = expected_session
    call["reported_session_id"] = reported_session
    if (
        not isinstance(reported_session, str)
        or not reported_session
        or reported_session != expected_session
        or reported_session != ticket_session
    ):
        raise PlanError(
            "claude-session-mismatch",
            f"Claude 返回的 Session ID 与当前 Task Session 不一致；日志: {raw_path}",
        )
    call["result"] = parsed
    cost = ledger.setdefault("cost", {"total_usd": 0.0, "unknown": False})
    reported_cost = parsed.get("total_cost_usd")
    if isinstance(reported_cost, (int, float)) and not isinstance(reported_cost, bool):
        cost["total_usd"] = round(float(cost["total_usd"]) + float(reported_cost), 10)
    else:
        cost["unknown"] = True
    if process.returncode:
        raise PlanError("claude-failed", f"Claude 执行失败，退出码 {process.returncode}；日志: {raw_path}")
    if parsed.get("is_error") is True or parsed.get("type") == "error" or parsed.get("subtype") == "error":
        raise PlanError("claude-reported-error", f"Claude JSON 报告执行失败；日志: {raw_path}")
    return parsed


def runtime_git_gate(ledger: dict[str, object]) -> tuple[Path, str]:
    ensure_run_lock(ledger)
    repository, head = ensure_repository_matches(ledger)
    ensure_no_git_operation(repository)
    start = str(ledger["ticket_start_head"])
    if run_git(repository, "merge-base", "--is-ancestor", start, head, check=False).returncode:
        raise PlanError("history-rewritten", "工单起始 HEAD 不再是当前 HEAD 的祖先")
    return repository, head


def implementation_gate(ledger: dict[str, object], head: str) -> str:
    repository = Path(str(ledger["repository"]))
    start = str(ledger["ticket_start_head"])
    ensure_clean(repository)
    if head == start:
        raise PlanError("implementation-no-commit", "Implementation 没有产生新 commit")
    ancestor = run_git(repository, "merge-base", "--is-ancestor", start, head, check=False)
    if ancestor.returncode:
        raise PlanError("history-rewritten", "工单起始 HEAD 不再是当前 HEAD 的祖先")
    return head


def completion_gate(ledger: dict[str, object], head: str) -> None:
    repository = Path(str(ledger["repository"]))
    ensure_clean(repository)
    implementation_head = str(ledger["implementation_head"])
    ancestor = run_git(repository, "merge-base", "--is-ancestor", implementation_head, head, check=False)
    if ancestor.returncode:
        raise PlanError("history-rewritten", "Implementation HEAD 不再是当前 HEAD 的祖先")
    if head != implementation_head:
        commits = run_git(repository, "rev-list", "--reverse", f"{implementation_head}..{head}").stdout.splitlines()
        expected = f"chore(ticket-loop): close {ledger['active_ticket']}"
        if len(commits) != 1:
            raise PlanError("invalid-closeout-commit", "Closeout 最多只能新增一个提交")
        subject = run_git(repository, "log", "-1", "--pretty=%s", commits[0]).stdout.strip()
        if subject != expected:
            raise PlanError("invalid-closeout-commit", f"Closeout commit 信息必须为: {expected}")
        changed = run_git(
            repository, "-c", "core.quotepath=false", "diff-tree", "--no-commit-id", "--name-only", "-r", commits[0]
        ).stdout.splitlines()
        try:
            expected_path = str(Path(str(ledger["ticket_path"])).relative_to(repository))
        except ValueError as error:
            raise PlanError("ticket-outside-repository", "Task Ticket 必须位于当前仓库中") from error
        if changed != [expected_path]:
            raise PlanError("invalid-closeout-changes", "Closeout commit 只能修改当前 Task Ticket")
    ticket_path = Path(str(ledger["ticket_path"]))
    ticket = parse_ticket(ticket_path, require_task=True)
    if not ticket.done:
        raise PlanError("ticket-not-done", "Closeout 后工单状态不是 done (...)")
    done_details = DONE_DETAILS_RE.fullmatch(ticket.status)
    if not done_details:
        raise PlanError("invalid-done-commit", "done 状态必须包含 ISO 日期和实现 commit")
    try:
        datetime.strptime(done_details.group(1), "%Y-%m-%d")
    except ValueError as error:
        raise PlanError("invalid-done-commit", "done 状态日期无效") from error
    referenced_commit = run_git(
        repository, "rev-parse", "--verify", f"{done_details.group(2)}^{{commit}}", check=False
    )
    if referenced_commit.returncode:
        raise PlanError("invalid-done-commit", "done 状态引用的实现 commit 不存在")
    referenced_head = referenced_commit.stdout.strip()
    if referenced_head == str(ledger["ticket_start_head"]):
        raise PlanError("invalid-done-commit", "done 状态必须引用本次运行产生的实现 commit")
    if run_git(
        repository,
        "merge-base",
        "--is-ancestor",
        str(ledger["ticket_start_head"]),
        referenced_head,
        check=False,
    ).returncode or run_git(
        repository, "merge-base", "--is-ancestor", referenced_head, implementation_head, check=False
    ).returncode:
        raise PlanError("invalid-done-commit", "done 状态引用的 commit 不属于本次实现历史")
    text = ticket_path.read_text(encoding="utf-8")
    checklist = list(CHECKLIST_RE.finditer(text.split("## Completion evidence", 1)[0]))
    if not checklist or any(match.group("mark").lower() != "x" for match in checklist):
        raise PlanError("incomplete-checklist", "工单验收 checklist 尚未全部勾选")
    if len(EVIDENCE_HEADING_RE.findall(text)) != 1:
        raise PlanError("invalid-completion-evidence", "必须且只能有一个 Completion evidence 区段")
    evidence_section = EVIDENCE_HEADING_RE.split(text, maxsplit=1)[1]
    entries: dict[str, str] = {}
    for match in EVIDENCE_ITEM_RE.finditer(evidence_section):
        key, value = match.group(1).lower(), match.group(2).strip()
        if key in entries:
            raise PlanError("invalid-completion-evidence", f"Completion evidence 重复: {key}")
        entries[key] = value
    required = {"implementation", "typecheck", "tests", "review"}
    if set(entries) != required or any(not value for value in entries.values()):
        raise PlanError("invalid-completion-evidence", "Completion evidence 缺少必需的非空条目")
    for value in entries.values():
        if value.lower().startswith("not applicable") and not NOT_APPLICABLE_RE.fullmatch(value):
            raise PlanError("invalid-completion-evidence", "Not applicable 必须包含括号内原因")
        if FAILURE_WORD_RE.search(value) and not re.search(r"\b(no|not|without)\b", value, re.IGNORECASE):
            raise PlanError("failed-completion-evidence", "Completion evidence 包含未解释的失败信息")


def git_snapshot(repository: Path) -> str:
    tracked = run_git(repository, "diff", "--binary", "HEAD").stdout
    untracked: list[dict[str, str]] = []
    for path in run_git(repository, "ls-files", "--others", "--exclude-standard", "-z").stdout.split("\0"):
        if not path:
            continue
        candidate = repository / path
        try:
            content = candidate.read_bytes()
        except OSError as error:
            raise PlanError("recovery-context-drift", f"无法读取未跟踪文件以验证恢复现场: {path}") from error
        untracked.append({"path": path, "sha256": hashlib.sha256(content).hexdigest()})
    payload = json.dumps({"tracked": tracked, "untracked": untracked}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def recovery_snapshot(repository: Path, head: str) -> dict[str, object]:
    return {
        "head": head,
        "worktree": worktree_status(repository),
        "fingerprint": git_snapshot(repository),
    }


def recovery_prompt(ledger: dict[str, object], recovery: dict[str, object]) -> str:
    snapshot = recovery["snapshot"]
    dirty = snapshot["worktree"] or []
    facts = [
        f"阶段: {recovery['phase']}",
        f"未通过的 Success Gate: {recovery['failure']['code']}",
        f"门禁诊断: {recovery['failure']['message']}",
        f"Task Ticket: {ledger['active_ticket']} ({ledger['ticket_path']})",
        f"工单起始 HEAD: {ledger['ticket_start_head']}",
        f"当前 HEAD: {snapshot['head']}",
        f"工作区状态: {json.dumps(dirty, ensure_ascii=False)}",
    ]
    return (
        "继续当前 Task Ticket 的工作，不得开始其他工单。根据以下由 runner 验证的事实修复未通过的门禁，"
        "完成验证与提交；不得 amend、rebase、改写历史、切换分支、push 或创建 PR。\n"
        + "\n".join(f"- {fact}" for fact in facts)
    )


def ensure_current_ledger(ledger: dict[str, object]) -> None:
    if ledger.get("_legacy"):
        raise PlanError(
            "legacy-ledger-not-resumable",
            "旧版 Run Ledger 仅支持 status、abort 和 clean，不能继续 Task Session",
        )


def ensure_run_can_invoke(ledger: dict[str, object]) -> None:
    ensure_current_ledger(ledger)
    if ledger["state"] == "completed":
        raise PlanError("run-already-completed", "Loop Run 已完成")
    if ledger["state"] == "aborted":
        raise PlanError("run-aborted", "Loop Run 已停止调度")
    if ledger["state"] == "interrupted":
        raise PlanError("run-interrupted", "Loop Run 已中断，必须显式 reopen")
    if ledger["state"] == "terminal-failure":
        raise PlanError("run-terminal-failure", "Loop Run 已进入 terminal-failure")


def invoke_phase_locked(args: argparse.Namespace, *, resume: bool) -> dict[str, object]:
    path, ledger = load_ledger(args.run)
    reconcile_stale_lock(path, ledger)
    ensure_run_can_invoke(ledger)
    recovery = ledger.get("recovery")
    if resume and not isinstance(recovery, dict):
        raise PlanError("run-not-recoverable", "Loop Run 当前没有可恢复异常")
    if not resume and recovery:
        raise PlanError("resume-required", "Loop Run 存在可恢复异常，必须使用 resume")

    phase = str(ledger["phase"])
    attempts = ledger["attempts"][phase]
    repository: Path | None = None
    invoked = False
    try:
        repository, head = runtime_git_gate(ledger)
        set_lock_active_pid(ledger, os.getpid())
        invocation_id = str(uuid.uuid4())
        ledger["in_flight"] = {
            "invocation_id": invocation_id,
            "phase": phase,
            "attempt_kind": "resume" if resume else "initial",
            "session_id": ledger["session_id"],
            "state": "prepared",
            "runner_pid": os.getpid(),
            "child_pid": None,
            "child_pgid": None,
            "prepared_at": datetime.now(timezone.utc).isoformat(),
            "spawned_at": None,
        }
        write_ledger(path, ledger)
        if resume:
            if recovery["phase"] != phase:
                raise PlanError("recovery-context-drift", "恢复阶段与 Run Ledger 不一致")
            current = recovery_snapshot(repository, head)
            if current != recovery["snapshot"]:
                raise PlanError("recovery-context-drift", "Git 现场已在失败后发生变化，无法安全恢复")
            prompt = recovery_prompt(ledger, recovery)
            timeout_seconds = float(runtime_config(ledger)["timeouts"]["resume"])
        else:
            ensure_clean(repository)
            prompt = str(ledger[f"{phase}_prompt"])
            timeout_seconds = float(runtime_config(ledger)["timeouts"][phase])
        invoked = True
        claude_error: PlanError | None = None
        try:
            invoke_claude(
                ledger,
                phase,
                prompt,
                resume=resume or phase == "closeout",
                recovery_attempt=resume,
                timeout_seconds=timeout_seconds,
            )
        except PlanError as error:
            claude_error = error
        _, head = runtime_git_gate(ledger)
        ensure_external_blockers_unchanged(ledger)
        if claude_error:
            raise claude_error
        if phase == "implementation":
            ledger["implementation_head"] = implementation_gate(ledger, head)
            ledger["phase"] = "closeout"
        else:
            completion_gate(ledger, head)
            advance_after_closeout(ledger, head)
        ledger["recovery"] = None
        ledger["in_flight"] = None
        ledger["gate_failures"] = []
    except PlanError as error:
        try:
            current_state = load_ledger(str(ledger["run_id"]))[1].get("state")
        except PlanError:
            current_state = None
        if current_state == "aborted":
            raise PlanError("run-aborted", "Loop Run 已停止调度") from error
        ledger["gate_failures"] = [{"code": error.code, "message": str(error)}]
        if error.code == "run-interrupted":
            if repository is not None:
                _, current_head = ensure_repository_matches(ledger)
                ledger["recovery"] = {
                    "phase": phase,
                    "failure": {"code": error.code, "message": str(error)},
                    "snapshot": recovery_snapshot(repository, current_head),
                }
            ledger["state"] = "interrupted"
            ledger["interrupted_at"] = datetime.now(timezone.utc).isoformat()
            write_ledger(path, ledger)
            release_run_lock(ledger)
            raise
        terminal = error.code in TERMINAL_GIT_ERRORS
        if error.code == "claude-launch-failed":
            ledger["in_flight"] = None
            terminal = False
        elif invoked and not terminal and repository is not None:
            _, current_head = runtime_git_gate(ledger)
            ledger["recovery"] = {
                "phase": phase,
                "failure": {"code": error.code, "message": str(error)},
                "snapshot": recovery_snapshot(repository, current_head),
            }
            if attempts["resumes"] >= MAX_PHASE_RESUMES:
                terminal = True
        if terminal:
            ledger["state"] = "terminal-failure"
            ledger["terminal_reason"] = error.code
            in_flight = ledger.get("in_flight")
            spawned = isinstance(in_flight, dict) and in_flight.get("state") == "spawned"
            if error.code != "claude-session-mismatch" and spawned:
                ledger["recovery"] = {
                    "phase": phase,
                    "failure": {"code": error.code, "message": str(error)},
                    "snapshot": None,
                    "requires_resume": True,
                }
                if repository is not None:
                    try:
                        _, current_head = ensure_repository_matches(ledger)
                        ledger["recovery"]["snapshot"] = recovery_snapshot(repository, current_head)
                    except PlanError:
                        pass
        write_ledger(path, ledger)
        if ledger["state"] == "terminal-failure":
            release_run_lock(ledger)
        else:
            set_lock_active_pid(ledger, None)
        raise
    write_ledger(path, ledger)
    if ledger["state"] == "completed":
        release_run_lock(ledger)
    else:
        set_lock_active_pid(ledger, None)
    return public_run(ledger, "resume" if resume else "step")


def invoke_phase(args: argparse.Namespace, *, resume: bool) -> dict[str, object]:
    _, snapshot = load_ledger(args.run)
    common = Path(str(snapshot["git_common_directory"]))
    with command_lock(common):
        return invoke_phase_locked(args, resume=resume)


def step_command(args: argparse.Namespace) -> dict[str, object]:
    return invoke_phase(args, resume=False)


def resume_command(args: argparse.Namespace) -> dict[str, object]:
    return invoke_phase(args, resume=True)


def pid_is_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_group_is_alive(pgid: object) -> bool:
    if not isinstance(pgid, int) or pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate_process_group(pgid: object, child_pid: object | None = None) -> None:
    if child_pid is not None:
        if not isinstance(child_pid, int) or child_pid <= 0 or not pid_is_alive(child_pid):
            raise PlanError("child-identity-mismatch", "无法验证 Claude 子进程身份")
        try:
            actual_pgid = os.getpgid(child_pid)
        except ProcessLookupError as error:
            raise PlanError("child-identity-mismatch", "Claude 子进程已消失，无法验证进程组身份") from error
        if actual_pgid != pgid:
            raise PlanError("child-identity-mismatch", "Claude 子进程与记录的进程组不匹配")
    if not process_group_is_alive(pgid):
        return
    assert isinstance(pgid, int)
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + INTERRUPT_GRACE_SECONDS
    while process_group_is_alive(pgid) and time.monotonic() < deadline:
        time.sleep(0.05)
    if process_group_is_alive(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + INTERRUPT_GRACE_SECONDS
        while process_group_is_alive(pgid) and time.monotonic() < deadline:
            time.sleep(0.05)
    if process_group_is_alive(pgid):
        raise PlanError("child-termination-failed", "无法确认 Claude 子进程组已经退出")


def lifecycle_lock_payload(ledger: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "pid": os.getpid(),
        "active_pid": None,
        "child_pid": None,
        "child_pgid": None,
        "run_id": ledger["run_id"],
        "lock_id": ledger["lock_id"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "repository": ledger["repository"],
        "git_common_directory": ledger["git_common_directory"],
        "branch": ledger["branch"],
    }


def reconcile_stale_lock(path: Path, ledger: dict[str, object]) -> None:
    if ledger["state"] != "running":
        return
    common = Path(str(ledger["git_common_directory"]))
    try:
        lock = read_run_lock(common)
    except PlanError:
        return
    active_pid = lock.get("active_pid")
    if (
        lock.get("run_id") != ledger["run_id"]
        or lock.get("lock_id") != ledger.get("lock_id")
        or active_pid is None
        or pid_is_alive(active_pid)
    ):
        return
    in_flight = ledger.get("in_flight")
    if isinstance(in_flight, dict) and in_flight.get("state") == "spawned":
        terminate_process_group(
            in_flight.get("child_pgid") or lock.get("child_pgid"),
            in_flight.get("child_pid") or lock.get("child_pid"),
        )
    source = lock_path(common)
    stale = source.parent / "stale"
    stale.mkdir(parents=True, exist_ok=True, mode=0o700)
    archived = stale / f"{ledger['run_id']}-{int(time.time())}.json"
    source.replace(archived)
    if isinstance(in_flight, dict) and in_flight.get("state") == "spawned":
        repository, head = ensure_repository_matches(ledger)
        ledger["recovery"] = {
            "phase": in_flight["phase"],
            "failure": {"code": "stale-in-flight", "message": "Runner 退出时 Claude 调用仍在进行"},
            "snapshot": recovery_snapshot(repository, head),
        }
    else:
        ledger["in_flight"] = None
        ledger["gate_failures"] = []
        acquire_run_lock(common, lifecycle_lock_payload(ledger))
        write_ledger(path, ledger)
        return
    ledger["state"] = "interrupted"
    ledger["interrupted_at"] = datetime.now(timezone.utc).isoformat()
    ledger["gate_failures"] = [{"code": "stale-run-lock", "message": "检测到已退出进程遗留的仓库锁"}]
    write_ledger(path, ledger)


def finish_abort(path: Path, ledger: dict[str, object]) -> dict[str, object]:
    if ledger["state"] == "completed":
        raise PlanError("run-already-completed", "已完成的 Loop Run 无需 abort")
    ledger["state"] = "aborted"
    ledger["in_flight"] = None
    ledger["aborted_at"] = datetime.now(timezone.utc).isoformat()
    write_ledger(path, ledger)
    release_run_lock(ledger)
    return public_run(ledger, "abort")


def abort_command(args: argparse.Namespace) -> dict[str, object]:
    path, snapshot = load_ledger(args.run)
    common = Path(str(snapshot["git_common_directory"]))
    try:
        with command_lock(common):
            return finish_abort(path, load_ledger(args.run)[1])
    except PlanError as error:
        if error.code != "run-active":
            raise
    lock = read_run_lock(common)
    if lock.get("run_id") != snapshot["run_id"] or lock.get("lock_id") != snapshot.get("lock_id"):
        raise PlanError("run-lock-mismatch", "仓库锁不属于当前 Loop Run")
    terminate_process_group(lock.get("child_pgid"), lock.get("child_pid"))
    with command_lock(common, blocking=True):
        return finish_abort(path, load_ledger(args.run)[1])


def reopen_command_locked(args: argparse.Namespace) -> dict[str, object]:
    path, ledger = load_ledger(args.run)
    ensure_current_ledger(ledger)
    if ledger["state"] not in {"interrupted", "terminal-failure", "aborted"}:
        raise PlanError("run-not-reopenable", "Loop Run 当前不允许 reopen")
    if ledger.get("terminal_reason") == "claude-session-mismatch":
        raise PlanError("run-not-reopenable", "Session 身份不可信的 Loop Run 不允许 reopen")
    repository, head = ensure_repository_matches(ledger)
    ensure_no_git_operation(repository)
    start = str(ledger["ticket_start_head"])
    if run_git(repository, "merge-base", "--is-ancestor", start, head, check=False).returncode:
        raise PlanError("history-rewritten", "工单起始 HEAD 不再是当前 HEAD 的祖先")
    recovery = ledger.get("recovery")
    if isinstance(recovery, dict):
        current_snapshot = recovery_snapshot(repository, head)
        if recovery.get("snapshot") is None and recovery.get("requires_resume") is True:
            recovery["snapshot"] = current_snapshot
        elif current_snapshot != recovery.get("snapshot"):
            raise PlanError("recovery-context-drift", "Git 现场已在失败后发生变化，无法安全 reopen")
    in_flight = ledger.get("in_flight")
    started_in_flight = isinstance(in_flight, dict) and in_flight.get("state") == "spawned"
    if not ledger.get("session_id") or (not ledger.get("claude_calls") and not started_in_flight):
        raise PlanError("session-unavailable", "原 Task Session 没有可验证的启动记录，拒绝创建新 Session 接管")
    ledger["lock_id"] = str(uuid.uuid4())
    acquire_run_lock(
        Path(str(ledger["git_common_directory"])),
        lifecycle_lock_payload(ledger),
    )
    ledger["state"] = "running"
    ledger["reopened_at"] = datetime.now(timezone.utc).isoformat()
    try:
        write_ledger(path, ledger)
    except BaseException:
        release_run_lock(ledger)
        raise
    return public_run(ledger, "reopen")


def reopen_command(args: argparse.Namespace) -> dict[str, object]:
    _, snapshot = load_ledger(args.run)
    with command_lock(Path(str(snapshot["git_common_directory"]))):
        return reopen_command_locked(args)


def clean_command_locked(args: argparse.Namespace) -> dict[str, object]:
    path, ledger = load_ledger(args.run)
    if ledger["state"] not in {"completed", "aborted"}:
        raise PlanError("run-not-terminal", "只有 completed 或 aborted Loop Run 可以 clean")
    repository = Path(str(ledger["repository"]))
    expected_path = ledger_path(repository, str(ledger["run_id"]))
    if path.resolve() != expected_path.resolve():
        raise PlanError("invalid-run-ledger", "Run Ledger 路径不安全")
    logs = repository / ".ticket-loop" / "logs" / str(ledger["run_id"])
    path.unlink()
    if logs.is_dir():
        shutil.rmtree(logs)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "command": "clean",
        "run_id": ledger["run_id"],
    }


def clean_command(args: argparse.Namespace) -> dict[str, object]:
    _, snapshot = load_ledger(args.run)
    with command_lock(Path(str(snapshot["git_common_directory"]))):
        return clean_command_locked(args)


def status_command(args: argparse.Namespace) -> dict[str, object]:
    _, ledger = load_ledger(args.run)
    return public_run(ledger, "status")


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PlanError("invalid-arguments", message)


def positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("超时必须是秒数") from error
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("超时必须是有限且大于 0 的秒数")
    return timeout


def positive_cost(value: str) -> float:
    try:
        cost = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("最大费用必须是美元数值") from error
    if not math.isfinite(cost) or cost <= 0:
        raise argparse.ArgumentTypeError("最大费用必须是有限且大于 0 的美元数值")
    return cost


def add_selection_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--issues-dir")
    command.add_argument("--range", dest="ticket_range")
    command.add_argument("--tickets", nargs="+")


def parser() -> argparse.ArgumentParser:
    root = JsonArgumentParser(prog="ticket-loop")
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    add_selection_arguments(plan)
    start = commands.add_parser("start")
    add_selection_arguments(start)
    start.add_argument("--claude-executable", default="claude")
    start.add_argument("--implement-command")
    start.add_argument("--model")
    start.add_argument("--max-cost-usd", type=positive_cost)
    start.add_argument(
        "--implementation-timeout-seconds",
        type=positive_timeout,
        default=float(DEFAULT_IMPLEMENTATION_TIMEOUT_SECONDS),
    )
    start.add_argument(
        "--closeout-timeout-seconds",
        type=positive_timeout,
        default=float(DEFAULT_CLOSEOUT_TIMEOUT_SECONDS),
    )
    start.add_argument(
        "--resume-timeout-seconds",
        type=positive_timeout,
        default=float(DEFAULT_RESUME_TIMEOUT_SECONDS),
    )
    step = commands.add_parser("step")
    step.add_argument("--run")
    resume = commands.add_parser("resume")
    resume.add_argument("--run")
    reopen = commands.add_parser("reopen")
    reopen.add_argument("--run")
    status = commands.add_parser("status")
    status.add_argument("--run")
    abort = commands.add_parser("abort")
    abort.add_argument("--run")
    clean = commands.add_parser("clean")
    clean.add_argument("--run", required=True)
    return root


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "__child-launcher":
        return child_launcher()
    command = "unknown"
    try:
        args = parser().parse_args()
        command = args.command
        if command == "plan":
            payload = {"schema_version": SCHEMA_VERSION, "ok": True, "command": command, "manifest": plan_command(args)}
        elif command == "start":
            payload = start_command(args)
        elif command == "step":
            payload = step_command(args)
        elif command == "resume":
            payload = resume_command(args)
        elif command == "reopen":
            payload = reopen_command(args)
        elif command == "status":
            payload = status_command(args)
        elif command == "abort":
            payload = abort_command(args)
        elif command == "clean":
            payload = clean_command(args)
        else:
            raise PlanError("unsupported-command", f"不支持的命令: {command}")
        emit(payload)
        return 0
    except PlanError as error:
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "command": command,
                "error": {"code": error.code, "message": str(error)},
            }
        )
        print(str(error), file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
