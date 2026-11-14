"""并发规则的静态文档回归；不启动真实子代理、评测或模型。"""
import json
from pathlib import Path
import re
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills/mia-eval"
REPO_ROOT = PLUGIN_ROOT.parents[1]


class DispatchContractTests(unittest.TestCase):
    def test_entrypoint_defaults_to_three_and_honors_requested_value(self):
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(
            text,
            r"并发数未指定时默认\s*3；用户指定时按指定并发数执行",
        )

    def test_no_unconditional_three_worker_cap(self):
        for relative in ("SKILL.md", "references/dispatch.md"):
            with self.subTest(document=relative):
                text = (SKILL_ROOT / relative).read_text(encoding="utf-8")
                self.assertIsNone(re.search(r"最多并发\s*3", text))

    def test_support_and_usage_describe_user_override(self):
        compatibility = json.loads(
            (PLUGIN_ROOT / "compatibility.json").read_text(encoding="utf-8")
        )
        requirements = "\n".join(
            compatibility["skills"]["mia-eval"]["environment_requirements"]
        )
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for name, text in (("compatibility", requirements), ("README", readme)):
            with self.subTest(document=name):
                self.assertRegex(text, r"默认并发\s*3")
                self.assertIn("用户指定时按指定并发数执行", text)

    def test_dispatch_keeps_slots_filled_and_reports_real_limits(self):
        text = (SKILL_ROOT / "references/dispatch.md").read_text(encoding="utf-8")
        for rule in (
            "填满 N 个槽位",
            "任一 worker 完成后立即补位",
            "剩余 case 少于 N 时全部启动",
            "仅凭推测不降低 N",
            "显式数值无效时请用户修正",
            "用户指定值与实际可用并发数",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, text)


if __name__ == "__main__":
    unittest.main()
