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

    def test_call_scoring_has_pointers_and_worker_requires_full_inventory(self):
        for relative in ("SKILL.md", "references/dispatch.md", "references/audit-rubric.md", "references/acquisition.md"):
            with self.subTest(document=relative):
                text = (SKILL_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("tool-call-scoring.md", text)
        dispatch = (SKILL_ROOT / "references/dispatch.md").read_text(encoding="utf-8")
        for rule in ("报告schema_version=3", "逐条核对packet的index.json及tool-calls.json全部调用索引", "calls无缺项", "复合ID(message_id,tool_call_id)唯一", "主线程另核对packet调用集合，不只核对数量", "逐调用fail不自动整体fail", "保留原fail"):
            self.assertIn(rule, dispatch)
        scoring = (SKILL_ROOT / "references/tool-call-scoring.md").read_text(encoding="utf-8")
        for rule in ("正确工具", "触发条件", "参数", "必要范围", "结果处理", "平台失败不自动等于调用错误", "首次 fail 保留", "必要重试及合理核验可 pass", "已证实没有增加信息", "首次相应生成调用", "首次相应 push", "所有下游通知不连带判错", "归到返回该结果的对应调用", "必须直接继续的分页", "不计调用分母", "一个调用即使有多个问题也只计一次 fail", "不评价最终回复", "工程注入字段", "未知总量不补写", "9/10=90%", "历史报告不自动重写"):
            self.assertIn(rule, scoring)
        entry = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("工具正确使用率 = 全部选定案例中正确实际调用数 / 全部实际调用数", entry)
        self.assertIn("工具遵循率 = 所有选定案例的 checks 中状态为 pass 的条数 / 所有选定案例的 checks 总条数", entry)
        statistics = (SKILL_ROOT / "references/check-statistics.md").read_text(encoding="utf-8")
        for rule in ("不按检查 ID、工具名或类型筛选", "fail、unknown", "不平均各 case 比例", "9/10=90%", "没有任何检查时为 N/A", "覆盖率（完整 / 全部样本）", "删除展示不取消取证", "不自动重写历史报告"):
            self.assertIn(rule, statistics)
        self.assertIn("check-statistics.md", dispatch)


if __name__ == "__main__":
    unittest.main()
