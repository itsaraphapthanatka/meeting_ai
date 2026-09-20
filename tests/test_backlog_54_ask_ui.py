"""BACKLOG #54 ขั้นที่ 2 — หน้าเว็บของถาม-ตอบ (ADR-002).

แท็บ "ถาม" ในหน้ารายละเอียด: ช่องพิมพ์ + รายการคำตอบที่เก็บไว้ (ใหม่สุดอยู่บน)

วัดกับเบราว์เซอร์จริงที่ 375x812 บนเซิร์ฟเวอร์จริงจาก git worktree **พร้อม LLM ปลอม**
เพื่อเดินเส้นเต็มจริง ๆ ไม่ใช่แค่ตรวจว่าปุ่มอยู่ตรงไหน:

    แท็บสี่ใบกว้างเท่ากัน 83px ไม่มีใบไหนล้น (แถบกว้าง 343px)
    พิมพ์คำถาม -> กดถาม -> ยิง /ask 1 ครั้ง · ช่องถูกล้าง · การ์ดงานโผล่
    รอ ~2 วินาที คำตอบขึ้นเอง โดยไม่ต้องรีเฟรช (pollJobs โหลดการประชุมใหม่ให้)
    คำตอบเรนเดอร์ Markdown จริง (**ตัวหนา** กลายเป็น <strong>)
    คำตอบที่โมเดลบอกว่าข้อมูลไม่พอ มีแถบเตือนสีส้มแยกออกมา
    กดลบคำถามเก่า -> หายทั้งบนหน้าจอและที่เซิร์ฟเวอร์

**บั๊กสองตัวที่เจอเพราะกดใช้จริง ไม่ใช่เพราะเทสต์** (ทั้งคู่มีเทสต์คุมแล้ว):

1. **คำถามหายในโหมดไฟล์** — `_enqueue()` โหมดไฟล์เก็บงานในหน่วยความจำและไม่มีคีย์
   `_spec` เลย ส่งแค่ `spec=` จึงได้คำถามว่างที่ `build_spec()` งานตายทันทีด้วย
   "งานนี้ไม่มีคำถามใน spec" ทั้งที่ผู้ใช้พิมพ์มาแล้ว (ดู `test_backlog_54_ask_backend`)
2. **ถามแล้วโดนเด้งออกจากแท็บ** — งานจบ `pollJobs()` เรียก `openMeeting()` ซึ่ง
   `setupDetailTabs()` เด้งกลับไปแท็บ "สรุป" เสมอ = พาผู้ใช้ออกจากที่ที่คำตอบอยู่พอดี
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


class TestTheTab(unittest.TestCase):

    def test_it_is_in_the_strip_between_actions_and_transcript(self):
        seg = HTML[HTML.index('id="d-seg"'):]
        seg = seg[:seg.index("</div>")]
        self.assertEqual(re.findall(r'data-tab="([a-z]+)"', seg),
                         ["summary", "actions", "ask", "transcript"])

    def test_the_pane_has_an_input_and_a_list(self):
        self.assertIn('<div class="dtab" data-tab="ask">', HTML)
        self.assertIn('id="q-input"', HTML)
        self.assertIn('id="q-send"', HTML)
        self.assertIn('id="d-qa"', HTML)

    def test_the_input_is_capped_in_the_markup_too(self):
        # เซิร์ฟเวอร์ตัดที่ MAX_QUESTION อยู่แล้ว แต่บอกผู้ใช้ตั้งแต่ตอนพิมพ์ดีกว่าตัดเงียบ ๆ
        m = re.search(r'<textarea id="q-input"[^>]*>', HTML, re.S)
        self.assertIsNotNone(m)
        self.assertIn('maxlength="500"', m.group(0))

    def test_it_says_where_the_answer_comes_from(self):
        # ตอบจากสรุปเท่านั้น (ADR-002 ชั้นที่ 1) — ถ้าไม่บอก คนจะคิดว่ามันอ่านบทถอดเสียงให้
        self.assertIn("ตอบจากสรุปของการประชุมนี้เท่านั้น", HTML)


class TestTheList(unittest.TestCase):

    def test_newest_first(self):
        self.assertIn(".slice().reverse()", fn("function renderQa()"))

    def test_everything_from_the_user_is_escaped(self):
        body = fn("function qaItem(row)")
        self.assertIn("esc(row.question)", body)
        self.assertIn("esc(row.id)", body)

    def test_the_answer_goes_through_the_markdown_renderer(self):
        # renderMarkdown() escape ให้แล้ว และคำตอบมี bullet/ตัวหนาเป็นปกติ
        self.assertIn("renderMarkdown(row.answer", fn("function qaItem(row)"))

    def test_a_thin_answer_is_flagged(self):
        body = fn("function qaItem(row)")
        self.assertIn("row.enough === false", body)
        self.assertIn("ข้อมูลในสรุปไม่พอ", body)
        self.assertRegex(CSS, r"\.qa-thin \{[^}]*var\(--warn\)")

    def test_an_empty_list_says_so(self):
        self.assertIn("ยังไม่มีคำถาม", fn("function renderQa()"))


class TestAskingIsBlockedWithoutASummary(unittest.TestCase):
    """เส้น API ตอบ 409 อยู่แล้ว แต่บอกตั้งแต่ก่อนกดดีกว่าให้กดแล้วเจอ error."""

    def test_the_button_is_disabled(self):
        body = fn("function renderQa()")
        self.assertIn("state.meeting.summary", body)
        self.assertIn("$('#q-send').disabled = !ready", body)

    def test_a_note_explains_why(self):
        self.assertIn("ยังไม่มีสรุป", fn("function renderQa()"))


class TestSending(unittest.TestCase):

    def test_it_posts_the_question(self):
        body = fn("async function sendQuestion()")
        self.assertIn("/ask", body)
        self.assertIn("jsonPost({ question })", body)

    def test_it_trims_and_refuses_empty(self):
        body = fn("async function sendQuestion()")
        self.assertIn(".trim()", body)
        self.assertIn("if (!question)", body)

    def test_the_job_card_shows_up_immediately(self):
        # คำตอบมาทีหลัง ถ้าไม่มีอะไรขยับเลยคนจะกดซ้ำ (บทเรียนเดียวกับ BUG-071)
        body = fn("async function sendQuestion()")
        self.assertIn("renderJobs()", body)
        self.assertIn("ensurePolling()", body)

    def test_the_button_is_released_even_on_failure(self):
        body = fn("async function sendQuestion()")
        self.assertIn("finally", body)
        self.assertIn("btn.disabled = false", body)

    def test_ctrl_enter_sends_but_plain_enter_does_not(self):
        # คำถามยาวหลายบรรทัดมีจริง Enter เปล่า ๆ ต้องขึ้นบรรทัดใหม่ได้
        body = fn("function setupQa()")
        self.assertIn("e.ctrlKey || e.metaKey", body)


class TestTheAnswerArrivesWithoutLeavingTheTab(unittest.TestCase):
    """บั๊กที่เจอตอนกดใช้จริง — ถามแล้วโดนเด้งกลับไปหน้าสรุปพอดีตอนคำตอบมาถึง."""

    def test_an_ask_job_does_not_navigate(self):
        body = APP_JS[APP_JS.index("async function pollJobs()"):]
        body = body[:body.index("async function refreshWorkers")] if "async function refreshWorkers" in body else body[:4000]
        self.assertIn("job.kind === 'ask'", body)
        self.assertIn("const quiet =", body)

    def test_reopening_the_same_meeting_keeps_the_tab(self):
        body = fn("function setupDetailTabs()")
        self.assertIn("if (!sameMeeting) state.detailTab = 'summary';", APP_JS)
        self.assertIn("state.detailTab = tab;", body)          # จำไว้ตอนกด
        # ...และต้อง **อ่านกลับมาใช้** ด้วย เวอร์ชันแรกเทสต์แต่ขาเขียน มุตันต์ที่เปลี่ยน
        # บรรทัดนี้เป็น 'summary' ตายตัวจึงรอดไปได้ ทั้งที่แท็บเด้งกลับทุกครั้งที่โหลดซ้ำ
        self.assertIn("state.detailTab || 'summary'", body)

    def test_a_tab_that_disappeared_falls_back_to_the_summary(self):
        # แท็บ "สิ่งที่ต้องทำ" หายได้เมื่อสรุปใหม่ไม่มีตาราง — อย่าค้างที่แท็บที่ไม่มีอยู่
        body = fn("function setupDetailTabs()")
        self.assertIn("btns.some((b) => b.dataset.tab === want) ? want : 'summary'", body)


class TestDeleting(unittest.TestCase):

    def test_it_calls_delete_with_the_row_id(self):
        body = fn("function setupQa()")
        self.assertIn("method: 'DELETE'", body)
        self.assertIn("/qa/${id}", body)

    def test_it_renders_what_the_server_returned(self):
        self.assertIn("state.meeting.qa = out.qa", fn("function setupQa()"))


if __name__ == "__main__":
    unittest.main()
