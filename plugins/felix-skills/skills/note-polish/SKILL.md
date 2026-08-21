---
name: note-polish
description: Polish non-standard notes in the basic-memory vault into the standard format
argument-hint: "[路径...]"
disable-model-invocation: true
---

# Polish Note Skill — 规整 vault 笔记

把 `/Users/congfei/basic-memory/` 中手写的不规范笔记**打磨**为标准格式：修正 frontmatter、统一标题层级、补齐 `## 相关笔记`、按标题重命名文件，最后刷新图谱。

## Usage

```
/note-polish [路径...]
```

| 命令 | 范围 |
|------|------|
| `/note-polish` | vault 中所有未提交的 `.md`（`git status --porcelain` 非删除项） |
| `/note-polish learning/HNSW.md` | 单文件 |
| `/note-polish learning/` | 目录递归 |
| `/note-polish a.md project/aiinfra/` | 混合，任意个路径 |

- 相对路径以 vault 根 `/Users/congfei/basic-memory/` 为基准
- 目录参数递归展开其下所有 `.md`

## 规范来源

标准笔记形状、frontmatter 四字段、标签生成、文件名规则、标题层级、目录（多问题块）、相关笔记搜索 —— 全部见 [`/Users/congfei/.claude/skills/note/vault-conventions.md`](../../skills/note/vault-conventions.md)。改规则只改那一处，本 skill 与 `note` 同步生效。本 skill 只描述「规整」这一条流程上的特殊判定。

## Behavior

1. **确定范围** —
   - 无参数：`cd /Users/congfei/basic-memory && git status --porcelain` 取所有状态非 `D` 的 `.md`
   - 有参数：文件直接纳入，目录用 `Bash find ... -name '*.md'` 递归展开
   - 去重得待规整列表。为空 → 输出"未发现需要规整的笔记"并结束

2. **逐个规整** — 对每个文件串行跑以下 9 项检查，**只改需要改的**，符合规范的项保持原样：

   1. **Frontmatter 字段裁剪与补齐** — `Read` 解析 frontmatter：
      - 只留 `title` / `description` / `type` / `permalink` / `tags`，删其余字段
      - `permalink` 缺失 → 补 `main/{相对目录}/{title}`（相对目录 = 文件相对 vault 根的所在目录）；已有 → 保留
      - `type` 缺失或非 `note` → 设 `note`

   2. **`title` 值** —
      - `未命名` / 空 / 缺失：优先取正文首个 `# xxx` 作 title；正文无 h1 则取文件名（去 `.md`）
      - 已有正常值 → 保留

   3. **`description` 值** —
      - 缺失 / 空 / 无信息量（如「关于 X 的分析」）：按 conventions 的「description 生成」重新写（写结论/核心问题，≤80 字）
      - 已有正常值 → 保留（不覆盖用户手写）
      - 字段位置紧跟 `title` 之后

   4. **`tags` 值** —
      - `null` / 空列表 / 缺失：按 conventions 的标签规则生成 3-7 个
      - 已有非空 → 保留（不覆盖用户手写）
      - 输出 YAML 列表

   5. **顶层一级标题** — 保证正文首行为 `# {title}`（与 frontmatter 一致）：
      - 已是 → 不动；与 title 不一致 → 改为 title；无 h1 → 正文开头插入

   6. **标题层级递降** — 正文中除顶层 h1 外的其他 `#` 一级标题全降为 `##`，下层相应递降（全文只有一个 h1，相对层级不乱）。**代码块内 `#` 不算标题**（识别 ``` 包裹段），跳过。

   7. **目录（多问题块）** — 按 conventions 的多块判定与锚点规则：
      - 多块结构（顶层 h1 后、`## 相关笔记` 前有独立 `---`，每块以 `## {块标题}` 开头）→ 在顶层 `# {title}` 下、首块前维护 `## 目录`
      - 已有目录但条目不全/过时 → 按现有块重生成
      - 无目录 → 补建
      - 单块笔记不要目录，误生成的删除

   8. **相关笔记区域** — 正文末尾无 `## 相关笔记` 时：
      - 追加 `## 相关笔记` 到文件末
      - 搜索算法见 conventions 的「相关笔记搜索」（扫 `learning/` 与 `project/*/`、取 `title` 造 `[[标题]]`、最多 5 篇）
      - 有匹配 → `- [[标题]] — 关联说明`；无匹配 → 空节不加占位行
      - 已有 `## 相关笔记` → 保持不动（尊重用户）

   9. **文件名与标题对齐** — 文件名（去 `.md`）与最终 `title` 不一致时，按 conventions 的文件名规则 `Bash mv` 重命名（保留 git 历史）；目标同名已存在 → 跳过，记录冲突

   全部改动一次 `Write`（或按需 `Edit`）写回；重命名单独 `Bash mv`。

3. **刷新图谱** — 全部规整后跑一次（让重命名/新关联生效）：

   ```bash
   bash /Users/congfei/.claude/skills/note/scripts/update-wiki.sh
   ```

   详见 conventions 的「图谱刷新」一节。

4. **汇总报告** —

   ```
   ✓ 已规整 {N} 个文件 · 图谱已刷新

   {相对 vault 路径}
     · {改动项 1}
     · {改动项 2}

   {下一个文件路径}
     · 已符合规范，跳过
   ```

   - 只列**实际做过**的改动项，未做的不列
   - 无改动文件写"已符合规范，跳过"
   - 重命名单独行 `旧名.md → 新名.md`

## 改动项文案参考

| 场景 | 报告文案 |
|------|---------|
| Frontmatter 删了字段 | `删除 frontmatter 字段: {字段名}` |
| permalink 补齐 | `补 permalink: main/learning/HNSW` |
| title 修正 | `title: 未命名 → 线上Agent集成CLI方案` |
| description 生成 | `生成 description: 2PC 是否只适用数据库事务` |
| tags 生成 | `生成 tags: agent, cli, 集成` |
| 补 h1 | `补充顶层标题 # {title}` |
| 层级递降 | `降级 {N} 个 h1 → h2` |
| 补建目录 | `补建目录（{N} 个问题块）` |
| 更新目录 | `更新目录条目（{N} → {M}）` |
| 追加相关笔记 | `追加 ## 相关笔记（{N} 篇）` |
| 相关笔记留空 | `追加 ## 相关笔记（空节）` |
| 重命名 | `重命名: 未命名.md → 线上Agent集成CLI方案.md` |
| 冲突未改名 | `文件名冲突未重命名: 目标 {name}.md 已存在` |

## 边界与保守原则

- **只改不规范处**：引用块、代码块、列表、图片、wikilinks、合理段落一律原样保留
- **不合并、不重写、不删段落**：这是「规整」不是「重写」
- **`## 相关笔记` 已存在则不动**，即使为空也尊重用户
- **目录只增删目录区**，不动各块正文；单块笔记不强制目录
- **重命名冲突**：目标已存在时跳过，不覆盖
