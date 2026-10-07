"""BACKLOG #97 — สองบทเรียนจากเหตุ production ล่ม 2026-10-06/07.

เหตุการณ์: รหัสผ่านฐานข้อมูลถูกเปลี่ยน `DATABASE_URL` ที่ Vercel เป็นรหัสเก่า ทุกคำขอที่
แตะฐานตอบ 500 เป็นเวลา **17 ชั่วโมง 17 นาที** (6 ต.ค. 17:18:16 → 7 ต.ค. 10:34:57,
นับได้ 709 ครั้งจาก log ของ worker) รู้เพราะบังเอิญไปอ่าน log ของ worker ไม่มีอะไรแจ้งเตือน

**ข้อ 1 — `/api/health` โกหกตลอดเวลาที่ล่ม**
มันตอบ `{"ok": true, "mode": "postgres", "auth": true}` ตลอด เพราะ `backend.health()`
เรียกแค่ `db.missing_pieces()` ซึ่งตรวจว่า "มีตัวแปร DATABASE_URL ไหม + import psycopg
ได้ไหม" — จริงทั้งคู่แม้ฐานจะปฏิเสธรหัสผ่าน ส่วน `"ok": true` เป็นค่าคงที่ที่เขียนตายไว้
เส้นตรวจสุขภาพที่โกหกแย่กว่าไม่มีเส้นตรวจ เพราะชี้คนไล่ปัญหาไปผิดทาง (ผมเสียเวลาไปกับมันเอง)

**ข้อ 2 — ข้อความ error ของ LLM พ่น body ดิบออกหน้าเว็บ**
`summarizer.py` แปะ `body[:500]` ลงในข้อความที่ไหลไปถึงผู้ใช้ วันนั้นผู้ใช้จึงเห็น
`sk-...ea09` กับเวลาหมดอายุของคีย์ LiteLLM บนจอ — ขัดกับกฎของโปรเจกต์เองที่ว่าข้อความ error
บนเว็บเป็นของผู้ใช้ ห้ามมี log ดิบ (CLAUDE.md ข้อ 2 · BACKLOG #40 · #51/#52)
"""

from __future__ import annotations

import json
import logging
import re
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from _harness import CloudCase, LocalCase          # type: ignore

from meeting_ai import summarizer                   # noqa: E402
from meeting_ai.web import backend, db              # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SUMMARIZER = (ROOT / "meeting_ai" / "summarizer.py").read_text(encoding="utf-8")
DB_SRC = (ROOT / "meeting_ai" / "web" / "db.py").read_text(encoding="utf-8")


# ----------------------------------------------------- ข้อ 1: /api/health

class TestHealthTellsTheTruth(CloudCase):

    def _health(self, ok: bool, err: str = "OperationalError", token: str | None = None):
        with mock.patch.object(db, "ping_cached", return_value=(ok, "" if ok else err)):
            cookies = {"mai_session": token} if token else None
            return self.get("/api/health", cookies=cookies)

    def test_a_healthy_system_answers_200(self):
        status, body, _ = self._health(True)
        self.assertEqual(status, 200)
        self.assertIs(body["ok"], True)
        self.assertEqual(body["db"], "ok")

    def test_a_broken_database_is_not_ok(self):
        """ข้อที่ทำให้ตั๋วนี้เกิด — 17 ชั่วโมงที่ผ่านมามันตอบ ok: true."""
        status, body, _ = self._health(False)
        self.assertIs(body["ok"], False)
        self.assertEqual(body["db"], "fail")

    def test_a_broken_database_answers_503(self):
        """ตัวเฝ้าระวังภายนอกต้องจับได้จากสถานะ ไม่ต้องอ่าน body."""
        status, _, _ = self._health(False)
        self.assertEqual(status, 503)

    def test_an_outsider_learns_nothing_about_why(self):
        _, body, _ = self._health(False, err="OperationalError")
        blob = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("OperationalError", blob)
        self.assertNotIn("db_missing", blob)

    def test_an_admin_gets_the_error_class(self):
        _, body, _ = self._health(False, err="OperationalError", token=self.tokAdm)
        self.assertEqual(body["db_error"], "OperationalError")

    def test_it_is_still_public(self):
        """ตัวเฝ้าระวังเรียกได้โดยไม่ต้องล็อกอิน — ไม่งั้นก็เฝ้าไม่ได้."""
        from meeting_ai.web import server
        self.assertIn(("health",), server.PUBLIC_API)


class TestTheProbeItself(unittest.TestCase):

    def test_it_actually_runs_a_query(self):
        """`missing_pieces()` อย่างเดียวคือสิ่งที่ทำให้เส้นนี้โกหกมา 17 ชั่วโมง.

        เวอร์ชันแรกของเทสต์นี้ตรวจว่าซอร์สของ `ping()` มีคำว่า "select 1" อยู่ —
        **ซึ่งเป็นเทสต์เปล่า** เพราะ docstring ของฟังก์ชันนั้นเองก็มีคำนี้อยู่
        ถอดคำสั่ง query ออกทั้งบรรทัดแล้วเทสต์ยังเขียว (เจอตอนกลับกลไก)
        """
        executed = []

        class FakeCursor:
            def fetchone(self):
                return (1,)

        class FakeConn:
            def execute(self, sql, *a):
                executed.append(sql)
                return FakeCursor()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class FakePool:
            def connection(self, timeout=None):
                return FakeConn()

        with mock.patch.object(db, "missing_pieces", return_value=[]), \
             mock.patch.object(db, "_get_pool", return_value=FakePool()):
            ok, err = db.ping()
        self.assertTrue(ok, err)
        self.assertTrue(executed, "ping() ไม่ได้ยิง query เลย — ยืม connection เฉย ๆ")
        self.assertIn("select", executed[0].lower())

    def test_it_reports_the_class_not_the_message(self):
        """ข้อความของ psycopg พ่วงโฮสต์/ชื่อฐาน/ผู้ใช้มาด้วย — ห้ามออกไปทางเส้นสาธารณะ."""
        class Boom(Exception):
            pass

        with mock.patch.object(db, "missing_pieces", return_value=[]), \
             mock.patch.object(db, "_get_pool",
                               side_effect=Boom("host=db.internal user=admin password=hunter2")):
            ok, err = db.ping()
        self.assertFalse(ok)
        self.assertEqual(err, "Boom")
        self.assertNotIn("hunter2", err)
        self.assertNotIn("db.internal", err)

    def test_missing_config_does_not_try_to_connect(self):
        with mock.patch.object(db, "missing_pieces", return_value=["ตัวแปร DATABASE_URL"]), \
             mock.patch.object(db, "_get_pool", side_effect=AssertionError("ไม่ควรถูกเรียก")):
            ok, err = db.ping()
        self.assertFalse(ok)
        self.assertEqual(err, "missing_config")

    def test_the_public_endpoint_cannot_be_used_to_drain_the_pool(self):
        """เส้นสาธารณะ + พูล 4 คอนเนกชัน = ช่องดูดคอนเนกชันถ้าตรวจจริงทุกคำขอ."""
        calls = []

        def fake_ping(timeout=None):
            calls.append(1)
            return True, ""

        with mock.patch.object(db, "ping", fake_ping), \
             mock.patch.object(db, "_ping_result", None), \
             mock.patch.object(db, "_ping_at", 0.0):
            for _ in range(20):
                db.ping_cached()
        self.assertEqual(len(calls), 1, "ตรวจจริงทุกคำขอ = ช่องดูดคอนเนกชัน")

    def test_the_cache_is_short_enough_to_be_useful(self):
        """แคชยาวเกินไปก็กลายเป็นโกหกแบบใหม่ — ล่มแล้วยังตอบว่าดีอยู่นาน ๆ."""
        self.assertLessEqual(db.PING_CACHE_SEC, 15.0)
        self.assertLess(db.PING_TIMEOUT, db.CONNECT_TIMEOUT)


class TestFileModeIsUnaffected(LocalCase):
    """โหมดไฟล์ไม่มีฐานข้อมูล — ห้ามกลายเป็น 503 เพราะของที่มันไม่ได้ใช้."""

    def test_it_stays_healthy(self):
        status, body, _ = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertIs(body["ok"], True)
        self.assertNotIn("db", body)


# ------------------------------------------------- ข้อ 2: ข้อความของ LLM

def http_error(code: int, body: str) -> urllib.error.HTTPError:
    import io as _io
    return urllib.error.HTTPError("http://x/v1/chat", code, "err", {},
                                  _io.BytesIO(body.encode("utf-8")))


class TestTheLlmErrorKeepsItsSecrets(unittest.TestCase):

    REAL_BODY = ('{"error":{"message":"Authentication Error - Expired Key. '
                 'Key Expiry time 2026-09-25 04:54:45.137000+00:00","type":"expired_key",'
                 '"param":"sk-...ea09","code":"401"}}')

    def _raise(self, code: int, body: str) -> str:
        with mock.patch.object(summarizer, "_stream_chat", side_effect=http_error(code, body)):
            with self.assertRaises(RuntimeError) as cm:
                summarizer._request([{"role": "user", "content": "hi"}],
                                    0.2, 30, 500, retries=0)
        return str(cm.exception)

    def test_the_users_message_has_no_trace_of_the_key(self):
        """ข้อความจริงที่ผู้ใช้เห็นเมื่อ 2026-10-07 มี sk-...ea09 อยู่ในนั้น."""
        msg = self._raise(401, self.REAL_BODY)
        for leak in ("sk-", "ea09", "expired_key", "Key Expiry", "Authentication Error"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, msg)

    def test_it_still_says_what_to_do(self):
        msg = self._raise(401, self.REAL_BODY)
        self.assertIn("คีย์", msg)
        self.assertIn("เครื่องประมวลผล", msg)

    def test_the_status_code_survives_because_chunking_reads_it(self):
        """`_worth_chunking()` อ่าน `HTTP <code>` จากข้อความ ตัดทิ้งเมื่อไหร่ฟีเจอร์แบ่งก้อนตาย."""
        self.assertIn("HTTP 400", self._raise(400, "context length exceeded"))
        self.assertTrue(summarizer._worth_chunking(
            RuntimeError(self._raise(400, "x")), "ข้อความยาว ๆ"))
        self.assertFalse(summarizer._worth_chunking(
            RuntimeError(self._raise(401, self.REAL_BODY)), "ข้อความยาว ๆ"))

    def test_each_class_of_failure_says_something_different(self):
        seen = {code: self._raise(code, "ของภายใน") for code in (401, 429, 500, 400)}
        self.assertEqual(len(set(seen.values())), 4, seen)
        for code, msg in seen.items():
            with self.subTest(code=code):
                self.assertNotIn("ของภายใน", msg)

    def test_the_raw_body_goes_to_the_worker_log_instead(self):
        """ตัดออกจากหน้าเว็บแล้วต้องยังไล่ปัญหาได้ — ไม่ใช่หายไปเฉย ๆ."""
        with self.assertLogs("meeting_ai.summarizer", level=logging.WARNING) as caught:
            self._raise(401, self.REAL_BODY)
        self.assertIn("ea09", "\n".join(caught.output))

    def test_a_connection_failure_hides_the_endpoint_too(self):
        with mock.patch.object(summarizer, "_stream_chat",
                               side_effect=urllib.error.URLError("[Errno 111] db.internal:4000")):
            with self.assertRaises(RuntimeError) as cm:
                summarizer._request([{"role": "user", "content": "hi"}],
                                    0.2, 30, 500, retries=0)
        msg = str(cm.exception)
        self.assertNotIn("db.internal", msg)
        self.assertNotIn("Errno", msg)

    def test_the_give_up_message_carries_no_detail_either(self):
        # บรรทัด log.warning เอ่ย last_err ได้ (นั่นคือที่ที่รายละเอียดควรไปอยู่)
        # ที่ห้ามคือบรรทัด raise ซึ่งข้อความจะไหลไปถึงหน้าเว็บ
        raises = [l for l in SUMMARIZER.split(chr(10))
                  if "raise RuntimeError" in l and "เรียก LLM ไม่สำเร็จ" in l]
        self.assertEqual(len(raises), 1, raises)
        self.assertNotIn("last_err", raises[0])

    def test_nothing_slices_the_body_into_a_message_any_more(self):
        self.assertNotIn("body[:500]}", SUMMARIZER.replace('f"LLM ตอบ HTTP {code} — คำตอบดิบ: {body[:500]}"', ""))


if __name__ == "__main__":
    unittest.main()
