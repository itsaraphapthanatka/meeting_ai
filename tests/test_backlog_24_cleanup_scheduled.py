"""BACKLOG #24 — ตัวเก็บกวาดที่เขียนไว้แต่ไม่มีใครเรียก.

ตั๋วรวมห้าอย่างไว้ด้วยกัน พอไล่ดูจริงแยกได้เป็นสามกอง:

* **รั่วจริง** — `purge_expired()` (sessions โตขึ้นหนึ่งแถวต่อการล็อกอินหนึ่งครั้ง ตลอดอายุระบบ)
  และ `workers_forget()` (เครื่องที่ใช้ครั้งเดียวเมื่อปีที่แล้วยังค้างอยู่ในรายการให้แอดมินเห็น)
  ไม่ใช่ช่องโหว่ — คิวรีกรอง `expires_at > now()` อยู่แล้ว — แต่เป็นตารางที่ไม่มีวันหยุดโต
* **สุขอนามัย** — `db.close()` ไม่เคยถูกเรียกตอนปิดเซิร์ฟเวอร์ (Neon นับ connection เป็น
  ทรัพยากรที่มีเพดาน) และ `backend.health()` ไม่มีใครเรียกเพราะยังไม่มีเส้น API ให้เรียก
* **ตั๋วเข้าใจผิด** — `blobstore.reset()` ไม่ใช่โค้ดตาย มันคือตะขอที่ `tests/_harness.py`
  ใช้สลับที่เก็บไฟล์ระหว่างเทสต์ (เรียกอยู่สามที่) ปล่อยไว้ตามเดิม

ที่เก็บกวาดต้องทำงานทั้งบนโพรเซสที่รันยาว (`mai web`) และบน serverless ที่ไม่มีโพรเซสค้าง
ให้ตั้งเวลา — ทั้งสองทางเรียก `store.sweep()` ตัวเดียวกันซึ่งจองสิทธิ์ผ่านนาฬิกาของฐานข้อมูล
"""

from __future__ import annotations

import ast
import os
import unittest
import uuid
from pathlib import Path
from unittest import mock

from meeting_ai.web import backend, blobstore, server


class TestTheWiring(unittest.TestCase):
    """ตัวเก็บกวาดที่ไม่มีใครเรียกก็ยังเป็นโค้ดตาย — ตรวจจุดเรียกจากซอร์สจริง."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.jobs_src = Path(Path(server.__file__).parent / "jobs.py").read_text(encoding="utf-8")
        cls.server_src = Path(server.__file__).read_text(encoding="utf-8")

    def test_the_worker_claim_path_sweeps(self):
        # บน serverless นี่คือจังหวะเดียวที่วิ่งสม่ำเสมอ
        claim = self.jobs_src[self.jobs_src.index("def claim("):]
        self.assertIn("store.sweep_if_due()", claim[:claim.index("\ndef ")])

    def test_the_long_running_server_starts_a_sweeper(self):
        # ต้องดูใน serve() เท่านั้น — "_start_sweeper()" โผล่ในบรรทัด def ของตัวเองด้วย
        # เช็คทั้งไฟล์จะผ่านแม้ไม่มีใครเรียกมันเลย
        tail = self.server_src[self.server_src.index("def serve("):]
        self.assertIn("_start_sweeper()", tail)
        self.assertIn("mai-sweeper", self.server_src)

    def test_the_sweeper_is_a_daemon_so_ctrl_c_still_exits(self):
        self.assertRegex(self.server_src, r"mai-sweeper\W+.*daemon=True")

    def test_the_pool_is_closed_on_shutdown(self):
        tail = self.server_src[self.server_src.index("def serve("):]
        self.assertIn("db.close()", tail)

    def test_no_sweeper_thread_in_file_mode(self):
        # โหมดไฟล์ไม่มีตาราง session/worker ให้กวาด และ store ก็ไม่มีฟังก์ชันนี้ด้วยซ้ำ
        with mock.patch.object(backend, "cloud", False), \
             mock.patch.object(server.threading, "Thread") as thread:
            server._start_sweeper()
        thread.assert_not_called()

    def test_the_sweeper_thread_starts_in_cloud_mode(self):
        with mock.patch.object(backend, "cloud", True), \
             mock.patch.object(server.threading, "Thread") as thread:
            server._start_sweeper()
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs.get("daemon"))

    def test_blobstore_reset_is_not_dead_code(self):
        # ตั๋วนับรวมมาผิด — มันคือตะขอของ _harness ไม่ใช่ของที่ลืมเรียก
        harness = Path(__file__).with_name("_harness.py").read_text(encoding="utf-8")
        self.assertIn("blobstore.reset()", harness)
        self.assertTrue(callable(blobstore.reset))


class TestHealthEndpoint(unittest.TestCase):
    """backend.health() ไม่มีใครเรียกเพราะไม่มีเส้นให้เรียก."""

    def test_health_is_public(self):
        self.assertIn(("health",), server.PUBLIC_API)

    def test_health_reports_the_mode(self):
        info = backend.health()
        self.assertIn("mode", info)
        self.assertIn("auth", info)


@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestSweepAgainstPostgres(unittest.TestCase):
    """ทั้งหมดเป็น SQL — FakeStore พิสูจน์อะไรไม่ได้เลยกับเรื่องนี้."""

    def setUp(self) -> None:
        from meeting_ai.web import db as pgdb
        from meeting_ai.web import pgstore

        self.pgdb, self.pgstore = pgdb, pgstore
        env = mock.patch.dict(os.environ,
                              {"DATABASE_URL": os.environ["MAI_TEST_DATABASE_URL"]})
        env.start()
        self.addCleanup(env.stop)
        pgdb.init()
        self.addCleanup(pgdb.close)
        self.tag = uuid.uuid4().hex[:8]
        self.addCleanup(self._cleanup)
        pgstore._last_sweep = 0.0

    def _cleanup(self) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.workers where name like %s", (f"{self.tag}%",))
            conn.execute("delete from meeting_ai.rate_limits where key like %s", (f"{self.tag}%",))
            conn.execute("delete from meeting_ai.users where email like %s",
                         (f"%-{self.tag}@test.local",))

    # ---------- fixtures ----------

    def _user(self) -> str:
        with self.pgdb.connect() as conn:
            row = conn.execute(
                "insert into meeting_ai.users (email, name) values (%s, %s) returning id",
                (f"u-{self.tag}@test.local", "u")).fetchone()
        return str(row[0])

    def _session(self, user_id: str, *, expired: bool) -> str:
        token_hash = f"{self.tag}-{uuid.uuid4().hex}"
        age = "now() - interval '1 day'" if expired else "now() + interval '1 day'"
        with self.pgdb.connect() as conn:
            conn.execute(
                "insert into meeting_ai.sessions (token_hash, user_id, expires_at) "
                f"values (%s, %s, {age})", (token_hash, user_id))
        return token_hash

    def _sessions_left(self) -> int:
        with self.pgdb.connect() as conn:
            return conn.execute("select count(*) from meeting_ai.sessions "
                                "where token_hash like %s", (f"{self.tag}%",)).fetchone()[0]

    def _worker(self, name: str, days_ago: int) -> None:
        with self.pgdb.connect() as conn:
            conn.execute(
                "insert into meeting_ai.workers (name, status, last_seen) "
                "values (%s, 'idle', now() - make_interval(days => %s)) "
                "on conflict (name) do update set last_seen = excluded.last_seen",
                (name, days_ago))

    def _workers_left(self) -> set[str]:
        with self.pgdb.connect() as conn:
            rows = conn.execute("select name from meeting_ai.workers where name like %s",
                                (f"{self.tag}%",)).fetchall()
        return {r[0] for r in rows}

    # ---------- สิ่งที่ตั๋วบอกว่ารั่ว ----------

    def test_expired_sessions_are_deleted(self):
        uid = self._user()
        self._session(uid, expired=True)
        self._session(uid, expired=True)
        keep = self._session(uid, expired=False)
        self.assertEqual(self._sessions_left(), 3)

        self.pgstore.sweep(force=True)

        self.assertEqual(self._sessions_left(), 1, "ต้องเหลือเฉพาะใบที่ยังไม่หมดอายุ")
        with self.pgdb.connect() as conn:
            row = conn.execute("select 1 from meeting_ai.sessions where token_hash = %s",
                               (keep,)).fetchone()
        self.assertIsNotNone(row, "เผลอลบ session ที่ยังใช้ได้ = เตะคนที่ล็อกอินอยู่ออก")

    def test_long_gone_workers_are_forgotten_and_recent_ones_are_not(self):
        self._worker(f"{self.tag}-old", days_ago=30)
        self._worker(f"{self.tag}-new", days_ago=1)

        self.pgstore.sweep(force=True)

        self.assertEqual(self._workers_left(), {f"{self.tag}-new"})

    def test_expired_rate_limits_are_deleted(self):
        with self.pgdb.connect() as conn:
            conn.execute("insert into meeting_ai.rate_limits (key, hits, expires_at) "
                         "values (%s, 1, now() - interval '1 hour')", (f"{self.tag}-old",))
            conn.execute("insert into meeting_ai.rate_limits (key, hits, expires_at) "
                         "values (%s, 1, now() + interval '1 hour')", (f"{self.tag}-new",))

        self.pgstore.sweep(force=True)

        with self.pgdb.connect() as conn:
            rows = conn.execute("select key from meeting_ai.rate_limits where key like %s",
                                (f"{self.tag}%",)).fetchall()
        self.assertEqual({r[0] for r in rows}, {f"{self.tag}-new"})

    def test_it_reports_what_it_removed(self):
        uid = self._user()
        self._session(uid, expired=True)
        got = self.pgstore.sweep(force=True)
        self.assertGreaterEqual(got.get("sessions", 0), 1)
        self.assertIn("workers", got)

    # ---------- ตัวคุมความถี่ ----------

    def test_the_second_sweep_within_the_hour_is_skipped(self):
        # เรียกได้ถี่เท่าไรก็ได้คือเงื่อนไขที่ทำให้เกาะทาง claim ได้ตั้งแต่แรก
        self.assertTrue(self.pgstore._claim_sweep())
        self.assertFalse(self.pgstore._claim_sweep(),
                         "รอบที่สองต้องไม่ได้สิทธิ์ ไม่งั้นทุก claim จะยิง DELETE")

    def test_the_gate_uses_the_database_clock_not_the_process(self):
        # สอง instance บน serverless ไม่เห็นนาฬิกาของกันและกัน — ต้องแพ้ที่ฐานข้อมูล
        self.pgstore.sweep(force=True)
        self.pgstore._last_sweep = 0.0          # เหมือน instance ที่เพิ่งเย็นแล้วเกิดใหม่
        self.assertEqual(self.pgstore.sweep_if_due(), {},
                         "instance ใหม่ต้องยังโดนกั้นด้วยนาฬิกาของฐาน")

    def test_a_due_sweep_runs_for_a_fresh_instance(self):
        # กันเทสต์ข้างบนผ่านเพราะ "ไม่เคยทำงานเลย" — ตั้งนาฬิกาให้เลยรอบแล้วต้องได้สิทธิ์
        self.pgstore.sweep(force=True)
        with self.pgdb.connect() as conn:
            conn.execute("update meeting_ai.settings set updated_at = now() - interval '2 days' "
                         "where key = %s", (self.pgstore.SWEEP_KEY,))
        self.pgstore._last_sweep = 0.0
        self.assertNotEqual(self.pgstore.sweep_if_due(), {})

    def test_a_broken_sweep_never_breaks_the_caller(self):
        # มันเกาะอยู่บนทางหยิบงาน ล้มเมื่อไรต้องไม่ลากงานของ worker ล้มตาม
        self.pgstore._last_sweep = 0.0
        with mock.patch.object(self.pgstore, "_claim_sweep", side_effect=RuntimeError("ฐานล่ม")):
            self.assertEqual(self.pgstore.sweep_if_due(), {})


class TestPurgeExpiredShape(unittest.TestCase):
    """ไม่ต้องมี Postgres ก็ตรวจได้ว่าฟังก์ชันคืนตัวเลขให้คนเรียกดูได้."""

    def test_purge_expired_reports_counts(self):
        from meeting_ai.web import pgstore

        src = Path(pgstore.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "purge_expired")
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
        self.assertTrue(returns, "เดิมคืน None เฉย ๆ ไม่มีใครรู้ว่ามันลบอะไรไปบ้าง")


if __name__ == "__main__":
    unittest.main()
