---
name: bug-044-worker-auth-header-harness
description: How to extend tests/_harness.py's HTTP mixin for worker-token-authenticated requests and FakeStore job/translation completion, used for BUG-044 and reusable for any future worker API test.
metadata:
  type: project
---

`tests/_harness.py`'s `_HttpCaseMixin._do`/`get`/`post_json`/`post_bytes` did not support custom
headers (only `Cookie` and `Content-Type`), so nothing could exercise `/api/worker/...` routes
(`Authorization: Bearer <WORKER_TOKEN>`). Added an optional `extra_headers: dict|None` param
threaded through all four methods (backward compatible, default `None`). Also added
`FakeStore.job_done()` and `FakeStore.set_translation()` — needed to let a CloudCase test drive a
translate job all the way through `POST /api/worker/jobs/{id}/result` and assert
`meeting["translations"][lang]` actually got written (`apply_result()` in `jobs.py` calls both
unconditionally for translate/summarize jobs, cloud or not).

Patch `config.worker_token` per test via `mock.patch.object(server.config, "worker_token", "...")`
in `setUp` — `config` is a module-level singleton (`Config()` instance) imported by name into
`server.py`, so patching the attribute on either reference affects both.

**Why:** BUG-044 (translate `lang` allow-list + worker `audio` job-id guard) needed HTTP-level
proof that the worker endpoints reject malicious ids and still accept legit ones — impossible
without an authenticated request path in the harness.

**How to apply:** Reuse `extra_headers=` for any future worker-API test instead of re-inventing a
second HTTP client. If FakeStore is missing a pgstore method a new test needs, add it there (small,
in-memory, matches call signature) rather than hitting real Postgres or skipping the cloud side.
See also [[bug-044-regression-proof-technique]].
