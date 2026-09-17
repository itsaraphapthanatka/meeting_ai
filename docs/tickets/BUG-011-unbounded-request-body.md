# BUG-011 — `_body_json` อ่าน body ไม่จำกัดขนาด และ `_clean_segments` ไม่มีเพดาน

- **Severity:** P1 (BACKLOG #11) — QA report จัดเป็น BLOCKER 2 + HIGH 3
- **Owner:** backend-dev
- **Status:** **fixed (uncommitted)** 2026-09-16
- **Source:** [docs/qa/QA-REPORT-2026-09-16.md](../qa/QA-REPORT-2026-09-16.md) BLOCKER 2 / HIGH 3 · พบโดย api-tester (ยิงจริง)

## อาการ
[meeting_ai/web/server.py:376-397](../../meeting_ai/web/server.py#L376) `_body_json()` อ่าน `Content-Length` แล้ว `self.rfile.read(length)` **ไม่มีเพดานเลย** ต่างจาก `_read_body_to()` ที่รับ `limit` (ใช้ `MAX_UPLOAD` 2 GB / `MAX_LIVE_CLIP` 32 MB)

ยืนยันจริง: `POST /api/meetings` body 20,000,013 ไบต์ (`{"title": "x"*20000000}`) → **201 Created**

[server.py:1274](../../meeting_ai/web/server.py#L1274) `_clean_segments()` วน `for item in raw` โดยไม่จำกัดจำนวน item และไม่ตัดความยาว `text` (ขณะที่ `speaker` ถูกตัดที่ 60 ตัวอักษร)

กระทบทุก call site ของ `_body_json()` — มี **15 จุด** ใน `server.py`

## ⚠️ ข้อควรระวังที่สำคัญที่สุดของตั๋วนี้
**อย่าใส่เพดานเดียวแบบ 1 MB ให้ทุก call site** — จะทำให้ระบบพังกับข้อมูลจริง

ตัวเลขที่วัดจาก production จริง (2026-09-16, read-only):

| ค่า | ของจริงวันนี้ |
|---|---|
| meeting ทั้งหมด | 13 |
| `segment_count` สูงสุด | **2,905** |
| `segments` JSON ใหญ่สุด | **327,374 bytes** |
| `segments` JSON เฉลี่ย | 38,384 bytes |
| `summary` ใหญ่สุด | 7,258 bytes |
| `segments + summary + translations` | **334,634 bytes** |

สอง call site ที่ถือ payload ใหญ่จริงและห้ามรัดคอ:
- `POST /api/worker/jobs/{id}/result` ([server.py:921-922](../../meeting_ai/web/server.py#L921)) — worker ส่ง transcript + summary ทั้งก้อนกลับมา วันนี้ใหญ่สุด ~335 KB
- `PATCH /api/meetings/{id}` ([server.py:1078](../../meeting_ai/web/server.py#L1078)) — ผู้ใช้แก้ transcript แล้วส่ง segments ทั้งชุดกลับมา

เพดาน 1 MB = แค่ ~3 เท่าของสถิติสูงสุดปัจจุบัน ประชุม 3-4 ชั่วโมงจะทะลุ และผู้ใช้จะแก้ transcript ไม่ได้โดยไม่มีคำอธิบาย

## สิ่งที่ต้องทำ
1. ให้ `_body_json()` รับพารามิเตอร์ `limit` (ตามแบบ `_read_body_to`) โดย **default เป็นค่าเล็ก** สำหรับ control-plane (settings, login, signup, invite, translate, visibility, share, stop, bot) — ขนาดหลักสิบ KB ก็เกินพอ
2. ส่ง `limit` ที่ใหญ่กว่าอย่างชัดเจนเฉพาะสอง call site ข้างบน เลือกค่าที่มี headroom จริง (ข้อเสนอ: 8 MB = ~24 เท่าของสถิติปัจจุบัน) และเขียนคอมเมนต์อ้างตัวเลขที่วัดได้ เพื่อให้คนที่มาปรับทีหลังรู้ว่าเลขนี้มาจากไหน
3. เกินเพดาน → **413 Payload Too Large** พร้อมข้อความไทย (ไม่ใช่ 400) และต้องไม่อ่าน body ทิ้งจนหมดก่อนปฏิเสธ
4. `Content-Length` ที่หายไป/ติดลบ/ไม่ใช่ตัวเลข ต้องไม่ทำให้ 500
5. `_clean_segments()` — จำกัดจำนวน item และตัด `text` ให้มีเพดาน เลือกค่าที่รองรับ 2,905 segments วันนี้ได้สบาย ๆ (ข้อเสนอ: 50,000 items, text 5,000 ตัวอักษร)

## Acceptance criteria
1. `POST /api/meetings` ด้วย body 20 MB → **413** (วันนี้ได้ 201)
2. `POST /api/settings`, `/translate`, `/visibility` ด้วย body เกินเพดานเล็ก → 413
3. `POST /api/worker/jobs/{id}/result` ด้วย payload ขนาด ~335 KB (เท่าของจริงที่ใหญ่สุด) → **ยังทำงานปกติ** ไม่ใช่ 413
4. `PATCH /api/meetings/{id}` ด้วย segments 3,000 รายการ → ยังทำงานปกติ
5. `_clean_segments` ปฏิเสธหรือตัดลิสต์ที่ยาวเกินเพดาน และตัด `text` ที่ยาวผิดปกติ
6. ชุดทดสอบเดิมยังผ่านทั้งหมด

## ห้ามแตะ
`meeting_ai/config.py` — เจ้าของกำลังแก้ค้างไว้ใน working tree สำหรับ BACKLOG #7 (คนละเรื่องกัน)


---

## Resolution — fixed (uncommitted) 2026-09-16

ผ่าน review 1 รอบ โดย code-reviewer, security-engineer และ test-engineer ทำงานขนานกัน ทั้งสามเจอปัญหาจริงพร้อม PoC ระดับ byte

**ไฟล์ที่แก้:** `meeting_ai/web/server.py` เท่านั้น (โค้ด production)

**เพดานที่เลือก** (อ้างตัวเลข production ที่วัดวันนี้: 2,905 segments / 327,374 B / payload รวม 334,634 B)
- `MAX_JSON_BODY` 64 KB สำหรับ control-plane ทั้ง 15 call site
- `MAX_JSON_TRANSCRIPT` 8 MB เฉพาะ `POST /api/worker/jobs/{id}/result` และ `PATCH /api/meetings/{id}` (~25 เท่าของสถิติจริง)
- `MAX_SEGMENTS` 50,000 · `MAX_SEGMENT_TEXT` 5,000

**สิ่งที่เพิ่มเข้ามาในรอบ review — สำคัญกว่าตัวเพดานเอง**

รีวิวพบว่าแพตช์รอบแรกยังเปิดช่องไว้หลายจุด และหนึ่งในนั้นแพตช์เป็นคนสร้างเอง:

1. **Request smuggling บน keep-alive** — ยืนยันด้วย byte stream ได้ 2 responses ต่อ 1 คอนเนกชัน 4 รูปแบบ (chunked ซ้อนคำขอ, `Content-Length` สั้นกว่า body จริง, CL ซ้ำสองค่า, 404 ที่ตอบก่อนอ่าน body) ทำได้ในโหมด cloud โดยไม่ต้องล็อกอิน แก้ด้วยกฎกลางใน `_begin_body()`/`_send()`: คอนเนกชันที่เคยมี request body จะไม่ถูกใช้ซ้ำ + `Transfer-Encoding` ที่ไม่ใช่ identity → 501 + `Content-Length` ซ้ำ → 400
2. **slowloris** — `LINGER_SECONDS` ไม่ได้บังคับเวลาจริงเพราะ `BufferedReader.read()` รอจนครบ n และ `settimeout` นับเฉพาะช่วงเงียบ วัดได้ 21.62 วิต่อเธรด และ 100 คอนเนกชันด้วย 7.4 KB แก้ด้วย `read1()` + คำนวณ timeout ใหม่ทุกรอบ + `Handler.timeout = 30`
3. **413 ไม่ส่ง header `Connection: close`** ทำให้คำขอ *ถัดไป* ตายด้วย `RemoteDisconnected`
4. **`_read_body_to()`** ยังใช้ `int()` ดิบ → 500 และคาย exception ของ Python ให้ client
5. **`NaN`/`Infinity` ใน segments** เขียนลงไฟล์ได้ ทำให้ `GET` และ export ตอบ 500 **ถาวร** — ผู้ถือลิงก์แชร์แบบแก้ไขได้ก็ทำได้
6. `_content_length()` เคยรับ `1_0`, `+10`, `" 10 "` = parser differential กับ proxy
7. ของแถมที่ dev เจอเอง: `Handler.timeout` ที่เพิ่งใส่ทำให้ `log_message` พังด้วย AttributeError ก่อนมี `self.path`

**ผลหลังแก้ (วัดโดย dev และ QA lead ยืนยันซ้ำอิสระ)**

| | ก่อน | หลัง |
|---|---|---|
| smuggling ทั้ง 4 แบบ | 2 responses | **1** ทุกแบบ |
| keep-alive ปกติ (GET ×2) | 2 | **2** — ไม่ได้เหวี่ยงปิดทุกคอนเนกชัน |
| หยด 1 ไบต์/0.4 วิ | 21.62 วิ | **2.03 วิ** |
| 100 คอนเนกชัน | ค้าง 100/100 | ปล่อยครบใน **2.59 วิ** |
| 413 | ไม่มี `Connection: close` | มี และใช้คอนเนกชันต่อได้ |
| `NaN` | meeting พังถาวร | **400** ไม่มีไฟล์เสีย |

**Regression test:** `tests/test_bug_011_body_size_caps.py` (43 tests) — ชุดเต็ม **107 tests OK, skipped 1, exit 0** (เดิม 64)
test-engineer พิสูจน์ว่าเทสไม่ vacuous ด้วยการย้อนกลไกทีละจุด และพบว่าเทสของ `_read_body_to` ต้องย้อน **สองจุดพร้อมกัน** ถึงจะได้บั๊กเดิมคืน เพราะ `_begin_body()` ดักไว้ก่อน — แก้ docstring ให้ตรงความจริงแล้ว

**Migration / SQL:** none

**ยกไปตั๋วใหม่:** BACKLOG #50 (worker result เกิน 8 MB ทิ้ง transcript ถาวร), #51 (`except Exception` คาย exception ของ Python), #52 (`Server:` header เปิดเผยเวอร์ชัน Python) · และ slowloris เฟส header ยังเปิดอยู่ (`timeout` วัดความเงียบ ไม่ใช่เวลารวม) ควรเปิดตั๋วแยก

**สิ่งที่แลกมาและต้องรู้:** ทุก POST/PATCH ที่มี body จบด้วยการปิดคอนเนกชัน (หนึ่ง TCP ต่อหนึ่งคำขอที่มี body) ส่วน GET/HEAD/static ยัง keep-alive ตามเดิม
