"""BACKLOG #50 — ผลงานที่ถอดเสียงเสร็จแล้วต้องไม่หายเพราะส่งกลับไม่สำเร็จ.

ตั๋วตั้งต้นจากกรณี 413 (payload เกิน MAX_JSON_TRANSCRIPT = 8 MB) แต่พอวัดของจริงในฐาน
production: ประชุมที่ใหญ่ที่สุด 149 นาที / 2,905 segment ส่งจริงราว 0.64 MB — ห่างเพดาน
12 เท่า ต้องยาวราว 31 ชั่วโมงถึงจะชน **413 จึงไม่ใช่ความเสี่ยงที่ใกล้**

ของจริงคือประโยคหลังของตั๋ว: "turns that into a job error — the transcript is discarded
with no retry" — `work()` ยิง POST ครั้งเดียว พลาดเมื่อไรก็เข้า except ที่ไปแจ้ง /error
แล้วบทถอดเสียงทั้งไฟล์หายถาวร เน็ตสะดุดวินาทีเดียว/Vercel ตอบ 502/หมดเวลา = เสียเวลา GPU
เป็นชั่วโมง ซึ่งเกิดง่ายกว่า 413 มาก (worker อยู่บ้าน ยิงข้ามอินเทอร์เน็ตไปหา serverless)

พิสูจน์ว่าไม่ vacuous (ทำระหว่างพัฒนา): ให้ post_result ยิงครั้งเดียวแบบเดิมโดยไม่ save
— เทสต์กลุ่ม retry/พักไว้ในดิสก์ต้องล้มทั้งหมด
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import worker
from meeting_ai.worker import WorkerError


class FakeClient:
    """แทน Client จริง — บันทึกทุกครั้งที่ถูกเรียก และโยนตามสคริปต์ที่กำหนด."""

    def __init__(self, outcomes=None):
        # outcomes = ลิสต์ของ None (สำเร็จ) หรือ WorkerError ต่อการเรียกแต่ละครั้ง
        self.outcomes = list(outcomes or [])
        self.calls = []

    def post_json(self, path, payload, timeout=None):
        self.calls.append({"path": path, "payload": payload, "timeout": timeout})
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        return {"ok": True}


RESULT = {
    "segments": [{"start": 0.0, "end": 1.0, "text": "สวัสดีครับ ประชุมวันนี้", "speaker": "A"}],
    "summary": "## ประเด็นหลัก\n\n- ทดสอบ",
    "language": "th",
    "duration": 3600.0,
}


class PendingDirCase(unittest.TestCase):
    """PENDING_DIR ชี้ไป temp dir เสมอ — ห้ามเขียนลง recordings/ ของเครื่องเจ้าของ."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-pending-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        for target in (mock.patch.object(worker, "PENDING_DIR", self.tmp),
                       mock.patch.object(worker.time, "sleep", lambda *_: None)):
            target.start()
            self.addCleanup(target.stop)

    def saved_files(self):
        return sorted(p.name for p in self.tmp.glob("*.json"))


class TestTransientClassification(unittest.TestCase):
    """แยก "ลองใหม่แล้วมีหวัง" ออกจาก "ลองกี่ครั้งก็เหมือนเดิม"."""

    def test_connection_failure_is_transient(self):
        self.assertTrue(worker._transient(WorkerError("ต่อไม่ติด")))          # status None

    def test_server_errors_are_transient(self):
        for code in (500, 502, 503, 504):
            with self.subTest(code=code):
                self.assertTrue(worker._transient(WorkerError("x", code)))

    def test_timeout_and_rate_limit_are_transient(self):
        self.assertTrue(worker._transient(WorkerError("x", 408)))
        self.assertTrue(worker._transient(WorkerError("x", 429)))

    def test_client_errors_are_permanent(self):
        # 413 = payload ใหญ่เกิน, 400 = ข้อมูลไม่ผ่านการตรวจ — ยิงซ้ำก็ได้ผลเดิม
        for code in (400, 404, 413, 422):
            with self.subTest(code=code):
                self.assertFalse(worker._transient(WorkerError("x", code)))


class TestPostResult(PendingDirCase):

    def test_success_on_first_try_leaves_nothing_behind(self):
        client = FakeClient()
        worker.post_result(client, "job-1", RESULT)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(self.saved_files(), [])

    def test_transient_failure_is_retried_until_it_works(self):
        client = FakeClient([WorkerError("ต่อไม่ติด"), WorkerError("x", 503), None])
        worker.post_result(client, "job-2", RESULT)
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(self.saved_files(), [], "สำเร็จแล้วต้องไม่มีไฟล์ค้าง")

    def test_permanent_failure_is_not_retried(self):
        # ยิงซ้ำ 413 ไปห้าครั้งคือเสียเวลาเปล่าและกันช่องงานไว้นานขึ้น
        client = FakeClient([WorkerError("payload ใหญ่เกิน", 413)])
        with self.assertRaises(WorkerError):
            worker.post_result(client, "job-3", RESULT)
        self.assertEqual(len(client.calls), 1, "4xx ต้องไม่ลองใหม่")

    def test_gives_up_after_the_retry_budget(self):
        client = FakeClient([WorkerError("ต่อไม่ติด")] * 20)
        with self.assertRaises(WorkerError):
            worker.post_result(client, "job-4", RESULT)
        self.assertEqual(len(client.calls), worker.RESULT_RETRIES)

    def test_the_transcript_is_kept_on_disk_when_sending_fails(self):
        client = FakeClient([WorkerError("payload ใหญ่เกิน", 413)])
        with self.assertRaises(WorkerError):
            worker.post_result(client, "job-5", RESULT)
        self.assertEqual(self.saved_files(), ["job-5.json"])
        saved = json.loads((self.tmp / "job-5.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["job_id"], "job-5")
        self.assertEqual(saved["result"], RESULT, "ต้องเก็บผลงานครบทุกฟิลด์ ไม่ใช่แค่สรุป")

    def test_the_error_sent_upstream_hides_the_worker_path(self):
        # ข้อความนี้ไปโผล่ในการ์ดงานของเจ้าของการประชุม — เส้นทางไฟล์ในเครื่อง worker
        # ไม่ใช่ข้อมูลที่เขาควรเห็น (BACKLOG #40)
        client = FakeClient([WorkerError("payload ใหญ่เกิน", 413)])
        with self.assertRaises(WorkerError) as ctx:
            worker.post_result(client, "job-6", RESULT)
        msg = str(ctx.exception)
        self.assertNotIn(str(self.tmp), msg)
        self.assertNotIn("job-6.json", msg)
        self.assertIn("เก็บไว้ที่เครื่องประมวลผล", msg)


class TestFlushPending(PendingDirCase):

    def _drop(self, job_id: str, result=None) -> Path:
        self.tmp.mkdir(parents=True, exist_ok=True)
        path = self.tmp / f"{job_id}.json"
        path.write_text(json.dumps({"job_id": job_id, "reason": "ทดสอบ",
                                    "result": result if result is not None else RESULT},
                                   ensure_ascii=False), encoding="utf-8")
        return path

    def test_pending_results_are_resent_and_removed(self):
        self._drop("job-a")
        self._drop("job-b")
        client = FakeClient()
        self.assertEqual(worker.flush_pending(client), 2)
        self.assertEqual(self.saved_files(), [])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["payload"], RESULT)

    def test_a_file_is_kept_when_resending_still_fails(self):
        self._drop("job-c")
        client = FakeClient([WorkerError("ต่อไม่ติด")])
        self.assertEqual(worker.flush_pending(client), 0)
        self.assertEqual(self.saved_files(), ["job-c.json"], "ส่งไม่ได้ก็ห้ามลบทิ้ง")

    def test_a_corrupt_file_does_not_stop_the_others(self):
        (self.tmp / "broken.json").write_text("{ไม่ใช่ JSON", encoding="utf-8")
        self._drop("job-d")
        client = FakeClient()
        self.assertEqual(worker.flush_pending(client), 1)
        self.assertIn("broken.json", self.saved_files())

    def test_nothing_pending_is_not_an_error(self):
        self.assertEqual(worker.flush_pending(FakeClient()), 0)


class TestPayloadSize(unittest.TestCase):
    """ensure_ascii=False — เหตุผลที่ 413 ยิ่งไกลออกไปอีกเท่าตัวสำหรับภาษาไทย."""

    def test_thai_payload_is_about_half_the_size(self):
        big = {"segments": [{"start": i, "end": i + 1, "text": "ประชุมทีมผลิตภัณฑ์ประจำสัปดาห์",
                             "speaker": "คุณสมชาย"} for i in range(500)]}
        escaped = len(json.dumps(big).encode("utf-8"))
        utf8 = len(json.dumps(big, ensure_ascii=False).encode("utf-8"))
        self.assertLess(utf8, escaped * 0.6, f"escaped {escaped} vs utf-8 {utf8}")

    def test_the_client_sends_utf8_not_escaped(self):
        sent = {}

        class C(worker.Client):
            def _request(self, method, path, *, data=None, ctype=None, timeout=None):
                sent["data"] = data
                return 200, {"ok": True}

        C("http://x", "t").post_json("/p", {"text": "ประชุม"})
        self.assertIn("ประชุม".encode("utf-8"), sent["data"])
        self.assertNotIn(b"\\u0e1b", sent["data"])


if __name__ == "__main__":
    unittest.main()
