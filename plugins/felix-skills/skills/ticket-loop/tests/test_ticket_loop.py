import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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

args = sys.argv[1:]
root = Path.cwd()
with (root / ".ticket-loop" / "claude-calls.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(args, ensure_ascii=False) + "\\n")
prompt = args[-1]
scenario_path = root / ".ticket-loop" / "scenario"
scenario = scenario_path.read_text(encoding="utf-8").strip() if scenario_path.exists() else "default"
ticket = root / "需求 空间" / "issues" / "02-单工单.md"
if prompt.startswith("/implement "):
    if scenario == "no-commit":
        print(json.dumps({"type": "result", "cost_usd": 0.01}))
        raise SystemExit(0)
    (root / "implementation.txt").write_text("implemented\\n", encoding="utf-8")
    subprocess.run(["git", "add", "implementation.txt"], check=True)
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

    def start(self):
        return self.cli(
            "start",
            "--tickets",
            self.ticket,
            "--claude-executable",
            self.fake_claude,
        )

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

    def test_post_call_branch_drift_becomes_terminal_failure_and_releases_lock(self):
        started = self.start()
        run_id = self.payload(started)["run_id"]
        (self.root / ".ticket-loop" / "scenario").write_text("branch-drift", encoding="utf-8")

        failed = self.cli("step", "--run", run_id)

        self.assert_rejected(failed, "branch-drift")
        status = self.payload(self.cli("status", "--run", run_id))
        self.assertEqual(status["state"], "terminal-failure")
        self.assertEqual(status["phase"], "implementation")
        self.assertEqual(status["allowed_actions"], ["status"])
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
        self.assertEqual(start_payload["allowed_actions"], ["step", "status"])
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
        self.assertEqual(closeout_payload["allowed_actions"], ["status"])
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


if __name__ == "__main__":
    unittest.main()
