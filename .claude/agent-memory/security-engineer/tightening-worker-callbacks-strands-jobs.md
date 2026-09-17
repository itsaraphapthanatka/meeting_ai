---
name: tightening-worker-callbacks-strands-jobs
description: Hardening an id on /api/worker/jobs/{id}/* turns an already-injected payload into an unbounded LLM replay loop — always audit the rows already in the queue
metadata:
  type: project
---

When a fix adds validation to a callback the worker already calls, the security question is not only
"is the guard bypassable" but "what happens to rows created while the bug was open".

In meeting_ai: `worker.py` reports failure via `POST /api/worker/jobs/{id}/error` and swallows the
`WorkerError` from it. If the server 400s on the id itself, the job can neither finish nor fail → stays
`running` → `pgstore.jobs_reap(30)` (called from `jobs.claim()` on every poll) requeues it → claimed again →
`runner.translate_job` calls the LLM again. No `attempts` cap anywhere. Verified 2026-09-16 on the BUG-044 fix:
claim returned the poisoned spec (200) while all four report actions returned 400 and the job stayed `running`.

**Why this is a security finding, not just a bug:** the injected `lang` string is still concatenated into the
LLM prompt every cycle, and the meeting summary (PII) is re-sent to the LLM provider forever. Allow-lists added
at the submit route only protect rows created after the patch.

**How to apply:** for any "we now reject X" diff, ask for (a) a guard at the *claim* side that fails such rows
out of the queue, (b) a read-only detection query for existing rows, (c) an attempts cap. Detection SQL used:
`select id,kind,status from meeting_ai.jobs where id !~ '^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(\.[A-Za-z0-9._-]*)?$'`
(Postgres `$` does not have Python's trailing-newline behaviour — see [[id-guard-payload-matrix]]).
