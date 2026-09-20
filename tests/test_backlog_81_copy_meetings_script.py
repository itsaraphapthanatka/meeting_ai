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
        # ทางเขียนย้ายไปอยู่ใน `_insert()` ตอนแก้บั๊ก jsonb (#81b) — ด่านยังต้องมาก่อน
        # เวอร์ชันแรกหาจาก `index(..., i)` คือหาเฉพาะหลังด่าน มุตันต์ที่แอบเพิ่มทางเขียน
        # ไว้ **ก่อน** ด่านจึงรอดไปได้ ต้องดูครั้งแรกสุดในไฟล์ และต้องมีที่เดียว
        self.assertEqual(SRC.count("_insert(dst, cols"), 1, "ทางเขียนต้องมีทางเดียว")
        self.assertLess(SRC.index("if not args.apply:"), SRC.index("_insert(dst, cols"),
                        "ทางเขียนต้องอยู่หลังด่าน --apply เสมอ")

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


class TestTheWrongDatabaseGuard(unittest.TestCase):
    """เกิดขึ้นจริงมาแล้ว 2026-09-20 — รัน `db-init` ใส่ฐานผิดไปสองรอบ.

    ฐานเก่ากับ production ชื่อเหมือนกันทั้งคู่ (`neondb`) จึงดูจากชื่อไม่ออก ต้องนับแถวเอา
    ลองจริงแล้ว: สั่ง `--expect-dst-meetings 8` กับฐานที่มี 13 → พิมพ์ว่าหยุดก่อน exit 1
    ส่วน `--expect-dst-meetings 13` → ผ่านแล้วไปต่อ
    """

    def test_the_flag_exists_and_takes_a_number(self):
        self.assertIn('"--expect-dst-meetings", type=int', SRC)

    def test_it_is_optional(self):
        # ไม่ควรบังคับ คนที่รู้อยู่แล้วว่าชี้ถูกไม่ต้องนับให้
        self.assertIn("if args.expect_dst_meetings is not None:", SRC)

    def test_a_mismatch_stops_the_run(self):
        i = SRC.index("if args.expect_dst_meetings is not None:")
        block = SRC[i:SRC.index("rows = src.execute", i)]
        self.assertIn("return 1", block, "ไม่ตรงแล้วต้องหยุด ไม่ใช่แค่เตือน")

    def test_the_check_runs_before_anything_else(self):
        # ล้มทั้งใน dry-run ด้วย ไม่งั้นคนอ่านรายงานของฐานผิดไปทั้งหน้าแล้วเชื่อ
        i = SRC.index("if args.expect_dst_meetings is not None:")
        self.assertLess(i, SRC.index("rows = src.execute"))
        self.assertLess(i, SRC.index("if not args.apply:"))

    def test_it_counts_rows_not_database_names(self):
        block = SRC[SRC.index("if args.expect_dst_meetings is not None:"):]
        block = block[:block.index("rows = src.execute")]
        self.assertIn("select count(*) from meeting_ai.meetings", block)


class TestTheWrongApplicationGuard(unittest.TestCase):
    """เจอจริง 2026-09-20 — เจ้าของหยิบ connection string จากโปรเจกต์ Neon ผิดตัว.

    ได้ฐานของแอปอื่นทั้งใบ (45 ตาราง มี `neon_auth.*`, `public.AuditLog`, `public.Branding`)
    สคริปต์เวอร์ชันแรกพังด้วย traceback ของ psycopg (`UndefinedTable`) ซึ่งอ่านไม่ออกว่า
    เกิดอะไรขึ้น · ตอนนี้บอกตรง ๆ พร้อมบอกว่าเจออะไรในฐานนั้นแทน แล้ว exit 1

    ลองจริงกับฐานนั้น: "ปลายทางไม่มีตาราง meeting_ai.meetings · สิ่งที่เจอในฐานนั้นแทน:
    public (36 ตาราง), neon_auth (9 ตาราง)"
    """

    def test_it_checks_the_table_exists_before_using_it(self):
        self.assertIn("def _looks_like_ours(conn)", SRC)
        i = SRC.index("if not _looks_like_ours(dst):")
        self.assertLess(i, SRC.index("if args.expect_dst_meetings is not None:"),
                        "ต้องรู้ก่อนว่าเป็นฐานของแอปนี้ไหม ก่อนจะไปนับแถว")

    def test_it_stops_instead_of_crashing(self):
        block = SRC[SRC.index("if not _looks_like_ours(dst):"):]
        block = block[:block.index("ด่านที่สอง")]
        self.assertIn("return 1", block)

    def test_it_says_what_it_found_instead(self):
        # "ไม่มีตาราง" อย่างเดียวไม่พอ ต้องช่วยให้คนรู้ว่าไปโดนฐานของอะไร
        self.assertIn("def _other_app_hint(conn)", SRC)
        self.assertIn("_other_app_hint(dst)", SRC)

    def test_it_offers_the_legitimate_path_too(self):
        # ฐานใหม่ของ meeting_ai เองก็ยังไม่มีตาราง — ต้องบอกว่าให้รัน db-init
        self.assertIn("db-init", SRC)


class TestSecretsDoNotLeak(unittest.TestCase):

    def test_the_urls_come_from_the_environment_not_argv(self):
        # argument จะไปโผล่ใน shell history และใน ps ของทั้งเครื่อง
        self.assertIn('os.environ.get("SRC_DATABASE_URL")', SRC)
        self.assertIn('os.environ.get("DST_DATABASE_URL")', SRC)

    def test_the_source_defaults_to_the_local_env_file(self):
        """เจ้าของเปิด `.env` อ่านค่าเองไม่สะดวก และยิ่งคัดลอกไปมายิ่งเสี่ยงหลุด.

        ต้นทางปกติคือฐานที่ `.env` ของเครื่องนั้นชี้อยู่แล้ว จึงให้ default ไปเลย
        เหลือให้กรอกแค่ปลายทาง
        """
        self.assertIn('os.environ.get("SRC_DATABASE_URL") or os.environ.get("DATABASE_URL")',
                      SRC)

    def test_the_destination_never_defaults(self):
        # ปลายทางคือที่ที่จะถูกเขียน ห้ามเดาเด็ดขาด
        i = SRC.index('dst_url = os.environ.get("DST_DATABASE_URL")')
        line_end = SRC.index(chr(10), i)
        self.assertNotIn(" or os.environ", SRC[i:line_end])
        self.assertIn("if not dst_url:", SRC)
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
