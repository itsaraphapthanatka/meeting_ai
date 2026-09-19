"""BUG-073 — ลำดับ "เอาเสียงเข้ามายังไง": ส่งบอทซ้ายสุด อัปโหลดขวาสุด.

เจ้าของขอ (2026-09-19): ย้าย "อัปโหลดไฟล์" ไปขวาสุด "ส่งบอท" มาซ้ายสุด

สิ่งที่ไฟล์นี้กันไว้ไม่ใช่ลำดับอย่างเดียว แต่เป็น **สามที่ที่ต้องเรียงตรงกัน**:
แถบปุ่ม `#cap-seg` · การ์ดใน `.cards` · คำโปรยบนปุ่ม "เริ่มประชุมใหม่" ของจอแคบ
ย้ายที่เดียวลืมอีกสองที่ = จอแคบสลับการ์ดถูกแต่จอกว้างเรียงอีกแบบ

และค่าเริ่มต้นไม่ได้เขียนตายตัวไว้ที่ไหน — `setupCapturePicker()` เรียก
`pick(btns[0].dataset.cap)` คือ **ปุ่มซ้ายสุดที่ใช้ได้** ลำดับใน HTML จึงเป็นตัวกำหนด
ว่าเปิดหน้ามาเจอแท็บไหน เทสต์จึงผูกกติกาข้อนี้ไว้ด้วย ไม่งั้นการย้ายปุ่มครั้งหน้า
จะเปลี่ยนพฤติกรรมโดยไม่มีใครตั้งใจ

วัดกับเบราว์เซอร์จริงที่ 375x812 (hit-test กลางปุ่มแล้วยิงคลิกจริง):

    ส่งบอท        x=75   กดโดนตัวเอง=True  การ์ดที่โชว์=['bot']
    อัดสด         x=188  กดโดนตัวเอง=True  การ์ดที่โชว์=['rec']
    อัปโหลดไฟล์   x=301  กดโดนตัวเอง=True  การ์ดที่โชว์=['upload']

และตอนแอดมินปิดอัดสด: ปุ่มที่เหลือ = bot(เลือก), upload — ยังตกที่ซ้ายสุดเหมือนเดิม
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "meeting_ai" / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

WANT = ["bot", "rec", "upload"]


def seg_order() -> list[str]:
    seg = HTML[HTML.index('id="cap-seg"'):]
    seg = seg[:seg.index("</div>")]
    return re.findall(r'data-cap="([a-z]+)"', seg)


def card_order() -> list[str]:
    cards = HTML[HTML.index('<div class="cards">'):]
    return re.findall(r'<div class="card"[^>]*data-cap="([a-z]+)"', cards)


class TestTheOrderTheOwnerAskedFor(unittest.TestCase):

    def test_the_buttons_read_left_to_right(self):
        self.assertEqual(seg_order(), WANT)

    def test_upload_is_last(self):
        self.assertEqual(seg_order()[-1], "upload")

    def test_bot_is_first(self):
        self.assertEqual(seg_order()[0], "bot")


class TestTheThreePlacesAgree(unittest.TestCase):
    """ย้ายที่เดียวแล้วลืมอีกสองที่ = จอแคบกับจอกว้างเรียงคนละแบบ."""

    def test_the_cards_follow_the_buttons(self):
        self.assertEqual(card_order(), seg_order(),
                         "จอกว้างกางการ์ดตามลำดับ DOM — ต้องตรงกับแถบปุ่มของจอแคบ")

    def test_there_are_exactly_three_of_each(self):
        self.assertEqual(len(seg_order()), 3)
        self.assertEqual(len(card_order()), 3)

    def test_the_home_cta_subtitle_lists_the_same_order(self):
        m = re.search(r'class="m-cta-s">([^<]+)<', HTML)
        self.assertIsNotNone(m, "หาคำโปรยปุ่มเริ่มประชุมใหม่ไม่เจอ")
        words = {"bot": "ส่งบอท", "rec": "อัดสด", "upload": "อัปโหลด"}
        pos = [m.group(1).index(words[cap]) for cap in seg_order()]
        self.assertEqual(pos, sorted(pos), f"คำโปรยเรียงไม่ตรงกับแถบปุ่ม: {m.group(1)}")


class TestTheDefaultFollowsTheFirstButton(unittest.TestCase):
    """ค่าเริ่มต้นไม่ได้เขียนตายตัว — มันคือ "ปุ่มซ้ายสุดที่ใช้ได้"."""

    def test_the_picker_defaults_to_the_first_available_button(self):
        self.assertIn("pick(btns[0].dataset.cap)", APP_JS,
                      "ถ้ากติกานี้เปลี่ยน ลำดับใน HTML จะไม่ใช่ตัวกำหนดแท็บเริ่มต้นอีกต่อไป")

    def test_the_markup_preselects_that_same_button(self):
        # is-on ใน HTML คือสถานะก่อน JS ทำงาน ถ้าไม่ตรงกับ btns[0] จะเห็นไฮไลต์กระพริบสลับ
        on = re.findall(r'<button class="seg-btn is-on"[^>]*data-cap="([a-z]+)"',
                        HTML[HTML.index('id="cap-seg"'):])
        self.assertEqual(on, [seg_order()[0]])

    def test_only_one_button_is_preselected(self):
        seg = HTML[HTML.index('id="cap-seg"'):]
        seg = seg[:seg.index("</div>")]
        self.assertEqual(seg.count("is-on"), 1)


if __name__ == "__main__":
    unittest.main()
