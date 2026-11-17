"""离线报告契约与命令回归；仅临时目录中的匿名合成数据。"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "skills/mia-eval/scripts/render_report.py"
SPEC = importlib.util.spec_from_file_location("mia_eval_render_report", SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.links.append(attrs.get("href", ""))
        if tag == "script":
            self.scripts.append(attrs)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manifest, self.reports, self.synthesis = renderer.demo_data(version=1)
        self.dump("manifest.json", self.manifest)
        self.dump("synthesis.json", self.synthesis)
        (self.root / "reports").mkdir()
        for key, report in self.reports.items():
            self.dump("reports/" + key + ".json", report)

    def dump(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def case(self, key, overwrite=False):
        return renderer.case_command(argparse.Namespace(manifest=str(self.root / "manifest.json"), report=str(self.root / "reports" / (key + ".json")), out=str(self.root / "output/cases" / (key + ".html")), overwrite=overwrite))

    def cases(self):
        for key in self.reports:
            self.case(key)

    def summary(self, overwrite=False):
        return renderer.summary_command(argparse.Namespace(manifest=str(self.root / "manifest.json"), synthesis=str(self.root / "synthesis.json"), reports_dir=str(self.root / "reports"), out=str(self.root / "output/index.html"), overwrite=overwrite))

    def test_all_categories_denominator_and_links(self):
        self.cases()
        result = self.summary()
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["counts"], {"pass": 1, "fail": 1, "recovered": 1, "insufficient": 1})
        summary = (self.root / "output/index.html").read_text()
        self.assertNotIn("顺畅通过率=", summary)
        self.assertEqual(result["smooth_rate"], "25.0%")
        self.assertEqual(result["statistics_schema_version"], 2)
        self.assertEqual(summary.count('<th scope="col">已列调用数</th>'), 1)
        self.assertIn("涉及 1 个 case", summary)
        for file in (self.root / "output").rglob("*.html"):
            links = Links()
            links.feed(file.read_text())
            self.assertEqual(len(links.scripts), 1)
            self.assertNotIn("src", links.scripts[0])
            for link in links.links:
                self.assertTrue((file.parent / link).is_file(), (file, link))

    def test_summary_shows_issues_and_concrete_cases_without_advice(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                manifest, reports, synthesis = renderer.demo_data(version=version)
                self.assertEqual(set(synthesis), {"summary", "issues"})
                issue = synthesis["issues"][0]
                self.assertEqual(set(issue), {"id", "priority", "title", "layer", "problem", "examples"})
                page = renderer.render_summary(manifest, reports, synthesis, set())
                for value in ("问题汇总", "具体 case", issue["title"], "层级：" + issue["layer"], issue["problem"]):
                    self.assertIn(value, page)
                for example in issue["examples"]:
                    self.assertIn(example["description"], page)
                    self.assertIn('href="cases/' + example["case_key"] + '.html"', page)
                for value in ("改进建议", "暂无建议", "修改：", "验证：", "回归验证", reports["fail"]["findings"][0]["suggestion"]):
                    self.assertNotIn(value, page)

    def test_summary_issue_order_and_empty_state(self):
        issue = {"id": "issue-2", "priority": "P2", "title": "次要问题", "layer": "Agent", "problem": "结果说明不准确。", "examples": [{"case_key": "fail", "description": "只返回一项，却说返回了两项。"}]}
        self.synthesis = {"summary": "合成示例", "issues": [issue, dict(issue, id="issue-0", priority="P0", title="严重问题"), dict(issue, id="issue-1", priority="P1", title="主要问题")]}
        renderer.validate_synthesis(self.synthesis, self.manifest)
        page = renderer.render_summary(self.manifest, self.reports, self.synthesis, set())
        self.assertLess(page.index("P0 · 严重问题"), page.index("P1 · 主要问题"))
        self.assertLess(page.index("P1 · 主要问题"), page.index("P2 · 次要问题"))
        self.assertIn("涉及 1 个 case", page)
        self.synthesis["issues"] = []
        renderer.validate_synthesis(self.synthesis, self.manifest)
        page = renderer.render_summary(self.manifest, self.reports, self.synthesis, set())
        self.assertIn("未发现本次范围内的问题", page)

    def test_summary_removes_case_verdict_table_but_keeps_details_and_issue_examples(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                manifest, reports, synthesis = renderer.demo_data(version=version)
                reports["fail"]["checks"][0]["observed"] = "只返回了项目 A，项目 B 的建议没有给出。"
                page = renderer.render_summary(manifest, reports, synthesis, set())
                for value in ('id="case-table"', 'id="case-search"', 'id="case-status"', "判定依据 / 缺口", "全部 case</h2>", "visible-count", "noscript"):
                    self.assertNotIn(value, page)
                for case in manifest["cases"]:
                    self.assertIn('href="cases/' + case["case_key"] + '.html"', page)
                self.assertIn(synthesis["issues"][0]["examples"][0]["description"], page)
                self.assertIn(reports["fail"]["checks"][0]["observed"], renderer.render_case(manifest, reports["fail"]))

    def test_summary_metadata_keeps_evaluation_mia_link(self):
        task_url = "https://mia.example.invalid/evaluation/task/task-123?space_id=space-456&view=details"
        for version in (1, 2, 3):
            with self.subTest(version=version):
                manifest, reports, synthesis = renderer.demo_data(version=version)
                manifest["task_url"] = task_url
                renderer.validate_manifest(manifest)
                page = renderer.render_summary(manifest, reports, synthesis, set())
                metadata = page.split('<details id="metadata-details" class="metadata-details">', 1)[1].split("</details>", 1)[0]
                self.assertIn('<summary id="meta-heading">', metadata)
                self.assertNotIn(task_url.replace("&", "&amp;"), metadata)
                self.assertNotIn("评测 MIA 链接", metadata)
                self.assertNotIn('<details id="metadata-details" class="metadata-details" open', page)
                self.assertIn('<p class="evaluation-link">评测 MIA 链接：<a href="' + task_url.replace("&", "&amp;") + '"', page)
                parser = Links()
                parser.feed(page)
                self.assertEqual(parser.links.count(task_url), 1)

    def test_summary_missing_evaluation_link_stays_unprovided(self):
        page = renderer.render_summary(self.manifest, self.reports, self.synthesis, set())
        self.assertIn('<p class="evaluation-link">评测 MIA 链接：未提供</p>', page)

    def test_summary_omits_methodology_notes_and_conclusion(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                manifest, reports, synthesis = renderer.demo_data(version=version)
                synthesis["summary"] = "不应在汇总页展示的独立结论正文"
                renderer.validate_synthesis(synthesis, manifest)
                page = renderer.render_summary(manifest, reports, synthesis, set())
                for value in ("元信息与统计口径", "顺畅通过率=P/N", "证据使用脱敏定位与短引用", "conclusion-heading", "{{CONCLUSION}}", synthesis["summary"]):
                    self.assertNotIn(value, page)
                self.assertIn('<h2 id="rate-heading" class="rate">综合表现</h2>', page)
                self.assertNotIn("顺畅通过率=", page)
                positions = [page.index('class="evaluation-link"'), page.index('id="metadata-details"'),
                             page.index('aria-labelledby="rate-heading"'), page.index('aria-labelledby="issues-heading"')]
                self.assertEqual(positions, sorted(positions))
                for removed in ("cases-heading", "工具调用正确性", "各项按自身判定", "计算方式：正确实际调用数", 'scope="col">调用统计', "设计遵循率计算方式"):
                    self.assertNotIn(removed, page)

    def test_summary_omits_limitations_section_but_case_keeps_it(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                manifest, reports, synthesis = renderer.demo_data(version=version)
                report = reports["insufficient"]
                report["limitations"].append("仅详情展示的限制说明")
                summary = renderer.render_summary(manifest, reports, synthesis, set())
                self.assertNotIn("证据缺口与限制", summary)
                self.assertNotIn("gaps-heading", summary)
                self.assertNotIn("仅详情展示的限制说明", summary)
                self.assertIn("证据不足", summary)
                self.assertNotIn("判定依据 / 缺口", summary)
                detail = renderer.render_case(manifest, report)
                self.assertIn("覆盖与限制", detail)
                self.assertIn("仅详情展示的限制说明", detail)

    def test_case_shows_plain_checks_without_evidence_or_findings(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                manifest, reports, _ = renderer.demo_data(version=version)
                report = reports["fail"]
                check = report["checks"][0]
                check["requirement"] = "处理完两个任务再结束"
                check["expected"] = "先查清两个任务的状态，再分别处理尚未完成的任务。"
                check["observed"] = "只处理了第一个任务，第二个还没处理就结束了。"
                report["evidence_index"][0]["excerpt"] = "不应进入页面的证据正文"
                report["findings"][0]["claim"] = "不应进入页面的偏差判断"
                renderer.validate_case(report, manifest)
                page = renderer.render_case(manifest, report)
                for text in ("检查要求", "应该怎么做", "实际做了什么", check["requirement"], check["expected"], check["observed"], "修改建议与回归验证", report["findings"][0]["suggestion"]):
                    self.assertIn(text, page)
                for text in ("<th>证据</th>", "证据短引用", "执行偏差与范围外背景", "设计与业务偏差", "不应进入页面的证据正文", "不应进入页面的偏差判断", check["id"] + " / " + check["requirement"]):
                    self.assertNotIn(text, page)

    def test_missing_report_creates_placeholder_and_keeps_denominator(self):
        (self.root / "reports/pass.json").unlink()
        for key in ("fail", "recovered", "insufficient"):
            self.case(key)
        result = self.summary()
        self.assertEqual(result["counts"]["insufficient"], 2)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["placeholder_cases"], ["pass"])
        page = (self.root / "output/cases/pass.html").read_text()
        self.assertIn("证据不足占位页", page)
        self.assertIn("缺少 case JSON", page)

    def test_missing_html_is_placeholder_not_pass(self):
        result = self.summary()
        self.assertEqual(result["counts"]["pass"], 0)
        self.assertEqual(result["counts"]["fail"], 1)
        self.assertEqual(result["counts"]["insufficient"], 3)
        self.assertEqual(len(result["placeholder_cases"]), 4)
        self.assertIn("已有 JSON，但缺少", (self.root / "output/cases/pass.html").read_text())

    def test_version_mismatch_even_when_fail(self):
        for key in ("pass", "fail"):
            report = self.reports[key]
            report["identity"]["version"] = "another-version"
            renderer.validate_case(report, self.manifest)
            status, reasons = renderer.assess(self.manifest, report)
            self.assertEqual(status, "insufficient" if key == "pass" else "fail")
            self.assertTrue(any("不匹配" in r for r in reasons))
            self.assertIn("不匹配", renderer.render_case(self.manifest, report))

    def test_unstarted_case_has_no_invented_session(self):
        self.manifest["cases"][0]["session_id"] = ""
        self.manifest["cases"][0]["message_ids"] = []
        renderer.validate_manifest(self.manifest)
        self.assertEqual(self.manifest["cases"][0]["session_id"], "")

    def test_unknown_identity_not_invented(self):
        report = self.reports["pass"]
        report["identity"]["version"] = ""
        renderer.validate_case(report, self.manifest)
        self.assertEqual(renderer.assess(self.manifest, report)[0], "insufficient")

    def test_null_errors_stay_missing(self):
        report = self.reports["pass"]
        self.assertIsNone(report["metrics"]["business_errors"])
        self.assertIn("<td>business_errors</td><td>未提供</td>", renderer.render_case(self.manifest, report))
        self.assertEqual(renderer.assess(self.manifest, report)[0], "pass")

    def test_retries_or_failures_override_smooth_claim(self):
        for key in ("retries", "tool_failures", "business_errors"):
            with self.subTest(key=key):
                report = copy.deepcopy(self.reports["pass"])
                report["metrics"][key] = 1
                self.assertEqual(renderer.assess(self.manifest, report)[0], "recovered")

    def test_required_unknown_or_missing_blocks_pass(self):
        for mode in ("unknown", "missing"):
            report = copy.deepcopy(self.reports["pass"])
            if mode == "unknown":
                report["checks"][0]["status"] = "unknown"
                report["checks"][0]["evidence_refs"] = []
            else:
                report["checks"].pop(0)
            renderer.validate_case(report, self.manifest)
            self.assertEqual(renderer.assess(self.manifest, report)[0], "insufficient")

    def test_thinking_missing_pass_requires_trace(self):
        report = self.reports["pass"]
        self.assertEqual(renderer.assess(self.manifest, report)[0], "pass")
        report["timeline"] = []
        self.assertEqual(renderer.assess(self.manifest, report)[0], "insufficient")

    def test_checks_and_findings_override_verdict(self):
        report = self.reports["pass"]
        report["checks"][0]["status"] = "fail"
        self.assertEqual(renderer.assess(self.manifest, report)[0], "fail")
        report["checks"][0]["status"] = "pass"
        report["findings"] = self.reports["fail"]["findings"]
        self.assertEqual(renderer.assess(self.manifest, report)[0], "fail")
        report["findings"][0]["confidence"] = "hypothesis"
        self.assertEqual(renderer.assess(self.manifest, report)[0], "pass")

    def test_duplicate_and_extra_reports_rejected(self):
        self.dump("reports/duplicate.json", self.reports["pass"])
        with self.assertRaisesRegex(renderer.ContractError, "重复 case"):
            self.summary()
        (self.root / "reports/duplicate.json").unlink()
        extra = copy.deepcopy(self.reports["pass"])
        extra["case_key"] = "extra"
        self.dump("reports/extra.json", extra)
        with self.assertRaisesRegex(renderer.ContractError, "以外"):
            self.summary()

    def test_invalid_json_and_duplicate_fields_rejected(self):
        for content in ("{broken", '{"a": 1, "a": 2}', '{"n": NaN}', '{"n": Infinity}'):
            with self.subTest(content=content):
                path = self.root / "bad.json"
                path.write_text(content)
                with self.assertRaises(renderer.ContractError):
                    renderer.load_json(path)
        (self.root / "reports/bad.json").write_text("{")
        with self.assertRaises(renderer.ContractError):
            self.summary()
        self.assertFalse((self.root / "output/index.html").exists())

    def test_path_injection_and_manifest_duplicate(self):
        for key in ("../secret", "a/b", "/tmp/x", "x.html", 'x\" onclick=\"bad', "UPPER", "x%2fy"):
            with self.subTest(key=key):
                manifest = copy.deepcopy(self.manifest)
                manifest["cases"][0]["case_key"] = key
                with self.assertRaises(renderer.ContractError):
                    renderer.validate_manifest(manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["cases"].append(copy.deepcopy(manifest["cases"][0]))
        with self.assertRaises(renderer.ContractError):
            renderer.validate_manifest(manifest)

    def test_unsafe_links_and_html_escaping(self):
        for url in ("javascript:alert(1)", "data:text/html,test", "http://example.invalid", "https://user:secret@example.invalid", "https://example.invalid\\@evil.invalid", "https://example.invalid/\npath", "https://example.invalid:bad", "https://-bad.invalid", "https://a..invalid"):
            with self.subTest(url=url):
                manifest = copy.deepcopy(self.manifest)
                manifest["task_url"] = url
                with self.assertRaises(renderer.ContractError):
                    renderer.validate_manifest(manifest)
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>{{TITLE}}'
        report = self.reports["pass"]
        report["summary"] = payload
        report["evidence_index"][0]["excerpt"] = payload
        report["evidence_index"][0]["locator"] = "javascript:alert(1)"
        page = renderer.render_case(self.manifest, report)
        self.assertNotIn('<script>alert("x")', page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("{{TITLE}}", page)
        parser = Links()
        parser.feed(page)
        self.assertEqual(parser.links, ["../index.html"])
        self.assertEqual(len(parser.scripts), 1)

    def test_no_evidence_fail_and_dangling_reference_rejected(self):
        for key in ("checks", "findings"):
            report = copy.deepcopy(self.reports["fail"])
            report[key][0]["evidence_refs"] = []
            with self.assertRaisesRegex(renderer.ContractError, "证据"):
                renderer.validate_case(report, self.manifest)
        report = copy.deepcopy(self.reports["pass"])
        report["checks"][0]["evidence_refs"] = ["not-found"]
        with self.assertRaisesRegex(renderer.ContractError, "引用不存在"):
            renderer.validate_case(report, self.manifest)
        report = copy.deepcopy(self.reports["pass"])
        report["checks"], report["timeline"] = [], []
        report["verdict"]["execution"] = "fail"
        with self.assertRaisesRegex(renderer.ContractError, "fail 必须"):
            renderer.validate_case(report, self.manifest)

    def test_bool_nan_infinity_and_negative_numbers_rejected(self):
        for value in (True, float("nan"), float("inf"), -1):
            report = copy.deepcopy(self.reports["pass"])
            report["metrics"]["model_calls"] = value
            with self.subTest(value=value), self.assertRaises(renderer.ContractError):
                renderer.validate_case(report, self.manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["cases"][0]["ordinal"] = True
        with self.assertRaises(renderer.ContractError):
            renderer.validate_manifest(manifest)

    def test_zero_samples_is_na_not_zero_percent(self):
        self.manifest["cases"] = []
        self.synthesis["issues"] = []
        self.dump("manifest.json", self.manifest)
        self.dump("synthesis.json", self.synthesis)
        for path in (self.root / "reports").iterdir():
            path.unlink()
        result = self.summary()
        self.assertEqual(result["total"], 0)
        page = (self.root / "output/index.html").read_text()
        self.assertEqual(result["smooth_rate"], "N/A")
        self.assertIn("设计遵循率</td><td>0 / 0</td><td>N/A", page)
        self.assertNotIn("0.0%", page)

    def test_overwrite_default_refused_and_explicit_allowed(self):
        self.case("pass")
        with self.assertRaisesRegex(renderer.ContractError, "overwrite"):
            self.case("pass")
        self.case("pass", True)
        for key in ("fail", "recovered", "insufficient"):
            self.case(key)
        self.summary()
        with self.assertRaisesRegex(renderer.ContractError, "overwrite"):
            self.summary()
        self.summary(True)

    def test_tampered_stale_or_symlink_html_rejected(self):
        self.cases()
        path = self.root / "output/cases/pass.html"
        path.write_text("<html>forged</html>")
        with self.assertRaisesRegex(renderer.ContractError, "不一致"):
            self.summary()
        path.unlink()
        path.symlink_to(self.root / "manifest.json")
        with self.assertRaisesRegex(renderer.ContractError, "符号链接"):
            self.summary()

    def test_strict_template_slots(self):
        templates = self.root / "templates"
        templates.mkdir()
        test = templates / "test.html"
        with mock.patch.object(renderer, "TEMPLATES", templates):
            test.write_text("{{A}} {{B}}")
            self.assertEqual(renderer.render_template("test.html", {"A": "one", "B": "two"}), "one two")
            for slots in ({"A": "x"}, {"A": "x", "B": "y", "C": "z"}):
                with self.assertRaises(renderer.ContractError):
                    renderer.render_template("test.html", slots)
            for template in ("{{A}}{{A}}", "{{bad}}", "{{A}"):
                test.write_text(template)
                with self.assertRaises(renderer.ContractError):
                    renderer.render_template("test.html", {"A": "x"})

    def test_synthesis_invalid_reference_and_fields(self):
        issue = {"id": "issue-1", "priority": "P1", "title": "漏掉第二项", "layer": "Agent", "problem": "要求返回两项，实际只返回一项。", "examples": [{"case_key": "fail", "description": "返回了项目 A，漏掉项目 B。"}]}
        valid = {"summary": "合成问题汇总", "issues": [issue]}
        renderer.validate_synthesis(valid, self.manifest)
        mutations = (
            ("范围外 case", lambda value: value["issues"][0]["examples"][0].update(case_key="not-present")),
            ("没有具体 case", lambda value: value["issues"][0].update(examples=[])),
            ("重复 case", lambda value: value["issues"][0]["examples"].append(copy.deepcopy(issue["examples"][0]))),
            ("空描述", lambda value: value["issues"][0]["examples"][0].update(description=" ")),
            ("缺少描述", lambda value: value["issues"][0]["examples"][0].pop("description")),
            ("非法严重程度", lambda value: value["issues"][0].update(priority="P3")),
            ("重复问题编号", lambda value: value["issues"].append(copy.deepcopy(issue))),
            ("修改建议字段", lambda value: value["issues"][0].update(change="不应要求改法")),
            ("验证字段", lambda value: value["issues"][0].update(validation="不应要求验证")),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                value = copy.deepcopy(valid)
                mutate(value)
                with self.assertRaises(renderer.ContractError):
                    renderer.validate_synthesis(value, self.manifest)
        with self.assertRaises(renderer.ContractError):
            renderer.validate_synthesis({"summary": "旧格式", "recommendations": []}, self.manifest)
        report = self.reports["pass"]
        report["aggregate_status"] = "pass"
        with self.assertRaises(renderer.ContractError):
            renderer.validate_case(report, self.manifest)

    def test_summary_issue_text_is_escaped(self):
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>{{TITLE}}'
        self.synthesis = {"summary": payload, "issues": [{"id": "issue-1", "priority": "P1", "title": payload, "layer": payload, "problem": payload, "examples": [{"case_key": "fail", "description": payload}]}]}
        renderer.validate_synthesis(self.synthesis, self.manifest)
        page = renderer.render_summary(self.manifest, self.reports, self.synthesis, set())
        self.assertNotIn('<script>alert("x")', page)
        self.assertNotIn("<img", page)
        self.assertEqual(page.count("&lt;script&gt;"), 4)
        self.assertEqual(page.count("{{TITLE}}"), 4)
        parser = Links()
        parser.feed(page)
        self.assertEqual(len(parser.scripts), 1)
        self.assertIn("cases/fail.html", parser.links)

    def test_partial_scope_and_axis_unknown_visible(self):
        self.manifest["scope_complete"] = False
        page = renderer.render_summary(self.manifest, self.reports, self.synthesis, set())
        for text in ("部分清单，仅对已取得样本统计", "设计遵循率", "任务完成率", "unknown", "工具遵循率（历史格式无逐调用数据）"):
            self.assertIn(text, page)

    def test_agent_flow_mock_context_and_legal_block_are_not_failures(self):
        manifest, reports, _ = renderer.demo_data()
        report = reports["pass"]
        context = copy.deepcopy(reports["fail"]["findings"][0])
        context.update(id="platform-mock", scope="platform_context", claim="Mock不执行真实落盘")
        report["findings"] = [context]
        report["metrics"]["business_errors"] = 1
        report["metrics"]["tool_failures"] = 1
        report["summary"] = "首次查询不可用，按设计合法受阻，其他独立项完成。"
        renderer.validate_manifest(manifest)
        renderer.validate_case(report, manifest)
        self.assertEqual(renderer.assess(manifest, report)[0], "pass")
        self.assertIn("不评Tool是否Mock", renderer.render_case(manifest, report))
        context["scope"] = "agent_flow"
        self.assertEqual(renderer.assess(manifest, report)[0], "fail")

    def test_agent_flow_premature_final_response_fails(self):
        manifest, reports, _ = renderer.demo_data()
        report = reports["pass"]
        gate = next(c for c in report["checks"] if c["id"] == "completion_gate")
        gate.update(status="fail", observed="仍有第二项待处理即最终结束")
        self.assertEqual(renderer.assess(manifest, report)[0], "fail")
        gate.update(status="unknown", evidence_refs=[])
        self.assertEqual(renderer.assess(manifest, report)[0], "insufficient")

    def test_agent_flow_requires_scope_and_specialized_checks(self):
        manifest, reports, _ = renderer.demo_data()
        del manifest["audit_scope"]
        with self.assertRaises(renderer.ContractError):
            renderer.validate_manifest(manifest)
        manifest, reports, _ = renderer.demo_data()
        manifest["cases"][0]["required_check_ids"].remove("completion_gate")
        with self.assertRaises(renderer.ContractError):
            renderer.validate_manifest(manifest)
        manifest, reports, _ = renderer.demo_data()
        del reports["fail"]["findings"][0]["scope"]
        with self.assertRaises(renderer.ContractError):
            renderer.validate_case(reports["fail"], manifest)
        with self.assertRaisesRegex(renderer.ContractError, "schema_version"):
            renderer.validate_case(self.reports["pass"], manifest)

    def test_agent_flow_axes_remain_independent(self):
        manifest, reports, synthesis = renderer.demo_data()
        # fail案例执行不通过，设计与结束时机已有完整通过证据。
        page = renderer.render_summary(manifest, reports, synthesis, set())
        self.assertIn("设计遵循通过案例占比</td><td>3 / 4", page)
        self.assertIn("流程完成率</td><td>2 / 4", page)
        self.assertIn("结束时机正确率</td><td>3 / 4", page)
        self.assertIn("工具遵循率", page)
        statistics = renderer.audit_statistics(manifest, reports)
        self.assertEqual(statistics["case_axes"]["design"]["pass"], 3)
        self.assertEqual(statistics["case_axes"]["execution"]["pass"], 2)
        self.assertNotEqual(statistics["design_adherence"]["total"], statistics["total"])

    def test_axis_examples_distinguish_admission_closure_and_prefetch(self):
        examples = {e["id"]: e for e in renderer.axis_examples()}
        admission = examples["admission-fails-but-closed"]
        self.assertEqual(admission["classification"], "fail")
        self.assertEqual(admission["verdict"], {"design": "fail", "execution": "pass", "smoothness": "smooth"})
        self.assertEqual(admission["check_statuses"]["tool_usage"], "fail")
        self.assertEqual(admission["check_statuses"]["completion_gate"], "pass")
        unfinished = examples["unfinished-item"]
        self.assertEqual(unfinished["verdict"]["execution"], "fail")
        self.assertEqual(unfinished["check_statuses"]["completion_gate"], "fail")
        self.assertEqual(examples["rule-prefetch"]["classification"], "pass")

    def test_rule_examples_anchor_actions_scope_and_required_query_closure(self):
        for version in (1, 2, 3):
            with self.subTest(version=version):
                examples = {item["id"]: item for item in renderer.axis_examples(version)}
                care = examples["normal-task-care"]
                self.assertEqual(care["classification"], "pass")
                self.assertIn("Message 标签不证明", care["description"])
                extra = examples["independent-proactive-item"]
                self.assertEqual(extra["classification"], "fail")
                self.assertEqual(extra["verdict"]["execution"], "pass")
                query = examples["unfinished-required-query"]
                self.assertEqual(query["classification"], "fail")
                self.assertEqual(query["verdict"]["execution"], "fail")
                for key in ("design", "execution") + (("tool_usage", "completion_gate") if version > 1 else ()):
                    self.assertEqual(query["check_statuses"][key], "fail")
                self.assertEqual(examples["excluded-domain-detail"]["classification"], "pass")
                self.assertIn("本合成审计", examples["excluded-domain-detail"]["audit_exclusions"][0])
                if version == 3:
                    self.assertEqual([call["status"] for call in query["tool_usage_review"]["calls"]], ["fail", "pass", "pass"])
                    self.assertEqual(len(query["tool_usage_review"]["calls"]), 3)
                    self.assertEqual(extra["tool_usage_review"]["calls"][1]["status"], "fail")
                    self.assertEqual(care["tool_usage_review"]["calls"][0]["tool_name"], "push")

    def test_agent_flow_struggles_not_raw_counters_determine_recovery(self):
        manifest, reports, _ = renderer.demo_data()
        report = reports["pass"]
        report["metrics"]["business_errors"] = 1
        self.assertEqual(renderer.assess(manifest, report)[0], "pass")
        report["metrics"]["retries"] = 1
        self.assertEqual(renderer.assess(manifest, report)[0], "recovered")
        report["struggles"] = reports["recovered"]["struggles"]
        self.assertEqual(renderer.assess(manifest, report)[0], "recovered")

    def call_fixture(self, statuses_by_case):
        manifest, reports, _ = renderer.demo_data(version=3)
        selected = manifest["cases"][:len(statuses_by_case)]
        base = reports["pass"]
        reports = {}
        for case, statuses in zip(selected, statuses_by_case):
            report = copy.deepcopy(base)
            report.update(case_key=case["case_key"], title=case["title"])
            report["tool_usage_review"] = {"inventory_complete": True, "calls": [
                {"message_id": "synthetic-message", "tool_call_id": "call-%d" % i,
                 "tool_name": "synthetic-tool", "status": status,
                 "reason": "合成调用符合规则。" if status == "pass" else "合成参数错误。" if status == "fail" else "缺少合成回包记录。",
                 "evidence_refs": ["e-result"] if status != "unknown" else []}
                for i, status in enumerate(statuses, 1)
            ]}
            report["metrics"]["tool_calls"] = len(statuses)
            reports[case["case_key"]] = report
        manifest["cases"] = selected
        renderer.validate_manifest(manifest)
        for report in reports.values():
            renderer.validate_case(report, manifest)
        return manifest, reports, {"summary": "只用于调用统计回归。", "issues": []}

    def test_tool_usage_weights_all_calls_not_case_average(self):
        manifest, reports, synthesis = self.call_fixture([["fail"], ["pass"] * 9])
        usage = renderer.tool_usage_stats(manifest, reports)
        self.assertEqual(usage, {"total": 10, "pass": 9, "fail": 1, "unknown": 0, "incomplete_cases": 0, "inventory_complete": True})
        page = renderer.render_summary(manifest, reports, synthesis, set())
        self.assertIn('工具遵循率<span id="tool-drilldown-label">展开 ▾</span></button></td><td>9 / 10</td><td>90.0%', page)
        self.assertNotIn("50.0%", page)
        self.assertNotIn("按调用总数加权，不取案例平均", page)
        checks_total = sum(len(report["checks"]) for report in reports.values())
        self.assertIn("设计遵循率</td><td>" + str(checks_total) + " / " + str(checks_total), page)
        for axis in ("设计遵循通过案例占比", "流程完成率", "结束时机正确率"):
            self.assertIn(axis + "</td><td>2 / 2", page)
        self.assertNotIn("总调用数</td><td>10", page)
        self.assertIn('<td>synthetic-tool</td><td>10</td><td>9</td><td>1</td><td>0</td><td>90.0%</td>', page)

    def test_tool_usage_retry_fail_and_pass_keep_original_and_recovery(self):
        manifest, reports, _ = renderer.demo_data(version=3)
        report = reports["recovered"]
        self.assertEqual([c["status"] for c in report["tool_usage_review"]["calls"]], ["fail", "pass"])
        self.assertEqual(renderer.assess(manifest, report)[0], "recovered")
        one = copy.deepcopy(manifest)
        one["cases"] = [next(c for c in manifest["cases"] if c["case_key"] == "recovered")]
        page = renderer.render_summary(one, {"recovered": report}, {"summary": "合成", "issues": []}, set())
        self.assertIn('工具遵循率<span id="tool-drilldown-label">展开 ▾</span></button></td><td>1 / 2</td><td>50.0%', page)
        self.assertIn("设计遵循率</td><td>" + str(len(report["checks"])) + " / " + str(len(report["checks"])), page)
        report["tool_usage_review"]["calls"][0]["reason"] = "初次参数错误；修正成功不抹去首次失败。"
        renderer.validate_case(report, manifest)
        self.assertEqual(report["tool_usage_review"]["calls"][0]["status"], "fail")

    def test_tool_usage_platform_failure_can_pass_and_inventory_is_not_verdict(self):
        manifest, reports, _ = self.call_fixture([["pass"]])
        report = reports["pass"]
        report["metrics"]["tool_failures"] = 1
        report["metrics"]["business_errors"] = 1
        report["tool_usage_review"]["calls"][0]["reason"] = "请求正确；平台不可用后按规则交接受阻并继续独立项。"
        renderer.validate_case(report, manifest)
        self.assertEqual(renderer.assess(manifest, report)[0], "pass")
        self.assertEqual(renderer.tool_usage_stats(manifest, reports)["pass"], 1)
        report["tool_usage_review"]["calls"][0].update(status="fail", reason="首次参数错误。")
        # 调用 fail 自身不能替代原检查、findings 和 struggles 的整体分类。
        self.assertEqual(renderer.assess(manifest, report)[0], "pass")
        report["tool_usage_review"]["inventory_complete"] = False
        self.assertEqual(renderer.assess(manifest, report)[0], "pass")

    def test_tool_usage_unknown_keeps_known_denominator(self):
        manifest, reports, synthesis = self.call_fixture([["pass", "unknown"]])
        usage = renderer.tool_usage_stats(manifest, reports)
        self.assertEqual((usage["total"], usage["pass"], usage["unknown"]), (2, 1, 1))
        page = renderer.render_summary(manifest, reports, synthesis, set())
        self.assertIn('工具遵循率<span id="tool-drilldown-label">展开 ▾</span></button></td><td>1 / 2</td><td>50.0%', page)
        self.assertIn('<td>synthetic-tool</td><td>2</td><td>1</td><td>0</td><td>1</td><td>50.0%</td>', page)
        detail = renderer.render_case(manifest, reports["pass"])
        self.assertIn("无法判断", detail)
        self.assertIn("缺少合成回包记录", detail)

    def test_tool_usage_zero_calls_and_zero_cases_are_na(self):
        manifest, reports, synthesis = self.call_fixture([[]])
        report = reports["pass"]
        page = renderer.render_summary(manifest, reports, synthesis, set())
        self.assertIn('工具遵循率<span id="tool-drilldown-label">展开 ▾</span></button></td><td>0 / 0</td><td>N/A', page)
        self.assertIn("工具遵循率=0/0 · N/A", renderer.render_case(manifest, report))
        manifest["cases"] = []
        page = renderer.render_summary(manifest, {}, synthesis, set())
        self.assertIn('工具遵循率<span id="tool-drilldown-label">展开 ▾</span></button></td><td>0 / 0</td><td>N/A', page)
        self.assertNotIn("100.0%", page)

    def test_tool_usage_partial_inventory_warns_and_never_fills_unknown_total(self):
        manifest, reports, synthesis = self.call_fixture([["pass", "unknown"], ["pass"]])
        report = reports["pass"]
        report["tool_usage_review"]["inventory_complete"] = False
        report["metrics"]["tool_calls"] = 20
        renderer.validate_case(report, manifest)
        page = renderer.render_summary(manifest, reports, synthesis, set())
        self.assertIn("1 个 case 的调用清单不完整", page)
        self.assertIn('已知调用正确占比（非全量工具遵循率）<span id="tool-drilldown-label">展开 ▾</span></button></td><td>2 / 3</td><td>66.7%', page)
        self.assertIn('<th scope="col">已知调用数（总量下限）</th>', page)
        metadata = page.split('<div class="metadata-body">', 1)[1].split('</details>', 1)[0]
        self.assertIn("只能声称已知调用正确占比", metadata)
        self.assertIn("未知总量不补写", metadata)
        self.assertNotIn("工具遵循率=", page)
        self.assertNotIn("2/21", page)
        self.assertIn("调用清单不完整", renderer.render_case(manifest, report))
        usage = renderer.tool_usage_stats(manifest, {"fail": reports["fail"]})
        self.assertEqual((usage["total"], usage["incomplete_cases"], usage["inventory_complete"]), (1, 1, False))
        report["tool_usage_review"]["calls"] = []
        report["metrics"]["tool_calls"] = None
        renderer.validate_case(report, manifest)
        self.assertIn("已知调用正确占比（非全量工具遵循率）=0/0 · N/A", renderer.render_case(manifest, report))
        self.assertNotIn("本 case 没有实际工具调用", renderer.render_case(manifest, report))

    def test_tool_usage_strict_fields_ids_refs_status_and_inventory(self):
        manifest, reports, _ = self.call_fixture([["pass"]])
        base = reports["pass"]
        mutations = (
            ("缺review", lambda r: r.pop("tool_usage_review")),
            ("额外review字段", lambda r: r["tool_usage_review"].update(extra=1)),
            ("非布尔完整性", lambda r: r["tool_usage_review"].update(inventory_complete=1)),
            ("非数组calls", lambda r: r["tool_usage_review"].update(calls={})),
            ("额外调用字段", lambda r: r["tool_usage_review"]["calls"][0].update(extra="bad")),
            ("缺原因", lambda r: r["tool_usage_review"]["calls"][0].pop("reason")),
            ("数字ID", lambda r: r["tool_usage_review"]["calls"][0].update(message_id=1)),
            ("空ID", lambda r: r["tool_usage_review"]["calls"][0].update(tool_call_id="")),
            ("空工具名", lambda r: r["tool_usage_review"]["calls"][0].update(tool_name=" ")),
            ("空原因", lambda r: r["tool_usage_review"]["calls"][0].update(reason=" ")),
            ("未知状态", lambda r: r["tool_usage_review"]["calls"][0].update(status="success")),
            ("pass缺引用", lambda r: r["tool_usage_review"]["calls"][0].update(evidence_refs=[])),
            ("fail缺引用", lambda r: r["tool_usage_review"]["calls"][0].update(status="fail", evidence_refs=[])),
            ("引用不存在", lambda r: r["tool_usage_review"]["calls"][0].update(evidence_refs=["not-present"])),
            ("非数组引用", lambda r: r["tool_usage_review"]["calls"][0].update(evidence_refs="e-result")),
            ("重复引用", lambda r: r["tool_usage_review"]["calls"][0].update(evidence_refs=["e-result", "e-result"])),
            ("重复调用键", lambda r: r["tool_usage_review"]["calls"].append(copy.deepcopy(r["tool_usage_review"]["calls"][0]))),
            ("完整数超出", lambda r: r["metrics"].update(tool_calls=2)),
            ("完整数不足", lambda r: r["metrics"].update(tool_calls=0)),
            ("非整数总量", lambda r: r["metrics"].update(tool_calls=1.5)),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                report = copy.deepcopy(base)
                mutate(report)
                with self.assertRaises(renderer.ContractError):
                    renderer.validate_case(report, manifest)
        report = copy.deepcopy(base)
        report["tool_usage_review"]["inventory_complete"] = False
        report["metrics"]["tool_calls"] = 0
        with self.assertRaisesRegex(renderer.ContractError, "下限"):
            renderer.validate_case(report, manifest)
        report["metrics"]["tool_calls"] = None
        renderer.validate_case(report, manifest)
        report["tool_usage_review"]["inventory_complete"] = True
        renderer.validate_case(report, manifest)
        report["tool_usage_review"]["calls"].append(dict(report["tool_usage_review"]["calls"][0], message_id="second-message"))
        renderer.validate_case(report, manifest)  # 跨消息相同tool_call_id允许，复合键不同。
        self.assertEqual(renderer.tool_usage_stats(manifest, {"pass": report})["total"], 2)

    def test_tool_usage_versions_are_not_upgraded_or_mixed(self):
        for version in (1, 2, 3):
            manifest, reports, synthesis = renderer.demo_data(version=version)
            renderer.validate_manifest(manifest)
            for report in reports.values():
                renderer.validate_case(report, manifest)
                self.assertEqual(report["schema_version"], version)
            page = renderer.render_summary(manifest, reports, synthesis, set())
            self.assertEqual(renderer.agent_flow(manifest), version in (2, 3))
            statistics = renderer.audit_statistics(manifest, reports)
            self.assertEqual(statistics["statistics_schema_version"], 2)
            self.assertNotIn("tool_usage", statistics)
            self.assertNotIn("工具正确使用率", page)
            if version < 3:
                self.assertIn("工具遵循率（历史格式无逐调用数据）", page)
                self.assertIn("设计遵循率", page)
                self.assertNotIn('id="tool-drilldown-toggle"', page)
                self.assertNotIn("逐调用工具使用检查", renderer.render_case(manifest, reports["pass"]))
                self.assertNotIn("tool_usage_review", reports["pass"])
                self.assertIsNone(statistics["tool_adherence"])
                self.assertEqual(statistics["tool_adherence_by_tool"], [])
                self.assertEqual(statistics["design_adherence"]["total"], sum(len(report["checks"]) for report in reports.values()))
                with self.assertRaises(renderer.ContractError):
                    renderer.tool_adherence_stats(manifest, reports)
                with self.assertRaises(renderer.ContractError):
                    renderer.tool_usage_stats(manifest, reports)
        new_manifest, new_reports, _ = renderer.demo_data(version=3)
        for historical in (1, 2):
            old_manifest, old_reports, _ = renderer.demo_data(version=historical)
            with self.assertRaisesRegex(renderer.ContractError, "schema_version"):
                renderer.validate_case(old_reports["pass"], new_manifest)
            with self.assertRaisesRegex(renderer.ContractError, "schema_version"):
                renderer.validate_case(new_reports["pass"], old_manifest)
        for version in (True, 0, 4, "3"):
            with self.assertRaises(renderer.ContractError):
                renderer.demo_data(version=version)

    def test_tool_usage_html_escapes_text_hides_sensitive_ids_and_links(self):
        manifest, reports, synthesis = self.call_fixture([["pass"]])
        call = reports["pass"]["tool_usage_review"]["calls"][0]
        call.update(message_id="sensitive-message-not-for-html", tool_call_id="sensitive-call-not-for-html")
        payload = '<script>alert("x")</script><img src=x onerror=alert(1)>{{TITLE}}'
        call.update(tool_name=payload, reason=payload)
        reports["pass"]["evidence_index"][0]["excerpt"] = "敏感证据原文不进HTML"
        renderer.validate_case(reports["pass"], manifest)
        for page in (renderer.render_case(manifest, reports["pass"]), renderer.render_summary(manifest, reports, synthesis, set())):
            for forbidden in ("sensitive-message-not-for-html", "sensitive-call-not-for-html", "敏感证据原文不进HTML", '<script>alert("x")', "<img"):
                self.assertNotIn(forbidden, page)
            self.assertIn("&lt;script&gt;", page)
            self.assertIn("{{TITLE}}", page)
            parser = Links()
            parser.feed(page)
            self.assertEqual(len(parser.scripts), 1)
        parser = Links()
        parser.feed(renderer.render_case(manifest, reports["pass"]))
        self.assertEqual(parser.links, ["../index.html"])
        parser = Links()
        parser.feed(renderer.render_summary(manifest, reports, synthesis, set()))
        self.assertIn("cases/pass.html", parser.links)

    def test_summary_omits_the_entire_coverage_table(self):
        for version in (1, 2, 3):
            manifest, reports, synthesis = renderer.demo_data(version=version)
            page = renderer.render_summary(manifest, reports, synthesis, set())
            for label in ("覆盖率（完整 / 全部样本）", "已可判定分析（非证据不足）", "JSON 报告", "原始 case 页面（不含占位）", "设计基线", "调用轨迹", "思考证据"):
                self.assertNotIn("<td>" + label + "</td>", page)
            self.assertNotIn("覆盖率（完整 / 全部样本）", page)
            self.assertIn("覆盖与限制", renderer.render_case(manifest, reports["pass"]))

    def test_design_adherence_counts_all_checks_not_case_average(self):
        manifest = {"cases": [{"case_key": "first", "required_check_ids": []}, {"case_key": "second", "required_check_ids": []}]}
        reports = {"first": {"checks": [{"id": "same", "status": "fail"}]}, "second": {"checks": [{"id": "same" if i == 0 else "check-%d" % i, "status": "pass"} for i in range(9)]}}
        result = renderer.design_adherence_stats(manifest, reports)
        self.assertEqual(result, {"total": 10, "pass": 9, "fail": 1, "unknown": 0, "incomplete_cases": 0, "checks_complete": True})
        self.assertEqual(renderer.ratio(result["pass"], result["total"]), "90.0%")
        # 相同检查ID在不同case各计一条，范围外报告不计入分母。
        reports["outside"] = {"checks": [{"id": "outside", "status": "fail"}]}
        self.assertEqual(renderer.design_adherence_stats(manifest, reports), result)

    def test_design_adherence_unknown_and_empty_checks(self):
        manifest = {"cases": [{"case_key": "first", "required_check_ids": []}]}
        reports = {"first": {"checks": [{"id": "pass", "status": "pass"}, {"id": "unknown", "status": "unknown"}]}}
        result = renderer.design_adherence_stats(manifest, reports)
        self.assertEqual((result["total"], result["pass"], result["unknown"]), (2, 1, 1))
        self.assertEqual(renderer.ratio(result["pass"], result["total"]), "50.0%")
        reports["first"]["checks"] = []
        result = renderer.design_adherence_stats(manifest, reports)
        self.assertEqual(result["total"], 0)
        self.assertEqual(renderer.ratio(result["pass"], result["total"]), "N/A")
        manifest["cases"] = []
        self.assertEqual(renderer.design_adherence_stats(manifest, {})["total"], 0)

    def test_design_adherence_missing_reports_and_required_checks_are_partial(self):
        manifest = {"cases": [{"case_key": "first", "required_check_ids": ["required"]}, {"case_key": "second", "required_check_ids": []}]}
        reports = {"first": {"checks": [{"id": "other", "status": "pass"}]}}
        result = renderer.design_adherence_stats(manifest, reports)
        self.assertEqual(result, {"total": 1, "pass": 1, "fail": 0, "unknown": 0, "incomplete_cases": 2, "checks_complete": False})
        # 没拿到的检查总量不按case数补造。
        manifest, reports, synthesis = self.call_fixture([["pass"], ["pass"]])
        page = renderer.render_summary(manifest, {"pass": reports["pass"]}, synthesis, set())
        self.assertIn("已知检查通过占比（非完整设计遵循率）", page)
        self.assertIn("缺报告或必要检查", page)
        self.assertNotIn("<td>设计遵循率</td>", page)

    def test_design_adherence_uses_checks_independently_of_calls_and_verdict(self):
        manifest, reports, synthesis = self.call_fixture([["fail"], ["pass"] * 9])
        reports["pass"]["checks"][0]["status"] = "fail"
        reports["fail"]["checks"][1]["status"] = "unknown"
        result = renderer.design_adherence_stats(manifest, reports)
        expected_total = sum(len(report["checks"]) for report in reports.values())
        self.assertEqual((result["total"], result["pass"], result["fail"], result["unknown"]), (expected_total, expected_total - 2, 1, 1))
        page = renderer.render_summary(manifest, reports, synthesis, set())
        self.assertIn("设计遵循率</td><td>" + str(expected_total - 2) + " / " + str(expected_total), page)
        self.assertIn('工具遵循率<span id="tool-drilldown-label">展开 ▾</span></button></td><td>9 / 10</td><td>90.0%', page)
        self.assertNotIn("所有选定案例的 checks 中状态为 pass 的条数 / 所有选定案例的 checks 总条数", page)
        self.assertNotIn("工具使用案例检查</td><td>", page)
        self.assertIn("工具使用案例检查", renderer.render_case(manifest, reports["pass"]))

    def test_summary_native_metric_order_and_collapsed_controls(self):
        manifest, reports, synthesis = renderer.demo_data(version=3)
        reports["insufficient"]["tool_usage_review"]["inventory_complete"] = True
        page = renderer.render_summary(manifest, reports, synthesis, set())
        body = page.split('<th scope="col">分项指标</th>', 1)[1].split('<tbody>', 1)[1]
        self.assertTrue(body.startswith('<tr><td>设计遵循率</td>'))
        positions = [body.index(label) for label in ("设计遵循率", "设计遵循通过案例占比", "流程完成率", "结束时机正确率", 'id="tool-drilldown-toggle"')]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('type="button" aria-expanded="false" aria-controls="tool-drilldown-row"', page)
        self.assertIn('<tr id="tool-drilldown-row" hidden><td colspan="5">', page)
        self.assertIn('<details id="metadata-details" class="metadata-details">', page)
        self.assertEqual(page.count('<th scope="col">工具名</th>'), 1)
        self.assertEqual(page.count('<th scope="col">调用清单</th>'), 1)
        for label in ("工具名", "调用总数", "正确", "错误", "无法判断", "使用正确率"):
            self.assertIn('<th scope="col">' + label + '</th>', page)
        detail = renderer.render_case(manifest, reports["pass"])
        self.assertIn("工具遵循率=1/1 · 100.0%", detail)
        self.assertNotIn("工具正确使用率", detail)
        self.assertIn("计算方式：正确实际调用数", detail)
        self.assertIn("调用时间线", detail)

    def test_tool_grouping_sums_to_calls_and_never_uses_handwritten_totals(self):
        manifest, reports, synthesis = self.call_fixture([["pass", "unknown"], ["fail", "pass", "pass"]])
        reports["pass"]["tool_usage_review"]["calls"][0]["tool_name"] = "skill_read"
        reports["fail"]["tool_usage_review"]["calls"][1]["tool_name"] = "skill_read"
        reports["outside"] = copy.deepcopy(reports["pass"])
        statistics = renderer.audit_statistics(manifest, reports)
        expected = [
            {"tool_name": "skill_read", "total": 2, "pass": 2, "fail": 0, "unknown": 0, "rate": "100.0%"},
            {"tool_name": "synthetic-tool", "total": 3, "pass": 1, "fail": 1, "unknown": 1, "rate": "33.3%"},
        ]
        self.assertEqual(statistics["tool_adherence_by_tool"], expected)
        for key in ("total", "pass", "fail", "unknown"):
            self.assertEqual(sum(item[key] for item in expected), statistics["tool_adherence"][key])
        self.assertEqual(statistics["tool_adherence"]["rate"], "60.0%")
        page = renderer.render_summary(manifest, reports, synthesis, set(), statistics)
        self.assertIn('<td>skill_read</td><td>2</td><td>2</td><td>0</td><td>0</td><td>100.0%</td>', page)
        self.assertEqual(renderer.tool_usage_stats(manifest, reports), renderer.tool_adherence_stats(manifest, reports))
        reports["fail"]["tool_usage_review"]["calls"][0]["status"] = "pass"
        self.assertEqual(renderer.audit_statistics(manifest, reports)["tool_adherence"]["pass"], 4)

    def test_summary_machine_statistics_keeps_axes_and_checks_independent(self):
        self.cases()
        result = self.summary()
        expected = renderer.audit_statistics(self.manifest, self.reports)
        self.assertEqual(result, dict(expected, out=str(self.root / "output/index.html")))
        self.assertIsNone(result["tool_adherence"])
        self.assertNotIn("tool_usage", result)
        self.assertEqual(result["statistics_schema_version"], 2)
        self.assertEqual(result["counts"]["fail"], 1)
        self.assertEqual(result["design_adherence"]["total"], 20)
        self.assertEqual(result["case_axes"]["design"]["total"], 4)
        self.assertEqual(sum(result["counts"].values()), result["total"])
        self.assertEqual(result["design_adherence"]["unknown"], 1)
        self.assertIn("checks", result["metric_definitions"]["design_adherence"])
        self.assertIn("历史 v1/v2", result["metric_definitions"]["tool_adherence"])

    def test_controls_expand_collapse_and_theme_without_removed_filters(self):
        source = (renderer.TEMPLATES / "controls.js").read_text()
        for removed in ("case-search", "case-status", "case-table", "visible-count", "filter", "innerHTML"):
            self.assertNotIn(removed, source)
        # 浏览器原生 button 提供 Enter/Space；这里直接执行相同 click 处理，核对 aria 和 hidden 同步。
        setup = r'''
const assert = require("node:assert/strict");
const nodes = {};
for (const id of ["theme-choice", "tool-drilldown-toggle", "tool-drilldown-row", "tool-drilldown-label"]) {
  nodes[id] = {events: {}, attrs: {}, hidden: id === "tool-drilldown-row",
    addEventListener(name, action) {this.events[name] = action;},
    getAttribute(name) {return this.attrs[name] ?? null;},
    setAttribute(name, value) {this.attrs[name] = value;}};
}
const root = {attrs: {}, setAttribute(key, value) {this.attrs[key] = value;}, removeAttribute(key) {delete this.attrs[key];}};
global.document = {getElementById(id) {return nodes[id] ?? null;}, documentElement: root};
'''
        checks = r'''
const button = nodes["tool-drilldown-toggle"], row = nodes["tool-drilldown-row"], label = nodes["tool-drilldown-label"];
button.events.click();
assert.equal(button.attrs["aria-expanded"], "true"); assert.equal(row.hidden, false); assert.equal(label.textContent, "收起 ▴");
button.events.click();
assert.equal(button.attrs["aria-expanded"], "false"); assert.equal(row.hidden, true); assert.equal(label.textContent, "展开 ▾");
for (const theme of ["dark", "light", "system"]) {
  nodes["theme-choice"].value = theme; nodes["theme-choice"].events.change();
  assert.equal(root.attrs["data-theme"], theme === "system" ? undefined : theme);
}
process.stdout.write("ok");
'''
        try:
            result = subprocess.run(["node", "-e", setup + source + checks], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            self.skipTest("Node.js 不可用，浏览器交互验收另行覆盖")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "ok")
        # 详情页没有下钻控件，主题仍工作且初始化不抛错。
        result = subprocess.run(["node", "-e", setup + 'delete nodes["tool-drilldown-toggle"];\n' + source], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_report_styles_keep_scroll_in_table_and_both_themes(self):
        style = (renderer.TEMPLATES / "style.css").read_text()
        self.assertIn('padding-left: max(16px, env(safe-area-inset-left, 0px))', style)
        self.assertIn('padding-right: max(16px, env(safe-area-inset-right, 0px))', style)
        self.assertIn('.table-scroll { max-width: 100%; overflow-x: auto;', style)
        self.assertIn(':root:not([data-theme="light"])', style)
        self.assertIn(':root[data-theme="dark"]', style)
        self.assertIn(':focus-visible', style)
        self.assertIn('background: var(--bg); color: var(--fg)', style)
        self.assertNotIn('.filters', style)

    def test_cli_demo_deterministic_private_synthetic(self):
        dest = self.root / "demo"
        command = [sys.executable, "-I", "-B", str(SCRIPT), "demo", "--out", str(dest)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["counts"]["pass"], 1)
        self.assertEqual(output["tool_adherence"], {"total": 5, "pass": 3, "fail": 1, "unknown": 1, "incomplete_cases": 1, "inventory_complete": False, "rate": "60.0%"})
        self.assertNotIn("tool_usage", output)
        self.assertEqual(renderer.load_json(dest / "manifest.json")["schema_version"], 3)
        demo_manifest = renderer.load_json(dest / "manifest.json")
        demo_reports = {case["case_key"]: renderer.load_json(dest / "reports" / (case["case_key"] + ".json")) for case in demo_manifest["cases"]}
        expected_design = renderer.design_adherence_stats(demo_manifest, demo_reports)
        self.assertEqual(output["design_adherence"], dict(expected_design, rate=renderer.ratio(expected_design["pass"], expected_design["total"])))
        self.assertEqual(output["tool_adherence"], dict(renderer.tool_adherence_stats(demo_manifest, demo_reports), rate="60.0%"))
        self.assertIn("合成", (dest / "index.html").read_text())
        for file in dest.rglob("*.html"):
            parser = Links()
            parser.feed(file.read_text())
            for link in parser.links:
                self.assertTrue((file.parent / link).is_file(), (file, link))
        duplicate = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(duplicate.returncode, 2)
        other = self.root / "demo-two"
        result = subprocess.run(command[:-1] + [str(other)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((dest / "index.html").read_bytes(), (other / "index.html").read_bytes())
        self.assertEqual((dest / "cases/pass.html").read_bytes(), (other / "cases/pass.html").read_bytes())
        self.assertNotIn("innerHTML", (renderer.TEMPLATES / "controls.js").read_text())


if __name__ == "__main__":
    unittest.main()
