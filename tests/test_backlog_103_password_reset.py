"""BACKLOG #103 — ลิงก์ตั้งรหัสผ่านใหม่ที่แอดมินออกให้.

หลัง #102 เจ้าของสั่งทำปุ่มรีเซ็ตรหัสผ่านต่อ · โปรเจกต์นี้**ไม่มีช่องทางส่งอีเมล**
(stdlib อย่างเดียว ไม่มี SMTP ไม่มี provider) ทางที่ทำได้จริงจึงเป็นแบบเดียวกับลิงก์เชิญ:
แอดมินออกลิงก์ แล้วส่งต่อทางแชท ซึ่งเป็นช่องทางที่ทีมนี้ใช้อยู่แล้ว

สิ่งที่เทสต์ชุดนี้ตรึงไว้ และเหตุผล

* **โทเคนเก็บเป็น hash** เหมือน invites/sessions — ฐานไม่เคยมีตัวโทเคนอยู่เลย
* **ใช้ได้ครั้งเดียว ตัดสินใน statement เดียว** (บทเรียน BUG-012: select-แล้ว-update
  ทำให้คนที่ยิงพร้อมกันผ่านได้ทุกราย)
* **รหัสสั้นเกินต้องถูกปฏิเสธก่อนโทเคนถูกเผา** ไม่งั้นคนพิมพ์รหัสสั้นหนึ่งครั้ง
  แล้วลิงก์ตายทั้งใบ ต้องไปขอใหม่จากแอดมิน
* **เตะ session เก่าทิ้งทั้งหมด** — เหตุผลหลักข้อหนึ่งที่คนรีเซ็ตรหัสผ่านคือ
  "มีคนอื่นเข้าบัญชีฉันอยู่" ถ้าไม่เตะออก การรีเซ็ตก็ไม่ได้แก้อะไรเลย
* **ออกใบใหม่แล้วใบเก่าต้องตาย** ไม่งั้นลิงก์ที่เคยส่งผิดคนยังเปิดประตูได้
* **ข้อความปฏิเสธไม่แยกว่า "ไม่มีโทเคนนี้" กับ "หมดอายุ"** การแยกให้คือบอกคนเดา
  ว่าเดาใกล้แล้ว
* **ตารางเกิดทีหลัง** deploy ถึง production ก่อน db-init เสมอ ช่วงนั้นต้องตอบ 503
  พร้อมข้อความที่บอกว่าต้องทำอะไร ไม่ใช่ 500 ที่อ่านไม่ออก

วัดบนเบราว์เซอร์จริง — ดูหมายเหตุใน BACKLOG แถว #103
"""

from __future__ import annotations

import hashlib
import re
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from _harness import AuthCase, CloudCase, LocalCase
from meeting_ai.web import pgstore

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "meeting_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")
SCHEMA = (ROOT / "meeting_ai" / "web" / "schema.sql").read_text(encoding="utf-8")


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestOnlyAdminsIssueLinks(CloudCase):
    def _issue(self, uid=None, token=None):
        return self.post_json(f"/api/admin/users/{uid or self.uid_a}/reset", {},
                              cookies={"mai_session": token} if token else None)

    def test_an_admin_can_issue_one(self):
        status, body, _ = self._issue(token=self.tokAdm)
        self.assertEqual(status, 200)
        self.assertTrue(body.get("token"))
        self.assertEqual(body.get("hours"), 24)

    def test_an_ordinary_member_cannot(self):
        status, _, _ = self._issue(token=self.tokB)
        self.assertEqual(status, 403)
        self.assertEqual(self.store.resets, {})

    def test_a_stranger_cannot(self):
        status, _, _ = self._issue()
        self.assertIn(status, (401, 403))
        self.assertEqual(self.store.resets, {})

    def test_a_malformed_user_id_is_rejected(self):
        """ต้องเป็น uid ที่ยัง "เป็นสี่ส่วนของ path" อยู่ ไม่งั้นได้ 404 จากการไม่เข้าเส้นทาง

        แล้วเทสต์ก็ผ่านแม้ถอดการตรวจรูปออก (มุตันต์รอดมาแล้วครั้งหนึ่ง)
        """
        for bad in ("uid.a", "uid~a", "uid!", "u" * 200):
            with self.subTest(uid=bad):
                status, _, _ = self._issue(uid=bad, token=self.tokAdm)
                self.assertEqual(status, 400)
        self.assertEqual(self.store.resets, {})

    def test_issuing_is_written_to_the_log(self):
        from meeting_ai.web import server as srv
        with mock.patch.object(srv.log, "info") as spy:
            self._issue(token=self.tokAdm)
        said = " ".join(str(c) for c in spy.call_args_list)
        self.assertIn("[password-reset]", said)

    def test_a_new_link_kills_the_previous_unused_one(self):
        """ลิงก์ที่เคยส่งผิดคนต้องตายทันทีที่ออกใบใหม่ ไม่ใช่รอหมดอายุ."""
        _, first, _ = self._issue(token=self.tokAdm)
        _, second, _ = self._issue(token=self.tokAdm)
        self.assertNotEqual(first["token"], second["token"])
        status, _, _ = self.post_json("/api/auth/reset",
                                      {"token": first["token"], "password": "x" * 10})
        self.assertEqual(status, 403)

    def test_a_database_without_the_table_says_what_is_wrong(self):
        """deploy ถึง production ก่อน db-init เสมอ — ช่วงนั้นต้องไม่เป็น 500."""
        self.store._resets_ready = False
        status, body, _ = self._issue(token=self.tokAdm)
        self.assertEqual(status, 503)
        self.assertIn("ฐานข้อมูล", str(body))


class TestUsingTheLink(CloudCase):
    def _token(self):
        _, body, _ = self.post_json(f"/api/admin/users/{self.uid_a}/reset", {},
                                    cookies={"mai_session": self.tokAdm})
        return body["token"]

    def test_a_valid_token_sets_the_password_and_logs_in(self):
        status, body, headers = self.post_json(
            "/api/auth/reset", {"token": self._token(), "password": "brand-new-pw"})
        self.assertEqual(status, 200)
        self.assertEqual(body["user"]["id"], self.uid_a)
        self.assertIn("mai_session", str(headers))
        self.assertIsNotNone(self.store.verify_password("a@example.com", "brand-new-pw"))

    def test_no_cookie_is_needed_to_use_it(self):
        """คนที่เข้าไม่ได้คือคนที่ไม่มีคุกกี้ — เส้นนี้ต้องอยู่ใน PUBLIC_API."""
        from meeting_ai.web import server as srv
        self.assertIn(("auth", "reset"), srv.PUBLIC_API)

    def test_the_token_works_only_once(self):
        token = self._token()
        self.post_json("/api/auth/reset", {"token": token, "password": "first-pw-01"})
        status, _, _ = self.post_json("/api/auth/reset",
                                      {"token": token, "password": "second-pw-02"})
        self.assertEqual(status, 403)
        self.assertIsNotNone(self.store.verify_password("a@example.com", "first-pw-01"))

    def test_an_unknown_token_is_refused_without_saying_why(self):
        """แยก "ไม่มีโทเคนนี้" กับ "หมดอายุ" ให้ = บอกคนเดาว่าเดาใกล้แล้ว."""
        status, body, _ = self.post_json("/api/auth/reset",
                                         {"token": "ไม่มีจริง", "password": "x" * 10})
        self.assertEqual(status, 403)
        for word in ("หมดอายุ", "ถูกใช้ไปแล้ว", "ไม่มี"):
            self.assertNotIn(word, str(body.get("error", "")))

    def test_a_short_password_is_refused_before_the_token_is_burned(self):
        """พิมพ์รหัสสั้นหนึ่งครั้งแล้วลิงก์ตายทั้งใบ = ต้องไปขอใหม่จากแอดมิน."""
        token = self._token()
        status, _, _ = self.post_json("/api/auth/reset", {"token": token, "password": "sh"})
        self.assertEqual(status, 400)
        status, _, _ = self.post_json("/api/auth/reset",
                                      {"token": token, "password": "long-enough-pw"})
        self.assertEqual(status, 200)

    def test_every_old_session_is_kicked_out(self):
        """เหตุผลหลักข้อหนึ่งที่คนรีเซ็ตคือ "มีคนอื่นเข้าบัญชีฉันอยู่"."""
        self.assertIsNotNone(self.store.user_for_session(self.tokA))
        self.post_json("/api/auth/reset",
                       {"token": self._token(), "password": "fresh-pw-123"})
        self.assertIsNone(self.store.user_for_session(self.tokA))

    def test_other_peoples_sessions_are_untouched(self):
        self.post_json("/api/auth/reset",
                       {"token": self._token(), "password": "fresh-pw-123"})
        self.assertIsNotNone(self.store.user_for_session(self.tokB))


class TestFileModeHasNoSuchThing(LocalCase):
    def test_issuing_is_404(self):
        status, _, _ = self.post_json("/api/admin/users/uid-a/reset", {})
        self.assertEqual(status, 404)

    def test_using_is_not_a_500(self):
        status, _, _ = self.post_json("/api/auth/reset",
                                      {"token": "x", "password": "y" * 10})
        self.assertIn(status, (404, 501))


class TestTheSchema(unittest.TestCase):
    def test_the_table_is_in_the_schema(self):
        self.assertIn("create table if not exists meeting_ai.password_resets", SCHEMA)

    def test_the_token_is_stored_hashed_not_in_the_clear(self):
        block = SCHEMA[SCHEMA.index("meeting_ai.password_resets"):][:500]
        self.assertIn("token_hash  bytea primary key", block)
        self.assertNotIn("token  text", block)

    def test_deleting_a_user_takes_their_reset_links_with_them(self):
        block = SCHEMA[SCHEMA.index("meeting_ai.password_resets"):][:500]
        self.assertIn("on delete cascade", block)


class TestPgstoreSqlShape(unittest.TestCase):
    """ตรวจรูป SQL ของ pgstore ตัวจริงโดยไม่ต้องมี Postgres (รูปแบบเดียวกับ BUG-012).

    เทสต์ข้างบนทุกข้อเดินผ่าน FakeStore ซึ่งมีตรรกะสำเนาของตัวเอง — ถ้าไม่มีชุดนี้
    ถอดเงื่อนไข `used_at is null` ออกจาก SQL จริงแล้วสวีทยังเขียว (บทเรียนจาก #102)
    """

    def _capture(self, call, ready=True):
        calls: list[tuple] = []

        class FakeCursor:
            def fetchone(self):
                return None

            def fetchall(self):
                return []

        class FakeConn:
            def execute(self, sql, params=None):
                calls.append((sql, params))
                return FakeCursor()

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with mock.patch.object(pgstore.db, "connect", fake_connect), \
             mock.patch.object(pgstore, "_resets_ready", ready), \
             mock.patch.object(pgstore, "_resets_checked_at", time.monotonic()):
            call()
        return calls

    def test_claiming_is_one_statement_with_every_condition_in_the_where(self):
        calls = self._capture(lambda: pgstore.claim_reset("tok"))
        self.assertEqual(len(calls), 1, calls)
        sql = calls[0][0]
        self.assertIn("used_at is null", sql)
        self.assertIn("expires_at > now()", sql)
        self.assertIn("set used_at = now()", sql)

    def test_the_token_is_hashed_before_it_reaches_the_database(self):
        calls = self._capture(lambda: pgstore.claim_reset("tok"))
        params = calls[0][1]
        self.assertNotIn("tok", [p for p in params if isinstance(p, str)])
        self.assertIsInstance(params[0], bytes)

    def test_issuing_clears_the_previous_unused_link_first(self):
        calls = self._capture(lambda: pgstore.create_reset("u1", "adm"))
        self.assertEqual(len(calls), 2, calls)
        self.assertIn("delete from meeting_ai.password_resets", calls[0][0])
        self.assertIn("used_at is null", calls[0][0])
        self.assertIn("insert into meeting_ai.password_resets", calls[1][0])

    def test_the_issued_token_is_hashed_before_it_is_stored(self):
        """ฐานต้องไม่เคยมีตัวโทเคนอยู่เลย เหมือน invites/sessions."""
        out = {}
        calls = self._capture(
            lambda: out.setdefault("token", pgstore.create_reset("u1", "adm")))
        params = calls[1][1]
        # คำนวณ hash เองด้วย hashlib ไม่เรียก pgstore._hash — ไม่งั้นมุตันต์ที่เปลี่ยน
        # วิธี hash จะผ่านทั้งคู่ · และ `assertNotIn(str, params)` อย่างเดียวก็ไม่พอ
        # เพราะ token.encode() เป็น bytes ซึ่งไม่เท่ากับ str อยู่แล้ว (มุตันต์รอดมาแล้ว)
        self.assertEqual(params[0], hashlib.sha256(out["token"].encode()).digest())
        self.assertNotEqual(params[0], out["token"].encode())

    def test_nothing_is_written_when_the_table_is_missing(self):
        calls = self._capture(lambda: pgstore.create_reset("u1", "adm"), ready=False)
        self.assertEqual(calls, [])

    def test_dropping_sessions_targets_one_user_only(self):
        calls = self._capture(lambda: pgstore.drop_sessions_of("u1"))
        self.assertEqual(len(calls), 1)
        self.assertIn("where user_id = %s", calls[0][0])


class TestThePage(unittest.TestCase):
    def test_one_reader_for_the_url_parameter(self):
        """อ่านสองที่แล้วแก้ที่เดียวคือทางที่เคยพลาดมาแล้ว (#98)."""
        self.assertEqual(APP_JS.count("get('reset')"), 1)

    def test_a_reset_link_wins_over_login_and_signup(self):
        """คนที่ถือลิงก์นี้คือคนที่เข้าไม่ได้อยู่แล้ว อย่าพาไปหน้าล็อกอิน."""
        body = fn("function showAuth(mode)")
        decide = body[:body.index("document.body.classList")]
        self.assertIn("resetFromUrl()", decide)
        self.assertRegex(decide, r"!resetToken && \(mode === 'signup'")

    def test_the_form_asks_only_for_the_new_password(self):
        body = fn("function showResetForm(")
        for hidden in ("#a-email-wrap", "#a-name-wrap", "#a-invite-wrap"):
            self.assertRegex(body, re.escape(hidden) + r"'\)\.hidden = true")
        self.assertIn('id="a-email-wrap"', HTML, "ไม่มีตัวห่อช่องอีเมลให้ซ่อน")

    def test_it_posts_the_token_with_the_password(self):
        body = fn("function showResetForm(")
        self.assertIn("'/api/auth/reset'", body)
        self.assertIn("{ token, password }", body)

    def test_the_token_does_not_stay_in_the_address_bar(self):
        """โทเคนใน URL ค้างอยู่ใน history ของเบราว์เซอร์."""
        body = fn("function showResetForm(")
        self.assertIn("location.href = '/'", body)

    def test_the_admin_has_a_button(self):
        body = fn("function renderUsers(")
        self.assertIn("u-reset", body)
        self.assertIn("data-uid=", body)

    def test_the_button_does_not_also_open_the_meetings_view(self):
        """ปุ่มอยู่ในแถวที่กดแล้วเปิดรายการประชุม — ต้องกินเหตุการณ์ก่อน."""
        body = fn("function renderUsers(")
        handler = body[body.index("table.onclick ="):body.index("};", body.index("table.onclick ="))]
        self.assertLess(handler.index("u-reset"), handler.index("open("))
        self.assertRegex(handler, r"if \(btn\) return resetPassword\(")

    def test_issuing_asks_first(self):
        body = fn("async function resetPassword(")
        self.assertLess(body.index("confirm("), body.index("api("))

    def test_the_link_is_shown_not_only_copied(self):
        """บทเรียนตรง ๆ จาก #101 — คลิปบอร์ดเป็นที่ที่มองไม่เห็น."""
        body = fn("async function resetPassword(")
        self.assertIn("showLinkBox(", body)
        m = re.search(r"const (\w+) = await copyText\(", body)
        self.assertIsNotNone(m, "ทิ้งค่าที่ copyText() คืนมา เหมือนบั๊กของ #101")
        self.assertIn(m.group(1), body[m.end():])

    def test_the_note_says_it_is_single_use_and_when_it_dies(self):
        body = fn("async function resetPassword(")
        self.assertIn("ใช้ได้ครั้งเดียว", body)
        self.assertIn("ชั่วโมง", body)


class TestTheRateLimitBucketIsItsOwn(AuthCase):
    def test_the_reset_endpoint_has_its_own_scope(self):
        """ถังเดียวกับล็อกอินแปลว่าคนเดาโทเคนล็อกคนอื่นออกจากการล็อกอินได้."""
        from meeting_ai.web import server as srv
        body = srv.Handler._reset_password.__doc__ or ""
        self.assertIn("แยกถัง", body)
        src = (ROOT / "meeting_ai" / "web" / "server.py").read_text(encoding="utf-8")
        handler = src[src.index("def _reset_password"):]
        handler = handler[:handler.index(chr(10) + "    def ")]
        self.assertIn('self._rate_limited("reset")', handler)
        self.assertIn('self._rate_ok("reset")', handler)


if __name__ == "__main__":
    unittest.main()
