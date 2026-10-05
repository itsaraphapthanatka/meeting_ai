"""BACKLOG #90 — เมนูคู่มือการใช้งาน.

คู่มือที่ไม่ตรงกับหน้าจอแย่กว่าไม่มีคู่มือ — คนอ่านจะเชื่อแล้วหาปุ่มที่ไม่มีอยู่จริง
เทสต์ชุดนี้จึง **ตรึงทุกคำที่คู่มืออ้างถึงกับของจริงในโค้ด** ไม่ใช่แค่ตรวจว่าหน้ามันเปิดได้:

* ชื่อแท็บทุกใบที่คู่มือเอ่ยถึง ต้องมีอยู่จริงใน `index.html`
* ปุ่มทุกปุ่มที่คู่มือบอกให้กด ต้องมีอยู่จริง
* ข้อความสถานะที่คู่มือยกมา ต้องตรงกับที่ `app.js` เขียนจริง
* ตัวเลขที่คู่มือสัญญาไว้ (6 วินาที, 30 นาที) ต้องตรงกับค่าคงที่ในโค้ด

ถ้าวันหนึ่งมีคนเปลี่ยนชื่อแท็บหรือย้ายปุ่ม เทสต์จะล้มพร้อมบอกว่าคู่มือต้องแก้ตรงไหน

วัดกับเบราว์เซอร์จริงแล้วทั้งสองขนาด:

    มือถือ 375x812  ปุ่ม ? บน topbar 32x32 · elementFromPoint ได้ตัวมันเอง · กดแล้วไป #help
                    sidebar ถูกซ่อน เหลือคู่มือเต็มจอ · TOC และกล่องเตือน hit-test ผ่าน
    เดสก์ท็อป 1280x860  ปุ่มผ่าน hit-test · sidebar ยังอยู่ · เนื้อหากว้าง 760px · 7 หัวข้อ

**โค้ดตายที่ถอดออกเพราะ hit-test**: ตอนแรกผมเพิ่มปุ่ม '?' ลงแถบมือถือด้วย เพราะวัดครั้งแรก
ได้ว่าปุ่มบน topbar กว้าง 0 สูง 0 — แต่นั่นเป็นการวัดก่อน layout นิ่ง ของจริงคือหน้าแรกบนมือถือ
ใช้ topbar (CSS ซ่อน `.mobilebar` ในหน้านั้นเสมอ) ปุ่มบนแถบจึงไม่มีวันถูกเห็น ถอดออกแล้ว
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "meeting_ai" / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
RUNNER = (ROOT / "meeting_ai" / "runner.py").read_text(encoding="utf-8")
JOBS = (ROOT / "meeting_ai" / "web" / "jobs.py").read_text(encoding="utf-8")

HELP = HTML[HTML.index('<template id="tpl-help">'):]
HELP = HELP[:HELP.index("</template>")]


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestItIsReachable(unittest.TestCase):

    def test_there_is_one_button_and_it_is_on_the_topbar(self):
        head = HTML[HTML.index("<header"):HTML.index("</header>")]
        self.assertIn('id="btn-help"', head)
        self.assertEqual(HTML.count('id="btn-help"'), 1)

    def test_the_button_opens_the_manual(self):
        self.assertIn("$('#btn-help').onclick = () => showHelp();", APP_JS)

    def test_it_has_a_label_for_screen_readers(self):
        btn = HTML[HTML.index('id="btn-help"') - 60:]
        btn = btn[:btn.index("</button>")]
        self.assertIn('aria-label="คู่มือการใช้งาน"', btn)

    def test_the_hash_route_works_on_its_own(self):
        """เปิด /#help ตรง ๆ จากลิงก์ที่ส่งให้คนอื่นได้."""
        self.assertIn("else if (h === '#help') showHelp();", APP_JS)

    def test_the_mobile_bar_shows_a_title_for_it(self):
        self.assertIn("help:    { title: 'คู่มือการใช้งาน', back: true },", APP_JS)

    def test_no_dead_entry_point_was_left_on_the_mobile_bar(self):
        """หน้าแรกบนมือถือซ่อน `.mobilebar` เสมอ — ปุ่มตรงนั้นไม่มีวันถูกเห็น."""
        self.assertIn('body[data-view="home"] .mobilebar { display: none; }', CSS)
        self.assertNotIn("back: false, action: '?'", APP_JS)


class TestItWorksWithoutTheServer(unittest.TestCase):
    """คนที่ต้องการคู่มือที่สุดคือคนที่เพิ่งเปิดครั้งแรก หรือตอนอะไรสักอย่างพัง."""

    def test_the_content_is_static_markup(self):
        self.assertIn('<template id="tpl-help">', HTML)
        self.assertIn("$('#tpl-help').content.cloneNode(true)", APP_JS)

    def test_it_calls_no_api(self):
        body = fn("function showHelp()")
        self.assertNotIn("api(", body)
        self.assertNotIn("await", body)

    def test_it_starts_at_the_top(self):
        # เปิดคู่มือจากหน้าที่เลื่อนลงไปแล้ว ต้องไม่โผล่กลางเรื่อง
        self.assertIn("panel.scrollTop = 0;", fn("function showHelp()"))


class TestEveryTabItMentionsExists(unittest.TestCase):
    """ชื่อแท็บในคู่มือต้องตรงกับปุ่มจริง ไม่งั้นคนอ่านจะหาไม่เจอ.

    เช็คว่าชื่อถูกใช้เป็น **หัวข้อของรายการ** (`<dt>`) ไม่ใช่แค่โผล่ที่ไหนก็ได้ในหน้า —
    การเทียบสตริงย่อยกับภาษาไทยผ่านโดยบังเอิญได้ง่ายมาก: ตอนแรกเทสต์นี้เขียนแบบ
    `assertIn(label, HELP)` แล้วมุตันต์ที่เปลี่ยนชื่อแท็บ "ถาม" เป็น "คำถาม" รอดไปได้
    เพราะคู่มือมีประโยค "ลิงก์แชร์…ถามคำถามในแท็บ…" อยู่คนละที่
    """

    def _named_in_a_list(self, labels, count):
        self.assertEqual(len(labels), count, labels)
        for label in labels:
            with self.subTest(label=label):
                self.assertIn(f"<dt>{label}</dt>", HELP,
                              "คู่มือต้องมีหัวข้อของแท็บนี้เป็นของตัวเอง")

    def test_the_three_ways_to_get_audio_in(self):
        self._named_in_a_list(re.findall(r'data-cap="\w+">([^<]+)</button>', HTML), 3)

    def test_the_four_detail_tabs(self):
        self._named_in_a_list(re.findall(r'data-tab="\w+">([^<]+)</button>', HTML), 4)


class TestEveryButtonItTellsYouToPressExists(unittest.TestCase):

    def test_the_detail_actions(self):
        for label in ("🔗 แชร์", "⬇ ดาวน์โหลด", "สรุปใหม่ด้วย AI", "แปล"):
            with self.subTest(label=label):
                self.assertIn(label, HTML, "ปุ่มนี้หายไปจากหน้าเว็บแล้ว")
                self.assertIn(label, HELP)

    def test_the_visibility_choices(self):
        for label in ("🔒 เฉพาะฉัน", "👥 ทั้งทีม"):
            with self.subTest(label=label):
                self.assertIn(label, HTML)
                self.assertIn(label, HELP)

    def test_the_new_meeting_button(self):
        self.assertIn("+ ประชุมใหม่", HTML)
        self.assertIn("+ ประชุมใหม่", HELP)


class TestEveryStatusItQuotesIsReal(unittest.TestCase):
    """คู่มือยกข้อความบนหน้าจอมาอ้าง — ถ้าข้อความเปลี่ยน คู่มือต้องเปลี่ยนตาม."""

    def test_the_worker_chip_wordings(self):
        for phrase in ("เครื่องพร้อม", "ไม่มีเครื่องประมวลผลออนไลน์", "ไม่มีเครื่องรับงาน"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, APP_JS)
                self.assertIn(phrase, HELP)

    def test_the_no_speech_error(self):
        self.assertIn("ถอดเสียงไม่ได้ข้อความเลย", RUNNER)
        self.assertIn("ถอดเสียงไม่ได้ข้อความเลย", HELP)

    def test_the_outdated_worker_warning(self):
        self.assertIn("คนละรุ่นกับเซิร์ฟเวอร์", APP_JS)
        self.assertIn("คนละรุ่น", HELP)


class TestEveryNumberItPromisesMatchesTheCode(unittest.TestCase):
    """ตัวเลขในคู่มือเก่าเร็วที่สุด — ผูกกับค่าคงที่จริงไว้เลย."""

    def test_the_silence_warning_delay(self):
        m = re.search(r"const SILENT_START_SEC = (\d+);", APP_JS)
        self.assertIsNotNone(m)
        self.assertIn(f"{m.group(1)} วินาที", HELP)

    def test_the_queue_give_up_time(self):
        m = re.search(r"UNCLAIMABLE_MINUTES = (\d+)", JOBS)
        self.assertIsNotNone(m)
        self.assertIn(f"{m.group(1)} นาที", HELP)


class TestItLooksLikeTheRestOfTheApp(unittest.TestCase):

    def test_the_manual_page_is_full_width_on_a_phone(self):
        # ไม่งั้นเนื้อหาจะไปต่อท้ายรายการประชุมแทนที่จะเป็นหน้าของตัวเอง
        self.assertIn('body[data-view="help"] .sidebar', CSS)

    def test_the_callouts_use_the_warning_colour_not_the_error_one(self):
        """ส้ม = "อ่านก่อนจะเสียเวลา" · แดง = "พังแล้ว" — คู่มือไม่มีอะไรพัง.

        ของเดิมเขียน regex ล็อกไว้ว่ากฎต้องขึ้นต้นด้วย `.help-note {` เป๊ะ ๆ พอ #91
        รวมกฎเป็น `.help-note, .help-tip { }` ก็ล้มทั้งที่สียังเป็นสีเดิมทุกประการ
        — ตรึงว่า "กฎที่ประกาศ .help-note ใช้ --warn ไม่ใช่ --danger" ก็พอ
        """
        rules = [m.group(2) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS)
                 if ".help-note" in [n.strip().split(chr(10))[-1].strip()
                                     for n in m.group(1).split(",")]]
        self.assertTrue(rules, "ไม่เจอกฎของ .help-note เลย")
        self.assertIn("var(--warn)", chr(10).join(rules))
        self.assertNotIn("var(--danger)", chr(10).join(rules))

    def test_the_icon_button_does_not_grow_the_topbar(self):
        """topbar บนจอ 390px เคยสูงเกือบ 80px เพราะปุ่มตัดบรรทัด — ปุ่มนี้ต้องไม่ทำซ้ำ."""
        rule = CSS[CSS.index(".btn-icon {"):]
        rule = rule[:rule.index("}")]
        self.assertIn("flex: none", rule)
        self.assertIn("width: 32px", rule)
        self.assertIn("height: 32px", rule)


class TestTheTableOfContentsPointsSomewhere(unittest.TestCase):

    def test_every_link_has_a_section(self):
        targets = re.findall(r'<a href="#(help-[\w-]+)">', HELP)
        self.assertGreaterEqual(len(targets), 5)
        for t in targets:
            with self.subTest(target=t):
                self.assertIn(f'id="{t}"', HELP)

    def test_every_section_is_in_the_table_of_contents(self):
        sections = re.findall(r'<section id="(help-[\w-]+)"', HELP)
        targets = set(re.findall(r'<a href="#(help-[\w-]+)">', HELP))
        self.assertEqual(set(sections), targets)

    def test_the_anchors_cannot_be_mistaken_for_routes(self):
        """`applyHash()` รู้จักเฉพาะ #home/#new/#devices/#help/#m/<id> — ชื่อสมอต้องไม่ชน."""
        targets = re.findall(r'<a href="#(help-[\w-]+)">', HELP)
        for t in targets:
            with self.subTest(target=t):
                self.assertNotIn(f"'#{t}'", APP_JS)


if __name__ == "__main__":
    unittest.main()
