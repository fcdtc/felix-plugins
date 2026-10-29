# 独立式适配

当前仓库独立维护文档、工单和代码；不创建 `projects.json` 或 workspace 脚本。开发分支默认 `codex/dev`，已有约定不一致先确认。

- 初始化六份 `docs/development/*.md` 及缺失的基础 `docs/agents/*.md`；存在自定义 tracker／领域布局时按其真源改写模板指针。
- `README.md` 采用仓库实际名称和能力描述，补充开发、平台、发布、MIA 与人工交接导航，保留现有运行说明。
- `AGENTS.md`／`CLAUDE.md` 添加精简任务指针。开始开发／Git 操作前读取 `docs/development/environment.md`；平台操作前读取 `platform-operations.md`；再按构建发布、Agent／Skill、RPC 接入分别读取对应手册。
- 人工交接通过平台手册按需加载 `human-handoff.md`，不将全部模板塞进代理入口。
- 新仓默认采用本地 `.scratch/` 和 single-context；已有规则优先，领域文件尚不存在时不创建空 GLOSSARY 或 ADR。

工具以 `--branch` 接收已确认分支。已有文件报告 `preserved` 时先检查其内容，避免将其他项目策略或旧初始化状态留在最终产物中。
