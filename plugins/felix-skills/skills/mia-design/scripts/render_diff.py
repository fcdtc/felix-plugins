#!/usr/bin/env python3
"""从稀疏 baseline/ 和 updated/ 生成私有离线 Diff；仅用标准库。"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
from itertools import zip_longest
from pathlib import Path, PurePosixPath
import re
import sys
from datetime import datetime

MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_FILES = 300
MAX_LINES = 10000
MAX_INLINE_CHARS = 2000


class DiffError(ValueError):
    pass


def scan(root: Path) -> dict[str, Path]:
    if root.is_symlink() or not root.is_dir():
        raise DiffError(f"比较目录不存在、不是目录或为符号链接：{root}")
    files: dict[str, Path] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            relative = path.relative_to(root)
            if relative.parts[0] == "tools":
                continue  # Tool 定义完全排除，不读取其正文。
            if path.is_symlink():
                raise DiffError(f"比较目录中不接受符号链接：{relative.as_posix()}")
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                files[relative.as_posix()] = path
                if len(files) > MAX_FILES:
                    raise DiffError(f"比较文件超过 {MAX_FILES} 个，请缩小本轮范围")
            else:
                raise DiffError(f"比较目录中不接受特殊文件：{relative.as_posix()}")
    return files


def read_bytes(path: Path | None) -> bytes | None:
    if path is None:
        return None
    with path.open("rb") as stream:
        content = stream.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        raise DiffError(f"文件超过 1 MiB，无法完整比较：{path.name}")
    return content


def text_lines(content: bytes | None) -> list[str] | None:
    if content is None:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
        return None
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        raise DiffError(f"文本超过 {MAX_LINES} 行，请缩小本轮范围")
    return lines


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def inline_diff(before: str, after: str) -> tuple[str, str]:
    if max(len(before), len(after)) > MAX_INLINE_CHARS:
        return escape(before), escape(after)
    left: list[str] = []
    right: list[str] = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=True)
    for tag, i, j, k, l in matcher.get_opcodes():
        old, new = escape(before[i:j]), escape(after[k:l])
        left.append(old if tag == "equal" else f'<mark class="removed">{old}</mark>')
        right.append(new if tag == "equal" else f'<mark class="added">{new}</mark>')
    return "".join(left), "".join(right)


def line_row(old_number, old, new_number, new, changed=False) -> str:
    if changed and old is not None and new is not None:
        left, right = inline_diff(old, new)
    else:
        left, right = escape(old or ""), escape(new or "")
    old_class = "before" if changed and old is not None else ""
    new_class = "after" if changed and new is not None else ""
    old_label = ("− " if old_class else "") + str(old_number or "")
    new_label = ("+ " if new_class else "") + str(new_number or "")
    return (
        f'<tr><td class="line-number {old_class}">{old_label}</td>'
        f'<td class="code {old_class}">{left}</td>'
        f'<td class="line-number {new_class}">{new_label}</td>'
        f'<td class="code {new_class}">{right}</td></tr>'
    )


def render_table(before: list[str], after: list[str]) -> str:
    rows: list[str] = []
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=True)
    for group in matcher.get_grouped_opcodes(3):
        start, end = group[0], group[-1]
        old_range = f"{start[1] + 1}–{end[2]}" if end[2] > start[1] else "∅"
        new_range = f"{start[3] + 1}–{end[4]}" if end[4] > start[3] else "∅"
        rows.append(
            '<tr class="hunk"><td colspan="4">'
            f'@@ 修改前 {old_range} · 修改后 {new_range} @@'
            '</td></tr>'
        )
        for tag, i, j, k, l in group:
            if tag == "equal":
                for offset, line in enumerate(before[i:j]):
                    rows.append(line_row(i + offset + 1, line, k + offset + 1, line))
            else:
                old_lines = list(enumerate(before[i:j], i + 1))
                new_lines = list(enumerate(after[k:l], k + 1))
                for old, new in zip_longest(old_lines, new_lines):
                    old_number, old_text = old if old is not None else (None, None)
                    new_number, new_text = new if new is not None else (None, None)
                    rows.append(line_row(old_number, old_text, new_number, new_text, True))
    if not rows:
        return '<p class="notice">可见文本行相同；字节差异来自换行符、末尾换行或编码标记，请核对两边原文件。</p>'
    return (
        '<div class="table-scroll"><table class="diff-table">'
        '<colgroup><col class="number-col"><col><col class="number-col"><col></colgroup>'
        '<thead><tr><th colspan="2">修改前 · baseline</th>'
        '<th colspan="2">修改后 · updated</th></tr></thead><tbody>'
        + "".join(rows) + '</tbody></table></div>'
    )


def describe(content: bytes | None) -> str:
    if content is None:
        return "无文件"
    digest = hashlib.sha256(content).hexdigest()[:12]
    ending = "有末尾换行" if content.endswith((b"\n", b"\r")) else "无末尾换行"
    return f"{len(content)} 字节 · SHA-256 {digest} · {ending}"


def read_reasons(delivery: Path) -> dict[str, list[str]]:
    """只解析清单的逐文件章节；原文是不可信数据，正文渲染时转义。"""
    manifest = delivery / "replacement-manifest.md"
    if manifest.is_symlink() or not manifest.is_file():
        raise DiffError("需要普通文件 replacement-manifest.md，包含逐文件修改说明")
    content = read_bytes(manifest)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiffError("replacement-manifest.md 必须使用 UTF-8") from exc
    reasons: dict[str, list[str]] = {}
    active = False
    current = None
    fence = None
    for line in text.splitlines():
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            if current is not None:
                reasons[current].append(line)
            continue
        if fence is not None:
            if current is not None:
                reasons[current].append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            if level <= 2:
                active = level == 2 and title == "逐文件修改说明"
                current = None
                continue
            if level == 3 and active:
                path = title.removeprefix("`").removesuffix("`")
                relative = PurePosixPath(path)
                if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != path or path in {"", "."}:
                    raise DiffError(f"逐文件说明标题必须是两边目录内的相对文件路径：{path}")
                if path in reasons:
                    raise DiffError(f"逐文件修改说明重复：{path}")
                reasons[path] = []
                current = path
                continue
        if active and current is not None:
            reasons[current].append(line)
    return reasons


def note_fields(lines: list[str]) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    label = "说明"
    content: list[str] = []
    for line in lines:
        field = re.match(r"^- ([^：:]+)[：:]\s*(.*)$", line)
        if field:
            if content:
                fields.append((label, "\n".join(content).strip()))
            label, first = field.groups()
            content = [first]
        else:
            content.append(line)
    if content:
        fields.append((label, "\n".join(content).strip()))
    return [(label, text) for label, text in fields if text]


def render_reasons(path: str, status: str, reasons: dict[str, list[str]]) -> str:
    fields = note_fields(reasons.get(path, []))
    if status != "unchanged" and not any(label == "为什么改" and text for label, text in fields):
        raise DiffError(f"缺少逐文件修改原因：{path}；在清单对应 ### 文件路径下填写 - 为什么改：...")
    if not fields:
        return '<p class="muted">此文件未改动，无需修改说明。</p>'
    return '<dl class="reason-fields">' + "".join(
        f'<div class="reason-field"><dt>{escape(label)}</dt><dd>{escape(text)}</dd></div>'
        for label, text in fields
    ) + '</dl>'


STYLE = """
/* 一文件一页：分页导航、左右差异、同文件修改说明。 */
:root {
  --bg: #f3f6f9; --surface: #ffffff; --fg: #182c3a; --muted: #526574;
  --border: #d6e0e7; --accent: #176b96;
  --add-bg: #e2f4eb; --add-mark: #b8e6ce; --add-fg: #155438;
  --remove-bg: #fff0ed; --remove-mark: #f7cfc6; --remove-fg: #8a342b;
  --body-font: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  --code-font: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #111d26; --surface: #182832; --fg: #e3edf3; --muted: #a2b5c2;
  --border: #354956; --accent: #82c9ee;
  --add-bg: #183a2c; --add-mark: #24573e; --add-fg: #b8e6ce;
  --remove-bg: #432b29; --remove-mark: #683d36; --remove-fg: #f8cdc5;
  color-scheme: dark;
} }
:root[data-theme="dark"] {
  --bg: #111d26; --surface: #182832; --fg: #e3edf3; --muted: #a2b5c2;
  --border: #354956; --accent: #82c9ee;
  --add-bg: #183a2c; --add-mark: #24573e; --add-fg: #b8e6ce;
  --remove-bg: #432b29; --remove-mark: #683d36; --remove-fg: #f8cdc5;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--fg); font: 15px/1.6 var(--body-font);
  padding-block: 28px; padding-left: max(16px, env(safe-area-inset-left, 0px));
  padding-right: max(16px, env(safe-area-inset-right, 0px)); }
main { max-width: 1480px; margin: auto; display: grid; gap: 20px; min-width: 0; }
h1 { font-size: 28px; line-height: 1.25; letter-spacing: -.02em; margin: 0; text-wrap: balance; }
p, h2, h3 { margin: 0; } a { color: var(--accent); } code { font-family: var(--code-font); }
header { display: grid; gap: 10px; } .muted, .file-meta { color: var(--muted); }
.notice { border-left: 3px solid var(--accent); padding: 8px 12px; background: var(--surface); }
.pager { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding-block: 14px; border-block: 1px solid var(--border); }
select, button { padding: 9px 12px; background: var(--surface); color: var(--fg);
  border: 1px solid var(--border); border-radius: 4px; font: inherit; }
select { min-width: 0; flex: 1; max-width: 100%; width: 100px; }
button { cursor: pointer; white-space: nowrap; } button:disabled { cursor: default; opacity: .45; }
.page-counter { font-variant-numeric: tabular-nums; min-width: 5em; text-align: center; color: var(--muted); }
a:focus-visible, select:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.files, .file-page { display: grid; gap: 20px; min-width: 0; }
.file { min-width: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 5px; overflow: hidden; }
.file-heading { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 14px 16px; }
.file-name { font: 600 16px var(--code-font); overflow-wrap: anywhere; }
.status { color: var(--muted); font-size: 12px; }
.file-meta { display: flex; flex-wrap: wrap; gap: 6px 24px; font: 12px/1.7 var(--code-font); padding: 0 16px 12px; }
.file .notice { margin: 0 16px 12px; }
.reasons { display: grid; gap: 14px; min-width: 0; }
.reasons h3 { font-size: 18px; }
.reason-fields { margin: 0; padding: 18px 20px; display: grid; gap: 16px; background: var(--surface); border: 1px solid var(--border); border-radius: 5px; }
.reason-field { display: grid; grid-template-columns: 140px minmax(0, 1fr); gap: 12px; }
.reason-field dt { color: var(--muted); font-weight: 600; }
.reason-field dd { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.bottom-pager { display: flex; gap: 10px; justify-content: flex-end; }
@media (max-width: 600px) { .reason-field { grid-template-columns: 1fr; gap: 4px; }
  .pager select { flex-basis: 100%; order: -1; width: 100%; } }
.table-scroll { overflow-x: auto; max-width: 100%; }
.diff-table { width: 100%; table-layout: fixed; border-collapse: collapse; font: 12px/1.6 var(--code-font); }
.number-col { width: 58px; }
th { text-align: left; background: var(--bg); padding: 7px 12px; font-family: var(--body-font); }
td { vertical-align: top; border-right: 1px solid var(--border); }
.line-number { text-align: right; padding: 0 8px; color: var(--muted); font-variant-numeric: tabular-nums; user-select: none; }
.code { padding: 0 8px; white-space: pre-wrap; overflow-wrap: anywhere; tab-size: 4; }
.hunk td { background: var(--bg); color: var(--muted); padding: 7px 12px; }
.before { background: var(--remove-bg); } .after { background: var(--add-bg); }
mark { padding: 0; } .removed { color: var(--remove-fg); background: var(--remove-mark); }
.added { color: var(--add-fg); background: var(--add-mark); }
footer { color: var(--muted); font-size: 13px; }
[hidden] { display: none !important; }
@media (max-width: 600px) { .diff-table { min-width: 680px; } h1 { font-size: 24px; } }
"""

SCRIPT = """
const pages = [...document.querySelectorAll(".file-page")];
const picker = document.getElementById("file-picker");
const counter = document.getElementById("page-counter");
const previous = [...document.querySelectorAll("[data-step='-1']")];
const next = [...document.querySelectorAll("[data-step='1']")];
let current = 0;
function showPage(index, updateHash = false) {
  if (!pages.length) return;
  current = Math.max(0, Math.min(index, pages.length - 1));
  pages.forEach((page, number) => { page.hidden = number !== current; });
  picker.value = String(current);
  counter.textContent = `${current + 1} / ${pages.length}`;
  previous.forEach(button => { button.disabled = current === 0; });
  next.forEach(button => { button.disabled = current === pages.length - 1; });
  if (updateHash) {
    const hash = '#' + pages[current].id;
    if (location.hash !== hash) location.hash = hash;
  }
}
function showHash() {
  const index = pages.findIndex(page => '#' + page.id === location.hash);
  showPage(index < 0 ? 0 : index);
}
function navigate(step) {
  if (!pages.length) return;
  showPage(current + step, true);
  document.getElementById("pager").scrollIntoView({block: "start"});
}
previous.forEach(button => button.addEventListener("click", () => navigate(-1)));
next.forEach(button => button.addEventListener("click", () => navigate(1)));
if (picker) picker.addEventListener("change", () => showPage(Number(picker.value), true));
window.addEventListener("hashchange", showHash);
document.addEventListener("keydown", event => {
  if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey
      || event.target.closest("input, select, textarea, button, a, [contenteditable]")) return;
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    navigate(event.key === "ArrowLeft" ? -1 : 1);
  }
});
showHash();
"""


def render(delivery: Path) -> tuple[str, dict[str, int]]:
    before_files = scan(delivery / "baseline")
    after_files = scan(delivery / "updated")
    reasons = read_reasons(delivery)
    paths = sorted(before_files.keys() | after_files.keys())
    if len(paths) > MAX_FILES:
        raise DiffError(f"比较文件超过 {MAX_FILES} 个，请缩小本轮范围")
    counts = {"modified": 0, "no-baseline": 0, "baseline-only": 0, "unchanged": 0}
    labels = {"modified": "已修改", "no-baseline": "无基线", "baseline-only": "仅基线存在", "unchanged": "未改动"}
    sections: list[str] = []
    options: list[str] = []
    total_bytes = 0
    for number, path in enumerate(paths, 1):
        before = read_bytes(before_files.get(path))
        after = read_bytes(after_files.get(path))
        total_bytes += len(before or b"") + len(after or b"")
        if total_bytes > MAX_TOTAL_BYTES:
            raise DiffError("比较输入超过 8 MiB，请缩小本轮范围")
        status = "no-baseline" if before is None else "baseline-only" if after is None else "unchanged" if before == after else "modified"
        counts[status] += 1
        left, right = text_lines(before), text_lines(after)
        safe_path = escape(path)
        attrs = f'data-path="{safe_path}" data-status="{status}"'
        anchor = f"file-{number}"
        options.append(f'<option value="{number - 1}">{number}. {safe_path} · {labels[status]}</option>')
        if status == "unchanged":
            content = '<p class="notice">两边内容完全相同，无需作为改动文件交付。</p>'
        elif left is None or right is None:
            content = '<p class="notice">二进制或非 UTF-8 文件；只展示大小与哈希差异，不渲染正文。</p>'
        else:
            content = render_table(left, right)
        note = ""
        if status == "no-baseline":
            note = '<p class="notice">未提供旧正文；是否新增或基线读取缺失，请以清单的逐文件说明为准。</p>'
        elif status == "baseline-only":
            note = '<p class="notice">未提供修改后文件；是否删除，请以清单明确的应用动作为准。</p>'
        hidden = " hidden" if number > 1 else ""
        explanation = render_reasons(path, status, reasons)
        sections.append(
            f'<article class="file-page" id="{anchor}" {attrs}{hidden}>'
            '<section class="file">'
            f'<div class="file-heading"><h2 class="file-name">{safe_path}</h2><span class="status">{labels[status]}</span></div>'
            f'<div class="file-meta"><span>修改前：{describe(before)}</span><span>修改后：{describe(after)}</span></div>'
            + note + content + '</section>'
            f'<section class="reasons" aria-labelledby="reason-{number}">'
            f'<h3 id="reason-{number}">本文件修改说明</h3>' + explanation + '</section></article>'
        )
    summary = " · ".join(f"{labels[key]} {value}" for key, value in counts.items())
    empty = '<p class="notice">没有可比较的文件；可能本轮仅修改了 Tool。Tool 定义不参与此页面比较。</p>' if not paths else ""
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    disabled_next = " disabled" if len(paths) < 2 else ""
    navigation = (
        '<nav class="pager" id="pager" aria-label="文件翻页">'
        '<button type="button" id="previous-file" data-step="-1" disabled>← 上一个文件</button>'
        f'<span class="page-counter" id="page-counter" aria-live="polite">1 / {len(paths)}</span>'
        f'<button type="button" id="next-file" data-step="1"{disabled_next}>下一个文件 →</button>'
        '<label for="file-picker">选择文件</label><select id="file-picker">' + "".join(options) + '</select></nav>'
    ) if paths else ""
    bottom_navigation = (
        '<nav class="bottom-pager" aria-label="页底翻页">'
        '<button type="button" id="previous-file-bottom" data-step="-1" disabled>← 上一个文件</button>'
        f'<button type="button" id="next-file-bottom" data-step="1"{disabled_next}>下一个文件 →</button></nav>'
    ) if paths else ""
    page = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<meta name="claude:viewport-fit-sides" content="cover">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'">'
        '<title>MIA 设计差异</title><style>' + STYLE + '</style></head><body><main>'
        '<header><h1>MIA 设计差异</h1>'
        '<p class="muted">每页一个文件：左侧修改前，右侧修改后；下方是该文件的修改说明。− / + 标记增删，支持左右方向键翻页。</p>'
        f'<p>{summary}</p>'
        '<p class="notice">稀疏交付仅包含改动文件。Tool 定义已排除，Tool SP 仍参与比较。'
        '修改说明读取自 replacement-manifest.md，随本页一起离线保存。</p></header>'
        + navigation + '<section class="files">' + empty + "".join(sections) + '</section>' + bottom_navigation
        + f'<footer>生成时间：{timestamp}。页面为离线快照，源文件或清单修改后须重新生成；未执行 MIA 写入或模型运行。</footer>'
        '</main><script>' + SCRIPT + '</script></body></html>'
    )
    if len(page.encode("utf-8")) > MAX_PAGE_BYTES:
        raise DiffError("差异页面超过 16 MiB，请缩小本轮范围；未写入截断页面")
    return page, counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="比较本轮 baseline/ 与 updated/，生成离线 diff.html（排除 Tool）")
    parser.add_argument("delivery", type=Path, help="本轮 .design/ 交付目录")
    parser.add_argument("--overwrite", action="store_true", help="确认更新已有 diff.html；先检查目标页面")
    args = parser.parse_args(argv)
    try:
        delivery = args.delivery.expanduser()
        if delivery.is_symlink() or not delivery.is_dir():
            raise DiffError("交付目录不存在、不是目录或为符号链接")
        delivery = delivery.resolve()
        output = delivery / "diff.html"
        if output.is_symlink() or (output.exists() and not output.is_file()):
            raise DiffError("diff.html 不是普通文件，拒绝写入")
        if output.exists() and not args.overwrite:
            raise DiffError("diff.html 已存在；先检查目标页面，确认更新后使用 --overwrite")
        page, counts = render(delivery)
        with output.open("w" if args.overwrite else "x", encoding="utf-8") as stream:
            stream.write(page)
        print(f"已生成：{output}")
        print(f"比较 {sum(counts.values())} 个文件；Tool 定义未参与；未修改源文件")
    except (DiffError, OSError) as exc:
        print(f"生成失败：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
