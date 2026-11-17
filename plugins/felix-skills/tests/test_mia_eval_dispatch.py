"""调度与worker指针的文档契约回归；不启动代理、平台或模型。"""
import json
from pathlib import Path
import re
import unittest


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = PLUGIN_ROOT / "skills/mia-eval"
REPO_ROOT = PLUGIN_ROOT.parents[1]


def document(relative):
    return (SKILL_ROOT / relative).read_text(encoding="utf-8")


class DispatchContractTests(unittest.TestCase):
    def test_entrypoint_defaults_to_three_and_honors_requested_value(self):
        text = document("SKILL.md")
        self.assertRegex(text, r"默认并发\s*3")
        self.assertRegex(text, r"用户指定.*正整数 N")

    def test_no_unconditional_three_worker_cap(self):
        for relative in ("SKILL.md", "references/dispatch.md"):
            with self.subTest(document=relative):
                self.assertIsNone(re.search(r"最多并发\s*3", document(relative)))

    def test_support_and_usage_describe_user_override(self):
        compatibility = json.loads((PLUGIN_ROOT / "compatibility.json").read_text(encoding="utf-8"))
        requirements = "\n".join(compatibility["skills"]["mia-eval"]["environment_requirements"])
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        for name, text in (("compatibility", requirements), ("README", readme)):
            with self.subTest(document=name):
                self.assertRegex(text, r"默认并发\s*3")
                self.assertIn("用户指定时按指定并发数执行", text)

    def test_dispatch_keeps_slots_filled_and_uses_host_completion(self):
        text = document("references/dispatch.md")
        for concept in ("N 个槽位", "填满", "不足时全部启动", "立即补位", "不等整批", "宿主容量", "实际限制", "status.available"):
            with self.subTest(concept=concept):
                self.assertIn(concept, text)
        for event in ("reserve", "started --ordinal", "report-ready", "finished", "start-rejected", "repair-needed", "reserve-repair --worker-id", "resume --worker-id", "resume-rejected"):
            with self.subTest(event=event):
                self.assertIn(event, text)
        self.assertRegex(text, r"case_key\+ordinal.*核对")
        self.assertRegex(text, r"report-ready.*仍占槽位")
        self.assertRegex(text, r"finished.*实际宿主 completed")
        self.assertIn("返回数组位置不是身份", text)
        self.assertIn("进度消息", text)
        self.assertIn("阻塞全部剩余工作", text)

    def test_worker_loads_authoritative_scoring_and_full_inventory(self):
        dispatch = document("references/dispatch.md")
        for pointer in ("audit-brief.json", "report-contract.json", "worker-report-example.json", "axis-examples.json", "references/audit-rubric.md", "references/tool-call-scoring.md", "references/check-statistics.md"):
            with self.subTest(pointer=pointer):
                self.assertIn(pointer, dispatch)
        for concept in ("完整本例输入", "全部实际调用", "复合ID集合", "不只比数量", "已知调用无缺项", "不造未实际发生的调用", "schema_version与manifest一致"):
            with self.subTest(concept=concept):
                self.assertIn(concept, dispatch)
        scoring = document("references/tool-call-scoring.md")
        for concept in ("正确工具", "触发条件", "参数", "必要范围", "结果处理", "平台失败不自动等于调用错误", "首次 fail 保留", "所有下游通知不连带判错", "不计调用分母", "一个调用即使有多个问题也只计一次 fail", "skill_read", "未知总量不补写"):
            with self.subTest(concept=concept):
                self.assertIn(concept, scoring)
        self.assertIn("9/10=90%", scoring)
        self.assertIn("历史报告不自动重写", scoring)

    def test_statistical_names_and_denominators_have_one_authoritative_pointer(self):
        statistics = document("references/check-statistics.md")
        calls = document("references/tool-call-scoring.md")
        self.assertRegex(statistics, r"设计遵循率\s*=.*checks.*pass.*全部 checks")
        self.assertRegex(calls, r"工具遵循率\s*=.*正确实际调用数.*全部实际调用数")
        self.assertIn("设计遵循通过案例占比", statistics)
        self.assertIn("design_adherence_stats", statistics)
        self.assertIn("tool_adherence_stats", statistics)
        self.assertIn("statistics_schema_version=2", statistics)
        self.assertIn("metric_definitions", statistics)
        self.assertIn("不从检查推算", statistics)
        self.assertIn("unknown", statistics)
        self.assertIn("N/A", statistics)
        self.assertIn("旧页与冻结快照保持原样", document("references/revisions.md"))
        self.assertIn("check-statistics.md", document("SKILL.md"))
        self.assertIn("tool-call-scoring.md", document("SKILL.md"))
        self.assertNotIn("工具正确使用率 =", document("SKILL.md"))

    def test_input_citations_and_freeze_path_order_are_explicit(self):
        dispatch = document("references/dispatch.md")
        acquisition = document("references/acquisition.md")
        for concept in ("物理绝对路径", "拒绝符号链接", "0700", "为空或只含 acquisition", "task.json", "source+locator", "input-index", "verify-citations", "case --packet", "unchecked"):
            with self.subTest(concept=concept):
                self.assertIn(concept, dispatch)
        for concept in ("--input", "根类型", "RFC6901", "哈希", "locator"):
            with self.subTest(concept=concept):
                self.assertIn(concept, acquisition)

    def test_rubric_carries_anonymous_action_scope_and_pagination_counterexamples(self):
        rubric = document("references/audit-rubric.md")
        for concept in ("范围对照（匿名合成）", "动作对照（匿名合成）", "分页对照（匿名合成）", "实际新增的独立目的", "生成/push 参数", "正常任务介绍", "有据短关怀", "思考", "不能证明新增或发送", "必要查询本身也要收尾", "next_cursor", "同范围完整替代", "不推断隐藏页", "超出范围的细则不形成 fail", "另一轮用户明确包含", "execution/completion_gate"):
            with self.subTest(concept=concept):
                self.assertIn(concept, rubric)
        # 单轮额度豁免不是通用审计的默认规则。
        self.assertNotIn("仅缺今日已保存待推送主动帮助核验", rubric)
        self.assertIn("报告修订", rubric)


if __name__ == "__main__":
    unittest.main()
