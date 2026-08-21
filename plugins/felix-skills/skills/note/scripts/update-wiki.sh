#!/usr/bin/env bash
# 一键更新知识库的 LLM 索引层：先重建 INDEX.md，再刷新所有笔记的关联区块。
# 用法: bash update-wiki.sh [vault根目录]
#   不传参时默认 vault 根目录为 /Users/congfei/basic-memory

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VAULT="${1:-/Users/congfei/basic-memory}"

echo "▶ 1/2 重建 INDEX.md ..."
bash "$SCRIPT_DIR/build-index.sh" "$VAULT" || { echo "❌ INDEX 生成失败，终止"; exit 1; }

echo ""
echo "▶ 2/2 刷新所有笔记的关联区块 ..."
bash "$SCRIPT_DIR/build-backlinks.sh" "" "$VAULT" || { echo "❌ backlinks 刷新失败"; exit 1; }

echo ""
echo "✅ 知识库索引层更新完成"
