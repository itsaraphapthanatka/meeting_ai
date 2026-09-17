# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — BUG-010 login rate limit (2 rounds)
- [Testing the two-layer rate limiter + 3 bypasses from round-2 review](bug_010_rate_limit_testing.md) — FakeStore needed a whole auth surface + rate_hit/rate_reset added, or the DB layer silently fails open and tests are vacuous; prove both layers independently, assert call counts not status codes for "no scrypt after block"; round 2 covers forwarded_headers allowlist, _ip_key normalisation, ratelimit._prune self-eviction, session soft/hard bucket split — always `git diff` the app files first when told "these bypassed the suite".
## 2026-09-16 — BUG-011 body size caps (2 รอบ: fix แรก + review-round hardening)
- [raw_request/raw_send_and_collect + กับดักตอนพิสูจน์ไม่ vacuous](bug_011_body_caps.md) — default-param vs global-lookup ตอนแพตช์ค่าคงที่, อย่าคูณลิสต์ด้วยค่าที่แพตช์ให้ใหญ่, ย้อนกลไกทีละจุดไม่ใช่ทั้งฟังก์ชัน, xfail ที่กลับเป็น unexpected-success ต้อง diff โค้ดจริงก่อนแปลงเป็น assert, ไม่ใส่ wall-clock assertion ถาวร
## 2026-09-17 — BUG-055 stale detail cache
- [bug_055_mtime_cache_testing.md](bug_055_mtime_cache_testing.md) — tight real write loops (no sleep) reproduce mtime-tick collisions; `os.utime` to backdate is fine, forcing identical mtimes is not; spy on `_read_json` to prove caching still happens.
## 2026-09-16 — BUG-045 blobstore opt-in
- [blob-storage-env-leak](blob-storage-env-leak.md) — _harness.py only blanks S3_BUCKET; config.py's setdefault .env load leaks real R2 creds into os.environ for the other S3_* vars unless a test overrides all five itself.
- [nonvacuous-proof-via-monkeypatch](nonvacuous-proof-via-monkeypatch.md) — prove regression tests catch reintroduced bugs via a throwaway script that mock.patch.objects the function with the old buggy body; no repo file touched, one stand-in per independent guard.
## 2026-09-16 — BUG-044 (translate lang + worker audio path traversal)
- [Harness header/FakeStore extension](bug_044_worker_auth_header_harness.md) — _do/get/post_json/post_bytes now take extra_headers; FakeStore gained job_done/set_translation.
- [Regression-proof technique](bug_044_regression_proof_technique.md) — revert-in-memory recipe that caught a real Windows lexical-`..` file write; see also docs/LEARNINGS.md.

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
