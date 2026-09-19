"""BUG-071 — ส่งบอทแล้วหน้ายังเป็นฟอร์มเดิม ดูเหมือนยังไม่ได้ส่ง.

เจ้าของส่งภาพหน้าจอมา (2026-09-19): บอทเข้าห้องแล้วและนับเวลาอยู่ใน log แต่หน้าเว็บบนมือถือ
ยังเป็นฟอร์ม "ประชุมใหม่" ที่ช่องลิงก์ถูกล้าง เห็นแค่แบนเนอร์บาง ๆ ด้านบน — อ่านได้ว่า
"ยังไม่ได้ส่ง" แล้วผู้ใช้กดส่งซ้ำ ทั้งที่บอทอยู่ในห้องแล้ว

เหตุ: หลังส่งสำเร็จ โค้ดใส่งานเข้า `state.jobs` แล้ว `renderJobs()` — แต่การ์ดงานอยู่ใน
`.sidebar` ซึ่ง CSS ของจอแคบซ่อนทั้งแถบตอน `body[data-view="new"]` ผู้ใช้จึงไม่มีทางเห็น
สถานะ "บอทอยู่ในห้อง mm:ss" เลย

วัดกับเบราว์เซอร์จริงที่ความกว้างมือถือ:

    view=new   -> .sidebar display:none  · การ์ดงานมองเห็น = false
    view=home  -> .sidebar display:flex  · การ์ดงานมองเห็น = true

แก้: หลังส่งบอทสำเร็จให้ `showNew('#home')` — ไปหน้ารายการที่การ์ดงานมองเห็นได้
(บนเดสก์ท็อปฟอร์มยังอยู่ครบ ไม่เสียอะไร)
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "meeting_ai" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def _bot_submit_block() -> str:
    start = APP_JS.index("const job = await api('/api/meetings/bot'")
    end = APP_JS.index("} catch (e) {", start)
    return APP_JS[start:end]


class TestSendingABotLeavesTheForm(unittest.TestCase):

    def test_it_navigates_to_the_list(self):
        self.assertIn("showNew('#home')", _bot_submit_block(),
                      "ยังค้างอยู่ที่ฟอร์มเดิม — ผู้ใช้จะกดส่งซ้ำ")

    def test_the_job_is_rendered_before_navigating(self):
        block = _bot_submit_block()
        self.assertLess(block.index("renderJobs()"), block.index("showNew('#home')"),
                        "ไปหน้ารายการก่อนใส่การ์ดงาน = เห็นรายการว่างหนึ่งจังหวะ")

    def test_polling_is_started(self):
        # ไม่งั้นการ์ดค้างที่ 2% ไม่ขยับ ซึ่งก็ดูเหมือนพังอีกแบบ
        self.assertIn("ensurePolling()", _bot_submit_block())

    def test_it_no_longer_clears_the_field_by_hand(self):
        # showNew() สร้างฟอร์มใหม่ให้อยู่แล้ว การล้างเองคือร่องรอยของพฤติกรรมเดิม
        self.assertNotIn("$('#b-url').value = ''", _bot_submit_block())

    def test_the_banner_still_explains_the_admit_step(self):
        self.assertIn("Admit", _bot_submit_block())


class TestWhyItMattered(unittest.TestCase):
    """ปักหมุดเหตุผล — ถ้าจอแคบเลิกซ่อน sidebar แล้ว เหตุของบั๊กนี้ก็หายไป."""

    def test_narrow_screens_hide_the_sidebar_on_the_new_view(self):
        self.assertRegex(
            STYLE,
            r'body\[data-view="new"\] \.sidebar,\s*\n\s*body\[data-view="meeting"\] \.sidebar'
            r' \{ display: none; \}')

    def test_the_job_cards_live_in_that_sidebar(self):
        index = (ROOT / "meeting_ai" / "web" / "static" / "index.html").read_text(
            encoding="utf-8")
        sidebar = index[index.index('class="sidebar"'):]
        sidebar = sidebar[:sidebar.index("</aside>")] if "</aside>" in sidebar else sidebar
        self.assertIn('id="jobs"', sidebar,
                      "การ์ดงานย้ายออกจาก sidebar แล้ว — ทบทวนว่าบั๊กนี้ยังมีอยู่ไหม")

    def test_the_home_view_does_not_hide_it(self):
        self.assertNotRegex(STYLE, r'body\[data-view="home"\][^\n]*\.sidebar[^\n]*none')


if __name__ == "__main__":
    unittest.main()
