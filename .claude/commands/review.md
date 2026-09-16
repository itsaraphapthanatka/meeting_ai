---
description: Review code with the code-reviewer subagent (default: uncommitted changes)
argument-hint: [commit-range | branch | file paths]
---
Use the **code-reviewer** subagent to review: $ARGUMENTS

If no argument was given, review the current uncommitted changes (staged + unstaged + untracked) in the current repo. Relay the subagent's full report to the user verbatim, in Thai, without summarising it away.
