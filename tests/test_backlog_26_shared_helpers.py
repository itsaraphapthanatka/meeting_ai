"""BACKLOG #26 — store.py กับ pgstore.py มีตัวช่วยชุดเดียวกันคนละสำเนา.

ตั๋วเขียนว่า "~70 duplicated lines" เหมือนเป็นเรื่องความสวยงาม ของจริงคือ **มันเพี้ยนจากกัน
ไปแล้ว และเพี้ยนไปทางเดียว** — ฝั่ง Postgres ถูกทำให้ทนค่า None ไปแล้วสามจุด ฝั่งไฟล์ไม่ถูก
แก้ตาม วัดก่อนรวม (2026-09-18):

    fmt_time(None)                  store.py -> TypeError       · pgstore.py -> "00:00"
    transcript_text ที่ text=None   store.py -> AttributeError  · pgstore.py -> ""
    _snippet(None, "ก")             store.py -> AttributeError  · pgstore.py -> ""

กับดักที่ทำให้ None หลุดเข้ามาได้: `meta.get("duration", 0)` คืน 0 เฉพาะตอน**ไม่มีคีย์**
ถ้าคีย์มีแต่ค่าเป็น `null` (ข้อมูลเก่าก่อนมี sanitize.py หรือไฟล์ที่ถูกแก้มือ) มันคืน None
แล้วส่งต่อเข้าฟังก์ชันพวกนี้ตรง ๆ ผู้เรียกคือหน้ารายละเอียดและตัว export — พังตรงนั้นเท่ากับ
เปิดการประชุมนั้นไม่ได้เลย เพราะเวลาตัวเดียวที่ไม่มีค่า

เทสต์ไฟล์นี้จึงไม่ได้เช็คแค่ "โค้ดไม่ซ้ำแล้ว" แต่เช็คว่า **สองโหมดตอบเหมือนกันทุกอินพุต**
รวมถึงรูปแบบเวลาที่ผู้ใช้เห็น ซึ่งเกือบเปลี่ยนตอนรวม (MM:SS เมื่อไม่ถึงชั่วโมง ไม่ใช่ HH:MM:SS)
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from meeting_ai.web import _common, pgstore, store

WEB = Path(_common.__file__).parent
SHARED = ("new_id", "valid_id", "fmt_time", "transcript_text", "timestamped", "_snippet")


class TestBothModesAgree(unittest.TestCase):
    """เส้นที่สำคัญที่สุดของตั๋วนี้: โหมดไฟล์กับโหมด cloud ต้องตอบเหมือนกัน."""

    def test_fmt_time_agrees_including_the_format_users_see(self):
        # เกือบพลาด: ตอนรวมผมเขียนเป็น HH:MM:SS เสมอ ซึ่งเปลี่ยนเวลาที่ผู้ใช้เห็นทุกอัน
        for sec in (0, 1, 59, 60, 61, 599, 3599, 3600, 3601, 7322, 86399):
            with self.subTest(sec=sec):
                self.assertEqual(store.fmt_time(sec), pgstore.fmt_time(sec))

    def test_under_an_hour_has_no_hour_part(self):
        self.assertEqual(store.fmt_time(323), "05:23")
        self.assertEqual(store.fmt_time(3601), "01:00:01")

    def test_valid_id_agrees(self):
        cases = ["20260918-120000-abcdef", "20260918-120000-ABCDEF", "", None,
                 "20260918-120000-abcdef\n", "../../etc/passwd", "20260918-120000-abcde"]
        for mid in cases:
            with self.subTest(mid=mid):
                self.assertEqual(store.valid_id(mid), pgstore.valid_id(mid))

    def test_a_trailing_newline_is_still_rejected(self):
        # fullmatch ไม่ใช่ match — `$` ของ re ปล่อยตัวขึ้นบรรทัดใหม่ท้ายสุดผ่านได้
        self.assertFalse(store.valid_id("20260918-120000-abcdef\n"))

    def test_transcript_text_agrees(self):
        details = [
            {},
            {"segments": []},
            {"segments": [{"text": " สวัสดี "}, {"text": "ครับ"}]},
            {"segments": [{"text": None}, {"text": "ครับ"}]},
            {"segments": [{}]},
        ]
        for detail in details:
            with self.subTest(detail=detail):
                self.assertEqual(store.transcript_text(detail), pgstore.transcript_text(detail))

    def test_timestamped_agrees(self):
        detail = {"segments": [
            {"start": 0, "end": 5, "text": "เปิดประชุม", "speaker": "ผู้พูด 1"},
            {"start": 5, "end": 9, "text": None},
            {"start": None, "end": None, "text": "ไม่มีเวลา"},
        ]}
        self.assertEqual(store.timestamped(detail), pgstore.timestamped(detail))

    def test_snippet_agrees(self):
        for text in (None, "", "ประชุมเรื่องงบประมาณประจำปี", "ก" * 300):
            with self.subTest(text=(text or "")[:12]):
                self.assertEqual(store._snippet(text, "งบ"), pgstore._snippet(text, "งบ"))


class TestTheNullsThatUsedToCrashFileMode(unittest.TestCase):
    """สามเคสที่ฝั่งไฟล์เคยโยนข้อยกเว้น ส่วนฝั่ง cloud รอดมานานแล้ว."""

    def test_a_null_duration_no_longer_raises(self):
        self.assertEqual(store.fmt_time(None), "00:00")

    def test_a_null_segment_text_no_longer_raises(self):
        self.assertEqual(store.transcript_text({"segments": [{"text": None}]}), "")

    def test_a_null_search_target_no_longer_raises(self):
        self.assertEqual(store._snippet(None, "ก"), "")

    def test_the_get_with_default_trap_is_what_lets_none_through(self):
        # เอกสารประกอบของบั๊ก: .get(key, 0) ไม่ได้กันค่า null ที่เก็บไว้จริง
        meta = {"duration": None}
        self.assertIsNone(meta.get("duration", 0))
        self.assertEqual(store.fmt_time(meta.get("duration", 0)), "00:00")


class TestThereIsOnlyOneCopyNow(unittest.TestCase):

    @staticmethod
    def _funcs(name: str) -> dict[str, str]:
        text = (WEB / name).read_text(encoding="utf-8")
        lines = text.split("\n")
        return {n.name: "\n".join(lines[n.lineno - 1:n.end_lineno])
                for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)}

    def test_neither_store_defines_them_any_more(self):
        for module in ("store.py", "pgstore.py"):
            defined = self._funcs(module)
            for name in SHARED:
                with self.subTest(module=module, func=name):
                    self.assertNotIn(name, defined, f"{module} ยังนิยาม {name} เอง")

    def test_common_defines_them_all(self):
        for name in SHARED:
            with self.subTest(func=name):
                public = "snippet" if name == "_snippet" else name
                self.assertTrue(callable(getattr(_common, public)))

    def test_the_names_are_still_reachable_through_both_modules(self):
        # server.py / exports.py / เทสต์เดิม เรียก store.fmt_time, pgstore.valid_id ฯลฯ
        for module in (store, pgstore):
            for name in SHARED:
                with self.subTest(module=module.__name__, func=name):
                    self.assertTrue(callable(getattr(module, name)))

    def test_the_id_pattern_is_defined_once(self):
        self.assertIs(store._ID_RE, pgstore._ID_RE)
        self.assertIs(store._ID_RE, _common.ID_RE)

    def test_the_snippet_padding_is_defined_once(self):
        self.assertEqual(store.SNIPPET_PAD, _common.SNIPPET_PAD)
        self.assertEqual(pgstore.SNIPPET_PAD, _common.SNIPPET_PAD)


class TestNewIdWasNotQuietlyChanged(unittest.TestCase):
    """id การประชุมกลายเป็นชื่อไฟล์และส่วนหนึ่งของ URL — เปลี่ยนวิธีสร้างต้องเป็นเรื่องตั้งใจ."""

    def test_the_shape_is_unchanged(self):
        for _ in range(20):
            mid = _common.new_id()
            self.assertTrue(_common.valid_id(mid), mid)

    def test_it_still_uses_a_secure_source(self):
        # ตอนรวมผมเผลอเขียนใหม่เป็น random.randrange ซึ่งไม่ใช่ของเดิม
        src = (WEB / "_common.py").read_text(encoding="utf-8")
        self.assertIn("secrets.token_hex(3)", src)
        self.assertNotIn("random.randrange", src)

    def test_two_ids_in_the_same_second_differ(self):
        self.assertNotEqual(_common.new_id()[-6:], _common.new_id()[-6:])


if __name__ == "__main__":
    unittest.main()
