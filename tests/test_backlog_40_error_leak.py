"""BACKLOG #40 — ข้อความ error ของงานเป็นของเจ้าของการประชุม ไม่ใช่ของคนดูแลเครื่อง.

`_fail_reason()` เดิมยัดลง `jobs.error`:

  * path เต็มของเครื่อง worker (`C:\\Users\\...\\recordings\\bot\\...`)
  * log ดิบ 12 บรรทัดสุดท้ายของ container ซึ่งมีลิงก์ห้องประชุม/ชื่อไฟล์ปนได้

ซึ่งเจ้าของการประชุม (และตั้งแต่ BACKLOG #37 คือเพื่อนร่วมทีมบนประชุมแบบ team ด้วย)
อ่านได้ผ่าน `/api/jobs/{id}` — เป็นข้อมูลที่เขาเอาไปทำอะไรไม่ได้ และไม่ควรเห็น

หลังแก้: ข้อความที่ขึ้นเว็บเหลือ "อาการ + สิ่งที่ทำต่อได้ + รหัสอ้างอิง" ส่วน log กับ path
ออกทาง stderr ของ worker พร้อมรหัสเดียวกัน คนดูแลเครื่องจึงยังไล่ต่อได้เหมือนเดิม

`public_error()` ใน worker.py เป็นด่านสุดท้าย: ข้อยกเว้นที่ไม่ได้เขียนเอง (FileNotFoundError,
PermissionError, ffmpeg, shutil) พา path ขึ้นเว็บได้เองโดยไม่มีใครตั้งใจ
"""

from __future__ import annotations

import ast
import io
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _harness import backend  # noqa: F401

from meeting_ai import bot, worker

# log ที่ container พ่นออกมาจริง — มีลิงก์ห้องและ path ปนอยู่
BOT_LOG = [
    "[bot] เปิดหน้า https://meet.google.com/abc-defg-hij?pwd=s3cret",
    "[bot] รอ host กดรับ...",
    "[bot] เขียนไฟล์ /out/20260918-120000-abcdef.wav",
    "[bot] ผิดพลาด: Timeout",
]


class FailReasonCase(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-b40-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.debug = self.tmp / "logs"
        self.debug.mkdir()
        self.stage = self.tmp / "stage" / "w_j1"
        self.stage.mkdir(parents=True)
        (self.stage / "bot_debug.png").write_bytes(b"shot")
        (self.stage / bot.STATUS_NAME).write_text("waiting", encoding="utf-8")
        p = mock.patch.object(bot, "DEBUG_DIR", self.debug)
        p.start()
        self.addCleanup(p.stop)
        self.err = io.StringIO()
        q = mock.patch.object(bot.sys, "stderr", self.err)
        q.start()
        self.addCleanup(q.stop)

    def reason(self) -> str:
        return bot._fail_reason(self.stage / "out.wav", BOT_LOG, "20260918-120000-abcdef")


class TestWhatTheOwnerSees(FailReasonCase):

    def test_the_container_log_is_not_in_the_message(self):
        msg = self.reason()
        for line in BOT_LOG:
            self.assertNotIn(line, msg)

    def test_the_meeting_link_is_not_in_the_message(self):
        self.assertNotIn("meet.google.com", self.reason())
        self.assertNotIn("s3cret", self.reason())

    def test_no_local_path_is_in_the_message(self):
        msg = self.reason()
        self.assertNotIn(str(self.tmp), msg)
        self.assertNotIn(str(self.debug), msg)
        self.assertNotIn(".png", msg)

    def test_the_actionable_part_survives(self):
        # ตัดของที่ไม่ควรเห็นออก ต้องไม่ตัดของที่ช่วยให้ผู้ใช้ทำอะไรต่อได้ทิ้งไปด้วย
        msg = self.reason()
        self.assertIn("บอทเข้าห้องไม่สำเร็จ", msg)
        self.assertIn("ไม่มีใครกด Admit", msg)          # hint จาก "รอ host กดรับ"
        self.assertIn("waiting", msg)                    # สถานะสุดท้ายที่บอทรายงาน

    def test_the_message_stays_short_enough_to_read(self):
        self.assertLess(len(self.reason()), 500)


class TestWhatTheOperatorStillGets(FailReasonCase):

    def test_the_log_tail_goes_to_the_worker_stderr(self):
        self.reason()
        out = self.err.getvalue()
        for line in BOT_LOG:
            self.assertIn(line, out)

    def test_the_screenshot_path_goes_to_the_worker_stderr(self):
        self.reason()
        self.assertIn(str(self.debug), self.err.getvalue())

    def test_the_same_reference_appears_on_both_sides(self):
        msg = self.reason()
        ref = re.search(r"รหัส ([0-9a-f]{6})", msg)
        self.assertIsNotNone(ref, f"ไม่มีรหัสอ้างอิงในข้อความ: {msg}")
        self.assertIn(ref.group(1), self.err.getvalue(),
                      "รหัสที่ให้ผู้ใช้ไปอ้าง ต้องอยู่ใน log ของ worker ด้วย ไม่งั้นอ้างไปก็หาไม่เจอ")

    def test_each_failure_gets_its_own_reference(self):
        a = re.search(r"รหัส ([0-9a-f]{6})", self.reason()).group(1)
        b = re.search(r"รหัส ([0-9a-f]{6})", self.reason()).group(1)
        self.assertNotEqual(a, b)


class TestPublicError(unittest.TestCase):
    """ด่านสุดท้ายสำหรับข้อยกเว้นที่เราไม่ได้เขียนข้อความเอง."""

    def test_a_windows_path_is_replaced(self):
        e = FileNotFoundError(2, "No such file",
                              "C:" + chr(92) + "Users" + chr(92) + "WIN11" + chr(92) + "a.wav")
        got = worker.public_error(e)
        self.assertNotIn("WIN11", got)
        self.assertNotIn("a.wav", got)
        self.assertIn("เครื่องประมวลผล", got)

    def test_a_posix_path_is_replaced(self):
        got = worker.public_error(RuntimeError("อ่าน /home/bot/mai/recordings/x.wav ไม่ได้"))
        self.assertNotIn("/home/bot", got)

    def test_a_url_is_left_alone(self):
        # "https://..." มี "s:" อยู่ข้างใน ตัวกรอง path ที่หยาบไปจะกินลิงก์ทั้งเส้น
        msg = "เรียก https://meet.example/abc ไม่สำเร็จ"
        self.assertEqual(worker.public_error(RuntimeError(msg)), msg)

    def test_ordinary_text_with_a_colon_is_left_alone(self):
        msg = "อัตราส่วน 16:9 ไม่รองรับ"
        self.assertEqual(worker.public_error(RuntimeError(msg)), msg)

    def test_a_thai_message_we_wrote_ourselves_passes_through(self):
        msg = "ไม่ได้ไฟล์เสียง — บอทเข้าห้องไม่สำเร็จ"
        self.assertEqual(worker.public_error(RuntimeError(msg)), msg)

    def test_a_giant_message_is_capped(self):
        got = worker.public_error(RuntimeError("x" * 5000))
        self.assertLessEqual(len(got), worker.MAX_ERROR_CHARS)

    def test_an_exception_with_no_message_still_says_something(self):
        self.assertEqual(worker.public_error(RuntimeError()), "RuntimeError")


class TestTheWorkerActuallyUsesIt(unittest.TestCase):
    """ตัวกรองที่ไม่ได้ถูกเรียกก็ไม่ได้กรองอะไร — ตรวจจุดเรียกจากซอร์สจริง."""

    def test_the_error_post_does_not_send_str_of_the_exception(self):
        src = Path(worker.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        posts = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "post_json"
                 and "/error" in ast.dump(n.args[0] if n.args else ast.Constant(""))]
        self.assertEqual(len(posts), 1, "หาจุดแจ้ง error ไม่เจอ — ตัวตรวจพัง ไม่ใช่โค้ดดี")
        sent = ast.dump(posts[0])
        self.assertIn("public_error", sent)
        self.assertNotIn("id='str'", sent)


if __name__ == "__main__":
    unittest.main()
