"""BACKLOG #51 + #52 — คำตอบของเซิร์ฟเวอร์ต้องไม่บอกโครงสร้างภายในให้คนนอก.

#51: `except Exception: self._error(500, str(e))` ส่งข้อความของ Python ออกไปตรง ๆ —
     "invalid literal for int() with base 10: 'abc'" บอกว่าเราแปลงอะไรอยู่,
     ข้อผิดพลาดของ psycopg บอกโฮสต์/พอร์ต/ชื่อฐานข้อมูลจริง (Neon), ของ STT บอก endpoint
#52: ทุกคำตอบมี `Server: meeting_ai Python/3.12.10` บอกเวอร์ชันล่ามให้คนสแกนหา CVE

เส้นแบ่งที่ตั้งใจ: ข้อความภาษาไทยที่เราเขียนเองให้ผู้ใช้อ่าน (BadBody, 404, RuntimeError
จาก apply_result) **ต้องยังส่งออกไปเหมือนเดิม** — ถ้าเหมารวมทุกอย่างเป็นข้อความกลาง ๆ
ผู้ใช้จะแก้ปัญหาของตัวเองไม่ได้เลย เทสต์ในไฟล์นี้จึงคุมทั้งสองด้าน

พิสูจน์ว่าไม่ vacuous (ทำระหว่างพัฒนา): เปลี่ยน _oops() กลับไปส่ง str(e) — เทสต์กลุ่มแรกล้ม;
เปลี่ยน version_string() กลับเป็นของ base class — เทสต์ #52 ล้ม
"""

from __future__ import annotations

import io
import sys
import urllib.parse
import unittest
from unittest import mock

from _harness import LocalCase
from meeting_ai.web import server

# ข้อความแบบที่ Python สร้างเอง ไม่ใช่ที่เราเขียน — ห้ามหลุดออกไปในคำตอบ
PYTHON_LEAK = "invalid literal for int() with base 10: 'abc'"


class ErrorHygieneCase(LocalCase):
    """ยิงคำขอปกติแต่บังคับให้ชั้นล่างพัง แล้วดูว่าอะไรหลุดออกมากับคำตอบบ้าง."""

    def _boom(self, exc: Exception):
        """ทำให้ GET /api/meetings ระเบิดที่ชั้น store (จุดที่ _route() ดักไว้เป็นด่านสุดท้าย)."""
        return mock.patch.object(server.store, "search", side_effect=exc)

    def _capture_stderr(self):
        buf = io.StringIO()
        patch = mock.patch.object(sys, "stderr", buf)
        patch.start()
        self.addCleanup(patch.stop)
        return buf


class TestInternalErrorsStayInternal(ErrorHygieneCase):

    def test_python_message_does_not_reach_the_client(self):
        with self._boom(ValueError(PYTHON_LEAK)):
            self._capture_stderr()
            status, body, _ = self.get("/api/meetings")
        self.assertEqual(status, 500)
        text = str(body)
        self.assertNotIn(PYTHON_LEAK, text)
        self.assertNotIn("ValueError", text)
        self.assertNotIn("Traceback", text)

    def test_client_gets_a_thai_message_with_a_reference_code(self):
        with self._boom(ValueError(PYTHON_LEAK)):
            self._capture_stderr()
            _, body, _ = self.get("/api/meetings")
        err = body["error"]
        self.assertIn("เกิดข้อผิดพลาดภายในเซิร์ฟเวอร์", err)
        self.assertIn("รหัสอ้างอิง", err)

    def test_the_detail_is_logged_with_the_same_reference(self):
        # รหัสจะไร้ประโยชน์ถ้าเจ้าของหาใน log ไม่เจอ — ต้องเป็นรหัสเดียวกันทั้งสองฝั่ง
        buf = self._capture_stderr()
        with self._boom(ValueError(PYTHON_LEAK)):
            _, body, _ = self.get("/api/meetings")
        ref = body["error"].split("รหัสอ้างอิง")[1].strip(" )")
        logged = buf.getvalue()
        self.assertIn(ref, logged, "ไม่พบรหัสอ้างอิงใน log")
        self.assertIn(PYTHON_LEAK, logged, "รายละเอียดต้องถูกเก็บไว้ ไม่ใช่ทิ้ง")
        self.assertIn("Traceback", logged)

    def test_query_string_is_not_written_to_the_log(self):
        # ค่าที่ผู้ใช้ค้นหา (และโทเคนที่อาจอยู่ใน query) ไม่ควรไปนอนอยู่ใน log ของเซิร์ฟเวอร์
        secret = "ความลับของบริษัท"
        buf = self._capture_stderr()
        with self._boom(ValueError(PYTHON_LEAK)):
            # http.client เข้ารหัส request line เป็น ascii — ต้อง percent-encode เอง
            self.get(f"/api/meetings?q={urllib.parse.quote(secret)}")
        logged = buf.getvalue()
        self.assertIn("/api/meetings", logged)
        self.assertNotIn(secret, logged)
        self.assertNotIn(urllib.parse.quote(secret), logged)

    def test_each_failure_gets_its_own_reference(self):
        self._capture_stderr()
        refs = set()
        for _ in range(3):
            with self._boom(ValueError(PYTHON_LEAK)):
                _, body, _ = self.get("/api/meetings")
            refs.add(body["error"].split("รหัสอ้างอิง")[1].strip(" )"))
        self.assertEqual(len(refs), 3, f"รหัสซ้ำกัน: {refs}")


class TestDeliberateMessagesSurvive(ErrorHygieneCase):
    """ด้านตรงข้าม — ข้อความที่เราเขียนเองให้ผู้ใช้อ่านต้องไม่ถูกกลบไปด้วย."""

    def test_bad_json_still_says_what_is_wrong(self):
        # POST /api/meetings อ่าน body เป็นอย่างแรก จึงไปถึง BadBody ได้โดยไม่ต้องเตรียมอะไร
        status, body, _ = self.post_raw("/api/meetings", "{ไม่ใช่ JSON".encode("utf-8"),
                                        content_type="application/json")
        self.assertEqual(status, 400, body)
        self.assertIn("JSON", body["error"])

    def test_unknown_meeting_still_says_not_found(self):
        status, body, _ = self.get("/api/meetings/20260101-000000-abcdef")
        self.assertEqual(status, 404)
        self.assertIn("ไม่พบการประชุม", body["error"])

    def test_method_not_allowed_still_explains(self):
        status, body, _ = self._do("DELETE", "/static/app.js")
        self.assertEqual(status, 405)
        self.assertIn("method", body["error"])


class TestServerHeader(LocalCase):
    """#52 — หัวข้อ Server ต้องไม่บอกเวอร์ชันล่าม."""

    def test_header_has_no_interpreter_version(self):
        _, _, headers = self.get("/api/config")
        server_hdr = headers.get("Server", "")
        self.assertEqual(server_hdr, "meeting_ai", f"ได้ {server_hdr!r}")
        self.assertNotIn("Python", server_hdr)
        self.assertNotIn(sys.version.split()[0], server_hdr)

    def test_error_responses_hide_it_too(self):
        # คนสแกนมักยิง path มั่ว ๆ ให้ได้ 404 ก่อน — หัวข้อต้องสะอาดทุกคำตอบ ไม่ใช่เฉพาะ 200
        _, _, headers = self.get("/api/no-such-endpoint")
        self.assertNotIn("Python", headers.get("Server", ""))


if __name__ == "__main__":
    unittest.main()
