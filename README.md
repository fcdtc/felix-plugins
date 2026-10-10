# felix-plugins

felix 的个人 Agent Skills 仓库，同时支持 Claude Code 与 Codex。技能正文只维护一份；Claude Code 使用原生 plugin marketplace，Codex 使用 `skills` CLI。

## 支持范围

所有 skill 都要求用户显式调用，不会由模型隐式触发。

| Skill | Claude Code | Codex | 备注 |
|---|---:|---:|---|
| `adhd` | ✓ | ✓ | |
| `codebase-query` | ✓ | ✓ | |
| `codebase-tutor` | ✓ | ✓ | |
| `deepinit` | ✓ | ✓ | 按 target 生成平台对应的本地导航文件 |
| `go-service-structure` | ✓ | ✓ | |
| `html-ppt` | ✓ | ✓ | 部分导出能力需要本机 Chrome |
| `note` | ✓ | ✓ | 使用 `~/basic-memory/` |
| `note-import` | ✓ | ✓ | 同时安装 `note` |
| `note-lint` | ✓ | ✓ | 同时安装 `note` |
| `note-polish` | ✓ | ✓ | 同时安装 `note` |
| `note-query` | ✓ | ✓ | 同时安装 `note` |
| `prompt-builder` | ✓ | ✓ | |
| `video-summary` | ✓ | ✓ | ASR 兜底要求 macOS/Apple Silicon、ffmpeg、yt-dlp 和网络 |
| `ticket-loop` | ✓ | — | **Claude Code only**：依赖 Claude CLI Task Session、resume、权限模式与成本协议 |

机器可读的完整边界见 [`plugins/felix-skills/compatibility.json`](plugins/felix-skills/compatibility.json)。

## Claude Code 安装

在 Claude Code 会话中运行：

```text
/plugin marketplace add fcdtc/felix-plugins
/plugin install felix-skills@felix
```

安装后 skill 以 `felix-skills:<skill名>` 形式出现。

## Codex 安装

`skills` CLI 能从本仓库的嵌套目录发现全部 14 个 skill。因为 `ticket-loop` 是 Claude Code only，Codex 安装时必须明确选择下面 13 个兼容 skill，不能使用 `--skill '*'`。

```bash
CODEX_SKILLS="adhd codebase-query codebase-tutor deepinit go-service-structure html-ppt note note-import note-lint note-polish note-query prompt-builder video-summary"
```

### 项目级

在目标仓库根目录运行；项目级是默认 scope：

```bash
npx skills@latest add fcdtc/felix-plugins \
  --agent codex --skill $CODEX_SKILLS
```

安装器会把 skill 安装到当前项目的 agent skills 目录，并生成项目级 lockfile。也可以省略 `--skill` 进入交互选择，但不要选择 `ticket-loop`。

### 用户级

添加 `--global`，使 skills 对该用户的 Codex 项目可用：

```bash
npx skills@latest add fcdtc/felix-plugins \
  --global --agent codex --skill $CODEX_SKILLS
```

需要非交互安装时可额外传 `--yes`。`note-import`、`note-lint`、`note-polish`、`note-query` 依赖 sibling `note` 目录，因此不要单独安装这些 consumer。

## 仓库结构

```text
.
├── .claude-plugin/marketplace.json
├── AGENTS.md -> CLAUDE.md
├── plugins/felix-skills/
│   ├── .claude-plugin/plugin.json
│   ├── compatibility.json
│   └── skills/<skill-name>/
│       ├── SKILL.md
│       └── agents/openai.yaml   # Codex sidecar；Claude-only skill 无此文件
└── scripts/validate-skills.py
```

`SKILL.md` 是唯一的行为正文。`agents/openai.yaml` 只描述 Codex 的展示信息和调用策略，不复制工作流。

## 开发验证

```bash
python3 scripts/validate-skills.py
python3 -m unittest plugins/felix-skills/skills/ticket-loop/tests/test_ticket_loop.py
```

验证器检查 marketplace/plugin、兼容矩阵、frontmatter、Codex sidecar、平台路径边界、文档链接和共享 `AGENTS.md` 入口。
