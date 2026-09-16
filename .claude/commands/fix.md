---
description: Bug pipeline — triage → fix by the owning dev → review (+ security when relevant) → regression test → re-test
argument-hint: <bug description, ticket path, or QA finding>
---
You are the engineering manager handling: **$ARGUMENTS**

Read `docs/PROJECT-CONTEXT.md` and `docs/LEARNINGS.md`. Use the Agent tool for each role (fall back to `general-purpose` + `cat .claude/agents/<role>.md` when a type is not loaded). Roles available: product-manager, architect, backend-dev, web-dev, test-engineer, code-reviewer, bug-triager, api-tester, e2e-tester, web-tester, security-engineer, devops-engineer.

1. **Triage.** If `$ARGUMENTS` is not already a ticket under `docs/tickets/` or a numbered finding in `docs/qa/`, run `bug-triager` to reproduce and write the ticket. Read it; it names the owner role and file:line.
2. **Decide.** If the fix spans more than one repo or changes a schema, show the plan in Thai and confirm with AskUserQuestion before editing. Otherwise proceed.
3. **Fix.** Run the owning dev with the ticket path, told to fix only that ticket and to report (not fix) other occurrences of the same defect pattern.
4. **Verify.** In parallel: `code-reviewer` on the diff; `security-engineer` when the ticket involves auth, ownership, money, personal data, or secrets; `test-engineer` for the regression test named after the ticket. Blocking findings go back to the dev for one round. Then re-run the single most relevant tester scoped to the area, or re-run the ticket's reproduction yourself.
5. **Close.** Ticket status → `fixed (uncommitted)` with files changed and test name; update `docs/product/BACKLOG.md`; add the lesson to `docs/LEARNINGS.md` if it generalises.
6. **Report** in Thai: root cause, files changed, proving test, other places the pattern was seen, anything the owner must run in production, deferred findings. No commits unless asked.
