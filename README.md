# felix-plugins

felix 的 Agent Skills 仓库。同一份 `SKILL.md` 可以通过 Claude Code plugin、Codex plugin，或标准 Agent Skills 目录分发；平台专属的展示和调用策略放在对应 sidecar 中。

## 安装

Marketplace 名为 `felix`，其中的插件名为 `felix-skills`。远程安装适合日常使用，本地安装适合开发和调试当前工作区。

> 不要同时通过 plugin 和文件复制工具安装同一批 skill，否则客户端中可能出现重复项。

### Claude Code

#### 从远程仓库安装

```bash
claude plugin marketplace add fcdtc/felix-plugins
claude plugin install felix-skills@felix
```

#### 从本地仓库安装

传入包含 `.claude-plugin/marketplace.json` 的仓库根目录，而不是 `plugins/felix-skills` 子目录：

```bash
claude plugin marketplace add /absolute/path/to/felix-plugins
claude plugin install felix-skills@felix
```

修改本地 skill 后，可以重启 Claude Code，或在会话中运行：

```text
/reload-plugins
```

必要时强制重新加载：

```text
/reload-plugins --force
```

可通过以下命令检查安装状态：

```bash
claude plugin marketplace list
claude plugin list
claude plugin details felix-skills
```

Claude Code 中的 skill 使用 `felix-skills:<skill-name>` 命名空间。

### Codex CLI

#### 从远程仓库安装

```bash
codex plugin marketplace add fcdtc/felix-plugins
codex plugin add felix-skills@felix
```

#### 从本地仓库安装

同样传入 marketplace 仓库根目录：

```bash
codex plugin marketplace add /absolute/path/to/felix-plugins
codex plugin add felix-skills@felix
```

安装或更新插件后，重新开启 Codex 会话，再通过 `/plugins` 和 `/skills` 检查插件及其 skills。

Codex IDE 扩展不使用 plugin marketplace 时，可以把所需 skill 安装到项目级 `.agents/skills/` 或用户级 `~/.agents/skills/`。也可以使用通用 Agent Skills 安装器，但必须显式选择活动 skill；不要使用会把 `skills/deprecated/` 一并复制的全量或通配安装方式。

## Skill 发现范围

当前插件按三类目录组织：

1. **顶层活动 skills**

   ```text
   plugins/felix-skills/skills/<skill-name>/SKILL.md
   ```

   这是插件的默认目录，无需在 `plugin.json` 中逐项声明。

2. **嵌套活动 skills**

   当前包含：

   ```text
   plugins/felix-skills/skills/mattpocock/engineering/<skill-name>/SKILL.md
   plugins/felix-skills/skills/mattpocock/productivity/<skill-name>/SKILL.md
   plugins/felix-skills/skills/pstack/<skill-name>/SKILL.md
   ```

   这三个集合由 [`plugins/felix-skills/.claude-plugin/plugin.json`](plugins/felix-skills/.claude-plugin/plugin.json) 的 `skills` 字段显式注册。集合内的说明见：

   - [Engineering skills](plugins/felix-skills/skills/mattpocock/engineering/README.md)
   - [Productivity skills](plugins/felix-skills/skills/mattpocock/productivity/README.md)

3. **弃用 skills**

   ```text
   plugins/felix-skills/skills/deprecated/<skill-name>/SKILL.md
   ```

   此目录只保存历史内容，不属于插件的活动发布范围。

各 skill 是否只能显式调用，由其 `SKILL.md` frontmatter 和 `agents/openai.yaml` 策略决定；仓库中同时存在 user-invoked 和 model-invoked skills。

## 添加和组织 Skill

### 添加顶层 Skill

使用默认结构：

```text
plugins/felix-skills/skills/my-skill/SKILL.md
```

插件会从默认 `skills/` 目录发现它，不需要修改 `plugin.json` 的 `skills` 数组。

### 添加到已有嵌套集合

例如新增：

```text
plugins/felix-skills/skills/mattpocock/engineering/my-skill/SKILL.md
```

由于 `./skills/mattpocock/engineering` 已经注册，无需再次修改 manifest。

### 添加新的二级或多级集合

假设目录是：

```text
plugins/felix-skills/skills/vendor/category/my-skill/SKILL.md
```

应找到**直接包含 `<skill-name>/SKILL.md` 子目录的容器目录**，本例为：

```text
./skills/vendor/category
```

然后将它加入 `plugins/felix-skills/.claude-plugin/plugin.json`：

```json
{
  "skills": [
    "./skills/mattpocock/engineering",
    "./skills/mattpocock/productivity",
    "./skills/pstack",
    "./skills/vendor/category"
  ]
}
```

维护时注意：

- `skills` 字段会**追加**到默认 `skills/` 扫描，不会替换默认目录。
- 路径相对于插件根目录，使用 `./` 开头，且必须位于插件目录内。
- 注册直接包含 skill 子目录的精确容器；不要依赖客户端无限递归扫描。
- 新增普通 skill root 通常只需修改插件的 `plugin.json`，无需修改 marketplace 根目录的 `.claude-plugin/marketplace.json`。
- 远程发布时同步更新插件版本，并检查 `SKILL.md` frontmatter、sidecar、本地链接和辅助资源。
- 跨 Claude Code/Codex 使用的 skill 按项目约定提供 `agents/openai.yaml`；Claude-only skill 则记录其平台限制。

## Deprecated 规则

`deprecated` 只是本项目的目录约定，不是 Claude Code、Codex 或第三方扫描器通用的保留名称。

为了避免弃用 skill 被插件发现：

- 不要将 `./skills/deprecated` 加入 `plugin.json` 的 `skills` 数组。
- 不要注册会把 `deprecated/` 间接纳入扫描范围的过宽父目录。
- 不要通过通配或递归复制工具全量安装仓库中的 skills。
- 弃用一个 skill 时，将它移入 `skills/deprecated/`，并从活动 manifest、安装说明和兼容性元数据中移除。

当前 Claude Code plugin 不会发现该目录，是因为默认布局只把 `skills/<skill-name>/SKILL.md` 视为顶层 skill，而 manifest 只额外注册了两个 Matt Pocock 活动集合；并不是因为客户端识别了 `deprecated` 这个名称。

如果未来需要让所有通用 Agent Skills 扫描器都绝对无法看到弃用内容，应将归档移出标准 `skills/` 树。

## 仓库结构

```text
.
├── .claude-plugin/marketplace.json
├── AGENTS.md -> CLAUDE.md
├── plugins/felix-skills/
│   ├── .claude-plugin/plugin.json
│   ├── compatibility.json
│   └── skills/
│       ├── <skill-name>/
│       │   ├── SKILL.md
│       │   └── agents/openai.yaml
│       ├── mattpocock/
│       │   ├── engineering/<skill-name>/
│       │   └── productivity/<skill-name>/
│       ├── pstack/<skill-name>/
│       └── deprecated/<skill-name>/
└── scripts/validate-skills.py
```

`SKILL.md` 是每个 skill 的主入口和行为契约。脚本、引用和资源可以放在同一 skill 目录的 `scripts/`、`references/`、`assets/` 等辅助目录中。`agents/openai.yaml` 提供 Codex/OpenAI 的展示、调用策略和依赖元数据，不复制主工作流。

## 开发验证

修改 plugin、skill、平台 sidecar 或安装说明后，至少运行：

```bash
claude plugin validate ./plugins/felix-skills
python3 scripts/validate-skills.py
```

然后通过本地 marketplace 安装或重新加载插件，在 Claude Code 和 Codex 的实际 `/skills` 列表中确认：

- 顶层活动 skills 可见；
- manifest 显式注册的嵌套 skills 可见；
- `deprecated/` 下的 skills 不可见。

`scripts/validate-skills.py` 会组合默认顶层目录与 `plugin.json` 显式注册的容器，校验完整活动 skill inventory、名称唯一性、兼容性契约及 Codex sidecar。
