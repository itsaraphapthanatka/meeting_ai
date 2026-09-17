# BUG-045 — โหมด `files` ใช้บัคเก็ต R2 production โดยไม่มีใครสั่ง

- **Severity:** P1 (BACKLOG #45) — ความเสี่ยงต่อข้อมูล production ไม่ใช่บั๊กที่ผู้ใช้เห็น
- **Owner:** backend-dev (verify: security-engineer)
- **Status:** **fixed (uncommitted)** 2026-09-17
- **Source:** [docs/qa/QA-REPORT-2026-09-16.md](../qa/QA-REPORT-2026-09-16.md) HIGH 4 · **เกิดขึ้นจริงแล้ว** ระหว่างรอบ QA

## เกิดอะไรขึ้นจริง
api-tester รันเซิร์ฟเวอร์ทดสอบตามสูตรปกติ `./mai web --port 55430 --no-open` (โหมดไฟล์ ไม่มี DB) แล้วเรียก `GET /api/meetings/<mid>/tracks/mixed/upload-url?ext=wav` ได้ **presigned PUT URL ของบัคเก็ต R2 production จริง** กลับมา อายุ 1 ชั่วโมง ทั้งที่ `/api/config` รายงาน `"mode": "files"`

ไม่มีไฟล์ถูกอัปโหลดขึ้นไปจริง (ใช้เส้นทาง POST bytes แทน) แต่ URL ที่เขียนบัคเก็ตได้ถูกพิมพ์ลง log ของเซสชัน — เป็นเหตุผลที่ต้องหมุนกุญแจ R2 ([runbook](../runbooks/rotate-r2-keys.md))

## Root cause
[meeting_ai/web/backend.py:36-39](../../meeting_ai/web/backend.py#L36) `storage()` เรียก `blobstore.get_storage()` ตรง ๆ และ [blobstore.py:284](../../meeting_ai/web/blobstore.py#L284) เลือก `S3Storage` ทันทีที่เจอ `S3_BUCKET` ในสภาพแวดล้อม **โดยไม่ดู `MEETING_AI_CLOUD` และไม่มีสัญญาณใด ๆ บอกผู้ใช้**

`.env` ของเครื่องพัฒนาคือไฟล์เดียวกับที่ถือค่า production ทุกการรันในเครื่องจึงเปิด S3 ให้เองเงียบ ๆ

## ⚠️ อย่าแก้ด้วยการผูกกับ `MEETING_AI_CLOUD`
การที่ blob storage แยกจาก DB storage **เป็นความตั้งใจ** (มีคอมเมนต์ในโค้ด) มีการตั้งค่าที่ถูกต้องอยู่จริงสองแบบที่ต้องไม่พัง:
- cloud DB + ดิสก์ในเครื่อง (เช่น self-host ที่ไม่ใช้ R2)
- โหมดไฟล์ + S3 โดยตั้งใจ (เช่น ทดสอบ blob path กับ bucket ของตัวเอง)

ปัญหาคือ **มันเปิดเองโดยไม่มีใครสั่ง** ไม่ใช่ว่ามันเปิดได้

## สิ่งที่ต้องทำ
1. S3 ยังเป็นค่าเริ่มต้นเมื่ออยู่ในโหมด cloud (`MEETING_AI_CLOUD` + `DATABASE_URL`) — **พฤติกรรม production ต้องไม่เปลี่ยน**
2. เมื่อ**ไม่ได้**อยู่ในโหมด cloud ให้ใช้ดิสก์ในเครื่อง **เว้นแต่**มีการ opt-in อย่างชัดเจน (เสนอ: `MEETING_AI_REMOTE_BLOBS=1`) ตั้งชื่อตัวแปรให้อ่านแล้วรู้ว่ากำลังจะแตะของจริง
3. **ทุกครั้ง**ที่เลือก `S3Storage` ให้พิมพ์บรรทัดเตือนตอนเริ่มทำงาน ระบุ endpoint + ชื่อ bucket (ห้ามพิมพ์คีย์) เพื่อให้คนที่รันเห็นทันทีว่ากำลังต่อของจริง
4. ถ้ามี `S3_*` ครบแต่ถูกข้ามเพราะไม่ได้ opt-in ให้บอกด้วยหนึ่งบรรทัดว่ากำลังใช้ดิสก์และจะเปิด S3 ได้อย่างไร — ไม่ใช่เงียบ เพราะคนที่ตั้งใจใช้ S3 จะงงว่าทำไมไม่ทำงาน
5. อัปเดต `.env.example` และ `README.md` ให้ตรงกับตัวแปรใหม่

## Acceptance criteria
1. `./mai web --no-open` โดยมี `S3_*` ครบใน env และไม่มี `MEETING_AI_CLOUD` → `upload-url` คืน `url: null` (ดิสก์) และมีบรรทัดแจ้งว่าข้าม S3 อยู่
2. เพิ่ม `MEETING_AI_REMOTE_BLOBS=1` เข้าไป → กลับไปใช้ S3 และมีบรรทัดเตือนระบุ bucket
3. โหมด cloud (`MEETING_AI_CLOUD=1` + `DATABASE_URL`) + `S3_*` → ใช้ S3 เหมือนเดิม **ไม่ต้อง** opt-in และมีบรรทัดเตือน
4. ไม่มี `S3_*` เลย → ดิสก์ เงียบ ๆ ตามเดิม ไม่มีเสียงรบกวน
5. ไม่มีค่าคีย์ (`S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`) ปรากฏใน log ใด ๆ
6. ชุดทดสอบเดิมยังผ่านครบ (baseline branch นี้ = 64 tests, 1 skipped)

## หมายเหตุ
`blobstore.get_storage()` แคชผลไว้ใน `_current` และมี `reset()` สำหรับเทส — ใช้ `reset()` ในเทส อย่าไปแก้ตัวแคช


---

## Resolution — fixed (uncommitted) 2026-09-17

ผ่าน review 1 รอบ (code-reviewer: approve with nits · test-engineer: 11+2 เทส)

**ไฟล์ที่แก้:** `meeting_ai/web/blobstore.py`, `meeting_ai/web/backend.py`, `meeting_ai/web/server.py` + `.env.example`, `README.md`, `docs/PROJECT-CONTEXT.md`

**กลไก**
- `get_storage(local_root, *, allow_remote=False)` — keyword-only และ default เป็น "ไม่แตะของจริง" ผู้เรียกใหม่ที่ลืมส่งธงจะพลาดไปทางปลอดภัย
- `backend.storage()` ส่ง `allow_remote=cloud` ซึ่งเป็นผู้เรียกเดียวของ `get_storage` ทั้งระบบ (ยืนยันด้วย grep ทุก call site) — นโยบายอยู่ที่ชั้นที่รู้เรื่องโหมด ส่วน `blobstore` แค่รับธง จึงไม่ได้ผูก blob เข้ากับ DB backend ตามที่ตั๋วห้ามไว้
- โหมดไฟล์ต้องตั้ง `MEETING_AI_REMOTE_BLOBS=1` เอง · โหมด cloud ใช้ S3 เป็นค่าเริ่มต้นเหมือนเดิม

**ทุกเส้นทางประกาศตัวเองแล้ว — ไม่มีการตกกลับแบบเงียบเหลืออยู่**

| สถานการณ์ | ผล | แจ้ง |
|---|---|---|
| `S3_*` ครบ, file mode, ไม่ opt-in | ดิสก์ | ℹ️ บอกวิธีเปิด |
| `S3_*` ครบ + opt-in | S3 | ⚠️ ระบุ bucket + endpoint |
| `S3_*` ครบ + cloud mode | S3 | ⚠️ เหมือนกัน |
| opt-in แต่ `S3_*` ไม่ครบ | ดิสก์ | ⚠️ **ระบุชื่อตัวแปรที่ขาด** |
| cloud mode แต่ `S3_*` ไม่ครบ | ดิสก์ | ⚠️ เตือนว่า serverless เขียนดิสก์ไม่ได้ |
| ไม่มี `S3_*` เลย | ดิสก์ | เงียบ (ถูกต้อง) |

สองแถวท้ายกลุ่มกลางเป็นผลจากรอบ review — ฉบับแรกยังเงียบอยู่ ซึ่งเป็นบั๊กชนิดเดียวกับที่ตั๋วนี้ตั้งใจกำจัด (ตั๋วเขียนว่า "มี `S3_*` **ครบ**" จึงหลุดตามตัวอักษร ไม่ใช่ความผิดของ dev)

**การตัดสินใจที่บันทึกไว้:** `allow_remote=cloud` หมายถึง "ขอโหมด cloud **และ** ต่อ DB ได้" ถ้า `DATABASE_URL` หายหรือพิมพ์ผิด จะไม่แตะบัคเก็ตของจริง — deployment ที่ตั้งค่าไม่ครบควรพังแบบอ่านง่าย ดีกว่าเขียนข้อมูลไปผิดที่ เขียนเหตุผลไว้ใน docstring ของ `storage()` แล้ว

**ความทนทานของบรรทัดแจ้งเตือน:** `_notice()` ตกไปใช้ ASCII เมื่อ stderr เป็น cp874 และเงียบเมื่อ stderr ถูกปิด — บรรทัดเตือนกลายเป็น 500 ไม่ได้ (เครื่องเจ้าของเป็น cp874 จริง)

**Regression test:** `tests/test_bug_045_blob_storage_optin.py` (13 tests) · ชุดเต็ม **77 tests OK, skipped 1** (เดิม 64)
พ่วงปิดช่องใน `tests/_harness.py` ที่ blank แค่ `S3_BUCKET` — ตอนนี้ blank ครบทั้ง 6 ตัวแปร ไม่ให้คีย์ production เข้าไปอยู่ใน env ของโพรเซสเทสเลย

**Migration / SQL:** none · Vercel ไม่ต้องเพิ่ม env (`MEETING_AI_CLOUD=1` ทำให้ S3 เป็นค่าเริ่มต้นอยู่แล้ว)

**แพตเทิร์นเดียวกันที่ยังเหลือ (ส่งต่อ bug-triager ไม่ได้แก้):** `config.py:33-35` การรันในเครื่องยิง LLM จริงด้วยคีย์จริงโดยไม่มีสัญญาณ · `stt.py:110-118` `resolve()` ตกจาก local ไป API เงียบ ๆ ทำให้เสียงประชุมออกนอกเครื่อง · `config.py:62` `WORKER_TOKEN` เปิด `/api/worker/*` ให้เองทุกครั้ง
