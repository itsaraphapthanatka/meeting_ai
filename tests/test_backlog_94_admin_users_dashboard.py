"""BACKLOG #94 — หน้า dashboard แสดงผู้ที่สมัครเข้ามา.

`/api/admin/users` คืน **ข้อมูลส่วนบุคคลของคนทั้งระบบ** (อีเมล ชื่อ เวลาสมัคร เวลาเข้าใช้
ล่าสุด ใครเป็นคนเชิญ) ซึ่งเป็นของที่หลุดแล้วเอาคืนไม่ได้ เทสต์ส่วนใหญ่ในไฟล์นี้จึงเป็น
เรื่องสิทธิ์ ไม่ใช่เรื่องหน้าตา:

* คนที่ยังไม่ล็อกอิน → 401
* คนที่ล็อกอินแต่ไม่ใช่แอดมิน → 403 และ **ต้องไม่มีอีเมลของใครหลุดไปในคำตอบ**
* คนถือลิงก์แชร์ → ต้องไม่ผ่าน (ลิงก์แชร์เปิดได้เฉพาะเส้นใน `_share_may_call`)
* โหมดไฟล์ที่ไม่มีระบบบัญชี → 404 ไม่ใช่ 500

ที่เหลือคือตรึงว่าตัวเลขสรุปนับถูก และหน้าเว็บไม่ได้พึ่งการซ่อนปุ่มเป็นด่านความปลอดภัย
"""

from __future__ import annotations

import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _harness import CloudCase, LocalCase          # type: ignore

from meeting_ai.web.server import _users_summary   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "meeting_ai" / "web" / "static" / "style.css").read_text(encoding="utf-8")
SERVER = (ROOT / "meeting_ai" / "web" / "server.py").read_text(encoding="utf-8")


def iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


class TestWhoMayRead(CloudCase):
    """ด่านสิทธิ์ — ข้อที่เหลือไม่มีความหมายถ้าข้อนี้พัง."""

    PATH = "/api/admin/users"

    def test_anonymous_is_rejected(self):
        status, body, _ = self.get(self.PATH)
        self.assertEqual(status, 401)
        self.assertNotIn("@example.com", json.dumps(body, ensure_ascii=False))

    def test_a_logged_in_non_admin_is_rejected(self):
        status, body, _ = self.get(self.PATH, cookies={"mai_session": self.tokA})
        self.assertEqual(status, 403)

    def test_the_rejection_leaks_nobody(self):
        """403 ที่แนบรายชื่อมาด้วย = รั่วทั้งที่ปฏิเสธ."""
        _, body, _ = self.get(self.PATH, cookies={"mai_session": self.tokA})
        blob = json.dumps(body, ensure_ascii=False)
        for leak in ("a@example.com", "b@example.com", "admin@example.com", "users"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, blob)

    def test_a_share_link_holder_is_rejected(self):
        status, _, _ = self.get(self.PATH, cookies={"mai_share": self.shr1})
        self.assertIn(status, (401, 403))

    def test_an_admin_gets_the_list(self):
        status, body, _ = self.get(self.PATH, cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        self.assertIn("users", body)
        self.assertIn("summary", body)

    def test_it_is_not_in_the_public_allow_list(self):
        self.assertNotIn('("admin", "users")', SERVER)


class TestWhatTheAdminSees(CloudCase):

    def test_everyone_who_signed_up_is_listed(self):
        _, body, _ = self.get("/api/admin/users", cookies={"mai_session": self.tokAdm})
        emails = {u["email"] for u in body["users"]}
        self.assertEqual(emails, {"a@example.com", "b@example.com", "admin@example.com"})

    def test_it_counts_the_meetings_each_person_owns(self):
        _, body, _ = self.get("/api/admin/users", cookies={"mai_session": self.tokAdm})
        owned = {u["email"]: u["meetings"] for u in body["users"]}
        self.assertEqual(owned["a@example.com"], 1)     # M1
        self.assertEqual(owned["b@example.com"], 1)     # M2
        self.assertEqual(owned["admin@example.com"], 0)

    def test_it_never_sends_the_password_hash(self):
        """FakeStore เก็บ password_hash ไว้ในของผู้ใช้ — ห้ามหลุดออกไปกับรายการ."""
        _, body, _ = self.get("/api/admin/users", cookies={"mai_session": self.tokAdm})
        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("password_hash", blob)
        self.assertNotIn("seeded-for-test", blob)
        self.assertNotIn("password_salt", blob)

    def test_the_summary_counts_match_the_rows(self):
        _, body, _ = self.get("/api/admin/users", cookies={"mai_session": self.tokAdm})
        self.assertEqual(body["summary"]["total"], len(body["users"]))
        self.assertEqual(body["summary"]["admins"],
                         sum(1 for u in body["users"] if u["is_admin"]))


class TestTheFileModeHasNoSuchThing(LocalCase):
    """โหมดไฟล์ไม่มีตาราง users เลย — ต้องตอบว่าไม่มีเส้นนี้ ไม่ใช่ 500 จาก AttributeError."""

    def test_it_answers_not_found_rather_than_blowing_up(self):
        status, body, _ = self.get("/api/admin/users")
        self.assertEqual(status, 404)
        self.assertNotIn("Traceback", json.dumps(body, ensure_ascii=False))


class TestTheSummaryArithmetic(unittest.TestCase):
    """นับจากรายการที่ส่งไปอยู่แล้ว ไม่ยิง query เพิ่ม — จึงทดสอบเป็นฟังก์ชันล้วนได้."""

    def rows(self):
        return [
            {"created_at": iso(1), "is_admin": True, "last_login": iso(0), "meetings": 3},
            {"created_at": iso(10), "is_admin": False, "last_login": None, "meetings": 0},
            {"created_at": iso(200), "is_admin": False, "last_login": iso(150), "meetings": 1},
            {"created_at": None, "is_admin": False, "last_login": None, "meetings": 0},
        ]

    def test_the_windows_do_not_overlap_wrongly(self):
        out = _users_summary(self.rows())
        self.assertEqual(out["new_7d"], 1)
        self.assertEqual(out["new_30d"], 2, "คนที่สมัคร 10 วันก่อนต้องนับอยู่ในช่วง 30 วันด้วย")

    def test_people_who_never_came_back_are_counted(self):
        self.assertEqual(_users_summary(self.rows())["never_logged_in"], 2)

    def test_a_missing_signup_date_does_not_crash_or_get_counted(self):
        out = _users_summary(self.rows())
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["new_7d"] + 3, 4)

    def test_an_unparsable_date_is_ignored_not_fatal(self):
        out = _users_summary([{"created_at": "เมื่อวาน", "last_login": None}])
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["new_7d"], 0)

    def test_empty_system(self):
        self.assertEqual(_users_summary([])["total"], 0)

    def test_it_compares_against_utc_not_the_local_clock(self):
        """เคยพลาดมาแล้วตอนรายงานว่างานค้าง 17 ชม. เพราะเทียบ UTC กับเวลาไทย (+07)."""
        body = SERVER[SERVER.index("def _users_summary"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("datetime.now(timezone.utc)", body)
        self.assertIn("tzinfo=timezone.utc", body)


class TestTheFrontEndDoesNotRelyOnHidingTheButton(unittest.TestCase):

    def test_the_button_is_admin_only_but_so_is_the_endpoint(self):
        self.assertIn("isAdmin() ? '<button id=\"btn-users\"", APP_JS)
        gate = SERVER[SERVER.index('parts == ["admin", "users"]'):]
        gate = gate[:gate.index("store.users_list()")]
        self.assertIn('self.user.get("is_admin")', gate)

    def test_the_route_works_on_its_own(self):
        """ส่งลิงก์ /#users ให้แอดมินอีกคนได้ตรง ๆ."""
        self.assertIn("else if (h === '#users') showUsers();", APP_JS)

    def test_the_mobile_bar_names_the_page(self):
        self.assertIn("users:   { title: 'ผู้ใช้ที่สมัครเข้ามา', back: true },", APP_JS)

    def test_a_narrow_screen_gives_the_page_the_whole_width(self):
        self.assertIn('body[data-view="users"] .sidebar', CSS)

    def test_the_table_scrolls_inside_itself_on_a_phone(self):
        """ห้าคอลัมน์บนจอ 375px ถ้าไม่คุมจะดันทั้งหน้าให้เลื่อนแนวนอน."""
        # มี @media (max-width: 760px) หลายก้อนในไฟล์ (คู่มือ #91 ก็มี) — หาจากกฎของ
        # .utable เองดีกว่าไล่จากก้อนแรกที่เจอ ซึ่งเคยทำให้เทสต์นี้ล้มทั้งที่ CSS ถูก
        blocks = re.findall(r"\.utable \{([^}]*)\}", CSS)
        self.assertTrue(blocks, "ไม่เจอกฎของ .utable เลย")
        self.assertTrue(any("overflow-x: auto" in b for b in blocks),
                        "ตารางไม่ได้ถูกสั่งให้เลื่อนในกรอบตัวเอง")

    def test_everything_shown_is_escaped(self):
        """ชื่อและอีเมลมาจากสิ่งที่ผู้ใช้พิมพ์เอง — ต้องผ่าน esc() ทุกจุด."""
        body = APP_JS[APP_JS.index("function renderUsers("):]
        body = body[:body.index("\nfunction ", 10)]
        for field in ("u.name", "u.email", "u.invited_by"):
            with self.subTest(field=field):
                self.assertRegex(body, re.escape(f"esc({field})"))
        # ตัวเลขผ่าน Number() ไม่ใช่ esc() — กันสตริงแปลกปลอมไปโผล่เป็น markup
        self.assertIn("Number(u.meetings || 0)", body)

    def test_people_who_never_logged_in_stand_out(self):
        body = APP_JS[APP_JS.index("function renderUsers("):]
        body = body[:body.index("\nfunction ", 10)]
        self.assertIn("const cold = !u.last_login;", body)
        self.assertIn("u-cold", body)
        self.assertIn("var(--warn)", CSS[CSS.index(".u-never"):CSS.index(".u-never") + 400])


if __name__ == "__main__":
    unittest.main()
