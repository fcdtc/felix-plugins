# 07: 监督 Skill、运行配置与审计收口

**What to build:** 用户可以通过 `/ticket-loop` 一次性启动全自动监督流程。Skill 先执行计划预检，再持续驱动 Runner 的允许动作，直到成功或终止失败；同时支持模型、实现命令、超时、最大成本等配置，并保留最小但完整的审计链。

**Blocked by:** 05 / 依赖链的确定性串行调度；06 / 可中断、可重开的持久运行

**Status:** done (2026-09-15, 877864e)

- [x] 新增用户显式调用的 Ticket Loop Skill，并将 Python 标准库 Runner 作为可独立运行的脚本随 Skill 发布。
- [x] Skill 自动驱动 `plan → start → step/resume → completed | terminal-failure`，正常路径无需人工接入。
- [x] Skill 只读取 Runner 的结构化 JSON、必要 Git 摘要和相关日志片段，并严格遵守 `allowed_actions`。
- [x] Skill 只调度，不修改业务代码、工单文件或 Git 历史，也不代替 Task Session 提交。
- [x] 默认探测 `/implement` 或已安装的 namespaced 等价命令，支持显式覆盖，并将解析结果冻结到 Manifest。
- [x] Task Session 使用已确认的无人值守权限模式；该安全影响在 Skill 文档中明确说明。
- [x] 默认继承 Claude Code 模型设置，支持显式模型覆盖并冻结到 Manifest。
- [x] 支持 Implementation、Closeout 和恢复超时配置。
- [x] 支持可选最大成本；每次新 Claude 调用前检查累计费用，缺失费用字段记为 unknown 而不是零。
- [x] `.ticket-loop/` 中的原始 Claude JSON和账本尽可能仅当前用户可读写，且永不自动提交。
- [x] Git 审计只保存路径、状态、统计和 SHA，不额外复制完整 diff。
- [x] Skill、Runner JSON 和 Run Ledger 均带显式 schema version，并在不兼容时 fail closed。
- [x] 成功后保留 Session、账本和日志，向用户报告本地完成状态，不自动 push、创建 PR 或清理。
- [x] 文档说明 `plan`、`start`、`step`、`resume`、`reopen`、`status`、`abort`、`clean` 的直接 CLI 用法。
- [x] 从 Skill 入口通过 fake Claude 完成一条多工单链的端到端验收，证明无需人工介入且每张新票使用新 Session。

## Completion evidence

- Implementation: `877864e` adds the explicit Ticket Loop Skill, autonomous supervisor, frozen runtime configuration, command discovery, and cost/audit gates.
- Typecheck: `python3 -m py_compile plugins/felix-skills/skills/ticket-loop/scripts/ticket_loop.py plugins/felix-skills/skills/ticket-loop/scripts/supervise.py plugins/felix-skills/skills/ticket-loop/tests/test_ticket_loop.py` passed.
- Tests: `python3 -m unittest plugins/felix-skills/skills/ticket-loop/tests/test_ticket_loop.py` passed all 65 tests.
- Review: two-axis review completed; fixed Manifest source-of-truth, installed namespaced command discovery, Skill entry coverage, schema validation, non-finite limits, actual `total_cost_usd` parsing, unique allowed-action enforcement, and bounded stderr excerpts.
