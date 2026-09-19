"""BUG-066 — บอทออกจากห้องในวินาทีเดียวกับที่กดปุ่มเข้าห้อง.

เจ้าของบอกว่า "บอทส่งเข้าช้า" แล้วลองส่งสด (2026-09-19) log ชี้ตรงกว่านั้นมาก:

    11:56:53  [bot] กดปุ่มเข้าห้องแล้ว — รอ host กดรับถ้าเป็นห้องที่ต้องอนุมัติ
    11:56:53  [bot] ตรวจพบว่าประชุมจบ/ออกจากห้องแล้ว      <-- วินาทีเดียวกัน

ไม่ใช่เรื่องช้า และไม่ใช่เรื่องเจ้าของกด Admit ไม่ทัน — **บอทไม่เคยให้โอกาสกดเลย**

เหตุ: `MEET_END` จับคำว่า `Return to home` ซึ่งเป็นข้อความบน **หน้าห้องรอของ Meet เอง**
(`Returning to home screen in 60 seconds`) และ `_monitor()` ตรวจตัวจับจบห้องตั้งแต่รอบแรก
คือก่อนบอทเข้าห้องได้ จึงเจอทันทีแล้วสรุปว่าประชุมจบ

แก้: ตัวจับ "ประชุมจบ" เชื่อได้ต่อเมื่อบอท **เคยเข้าห้องแล้ว** เท่านั้น ส่วนช่วงก่อนหน้านั้น
ใช้เพดานเวลารอ (`JOIN_WAIT_SEC`) แทน — ไม่งั้นห้องที่ปฏิเสธบอทจริงจะถูกอัดความเงียบยาว
จนครบ MAX_MINUTES (ค่าเริ่มต้น 180 นาที)

`leave_reason()` เป็นฟังก์ชันล้วน จึงทดสอบได้ครบทุกทางโดยไม่ต้องมี Docker/เบราว์เซอร์
"""

from __future__ import annotations

import importlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "bot"
sys.path.insert(0, str(BOT_DIR))
platforms = importlib.import_module("platforms")
JOIN_SRC = (BOT_DIR / "join_meeting.py").read_text(encoding="utf-8")


class TestTheReportedSymptom(unittest.TestCase):
    """สถานการณ์จาก log จริง: เพิ่งกดเข้าห้อง ยังไม่เคยเข้าได้ และหน้ารอมีข้อความจบห้อง."""

    def test_it_does_not_leave_the_instant_it_joins(self):
        self.assertEqual(
            platforms.leave_reason(was_inside=False, end_seen=True, waited=0.0), "",
            "บอทยังออกทันทีที่กดเข้าห้อง — เจ้าของไม่มีทางกด Admit ทัน")

    def test_it_keeps_waiting_through_the_whole_grace_period(self):
        for waited in (5, 30, 60, platforms.JOIN_WAIT_SEC - 1):
            with self.subTest(waited=waited):
                self.assertEqual(
                    platforms.leave_reason(False, True, waited), "",
                    f"ยอมแพ้ตั้งแต่วินาทีที่ {waited} ทั้งที่ยังไม่หมดเวลารอ")

    def test_the_grace_period_is_long_enough_to_be_useful(self):
        # สั้นกว่านี้คือกลับไปเป็นบั๊กเดิมในรูปแบบที่อ่อนลง
        self.assertGreaterEqual(platforms.JOIN_WAIT_SEC, 60)


class TestItStillGivesUpEventually(unittest.TestCase):
    """ไม่งั้นห้องที่ปฏิเสธบอทจะถูกอัดความเงียบจนครบ MAX_MINUTES."""

    def test_it_stops_once_the_grace_period_is_over(self):
        why = platforms.leave_reason(False, True, platforms.JOIN_WAIT_SEC)
        self.assertIn("ไม่ได้ถูกรับเข้าห้อง", why)
        self.assertIn("Admit", why)

    def test_it_stops_even_when_the_page_shows_nothing_useful(self):
        # หน้าอาจไม่มีข้อความจบห้องเลย แต่ก็ยังไม่ได้เข้าห้องอยู่ดี
        self.assertTrue(platforms.leave_reason(False, False, platforms.JOIN_WAIT_SEC + 1))

    def test_the_reason_tells_the_owner_what_to_do(self):
        why = platforms.leave_reason(False, False, platforms.JOIN_WAIT_SEC)
        self.assertIn("รับเข้าห้อง", why)


class TestOnceInsideTheOldBehaviourIsUnchanged(unittest.TestCase):
    """เข้าห้องได้แล้ว ตัวจับจบห้องต้องเชื่อถือได้เหมือนเดิม ไม่งั้นบอทไม่ยอมออก."""

    def test_end_markers_end_the_meeting(self):
        self.assertIn("ประชุมจบ", platforms.leave_reason(True, True, 10.0))

    def test_it_stays_while_the_meeting_is_running(self):
        for waited in (10.0, 3600.0, 10800.0):
            with self.subTest(waited=waited):
                self.assertEqual(platforms.leave_reason(True, False, waited), "")

    def test_the_grace_period_never_kicks_a_bot_out_of_a_live_meeting(self):
        # เคยเข้าห้องแล้ว เพดานเวลารอต้องไม่มีผลอีก — ประชุมยาวกว่า 2 นาทีเป็นเรื่องปกติ
        self.assertEqual(
            platforms.leave_reason(True, False, platforms.JOIN_WAIT_SEC * 100), "")


class TestTheMonitorActuallyUsesIt(unittest.TestCase):

    def test_the_loop_calls_leave_reason(self):
        self.assertIn("platforms.leave_reason(was_inside, end_seen, now - start)", JOIN_SRC)

    def test_the_loop_no_longer_quits_on_end_markers_by_itself(self):
        # รูปเดิม: เจอ end_markers แล้ว return ทันทีโดยไม่สนว่าเคยเข้าห้องหรือยัง
        self.assertNotRegex(
            JOIN_SRC,
            r"if await _visible\(page, end_markers[^)]*\):\s*\n\s*log\(")

    def test_it_remembers_having_been_inside(self):
        self.assertIn("was_inside = was_inside or inside", JOIN_SRC)


class TestTheOverlapThatCausedIt(unittest.TestCase):
    """ปักหมุดว่าข้อความสองชุดนี้ทับกันจริง — ถ้าวันหนึ่งไม่ทับแล้ว ค่อยกลับมาคิดใหม่."""

    def test_the_meet_end_pattern_matches_the_waiting_screen(self):
        # ข้อความจริงจาก log ของเครื่องประมวลผล 2026-09-19
        waiting_screen = ("You can't join this video call / Return to home screen / "
                          "Submit feedback / Your meeting is safe / Returning to home "
                          "screen in 60 seconds.")
        body = platforms.MEET_END.split("text=/", 1)[1].rsplit("/i", 1)[0]
        self.assertRegex(waiting_screen, re.compile(body, re.I),
                         "ถ้าไม่ทับแล้ว แปลว่าเหตุของบั๊กหายไป — ทบทวน leave_reason ได้")


if __name__ == "__main__":
    unittest.main()
