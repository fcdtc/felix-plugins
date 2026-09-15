---
name: ticket-loop
description: "Preflight and autonomously supervise a local chain of implementation tickets."
disable-model-invocation: true
protocol_version: 1.0
argument-hint: "--issues-dir <path> [--range NN-NN] | --tickets <paths...> [runtime options]"
allowed-tools:
  - Bash
---

# Ticket Loop

Run the bundled supervisor once with the user's arguments:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/supervise.py" $ARGUMENTS
```

The supervisor performs the read-only `plan` preflight, then drives `start` and only the action named by each Runner response's `allowed_actions` until `completed` or a terminal state. Treat schema version `1.0` as mandatory and fail closed on any other version.

Report the final local state, Run ID, active ticket, gate failures, cost status, and ledger/log location. On success, state that commits, Task Sessions, the Run Ledger, and logs remain local. On failure, report only the structured Runner error plus the smallest relevant stderr/log excerpt.

## Supervisor boundary

The supervisor schedules only. It does not edit business files or ticket files, inspect full diffs, run arbitrary project commands, change Git history, commit, push, create pull requests, or clean artifacts. Task Sessions own implementation and closeout commits. Read only Runner JSON, necessary Git status/stat/SHA summaries, and targeted log excerpts.

## Security

Task Sessions run Claude Code with `bypassPermissions` and `--dangerously-skip-permissions` so the chain can execute unattended. This grants autonomous command and file access. Run Ticket Loop only in a trusted local repository with trusted project instructions, plugins, hooks, MCP servers, and dependencies. Git gates protect workflow consistency; they are not a sandbox.

## Runtime options

- `--model <model>` overrides the Claude Code model; omission inherits the user's Claude Code setting.
- `--implement-command <name>` overrides command discovery. Otherwise the Runner detects an installed namespaced `implement` Skill and falls back to `implement`.
- `--implementation-timeout-seconds`, `--closeout-timeout-seconds`, and `--resume-timeout-seconds` override 5400/1800/1800-second defaults.
- `--max-cost-usd <amount>` sets an optional cumulative ceiling. The Runner checks before every new Claude call; missing cost is `unknown` and stops a capped run rather than counting as zero.

Resolved runtime values are frozen in the versioned Manifest. Raw Claude JSON, stderr logs, and the Run Ledger stay under `.ticket-loop/`, are locally excluded from Git, and are created with current-user-only permissions where supported.

## Direct Runner CLI

The standard-library Runner is independently executable:

```bash
RUNNER="${CLAUDE_SKILL_DIR}/scripts/ticket_loop.py"
python3 "$RUNNER" plan --issues-dir .scratch/feature/issues --range 01-07
python3 "$RUNNER" start --tickets .scratch/feature/issues/01-first.md .scratch/feature/issues/02-second.md
python3 "$RUNNER" step --run <run-id>
python3 "$RUNNER" resume --run <run-id>
python3 "$RUNNER" reopen --run <run-id>
python3 "$RUNNER" status --run <run-id>
python3 "$RUNNER" abort --run <run-id>
python3 "$RUNNER" clean --run <run-id>
```

`plan` is read-only. `step` makes at most one Claude call. `resume` continues the exact Task Session after a recoverable gate failure. `reopen` revalidates a terminal/interrupted run before continuing. `status` is read-only. `abort` stops scheduling without changing Git. `clean` explicitly removes only the selected terminal run's ledger and logs; successful runs are never cleaned automatically.
