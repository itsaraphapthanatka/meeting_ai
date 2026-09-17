"""BUG-011 — _body_json() อ่าน Content-Length โดยไม่มีเพดาน + _clean_segments() ไม่จำกัด

ก่อนแก้: `_body_json()` เรียก `self.rfile.read(length)` ตรงๆ ตาม Content-Length ที่ client
อ้าง ไม่มีการเทียบกับเพดานใดๆ เลย (body 20 MB ที่ POST /api/meetings เคยได้ 201) และ
`_clean_segments()` วนลิสต์ไม่จำกัดจำนวนและไม่ตัดความยาว `text` (ตัด `speaker` อย่างเดียว)

หลังแก้ (server.py): `_body_json(limit=...)` ปฏิเสธด้วย 413 ก่อนอ่าน body เข้าแรม
(เทียบ Content-Length กับ limit ก่อน `rfile.read()` เสมอ) เพดานแยกเป็นสองระดับ —
MAX_JSON_BODY (64 KB) สำหรับ control-plane และ MAX_JSON_TRANSCRIPT (8 MB) สำหรับสองเส้นที่
ถือ transcript ทั้งก้อน (POST /api/worker/jobs/{id}/result, PATCH /api/meetings/{id})
`_clean_segments()` เพิ่ม MAX_SEGMENTS (50,000 รายการ) และ MAX_SEGMENT_TEXT (5,000 ตัวอักษร)

ข้อควรระวังที่สุดของตั๋วนี้ (จากตัวตั๋วเอง): "อย่าใส่เพดานเดียวแบบ 1 MB ให้ทุก call site" —
เพดานใหญ่ต้องมี headroom เหนือของจริงวันนี้ (2,905 segments / 334,634 bytes) จริงๆ ไม่ใช่แค่
เกินนิดเดียว ชุดนี้เลยมีเทสต์ "เพดานใหญ่ยังใช้งานได้จริงที่ ~3,000 segments / ~900 KB" คู่กับ
เทสต์ "เกินเพดานจริง -> 413" เพื่อกันคนมาปรับเพดานให้แคบลงทีหลังโดยไม่รู้ตัว (ดู docstring ของ
แต่ละคลาสด้านล่าง)

รอบตรวจของ security-engineer (docs/runbooks/security/AUDIT-2026-09-16-bug011-body-caps.md) เจอว่า
การแก้ข้างบนเปิดช่อง **request smuggling / keep-alive desync** ได้ (body ที่ยังไม่ถูกอ่านจนหมด
ตอนตอบ 401/403/404/413/501 กลายเป็น "คำขอถัดไป" บนคอนเนกชันเดียวกัน) กับรับ `NaN`/`Infinity`
เป็นเวลา segment จนทำให้การประชุมเปิดไม่ขึ้นถาวร backend-dev แก้ในรอบเดียวกัน (ยังไม่ commit
แยกจาก 39cb064): `_begin_body()` ตรวจ framing ก่อนตอบอะไรทั้งสิ้น ปฏิเสธ `Transfer-Encoding`
ที่ไม่ใช่ identity (501) และ `Content-Length` ที่ซ้ำ/ไม่ใช่เลขล้วนตาม RFC 9110 (400) กฎกลาง
"คอนเนกชันที่เคยมี request body จะไม่ถูกใช้ซ้ำ" (`Handler.body_bytes` + `_send()`) และ
`_clean_segments()` เพิ่ม `math.isfinite()` ให้ start/end คลาสท้ายไฟล์นี้ครอบส่วนนี้

**เจตนา**: ไม่ใส่เทสต์วัดเวลาแบบ wall-clock (slowloris/`_drain_rejected_body` deadline) ไว้ในชุดนี้
เพราะจะ flake บน CI ที่โหลดไม่แน่นอน — backend-dev วัดจริงแล้ว (หยอด 1 ไบต์ทุก 0.4 วินาที):
เธรดเดี่ยวจาก 21.62 วินาที เหลือ 2.03 วินาที และ 100 คอนเนกชันพร้อมกันถูกปล่อยครบภายใน 2.59
วินาที ตัวเลขนี้บันทึกไว้ใน docs/LEARNINGS.md แทน ไม่ใช่ใน assert
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from _harness import CloudCase, LocalCase, filestore, jobs, new_mid, server
from meeting_ai.config import config

# > MAX_JSON_BODY (64 KB) พอให้ overhead ของคีย์อื่นๆ ใน JSON ไม่ทำให้หลุดเพดานไปเอง
PAD_OVER_CONTROL_CAP = "y" * 70_000


def _raw_bytes(method: str, path: str, headers: list[tuple[str, str]], body: bytes = b"") -> bytes:
    """สร้าง byte ของคำขอ HTTP/1.1 ดิบก้อนเดียว — ใช้คุม header ที่ซ้ำกัน/framing แปลกๆ ที่

    `http.client` ทำให้ไม่ได้ (ดู `_HttpCaseMixin.raw_send_and_collect` ใน `_harness.py`)
    """
    head = f"{method} {path} HTTP/1.1\r\n"
    for k, v in headers:
        head += f"{k}: {v}\r\n"
    head += "\r\n"
    return head.encode("utf-8") + body


def _nested_get(port: int, path: str = "/api/config") -> bytes:
    """คำขอ HTTP ที่สมบูรณ์ 1 อัน — ใช้เป็น "ของแถม" ที่พยายามลักลอบส่งบนคอนเนกชันเดียวกัน

    (request smuggling) ถ้าเซิร์ฟเวอร์ไม่ปิดคอนเนกชัน/ไม่ระบาย body ค้างให้ถูกต้อง อันนี้จะถูก
    แยกออกมาตีความเป็นคำขอที่สอง แล้วเซิร์ฟเวอร์จะตอบ response เพิ่มอีกก้อนโดยไม่มีใครขอ
    """
    return _raw_bytes("GET", path, [("Host", f"127.0.0.1:{port}")])


def _segments(n: int, text_chars: int = 180) -> list[dict]:
    """สร้าง segments ปลอมให้ขนาดใกล้เคียงของจริง (ดู docstring ของตั๋ว: เฉลี่ย ~38 KB / 2,905

    รายการ ~= ~13 ไบต์ต่อรายการ แต่ทดสอบ "ยังใช้งานได้ที่ ~900 KB" ต้องการรายการที่หนักกว่านั้น
    เพื่อพิสูจน์ headroom จริง ไม่ใช่แค่ค่าเฉลี่ยของวันนี้)
    """
    return [
        {"start": round(i * 1.5, 2), "end": round(i * 1.5 + 1.4, 2),
         "speaker": f"SPEAKER_{i % 4}",
         "text": f"ประโยคทดสอบที่ {i} " + ("ก" * text_chars)}
        for i in range(n)
    ]


class TestBug011ControlPlaneBodyCaps(CloudCase):
    """AC2 — settings/translate/visibility/login (control-plane) ปฏิเสธที่ 64 KB แต่ขนาดปกติผ่าน.

    ใช้ CloudCase เพราะ /api/auth/* และ PATCH .../visibility ทำงานเฉพาะโหมด cloud
    (backend.auth_required()) M1 เป็นของ uid_a ตาม seed มาตรฐานของ CloudCase._seed()
    """

    def test_bug_011_ac2_settings_over_64kb_rejected_with_413(self):
        status, body, _ = self.post_json(
            "/api/settings", {"live_recording_enabled": True, "pad": PAD_OVER_CONTROL_CAP},
            cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 413)
        self.assertIn("64 KB", str(body))

    def test_bug_011_ac2_settings_normal_size_accepted(self):
        status, body, _ = self.post_json(
            "/api/settings", {"live_recording_enabled": True},
            cookies={"mai_session": self.tokAdm})
        self.assertEqual(status, 200)
        self.assertTrue(body["live_recording_enabled"])

    def test_bug_011_ac2_translate_over_64kb_rejected_with_413(self):
        status, body, _ = self.post_json(
            f"/api/meetings/{self.M1}/translate", {"lang": "en", "pad": PAD_OVER_CONTROL_CAP},
            cookies={"mai_session": self.tokA})
        self.assertEqual(status, 413)

    def test_bug_011_ac2_translate_normal_size_accepted(self):
        status, body, _ = self.post_json(
            f"/api/meetings/{self.M1}/translate", {"lang": "en"},
            cookies={"mai_session": self.tokA})
        self.assertEqual(status, 202)

    def test_bug_011_ac2_visibility_over_64kb_rejected_with_413(self):
        status, body, _ = self.patch_json(
            f"/api/meetings/{self.M1}/visibility", {"visibility": "team", "pad": PAD_OVER_CONTROL_CAP},
            cookies={"mai_session": self.tokA})
        self.assertEqual(status, 413)

    def test_bug_011_ac2_visibility_normal_size_accepted(self):
        status, body, _ = self.patch_json(
            f"/api/meetings/{self.M1}/visibility", {"visibility": "team"},
            cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200)
        self.assertEqual(body["visibility"], "team")

    def test_bug_011_ac2_login_over_64kb_rejected_with_413(self):
        status, body, _ = self.post_json(
            "/api/auth/login", {"email": "a@example.com", "password": PAD_OVER_CONTROL_CAP})
        self.assertEqual(status, 413)

    def test_bug_011_ac2_login_normal_size_not_rejected_by_size_cap(self):
        # FakeStore.verify_password ตอบ None เสมอ (ไม่มีระบบรหัสผ่านจำลอง) — จุดที่ต้องพิสูจน์
        # คือ "ผ่านการเช็คขนาดแล้วไปตายที่ตรรกะล็อกอินจริง" (401) ไม่ใช่ตายที่ 413
        status, body, _ = self.post_json(
            "/api/auth/login", {"email": "a@example.com", "password": "normal-password"})
        self.assertEqual(status, 401)


class TestBug011MeetingsCreateBodyCap(LocalCase):
    """AC1 — POST /api/meetings ด้วย body 20 MB -> 413 และปฏิเสธก่อนอ่าน body ทิ้งจนหมด."""

    def test_bug_011_ac1_create_meeting_20mb_body_returns_413(self):
        big = json.dumps({"title": "x" * 20_000_000}).encode("utf-8")
        self.assertGreater(len(big), 20_000_000)
        status, body, _ = self.post_json("/api/meetings", {"title": "x" * 20_000_000})
        self.assertEqual(status, 413)

    def test_bug_011_ac1_create_meeting_normal_body_returns_201(self):
        status, body, _ = self.post_json("/api/meetings", {"title": "ประชุมทดสอบ"})
        self.assertEqual(status, 201)
        self.assertIn("id", body)

    def test_bug_011_ac1b_huge_declared_length_rejected_before_full_body_read(self):
        """ประกาศ Content-Length ใหญ่มาก (50 MB) แต่ส่งจริงแค่ 1 KB แล้วหยุด — ถ้า server อ่าน

        body ให้ครบก่อนเช็คเพดาน (พฤติกรรมเดิมของตั๋วนี้) มันจะค้างรอ byte ที่เหลือ 49999+ ไบต์
        ที่ไม่มีวันมาถึง แล้ว client (timeout 5 วิ) จะไม่ได้ status กลับมาเลย (-2) เทสต์นี้จึง
        พิสูจน์ทั้ง "413" และ "เร็ว" (ปฏิเสธจาก header ไม่ใช่หลังอ่านครบ) ในเทสต์เดียว
        """
        declared = 50 * 1024 * 1024
        status, text, elapsed = self.raw_request(
            "POST", "/api/meetings",
            {"Content-Type": "application/json", "Content-Length": str(declared),
             "Connection": "close"},
            b"{" + b"x" * 1022,  # ส่งไปแค่ 1 KB เศษ ไม่ใช่ 50 MB ที่ประกาศไว้
        )
        self.assertEqual(status, 413, f"text={text[:200]!r}")
        self.assertLess(elapsed, 3.0, "ต้องปฏิเสธจาก Content-Length header ไม่ใช่รอข้อมูลจริง")


class TestBug011ContentLengthNeverCrashes(LocalCase):
    """Content-Length ที่หายไป/ติดลบ/ไม่ใช่ตัวเลข/ว่าง ต้องไม่ทำให้ 500 (ทั้งเดิมและตอนนี้).

    ใช้ /api/settings (control-plane, โหมดไฟล์ไม่ต้องล็อกอิน) เป็นเป้าเพราะ handler ง่ายที่สุด
    """

    def test_bug_011_content_length_non_numeric_returns_400(self):
        status, text, _ = self.raw_request(
            "POST", "/api/settings",
            {"Content-Type": "application/json", "Content-Length": "abc", "Connection": "close"},
            b"")
        self.assertEqual(status, 400)

    def test_bug_011_content_length_negative_returns_400(self):
        status, text, _ = self.raw_request(
            "POST", "/api/settings",
            {"Content-Type": "application/json", "Content-Length": "-5", "Connection": "close"},
            b"")
        self.assertEqual(status, 400)

    def test_bug_011_content_length_empty_value_returns_200(self):
        status, text, _ = self.raw_request(
            "POST", "/api/settings",
            {"Content-Type": "application/json", "Content-Length": "", "Connection": "close"},
            b"")
        self.assertEqual(status, 200)

    def test_bug_011_content_length_header_absent_returns_200(self):
        status, text, _ = self.raw_request(
            "POST", "/api/settings",
            {"Content-Type": "application/json", "Connection": "close"},
            b"")
        self.assertEqual(status, 200)


class TestBug011LargeRoutesAcceptRealisticSize(LocalCase):
    """AC3 + AC4 — สองเส้นที่ถือ transcript ทั้งก้อนต้องรับของจริงได้ (ไม่ใช่แค่ปฏิเสธของใหญ่).

    ห้าม "แก้ผ่าน" ด้วยการลดเพดานลง (เช่นกลับไปใช้ 1 MB เดียวทั้งระบบ) — ตั๋วนี้เตือนไว้ตรงๆ ว่า
    ของจริงวันนี้มี 2,905 segments / 334,634 bytes เทสต์นี้ยิงที่ ~3,000 segments / ~900 KB
    (เกิน 2,905 ของจริงแต่ยังเล็กกว่า 8 MB มาก) แล้วต้องได้ 200 ไม่ใช่ 413
    """

    def setUp(self) -> None:
        super().setUp()
        self.mid = filestore.new_id()
        filestore.create(mid=self.mid, title="เทสต์ BUG-011 (ลบได้)", audio_name="",
                         source="upload", language="th", duration=0.0, segments=[], summary="")

    # ---------- PATCH /api/meetings/{id} ----------

    def test_bug_011_ac4_patch_meeting_3000_segments_returns_200_and_saves_all(self):
        segs = _segments(3000)
        payload = json.dumps({"segments": segs}, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(payload), 500_000, "ต้องหนักพอที่จะพิสูจน์ headroom เหนือของจริง")
        self.assertLess(len(payload), server.MAX_JSON_TRANSCRIPT, "แต่ต้องยังต่ำกว่าเพดาน 8 MB")

        status, body, _ = self.patch_json(f"/api/meetings/{self.mid}", {"segments": segs})
        self.assertEqual(status, 200)
        saved = filestore.get(self.mid)
        self.assertEqual(len(saved["segments_list"]), 3000)

    def test_bug_011_ac4_patch_meeting_over_8mb_returns_413(self):
        # 2,000 รายการ x ข้อความ 5,000 ตัวอักษร ~= เกิน 8 MB แน่นอน (ไม่ใช่แค่เกินนิดเดียว)
        fat = [{"start": 0, "end": 1, "text": "ข" * 5000} for _ in range(2000)]
        payload = json.dumps({"segments": fat}, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(payload), server.MAX_JSON_TRANSCRIPT)

        status, body, _ = self.patch_json(f"/api/meetings/{self.mid}", {"segments": fat})
        self.assertEqual(status, 413)

    def test_bug_011_ac4_patch_meeting_over_max_segment_count_returns_413(self):
        """แยกเพดาน "จำนวนรายการ" ออกจากเพดาน "ขนาดไบต์" — 60,000 รายการเล็กๆ ยังเล็กกว่า 8 MB

        มาก (แค่ไม่กี่ MB) แต่เกิน MAX_SEGMENTS (50,000) ต้องโดน 413 จากการเช็คจำนวนโดยเฉพาะ
        ไม่ใช่ path ของ _body_json
        """
        tiny = [{"start": 0, "end": 1, "text": "a"} for _ in range(server.MAX_SEGMENTS + 10_000)]
        payload = json.dumps({"segments": tiny}, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(payload), server.MAX_JSON_TRANSCRIPT, "ต้องไม่ใช่เพดานไบต์ที่ทำงาน")

        status, body, _ = self.patch_json(f"/api/meetings/{self.mid}", {"segments": tiny})
        self.assertEqual(status, 413)
        self.assertIn("50,000", str(body))

    # ---------- POST /api/worker/jobs/{id}/result ----------
    # ไม่ต้องมี draft/store จริง — ปลอม jobs.get()/jobs.apply_result() แล้วดูว่า body ที่ถูกส่ง
    # เข้ามาครบไม่ถูกตัดทอน (พิสูจน์ทั้ง "ไม่ 413" และ "apply_result เห็นข้อมูลครบ" ในเทสต์เดียว)

    def _worker_patches(self):
        return [
            mock.patch.object(config, "worker_token", "test-worker-token"),
            mock.patch.object(jobs, "get", lambda job_id: {"id": job_id, "status": "running"}),
        ]

    def test_bug_011_ac3_worker_result_900kb_payload_returns_200(self):
        segs = _segments(3000)
        summary = "# สรุป\n" + ("บรรทัดสรุปทดสอบ\n" * 600)
        result_body = {"worker": "test-worker", "segments": segs, "summary": summary,
                       "translations": {}}
        payload = json.dumps(result_body, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(payload), 500_000)
        self.assertLess(len(payload), server.MAX_JSON_TRANSCRIPT)

        seen: dict = {}

        def fake_apply_result(job_id, body):
            seen["job_id"] = job_id
            seen["segments"] = len(body.get("segments") or [])
            seen["summary_len"] = len(body.get("summary") or "")

        patches = self._worker_patches() + [mock.patch.object(jobs, "apply_result", fake_apply_result)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        job_id = new_mid()
        status, body, _ = self.post_json(
            f"/api/worker/jobs/{job_id}/result", result_body,
            headers={"Authorization": "Bearer test-worker-token"})
        self.assertEqual(status, 200)
        # ยืนยันว่า apply_result เห็นข้อมูลครบ ไม่ใช่แค่ status 200 เฉยๆ (กัน 200 ปลอมจาก error
        # ที่ถูกกลืนไปเงียบๆ ก่อนถึง apply_result)
        self.assertEqual(seen["segments"], 3000)
        self.assertEqual(seen["job_id"], job_id)

    def test_bug_011_ac3_worker_result_over_8mb_returns_413(self):
        huge = {"segments": [{"start": 0, "end": 1, "text": "ค" * 5000} for _ in range(2000)]}
        payload = json.dumps(huge, ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(payload), server.MAX_JSON_TRANSCRIPT)

        patches = self._worker_patches()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        job_id = new_mid()
        status, body, _ = self.post_json(
            f"/api/worker/jobs/{job_id}/result", huge,
            headers={"Authorization": "Bearer test-worker-token"})
        self.assertEqual(status, 413)


class TestBug011CleanSegmentsCaps(unittest.TestCase):
    """AC5 — _clean_segments() ตัด text ที่ MAX_SEGMENT_TEXT และปฏิเสธลิสต์ที่ยาวเกิน MAX_SEGMENTS.

    เรียกฟังก์ชันตรงๆ ไม่ต้องมี HTTP server — เร็วและตรงจุดที่สุดสำหรับ pure function นี้
    """

    def test_bug_011_ac5_text_truncated_at_max_segment_text(self):
        out = server._clean_segments(
            [{"start": 0, "end": 1, "text": "ก" * 9000, "speaker": "S1"}])
        self.assertIsNotNone(out)
        self.assertEqual(len(out[0]["text"]), server.MAX_SEGMENT_TEXT)

    def test_bug_011_ac5_short_text_not_truncated(self):
        out = server._clean_segments([{"start": 0, "end": 1, "text": "สวัสดี"}])
        self.assertEqual(out[0]["text"], "สวัสดี")

    def test_bug_011_ac5_exactly_max_segments_accepted(self):
        out = server._clean_segments(
            [{"start": 0, "end": 1, "text": "a"}] * server.MAX_SEGMENTS)
        self.assertIsNotNone(out)
        self.assertEqual(len(out), server.MAX_SEGMENTS)

    def test_bug_011_ac5_over_max_segments_rejected(self):
        out = server._clean_segments(
            [{"start": 0, "end": 1, "text": "a"}] * (server.MAX_SEGMENTS + 1))
        self.assertIsNone(out)

    def test_bug_011_ac5_non_list_rejected(self):
        self.assertIsNone(server._clean_segments({"start": 0, "end": 1, "text": "a"}))

    def test_bug_011_ac5_non_dict_item_rejected(self):
        self.assertIsNone(server._clean_segments(["not-a-dict"]))


class TestBug011ReviewRoundSiblingFixes(LocalCase):
    """สองจุดที่การแก้ BUG-011 รอบแรก (39cb064) ไม่ครอบ — พบระหว่างเขียนชุดนี้ ตรงกับ

    AUDIT-2026-09-16-bug011-body-caps.md ข้อ 1/3 — backend-dev แก้ในรอบตรวจเดียวกัน (ยัง
    ไม่ commit แยก อยู่ใน working tree ของ meeting_ai/web/server.py) เดิมเป็น
    `@unittest.expectedFailure`; ตอนนี้ยืนยันแล้วว่าแก้จริงจึงเปลี่ยนเป็น assert ปกติ
    """

    def test_bug_011_read_body_to_non_numeric_content_length_returns_400_no_internals(self):
        """`_read_body_to()` (track upload / worker audio / live clip) เดิมอ่าน

        `int(self.headers.get("Content-Length") or 0)` ตรงๆ ไม่ผ่าน `_content_length()` —
        Content-Length ที่ไม่ใช่ตัวเลขเคยหลุดเป็น 500 พร้อมข้อความ exception ของ Python
        ("invalid literal for int() with base 10: 'abc'") ตอนนี้ `_begin_body()` ตรวจ
        Content-Length ของ**ทุก**คำขอที่ `_route()` เลย ก่อนแม้แต่จะรู้ว่าจะ dispatch ไปเส้นไหน
        คำขอที่ Content-Length พังจึงโดน 400 ตั้งแต่ต้นทาง ไม่มีทางไปถึง `_read_body_to()`
        เลยด้วยซ้ำ (ยืนยันด้วยการปลอมสองจุดพร้อมกัน — เฉพาะ `_begin_body()` ไม่พอ ต้องปลอม
        `_read_body_to()` กลับไปอ่าน header ดิบเองด้วย — ถึงจะเห็น 500 กลับมาอีกครั้ง)
        """
        with mock.patch.object(config, "worker_token", "test-worker-token"), \
             mock.patch.object(jobs, "get", lambda job_id: {"id": job_id, "status": "running"}):
            job_id = new_mid()
            status, text, _ = self.raw_request(
                "POST", f"/api/worker/jobs/{job_id}/audio?ext=ogg",
                {"Content-Type": "application/octet-stream", "Content-Length": "abc",
                 "Authorization": "Bearer test-worker-token", "Connection": "close"},
                b"")
        self.assertEqual(status, 400, f"text={text[:200]!r}")
        self.assertNotIn("invalid literal", text)
        self.assertNotIn("Traceback", text)

    def test_bug_011_chunked_transfer_encoding_rejected_with_501(self):
        """`Transfer-Encoding: chunked` (ไม่มี Content-Length ตาม HTTP spec) เคยทำให้

        `_content_length()` คืน 0 = "ไม่มี body" ทั้งที่ payload จริงมากับ chunked body บนสาย
        (เพดานทั้งหมดของ BUG-011 อิง Content-Length เป็นหลัก จึงถูกข้ามได้หมด — ยืนยันจริงตอน
        เขียนเทสต์นี้ครั้งแรก: POST /api/meetings ด้วย chunked body {"title": "x"} -> 201 แต่
        title กลายเป็นค่า default เพราะ body ถูกละเลยทั้งก้อน) ตอนนี้ `_begin_body()` ปฏิเสธ
        Transfer-Encoding ใดๆ ที่ไม่ใช่ identity ด้วย 501 ก่อนอ่าน socket เลย
        """
        title_sent = "หัวข้อจริงที่ควรถูกบันทึก"
        body = json.dumps({"title": title_sent}, ensure_ascii=False).encode("utf-8")
        chunked = b"%x\r\n%s\r\n0\r\n\r\n" % (len(body), body)
        status, text, _ = self.raw_request(
            "POST", "/api/meetings",
            {"Content-Type": "application/json", "Transfer-Encoding": "chunked",
             "Connection": "close"},
            chunked)
        self.assertEqual(status, 501, f"text={text[:300]!r}")


class TestBug011RequestSmugglingSingleResponse(LocalCase):
    """AUDIT ข้อ 1 — บนคอนเนกชัน keep-alive เดียว body ที่ยังไม่ถูกอ่านจนหมดตอนตอบกลับ (ไม่ว่า

    การตอบนั้นจะเป็น success, error, หรือปฏิเสธ framing) ต้องไม่กลายเป็น "คำขอถัดไป" นับจำนวน
    `HTTP/1.1 ` ในสิ่งที่เซิร์ฟเวอร์ตอบกลับมาบนคอนเนกชันเดียวกัน — ทุกกรณีข้างล่างต้องได้ค่า 1
    (ไม่ใช่ 2) มิฉะนั้นคำขอที่แนบมา (`_nested_get`) จะถูกตีความเป็นคำขอที่สองแล้วมี response
    เพิ่มมาโดยไม่มีใครขอ = ผู้ใช้คนถัดไปบนคอนเนกชันเดิม (ถ้ามี proxy คั่นอยู่) จะได้ response นั้นไป

    เพดานเวลา/ค่า `expect` ข้างล่าง (แก้ 2026-09-17 — CI flake บน windows-latest ขณะ CPU ถูก
    แย่งเต็มทุกคอร์): เดิม `raw_send_and_collect()` ใช้ timeout สั้น (1.0-1.5s) เป็นทั้ง socket
    timeout และเพดานรวมในตัวเดียว บน runner ที่ CPU ไม่ว่าง เซิร์ฟเวอร์อาจตอบช้ากว่านั้นแม้จะ
    ทำงานถูกต้องทุกอย่าง ทำให้เทสต์เห็น `blob` ว่างเปล่าและ fail ด้วยเหตุผลที่ไม่เกี่ยวกับโค้ด
    ที่ทดสอบเลย ตอนนี้ทุกเทสต์ในคลาสนี้ระบุ `expect=` ชัดเจน (จำนวน response ที่ถูกต้อง) พร้อม
    เพดานรวมใจกว้าง (`RAW_COLLECT_TIMEOUT`) — ฟังก์ชันจะรอจนกว่าจะเห็นครบ `expect` responses
    (ไม่ใช่รอเวลาคงที่) แล้วรอต่ออีก `RAW_COLLECT_SETTLE` วินาทีเพื่อยืนยันว่าไม่มี response ที่
    เกินมา (ข้อนี้สำคัญที่สุด: ถ้าไม่มี settle เทสต์ "ต้องมีแค่ 1" จะผ่านแบบไม่มีความหมาย เพราะ
    หยุดอ่านทันทีที่เจอ response แรกโดยไม่เคยให้เวลาเซิร์ฟเวอร์ตีความ byte ที่เหลือเป็นคำขอที่สอง)
    """

    RAW_COLLECT_TIMEOUT = 20.0
    RAW_COLLECT_SETTLE = 1.5

    def test_bug_011_smuggling_chunked_with_nested_request_yields_one_response(self):
        req = _raw_bytes(
            "POST", "/api/settings",
            [("Content-Type", "application/json"), ("Transfer-Encoding", "chunked")],
            _nested_get(self.port))
        blob = self.raw_send_and_collect(
            req, timeout=self.RAW_COLLECT_TIMEOUT, expect=1, settle=self.RAW_COLLECT_SETTLE)
        self.assertEqual(blob.count(b"HTTP/1.1 "), 1, blob[:400])

    def test_bug_011_smuggling_content_length_shorter_than_body_yields_one_response(self):
        req = _raw_bytes(
            "POST", "/api/settings",
            [("Content-Type", "application/json"), ("Content-Length", "2")],
            b"{}" + _nested_get(self.port))
        blob = self.raw_send_and_collect(
            req, timeout=self.RAW_COLLECT_TIMEOUT, expect=1, settle=self.RAW_COLLECT_SETTLE)
        self.assertEqual(blob.count(b"HTTP/1.1 "), 1, blob[:400])

    def test_bug_011_smuggling_duplicate_content_length_yields_one_response(self):
        req = _raw_bytes(
            "POST", "/api/settings",
            [("Content-Type", "application/json"), ("Content-Length", "2"),
             ("Content-Length", "100")],
            b"{}" + _nested_get(self.port))
        blob = self.raw_send_and_collect(
            req, timeout=self.RAW_COLLECT_TIMEOUT, expect=1, settle=self.RAW_COLLECT_SETTLE)
        self.assertEqual(blob.count(b"HTTP/1.1 "), 1, blob[:400])

    def test_bug_011_smuggling_404_with_body_yields_one_response(self):
        req = _raw_bytes(
            "POST", "/api/no-such-endpoint",
            [("Content-Type", "application/json"), ("Content-Length", "2")],
            b"{}" + _nested_get(self.port))
        blob = self.raw_send_and_collect(
            req, timeout=self.RAW_COLLECT_TIMEOUT, expect=1, settle=self.RAW_COLLECT_SETTLE)
        self.assertEqual(blob.count(b"HTTP/1.1 "), 1, blob[:400])

    def test_bug_011_keepalive_still_yields_two_responses_for_two_plain_gets(self):
        """เทสต์คุมกฎ — กันคนมา "แก้" ข้อ 1 ด้วยการปิดทุกคอนเนกชันเสมอ (ซึ่งจะทำให้ 4 เทสต์

        ข้างบนผ่านหมดแบบไม่มีความหมาย) คำขอ GET ธรรมดา 2 อันไม่มี body บนคอนเนกชันเดียวยังต้อง
        ได้ 2 responses จริง — keep-alive ใช้งานได้ตามปกติสำหรับคำขอที่ไม่มี body

        `expect=2`: รอจนกว่าจะเห็นครบ 2 responses จริง (ไม่ใช่รอ 1.0 วินาทีคงที่แบบเดิม ซึ่งเคย
        วัดได้ 0 ไบต์ล้วนๆ บน CI ที่ CPU ถูกแย่งเต็ม — ดู docs/tickets/BUG-059-ci-flaky-body-caps-harness.md
        สำหรับตัวเลขก่อน/หลังแก้) แล้วรอ settle ต่อเพื่อยืนยันว่าไม่มี response ที่ 3 โผล่มา
        (เผื่อกรณี server ตีความคำขอที่ 2 ผิดจนแตกเป็นสองคำตอบ)
        """
        one = _raw_bytes("GET", "/api/config", [("Host", f"127.0.0.1:{self.port}")])
        blob = self.raw_send_and_collect(
            one + one, timeout=self.RAW_COLLECT_TIMEOUT, expect=2, settle=self.RAW_COLLECT_SETTLE)
        self.assertEqual(blob.count(b"HTTP/1.1 "), 2, blob[:600])


class TestBug011ConnectionCloseOn413(LocalCase):
    """AUDIT ข้อ 6 — `self.close_connection = True` ไม่ส่ง header อะไรออกสายเอง ต้องมี

    `Connection: close` จริงในคำตอบ 413 ไม่งั้น client/proxy เก็บคอนเนกชันไว้ใช้ต่อแล้วคำขอถัดไป
    ไปตายที่ socket ที่กำลังจะถูกปิด อีกส่วนที่ต้องพิสูจน์คู่กัน: การปิดคอนเนกชันนี้ต้องเป็นการปิด
    ที่ตั้งใจ ไม่ใช่เซิร์ฟเวอร์พังทั้งตัว — เปิดคอนเนกชันใหม่ทันทีหลังจากนั้นต้องยังใช้งานได้ปกติ

    เพดานเวลาข้างล่าง (แก้ 2026-09-17 — CI flake): `raw_send_and_collect(req)` เดิมใช้
    timeout=1.5s เป็นเพดานรวม บน CI ที่ CPU ถูกแย่งเต็มทุกคอร์ เธรด `serve_forever` อาจยังไม่ได้
    CPU มาตอบภายในเวลานั้น ทำให้ได้ `blob` ว่างเปล่า (`assertTrue(blob.startswith(...))` ตายที่
    บรรทัดนั้นเลย ไม่ใช่ที่ `self.get(...)` ด้านล่าง) `_HttpCaseMixin._wait_until_ready()` (เพิ่ม
    ใน `_start_server()`) ดูดซับต้นทุน cold-start ไว้ใน `setUp()` แล้วก่อนถึงบรรทัดนี้ ส่วน
    `timeout=` ที่ยกให้ใจกว้างขึ้นตรงนี้ป้องกันกรณีเซิร์ฟเวอร์ "ตอบช้าแต่ยังตอบ" ระหว่างคำขอจริง
    ของเทสต์เอง (คนละจุดกับ cold start ตอน setUp) — วัดจริงแล้วว่า **ไม่ใช่บั๊กจริงของ
    server.py** (ดู docs/tickets/BUG-059-ci-flaky-body-caps-harness.md หัวข้อ 3
    "คำตอบของคำถาม 'ตอบช้า หรือ ไม่ตอบเลย'")
    """

    def test_bug_011_413_has_connection_close_header_and_server_stays_healthy(self):
        big = b"x" * (server.MAX_JSON_BODY + 1024)
        req = _raw_bytes(
            "POST", "/api/meetings",
            [("Content-Type", "application/json"), ("Content-Length", str(len(big)))],
            big)
        blob = self.raw_send_and_collect(req, timeout=20.0, expect=1, settle=0.5)
        self.assertTrue(blob.startswith(b"HTTP/1.1 413"), blob[:200])
        self.assertIn(b"Connection: close", blob)

        status, body, _ = self.get("/api/config")
        self.assertEqual(status, 200)


class TestBug011SegmentsRejectNonFiniteNumbers(LocalCase):
    """AUDIT ข้อ 4 — `NaN`/`Infinity` เป็น start/end เคยเขียนลงไฟล์จริงแล้วทำให้เปิดการประชุม

    นั้นไม่ขึ้นอีกเลย (`json.dumps` เขียน `NaN` ได้ แต่ export/`GET` ที่คำนวณต่อพังถาวร) —
    `_clean_segments()` ตอนนี้เช็ค `math.isfinite()` หลัง `float()` ปฏิเสธก่อนจะไปถึง
    `store.set_segments()` เลย ทดสอบทั้งรูปแบบ string ("nan"/"1e400" ที่ overflow เป็น inf)
    และ literal JSON ตรงๆ (`NaN`/`Infinity` ที่ json.loads ของ Python รับเป็นค่าพิเศษได้)
    """

    def setUp(self) -> None:
        super().setUp()
        self.mid = filestore.new_id()
        self.good_segments = [{"start": 0, "end": 1, "text": "เดิม"}]
        filestore.create(mid=self.mid, title="เทสต์ NaN (ลบได้)", audio_name="",
                         source="upload", language="th", duration=0.0,
                         segments=self.good_segments, summary="")

    def _assert_rejected_and_store_untouched(self) -> None:
        status2, body2, _ = self.get(f"/api/meetings/{self.mid}")
        self.assertEqual(status2, 200)
        self.assertEqual(body2["segments_list"], self.good_segments)

        raw_on_disk = filestore._detail_path(self.mid).read_text(encoding="utf-8")
        self.assertNotIn("NaN", raw_on_disk)
        self.assertNotIn("Infinity", raw_on_disk)

    def test_bug_011_segments_nan_string_rejected(self):
        status, body, _ = self.patch_json(
            f"/api/meetings/{self.mid}",
            {"segments": [{"start": "nan", "end": 1, "text": "pwn"}]})
        self.assertEqual(status, 400, body)
        self._assert_rejected_and_store_untouched()

    def test_bug_011_segments_1e400_overflow_to_infinity_rejected(self):
        status, body, _ = self.patch_json(
            f"/api/meetings/{self.mid}",
            {"segments": [{"start": 0, "end": "1e400", "text": "pwn"}]})
        self.assertEqual(status, 400, body)
        self._assert_rejected_and_store_untouched()

    def test_bug_011_segments_json_literal_nan_rejected(self):
        # dict ปกติของ Python เก็บ NaN ผ่าน patch_json ได้อยู่แล้ว (json.dumps เขียนออกเป็น
        # NaN โดยปริยาย) แต่จุดที่ต้องพิสูจน์คือ literal ที่ไม่ได้ครอบด้วย " " — ยิง raw body เอง
        body = b'{"segments": [{"start": NaN, "end": 1, "text": "pwn"}]}'
        status, text, _ = self.raw_request(
            "PATCH", f"/api/meetings/{self.mid}",
            {"Content-Type": "application/json", "Content-Length": str(len(body))},
            body)
        self.assertEqual(status, 400, text[:200])
        self._assert_rejected_and_store_untouched()

    def test_bug_011_segments_json_literal_infinity_rejected(self):
        body = b'{"segments": [{"start": 0, "end": Infinity, "text": "pwn"}]}'
        status, text, _ = self.raw_request(
            "PATCH", f"/api/meetings/{self.mid}",
            {"Content-Type": "application/json", "Content-Length": str(len(body))},
            body)
        self.assertEqual(status, 400, text[:200])
        self._assert_rejected_and_store_untouched()


class TestBug011StrictContentLength(LocalCase):
    """AUDIT ข้อ 5 — `int()` ของ Python ยอมรับ `1_0`, `+10`, เว้นวรรค ซึ่ง RFC 9110 ห้าม (ต้องเป็น

    `1*DIGIT` ล้วน) proxy ข้างหน้าที่ตีความค่าพวกนี้ต่างจากเราคือวัตถุดิบของ request smuggling
    (คู่กับ `TestBug011RequestSmugglingSingleResponse`) — ทุกรูปแบบข้างล่างต้องได้ 400
    """

    def _assert_rejected(self, content_length: str) -> None:
        status, text, _ = self.raw_request(
            "POST", "/api/settings",
            {"Content-Type": "application/json", "Content-Length": content_length,
             "Connection": "close"},
            b'{"a":"bc"}')
        self.assertEqual(status, 400, f"Content-Length={content_length!r} text={text[:200]!r}")

    def test_bug_011_content_length_underscore_digit_grouping_rejected(self):
        self._assert_rejected("1_0")

    def test_bug_011_content_length_leading_plus_rejected(self):
        self._assert_rejected("+10")

    def test_bug_011_content_length_surrounding_whitespace_rejected(self):
        self._assert_rejected(" 10 ")

    def test_bug_011_content_length_hex_prefix_rejected(self):
        self._assert_rejected("0x10")

    def test_bug_011_content_length_comma_separated_list_rejected(self):
        self._assert_rejected("10, 10")


if __name__ == "__main__":
    unittest.main()
