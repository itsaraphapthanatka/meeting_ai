# security-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — login rate limit (BUG-010)
- [Rate-limit audit checklist](rate-limit-audit-checklist.md) — six probes that beat a per-IP limiter: reset-on-success, spoofable proxy header, regex-not-parser IP key, dead cleanup, silent fail-open, shared NAT lockout.
- Report: docs/runbooks/security/AUDIT-2026-09-17-login-rate-limit.md (High 2 · Medium 4 · Low 2).

## 2026-09-16 — P0 fix round
- Cloud-mode authz matrix without a DB: in-process server.Server on port 55450 with FakeStore and five patched bindings (backend.cloud/store, jobs.cloud/store, server.store) + config.remote_worker=True. ~45 requests cover the share/owner/admin/stranger grid.
- _share_may_call and _meeting() must parse `parts` identically (both use urllib.parse.unquote(parts[1])) — any divergence creates a parser differential. Tested: %2D, case, trailing slash, %00, two cookies, ?share= → all 401.
- jobs.draft() in cloud returns the spec for done/running jobs too; upload routes needed an explicit status check (audit #4).
- workers_list() joins jobs.title: title leak survived the jobs[] scoping fix until workers were stripped for non-admins.
- pgstore.access() grants owner to every user when meetings.owner_id is null — ask the owner to verify prod has no such rows (BACKLOG #42).
- Report path: docs/runbooks/security/AUDIT-<date>-<scope>.md; latest: AUDIT-2026-09-16-p0-authz.md.
