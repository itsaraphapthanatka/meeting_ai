"""BACKLOG #42 — การประชุมที่ไม่มีเจ้าของให้สิทธิ์ 'owner' กับผู้ใช้ที่ล็อกอินทุกคน.

ตั๋วขอให้ "ตรวจว่า production ไม่มีแถว `owner_id is null`" ด้วยคิวรีเดียว แต่ agent ไม่แตะ
production (ดู PROJECT-CONTEXT) สิ่งที่ทำได้และมีค่ากว่าการรันคิวรีครั้งเดียวคือ:

1. **วัดว่าความเสี่ยงมีจริงแค่ไหน** — เทสต์กับ Postgres ของจริง (ชั่วคราว) ว่าคนแปลกหน้า
   ที่ล็อกอินอยู่ได้สิทธิ์ `owner` กับแถวแบบนั้นจริง และเห็นมันในรายการของตัวเองด้วย
2. **วัดว่าแถวแบบนี้ยังเกิดใหม่ได้ไหม** — schema เขียนว่า
   `owner_id ... on delete set null` ดังนั้น **ลบผู้ใช้หนึ่งคน = การประชุมทั้งหมดของเขา
   กลายเป็นของทุกคน** ไม่ใช่แค่ข้อมูลเก่าที่ย้ายมาจากโหมดไฟล์อย่างที่ตั๋วสันนิษฐาน
3. **ทำให้เจ้าของตรวจเองได้ในคำสั่งเดียว** — `mai db-check` อ่านอย่างเดียว คืนแค่ตัวเลข
   ไม่คืนข้อมูลของใคร จึงรันกับฐานจริงได้โดยไม่เสี่ยงข้อมูลหลุด

ข้อ 1 กับ 2 ต้องมี `MAI_TEST_DATABASE_URL` (CI งาน ubuntu+postgres มีให้) ที่เหลือรันได้ทุกที่
"""

from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock

from meeting_ai import cli
from meeting_ai.web import pgstore

WRITES = ("insert ", "update ", "delete ", "drop ", "alter ", "truncate ", "create ")


class TestTheAuditIsReadOnly(unittest.TestCase):
    """คำสั่งที่เจ้าของจะเอาไปรันกับฐานจริง ต้องพิสูจน์ได้ว่าอ่านอย่างเดียว."""

    def test_every_query_only_counts(self):
        for key, severity, sql, why in pgstore.AUDITS:
            with self.subTest(check=key):
                low = sql.lower()
                self.assertTrue(low.startswith("select count("), sql)
                for word in WRITES:
                    self.assertNotIn(word, low, f"{key} มีคำสั่งเขียน: {word.strip()}")
                self.assertIn(severity, ("security", "hygiene"))
                self.assertTrue(why.strip(), f"{key} ไม่ได้บอกว่าทำไมถึงสำคัญ")

    def test_the_ticket_query_is_one_of_them(self):
        sqls = [sql.lower() for _, _, sql, _ in pgstore.AUDITS]
        self.assertTrue(
            any("meeting_ai.meetings where owner_id is null" in s for s in sqls),
            "หายไปแล้วคิวรีที่ตั๋ว #42 ขอ")

    def test_the_ownerless_check_is_the_security_one(self):
        sev = {key: severity for key, severity, _, _ in pgstore.AUDITS}
        self.assertEqual(sev["ownerless_meetings"], "security")
        # ข้ออื่นเป็นความสะอาดของข้อมูล ไม่ควรทำให้คำสั่งล้มจนคนเลิกรัน
        self.assertEqual({v for k, v in sev.items() if k != "ownerless_meetings"},
                         {"hygiene"})


class TestTheCommand(unittest.TestCase):
    """`mai db-check` — ไม่ต้องมีฐานข้อมูลก็ทดสอบพฤติกรรมได้."""

    def _run(self, rows: list[dict]) -> int:
        from meeting_ai.web import db

        def boom(*a, **k):
            raise AssertionError("db-check ต้องไม่แตะ schema — นี่คือคำสั่งอ่านอย่างเดียว")

        with mock.patch.object(db, "missing_pieces", lambda: []), \
             mock.patch.object(db, "init", boom), \
             mock.patch.object(db, "close", lambda: None), \
             mock.patch.object(pgstore, "audit", lambda: rows):
            return cli._cmd_db_check(mock.Mock())

    @staticmethod
    def _row(key: str, severity: str, count: int) -> dict:
        return {"key": key, "severity": severity, "count": count,
                "ok": count == 0, "why": "เหตุผล"}

    def test_clean_database_exits_zero(self):
        self.assertEqual(self._run([self._row("ownerless_meetings", "security", 0)]), 0)

    def test_an_ownerless_row_fails_the_command(self):
        # ต้องเป็น exit code ไม่ใช่แค่ข้อความ — เจ้าของจะได้เอาไปใส่สคริปต์ได้
        self.assertEqual(self._run([self._row("ownerless_meetings", "security", 3)]), 1)

    def test_hygiene_findings_do_not_fail_the_command(self):
        rows = [self._row("ownerless_meetings", "security", 0),
                self._row("expired_sessions", "hygiene", 120)]
        self.assertEqual(self._run(rows), 0)

    def test_it_stops_before_touching_the_database_when_pieces_are_missing(self):
        from meeting_ai.web import db

        with mock.patch.object(db, "missing_pieces", lambda: ["ตัวแปร DATABASE_URL"]), \
             mock.patch.object(pgstore, "audit",
                               mock.Mock(side_effect=AssertionError("ไม่ควรถูกเรียก"))):
            self.assertEqual(cli._cmd_db_check(mock.Mock()), 2)

    def test_it_is_registered_as_a_subcommand(self):
        args = cli.build_parser().parse_args(["db-check"])
        self.assertIs(args.func, cli._cmd_db_check)


@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestWhatAnOwnerlessRowActuallyDoes(unittest.TestCase):
    """วัดกับฐานจริง (ชั่วคราว) ว่าแถวแบบนี้อันตรายแค่ไหน — ไม่ใช่เชื่อคำในตั๋ว."""

    def setUp(self) -> None:
        from meeting_ai.web import db as pgdb

        self.pgdb = pgdb
        env = mock.patch.dict(os.environ,
                              {"DATABASE_URL": os.environ["MAI_TEST_DATABASE_URL"]})
        env.start()
        self.addCleanup(env.stop)
        pgdb.init()
        self.addCleanup(pgdb.close)
        self.tag = uuid.uuid4().hex[:8]
        self.alice = self._user("alice")      # เจ้าของตัวจริง
        self.stranger = self._user("mallory")  # คนอื่นในระบบเดียวกัน
        self.addCleanup(self._cleanup)

    def _user(self, name: str) -> str:
        with self.pgdb.connect() as conn:
            row = conn.execute(
                "insert into meeting_ai.users (email, name) values (%s, %s) returning id",
                (f"{name}-{self.tag}@test.local", name)).fetchone()
        return str(row[0])

    def _meeting(self, suffix: str, owner: str | None) -> str:
        mid = f"20260918-1200{suffix}-{self.tag[:6]}"
        with self.pgdb.connect() as conn:
            conn.execute(
                """insert into meeting_ai.meetings (id, owner_id, title, visibility, language)
                   values (%s, %s, %s, 'private', 'th')""",
                (mid, owner, f"ประชุมทดสอบ {suffix}"))
        return mid

    def _cleanup(self) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.meetings where id like %s",
                         (f"%{self.tag[:6]}",))
            conn.execute("delete from meeting_ai.users where email like %s",
                         (f"%-{self.tag}@test.local",))

    def test_a_stranger_becomes_owner_of_an_ownerless_meeting(self):
        mid = self._meeting("00", None)
        self.assertEqual(pgstore.access(mid, self.stranger), "owner",
                         "ถ้าข้อนี้เปลี่ยน แปลว่าความเสี่ยงของตั๋ว #42 หายไปแล้ว")

    def test_an_owned_private_meeting_stays_closed(self):
        # เทียบให้เห็นว่าเป็นเพราะ owner_id เป็น null จริง ๆ ไม่ใช่เพราะสิทธิ์พังทั้งระบบ
        mid = self._meeting("01", self.alice)
        self.assertEqual(pgstore.access(mid, self.stranger), "none")
        self.assertEqual(pgstore.access(mid, self.alice), "owner")

    def test_it_shows_up_in_a_strangers_list(self):
        mid = self._meeting("02", None)
        ids = [m["id"] for m in pgstore.search("", user_id=self.stranger)]
        self.assertIn(mid, ids, "แถวไม่มีเจ้าของโผล่ในรายการของทุกคน")

    def test_deleting_a_user_creates_new_ownerless_rows(self):
        """ข้อนี้คือสิ่งที่ตั๋วไม่ได้บอก: แถวแบบนี้ **ไม่ได้มีแต่ของเก่าที่ย้ายมา**.

        schema เขียน `owner_id uuid references users(id) on delete set null`
        ลบผู้ใช้หนึ่งคนในฐาน = การประชุมทั้งหมดของเขากลายเป็นของทุกคนทันที
        """
        mid = self._meeting("03", self.alice)
        self.assertEqual(pgstore.access(mid, self.stranger), "none")
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.users where id = %s", (self.alice,))
        self.assertEqual(pgstore.access(mid, self.stranger), "owner",
                         "ลบผู้ใช้แล้วการประชุมของเขาต้องไม่กลายเป็นของทุกคนเงียบ ๆ")

    def test_the_audit_counts_it(self):
        before = self._count()
        self._meeting("04", None)
        self.assertEqual(self._count(), before + 1)

    def _count(self) -> int:
        rows = {r["key"]: r for r in pgstore.audit()}
        self.assertIn("ownerless_meetings", rows)
        self.assertEqual(rows["ownerless_meetings"]["severity"], "security")
        return rows["ownerless_meetings"]["count"]


if __name__ == "__main__":
    unittest.main()
