"""สรุปการประชุมด้วย LLM ผ่าน endpoint แบบ OpenAI-compatible (stdlib ล้วน)."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request

from .config import config

# โค้ด HTTP ที่ถือว่าชั่วคราว ลองใหม่ได้ (524 = Cloudflare timeout ฝั่ง origin LLM)
_RETRY_CODES = {429, 500, 502, 503, 504, 520, 522, 524}

SYSTEM_PROMPT = """คุณคือผู้ช่วยจดและสรุปการประชุมมืออาชีพ
สรุปเป็นภาษาไทยที่กระชับ อ่านง่าย ตรงประเด็น อ้างอิงเฉพาะสิ่งที่ปรากฏใน transcript เท่านั้น
ห้ามแต่งเติมข้อมูลที่ไม่มีในบทสนทนา ถ้าข้อมูลส่วนใดไม่มีให้ระบุว่า "ไม่ได้ระบุ"
"""

_SPEAKER_NOTE = """
บทถอดเสียงนี้ระบุชื่อผู้พูดไว้หน้าแต่ละประโยคแล้ว ให้ใช้ข้อมูลนั้นระบุว่าใครรับผิดชอบงานใด
และใครเสนอความเห็นอะไร ห้ามเดาชื่อคนที่ไม่ปรากฏใน transcript
"""

# ---- เทมเพลตสรุป: โครงหัวข้อต่างกันตามชนิดการประชุม ----

_GENERAL_BODY = """## 📌 สรุปย่อ (TL;DR)
(2-4 บรรทัด ภาพรวมว่าประชุมเรื่องอะไร ได้ข้อสรุปหลักอะไร)

## 🗣️ ประเด็นที่พูดคุย
- (bullet ประเด็นสำคัญแต่ละเรื่อง)

## ✅ ข้อสรุป / มติที่ตกลงกัน
- (สิ่งที่ตัดสินใจหรือตกลงกันได้)

## 📋 สิ่งที่ต้องทำต่อ (Action Items)
| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |
|---|---|---|
| ... | ... | ... |
(ถ้าไม่ระบุผู้รับผิดชอบ/กำหนด ให้ใส่ "ไม่ได้ระบุ")

## ❓ ประเด็นค้าง / ต้องติดตาม
- (คำถามที่ยังไม่มีคำตอบ หรือเรื่องที่ต้องคุยต่อ ถ้าไม่มีให้ใส่ "- ไม่มี")"""

_ONEONONE_BODY = """## 📌 สรุปย่อ (TL;DR)
(2-4 บรรทัด ภาพรวมของการคุย 1:1 ครั้งนี้)

## 🌤️ สถานะและความรู้สึกของอีกฝ่าย
- (เรื่องที่เล่าว่าไปได้ดี / กำลังติดขัด / กังวล)

## 💬 Feedback ที่ให้และได้รับ
- (feedback สองทาง ระบุว่าใครให้ใคร)

## 🎯 เป้าหมายและการเติบโต
- (สิ่งที่อยากพัฒนา เส้นทางอาชีพ เป้าหมายถัดไป)

## 🚧 อุปสรรคที่ต้องการให้ช่วย
- (สิ่งที่ต้องการการสนับสนุน ถ้าไม่มีให้ใส่ "- ไม่มี")

## 📋 สิ่งที่ต้องทำต่อ (Action Items)
| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |
|---|---|---|
| ... | ... | ... |"""

_SALES_BODY = """## 📌 สรุปย่อ (TL;DR)
(2-4 บรรทัด คุยกับใคร เรื่องอะไร จบที่ตรงไหน)

## 🏢 ข้อมูลลูกค้า
- (บริษัท ตำแหน่งผู้คุย ขนาดทีม เครื่องมือที่ใช้อยู่ — เท่าที่ปรากฏ)

## 🔥 ปัญหา / ความต้องการที่ลูกค้าบอก
- (pain point ตามคำพูดของลูกค้า)

## ❗ ข้อโต้แย้ง / ข้อกังวล
- (เรื่องราคา เวลา ความเสี่ยง คู่แข่ง ถ้าไม่มีให้ใส่ "- ไม่มี")

## 💰 งบและกระบวนการตัดสินใจ
- (งบประมาณ ผู้มีอำนาจตัดสินใจ ไทม์ไลน์ — ไม่ได้พูดถึงให้ใส่ "ไม่ได้ระบุ")

## 🤝 ขั้นถัดไปที่ตกลงกัน
| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |
|---|---|---|
| ... | ... | ... |"""

_INTERVIEW_BODY = """## 📌 สรุปย่อ (TL;DR)
(2-4 บรรทัด ผู้สมัครคือใคร สมัครตำแหน่งอะไร ภาพรวมการสัมภาษณ์)

## 👤 ประสบการณ์และผลงาน
- (งานที่ผ่านมา ผลงานที่เล่า ตัวเลขที่อ้าง)

## 🛠️ ทักษะที่ประเมินได้จากบทสนทนา
- (ทักษะที่แสดงออกจริงในการคุย พร้อมหลักฐานจากคำตอบ)

## ✅ จุดแข็ง
- (ตามที่ปรากฏใน transcript)

## ⚠️ จุดที่ต้องตรวจเพิ่ม
- (คำตอบที่คลุมเครือ หรือช่องว่างที่ควรถามรอบต่อไป)

## 💼 เงื่อนไขที่ผู้สมัครบอก
- (เงินเดือนที่คาดหวัง วันเริ่มงาน รูปแบบการทำงาน — ไม่ได้พูดถึงให้ใส่ "ไม่ได้ระบุ")

## 📋 ขั้นถัดไป
| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |
|---|---|---|
| ... | ... | ... |"""

_STANDUP_BODY = """## 📌 สรุปย่อ (TL;DR)
(2-3 บรรทัด ภาพรวมความคืบหน้าของทีมวันนี้)

## 👥 ความคืบหน้ารายคน
(ตารางนี้ทำเฉพาะคนที่พูดใน transcript)
| คน | ทำอะไรไปแล้ว | จะทำอะไรต่อ | ติดอะไร |
|---|---|---|---|
| ... | ... | ... | ... |

## 🚧 สิ่งที่ติดขัด (Blockers)
- (เรื่องที่ทำให้งานเดินต่อไม่ได้ พร้อมคนที่ต้องช่วย ถ้าไม่มีให้ใส่ "- ไม่มี")

## 📋 สิ่งที่ต้องทำต่อ (Action Items)
| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |
|---|---|---|
| ... | ... | ... |"""

TEMPLATES: dict[str, dict[str, str]] = {
    "general": {"label": "ประชุมทั่วไป", "body": _GENERAL_BODY},
    "oneonone": {"label": "คุย 1:1 / feedback", "body": _ONEONONE_BODY},
    "sales": {"label": "คุยกับลูกค้า / sales call", "body": _SALES_BODY},
    "interview": {"label": "สัมภาษณ์งาน", "body": _INTERVIEW_BODY},
    "standup": {"label": "Daily standup", "body": _STANDUP_BODY},
}
DEFAULT_TEMPLATE = "general"

USER_TEMPLATE = """ต่อไปนี้คือ transcript ของการประชุม{meta}
{speaker_note}
จงสรุปโดยใช้รูปแบบ Markdown หัวข้อภาษาไทยตามนี้เป๊ะๆ:

{body}

--- TRANSCRIPT ---
{transcript}
--- จบ TRANSCRIPT ---
"""

TRANSLATE_PROMPT = """แปลเอกสารสรุปการประชุมด้านล่างเป็น{language}

กติกา:
- คงโครงสร้าง Markdown เดิมไว้ทั้งหมด (หัวข้อ ##, bullet, ตาราง) ห้ามเพิ่มหรือลดหัวข้อ
- ชื่อคน ชื่อบริษัท และศัพท์เทคนิคที่แปลแล้วเสียความหมาย ให้คงไว้ตามเดิม
- ตอบกลับมาเฉพาะเอกสารที่แปลแล้ว ไม่ต้องมีคำอธิบายนำ

--- เอกสาร ---
{text}
"""

LANGUAGE_NAMES = {
    "th": "ภาษาไทย",
    "en": "ภาษาอังกฤษ (English)",
    "ja": "ภาษาญี่ปุ่น (日本語)",
    "zh": "ภาษาจีนตัวย่อ (简体中文)",
    "ko": "ภาษาเกาหลี (한국어)",
}


def _chat(messages: list[dict], temperature: float = 0.3, timeout: int = 300,
          max_tokens: int = 4000, retries: int = 2) -> str:
    """เรียก LLM แบบ streaming (SSE) เพื่อกัน Cloudflare 524 บน origin ที่ตอบช้า.

    การถอด/สรุป transcript ยาว โมเดลอาจใช้เวลาเกิน 120 วินาที ซึ่ง Cloudflare หน้า
    endpoint จะตัดด้วย error 524 ถ้าเป็น request เดียวรอทั้งก้อน — streaming ทำให้มี
    byte ไหลตลอด CF จึงนับ read-timeout ใหม่เรื่อยๆ ไม่ตัดกลางคัน
    ลองใหม่อัตโนมัติเมื่อเจอ error ชั่วคราว (524/502/503/timeout)
    """
    if not config.llm_api_key:
        raise RuntimeError("ยังไม่ได้ตั้ง LLM_API_KEY ใน .env")

    url = f"{config.llm_base_url}/chat/completions"
    payload = json.dumps({
        "model": config.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode("utf-8")

    def build_req() -> urllib.request.Request:
        return urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {config.llm_api_key}",
                "Content-Type": "application/json",
                # Cloudflare หน้า endpoint บล็อก UA ของ urllib (error 1010) จึงต้องตั้งเอง
                "User-Agent": "meeting_ai/0.1 (+https://github.com/meeting-ai)",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return _stream_chat(build_req(), timeout)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code in _RETRY_CODES and attempt < retries:
                last_err = RuntimeError(f"HTTP {e.code}")
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"LLM ตอบกลับผิดพลาด HTTP {e.code}: {body[:500]}") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            reason = getattr(e, "reason", e)
            if attempt < retries:
                last_err = RuntimeError(f"ต่อ LLM ไม่สำเร็จ: {reason}")
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"ต่อ LLM endpoint ไม่ได้: {reason}") from e

    raise RuntimeError(f"เรียก LLM ไม่สำเร็จหลังลอง {retries + 1} ครั้ง: {last_err}")


def _stream_chat(req: urllib.request.Request, timeout: int) -> str:
    """อ่าน SSE stream แล้วประกอบ content. รองรับ fallback ถ้า endpoint ไม่ stream."""
    parts: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" not in ctype:
            # endpoint ไม่ stream — อ่านทั้งก้อนแบบเดิม
            data = json.loads(resp.read().decode("utf-8"))
            return (data["choices"][0]["message"].get("content") or "").strip()
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[len("data:"):].strip()
            if chunk == "[DONE]":
                break
            try:
                delta = json.loads(chunk)["choices"][0].get("delta") or {}
            except (ValueError, KeyError, IndexError):
                continue
            piece = delta.get("content")
            if piece:
                parts.append(piece)

    text = "".join(parts).strip()
    if not text:
        raise RuntimeError(
            "LLM ไม่ได้คืนเนื้อหา (อาจใช้ token หมดไปกับ reasoning หรือถูกตัดกลางคัน) — "
            "ลองใหม่อีกครั้ง หรือลดความยาว transcript"
        )
    return text


def summarize(
    transcript_text: str,
    meeting_title: str | None = None,
    template: str = DEFAULT_TEMPLATE,
    has_speakers: bool = False,
) -> str:
    """รับข้อความ transcript คืนสรุปการประชุมเป็น Markdown ภาษาไทย.

    template: คีย์ใน TEMPLATES — โครงหัวข้อต่างกันตามชนิดการประชุม
    has_speakers: True ถ้า transcript มีชื่อผู้พูดกำกับอยู่ (ให้ LLM ระบุผู้รับผิดชอบได้)
    """
    body = TEMPLATES.get(template, TEMPLATES[DEFAULT_TEMPLATE])["body"]
    meta = f' หัวข้อ "{meeting_title}"' if meeting_title else ""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                meta=meta,
                speaker_note=_SPEAKER_NOTE if has_speakers else "",
                body=body,
                transcript=transcript_text,
            ),
        },
    ]
    return _chat(messages)


def translate(text: str, target_lang: str) -> str:
    """แปลสรุปเป็นภาษาอื่นโดยคงโครงสร้าง Markdown เดิม."""
    if not text.strip():
        raise RuntimeError("ไม่มีข้อความให้แปล")
    language = LANGUAGE_NAMES.get(target_lang, target_lang)
    return _chat(
        [{"role": "user", "content": TRANSLATE_PROMPT.format(language=language, text=text)}],
        temperature=0.1,
    )
