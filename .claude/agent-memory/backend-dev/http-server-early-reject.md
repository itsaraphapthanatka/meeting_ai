---
name: http-server-early-reject
description: Rejecting a request before reading its body in stdlib http.server (HTTP/1.1 keep-alive) corrupts the next request on that connection unless you send Connection close
metadata:
  type: feedback
---

ตอบ 4xx/429 **ก่อน** อ่าน request body ใน `meeting_ai/web/server.py` ต้องสั่งปิดสายด้วยเสมอ:
ใส่ header `Connection: close` แล้วตั้ง `self.close_connection = True`

**Why:** `Handler.protocol_version = "HTTP/1.1"` → keep-alive เปิดอยู่ ไบต์ของ body ที่ยังไม่ถูกอ่าน
ค้างในซ็อกเก็ต แล้ว `BaseHTTPRequestHandler` จะอ่านมันเป็น request line ของคำขอถัดไป → 400 เพี้ยน
ทั้งสาย เจอตอนทำ rate limit ของ `/api/auth/login` (BUG-010) ซึ่งจงใจตัดสินก่อนอ่าน body เพื่อไม่ให้
เสียค่า scrypt ~16 MB/43 ms ต่อคำขอ ทดสอบด้วย connection เดียวยิงหลายครั้ง (`r.will_close`) ถึงจะเห็น
— ถ้าเทสต์เปิดสายใหม่ทุกครั้ง (เหมือน `tests/_harness.py`) บั๊กนี้จะไม่โผล่เลย

**How to apply:** ทุกเส้นที่ตอบก่อนเรียก `_body_json()` — rate limit, auth gate, size guard
ข้อดีพ่วง: คนยิงรัวต้องเปิด TCP ใหม่ทุกครั้ง ราคาฝั่งเขาสูงขึ้นอีกชั้น
ดู [[deploy-shape-matters-serverless]] สำหรับข้อจำกัดอีกด้านของงานเดียวกัน
