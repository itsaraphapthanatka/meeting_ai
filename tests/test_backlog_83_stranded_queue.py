"""BACKLOG #83 — บอกให้รู้ว่างานค้างคิวเพราะไม่มีเครื่องรับงานชนิดนั้น.

เจ้าของเจอเอง 2026-09-20: การ์ด "ประชุมที่บอทเข้าร่วม · รอคิว" ค้างอยู่ ขณะที่ชิปบอกว่า
"1 เครื่องพร้อม" · เครื่องเดียวที่ออนไลน์ไม่มี Docker จึงไม่ประกาศ kind `bot`
(`runner.job_kinds()` ใส่ `bot` เฉพาะเมื่อ `caps["bot"]` จริง) งานจึงไม่มีวันถูกคว้า
และไม่มีกลไกไหนแตะงานที่ค้างใน `queued` เลย — requeue orphan ดูแลเฉพาะงาน `running`
ที่ worker เงียบไป

**กติกาที่ตรึงไว้**: กติกา "เครื่องนี้รับงานอะไรได้" มีบ้านเดียวคือ `runner.job_kinds()`
เซิร์ฟเวอร์ส่ง `kinds` มาให้หน้าเว็บ (`server.worker_kinds()`) **ห้าม app.js คิดเอง**
ถ้าวันหนึ่ง `job_kinds()` เพิ่มเงื่อนไข สองฝั่งจะเพี้ยนกันเงียบ ๆ แล้วหน้าเว็บจะโกหก

วัดกับเบราว์เซอร์จริงที่ 375x812 บนเซิร์ฟเวอร์จาก git worktree — หกสถานะ:

    A งานบอทค้างคิว เครื่องทำบอทไม่ได้  -> ชิป "1 เครื่องออนไลน์ · ไม่มีเครื่องรับงานบอท"
                                          การ์ด "รอคิว · ค้างมา 42 นาที — ไม่มีเครื่อง…"
    B งานเดียวกัน แต่เครื่องทำบอทได้     -> "1 เครื่องพร้อม" การ์ดไม่มีคำเตือน
    C งานวิ่งอยู่แล้ว                    -> ไม่เตือน
    D ไม่มีเครื่องออนไลน์                -> "ไม่มีเครื่องประมวลผลออนไลน์" (ของเดิม)
    E ไม่มีงานค้างคิว                    -> "1 เครื่องพร้อม"
    F worker รุ่นเก่าไม่ส่ง kinds        -> ไม่เตือน (เตือนผิดแย่กว่าไม่เตือน)

hit-test: `.job-stuck` กว้าง 249px สูง 17px อยู่ในจอ `elementFromPoint` กลางข้อความได้
ตัวมันเอง สี `rgb(255, 149, 0)` = `--warn` · ชิปและจุดสีก็ผ่านเหมือนกัน

**บั๊กที่เจอเพราะเปิดดูจริง ไม่ใช่เพราะเทสต์**: ข้อความซ้ำเป็น "รอคิวรอคิว 42 นาที"
เพราะ `step` ของงานที่ค้างคือ "รอคิว" อยู่แล้ว แก้เป็น "ค้างมา 42 นาที"
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from meeting_ai import runner
from meeting_ai.web import server

STATIC = Path(__file__).resolve().parents[1] / "meeting_ai" / "web" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
SERVER_PY = (Path(server.__file__)).read_text(encoding="utf-8")


def fn(name: str) -> str:
    start = APP_JS.index(name)
    nl = chr(10)
    nxt = APP_JS.find(nl + "function ", start + 1)
    other = APP_JS.find(nl + "async function ", start + 1)
    if other != -1 and (nxt == -1 or other < nxt):
        nxt = other
    return APP_JS[start:nxt if nxt != -1 else len(APP_JS)]


class TestTheRuleHasOneHome(unittest.TestCase):
    """`runner.job_kinds()` เป็นเจ้าของกติกา — เซิร์ฟเวอร์แปลงให้ หน้าเว็บไม่คิดเอง."""

    def test_a_machine_without_docker_is_not_offered_bot(self):
        self.assertNotIn("bot", server.worker_kinds({"can": ["local", "api"]}))

    def test_a_machine_with_docker_is(self):
        self.assertIn("bot", server.worker_kinds({"can": ["local", "api", "bot"]}))

    def test_it_matches_what_the_worker_itself_would_claim(self):
        # ถ้าสองฝั่งไม่ตรงกัน หน้าเว็บจะบอกผิดว่ามี/ไม่มีเครื่องรับงาน
        for cap in (True, False):
            with self.subTest(bot=cap):
                self.assertEqual(
                    server.worker_kinds({"can": ["bot"] if cap else []}),
                    runner.job_kinds({"bot": cap}))

    def test_a_worker_row_without_can_does_not_crash(self):
        self.assertNotIn("bot", server.worker_kinds({}))

    def test_the_view_attaches_it_to_every_worker(self):
        body = SERVER_PY[SERVER_PY.index("def _workers_view"):]
        body = body[:body.index("def _job_scope")]
        self.assertIn('w["kinds"] = worker_kinds(w)', body)
        self.assertLess(body.index('w["kinds"]'), body.index("is_admin"),
                        "ต้องติดไปกับทุกเครื่อง ไม่ใช่เฉพาะที่แอดมินเห็น")

    def test_the_browser_is_not_given_the_rule_to_re_derive(self):
        # app.js ต้องอ่าน w.kinds ที่ส่งมา ห้ามเดาจาก caps/can เอง
        body = fn("function strandedKinds()")
        self.assertIn("w.kinds", body)
        self.assertNotIn("w.can", body)


class TestStrandedKinds(unittest.TestCase):

    def test_it_only_counts_machines_that_are_alive(self):
        body = fn("function strandedKinds()")
        self.assertIn("filter((w) => w.alive)", body)

    def test_it_only_looks_at_queued_jobs(self):
        # งานที่วิ่งอยู่แล้วมีเครื่องถืออยู่ ไม่ใช่ปัญหานี้
        self.assertIn("j.status === 'queued'", fn("function strandedKinds()"))

    def test_a_kind_is_stranded_only_when_no_machine_takes_it(self):
        # every() ไม่ใช่ some() — เครื่องเดียวที่รับได้ก็พอแล้ว
        self.assertIn("alive.every((w) => w.kinds && !w.kinds.includes(k))",
                      fn("function strandedKinds()"))

    def test_an_old_worker_without_kinds_is_assumed_capable(self):
        """`w.kinds &&` คือจุดนี้ — เตือนผิดแย่กว่าไม่เตือน."""
        body = fn("function strandedKinds()")
        self.assertIn("w.kinds &&", body)

    def test_no_machine_online_is_not_this_problem(self):
        # มีข้อความของตัวเองอยู่แล้ว ("ไม่มีเครื่องประมวลผลออนไลน์") อย่าไปทับ
        self.assertIn("if (!alive.length) return [];", fn("function strandedKinds()"))


class TestTheChip(unittest.TestCase):

    def test_it_no_longer_says_ready_when_work_is_stranded(self):
        body = fn("function renderDeviceChip(ws)")
        self.assertIn("strandedKinds()", body)
        self.assertIn("เครื่องออนไลน์ · ไม่มีเครื่องรับงาน", body)

    def test_the_old_wording_survives_when_everything_is_fine(self):
        self.assertIn("เครื่องพร้อม", fn("function renderDeviceChip(ws)"))

    def test_the_offline_message_is_unchanged(self):
        self.assertIn("ไม่มีเครื่องประมวลผลออนไลน์", fn("function renderDeviceChip(ws)"))

    def test_the_dot_turns_warning_coloured(self):
        self.assertIn("'stuck'", fn("function renderDeviceChip(ws)"))
        self.assertRegex(CSS, r'\.m-chip\[data-state="stuck"\] \.m-chip-dot')


class TestTheJobCard(unittest.TestCase):

    def test_only_a_queued_job_of_a_stranded_kind_is_flagged(self):
        body = fn("function renderJobs()")
        self.assertIn("j.status === 'queued' && stranded.includes(j.kind)", body)

    def test_it_says_the_reason_and_the_age(self):
        body = fn("function renderJobs()")
        self.assertIn("job-stuck", body)
        self.assertIn("queuedFor(j)", body)
        self.assertIn("ไม่มีเครื่องที่ทำงาน", body)

    def test_everything_interpolated_is_escaped(self):
        body = fn("function renderJobs()")
        self.assertIn("esc(queuedFor(j))", body)
        self.assertIn("esc(KIND_LABELS[j.kind] || j.kind)", body)

    def test_the_reason_is_visible(self):
        self.assertRegex(CSS, r"\.job \.job-stuck \{[^}]*var\(--warn\)")


class TestQueuedFor(unittest.TestCase):

    def test_it_does_not_repeat_the_step_text(self):
        """step ของงานที่ค้างคือ "รอคิว" อยู่แล้ว — เวอร์ชันแรกได้ "รอคิวรอคิว 42 นาที"."""
        body = fn("function queuedFor(j)")
        self.assertIn("ค้างมา", body)
        self.assertNotIn("`รอคิว ${", body)

    def test_a_job_without_a_timestamp_still_says_something(self):
        self.assertIn("if (!j.created) return", fn("function queuedFor(j)"))

    def test_the_clock_cannot_run_backwards(self):
        # นาฬิกาเครื่องผู้ใช้กับเซิร์ฟเวอร์ไม่ตรงกันได้ "-3 วินาที" อ่านแล้วงง
        self.assertIn("Math.max(0,", fn("function queuedFor(j)"))


class TestEveryKindHasALabel(unittest.TestCase):
    """เพิ่ม kind ใหม่แล้วลืมใส่ป้ายไทย = ผู้ใช้เห็นคำอังกฤษดิบ ๆ กลางประโยคไทย."""

    def test_the_labels_cover_every_kind_a_worker_can_claim(self):
        block = APP_JS[APP_JS.index("const KIND_LABELS"):]
        block = block[:block.index("};") + 2]
        labelled = set(re.findall(r"(\w+):", block))
        self.assertLessEqual(set(runner.job_kinds({"bot": True})), labelled)


if __name__ == "__main__":
    unittest.main()
