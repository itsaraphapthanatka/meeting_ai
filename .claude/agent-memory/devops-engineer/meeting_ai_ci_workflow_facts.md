---
name: meeting-ai-ci-workflow-facts
description: verified, non-obvious facts behind meeting_ai's .github/workflows/ci.yml (BACKLOG #23) — check these still hold before changing the workflow
metadata:
  type: project
---

Built `.github/workflows/ci.yml` for BACKLOG #23 (ticket: `docs/tickets/BUG-023-ci.md`,
committed-uncommitted on a worktree branch, never yet run on real GitHub Actions). Facts that
took real investigation, not just reading the workflow file:

- The suite's "264 tests, 7 skipped" baseline (no `MAI_TEST_DATABASE_URL`) breaks down as
  **7 = 2 (`TestApplyResultAgainstRealDb`) + 5 (`TestPgstoreJobActiveAgainstRealDb`)** — both
  gated purely on `MAI_TEST_DATABASE_URL`. The BUG-056 lock tests
  (`@unittest.skipUnless(filestore._LOCK_KIND, ...)` in `tests/test_bug_056_file_store_concurrency.py`)
  do **not** skip on either Windows or POSIX in practice — `_LOCK_KIND` is truthy whenever
  `msvcrt` or `fcntl` is importable, which is always true on GitHub's `windows-latest` and
  `ubuntu-latest` images. So a CI matrix across both OS actually **runs** the lock tests through
  their native code path on each OS, not "skips half of them" — this is why a 2-OS matrix job is
  worth having, not just symbolic coverage.
- `meeting_ai/web/backend.py` and `web/db.py` import `psycopg`/`psycopg_pool` lazily, only when
  `MEETING_AI_CLOUD` is truthy **and** `DATABASE_URL` is set (confirmed via subprocess:
  `import meeting_ai.web.backend` then check `"psycopg" in sys.modules` → `False` with those env
  vars unset). `tests/_harness.py` forces `MEETING_AI_CLOUD=0`/`DATABASE_URL=""` at import time,
  so the whole 264-test suite needs **zero** pip installs when no Postgres job is involved —
  `requirements.txt` (`psycopg[binary]==3.3.4`, `psycopg-pool==3.2.6`) only needs installing in
  the Postgres-integration job.
- The two real-Postgres test classes call `db.init()` themselves in `setUpClass`
  (`tests/test_p0_03_jobs_scoped.py`, `tests/test_bug_048_apply_result.py`) — equivalent to
  `./mai db-init`. CI does not need a separate `mai db-init` step; just point
  `MAI_TEST_DATABASE_URL` at an empty Postgres and run the same `unittest discover` command.
- `python -m unittest discover -s tests` works from the repo root with **no** `PYTHONPATH` set —
  verified by running it plain locally. Don't add a PYTHONPATH-setting step to the workflow, it's
  unnecessary and was a false lead.
- `.gitattributes` LF/CRLF rules (`*.sh`/`Dockerfile`/`mai`/`bot/*.py` → LF, `*.ps1`/`*.cmd` →
  CRLF) are respected by a plain `actions/checkout` with no extra `core.autocrlf` config needed —
  git attributes apply regardless of the runner's global git config.
- PyYAML (`yaml.safe_load`) resolves the unquoted `on:` top-level workflow key to the Python
  boolean `True` (YAML 1.1 on/off/yes/no quirk) — this is expected and harmless; GitHub's actual
  workflow parser special-cases the `on` key, and every real GitHub Actions example uses it
  unquoted. Don't "fix" this by quoting `"on":` on sight of that quirk in a validation script;
  it's not a real bug, and quoting it is also valid but is not itself necessary.
- Pinned `actions/checkout` to `v7.0.1` and `actions/setup-python` to `v7.0.0` by full commit SHA,
  looked up live via `gh api repos/<org>/<repo>/git/refs/tags/<tag>` (gh CLI is authenticated in
  this environment — see [[sandbox-bash-limits]] for a related environment note). Don't guess
  SHAs from memory; `gh api` is available and fast for this.
