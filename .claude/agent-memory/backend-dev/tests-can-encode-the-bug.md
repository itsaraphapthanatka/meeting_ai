---
name: tests-can-encode-the-bug
description: Before fixing an old review finding, grep tests/ for assertions that lock in the buggy behaviour; also the tests/ import convention and the fact that suite counts shift when another agent shares the worktree
metadata:
  type: feedback
---

ก่อนแก้ finding เก่า ให้ `grep` ใน `tests/` หา assertion ที่ **ยืนยันพฤติกรรมที่เป็นบั๊กเอง**

**Why:** `tests/test_p0_02_share_gate.py::test_share_entry_sets_cookie` ยืนยันว่า `GET /s/<token>`
ต้องมี `Set-Cookie: mai_share=` — ซึ่งคือช่อง fixation ของ BUG-016 พอดี ถ้าไม่เจอก่อนจะไปเจอตอน
รันชุดเต็มแล้วเข้าใจผิดว่าตัวเองทำพัง เทสต์ที่เขียนไว้ตอนแก้ P0 บันทึก "สิ่งที่ระบบทำ" ไม่ใช่
"สิ่งที่ระบบควรทำ" เสมอไป — แก้ชื่อเทสต์ + เขียนเหตุผลว่าสัญญาเปลี่ยนเพราะตั๋วใบไหน

**How to apply:**
- ธรรมเนียม import ของ `tests/` คือ `from _harness import ...` (ไม่ใช่ `from tests._harness import`)
  ทั้งสองแบบผ่าน `discover -s tests` แต่แบบ `tests._harness` ทำให้ `python -m unittest tests.test_x`
  ของไฟล์อื่นในคำสั่งเดียวกันพัง (`ModuleNotFoundError: _harness`) และได้โมดูล harness สองก๊อป
- จำนวนเทสต์/เวลาที่รันได้อาจไม่ใช่ของเรา: worktree นี้มีเอเจนต์อื่นทำงานพร้อมกัน (`git status`
  โผล่ไฟล์ที่เราไม่ได้แตะ) ชุดเต็มจาก 264 → 288 เพราะมีคนเพิ่ม 4 เทสต์ของตั๋วอื่นระหว่างทาง
- `tests/test_bug_011_body_size_caps.py` มี 2 เคสที่วัดเวลา/keep-alive จริง → **flake เมื่อเครื่องโหลดหนัก**
  (สองชุดทดสอบรันพร้อมกัน เวลาเดิม 87 วิ → 528 วิ แล้วล้ม 2 เคส) รันไฟล์นั้นเดี่ยว ๆ ซ้ำ 3 รอบ
  เพื่อยืนยันว่าไม่ใช่ของเราก่อนจะไปไล่หาสาเหตุ
