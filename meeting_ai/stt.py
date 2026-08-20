"""เลือกตัวถอดเสียงได้ระหว่างในเครื่อง (whisper.cpp) กับ API แบบ OpenAI-compatible.

local  — whisper.cpp บนเครื่อง ฟรี เสียงไม่ออกจากเครื่อง แต่ต้องมี GPU ถึงจะเร็ว
api    — ส่งไฟล์ไปถอดที่ /audio/transcriptions ไม่ต้องมี GPU แต่เสียเงินและเสียงออกจากเครื่อง

ใช้ได้กับ OpenAI ตรงๆ, LiteLLM proxy, Groq หรืออะไรก็ได้ที่พูดสเปกเดียวกัน
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from . import transcriber
from .config import config
from .transcriber import Segment, Transcript

LOCAL = "local"
API = "api"

# OpenAI จำกัดไฟล์ที่ 25 MB — เผื่อไว้หน่อย
MAX_UPLOAD_BYTES = 24 * 1024 * 1024
# ถ้าไฟล์ใหญ่เกิน บีบเป็น opus ก่อน (เสียงพูดที่ 24kbps ยังถอดได้ดี)
COMPRESS_BITRATE = "24k"
# ยังใหญ่เกินอีกก็ตัดเป็นท่อนตามเวลา
CHUNK_SECONDS = 900


def local_available() -> bool:
    import shutil

    return config.whisper_model_path().exists() and (
        shutil.which(config.whisper_bin) is not None or Path(config.whisper_bin).exists()
    )


def api_host() -> str:
    import urllib.parse

    return urllib.parse.urlparse(config.stt_base_url()).netloc or config.stt_base_url()


def capabilities() -> dict:
    """ความสามารถของเครื่องนี้ — worker ส่งค่านี้ไปให้เซิร์ฟเวอร์ตอน heartbeat."""
    return {
        "local": local_available(),
        "api": bool(config.stt_key()),
        "stt_model": config.stt_model,
        "stt_host": api_host(),
    }


def providers(caps: dict | None = None) -> dict[str, dict]:
    """ตัวถอดเสียงที่เลือกได้.

    caps = ความสามารถที่ worker รายงานมา (โหมด cloud ที่ตัวเซิร์ฟเวอร์ถอดเสียงเองไม่ได้)
    ไม่ส่งมา = ดูจากเครื่องที่รันโค้ดนี้เอง
    """
    from_worker = caps is not None
    if from_worker:
        local_ok = bool(caps.get("local"))
        api_ok = bool(caps.get("api"))
        model = caps.get("stt_model") or config.stt_model
        host = caps.get("stt_host") or ""
        no_worker = not caps.get("workers")
    else:
        local_ok = local_available()
        api_ok = bool(config.stt_key())
        model = config.stt_model
        host = api_host()
        no_worker = False

    if no_worker:
        why_local = why_api = "ยังไม่มีเครื่องประมวลผลออนไลน์ — เปิด worker ก่อน"
    else:
        why_local = "" if local_ok else (
            "เครื่องประมวลผลยังไม่ได้ติดตั้ง whisper.cpp หรือไม่มีไฟล์โมเดล" if from_worker
            else "ยังไม่ได้ติดตั้ง whisper.cpp หรือไม่มีไฟล์โมเดล")
        why_api = "" if api_ok else (
            "เครื่องประมวลผลยังไม่ได้ตั้ง STT_API_KEY" if from_worker
            else "ยังไม่ได้ตั้ง STT_API_KEY (หรือ LLM_API_KEY)")

    return {
        LOCAL: {
            "label": "ในเครื่อง (whisper.cpp)",
            "available": local_ok,
            "why": why_local,
            "note": "ฟรี ใช้ GPU ของเครื่องประมวลผล เสียงไม่ออกไปที่อื่น",
        },
        API: {
            # บอกปลายทางด้วย ไม่งั้นไม่รู้ว่า "API" คืออะไรของใคร
            "label": f"API — {host} ({model})" if host else f"API ({model})",
            # เช็คได้แค่ว่ามีคีย์ ยืนยันไม่ได้ว่าคีย์นั้นเข้าถึงโมเดลถอดเสียงได้จริง
            # จนกว่าจะยิงจริง — error ตอนใช้จะบอกเองว่าคีย์ไม่มีสิทธิ์
            "available": api_ok,
            "why": why_api,
            "note": ("ไม่ต้องมี GPU แต่เสียเงินและเสียงถูกอัปโหลดไปที่ผู้ให้บริการ "
                     "— คีย์ต้องมีสิทธิ์เข้าโมเดลถอดเสียงด้วย"),
        },
    }


def resolve(name: str | None) -> str:
    """เลือกตัวที่ใช้ได้จริง — ถ้าที่ขอมาใช้ไม่ได้ ตกไปใช้อีกตัว."""
    avail = providers()
    want = (name or config.stt_provider or LOCAL).strip().lower()
    if want in avail and avail[want]["available"]:
        return want
    for other, info in avail.items():
        if info["available"]:
            return other
    raise RuntimeError(
        "ไม่มีตัวถอดเสียงที่ใช้ได้เลย — "
        + "; ".join(f"{k}: {v['why']}" for k, v in avail.items() if v["why"])
    )


# ---------- ฝั่ง API ----------

def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run([config.ffmpeg_bin, "-y", "-loglevel", "error", *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg ล้มเหลว:\n{proc.stderr[-800:]}")


def _multipart(fields: dict[str, str], file_path: Path) -> tuple[bytes, str]:
    boundary = "----meetingai" + uuid.uuid4().hex
    body = b""
    for key, value in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                 f"{value}\r\n").encode("utf-8")
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"{file_path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode("utf-8")
    body += file_path.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")
    return body, f"multipart/form-data; boundary={boundary}"


def _post_audio(path: Path, language: str | None, prompt: str | None, verbose: bool) -> dict:
    fields = {"model": config.stt_model}
    if verbose:
        fields["response_format"] = "verbose_json"
        fields["timestamp_granularities[]"] = "segment"
    else:
        fields["response_format"] = "json"
    if language and language != "auto":
        fields["language"] = language
    if prompt:
        fields["prompt"] = prompt[-800:]

    body, ctype = _multipart(fields, path)
    req = urllib.request.Request(f"{config.stt_base_url()}/audio/transcriptions",
                                 data=body, method="POST")
    req.add_header("Authorization", f"Bearer {config.stt_key()}")
    req.add_header("Content-Type", ctype)
    req.add_header("User-Agent", "meeting_ai/1.0")
    try:
        with urllib.request.urlopen(req, timeout=900) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"ถอดเสียงผ่าน API ไม่สำเร็จ (HTTP {e.code}): {detail}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ต่อ endpoint ถอดเสียงไม่ได้: {e.reason}") from e


def _segments_from(data: dict, offset: float) -> list[Segment]:
    raw = data.get("segments")
    if raw:
        return [
            Segment(start=float(s.get("start", 0)) + offset,
                    end=float(s.get("end", 0)) + offset,
                    text=(s.get("text") or "").strip())
            for s in raw if (s.get("text") or "").strip()
        ]
    # บางโมเดล (เช่น gpt-4o-transcribe) ไม่คืน segment ให้ ได้แต่ข้อความก้อนเดียว
    text = (data.get("text") or "").strip()
    return [Segment(start=offset, end=offset, text=text)] if text else []


def _prepare(src: Path, workdir: Path) -> list[tuple[Path, float]]:
    """คืนรายการ (ไฟล์, offset วินาที) ที่พร้อมส่งขึ้น API — บีบและตัดท่อนถ้าจำเป็น."""
    if src.stat().st_size <= MAX_UPLOAD_BYTES:
        return [(src, 0.0)]

    packed = workdir / "packed.ogg"
    _ffmpeg(["-i", str(src), "-ac", "1", "-c:a", "libopus", "-b:a", COMPRESS_BITRATE, str(packed)])
    if packed.stat().st_size <= MAX_UPLOAD_BYTES:
        return [(packed, 0.0)]

    # ยังใหญ่เกิน (ประชุมยาวมาก) ตัดเป็นท่อนแล้วเลื่อน timestamp ให้ต่อกัน
    pattern = str(workdir / "part-%03d.ogg")
    _ffmpeg(["-i", str(packed), "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
             "-c", "copy", pattern])
    parts = sorted(workdir.glob("part-*.ogg"))
    if not parts:
        raise RuntimeError("ตัดไฟล์เสียงเป็นท่อนไม่สำเร็จ")
    return [(p, i * float(CHUNK_SECONDS)) for i, p in enumerate(parts)]


def _transcribe_api(
    audio_path: Path,
    language: str | None,
    on_progress: Callable[[float], None] | None,
    prompt: str | None,
) -> Transcript:
    if not config.stt_key():
        raise RuntimeError("ยังไม่ได้ตั้ง STT_API_KEY (หรือ LLM_API_KEY) สำหรับถอดเสียงผ่าน API")

    with tempfile.TemporaryDirectory(prefix="mai-stt-") as tmp:
        parts = _prepare(audio_path, Path(tmp))
        segments: list[Segment] = []
        detected = language or config.whisper_lang
        for i, (part, offset) in enumerate(parts):
            if on_progress:
                on_progress(i / len(parts))
            try:
                data = _post_audio(part, language, prompt, verbose=True)
            except RuntimeError as e:
                # โมเดลที่ไม่รองรับ verbose_json จะฟ้อง 400 — ลองแบบธรรมดาอีกที
                if "400" not in str(e):
                    raise
                data = _post_audio(part, language, prompt, verbose=False)
            segments.extend(_segments_from(data, offset))
            lang = data.get("language")
            if lang:
                detected = lang
        if on_progress:
            on_progress(1.0)

    # ไม่มี segments = API ตอบ 200 แต่ไม่เจอเสียงพูด ซึ่งคือ "ไฟล์เงียบ" ไม่ใช่ API พัง
    # ปล่อยให้ว่างแล้วให้ runner เป็นคนบอกสาเหตุ (ทางเดียวกับ whisper ในเครื่อง)
    # ไม่งั้นข้อความ error จะชี้ไปที่ API ทั้งที่ปัญหาอยู่ที่ต้นทางเสียง
    return Transcript(language=detected, segments=segments)


# ---------- ทางเข้าเดียว ----------

def transcribe(
    audio_path: str | Path,
    language: str | None = None,
    on_progress: Callable[[float], None] | None = None,
    prompt: str | None = None,
    provider: str | None = None,
) -> tuple[Transcript, str]:
    """ถอดเสียงด้วยตัวที่เลือก คืน (Transcript, ชื่อ provider ที่ใช้จริง)."""
    used = resolve(provider)
    path = Path(audio_path)
    if used == LOCAL:
        return transcriber.transcribe(path, language=language, on_progress=on_progress,
                                      prompt=prompt), used
    return _transcribe_api(path, language, on_progress, prompt), used
