# Ticket Loop：全自动工单会话编排

Status: ready-for-agent

## Problem Statement

用户已经能够用 `/to-tickets` 将规格拆分为一组带依赖关系的本地 Markdown 实施工单，并用 `/implement @工单路径` 逐张实施。然而，当前流程仍要求用户手工寻找下一张可执行工单、启动全新 Claude Code 会话、检查实现是否提交、处理失败或遗留改动、恢复原会话、收尾工单状态，再启动下一张工单。

长工单链中，手工编排容易出现以下问题：

- 新工单错误继承上一张工单的上下文；
- 上一张工单留下未提交代码时，下一张工单仍被启动；
- Claude 进程正常退出被误认为任务已经完成；
- 实现已经提交，但工单状态、验收清单和完成证据没有收尾；
- 失败后启动了新会话，而不是恢复了解当前实现上下文的原会话；
- 工单依赖未满足时仍按文件名继续向后执行；
- 并发运行、外部 Git 操作或中断使工作区与运行状态失去对应关系；
- 无人值守执行缺少超时、恢复上限、审计记录和安全停止条件。

用户需要一套可在本地仓库中全自动运行、以 Git 和工单状态为硬门禁、并能精确恢复 Claude Code session 的串行工单编排方案。它应借鉴 Ralph 的“每个工作单元使用全新上下文、通过持久状态跨轮推进”思想，同时补足 Ralph 在 Git 校验、依赖调度、完成判定、超时、恢复和审计方面的薄弱点。

## Solution

提供一个 `ticket-loop` Skill 和一个可独立调用的 Python 标准库 CLI。Skill 充当全自动监督者，CLI 充当确定性的执行内核。一次运行会冻结工单清单和依赖图，随后串行选择编号最小的可执行工单，为每张工单创建全新的 Claude Code session，并严格以 `/implement @工单绝对路径` 作为该 session 的第一条 prompt。

CLI 每次只执行一个原子步骤，并以结构化 JSON 返回状态、失败门禁和允许的后续动作。Skill 根据这些可信事实自动选择下一步：开始新工单、恢复当前 session、进入固定 closeout、继续 closeout，或安全终止。正常路径不要求人工介入。

每张工单分为两个阶段：

1. **Implementation**：任务 session 实现、验证并提交代码。
2. **Closeout**：恢复同一 session，核验验收标准、更新工单状态和 checklist、写入完成证据并提交剩余变更。

只有当实现产生提交、Git 历史保持合法、工作区完全干净、工单被正确收尾且完成证据通过结构校验后，系统才会启动下一张工单。实现或 closeout 出现可恢复异常时，系统最多恢复原 session 两次；发生危险 Git 漂移或恢复额度耗尽时，运行进入终止失败状态，保留全部现场，不自动 reset、stash、切换分支或强制通过。

## User Stories

1. 作为拥有一组本地实施工单的开发者，我希望用一个命令启动整条工单链，以便不必逐张手工调用 `/implement`。
2. 作为开发者，我希望每张新工单都在全新的 Claude Code session 中执行，以免上一张工单的对话上下文污染下一张工单。
3. 作为 Skill 作者，我希望新 session 的首条 prompt 严格以 `/implement` 起手，以确保只能人工触发的 Skill 被 Claude Code 正确加载。
4. 作为开发者，我希望可以传入 issues 目录和编号范围，以便运行目录中的指定工单集合。
5. 作为开发者，我希望可以直接传入若干工单文件，以便精确控制一次运行的范围。
6. 作为开发者，我希望运行开始时冻结 manifest，以便中途新增或修改目录中的其他工单不会改变本次运行范围。
7. 作为开发者，我希望系统自动区分 implementation ticket 和 wayfinder ticket，以免把研究、原型或 grilling 工单交给 `/implement`。
8. 作为开发者，我希望系统兼容本地工单中 Markdown 加粗与裸字段两种状态书写方式，以适应现有 tracker 配置与 `/to-tickets` 输出。
9. 作为开发者，我希望系统解析 `Blocked by` 并验证依赖图，以免在 blocker 未完成时启动后续工单。
10. 作为开发者，我希望系统始终选择编号最小的 frontier 工单，以获得确定、可复现的串行执行顺序。
11. 作为开发者，我希望 manifest 外的 task blocker 只有在完成后才被视为满足，以免范围裁剪绕过依赖。
12. 作为开发者，我希望 manifest 外的 wayfinder blocker 只有在 resolved 后才被视为满足，以兼容规划阶段和实施阶段之间的边界。
13. 作为开发者，我希望缺失 blocker、重复编号、自依赖或依赖环使运行在修改代码前失败，以免系统猜测依赖语义。
14. 作为开发者，我希望在正式执行前运行只读 plan，以便系统先验证仓库、工单分类、依赖图和 frontier。
15. 作为开发者，我希望 plan 不调用 Claude、不创建 session，也不修改工单，以便安全预检。
16. 作为开发者，我希望启动运行时必须位于本地命名分支，以免在 detached HEAD 上产生难以归属的提交。
17. 作为开发者，我希望运行冻结仓库根目录、Git common directory、分支和起始 HEAD，以便每一步都能发现外部漂移。
18. 作为开发者，我希望系统在启动运行和每张新工单前要求工作区、index 和 untracked 文件全部干净，以免任务 session 提交既有改动。
19. 作为开发者，我希望系统在 merge、rebase、cherry-pick 或 revert 未完成时拒绝推进，以免自动化扩大 Git 冲突。
20. 作为开发者，我希望恢复当前工单时允许保留该 session 遗留的未提交修改，以便原执行者能继续完成半成品。
21. 作为开发者，我希望恢复前确认分支、HEAD 和工作区仍可归属于当前工单，以免错误 session 修改错误现场。
22. 作为开发者，我希望任务 session 可以产生一个或多个实现 commit，以免编排器强迫不自然的单 commit 历史。
23. 作为开发者，我希望外层监督者不替任务 session 自动 `git add` 或 `git commit`，以免把半成品或无关文件包装成成功提交。
24. 作为开发者，我希望实现结束后检查起始 HEAD 是否为当前 HEAD 的祖先，以发现 reset、rebase 或历史改写。
25. 作为开发者，我希望 Claude 进程退出码和 JSON 错误状态被记录并校验，以免把执行失败当成成功。
26. 作为开发者，我希望进程正常退出仍需通过 Git 与工单成功关卡，以免仅凭最终文本推进。
27. 作为开发者，我希望实现阶段未提交代码时自动恢复原 session，以便最了解修改意图的执行者完成验证和提交。
28. 作为开发者，我希望实现阶段 HEAD 未前进时自动恢复原 session，以便它继续未完成的实现，而不是启动下一张工单。
29. 作为开发者，我希望实现提交后自动恢复同一 session 进入 closeout，以便复用它对实现和验证证据的理解。
30. 作为开发者，我希望 closeout 核对每一项 acceptance criterion，并且只勾选有证据证明已满足的项目，以免工单状态虚假完成。
31. 作为开发者，我希望 closeout 将状态写为包含日期和实现 commit 的 done 标记，以便从工单直接追溯实现。
32. 作为开发者，我希望 closeout 写入 Implementation、Typecheck、Tests 和 Review 证据，以便后续审计完成依据。
33. 作为开发者，我希望不适用的验证项必须记录原因，以免空缺被误认为通过。
34. 作为开发者，我希望完成证据出现未解释的失败信息时自动恢复而非推进，以免已知失败被隐藏。
35. 作为开发者，我希望 closeout 提交只负责当前工单的状态、checklist 和证据，以便实现历史与生命周期收尾清晰可辨。
36. 作为开发者，我希望实现 session 已经更新部分工单内容时不自动改写历史，以免编排器执行危险的 rebase 或 commit 拆分。
37. 作为开发者，我希望实现与 closeout 分别拥有最多两次恢复机会，以便小型遗漏可以自动修复，又不会无限循环。
38. 作为开发者，我希望 resume prompt 包含失败门禁和已观测事实，以便原 session 能针对性修复，而不是重复完整任务。
39. 作为开发者，我希望 resume prompt 明确禁止开始其他工单，以维持一次 session 只负责一张工单的不变量。
40. 作为开发者，我希望首次实现默认有 90 分钟超时，以免 Claude 进程永久挂起。
41. 作为开发者，我希望 closeout 和恢复默认有 30 分钟超时，以便异常不会无限占用运行。
42. 作为开发者，我希望超时后保留 session、日志和工作区，以便自动恢复可以继续同一执行上下文。
43. 作为开发者，我希望每次恢复使用精确 session ID 而不是最近会话，以免同目录中的其他 Claude 会话被误恢复。
44. 作为开发者，我希望运行默认继承当前 Claude Code 模型设置，并允许显式覆盖，以便能力和成本可按场景配置。
45. 作为开发者，我希望实现命令能自动探测短名称或 namespaced 名称，并冻结到 manifest，以免恢复阶段命令解析发生变化。
46. 作为开发者，我希望所有任务 session 使用无人值守权限模式，以便整条工单链无需人工批准工具调用。
47. 作为开发者，我希望运行可以设置可选成本上限，以便在下一次模型调用前停止超预算执行。
48. 作为开发者，我希望费用字段缺失时被标记为未知，而不是错误地按零成本累计。
49. 作为开发者，我希望同一仓库同一时间只允许一个 ticket loop，以免多个运行竞争工作区和 Git index。
50. 作为开发者，我希望陈旧锁可被识别并归档，以便进程崩溃后能够恢复，而不需要手工删除锁文件。
51. 作为开发者，我希望运行账本保存在仓库内，以便状态与目标项目相邻、容易发现和检查。
52. 作为开发者，我希望账本通过本地 Git exclude 排除，以免自动化状态污染项目提交。
53. 作为开发者，我希望账本原子写入，以免进程中断留下半个 JSON 文件。
54. 作为开发者，我希望账本记录 manifest、状态、session ID、HEAD、尝试次数、退出码、成本和日志路径，以便完整恢复和审计。
55. 作为开发者，我希望原始 Claude JSON 被保留，以便故障分析时能够还原模型执行结果。
56. 作为开发者，我希望账本文件仅当前用户可读写，以降低日志中项目内容泄露的风险。
57. 作为开发者，我希望 Git 摘要只记录路径、状态、统计和 SHA，而不是复制完整 diff，以减少敏感代码重复落盘。
58. 作为开发者，我希望 runner 每次只执行一个 Claude 调用，以便主 Agent 在每个状态转换之间保持监督能力。
59. 作为开发者，我希望 runner 始终返回一个结构化 JSON 对象，以便 Skill 不需要解析面向人的终端文本。
60. 作为开发者，我希望结构化结果明确列出 allowed actions，以免监督者绕过状态机执行非法转换。
61. 作为开发者，我希望主 Agent 只调度、不修改业务代码、不提交代码，以保持监督者与执行者职责分离。
62. 作为开发者，我希望主 Agent 默认不读取完整 diff 和完整日志，以保持长工单链中的上下文整洁。
63. 作为开发者，我希望无法安全恢复的分支漂移、历史改写或 Git 操作进入终止失败，以免自动化破坏现场。
64. 作为开发者，我希望终止失败后保留可恢复命令和 session ID，以便之后精确继续。
65. 作为开发者，我希望普通 resume 不能越过终止失败，以免危险现场被悄悄重新开启。
66. 作为开发者，我希望显式 reopen 在重新校验仓库、分支、历史、Git 操作和 session 后恢复运行，以便人工修好环境后继续。
67. 作为开发者，我希望 abort 只停止调度并释放锁，不 reset、不 stash、不删除现场，以便安全停止。
68. 作为开发者，我希望 status 可以只读查看运行和当前工单，以便诊断而不触发 Claude 调用。
69. 作为开发者，我希望普通启动在只存在一个未完成运行时自动恢复，以便意外中断后的使用保持简单。
70. 作为开发者，我希望存在多个未完成运行时 fail closed 并列出精确恢复方式，以免系统猜测应恢复哪个运行。
71. 作为开发者，我希望 SIGINT 或 SIGTERM 能先终止当前 Claude 子进程、记录 interrupted 并释放锁，以便受控中断。
72. 作为开发者，我希望 SIGKILL 后能通过陈旧锁与 running 状态识别中断，以便下次运行自动恢复。
73. 作为开发者，我希望成功后保留账本和 Claude session，以便进行审计或复盘。
74. 作为开发者，我希望 clean 必须显式调用，以免成功运行后自动删除有价值的恢复信息。
75. 作为开发者，我希望全部工单完成后只报告本地成功，不自动 push、创建 PR 或切换分支，以避免额外外部副作用。
76. 作为开发者，我希望任何工单在恢复额度耗尽后都不能“低质量通过”，以保证后续工单只建立在真正完成的前置任务上。
77. 作为插件维护者，我希望 runner 只使用 Python 标准库，以减少安装依赖和跨项目运行摩擦。
78. 作为插件维护者，我希望 Skill 与 runner 的协议有版本号，以便将来演进账本和 JSON 输出时能够检测不兼容状态。
79. 作为插件维护者，我希望 CLI 可以独立运行，以便排查 Skill 编排问题和用于其他自动化入口。
80. 作为插件维护者，我希望外部行为测试不调用真实 Claude，以免测试产生模型费用或不确定结果。

## Implementation Decisions

- 新增一个用户显式调用的 `ticket-loop` Skill，作为全自动监督者；新增一个 Python 标准库 CLI，作为确定性的状态机和执行内核。
- 不修改或复制上游 `/to-tickets` 与 `/implement`。`ticket-loop` 消费 `/to-tickets` 生成的本地 Markdown 工单，并以用户显式命令的形式启动 `/implement`。
- Skill 在一次调用中持续驱动 CLI 的原子步骤，直到运行完成或进入终止失败；正常路径不向用户请求逐步批准。
- CLI 提供 `plan`、`start`、`step`、`resume`、`reopen`、`status`、`abort` 和 `clean` 操作。
- `step` 每次最多触发一次 Claude Code 调用。CLI 的 stdout 只输出单个、带 schema version 的 JSON 对象；面向人的诊断写入 stderr。
- JSON 结果包含运行状态、当前工单、session ID、失败门禁、Git 摘要和允许执行的后续动作。监督 Skill 不得绕过 `allowed_actions`。
- 支持以 issues 目录、编号范围或显式文件列表选择工单。正式运行前冻结 manifest；恢复运行时不重新扫描范围。
- task ticket 通过文件命名与字段集合联合识别；含裸 `Type:` 的 wayfinder ticket 不进入 task manifest。
- task ticket 状态兼容 `**Status:**` 与裸 `Status:` 语法，但分类仍要求 task ticket 的完整结构，避免与 wayfinder ticket 混淆。
- 工单依赖解析只读取 `Blocked by` 字段中的编号，不从标题或正文任意提取数字。
- manifest 和同一 issues 目录共同构成依赖解析域。manifest 外的 task blocker 必须为 done；manifest 外的 wayfinder blocker 必须为 resolved。
- 依赖图在开始执行前检查缺失节点、重复编号、自依赖和环。运行始终串行选择编号最小的 frontier 工单，不并行处理 ticket。
- 每张新工单使用预生成 UUID 的全新、可持久化 Claude Code session。恢复只使用精确 session ID，不使用“最近会话”语义；Claude JSON 返回的 session ID 必须同时匹配当前 ledger 与 active ticket 映射，否则 fail closed。
- 所有 mutation 命令通过 Git common directory 下固定 inode 的 POSIX command lock 串行执行；活动调用记录 Runner PID、Claude child PID/PGID 和 in-flight transport 状态，`abort` 先终止并确认整个子进程组退出，再释放仓库锁。
- Claude 调用前持久化 prepared intent，spawn 后持久化 child PGID；spawn 失败不创建 recovery，Runner 崩溃后若调用已 spawn，则回收遗留进程组并只允许通过原 session resume。
- 新 session 的第一条 prompt 严格为 `/<resolved-implement-command> @<ticket-absolute-path>`，不追加其他文字。
- 实现命令默认解析 `/implement`；可自动识别已安装的 namespaced 等价命令，也可由参数显式覆盖。解析结果冻结到 manifest。
- Claude Code 子进程使用非交互打印模式、JSON 输出和无人值守权限模式。模型默认继承用户配置，可显式覆盖并冻结到 manifest。
- Task Session 通过独立的 appended system context 获得无人值守决策契约，不改变严格的首条 slash-command prompt：工单/spec 已声明的测试 seam 视为预先同意；缺失时对纯本地、可逆、低风险 seam 采用推荐默认并记录，高风险、不可逆或外部副作用仍停止。
- 每张工单有 implementation 与 closeout 两个阶段。implementation 首次调用后最多恢复两次；closeout 固定首次调用后最多恢复两次。
- implementation 默认超时 90 分钟；closeout 与异常恢复默认超时 30 分钟；所有超时可配置。
- implementation 成功不能只依赖 Claude 文本或退出码，必须同时满足 Git 历史合法、存在至少一个新提交、没有未提交修改、没有未完成 Git 操作等门禁。
- closeout 复用 implementation session，不新建审计 session。正常 closeout prompt 固定冻结；异常 resume prompt 由 CLI 事实模板和监督 Skill 的语义归纳生成。
- closeout 要求逐项核验验收标准、更新 done 状态、记录日期与实现 commit、写 Completion evidence，并提交属于当前工单的剩余修改。
- Completion evidence 至少包含 Implementation、Typecheck、Tests 和 Review。空值不允许；`Not applicable` 必须说明原因；未解释的失败词被保守判定为未通过。
- 实现可以产生多个 commit。若 implementation 已经提交部分或全部 ticket 收尾信息，不改写 Git 历史；closeout 只补齐缺失内容。没有剩余修改时不制造空 commit。
- closeout 新增提交时，提交信息固定使用 `chore(ticket-loop): close <ticket-id>`。
- 启动运行与启动下一张新工单前，tracked、staged、untracked 工作区都必须干净，且不存在 merge、rebase、cherry-pick 或 revert 状态。
- 恢复当前工单时允许该 session 遗留的未提交改动，但仍需确认仓库、分支、历史和工作区可归属于账本中的 active ticket。
- 启动时必须处于命名分支。运行冻结仓库根、Git common directory、分支和起始 HEAD。外层不创建、切换、拉取、推送或合并分支。
- 允许的 HEAD 变化只能来自当前任务 session 的新增提交，且工单起始 HEAD 必须保持为当前 HEAD 的祖先。分支变化、历史改写或未完成 Git 操作是不可自动恢复的错误。
- 监督 Skill 和 runner 都不得修改业务文件、ticket 内容或创建业务 commit。唯一仓库级例外是 runner 可以维护本地 Git exclude 并写运行账本。
- 运行账本位于仓库内的 `.ticket-loop/`，由 `.git/info/exclude` 本地排除，不修改团队 `.gitignore`。若该路径已有 tracked 内容，则拒绝自动占用。
- 账本使用原子写入，记录 manifest、阶段状态、session ID、Git SHA、尝试次数、退出码、费用和日志路径。原始 Claude JSON保留，文件权限尽量限制为当前用户。
- Git 诊断只持久化文件路径、状态、统计和 SHA，不额外保存完整 diff。
- 同一仓库使用单实例锁。有效锁使新运行拒绝启动；陈旧锁被归档后进入中断恢复流程。
- SIGINT/SIGTERM 先终止 Claude 子进程，经过宽限期后强制结束，随后原子记录 interrupted、释放锁并保留现场。SIGKILL 通过陈旧锁和 running 状态在下一次调用时识别。
- 自动恢复 prompt 只传递可信失败门禁和必要事实，不复制整份日志，并明确要求继续当前工单、不得开始其他工单、完成验证与提交。
- 恢复额度耗尽后进入 terminal-failure，不强制通过。普通 resume 不能越过该状态；显式 reopen 必须重新通过仓库、分支、历史、Git 操作、现场归属和 session 存在性检查。
- abort 只更新账本并释放锁，不清理 Git 或 session。clean 是显式操作，不在成功后自动运行。
- 可配置最大成本；费用在每次下一模型调用前检查。缺失费用字段标记 unknown，不按零处理。
- 所有 manifest 工单完成后，运行仅报告本地成功；不自动 push、创建 PR、切换分支或删除 Claude session。
- 领域术语采用：**Loop Run** 表示一次冻结工单集的完整运行；**Task Ticket** 表示 `/to-tickets` 生成、可由 `/implement` 执行的实施工单；**Wayfinder Ticket** 表示研究、原型、grilling 或规划工单；**Task Session** 表示只处理一张 Task Ticket 的 Claude Code session；**Frontier** 表示所有 blocker 已完成的待执行 Task Tickets；**Success Gate** 表示允许状态推进的硬条件；**Run Ledger** 表示可恢复的持久运行状态。

## Testing Decisions

- 采用一个主要测试接缝：从 `ticket-loop` CLI 的进程边界测试外部行为，而不是以内部函数或类作为主要测试目标。
- 测试通过 subprocess 调用 `plan`、`start`、`step`、`resume`、`reopen`、`status`、`abort` 和 `clean`，观察退出码、stdout JSON、stderr、运行账本、工单文件和 Git 状态。
- 每个测试在临时目录中创建真实的最小 Git 仓库和本地 Markdown 工单，以验证真实 Git 命令语义，而不是 mock Git 实现细节。
- 使用可注入的 fake Claude executable 模拟成功、非零退出、错误 JSON、缺失字段、超时、实现提交、未提交修改、closeout 修改和多轮 resume；测试不得调用真实模型或产生模型费用。
- 好的测试只断言用户和监督 Skill 可观察到的行为：状态转换、门禁结果、允许动作、Git 历史、工单状态、日志与恢复效果，不断言内部函数调用顺序或私有数据结构。
- task/wayfinder 分类测试覆盖加粗和裸字段、字段缺失、重复状态字段、编号与中文路径。
- 依赖测试覆盖 None、单 blocker、多 blocker、自然语言标题后缀、manifest 外 task blocker、manifest 外 wayfinder blocker、缺失 blocker、重复编号、自依赖、环和多个 frontier 的确定性选择。
- Git 测试覆盖 tracked 修改、staged 修改、untracked 文件、ignored 账本、detached HEAD、分支漂移、HEAD 前进、祖先关系破坏以及 merge/rebase/cherry-pick/revert 状态。
- 生命周期测试覆盖 implementation 成功、无 commit、脏工作区、Claude 错误、超时、最多两次 resume、进入 closeout、closeout 补证据、无需额外 commit、closeout 恢复与额度耗尽。
- 完成校验测试覆盖 done 状态格式、全部 checklist、唯一 Completion evidence、有效实现 commit、四类证据、带原因的 Not applicable 和失败关键词门禁。
- 恢复测试覆盖精确 session ID、多个未完成 run、普通 resume 拒绝 terminal-failure、reopen 前置检查、原 session 缺失和中断后的陈旧锁处理。
- 安全与运维测试覆盖 `.git/info/exclude` 的幂等维护、tracked `.ticket-loop/` 冲突、文件权限、原子账本写入、SIGINT/SIGTERM、abort 不改 Git、clean 的显式删除边界和成功后不自动清理。
- 路径测试覆盖中文、空格和绝对路径，确保首条 `/implement @路径` prompt 不被 shell 拆词或转义破坏。
- 成本测试覆盖正常累计、费用 unknown、达到上限前停止和恢复后继续累计。
- 仓库当前没有既有自动化测试框架或同类 CLI runner 测试。实现时采用 Python 标准库测试设施，避免仅为该功能引入第三方测试依赖。
- 只有在 CLI 接缝无法精确制造或观察某个纯算法边界时，才补充少量内部单元测试；CLI 行为测试仍是验收依据。

## Out of Scope

- 修改 `/to-tickets`、`/implement`、`/tdd` 或 `/code-review` 的上游行为。
- 处理远程 GitHub、Linear 等真实 issue tracker；首版只处理本地 Markdown tickets。
- 并行执行多个 Task Ticket 或在多个 worktree 中分发任务。
- 为每张工单自动创建分支、worktree 或 clone。
- 自动 pull、push、创建 PR、合并、rebase、cherry-pick、stash、reset 或改写提交历史。
- 自动替任务 session 提交业务代码或工单收尾变更。
- 在外层重新运行项目任意测试命令，或通过自然语言完全理解测试语义。
- 首版创建临时 worktree，在工单起始 SHA 上重放未知测试命令以建立严格基线。
- 允许测试失败、证据缺失或恢复耗尽的工单低质量通过。
- 自动清理成功或失败运行的账本、日志和 Claude session。
- 自动修复损坏的 Git 仓库、冲突、外部分支漂移或被改写的历史。
- 将 `.ticket-loop/` 提交到版本控制或作为团队共享状态后端。
- 提供 GUI、Web dashboard 或远程运行服务。
- 使用 Claude API 或 Agent SDK替代本机 Claude Code CLI。
- 保证跨 Claude Code 版本的内部 transcript 文件格式兼容；恢复只依赖官方 session CLI 接口。

## Further Notes

- Ralph 提供了“每轮启动新的 CLI 上下文、共享工作区、依赖 Git 与状态文件跨轮推进”的有价值起点。本方案保留 fresh context，但不采用其仅靠输出字符串判完成、忽略 CLI 退出码、固定盲重试、无超时、无 Git 门禁和默认强制跳过权限确认以外的薄弱控制方式。
- 本方案在用户明确要求下采用 Claude Code 的无人值守危险权限模式。由于该模式允许任务 session 自主修改文件和执行命令，运行必须只在用户信任的本地仓库、插件、hooks、MCP 和项目配置中启动；Git 门禁用于保护流程一致性，不构成安全沙箱。
- 参考既有 PPT 多智能体 Loop 后，保留三个关键原则：监督者只调度不改代码；恢复必须精确指向原执行上下文；进程结束或空闲通知不等于任务 PASS。不同之处是本方案绝不提供“低质量强制通过”。
- `/implement` 当前负责实现、测试、review 和提交，但不负责勾选验收项、更新本地 ticket 状态或关闭 tracker 生命周期。因此 closeout 是本方案的必要标准阶段，而不是异常补丁。
- `/implement` 与 `/code-review` 当前存在 review-before-commit 与仅审查已提交 diff 之间的已知张力。Ticket Loop 不改写这两个 Skill，但 Completion evidence 必须如实记录 Review 结果；无法证明 review 完成时不得关闭工单。
- 如果 ticket 提供可选 Validation 命令，runner 可在实现前记录基线。没有事前声明时，首版不盲目运行整个项目测试；如果完成证据仍含失败，原 session 必须提供可复现解释，否则 fail closed。
- 状态机、JSON 协议和账本均应带显式 schema version，为未来增加远程 tracker、并行 worktree 或更严格验证留下兼容演进空间。
