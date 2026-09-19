"""BUG-068 — `mai bot-login` พังสนิทตั้งแต่ BACKLOG #21.

เจ้าของรัน `./mai bot-login` แล้วหน้าจอค้างรอที่ "กด Enter เพื่อบันทึก" ทั้งที่คอนเทนเนอร์
ตายไปแล้วตั้งแต่วินาทีแรก (2026-09-19):

    [entrypoint] ❌ เขียน /out ไม่ได้ (รันในนามผู้ใช้ 1000:1000)

`bot.login()` mount มาให้แค่ `/prof` — โหมดล็อกอินไม่ได้อัดเสียง จึงไม่ต้องใช้ `/out` เลย
แต่ `/out` **มีอยู่จริงในอิมเมจ** เพราะบรรทัด `VOLUME ["/out"]` ใน Dockerfile และเป็นของ
root พอ #21 เปลี่ยนไปรันในนาม `pwuser` การตรวจสิทธิ์เขียน (ที่ #21 เพิ่มเข้ามาเอง) จึงล้ม
ทุกครั้ง แล้ว entrypoint ออกด้วย exit 1

ผลต่อเนื่องที่กินเวลาไล่ทั้งวัน: ล็อกอินไม่เคยสำเร็จ -> `bot/profile` ว่างเปล่า ->
บอทเข้าห้องแบบไม่ระบุตัวตน -> ห้องที่ไม่รับ guest ปฏิเสธทันที -> host ไม่เคยเห็นการเคาะ
ประตู -> อาการที่ผู้ใช้เห็นคือ "ส่งบอทเข้าห้องแล้วไม่มีอะไรเกิดขึ้น"

เทสต์นี้ตัดบล็อกตรวจสิทธิ์ออกมาจาก `bot/entrypoint.sh` ตัวจริงแล้วรันด้วย bash
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = (ROOT / "bot" / "entrypoint.sh").read_text(encoding="utf-8")
DOCKERFILE = (ROOT / "bot" / "Dockerfile").read_text(encoding="utf-8")
BASH = shutil.which("bash")
# chmod 000 ไม่มีผลกับ root และบน Windows — ข้ามไปแทนที่จะผ่านแบบกลวง
CAN_BLOCK_WRITES = os.name != "nt" and os.geteuid() != 0 if hasattr(os, "geteuid") else False


@unittest.skipUnless(BASH, "ต้องมี bash เพื่อรันตรรกะของ entrypoint ตัวจริง")
class TestThePreflightSkipsOutInLoginMode(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        start = ENTRYPOINT.index('if [ "$MODE" = "login" ]; then\n    NEED_WRITE=')
        end = ENTRYPOINT.index("\ndone", start) + len("\ndone")
        cls.block = ENTRYPOINT[start:end]

    def _run(self, mode: str, out_writable: bool, prof_writable: bool = True) -> int:
        tmp = Path(tempfile.mkdtemp(prefix="mai-bug068-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out, prof = tmp / "out", tmp / "prof"
        out.mkdir()
        prof.mkdir()
        if not out_writable:
            out.chmod(0o500)
            self.addCleanup(out.chmod, 0o700)
        if not prof_writable:
            prof.chmod(0o500)
            self.addCleanup(prof.chmod, 0o700)
        script = self.block.replace('NEED_WRITE="/prof"', f'NEED_WRITE="{prof.as_posix()}"')
        script = script.replace('NEED_WRITE="/out /prof"',
                                f'NEED_WRITE="{out.as_posix()} {prof.as_posix()}"')
        probe = tmp / "probe.sh"
        probe.write_text(f'MODE={mode}\n{script}\n', encoding="utf-8", newline="\n")
        return subprocess.run([BASH, probe.as_posix()], capture_output=True,
                              text=True, encoding="utf-8", errors="replace").returncode

    @unittest.skipUnless(CAN_BLOCK_WRITES, "ต้องเป็น POSIX และไม่ใช่ root ถึงจะทำให้เขียนไม่ได้")
    def test_login_mode_survives_an_unwritable_out(self):
        """อาการที่เจอจริง — โหมดล็อกอินไม่ได้ mount /out มาด้วยซ้ำ."""
        self.assertEqual(self._run("login", out_writable=False), 0,
                         "โหมดล็อกอินยังตายเพราะ /out เขียนไม่ได้")

    @unittest.skipUnless(CAN_BLOCK_WRITES, "ต้องเป็น POSIX และไม่ใช่ root ถึงจะทำให้เขียนไม่ได้")
    def test_recording_mode_still_refuses_an_unwritable_out(self):
        # การ์ดของ #21 ต้องไม่หายไป ไม่งั้นกลับไปเป็น "อัดจนจบแล้วไม่มีไฟล์เสียง"
        self.assertEqual(self._run("", out_writable=False), 1)

    @unittest.skipUnless(CAN_BLOCK_WRITES, "ต้องเป็น POSIX และไม่ใช่ root ถึงจะทำให้เขียนไม่ได้")
    def test_login_mode_still_refuses_an_unwritable_prof(self):
        # /prof คือที่เก็บ session ที่กำลังจะล็อกอิน เขียนไม่ได้ = ล็อกอินไปก็สูญเปล่า
        self.assertEqual(self._run("login", out_writable=True, prof_writable=False), 1)

    def test_both_modes_pass_when_everything_is_writable(self):
        for mode in ("login", ""):
            with self.subTest(mode=mode or "(อัดเสียง)"):
                self.assertEqual(self._run(mode, out_writable=True), 0)


class TestTheShapeOfTheGuard(unittest.TestCase):

    def test_login_mode_only_needs_prof(self):
        self.assertIn('if [ "$MODE" = "login" ]; then\n    NEED_WRITE="/prof"', ENTRYPOINT)

    def test_recording_mode_needs_both(self):
        self.assertIn('NEED_WRITE="/out /prof"', ENTRYPOINT)

    def test_nothing_checks_out_unconditionally_any_more(self):
        self.assertNotIn("for d in /out /prof; do", ENTRYPOINT)

    def test_the_image_hands_out_to_the_non_root_user(self):
        # กันชั้นสอง: /out ที่มาจาก VOLUME เป็นของ root ถ้าไม่ยกให้ pwuser
        self.assertRegex(DOCKERFILE, r"chown -R pwuser:pwuser [^\n]*/out")


if __name__ == "__main__":
    unittest.main()
