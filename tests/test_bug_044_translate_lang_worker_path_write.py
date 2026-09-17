"""BUG-044 — arbitrary file write ผ่าน translate `lang` + worker audio endpoint (chain #13+#14).

Chain สองจุด (ทั้งคู่ต้องแก้ ดู docs/tickets/BUG-044-translate-lang-worker-path-write.md):
  1. `_translate()` เดิมเช็คแค่ `lang` ไม่ว่าง แล้วส่งต่อไปประกอบเป็น job id
     `f"{meeting_id}.tr.{lang}"` ตรงๆ (jobs.py:submit_translate) — `lang` ไม่ผ่าน valid_id เลย
  2. worker `audio` action (`_worker_api`) เดิมไม่เรียก `store.valid_id()`/`_safe_job_id()`
     ก่อนประกอบ `dest = store.WEB_DIR / f"{job_id}.{ext}"` — `urllib.parse.unquote(job_id)`
     เกิดขึ้น *หลัง* `_api()` ตัด path ด้วย "/" ไปแล้ว ทำให้ `%2F..%2F` รอดมาเป็น "/../" จริง

Fix ที่ทดสอบ (ยังไม่ commit ตอนเขียนไฟล์นี้ — server.py):
  - `_translate()`: allow-list `lang` กับ `summarizer.LANGUAGE_NAMES` -> 400 ถ้าไม่ผ่าน
  - `_safe_job_id()` (module level, server.py) + guard ต้นๆ ของ `_worker_api()` หลังแยก
    `job_id, action = rest[1], rest[2]` และ unquote ครั้งเดียว — อยู่ *ก่อน*
    `jobs.get(job_id) is None` (404) เจตนา: งาน "เก่า" ที่หลุดเข้าคิวมาก่อนแพตช์ (หรือจาก
    root cause อื่นในอนาคต) ต้องโดนกันเหมือนกัน ไม่ใช่กันเฉพาะ id ที่ยังไม่เคยมีอยู่จริง

ครอบทั้ง chain: _safe_job_id() แบบ pure-function ก่อน, ตามด้วย HTTP ทั้งสองโหมด (Local/Cloud):
translate allow-list (reject + accept ครบ 5 ภาษา), worker guard (หลายรูปแบบ encode: %2F จริง,
%252F double-encode, %00, %5C ผสม), งานเก่าที่ปนเปื้อนอยู่แล้วในคิว (พิสูจน์ guard มาก่อนเช็ค 404),
และ happy path (worker audio/tracks + lang ปกติ) ต้องยังทำงาน 200/202 เหมือนเดิม
"""

from __future__ import annotations

import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from _harness import CloudCase, LocalCase, filestore, jobs, new_mid, server

WORKER_TOKEN = "bug044-test-worker-token"
LANGS = ("th", "en", "ja", "zh", "ko")
BAD_JOB_ID_MSG = "job id ไม่ถูกต้อง"


def _worker_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {WORKER_TOKEN}"}


# =====================================================================
# 1) server._safe_job_id — ฟังก์ชันล้วน ไม่ต้องยิง HTTP
# =====================================================================

class TestSafeJobIdPure(unittest.TestCase):
    def test_meeting_id_alone_is_safe(self):
        self.assertTrue(server._safe_job_id(new_mid()))

    def test_translate_id_with_allowlisted_lang_is_safe(self):
        for lang in LANGS:
            with self.subTest(lang=lang):
                self.assertTrue(server._safe_job_id(f"{new_mid()}.tr.{lang}"))

    def test_legit_suffix_characters_allowed(self):
        # จุด/ขีด/underscore/ตัวเลขในส่วนท้าย id ต้องยังผ่าน (ไม่ใช่แค่ ".tr.<lang>")
        self.assertTrue(server._safe_job_id(f"{new_mid()}.summary-v2_final.99"))

    def test_empty_and_garbage_base_are_unsafe(self):
        self.assertFalse(server._safe_job_id(""))
        self.assertFalse(server._safe_job_id("not-a-meeting-id"))
        self.assertFalse(server._safe_job_id("../../../etc/passwd"))

    def test_single_decoded_forward_slash_in_suffix_is_unsafe(self):
        # สภาพหลัง unquote(%2F) ครั้งเดียว — คือรูปแบบที่ถูกใช้โจมตีจริงตามตั๋ว
        mid = new_mid()
        self.assertFalse(server._safe_job_id(f"{mid}.tr./../../../pwned"))

    def test_single_decoded_backslash_in_suffix_is_unsafe(self):
        # สภาพหลัง unquote(%5C) ครั้งเดียว — \\ ไม่ใช่ตัวคั่นบน POSIX แต่ก็ต้องกันไว้ (docstring
        # ของ _safe_job_id พูดถึงเคสนี้ตรงๆ)
        mid = new_mid()
        self.assertFalse(server._safe_job_id(f"{mid}.tr.\\..\\..\\pwned"))

    def test_single_decoded_nul_in_suffix_is_unsafe(self):
        # สภาพหลัง unquote(%00) ครั้งเดียว — NUL ผ่าน Path() ได้แต่ต้องไม่ผ่านตัวนี้
        mid = new_mid()
        self.assertFalse(server._safe_job_id(f"{mid}.tr.\x00pwned"))

    def test_leftover_percent_from_double_encoding_is_unsafe(self):
        # %252F ถูก unquote (ครั้งเดียวที่ server ทำ) กลายเป็น "%2F" ตัวอักษรจริง (ยังไม่ใช่ "/")
        # ต้องติด regex เพราะมี '%' หลงเหลืออยู่ — ไม่ใช่ตัวอักษรที่อนุญาต
        mid = new_mid()
        self.assertFalse(server._safe_job_id(f"{mid}.tr.%2F..%2Fpwned"))


# =====================================================================
# 2) HTTP chain: translate allow-list + worker audio guard, Local/Cloud
# =====================================================================

class _TranslateWorkerChainMixin:
    """เมธอด test_* ทั้งหมดเขียนผ่าน hook ที่ subclass (Cloud/Local) ต้อง implement:

    self.web_dir              -> Path ของ WEB_DIR ที่ทดสอบใช้จริง
    self._new_meeting()       -> สร้างการประชุมจริง (ไม่ใช่ draft) คืน mid
    self._translate(mid, lang) -> เรียก POST .../translate คืน (status, body, headers)
    self._insert_job(job_id, meeting_id, lang) -> ยัด job "เก่า" ตรงเข้า store โดยไม่ผ่าน
                                  translate (จำลองข้อมูลก่อนแพตช์/ก่อนแก้จุดที่ 1)
    self._job_exists(job_id)  -> bool
    self._job_ids_with_prefix(prefix) -> list[str]
    self._make_draft_with_track_and_queue() -> job_id ของงานที่คิวจริงพร้อมแทร็ก mixed
    """

    def setUp(self) -> None:
        super().setUp()
        p = mock.patch.object(server.config, "worker_token", WORKER_TOKEN)
        p.start()
        self.addCleanup(p.stop)

    # ---------- translate: allow-list ----------

    def test_translate_rejects_path_traversal_lang_and_creates_no_job(self):
        mid = self._new_meeting()
        status, body, _ = self._translate(mid, "/../../../pwned")
        self.assertEqual(status, 400)
        self.assertEqual(self._job_ids_with_prefix(f"{mid}.tr."), [])

    def test_translate_rejects_unsupported_lang_code(self):
        mid = self._new_meeting()
        status, body, _ = self._translate(mid, "fr")
        self.assertEqual(status, 400)
        self.assertEqual(self._job_ids_with_prefix(f"{mid}.tr."), [])

    def test_translate_allowlist_happy_path_all_langs(self):
        # ครบวงจร: submit -> worker "claim" งานนี้ทำจริง -> POST result กลับมา -> ต้องเห็น
        # translations.<lang> ถูกบันทึกในการประชุม (acceptance criteria ข้อ 2 ของตั๋ว)
        mid = self._new_meeting()
        for lang in LANGS:
            with self.subTest(lang=lang):
                status, body, _ = self._translate(mid, lang)
                self.assertEqual(status, 202, body)
                job_id = f"{mid}.tr.{lang}"
                self.assertTrue(self._job_exists(job_id))
                self.addCleanup(self._cleanup_job, job_id)

                text = f"translated-text-{lang}"
                status, body, _ = self.post_json(
                    f"/api/worker/jobs/{job_id}/result", {"lang": lang, "text": text},
                    extra_headers=_worker_header())
                self.assertEqual(status, 200, body)
                self.assertEqual(self._translation_saved(mid, lang), text)

    def _cleanup_job(self, job_id: str) -> None:
        """no-op ในโหมด cloud (FakeStore ถูกทิ้งทั้งอินสแตนซ์ตอนจบเทสต์อยู่แล้ว)."""

    # ---------- worker audio: ปฏิเสธ job id ไม่ปลอดภัยทุกรูปแบบ encode ----------

    def test_worker_audio_blocks_every_traversal_encoding_for_unknown_job(self):
        mid = new_mid()
        payloads = {
            # ตรงกับ PoC ในตั๋ว: %2F เดี่ยว unquote ครั้งเดียวกลายเป็น "/" จริง
            "single-encoded slash (real exploit)": f"{mid}.tr.%2F..%2F..%2F..%2Fpwned",
            # %252F unquote ครั้งเดียว = "%2F" ตัวอักษรจริง — ยังไม่ทะลุเป็น "/"
            "double-encoded slash": f"{mid}.tr.%252F..%252F..%252Fpwned",
            "NUL byte": f"{mid}.tr.%00pwned",
            "mixed backslash": f"{mid}.tr.%5C..%5C..%5Cpwned",
        }
        for label, raw_id in payloads.items():
            with self.subTest(label=label):
                path = f"/api/worker/jobs/{raw_id}/audio?ext=wav"
                status, body, _ = self.post_bytes(path, b"malicious-bytes",
                                                  extra_headers=_worker_header())
                self.assertEqual(status, 400, body)
                self.assertEqual(body.get("error"), BAD_JOB_ID_MSG)
        # ไม่มีไฟล์ชื่อ pwned หลุดออกไปนอก WEB_DIR จากความพยายามทั้งหมดข้างบน
        self.assertEqual(list(self.web_dir.parent.glob("*pwned*")), [])

    def test_worker_audio_blocks_poisoned_job_already_in_queue(self):
        """งานที่ id เป็นพิษ "มีอยู่แล้ว" ในคิว (จำลองข้อมูลก่อนแพตช์จุดที่ 1) ต้องยังโดนกัน

        นี่คือสิ่งที่พิสูจน์ว่า guard อยู่ *ก่อน* เช็ค 404: ถ้า guard เขียนผิดที่ (มาหลังเช็ค
        `jobs.get(job_id) is None`) เคสนี้จะหลุดไปเขียนไฟล์เพราะ jobs.get() เจอ job จริง
        เทียบกับงานที่ไม่เคยมีอยู่เลย (ไม่ insert ก่อน) ก็ต้องได้ 400 เหมือนกันเป๊ะ — สถานะ
        "มี/ไม่มี job" ต้องไม่มีผลต่อคำตอบเลย ถ้า guard อยู่ถูกที่
        """
        mid = self._new_meeting()
        canary = f"MAI_BUG044_CANARY_{new_mid().replace('-', '')}"
        # ".." ตัวแรกหักล้าง segment "{mid}.tr." (ที่ไม่มีอยู่จริงเป็นโฟลเดอร์) ตัวที่สองจึง
        # ค่อยพาออกไปเหนือ WEB_DIR หนึ่งชั้น — ตรงกับรูปแบบ "<mid>.tr./../../../pwned" ในตั๋ว
        poisoned_suffix = f"tr./../../{canary}"
        poisoned_id = f"{mid}.{poisoned_suffix}"
        canary_path = self.web_dir.parent / f"{canary}.wav"
        self.addCleanup(lambda: canary_path.unlink(missing_ok=True))

        self._insert_job(poisoned_id, meeting_id=mid, lang=f"/../../{canary}")
        self.assertTrue(self._job_exists(poisoned_id), "precondition: งานเป็นพิษต้องมีอยู่จริง")

        encoded = urllib.parse.quote(poisoned_id, safe="")
        status_existing, body_existing, _ = self.post_bytes(
            f"/api/worker/jobs/{encoded}/audio?ext=wav", b"pwn-me",
            extra_headers=_worker_header())

        # เทียบกับ id เดียวกันเป๊ะแต่ไม่เคย insert เลย (ไม่มี job จริง)
        never_existed_id = f"{new_mid()}.{poisoned_suffix}"
        self.assertFalse(self._job_exists(never_existed_id))
        encoded_missing = urllib.parse.quote(never_existed_id, safe="")
        status_missing, body_missing, _ = self.post_bytes(
            f"/api/worker/jobs/{encoded_missing}/audio?ext=wav", b"pwn-me",
            extra_headers=_worker_header())

        self.assertEqual(status_existing, 400, body_existing)
        self.assertEqual(body_existing.get("error"), BAD_JOB_ID_MSG)
        self.assertEqual(status_missing, 400, body_missing)
        self.assertEqual(body_missing.get("error"), BAD_JOB_ID_MSG)
        self.assertNotEqual(status_existing, 404)
        self.assertFalse(canary_path.exists())

    # ---------- happy path: ต้องยังทำงานเหมือนเดิม ----------

    def test_worker_audio_and_tracks_happy_path_still_200(self):
        job_id = self._make_draft_with_track_and_queue()

        status, raw, _ = self.get(f"/api/worker/jobs/{job_id}/tracks/mixed",
                                  extra_headers=_worker_header())
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"0" * 32)

        status, body, _ = self.post_bytes(f"/api/worker/jobs/{job_id}/audio?ext=wav",
                                          b"processed-mixdown", extra_headers=_worker_header())
        self.assertEqual(status, 200, body)
        dest = self.web_dir / f"{job_id}.wav"
        self.assertTrue(dest.exists())
        self.assertEqual(dest.read_bytes(), b"processed-mixdown")


class TestBug044TranslateWorkerChainCloud(_TranslateWorkerChainMixin, CloudCase):
    @property
    def web_dir(self) -> Path:
        return self.store.WEB_DIR

    def _new_meeting(self) -> str:
        mid = new_mid()
        self.store.add_meeting(mid, self.uid_a, title="ประชุมทดสอบ BUG-044")
        return mid

    def _translate(self, mid: str, lang: str):
        return self.post_json(f"/api/meetings/{mid}/translate", {"lang": lang},
                              cookies={"mai_session": self.tokA})

    def _insert_job(self, job_id: str, meeting_id: str, lang: str) -> None:
        self.store.jobs[job_id] = {
            "id": job_id, "meeting_id": meeting_id, "kind": "translate", "status": "queued",
            "step": "รอคิว", "progress": 0.0, "title": "poisoned (pre-patch data)",
            "error": None, "warning": None, "created": "2026-01-01T00:00:00", "worker": None,
            "spec": {"id": job_id, "title": "poisoned", "owner_id": None,
                     "meeting": meeting_id, "lang": lang},
        }

    def _job_exists(self, job_id: str) -> bool:
        return jobs.get(job_id) is not None

    def _job_ids_with_prefix(self, prefix: str) -> list[str]:
        return [k for k in self.store.jobs if k.startswith(prefix)]

    def _translation_saved(self, mid: str, lang: str) -> str | None:
        return (self.store.meetings[mid].get("translations") or {}).get(lang)

    def _make_draft_with_track_and_queue(self) -> str:
        cookies = {"mai_session": self.tokA}
        status, body, _ = self.post_bytes(f"/api/meetings/{self.D_A}/tracks/mixed?ext=wav",
                                          b"0" * 32, cookies=cookies)
        self.assertEqual(status, 200, body)
        status, body, _ = self.post_json(f"/api/meetings/{self.D_A}/process", cookies=cookies)
        self.assertEqual(status, 202, body)
        return self.D_A


class TestBug044TranslateWorkerChainLocal(_TranslateWorkerChainMixin, LocalCase):
    @property
    def web_dir(self) -> Path:
        return filestore.WEB_DIR

    def _new_meeting(self) -> str:
        mid = new_mid()
        filestore.create(mid, title="ประชุมทดสอบ BUG-044", audio_name="", source="upload",
                         language="th", duration=0, segments=[], summary="สรุปทดสอบ")
        return mid

    def _translate(self, mid: str, lang: str):
        return self.post_json(f"/api/meetings/{mid}/translate", {"lang": lang})

    def _insert_job(self, job_id: str, meeting_id: str, lang: str) -> None:
        jobs._jobs[job_id] = {
            "id": job_id, "status": "queued", "step": "รอคิว", "progress": 0.0,
            "title": "poisoned (pre-patch data)", "kind": "translate", "meeting_id": meeting_id,
            "error": None, "warning": None, "created": "2026-01-01T00:00:00",
            "_meeting": meeting_id, "_lang": lang,
        }
        self.addCleanup(jobs._jobs.pop, job_id, None)

    def _job_exists(self, job_id: str) -> bool:
        return jobs.get(job_id) is not None

    def _job_ids_with_prefix(self, prefix: str) -> list[str]:
        return [k for k in jobs._jobs if k.startswith(prefix)]

    def _translation_saved(self, mid: str, lang: str) -> str | None:
        return (filestore.get(mid).get("translations") or {}).get(lang)

    def _make_draft_with_track_and_queue(self) -> str:
        status, body, _ = self.post_json("/api/meetings", {"title": "ประชุมทดสอบ audio"})
        self.assertEqual(status, 201, body)
        mid = body["id"]
        status, body, _ = self.post_bytes(f"/api/meetings/{mid}/tracks/mixed?ext=wav", b"0" * 32)
        self.assertEqual(status, 200, body)
        status, body, _ = self.post_json(f"/api/meetings/{mid}/process")
        self.assertEqual(status, 202, body)
        self.addCleanup(jobs._jobs.pop, mid, None)
        return mid

    def _cleanup_job(self, job_id: str) -> None:
        # local mode ไม่มี _ensure_worker (REMOTE_WORKER=1) — งานค้างใน jobs._jobs ข้ามเทสต์ได้
        # (ดู MEMORY.md ของ test-engineer) ต้องเก็บกวาดเองไม่งั้นเทสต์อื่นนับจำนวนงานผิด
        jobs._jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
