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


# อ่านทีละก้อนแทนการดูดทั้งไฟล์เข้ามาเป็น bytes ก้อนเดียวก่อนแปลง
READ_FRAMES = 1 << 20      # ~1M sample = 2 MB ต่อรอบ


def _read_wav_mono16k(path: Path):
    """อ่าน WAV 16-bit mono เป็นบัฟเฟอร์ float32 -1..1 (ไฟล์ถูกแปลงมาแล้วโดย ffmpeg).

    **ห้ามคืนเป็น list ของ float** ซึ่งเป็นของเดิม: float ของ Python เป็นออบเจกต์ 24 ไบต์
    บวกพอยน์เตอร์ในลิสต์อีก 8 วัดจริงได้ 32.8 ไบต์ต่อ sample = **3.78 GB สำหรับประชุม
    2 ชั่วโมง** ที่ 16 kHz (BACKLOG #31) บัฟเฟอร์ float32 ใช้ 4 ไบต์ต่อ sample = 0.46 GB
    และ sherpa-onnx รับ numpy float32 อยู่แล้วตามตัวอย่างของมันเอง จึงไม่ต้องแปลงอะไรต่อ

    numpy เป็น dependency ของ sherpa-onnx อยู่แล้ว (ทางที่เรียกฟังก์ชันนี้จริงต้องมีทั้งคู่)
    แต่ยังเผื่อทาง array.array ไว้ ให้เทสต์/เครื่องที่ไม่มี numpy ยังอ่านไฟล์ได้
    """
    with wave.open(str(path), "rb") as wf:
        if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
            raise RuntimeError("diarization ต้องใช้ WAV 16-bit mono")
        rate = wf.getframerate()
        frames = wf.getnframes()
        try:
            import numpy as np
        except ImportError:
            np = None

        if np is not None:
            out = np.empty(frames, dtype=np.float32)
            pos = 0
            while pos < frames:
                chunk = wf.readframes(min(READ_FRAMES, frames - pos))
                if not chunk:
                    break
                # "<i2" ระบุ little-endian ตรงตามสเปก WAV ไม่ใช่ฝากไว้กับ byte order ของเครื่อง
                block = np.frombuffer(chunk, dtype="<i2")
                out[pos:pos + block.size] = block
                pos += block.size
            out = out[:pos]
            out /= 32768.0
            return out, rate

        out = array.array("f")
        while True:
            chunk = wf.readframes(READ_FRAMES)
            if not chunk:
                break
            ints = array.array("h")
            ints.frombytes(chunk)
            out.extend(s / 32768.0 for s in ints)
        return out, rate


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

    # เลข cluster ที่โมเดลให้มาอาจไม่เริ่มที่ 0 หรือมีช่องว่าง (เช่นได้ 1 คนแต่เลขเป็น 1)
    # เรียงใหม่ให้ต่อเนื่องตามลำดับที่ปรากฏก่อน จะได้ "ผู้พูด 1, 2, 3" ไม่ข้ามเลข
    order: dict[int, int] = {}
    for turn in sorted(turns, key=lambda t: t.start):
        if turn.speaker not in order:
            order[turn.speaker] = len(order)

    def name_of(raw: int) -> str:
        idx = order.get(raw, raw)
        return namer(idx) if namer else f"ผู้พูด {idx + 1}"

    # เดินสองรายการที่เรียงตามเวลาพร้อมกัน แทนการวน turns ทั้งหมดต่อหนึ่ง segment:
    # ประชุม 2 ชั่วโมงมีราว 2,900 segment และ turn ได้เป็นพัน = สิบล้านรอบใน Python (BACKLOG #31)
    #
    # เงื่อนไขที่ทำให้ตัดได้: turns เรียงตาม start แล้ว ดังนั้นเมื่อเจอ turn ที่ start >= จบ segment
    # ตัวถัด ๆ ไปก็ยิ่งเริ่มช้ากว่า ไม่มีทางทับ — หยุดได้เลย
    # ส่วนหัวรายการเลื่อนได้เฉพาะตอนที่ turn จบก่อน segment จะเริ่ม **และ** ยังไม่เจอตัวที่ยังเปิดอยู่
    # (turn ซ้อนกันได้เมื่อสองคนพูดพร้อมกัน ตัวที่จบเร็วอาจมาหลังตัวที่จบช้า) เลื่อนเกินนั้น = ข้ามของจริง
    # เก็บลำดับเดิมไว้ด้วย เพราะมันเป็นตัวตัดสินตอนคะแนนเท่ากัน — และเท่ากันบ่อยมาก:
    # segment ที่อยู่ในหลาย turn พร้อมกัน (คนพูดทับกัน) ทุก turn จะทับเท่ากับความยาว segment เป๊ะ ๆ
    # ของเดิมวน turns ตามลำดับที่รับมาแล้วแทนที่เฉพาะตอน "มากกว่า" ผู้ชนะจึงเป็นตัวแรกในลำดับนั้น
    # ถ้าเรียงใหม่แล้วไม่คุมจุดนี้ ผลจะเปลี่ยนไปเงียบ ๆ (วัดจริง: ต่างกัน 84 ใน 300 ชุดสุ่ม)
    ordered = sorted(enumerate(turns), key=lambda it: it[1].start)
    seen = sorted(segments, key=lambda sg: sg.get("start", 0.0))
    lo = 0
    for seg in seen:
        s_start, s_end = seg.get("start", 0.0), seg.get("end", 0.0)
        while lo < len(ordered) and ordered[lo][1].end <= s_start:
            lo += 1
        best_overlap, best_speaker, best_idx = 0.0, None, len(turns)
        for idx, turn in ordered[lo:]:
            if turn.start >= s_end:
                break
            overlap = min(s_end, turn.end) - max(s_start, turn.start)
            if overlap > best_overlap or (overlap == best_overlap and overlap > 0
                                          and idx < best_idx):
                best_overlap, best_speaker, best_idx = overlap, turn.speaker, idx
        if best_speaker is None:
            # ไม่ทับกับใครเลย (เช่น whisper จับเสียงที่ diarizer มองว่าเงียบ) — ยึดคนที่ใกล้สุด
            # ตรงนี้ยังสแกนทั้งรายการโดยตั้งใจ: ระยะที่ใช้วัดดูปลายทั้งสองข้าง จึงไม่ได้เพิ่มขึ้น
            # ตาม start การตัดด้วยหน้าต่างรอบ ๆ จะเปลี่ยน "คนที่ใกล้สุด" ในบางเคสโดยไม่มีใครรู้
            # และสาขานี้เกิดเฉพาะ segment ที่ไม่ทับกับใครเลย ซึ่งพบน้อย — ไม่คุ้มเสี่ยงผลเปลี่ยน
            # วนจาก turns ตามลำดับเดิม ไม่ใช่ ordered — min() คืนตัวแรกที่น้อยสุด ลำดับจึงมีผล
            nearest = min(turns, key=lambda t: min(abs(t.start - s_start), abs(t.end - s_end)))
            best_speaker = nearest.speaker
        seg["speaker"] = name_of(best_speaker)
    return segments
