"""BUG-067 — ตัวตรวจ "ล็อกอินบอทแล้วหรือยัง" ผ่านทั้งที่ยังไม่เคยล็อกอิน.

เจ้าของแจ้งว่า "ไม่มี bot ส่งถึงห้อง" (2026-09-19) log ของบอทบอกว่า Meet ตอบทันทีว่า

    You can't join this video call / No one can join a meeting unless invited or
    admitted by the host

แล้วสองนาทีต่อมาหน้าเว็บ redirect ไปหน้าโฆษณา Google Workspace ที่มีปุ่ม "ลงชื่อเข้าใช้"
— บอทเข้าห้องแบบ **ไม่ระบุตัวตน** (สังเกตได้จากขั้น `ตั้งชื่อบอท` ด้วย: ช่องกรอกชื่อโผล่
เฉพาะตอนไม่ได้ล็อกอิน) ห้องนั้นไม่รับ guest จึงปฏิเสธทันที และ **host ไม่เคยเห็นการเคาะ
ประตูเลย** จึงไม่มีอะไรให้กด Admit

แต่ `missing_pieces()` ตอบว่า "ไม่ขาดอะไร" เพราะเช็กแค่ว่าโฟลเดอร์ `bot/profile` ไม่ว่าง
วัดของจริงบนเครื่อง worker: **5 ไฟล์ 92 KB ไม่มี `Default/Cookies` เลย** — คอนเทนเนอร์ที่
เคยรันแล้วออกโดยยังไม่ได้ล็อกอิน ก็ทิ้งไฟล์โครงของ Chromium ไว้พอให้ผ่านการตรวจแล้ว

ผลคือระบบบอกว่าพร้อม แล้วไปล้มกลางห้องประชุมจริงแทน
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import bot


class ProfileCase(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-bug067-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.profile = self.tmp / "profile"
        patch = mock.patch.object(bot, "PROFILE_DIR", self.profile)
        patch.start()
        self.addCleanup(patch.stop)
        # BACKLOG #89 ใส่แคชอายุ 30 วินาทีให้ missing_pieces() — เทสต์ที่ patch ของข้างใน
        # แล้วคาดว่าจะได้คำตอบสด ๆ ต้องล้างแคชก่อน ไม่งั้นได้ของที่เทสต์ก่อนหน้าทิ้งไว้
        bot._missing_cache = None
        self.addCleanup(setattr, bot, "_missing_cache", None)

    def _cookies(self, data: bytes = b"SQLite format 3") -> None:
        (self.profile / "Default").mkdir(parents=True, exist_ok=True)
        (self.profile / "Default" / "Cookies").write_bytes(data)


class TestProfileReady(ProfileCase):

    def test_a_missing_folder_is_not_ready(self):
        self.assertFalse(bot.profile_ready())

    def test_an_empty_folder_is_not_ready(self):
        self.profile.mkdir(parents=True)
        self.assertFalse(bot.profile_ready())

    def test_the_real_worker_state_is_not_ready(self):
        """จำลองของจริง: มีไฟล์โครงของ Chromium แต่ไม่เคยล็อกอิน.

        นี่คือสถานะที่ผ่านการตรวจแบบเดิม แล้วพาไปล้มกลางห้องประชุม
        """
        self.profile.mkdir(parents=True)
        for name in ("Local State", "first_party_sets.db", "Variations",
                     "BrowserMetrics-spare.pma", "SingletonLock"):
            (self.profile / name).write_bytes(b"x" * 100)
        self.assertEqual(len(list(self.profile.iterdir())), 5)
        self.assertFalse(bot.profile_ready(),
                         "โปรไฟล์ที่ไม่เคยล็อกอินยังผ่านการตรวจอยู่")

    def test_an_empty_cookie_db_is_not_ready(self):
        self._cookies(b"")
        self.assertFalse(bot.profile_ready())

    def test_a_logged_in_profile_is_ready(self):
        self._cookies()
        self.assertTrue(bot.profile_ready())


class TestItIsUsedWhereItMatters(ProfileCase):

    def test_missing_pieces_reports_the_login(self):
        self.profile.mkdir(parents=True)
        (self.profile / "Local State").write_bytes(b"x")
        with mock.patch.object(bot.shutil, "which", lambda n: "docker"), \
             mock.patch.object(bot, "_run",
                               lambda *a, **k: mock.Mock(returncode=0, stdout="id\n",
                                                         stderr="")), \
             mock.patch.object(bot, "_image_exists", lambda d: True), \
             mock.patch.object(bot, "_probe_run", lambda d: ""):
            gaps = bot.missing_pieces()
        self.assertTrue(any("bot-login" in g for g in gaps),
                        f"ไม่ได้บอกว่าต้องล็อกอิน: {gaps}")

    def test_a_logged_in_profile_reports_nothing_missing(self):
        self._cookies()
        with mock.patch.object(bot.shutil, "which", lambda n: "docker"), \
             mock.patch.object(bot, "_run",
                               lambda *a, **k: mock.Mock(returncode=0, stdout="id\n",
                                                         stderr="")), \
             mock.patch.object(bot, "_image_exists", lambda d: True), \
             mock.patch.object(bot, "_probe_run", lambda d: ""):
            self.assertEqual(bot.missing_pieces(), [])

    def test_sending_a_bot_stops_before_docker_when_not_logged_in(self):
        self.profile.mkdir(parents=True)
        (self.profile / "Local State").write_bytes(b"x")
        with mock.patch.object(bot, "_docker", lambda: "docker"), \
             mock.patch.object(bot, "build_image", lambda *a, **k: None), \
             mock.patch.object(bot.subprocess, "Popen",
                               mock.Mock(side_effect=AssertionError("ไม่ควรรัน docker"))):
            with self.assertRaises(RuntimeError) as e:
                bot.join_and_record("https://meet.google.com/abc-defg-hij",
                                    self.tmp / "out.wav")
        self.assertIn("bot-login", str(e.exception))


class TestAnUnreadableProfileIsReportedAsSuch(ProfileCase):
    """BUG-069 — โปรไฟล์ที่ root เป็นเจ้าของ: ล็อกอินกี่ครั้งก็ไม่ถูกบันทึก."""

    def _lock_default(self):
        import os
        (self.profile / "Default").mkdir(parents=True)
        (self.profile / "Default").chmod(0o000)
        self.addCleanup((self.profile / "Default").chmod, 0o700)
        return os.access(self.profile / "Default", os.R_OK)

    def test_it_names_the_permission_problem_not_the_login(self):
        if self._lock_default():
            self.skipTest("ทำให้โฟลเดอร์อ่านไม่ได้ไม่สำเร็จ (Windows/root)")
        why = bot.profile_problem()
        self.assertIn("chown", why)
        self.assertNotIn("bot-login", why,
                         "ชี้ให้ไปทำสิ่งที่ไม่มีวันสำเร็จซ้ำอีก")

    def test_a_healthy_profile_reports_no_problem(self):
        self._cookies()
        self.assertEqual(bot.profile_problem(), "")

    def test_an_empty_profile_still_asks_for_bot_login(self):
        self.profile.mkdir(parents=True)
        self.assertIn("bot-login", bot.profile_problem())


if __name__ == "__main__":
    unittest.main()
