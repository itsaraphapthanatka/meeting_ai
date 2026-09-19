"""BUG-065 — บอทเสียเวลาให้ selector ที่ไม่มีอยู่บนหน้านั้น ก่อนจะเจอปุ่มจริง.

เจ้าของบอกว่า "บอทส่งเข้าช้า" · วัดจาก log ของเครื่องประมวลผล (2026-09-19):

    +0s   รับงาน
    +1s   คอนเทนเนอร์ขึ้น ตรวจแพลตฟอร์มได้
    +3s   เริ่มอัดเสียง · เปิดลิงก์
    +6s   ตั้งชื่อบอท
    +11s  กดปุ่มเข้าห้อง      <-- 5 วินาทีหายไปตรงนี้

`click_first()` ไล่ selector ทีละตัว และตัวที่ **ไม่มีอยู่จริงบนหน้านั้น** กิน timeout
เต็มก้อนก่อนจะได้ลองตัวถัดไป ห้อง Meet ที่ต้องขออนุมัติมีแต่ปุ่ม "Ask to join" จึงเสีย
5 วินาทีให้ "Join now" ที่ไม่มีอยู่ ทุกครั้งที่ส่งบอท และถ้าไม่เจอสักตัว (UI เปลี่ยน)
เสียเต็ม ๆ 4 × 5 = 20 วินาที

แก้เป็น: รอ selector ทุกตัวพร้อมกันหนึ่งครั้ง แล้วค่อยเลือกตามลำดับความสำคัญเดิม
ด้วยเวลาสั้น ๆ — **ลำดับไม่เปลี่ยน** ซึ่งสำคัญกับรายการที่มี selector กว้าง ๆ เป็นตัวสำรอง
ท้ายสุด (เช่น `input[type="text"]` ของ Zoom)

เทสต์วัด "งบเวลาที่ขอรอทั้งหมด" ไม่ใช่เวลานาฬิกาจริง จึงรันเร็วและไม่ขึ้นกับเครื่อง
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parents[1] / "bot"
sys.path.insert(0, str(BOT_DIR))
platforms = importlib.import_module("platforms")

MEET_JOIN = [
    'button:has-text("Join now")',
    'button:has-text("Ask to join")',
    'button:has-text("เข้าร่วมเลย")',
    'button:has-text("ขอเข้าร่วม")',
]


class _Missing(Exception):
    pass


class FakeLocator:
    def __init__(self, page, selector: str) -> None:
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    async def wait_for(self, state="visible", timeout=0):
        # งบเวลาที่ "ขอรอ" — ของที่ไม่มีจะกินเต็มเพดาน ของที่มีตอบทันที
        parts = [p.strip() for p in self.selector.split(", ")]
        # Playwright แยกวิเคราะห์ selector ที่รวมคนละ engine ด้วยคอมมาไม่ออก — โยนทิ้งทันที
        # ไม่ใช่ "หาไม่เจอ" จำลองให้ตรง ไม่งั้นเทสต์จะไม่เห็นความต่างของการ์ด _combinable()
        if len(parts) > 1 and any(p.startswith(platforms._UNCOMBINABLE) for p in parts):
            raise ValueError(f"unknown engine in combined selector: {self.selector}")
        if any(p in self.page.visible for p in parts):
            return None
        self.page.waited += timeout
        raise _Missing(self.selector)

    async def click(self):
        self.page.clicked.append(self.selector)

    async def fill(self, value):
        self.page.filled.append((self.selector, value))


class FakePage:
    def __init__(self, visible) -> None:
        self.visible = set(visible)
        self.waited = 0        # มิลลิวินาทีที่ขอรอรวมทั้งหมด
        self.clicked: list[str] = []
        self.filled: list[tuple[str, str]] = []

    def locator(self, selector: str):
        return FakeLocator(self, selector)


def run(coro):
    return asyncio.run(coro)


class TestJoinButtonIsFoundWithoutWaitingOnMissingOnes(unittest.TestCase):

    def test_the_real_meet_case_no_longer_burns_five_seconds(self):
        # ห้องที่ต้องขออนุมัติ: มีแต่ "Ask to join"
        page = FakePage([MEET_JOIN[1]])
        ok = run(platforms.click_first(page, MEET_JOIN, timeout=5000))
        self.assertTrue(ok)
        self.assertEqual(page.clicked, [MEET_JOIN[1]])
        self.assertLessEqual(page.waited, 1000,
                             f"ยังเสียเวลารอ {page.waited} ms ให้ปุ่มที่ไม่มีอยู่")

    def test_a_room_that_needs_no_approval_is_just_as_fast(self):
        page = FakePage([MEET_JOIN[0]])
        self.assertTrue(run(platforms.click_first(page, MEET_JOIN, timeout=5000)))
        self.assertEqual(page.clicked, [MEET_JOIN[0]])
        self.assertEqual(page.waited, 0)

    def test_finding_nothing_costs_one_timeout_not_four(self):
        # UI เปลี่ยนจนหาปุ่มไม่เจอ — ของเดิมเสีย 4 x 5000 = 20 วินาที
        page = FakePage([])
        self.assertFalse(run(platforms.click_first(page, MEET_JOIN, timeout=5000)))
        self.assertEqual(page.clicked, [])
        self.assertLessEqual(page.waited, 5000,
                             f"เสียเวลารอ {page.waited} ms ทั้งที่ควรรอรอบเดียว")

    def test_priority_is_still_respected(self):
        """สำคัญ: รายการที่มีตัวสำรองกว้าง ๆ ท้ายสุด ต้องไม่ถูกเลือกก่อนตัวที่เจาะจง."""
        page = FakePage([MEET_JOIN[0], MEET_JOIN[1]])
        run(platforms.click_first(page, MEET_JOIN, timeout=5000))
        self.assertEqual(page.clicked, [MEET_JOIN[0]])

    def test_a_broad_fallback_last_still_comes_last(self):
        specific, broad = 'input[aria-label="Your name"]', 'input[type="text"]'
        page = FakePage([specific, broad])
        run(platforms.fill_first(page, [specific, broad], "AI Notetaker", timeout=5000))
        self.assertEqual(page.filled, [(specific, "AI Notetaker")])


class TestFillFirstGotTheSameTreatment(unittest.TestCase):

    def test_it_fills_the_one_that_exists(self):
        page = FakePage(['input[type="text"]'])
        self.assertTrue(run(platforms.fill_first(
            page, ['input[aria-label="ชื่อ"]', 'input[type="text"]'], "บอท", timeout=6000)))
        self.assertEqual(page.filled, [('input[type="text"]', "บอท")])
        self.assertLessEqual(page.waited, 1000)

    def test_no_field_at_all_returns_false_quickly(self):
        page = FakePage([])
        self.assertFalse(run(platforms.fill_first(page, ['input#a', 'input#b'],
                                                  "x", timeout=6000)))
        self.assertLessEqual(page.waited, 6000)


class TestSelectorsThatCannotBeCombined(unittest.TestCase):
    """Playwright มี selector หลาย engine — เอามาต่อด้วยคอมมาไม่ได้ทุกแบบ."""

    def test_text_engine_selectors_fall_back_to_the_old_loop(self):
        page = FakePage(["text=/ปุ่มจริง/i"])
        ok = run(platforms.click_first(page, ["text=/ไม่มี/i", "text=/ปุ่มจริง/i"],
                                       timeout=1000))
        self.assertTrue(ok)
        self.assertEqual(page.clicked, ["text=/ปุ่มจริง/i"])

    def test_the_guard_knows_which_ones_are_safe(self):
        self.assertTrue(platforms._combinable(['button:has-text("x")', '[aria-label*="y"]']))
        for bad in ("text=/x/i", "xpath=//button", "//button", "css=button"):
            with self.subTest(selector=bad):
                self.assertFalse(platforms._combinable(["button", bad]))


if __name__ == "__main__":
    unittest.main()
