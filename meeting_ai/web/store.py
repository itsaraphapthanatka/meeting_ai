"""คลังการประชุมสำหรับหน้าเว็บ — เก็บเป็นไฟล์ JSON ใต้ recordings/web (stdlib ล้วน).

โครงไฟล์:
    recordings/web/index.json     รายการ metadata ทุกการประชุม (เรียงใหม่สุดก่อน)
    recordings/web/<id>.json      รายละเอียด: segments + สรุป
    recordings/web/<id>.<ext>     ไฟล์เสียงต้นฉบับ
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import config

WEB_DIR = config.root / "recordings" / "web"
INDEX_PATH = WEB_DIR / "index.json"
SETTINGS_PATH = WEB_DIR / "settings.json"

_lock = threading.RLock()
# แคชรายละเอียดตาม mtime — ค้นหาต้องอ่านทุกไฟล์ ไม่อยากอ่านซ้ำทุกครั้ง
_detail_cache: dict[str, tuple[float, dict]] = {}

_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
SNIPPET_PAD = 70


def _ensure_dir() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)


def new_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def valid_id(mid: str) -> bool:
    """กัน path traversal — id ต้องตรงรูปแบบที่เราสร้างเท่านั้น."""
    return bool(_ID_RE.match(mid or ""))


def _detail_path(mid: str) -> Path:
    return WEB_DIR / f"{mid}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    """เขียนแบบ atomic — ไฟล์ index พังยากขึ้นเวลาโดนขัดจังหวะ."""
    _ensure_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ---------- ตั้งค่าระบบ (แอดมินปรับ) — มี API เดียวกับ pgstore ----------

def get_setting(key: str, default: Any = None) -> Any:
    with _lock:
        return _read_json(SETTINGS_PATH, {}).get(key, default)


def set_setting(key: str, value: Any) -> None:
    with _lock:
        data = _read_json(SETTINGS_PATH, {})
        data[key] = value
        _write_json(SETTINGS_PATH, data)


def _load_index() -> list[dict]:
    data = _read_json(INDEX_PATH, {})
    meetings = data.get("meetings") if isinstance(data, dict) else None
    return meetings if isinstance(meetings, list) else []


def _save_index(meetings: list[dict]) -> None:
    _write_json(INDEX_PATH, {"version": 1, "meetings": meetings})


def load_detail(mid: str) -> dict:
    path = _detail_path(mid)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _detail_cache.get(mid)
    if cached and cached[0] == mtime:
        return cached[1]
    detail = _read_json(path, {})
    _detail_cache[mid] = (mtime, detail)
    return detail


def fmt_time(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcript_text(detail: dict) -> str:
    """ข้อความล้วนสำหรับค้นหา."""
    return " ".join(s.get("text", "").strip() for s in detail.get("segments", [])).strip()


def timestamped(detail: dict) -> str:
    parts = []
    for s in detail.get("segments", []):
        head = f"[{fmt_time(s.get('start', 0))} - {fmt_time(s.get('end', 0))}]"
        speaker = s.get("speaker")
        if speaker:
            head += f" {speaker}:"
        parts.append(f"{head} {s.get('text', '').strip()}")
    return "\n".join(parts)


def create(
    mid: str,
    title: str,
    audio_name: str,
    source: str,
    language: str,
    duration: float,
    segments: list[dict],
    summary: str,
    summary_error: str | None = None,
    template: str = "general",
    speakers: list[str] | None = None,
    owner_id: str | None = None,      # ไม่ใช้ในโหมดไฟล์ — มีไว้ให้ signature ตรงกับ pgstore
    visibility: str = "private",      # เช่นกัน
) -> dict:
    """บันทึกการประชุมใหม่ คืน metadata ที่เก็บลง index.

    summary_error: ถ้าสรุปไม่สำเร็จ ยังบันทึกบทถอดเสียงไว้ — ถอดเสียงใหม่แพงกว่าสรุปใหม่มาก
    speakers: รายชื่อผู้พูดที่พบ (ว่าง = ไม่ได้แยกผู้พูด)
    """
    now = datetime.now().isoformat(timespec="seconds")
    meta = {
        "id": mid,
        "title": title,
        "created": now,
        "updated": now,
        "language": language,
        "duration": round(duration, 1),
        "segments": len(segments),
        "audio": audio_name,
        "source": source,
        "edited": False,
        "summary_error": summary_error,
        "template": template,
        "speakers": speakers or [],
    }
    detail = {"id": mid, "segments": segments, "summary": summary, "translations": {}}
    with _lock:
        _write_json(_detail_path(mid), detail)
        meetings = _load_index()
        meetings = [m for m in meetings if m.get("id") != mid]
        meetings.insert(0, meta)
        _save_index(meetings)
    return meta


def get(mid: str) -> dict | None:
    """คืน metadata + สรุป + segments ของการประชุมเดียว."""
    with _lock:
        meta = next((m for m in _load_index() if m.get("id") == mid), None)
        if meta is None:
            return None
        detail = load_detail(mid)
    out = dict(meta)
    out["summary"] = detail.get("summary", "")
    out["segments_list"] = detail.get("segments", [])
    out["transcript"] = timestamped(detail)
    out["translations"] = detail.get("translations", {})
    return out


def set_translation(mid: str, lang: str, text: str) -> dict | None:
    with _lock:
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        detail = dict(load_detail(mid))
        translations = dict(detail.get("translations") or {})
        translations[lang] = text
        detail["translations"] = translations
        _write_json(_detail_path(mid), detail)
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def set_segments(mid: str, segments: list[dict]) -> dict | None:
    """เขียนบทถอดเสียงที่ผู้ใช้แก้เอง (แก้คำผิดของ whisper / เปลี่ยนชื่อผู้พูด)."""
    with _lock:
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        detail = dict(load_detail(mid))
        detail["segments"] = segments
        _write_json(_detail_path(mid), detail)
        meta["segments"] = len(segments)
        meta["speakers"] = sorted({s["speaker"] for s in segments if s.get("speaker")})
        meta["transcript_edited"] = True
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def set_summary(mid: str, summary: str, error: str | None = None) -> dict | None:
    """เขียนสรุปที่ได้จาก AI (ใช้ตอนสรุปใหม่) — ไม่ตั้ง flag edited เพราะไม่ใช่คนแก้."""
    with _lock:
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        detail = dict(load_detail(mid))
        detail["summary"] = summary
        _write_json(_detail_path(mid), detail)
        meta["summary_error"] = error
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def update(mid: str, title: str | None = None, summary: str | None = None) -> dict | None:
    """แก้ชื่อเรื่อง/สรุป (ผู้ใช้เกลาสรุปกับ action items เองได้)."""
    with _lock:
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        if summary is not None:
            detail = dict(load_detail(mid))
            detail["summary"] = summary
            _write_json(_detail_path(mid), detail)
            meta["edited"] = True
            meta["summary_error"] = None  # คนเขียนสรุปเองแล้ว ไม่ต้องเตือนค้างไว้
        if title is not None:
            meta["title"] = title.strip() or meta["title"]
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def delete(mid: str) -> bool:
    with _lock:
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return False
        _save_index([m for m in meetings if m.get("id") != mid])
        _detail_cache.pop(mid, None)
        # ลบทุกไฟล์ของการประชุมนี้ — มีทั้ง detail, ไฟล์ผสม และแทร็กแยก (<id>_mic.webm ฯลฯ)
        for path in WEB_DIR.glob(f"{mid}*"):
            if path.is_file():
                path.unlink(missing_ok=True)
    return True


def audio_path(meta: dict) -> Path | None:
    name = meta.get("audio")
    if not name:
        return None
    # ใช้แค่ basename กัน path ที่หลุดออกนอกโฟลเดอร์
    return WEB_DIR / Path(name).name


def _snippet(text: str, query: str) -> str:
    pos = text.lower().find(query.lower())
    if pos < 0:
        return ""
    start = max(0, pos - SNIPPET_PAD)
    end = min(len(text), pos + len(query) + SNIPPET_PAD)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")


def search(query: str = "", user_id: str | None = None) -> list[dict]:
    """คืนรายการการประชุม ถ้ามี query จะกรองด้วยชื่อเรื่อง/สรุป/บทถอดเสียง.

    ใช้การค้นแบบ substring เพราะภาษาไทยไม่มีช่องว่างระหว่างคำ การตัดคำจะพลาดมากกว่า
    """
    query = (query or "").strip()
    with _lock:
        meetings = _load_index()
        if not query:
            return [dict(m) for m in meetings]

        results = []
        for meta in meetings:
            detail = load_detail(meta.get("id", ""))
            summary = detail.get("summary", "")
            body = transcript_text(detail)
            haystacks = (meta.get("title", ""), summary, body)
            if not any(query.lower() in h.lower() for h in haystacks):
                continue
            hit = dict(meta)
            hit["snippet"] = (
                _snippet(summary, query) or _snippet(body, query) or meta.get("title", "")
            )
            results.append(hit)
    return results


def stats(user_id: str | None = None) -> dict:
    with _lock:
        meetings = _load_index()
    return {
        "count": len(meetings),
        "total_duration": round(sum(m.get("duration", 0) or 0 for m in meetings), 1),
    }
