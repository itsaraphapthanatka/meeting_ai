---
name: deploy-shape-matters-serverless
description: Any counter/cache/lock in meeting_ai must live in Postgres, not process memory — Vercel invocations share nothing; and login only exists in cloud mode so Postgres is always available
metadata:
  type: project
---

สถานะอะไรก็ตามที่ต้อง "นับรวมกันทุกคำขอ" (rate limit, quota, lock, dedupe) ต้องอยู่ในตาราง
Postgres ไม่ใช่ dict ระดับโมดูล — บน Vercel แต่ละ invocation เป็นคนละโพรเซส ตัวนับใน memory
จะ "ทำงานเงียบๆ โดยไม่ได้กันอะไร" ซึ่งแย่กว่าไม่แก้ เพราะ backlog จะขึ้นว่าปิดแล้ว

**Why:** เจอตอนทำ BUG-010 (rate limit ล็อกอิน) — และมีข้อเท็จจริงที่ทำให้ตัดสินใจง่ายขึ้นมาก:
**หน้าล็อกอินมีเฉพาะโหมด cloud** (`_auth_api` ตอบ 501 เมื่อ `backend.auth_required()` เป็นเท็จ)
แปลว่าทุก deployment ที่มี endpoint นั้นมี Postgres อยู่แล้ว → เขียน store function ใน `pgstore.py`
อย่างเดียวได้ ไม่ต้องทำฝาแฝดใน `store.py` ขอแค่ call site มี `if backend.cloud` คุมไว้
(ตรวจแบบเดียวกันก่อนตัดสินใจ: endpoint นี้โหมดไฟล์เรียกถึงไหม)

**How to apply:** ก่อนเขียน state ระดับโพรเซส ถามสองข้อ — (1) ทรง serverless ยังถูกต้องไหม
(2) เส้นนี้โหมดไฟล์เข้าถึงได้ไหม ถ้า (1) ไม่ผ่าน ให้ย้ายไป Postgres แล้วเหลือ memory เป็นชั้น
เร่งความเร็ว/fail-safe เท่านั้น และรายงานให้ชัดว่า "แต่ละทรงได้การป้องกันอะไรจริง"
psycopg ไม่มีบน CI/เครื่องนี้ก็พิสูจน์ SQL ได้ด้วย fake `db.connect` ที่บันทึก `(sql, params)`
