"""匿名合成调度事件回归；不启动子代理、网络或模型。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/mia-eval/scripts/dispatch_queue.py"
SPEC = importlib.util.spec_from_file_location("mia_eval_dispatch_queue", SCRIPT)
queue = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(queue)


class QueueTests(unittest.TestCase):
    def start(self, state, key, worker):
        queue.apply(state, "reserve", key)
        return queue.apply(state, "started", key, worker, ordinal=state["cases"][key]["ordinal"])

    def test_zero_cases_has_no_pending_or_active_work(self):
        state = queue.new_queue([], 3)
        result = queue.status(state)
        self.assertEqual(result["pending"], [])
        self.assertEqual(result["active"], [])
        self.assertEqual(result["finished"], [])
        self.assertEqual(result["available"], 3)

    def test_requested_capacity_and_remaining_cases(self):
        state = queue.new_queue(["case-1", "case-2", "case-3"], 30)
        self.assertEqual(queue.status(state)["limit"], 30)
        queue.apply(state, "capacity", capacity=2)
        self.start(state, "case-1", "worker-a")
        self.start(state, "case-2", "worker-b")
        with self.assertRaisesRegex(queue.QueueError, "没有空槽位"):
            queue.apply(state, "reserve", "case-3")
        queue.apply(state, "finished", "case-2", "worker-b")
        self.start(state, "case-3", "worker-c")
        self.assertEqual(state["requested"], 30)
        self.assertEqual(queue.status(state)["active"], ["case-1", "case-3"])

    def test_identity_uses_ack_not_parallel_result_order(self):
        state = queue.new_queue(["case-a", "case-b"], 2)
        queue.apply(state, "reserve", "case-a")
        queue.apply(state, "reserve", "case-b")
        # 回传顺序反转仍由case_key明确绑定。
        with self.assertRaisesRegex(queue.QueueError, "ordinal.*不一致"):
            queue.apply(state, "started", "case-b", "worker-first-result", ordinal=1)
        self.assertEqual(state["cases"]["case-b"]["state"], "reserved")
        queue.apply(state, "started", "case-b", "worker-first-result", ordinal=2)
        queue.apply(state, "started", "case-a", "worker-second-result", ordinal=1)
        with self.assertRaisesRegex(queue.QueueError, "不一致"):
            queue.apply(state, "finished", "case-a", "worker-first-result")
        self.assertEqual(queue.status(state)["available"], 0)
        queue.apply(state, "finished", "case-b", "worker-first-result")
        self.assertEqual(queue.status(state)["available"], 1)

    def test_repair_waits_and_occupies_same_slot(self):
        state = queue.new_queue(["case-a", "case-b"], 1)
        self.start(state, "case-a", "worker-a")
        queue.apply(state, "finished", "case-a", "worker-a")
        self.start(state, "case-b", "worker-b")
        queue.apply(state, "repair-needed", "case-a")
        with self.assertRaisesRegex(queue.QueueError, "没有空槽位"):
            queue.apply(state, "reserve-repair", "case-a", "worker-a")
        self.assertEqual(state["cases"]["case-a"]["state"], "repair-pending")
        queue.apply(state, "finished", "case-b", "worker-b")
        with self.assertRaisesRegex(queue.QueueError, "原 worker"):
            queue.apply(state, "reserve-repair", "case-a", "new-worker")
        queue.apply(state, "reserve-repair", "case-a", "worker-a")
        self.assertEqual(queue.status(state)["available"], 0)
        queue.apply(state, "resume-rejected", "case-a", "worker-a")
        self.assertEqual(queue.status(state)["available"], 1)
        self.assertEqual(state["cases"]["case-a"]["state"], "repair-pending")
        queue.apply(state, "reserve-repair", "case-a", "worker-a")
        queue.apply(state, "resume", "case-a", "worker-a")
        self.assertEqual(queue.status(state)["available"], 0)
        self.assertEqual(state["cases"]["case-a"]["attempt"], 2)
        queue.apply(state, "finished", "case-a", "worker-a")
        self.assertEqual(queue.status(state)["available"], 1)

    def test_report_ready_does_not_release_slot_before_host_completion(self):
        state = queue.new_queue(["case-a", "case-b"], 1)
        self.start(state, "case-a", "worker-a")
        queue.apply(state, "report-ready", "case-a", "worker-a")
        self.assertEqual(queue.status(state)["active"], ["case-a"])
        self.assertEqual(queue.status(state)["available"], 0)
        with self.assertRaisesRegex(queue.QueueError, "没有空槽位"):
            queue.apply(state, "reserve", "case-b")
        with self.assertRaises(queue.QueueError):
            queue.apply(state, "repair-needed", "case-a")
        with self.assertRaises(queue.QueueError):
            queue.apply(state, "insufficient", "case-a")
        queue.apply(state, "finished", "case-a", "worker-a")
        self.assertEqual(queue.status(state)["available"], 1)

    def test_legacy_queue_can_finish_but_not_guess_missing_ordinal(self):
        state = queue.new_queue(["case-a", "case-b"], 2)
        self.start(state, "case-a", "worker-a")
        for row in state["cases"].values():
            del row["ordinal"]
        self.assertEqual(queue.status(state)["active"], ["case-a"])
        queue.apply(state, "finished", "case-a", "worker-a")
        queue.apply(state, "reserve", "case-b")
        with self.assertRaisesRegex(queue.QueueError, "缺 ordinal"):
            queue.apply(state, "started", "case-b", "worker-b", ordinal=2)

    def test_duplicate_completion_does_not_release_another_slot(self):
        state = queue.new_queue(["case-a", "case-b"], 1)
        self.start(state, "case-a", "worker-a")
        queue.apply(state, "finished", "case-a", "worker-a")
        self.start(state, "case-b", "worker-b")
        with self.assertRaises(queue.QueueError):
            queue.apply(state, "finished", "case-a", "worker-a")
        self.assertEqual(queue.status(state)["available"], 0)

    def test_rejection_preserves_case_and_insufficient_denominator(self):
        state = queue.new_queue(["case-a", "case-b"], 2)
        queue.apply(state, "reserve", "case-a")
        queue.apply(state, "start-rejected", "case-a")
        self.assertEqual(queue.status(state)["pending"], ["case-a", "case-b"])
        self.start(state, "case-a", "worker-a")
        queue.apply(state, "finished", "case-a", "worker-a")
        queue.apply(state, "insufficient", "case-a")
        self.assertEqual(len(state["cases"]), 2)
        self.assertIn("case-a", queue.status(state)["finished"])

    def test_lower_capacity_blocks_new_starts_without_killing_existing(self):
        state = queue.new_queue(["a", "b", "c"], 3)
        self.start(state, "a", "w-a")
        self.start(state, "b", "w-b")
        queue.apply(state, "capacity", capacity=1)
        self.assertEqual(queue.status(state)["available"], 0)
        self.assertEqual(queue.status(state)["active"], ["a", "b"])
        with self.assertRaises(queue.QueueError):
            queue.apply(state, "reserve", "c")

    def test_invalid_requests_and_worker_reuse(self):
        for value in (0, -1, True, "3"):
            with self.subTest(value=value), self.assertRaises(queue.QueueError):
                queue.new_queue(["case"], value)
        with self.assertRaises(queue.QueueError):
            queue.new_queue(["case", "case"], 3)
        for ordinals in ([1], [1, 1], [0, 2], [True, 2]):
            with self.subTest(ordinals=ordinals), self.assertRaises(queue.QueueError):
                queue.new_queue(["a", "b"], 2, ordinals=ordinals)
        state = queue.new_queue(["a", "b"], 2)
        self.start(state, "a", "same-worker")
        queue.apply(state, "reserve", "b")
        with self.assertRaises(queue.QueueError):
            queue.apply(state, "started", "b", "same-worker", ordinal=2)

    def test_cli_private_state_and_reinitialization_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"cases": [{"case_key": "case-a", "ordinal": 7}]}))
            state = root / "dispatch.json"
            command = [sys.executable, "-I", "-B", str(SCRIPT), "--state", str(state), "init", "--manifest", str(manifest), "--requested", "8"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["limit"], 8)
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(state.read_text())["cases"]["case-a"]["ordinal"], 7)
            second = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(second.returncode, 2)
            prefix = command[:6]
            reserved = subprocess.run(prefix + ["reserve", "--case-key", "case-a"], capture_output=True, text=True)
            self.assertEqual(reserved.returncode, 0, reserved.stderr)
            wrong = subprocess.run(prefix + ["started", "--case-key", "case-a", "--worker-id", "worker-a", "--ordinal", "1"], capture_output=True, text=True)
            self.assertEqual(wrong.returncode, 2)
            self.assertEqual(json.loads(state.read_text())["cases"]["case-a"]["state"], "reserved")
            started = subprocess.run(prefix + ["started", "--case-key", "case-a", "--worker-id", "worker-a", "--ordinal", "7"], capture_output=True, text=True)
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(json.loads(started.stdout)["available"], 7)
            link = root / "alias.json"
            link.symlink_to(state)
            result = subprocess.run(command[:5] + [str(link), "status"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
