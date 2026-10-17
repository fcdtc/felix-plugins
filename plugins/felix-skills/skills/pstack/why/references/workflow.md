# Why workflow

Investigate the motivation and intent behind code, then produce a confidence-weighted explanation.

## 1. Gather evidence

Read and execute [`investigation.md`](investigation.md) for the user's original question. Its evidence packet is the sole input to synthesis beyond the original question and code anchor.

For a trivial target, answer inline only after confirming all seven available category searches would be redundant and state that explicitly. This should be rare.

## 2. Synthesize

Spawn one synthesizer:

- `subagent_type`: `generalPurpose`
- `model`: the `why synthesizer` line from the pstack settings, default `claude-opus-5-5-xhigh`
- `readonly`: `false`, because citation spot checks may require MCP access. It must not write.

Give it:

1. the complete evidence packet, including null results and justified skips;
2. the code anchor;
3. the original question;
4. [`epistemics.md`](epistemics.md);
5. [`synthesizer-prompt.md`](synthesizer-prompt.md).

## 3. Present

Present the synthesizer output. Light clarity edits are fine, but do not rewrite confidence language.

Use the applicable sections defined in the synthesizer prompt: The Question, The Code in Question, What We Found, What We Can Reasonably Infer, Competing Hypotheses, What We Don't Know, Sources Consulted, Confidence Summary. Keep Sources Consulted as one line per investigator, including empty and skipped sources with reasons.

When the question precedes a code change, turn lineage findings into Preserve / Change / Avoid / Risk constraints suitable for planning.

Avoid recency bias. The current shape may be an accretion of older decisions rather than the intent of the latest commit.
