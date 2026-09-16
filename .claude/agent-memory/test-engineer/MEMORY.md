# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-16 — BUG-045 blobstore opt-in
- [blob-storage-env-leak](blob-storage-env-leak.md) — _harness.py only blanks S3_BUCKET; config.py's setdefault .env load leaks real R2 creds into os.environ for the other S3_* vars unless a test overrides all five itself.
- [nonvacuous-proof-via-monkeypatch](nonvacuous-proof-via-monkeypatch.md) — prove regression tests catch reintroduced bugs via a throwaway script that mock.patch.objects the function with the old buggy body; no repo file touched, one stand-in per independent guard.

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
