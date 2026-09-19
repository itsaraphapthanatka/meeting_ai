"""BACKLOG #77 — traceback ของคอนเนกชันที่ client ทิ้งไปเอง ไม่ควรขึ้น log.

`socketserver.BaseServer.handle_error()` พิมพ์ traceback เต็มลง stderr ทุกครั้งที่เธรด
จัดการคำขอโยน exception รวมถึงกรณีที่ client ปิดคอนเนกชันไปเองระหว่างทาง ซึ่งไม่ใช่
ความผิดของเซิร์ฟเวอร์และไม่มีอะไรให้แก้

วัดจาก log ของ CI จริง: รัน `test (windows-latest)` ที่ **ผ่านทั้งรอบ** (run 35449170539)
มี `ConnectionAbortedError: [WinError 10053]` อยู่ **15 ครั้ง** — traceback ชี้ไปที่
`handle_one_request()` -> `self.rfile.readline(65537)` คือเซิร์ฟเวอร์รอคำขอถัดไปบน
คอนเนกชัน keep-alive แล้ว client หายไป

ราคาของเสียงรบกวนนี้ไม่ใช่แค่ log รก: ระหว่างไล่ #59/#75/#76 ผมอ่านมันปนกับชื่อเทสต์
แล้วสรุปว่า `test_bug_011_content_length_surrounding_whitespace_rejected` ล้มด้วย 10053
ซึ่งไม่จริงเลย แล้วสร้างสมมติฐาน วัด และเขียนตั๋วบนความเข้าใจผิดนั้นไปหนึ่งวัน

เงียบเฉพาะสามชนิดที่แปลว่า "อีกฝั่งหายไปเอง" — กลุ่มเดียวกับที่ `_route()` ดักไว้อยู่แล้ว
อย่างอื่นต้องยังพิมพ์ ไม่งั้นกลายเป็นการซ่อนบั๊กจริง
"""

from __future__ import annotations

import contextlib
import io
import re
import unittest
from pathlib import Path

from meeting_ai.web import server

SRC = (Path(__file__).resolve().parents[1] / "meeting_ai" / "web"
       / "server.py").read_text(encoding="utf-8")


class _Srv:
    """เปิดเซิร์ฟเวอร์จริงบนพอร์ตสุ่ม แค่เพื่อเรียก handle_error ของมัน (ไม่ serve)."""

    def __enter__(self):
        self.srv = server.Server(("127.0.0.1", 0), server.Handler)
        return self.srv

    def __exit__(self, *a):
        self.srv.server_close()
        return False


def _stderr_when(exc: BaseException) -> str:
    buf = io.StringIO()
    with _Srv() as srv, contextlib.redirect_stderr(buf):
        try:
            raise exc
        except BaseException:
            srv.handle_error(None, ("127.0.0.1", 12345))
    return buf.getvalue()


class TestClientsThatVanishAreQuiet(unittest.TestCase):

    def test_connection_aborted_prints_nothing(self):
        # นี่คือตัวที่เจอ 15 ครั้งในรัน CI ที่ผ่านทั้งรอบ
        out = _stderr_when(ConnectionAbortedError(10053, "aborted by host"))
        self.assertEqual(out, "", f"ยังพิมพ์อยู่: {out[:200]!r}")

    def test_connection_reset_prints_nothing(self):
        self.assertEqual(_stderr_when(ConnectionResetError(10054, "reset")), "")

    def test_broken_pipe_prints_nothing(self):
        self.assertEqual(_stderr_when(BrokenPipeError(32, "broken pipe")), "")


class TestRealErrorsStillShow(unittest.TestCase):
    """เงียบหมด = ซ่อนบั๊ก ต้องเหลือทางให้ของจริงโผล่."""

    def test_an_unexpected_error_still_prints_a_traceback(self):
        out = _stderr_when(ValueError("บั๊กจริงของเรา"))
        self.assertIn("ValueError", out)
        self.assertIn("บั๊กจริงของเรา", out)

    def test_a_timeout_still_prints(self):
        # timeout อาจแปลว่าเราเองช้า ไม่ใช่แค่ client หาย จึงไม่อยู่ในรายการเงียบ
        self.assertIn("TimeoutError", _stderr_when(TimeoutError("timed out")))

    def test_a_plain_oserror_still_prints(self):
        self.assertIn("OSError", _stderr_when(OSError("อย่างอื่น")))


class TestItMatchesTheGroupTheCodeAlreadyTrusts(unittest.TestCase):

    def test_the_quiet_list_is_the_same_group_route_already_swallows(self):
        # _route() ดักสามตัวนี้ไว้ว่า "เบราว์เซอร์ปิดไปกลางทาง ไม่ใช่ปัญหา" มาก่อนแล้ว
        m = re.search(r"except \(BrokenPipeError, ConnectionResetError, "
                      r"ConnectionAbortedError\)", SRC)
        self.assertIsNotNone(m, "กลุ่มที่ _route ดักไว้เปลี่ยนไป — ทบทวน QUIET_ERRORS ด้วย")
        self.assertEqual(
            set(server.Server.QUIET_ERRORS),
            {BrokenPipeError, ConnectionResetError, ConnectionAbortedError})

    def test_it_does_not_swallow_everything(self):
        self.assertNotIn(Exception, server.Server.QUIET_ERRORS)
        self.assertNotIn(OSError, server.Server.QUIET_ERRORS)


if __name__ == "__main__":
    unittest.main()
