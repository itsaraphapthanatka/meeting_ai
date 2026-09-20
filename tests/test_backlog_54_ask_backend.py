"""BACKLOG #54 — ถาม-ตอบอิสระกับการประชุม · ฝั่งหลังบ้าน (ADR-002).

ADR: `docs/adr/ADR-002-ad-hoc-qa.md`

**ข้อ ❓ ทั้งห้า ตัดสินไว้แบบนี้** (เปลี่ยนได้ทุกข้อ):

| ข้อ | ทำแบบไหน | ทำไม |
|---|---|---|
| 5.1 ชั้นเดียวพอไหม | **ชั้นเดียว** (ตอบจากสรุป) + เก็บธง `enough` | ADR เสนอเอง — ธงคือเครื่องมือเก็บตัวเลขจริงว่าไม่พอบ่อยแค่ไหน |
| 5.2 ให้เว็บเรียก LLM เอง | **ไม่** เป็นงานทางเดียว | ขัดกติกา "runner.py คือทางประมวลผลทางเดียว" |
| 5.3 ใครถามได้ | **ต้องมีสิทธิ์แก้** | ต่างจากที่ ADR เอนไป — ดู `TestWhoMayAsk` |
| 5.4 เก็บคำตอบไหม | **เก็บ** คอลัมน์ `qa` เก็บล่าสุด 20 คู่ | ทำ `action_items` มาแล้ว รูปแบบพิสูจน์แล้ว |
| 5.5 อ้างช่วงเวลาไหม | **ไม่** และสั่งห้ามใน prompt | ชั้นที่ 1 อ่านจากสรุปซึ่งไม่มีเวลาอยู่เลย อ้างไปก็คือแต่ง |

**ข้อ 5.3 เลือกต่างจาก ADR โดยตั้งใจ** — ADR ข้อ 2.8 เตือนว่าเส้น POST ใหม่จะกลายเป็น
"การเขียน" อัตโนมัติ และสั่งให้ผู้ลงมือตัดสินใจเอง ไม่ใช่รับมรดกเงียบ ๆ v1 เลือกทางเข้ม
เพราะการถามใช้เงินของเจ้าของ และลิงก์แชร์แบบอ่านอย่างเดียวมักส่งให้คนนอก — ปลดล็อกทีหลัง
ง่ายกว่าตามเก็บบิล
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest import mock

from _harness import CloudCase, LocalCase, new_mid

from meeting_ai import runner, summarizer
from meeting_ai.web import jobs, pgstore, server, store

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = (ROOT / "meeting_ai" / "web" / "schema.sql").read_text(encoding="utf-8")
SERVER_SRC = (ROOT / "meeting_ai" / "web" / "server.py").read_text(encoding="utf-8")
JOBS_SRC = (ROOT / "meeting_ai" / "web" / "jobs.py").read_text(encoding="utf-8")

def _apply_result_ask() -> str:
    """สาขา ask ของ apply_result — `elif kind == "ask":` โผล่สองที่ (build_spec ด้วย)."""
    body = JOBS_SRC[JOBS_SRC.index("def apply_result("):]
    body = body[body.index('elif kind == "ask":'):]
    return body[:body.index("    else:")]


SUMMARY = "## สรุปย่อ\nตกลงกันว่าจะเลื่อนเปิดตัวไปไตรมาสหน้า และให้สมชายทำสไลด์"


class TestThePrompt(unittest.TestCase):

    def test_it_answers_from_the_summary_only(self):
        seen = {}

        def fake(messages, **kw):
            seen["prompt"] = messages[0]["content"]
            return "เลื่อนไปไตรมาสหน้า"

        with mock.patch.object(summarizer, "_chat", fake):
            out = summarizer.answer("เลื่อนเปิดตัวไหม", SUMMARY)
        self.assertTrue(out["enough"])
        self.assertIn(SUMMARY, seen["prompt"])
        self.assertIn("เลื่อนเปิดตัวไหม", seen["prompt"])

    def test_it_forbids_inventing_timestamps(self):
        # ชั้นที่ 1 อ่านจากสรุปซึ่งไม่มีเวลาอยู่เลย ถ้าอ้างก็คือแต่ง (ADR-002 ข้อ 5.5)
        self.assertIn("ห้ามอ้างเวลา", summarizer.ASK_PROMPT)

    def test_it_forbids_guessing(self):
        self.assertIn("ห้ามเดา", summarizer.ASK_PROMPT)

    def test_not_enough_is_reported_as_a_flag(self):
        with mock.patch.object(summarizer, "_chat",
                               lambda *a, **k: summarizer.NOT_ENOUGH + " — สรุปไม่ได้พูดถึงงบ"):
            out = summarizer.answer("งบเท่าไร", SUMMARY)
        self.assertFalse(out["enough"], "ธงนี้คือวิธีวัดว่าชั้นเดียวพอไหม (ADR ข้อ 5.1)")

    def test_an_empty_question_or_summary_is_refused(self):
        with self.assertRaises(RuntimeError):
            summarizer.answer("", SUMMARY)
        with self.assertRaises(RuntimeError):
            summarizer.answer("อะไรนะ", "   ")


class TestTheJobKind(unittest.TestCase):

    def test_workers_accept_it_without_extra_caps(self):
        # LLM เป็น HTTP endpoint ระยะไกล ไม่ต้องใช้ GPU เหมือนงาน bot
        self.assertIn("ask", runner.job_kinds({}))

    def test_it_has_a_handler(self):
        self.assertIs(runner.HANDLERS["ask"], runner.ask_job)

    def test_the_question_comes_from_the_spec(self):
        with mock.patch.object(summarizer, "answer",
                               lambda q, s: {"text": f"ตอบ:{q}", "enough": True}):
            out = runner.ask_job({"question": "ถามอะไร", "summary": SUMMARY}, lambda *a: None)
        self.assertEqual(out["answer"], "ตอบ:ถามอะไร")
        self.assertTrue(out["enough"])

    def test_a_job_without_a_question_fails_loudly(self):
        # เช็คข้อความด้วย ไม่งั้นถอดด่านนี้ออกแล้วมันจะไปตายที่ summarizer.answer() แทน
        # ซึ่งก็ raise เหมือนกัน เทสต์เลยไม่รู้ว่าด่านหายไป (มุตันต์รอดในรอบแรก)
        with mock.patch.object(summarizer, "answer",
                               lambda q, s: {"text": "ไม่ควรมาถึงตรงนี้", "enough": True}):
            with self.assertRaises(RuntimeError) as cm:
                runner.ask_job({"summary": SUMMARY}, lambda *a: None)
        self.assertIn("ไม่มีคำถามใน spec", str(cm.exception))


class TestTheQueueEntry(unittest.TestCase):

    def test_the_id_shape_is_mid_dot_ask_dot_hex(self):
        self.assertIn('f"{meeting_id}.ask.{secrets.token_hex(3)}"', JOBS_SRC)

    def test_the_question_is_not_in_the_id(self):
        # ยาว มีช่องว่าง มีตัวคั่น path ได้ และเป็นข้อมูลส่วนตัวที่จะไปโผล่ใน URL กับ log
        body = JOBS_SRC[JOBS_SRC.index("def submit_ask("):]
        body = body[:body.index("def build_spec(")]
        self.assertNotIn("{question}", body)
        self.assertIn('"question": question', body)

    def test_apply_result_reads_the_question_from_the_spec_not_the_result(self):
        """BUG-048 ซ้ำรอย: ฟิลด์ที่เซิร์ฟเวอร์เลือกไว้แล้ว ห้ามอ่านจาก result ของ worker."""
        body = _apply_result_ask()
        self.assertIn('(job.get("_spec") or {}).get("question")', body)
        self.assertNotIn('result.get("question")', body)

    def test_the_answer_is_sanitised(self):
        self.assertIn('sanitize.text(result.get("answer"))', _apply_result_ask())


class TestTheQuestionSurvivesBothModes(LocalCase):
    """เจอตอนกดใช้จริง ไม่ใช่ตอนเทสต์ — โหมดไฟล์กับ cloud เก็บ spec คนละที่.

    `_enqueue()` โหมดไฟล์เก็บงานไว้ในหน่วยความจำ **ไม่มีคีย์ `_spec`** เลย ส่วนโหมด cloud
    เก็บลง `jobs.spec` แล้ว `job_get()` คืนมาเป็น `_spec` โดยไม่มีคีย์ระดับบน — งานแปลแก้เรื่องนี้
    ไปแล้วด้วยการส่ง `_lang` เป็น extra ควบคู่กับ `spec` (เห็นได้ใน BUG-048)

    รอบแรกผมส่งแค่ `spec=` ทำให้ `build_spec()` ในโหมดไฟล์ได้คำถามว่าง งานเลยตายด้วย
    "งานนี้ไม่มีคำถามใน spec" ทั้งที่ผู้ใช้พิมพ์คำถามมาแล้ว — เทสต์เดิมไม่เห็นเพราะตรวจแต่
    ซอร์สกับเส้น cloud
    """

    def test_build_spec_gets_the_question_in_file_mode(self):
        mid = new_mid()
        store.create(mid=mid, title="ประชุม", audio_name="a.wav", source="upload",
                     language="th", duration=1.0, segments=[], summary=SUMMARY)
        job = jobs.submit_ask(mid, "ประชุม", "ใครทำสไลด์")
        spec = jobs.build_spec(job["id"])
        self.assertEqual(spec["question"], "ใครทำสไลด์",
                         "คำถามหายระหว่างทาง — งานจะตายทั้งที่ผู้ใช้พิมพ์มาแล้ว")
        self.assertIn(SUMMARY, spec["summary"])

    def test_apply_result_finds_it_in_file_mode_too(self):
        mid = new_mid()
        store.create(mid=mid, title="ประชุม", audio_name="a.wav", source="upload",
                     language="th", duration=1.0, segments=[], summary=SUMMARY)
        job = jobs.submit_ask(mid, "ประชุม", "ใครทำสไลด์")
        jobs.apply_result(job["id"], {"answer": "สมชาย", "enough": True})
        rows = store.get(mid)["qa"]
        self.assertEqual([r["question"] for r in rows], ["ใครทำสไลด์"])


class TestStorage(LocalCase):

    def setUp(self):
        super().setUp()
        self.mid = new_mid()
        store.create(mid=self.mid, title="ประชุม", audio_name="a.wav", source="upload",
                     language="th", duration=1.0, segments=[], summary=SUMMARY)

    def test_a_new_meeting_has_an_empty_list(self):
        self.assertEqual(store.get(self.mid)["qa"], [])

    def test_adding_a_pair(self):
        store.add_qa(self.mid, "เลื่อนไหม", "เลื่อนไปไตรมาสหน้า")
        rows = store.get(self.mid)["qa"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["question"], "เลื่อนไหม")
        self.assertEqual(rows[0]["answer"], "เลื่อนไปไตรมาสหน้า")
        self.assertTrue(rows[0]["enough"])
        self.assertTrue(rows[0]["id"])

    def test_it_keeps_only_the_most_recent_ones(self):
        for i in range(store.MAX_QA + 5):
            store.add_qa(self.mid, f"ถาม {i}", f"ตอบ {i}")
        rows = store.get(self.mid)["qa"]
        self.assertEqual(len(rows), store.MAX_QA)
        self.assertEqual(rows[-1]["question"], f"ถาม {store.MAX_QA + 4}")

    def test_deleting_one(self):
        store.add_qa(self.mid, "ถาม", "ตอบ")
        qid = store.get(self.mid)["qa"][0]["id"]
        self.assertIsNotNone(store.delete_qa(self.mid, qid))
        self.assertEqual(store.get(self.mid)["qa"], [])
        self.assertIsNone(store.delete_qa(self.mid, qid), "ลบซ้ำต้องบอกว่าไม่เจอ")

    def test_the_answer_does_not_touch_the_summary_or_translations(self):
        """ADR-002 ข้อ 2.5 — ยัดลง translations จะทำให้คำถามกลายเป็น "ภาษา" หนึ่ง."""
        before = store.get(self.mid)
        store.add_qa(self.mid, "ถาม", "ตอบ")
        after = store.get(self.mid)
        self.assertEqual(after["summary"], before["summary"])
        self.assertEqual(after["translations"], before["translations"])


class TestBothBackendsAgree(unittest.TestCase):

    def test_same_functions_same_signature(self):
        for name in ("add_qa", "delete_qa"):
            with self.subTest(name=name):
                self.assertEqual(str(inspect.signature(getattr(store, name))),
                                 str(inspect.signature(getattr(pgstore, name))))

    def test_the_cap_is_the_same_number(self):
        self.assertEqual(store.MAX_QA, pgstore.MAX_QA)

    def test_the_column_migration_is_idempotent(self):
        self.assertIn("add column if not exists qa jsonb", SCHEMA)

    def test_postgres_guards_the_column(self):
        pg = (ROOT / "meeting_ai" / "web" / "pgstore.py").read_text(encoding="utf-8")
        self.assertIn("def _has_qa(conn)", pg)
        self.assertIn("_qa_ok = None", pg)


class TestTheEndpoint(LocalCase):

    def setUp(self):
        super().setUp()
        self.mid = new_mid()
        store.create(mid=self.mid, title="ประชุม", audio_name="a.wav", source="upload",
                     language="th", duration=1.0, segments=[], summary=SUMMARY)

    def test_it_queues_a_job(self):
        with mock.patch.object(jobs, "submit_ask",
                               lambda *a, **k: {"id": "x", "status": "queued"}) as _:
            status, body, _h = self.post_json(f"/api/meetings/{self.mid}/ask",
                                              {"question": "เลื่อนไหม"})
        self.assertEqual(status, 202, body)
        self.assertEqual(body["status"], "queued")

    def test_an_empty_question_is_400(self):
        status, _, _ = self.post_json(f"/api/meetings/{self.mid}/ask", {"question": "   "})
        self.assertEqual(status, 400)

    def test_a_missing_question_is_400(self):
        status, _, _ = self.post_json(f"/api/meetings/{self.mid}/ask", {})
        self.assertEqual(status, 400)

    def test_a_non_string_question_is_400(self):
        status, _, _ = self.post_json(f"/api/meetings/{self.mid}/ask", {"question": 5})
        self.assertEqual(status, 400)

    def test_a_meeting_without_a_summary_is_409_not_500(self):
        mid2 = new_mid()
        store.create(mid=mid2, title="ยังไม่สรุป", audio_name="a.wav", source="upload",
                     language="th", duration=1.0, segments=[], summary="")
        status, body, _ = self.post_json(f"/api/meetings/{mid2}/ask", {"question": "อะไร"})
        self.assertEqual(status, 409)
        self.assertIn("สรุป", str(body))

    def test_other_methods_are_405(self):
        status, _, _ = self.get(f"/api/meetings/{self.mid}/ask")
        self.assertEqual(status, 405)

    def test_deleting_a_saved_answer(self):
        store.add_qa(self.mid, "ถาม", "ตอบ")
        qid = store.get(self.mid)["qa"][0]["id"]
        status, body, _ = self.delete(f"/api/meetings/{self.mid}/qa/{qid}")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["qa"], [])

    def test_a_malformed_qa_id_is_rejected(self):
        status, _, _ = self.delete(f"/api/meetings/{self.mid}/qa/zzzz")
        self.assertIn(status, (400, 404))


class TestTheQuotaIsPerPersonNotPerIp(unittest.TestCase):
    """ADR-002 ข้อ 2.7 — ทั้งออฟฟิศที่ออกเน็ต IP เดียวไม่ควรแย่งโควตากันเอง."""

    @staticmethod
    def _key(user=None, share=None, ip="1.2.3.4") -> str:
        """เรียก _ask_key() ตัวจริงโดยไม่ต้องตั้งเซิร์ฟเวอร์ — ตรวจพฤติกรรม ไม่ใช่ลำดับบรรทัด.

        เวอร์ชันแรกดูแค่ว่าบรรทัด `ask:{uid}` อยู่ก่อน `mai_share` ในซอร์ส ซึ่งมุตันต์
        "ทำให้ uid เป็น None เสมอ" รอดไปได้สบาย ๆ
        """
        class _Stub:
            pass

        stub = _Stub()
        stub.user = user
        stub._cookie = lambda name: share if name == "mai_share" else None
        stub._client_ip = lambda: ip
        return server.Handler._ask_key(stub)

    def test_a_logged_in_user_gets_their_own_bucket(self):
        self.assertEqual(self._key(user={"id": "uid-a"}), "ask:uid-a")
        self.assertNotEqual(self._key(user={"id": "uid-a"}),
                            self._key(user={"id": "uid-b"}),
                            "สองคนต้องไม่ใช้โควตาถังเดียวกัน")

    def test_two_people_behind_one_ip_do_not_share_a_bucket(self):
        # ทั้งออฟฟิศที่ออกเน็ต IP เดียวไม่ควรแย่งโควตากันเอง (ADR-002 ข้อ 2.7)
        a = self._key(user={"id": "uid-a"}, ip="203.0.113.9")
        b = self._key(user={"id": "uid-b"}, ip="203.0.113.9")
        self.assertNotEqual(a, b)

    def test_a_share_link_holder_gets_a_hashed_bucket(self):
        key = self._key(share="ลับมาก")
        self.assertTrue(key.startswith("ask.share:"))
        self.assertNotIn("ลับมาก", key, "โทเคนแชร์เป็นความลับ ห้ามเอาไปเป็นคีย์ตรง ๆ")

    def test_it_falls_back_to_the_ip_when_there_is_nothing_else(self):
        self.assertEqual(self._key(ip="198.51.100.7"), "ask.ip:198.51.100.7")

    def test_the_limit_is_wired_and_reads_the_wait_correctly(self):
        # _bucket_hit คืน "วินาทีที่ต้องรอ" ไม่ใช่ True/False — เขียนกลับด้านแล้วเพดานจะหายไป
        body = SERVER_SRC[SERVER_SRC.index("def _ask(self"):]
        body = body[:body.index("def _ask_key(self")]
        self.assertIn("wait = self._bucket_hit(self._ask_key(), ASK_LIMIT, ASK_WINDOW)", body)
        self.assertIn("if wait:", body)
        self.assertNotIn("if not self._bucket_hit", body)


class TestTheAnswerLandsInCloudToo(CloudCase):
    """เดินเส้นเขียนผลจริงในโหมด cloud — ไม่งั้น `FakeStore.add_qa` ไม่เคยถูกเรียกเลย.

    บทเรียนจาก #53 ขั้นที่ 2: ของปลอมที่ไม่มีเมธอดจะทำให้เทสต์ผ่านไปเฉย ๆ แล้วไปตายด้วย
    AttributeError ตอนรันจริง มุตันต์ข้อ 17 จงใจถอด `add_qa` ออกเพื่อพิสูจน์ว่าเทสต์จับได้
    """

    def test_apply_result_writes_the_pair(self):
        job = jobs.submit_ask(self.M1, "ประชุม", "ตกลงอะไรกัน")
        jobs.apply_result(job["id"], {"answer": "เลื่อนไปไตรมาสหน้า", "enough": True})
        rows = self.store.meetings[self.M1]["qa"]
        self.assertEqual([r["question"] for r in rows], ["ตกลงอะไรกัน"])
        self.assertEqual(rows[0]["answer"], "เลื่อนไปไตรมาสหน้า")

    def test_a_worker_cannot_swap_the_question(self):
        # คำถามอ่านจาก spec ที่เซิร์ฟเวอร์สร้าง ไม่ใช่จาก body ของ worker (BUG-048)
        job = jobs.submit_ask(self.M1, "ประชุม", "คำถามจริง")
        jobs.apply_result(job["id"], {"answer": "ตอบ", "question": "คำถามปลอม"})
        rows = self.store.meetings[self.M1]["qa"]
        self.assertEqual(rows[-1]["question"], "คำถามจริง")

    def test_an_empty_answer_is_an_error_not_a_blank_row(self):
        job = jobs.submit_ask(self.M1, "ประชุม", "ถาม")
        with self.assertRaises(RuntimeError):
            jobs.apply_result(job["id"], {"answer": "   "})


class TestWhoMayAsk(CloudCase):
    """ข้อ 5.3 — v1 เลือก "ต้องมีสิทธิ์แก้" ต่างจากที่ ADR เอนไป."""

    def test_a_stranger_cannot_ask(self):
        status, _, _ = self.post_json(f"/api/meetings/{self.M1}/ask", {"question": "อะไร"},
                                      cookies={"mai_session": self.tokB})
        self.assertEqual(status, 403)

    def test_without_a_session_it_is_refused(self):
        status, _, _ = self.post_json(f"/api/meetings/{self.M1}/ask", {"question": "อะไร"})
        self.assertIn(status, (401, 403))

    def test_the_route_is_after_the_permission_block(self):
        body = SERVER_SRC[SERVER_SRC.index("def _meeting(self"):]
        body = body[:body.index("def _ask(self")]
        guard = body.index("allowed = self._may_write(mid) if writing else self._may_read(mid)")
        self.assertLess(guard, body.index('rest == ["ask"]'))

    def test_it_is_not_public(self):
        pub = SERVER_SRC[SERVER_SRC.index("PUBLIC_API"):]
        pub = pub[:pub.index("\n\n")]
        self.assertNotIn("ask", pub)


if __name__ == "__main__":
    unittest.main()
