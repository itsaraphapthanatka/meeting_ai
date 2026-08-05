"""ตัวประมวลผลงานหนึ่งงาน — ใช้ร่วมกันทั้งโหมดในเครื่องและโหมด worker แยกเครื่อง.

แยกออกมาจาก web/jobs.py เพื่อให้ logic การถอดเสียง/แยกผู้พูด/สรุป มีที่เดียว
ไม่ว่าจะรันในโพรเซสเดียวกับเว็บ (mai web) หรือรันบนเครื่องที่มี GPU แล้วคุยกับ cloud (mai worker)

ฝั่ง cloud สร้าง "spec" (งานที่ต้องทำ + ที่อยู่ไฟล์เสียง) → ฝั่งที่มี GPU รัน → ส่ง "result" กลับ
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Callable

from . import diarize, summarizer, transcriber
from .config import config

# น้ำหนักของแต่ละขั้นในแถบ progress รวม — ถอดเสียงกินเวลาเป็นส่วนใหญ่
TRANSCRIBE_START = 0.05
TRANSCRIBE_END = 0.70
DIARIZE_END = 0.80
SUMMARY_END = 0.97

# ชื่อผู้พูดสำหรับโหมดอัด 2 แทร็ก — รู้จากแหล่งเสียงเลยว่าใครเป็นใคร ไม่ต้องเดา
SELF_LABEL = "ฉัน"
OTHERS_LABEL = "ผู้ร่วมประชุม"

# ลำดับที่ประมวลผลแทร็ก (system ก่อน mic เพื่อให้ผลลัพธ์เรียงเหมือนกันทุกครั้ง)
TRACK_ORDER = ("system", "mic", "mixed")

ProgressFn = Callable[[str, float], None]
FetchFn = Callable[[str], Path]


def audio_duration(path: Path) -> float:
    """ความยาวไฟล์เสียงเป็นวินาที — 0.0 ถ้าอ่านไม่ได้."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if proc.returncode == 0:
            return float(json.loads(proc.stdout)["format"]["duration"])
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return 0.0


def mix_tracks(paths: list[Path], dest: Path) -> Path:
    """ผสมหลายแทร็กเป็นไฟล์เดียวไว้ให้ฟังย้อนหลัง (แทร็กแยกยังเก็บไว้สำหรับถอดเสียง)."""
    cmd = [config.ffmpeg_bin, "-y", "-loglevel", "error"]
    for p in paths:
        cmd += ["-i", str(p)]
    if len(paths) > 1:
        cmd += ["-filter_complex", f"amix=inputs={len(paths)}:duration=longest:normalize=0"]
    cmd += ["-ac", "1", "-c:a", "libopus", "-b:a", "32k", str(dest)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ผสมแทร็กไม่สำเร็จ:\n{proc.stderr[-800:]}")
    return dest


def _transcribe_track(
    path: Path, language: str | None, base: float, span: float, label: str, progress: ProgressFn,
) -> tuple[list[dict], str]:
    def on_progress(frac: float) -> None:
        progress(f"ถอดเสียง{label} {int(frac * 100)}%",
                 base + span * max(0.0, min(1.0, frac)))

    progress(f"ถอดเสียง{label}", base)
    result = transcriber.transcribe(path, language=language, on_progress=on_progress)
    segments = [
        {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
        for s in result.segments
        if s.text.strip()
    ]
    return segments, result.language


def _diarize_into(source: Path, segments: list[dict], num_speakers: int, namer) -> None:
    """แยกผู้พูดจากไฟล์เสียงหนึ่งไฟล์แล้วใส่ชื่อลง segments (แก้ในที่)."""
    with tempfile.TemporaryDirectory() as tmp:
        wav = transcriber.to_wav16k(source, Path(tmp) / "d16k.wav")
        turns = diarize.diarize(wav, num_speakers=num_speakers)
    diarize.label_segments(segments, turns, namer=namer)


def transcribe_job(spec: dict, fetch: FetchFn, progress: ProgressFn, mix_dir: Path) -> dict:
    """ถอดเสียง + แยกผู้พูด + สรุป ตาม spec. คืน result dict ที่ฝั่ง cloud เอาไปบันทึกได้.

    spec:   {title, language, template, diarize, num_speakers, tracks: [ชื่อแทร็ก...]}
    fetch:  ชื่อแทร็ก -> path ของไฟล์บนดิสก์ (ดาวน์โหลดมาก่อนถ้าอยู่ไกล)
    mix_dir: โฟลเดอร์ที่จะเขียนไฟล์เสียงผสมสำหรับฟังย้อนหลัง
    """
    title = spec["title"]
    language = spec.get("language") or None
    want_diarize = bool(spec.get("diarize")) and diarize.available()
    num_speakers = int(spec.get("num_speakers") or 0)
    names = [n for n in TRACK_ORDER if n in spec["tracks"]]
    if not names:
        raise RuntimeError("ไม่มีแทร็กเสียงใน spec")

    progress("เตรียมไฟล์เสียง", 0.02)
    paths = {name: fetch(name) for name in names}

    segments: list[dict] = []
    detected = language or config.whisper_lang
    dual = "mixed" not in paths
    span_each = (TRANSCRIBE_END - TRANSCRIBE_START) / len(names)
    warning = None

    for i, name in enumerate(names):
        label = {"mic": " (ไมค์คุณ)", "system": " (ผู้ร่วมประชุม)"}.get(name, "")
        segs, lang = _transcribe_track(
            paths[name], language,
            base=TRANSCRIBE_START + span_each * i, span=span_each,
            label=label, progress=progress,
        )
        detected = lang or detected

        if name == "mic":
            for s in segs:
                s["speaker"] = SELF_LABEL
        elif name == "system":
            # ฝั่งผู้ร่วมประชุมอาจมีหลายคน — แยกต่อได้ถ้าเปิด diarization
            if want_diarize and segs:
                progress("แยกผู้พูดฝั่งผู้ร่วมประชุม", TRANSCRIBE_END)
                try:
                    _diarize_into(paths[name], segs, num_speakers,
                                  lambda i: f"{OTHERS_LABEL} {i + 1}")
                except Exception:
                    traceback.print_exc()
                    for s in segs:
                        s["speaker"] = OTHERS_LABEL
            else:
                for s in segs:
                    s["speaker"] = OTHERS_LABEL
        segments.extend(segs)

    if dual:
        segments.sort(key=lambda s: s["start"])

    # แทร็กเดียว: ไม่รู้จากแหล่งเสียงว่าใครเป็นใคร ต้องพึ่ง diarization ล้วน
    if not dual and want_diarize and segments:
        progress("แยกผู้พูด", TRANSCRIBE_END)
        try:
            _diarize_into(paths["mixed"], segments, num_speakers, None)
        except Exception as e:
            traceback.print_exc()
            warning = f"แยกผู้พูดไม่สำเร็จ: {e} — ส่วนอื่นยังทำงานปกติ"

    if not segments:
        raise RuntimeError("ถอดเสียงไม่ได้ข้อความเลย — ไฟล์อาจไม่มีเสียงพูด หรือเงียบทั้งไฟล์")

    progress("รวมไฟล์เสียง", DIARIZE_END)
    playback: Path | None = None
    try:
        playback = mix_tracks([paths[n] for n in names], mix_dir / f"{spec['id']}.ogg")
    except Exception:
        traceback.print_exc()

    duration = audio_duration(playback) if playback else 0.0
    if not duration:
        duration = audio_duration(paths[names[0]]) or (segments[-1]["end"] if segments else 0.0)

    speakers = sorted({s["speaker"] for s in segments if s.get("speaker")})

    # สรุปพลาดแล้วโยนบทถอดเสียงทิ้ง = เสียเวลา GPU ไปฟรีๆ ทั้งไฟล์
    # ส่งกลับพร้อม summary_error แล้วให้กด "สรุปใหม่" ทีหลังได้
    progress("สรุปด้วย AI", DIARIZE_END)
    summary, summary_error = "", None
    try:
        summary = summarizer.summarize(
            transcript_for_llm(segments),
            meeting_title=title,
            template=spec.get("template") or summarizer.DEFAULT_TEMPLATE,
            has_speakers=bool(speakers),
        )
    except Exception as e:
        summary_error = str(e)

    progress("บันทึก", SUMMARY_END)
    return {
        "segments": segments,
        "language": detected,
        "duration": duration,
        "speakers": speakers,
        "summary": summary,
        "summary_error": summary_error,
        "warning": warning,
        "playback": str(playback) if playback else None,
    }


def transcript_for_llm(segments: list[dict]) -> str:
    """ข้อความสำหรับส่งให้ LLM — ใส่ชื่อผู้พูดกำกับถ้ามี เพื่อให้ระบุผู้รับผิดชอบได้."""
    lines = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        speaker = s.get("speaker")
        lines.append(f"{speaker}: {text}" if speaker else text)
    return "\n".join(lines)


def summarize_job(spec: dict, progress: ProgressFn) -> dict:
    """สรุปใหม่จากบทถอดเสียงที่มีอยู่แล้ว (ไม่ต้องถอดเสียงซ้ำ)."""
    segments = spec.get("segments") or []
    text = transcript_for_llm(segments)
    if not text:
        raise RuntimeError("ไม่มีบทถอดเสียงให้สรุป")
    progress("สรุปด้วย AI", 0.4)
    summary = summarizer.summarize(
        text,
        meeting_title=spec.get("title"),
        template=spec.get("template") or summarizer.DEFAULT_TEMPLATE,
        has_speakers=any(s.get("speaker") for s in segments),
    )
    return {"summary": summary, "summary_error": None}


def translate_job(spec: dict, progress: ProgressFn) -> dict:
    lang = spec["lang"]
    text = spec.get("summary") or ""
    if not text.strip():
        raise RuntimeError("ยังไม่มีสรุปให้แปล — สรุปก่อนแล้วค่อยแปล")
    progress(f"แปลเป็น {lang}", 0.4)
    return {"lang": lang, "text": summarizer.translate(text, lang)}


HANDLERS = {
    "process": None,      # ต้องใช้ fetch/mix_dir จึงเรียก transcribe_job ตรงๆ
    "summarize": summarize_job,
    "translate": translate_job,
}
