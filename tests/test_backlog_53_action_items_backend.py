"""BACKLOG #53 — Action Items ที่ติ๊กได้ · ฝั่งหลังบ้าน (ขั้นที่ 1 ของ PRD).

PRD: `docs/product/PRD-structured-action-items.md` — ทาง B "ซิงก์จากสรุป จับคู่ด้วยข้อความ
และห้ามลบรายการที่ติ๊กแล้ว"

**เรื่องที่ไฟล์นี้มีไว้พิสูจน์คือ AC-4.x** — สรุปถูกเขียนทับได้สี่ทาง ถ้าจับคู่ผิด
เครื่องหมายถูกที่ผู้ใช้ติ๊กไว้จะหาย ซึ่ง PRD บอกว่าแย่กว่าไม่มีฟีเจอร์เลย เพราะคนเชื่อมันไปแล้ว
ส่วนตัวแยกตารางเป็นเรื่องรอง

ข้อที่เจ้าของทำเครื่องหมาย ❓ ไว้ เดินตามข้อเสนอใน PRD ทุกข้อ (6.1 แยกจาก `summary`
ภาษาต้นฉบับเท่านั้น · 6.2 ผู้รับผิดชอบเป็นข้อความอิสระ · 6.5 เก็บเป็น JSON บนแถวการประชุม)
ยกเว้น **6.4 (เครื่องหมายถูกในไฟล์ที่ส่งออก) ยังไม่ทำ** เพราะเปลี่ยนหน้าตาไฟล์ที่คนเคยชิน
และไม่จำเป็นต่อขั้นที่ 1 · ข้อ 6.3 (สิทธิ์ของลิงก์แชร์) จะมีผลจริงตอนมีเส้น API ขั้นถัดไป

**ต่างจาก PRD หนึ่งข้อ**: PRD ร่างเส้น API ไว้เป็น `.../action-items/{index}` แต่ index
ขยับได้ทุกครั้งที่สรุปถูกเขียนใหม่ — ถ้าผู้ใช้กดติ๊กพอดีกับที่งานสรุปใหม่เพิ่งจบ จะติ๊กโดน
คนละรายการ ระเบียนจึงมี `id` ของตัวเอง (ไม่ฝังลง Markdown — นั่นคือทาง D ที่ PRD ปฏิเสธไว้)
"""

from __future__ import annotations

import inspect
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai.web import actionitems as A
from meeting_ai.web import pgstore
from meeting_ai.web import store

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "meeting_ai" / "web" / "schema.sql").read_text(encoding="utf-8")
PGSRC = (ROOT / "meeting_ai" / "web" / "pgstore.py").read_text(encoding="utf-8")

HEADING = "## 📋 สิ่งที่ต้องทำต่อ (Action Items)"


def summary_with(rows, heading: str = HEADING) -> str:
    body = "\n".join(f"| {a} | {b} | {c} |" for a, b, c in rows)
    return ("## 📌 สรุปย่อ (TL;DR)\nคุยกันเรื่องแผนไตรมาสหน้า\n\n"
            + heading + "\n| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |\n|---|---|---|\n"
            + body + "\n\n## ❓ ประเด็นค้าง\n- ไม่มี\n")


class TestParsing(unittest.TestCase):

    def test_it_reads_the_table_under_the_heading(self):
        items = A.parse(summary_with([("ทำสไลด์", "สมชาย", "ศุกร์"),
                                      ("คุยกับลูกค้า", "ไม่ได้ระบุ", "")]))
        self.assertEqual([i["text"] for i in items], ["ทำสไลด์", "คุยกับลูกค้า"])
        self.assertEqual(items[0]["assignee"], "สมชาย")
        self.assertEqual(items[1]["assignee"], "", "ไม่ได้ระบุ คือว่าง ไม่ใช่ชื่อคน")

    def test_it_ignores_other_tables_in_the_summary(self):
        # เทมเพลต standup มีตารางของตัวเองก่อนหน้า ถ้าจับตารางแรกที่เจอ จะได้ชื่อคนมาเป็นงาน
        s = ("## 👥 อัปเดตรายคน\n| คน | ทำอะไรไปแล้ว | จะทำอะไรต่อ | ติดอะไร |\n"
             "|---|---|---|---|\n| สมชาย | ก | ข | ค |\n\n"
             + summary_with([("ส่งรายงาน", "สมหญิง", "จันทร์")]))
        self.assertEqual([i["text"] for i in A.parse(s)], ["ส่งรายงาน"])

    def test_it_stops_at_the_next_heading(self):
        self.assertEqual(len(A.parse(summary_with([("งานเดียว", "", "")]))), 1)

    def test_it_drops_placeholder_rows(self):
        self.assertEqual(A.parse(summary_with([("...", "...", "..."), ("ของจริง", "", "")])),
                         [{"text": "ของจริง", "assignee": "", "due": ""}])

    def test_it_strips_markdown_emphasis(self):
        self.assertEqual(A.parse(summary_with([("**ทำสไลด์**", "*สมชาย*", "")]))[0],
                         {"text": "ทำสไลด์", "assignee": "สมชาย", "due": ""})

    def test_duplicate_rows_collapse(self):
        items = A.parse(summary_with([("ทำสไลด์", "ก", ""), ("ทำสไลด์ ", "ข", "")]))
        self.assertEqual(len(items), 1, "งานเดียวกันสองแถว = รายการเดียว")

    def test_no_heading_means_no_items(self):
        self.assertEqual(A.parse("## สรุป\nไม่มีตารางอะไรเลย"), [])

    def test_it_survives_an_edited_heading(self):
        # ผู้ใช้เกลาสรุปเองได้ อีโมจิหรือคำไทยอาจหาย แต่คำว่า Action Items มักอยู่
        self.assertEqual(len(A.parse(summary_with([("งาน", "", "")], "### Action Items"))), 1)


class TestNormalizeIsTheMatchingKey(unittest.TestCase):

    def test_case_space_and_trailing_punctuation_do_not_matter(self):
        self.assertEqual(A.normalize("  Send   the Report. "), A.normalize("send the report"))

    def test_fullwidth_and_halfwidth_match(self):
        self.assertEqual(A.normalize("ทำสไลด์（ด่วน）"), A.normalize("ทำสไลด์(ด่วน)"))

    def test_different_tasks_still_differ(self):
        self.assertNotEqual(A.normalize("ส่งรายงาน"), A.normalize("ส่งอีเมล"))


class TestReconcileNeverLosesATick(unittest.TestCase):
    """AC-4.x — หัวใจของทั้งฟีเจอร์."""

    def setUp(self):
        self.first = A.reconcile(None, summary_with([("ทำสไลด์", "สมชาย", "ศุกร์"),
                                                     ("ส่งรายงาน", "สมหญิง", "")]))
        self.first[0]["done"] = True

    def test_a_new_summary_keeps_the_tick_when_the_text_is_unchanged(self):
        again = A.reconcile(self.first, summary_with([("ทำสไลด์", "สมชาย", "ศุกร์"),
                                                      ("ส่งรายงาน", "สมหญิง", "")]))
        self.assertTrue(next(i for i in again if i["text"] == "ทำสไลด์")["done"])

    def test_wording_that_only_differs_by_spacing_still_matches(self):
        again = A.reconcile(self.first, summary_with([("ทำสไลด์  ", "สมชาย", "ศุกร์")]))
        self.assertTrue(again[0]["done"])

    def test_a_ticked_item_that_vanishes_is_kept_as_an_orphan(self):
        again = A.reconcile(self.first, summary_with([("ส่งรายงาน", "สมหญิง", "")]))
        orphans = [i for i in again if i.get("orphan")]
        self.assertEqual([o["text"] for o in orphans], ["ทำสไลด์"])
        self.assertTrue(orphans[0]["done"], "ยังต้องเป็น done อยู่ ไม่ใช่แค่ค้างไว้เฉย ๆ")

    def test_an_untouched_item_that_vanishes_is_dropped(self):
        again = A.reconcile(self.first, summary_with([("ทำสไลด์", "สมชาย", "ศุกร์")]))
        self.assertEqual([i["text"] for i in again], ["ทำสไลด์"],
                         "ยังไม่ติ๊ก = ไม่มีอะไรของผู้ใช้ให้เสีย ลบได้")

    def test_an_orphan_that_comes_back_stops_being_an_orphan(self):
        gone = A.reconcile(self.first, summary_with([("ส่งรายงาน", "สมหญิง", "")]))
        back = A.reconcile(gone, summary_with([("ทำสไลด์", "สมชาย", "ศุกร์")]))
        hit = next(i for i in back if i["text"] == "ทำสไลด์")
        self.assertFalse(hit["orphan"])
        self.assertTrue(hit["done"], "กลับมาแล้วต้องยังติ๊กอยู่")

    def test_ids_survive_a_resync(self):
        again = A.reconcile(self.first, summary_with([("ทำสไลด์", "สมชาย", "ศุกร์")]))
        self.assertEqual(again[0]["id"], self.first[0]["id"],
                         "id ขยับ = ปุ่มติ๊กที่ผู้ใช้เปิดค้างไว้จะชี้ผิดรายการ")

    def test_an_edited_assignee_is_not_overwritten_by_the_next_summary(self):
        self.first[1]["assignee"] = "สมศรี"
        self.first[1]["assignee_edited"] = True
        again = A.reconcile(self.first, summary_with([("ส่งรายงาน", "คนอื่น", "")]))
        self.assertEqual(again[0]["assignee"], "สมศรี")

    def test_an_untouched_assignee_follows_the_summary(self):
        again = A.reconcile(self.first, summary_with([("ส่งรายงาน", "คนใหม่", "")]))
        self.assertEqual(again[0]["assignee"], "คนใหม่",
                         "สรุปยังเป็นแหล่งความจริงของเนื้อหา ถ้าคนไม่ได้แก้เอง")

    def test_an_empty_summary_keeps_only_the_ticked_ones(self):
        again = A.reconcile(self.first, "")
        self.assertEqual([i["text"] for i in again], ["ทำสไลด์"])
        self.assertTrue(again[0]["orphan"])


class TestTheFileStoreKeepsState(unittest.TestCase):
    """เดินผ่านที่เก็บจริง ไม่ใช่เรียก reconcile ตรง ๆ — AC-4.1 ถึง AC-4.3."""

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix="mai-ai-test-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        web = Path(tmp)
        for name, val in (("WEB_DIR", web), ("INDEX_PATH", web / "index.json"),
                          ("SETTINGS_PATH", web / "settings.json")):
            p = mock.patch.object(store, name, val)
            p.start()
            self.addCleanup(p.stop)
        self.mid = "20260920-010203-abcdef"
        store.create(mid=self.mid, title="ประชุม", audio_name="a.wav", source="upload",
                     language="th", duration=1.0, segments=[],
                     summary=summary_with([("ทำสไลด์", "สมชาย", "ศุกร์"),
                                           ("ส่งรายงาน", "สมหญิง", "")]))

    def _items(self):
        return store.get(self.mid)["action_items"]

    def test_creating_a_meeting_extracts_them(self):
        self.assertEqual([i["text"] for i in self._items()], ["ทำสไลด์", "ส่งรายงาน"])

    def test_ticking_then_resummarising_keeps_the_tick(self):
        item = self._items()[0]
        store.set_action_item(self.mid, item["id"], done=True)
        store.set_summary(self.mid, summary_with([("ทำสไลด์", "สมชาย", "จันทร์"),
                                                  ("งานใหม่", "", "")]))
        after = {i["text"]: i for i in self._items()}
        self.assertTrue(after["ทำสไลด์"]["done"], "สรุปใหม่แล้วเครื่องหมายถูกหาย")
        self.assertFalse(after["งานใหม่"]["done"])
        self.assertNotIn("ส่งรายงาน", after, "ยังไม่ติ๊กและหายจากสรุป = ลบได้")

    def test_a_user_edit_of_the_summary_also_syncs(self):
        store.update(self.mid, summary=summary_with([("งานที่คนพิมพ์เอง", "", "")]))
        self.assertEqual([i["text"] for i in self._items()], ["งานที่คนพิมพ์เอง"])

    def test_editing_the_assignee_sets_the_edited_flag(self):
        item = self._items()[0]
        store.set_action_item(self.mid, item["id"], assignee="สมศรี")
        hit = next(i for i in self._items() if i["id"] == item["id"])
        self.assertEqual(hit["assignee"], "สมศรี")
        self.assertTrue(hit["assignee_edited"])

    def test_deleting_an_item_works_and_is_reported(self):
        item = self._items()[0]
        self.assertIsNotNone(store.delete_action_item(self.mid, item["id"]))
        self.assertNotIn(item["id"], [i["id"] for i in self._items()])
        self.assertIsNone(store.delete_action_item(self.mid, item["id"]),
                          "ลบซ้ำต้องบอกว่าไม่เจอ ไม่ใช่เงียบ ๆ สำเร็จ")

    def test_an_unknown_meeting_or_item_is_none(self):
        self.assertIsNone(store.set_action_item("20260101-000000-aaaaaa", "x", done=True))
        self.assertIsNone(store.set_action_item(self.mid, "ไม่มีจริง", done=True))


class TestBothBackendsAgree(unittest.TestCase):
    """CLAUDE.md: ฟังก์ชันของการประชุมต้องมีชื่อเหมือนกันทั้งสองฝั่ง."""

    def test_the_same_functions_exist_with_the_same_signature(self):
        for name in ("set_action_item", "delete_action_item"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(store, name))
                self.assertTrue(hasattr(pgstore, name))
                self.assertEqual(str(inspect.signature(getattr(store, name))),
                                 str(inspect.signature(getattr(pgstore, name))))


class TestTheColumnCannotBreakProductionBeforeMigration(unittest.TestCase):
    """deploy ถึง production ก่อน migration เสมอ — เส้นอ่านหลักห้ามพึ่งคอลัมน์ใหม่."""

    def test_the_migration_is_idempotent(self):
        self.assertIn("add column if not exists action_items", SCHEMA)

    def test_the_column_is_also_in_the_create_table(self):
        self.assertIn("action_items      jsonb not null default '[]'::jsonb", SCHEMA)

    def test_the_guard_exists(self):
        self.assertIn("def _has_action_items(conn)", PGSRC)

    def test_no_sql_touches_the_column_when_it_is_missing(self):
        """เทสต์พฤติกรรม ไม่ใช่การนับคำ — เคยนับแล้วมุตันต์รอดไปได้.

        จำลองฐานที่ยังไม่ได้รัน db-init (information_schema ไม่คืนอะไร) แล้วเดินทุกเส้น
        ที่เกี่ยวข้อง ถ้ามีคำสั่งไหนเอ่ยถึง action_items หลุดออกไป ฐานจริงจะตอบ
        UndefinedColumn แล้วหน้าเปิดการประชุมพังทั้งหน้า (เจอมาแล้วตอน peaks)
        """
        sent: list[str] = []

        class _Cur:
            def __init__(self, row):
                self._row = row

            def fetchone(self):
                return self._row

        class _Conn:
            def execute(self, sql, args=None):
                sent.append(" ".join(str(sql).split()))
                if "information_schema" in sql:
                    return _Cur(None)          # ยังไม่มีคอลัมน์ทั้ง peaks และ action_items
                if "returning id" in sql or "select 1" in sql:
                    return _Cur(("m1",))
                return _Cur(None)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        pgstore.reset_peaks_cache()
        self.addCleanup(pgstore.reset_peaks_cache)
        with mock.patch.object(pgstore.db, "connect", lambda *a, **k: _Conn()):
            pgstore.set_summary("m1", summary_with([("งาน", "", "")]))
            pgstore.update("m1", summary=summary_with([("งาน", "", "")]))
            self.assertIsNone(pgstore.set_action_item("m1", "x", done=True))
            self.assertIsNone(pgstore.delete_action_item("m1", "x"))

        leaked = [q for q in sent if "action_items" in q and "information_schema" not in q]
        self.assertEqual(leaked, [], f"SQL หลุดไปแตะคอลัมน์ที่ยังไม่มี: {leaked[:2]}")
        self.assertTrue(any("information_schema" in q for q in sent),
                        "ไม่ได้ตรวจเลยด้วยซ้ำ")

    def test_the_cache_reset_covers_the_new_flag(self):
        self.assertIn("_ai_ok = None", PGSRC)


if __name__ == "__main__":
    unittest.main()
