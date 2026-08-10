"""ถอดเสียงเป็นข้อความด้วย whisper.cpp (whisper-cli)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import config

_PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)%")

# ความยาว prompt สูงสุดที่ส่งให้ whisper — ยาวกว่านี้ไม่ช่วย แต่กินหน้าต่างบริบท
PROMPT_MAX_CHARS = 400


@dataclass
class Segment:
    start: float  # วินาที
    end: float
    text: str

    @property
    def ts(self) -> str:
        return f"{_fmt(self.start)} - {_fmt(self.end)}"


@dataclass
class Transcript:
    language: str
    segments: list[Segment]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments).strip()

    def to_timestamped(self) -> str:
        return "\n".join(f"[{s.ts}] {s.text.strip()}" for s in self.segments)


def _fmt(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _check(bin_name: str, hint: str) -> None:
    if shutil.which(bin_name) is None and not Path(bin_name).exists():
        raise RuntimeError(f"ไม่พบคำสั่ง '{bin_name}'. {hint}")


def _to_wav16k(src: Path, dst: Path) -> None:
    """แปลงไฟล์เสียงใดๆ เป็น WAV 16kHz mono ตามที่ whisper.cpp ต้องการ."""
    _check(config.ffmpeg_bin, "ติดตั้งด้วย: brew install ffmpeg")
    cmd = [
        config.ffmpeg_bin, "-y", "-i", str(src),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        str(dst),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg แปลงไฟล์ล้มเหลว:\n{proc.stderr[-2000:]}")


def _run_whisper(cmd: list[str], on_progress: Callable[[float], None] | None) -> None:
    """รัน whisper-cli. ถ้ามี on_progress จะอ่าน output ทีละบรรทัดเพื่อรายงาน % ระหว่างทาง."""
    if on_progress is None:
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"whisper-cli ล้มเหลว:\n{proc.stderr[-2000:]}")
        return

    # รวม stderr เข้า stdout ได้เพราะผลลัพธ์จริงเขียนลงไฟล์ JSON ไม่ได้ออกทาง stdout
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    tail: deque[str] = deque(maxlen=40)
    assert proc.stdout is not None
    for line in proc.stdout:
        tail.append(line)
        m = _PROGRESS_RE.search(line)
        if m:
            on_progress(int(m.group(1)) / 100.0)
    if proc.wait() != 0:
        raise RuntimeError(f"whisper-cli ล้มเหลว:\n{''.join(tail)[-2000:]}")


def to_wav16k(src: str | Path, dst: str | Path) -> Path:
    """แปลงไฟล์เสียงใดๆ เป็น WAV 16kHz mono (ใช้ร่วมกับ diarization ที่ต้องการฟอร์แมตเดียวกัน)."""
    _to_wav16k(Path(src), Path(dst))
    return Path(dst)


def transcribe(
    audio_path: str | Path,
    language: str | None = None,
    on_progress: Callable[[float], None] | None = None,
    prompt: str | None = None,
) -> Transcript:
    """ถอดเสียงไฟล์ -> Transcript (มี timestamp รายประโยค).

    on_progress: ถ้าส่งมา จะถูกเรียกด้วยค่า 0.0-1.0 ระหว่างถอดเสียง
    prompt: ข้อความก่อนหน้า ใช้เป็นบริบทให้ถอดต่อได้แม่นขึ้น (สำคัญกับการถอดสดที่ตัดเป็นคลิป)
    """
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"ไม่พบไฟล์เสียง: {audio_path}")

    model = config.whisper_model_path()
    if not model.exists():
        raise RuntimeError(
            f"ไม่พบโมเดล whisper: {model}\nดาวน์โหลดด้วย: ./setup.sh  (หรือดูใน README)"
        )
    _check(config.whisper_bin, "ติดตั้งด้วย: brew install whisper-cpp")

    lang = language or config.whisper_lang

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio16k.wav"
        _to_wav16k(audio_path, wav)

        out_prefix = Path(tmp) / "out"
        cmd = [
            config.whisper_bin,
            "-m", str(model),
            "-f", str(wav),
            "-l", lang,
            "-t", str(config.whisper_threads),  # ใช้หลาย thread
            "-oj",                     # เขียนผลเป็น JSON
            "-of", str(out_prefix),
        ]
        # VAD ตัดช่วงเงียบออกก่อน — กันอาการหลอนคำซ้ำเมื่อป้อนความเงียบให้โมเดล
        # (เจอจริง: แทร็กไมค์ที่ไม่มีคนพูด ได้ "เจ้าไหรือ" ซ้ำ 20 รอบยาว 30 วิพอดี)
        vad = config.vad_model_path()
        if vad:
            cmd += ["--vad", "--vad-model", str(vad)]
        # ไม่เอา token ที่ไม่ใช่คำพูด เช่น (เสียงดนตรี) [เสียงหัวเราะ]
        cmd.append("--suppress-nst")
        if prompt:
            # จำกัดความยาว — prompt ยาวเกินไปกินหน้าต่างบริบทของโมเดลเอง
            cmd += ["--prompt", prompt.strip()[-PROMPT_MAX_CHARS:]]
        # ไม่ต้องรายงาน % ก็ปิด output รกไปเลย
        cmd.append("--print-progress" if on_progress else "-np")
        _run_whisper(cmd, on_progress)

        data = json.loads((out_prefix.with_suffix(".json")).read_text(encoding="utf-8"))

    segments: list[Segment] = []
    for t in data.get("transcription", []):
        offsets = t.get("offsets", {})
        segments.append(
            Segment(
                start=offsets.get("from", 0) / 1000.0,
                end=offsets.get("to", 0) / 1000.0,
                text=t.get("text", ""),
            )
        )
    detected = data.get("result", {}).get("language", lang)
    return Transcript(language=detected, segments=drop_hallucinations(segments))


# ---------- ตัวกรองข้อความหลอน ----------

# ถ้า VAD ไม่ได้ทำงาน (ไม่มีไฟล์โมเดล) หรือปล่อยเสียงเบาๆ ผ่านมา โมเดลจะวนคำเดิมซ้ำ
# เช่น "ที่สุด" ×40 หรือ "เจ้าไหรือ" ×20 — ทิ้งช่วงพวกนี้ ดีกว่าปล่อยขยะเข้าบทถอดเสียง
REPEAT_MIN_LEN = 2      # ความยาววลีที่จะไล่หา (ตัวอักษร)
REPEAT_MAX_LEN = 12
REPEAT_TIMES = 6        # ซ้ำเกินเท่านี้ถือว่าหลอน
REPEAT_COVER = 0.7      # และต้องกินพื้นที่ข้อความเกินสัดส่วนนี้


REPEAT_MAX_SKIP = 24    # การวนอาจเริ่มหลังคำนำ เช่น "ที่สุดท้าย" แล้วค่อยวน "ที่สุด"


def looks_hallucinated(text: str) -> bool:
    """จริงไหมว่าข้อความนี้เป็นการวนคำเดิมซ้ำๆ."""
    squashed = re.sub(r"\s+", "", text)
    if len(squashed) < REPEAT_MIN_LEN * REPEAT_TIMES:
        return False
    for start in range(0, min(REPEAT_MAX_SKIP, len(squashed))):
        rest = squashed[start:]
        if len(rest) < REPEAT_MIN_LEN * REPEAT_TIMES:
            break
        for size in range(REPEAT_MIN_LEN, REPEAT_MAX_LEN + 1):
            phrase = rest[:size]
            times = 0
            while rest[times * size:(times + 1) * size] == phrase:
                times += 1
            # ต้องซ้ำถี่ และกินพื้นที่เกือบทั้งข้อความ (นับจากต้นจริงๆ ไม่ใช่จากจุดที่เริ่มวน)
            if times >= REPEAT_TIMES and times * size >= len(squashed) * REPEAT_COVER:
                return True
    return False


def drop_hallucinations(segments: list[Segment]) -> list[Segment]:
    return [s for s in segments if not looks_hallucinated(s.text)]
