---
name: deepinit
description: 扫描整个代码库目录树，生成带层级父引用导航文档
disable-model-invocation: true
---

# Deep Init

生成一组层级的 **`CLAUDE.local.md`**——让 Claude Code 在后续搜索文件、理解模块关系、动手改代码时，不用每次重新摸索就能快速定位。

文档用 `.local` 命名，是**纯个人的**，不进版本库、不影响团队：首次运行时自动把 `**/CLAUDE.local.md` 加进项目 `.gitignore`

## 五条规则（贯穿全程，单一事实源）

1. **父级先行**：按目录深度从根到叶生成，保证子目录的 `Parent` 引用总是指向已存在的父文件。

2. **全量重生成**：已存在的 CLAUDE.local.md 一律覆盖，不做增量合并。CLAUDE.local.md 是机器生成的纯导航文档，手工内容应放代码注释或独立 NOTES——所以覆盖不会丢失该保留的东西。

3. **空目录跳过**：不值得生成的不写，避免噪音。判定见 [template.md](template.md) 的「空目录处理」。

4. **不进版本库**：首次运行时，在项目根的 `.gitignore` 里加 `CLAUDE.local.md`（裸名，匹配全树任意目录下的同名文件；幂等，已存在该行则跳过）。保证这组文档纯属个人，不影响团队。

5. **上下文隔离（强制分工）**：主 agent **绝不读代码**，只做三件事——扫描目录树、调度 subagent、校验。所有"读代码、提炼职责、写 CLAUDE.local.md"的负载全部交给 subagent。这是大代码库能跑得动的唯一方式：每个 subagent 只读自己那一个目录，产出文件即退出，主 agent 的上下文永远只装目录树骨架 + 进度，不装代码细节，不会随着项目变大而撑爆。
   - **同层并行、不同层串行**：同一层的多个目录互相独立，可同时派多个 subagent；上一层全部落盘后，才派下一层（父级先行）。
   - **派发单元**：大目录一个 subagent 独占；小目录可几个打包给同一个 subagent。
   - **派发内容**：只给「目录路径 + template.md 的字段要求 + 父 CLAUDE.local.md 路径」，subagent 无需全局上下文。用当前环境可用的 subagent 类型即可。

## 执行流

每步在进入下一步前，必须满足该步的**完成条件**——这是防提前收工的硬边界，不是建议。

### Step 1：扫描目录树

递归列出所有目录，剔除隐藏目录与生成物/依赖目录。

排除项：
- **所有以 `.` 开头的隐藏目录**（`.git`、`.venv`、`.github`、`.vscode`、`.idea` 等）——一律不进入、不递归、不生成 CLAUDE.local.md
- 非隐藏的生成物与依赖目录：`node_modules` `dist` `build` `__pycache__` `coverage`

**完成条件**：产出一棵完整的目录树，每个目录按深度分级（Level 0 = 根），树中不含任何隐藏目录。可用此命令对照核验，结果应与你的清单一致：

```bash
find . -type d \
  -not -path '*/.*' \
  -not -path '*/node_modules/*' -not -path '*/dist/*' \
  -not -path '*/build/*' -not -path '*/__pycache__/*' \
  -not -path '*/coverage/*'
```

### Step 2：逐层生成（父级先行，subagent 隔离上下文）

从 Level 0 开始，逐层向下。**上一层全部写完，才开始下一层**——否则子目录的 Parent 引用会指向还不存在的文件。

主 agent 对当前层的目录，**按派发单元（见规则 5）分组，派 subagent 并行生成**。每个 subagent 收到的是：目录路径、template.md 字段要求、父 CLAUDE.local.md 路径。subagent 自行完成：

1. 读该目录下所有文件，提炼职责与关系（遵循「生成铁律」）。
2. 按 template.md 字段填写：Purpose / Key Files / Subdirectories / Working Here。
3. 非根目录顶部加 `<!-- Parent: ../CLAUDE.local.md -->`（路径随层级调整）。
4. 写入文件，已存在则覆盖。完成后回报「目录路径 + 已生成」，不回传代码内容。

主 agent 收齐当前层所有 subagent 的完成信号后，才推进到下一层。

**完成条件**：每个通过空目录判定、应该有 CLAUDE.local.md 的目录都已生成；当前层全部落盘并回报后，才进入下一层。主 agent 的上下文里始终没有代码细节。

## 生成铁律（subagent 生成每个目录时的质量约束）

主 agent 派发任务时，把这四条随任务一并交代给 subagent——它们是产出质量的本体。

CLAUDE.local.md 的价值在于**准**——不准的导航文档比没有更糟，会误导后续的 search 和写代码。每条描述都必须经得起核验：

1. **先探查，再动笔**：生成一个目录前，并行用 Glob/Grep/Read 摸清它的结构、依赖、测试位置。绝不凭文件名或目录名臆测内容。
2. **每条描述可追溯**：Purpose、Key Files 的每个论断都要有依据——读过的具体文件。拿不准就标注「待确认」，不要编造职责或关系。
3. **命令与入口实测**：`## Working Here` 里写的测试命令、启动方式必须真实存在——能从 package.json/Makefile/README 核对。写不出来的不要凭印象填。
4. **贴现有风格**：字段措辞、中英文、详略，跟随该项目已有文档或代码注释的风格，而非套统一模板腔。

**完成条件**：生成的每个 CLAUDE.local.md，其 Key Files 与 Working Here 都基于实际读过的代码，无臆测、无编造。

### Step 3：校验

生成完毕后逐一核对，不通过则回到对应目录修正。

| 检查项 | 怎么验证 | 不通过的处理 |
|--------|----------|--------------|
| Parent 引用全可解析 | `grep -r "<!-- Parent:" --include="CLAUDE.local.md" .`，逐条核对路径存在 | 修路径，或删孤儿文件 |
| 无孤儿文件 | 对照 Step 1 目录树，所有 CLAUDE.local.md 都落在真实目录里 | 删除多余文件 |
| 无遗漏 | 每个「有源码或配置」的目录都有 CLAUDE.local.md（隐藏目录除外） | 对照 Step 1 目录树补生成 |
| 无隐藏目录索引 | 任何隐藏目录下都没有 CLAUDE.local.md | 删除该文件 |
| 已加入 .gitignore | 项目根 `.gitignore` 含裸名 `CLAUDE.local.md` 一行（匹配全树所有同名文件） | 追加该行 |

**完成条件**：四项全部通过。五条规则无违反——父级先于子级、无手工残留被误保留、空目录无文件、已隔离出版本库、同层并行不破坏父级先行。

## 参考

模板、字段取舍理由、空目录判定、最小示例：[template.md](template.md)。生成时按需查阅，无需通读。
