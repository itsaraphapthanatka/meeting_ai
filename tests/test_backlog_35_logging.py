"""BACKLOG #35 — ไลบรารีเลิก print() ไปใช้ logging (ข้อความแบบอิโมจิยังอยู่ที่ CLI).

เหตุผลที่ไม่ใช่แค่ "ทำให้เป็นมาตรฐาน":

1. `print()` ไปที่เดียวเสมอ ปิดไม่ได้ กรองไม่ได้ ใส่เวลาให้ไม่ได้ เครื่อง worker ที่รันเป็น
   service เขียนทุกอย่างลง `logs/worker.log` เป็นสายข้อความไม่มีเวลา พอมีปัญหาว่า "บอทหลุด
   ตอนไหน" ก็ตอบไม่ได้
2. **`print()` เคยทำให้งานพังจริง** — คอนโซล cp874 เขียนภาษาไทยไม่ได้ `UnicodeEncodeError`
   จากบรรทัดเตือนเคยกลายเป็นงานล้ม/500 ทั้งคำขอ (BUG-045) จนต้องมี `_notice()` สองตัวที่
   ลองเขียนไทยก่อนแล้วค่อยถอยไป ascii ส่วน `logging` จับข้อยกเว้นของ handler ไว้เอง

**เส้นที่ห้ามหลุด**: เจ้าของอ่าน `logs/worker.log` อยู่ทุกวัน ข้อความและปลายทาง (stdout/stderr)
ต้องเหมือนเดิมเป๊ะ ๆ การย้ายบ้านครั้งนี้จึงตั้งรูปแบบเป็น "ข้อความล้วน" ไม่มีคำนำหน้า
"""

from __future__ import annotations

import ast
import io
import logging
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import log

PKG = Path(log.__file__).parent
# ตั๋วบอกให้เก็บข้อความแบบอิโมจิไว้ที่ CLI — สองไฟล์นี้คือ "ของที่ผู้ใช้อ่าน" ไม่ใช่ log ของระบบ
CLI_FILES = {"cli.py", "pipeline.py"}


def prints_in(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "print"]


class TestLibraryModulesDoNotPrint(unittest.TestCase):

    def test_no_print_left_in_library_code(self):
        for path in sorted(PKG.rglob("*.py")):
            if path.name in CLI_FILES:
                continue
            with self.subTest(module=path.relative_to(PKG).as_posix()):
                self.assertEqual(prints_in(path), [], "ยังมี print() เหลืออยู่")

    def test_the_cli_keeps_its_own_output(self):
        # ถ้าวันหนึ่งมีคนแปลง cli.py ตามไปด้วย ข้อความที่ผู้ใช้เห็นจะเปลี่ยนรูปโดยไม่ตั้งใจ
        total = sum(len(prints_in(PKG / name)) for name in CLI_FILES)
        self.assertGreater(total, 10, "cli.py/pipeline.py ควรยังพิมพ์ข้อความของตัวเอง")

    def test_every_converted_module_has_its_own_logger(self):
        for path in sorted(PKG.rglob("*.py")):
            if path.name in CLI_FILES or path.name == "log.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "log." not in src:
                continue
            with self.subTest(module=path.name):
                self.assertIn("_log.get(__name__)", src,
                              "ใช้ logger ของโมดูลตัวเอง ไม่ใช่ root")


class TestOutputGoesWhereItUsedTo(unittest.TestCase):
    """INFO -> stdout, WARNING ขึ้นไป -> stderr เหมือนที่ print(file=sys.stderr) เคยทำ."""

    def setUp(self) -> None:
        self.out, self.err = io.StringIO(), io.StringIO()
        for target in (mock.patch("sys.stdout", self.out), mock.patch("sys.stderr", self.err)):
            target.start()
            self.addCleanup(target.stop)
        log.reset()
        log.setup(force=True)
        self.addCleanup(log.reset)

    def test_info_goes_to_stdout_only(self):
        log.get("meeting_ai.probe").info("เริ่มงานแล้ว")
        self.assertEqual(self.out.getvalue().strip(), "เริ่มงานแล้ว")
        self.assertEqual(self.err.getvalue(), "")

    def test_warning_goes_to_stderr_only(self):
        log.get("meeting_ai.probe").warning("⚠️  เตือน")
        self.assertEqual(self.err.getvalue().strip(), "⚠️  เตือน")
        self.assertEqual(self.out.getvalue(), "")

    def test_the_message_has_no_prefix_added(self):
        # เจ้าของอ่าน logs/worker.log อยู่ — เติม "INFO:meeting_ai.worker:" เข้าไปคือเปลี่ยนรูป
        log.get("meeting_ai.probe").info("🛠️  worker พร้อม")
        self.assertEqual(self.out.getvalue(), "🛠️  worker พร้อม\n")

    def test_a_stamped_setup_is_available_when_wanted(self):
        log.setup(stamp=True, force=True)
        log.get("meeting_ai.probe").info("มีเวลานำหน้า")
        self.assertIn("meeting_ai.probe", self.out.getvalue())
        self.assertIn("INFO", self.out.getvalue())

    def test_the_level_can_be_raised_to_silence_info(self):
        log.setup(level=logging.WARNING, force=True)
        probe = log.get("meeting_ai.probe")
        probe.info("ไม่ควรเห็น")
        probe.warning("ควรเห็น")
        self.assertEqual(self.out.getvalue(), "")
        self.assertIn("ควรเห็น", self.err.getvalue())

    def test_it_does_not_leak_into_the_root_logger(self):
        # โปรแกรมที่ import เราแล้วตั้ง basicConfig ไว้ ต้องไม่ได้ข้อความซ้ำสองรอบ
        self.assertFalse(logging.getLogger(log.ROOT).propagate)


class TestALogLineCanNeverKillTheJob(unittest.TestCase):
    """เหตุผลข้อสองของตั๋ว — และเป็นเหตุผลที่ _notice() สองตัวเคยต้องมีทางสำรอง ascii."""

    def setUp(self) -> None:
        log.reset()
        self.addCleanup(log.reset)

    def test_a_handler_that_explodes_does_not_reach_the_caller(self):
        class Exploding(io.StringIO):
            def write(self, _):
                raise UnicodeEncodeError("cp874", "ก", 0, 1, "ไม่รองรับ")

        with mock.patch("sys.stdout", Exploding()), mock.patch("sys.stderr", io.StringIO()):
            log.setup(force=True)
            log.get("meeting_ai.probe").info("ภาษาไทยบนคอนโซล cp874")   # ต้องไม่โยนออกมา

    def test_the_dead_ascii_fallbacks_are_gone(self):
        # ทางสำรองสองรอบไม่มีทางทำงานอีกแล้ว ปล่อยไว้คือโค้ดที่หลอกคนอ่าน
        for name in ("stt.py", "web/blobstore.py"):
            src = (PKG / name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                self.assertNotIn('encode("ascii", "replace")', src)

    def test_notices_are_still_deduplicated(self):
        from meeting_ai import stt

        stt.reset_notices()
        self.addCleanup(stt.reset_notices)
        err = io.StringIO()
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", err):
            log.setup(force=True)
            stt._notice("ℹ️  ข้อความเดียวกัน")
            stt._notice("ℹ️  ข้อความเดียวกัน")
        self.assertEqual(err.getvalue().count("ข้อความเดียวกัน"), 1)


class TestOnlyTheEdgeConfiguresLogging(unittest.TestCase):
    """ไลบรารีที่เรียก basicConfig เองจะไปทับค่าของโปรแกรมที่ import มัน."""

    def test_no_library_module_calls_basic_config(self):
        for path in sorted(PKG.rglob("*.py")):
            if path.name == "log.py":
                continue
            with self.subTest(module=path.name):
                self.assertNotIn("basicConfig", path.read_text(encoding="utf-8"))

    def test_the_cli_sets_it_up(self):
        src = (PKG / "cli.py").read_text(encoding="utf-8")
        main = src[src.index("def main("):]
        self.assertIn("log.setup()", main)

    def test_setup_is_idempotent(self):
        log.reset()
        self.addCleanup(log.reset)
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            log.setup()
            log.setup()
            log.setup()
        self.assertEqual(len(logging.getLogger(log.ROOT).handlers), 2)


if __name__ == "__main__":
    unittest.main()
