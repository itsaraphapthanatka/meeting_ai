"""BACKLOG #74 — เปิดหน้า "ประชุมใหม่" มาแล้วต้องตกที่แท็บ "อัดสด".

เจ้าของขอ (2026-09-19) ทันทีหลัง #73 สลับลำดับปุ่มเสร็จ

ก่อนหน้านี้แท็บเริ่มต้นคือ **ผลข้างเคียงของลำดับ** — `setupCapturePicker()` จบด้วย
`pick(btns[0].dataset.cap)` คือปุ่มซ้ายสุด อยากเปลี่ยนแท็บแรกต้องเรียงปุ่มใหม่ทั้งแถบ
ซึ่งพอ #73 ย้ายบอทไปซ้ายสุด แท็บเริ่มต้นก็เปลี่ยนตามไปโดยไม่มีใครขอ

ตอนนี้แยกเป็น `DEFAULT_CAP` ต่างหาก ลำดับปุ่มกับค่าเริ่มต้นจึงขยับแยกกันได้ —
ซึ่งจำเป็น เพราะ "อัดสด" อยู่ **ตรงกลาง** แถบ ไม่มีทางเป็น `btns[0]` ได้เลย

**ต้องมีทางถอย**: การ์ดอัดสดถูกแอดมินปิดได้ (`#rec-card.hidden`) ปุ่มของมันจะไม่อยู่
ใน `btns` เลย ถ้าไม่ถอยไปปุ่มแรกที่เหลือ จะเปิดมาเจอหน้าที่ไม่มีแท็บไหนถูกเลือก

วัดกับเบราว์เซอร์จริงที่ 375x812:

    ปกติ              ปุ่ม bot, rec(เลือก), upload   · การ์ดที่โชว์ ['rec'] · cap-rec=True
    แอดมินปิดอัดสด    ปุ่ม bot(เลือก), upload        · การ์ดที่โชว์ ['bot'] · cap-rec=False
    เหลือแต่บอท       แถบซ่อน (segHidden=True)       · การ์ดที่โชว์ ['bot']

และแถบอัดลอยด้านล่างโผล่มาพร้อมกันตั้งแต่เปิดหน้า (ปุ่ม "⏺ เริ่มอัด" ที่ y=769
hit-test กลางปุ่มแล้วกดโดนตัวเอง ไม่มีอะไรบัง) — ตั้งใจให้เป็นแบบนั้น เพราะมันคือ
สิ่งที่แท็บอัดสดมีไว้ให้กด
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "meeting_ai" / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")


def default_cap() -> str:
    m = re.search(r"const DEFAULT_CAP = '([a-z]+)';", APP_JS)
    assert m is not None, "หา DEFAULT_CAP ใน app.js ไม่เจอ"
    return m.group(1)


def seg() -> str:
    s = HTML[HTML.index('id="cap-seg"'):]
    return s[:s.index("</div>")]


class TestItOpensOnLiveRecording(unittest.TestCase):

    def test_the_default_is_the_live_recording_tab(self):
        self.assertEqual(default_cap(), "rec")

    def test_the_markup_preselects_the_same_tab(self):
        # is-on คือสถานะก่อน JS ทำงาน ไม่ตรงกับ DEFAULT_CAP = เห็นไฮไลต์กระพริบสลับ
        on = re.findall(r'<button class="seg-btn is-on"[^>]*data-cap="([a-z]+)"', seg())
        self.assertEqual(on, [default_cap()])

    def test_only_one_button_is_preselected(self):
        self.assertEqual(seg().count("is-on"), 1)

    def test_that_tab_really_exists_in_the_strip(self):
        self.assertIn(f'data-cap="{default_cap()}"', seg())


class TestItIsNoLongerTiedToTheOrder(unittest.TestCase):
    """เหตุผลที่ต้องแยกออกมา: "อัดสด" อยู่ตรงกลาง เป็น btns[0] ไม่ได้."""

    def test_the_picker_uses_the_constant_not_the_first_button(self):
        self.assertIn("b.dataset.cap === DEFAULT_CAP", APP_JS)
        self.assertNotIn("pick(btns[0].dataset.cap)", APP_JS)

    def test_the_default_is_not_the_leftmost_button(self):
        order = re.findall(r'data-cap="([a-z]+)"', seg())
        self.assertNotEqual(order[0], default_cap(),
                            "ถ้าวันหนึ่งมันกลับมาตรงกัน เทสต์กลุ่มนี้จะพิสูจน์อะไรไม่ได้อีก "
                            "— ตั้งใจให้แดงเพื่อเตือนว่าต้องทบทวน")


class TestTheFallbackWhenAdminDisablesIt(unittest.TestCase):
    """แอดมินปิดการ์ดอัดสดได้ ปุ่มมันจะหายไปจาก btns ทั้งอัน."""

    def test_it_falls_back_to_the_first_available_button(self):
        self.assertRegex(
            APP_JS,
            r"btns\.find\(\(b\) => b\.dataset\.cap === DEFAULT_CAP\) \|\| btns\[0\]",
            "ไม่มีทางถอย = เปิดมาเจอหน้าที่ไม่มีแท็บไหนถูกเลือก")

    def test_the_rec_card_is_still_the_hideable_one(self):
        # ปักหมุดว่าทางถอยนี้มีไว้เพราะอะไร ถ้าการ์ดนี้ปิดไม่ได้แล้ว ก็ทบทวนได้
        self.assertIn('id="rec-card"', HTML)
        self.assertIn("rec-card", APP_JS + (STATIC / "style.css").read_text(encoding="utf-8"))

    def test_the_strip_hides_itself_when_only_one_way_is_left(self):
        self.assertIn("seg.hidden = btns.length < 2", APP_JS)


if __name__ == "__main__":
    unittest.main()
