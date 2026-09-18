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


def _get_int(name: str, default: int, minimum: int = 1) -> int:
    """อ่านค่า int จาก env — ค่าที่ว่าง/ไม่ใช่ตัวเลข/ต่ำกว่า minimum ให้ใช้ default."""
    try:
        value = int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


class Config:
    # LLM (สรุป)
    llm_base_url: str = _get("LLM_BASE_URL", "https://consoletoken.aunjai.org/api/v1").rstrip("/")
    llm_api_key: str = _get("LLM_API_KEY", "")
    llm_model: str = _get("LLM_MODEL", "gemma-4-12b")
    # เพดาน token ของคำตอบ สรุปประชุมยาวๆ ชน 4000 แล้วถูกตัดกลางคัน (BACKLOG #7)
    # ถ้าโดนตัด summarizer จะขยายเพดานเป็นเท่าตัวแล้วลองใหม่ จนถึง llm_max_tokens_ceiling
    llm_max_tokens: int = _get_int("LLM_MAX_TOKENS", 4000, minimum=256)
    llm_max_tokens_ceiling: int = _get_int("LLM_MAX_TOKENS_CEILING", 16000, minimum=256)
    # บทถอดเสียงยาวเกินนี้ (ตัวอักษร) จะถูกแบ่งเป็นก้อนแล้วสรุปแบบ map-reduce — ดู
    # docs/adr/ADR-001-transcript-chunking.md ว่าทำไมนับเป็น "ตัวอักษร" ไม่ใช่ "โทเคน"
    # 24,000 ตัวไทย ~ 12,000-24,000 โทเคน + เทมเพลต + งบ output ยังอยู่ในกรอบโมเดล 32k
    # โมเดล context ใหญ่ตั้งให้สูงขึ้นได้ = ถูกลงและสรุปดีขึ้น
    llm_chunk_chars: int = _get_int("LLM_CHUNK_CHARS", 24000, minimum=2000)

    # ตัวถอดเสียงที่ใช้เป็นค่าเริ่มต้น: local (whisper.cpp) หรือ api (OpenAI-compatible)
    stt_provider: str = _get("STT_PROVIDER", "local").strip().lower()
    # มีคนตั้ง STT_PROVIDER ไว้เองจริง ๆ หรือได้ "local" มาเพราะค่าเริ่มต้นข้างบนเฉย ๆ
    # BUG-019: stt.resolve() ต้องแยกสองเคสนี้ให้ออก — "ขอ local เอง" แล้วไม่มี whisper ต้องหยุด
    # ไม่ใช่เงียบ ๆ อัปโหลดเสียงประชุมไป API ส่วน "ไม่มีใครตั้งอะไรเลย" (เช่นบน Vercel) ยังไป API ได้
    stt_provider_set: bool = bool(_get("STT_PROVIDER", "").strip())
    stt_model: str = _get("STT_MODEL", "whisper-1")
    # ว่างไว้ = ใช้ค่าเดียวกับ LLM (endpoint แบบ LiteLLM มักให้ทั้งสองอย่างด้วยคีย์เดียว)
    stt_base_url_raw: str = _get("STT_BASE_URL", "")
    stt_api_key_raw: str = _get("STT_API_KEY", "")

    # Whisper (ถอดเสียง)
    whisper_bin: str = _get("WHISPER_BIN", "whisper-cli")
    whisper_model: str = _get("WHISPER_MODEL", "models/ggml-large-v3-turbo-q5_0.bin")
    whisper_lang: str = _get("WHISPER_LANG", "th")
    whisper_threads: str = _get("WHISPER_THREADS", "8")
    # โมเดล VAD — ข้ามช่วงเงียบก่อนป้อนให้ whisper กันอาการหลอนคำซ้ำบนความเงียบ
    # ว่าง/ไม่มีไฟล์ = ไม่ใช้ VAD (ยังถอดได้ แต่แทร็กที่เงียบจะได้ข้อความขยะ)
    vad_model: str = _get("VAD_MODEL", "models/ggml-silero-v5.1.2.bin")

    # เก็บภาพหน้าจอบอทและโฟลเดอร์พักที่กำพร้าไว้กี่วัน — 0 = เก็บตลอดไป (BACKLOG #25)
    # ของพวกนี้เป็นข้อมูลของการประชุมจริง (หน้าจอห้อง ชื่อผู้เข้าร่วม เสียงที่อัดค้างไว้)
    # ไม่ใช่แค่เรื่องพื้นที่ดิสก์ การเก็บไว้ตลอดกาลจึงเป็นการตัดสินใจ ไม่ใช่ค่าเริ่มต้นที่ควรเป็น
    bot_retention_days: int = _get_int("BOT_RETENTION_DAYS", 30, minimum=0)

    # เปิด sandbox ของ Chromium ในคอนเทนเนอร์บอท (BACKLOG #21b)
    #
    # ค่าเริ่มต้นยังเป็นปิด (= ส่ง --no-sandbox เหมือนเดิม) **โดยตั้งใจ** เพราะการเปิดโดยที่
    # host ไม่มีของคู่กัน ทำให้ Chromium ไม่เปิดเลย = บอทไม่ได้เข้าห้อง = ประชุมหาย
    # ซึ่งแย่กว่าความเสี่ยงที่เรากำลังลด และยืนยันได้ทางเดียวคือส่งบอทเข้าห้องจริง
    #
    # เปิดได้สองทาง เลือกอย่างใดอย่างหนึ่ง:
    #   MAI_BOT_SANDBOX=1                    -> docker run --cap-add=SYS_ADMIN
    #   MAI_BOT_SECCOMP=/path/chrome.json    -> docker run --security-opt seccomp=...
    # ทาง seccomp แคบกว่าจึงดีกว่า ถ้าตั้งมาจะใช้ทางนั้นแทน SYS_ADMIN
    bot_sandbox: bool = _get("MAI_BOT_SANDBOX", "0").lower() in ("1", "true", "yes", "on")
    bot_seccomp: str = _get("MAI_BOT_SECCOMP", "").strip()

    # Recording
    ffmpeg_bin: str = _get("FFMPEG_BIN", "ffmpeg")
    # ว่าง = เดาเอาจาก ffmpeg_bin (ดู ffprobe_bin()) ตั้งเองได้ถ้าสองตัวไม่ได้อยู่ด้วยกัน
    ffprobe_bin_raw: str = _get("FFPROBE_BIN", "")
    mic_device: str = _get("MIC_DEVICE", "0")
    system_device: str = _get("SYSTEM_DEVICE", "1")

    # Worker แยกเครื่อง (โหมด cloud) — เว็บทำหน้าที่แค่คุมคิว งานหนักไปอยู่เครื่องที่มี GPU
    # REMOTE_WORKER=1 = ไม่ต้องประมวลผลในโพรเซสเดียวกับเว็บ รอ worker มารับงานเอง
    remote_worker: bool = _get("REMOTE_WORKER", "0").lower() in ("1", "true", "yes", "on")
    worker_token: str = _get("WORKER_TOKEN", "")

    # อยู่หลัง reverse proxy (nginx/Cloudflare) หรือเปล่า — มีผลกับการหา IP ผู้เรียกที่ใช้
    # เป็นคีย์จำกัดอัตราคำขอ ถ้าเปิดทั้งที่ไม่มี proxy จริง ใครก็ปลอม X-Forwarded-For
    # เพื่อเลี่ยง rate limit ได้ จึงต้องเปิดเอง (บน Vercel api/index.py เปิดให้แล้ว)
    trust_proxy: bool = _get("TRUST_PROXY", "0").lower() in ("1", "true", "yes", "on")

    root: Path = ROOT

    @classmethod
    def whisper_model_path(cls) -> Path:
        p = Path(cls.whisper_model)
        return p if p.is_absolute() else cls.root / p

    @classmethod
    def vad_model_path(cls) -> Path | None:
        if not cls.vad_model:
            return None
        p = Path(cls.vad_model)
        p = p if p.is_absolute() else cls.root / p
        return p if p.exists() else None

    @classmethod
    def stt_base_url(cls) -> str:
        return (cls.stt_base_url_raw or cls.llm_base_url).rstrip("/")

    @classmethod
    def ffprobe_bin(cls) -> str:
        """ffprobe ที่ "คู่กับ" ffmpeg ที่ตั้งไว้ ไม่ใช่ชื่อ ffprobe ลอย ๆ บน PATH.

        สองตัวนี้มาด้วยกันเสมอในแพ็กเกจเดียว คนที่ตั้ง FFMPEG_BIN เป็นพาธเต็ม (เครื่อง Windows
        ที่ไม่ได้ใส่ ffmpeg ลง PATH เป็นเคสปกติ) จึงมี ffprobe อยู่ในโฟลเดอร์เดียวกันแน่ ๆ แต่
        ไม่มีบน PATH — เรียก "ffprobe" ตรง ๆ แล้วไม่เจอ audio_duration จะคืน 0.0 **เงียบ ๆ**
        ความยาวประชุมขึ้นเป็น 0 ทั้งที่ไฟล์ดีทุกอย่าง (BACKLOG #32)
        """
        if cls.ffprobe_bin_raw:
            return cls.ffprobe_bin_raw
        exe = Path(cls.ffmpeg_bin)
        name = exe.name.replace("ffmpeg", "ffprobe", 1) if "ffmpeg" in exe.name else "ffprobe"
        # ชื่อเปล่า ๆ (ค่าเริ่มต้น "ffmpeg") ต้องคืนชื่อเปล่า ๆ ไม่ใช่ "./ffprobe"
        return name if str(exe.parent) == "." else str(exe.with_name(name))

    @classmethod
    def stt_key(cls) -> str:
        key = cls.stt_api_key_raw or cls.llm_api_key
        return "" if "your-key" in key else key


config = Config()

# จังหวะ heartbeat ของ worker กับเพดานที่ฝั่งเซิร์ฟเวอร์ถือว่า "หลุดไปแล้ว" — ต้องอยู่ด้วยกัน
# เพราะค่าหนึ่งกำหนดอีกค่า (หลุดเมื่อพลาดไปราวสามจังหวะ) เดิมเลข 75 เขียนซ้ำอยู่สามที่:
# pgstore (ตัวที่ SQL ใช้จริง), server.py (ตัวที่ตอบกลับไปให้ worker) และคอมเมนต์ใน worker.py
# แก้ที่เดียวไม่ครบ = เซิร์ฟเวอร์ตัดคนที่ยังเต้นอยู่ทิ้ง หรือเก็บคนที่ตายแล้วไว้ (BACKLOG #33)
WORKER_HEARTBEAT_SECONDS = 20.0
WORKER_STALE_SECONDS = 75

# บอทหลายห้องพร้อมกันได้ — ช่วงนั่งในห้องแทบไม่ใช้ CPU (รอเฉย ๆ) ช่วงถอดเสียงถูกบีบให้ทำทีละงาน
# ด้วย runner.HEAVY_LOCK อยู่แล้ว ส่วนงานที่ไม่ใช่บอทยังทำทีละงานเพราะเข้าช่วงหนักทันที
# อยู่ที่นี่เพราะ cli.py ต้องรู้ค่านี้ตอน parse args โดยไม่ import worker (ซึ่งลาก runner มาทั้งชุด)
DEFAULT_MAX_BOTS = 3
