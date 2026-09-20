"""สรุปการประชุมด้วย LLM ผ่าน endpoint แบบ OpenAI-compatible (stdlib ล้วน)."""

from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request

from .config import config

# โค้ด HTTP ที่ถือว่าชั่วคราว ลองใหม่ได้ (524 = Cloudflare timeout ฝั่ง origin LLM)
_RETRY_CODES = {429, 500, 502, 503, 504, 520, 522, 524}

# {language} มาจาก LANGUAGE_NAMES — ค่าเริ่มต้น th ทำให้ได้ข้อความเดิมทุกตัวอักษร
SYSTEM_PROMPT = """คุณคือผู้ช่วยจดและสรุปการประชุมมืออาชีพ
สรุปเป็น{language}ที่กระชับ อ่านง่าย ตรงประเด็น อ้างอิงเฉพาะสิ่งที่ปรากฏใน transcript เท่านั้น
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
# ภาษาของตัวสรุป — คงเป็นไทยไว้ ไม่ใช่ตามภาษาของเสียงอัตโนมัติ: ผู้ใช้ไทยที่ประชุมภาษาอังกฤษ
# ส่วนใหญ่อยากได้สรุปไทย การเปลี่ยนค่าเริ่มต้นเป็นเรื่องของเจ้าของผลิตภัณฑ์ ไม่ใช่ผลพลอยได้ของบั๊กฟิกซ์
DEFAULT_SUMMARY_LANG = "th"

# โครงหัวข้อใน TEMPLATES เขียนเป็นภาษาไทยทั้งหมด จะทำเป็นชุดละภาษา (5 เทมเพลต x 5 ภาษา)
# ก็บานปลายและต้องตามแก้พร้อมกันตลอดไป — ใช้โครงเดิมเป็น "สเปกโครงสร้าง" แล้วสั่งให้แปลหัวข้อ
# แทน (วิธีเดียวกับ TRANSLATE_PROMPT ที่รักษาโครง Markdown เดิมไว้ได้อยู่แล้ว)
_FORMAT_TH = "จงสรุปโดยใช้รูปแบบ Markdown หัวข้อภาษาไทยตามนี้เป๊ะๆ:"
_FORMAT_OTHER = """จงสรุปโดยใช้โครง Markdown ด้านล่างนี้ — คงลำดับหัวข้อ อิโมจิ และรูปแบบ
ตาราง/bullet ไว้เป๊ะๆ แต่ให้ **แปลชื่อหัวข้อ และเขียนเนื้อหาทั้งหมดเป็น{language}**
รวมถึงคำแทนค่าว่างอย่าง "ไม่ได้ระบุ" / "ไม่มี" ให้ใช้คำที่เทียบเท่าใน{language}:"""

USER_TEMPLATE = """ต่อไปนี้คือ transcript ของการประชุม{meta}
{speaker_note}
{format_line}

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

# ---- ถาม-ตอบอิสระกับการประชุมหนึ่งครั้ง (ADR-002, BACKLOG #54) ----

# v1 ตอบจาก "สรุป" อย่างเดียว ไม่ส่งบทถอดเสียงเข้าไป (ADR-002 ชั้นที่ 1) — สรุปยาวหลักพัน
# ตัวอักษร ยิงครั้งเดียวจบ ส่วนชั้นที่ 2 (ค้นบทถอดเสียงคำต่อคำ) ยังไม่ทำ เพราะยังไม่มีตัวเลข
# จริงว่าชั้นเดียวพอกี่เปอร์เซ็นต์ (ADR-002 ข้อ 5.1) — ธง `enough` ข้างล่างคือเครื่องมือเก็บ
# ตัวเลขนั้น ไม่ใช่แค่ข้อความสวย ๆ
NOT_ENOUGH = "ข้อมูลไม่พอ"

ASK_PROMPT = """ตอบคำถามเกี่ยวกับการประชุมนี้ โดยใช้**เฉพาะ**สรุปด้านล่างเป็นข้อมูล

กติกา:
- ถ้าสรุปไม่มีข้อมูลพอจะตอบ ให้ขึ้นต้นบรรทัดแรกด้วยคำว่า "{marker}" แล้วอธิบายสั้น ๆ
  ว่าขาดอะไร — ห้ามเดา ห้ามแต่งเพิ่มจากความรู้ทั่วไป
- ถ้าตอบได้ ตอบตรงคำถาม สั้นกระชับ เป็นภาษาไทย ใช้ bullet ได้ถ้ามีหลายข้อ
- ห้ามอ้างเวลาหรือนาทีที่พูด สรุปไม่มีข้อมูลนั้น (ADR-002 ข้อ 5.5)

--- คำถาม ---
{question}

--- สรุปการประชุม ---
{summary}
"""


def answer(question: str, summary: str) -> dict:
    """ตอบคำถามจากสรุป คืน {"text", "enough"}.

    `enough` = โมเดลบอกว่าตอบได้จากสรุปหรือไม่ เก็บไว้เพื่อตอบคำถามของ ADR-002 ข้อ 5.1
    ว่า "ชั้นเดียวพอกี่เปอร์เซ็นต์" ด้วยตัวเลขจริง ไม่ใช่การเดา
    """
    q = (question or "").strip()
    if not q:
        raise RuntimeError("ไม่มีคำถาม")
    if not (summary or "").strip():
        raise RuntimeError("การประชุมนี้ยังไม่มีสรุป — สรุปก่อนแล้วค่อยถาม")
    text = _chat(
        [{"role": "user", "content": ASK_PROMPT.format(
            marker=NOT_ENOUGH, question=q, summary=summary)}],
        temperature=0.2,
    )
    return {"text": text, "enough": not text.strip().startswith(NOT_ENOUGH)}


# ---- สรุปประชุมยาว: แบ่งก้อน -> สกัดข้อเท็จจริง -> รวม (ADR-001, BACKLOG #8) ----

# ขั้น map ขอ "ข้อเท็จจริง" ไม่ใช่ "สรุปย่อย": ถ้าให้แต่ละก้อนสรุปตามเทมเพลต จะได้ TL;DR
# หลายอันมาต่อกัน ซึ่งรวมกลับเป็นสรุปเดียวที่ดีไม่ได้ ข้อเท็จจริงดิบรวมง่ายกว่าและไม่บีบอัดซ้ำสองชั้น
NOTES_PROMPT = """ต่อไปนี้คือ transcript **ช่วงที่ {part} จาก {total}** ของการประชุมเดียวกัน{meta}

อย่าเพิ่งสรุปทั้งการประชุม — ช่วงนี้เป็นแค่ส่วนหนึ่ง จงบันทึกสิ่งที่เกิดขึ้น**เฉพาะในช่วงนี้**
เป็น bullet สั้น ๆ ภายใต้สี่หัวข้อนี้ ใช้คำเดิมของผู้พูดเท่าที่ทำได้ ห้ามเติมสิ่งที่ไม่มีใน transcript
หัวข้อไหนไม่มีให้ใส่ "- ไม่มี"

## ประเด็นที่คุยกัน
## ข้อสรุป/มติ
## งานที่มีคนรับไป (ระบุชื่อผู้รับผิดชอบและกำหนดเสร็จถ้ามี)
## คำถามที่ยังไม่มีคำตอบ

--- TRANSCRIPT ช่วงที่ {part} ---
{transcript}
--- จบช่วงที่ {part} ---
"""

# ขั้น reduce ส่งบันทึกย่อยเข้า USER_TEMPLATE ตัวเดียวกับที่ประชุมสั้นใช้ เทมเพลต/ภาษา/กติกา
# ชื่อผู้พูดจึงทำงานเหมือนเดิมโดยไม่ต้องดูแลสองทาง — ต่างแค่บอกว่าของที่ให้มาเป็นบันทึกย่อย
# พื้นก้อนตอนถอยไปแบ่งใหม่ — เล็กกว่านี้ได้บันทึกย่อยที่สั้นจนไร้ประโยชน์ และยิงถี่โดยไม่จำเป็น
MIN_CHUNK_CHARS = 2000

MERGE_NOTE = """ข้อมูลด้านล่างไม่ใช่ transcript ดิบ แต่เป็น**บันทึกประเด็นของแต่ละช่วง**ที่สกัดมาแล้ว
ตามลำดับเวลา จงรวมเป็นสรุปฉบับเดียวของการประชุมทั้งหมด: รวมเรื่องเดียวกันที่โผล่หลายช่วงเข้าด้วยกัน
ตัดของซ้ำ และเรียงตามความสำคัญ ไม่ใช่เรียงตามช่วง

"""

LANGUAGE_NAMES = {
    "th": "ภาษาไทย",
    "en": "ภาษาอังกฤษ (English)",
    "ja": "ภาษาญี่ปุ่น (日本語)",
    "zh": "ภาษาจีนตัวย่อ (简体中文)",
    "ko": "ภาษาเกาหลี (한국어)",
}


def _chat(messages: list[dict], temperature: float = 0.3, timeout: int = 300,
          max_tokens: int | None = None, retries: int = 2) -> str:
    """เรียก LLM แล้วคืนคำตอบที่ "จบเอง" เท่านั้น — ไม่ยอมคืนคำตอบที่ถูกตัดกลางคัน.

    เพดาน token มาจาก LLM_MAX_TOKENS (ค่าเริ่มต้น 4000) ถ้าโมเดลตอบจนชนเพดาน
    (finish_reason = "length") จะขยายเพดานเป็นเท่าตัวแล้วเรียกใหม่ จนถึง
    LLM_MAX_TOKENS_CEILING แล้วจึง raise — ก่อนหน้านี้ค่านี้ฮาร์ดโค้ดไว้ 4000 และ
    finish_reason ไม่เคยถูกอ่าน สรุปประชุมยาวๆ จึงขาดท้ายแบบเงียบๆ (BACKLOG #7)
    """
    if not config.llm_api_key:
        raise RuntimeError("ยังไม่ได้ตั้ง LLM_API_KEY ใน .env")

    budget = max_tokens if max_tokens is not None else config.llm_max_tokens
    ceiling = max(config.llm_max_tokens_ceiling, budget)

    while True:
        text, finish_reason = _request(messages, temperature, timeout, budget, retries)
        if finish_reason != "length":
            if not text:
                raise RuntimeError(
                    "LLM ไม่ได้คืนเนื้อหา (อาจใช้ token หมดไปกับ reasoning หรือถูกตัดกลางคัน) — "
                    "ลองใหม่อีกครั้ง หรือลดความยาว transcript"
                )
            return text
        if budget >= ceiling:
            raise RuntimeError(
                f"คำตอบจาก LLM ถูกตัดกลางคันที่เพดาน {budget} token — "
                "ขยาย LLM_MAX_TOKENS_CEILING ใน .env หรือลดความยาว transcript"
            )
        budget = min(budget * 2, ceiling)


def _request(messages: list[dict], temperature: float, timeout: int,
             max_tokens: int, retries: int) -> tuple[str, str]:
    """ยิง request เดียว (streaming) คืน (เนื้อหา, finish_reason).

    ใช้ streaming (SSE) เพื่อกัน Cloudflare 524 บน origin ที่ตอบช้า: การถอด/สรุป
    transcript ยาว โมเดลอาจใช้เวลาเกิน 120 วินาที ซึ่ง Cloudflare หน้า endpoint จะตัด
    ด้วย error 524 ถ้าเป็น request เดียวรอทั้งก้อน — streaming ทำให้มี byte ไหลตลอด
    CF จึงนับ read-timeout ใหม่เรื่อยๆ ไม่ตัดกลางคัน
    ลองใหม่อัตโนมัติเมื่อเจอ error ชั่วคราว (524/502/503/timeout)
    """
    payload = json.dumps({
        "model": config.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode("utf-8")
    url = f"{config.llm_base_url}/chat/completions"

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


def _stream_chat(req: urllib.request.Request, timeout: int) -> tuple[str, str]:
    """อ่าน SSE stream แล้วประกอบ content คืน (เนื้อหา, finish_reason).

    finish_reason เป็น "" ถ้า endpoint ไม่ส่งมา — ผู้เรียกต้องถือว่า "จบเอง"
    ไม่งั้น endpoint ที่ไม่ยอมส่งฟิลด์นี้จะโดนขยายเพดาน token ไปเรื่อยๆ
    """
    parts: list[str] = []
    finish_reason = ""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        ctype = resp.headers.get("Content-Type", "")
        if "text/event-stream" not in ctype:
            # endpoint ไม่ stream — อ่านทั้งก้อนแบบเดิม
            data = json.loads(resp.read().decode("utf-8"))
            choice = data["choices"][0]
            return (
                (choice["message"].get("content") or "").strip(),
                choice.get("finish_reason") or "",
            )
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[len("data:"):].strip()
            if chunk == "[DONE]":
                break
            try:
                choice = json.loads(chunk)["choices"][0]
            except (ValueError, KeyError, IndexError):
                continue
            finish_reason = choice.get("finish_reason") or finish_reason
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                parts.append(piece)

    return "".join(parts).strip(), finish_reason


def _split_lines(text: str, budget: int) -> list[str]:
    """แบ่งข้อความเป็นก้อนละไม่เกิน budget ตัวอักษร โดยตัดที่ขอบบรรทัดเสมอ.

    หนึ่งบรรทัด = หนึ่ง segment ของ whisper = ช่วงที่คนหยุดพูด จึงไม่ตัดกลางประโยค
    บรรทัดเดียวที่ยาวเกิน budget เองจะอยู่ก้อนของมันคนเดียว (ยอมให้เกิน) ดีกว่าตัดกลางคำ
    ซึ่งจะทำให้ประโยคเสียความหมายทั้งสองข้าง
    """
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.split(chr(10)):
        add = len(line) + 1
        if current and size + add > budget:
            chunks.append(chr(10).join(current))
            current, size = [], 0
        current.append(line)
        size += add
    if current:
        chunks.append(chr(10).join(current))
    return chunks


def _map_notes(chunks: list[str], meta: str, progress=None) -> str:
    """สกัดบันทึกประเด็นจากแต่ละก้อน คืนข้อความที่ต่อกันแล้วพร้อมส่งเข้าขั้น reduce.

    ทำทีละก้อนแบบเรียงกัน ไม่ขนาน — ยิงพร้อมกันหลาย request ไป endpoint เดียวเสี่ยง 429
    ซึ่งจะกลายเป็นเรื่องยุ่งกว่าเดิม (ต้อง backoff รายก้อน) ดู ADR-001 ข้อ 4.4
    """
    notes = []
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        if progress:
            progress(i, total)
        text = _chat([{"role": "system", "content": SYSTEM_PROMPT.format(
            language=LANGUAGE_NAMES[DEFAULT_SUMMARY_LANG])},
            {"role": "user", "content": NOTES_PROMPT.format(
                part=i, total=total, meta=meta, transcript=chunk)}])
        notes.append(f"### ช่วงที่ {i} จาก {total}{chr(10)}{text.strip()}")
    return (chr(10) * 2).join(notes)


def summarize(
    transcript_text: str,
    meeting_title: str | None = None,
    template: str = DEFAULT_TEMPLATE,
    has_speakers: bool = False,
    target_lang: str = DEFAULT_SUMMARY_LANG,
    progress=None,
) -> str:
    """รับข้อความ transcript คืนสรุปการประชุมเป็น Markdown.

    template: คีย์ใน TEMPLATES — โครงหัวข้อต่างกันตามชนิดการประชุม
    has_speakers: True ถ้า transcript มีชื่อผู้พูดกำกับอยู่ (ให้ LLM ระบุผู้รับผิดชอบได้)
    target_lang: ภาษาของ "ตัวสรุป" ไม่ใช่ภาษาของเสียง — คนละเรื่องกับ --lang/whisper_lang
      ที่บอกว่าเสียงเป็นภาษาอะไร (ประชุมภาษาอังกฤษแล้วอยากได้สรุปไทยเป็นเรื่องปกติ)
      **ค่าเริ่มต้นคือ th** เพื่อไม่ให้สรุปของทุกคนเปลี่ยนภาษาเองจากการอัปเกรด
      รหัสที่ไม่รู้จักจะถูกส่งให้ LLM ตามตัว (เหมือน translate) ไม่ใช่เงียบ ๆ กลับไปเป็นไทย
    """
    body = TEMPLATES.get(template, TEMPLATES[DEFAULT_TEMPLATE])["body"]
    meta = f' หัวข้อ "{meeting_title}"' if meeting_title else ""
    lang = (target_lang or DEFAULT_SUMMARY_LANG).strip() or DEFAULT_SUMMARY_LANG
    language = LANGUAGE_NAMES.get(lang, lang)
    format_line = (_FORMAT_TH if lang == DEFAULT_SUMMARY_LANG
                   else _FORMAT_OTHER.format(language=language))
    def ask(source: str, merged: bool) -> str:
        return _chat([
            {"role": "system", "content": SYSTEM_PROMPT.format(language=language)},
            {
                "role": "user",
                "content": (MERGE_NOTE if merged else "") + USER_TEMPLATE.format(
                    meta=meta,
                    speaker_note="" if merged else (_SPEAKER_NOTE if has_speakers else ""),
                    format_line=format_line,
                    body=body,
                    transcript=source,
                ),
            },
        ])

    budget = config.llm_chunk_chars
    if len(transcript_text) > budget:
        chunks = _split_lines(transcript_text, budget)
        return ask(_map_notes(chunks, meta, progress), merged=True)

    try:
        return ask(transcript_text, merged=False)
    except RuntimeError as e:
        # งบตัวอักษรเป็นการประมาณ มันผิดได้ (ภาษาที่กินโทเคนมากกว่าไทย / โมเดล context เล็กกว่าที่คิด)
        # ยิงตรงแล้วโดนปฏิเสธ = ลอง map-reduce หนึ่งครั้งก่อนยอมแพ้ ดีกว่าล้มถาวรทั้งที่แก้ได้
        # จงใจไม่อ่านข้อความ error เพื่อเดาว่า "ใช่ context เกินไหม" — ข้อความต่างกันไปตาม
        # ผู้ให้บริการและเปลี่ยนได้ทุกเมื่อ จับคำเมื่อไรก็กลายเป็นจุดพังที่ไม่มีใครเห็น (ADR-001 ข้อ 2.4)
        if not _worth_chunking(e, transcript_text):
            raise
        # ต้องแบ่งด้วยงบที่ **เล็กกว่าเดิม**: งบเดิมคือตัวที่เพิ่งพิสูจน์ว่าใหญ่เกินสำหรับโมเดลนี้
        # แบ่งด้วยค่าเดิมจะได้ก้อนเดียวเสมอ (เพราะข้อความสั้นกว่างบอยู่แล้ว) = โค้ดที่ไม่มีวันทำงาน
        retry_budget = max(MIN_CHUNK_CHARS, min(budget, len(transcript_text) // 2))
        chunks = _split_lines(transcript_text, retry_budget)
        if len(chunks) < 2:
            raise           # สั้นเกินกว่าจะแบ่ง = ปัญหาไม่ได้อยู่ที่ความยาว ยิงซ้ำก็ได้ผลเดิม
        return ask(_map_notes(chunks, meta, progress), merged=True)


def _worth_chunking(err: Exception, text: str) -> bool:
    """ล้มแบบนี้ ลองแบ่งก้อนแล้วมีหวังไหม.

    เอาเฉพาะ 4xx ที่ไม่ใช่เรื่องสิทธิ์/โควตา: 401/403 แบ่งกี่ก้อนก็ไม่ผ่าน ส่วน 429 คือโดน
    จำกัดอัตรา ซึ่งการยิงเพิ่มหกก้อนมีแต่จะแย่ลง 5xx ถูก _request() ลองใหม่ให้แล้ว
    """
    m = re.search(r"HTTP (\d{3})", str(err))
    if not m:
        return False
    code = int(m.group(1))
    return 400 <= code < 500 and code not in (401, 403, 429)


def translate(text: str, target_lang: str) -> str:
    """แปลสรุปเป็นภาษาอื่นโดยคงโครงสร้าง Markdown เดิม."""
    if not text.strip():
        raise RuntimeError("ไม่มีข้อความให้แปล")
    language = LANGUAGE_NAMES.get(target_lang, target_lang)
    return _chat(
        [{"role": "user", "content": TRANSLATE_PROMPT.format(language=language, text=text)}],
        temperature=0.1,
    )
