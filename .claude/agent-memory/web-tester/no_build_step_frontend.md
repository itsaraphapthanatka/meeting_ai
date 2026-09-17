---
name: no-build-step-frontend
description: meeting_ai's web front-end (meeting_ai/web/static/) is vanilla JS with no package.json, no bundler, no eslint/tsconfig config — lint/typecheck/build checks are N/A, not FAIL.
metadata:
  type: project
---

Confirmed 2026-09-16: `find . -maxdepth 2 -iname package.json` and `find . -maxdepth 3 -iname "*.eslintrc*" -o -iname "tsconfig*"` both return nothing. `app.js`/`index.html`/`style.css`/`sw.js` are served as-is by `server.py` static handler; there is no npm install, no build artifact directory to check.

**Why:** the checklist in `.claude/agents/web-tester.md` (lint/typecheck/build/render/API-contract) assumes a typical JS toolchain. This repo deliberately has none for the front-end (matches `PROJECT-CONTEXT.md`'s "no pip dependency for the front-end either" / stdlib-only rule).

**How to apply:** report lint/typecheck/build as N/A (not a failure, not skipped) with the one-line reason ("no build step — vanilla JS served as static files, verified no package.json/eslint/tsconfig present"), then spend the time budget on render check + API contract + code-level correctness review instead. Re-verify with the same `find` commands each run in case a build step is added later.
