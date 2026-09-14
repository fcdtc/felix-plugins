import json
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

    def plan(self, *args):
        return subprocess.run(
            [sys.executable, str(CLI), "plan", *map(str, args)],
            cwd=self.root,
            text=True,
            capture_output=True,
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


if __name__ == "__main__":
    unittest.main()
