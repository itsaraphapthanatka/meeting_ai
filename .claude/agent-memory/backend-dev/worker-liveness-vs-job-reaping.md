---
name: worker-liveness-vs-job-reaping
description: meeting_ai — heartbeat กับการ reap งานเป็นคนละกลไก; กับดักตอนเขียนเทสต์ที่ขับ worker.run() ของจริง (BACKLOG #20)
metadata:
  type: project
---

# heartbeat ≠ ตัวตัดสินว่างานจะถูก reap (ตรวจกับโค้ด 2026-09-17)

**Why:** ตั๋ว BACKLOG #20 เขียนว่า "heartbeat หยุด → server reap งาน" ซึ่งฟังดูเป็นเหตุเป็นผล
แต่ของจริงเป็นคนละเส้น ถ้าเชื่อตามตั๋วจะไปแก้ผิดชั้น (เช่นไปผ่อนกฎ reap ฝั่งเซิร์ฟเวอร์)

- `workers.last_seen` (heartbeat) ใช้แค่ `pgstore.workers_list()` (`alive`/`status='gone'`) และ
  `worker_capabilities()` — ตัวหลังทำให้ `/api/config` ตอบ "ยังไม่มีเครื่องประมวลผลออนไลน์" เมื่อ 0 เครื่อง
- `pgstore.jobs_reap()` ตัดสินจาก **`jobs.updated_at`** (ขยับเมื่อ worker POST `progress`) ไม่ใช่ heartbeat
  และถูกเรียกจาก `jobs.claim()` **ที่เดียว** → ไม่มี worker มาขอคิว = ไม่มีใคร reap อะไรเลย
- `pgstore.job_request_stop()` มี `STOP_ORPHAN_SECONDS = 120`: ปุ่ม "หยุด" ของผู้ใช้จะ **ยกเลิกงานทิ้ง**
  ถ้า `updated_at` เก่ากว่า 2 นาที — หน้าต่างแคบกว่า reaper 30 นาทีมาก
- `stale_after` ที่ `server.py` ตอบกลับไปกับ heartbeat เป็นเลขตายตัวที่ **ไม่มีใครอ่าน** (`Client.heartbeat()` ทิ้งคำตอบ)
  ตัวที่มีผลจริงคือ `pgstore.WORKER_STALE_SECONDS`

**How to apply:** เจอคำถาม "ทำไมงานถูกคืนคิว/ตีเป็น error" ให้ไล่จาก `jobs.updated_at` ก่อนเสมอ
ส่วนอาการ "หน้าเว็บบอกว่าไม่มี worker" ค่อยไปดู heartbeat — และอย่าลืมว่าธง `stopping` ใน `worker.run()`
เคยถูกใช้สองความหมาย (หยุดคว้างานใหม่ / หยุดเต้น) จนเป็นที่มาของบั๊กนี้

# เทสต์ที่ขับ `worker.run()` ของจริง — กับดักที่เสียเวลาไปแล้ว

- ต้องรันใน **เธรดรอง**: `run()` ติดตั้ง SIGINT handler เมื่ออยู่เธรดหลัก แล้วไม่คืนของเดิม (เธรดรอง → ValueError ที่โค้ดจับเอง)
- ค่าเวลาต้องเป็น **ค่าคงที่ระดับโมดูล** ถึงจะ `mock.patch.object` บีบเวลาได้ — เลขที่ฝังในลูป (`for _ in range(600)`) ทดสอบไม่ได้เลย
  และ `range(n) + sleep(1)` ≠ n วินาทีจริง ใช้ `time.monotonic()` deadline แทน
- heartbeat ครั้งแรกหลัง claim อาจยังรายงาน `idle` (เธรด beat อ่าน `active` ก่อนเธรดหลักลงทะเบียนงานเสร็จ) — อย่า assert ทุกครั้ง
- งานปลอมที่ยังเดินอยู่ตอน `run()` คืนค่าเป็นเธรด daemon: ต้องมี cancel event ให้มันจบ **ก่อน** ปิดเซิร์ฟเวอร์ปลอม
  และต้องอยู่ในขอบเขต `redirect_stdout` ไม่งั้นข้อความไทยของมันหลุดออกคอนโซล cp874 หลังเทสต์จบ
- `progress()` ฝั่ง worker มี `PROGRESS_MIN_GAP = 1.5` — งานปลอมต้องเปลี่ยนข้อความ step ทุกครั้ง ไม่งั้น progress ถูกกลืน

# ชุดเทสต์แกว่งเมื่อรันพร้อมกันหลาย agent

`tests/test_bug_011_body_size_caps.py` (413 + keep-alive บน socket จริง) ล้ม 2 เคสและทำให้ทั้งชุดใช้เวลา
543 วิ แทน ~90 วิ ตอนมีอีก session รันชุดเทสต์พร้อมกัน — รันซ้ำแล้วผ่านทุกครั้ง (268 OK / 104 วิ)
**อย่าเพิ่งโทษ diff ตัวเอง: รันซ้ำ และรันโมดูลนั้นเดี่ยว ๆ ก่อน**
