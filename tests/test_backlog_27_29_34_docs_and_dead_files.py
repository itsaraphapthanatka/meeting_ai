"""BACKLOG #27 + #29 + #34 — ไฟล์ตาย, เอกสารที่ขัดกันเอง, และโค้ดที่เพี้ยนแบบไม่พัง.

สามตั๋วเล็กที่รวมไว้ที่เดียวเพราะไม่มีอันไหนแตะ logic เลย

**#27** `meeting-ai-poster.html` ที่ root เหมือนกับใน `web/static/` ทุกไบต์ ตัวที่เสิร์ฟจริงคือ
ตัวใน static (`server.STATIC_DIR`) ตัวที่ root จึงไม่มีใครเรียก แต่ยังถูกอัปขึ้น Vercel ทุกครั้ง
พร้อมกับโฟลเดอร์ `bot/` ที่รันได้เฉพาะบนเครื่องที่มี Docker

**#29** เอกสารขัดกันเอง — ที่หนักสุดคือ **โปสเตอร์โฆษณาสิ่งที่ไม่มี**: บอกว่า Zoom "รองรับ"
และ Teams "กำลังพัฒนา" ซึ่งกลับด้านกับความจริง Zoom บล็อกบอทตามนโยบายและโปรเจกต์นี้
ตั้งใจไม่หลบเลี่ยง ส่วน README บอกเพดานเวลา Vercel ผิดไป 5 เท่า และผังไฟล์ยังเป็นของเก่า

**#34** ช่องว่างที่เพี้ยนจากบรรทัดต่อที่หายไป (`"stop"` ตามด้วยช่องว่าง 17 ตัวแล้ว `and`)
และ `_transcribe_track` ประกาศว่าคืน 2 ค่า แต่คืน 3 — ผู้เรียกแกะ 3 ค่าอยู่แล้วจึงไม่พัง
เป็นกับดักของคนอ่านครั้งหน้า ไม่ใช่บั๊กที่ทำงานผิด
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
POSTER = ROOT / "meeting_ai" / "web" / "static" / "meeting-ai-poster.html"
SERVER = ROOT / "meeting_ai" / "web" / "server.py"
RUNNER = ROOT / "meeting_ai" / "runner.py"


class TestNoDeadDuplicate(unittest.TestCase):

    def test_the_root_poster_is_gone(self):
        self.assertFalse((ROOT / "meeting-ai-poster.html").exists(),
                         "สำเนาที่ root ไม่มีใครเสิร์ฟ แต่ถูกอัปขึ้น Vercel ทุกครั้ง")

    def test_the_served_one_is_still_there(self):
        # ลบผิดตัวคือหน้าโปสเตอร์หายจากเว็บ
        self.assertTrue(POSTER.exists())

    def test_the_bot_folder_is_excluded_from_vercel(self):
        ignore = (ROOT / ".vercelignore").read_text(encoding="utf-8")
        self.assertRegex(ignore, r"(?m)^bot/\s*$")

    def test_the_things_that_must_stay_excluded_still_are(self):
        ignore = (ROOT / ".vercelignore").read_text(encoding="utf-8")
        for line in ("models/", "recordings/", ".env"):
            with self.subTest(line=line):
                self.assertIn(line, ignore)


class TestThePosterTellsTheTruth(unittest.TestCase):
    """โปสเตอร์เป็นหน้าที่ผู้ใช้เห็น การโฆษณาเกินจริงไม่ใช่แค่ 'เอกสารไม่ตรง'."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = POSTER.read_text(encoding="utf-8")

    def test_it_does_not_claim_zoom_is_supported(self):
        self.assertNotIn("รองรับ · ล็อกอินบอท", self.text)

    def test_it_says_zoom_is_blocked(self):
        self.assertIn("Zoom บล็อกบอท", self.text)

    def test_it_does_not_call_teams_unfinished(self):
        # Teams ใช้งานได้จริง (bot/platforms.py มี join_teams และ README ยืนยัน)
        self.assertNotRegex(self.text, r"Microsoft Teams</span><span class=\"badge wip\"")

    def test_it_no_longer_sells_a_zoom_bot(self):
        self.assertNotIn("ส่งบอทเข้า Meet/Zoom", self.text)


class TestTheReadmeAgreesWithItself(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = README.read_text(encoding="utf-8")

    def test_the_vercel_ceiling_matches_vercel_json(self):
        conf = (ROOT / "vercel.json").read_text(encoding="utf-8")
        seconds = int(re.search(r'"maxDuration"\s*:\s*(\d+)', conf).group(1))
        self.assertIn(f"{seconds} วินาที", self.text,
                      f"vercel.json ตั้งไว้ {seconds} วินาที README ต้องพูดตรงกัน")

    def test_it_no_longer_claims_five_minutes(self):
        self.assertNotIn("function ยาวสุด 5 นาที", self.text)

    def test_zoom_is_not_described_as_in_progress(self):
        # เคยมีสองตารางในไฟล์เดียวกันที่ขัดกันเอง: ตารางหนึ่งบอก Teams ใช้ได้
        # อีกตารางบอก "Teams / Zoom กำลังพัฒนา"
        self.assertNotIn("**Teams / Zoom** | 🚧 กำลังพัฒนา", self.text)

    def test_every_module_in_the_package_is_in_the_tree(self):
        tree = self.text[self.text.index("## โครงสร้าง"):]
        for path in sorted((ROOT / "meeting_ai").glob("*.py")):
            if path.name.startswith("__"):
                continue
            with self.subTest(module=path.name):
                self.assertIn(path.name, tree)

    def test_every_web_module_is_in_the_tree(self):
        tree = self.text[self.text.index("## โครงสร้าง"):]
        for path in sorted((ROOT / "meeting_ai" / "web").glob("*.py")):
            if path.name.startswith("__"):
                continue
            with self.subTest(module=path.name):
                self.assertIn(path.name, tree)

    def test_the_bot_files_are_named_correctly(self):
        tree = self.text[self.text.index("## โครงสร้าง"):]
        for path in sorted((ROOT / "bot").glob("*.py")):
            with self.subTest(module=path.name):
                self.assertIn(path.name, tree)

    def test_it_no_longer_lists_a_file_that_does_not_exist(self):
        self.assertNotIn("join_meet.py", self.text)
        self.assertFalse((ROOT / "bot" / "join_meet.py").exists())


class TestTheCosmeticCodeIssues(unittest.TestCase):

    def test_no_lost_line_continuations_remain(self):
        # ลายเซ็นของบรรทัดต่อที่หายไป: โค้ดจริงตามด้วยช่องว่างยาว ๆ แล้ว and/or
        for path in sorted((ROOT / "meeting_ai").rglob("*.py")):
            body = path.read_text(encoding="utf-8")
            for i, line in enumerate(body.split("\n"), 1):
                if line.lstrip().startswith("#"):
                    continue
                with self.subTest(file=path.name, line=i):
                    self.assertNotRegex(line, r"\S {5,}(and|or|else|not) ")

    def test_lines_stay_within_a_readable_width(self):
        # บรรทัดที่เพี้ยนทั้งสองอันยาวเกิน 110 ตัว ซึ่งเป็นอาการที่มองเห็นได้ง่ายกว่า
        long_lines = [(p.name, i) for p in (SERVER,)
                      for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1)
                      if len(line) > 120]
        self.assertEqual(long_lines, [])

    def test_transcribe_track_says_what_it_returns(self):
        tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_transcribe_track")
        self.assertIsNotNone(fn.returns, "ไม่มี annotation เลย")
        declared = len(fn.returns.slice.elts)
        returns = [n for n in ast.walk(fn)
                   if isinstance(n, ast.Return) and isinstance(n.value, ast.Tuple)]
        self.assertTrue(returns, "หา return แบบ tuple ไม่เจอ — ตัวตรวจพัง ไม่ใช่โค้ดดี")
        for node in returns:
            with self.subTest(line=node.lineno):
                self.assertEqual(declared, len(node.value.elts),
                                 f"ประกาศว่าคืน {declared} ค่า แต่บรรทัด {node.lineno} "
                                 f"คืน {len(node.value.elts)}")

    def test_the_caller_unpacks_the_same_number(self):
        src = RUNNER.read_text(encoding="utf-8")
        m = re.search(r"^\s*([\w, ]+)\s*=\s*_transcribe_track\(", src, re.M)
        self.assertIsNotNone(m)
        self.assertEqual(len(m.group(1).split(",")), 3)


if __name__ == "__main__":
    unittest.main()
