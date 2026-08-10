"""โหลดค่าคอนฟิกจากไฟล์ .env (ไม่พึ่ง dependency ภายนอก)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """อ่าน .env แบบง่ายๆ ใส่ค่าเข้า os.environ ถ้ายังไม่ถูกตั้งไว้."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(ROOT / ".env")


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


class Config:
    # LLM (สรุป)
    llm_base_url: str = _get("LLM_BASE_URL", "https://consoletoken.aunjai.org/api/v1").rstrip("/")
    llm_api_key: str = _get("LLM_API_KEY", "")
    llm_model: str = _get("LLM_MODEL", "gemma-4-12b")

    # ตัวถอดเสียงที่ใช้เป็นค่าเริ่มต้น: local (whisper.cpp) หรือ api (OpenAI-compatible)
    stt_provider: str = _get("STT_PROVIDER", "local").strip().lower()
    stt_model: str = _get("STT_MODEL", "whisper-1")
    # ว่างไว้ = ใช้ค่าเดียวกับ LLM (endpoint แบบ LiteLLM มักให้ทั้งสองอย่างด้วยคีย์เดียว)
    stt_base_url_raw: str = _get("STT_BASE_URL", "")
    stt_api_key_raw: str = _get("STT_API_KEY", "")

    # Whisper (ถอดเสียง)
    whisper_bin: str = _get("WHISPER_BIN", "whisper-cli")
    whisper_model: str = _get("WHISPER_MODEL", "models/ggml-large-v3-turbo-q5_0.bin")
    whisper_lang: str = _get("WHISPER_LANG", "th")
    whisper_threads: str = _get("WHISPER_THREADS", "8")

    # Recording
    ffmpeg_bin: str = _get("FFMPEG_BIN", "ffmpeg")
    mic_device: str = _get("MIC_DEVICE", "0")
    system_device: str = _get("SYSTEM_DEVICE", "1")

    # Worker แยกเครื่อง (โหมด cloud) — เว็บทำหน้าที่แค่คุมคิว งานหนักไปอยู่เครื่องที่มี GPU
    # REMOTE_WORKER=1 = ไม่ต้องประมวลผลในโพรเซสเดียวกับเว็บ รอ worker มารับงานเอง
    remote_worker: bool = _get("REMOTE_WORKER", "0").lower() in ("1", "true", "yes", "on")
    worker_token: str = _get("WORKER_TOKEN", "")

    root: Path = ROOT

    @classmethod
    def whisper_model_path(cls) -> Path:
        p = Path(cls.whisper_model)
        return p if p.is_absolute() else cls.root / p

    @classmethod
    def stt_base_url(cls) -> str:
        return (cls.stt_base_url_raw or cls.llm_base_url).rstrip("/")

    @classmethod
    def stt_key(cls) -> str:
        key = cls.stt_api_key_raw or cls.llm_api_key
        return "" if "your-key" in key else key


config = Config()
