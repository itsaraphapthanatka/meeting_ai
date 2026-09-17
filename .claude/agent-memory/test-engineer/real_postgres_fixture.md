---
name: real-postgres-fixture
description: How to wire a real-DB unittest class for pgstore against MAI_TEST_DATABASE_URL without touching production code
metadata:
  type: project
---

Task (2026-09-17): replaced a vacuous placeholder (`skipTest` unconditionally, never actually
gated on DB presence in practice) in `tests/test_p0_03_jobs_scoped.py::TestPgstoreJobActiveAgainstRealDb`
with a real fixture against a throwaway Postgres at `postgresql://postgres:itest@127.0.0.1:55440/maitest`
(read from `MAI_TEST_DATABASE_URL`, never hardcoded/committed).

Recipe that worked:
- `setUpClass`: `os.environ["DATABASE_URL"] = <the test URL>` then `db.close()` (drop any pool
  cached under a different URL earlier in the process) then `db.init()` (== `./mai db-init`,
  applies `web/schema.sql`, idempotent). Assert the expected table names came back — this is
  also how you'd notice schema.sql broke on a real Postgres (it didn't, as of PG16.15).
- `setUp`: `TRUNCATE meeting_ai.jobs, meeting_ai.shares, meeting_ai.sessions,
  meeting_ai.meetings, meeting_ai.users RESTART IDENTITY CASCADE` before seeding. Chose this
  over a dedicated schema (queries hardcode `meeting_ai.` — see [[pgstore-sql-conventions]])
  or a rolled-back transaction (`pgstore.*` each open their own autocommit connection from the
  pool; forcing one shared connection would need patching `db.connect()`, risking masking real
  pooler/autocommit bugs).
- `tearDownClass`: `db.close()` — good hygiene, this function is otherwise never called in
  the app (see BACKLOG "never called" list).
- Drive `pgstore.job_active()` directly with real rows from `pgstore.ensure_user` /
  `pgstore.create` / `pgstore.job_upsert` — no HTTP layer, no FakeStore. That is what makes it
  an actual SQL test rather than a scoping-logic test (the FakeStore-based `TestJobsScopedCloud`
  in the same file already covers the HTTP/permission layer).
- Proved not vacuous by monkeypatching `pgstore.job_active` itself (not the mock-connection
  unit test) back to unscoped in a standalone script run with `PYTHONPATH=.` from the worktree
  root against the real DB, and watching 3 of 5 new tests turn red.

Gotcha: running a helper script from outside `tests/` needs `PYTHONPATH=.` (worktree root) to
import `meeting_ai`; running it via `python -m unittest discover -s tests` does not need this
because discover adds `tests/` to `sys.path`, but `meeting_ai` package resolution there works
because the process cwd is also the worktree root when you invoke `python -m unittest discover`
from there — a bare `python /abs/path/script.py` does NOT add the cwd, only the script's own dir.
