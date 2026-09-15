import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


CLI = Path(__file__).parents[1] / "scripts" / "ticket_loop.py"


def task(number, status="ready-for-agent", blocked_by="None", *, bold=True, title="工单"):
    marker = "**" if bold else ""
    return f"""# {number}: {title}

**What to build:** 实现 {title}

{marker}Blocked by:{marker} {blocked_by}

{marker}Status:{marker} {status}

- [ ] 验收 {title}
"""


def wayfinder(number, status="resolved", blocked_by="None"):
    return f"""# {number}: 调研

Type: research
Status: {status}
Blocked by: {blocked_by}

## Question
为什么？
"""


class TicketLoopPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "含 空格的仓库"
        self.issues = self.root / "需求 空间" / "issues"
        self.issues.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, filename, content):
        path = self.issues / filename
        path.write_text(content, encoding="utf-8")
        return path

    def plan(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(CLI), "plan", *map(str, args)],
            cwd=self.root,
            text=True,
            capture_output=True,
            env=env,
        )

    def payload(self, result):
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        return json.loads(lines[0])

    def assert_rejected(self, result, code):
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], code)
        self.assertNotIn("Traceback", result.stderr)

    def test_plans_directory_as_frozen_absolute_manifest_and_lowest_frontier(self):
        two = self.write("02-后续 工单.md", task("02", blocked_by="01 / 前置"))
        one = self.write("01-中文 工单.md", task("01", bold=False))

        before = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain=v1", "--untracked-files=all"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        result = self.plan("--issues-dir", self.issues)
        after = subprocess.run(
            ["git", "-C", str(self.root), "status", "--porcelain=v1", "--untracked-files=all"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["schema_version"], "1.0")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "plan")
        self.assertEqual(payload["manifest"]["issues_directory"], str(self.issues.resolve()))
        self.assertEqual(
            [(item["id"], item["path"], item["status"], item["blocked_by"]) for item in payload["manifest"]["tickets"]],
            [
                ("01", str(one.resolve()), "ready-for-agent", []),
                ("02", str(two.resolve()), "ready-for-agent", ["01"]),
            ],
        )
        self.assertEqual(payload["manifest"]["frontier"], "01")
        self.assertEqual(before, after)
        self.assertFalse((self.root / ".ticket-loop").exists())

    def test_plan_does_not_invoke_claude(self):
        ticket = self.write("01-ticket.md", task("01"))
        fake_bin = self.root / "fake-bin"
        fake_bin.mkdir()
        sentinel = self.root / "claude-called"
        fake_claude = fake_bin / "claude"
        fake_claude.write_text(f"#!/bin/sh\ntouch '{sentinel}'\nexit 99\n", encoding="utf-8")
        fake_claude.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"

        result = self.plan("--tickets", ticket, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(sentinel.exists())

    def test_range_and_explicit_ticket_selection(self):
        one = self.write("01-one.md", task("01"))
        two = self.write("02-two.md", task("02"))
        three = self.write("03-three.md", task("03"))

        ranged = self.plan("--issues-dir", self.issues, "--range", "02-03")
        explicit = self.plan("--tickets", three, one)

        self.assertEqual(ranged.returncode, 0, ranged.stderr)
        self.assertEqual([t["id"] for t in self.payload(ranged)["manifest"]["tickets"]], ["02", "03"])
        self.assertEqual(explicit.returncode, 0, explicit.stderr)
        explicit_payload = self.payload(explicit)
        self.assertEqual([t["id"] for t in explicit_payload["manifest"]["tickets"]], ["01", "03"])
        self.assertEqual(explicit_payload["manifest"]["issues_directory"], str(self.issues.resolve()))
        self.assertEqual(two.read_text(encoding="utf-8"), task("02"))

    def test_external_done_task_and_resolved_wayfinder_satisfy_dependencies(self):
        self.write("01-done.md", task("01", status="done (2026-09-14, abc1234)"))
        self.write("02-research.md", wayfinder("02", status="resolved"))
        target = self.write("03-target.md", task("03", blocked_by="01 / 完成, 02 / 调研"))

        result = self.plan("--tickets", target)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertEqual(payload["manifest"]["frontier"], "03")
        self.assertEqual(payload["manifest"]["tickets"][0]["blocked_by"], ["01", "02"])

    def test_external_unfinished_blockers_leave_no_frontier(self):
        self.write("01-open.md", task("01"))
        self.write("02-research.md", wayfinder("02", status="claimed"))
        target = self.write("03-target.md", task("03", blocked_by="01, 02"))

        result = self.plan("--tickets", target)

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = self.payload(result)
        self.assertIsNone(payload["manifest"]["frontier"])

    def test_directory_excludes_wayfinder_but_rejects_malformed_task_candidate(self):
        self.write("01-wayfinder.md", wayfinder("01"))
        task_path = self.write("02-task.md", task("02"))

        result = self.plan("--issues-dir", self.issues)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            [item["path"] for item in self.payload(result)["manifest"]["tickets"]],
            [str(task_path.resolve())],
        )

        task_path.write_text("# 02: 缺字段\n\nStatus: ready-for-agent\n", encoding="utf-8")
        self.assert_rejected(self.plan("--issues-dir", self.issues), "invalid-task-ticket")

    def test_rejects_wayfinder_or_malformed_selected_ticket(self):
        selected_wayfinder = self.write("01-wayfinder.md", wayfinder("01"))
        result = self.plan("--tickets", selected_wayfinder)
        self.assert_rejected(result, "invalid-task-ticket")

        selected_wayfinder.write_text(wayfinder("01").replace("Type: research", "Type: task"), encoding="utf-8")
        self.assert_rejected(self.plan("--tickets", selected_wayfinder), "invalid-task-ticket")

        selected_wayfinder.write_text("# 01: 缺字段\n\nStatus: ready-for-agent\n", encoding="utf-8")
        result = self.plan("--tickets", selected_wayfinder)
        self.assert_rejected(result, "invalid-task-ticket")

    def test_rejects_missing_or_duplicate_status_and_unsupported_status(self):
        ticket = self.write("01-status.md", task("01").replace("**Status:** ready-for-agent\n", ""))
        self.assert_rejected(self.plan("--tickets", ticket), "invalid-task-ticket")

        ticket.write_text(task("01") + "\nStatus: ready-for-agent\n", encoding="utf-8")
        self.assert_rejected(self.plan("--tickets", ticket), "duplicate-field")

        ticket.write_text(task("01", status="claimed"), encoding="utf-8")
        self.assert_rejected(self.plan("--tickets", ticket), "invalid-status")

    def test_rejects_missing_blocker_duplicate_number_and_self_dependency(self):
        target = self.write("02-target.md", task("02", blocked_by="01"))
        self.assert_rejected(self.plan("--tickets", target), "missing-blocker")

        first = self.write("01-first.md", task("01"))
        duplicate = self.write("01-duplicate.md", task("01"))
        self.assert_rejected(self.plan("--tickets", first, duplicate), "duplicate-ticket-id")
        duplicate.unlink()

        target.write_text(task("02", blocked_by="02"), encoding="utf-8")
        self.assert_rejected(self.plan("--tickets", target), "self-dependency")

    def test_rejects_dependency_cycle_including_external_blockers(self):
        one = self.write("01-one.md", task("01", blocked_by="02"))
        two = self.write("02-two.md", task("02", blocked_by="01"))

        self.assert_rejected(self.plan("--tickets", one, two), "dependency-cycle")
        self.assert_rejected(self.plan("--tickets", one), "dependency-cycle")

    def test_accepts_none_with_explanatory_suffix(self):
        ticket = self.write("01-ticket.md", task("01", blocked_by="None (can start immediately)"))

        result = self.plan("--tickets", ticket)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.payload(result)["manifest"]["tickets"][0]["blocked_by"], [])

    def test_argument_errors_are_structured_json(self):
        result = self.plan("--unknown")
        self.assert_rejected(result, "invalid-arguments")

    def test_invalid_utf8_is_a_structured_error(self):
        ticket = self.issues / "01-ticket.md"
        ticket.write_bytes(b"\xff\xfe")

        self.assert_rejected(self.plan("--tickets", ticket), "invalid-ticket-encoding")

    def test_rejects_invalid_filename_and_duplicate_blocked_by_field(self):
        for filename in ("ticket.md", "1-ticket.md", "001-ticket.md", "00-ticket.md"):
            invalid = self.write(filename, task("01"))
            self.assert_rejected(self.plan("--tickets", invalid), "invalid-ticket-filename")
            invalid.unlink()

        ticket = self.write("01-ticket.md", task("01") + "\nBlocked by: None\n")
        self.assert_rejected(self.plan("--tickets", ticket), "duplicate-field")

    def test_rejects_invalid_wayfinder_type_or_status_in_dependency_domain(self):
        invalid = self.write("01-wayfinder.md", wayfinder("01").replace("Type: research", "Type: typo"))
        target = self.write("02-target.md", task("02", blocked_by="01"))
        self.assert_rejected(self.plan("--tickets", target), "invalid-wayfinder-type")

        invalid.write_text(wayfinder("01", status="resolve"), encoding="utf-8")
        self.assert_rejected(self.plan("--tickets", target), "invalid-wayfinder-status")


class TicketLoopSingleTicketRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "含 空格的仓库"
        self.issues = self.root / "需求 空间" / "issues"
        self.issues.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        self.ticket = self.issues / "02-单工单.md"
        self.ticket.write_text(task("02", title="单工单"), encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        self.start_head = self.git("rev-parse", "HEAD").strip()
        self.calls = self.root / ".ticket-loop" / "claude-calls.jsonl"
        self.fake_claude = Path(self.temp.name) / "fake-claude.py"
        self.fake_claude.write_text(
            """#!/usr/bin/env python3
import json
from pathlib import Path
import subprocess
import sys
import time

args = sys.argv[1:]
root = Path.cwd()
calls_path = root / ".ticket-loop" / "claude-calls.jsonl"
prior_calls = calls_path.read_text(encoding="utf-8").splitlines() if calls_path.exists() else []
call_number = len(prior_calls) + 1
with calls_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args, ensure_ascii=False) + "\\n")
prompt = args[-1]
scenario_path = root / ".ticket-loop" / "scenario"
scenario = scenario_path.read_text(encoding="utf-8").strip() if scenario_path.exists() else "default"
ticket = root / "需求 空间" / "issues" / "02-单工单.md"
is_initial_implementation = prompt.startswith("/implement ")
is_implementation = is_initial_implementation or "阶段: implementation" in prompt

if scenario == "timeout-once" and call_number == 1:
    time.sleep(2)
if scenario == "timeout-child-once" and call_number == 1:
    subprocess.Popen([
        sys.executable,
        "-c",
        "import pathlib,time; time.sleep(1); pathlib.Path('late-child.txt').write_text('late')",
    ])
    time.sleep(2)
if scenario in {"interruptible", "interruptible-ignore-term"}:
    if scenario == "interruptible-ignore-term":
        import signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (root / ".ticket-loop" / "claude-ready").write_text("ready", encoding="utf-8")
    time.sleep(30)
if scenario == "nonzero-once" and call_number == 1:
    print(json.dumps({"type": "result"}))
    print("fake failure", file=sys.stderr)
    raise SystemExit(7)
if scenario == "invalid-json-once" and call_number == 1:
    print("not json")
    raise SystemExit(0)
if scenario == "is-error-once" and call_number == 1:
    print(json.dumps({"type": "result", "is_error": True}))
    raise SystemExit(0)
if scenario in {"always-error", "closeout-always-error"} and (
    scenario == "always-error" or not is_implementation
):
    print(json.dumps({"type": "error", "message": "recoverable fake failure"}))
    raise SystemExit(0)

if is_implementation:
    if scenario == "no-commit" or (scenario == "no-commit-once" and call_number == 1):
        print(json.dumps({"type": "result", "cost_usd": 0.01}))
        raise SystemExit(0)
    if scenario == "dirty-once" and call_number == 1:
        (root / "implementation.txt").write_text("implemented\\n", encoding="utf-8")
        print(json.dumps({"type": "result", "cost_usd": 0.01}))
        raise SystemExit(0)
    if scenario == "commit-and-dirty-once" and call_number == 1:
        (root / "implementation.txt").write_text("implemented\\n", encoding="utf-8")
        subprocess.run(["git", "add", "implementation.txt"], check=True)
        subprocess.run(["git", "commit", "-qm", "feat: partial implementation"], check=True)
        (root / "follow-up.txt").write_text("remaining\\n", encoding="utf-8")
        print(json.dumps({"type": "result", "cost_usd": 0.01}))
        raise SystemExit(0)
    if (root / "implementation.txt").exists():
        subprocess.run(["git", "add", "implementation.txt"], check=True)
    else:
        (root / "implementation.txt").write_text("implemented\\n", encoding="utf-8")
        subprocess.run(["git", "add", "implementation.txt"], check=True)
    if (root / "follow-up.txt").exists():
        subprocess.run(["git", "add", "follow-up.txt"], check=True)
    subprocess.run(["git", "commit", "-qm", "feat: implement ticket"], check=True)
    if scenario in {"branch-drift", "branch-drift-invalid-json"}:
        subprocess.run(["git", "switch", "-qc", "drift"], check=True)
    if scenario == "history-rewrite":
        tree = subprocess.run(["git", "write-tree"], text=True, capture_output=True, check=True).stdout.strip()
        replacement = subprocess.run(
            ["git", "commit-tree", tree, "-m", "replacement root"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        branch = subprocess.run(["git", "branch", "--show-current"], text=True, capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "update-ref", f"refs/heads/{branch}", replacement], check=True)
    if scenario == "merge-in-progress":
        git_dir = Path(subprocess.run(["git", "rev-parse", "--git-dir"], text=True, capture_output=True, check=True).stdout.strip())
        (git_dir / "MERGE_HEAD").write_text(subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout, encoding="utf-8")
    if scenario == "branch-drift-invalid-json":
        print("not json")
        raise SystemExit(1)
    if scenario == "already-closed":
        implementation = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
        text = ticket.read_text(encoding="utf-8")
        text = text.replace("**Status:** ready-for-agent", f"**Status:** done (2026-09-14, {implementation[:7]})")
        text = text.replace("- [ ] 验收 单工单", "- [x] 验收 单工单")
        text += "\\n## Completion evidence\\n\\n- Implementation: implementation commit\\n- Typecheck: Not applicable (pure text fixture)\\n- Tests: fake CLI lifecycle passed\\n- Review: reviewed implementation commit\\n"
        ticket.write_text(text, encoding="utf-8")
        subprocess.run(["git", "add", str(ticket)], check=True)
        subprocess.run(["git", "commit", "-qm", "docs: record ticket completion"], check=True)
elif scenario != "already-closed":
    text = ticket.read_text(encoding="utf-8")
    implementation = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    text = text.replace("**Status:** ready-for-agent", f"**Status:** done (2026-09-14, {implementation[:7]})")
    text = text.replace("- [ ] 验收 单工单", "- [x] 验收 单工单")
    if scenario == "closeout-dirty-once" and "## Completion evidence" not in text:
        text += "\\n## Completion evidence\\n\\n- Implementation: implementation commit\\n- Typecheck: Not applicable (pure text fixture)\\n- Tests: fake CLI lifecycle passed\\n"
        ticket.write_text(text, encoding="utf-8")
        print(json.dumps({"type": "result", "cost_usd": 0.01}))
        raise SystemExit(0)
    if scenario == "closeout-dirty-once" and "- Review:" not in text:
        text += "- Review: reviewed implementation commit\\n"
    elif "## Completion evidence" not in text:
        text += "\\n## Completion evidence\\n\\n- Implementation: implementation commit\\n- Typecheck: Not applicable (pure text fixture)\\n- Tests: fake CLI lifecycle passed\\n"
        if scenario != "invalid-evidence":
            text += "- Review: reviewed implementation commit\\n"
    ticket.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", str(ticket)], check=True)
    subprocess.run(["git", "commit", "-qm", "chore(ticket-loop): close 02"], check=True)
print(json.dumps({"type": "result", "cost_usd": 0.01}))
""",
            encoding="utf-8",
        )
        self.fake_claude.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.root), *args],
            text=True,
            capture_output=True,
            check=True,
        ).stdout

    def cli(self, command, *args):
        return subprocess.run(
            [sys.executable, str(CLI), command, *map(str, args)],
            cwd=self.root,
            text=True,
            capture_output=True,
        )

    def payload(self, result):
        self.assertEqual(result.stdout.count("\n"), 1, result.stdout)
        return json.loads(result.stdout)

    def start(self, *args):
        return self.cli(
            "start",
            "--tickets",
            self.ticket,
            "--claude-executable",
            self.fake_claude,
            *args,
        )

    def set_scenario(self, scenario):
        (self.root / ".ticket-loop" / "scenario").write_text(scenario, encoding="utf-8")

    def recorded_calls(self):
        return [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]

    def ledger(self, run_id):
        return json.loads((self.root / ".ticket-loop" / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))

    def assert_rejected(self, result, code):
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = self.payload(result)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], code)

    def lock_path(self):
        common = Path(self.git("rev-parse", "--git-common-dir").strip())
        if not common.is_absolute():
            common = self.root / common
        return common / "ticket-loop" / "lock.json"

    def test_start_rejects_detached_dirty_and_git_operation_states(self):
        subprocess.run(["git", "-C", str(self.root), "checkout", "--detach", "-q"], check=True)
        self.assert_rejected(self.start(), "detached-head")
        subprocess.run(["git", "-C", str(self.root), "switch", "-q", "-"], check=True)

        (self.root / "README.md").write_text("changed\n", encoding="utf-8")
        self.assert_rejected(self.start(), "dirty-worktree")
        subprocess.run(["git", "-C", str(self.root), "restore", "README.md"], check=True)

        (self.root / "staged.txt").write_text("staged\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "staged.txt"], check=True)
        self.assert_rejected(self.start(), "dirty-worktree")
        subprocess.run(["git", "-C", str(self.root), "reset", "-q", "HEAD", "staged.txt"], check=True)
        (self.root / "staged.txt").unlink()

        (self.root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        self.assert_rejected(self.start(), "dirty-worktree")
        (self.root / "untracked.txt").unlink()

        git_dir = Path(self.git("rev-parse", "--git-dir").strip())
        if not git_dir.is_absolute():
            git_dir = self.root / git_dir
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge"):
            path = git_dir / marker
            if marker == "rebase-merge":
                path.mkdir()
            else:
                path.write_text(self.start_head + "\n", encoding="utf-8")
            self.assert_rejected(self.start(), "git-operation-in-progress")
            if path.is_dir():
                path.rmdir()
            else:
                path.unlink()

    def test_start_maintains_local_exclude_without_touching_gitignore(self):
        gitignore = self.root / ".gitignore"
        gitignore.write_text("*.tmp\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "add ignore"], check=True)
        exclude = self.root / ".git" / "info" / "exclude"
        exclude.write_text("*.local", encoding="utf-8")

        started = self.start()

        self.assertEqual(started.returncode, 0, started.stderr)
        self.assertEqual(exclude.read_text(encoding="utf-8").splitlines().count(".ticket-loop/"), 1)
        self.assertEqual(gitignore.read_text(encoding="utf-8"), "*.tmp\n")
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_start_rejects_tracked_ticket_loop_directory(self):
        tracked = self.root / ".ticket-loop" / "owned.txt"
        tracked.parent.mkdir()
        tracked.write_text("owned\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", ".ticket-loop/owned.txt"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "track reserved path"], check=True)

        self.assert_rejected(self.start(), "tracked-run-directory")
        self.assertEqual(tracked.read_text(encoding="utf-8"), "owned\n")

    def test_repository_lock_records_owner_and_rejects_second_run(self):
        started = self.start()
        self.assertEqual(started.returncode, 0, started.stderr)
        first = self.payload(started)
        lock_path = self.lock_path()
        lock = json.loads(lock_path.read_text(encoding="utf-8"))

        self.assertEqual(lock["run_id"], first["run_id"])
        self.assertEqual(lock["branch"], self.git("branch", "--show-current").strip())
        self.assertEqual(lock["repository"], str(self.root.resolve()))
        self.assertIsInstance(lock["pid"], int)
        self.assertTrue(lock["started_at"].endswith("+00:00"))
        self.assertEqual(lock_path.stat().st_mode & 0o777, 0o600)
        self.assert_rejected(self.start(), "repository-locked")
        self.assertEqual(json.loads(lock_path.read_text(encoding="utf-8"))["run_id"], first["run_id"])
        self.assertEqual(len(list((self.root / ".ticket-loop" / "runs").glob("*.json"))), 1)

    def test_recoverable_gate_failure_keeps_lock(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "scenario").write_text("no-commit", encoding="utf-8")

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "implementation-no-commit")
        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["state"], "running")
        self.assertEqual(json.loads(self.lock_path().read_text(encoding="utf-8"))["run_id"], run_id)

    def test_implementation_failure_resumes_exact_session_with_targeted_prompt(self):
        started = self.start()
        start_payload = self.payload(started)
        run_id = start_payload["run_id"]
        self.set_scenario("no-commit-once")

        first = self.cli("step", "--run", run_id)
        self.assert_rejected(first, "implementation-no-commit")
        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["allowed_actions"], ["resume", "status", "abort"])

        resumed = self.cli("resume", "--run", run_id)

        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.payload(resumed)["phase"], "closeout")
        calls = self.recorded_calls()
        self.assertEqual(len(calls), 2)
        self.assertIn("--session-id", calls[0])
        self.assertIn("--resume", calls[1])
        self.assertEqual(calls[1][calls[1].index("--resume") + 1], start_payload["session_id"])
        recovery_prompt = calls[1][-1]
        self.assertIn("implementation-no-commit", recovery_prompt)
        self.assertIn("Implementation 没有产生新 commit", recovery_prompt)
        self.assertIn("继续当前 Task Ticket", recovery_prompt)
        self.assertIn("不得开始其他工单", recovery_prompt)
        self.assertIn("完成验证与提交", recovery_prompt)
        self.assertNotIn("/implement", recovery_prompt)
        self.assertNotIn("cost_usd", recovery_prompt)

    def test_recovers_nonzero_invalid_json_and_is_error_results(self):
        scenarios = {
            "nonzero-once": "claude-failed",
            "invalid-json-once": "invalid-claude-json",
            "is-error-once": "claude-reported-error",
        }
        for scenario, code in scenarios.items():
            with self.subTest(scenario=scenario):
                if scenario != "nonzero-once":
                    self.tearDown()
                    self.setUp()
                run_id = self.payload(self.start())["run_id"]
                self.set_scenario(scenario)

                self.assert_rejected(self.cli("step", "--run", run_id), code)
                resumed = self.cli("resume", "--run", run_id)

                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(self.payload(resumed)["phase"], "closeout")
                self.assertIn("--resume", self.recorded_calls()[1])

    def test_timeout_is_recorded_and_resumed_with_configured_deadline(self):
        started = self.start("--implementation-timeout-seconds", "0.1", "--resume-timeout-seconds", "10")
        start_payload = self.payload(started)
        run_id = start_payload["run_id"]
        self.set_scenario("timeout-once")

        timed_out = self.cli("step", "--run", run_id)

        self.assert_rejected(timed_out, "claude-timeout")
        ledger = self.ledger(run_id)
        self.assertEqual(ledger["timeouts"], {"implementation": 0.1, "closeout": 1800.0, "resume": 10.0})
        self.assertTrue(ledger["claude_calls"][0]["timed_out"])
        self.assertEqual(ledger["claude_calls"][0]["timeout_seconds"], 0.1)
        resumed = self.cli("resume", "--run", run_id)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.ledger(run_id)["claude_calls"][1]["timeout_seconds"], 10.0)

    def test_timeout_terminates_the_claude_process_group(self):
        run_id = self.payload(
            self.start("--implementation-timeout-seconds", "0.1")
        )["run_id"]
        self.set_scenario("timeout-child-once")

        self.assert_rejected(self.cli("step", "--run", run_id), "claude-timeout")
        time.sleep(1.2)

        self.assertFalse((self.root / "late-child.txt").exists())

    def test_resume_rejects_dirty_file_content_changed_after_failure(self):
        run_id = self.payload(self.start())["run_id"]
        self.set_scenario("dirty-once")
        self.assert_rejected(self.cli("step", "--run", run_id), "dirty-worktree")
        (self.root / "implementation.txt").write_text("externally changed\n", encoding="utf-8")

        failed = self.cli("resume", "--run", run_id)

        self.assert_rejected(failed, "recovery-context-drift")
        self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")
        self.assertEqual(len(self.recorded_calls()), 1)

    def test_resume_allows_dirty_implementation_workspace_and_commits_it(self):
        for scenario in ("dirty-once", "commit-and-dirty-once"):
            with self.subTest(scenario=scenario):
                if scenario != "dirty-once":
                    self.tearDown()
                    self.setUp()
                run_id = self.payload(self.start())["run_id"]
                self.set_scenario(scenario)

                self.assert_rejected(self.cli("step", "--run", run_id), "dirty-worktree")
                resumed = self.cli("resume", "--run", run_id)

                self.assertEqual(resumed.returncode, 0, resumed.stderr)
                self.assertEqual(self.payload(resumed)["phase"], "closeout")
                self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_implementation_recovery_budget_exhaustion_is_terminal(self):
        start_payload = self.payload(self.start())
        run_id = start_payload["run_id"]
        self.set_scenario("always-error")

        self.assert_rejected(self.cli("step", "--run", run_id), "claude-reported-error")
        self.assert_rejected(self.cli("resume", "--run", run_id), "claude-reported-error")
        exhausted = self.cli("resume", "--run", run_id)

        self.assert_rejected(exhausted, "claude-reported-error")
        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["state"], "terminal-failure")
        self.assertEqual(status["allowed_actions"], ["reopen", "status", "abort"])
        self.assertEqual(self.ledger(run_id)["attempts"]["implementation"], {"initial": 1, "resumes": 2})
        self.assertEqual(len(self.recorded_calls()), 3)
        self.assertFalse(self.lock_path().exists())
        self.assert_rejected(self.cli("resume", "--run", run_id), "run-terminal-failure")

    def test_post_call_branch_drift_becomes_terminal_failure_and_releases_lock(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "scenario").write_text("branch-drift", encoding="utf-8")

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "branch-drift")
        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["state"], "terminal-failure")
        self.assertEqual(status["phase"], "implementation")
        self.assertEqual(status["allowed_actions"], ["reopen", "status", "abort"])
        self.assertFalse(self.lock_path().exists())
        self.assert_rejected(self.cli("step", "--run", run_id), "run-terminal-failure")

    def test_pre_call_dirty_worktree_does_not_invoke_claude(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        (self.root / "outside.txt").write_text("dirty\n", encoding="utf-8")

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "dirty-worktree")
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "running")
        self.assertTrue((self.root / "outside.txt").exists())

    def test_pre_call_branch_drift_and_git_operation_do_not_invoke_claude(self):
        for scenario, code in (("branch", "branch-drift"), ("operation", "git-operation-in-progress")):
            with self.subTest(scenario=scenario):
                if scenario != "branch":
                    self.tearDown()
                    self.setUp()
                started = self.start()
                run_id = self.payload(started)["run_id"]
                if scenario == "branch":
                    subprocess.run(["git", "-C", str(self.root), "switch", "-qc", "other"], check=True)
                else:
                    git_dir = Path(self.git("rev-parse", "--git-dir").strip())
                    if not git_dir.is_absolute():
                        git_dir = self.root / git_dir
                    (git_dir / "MERGE_HEAD").write_text(self.start_head + "\n", encoding="utf-8")

                failed = self.cli("step", "--run", run_id)

                self.assert_rejected(failed, code)
                self.assertFalse(self.calls.exists())
                self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")

    def test_pre_call_detached_head_becomes_terminal_without_invoking_claude(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        subprocess.run(["git", "-C", str(self.root), "checkout", "--detach", "-q"], check=True)

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "branch-drift")
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")

    def test_pre_call_history_rewrite_becomes_terminal_without_invoking_claude(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        tree = self.git("write-tree").strip()
        replacement = subprocess.run(
            ["git", "-C", str(self.root), "commit-tree", tree, "-m", "replacement root"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        branch = self.git("branch", "--show-current").strip()
        subprocess.run(["git", "-C", str(self.root), "update-ref", f"refs/heads/{branch}", replacement], check=True)

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "history-rewritten")
        self.assertFalse(self.calls.exists())
        self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")

    def test_post_call_history_rewrite_and_git_operation_are_terminal(self):
        for scenario, code in (("history-rewrite", "history-rewritten"), ("merge-in-progress", "git-operation-in-progress")):
            with self.subTest(scenario=scenario):
                if scenario != "history-rewrite":
                    self.tearDown()
                    self.setUp()
                started = self.start()
                run_id = self.payload(started)["run_id"]
                (self.root / ".ticket-loop" / "scenario").write_text(scenario, encoding="utf-8")

                failed = self.cli("step", "--run", run_id)

                self.assert_rejected(failed, code)
                self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")
                self.assertFalse(self.lock_path().exists())

    def test_git_drift_takes_priority_over_invalid_claude_json(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "scenario").write_text("branch-drift-invalid-json", encoding="utf-8")

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "branch-drift")
        logs = list((self.root / ".ticket-loop" / "logs" / run_id).glob("*.json"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].read_text(encoding="utf-8"), "not json\n")

    def test_malformed_lock_becomes_structured_terminal_failure(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        self.lock_path().write_text("[]", encoding="utf-8")

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "run-lock-mismatch")
        self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")

    def test_missing_or_replaced_lock_becomes_terminal_without_deleting_foreign_lock(self):
        for replacement in (None, "foreign"):
            with self.subTest(replacement=replacement):
                if replacement == "foreign":
                    self.tearDown()
                    self.setUp()
                started = self.start()
                run_id = self.payload(started)["run_id"]
                self.lock_path().unlink()
                if replacement:
                    self.lock_path().write_text(json.dumps({"run_id": replacement}), encoding="utf-8")

                failed = self.cli("step", "--run", run_id)

                self.assert_rejected(failed, "run-lock-missing" if replacement is None else "run-lock-mismatch")
                self.assertFalse(self.calls.exists())
                self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")
                if replacement:
                    self.assertEqual(json.loads(self.lock_path().read_text(encoding="utf-8"))["run_id"], replacement)

    def test_runs_implementation_and_closeout_in_the_same_persistent_session(self):
        started = self.cli(
            "start",
            "--tickets",
            self.ticket,
            "--claude-executable",
            self.fake_claude,
            "--implement-command",
            "implement",
        )

        self.assertEqual(started.returncode, 0, started.stderr)
        start_payload = self.payload(started)
        self.assertEqual(start_payload["state"], "running")
        self.assertEqual(start_payload["phase"], "implementation")
        self.assertEqual(start_payload["allowed_actions"], ["step", "status", "abort"])
        run_id = start_payload["run_id"]
        session_id = start_payload["session_id"]

        implementation = self.cli("step", "--run", run_id)
        self.assertEqual(implementation.returncode, 0, implementation.stderr)
        implementation_payload = self.payload(implementation)
        self.assertEqual(implementation_payload["phase"], "closeout")
        self.assertEqual(implementation_payload["state"], "running")
        self.assertTrue(self.git("merge-base", "--is-ancestor", self.start_head, "HEAD") == "")

        closeout = self.cli("step", "--run", run_id)
        self.assertEqual(closeout.returncode, 0, closeout.stderr)
        closeout_payload = self.payload(closeout)
        self.assertEqual(closeout_payload["state"], "completed")
        self.assertIsNone(closeout_payload["phase"])
        self.assertEqual(closeout_payload["allowed_actions"], ["status", "clean"])
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), "")
        self.assertEqual(self.git("log", "-1", "--pretty=%s").strip(), "chore(ticket-loop): close 02")
        self.assertTrue((self.root / ".ticket-loop" / "runs" / f"{run_id}.json").is_file())
        self.assertIn(".ticket-loop/", (self.root / ".git" / "info" / "exclude").read_text(encoding="utf-8"))

        status = self.cli("status", "--run", run_id)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(self.payload(status)["state"], "completed")

        calls = [json.loads(line) for line in self.calls.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][-1], f"/implement @{self.ticket.resolve()}")
        self.assertIn("--session-id", calls[0])
        self.assertEqual(calls[0][calls[0].index("--session-id") + 1], session_id)
        self.assertIn("--resume", calls[1])
        self.assertEqual(calls[1][calls[1].index("--resume") + 1], session_id)
        self.assertNotIn("--session-id", calls[1])

    def test_closeout_creates_no_empty_commit_when_implementation_already_closed_ticket(self):
        started = self.cli(
            "start",
            "--tickets",
            self.ticket,
            "--claude-executable",
            self.fake_claude,
        )
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "scenario").write_text("already-closed", encoding="utf-8")

        implementation = self.cli("step", "--run", run_id)
        self.assertEqual(implementation.returncode, 0, implementation.stderr)
        before_closeout = self.git("rev-parse", "HEAD").strip()
        closeout = self.cli("step", "--run", run_id)

        self.assertEqual(closeout.returncode, 0, closeout.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), before_closeout)
        self.assertEqual(self.payload(closeout)["state"], "completed")

    def test_closeout_recovery_has_an_independent_budget(self):
        start_payload = self.payload(self.start())
        run_id = start_payload["run_id"]
        self.assertEqual(self.cli("step", "--run", run_id).returncode, 0)
        self.set_scenario("closeout-always-error")

        self.assert_rejected(self.cli("step", "--run", run_id), "claude-reported-error")
        self.assert_rejected(self.cli("resume", "--run", run_id), "claude-reported-error")
        exhausted = self.cli("resume", "--run", run_id)

        self.assert_rejected(exhausted, "claude-reported-error")
        ledger = self.ledger(run_id)
        self.assertEqual(ledger["state"], "terminal-failure")
        self.assertEqual(ledger["attempts"]["implementation"], {"initial": 1, "resumes": 0})
        self.assertEqual(ledger["attempts"]["closeout"], {"initial": 1, "resumes": 2})

    def test_closeout_dirty_workspace_resumes_to_finish_evidence_and_commit(self):
        run_id = self.payload(self.start())["run_id"]
        self.set_scenario("closeout-dirty-once")
        self.assertEqual(self.cli("step", "--run", run_id).returncode, 0)

        failed = self.cli("step", "--run", run_id)
        self.assert_rejected(failed, "dirty-worktree")
        resumed = self.cli("resume", "--run", run_id)

        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        self.assertEqual(self.payload(resumed)["state"], "completed")
        recovery_prompt = self.recorded_calls()[2][-1]
        self.assertIn("dirty-worktree", recovery_prompt)
        self.assertIn("closeout", recovery_prompt.lower())
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), "")

    def test_closeout_missing_evidence_stays_running_with_a_structured_gate_failure(self):
        started = self.cli(
            "start",
            "--tickets",
            self.ticket,
            "--claude-executable",
            self.fake_claude,
        )
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "scenario").write_text("invalid-evidence", encoding="utf-8")
        self.assertEqual(self.cli("step", "--run", run_id).returncode, 0)

        closeout = self.cli("step", "--run", run_id)

        self.assertEqual(closeout.returncode, 2)
        self.assertEqual(self.payload(closeout)["error"]["code"], "invalid-completion-evidence")
        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["state"], "running")
        self.assertEqual(status["phase"], "closeout")
        self.assertEqual(status["gate_failures"][0]["code"], "invalid-completion-evidence")

    def test_status_reports_attempts_calls_and_remains_read_only(self):
        run_id = self.payload(self.start())["run_id"]
        before = self.ledger(run_id)

        status = self.cli("status", "--run", run_id)

        self.assertEqual(status.returncode, 0, status.stderr)
        payload = self.payload(status)
        self.assertEqual(payload["attempts"], before["attempts"])
        self.assertEqual(payload["claude_calls"], [])
        self.assertEqual(self.ledger(run_id), before)
        self.assertFalse(self.calls.exists())

    def test_abort_stops_only_scheduling_and_preserves_git_and_run_artifacts(self):
        run_id = self.payload(self.start())["run_id"]
        head = self.git("rev-parse", "HEAD").strip()
        status = self.git("status", "--porcelain=v1", "--untracked-files=all")

        aborted = self.cli("abort", "--run", run_id)

        self.assertEqual(aborted.returncode, 0, aborted.stderr)
        payload = self.payload(aborted)
        self.assertEqual(payload["state"], "aborted")
        self.assertEqual(payload["allowed_actions"], ["status", "clean"])
        self.assertFalse(self.lock_path().exists())
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), head)
        self.assertEqual(self.git("status", "--porcelain=v1", "--untracked-files=all"), status)
        self.assertEqual(self.ledger(run_id)["session_id"], payload["session_id"])
        self.assertTrue((self.root / ".ticket-loop" / "runs" / f"{run_id}.json").exists())
        self.assertFalse(self.calls.exists())

    def test_clean_requires_terminal_run_and_removes_only_selected_ledger_and_logs(self):
        first = self.payload(self.start())["run_id"]
        self.assert_rejected(self.cli("clean", "--run", first), "run-not-terminal")
        self.assertEqual(self.cli("abort", "--run", first).returncode, 0)
        first_logs = self.root / ".ticket-loop" / "logs" / first
        first_logs.mkdir(parents=True)
        (first_logs / "kept-until-clean.log").write_text("log", encoding="utf-8")
        unrelated = self.root / ".ticket-loop" / "logs" / "unrelated"
        unrelated.mkdir()
        (unrelated / "keep.log").write_text("keep", encoding="utf-8")

        cleaned = self.cli("clean", "--run", first)

        self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
        payload = self.payload(cleaned)
        self.assertEqual(payload["command"], "clean")
        self.assertFalse((self.root / ".ticket-loop" / "runs" / f"{first}.json").exists())
        self.assertFalse(first_logs.exists())
        self.assertEqual((unrelated / "keep.log").read_text(encoding="utf-8"), "keep")

    def test_reopen_terminal_failure_revalidates_context_and_never_creates_a_session(self):
        run_id = self.payload(self.start())["run_id"]
        self.set_scenario("always-error")
        self.assert_rejected(self.cli("step", "--run", run_id), "claude-reported-error")
        self.assert_rejected(self.cli("resume", "--run", run_id), "claude-reported-error")
        self.assert_rejected(self.cli("resume", "--run", run_id), "claude-reported-error")
        calls_before = self.recorded_calls()

        reopened = self.cli("reopen", "--run", run_id)

        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        payload = self.payload(reopened)
        self.assertEqual(payload["state"], "running")
        self.assertEqual(payload["allowed_actions"], ["resume", "status", "abort"])
        self.assertEqual(self.recorded_calls(), calls_before)
        self.assertEqual(self.ledger(run_id)["session_id"], payload["session_id"])
        self.assertEqual(json.loads(self.lock_path().read_text(encoding="utf-8"))["run_id"], run_id)

    def test_reopen_fails_closed_when_repository_context_changed(self):
        run_id = self.payload(self.start())["run_id"]
        self.set_scenario("no-commit")
        self.assert_rejected(self.cli("step", "--run", run_id), "implementation-no-commit")
        self.assertEqual(self.cli("abort", "--run", run_id).returncode, 0)
        subprocess.run(["git", "-C", str(self.root), "switch", "-qc", "other"], check=True)

        self.assert_rejected(self.cli("reopen", "--run", run_id), "branch-drift")
        self.assertFalse(self.lock_path().exists())
        self.assertEqual(self.ledger(run_id)["state"], "aborted")

    def test_reopen_rejects_run_whose_session_was_never_created(self):
        run_id = self.payload(self.start())["run_id"]
        self.assertEqual(self.cli("abort", "--run", run_id).returncode, 0)

        self.assert_rejected(self.cli("reopen", "--run", run_id), "session-unavailable")
        self.assertFalse(self.calls.exists())
        self.assertFalse(self.lock_path().exists())

    def test_stale_lock_marks_running_run_interrupted_and_unique_step_auto_selects_it(self):
        run_id = self.payload(self.start())["run_id"]
        lock = json.loads(self.lock_path().read_text(encoding="utf-8"))
        lock["active_pid"] = 99999999
        self.lock_path().write_text(json.dumps(lock), encoding="utf-8")

        interrupted = self.cli("step")

        self.assert_rejected(interrupted, "run-interrupted")
        payload = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(payload["state"], "interrupted")
        self.assertEqual(payload["allowed_actions"], ["reopen", "status", "abort"])
        self.assertFalse(self.lock_path().exists())
        archived = list((self.lock_path().parent / "stale").glob("*.json"))
        self.assertEqual(len(archived), 1)

    def test_implicit_run_selection_fails_closed_with_multiple_candidates(self):
        first = self.payload(self.start())["run_id"]
        self.assertEqual(self.cli("abort", "--run", first).returncode, 0)
        first_path = self.root / ".ticket-loop" / "runs" / f"{first}.json"
        first_ledger = json.loads(first_path.read_text(encoding="utf-8"))
        first_ledger["state"] = "interrupted"
        first_path.write_text(json.dumps(first_ledger), encoding="utf-8")
        second = self.payload(self.start())["run_id"]
        self.assertEqual(self.cli("abort", "--run", second).returncode, 0)
        second_path = self.root / ".ticket-loop" / "runs" / f"{second}.json"
        second_ledger = json.loads(second_path.read_text(encoding="utf-8"))
        second_ledger["state"] = "terminal-failure"
        second_path.write_text(json.dumps(second_ledger), encoding="utf-8")

        ambiguous = self.cli("status")

        self.assert_rejected(ambiguous, "multiple-incomplete-runs")
        payload = self.payload(ambiguous)
        self.assertIn(first, payload["error"]["message"])
        self.assertIn(second, payload["error"]["message"])
        self.assertIn("--run", payload["error"]["message"])

    def test_sigterm_interrupts_child_records_run_and_releases_lock(self):
        run_id = self.payload(self.start())["run_id"]
        self.set_scenario("interruptible")
        process = subprocess.Popen(
            [sys.executable, str(CLI), "step", "--run", run_id],
            cwd=self.root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ready = self.root / ".ticket-loop" / "claude-ready"
        deadline = time.time() + 5
        while not ready.exists() and time.time() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists())

        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)

        self.assertEqual(process.returncode, 130, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["error"]["code"], "run-interrupted")
        ledger = self.ledger(run_id)
        self.assertEqual(ledger["state"], "interrupted")
        self.assertEqual(ledger["claude_calls"][-1]["interrupted_by"], "SIGTERM")
        self.assertFalse(self.lock_path().exists())
        self.assertEqual(ledger["session_id"], self.payload(self.cli("status", "--run", run_id))["session_id"])


class TicketLoopManifestRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "manifest repo"
        self.issues = self.root / "issues"
        self.issues.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        self.fake_claude = Path(self.temp.name) / "manifest-claude.py"
        self.fake_claude.write_text(
            '''#!/usr/bin/env python3
import json
from pathlib import Path
import re
import subprocess
import sys

args = sys.argv[1:]
root = Path.cwd()
session_flag = "--resume" if "--resume" in args else "--session-id"
session_id = args[args.index(session_flag) + 1]
prompt = args[-1]
sessions_path = root / ".ticket-loop" / "fake-sessions.json"
sessions = json.loads(sessions_path.read_text(encoding="utf-8")) if sessions_path.exists() else {}
if prompt.startswith("/"):
    ticket = Path(prompt.split(" @", 1)[1])
    sessions[session_id] = str(ticket)
    sessions_path.write_text(json.dumps(sessions), encoding="utf-8")
    phase = "implementation"
else:
    ticket = Path(sessions[session_id])
    phase = "implementation" if "阶段: implementation" in prompt else "closeout"
ticket_id = ticket.name.split("-", 1)[0]
calls_path = root / ".ticket-loop" / "manifest-calls.jsonl"
with calls_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"args": args, "ticket": ticket_id, "phase": phase}) + "\\n")
scenario_path = root / ".ticket-loop" / "manifest-scenario.json"
scenarios = json.loads(scenario_path.read_text(encoding="utf-8")) if scenario_path.exists() else {}
scenario = scenarios.get(ticket_id, "success")
if scenario == "always-error" and phase == "implementation":
    print(json.dumps({"type": "error"}))
    raise SystemExit(0)
if phase == "implementation":
    implementation = root / f"implementation-{ticket_id}.txt"
    implementation.write_text(f"implemented {ticket_id}\\n", encoding="utf-8")
    subprocess.run(["git", "add", str(implementation)], check=True)
    if scenario == "add-ticket":
        added = ticket.parent / "03-added-during-run.md"
        added.write_text("# 03: 新增\\n\\n**What to build:** 不应执行\\n\\n**Blocked by:** None\\n\\n**Status:** ready-for-agent\\n\\n- [ ] 验收新增\\n", encoding="utf-8")
        subprocess.run(["git", "add", str(added)], check=True)
    if scenario == "modify-external":
        external = ticket.parent / "01-external.md"
        external.write_text(external.read_text(encoding="utf-8") + "\\nmodified\\n", encoding="utf-8")
        subprocess.run(["git", "add", str(external)], check=True)
    subprocess.run(["git", "commit", "-qm", f"feat: implement {ticket_id}"], check=True)
else:
    implementation = subprocess.run(["git", "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
    text = ticket.read_text(encoding="utf-8")
    text = text.replace("**Status:** ready-for-agent", f"**Status:** done (2026-09-15, {implementation[:7]})")
    text = re.sub(r"^- \\[ \\]", "- [x]", text, flags=re.MULTILINE)
    text += "\\n## Completion evidence\\n\\n- Implementation: implementation commit\\n- Typecheck: Not applicable (fixture)\\n- Tests: lifecycle passed\\n- Review: reviewed\\n"
    ticket.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", str(ticket)], check=True)
    subprocess.run(["git", "commit", "-qm", f"chore(ticket-loop): close {ticket_id}"], check=True)
print(json.dumps({"type": "result", "cost_usd": 0.01}))
''',
            encoding="utf-8",
        )
        self.fake_claude.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def write_ticket(self, number, *, blocked_by="None", status="ready-for-agent", title=None):
        path = self.issues / f"{number}-{title or number}.md"
        path.write_text(task(number, status=status, blocked_by=blocked_by, title=title or number), encoding="utf-8")
        return path

    def commit_fixture(self):
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)

    def cli(self, command, *args):
        return subprocess.run(
            [sys.executable, str(CLI), command, *map(str, args)],
            cwd=self.root,
            text=True,
            capture_output=True,
        )

    def payload(self, result):
        self.assertEqual(result.stdout.count("\n"), 1, result.stdout)
        return json.loads(result.stdout)

    def start(self, *tickets):
        return self.cli(
            "start",
            "--tickets",
            *tickets,
            "--claude-executable",
            self.fake_claude,
        )

    def calls(self):
        path = self.root / ".ticket-loop" / "manifest-calls.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def run_to_end(self, run_id):
        while True:
            status = self.payload(self.cli("status", "--run", run_id))
            if status["state"] != "running":
                return status
            action = status["allowed_actions"][0]
            result = self.cli(action, "--run", run_id)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_runs_linear_chain_with_a_fresh_session_per_ticket(self):
        one = self.write_ticket("01")
        two = self.write_ticket("02", blocked_by="01")
        self.commit_fixture()

        started = self.start(two, one)
        self.assertEqual(started.returncode, 0, started.stderr)
        run_id = self.payload(started)["run_id"]
        final = self.run_to_end(run_id)

        self.assertEqual(final["state"], "completed")
        calls = self.calls()
        self.assertEqual([(call["ticket"], call["phase"]) for call in calls], [
            ("01", "implementation"), ("01", "closeout"),
            ("02", "implementation"), ("02", "closeout"),
        ])
        session_ids = [call["args"][call["args"].index("--session-id") + 1] for call in calls if "--session-id" in call["args"]]
        self.assertEqual(len(session_ids), 2)
        self.assertNotEqual(session_ids[0], session_ids[1])
        self.assertEqual(subprocess.run(["git", "-C", str(self.root), "status", "--porcelain=v1", "--untracked-files=all"], text=True, capture_output=True, check=True).stdout, "")

    def test_manifest_done_ticket_satisfies_a_ready_ticket_dependency(self):
        one = self.write_ticket("01", status="done (2026-09-14, abc1234)")
        two = self.write_ticket("02", blocked_by="01")
        self.commit_fixture()

        started = self.start(one, two)
        self.assertEqual(started.returncode, 0, started.stderr)
        run_id = self.payload(started)["run_id"]
        final = self.run_to_end(run_id)

        self.assertEqual(final["state"], "completed")
        self.assertEqual([call["ticket"] for call in self.calls() if call["phase"] == "implementation"], ["02"])

    def test_forked_dag_always_chooses_the_lowest_frontier(self):
        one = self.write_ticket("01")
        two = self.write_ticket("02", blocked_by="01")
        four = self.write_ticket("04", blocked_by="02")
        five = self.write_ticket("05", blocked_by="01")
        self.commit_fixture()

        run_id = self.payload(self.start(five, four, two, one))["run_id"]
        final = self.run_to_end(run_id)

        self.assertEqual(final["state"], "completed")
        self.assertEqual([call["ticket"] for call in self.calls() if call["phase"] == "implementation"], ["01", "02", "04", "05"])

    def test_external_blocker_is_never_executed_and_empty_frontier_stops_run(self):
        external = self.write_ticket("01")
        two = self.write_ticket("02")
        three = self.write_ticket("03", blocked_by="01")
        self.commit_fixture()
        original = external.read_text(encoding="utf-8")

        run_id = self.payload(self.start(two, three))["run_id"]
        self.assertEqual(self.cli("step", "--run", run_id).returncode, 0)
        blocked = self.cli("step", "--run", run_id)

        self.assertEqual(blocked.returncode, 2)
        self.assertEqual(self.payload(blocked)["error"]["code"], "frontier-empty")
        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["state"], "terminal-failure")
        self.assertEqual([call["ticket"] for call in self.calls()], ["02", "02"])
        self.assertEqual(external.read_text(encoding="utf-8"), original)

    def test_modifying_an_external_blocker_is_a_terminal_failure(self):
        external = self.write_ticket("01", status="done (2026-09-14, abc1234)", title="external")
        target = self.write_ticket("02", blocked_by="01")
        self.commit_fixture()

        started = self.start(target)
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "manifest-scenario.json").write_text(json.dumps({"02": "modify-external"}), encoding="utf-8")
        failed = self.cli("step", "--run", run_id)

        self.assertEqual(failed.returncode, 2)
        self.assertEqual(self.payload(failed)["error"]["code"], "external-blocker-modified")
        self.assertEqual(self.payload(self.cli("status", "--run", run_id))["state"], "terminal-failure")
        self.assertNotIn("done (2026-09-15", target.read_text(encoding="utf-8"))
        self.assertIn("modified", external.read_text(encoding="utf-8"))

    def test_ticket_added_during_run_is_not_absorbed_into_frozen_manifest(self):
        one = self.write_ticket("01")
        two = self.write_ticket("02", blocked_by="01")
        self.commit_fixture()
        scenario = self.root / ".ticket-loop" / "manifest-scenario.json"

        started = self.start(one, two)
        run_id = self.payload(started)["run_id"]
        scenario.write_text(json.dumps({"01": "add-ticket"}), encoding="utf-8")
        final = self.run_to_end(run_id)

        self.assertEqual(final["state"], "completed")
        self.assertEqual([call["ticket"] for call in self.calls() if call["phase"] == "implementation"], ["01", "02"])
        self.assertIn("ready-for-agent", (self.issues / "03-added-during-run.md").read_text(encoding="utf-8"))

    def test_terminal_failure_on_current_ticket_never_starts_the_next_ticket(self):
        one = self.write_ticket("01")
        two = self.write_ticket("02", blocked_by="01")
        self.commit_fixture()

        started = self.start(one, two)
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "manifest-scenario.json").write_text(json.dumps({"01": "always-error"}), encoding="utf-8")
        for _ in range(3):
            result = self.cli("step" if not self.calls() else "resume", "--run", run_id)
            self.assertEqual(result.returncode, 2)

        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["state"], "terminal-failure")
        self.assertEqual(status["active_ticket"], "01")
        self.assertEqual({call["ticket"] for call in self.calls()}, {"01"})


if __name__ == "__main__":
    unittest.main()
