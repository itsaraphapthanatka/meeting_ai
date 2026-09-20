"""BACKLOG #82 — ด่านกันชี้ผิดฐานของ `mai db-init`.

`db-init` **สร้าง schema ทิ้งไว้** ในฐานที่มันไปโดน ต่างจาก `db-check` ที่อ่านอย่างเดียว
ถ้าชี้ผิดจึงไม่ใช่แค่ "ไม่ได้ผล" แต่ทิ้งร่องรอยไว้ในฐานของคนอื่น

เกิดขึ้นจริง 2026-09-20 สองครั้งในวันเดียว:

* รัน `db-init` ใส่ฐาน Neon เก่าไปสองรอบโดยคิดว่าเป็น production — ฐานเก่ากับ production
  **ชื่อเหมือนกันทั้งคู่** (`neondb`) ดูจากชื่อไม่ออก ต้องนับแถวเอา
* หยิบ connection string จากโปรเจกต์ Neon ผิดตัว ได้ฐานของ**แอปอื่น** 45 ตาราง
  (`neon_auth.*`, `public.AuditLog`) — ถ้ารัน `db-init` ตอนนั้นจะได้ schema `meeting_ai`
  ไปงอกอยู่ในฐานของแอปนั้น

`--expect-meetings N` เป็นการยืนยันเชิงบวกว่า "ฐานนี้คือฐานที่ฉันคิดถึง" ฐานใหม่เอี่ยมที่ยัง
ไม่มี schema ให้รันโดยไม่ใส่แฟล็ก — จะได้ไม่มีทางที่ `--expect-meetings 0` ไปผ่านด่านใน
ฐานของแอปอื่นที่บังเอิญไม่มีตารางของเรา

กติกาที่เทสต์ชุดนี้ตรึง: ด่านต้องอยู่**ก่อน** `db.init()` เสมอ และไม่ตรงต้องคืน exit code
ที่ไม่ใช่ 0 ไม่ใช่แค่พิมพ์เตือนแล้วทำต่อ
"""

from __future__ import annotations

import io
import os
import unittest
import urllib.parse
import uuid
from contextlib import redirect_stdout
from unittest import mock

from meeting_ai import cli


def run_db_init(**db_attrs):
    """เรียก `_cmd_db_init` ของจริงโดยแทนที่ `meeting_ai.web.db` ทั้งก้อน.

    คืน (exit code, ข้อความที่พิมพ์, ตัว mock ของ db) — ตัว mock บอกได้ว่า `init()`
    ถูกเรียกหรือไม่ ซึ่งคือคำถามสำคัญที่สุดของด่านนี้
    """
    args = mock.Mock()
    args.expect_meetings = db_attrs.pop("expect", None)
    db = mock.Mock()
    db.missing_pieces.return_value = db_attrs.pop("gaps", [])
    db.init.return_value = ["meetings", "users"]
    for k, v in db_attrs.items():
        getattr(db, k).return_value = v
    out = io.StringIO()
    # `from .web import db` หยิบ **attribute ของแพ็กเกจ** ก่อนเสมอ ถ้ามีเทสต์อื่นในสวีท
    # import ตัวจริงไปแล้ว การแทนที่เฉพาะ sys.modules จะไม่มีผล — ผ่านตอนรันไฟล์เดียว
    # แต่ล้ม 7 ข้อตอนรันทั้งสวีท (เจอจริง 2026-09-20) จึงต้องแทนที่ทั้งสองที่
    import meeting_ai.web as webpkg

    with mock.patch.dict("sys.modules", {"meeting_ai.web.db": db}),             mock.patch.object(webpkg, "db", db, create=True),             redirect_stdout(out):
        code = cli._cmd_db_init(args)
    return code, out.getvalue(), db


class TestTheFlag(unittest.TestCase):

    def test_it_exists_and_takes_a_number(self):
        args = cli.build_parser().parse_args(["db-init", "--expect-meetings", "24"])
        self.assertEqual(args.expect_meetings, 24)

    def test_it_is_optional(self):
        args = cli.build_parser().parse_args(["db-init"])
        self.assertIsNone(args.expect_meetings)


class TestWithoutTheFlagNothingChanges(unittest.TestCase):
    """คนที่รู้อยู่แล้วว่าชี้ถูก (เช่นฐานใหม่เอี่ยม) ต้องรันได้เหมือนเดิม."""

    def test_it_initialises(self):
        code, out, db = run_db_init()
        self.assertEqual(code, 0)
        db.init.assert_called_once()
        self.assertIn("สร้าง/อัปเดต schema", out)

    def test_it_does_not_even_count(self):
        _code, _out, db = run_db_init()
        db.meeting_count.assert_not_called()


class TestAMismatchStops(unittest.TestCase):

    def test_nothing_is_created(self):
        code, out, db = run_db_init(expect=24, meeting_count=11)
        self.assertEqual(code, 1)
        db.init.assert_not_called()

    def test_it_says_both_numbers(self):
        # บอกแค่ "ไม่ตรง" ไม่พอ คนอ่านต้องเดาได้ทันทีว่าไปโดนฐานไหน
        _code, out, _db = run_db_init(expect=24, meeting_count=11)
        self.assertIn("11", out)
        self.assertIn("24", out)
        self.assertIn("ชี้ผิดฐาน", out)


class TestAMatchGoesAhead(unittest.TestCase):

    def test_it_initialises(self):
        code, out, db = run_db_init(expect=24, meeting_count=24)
        self.assertEqual(code, 0)
        db.init.assert_called_once()
        self.assertIn("ตรงกับที่คาดไว้", out)


class TestADatabaseWithoutOurTables(unittest.TestCase):
    """ฐานของแอปอื่น — ห้ามสร้าง schema ทิ้งไว้ และต้องบอกว่าเจออะไรแทน."""

    def test_nothing_is_created(self):
        code, _out, db = run_db_init(
            expect=24, meeting_count=None, schema_hint="public (36 ตาราง)")
        self.assertEqual(code, 1)
        db.init.assert_not_called()

    def test_it_says_what_it_found_instead(self):
        _code, out, _db = run_db_init(
            expect=24, meeting_count=None, schema_hint="public (36 ตาราง), neon_auth (9 ตาราง)")
        self.assertIn("neon_auth", out)

    def test_it_points_at_the_legitimate_path(self):
        # ฐานใหม่ของ meeting_ai เองก็มาทางนี้ — ต้องบอกว่าให้รันโดยไม่ใส่แฟล็ก
        _code, out, _db = run_db_init(expect=0, meeting_count=None, schema_hint="(ว่างเปล่า)")
        self.assertIn("--expect-meetings", out)


class TestTheOrderOfTheChecks(unittest.TestCase):

    def test_a_missing_database_url_still_wins(self):
        # ไม่มี DATABASE_URL ต้องได้ 2 เหมือนเดิม ไม่ใช่ไปตายตอนนับแถว
        code, _out, db = run_db_init(expect=24, gaps=["ตัวแปร DATABASE_URL"])
        self.assertEqual(code, 2)
        db.meeting_count.assert_not_called()
        db.init.assert_not_called()

    def test_the_guard_is_written_before_init_in_the_source(self):
        from pathlib import Path

        src = (Path(cli.__file__)).read_text(encoding="utf-8")
        body = src[src.index("def _cmd_db_init"):]
        body = body[:body.index("def _db_gaps")]
        self.assertLess(body.index("expect_meetings"), body.index("db.init()"))


@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestTheCounterItselfAgainstRealPostgres(unittest.TestCase):
    """เทสต์ข้างบน mock `db` ทั้งก้อน กลไกใน `meeting_count()` เองจึงต้องวัดกับของจริง.

    มุตันต์ที่ถอดการเช็ค `information_schema` ออกรอดจากเทสต์ที่ mock ได้ทั้งหมด —
    เพราะ mock ไม่มีวันเจอ `UndefinedTable`
    """

    def setUp(self) -> None:
        from meeting_ai.web import db as pgdb

        self.pgdb = pgdb
        self.url = os.environ["MAI_TEST_DATABASE_URL"]
        self._point_at(self.url)
        pgdb.init()

    def _point_at(self, url: str) -> None:
        self.pgdb.close()
        env = mock.patch.dict(os.environ, {"DATABASE_URL": url})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.pgdb.close)

    def test_it_counts_what_is_really_there(self):
        with self.pgdb.connect() as conn:
            want = conn.execute("select count(*) from meeting_ai.meetings").fetchone()[0]
        self.assertEqual(self.pgdb.meeting_count(), want)

    def test_it_sees_a_row_appear(self):
        before = self.pgdb.meeting_count()
        tag = uuid.uuid4().hex[:8]
        mid = f"20260920-000000-{tag[:6]}"
        with self.pgdb.connect() as conn:
            conn.execute(
                """insert into meeting_ai.meetings (id, title, visibility, language)
                   values (%s, 'นับดู', 'private', 'th')""", (mid,))
        self.addCleanup(self._drop, mid)
        self.assertEqual(self.pgdb.meeting_count(), before + 1)

    def _drop(self, mid: str) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.meetings where id = %s", (mid,))

    def test_a_database_without_our_tables_gives_none(self):
        """ชี้ไปฐาน `postgres` ของเซิร์ฟเวอร์เดียวกัน — มีจริง ต่อติด แต่ไม่ใช่ของเรา.

        นี่คือรูปร่างของ "ชี้ไปฐานของแอปอื่น" ที่ทำให้ต้องมีด่านนี้ตั้งแต่แรก
        """
        parts = urllib.parse.urlsplit(self.url)
        if parts.path in ("/postgres", ""):
            self.skipTest("ฐานทดสอบชื่อ postgres อยู่แล้ว เทียบไม่ได้")
        self._point_at(urllib.parse.urlunsplit(parts._replace(path="/postgres")))
        self.assertIsNone(self.pgdb.meeting_count())
        self.assertNotIn("meeting_ai", self.pgdb.schema_hint())


if __name__ == "__main__":
    unittest.main()
