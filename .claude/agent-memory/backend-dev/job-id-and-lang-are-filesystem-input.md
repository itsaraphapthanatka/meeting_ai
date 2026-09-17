---
name: job-id-and-lang-are-filesystem-input
description: In meeting_ai a job id is a filename — user strings that get concatenated into ids (translate lang) must be allow-listed, and the worker API must validate the id before dispatch, not inside one action
metadata:
  type: project
---

Job ids in `web/jobs.py` are built by string concatenation (`f"{meeting_id}.tr.{lang}"`) and are
later used as filenames (`store.WEB_DIR / f"{job_id}.{ext}"` in the worker `audio` action,
`runner.py` mix/bot paths). `store.valid_id()` only ever saw the **meeting id**, so any user
string spliced into an id bypassed it (BUG-044, fixed 2026-09-16 in `server._safe_job_id` +
`lang` allow-list against `summarizer.LANGUAGE_NAMES`).

**Why:** the worker API percent-decodes the id **after** `_route` splits the path on `/`, so
`%2F..%2F` survives into the id. The existence check (`jobs.get(job_id)`) is not a filter — it
only forces the attacker to create the poisoned job first via another endpoint.

**How to apply:**
- Validate the id at the **top of the `jobs` block in `_worker_api`**, right after `unquote`,
  not inside the single action that builds a path — otherwise the 404 "job not found" check runs
  first and the endpoint can never answer 400 for an id that was never created.
- Prefer a full-shape check (`base` through `store.valid_id` + suffix regex) over
  `Path(job_id).name != job_id`: on POSIX `\` is a legal filename char and `\x00` passes `Path()`
  but blows up `open()` with a 500.
- When a user string reaches an identifier, follow it into **every** path and prompt built from
  it (`lang` also reached the LLM prompt in `summarizer.translate`).
- **Tightening an id guard on the write path strands the jobs that already carry a bad id.**
  `jobs.claim()` is not on the HTTP path, so it kept handing poisoned specs to workers while
  `progress`/`result`/`error` all answered 400 — and `worker.py` swallows the error from
  `/error`. Net effect: job stuck `running` -> `pgstore.jobs_reap(30)` requeues -> claimed again
  every 30 min forever, re-sending the meeting summary to the LLM each round. Whenever you make a
  callback endpoint stricter, walk the whole job lifecycle (claim -> progress -> result -> error
  -> reap) and ask "can this job still reach a terminal state?".
- An attempts ceiling only works if something resets it: `pgstore.job_upsert`'s
  `on conflict do update` is the "user submitted this id again" path (summarize/translate reuse
  the meeting id), so it must set `attempts = 0`, while `job_requeue`/`jobs_reap` must not.
- Extra internal fields from `pgstore.job_get` must start with `_` (`_spec`, `_attempts`):
  `jobs.public()` strips exactly those before the dict reaches the browser.
- `re` `$` matches before a trailing newline, so `_ID_RE.match(...)` accepted `"<id>" + newline` in BOTH
  `store.valid_id` and `pgstore.valid_id`. Use `fullmatch` for anything that becomes a filename.
- Residual risk after such a fix: rows already poisoned in the prod `meeting_ai.jobs` table.
  The owner should check `select id from meeting_ai.jobs where id ~ '[/\\]'` — agents never query prod.

See also [[proving-fixes-against-pre-fix-code]].
