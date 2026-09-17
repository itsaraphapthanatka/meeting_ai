"""คลังการประชุมสำหรับหน้าเว็บ — เก็บเป็นไฟล์ JSON ใต้ recordings/web (stdlib ล้วน).

โครงไฟล์:
    recordings/web/index.json     รายการ metadata ทุกการประชุม (เรียงใหม่สุดก่อน)
    recordings/web/<id>.json      รายละเอียด: segments + สรุป
    recordings/web/<id>.<ext>     ไฟล์เสียงต้นฉบับ
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import config

try:                      # Windows
    import msvcrt
except ImportError:       # pragma: no cover - ขึ้นกับระบบปฏิบัติการ
    msvcrt = None         # type: ignore[assignment]
try:                      # POSIX
    import fcntl
except ImportError:       # pragma: no cover - ขึ้นกับระบบปฏิบัติการ
    fcntl = None          # type: ignore[assignment]

# เลือกกลไกล็อกครั้งเดียวตอน import — ถ้าไม่มีเลย ห้ามไปวนรอล็อกที่ไม่มีวันได้ (ดู _file_lock)
if msvcrt is not None and hasattr(msvcrt, "LK_NBLCK"):
    _LOCK_KIND = "msvcrt"
elif fcntl is not None and hasattr(fcntl, "flock"):
    _LOCK_KIND = "fcntl"
else:                     # pragma: no cover - ไม่มีทั้งสองโมดูล
    _LOCK_KIND = ""

WEB_DIR = config.root / "recordings" / "web"
INDEX_PATH = WEB_DIR / "index.json"
SETTINGS_PATH = WEB_DIR / "settings.json"

_lock = threading.RLock()
# แคชรายละเอียดตาม mtime — ค้นหาต้องอ่านทุกไฟล์ ไม่อยากอ่านซ้ำทุกครั้ง
_detail_cache: dict[str, tuple[float, dict]] = {}
# BUG-055: mtime หยาบกว่าจังหวะเขียนของเรา (บน Windows นาฬิกาไฟล์ขยับทุก ~15 ms, FAT ทุก 2 s)
# สองการเขียนใน tick เดียวกันจึงได้ mtime เท่ากัน แล้วแคชค้างเป็นของเก่า = "แก้แล้วไม่เซฟ"
# กติกา: แคชได้เฉพาะไฟล์ที่นิ่งมานานกว่าความละเอียดของ timestamp แล้ว — การเขียนครั้งต่อไป
# (โพรเซสไหนก็ตาม เพราะ CLI กับ mai web ใช้โฟลเดอร์เดียวกันได้) จะได้ mtime ใหม่เสมอ
_CACHE_MIN_AGE = 2.0

_ID_RE = re.compile(r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}")
SNIPPET_PAD = 70


def _ensure_dir() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)


# ---------- ล็อกข้ามโพรเซส (BUG-056) ----------
# _lock กันได้แค่เธรดในโพรเซสเดียวกัน แต่ผู้ใช้เปิด mai web ค้างไว้แล้วรัน mai process/mai bot
# ในเทอร์มินัลอีกอันได้ตลอด สองโพรเซสอ่าน index.json ชุดเดียวกันแล้วเขียนทับกัน = งานที่บันทึก
# สำเร็จไปแล้วหายถาวร (ไฟล์ไม่เคยฉีก เพราะ _write_json เป็น atomic — ที่หายคือช่วง "อ่านแล้วยังไม่เขียน")
LOCK_NAME = ".store.lock"
LOCK_TIMEOUT = 10.0       # วินาที — เขตวิกฤตจริงกินเวลาระดับ ms ถ้าถึง 10 วิแปลว่ามีอะไรผิดปกติ
REPLACE_TIMEOUT = 2.0     # วินาที — เพดานการลองใหม่ของ os.replace (ดู _write_json)
_lock_depth = 0           # รองรับการเรียกซ้อน: จับล็อกไฟล์จริงเฉพาะชั้นนอกสุด


def _warn(msg: str) -> None:
    # ภาษาอังกฤษล้วน: คอนโซลของเจ้าของเครื่องเป็น cp874 ข้อความ debug ภาษาไทยทำให้ล่มซ้ำซ้อน
    print(f"meeting_ai.store: {msg}", file=sys.stderr)


def _store_lock_path() -> Path:
    """คำนวณตอนเรียก ไม่ใช่ตอน import — เทสแพตช์ WEB_DIR ทีหลัง

    ถ้าผูกค่าไว้ตอน import เหมือน INDEX_PATH ไฟล์ล็อกของเทสจะไปโผล่ใน recordings/web ตัวจริง
    """
    return WEB_DIR / LOCK_NAME


def _try_lock(fd: int) -> bool:
    """จองล็อกแบบไม่รอ — คืน False เมื่อโพรเซสอื่นถืออยู่ (OSError คือ 'ไม่ว่าง' ไม่ใช่ความผิดพลาด)."""
    try:
        if _LOCK_KIND == "msvcrt":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    try:
        if _LOCK_KIND == "msvcrt":
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass                                   # ปิด fd ต่อไป ระบบปฏิบัติการปล่อยล็อกให้เองอยู่แล้ว


@contextlib.contextmanager
def _file_lock():
    """ล็อกทั้ง store ข้ามโพรเซส ครอบทั้งช่วง read-modify-write.

    **ต้องเรียกใต้ `_lock` เสมอ — ใช้ผ่าน `_guard()` เท่านั้น** ตัวนับ `_lock_depth`
    ปลอดภัยได้เพราะมีเธรดเดียวเข้ามาถึงตรงนี้ได้ในแต่ละครั้ง
    ถ้าเจ้าของล็อกตาย ระบบปฏิบัติการปล่อยล็อกให้เองตอนปิด handle จึงไม่มี stale lock
    ให้ต้องเก็บกวาด (และเราไม่ลบไฟล์ล็อกทิ้ง เพราะลบไฟล์ที่โพรเซสอื่นถือ handle อยู่คือ race ใหม่)
    รอเกิน LOCK_TIMEOUT แล้วยังไม่ได้ = เขียนต่อโดยไม่มีล็อก (พฤติกรรมเดิมก่อนแก้บั๊กนี้)
    ดีกว่าโยน exception ทิ้งงานของผู้ใช้ทั้งก้อน เช่นบทถอดเสียงที่เพิ่งถอดมาสี่สิบนาที
    """
    global _lock_depth
    if _lock_depth:                            # ซ้อนอยู่แล้ว — ถือล็อกเดิมต่อ
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return

    fd = None
    if _LOCK_KIND:
        try:
            _ensure_dir()
            fd = os.open(_store_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as exc:
            _warn(f"cannot open store lock ({exc}); writing without it")
            fd = None
    if fd is not None:
        deadline = time.monotonic() + LOCK_TIMEOUT
        delay = 0.001
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                _warn(f"store lock busy for {LOCK_TIMEOUT}s; writing without it")
                os.close(fd)
                fd = None
                break
            time.sleep(delay)
            delay = min(delay * 2, 0.05)

    # ระหว่างที่รอ โพรเซสอื่นอาจเขียน detail ไปแล้ว — ของในแคชคือของก่อนเข้าเขตล็อก ทิ้งให้หมด
    _detail_cache.clear()
    _lock_depth += 1
    try:
        yield
    finally:
        _lock_depth -= 1
        # ออกจากเขตล็อกแล้วทิ้งอีกรอบ: ค่าที่เราเพิ่งเขียนอาจถูกแคชไว้ก่อนเขียน
        _detail_cache.clear()
        if fd is not None:
            _unlock(fd)
            os.close(fd)


@contextlib.contextmanager
def _guard():
    """ล็อกทั้งสองชั้นตามลำดับเดิมเสมอ (เธรดก่อน แล้วค่อยไฟล์) — call site จับสลับลำดับไม่ได้."""
    with _lock, _file_lock():
        yield


def new_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def valid_id(mid: str) -> bool:
    """กัน path traversal — id ต้องตรงรูปแบบที่เราสร้างเท่านั้น.

    fullmatch ไม่ใช่ match: `$` ของ re ยอมให้มีตัวขึ้นบรรทัดใหม่ปิดท้ายได้
    "<id>" กับ "<id>ขึ้นบรรทัดใหม่" จึงเคยผ่านทั้งคู่ ทั้งที่ชื่อไฟล์ไม่เหมือนกัน
    """
    return bool(_ID_RE.fullmatch(mid or ""))


def _detail_path(mid: str) -> Path:
    return WEB_DIR / f"{mid}.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: Any) -> None:
    """เขียนแบบ atomic — ไฟล์ index พังยากขึ้นเวลาโดนขัดจังหวะ.

    ชื่อ tmp ต้องมี pid (BUG-056): เดิมทุกโพรเซสใช้ index.json.tmp ชื่อเดียวกัน
    บน Windows โพรเซสหนึ่งเปิดเขียนขณะอีกโพรเซส os.replace ไฟล์เดียวกัน = PermissionError ทะลุถึงผู้ใช้
    """
    _ensure_dir()
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # บน Windows ถ้ามีโพรเซสอื่น "เปิดอ่าน" ไฟล์ปลายทางค้างอยู่ os.replace จะล้มเป็น PermissionError
    # (วัดจริง: ผู้อ่านสองโพรเซสยิงต่อเนื่องทำให้ replace ล้ม 83%) และนั่นคือหน้าเว็บที่เปิดค้างไว้
    # กับ mai process ที่เพิ่งถอดเสียงเสร็จพอดี ผู้อ่านถือไฟล์แค่ไม่กี่ไมโครวินาที จึงลองใหม่ถี่ ๆ
    # ในกรอบสั้น ๆ ดีกว่าโยน 500 แล้วทิ้งงานที่ถอดเสียงมาทั้งชั่วโมง
    deadline = time.monotonic() + REPLACE_TIMEOUT
    delay = 0.001
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError:      # เฉพาะ sharing violation — ENOSPC ฯลฯ ต้องเด้งทันที ไม่ต้องรอ
            if time.monotonic() >= deadline:
                raise      # ปล่อยไฟล์ .tmp ค้างไว้ตั้งใจ — ข้างในคือข้อมูลใหม่ที่ยังกู้ด้วยมือได้
            time.sleep(delay)
            delay = min(delay * 2, 0.02)


# ---------- ตั้งค่าระบบ (แอดมินปรับ) — มี API เดียวกับ pgstore ----------

def get_setting(key: str, default: Any = None) -> Any:
    with _lock:
        return _read_json(SETTINGS_PATH, {}).get(key, default)


def set_setting(key: str, value: Any) -> None:
    with _guard():
        data = _read_json(SETTINGS_PATH, {})
        data[key] = value
        _write_json(SETTINGS_PATH, data)


def _load_index() -> list[dict]:
    data = _read_json(INDEX_PATH, {})
    meetings = data.get("meetings") if isinstance(data, dict) else None
    return meetings if isinstance(meetings, list) else []


def _save_index(meetings: list[dict]) -> None:
    _write_json(INDEX_PATH, {"version": 1, "meetings": meetings})


def _write_detail(mid: str, detail: dict) -> None:
    """ทางเดียวที่เขียนไฟล์ detail — ล้างแคชในตัวเสมอ (BUG-055).

    เขียนแล้วทิ้งแคชแทนที่จะยัดค่าที่เพิ่งเขียนลงไป เพราะไฟล์ที่ mtime เป็น "เดี๋ยวนี้"
    คือไฟล์ที่โพรเซสอื่นอาจเขียนทับใน tick เดียวกันได้ — การอ่านครั้งถัดไปต้องไปดูดิสก์
    """
    _write_json(_detail_path(mid), detail)
    _detail_cache.pop(mid, None)


def load_detail(mid: str) -> dict:
    path = _detail_path(mid)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        _detail_cache.pop(mid, None)
        return {}
    cached = _detail_cache.get(mid)
    if cached and cached[0] == mtime:
        return cached[1]
    detail = _read_json(path, {})
    if time.time() - mtime >= _CACHE_MIN_AGE:
        _detail_cache[mid] = (mtime, detail)
    else:
        # เพิ่งถูกแก้ — mtime ยังชนกับการเขียนครั้งถัดไปได้ ห้ามแคช
        _detail_cache.pop(mid, None)
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
    # _guard = ล็อกข้ามโพรเซส (BUG-056) · _write_detail = เขียนแล้วล้างแคช (BUG-055)
    # ต้องใช้ทั้งคู่ ไม่ใช่เลือกอย่างใดอย่างหนึ่ง
    with _guard():
        _write_detail(mid, detail)
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
    with _guard():
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        detail = dict(load_detail(mid))
        translations = dict(detail.get("translations") or {})
        translations[lang] = text
        detail["translations"] = translations
        _write_detail(mid, detail)
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def set_segments(mid: str, segments: list[dict]) -> dict | None:
    """เขียนบทถอดเสียงที่ผู้ใช้แก้เอง (แก้คำผิดของ whisper / เปลี่ยนชื่อผู้พูด)."""
    with _guard():
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        detail = dict(load_detail(mid))
        detail["segments"] = segments
        _write_detail(mid, detail)
        meta["segments"] = len(segments)
        meta["speakers"] = sorted({s["speaker"] for s in segments if s.get("speaker")})
        meta["transcript_edited"] = True
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def set_summary(mid: str, summary: str, error: str | None = None) -> dict | None:
    """เขียนสรุปที่ได้จาก AI (ใช้ตอนสรุปใหม่) — ไม่ตั้ง flag edited เพราะไม่ใช่คนแก้."""
    with _guard():
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        detail = dict(load_detail(mid))
        detail["summary"] = summary
        _write_detail(mid, detail)
        meta["summary_error"] = error
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def update(mid: str, title: str | None = None, summary: str | None = None) -> dict | None:
    """แก้ชื่อเรื่อง/สรุป (ผู้ใช้เกลาสรุปกับ action items เองได้)."""
    with _guard():
        meetings = _load_index()
        meta = next((m for m in meetings if m.get("id") == mid), None)
        if meta is None:
            return None
        if summary is not None:
            detail = dict(load_detail(mid))
            detail["summary"] = summary
            _write_detail(mid, detail)
            meta["edited"] = True
            meta["summary_error"] = None  # คนเขียนสรุปเองแล้ว ไม่ต้องเตือนค้างไว้
        if title is not None:
            meta["title"] = title.strip() or meta["title"]
        meta["updated"] = datetime.now().isoformat(timespec="seconds")
        _save_index(meetings)
    return get(mid)


def delete(mid: str) -> bool:
    with _guard():
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
