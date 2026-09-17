---
name: file-store-concurrency
description: กับดักที่เสียเวลาจริงตอนทำ cross-process locking ให้ web/store.py (BUG-056) — ค่าคงที่ msvcrt, os.replace บน Windows, และชุดเทสที่ไม่แตะเส้นทางเขียน
metadata:
  type: project
---

ทำ BUG-056 (lost update ใน file store) แล้วเจอสามอย่างที่ไม่เห็นจากการอ่านโค้ด:

1. **`msvcrt.LOCK_NBLCK` ไม่มีจริง** ชื่อถูกคือ `msvcrt.LK_NBLCK` / `LK_UNLCK` (ฝั่ง POSIX คือ `fcntl.LOCK_EX`) และ `AttributeError` ไม่ถูก `except OSError` จับ
2. **`os.replace` ไม่ atomic เชิงปฏิบัติบน Windows ถ้ามีคนเปิดอ่านไฟล์ปลายทางอยู่** — วัดได้: ผู้อ่าน 2 โพรเซสยิงต่อเนื่องทำให้ `os.replace` ล้มเป็น `PermissionError` 83% (Python เปิดไฟล์โดยไม่ขอ `FILE_SHARE_DELETE`) การล็อกฝั่งเขียนอย่างเดียวไม่พอ ต้อง retry `PermissionError` ในกรอบสั้น ๆ และชื่อไฟล์ tmp ต้องมี pid ไม่งั้นสองโพรเซสเขียน `index.json.tmp` ตัวเดียวกัน
3. **ชุดเทส 64 ตัวไม่แตะเส้นทางเขียนของ file store เลย** — ใส่บั๊กที่ทำให้ `store.create` โยน `AttributeError` ทุกครั้ง เทสยังผ่าน 64/64 ถ้าจะพิสูจน์อะไรเกี่ยวกับ store ต้องเขียนสคริปต์หลายโพรเซสเอง (เธรดพิสูจน์ไม่ได้)

**Why:** เจ้าของเปิดหน้าเว็บค้างไว้แล้วรัน `mai process`/`mai bot` ในเทอร์มินัล = สองโพรเซสบน `recordings/web/` เป็นเรื่องปกติ ไม่ใช่เคสหายาก
**How to apply:** แตะ `store.py` เมื่อไรให้พิสูจน์ด้วยโพรเซสลูกจริง (`subprocess.Popen(sys.executable, ...)` + barrier แบบไฟล์ + นับของที่รอดบนดิสก์) ทั้งเคส writer×writer และ writer×reader · path ของไฟล์ล็อกต้องคำนวณตอนเรียกจาก `WEB_DIR` (เทสแพตช์ `WEB_DIR` ทีหลัง ดู [[store-paths-frozen-at-import]])
