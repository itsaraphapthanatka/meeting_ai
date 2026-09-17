---
name: bug-011-body-caps
description: วิธีเทสต์ body-size caps บน http.server ของ meeting_ai (BUG-011) — raw socket ที่ต้องใช้, กับดักตอนพิสูจน์ไม่ vacuous ด้วยการแพตช์ค่าคงที่
metadata:
  type: project
---

`tests/_harness.py::_HttpCaseMixin` ตอนนี้มี `raw_request(method, path, headers, body)` ยิงผ่าน
socket ดิบ (ไม่ใช่ http.client) คืน `(status, response text, elapsed seconds)` — ต้องใช้ตัวนี้
ทุกครั้งที่ต้องคุม `Content-Length` เองแบบไม่ตรงกับความยาว body จริง (ประกาศใหญ่/ไม่ใช่ตัวเลข/
ติดลบ/ว่าง/หายไป) เพราะ `http.client` คำนวณ header นี้ให้เองตามความยาว body เสมอ ใช้แทนไม่ได้
`_do`/`post_json`/`patch_json` ก็เพิ่ม `headers=`/`timeout=` แล้ว (ใช้กับ `Authorization: Bearer`
ของ worker API) `FakeStore` เพิ่ม `verify_password` (คืน None เสมอ) กับ `set_visibility`

**กับดักสำคัญที่สุด**: เวลาจะพิสูจน์ว่าเทสต์ไม่ vacuous ด้วยการแพตช์ค่าคงที่ระดับโมดูลกลับไปเป็น
ค่าเดิม/ค่าที่ไม่มีเพดาน ให้เช็คก่อนว่าโค้ดใช้ค่านั้นเป็น **default parameter ของฟังก์ชัน**
(เช่น `def _body_json(self, limit=MAX_JSON_BODY)`) — ค่านี้ผูกตอนนิยามฟังก์ชัน ไม่มีทาง
`mock.patch.object(module, "MAX_JSON_BODY", ...)` แล้วมีผลย้อนหลังกับ call site ที่ไม่ส่ง
`limit` มาเอง (ต้องแพตช์ตัวเมธอดทั้งฟังก์ชันแทน) ส่วนค่าที่ถูกอ้างเป็น **global lookup ตอนเรียก**
(เช่น `self._body_json(MAX_JSON_TRANSCRIPT)` หรือใน `_clean_segments` เอง) แพตช์ค่าคงที่ได้จริง

**อย่าแพตช์ค่าคงที่ที่เทสต์เอาไปคูณสร้างลิสต์** (เช่น `[...] * server.MAX_SEGMENTS`) ให้ใหญ่แบบ
"ไม่มีเพดาน" (เช่น 10**9) — พลาดมาแล้วจริง: python กิน RAM ไป 18 GB ก่อนถูก `taskkill` ทัน
ถ้าจะพิสูจน์เพดานจำนวนรายการ ให้แพตช์ที่ตัวฟังก์ชัน/guard แทน หรือยิง payload ขนาดคงที่
(ไม่อิงค่าคงที่) เทียบสองรอบ (ค่าจริง vs แพตช์ให้ใหญ่กว่า payload)

เจอ regression gap จริงที่ไม่ใช่ 1 ใน 6 AC ของตั๋วระหว่างทาง (ตรงกับที่ code-reviewer เจอวันเดียวกัน
ใน `.claude/agent-memory/code-reviewer/http-body-limit-review.md`): `_read_body_to()` (track
upload/worker audio/live clip) ยังอ่าน `int(Content-Length or 0)` ตรงๆ ไม่ผ่าน `_content_length()`
→ 500 อยู่ และ `Transfer-Encoding: chunked` (ไม่มี Content-Length) หลบทุกเพดานที่มีทั้งหมด —
เขียนเป็น `@unittest.expectedFailure` ในคลาสแยกก่อน (ไม่เงียบไว้ เพราะเป็นช่องโหว่จริงของฟีเจอร์
ที่กำลังเทสต์อยู่ ไม่ใช่แค่ "เรื่องอื่น") เช็ค memory ไฟล์ของ role อื่นที่แก้ตั๋วเดียวกันวันเดียวกัน
เสมอ (`.claude/agent-memory/<role>/`) ก่อนปิดงาน — คุ้มค่าจริง: security-engineer ตรวจต่อแล้วเจอ
request smuggling/keep-alive desync เพิ่มอีกชุดจากการแก้รอบแรกเอง (`docs/runbooks/security/
AUDIT-2026-09-16-bug011-body-caps.md`) backend-dev แก้ต่อ แล้ว coordinator สั่งกลับมาให้แปลง
xfail เป็น assert ปกติ + เพิ่มเทสต์ชุดใหม่ (สมมติเดิมว่า "แก้เสร็จแล้วจบ" ผิด — ตั๋วเดียวกันวนกลับมา
ให้ทำต่ออีกรอบเป็นเรื่องปกติ อย่าลบไฟล์เทสต์ทิ้งแค่เพราะ xfail มัน unexpected-success)

**เมื่อ xfail กลับเป็น unexpected-success**: ก่อนแปลงเป็น assert ปกติ ต้อง diff โค้ด production
ที่เปลี่ยนจริงก่อนเสมอ (`git diff -- <ไฟล์นั้น>`) อย่าเดา status code ใหม่จากความจำ/สมมติฐาน —
รอบนี้ `git status` โชว์ `meeting_ai/web/server.py` เป็น working-tree change ที่ยังไม่ commit
(ไม่ใช่ commit ใหม่ใน log) ต้องอ่าน diff ตรงๆ ถึงจะรู้ว่าโครงสร้างเปลี่ยนไปมาก (`_begin_body()`
ใหม่ทำงานก่อน routing ทั้งหมด, `Handler.body_bytes` เป็น state ใหม่ที่หลายจุดพึ่งพา)

**เทสต์ "sibling function ที่เคย 500" อาจไม่ได้รับการปกป้องจากตัวมันเองอีกต่อไปหลังรีแฟกเตอร์** —
ตรวจ call path จริงก่อนเชื่อว่า "ปลอมจุดที่เคยพังจุดเดียวก็พอ" (รายละเอียดเต็มใน docs/LEARNINGS.md
หัวข้อ "รอบ hardening ที่สอง") ต้องปลอมทั้ง `_begin_body()` (no-op) และฟังก์ชันเป้าหมายแบบเดิม
พร้อมกันถึงจะเห็นพฤติกรรมพังแบบเดิมกลับมา ปลอมจุดเดียวไม่พอเพราะ gate ใหม่ดักไว้ก่อนถึง

**พิสูจน์ "1 คอนเนกชัน = 1 response" (request smuggling) ต้องย้อนกลไกทีละจุด ไม่ใช่ทั้งฟังก์ชัน**
ย้อนกฎเดียวใน `_send()` แล้วดูว่าเทสต์ไหนล้ม/ไหนไม่ล้ม — ถ้าล้มไม่ครบทุกเคสตามที่ควร (เช่น เคสที่มี
กลไกป้องกันซ้อนกันสองชั้นยังผ่านอยู่) นั่นคือสัญญาณที่ดีว่าเทสต์แต่ละตัวจับกลไกคนละจุดจริง ไม่ใช่
บั๊กของเทสต์เอง — และคู่กับเทสต์ "control": GET เปล่าสองอันบนคอนเนกชันเดียวต้องได้ 2 responses
เสมอ กันคนมา "แก้" ด้วยการปิดทุกคอนเนกชัน (ซึ่งจะทำให้เทสต์ smuggling ผ่านหมดแบบไม่มีความหมาย)

**อย่าใส่ wall-clock assertion ในชุดเทสต์ถาวร** (เช่น "ต้องปิดคอนเนกชันภายใน N วินาที" สำหรับ
slowloris) — จะ flake บน CI ที่โหลดไม่แน่นอน ให้ขอเลขที่ dev วัดจริงมาแล้วบันทึกไว้ใน
docs/LEARNINGS.md แทน (บันทึกของรอบนี้: 21.62s→2.03s เธรดเดี่ยว, 100 คอนเนกชันปล่อยครบใน 2.59s)
