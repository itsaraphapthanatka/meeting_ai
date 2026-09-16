---
name: http-413-needs-lingering-drain
description: ตอบ 413/4xx แล้วปิดคอนเนกชันทันทีบน stdlib http.server ทำให้ client เห็น connection reset แทน status — ต้องระบาย body ที่เหลือแบบจำกัดก่อนปิด
metadata:
  type: feedback
---

ใน `meeting_ai/web/server.py` (ThreadingHTTPServer + `protocol_version = "HTTP/1.1"`) การปฏิเสธ
request ที่ body ใหญ่เกินเพดานด้วย "ตอบ 413 + `self.close_connection = True`" **ยังไม่พอ**:
ถ้ายังมี body ค้างใน socket ตอนปิด TCP จะส่ง RST และ client (http.client / requests / fetch)
เห็นเป็น `ConnectionAbortedError 10053` ไม่ใช่ 413 ที่เราอุตส่าห์เขียน

**Why:** วัดจริงตอนแก้ BUG-011 (2026-09-16) — ยิง `POST /api/meetings` body 20 MB เข้า 127.0.0.1
ครั้งแรกได้ `status=-2 recv failed: ConnectionAbortedError` ทั้งที่โค้ดตอบ 413 ถูกต้องแล้ว
Acceptance criterion ที่เขียนว่า "ต้องได้ 413" จะสอบตกทันทีถ้าไม่ระบาย body ทิ้งก่อนปิด

**How to apply:** เวลาปฏิเสธ request ก่อนอ่าน body (413, 401 แบบ fail-fast, quota ฯลฯ)
ให้ทำตามลำดับ: ตอบ response ก่อน → แล้วอ่าน body ที่เหลือทิ้งทีละก้อน (ไม่เก็บลงแรม)
โดยจำกัดทั้งจำนวนไบต์และเวลา (ในโค้ดคือ `_drain_rejected_body()` + `LINGER_DRAIN` 64 MB /
`LINGER_SECONDS` 2.0 และตั้ง `connection.settimeout()` ระหว่างระบาย เพราะ `rfile.read(n)`
บล็อกจนครบ n) — ตรงกับที่ nginx เรียกว่า lingering close และยังกัน DoS อยู่เพราะไม่มีการ buffer

พิสูจน์ด้วย socket ดิบเสมอ: ส่ง header `Content-Length` ใหญ่แต่ส่ง body จริงแค่ 1 KB
ถ้าได้ 413 กลับมาภายในเสี้ยววินาที = ยืนยันว่าไม่ได้รออ่านจนครบก่อนปฏิเสธ
(ดู [[cloud-mode-test-recipe]] สำหรับการยิงเทสต์ HTTP โดยไม่แตะข้อมูลจริง)
