"""เลือกตัวถอดเสียงได้ระหว่างในเครื่อง (whisper.cpp) กับ API แบบ OpenAI-compatible.

local  — whisper.cpp บนเครื่อง ฟรี เสียงไม่ออกจากเครื่อง แต่ต้องมี GPU ถึงจะเร็ว
api    — ส่งไฟล์ไปถอดที่ /audio/transcriptions ไม่ต้องมี GPU แต่เสียเงินและเสียงออกจากเครื่อง

ใช้ได้กับ OpenAI ตรงๆ, LiteLLM proxy, Groq หรืออะไรก็ได้ที่พูดสเปกเดียวกัน
"""

from __future__ import annotations

import json
import mimetypes
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

from . import transcriber
from .config import config
from .transcriber import Segment, Transcript
from . import log as _log

log = _log.get(__name__)

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


_announced: set[str] = set()


def _notice(msg: str) -> None:
    """บอกว่าเลือกตัวถอดเสียงอะไร — ข้อความเดิมออกครั้งเดียวต่อโพรเซส.

    กันซ้ำเพราะ CLI เรียก resolve() เองแล้วส่งค่าที่ได้ต่อให้ transcribe() ซึ่ง resolve() ซ้ำอีกรอบ

    เดิมต้องลองเขียนสองรอบ (ไทย แล้วค่อย ascii) เพราะคอนโซล cp874 เขียนภาษาไทยไม่ได้ และ
    UnicodeEncodeError จากบรรทัดเตือนเคยทำให้ทั้งงานล้ม (BUG-045) — ตั้งแต่ย้ายมาใช้ logging
    (BACKLOG #35) handler จัดการข้อยกเว้นของตัวเอง บรรทัด log จึงไม่มีทางล้มงานที่เรียกมันอีก
    """
    if msg in _announced:
        return
    _announced.add(msg)
    log.warning(msg)


def reset_notices() -> None:
    """ให้เทสเห็นบรรทัดเตือนเดิมได้อีกครั้ง — โค้ดจริงไม่ต้องเรียก."""
    _announced.clear()


def _destination() -> str:
    """ปลายทางที่ไฟล์เสียงจะถูกส่งไปถ้าใช้ api — คำเตือนต้องบอกชื่อบริการเสมอ ไม่งั้นไม่มีความหมาย."""
    return f"{api_host() or 'ปลายทางที่ยังไม่ได้ตั้งค่า'} (โมเดล {config.stt_model})"


def resolve(name: str | None) -> str:
    """เลือกตัวถอดเสียงที่ใช้ได้จริง — และประกาศทุกครั้งที่ผลลัพธ์คือเสียงออกนอกเครื่อง

    BUG-019: เดิมถ้าตัวที่ขอใช้ไม่ได้ จะวนหยิบอีกตัวมาให้เงียบ ๆ "ขอ local แล้วเครื่องไม่มี whisper"
    จึงกลายเป็นการอัปโหลดไฟล์ประชุมทั้งไฟล์ไปหาบุคคลที่สามโดยไม่มีใครสั่งและไม่มีใครรู้

    สองทิศทางไม่เท่ากัน: local -> api = เสียงออกจากเครื่อง (ห้ามตัดสินใจแทนคนที่เลือก local เอง)
    ส่วน api -> local = เสียงยังอยู่กับเครื่อง (ทำได้ แค่ต้องบอก)

    "ขอ local" มีสองแบบ: มีคนสั่งเอง (--stt local, dropdown ในหน้าเว็บ, STT_PROVIDER=local) กับ
    ได้ local มาเพราะเป็นค่าเริ่มต้นในโค้ดโดยไม่มีใครตั้งอะไรเลย — แบบหลังคือเส้นทางปกติของ Vercel
    ที่ไม่มี whisper อยู่แล้ว ถ้าทำให้เป็น error การถอดเสียงบน cloud จะพังทั้งระบบ จึงยังตกไป api ได้
    แต่ต้องมีบรรทัดบอกปลายทาง ส่วนแบบแรกคือคำสั่ง "ห้ามส่งเสียงออก" ต้องหยุดให้เห็น
    """
    avail = providers()
    asked = (name or "").strip().lower()
    # config.stt_provider มีค่า "local" เสมอแม้ไม่มีใครตั้ง จึงต้องดู stt_provider_set ประกอบ
    want = asked or (config.stt_provider if config.stt_provider_set else "") or LOCAL
    chosen = bool(asked) or config.stt_provider_set

    if want not in avail:
        raise RuntimeError(f"ไม่รู้จักตัวถอดเสียง '{want}' — ใช้ได้แค่ {LOCAL} หรือ {API}")

    if avail[want]["available"]:
        if want == API:
            _notice(f"⚠️  ถอดเสียงผ่าน API: ไฟล์เสียงจะถูกอัปโหลดไปที่ {_destination()}")
        return want

    other = API if want == LOCAL else LOCAL
    if not avail[other]["available"]:
        raise RuntimeError(
            "ไม่มีตัวถอดเสียงที่ใช้ได้เลย — "
            + "; ".join(f"{k}: {v['why']}" for k, v in avail.items() if v["why"])
        )

    if want == API:
        # ตกกลับเข้าเครื่องตัวเอง ไม่อันตราย แต่ผู้ใช้ขออย่างอื่นไว้ ต้องรู้ว่าทำไมได้ไม่ตรงที่ขอ
        _notice(f"ℹ️  ใช้ API ถอดเสียงไม่ได้ ({avail[API]['why']}) "
                "— ถอดด้วย whisper.cpp ในเครื่องแทน เสียงไม่ออกจากเครื่อง")
        return LOCAL

    if chosen:
        raise RuntimeError(
            f"ขอถอดเสียงในเครื่อง ({LOCAL}) แต่ใช้ไม่ได้: {avail[LOCAL]['why']} "
            f"— จะไม่ส่งไฟล์เสียงออกนอกเครื่องให้เอง ถ้าต้องการให้อัปโหลดไปถอดที่ {_destination()} "
            f"ต้องสั่งเอง (--stt {API} หรือตั้ง STT_PROVIDER={API})"
        )

    _notice(f"⚠️  ไม่มีตัวถอดเสียงในเครื่อง ({avail[LOCAL]['why']}) "
            f"— ไฟล์เสียงจะถูกอัปโหลดไปถอดที่ {_destination()} "
            f"· ถ้าไม่ต้องการให้เสียงออกจากเครื่อง ตั้ง STT_PROVIDER={LOCAL} "
            "แล้วติดตั้ง whisper.cpp + ไฟล์โมเดล (งานจะหยุดแทนการอัปโหลด)")
    return API


def label(name: str) -> str:
    """ป้ายชื่อ provider แบบเดียวกับ dropdown ในหน้าเว็บ — CLI ใช้บอกว่างานนี้ถอดด้วยอะไร."""
    return providers()[name]["label"] if name in (LOCAL, API) else name


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
    #
    # -reset_timestamps 1 ไม่ใช่ของประดับ: Ogg เก็บ granule position แบบ "เวลาสัมบูรณ์ของสตรีมเดิม"
    # ตัดด้วย -c copy เฉยๆ ท่อนที่ห้าจึงเป็นไฟล์ที่ **ประกาศว่าตัวเองยาว 0→หลายนาที** ทั้งที่มีเสียง
    # อยู่แค่ช่วงท้าย (วัดจริงด้วย ffprobe: ท่อนละ 7 วิ ได้ duration 7, 14, 21, 28... ตามลำดับท่อน)
    # ตัวถอดเสียงฝั่ง API ก็ decode ด้วย libav เหมือนกัน มันจึงคืน timestamp ที่บวก offset มาแล้ว
    # แล้วโค้ดนี้บวก i*900 ทับเข้าไปอีก = **เลื่อนสองเท่า** ไม่ใช่แค่คลาดนิดหน่อยอย่างที่ตั๋วเขียน
    #
    # และ offset ต้องถามจาก ffmpeg เอง (-segment_list csv) ไม่ใช่คำนวณจากลำดับท่อน: -segment_time
    # เป็น "ตัดที่แพ็กเก็ตแรกตั้งแต่เวลานี้ไป" ไม่ใช่ตัดตรงเป๊ะ ความยาวจริงของแต่ละท่อนจึงไม่เท่ากัน
    # เสมอไป และความคลาดจะสะสมทบกันไปทุกท่อน
    pattern = str(workdir / "part-%03d.ogg")
    listing = workdir / "parts.csv"
    _ffmpeg(["-i", str(packed), "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
             "-reset_timestamps", "1", "-c", "copy",
             "-segment_list", str(listing), "-segment_list_type", "csv", pattern])
    parts = sorted(workdir.glob("part-*.ogg"))
    if not parts:
        raise RuntimeError("ตัดไฟล์เสียงเป็นท่อนไม่สำเร็จ")
    return list(zip(parts, _segment_offsets(listing, parts)))


def _segment_offsets(listing: Path, parts: list[Path]) -> list[float]:
    """เวลาเริ่มจริงของแต่ละท่อน ตามที่ ffmpeg เขียนรายการไว้เอง (ชื่อไฟล์,เริ่ม,จบ)."""
    starts: dict[str, float] = {}
    try:
        for row in listing.read_text(encoding="utf-8").splitlines():
            cols = row.split(",")
            if len(cols) >= 2 and cols[0].strip():
                starts[Path(cols[0].strip()).name] = float(cols[1])
    except (OSError, ValueError):
        starts = {}
    if len(starts) == len(parts) and all(p.name in starts for p in parts):
        return [starts[p.name] for p in parts]
    # ffmpeg รุ่นที่ไม่เขียนรายการให้ — กลับไปใช้ค่าตามลำดับท่อน ซึ่งถูกเมื่อตัดได้ตรงเป๊ะเท่านั้น
    # บอกให้รู้ตัว ดีกว่าปล่อยให้ timestamp เพี้ยนเงียบๆ แบบเดิม
    _notice("⚠️  อ่านเวลาเริ่มของแต่ละท่อนจาก ffmpeg ไม่ได้ — ใช้ค่าตามลำดับท่อนแทน "
            "timestamp ของประชุมที่ยาวมากอาจคลาดเคลื่อน")
    return [i * float(CHUNK_SECONDS) for i in range(len(parts))]


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
    #
    # กรองข้อความหลอนด้วยตัวเดียวกับทางในเครื่อง (BACKLOG #18): การวนคำเดิมซ้ำๆ ตอนเจอช่วงเงียบ
    # เป็นพฤติกรรมของโมเดล whisper ไม่ใช่ของ "ที่รัน" — ฝั่ง API ก็วนเหมือนกัน แต่เดิมไม่มีใครกรอง
    # ขยะจึงไหลเข้าบทถอดเสียง ไปโผล่ในสรุป และกิน token ของ LLM ไปฟรีๆ
    return Transcript(language=detected,
                      segments=transcriber.drop_hallucinations(segments))


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
