---
name: regex-dollar-allows-trailing-newline
description: In meeting_ai every id validator uses re.match + `$`, which accepts a trailing newline — check this first on any path/id validation fix
metadata:
  type: project
---

`store.valid_id` / `pgstore.valid_id` are `_ID_RE.match(mid)` with `_ID_RE = ^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$`.
Python's `$` also matches just before a trailing `\n`, so `valid_id("20260916-120000-abcdef\n") is True`
(verified 2026-09-16). Any new validator copied from this pattern (e.g. `_safe_job_id` / `_JOB_SUFFIX_RE`
in `meeting_ai/web/server.py`) inherits the hole.

**Why:** ids become filenames (`WEB_DIR / f"{job_id}.{ext}"`) and `\n` is a legal POSIX filename char, so the
"whole id is safe" claim is not literally true; it only stays harmless because a job row must already exist.
**How to apply:** when reviewing an id/path validation fix, run the validator against `"<valid>\n"` before
approving, and ask for `re.fullmatch` or `\Z` instead of `$`. `\x00` is correctly rejected by both classes.
