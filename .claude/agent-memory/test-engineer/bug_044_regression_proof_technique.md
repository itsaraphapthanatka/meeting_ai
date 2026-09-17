---
name: bug-044-regression-proof-technique
description: Concrete recipe for proving a security-guard regression test is not vacuous, without editing committed source, and what it revealed about Windows path resolution.
metadata:
  type: feedback
---

For BUG-044 (worker `audio` action must reject an unsafe `job_id` *before* the `jobs.get(job_id)
is None` 404 check — the exact ordering is the security property), I proved the test would
actually catch a regression by writing a throwaway script (not committed) that:

1. Imports the test module directly (`sys.path` needs both the project root and `tests/` — the
   project root is NOT added automatically when running a standalone script from elsewhere).
2. Wraps a `mock.patch.object(server, "_safe_job_id", lambda job_id: True)` context around a
   fresh `unittest.TextTestRunner().run(TestSuite([case_cls(method_name)]))` call for just the
   guard tests, and asserts `wasSuccessful() is False`.
3. Does the same for the translate allow-list by patching `summarizer.LANGUAGE_NAMES` to an
   object whose `__contains__` always returns `True`.
4. Re-runs the same tests with no patch active and confirms they pass again.

This caught something concrete: with the guard disabled, the "poisoned job already in queue" test
did not just fail an assertion — it actually **wrote a file** to `WEB_DIR.parent` with HTTP `200`,
proving Windows resolves `..` lexically through a path segment (`<mid>.tr.`) that is never created
as a real directory (POSIX would `ENOENT` there). See the dated bullet added to
`docs/LEARNINGS.md` for the reusable fact; this memory is about the *technique*, not the OS fact.

**Why:** the team's standing practice (see MEMORY.md 2026-09-16 P0 round) is "prove tests are not
vacuous by temporarily reverting the fix in-memory" — this is the concrete pattern for when the
fix is inline logic (not an isolated helper function) and for when you want the flip to survive as
independent evidence outside the permanent test file (kept in scratch, not committed).

**How to apply:** Use this whenever a fix is "guard runs before existing check X" — assert the
ordering explicitly (same 400 whether or not the resource exists), then do a one-off revert-and-run
outside the committed suite to confirm the specific ordering (not just presence of the guard) is
what the test depends on. Always run the cleanup (`addCleanup`) path even under the reverted/buggy
condition to confirm no leftover file survives a failed test.
