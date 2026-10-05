"""ADR-004 — คำแนะนำเรื่อง Zoom ต้องไม่ชี้ไปทางตัน.

ADR-004 สรุปว่า **ไม่ทำทั้ง Meeting SDK และ RTMS** และสั่งให้ถอดคำแนะนำเดิมที่บอกผู้ใช้ว่า
"ล็อกอินบัญชี Zoom ให้บอท" ออกจากทุกที่ที่ผู้ใช้อ่าน เหตุผลคือ

* หน้าเอกสารของ Zoom เองบอกให้ใช้ RTMS แทน Meeting SDK สำหรับงาน AI notetaker
* ตั้งแต่ 2 มี.ค. 2026 แอปที่เข้าห้องซึ่ง host เป็นบัญชีภายนอกต้องมี OBF token
  ซึ่งต้องมีผู้ใช้จริงที่อนุญาตแอปผ่าน OAuth **และอยู่ในห้องนั้นอยู่แล้ว**
* ถึงล็อกอินผ่านก็ยังเป็น "automated client" ที่ Zoom ประกาศว่าไม่ต้องการ

คำแนะนำนั้นเคยอยู่สองที่: `README.md` และ **ข้อความที่ผู้ใช้เห็นจริงใน `bot/platforms.py`**
ที่สองสำคัญกว่า เพราะมันโผล่ตอนที่ผู้ใช้เพิ่งโดนปฏิเสธและกำลังหาทางต่อ

เทสต์ชุดนี้ไม่ได้ยืนยันข้อเท็จจริงของ Zoom (ยืนยันจากในนี้ไม่ได้ — ดู ADR ว่าอ้างอิงที่ไหน)
แต่กันไม่ให้คำแนะนำที่ถอดออกแล้วเดินกลับเข้ามา และกันเอกสารขัดกันเอง
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr" / "ADR-004-zoom-meeting-sdk.md"
README = (ROOT / "README.md").read_text(encoding="utf-8")
PLATFORMS = (ROOT / "bot" / "platforms.py").read_text(encoding="utf-8")
HELP = (ROOT / "meeting_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")

ZOOM_SECTION = README[README.index("#### Zoom ปฏิเสธบอท"):]
ZOOM_SECTION = ZOOM_SECTION[:ZOOM_SECTION.index("เตรียมเครื่องที่จะรันบอท")]
# ส่วนที่เป็น "คำแนะนำ" จบตรงที่ blockquote แรก — ย่อหน้าหลังจากนั้นคือบันทึกว่า**ถอดอะไรออกไป
# และทำไม** ซึ่งต้องเอ่ยถึงคำสั่งเดิมได้ ไม่งั้นบันทึกก็เขียนไม่ได้ว่าถอดอะไร
ZOOM_ADVICE = ZOOM_SECTION.split(chr(10) + "> ")[0]


def strings_in(src: str) -> list[str]:
    """ข้อความใน literal ของไฟล์ Python — คือสิ่งที่ผู้ใช้มีโอกาสได้อ่าน."""
    return re.findall(r'"([^"\\]{4,})"', src) + re.findall(r"'([^'\\]{4,})'", src)


class TestTheDecisionIsWrittenDown(unittest.TestCase):

    def test_the_adr_exists(self):
        self.assertTrue(ADR.exists(), "ADR-004 หายไป")

    def test_it_states_a_verdict_not_just_options(self):
        text = ADR.read_text(encoding="utf-8")
        head = text[:text.index("---")]
        self.assertIn("ประเมินแล้ว", head)
        self.assertRegex(head, r"ยังไม่ทำ", "หัว ADR ต้องบอกผลตัดสิน ไม่ใช่แค่สรุปทางเลือก")

    def test_it_says_when_to_look_again(self):
        """ADR ที่ไม่บอกเงื่อนไขทบทวน จะกลายเป็นคำว่า 'ไม่' ถาวรโดยไม่มีใครกล้ารื้อ."""
        text = ADR.read_text(encoding="utf-8")
        self.assertIn("กลับมาดูใหม่เมื่อไหร่", text)
        tail = text[text.index("กลับมาดูใหม่เมื่อไหร่"):]
        self.assertGreaterEqual(tail.count("\n- ") + tail.count("\n* "), 3,
                                "ต้องมีเงื่อนไขทบทวนอย่างน้อยสามข้อ")

    def test_it_admits_what_is_still_unverified(self):
        text = ADR.read_text(encoding="utf-8")
        self.assertIn("ยังยืนยันไม่ได้", text,
                      "ADR ต้องแยกของที่ตรวจมาแล้วออกจากของที่ยังเดา")


class TestTheRetiredAdviceIsGoneEverywhere(unittest.TestCase):
    """คำแนะนำที่พาไปทางตันแย่กว่าไม่พูดอะไรเลย — คนจะไปเสียเวลาแล้วกลับมาไม่เชื่ออย่างอื่นด้วย."""

    def test_the_message_the_user_actually_sees_when_zoom_blocks_the_bot(self):
        block = PLATFORMS[PLATFORMS.index("ZOOM_BOT_BLOCK, timeout"):]
        block = block[:block.index("elif")]
        said = " ".join(strings_in(block))
        self.assertNotIn("bot-login", said, "ยังชี้ให้ผู้ใช้ไปล็อกอินบัญชี Zoom ให้บอท")
        self.assertRegex(said, r"อัปโหลด|อัดสด",
                         "ถอดทางเดิมออกแล้วต้องเหลือทางที่ใช้ได้จริงไว้ให้ ไม่ใช่ปล่อยค้าง")

    def test_the_readme_no_longer_lists_it(self):
        self.assertNotIn("bot-login --site zoom", ZOOM_ADVICE)
        self.assertNotIn("ล็อกอินบัญชี Zoom ให้บอท", ZOOM_ADVICE)
        # แต่ต้องยังบันทึกไว้ว่าถอดอะไรออกไป ไม่งั้นอีกหกเดือนมีคนเติมกลับเข้ามาด้วยความหวังดี
        self.assertIn("ถอดออกแล้ว", ZOOM_SECTION)

    def test_the_readme_points_at_the_adr_instead(self):
        self.assertIn("ADR-004", ZOOM_SECTION)
        self.assertIn("docs/adr/ADR-004-zoom-meeting-sdk.md", ZOOM_SECTION)

    def test_the_readme_still_keeps_the_two_ways_that_work(self):
        for way in ("Local/Cloud Recording", "ไมค์ + เสียงในเครื่อง"):
            with self.subTest(way=way):
                self.assertIn(way, ZOOM_SECTION)


class TestTheDocsDoNotContradictEachOther(unittest.TestCase):

    def test_the_readme_quotes_the_same_figures_as_the_adr(self):
        text = ADR.read_text(encoding="utf-8")
        for figure in ("$0.01", "$100", "2 มี.ค. 2026"):
            with self.subTest(figure=figure):
                self.assertIn(figure, text)
                self.assertIn(figure, ZOOM_SECTION,
                              "README อ้างตัวเลขคนละชุดกับ ADR")

    def test_the_in_app_manual_gives_the_same_two_ways_out(self):
        """คู่มือในเว็บเป็นที่ที่ผู้ใช้อ่านจริงที่สุด ต้องไม่หลงเหลือทางที่ถอดไปแล้ว."""
        help_tpl = HELP[HELP.index('<template id="tpl-help">'):]
        help_tpl = help_tpl[:help_tpl.index("</template>")]
        self.assertNotIn("bot-login", help_tpl)
        self.assertIn("อัปโหลดไฟล์", help_tpl)
        self.assertIn("อัดสด", help_tpl)

    def test_nothing_promises_zoom_bot_support(self):
        """`BOT_HOSTS` ยังรับลิงก์ Zoom อยู่ (เพื่อให้รายงานสาเหตุได้) — แต่ห้ามโฆษณาว่าเข้าได้."""
        self.assertIn("zoom.us", (ROOT / "meeting_ai" / "web" / "server.py")
                      .read_text(encoding="utf-8"))
        row = next(l for l in README.split("\n") if l.startswith("| Zoom |"))
        self.assertIn("บล็อก", row, "ตารางใน README ต้องบอกตรง ๆ ว่า Zoom บล็อกบอท")


if __name__ == "__main__":
    unittest.main()
