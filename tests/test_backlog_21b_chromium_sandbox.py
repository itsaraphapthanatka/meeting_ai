"""BACKLOG #21b — เปิด sandbox ของ Chromium ในคอนเทนเนอร์บอทได้.

ตั๋วแยกออกมาจาก #21 เพราะการถอด `--no-sandbox` ออกเฉย ๆ ทำให้ Chromium **ไม่เปิดเลย**
ถ้า host ไม่มีของคู่กัน (`--cap-add=SYS_ADMIN` หรือ seccomp profile) และอาการที่ผู้ใช้เห็น
คือ "บอทไม่เข้าห้อง" ซึ่งแปลว่าประชุมครั้งนั้นหายไปทั้งครั้ง

เครื่องที่เขียนเทสต์นี้ไม่มี Docker daemon จึงยืนยันปลายทางไม่ได้ สิ่งที่ยืนยันได้และสำคัญ
ที่สุดคือ **สองฝั่งตรงกันเสมอ**: ถ้า host ไม่ได้เปิด ฝั่งคอนเทนเนอร์ต้องยังส่ง `--no-sandbox`
และถ้า host เปิด ฝั่งคอนเทนเนอร์ต้องไม่ส่ง — ความไม่ตรงกันคือทางเดียวที่ฟีเจอร์นี้จะพัง
แบบเงียบ ๆ

ค่าเริ่มต้นยังเป็น "ปิด" โดยตั้งใจ ดู `config.bot_sandbox` และแถว #21b ในแบ็กล็อก
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "bot"
JOIN_SRC = (BOT_DIR / "join_meeting.py").read_text(encoding="utf-8")
LOGIN_SRC = (BOT_DIR / "login.py").read_text(encoding="utf-8")

from meeting_ai import bot                                        # noqa: E402
from meeting_ai.config import config                              # noqa: E402

# bot/platforms.py เป็น stdlib ล้วน จึง import ตรง ๆ ได้ ไม่ต้องมี playwright
# (join_meeting.py กับ login.py import playwright ตั้งแต่บรรทัดบน จึงแตะได้แค่ตัวหนังสือ)
sys.path.insert(0, str(BOT_DIR))
platforms = importlib.import_module("platforms")


class TestTheContainerSideAsksTheHost(unittest.TestCase):
    """`bot/platforms.py` -> `sandbox_args()` คือจุดเดียวที่ตัดสินเรื่องนี้ในคอนเทนเนอร์."""

    def setUp(self) -> None:
        self.addCleanup(os.environ.pop, platforms.SANDBOX_ENV, None)
        os.environ.pop(platforms.SANDBOX_ENV, None)

    def test_without_the_env_it_still_disables_the_sandbox(self):
        # ค่าเริ่มต้นต้องเป็นพฤติกรรมเดิมเป๊ะ ไม่งั้นอัปเดตโค้ดแล้วบอทหยุดทำงานทันที
        self.assertEqual(platforms.sandbox_args(), ["--no-sandbox"])

    def test_with_the_env_set_it_keeps_the_sandbox(self):
        os.environ[platforms.SANDBOX_ENV] = "1"
        self.assertEqual(platforms.sandbox_args(), [])

    def test_any_other_value_means_off(self):
        # "true"/"yes" ไม่ถือว่าเปิด — ค่านี้ถูกตั้งโดย bot.py เท่านั้น ไม่ใช่โดยคน
        for value in ("0", "", "true", "yes", "on"):
            with self.subTest(value=value):
                os.environ[platforms.SANDBOX_ENV] = value
                self.assertEqual(platforms.sandbox_args(), ["--no-sandbox"])

    def test_neither_script_hardcodes_the_flag_any_more(self):
        for name, src in (("join_meeting.py", JOIN_SRC), ("login.py", LOGIN_SRC)):
            with self.subTest(script=name):
                self.assertNotIn('"--no-sandbox"', src,
                                 "ยังมี --no-sandbox ตายตัวอยู่ — ตั้ง env แล้วจะไม่มีผล")
                self.assertIn("sandbox_args()", src)


class TestTheHostSideFlags(unittest.TestCase):
    """`_sandbox_flags()` ต้องตั้งทั้งธงของ docker และ env ของคอนเทนเนอร์พร้อมกัน."""

    def test_off_by_default_adds_nothing(self):
        with mock.patch.object(config, "bot_sandbox", False), \
             mock.patch.object(config, "bot_seccomp", ""):
            self.assertEqual(bot._sandbox_flags(), [])

    def test_turning_it_on_adds_the_capability_and_the_env(self):
        with mock.patch.object(config, "bot_sandbox", True), \
             mock.patch.object(config, "bot_seccomp", ""):
            flags = bot._sandbox_flags()
        self.assertIn("--cap-add=SYS_ADMIN", flags)
        self.assertEqual(flags[flags.index("-e") + 1], "CHROMIUM_SANDBOX=1")

    def test_a_seccomp_profile_is_used_instead_of_the_capability(self):
        # seccomp แคบกว่า SYS_ADMIN มาก — ให้ทั้งสองอย่างพร้อมกันคือคืนสิทธิ์ที่เพิ่งตัดไป
        tmp = Path(tempfile.mkdtemp(prefix="mai-seccomp-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        profile = tmp / "chrome.json"
        profile.write_text("{}", encoding="utf-8")
        with mock.patch.object(config, "bot_sandbox", True), \
             mock.patch.object(config, "bot_seccomp", str(profile)):
            flags = bot._sandbox_flags()
        self.assertIn("--security-opt", flags)
        self.assertEqual(flags[flags.index("--security-opt") + 1], f"seccomp={profile}")
        self.assertNotIn("--cap-add=SYS_ADMIN", flags)
        self.assertEqual(flags[flags.index("-e") + 1], "CHROMIUM_SANDBOX=1")

    def test_a_missing_seccomp_file_fails_loudly_before_docker_runs(self):
        # ปล่อยผ่าน = docker ปฏิเสธคำสั่ง run ด้วยข้อความอังกฤษ แล้วงานล้มโดยไม่บอกสาเหตุจริง
        with mock.patch.object(config, "bot_sandbox", True), \
             mock.patch.object(config, "bot_seccomp", str(ROOT / "ไม่มีไฟล์นี้.json")):
            with self.assertRaises(RuntimeError) as e:
                bot._sandbox_flags()
        self.assertIn("MAI_BOT_SECCOMP", str(e.exception))


class _DockerRun(unittest.TestCase):
    """จับคำสั่ง `docker run` ที่ประกอบได้จริง โดยไม่ต้องมี Docker daemon."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-21b-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.profile = self.tmp / "prof"
        self.profile.mkdir(parents=True)
        (self.profile / "cookies").write_text("x", encoding="utf-8")
        self.cmds: list[list[str]] = []
        for target in (
            mock.patch.object(bot, "STAGE_DIR", self.tmp / "stage"),
            mock.patch.object(bot, "DEBUG_DIR", self.tmp / "logs"),
            mock.patch.object(bot, "PROFILE_DIR", self.profile),
            mock.patch.object(bot, "_docker", lambda: "docker"),
            mock.patch.object(bot, "build_image", lambda *a, **k: None),
            mock.patch.object(bot, "_rm", lambda *a: None),
            mock.patch.object(bot, "_stop", lambda *a, **k: None),
            mock.patch.object(bot, "_open_bot_screen", lambda: ""),
            mock.patch.object(bot.time, "sleep", lambda *_: None),
        ):
            target.start()
            self.addCleanup(target.stop)
        (self.tmp / "logs").mkdir(parents=True, exist_ok=True)

    def _login_cmd(self) -> list[str]:
        def fake_run(cmd, **kw):
            self.cmds.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(bot, "_run", fake_run), \
             mock.patch("builtins.input", lambda *_: ""):
            bot.login()
        return next(c for c in self.cmds if "run" in c)

    def _join_cmd(self) -> list[str]:
        seen: dict = {}

        class Proc:
            stdout = iter(())
            returncode = 0

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

            def kill(self):
                pass

        def fake_popen(cmd, **kw):
            seen["cmd"] = cmd
            _, stage = bot._job_slot("job1", "w1")
            (stage / "meeting.wav").write_bytes(b"RIFF0000WAVE")
            (stage / bot.STATUS_NAME).write_text("inroom", encoding="utf-8")
            return Proc()

        with mock.patch.object(bot, "_run",
                               lambda *a, **k: mock.Mock(returncode=0, stdout="", stderr="")), \
             mock.patch.object(bot.subprocess, "Popen", fake_popen):
            bot.join_and_record("https://meet.google.com/abc-defg-hij",
                                self.tmp / "meeting.wav", job_id="job1", worker="w1")
        return seen["cmd"]


class TestBothContainersGetTheSameDecision(_DockerRun):

    def test_login_container_off_by_default(self):
        with mock.patch.object(config, "bot_sandbox", False), \
             mock.patch.object(config, "bot_seccomp", ""):
            cmd = self._login_cmd()
        self.assertNotIn("--cap-add=SYS_ADMIN", cmd)
        self.assertNotIn("CHROMIUM_SANDBOX=1", cmd)

    def test_login_container_gets_the_flags_when_on(self):
        # หน้าล็อกอินคือหน้าที่ผู้ใช้กรอกรหัส Google ลงไป — ต้องไม่ถูกลืมไว้ข้างหลัง
        with mock.patch.object(config, "bot_sandbox", True), \
             mock.patch.object(config, "bot_seccomp", ""):
            cmd = self._login_cmd()
        self.assertIn("--cap-add=SYS_ADMIN", cmd)
        self.assertIn("CHROMIUM_SANDBOX=1", cmd)

    def test_join_container_off_by_default(self):
        with mock.patch.object(config, "bot_sandbox", False), \
             mock.patch.object(config, "bot_seccomp", ""):
            cmd = self._join_cmd()
        self.assertNotIn("--cap-add=SYS_ADMIN", cmd)
        self.assertNotIn("CHROMIUM_SANDBOX=1", cmd)

    def test_join_container_gets_the_flags_when_on(self):
        with mock.patch.object(config, "bot_sandbox", True), \
             mock.patch.object(config, "bot_seccomp", ""):
            cmd = self._join_cmd()
        self.assertIn("--cap-add=SYS_ADMIN", cmd)
        self.assertIn("CHROMIUM_SANDBOX=1", cmd)

    def test_the_flags_come_before_the_image_name(self):
        # docker อ่านทุกอย่างหลังชื่อ image เป็นคำสั่งของ container ไม่ใช่ตัวเลือกของ run
        with mock.patch.object(config, "bot_sandbox", True), \
             mock.patch.object(config, "bot_seccomp", ""):
            for cmd in (self._login_cmd(), self._join_cmd()):
                with self.subTest(cmd=cmd[:3]):
                    self.assertLess(cmd.index("--cap-add=SYS_ADMIN"), cmd.index(bot.IMAGE))


class TestTheDefaultIsDeliberate(unittest.TestCase):

    def test_the_shipped_default_is_off(self):
        """เปลี่ยนค่านี้โดยไม่ได้ส่งบอทเข้าห้องจริงก่อน = ทุกงานบอทล้มเงียบ ๆ.

        อ่านค่าในโพรเซสใหม่ ไม่ใช่ importlib.reload: config ถูก import แบบ
        `from .config import config` ไว้หลายที่ reload แล้วโมดูลอื่นจะยังถือวัตถุเก่า
        ซึ่งทำให้เทสต์ตัวถัดไปในสวีทเดียวกันเพี้ยนแบบหาสาเหตุยาก
        """
        env = {k: v for k, v in os.environ.items()
               if k not in ("MAI_BOT_SANDBOX", "MAI_BOT_SECCOMP")}
        env["PYTHONIOENCODING"] = "utf-8"
        r = subprocess.run(
            [sys.executable, "-c",
             "from meeting_ai.config import config;"
             "print(config.bot_sandbox, repr(config.bot_seccomp))"],
            cwd=str(ROOT), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "False ''")

    def test_a_runbook_tells_the_owner_how_to_verify(self):
        """กลไกที่เปิดไม่ได้ด้วยตัวเอง ต้องมีคนทำต่อ — ถ้าไม่มีเอกสารก็ไม่มีใครทำ."""
        runbook = ROOT / "docs" / "runbooks" / "enable-bot-sandbox.md"
        self.assertTrue(runbook.exists(), f"ไม่พบ {runbook}")
        text = runbook.read_text(encoding="utf-8")
        for needle in ("MAI_BOT_SANDBOX", "MAI_BOT_SECCOMP", "./mai bot"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        # ต้องบอกทางถอยด้วย ไม่ใช่บอกแค่วิธีเปิด
        self.assertIn("MAI_BOT_SANDBOX=0", text)
        row = next(ln for ln in (ROOT / "docs" / "product" / "BACKLOG.md")
                   .read_text(encoding="utf-8").split("\n") if ln.startswith("| 21b |"))
        self.assertIn("enable-bot-sandbox.md", row)

    def test_the_example_env_documents_both_ways(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("MAI_BOT_SANDBOX=0", text)
        self.assertIn("MAI_BOT_SECCOMP", text)


if __name__ == "__main__":
    unittest.main()
