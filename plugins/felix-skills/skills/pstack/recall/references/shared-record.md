# Shared-record investigation

Use the sibling skill's ordinary [`evidence investigation reference`](../../why/references/investigation.md). This is a declared content dependency, not a skill invocation.

## Question adaptation

Ask for evidence about:

- the target's current state;
- fixes or approaches that were tried but reverted, superseded, or failed to hold;
- symptoms users still report;
- active production errors, incidents, or operational constraints;
- merged, open, reverted, and in-flight changes that affect the target.

Run this investigation concurrently with transcript mining.

## Return to recall

Transform the investigation's evidence packet into these fields without running the `why` user-facing synthesizer:

- **Current state:** verified facts and their citations.
- **Tried before:** attempts, outcomes, and whether they were reverted or superseded.
- **Still reported:** unresolved symptoms, errors, and incidents.
- **Live artifacts:** PRs, tickets, branches, and documents to verify against live state.
- **Coverage and gaps:** every source searched, empty, unavailable, or skipped, with reasons.

Keep confidence wording calibrated to the shared [`epistemics` framework](../../why/references/epistemics.md). The recall workflow folds this packet into its own Capsule / Threads / Problems / Next move output.
