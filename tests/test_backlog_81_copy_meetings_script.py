"""BACKLOG #81 — เครื่องมือย้ายการประชุมข้ามฐาน (`scripts/copy_meetings.py`).

ต้องมีเพราะ `.env` ของเครื่องเจ้าของชี้ไปฐาน Neon เก่าที่มีการประชุม 13 รายการ ส่วน
production ใช้อีกฐานหนึ่ง · **เอเจนต์ทำแทนไม่ได้**: ทุกตัวแปรของโปรเจกต์บน Vercel ถูกตั้งเป็น
`sensitive` ซึ่ง Vercel ไม่ยอมคืนค่ากลับมาเลยแม้แต่ทาง API ด้วย `VERCEL_TOKEN` (ตรวจแล้ว
2026-09-20 ทั้ง 13 ตัว) สคริปต์จึงรับสองค่าจาก environment ของคนรันเอง

เทสต์ชุดนี้ตรวจ **กติกาที่ทำให้มันไม่พังของจริง** ไม่ใช่ว่ามันรันได้:

* ค่าเริ่มต้นต้องเป็น dry-run — เผลอรันแล้วต้องไม่เขียนอะไร
* `owner_id` ต้องไม่ถูกคัดลอกมา — id ผู้ใช้เป็นคนละชุดกันสองฐาน คัดลอกมาแล้วจะชน FK
  หรือกลายเป็นแถวไม่มีเจ้าของ ซึ่งตั้งแต่ BACKLOG #42 แปลว่า **ไม่มีใครเปิดได้เลย**
* ต้องไม่เขียนทับของที่ปลายทางมีอยู่แล้ว
* คอลัมน์ที่ปลายทางยังไม่มีต้องถูกข้าม ไม่ใช่ทำให้ทั้งงานล้ม

ลองจริงแล้วกับ Postgres จริง (ต้นทาง = ปลายทาง = ฐานเก่า) → รายงาน "13 แถว · ปลายทาง
มีอยู่แล้ว 13 · จะย้าย 0" แล้วหยุดเพราะไม่ได้ระบุ `--owner` โดยไม่เขียนอะไรเลย
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "scripts"
       / "copy_meetings.py").read_text(encoding="utf-8")
TREE = ast.parse(SRC)


def const(name: str):
    for node in TREE.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
            return ast.literal_eval(node.value)
    raise AssertionError(f"ไม่พบค่าคงที่ {name}")


class TestItCannotWriteByAccident(unittest.TestCase):

    def test_apply_is_opt_in(self):
        self.assertIn('"--apply", action="store_true"', SRC)

    def test_nothing_is_written_without_apply(self):
        i = SRC.index("if not args.apply:")
        j = SRC.index("dst.execute(sql", i)
        self.assertLess(i, j, "ทางเขียนต้องอยู่หลังด่าน --apply เสมอ")

    def test_it_stops_when_no_owner_is_given(self):
        i = SRC.index("if not args.owner:")
        self.assertLess(i, SRC.index("if not args.apply:"),
                        "ต้องหยุดก่อนถึงด่าน apply ถ้ายังไม่รู้ว่าใครเป็นเจ้าของ")


class TestTheOwnerIsNeverCopied(unittest.TestCase):
    """id ผู้ใช้เป็นคนละชุดกันสองฐาน — นี่คือจุดที่พังเงียบที่สุดถ้าทำผิด."""

    def test_owner_id_is_not_in_the_copied_columns(self):
        self.assertNotIn("owner_id", const("BASE_COLS"))
        self.assertNotIn("owner_id", const("OPTIONAL_COLS"))

    def test_the_owner_comes_from_an_email_lookup_at_the_destination(self):
        self.assertIn("next((i for i, e in _users(dst) if e == args.owner)", SRC)

    def test_an_unknown_email_is_refused(self):
        self.assertIn("ไม่พบผู้ใช้", SRC)


class TestItDoesNotClobberTheDestination(unittest.TestCase):

    def test_existing_ids_are_skipped_before_insert(self):
        self.assertIn("todo = [r for r in rows if r[0] not in have]", SRC)

    def test_the_insert_is_also_guarded_in_sql(self):
        # ด่านในโค้ดกันเคสปกติ ส่วน on conflict กันเคสที่มีคนเขียนแทรกระหว่างทาง
        self.assertIn("on conflict (id) do nothing", SRC)

    def test_search_text_is_rebuilt_not_copied(self):
        self.assertNotIn("search_text", const("BASE_COLS"))
        self.assertIn("set\n                 search_text = concat_ws", SRC)


class TestItSurvivesAnUnmigratedDestination(unittest.TestCase):
    """ปลายทางอาจยังไม่ได้รัน db-init — คอลัมน์ใหม่ยังไม่มี."""

    def test_optional_columns_are_intersected_with_both_sides(self):
        self.assertIn("extra = [c for c in OPTIONAL_COLS if c in src_cols and c in dst_cols]",
                      SRC)

    def test_it_says_which_columns_it_skipped(self):
        self.assertIn("ปลายทางยังไม่มีคอลัมน์", SRC)

    def test_the_optional_list_is_the_three_we_added(self):
        self.assertEqual(set(const("OPTIONAL_COLS")), {"peaks", "action_items", "qa"})


class TestSecretsDoNotLeak(unittest.TestCase):

    def test_the_urls_come_from_the_environment_not_argv(self):
        # argument จะไปโผล่ใน shell history และใน ps ของทั้งเครื่อง
        self.assertIn('os.environ.get("SRC_DATABASE_URL")', SRC)
        self.assertIn('os.environ.get("DST_DATABASE_URL")', SRC)
        for bad in ("--src", "--dst", "--src-url", "--dst-url"):
            with self.subTest(bad=bad):
                self.assertNotIn(f'"{bad}"', SRC)

    def test_it_never_prints_a_url(self):
        # พิมพ์ได้แค่ชื่อฐาน ซึ่งมาจาก current_database() ไม่ใช่จาก connection string
        self.assertIn("select current_database()", SRC)
        self.assertNotIn("print(src_url", SRC)
        self.assertNotIn("print(dst_url", SRC)


class TestItWarnsAboutWhatItCannotDo(unittest.TestCase):

    def test_it_says_the_audio_is_not_moved(self):
        """ต้องเตือน **ตอนรัน** ไม่ใช่แค่เขียนไว้ใน docstring.

        เวอร์ชันแรกเช็คแค่ว่าสตริงอยู่ในไฟล์ — ซึ่งจริงอยู่แล้วเพราะ docstring เขียนไว้
        มุตันต์ที่ถอด `print` ออกจึงรอดไปได้ ทั้งที่คนรันจะไม่เห็นคำเตือนเลย
        """
        body = SRC[SRC.index("def main()"):]
        self.assertIn("ไฟล์เสียงไม่ได้ถูกย้าย", body)

    def test_it_lists_the_keys_to_copy(self):
        self.assertIn('keys = [r[cols.index("audio_key")]', SRC)


if __name__ == "__main__":
    unittest.main()
