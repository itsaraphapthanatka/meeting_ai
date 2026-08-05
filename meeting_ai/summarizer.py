"""สรุปการประชุมด้วย LLM ผ่าน endpoint แบบ OpenAI-compatible (stdlib ล้วน)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import config

SYSTEM_PROMPT = """คุณคือผู้ช่วยจดและสรุปการประชุมมืออาชีพ
สรุปเป็นภาษาไทยที่กระชับ อ่านง่าย ตรงประเด็น อ้างอิงเฉพาะสิ่งที่ปรากฏใน transcript เท่านั้น
ห้ามแต่งเติมข้อมูลที่ไม่มีในบทสนทนา ถ้าข้อมูลส่วนใดไม่มีให้ระบุว่า "ไม่ได้ระบุ"
"""

USER_TEMPLATE = """ต่อไปนี้คือ transcript ของการประชุม{meta}

จงสรุปโดยใช้รูปแบบ Markdown หัวข้อภาษาไทยตามนี้เป๊ะๆ:

## 📌 สรุปย่อ (TL;DR)
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
- (คำถามที่ยังไม่มีคำตอบ หรือเรื่องที่ต้องคุยต่อ ถ้าไม่มีให้ใส่ "- ไม่มี")

--- TRANSCRIPT ---
{transcript}
--- จบ TRANSCRIPT ---
"""


def _chat(messages: list[dict], temperature: float = 0.3, timeout: int = 180) -> str:
    if not config.llm_api_key:
        raise RuntimeError("ยังไม่ได้ตั้ง LLM_API_KEY ใน .env")

    url = f"{config.llm_base_url}/chat/completions"
    payload = json.dumps(
        {"model": config.llm_model, "messages": messages, "temperature": temperature}
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {config.llm_api_key}",
            "Content-Type": "application/json",
            # Cloudflare หน้า endpoint บล็อก UA ของ urllib (error 1010) จึงต้องตั้งเอง
            "User-Agent": "meeting_ai/0.1 (+https://github.com/meeting-ai)",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"LLM ตอบกลับผิดพลาด HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"ต่อ LLM endpoint ไม่ได้: {e.reason}") from e

    return data["choices"][0]["message"]["content"].strip()


def summarize(transcript_text: str, meeting_title: str | None = None) -> str:
    """รับข้อความ transcript คืนสรุปการประชุมเป็น Markdown ภาษาไทย."""
    meta = f' หัวข้อ "{meeting_title}"' if meeting_title else ""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(meta=meta, transcript=transcript_text)},
    ]
    return _chat(messages)
