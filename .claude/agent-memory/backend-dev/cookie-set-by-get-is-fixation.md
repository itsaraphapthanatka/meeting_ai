---
name: cookie-set-by-get-is-fixation
description: Any GET that plants a long-lived cookie is attacker-triggerable; the confirm step must be a POST with application/json, and in this SPA the confirm branch must run before needsAuth()
metadata:
  type: feedback
---

endpoint ที่ **GET แล้วตั้งคุกกี้ให้เลย** = เว็บอื่นยัดค่าเข้าเบราว์เซอร์คนอื่นได้เสมอ
ย้ายการตั้งคุกกี้ไปที่ `POST` ที่บังคับ `Content-Type: application/json` (BUG-016, `/s/<token>` → `POST /api/auth/share`)

**Why:** `SameSite=Lax` คุมแค่ "ส่งคุกกี้ตอนไหน" ไม่ได้ห้าม **ตั้ง** คุกกี้จาก top-level navigation
ที่มาจากโดเมนอื่น (ลิงก์/redirect/window.open) และ repo นี้ไม่มี CSRF token เลยสักเส้น
สิ่งที่ใช้แทนได้จริงคือ: ฟอร์มข้ามเว็บส่ง Content-Type ได้แค่ urlencoded/plain/multipart และ
`fetch` ข้ามโดเมนที่ตั้ง `application/json` ต้อง preflight ซึ่งเซิร์ฟเวอร์นี้ไม่ตอบ CORS เลย
→ เช็ค Content-Type แล้วตอบ 415 ก็พอ **ต้องเช็คจริง ๆ** เพราะ `_body_json()` parse สำเร็จ
ไม่ว่ามาด้วย Content-Type อะไร (ฟอร์มข้ามเว็บส่ง `{"token":"..."}` เป็น text/plain ได้)
ทางเลือกที่ตั้งใจไม่ทำ: เทียบ `Origin` กับ `Host` — ได้เพิ่มน้อย แต่พังง่ายหลัง proxy/Vercel

**How to apply:** ทุกครั้งที่จะเขียน `Set-Cookie` ถามก่อนว่า "คำขอนี้เว็บอื่นสั่งให้เบราว์เซอร์
เหยื่อยิงได้ไหม" · ฝั่ง `app.js` ต้องเช็ค "โทเคนค้างอยู่ใน URL" **ก่อน** `needsAuth()` ใน `init()`
ไม่งั้นผู้เยี่ยมชมที่ยังไม่มีคุกกี้จะเจอหน้าล็อกอินแทนหน้ายืนยัน (พลาดตอนวางลำดับครั้งแรก)
และเมื่อคุกกี้เป็น `HttpOnly` ผู้ใช้ลบเองไม่ได้ → ต้องคู่กับทางออก (`DELETE` endpoint + ปุ่ม)
ดู [[tests-can-encode-the-bug]]
