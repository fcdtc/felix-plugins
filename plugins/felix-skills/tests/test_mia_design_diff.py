"""稀疏交付 Diff 回归；仅使用临时目录中的合成文件，不连接 MIA。"""

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "skills/mia-design/scripts/render_diff.py"
SPEC = importlib.util.spec_from_file_location("mia_design_diff", SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.resources = []
        self.pages = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article" and attrs.get("class") == "file-page":
            self.pages.append(attrs)
        if tag == "script":
            self.scripts.append(attrs)
        if attrs.get("src"):
            self.resources.append(attrs["src"])
        if tag == "link" and attrs.get("href"):
            self.resources.append(attrs["href"])


class DiffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "baseline").mkdir()
        (self.root / "updated").mkdir()
        self.manifest_entries = {}
        self.write_manifest()

    def write_manifest(self):
        sections = ["# 合成测试清单", "\n## 逐文件修改说明"]
        sections.extend(f"\n### {path}\n{body}" for path, body in self.manifest_entries.items())
        sections.append("\n## 应用顺序\n按固定版本合入改动文件。\n")
        (self.root / "replacement-manifest.md").write_text("\n".join(sections), encoding="utf-8")

    def put(self, side, relative, value):
        path = self.root / side / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value.encode("utf-8") if isinstance(value, str) else value)
        if relative not in self.manifest_entries:
            self.manifest_entries[relative] = "- 改了什么：合成修改用于测试。\n- 为什么改：合成原因用于检查逐文件绑定。\n- 预期效果与取舍：不连接真实平台。"
            self.write_manifest()
        return path

    def cli(self, *options):
        return subprocess.run(
            [sys.executable, "-I", "-B", str(SCRIPT), str(self.root), *options],
            text=True, capture_output=True,
        )

    def test_modified_file_has_two_sides_and_inline_changes(self):
        self.put("baseline", "system-prompt.md", "先查询对象\n只处理任务一\n")
        self.put("updated", "system-prompt.md", "先查询对象\n分别处理任务一和任务二\n")
        page, counts = renderer.render(self.root)
        self.assertEqual(counts["modified"], 1)
        self.assertIn("修改前 · baseline", page)
        self.assertIn("修改后 · updated", page)
        self.assertIn('class="removed"', page)
        self.assertIn('class="added"', page)
        self.assertIn("− 2", page)
        self.assertIn("+ 2", page)

    def test_sparse_files_missing_sides_are_not_assumed_added_or_deleted(self):
        self.put("updated", "skills/new/SKILL.md", "新正文")
        self.put("baseline", "skills/old/reference.md", "旧正文")
        page, counts = renderer.render(self.root)
        self.assertEqual(counts["no-baseline"], 1)
        self.assertEqual(counts["baseline-only"], 1)
        self.assertIn("是否新增或基线读取缺失", page)
        self.assertIn("是否删除", page)
        self.assertIn("修改前 ∅", page)
        self.assertIn("修改后 ∅", page)
        self.assertIn("new/SKILL.md", page)

    def test_tools_excluded_but_tool_sp_and_skill_nested_tools_included(self):
        self.put("baseline", "tools/component.json", '{"private": "old"}')
        self.put("updated", "tools/component.json", '{"private": "SENTINEL_TOOL_CONTENT"}')
        self.put("baseline", "tool-sp/component.md", "旧提示")
        self.put("updated", "tool-sp/component.md", "新提示")
        self.put("updated", "skills/component/tools/guide.md", "技能内部工具说明")
        page, counts = renderer.render(self.root)
        self.assertNotIn("SENTINEL_TOOL_CONTENT", page)
        self.assertNotIn("tools/component.json", page)
        self.assertIn("tool-sp/component.md", page)
        self.assertIn("skills/component/tools/guide.md", page)
        self.assertEqual(sum(counts.values()), 2)

    def test_tool_only_and_empty_deliveries_have_clear_empty_state(self):
        for tools in (False, True):
            with self.subTest(tools=tools):
                if tools:
                    self.put("updated", "tools/component.json", "{\"a\": 1}")
                page, counts = renderer.render(self.root)
                self.assertEqual(sum(counts.values()), 0)
                self.assertIn("没有可比较的文件", page)

    def test_identical_files_are_marked_unchanged(self):
        self.put("baseline", "user-prompt.md", "{{input}}")
        self.put("updated", "user-prompt.md", "{{input}}")
        page, counts = renderer.render(self.root)
        self.assertEqual(counts["unchanged"], 1)
        self.assertIn("无需作为改动文件交付", page)
        self.assertIn('data-status="unchanged"', page)

    def test_binary_and_invalid_utf8_are_not_rendered_as_text(self):
        self.put("baseline", "skills/component/assets/icon.bin", b"\x00\xffold")
        self.put("updated", "skills/component/assets/icon.bin", b"\x00\xffnew")
        page, counts = renderer.render(self.root)
        self.assertEqual(counts["modified"], 1)
        self.assertIn("二进制或非 UTF-8", page)
        self.assertIn("SHA-256", page)

    def test_empty_file_is_distinguished_from_missing(self):
        self.put("baseline", "system-prompt.md", b"")
        self.put("updated", "system-prompt.md", "补充规则")
        self.put("updated", "user-prompt.md", b"")
        _, counts = renderer.render(self.root)
        self.assertEqual(counts["modified"], 1)
        self.assertEqual(counts["no-baseline"], 1)

    def test_newline_only_differences_are_explicit(self):
        self.put("baseline", "user-prompt.md", b"input\r\n")
        self.put("updated", "user-prompt.md", b"input\n")
        page, counts = renderer.render(self.root)
        self.assertEqual(counts["modified"], 1)
        self.assertIn("换行符", page)
        self.assertIn("7 字节", page)
        self.assertIn("6 字节", page)

    def test_untrusted_paths_and_content_are_html_escaped(self):
        name = 'skills/component/<img onerror="bad">.md'
        self.put("updated", name, '<script>alert("bad")</script>\n<img src="https://invalid.test/a">')
        page, _ = renderer.render(self.root)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("&lt;img", page)
        parsed = PageParser()
        parsed.feed(page)
        self.assertEqual(parsed.resources, [])
        self.assertEqual(len(parsed.scripts), 1)
        self.assertNotIn("src", parsed.scripts[0])

    def test_context_limits_unchanged_lines_and_keeps_original_numbers(self):
        before = [f"KEEP_{i:03d}" for i in range(30)]
        after = list(before)
        after[15] = "CHANGED_LINE"
        self.put("baseline", "system-prompt.md", "\n".join(before))
        self.put("updated", "system-prompt.md", "\n".join(after))
        page, _ = renderer.render(self.root)
        self.assertNotIn("KEEP_000", page)
        self.assertIn("KEEP_012", page)
        self.assertIn("− 16", page)
        self.assertIn("+ 16", page)

    def test_symlink_file_and_directory_are_rejected(self):
        source = self.root / "private.md"
        source.write_text("PRIVATE")
        link = self.root / "updated/link.md"
        link.symlink_to(source)
        with self.assertRaises(renderer.DiffError):
            renderer.render(self.root)
        link.unlink()
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(renderer.DiffError):
            renderer.render(self.root)

    def test_missing_compare_directory_is_an_error(self):
        (self.root / "baseline").rmdir()
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("比较目录不存在", result.stderr)
        self.assertFalse((self.root / "diff.html").exists())

    def test_size_line_file_count_and_page_limits_are_errors(self):
        self.put("updated", "system-prompt.md", "12345\n67890\n")
        for limit in ("MAX_FILE_BYTES", "MAX_TOTAL_BYTES", "MAX_FILES", "MAX_LINES", "MAX_PAGE_BYTES"):
            with self.subTest(limit=limit), mock.patch.object(renderer, limit, 1):
                if limit == "MAX_FILES":
                    self.put("updated", "user-prompt.md", "input")
                with self.assertRaises(renderer.DiffError):
                    renderer.render(self.root)

    def test_cli_generates_self_contained_page_and_preserves_sources(self):
        before = self.put("baseline", "system-prompt.md", "旧规则")
        after = self.put("updated", "system-prompt.md", "新规则")
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        page = (self.root / "diff.html").read_text()
        self.assertTrue(page.startswith("<!doctype html>"))
        self.assertIn("Content-Security-Policy", page)
        self.assertEqual(before.read_text(), "旧规则")
        self.assertEqual(after.read_text(), "新规则")
        self.assertEqual(set(path.name for path in self.root.iterdir()), {"baseline", "updated", "replacement-manifest.md", "diff.html"})

    def test_cli_refuses_existing_output_and_regenerates_only_with_flag(self):
        self.put("updated", "system-prompt.md", "正文一")
        self.assertEqual(self.cli().returncode, 0)
        original = (self.root / "diff.html").read_bytes()
        self.put("updated", "system-prompt.md", "正文二")
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual((self.root / "diff.html").read_bytes(), original)
        self.assertEqual(self.cli("--overwrite").returncode, 0)
        self.assertIn("正文二", (self.root / "diff.html").read_text())

    def test_cli_rejects_output_symlink_even_with_overwrite(self):
        outside = self.root / "preserve.html"
        outside.write_text("保留")
        (self.root / "diff.html").symlink_to(outside)
        self.assertEqual(self.cli("--overwrite").returncode, 2)
        self.assertEqual(outside.read_text(), "保留")

    def test_paginated_html_opens_only_one_file_and_has_both_pagers(self):
        self.put("updated", "system-prompt.md", "系统正文")
        self.put("updated", "user-prompt.md", "用户正文")
        page, _ = renderer.render(self.root)
        parsed = PageParser()
        parsed.feed(page)
        self.assertEqual(len(parsed.pages), 2)
        self.assertNotIn("hidden", parsed.pages[0])
        self.assertIn("hidden", parsed.pages[1])
        for control in ("previous-file", "next-file", "file-picker", "page-counter", "previous-file-bottom", "next-file-bottom"):
            self.assertIn(f'id="{control}"', page)
        self.assertIn("hashchange", page)
        self.assertIn("ArrowRight", page)

    def test_each_page_attaches_only_its_own_manifest_reason_after_diff(self):
        self.put("updated", "system-prompt.md", "系统正文")
        self.put("updated", "user-prompt.md", "用户正文")
        self.manifest_entries = {
            "system-prompt.md": "- 改了什么：系统变化\n- 为什么改：SYSTEM_REASON\n- 验证情况：本地通过",
            "user-prompt.md": "- 改了什么：用户变化\n- 为什么改：USER_REASON",
        }
        self.write_manifest()
        page, _ = renderer.render(self.root)
        first, second = page.split('<article class="file-page"')[1:]
        self.assertIn("SYSTEM_REASON", first)
        self.assertNotIn("USER_REASON", first)
        self.assertIn("USER_REASON", second)
        self.assertGreater(first.index("SYSTEM_REASON"), first.index("</table>"))
        self.assertNotIn("按固定版本合入改动文件", page)

    def test_missing_reason_or_manifest_is_an_error_without_partial_output(self):
        self.put("updated", "system-prompt.md", "正文")
        for body in ("- 改了什么：有改动", "- 为什么改：\n", ""):
            with self.subTest(body=body):
                self.manifest_entries["system-prompt.md"] = body
                self.write_manifest()
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertIn("缺少逐文件修改原因", result.stderr)
                self.assertFalse((self.root / "diff.html").exists())
        (self.root / "replacement-manifest.md").unlink()
        self.assertEqual(self.cli().returncode, 2)

    def test_manifest_titles_inside_code_fence_are_not_file_sections(self):
        self.put("updated", "system-prompt.md", "正文")
        self.manifest_entries["system-prompt.md"] = "- 为什么改：真实原因\n```markdown\n### not-a-file.md\n- 为什么改：示例\n```"
        self.write_manifest()
        reasons = renderer.read_reasons(self.root)
        self.assertEqual(set(reasons), {"system-prompt.md"})

    def test_reason_text_is_escaped_and_tool_reason_is_not_in_pages(self):
        self.put("updated", "system-prompt.md", "正文")
        self.manifest_entries["system-prompt.md"] = '- 为什么改：<img src="https://invalid.test/x" onerror="bad">\n- 验证情况：<script>bad()</script>'
        self.manifest_entries["tools/private.json"] = "- 为什么改：EXCLUDED_TOOL_REASON"
        self.write_manifest()
        page, _ = renderer.render(self.root)
        parsed = PageParser()
        parsed.feed(page)
        self.assertEqual(parsed.resources, [])
        self.assertEqual(len(parsed.scripts), 1)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("EXCLUDED_TOOL_REASON", page)

    def test_manifest_duplicate_titles_and_symlink_are_rejected(self):
        self.put("updated", "system-prompt.md", "正文")
        manifest = self.root / "replacement-manifest.md"
        with manifest.open("a") as stream:
            stream.write("\n## 逐文件修改说明\n### system-prompt.md\n- 为什么改：重复\n")
        with self.assertRaises(renderer.DiffError):
            renderer.render(self.root)
        manifest.unlink()
        target = self.root / "notes.md"
        target.write_text("私有清单")
        manifest.symlink_to(target)
        with self.assertRaises(renderer.DiffError):
            renderer.render(self.root)

    def test_manifest_backtick_file_titles_and_multiline_reason(self):
        self.put("updated", "system-prompt.md", "正文")
        (self.root / "replacement-manifest.md").write_text(
            "## 逐文件修改说明\n### `system-prompt.md`\n- 为什么改：第一行\n  第二行补充证据\n- 验证情况：本地检查\n## 应用顺序\nOTHER_SECTION\n"
        )
        page, _ = renderer.render(self.root)
        self.assertIn("第一行\n  第二行补充证据", page)
        self.assertNotIn("OTHER_SECTION", page)


if __name__ == "__main__":
    unittest.main()
