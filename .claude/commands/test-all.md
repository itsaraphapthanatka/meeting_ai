---
description: Run the QA team (api-tester, e2e-tester, web-tester) in parallel and aggregate one report
argument-hint: [api|e2e|mobile|web ...]  (default: all available)
---
You are the QA lead. Run the tester subagents for: $ARGUMENTS (empty = all of: api-tester, e2e-tester, web-tester).

1. Launch every selected tester **in parallel** with the Agent tool (one message, multiple calls), each with: "Run your full procedure now. No arguments. Return only your report." Fall back to `general-purpose` adopting the matching `.claude/agents/<role>.md` when a type is not loaded.
2. Wait for all. Do not start fixing anything.
3. Verify the top 3 blocking findings yourself (read the file:line or re-run the request); drop or downgrade what you cannot reproduce.
4. Write one aggregated report in Thai: a table (area · verdict · bugs · security · warnings); "must fix first" across all areas by severity with file:line / METHOD /path and fix; security; warnings; "not tested and why"; then each tester's full report verbatim. Deduplicate findings several testers reported (keep the most precise, note who else saw it). Save it to `docs/qa/QA-REPORT-<YYYY-MM-DD>.md` and update `docs/product/BACKLOG.md` rows for new blockers.
