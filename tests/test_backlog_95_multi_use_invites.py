"""BACKLOG #95 — รหัสเชิญใบเดียวใช้ได้หลายคน + ข้อความที่ไม่โกหก.

เจ้าของถามว่า "รหัสเชิญจาก admin เชิญได้แค่คนเดียวหรอ" — ตอบจากโค้ดแล้วว่าใช่ และ
ระหว่างตอบก็พบว่า**ข้อความในหน้าเว็บชวนเข้าใจผิด**: เว้นอีเมลว่างแล้วมันบอกว่า
"ใครก็ใช้รหัสนี้ได้" ซึ่งอ่านแล้วนึกว่าใช้ได้หลายคน ทั้งที่หมายถึง "ใครก็ได้ แต่คนเดียว"

ตั๋วนี้ทำสองอย่าง: แก้ข้อความ และเพิ่มโควตาจริง

**ข้อจำกัดที่กำหนดรูปร่างของโค้ดนี้**: merge เข้า main แล้ว Vercel deploy ทันที
แต่ `db-init` ไม่เคยถูกรันเอง — มีช่วงที่ **โค้ดใหม่เจอฐานที่ยังไม่มีคอลัมน์** และ
`claim_invite()` อยู่บนเส้นทางสมัครสมาชิกซึ่งถ้าพังคือพังทั้งระบบ ไม่ใช่แค่ฟีเจอร์ใหม่ใช้ไม่ได้
โค้ดจึงถามฐานครั้งเดียวต่อโพรเซสว่ามีคอลัมน์หรือยัง แล้วเลือก SQL ให้เหมาะ
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from _harness import AuthCase          # type: ignore

from meeting_ai.web import pgstore      # noqa: E402
from meeting_ai.web.server import INVITE_MAX_USES   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
SCHEMA = (ROOT / "meeting_ai" / "web" / "schema.sql").read_text(encoding="utf-8")
PGSTORE = (ROOT / "meeting_ai" / "web" / "pgstore.py").read_text(encoding="utf-8")


class TestOneCodeManyPeople(AuthCase):

    def _admin(self):
        """สมัครคนแรกของระบบ = แอดมิน แล้วคืนคุกกี้."""
        status, body, headers = self.signup("boss@example.com")
        self.assertEqual(status, 200, body)
        cookie = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        return cookie

    def test_a_code_for_three_lets_exactly_three_in(self):
        admin = self._admin()
        status, body, _ = self.post_json("/api/auth/invite", {"max_uses": 3},
                                         cookies={"mai_session": admin})
        self.assertEqual(status, 200, body)
        code = body["code"]
        self.assertEqual(body["max_uses"], 3)

        for n in range(3):
            with self.subTest(person=n):
                st, bd, _ = self.signup(f"p{n}@example.com", invite=code)
                self.assertEqual(st, 200, bd)

        st, bd, _ = self.signup("p3@example.com", invite=code)
        self.assertNotEqual(st, 200, "คนที่สี่ต้องเข้าไม่ได้ — โควตาหมดแล้ว")

    def test_the_default_is_still_one_person(self):
        """ของเดิมเป็นแบบนี้ และคนส่วนใหญ่กดผ่านโดยไม่ตั้งค่า — ห้ามเปลี่ยนเงียบ ๆ."""
        admin = self._admin()
        _, body, _ = self.post_json("/api/auth/invite", {}, cookies={"mai_session": admin})
        self.assertEqual(body["max_uses"], 1)
        code = body["code"]
        self.assertEqual(self.signup("one@example.com", invite=code)[0], 200)
        self.assertNotEqual(self.signup("two@example.com", invite=code)[0], 200)

    def test_an_email_bound_code_cannot_be_shared(self):
        """ผูกอีเมลแล้วเชิญหลายคนไม่ได้ — อีเมลเดียวสมัครได้ครั้งเดียวอยู่แล้ว

        ถ้าปล่อยให้ตั้งได้ คนออกรหัสจะนึกว่าเชิญไปแล้ว N คน ทั้งที่โควตาที่เหลือใช้ไม่ได้เลย
        """
        admin = self._admin()
        status, body, _ = self.post_json(
            "/api/auth/invite", {"email": "only@example.com", "max_uses": 5},
            cookies={"mai_session": admin})
        self.assertEqual(status, 400)
        self.assertIn("คนเดียว", json.dumps(body, ensure_ascii=False))

    def test_the_quota_is_bounded(self):
        admin = self._admin()
        for bad in (0, -1, INVITE_MAX_USES + 1, 10 ** 9):
            with self.subTest(value=bad):
                status, _, _ = self.post_json("/api/auth/invite", {"max_uses": bad},
                                              cookies={"mai_session": admin})
                self.assertEqual(status, 400)

    def test_a_non_number_is_refused_not_crashed(self):
        admin = self._admin()
        status, body, _ = self.post_json("/api/auth/invite", {"max_uses": "สาม"},
                                         cookies={"mai_session": admin})
        self.assertEqual(status, 400)
        self.assertNotIn("Traceback", json.dumps(body, ensure_ascii=False))

    def test_still_admin_only(self):
        self._admin()
        status, body, _ = self.post_json("/api/auth/invite", {"max_uses": 5})
        self.assertEqual(status, 401)

    def test_the_first_person_keeps_the_credit(self):
        """`attach_invite` ยังเป็น used_by is null — คนแรกเท่านั้นที่ถูกบันทึกว่าใช้ใบนี้.

        หน้า dashboard ผู้ใช้ (#94) อ่าน invited_by จากตรงนี้ ถ้าเขียนทับทุกครั้ง
        คนก่อนหน้าจะกลายเป็น "ไม่รู้ว่าใครเชิญ" ย้อนหลัง
        """
        body = PGSTORE[PGSTORE.index("def attach_invite"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("used_by is null", body)


class TestItSurvivesADeployBeforeTheMigration(unittest.TestCase):
    """deploy ถึง production ก่อน db-init เสมอ — ช่วงนั้นคอลัมน์ยังไม่มี."""

    def test_the_schema_adds_the_columns_without_recreating_the_table(self):
        self.assertIn("add column if not exists max_uses", SCHEMA)
        self.assertIn("add column if not exists used_count", SCHEMA)

    def test_old_used_codes_do_not_come_back_to_life_after_migrating(self):
        """used_count ตั้งต้นเป็น 0 — แถวที่เคยถูกใช้จะกลายเป็นว่างอีกครั้งถ้าไม่ไล่ปิด."""
        self.assertRegex(SCHEMA, r"update meeting_ai\.invites set used_count = 1")
        fix = SCHEMA[SCHEMA.index("update meeting_ai.invites set used_count = 1"):]
        fix = fix[:fix.index(";")]
        self.assertIn("used_at is not null", fix)
        self.assertIn("used_count = 0", fix, "ไม่มีเงื่อนไขนี้แล้วรันซ้ำจะทับของที่นับไปแล้ว")

    def test_the_code_asks_the_database_before_using_the_new_columns(self):
        body = PGSTORE[PGSTORE.index("def invites_have_quota"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("information_schema.columns", body)
        self.assertIn("max_uses", body)
        self.assertIn("used_count", body)

    def test_half_a_migration_counts_as_not_ready(self):
        """คอลัมน์มาแค่ตัวเดียวต้องถือว่ายังไม่พร้อม.

        เจอตอนกลับกลไก: เปลี่ยน `== 2` เป็น `>= 1` แล้วไม่มีเทสต์ไหนจับได้เลย
        ซึ่งเป็นเคสที่ตัวตรวจนี้มีไว้กันพอดี — ถ้า db-init ล้มกลางคัน หรือมีคนเพิ่ม
        คอลัมน์เองทีละตัว โค้ดจะคิดว่าพร้อมแล้วไปพังที่อีกคอลัมน์หนึ่ง
        """
        from contextlib import contextmanager

        def fake_with(count):
            class FakeCursor:
                def fetchone(self):
                    return (count,)

            class FakeConn:
                def execute(self, sql, params=None):
                    return FakeCursor()

            @contextmanager
            def _c():
                yield FakeConn()
            return _c

        for count, expected in ((0, False), (1, False), (2, True)):
            with self.subTest(columns=count):
                patches = (mock.patch.object(pgstore.db, "connect", fake_with(count)),
                           mock.patch.object(pgstore, "_quota_ready", None))
                for p in patches:
                    p.start()
                try:
                    self.assertIs(pgstore.invites_have_quota(), expected)
                finally:
                    for p in patches:
                        p.stop()

    def test_a_long_running_server_notices_the_migration_without_a_redeploy(self):
        """จังหวะแคชต้องเหมือน _has_peaks()/_has_action_items() ที่มีอยู่ก่อนแล้ว.

        จำ True ตลอดอายุโพรเซส (คอลัมน์ไม่หายไปเอง) แต่ถ้ายังเป็น False ให้ลองใหม่
        ทุก 60 วินาที — แคชถาวรแปลว่าเซิร์ฟเวอร์ที่รันยาวจะไม่มีวันรู้ว่า migrate แล้ว
        ต้อง redeploy ถึงจะใช้ได้ ซึ่งเป็นสิ่งที่แพตเทิร์นนี้มีไว้กันตั้งแต่ BACKLOG #53
        """
        import time as _t
        from contextlib import contextmanager

        calls = []

        def conn_with(n):
            class FakeCursor:
                def fetchone(self):
                    return (n,)

            class FakeConn:
                def execute(self, sql, params=None):
                    calls.append(1)
                    return FakeCursor()

            @contextmanager
            def _c():
                yield FakeConn()
            return _c

        def probe(columns, cached, age):
            calls.clear()
            at = 0.0 if cached is None else _t.monotonic() - age
            patches = (mock.patch.object(pgstore.db, "connect", conn_with(columns)),
                       mock.patch.object(pgstore, "_quota_ready", cached),
                       mock.patch.object(pgstore, "_quota_checked_at", at))
            for p in patches:
                p.start()
            try:
                return pgstore.invites_have_quota(), len(calls)
            finally:
                for p in patches:
                    p.stop()

        # จำ True ไว้ ไม่ถามซ้ำ
        self.assertEqual(probe(2, True, 0), (True, 0))
        # False ที่เพิ่งถามไป ยังไม่ถามซ้ำ
        self.assertEqual(probe(2, False, 1), (False, 0))
        # False ที่เก่าเกิน 60 วินาที ถามซ้ำ และเจอว่า migrate แล้ว
        self.assertEqual(probe(2, False, 61), (True, 1))

    def test_it_reuses_the_recheck_window_the_project_already_has(self):
        """ค่าคงที่ตัวเดียวกัน ไม่ตั้งเลขใหม่ให้มีสองที่ต้องตามแก้."""
        body = PGSTORE[PGSTORE.index("def invites_have_quota"):]
        body = body[:body.index(chr(10) + "def ", 10)]
        self.assertIn("_PEAKS_RECHECK_SEC", body)

    def test_both_paths_exist_in_claim_invite(self):
        body = PGSTORE[PGSTORE.index("def claim_invite"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("invites_have_quota()", body)
        self.assertIn("used_count < max_uses", body)
        self.assertIn("used_by is null and used_at is null", body)

    def test_signup_still_works_when_the_columns_are_missing(self):
        """จุดสำคัญที่สุดของตั๋วนี้ — ถ้าข้อนี้พัง production ล็อกอิน/สมัครไม่ได้ทั้งระบบ."""
        calls = []

        class FakeCursor:
            def fetchone(self):
                return ("code",)

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append(sql)
                return FakeCursor()

        from contextlib import contextmanager

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with mock.patch.object(pgstore.db, "connect", fake_connect), \
             mock.patch.object(pgstore, "_quota_ready", False):
            self.assertTrue(pgstore.claim_invite("c", "a@b.co"))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("max_uses", calls[0],
                         "ยิง SQL ที่อ้างคอลัมน์ที่ยังไม่มี = สมัครสมาชิกพังทั้งระบบ")

    def test_create_invite_also_has_both_paths(self):
        body = PGSTORE[PGSTORE.index("def create_invite"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("invites_have_quota()", body)
        self.assertEqual(body.count("insert into meeting_ai.invites"), 2)

    def test_the_pre_check_agrees_with_the_decision(self):
        """invite_email() เลือกข้อความผิดพลาด ถ้าเงื่อนไขไม่ตรง claim_invite ข้อความจะหลอกกัน."""
        body = PGSTORE[PGSTORE.index("def invite_email"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("used_count < max_uses", body)
        self.assertIn("used_by is null and used_at is null", body)


class TestTheWordingNoLongerMisleads(unittest.TestCase):

    def test_the_old_sentence_is_gone(self):
        """ตรึงเฉพาะข้อความที่ผู้ใช้เห็นจริง ไม่ใช่ทั้งไฟล์.

        คอมเมนต์เหนือบรรทัดนั้นอธิบายว่าของเดิมเขียนว่าอะไรและทำไมถึงเปลี่ยน
        ซึ่งต้องเอ่ยถึงประโยคเดิมได้ ไม่งั้นก็เขียนอธิบายไม่ได้
        """
        prompts = re.findall(r"prompt\('([^']*)'", APP_JS)
        self.assertTrue(prompts, "ไม่เจอ prompt() เลย")
        for text in prompts:
            with self.subTest(text=text[:40]):
                self.assertNotIn("ใครก็ใช้รหัสนี้ได้", text)

    def test_it_asks_how_many_people_when_no_email_is_given(self):
        body = APP_JS[APP_JS.index("async function inviteMember()"):]
        body = body[:body.index("\nasync function ", 10)]
        self.assertRegex(body, r"ใช้ได้กี่คน")
        self.assertIn("max_uses", body)

    def test_it_says_the_expiry_out_loud(self):
        """รหัสหมดอายุ 14 วันมาตั้งแต่ต้น แต่ไม่เคยบอกใคร."""
        body = APP_JS[APP_JS.index("async function inviteMember()"):]
        body = body[:body.index("\nasync function ", 10)]
        self.assertIn("14 วัน", body)
        self.assertIn("days: int = 14", PGSTORE)

    def test_the_banner_reports_the_quota_it_actually_got(self):
        """บอกจำนวนจาก **คำตอบของเซิร์ฟเวอร์** ไม่ใช่จากที่หน้าเว็บขอไป."""
        body = APP_JS[APP_JS.index("async function inviteMember()"):]
        body = body[:body.index("\nasync function ", 10)]
        self.assertIn("out.max_uses", body)

    def test_the_browser_side_bound_matches_the_server(self):
        body = APP_JS[APP_JS.index("async function inviteMember()"):]
        body = body[:body.index("\nasync function ", 10)]
        found = {int(n) for n in re.findall(r"uses <= (\d+)", body)}
        self.assertEqual(found, {INVITE_MAX_USES},
                         "เพดานสองฝั่งไม่ตรงกัน ผู้ใช้จะเจอ error ที่หน้าเว็บไม่ได้เตือนไว้")


if __name__ == "__main__":
    unittest.main()
