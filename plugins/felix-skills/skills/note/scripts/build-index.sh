#!/usr/bin/env bash
# 为 Obsidian 知识库生成 LLM 可读的索引层（INDEX.md）。
# 用法: bash build-index.sh [vault根目录]
#   不传参时默认 vault 根目录为 /Users/congfei/basic-memory
#
# 设计说明：不使用 `{ ... } > file` 命令组重定向（在某些 shell 下会异常截断），
# 而是用一个全局输出函数 out() 持续追加到一个临时文件，最后原子改名。
# 也不使用 set -e/pipefail：grep 无匹配、head 关闭管道时返回非零是正常的。

ROOT="${1:-/Users/congfei/basic-memory}"
cd "$ROOT"

TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

out() { printf '%s\n' "$@" >> "$TMP"; }

# 提取一篇笔记的一句话描述（给 LLM 全库目录定位用）。
# 优先级：frontmatter 的 description 字段 > 正文第一个非空、非标题、非分隔线的段落首句。
# 用 python3 处理，保证多字节字符（中文）按字符数正确截断，避免字节截断乱码。
extract_description() {
  python3 - "$1" <<'PY'
import sys, re
p = sys.argv[1]
try:
    txt = open(p, encoding="utf-8").read()
except Exception:
    sys.exit(0)

# 1) frontmatter description
m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
if m:
    fm = m.group(1)
    d = re.search(r"^description:\s*(.+?)\s*$", fm, re.M | re.I)
    if d:
        v = d.group(1).strip().strip("\"'")
        if v:
            print(v[:80] + ("…" if len(v) > 80 else ""), end="")
            sys.exit(0)

# 2) 正文首个有效段落首句
body = txt[m.end():] if m else txt
for line in body.splitlines():
    s = line.strip()
    if not s or s.startswith(("#", "---", ">", "|", "![", "- [")):
        continue
    # 跳过纯嵌入引用/链接行（如 ![[x]] 或 [[x]] 或 [x](y)）
    bare = re.sub(r"\s+", "", s)
    if re.fullmatch(r"(\[!\[.*?\]\]|!\[\[.*?\]\]|\[\[.*?\]\]|\[.*?\]\(.*?\)\!?\.?)+", bare):
        continue
    # 首句：取到第一个句末标点
    mm = re.search(r"[。！？!?；;]", s)
    one = s[:mm.end()] if mm else s
    print(one[:80] + ("…" if len(one) > 80 else ""), end="")
    sys.exit(0)
PY
}

# 排除点目录、INDEX.md、log.md、templates、Excalidraw 这些非内容笔记
all_notes=$(find . -name "*.md" \
  -not -path '*/.*' \
  -not -name "INDEX.md" \
  -not -name "log.md" \
  -not -path '*/templates/*' \
  -not -path '*/Excalidraw/*' \
  | sort)

# === 头部 ===
out "---"
out "title: 知识库索引（LLM 导航层）"
out "type: index"
out "permalink: main/index"
out "generated: 自动生成，请勿手动编辑，由 note skill 的 scripts/build-index.sh 更新"
out "---"
out ""
out "# 知识库索引"
out ""
out "> 本文件是 LLM 进入知识库的**入口**。回答问题前先读本文件定位相关笔记，再沿双链展开关联。"
out ""

# === 1. 全库目录 ===
out "## 📂 全库目录"
out ""
for dir in learning project project/*; do
  [ -d "$dir" ] || continue
  files=$(find "$dir" -maxdepth 1 -name "*.md" | sort)
  [ -z "$files" ] && continue
  out "### \`$dir\`"
  out ""
  while IFS= read -r f; do
    base=$(basename "$f" .md)
    desc=$(extract_description "$f")
    if [ -n "$desc" ]; then
      out "- [[$base]] — $desc"
    else
      out "- [[$base]]"
    fi
  done <<< "$files"
  out ""
done

# === 2. 知识枢纽（被引用最多的笔记） ===
out "## 🌐 知识枢纽（被引用最多的笔记）"
out ""
out "这些是全库的核心节点，回答相关问题时几乎都需要参考。"
out ""
out "| 笔记 | 被引用次数 |"
out "|---|---|"
grep -rhoE '\[\[[^]|]+(\|[^]]*)?\]\]' --include="*.md" --exclude="INDEX.md" --exclude="log.md" . 2>/dev/null \
  | sed -E 's/\|.*//; s/\[\[//; s/\]\]//' \
  | sort | uniq -c | sort -rn | head -20 \
  | while read -r count name; do
      out "| [[$name]] | $count |"
    done
out ""

# === 3. 按标签聚类 ===
out "## 🏷️ 按标签聚类"
out ""
out "回答时若问题落在某个标签域内，优先检索该标签下所有笔记。"
out ""
out "| 标签 | 笔记数 |"
out "|---|---|"
grep -rh '^-' --include="*.md" -A0 . 2>/dev/null >/dev/null
# 从 frontmatter 的 tags: 列表项（- tagname）提取
grep -rh '^tags:' --include="*.md" . 2>/dev/null >/dev/null
# 更稳妥：扫描每个文件的 tags 块（列表项允许缩进，conventions 标准形状里是缩进两格）
find . -name "*.md" -not -path '*/.*' -not -name "INDEX.md" -not -name "log.md" \
  -not -path '*/templates/*' -not -path '*/Excalidraw/*' \
  | while IFS= read -r f; do
      # 提取 tags: 后面到下一个非列表项的内容
      LC_ALL=en_US.UTF-8 awk '/^tags:/{flag=1;next} flag&&/^[[:space:]]*-[[:space:]]/{sub(/^[[:space:]]*-[[:space:]]*/,"");print} flag&&/^[^[:space:]-]/{flag=0}' "$f"
    done \
  | sed 's/^ *//; s/ *$//' \
  | grep -v '^$' \
  | sort | uniq -c | sort -rn | head -30 \
  | while read -r count tag; do
      out "| **$tag** | $count |"
    done
out ""

# === 4. 孤立笔记 ===
out "## 🏚️ 孤立笔记（无任何双链，需补充关联）"
out ""
orphans=0
while IFS= read -r f; do
  name=$(basename "$f" .md)
  inbound=$(grep -rlF --include="*.md" -- "[[$name]]" . 2>/dev/null | grep -v -e "$f" -e '\./INDEX.md' -e '\./log.md' | head -1)
  outbound=$(grep -c '\[\[' "$f" 2>/dev/null)
  [ -z "$outbound" ] && outbound=0
  if [ -z "$inbound" ] && [ "$outbound" = "0" ]; then
    out "- [[$name]]"
    orphans=$((orphans+1))
  fi
done <<< "$all_notes"
[ "$orphans" = "0" ] && out "（无孤立笔记，库结构健康 ✅）"
out ""

# === 5. 健康度指标 ===
# 数值化健康趋势：孤立笔记率、双链密度、索引新鲜度（重建前的落后时长）。
note_count=$(echo "$all_notes" | grep -c .)
link_count=$(grep -rho '\[\[' --include='*.md' --exclude='INDEX.md' --exclude='log.md' . 2>/dev/null | wc -l | tr -d ' ')
orphan_pct=$(awk -v o="$orphans" -v n="$note_count" 'BEGIN{printf "%.0f", (n>0 ? o*100/n : 0)}')
link_density=$(awk -v l="$link_count" -v n="$note_count" 'BEGIN{printf "%.1f", (n>0 ? l/n : 0)}')

# 索引新鲜度：本次重建前 INDEX.md 落后最新笔记多久（mv 之后测就恒为 0 了，必须在重建前测）
mtime_of() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
index_freshness="首次生成（无历史索引）"
if [ -f INDEX.md ]; then
  prev_idx=$(mtime_of INDEX.md)
  newest_note=0
  while IFS= read -r f; do
    m=$(mtime_of "$f")
    [ "$m" -gt "$newest_note" ] && newest_note=$m
  done <<< "$all_notes"
  if [ "$newest_note" -gt "$prev_idx" ]; then
    lag_min=$(( (newest_note - prev_idx) / 60 ))
    if [ "$lag_min" -ge 60 ]; then
      index_freshness="重建前落后 $(( lag_min / 60 )) 小时（期间有笔记未入索引）"
    else
      index_freshness="重建前落后 ${lag_min} 分钟"
    fi
  else
    index_freshness="重建前已是最新 ✅"
  fi
fi

out "## 📈 健康度指标"
out ""
out "| 指标 | 数值 | 说明 |"
out "|---|---|---|"
out "| 孤立笔记率 | ${orphan_pct}%（${orphans}/${note_count}） | 越低越好，孤岛应尽快连入图谱 |"
out "| 双链密度 | ${link_density} 链/篇 | 关联丰富度，健康参考值 2+ |"
out "| 索引新鲜度 | $index_freshness | 落后过多说明有笔记写后未刷新 |"
out ""

# === 尾部 ===
out "---"
out ""
out "**使用说明**"
out "- 本索引解决「LLM 找得到入口」的问题。"
out "- 沿双链展开关联的能力，见每篇笔记底部的「关联笔记」区块（由 scripts/build-backlinks.sh 维护）。"
out "- 回答问题的工作流见 \`CLAUDE-MANIFEST.md\`（知识库使用规范）。"

mv "$TMP" INDEX.md

echo "✅ 已生成 INDEX.md"
echo "   笔记总数: $note_count"
echo "   双链总数: $link_count"
echo "   孤立笔记率: ${orphan_pct}% ($orphans/$note_count)"
echo "   双链密度: ${link_density} 链/篇"
echo "   索引新鲜度: $index_freshness"
