"""BACKLOG #9b — "สรุปใหม่ด้วย AI" เลือกภาษาได้ และสรุปภาษาอื่นต้องมีที่อยู่ของตัวเอง.

BACKLOG #9 ทำให้ `summarize(target_lang=...)` ทำงานจริงตั้งแต่ CLI ถึง prompt แต่ฝั่งเว็บ
ยังสรุปเป็นไทยอย่างเดียว เพราะ `POST /resummarize` ไม่รับภาษา และ**สรุปภาษาอื่นไม่มีที่เก็บ**
— เขียนทับ `summary` ไม่ได้ (นั่นคือสรุปต้นฉบับของการประชุม)

ที่ตัดสินใจ: สรุปภาษาอื่นอยู่ช่องเดียวกับคำแปล (`translations[lang]`) ผู้ใช้จึงเลือกดูจาก
`#d-lang` ตัวเดิมที่มีอยู่แล้ว ไม่ต้องเพิ่ม schema หรือหน้าจอใหม่ ต่างจากปุ่ม "แปล" ตรงที่
อันนี้สรุป**ตรงจากบทถอดเสียง** ไม่ใช่แปลจากสรุปไทย (ซึ่งจะบีบอัดสองชั้นและเพี้ยนกว่า)

เส้นที่ห้ามหลุด และเป็นบทเรียนจาก BUG-048: **ภาษาปลายทางอ่านจาก spec เท่านั้น ห้ามอ่านจาก
ผลที่ worker ส่งกลับ** ไม่งั้นใครถือ WORKER_TOKEN ก็ยัด key อะไรก็ได้ลง translations ของคนอื่น
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from _harness import CloudCase, jobs, new_mid

from meeting_ai import summarizer

APP_JS = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static" / "app.js"


class TestTheEndpointAcceptsALanguage(CloudCase):

    def _post(self, mid: str, body=None):
        if body is None:
            return self.post_json(f"/api/meetings/{mid}/resummarize",
                                  cookies={"mai_session": self.tokA})
        return self.post_json(f"/api/meetings/{mid}/resummarize", body,
                              cookies={"mai_session": self.tokA})

    def test_no_language_behaves_exactly_as_before(self):
        status, body, _ = self._post(self.M1)
        self.assertEqual(status, 202)
        self.assertEqual(body["id"], self.M1, "ไทย = job id เท่ากับ id การประชุมเหมือนเดิม")
        self.assertIsNone(self.store.jobs[self.M1]["spec"].get("summary_lang"))

    def test_thai_is_the_same_path_as_no_language(self):
        status, body, _ = self._post(self.M1, {"lang": "th"})
        self.assertEqual(status, 202)
        self.assertEqual(body["id"], self.M1)

    def test_another_language_gets_its_own_job_id(self):
        # ไม่งั้นสั่งสรุปไทยกับอังกฤษพร้อมกันจะทับกันเอง (job id = id การประชุม)
        status, body, _ = self._post(self.M1, {"lang": "en"})
        self.assertEqual(status, 202)
        self.assertEqual(body["id"], f"{self.M1}.sum.en")
        self.assertEqual(self.store.jobs[body["id"]]["spec"]["summary_lang"], "en")

    def test_the_job_still_points_at_its_meeting(self):
        _, body, _ = self._post(self.M1, {"lang": "en"})
        self.assertEqual(jobs._meeting_of(self.store.job_get(body["id"])), self.M1)

    def test_an_unknown_language_is_rejected(self):
        # lang ไหลไปเป็นส่วนหนึ่งของ job id และเข้า prompt ของ LLM
        status, body, _ = self._post(self.M1, {"lang": "../../etc"})
        self.assertEqual(status, 400)
        self.assertIn("lang", str(body))

    def test_every_offered_language_is_accepted(self):
        for code in summarizer.LANGUAGE_NAMES:
            with self.subTest(code=code):
                status, _, _ = self._post(self.M1, {"lang": code})
                self.assertEqual(status, 202)

    def test_a_meeting_that_is_not_ours_queues_nothing(self):
        # ตอบ 403 ไม่ใช่ 404 โดยตั้งใจ — 404 จะบอกคนนอกว่า id นี้มีอยู่จริงหรือไม่
        # ที่ต้องยืนยันคือ "ไม่มีงานถูกสร้าง" ไม่ใช่ตัวเลขสถานะ
        before = set(self.store.jobs)
        status, _, _ = self._post(new_mid(), {"lang": "en"})
        self.assertIn(status, (403, 404))
        self.assertEqual(set(self.store.jobs), before)

    def test_the_spec_reaches_the_worker(self):
        _, body, _ = self._post(self.M1, {"lang": "ja"})
        spec = jobs.build_spec(body["id"])
        self.assertEqual(spec["summary_lang"], "ja")
        self.assertEqual(spec["kind"], "summarize")


class TestWhereTheResultLands(CloudCase):

    def _run(self, lang: str | None, summary: str = "## Summary\n\n- ok"):
        body = {"lang": lang} if lang else None
        args = ([body] if body else []) + [{"mai_session": self.tokA}]
        status, job, _ = (self.post_json(f"/api/meetings/{self.M1}/resummarize", body,
                                         cookies={"mai_session": self.tokA})
                          if body else
                          self.post_json(f"/api/meetings/{self.M1}/resummarize",
                                         cookies={"mai_session": self.tokA}))
        self.assertEqual(status, 202)
        jobs.apply_result(job["id"], {"summary": summary})
        return self.store.meetings[self.M1]

    def test_thai_still_overwrites_the_original_summary(self):
        meeting = self._run(None, "## สรุปไทย")
        self.assertEqual(meeting["summary"], "## สรุปไทย")
        self.assertFalse(meeting.get("translations"))

    def test_another_language_goes_to_translations_not_over_the_original(self):
        self.store.meetings[self.M1]["summary"] = "## สรุปไทยเดิม"
        meeting = self._run("en", "## English summary")
        self.assertEqual(meeting["summary"], "## สรุปไทยเดิม", "สรุปต้นฉบับห้ามถูกทับ")
        self.assertEqual(meeting["translations"]["en"], "## English summary")

    def test_an_empty_result_is_an_error_not_a_blank_translation(self):
        status, job, _ = self.post_json(f"/api/meetings/{self.M1}/resummarize", {"lang": "en"},
                                        cookies={"mai_session": self.tokA})
        self.assertEqual(status, 202)
        with self.assertRaises(RuntimeError):
            jobs.apply_result(job["id"], {"summary": "   "})

    def test_the_language_comes_from_the_spec_not_the_worker_result(self):
        # BUG-048 ซ้ำรอย: ใครถือ WORKER_TOKEN ยัด key ลง translations ของคนอื่นไม่ได้
        status, job, _ = self.post_json(f"/api/meetings/{self.M1}/resummarize", {"lang": "en"},
                                        cookies={"mai_session": self.tokA})
        self.assertEqual(status, 202)
        jobs.apply_result(job["id"], {"summary": "## hi", "lang": "ko", "summary_lang": "ko"})
        translations = self.store.meetings[self.M1]["translations"]
        self.assertIn("en", translations)
        self.assertNotIn("ko", translations)


class TestTheFrontEndUsesIt(unittest.TestCase):
    """ฟีเจอร์ที่หน้าเว็บเรียกไม่ถึงก็ไม่มีใครได้ใช้."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = APP_JS.read_text(encoding="utf-8")

    def test_it_sends_the_selected_language(self):
        block = self.src[self.src.index("#d-resummarize').onclick"):]
        self.assertIn("jsonPost({ lang })", block[:600])

    def test_it_still_sends_a_plain_post_for_the_original(self):
        block = self.src[self.src.index("#d-resummarize').onclick"):]
        self.assertIn("'orig'", block[:600])

    def test_the_button_says_what_it_will_do(self):
        # ปุ่มที่ยังเขียน "สรุปใหม่ด้วย AI" ตอนเลือกภาษาอังกฤษอยู่ ทำให้คนนึกว่าจะไปทับสรุปต้นฉบับ
        self.assertIn("สรุปใหม่เป็น", self.src)

    def test_the_chosen_language_survives_a_reload(self):
        # งานเสร็จแล้วหน้าโหลดซ้ำ ถ้าตัวเลือกเด้งกลับ "ต้นฉบับ" ผู้ใช้จะเห็นสรุปไทยแล้วนึกว่าไม่มีอะไรเกิด
        block = self.src[self.src.index("function renderLangSelect"):]
        block = block[:block.index("\n}")]
        self.assertIn("const keep", block)
        self.assertIn("$('#d-lang').value = keep", block)

    def test_the_progress_badge_knows_the_new_job_id_shape(self):
        block = self.src[self.src.index("function jobForMeeting"):]
        self.assertIn(".sum.", block[:400])

    def test_the_comment_above_it_lists_all_three_shapes(self):
        # คอมเมนต์นั้นเป็นเอกสารเดียวที่บอกรูปแบบ job id ให้คนอ่านฝั่งหน้าเว็บ
        head = self.src[:self.src.index("function jobForMeeting")]
        self.assertIn("${mid}.sum.<lang>", head[-500:])


class TestBacklogRowIsClosed(unittest.TestCase):

    def test_the_row_is_marked_done(self):
        backlog = (Path(__file__).resolve().parents[1]
                   / "docs" / "product" / "BACKLOG.md").read_text(encoding="utf-8")
        row = next(line for line in backlog.split("\n") if line.startswith("| 9b |"))
        self.assertTrue(re.match(r"\| 9b \| ✅", row), row[:60])


if __name__ == "__main__":
    unittest.main()
