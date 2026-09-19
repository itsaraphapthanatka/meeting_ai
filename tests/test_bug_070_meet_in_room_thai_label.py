"""BUG-070 — ตัวตรวจ "อยู่ในห้องแล้ว" ของ Meet ไม่ตรงกับคำแปลไทยปัจจุบัน.

log จริงตอนบอทเข้าห้องได้สำเร็จครั้งแรก (2026-09-19 14:08:30):

    [bot] ไม่มีช่องกรอกชื่อ — ปกติถ้าบอทล็อกอิน Google อยู่แล้ว
    [bot] กดปุ่มเข้าห้องแล้ว
    [bot] เฝ้าดูมา 0s (ยังไม่เข้าห้อง) — ... call_end / ออกจากการโทร / chat / แชทกับทุกคน

หน้าเว็บมีปุ่มวางสายและแถบควบคุมครบ = บอทอยู่ในห้องจริงและอัดเสียงอยู่ แต่ `MEET_IN`
หาคำว่า **"ออกจากสาย"** ซึ่ง Meet เปลี่ยนเป็น **"ออกจากการโทร"** ไปแล้ว ตัวตรวจจึงไม่เคยเจอ

ผลที่ตามมาหนักกว่าการรายงานสถานะผิด: `was_inside` ไม่เคยเป็นจริง แล้วกติกาที่เพิ่งเพิ่มใน
BUG-066 (`JOIN_WAIT_SEC` = 120 วิ) จะ **เตะบอทออกจากห้องที่มันเข้าได้แล้ว** — คือฟีเจอร์ที่
เพิ่งซ่อมกลายเป็นตัวทำลายการประชุมเสียเอง

ยืนยัน selector กับ DOM จริงในเบราว์เซอร์ (หน้าเลียนแบบจากข้อความใน log):
    selector_ok = true · หน้าในห้องแมตช์ 1 · หน้าห้องรอแมตช์ 0
"""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1] / "bot"
sys.path.insert(0, str(BOT_DIR))
platforms = importlib.import_module("platforms")

# ข้อความที่หน้าเว็บแสดงจริง ตอนบอทอยู่ในห้อง (คัดจาก log 2026-09-19 14:08:30)
IN_ROOM_LABELS = ("ออกจากการโทร", "แชทกับทุกคน", "เปิดไมโครโฟน (ctrl + d)")
# ข้อความบนหน้าห้องรอ/ถูกปฏิเสธ — ต้องไม่ถูกนับว่าอยู่ในห้อง
WAITING_LABELS = ("ขอเข้าร่วม", "เข้าร่วมเลย", "Returning to home screen")


class TestTheThaiLeaveButtonIsCovered(unittest.TestCase):

    def test_the_current_thai_label_is_matched(self):
        self.assertIn("ออกจากการโทร", platforms.MEET_IN,
                      "Meet ใช้คำนี้แล้ว — ไม่ครอบ = บอทถูกเตะออกจากห้องที่เข้าได้แล้ว")

    def test_the_old_thai_label_is_still_covered(self):
        # ผู้ใช้บางคนอาจยังเจอ UI เก่า อย่าทิ้งของเดิมตอนเพิ่มของใหม่
        self.assertIn("ออกจากสาย", platforms.MEET_IN)

    def test_english_is_still_covered(self):
        self.assertIn("Leave call", platforms.MEET_IN)

    def test_there_is_a_broad_thai_fallback(self):
        # Meet เปลี่ยนคำแปลมาแล้วอย่างน้อยหนึ่งครั้ง — เผื่อครั้งหน้าไว้ด้วย
        self.assertIn('button[aria-label*="ออกจาก"]', platforms.MEET_IN)


class TestTheSelectorStaysPlainCss(unittest.TestCase):
    """ผสม pseudo-class เฉพาะของ Playwright เข้าไปแล้วตรวจไม่ได้ว่าจะพังตอนไหน.

    โค้ดนี้รันได้เฉพาะในคอนเทนเนอร์ที่มี Playwright ซึ่งเครื่องพัฒนาและ CI ไม่มี —
    selector ที่พาร์สไม่ผ่านจะไปโผล่เป็น "บอทเข้าห้องไม่ได้" กลางการประชุมจริงเท่านั้น
    CSS ล้วนจึงตรวจในเบราว์เซอร์ธรรมดาได้ และเป็นเงื่อนไขที่ตั้งใจรักษาไว้
    """

    PLAYWRIGHT_ONLY = (":text-is(", ":text(", ":has-text(", ">>", "text=", "xpath=")

    def test_no_playwright_only_syntax(self):
        for marker in self.PLAYWRIGHT_ONLY:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, platforms.MEET_IN)

    def test_every_in_room_marker_is_plain_css(self):
        for name in ("MEET_IN", "TEAMS_IN", "ZOOM_IN"):
            sel = getattr(platforms, name)
            for marker in self.PLAYWRIGHT_ONLY:
                with self.subTest(marker=name):
                    self.assertNotIn(marker, sel)


class TestItCannotMatchTheWaitingScreen(unittest.TestCase):
    """false positive อันตรายกว่า false negative: จะรายงานว่าอยู่ในห้องทั้งที่ยังรออยู่."""

    def test_no_waiting_room_wording_leaked_into_the_selector(self):
        for word in WAITING_LABELS:
            with self.subTest(word=word):
                self.assertNotIn(word, platforms.MEET_IN)

    def test_it_does_not_match_the_join_buttons(self):
        # ปุ่มก่อนเข้าห้องไม่มีคำว่า "ออกจาก" อยู่เลย — นี่คือเหตุผลที่ fallback กว้าง ๆ ปลอดภัย
        for word in ("ขอเข้าร่วม", "เข้าร่วมเลย", "Ask to join", "Join now"):
            with self.subTest(word=word):
                self.assertNotIn(word, platforms.MEET_IN)


class TestTheConsequenceIsWiredUp(unittest.TestCase):
    """ปักหมุดว่าทำไมเรื่องนี้ถึงร้ายแรง — ผูกกับ leave_reason ของ BUG-066."""

    def test_never_seeing_inroom_ends_the_meeting(self):
        self.assertTrue(
            platforms.leave_reason(was_inside=False, end_seen=False,
                                   waited=platforms.JOIN_WAIT_SEC + 1),
            "ถ้าข้อนี้เปลี่ยน แปลว่าการตรวจไม่เจอ in-room ไม่อันตรายแล้ว — ทบทวนเทสต์นี้")

    def test_seeing_it_once_protects_the_rest_of_the_meeting(self):
        self.assertEqual(
            platforms.leave_reason(was_inside=True, end_seen=False,
                                   waited=platforms.JOIN_WAIT_SEC * 50), "")


if __name__ == "__main__":
    unittest.main()
