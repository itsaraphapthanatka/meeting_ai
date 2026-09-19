"""BACKLOG #79 — แท็บ "เอาเสียงเข้ามายังไง" ใช้ทั้งจอกว้างและจอแคบ.

เจ้าของขอ (2026-09-20) พร้อมภาพหน้าจอเดสก์ท็อป: จอกว้างกางการ์ดทั้งสามใบพร้อมกัน
ซึ่งกลายเป็นกริดสองคอลัมน์ที่ใบที่สามตกไปแถวล่าง เหลือที่ว่างข้าง ๆ หนึ่งช่อง และทำให้
หน้ายาวขึ้นโดยไม่ได้อะไรกลับมา — คนก็เลือกทางเดียวอยู่ดี

สิ่งที่ **ไม่** เปลี่ยน และมีเทสต์ปักหมุดไว้:
- แท็บของหน้ารายละเอียด (`#d-seg`) ยังเป็นของจอแคบล้วน `setupDetailTabs()` ยังเช็ค
  `isMobile()` อยู่ — เจ้าของพูดถึงฟอร์ม "ประชุมใหม่" ไม่ใช่ทั้งเว็บ
- คำอธิบายใต้หัวข้อการ์ด (`p.muted`) ยัง **ซ่อนเฉพาะจอแคบ** จอกว้างมีที่พอและข้อความ
  นั้นมีประโยชน์จริง
- เหลือทางเดียวเมื่อไร แถบแท็บซ่อนตัว แล้วการ์ดต้อง **ได้กรอบกับหัวข้อคืน** เพราะกฎถอด
  กรอบผูกกับ `.seg:not([hidden])` ไม่ใช่กับความกว้างจอ

วัดกับเบราว์เซอร์จริงบนเซิร์ฟเวอร์จริงจาก git worktree:

    1280x900  แถบแท็บ display:flex · ปุ่มกว้าง 283px เท่ากันสามปุ่ม x=367/652/938
              hit-test กลางปุ่มแล้วยิงคลิกจริง โดนตัวเองทั้งสาม การ์ดตรงกับปุ่มทุกครั้ง
              แถบอัดลอยโผล่เฉพาะแท็บอัดสด (flex / none / none)
              ความสูงทั้งหน้า 1104px (เดิมต้องเลื่อนยาวกว่านี้มาก)
    1280x900  แอดมินปิดอัดสด -> เหลือ bot(เลือก), upload
              เหลือทางเดียว -> แถบซ่อน · การ์ดได้ padding 16px, border 1px, h3 กลับมา
    375x812   เหมือนเดิมทุกอย่าง คำอธิบายยังถูกซ่อน
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")

NARROW_AT = "@media (max-width: 860px) {"


def narrow_block() -> str:
    """เนื้อใน @media (max-width: 860px) — จับคู่วงเล็บปีกกาเอา ไม่เดาด้วย regex."""
    i = CSS.index(NARROW_AT)
    depth = 0
    for j in range(i + len(NARROW_AT) - 1, len(CSS)):
        if CSS[j] == "{":
            depth += 1
        elif CSS[j] == "}":
            depth -= 1
            if depth == 0:
                return CSS[i:j + 1]
    raise AssertionError("หาปลายของ media query ไม่เจอ")


def global_css() -> str:
    b = narrow_block()
    return CSS.replace(b, "")


class TestTheStripIsNoLongerMobileOnly(unittest.TestCase):

    def test_the_picker_does_not_branch_on_screen_width(self):
        start = APP_JS.index("function setupCapturePicker()")
        body = APP_JS[start:APP_JS.index("function setupAdvSummary()")]
        self.assertNotIn("isMobile()", body,
                         "ยังแยกทางตามความกว้างจออยู่ — จอกว้างจะไม่ได้แท็บ")

    def test_the_desktop_rule_no_longer_hides_every_seg(self):
        m = re.search(r"^\.mobilebar,[^\n]*\{ display: none; \}$", CSS, re.M)
        self.assertIsNotNone(m, "หาแถวที่ซ่อนของเฉพาะจอแคบไม่เจอ")
        self.assertNotIn(".seg,", m.group(0),
                         "ยังซ่อน .seg บนจอกว้าง แท็บจะไม่โผล่")

    def test_the_strip_styling_is_global(self):
        g = global_css()
        for rule in (".seg:not([hidden]) {", ".seg-btn {", ".seg-btn.is-on {"):
            with self.subTest(rule=rule):
                self.assertIn(rule, g)

    def test_the_heading_shows_on_both_widths(self):
        self.assertIn("#cap-label:not([hidden]) {", global_css())

    def test_the_floating_record_bar_rule_is_global(self):
        # เดิมอยู่ในกรอบจอแคบ จอกว้างจึงเห็นปุ่ม "เริ่มอัด" ค้างอยู่ทุกแท็บ
        self.assertIn("body:not(.cap-rec) .floatbar { display: none; }", global_css())


class TestALoneCardStillLooksLikeACard(unittest.TestCase):
    """กฎถอดกรอบต้องผูกกับ "มีแถบแท็บ" ไม่ใช่กับความกว้างจอ."""

    def test_the_frame_stripping_is_scoped_to_a_visible_strip(self):
        g = global_css()
        self.assertIn(".seg:not([hidden]) ~ .cards .card {", g)
        self.assertIn(".seg:not([hidden]) ~ .cards .card > h3 { display: none; }", g)

    def test_the_grid_gap_only_collapses_when_the_strip_is_up(self):
        # `.cards { gap: 0 }` แบบไม่มีเงื่อนไขจะทำให้การ์ดสองใบติดกันตอนแถบซ่อน
        self.assertIn(".seg:not([hidden]) ~ .cards { gap: 0; }", global_css())
        self.assertNotRegex(global_css(), r"(?m)^\.cards \{ gap: 0; \}$")


class TestWhatDeliberatelyDidNotChange(unittest.TestCase):

    def test_the_detail_view_tabs_are_still_mobile_only(self):
        start = APP_JS.index("function setupDetailTabs()")
        body = APP_JS[start:APP_JS.index("function setupCapturePicker()")]
        self.assertIn("if (!isMobile())", body,
                      "เจ้าของขอเฉพาะฟอร์มประชุมใหม่ ไม่ใช่ทั้งเว็บ")

    def test_the_card_blurb_is_still_hidden_only_on_narrow_screens(self):
        rule = ".seg:not([hidden]) ~ .cards .card > p.muted { display: none; }"
        self.assertIn(rule, narrow_block())
        self.assertNotIn(rule, global_css(), "จอกว้างมีที่พอ ข้อความนั้นมีประโยชน์")

    def test_the_narrow_block_still_exists(self):
        self.assertIn(NARROW_AT, CSS)


if __name__ == "__main__":
    unittest.main()
