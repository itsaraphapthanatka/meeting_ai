"""BACKLOG #78 — ชื่อการประชุมเป็นช่องบังคับ.

เจ้าของขอ (2026-09-20) เดิมเว้นว่างได้แล้วระบบตั้งชื่อให้เอง (`อัดสด 19/9/2569 23:46`
หรือชื่อไฟล์ที่อัปมา) คลังจึงเต็มไปด้วยชื่อที่ค้นไม่เจอภายหลัง

**เรื่องจังหวะสำคัญกว่าตัวเช็ค**: ต้องดัก **ก่อนเริ่มงาน** ไม่ใช่ตอนส่ง — คนที่อัดประชุม
ไปแล้วสี่สิบนาทีแล้วเพิ่งรู้ว่าลืมใส่ชื่อ คือประสบการณ์ที่แย่กว่าการไม่มีตัวเช็คเสียอีก
ไฟล์นี้จึงตรวจ *ลำดับ* ของทุกเส้น ไม่ใช่แค่ว่ามีการเรียก `requireTitle()`

และ `submitMeeting()` ยังเก็บ `fallbackTitle` ไว้เป็นตาข่ายกันตก ถ้าเส้นไหนหลุดมาได้
การทิ้งไฟล์ที่อัดมาสี่สิบนาทีแย่กว่าการตั้งชื่อให้เองมาก

วัดกับเบราว์เซอร์จริงที่ 375x812 (เซิร์ฟเวอร์จริงจาก git worktree พอร์ต 8791)
ดัก `#f-file.click`, `getUserMedia` และ `window.fetch` เพื่อดูว่า "ไปต่อ" จริงไหม:

    กดเลือกไฟล์ ชื่อว่าง       แบนเนอร์ขึ้น · โฟกัสกลับช่องชื่อ · เปิด dialog 0 ครั้ง
    แตะกล่องลากวาง ชื่อว่าง    เหมือนกัน
    ลากไฟล์มาวาง ชื่อว่าง      แบนเนอร์ขึ้น · ยิง /api/meetings 0 ครั้ง
    กดเริ่มอัด ชื่อว่าง         แบนเนอร์ขึ้น · ขอสิทธิ์ไมค์ 0 ครั้ง · แถบอัดไม่โผล่
    ส่งบอท ชื่อว่าง            แบนเนอร์ขึ้น · ยิง /api/meetings/bot 0 ครั้ง · ปุ่มไม่ค้าง disabled
    ชื่อเป็นช่องว่างล้วน        ถูกปฏิเสธเหมือนว่าง
    มีชื่อแล้ว                 เปิด dialog 1 ครั้ง / ยิง API 1 ครั้ง (ไปต่อจริง)
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


def body_of(sig: str) -> str:
    """เนื้อฟังก์ชันตั้งแต่บรรทัด def จนถึงฟังก์ชันถัดไป — พอสำหรับตรวจลำดับ."""
    start = APP_JS.index(sig)
    nl = chr(10)
    nxt = APP_JS.find(nl + "async function ", start + 1)
    other = APP_JS.find(nl + "function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestTheHelper(unittest.TestCase):

    def test_it_exists(self):
        self.assertIn("function requireTitle()", APP_JS)

    def test_it_trims_before_judging(self):
        # ชื่อที่เป็นช่องว่างล้วนต้องไม่ผ่าน
        b = body_of("function requireTitle()")
        self.assertRegex(b, r"\(el\?\.value \|\| ''\)\.trim\(\)")

    def test_it_tells_the_user_and_puts_the_cursor_back(self):
        b = body_of("function requireTitle()")
        self.assertIn("banner('ใส่ชื่อการประชุมก่อน')", b)
        self.assertIn(".focus()", b)

    def test_it_returns_null_so_callers_must_stop(self):
        self.assertIn("return null;", body_of("function requireTitle()"))


class TestEveryEntryPointChecksBeforeStarting(unittest.TestCase):
    """ลำดับคือหัวใจ — เช็คตอนจบงานเท่ากับไม่มีตัวเช็ค."""

    def test_recording_checks_before_asking_for_the_microphone(self):
        b = body_of("async function startRecording()")
        self.assertIn("if (!requireTitle()) return;", b)
        got = b.index("requireTitle()")
        for later in ("getUserMedia", "recMode()"):
            with self.subTest(later=later):
                if later in b:
                    self.assertLess(got, b.index(later),
                                    f"เช็คทีหลัง {later} = สายไป")

    def test_sending_a_bot_checks_before_touching_the_link_or_the_api(self):
        b = body_of("async function sendBot()")
        got = b.index("requireTitle()")
        self.assertLess(got, b.index("'#b-url'"))
        self.assertLess(got, b.index("/api/meetings/bot"))

    def test_sending_a_bot_checks_before_disabling_its_button(self):
        # ไม่งั้นปุ่มค้าง disabled ทั้งที่ยังไม่ได้ส่งอะไรเลย
        b = body_of("async function sendBot()")
        self.assertLess(b.index("requireTitle()"), b.index("btn.disabled = true"))

    def test_uploading_checks_before_submitting(self):
        b = body_of("async function uploadFile(file)")
        self.assertLess(b.index("requireTitle()"), b.index("submitMeeting("))

    def test_the_file_picker_does_not_even_open(self):
        # เปิด dialog ให้เลือกไฟล์แล้วค่อยบอกว่าลืมใส่ชื่อ = เสียเวลาเปล่า
        self.assertIn("$('#btn-pick').onclick = () => { if (requireTitle()) $('#f-file').click(); };",
                      APP_JS)

    def test_tapping_the_drop_zone_is_guarded_too(self):
        self.assertIn("if (requireTitle()) $('#f-file').click();", APP_JS)

    def test_dropping_a_file_is_guarded_by_the_function_itself(self):
        # ลากมาวางไม่ผ่านปุ่มหรือกล่องที่ดักไว้ ต้องพึ่งด่านใน uploadFile
        self.assertIn("if (!requireTitle()) return;", body_of("async function uploadFile(file)"))

    def test_all_three_ways_in_are_covered(self):
        for sig in ("async function startRecording()", "async function sendBot()",
                    "async function uploadFile(file)"):
            with self.subTest(sig=sig):
                self.assertIn("requireTitle()", body_of(sig))


class TestTheSafetyNetStays(unittest.TestCase):
    """ถ้ามีเส้นไหนหลุดด่านมาได้ ห้ามทิ้งสิ่งที่อัดมาแล้ว."""

    def test_submit_still_falls_back_to_a_generated_title(self):
        self.assertIn("title: v.title || fallbackTitle,", APP_JS)

    def test_both_callers_still_pass_one(self):
        self.assertEqual(APP_JS.count("fallbackTitle:"), 2)


class TestTheFieldSaysSoOnScreen(unittest.TestCase):
    """ถ้าไม่บอกไว้ล่วงหน้า ผู้ใช้จะรู้ก็ต่อเมื่อโดนปฏิเสธ."""

    def test_the_input_is_marked_required(self):
        m = re.search(r'<input id="f-title"[^>]*>', HTML, re.S)
        self.assertIsNotNone(m)
        self.assertIn("required", m.group(0))
        self.assertIn('aria-required="true"', m.group(0))

    def test_the_label_shows_it(self):
        self.assertIn('<span>ชื่อการประชุม <b class="req">จำเป็น</b></span>', HTML)

    def test_the_marker_has_a_style_of_its_own(self):
        self.assertRegex(CSS, r"\.req \{[^}]*var\(--warn\)")


if __name__ == "__main__":
    unittest.main()
