"""BUG-072 — แบนเนอร์ "ไปกด Admit" ค้างอยู่ทั้งที่บอทเข้าห้องแล้ว.

เจ้าของส่งภาพหน้าจอมา (2026-09-19): การ์ดงานบอก **"บอทอยู่ในห้อง 03:02"** แต่แบนเนอร์
ด้านบนในจอเดียวกันยังบอก **"ส่งบอทแล้ว — ไปกด 'รับเข้าห้อง' (Admit) ในห้องประชุมด้วย"**
สองข้อความขัดกันเอง ผู้ใช้ไม่รู้ว่าต้องเชื่ออันไหนหรือต้องไปทำอะไรต่อ

`banner()` เดิมไม่มีวันหมดอายุ — ตั้งครั้งเดียวแล้วค้างจนกว่าจะมีข้อความใหม่มาทับ
คำแนะนำที่ถูกต้อง ณ วินาทีที่กดส่ง จึงกลายเป็นคำสั่งที่ผิดในอีกสิบวินาทีต่อมา

แก้: ติดป้าย `tag` ให้แบนเนอร์ว่าขึ้นเพราะเรื่องอะไร แล้วถอนออกตอน poll เจอว่างานของเรา
รายงานว่าเข้าห้องได้แล้ว

**ข้อความ "บอทอยู่ในห้อง" ถูกสร้างที่ `runner.py` แต่ถูกอ่านที่ `app.js`** — สองไฟล์คนละ
ภาษาที่ต้องตรงกัน ไฟล์นี้จึงผูกมันไว้ด้วยกัน ถ้าใครแก้ฝั่งเดียวจะรู้ทันที แทนที่จะปล่อยให้
แบนเนอร์เงียบ ๆ เลิกหายไปเองโดยไม่มีใครสังเกต
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
RUNNER = (ROOT / "meeting_ai" / "runner.py").read_text(encoding="utf-8")


class TestTheTwoSidesAgree(unittest.TestCase):
    """ฝั่งที่สร้างข้อความกับฝั่งที่อ่าน อยู่คนละไฟล์คนละภาษา — ต้องตรงกันเป๊ะ."""

    def test_the_client_constant_matches_what_the_server_sends(self):
        m = re.search(r"const BOT_IN_ROOM = '([^']+)';", APP_JS)
        self.assertIsNotNone(m, "หา BOT_IN_ROOM ใน app.js ไม่เจอ")
        client = m.group(1)
        m2 = re.search(r'what = f"([^"{]*)\{_mmss\(elapsed\)\}"', RUNNER)
        self.assertIsNotNone(m2, "หาข้อความ step ของ tick() ใน runner.py ไม่เจอ")
        server = m2.group(1).strip()
        self.assertEqual(client, server,
                         "ข้อความสองฝั่งไม่ตรงกัน — แบนเนอร์จะเลิกหายเองโดยไม่มีใครรู้")

    def test_the_server_really_sends_it_only_when_inside(self):
        # ต้องมาจากสาขา status == "inroom" ไม่ใช่ข้อความรอหน้าห้อง
        block = RUNNER[RUNNER.index('if status == "inroom":'):]
        block = block[:block.index("elif")]
        self.assertIn("บอทอยู่ในห้อง", block)


class TestTheBannerExpires(unittest.TestCase):

    def test_banner_takes_a_tag(self):
        self.assertRegex(APP_JS, r"function banner\(msg, tag = ''\)")

    def test_clearing_the_banner_clears_the_tag(self):
        # ไม่งั้นป้ายเก่าค้าง แล้วแบนเนอร์อันถัดไปโดนถอนผิดตัว
        self.assertRegex(APP_JS, r"if \(!msg\) \{ el\.hidden = true; el\.dataset\.tag = '';")

    def test_the_admit_banner_is_tagged(self):
        self.assertIn("""'bot-admit');""", APP_JS)

    def test_expire_only_touches_its_own_banner(self):
        block = APP_JS[APP_JS.index("function expireBanner()"):]
        block = block[:block.index("\n}")]
        self.assertIn("dataset.tag !== 'bot-admit'", block)
        self.assertIn("return", block)

    def test_expire_only_looks_at_our_own_jobs(self):
        # แอดมินเห็นคิวของทั้งระบบ งานของ tenant อื่นต้องไม่มาถอนแบนเนอร์ของเรา (BACKLOG #41)
        block = APP_JS[APP_JS.index("function expireBanner()"):]
        block = block[:block.index("\n}")]
        self.assertIn("isMine(j)", block)

    def test_it_runs_on_every_poll(self):
        # ไม่เจาะจงว่าอยู่บรรทัดไหนในฟังก์ชัน — ขอแค่ถูกเรียกทุกครั้งที่ได้สถานะใหม่
        # (ลำดับที่สำคัญจริงคือ 'หลัง state.jobs ถูกอัปเดต' ซึ่งมีเทสต์แยกข้างล่าง)
        nl = chr(10)
        body = APP_JS[APP_JS.index("async function pollJobs()"):]
        nxt = body.find(nl + 'async function ', 10)
        if nxt == -1:
            nxt = body.find(nl + 'function ', 10)
        self.assertGreater(nxt, 0, 'หาขอบล่างของ pollJobs ไม่เจอ')
        self.assertIn("expireBanner()", body[:nxt])

    def test_it_runs_after_the_jobs_are_updated(self):
        block = APP_JS[APP_JS.index("async function pollJobs()"):]
        self.assertLess(block.index("state.jobs = data.jobs"), block.index("expireBanner()"),
                        "ตรวจก่อนอัปเดตงาน = ใช้ข้อมูลรอบที่แล้ว ช้าไปหนึ่งจังหวะเสมอ")


if __name__ == "__main__":
    unittest.main()
