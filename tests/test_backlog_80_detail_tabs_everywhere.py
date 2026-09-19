"""BACKLOG #80 — หน้ารายละเอียดใช้แท็บทั้งสองจอ.

เจ้าของขอ (2026-09-20) ต่อจาก #79 — คราวนี้คือหน้ารายละเอียด (สรุป / บทถอดเสียง)

เหตุผลที่จอแคบแยกแท็บมาตั้งแต่แรกคือ "สรุปกับบทถอดเสียงยาวมากทั้งคู่ วางต่อกันแล้วต้อง
เลื่อนผ่านสรุปทั้งอันกว่าจะถึงบทถอดเสียง" — ซึ่งจริงบนจอกว้างพอ ๆ กัน ความกว้างจอไม่ได้
ทำให้สรุปสั้นลง

วัดกับเบราว์เซอร์จริง 1280x900 บนเซิร์ฟเวอร์จริงจาก git worktree
(seed การประชุมที่มี 8 หัวข้อสรุป + 60 ช่วงพูด ผ่าน `store.create()`):

    กางสองส่วนแบบเดิม   หน้าสูง 2013px
    แบบแท็บ             หน้าสูง 1447px   ลดลง 566px (เหลือ 72%)

    ปุ่ม "สรุป"        x=580  กว้าง 426  กดโดนตัวเอง  พาเนลที่โชว์ ['summary']
    ปุ่ม "บทถอดเสียง"   x=1008 กว้าง 426  กดโดนตัวเอง  พาเนลที่โชว์ ['transcript']
    หัวข้อ h3 ที่ซ้ำกับชื่อปุ่ม ถูกซ่อนทั้งสองอัน (display: none)
    ปุ่มควบคุมในหัวข้อ (`#t-edit`) ยัง hit-test ผ่าน ไม่ได้โดนซ่อนไปกับ h3
    จอ 375x812 เหมือนเดิมทุกอย่าง

**กับดักที่เจอระหว่างทาง** (ไม่ใช่บั๊กของฟีเจอร์นี้ แต่เสียเวลาไปหนึ่งรอบ): `store.create()`
รับ mid อะไรก็ได้ แต่ `_common.ID_RE` บังคับรูปแบบ `[0-9]{8}-[0-9]{6}-[0-9a-f]{6}`
การ seed ด้วย id สวย ๆ อย่าง `demo0001` จึงบันทึกลงคลังได้แต่เปิดไม่ได้ (400 "id ไม่ถูกต้อง")
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
    return CSS.replace(narrow_block(), "")


def detail_tabs_body() -> str:
    start = APP_JS.index("function setupDetailTabs()")
    return APP_JS[start:APP_JS.index("function setupCapturePicker()")]


class TestTheDetailTabsAreNoLongerMobileOnly(unittest.TestCase):

    def test_the_setup_does_not_branch_on_screen_width(self):
        self.assertNotIn("isMobile()", detail_tabs_body(),
                         "ยังแยกทางตามความกว้างจอ — จอกว้างจะไม่ได้แท็บ")

    def test_it_no_longer_strips_tab_off_for_wide_screens(self):
        self.assertNotIn("panes.forEach((p) => p.classList.remove('tab-off'))",
                         detail_tabs_body())

    def test_the_strip_is_always_shown(self):
        self.assertIn("seg.hidden = false;", detail_tabs_body())

    def test_hiding_a_pane_is_a_global_rule(self):
        self.assertIn(".dtab.tab-off { display: none; }", global_css())

    def test_the_duplicated_section_headings_are_hidden_globally(self):
        g = global_css()
        self.assertIn(".dtab > .section-head > h3,", g)
        self.assertIn(".dtab > .section-head > div > h3 { display: none; }", g)

    def test_those_rules_left_the_narrow_only_block(self):
        n = narrow_block()
        self.assertNotIn(".dtab.tab-off { display: none; }", n)
        self.assertNotIn(".dtab > .section-head > div > h3 { display: none; }", n)


class TestTheTabsStillBehave(unittest.TestCase):

    def test_it_opens_on_the_summary(self):
        self.assertIn("pick('summary');", detail_tabs_body())

    def test_each_button_switches_its_own_pane(self):
        b = detail_tabs_body()
        self.assertIn("p.classList.toggle('tab-off', p.dataset.tab !== tab)", b)

    def test_it_marks_the_selected_tab_for_screen_readers(self):
        self.assertIn("aria-selected", detail_tabs_body())


class TestNarrowOnlyLayoutTweaksStay(unittest.TestCase):
    """กฎที่เป็นเรื่องของ "จอแคบ" จริง ๆ ต้องไม่ถูกลากออกมาด้วย."""

    def test_the_head_wrapping_rules_are_still_narrow_only(self):
        n = narrow_block()
        for rule in (".section-head { flex-wrap: wrap; gap: 8px; }",
                     ".detail-head { flex-direction: column;"):
            with self.subTest(rule=rule):
                self.assertIn(rule, n)
                self.assertNotIn(rule, global_css())


if __name__ == "__main__":
    unittest.main()
