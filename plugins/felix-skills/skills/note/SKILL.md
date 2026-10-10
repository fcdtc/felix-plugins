---
name: note
description: Summarize conversation into a knowledge note in basic-memory vault
argument-hint: "[--scope reply|session] [--dir 目录名]"
disable-model-invocation: true
---

# Note Skill — 沉淀为知识笔记

把当前 agent 会话的回复或多轮对话**沉淀**为一篇 vault 知识笔记，写完即连入知识图谱。

执行 bundled scripts 前，先根据当前已加载的 `SKILL.md` 所在目录确定 `NOTE_SKILL_DIR`，并解析为绝对路径。`NOTE_SKILL_DIR` 是下文的说明性占位符，不是假定存在的环境变量。脚本不存在时停止并提示重新安装 `note` skill，不猜测 Claude Code、Codex 或其他 agent 的用户目录。

## Usage

```
/note [--scope reply|session] [--dir 目录名]
```

| 命令 | 落点 |
|------|------|
| `/note` | `learning/` |
| `/note --dir aiinfra` | `project/aiinfra/` |
| `/note --scope reply` | 总结最近一条助手回复（默认） |
| `/note --scope session` | 总结当前 session 的多轮对话为一篇笔记 |
| `/note --scope session --dir aiinfra` | 总结当前 session 并写入 `project/aiinfra/` |

格式规范（frontmatter、标签、文件名、相关笔记）见 [`vault-conventions.md`](vault-conventions.md) —— 改规则只改那一处。

## Behavior

1. **解析参数** — `--scope` 只接受 `reply` 或 `session`，省略时为 `reply`；`--dir` 决定目标目录，不存在则 `Bash mkdir -p` 创建。参数缺值或 scope 无效时说明正确用法并停止。
2. **选定范围** — `reply` 只沉淀最近一次助手输出；`session` 汇总当前对话中与主题相关的多轮用户和助手内容，作为一篇完整笔记。用户明确给出其他范围（如「总结前面关于 X 的讨论」「从第 N 条起」）时按明确范围处理。只纳入对话中实际出现的内容，不推断未讨论的过程或结论。
3. **提炼标题** — 从选定范围提取最核心主题。中文优先（匹配 vault 风格），英文专有名词保留原文（`HNSW`、`PostgreSQL`）。
4. **生成 description** — 按 conventions 的「description 生成」写一句内容摘要（≤80 字，写结论/核心问题，给 LLM 全库目录定位用）。
5. **生成标签** — 按 `vault-conventions` 的标签规则生成 3-7 个。
6. **定路径** — 文件名 = `{title}.md`（规则见 conventions）；目标绝对路径 `{vault}/{目标目录}/{文件名}`。
7. **分析先行** — 写入前先产出一份简短结构化分析（只在后续步骤使用，不落盘）：

   ```
   - 涉及概念：{选定范围最核心的 3-5 个概念，中英不限}
   - 可能相关：{凭概念推测的相关笔记方向，如「库里讲 2PC 的那篇」}
   - 矛盾嫌疑：{与已有笔记可能冲突的结论，无则写「无」}
   ```

   分析驱动后续：概念 → 步骤 8 的 `--keywords`；矛盾嫌疑 → 步骤 11 的矛盾标注。正文忠实综合选定范围；`reply` 模式保留最近输出的原意，`session` 模式去除重复往返并整合多轮形成的结论、依据和必要分歧。分析本身不落盘。
8. **搜相关笔记（多信号评分）** — 跑 `vault-conventions` 的评分脚本：

   ```bash
   bash "<NOTE_SKILL_DIR>/scripts/find-related.sh" \
     --tags "{tag1,tag2}" --keywords "{步骤 7 的概念}"
   ```

   取评分榜 top（默认 5）造 `[[标题]]`；明显不相关的低分候选剔除。无匹配则 `## 相关笔记` 留空节。
9. **碰撞检测** — `Read`/`Bash ls` 查目标路径：
   - 已存在 → 使用当前环境的交互询问能力问：追加 / 覆盖 / 换标题 / 取消；若没有专用询问工具，则在回复中列出选项并等待用户选择
   - 不存在 → 步骤 10A
   - 选「追加」→ 步骤 10B

10A. **写入（新建）** — `Write` 写入。形状：

   ```markdown
   ---
   title: {标题}
   description: {一句话摘要}
   type: note
   tags:
     - {tag1}
     - {tag2}
   ---

   # {标题}

   {自由结构的知识总结}

   ## 相关笔记

   - [[已有笔记标题]] — 关联说明
   ```

10B. **追加到已有笔记** — 目标已是（或将变成）多块结构：

   1. `Read` 已有文件，定位末尾 `## 相关笔记`。
   2. 为本次输出提炼**问题块标题**（`## {问题标题}`，如「HNSW 的检索流程」）。
   3. 在 `## 相关笔记` **之前**插入新块，块间用独立一行 `---` 分隔：

      ```markdown
      ---

      ## {问题标题}

      {本次输出正文}
      ```

   4. **更新目录** — 按 `vault-conventions` 的多块判定与锚点规则维护 `## 目录`：首次追加时补建目录并把原内容纳入，已有目录则把新块标题追加末尾。
   5. **追加相关笔记** — 按步骤 8 再搜一次（追加场景传 `--note {相对路径}` 启用共同邻居信号），新发现的相关笔记追加进 `## 相关笔记`（与已有去重）。
   6. 一次 `Write` 写回完整文件。

11. **矛盾标注（条件触发）** — 仅当步骤 7 标记了「矛盾嫌疑」且嫌疑对象在库里：使用当前环境的交互询问能力呈报冲突双方与拟写文案；用户确认后，在**两篇**笔记的 `## 相关笔记` 各追加一行（格式见 conventions「矛盾标注」）：

    ```markdown
    - [[对方标题]] — ⚠️ 结论冲突：{本文认为 X，该文认为 Y，以何者为准待定}
    ```

    用户拒绝则只在确认信息里口头提示，不动笔记。

12. **刷新图谱** — 笔记落盘后立刻跑（新笔记才不会是孤岛）：

   ```bash
   bash "<NOTE_SKILL_DIR>/scripts/update-wiki.sh"
   ```

   详见 `vault-conventions` 的「图谱刷新」一节。

13. **确认** —

    ```
    ✓ 已沉淀：{title}
      {绝对路径}
      关联 {N} 篇笔记 · 图谱已刷新
    ```

    追加场景：

    ```
    ✓ 已追加：{问题标题} → {笔记总标题}
      {绝对路径}
      新增关联 {N} 篇笔记 · 图谱已刷新
    ```
