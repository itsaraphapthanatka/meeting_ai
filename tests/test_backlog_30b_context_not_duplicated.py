"""BACKLOG #30b — PROJECT-CONTEXT.md มีย่อหน้าซ้ำที่ขัดกันเอง.

ไฟล์นี้คือ "แหล่งความจริงเดียว" ที่เอเจนต์ทุกตัวอ่านก่อนแตะโค้ด แต่มันโตด้วยการ **ต่อท้าย**
แทนการ **แทนที่** จนมีย่อหน้าซ้ำห้าจุด:

    Storage        3 ชุด    Auth pattern   3 ชุด
    P1             2 ชุด    Baseline       3 ชุด

และแต่ละชุดเป็นคนละรุ่นที่ **ขัดกันเอง** — ชุดหนึ่งบอกว่า P0 fix ยัง `uncommitted` อีกชุดบอกว่า
merged ใน `c0b1115` แล้ว เอเจนต์ที่อ่านเจอชุดแรกจะทำงานบนข้อมูลที่ผิดโดยไม่มีอะไรเตือน

**การยุบไม่ใช่ "เอาชุดล่าสุด"** — วัดแล้วพบว่าแต่ละชุดมีประโยคที่อีกชุดไม่มี เช่น รายชื่อตาราง
ที่มี `rate_limits` อยู่แค่ชุดที่สอง ส่วนรายละเอียดไฟล์ล็อกข้ามโพรเซส (BUG-056) อยู่แค่ชุดแรก
และเรื่อง `allow_remote` (BUG-045) อยู่แค่ชุดที่สาม จึงต้องรวมแบบ union แล้วยืนยันทุกข้อ
กับโค้ดจริงก่อนเก็บไว้

เทสต์ไฟล์นี้กันไม่ให้มันโตแบบเดิมอีก และกันไม่ให้ข้อเท็จจริงที่กู้มาได้หายไปเงียบ ๆ
"""

from __future__ import annotations

import unittest
from pathlib import Path

CONTEXT = Path(__file__).resolve().parents[1] / "docs" / "PROJECT-CONTEXT.md"
MIN_LEN = 40        # บรรทัดสั้นกว่านี้ (หัวข้อ, โค้ดบรรทัดเดียว) ซ้ำกันได้ตามปกติ


class TestNoDuplicatedParagraphs(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.lines = CONTEXT.read_text(encoding="utf-8").split("\n")

    def test_no_paragraph_starts_the_same_way_twice(self):
        # ลายเซ็นของการต่อท้ายแทนการแทนที่: ย่อหน้าใหม่ขึ้นต้นเหมือนของเดิมเป๊ะ ๆ
        seen: dict[str, list[int]] = {}
        in_code = False
        for i, line in enumerate(self.lines, 1):
            if line.lstrip().startswith("```"):
                in_code = not in_code
                continue
            # ข้ามในบล็อกโค้ด: คำสั่งคนละอันขึ้นต้นเหมือนกันได้ตามปกติ เช่น
            # `MEETING_AI_CLOUD=1 … ./mai db-init` กับ `… ./mai web --cloud`
            if in_code:
                continue
            head = line.strip()[:60]
            if len(head) < MIN_LEN or head.startswith(("#", "|", "-", "*", ">")):
                continue
            seen.setdefault(head, []).append(i)
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        self.assertEqual(dupes, {}, f"ย่อหน้าซ้ำ: { {k[:40]: v for k, v in dupes.items()} }")

    def test_the_headline_sections_appear_once(self):
        for label in ("**Storage**", "**Auth pattern**", "**Routes**", "**Jobs**", "**Bot**"):
            with self.subTest(label=label):
                count = sum(1 for ln in self.lines if ln.lstrip().startswith(label))
                self.assertEqual(count, 1, f"{label} โผล่ {count} ครั้ง")


class TestTheMergedFactsSurvived(unittest.TestCase):
    """ของที่เคยมีอยู่ใน "ชุดใดชุดหนึ่ง" เท่านั้น — ยุบผิดวิธีแล้วหายไปได้ง่ายมาก."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CONTEXT.read_text(encoding="utf-8")

    def test_facts_that_lived_in_only_one_copy(self):
        for fact, where in [
            ("rate_limits", "รายชื่อตาราง — มีแค่ชุด Storage ที่สอง"),
            (".store.lock", "BUG-056 — มีแค่ชุด Storage แรก"),
            ("msvcrt.locking", "BUG-056"),
            ("allow_remote=cloud", "BUG-045 — มีแค่ชุด Storage ที่สาม"),
            ("MEETING_AI_REMOTE_BLOBS", "BUG-045"),
            ("claim_first_admin", "มีแค่ชุด Auth แรก"),
            ("signup_bootstrap", "มีแค่ชุด Auth แรก"),
            ("_ip_key()", "BUG-010 — มีแค่ชุด Auth ที่สอง"),
            ("X-Vercel-Forwarded-For", "BUG-010"),
            ("TestPgstoreJobActiveAgainstRealDb", "บทเรียนจากชุด Baseline แรก"),
        ]:
            with self.subTest(fact=fact):
                self.assertIn(fact, self.text, where)

    def test_the_table_list_matches_the_real_schema(self):
        # รายชื่อตารางที่ไม่ตรงกับ schema.sql คือสิ่งที่ทำให้เกิดชุดซ้ำตั้งแต่แรก
        schema = (CONTEXT.parent.parent / "meeting_ai" / "web" / "schema.sql")
        import re

        real = set(re.findall(r"create table if not exists meeting_ai\.(\w+)",
                              schema.read_text(encoding="utf-8")))
        for table in real:
            with self.subTest(table=table):
                self.assertIn(table, self.text)


class TestNoContradictoryStatus(unittest.TestCase):
    """สองชุดที่บอกสถานะต่างกันคืออาการที่ทำให้ตั๋วนี้เกิด."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = CONTEXT.read_text(encoding="utf-8")

    def test_nothing_still_claims_to_be_uncommitted(self):
        # ทุกอย่างที่เคยเขียนว่า uncommitted ตรวจแล้วว่าอยู่ใน main จริง (0710d5b, c0b1115)
        self.assertNotIn("uncommitted", self.text)

    def test_no_stale_test_counts(self):
        # เคยมีสแนปช็อตจำนวนเทสต์สามรุ่นซ้อนกัน (64 / 76 / 81) ซึ่งล้าสมัยทั้งหมด
        for count in ("64 tests OK", "68 tests OK", "73 tests OK", "76 tests OK", "81 tests OK"):
            with self.subTest(count=count):
                self.assertNotIn(count, self.text)

    def test_it_points_at_the_backlog_for_current_status(self):
        # สถานะของงานเปลี่ยนทุกวัน เอกสารบริบทไม่ควรพยายามเป็นเจ้าของข้อมูลนั้น
        self.assertIn("product/BACKLOG.md", self.text)


if __name__ == "__main__":
    unittest.main()
