"""BACKLOG #8 — ประชุมยาวต้องสรุปได้ ไม่ใช่ชน context ของ LLM แล้วล้มถาวร.

`summarize()` ยัดบทถอดเสียงทั้งไฟล์ลง user message เดียวเสมอ พอยาวเกิน context window
ฝั่ง endpoint ตอบ HTTP 400 ซึ่งไม่อยู่ใน `_RETRY_CODES` จึงกลายเป็น `summary_error` ทันที
และ **ล้มแบบถาวร** — ปุ่ม "สรุปใหม่ด้วย AI" ส่ง prompt ก้อนเดิมเป๊ะ ๆ กดกี่รอบก็ได้ผลเดิม

ขนาดของจริง (อนุมานจากประชุมที่ใหญ่ที่สุดใน production: 149 นาที / 2,905 segment /
payload 0.64 MB) ≈ 900 ตัวอักษรต่อนาที → ประชุม 2 ชั่วโมง ≈ 108,000 ตัวอักษร
= 54,000–108,000 โทเคน ซึ่งเกิน context 32k แน่ ๆ

การตัดสินใจทั้งหมดอยู่ใน `docs/adr/ADR-001-transcript-chunking.md` เทสต์ไฟล์นี้คุมข้อที่
เปลี่ยนพฤติกรรมจริง โดยเฉพาะ **เส้นที่ประชุมสั้นต้องไม่เปลี่ยนอะไรเลย** — ผู้ใช้ส่วนใหญ่อยู่ตรงนั้น
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import summarizer

ADR = Path(__file__).resolve().parents[1] / "docs" / "adr" / "ADR-001-transcript-chunking.md"


class Recorder:
    """แทน _chat — เก็บทุก prompt ที่ถูกส่ง และตอบตามสคริปต์."""

    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls: list[list[dict]] = []

    def __call__(self, messages, **kw):
        self.calls.append(messages)
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return outcome or f"คำตอบที่ {len(self.calls)}"

    @property
    def users(self) -> list[str]:
        return [m[-1]["content"] for m in self.calls]


def transcript(lines: int, per_line: int = 100) -> str:
    return "\n".join(f"ผู้พูด {i % 3 + 1}: " + "ก" * per_line for i in range(lines))


class TestShortMeetingsAreUntouched(unittest.TestCase):
    """คนส่วนใหญ่ประชุมสั้น ทางนั้นต้องเหมือนเดิมทุกตัวอักษร."""

    def test_one_call_and_the_raw_transcript(self):
        chat = Recorder()
        text = transcript(5)
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(text)
        self.assertEqual(len(chat.calls), 1)
        self.assertIn(text, chat.users[0])

    def test_no_merge_wording_leaks_into_the_short_path(self):
        chat = Recorder()
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(transcript(5))
        self.assertNotIn("บันทึกประเด็นของแต่ละช่วง", chat.users[0])

    def test_the_speaker_note_still_applies(self):
        chat = Recorder()
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(transcript(5), has_speakers=True)
        self.assertIn("ห้ามเดาชื่อคนที่ไม่ปรากฏ", chat.users[0])


class TestLongMeetingsAreChunked(unittest.TestCase):

    def setUp(self) -> None:
        p = mock.patch.object(summarizer.config, "llm_chunk_chars", 500)
        p.start()
        self.addCleanup(p.stop)

    def test_it_splits_and_then_merges(self):
        chat = Recorder()
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(transcript(40))
        self.assertGreater(len(chat.calls), 2, "ต้องมีหลายก้อน + หนึ่งรอบรวม")
        self.assertIn("ช่วงที่ 1 จาก", chat.users[0])
        self.assertIn("บันทึกประเด็นของแต่ละช่วง", chat.users[-1])

    def test_every_line_reaches_some_chunk(self):
        # ตัดบทถอดเสียงทิ้งเงียบ ๆ = ผู้ใช้ได้สรุปที่ดูสมบูรณ์แต่ไม่มีครึ่งหลังของประชุม
        chat = Recorder()
        text = transcript(40)
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(text)
        sent = "".join(chat.users[:-1])
        for line in text.split("\n"):
            self.assertIn(line, sent)

    def test_the_merge_step_keeps_the_template_and_language(self):
        chat = Recorder()
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(transcript(40), template="standup", target_lang="en")
        merged = chat.users[-1]
        self.assertIn(summarizer.TEMPLATES["standup"]["body"][:40], merged)
        self.assertIn("แปลชื่อหัวข้อ", merged)
        self.assertIn("ภาษาอังกฤษ (English)", "".join(m[0]["content"] for m in chat.calls[-1:]))

    def test_the_map_step_is_told_not_to_summarise_the_whole_meeting(self):
        chat = Recorder()
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(transcript(40))
        self.assertIn("อย่าเพิ่งสรุปทั้งการประชุม", chat.users[0])

    def test_the_map_step_asks_for_owners_of_action_items(self):
        # ส่วนที่ผู้ใช้ใช้จริงที่สุด และเป็นส่วนที่หายง่ายสุดตอนบีบอัดสองชั้น
        chat = Recorder()
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(transcript(40))
        self.assertIn("ผู้รับผิดชอบ", chat.users[0])

    def test_progress_is_reported_per_chunk(self):
        seen = []
        chat = Recorder()
        with mock.patch.object(summarizer, "_chat", chat):
            summarizer.summarize(transcript(40), progress=lambda i, n: seen.append((i, n)))
        self.assertTrue(seen)
        self.assertEqual(seen[0][0], 1)
        self.assertEqual(seen[-1][0], seen[-1][1], "ก้อนสุดท้ายต้องรายงานว่าเป็นก้อนที่ n จาก n")


class TestSplitting(unittest.TestCase):

    def test_chunks_respect_the_budget(self):
        text = transcript(40, per_line=50)
        for chunk in summarizer._split_lines(text, 500):
            self.assertLessEqual(len(chunk), 500)

    def test_it_never_cuts_inside_a_line(self):
        # หนึ่งบรรทัด = หนึ่ง segment = ช่วงที่คนหยุดพูด ตัดกลางประโยคทำให้เสียความหมายสองข้าง
        text = transcript(40, per_line=50)
        chunks = summarizer._split_lines(text, 500)
        for line in text.split("\n"):
            self.assertTrue(any(line in c for c in chunks), line[:20])

    def test_a_single_huge_line_gets_its_own_chunk(self):
        text = "สั้น\n" + "ย" * 5000 + "\nสั้น"
        chunks = summarizer._split_lines(text, 500)
        self.assertTrue(any(len(c) > 500 for c in chunks), "ยอมให้เกิน ดีกว่าตัดกลางคำ")

    def test_nothing_is_lost_or_duplicated(self):
        text = transcript(37, per_line=33)
        self.assertEqual("\n".join(summarizer._split_lines(text, 400)), text)


class TestFallbackWhenTheDirectCallIsRejected(unittest.TestCase):
    """งบตัวอักษรเป็นการประมาณ มันผิดได้ — ล้มถาวรทั้งที่แก้ได้คือสิ่งที่ตั๋วนี้บ่นถึง."""

    def test_a_400_triggers_the_chunked_path(self):
        # บทถอดเสียง **สั้นกว่างบ** จึงยิงตรงก่อน แล้วพอโดนปฏิเสธต้องถอยไปแบ่งก้อน
        chat = Recorder([RuntimeError("LLM ตอบกลับผิดพลาด HTTP 400: context length exceeded")])
        with mock.patch.object(summarizer.config, "llm_chunk_chars", 1_000_000):
            with mock.patch.object(summarizer, "_chat", chat):
                out = summarizer.summarize(transcript(200))
        self.assertTrue(out)
        self.assertGreater(len(chat.calls), 2, "ครั้งแรกล้ม แล้วต้องลองแบบแบ่งก้อน")
        self.assertIn("ช่วงที่ 1 จาก", chat.users[1])

    def test_the_retry_splits_smaller_than_the_budget_that_just_failed(self):
        # ถ้าแบ่งด้วยงบเดิม จะได้ก้อนเดียวเสมอ (ข้อความสั้นกว่างบอยู่แล้ว) = โค้ดที่ไม่มีวันทำงาน
        chat = Recorder([RuntimeError("HTTP 400 too long")])
        text = transcript(200)
        with mock.patch.object(summarizer.config, "llm_chunk_chars", 1_000_000):
            with mock.patch.object(summarizer, "_chat", chat):
                summarizer.summarize(text)
        maps = [u for u in chat.users if "ช่วงที่" in u and "บันทึกประเด็น" not in u]
        self.assertGreaterEqual(len(maps), 2, "ต้องได้อย่างน้อยสองก้อน")

    def test_an_auth_error_is_not_retried_by_chunking(self):
        # 401 แบ่งกี่ก้อนก็ไม่ผ่าน ยิงเพิ่มคือเผาเวลาและเงินเปล่า
        chat = Recorder([RuntimeError("LLM ตอบกลับผิดพลาด HTTP 401: bad key")])
        with mock.patch.object(summarizer.config, "llm_chunk_chars", 500):
            with mock.patch.object(summarizer, "_chat", chat):
                with self.assertRaises(RuntimeError):
                    summarizer.summarize(transcript(40))
        self.assertEqual(len(chat.calls), 1)

    def test_a_rate_limit_is_not_retried_by_chunking(self):
        # 429 คือโดนจำกัดอัตราอยู่แล้ว ยิงเพิ่มหกก้อนมีแต่จะแย่ลง
        chat = Recorder([RuntimeError("HTTP 429 too many requests")])
        with mock.patch.object(summarizer.config, "llm_chunk_chars", 500):
            with mock.patch.object(summarizer, "_chat", chat):
                with self.assertRaises(RuntimeError):
                    summarizer.summarize(transcript(40))
        self.assertEqual(len(chat.calls), 1)

    def test_a_connection_failure_is_not_retried_by_chunking(self):
        chat = Recorder([RuntimeError("ต่อ LLM endpoint ไม่ได้: timed out")])
        with mock.patch.object(summarizer, "_chat", chat):
            with self.assertRaises(RuntimeError):
                summarizer.summarize(transcript(5))
        self.assertEqual(len(chat.calls), 1)

    def test_a_transcript_too_short_to_split_is_not_retried(self):
        # แบ่งแล้วได้ก้อนเดียว = ยิงซ้ำของเดิม ซึ่งจะล้มเหมือนเดิม
        chat = Recorder([RuntimeError("HTTP 400 nope")])
        with mock.patch.object(summarizer, "_chat", chat):
            with self.assertRaises(RuntimeError):
                summarizer.summarize("บรรทัดเดียวสั้น ๆ")
        self.assertEqual(len(chat.calls), 1)


class TestTheDecisionIsWrittenDown(unittest.TestCase):
    """ตั๋วสั่ง 'ADR first' — โค้ดที่มาพร้อมเหตุผลที่บันทึกไว้ ต่างจากโค้ดที่มาเฉย ๆ."""

    def test_the_adr_exists(self):
        self.assertTrue(ADR.exists(), f"ไม่พบ {ADR}")

    def test_it_records_the_rejected_options(self):
        text = ADR.read_text(encoding="utf-8")
        for option in ("ตัดบทถอดเสียงให้สั้นลง", "tokenizer", "overlap", "ขนาน", "refine"):
            with self.subTest(option=option):
                self.assertIn(option, text)

    def test_it_says_what_is_still_unknown(self):
        self.assertIn("สิ่งที่ยังไม่รู้", ADR.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
