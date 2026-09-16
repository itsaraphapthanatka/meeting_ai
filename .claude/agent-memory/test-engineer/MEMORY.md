# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-16 — BUG-044 (translate lang + worker audio path traversal)
- [Harness header/FakeStore extension](bug_044_worker_auth_header_harness.md) — _do/get/post_json/post_bytes now take extra_headers; FakeStore gained job_done/set_translation.
- [Regression-proof technique](bug_044_regression_proof_technique.md) — revert-in-memory recipe that caught a real Windows lexical-`..` file write; see also docs/LEARNINGS.md.

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
