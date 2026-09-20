"""BACKLOG #84 — worker บอกเองว่ารับงานชนิดไหนได้ เซิร์ฟเวอร์เลิกเดาแทน.

**เกิดจริง 2026-09-20 และหลุดถึงผู้ใช้**: งาน `ask` (`…​.ask.c5a03d`) ค้างคิว
**17 ชั่วโมง** `attempts=0` ไม่เคยถูกคว้าเลย ขณะที่หน้าเว็บบอกว่า "1 เครื่องพร้อม"

ต้นตอ: `ask` เพิ่งเข้า `runner.job_kinds()` ตอน BACKLOG #54 วันเดียวกัน · Vercel
deploy ให้อัตโนมัติตอน merge แต่ **ไม่มีใครอัปเดตเครื่อง worker** — โค้ดบนเครื่องนั้นค้าง
อยู่ที่ PR #71 ส่วน production ไปถึง #98 แล้ว `job_kinds()` ของเครื่องนั้นจึงคืน
`['process', 'summarize', 'translate', 'bot']` ไม่มี `ask` และ `job_claim` กรองด้วย
kinds ที่ worker ส่งมาตอน claim งานนั้นจึงมองไม่เห็นตลอดกาล

**และ BACKLOG #83 เวอร์ชันแรกจะโกหกในเคสนี้พอดี** — มันให้เซิร์ฟเวอร์คำนวณ kinds จาก
caps ด้วย `runner.job_kinds()` **ของฝั่งเซิร์ฟเวอร์** ซึ่งเป็นโค้ดใหม่ จึงสรุปว่า worker
รับ `ask` ได้ ทั้งที่เครื่องนั้นไม่รู้จัก · สมมติฐานที่ผิดคือ "สองฝั่งรันโค้ดเวอร์ชันเดียวกัน"

กติกาใหม่: caps ที่ worker ส่งมาพก `kinds` ของตัวเองมาด้วย (ไม่ต้อง migrate ฐาน — caps
เป็น jsonb อยู่แล้ว ตามบทเรียน "deploy ถึงก่อน migration") เซิร์ฟเวอร์ใช้ค่านั้นตรง ๆ
และเทียบกับสิ่งที่โค้ดรุ่นนี้จะได้ ไม่ตรง = ติดธง `outdated` ให้หน้า devices เตือน
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import runner
from meeting_ai.web import server

STATIC = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
PGSTORE = (Path(__file__).resolve().parents[1] / "meeting_ai" / "web"
           / "pgstore.py").read_text(encoding="utf-8")
SERVER_PY = (Path(server.__file__)).read_text(encoding="utf-8")


class TestCapsCarryTheKinds(unittest.TestCase):
    """`machine_caps()` คือสิ่งที่ worker ส่งไปกับ heartbeat ทุกครั้ง."""

    def _caps(self, bot: bool):
        with mock.patch.object(runner.stt, "capabilities", return_value={"local": True}), \
                mock.patch.object(runner.diarize, "available", return_value=True), \
                mock.patch.object(runner.diarize, "missing_pieces", return_value=[]), \
                mock.patch.object(runner.bot, "missing_pieces",
                                  return_value=[] if bot else ["Docker"]):
            return runner.machine_caps()

    def test_the_kinds_travel_with_the_caps(self):
        self.assertIn("kinds", self._caps(bot=True))

    def test_they_are_what_this_machine_would_actually_claim(self):
        for bot in (True, False):
            with self.subTest(bot=bot):
                caps = self._caps(bot)
                self.assertEqual(caps["kinds"], runner.job_kinds(caps))

    def test_a_machine_without_docker_does_not_offer_bot(self):
        self.assertNotIn("bot", self._caps(bot=False)["kinds"])

    def test_a_machine_with_docker_does(self):
        self.assertIn("bot", self._caps(bot=True)["kinds"])

    def test_no_migration_is_needed_to_carry_it(self):
        """caps เป็น jsonb อยู่แล้ว — คอลัมน์ใหม่จะทำ production พังก่อน migrate."""
        self.assertIn('"kinds": caps.get("kinds")', PGSTORE)
        self.assertNotIn("add column if not exists kinds",
                         (Path(server.__file__).parent / "schema.sql").read_text("utf-8"))


class TestTheServerTrustsTheWorker(unittest.TestCase):

    def test_it_returns_what_the_worker_said(self):
        self.assertEqual(server.worker_kinds({"kinds": ["process", "ask"]}),
                         ["process", "ask"])

    def test_an_old_worker_that_reports_nothing_gives_none(self):
        # None = "ไม่รู้" ซึ่งต่างจาก [] = "รับอะไรไม่ได้เลย" — หน้าเว็บต้องแยกสองอย่างนี้ออก
        self.assertIsNone(server.worker_kinds({}))
        self.assertIsNone(server.worker_kinds({"kinds": None}))

    def test_rubbish_from_the_worker_is_not_trusted(self):
        # worker คือฝั่งที่เชื่อไม่ได้ (กติกาเดียวกับ web/sanitize.py)
        for junk in ("bot", 42, {"bot": True}):
            with self.subTest(junk=junk):
                self.assertIsNone(server.worker_kinds({"kinds": junk}))

    def test_it_does_not_fall_back_to_guessing(self):
        """เดาแทนคือบั๊กเดิมเป๊ะ ๆ — worker ที่ไม่บอกต้องเป็น "ไม่รู้" ไม่ใช่ "เดาว่าได้ทุกอย่าง"."""
        self.assertIsNone(server.worker_kinds({"can": ["local", "api", "bot"]}))


class TestTheVersionMismatchFlag(unittest.TestCase):

    def test_a_worker_in_step_with_the_server_is_not_flagged(self):
        caps_kinds = runner.job_kinds({"bot": True})
        self.assertFalse(server.worker_outdated({"kinds": caps_kinds, "can": ["bot"]}))

    def test_the_real_2026_09_20_case_is_flagged(self):
        # โค้ด PR #71 บนเครื่อง worker คืนชุดนี้ — ขาด 'ask' ที่ #54 เพิ่งเพิ่ม
        old = ["process", "summarize", "translate", "bot"]
        self.assertTrue(server.worker_outdated({"kinds": old, "can": ["local", "bot"]}))

    def test_a_worker_that_reports_nothing_is_flagged(self):
        self.assertTrue(server.worker_outdated({"can": ["bot"]}))

    def test_order_does_not_matter(self):
        kinds = list(reversed(runner.job_kinds({"bot": False})))
        self.assertFalse(server.worker_outdated({"kinds": kinds, "can": ["local"]}))

    def test_the_flag_rides_along_with_every_worker(self):
        body = SERVER_PY[SERVER_PY.index("def _workers_view"):]
        body = body[:body.index("def _job_scope")]
        self.assertIn('w["outdated"] = worker_outdated(w)', body)
        self.assertLess(body.index('w["outdated"]'), body.index("is_admin"),
                        "ต้องติดไปกับทุกเครื่อง ไม่ใช่เฉพาะที่แอดมินเห็น")


class TestTheDevicesPageSaysSo(unittest.TestCase):

    def test_an_outdated_machine_is_called_out(self):
        self.assertIn("w.alive && w.outdated", APP_JS)
        self.assertIn("คนละรุ่นกับเซิร์ฟเวอร์", APP_JS)

    def test_it_says_what_to_do_about_it(self):
        # "คนละรุ่น" อย่างเดียวไม่ช่วยใคร — คนอ่านต้องรู้ว่าต้องทำอะไรต่อ
        self.assertIn("git pull", APP_JS)

    def test_an_offline_machine_is_not_nagged_about(self):
        # เครื่องที่ดับอยู่แล้วไม่ต้องบอกว่าโค้ดเก่า มันไม่ได้รับงานอยู่แล้ว
        self.assertIn("w.alive && w.outdated", APP_JS)
        self.assertNotIn("${w.outdated ?", APP_JS)

    def test_the_warning_is_visible(self):
        self.assertRegex(CSS, r"\.wk \.wk-old \{[^}]*var\(--warn\)")


if __name__ == "__main__":
    unittest.main()
