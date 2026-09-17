# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — BUG-012 invite/first-admin TOCTOU
- [Deterministic race reproduction without sleeps](bug-012-toctou-fake-store-sync.md) — Barrier hook in the store's unconditional last pre-check method (`has_password`), not `time.sleep`.
- [Per-statement locks in FakeStore atomic methods](fake-store-atomicity-per-statement.md) — one lock per method body only, or concurrency tests pass for the wrong reason.
- `tests/_harness.py::FakeStore.count_users()` now counts `self.users` (real accounts), not `len(self.sessions)`; `add_user()` (used by CloudCase seeding) populates both so old tests keep first_run=False.
- Added `tests/_harness.py::AuthCase` — cloud-mode case that skips `CloudCase._seed()` so tests control `count_users()==0` exactly, needed for first-admin race tests.

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
