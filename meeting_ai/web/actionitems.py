"""แยก Action Items ออกจากสรุป แล้วกระทบยอดกับระเบียนเดิม (BACKLOG #53 · ทาง B ของ PRD).

หัวใจของไฟล์นี้ไม่ใช่ตัวแยกตาราง แต่คือ `reconcile()` — สรุปถูกเขียนทับได้สี่ทาง
(`create`, `set_summary`, `update`, และงานสรุปใหม่) ทุกครั้งที่เขียน รายการจะถูกแยกใหม่
ถ้าจับคู่ผิด **เครื่องหมายถูกที่ผู้ใช้ติ๊กไว้จะหาย** ซึ่ง PRD ระบุว่าเป็นความล้มเหลว
ที่แย่ที่สุดของฟีเจอร์นี้ แย่กว่าไม่มีฟีเจอร์เลย เพราะคนเชื่อมันไปแล้ว

กติกาที่ตามมาจากข้อนั้น: **ไม่ลบรายการที่ติ๊กว่าทำแล้ว ด้วยการเดาของเครื่อง**
จับคู่ไม่ได้ก็ติดป้าย `orphan` ไว้ให้คนตัดสินใจเอง

แยกจาก `summary` (ภาษาต้นฉบับ) เท่านั้น ไม่แยกจากคำแปล — คำแปลมีหัวตารางที่ถูกแปลไปด้วย
การแยกจากคำแปลต้องรู้หัวตารางทุกภาษา และจะได้รายการซ้ำข้ามภาษา (PRD 6.1)
"""

from __future__ import annotations

import re
import secrets
import unicodedata

# หัวข้อที่เทมเพลตสรุปทั้งห้าใช้ตรงกัน (summarizer.py) — จับที่คำว่า Action Items
# ไม่ใช่ทั้งบรรทัด เพราะผู้ใช้แก้สรุปเองได้ อีโมจิหรือคำไทยอาจถูกเกลาไป
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+.*action\s*items.*$", re.I)
_ANY_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
# ค่าที่พรอมต์สั่งให้เติมตอนไม่รู้ ถือว่า "ไม่ได้ระบุ" ไม่ใช่ชื่อคน
_BLANKS = {"", "-", "—", "–", "ไม่ได้ระบุ", "ไม่ระบุ", "n/a", "na", "tbd", "...", "…"}
MAX_ITEMS = 200          # กันสรุปที่โดนยัดตารางยาวผิดปกติมาทำให้แถวการประชุมบวม
MAX_TEXT = 500


def _cells(line: str) -> list[str]:
    s = line.strip()
    if not s.startswith("|"):
        return []
    return [c.strip() for c in s.strip("|").split("|")]


def _clean(value: str) -> str:
    v = re.sub(r"\s+", " ", (value or "").strip())
    # เอา markdown ตัวหนา/เอียงออก ผู้ใช้เกลาสรุปเองแล้วใส่ **ชื่อ** ได้
    v = re.sub(r"[*_`]+", "", v).strip()
    return "" if v.lower() in _BLANKS else v[:MAX_TEXT]


def new_id() -> str:
    """id ของรายการ — อยู่ในระเบียน ไม่ฝังลง Markdown.

    PRD ปฏิเสธทาง D (ฝังรหัสซ่อนใน Markdown) เพราะผู้ใช้แก้สรุปเองได้แล้วรหัสจะพัง
    และไฟล์ที่ดาวน์โหลดไปจะมีขยะ — id นี้จึงใช้อ้างถึงรายการจาก API เท่านั้น
    ไม่ใช่ตัวจับคู่ตอนกระทบยอด (ตัวจับคู่คือข้อความที่ normalize แล้ว)
    """
    return secrets.token_hex(6)


def normalize(text: str) -> str:
    """คีย์จับคู่ — ต่างกันแค่ช่องว่าง วรรคตอนท้าย หรือตัวพิมพ์ ถือว่าเป็นงานเดียวกัน.

    NFKC ก่อน: LLM สลับไปมาระหว่างวงเล็บครึ่งความกว้างกับเต็มความกว้างได้เอง
    ซึ่งไม่ควรทำให้ของที่ติ๊กไว้หลุด
    """
    s = unicodedata.normalize("NFKC", text or "")
    s = re.sub(r"[*_`]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(" .,;:!?、。ฯ")
    return s.casefold()


def parse(summary: str) -> list[dict]:
    """ดึงตารางใต้หัวข้อ Action Items ออกมาเป็นรายการ.

    ต้องยึดหัวข้อเป็นหลัก ไม่ใช่ "หาตารางแรกที่เจอ" — เทมเพลต standup มีตารางของตัวเอง
    (`| คน | ทำอะไรไปแล้ว | ...`) อยู่ก่อนหน้า ถ้าจับผิดจะได้ชื่อคนมาเป็นรายการงาน
    """
    lines = (summary or "").splitlines()
    out: list[dict] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        if not _HEADING.match(lines[i]):
            i += 1
            continue
        i += 1
        header_done = False
        while i < len(lines):
            line = lines[i]
            if _ANY_HEADING.match(line):
                break                       # จบส่วนนี้ ไปหาหัวข้อ Action Items อันถัดไป
            cells = _cells(line)
            if not cells:
                if line.strip() and header_done:
                    break                   # ตารางจบแล้ว มีย่อหน้าอื่นต่อ
                i += 1
                continue
            if _SEPARATOR.match(line):
                header_done = True
                i += 1
                continue
            if not header_done:
                i += 1                      # แถวหัวตาราง
                continue
            text = _clean(cells[0])
            key = normalize(text)
            if text and key not in seen and len(out) < MAX_ITEMS:
                seen.add(key)
                out.append({
                    "text": text,
                    "assignee": _clean(cells[1]) if len(cells) > 1 else "",
                    "due": _clean(cells[2]) if len(cells) > 2 else "",
                })
            i += 1
    return out


def _record(parsed: dict) -> dict:
    return {"id": new_id(), "text": parsed["text"], "assignee": parsed.get("assignee", ""),
            "due": parsed.get("due", ""), "done": False, "orphan": False,
            "assignee_edited": False}


def reconcile(existing: list[dict] | None, summary: str) -> list[dict]:
    """รวมรายการที่แยกจากสรุปล่าสุด เข้ากับสถานะที่ผู้ใช้ตั้งไว้.

    - งานใหม่ -> เพิ่มเป็น pending
    - งานเดิมที่ยังจับคู่ได้ -> **คงสถานะ done และผู้รับผิดชอบที่ผู้ใช้แก้ไว้**
      (ถ้าผู้ใช้ไม่เคยแก้ ให้ใช้ค่าล่าสุดจากสรุป — สรุปยังเป็นแหล่งความจริงของเนื้อหา)
    - งานเดิมที่จับคู่ไม่ได้และยัง pending -> ทิ้งได้ ไม่มีอะไรของผู้ใช้อยู่ในนั้น
    - งานเดิมที่จับคู่ไม่ได้แต่ **done แล้ว -> เก็บไว้ ติดป้าย orphan** ต่อท้าย
    """
    old = [dict(x) for x in (existing or []) if isinstance(x, dict)]
    by_key: dict[str, dict] = {}
    for item in old:
        by_key.setdefault(normalize(item.get("text", "")), item)

    out: list[dict] = []
    used: set[str] = set()
    for parsed in parse(summary):
        key = normalize(parsed["text"])
        prev = by_key.get(key)
        if prev is None:
            out.append(_record(parsed))
            continue
        used.add(key)
        keep_assignee = bool(prev.get("assignee_edited"))
        out.append({
            "id": prev.get("id") or new_id(),
            "text": parsed["text"],          # ข้อความล่าสุดจากสรุป (ต่างกันได้แค่รูปแบบ)
            "assignee": prev.get("assignee", "") if keep_assignee else parsed.get("assignee", ""),
            "due": parsed.get("due", ""),
            "done": bool(prev.get("done")),
            "orphan": False,                 # กลับมาอยู่ในสรุปแล้ว เลิกเป็นเด็กกำพร้า
            "assignee_edited": keep_assignee,
        })

    for item in old:
        key = normalize(item.get("text", ""))
        if key in used or not item.get("done"):
            continue                          # ยังไม่ติ๊ก = ไม่มีอะไรของผู้ใช้ให้เสีย
        orphan = dict(item)
        orphan["orphan"] = True
        orphan.setdefault("id", new_id())
        out.append(orphan)
    return out[:MAX_ITEMS]
