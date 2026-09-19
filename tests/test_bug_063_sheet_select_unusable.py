"""BUG-063 — บนจอแคบ <select> ในแผ่นล่างจอกดไม่ได้ ("เฉพาะฉัน" กับ ".docx").

เจ้าของรายงานพร้อมภาพหน้าจอ (2026-09-19): ในเมนู ⋯ ของหน้ารายละเอียด ปุ่มใช้ได้ปกติ
แต่ตัวเลือกสองตัวกดแล้วไม่มีอะไรเกิดขึ้น

สาเหตุไม่ได้อยู่ที่ CSS (ตรวจแล้วไม่มีอะไรบัง — `elementFromPoint` กลางตัวเลือกทั้งสอง
คืนตัวมันเองทั้งคู่) แต่อยู่ที่ตัวฟัง click บน `.detail-actions` ที่ปิดแผ่นเมื่อคลิก
**อะไรก็ได้** ข้างใน:

    $('.detail-actions').addEventListener('click', () => {
      if (document.body.classList.contains('sheet-open')) closeMeetingSheet();
    });

บนมือถือ การแตะ `<select>` ยิง `click` ขึ้นมาถึงตัวนี้ **ก่อน** ที่ตัวเลือกจะโผล่ พอปิดแผ่น
CSS `body.sheet-open .detail-actions` หลุด แล้ว `.detail-actions { display: none }` ของ
จอแคบเข้าแทน — `<select>` หายจาก layout และตัวเลือกไม่เคยขึ้นเลย

ยืนยันในเบราว์เซอร์จริงที่ความกว้างมือถือ โดยดึงตัวฟังตัวจริงจาก `app.js` ที่เสิร์ฟอยู่มารัน:

    ก่อนแก้ แตะ select -> "แผ่นถูกปิด · .detail-actions display=none"
    หลังแก้ แตะ select -> "แผ่นยังเปิดอยู่" · แตะปุ่ม -> "แผ่นถูกปิด" (เหมือนเดิม)

เทสต์นี้รันตัวฟังตัวจริงที่ตัดมาจาก `app.js` ด้วย node โดยปลอมเฉพาะ DOM รอบ ๆ
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
STYLE = (ROOT / "meeting_ai" / "web" / "static" / "style.css").read_text(encoding="utf-8")
INDEX = (ROOT / "meeting_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")
NODE = shutil.which("node")

LISTENER = re.compile(
    r"\$\('\.detail-actions'\)\.addEventListener\('click', \(e\) => \{([\s\S]*?)\n  \}\);")


class TestTheSheetStillHidesTheRow(unittest.TestCase):
    """ถ้าจอแคบไม่ซ่อน .detail-actions แล้ว บั๊กนี้จะเปลี่ยนรูปไปเลย — ปักหมุดไว้."""

    def test_narrow_screens_hide_the_row_and_show_it_as_a_sheet(self):
        self.assertRegex(STYLE, r"@media \(max-width: 860px\)")
        self.assertRegex(STYLE, r"\.detail-actions \{ display: none; \}")
        self.assertRegex(STYLE, r"body\.sheet-open \.detail-actions \{")

    def test_both_selects_live_in_that_row(self):
        row = INDEX[INDEX.index('<div class="detail-actions">'):]
        row = row[:row.index("</div>")]
        for sel in ("d-visibility", "d-export-fmt"):
            with self.subTest(select=sel):
                self.assertIn(f'id="{sel}"', row)
                self.assertIn("<select", row)


@unittest.skipUnless(NODE, "ต้องมี node เพื่อรันตัวฟังจริงจาก app.js")
class TestTappingASelectKeepsTheSheetOpen(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        m = LISTENER.search(APP_JS)
        if not m:
            raise AssertionError("ตัดตัวฟัง click ของ .detail-actions จาก app.js ไม่ได้")
        cls.body = m.group(1)
        cls.tmp = Path(tempfile.mkdtemp(prefix="mai-bug063-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)

    def _tap(self, tag: str) -> bool:
        """คืน True ถ้าแผ่นยังเปิดอยู่หลังคลิกของที่มี tag นี้."""
        script = self.tmp / "run.js"
        script.write_text(
            "let open = true;\n"
            "const document = { body: { classList: {\n"
            "  contains: (c) => c === 'sheet-open' && open,\n"
            "  remove: (c) => { if (c === 'sheet-open') open = false; },\n"
            "} } };\n"
            "function closeMeetingSheet() { document.body.classList.remove('sheet-open'); }\n"
            f"const tag = {json.dumps(tag)};\n"
            "const e = { target: { closest: (s) => (s === tag ? { tag } : null) } };\n"
            "const run = (e) => {" + self.body + "\n};\n"
            "run(e);\n"
            "process.stdout.write(String(open));\n",
            encoding="utf-8", newline="\n")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout.strip() == "true"

    def test_a_select_does_not_close_the_sheet(self):
        # หัวใจของบั๊ก — ของเดิมปิด ทำให้ตัวเลือกหายไปก่อนจะโผล่
        self.assertTrue(self._tap("select"),
                        "แตะ <select> แล้วแผ่นปิด — ตัวเลือกจะไม่มีทางขึ้น")

    def test_a_button_still_closes_the_sheet(self):
        # ต้องไม่แก้จนปุ่มอื่นเลิกปิดแผ่น ไม่งั้นม่านค้างทับหน้า (เหตุผลเดิมของตัวฟังนี้)
        self.assertFalse(self._tap("button"),
                         "แตะปุ่มแล้วแผ่นไม่ปิด — ม่านจะค้างทับหน้า")


class TestTheGuardIsActuallyThere(unittest.TestCase):

    def test_the_listener_skips_selects(self):
        m = LISTENER.search(APP_JS)
        self.assertIsNotNone(m, "รูปของตัวฟังเปลี่ยนไป — เทสต์ข้างบนจะตัดโค้ดไม่ได้")
        self.assertIn("e.target.closest('select')", m.group(1))

    def test_nothing_closes_the_sheet_unconditionally_any_more(self):
        # รูปเดิมคือ addEventListener('click', () => { ... }) ที่ไม่รับ event เลย
        self.assertNotIn("$('.detail-actions').addEventListener('click', () => {", APP_JS)

    def test_choosing_a_visibility_closes_the_sheet_afterwards(self):
        # เลือกเสร็จแล้วธุระจบ ควรปิดให้เอง ไม่ใช่ทิ้งม่านไว้ให้ผู้ใช้หาทางปิด
        self.assertIn("$('#d-visibility').addEventListener('change', () => {", APP_JS)


if __name__ == "__main__":
    unittest.main()
