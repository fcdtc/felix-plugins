#!/usr/bin/env bash
# 知识库结构层体检（note-lint skill 的脚本部分）。
# 用法: bash lint.sh [vault根目录]
#   不传参时默认 vault 根目录为 /Users/congfei/basic-memory
#
# 检查项：frontmatter 缺字段 / 重名冲突 / 死链 / 孤立笔记 / 索引过期 /
#         多块笔记缺目录 / 矛盾标注现状
#
# 铁律：只读不写——只打印报告，绝不修改任何笔记。语义层检查（矛盾/过时结论）
# 由 note-lint skill 的 LLM 部分完成，同样只报告。

set -u

VAULT="${1:-/Users/congfei/basic-memory}"
cd "$VAULT" || { echo "❌ vault 目录不存在: $VAULT" >&2; exit 1; }

python3 - "$VAULT" <<'PY'
import os, re, sys
from collections import defaultdict

vault = sys.argv[1]
SKIP_DIRS = {".git", ".obsidian", "templates", "Excalidraw"}
REQUIRED = ["title", "description", "type", "permalink", "tags"]

notes = []       # {rel, stem, title, desc, tags, txt, links, mtime}
by_stem = {}
for dirpath, dirnames, filenames in os.walk(vault):
    dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
    for fn in sorted(filenames):
        if not fn.endswith(".md") or fn in ("INDEX.md", "log.md"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            txt = open(p, encoding="utf-8").read()
        except Exception:
            continue
        rel = os.path.relpath(p, vault)
        stem = fn[:-3]
        title, tags = "", []
        m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
        fm = m.group(1) if m else ""
        t = re.search(r"^title:\s*(.+?)\s*$", fm, re.M)
        if t: title = t.group(1).strip().strip("\"'")
        if not title or title in ("未命名",):
            h1 = re.search(r"^# (.+)$", txt, re.M)
            title = h1.group(1).strip() if h1 else stem
        block = re.search(r"^tags:\n((?:\s*-\s*.+\n?)+)", fm, re.M)
        if block:
            tags = [x.strip() for x in re.findall(r"^\s*-\s*(.+?)\s*$", block.group(1), re.M)]
        links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", txt)
        n = {"rel": rel, "stem": stem, "title": title, "tags": tags,
             "txt": txt, "links": links, "mtime": os.path.getmtime(p), "fm": fm}
        notes.append(n)
        by_stem[stem.lower()] = n

issues = 0

def section(name, lines):
    global issues
    print(f"\n### {name}" if lines else f"\n### {name} — 无 ✅")
    if lines:
        issues += len(lines)
        for l in lines:
            print("  " + l)

# 1. frontmatter 缺字段
lines = []
for n in notes:
    missing = []
    if not n["fm"]:
        missing = REQUIRED
    else:
        for f in REQUIRED:
            v = re.search(rf"^{f}:\s*(.*)$", n["fm"], re.M | re.I)
            if not v or not v.group(1).strip():
                if f == "tags":  # tags 可为 YAML 列表形式（值为空、块在下一行）
                    if not re.search(r"^tags:\s*\n(\s*-)", n["fm"], re.M):
                        missing.append(f)
                else:
                    missing.append(f)
    if missing:
        lines.append(f"{n['rel']} — 缺 {', '.join(missing)}")
section("1. frontmatter 缺字段", lines)

# 2. 重名冲突（不同文件的 title 相同，或 title 撞了别的文件的文件名 → wikilink 歧义）
lines = []
title_map = defaultdict(list)
for n in notes:
    title_map[n["title"].lower()].append(n["rel"])
for t, rels in title_map.items():
    if len(rels) > 1:
        lines.append(f"标题「{t}」被 {len(rels)} 篇使用: {'、'.join(rels)}")
for n in notes:
    other = by_stem.get(n["title"].lower())
    if other and other is not n:
        lines.append(f"{n['rel']} 的 title 与 {other['rel']} 的文件名相同（wikilink 歧义）")
section("2. 重名冲突", lines)

# 3. 死链（[[X]] 的目标在全库不存在）
known = set(by_stem) | {n["title"].lower() for n in notes}
lines = []
for n in notes:
    dead = sorted({l.strip() for l in n["links"] if l.strip().lower() not in known})
    if dead:
        lines.append(f"{n['rel']} — 死链: {'、'.join('[[' + d + ']]' for d in dead)}")
section("3. 死链", lines)

# 4. 孤立笔记（无入链也无出链；脚本维护的关联区块不算内容出链）
lines = []
linked_into = defaultdict(set)
for n in notes:
    for l in n["links"]:
        t = l.strip().lower()
        if t in by_stem:
            linked_into[t].add(n["stem"].lower())
for n in notes:
    k = n["stem"].lower()
    if not n["links"] and not linked_into.get(k):
        lines.append(f"{n['rel']}")
section("4. 孤立笔记", lines)

# 5. 索引新鲜度（INDEX.md 落后于最新笔记）
lines = []
idx = os.path.join(vault, "INDEX.md")
if os.path.exists(idx):
    idx_m = os.path.getmtime(idx)
    newest = max(n["mtime"] for n in notes) if notes else idx_m
    if newest > idx_m:
        h = (newest - idx_m) / 3600
        lag = f"{h:.1f} 小时" if h >= 1 else f"{(newest - idx_m) / 60:.0f} 分钟"
        lines.append(f"INDEX.md 落后最新笔记 {lag} → 跑 update-wiki.sh 重建")
else:
    lines.append("INDEX.md 不存在 → 跑 update-wiki.sh 生成")
section("5. 索引新鲜度", lines)

# 6. 多块笔记缺目录
lines = []
for n in notes:
    body = re.sub(r"```.*?```", "", n["txt"], flags=re.S)  # 代码块内的 --- 不算
    m = re.search(r"^# .+?\n(.*?)^## 相关笔记", body, re.S | re.M)
    mid = m.group(1) if m else ""
    if re.search(r"^---\s*$", mid, re.M) and re.search(r"^## .+", mid, re.M):
        if not re.search(r"^## 目录\s*$", n["txt"], re.M):
            lines.append(f"{n['rel']} — 多块结构但缺 `## 目录`")
section("6. 多块笔记缺目录", lines)

# 8. 矛盾标注现状（ informational，不算问题）
lines = []
for n in notes:
    for ln in n["txt"].splitlines():
        if "⚠️" in ln and "[[" in ln:
            lines.append(f"{n['rel']}: {ln.strip()[:70]}")
print(f"\n### 7. 矛盾标注现状 — {len(lines)} 条（信息项，不计入问题数）")
for l in lines:
    print("  " + l)

print(f"\n{'─' * 40}")
print(f"共 {len(notes)} 篇笔记 · 结构问题 {issues} 项（只报告，修改由人决定）")
PY
