---
name: implement-continue
description: "Continue implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Continue implement the work described by the user in the spec or tickets.


If the user passes a ticket reference, fetch it from the issue tracker and state its title before starting. If the reference is ambiguous, ask.

当前ticket执行过所以是claimed状态，未最终完成。启动实现前，先核对已有进度、关键阶段结束或交接前写入检查点。


Call the Skill tool with "tdd" where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, call the Skill tool with "code-review" to review the work.

Commit your work to the current branch.
