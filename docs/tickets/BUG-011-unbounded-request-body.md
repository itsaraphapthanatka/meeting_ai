# BUG-011 — `_body_json` อ่าน body ไม่จำกัดขนาด และ `_clean_segments` ไม่มีเพดาน

- **Severity:** P1 (BACKLOG #11) — QA report จัดเป็น BLOCKER 2 + HIGH 3
- **Owner:** backend-dev
- **Status:** open
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
