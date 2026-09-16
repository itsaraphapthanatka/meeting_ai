# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — BUG-010 login rate limit (2 rounds)
- [Testing the two-layer rate limiter + 3 bypasses from round-2 review](bug_010_rate_limit_testing.md) — FakeStore needed a whole auth surface + rate_hit/rate_reset added, or the DB layer silently fails open and tests are vacuous; prove both layers independently, assert call counts not status codes for "no scrypt after block"; round 2 covers forwarded_headers allowlist, _ip_key normalisation, ratelimit._prune self-eviction, session soft/hard bucket split — always `git diff` the app files first when told "these bypassed the suite".

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
