"""BUG-048 — jobs.apply_result() เชื่อฟิลด์ที่ worker (ผู้ถือ WORKER_TOKEN) ส่งกลับมาโดยไม่ตรวจ.

ก่อนแก้: 1) งานแปล อ่าน `lang` จาก body ของ worker ตรงๆ (ใครถือ WORKER_TOKEN ยัด key อะไรก็ได้
ลง translations ของการประชุมคนอื่น) 2) สาขา process/bot ส่ง segments เข้า store.create() ตรงๆ
ไม่ผ่านการตรวจเลย — NaN/Infinity ที่หลุดลงไปทำให้ GET /api/meetings/<id> และ export ทุกฟอร์แมต
ตอบ 500 ถาวร (เจ้าของแก้เองไม่ได้เพราะเปิดการประชุมไม่ขึ้น)

รายละเอียด: docs/tickets/BUG-048-worker-result-trusted-fields.md
สิ่งที่พิสูจน์ในไฟล์นี้ตรงกับ Acceptance criteria ของตั๋ว 1-5 (ดูหัวข้อในตั๋ว)

หมายเหตุการพิสูจน์ว่าเทสต์ไม่ vacuous (ทำระหว่างพัฒนา ไม่ใช่โค้ดถาวรในนี้): แพตช์
sanitize.segments()/duration()/speakers()/language() ให้เป็น pass-through ชั่วคราวแล้วรัน
ไฟล์นี้ซ้ำ — เทสต์ hostile-segments/duration/language/speakers ต้องล้มทุกตัว ส่วนแพตช์ jobs.py
ให้กลับไปอ่าน result["lang"]/result["text"] ตรงๆ ต้องทำให้เทสต์ lang-ignored/missing-text ล้ม
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest import mock

from _harness import CloudCase, LocalCase, filestore, jobs, new_mid
from meeting_ai.config import config as app_config
from meeting_ai.web import sanitize

WORKER_TOKEN = "wk-test-token-048"


class WorkerAuthMixin:
    """เปิด worker API ให้ทดสอบได้ (ต้องตั้ง WORKER_TOKEN ไม่งั้น _worker_authed() ปฏิเสธเสมอ)."""

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(app_config, "worker_token", WORKER_TOKEN)
        patch.start()
        self.addCleanup(patch.stop)

    def worker_result(self, job_id: str, body: dict):
        return self.post_worker_json(f"/api/worker/jobs/{job_id}/result", body, WORKER_TOKEN)


# ---------- โหมดไฟล์ (LocalCase) — เส้น process/bot ผ่าน store จริง + exports จริง ----------

class TestApplyResultProcessLocal(WorkerAuthMixin, LocalCase):
    def _seed_process_job(self, title: str = "ทดสอบ BUG-048") -> str:
        mid = jobs.create_draft(title=title, language=None, template="general",
                                want_diarize=False, num_speakers=0, source="upload")
        jobs.register_track(mid, "mix", Path("mix.wav"))
        job = jobs.start(mid)
        self.assertIsNotNone(job, "seed ผิด: start() ควรสร้างงาน process ได้")
        self.assertEqual(job["kind"], "process")
        return mid

    def test_hostile_segments_dropped_meeting_still_readable_and_exportable(self):
        """AC3/AC4 — segment เสียถูกทิ้ง แต่ GET และ export ทุกฟอร์แมตยังใช้ได้ปกติ."""
        mid = self._seed_process_job()
        long_text = "x" * (sanitize.MAX_SEGMENT_TEXT + 100)
        body = {
            "segments": [
                {"start": 0, "end": 1, "text": "ปกติ", "speaker": "A"},
                {"start": float("nan"), "end": 2, "text": "bad-nan"},
                {"start": 1, "end": float("inf"), "text": "bad-inf"},
                {"start": "abc", "end": 2, "text": "bad-str"},
                "not-a-dict",
                {"start": 2, "end": 3, "text": long_text},
                {"start": 3, "end": 4, "text": "ok2", "speaker": "B\r\nC"},
            ],
            "language": "th", "duration": 5.0, "speakers": ["A", "B"],
            "summary": "สรุปทดสอบ",
        }
        status, resp, _ = self.worker_result(mid, body)
        self.assertEqual(status, 200, resp)

        status, meeting, _ = self.get(f"/api/meetings/{mid}")
        self.assertEqual(status, 200)
        segs = meeting["segments_list"]
        self.assertEqual(len(segs), 3)  # 7 เข้ามา - 4 เสีย = 3 รอด
        texts = {s["text"] for s in segs if len(s["text"]) < 100}
        self.assertEqual(texts, {"ปกติ", "ok2"})
        kept_long = next(s for s in segs if s["text"].startswith("x"))
        self.assertEqual(len(kept_long["text"]), sanitize.MAX_SEGMENT_TEXT)  # ตัดไม่ทิ้ง
        ok2 = next(s for s in segs if s["text"] == "ok2")
        self.assertEqual(ok2.get("speaker"), "B C")  # \r\n ถูกล้าง ไม่ใช่ทิ้งทั้ง segment

        for fmt in ("md", "txt", "srt", "vtt", "docx"):
            status, _, _ = self.get(f"/api/meetings/{mid}/export.{fmt}")
            self.assertEqual(status, 200, f"export.{fmt} ควรได้ 200")

        status, job, _ = self.get(f"/api/jobs/{mid}")
        self.assertEqual(status, 200)
        self.assertIsNotNone(job["warning"])
        self.assertIn("4", job["warning"])  # จำนวนที่ทิ้งบอกในข้อความ

    def test_normal_process_result_lands_complete(self):
        """AC5 — ผล process ปกติ (ไม่มีอะไรเสีย) ต้องลงคลังครบทุกฟิลด์ ห้ามพังเส้นทางปกติ."""
        mid = self._seed_process_job()
        segments = [
            {"start": 0, "end": 1.5, "text": "สวัสดีครับ", "speaker": "A"},
            {"start": 1.5, "end": 3.0, "text": "สวัสดีค่ะ", "speaker": "B"},
        ]
        body = {
            "segments": segments, "language": "th", "duration": 3.0,
            "speakers": ["A", "B"], "summary": "# สรุป\nประชุมเสร็จแล้ว",
            "playback": str(Path("recordings") / f"{mid}.ogg"),
        }
        status, resp, _ = self.worker_result(mid, body)
        self.assertEqual(status, 200, resp)

        status, meeting, _ = self.get(f"/api/meetings/{mid}")
        self.assertEqual(status, 200)
        self.assertEqual(len(meeting["segments_list"]), 2)
        self.assertEqual([s["text"] for s in meeting["segments_list"]],
                         ["สวัสดีครับ", "สวัสดีค่ะ"])
        self.assertEqual(meeting["speakers"], ["A", "B"])
        self.assertEqual(meeting["language"], "th")
        self.assertEqual(meeting["duration"], 3.0)
        self.assertIn("ประชุมเสร็จแล้ว", meeting["summary"])
        self.assertEqual(meeting["audio"], f"{mid}.ogg")

        status, job, _ = self.get(f"/api/jobs/{mid}")
        self.assertEqual(status, 200)
        self.assertEqual(job["status"], "done")
        self.assertIsNone(job["warning"])  # ไม่มีอะไรถูกทิ้ง = ไม่มี warning

        for fmt in ("md", "txt", "srt", "vtt", "docx"):
            status, _, _ = self.get(f"/api/meetings/{mid}/export.{fmt}")
            self.assertEqual(status, 200, f"export.{fmt} ควรได้ 200")

    def test_duration_infinity_clamped_to_zero(self):
        mid = self._seed_process_job()
        status, _, _ = self.worker_result(
            mid, {"segments": [{"start": 0, "end": 1, "text": "x"}], "duration": float("inf")})
        self.assertEqual(status, 200)
        status, meeting, _ = self.get(f"/api/meetings/{mid}")
        self.assertEqual(meeting["duration"], 0.0)

    def test_language_over_cap_clamped(self):
        mid = self._seed_process_job()
        status, _, _ = self.worker_result(mid, {"segments": [], "language": "x" * 100})
        self.assertEqual(status, 200)
        status, meeting, _ = self.get(f"/api/meetings/{mid}")
        self.assertLessEqual(len(meeting["language"]), sanitize.MAX_LANGUAGE)

    def test_speakers_non_string_items_dropped(self):
        mid = self._seed_process_job()
        status, _, _ = self.worker_result(
            mid, {"segments": [], "speakers": ["ok", 123, {"a": 1}, None]})
        self.assertEqual(status, 200)
        status, meeting, _ = self.get(f"/api/meetings/{mid}")
        self.assertEqual(meeting["speakers"], ["ok"])


class TestApplyResultTranslateLocal(WorkerAuthMixin, LocalCase):
    def _seed_meeting(self) -> str:
        mid = filestore.new_id()
        filestore.create(mid=mid, title="ต้นฉบับ", audio_name="a.wav", source="upload",
                         language="th", duration=10, segments=[{"start": 0, "end": 1, "text": "hi"}],
                         summary="")
        return mid

    def test_worker_supplied_lang_ignored_uses_spec_lang(self):
        """AC1 — worker ส่ง lang="pwned" มา ต้องไม่มี key นี้ใน translations เลย."""
        mid = self._seed_meeting()
        job = jobs.submit_translate(mid, "ต้นฉบับ", "en")
        status, resp, _ = self.worker_result(job["id"], {"lang": "pwned", "text": "Hello translated"})
        self.assertEqual(status, 200, resp)

        meeting = filestore.get(mid)
        self.assertEqual(meeting["translations"], {"en": "Hello translated"})
        self.assertNotIn("pwned", meeting["translations"])

    def test_missing_text_fails_job_with_readable_thai_message(self):
        """AC2 — text หาย ต้องได้ 400 + ข้อความอ่านรู้เรื่อง ไม่ใช่ KeyError: 'text' ดิบๆ."""
        mid = self._seed_meeting()
        job = jobs.submit_translate(mid, "ต้นฉบับ", "en")
        status, resp, _ = self.worker_result(job["id"], {"lang": "pwned"})
        self.assertEqual(status, 400)
        self.assertTrue(resp.get("error"))
        self.assertNotIn("'text'", resp["error"])
        self.assertNotEqual(resp["error"], "text")

        status, job_after, _ = self.get(f"/api/jobs/{job['id']}")
        self.assertEqual(job_after["status"], "error")

    def test_missing_spec_lang_fails_readable_not_keyerror(self):
        mid = self._seed_meeting()
        job_id = f"{mid}.tr.broken"
        with jobs._cv:
            jobs._jobs[job_id] = {
                "id": job_id, "status": "queued", "step": "รอคิว", "progress": 0.0,
                "title": "ต้นฉบับ", "kind": "translate", "meeting_id": None,
                "error": None, "warning": None, "created": "2026-01-01T00:00:00",
                "_meeting": mid,
            }
        self.addCleanup(lambda: jobs._jobs.pop(job_id, None))

        status, resp, _ = self.worker_result(job_id, {"text": "hello"})
        self.assertEqual(status, 400)
        self.assertTrue(resp.get("error"))
        self.assertNotIn("'lang'", resp["error"])


# ---------- โหมด cloud (FakeStore) — ยืนยันช่องโหว่เฉพาะ cloud: job row ไม่มี _lang top-level ----------

class TestApplyResultTranslateCloud(WorkerAuthMixin, CloudCase):
    def test_cloud_job_row_has_no_top_level_lang_only_spec(self):
        """สมมติฐานสำคัญของ fix: pgstore.job_get() ไม่มีคีย์ _lang เลย มีแต่ _spec['lang']
        (ต่างจากโหมดไฟล์ที่ _jobs[id]['_lang'] มีจริง) — ถ้า apply_result() พึ่ง job['_lang']
        อย่างเดียวโดยไม่ fallback ไป _spec, งานแปลฝั่ง cloud จะหา lang ไม่เจอเสมอ"""
        job = jobs.get(self.M1_tr_en)
        self.assertNotIn("_lang", job)
        self.assertEqual(job["_spec"]["lang"], "en")

    def test_worker_supplied_lang_ignored_uses_spec_lang(self):
        status, resp, _ = self.worker_result(self.M1_tr_en, {"lang": "pwned", "text": "Hello"})
        self.assertEqual(status, 200, resp)
        self.assertEqual(self.store.meetings[self.M1]["translations"], {"en": "Hello"})

    def test_missing_text_fails_with_readable_message(self):
        status, resp, _ = self.worker_result(self.M1_tr_en, {"lang": "pwned"})
        self.assertEqual(status, 400)
        self.assertTrue(resp.get("error"))
        self.assertNotIn("'text'", resp["error"])
        self.assertEqual(self.store.jobs[self.M1_tr_en]["status"], "error")

    def test_missing_spec_lang_fails_readable(self):
        job_id = f"{self.M1}.tr.broken"
        self.store.jobs[job_id] = {
            "id": job_id, "meeting_id": self.M1, "kind": "translate", "status": "queued",
            "step": "รอคิว", "progress": 0.0, "title": "t",
            "error": None, "warning": None, "created": "2026-01-01T00:00:00", "worker": None,
            "spec": {"id": job_id, "title": "t", "owner_id": None, "meeting": self.M1},
        }
        status, resp, _ = self.worker_result(job_id, {"text": "hi"})
        self.assertEqual(status, 400)
        self.assertTrue(resp.get("error"))
        self.assertNotIn("'lang'", resp["error"])


class TestApplyResultProcessCloud(WorkerAuthMixin, CloudCase):
    def test_hostile_segments_dropped_meeting_still_created_and_readable(self):
        mid = new_mid()
        self.store.add_draft(mid, owner_id=self.uid_a, tracks={"mix": "s3://bucket/mix.wav"},
                             title="ทดสอบ cloud")
        body = {
            "segments": [
                {"start": 0, "end": 1, "text": "ok"},
                {"start": float("nan"), "end": 1, "text": "bad"},
            ],
            "duration": float("inf"), "language": "th", "speakers": ["A", 1],
            "summary": "สรุป",
        }
        status, resp, _ = self.worker_result(mid, body)
        self.assertEqual(status, 200, resp)

        meeting = self.store.meetings[mid]
        self.assertEqual(len(meeting["segments_list"]), 1)
        self.assertEqual(meeting["segments_list"][0]["text"], "ok")
        self.assertEqual(meeting["duration"], 0.0)
        self.assertEqual(meeting["speakers"], ["A"])
        # ค่าที่บันทึกต้อง JSON round-trip ได้ปกติ — ก่อนแก้ NaN/Infinity หลุดลงไปพัง GET/export ถาวร
        json.dumps(meeting)
        self.assertEqual(self.store.jobs[mid]["status"], "done")
        self.assertIn("1", self.store.jobs[mid]["warning"])

    def test_normal_process_result_lands_complete_cloud(self):
        mid = new_mid()
        self.store.add_draft(mid, owner_id=self.uid_a, tracks={"mix": "s3://bucket/mix.wav"},
                             title="ทดสอบ cloud ปกติ")
        segments = [{"start": 0, "end": 1, "text": "สวัสดี", "speaker": "A"}]
        status, resp, _ = self.worker_result(mid, {
            "segments": segments, "language": "th", "duration": 1.0,
            "speakers": ["A"], "summary": "สรุปครบ",
        })
        self.assertEqual(status, 200, resp)
        meeting = self.store.meetings[mid]
        self.assertEqual(len(meeting["segments_list"]), 1)
        self.assertEqual(meeting["duration"], 1.0)
        self.assertEqual(meeting["speakers"], ["A"])
        self.assertEqual(meeting["summary"], "สรุปครบ")
        self.assertIsNone(self.store.jobs[mid]["warning"])


# ---------- Postgres จริง (ไม่บังคับ) — ยืนยันว่าไม่มี NaN หลุดลง jsonb จริง ----------

@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestApplyResultAgainstRealDb(unittest.TestCase):
    """ต่อ Postgres จริง (throwaway) — ยืนยัน pgstore.create()/set_translation() เก็บค่าที่
    sanitize.py คัดมาแล้วได้จริง ไม่ใช่แค่พฤติกรรมที่จำลองด้วย FakeStore."""

    def setUp(self) -> None:
        from meeting_ai.web import db as pgdb
        from meeting_ai.web import pgstore

        self.pgdb = pgdb
        self.pgstore = pgstore
        db_url = os.environ["MAI_TEST_DATABASE_URL"]
        env_patch = mock.patch.dict(os.environ, {"DATABASE_URL": db_url})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        pgdb.init()
        self.addCleanup(pgdb.close)

        store_patches = [
            mock.patch.object(jobs, "store", pgstore),
            mock.patch.object(jobs, "cloud", True),
        ]
        for p in store_patches:
            p.start()
            self.addCleanup(p.stop)

        self.mid = new_mid()
        self.addCleanup(self._cleanup_rows)

    def _cleanup_rows(self) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.jobs where id = %s or id like %s",
                        (self.mid, f"{self.mid}.tr.%"))
            conn.execute("delete from meeting_ai.meetings where id = %s", (self.mid,))

    def test_translate_lang_from_spec_not_worker_body(self):
        self.pgstore.create(mid=self.mid, title="ทดสอบจริง", audio_name="a.wav", source="upload",
                            language="th", duration=5, segments=[{"start": 0, "end": 1, "text": "hi"}],
                            summary="", owner_id=None)
        job_id = f"{self.mid}.tr.en"
        self.pgstore.job_upsert(job_id, "translate", "ทดสอบจริง",
                                {"id": job_id, "meeting": self.mid, "lang": "en", "owner_id": None},
                                status="queued", meeting_id=self.mid)

        jobs.apply_result(job_id, {"lang": "pwned", "text": "Real DB translation"})

        meeting = self.pgstore.get(self.mid)
        self.assertEqual(meeting["translations"], {"en": "Real DB translation"})
        self.assertNotIn("pwned", meeting["translations"])

    def test_hostile_segments_do_not_break_real_row(self):
        self.pgstore.job_upsert(self.mid, "process", "ทดสอบจริง",
                                {"id": self.mid, "title": "ทดสอบจริง", "owner_id": None, "tracks": {}},
                                status="queued")

        jobs.apply_result(self.mid, {
            "segments": [{"start": 0, "end": 1, "text": "ok"},
                        {"start": float("nan"), "end": 1, "text": "bad"},
                        {"start": 1, "end": float("inf"), "text": "bad2"}],
            "duration": float("inf"), "language": "th", "summary": "สรุปจริง",
        })

        meeting = self.pgstore.get(self.mid)
        self.assertEqual(len(meeting["segments_list"]), 1)
        self.assertEqual(meeting["segments_list"][0]["text"], "ok")
        self.assertEqual(meeting["duration"], 0.0)
        # ก่อนแก้: NaN/Infinity ที่หลุดลง jsonb ทำให้อ่านกลับมาแล้ว dumps ใหม่ไม่ได้ (คือ 500 ที่ตั๋วบอก)
        json.dumps(meeting)


if __name__ == "__main__":
    unittest.main()
