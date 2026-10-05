"""BACKLOG #91 — คู่มือทรงใหม่ตามตัวอย่างที่เจ้าของส่งมา.

#90 ทำคู่มือที่ "ถูก" แต่หน้าตาเป็นเอกสารยาว ๆ อันเดียว ตั๋วนี้จัดใหม่ให้เป็นหนังสือ:
สารบัญมีเลขข้อ · หัวข้อเป็นแถบมีเลข · ขั้นตอนมีเลขวงกลม · ตารางเปรียบเทียบ ·
ชิ้นส่วนหน้าจอตัวอย่างพร้อมคำบรรยาย · คำถามที่พบบ่อยแบบย่อ-กาง

เทสต์ชุดนี้แบ่งเป็นสี่กลุ่มตามสิ่งที่มันกันไม่ให้พัง:

1. **โครงที่ขอมา** — เลขข้อ สารบัญ ตาราง ขั้นตอน รูปตัวอย่าง FAQ ยังอยู่ครบ
2. **ชิ้นส่วนตัวอย่างสร้างจากของจริง** — คลาสทุกตัวใน `.help-mock` ต้องมีอยู่จริง
   ใน `style.css` หรือ `app.js` ไม่ใช่มาร์กอัปปลอมที่จะเพี้ยนจากของจริงเงียบ ๆ
3. **สามบั๊กที่วัดเจอบนเบราว์เซอร์จริง** (ทั้งสามเป็นของที่ #90 ปล่อยผ่าน):
   - กดสารบัญแล้ว `applyHash()` ตกไปที่ `showNew()` → หลุดออกจากคู่มือทุกครั้ง
   - ลิงก์ `/#help` ที่ส่งให้คนยังไม่ล็อกอิน ไปจบที่ฟอร์มเข้าสู่ระบบ ไม่เคยเปิดคู่มือ
   - กด ? จากหน้าเข้าสู่ระบบแล้วไม่มีทางกลับ ต้องรีโหลดเอง
4. **ตัวเลข/ชื่อที่เพิ่มเข้ามาใหม่** ต้องตรงกับของจริงในหน้าเว็บ

วัดกับเบราว์เซอร์จริงแล้วทั้งสองขนาด (เซิร์ฟเวอร์รันจาก worktree ไม่ใช่ที่เก็บหลัก):

    เดสก์ท็อป 1280x860  สารบัญ 14 ลิงก์ hit-test ผ่านทุกอัน · กด "คำถามที่พบบ่อย"
                        แล้วยังอยู่ในคู่มือ (view=help) และเลื่อนไปที่หัวข้อจริง
                        · FAQ กางได้ คำตอบสูง 70px · ชิปจำลองสูง 37px ทั้งสามใบ
                        · ปุ่มกลับซ่อน (display:none)
    มือถือ 375x812      ไม่มีการเลื่อนแนวนอนของทั้งหน้า (scrollWidth = 375)
                        · ตารางสองใบเลื่อนในกรอบตัวเอง (343 → 451 และ 601)
                        · สารบัญ 14 ลิงก์ hit-test ผ่าน · sidebar display:none
                        · ปุ่ม ? ที่ **หน้าแรก** 32x32 กดติด แล้วเข้าคู่มือจริง
                          (ในหน้าคู่มือปุ่มนี้ 0x0 เพราะ CSS สลับไปใช้ mobilebar ตามกติกา
                           "หนึ่งหน้าจอ = หนึ่งแถบหัว" — ไม่ใช่บั๊ก)
    หน้าเข้าสู่ระบบ      ปุ่มกลับ 163x41 กดติด · กดแล้วได้ฟอร์มเข้าสู่ระบบคืนและ hash ว่าง
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

HELP = HTML[HTML.index('<template id="tpl-help">'):]
HELP = HELP[:HELP.index("</template>")]

MOCKS = re.findall(r'<div class="help-mock[^"]*">(.*?)\n        </div>', HELP, re.S) \
    or re.findall(r'<div class="help-mock[^"]*">(.*?)</figure>', HELP, re.S)


def fn(name: str) -> str:
    """ตัวฟังก์ชันใน app.js ตั้งแต่ชื่อจนถึงฟังก์ชันถัดไป."""
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


# คอมเมนต์แทรกกลางรายการ selector ได้ (เช่นกฎของ .m-chip) ตัดทิ้งก่อนค่อยอ่าน
CSS_NC = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)


def css_rule(selector: str) -> str:
    """ทุกบล็อกกฎที่ประกาศ selector นี้ไว้ ต่อกัน.

    ไม่ผูกกับ `^` ของบรรทัด เพราะกฎในมีเดียควีรีย่อหน้าเข้าไป และไม่ผูกกับรูปแบบ
    การจัดกลุ่ม selector ด้วย — `.help-note, .help-tip { }` กับการแยกเขียนสองกฎ
    ต้องให้ผลเหมือนกัน ไม่งั้นเทสต์จะพังตอนจัดระเบียบ CSS ทั้งที่พฤติกรรมไม่เปลี่ยน
    (กับดักเดียวกับ BUG-071)
    """
    out = [m.group(2) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", CSS_NC)
           if selector in [n.strip().split(chr(10))[-1].strip()
                           for n in m.group(1).split(",")]]
    assert out, f"ไม่เจอกฎของ {selector}"
    return chr(10).join(out)


# ---------------------------------------------------------------- 1. โครง

class TestTheShapeTheOwnerAskedFor(unittest.TestCase):

    def test_the_table_of_contents_is_numbered_by_the_browser(self):
        """เลขข้อมาจาก <ol> ไม่ใช่พิมพ์มือ — เพิ่ม/ลบหัวข้อแล้วเลขไม่มีทางเพี้ยน."""
        toc = HELP[HELP.index('<nav class="help-toc"'):]
        toc = toc[:toc.index("</nav>")]
        self.assertIn("<ol>", toc)
        self.assertNotRegex(toc, r"<li>\s*\d+[.)]", "เลขข้อถูกพิมพ์มือ")

    def test_every_section_carries_its_own_number(self):
        nums = re.findall(r'<span class="help-n">(\d+)</span>', HELP)
        sections = re.findall(r'<section id="(help-[\w-]+)"', HELP)
        self.assertEqual(len(nums), len(sections), "มีหัวข้อที่ไม่มีเลข")
        self.assertEqual([int(n) for n in nums], list(range(1, len(sections) + 1)),
                         "เลขหัวข้อไม่เรียง 1..N")

    def test_the_numbers_follow_the_order_of_the_table_of_contents(self):
        order = re.findall(r'<a href="#(help-[\w-]+)">', HELP)
        sections = re.findall(r'<section id="(help-[\w-]+)"', HELP)
        self.assertEqual(order, sections, "ลำดับในสารบัญกับลำดับหัวข้อจริงไม่ตรงกัน")

    def test_it_is_long_enough_to_need_a_table_of_contents(self):
        self.assertGreaterEqual(len(re.findall(r'<section id="help-', HELP)), 12)

    def test_there_are_comparison_tables_with_headers(self):
        tables = re.findall(r"<table class=\"help-table\">(.*?)</table>", HELP, re.S)
        self.assertGreaterEqual(len(tables), 3)
        for i, t in enumerate(tables):
            with self.subTest(table=i):
                self.assertIn("<thead>", t)
                self.assertIn("<tbody>", t)

    def test_there_are_numbered_step_lists(self):
        self.assertGreaterEqual(HELP.count('<ol class="help-steps">'), 5)

    def test_the_step_numbers_are_drawn_by_css_not_typed(self):
        rule = css_rule(".help-steps li::before")
        self.assertIn("counter-increment: hstep", rule)
        self.assertNotRegex(HELP, r'<li>\s*1\.\s', "ขั้นตอนถูกพิมพ์เลขมือ")

    def test_every_example_has_a_caption(self):
        """รูปตัวอย่างที่ไม่มีคำบรรยายคือรูปที่ต้องเดาเอาเองว่าให้ดูอะไร."""
        figs = re.findall(r"<figure class=\"help-fig\">(.*?)</figure>", HELP, re.S)
        self.assertGreaterEqual(len(figs), 4)
        for i, f in enumerate(figs):
            with self.subTest(figure=i):
                self.assertIn("<figcaption>", f)

    def test_the_faq_entries_fold_away(self):
        qs = re.findall(r"<details class=\"help-q\">(.*?)</details>", HELP, re.S)
        self.assertGreaterEqual(len(qs), 8)
        for i, q in enumerate(qs):
            with self.subTest(q=i):
                self.assertIn("<summary>", q)
                self.assertIn("<p>", q)

    def test_the_two_kinds_of_callout_are_told_apart_by_more_than_colour(self):
        """สีอย่างเดียวแยกไม่ออกสำหรับคนตาบอดสี — ติดสัญลักษณ์นำหน้าให้ด้วย."""
        self.assertIn("'⚠️ '", css_rule(".help-note::before"))
        self.assertIn("'💡 '", css_rule(".help-tip::before"))
        self.assertIn("var(--warn)", css_rule(".help-note"))
        self.assertIn("var(--accent)", css_rule(".help-tip"))
        for cls in ("help-note", "help-tip"):
            with self.subTest(cls=cls):
                self.assertGreaterEqual(HELP.count(f'class="{cls}"'), 3)


# ------------------------------------------------- 2. ของจำลองสร้างจากของจริง

class TestTheExamplesAreBuiltFromTheRealThing(unittest.TestCase):
    """ชิ้นส่วนตัวอย่างในคู่มือต้องใช้คลาสจริงของแอป.

    ถ้าปล่อยให้ประกอบมาร์กอัปปลอมขึ้นมาเอง วันหนึ่งหน้าจริงเปลี่ยนหน้าตาแล้วตัวอย่าง
    ในคู่มือจะยังเป็นของเก่าโดยไม่มีอะไรฟ้อง — เหมือนรูปถ่ายที่ลืมอัปเดต
    """

    def test_there_are_examples_at_all(self):
        self.assertGreaterEqual(len(MOCKS), 4, MOCKS)

    def test_every_class_in_an_example_exists_somewhere_real(self):
        own = {"help-mock", "help-mock-bar", "help-mock-brand", "help-mock-sp",
               "help-mock-chips", "help-k", "help-badge"}
        for i, mock in enumerate(MOCKS):
            for cls in re.findall(r'class="([^"]+)"', mock):
                for one in cls.split():
                    if one in own:
                        continue
                    with self.subTest(mock=i, cls=one):
                        self.assertTrue(f".{one}" in CSS or f'"{one}' in APP_JS,
                                        f"คลาส {one} ไม่มีอยู่จริงในแอป")

    def test_classes_borrowed_from_real_buttons_are_centred_like_buttons(self):
        """ตัวอย่างใช้ <span> แต่คลาสถูกออกแบบมากับ <button>.

        คนที่จัดตัวอักษรให้อยู่กลางปุ่มคือ UA stylesheet ของ <button> เอง ไม่ใช่ CSS ของเรา
        — <span> จึงได้แค่กล่องเปล่าแล้วตัวอักษรกองมุมซ้ายบน

        เจ้าของส่งภาพหน้าจอมาให้ดูว่าปุ่ม ? ในรูปตัวอย่างเพี้ยน พอไปวัดจริงพบว่า**ไม่ใช่
        ปุ่มเดียว**: ตัว ? อยู่ที่ (1, -2) ของกล่อง 32x32 คือโผล่พ้นวงกลมขึ้นไปข้างบน
        และชื่อแท็บทั้งสี่ชิดซ้ายที่ offsetX 0 ด้วย — หลังแก้ ทั้งเก้าชิ้นอยู่กลางทุกแกน
        ทั้งธีมสว่างและมืด

        เทสต์นี้จึงไม่ได้ตรึงปุ่มใดปุ่มหนึ่ง แต่ไล่ทุก <span> ในตัวอย่างที่ยืมคลาสของ
        <button> จริงมาใช้ — เพิ่มชิ้นส่วนใหม่ทีหลังก็ถูกตรวจเองโดยไม่ต้องแก้เทสต์
        """
        on_buttons = set()
        for src in (HTML, APP_JS):
            for m in re.finditer(r'<button[^>]*class="([^"]+)"', src):
                on_buttons.update(m.group(1).split())
        self.assertIn("btn", on_buttons, "สมมติฐานเปลี่ยน: หาคลาสของปุ่มจริงไม่เจอ")

        checked = 0
        for i, mock in enumerate(MOCKS):
            for m in re.finditer(r'<span class="([^"]+)"', mock):
                classes = [c for c in m.group(1).split() if c in on_buttons]
                if not classes:
                    continue
                checked += 1
                rules = ""
                for c in classes:
                    try:
                        rules += css_rule(f".help-mock .{c}")
                    except AssertionError:
                        pass
                with self.subTest(mock=i, classes=" ".join(classes)):
                    self.assertRegex(rules, r"display: (inline-)?flex",
                                     "กล่องไม่ได้เป็น flex ตัวอักษรจะไม่อยู่กลาง")
                    self.assertIn("align-items: center", rules)
        self.assertGreaterEqual(checked, 5, "ไม่ได้ตรวจอะไรเลย — ตัวอย่างเปลี่ยนโครงไปแล้ว?")

    def test_the_status_chip_example_uses_the_states_the_code_sets(self):
        used = set(re.findall(r'<span class="m-chip" data-state="(\w+)"', HELP))
        real = set(re.findall(r"chip\.dataset\.state = [^;]+", APP_JS)[0]
                   .replace("'", " ").split()) & {"ok", "down", "stuck"}
        self.assertEqual(used, real, "สถานะของชิปในคู่มือไม่ตรงกับที่โค้ดตั้งจริง")

    def test_the_job_card_example_has_the_same_bones_as_the_real_one(self):
        real = fn("function renderJobs()")
        mock = next(m for m in MOCKS if 'class="job"' in m)
        for part in ('class="jt"', 'class="js"', 'class="bar"'):
            with self.subTest(part=part):
                self.assertIn(part, real)
                self.assertIn(part, mock)
        self.assertIn('class="job-stuck"', mock,
                      "ตัวอย่างควรมีทั้งงานปกติและงานค้างคิว ไม่งั้นไม่ได้สอนอะไร")

    def test_the_chip_example_is_visible_on_wide_screens_too(self):
        """ชิปตัวจริงถูก display:none บนจอกว้าง ตัวอย่างต้องสั่งให้เห็นเอง.

        วัดจริงแล้ว: บน 1280x860 ชิปทั้งสามใบสูง 37px และ elementFromPoint ได้ตัวมันเอง
        """
        self.assertIn("display: none", css_rule(".m-chip"),
                      "สมมติฐานเปลี่ยน: ชิปตัวจริงไม่ได้ถูกซ่อนบนจอกว้างแล้ว")
        self.assertIn("display: inline-flex", css_rule(".help-mock .m-chip"))
        for state in ("down", "stuck"):
            with self.subTest(state=state):
                self.assertIn("var(--warn)", css_rule(
                    f'.help-mock .m-chip[data-state="{state}"] .m-chip-dot'))


# ------------------------------------------- 3. บั๊กที่วัดเจอบนเบราว์เซอร์จริง

class TestClickingTheTableOfContentsStaysInTheManual(unittest.TestCase):
    """บั๊กของ #90: `applyHash()` ลงท้ายด้วย `else showNew()`.

    สมอทุกอันในสารบัญ (`#help-start` ฯลฯ) จึงไม่ตรงเงื่อนไขไหนเลยแล้วตกไปที่
    หน้า "ประชุมใหม่" — กดสารบัญทีไรหลุดออกจากคู่มือทุกครั้ง ตั้งแต่วันที่ shipped
    """

    def test_apply_hash_knows_about_in_page_anchors(self):
        body = fn("function applyHash()")
        self.assertIn("h.startsWith('#help-')", body)
        # ต้องมาก่อน else สุดท้ายที่พาไปหน้าประชุมใหม่
        self.assertLess(body.index("'#help-'"), body.index("else showNew()"))

    def test_it_scrolls_to_the_section_instead_of_changing_page(self):
        body = fn("function applyHash()")
        self.assertIn("scrollIntoView()", body)
        self.assertNotIn("setView('new')", body)

    def test_opening_a_section_link_cold_renders_the_manual_first(self):
        """เปิด /#help-faq ตรง ๆ จากลิงก์ ต้องได้คู่มือ ไม่ใช่หน้าว่าง."""
        body = fn("function applyHash()")
        self.assertRegex(body, r"if \(!\$\('#panel'\)\.querySelector\('\.help'\)\) showHelp\(\)")


class TestTheManualOpensBeforeLogin(unittest.TestCase):
    """ลิงก์คู่มือมักถูกส่งให้คนที่ยังไม่มีบัญชี — ของเดิมพาไปที่ฟอร์มเข้าสู่ระบบเฉย ๆ."""

    def test_init_honours_the_help_hash_even_when_auth_is_required(self):
        init = APP_JS[APP_JS.index("(async function init"):]
        gate = init.index("if (needsAuth())")
        tail = init[gate:gate + 600]
        self.assertIn("showAuth();", tail)
        self.assertIn("if (location.hash.startsWith('#help')) applyHash();", tail)
        self.assertLess(tail.index("applyHash()"), tail.index("return;"),
                        "คู่มือถูกเปิดหลัง return — ไม่มีทางทำงาน")

    def test_the_hash_check_runs_before_the_rest_of_the_app_loads(self):
        """ยังไม่ล็อกอินก็ไม่มี refresh() ให้รอ — คู่มือต้องขึ้นได้โดยไม่เรียก API."""
        body = fn("function showHelp()")
        self.assertNotIn("api(", body)
        self.assertNotIn("await", body)


class TestThereIsAWayBackFromTheManual(unittest.TestCase):
    """กด ? จากหน้าเข้าสู่ระบบแล้วแผงถูกแทนทั้งแผง — ของเดิมเหลือแต่ลิงก์สารบัญ."""

    def test_the_button_exists_and_says_where_it_goes(self):
        self.assertIn('id="help-back"', HELP)
        btn = HELP[HELP.index('id="help-back"'):]
        btn = btn[:btn.index("</button>")]
        self.assertIn("เข้าสู่ระบบ", btn)

    def test_it_is_wired_up(self):
        body = fn("function showHelp()")
        self.assertIn("$('#help-back').onclick", body)
        self.assertIn("showAuth()", body)

    def test_it_clears_the_hash_so_a_reload_does_not_bounce_back(self):
        self.assertIn("setHash('')", fn("function showHelp()"))

    def test_it_only_shows_on_the_login_screen(self):
        """ล็อกอินแล้วมีปุ่มย้อนกลับของตัวเองอยู่ ปุ่มนี้จะกลายเป็นขยะ."""
        self.assertIn("display: none", css_rule(".help-back"))
        self.assertIn("display: inline-block", css_rule("body.auth-only .help-back"))


# ----------------------------------------- 4. เนื้อหาใหม่ต้องตรงกับหน้าจอจริง

class TestTheNewContentMatchesTheApp(unittest.TestCase):

    def test_the_file_formats_it_lists(self):
        shown = re.search(r'<p class="drop-formats muted">([^<]+)</p>', HTML).group(1)
        for ext in re.findall(r"\w+", shown):
            with self.subTest(ext=ext):
                self.assertIn(ext, HELP, "คู่มือบอกฟอร์แมตไม่ครบตามที่หน้าเว็บโฆษณา")

    def test_the_default_bot_name(self):
        name = re.search(r'id="b-name"[^>]*value="([^"]+)"', HTML).group(1)
        self.assertIn(name, HELP)

    def test_the_default_time_limit_in_the_room(self):
        opt = re.search(r'<option value="(\d+)" selected>([^<]+)</option>', HTML)
        self.assertEqual(opt.group(1), "120")
        self.assertIn(opt.group(2), HELP, "ค่าเริ่มต้นของ 'อยู่ในห้องไม่เกิน' ไม่ตรงกับคู่มือ")

    def test_the_three_recording_modes(self):
        modes = re.findall(r"<strong>([^<]+)</strong>", HTML)
        modes = [m.split("—")[0].strip() for m in modes if m.startswith(("🎙️", "🎧", "🖥️"))]
        self.assertEqual(len(modes), 3, modes)
        for m in modes:
            with self.subTest(mode=m):
                self.assertIn(m, HELP)

    def test_the_claim_that_tab_mode_is_missing_on_phones(self):
        self.assertIn("TAB_MODE_Q.matches", APP_JS)
        self.assertIn("แชร์แท็บ", HELP)
        self.assertRegex(HELP, r"แชร์แท็บ</b> ไม่มีบนจอแคบ")

    def test_the_speaker_names_it_quotes_are_the_ones_the_code_writes(self):
        """คู่มือเคยบอกแค่ “ผู้พูด N” ซึ่งจริงเฉพาะการแยกอัตโนมัติ.

        โหมดสองแทร็กรู้จากแหล่งเสียงอยู่แล้วว่าใครเป็นใคร จึงตั้งชื่อเป็น
        SELF_LABEL/OTHERS_LABEL ตั้งแต่ต้น — คนอ่านที่ใช้โหมดนั้นจะหาชิป
        "ผู้พูด 1" ไม่เจอแล้วนึกว่าระบบพัง
        """
        runner = (ROOT / "meeting_ai" / "runner.py").read_text(encoding="utf-8")
        diarize = (ROOT / "meeting_ai" / "diarize.py").read_text(encoding="utf-8")
        auto = re.search(r'f"(ผู้พูด) \{idx \+ 1\}"', diarize)
        self.assertIsNotNone(auto, "ชื่อผู้พูดอัตโนมัติเปลี่ยนรูปแบบไปแล้ว")
        self.assertIn(auto.group(1), HELP)
        for const in ("SELF_LABEL", "OTHERS_LABEL"):
            label = re.search(rf'{const} = "([^"]+)"', runner).group(1)
            with self.subTest(label=label):
                self.assertIn(f"<b>{label}</b>", HELP,
                              "คู่มือไม่ได้บอกชื่อผู้พูดของโหมดสองแทร็ก")

    def test_it_does_not_claim_the_topbar_is_on_every_screen(self):
        """บนมือถือมีกติกา "หนึ่งหน้าจอ = หนึ่งแถบหัว" — หน้าอื่นซ่อน topbar ทิ้ง.

        วัดแล้ว: บน 375x812 ปุ่ม ? ที่หน้าแรก 32x32 กดติด แต่ในหน้าคู่มือวัดได้ 0x0
        คำบรรยายรูปจึงห้ามบอกว่าแถบบนสุด "มีอยู่ทุกหน้า"
        """
        self.assertIn('body:not([data-view="home"]) .topbar', CSS,
                      "สมมติฐานเปลี่ยน: topbar ไม่ได้ถูกซ่อนในหน้าอื่นบนมือถือแล้ว")
        self.assertNotIn("แถบบนสุดมีอยู่ทุกหน้า", HELP)
        self.assertIn("หน้าอื่นสลับไปใช้แถบล่างแทน", HELP)

    def test_the_button_that_pulls_the_bot_out_early(self):
        label = "ให้บอทออกจากห้องแล้วสรุป"
        self.assertIn(label, APP_JS)
        self.assertIn(label, HELP)

    def test_the_install_as_an_app_claim_has_a_manifest_behind_it(self):
        self.assertIn('rel="manifest"', HTML)
        self.assertIn("เพิ่มไปยังหน้าจอหลัก", HELP)

    def test_the_languages_it_names_are_offered(self):
        sel = HTML[HTML.index('id="f-lang"'):]
        sel = sel[:sel.index("</select>")]
        offered = re.findall(r"<option[^>]*>([^<]+)</option>", sel)
        named = [o for o in offered if o in HELP]
        self.assertGreaterEqual(len(named), 4, f"คู่มือเอ่ยถึงภาษาแค่ {named}")

    def test_it_does_not_promise_a_red_dot_that_does_not_exist(self):
        """กับดักที่เกือบเขียนลงไป: `down` กับ `stuck` ใช้ var(--warn) ทั้งคู่ ไม่มีสีแดง."""
        for state in ("down", "stuck"):
            with self.subTest(state=state):
                self.assertIn("var(--warn)",
                              css_rule(f'.m-chip[data-state="{state}"] .m-chip-dot'))
        self.assertNotIn("แดง = ไม่มีเครื่องเลย", HELP)


if __name__ == "__main__":
    unittest.main()
