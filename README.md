# felix-plugins

felix 的 Agent Skills 仓库。同一份 `SKILL.md` 可以通过 Claude Code plugin、Codex plugin，或标准 Agent Skills 目录分发；平台专属的展示和调用策略放在对应 sidecar 中。

## 安装

Marketplace 名为 `felix`，插件名为 `felix-skills`。远程安装适合日常使用，本地安装适合开发和调试当前工作区。

> 不要同时通过 plugin 和文件复制工具安装同一批 skill，否则客户端中可能出现重复项。

### Claude Code

远程安装：

```bash
claude plugin marketplace add fcdtc/felix-plugins
claude plugin install felix-skills@felix
```

本地安装时传入包含 `.claude-plugin/marketplace.json` 的仓库根目录：

```bash
claude plugin marketplace add /absolute/path/to/felix-plugins
claude plugin install felix-skills@felix
```

修改本地 skill 后，可以重启 Claude Code，或运行 `/reload-plugins`；必要时使用 `/reload-plugins --force`。通过 `claude plugin list` 和 `claude plugin details felix-skills` 检查安装状态。

Claude Code 中的 skill 使用 `felix-skills:<skill-name>` 命名空间。

### Codex CLI

远程安装：

```bash
codex plugin marketplace add fcdtc/felix-plugins
codex plugin add felix-skills@felix
```

远程更新时先刷新 marketplace，再重新安装插件：

```bash
codex plugin marketplace upgrade felix
codex plugin add felix-skills@felix
```

本地安装时同样传入 marketplace 仓库根目录：

```bash
codex plugin marketplace add /absolute/path/to/felix-plugins
codex plugin add felix-skills@felix
```

安装或更新后必须重新开启 Codex 会话，再通过 `/plugins` 和 `/skills` 检查实际加载结果。`show-me` 的完整调用名是 `felix-skills:show-me`，不是 `showme`；它只允许显式调用，但仍应出现在 `/skills` 列表中。

Codex IDE 扩展不使用 plugin marketplace 时，可以把所需 skill 安装到项目级 `.agents/skills/` 或用户级 `~/.agents/skills/`。

## 爱学项目初始化

`init_aixue` 仅显式调用，初始化目标仓库的开发、构建部署、MIA 与人工交接文档；支持独立仓库和集中式协调主仓。集中式额外交付配置驱动的 workspace 脚本与临时仓库测试，不创建实际工作区。

Claude Code 调用示例：

```text
/felix-skills:init_aixue standalone
/felix-skills:init_aixue centralized，主仓=<路径>，子仓清单=<已确认登记或配置文件>
```

Codex 使用技能列表中实际发现的 `init_aixue` 显式调用。已有文档、代理规则与脚本保留并增量合并；源码入口和内部模板见 [init_aixue](plugins/felix-skills/skills/init_aixue/SKILL.md)。技能初始化不安装依赖、不提交／推送或操作平台，生成手册不代表自动部署已绑定。

## 会话总结

`session-summary` 仅由用户显式触发，按今天、昨天、指定日期、本周、上周或自定义时间区间，读取本机 Claude Code 与 Codex 全部项目会话，生成中文 Markdown。支持跨天续聊、压缩归档和跨平台主题归纳；系统／工具／子代理消息不作为提问。日报、周报统一采用“整体概览、重点工作、推进脉络”，时间线列为“时间、session_id、主要内容”；正文不展示日志来源、调用指令或扫描统计。读取缺口在交付回复中单独说明。

Claude Code 调用示例：

```text
/felix-skills:session-summary
/felix-skills:session-summary 昨天
/felix-skills:session-summary 本周
/felix-skills:session-summary 上周
/felix-skills:session-summary 2026-11-12
/felix-skills:session-summary 2026-11-01 至 2026-11-12
/felix-skills:session-summary --start 2026-11-12T09:00:00+08:00 --end 2026-11-12T18:00:00+08:00
/felix-skills:session-summary 本周 --output /absolute/path/to/summary.md
```

Codex 从技能列表中显式调用 `session-summary`，使用相同参数。所有时间统一为北京时间（UTC+08:00），无需指定时区，不受电脑系统时区影响；默认总结北京时间今天。自然周从周一开始，本周只覆盖截至扫描时间的部分。日期区间包含起止两天，精确时间区间开始含、结束不含。默认输出到当前目录 `session-summaries/`，文件名带单日日期或起止范围；已有文件保留，除非明确要求更新。也支持只返回 Markdown。记录仅在本地处理，不创建定时任务或自动上传。

入口：[session-summary](plugins/felix-skills/skills/session-summary/SKILL.md)，详细语义见[时间范围规则](plugins/felix-skills/skills/session-summary/references/time-ranges.md)。需要 Python 3.9+；读取 `.jsonl.zst` 需要本机 `zstd`，缺少时继续总结可读部分并标记覆盖不完整。

提取器的本地回归测试（不读取真实会话）：

```bash
python3 -I -B -m unittest discover -s plugins/felix-skills/tests -p 'test_collect_sessions.py'
```

## Skill 复盘优化

`skill-retro` 仅由用户显式触发。在新会话中先用待优化 Skill 执行真实任务，再在同一会话调用它；它依据工具调用、结果、用户纠正和产物，分别检查执行与表达问题，默认直接修改目标 Skill 的本地维护源。

```text
/felix-skills:skill-retro
/felix-skills:skill-retro 优化 <skill 名称或路径>，重点看重复探索和交付表达
/felix-skills:skill-retro 只分析 <skill 名称或路径>，暂不修改
```

未指定目标且执行记录能唯一定位时自动选择，否则询问。Codex 从技能列表显式调用 `skill-retro`，使用相同要求。默认复用当前上下文，仅在必要时定向补读本次任务日志；不扫描无关会话，不自动重跑原任务或上传记录。修改后运行仓库校验，并对原问题和相邻场景做静态走查；静态检查与实跑结果分开报告。

入口：[skill-retro](plugins/felix-skills/skills/skill-retro/SKILL.md)。借鉴 `retro` 的证据驱动复盘方法，聚焦目标 Skill，而非扩展成全局环境审计；写作方法复用 `writing-for-agents`。

## MIA Agent 链路设计

`mia-design` 仅显式调用，支持新建或优化 Agent，核对 System Prompt、User Prompt、Skill、Tool、Skill SP 与 Tool SP 六类配置。通过**交付模式（Delivery Mode）**参数 `--delivery local|sync` 固定选择本轮交付方式：

- `local`：只读获取平台基线，在项目 `.design/<agent_id>/<本轮唯一标识>/` 下仅交付改动文件，`baseline/` 放对应已取得的旧稿，`updated/` 放修改或新增文件的完整正文；`replacement-manifest.md` 为每个改动文件分别说明改了什么、为什么改、依据及预期效果。另生成可直接离线打开的分页 `diff.html`，每页一个文件的 Diff，下方附该文件的修改说明；对比 Prompt、Skill 与 SP，排除 Tool 定义（Tool SP 仍参与）；不生成 `packages/` 或 ZIP。由用户按清单应用，不修改、运行或发布 MIA。新建且暂无 Agent ID 时目录使用 `new-agent`。
- `sync`：直接保存目标范围内的 MIA 草稿、更新 Agent 挂载并回读核对；选择该模式即授权本轮范围内的草稿同步，运行、发布与部署仍需单独授权。

```text
/felix-skills:mia-design --delivery local Agent=<Agent ID 或设计页URL> 优化工具调用规则
/felix-skills:mia-design --delivery sync Agent=<Agent ID 或设计页URL> 优化场景路由
/felix-skills:mia-design --delivery local 新建 Agent，目标=<业务职责>
```

没有默认模式：省略参数且未明确表达交付方式时，先询问用户选择；“只出本地文档／我自行同步”与“直接改 MIA”分别等价于 `local`、`sync`。冲突或无效参数先澄清，MIA 链接不代表写入授权，平台依赖或权限缺失不自动切换模式。Codex 从技能列表显式调用 `mia-design`，使用同一参数与规则。

入口与完整模式契约：[mia-design](plugins/felix-skills/skills/mia-design/SKILL.md)；本地文件命名、逐文件修改说明和应用规则见[本地交付与差异查看](plugins/felix-skills/skills/mia-design/references/local-delivery.md)。平台操作依赖官方 `miacli`；离线新建的 `local` 设计不要求平台访问，既有 Agent 的本地修改仍需只读基线。

Diff 工具使用 Python 3.9+ 标准库，直接比较两边同路径文件，并从清单的 `## 逐文件修改说明` 下按 `### 相对文件路径` 读取说明，生成单个分页 HTML；每个变化文件须有非空的 `- 为什么改：...`，缺失时明确报错。支持上一页／下一页、文件选择与左右方向键；不需要中间 diff 文件、服务或前端依赖。缺旧稿标为“无基线”，是否新增或删除以清单为准；源文件或清单变化后须重新生成。Skill 的改动文件需合入对应固定版本的完整目录后再打包上传，稀疏交付目录不能直接作为完整 Skill 上传。

```bash
python3 -I -B plugins/felix-skills/skills/mia-design/scripts/render_diff.py <本轮交付目录>
python3 -I -B -m unittest discover -s plugins/felix-skills/tests -p 'test_mia_design_diff.py'
```

默认生成交付目录下的 `diff.html`；更新既有页面时先检查目标，再使用 `--overwrite`。页面保持私有本地，不上传 Artifact 或其他服务。

## MIA 评测链路审计

`mia-eval` 仅显式调用，输入一个固定 MIA Agent 版本与评测任务 URL，适用于任意 Agent，不限定 Alfred。默认由官方 CLI 获取完整记录、按需导出和读取 Session debug；CSV 是权限、保留期限或平台外标注等缺口的可选补充，不要求用户每次手工下载。

```text
/felix-skills:mia-eval Agent=<Agent设计页URL或motif_id/agent_id/固定版本> 评测=<含task_id的评测任务URL>
/felix-skills:mia-eval Agent=<固定版本定位> 评测=<任务URL> case=1-10 并发=3 输出=<私有目录>
```

每个 case 由独立子代理对照设计基线、可见思考和实际请求回包，分别分析设计遵循、任务完成及执行挣扎。默认最多并发 3 个，明确消耗模型 tokens；只读已有运行，不重新评测或修改平台。环境与身份 Header 遵循目标项目规则，不写死某个 Agent 的 PPE。

固定模板输出 `site/index.html` 和 `site/cases/<case_key>.html`，支持从汇总逐项打开详情。统计由 Python 渲染器重算：顺畅通过率以全部选定 case 为分母，恢复/挣扎、不通过与证据不足分别列示；缺失 case 不被排除。默认私有本地交付，不上传真实学习内容、凭据或整段思考。

入口：[mia-eval](plugins/felix-skills/skills/mia-eval/SKILL.md)；字段覆盖与 URL 限制见[取证规则](plugins/felix-skills/skills/mia-eval/references/acquisition.md)，固定 JSON 与页面约定以[渲染器](plugins/felix-skills/skills/mia-eval/scripts/render_report.py)的校验及 `demo` 生成的合成 JSON 为准。

纯合成模板预览与本地测试，不调用 MIA 或模型：

```bash
python3 -I -B plugins/felix-skills/skills/mia-eval/scripts/render_report.py demo --out /private/tmp/mia-eval-demo-new
python3 -I -B -m unittest discover -s plugins/felix-skills/tests -p 'test_mia_eval_report.py'
```

## 双平台 manifest

插件根同时维护两份原生 manifest：

- [`.claude-plugin/plugin.json`](plugins/felix-skills/.claude-plugin/plugin.json)：Claude Code manifest。Claude Code 默认发现顶层 `skills/<name>/SKILL.md`，并通过字符串数组追加嵌套 skill 容器。
- [`.codex-plugin/plugin.json`](plugins/felix-skills/.codex-plugin/plugin.json)：Codex manifest。Codex 使用单字符串根 `./skills/`，递归发现该根下的活动 skills。

两份 manifest 的 `name` 和 `version` 必须一致。新增、删除或修改发布内容时同步提升版本，避免客户端继续使用旧缓存。

## Skill 发现范围

### 顶层活动 skills

```text
plugins/felix-skills/skills/<skill-name>/SKILL.md
```

这是 Claude Code 的默认目录，也位于 Codex 的统一活动根中。新增普通顶层 skill 不需要逐项登记 manifest。

### 嵌套活动集合

当前包含：

```text
plugins/felix-skills/skills/mattpocock/engineering/<skill-name>/SKILL.md
plugins/felix-skills/skills/mattpocock/productivity/<skill-name>/SKILL.md
plugins/felix-skills/skills/pstack/<skill-name>/SKILL.md
```

Codex 从 `./skills/` 递归发现这些技能；Claude Code 则由 `.claude-plugin/plugin.json` 显式注册直接包含技能子目录的容器。

集合说明：

- [Engineering skills](plugins/felix-skills/skills/mattpocock/engineering/README.md)
- [Productivity skills](plugins/felix-skills/skills/mattpocock/productivity/README.md)

### 弃用 skills

```text
plugins/felix-skills/archive/deprecated-skills/<skill-name>/SKILL.md
```

弃用内容必须位于活动 `skills/` 根之外。`deprecated` 不是任何客户端通用的保留名称，不能依赖目录名阻止递归发现。

## 添加和组织 Skill

### 添加顶层 Skill

创建：

```text
plugins/felix-skills/skills/my-skill/SKILL.md
plugins/felix-skills/skills/my-skill/agents/openai.yaml
```

并同步更新 `compatibility.json`。顶层 skill 无需写入 Claude manifest；Codex 也会从统一根发现它。

### 添加到已有嵌套集合

例如：

```text
plugins/felix-skills/skills/mattpocock/engineering/my-skill/SKILL.md
```

已有容器已经注册到 Claude manifest，Codex 也会递归发现，无需再次修改 manifest。

### 添加新的嵌套集合

假设目录是：

```text
plugins/felix-skills/skills/vendor/category/my-skill/SKILL.md
```

Codex 会从 `./skills/` 递归发现。Claude Code 还需要把直接包含 `<skill-name>/SKILL.md` 子目录的容器 `./skills/vendor/category` 加入 `.claude-plugin/plugin.json` 的 `skills` 数组。

维护时注意：

- Claude manifest 的 `skills` 是字符串数组；Codex manifest 的 `skills` 是单字符串根，二者不可混用。
- 所有路径必须位于插件目录内。
- 远程发布时同步提升两份 manifest 的版本。
- 跨 Claude Code/Codex 使用的 skill 按项目约定提供 `agents/openai.yaml`；Claude-only skill 记录平台限制。
- 修改后运行 `python3 scripts/validate-skills.py`，确保两个平台 inventory 与 `compatibility.json` 完全一致。

## 弃用规则

弃用 skill 时：

1. 将完整技能目录移到 `plugins/felix-skills/archive/deprecated-skills/<skill-name>/`；
2. 从 `compatibility.json` 和活动文档中移除；
3. 同步提升两份 plugin manifest 的版本；
4. 验证 Claude Code 与 Codex 的实际 `/skills` 均不再显示它。

不要把归档放回 `skills/`，也不要让任何活动 manifest 指向归档目录。

## 仓库结构

```text
.
├── .claude-plugin/marketplace.json
├── AGENTS.md -> CLAUDE.md
├── plugins/felix-skills/
│   ├── .claude-plugin/plugin.json
│   ├── .codex-plugin/plugin.json
│   ├── compatibility.json
│   ├── archive/deprecated-skills/<skill-name>/
│   └── skills/
│       ├── <skill-name>/
│       │   ├── SKILL.md
│       │   └── agents/openai.yaml
│       ├── mattpocock/
│       │   ├── engineering/<skill-name>/
│       │   └── productivity/<skill-name>/
│       └── pstack/<skill-name>/
└── scripts/validate-skills.py
```

`SKILL.md` 是每个 skill 的主入口和行为契约。脚本、引用和资源可以放在同一 skill 目录的 `scripts/`、`references/`、`assets/` 等辅助目录中。`agents/openai.yaml` 提供 Codex/OpenAI 的展示和调用策略，不复制主工作流。

## 开发验证

修改 plugin、skill、平台 sidecar、安装说明或支持范围后，至少运行：

```bash
claude plugin validate ./plugins/felix-skills
python3 scripts/validate-skills.py
git diff --check
```

然后从本地 marketplace 重新安装或加载插件，并在 Claude Code 与全新 Codex 会话的实际 `/skills` 中确认：

- 顶层活动 skills 可见；
- Matt Pocock 和 pstack 嵌套 skills 可见；
- `archive/deprecated-skills/` 中的 skills 不可见；
- 两个平台均发现完整且无重复的活动 inventory。

`scripts/validate-skills.py` 会分别模拟 Claude Code 与 Codex 的发现规则，并校验两份 manifest、完整活动 inventory、名称唯一性、兼容性契约及 Codex sidecar。
