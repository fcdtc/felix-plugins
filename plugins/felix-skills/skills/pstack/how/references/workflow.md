# How workflow

Explore the codebase to answer how something works. Produce an architectural explanation at the level of a senior engineer onboarding onto a subsystem: enough to build a working mental model, not annotated source code.

Each spawn below names a role line in the pstack settings file (`~/.cursor/rules/pstack-models.mdc` in Cursor, `~/.agents/pstack-models.md` in other harnesses) and a default. Set `model` to that line's value, or to the default if the file or line is missing. Leave `model` unset when the value is `auto` or `inherit-parent`. If the subagent tool rejects a slug, use the default and say so. If it rejects the default, use the closest valid slug of the same family from its error message or the harness model list.

## 1. Assess complexity

If the scope is ambiguous, state the interpretation and explore. The user can redirect.

- **Simple:** a single module, small utility, or narrow question. Skip explorers. One explainer explores and explains in one pass.
- **Complex:** a subsystem spanning multiple files or services, a cross-cutting feature, or a full architectural overview. Run parallel explorers before the explainer.

When in doubt, take the simple path.

The spawns use Cursor's `Task` tool. In another harness, use its subagent tool: `Agent` in Claude Code (`subagent_type: general-purpose`), `task` in OpenCode (`subagent_type: general`), `spawn_agent` in Codex. Keep the prompt and model. Drop unsupported parameters. If the harness has no subagent tool, run each role directly, one after another.

## 2a. Explore complex questions

Decompose the question into 2 to 4 distinct exploration angles. Spawn all explorers in one message:

- `subagent_type`: `generalPurpose`
- `model`: the `how explorer` line, default `grok-4.7-xhigh-fast`
- `readonly`: `true`

Build each prompt from [`explorer-prompt.md`](explorer-prompt.md), filling in its angle.

## 2b. Explain simple questions directly

Spawn one subagent that explores and explains in one pass:

- `subagent_type`: `generalPurpose`
- `model`: the `how explainer` line, default `claude-opus-5-5-xhigh`
- `readonly`: `true`

Build its prompt from [`explainer-prompt.md`](explainer-prompt.md), but adapt the explorer-specific framing for a direct pass: tell this subagent to inspect the code itself before explaining, and omit the explorer-findings section. Do not claim that explorers already ran.

## 3. Synthesize complex questions

After every explorer returns, spawn one explainer:

- `subagent_type`: `generalPurpose`
- `model`: the `how explainer` line, default `claude-opus-5-5-xhigh`
- `readonly`: `true`

Build its prompt from [`explainer-prompt.md`](explainer-prompt.md), filling in all explorer findings.

## 4. Present

Present the explainer output. Light edits for clarity or conversational context are fine. Do not substantially rewrite it.

Use the applicable sections defined in the explainer prompt: Overview, Key Concepts, How It Works, Where Things Live, Gotchas.