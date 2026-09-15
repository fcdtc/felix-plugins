#!/usr/bin/env python3
"""Deterministic planning and single-ticket execution for Ticket Loop."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Iterable
import uuid


SCHEMA_VERSION = "1.0"
TERMINAL_GIT_ERRORS = {
    "repository-drift",
    "branch-drift",
    "history-rewritten",
    "git-operation-in-progress",
    "run-lock-missing",
    "run-lock-mismatch",
}
FILENAME_RE = re.compile(r"^(?P<id>0[1-9]|[1-9]\d)-.+\.md$")
FIELD_RE = re.compile(
    r"^\s*(?:\*\*)?(?P<name>What to build|Blocked by|Status|Type)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
WAYFINDER_TYPES = {"research", "prototype", "grilling", "task"}
WAYFINDER_STATUSES = {"claimed", "resolved"}
BLOCKER_RE = re.compile(r"^\s*(\d+)(?:\s*(?:/|[-–—])\s*\S.*)?\s*$")
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
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
    if re.fullmatch(r"\s*None(?:\s+(?:\([^\n]*\)|[-–—/]\s*[^\n]+))?\s*", raw, re.IGNORECASE):
        return ()
    if not raw.strip():
        raise PlanError("invalid-blocked-by", f"Blocked by 字段为空: {path}")

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
    return {
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


def ensure_clean(repository: Path) -> None:
    ensure_no_git_operation(repository)
    status = run_git(repository, "status", "--porcelain=v1", "--untracked-files=all").stdout
    if status:
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
    if lock.get("run_id") != ledger["run_id"]:
        raise PlanError("run-lock-mismatch", "仓库锁不属于当前 Loop Run")


def release_run_lock(ledger: dict[str, object]) -> None:
    path = lock_path(Path(str(ledger["git_common_directory"])))
    try:
        with path.open("r+", encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                lock = json.load(stream)
            except json.JSONDecodeError:
                return
            same_file = os.fstat(stream.fileno()).st_ino == path.stat().st_ino
            if same_file and isinstance(lock, dict) and lock.get("run_id") == ledger["run_id"]:
                path.unlink()
    except FileNotFoundError:
        return


def write_ledger(path: Path, ledger: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_ledger(run_id: str) -> tuple[Path, dict[str, object]]:
    repository, _ = repository_location(Path.cwd())
    path = ledger_path(repository, run_id)
    try:
        ledger = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PlanError("run-not-found", f"Loop Run 不存在: {run_id}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise PlanError("invalid-run-ledger", f"无法读取 Run Ledger: {path}") from error
    if ledger.get("schema_version") != SCHEMA_VERSION or ledger.get("run_id") != run_id:
        raise PlanError("invalid-run-ledger", f"Run Ledger 不兼容: {path}")
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
        "allowed_actions": ["status"] if ledger["state"] in {"completed", "terminal-failure"} else ["step", "status"],
        "git": {
            "repository": ledger["repository"],
            "branch": ledger["branch"],
            "run_start_head": ledger["run_start_head"],
            "ticket_start_head": ledger["ticket_start_head"],
            "implementation_head": ledger.get("implementation_head"),
        },
        "gate_failures": ledger.get("gate_failures", []),
    }


def start_command(args: argparse.Namespace) -> dict[str, object]:
    repository, common, branch, head = repository_identity(Path.cwd())
    ensure_clean(repository)
    manifest = plan_command(args)
    if len(manifest["tickets"]) != 1:
        raise PlanError("single-ticket-required", "本阶段一次 Run 只能包含一张 Task Ticket")
    ticket = manifest["tickets"][0]
    if ticket["status"] != "ready-for-agent" or manifest["frontier"] != ticket["id"]:
        raise PlanError("ticket-not-runnable", "所选工单当前不可执行")
    ticket_path = Path(ticket["path"])
    try:
        ticket_path.relative_to(repository)
    except ValueError as error:
        raise PlanError("ticket-outside-repository", "Task Ticket 必须位于当前仓库中") from error

    maintain_local_exclude(repository, common)
    run_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    implement_command = args.implement_command.lstrip("/")
    if not implement_command or any(character.isspace() for character in implement_command):
        raise PlanError("invalid-implement-command", "实现命令必须是单个 slash command 名称")
    closeout_prompt = CLOSEOUT_PROMPT.replace("<today>", date.today().isoformat()).replace(
        "<ticket-id>", ticket["id"]
    )
    ledger: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "state": "running",
        "phase": "implementation",
        "repository": str(repository),
        "git_common_directory": str(common),
        "branch": branch,
        "run_start_head": head,
        "ticket_start_head": head,
        "implementation_head": None,
        "manifest": manifest,
        "active_ticket": ticket["id"],
        "ticket_path": str(ticket_path),
        "implement_command": implement_command,
        "implementation_prompt": f"/{implement_command} @{ticket_path}",
        "closeout_prompt": closeout_prompt,
        "session_id": session_id,
        "claude_executable": str(Path(args.claude_executable).expanduser().resolve()) if os.sep in args.claude_executable else args.claude_executable,
        "model": args.model,
        "claude_calls": [],
        "gate_failures": [],
    }
    acquire_run_lock(
        common,
        {
            "schema_version": SCHEMA_VERSION,
            "pid": os.getpid(),
            "run_id": run_id,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "repository": str(repository),
            "git_common_directory": str(common),
            "branch": branch,
        },
    )
    path = ledger_path(repository, run_id)
    try:
        write_ledger(path, ledger)
    except BaseException:
        release_run_lock(ledger)
        raise
    return public_run(ledger, "start")


def invoke_claude(ledger: dict[str, object], phase: str, prompt: str) -> dict[str, object]:
    command = [
        str(ledger["claude_executable"]),
        "--print",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--dangerously-skip-permissions",
    ]
    if ledger.get("model"):
        command.extend(["--model", str(ledger["model"])])
    if phase == "implementation":
        command.extend(["--session-id", str(ledger["session_id"])])
    else:
        command.extend(["--resume", str(ledger["session_id"])])
    command.append(prompt)
    result = subprocess.run(
        command,
        cwd=str(ledger["repository"]),
        text=True,
        capture_output=True,
    )
    call_number = len(ledger["claude_calls"]) + 1
    log_path = Path(str(ledger["repository"])) / ".ticket-loop" / "logs" / str(ledger["run_id"])
    log_path.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw_path = log_path / f"{call_number:02d}-{phase}.json"
    raw_path.write_text(result.stdout, encoding="utf-8")
    os.chmod(raw_path, 0o600)
    call: dict[str, object] = {
        "phase": phase,
        "exit_code": result.returncode,
        "log_path": str(raw_path),
        "result": None,
    }
    ledger["claude_calls"].append(call)
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PlanError("invalid-claude-json", f"Claude 未返回有效 JSON；日志: {raw_path}") from error
    call["result"] = parsed
    if result.returncode:
        raise PlanError("claude-failed", f"Claude 执行失败，退出码 {result.returncode}；日志: {raw_path}")
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
    if head == start:
        raise PlanError("implementation-no-commit", "Implementation 没有产生新 commit")
    ancestor = run_git(repository, "merge-base", "--is-ancestor", start, head, check=False)
    if ancestor.returncode:
        raise PlanError("history-rewritten", "工单起始 HEAD 不再是当前 HEAD 的祖先")
    ensure_clean(repository)
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


def step_command(args: argparse.Namespace) -> dict[str, object]:
    path, ledger = load_ledger(args.run)
    if ledger["state"] == "completed":
        raise PlanError("run-already-completed", "Loop Run 已完成")
    if ledger["state"] == "terminal-failure":
        raise PlanError("run-terminal-failure", "Loop Run 已进入 terminal-failure")
    phase = str(ledger["phase"])
    prompt = str(ledger[f"{phase}_prompt"])
    try:
        repository, _ = runtime_git_gate(ledger)
        ensure_clean(repository)
        claude_error: PlanError | None = None
        try:
            invoke_claude(ledger, phase, prompt)
        except PlanError as error:
            claude_error = error
        _, head = runtime_git_gate(ledger)
        if claude_error:
            raise claude_error
        if phase == "implementation":
            ledger["implementation_head"] = implementation_gate(ledger, head)
            ledger["phase"] = "closeout"
        else:
            completion_gate(ledger, head)
            ledger["state"] = "completed"
            ledger["phase"] = None
        ledger["gate_failures"] = []
    except PlanError as error:
        ledger["gate_failures"] = [{"code": error.code, "message": str(error)}]
        if error.code in TERMINAL_GIT_ERRORS:
            ledger["state"] = "terminal-failure"
        write_ledger(path, ledger)
        if ledger["state"] == "terminal-failure":
            release_run_lock(ledger)
        raise
    write_ledger(path, ledger)
    if ledger["state"] == "completed":
        release_run_lock(ledger)
    return public_run(ledger, "step")


def status_command(args: argparse.Namespace) -> dict[str, object]:
    _, ledger = load_ledger(args.run)
    return public_run(ledger, "status")


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PlanError("invalid-arguments", message)


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
    start.add_argument("--implement-command", default="implement")
    start.add_argument("--model")
    step = commands.add_parser("step")
    step.add_argument("--run", required=True)
    status = commands.add_parser("status")
    status.add_argument("--run", required=True)
    return root


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
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
        elif command == "status":
            payload = status_command(args)
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
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
