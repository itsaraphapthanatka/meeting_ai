"""BUG-048 — กติกากลางใน meeting_ai/web/sanitize.py (pure, stdlib เท่านั้น).

ทดสอบทีละฟังก์ชันแบบ table-driven ก่อนไปทดสอบ jobs.apply_result() ที่ใช้กติกาพวกนี้
(ดู test_bug_048_apply_result.py) — ไฟล์นี้ import ผ่าน _harness ก่อนเสมอตามธรรมเนียมทีม
แม้ sanitize.py เองจะไม่พึ่ง env ก็ตาม (import meeting_ai.web ก่อนจะลาก server.py/backend.py
มาด้วยซึ่งพึ่ง env — กันพลาดไปแตะ WEB_DIR/DB จริงถ้ารันไฟล์นี้เดี่ยวๆ)
"""

import math
import unittest
from unittest import mock

import _harness  # noqa: F401  (import ก่อนเพื่อบังคับตั้ง env ก่อน import meeting_ai.web.*)
from meeting_ai.web import sanitize


class TestSanitizeSegment(unittest.TestCase):
    def test_valid_segment_kept_with_rounded_times(self):
        out = sanitize.segment({"start": 1.005, "end": 2.5, "text": " สวัสดี ", "speaker": "A"})
        self.assertIsNotNone(out)
        self.assertEqual(out["start"], round(1.005, 2))
        self.assertEqual(out["end"], 2.5)
        self.assertEqual(out["text"], "สวัสดี")
        self.assertEqual(out["speaker"], "A")

    def test_missing_optional_fields_default_safely(self):
        out = sanitize.segment({})
        self.assertEqual(out, {"start": 0.0, "end": 0.0, "text": ""})
        self.assertNotIn("speaker", out)

    def test_nan_start_dropped(self):
        self.assertIsNone(sanitize.segment({"start": float("nan"), "end": 1, "text": "x"}))

    def test_infinity_end_dropped(self):
        self.assertIsNone(sanitize.segment({"start": 0, "end": float("inf"), "text": "x"}))

    def test_negative_infinity_start_dropped(self):
        self.assertIsNone(sanitize.segment({"start": float("-inf"), "end": 1, "text": "x"}))

    def test_non_numeric_start_dropped(self):
        self.assertIsNone(sanitize.segment({"start": "abc", "end": 1, "text": "x"}))

    def test_none_start_end_default_to_zero_not_dropped(self):
        # item.get("start", 0) เจอ None ตรงๆ (key มีอยู่แต่ค่า None) -> float(None) พัง -> ถูกทิ้ง
        self.assertIsNone(sanitize.segment({"start": None, "end": 1, "text": "x"}))

    def test_non_dict_item_dropped(self):
        self.assertIsNone(sanitize.segment("not a dict"))
        self.assertIsNone(sanitize.segment(None))
        self.assertIsNone(sanitize.segment(["start", 0]))

    def test_text_over_cap_truncated_not_dropped(self):
        long_text = "ก" * (sanitize.MAX_SEGMENT_TEXT + 500)
        out = sanitize.segment({"start": 0, "end": 1, "text": long_text})
        self.assertIsNotNone(out)
        self.assertEqual(len(out["text"]), sanitize.MAX_SEGMENT_TEXT)

    def test_speaker_with_control_chars_cleaned_not_dropped(self):
        out = sanitize.segment({"start": 0, "end": 1, "text": "x", "speaker": "A\r\nB\t C"})
        self.assertIsNotNone(out)
        self.assertNotIn("\n", out["speaker"])
        self.assertNotIn("\r", out["speaker"])
        self.assertNotIn("\t", out["speaker"])

    def test_empty_speaker_omitted(self):
        out = sanitize.segment({"start": 0, "end": 1, "text": "x", "speaker": "   "})
        self.assertIsNotNone(out)
        self.assertNotIn("speaker", out)

    def test_non_string_text_coerced(self):
        out = sanitize.segment({"start": 0, "end": 1, "text": 123})
        self.assertEqual(out["text"], "123")


class TestSanitizeSegments(unittest.TestCase):
    def test_raw_none_is_zero_dropped_not_one(self):
        # ไม่มี key "segments" มาเลย (result.get("segments") -> None) ต้องไม่นับเป็นของเสีย
        kept, dropped = sanitize.segments(None)
        self.assertEqual((kept, dropped), ([], 0))

    def test_raw_non_list_truthy_counts_as_one_dropped(self):
        kept, dropped = sanitize.segments("oops a string")
        self.assertEqual((kept, dropped), ([], 1))

    def test_empty_list_is_fine(self):
        self.assertEqual(sanitize.segments([]), ([], 0))

    def test_mixed_list_keeps_valid_drops_invalid(self):
        raw = [
            {"start": 0, "end": 1, "text": "ok1"},
            {"start": float("nan"), "end": 1, "text": "bad-nan"},
            {"start": 0, "end": float("inf"), "text": "bad-inf"},
            {"start": "x", "end": 1, "text": "bad-str"},
            "not-a-dict",
            {"start": 2, "end": 3, "text": "ok2"},
        ]
        kept, dropped = sanitize.segments(raw)
        self.assertEqual(dropped, 4)
        self.assertEqual([s["text"] for s in kept], ["ok1", "ok2"])

    def test_over_cap_count_truncated(self):
        with mock.patch.object(sanitize, "MAX_SEGMENTS", 3):
            raw = [{"start": i, "end": i + 1, "text": str(i)} for i in range(10)]
            kept, dropped = sanitize.segments(raw)
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, 7)


class TestSanitizeDuration(unittest.TestCase):
    def test_finite_positive_kept(self):
        self.assertEqual(sanitize.duration(12.5), 12.5)

    def test_numeric_string_coerced(self):
        self.assertEqual(sanitize.duration("42"), 42.0)

    def test_nan_becomes_zero(self):
        self.assertEqual(sanitize.duration(float("nan")), 0.0)

    def test_infinity_becomes_zero(self):
        self.assertEqual(sanitize.duration(float("inf")), 0.0)
        self.assertEqual(sanitize.duration(float("-inf")), 0.0)

    def test_negative_becomes_zero(self):
        self.assertEqual(sanitize.duration(-5), 0.0)

    def test_non_numeric_becomes_zero(self):
        self.assertEqual(sanitize.duration("not a number"), 0.0)
        self.assertEqual(sanitize.duration(None), 0.0)
        self.assertEqual(sanitize.duration([1, 2]), 0.0)


class TestSanitizeLanguageAndName(unittest.TestCase):
    def test_language_truncated_to_cap(self):
        out = sanitize.language("x" * 100)
        self.assertEqual(len(out), sanitize.MAX_LANGUAGE)

    def test_language_control_chars_stripped(self):
        self.assertNotIn("\n", sanitize.language("th\nen"))

    def test_name_truncated_and_trimmed(self):
        out = sanitize.name("  " + "A" * 100 + "  ")
        self.assertLessEqual(len(out), sanitize.MAX_NAME)
        self.assertFalse(out.startswith(" "))


class TestSanitizeSpeakers(unittest.TestCase):
    def test_non_list_becomes_empty(self):
        self.assertEqual(sanitize.speakers("A"), [])
        self.assertEqual(sanitize.speakers(None), [])
        self.assertEqual(sanitize.speakers({"a": 1}), [])

    def test_non_string_items_dropped(self):
        self.assertEqual(sanitize.speakers(["A", 123, {"x": 1}, None, "B"]), ["A", "B"])

    def test_duplicates_removed_preserving_order(self):
        self.assertEqual(sanitize.speakers(["A", "B", "A"]), ["A", "B"])

    def test_over_cap_truncated(self):
        with mock.patch.object(sanitize, "MAX_SPEAKERS", 2):
            self.assertEqual(sanitize.speakers(["A", "B", "C", "D"]), ["A", "B"])

    def test_blank_after_cleaning_dropped(self):
        self.assertEqual(sanitize.speakers(["   ", "\r\n", "C"]), ["C"])


class TestSanitizeTextAndMessage(unittest.TestCase):
    def test_text_none_becomes_empty_string(self):
        self.assertEqual(sanitize.text(None), "")

    def test_text_keeps_newlines_for_markdown_summary(self):
        self.assertIn("\n", sanitize.text("line1\nline2"))

    def test_text_limit_applied(self):
        self.assertEqual(len(sanitize.text("x" * 50, 10)), 10)

    def test_message_blank_becomes_none(self):
        self.assertIsNone(sanitize.message(""))
        self.assertIsNone(sanitize.message("   "))
        self.assertIsNone(sanitize.message(None))

    def test_message_over_cap_truncated(self):
        out = sanitize.message("y" * 1000)
        self.assertEqual(len(out), sanitize.MAX_MESSAGE)


if __name__ == "__main__":
    unittest.main()
