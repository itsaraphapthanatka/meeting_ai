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

import socket
import tempfile
import threading
import time
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

from .. import runner, transcriber
from ..config import config
from . import backend
from .backend import cloud, store

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
    if cloud:
        return store.job_get(job_id)
    with _cv:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def active() -> list[dict]:
    if cloud:
        return store.job_active()
    with _cv:
        return [public(j) for j in _jobs.values() if j["status"] in ("queued", "running")]


def public(job: dict) -> dict:
    """ตัดฟิลด์ภายใน (ขึ้นต้นด้วย _) ออกก่อนส่งให้เบราว์เซอร์."""
    return {k: v for k, v in job.items() if not k.startswith("_")}


def draft(mid: str) -> dict | None:
    """ข้อมูลการประชุมที่ยังรออัปโหลดไฟล์ครบ."""
    if cloud:
        job = store.job_get(mid)
        return (job or {}).get("_spec") or None
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
    owner_id: str | None = None,
    stt_provider: str | None = None,
) -> str:
    mid = store.new_id()
    spec = {
        "id": mid,
        "title": title,
        "language": language,
        "template": template,
        "stt": stt_provider,
        "diarize": want_diarize,
        "num_speakers": num_speakers,
        "source": source,
        "owner_id": owner_id,
        "tracks": {},
    }
    if cloud:
        store.job_upsert(mid, "process", title, spec, status="draft")
    else:
        with _cv:
            _drafts[mid] = spec
    return mid


def create_bot(
    url: str,
    title: str,
    language: str | None,
    template: str,
    want_diarize: bool,
    num_speakers: int,
    bot_name: str,
    max_minutes: int,
    owner_id: str | None = None,
    stt_provider: str | None = None,
) -> dict:
    """สร้างงาน "ส่งบอทเข้าห้อง" — เข้าคิวได้เลยเพราะไฟล์เสียงเกิดที่ฝั่งประมวลผล.

    ต่างจาก upload/อัดสด ที่ต้องอัปโหลดแทร็กให้ครบก่อนแล้วค่อยกด process
    """
    mid = store.new_id()
    spec = {
        "id": mid,
        "title": title,
        "language": language,
        "template": template,
        "stt": stt_provider,
        "diarize": want_diarize,
        "num_speakers": num_speakers,
        "source": "bot",
        "owner_id": owner_id,
        "tracks": {},
        "url": url,
        "bot_name": bot_name,
        "max_minutes": max_minutes,
    }
    if cloud:
        store.job_upsert(mid, "bot", title, spec, status="queued")
        if not config.remote_worker:
            _ensure_worker()
        job = store.job_get(mid)
        return public(job)
    with _cv:
        _drafts[mid] = spec
    return _enqueue(mid, title, "bot")


def register_track(mid: str, name: str, path: Path) -> bool:
    if cloud:
        job = store.job_get(mid)
        if job is None:
            return False
        spec = dict(job.get("_spec") or {})
        tracks = dict(spec.get("tracks") or {})
        tracks[name] = str(path)
        spec["tracks"] = tracks
        return store.job_set_spec(mid, spec)
    with _cv:
        d = _drafts.get(mid)
        if d is None:
            return False
        d["tracks"][name] = str(path)
    return True


# ---------- คิว ----------

def _enqueue(job_id: str, title: str, kind: str, spec: dict | None = None, **extra) -> dict:
    if cloud:
        job = store.job_upsert(job_id, kind, title, spec or {}, status="queued",
                               meeting_id=extra.get("_meeting"))
        if not config.remote_worker:
            _ensure_worker()
        return public(job)

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
    if d is None or not d.get("tracks"):
        return None
    if cloud:
        job = store.job_start(mid)
        if job is None:
            return None
        if not config.remote_worker:
            _ensure_worker()
        return public(job)
    return _enqueue(mid, d["title"], "process")


def submit_summarize(meeting_id: str, title: str) -> dict:
    """สรุปใหม่จากบทถอดเสียงที่เก็บไว้แล้ว (ไม่ต้องถอดเสียงซ้ำ)."""
    return _enqueue(meeting_id, title, "summarize",
                    spec={"kind": "summarize", "meeting": meeting_id},
                    _meeting=meeting_id)


def submit_translate(meeting_id: str, title: str, lang: str) -> dict:
    # คั่นด้วยจุดเพราะปลอดภัยใน URL (`#` จะกลายเป็น fragment)
    return _enqueue(f"{meeting_id}.tr.{lang}", title, "translate",
                    spec={"kind": "translate", "meeting": meeting_id, "lang": lang},
                    _meeting=meeting_id, _lang=lang)


# ---------- spec: สิ่งที่ฝั่งประมวลผลต้องรู้ ----------

def _meeting_of(job: dict) -> str | None:
    return job.get("_meeting") or (job.get("_spec") or {}).get("meeting") or job.get("meeting_id")


def build_spec(job_id: str) -> dict | None:
    """แปลงงานในคิวเป็น spec ที่ runner เอาไปทำได้ (ไม่มี path เครื่องอยู่ในนี้)."""
    job = get(job_id)
    if job is None:
        return None
    kind = job["kind"]

    if kind in ("process", "bot"):
        d = draft(job_id)
        if d is None:
            return None
        # งาน process ต้องมีไฟล์ครบก่อน ส่วนงาน bot ยังไม่มีไฟล์ — บอทเป็นคนอัดเอง
        if kind == "process" and not d.get("tracks"):
            return None
        return {
            "id": job_id,
            "kind": kind,
            "title": d["title"],
            "language": d.get("language"),
            "template": d.get("template"),
            "diarize": d.get("diarize"),
            "num_speakers": d.get("num_speakers"),
            "stt": d.get("stt"),
            "tracks": sorted(d.get("tracks") or {}),
            # เฉพาะงานบอท — ฝั่งประมวลผลต้องรู้ว่าจะเข้าห้องไหน ในชื่ออะไร นานเท่าไร
            "url": d.get("url"),
            "bot_name": d.get("bot_name"),
            "max_minutes": d.get("max_minutes"),
        }

    mid = _meeting_of(job)
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
        spec["lang"] = job.get("_lang") or (job.get("_spec") or {}).get("lang")
        spec["summary"] = meeting.get("summary") or ""
    return spec


def track_path(job_id: str, name: str) -> Path | None:
    """path ของแทร็กบนดิสก์ของฝั่งที่ถือไฟล์ (ใช้ทั้งโหมดในเครื่องและตอนให้ worker ดาวน์โหลด)."""
    d = draft(job_id)
    if d is None:
        return None
    raw = (d.get("tracks") or {}).get(name)
    return Path(raw) if raw else None


# ---------- นำผลลัพธ์เข้าคลัง ----------

def apply_result(job_id: str, result: dict) -> None:
    job = get(job_id)
    if job is None:
        raise RuntimeError("ไม่พบงานนี้")
    kind = job["kind"]

    if kind in ("process", "bot"):
        d = draft(job_id)
        if d is None:
            raise RuntimeError("ไม่พบข้อมูลการประชุมที่รออัปโหลด")
        playback = result.get("playback")
        tracks = d.get("tracks") or {}
        audio_name = (Path(playback).name if playback
                      else Path(sorted(tracks.values())[0]).name if tracks else "")
        store.create(
            mid=job_id,
            title=d["title"],
            audio_name=audio_name,
            source=d.get("source") or ("bot" if kind == "bot" else "upload"),
            language=result.get("language") or config.whisper_lang,
            duration=result.get("duration") or 0.0,
            segments=result.get("segments") or [],
            summary=result.get("summary") or "",
            summary_error=result.get("summary_error"),
            template=d.get("template") or "general",
            speakers=result.get("speakers") or [],
            owner_id=d.get("owner_id"),
        )
        if not cloud:
            with _cv:
                _drafts.pop(job_id, None)
                _stop_requests.discard(job_id)
        meeting_id = job_id

    elif kind == "summarize":
        meeting_id = _meeting_of(job)
        store.set_summary(meeting_id, result.get("summary") or "",
                          error=result.get("summary_error"))
    else:
        meeting_id = _meeting_of(job)
        store.set_translation(meeting_id, result["lang"], result["text"])

    warning = result.get("warning")
    if result.get("summary_error"):
        warning = (f"สรุปไม่สำเร็จ: {result['summary_error']} — บทถอดเสียงเก็บไว้แล้ว "
                   "กด “สรุปใหม่ด้วย AI” เพื่อลองอีกครั้ง")
    step = "ถอดเสียงเสร็จ แต่สรุปไม่ได้" if result.get("summary_error") else "เสร็จ"
    if cloud:
        store.job_done(job_id, meeting_id, step, warning)
    else:
        _set(job_id, status="done", step=step, progress=1.0,
             meeting_id=meeting_id, warning=warning)


def fail(job_id: str, error: str) -> None:
    if cloud:
        store.job_fail(job_id, error)
    else:
        _set(job_id, status="error", step="ผิดพลาด", error=error)


def report_progress(job_id: str, step: str, progress: float) -> None:
    if cloud:
        store.job_progress(job_id, step, progress)
    else:
        _set(job_id, status="running", step=step, progress=max(0.0, min(1.0, progress)))


# ---------- ฝั่ง worker แยกเครื่อง ----------

def claim(worker: str | None = None, kinds: list[str] | None = None) -> dict | None:
    """หยิบงานถัดไปให้ worker ที่อยู่ไกล — คืน spec หรือ None ถ้าคิวว่าง.

    kinds = ชนิดงานที่เครื่องนั้นทำได้ (งาน bot ต้องมี Docker + image + profile)
    """
    if cloud:
        # คืนงานที่ worker เก่าหลุดไปกลางทางกลับเข้าคิวก่อน
        store.jobs_reap(30)
        while True:
            job = store.job_claim(worker, kinds)
            if job is None:
                return None
            spec = build_spec(job["id"])
            if spec is None:
                fail(job["id"], "ข้อมูลของงานนี้หายไปก่อนจะได้ประมวลผล")
                continue
            if worker:
                store.worker_seen(worker, "busy", job["id"])
            return spec

    skipped: list[str] = []
    with _cv:
        while _pending:
            job_id = _pending.popleft()
            spec = build_spec(job_id)
            if spec is not None and kinds is not None and spec["kind"] not in kinds:
                # เครื่องนี้ทำงานชนิดนี้ไม่ได้ — พักไว้แล้วมองงานถัดไป
                # ถ้า return ทันทีตรงนี้ งานที่ทำไม่ได้หนึ่งงานจะขวางคิวทั้งคิวไปตลอด
                skipped.append(job_id)
                continue
            if spec is None:
                # draft/การประชุมหายไปแล้ว (ถูกลบ?) ข้ามไป
                fail(job_id, "ข้อมูลของงานนี้หายไปก่อนจะได้ประมวลผล")
                continue
            report_progress(job_id, "worker รับงานแล้ว", 0.01)
            _pending.extendleft(reversed(skipped))
            return spec
        _pending.extendleft(reversed(skipped))
    return None


_stop_requests: set[str] = set()   # โหมดไฟล์: เก็บในหน่วยความจำพอ เพราะ worker อยู่โพรเซสเดียวกัน


def request_stop(job_id: str) -> bool:
    """ขอให้งานนี้หยุด — สำหรับงานบอทหมายถึง "ออกจากห้องแล้วไปสรุปเลย"."""
    if cloud:
        return store.job_request_stop(job_id)
    with _cv:
        if job_id not in _jobs or _jobs[job_id]["status"] not in ("queued", "running"):
            return False
        _stop_requests.add(job_id)
    return True


def stop_requested(job_id: str) -> bool:
    if cloud:
        return store.job_stop_requested(job_id)
    with _cv:
        return job_id in _stop_requests


def requeue(job_id: str) -> bool:
    """คืนงานกลับคิว (worker หลุดกลางทาง)."""
    if cloud:
        return store.job_requeue(job_id)
    with _cv:
        if job_id not in _jobs:
            return False
        _pending.append(job_id)
        _cv.notify_all()
    _set(job_id, status="queued", step="รอคิว", progress=0.0)
    return True


# ---------- ถอดเสียงสด ----------

def transcribe_clip(path: Path, language: str | None, prompt: str | None = None) -> str:
    """ถอดเสียงคลิปสั้นสำหรับแสดงสดระหว่างประชุม.

    ใช้ lock แยกจากคิวหลัก คลิปสดจะได้ไม่ต้องรอหลังงานถอดเสียงไฟล์ยาว
    prompt = ข้อความจากคลิปก่อนหน้า ช่วยให้ถอดต่อเนื่องแม่นขึ้น
    """
    with _live_lock:
        return transcriber.transcribe(path, language=language, prompt=prompt).text


# ---------- เธรดประมวลผลในเครื่อง ----------

def _execute(spec: dict) -> None:
    """ทำงานตาม spec ในโพรเซสนี้.

    ไฟล์เสียงอาจอยู่บนดิสก์ (โหมดในเครื่อง) หรือในที่เก็บภายนอกอย่าง R2 (โหมด cloud)
    ต้องผ่าน storage เสมอ ห้ามสมมติว่าเป็น path บนดิสก์
    """
    job_id = spec["id"]

    def progress(step: str, value: float) -> None:
        report_progress(job_id, step, value)

    if spec["kind"] not in ("process", "bot"):
        return apply_result(job_id, runner.HANDLERS[spec["kind"]](spec, progress))

    storage = backend.storage()
    external = storage.kind != "local"

    with tempfile.TemporaryDirectory(prefix="mai-job-") as tmpdir:
        tmp = Path(tmpdir)

        def fetch(name: str) -> Path:
            ref = track_path(job_id, name)
            if ref is None:
                raise RuntimeError(f"ไม่มีข้อมูลแทร็ก {name}")
            key = ref.name
            local = storage.local_path(key)
            if local is not None:
                return local
            if not storage.exists(key):
                raise RuntimeError(f"ไม่พบไฟล์แทร็ก {name} ในที่เก็บ ({key})")
            dest = tmp / key
            dest.write_bytes(storage.get(key))
            return dest

        if external:
            mix_dir = tmp
        else:
            store.WEB_DIR.mkdir(parents=True, exist_ok=True)
            mix_dir = store.WEB_DIR

        if spec["kind"] == "bot":
            # บอทอัดไฟล์เองในโฟลเดอร์ชั่วคราว ไม่มีแทร็กให้ fetch
            result = runner.bot_job(spec, progress, mix_dir,
                                    stop_check=lambda: stop_requested(job_id), work_dir=tmp)
        else:
            result = runner.transcribe_job(spec, fetch, progress, mix_dir)

        # ไฟล์เสียงผสมเกิดในโฟลเดอร์ชั่วคราว ต้องส่งขึ้นที่เก็บก่อนโฟลเดอร์ถูกลบ
        playback = result.get("playback")
        if external and playback:
            src = Path(playback)
            progress("อัปโหลดไฟล์เสียงผสม", runner.SUMMARY_END)
            storage.put(src.name, src.read_bytes())
            result["playback"] = src.name

    apply_result(job_id, result)


LOCAL_WORKER_NAME = f"{socket.gethostname()} (ในโพรเซสเว็บ)"


def _next_spec() -> dict | None:
    """หยิบงานถัดไปให้เธรดในเครื่อง — โหมด cloud อ่านคิวจาก DB, โหมดไฟล์รอที่ condition."""
    if cloud:
        # ลงทะเบียนตัวเองด้วย ไม่งั้นหน้าเว็บจะไม่เห็นว่ามีใครทำงานอยู่
        try:
            store.worker_seen(LOCAL_WORKER_NAME, "idle")
        except Exception:
            pass
        return claim(LOCAL_WORKER_NAME, runner.job_kinds(runner.machine_caps()))
    with _cv:
        while not _pending:
            _cv.wait()
        job_id = _pending.popleft()
    spec = build_spec(job_id)
    if spec is None:
        fail(job_id, "ไม่พบข้อมูลของงานนี้ (อาจถูกลบไปแล้ว)")
        return None
    return spec


def _loop() -> None:
    while True:
        try:
            spec = _next_spec()
        except Exception:
            traceback.print_exc()
            time.sleep(2.0)
            continue
        if spec is None:
            if cloud:
                time.sleep(1.5)   # คิวว่าง — DB ไม่มี notify ต้อง poll
            continue
        try:
            _execute(spec)
        except Exception as e:
            traceback.print_exc()
            fail(spec["id"], str(e))
        finally:
            if cloud:
                try:
                    store.worker_finished(LOCAL_WORKER_NAME)
                except Exception:
                    pass


def _ensure_worker() -> None:
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_loop, name="meeting-ai-worker", daemon=True)
            _worker.start()
