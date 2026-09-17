"""BACKLOG #3 — jobs.active()/store.job_active() เดิมไม่กรองตามเจ้าของเลย.

ก่อนแก้: jobs.active() ไม่มีพารามิเตอร์ ทุกคนที่ล็อกอินเห็นคิวงานของทั้งระบบ (รวมชื่อการประชุม
ของคนอื่นที่เป็นข้อมูลส่วนบุคคล) หลังแก้: server._job_scope() ตัดสินว่าจะกรองด้วย owner_id
หรือ meeting_id ตามบทบาทผู้เรียก แล้วส่งต่อให้ jobs.active(**scope) -> store.job_active(...)
"""

import os
import secrets
import unittest
from contextlib import contextmanager
from unittest import mock

from _harness import CloudCase, jobs, new_mid
from meeting_ai.web import db, pgstore


class TestJobsScopedCloud(CloudCase):
    def test_owner_sees_only_own_job_in_meetings_feed(self):
        status, body, _ = self.get("/api/meetings", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        job_ids = {j["id"] for j in body["jobs"]}
        self.assertEqual(job_ids, {self.J_A})
        titles = {j["title"] for j in body["jobs"]}
        self.assertNotIn("B secret", titles)
        self.assertEqual(self.store.job_active_calls[-1], (self.uid_a, None))

    def test_owner_sees_only_own_job_and_workers_on_jobs_endpoint(self):
        status, body, _ = self.get("/api/jobs", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        self.assertEqual({j["id"] for j in body["jobs"]}, {self.J_A})
        self.assertIn("workers", body)

    def test_admin_sees_every_job_unscoped(self):
        status, body, _ = self.get("/api/jobs", cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        self.assertEqual({j["id"] for j in body["jobs"]}, {self.J_A, self.J_B, self.M1_tr_en})
        self.assertEqual(self.store.job_active_calls[-1], (None, None))

    def test_get_single_job_permission_matrix(self):
        url = f"/api/jobs/{self.J_A}"
        status, _, _ = self.get(url, cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)
        status, _, _ = self.get(url, cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        status, _, _ = self.get(url, cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)

        status, _, _ = self.get(f"/api/jobs/{self.M1_tr_en}", cookies={"mai_share": self.shr1})
        self.assertEqual(status, 200)
        status, _, _ = self.get(url, cookies={"mai_share": self.shr1})
        self.assertEqual(status, 403)

    def test_resummarize_embeds_caller_as_owner(self):
        status, body, _ = self.post_json(f"/api/meetings/{self.M1}/resummarize",
                                         cookies={"mai_session": self.tokA})
        self.assertEqual(status, 202)
        self.assertEqual(self.store.jobs[self.M1]["spec"]["owner_id"], self.uid_a)

    def test_translate_embeds_caller_as_owner(self):
        job_id = f"{self.M1}.tr.ja"
        status, body, _ = self.post_json(f"/api/meetings/{self.M1}/translate", {"lang": "ja"},
                                         cookies={"mai_session": self.tokA})
        self.assertEqual(status, 202)
        self.assertEqual(self.store.jobs[job_id]["spec"]["owner_id"], self.uid_a)

    def test_stranger_cannot_stop_job(self):
        status, _, _ = self.post_json(f"/api/jobs/{self.J_A}/stop", cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)

    # ---------- Round 2: _may_write_job ใช้ meeting_id ไม่ใช่ job["id"] เป็น fallback ----------

    def test_owner_can_stop_translate_job_via_meeting_id_fallback(self):
        """M1_tr_en มี id เป็น <mid>.tr.<lang> ไม่ใช่ id การประชุม — เดิม _may_write_job

        fallback ไปเช็คสิทธิ์บน job["id"] เอง (= "<M1>.tr.en") ซึ่งไม่มีการประชุมชื่อนี้อยู่จริง
        เจ้าของการประชุม M1 (ที่ควรหยุดงานแปลของตัวเองได้) เลยโดนตอบ 403 อย่างผิดๆ
        หลังแก้ fallback ไปที่ job["meeting_id"] (= M1) แทน ต้องหยุดได้ (200) — สายเดิม
        (คนแปลกหน้า) ยังต้องโดนกันเหมือนเดิม
        """
        status, _, _ = self.post_json(f"/api/jobs/{self.M1_tr_en}/stop",
                                      cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)

    def test_stranger_still_cannot_stop_translate_job(self):
        status, _, _ = self.post_json(f"/api/jobs/{self.M1_tr_en}/stop",
                                      cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)

    # ---------- Round 2: _workers_view — เจ้าของงานเป็นข้อมูลชนิดเดียวกับที่กรองออกจาก jobs ----------

    def _seed_worker(self):
        self.store.workers = [{
            "name": "gb10", "status": "busy", "job_id": self.J_B, "job_title": "B secret",
            "gpu": "rtx", "last_seen": "2026-01-01T00:00:00", "jobs_done": 3,
        }]

    def test_non_admin_workers_endpoint_hides_job_title_and_job_id(self):
        self._seed_worker()
        status, body, _ = self.get("/api/workers", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        self.assertEqual(len(body["workers"]), 1)
        w = body["workers"][0]
        self.assertNotIn("job_title", w)
        self.assertNotIn("job_id", w)
        self.assertEqual(w["name"], "gb10")  # ฟิลด์อื่นยังอยู่ครบ

    def test_admin_workers_endpoint_keeps_job_title_and_job_id(self):
        self._seed_worker()
        status, body, _ = self.get("/api/workers", cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        w = body["workers"][0]
        self.assertEqual(w["job_title"], "B secret")
        self.assertEqual(w["job_id"], self.J_B)

    def test_non_admin_jobs_endpoint_workers_key_also_masked(self):
        self._seed_worker()
        status, body, _ = self.get("/api/jobs", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        self.assertIn("workers", body)
        w = body["workers"][0]
        self.assertNotIn("job_title", w)
        self.assertNotIn("job_id", w)


class TestJobsActivePureLocalMode(unittest.TestCase):
    """โหมดไฟล์ (jobs.cloud False) จงใจไม่กรอง — มีผู้ใช้คนเดียวคือเจ้าของเครื่อง."""

    def test_local_mode_ignores_owner_filter(self):
        job_id = new_mid()
        record = {
            "id": job_id, "status": "queued", "step": "รอคิว", "progress": 0.0,
            "title": "t", "kind": "process", "meeting_id": None,
            "error": None, "warning": None, "created": "2026-01-01T00:00:00",
        }
        with jobs._cv:
            jobs._jobs[job_id] = record
        self.addCleanup(lambda: jobs._jobs.pop(job_id, None))

        with mock.patch.object(jobs, "cloud", False):
            result = jobs.active(owner_id="ไม่ตรงกับใครเลย")

        self.assertIn(job_id, {j["id"] for j in result})


class TestPgstoreJobActiveSqlShape(unittest.TestCase):
    """ไม่ต่อ DB จริง — ปลอม db.connect() แล้วตรวจแค่รูป SQL/พารามิเตอร์ที่ job_active() ส่งไป.

    เหตุผลของ %s::text is null: psycopg ส่ง NULL แบบไม่ระบุชนิดไม่ได้ ต้อง cast
    เหมือน job_claim ไม่งั้น Postgres ฟ้อง "could not determine data type of parameter"
    """

    def test_sql_has_four_placeholders_and_owner_cast(self):
        calls = []

        class FakeCursor:
            def fetchall(self):
                return []

        class FakeConn:
            def execute(self, sql, params):
                calls.append((sql, params))
                return FakeCursor()

        @contextmanager
        def fake_connect():
            yield FakeConn()

        with mock.patch.object(pgstore.db, "connect", fake_connect):
            result = pgstore.job_active("u", "m")

        self.assertEqual(result, [])
        self.assertEqual(len(calls), 1)
        sql, params = calls[0]
        self.assertEqual(sql.count("%s"), 4)
        self.assertEqual(len(params), 4)
        self.assertEqual(params, ("u", "u", "m", "m"))
        self.assertIn("spec->>'owner_id'", sql)
        self.assertIn("::text is null", sql)


_REAL_DB_URL = os.environ.get("MAI_TEST_DATABASE_URL")


@unittest.skipUnless(_REAL_DB_URL, "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestPgstoreJobActiveAgainstRealDb(unittest.TestCase):
    """ต่อ Postgres จริง (schema.sql เดียวกับที่ `./mai db-init` ใช้) แล้วยิง pgstore.job_active()

    ตรงๆ ไม่ผ่านชั้น HTTP — TestJobsScopedCloud (ด้านบน) พิสูจน์แค่ว่า server.py เรียก
    job_active(**scope) ถูกพารามิเตอร์ ส่วน TestPgstoreJobActiveSqlShape พิสูจน์แค่รูป SQL
    ด้วย connection ปลอม ทั้งคู่ไม่เคยรัน SQL จริงกับ Postgres จริงเลยสักครั้ง คลาสนี้ปิดช่องว่างนั้น

    วิธีแยกข้อมูลระหว่างเทสต์ — เลือก "truncate ทุกตารางใน setUp" แทนอีกสองทาง:
    - schema แยกต่อเทสต์: ทุก query ใน pgstore.py เขียน "meeting_ai." ตรงๆ ไม่พึ่ง search_path
      (ดูคอมเมนต์หัว schema.sql — เหตุผลคือ Neon pooler โหมด transaction ไม่การันตี SET
      ระดับ session) จะรันคู่ขนานหลาย schema ต้องเขียน SQL ของ pgstore.py ใหม่ ไม่ใช่แค่ config
    - ครอบทุกเทสต์ในทรานแซกชันเดียวแล้ว rollback: ทุกฟังก์ชันใน pgstore.py เปิด
      connection ใหม่จาก pool เอง (db.connect() เป็น autocommit) จะบังคับให้ใช้ connection
      เดียวกันซ้ำต้องแพตช์ db.connect() เอง ซึ่งเสี่ยงบดบังบั๊กเรื่อง autocommit/pooler จริงๆ
      ที่ทีมเจอมาแล้ว (ดู web/db.py docstring เรื่อง prepare_threshold)
    ฐานนี้เป็นฐานทิ้งของชุดทดสอบนี้อย่างเดียว (ไม่ใช่ของใช้ร่วมกับคนอื่น) truncate ทุกตาราง
    ก่อนแต่ละเทสต์จึงตรงไปตรงมาที่สุดและไม่เสี่ยงบดบังอะไร
    """

    @classmethod
    def setUpClass(cls) -> None:
        # ต้องตั้ง DATABASE_URL ตอนรันเทสต์ ไม่ใช่ตอน import — _harness.py (ซึ่งไฟล์นี้ import
        # ด้วย) ตั้ง DATABASE_URL="" ไว้ตอน import module ระดับโพรเซส (ดู docstring ของมัน)
        # db.url()/_connect_kwargs() อ่าน os.environ สดทุกครั้งที่เรียก ไม่ได้ cache ตอน import
        # ของโมดูล db.py เอง จึงตั้งค่าตรงนี้แล้วมีผลจริงได้
        os.environ["DATABASE_URL"] = _REAL_DB_URL
        db.close()  # เผื่อมีพูลเก่าค้างจาก DATABASE_URL อื่นในโพรเซสเดียวกัน (พูลเป็น global ของโมดูล)
        tables = db.init()  # เท่ากับที่ `./mai db-init` ทำ — ยืนยันว่า schema.sql รันกับ Postgres จริงได้จริง
        for name in ("users", "meetings", "jobs"):
            assert name in tables, f"schema.sql ไม่ได้สร้างตาราง {name}"

    @classmethod
    def tearDownClass(cls) -> None:
        db.close()

    def setUp(self) -> None:
        with db.connect() as conn:
            conn.execute(
                "truncate table meeting_ai.jobs, meeting_ai.shares, meeting_ai.sessions, "
                "meeting_ai.meetings, meeting_ai.users restart identity cascade"
            )
        tag = secrets.token_hex(4)
        self.user_a = pgstore.ensure_user(f"tenant-a-{tag}@example.com", "Tenant A")
        self.user_b = pgstore.ensure_user(f"tenant-b-{tag}@example.com", "Tenant B")
        self.admin = pgstore.ensure_user(f"admin-{tag}@example.com", "Admin", is_admin=True)

        self.meeting_a = pgstore.create(
            pgstore.new_id(), "Meeting A (tenant A)", None, "upload", "th", 0, [], "",
            owner_id=self.user_a["id"],
        )
        self.meeting_b = pgstore.create(
            pgstore.new_id(), "Meeting B (tenant B)", None, "upload", "th", 0, [], "",
            owner_id=self.user_b["id"],
        )

        self.job_a = pgstore.new_id()
        pgstore.job_upsert(self.job_a, "process", "A secret", {"owner_id": self.user_a["id"]},
                           status="running", meeting_id=self.meeting_a["id"])
        self.job_b = pgstore.new_id()
        pgstore.job_upsert(self.job_b, "process", "B secret", {"owner_id": self.user_b["id"]},
                           status="queued", meeting_id=self.meeting_b["id"])

    def test_admin_sees_every_job_system_wide(self):
        # _job_scope() ของแอดมินส่ง owner_id=None, meeting_id=None (ไม่กรองอะไรเลย)
        result = pgstore.job_active()
        self.assertEqual({j["id"] for j in result}, {self.job_a, self.job_b})

    def test_user_sees_only_own_job(self):
        result = pgstore.job_active(owner_id=self.user_a["id"])
        self.assertEqual({j["id"] for j in result}, {self.job_a})
        self.assertEqual(result[0]["title"], "A secret")

    def test_tenant_b_job_never_appears_for_tenant_a(self):
        result = pgstore.job_active(owner_id=self.user_a["id"])
        self.assertNotIn(self.job_b, {j["id"] for j in result})
        self.assertNotIn("B secret", {j["title"] for j in result})

    def test_share_only_visitor_sees_only_shared_meetings_job(self):
        # ผู้เข้าทาง /s/<token> ไม่มี user_id (self.user_id is None) — _job_scope() จึงส่ง
        # meeting_id ของการประชุมที่แชร์แทน owner_id (ดู PROJECT-CONTEXT.md บรรทัด _job_scope)
        result = pgstore.job_active(meeting_id=self.meeting_a["id"])
        self.assertEqual({j["id"] for j in result}, {self.job_a})

    def test_done_jobs_are_not_active_regardless_of_scope(self):
        finished = pgstore.new_id()
        pgstore.job_upsert(finished, "process", "finished job", {"owner_id": self.user_a["id"]},
                           status="done", meeting_id=self.meeting_a["id"])
        result = pgstore.job_active(owner_id=self.user_a["id"])
        self.assertNotIn(finished, {j["id"] for j in result})


if __name__ == "__main__":
    unittest.main()
