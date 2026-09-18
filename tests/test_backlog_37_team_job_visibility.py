"""BACKLOG #37 — งานบนการประชุมที่เราอ่านได้ ต้องมองเห็นได้ ไม่ใช่เห็นเฉพาะงานที่ตัวเองสั่ง.

`job_active(owner_id=X)` เดิมกรองด้วย `spec->>'owner_id' = X` อย่างเดียว ผลคือ:

  1. เพื่อนร่วมทีมกดสรุปใหม่/แปลบนการประชุมแบบ team ของเรา -> **เจ้าของการประชุมไม่เห็นงานนั้น**
     หน้าเว็บนิ่งสนิททั้งที่มีงานเดินอยู่ กดสั่งซ้ำก็ไม่รู้ว่าซ้ำ
  2. งานที่สร้างก่อนจะมี owner_id ใน spec (ก่อน deploy รอบที่ใส่ฟิลด์นี้) ไม่มีใครเห็นเลย
     นอกจากแอดมิน — ค้างอยู่ในคิวโดยไม่มีใครรู้

แก้โดยเพิ่มสาขา "งานบนการประชุมที่คนนี้อ่านได้" ซึ่งใช้กติกาเดียวกับการเห็นตัวการประชุม
(เจ้าของ หรือ visibility = team) **ไม่ได้เปิดกว้างกว่าที่เห็นการประชุมอยู่แล้ว** — เทสต์
กลุ่มที่สองในไฟล์นี้คุมเส้นนั้นไว้: การประชุมส่วนตัวของคนอื่นต้องยังไม่เห็น

ทั้งหมดเป็น SQL จึงต้องยิง Postgres จริง (FakeStore พิสูจน์อะไรไม่ได้เลยกับเรื่องนี้)
"""

from __future__ import annotations

import os
import unittest
import uuid
from unittest import mock


@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestTeamJobVisibility(unittest.TestCase):

    def setUp(self) -> None:
        from meeting_ai.web import db as pgdb
        from meeting_ai.web import pgstore

        self.pgdb = pgdb
        self.pgstore = pgstore
        env = mock.patch.dict(os.environ,
                              {"DATABASE_URL": os.environ["MAI_TEST_DATABASE_URL"]})
        env.start()
        self.addCleanup(env.stop)
        pgdb.init()
        self.addCleanup(pgdb.close)

        self.tag = uuid.uuid4().hex[:8]
        self.alice = self._user("alice")
        self.bob = self._user("bob")
        self.addCleanup(self._cleanup)

    # ---------- fixtures ----------

    def _user(self, name: str) -> str:
        with self.pgdb.connect() as conn:
            row = conn.execute(
                "insert into meeting_ai.users (email, name) values (%s, %s) returning id",
                (f"{name}-{self.tag}@test.local", name)).fetchone()
        return str(row[0])

    def _meeting(self, mid_suffix: str, owner: str, visibility: str) -> str:
        mid = f"20260918-120000-{mid_suffix}"
        with self.pgdb.connect() as conn:
            conn.execute(
                """insert into meeting_ai.meetings (id, owner_id, title, visibility, language)
                   values (%s, %s, %s, %s, 'th')""",
                (mid, owner, f"ประชุมทดสอบ {mid_suffix}", visibility))
        return mid

    def _job(self, job_suffix: str, meeting_id: str, submitted_by: str | None) -> str:
        job_id = f"{meeting_id}.{job_suffix}"
        spec = {} if submitted_by is None else {"owner_id": submitted_by}
        self.pgstore.job_upsert(job_id, "summarize", "สรุปใหม่", spec,
                                meeting_id=meeting_id)
        return job_id

    def _cleanup(self) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.jobs where id like %s",
                         (f"20260918-120000-{self.tag}%",))
            conn.execute("delete from meeting_ai.meetings where id like %s",
                         (f"20260918-120000-{self.tag}%",))
            conn.execute("delete from meeting_ai.users where email like %s",
                         (f"%-{self.tag}@test.local",))

    def _ids(self, **scope) -> set[str]:
        return {j["id"] for j in self.pgstore.job_active(**scope)}

    # ---------- สิ่งที่เคยหายไป ----------

    def test_owner_sees_a_teammate_job_on_their_team_meeting(self):
        mid = self._meeting(f"{self.tag}a", owner=self.alice, visibility="team")
        job = self._job("sum", mid, submitted_by=self.bob)
        self.assertIn(job, self._ids(owner_id=self.alice),
                      "เจ้าของการประชุมต้องเห็นงานที่เพื่อนร่วมทีมสั่งบนประชุมของตัวเอง")

    def test_a_job_without_owner_in_spec_is_visible_to_the_meeting_owner(self):
        # งานเก่าที่สร้างก่อนจะมีฟิลด์นี้ — เดิมไม่มีใครเห็นนอกจากแอดมิน
        mid = self._meeting(f"{self.tag}b", owner=self.alice, visibility="private")
        job = self._job("sum", mid, submitted_by=None)
        self.assertIn(job, self._ids(owner_id=self.alice))

    def test_any_member_sees_jobs_on_a_team_meeting(self):
        # การประชุมแบบ team ทุกคนที่ล็อกอินเห็นอยู่แล้ว งานของมันจึงไม่ใช่ความลับเพิ่ม
        mid = self._meeting(f"{self.tag}c", owner=self.alice, visibility="team")
        job = self._job("sum", mid, submitted_by=self.alice)
        self.assertIn(job, self._ids(owner_id=self.bob))

    # ---------- เส้นที่ห้ามหลุด ----------

    def test_a_private_meeting_of_someone_else_stays_hidden(self):
        mid = self._meeting(f"{self.tag}d", owner=self.alice, visibility="private")
        job = self._job("sum", mid, submitted_by=self.alice)
        self.assertNotIn(job, self._ids(owner_id=self.bob),
                         "งานบนประชุมส่วนตัวของคนอื่นต้องไม่โผล่")

    def test_an_orphan_job_without_owner_or_meeting_stays_hidden(self):
        # ไม่มีทั้ง owner_id และการประชุมให้ยึด — ไม่ควรไปโผล่ในคิวของใครสักคน
        job_id = f"20260918-120000-{self.tag}e.orphan"
        self.pgstore.job_upsert(job_id, "summarize", "ไม่มีเจ้าของ", {}, meeting_id=None)
        self.assertNotIn(job_id, self._ids(owner_id=self.bob))
        self.assertNotIn(job_id, self._ids(owner_id=self.alice))
        self.assertIn(job_id, self._ids(), "แอดมิน (ไม่กรอง) ต้องยังเห็นเพื่อไล่ปัญหาได้")

    def test_my_own_job_on_someone_elses_private_meeting_is_still_mine_to_see(self):
        # เคยสั่งไว้ตอนยังมีสิทธิ์ แล้วสิทธิ์ถูกถอน — งานที่ตัวเองสั่งยังควรเห็นสถานะ
        mid = self._meeting(f"{self.tag}f", owner=self.alice, visibility="private")
        job = self._job("sum", mid, submitted_by=self.bob)
        self.assertIn(job, self._ids(owner_id=self.bob))

    # ---------- ตัวกรองเดิมต้องไม่เพี้ยน ----------

    def test_meeting_filter_still_narrows_for_share_links(self):
        mine = self._meeting(f"{self.tag}g", owner=self.alice, visibility="team")
        other = self._meeting(f"{self.tag}h", owner=self.alice, visibility="team")
        want = self._job("sum", mine, submitted_by=self.alice)
        skip = self._job("sum", other, submitted_by=self.alice)
        seen = self._ids(meeting_id=mine)
        self.assertIn(want, seen)
        self.assertNotIn(skip, seen)

    def test_finished_jobs_are_not_listed(self):
        mid = self._meeting(f"{self.tag}i", owner=self.alice, visibility="team")
        job = self._job("sum", mid, submitted_by=self.alice)
        self.pgstore.job_fail(job, "พัง")
        self.assertNotIn(job, self._ids(owner_id=self.alice))


if __name__ == "__main__":
    unittest.main()
