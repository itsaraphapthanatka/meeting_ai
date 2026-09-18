"""BACKLOG #30 — LICENSE และ CLAUDE.md.

**LICENSE** — ที่เก็บนี้เปิดสาธารณะบน GitHub โดยไม่มีไฟล์สัญญาอนุญาตเลย ซึ่งตามกฎหมายแปลว่า
"สงวนสิทธิ์ทั้งหมด" อยู่แล้ว แต่คนทั่วไปเห็น repo สาธารณะแล้วมักเข้าใจว่าใช้ได้ฟรี การไม่มีไฟล์
จึงเป็นความกำกวมที่เสียเปรียบเจ้าของ **เจ้าของเลือกแล้วว่าไม่ให้ใครเอาไปใช้** ไฟล์นี้จึงเขียน
ข้อสงวนสิทธิ์ให้ชัด ไม่ใช่สัญญาอนุญาตแบบโอเพนซอร์ส

**CLAUDE.md** — โหลดเข้าบริบททุกครั้ง จึงต้องสั้นและเป็น "กฎ" ไม่ใช่สำเนาของ
PROJECT-CONTEXT.md ซึ่งยาวกว่าและเป็น "แผนที่"

เทสต์ชุดนี้คุมสิ่งที่พังเงียบได้: ไฟล์หาย, มีคนเผลอเติมข้อความแบบโอเพนซอร์ส, CLAUDE.md บวมจน
ไม่มีใครอ่าน, หรือกฎในนั้นชี้ไปยังไฟล์/คำสั่งที่ไม่มีอยู่จริง
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE = ROOT / "LICENSE"
CLAUDE = ROOT / "CLAUDE.md"


class TestLicenseSaysWhatTheOwnerChose(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = LICENSE.read_text(encoding="utf-8")

    def test_the_file_exists(self):
        self.assertTrue(LICENSE.exists())

    def test_it_reserves_all_rights(self):
        self.assertIn("All rights reserved", self.text)
        self.assertIn("สงวนลิขสิทธิ์ทั้งหมด", self.text)

    def test_it_is_not_an_open_source_licence(self):
        # เจ้าของเลือกว่า "ไม่ให้ใครเอาไปใช้" — เผลอวางข้อความ MIT/Apache ลงไปคือยกสิทธิ์ให้ฟรี
        forbidden = ["Permission is hereby granted, free of charge",
                     "Licensed under the Apache License",
                     "GNU GENERAL PUBLIC LICENSE",
                     "redistribute it and/or modify"]
        for phrase in forbidden:
            with self.subTest(phrase=phrase[:30]):
                self.assertNotIn(phrase, self.text)

    def test_it_says_public_on_github_is_not_a_licence(self):
        # ความเข้าใจผิดที่พบบ่อยที่สุด และเป็นเหตุผลหลักที่ต้องมีไฟล์นี้
        self.assertIn("does NOT grant any licence", self.text)
        self.assertIn("ไม่ได้", self.text)

    def test_it_is_in_both_languages(self):
        self.assertIn("ภาษาไทย", self.text)
        self.assertIn("English", self.text)

    def test_it_says_how_to_ask_for_permission(self):
        # ข้อสงวนสิทธิ์ที่ไม่บอกทางติดต่อ = ปิดประตูโดยไม่ตั้งใจ
        self.assertIn("github.com/itsaraphapthanatka", self.text)

    def test_it_does_not_claim_third_party_work(self):
        # whisper.cpp / Playwright / ffmpeg / โมเดล ggml มีสัญญาอนุญาตของตัวเอง
        self.assertIn("Third-party", self.text)
        self.assertIn("whisper.cpp", self.text)

    def test_there_is_no_open_source_licence_file_elsewhere(self):
        strays = [p.name for p in ROOT.glob("LICENSE*") if p.name != "LICENSE"]
        self.assertEqual(strays, [])


class TestClaudeMdIsUsable(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CLAUDE.read_text(encoding="utf-8")

    def test_the_file_exists(self):
        self.assertTrue(CLAUDE.exists())

    def test_it_stays_short_enough_to_be_read_every_time(self):
        # โหลดเข้าบริบททุกครั้ง — ยาวเกินไปคือเสียโควตาและไม่มีใครอ่านจบ
        lines = len(self.text.split("\n"))
        self.assertLess(lines, 120, f"ยาว {lines} บรรทัด — ย้ายรายละเอียดไป PROJECT-CONTEXT")

    def test_it_points_at_the_deeper_documents_instead_of_copying_them(self):
        for target in ("docs/PROJECT-CONTEXT.md", "docs/product/BACKLOG.md"):
            with self.subTest(target=target):
                self.assertIn(target, self.text)

    def test_every_file_it_links_to_exists(self):
        # กฎที่ชี้ไปไฟล์ที่ไม่มีอยู่จริง แย่กว่าไม่มีกฎ
        for link in re.findall(r"\]\(([^)#]+)\)", self.text):
            if link.startswith("http"):
                continue
            with self.subTest(link=link):
                self.assertTrue((ROOT / link).exists(), f"{link} ไม่มีอยู่จริง")

    def test_it_carries_the_rules_that_cost_us_the_most(self):
        # กฎที่มาจากเหตุการณ์จริงในโปรเจกต์นี้ ไม่ใช่คำแนะนำทั่วไป
        must_mention = [
            "stdlib",                    # ข้อจำกัดหลักของ meeting_ai/
            "sanitize",                  # ข้อมูลจาก worker เชื่อไม่ได้ (BUG-048)
            "valid_id",                  # path traversal
            "bot/profile/",              # session ที่ล็อกอิน Google ของเจ้าของ
            "cp874",                     # คอนโซลของเครื่องเจ้าของ
            "recordings/web/",           # ห้ามรันเซิร์ฟเวอร์ทดสอบจาก repo จริง
            "production",                # ห้ามทดสอบกับของจริง
        ]
        for token in must_mention:
            with self.subTest(token=token):
                self.assertIn(token, self.text)

    def test_it_states_the_licence_position(self):
        # ไฟล์นี้คือที่ที่เอเจนต์อ่านก่อน — ถ้าไม่บอกไว้ อาจมีคนเติม LICENSE แบบโอเพนซอร์สให้
        self.assertIn("สงวนลิขสิทธิ์ทั้งหมด", self.text)
        self.assertIn("LICENSE", self.text)

    def test_the_commands_it_lists_are_real(self):
        for command in ("python -m compileall", "unittest discover -s tests", "./mai web"):
            with self.subTest(command=command):
                self.assertIn(command, self.text)

    def test_it_does_not_duplicate_the_architecture_map(self):
        # PROJECT-CONTEXT มีอยู่แล้ว และมันเคยบวมจนมีย่อหน้าซ้ำสามชุดที่ขัดกันเอง (ดู #30b)
        # CLAUDE.md ต้องไม่เดินทางเดียวกัน
        self.assertNotIn("_route → _api", self.text)
        self.assertLess(self.text.count("`/api/"), 6)


if __name__ == "__main__":
    unittest.main()
