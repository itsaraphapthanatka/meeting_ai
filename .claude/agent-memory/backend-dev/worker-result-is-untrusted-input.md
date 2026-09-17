---
name: worker-result-is-untrusted-input
description: jobs.apply_result is an untrusted input boundary (worker POST) as well as the local pipeline's write path — check both sides before changing it
metadata:
  type: project
---

`meeting_ai/web/jobs.py:apply_result()` is reached from two places: the in-process runner
(trusted) and `POST /api/worker/jobs/{id}/result` (whoever holds `WORKER_TOKEN`). Anything it
reads out of `result` is attacker-controlled in cloud mode, and it is the ONLY write path for a
new meeting — breaking it means no meeting can be created at all.

**Why:** BUG-048 (2026-09-17). `store.set_translation(mid, result["lang"], result["text"])` let a
worker pick the key in a user's `translations` map, and the `process`/`bot` branch wrote
`result["segments"]` straight into `store.create()` with no validation, so `NaN`/`Infinity`
timestamps bricked a meeting permanently: measured in file mode, `GET /api/meetings/<id>` →
500 `cannot convert float NaN to integer` (from `store.fmt_time()`'s `int(sec)`) and every
export → 500 (`web/exports.py:26` `int(round(sec*1000))`).

**How to apply:**
- Field the server already chose (translate `lang`) → read it from the job spec, never from the
  result body. In file mode it is `job["_lang"]`; in cloud mode the pgstore job row has **no**
  `_lang` key at all — it lives in `job["_spec"]["lang"]`. Support both or you break cloud.
- Shape rules live in `meeting_ai/web/sanitize.py` (stdlib only, imports nothing from the
  project). `jobs.py` cannot import `server.py`: `server.py` does `from . import backend,
  exports, jobs` at module level, so the reverse import is circular.
- Policy differs on purpose: the user path (`server._clean_segments`, PATCH) rejects the whole
  payload → 400; the worker path salvages usable segments, drops the rest and appends a Thai
  `warning`, because rejecting costs the owner a whole re-transcription.
- Before touching this function, prove a NORMAL result still lands complete (segments verbatim,
  speakers, duration, language, summary, audio name, all five export formats) — that regression
  would be far worse than the bug being fixed.
