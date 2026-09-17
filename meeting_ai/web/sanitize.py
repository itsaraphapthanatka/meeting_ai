"""กติกากลางของรูปร่างข้อมูลการประชุมที่มาจากฝั่งที่เชื่อไม่ได้เต็มร้อย.

มีสองทางที่เขียนบทถอดเสียงลงคลังได้ และทั้งสองทางเป็น input จากภายนอกทั้งคู่:
  1. ผู้ใช้แก้ transcript เอง  -> PATCH /api/meetings/{id} -> server._clean_segments()
  2. worker ส่งผลงานกลับมา     -> POST /api/worker/jobs/{id}/result -> jobs.apply_result()

ทาง (2) เคยไม่ตรวจอะไรเลย (BUG-048) ทั้งที่ WORKER_TOKEN แปลว่า "รับจ้างถอดเสียง" ไม่ใช่
"เจ้าของข้อมูล" — ค่า NaN/Infinity ที่หลุดลงไปทำให้ GET การประชุมได้ JSON ที่ parse ไม่ได้
และ export พัง 500 ถาวร (เวลาถูกแปลงด้วย int() ซึ่ง NaN = ValueError, inf = OverflowError)
เจ้าของแก้เองไม่ได้ด้วยเพราะเปิดการประชุมไม่ขึ้น

โมดูลนี้จึงถือกติกาไว้ที่เดียว: ใช้แต่ stdlib ไม่ import โมดูลอื่นในโปรเจกต์ จึงอยู่ใต้ทั้ง
server.py และ jobs.py ได้ (jobs.py import server.py ไม่ได้ — server.py import jobs อยู่แล้ว
จะกลายเป็นวงกลม และการตรวจรูปร่างข้อมูลก็ไม่ใช่งานของชั้น HTTP)

นโยบายของสองทางต่างกันโดยเจตนา:
  ทางผู้ใช้  = ปฏิเสธทั้งก้อน (ตอบ 400) เพราะ client แก้แล้วส่งใหม่ได้ทันที
  ทาง worker = เก็บของที่ใช้ได้ ทิ้งเฉพาะรายการที่เสีย แล้วแนบ warning ให้เจ้าของ
               เพราะถ้าปฏิเสธทั้งก้อน = เสียการถอดเสียงทั้งไฟล์ (แพงกว่ามาก) จาก segment เดียวที่เพี้ยน
"""

from __future__ import annotations

import math
import re

# เพดานเดียวกับที่ทางผู้ใช้ใช้ — ประชุมจริงที่ยาวที่สุดในระบบวันนี้อยู่หลักพัน segment
MAX_SEGMENTS = 50_000
MAX_SEGMENT_TEXT = 5_000      # ตัวอักษรต่อ segment (ประโยคพูดจริงยาวหลักร้อยตัว)
MAX_NAME = 60                 # ชื่อผู้พูด
MAX_SPEAKERS = 200
MAX_LANGUAGE = 32             # รหัสภาษาจาก whisper เช่น "th", "en"
MAX_MESSAGE = 500             # warning / summary_error ที่เอาไปโชว์ในการ์ดงาน

# ชื่อ/รหัสสั้น ๆ ห้ามมีตัวขึ้นบรรทัดใหม่ ไม่งั้นไปโผล่กลางตาราง export และหัวข้อสรุป
_CTRL_RE = re.compile(r"[\r\n\t]+")


def text(value, limit: int | None = None) -> str:
    """บังคับให้เป็นสตริง (None -> "") โดยไม่แตะขึ้นบรรทัดใหม่ — ใช้กับ summary/คำแปลที่เป็น Markdown."""
    if value is None:
        return ""
    out = value if isinstance(value, str) else str(value)
    return out[:limit] if limit else out


def message(value, limit: int = MAX_MESSAGE) -> str | None:
    """ข้อความสั้นที่เอาไปโชว์ (warning, summary_error) — คืน None ถ้าไม่มีอะไรจะโชว์."""
    out = text(value, limit).strip()
    return out or None


def name(value, limit: int = MAX_NAME) -> str:
    return _CTRL_RE.sub(" ", text(value)).strip()[:limit]


def language(value) -> str:
    return name(value, MAX_LANGUAGE)


def duration(value) -> float:
    """วินาที — ต้องเป็นตัวเลข finite ที่ไม่ติดลบ ไม่งั้นคืน 0.0 (แบบเดียวกับตอนหา duration ไม่เจอ)."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(seconds) or seconds < 0:
        return 0.0
    return seconds


def speakers(raw) -> list[str]:
    """รายชื่อผู้พูด — ตัดของที่ไม่ใช่สตริงทิ้ง (คอลัมน์ฝั่ง Postgres เป็น text[])."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:MAX_SPEAKERS]:
        if not isinstance(item, str):
            continue
        cleaned = name(item)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def segment(item) -> dict | None:
    """ทำความสะอาด segment เดียว — คืน None ถ้าใช้ไม่ได้."""
    if not isinstance(item, dict):
        return None
    try:
        start = float(item.get("start", 0))
        end = float(item.get("end", 0))
    except (TypeError, ValueError):
        return None
    # NaN/Infinity ผ่าน float() ได้ (json.loads รับ NaN, "1e400" = inf) แต่เขียนลงคลังแล้ว
    # อ่านกลับเป็น JSON ที่ถูกต้องไม่ได้ และ export พังถาวร
    if not (math.isfinite(start) and math.isfinite(end)):
        return None
    out = {
        "start": round(start, 2),
        "end": round(end, 2),
        "text": text(item.get("text", ""), MAX_SEGMENT_TEXT).strip(),
    }
    speaker = name(item.get("speaker") or "")
    if speaker:
        out["speaker"] = speaker
    return out


def segments(raw) -> tuple[list[dict], int]:
    """กรอง segments ที่มาจาก worker — คืน (ที่ใช้ได้, จำนวนที่ทิ้ง).

    ทิ้งทีละรายการ ไม่ล้มทั้งงาน (ดูเหตุผลในหัวไฟล์)
    """
    if not isinstance(raw, list):
        # worker ส่งอย่างอื่นมาแทนลิสต์ = ใช้ไม่ได้ทั้งก้อน แต่ summary/เสียงที่ได้มายังมีค่า
        return [], (1 if raw else 0)
    kept: list[dict] = []
    dropped = max(0, len(raw) - MAX_SEGMENTS)
    for item in raw[:MAX_SEGMENTS]:
        cleaned = segment(item)
        if cleaned is None:
            dropped += 1
        else:
            kept.append(cleaned)
    return kept, dropped
