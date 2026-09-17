"""BACKLOG #7 — สรุปยาวๆ ถูกตัดเงียบๆ เพราะ max_tokens ฮาร์ดโค้ด 4000 และไม่มีใครอ่าน finish_reason.

ก่อนแก้: summarizer._chat ส่ง "max_tokens": 4000 ตายตัว และ _stream_chat ประกอบเฉพาะ
delta.content ทิ้ง finish_reason ทั้งหมด — พอโมเดลตอบชนเพดาน ผู้ใช้จะได้สรุปที่ขาดท้าย
โดยไม่มี error ใดๆ บอก

หลังแก้: เพดานมาจาก LLM_MAX_TOKENS, finish_reason = "length" ทำให้ขยายเพดานเป็นเท่าตัว
แล้วเรียกใหม่จนถึง LLM_MAX_TOKENS_CEILING แล้วจึง raise แทนที่จะคืนของที่ขาด
"""

from __future__ import annotations

import os

os.environ["MEETING_AI_CLOUD"] = "0"
os.environ["DATABASE_URL"] = ""
os.environ["REMOTE_WORKER"] = "1"
os.environ["S3_BUCKET"] = ""

import json  # noqa: E402
import unittest  # noqa: E402
from unittest import mock  # noqa: E402

from meeting_ai import summarizer  # noqa: E402
from meeting_ai.config import _get_int, config  # noqa: E402

MESSAGES = [{"role": "user", "content": "สรุปให้หน่อย"}]


class FakeResponse:
    """พอสำหรับ _stream_chat: เป็น context manager, มี headers.get, iterate ได้, read() ได้."""

    def __init__(self, lines: list[bytes], ctype: str = "text/event-stream", body: bytes = b""):
        self._lines = lines
        self._body = body
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)

    def read(self) -> bytes:
        return self._body


def sse(*chunks: dict) -> list[bytes]:
    lines = [b"data: " + json.dumps(c).encode("utf-8") for c in chunks]
    lines.append(b"data: [DONE]")
    return lines


def delta(content: str | None = None, finish: str | None = None) -> dict:
    choice: dict = {}
    if content is not None:
        choice["delta"] = {"content": content}
    if finish is not None:
        choice["finish_reason"] = finish
    return {"choices": [choice]}


class Recorder:
    """แทน urlopen — เก็บ max_tokens ของทุกครั้งที่ถูกเรียก แล้วคืน response ตามคิว."""

    def __init__(self, responses: list[FakeResponse]):
        self._responses = list(responses)
        self.budgets: list[int] = []

    def __call__(self, req, timeout=None):
        self.budgets.append(json.loads(req.data.decode("utf-8"))["max_tokens"])
        if not self._responses:
            raise AssertionError("urlopen ถูกเรียกมากกว่าจำนวน response ที่เตรียมไว้")
        return self._responses.pop(0)


class ChatTokenBudget(unittest.TestCase):
    def setUp(self):
        # config อ่าน env ตอน import จึงต้อง patch ที่ตัวคลาสแทนการตั้ง env
        patcher = mock.patch.multiple(
            config,
            llm_api_key="test-key",
            llm_base_url="https://llm.invalid/v1",
            llm_model="test-model",
            llm_max_tokens=4000,
            llm_max_tokens_ceiling=16000,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_chat(self, responses: list[FakeResponse], **kw) -> tuple[str, Recorder]:
        rec = Recorder(responses)
        with mock.patch("urllib.request.urlopen", rec):
            return summarizer._chat(MESSAGES, **kw), rec

    # ---- finish_reason ถูกอ่านจริง ----

    def test_stream_reports_finish_reason(self):
        rec = Recorder([FakeResponse(sse(delta("สรุป"), delta(finish="length")))])
        with mock.patch("urllib.request.urlopen", rec):
            text, finish = summarizer._request(MESSAGES, 0.3, 300, 4000, 0)
        self.assertEqual(text, "สรุป")
        self.assertEqual(finish, "length", "finish_reason จาก SSE ต้องถูกส่งกลับ ไม่ใช่ถูกทิ้ง")

    def test_non_streaming_endpoint_reports_finish_reason(self):
        body = json.dumps({
            "choices": [{"message": {"content": "สรุปสั้น"}, "finish_reason": "length"}]
        }).encode("utf-8")
        rec = Recorder([FakeResponse([], ctype="application/json", body=body)])
        with mock.patch("urllib.request.urlopen", rec):
            text, finish = summarizer._request(MESSAGES, 0.3, 300, 4000, 0)
        self.assertEqual(text, "สรุปสั้น")
        self.assertEqual(finish, "length")

    # ---- เพดานตั้งค่าได้ ----

    def test_budget_comes_from_config(self):
        with mock.patch.object(config, "llm_max_tokens", 9001):
            _, rec = self.run_chat([FakeResponse(sse(delta("ok"), delta(finish="stop")))])
        self.assertEqual(rec.budgets, [9001], "max_tokens ต้องมาจาก config ไม่ใช่ค่าคงที่ 4000")

    def test_explicit_max_tokens_wins(self):
        _, rec = self.run_chat(
            [FakeResponse(sse(delta("ok"), delta(finish="stop")))], max_tokens=512
        )
        self.assertEqual(rec.budgets, [512])

    def test_config_reads_env_and_rejects_junk(self):
        with mock.patch.dict(os.environ, {"X_TOK": "12000"}):
            self.assertEqual(_get_int("X_TOK", 4000, minimum=256), 12000)
        for junk in ("", "abc", "0", "-5", "12.5"):
            with mock.patch.dict(os.environ, {"X_TOK": junk}):
                self.assertEqual(
                    _get_int("X_TOK", 4000, minimum=256), 4000,
                    f"ค่าขยะ {junk!r} ต้องตกไปใช้ default",
                )

    # ---- ถูกตัด = ขยายเพดานแล้วลองใหม่ ----

    def test_truncated_answer_retries_with_double_budget(self):
        text, rec = self.run_chat([
            FakeResponse(sse(delta("ครึ่งเดียว"), delta(finish="length"))),
            FakeResponse(sse(delta("สรุปเต็ม"), delta(finish="stop"))),
        ])
        self.assertEqual(text, "สรุปเต็ม", "ต้องคืนคำตอบที่จบเอง ไม่ใช่ก้อนที่ถูกตัด")
        self.assertEqual(rec.budgets, [4000, 8000])

    def test_budget_growth_stops_at_ceiling(self):
        with mock.patch.object(config, "llm_max_tokens_ceiling", 10000):
            rec = Recorder([
                FakeResponse(sse(delta("a"), delta(finish="length"))),
                FakeResponse(sse(delta("b"), delta(finish="length"))),
                FakeResponse(sse(delta("c"), delta(finish="stop"))),
            ])
            with mock.patch("urllib.request.urlopen", rec):
                text = summarizer._chat(MESSAGES)
        self.assertEqual(text, "c")
        self.assertEqual(rec.budgets, [4000, 8000, 10000], "ก้าวสุดท้ายต้องหยุดที่เพดาน")

    def test_still_truncated_at_ceiling_raises(self):
        with mock.patch.object(config, "llm_max_tokens_ceiling", 8000):
            rec = Recorder([
                FakeResponse(sse(delta("a"), delta(finish="length"))),
                FakeResponse(sse(delta("b"), delta(finish="length"))),
            ])
            with mock.patch("urllib.request.urlopen", rec):
                with self.assertRaises(RuntimeError) as cm:
                    summarizer._chat(MESSAGES)
        self.assertIn("ถูกตัดกลางคัน", str(cm.exception))
        self.assertIn("LLM_MAX_TOKENS_CEILING", str(cm.exception))
        self.assertEqual(rec.budgets, [4000, 8000])

    def test_empty_answer_at_length_also_grows(self):
        """token หมดไปกับ reasoning จน content ว่าง ก็ต้องได้เพดานใหม่ ไม่ใช่ error ทันที."""
        text, rec = self.run_chat([
            FakeResponse(sse(delta(finish="length"))),
            FakeResponse(sse(delta("มาแล้ว"), delta(finish="stop"))),
        ])
        self.assertEqual(text, "มาแล้ว")
        self.assertEqual(rec.budgets, [4000, 8000])

    # ---- ไม่ถดถอย ----

    def test_missing_finish_reason_is_treated_as_complete(self):
        """endpoint ที่ไม่ส่ง finish_reason ต้องไม่ถูกขยายเพดานวนไปเรื่อยๆ."""
        text, rec = self.run_chat([FakeResponse(sse(delta("ตอบครบ")))])
        self.assertEqual(text, "ตอบครบ")
        self.assertEqual(rec.budgets, [4000], "เรียกครั้งเดียวพอ")

    def test_empty_answer_without_length_still_raises(self):
        rec = Recorder([FakeResponse(sse(delta(finish="stop")))])
        with mock.patch("urllib.request.urlopen", rec):
            with self.assertRaises(RuntimeError) as cm:
                summarizer._chat(MESSAGES)
        self.assertIn("ไม่ได้คืนเนื้อหา", str(cm.exception))

    def test_missing_api_key_raises_before_any_request(self):
        rec = Recorder([])
        with mock.patch.object(config, "llm_api_key", ""):
            with mock.patch("urllib.request.urlopen", rec):
                with self.assertRaises(RuntimeError) as cm:
                    summarizer._chat(MESSAGES)
        self.assertIn("LLM_API_KEY", str(cm.exception))
        self.assertEqual(rec.budgets, [])


class PublicHelpers(unittest.TestCase):
    """summarize()/translate() ยังเรียก _chat ตามเดิม — เปลี่ยนแค่ชั้นใน."""

    def setUp(self):
        patcher = mock.patch.multiple(
            config, llm_api_key="test-key", llm_model="test-model",
            llm_max_tokens=4000, llm_max_tokens_ceiling=16000,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_summarize_uses_configured_budget(self):
        with mock.patch.object(config, "llm_max_tokens", 12000):
            rec = Recorder([FakeResponse(sse(delta("# สรุป"), delta(finish="stop")))])
            with mock.patch("urllib.request.urlopen", rec):
                out = summarizer.summarize("สวัสดีครับ วันนี้ประชุมเรื่องงบ")
        self.assertEqual(out, "# สรุป")
        self.assertEqual(rec.budgets, [12000])

    def test_translate_truncated_then_retried(self):
        rec = Recorder([
            FakeResponse(sse(delta("half"), delta(finish="length"))),
            FakeResponse(sse(delta("full"), delta(finish="stop"))),
        ])
        with mock.patch("urllib.request.urlopen", rec):
            out = summarizer.translate("# สรุป\n- ข้อ 1", "en")
        self.assertEqual(out, "full")
        self.assertEqual(rec.budgets, [4000, 8000])


if __name__ == "__main__":
    unittest.main()
