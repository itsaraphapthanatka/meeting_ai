"""บันทึกการทำงานของไลบรารี — แยกจาก "ข้อความที่ผู้ใช้อ่าน" ซึ่งยังเป็น print() ใน cli.py.

**ทำไมต้องเปลี่ยนจาก print() (BACKLOG #35)**

1. `print()` ไปที่เดียวเสมอ ปิดไม่ได้ กรองไม่ได้ ใส่เวลาให้ไม่ได้ เครื่อง worker ที่รันเป็น
   service เขียนทุกอย่างลง `logs/worker.log` เป็นสายข้อความไม่มีเวลา พอมีปัญหา "บอทหลุดตอนไหน"
   ก็ตอบไม่ได้
2. **`print()` ทำให้งานพังได้จริง** คอนโซล cp874 ของเครื่องเจ้าของเขียนภาษาไทยไม่ได้
   `UnicodeEncodeError` จากบรรทัดเตือนจึงเคยทำให้ทั้งงานล้ม — จนต้องมี `stt._notice()` และ
   `blobstore._notice()` ลองเขียนสองรอบ (ไทย แล้วค่อย ascii) มาก่อนหน้านี้ ส่วน `logging`
   จับข้อยกเว้นของ handler ไว้เองอยู่แล้ว บรรทัด log จึงไม่มีทางล้มงานที่เรียกมัน

**สิ่งที่ตั้งใจให้เหมือนเดิมเป๊ะ ๆ**: ข้อความและปลายทาง — INFO ออก stdout, WARNING ขึ้นไปออก
stderr เหมือนที่ `print(..., file=sys.stderr)` เคยทำ และรูปแบบเป็น "ข้อความล้วน" ไม่มีคำนำหน้า
เจ้าของอ่าน `logs/worker.log` อยู่ทุกวัน การเปลี่ยนรูปแบบต้องเป็นการตัดสินใจ ไม่ใช่ผลข้างเคียง
ของการย้ายบ้าน (`setup(stamp=True)` เปิดเวลานำหน้าได้เมื่อต้องการ)
"""

from __future__ import annotations

import logging
import os
import sys

ROOT = "meeting_ai"
STAMP_FORMAT = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"
_configured = False


def get(name: str) -> logging.Logger:
    """logger ของโมดูล — เรียกจากไลบรารีได้เลย ไม่ต้องรอใครตั้งค่า."""
    return logging.getLogger(name)


class _MaxLevel(logging.Filter):
    """ให้ handler ของ stdout รับเฉพาะระดับต่ำกว่า WARNING (ที่เหลือเป็นของ stderr)."""

    def __init__(self, level: int) -> None:
        super().__init__()
        self.level = level

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= self.level


def _stream(fh):
    """สตรีมที่เขียนภาษาไทยแล้วไม่ระเบิด — คอนโซล Windows ไทยเป็น cp874."""
    try:
        fh.reconfigure(errors="replace")
    except (AttributeError, OSError, ValueError):
        pass        # สตรีมที่ reconfigure ไม่ได้ (serverless, ไฟล์ที่ถูกแทน) ปล่อยตามเดิม
    return fh


def setup(level: int | None = None, stamp: bool = False, force: bool = False) -> None:
    """ตั้งค่าที่ "ขอบนอกสุด" ของโปรแกรมเท่านั้น (cli.py) — ไลบรารีห้ามเรียก.

    เรียกซ้ำไม่ทำอะไร เว้นแต่สั่ง force เพราะ CLI หลายคำสั่งเรียกต่อกันได้ในโพรเซสเดียว
    MAI_LOG_LEVEL ตั้งเป็น DEBUG/INFO/WARNING ได้ ไว้ไล่ปัญหาโดยไม่ต้องแก้โค้ด
    """
    global _configured
    if _configured and not force:
        return

    root = logging.getLogger(ROOT)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    if level is None:
        level = getattr(logging, (os.environ.get("MAI_LOG_LEVEL") or "INFO").upper(), logging.INFO)
    fmt = logging.Formatter(STAMP_FORMAT if stamp else "%(message)s")

    out = logging.StreamHandler(_stream(sys.stdout))
    out.setFormatter(fmt)
    out.addFilter(_MaxLevel(logging.INFO))

    err = logging.StreamHandler(_stream(sys.stderr))
    err.setFormatter(fmt)
    err.setLevel(logging.WARNING)

    root.setLevel(level)
    root.addHandler(out)
    root.addHandler(err)
    # ไม่ให้ไหลขึ้น root logger ของ Python ไม่งั้นโปรแกรมที่ import เราแล้วตั้ง basicConfig ไว้
    # จะได้ข้อความซ้ำสองรอบ
    root.propagate = False
    _configured = True


def reset() -> None:
    """ให้เทสต์ตั้งค่าใหม่ได้ — โค้ดจริงไม่ต้องเรียก."""
    global _configured
    _configured = False
    root = logging.getLogger(ROOT)
    for handler in list(root.handlers):
        root.removeHandler(handler)
