# BUG-044 — Arbitrary file write ผ่าน translate `lang` + worker audio endpoint

- **Severity:** P0 (BACKLOG #44 — chain ของ #13 + #14)
- **Owner:** backend-dev (verify: security-engineer, test-engineer)
- **Status:** **fixed (uncommitted)** 2026-09-16
- **Source:** [docs/qa/QA-REPORT-2026-09-16.md](../qa/QA-REPORT-2026-09-16.md) BLOCKER 1 · พบโดย api-tester + e2e-tester · PoC ยืนยันอิสระ 2 ครั้ง (e2e-tester, QA lead)

## อาการ
ผู้ใช้ที่เรียก `translate` ได้ (ผู้ใช้ที่ล็อกอินคนใดก็ได้ หรือผู้ถือลิงก์แชร์แบบแก้ไขได้) ร่วมกับผู้ที่ถือ `WORKER_TOKEN` สามารถเขียนไฟล์ลงตำแหน่งใดก็ได้ที่สิทธิ์ของ process เอื้อมถึง — นอก `recordings/web/`

## Root cause (chain 2 จุด — ต้องแก้ทั้งคู่)

1. **`lang` ไม่ถูกตรวจสอบ** — [meeting_ai/web/server.py:1129-1140](../../meeting_ai/web/server.py#L1129) `_translate()` เช็คแค่ว่า `lang` ไม่ว่าง แล้วส่งต่อทันที
2. `lang` ถูกนำไปประกอบเป็น job id แบบสตริง — [meeting_ai/web/jobs.py:244](../../meeting_ai/web/jobs.py#L244) `submit_translate()` ทำ `f"{meeting_id}.tr.{lang}"` (`meeting_id` ผ่าน `valid_id` แล้ว แต่ `lang` ไม่ผ่าน)
3. **worker audio endpoint ไม่ validate `job_id`** — [meeting_ai/web/server.py:877](../../meeting_ai/web/server.py#L877) ทำ `urllib.parse.unquote(job_id)` **หลัง** `_api()` split path ด้วย `/` แล้ว ทำให้ `%2F..%2F` รอดมาเป็น `../` จากนั้น [server.py:905](../../meeting_ai/web/server.py#L905) สร้าง `dest = store.WEB_DIR / f"{job_id}.{ext}"` โดยทั้งบล็อก worker API **ไม่เรียก `store.valid_id()` เลยสักที่**

## Reproduction (ยืนยันแล้ว)
```
POST /api/meetings/<mid>/translate      {"lang": "/../../../pwned"}
  -> 202   job id = "<mid>.tr./../../../pwned"
POST /api/worker/jobs/<mid>.tr.%2F..%2F..%2F..%2Fpwned/audio?ext=wav
  Authorization: Bearer <WORKER_TOKEN>
  -> 200   ไฟล์ถูกเขียน 2 ชั้นเหนือ WEB_DIR
```
หมายเหตุสำคัญ: ยิง `%2e%2e%2f` เข้า URL ตรง ๆ **โดยไม่ผ่าน translate ได้ 404 `ไม่พบงานนี้`** เพราะ endpoint เช็คว่า job มีอยู่จริงก่อน → ต้องใช้บั๊กข้อ 1 สร้าง job id ก่อนเสมอ
ถ้า `lang` เป็น absolute path (`C:/...`) `pathlib` จะทิ้ง base ทั้งก้อน → เขียนได้ทุกที่ (ไม่ได้ยิงจริง)

## ผลข้างเคียงที่ต้องแก้ไปด้วย
`lang` ขยะไหลเข้า LLM prompt ตรง ๆ ที่ [meeting_ai/summarizer.py:288](../../meeting_ai/summarizer.py#L288) (`LANGUAGE_NAMES.get(target_lang, target_lang)`) = **prompt injection** และคีย์ขยะถูกเก็บลง `translations` จริง (`translation_keys=['../../pwn']`)

## สิ่งที่ต้องทำ
- (ก) `server.py` `_translate()` — จำกัด `lang` ด้วย allow-list `LANGUAGE_NAMES` keys ตอบ 400 พร้อมข้อความไทยถ้าไม่ผ่าน
- (ข) `server.py` worker `audio` action — ก่อนสร้าง `dest` ต้องปฏิเสธ job id ที่ไม่ปลอดภัย: `Path(job_id).name != job_id` → 400 และตรวจ base ด้วย `store.valid_id()`
- อย่าแก้แค่จุดเดียว — จุด (ข) คือ defence in depth สำหรับ job id ทุกรูปแบบ ไม่ใช่เฉพาะที่มาจาก translate

## Acceptance criteria
1. `POST /api/meetings/<mid>/translate` ด้วย `lang` ที่ไม่อยู่ใน allow-list → **400** และ**ไม่มี job ถูกสร้าง**
2. `lang` ที่ถูกต้อง (`th/en/ja/zh/ko`) ยังทำงานเหมือนเดิม job id = `<mid>.tr.<lang>` และ `translations.<lang>` ถูกบันทึก
3. `POST /api/worker/jobs/<id ที่มี path separator>/audio` → **400** ไม่ว่าจะ encode มาแบบใด
4. ไม่มีไฟล์ใดถูกเขียนนอก `store.WEB_DIR`
5. regression test ครอบ **ทั้ง chain** (ไม่ใช่แค่จุดใดจุดหนึ่ง) และชุดทดสอบเดิม 64 tests ยังผ่าน


---

## Resolution — fixed (uncommitted) 2026-09-16

ผ่าน review 1 รอบ (code-reviewer + security-engineer ขนานกัน) และ regression test จาก test-engineer

**ไฟล์ที่แก้ (4 ไฟล์, +68/-7 โค้ด production):**
- `meeting_ai/web/jobs.py` — ย้ายตัวกรองมาอยู่ชั้นที่ประกอบ id เอง: `safe_job_id()` (ใช้ `re.fullmatch`), `MAX_ATTEMPTS = 5`, การ์ดใน `claim()` ทั้งเส้น cloud และ file mode → id ไม่ปลอดภัยหรือ claim เกินเพดาน = `fail()` + `continue`
- `meeting_ai/web/server.py` — `_safe_job_id = jobs.safe_job_id`, guard ใน `_worker_api()` ก่อน dispatch ทุก action, allow-list `lang` จาก `summarizer.LANGUAGE_NAMES` ใน `_translate()`
- `meeting_ai/web/store.py`, `meeting_ai/web/pgstore.py` — `valid_id()` เปลี่ยนเป็น `re.fullmatch` (เดิม `re.match` + `$` ยอมรับ newline ปิดท้าย)
- `meeting_ai/web/pgstore.py` — `job_get` คืน `_attempts`; `job_upsert ... on conflict do update` รีเซ็ต `attempts = 0` (สั่งใหม่ = รอบใหม่) ส่วน requeue/reap ไม่รีเซ็ต

**Regression test:** `tests/test_bug_044_translate_lang_worker_path_write.py` (20 tests, ครอบทั้ง `CloudCase` และ `LocalCase`) · ชุดเต็ม **84 tests OK, skipped 1** (เดิม 64)
test-engineer พิสูจน์ว่าเทสไม่ vacuous ด้วยการ monkeypatch `_safe_job_id` กลับเป็น always-True แล้วเห็นเทสล้มเป็นการเขียนไฟล์จริง

**ยืนยันโดย QA lead:** PoC เดิมที่เคยเจาะสำเร็จ ตอนนี้ได้ 400 ทั้งสองจุด (`translate` → `"lang ต้องเป็นหนึ่งใน th, en, ja, zh, ko"`, worker audio → 400) ไม่มีไฟล์หลุดออกนอก `WEB_DIR` และ happy path `lang=en` ยังได้ 202 พร้อม job id `<mid>.tr.en`

**สิ่งที่เจอเพิ่มระหว่าง review (แก้ไปแล้วในรอบนี้):**
- แพตช์รอบแรกสร้าง regression ของตัวเอง — งานที่ id ถูกวางยาไว้ก่อนแพตช์จะ claim ได้แต่ `result`/`error` โดน 400 และ `worker.py:352-356` กลืน error ทิ้ง → ค้าง `running` → `jobs_reap(30)` requeue → **เรียก LLM ซ้ำทุก 30 นาทีไม่มีเพดาน** ปิดด้วยการ์ดที่ `claim()` + `MAX_ATTEMPTS`
- dev เจอเองตอน self-review: `job_upsert` ไม่เคยรีเซ็ต `attempts` และ job id ของ summarize/translate คงที่ตลอดอายุการประชุม → เพดานจะไปฆ่างานปกติตอนสั่งซ้ำครั้งที่ 6

**เจ้าของต้องรันบน production (read-only):**
```bash
python scripts/audit_job_ids.py
```
`psql` ไม่ได้ติดตั้งบนเครื่องเจ้าของ สคริปต์นี้ใช้ psycopg ที่มีอยู่แล้ว อ่าน `DATABASE_URL` จาก `.env` เอง (ไม่พิมพ์ค่าออกมา) และกรองด้วย `jobs.safe_job_id` ตัวจริงของแอป จึงจับได้ทั้ง newline/NUL ไม่ใช่แค่ `/` กับ `\`
แถวที่เจอตอนนี้จะถูก claim ครั้งสุดท้ายแล้วกลายเป็น `error` เอง ไม่วนคิวอีก — ไม่มี DDL ใหม่ (`attempts` มีในสคีมาอยู่แล้ว)

**ยกไปตั๋วใหม่ (BACKLOG #48):** `jobs.py:382` `store.set_translation(meeting_id, result["lang"], ...)` ใช้ `lang` ที่ worker ส่งกลับมาแทนค่าจาก spec — คนละ trust boundary (คนร้ายคือผู้ถือ `WORKER_TOKEN`) และการแก้ที่ถูกต้องต้องเปลี่ยน contract ระหว่าง `worker.py` กับ `apply_result` จึงต้องมีเทสของตัวเอง
