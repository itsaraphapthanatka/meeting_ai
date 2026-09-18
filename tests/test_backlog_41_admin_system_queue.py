"""BACKLOG #41 — แอดมินเห็นคิวของทั้งระบบ แต่หน้าจอต้องเป็นของเขาเอง.

`_job_scope()` คืน `{}` ให้แอดมิน (ตั้งใจ — ใช้ไล่ปัญหา) แต่ `store.search(user_id=admin)`
คืนเฉพาะการประชุมของตัวเอง ผลคือ `pollJobs()` เห็นงานของ tenant อื่นจบ แล้วสั่ง
`openMeeting(job.meeting_id)` ไปที่การประชุมที่ตัวเองเปิดไม่ได้ → แบนเนอร์ 403 เด้งใส่หน้าจอ
และข้อความ error ของงานคนอื่นถูกเอามาขึ้นให้อ่าน

แก้ด้วยการติดธง `mine` ให้แอดมินเท่านั้น: "เปิดดูได้ไหม" กับ "เป็นงานของฉันไหม" เป็นคนละคำถาม
คนทั่วไปไม่ได้ฟิลด์นี้เลย เพราะทุกงานที่เขาเห็นเป็นของเขาอยู่แล้ว — หน้าเว็บอ่านว่า
"ไม่มีฟิลด์ = ของฉัน" จึงเข้ากันได้กับเซิร์ฟเวอร์/หน้าเว็บคนละรุ่น

ฝั่งหน้าเว็บมีเทสต์ที่อ่านจาก app.js จริง: ธงที่เซิร์ฟเวอร์ส่งไปแล้วไม่มีใครใช้ ก็ไม่ได้แก้อะไร
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from _harness import CloudCase, new_mid

APP_JS = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static" / "app.js"


class TestMineFlag(CloudCase):

    def _jobs(self, token: str) -> dict[str, dict]:
        status, body, _ = self.get("/api/jobs", cookies={"mai_session": token})
        self.assertEqual(status, 200)
        return {j["id"]: j for j in body["jobs"]}

    # ---------- แอดมิน ----------

    def test_admin_still_sees_the_whole_system_queue(self):
        # ธง mine ต้องไม่ไปตัดงานออกจากคิว — แอดมินยังต้องเห็นครบเพื่อไล่ปัญหา
        self.assertEqual(set(self._jobs(self.tokAdm)),
                         {self.J_A, self.J_B, self.M1_tr_en})

    def test_other_tenants_jobs_are_marked_not_mine(self):
        got = self._jobs(self.tokAdm)
        self.assertFalse(got[self.J_A]["mine"])
        self.assertFalse(got[self.J_B]["mine"])
        self.assertFalse(got[self.M1_tr_en]["mine"], "งานบนประชุมส่วนตัวของ A ไม่ใช่ของแอดมิน")

    def test_the_admins_own_job_is_marked_mine(self):
        # ถ้าไม่มีเคสนี้ การติดธง False ให้ทุกงานก็ผ่านหมด
        mid = new_mid()
        self.store.add_meeting(mid, self.uid_adm, title="ของแอดมินเอง", visibility="private")
        job_id = new_mid()
        self.store.add_job(job_id, self.uid_adm, title="งานแอดมิน", meeting_id=mid)
        self.assertTrue(self._jobs(self.tokAdm)[job_id]["mine"])

    def test_a_job_on_a_team_meeting_of_someone_else_is_mine_to_watch(self):
        # ประชุมแบบ team ทุกคนที่ล็อกอินอ่านได้อยู่แล้ว เปิดให้ไม่ใช่การเปิดกว้างเพิ่ม
        mid = new_mid()
        self.store.add_meeting(mid, self.uid_a, title="ประชุมทีม", visibility="team")
        job_id = new_mid()
        self.store.add_job(job_id, self.uid_a, title="งานบนประชุมทีม", meeting_id=mid)
        self.assertTrue(self._jobs(self.tokAdm)[job_id]["mine"])

    # ---------- คนทั่วไป ----------

    def test_an_ordinary_user_gets_no_flag_at_all(self):
        for job in self._jobs(self.tokA).values():
            self.assertNotIn("mine", job, "คนทั่วไปเห็นเฉพาะงานตัวเอง ไม่ต้องมีธงให้เปลือง")

    def test_the_meetings_feed_carries_the_same_flags(self):
        # หน้าเว็บโหลดครั้งแรกจาก /api/meetings ไม่ใช่ /api/jobs — ถ้าธงไม่มาด้วย
        # รอบแรกจะยังเด้งไปที่ประชุมของคนอื่นอยู่ดี
        status, body, _ = self.get("/api/meetings", cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        flags = {j["id"]: j.get("mine") for j in body["jobs"]}
        self.assertEqual(flags, {self.J_A: False, self.J_B: False, self.M1_tr_en: False})

    def test_admin_can_still_open_any_single_job(self):
        # ธงบอกว่า "ไม่ใช่ของฉัน" ไม่ใช่ "ห้ามดู" — สิทธิ์ไล่ปัญหาต้องไม่หายไป
        status, _, _ = self.get(f"/api/jobs/{self.J_B}", cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)

    def test_an_ordinary_user_still_cannot_open_someone_elses_job(self):
        status, _, _ = self.get(f"/api/jobs/{self.J_B}", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 403)


class TestTheFrontEndUsesIt(unittest.TestCase):
    """ธงที่ส่งไปแล้วไม่มีใครอ่าน ก็ไม่ได้แก้อะไร — ตรวจจาก app.js จริง."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = APP_JS.read_text(encoding="utf-8")

    def test_the_flag_is_read_with_a_missing_field_meaning_mine(self):
        # "!== false" ไม่ใช่รายละเอียดจุกจิก: ใช้ "=== true" เมื่อไหร่ ผู้ใช้ทั่วไป (ที่ไม่ได้ธง)
        # จะกลายเป็น "ไม่ใช่ของฉัน" ทั้งหมด แล้วหน้าเว็บจะหยุดเปิดผลงานให้เขาเลย
        self.assertIn("j.mine !== false", self.src)

    def test_only_my_own_jobs_can_change_the_screen(self):
        m = re.search(r"const before = state\.jobs\.filter\(([^;]*)\);", self.src)
        self.assertIsNotNone(m, "หาบรรทัดที่เลือกงานที่เพิ่งจบไม่เจอ — ตัวตรวจพัง ไม่ใช่โค้ดดี")
        self.assertIn("isMine", m.group(1))

    def test_the_system_queue_is_polled_more_slowly(self):
        # แอดมินเคยถูก poll ทุก 1.5 วิตลอดเวลาที่ "มีใครสักคนในระบบ" มีงานเดินอยู่
        m = re.search(r"const ms = busy\.some\(isMine\) \? (\w+) : busy\.length \? (\w+)",
                      self.src)
        self.assertIsNotNone(m, self.src[:0] or "หาสูตรจังหวะ poll ไม่เจอ")
        fast = int(re.search(rf"const {m.group(1)} = (\d+)", self.src).group(1))
        slow = int(re.search(rf"const {m.group(2)} = (\d+)", self.src).group(1))
        self.assertGreater(slow, fast)

    def test_other_peoples_jobs_are_labelled_in_the_list(self):
        self.assertIn("job-sys", self.src)
        self.assertIn(".job .job-sys", (APP_JS.parent / "style.css").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
