"""scripts/invite_from_csv.py — ออกรหัสเชิญจากไฟล์ HR โดยข้ามคนที่มีบัญชีแล้ว.

โหลดไฟล์จริงมารัน ไม่ใช่สำเนาตรรกะ (กติกาข้อ 4 ของ CLAUDE.md) และยกเซิร์ฟเวอร์ปลอม
ที่เลียนสัญญาของ 3 endpoint ขึ้นในเธรด — สคริปต์นี้ถูกออกแบบมาให้ยิงใส่ production
เทสต์จึงต้องพิสูจน์ "ไม่ยิงตอนซ้อม" ให้ได้ โดยไม่แตะ production เอง
"""
from __future__ import annotations

import importlib.util
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "invite_from_csv.py"
_spec = importlib.util.spec_from_file_location("invite_from_csv", _SRC)
inv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(inv)

CSV = """work_email,full_name,nickname,department_code,role
a@x.co,หนึ่ง,เอ,IT,staff
B@x.co,สอง,บี,IT,staff
b@x.co,สองซ้ำ,บีซ้ำ,IT,staff
,สาม,ซี,House Keeper,staff
c@x.co,สี่,ดี,Media,staff
"""


class _Stub(BaseHTTPRequestHandler):
    """สัญญาเดียวกับ server.py: login ตั้งคุกกี้ · users/invite ต้องมีคุกกี้ + is_admin."""

    users = [{"id": "u1", "email": "Admin@x.co", "is_admin": True},
             {"id": "u2", "email": "A@x.co", "is_admin": False}]
    issued: list[str] = []
    admin = True
    set_cookie = True

    def log_message(self, *a):
        pass

    def _json(self, code, obj, cookie=None):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _authed(self):
        return "mai_session=tok-abcdef12" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        if self.path == "/api/auth/me":
            if not self._authed():
                return self._json(200, {"user": None, "first_run": False})
            return self._json(200, {"user": {"id": "u1", "email": "Admin@x.co",
                                             "is_admin": type(self).admin}})
        if self.path == "/api/admin/users":
            if not self._authed():
                return self._json(403, {"error": "ต้องเป็นแอดมิน"})
            return self._json(200, {"users": type(self).users})
        self._json(404, {"error": "ไม่พบ"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/auth/login":
            if body.get("password") != "pw":
                return self._json(401, {"error": "อีเมลหรือรหัสผ่านไม่ถูกต้อง"})
            cookie = "mai_session=tok-abcdef12; Path=/; HttpOnly" if type(self).set_cookie else None
            return self._json(200, {"user": {"id": "u1", "email": body["email"],
                                             "is_admin": type(self).admin}}, cookie=cookie)
        if self.path == "/api/auth/invite":
            if not self._authed():
                return self._json(403, {"error": "ต้องเป็นแอดมิน"})
            type(self).issued.append(body.get("email"))
            return self._json(200, {"code": f"code{len(type(self).issued)}"})
        self._json(404, {"error": "ไม่พบ"})


class TestReadCsv(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "hr.csv"
        with io.open(self.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write(CSV)

    def test_emails_are_lowercased(self):
        """เซิร์ฟเวอร์ lower() ก่อนเทียบ ถ้าฝั่งนี้ไม่ทำ คนเดิมจะได้รหัสซ้ำ."""
        rows = inv.read_csv_emails(self.csv)
        self.assertIn("b@x.co", [r["email"] for r in rows])
        self.assertNotIn("B@x.co", [r["email"] for r in rows])

    def test_a_repeated_email_is_kept_once(self):
        emails = [r["email"] for r in inv.read_csv_emails(self.csv)]
        self.assertEqual(emails.count("b@x.co"), 1)

    def test_a_row_without_an_email_is_reported_not_dropped(self):
        """แม่บ้านไม่มีอีเมลบริษัท — ต้องรู้ว่าใครตกหล่น ไม่ใช่หายไปเงียบ ๆ."""
        blanks = [r for r in inv.read_csv_emails(self.csv) if not r["email"]]
        self.assertEqual(len(blanks), 1)
        self.assertEqual(blanks[0]["name"], "สาม")

    def test_the_other_columns_come_along(self):
        first = inv.read_csv_emails(self.csv)[0]
        self.assertEqual((first["name"], first["nickname"], first["dept"]),
                         ("หนึ่ง", "เอ", "IT"))


class TestSplit(unittest.TestCase):
    ROWS = [{"email": "a@x.co", "name": "", "nickname": "", "dept": ""},
            {"email": "", "name": "ไม่มีอีเมล", "nickname": "", "dept": ""},
            {"email": "c@x.co", "name": "", "nickname": "", "dept": ""}]

    def test_an_existing_account_is_skipped(self):
        todo, have, _ = inv.split_rows(self.ROWS, {"a@x.co"})
        self.assertEqual([r["email"] for r in todo], ["c@x.co"])
        self.assertEqual([r["email"] for r in have], ["a@x.co"])

    def test_the_blank_row_is_in_its_own_bucket(self):
        todo, have, noemail = inv.split_rows(self.ROWS, set())
        self.assertEqual(len(noemail), 1)
        self.assertNotIn("", [r["email"] for r in todo + have])


class _ServerCase(unittest.TestCase):
    def setUp(self):
        _Stub.issued = []
        _Stub.admin = True
        _Stub.set_cookie = True
        _Stub.users = [{"id": "u1", "email": "Admin@x.co", "is_admin": True},
                       {"id": "u2", "email": "A@x.co", "is_admin": False}]
        self.srv = HTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = Path(self.tmp.name) / "hr.csv"
        with io.open(self.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write(CSV)
        self.out = Path(self.tmp.name) / "out.csv"
        p = mock.patch.object(inv.getpass, "getpass", return_value="pw")
        p.start()
        self.addCleanup(p.stop)

    def run_main(self, *extra):
        with mock.patch("sys.stdout", io.StringIO()) as out:
            rc = inv.main([str(self.csv), "--base", self.base,
                           "--admin", "admin@x.co", *extra])
        return rc, out.getvalue()


class TestDryRunTouchesNothing(_ServerCase):
    def test_no_invite_is_issued_without_apply(self):
        """ค่าเริ่มต้นต้องไม่เขียนอะไรลง production — นี่คือเหตุผลที่ --apply มีอยู่."""
        rc, _ = self.run_main()
        self.assertEqual(rc, 0)
        self.assertEqual(_Stub.issued, [])

    def test_no_file_is_written_either(self):
        self.run_main("--out", str(self.out))
        self.assertFalse(self.out.exists())

    def test_it_says_who_would_get_one(self):
        _, said = self.run_main()
        self.assertIn("c@x.co", said)
        self.assertIn("b@x.co", said)


class TestApply(_ServerCase):
    def test_only_the_people_without_an_account_get_a_code(self):
        """a@x.co มีบัญชีแล้วในชื่อ A@x.co — ตัวพิมพ์ต่างกันต้องยังนับว่าคนเดียวกัน."""
        rc, _ = self.run_main("--apply", "--out", str(self.out))
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(_Stub.issued), ["b@x.co", "c@x.co"])

    def test_the_file_has_a_link_per_person(self):
        self.run_main("--apply", "--out", str(self.out))
        with io.open(self.out, encoding="utf-8-sig") as fh:
            lines = fh.read().strip().split(chr(10))
        self.assertEqual(len(lines), 3)          # หัวตาราง + 2 คน
        self.assertIn(f"{self.base}/?invite=code", lines[1])

    def test_the_code_never_reaches_the_screen(self):
        """รหัสอยู่ในไฟล์อย่างเดียว — กันรหัสติดไปกับสกรีนช็อต/log ที่ส่งต่อกัน."""
        _, said = self.run_main("--apply", "--out", str(self.out))
        self.assertNotIn("code1", said)
        self.assertNotIn("invite=", said)

    def test_everyone_already_has_an_account(self):
        _Stub.users = [{"id": f"u{i}", "email": e, "is_admin": False}
                       for i, e in enumerate(("a@x.co", "b@x.co", "c@x.co"))]
        rc, _ = self.run_main("--apply", "--out", str(self.out))
        self.assertEqual(rc, 0)
        self.assertEqual(_Stub.issued, [])
        self.assertFalse(self.out.exists())


class TestCookieLogin(_ServerCase):
    """ทางเลี่ยงรหัสผ่าน — ใช้ session ที่ล็อกอินค้างไว้ในเบราว์เซอร์.

    มีไว้เพราะของจริงเกิดแล้ว: เจ้าของรันแล้วได้ 401 จากหน้าล็อกอิน การไล่หาว่า
    รหัสผ่านตัวไหนถูกไม่ควรเป็นเงื่อนไขของการออกรหัสเชิญ
    """

    def run_cookie(self, value, *extra):
        out = io.StringIO()
        with mock.patch.object(inv.getpass, "getpass", return_value=value):
            with mock.patch("sys.stdout", out):
                rc = inv.main([str(self.csv), "--base", self.base, "--cookie", *extra])
        return rc, out.getvalue()

    def test_a_valid_cookie_replaces_the_password(self):
        rc, _ = self.run_cookie("tok-abcdef12", "--apply", "--out", str(self.out))
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(_Stub.issued), ["b@x.co", "c@x.co"])

    def test_an_expired_cookie_stops_before_issuing_anything(self):
        rc, said = self.run_cookie("tok-expired99", "--apply", "--out", str(self.out))
        self.assertEqual(rc, 1)
        self.assertEqual(_Stub.issued, [])
        self.assertIn("คุกกี้", said)

    def test_a_paste_that_is_not_a_cookie_value_says_so(self):
        """วางผิดเป็นภาษาไทยเคยพังเป็น UnicodeEncodeError ดิบ ๆ ที่ http.client

        — header เป็น latin-1 เท่านั้น ข้อความ error ที่ขึ้นหน้าจอเป็นของผู้ใช้
        ไม่ใช่ traceback ของ Python (กติกาข้อ 2 ของ CLAUDE.md)
        """
        with mock.patch.object(inv.getpass, "getpass", return_value="วางผิดมาเป็นภาษาไทย"):
            with mock.patch("sys.stdout", io.StringIO()):
                with self.assertRaises(SystemExit) as e:
                    inv.main([str(self.csv), "--base", self.base, "--cookie", "--apply"])
        self.assertIn("ค่าคุกกี้ไม่ถูกรูป", str(e.exception))
        self.assertEqual(_Stub.issued, [])

    def test_a_non_admin_session_still_cannot_issue(self):
        _Stub.admin = False
        rc, _ = self.run_cookie("tok-abcdef12", "--apply", "--out", str(self.out))
        self.assertEqual(rc, 1)
        self.assertEqual(_Stub.issued, [])

    def test_the_cookie_is_never_an_argv_value(self):
        """ค่าใน argv ติด history ของเชลล์และโผล่ในรายการโพรเซส.

        ต้องดูด้วยว่า *argparse* เป็นคนปฏิเสธ (exit code 2) ไม่ใช่แค่ "มี SystemExit"
        — ถ้า --cookie รับค่าได้ โปรแกรมก็ยังจบด้วย SystemExit จากตัวกรองค่าคุกกี้
        อยู่ดี เทสต์รุ่นแรกจึงเขียวทั้งที่กลไกถูกถอดออก (เจอตอนกลับกลไก)
        """
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            with self.assertRaises(SystemExit) as e:
                inv.main([str(self.csv), "--base", self.base, "--cookie", "tok-abcdef12"])
        self.assertEqual(e.exception.code, 2)
        self.assertIn("unrecognized arguments", err.getvalue())


class TestItRefusesWhenItShould(_ServerCase):
    def test_a_non_admin_cannot_issue_invites(self):
        _Stub.admin = False
        rc, _ = self.run_main("--apply", "--out", str(self.out))
        self.assertEqual(rc, 1)
        self.assertEqual(_Stub.issued, [])

    def test_a_login_without_a_session_cookie_is_not_a_login(self):
        """200 ไม่ได้แปลว่าล็อกอินผ่าน — ถ้าไม่มีคุกกี้ คำขอถัดไปจะโดน 403

        อยากให้พังตรงนี้พร้อมข้อความที่อ่านรู้เรื่อง ไม่ใช่ไปพังกลางทางตอนยิง
        /api/auth/invite ใบที่เท่าไหร่ก็ไม่รู้
        """
        _Stub.set_cookie = False
        with mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as e:
                inv.main([str(self.csv), "--base", self.base,
                          "--admin", "admin@x.co", "--apply"])
        self.assertIn("session", str(e.exception))
        self.assertEqual(_Stub.issued, [])

    def test_a_wrong_password_stops_before_anything_else(self):
        with mock.patch.object(inv.getpass, "getpass", return_value="nope"), \
             mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as e:
                inv.main([str(self.csv), "--base", self.base,
                          "--admin", "admin@x.co", "--apply"])
        self.assertIn("401", str(e.exception))
        self.assertEqual(_Stub.issued, [])

    def test_a_missing_csv_is_a_plain_message_not_a_traceback(self):
        with mock.patch("sys.stdout", io.StringIO()) as out:
            rc = inv.main(["ไม่มีไฟล์นี้.csv", "--base", self.base, "--admin", "a@x.co"])
        self.assertEqual(rc, 2)
        self.assertIn("ไม่พบไฟล์", out.getvalue())


if __name__ == "__main__":
    unittest.main()
