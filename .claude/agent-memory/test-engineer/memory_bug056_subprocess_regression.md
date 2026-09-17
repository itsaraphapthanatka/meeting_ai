---
name: bug056-subprocess-regression-pattern
description: How to write real-subprocess regression tests for cross-process file locking bugs (BUG-056), reusable for any future process-local-lock bug
metadata:
  type: project
---

BUG-056 (file store lost update): store.py's read-modify-write was guarded only by
`threading.RLock` (process-local) — two real OS processes (`mai web` + `mai
process`/`mai bot`) could both read `index.json`, both append, both write back, and
the earlier writer's already-"saved" record silently vanished from disk. Fixed by
adding a cross-process file lock (`store._guard()` = RLock + msvcrt/fcntl) plus a
pid-suffixed tmp filename and `os.replace` retry (Windows sharing violation from a
concurrent reader, ~83% failure rate measured in the ticket).

**Why:** A bug like this is invisible to thread-based tests and to any harness that
mocks the store (FakeStore, CloudCase) — the whole 64-test P0 suite passed 64/64
while `store.create()` was raising `AttributeError` on every single call (wrong
`msvcrt.LOCK_NBLCK` constant instead of `LK_NBLCK`) because no test exercised the
real file-mode write path at all. Regression tests for this class of bug **must**
spawn real `subprocess.Popen` children (not threads) that import the module fresh
and hit real files, synchronized on a file-based barrier (touch a "ready" marker
per child, main process waits for N markers then creates the barrier file so all
children start together — otherwise races serialize on process-launch order and
the bug won't reproduce reliably).

**How to apply:** For any "two processes both did X" bug: write a small standalone
worker script (not matching `test*.py` so unittest discover skips it) that
patches the module's globals the same way `tests/_harness.py` patches LocalCase
(`WEB_DIR`/`INDEX_PATH`/`SETTINGS_PATH` are computed once at import — patching one
is not enough), runs its operation, and writes its result to a JSON file per
worker (not stdout — avoids interleaving garbage across children). Verify
non-vacuousness by temporarily monkeypatching the guard function to a null
contextmanager (in the worker script *and* in-process for thread/nested tests
that live in the main test process) and confirming most of the new tests fail —
then revert before running the real suite. See
`meeting_ai/.claude/worktrees/*/tests/test_bug_056_file_store_concurrency.py` and
its `tests/_bug056_worker.py` helper for the full pattern (barrier, dead-lock-holder
via `proc.kill()` + `proc.wait()`, lock-busy-timeout via a small patched
`LOCK_TIMEOUT` + mocked `_warn`).

For a "lock holder dies" test: don't assert wall-clock durations as the pass/fail
signal (flaky, and the ticket explicitly asked not to) — instead assert the
functional side-effect. A dead lock holder that truly released the OS lock means
the next writer never hits the busy-timeout branch, so mock `store._warn` and
assert it was **not** called. A live holder that outlives a small patched
`LOCK_TIMEOUT` should hit that branch, so assert `_warn` **was** called with the
expected message. Both avoid timing assertions entirely while still being
specific about which code path ran.
