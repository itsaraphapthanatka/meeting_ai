"""BACKLOG #39 — คำสั่ง docker ทุกตัวต้องมีเพดานเวลา และบอทที่ไม่ยอมตายต้องไม่ทำให้เสียงหาย.

สองเรื่องในตั๋วเดียว:

1. `subprocess.run` ดิบๆ ไม่มี timeout (`docker rm -f`, `docker stop`, `docker build`)
   Docker Desktop ค้างได้จริง (อัปเดตตัวเอง / WSL สะดุด) คำสั่งที่ไม่มีเพดานเวลาจะแขวน
   worker ไว้เงียบๆ ช่องงานถูกจองค้าง และไม่มีใครได้ error อะไรเลย

2. `proc.wait(timeout=120)` เปล่าๆ นอก except — **ข้อนี้อันตรายกว่า** พอ container
   ไม่ยอมตาย TimeoutExpired หลุดออกจาก join_and_record ทั้งดุ้น ข้ามท่อนย้ายไฟล์เสียงไป
   ปลายทางและท่อนเก็บกวาด เสียงที่อัดมาทั้งชั่วโมงค้างอยู่ในโฟลเดอร์พัก แล้วงานถูกรายงานว่า
   ล้มด้วย traceback ภาษาอังกฤษในการ์ดงานของเจ้าของการประชุม

กับดักที่ต้องคุมไว้ด้วยเทสต์: `_run()` มีเพดานเริ่มต้น 25 วินาที ถ้า route `stop -t 30`
เข้าไปเฉยๆ ตามที่ตั๋วบอก เพดานจะมาถึง**ก่อน**ระยะผ่อนผันจะหมด = ยกเลิกคำสั่งหยุดของตัวเอง
ตอนบอทกำลังปิดไฟล์เสียงพอดี ได้ wav ที่ header ไม่ถูกปิด
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _harness import backend  # noqa: F401

from meeting_ai import bot

BOT_SRC = Path(bot.__file__)


class TestEveryDockerCallHasADeadline(unittest.TestCase):
    """ตรวจจากซอร์สจริง — เขียน subprocess.run ใหม่โดยลืม timeout เมื่อไหร่ เทสต์นี้ล้มทันที."""

    @staticmethod
    def _runs() -> list[ast.Call]:
        tree = ast.parse(BOT_SRC.read_text(encoding="utf-8"))
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "run" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "subprocess"]

    def test_the_check_itself_finds_the_calls(self):
        # กันเทสต์ผ่านแบบว่างเปล่า: ถ้า walk หาไม่เจออะไรเลย ข้อถัดไปจะผ่านฟรี
        self.assertTrue(self._runs(), "หา subprocess.run ในซอร์สไม่เจอ — ตัวตรวจพัง ไม่ใช่โค้ดดี")

    def test_every_subprocess_run_passes_a_timeout(self):
        for call in self._runs():
            with self.subTest(line=call.lineno):
                self.assertIn("timeout", {kw.arg for kw in call.keywords},
                              f"bot.py:{call.lineno} เรียก docker โดยไม่มีเพดานเวลา")

    def test_no_raw_check_true_left(self):
        # check=True โยน CalledProcessError ซึ่งไปโผล่เป็น traceback อังกฤษในการ์ดงาน
        for call in self._runs():
            for kw in call.keywords:
                if kw.arg == "check":
                    self.assertFalse(getattr(kw.value, "value", False),
                                     f"bot.py:{call.lineno} ยังใช้ check=True")


class TestStopDeadlineOutlastsTheGrace(unittest.TestCase):
    """เพดานเวลาต้องยาวกว่าระยะผ่อนผันเสมอ ไม่งั้นเราตัดคำสั่งหยุดของตัวเองทิ้ง."""

    def _stop_call(self, grace=None):
        seen = {}

        def fake_run(cmd, text=False, timeout=None):
            seen.update(cmd=cmd, timeout=timeout)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(bot, "_run", fake_run):
            if grace is None:
                bot._stop("docker", "c1")
            else:
                bot._stop("docker", "c1", grace)
        return seen

    def test_default_grace(self):
        seen = self._stop_call()
        self.assertEqual(seen["cmd"], ["docker", "stop", "-t", str(bot.STOP_GRACE), "c1"])
        self.assertGreater(seen["timeout"], bot.STOP_GRACE)

    def test_the_in_room_grace_is_longer_than_the_default_run_deadline(self):
        # นี่คือกับดักจริง: STOP_GRACE_INROOM (30) > DOCKER_TIMEOUT (25)
        self.assertGreater(bot.STOP_GRACE_INROOM, bot.DOCKER_TIMEOUT,
                           "ถ้าไม่จริงอีกต่อไป เทสต์ข้างล่างก็ไม่ได้พิสูจน์อะไร")
        seen = self._stop_call(bot.STOP_GRACE_INROOM)
        self.assertGreater(seen["timeout"], bot.STOP_GRACE_INROOM)

    def test_cleanup_stale_stops_through_the_helper(self):
        stopped = []
        ps = mock.Mock(returncode=0, stdout="maibot_job_abc_j1\n", stderr="")
        with mock.patch.object(bot.shutil, "which", lambda _: "docker"), \
             mock.patch.object(bot, "_run", lambda *a, **k: ps), \
             mock.patch.object(bot, "_stop", lambda d, n, *a: stopped.append(n)), \
             mock.patch.object(bot, "_prune_stages", lambda *a, **k: []):
            names = bot.cleanup_stale("w1")
        self.assertEqual(names, ["maibot_job_abc_j1"])
        self.assertEqual(stopped, ["maibot_job_abc_j1"])


class TestBuildFailuresSpeakThai(unittest.TestCase):

    def _build(self, outcome):
        def fake_run(cmd, timeout=None):
            if isinstance(outcome, Exception):
                raise outcome
            return mock.Mock(returncode=outcome)

        with mock.patch.object(bot, "_docker", lambda: "docker"), \
             mock.patch.object(bot, "_image_exists", lambda _: False), \
             mock.patch.object(bot, "source_hash", lambda: "h"), \
             mock.patch.object(bot.subprocess, "run", fake_run):
            bot.build_image()

    def test_a_failed_build_raises_a_thai_runtime_error(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._build(1)
        self.assertIn("build image ของบอทไม่สำเร็จ", str(ctx.exception))

    def test_a_hung_build_is_not_waited_on_forever(self):
        with self.assertRaises(RuntimeError) as ctx:
            self._build(subprocess.TimeoutExpired(["docker"], bot.BUILD_TIMEOUT))
        self.assertIn("นาที", str(ctx.exception))

    def test_a_successful_build_returns_quietly(self):
        self._build(0)


class FakeProc:
    """docker client ที่จบตามสคริปต์: รอ n ครั้งแรกแล้วค่อยจบ (หรือไม่จบเลย)."""

    def __init__(self, survives: int = 0):
        self.survives = survives
        self.waits: list[float | None] = []
        self.killed = False
        self.stdout = iter(())

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.survives > 0:
            self.survives -= 1
            raise subprocess.TimeoutExpired(["docker", "run"], timeout or 0)
        return 0

    def poll(self):
        return None if self.survives > 0 else 0

    def kill(self):
        self.killed = True


class TestWaitOrKill(unittest.TestCase):

    def setUp(self):
        self.cmds = []
        p = mock.patch.object(
            bot, "_run",
            lambda cmd, **k: (self.cmds.append(cmd), mock.Mock(returncode=0))[1])
        p.start()
        self.addCleanup(p.stop)

    def test_a_clean_exit_says_nothing_and_kills_nothing(self):
        proc = FakeProc(survives=0)
        self.assertEqual(bot._wait_or_kill(proc, "docker", "c1"), "")
        self.assertEqual(self.cmds, [], "จบเองอยู่แล้ว ไม่ต้องไปฆ่าอะไร")
        self.assertFalse(proc.killed)

    def test_a_stuck_container_is_killed_by_name_not_just_the_client(self):
        # ฆ่า proc เฉยๆ ไม่พอ — container ยังเขียน /out อยู่ ไฟล์เสียงจะขาดกลางตอนเราย้าย
        proc = FakeProc(survives=1)
        warn = bot._wait_or_kill(proc, "docker", "c1")
        self.assertEqual(self.cmds, [["docker", "kill", "c1"]])
        self.assertTrue(warn)
        self.assertFalse(proc.killed, "docker kill ได้ผลแล้ว ไม่ต้องฆ่า client ซ้ำ")

    def test_a_client_that_survives_even_docker_kill_is_killed_directly(self):
        proc = FakeProc(survives=99)
        self.assertTrue(bot._wait_or_kill(proc, "docker", "c1"))
        self.assertTrue(proc.killed)

    def test_it_never_raises_timeout_expired_to_the_caller(self):
        # หัวใจของบั๊ก: ข้อยกเว้นตัวนี้เคยหลุดออกไปข้ามท่อนย้ายไฟล์เสียง
        bot._wait_or_kill(FakeProc(survives=99), "docker", "c1")

    def test_the_first_wait_uses_the_exit_deadline(self):
        proc = FakeProc(survives=0)
        bot._wait_or_kill(proc, "docker", "c1")
        self.assertEqual(proc.waits, [bot.EXIT_TIMEOUT])


class TestTheRecordingSurvivesAStuckBot(unittest.TestCase):
    """จุดที่บั๊กทำร้ายจริง: เสียงอัดมาครบแล้ว แต่ container ไม่ยอมตาย."""

    def setUp(self):
        # .resolve() ไม่ใช่ของประดับ: บน Windows บางเครื่อง tempfile คืนชื่อย่อ 8.3
        # ("RUNNER~1") ส่วน join_and_record เรียก .resolve() กับปลายทางเสมอ
        # ไม่ทำให้ตรงกันตั้งแต่ต้น เทสต์จะล้มที่ CI ทั้งที่โค้ดถูก (เจอจริงใน windows-latest)
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-b39-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.stage = self.tmp / "stage"
        self.logs = self.tmp / "logs"
        self.logs.mkdir(parents=True)
        self.profile = self.tmp / "prof"
        self.profile.mkdir(parents=True)
        (self.profile / "cookies").write_text("x", encoding="utf-8")
        for p in (mock.patch.object(bot, "STAGE_DIR", self.stage),
                  mock.patch.object(bot, "DEBUG_DIR", self.logs),
                  mock.patch.object(bot, "PROFILE_DIR", self.profile),
                  mock.patch.object(bot, "_docker", lambda: "docker"),
                  mock.patch.object(bot, "build_image", lambda *a, **k: None),
                  mock.patch.object(bot, "_run",
                                    lambda *a, **k: mock.Mock(returncode=0, stdout="",
                                                              stderr="")),
                  mock.patch.object(bot.time, "sleep", lambda *_: None)):
            p.start()
            self.addCleanup(p.stop)

    def _record(self, survives: int) -> Path:
        out = self.tmp / "meeting.wav"
        proc = FakeProc(survives=survives)

        def fake_popen(cmd, **kw):
            # container เขียนไฟล์เสียงลง /out เสร็จแล้ว — เหมือนประชุมที่อัดจบจริง
            _, stage = bot._job_slot("job1", "w1")
            (stage / out.name).write_bytes(b"RIFF" + b"x" * 4096)
            return proc

        with mock.patch.object(bot.subprocess, "Popen", fake_popen), \
             mock.patch.object(bot.threading, "Thread", lambda **k: mock.Mock()):
            return bot.join_and_record("https://meet.example/abc", out,
                                       on_tick=lambda *_: True, job_id="job1", worker="w1")

    def test_the_audio_reaches_its_destination_even_when_the_bot_will_not_die(self):
        got = self._record(survives=99)
        self.assertEqual(got, self.tmp / "meeting.wav")
        self.assertTrue(got.exists())
        self.assertGreater(got.stat().st_size, 0, "เสียงต้องไม่ค้างอยู่ในโฟลเดอร์พัก")

    def test_the_staging_dir_is_cleaned_up_afterwards(self):
        self._record(survives=99)
        self.assertFalse(list(self.stage.glob("*")), "โฟลเดอร์พักต้องไม่ค้าง")

    def test_the_normal_path_still_works(self):
        got = self._record(survives=0)
        self.assertTrue(got.exists())


if __name__ == "__main__":
    unittest.main()
