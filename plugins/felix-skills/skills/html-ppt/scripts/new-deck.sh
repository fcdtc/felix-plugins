#!/usr/bin/env bash
# html-ppt :: new-deck.sh — scaffold a new deck from templates/deck.html
#
# Usage:
#   new-deck.sh <name> [output-parent-dir]
#
# Creates <parent>/<name>/index.html with paths rewritten to point at the
# skill's shared assets/themes/animations. Defaults to ./examples/.

set -euo pipefail

NAME="${1:-}"
if [[ -z "$NAME" ]]; then
  echo "usage: new-deck.sh <name> [parent-dir]" >&2
  exit 1
fi

PARENT="${2:-examples}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$HERE/templates/deck.html"

if [[ ! -f "$TEMPLATE" ]]; then
  echo "error: template not found at $TEMPLATE" >&2
  exit 1
fi

OUT_DIR="$HERE/$PARENT/$NAME"
if [[ -e "$OUT_DIR" ]]; then
  echo "error: $OUT_DIR already exists" >&2
  exit 1
fi
mkdir -p "$OUT_DIR"

# templates/deck.html references ../assets/...; for examples/<name>/index.html
# that same relative path (../../assets/...) needs one more ../.
# Also inject <script src="notes.js"> right before runtime.js so users can
# edit speaker scripts in a single external file.
sed 's|href="../assets/|href="../../assets/|g; s|src="../assets/|src="../../assets/|g; s|data-theme-base="../assets/|data-theme-base="../../assets/|g; s|<script src="../../assets/runtime.js"></script>|<script src="notes.js"></script>\n<script src="../../assets/runtime.js"></script>|' \
  "$TEMPLATE" > "$OUT_DIR/index.html"

# Generate a notes.js scaffold — one entry per slide, matching the template's
# slide count. Speakers edit this single file to author 逐字稿.
SLIDE_COUNT=$(grep -c '<section class="slide' "$TEMPLATE" || echo 6)
{
  echo "// notes.js — 逐字稿 (speaker script) for this deck."
  echo "// 每个数组元素对应一张 slide 的逐字稿（按顺序）。"
  echo "// 支持 mini-markdown：**加粗**、*斜体*、\`代码\`、空行分段。"
  echo "// 用编辑器直接改这个文件即可，不用改 index.html。"
  echo "window.__PPT_NOTES__ = ["
  for i in $(seq 1 "$SLIDE_COUNT"); do
    echo "  // slide $i"
    echo "  \`第 $i 页的逐字稿写在这里。"
    echo ""
    echo "支持 **加粗**、*斜体*、\\\`代码\\\`。空行会自动分段。\`,"
    echo ""
  done
  echo "];"
} > "$OUT_DIR/notes.js"

echo "✔ created $OUT_DIR/index.html"
echo "✔ created $OUT_DIR/notes.js  (edit this to author 逐字稿)"
echo ""
echo "next steps:"
echo "  open  $OUT_DIR/index.html"
echo "  # 编辑 $OUT_DIR/notes.js 改逐字稿（按 S 打开演讲者视图查看效果）"
echo "  # press T to cycle themes, ← → to navigate, O for overview"
echo ""
echo "  # render to PNG:"
echo "  $HERE/scripts/render.sh $OUT_DIR/index.html all"
