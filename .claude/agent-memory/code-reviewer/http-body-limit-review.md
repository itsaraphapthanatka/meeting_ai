---
name: http-body-limit-review
description: วิธีตรวจ diff ที่ใส่เพดาน body / ปฏิเสธ request ก่อนอ่าน body บน stdlib http.server ของ meeting_ai — จุดที่พังจริงคือ time budget, Connection: close, chunked, และฟังก์ชันพี่น้องที่ลืมแก้
metadata:
  type: project
---

ตรวจ diff ประเภท "ปฏิเสธ request ก่อน/ระหว่างอ่าน body" (413, quota, rate limit) ใน
`meeting_ai/web/server.py` ด้วยการยิง **socket ดิบ** เสมอ ไม่ใช่แค่ `http.client` และเช็ค 4 ข้อนี้:

1. **deadline ที่เช็คระหว่าง loop ไม่ใช่เพดานเวลา** — `self.rfile.read(n)` (BufferedReader)
   บล็อกจนครบ n ไบต์ และ `socket.settimeout()` นับใหม่ทุก `recv` ที่ได้ข้อมูล ดังนั้น client
   ที่หยอดทีละ 1 ไบต์ทุก < timeout ค้างเธรดได้ไม่จำกัด (พิสูจน์แล้วกับ `_drain_rejected_body`
   ของ BUG-011: หยอด 1 ไบต์/1.5 วิ ค้าง > 20 วิ) ทางแก้ที่ทดสอบแล้วได้จริง: `rfile.read1()`
   + `settimeout(deadline - now)` ใหม่ทุกรอบ → ปิดที่ 1.99 วิเป๊ะ
2. **`self.close_connection = True` ไม่ได้ส่ง header `Connection: close`** — http.server ไม่เติมให้เอง
   client (http.client/fetch) จึงคิดว่าคอนเนกชันยัง keep-alive แล้วคำขอถัดไปเจอ RemoteDisconnected
   ต้องส่งเองผ่าน `self._send(status, body, ctype, {"Connection": "close"})`
3. **เพดานที่อิง Content-Length ข้ามได้ด้วย `Transfer-Encoding: chunked`** — `_content_length()`
   คืน 0, body ไม่ถูกอ่าน, request ผ่านไปแบบ `{}` (POST /api/meetings 20 MB chunked → 201)
4. **ฟังก์ชันพี่น้อง** — `_body_json` กับ `_read_body_to` อ่าน Content-Length คนละที่;
   helper ใหม่ (`_content_length`) มักถูกใส่ให้ตัวที่แก้เท่านั้น อีกตัวยัง 500 อยู่

ยิงทดสอบโดยไม่แตะข้อมูลจริง: สตาร์ท `server.Server(("127.0.0.1", 0), server.Handler)` ในสคริปต์
ชั่วคราว พร้อม patch `store.WEB_DIR/INDEX_PATH/SETTINGS_PATH` ไป temp dir (แบบเดียวกับ
`tests/_harness.py` LocalCase) แล้วส่ง header `Content-Length` ใหญ่แต่ body จริงสั้น
