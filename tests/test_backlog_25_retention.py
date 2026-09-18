"""BACKLOG #25 — ภาพหน้าจอห้องประชุมและเสียงที่อัดค้างไว้ ต้องมีอายุ ไม่ใช่เก็บตลอดกาล.

ของสองกองนี้ไม่มีใครลบให้เลย และไม่ใช่แค่เรื่องพื้นที่ดิสก์:

* `logs/bot_*.png` คือภาพหน้าจอ **ห้องประชุมจริง** — เห็นชื่อผู้เข้าร่วม แชท สไลด์
* `recordings/bot/<tag>_<job>/` ที่รอดจาก `_prune_stages()` คือโฟลเดอร์ที่มี wav ขนาดไม่ใช่
  ศูนย์ = **เสียงประชุมจริงที่กำพร้า** เดิมเก็บไว้ "ให้คนตัดสินใจ" ซึ่งแปลว่าตลอดไป
  เพราะไม่มีใครมานั่งดู

เส้นที่ห้ามหลุด และเป็นเหตุผลที่เทสต์ไฟล์นี้ยาวกว่าที่ควร: ตัวลบนี้ลบ **เสียงที่อาจไม่มีสำเนา
ที่อื่น** พลาดทีเดียวคือประชุมของลูกค้าหาย จึงต้องมีสองด่านเสมอ — เก่าเกินกำหนด **และ**
ไม่ใช่ของ container ที่ยังรันอยู่ ถ้าถาม docker ไม่ได้ก็ไม่แตะโฟลเดอร์พักเลย
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _harness import backend  # noqa: F401

from meeting_ai import bot, worker

DAY = 86400


class RetentionCase(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-ret-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.logs = self.tmp / "logs"
        self.stage = self.tmp / "bot"
        self.logs.mkdir()
        self.stage.mkdir()
        for target in (mock.patch.object(bot, "DEBUG_DIR", self.logs),
                       mock.patch.object(bot, "STAGE_DIR", self.stage)):
            target.start()
            self.addCleanup(target.stop)
        self.live = frozenset()
        p = mock.patch.object(bot, "_live_containers", lambda *a, **k: self.live)
        p.start()
        self.addCleanup(p.stop)

    # ---------- fixtures ----------

    def shot(self, name: str, age_days: float) -> Path:
        path = self.logs / name
        path.write_bytes(b"png" * 100)
        self._age(path, age_days)
        return path

    def staging(self, name: str, age_days: float, wav_bytes: int = 4096) -> Path:
        d = self.stage / name
        d.mkdir(parents=True)
        (d / "audio.wav").write_bytes(b"x" * wav_bytes)
        self._age(d / "audio.wav", age_days)
        self._age(d, age_days)
        return d

    @staticmethod
    def _age(path: Path, days: float) -> None:
        when = time.time() - days * DAY
        os.utime(path, (when, when))


class TestWhatGetsRemoved(RetentionCase):

    def test_an_old_screenshot_goes(self):
        old = self.shot("bot_debug_20260101-000000-abc.png", age_days=45)
        got = bot.prune_old_artifacts(days=30)
        self.assertFalse(old.exists())
        self.assertEqual(got["shots"], 1)
        self.assertGreater(got["bytes"], 0)

    def test_a_recent_screenshot_stays(self):
        fresh = self.shot("bot_debug_20260918-000000-abc.png", age_days=3)
        bot.prune_old_artifacts(days=30)
        self.assertTrue(fresh.exists())

    def test_an_old_orphaned_recording_goes(self):
        old = self.staging("gb10_20260101-000000-abc", age_days=45)
        got = bot.prune_old_artifacts(days=30)
        self.assertFalse(old.exists())
        self.assertEqual(got["stages"], 1)

    def test_a_recent_orphaned_recording_stays(self):
        fresh = self.staging("gb10_20260918-000000-abc", age_days=2)
        bot.prune_old_artifacts(days=30)
        self.assertTrue(fresh.exists())

    def test_all_three_shot_kinds_are_covered(self):
        for name in bot.SHOTS:
            with self.subTest(name=name):
                shot = self.shot(f"{Path(name).stem}_job.png", age_days=45)
                bot.prune_old_artifacts(days=30)
                self.assertFalse(shot.exists())


class TestWhatMustSurvive(RetentionCase):

    def test_it_never_touches_the_worker_log(self):
        # logs/ ไม่ได้มีแต่ภาพ — worker.log คือไฟล์ที่คนกำลังใช้ไล่ปัญหาอยู่
        log = self.logs / "worker.log"
        log.write_text("บรรทัดที่ต้องไม่หาย", encoding="utf-8")
        self._age(log, 400)
        bot.prune_old_artifacts(days=30)
        self.assertTrue(log.exists())
        self.assertIn("ต้องไม่หาย", log.read_text(encoding="utf-8"))

    def test_it_never_touches_other_peoples_files(self):
        other = self.logs / "screenshot-ของฉัน.png"
        other.write_bytes(b"mine")
        self._age(other, 400)
        bot.prune_old_artifacts(days=30)
        self.assertTrue(other.exists(), "ลบเฉพาะไฟล์ที่เราสร้างเอง ไม่ใช่ทุกอย่างใน logs/")

    def test_a_live_container_keeps_its_folder_however_old_it_looks(self):
        # ffmpeg อาจกำลังเขียน wav อยู่ตรงนั้น — อายุไฟล์ไม่ใช่หลักฐานว่าไม่มีใครใช้
        d = self.staging("gb10_job", age_days=400)
        self.live = frozenset({bot.PREFIX + d.name})
        got = bot.prune_old_artifacts(days=30)
        self.assertTrue(d.exists())
        self.assertEqual(got["stages"], 0)

    def test_nothing_is_touched_when_docker_cannot_be_asked(self):
        # ตอบไม่ได้ = ไม่รู้ว่าใครยังอยู่ ลบเสียงทิ้งตอนนั้นคือเดา
        d = self.staging("gb10_job", age_days=400)
        with mock.patch.object(bot, "_live_containers", lambda *a, **k: None):
            got = bot.prune_old_artifacts(days=30)
        self.assertTrue(d.exists())
        self.assertEqual(got["stages"], 0)

    def test_but_screenshots_are_still_pruned_without_docker(self):
        # ภาพไม่ใช่ไฟล์ที่ container กำลังเขียน จึงไม่ต้องรอคำตอบจาก docker
        shot = self.shot("bot_debug_job.png", age_days=400)
        with mock.patch.object(bot, "_live_containers", lambda *a, **k: None):
            got = bot.prune_old_artifacts(days=30)
        self.assertFalse(shot.exists())
        self.assertEqual(got["shots"], 1)

    def test_zero_days_means_keep_forever(self):
        shot = self.shot("bot_debug_job.png", age_days=9999)
        d = self.staging("gb10_job", age_days=9999)
        got = bot.prune_old_artifacts(days=0)
        self.assertTrue(shot.exists())
        self.assertTrue(d.exists())
        self.assertEqual(got, {"shots": 0, "stages": 0, "bytes": 0})

    def test_a_folder_whose_audio_was_just_written_is_not_old(self):
        # mtime ของโฟลเดอร์ไม่ขยับตอนไฟล์ข้างในถูกเขียนทับ ดูแต่โฟลเดอร์จะตัดสินผิด
        d = self.staging("gb10_job", age_days=400)
        self._age(d / "audio.wav", 0)
        bot.prune_old_artifacts(days=30)
        self.assertTrue(d.exists(), "ต้องดู mtime ของไฟล์ข้างในด้วย ไม่ใช่แค่ของโฟลเดอร์")


class TestTheDefaults(unittest.TestCase):

    def test_the_default_is_thirty_days(self):
        from meeting_ai.config import config

        self.assertEqual(config.bot_retention_days, 30)

    def test_the_setting_is_read_when_no_days_are_passed(self):
        with mock.patch.object(bot.config, "bot_retention_days", 0):
            self.assertEqual(bot.prune_old_artifacts(),
                             {"shots": 0, "stages": 0, "bytes": 0})


class TestTheWorkerRunsIt(unittest.TestCase):
    """ตัวเก็บกวาดที่ไม่มีใครเรียกก็ไม่ได้เก็บอะไร — บทเรียนเดียวกับ BACKLOG #24."""

    def setUp(self) -> None:
        worker._last_prune = 0.0
        self.addCleanup(setattr, worker, "_last_prune", 0.0)

    def test_it_runs_once_then_holds_off(self):
        calls = []
        with mock.patch.object(bot, "prune_old_artifacts",
                               lambda *a, **k: calls.append(1) or {"shots": 0, "stages": 0}):
            worker.prune_artifacts_if_due()
            worker.prune_artifacts_if_due()
            worker.prune_artifacts_if_due()
        self.assertEqual(len(calls), 1, "ลูปหลักเรียกทุกงวด ของจริงต้องเกิดวันละครั้ง")

    def test_force_ignores_the_timer(self):
        calls = []
        with mock.patch.object(bot, "prune_old_artifacts",
                               lambda *a, **k: calls.append(1) or {"shots": 0, "stages": 0}):
            worker.prune_artifacts_if_due()
            worker.prune_artifacts_if_due(force=True)
        self.assertEqual(len(calls), 2)

    def test_a_failure_does_not_reach_the_caller(self):
        # มันเกาะอยู่บนลูปรับงาน ล้มเมื่อไรต้องไม่ลากงานของ worker ล้มตาม
        with mock.patch.object(bot, "prune_old_artifacts",
                               side_effect=OSError("ดิสก์มีปัญหา")):
            self.assertEqual(worker.prune_artifacts_if_due(), {})

    def test_the_startup_path_starts_the_pruner(self):
        src = Path(worker.__file__).read_text(encoding="utf-8")
        self.assertIn("_start_pruner()", src[src.index("def run("):])

    def test_it_runs_on_a_daemon_thread_not_on_the_claim_loop(self):
        # รอบแรกผมวางไว้บนลูปรับงาน แล้ว CI ฝั่ง Windows จับได้: prune ถาม docker
        # (รอได้ถึง DOCKER_TIMEOUT) + เดินไล่โฟลเดอร์ = หน่วงทุกงาน และชนเพดาน drain ของ #20
        src = Path(worker.__file__).read_text(encoding="utf-8")
        loop = src[src.index('    while not stopping["flag"]:'):]
        self.assertNotIn("prune_artifacts_if_due", loop,
                         "เก็บกวาดต้องไม่คั่นทางหยิบงาน")
        self.assertIn("mai-bot-pruner", src)

    def test_the_pruner_thread_is_a_daemon(self):
        # ไม่ใช่ daemon = Ctrl+C แล้วโพรเซสไม่ยอมจบ เพราะเธรดนี้นอนรออีกหนึ่งวัน
        with mock.patch.object(worker.threading, "Thread") as thread:
            worker._start_pruner()
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs.get("daemon"))


if __name__ == "__main__":
    unittest.main()
