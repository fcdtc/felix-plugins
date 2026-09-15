# 03: Git 门禁与仓库本地运行隔离

**What to build:** 用户可以确信 Loop 只会在可归属、可审计的 Git 现场中推进。启动和下一张工单前必须完全干净；运行期间能发现分支变化、历史改写及未完成 Git 操作；账本不会污染项目提交；同一仓库不能同时启动两个 Loop Run。

**Blocked by:** 02 / 单工单全自动实现与收尾闭环

**Status:** done (2026-09-15, 147cc95)

- [x] 启动 Run 时必须处于本地命名分支，detached HEAD 被拒绝。
- [x] 启动 Run 和启动下一张新工单前同时检查 tracked、staged 和 untracked 文件，任何已有改动均阻止推进。
- [x] 检测 merge、rebase、cherry-pick 和 revert 等未完成 Git 操作并阻止推进。
- [x] 每次 Claude 调用前后校验仓库、Git common directory 和分支与账本一致。
- [x] 工单起始 HEAD 必须始终是当前 HEAD 的祖先；分支漂移或历史改写进入 terminal-failure。
- [x] Runner 不自动执行 reset、stash、checkout、switch、merge、rebase、pull、push 或历史改写。
- [x] 监督 Skill 与 Runner 均不替任务 Session 修改业务文件、工单文件或创建业务 commit。
- [x] `.ticket-loop/` 被幂等加入 `.git/info/exclude`，不会修改团队 `.gitignore` 或污染工作区。
- [x] `.ticket-loop/` 已包含 tracked 内容时拒绝自动占用该路径。
- [x] 仓库级锁记录 PID、Run ID、启动时间和分支；有效锁阻止第二个 Loop 并发启动。
- [x] Git 门禁的每一种允许与拒绝场景均通过临时真实 Git 仓库的 CLI 行为测试验证。
