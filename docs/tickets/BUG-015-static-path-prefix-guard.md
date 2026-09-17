# BUG-015 (BACKLOG #15) — ด่านไฟล์ static ใช้ `str.startswith` แทน `Path.is_relative_to`

**Severity:** P2 — **การเสริมความแข็งแรง ไม่ใช่ช่องที่ยิงได้บนซอร์สทรีปัจจุบัน** (เหตุผลอยู่ในหัวข้อ "ยิงได้จริงแค่ไหน")
**Component:** `meeting_ai/web/server.py` → `Handler._static()` (ใช้ทั้งโหมด local และ cloud; `api/index.py` สืบทอด `Handler` ตัวเดียวกัน)
**Reported by:** code review 2026-09-16 (BACKLOG #15) · **Fixed by:** backend-dev · **Date:** 2026-09-17

## สาเหตุ (root cause)

`_static()` แปลง path จาก URL เป็นไฟล์จริงแล้วตรวจว่า "อยู่ใน `STATIC_DIR` ไหม" ด้วยการเทียบ **สตริง**:

```python
target = (STATIC_DIR / rel).resolve()
if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
```

`str.startswith` ไม่รู้จักขอบของโฟลเดอร์: `C:\...\web\static_backup\secret.txt` ขึ้นต้นด้วย
`C:\...\web\static` เหมือนกับไฟล์ที่อยู่ใน `static/` จริง ๆ ทุกประการ ดังนั้น **โฟลเดอร์หรือไฟล์
พี่น้องที่ชื่อขึ้นต้นด้วย `static`** (`static_backup/`, `static-old/`, `static.bak`) ถูกเสิร์ฟออกไปได้
ด้วยคำขอ `GET /static/../static_backup/<ไฟล์>` — `rel` ไม่ผ่านการ normalize และ `resolve()` จัดการ
`..` ให้เรียบร้อยก่อนถึงบรรทัดตรวจ

การหนีออกไป *นอก* คำนำหน้า (`/static/../../../.env`) ด่านเดิมกันได้อยู่แล้ว — ที่พังคือคำนำหน้าที่
ตรงกันแต่คนละโฟลเดอร์ (prefix confusion) ไม่ใช่ path traversal เต็มรูปแบบ

## พิสูจน์ก่อนแก้ (รันจริง ไม่ใช่การอ่านโค้ด)

PoC ยิง HTTP เข้า `server.Handler` ตัวจริง โดยชี้ `STATIC_DIR` ไปโฟลเดอร์ชั่วคราวที่จำลอง
deployment ซึ่งมี `static/` กับ `static_backup/` วางข้างกัน (`mock.patch.object(server, "STATIC_DIR", ...)`
เปลี่ยนแค่รากของโฟลเดอร์ โค้ดที่ตัดสินใจคือของจริงทั้งหมด):

```
ก่อนแก้                                          หลังแก้
200  /static/app.js                     -> '// app'                 200 (เหมือนเดิม)
200  /static/../static_backup/secret.txt -> 'LEAKED-CONTENT-12345'  404
404  /static/..%2f..%2f.env                                          404 (เหมือนเดิม)
404  /static/../../../.env                                           404 (เหมือนเดิม)
```

## ยิงได้จริงแค่ไหน (พูดตรง ๆ)

**ยิงไม่ได้บนซอร์สทรีปัจจุบัน**: ข้างๆ `meeting_ai/web/static/` มีแต่ `*.py`, `schema.sql`,
`__pycache__/` — ไม่มีชื่อไหนขึ้นต้นด้วย `static` (`ls meeting_ai/web/` ยืนยันแล้ว) จึงไม่มีไฟล์ให้
รั่วออกไปตอนนี้ ความเสี่ยงจริงคือ "วันที่ใครสักคนวาง `static-old/`, `static_backup/`, `static.bak`
หรือ bundle เก่าไว้ข้าง ๆ ตอน deploy/ดีบั๊ก" แล้วมันกลายเป็นไฟล์สาธารณะทันทีโดยไม่มีใครรู้ตัว
ตั๋วนี้จึงปิด **คลาสของบั๊ก** ไม่ใช่ปิดช่องที่มีคนยิงอยู่ — priority ตามนั้น (ไม่ใช่ของด่วน)

ที่ตรวจแล้วว่า *ไม่ใช่* ปัญหา: `/static/..\..\.env` (backslash) และ `%2e%2e` — `_static()` ไม่ได้
percent-decode และเส้นทางที่ออกนอกคำนำหน้าโดนด่านเดิมอยู่แล้ว

## ทางเลือกที่พิจารณา

| ทางเลือก | ผล | ทำไมไม่เลือก |
|---|---|---|
| A. เติม `os.sep` ต่อท้ายคำนำหน้าแล้วค่อย `startswith` | ปิดเคสนี้ได้ | ยังเป็นการเทียบสตริง (ต้องคิดเรื่อง separator ของแต่ละ OS, trailing sep, ตัวพิมพ์) และคนอ่านโค้ดครั้งหน้ายังต้องพิสูจน์ความถูกต้องเอง |
| B. `Path.relative_to()` ใน try/except ValueError | ถูกต้อง | เขียนยาวกว่าและใช้ exception เป็น control flow ทั้งที่ stdlib มีตัวตรงอยู่แล้ว |
| C. `os.path.commonpath([...])` | ถูกต้อง | โยน `ValueError` เมื่อคนละ drive บน Windows ต้อง try/except อีกชั้น |
| D. allow-list ชื่อไฟล์ที่เสิร์ฟได้ | แคบที่สุด | เปลี่ยนพฤติกรรม (เพิ่มไฟล์ใหม่ใน `static/` ต้องแก้โค้ด) เกินขอบเขตตั๋ว |
| **E. `Path.is_relative_to()`** | ถูกต้อง | **เลือกข้อนี้** — stdlib (3.9+, โปรเจกต์ใช้ 3.12), เทียบเป็นส่วนประกอบของ path, diff 1 บรรทัดครึ่ง |

## สิ่งที่แก้

`meeting_ai/web/server.py` → `_static()` เท่านั้น:

```python
root = STATIC_DIR.resolve()
target = (root / rel).resolve()
if not target.is_relative_to(root) or not target.is_file():
```

(เก็บ `root` ไว้ใช้ซ้ำแทนที่จะเรียก `STATIC_DIR.resolve()` สองครั้งเหมือนเดิม) ไม่แตะ API, สคีมา,
หรือพฤติกรรมของไฟล์ที่ถูกต้องเลย

## เกณฑ์ผ่าน (acceptance)

- `GET /static/../static_backup/secret.txt` และ `GET /static/../static.bak` → **404** (ก่อนแก้ = 200 + เนื้อไฟล์)
- `GET /static/app.js`, `GET /`, `GET /sw.js`, `GET /manifest.webmanifest` → **200** เหมือนเดิม
- `GET /static/../../../.env`, `/static/../../store.py`, `/static/..%2f..%2f.env` → 404 เหมือนเดิม
- `GET /static/../static` (ตัวโฟลเดอร์เอง) → 404 (`is_file()` เป็นด่านที่สอง)
- เทสต์ไม่ vacuous: ปลอม `is_relative_to` ให้ทำงานแบบ `startswith` แล้วคำขอเดิมต้องกลับมา 200 อีกครั้ง

regression test: `tests/test_bug_015_static_path_guard.py` (7 เคส รวมเคสพิสูจน์ว่าไม่ vacuous)

## ของแถมที่ไม่ได้แก้ในตั๋วนี้ (ส่งต่อ bug-triager)

- `_static()` ไม่ percent-decode path เลย ไฟล์ static ที่ชื่อมีอักขระต้องเข้ารหัส (เช่น ช่องว่าง)
  จะเปิดไม่ได้ — ตอนนี้ไม่มีไฟล์แบบนั้นใน `static/` จึงไม่กระทบใคร แต่เป็นกับดักถ้ามีคนเพิ่ม
- `_static()` อ่านไฟล์ทั้งก้อนด้วย `read_bytes()` ทุกคำขอ (ไม่มี ETag/If-None-Match, `Cache-Control: no-cache`)
