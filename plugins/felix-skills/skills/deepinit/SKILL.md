---
name: deepinit
description: 扫描整个代码库目录树，生成带层级父引用的本地导航文档
argument-hint: "[--target claude|codex|auto]"
disable-model-invocation: true
---

# Deep Init

为当前代码库生成一组不进入版本库的层级导航文档，让后续 agent 快速定位模块、入口和验证方式。

## 目标平台

先解析 `--target`：

- `claude`：导航文件名为 `CLAUDE.local.md`。
- `codex`：导航文件名为 `AGENTS.override.md`。Codex 会在同一目录优先读取该文件，因此生成内容必须显式要求继续遵守同目录已有的 `AGENTS.md`；不得静默遮蔽团队规则。
- `auto` 或未指定：仅在当前 harness 可可靠识别时自动选择；否则询问用户，不猜测。

首次运行时，把选定文件名作为裸名加入项目根 `.gitignore`，以匹配全树。任一模式若发现已有导航文件且无法确认是本 skill 的生成物，必须停止并询问；禁止静默覆盖人工文件。确认属于本 skill 后，按全量重生成处理，不做增量合并。

## 五条规则

1. **父级先行**：按目录深度从根到叶生成，保证 Parent 引用指向已存在文件。
2. **生成物可重建**：仅覆盖已确认由本 skill 生成的导航文件；人工文件必须先获确认。
3. **空目录跳过**：判定见 [template.md](template.md) 的“空目录处理”。
4. **不进版本库**：选定文件名必须出现在根 `.gitignore`。
5. **上下文隔离**：主 agent 只扫描目录树、调度 subagent、校验；读代码、提炼职责和写导航文件由 subagent 完成。同层并行、不同层串行。若当前平台没有委派能力，则按目录顺序处理，但仍限制每次只读取一个目录。

## 执行流

### Step 1：确定导航文件名并扫描目录树

记录 `NAV_FILE` 为所选平台文件名。递归列出所有目录，排除：

- 所有以 `.` 开头的隐藏目录；
- `node_modules`、`dist`、`build`、`__pycache__`、`coverage`。

可用下列命令核验目录清单：

```bash
find . -type d \
  -not -path '*/.*' \
  -not -path '*/node_modules/*' -not -path '*/dist/*' \
  -not -path '*/build/*' -not -path '*/__pycache__/*' \
  -not -path '*/coverage/*'
```

**完成条件**：得到按深度分级的完整目录树，并确认不会覆盖未经识别的人工导航文件。

### Step 2：逐层生成

从 Level 0 开始，上一层全部写完才处理下一层。每个派发单元只接收：目录路径、[template.md](template.md) 的字段要求、父导航文件路径和 `NAV_FILE`。

处理者必须：

1. 读取该目录内的实际源码、配置和测试，提炼职责与关系。
2. 填写 Purpose / Key Files / Subdirectories / Working Here。
3. 非根目录顶部添加 `<!-- Parent: {relative-parent-path} -->`。
4. Codex 模式在正文顶部增加“同时遵守同目录 `AGENTS.md`（如存在）”的说明；该说明不替代 Parent 引用。
5. 写入 `NAV_FILE` 并只回报目录路径与完成状态，不回传代码内容。

**完成条件**：每个通过空目录判定的目录都有导航文件，且父级文件先于子级存在。

## 生成铁律

1. **先探查，再动笔**：使用当前环境的文件搜索和读取能力核实结构、依赖与测试位置。
2. **每条描述可追溯**：Purpose、Key Files 的论断必须来自实际读过的文件；拿不准则标注“待确认”。
3. **命令与入口可核验**：Working Here 中的命令必须能从 package manifest、Makefile、README 等事实源确认。
4. **贴现有风格**：跟随项目已有文档和注释风格，不套统一模板腔。

## Step 3：校验

| 检查项 | 验证 | 不通过时 |
|---|---|---|
| Parent 可解析 | 搜索 `NAV_FILE` 中的 `<!-- Parent:` 并核对目标 | 修路径或删孤儿 |
| 无孤儿 | 对照目录树 | 删除多余生成物 |
| 无遗漏 | 有源码或配置的非隐藏目录均有导航 | 补生成 |
| 不进入隐藏目录 | 隐藏目录下无导航文件 | 删除 |
| 已忽略 | 根 `.gitignore` 含 `NAV_FILE` 裸名 | 幂等追加 |
| Codex 规则未丢失 | Codex 生成物明确承接同目录 `AGENTS.md` | 补充承接说明 |

**完成条件**：全部检查通过，并报告 target、导航文件数、跳过目录数和任何需要用户处理的人工文件冲突。
