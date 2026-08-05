"""แยกผู้พูด (speaker diarization) ด้วย sherpa-onnx.

ใช้ ONNX runtime ไม่ต้องมี torch — wheel ~2MB โมเดลรวมกัน ~70MB ดาวน์โหลดได้อิสระไม่ต้องมี token

โมดูลนี้เป็น "ทางเลือก": ถ้ายังไม่ได้ติดตั้ง sherpa-onnx หรือไม่มีโมเดล ระบบส่วนอื่นยังทำงานได้
เพียงแต่จะไม่ระบุว่าใครพูด — ดูฟังก์ชัน available()
"""

from __future__ import annotations

import array
import wave
from dataclasses import dataclass
from pathlib import Path

from .config import config

# ชื่อไฟล์โมเดลที่คาดว่าอยู่ในโฟลเดอร์ models/
SEG_DIR = "sherpa-onnx-pyannote-segmentation-3-0"
SEG_FILE = "model.onnx"
EMB_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"

_pipeline = None
_pipeline_key: tuple | None = None


@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: int


def _seg_model_path() -> Path:
    return config.root / "models" / SEG_DIR / SEG_FILE


def _emb_model_path() -> Path:
    return config.root / "models" / EMB_FILE


def missing_pieces() -> list[str]:
    """คืนรายการสิ่งที่ยังขาดเพื่อให้แยกผู้พูดได้ — ว่างเปล่า = พร้อมใช้."""
    missing = []
    try:
        import sherpa_onnx  # noqa: F401
    except ImportError:
        missing.append("แพ็กเกจ sherpa-onnx (pip install sherpa-onnx)")
    if not _seg_model_path().exists():
        missing.append(f"โมเดล segmentation: models/{SEG_DIR}/{SEG_FILE}")
    if not _emb_model_path().exists():
        missing.append(f"โมเดล embedding: models/{EMB_FILE}")
    return missing


def available() -> bool:
    return not missing_pieces()


def _read_wav_mono16k(path: Path) -> tuple[list[float], int]:
    """อ่าน WAV 16-bit mono เป็น float32 -1..1 (ไฟล์ถูกแปลงมาแล้วโดย ffmpeg)."""
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise RuntimeError("diarization ต้องใช้ WAV 16-bit mono")
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    ints = array.array("h")
    ints.frombytes(raw)
    return [s / 32768.0 for s in ints], rate


def _build(num_speakers: int, threshold: float):
    """สร้าง pipeline (แคชไว้ — โหลดโมเดลใหม่ทุกครั้งเสียเวลาเปล่า)."""
    global _pipeline, _pipeline_key
    key = (num_speakers, threshold)
    if _pipeline is not None and _pipeline_key == key:
        return _pipeline

    import sherpa_onnx

    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(_seg_model_path()),
            ),
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(_emb_model_path())),
        clustering=sherpa_onnx.FastClusteringConfig(
            # ไม่รู้จำนวนคนล่วงหน้าก็ให้จัดกลุ่มด้วย threshold แทน
            num_clusters=num_speakers if num_speakers > 0 else -1,
            threshold=threshold,
        ),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    if not cfg.validate():
        raise RuntimeError("คอนฟิก diarization ไม่ถูกต้อง — ตรวจไฟล์โมเดลใน models/")
    _pipeline = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    _pipeline_key = key
    return _pipeline


def diarize(
    wav16k_path: str | Path,
    num_speakers: int = 0,
    threshold: float = 0.5,
) -> list[SpeakerTurn]:
    """คืนช่วงเวลาว่าใครพูดเมื่อไหร่ จากไฟล์ WAV 16kHz mono.

    num_speakers: ใส่ถ้ารู้จำนวนคนแน่ๆ (แม่นกว่า) — 0 = ให้ระบบเดาเอง
    """
    gaps = missing_pieces()
    if gaps:
        raise RuntimeError("แยกผู้พูดไม่ได้ ยังขาด: " + "; ".join(gaps))

    path = Path(wav16k_path)
    samples, rate = _read_wav_mono16k(path)
    sd = _build(num_speakers, threshold)
    if rate != sd.sample_rate:
        raise RuntimeError(f"โมเดลต้องการ {sd.sample_rate} Hz แต่ไฟล์เป็น {rate} Hz")

    result = sd.process(samples).sort_by_start_time()
    return [SpeakerTurn(start=s.start, end=s.end, speaker=s.speaker) for s in result]


def label_segments(
    segments: list[dict],
    turns: list[SpeakerTurn],
    namer=None,
) -> list[dict]:
    """ใส่ชื่อผู้พูดให้ segment ของ whisper โดยจับคู่จากช่วงเวลาที่ทับกันมากที่สุด."""
    if not turns:
        return segments

    def name_of(idx: int) -> str:
        return namer(idx) if namer else f"ผู้พูด {idx + 1}"

    for seg in segments:
        s_start, s_end = seg.get("start", 0.0), seg.get("end", 0.0)
        best_overlap, best_speaker = 0.0, None
        for turn in turns:
            overlap = min(s_end, turn.end) - max(s_start, turn.start)
            if overlap > best_overlap:
                best_overlap, best_speaker = overlap, turn.speaker
        if best_speaker is None:
            # ไม่ทับกับใครเลย (เช่น whisper จับเสียงที่ diarizer มองว่าเงียบ) — ยึดคนที่ใกล้สุด
            nearest = min(turns, key=lambda t: min(abs(t.start - s_start), abs(t.end - s_end)))
            best_speaker = nearest.speaker
        seg["speaker"] = name_of(best_speaker)
    return segments
