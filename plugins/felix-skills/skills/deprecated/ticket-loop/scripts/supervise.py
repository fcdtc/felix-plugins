#!/usr/bin/env python3
"""Drive Ticket Loop's versioned JSON protocol to a terminal state."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


PROTOCOL_VERSION = "1.0"
TERMINAL_STATES = {"completed", "terminal-failure", "aborted", "interrupted"}
DRIVABLE_ACTIONS = {"step", "resume"}
STDERR_EXCERPT_LINES = 8


class SupervisorError(Exception):
    pass


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="ticket-loop-supervisor")
    command.add_argument("--runner", default=str(Path(__file__).with_name("ticket_loop.py")))
    selection = command.add_mutually_exclusive_group()
    selection.add_argument("--issues-dir")
    selection.add_argument("--tickets", nargs="+")
    command.add_argument("--range", dest="ticket_range")
    command.add_argument("--claude-executable", default="claude")
    command.add_argument("--implement-command")
    command.add_argument("--model")
    command.add_argument("--max-cost-usd", type=float)
    command.add_argument("--implementation-timeout-seconds", type=float)
    command.add_argument("--closeout-timeout-seconds", type=float)
    command.add_argument("--resume-timeout-seconds", type=float)
    return command


def run_runner(runner: str, command: str, arguments: Sequence[str]) -> tuple[int, dict[str, object], str]:
    result = subprocess.run(
        [sys.executable, runner, command, *arguments],
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise SupervisorError(f"Runner 未返回有效 JSON ({command})") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != PROTOCOL_VERSION:
        raise SupervisorError(f"Runner JSON 协议不兼容 ({command})")
    return result.returncode, payload, result.stderr


def report_stderr(stderr: str) -> None:
    lines = stderr.splitlines()
    if lines:
        print("\n".join(lines[-STDERR_EXCERPT_LINES:]), file=sys.stderr)


def selection_arguments(args: argparse.Namespace) -> list[str]:
    if args.tickets:
        result = ["--tickets", *args.tickets]
    elif args.issues_dir:
        result = ["--issues-dir", args.issues_dir]
    else:
        raise SupervisorError("必须提供 --issues-dir 或 --tickets")
    if args.ticket_range:
        result.extend(["--range", args.ticket_range])
    return result


def optional_start_arguments(args: argparse.Namespace) -> list[str]:
    result = ["--claude-executable", args.claude_executable]
    options = (
        ("--implement-command", args.implement_command),
        ("--model", args.model),
        ("--max-cost-usd", args.max_cost_usd),
        ("--implementation-timeout-seconds", args.implementation_timeout_seconds),
        ("--closeout-timeout-seconds", args.closeout_timeout_seconds),
        ("--resume-timeout-seconds", args.resume_timeout_seconds),
    )
    for flag, value in options:
        if value is not None:
            result.extend([flag, str(value)])
    return result


def drive(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    selection = selection_arguments(args)
    code, planned, stderr = run_runner(args.runner, "plan", selection)
    if code or planned.get("ok") is not True:
        report_stderr(stderr)
        return code or 2, planned

    code, current, stderr = run_runner(
        args.runner,
        "start",
        [*selection, *optional_start_arguments(args)],
    )
    if code or current.get("ok") is not True:
        report_stderr(stderr)
        return code or 2, current

    while current.get("state") not in TERMINAL_STATES:
        actions = current.get("allowed_actions")
        if not isinstance(actions, list):
            raise SupervisorError("Runner JSON 缺少 allowed_actions")
        candidates = DRIVABLE_ACTIONS.intersection(actions)
        if len(candidates) != 1:
            raise SupervisorError(f"Runner 必须且只能允许一个推进动作: {actions}")
        action = candidates.pop()
        run_id = current.get("run_id")
        if not isinstance(run_id, str):
            raise SupervisorError("Runner JSON 缺少 run_id")
        code, result, stderr = run_runner(args.runner, action, ["--run", run_id])
        report_stderr(stderr)
        if code or result.get("ok") is not True:
            status_code, current, status_stderr = run_runner(args.runner, "status", ["--run", run_id])
            report_stderr(status_stderr)
            if status_code or current.get("ok") is not True:
                return status_code or code or 2, result
            if current.get("state") in TERMINAL_STATES:
                return 2, current
            if "resume" not in current.get("allowed_actions", []):
                return code or 2, result
            continue
        current = result

    return (0 if current.get("state") == "completed" else 2), current


def main() -> int:
    try:
        code, payload = drive(parser().parse_args())
    except SupervisorError as error:
        code = 2
        payload = {
            "schema_version": PROTOCOL_VERSION,
            "ok": False,
            "command": "supervise",
            "error": {"code": "supervisor-error", "message": str(error)},
        }
        print(str(error), file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
