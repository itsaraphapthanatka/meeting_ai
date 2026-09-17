---
name: bug-048-worker-result-sanitize
description: How to test jobs.apply_result()/sanitize.py (worker-result trust boundary) — harness additions needed and where the real breakage shows up
metadata:
  type: project
---

BUG-048 fixed `jobs.apply_result()` trusting worker-supplied `lang`/`segments`/`duration`/
`language`/`speakers`. Regression tests: `tests/test_bug_048_sanitize.py` (pure, table-driven,
36 cases) + `tests/test_bug_048_apply_result.py` (16 cases: LocalCase HTTP, CloudCase FakeStore,
optional real-Postgres via `MAI_TEST_DATABASE_URL`).

**Why:** the ticket's central claim — "hostile segments break `GET`/export permanently" — only
reproduces through the *real* file store (`LocalCase`), because `store.fmt_time()`/`exports.py`
are what actually crash on NaN/Infinity. `FakeStore` never calls those, so a FakeStore-only test
proves sanitize.py *ran* but not that the fix prevents the 500. Always pair a FakeStore assertion
(fast, no I/O) with at least one LocalCase assertion that hits the real GET + all 5 export formats
whenever a ticket's exploit path is "value X breaks reading/exporting the meeting later".

**How to apply:** `tests/_harness.py`'s `FakeStore` did not implement `create`/`set_translation`/
`set_summary`/`job_done`/`job_fail`/`job_progress` (only P0 permission routes were exercised
before). Added all six as an additive, backward-compatible harness change — needed any time a
test drives `jobs.apply_result()` through `CloudCase`. Also added an optional `headers` param to
`_HttpCaseMixin._do`/`post_json` plus `post_worker_json(path, body, token)`, since
`/api/worker/...` uses `Authorization: Bearer <WORKER_TOKEN>`, not a cookie — patch
`meeting_ai.config.config.worker_token` (the shared singleton instance) in `setUp`, it is read at
call time by `_worker_authed()`, not cached.

Real-Postgres test recipe that worked: patch `os.environ["DATABASE_URL"]` via
`mock.patch.dict`, call `meeting_ai.web.db.init()` (idempotent `create table if not exists`),
patch `jobs.store`/`jobs.cloud` to `pgstore`/`True`, call `jobs.apply_result()` directly (skip
the HTTP/auth layer entirely — not needed to prove the store layer). `pgstore.job_get()` really
has no top-level `_lang` key (confirmed against a live DB), only `_spec['lang']` — this is the
exact cloud-only defect the ticket described, not a FakeStore assumption.

See also [[proving-tests-non-vacuous]].
