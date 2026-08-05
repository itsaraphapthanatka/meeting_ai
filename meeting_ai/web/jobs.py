"""คิวงานของหน้าเว็บ + สะพานไปหา worker.

รันได้สองโหมด:
  โหมดในเครื่อง (ค่าเริ่มต้น) — เธรดเดียวในโพรเซสเดียวกับเว็บหยิบงานไปทำเลย
  โหมด worker แยกเครื่อง (REMOTE_WORKER=1) — เว็บแค่ถือคิว รอ `mai worker` มา claim
      ใช้ตอน deploy เว็บขึ้น cloud ที่ไม่มี GPU แล้วให้เครื่องที่มี GPU มารับงาน

ทั้งสองโหมดใช้ตัวประมวลผลตัวเดียวกัน (meeting_ai.runner) — logic ไม่แตกสองทาง

ใช้ worker เดียวโดยเจตนา: whisper กิน VRAM/CPU เต็มที่อยู่แล้ว
รันพร้อมกันหลายงานมีแต่จะแย่งกันแล้วช้าลงทั้งคู่ (หรือ VRAM ไม่พอ)
"""

from __future__ import annotations

import threading
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

from .. import runner, transcriber
from ..config import config
from . import store

_jobs: dict[str, dict] = {}
_drafts: dict[str, dict] = {}
_pending: deque[str] = deque()

# ใช้ Condition เดียวคุมทั้ง _jobs/_drafts/_pending — เธรดในเครื่องรอที่นี่, remote claim ก็หยิบจากที่นี่
_cv = threading.Condition(threading.RLock())
_live_lock = threading.Lock()
_worker: threading.Thread | None = None
_worker_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _set(job_id: str, **fields) -> None:
    with _cv:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields)


def get(job_id: str) -> dict | None:
    with _cv:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def active() -> list[dict]:
    with _cv:
        return [public(j) for j in _jobs.values() if j["status"] in ("queued", "running")]


def public(job: dict) -> dict:
    """ตัดฟิลด์ภายใน (ขึ้นต้นด้วย _) ออกก่อนส่งให้เบราว์เซอร์."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def draft(mid: str) -> dict | None:
    with _cv:
        d = _drafts.get(mid)
        return dict(d) if d else None


# ---------- draft: สร้างการประชุมก่อนอัปโหลดไฟล์ ----------

def create_draft(
    title: str,
    language: str | None,
    template: str,
    want_diarize: bool,
    num_speakers: int,
    source: str,
) -> str:
    mid = store.new_id()
    with _cv:
        _drafts[mid] = {
            "id": mid,
            "title": title,
            "language": language,
            "template": template,
            "diarize": want_diarize,
            "num_speakers": num_speakers,
            "source": source,
            "tracks": {},
        }
    return mid


def register_track(mid: str, name: str, path: Path) -> bool:
    with _cv:
        d = _drafts.get(mid)
        if d is None:
            return False
        d["tracks"][name] = str(path)
    return True


# ---------- คิว ----------

def _enqueue(job_id: str, title: str, kind: str, **extra) -> dict:
    job = {
        "id": job_id,
        "status": "queued",
        "step": "รอคิว",
        "progress": 0.0,
        "title": title,
        "kind": kind,
        "meeting_id": None,
        "error": None,
        "warning": None,
        "created": _now(),
        **extra,
    }
    with _cv:
        _jobs[job_id] = job
        _pending.append(job_id)
        _cv.notify_all()
    if not config.remote_worker:
        _ensure_worker()
    return public(job)


def start(mid: str) -> dict | None:
    """สั่งประมวลผล draft ที่อัปโหลดแทร็กครบแล้ว."""
    d = draft(mid)
    if d is None or not d["tracks"]:
        return None
    return _enqueue(mid, d["title"], "process")


def submit_summarize(meeting_id: str, title: str) -> dict:
    """สรุปใหม่จากบทถอดเสียงที่เก็บไว้แล้ว (ไม่ต้องถอดเสียงซ้ำ)."""
    return _enqueue(meeting_id, title, "summarize", _meeting=meeting_id)


def submit_translate(meeting_id: str, title: str, lang: str) -> dict:
    # คั่นด้วยจุดเพราะปลอดภัยใน URL (`#` จะกลายเป็น fragment)
    return _enqueue(f"{meeting_id}.tr.{lang}", title, "translate",
                    _meeting=meeting_id, _lang=lang)


# ---------- spec: สิ่งที่ฝั่งประมวลผลต้องรู้ ----------

def build_spec(job_id: str) -> dict | None:
    """แปลงงานในคิวเป็น spec ที่ runner เอาไปทำได้ (ไม่มี path เครื่องอยู่ในนี้)."""
    job = get(job_id)
    if job is None:
        return None
    kind = job["kind"]

    if kind == "process":
        d = draft(job_id)
        if d is None:
            return None
        return {
            "id": job_id,
            "kind": kind,
            "title": d["title"],
            "language": d["language"],
            "template": d["template"],
            "diarize": d["diarize"],
            "num_speakers": d["num_speakers"],
            "tracks": sorted(d["tracks"]),
        }

    mid = job.get("_meeting")
    meeting = store.get(mid) if mid else None
    if meeting is None:
        return None
    spec = {
        "id": job_id,
        "kind": kind,
        "title": meeting["title"],
        "template": meeting.get("template"),
    }
    if kind == "summarize":
        spec["segments"] = meeting.get("segments_list") or []
    else:
        spec["lang"] = job.get("_lang")
        spec["summary"] = meeting.get("summary") or ""
    return spec


def track_path(job_id: str, name: str) -> Path | None:
    """path ของแทร็กบนดิสก์ของฝั่งที่ถือไฟล์ (ใช้ทั้งโหมดในเครื่องและตอนให้ worker ดาวน์โหลด)."""
    d = draft(job_id)
    if d is None:
        return None
    raw = d["tracks"].get(name)
    return Path(raw) if raw else None


# ---------- นำผลลัพธ์เข้าคลัง ----------

def apply_result(job_id: str, result: dict) -> None:
    job = get(job_id)
    if job is None:
        raise RuntimeError("ไม่พบงานนี้")
    kind = job["kind"]

    if kind == "process":
        d = draft(job_id)
        if d is None:
            raise RuntimeError("ไม่พบข้อมูลการประชุมที่รออัปโหลด")
        playback = result.get("playback")
        audio_name = Path(playback).name if playback else Path(
            sorted(d["tracks"].values())[0]
        ).name
        store.create(
            mid=job_id,
            title=d["title"],
            audio_name=audio_name,
            source=d["source"],
            language=result.get("language") or config.whisper_lang,
            duration=result.get("duration") or 0.0,
            segments=result.get("segments") or [],
            summary=result.get("summary") or "",
            summary_error=result.get("summary_error"),
            template=d["template"],
            speakers=result.get("speakers") or [],
        )
        with _cv:
            _drafts.pop(job_id, None)
        meeting_id = job_id

    elif kind == "summarize":
        meeting_id = job["_meeting"]
        store.set_summary(meeting_id, result.get("summary") or "",
                          error=result.get("summary_error"))
    else:
        meeting_id = job["_meeting"]
        store.set_translation(meeting_id, result["lang"], result["text"])

    warning = result.get("warning")
    if result.get("summary_error"):
        warning = (f"สรุปไม่สำเร็จ: {result['summary_error']} — บทถอดเสียงเก็บไว้แล้ว "
                   "กด “สรุปใหม่ด้วย AI” เพื่อลองอีกครั้ง")
    _set(job_id, status="done",
         step="ถอดเสียงเสร็จ แต่สรุปไม่ได้" if result.get("summary_error") else "เสร็จ",
         progress=1.0, meeting_id=meeting_id, warning=warning)


def fail(job_id: str, error: str) -> None:
    _set(job_id, status="error", step="ผิดพลาด", error=error)


def report_progress(job_id: str, step: str, progress: float) -> None:
    _set(job_id, status="running", step=step, progress=max(0.0, min(1.0, progress)))


# ---------- ฝั่ง worker แยกเครื่อง ----------

def claim() -> dict | None:
    """หยิบงานถัดไปให้ worker ที่อยู่ไกล — คืน spec หรือ None ถ้าคิวว่าง."""
    with _cv:
        while _pending:
            job_id = _pending.popleft()
            spec = build_spec(job_id)
            if spec is None:
                # draft/การประชุมหายไปแล้ว (ถูกลบ?) ข้ามไป
                fail(job_id, "ข้อมูลของงานนี้หายไปก่อนจะได้ประมวลผล")
                continue
            report_progress(job_id, "worker รับงานแล้ว", 0.01)
            return spec
    return None


def requeue(job_id: str) -> bool:
    """คืนงานกลับคิว (worker หลุดกลางทาง)."""
    with _cv:
        if job_id not in _jobs:
            return False
        _pending.append(job_id)
        _cv.notify_all()
    _set(job_id, status="queued", step="รอคิว", progress=0.0)
    return True


# ---------- ถอดเสียงสด ----------

def transcribe_clip(path: Path, language: str | None) -> str:
    """ถอดเสียงคลิปสั้นสำหรับแสดงสดระหว่างประชุม.

    ใช้ lock แยกจากคิวหลัก คลิปสดจะได้ไม่ต้องรอหลังงานถอดเสียงไฟล์ยาว
    """
    with _live_lock:
        return transcriber.transcribe(path, language=language).text


# ---------- เธรดประมวลผลในเครื่อง ----------

def _run_local(job_id: str) -> None:
    spec = build_spec(job_id)
    if spec is None:
        raise RuntimeError("ไม่พบข้อมูลของงานนี้ (อาจถูกลบไปแล้ว)")

    def progress(step: str, value: float) -> None:
        report_progress(job_id, step, value)

    if spec["kind"] == "process":
        def fetch(name: str) -> Path:
            path = track_path(job_id, name)
            if path is None or not path.exists():
                raise RuntimeError(f"ไม่พบไฟล์แทร็ก {name}")
            return path

        store.WEB_DIR.mkdir(parents=True, exist_ok=True)
        result = runner.transcribe_job(spec, fetch, progress, store.WEB_DIR)
    else:
        result = runner.HANDLERS[spec["kind"]](spec, progress)

    apply_result(job_id, result)


def _loop() -> None:
    while True:
        with _cv:
            while not _pending:
                _cv.wait()
            job_id = _pending.popleft()
        try:
            _run_local(job_id)
        except Exception as e:
            traceback.print_exc()
            fail(job_id, str(e))


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="meeting-ai-worker", daemon=True)
            _worker.start()
