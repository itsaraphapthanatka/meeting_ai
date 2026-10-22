"""BACKLOG #98 — ลิงก์เชิญรายคน: เปิดแล้วต้องเจอหน้าสมัคร ไม่ใช่หน้าล็อกอิน.

ผู้ใช้แจ้งมาว่า “หลายคนยังใช้โค้ดเข้า meeting AI ไม่ได้” และถามว่าเจนโค้ดรายคนแบบตอน
เชื่อมบัญชีไลน์ได้ไหม — ไปวัดของจริงแล้วพบว่า**โค้ดไม่ได้มีปัญหา ลิงก์พาไปผิดหน้าต่างหาก**

วัดบนเบราว์เซอร์จริง (viewport 1280x860) เปิด `/?invite=CODE` บนระบบที่มีคนอยู่แล้ว:

    ก่อนแก้   หัวข้อ "เข้าสู่ระบบ" · ช่องรหัสเชิญ hidden=true display:none · ค่าว่าง
              ทางไปต่อมีแค่ลิงก์เล็ก ๆ ท้ายฟอร์ม "ได้รับรหัสเชิญมา? สมัครสมาชิก"
    หลังแก้   หัวข้อ "สมัครสมาชิก" · ช่องรหัสเชิญเห็น 350x71 · ค่าถูกเติมเป็นโค้ดใน URL
              และเปิดเว็บเฉย ๆ (ไม่มีโค้ด) ยังเป็นหน้า "เข้าสู่ระบบ" เหมือนเดิม

และต้นตอของคำถาม “เจนโค้ดรายคนได้ไหม”: **โค้ดสร้างลิงก์รายคนไว้อยู่แล้ว** แต่บรรทัดนั้น
ส่งเข้า `console.log` ส่วนคลิปบอร์ดได้แค่รหัสเปล่า คนกดเชิญจึงไม่เคยเห็นลิงก์
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "meeting_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")


def code_only(body: str) -> str:
    """ตัดบรรทัดคอมเมนต์ออก.

    กับดักที่โดนมาสามรอบในตั๋วชุดนี้: `assertNotIn("x", body)` จับคอมเมนต์ที่เขียน
    อธิบายว่า "ถอด x ออกไปแล้วเพราะ..." ได้ด้วย — คอมเมนต์ที่ดีจึงทำให้เทสต์ล้ม
    ซึ่งเป็นแรงจูงใจผิดทาง (เคยเจอกับ README, ข้อความ prompt และ console.log)
    """
    out = []
    for line in body.split(chr(10)):
        stripped = line.strip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        out.append(line)
    return chr(10).join(out)


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestTheLinkOpensTheRightForm(unittest.TestCase):

    def test_an_invite_in_the_url_means_signup(self):
        body = fn("function showAuth(mode)")
        decide = body[:body.index("document.body.classList")]
        self.assertIn("inviteFromUrl()", decide,
                      "หน้าที่เปิดไม่ได้ดูเลยว่ามีรหัสเชิญติดมากับ URL ไหม")
        self.assertIn("state.firstRun", decide, "คนแรกของระบบต้องยังได้หน้าสมัครเหมือนเดิม")

    def test_a_plain_visit_still_lands_on_login(self):
        """คนที่เข้ามาเฉย ๆ ต้องไม่ถูกพาไปหน้าสมัคร — ไม่งั้นคนมีบัญชีอยู่แล้วจะงง."""
        body = fn("function showAuth(mode)")
        decide = body[:body.index("document.body.classList")]
        self.assertIn("mode === undefined &&", decide,
                      "เงื่อนไขต้องยังผูกกับ mode ที่ผู้เรียกส่งมา")

    def test_the_code_is_filled_in_for_them(self):
        body = fn("function showAuth(mode)")
        self.assertRegex(body, r"if \(fromUrl && signup\) \$\('#a-invite'\)\.value = fromUrl")

    def test_one_reader_for_the_url_parameter(self):
        """อ่านสองที่แล้วแก้ที่เดียวคือทางที่เคยพลาดมาแล้ว — ให้เหลือตัวเดียว."""
        self.assertEqual(APP_JS.count("new URLSearchParams(location.search).get('invite')"), 1)
        self.assertIn("const inviteFromUrl =", APP_JS)

    def test_the_form_really_has_those_fields(self):
        """ถ้าเปลี่ยน id ของช่องเมื่อไหร่ เทสต์ข้างบนจะยังเขียวแต่หน้าจอพัง."""
        for el in ('id="a-invite"', 'id="a-invite-wrap"', 'id="a-switch"'):
            with self.subTest(el=el):
                self.assertIn(el, HTML)


class TestTheAdminGetsALinkNotABareCode(unittest.TestCase):

    def test_the_clipboard_gets_the_link(self):
        """ของเดิมคัดลอก out.code — คนรับได้รหัสเปล่าที่ไม่รู้ว่าเอาไปใส่ตรงไหน."""
        body = code_only(fn("async function inviteMember()"))
        self.assertIn("await copyText(link);", body)
        self.assertNotIn("await copyText(out.code);", body)

    def test_the_link_points_at_this_site(self):
        body = fn("async function inviteMember()")
        self.assertIn("${location.origin}/?invite=${encodeURIComponent(out.code)}", body)

    def test_the_link_is_not_buried_in_the_console(self):
        """บรรทัด console.log คือเหตุผลที่ฟีเจอร์นี้มีอยู่แต่ไม่มีใครรู้."""
        body = code_only(fn("async function inviteMember()"))
        self.assertNotIn("console.log", body)

    def test_the_banner_says_what_was_copied(self):
        # เจาะบรรทัดข้อความของทางสำเร็จ — หาทั้งฟังก์ชันจะไปเจอข้อความใน prompt
        # ที่อยู่ด้านบน ซึ่งพูดถึง 14 วันเหมือนกัน (มุตันต์รอดมาแล้วครั้งหนึ่ง)
        # #101 แยกข้อความสำเร็จ/ล้มเหลวเป็น ternary ข้อความจึงไม่ได้ติดกับ `banner(` แล้ว
        body = code_only(fn("async function inviteMember()"))
        line = next(l for l in body.split(chr(10)) if "คัดลอกลิงก์เชิญให้แล้ว" in l)
        self.assertIn("ลิงก์เชิญ", line)
        self.assertIn("14 วัน", line)

    def test_it_still_reports_the_quota_from_the_server(self):
        """มาจากคำตอบของเซิร์ฟเวอร์ ไม่ใช่จากที่หน้าเว็บขอไป (BACKLOG #95)."""
        body = fn("async function inviteMember()")
        self.assertIn("out.max_uses", body)


if __name__ == "__main__":
    unittest.main()
