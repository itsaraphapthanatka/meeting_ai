"""BACKLOG #76 — ระบาย body ที่ยังไม่ได้อ่านก่อนปิด ไม่ใช่เฉพาะเส้น 413.

เส้นที่ตอบก่อนอ่าน body (401/403/404/405/415/400 framing ผิด) ทิ้งไบต์ของ client ค้างไว้
ใน receive buffer ของเรา พอปิด socket TCP จะยิง RST แล้วฝั่ง client (อย่างน้อยบน Windows)
ทิ้งไบต์ที่รับมาแล้วแต่ยังไม่ได้อ่านไปพร้อมกัน — คำตอบที่เราเพิ่งส่งหายทั้งก้อน client
เห็นแค่ "connection reset" แทนที่จะได้อ่าน 415

เดิมมีแต่เส้น 413 ที่ระบาย (`_drain_rejected_body`) ทั้งที่กลไกเดียวกันเป๊ะ

**ขอบเขตของหลักฐาน — อ่านก่อนเชื่อ:**

วัดกับแบบจำลองที่ทำ `shutdown(SHUT_WR)` ก่อน `close()` เหมือน `socketserver` ของจริง
(`http.client` ส่ง header แล้วค่อยส่ง body เสมอ · body 29 ไบต์ · 240 ครั้ง):

    ไม่ระบาย     ได้ 415 กลับมา 230/240   ConnectionAbortedError 10 ครั้ง   8.3 ms/คำขอ
    ระบายก่อนปิด               240/240   ไม่มี error เลย                   7.3 ms/คำขอ

**แต่ยิงกับเซิร์ฟเวอร์จริง 200 ครั้ง × 3 รอบ ทั้งก่อนและหลังแก้ ได้ 415 ครบ 600/600 ทั้งคู่**
และรัน `test_bug_016_share_cookie_confirm` ซ้ำ 40 รอบขณะอัด CPU 10 โปรเซส → แดง 0/40

แปลว่า **ยังพิสูจน์ไม่ได้ว่าการแก้นี้ทำให้เทสต์ที่แดง 1/10 รอบตอนวัด #59 หายไป** —
อัตราของจริงต่ำกว่าที่ 600 คำขอจะจับได้ สิ่งที่พิสูจน์ได้คือ "เส้นนี้ทิ้ง body ไว้จริง"
กับ "การระบายปิดรูที่วัดได้ในแบบจำลองที่ซื่อตรงต่อของจริง โดยไม่เสียเวลาเพิ่ม"

ไฟล์นี้จึงตรวจ **การต่อสาย** (เรียกระบายด้วยจำนวนที่ถูกต้องไหม) ไม่ใช่ตรวจว่า RST หายไป
เพราะ RST เป็น race ที่สั่งให้เกิดไม่ได้
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import CloudCase, LocalCase  # noqa: E402

from meeting_ai.web import server  # noqa: E402

SRC = (Path(__file__).resolve().parents[1] / "meeting_ai" / "web"
       / "server.py").read_text(encoding="utf-8")


class _DrainSpy:
    """ดักว่า _drain_rejected_body ถูกเรียกด้วยจำนวนเท่าไร แล้วยังปล่อยให้ทำงานจริง."""

    def __init__(self) -> None:
        self.calls: list[int] = []
        self._real = server.Handler._drain_rejected_body

    def __enter__(self):
        spy = self

        def wrapper(handler, pending):
            spy.calls.append(pending)
            return spy._real(handler, pending)

        self._patch = mock.patch.object(server.Handler, "_drain_rejected_body", wrapper)
        self._patch.start()
        return self

    def __exit__(self, *a):
        self._patch.stop()
        return False

    @property
    def drained(self) -> list[int]:
        """เฉพาะครั้งที่มีอะไรให้ระบายจริง — ครั้งที่ 0 ไบต์ไม่นับว่า "ได้ระบาย"."""
        return [n for n in self.calls if n > 0]


class TestRejectedBodyIsDrained(CloudCase):
    """เส้น 415 ของ /api/auth/share ตอบก่อนอ่าน body — ของที่ค้างต้องถูกระบาย."""

    def test_the_unread_body_is_drained(self):
        payload = b'{"token": "abcdef0123456789"}'
        with _DrainSpy() as spy:
            status, _, _ = self.post_raw("/api/auth/share", payload,
                                         content_type="text/plain")
        self.assertEqual(status, 415)
        self.assertEqual(spy.drained, [len(payload)],
                         "ไม่ได้ระบาย body ที่ปฏิเสธไป — client อาจเจอ reset แทน 415")

    def test_a_request_without_a_body_drains_nothing(self):
        with _DrainSpy() as spy:
            self.get("/api/config")
        self.assertEqual(spy.drained, [], "ไม่มี body ให้ระบายแต่ไปเรียก")


class TestAlreadyReadBodiesAreNotDrainedTwice(LocalCase):
    """อ่าน body ไปแล้วต้องไม่ไประบายซ้ำ ไม่งั้นรอจนชนเพดานเวลาเปล่า ๆ ทุกคำขอ."""

    def test_a_successful_json_post_drains_nothing(self):
        with _DrainSpy() as spy:
            status, _, _ = self.post_json("/api/settings", {"lang": "th"})
        self.assertLess(status, 500, "เส้นนี้ต้องไม่ 500 ไม่งั้นเทสต์วัดผิดเรื่อง")
        self.assertEqual(spy.drained, [],
                         f"อ่าน body ครบแล้วยังไประบายอีก {spy.drained}")

    def test_a_rejected_json_body_is_still_drained_by_the_413_path(self):
        # เส้น 413 เคยระบายเองอยู่แล้ว ย้ายมาอยู่ใน _send ต้องไม่หายไป
        big = b"x" * (server.MAX_JSON_BODY + 512)
        with _DrainSpy() as spy:
            status, _, _ = self.post_raw("/api/settings", big)
        self.assertEqual(status, 413)
        self.assertEqual(spy.drained, [len(big)])


class TestTheWiring(unittest.TestCase):

    def test_the_413_path_no_longer_drains_by_hand(self):
        # เรียกสองครั้งเสี่ยงรอเพดานเวลาสองรอบ — _send ทำให้แล้ว
        block = SRC[SRC.index("except BodyTooLarge as e:"):]
        block = block[:block.index("except BadBody")]
        self.assertNotIn("self._drain_rejected_body(e.pending)", block)

    def test_send_drains_whatever_is_left_unread(self):
        block = SRC[SRC.index("    def _send(self"):]
        block = block[:block.index("    def _json(")]
        self.assertIn("self._drain_rejected_body(self.body_bytes - self.body_read)", block,
                      "ต้องระบายด้วย *ส่วนต่าง* ไม่ใช่ความยาวทั้งก้อน")
        # เคยมี if self.close_connection: คร่อมไว้ ซึ่งเป็นเงื่อนไขที่เป็นจริงเสมอเมื่อมีของ
        # ให้ระบาย (บรรทัดบนสุดของ _send ตั้ง close เองเมื่อ body_bytes ไม่เป็นศูนย์) —
        # กลับกลไกโดยถอดมันออกแล้วไม่มีเทสต์ไหนแดง จึงถอดทิ้งแทนที่จะเก็บสาขาที่ทดสอบไม่ได้
        guard = "if self.close_connection:" + chr(10) + "            self._drain"
        self.assertNotIn(guard, block)

    def test_every_body_read_path_updates_the_counter(self):
        # ส่วนต่าง body_bytes - body_read คือหัวใจ ถ้าเส้นไหนอ่านแล้วไม่นับ จะระบายซ้ำ
        self.assertEqual(SRC.count("self.body_read += len("), 2,
                         "มีเส้นอ่าน body ที่ไม่ได้นับ หรือเพิ่มมาเกิน — ตรวจ _body_json "
                         "กับ _read_body_to")


if __name__ == "__main__":
    unittest.main()
