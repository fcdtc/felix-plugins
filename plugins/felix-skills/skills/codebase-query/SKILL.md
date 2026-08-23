---
name: codebase-query
description: Answer questions about how a codebase works.
disable-model-invocation: true
---

# Codebase Query

Answer the user's question about the codebase with evidence. Read-only.

## Process

1. Restate what the question is really asking; if it is ambiguous, ask one clarifying question before digging.
2. Locate the smallest scope that can answer it — the files, symbols, and docs involved. Read repo instructions, Glossary, or ADRs relevant to the question first.
3. Gather just enough evidence: code, callers, tests, and targeted history only when current evidence cannot explain a consequential design choice.
4. Answer directly at the user's level, citing focused file references. Mark each claim as verified evidence, inference, or unknown.
5. Stop. Wait for the next question — produce no tour, no overview, no unrequested context.

Use focused file references, not a search dump. Do not edit, diagnose, review, or refactor unless the user switches workflows. If evidence reveals a likely bug or architecture friction, note it in one sentence and recommend `diagnose-issue` or `improve-architecture` as a separate next step.
