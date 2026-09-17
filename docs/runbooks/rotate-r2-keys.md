# Runbook — หมุนกุญแจ Cloudflare R2

**ทำโดย:** เจ้าของระบบเท่านั้น (ต้องล็อกอิน Cloudflare + กรอกค่าลับ — agent ทำแทนไม่ได้)
**สาเหตุที่ต้องหมุนรอบนี้:** ระหว่าง QA 2026-09-16 โหมด `files` ออก presigned PUT URL ของบัคเก็ต production จริง และ URL ที่เซ็นด้วยกุญแจนี้ถูกพิมพ์ลง log ของเซสชัน agent (ดู [QA-REPORT-2026-09-16.md](../qa/QA-REPORT-2026-09-16.md) HIGH 4 · BACKLOG #45)
**ตรวจสอบแล้ว 2026-09-16:** ทุกข้อในเอกสารนี้ตรวจจาก repo จริง ไม่ได้เดา

## ขอบเขตการรั่ว (ตรวจแล้ว)

| จุด | สถานะ |
|---|---|
| `.env` ถูก track ใน git | **ไม่เคย** — `git ls-files .env` ไม่พบ |
| `.env` เคยอยู่ใน git history | **ไม่เคย** — ไฟล์ตระกูล `.env*` ที่เคยเข้า git มีแค่ `.env.example` ซึ่งบรรทัด `S3_*` ถูกคอมเมนต์และไม่มีค่า |
| ค่าลับ hardcode ในโค้ด | ไม่มี — ทุกจุดอ่านจาก `os.environ` ([blobstore.py:284-290](../../meeting_ai/web/blobstore.py#L284)) |
| presigned URL หลุดออกนอกเครื่อง | ไม่ (อยู่ใน log เซสชันบนเครื่องนี้) — แต่ URL มีสิทธิ์เขียนบัคเก็ตจริง อายุ 1 ชม. จึงควรหมุนตามแนวปฏิบัติ |

**สรุป: ไม่มี credential ใน git** ความเสี่ยงจำกัดอยู่ที่ log บนเครื่องพัฒนา

## กุญแจอยู่ที่ไหนบ้าง (ครบทุกจุด)

| # | ที่อยู่ | ตัวแปร | หมายเหตุ |
|---|---|---|---|
| 1 | Cloudflare dashboard → R2 → API Tokens | (ต้นทาง) | ที่สร้าง/เพิกถอนกุญแจ |
| 2 | Vercel project → Settings → Environment Variables (production) | `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION` | ฝั่งเสิร์ฟเวอร์ที่ออก presigned URL |
| 3 | `C:/project/meeting_ai/.env` (เครื่องพัฒนา) | ชุดเดียวกัน 5 ตัว | ยืนยันแล้วว่ามีครบทั้ง 5 ตัว |

**เครื่อง worker ไม่ต้องมีกุญแจ R2** — ยืนยันจาก `setup-worker.ps1` (`# worker ไม่ต้องใช้ DATABASE_URL และไม่ต้องมีคีย์ R2`) worker รับ presigned URL จากเซิร์ฟเวอร์แทน จึงไม่ต้องแตะ `meeting-ai-worker.service` หรือ `worker-service.ps1` ในการหมุนรอบนี้

## ขั้นตอน

1. **สร้างกุญแจใหม่** — Cloudflare → R2 → Manage API Tokens → Create API Token
   - Permission: **Object Read & Write** และจำกัดเฉพาะบัคเก็ตที่ใช้จริง (อย่าใช้ Admin)
   - เก็บ Access Key ID + Secret Access Key ไว้ (Secret แสดงครั้งเดียว)
2. **อัปเดต Vercel ก่อน** (จุดที่ 2) แล้ว **redeploy** — env var ใหม่จะมีผลก็ต่อเมื่อ deploy ใหม่เท่านั้น
3. **ทดสอบ production ว่ายังอัปโหลดได้** — อัดประชุมสั้น ๆ หนึ่งรายการแล้วดูว่าไฟล์ขึ้นบัคเก็ตจริง (ถ้าพลาดขั้นนี้ ผู้ใช้จะอัปโหลดไม่ได้ทั้งระบบ)
4. **อัปเดต `.env` เครื่องพัฒนา** (จุดที่ 3)
5. **เพิกถอนกุญแจเก่า** ใน Cloudflare — ทำหลังข้อ 3 ผ่านแล้วเท่านั้น
6. **ตรวจว่าไม่มีไฟล์แปลกในบัคเก็ต** ช่วง 2026-09-16 เป็นต้นมา (URL ที่หลุดมีสิทธิ์เขียน)

## กันไม่ให้เกิดซ้ำ

- **BACKLOG #45**: ให้ `backend.storage()` เคารพ `MEETING_AI_CLOUD` หรืออย่างน้อยเตือนดัง ๆ เมื่อโหมด `files` กำลังจะใช้ S3 — ตอนนี้ [blobstore.py:284](../../meeting_ai/web/blobstore.py#L284) เลือก S3 ทันทีที่เจอ `S3_BUCKET` โดยไม่สนโหมด
- **แยก bucket dev/test ออกจาก production** และให้ `.env` เครื่องพัฒนาชี้ bucket dev เท่านั้น
- ทุกครั้งที่เปิดเซิร์ฟเวอร์ทดสอบ ให้ล้าง `S3_BUCKET S3_ENDPOINT S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY` ก่อน (บันทึกไว้ใน `docs/LEARNINGS.md` แล้ว และ `tests/_harness.py:20` ล้างให้อัตโนมัติ)

## สิ่งที่ไม่ต้องทำ
- ไม่ต้องล้าง git history (ไม่มี credential อยู่ในนั้น)
- ไม่ต้องแตะ CORS ของบัคเก็ต (คนละเรื่องกับกุญแจ ดู README หัวข้อ CORS)
- ไม่ต้องหมุน `WORKER_TOKEN` หรือ `LLM_API_KEY` ในรอบนี้ (ไม่เกี่ยวกับเหตุการณ์นี้)
