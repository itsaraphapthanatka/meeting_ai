"""BACKLOG #46 — Range ที่เลยท้ายไฟล์ต้องตอบ 416 ไม่ใช่ 200 + ไฟล์ทั้งก้อน.

`_parse_range()` ยุบสองกรณีที่ RFC 9110 แยกไว้เข้าด้วยกัน แล้วคืน `None` ทั้งคู่:

  * "ไม่มี header / อ่านไม่ออก"  -> ต้องเมิน Range แล้วเสิร์ฟทั้งไฟล์ 200  (ถูกอยู่แล้ว)
  * "อ่านออกแต่สนองไม่ได้"       -> ต้องตอบ 416 + `Content-Range: bytes */<size>`

ผลของการยุบ: ผู้เล่นที่เลื่อนไปท้ายคลิป (หรือขอ byte ถัดจากไบต์สุดท้ายเพื่อถามความยาว)
ได้ไฟล์เสียงทั้งก้อนกลับไป ประชุมสองชั่วโมงคือหลายร้อยเมกะไบต์ที่ไหลผ่าน serverless
function โดยไม่มีใครต้องการ และผู้เล่นก็ยังไม่รู้อยู่ดีว่าขอเกินไปแล้ว

เจอระหว่างทางอีกอัน: `bytes=5-2` (ปลายน้อยกว่าต้น) ผ่าน regex เข้าไปได้ แล้ว
`length = end - start + 1` ติดลบ -> เซิร์ฟเวอร์ส่ง `Content-Length: -2` ออกสาย
"""

from __future__ import annotations

from _harness import LocalCase, new_mid
from meeting_ai.web import store

AUDIO = b"RIFF" + bytes(range(256)) * 4      # 1028 ไบต์ เนื้อในเดาได้ ใช้ตรวจว่าตัดช่วงถูกจุด


class RangeCase(LocalCase):

    def setUp(self) -> None:
        super().setUp()
        self.mid = new_mid()
        name = f"{self.mid}.wav"
        (store.WEB_DIR / name).write_bytes(AUDIO)
        store.create(self.mid, "ประชุมทดสอบ", name, "upload", "th", 60.0, [], "")
        self.url = f"/api/meetings/{self.mid}/audio"
        self.size = len(AUDIO)

    def head(self, rng: str | None = None):
        headers = {"Range": rng} if rng else {}
        return self._do("HEAD", self.url, extra_headers=headers)

    def fetch(self, rng: str | None = None):
        return self.get(self.url, extra_headers={"Range": rng} if rng else None)


class TestUnsatisfiableRange(RangeCase):

    def test_a_start_past_the_end_is_416(self):
        status, body, hdrs = self.fetch(f"bytes={self.size}-")
        self.assertEqual(status, 416)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes */{self.size}")
        self.assertFalse(body, "416 ต้องไม่แนบไฟล์มาด้วย")

    def test_a_range_far_past_the_end_is_416(self):
        status, _, hdrs = self.fetch("bytes=999999-1000000")
        self.assertEqual(status, 416)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes */{self.size}")

    def test_a_zero_length_suffix_is_416(self):
        # bytes=-0 คือ "0 ไบต์ท้ายไฟล์" ซึ่งสนองไม่ได้ เดิมได้ช่วงกลับหัว size..size-1
        status, _, hdrs = self.fetch("bytes=-0")
        self.assertEqual(status, 416)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes */{self.size}")

    def test_the_416_still_advertises_range_support(self):
        # ผู้เล่นอ่าน Accept-Ranges เพื่อรู้ว่ายังขอเป็นช่วงได้อยู่ ไม่ใช่ถอยไปโหลดทั้งไฟล์
        _, _, hdrs = self.fetch(f"bytes={self.size}-")
        self.assertEqual(hdrs.get("Accept-Ranges"), "bytes")

    def test_head_gets_the_same_answer(self):
        status, _, hdrs = self.head(f"bytes={self.size}-")
        self.assertEqual(status, 416)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes */{self.size}")

    def test_no_audio_body_is_sent_with_the_416(self):
        # ใจความของตั๋วทั้งข้อ: เดิมตรงนี้คือไฟล์ประชุมทั้งก้อน
        _, body, hdrs = self.fetch(f"bytes={self.size}-")
        self.assertEqual(hdrs.get("Content-Length"), "0")
        self.assertNotEqual(body, AUDIO)


class TestOrdinaryRangesStillWork(RangeCase):

    def test_no_range_header_serves_the_whole_file(self):
        status, body, hdrs = self.fetch()
        self.assertEqual(status, 200)
        self.assertEqual(body, AUDIO)
        self.assertEqual(hdrs.get("Accept-Ranges"), "bytes")

    def test_a_normal_range_is_206_with_the_right_bytes(self):
        status, body, hdrs = self.fetch("bytes=10-19")
        self.assertEqual(status, 206)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes 10-19/{self.size}")
        self.assertEqual(body, AUDIO[10:20])

    def test_an_open_ended_range_reaches_the_last_byte(self):
        status, body, hdrs = self.fetch("bytes=1000-")
        self.assertEqual(status, 206)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes 1000-{self.size - 1}/{self.size}")
        self.assertEqual(body, AUDIO[1000:])

    def test_a_suffix_range_returns_the_tail(self):
        status, body, _ = self.fetch("bytes=-16")
        self.assertEqual(status, 206)
        self.assertEqual(body, AUDIO[-16:])

    def test_a_suffix_longer_than_the_file_returns_the_whole_file(self):
        status, body, hdrs = self.fetch(f"bytes=-{self.size * 2}")
        self.assertEqual(status, 206)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes 0-{self.size - 1}/{self.size}")
        self.assertEqual(body, AUDIO)

    def test_an_end_past_the_file_is_clamped_not_rejected(self):
        # ผู้เล่นขอเผื่อท้ายเป็นเรื่องปกติ ตราบใดที่จุดเริ่มยังอยู่ในไฟล์ = สนองได้
        status, body, hdrs = self.fetch(f"bytes=1020-{self.size + 500}")
        self.assertEqual(status, 206)
        self.assertEqual(hdrs.get("Content-Range"), f"bytes 1020-{self.size - 1}/{self.size}")
        self.assertEqual(body, AUDIO[1020:])

    def test_the_last_byte_is_reachable(self):
        # เส้นแบ่งอยู่ตรงนี้พอดี: size-1 ต้องได้ 206 ส่วน size ต้องได้ 416
        status, body, _ = self.fetch(f"bytes={self.size - 1}-")
        self.assertEqual(status, 206)
        self.assertEqual(body, AUDIO[-1:])


class TestBrokenHeadersAreIgnored(RangeCase):
    """header ที่พังต้องถูกเมินแล้วเสิร์ฟทั้งไฟล์ — ไม่ใช่ 416 และไม่ใช่คำตอบพิกล."""

    def test_a_reversed_range_does_not_produce_a_negative_length(self):
        status, body, hdrs = self.fetch("bytes=5-2")
        self.assertEqual(status, 200)
        self.assertEqual(hdrs.get("Content-Length"), str(self.size))
        self.assertEqual(body, AUDIO)

    def test_a_nonsense_unit_is_ignored(self):
        status, body, _ = self.fetch("items=0-10")
        self.assertEqual(status, 200)
        self.assertEqual(body, AUDIO)

    def test_an_empty_range_is_ignored(self):
        status, _, _ = self.fetch("bytes=-")
        self.assertEqual(status, 200)

    def test_multiple_ranges_are_ignored_not_broken(self):
        # เราไม่รองรับ multipart/byteranges — RFC อนุญาตให้เมินแล้วส่งทั้งไฟล์
        status, body, _ = self.fetch("bytes=0-10,20-30")
        self.assertEqual(status, 200)
        self.assertEqual(body, AUDIO)
