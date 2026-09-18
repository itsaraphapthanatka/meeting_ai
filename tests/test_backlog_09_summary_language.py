"""BACKLOG #9 — `summarize()` ออกภาษาไทยเสมอ ภาษาที่ผู้ใช้เลือกไม่เคยไปถึง prompt.

ก่อนแก้: SYSTEM_PROMPT เขียนตายตัวว่า "สรุปเป็นภาษาไทย" และ USER_TEMPLATE สั่งว่า
"หัวข้อภาษาไทยตามนี้เป๊ะๆ" ส่วน `summarize()` ไม่มีพารามิเตอร์ภาษาให้ส่งเลย ต่อให้ผู้ใช้
สั่งอะไรมาก็ตกที่พื้นตั้งแต่ชั้น API

หลังแก้: `target_lang` เดินทางจาก CLI -> pipeline/runner -> prompt จริง

สองเรื่องที่ตั้งใจไม่ทำ และมีเทสต์ล็อกไว้:

1. **ค่าเริ่มต้นยังเป็นไทย** ไม่ใช่ตามภาษาของเสียง — ผู้ใช้ไทยที่ประชุมภาษาอังกฤษส่วนใหญ่
   อยากได้สรุปไทย การเปลี่ยนค่าเริ่มต้นเป็นการตัดสินใจของเจ้าของผลิตภัณฑ์ ไม่ใช่ผลพลอยได้
   ของบั๊กฟิกซ์ prompt ภาษาไทยจึงต้องเหมือนเดิม **ทุกตัวอักษร**
2. **ไม่ทำเทมเพลตแยกรายภาษา** (5 เทมเพลต x 5 ภาษา = 25 ชุดที่ต้องตามแก้พร้อมกันตลอดไป)
   ใช้โครงไทยเป็น "สเปกโครงสร้าง" แล้วสั่งให้แปลหัวข้อ วิธีเดียวกับ TRANSLATE_PROMPT
"""

from __future__ import annotations

import argparse
import unittest
from unittest import mock

from meeting_ai import cli, pipeline, runner, summarizer

TRANSCRIPT = "[00:00] สมชาย: เปิดประชุมครับ\n[00:05] สมหญิง: ขอเริ่มที่งบก่อน"


def captured(**kwargs) -> list[dict]:
    """เรียก summarize() แล้วคืน messages ที่ถูกส่งเข้า _chat จริง ๆ."""
    seen = {}

    def fake_chat(messages, **_):
        seen["messages"] = messages
        return "## สรุป"

    with mock.patch.object(summarizer, "_chat", fake_chat):
        summarizer.summarize(TRANSCRIPT, **kwargs)
    return seen["messages"]


def prompt_text(**kwargs) -> str:
    return "\n".join(m["content"] for m in captured(**kwargs))


class TestTheDefaultDidNotMove(unittest.TestCase):

    def test_the_default_is_thai(self):
        self.assertEqual(summarizer.DEFAULT_SUMMARY_LANG, "th")

    def test_the_thai_prompt_is_byte_for_byte_what_it_was(self):
        # ถ้าข้อความนี้เปลี่ยน สรุปของผู้ใช้เดิมทุกคนเปลี่ยนตาม — ต้องเป็นการตัดสินใจ ไม่ใช่อุบัติเหตุ
        msgs = captured()
        self.assertEqual(msgs[0]["content"], (
            "คุณคือผู้ช่วยจดและสรุปการประชุมมืออาชีพ\n"
            "สรุปเป็นภาษาไทยที่กระชับ อ่านง่าย ตรงประเด็น อ้างอิงเฉพาะสิ่งที่ปรากฏใน transcript เท่านั้น\n"
            'ห้ามแต่งเติมข้อมูลที่ไม่มีในบทสนทนา ถ้าข้อมูลส่วนใดไม่มีให้ระบุว่า "ไม่ได้ระบุ"\n'
        ))
        self.assertIn("จงสรุปโดยใช้รูปแบบ Markdown หัวข้อภาษาไทยตามนี้เป๊ะๆ:", msgs[1]["content"])

    def test_asking_for_thai_explicitly_gives_the_same_prompt(self):
        self.assertEqual(captured(), captured(target_lang="th"))

    def test_a_blank_language_falls_back_to_thai(self):
        self.assertEqual(captured(target_lang=""), captured())
        self.assertEqual(captured(target_lang=None), captured())


class TestTheLanguageReachesThePrompt(unittest.TestCase):

    def test_english_is_named_in_the_system_prompt(self):
        self.assertIn("สรุปเป็นภาษาอังกฤษ (English)", captured(target_lang="en")[0]["content"])

    def test_the_headings_are_told_to_be_translated(self):
        body = captured(target_lang="en")[1]["content"]
        self.assertIn("แปลชื่อหัวข้อ", body)
        self.assertIn("ภาษาอังกฤษ (English)", body)
        self.assertNotIn("หัวข้อภาษาไทยตามนี้เป๊ะๆ", body)

    def test_the_structure_is_still_pinned(self):
        # เปลี่ยนภาษาแล้วโครงต้องไม่หาย ไม่งั้นสรุปภาษาอื่นจะได้หัวข้ออะไรก็ได้ตามใจโมเดล
        body = captured(target_lang="ja")[1]["content"]
        self.assertIn("## 📌 สรุปย่อ (TL;DR)", body)
        self.assertIn("## 📋 สิ่งที่ต้องทำต่อ (Action Items)", body)

    def test_every_offered_language_is_named_not_left_as_a_code(self):
        for code, name in summarizer.LANGUAGE_NAMES.items():
            with self.subTest(code=code):
                self.assertIn(name, prompt_text(target_lang=code))

    def test_an_unknown_code_is_passed_through_not_silently_thai(self):
        # เงียบ ๆ กลับไปเป็นไทยคือบั๊กเดิมในรูปใหม่ — ผู้ใช้สั่งแล้วไม่เกิดอะไรขึ้นและไม่มีใครบอก
        text = prompt_text(target_lang="de")
        self.assertIn("de", text)
        self.assertNotIn("สรุปเป็นภาษาไทยที่กระชับ", text)

    def test_the_template_choice_still_works_per_language(self):
        body = captured(template="standup", target_lang="en")[1]["content"]
        self.assertIn(summarizer.TEMPLATES["standup"]["body"][:40], body)

    def test_the_speaker_note_still_works_per_language(self):
        self.assertIn("ห้ามเดาชื่อคนที่ไม่ปรากฏ",
                      captured(has_speakers=True, target_lang="en")[1]["content"])


class TestItTravelsFromTheCallers(unittest.TestCase):
    """พารามิเตอร์ที่ไม่มีใครส่งมาก็ไม่ได้แก้อะไร — ไล่ตั้งแต่ CLI ถึง prompt."""

    def _args(self, cmd: str, *argv: str) -> argparse.Namespace:
        return cli.build_parser().parse_args([cmd, *argv])

    def test_the_cli_exposes_summary_lang_separately_from_lang(self):
        args = self._args("process", "a.wav", "--lang", "en", "--summary-lang", "ja")
        self.assertEqual(args.lang, "en", "--lang ยังต้องหมายถึงภาษาของเสียง")
        self.assertEqual(args.summary_lang, "ja")

    def test_the_cli_default_is_thai(self):
        self.assertEqual(self._args("summarize", "t.txt").summary_lang, "th")

    def test_an_unsupported_cli_language_is_rejected_early(self):
        # ล้มตั้งแต่ตอนพิมพ์คำสั่ง ดีกว่าไปเสียเงินค่า LLM แล้วได้ภาษาที่ไม่ได้ขอ
        with self.assertRaises(SystemExit):
            self._args("summarize", "t.txt", "--summary-lang", "xx")

    def test_pipeline_passes_it_on(self):
        seen = {}
        with mock.patch.object(pipeline.summarizer, "summarize",
                               lambda *a, **k: seen.update(k) or "s"), \
             mock.patch.object(pipeline.stt, "resolve", lambda p: "local"), \
             mock.patch.object(pipeline.stt, "label", lambda u: "local"), \
             mock.patch.object(pipeline.stt, "transcribe",
                               lambda *a, **k: (mock.Mock(text="x", segments=[], language="en",
                                                          to_timestamped=lambda: "x"), None)), \
             mock.patch.object(pipeline.Path, "write_text", lambda *a, **k: None), \
             mock.patch.object(pipeline.Path, "mkdir", lambda *a, **k: None):
            pipeline.process_file("a.wav", summary_lang="ko")
        self.assertEqual(seen.get("target_lang"), "ko")

    def test_the_runner_reads_it_from_the_job_spec(self):
        seen = {}
        spec = {"segments": [{"start": 0, "end": 1, "text": "hi"}], "summary_lang": "en"}
        with mock.patch.object(runner.summarizer, "summarize",
                               lambda *a, **k: seen.update(k) or "s"):
            runner.summarize_job(spec, lambda *a: None)
        self.assertEqual(seen.get("target_lang"), "en")

    def test_a_job_spec_without_the_field_still_gets_thai(self):
        seen = {}
        spec = {"segments": [{"start": 0, "end": 1, "text": "hi"}]}
        with mock.patch.object(runner.summarizer, "summarize",
                               lambda *a, **k: seen.update(k) or "s"):
            runner.summarize_job(spec, lambda *a: None)
        self.assertEqual(seen.get("target_lang"), "th")


if __name__ == "__main__":
    unittest.main()
