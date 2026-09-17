---
name: keepalive-desync-checklist
description: server.py ตั้ง protocol_version = HTTP/1.1 (keep-alive) แต่ไม่รู้จัก Transfer-Encoding และตอบ 4xx โดยไม่อ่าน body — ทุก request ที่ body ไม่ถูกอ่านจนหมดจะกลายเป็น request ถัดไป
metadata:
  type: project
---

`meeting_ai/web/server.py` ใช้ `protocol_version = "HTTP/1.1"` บน `ThreadingHTTPServer` = keep-alive เปิดตลอด
stdlib `http.server` **ไม่ถอด chunked ให้** และไม่มีใครระบาย body ทิ้ง → ยิงทีเดียวได้ 2 responses (พิสูจน์แล้ว 2026-09-16)

**Why:** ทดสอบตอนตรวจ BUG-011 ได้ `responses=2` จาก 4 รูปแบบ: `Transfer-Encoding: chunked` (ไม่มี Content-Length
= `_body_json()` คืน `{}` โดยไม่แตะ socket), Content-Length สั้นกว่าของจริง, Content-Length ซ้ำสองบรรทัด
(Python ใช้ค่าแรก), และทุกเส้นที่ตอบ 401/403/404/405 **ก่อน** อ่าน body (auth gate `server.py:552`,
permission block `server.py:1104`, `_read_body_to` ที่ปฏิเสธไฟล์ใหญ่แล้วไม่ปิดคอนเนกชัน)
`int()` ของ Python ยังรับ `1_0`, `+10`, ` 10 ` เป็นเลขด้วย = parser differential กับ proxy ทุกตัว
ตัวโค้ดเองเชื่อ `X-Forwarded-Proto` (`server.py:236`, `:1328`) แปลว่าการรันหลัง reverse proxy เป็น config ที่รองรับ
→ ข้อนี้ไม่ใช่ทฤษฎี

**How to apply:** ตรวจ diff ที่แตะการอ่าน body เมื่อไร ให้ยิง socket ดิบเช็คสามอย่างเสมอ
(นับ `buf.count(b"HTTP/1.1 ")` บนคอนเนกชันเดียว): (1) TE: chunked, (2) CL สั้นกว่าจริง, (3) 4xx ที่ตอบก่อนอ่าน body
ถ้าได้ 2 = desync แก้ด้วยกฎเดียว "ตอบกลับตอน body ยังไม่ถูกอ่านหมด = `self.close_connection = True`"
และอย่าลืมว่า `close_connection = True` ไม่ได้ส่ง header `Connection: close` ให้อัตโนมัติ (ต้องใส่เอง)
ดู [[http-deadline-is-not-a-timeout]] สำหรับเรื่องการระบาย body ทิ้งอย่างปลอดภัย
