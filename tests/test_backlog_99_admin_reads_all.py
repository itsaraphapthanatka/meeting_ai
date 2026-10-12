"""BACKLOG #99 — แอดมินดูการประชุมของผู้ใช้คนอื่นได้ทั้งหมด.

เจ้าของสั่งเมื่อ 2026-10-12 ว่า **“ดูได้ทั้งหมด”** หลังผมเสนอขอบเขตที่แคบกว่า (เห็นแต่รายชื่อ
ไม่เปิดอ่านเนื้อหา) — นี่คือ**การเพิ่มการมองเห็นจริง** ไม่ใช่ของที่มีอยู่แล้ว ก่อนหน้านี้
`_jobs_view()` เขียนกำกับไว้เองว่า *“แอดมินเห็นคิวทั้งระบบ แต่รายการการประชุมมีแค่ของตัวเอง”*

สิ่งที่ตั๋วนี้ต้องรักษาไว้ให้ได้คือ **เส้นแบ่งระหว่างอ่านกับเขียน**: `admin-read` เป็นระดับ
ของตัวเอง ไม่ใช่ `owner`/`team` เพราะ `_may_write()` เช็กรายชื่อระดับที่เขียนได้แบบระบุชื่อ
ถ้าวันหนึ่งมีคนเผลอคืน `"owner"` ให้แอดมินแทน แอดมินจะลบการประชุมของคนอื่นได้ทันที

และเพราะเป็นอำนาจที่กว้าง **การเปิดดูของคนอื่นต้องมีร่องรอย** — ลง log ของเซิร์ฟเวอร์เสมอ
"""

from __future__ import annotations

import json
import re
import unittest
from unittest import mock
from pathlib import Path

from _harness import CloudCase, LocalCase          # type: ignore

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
SERVER = (ROOT / "meeting_ai" / "web" / "server.py").read_text(encoding="utf-8")
HELP = (ROOT / "meeting_ai" / "web" / "static" / "index.html").read_text(encoding="utf-8")


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestWhoMayListSomeoneElsesMeetings(CloudCase):

    def path(self, uid: str | None = None) -> str:
        return f"/api/admin/users/{uid or self.uid_a}/meetings"

    def test_anonymous_is_rejected(self):
        status, body, _ = self.get(self.path())
        self.assertEqual(status, 401)
        self.assertNotIn("Meeting 1", json.dumps(body, ensure_ascii=False))

    def test_a_logged_in_non_admin_is_rejected(self):
        status, _, _ = self.get(self.path(), cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)

    def test_the_rejection_leaks_no_titles(self):
        _, body, _ = self.get(self.path(), cookies={"mai_session": self.tokB})
        self.assertNotIn("Meeting 1", json.dumps(body, ensure_ascii=False))

    def test_the_owner_themselves_is_still_not_an_admin(self):
        """เจ้าของข้อมูลก็ใช้เส้นนี้ไม่ได้ — เป็นเส้นของแอดมิน ไม่ใช่เส้นดูของตัวเอง."""
        status, _, _ = self.get(self.path(), cookies={"mai_session": self.tokA})
        self.assertEqual(status, 403)

    def test_a_share_link_holder_is_rejected(self):
        status, _, _ = self.get(self.path(), cookies={"mai_share": self.shr1})
        self.assertIn(status, (401, 403))

    def test_an_admin_sees_the_private_meetings_too(self):
        status, body, _ = self.get(self.path(), cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200, body)
        titles = [m["title"] for m in body["meetings"]]
        self.assertIn("Meeting 1", titles)

    def test_it_only_lists_that_persons_meetings(self):
        _, body, _ = self.get(self.path(), cookies={"mai_session": self.tokAdm})
        titles = [m["title"] for m in body["meetings"]]
        self.assertNotIn("Meeting 2", titles, "หลุดการประชุมของคนอื่นเข้ามา")

    def test_a_malformed_user_id_is_refused(self):
        # ไม่ใส่อักขระที่ http.client ปฏิเสธเองตั้งแต่ต้นทาง (เช่นช่องว่าง)
        # เพราะนั่นเป็นการทดสอบไลบรารี ไม่ใช่ทดสอบด่านของเรา
        for bad in ("..%2F..%2Fetc", "a" * 100, "abc!def", "a.b"):
            with self.subTest(uid=bad):
                status, _, _ = self.get(self.path(bad), cookies={"mai_session": self.tokAdm})
                self.assertIn(status, (400, 404))

    def test_an_unknown_user_is_an_empty_list_not_an_error(self):
        _, body, _ = self.get(self.path("uid-nobody"), cookies={"mai_session": self.tokAdm})
        self.assertEqual(body.get("meetings"), [])


class TestTheAdminMayReadButNotWrite(CloudCase):
    """เส้นแบ่งที่สำคัญที่สุดของตั๋วนี้."""

    def test_an_admin_can_open_someone_elses_private_meeting(self):
        status, body, _ = self.get(f"/api/meetings/{self.M1}",
                                   cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200, body)

    def test_a_normal_user_still_cannot(self):
        """ของเดิมต้องไม่พัง — คนทั่วไปยังเปิดของคนอื่นไม่ได้."""
        status, _, _ = self.get(f"/api/meetings/{self.M1}",
                                cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)

    def test_an_admin_cannot_delete_someone_elses_meeting(self):
        status, _, _ = self.delete(f"/api/meetings/{self.M1}",
                                   cookies={"mai_session": self.tokAdm})
        self.assertNotEqual(status, 200)
        self.assertIsNotNone(self.store.get(self.M1), "การประชุมถูกลบไปจริง")

    def test_an_admin_cannot_edit_someone_elses_meeting(self):
        status, _, _ = self.patch_json(f"/api/meetings/{self.M1}",
                                       {"title": "โดนแก้"},
                                       cookies={"mai_session": self.tokAdm})
        self.assertNotEqual(status, 200)
        self.assertEqual(self.store.get(self.M1)["title"], "Meeting 1")

    def test_a_meeting_that_does_not_exist_is_still_404_for_an_admin(self):
        """สิทธิ์แอดมินต้องผูกกับแถวที่มีจริง ไม่ใช่กับการล็อกอิน.

        ถ้าคืน admin-read ให้ทุก id ที่ถูกถาม ด่าน `_may_read` จะผ่านแล้วไปพังที่ชั้นล่าง
        แทนที่จะตอบ 404 ตรง ๆ — และร่องรอยการเข้าดูจะถูกเขียนสำหรับ id ที่ไม่มีอยู่ด้วย
        """
        from meeting_ai.web import server as srv
        with mock.patch.object(srv.log, "info") as spy:
            status, _, _ = self.get("/api/meetings/20260101-000000-abcdef",
                                    cookies={"mai_session": self.tokAdm})
        self.assertNotEqual(status, 200)
        # ของจริงตอบ 403 (ไม่บอกด้วยซ้ำว่า id นั้นมีจริงไหม) ซึ่งดีกว่า 404
        # สิ่งที่ต้องตรึงคือ **ร่องรอยต้องไม่ถูกเขียนสำหรับของที่ไม่มีอยู่**
        said = " ".join(str(c) for c in spy.call_args_list)
        self.assertNotIn("[admin-read]", said)

    def test_the_level_is_its_own_not_owner(self):
        """ถ้าคืน owner/team แทน แอดมินจะได้สิทธิ์เขียนไปด้วยทันที."""
        body = SERVER[SERVER.index("def _level"):]
        body = body[:body.index("def _may_read")]
        self.assertIn('return "admin-read"', body)
        self.assertNotIn('return "owner"', body)
        write = SERVER[SERVER.index("def _may_write(self"):]
        write = write[:write.index("def ", 10)]
        self.assertNotIn("admin-read", write)


class TestItLeavesATrail(CloudCase):
    """อำนาจที่ไม่มีใครตรวจสอบได้คือของที่ย้อนกลับมาเจ็บทีหลัง."""

    def test_reading_someone_elses_meeting_is_logged(self):
        with self.assertLogs("meeting_ai.web.server", level="INFO") as caught:
            self.get(f"/api/meetings/{self.M1}", cookies={"mai_session": self.tokAdm})
        blob = "\n".join(caught.output)
        self.assertIn("[admin-read]", blob)
        self.assertIn(self.M1, blob)

    def test_reading_your_own_meeting_is_not_logged(self):
        """ไม่งั้น log จะเต็มไปด้วยเรื่องปกติจนหาเรื่องผิดปกติไม่เจอ.

        ใช้ mock แทน assertLogs เพราะ assertLogs **ล้มเองเมื่อไม่มี log เลย**
        ซึ่งเป็นผลลัพธ์ที่เทสต์นี้ต้องการพอดี
        """
        from meeting_ai.web import server as srv
        with mock.patch.object(srv.log, "info") as spy:
            self.get(f"/api/meetings/{self.M1}", cookies={"mai_session": self.tokA})
        said = " ".join(str(c) for c in spy.call_args_list)
        self.assertNotIn("[admin-read]", said)

    def test_listing_is_logged_too(self):
        with self.assertLogs("meeting_ai.web.server", level="INFO") as caught:
            self.get(f"/api/admin/users/{self.uid_a}/meetings",
                     cookies={"mai_session": self.tokAdm})
        self.assertIn("[admin-read]", "\n".join(caught.output))


class TestItDoesNotBreakTheSystemQueueBadge(CloudCase):
    """เจอตอนรันสวีทเต็ม ไม่ใช่ตอนเขียนเทสต์ของตั๋วนี้.

    ป้าย “คิวระบบ” (BACKLOG #41) มีไว้ให้แอดมินแยกงานของตัวเองออกจากงานคนอื่นด้วยตา
    พอแอดมินอ่านได้ทุกการประชุม งานของคนอื่นเลยถูกนับเป็น “ของฉัน” ทั้งหมด
    ซึ่งทำลายจุดประสงค์ของป้ายนั้นทั้งอัน — และ `_job_is_mine()` เองก็เขียน docstring
    ไว้ว่า “กติกาเดียวกับคนทั่วไป **โดยไม่นับสิทธิ์แอดมิน**” อยู่แล้ว
    """

    def test_another_persons_job_is_still_not_mine_for_an_admin(self):
        _, body, _ = self.get("/api/jobs", cookies={"mai_session": self.tokAdm})
        flags = {j["id"]: j.get("mine") for j in body["jobs"]}
        self.assertIn(self.J_A, flags)
        self.assertFalse(flags[self.J_A], "งานของคนอื่นถูกนับเป็นของแอดมิน")

    def test_the_translate_job_of_someone_elses_meeting_too(self):
        """งานที่ spec ไม่มีเจ้าของ ตกไปใช้สิทธิ์อ่านของการประชุม — จุดที่พังพอดี."""
        _, body, _ = self.get("/api/jobs", cookies={"mai_session": self.tokAdm})
        flags = {j["id"]: j.get("mine") for j in body["jobs"]}
        self.assertFalse(flags.get(self.M1_tr_en, True))

    def test_rendering_the_queue_does_not_flood_the_audit_log(self):
        """หน้าเว็บ poll คิวทุก 1.5 วินาที — ถ้าทุกงานลงร่องรอย จะหาของจริงไม่เจอ."""
        from meeting_ai.web import server as srv
        with mock.patch.object(srv.log, "info") as spy:
            self.get("/api/jobs", cookies={"mai_session": self.tokAdm})
        said = " ".join(str(c) for c in spy.call_args_list)
        self.assertNotIn("[admin-read]", said)


class TestFileModeHasNoSuchThing(LocalCase):
    def test_not_found_rather_than_crash(self):
        status, body, _ = self.get("/api/admin/users/anyone/meetings")
        self.assertEqual(status, 404)
        self.assertNotIn("Traceback", json.dumps(body, ensure_ascii=False))


class TestTheScreen(unittest.TestCase):

    def test_rows_are_clickable_and_keyboard_reachable(self):
        body = fn("function renderUsers(")
        self.assertIn('class="u-row', body)
        self.assertIn("tabindex=", body)
        self.assertIn("onkeydown", body)

    def test_the_handler_is_bound_to_the_table_not_each_row(self):
        """แถวถูกวาดใหม่ทุกครั้งที่โหลด ผูกทีละแถวจะพอกพูน.

        ตรวจว่ามันเรียกอะไรจริง ๆ ด้วย — เวอร์ชันแรกดูแค่ว่ามีคำว่า "table.onclick"
        ซึ่ง `table.onclick = null` ก็ผ่าน (เจอตอนกลับกลไก)
        """
        body = fn("function renderUsers(")
        m = re.search(r"table\.onclick = ([^;]+);", body)
        self.assertIsNotNone(m, "ไม่ได้ผูก onclick ไว้ที่ตาราง")
        self.assertIn("open(", m.group(1))
        self.assertIn("showUserMeetings(", body)

    def test_everything_shown_is_escaped(self):
        body = fn("async function showUserMeetings(")
        for field in ("m.title", "m.id"):
            with self.subTest(field=field):
                self.assertIn(f"esc({field})", body)
        self.assertIn("Number(m.segments", body)

    def test_it_says_the_visit_is_recorded(self):
        """บอกแอดมินตรง ๆ ว่าการเปิดดูถูกบันทึก — ดีกว่าให้รู้ทีหลัง."""
        body = fn("async function showUserMeetings(")
        self.assertIn("บันทึกไว้ที่เซิร์ฟเวอร์", body)

    def test_there_is_a_way_back(self):
        """id ในมาร์กอัปกับ id ที่ผูกเหตุการณ์ต้องเป็นตัวเดียวกัน.

        เปลี่ยนชื่อ id ในมาร์กอัปอย่างเดียวแล้ว `$('#um-back')` จะได้ null
        ปุ่มกลับกลายเป็นปุ่มตาย — เทสต์เดิมที่หาแค่คำว่า "um-back" จับไม่ได้
        """
        body = fn("async function showUserMeetings(")
        in_markup = re.search(r'<button id="([\w-]+)" class="btn btn-sm"', body)
        self.assertIsNotNone(in_markup, "ไม่เจอปุ่มกลับในมาร์กอัป")
        self.assertIn(f"$('#{in_markup.group(1)}').onclick", body)
        self.assertIn("showUsers()", body)

    def test_private_meetings_are_marked(self):
        body = fn("async function showUserMeetings(")
        self.assertIn("เฉพาะฉัน", body)
        self.assertIn("ทั้งทีม", body)


class TestTheManualNoLongerPromisesPrivacyItCannotKeep(unittest.TestCase):
    """คู่มือเคยบอกผู้ใช้ว่า 'คนอื่นในทีมไม่เห็น' ซึ่งตอนนี้ไม่จริงสำหรับแอดมิน."""

    def test_it_says_admins_can_see(self):
        tpl = HELP[HELP.index('<template id="tpl-help">'):]
        tpl = tpl[:tpl.index("</template>")]
        self.assertIn("แอดมิน", tpl)
        self.assertRegex(tpl, r"แอดมิน[^<]*เปิดดู|เปิดดู[^<]*แอดมิน|แอดมิน[^<]*เห็น")


if __name__ == "__main__":
    unittest.main()
