# security-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-16 — BUG-044 fix audit (translate lang + worker job id)
- [ID guard payload matrix](id-guard-payload-matrix.md) — what to throw at a path/id validator here; `re.match` + `$` accepts a trailing newline.
- [Tightening worker callbacks strands jobs](tightening-worker-callbacks-strands-jobs.md) — new validation on `/api/worker/jobs/{id}/*` = queued rows loop forever and replay the LLM.
- Report: docs/runbooks/security/AUDIT-2026-09-16-bug044-translate-lang.md (Critical 0 / High 0 / Medium 2 / Low 3).
- Probe recipe that worked: `tests/_harness.py` LocalCase + `os.environ["WORKER_TOKEN"]` set BEFORE importing the harness, then raw `http.client` with an `Authorization` header (the harness helpers gained `extra_headers` mid-session). Diff WEB_DIR *and its parent* before/after — that is the only real proof of "no file written outside".

## 2026-09-16 — P0 fix round
- Cloud-mode authz matrix without a DB: in-process server.Server on port 55450 with FakeStore and five patched bindings (backend.cloud/store, jobs.cloud/store, server.store) + config.remote_worker=True. ~45 requests cover the share/owner/admin/stranger grid.
- _share_may_call and _meeting() must parse `parts` identically (both use urllib.parse.unquote(parts[1])) — any divergence creates a parser differential. Tested: %2D, case, trailing slash, %00, two cookies, ?share= → all 401.
- jobs.draft() in cloud returns the spec for done/running jobs too; upload routes needed an explicit status check (audit #4).
- workers_list() joins jobs.title: title leak survived the jobs[] scoping fix until workers were stripped for non-admins.
- pgstore.access() grants owner to every user when meetings.owner_id is null — ask the owner to verify prod has no such rows (BACKLOG #42).
- Report path: docs/runbooks/security/AUDIT-<date>-<scope>.md; latest: AUDIT-2026-09-16-p0-authz.md.
