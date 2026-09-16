---
name: http-413-needs-lingering-drain
description: ตอบ 413/4xx บน stdlib http.server ต้องส่ง Connection close + ระบาย body ที่เหลือแบบมี deadline จริง ไม่งั้น client เห็น connection reset และคนหยดข้อมูลยึดเธรดได้
metadata:
  type: feedback
---

ใน `meeting_ai/web/server.py` (ThreadingHTTPServer + `protocol_version = "HTTP/1.1"`) การปฏิเสธ
request ที่ body ใหญ่เกินเพดานต้องทำครบ **สามอย่าง** ไม่ใช่แค่ตอบ 413:

1. `send_header("Connection", "close")` จริง ๆ — `self.close_connection = True` เป็นสถานะภายใน
   ของ `http.server` ไม่มีอะไรออกไปบนสาย client จึงใช้คอนเนกชันเดิมต่อแล้วไปตายที่ **คำขอถัดไป**
   (`RemoteDisconnected`) คือ error โผล่ผิดคำขอ
2. ระบาย body ที่เหลือแบบจำกัดก่อนปิด — ปิดทั้งที่มีข้อมูลค้าง = TCP RST = client เห็น
   `ConnectionAbortedError 10053` แทน 413
3. เวลาที่ใช้ระบายต้องเป็น **deadline จริง**: `self.rfile` เป็น `BufferedReader` (`read(n)` รอจนครบ n
   ต้องใช้ `read1`) และ `settimeout()` วัดความเงียบต่อหนึ่ง recv ไม่ใช่เวลารวม — ต้อง
   `settimeout(max(0.05, deadline - time.monotonic()))` ใหม่ทุกรอบ

**Why:** วัดจริงตอนแก้ BUG-011 (2026-09-16) — รอบแรกเขียนแค่ข้อ 2 แล้วเคลมในคอมเมนต์ว่ากัน
slowloris ได้ security-engineer ยิงกลับด้วยการหยด 1 ไบต์ทุก 0.4 วิ ยึดเธรดไว้ได้ **21.62 วินาที**
ด้วยทราฟฟิก 74 ไบต์ และ 100 คอนเนกชัน (7.4 KB) ยึดได้ครบ 100/100 หลังแก้เหลือ **2.03 วินาที**
(100 คอนเนกชันพร้อมกัน ช้าสุด 2.55 วิ) — บน Vercel เวลาที่ถูกยึดคือเงินตาม `maxDuration`

**How to apply:** ทุกเส้นทางที่ตอบก่อนอ่าน body จนหมด (401/403/404/405/413) ต้องปิดคอนเนกชัน
พร้อมส่ง header ไม่งั้นไบต์ที่ค้างจะถูกอ่านเป็น "คำขอถัดไป" = 2 คำตอบใน 1 คอนเนกชัน
(request smuggling — รางวัลคือคุกกี้ session ของคนอื่นเมื่อมี proxy ที่ pool คอนเนกชัน)
กฎที่ใช้ในโค้ดตอนนี้: **คอนเนกชันที่เคยมี request body จะไม่ถูกใช้ซ้ำ** (`Handler.body_bytes`
ตั้งใน `_begin_body()` และ `_send()` เป็นคนตัดสินใจ) คู่กับปฏิเสธ `Transfer-Encoding` ที่ไม่ใช่
identity (501) และ `Content-Length` ที่ซ้ำ/ไม่ใช่เลขล้วน (400) เพราะ `int()` รับ `1_0`, `+10`, `" 10 "`
ซึ่ง proxy ข้างหน้าอ่านไม่เหมือนเรา

ระวัง: `Handler.timeout` (เดิมเป็น `None`) เมื่อตั้งเป็นตัวเลขแล้ว `http.server` จะเรียก
`log_error("Request timed out")` → `log_message` ตั้งแต่ยังไม่มี `self.path` → `AttributeError`
หลุดเป็น traceback ของ socketserver ต้อง `getattr(self, "path", "")` ใน `log_message` ด้วย

พิสูจน์ด้วย socket ดิบเสมอ: นับ `HTTP/1.1 ` ในสิ่งที่ตอบกลับมาบนคอนเนกชันเดียว (ต้องได้ 1),
วัดเวลาที่คนหยดข้อมูลถูกปล่อย, และลองใช้คอนเนกชันซ้ำหลัง 413
(ดู [[cloud-mode-test-recipe]] สำหรับการยิงเทสต์ HTTP โดยไม่แตะข้อมูลจริง)
