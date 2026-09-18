"""BACKLOG #53 — PRD ของ Action Items ที่ติ๊กได้.

ตั๋วสั่งให้ออกแบบก่อนสร้าง (`architect → backend-dev → web-dev`) ไฟล์เทสต์นี้จึงไม่ได้ทดสอบ
ฟีเจอร์ — ยังไม่มีฟีเจอร์ — แต่ทดสอบว่า **ข้อเท็จจริงที่ PRD อ้างอิงยังเป็นจริงอยู่**

เหตุผล: PRD ที่อ้างโครงตารางของ prompt, รายชื่อทางที่สรุปถูกเขียนทับ, หรือชื่อฟังก์ชันใน
โค้ด จะกลายเป็นเอกสารที่พาคนทำผิดทางทันทีที่โค้ดขยับ และไม่มีใครรู้ เพราะเอกสารไม่มีเทสต์
เทสต์ชุดนี้ผูก PRD ไว้กับโค้ดจริง — โค้ดเปลี่ยนเมื่อไร PRD ต้องถูกแก้ตาม ไม่ใช่ค้างไว้เงียบ ๆ
"""

from __future__ import annotations

import unittest
from pathlib import Path

from meeting_ai import summarizer

ROOT = Path(__file__).resolve().parents[1]
PRD = ROOT / "docs" / "product" / "PRD-structured-action-items.md"
TABLE_HEADER = "| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |"


class TestThePrdExists(unittest.TestCase):

    def test_the_file_is_there(self):
        self.assertTrue(PRD.exists(), f"ไม่พบ {PRD}")

    def test_the_backlog_row_points_at_it(self):
        backlog = (ROOT / "docs" / "product" / "BACKLOG.md").read_text(encoding="utf-8")
        row = next(ln for ln in backlog.split("\n") if ln.startswith("| 53 |"))
        self.assertIn("PRD-structured-action-items.md", row)


class TestThePrdMatchesTheCode(unittest.TestCase):
    """ทุกข้อเท็จจริงที่ PRD อ้าง ต้องยังเป็นจริงในโค้ดวันนี้."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_every_template_really_emits_the_table(self):
        # PRD อ้างว่า "ตรวจครบทั้งห้าแล้ว" — ถ้าเทมเพลตใหม่ไม่มีตาราง ข้ออ้างนั้นผิดทันที
        for name, tpl in summarizer.TEMPLATES.items():
            with self.subTest(template=name):
                self.assertIn(TABLE_HEADER, tpl["body"])

    def test_the_prd_quotes_the_header_exactly(self):
        self.assertIn(TABLE_HEADER, self.text)

    def test_the_prd_lists_every_template_by_name(self):
        for name in summarizer.TEMPLATES:
            with self.subTest(template=name):
                self.assertIn(name, self.text)

    def test_the_placeholder_row_still_looks_like_that(self):
        # AC-1.1 บอกว่าต้องไม่นับแถวตัวอย่าง `| ... | ... | ... |`
        self.assertIn("| ... | ... | ... |", summarizer.TEMPLATES["general"]["body"])
        self.assertIn("| ... | ... | ... |", self.text)

    def test_the_unassigned_wording_is_still_what_the_prompt_uses(self):
        # AC-1.3 อ้างถึงคำนี้ตรง ๆ
        self.assertIn("ไม่ได้ระบุ", summarizer.TEMPLATES["general"]["body"])
        self.assertIn("ไม่ได้ระบุ", self.text)

    def test_all_four_write_paths_still_exist(self):
        # หัวใจของ PRD คือ "สรุปเปลี่ยนได้สี่ทาง" — ทางไหนหายหรือเพิ่ม ต้องกลับมาแก้ PRD
        from meeting_ai.web import jobs, store

        for fn in ("create", "set_summary", "set_translation", "update"):
            with self.subTest(fn=fn):
                self.assertTrue(callable(getattr(store, fn)), f"store.{fn} หายไป")
                self.assertIn(f"store.{fn}(", self.text.replace("`", ""))
        self.assertTrue(callable(jobs.apply_result))

    def test_both_backends_expose_the_same_write_paths(self):
        # PRD ข้อ 5 อ้างว่าสองแบ็กเอนด์มีชื่อฟังก์ชันชุดเดียวกัน
        from meeting_ai.web import pgstore, store

        for fn in ("create", "set_summary", "set_translation", "update"):
            with self.subTest(fn=fn):
                self.assertTrue(callable(getattr(pgstore, fn)))
                self.assertTrue(callable(getattr(store, fn)))

    def test_the_permission_helper_it_names_exists(self):
        from meeting_ai.web import server

        self.assertTrue(callable(server.Handler._may_write))
        self.assertIn("_may_write", self.text)

    def test_the_export_module_it_names_exists(self):
        from meeting_ai.web import exports

        self.assertTrue(hasattr(exports, "FORMATS"))
        self.assertIn("web/exports.py", self.text)


class TestThePrdIsDecidable(unittest.TestCase):
    """PRD ที่ไม่มีเกณฑ์ยอมรับที่ทดสอบได้ คือความเห็น ไม่ใช่สเปก."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PRD.read_text(encoding="utf-8")

    def test_it_has_acceptance_criteria(self):
        import re

        acs = re.findall(r"\*\*AC-\d+\.\d+\*\*", self.text)
        self.assertGreaterEqual(len(acs), 15, f"มีเกณฑ์ยอมรับแค่ {len(acs)} ข้อ")

    def test_every_user_story_has_at_least_one_criterion(self):
        import re

        stories = set(re.findall(r"### (US-\d+)", self.text))
        self.assertTrue(stories)
        for story in stories:
            number = story.split("-")[1]
            with self.subTest(story=story):
                self.assertRegex(self.text, rf"\*\*AC-{number}\.\d+\*\*")

    def test_it_says_what_it_will_not_build(self):
        # ขอบเขตที่ไม่เขียนไว้ คือขอบเขตที่จะบานปลาย
        # หัวข้อจริงคือ "สิ่งที่ *ไม่* ใช่ปัญหา (ขอบเขตที่ไม่ทำ)" — เครื่องหมายเน้นคั่นคำอยู่
        self.assertIn("ขอบเขตที่ไม่ทำ", self.text)
        self.assertIn("ไม่ทำระบบจัดการงานเต็มรูปแบบ", self.text)

    def test_it_records_the_options_it_rejected(self):
        for marker in ("ทางเลือกที่พิจารณา", "ทำไมไม่เอา"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)

    def test_it_marks_the_open_decisions_for_the_owner(self):
        # ของที่เอเจนต์ตัดสินใจแทนเจ้าของไม่ได้ ต้องหาเจอในไฟล์ ไม่ใช่ซ่อนอยู่ในย่อหน้า
        self.assertIn("ต้องให้เจ้าของตัดสินใจ", self.text)
        self.assertGreaterEqual(self.text.count("❓"), 3)

    def test_it_names_the_worst_failure_mode(self):
        # ฟีเจอร์นี้ถ้าทำพลาด จะแย่กว่าไม่ทำ — PRD ต้องพูดเรื่องนี้ออกมาตรง ๆ
        self.assertIn("แย่กว่าไม่มีฟีเจอร์", self.text)

    def test_it_says_how_to_tell_whether_it_worked(self):
        self.assertIn("วัดผล", self.text)


if __name__ == "__main__":
    unittest.main()
