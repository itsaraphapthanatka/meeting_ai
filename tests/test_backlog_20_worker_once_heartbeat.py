"""BACKLOG #20 — `worker --once` หยุดเต้น heartbeat ก่อนงานที่ค้างอยู่จะจบ.

ตั๋ว: docs/tickets/BUG-020-worker-once-heartbeat-gap.md

Root cause เดิม (worker.py ที่ HEAD ก่อนแก้):
  * `beat()` วนด้วย `while not stopping["flag"]` และคืนค่าทันทีที่ธงถูกตั้ง
  * โหมด `--once` ตั้ง `stopping["flag"] = True` **ทันทีที่คว้างานมาได้** (ก่อนงานจะเริ่มทำด้วยซ้ำ)
  -> ตลอดเวลาที่งานกำลังทำอยู่ ไม่มี heartbeat ออกไปเลย ครบ 75 วิ
     (pgstore.WORKER_STALE_SECONDS) เซิร์ฟเวอร์ถือว่าเครื่องนี้ตายแล้ว: workers_list()
     ตอบ status='gone' และ worker_capabilities() มองไม่เห็นเครื่องนี้
  * แล้ว `for _ in range(600)` รองานค้างแค่ 10 นาทีก่อนจะ return — สั้นกว่างานจริงมาก
     (งานบอทนั่งในห้องได้ถึง 180 นาที) พอโปรเซสออก เธรดงานที่เป็น daemon ถูกฆ่ากลางคัน
     งานค้างสถานะ running ไม่มีใครรายงานต่อ -> pgstore.jobs_reap() คืนงานเข้าคิว
     (งาน kind='bot' ถูกตีเป็น error) ทั้งที่งานเดิมเกือบเสร็จแล้ว

เทสต์นี้รัน `worker.run(once=True)` ของจริงกับเซิร์ฟเวอร์ปลอมที่พูด worker API ได้จริง
(ไม่แตะ LLM/GPU/Docker: patch runner.HANDLERS['summarize'] เป็นงานหน่วงเวลาเปล่า ๆ)
เวลาถูกบีบผ่านค่าคงที่ระดับโมดูล — `HEARTBEAT_SEC` (จริง 20 วิ) และ `DRAIN_MAX_SEC`
(จริง 6 ชม.) ต้องยังเป็นตัวแปรระดับโมดูลที่ patch ได้ ไม่งั้นเทสต์ชุดนี้จะพังทันที

ตรวจความไม่กลวงแล้วด้วยมือ 2026-09-17 (แก้ชั่วคราว ไม่ได้ commit):
  * ย้อน `beat()` กลับเป็น `while not stopping["flag"]` -> test_heartbeat_keeps_beating_...
    ล้มด้วย "heartbeat ระหว่างทำงาน: []" (0 ครั้ง) อีกสามเทสต์ยังผ่าน
  * ย้อน drain กลับเป็น `for _ in range(600)` -> test_drain_wait_is_still_bounded ล้ม
    (run() รอจนงานจบ เพดานที่สั่งไว้ไม่มีผล)
ส่วนอาการ "โปรเซสออกทั้งที่งานยังเดิน แล้วเซิร์ฟเวอร์ reap งานนั้น" พิสูจน์แบบ end-to-end
ด้วยสคริปต์รีโปรแยก (โปรเซสจริง + เซิร์ฟเวอร์ปลอมที่มี reaper) ตามที่บันทึกไว้ในตั๋ว —
เทสต์ในไฟล์นี้จับเฉพาะเงื่อนไขฝั่ง worker ที่ทำให้อาการนั้นเกิด
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from _harness import server as _server  # noqa: F401  ทุกไฟล์เทสต์ import _harness ก่อนเสมอ (ตั้ง env)

from meeting_ai import runner, worker  # noqa: E402

JOB_ID = "20260917-120000-abcdef"
FAST_HEARTBEAT = 1.0     # แทน 20 วิของจริง (0.5 คือ granularity ของลูป sleep ใน beat())
JOB_SECONDS = 3.0        # งานยาวกว่าช่วงเต้นหลายเท่า เพื่อให้เห็นว่าเต้นต่อระหว่างทำงาน


class _FakeWorkerAPI(BaseHTTPRequestHandler):
    """พูดเฉพาะ 4 เส้นที่ worker ใช้: heartbeat, claim, progress, result/error."""

    protocol_version = "HTTP/1.1"
    log = None   # dict ที่เทสต์ยัดให้ (ตั้งใน _Case.setUp)

    def log_message(self, *a):  # เงียบ — ไม่งั้น console เต็มไปด้วย access log
        pass

    def _reply(self, code: int, payload: dict | None = None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(code)
        if body:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):  # noqa: N802  (ชื่อมาจาก BaseHTTPRequestHandler)
        size = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(size) if size else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        rec, now = self.log, time.monotonic()
        with rec["lock"]:
            if self.path == "/api/worker/heartbeat":
                rec["beats"].append((now, body.get("status"), body.get("job")))
                return self._reply(200, {"ok": True, "stale_after": 75})
            if self.path == "/api/worker/claim":
                if rec["claimed_at"] is not None:
                    return self._reply(204)
                rec["claimed_at"] = now
                return self._reply(200, {"id": JOB_ID, "kind": "summarize", "title": "งานทดสอบ"})
            if self.path.endswith("/progress"):
                rec["progress"].append(now)
                return self._reply(200, {"ok": True, "stop": False})
            if self.path.endswith("/result"):
                rec["result_at"] = now
                return self._reply(200, {"ok": True})
            if self.path.endswith("/error"):
                rec["error_at"] = now
                rec["error"] = str(body.get("error"))
                return self._reply(200, {"ok": True})
        self._reply(404, {"error": "ไม่มีเส้นนี้"})


class WorkerOnceHeartbeatTest(unittest.TestCase):
    """งานที่ยังทำอยู่ต้องมี heartbeat คุ้มกัน และ worker ห้ามออกก่อนงานจบ."""

    def setUp(self) -> None:
        self.rec = {"lock": threading.Lock(), "beats": [], "progress": [],
                    "claimed_at": None, "result_at": None, "error_at": None, "error": None}
        handler = type("Handler", (_FakeWorkerAPI,), {"log": self.rec})
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.api = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.job_done = threading.Event()
        self.cancel = threading.Event()   # ให้เทสต์เร่งงานปลอมให้จบก่อนปิดเซิร์ฟเวอร์ปลอม

    def _slow_job(self, spec: dict, progress) -> dict:
        """งานปลอมที่กินเวลา JOB_SECONDS วินาทีและรายงาน progress ระหว่างทาง."""
        steps = int(JOB_SECONDS)
        for i in range(steps):
            progress(f"ขั้นที่ {i + 1}", (i + 1) / (steps + 1))   # เปลี่ยนข้อความทุกครั้ง ไม่ติด PROGRESS_MIN_GAP
            if self.cancel.wait(1.0):
                break
        self.job_done.set()
        return {"summary": "ทดสอบ", "segments": [], "warning": None}

    def _run_worker(self, drain: float | None = None) -> tuple[int, float, str]:
        """รัน worker.run(once=True) จนจบ — คืน (exit code, เวลาที่ return, stderr).

        รันในเธรดแยกเพราะ run() ติดตั้ง SIGINT handler เมื่ออยู่เธรดหลัก (จะค้างไว้ถึงเทสต์อื่น)
        — ในเธรดรอง signal.signal โยน ValueError แล้วโค้ดจับไว้เอง
        """
        out: dict = {}
        err = io.StringIO()
        patches = [
            mock.patch.dict(runner.HANDLERS, {"summarize": self._slow_job}),
            mock.patch.object(runner, "machine_caps", lambda: {
                "local": True, "api": False, "diarize": False, "diarize_missing": [],
                "bot": False, "bot_missing": ["docker"]}),
            mock.patch.object(worker, "describe_gpu", lambda: None),
            mock.patch.object(worker, "HEARTBEAT_SEC", FAST_HEARTBEAT),
        ]
        if drain is not None:
            patches.append(mock.patch.object(worker, "DRAIN_MAX_SEC", drain))

        def go():
            out["code"] = worker.run(self.api, "test-token", once=True, poll=0.2,
                                     name="test-worker", max_bots=1)
            out["at"] = time.monotonic()

        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            # ข้อความของ worker เป็นภาษาไทย — กลืนไว้ ไม่ให้ชนคอนโซล cp874 ของเครื่องที่รันเทสต์
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(contextlib.redirect_stderr(err))
            th = threading.Thread(target=go, name="worker-under-test", daemon=True)
            th.start()
            th.join(timeout=60)
            self.still_running_at_return = not self.job_done.is_set()
            if drain is not None:
                # เคสเพดาน: งานปลอมยังเดินอยู่หลัง run() คืนค่า — เร่งให้จบแล้วรอให้ยิงผลกลับ
                # ตอนที่ยังดักเอาต์พุตอยู่ ไม่งั้นข้อความไทยของเธรดที่เหลือหลุดออกคอนโซล cp874
                # และไปยิงใส่พอร์ตที่ tearDown ปิดไปแล้ว
                self.cancel.set()
                self.job_done.wait(10)
                time.sleep(0.5)
        self.assertFalse(th.is_alive(), "worker.run() ไม่ยอมจบภายใน 60 วินาที")
        return out["code"], out["at"], err.getvalue()

    def test_heartbeat_keeps_beating_while_the_job_runs(self):
        code, returned_at, _ = self._run_worker()
        with self.rec["lock"]:
            claimed, result_at = self.rec["claimed_at"], self.rec["result_at"]
            beats = list(self.rec["beats"])
        self.assertEqual(0, code)
        self.assertIsNotNone(claimed, "เซิร์ฟเวอร์ปลอมต้องถูก claim จริง ไม่งั้นเทสต์กลวง")
        self.assertIsNotNone(result_at, "ต้องมีการส่งผลงานกลับมา")

        during = [b for b in beats if claimed < b[0] < result_at]
        # งานยาว JOB_SECONDS วิ เต้นทุก FAST_HEARTBEAT วิ -> ต้องได้อย่างน้อย 2 ครั้งระหว่างทำงาน
        # ก่อนแก้: 0 ครั้ง (beat() คืนค่าทันทีที่ --once ตั้ง stopping flag ตอน claim)
        self.assertGreaterEqual(len(during), 2, f"heartbeat ระหว่างทำงาน: {during}")
        # ต้องบอกด้วยว่า "busy + ถืองานใบไหน" ไม่ใช่เต้นเปล่า ๆ (หน้าเว็บโชว์ job_title จากตรงนี้)
        # ครั้งแรกหลัง claim อาจยังเป็น idle ได้ตามจังหวะ: beat() อ่านค่า active ก่อนที่
        # เธรดหลักจะลงทะเบียนงานเสร็จ — เป็นการแข่งที่ไม่มีพิษภัย จึงนับเฉพาะครั้งหลังจากนั้น
        busy = [b for b in during if b[1] == "busy" and b[2] == JOB_ID]
        self.assertGreaterEqual(len(busy), 2, f"heartbeat ที่บอกว่าถืองานอยู่: {during}")
        self.assertTrue(all(b[1] == "busy" and b[2] == JOB_ID for b in during[1:]),
                        f"heartbeat ระหว่างทำงานต้องบอกว่า busy + ถืองาน {JOB_ID}: {during}")
        # ช่องว่างที่ยาวที่สุดต้องไม่เกินหน้าต่างที่เซิร์ฟเวอร์ถือว่าหลุด (จริง 75 วิ = 3.75 จังหวะ)
        stamps = [claimed] + [b[0] for b in during] + [result_at]
        worst = max(b - a for a, b in zip(stamps, stamps[1:]))
        self.assertLess(worst, FAST_HEARTBEAT * 3.75,
                        f"เงียบนานสุด {worst:.1f}s เกินหน้าต่างที่เซิร์ฟเวอร์ถือว่า worker ตาย")

    def test_worker_does_not_exit_before_the_running_job_reports_back(self):
        code, returned_at, _ = self._run_worker()
        with self.rec["lock"]:
            result_at, error_at = self.rec["result_at"], self.rec["error_at"]
        self.assertEqual(0, code)
        self.assertIsNone(error_at, "งานนี้ต้องไม่จบด้วย error")
        self.assertIsNotNone(result_at)
        # ออกก่อนงานส่งผล = เธรด daemon ถูกฆ่า งานค้าง running แล้วโดน jobs_reap() คืนคิว
        self.assertLess(result_at, returned_at,
                        "worker.run() คืนค่าก่อนงานที่ค้างอยู่จะรายงานผลกลับ")
        self.assertTrue(self.job_done.is_set(), "งานต้องทำจนจบจริง ไม่ใช่ถูกตัดกลางคัน")

    def test_heartbeat_stops_after_the_worker_is_done(self):
        """เต้นต่อได้ แต่ต้องหยุดเมื่อไม่มีงานค้างแล้ว — ไม่ใช่เต้นค้างตลอดชีพโปรเซส."""
        self._run_worker()
        with self.rec["lock"]:
            after_exit = len(self.rec["beats"])
        time.sleep(FAST_HEARTBEAT * 2.5)
        with self.rec["lock"]:
            self.assertEqual(after_exit, len(self.rec["beats"]),
                             "ยังมี heartbeat ออกมาหลัง run() จบ = เธรด beat ไม่ยอมตาย")

    def test_drain_wait_is_still_bounded(self):
        """เพดานรองานยังมีอยู่ — งานที่ค้างนานเกินเพดานต้องออกได้ พร้อมบอกว่าทิ้งงานไหนไว้."""
        started = time.monotonic()
        code, returned_at, err = self._run_worker(drain=0.5)
        self.assertEqual(0, code)
        self.assertTrue(self.still_running_at_return,
                        "งานปลอมจบไปก่อน — เทสต์นี้ไม่ได้วัดเพดานอะไรเลย")
        self.assertLess(returned_at - started, JOB_SECONDS,
                        "เพดาน DRAIN_MAX_SEC ไม่ทำงาน — รอจนงานจบทั้งที่สั่งให้เลิกรอแล้ว")
        self.assertIn(JOB_ID, err, "ต้องบอกทาง stderr ว่าออกทั้งที่งานไหนยังค้าง")
        # เพดานค่าจริงต้องครอบงานบอทที่ยาวที่สุดได้ (runner.bot_job: max_minutes 180 + ถอดเสียง + สรุป)
        self.assertGreaterEqual(worker.DRAIN_MAX_SEC, 3 * 3600)


if __name__ == "__main__":
    unittest.main()
