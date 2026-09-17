"""BACKLOG #16 / BUG-016 — `GET /s/<token>` ตั้งคุกกี้ mai_share ให้เองตั้งแต่เปิด URL.

ก่อนแก้ (วัดจริงด้วย PoC): `GET /s/<token>` แบบไม่มีคุกกี้อะไรเลย → 200 +
`Set-Cookie: mai_share=<token>; Max-Age=604800; HttpOnly; SameSite=Lax; Path=/`
เท่ากับ "เว็บไหนก็ยัดโทเคนแชร์ของตัวเองเข้าเบราว์เซอร์คนอื่นได้" ด้วยลิงก์/redirect/window.open
(SameSite=Lax ไม่กัน top-level navigation) หลังจากนั้น `/api/auth/me` รายงาน share ของคนยัดให้
แม้เหยื่อจะล็อกอินอยู่ และ `app.js` ก็เด้งไปเปิดการประชุมของคนยัดทุกครั้งที่โหลดหน้า
(`if (state.share) return openMeeting(state.share.meeting_id)`)

หลังแก้: `GET /s/<token>` เหลือหน้าที่แค่ "ตรวจว่าโทเคนใช้ได้แล้วเสิร์ฟหน้าเว็บ" ไม่ตั้งคุกกี้
คุกกี้ตั้งได้ทางเดียวคือ `POST /api/auth/share` (Content-Type: application/json) ซึ่งหน้าเว็บ
ยิงให้หลังผู้ใช้กดปุ่มยืนยันเท่านั้น
"""

from __future__ import annotations

import unittest

from _harness import CloudCase, LocalCase


class TestShareEntryNoLongerSetsCookie(CloudCase):

    def test_get_share_entry_sets_no_cookie(self):
        status, _, headers = self.get(f"/s/{self.shr1}")
        self.assertEqual(status, 200)
        self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))

    def test_get_share_entry_still_serves_the_spa_shell(self):
        status, body, headers = self.get(f"/s/{self.shr1}")
        self.assertEqual(status, 200)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/html"))
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertIn(b"<html", bytes(body).lower())

    def test_unknown_token_still_404(self):
        status, _, headers = self.get("/s/does-not-exist")
        self.assertEqual(status, 404)
        self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))


class TestShareAcceptEndpoint(CloudCase):

    def test_post_confirm_sets_the_cookie(self):
        status, body, headers = self.post_json("/api/auth/share", {"token": self.shr1})
        self.assertEqual(status, 200)
        self.assertEqual(body["share"]["meeting_id"], self.M1)
        self.assertFalse(body["share"]["can_edit"])
        cookie = headers.get("Set-Cookie", "")
        self.assertIn(f"mai_share={self.shr1}", cookie)
        # แอตทริบิวต์ต้องเหมือนของเดิมทุกตัว (ไม่ได้ผ่อนอะไรตอนย้ายจุดตั้งคุกกี้)
        for flag in ("HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=604800"):
            self.assertIn(flag, cookie)

    def test_cookie_from_confirm_actually_works(self):
        _, _, headers = self.post_json("/api/auth/share", {"token": self.shr1})
        token = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
        status, body, _ = self.get("/api/auth/me", cookies={"mai_share": token})
        self.assertEqual(status, 200)
        self.assertIsNone(body["user"])
        self.assertEqual(body["share"]["meeting_id"], self.M1)

    def test_secure_flag_when_behind_https_proxy(self):
        _, _, headers = self.post_json("/api/auth/share", {"token": self.shr1},
                                       headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", headers.get("Set-Cookie", ""))

    def test_can_edit_share_is_reported(self):
        status, body, _ = self.post_json("/api/auth/share", {"token": self.shr2})
        self.assertEqual(status, 200)
        self.assertTrue(body["share"]["can_edit"])

    # ---------- เส้นทางล้มเหลว ----------

    def test_unknown_token_404_and_no_cookie(self):
        status, _, headers = self.post_json("/api/auth/share", {"token": "nope"})
        self.assertEqual(status, 404)
        self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))

    def test_missing_or_empty_token_404_and_no_cookie(self):
        for body in ({}, {"token": ""}, {"token": "   "}, {"token": None}, {"token": 123}):
            with self.subTest(body=body):
                status, _, headers = self.post_json("/api/auth/share", body)
                self.assertEqual(status, 404)
                self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))

    def test_non_json_content_type_is_rejected(self):
        """กันฟอร์มข้ามเว็บ: ฟอร์มส่งได้แค่ urlencoded/plain/multipart เท่านั้น."""
        payload = f'{{"token": "{self.shr1}"}}'.encode("utf-8")
        for ctype in ("text/plain", "application/x-www-form-urlencoded",
                      "multipart/form-data; boundary=x", ""):
            with self.subTest(ctype=ctype):
                status, _, headers = self.post_raw("/api/auth/share", payload,
                                                   content_type=ctype or "text/plain")
                self.assertEqual(status, 415)
                self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))

    def test_get_on_share_endpoint_is_not_a_route(self):
        status, _, headers = self.get("/api/auth/share?token=" + self.shr1)
        self.assertEqual(status, 404)
        self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))

    def test_broken_json_body_is_400_not_500(self):
        status, _, _ = self.post_raw("/api/auth/share", b"{not json",
                                     content_type="application/json")
        self.assertEqual(status, 400)


class TestShareAcceptInFileMode(LocalCase):
    """โหมดไฟล์ไม่มีระบบล็อกอิน/แชร์เลย — ต้องตอบ 501 ตามเส้นทาง _auth_api เดิม ไม่ใช่ 500."""

    def test_file_mode_returns_501(self):
        status, _, headers = self.post_json("/api/auth/share", {"token": "x"})
        self.assertEqual(status, 501)
        self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))


if __name__ == "__main__":
    unittest.main()
