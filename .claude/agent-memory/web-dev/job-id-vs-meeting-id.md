---
name: job-id-vs-meeting-id
description: How to detect "a job is running for meeting X" from the front end without relying on job.meeting_id
metadata:
  type: project
---

`state.jobs` items from `GET /api/jobs` do **not** reliably carry `meeting_id` while a job is
`queued`/`running` in local (file) mode — `meeting_ai/web/jobs.py`'s `_enqueue()` sets
`"meeting_id": None` at creation and only fills it in `done()`. Cloud mode (`pgstore`) fills it
immediately, so the two deployment modes disagree on this field mid-flight.

The reliable, mode-agnostic signal is the job's own **id**:
- `process` and `summarize` jobs are enqueued with `job_id == meeting_id` (see `_enqueue(mid, …)`
  in `submit_process`/`submit_summarize`).
- `translate` jobs use `job_id == f"{meeting_id}.tr.{lang}"` (`submit_translate`).

So to check "is a job actively running for the currently-open meeting", match on `job.id`, e.g.:
```js
state.jobs.find(j => (j.status === 'running' || j.status === 'queued')
  && (j.id === meetingId || j.id.startsWith(`${meetingId}.tr.`)));
```
This is what `jobForMeeting()`/`updateStreamingIndicator()` in `app.js` do (added for the AI
"streaming" badge next to the summary section header, driven by the existing 1.5s `pollJobs()`
poll — no new transport).

Before reusing this pattern: re-verify against `meeting_ai/web/jobs.py` in case job-id conventions
change (e.g. a future `bot` or `resummarize` kind with a different id scheme).

See also [[apple-red-contrast]] for another spec-vs-code verification gotcha from the same task.
