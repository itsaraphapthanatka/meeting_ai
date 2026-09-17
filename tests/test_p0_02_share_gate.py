"""BACKLOG #2 — คุกกี้ mai_share เดิมผ่านด่านล็อกอินได้ทั้ง API ไม่ใช่แค่การประชุมที่แชร์ให้.

ก่อนแก้: `if backend.auth_required() and not self.user and tuple(parts) not in PUBLIC_API:
if not self.share: return 401` — มีคุกกี้แชร์ใบเดียวก็ผ่านด่านไปเปิด /api/jobs, /api/workers,
สร้างการประชุมใหม่ ฯลฯ ได้หมด
หลังแก้: ต้องผ่าน _share_may_call(parts) ด้วย ซึ่งอนุญาตเฉพาะ GET /api/meetings,
GET /api/jobs(/{id}) และ /api/meetings/{mid ที่แชร์ให้}/...
"""

from _harness import CloudCase


class TestShareGate(CloudCase):
    def test_share_sees_only_its_own_meeting_and_its_jobs(self):
        status, body, _ = self.get("/api/meetings", cookies={"mai_share": self.shr1})
        self.assertEqual(status, 200)
        self.assertEqual([m["id"] for m in body["meetings"]], [self.M1])
        job_ids = {j["id"] for j in body["jobs"]}
        self.assertEqual(job_ids, {self.M1_tr_en})
        self.assertNotIn(self.J_A, job_ids)
        self.assertNotIn(self.J_B, job_ids)

    def test_share_cookie_denied_on_system_wide_and_unrelated_routes(self):
        cookies = {"mai_share": self.shr1}
        cases = [
            ("GET", "/api/workers", None),
            ("POST", "/api/live?ext=webm", b""),
            ("POST", "/api/settings", b"{}"),
            ("POST", "/api/meetings", b"{}"),
            ("POST", "/api/meetings/bot", b"{}"),
            ("GET", f"/api/meetings/{self.M2}", None),
        ]
        for method, path, data in cases:
            status, _, _ = self._do(method, path, data=data, cookies=cookies,
                                    content_type="application/json" if data else None)
            self.assertEqual(status, 401, f"{method} {path} ควรเป็น 401")

    def test_share_jobs_endpoint_scoped_and_no_workers_key(self):
        status, body, _ = self.get("/api/jobs", cookies={"mai_share": self.shr1})
        self.assertEqual(status, 200)
        self.assertEqual({j["id"] for j in body["jobs"]}, {self.M1_tr_en})
        self.assertNotIn("workers", body)
        self.assertEqual(self.store.job_active_calls[-1], (None, self.M1))

    def test_share_read_only_cannot_translate_or_see_share_links(self):
        cookies = {"mai_share": self.shr1}
        status, _, _ = self.post_json(f"/api/meetings/{self.M1}/translate", {"lang": "en"},
                                      cookies=cookies)
        self.assertEqual(status, 403)
        status, _, _ = self.get(f"/api/meetings/{self.M1}/share", cookies=cookies)
        self.assertEqual(status, 403)

    def test_share_can_edit_may_translate_and_poll_the_new_job(self):
        cookies = {"mai_share": self.shr2}
        status, body, _ = self.post_json(f"/api/meetings/{self.M1}/translate", {"lang": "en"},
                                         cookies=cookies)
        self.assertEqual(status, 202)
        status, body, _ = self.get(f"/api/jobs/{self.M1_tr_en}", cookies=cookies)
        self.assertEqual(status, 200)

    def test_share_entry_serves_the_page_without_planting_the_cookie(self):
        """BACKLOG #16 เปลี่ยนสัญญาข้อนี้: เดิมเทสต์นี้ยืนยันว่า GET /s/<token> ตั้งคุกกี้ให้เลย
        ซึ่งคือช่อง cookie fixation เอง — คุกกี้ย้ายไปตั้งที่ POST /api/auth/share หลังผู้ใช้
        กดยืนยัน (รายละเอียดและเคสล้มเหลวอยู่ใน tests/test_bug_016_share_cookie_confirm.py)"""
        status, _, headers = self.get(f"/s/{self.shr1}")
        self.assertEqual(status, 200)
        self.assertNotIn("mai_share", headers.get("Set-Cookie", ""))

    def test_auth_me_with_share_cookie_has_no_user(self):
        status, body, _ = self.get("/api/auth/me", cookies={"mai_share": self.shr1})
        self.assertEqual(status, 200)
        self.assertIsNone(body["user"])
        self.assertEqual(body["share"]["meeting_id"], self.M1)

    def test_logged_in_user_with_stale_share_cookie_sees_own_jobs_and_workers(self):
        cookies = {"mai_session": self.tokA, "mai_share": self.shr1}
        status, body, _ = self.get("/api/jobs", cookies=cookies)
        self.assertEqual(status, 200)
        self.assertEqual({j["id"] for j in body["jobs"]}, {self.J_A})
        self.assertIn("workers", body)

    def test_config_needs_no_cookie(self):
        status, _, _ = self.get("/api/config")
        self.assertEqual(status, 200)

    # ---------- Round 2: share-edit ไม่มี user_id ของตัวเอง ต้องตกไปใช้เจ้าของการประชุม ----------

    def test_share_edit_translate_job_owner_falls_back_to_meeting_owner(self):
        """คนถือลิงก์แชร์แบบแก้ได้ (shr2) ไม่ได้ล็อกอิน self.user_id == None

        เดิมถ้าส่ง owner_id=None ตรงๆ งานแปลจะไม่มีเจ้าของ แล้วหลุดตัวกรอง owner_id ของ
        /api/jobs ไปโผล่ในคิวของทุกคน (ย้อนกลับไปเป็น BACKLOG #3) หลังแก้ server.py ใช้
        `self.user_id or meeting.get("owner_id")` — ต้องพิสูจน์ว่า meeting.get("owner_id")
        มีค่าจริง (ไม่ใช่ผ่านลมๆ เพราะ FakeStore.get() ไม่ใส่คีย์นี้มาแต่ต้น)
        """
        status, _, _ = self.post_json(f"/api/meetings/{self.M1}/translate", {"lang": "en"},
                                      cookies={"mai_share": self.shr2})
        self.assertEqual(status, 202)
        job_id = f"{self.M1}.tr.en"
        self.assertEqual(self.store.jobs[job_id]["spec"]["owner_id"], self.uid_a)

        # เจ้าของการประชุมต้องเห็นงานนี้ใน /api/jobs ของตัวเอง (ไม่ใช่งานไม่มีเจ้าของที่หายไป)
        status, body, _ = self.get("/api/jobs", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        self.assertIn(job_id, {j["id"] for j in body["jobs"]})

    def test_share_edit_resummarize_job_owner_falls_back_to_meeting_owner(self):
        status, _, _ = self.post_json(f"/api/meetings/{self.M1}/resummarize",
                                      cookies={"mai_share": self.shr2})
        self.assertEqual(status, 202)
        self.assertEqual(self.store.jobs[self.M1]["spec"]["owner_id"], self.uid_a)

        status, body, _ = self.get("/api/jobs", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        self.assertIn(self.M1, {j["id"] for j in body["jobs"]})


if __name__ == "__main__":
    import unittest
    unittest.main()
