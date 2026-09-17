# Security audit — BUG-044 fix (translate `lang` + worker job id) — 2026-09-16

**Scope / method:** ตรวจ diff ที่ยังไม่ commit ใน `meeting_ai/web/server.py` (`_safe_job_id` + `_JOB_SUFFIX_RE` + allow-list ของ `lang`) เทียบกับ [BUG-044](../../tickets/BUG-044-translate-lang-worker-path-write.md) และ [QA-REPORT-2026-09-16 BLOCKER 1](../../qa/QA-REPORT-2026-09-16.md)
วิธี: (1) อ่านโค้ดทุกเส้นที่ `job_id` / `lang` ไหลไปถึงไฟล์หรือ prompt (`server.py`, `jobs.py`, `runner.py`, `worker.py`, `summarizer.py`, `store.py`, `pgstore.py`, `bot.py`, `app.js`) (2) probe จริงแบบ in-process บน `tests/_harness.py` (`LocalCase`, WEB_DIR = temp dir, `WORKER_TOKEN` ปลอมของตัวเอง, ไม่แตะ `recordings/web/` ไม่แตะ production, ไม่เรียก LLM จริง) — 25 เคส unit ของ `_safe_job_id` + 48 request ต่อ worker API + 36 payload ของ `lang` (3) รันชุดทดสอบเดิม 64 tests
**Verdict:** chain ที่รายงานไว้ **ปิดแล้ว** — ไม่พบทางลอดผ่าน `_safe_job_id` และไม่มีไฟล์ใดถูกเขียนใน/เหนือ `WEB_DIR` เลย แต่การแก้ยัง**ไม่ครอบ "งานที่ถูกวางยาไว้ก่อนแพตช์"** และตัว regex มีจุดอ่อนที่ควรปิดเชิงป้องกัน

**Summary:** Critical 0 · High 0 · Medium 2 · Low 3
หมายเหตุ: code-reviewer ที่ทำงานคู่ขนานในรอบเดียวกันสรุปตรงกันในสองข้อแรก (ดู `.claude/agent-memory/code-reviewer/`) — รายงานนี้เพิ่มหลักฐานที่ยิงจริง ขอบเขตที่กว้างกว่า และคำสั่งตรวจ/ล้างของค้าง

---

## Findings (by severity)

### 1. [Medium] งาน translate ที่ id ถูกวางยาไว้ก่อนแพตช์ จะค้างถาวรและวนเรียก LLM ซ้ำไม่รู้จบ (พร้อม prompt injection ที่ยังทำงานอยู่)
**เหตุผลของระดับ:** ไม่ได้เปิดช่องใหม่ให้ผู้โจมตี แต่ทำให้ payload ที่ฝังไว้แล้ว "ทำงานซ้ำตลอดไป" + เผาโควตา LLM + ส่งสรุปการประชุม (PII) ออกไปนอกระบบทุกรอบ โดยไม่มีเพดาน — **ยกระดับเป็น High ทันทีถ้าคำสั่งตรวจด้านล่างเจอแถวจริงในฐานข้อมูล production**

**Location**
- การ์ดใหม่: [server.py:895-896](../../../meeting_ai/web/server.py#L895) — 400 ทุก action ของ `/api/worker/jobs/{id}/…`
- แต่ `claim` ไม่ถูกการ์ด: [server.py:858-886](../../../meeting_ai/web/server.py#L858) → [jobs.py:389-396](../../../meeting_ai/web/jobs.py#L389) (`claim()` เรียก `store.jobs_reap(30)` ทุกครั้งแล้วคว้างานถัดไปโดยไม่ดู id)
- worker กลืน error: [worker.py:346-357](../../../meeting_ai/worker.py#L346) — `POST …/result` พังแล้วไป `POST …/error` ซึ่งพังอีก แล้ว `except WorkerError: pass`
- reaper คืนงานเข้าคิวไม่จำกัดจำนวนครั้ง: [pgstore.py:877-883](../../../meeting_ai/web/pgstore.py#L877) (ไม่มีเพดาน `attempts`)
- prompt ที่ยังถูกวางยา: [runner.py:359-365](../../../meeting_ai/runner.py#L359) → [summarizer.py:288](../../../meeting_ai/summarizer.py#L288) `LANGUAGE_NAMES.get(target_lang, target_lang)`

**Proof** (ยิงจริง, in-process, job id ที่วางยาถูกใส่เข้าคิวโดยตรงเพื่อจำลองแถวที่ค้างจากก่อนแพตช์)
```
POST /api/worker/claim                         -> 200
  {"id":"<mid>.tr./../../pwned","kind":"translate","lang":"/../../pwned","summary":"สรุปตัวอย่าง"}
POST /api/worker/jobs/<id เข้ารหัส>/progress   -> 400 {"error":"job id ไม่ถูกต้อง"}
POST /api/worker/jobs/<id เข้ารหัส>/result     -> 400 {"error":"job id ไม่ถูกต้อง"}
POST /api/worker/jobs/<id เข้ารหัส>/error      -> 400 {"error":"job id ไม่ถูกต้อง"}
POST /api/worker/jobs/<id เข้ารหัส>/requeue    -> 400 {"error":"job id ไม่ถูกต้อง"}
job status หลัง worker ยอมแพ้                  -> "running"   (ไม่ done ไม่ error)
```
วงจรที่ตามมาในโหมด cloud: `running` ไม่ถูกอัปเดต → ครบ 30 นาที `jobs_reap` เปลี่ยนเป็น `queued` → worker `claim` ใหม่ → `runner.translate_job` เรียก `summarizer.translate(summary, "/../../pwned")` อีกครั้ง → รายงานผลไม่ได้ → วนซ้ำ **ไม่มีที่สิ้นสุด**

**Impact (ภาษาผลิตภัณฑ์):** เจ้าของการประชุมเห็นงาน "กำลังแปล" ค้างตลอดกาล กดยกเลิกได้แต่ไม่มีทางสำเร็จ; เจ้าของระบบจ่ายค่า LLM ทุก 30 นาทีต่อหนึ่งแถวเสีย; สรุปการประชุม (ข้อมูลส่วนบุคคล) ถูกส่งไปผู้ให้บริการ LLM ซ้ำไม่จำกัดครั้ง; และข้อความที่ผู้โจมตีใส่ไว้ใน `lang` ยังถูกต่อเข้า prompt ทุกรอบ (allow-list ใหม่กันเฉพาะงานที่สร้าง**หลัง**แพตช์ ไม่ย้อนไปแตะแถวเก่า)
ก่อนแพตช์ งานแบบนี้ถ้า id ไม่มี `/` จะรายงานผลสำเร็จแล้วจบไป — แพตช์นี้จึงเปลี่ยน "งานเสียที่จบได้" ให้กลายเป็น "งานเสียที่จบไม่ได้"

**Fix (dev ทำได้ทันที)**
1. ที่ `jobs.claim()` — ใช้รูปแบบเดิมที่มีอยู่แล้วใน loop เดียวกัน ([jobs.py:401-404](../../../meeting_ai/web/jobs.py#L401) ที่ `build_spec` คืน None แล้วเรียก `fail()` + `continue`): ถ้า id ไม่ผ่านการ์ด ให้ `fail(job["id"], "job id ไม่ปลอดภัย — ยกเลิกโดยระบบ (BUG-044)")` แล้ว `continue` → งานหลุดจากคิวถาวร ไม่วนกลับ และ worker ไม่มีทางเห็น spec ที่วางยา (ย้ายตรรกะ `_safe_job_id` ไปไว้ใน `jobs.py` หรือ `store` เพื่อไม่ให้ `jobs.py` ต้อง import `server`)
2. one-off cleanup ก่อน deploy — ตรวจก่อนว่ามีจริงไหม:
   ```sql
   -- ตรวจ (read-only)
   select id, kind, status, created_at from meeting_ai.jobs
   where id !~ '^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(\.[A-Za-z0-9._-]*)?$';
   -- ถ้าเจอ: ปิดงานทิ้ง
   update meeting_ai.jobs set status='error', step='ผิดพลาด',
          error='job id ไม่ปลอดภัย — ยกเลิกโดยระบบ (BUG-044)'
   where status in ('queued','running')
     and id !~ '^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(\.[A-Za-z0-9._-]*)?$';
   -- คีย์ขยะที่หลุดลง translations แล้ว
   select id, k from meeting_ai.meetings m, jsonb_object_keys(m.translations) k
   where k not in ('th','en','ja','zh','ko');
   ```
3. ใส่เพดาน `attempts` ใน `job_claim`/`jobs_reap` (เช่น > 5 → `error`) เพื่อกันวงจรซ้ำทุกชนิด ไม่ใช่เฉพาะเคสนี้
**Owner:** backend-dev (ข้อ 1, 3) · เจ้าของระบบ/ops (ข้อ 2 — ต้องรันกับ production DB ซึ่ง agent ห้ามแตะ)

---

### 2. [Medium] `_safe_job_id` และ `store.valid_id` ใช้ `re.match` + `$` จึงรับ newline ท้ายสตริง
**เหตุผลของระดับ:** ตอนนี้ยังใช้โจมตีไม่ได้ (ต้องมี job อยู่จริงก่อนถึงจะเขียนไฟล์) แต่นี่คือ **การ์ดกลางเรื่อง path traversal ของทั้งแอป** — คำกล่าวอ้างในคอมเมนต์ว่า "ฐานต้องผ่าน valid_id เสมอ" จึงไม่จริงตามตัวอักษร

**Location** [server.py:63](../../../meeting_ai/web/server.py#L63) `_JOB_SUFFIX_RE = re.compile(r"^[A-Za-z0-9._-]*$")` และ [server.py:73](../../../meeting_ai/web/server.py#L73) `_safe_job_id`; ต้นตอเดิม [store.py:30](../../../meeting_ai/web/store.py#L30) / [pgstore.py:29](../../../meeting_ai/web/pgstore.py#L29) `_ID_RE` + `.match()`

**Proof** (unit จริง)
```
_safe_job_id("20260916-162821-8f9c0a\n")        -> True    # newline ท้าย base
_safe_job_id("20260916-162821-8f9c0a\n.tr.en")  -> True    # newline กลาง id
_safe_job_id("20260916-162821-8f9c0a.tr.en\n")  -> True    # newline ท้าย suffix
_safe_job_id("20260916-162821-8f9c0a.tr.en\nX") -> False   # ($ ตรงกับก่อน \n ตัวท้ายเท่านั้น)
_safe_job_id("20260916-162821-8f9c0a\x00")      -> False   # NUL ถูกปฏิเสธถูกต้อง
```
**Impact:** `\n` เป็นอักขระที่ถูกกฎหมายในชื่อไฟล์บน POSIX — ถ้าวันหนึ่งมีเส้นทางที่สร้างไฟล์จาก id โดยไม่เช็คว่า job มีอยู่จริงก่อน (หรือมีใครก็อป regex นี้ไปใช้ที่อื่น) จะได้ชื่อไฟล์/คีย์ที่ระบบมองไม่เห็นและ log ที่แทรกบรรทัดได้ ตอนนี้กันไว้ด้วย `jobs.get(job_id) is None → 404` ที่ [server.py:906](../../../meeting_ai/web/server.py#L906) เท่านั้น ซึ่งเป็นการกันโดยบังเอิญ ไม่ใช่โดยการ์ด
**Fix:** เปลี่ยนเป็น `re.fullmatch` (หรือ `\Z` แทน `$`) ทั้งใน `_JOB_SUFFIX_RE`, `store._ID_RE` และ `pgstore._ID_RE` — แก้ 3 บรรทัด ไม่กระทบ id ที่ถูกต้อง (ยืนยันแล้วว่า 64 tests ยังผ่าน)
**Owner:** backend-dev

---

### 3. [Low] `apply_result` เชื่อ `lang` ที่ worker ส่งกลับมา ไม่ใช่ `lang` ใน spec
**Location** [jobs.py:358](../../../meeting_ai/web/jobs.py#L358) `store.set_translation(meeting_id, result["lang"], result["text"])`
**Proof (code trace):** `_worker_api` action `result` ส่ง body ดิบเข้า `jobs.apply_result` โดยไม่เทียบกับ `job["_lang"]` → ผู้ถือ `WORKER_TOKEN` ใส่คีย์อะไรก็ได้ลง `meetings.translations` ได้แม้ allow-list ใหม่จะปิดฝั่งผู้ใช้แล้ว
**Impact:** ต่ำ — ต้องมี worker token (ถือว่าเป็น trusted principal) และคีย์ถูกเก็บเป็น jsonb key/dict key เท่านั้น ไม่เคยกลายเป็น path (ยืนยัน: [pgstore.py:381](../../../meeting_ai/web/pgstore.py#L381) ใช้ `jsonb_build_object(%s::text,%s::text)` แบบ parameterized; [store.py:195](../../../meeting_ai/web/store.py#L195) เป็น dict assignment) และหน้าเว็บไม่เคย render คีย์เหล่านี้ (ดู Verified safe)
**Fix:** ใน `apply_result` ให้ใช้ `lang` จาก job spec เป็นหลัก: `lang = job.get("_lang") or (job.get("_spec") or {}).get("lang")` แล้วปฏิเสธถ้า `lang not in summarizer.LANGUAGE_NAMES`
**Owner:** backend-dev

---

### 4. [Low] suffix ของ job id ไม่มีเพดานความยาว
**Location** [server.py:63](../../../meeting_ai/web/server.py#L63) — `[A-Za-z0-9._-]*` ไม่จำกัดจำนวน
**Proof:** `_safe_job_id("<mid>." + "a"*5000)` → `True` (ผ่านการ์ด) แล้วไปตกที่ `jobs.get()` → 404
**Impact:** ต่ำมาก — ถูกจำกัดโดยอ้อมด้วยเพดาน request line 65536 ไบต์ของ `http.server` และ id ที่ถูกต้องยาวสุดแค่ `<mid>.tr.xx` ผลที่เหลือคือ query/log ขยะ
**Fix:** `if len(job_id) > 48: return False` (หรือรัดเป็น `^(tr\.(th|en|ja|zh|ko))?$` ไปเลย เพราะระบบสร้าง suffix แค่รูปแบบเดียว)
**Owner:** backend-dev

---

### 5. [Low] regression test ที่มาระหว่างรอบตรวจ ยังไม่ครอบสองเคสที่รายงานนี้เจอ
**Location** `tests/test_bug_044_translate_lang_worker_path_write.py` (302 บรรทัด) ถูกเพิ่มเข้ามาโดย test-engineer **ระหว่าง** audit นี้ — ตรวจซ้ำแล้ว: ครอบทั้ง chain จริง ทั้งโหมด cloud และ local รวมถึงเคส "งานเป็นพิษอยู่ในคิวแล้ว" · `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests` → **84 tests OK, skipped 1**
**สิ่งที่ยังขาด** (จากการเทียบกับ payload ที่รายงานนี้ยิงจริง)
- action ที่ทดสอบกับ id เป็นพิษมีแต่ `audio` — ยังไม่มี `progress`/`result`/`error`/`requeue`/`tracks` ทั้งที่การ์ดตัวเดียวกันคุมทั้งหมด ถ้าใครย้ายการ์ดไปไว้ในบล็อก `audio` ในอนาคต เทสต์ชุดนี้จะยังเขียว
- ไม่มีเคส newline ของ finding 2 (`_safe_job_id("<mid>" + chr(10))` → `True`) และไม่มีเคสการวนคิวของ finding 1
**Fix:** เติม subTest วน 6 action, เคส `chr(10)`, และเทสต์ที่ยืนยันว่า `claim` ไม่คืนงานที่ id ไม่ผ่าน `_safe_job_id`
**Owner:** test-engineer

---

## Verified safe (ทดสอบแล้ว ไม่ใช่การอนุมาน)

| สิ่งที่ตรวจ | หลักฐาน |
|---|---|
| ลอด `_safe_job_id` ด้วย encoding ทุกแบบ | 48 request (8 id × 6 action) `%2F`, `%2e%2e%2f`, **double encoding `%252F`**, `%5C` (backslash), `%00`, `C%3A%2F`, id ที่ไม่มี base เลย → **400 ทุกเคส** ทั้ง `progress`/`audio`/`result`/`error`/`requeue`/`tracks` |
| ไม่มีไฟล์หลุดออกนอกโฟลเดอร์ข้อมูล | diff ของ `WEB_DIR` และไดเรกทอรีแม่ ก่อน/หลัง 48 request → `[]` และ `[]` |
| การ์ดอยู่ก่อนทุกเส้นจริง | [server.py:891-896](../../../meeting_ai/web/server.py#L891) อยู่หลัง `unquote` ทันที และก่อนทั้ง `tracks` (ซึ่งอยู่ก่อนการเช็คว่างานมีจริง), `progress`, `audio`, `result`, `error`, `requeue` — `rest[1]` ไม่ถูกใช้ที่อื่นในบล็อกนี้เลย |
| `.`/`..` เป็น suffix ทั้งก้อน | `<mid>.`, `<mid>..`, `<mid>...` ผ่านการ์ด แต่ base ถูกบังคับเป็น meeting id เสมอ และ suffix ไม่มี `/` `\` ได้ → `WEB_DIR / "<mid>....wav"` เป็นชื่อไฟล์ธรรมดา ออกนอกโฟลเดอร์ไม่ได้ |
| unicode / homoglyph / fullwidth | `ｅｎ`, `é` (combining), `еn` (cyrillic) → ไม่ผ่านทั้งการ์ดและ allow-list (`[A-Za-z0-9]` เป็นช่วง codepoint ตรง ๆ ไม่ใช่ `\w` จึงไม่มีปัญหา unicode normalization) |
| allow-list ของ `lang` | 36 payload: `/../../../pwned`, `..`, `%2F..%2F`, `EN`/`En` (case), `e n`, `en\x00`, `en;rm -rf /`, ประโยค prompt injection, สตริงยาว 5000, `th-TH`, `en-US` → **400 ทุกเคส ไม่มี job ถูกสร้าง**; `th/en/ja/zh/ko` → 202 และ job id เป็น `<mid>.tr.<lang>` เท่านั้น |
| type confusion ของ `lang` | `5`, `["en"]`, `{"a":1}`, `true`, `null` → 400 (`str(...)` + `.strip()` ทำให้กลายเป็นสตริงที่ไม่อยู่ใน allow-list) |
| ช่องว่าง/newline รอบ `lang` | `"en "`, `" en"`, `"en\n"`, `"\nen"` → 202 แต่ `.strip()` ทำงาน**ก่อน**เช็ค allow-list และค่าที่ใช้ต่อคือค่าที่ strip แล้ว → job id สะอาด (`<mid>.tr.en`) ไม่ใช่ช่องโหว่ |
| ทางเข้าอื่นสู่ `summarizer.translate` | มีผู้เรียกเดียว [runner.py:365](../../../meeting_ai/runner.py#L365) ← `spec["lang"]` ← `jobs.build_spec` ← `_lang`/`_spec["lang"]` ← `submit_translate` ← `_translate` เท่านั้น (`grep -rn "translate("` ทั้ง repo) — ไม่มี CLI หรือ route อื่น |
| **runner.py:216 / :291 (คำถามของ dev)** | ทั้งสองบรรทัดอยู่ใน `transcribe_job`/`bot_job` ซึ่งรันเฉพาะ `kind in ("process","bot")` และ id ของสองชนิดนี้มาจาก `store.new_id()` เสมอ ([jobs.py:101](../../../meeting_ai/web/jobs.py#L101), [jobs.py:139](../../../meeting_ai/web/jobs.py#L139)) ไม่มีเส้นทางใดให้ผู้ใช้กำหนด id ของงาน process/bot ได้ · งาน translate/summarize ไม่เคยแตะสองบรรทัดนี้ (`_execute` → `runner.HANDLERS[kind]` ที่ [jobs.py:513](../../../meeting_ai/web/jobs.py#L513), worker ที่ [worker.py:192](../../../meeting_ai/worker.py#L192)) → **ความเสี่ยงคงเหลือจากงานที่ถูกวางยาก่อนแพตช์ที่ปลายทางนี้ = ไม่มี** เหลือแค่เป็น defence in depth (แนะนำใส่ `assert store.valid_id(spec["id"])` หรือใช้ `Path(...).name` กันอนาคต) |
| stored XSS จากคีย์ `translations` ขยะ | [app.js:1252](../../../meeting_ai/web/static/app.js#L1252) อ่านด้วย index เท่านั้น และ [app.js:1266-1273](../../../meeting_ai/web/static/app.js#L1266) วนจาก `state.config.languages` (ไม่ใช่คีย์ใน meeting) + `esc()` ทุกจุด → คีย์ขยะไม่ถูก render |
| allow-list กับ UI ไม่มีวันเบี่ยงกัน | ทั้งคู่ใช้ `summarizer.LANGUAGE_NAMES` — [server.py:511](../../../meeting_ai/web/server.py#L511) ส่งตัวเดียวกันให้หน้าเว็บ |
| ลำดับสิทธิ์ยังถูกต้อง | `_translate` ถูกเรียกหลังบล็อกสิทธิ์ของ `_meeting` ([server.py:1023-1036](../../../meeting_ai/web/server.py#L1023)) → 400 ของ allow-list ไม่ได้กลายเป็น oracle บอกว่ามี meeting นี้อยู่หรือไม่ |
| เส้นทางเขียนไฟล์อื่นที่สร้างจากอินพุต URL | `_put_track`/`_track_upload_url` บังคับชื่อแทร็กด้วย `TRACK_NAMES` ([server.py:674](../../../meeting_ai/web/server.py#L674), [712](../../../meeting_ai/web/server.py#L712)) และ mid ผ่าน `valid_id`; `bot._job_slot` กรองด้วย `re.sub(r"[^A-Za-z0-9_.-]","",job_id)[:40]` ([bot.py:202](../../../meeting_ai/bot.py#L202)); `blobstore` local ใช้ `Path(name).name` ([pgstore.py:449](../../../meeting_ai/web/pgstore.py#L449)) |
| ไม่มี functional regression | `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests` → **64 tests OK, skipped 1** (ตอนเริ่ม audit) และ **84 tests OK, skipped 1** หลัง test-engineer เพิ่มชุด BUG-044 เข้ามาระหว่างรอบ; job id ที่ถูกต้องทุกรูปแบบ (`<mid>`, `<mid>.tr.<lang>`) ผ่านการ์ด |

## Not checked and why

| ส่วน | เหตุผล |
|---|---|
| cloud mode กับ Postgres จริง | ไม่มี DB บนเครื่องนี้ · `_safe_job_id` ไม่ขึ้นกับ backend (ใช้ `store.valid_id` ซึ่งเหมือนกันทั้งสอง store แบบตัวต่อตัว) จึงเทียบเคียงได้ แต่พฤติกรรม reaper/claim วนซ้ำในข้อ 1 **ยืนยันจากโค้ด SQL ไม่ได้ยิงจริง** |
| ฐานข้อมูล production มีแถวที่ id เสียจริงหรือไม่ | ห้ามแตะ production — ให้เจ้าของรันคำสั่งตรวจ (read-only) ในข้อ 1 แล้วแจ้งผล นี่คือตัวชี้ว่าข้อ 1 เป็น Medium หรือ High |
| พฤติกรรมโค้ด**ก่อน**แพตช์ | ไม่ revert/stash อะไรทั้งสิ้น (read-only) — อ้างอิง PoC ของ QA ที่ยืนยันมาแล้ว 2 ครั้ง |
| LLM จริง / prompt injection ได้ผลแค่ไหนกับโมเดล | ห้ามเรียก third-party จากการทดสอบ ประเมินจากเส้นทางข้อมูลเท่านั้น |
| BLOCKER 2 (`_body_json` ไม่จำกัดขนาด), HIGH 4 (presigned URL ของ R2 production), MEDIUM 5/6 | อยู่นอกขอบเขตของ diff นี้ — **ยังเปิดอยู่ทั้งหมด** โดยเฉพาะข้อเสนอให้หมุนกุญแจ R2 ที่ยังไม่มีใครทำ |

## Key rotation runbook
ไม่มีความลับรั่วจาก diff นี้ (ไม่มีค่า secret ใน patch, ไม่มี token/คีย์ถูกพิมพ์ในการทดสอบ — ใช้ `WORKER_TOKEN` ปลอมของ audit เอง)
ข้อเสนอเดิมที่ยังค้าง: หมุน `S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` ของ R2 ตาม QA-REPORT HIGH 4 — ยังไม่ได้ทำ

## ความมั่นใจ
สูงสำหรับข้อสรุป "chain ของ BUG-044 ปิดแล้ว ไม่มีทางลอด" (ยิงจริง 48 request + ตรวจ filesystem จริง)
ปานกลางสำหรับข้อ 1 ในบริบท production (เส้นทางวนซ้ำยืนยันจากโค้ดและจำลองในโหมดไฟล์ ยังไม่ได้ยิงกับ Postgres จริง และไม่ทราบว่ามีแถวเสียใน production หรือไม่)
