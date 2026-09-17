# BUG-048 — `apply_result` เชื่อฟิลด์ที่ worker ส่งกลับมาโดยไม่ตรวจ

- **Severity:** P1 (BACKLOG #48) — integrity ของคลังการประชุม
- **Owner:** backend-dev
- **Status:** fixed (uncommitted) 2026-09-17
- **Scope:** `POST /api/worker/jobs/{id}/result` → [meeting_ai/web/jobs.py](../../meeting_ai/web/jobs.py) `apply_result()`

## โมเดลความเชื่อใจ
`WORKER_TOKEN` ทำให้เครื่อง worker "เป็นคนถอดเสียงให้" ไม่ได้แปลว่าเป็น **เจ้าของข้อมูล**
worker ที่โดนยึด (หรือ worker ที่บั๊ก/เวอร์ชันเก่า/ส่งผลของงานผิดงาน) ต้องเขียนได้แค่ผลของงานที่
เซิร์ฟเวอร์สั่งไปเท่านั้น ฟิลด์ที่ "เซิร์ฟเวอร์เลือกไว้แล้ว" ต้องอ่านจาก spec ไม่ใช่จาก body

## อาการ 1 — `lang` ของงานแปลมาจาก body ของ worker
[jobs.py:358](../../meeting_ai/web/jobs.py#L358)

```python
store.set_translation(meeting_id, result["lang"], result["text"])
```

`lang` ถูกเลือกตั้งแต่ `submit_translate()` แล้ว (`spec["lang"]`, `job["_lang"]`) และ `build_spec()`
ก็ส่งค่านั้นไปให้ worker อยู่แล้ว การอ่านกลับจาก body แปลว่าใครถือ `WORKER_TOKEN` ยัด key อะไรก็ได้
ลง `translations` ของการประชุมคนอื่น (เช่น `"th"` ทับของจริง, key ยาวเป็น MB, key ที่หน้าเว็บไม่รู้จัก)

`result["lang"]` / `result["text"]` แบบ subscript ตรง ๆ ยัง raise `KeyError` ถ้า worker ไม่ส่งมา
→ job error เก็บข้อความว่า `'lang'` ซึ่งอ่านไม่รู้เรื่องทั้งกับเจ้าของและกับคนดูแล worker

## อาการ 2 (คลาสเดียวกัน แต่หนักกว่า) — เส้นทาง worker ไม่ผ่านการตรวจ segments เลย
`_clean_segments()` ใน `server.py` คือด่านตรวจของ segments ที่ "ผู้ใช้แก้เอง" (PATCH) และ BACKLOG #11
กำลังเพิ่ม `math.isfinite` + เพดานจำนวน/ความยาวไว้ที่นั่น เพราะ `NaN`/`Infinity` ที่หลุดลงการประชุม
ทำให้ **`GET /api/meetings/<id>` และ export ทุกฟอร์แมตตอบ 500 ถาวร** — วัดจริงในโหมดไฟล์
วันนี้ (2026-09-17): `GET` → `500 {"error": "cannot convert float NaN to integer"}` จาก
`store.fmt_time()` ที่ทำ `int(sec)` ส่วน export พังที่ `web/exports.py:26` `int(round(sec * 1000))`
(`int(nan)` = ValueError, `int(inf)` = OverflowError)

`apply_result()` ไม่ได้ผ่านด่านนั้น — สาขา `process`/`bot` ส่ง `result.get("segments")` เข้า
`store.create()` ตรง ๆ ผลคือ worker ทำให้การประชุมพังแบบเดียวกับที่ผู้ใช้เคยทำได้ก่อน #11 และพังแบบ
**ถาวร** เพราะเจ้าของเปิดการประชุมไม่ขึ้นจึงแก้ผ่าน UI ไม่ได้ ฟิลด์ `speakers`, `duration`,
`language` ก็มาจาก body โดยไม่ตรวจชนิดเช่นกัน (`duration` = `inf` → `round(inf, 1)` → ลงคลัง)

## สิ่งที่ต้องทำ
1. งาน `translate`: ใช้ `lang` จาก spec ของงาน (`job["_lang"]` หรือ `_spec["lang"]`) เท่านั้น
   ไม่มีใน spec = **ทำงานนี้ไม่สำเร็จ** (ข้อความไทย) ห้าม fallback ไปใช้ค่าจาก worker
2. `text` ที่หาย/ไม่ใช่สตริง/ว่างเปล่า ต้องเป็น error ภาษาไทยที่อ่านรู้เรื่อง ไม่ใช่ `KeyError: 'text'`
3. สาขา `process`/`bot`: ตรวจ `segments` ด้วยกติกาอย่างน้อยเท่าที่ `_clean_segments` บังคับ
   (ตัวเลข finite, เพดานจำนวน, เพดานความยาวข้อความ, ชื่อผู้พูดถูกตัด) — **clamp/ทิ้งรายการที่เสีย**
   ไม่ใช่ปฏิเสธทั้งงาน เพราะการถอดเสียงแพงกว่าการเสีย segment เดียวมาก แล้วบอกเจ้าของผ่าน `warning`
4. `duration` ต้องเป็นตัวเลข finite ที่ไม่ติดลบ, `language` เป็นสตริงสั้น, `speakers` เป็นลิสต์ของสตริง
5. **ห้ามพังเส้นทางปกติ** — นี่คือเส้นทางที่ทุกการประชุมจริงเดินผ่าน ผล `process` ปกติต้องลงคลังครบ

## กติกาอยู่ที่ไหน (การตัดสินใจเรื่อง layering)
`jobs.py` **import `server.py` ไม่ได้** — `server.py` ทำ `from . import backend, exports, jobs`
ตั้งแต่ระดับโมดูล การ import กลับจะเป็นวงกลม และการตรวจรูปร่างของข้อมูลก็ไม่ใช่เรื่องของชั้น HTTP อยู่แล้ว
กติกาจึงย้ายลงไปอยู่โมดูลใหม่ [meeting_ai/web/sanitize.py](../../meeting_ai/web/sanitize.py) ซึ่งเป็น
stdlib ล้วนและไม่ import อะไรในโปรเจกต์เลย ทั้ง `server.py` (ทางผู้ใช้) และ `jobs.py` (ทาง worker)
อยู่เหนือมันทั้งคู่

สองทางมีนโยบายต่างกันโดยเจตนา และโมดูลนี้บอกไว้ในคอมเมนต์:
- ทางผู้ใช้ = **reject** ทั้งก้อน (`_clean_segments` คืน `None` → 400) เพราะ client แก้แล้วส่งใหม่ได้
- ทาง worker = **salvage** เก็บรายการที่ใช้ได้ ทิ้งรายการที่เสีย แล้วแนบ `warning` เพราะถ้าปฏิเสธทั้งก้อน
  เจ้าของจะเสียบทถอดเสียงทั้งงาน (ต้องถอดเสียงใหม่ทั้งไฟล์) จากข้อมูลเสียรายการเดียว

## Acceptance criteria
1. worker POST `result` ของงาน translate พร้อม `"lang": "pwned"` → `translations` ได้ key ตาม spec
   (เช่น `en`) เท่านั้น ไม่มี key `pwned`
2. worker POST `result` ของงาน translate ที่ไม่มี `text` → job `error` + HTTP 400 พร้อมข้อความไทย
3. worker POST `result` ของงาน process ที่มี segment `start = NaN` / `end = Infinity` → การประชุมยัง
   `GET` ได้ 200 (JSON ถูกต้อง), export ทุกฟอร์แมตยังได้ 200 และ segment ที่เสียไม่ถูกเขียนลงคลัง
4. `duration = Infinity`, `language` ยาวผิดปกติ, `speakers` เป็นลิสต์ของ dict → ไม่ทำให้การประชุมพัง
5. ผล `process` ปกติ (segments + speaker + summary + duration + playback) ยังลงคลังครบทุกฟิลด์
6. ชุดทดสอบเดิมยังผ่านทั้งหมด (baseline 64 tests, 1 skipped)

## นอกขอบเขตของตั๋วนี้ (ส่งต่อ bug-triager)
- `audio_name` มาจาก `result["playback"]` ของ worker เช่นกัน วันนี้ถูกตัดเหลือ `Path(...).name` และ
  `store.audio_path()` ตัดซ้ำอีกชั้น จึงออกนอก `WEB_DIR` ไม่ได้ แต่ชื่ออย่าง `".."` ยังเล็ดลอดได้
  (เปิดไฟล์ไม่สำเร็จ → 404/500 ที่ endpoint audio ไม่ถึงขั้นทำให้ข้อมูลพัง)
- ขนาดของ `summary`/`text` ที่ worker ส่งกลับมายังไม่มีเพดาน — เป็นของ BACKLOG #11 (`_body_json` limit)
- ค่า `lang` ที่ **ผู้ใช้** ส่งเข้ามาที่ `POST /api/meetings/{id}/translate` ยังไม่ถูกตรวจ = BACKLOG #13
  ตั๋วนี้แก้แค่ฝั่ง worker ไม่ได้ทับงานนั้น
- `server._clean_segments()` ควรเรียกใช้กติกากลางใน `web/sanitize.py` ด้วย เพื่อไม่ให้สองทางเพี้ยนกัน
  แต่ไฟล์นั้นกำลังถูกแก้ค้างอยู่ใน BUG-011 (branch `fix/bug-011-body-size-caps`) ตั๋วนี้จึงไม่แตะ
  ให้รวมเป็น follow-up หลัง #11 merge

---

## Resolution — fixed (uncommitted) 2026-09-17

**ไฟล์ที่แก้**
- `meeting_ai/web/sanitize.py` (ใหม่) — กติกากลาง: `segments()` คืน `(ที่ใช้ได้, จำนวนที่ทิ้ง)`,
  `segment()`, `speakers()`, `duration()`, `language()`, `name()`, `text()`, `message()`
  เพดาน: `MAX_SEGMENTS=50_000`, `MAX_SEGMENT_TEXT=5_000`, `MAX_NAME=60`, `MAX_SPEAKERS=200`,
  `MAX_LANGUAGE=32`, `MAX_MESSAGE=500` (ค่าเดียวกับที่ BUG-011 เลือกไว้ฝั่งผู้ใช้ เพื่อให้ merge แล้วตรงกัน)
- `meeting_ai/web/jobs.py` `apply_result()` — `lang` อ่านจาก `job["_lang"]` (โหมดไฟล์) หรือ
  `job["_spec"]["lang"]` (โหมด cloud — แถว job ของ pgstore ไม่มีคีย์ `_lang`), ไม่มี = `RuntimeError`
  ภาษาไทย; `text` ที่หาย/ว่าง = `RuntimeError` ภาษาไทย (เดิม `KeyError: 'lang'`); `segments`,
  `speakers`, `duration`, `language`, `summary`, `summary_error`, `warning` ผ่าน `sanitize` ทั้งหมด;
  ถ้ามี segment ถูกทิ้ง จะต่อท้าย `warning` ของงานว่า "ข้ามข้อมูลบทถอดเสียงที่ผิดรูปแบบ N รายการ"

**ไม่มีการเปลี่ยน schema และไม่มี store function ใหม่** — `sanitize` เป็นฟังก์ชันบริสุทธิ์ ไม่แตะ store
จึงไม่ต้องมีฝาแฝดใน `store.py`/`pgstore.py` (ตรวจแล้ว: `apply_result` เรียกแค่ `create`,
`set_summary`, `set_translation`, `job_done` ซึ่งมีครบทั้งสอง backend อยู่แล้ว)

**พิสูจน์** (สคริปต์ชั่วคราวใน scratchpad ลบแล้ว — test-engineer เป็นเจ้าของเทสต์ถาวร)
- โหมดไฟล์ผ่าน HTTP จริง (127.0.0.1:55511, `WORKER_TOKEN` ของเล่น): 47 เช็ก ผ่านหมด
  ครอบคลุม AC 1-5 รวมทั้ง "ก่อนแก้พังจริง" (เขียน NaN ลง store ตรง ๆ → `GET` 500, `export.srt` 500)
- โหมด cloud ด้วย FakeStore (ไม่มี Postgres): 8 เช็ก ผ่านหมด — ยืนยันว่าแถว job ของ cloud
  ไม่มี `_lang` จริง และ `set_translation` ได้ `lang` จาก spec
- `python -m compileall -q meeting_ai api bot` → OK · `python -m unittest discover -s tests`
  → 64 tests, OK (skipped=1) เท่ากับ baseline
