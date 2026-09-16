# test-engineer memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-16 — BUG-011 body size caps (2 รอบ: fix แรก + review-round hardening)
- [raw_request/raw_send_and_collect + กับดักตอนพิสูจน์ไม่ vacuous](bug_011_body_caps.md) — default-param vs global-lookup ตอนแพตช์ค่าคงที่, อย่าคูณลิสต์ด้วยค่าที่แพตช์ให้ใหญ่, ย้อนกลไกทีละจุดไม่ใช่ทั้งฟังก์ชัน, xfail ที่กลับเป็น unexpected-success ต้อง diff โค้ดจริงก่อนแปลงเป็น assert, ไม่ใส่ wall-clock assertion ถาวร

## 2026-09-16 — P0 fix round
- tests/_harness.py is the canonical harness: env before import, FakeStore, CloudCase (five patches + server.Server on port 0) and LocalCase (patch store.WEB_DIR AND INDEX_PATH AND SETTINGS_PATH).
- Prove tests are not vacuous by temporarily reverting each fix in-memory (patch the helper to the old behaviour) and watching the matching test fail — then restore.
- `python -m unittest discover -s tests` puts tests/ on sys.path; absolute `from _harness import ...` works without __init__.py.
- Local mode leaves entries in jobs._jobs/_drafts between tests; use unique ids or clear in tearDown before asserting on counts.
- Never call the LLM, whisper-cli or Docker; patch stt.transcribe / summarizer.summarize / runner.* instead. Postgres-only assertions go behind @skipUnless(MAI_TEST_DATABASE_URL).
