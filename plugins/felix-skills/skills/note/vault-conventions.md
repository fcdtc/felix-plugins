# Vault Conventions — 笔记规范（单一事实源）

本文件是 `note`、`note-polish`、`note-query`、`note-lint` 共享的规范定义。`note`/`note-polish` 写笔记时遵循，`note-query` 只读时据此识别笔记结构，`note-lint` 据此做体检。任何格式规则的改动只改这里一处，四个 skill 同步生效。

## Vault 位置

根目录：`/Users/congfei/basic-memory/`

目录结构：

| 目录 | 用途 |
|------|------|
| `learning/` | 默认落点，通用知识笔记 |
| `project/{xxx}/` | `--dir xxx` 指定的项目专属笔记 |
| `templates/` | 模板，不参与笔记生产 |
| `Excalidraw/` | 画板附件 |
| `scripts/` | 索引/反向链接维护脚本 |
| `INDEX.md` | LLM 导航层（脚本自动生成，勿手改） |

## 笔记标准形状

```markdown
---
title: {标题}
description: {一句话内容摘要}
type: note
permalink: main/{目录}/{标题}
tags:
  - {tag1}
  - {tag2}
---

# {标题}

## 目录

- [问题块标题](#问题块标题)

---

## {问题块标题}

{正文，自由结构}

## 相关笔记

- [[关联笔记标题]] — 关联说明
```

## Frontmatter 规则

固定五字段，多删少补：

- `title` — 与正文顶层 `#` 一致；为空/`未命名`/缺失时，取正文首个 h1，再退回文件名（去 `.md`）
- `description` — **一句话内容摘要**（≤80 字符），LLM 全库目录据此定位该笔记，避免盲目 `Read`。规则见下方「description 生成」
- `type` — 恒为 `note`
- `permalink` — `main/{相对目录}/{title}`；相对目录 = 文件相对 vault 根的所在目录（如 `learning`、`project/aiinfra`）
- `tags` — YAML 列表；为空/缺失时按标签规则生成，已有非空值则保留

> 注：`note` skill 产出可不带 `permalink`（新笔记由 `build-index.sh` 不依赖该字段）；`note-polish` 规整时补齐。`description` 两者都要写。两者共用字段定义。

## description 生成

一句话讲清「这篇笔记解决什么问题 / 给出什么结论」，让 LLM 在 `INDEX.md` 全库目录里一眼判断要不要深读。

- ≤80 字符（中文字符数，非字节）；超出 `build-index.sh` 会自动截断加 `…`
- 写**结论或核心问题**，不写「本文介绍了…」这类元描述
- 举例：
  - 好：`2PC 提交协议只适用于数据库事务吗？分布式系统中其他资源能用 2PC 吗？`
  - 好：`TCC 本质上就是应用层（业务层）的 2PC`
  - 差：`关于 2PC 协议的分析`（无信息量）
- `build-index.sh` 的兜底：若 `description` 缺失，自动取正文首句（跳过标题/分隔线/图片/链接/嵌入引用）。但**手写 description 质量更高**，应主动写

> 注：`note` skill 产出可不带 `permalink`（新笔记由 `build-index.sh` 不依赖该字段）；`note-polish` 规整时补齐。两者共用字段定义。

## 标签生成规则

提取 3-7 个标签，至少一个：

- 优先小写英文连字符式（`distributed-systems`、`vector-search`、`cli`）
- 无英文等价词的领域概念用中文（`分布式事务`、`积分防刷`）
- 输出为 YAML 列表

## 文件名规则

- 文件名 = `{title}.md`，与标题完全一致（保留空格、中文、大小写）
- 标题含文件系统非法字符 `/ \ : * ? " < > |` 时，替换为空格
- 文件名与最终 `title` 不一致 → `mv` 重命名（保留 git 历史）；目标已存在同名 → 跳过，记录冲突

## 标题层级

- 全文只有一个 h1（正文首行 `# {title}`）
- 其他 `#` 一级标题递降为 `##`，下层相应递降
- **代码块内**（``` 包裹段）的 `#` 不算标题

## 目录（多问题块）

仅当笔记为「以 `---` 分隔的多问题块」结构时维护目录；单块笔记不要目录。

- 多块判定：顶层 h1 之后、`## 相关笔记` 之前出现独立一行 `---`，且每块以 `## {块标题}` 开头
- 目录位置：顶层 `# {title}` 之下、首块之前，标题 `## 目录`
- 目录项：`- [{块标题}](#{锚点})`，只收实际知识块，不含 `## 目录`/`## 相关笔记`
- 锚点规则：块标题转小写 → 空格转连字符 → 去标点（`## HNSW 的检索流程` → `#hnsw-的检索流程`）
- 已有目录条目不全/过时 → 按现有块重新生成

## 相关笔记搜索（多信号评分）

把新笔记连入知识图谱的关联发现。单条 `grep` 只能抓字面命中（「向量检索」搜不到只写「HNSW」的笔记），因此用**三信号加权评分**（借鉴 llm_wiki 的加权融合 + Adamic-Adar 思想）：

| 信号 | 权重 | 数据来源 |
|------|------|---------|
| 标签重叠 | +3 / 个 | 双方 frontmatter `tags` 交集 |
| 标题/摘要关键词 | +2 / 个 | 关键词命中对方 `title`/`description`/文件名 |
| 共同邻居 | +1.5 / 个，封顶 3 个 | 双方图谱邻居（出链 ∪ 入链）交集 |

评分由脚本完成（LLM 只负责喂参数、剔误报、写关联说明）：

```bash
bash /Users/congfei/.claude/skills/note/scripts/find-related.sh \
  --tags "tag1,tag2" --keywords "词1,词2" [--note 相对路径] [--top N]
```

- `--tags` 必填（本次笔记的标签）；`--keywords` 来自「分析先行」提炼的核心概念
- `--note` 仅追加/规整已有笔记时传，启用共同邻居信号并排除自身
- 从评分榜取 top（默认 5）；**低分但明显不相关的候选应剔除**，分数是线索不是判决
- 取 frontmatter `title` 构造 `[[标题]]` wikilink；找不到（全零分）则 `## 相关笔记` 留空节，不加占位行
- 追加场景与已有条目去重

## 矛盾标注

两篇笔记结论冲突时（`/note` 的分析先行或 `note-lint` 的语义检查发现），在**双方**的 `## 相关笔记` 各追加一行带 ⚠️ 的链接：

```markdown
- [[对方标题]] — ⚠️ 结论冲突：{本文认为 X，该文认为 Y，以何者为准待定}
```

- 这是轻量版 `contradicted_by`：让查询（note-query 流程 B）读到矛盾时如实呈现，而不是被后读到的那篇悄悄覆盖
- **加不加由人决定**：LLM 只拟文案并询问，`note-lint` 只报告绝不写入
- 冲突解决后由人手动移除该行

## 规模预案（只记录，暂不实施）

当前规模下 INDEX.md + 双链 + grep 评分完全够用。**触发条件**：笔记数超过 ~500 篇或 INDEX.md 超过 ~2000 行时，再考虑接入 qmd（本地 Markdown 混合检索，CLI + MCP 双形态，BM25 + 向量 + 图遍历三路融合）作为 note-query 的路由层——按需路由，不预先建设。

## 图谱刷新（写后必做）

笔记写入/规整/重命名后，必须刷新索引层，否则新笔记是孤岛。脚本位于 **note skill 目录下**（非 vault 库内），默认操作 vault 根 `/Users/congfei/basic-memory`，无需 `cd`：

```bash
bash /Users/congfei/.claude/skills/note/scripts/update-wiki.sh
```

脚本清单（任一 skill 均可调用，首个可选参数为 vault 根目录）：

- `update-wiki.sh [vault]` — 串起下面两步，写后用这个
- `build-index.sh [vault]` — 重建 vault 的 `INDEX.md`（LLM 导航层：全部笔记 + 标签云 + 健康度指标）
- `build-backlinks.sh [笔记] [vault]` — 为笔记注入 `<!--LLM-BACKLINKS-*-END-->` 标记的反向链接区块；不传笔记则全库
- `find-related.sh --tags … [--keywords …] [--note …]` — 多信号关联评分，只读（见上方「相关笔记搜索」）
- `lint.sh [vault]` — 结构层体检（死链/孤儿/frontmatter/重名/索引过期），只读不写（`note-lint` skill 用）

刷新耗时约数秒。该区块由脚本自动维护，**勿手改**。
