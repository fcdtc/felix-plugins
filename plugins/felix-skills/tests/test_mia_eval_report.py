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
        self.assertIn("顺畅通过率=1/4 · 25.0%", summary)
        self.assertIn("影响 2 个 case", summary)
        for file in (self.root / "output").rglob("*.html"):
            links = Links()
            links.feed(file.read_text())
            self.assertEqual(len(links.scripts), 1)
            self.assertNotIn("src", links.scripts[0])
            for link in links.links:
                self.assertTrue((file.parent / link).is_file(), (file, link))

    def test_summary_omits_limitations_section_but_case_keeps_it(self):
        for version in (1, 2):
            with self.subTest(version=version):
                manifest, reports, synthesis = renderer.demo_data(version=version)
                report = reports["insufficient"]
                report["limitations"].append("仅详情展示的限制说明")
                summary = renderer.render_summary(manifest, reports, synthesis, set())
                self.assertNotIn("证据缺口与限制", summary)
                self.assertNotIn("gaps-heading", summary)
                self.assertNotIn("仅详情展示的限制说明", summary)
                self.assertIn("证据不足", summary)
                self.assertIn("判定依据 / 缺口", summary)
                detail = renderer.render_case(manifest, report)
                self.assertIn("覆盖与限制", detail)
                self.assertIn("仅详情展示的限制说明", detail)

    def test_case_shows_plain_checks_without_evidence_or_findings(self):
        for version in (1, 2):
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
        self.synthesis["recommendations"] = []
        self.dump("manifest.json", self.manifest)
        self.dump("synthesis.json", self.synthesis)
        for path in (self.root / "reports").iterdir():
            path.unlink()
        result = self.summary()
        self.assertEqual(result["total"], 0)
        page = (self.root / "output/index.html").read_text()
        self.assertIn("顺畅通过率=0/0 · N/A", page)
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
        self.synthesis["recommendations"][0]["case_keys"] = ["not-present"]
        with self.assertRaises(renderer.ContractError):
            renderer.validate_synthesis(self.synthesis, self.manifest)
        report = self.reports["pass"]
        report["aggregate_status"] = "pass"
        with self.assertRaises(renderer.ContractError):
            renderer.validate_case(report, self.manifest)

    def test_partial_scope_and_axis_unknown_visible(self):
        self.manifest["scope_complete"] = False
        page = renderer.render_summary(self.manifest, self.reports, self.synthesis, set())
        for text in ("部分清单，仅对已取得样本统计", "设计遵循率", "任务完成率", "unknown", "思考证据", "已可判定分析"):
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
        self.assertIn("设计遵循率</td><td>3 / 4", page)
        self.assertIn("流程完成率</td><td>2 / 4", page)
        self.assertIn("结束时机正确率</td><td>3 / 4", page)
        self.assertIn("工具正确使用率", page)

    def test_agent_flow_struggles_not_raw_counters_determine_recovery(self):
        manifest, reports, _ = renderer.demo_data()
        report = reports["pass"]
        report["metrics"]["business_errors"] = 1
        self.assertEqual(renderer.assess(manifest, report)[0], "pass")
        report["metrics"]["retries"] = 1
        self.assertEqual(renderer.assess(manifest, report)[0], "recovered")
        report["struggles"] = reports["recovered"]["struggles"]
        self.assertEqual(renderer.assess(manifest, report)[0], "recovered")

    def test_cli_demo_deterministic_private_synthetic(self):
        dest = self.root / "demo"
        command = [sys.executable, "-I", "-B", str(SCRIPT), "demo", "--out", str(dest)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["counts"]["pass"], 1)
        self.assertIn("合成", (dest / "index.html").read_text())
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
