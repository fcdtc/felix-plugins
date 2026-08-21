#!/usr/bin/env bash
# 为每篇笔记注入「关联笔记」区块（backlinks），让 LLM 能看见 Obsidian 的反向链接。
# 幂等：重复运行会先删除旧的 ## 关联笔记区块再重写，不会累积垃圾。
# 用法: bash build-backlinks.sh [笔记路径] [vault根目录]
#   第一个参数（可选）：单篇笔记路径（相对 vault 根或绝对路径），不传则处理全库
#   第二个参数（可选）：vault 根目录，默认 /Users/congfei/basic-memory

set -euo pipefail
ROOT="${2:-/Users/congfei/basic-memory}"
cd "$ROOT"

MARKER_BEGIN="<!--LLM-BACKLINKS-BEGIN-->"
MARKER_END="<!--LLM-BACKLINKS-END-->"

process_one() {
  local f="$1"
  local name
  name=$(basename "$f" .md)

  # 删除旧区块（含标记行，以及区块前的尾部空行，保证幂等）
  if grep -q "$MARKER_BEGIN" "$f"; then
    # 单遍 awk 三态：
    #   阶段1（遇到 MARKER_BEGIN 前）：正文缓冲进 buf，暂不输出
    #   阶段2（MARKER_BEGIN..MARKER_END，含标记）：全部丢弃；进入时把 buf 尾部连续空行回退掉再输出 buf
    #   阶段3（MARKER_END 之后）：原样输出（区块虽在末尾，仍稳妥保留）
    # 这样重复运行不再累积空行。
    awk -v b="$MARKER_BEGIN" -v e="$MARKER_END" '
      phase == 1 && $0 ~ b {
        for (i=1; i<=n; i++) if (buf[i] ~ /[^[:space:]]/) last=i
        for (i=1; i<=last; i++) print buf[i]
        n=0; phase=2; next
      }
      phase == 2 { if ($0 ~ e) phase=3; next }
      phase == 3 { print; next }
      phase == 1 { buf[++n]=$0 }
      BEGIN { phase=1 }
    ' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
  fi

  # 收集反向链接：哪些笔记里出现了 [[本笔记名]]。用 grep -F 固定字符串匹配，
  # 因为文件名含空格/括号，正则会出问题。
  local bl
  bl=$(grep -rlF --include="*.md" -- "[[$name]]" . 2>/dev/null | grep -v "^./$f$" | grep -v "^$f$" | sort -u || true)

  # 收集正向链接：本笔记里出现的 [[xxx]]
  local fl
  fl=$(grep -oE '\[\[[^]|]+(\|[^]]*)?\]\]' "$f" 2>/dev/null \
      | sed -E 's/\|.*//; s/\[\[//; s/\]\]//' \
      | grep -v -E '\.(png|jpg|jpeg|gif|svg)$' \
      | sort -u || true)

  # 没有任何关联就不写区块（保持原文件干净）
  if [ -z "$bl" ] && [ -z "$fl" ]; then
    return
  fi

  {
    echo ""
    echo "$MARKER_BEGIN"
    echo "## 关联笔记"
    echo ""
    if [ -n "$fl" ]; then
      echo "**本文引用（→）**:"
      echo ""
      while IFS= read -r target; do
        [ -z "$target" ] && continue
        tt=$(grep -rlF --include="*.md" -- "[[$target]]" . 2>/dev/null | head -1)
        if [ -n "$tt" ]; then
          echo "- [[$target]]"
        else
          echo "- [[$target]] ⚠️ 目标笔记不存在（悬空链接，建议补建或修正）"
        fi
      done <<< "$fl"
      echo ""
    fi
    if [ -n "$bl" ]; then
      echo "**引用本文（←）**:"
      echo ""
      while IFS= read -r src; do
        [ -z "$src" ] && continue
        src=${src#./}
        echo "- [[$(basename "$src" .md)]]"
      done <<< "$bl"
      echo ""
    fi
    echo "> 本区块由 note skill 的 scripts/build-backlinks.sh 自动维护，记录 Obsidian 图谱中本笔记的关联。LLM 回答相关问题时应沿这些链接展开。"
    echo "$MARKER_END"
  } >> "$f"
}

if [ $# -ge 1 ] && [ -n "$1" ]; then
  process_one "$1"
  echo "✅ 处理完成: $1"
else
  count=0
  while IFS= read -r f; do
    f=${f#./}
    process_one "$f"
    count=$((count+1))
  done < <(find . -name "*.md" -not -path '*/.*' -not -name "INDEX.md" -not -name "log.md" -not -path '*/templates/*' -not -path '*/Excalidraw/*')
  echo "✅ 全库处理完成，共 $count 篇笔记注入了关联区块"
fi
