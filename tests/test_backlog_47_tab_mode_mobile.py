"""BACKLOG #47 — ตัวเลือก "แชร์แท็บ" ต้องหายไปบนจอแคบ และต้องตามความกว้างที่เปลี่ยนระหว่างใช้งาน.

ตั๋วชี้สองอย่าง: เช็คแค่ feature-detect `getDisplayMedia` (พลาดกรณี Chrome เดสก์ท็อปจำลอง
จอมือถือ หรือ Chrome เต็มตัวบนแท็บเล็ตที่มี API นี้จริงแต่ผู้ใช้แชร์แท็บไม่ได้อย่างมีความหมาย)
และตั้งแค่ `disabled` ไม่ได้ตั้ง `hidden` — ตัวเลือกที่กดไม่ได้แต่ยังกินที่อยู่บนจอเล็ก

ที่ยังเหลือหลังรอบแก้หน้าตา: `updateTabModeAvailability()` ถูกเรียกครั้งเดียวตอน
`setupSources()` ความกว้างเปลี่ยนระหว่างใช้งานได้จริง (หมุนแท็บเล็ต ย่อหน้าต่าง สลับโหมด
อุปกรณ์ใน DevTools ซึ่งเป็นกรณีที่ตั๋วยกมาเอง) ตัวเลือกจึงค้างตามจอตอนโหลดหน้า

ยืนยันในเบราว์เซอร์จริงแล้ว (Chromium, โคลน tpl-new เข้า DOM):
  1024px -> display: flex, กดได้, เลือก "tab" ได้ (elementFromPoint ตกในป้ายนั้นจริง)
  ย่อเป็น 390px -> change ยิง, hidden, display: none, rect 0x0, disabled, สลับกลับเป็น room
เทสต์ไฟล์นี้อ่านซอร์สเพื่อกันการถอยกลับ ไม่ได้แทนการดูของจริง
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static"
APP_JS = STATIC / "app.js"
STYLE = STATIC / "style.css"
INDEX = STATIC / "index.html"


class TabModeCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.css = STYLE.read_text(encoding="utf-8")
        cls.html = INDEX.read_text(encoding="utf-8")
        m = re.search(r"function updateTabModeAvailability\(\) \{(.*?)\n\}", cls.js, re.S)
        assert m, "หา updateTabModeAvailability ไม่เจอ — ตัวตรวจพัง ไม่ใช่โค้ดดี"
        cls.fn = m.group(1)


class TestTheOptionExistsToBeHidden(TabModeCase):

    def test_the_tab_mode_is_still_in_the_markup(self):
        # ถ้าวันหนึ่งลบตัวเลือกนี้ทิ้ง เทสต์ที่เหลือจะผ่านแบบว่างเปล่าทั้งไฟล์
        self.assertIn('value="tab"', self.html)

    def test_hidden_beats_our_own_display_rules(self):
        # .mode ตั้ง display: flex ซึ่งชนะ [hidden] ของ UA stylesheet ถ้าไม่มีกฎนี้
        # ตัวเลือกจะยัง "โผล่" อยู่ทั้งที่โค้ดสั่งซ่อนแล้ว (ยืนยันในเบราว์เซอร์: display: none)
        self.assertIn("[hidden] { display: none !important; }", self.css)


class TestItHidesNotJustDisables(TabModeCase):

    def test_it_sets_hidden_on_the_whole_option(self):
        self.assertIn("mode.hidden = hide", self.fn)

    def test_it_also_disables_the_input(self):
        # ซ่อนอย่างเดียวไม่พอ: ปุ่มที่ซ่อนแต่ยัง enabled ยังถูกโฟกัสด้วยคีย์บอร์ดได้ในบางเบราว์เซอร์
        self.assertIn("tab.disabled = hide", self.fn)

    def test_a_selected_tab_mode_falls_back_to_something_usable(self):
        self.assertIn("hide && tab.checked", self.fn)
        self.assertIn("'#rec-modes input[value=\"room\"]'", self.fn)

    def test_the_fallback_is_remembered(self):
        # ไม่จำ = ครั้งหน้าโหลดโหมดที่ซ่อนอยู่กลับมาอีก แล้วผู้ใช้กดอัดไม่ได้โดยไม่รู้สาเหตุ
        self.assertIn("store.set(REC_MODE_KEY", self.fn)

    def test_the_panel_is_redrawn_after_the_fallback(self):
        # แผงเลือกอุปกรณ์/ข้อความช่วยผูกกับโหมด ไม่วาดใหม่ = ค้างเป็นของโหมดเดิม
        self.assertIn("renderSources()", self.fn)


class TestItFollowsTheViewport(TabModeCase):

    def test_width_is_checked_not_only_feature_detection(self):
        self.assertIn("getDisplayMedia", self.fn)
        self.assertIn("TAB_MODE_Q.matches", self.fn)

    def test_the_breakpoint_matches_a_real_one_in_the_stylesheet(self):
        px = int(re.search(r"const MOBILE_BREAKPOINT_PX = (\d+)", self.js).group(1))
        self.assertIn(f"@media (max-width: {px}px)", self.css,
                      "breakpoint ที่ไม่ตรงกับ CSS = ซ่อนคนละจุดกับที่หน้าตาเปลี่ยน")

    def test_the_query_is_a_single_listenable_object(self):
        # matchMedia() ใหม่ทุกครั้งที่เรียก = ไม่มีอะไรให้ฟัง change ได้
        self.assertRegex(self.js, r"const TAB_MODE_Q = window\.matchMedia\(")
        self.assertNotIn("window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT_PX}px)`).matches",
                         self.fn)

    def test_a_width_change_re_runs_the_check(self):
        self.assertIn("TAB_MODE_Q.addEventListener('change', updateTabModeAvailability)", self.js)

    def test_the_check_still_runs_on_first_paint(self):
        # ฟัง change อย่างเดียวไม่พอ — โหลดหน้าบนมือถือตั้งแต่แรกไม่มี change ให้ฟัง
        setup = re.search(r"function setupSources\(\) \{(.*?)\n\}", self.js, re.S)
        self.assertIsNotNone(setup)
        self.assertIn("updateTabModeAvailability()", setup.group(1))


if __name__ == "__main__":
    unittest.main()
