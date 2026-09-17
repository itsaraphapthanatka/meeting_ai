---
name: prefix-string-path-guard
description: str.startswith containment checks let sibling paths with the same prefix through; how to PoC the static guard honestly by patching server.STATIC_DIR instead of littering the repo
metadata:
  type: feedback
---

`str(target).startswith(str(root))` ไม่ใช่การตรวจว่า "อยู่ในโฟลเดอร์นี้" — `.../web/static_backup`
ขึ้นต้นด้วย `.../web/static` เหมือนกัน ใช้ `Path.is_relative_to(root)` (BUG-015 / `_static()`)

**Why:** ด่านเดิมกัน `/static/../../../.env` ได้อยู่แล้ว (ออกนอกคำนำหน้า) สิ่งที่มันไม่กันคือ
โฟลเดอร์/ไฟล์ **พี่น้อง** ที่ชื่อขึ้นต้นเหมือนกัน → `GET /static/../static_backup/secret.txt`
อ่านไฟล์ได้ 200 จริง (วัดแล้ว) ตอนนี้ยิงไม่ได้เพราะซอร์สทรีไม่มีเพื่อนบ้านชื่อแบบนั้น —
รายงานตามจริงว่าเป็น hardening ไม่ใช่ช่องที่ยิงได้ อย่าตีเป็น P1 เพื่อให้ตั๋วดูใหญ่

**How to apply:** PoC เส้นไฟล์ static ทำได้โดยไม่สร้างขยะในรีโป: `mock.patch.object(server,
"STATIC_DIR", tmp/"static")` แล้วสร้าง `tmp/static_backup/` ข้าง ๆ — โค้ดที่ตัดสินใจยังเป็นของจริง
ทั้งหมด เปลี่ยนแค่ราก · พิสูจน์ว่าเทสต์ไม่ vacuous ด้วย subclass ของ `type(Path())` ที่ override
`is_relative_to` ให้ทำงานแบบ `startswith` + patch `Path.resolve` ให้คืนคลาสนั้น แล้วคำขอเดิมต้อง
กลับมา 200 (ถูกกว่าการแก้ซอร์สจริงแล้ว revert)
