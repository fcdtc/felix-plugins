"""freeze/packet 的离线回归；只用匿名合成数据，不连接 MIA 或模型。"""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1] / "skills/mia-eval"
SCRIPT = SKILL_ROOT / "scripts/prepare_audit.py"
SPEC = importlib.util.spec_from_file_location("mia_eval_prepare", SCRIPT)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def status():
    return {"status_code": 0, "status_msg": "anonymous synthetic"}


def complete_debug(message_id="message-1", session_id="session-1", thinking=False):
    prompt = {"message_list": [{"role": "system", "content": "合成系统"}, {"role": "user", "content": '{"keep":"text"}'}],
              "prompt_parts": {"system_prompt": "合成系统"}, "user_message": {"role": "user", "content": "合成请求"}}
    later = copy.deepcopy(prompt)
    later["message_list"].extend([{"role": "assistant", "content": "合成调用"}, {"role": "tool", "content": "合成回包"}])
    events = [
        {"event_id": "event-1", "session_id": session_id, "run_id": "run-not-message", "sequence": 1, "type": 991,
         "payload": {"prompt_built_payload": prompt}},
        {"event_id": "event-2", "session_id": session_id, "sequence": 2, "type": 992,
         "payload": {"tool_payload": {"tool_call_id": "call-1", "name": "synthetic-tool", "request": '{"input":"leave as string"}', "status": 99123}}},
        {"event_id": "event-3", "session_id": session_id, "sequence": 3, "type": 993,
         "payload": {"tool_payload": {"tool_call_id": "call-1", "name": "synthetic-tool", "output": '{"text":"leave as string"}', "status": 99234}}},
        {"event_id": "event-4", "session_id": session_id, "sequence": 4, "type": 991,
         "payload": {"prompt_built_payload": later}},
        {"event_id": "event-5", "session_id": session_id, "sequence": 5, "type": 999,
         "payload": {"observation_payload": {"usage_payload": {"total_tokens": 12}}}},
        {"event_id": "event-6", "session_id": session_id, "sequence": 6, "type": 1000,
         "payload": {"run_finished_payload": {"final_answer": "合成结果"}, "run_lifecycle_payload": {"status": 87654}}},
    ]
    if thinking:
        events.insert(2, {"event_id": "thinking-1", "session_id": session_id, "type": 9999,
                          "payload": {"thinking_payload": {"text": "匿名合成思考，仅按需读取。"}}})
    return {"session_id": session_id, "message_id": message_id,
            "sp": "合成系统", "pe": '{"keep":"natural language"}',
            "raw_events_json": json.dumps(events, ensure_ascii=False),
            "runtime_steps_json": json.dumps([{"type": "prompt_built", "prompt_built": prompt},
                                               {"type": "prompt_built", "prompt_built": later}], ensure_ascii=False),
            "tool_calls_json": json.dumps([{"tool_call_id": "call-1", "name": "synthetic-tool", "request": '{"input":"leave as string"}',
                                            "output": '{"text":"leave as string"}', "status": "future-status"}]),
            "runtime_usage_json": json.dumps({"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12,
                                               "token_usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}}),
            "token_usage_json": json.dumps({"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12})}


def history(*debugs, has_more=False):
    debugs = debugs or (complete_debug(),)
    return {"status_info": status(), "has_more": has_more,
            "message_indexes": [debug["message_id"] for debug in debugs],
            "messages": [{"message_id": debug["message_id"], "session_id": debug["session_id"], "role": "assistant",
                          "content": '{"this":"is natural language"}', "debug": debug} for debug in debugs]}


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def dump(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def load(self, out, name="index.json"):
        return json.loads((out / name).read_text(encoding="utf-8"))

    def cli(self, *args, isolated=True, cwd=None):
        command = [sys.executable] + (["-I"] if isolated else []) + ["-B", str(SCRIPT)] + list(map(str, args))
        return subprocess.run(command, capture_output=True, text=True, cwd=cwd, check=False)

    def packet(self, value, debugs=(), name="packet"):
        path = self.dump(name + "-input/history.json", value)
        debug_paths = [self.dump(name + "-input/debug-%d.json" % i, d) for i, d in enumerate(debugs)]
        original = {p: p.read_bytes() for p in [path] + debug_paths}
        out = self.root / name
        args = ["packet", "--history", path, "--out", out]
        for debug in debug_paths:
            args.extend(["--debug", debug])
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        for p, body in original.items():
            self.assertEqual(p.read_bytes(), body)
        return out, self.load(out)

    def skill_copy(self):
        dest = self.root / "trusted-skill"
        dest.mkdir()
        shutil.copy2(SKILL_ROOT / "SKILL.md", dest / "SKILL.md")
        for directory in ("references", "scripts", "templates"):
            shutil.copytree(SKILL_ROOT / directory, dest / directory, ignore=shutil.ignore_patterns("__pycache__"))
        return dest

    def pointer(self, index, ref):
        value = json.loads(Path(index["sources"][ref["source"]]["path"]).read_text())
        for pointer in ref["locator"]:
            if isinstance(value, str):
                value = json.loads(value)
            if not pointer:
                continue
            for key in pointer[1:].split("/"):
                key = key.replace("~1", "/").replace("~0", "~")
                value = value[int(key)] if isinstance(value, list) else value[key]
        return value

    def test_freeze_keeps_exact_resources_hashes_example_and_contract(self):
        skill = self.skill_copy()
        (skill / "templates/nested").mkdir()
        (skill / "templates/nested/fixture.bin").write_bytes(b"\x00anonymous\xff")
        run = self.root / "private-run"
        (run / "acquisition").mkdir(parents=True)
        scope1 = self.root / "approved-rule.md"
        scope1.write_text("匿名补充规则", encoding="utf-8")
        scope2 = self.root / "approved-second.txt"
        scope2.write_text("匿名第二规则", encoding="utf-8")
        result = self.cli("freeze", "--skill-root", skill, "--run-dir", run,
                          "--scope-file", scope1, "--scope-file", scope2)
        self.assertEqual(result.returncode, 0, result.stderr)
        runtime = run / "runtime"
        snapshot = self.load(runtime, "snapshot.json")
        self.assertEqual(snapshot["skill_root"], str(skill))
        self.assertTrue(snapshot["frozen_at"])
        for entry in snapshot["files"]:
            path = runtime / entry["path"]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry["sha256"])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        for path in [run, runtime] + list(runtime.rglob("*")):
            if path.is_dir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700)
        source = skill / "templates/style.css"
        frozen = runtime / "skill/templates/style.css"
        body = frozen.read_bytes()
        digest = next(e["sha256"] for e in snapshot["files"] if e["path"] == "skill/templates/style.css")
        source.write_text("source changed after freeze", encoding="utf-8")
        self.assertEqual(frozen.read_bytes(), body)
        self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(), digest)
        self.assertEqual((runtime / "scope/scope-0001.md").read_bytes(), scope1.read_bytes())
        self.assertEqual((runtime / "scope/scope-0002.md").read_bytes(), scope2.read_bytes())
        report = self.load(runtime, "worker-report-example.json")
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["verdict"]["execution"], "pass")
        contract = self.load(runtime, "report-contract.json")
        self.assertEqual(set(contract["object_fields"]["case"]), set(report))
        self.assertIn("scope", contract["object_fields"]["case.findings[]"])
        self.assertEqual(contract["enums"]["check.status"], ["pass", "fail", "unknown"])
        self.assertEqual(contract["enums"]["finding.scope"], ["agent_flow", "platform_context"])
        self.assertIn("tool_usage", contract["required_check_ids"])
        self.assertIn("completion_gate", contract["required_check_ids"])
        self.assertEqual((runtime / "skill/templates/nested/fixture.bin").read_bytes(), b"\x00anonymous\xff")
        if "axis_examples" in contract:
            axes = self.load(runtime, contract["axis_examples"])
            self.assertEqual([e["id"] for e in axes], ["admission-fails-but-closed", "unfinished-item", "rule-prefetch"])
            self.assertEqual(axes[0]["verdict"]["execution"], "pass")
            self.assertEqual(axes[0]["check_statuses"]["tool_usage"], "fail")
            self.assertEqual(axes[0]["check_statuses"]["completion_gate"], "pass")
            self.assertEqual(axes[1]["check_statuses"]["completion_gate"], "fail")
            self.assertEqual(axes[2]["classification"], "pass")

    def test_freeze_rejects_repeat_nonempty_symlinks_and_relative_paths(self):
        skill = self.skill_copy()
        run = self.root / "run"
        result = self.cli("freeze", "--skill-root", skill, "--run-dir", run)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cli("freeze", "--skill-root", skill, "--run-dir", run).returncode, 2)
        occupied = self.root / "occupied"
        occupied.mkdir()
        (occupied / "other").write_text("do not modify")
        self.assertEqual(self.cli("freeze", "--skill-root", skill, "--run-dir", occupied).returncode, 2)
        link = skill / "references/planted.md"
        link.symlink_to(skill / "SKILL.md")
        self.assertEqual(self.cli("freeze", "--skill-root", skill, "--run-dir", self.root / "symlink-run").returncode, 2)
        self.assertFalse((self.root / "symlink-run").exists())
        link.unlink()
        source_link = self.root / "source-link"
        source_link.symlink_to(skill, target_is_directory=True)
        self.assertEqual(self.cli("freeze", "--skill-root", source_link, "--run-dir", self.root / "linked-root-run").returncode, 2)
        self.assertEqual(self.cli("freeze", "--skill-root", "relative", "--run-dir", self.root / "relative-run").returncode, 2)
        self.assertEqual(self.cli("freeze", "--skill-root", skill, "--run-dir", skill / "escape-run").returncode, 2)
        scope = self.root / "scope-symlink"
        scope.symlink_to(skill / "SKILL.md")
        self.assertEqual(self.cli("freeze", "--skill-root", skill, "--run-dir", self.root / "scope-run", "--scope-file", scope).returncode, 2)

    def test_freeze_detects_source_change_during_copy(self):
        skill = self.skill_copy()
        template = skill / "templates/style.css"
        original = prepare.write_bytes

        def mutate(path, content):
            original(path, content)
            if path.name == "style.css":
                template.write_bytes(b"anonymous changed during freeze")

        with mock.patch.object(prepare, "write_bytes", side_effect=mutate):
            with self.assertRaisesRegex(prepare.PrepareError, "源文件字节变化"):
                prepare.freeze_command(argparse.Namespace(skill_root=str(skill), run_dir=str(self.root / "mutating-run"), scope_file=[]))
        self.assertFalse((self.root / "mutating-run/runtime/snapshot.json").exists())

    def test_complete_history_does_not_need_debug_and_preserves_raw_values(self):
        out, index = self.packet(history())
        self.assertEqual(index["needs_debug_message_ids"], [])
        self.assertTrue(index["history_complete"])
        self.assertEqual(index["messages"][0]["missing_fields"], [])
        tools = self.load(out, "tool-calls.json")
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["fields"]["request"][0]["value"], '{"input":"leave as string"}')
        self.assertEqual(tools[0]["fields"]["output"][0]["value"], '{"text":"leave as string"}')
        self.assertEqual({v["value"] for v in tools[0]["fields"]["status"]}, {"future-status", 99123, 99234})
        self.assertNotIn("success", tools[0])
        timeline = self.load(out, "timeline-index.json")
        self.assertEqual(len(timeline), 6)
        self.assertEqual(timeline[0]["type"], 991)
        self.assertTrue(timeline[-1]["terminal_observed"])
        for event in timeline:
            for ref in event["sources"]:
                self.assertEqual(self.pointer(index, ref)["event_id"], event["event_id"])
        usage = self.load(out, "usage.json")
        self.assertEqual(len(usage["messages"]), 1)
        self.assertEqual(usage["messages"][0]["value"]["total_tokens"], 12)
        self.assertNotIn("total", usage)
        self.assertNotIn("value", usage["messages"][0]["alternatives"][0])

    def test_missing_key_fields_need_debug_not_thinking(self):
        fields = {"raw_events_json": {"raw_events", "terminal_event"},
                  "runtime_usage_json": {"usage"}, "tool_calls_json": {"tool_calls"}, "prompt": {"prompt"}}
        for field, expected in fields.items():
            with self.subTest(field=field):
                debug = complete_debug()
                if field == "raw_events_json":
                    debug.pop(field)
                elif field == "runtime_usage_json":
                    debug.pop(field)
                    debug.pop("token_usage_json")
                    events = json.loads(debug["raw_events_json"])
                    debug["raw_events_json"] = json.dumps([e for e in events if "observation_payload" not in e["payload"]])
                elif field == "tool_calls_json":
                    debug.pop(field)
                    events = json.loads(debug["raw_events_json"])
                    debug["raw_events_json"] = json.dumps([e for e in events if "tool_payload" not in e["payload"]])
                else:
                    debug.pop("sp")
                    debug.pop("pe")
                    debug.pop("runtime_steps_json")
                    events = json.loads(debug["raw_events_json"])
                    debug["raw_events_json"] = json.dumps([e for e in events if "prompt_built_payload" not in e["payload"]])
                _, index = self.packet(history(debug), name="missing-" + field)
                self.assertEqual(index["needs_debug_message_ids"], ["message-1"])
                self.assertTrue(expected.issubset(index["messages"][0]["missing_fields"]))
                self.assertNotIn("thinking", index["messages"][0]["missing_fields"])
                self.assertFalse(index["history_complete"])

    def test_embedded_authoritative_message_id_not_scalar_message_index(self):
        for mode in ("debug", "snapshot"):
            with self.subTest(mode=mode):
                value = history(complete_debug("anonymous-long-official-message-id"))
                value["message_indexes"] = ["496832"]
                message = value["messages"][0]
                message.pop("message_id")
                message.pop("session_id")
                if mode == "snapshot":
                    debug = message.pop("debug")
                    message["message_snapshot_json"] = json.dumps({"messageId": debug["message_id"],
                                                                   "sessionId": debug["session_id"], "debug": debug})
                out, index = self.packet(value, name="authoritative-" + mode)
                self.assertEqual(len(index["messages"]), 1)
                self.assertEqual(index["messages"][0]["message_id"], "anonymous-long-official-message-id")
                self.assertEqual(index["messages"][0]["identity"]["message_id"], "anonymous-long-official-message-id")
                self.assertEqual(index["message_indexes"], ["496832"])
                self.assertEqual(index["needs_debug_message_ids"], [])
                self.assertTrue(index["history_complete"])
                self.assertEqual(self.load(out, "tool-calls.json")[0]["message_id"], "anonymous-long-official-message-id")
        value = history()
        value["messages"][0].pop("message_id")
        value["messages"][0]["message_snapshot_json"] = json.dumps({"messageId": "different-message"})
        _, index = self.packet(value, name="embedded-ambiguous")
        self.assertIn("identity.conflict", index["messages"][0]["missing_fields"])
        self.assertFalse(index["history_complete"])

    def test_request_json_object_equivalence_preserves_original_values(self):
        debug = complete_debug()
        calls = json.loads(debug["tool_calls_json"])
        calls[0]["request"] = {"input": "leave as string"}
        debug["tool_calls_json"] = json.dumps(calls)
        out, index = self.packet(history(debug))
        self.assertEqual(index["needs_debug_message_ids"], [])
        self.assertTrue(index["history_complete"])
        request = self.load(out, "tool-calls.json")[0]["fields"]["request"]
        self.assertEqual(len(request), 2)
        self.assertEqual(request[0]["value"], {"input": "leave as string"})
        self.assertEqual(request[1]["value"], '{"input":"leave as string"}')
        calls[0]["request"] = {"input": "different"}
        debug["tool_calls_json"] = json.dumps(calls)
        out, index = self.packet(history(debug), name="request-conflict")
        self.assertIn("tool_calls.details", index["missing_fields"])
        self.assertIn("identity_or_request_conflict", self.load(out, "tool-calls.json")[0]["missing_fields"])

    def test_matching_debug_completes_only_its_authoritative_message(self):
        value = history(complete_debug("message-1"), complete_debug("message-2"))
        for row in value["messages"]:
            row.pop("debug")
        debug = dict(complete_debug("message-1"), status_info=status())
        _, index = self.packet(value, [debug])
        self.assertTrue(index["messages"][0]["debug_complete"])
        self.assertFalse(index["messages"][1]["debug_complete"])
        self.assertEqual(index["needs_debug_message_ids"], ["message-2"])

    def test_wrong_identity_and_failed_status_never_become_complete(self):
        value = history()
        value["messages"][0].pop("debug")
        for mode in ("session", "message", "run-only", "status", "nested-conflict"):
            with self.subTest(mode=mode):
                debug = dict(complete_debug(), status_info=status())
                if mode == "session":
                    debug["session_id"] = "wrong-session"
                elif mode == "message":
                    debug["message_id"] = "wrong-message"
                elif mode == "run-only":
                    debug.pop("message_id")
                    debug["run_id"] = "message-1"
                elif mode == "status":
                    debug["status_info"]["status_code"] = 42
                else:
                    debug = {"status_info": status(), "session_id": "wrong-session", "debug": debug}
                _, index = self.packet(value, [debug], name="wrong-" + mode)
                self.assertEqual(index["needs_debug_message_ids"], ["message-1"])
                self.assertFalse(index["history_complete"])
                self.assertEqual(len(index["rejected_debug"]), 1)
                self.assertIn("prompt", index["messages"][0]["missing_fields"])

    def test_multi_rounds_has_more_and_missing_body_are_not_truncated(self):
        value = history(complete_debug("message-1"), complete_debug("message-2"), has_more=True)
        value["messages"].insert(0, {"message_id": "user-message", "session_id": "session-1", "role": "user", "content": "匿名请求"})
        value["message_indexes"].append({"message_id": "missing-message-3"})
        out, index = self.packet(value)
        self.assertEqual([m["message_id"] for m in index["messages"]], ["user-message", "message-1", "message-2", "missing-message-3"])
        self.assertEqual(index["needs_debug_message_ids"], ["missing-message-3"])
        self.assertTrue(index["has_more"])
        self.assertIn("necessary_previous_history", index["missing_fields"])
        self.assertFalse(index["history_complete"])
        self.assertEqual(len(self.load(out, "usage.json")["messages"]), 2)
        self.assertEqual(len(self.load(out, "timeline-index.json")), 12)
        self.assertEqual(len(self.load(out, "tool-calls.json")), 2)

    def test_large_ids_are_lossless_and_float_ids_are_unknown(self):
        big = "00099999999999999999999999999999999999999"
        numeric = 999999999999999999999999999999999999999999
        value = history(complete_debug(big, "00session"), complete_debug(numeric, "00session"))
        out, index = self.packet(value)
        self.assertEqual([m["message_id"] for m in index["messages"]], [big, str(numeric)])
        self.assertEqual(self.load(out, "tool-calls.json")[0]["message_id"], big)
        value = history()
        value["messages"][0]["message_id"] = 1.25
        value["messages"][0].pop("debug")
        value["message_indexes"] = []
        _, index = self.packet(value, name="float-id")
        self.assertIsNone(index["messages"][0]["message_id"])
        self.assertIn("identity.message_id", index["messages"][0]["missing_fields"])
        self.assertFalse(index["history_complete"])

    def test_thinking_is_optional_and_text_only_in_on_demand_file(self):
        out, index = self.packet(history())
        self.assertEqual(self.load(out, "thinking-index.json"), [])
        self.assertEqual(index["needs_debug_message_ids"], [])
        self.assertEqual(index["messages"][0]["optional_missing_fields"], ["thinking"])
        out, index = self.packet(history(complete_debug(thinking=True)), name="thinking")
        items = self.load(out, "thinking-index.json")
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["read_on_demand"])
        text = (out / items[0]["file"]).read_text()
        self.assertEqual(text, "匿名合成思考，仅按需读取。")
        self.assertEqual(self.pointer(index, items[0]["source"]), text)
        for name in ("index.json", "timeline-index.json", "thinking-index.json", "prompt-index.json"):
            self.assertNotIn(text, (out / name).read_text())

    def test_only_first_prompt_saved_later_prompts_hashes_and_pointers(self):
        out, index = self.packet(history())
        prompts = self.load(out, "prompt-index.json")
        saved = [p for p in prompts if "snapshot_file" in p]
        self.assertEqual(len(saved), 1)
        self.assertEqual(len(list((out / "prompts").iterdir())), 1)
        self.assertEqual(self.load(out, saved[0]["snapshot_file"])["message_list"][1]["content"], '{"keep":"text"}')
        later = next(p for p in prompts if p["kind"] == "prompt_built" and "snapshot_file" not in p)
        self.assertEqual(later["fields"]["message_list"]["length"], 4)
        self.assertNotIn("合成回包", (out / "prompt-index.json").read_text())
        for ref in later["sources"]:
            self.assertEqual(len(self.pointer(index, ref)["message_list"]), 4)
        # 无 SP/PE 时首个 prompt 快照仍足够。
        value = history()
        value["messages"][0]["debug"].pop("sp")
        value["messages"][0]["debug"].pop("pe")
        _, index = self.packet(value, name="prompt-only")
        self.assertEqual(index["needs_debug_message_ids"], [])

    def test_snapshot_json_and_wrapped_official_responses(self):
        debug = complete_debug()
        message = {"message_id": "message-1", "session_id": "session-1", "role": "assistant",
                   "message_snapshot_json": json.dumps({"messageId": "message-1", "sessionId": "session-1", "debug": debug})}
        value = {"status_info": status(), "session_id": "session-1",
                 "data": {"has_more": False, "messages": [message]}}
        out, index = self.packet(value)
        self.assertEqual(index["needs_debug_message_ids"], [])
        self.assertEqual(self.pointer(index, self.load(out, "timeline-index.json")[0]["sources"][0])["event_id"], "event-1")
        value["data"]["messages"][0].pop("message_snapshot_json")
        _, index = self.packet(value, [{"status_info": status(), "data": debug}], name="wrapped-debug")
        self.assertEqual(index["needs_debug_message_ids"], [])

    def test_failures_and_conflicting_embedded_identities_are_gaps(self):
        value = history()
        value["status_info"]["status_code"] = 60
        _, index = self.packet(value)
        self.assertFalse(index["history_complete"])
        self.assertEqual(index["needs_debug_message_ids"], ["message-1"])
        value = history()
        value["messages"][0]["debug"]["message_id"] = "different-message"
        _, index = self.packet(value, name="embedded-conflict")
        self.assertIn("identity.conflict", index["messages"][0]["missing_fields"])
        self.assertFalse(index["history_complete"])
        value = history(complete_debug("message-1", "session-1"), complete_debug("message-2", "session-2"))
        _, index = self.packet(value, name="multi-session")
        self.assertIn("history.session_conflict", index["missing_fields"])

    def test_missing_fields_do_not_invent_calls_or_terminal_events(self):
        debug = complete_debug()
        debug["tool_calls_json"] = "[]"
        debug["raw_events_json"] = json.dumps([{"type": 123456, "payload": {"run_lifecycle_payload": {"status": 123456}}}])
        out, index = self.packet(history(debug))
        self.assertEqual(self.load(out, "tool-calls.json"), [])
        self.assertFalse(self.load(out, "timeline-index.json")[0]["terminal_observed"])
        self.assertIn("terminal_event", index["messages"][0]["missing_fields"])
        debug["raw_events_json"] = json.dumps([{"type": "run_finished", "session_id": "wrong-session", "payload": {}}])
        out, index = self.packet(history(debug), name="wrong-event")
        self.assertFalse(self.load(out, "timeline-index.json")[0]["terminal_observed"])
        self.assertIn("raw_events.identity_conflict", index["messages"][0]["missing_fields"])

    def test_malformed_known_container_is_gap_natural_text_is_not_parsed(self):
        value = history()
        value["messages"][0]["debug"]["raw_events_json"] = "{broken"
        value["messages"][0]["debug"]["unknown_json"] = "{broken-but-not-a-container-we-know"
        out, index = self.packet(value)
        self.assertEqual(index["needs_debug_message_ids"], ["message-1"])
        self.assertTrue(index["issues"])
        self.assertNotIn("unknown_json", [i["field"] for i in index["issues"]])
        self.assertEqual(self.load(out, "timeline-index.json"), [])

    def test_packet_output_unique_private_and_symlink_paths_refused(self):
        source = self.dump("input/history.json", history())
        out = self.root / "private-output"
        result = self.cli("packet", "--history", source, "--out", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cli("packet", "--history", source, "--out", out).returncode, 2)
        for path in [out] + list(out.rglob("*")):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        link = self.root / "input-link.json"
        link.symlink_to(source)
        self.assertEqual(self.cli("packet", "--history", link, "--out", self.root / "refused-link").returncode, 2)
        parent_link = self.root / "linked-dir"
        parent_link.symlink_to(source.parent, target_is_directory=True)
        self.assertEqual(self.cli("packet", "--history", parent_link / "history.json", "--out", self.root / "refused-parent").returncode, 2)
        self.assertEqual(self.cli("packet", "--history", source, "--out", parent_link / "out").returncode, 2)

    def test_planted_modules_never_execute_with_or_without_explicit_isolation(self):
        download = self.root / "download"
        download.mkdir()
        source = self.dump("download/history.json", history())
        marker = self.root / "executed-marker"
        body = "from pathlib import Path\nPath(%r).write_text('executed')\nraise RuntimeError('planted module')\n" % str(marker)
        for name in ("json.py", "hashlib.py", "render_report.py", "prepare_audit.py", "sitecustomize.py"):
            (download / name).write_text(body)
        for isolated in (True, False):
            result = self.cli("packet", "--history", source, "--out", self.root / ("clean-%s" % isolated), isolated=isolated, cwd=download)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists())
        # freeze 从下载 cwd 启动，执行的仍只能是可信维护资源副本。
        result = self.cli("freeze", "--skill-root", SKILL_ROOT, "--run-dir", self.root / "safe-freeze", isolated=False, cwd=download)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())

    def test_nested_debug_conflict_and_nested_failure_are_never_accepted(self):
        value = history()
        value["messages"][0].pop("debug")
        debug = dict(complete_debug(), status_info=status())
        events = json.loads(debug["raw_events_json"])
        events[-1]["message_id"] = "wrong-message"
        debug["raw_events_json"] = json.dumps(events)
        _, index = self.packet(value, [debug])
        self.assertEqual(index["rejected_debug"][0]["reason"], "debug.nested_identity_conflict")
        self.assertEqual(index["needs_debug_message_ids"], ["message-1"])
        debug = complete_debug()
        _, index = self.packet(value, [{"status_info": status(), "session_id": "wrong-session", "data": debug}], name="wrapper-identity")
        self.assertEqual(index["rejected_debug"][0]["reason"], "debug.identity_conflict")
        nested = dict(debug, status_info={"status_code": 7, "status_msg": "synthetic failure"})
        _, index = self.packet(value, [{"status_info": status(), "data": nested}], name="nested-status")
        self.assertEqual(index["rejected_debug"][0]["reason"], "debug.status_info")
        value = history(dict(debug, status_info={"status_code": 7, "status_msg": "synthetic failure"}))
        _, index = self.packet(value, name="embedded-status")
        self.assertIn("evidence.status_info", index["missing_fields"])
        self.assertEqual(index["needs_debug_message_ids"], ["message-1"])

    def test_missing_tool_detail_and_empty_prompt_are_gaps(self):
        debug = complete_debug()
        debug["sp"], debug["pe"] = "", ""
        debug["runtime_steps_json"] = "[]"
        debug["raw_events_json"] = json.dumps([{"type": "run_finished", "payload": {}}])
        debug["tool_calls_json"] = json.dumps([{"name": "anonymous", "tool_call_id": "c1"}])
        out, index = self.packet(history(debug))
        self.assertIn("prompt", index["missing_fields"])
        self.assertIn("tool_calls.details", index["missing_fields"])
        self.assertEqual(self.load(out, "tool-calls.json")[0]["missing_fields"], ["request", "output_or_error", "status"])
        self.assertFalse(index["messages"][0]["debug_complete"])

    def test_empty_error_on_started_call_is_not_a_return(self):
        debug = complete_debug()
        debug["tool_calls_json"] = json.dumps([{"tool_call_id": "call-1", "name": "anonymous", "request": {}, "status": 1, "error": ""}])
        debug["raw_events_json"] = json.dumps([{"type": "run_finished", "payload": {}}])
        out, index = self.packet(history(debug))
        call = self.load(out, "tool-calls.json")[0]
        self.assertEqual(call["fields"]["status"][0]["value"], 1)
        self.assertEqual(call["fields"]["error"][0]["value"], "")
        self.assertIn("output_or_error", call["missing_fields"])
        self.assertIn("tool_calls.details", index["missing_fields"])
        self.assertEqual(index["needs_debug_message_ids"], ["message-1"])
        for error in ("synthetic error", {"code": "synthetic-error"}):
            debug["tool_calls_json"] = json.dumps([{"tool_call_id": "call-1", "name": "anonymous", "request": {}, "status": 1, "error": error}])
            _, index = self.packet(history(debug), name="nonempty-error-" + type(error).__name__)
            self.assertEqual(index["needs_debug_message_ids"], [])

    def test_half_empty_sp_pe_requires_prompt_snapshot(self):
        for field in ("sp", "pe"):
            with self.subTest(field=field):
                debug = complete_debug()
                debug[field] = "   "
                debug["runtime_steps_json"] = "[]"
                events = json.loads(debug["raw_events_json"])
                debug["raw_events_json"] = json.dumps([e for e in events if "prompt_built_payload" not in e["payload"]])
                _, index = self.packet(history(debug), name="half-empty-" + field)
                self.assertIn("prompt", index["missing_fields"])
                self.assertEqual(index["needs_debug_message_ids"], ["message-1"])
                debug["runtime_steps_json"] = complete_debug()["runtime_steps_json"]
                _, index = self.packet(history(debug), name="snapshot-restores-" + field)
                self.assertEqual(index["needs_debug_message_ids"], [])

    def test_invalid_json_duplicate_keys_and_nonfinite_numbers_rejected(self):
        for i, body in enumerate(("{broken", '{"a":1,"a":2}', '{"n":NaN}', '{"n":Infinity}')):
            source = self.root / ("invalid-%d.json" % i)
            source.write_text(body)
            out = self.root / ("invalid-out-%d" % i)
            result = self.cli("packet", "--history", source, "--out", out)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
