## Agent skills

Claude Code 与 Codex 共用本文件；`AGENTS.md` 必须保持为指向本文件的相对符号链接。

每次添加、更新、改名或删除 skill，都必须检查并按需同步以下注册与支持文件，使两平台发现的技能清单、调用策略及依赖声明与实际文件一致：

- Claude Code：`plugins/felix-skills/.claude-plugin/plugin.json`。顶层技能自动发现；新增或迁移嵌套技能容器时，同步 `skills` 中的容器路径。
- Codex：`plugins/felix-skills/.codex-plugin/plugin.json`。保持技能发现根覆盖实际目录，并同步该 skill 的 `agents/openai.yaml` 展示信息与调用策略；改名时迁移 sidecar，删除时一并删除。
- 双平台支持清单：`plugins/felix-skills/compatibility.json`。同步技能名称、支持范围及 `requires`；改名或删除时，处理其他技能指向旧名称的依赖。

修改上述文件、skill、平台 sidecar 或安装说明后，运行 `python3 scripts/validate-skills.py`，通过后才算完成。注册配置已覆盖变更时保留原配置，无需为自动发现的单个技能添加重复注册项。

### Issue tracker

Issues are tracked as local Markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the five canonical labels without overrides. See `docs/agents/triage-labels.md`.

### Domain docs

Domain documentation uses the single-context layout. See `docs/agents/domain.md`.
