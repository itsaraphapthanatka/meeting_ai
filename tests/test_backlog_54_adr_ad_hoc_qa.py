"""BACKLOG #54 — ADR ของการถาม-ตอบอิสระกับการประชุม.

ตั๋วสั่งให้ตัดสินใจก่อนสร้าง (`architect → backend-dev`) ไฟล์นี้จึงไม่ได้ทดสอบฟีเจอร์
— ยังไม่มีฟีเจอร์ — แต่ทดสอบว่า **ข้อเท็จจริงที่ ADR ใช้ตัดสินใจยังเป็นจริงอยู่**

ADR-002 ปฏิเสธข้อเสนอในตั๋ว (ทำเป็นเทมเพลตของ `resummarize`) ด้วยเหตุผลที่อ้างโครงสร้างโค้ด
ตรง ๆ ทั้งหมด: รูปของ job id, เพดานเวลาของ Vercel, หัวข้อที่เทมเพลตผลิต, และกติกาสิทธิ์ของ
เส้น POST ถ้าข้อใดข้อหนึ่งเปลี่ยน คำตัดสินอาจผิดทันทีโดยไม่มีใครรู้ เพราะเอกสารไม่มีเทสต์
"""

from __future__ import annotations

import inspect
import json
import re
import types
import unittest
from pathlib import Path

from meeting_ai import config, runner, summarizer
from meeting_ai.web import jobs, server, store

ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs" / "adr" / "ADR-002-ad-hoc-qa.md"
MID = "20260918-120000-abc123"


class TestTheAdrExists(unittest.TestCase):

    def test_the_file_is_there(self):
        self.assertTrue(ADR.exists(), f"ไม่พบ {ADR}")

    def test_the_backlog_row_points_at_it(self):
        backlog = (ROOT / "docs" / "product" / "BACKLOG.md").read_text(encoding="utf-8")
        row = next(ln for ln in backlog.split("\n") if ln.startswith("| 54 |"))
        self.assertIn("ADR-002-ad-hoc-qa.md", row)


class TestTheChipsClaim(unittest.TestCase):
    """ADR ข้อ 1.1 อ้างว่าชิปสองใบในตั๋วตอบได้จากสรุปที่มีอยู่แล้ว — ข้ออ้างนี้คือเหตุผล
    ที่เราบอกให้ *ไม่* เรียก LLM กับมัน ถ้าเทมเพลตเปลี่ยน ข้ออ้างนี้ต้องถูกตรวจใหม่."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ADR.read_text(encoding="utf-8")

    def test_the_heading_counts_in_the_table_match_the_templates(self):
        # ตารางใน ADR บอกจำนวนหัวข้อของแต่ละเทมเพลต — เทียบกับของจริง ไม่ใช่เชื่อเอกสาร
        rows = dict(re.findall(r"^\| `(\w+)` \| (\d+) \|", self.text, re.M))
        self.assertEqual(set(rows), set(summarizer.TEMPLATES),
                         "ตารางใน ADR ไม่ได้ครอบเทมเพลตครบตามของจริง")
        for name, tpl in summarizer.TEMPLATES.items():
            with self.subTest(template=name):
                real = len(re.findall(r"^## ", tpl["body"], re.M))
                self.assertEqual(real, int(rows[name]),
                                 f"{name} มี {real} หัวข้อ แต่ ADR เขียนว่า {rows[name]}")

    def test_only_two_templates_talk_about_what_was_agreed(self):
        # ADR อ้างว่าชิป "What was decided?" ใช้ได้แค่บาง template — ถ้าข้อนี้เปลี่ยน
        # หน้าเว็บจะโชว์ชิปที่ชี้ไปยังหัวข้อที่ไม่มีอยู่
        have = {n for n, t in summarizer.TEMPLATES.items()
                if "ตกลงกัน" in t["body"]}
        self.assertEqual(have, {"general", "sales"})

    def test_every_template_still_emits_the_same_action_item_table(self):
        for name, tpl in summarizer.TEMPLATES.items():
            with self.subTest(template=name):
                self.assertIn("| งานที่ต้องทำ | ผู้รับผิดชอบ | กำหนดเสร็จ |", tpl["body"])


class TestTheReasonsForRejectingTheTicketsIdea(unittest.TestCase):
    """สามเหตุผลใน ADR ข้อ 2.2 ที่บอกว่าอย่ายัดคำถามเข้า `resummarize`."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ADR.read_text(encoding="utf-8")

    def test_resummarize_really_overwrites_the_summary(self):
        # เหตุผลข้อ 1: apply_result ของงาน summarize เขียนทับ summary ของการประชุม
        src = inspect.getsource(jobs.apply_result)
        self.assertIn("store.set_summary(", src)
        self.assertTrue(callable(store.set_summary))

    def test_a_thai_resummarize_job_reuses_the_meeting_id(self):
        # เหตุผลข้อ 2: job id เท่ากับ id การประชุม -> ถามสองคำถามพร้อมกันจะทับกัน
        src = inspect.getsource(jobs.submit_summarize)
        self.assertRegex(src, r"_enqueue\(\s*meeting_id\s*,")

    def test_the_language_slot_is_a_five_value_allow_list(self):
        # เหตุผลข้อ 2 (ต่อ): `<mid>.sum.<lang>` ใช้แทนช่องคำถามไม่ได้
        self.assertEqual(list(summarizer.LANGUAGE_NAMES), ["th", "en", "ja", "zh", "ko"])
        for code in summarizer.LANGUAGE_NAMES:
            with self.subTest(lang=code):
                self.assertIn(f"`{code}", self.text.replace(" ", "`"))

    def test_template_is_an_allow_list_key_stored_on_the_meeting(self):
        # เหตุผลข้อ 3
        schema = (ROOT / "meeting_ai" / "web" / "schema.sql").read_text(encoding="utf-8")
        self.assertIn("template", schema)
        self.assertIn(summarizer.DEFAULT_TEMPLATE, summarizer.TEMPLATES)


class TestTheJobIdShape(unittest.TestCase):
    """ADR ข้อ 2.4 เสนอ `<mid>.ask.<hex6>` และห้ามเอาคำถามไปใส่ใน id."""

    def test_the_proposed_id_is_already_accepted(self):
        self.assertTrue(jobs.safe_job_id(f"{MID}.ask.9f3c1a"))

    def test_a_question_cannot_be_smuggled_into_the_id(self):
        for bad in ("ลูกค้าติดเรื่องราคาตรงไหน",
                    "who said what",          # ช่องว่าง
                    "a/b",                    # ตัวคั่น path
                    "a\\b"):
            with self.subTest(question=bad):
                self.assertFalse(jobs.safe_job_id(f"{MID}.ask.{bad}"))

    def test_the_existing_shapes_still_pass(self):
        # ถ้าสองอันนี้พัง แปลว่า safe_job_id เปลี่ยนกติกา และข้อเสนอ 2.4 ต้องตรวจใหม่
        self.assertTrue(jobs.safe_job_id(MID))
        self.assertTrue(jobs.safe_job_id(f"{MID}.tr.en"))


class TestTheConstraintsItLeansOn(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ADR.read_text(encoding="utf-8")

    def test_the_serverless_ceiling_is_still_sixty_seconds(self):
        # ข้อ 2.3/4.2 ตัดทางเรียกแบบรอคำตอบทิ้งเพราะเลขนี้
        conf = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertEqual(conf["functions"]["api/index.py"]["maxDuration"], 60)
        self.assertIn("maxDuration", self.text)

    def test_the_web_page_already_polls(self):
        # ข้อ 2.3 อ้างว่าไม่ต้องมี transport ใหม่
        app_js = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertRegex(app_js, r"const POLL_MINE_MS = 1500;")
        self.assertIn("POLL_MINE_MS", self.text)

    def test_the_llm_is_a_remote_endpoint_not_a_local_model(self):
        # ข้อ 2.3 "ราคาที่จ่าย": งาน ask ไม่ต้องใช้ GPU แต่ยังต้องรอ worker
        self.assertTrue(str(config.config.llm_base_url).startswith("http"))
        self.assertIn("summarize", runner.job_kinds({}))
        self.assertNotIn("ask", runner.HANDLERS, "ฟีเจอร์ถูกสร้างแล้ว — ADR ต้องเปลี่ยนสถานะ")

    def test_the_rate_limit_counter_it_wants_to_reuse_exists(self):
        self.assertTrue(callable(server.Handler._bucket_hit))
        self.assertIn("_bucket_hit", self.text)

    def test_a_new_post_route_inherits_write_permission(self):
        # ข้อ 2.8 — กับดักที่ทำให้คนอ่านอย่างเดียวถามไม่ได้โดยไม่มีใครตั้งใจ
        src = inspect.getsource(server.Handler._meeting)
        self.assertRegex(src, r'writing = self\.command in \("PATCH", "DELETE", "POST"\)')

    def test_a_share_visitor_can_reach_a_new_subroute(self):
        # ข้อ 2.7 — คนถือลิงก์แชร์ยิง POST เข้าเส้นย่อยของการประชุมที่แชร์ได้จริง
        fake = types.SimpleNamespace(command="POST", share={"meeting_id": MID})
        self.assertTrue(server.Handler._share_may_call(fake, ["meetings", MID, "ask"]))
        self.assertFalse(server.Handler._share_may_call(fake, ["meetings", "other", "ask"]))

    def test_no_vector_library_is_available_server_side(self):
        # ข้อ 4.4 ปฏิเสธ RAG เพราะข้อนี้
        reqs = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for line in reqs.split("\n"):
            if line.strip().startswith("#") or not line.strip():
                continue
            with self.subTest(dep=line.strip()):
                self.assertTrue(line.strip().startswith("psycopg"),
                                "มี dependency ฝั่งเซิร์ฟเวอร์เพิ่มมา — ข้อ 4.4 ต้องตรวจใหม่")

    def test_the_size_numbers_agree_with_adr_001(self):
        # ข้อ 1.3 ยืมตัวเลขจาก ADR-001 มาทั้งชุด สองไฟล์ต้องไม่หลุดกัน
        adr1 = (ROOT / "docs" / "adr" / "ADR-001-transcript-chunking.md").read_text(encoding="utf-8")
        for number in ("149", "2,905", "900"):
            with self.subTest(number=number):
                self.assertIn(number, adr1)
                self.assertIn(number, self.text)
        self.assertEqual(config.config.llm_chunk_chars, 24000)
        self.assertIn("24,000", self.text)


class TestTheAdrIsDecidable(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = ADR.read_text(encoding="utf-8")

    def test_it_answers_the_question_the_ticket_asked(self):
        # ตั๋วถามตรง ๆ ว่า "endpoint ใหม่ หรือ template ของ resummarize"
        self.assertIn("endpoint ใหม่", self.text)
        self.assertIn("/ask", self.text)

    def test_it_records_the_options_it_rejected(self):
        self.assertIn("ทางเลือกที่พิจารณาแล้วไม่เอา", self.text)
        rejected = re.findall(r"^### 4\.\d+ ", self.text, re.M)
        self.assertGreaterEqual(len(rejected), 5, f"บันทึกทางที่ปฏิเสธแค่ {len(rejected)} ทาง")

    def test_it_leaves_the_owners_decisions_to_the_owner(self):
        self.assertGreaterEqual(self.text.count("❓"), 5)

    def test_it_names_what_it_is_least_sure_about(self):
        self.assertIn("มั่นใจน้อยที่สุด", self.text)

    def test_it_says_what_the_decision_costs(self):
        self.assertIn("เสีย / ต้องยอมรับ", self.text)

    def test_it_repeats_the_migration_rule(self):
        # คอลัมน์ใหม่ + deploy มาก่อน db-init เสมอ — ลืมข้อนี้แล้ว production พังทันที
        self.assertIn("db-init", self.text)
        self.assertIn("add column if not exists", self.text)

    def test_it_points_back_at_the_code(self):
        self.assertIn("อ้างอิงในโค้ด", self.text)


if __name__ == "__main__":
    unittest.main()
