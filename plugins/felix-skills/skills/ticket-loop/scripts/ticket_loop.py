#!/usr/bin/env python3
"""Deterministic, read-only planning for Ticket Loop task manifests."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Iterable


SCHEMA_VERSION = "1.0"
FILENAME_RE = re.compile(r"^(?P<id>0[1-9]|[1-9]\d)-.+\.md$")
FIELD_RE = re.compile(
    r"^\s*(?:\*\*)?(?P<name>What to build|Blocked by|Status|Type)(?:\*\*)?\s*:\s*(?:\*\*)?\s*(?P<value>.*?)\s*$",
    re.IGNORECASE,
)
WAYFINDER_TYPES = {"research", "prototype", "grilling", "task"}
WAYFINDER_STATUSES = {"claimed", "resolved"}
BLOCKER_RE = re.compile(r"^\s*(\d+)(?:\s*(?:/|[-–—])\s*\S.*)?\s*$")
DONE_RE = re.compile(r"^done\s*\([^()]+\)$", re.IGNORECASE)


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


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise PlanError("invalid-arguments", message)


def parser() -> argparse.ArgumentParser:
    root = JsonArgumentParser(prog="ticket-loop")
    commands = root.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument("--issues-dir")
    plan.add_argument("--range", dest="ticket_range")
    plan.add_argument("--tickets", nargs="+")
    return root


def emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main() -> int:
    try:
        args = parser().parse_args()
        if args.command == "plan":
            emit({"schema_version": SCHEMA_VERSION, "ok": True, "command": "plan", "manifest": plan_command(args)})
            return 0
        raise PlanError("unsupported-command", f"不支持的命令: {args.command}")
    except PlanError as error:
        emit(
            {
                "schema_version": SCHEMA_VERSION,
                "ok": False,
                "command": "plan",
                "error": {"code": error.code, "message": str(error)},
            }
        )
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
