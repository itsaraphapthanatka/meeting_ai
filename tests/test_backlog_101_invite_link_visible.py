"""BACKLOG #101 — ลิงก์เชิญต้องเห็นด้วยตา ไม่ใช่อยู่แต่ในคลิปบอร์ด.

เจ้าของกด "เชิญสมาชิก" บน production (2026-10-22) ได้แบนเนอร์
*"คัดลอกลิงก์เชิญให้แล้ว — ส่งให้ได้เลย · ใช้ได้ 30 คน · หมดอายุใน 14 วัน"*
แล้วแจ้งว่า **"ไม่เห็นมี code"** — คลิปบอร์ดว่าง และบนหน้าจอไม่มีลิงก์ให้เห็นเลย

ต้นตอ: `inviteMember()` เรียก `await copyText(link)` แล้ว **ทิ้งค่าที่คืนมา**
ส่วน `copyText()` กลืน error ของ `navigator.clipboard` แล้วคืน `false` เงียบ ๆ
แบนเนอร์จึงประกาศว่าคัดลอกแล้วทุกครั้งแม้ไม่ได้คัดลอก · และตั้งแต่ #98 เอา
`console.log` ออก ลิงก์จึงไม่ได้ถูกแสดงที่ไหนอีกเลย

ทำไมคลิปบอร์ดถึงพลาด: ฟังก์ชันนี้เปิด `prompt()` ถามอีเมลและจำนวนคนก่อน แล้ว
`await` ไปอีกหนึ่งรอบ — พอถึงตอนเขียนคลิปบอร์ด เบราว์เซอร์อาจถือว่าเอกสารเสียโฟกัส
หรือหมด transient activation แล้ว (`NotAllowedError`)

**รหัสเชิญเก็บเป็น hash ฝั่งเซิร์ฟเวอร์ และไม่มี endpoint ให้ดูย้อนหลัง** — ค่าที่หลุดมือ
ตอนนั้นคือหายถาวร ใบที่ออกไปแล้วค้างกินโควตาจนหมดอายุ 14 วัน

วัดบนเบราว์เซอร์จริงหลังแก้ (เซิร์ฟเวอร์จาก git worktree)

    1280x860  ช่องลิงก์ 1083x37 · เลือกข้อความไว้ให้ครบ 55 ตัว · ช่อง/ปุ่มคัดลอก/ปุ่มปิด
              `elementFromPoint` กลางปุ่มได้ตัวเอง ทั้งสามอัน
    คลิกจริง  ปุ่มปิด -> box.hidden=true และ **ค่าในช่องถูกล้าง**
    390x812   ไม่มี horizontal scroll (scrollWidth 390 = clientWidth) ทั้งสามอันยังกดได้
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "meeting_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "meeting_ai" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestTheBannerTellsTheTruth(unittest.TestCase):
    """อาการที่เจ้าของเจอคือแบนเนอร์โกหก ไม่ใช่คลิปบอร์ดพัง."""

    def test_the_result_of_the_copy_is_actually_used(self):
        body = fn("async function inviteMember()")
        m = re.search(r"(const|let)\s+(\w+)\s*=\s*await copyText\(", body)
        self.assertIsNotNone(m, "ยังทิ้งค่าที่ copyText() คืนมาอยู่")
        self.assertIn(m.group(2), body[m.end():], "รับค่ามาแล้วไม่ได้เอาไปใช้ต่อ")

    def test_there_are_two_different_messages(self):
        """ทางสำเร็จกับทางล้มเหลวต้องพูดคนละอย่าง ไม่งั้นเท่ากับไม่ได้เช็ก."""
        body = fn("async function inviteMember()")
        said = re.findall(r"'([^']*(?:คัดลอก|ลิงก์เชิญ)[^']*)'", body)
        said += re.findall(r"`([^`]*(?:คัดลอก|ลิงก์เชิญ)[^`]*)`", body)
        self.assertGreaterEqual(len({s for s in said if s.strip()}), 2)

    def test_the_failure_message_does_not_claim_it_copied(self):
        body = fn("async function inviteMember()")
        fail = re.search(r"\?[^:]+:\s*'([^']+)'", body)
        self.assertIsNotNone(fail, "ไม่เจอข้อความของทางที่คัดลอกไม่สำเร็จ")
        self.assertNotIn("คัดลอกลิงก์เชิญให้แล้ว", fail.group(1))
        self.assertIn("คัดลอกจากช่อง", fail.group(1),
                      "ต้องบอกด้วยว่าให้ไปคัดลอกจากที่ไหน")


class TestTheLinkIsOnScreen(unittest.TestCase):
    """คลิปบอร์ดเป็นที่ที่มองไม่เห็น — ลิงก์ที่กู้ไม่ได้ต้องถูกกางให้เห็น.

    #103 เปลี่ยนชื่อกล่องจาก invite-* เป็น linkbox-* เพราะมีผู้ใช้สองราย
    (ลิงก์เชิญ กับ ลิงก์ตั้งรหัสผ่านใหม่) ข้อกำหนดเดิมไม่เปลี่ยน
    """

    def test_the_link_is_shown_every_time_not_only_on_failure(self):
        body = fn("async function inviteMember()")
        call = body.index("showLinkBox(")
        after = body[call:]
        self.assertNotIn("if (!copied)", body[:call],
                         "กางลิงก์เฉพาะตอนคัดลอกพลาด = ตรวจไม่ได้ว่าได้อะไรมา")
        self.assertIn("link", after[:60])

    def test_the_box_exists_in_the_markup(self):
        for ident in ("linkbox", "linkbox-link", "linkbox-copy", "linkbox-close",
                      "linkbox-note"):
            self.assertIn(f'id="{ident}"', HTML, f"ไม่มี #{ident} ในหน้า")

    def test_the_field_is_readonly(self):
        """แก้ค่าในช่องแล้วส่งต่อ = ส่งลิงก์ที่ใช้ไม่ได้ให้คนอื่น."""
        m = re.search(r'<input id="linkbox-link"[^>]*>', HTML)
        self.assertIsNotNone(m)
        self.assertIn("readonly", m.group(0))

    def test_every_id_the_code_touches_exists_in_the_markup(self):
        """บทเรียนจาก #99: เปลี่ยนชื่อ id ที่เดียวแล้วปุ่มกลายเป็นปุ่มตาย เทสต์ที่หา
        แค่สตริงจับไม่ได้ — ตรงนี้จึงไล่จากสิ่งที่โค้ด *เรียกจริง* ไปหามาร์กอัป
        """
        body = fn("function showLinkBox(")
        used = set(re.findall(r"\$\('#([\w-]+)'\)", body))
        self.assertTrue(used, "showLinkBox ไม่ได้อ้าง element ไหนเลย")
        for ident in used:
            self.assertIn(f'id="{ident}"', HTML, f"โค้ดเรียก #{ident} แต่ในหน้าไม่มี")

    def test_the_box_has_its_own_margin(self):
        """มันอยู่ระดับบนสุดของหน้า ไม่ได้อยู่ในพาเนลเหมือน .share-box เดิม."""
        self.assertRegex(CSS, r"#linkbox\s*\{[^}]*margin")


class TestClosingItClearsTheValue(unittest.TestCase):
    def test_closing_hides_and_clears(self):
        """ซ่อนอย่างเดียวไม่พอ — ลิงก์เชิญที่ค้างในหน้าคือของที่คนเดินผ่านอ่านได้."""
        body = fn("function showLinkBox(")
        m = re.search(r"linkbox-close'\)\.onclick = \(\) => \{([^}]*)\}", body)
        self.assertIsNotNone(m, "ปุ่มปิดไม่ได้ถูกผูกเหตุการณ์")
        self.assertIn("hidden = true", m.group(1))
        self.assertRegex(m.group(1), r"value = ''")

    def test_the_copy_button_can_be_used_again(self):
        """คนที่คัดลอกทับไปแล้วต้องเอากลับมาได้ โดยไม่ต้องออกรหัสใบใหม่."""
        body = fn("function showLinkBox(")
        m = re.search(r"linkbox-copy'\)\.onclick = async \(\) => \{(.*?)\n  \};",
                      body, re.S)
        self.assertIsNotNone(m, "ปุ่มคัดลอกไม่ได้ถูกผูกเหตุการณ์")
        self.assertIn("copyText(", m.group(1))


class TestTheManualMatchesWhatItDoes(unittest.TestCase):
    def test_the_help_no_longer_promises_only_the_clipboard(self):
        """คู่มือที่บอกว่า "คัดลอกให้ทันที" อย่างเดียว คือสิ่งที่ทำให้คนไปหาผิดที่."""
        spot = HTML[HTML.index("<b>เชิญสมาชิก</b>"):][:300]
        self.assertIn("ลิงก์เชิญ", spot)
        self.assertNotIn("ระบบออกรหัสเชิญและคัดลอกให้ทันที", HTML)


if __name__ == "__main__":
    unittest.main()
