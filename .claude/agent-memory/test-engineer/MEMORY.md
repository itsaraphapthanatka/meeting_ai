# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — BUG-048 worker-result sanitize
- [BUG-048 harness additions and testing recipe](bug-048-worker-result-sanitize.md) — FakeStore needed create/set_translation/job_done etc; pair FakeStore checks with a real LocalCase GET+export check for "breaks reading later" tickets.

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
