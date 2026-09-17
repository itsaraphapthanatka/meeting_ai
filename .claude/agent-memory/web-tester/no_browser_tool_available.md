---
name: no-browser-tool-available
description: The web-tester role file promises a "built-in browser pane" but the actual tool list handed to this agent is Bash/Read/Write/Grep/Glob only — no browser/computer-use tool.
metadata:
  type: project
---

On 2026-09-16 the web-tester agent was invoked with only Bash, Read, Write, Grep, Glob tools, despite `.claude/agents/web-tester.md` instructing to "use the built-in browser pane" for drag-drop, VU meter, recording-mode cards, mobile viewport, click-to-seek, etc.

**Why:** whatever harness launches this agent did not attach a browser/computer-use tool in this session (or any session so far). There is no error — the checks just have to be done a different way.

**How to apply:** when no browser tool is present, do NOT silently skip the interactive/visual checks or pretend to have viewed them. Instead: (1) verify the same behaviour via static code review (grep the exact DOM/JS logic that implements the requirement, e.g. search for viewport/media-query logic, feature-detection gates, hidden attributes) and live `curl` against the API where possible; (2) explicitly report which checks were code-reviewed vs. actually rendered, and mark true visual-only checks (does it *look* right, does horizontal scroll actually occur, does the mic permission prompt actually appear) as "not testable here" alongside the mic-permission case the role file already calls out. Re-check at the start of each session whether a browser tool has been attached before assuming it is still missing — this may change.
