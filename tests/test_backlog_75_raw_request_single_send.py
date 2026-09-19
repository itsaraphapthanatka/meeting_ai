"""BACKLOG #75 — `raw_request` เสียคำตอบเมื่อเซิร์ฟเวอร์ปฏิเสธตั้งแต่ header แล้วปิดทันที.

พบระหว่างวัดอัตราแดงสุ่มของ #59: รันสวีทเต็ม 10 รอบ เขียว 9 แดง 1 (รอบที่ 6)

`raw_request` เดิมส่งสอง `sendall` — header ก่อน แล้วค่อย body ระหว่างสองก้อนนั้น
เซิร์ฟเวอร์อาจอ่าน header จบ ตัดสินว่าผิด (Content-Length ผิดรูป -> 400) ตอบกลับ แล้ว
`close()` **ทั้งที่ body ยังค้างใน receive buffer ของมัน** — TCP ไม่ส่ง FIN แต่ส่ง **RST**
และ Windows ทิ้งไบต์ที่ฝั่งเรารับมาแล้วแต่ยังไม่ได้อ่านไปพร้อมกับ RST นั้น คำตอบ 400
ที่เดินทางมาถึงแล้วจึงหายทั้งก้อน เทสต์แดงทั้งที่เซิร์ฟเวอร์ทำถูกทุกอย่าง

วัดจริงกับ body 10 ไบต์ อย่างละ 40 ครั้ง (`scratchpad/probe2.py`):

    แยกส่ง ไม่หน่วง        ได้ 400 กลับมา 28/40   recv พัง ConnectionAborted/Reset
    แยกส่ง คั่น 30ms         "            0/40   recv พัง ConnectionAborted 40 ครั้ง
    แยกส่ง เซิร์ฟตอบช้า      "           37/40
    ส่งก้อนเดียว             "           40/40   ไม่มี error เลย
    ส่งก้อนเดียว เซิร์ฟช้า    "           40/40   ไม่มี error เลย

**ข้อสังเกตที่กลับสมมติฐานเดิม**: `sendall(body)` ของ 10 ไบต์ **ไม่เคยล้ม** — มันลงบัฟเฟอร์
ในเครื่องได้เสมอ ความพังไปโผล่ที่ `recv` ต่างหาก ทางแก้จึงไม่ใช่ "อ่านคำตอบก่อนตัดสินว่า
ส่งล้ม" (สาขานั้นแทบไม่เคยถูกเดินเลย) แต่คือ **ไม่เปิดช่องว่างนั้นตั้งแต่แรก**

สิ่งที่ไฟล์นี้พิสูจน์ได้จริง: header กับ body ถึงเซิร์ฟเวอร์ใน `recv()` ครั้งเดียว
สิ่งที่พิสูจน์ไม่ได้ที่นี่: การแข่งกันของ RST เอง (มันคือ race — ทำให้เกิดแน่นอนไม่ได้)
ตัวเลขข้างบนคือหลักฐานส่วนนั้น และมาจากการวัดซ้ำ ไม่ใช่การอ้าง
"""

from __future__ import annotations

import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import _HttpCaseMixin  # noqa: E402

HARNESS_SRC = (Path(__file__).resolve().parent / "_harness.py").read_text(encoding="utf-8")

RESP = (b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n"
        b"Content-Length: 27\r\nConnection: close\r\n\r\n"
        b'{"error":"bad content-len"}')


class _RejectingServer:
    """เซิร์ฟเวอร์ปลอมที่ปฏิเสธตั้งแต่ header แล้วปิด — เลียนอาการที่ทำให้เกิด RST."""

    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.first_recv = b""          # ไบต์ก้อนแรกที่ OS ยกมาให้ = พิสูจน์ว่ามากี่ก้อน
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        try:
            conn.settimeout(5)
            self.first_recv = conn.recv(65536)
            conn.sendall(RESP)
            conn.close()               # ปิดทั้งที่ body อาจยังค้าง
        except OSError:
            pass

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass
        self.thread.join(timeout=3)


class _Caller(_HttpCaseMixin):
    """ยืม raw_request ตัวจริงมาใช้ โดยไม่ต้องบูตเซิร์ฟเวอร์ของแอป.

    **ห้ามสืบทอด TestCase**: unittest เก็บคลาสลูกของ TestCase ทุกตัวในโมดูล ไม่ว่าจะ
    ชื่อขึ้นต้นด้วย _ หรือไม่ — จะได้เทสต์ผีเพิ่มมาหนึ่งตัวที่ไม่ได้ทดสอบอะไรเลย
    """


class TestHeaderAndBodyArriveTogether(unittest.TestCase):

    def _call(self, **kw):
        srv = _RejectingServer()
        caller = _Caller()
        caller.port = srv.port
        try:
            status, text, _ = caller.raw_request(
                "POST", "/api/settings",
                {"Content-Type": "application/json", "Content-Length": " 10 ",
                 "Connection": "close"},
                b'{"a":"bc"}', **kw)
        finally:
            srv.close()
        return status, text, srv.first_recv

    def test_the_body_rides_in_the_same_recv_as_the_headers(self):
        _, _, first = self._call()
        self.assertIn(b"\r\n\r\n", first, "ไม่ได้รับ header ครบในก้อนแรก")
        self.assertTrue(first.endswith(b'{"a":"bc"}'),
                        "body ไม่ได้มากับก้อนเดียวกัน — ช่องว่างที่ทำให้เกิด RST ยังอยู่ "
                        f"(ก้อนแรกได้ {len(first)} ไบต์: {first[-40:]!r})")

    def test_the_rejection_still_comes_back(self):
        status, text, _ = self._call()
        self.assertEqual(status, 400, text[:200])

    def test_send_body_false_still_sends_headers_only(self):
        # เทสต์หลายตัวประกาศ Content-Length แล้วจงใจไม่ส่ง body — ต้องไม่พังเพราะการรวมก้อน
        _, _, first = self._call(send_body=False)
        self.assertTrue(first.endswith(b"\r\n\r\n"),
                        f"ส่ง body ไปทั้งที่สั่ง send_body=False: {first[-40:]!r}")


class TestTheHarnessKeepsWhatItAlreadyRead(unittest.TestCase):
    """ถ้า recv พังหลังได้ไบต์มาแล้ว ต้องใช้ของที่ได้ ไม่ใช่ทิ้งทั้งก้อนแล้วรายงาน -2.

    สาขานี้เป็น race ทำให้เกิดตามสั่งไม่ได้ จึงตรวจที่รูปโค้ดแทน และบอกไว้ตรงนี้ว่า
    นี่คือข้อจำกัดของเทสต์ ไม่ใช่การพิสูจน์พฤติกรรมจริง
    """

    def test_partial_data_is_not_discarded_on_a_recv_error(self):
        block = HARNESS_SRC[HARNESS_SRC.index("def raw_request"):]
        block = block[:block.index("def raw_send_and_collect")]
        i = block.index('return -2, f"recv failed:')
        self.assertIn("if not chunks:", block[max(0, i - 200):i],
                      "คืน -2 ทันทีโดยไม่ดูว่าอ่านมาได้แล้วหรือยัง")

    def test_there_is_exactly_one_sendall_for_the_request(self):
        block = HARNESS_SRC[HARNESS_SRC.index("def raw_request"):]
        block = block[:block.index("def raw_send_and_collect")]
        self.assertEqual(block.count("s.sendall("), 1,
                         "แยกส่งเป็นสองก้อนอีกแล้ว — ดู docstring ของ raw_request")


if __name__ == "__main__":
    unittest.main()
