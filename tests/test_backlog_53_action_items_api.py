"""BACKLOG #53 ขั้นที่ 2 — เส้น API ของ action items.

`PATCH /api/meetings/{id}/action-items/{item_id}` — `{"done": bool}` และ/หรือ `{"assignee": str}`
`DELETE` เส้นเดียวกัน — ลบรายการ (ใช้เก็บกวาด orphan ที่ระบบไม่ยอมลบให้เอง)

**สิทธิ์ไม่ได้เขียนใหม่โดยตั้งใจ** — เส้นนี้วาง *หลัง* บล็อกตรวจสิทธิ์ใน `_meeting()`
ตามกติกาใน CLAUDE.md จึงได้ `_may_write()` ตัวเดียวกับที่ใช้ตอนเกลาสรุปมาฟรี ๆ
ซึ่งเป็นคำตอบของ PRD 6.3 (คนถือลิงก์แชร์แบบแก้ได้ ติ๊กได้) ถ้าวันหนึ่งอยากให้เข้มกว่านั้น
ต้องไปแยกเงื่อนไขที่บล็อกนั้น ไม่ใช่ที่ handler — ไฟล์นี้มีเทสต์ปักหมุดตำแหน่งไว้ด้วย

อ้างด้วย `id` ของระเบียน ไม่ใช่ลำดับในลิสต์: ลำดับขยับทุกครั้งที่สรุปถูกเขียนใหม่
ถ้าผู้ใช้เปิดหน้าค้างไว้แล้วงานสรุปใหม่เพิ่งจบ การกดติ๊กจะไปโดนคนละรายการ
"""

from __future__ import annotations

import unittest
from pathlib import Path

from _harness import CloudCase, LocalCase, new_mid

ROOT = Path(__file__).resolve().parents[1]
SERVER_SRC = (ROOT / "meeting_ai" / "web" / "server.py").read_text(encoding="utf-8")

HEADING = "## 📋 สิ่งที่ต้องทำต่อ (Action Items)"


def summary_with(rows) -> str:
    body = "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)
    return (HEADING + "\n| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |\n|---|---|---|\n"
            + body + "\n")


SUMMARY = summary_with([("ทำสไลด์", "สมชาย", "ศุกร์"), ("ส่งรายงาน", "", "")])


class TestTheEndpointInFileMode(LocalCase):

    def setUp(self):
        super().setUp()
        self.mid = new_mid()
        self.store_mod = __import__("meeting_ai.web.store", fromlist=["store"])
        self.store_mod.create(mid=self.mid, title="ประชุม", audio_name="a.wav",
                              source="upload", language="th", duration=1.0,
                              segments=[], summary=SUMMARY)
        self.items = self.store_mod.get(self.mid)["action_items"]

    def _url(self, item_id: str) -> str:
        return f"/api/meetings/{self.mid}/action-items/{item_id}"

    def test_the_meeting_payload_carries_them(self):
        status, body, _ = self.get(f"/api/meetings/{self.mid}")
        self.assertEqual(status, 200)
        self.assertEqual([i["text"] for i in body["action_items"]], ["ทำสไลด์", "ส่งรายงาน"])

    def test_ticking_one(self):
        status, body, _ = self.patch_json(self._url(self.items[0]["id"]), {"done": True})
        self.assertEqual(status, 200, body)
        hit = next(i for i in body["action_items"] if i["id"] == self.items[0]["id"])
        self.assertTrue(hit["done"])

    def test_unticking_one(self):
        self.patch_json(self._url(self.items[0]["id"]), {"done": True})
        _, body, _ = self.patch_json(self._url(self.items[0]["id"]), {"done": False})
        self.assertFalse(body["action_items"][0]["done"])

    def test_setting_an_assignee_flags_it_as_edited(self):
        status, body, _ = self.patch_json(self._url(self.items[1]["id"]),
                                          {"assignee": "  สมศรี  "})
        self.assertEqual(status, 200, body)
        hit = next(i for i in body["action_items"] if i["id"] == self.items[1]["id"])
        self.assertEqual(hit["assignee"], "สมศรี", "ต้องตัดช่องว่างหัวท้าย")
        self.assertTrue(hit["assignee_edited"],
                        "ไม่ตั้งธง = สรุปใหม่ครั้งหน้าจะลบสิ่งที่คนพิมพ์ทิ้ง")

    def test_deleting_one(self):
        status, body, _ = self.delete(self._url(self.items[0]["id"]))
        self.assertEqual(status, 200, body)
        self.assertNotIn(self.items[0]["id"], [i["id"] for i in body["action_items"]])

    def test_deleting_twice_is_a_404(self):
        self.delete(self._url(self.items[0]["id"]))
        status, _, _ = self.delete(self._url(self.items[0]["id"]))
        self.assertEqual(status, 404)

    def test_a_tick_survives_a_new_summary(self):
        """เส้นทางเต็มของ AC-4.1 ผ่าน HTTP จริง ไม่ใช่เรียก store ตรง ๆ."""
        self.patch_json(self._url(self.items[0]["id"]), {"done": True})
        self.patch_json(f"/api/meetings/{self.mid}",
                        {"summary": summary_with([("ทำสไลด์", "สมชาย", "จันทร์")])})
        _, body, _ = self.get(f"/api/meetings/{self.mid}")
        self.assertTrue(body["action_items"][0]["done"], "สรุปใหม่แล้วเครื่องหมายถูกหาย")


class TestItRejectsBadInput(LocalCase):

    def setUp(self):
        super().setUp()
        self.mid = new_mid()
        store = __import__("meeting_ai.web.store", fromlist=["store"])
        store.create(mid=self.mid, title="ประชุม", audio_name="a.wav", source="upload",
                     language="th", duration=1.0, segments=[], summary=SUMMARY)
        self.item_id = store.get(self.mid)["action_items"][0]["id"]

    def _url(self, item_id: str) -> str:
        return f"/api/meetings/{self.mid}/action-items/{item_id}"

    def test_a_malformed_item_id_is_400_not_404(self):
        # มาจาก URL เหมือน mid — ต้องตรวจรูปแบบก่อนเอาไปค้น
        # ตัวพิมพ์ใหญ่ก็ไม่ผ่าน — token_hex() ให้ตัวเล็กเสมอ รับกว้างกว่าที่สร้างเองไม่มีประโยชน์
        for bad in ("../../etc", "zzzz", "0123456789ab0", "0123456789AB", "0123456789a"):
            with self.subTest(bad=bad):
                status, _, _ = self.patch_json(self._url(bad), {"done": True})
                self.assertIn(status, (400, 404))

    def test_an_unknown_item_is_404(self):
        status, _, _ = self.patch_json(self._url("0123456789ab"), {"done": True})
        self.assertEqual(status, 404)

    def test_an_empty_body_is_400(self):
        status, body, _ = self.patch_json(self._url(self.item_id), {})
        self.assertEqual(status, 400)
        self.assertIn("done", str(body))

    def test_done_must_be_a_boolean(self):
        status, _, _ = self.patch_json(self._url(self.item_id), {"done": "yes"})
        self.assertEqual(status, 400)

    def test_assignee_must_be_a_string(self):
        status, _, _ = self.patch_json(self._url(self.item_id), {"assignee": 5})
        self.assertEqual(status, 400)

    def test_other_methods_are_405(self):
        status, _, _ = self.post_json(self._url(self.item_id), {"done": True})
        self.assertEqual(status, 405)

    def test_an_unknown_meeting_is_not_a_500(self):
        status, _, _ = self.patch_json(
            f"/api/meetings/20260101-000000-abcdef/action-items/{self.item_id}",
            {"done": True})
        self.assertIn(status, (403, 404))


class TestPermissionsComeFromTheSharedBlock(CloudCase):
    """PRD 6.3 — สิทธิ์ต้องเป็นชุดเดียวกับการเกลาสรุป ไม่ใช่กติกาใหม่ของ handler นี้."""

    def test_a_stranger_cannot_touch_someone_elses_items(self):
        status, _, _ = self.patch_json(
            f"/api/meetings/{self.M1}/action-items/0123456789ab",
            {"done": True}, cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)

    def test_without_a_session_it_is_not_allowed(self):
        status, _, _ = self.patch_json(
            f"/api/meetings/{self.M1}/action-items/0123456789ab", {"done": True})
        self.assertIn(status, (401, 403))

    def test_the_owner_gets_past_the_permission_block(self):
        # ไม่มีรายการจริง จึงคาด 404 — สำคัญคือ "ไม่ใช่ 403"
        status, _, _ = self.patch_json(
            f"/api/meetings/{self.M1}/action-items/0123456789ab",
            {"done": True}, cookies={"mai_session": self.tokA})
        self.assertEqual(status, 404)


class TestTheRouteIsWiredWhereItShouldBe(unittest.TestCase):
    """CLAUDE.md: เส้นย่อยใหม่ของ _meeting() วาง **หลัง** บล็อกตรวจสิทธิ์."""

    def test_the_route_sits_after_the_permission_check(self):
        """ยึดบรรทัดที่ตัดสินสิทธิ์จริง ไม่ใช่ข้อความ error.

        เวอร์ชันแรกยึด "ไม่มีสิทธิ์กับการประชุมนี้" ซึ่งโผล่ในบล็อก draft ที่อยู่ก่อนหน้าด้วย
        พอย้ายเส้นไปวางผิดที่จริง ๆ เทสต์นี้กลับเขียว (จับได้จากเทสต์พฤติกรรมข้างบนแทน)
        """
        body = SERVER_SRC[SERVER_SRC.index("def _meeting(self"):]
        body = body[:body.index("def _action_item(self")]
        guard = body.index("allowed = self._may_write(mid) if writing else self._may_read(mid)")
        route = body.index('rest[0] == "action-items"')
        self.assertLess(guard, route,
                        "วางก่อนบล็อกตรวจสิทธิ์ = ใครก็ติ๊กของคนอื่นได้")

    def test_it_is_not_public(self):
        pub = SERVER_SRC[SERVER_SRC.index("PUBLIC_API"):]
        pub = pub[:pub.index("\n\n")]
        self.assertNotIn("action-items", pub)

    def test_the_item_id_is_validated_against_a_pattern(self):
        self.assertIn("_ITEM_ID_RE", SERVER_SRC)
        body = SERVER_SRC[SERVER_SRC.index("def _action_item(self"):]
        body = body[:body.index("def _patch(self")]
        self.assertLess(body.index("_ITEM_ID_RE.fullmatch"), body.index("store."),
                        "ต้องตรวจรูปแบบก่อนเอาไปค้น")


if __name__ == "__main__":
    unittest.main()
