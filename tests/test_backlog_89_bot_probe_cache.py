"""BACKLOG #89 — แคชผลตรวจความพร้อมของบอท ไม่ให้ทุกคำขอไปรันคอนเทนเนอร์.

`bot.missing_pieces()` จบด้วย `_probe_run()` ซึ่ง **สั่ง `docker run` จริงหนึ่งครั้ง**
เพื่อพิสูจน์ว่า mount ได้ (จำเป็น — `docker info` ผ่านไม่ได้แปลว่า `docker run` จะผ่าน
ดู BUG-067) แต่ `GET /api/config` เรียกมันทุกคำขอผ่าน `server._bot_state(None)`

**วัดบนเครื่องเจ้าของ (Windows + Docker Desktop, image พร้อม): 1.05 วินาทีต่อครั้ง**

อาการที่โผล่มาจริง 2026-09-22 หลังเจ้าของเปิด Docker Desktop:

| | ก่อน | หลังใส่แคช |
|---|---|---|
| `test_bug_011_body_size_caps` | หมดเวลา 600 วินาที | **23 วินาที ผ่าน** |
| `test_backlog_46_range_not_satisfiable` | หมดเวลา 600 วินาที | **12 วินาที ผ่าน** |
| `test_bug_015_static_path_guard` | 470 วินาที ล้ม | **6 วินาที ผ่าน** |

ก่อนเปิด Docker ฟังก์ชันนี้คืนค่าทันทีที่ด่าน "ไม่มี docker" สวีทจึงเคยจบใน 251 วินาที
พอมี Docker จริงบนเครื่อง ชุดทดสอบที่เปิดเซิร์ฟเวอร์ใหม่ทุกเทสต์ (`_wait_until_ready`
ยิง `/api/config` จนกว่าจะได้ 200) ก็จ่ายค่าคอนเทนเนอร์ทุกครั้ง

**ไม่ใช่แค่เรื่องเทสต์** — หน้าเว็บโหมดไฟล์ก็งอกคอนเทนเนอร์ทิ้งทุกคำขอเหมือนกัน
ฝั่ง production ไม่โดน เพราะที่นั่นอ่านจาก caps ที่ worker ส่งมา (`_bot_state(caps)`)
"""

from __future__ import annotations

import threading
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import bot

BOT_PY = (Path(bot.__file__)).read_text(encoding="utf-8")
SERVER_PY = (Path(bot.__file__).parent / "web" / "server.py").read_text(encoding="utf-8")


class CacheCase(unittest.TestCase):

    def setUp(self) -> None:
        bot._missing_cache = None
        self.addCleanup(setattr, bot, "_missing_cache", None)


class TestItStopsRepeatingTheExpensiveProbe(CacheCase):

    def test_the_second_call_does_not_probe_again(self):
        with mock.patch.object(bot, "_probe_missing", return_value=[]) as probe:
            bot.missing_pieces()
            bot.missing_pieces()
            bot.missing_pieces()
        self.assertEqual(probe.call_count, 1)

    def test_the_answer_is_the_same_every_time(self):
        with mock.patch.object(bot, "_probe_missing", return_value=["Docker"]):
            first = bot.missing_pieces()
            second = bot.missing_pieces()
        self.assertEqual(first, ["Docker"])
        self.assertEqual(second, ["Docker"])

    def test_it_probes_again_once_the_cache_is_stale(self):
        with mock.patch.object(bot, "_probe_missing", return_value=[]) as probe:
            bot.missing_pieces()
            # ทำให้ของในแคชเก่าเกิน TTL โดยไม่ต้องรอจริง
            when, value = bot._missing_cache
            bot._missing_cache = (when - bot._MISSING_TTL - 1, value)
            bot.missing_pieces()
        self.assertEqual(probe.call_count, 2)

    def test_zero_max_age_always_probes(self):
        """คนที่ต้องการคำตอบสด ๆ (เช่นก่อนส่งบอทจริง) ต้องข้ามแคชได้."""
        with mock.patch.object(bot, "_probe_missing", return_value=[]) as probe:
            bot.missing_pieces()
            bot.missing_pieces(max_age=0)
            bot.missing_pieces(max_age=0)
        self.assertEqual(probe.call_count, 3)

    def test_a_fresh_probe_refreshes_the_cache(self):
        # ไม่งั้นการบังคับตรวจใหม่จะแพงตลอดกาลเพราะไม่มีใครเก็บผลไว้
        with mock.patch.object(bot, "_probe_missing", return_value=["x"]) as probe:
            bot.missing_pieces(max_age=0)
            bot.missing_pieces()
        self.assertEqual(probe.call_count, 1)


class TestTheCacheCannotBePoisoned(CacheCase):

    def test_the_caller_gets_a_copy(self):
        """ผู้เรียกบางรายต่อ list ที่ได้ไปใช้ต่อ — ถ้าคืนตัวเดียวกันแคชจะเพี้ยนตามไปด้วย."""
        with mock.patch.object(bot, "_probe_missing", return_value=["Docker"]):
            got = bot.missing_pieces()
            got.append("ของแปลกปลอม")
            again = bot.missing_pieces()
        self.assertEqual(again, ["Docker"])

    def test_mutating_a_cached_answer_does_not_change_the_next_one(self):
        """เส้น "อ่านจากแคช" โดยเฉพาะ.

        เวอร์ชันแรกของเทสต์ชุดนี้แก้ list ที่ได้จากการเรียก **ครั้งแรก** ซึ่งเป็น list ที่เพิ่ง
        สร้างใหม่ ไม่ใช่ตัวในแคช มุตันต์ที่คืน `cached[1]` ตรง ๆ จึงรอดไปได้
        """
        with mock.patch.object(bot, "_probe_missing", return_value=["Docker"]):
            bot.missing_pieces()                 # ครั้งแรก: เติมแคช
            from_cache = bot.missing_pieces()    # ครั้งที่สอง: อ่านจากแคช
            from_cache.append("ของแปลกปลอม")
            self.assertEqual(bot.missing_pieces(), ["Docker"])

    def test_what_is_stored_is_not_the_list_the_prober_returned(self):
        source = ["Docker"]
        with mock.patch.object(bot, "_probe_missing", return_value=source):
            bot.missing_pieces()
        source.append("เปลี่ยนทีหลัง")
        with mock.patch.object(bot, "_probe_missing", return_value=[]):
            self.assertEqual(bot.missing_pieces(), ["Docker"])


class TestItIsSafeFromSeveralThreads(CacheCase):
    """เซิร์ฟเวอร์เป็น ThreadingHTTPServer — หลายคำขอวิ่งชนกันได้จริง."""

    def test_concurrent_callers_all_get_an_answer(self):
        out = []

        def slow():
            import time as _t
            _t.sleep(0.02)
            return ["Docker"]

        with mock.patch.object(bot, "_probe_missing", side_effect=slow):
            threads = [threading.Thread(target=lambda: out.append(bot.missing_pieces()))
                       for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
        self.assertEqual(len(out), 8)
        self.assertTrue(all(v == ["Docker"] for v in out), out)

    def test_both_the_read_and_the_write_are_guarded_by_the_lock(self):
        """การแข่งกันของเธรดทดสอบแบบกำหนดผลไม่ได้ — ตรึงที่ซอร์สแทน.

        เวอร์ชันแรกเช็คแค่ว่ามีคำว่า `with _missing_lock:` อยู่ในฟังก์ชัน ซึ่งยังจริงอยู่
        แม้จะถอด lock ของ **ขาอ่าน** ออก เพราะขาเขียนยังมีอยู่ มุตันต์นั้นจึงรอด
        """
        body = BOT_PY[BOT_PY.index("def missing_pieces("):]
        body = body[:body.index("def _probe_missing")]
        self.assertEqual(body.count("with _missing_lock:"), 2,
                         "ต้องล็อกทั้งตอนอ่านแคชและตอนเขียนแคช")
        self.assertIn("cached = _missing_cache", body)


class TestTheExpensiveWorkStayedWhereItWas(unittest.TestCase):
    """แคชต้องไม่เปลี่ยน *คำตอบ* — ด่านทั้งสี่ของ BUG-067 ต้องยังอยู่ครบ."""

    def test_the_prober_still_checks_all_four_things(self):
        body = BOT_PY[BOT_PY.index("def _probe_missing"):]
        body = body[:body.index("\ndef ", 10)]
        for piece in ('shutil.which("docker")', '"info"', "_image_exists", "profile_problem",
                      "_probe_run"):
            with self.subTest(piece=piece):
                self.assertIn(piece, body)

    def test_the_container_probe_is_last(self):
        # มันแพงที่สุด — ด่านถูก ๆ ต้องคัดออกก่อน
        body = BOT_PY[BOT_PY.index("def _probe_missing"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertLess(body.index("_image_exists"), body.index("_probe_run"))

    def test_the_config_route_goes_through_the_cached_function(self):
        body = SERVER_PY[SERVER_PY.index("def _bot_state"):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("bot.missing_pieces()", body)
        self.assertNotIn("_probe_missing", body)


if __name__ == "__main__":
    unittest.main()
