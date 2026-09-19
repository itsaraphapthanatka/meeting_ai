"""BACKLOG #21 + #22 — container ของบอท และของที่ดาวน์โหลดมาจากอินเทอร์เน็ต.

**#21** บอทคือส่วนที่เสี่ยงที่สุดของระบบ: มันเปิดหน้าเว็บของคนอื่นด้วย Chromium โดยมี
session ที่ล็อกอินบัญชี Google ของเจ้าของ mount อยู่ที่ `/prof` ของเดิม —

* รันเป็น **root** ใน container: หลุดออกจาก Chromium มาได้ = เขียนได้ทุกที่ที่ mount เข้ามา
* `x11vnc -nopw`: จอที่กำลังล็อกอินบัญชี Google เปิดให้ **ใครก็ได้ที่ต่อพอร์ตนั้นได้** ดูและ
  คลิกแทน ถึงจะผูกไว้ที่ 127.0.0.1 ก็ยังหมายถึงทุกผู้ใช้/ทุกโพรเซสบนเครื่องนั้น ซึ่งเครื่อง
  worker เป็นเครื่องที่แชร์กัน
* passcode ของห้องประชุมส่งทาง `-e`: `docker inspect` แสดง env ให้ทุกคนในกลุ่ม docker เห็น
  และค้างอยู่กับ container ตลอดอายุ ไม่ใช่แค่ตอนสั่ง
* ไม่มี `HEALTHCHECK`: container ที่ Chromium ตายไปแล้วยังขึ้นว่า Up

**#22** โมเดล whisper/VAD ถูกโหลดจากอินเทอร์เน็ตโดยไม่ตรวจอะไรเลย และ `setup-ubuntu.sh`
`git clone` whisper.cpp แบบไม่ระบุเวอร์ชัน = build โค้ดของ master วันไหนก็ได้ แล้วเอาไป
รันกับเสียงประชุมจริง

ที่มาของ sha256 ในไฟล์ `models/SHA256SUMS` (ไม่ใช่ตัวเลขที่คิดเอง): ตรงกันสองทาง —
`sha256sum` ของไฟล์ที่เครื่องเจ้าของใช้อยู่จริง และฟิลด์ `lfs.sha256` ที่ Hugging Face
ประกาศผ่าน API ของ repo ต้นทาง

เครื่องนี้ไม่มี Docker daemon จึง build/รัน container จริงไม่ได้ — เทสต์ชุดนี้ตรวจ *คำสั่งที่
จะถูกสั่ง* และ *ตรรกะของสคริปต์เชลล์ตัวจริง* (ตัดฟังก์ชันออกมารันด้วย bash) ส่วนการยืนยัน
ปลายทางต้องให้เจ้าของรัน `./mai bot` หนึ่งรอบ
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import bot

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "bot" / "Dockerfile").read_text(encoding="utf-8")
ENTRYPOINT = (ROOT / "bot" / "entrypoint.sh").read_text(encoding="utf-8")
SETUP_UBUNTU = (ROOT / "setup-ubuntu.sh").read_text(encoding="utf-8")
SETUP_MAC = (ROOT / "setup.sh").read_text(encoding="utf-8")
SUMS = ROOT / "models" / "SHA256SUMS"
# ต้องใช้ path เต็ม: เรียก "bash" ลอย ๆ บนเครื่องนี้ได้คนละตัวกับที่ which ชี้
# และตัวนั้นมองไม่เห็นพาธแบบ C:/... (rc 127) — เจอจริงตอนเขียนเทสต์นี้
BASH = shutil.which("bash")
HAVE_BASH = BASH is not None


class TestTheContainerIsNotRoot(unittest.TestCase):

    def test_it_switches_to_a_non_root_user(self):
        self.assertRegex(DOCKERFILE, r"(?m)^USER pwuser\s*$")

    def test_the_user_line_comes_after_the_files_are_copied(self):
        # สลับลำดับแล้ว COPY/RUN ที่เหลือจะรันในนามผู้ใช้นั้นและสิทธิ์ไฟล์จะเพี้ยน
        self.assertLess(DOCKERFILE.index("COPY entrypoint.sh"), DOCKERFILE.index("USER pwuser"))

    def test_the_directories_it_writes_to_are_handed_over(self):
        for path in ("/app", "/profwork"):
            with self.subTest(path=path):
                self.assertIn(path, DOCKERFILE[:DOCKERFILE.index("USER pwuser")])

    def test_there_is_a_healthcheck(self):
        self.assertIn("HEALTHCHECK", DOCKERFILE)
        self.assertIn("Xvfb", DOCKERFILE[DOCKERFILE.index("HEALTHCHECK"):])


class TestTheLoginScreenNeedsAPassword(unittest.TestCase):

    def test_nopw_is_gone(self):
        # ต้องดูเฉพาะบรรทัดที่เป็นคำสั่งจริง — คอมเมนต์ที่อธิบายว่าทำไมถึงเอา -nopw ออก
        # ก็มีคำนั้นอยู่ และมันควรอยู่ต่อไป
        code = [ln for ln in ENTRYPOINT.split("\n") if not ln.lstrip().startswith("#")]
        self.assertNotIn("-nopw", "\n".join(code))

    def test_it_refuses_to_open_the_screen_without_one(self):
        # ถ้าปล่อยผ่านเมื่อไม่มีรหัส เท่ากับ -nopw กลับมาทางอ้อม
        block = ENTRYPOINT[ENTRYPOINT.index('MODE" = "login"'):]
        self.assertIn("VNC_PASSWORD", block)
        self.assertIn("exit 1", block[:block.index("x11vnc")])

    def _login_password(self) -> str:
        cmds = []
        tmp = Path(tempfile.mkdtemp(prefix="mai-login-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with mock.patch.object(bot, "_docker", lambda: "docker"), \
             mock.patch.object(bot, "build_image", lambda *a, **k: None), \
             mock.patch.object(bot, "_rm", lambda *a: None), \
             mock.patch.object(bot, "_stop", lambda *a: None), \
             mock.patch.object(bot, "_run",
                               lambda cmd, **k: cmds.append(cmd) or mock.Mock(returncode=0,
                                                                              stderr="")), \
             mock.patch.object(bot, "_open_bot_screen", lambda: ""), \
             mock.patch.object(bot.time, "sleep", lambda *_: None), \
             mock.patch("builtins.input", lambda *_: ""), \
             mock.patch.object(bot, "PROFILE_DIR", tmp / "prof"):
            bot.login()
        run = next(c for c in cmds if "run" in c)
        env = [run[i + 1] for i, a in enumerate(run) if a == "-e"]
        password = next(v.split("=", 1)[1] for v in env if v.startswith("VNC_PASSWORD="))
        return password

    def test_the_host_sends_a_random_password(self):
        self.assertGreaterEqual(len(self._login_password()), 8,
                                "รหัสสั้นเกินไปที่จะมีประโยชน์")

    def test_each_login_gets_a_different_password(self):
        # รหัสเดิมทุกครั้ง = ใครเคยเห็นครั้งหนึ่งก็ใช้ได้ตลอดไป
        self.assertNotEqual(self._login_password(), self._login_password())


class TestThePasscodeDoesNotSitInDockerInspect(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-pass-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.stage = self.tmp / "stage"
        self.profile = self.tmp / "prof"
        self.profile.mkdir(parents=True)
        # โครงจริงของโปรไฟล์ Chromium ที่ล็อกอินแล้ว — bot.profile_ready() ดูไฟล์นี้
        (self.profile / "Default").mkdir(parents=True, exist_ok=True)
        (self.profile / "Default" / "Cookies").write_bytes(b"SQLite format 3")
        self.seen: dict = {}
        for target in (mock.patch.object(bot, "STAGE_DIR", self.stage),
                       mock.patch.object(bot, "DEBUG_DIR", self.tmp / "logs"),
                       mock.patch.object(bot, "PROFILE_DIR", self.profile),
                       mock.patch.object(bot, "_docker", lambda: "docker"),
                       mock.patch.object(bot, "build_image", lambda *a, **k: None),
                       mock.patch.object(bot, "_run", lambda *a, **k: mock.Mock(returncode=0,
                                                                                stdout="",
                                                                                stderr="")),
                       mock.patch.object(bot.time, "sleep", lambda *_: None)):
            target.start()
            self.addCleanup(target.stop)
        (self.tmp / "logs").mkdir(parents=True, exist_ok=True)

    def _record(self, passcode: str):
        out = self.tmp / "meeting.wav"

        class Proc:
            stdout = iter(())

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

            def kill(self):
                pass

        def fake_popen(cmd, **kw):
            self.seen["cmd"] = cmd
            _, stage = bot._job_slot("job1", "w1")
            # จำลองว่า entrypoint อ่านไฟล์แล้วลบทิ้ง — ต้องอ่านค่าได้ก่อน
            self.seen["file"] = (stage / ".passcode")
            self.seen["content"] = (stage / ".passcode").read_text(encoding="utf-8") \
                if (stage / ".passcode").exists() else None
            (stage / out.name).write_bytes(b"RIFF" + b"x" * 512)
            return Proc()

        with mock.patch.object(bot.subprocess, "Popen", fake_popen), \
             mock.patch.object(bot.threading, "Thread", lambda **k: mock.Mock()):
            bot.join_and_record("https://meet.example/abc", out, on_tick=lambda *_: True,
                                job_id="job1", worker="w1", passcode=passcode)
        return self.seen

    def test_the_passcode_is_not_an_env_var(self):
        seen = self._record("123456")
        env = [seen["cmd"][i + 1] for i, a in enumerate(seen["cmd"]) if a == "-e"]
        self.assertFalse([v for v in env if v.startswith("PASSCODE=")],
                         f"passcode ยังอยู่ใน env: {env}")
        self.assertNotIn("123456", " ".join(seen["cmd"]),
                         "passcode ไม่ควรโผล่ที่ไหนในคำสั่ง docker เลย")

    def test_the_container_is_told_where_to_read_it(self):
        seen = self._record("123456")
        env = [seen["cmd"][i + 1] for i, a in enumerate(seen["cmd"]) if a == "-e"]
        self.assertIn("PASSCODE_FILE=/out/.passcode", env)

    def test_the_file_really_holds_the_passcode(self):
        seen = self._record("123456")
        self.assertEqual(seen["content"], "123456")

    def test_the_file_is_gone_afterwards(self):
        seen = self._record("123456")
        self.assertFalse(seen["file"].exists(), "ไฟล์ passcode ต้องไม่ค้างอยู่หลังงานจบ")

    def test_no_file_is_written_when_there_is_no_passcode(self):
        seen = self._record("")
        env = [seen["cmd"][i + 1] for i, a in enumerate(seen["cmd"]) if a == "-e"]
        self.assertFalse([v for v in env if v.startswith("PASSCODE_FILE=")])
        self.assertIsNone(seen["content"])


@unittest.skipUnless(HAVE_BASH, "ต้องมี bash เพื่อรันตรรกะของสคริปต์ตัวจริง")
class TestTheEntrypointGuards(unittest.TestCase):
    """ตัดโค้ดจากไฟล์จริงมารัน — เทสต์ที่ทดสอบสำเนาที่พิมพ์ซ้ำ ไม่ได้ทดสอบของที่ shipped."""

    def _run(self, script: str, cwd: Path) -> subprocess.CompletedProcess:
        # เหตุผลเดียวกับ TestVerifyLogicItself._verify: ไฟล์ ไม่ใช่ bash -c
        probe = cwd / "probe.sh"
        probe.write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run([BASH, probe.as_posix()], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=str(cwd))

    def test_it_stops_when_a_mounted_folder_is_not_writable(self):
        # อาการถ้าไม่ตรวจ: บอทเข้าห้อง นั่งจนจบ แล้วไม่มีไฟล์เสียง ซึ่งไล่ยากมาก
        tmp = Path(tempfile.mkdtemp(prefix="mai-mount-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        block = ENTRYPOINT[ENTRYPOINT.index("for d in /out /prof"):]
        block = block[:block.index("\ndone") + 5]
        # ชี้ไปโฟลเดอร์ปลอมที่เขียนไม่ได้แทน /out /prof
        good, bad = tmp / "good", tmp / "bad"
        good.mkdir()
        bad.mkdir()
        bad.chmod(0o500)
        self.addCleanup(bad.chmod, 0o700)
        probe = block.replace("for d in /out /prof", f'for d in "{bad.as_posix()}"')
        r = self._run(probe, tmp)
        if r.returncode == 0:
            self.skipTest("ระบบไฟล์นี้ไม่บังคับสิทธิ์ (Windows) — ตรวจตรรกะไม่ได้")
        self.assertIn("เขียน", r.stderr)

    def test_it_continues_when_the_folder_is_writable(self):
        tmp = Path(tempfile.mkdtemp(prefix="mai-mount-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        block = ENTRYPOINT[ENTRYPOINT.index("for d in /out /prof"):]
        block = block[:block.index("\ndone") + 5]
        probe = block.replace("for d in /out /prof", f'for d in "{tmp.as_posix()}"')
        r = self._run(probe, tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(list(tmp.glob(".mai-write-test")), [], "ไฟล์ทดสอบต้องถูกลบทิ้ง")


class TestDownloadsArePinnedAndVerified(unittest.TestCase):

    def test_whisper_cpp_is_pinned_to_a_tag_and_a_commit(self):
        # tag ถูกย้ายไปชี้ commit อื่นได้ การ clone ตาม tag เฉย ๆ จึงไม่ใช่การปักหมุด
        self.assertRegex(SETUP_UBUNTU, r'WHISPER_REF="\$\{WHISPER_REF:-v[\d.]+\}"')
        self.assertRegex(SETUP_UBUNTU, r'WHISPER_COMMIT="\$\{WHISPER_COMMIT:-[0-9a-f]{40}\}"')

    def test_the_clone_uses_the_pinned_ref(self):
        clone = SETUP_UBUNTU[SETUP_UBUNTU.index("git clone"):]
        self.assertIn('--branch "$WHISPER_REF"', clone[:200])

    def test_the_checked_out_commit_is_verified(self):
        self.assertIn("rev-parse HEAD", SETUP_UBUNTU)
        self.assertIn('"$HEAD_SHA" != "$WHISPER_COMMIT"', SETUP_UBUNTU)

    def test_the_manifest_exists_and_parses(self):
        rows = [ln.split() for ln in SUMS.read_text(encoding="utf-8").split("\n")
                if ln.strip() and not ln.startswith("#")]
        self.assertTrue(rows)
        for row in rows:
            with self.subTest(row=row):
                self.assertEqual(len(row), 2)
                self.assertRegex(row[0], r"^[0-9a-f]{64}$")

    def test_the_models_the_project_ships_with_are_listed(self):
        text = SUMS.read_text(encoding="utf-8")
        for name in ("ggml-large-v3-turbo-q5_0.bin", "ggml-silero-v5.1.2.bin"):
            with self.subTest(name=name):
                self.assertIn(name, text)

    def test_both_setup_scripts_verify_what_they_download(self):
        self.assertIn("SHA256SUMS", SETUP_UBUNTU)
        self.assertIn("sha256sum", SETUP_UBUNTU)
        # macOS ไม่มี sha256sum ติดมา ต้องใช้ shasum
        self.assertIn("SHA256SUMS", SETUP_MAC)
        self.assertIn("shasum -a 256", SETUP_MAC)

    def test_a_bad_download_is_deleted_not_left_behind(self):
        # ไม่ลบทิ้ง = รอบหน้าเจอไฟล์แล้วคิดว่า "มีอยู่แล้ว" ข้ามการโหลดใหม่ไปเลย
        for script in (SETUP_UBUNTU, SETUP_MAC):
            with self.subTest(script=script[:30]):
                block = script[script.index("ไม่ตรงลายนิ้วมือ"):]
                self.assertIn("rm -f", block[:400])

    def test_an_unknown_model_warns_instead_of_failing(self):
        # ผู้ใช้เลือกโมเดลอื่นได้ ไม่ควรบล็อกการติดตั้ง แต่ต้องบอกว่าไฟล์นั้นไม่ถูกตรวจ
        self.assertIn("ไม่ถูกตรวจ", SETUP_UBUNTU)


@unittest.skipUnless(HAVE_BASH, "ต้องมี bash เพื่อรันตรรกะของสคริปต์ตัวจริง")
class TestVerifyLogicItself(unittest.TestCase):
    """ตัด verify() จาก setup-ubuntu.sh ตัวจริงมารันกับไฟล์จริง."""

    def _verify(self, tmp: Path, target: Path, sums_line: str | None):
        body = re.search(r"^verify\(\) \{.*?^\}", SETUP_UBUNTU, re.M | re.S)
        assert body, "ตัด verify() จากไฟล์จริงไม่ได้"
        sums = tmp / "SHA256SUMS"
        if sums_line is not None:
            sums.write_text(sums_line, encoding="utf-8")
        script = f'SUMS="{sums.as_posix()}"\n{body.group(0)}\nverify "{target.as_posix()}"'
        # เขียนเป็นไฟล์แล้วรัน ไม่ใช่ `bash -c "<หลายบรรทัด>"` — บน Windows การส่งสตริงหลาย
        # บรรทัดเป็น argv เดียวทำให้เครื่องหมายคำพูดข้างในเพี้ยน จน $SUMS กลายเป็นค่าว่าง
        # (เจอจริงตอนเขียนเทสต์นี้ ไม่ใช่ข้อสันนิษฐาน)
        probe = tmp / "probe.sh"
        probe.write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run([BASH, probe.as_posix()], capture_output=True, text=True,
                              encoding="utf-8", errors="replace")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-verify-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.target = self.tmp / "ggml-probe.bin"
        self.target.write_bytes(b"hello")
        self.digest = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

    def test_a_matching_file_passes_and_survives(self):
        r = self._verify(self.tmp, self.target, f"{self.digest}  ggml-probe.bin\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ถูกต้อง", r.stdout)
        self.assertTrue(self.target.exists())

    def test_a_tampered_file_fails_and_is_removed(self):
        self.target.write_bytes(b"tampered")
        r = self._verify(self.tmp, self.target, f"{self.digest}  ggml-probe.bin\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ไม่ตรงลายนิ้วมือ", r.stdout)
        self.assertFalse(self.target.exists())

    def test_an_unlisted_file_warns_but_passes(self):
        r = self._verify(self.tmp, self.target, "1111  something-else.bin\n")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ไม่ถูกตรวจ", r.stdout)

    def test_a_missing_manifest_warns_but_passes(self):
        r = self._verify(self.tmp, self.target, None)
        self.assertEqual(r.returncode, 0)
        self.assertIn("ข้ามการตรวจ", r.stdout)


if __name__ == "__main__":
    unittest.main()
