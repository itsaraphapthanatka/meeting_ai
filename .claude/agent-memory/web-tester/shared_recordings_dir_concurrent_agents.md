---
name: shared-recordings-dir-concurrent-agents
description: File-mode storage (recordings/web/) is one shared directory regardless of which port a tester binds — concurrent agent instances on other ports (api-tester 55430, e2e-tester 55431, etc.) write into the same folder.
metadata:
  type: project
---

Local/file-mode storage path (`recordings/web/index.json` + per-meeting files) is computed from the repo root at import time, not from `--port`. During a 2026-09-16 run, starting `./mai web --port 55432` and later finding an unexpected `.wav` file in `recordings/web/` turned out to be written by a concurrent api-tester (port 55430) / e2e-tester (port 55431) run, confirmed via `netstat -ano` + `ps aux` showing three separate python processes started minutes apart.

**Why:** every port in the allocation table in `docs/PROJECT-CONTEXT.md` (55430 api-tester, 55431 e2e-tester, 55432 web-tester, …) shares the same on-disk `recordings/web/` unless a test explicitly patches `store.WEB_DIR`/`INDEX_PATH` (recipe A in PROJECT-CONTEXT). Black-box recipe B does not isolate you from other agents running at the same time.

**How to apply:** before deleting/cleaning up any file found in `recordings/web/`, check `ps aux | grep python` and `netstat -ano | grep LISTEN` for other agent ports first — do not assume a stray file is your own test's leftover. Prefer creating drafts only (they live in an in-memory dict, `jobs._drafts`, and never touch disk until a track is uploaded) over uploading real audio when you just need to exercise routing/validation, so you don't add files another concurrent run has to account for.
