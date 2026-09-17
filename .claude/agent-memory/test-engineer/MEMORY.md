# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — BUG-055 stale detail cache
- [bug_055_mtime_cache_testing.md](bug_055_mtime_cache_testing.md) — tight real write loops (no sleep) reproduce mtime-tick collisions; `os.utime` to backdate is fine, forcing identical mtimes is not; spy on `_read_json` to prove caching still happens.

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
