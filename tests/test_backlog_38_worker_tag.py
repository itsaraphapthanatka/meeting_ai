"""BACKLOG #38 — worker tag ต้องไม่ชนกัน ไม่งั้น worker ตัวหนึ่งไปหยุดบอทของอีกตัวกลางห้องประชุม.

`worker_tag()` เดิมตัด slug ที่ 16 ตัวอักษรแล้วใช้ hash **เฉพาะตอน slug ว่าง** ผลคือ
ชื่อที่ต่างกันหลังตัวที่ 16 ได้ tag เดียวกัน — และชื่อที่ชนกันจริงคือชื่อยูนิตตามเอกสาร deploy เอง:

    meeting-ai-worker-01  ->  meetingaiworker0
    meeting-ai-worker-02  ->  meetingaiworker0

ความเสียหาย: `cleanup_stale()` **สั่ง docker stop** ไม่ใช่แค่ลบของเก่า worker ตัวที่เพิ่งเริ่ม
จึงไปหยุดบอทของอีกตัว **ที่กำลังนั่งอยู่ในห้องประชุมจริง** — บอทออกจากห้องกลางคัน ไฟล์เสียง
ขาด และไม่มีใครรู้ว่าทำไม ส่วนโฟลเดอร์พักมีด่าน `live` กับ "wav ไม่ว่าง" ช่วยไว้บางส่วน
(ตามที่ตั๋วเขียนว่า round-2 live check mitigates dirs only) แต่ container ไม่มีอะไรกัน

แก้โดยต่อ hash ของ **ชื่อเต็ม** ไว้เสมอ ความไม่ซ้ำจึงไม่ขึ้นกับว่า slug ถูกตัดตรงไหน
"""

from __future__ import annotations

import hashlib
import re
import unittest

from meeting_ai import bot

# ชื่อที่ใช้จริงในเอกสาร deploy และในเครื่องของเจ้าของ
REAL_NAMES = [
    "meeting-ai-worker-01", "meeting-ai-worker-02", "meeting-ai-worker-03",
    "edgexpert-1346", "msi-edgexpert-04dc", "DESKTOP-S8J37O3",
]


def _old_tag(worker: str) -> str:
    """อัลกอริทึมเดิมก่อนแก้ — ไว้ยืนยันว่าบั๊กมีอยู่จริง ไม่ใช่กังวลลอย ๆ."""
    slug = re.sub(r"[^A-Za-z0-9]", "", worker or "")[:16]
    return slug or hashlib.md5((worker or "solo").encode("utf-8")).hexdigest()[:8]


class TestTheBugWasReal(unittest.TestCase):

    def test_the_old_algorithm_really_did_collide(self):
        self.assertEqual(_old_tag("meeting-ai-worker-01"),
                         _old_tag("meeting-ai-worker-02"),
                         "ถ้าอันนี้ไม่ชน แปลว่าเข้าใจบั๊กผิดตั้งแต่ต้น")


class TestWorkerTagIsUnique(unittest.TestCase):

    def test_the_reported_collision_is_gone(self):
        self.assertNotEqual(bot.worker_tag("meeting-ai-worker-01"),
                            bot.worker_tag("meeting-ai-worker-02"))

    def test_names_that_differ_only_after_the_slug_cut_still_differ(self):
        # slug ถูกตัดที่ 12 ตัว — ชื่อที่ต่างกันหลังจากนั้นต้องยังแยกออก
        a = "worker-aaaaaaaaaaaaaaaaaaaa-1"
        b = "worker-aaaaaaaaaaaaaaaaaaaa-2"
        self.assertEqual(re.sub(r"[^A-Za-z0-9]", "", a)[:12],
                         re.sub(r"[^A-Za-z0-9]", "", b)[:12], "ตั้งใจให้ slug เท่ากัน")
        self.assertNotEqual(bot.worker_tag(a), bot.worker_tag(b))

    def test_no_collisions_across_many_names(self):
        # ตัดชื่อซ้ำออกก่อน — ชื่อเดียวกันต้องได้ tag เดียวกัน นั่นคือคุณสมบัติที่ต้องการ
        names = sorted({*REAL_NAMES, *(f"meeting-ai-worker-{i:02d}" for i in range(1, 40))})
        tags = [bot.worker_tag(n) for n in names]
        dupes = {t for t in tags if tags.count(t) > 1}
        self.assertEqual(len(set(tags)), len(names), f"tag ซ้ำ: {dupes}")

    def test_the_same_name_always_gives_the_same_tag(self):
        # ต้องเสถียรข้ามการรีสตาร์ต ไม่งั้น cleanup_stale หาของตัวเองไม่เจอหลังเปิดใหม่
        self.assertEqual(bot.worker_tag("edgexpert-1346"), bot.worker_tag("edgexpert-1346"))


class TestWorkerTagIsUsableAsAContainerName(unittest.TestCase):

    def test_only_letters_and_digits(self):
        for name in REAL_NAMES + ["เครื่องหลัก", "", "  ", "a/b:c", "über-worker"]:
            with self.subTest(name=name):
                self.assertRegex(bot.worker_tag(name), r"^[A-Za-z0-9]+$")

    def test_thai_only_name_still_gets_a_tag(self):
        # ชื่อเครื่องเป็นภาษาไทยได้ แต่ใช้เป็นชื่อ container ไม่ได้ — ต้องเหลือ hash ล้วน
        tag = bot.worker_tag("เครื่องหลัก")
        self.assertTrue(tag)
        self.assertRegex(tag, r"^[0-9a-f]{6}$")

    def test_empty_name_still_gets_a_tag(self):
        self.assertTrue(bot.worker_tag(""))

    def test_a_very_long_name_stays_bounded(self):
        self.assertLessEqual(len(bot.worker_tag("w" * 500)), 18)


class TestJobSlotsStayApart(unittest.TestCase):
    """จุดที่บั๊กทำร้ายจริง — ชื่อ container กับโฟลเดอร์พักของสอง worker ต้องไม่ทับกัน."""

    def test_two_workers_running_the_same_job_id_do_not_share_a_container(self):
        job = "20260918-120000-abcdef"
        name_a, dir_a = bot._job_slot(job, "meeting-ai-worker-01")
        name_b, dir_b = bot._job_slot(job, "meeting-ai-worker-02")
        self.assertNotEqual(name_a, name_b)
        self.assertNotEqual(dir_a, dir_b)

    def test_the_cleanup_scope_of_one_worker_does_not_match_the_other(self):
        # cleanup_stale กรองด้วย docker ps --filter name=<PREFIX><tag>_ ซึ่งเป็นการจับคำนำหน้า
        # ถ้า tag ของ A เป็นคำนำหน้าของ B ตัวกรองของ A จะกวาดของ B ไปด้วย
        tag_a = bot.worker_tag("meeting-ai-worker-01")
        tag_b = bot.worker_tag("meeting-ai-worker-02")
        scope_a = f"{bot.PREFIX}{tag_a}_"
        name_b, _ = bot._job_slot("20260918-120000-abcdef", "meeting-ai-worker-02")
        self.assertFalse(name_b.startswith(scope_a),
                         f"ขอบเขตของ worker-01 ({scope_a}) ยังจับ container ของ worker-02 ได้")
        self.assertFalse(tag_b.startswith(tag_a))
        self.assertFalse(tag_a.startswith(tag_b))


if __name__ == "__main__":
    unittest.main()
