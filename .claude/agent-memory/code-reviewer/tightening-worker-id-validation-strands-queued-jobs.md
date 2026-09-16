---
name: tightening-worker-id-validation-strands-queued-jobs
description: Adding validation to /api/worker/jobs/{id}/* strands already-queued rows — worker's /result AND /error both 400, reaper requeues forever
metadata:
  type: project
---

`meeting_ai/worker.py` reports failure with `POST /api/worker/jobs/{id}/error` and swallows `WorkerError`
from it (`except WorkerError: pass`). If a new server-side guard 400s on the job id itself, the worker can
neither finish nor fail the job: the row stays `running`, the reaper requeues stale non-bot jobs, the worker
claims it again and re-runs the LLM. Cost loop, not just a stuck job.

**Why:** found reviewing the BUG-044 fix (whole-id regex on `_worker_api`) — rows created while the bug was
open can carry ids the new guard rejects.
**How to apply:** any diff that adds/strengthens validation on an existing worker callback must answer
"what about rows already in the queue?" — ask for a one-off cleanup (mark non-conforming jobs `error`) or
proof that no such row can exist.
