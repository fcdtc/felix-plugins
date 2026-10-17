# Evidence investigation

Gather evidence about a code target without producing the final user-facing narrative.

Each spawn below names a role line in the pstack settings file (`~/.cursor/rules/pstack-models.mdc` in Cursor, `~/.agents/pstack-models.md` in other harnesses) and a default. Set `model` to that line's value, or to the default if the file or line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the subagent tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message or the harness model list.

## Operating posture

Operate as a careful, cautious, precise investigator. Read [`epistemics.md`](epistemics.md) in full and preserve its distinction between direct, supported, inferred, speculative, and unknown claims.

## 1. Establish the target and code anchor

Parse the target and question. If the target is vague, infer it from conversation context, state the interpretation briefly, and proceed so the user can redirect.

Before spawning investigators, identify:

- relevant file paths and line ranges;
- key symbols;
- recent commits touching the target;
- PR numbers from merge commits;
- linked ticket IDs.

Use `git blame`, `git log --follow -p`, and `git log --oneline`. Pull substantive PR bodies and discussions with `gh pr view`. Pass the resulting anchor to every investigator.

## 2. Discover evidence sources

List MCP servers connected to the harness and map the available capabilities to these evidence categories:

1. Source control history
2. Issue / ticket tracker
3. Long-form documents
4. Real-time team chat
5. Infrastructure observability
6. Error / exception tracking
7. Product analytics warehouse

Source control is available through git; use `gh` when the repository and environment support it. Classify other MCP capabilities from their names, instructions, tools, and resources. One MCP may cover multiple categories. Assign each category its best matching capability, and record when several categories share one MCP or when multiple MCPs could serve the same category. Build a complete coverage map and document null results.

## 3. Run parallel investigators

Launch all matching investigators in one message. Use one investigator per available evidence category, with one chosen tool or MCP capability assigned to it. Never ask one investigator to span categories. The same multi-capability MCP may back separate category investigators when its tools cover each category.

The spawns use Cursor's `Task` tool. In another harness, use its subagent tool: `Agent` in Claude Code (`subagent_type: general-purpose`), `task` in OpenCode (`subagent_type: general`), `spawn_agent` in Codex. Keep the prompt and model. Drop unsupported parameters. If the harness has no subagent tool, run each investigator directly, one after another.

Each investigator uses:

- `subagent_type`: `generalPurpose`
- `model`: the `why investigators` line, default `grok-4.7-xhigh-fast`
- `readonly`: `false`. Agent mode is required because readonly/Ask mode can strip MCP access. Investigators still must not write.

Build each investigator prompt from:

1. [`investigator-prompt.md`](investigator-prompt.md)
2. The matching category playbook indexed by [`source-playbook.md`](source-playbook.md)
3. [`sources/incident-postmortem.md`](sources/incident-postmortem.md) when the target looks defensive
4. The code anchor
5. The investigation question

The roster is:

1. **Source control:** always run. It finds implementation-time rationale in history, PRs, comments, and tests.
2. **Issue / ticket tracker:** product and business forcing functions.
3. **Long-form documents:** design rationale written before code.
4. **Real-time team chat:** deliberation absent from durable documents.
5. **Infrastructure observability:** runtime and infrastructure reality behind defensive code.
6. **Error / exception tracking:** exceptions and error trajectories behind corrective code.
7. **Product analytics warehouse:** product and data reality behind flags, experiments, migrations, and thresholds.

Skip an investigator only when no matching MCP is available or the source is provably irrelevant. Record every skip and reason. A source that returns nothing is still a finding.

## 4. Follow cross-source leads

After the parallel pass, collect every investigator's `Additional Leads`. Route each actionable lead to the investigator for its target category, or run a focused follow-up with that category's assigned tool or MCP. Repeat until no unexamined lead could materially change the answer. Record leads that cannot be followed and why.

## Evidence packet

Return, without a final narrative:

- the code anchor;
- every investigator finding, citation, contradiction, and additional lead;
- every query and source searched;
- null results;
- unavailable or skipped sources with reasons;
- confidence classification under [`epistemics.md`](epistemics.md).
