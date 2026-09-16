# code-reviewer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-16 — BUG-045 blob storage opt-in
- [Env matrix / .env trap](env-matrix-dotenv-trap.md) — prove env-driven paths with `VAR=`, never `env -u VAR`; `.env` refills it via setdefault.

## 2026-09-16 — P0 fix round
- In this repo, review any new `try/finally` cleanup against every exception path: bot staging `finally` deleted the only copy of meeting audio when shutil.move failed cross-filesystem.
- `bot._run()` returns _Timeout(rc 124) silently; comments saying 'after docker stop finished' are not invariants.
- When a diff adds a read-side twin of an existing write-side check, diff them line by line — _may_write_job still used job['id'] for translate jobs.
- Grep for leaking FIELDS (workers_list joins jobs.title), not just routes, when reviewing authorization fixes.
- Dead parameters/flags (`getattr(args, 'stt')` without a defined --stt) mislead readers; flag them.
- Cheap checks: `python -m compileall -q meeting_ai api bot`, import line, `git diff --check`; `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -v` once tests exist.
