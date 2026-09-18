"""ของที่ store.py (ไฟล์ JSON) กับ pgstore.py (Postgres) ต้องทำเหมือนกันเป๊ะ ๆ.

สองโมดูลนั้นเปิดชื่อฟังก์ชันชุดเดียวกันให้ server.py เรียกโดยไม่ต้องรู้ว่าอยู่โหมดไหน
(ดู backend.py) ของที่ "ไม่เกี่ยวกับที่เก็บข้อมูลเลย" — สร้าง id, ตรวจรูปแบบ id, จัดรูปเวลา,
ดึงข้อความไว้ค้นหา — จึงไม่มีเหตุผลให้มีสองชุด

**ทำไมต้องรวม ไม่ใช่แค่เรื่องความสวยงาม** (BACKLOG #26): พอมีสองชุด มันเพี้ยนจากกันจริง และ
เพี้ยนไปทางเดียว — ฝั่ง Postgres ถูกทำให้ทนค่า None ไปแล้วสามจุด ส่วนฝั่งไฟล์ยังไม่ถูกแก้ตาม
วัดตอนรวม (2026-09-18):

    fmt_time(None)                      store.py -> TypeError   · pgstore.py -> "00:00"
    transcript_text ที่ text=None       store.py -> AttributeError · pgstore.py -> ""
    _snippet(None, "ก")                 store.py -> AttributeError · pgstore.py -> ""

กับดักที่ทำให้มันหลุดมาถึงตรงนี้ได้: `meta.get("duration", 0)` คืนค่า 0 เฉพาะตอน**ไม่มีคีย์**
ถ้าคีย์มีอยู่แต่ค่าเป็น `null` (ข้อมูลเก่าที่เขียนไว้ก่อนมี sanitize.py หรือไฟล์ที่ถูกแก้มือ)
มันคืน None แล้วส่งต่อเข้าฟังก์ชันพวกนี้ตรง ๆ

ไฟล์นี้จึงเก็บ **ฉบับที่ทนค่า None** ไว้ชุดเดียว
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime

# id การประชุม: YYYYMMDD-HHMMSS-<hex 6 ตัว> — ใช้เป็นชื่อไฟล์และส่วนหนึ่งของ URL
ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
SNIPPET_PAD = 70


def new_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def valid_id(mid: str) -> bool:
    """กัน path traversal — id ต้องตรงรูปแบบที่เราสร้างเท่านั้น.

    fullmatch ไม่ใช่ match: `$` ของ re ยอมให้มีตัวขึ้นบรรทัดใหม่ปิดท้ายได้
    "<id>" กับ "<id>ขึ้นบรรทัดใหม่" จึงเคยผ่านทั้งคู่ ทั้งที่ชื่อไฟล์ไม่เหมือนกัน
    """
    return bool(ID_RE.fullmatch(mid or ""))


def fmt_time(sec: float | None) -> str:
    """วินาที -> HH:MM:SS — None ถือเป็นศูนย์ ไม่ใช่ข้อยกเว้น.

    ค่านี้มาจาก `meta.get("duration", 0)` / `seg.get("start", 0)` ซึ่งยัง None ได้
    ถ้าคีย์มีอยู่แต่ค่าเป็น null และผู้เรียกทั้งหมดคือหน้าเว็บกับตัว export — พังตรงนั้น
    เท่ากับเปิดไฟล์ไม่ได้ทั้งหน้า เพราะเวลาตัวเดียวที่ไม่มีค่า
    """
    m, s = divmod(int(sec or 0), 60)
    h, m = divmod(m, 60)
    # ไม่ถึงชั่วโมงคืน MM:SS — เปลี่ยนเป็น HH:MM:SS เสมอคือเปลี่ยนเวลาที่ผู้ใช้เห็นทุกอัน
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcript_text(detail: dict) -> str:
    """ข้อความล้วนสำหรับค้นหา."""
    return " ".join((s.get("text") or "").strip()
                    for s in detail.get("segments", [])).strip()


def timestamped(detail: dict) -> str:
    """บทถอดเสียงพร้อมเวลาและชื่อผู้พูด — รูปแบบที่ผู้ใช้ดาวน์โหลดไปอ่าน."""
    parts = []
    for s in detail.get("segments", []):
        head = f"[{fmt_time(s.get('start', 0))} - {fmt_time(s.get('end', 0))}]"
        speaker = s.get("speaker")
        if speaker:
            head += f" {speaker}:"
        parts.append(f"{head} {(s.get('text') or '').strip()}")
    return "\n".join(parts)


def snippet(text: str | None, query: str) -> str:
    """ท่อนข้อความรอบคำค้น พร้อม … บอกว่าถูกตัดหัว/ท้าย."""
    text = text or ""
    pos = text.lower().find((query or "").lower())
    if pos < 0:
        return ""
    start = max(0, pos - SNIPPET_PAD)
    end = min(len(text), pos + len(query) + SNIPPET_PAD)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")
