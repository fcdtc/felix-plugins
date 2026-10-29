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
