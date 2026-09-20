"""BACKLOG #53 ขั้นที่ 3 — เช็กลิสต์ Action Items ในหน้าเว็บ.

US-1 (เห็นรายการโดยไม่ต้องอ่านสรุปทั้งฉบับ) ทำเป็น **แท็บที่สาม** ของหน้ารายละเอียด
ต่อจาก #80 ที่เพิ่งทำให้แท็บใช้ได้ทั้งสองจอ · US-2 ติ๊ก · US-3 แก้ผู้รับผิดชอบ
· กลุ่ม "ทำแล้ว แต่ไม่อยู่ในสรุปล่าสุด" พร้อมปุ่มลบ

วัดกับเบราว์เซอร์จริงที่ 375x812 บนเซิร์ฟเวอร์จริงจาก git worktree (seed การประชุมที่มี
สามรายการ ติ๊กหนึ่งอันแล้วทำให้มันหายจากสรุป เพื่อให้เกิด orphan ของจริง):

    แท็บที่เห็น         สรุป | สิ่งที่ต้องทำ | บทถอดเสียง
    กดแท็บ              โดนตัวเอง · พาเนล actions โผล่อันเดียว
    กล่องติ๊ก           20x20 px · hit-test โดนทั้งสามแถว
    ปุ่มผู้รับผิดชอบ     hit-test โดนทั้งสามแถว
    ปุ่มลบ              มีเฉพาะแถว orphan (25x23) · hit-test โดน
    กดติ๊กจริง           หน้าจอขีดฆ่า True · **ยิงไปถึงเซิร์ฟเวอร์จริง done=True**
    กดลบ orphan จริง     เหลือ 2 แถวทั้งบนหน้าจอและที่เซิร์ฟเวอร์ · กลุ่มกำพร้าหายไป
    ไม่มีรายการ          แท็บหายไปเลย เหลือ สรุป | บทถอดเสียง (ไม่ใช่กดแล้วเจอหน้าว่าง)
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestTheThirdTabExists(unittest.TestCase):

    def test_the_button_is_in_the_strip(self):
        seg = HTML[HTML.index('id="d-seg"'):]
        seg = seg[:seg.index("</div>")]
        self.assertIn('data-tab="actions"', seg)

    def test_the_order_reads_summary_actions_transcript(self):
        seg = HTML[HTML.index('id="d-seg"'):]
        seg = seg[:seg.index("</div>")]
        self.assertEqual(re.findall(r'data-tab="([a-z]+)"', seg),
                         ["summary", "actions", "transcript"])

    def test_the_pane_exists_and_holds_the_list(self):
        self.assertIn('<div class="dtab" data-tab="actions">', HTML)
        self.assertIn('id="d-actions"', HTML)

    def test_an_empty_list_hides_the_button(self):
        # แท็บว่างเปล่าคือเสียงรบกวน — สรุปที่ไม่มีตารางมีอยู่จริง
        body = fn("function setupDetailTabs()")
        self.assertIn("action_items || []).length > 0", body)
        self.assertIn('actionsBtn.hidden = !hasItems', body)

    def test_hidden_buttons_are_not_counted_as_tabs(self):
        # ถ้ายังนับปุ่มที่ซ่อนอยู่ pick() จะเลือกแท็บที่มองไม่เห็นได้
        self.assertIn("$$('.seg-btn', seg).filter((b) => !b.hidden)",
                      fn("function setupDetailTabs()"))


class TestTheRowsCarryWhatTheUserNeeds(unittest.TestCase):

    def test_each_row_has_a_checkbox_and_the_item_id(self):
        body = fn("function actionRow(item)")
        self.assertIn('type="checkbox"', body)
        self.assertIn('data-id="${esc(item.id)}"', body)

    def test_done_rows_are_marked(self):
        self.assertIn("is-done", fn("function actionRow(item)"))
        self.assertRegex(CSS, r"\.ai-row\.is-done \.ai-text \{[^}]*line-through")

    def test_the_assignee_is_a_real_button(self):
        # ทำให้ดูเหมือนข้อความได้ แต่ต้องเป็นปุ่มจริงเพื่อให้แท็บ/สกรีนรีดเดอร์ถึงได้
        self.assertIn('<button type="button" class="ai-who">', fn("function actionRow(item)"))

    def test_the_checkbox_is_big_enough_to_hit_on_a_phone(self):
        m = re.search(r'\.ai-row input\[type="checkbox"\] \{([^}]*)\}', CSS)
        self.assertIsNotNone(m)
        self.assertIn("width: 20px", m.group(1))

    def test_everything_user_supplied_is_escaped(self):
        body = fn("function actionRow(item)")
        for field in ("item.text", "item.id"):
            with self.subTest(field=field):
                self.assertIn(f"esc({field})", body)
        self.assertIn("esc(who)", body)


class TestTheOrphanGroupExplainsItself(unittest.TestCase):
    """ระบบไม่ลบงานที่คนติ๊กว่าทำแล้วเอง จึงต้องบอกว่าทำไมมันยังอยู่."""

    def test_orphans_are_split_out(self):
        body = fn("function renderActionItems()")
        self.assertIn("i.orphan", body)
        self.assertIn("ai-orphans", body)

    def test_the_group_says_why(self):
        self.assertIn("ทำแล้ว แต่ไม่อยู่ในสรุปล่าสุด", APP_JS)

    def test_only_orphans_get_a_delete_button(self):
        body = fn("function actionRow(item)")
        i = body.index("item.orphan ?")
        self.assertIn("ai-del", body[i:i + 200],
                      "ปุ่มลบต้องผูกกับเงื่อนไข orphan ไม่ใช่มีทุกแถว")

    def test_there_is_no_delete_for_normal_rows(self):
        # ลบรายการปกติไม่มีความหมาย สรุปใหม่ก็กลับมาอยู่ดี
        self.assertEqual(fn("function actionRow(item)").count("ai-del"), 1)


class TestItTalksToTheApiAndStaysHonest(unittest.TestCase):

    def test_ticking_calls_patch_with_done(self):
        self.assertIn("{ done: cb.checked }", fn("function setupActionItems()"))

    def test_it_uses_the_item_id_not_an_index(self):
        body = fn("async function saveActionItem(id, body)")
        self.assertIn("/action-items/${id}", body)

    def test_a_failed_tick_is_rolled_back_on_screen(self):
        # ปล่อยให้ช่องติ๊กค้างสถานะที่เซิร์ฟเวอร์ไม่รับ = หน้าจอโกหก
        body = fn("function setupActionItems()")
        self.assertIn("cb.checked = !cb.checked", body)

    def test_deleting_uses_delete(self):
        self.assertIn("method: 'DELETE'", fn("function setupActionItems()"))

    def test_the_server_reply_is_what_gets_rendered(self):
        # ไม่เดาผลลัพธ์เอง — เส้น API คืนชุดล่าสุดทั้งก้อนมาให้อยู่แล้ว
        self.assertIn("state.meeting.action_items = out.action_items",
                      fn("async function saveActionItem(id, body)"))

    def test_it_is_rendered_when_a_meeting_opens(self):
        self.assertIn("renderActionItems();", APP_JS)
        self.assertIn("setupActionItems();", APP_JS)


if __name__ == "__main__":
    unittest.main()
