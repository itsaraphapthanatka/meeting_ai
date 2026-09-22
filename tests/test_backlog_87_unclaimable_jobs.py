"""BACKLOG #87 — ล้มงานที่ค้างคิวโดยไม่มีเครื่องไหนรับชนิดนั้นได้.

`jobs_reap()` ดูแลงานที่ถูกหยิบไปแล้วเงียบหาย · งานที่ **ยังไม่มีใครหยิบ** ไม่มีใครดูแลเลย
ของจริง 2026-09-20: งาน `ask` ค้าง **10 ชม. 28 นาที** เพราะโค้ดบนเครื่อง worker เก่ากว่า
เซิร์ฟเวอร์และไม่รู้จัก kind นั้น ไม่มีอะไรบอกใครจนเจ้าของสังเกตเห็นการ์ดค้างเอง (#84)

**สามข้อที่ตัดสินใจไว้ และเทสต์ชุดนี้ตรึงไว้:**

1. **ล้มเฉพาะงานที่พิสูจน์ได้ว่าไม่มีทางถูกหยิบ** ไม่ใช่งานที่แค่รอคิวนาน — คิวยาวเพราะ
   เครื่องไม่ว่างเป็นเรื่องปกติของระบบที่มี worker ตัวเดียว ห้ามไปยุ่ง
2. **ต้องมีเครื่องที่ยังมีชีวิตอย่างน้อยหนึ่งเครื่อง** ถึงจะตัดสิน — ไม่มีเลยแปลว่าระบบดับ
   ชั่วคราว (ไฟดับ/รีบูต/เน็ตหลุด) งานควรรออยู่ในคิวจนกว่าจะเปิดกลับมา
3. **เครื่องที่ยังไม่ส่ง `kinds` มา ทำให้งดตัดสินทั้งรอบ** — worker รุ่นก่อน #84 ไม่ได้บอก
   ว่าตัวเองทำอะไรได้ เดาแทนแล้วไปล้มงานของคนอื่นแย่กว่าปล่อยค้าง (กติกาเดียวกับ #85)

ส่วนที่ตัดสินใจถูกแยกออกมาเป็น `_claimable_kinds()` เพื่อให้เขียนเทสต์ได้โดยไม่ต้องมี
Postgres จริง — บทเรียนจาก #82 ที่มุตันต์ของกลไกซึ่งฝังอยู่ใน SQL พิสูจน์บนเครื่อง
เจ้าของไม่ได้เลย ส่วน SQL เองมีเทสต์กับฐานจริงเมื่อมี `MAI_TEST_DATABASE_URL`
"""

from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

from meeting_ai.web import jobs

PGSTORE_PY = (Path(__file__).resolve().parents[1] / "meeting_ai" / "web"
              / "pgstore.py").read_text(encoding="utf-8")
JOBS_PY = (Path(jobs.__file__)).read_text(encoding="utf-8")


def claimable(rows):
    from meeting_ai.web import pgstore
    return pgstore._claimable_kinds(rows)


class TestTheDecision(unittest.TestCase):
    """`_claimable_kinds()` — ส่วนที่ตัดสินใจ ไม่ต้องมีฐานข้อมูล."""

    def test_it_unions_every_live_machine(self):
        rows = [(["process", "summarize"],), (["bot"],)]
        self.assertEqual(claimable(rows), {"process", "summarize", "bot"})

    def test_no_live_machine_means_do_not_judge(self):
        """ระบบดับชั่วคราว — งานควรรอ ไม่ใช่ถูกล้มทิ้ง (ข้อ 2)."""
        self.assertIsNone(claimable([]))

    def test_a_machine_that_does_not_report_kinds_stops_the_whole_round(self):
        """worker รุ่นก่อน #84 — เดาแทนแล้วไปล้มงานของคนอื่น แย่กว่าปล่อยค้าง (ข้อ 3)."""
        self.assertIsNone(claimable([(["process"],), (None,)]))

    def test_rubbish_from_a_worker_is_not_trusted(self):
        # worker คือฝั่งที่เชื่อไม่ได้ (กติกาเดียวกับ web/sanitize.py)
        for junk in ("process", 42, {"bot": True}):
            with self.subTest(junk=junk):
                self.assertIsNone(claimable([(junk,)]))

    def test_a_machine_that_can_do_nothing_is_still_a_judgement(self):
        # ต่างจาก None: มันบอกมาแล้วว่าทำอะไรไม่ได้ ซึ่งเป็นข้อมูลที่ใช้ตัดสินได้
        self.assertEqual(claimable([([],)]), set())

    def test_values_are_coerced_to_text(self):
        self.assertEqual(claimable([([1, "bot"],)]), {"1", "bot"})


class TestTheQueryOnlyTouchesWhatItShould(unittest.TestCase):

    def _sql(self) -> str:
        i = PGSTORE_PY.index("def jobs_fail_unclaimable")
        return PGSTORE_PY[i:PGSTORE_PY.index("def jobs_reap")]

    def test_only_queued_jobs(self):
        # running มี jobs_reap ดูแล · draft ยังรออัปโหลดไฟล์อยู่ ไม่ใช่ความผิดของคิว
        self.assertIn("where status = 'queued'", self._sql())

    def test_only_kinds_nobody_can_take(self):
        self.assertIn("not (kind = any(%s::text[]))", self._sql())

    def test_only_after_the_grace_period(self):
        """ข้อ 1 — คิวยาวเพราะเครื่องไม่ว่างเป็นเรื่องปกติ ห้ามล้มงานที่เพิ่งเข้าคิว."""
        self.assertIn("created_at < now() - make_interval(mins => %s)", self._sql())

    def test_the_error_says_what_to_do_next(self):
        sql = self._sql()
        self.assertIn("ไม่มีเครื่องประมวลผลที่ทำงานชนิดนี้ได้", sql)
        self.assertIn("สั่งใหม่ได้", sql)

    def test_it_only_looks_at_live_workers(self):
        self.assertIn("last_seen > now() - make_interval(secs => %s)", self._sql())


class TestItRunsOnTheClaimPath(unittest.TestCase):
    """serverless ไม่มีโพรเซสค้างให้ตั้งเวลา — ต้องเกาะจังหวะที่วิ่งสม่ำเสมออยู่แล้ว."""

    def test_claim_calls_it(self):
        body = JOBS_PY[JOBS_PY.index("def claim("):]
        body = body[:body.index("while True:")]
        self.assertIn("store.jobs_fail_unclaimable(UNCLAIMABLE_MINUTES)", body)

    def test_it_runs_beside_the_existing_reaper(self):
        body = JOBS_PY[JOBS_PY.index("def claim("):]
        body = body[:body.index("while True:")]
        self.assertLess(body.index("jobs_reap"), body.index("jobs_fail_unclaimable"))

    def test_the_grace_is_a_named_constant(self):
        self.assertIn("UNCLAIMABLE_MINUTES = 30", JOBS_PY)

    def test_it_is_cloud_only(self):
        # โหมดไฟล์รันงานในโพรเซสเดียวกัน ไม่มีคิวข้ามเครื่องให้ค้าง
        body = JOBS_PY[JOBS_PY.index("def claim("):]
        body = body[:body.index("while True:")]
        self.assertLess(body.index("if cloud:"), body.index("jobs_fail_unclaimable"))


@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestAgainstRealPostgres(unittest.TestCase):
    """ตัว SQL เองต้องวัดกับฐานจริง — เงื่อนไขแบบนี้เขียน regex ครอบให้ถูกไม่ได้."""

    def setUp(self) -> None:
        from meeting_ai.web import db as pgdb
        from meeting_ai.web import pgstore

        self.pgstore = pgstore
        self.pgdb = pgdb
        env = mock.patch.dict(os.environ,
                              {"DATABASE_URL": os.environ["MAI_TEST_DATABASE_URL"]})
        env.start()
        self.addCleanup(env.stop)
        pgdb.init()
        self.addCleanup(pgdb.close)
        self.tag = uuid.uuid4().hex[:8]
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.jobs where id like %s", (f"%{self.tag}",))
            conn.execute("delete from meeting_ai.workers where name like %s",
                         (f"%{self.tag}",))

    def _worker(self, kinds) -> None:
        import json
        with self.pgdb.connect() as conn:
            conn.execute(
                """insert into meeting_ai.workers (name, status, caps, last_seen)
                   values (%s, 'idle', %s, now())""",
                (f"w-{self.tag}", json.dumps({"kinds": kinds} if kinds is not None else {})))

    def _job(self, kind: str, *, age_min: int, status: str = "queued") -> str:
        jid = f"20260922-000000-{self.tag[:6]}.{kind}.{self.tag}"
        with self.pgdb.connect() as conn:
            conn.execute(
                """insert into meeting_ai.jobs (id, kind, title, spec, status, step, progress,
                                                created_at)
                   values (%s, %s, 't', '{}'::jsonb, %s, 'รอคิว', 0,
                           now() - make_interval(mins => %s))""",
                (jid, kind, status, age_min))
        return jid

    def _status(self, jid: str):
        with self.pgdb.connect() as conn:
            return conn.execute(
                "select status, error from meeting_ai.jobs where id = %s", (jid,)).fetchone()

    def test_an_old_job_nobody_can_take_is_failed(self):
        self._worker(["process", "summarize"])
        jid = self._job("ask", age_min=120)
        self.assertEqual(self.pgstore.jobs_fail_unclaimable(30), 1)
        st, err = self._status(jid)
        self.assertEqual(st, "error")
        self.assertIn("ไม่มีเครื่องประมวลผล", err)

    def test_a_job_someone_can_take_is_left_alone(self):
        self._worker(["process", "ask"])
        jid = self._job("ask", age_min=120)
        self.assertEqual(self.pgstore.jobs_fail_unclaimable(30), 0)
        self.assertEqual(self._status(jid)[0], "queued")

    def test_a_fresh_job_is_left_alone(self):
        self._worker(["process"])
        jid = self._job("ask", age_min=5)
        self.assertEqual(self.pgstore.jobs_fail_unclaimable(30), 0)
        self.assertEqual(self._status(jid)[0], "queued")

    def test_a_running_job_is_not_this_function_s_business(self):
        self._worker(["process"])
        jid = self._job("ask", age_min=120, status="running")
        self.assertEqual(self.pgstore.jobs_fail_unclaimable(30), 0)
        self.assertEqual(self._status(jid)[0], "running")

    def test_an_old_worker_without_kinds_stops_everything(self):
        self._worker(None)
        jid = self._job("ask", age_min=120)
        self.assertEqual(self.pgstore.jobs_fail_unclaimable(30), 0)
        self.assertEqual(self._status(jid)[0], "queued")


if __name__ == "__main__":
    unittest.main()
