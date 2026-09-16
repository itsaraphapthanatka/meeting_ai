---
description: Cross-repo status in ten lines
---
Produce a standup in Thai (≤ 12 lines) without launching subagents:

1. For each repo in `docs/PROJECT-CONTEXT.md`, one Bash call: `git -C <repo> status --porcelain | wc -l` and `git -C <repo> log -3 --format='%h %ad %s' --date=short`.
2. Read `docs/product/BACKLOG.md` (top 5 open P0 rows), `ls docs/tickets` (open count), the newest file in `docs/qa/` (date + verdict), and `docs/releases/UNRELEASED.md` if present.
3. Report: one line per repo (uncommitted files, last commit date + subject); then urgent items, open tickets, latest QA, and one suggested next command (`/fix …`, `/feature …`, `/test-all`).
