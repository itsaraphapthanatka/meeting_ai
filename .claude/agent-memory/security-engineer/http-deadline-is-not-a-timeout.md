---
name: http-deadline-is-not-a-timeout
description: บน http.server ของ stdlib การเช็ค deadline ระหว่างรอบ loop ไม่ใช่ timeout จริง — rfile.read(n) บล็อกจนครบ n และ socket timeout รีเซ็ตทุกครั้งที่มีข้อมูลเข้า ทำให้ผู้โจมตีหยอดทีละไบต์ตรึงเธรดได้
metadata:
  type: project
---

เจอตอนตรวจ BUG-011 (`fix/bug-011-body-size-caps`, 2026-09-16) — `_drain_rejected_body()` เขียนว่า
"จำกัดทั้งจำนวนไบต์และเวลา (กัน slowloris)" แต่ยิงจริงแล้วตรึงเธรดได้ 21.6 วินาที ด้วยการหยอด 1 ไบต์ทุก 0.4 วินาที

**Why:** `self.rfile` เป็น `io.BufferedReader` (`rbufsize = -1`) → `read(n)` **ไม่คืนค่าจนกว่าจะครบ n ไบต์หรือ EOF**
ส่วน `connection.settimeout(t)` นับจาก "เงียบติดกัน t วินาที" ไม่ใช่เวลารวม เมื่อผู้โจมตีหยอดข้อมูลเรื่อย ๆ
timeout จึงไม่เคยทำงาน และบรรทัด `while ... time.monotonic() < deadline` ไม่ถูกประเมินอีกเลย
ซ้ำร้าย `BaseHTTPRequestHandler.timeout` เป็น `None` โดยปริยาย (`Handler` ไม่ override) → ทั้งเซิร์ฟเวอร์
ไม่มี socket timeout อยู่แล้ว: ส่ง header ไม่จบหรือส่ง body ไม่ครบ = เธรดค้างถาวรบน `ThreadingHTTPServer`

**How to apply:** เวลารีวิวโค้ดที่อ้างว่า "มีเพดานเวลา" บน http.server ให้ถามสองข้อ —
(1) การอ่านแต่ละรอบคืนค่าเร็วไหม (`read1()` คืนหลัง raw read ครั้งเดียว, `read()` ไม่คืน) และ
(2) timeout ที่ตั้งเป็นเวลารวมหรือเวลาเงียบ ถ้าไม่ใช่ทั้งสองอย่าง เพดานนั้นเป็นแค่คอมเมนต์
พิสูจน์เสมอด้วย socket ดิบ + trickle 1 ไบต์ทุก 0.4 วินาที แล้ววัด "เวลาที่เซิร์ฟเวอร์ปิดคอนเนกชัน"
ไม่ใช่แค่ ttfb ของ response (ttfb ของ 413 เป็น 0.00s ทั้งที่เธรดยังถูกตรึงอยู่ — ดูแค่ status จะไม่เห็นปัญหาเลย)
ดู [[keepalive-desync-checklist]] สำหรับปัญหาคู่แฝดในคอมมิตเดียวกัน
