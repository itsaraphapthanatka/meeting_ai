---
name: bug-055-mtime-cache-testing
description: How to write deterministic regression tests for the store.py detail-cache (mtime-keyed) bug, without sleeping or faking clock rollbacks
metadata:
  type: project
---

BUG-055 (`docs/tickets/BUG-055-stale-detail-cache.md`): `store.load_detail()` cached the
parsed meeting keyed on file mtime; 5 write sites only 1 popped the cache. Fix: `_write_detail()`
is the only writer and always pops; `load_detail()` only caches a file quiet for
`_CACHE_MIN_AGE = 2.0s`. Regression suite: `tests/test_bug_055_stale_detail_cache.py`.

**Why:** mtime resolution is much coarser than a tight Python write loop (measured: 300 writes
→ 14 distinct mtimes, 286/300 collide with the prior write). A test that relies on real elapsed
time between two writes will not reproduce the bug; a **tight loop of 100-200 real writes with
no `time.sleep`** reproduces it reliably on its own, because the loop itself is faster than the
filesystem clock's tick.

**How to apply:**
- To simulate "this file has been quiet a while" without actually waiting `_CACHE_MIN_AGE`
  seconds, backdate with `os.utime(path, (old, old))` — legitimate, deterministic, and it is
  what a genuinely old file looks like.
- Never force two writes to share an *identical* mtime via `os.utime` to manufacture a
  same-tick collision "for determinism" — that simulates a backward clock jump, which the
  ticket explicitly calls out as a separate, accepted limitation, not the coarse-quantization
  race this fix addresses. It would test something the code was never meant to handle.
- To prove a cache is still functioning (not gutted to "always re-read" by some future "fix"),
  spy on the read primitive: `mock.patch.object(module, "_read_json", wraps=module._read_json)`
  then assert `spy.assert_not_called()` across N repeated reads of an unchanged, aged file.
  This is the check the ticket explicitly asked for so the fix cannot be silently regressed to
  "delete the cache entirely" (which would also pass every freshness test).
- Proved non-vacuousness by writing a throwaway script (kept in the scratchpad, not the repo)
  that monkeypatches `filestore.load_detail` / `filestore._write_detail` back to the exact
  pre-fix bodies (from `git diff`) and reran the new test module: 7/11 tests flipped to
  failing (all 5 detail-write-path tight loops except the title-only update, the HTTP
  PATCH/GET loop, and the missing-file-pops-cache test); the other 4 (foreign-writer variants,
  cache-still-caches, title-only update) correctly still passed, since they exercise index-only
  writes or scenarios the old code already handled.
- `POST /api/meetings` only creates a draft job (no detail file yet) — for HTTP tests that need
  a real, gettable/patchable meeting, seed it directly with `filestore.create(...)` after
  `LocalCase.setUp()` (which already points `filestore.WEB_DIR` at the test temp dir), then
  drive the loop over HTTP.
