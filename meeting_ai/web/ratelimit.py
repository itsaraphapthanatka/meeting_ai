"""ตัวนับคำขอแบบ fixed window ในหน่วยความจำของโพรเซสเดียว (stdlib ล้วน).

ใช้กับเส้นที่ยิงได้โดยไม่ต้องล็อกอินและมีราคาแพง — ล็อกอินเรียก scrypt (n=2**14) ซึ่งกิน
หน่วยความจำราว 16 MB + CPU จริงต่อหนึ่งครั้ง request เล็กๆ จึงซื้องานหนักของเซิร์ฟเวอร์ได้

**ที่นี่ไม่ใช่ตัวกันหลัก** บน Vercel: แต่ละ invocation เป็นคนละโพรเซส ตัวนับนี้จะว่างเปล่า
เกือบทุกครั้ง ตัวจริงคือ pgstore.rate_hit() ที่นับร่วมกันในฐานข้อมูล (ดู docs/tickets/BUG-010)
ชั้นนี้มีไว้สองอย่าง
  1) คำขอที่ถูกบล็อกไปแล้วตอบได้โดยไม่ต้องแตะ DB เลย — ถูกกว่าการ hash หลายพันเท่า
  2) ถ้า DB ล่มหรือยังไม่ได้ db-init ยังเหลือการจำกัดระดับโพรเซส ไม่ใช่เปิดโล่งทั้งบาน
"""

from __future__ import annotations

import threading
import time

# กันหน่วยความจำบวมเมื่อคนยิงสลับ IP ไปเรื่อยๆ
MAX_KEYS = 4096
# เพดานแข็ง: ถ้าทุกรายการเป็นรายการที่ "ถูกบล็อกอยู่จริง" จนตัดแต่งแบบปกติไม่ได้
# ยังต้องมีขีดสูงสุดไว้ ไม่งั้นหน่วยความจำโตได้ไม่จำกัด (แต่ละรายการเล็กมาก ~100 ไบต์)
HARD_MAX_KEYS = 4 * MAX_KEYS
# ค่าที่แปลว่า "ตัวนับกลางบล็อก key นี้ไว้แล้ว" (ดู block())
_BLOCKED = 1 << 30

_lock = threading.Lock()
_hits: dict[str, list] = {}   # key -> [expires_at (monotonic), count]


def _prune(now: float, limit: int, keep: str) -> None:
    """ทำที่ว่างให้คีย์ใหม่ — เรียกใต้ _lock เท่านั้น.

    กติกาสำคัญสองข้อ (เคยพลาดมาแล้วตอนรีวิว BUG-010):
    1) **ห้ามทิ้ง `keep`** คือคีย์ที่กำลังนับอยู่ในคำขอนี้ ของเดิมเรียงตามจำนวนครั้งแล้วตัด
       ทำให้คีย์ที่เพิ่งนับ (count=1 อยู่ท้ายกลุ่มที่เท่ากัน) ถูกทิ้งในการเรียกครั้งเดียวกัน
       ผลคือพอคีย์เต็มเพดาน ตัวนับ "หยุดนับ" ทั้งชั้นโดยไม่มีใครรู้
    2) ทิ้งเฉพาะรายการที่ **ยังไม่ถูกบล็อก** (count <= limit) โดยเอาที่ใกล้หมดอายุที่สุดก่อน
       รายการที่ถูกบล็อกอยู่คือรายการที่กำลังทำร้ายเรา ต้องอยู่จนหมดหน้าต่างของมัน
    """
    for key in [k for k, v in _hits.items() if v[0] <= now and k != keep]:
        del _hits[key]
    excess = len(_hits) - MAX_KEYS + 1          # +1 เผื่อที่ว่างให้คีย์ใหม่
    if excess <= 0:
        return
    spare = sorted((k for k, v in _hits.items() if k != keep and v[1] <= limit),
                   key=lambda k: _hits[k][0])
    for key in spare[:excess]:
        del _hits[key]
    excess = len(_hits) - HARD_MAX_KEYS + 1
    if excess > 0:                               # เหลือแต่รายการที่ถูกบล็อก — จำเป็นต้องทิ้งบ้าง
        for key in sorted((k for k in _hits if k != keep), key=lambda k: _hits[k][0])[:excess]:
            del _hits[key]


def hit(key: str, limit: int, window: float) -> float:
    """นับหนึ่งครั้ง คืนจำนวนวินาทีที่ต้องรอ (0.0 = ยังไม่เกินโควตา).

    หน้าต่างไม่ถูกต่ออายุเมื่อถูกบล็อก — คนที่ยิงรัวรอไม่เกิน window วินาทีเสมอ ไม่มีล็อกถาวร
    """
    now = time.monotonic()
    with _lock:
        entry = _hits.get(key)
        if entry is None:
            if len(_hits) >= MAX_KEYS:
                _prune(now, limit, keep=key)     # ตัดแต่ง *ก่อน* ใส่ ไม่ใช่หลังใส่
            entry = [now + window, 0]
            _hits[key] = entry
        elif entry[0] <= now:
            entry[0], entry[1] = now + window, 0
        entry[1] += 1
        over = entry[1] > limit
        wait = entry[0] - now
    return max(1.0, wait) if over else 0.0


def block(key: str, seconds: float) -> None:
    """จำว่า key นี้ถูกบล็อกอยู่ (ผลตัดสินมาจากตัวนับกลางใน DB) จะได้ไม่ต้องถาม DB ซ้ำ.

    ตั้งตัวนับเป็นค่าสูงสุดไปเลย ไม่ใช่ +1 — เพราะจำนวนครั้งจริงอยู่ที่ฐานข้อมูล ถ้าเก็บแค่
    จำนวนที่โพรเซสนี้เห็น hit() ครั้งต่อไปจะตอบว่า "ยังไม่เกิน" ทั้งที่ตัวนับกลางบล็อกไปแล้ว
    """
    if seconds <= 0:
        return
    now = time.monotonic()
    with _lock:
        entry = _hits.get(key)
        if entry is None and len(_hits) >= MAX_KEYS:
            # limit = _BLOCKED - 1 → รายการปกติทิ้งได้ รายการที่ถูกบล็อกอยู่ทิ้งไม่ได้
            _prune(now, _BLOCKED - 1, keep=key)
        _hits[key] = [max(now + seconds, entry[0] if entry else 0.0), _BLOCKED]


def reset(key: str) -> None:
    """ล้างตัวนับ — ใช้เมื่อคำขอสำเร็จจริง (เช่นล็อกอินผ่าน) คนใช้งานจริงจะได้ไม่สะสมโควตา."""
    with _lock:
        _hits.pop(key, None)


def clear() -> None:
    """ล้างทั้งหมด — สำหรับเทสต์."""
    with _lock:
        _hits.clear()
