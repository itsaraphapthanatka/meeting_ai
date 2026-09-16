"""BACKLOG #1 — draft routes (tracks/upload-url, tracks/{name}, process) เดิมไม่เช็คเจ้าของ.

ก่อนแก้: ใครที่ล็อกอินอยู่ก็อัปโหลดแทร็ก/สั่งประมวลผล draft ของคนอื่นได้ถ้ารู้ id
(server.py _meeting() ข้ามตรงไปเรียก handler โดยเช็คแค่ store.valid_id())
หลังแก้: _meeting() เช็ค jobs.draft(mid) มีจริงไหม (404) แล้วเช็ค _may_write_draft() (403)
ก่อนจะเรียก _track_upload_url / _put_track / _start เส้นใดเส้นหนึ่ง
"""

from _harness import CloudCase, LocalCase, new_mid


class TestDraftOwnershipCloud(CloudCase):
    def _upload_url(self, mid, cookies=None):
        return self.get(f"/api/meetings/{mid}/tracks/mixed/upload-url?ext=wav", cookies=cookies)

    def _put_bytes(self, mid, cookies=None, n=16):
        return self.post_bytes(f"/api/meetings/{mid}/tracks/mixed?ext=wav", b"0" * n, cookies=cookies)

    def _process(self, mid, cookies=None):
        return self.post_json(f"/api/meetings/{mid}/process", cookies=cookies)

    # ---------- คนแปลกหน้า (ล็อกอินแต่ไม่ใช่เจ้าของ) ----------

    def test_stranger_cannot_get_upload_url(self):
        status, body, _ = self._upload_url(self.D_A, cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)

    def test_stranger_cannot_upload_track_and_nothing_written(self):
        status, body, _ = self._put_bytes(self.D_A, cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)
        # ต้องไม่มีไฟล์หลุดลงดิสก์เลยจากความพยายามของ B
        written = list(self.store.WEB_DIR.glob(f"{self.D_A}*"))
        self.assertEqual(written, [])
        self.assertEqual(self.store.jobs[self.D_A]["spec"]["tracks"], {})

    def test_stranger_cannot_start_process_status_stays_draft(self):
        # จำลองว่าเจ้าของอัปโหลดแทร็กไว้แล้ว ทดสอบว่า "process" ยังโดนกันแม้มีแทร็กพร้อม
        self.store.jobs[self.D_A]["spec"]["tracks"] = {"mixed": f"{self.D_A}.wav"}
        status, body, _ = self._process(self.D_A, cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)
        self.assertEqual(self.store.jobs[self.D_A]["status"], "draft")

    # ---------- ลิงก์แชร์ / ไม่ได้ล็อกอินเลย ----------

    def test_share_cookie_cannot_touch_unrelated_draft(self):
        cookies = {"mai_share": self.shr1}  # shr1 ผูกกับ M1 ไม่ใช่ D_A
        for status, _, _ in (
            self._upload_url(self.D_A, cookies=cookies),
            self._put_bytes(self.D_A, cookies=cookies),
            self._process(self.D_A, cookies=cookies),
        ):
            self.assertEqual(status, 401)

    def test_anonymous_cannot_touch_draft(self):
        for status, _, _ in (
            self._upload_url(self.D_A),
            self._put_bytes(self.D_A),
            self._process(self.D_A),
        ):
            self.assertEqual(status, 401)

    # ---------- เจ้าของทำได้ครบ flow ----------

    def test_owner_full_flow(self):
        cookies = {"mai_session": self.tokA}
        status, body, _ = self._upload_url(self.D_A, cookies=cookies)
        self.assertEqual(status, 200)
        self.assertIsNone(body["url"])  # ที่เก็บดิสก์ในเครื่อง = ไม่มี presigned url
        self.assertEqual(body["key"], f"{self.D_A}.wav")

        status, body, _ = self._put_bytes(self.D_A, cookies=cookies)
        self.assertEqual(status, 200)
        self.assertEqual(body["bytes"], 16)

        status, body, _ = self._process(self.D_A, cookies=cookies)
        self.assertEqual(status, 202)
        self.assertEqual(body["status"], "queued")

    # ---------- แอดมิน ----------

    def test_admin_may_touch_anyones_draft(self):
        cookies = {"mai_session": self.tokAdm}
        status, _, _ = self._upload_url(self.D_A, cookies=cookies)
        self.assertEqual(status, 200)
        status, _, _ = self._put_bytes(self.D_B, cookies=cookies)
        self.assertEqual(status, 200)

    # ---------- id ถูกรูปแบบแต่ไม่มี draft จริง ----------

    def test_unknown_valid_id_is_404(self):
        missing = new_mid()
        status, _, _ = self._upload_url(missing, cookies={"mai_session": self.tokA})
        self.assertEqual(status, 404)

    # ---------- Round 2: _draft_spec — งานที่ผ่านพ้น draft ไปแล้วไม่ใช่ draft อีกต่อไป ----------

    def test_draft_routes_404_once_job_left_draft_status(self):
        """เดิม _put_track/_track_upload_url เช็คแค่ jobs.draft(mid) มีอยู่ไหม — ไม่ดูสถานะ

        jobs.draft()/store.job_get()["_spec"] คืน spec ของแถวใน jobs ทุกสถานะ ไม่ใช่แค่ draft
        เจ้าของ (หรือแอดมิน) ที่รู้ id การประชุมที่ done/running/queued อยู่แล้วจะขอ upload-url
        ได้อีก คีย์ไฟล์ตรงกับของจริง เขียนทับเสียงต้นฉบับเงียบๆ ทั้งที่บทถอดเสียง/สรุปเป็นของเดิม
        หลังแก้ _draft_spec() ต้องเช็ค status == "draft" เป๊ะๆ ก่อน ไม่ว่าใครเรียก
        """
        for status in ("running", "queued", "done"):
            with self.subTest(status=status):
                jid = new_mid()
                self.store.add_job(jid, self.uid_a, status=status)
                for cookies in ({"mai_session": self.tokA}, {"mai_session": self.tokAdm}):
                    code, _, _ = self._upload_url(jid, cookies=cookies)
                    self.assertEqual(code, 404, f"status={status} cookies={cookies}")


class TestDraftOwnershipLocal(LocalCase):
    """โหมดไฟล์ไม่มีระบบล็อกอิน — เครื่องมือของเจ้าของเครื่องคนเดียว เปิดได้โดยไม่มีคุกกี้."""

    def test_local_mode_full_flow_without_any_cookie(self):
        status, body, _ = self.post_json("/api/meetings", {"title": "การประชุมในเครื่อง"})
        self.assertEqual(status, 201)
        mid = body["id"]

        status, body, _ = self.get(f"/api/meetings/{mid}/tracks/mixed/upload-url?ext=wav")
        self.assertEqual(status, 200)
        self.assertIsNone(body["url"])
        self.assertEqual(body["key"], f"{mid}.wav")

        status, body, _ = self.post_bytes(f"/api/meetings/{mid}/tracks/mixed?ext=wav", b"0" * 16)
        self.assertEqual(status, 200)
        self.assertEqual(body["bytes"], 16)


if __name__ == "__main__":
    import unittest
    unittest.main()
