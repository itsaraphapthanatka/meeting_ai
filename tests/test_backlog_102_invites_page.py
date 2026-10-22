"""BACKLOG #102 — หน้าดูรหัสเชิญที่ออกไปแล้ว.

ตั๋วนี้มาจาก #101 โดยตรง: ใบ 30 คนที่หายไปกับคลิปบอร์ดไม่มีที่ไหนให้ดูเลยว่ามันมีอยู่
ยังเหลือโควตาเท่าไร หรือจะยกเลิกทิ้งยังไง — แอดมินมองไม่เห็นของที่ตัวเองออกไป

**หน้านี้แสดงตัวรหัสไม่ได้ และนั่นคือข้อกำหนด ไม่ใช่ข้อจำกัดที่รอแก้** — เซิร์ฟเวอร์เก็บ
`sha256(code)` ตั้งแต่ตอนออก ตัวรหัสไม่เคยถูกเก็บ เทสต์ชุดนี้จึงล็อกไว้ว่า**คำตอบของ API
ต้องไม่มีรหัสหลุดออกมา** ไม่ว่าจะโดยตั้งใจหรือเผลอ

ตัวชี้แถว (`ref`) คือ 16 ตัวแรกของ hash ในรูป hex — ไม่ใช่ตัวรหัส และไม่ได้ทำให้เดารหัส
96 บิตง่ายขึ้นจนใช้การได้ · เลือกแบบนี้เพราะตาราง invites ไม่มีคอลัมน์ id และการเพิ่ม
คอลัมน์ใหม่แปลว่าทางอ่านของ main ต้องพึ่ง migration ซึ่งห้ามตามกติกา deploy-มาก่อน-migration

วัดบนเบราว์เซอร์จริง (เซิร์ฟเวอร์จาก git worktree)

    1280x860  ตาราง 7 คอลัมน์ · ปุ่มยกเลิก 57x33 `elementFromPoint` กลางปุ่มได้ตัวเอง
    คลิกจริง  ถาม confirm ก่อน -> DELETE /api/admin/invites/<ref> -> แบนเนอร์ -> โหลดใหม่
    390x812   ตารางเลื่อนแนวนอนในกรอบตัวเอง (696 -> 358) ไม่ดันทั้งหน้า และเลื่อนไปสุด
              แล้วปุ่มยกเลิกอยู่ในจอ (right 364 < 390) กดได้จริง

**ของที่เจอตอนเปิดดูด้วยตา ไม่ใช่ตอนเขียน**: คอลัมน์ "หมดอายุ" ขึ้น "—" ทุกแถว เพราะ
ใช้ `sinceText()` ซึ่งตอบ "ผ่านมานานแค่ไหน" และคืนค่าว่างเสมอสำหรับเวลาในอนาคต
— ซึ่งคือทุกแถวที่ยังใช้ได้ จึงเพิ่ม `untilText()` แยกต่างหาก
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from datetime import datetime, timedelta, timezone

from _harness import CloudCase, LocalCase
from meeting_ai.web import pgstore

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
SERVER_PY = (ROOT / "meeting_ai" / "web" / "server.py").read_text(encoding="utf-8")


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestOnlyAdminsSeeTheList(CloudCase):
    def test_an_admin_gets_the_list(self):
        self.store.create_invite(self.uid_adm, None, max_uses=5)
        status, body, _ = self.get("/api/admin/invites",
                                   cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["invites"]), 1)

    def test_an_ordinary_member_cannot(self):
        status, _, _ = self.get("/api/admin/invites", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 403)

    def test_a_stranger_cannot(self):
        status, _, _ = self.get("/api/admin/invites")
        self.assertIn(status, (401, 403))


class TestTheCodeNeverComesBack(CloudCase):
    """ข้อกำหนดหลักของตั๋วนี้ — รหัสเก็บเป็น hash ห้ามมีทางไหนคายมันออกมา."""

    def test_the_code_is_not_in_the_listing(self):
        code = self.store.create_invite(self.uid_adm, None, max_uses=5)
        status, body, raw = self.get("/api/admin/invites",
                                     cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        self.assertNotIn(code, str(raw))
        self.assertNotIn(code, str(body))

    def test_what_comes_back_instead_is_a_hash_prefix(self):
        code = self.store.create_invite(self.uid_adm, None)
        _, body, _ = self.get("/api/admin/invites", cookies={"mai_session": self.tokAdm})
        ref = body["invites"][0]["ref"]
        self.assertRegex(ref, r"^[0-9a-f]{16}$")
        self.assertEqual(ref, self.store.invite_ref(code))

    def test_the_page_tells_the_admin_the_code_cannot_be_shown(self):
        """ถ้าไม่บอก คนจะนั่งหาปุ่ม "ดูรหัส" ที่ไม่มีวันมี."""
        body = fn("function renderInvites(")
        self.assertIn("ดูตัวรหัสย้อนหลังไม่ได้", body)
        self.assertIn("ออกใบใหม่", body)


class TestWhatTheListSays(CloudCase):
    def test_an_unused_invite_is_open_with_its_quota_left(self):
        self.store.create_invite(self.uid_adm, None, max_uses=30)
        _, body, _ = self.get("/api/admin/invites", cookies={"mai_session": self.tokAdm})
        row = body["invites"][0]
        self.assertEqual((row["status"], row["max_uses"], row["used_count"], row["left"]),
                         ("open", 30, 0, 30))

    def test_a_fully_used_invite_says_so(self):
        self.store.add_invite("spent", email="x@example.com", used_by=self.uid_a)
        _, body, _ = self.get("/api/admin/invites", cookies={"mai_session": self.tokAdm})
        row = next(i for i in body["invites"] if i["ref"] == self.store.invite_ref("spent"))
        self.assertEqual(row["status"], "used")
        self.assertEqual(row["left"], 0)

    def test_an_expired_invite_says_so(self):
        self.store.add_invite("stale", expired=True)
        _, body, _ = self.get("/api/admin/invites", cookies={"mai_session": self.tokAdm})
        row = next(i for i in body["invites"] if i["ref"] == self.store.invite_ref("stale"))
        self.assertEqual(row["status"], "expired")

    def test_a_used_up_invite_that_also_expired_reads_as_used(self):
        """เรียงลำดับการตัดสิน: "ใช้ครบแล้ว" คือเรื่องที่เกิดขึ้นจริง หมดอายุเป็นผลพลอยได้."""
        self.store.add_invite("both", email="y@example.com", used_by=self.uid_a,
                              expired=True)
        _, body, _ = self.get("/api/admin/invites", cookies={"mai_session": self.tokAdm})
        row = next(i for i in body["invites"] if i["ref"] == self.store.invite_ref("both"))
        self.assertEqual(row["status"], "used")


class TestTheStatusRuleItself(unittest.TestCase):
    """เรียก `pgstore.invite_status()` ของจริง ไม่ใช่สำเนาใน FakeStore.

    เทสต์ข้างบนทุกข้อเดินผ่าน FakeStore ซึ่งมีตรรกะสถานะของตัวเอง — กลับกลไกใน
    pgstore แล้วสวีทยังเขียว (เจอตอนกลับกลไกจริง) ตรงนี้คือจุดที่ผูกกับของที่ shipped
    """

    NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    def test_open_while_quota_is_left_and_not_expired(self):
        self.assertEqual(
            pgstore.invite_status(4, 30, self.NOW + timedelta(days=5), self.NOW), "open")

    def test_used_when_the_quota_runs_out(self):
        self.assertEqual(
            pgstore.invite_status(30, 30, self.NOW + timedelta(days=5), self.NOW), "used")

    def test_expired_when_the_date_has_passed(self):
        self.assertEqual(
            pgstore.invite_status(0, 1, self.NOW - timedelta(seconds=1), self.NOW),
            "expired")

    def test_used_wins_over_expired(self):
        self.assertEqual(
            pgstore.invite_status(1, 1, self.NOW - timedelta(days=9), self.NOW), "used")

    def test_an_invite_with_no_expiry_never_expires(self):
        self.assertEqual(pgstore.invite_status(0, 1, None, self.NOW), "open")

    def test_the_server_accepts_exactly_the_ref_length_the_store_emits(self):
        """สองค่านี้อยู่คนละไฟล์ ถ้าเลื่อนไม่พร้อมกัน ปุ่มยกเลิกจะตอบ 400 ทุกใบ."""
        m = re.search(r"_INVITE_REF_RE = re\.compile\(r\"\[0-9a-f\]\{(\d+)\}\"\)",
                      SERVER_PY)
        self.assertIsNotNone(m, "ไม่เจอ regex ของตัวชี้ในเซิร์ฟเวอร์")
        self.assertEqual(int(m.group(1)), pgstore.INVITE_REF_LEN)


class TestRevoking(CloudCase):
    def test_an_admin_can_revoke(self):
        code = self.store.create_invite(self.uid_adm, None, max_uses=5)
        ref = self.store.invite_ref(code)
        status, body, _ = self.delete(f"/api/admin/invites/{ref}",
                                      cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("ok"))

    def test_the_revoked_code_stops_working(self):
        """ของจริงที่ต้องการ: ใบที่หลุดมือต้องตัดทางเข้าได้ ไม่ใช่แค่หายจากตาราง."""
        code = self.store.create_invite(self.uid_adm, None, max_uses=5)
        self.delete(f"/api/admin/invites/{self.store.invite_ref(code)}",
                    cookies={"mai_session": self.tokAdm})
        ok, _ = self.store.invite_email(code)
        self.assertFalse(ok)

    def test_an_ordinary_member_cannot_revoke(self):
        code = self.store.create_invite(self.uid_adm, None)
        status, _, _ = self.delete(f"/api/admin/invites/{self.store.invite_ref(code)}",
                                   cookies={"mai_session": self.tokA})
        self.assertEqual(status, 403)
        self.assertTrue(self.store.invite_email(code)[0])

    def test_a_malformed_ref_is_rejected_before_the_store(self):
        status, _, _ = self.delete("/api/admin/invites/not-a-ref",
                                   cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 400)

    def test_an_unknown_ref_is_a_404_not_a_silent_ok(self):
        status, _, _ = self.delete("/api/admin/invites/" + ("0" * 16),
                                   cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 404)

    def test_revoking_is_written_to_the_log(self):
        """ตัดทางเข้าของคนที่กำลังรออยู่ ต้องตอบได้ว่าใครทำและเมื่อไหร่."""
        from meeting_ai.web import server as srv
        code = self.store.create_invite(self.uid_adm, None)
        with mock.patch.object(srv.log, "info") as spy:
            self.delete(f"/api/admin/invites/{self.store.invite_ref(code)}",
                        cookies={"mai_session": self.tokAdm})
        said = " ".join(str(c) for c in spy.call_args_list)
        self.assertIn("[invite-revoke]", said)


class TestFileModeHasNoSuchThing(LocalCase):
    def test_the_listing_is_404_not_500(self):
        status, _, _ = self.get("/api/admin/invites")
        self.assertEqual(status, 404)

    def test_revoking_is_404_too(self):
        status, _, _ = self.delete("/api/admin/invites/" + ("a" * 16))
        self.assertEqual(status, 404)


class TestThePage(unittest.TestCase):
    def test_there_is_a_way_in(self):
        self.assertIn('id="btn-invites"', APP_JS)
        self.assertRegex(APP_JS, r"invs\.onclick = \(\) => showInvites\(\)")

    def test_the_hash_route_exists(self):
        self.assertRegex(APP_JS, r"h === '#invites'\) showInvites\(\)")

    def test_the_narrow_screen_bar_has_a_title_and_a_way_back(self):
        m = re.search(r"invites: \{([^}]*)\}", APP_JS)
        self.assertIsNotNone(m, "ไม่มีหน้านี้ใน VIEW_BAR จอแคบจะไม่มีปุ่มย้อนกลับ")
        self.assertIn("back: true", m.group(1))

    def test_the_expiry_column_does_not_use_the_ago_helper(self):
        """sinceText() คืนค่าว่างสำหรับเวลาในอนาคต = ทุกแถวที่ยังใช้ได้ขึ้น "—"."""
        body = fn("function renderInvites(")
        cell = next(l for l in body.split(chr(10)) if "i.expires_at" in l)
        self.assertIn("untilText(", cell)
        self.assertNotIn("sinceText(", cell)

    def test_until_text_handles_future_past_and_today(self):
        body = fn("function untilText(")
        for word in ("หมดแล้ว", "วันนี้", "พรุ่งนี้"):
            self.assertIn(word, body)
        self.assertIn("Date.now()", body)

    def test_revoking_asks_first(self):
        """กดพลาดแล้วคนที่ถือลิงก์อยู่เข้าไม่ได้ทันที — ถอยกลับไม่ได้."""
        body = fn("async function revokeInvite(")
        ask = body.index("confirm(")
        send = body.index("api(")
        self.assertLess(ask, send, "ยิง DELETE ก่อนถาม")
        self.assertIn('method: "DELETE"', body)

    def test_the_handler_is_bound_to_the_table_not_each_button(self):
        body = fn("function renderInvites(")
        m = re.search(r"table\.onclick = \(e\) => \{(.*?)\n  \};", body, re.S)
        self.assertIsNotNone(m, "ไม่ได้ผูก onclick ไว้ที่ตาราง")
        self.assertIn("revokeInvite(", m.group(1))

    def test_the_ref_rides_on_the_button_not_the_row_index(self):
        """เรียงลำดับเปลี่ยนเมื่อไหร่ ปุ่มที่ชี้ด้วยลำดับจะยกเลิกผิดใบ."""
        body = fn("function renderInvites(")
        self.assertIn("data-ref=", body)
        self.assertIn("btn.dataset.ref", body)


if __name__ == "__main__":
    unittest.main()
