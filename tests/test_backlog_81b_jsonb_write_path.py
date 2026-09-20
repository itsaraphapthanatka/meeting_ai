"""BACKLOG #81b — ทางเขียนของ `scripts/copy_meetings.py` กับคอลัมน์ jsonb.

**บั๊กที่หลุดออกไปถึงของจริง** (2026-09-20 ย้าย 13 การประชุมไป production ครั้งแรก):

    psycopg.ProgrammingError: cannot adapt type 'dict' using placeholder '%s'
    scripts/copy_meetings.py line 207, in main -> dst.execute(sql, (*r, owner))

psycopg แปลง jsonb **ขาออก** ให้เป็น dict/list ของ Python เอง แต่ **ขาเข้า** ไม่รู้ว่าจะ
แปลงกลับเป็นอะไร ต้องห่อด้วย `Jsonb()` — สองขาไม่สมมาตรกัน

ทำไมเทสต์ 27 ข้อของ #81 ไม่จับ: ทั้งชุดอ่านซอร์สอย่างเดียว ส่วนการ "ลองจริง" ที่บันทึกไว้ใน
ตั๋วใช้ต้นทาง = ปลายทาง = ฐานเดียวกัน จึงได้ "จะย้าย 0 แถว" — **ลูป insert ไม่เคยถูกรันเลย**
บทเรียน: เส้นทางที่เทสต์ไม่เคย *รัน* เท่ากับไม่มีเทสต์ ต่อให้มีเทสต์ล้อมรอบเยอะแค่ไหน

**และ `list` อันตรายกว่า `dict`** — วัดแล้วกับ dumper จริงของ psycopg: `dict` โยน error
ให้เห็น ส่วน `[1, 2]` มี dumper ของ array อยู่แล้วจึงกลายเป็น array literal `{1,2}` เงียบ ๆ
ซึ่งเป็นคนละชนิดกับ jsonb · `segments` กับ `speakers` เป็น list ทั้งคู่ การห่อจึงต้องยึด
**ชนิดของคอลัมน์ที่ปลายทาง** ไม่ใช่ยึดว่าค่าที่ได้มาเป็น dict หรือเปล่า

ชุดนี้วัด adaptation ของ psycopg จริง ๆ โดยไม่ต้องมีเซิร์ฟเวอร์ (`Transformer.get_dumper`)
บวกเทสต์ไป-กลับกับ Postgres จริงเมื่อมี `MAI_TEST_DATABASE_URL` (CI งาน ubuntu+postgres)
"""

from __future__ import annotations

import importlib.util
import os
import unittest
import uuid
from pathlib import Path

import psycopg
from psycopg.abc import PyFormat
from psycopg.adapt import Transformer
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]

# โหลดจากไฟล์จริงที่ shipped ไม่ใช่คัดลอกตรรกะมาไว้ในเทสต์ (scripts/ ไม่ใช่แพ็กเกจ)
_spec = importlib.util.spec_from_file_location(
    "copy_meetings", ROOT / "scripts" / "copy_meetings.py")
copy_meetings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(copy_meetings)


def dump(value):
    """ส่งค่าผ่าน dumper ตัวจริงของ psycopg — สิ่งที่จะถูกส่งขึ้นสายจริง ๆ."""
    return Transformer().get_dumper(value, PyFormat.AUTO).dump(value)


class TestWhyTheWrapperExists(unittest.TestCase):
    """ตรึงพฤติกรรมของ psycopg ที่ทำให้ต้องมี `_adapt()` — ถ้าวันหนึ่งมันเปลี่ยน เทสต์นี้จะบอก."""

    def test_a_bare_dict_is_refused(self):
        with self.assertRaises(psycopg.ProgrammingError) as cm:
            dump({"th": "สวัสดี"})
        self.assertIn("cannot adapt type 'dict'", str(cm.exception))

    def test_a_bare_list_is_worse_because_it_does_not_complain(self):
        # ไม่ error แต่ได้ array literal ของ Postgres ซึ่งไม่ใช่ JSON
        self.assertEqual(dump([1, 2]), b"{1,2}")

    def test_wrapping_gives_real_json(self):
        self.assertEqual(dump(Jsonb([1, 2])), b"[1, 2]")
        self.assertEqual(dump(Jsonb({"a": 1})), b'{"a": 1}')


class TestAdapt(unittest.TestCase):

    COLS = ("id", "title", "speakers", "segments", "translations")
    JSONB = {"speakers", "segments", "translations"}

    def _adapt(self, row):
        return copy_meetings._adapt(row, self.COLS, self.JSONB)

    def test_every_jsonb_column_is_wrapped_even_when_it_is_a_list(self):
        out = self._adapt(("m1", "ชื่อ", ["ก", "ข"], [{"text": "x"}], {"en": {}}))
        for i in (2, 3, 4):
            with self.subTest(col=self.COLS[i]):
                self.assertIsInstance(out[i], Jsonb)

    def test_the_wrapped_values_dump_as_json_not_as_an_array(self):
        out = self._adapt(("m1", "ชื่อ", ["ก", "ข"], [1, 2], {"en": {}}))
        self.assertEqual(dump(out[3]), b"[1, 2]")          # ไม่ใช่ b"{1,2}"
        self.assertEqual(dump(out[4]), b'{"en": {}}')

    def test_plain_columns_are_left_alone(self):
        out = self._adapt(("m1", "ชื่อ", [], [], {}))
        self.assertEqual(out[0], "m1")
        self.assertEqual(out[1], "ชื่อ")

    def test_none_stays_none(self):
        """ห่อ None จะได้ jsonb `null` ซึ่งไม่เท่ากับ SQL NULL — คอลัมน์ nullable จะเพี้ยน."""
        out = self._adapt(("m1", None, None, [], {}))
        self.assertIsNone(out[2])
        self.assertNotIsInstance(out[2], Jsonb)

    def test_the_row_keeps_its_length_and_order(self):
        row = ("m1", "ชื่อ", [], [], {})
        self.assertEqual(len(self._adapt(row)), len(row))


class TestJsonbColsIsAskedOfTheDestination(unittest.TestCase):
    """ชนิดของ **ปลายทาง** เป็นตัวตัดสิน ไม่ใช่ชนิดของต้นทาง — ปลายทางคือที่ที่จะถูกเขียน."""

    def test_it_filters_on_the_jsonb_type(self):
        src = (ROOT / "scripts" / "copy_meetings.py").read_text(encoding="utf-8")
        body = src[src.index("def _jsonb_cols"):src.index("def _adapt")]
        self.assertIn("data_type = 'jsonb'", body)

    def test_main_passes_the_destination_connection(self):
        src = (ROOT / "scripts" / "copy_meetings.py").read_text(encoding="utf-8")
        self.assertIn("_jsonb_cols(dst)", src)
        self.assertNotIn("_jsonb_cols(src)", src)


class FakeCursor:
    """จำ SQL กับพารามิเตอร์ที่ถูกส่งเข้ามา — ไม่ต้องมีเซิร์ฟเวอร์."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self


class TestInsert(unittest.TestCase):

    COLS = ["id", "title", "segments"]
    JSONB = {"segments"}

    def test_it_adapts_every_row_on_the_way_out(self):
        dst = FakeCursor()
        n = copy_meetings._insert(
            dst, self.COLS, [("a", "ก", [1]), ("b", "ข", [2])], "owner-1", self.JSONB)
        self.assertEqual(n, 2)
        self.assertEqual(len(dst.calls), 2)
        for _sql, params in dst.calls:
            with self.subTest(params=params):
                self.assertIsInstance(params[2], Jsonb)

    def test_the_owner_is_appended_last_and_is_not_wrapped(self):
        dst = FakeCursor()
        copy_meetings._insert(dst, self.COLS, [("a", "ก", [1])], "owner-1", self.JSONB)
        params = dst.calls[0][1]
        self.assertEqual(params[-1], "owner-1")
        self.assertEqual(len(params), len(self.COLS) + 1)

    def test_there_is_one_placeholder_per_value(self):
        dst = FakeCursor()
        copy_meetings._insert(dst, self.COLS, [("a", "ก", [1])], "owner-1", self.JSONB)
        sql, params = dst.calls[0]
        self.assertEqual(sql.count("%s"), len(params))

    def test_it_still_refuses_to_clobber(self):
        dst = FakeCursor()
        copy_meetings._insert(dst, self.COLS, [("a", "ก", [1])], "owner-1", self.JSONB)
        self.assertIn("on conflict (id) do nothing", dst.calls[0][0])

    def test_nothing_is_written_for_an_empty_list(self):
        dst = FakeCursor()
        self.assertEqual(copy_meetings._insert(dst, self.COLS, [], "o", self.JSONB), 0)
        self.assertEqual(dst.calls, [])


@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestARealRoundTrip(unittest.TestCase):
    """อ่านออกมาแล้วเขียนกลับเข้าไป ต้องได้ของเดิมเป๊ะ — เส้นทางที่บั๊กตัวจริงอยู่."""

    SPEAKERS = ["ผู้พูด 1", "ผู้พูด 2"]
    SEGMENTS = [{"start": 0.0, "text": "สวัสดีครับ", "speaker": 0},
                {"start": 1.5, "text": "ครับผม", "speaker": 1}]
    TRANSLATIONS = {"en": {"summary": "hello", "segments": ["hi", "yes"]}}

    def setUp(self) -> None:
        from unittest import mock

        from meeting_ai.web import db as pgdb

        self.pgdb = pgdb
        env = mock.patch.dict(os.environ,
                              {"DATABASE_URL": os.environ["MAI_TEST_DATABASE_URL"]})
        env.start()
        self.addCleanup(env.stop)
        pgdb.init()
        self.addCleanup(pgdb.close)
        self.tag = uuid.uuid4().hex[:8]
        self.src_id = f"20260920-120000-{self.tag[:6]}"
        self.dst_id = f"20260920-130000-{self.tag[:6]}"
        with self.pgdb.connect() as conn:
            self.owner = str(conn.execute(
                "insert into meeting_ai.users (email, name) values (%s, %s) returning id",
                (f"copy-{self.tag}@test.local", "copy")).fetchone()[0])
            conn.execute(
                """insert into meeting_ai.meetings
                       (id, owner_id, title, visibility, language,
                        speakers, segments, translations)
                   values (%s, %s, %s, 'private', 'th', %s, %s, %s)""",
                (self.src_id, self.owner, "ประชุมทดสอบ jsonb",
                 Jsonb(self.SPEAKERS), Jsonb(self.SEGMENTS), Jsonb(self.TRANSLATIONS)))
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.meetings where id = any(%s)",
                         ([self.src_id, self.dst_id],))
            conn.execute("delete from meeting_ai.users where id = %s", (self.owner,))

    def test_reading_a_row_and_writing_it_back_preserves_the_json(self):
        cols = ["id", "title", "speakers", "segments", "translations"]
        with self.pgdb.connect() as conn:
            row = conn.execute(
                f"select {', '.join(cols)} from meeting_ai.meetings where id = %s",
                (self.src_id,)).fetchone()
            # psycopg คืน jsonb มาเป็น dict/list ของ Python แล้ว — นี่คือต้นเหตุของบั๊ก
            self.assertIsInstance(row[2], list)
            self.assertIsInstance(row[4], dict)

            jsonb = copy_meetings._jsonb_cols(conn)
            self.assertLessEqual({"speakers", "segments", "translations"}, jsonb)

            moved = (self.dst_id, *row[1:])
            self.assertEqual(
                copy_meetings._insert(conn, cols, [moved], self.owner, jsonb), 1)

            back = conn.execute(
                "select speakers, segments, translations from meeting_ai.meetings "
                "where id = %s", (self.dst_id,)).fetchone()
        self.assertEqual(back[0], self.SPEAKERS)
        self.assertEqual(back[1], self.SEGMENTS)
        self.assertEqual(back[2], self.TRANSLATIONS)


if __name__ == "__main__":
    unittest.main()
