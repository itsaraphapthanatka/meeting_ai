"""BUG-045 — โหมด `files` ใช้บัคเก็ต R2 production โดยไม่มีใครสั่ง.

Root cause เดิม: backend.storage() เรียก blobstore.get_storage() ตรงๆ (ไม่ส่ง allow_remote)
และ get_storage() เลือก S3Storage ทันทีที่เจอ S3_BUCKET ครบใน environment โดยไม่ดูว่า
กำลังอยู่โหมด cloud หรือไม่ .env ของเครื่องพัฒนาถือค่า production จริง ๆ ทุกการรันโหมดไฟล์
ในเครื่องจึงเปิด S3 ให้เองเงียบ ๆ (docs/tickets/BUG-045-implicit-remote-blob-storage.md)

หลังแก้: blobstore.get_storage(local_root, allow_remote=False) เลือก S3 ก็ต่อเมื่อ
allow_remote=True (โหมด cloud ส่งมาเอง) หรือ remote_opt_in() เป็นจริง (ตั้ง
MEETING_AI_REMOTE_BLOBS=1 เอง) — และทุกการเลือกพิมพ์บรรทัดแจ้งเตือนลง stderr (ห้ามมีคีย์)
backend.storage() ส่ง allow_remote=cloud ให้ blobstore.get_storage()

ANTI-LEAK: ห้ามพึ่งค่า S3_* ที่หลุดมาจาก .env จริงของเครื่อง (config.py โหลดด้วย
os.environ.setdefault ตอน import — ถ้า _harness ไม่ได้ตั้งชื่อตัวแปรนั้นไว้ก่อน ค่า production
อาจหลุดเข้า os.environ ของโพรเซสเทสต์ได้) ทุกเทสต์ในไฟล์นี้ override ตัวแปร S3_* และ
MEETING_AI_REMOTE_BLOBS ทั้งหมดด้วยค่าปลอมชัดเจนผ่าน mock.patch.dict เสมอ ไม่มีเทสต์ไหน
เรียก .put()/.get()/.exists() ของ S3Storage จริง (จะยิง HTTP ออกไปที่ endpoint ปลอม) —
ตรวจแค่ .kind/.bucket/.endpoint และข้อความแจ้งเตือนที่พิมพ์ก็พอ
"""

from __future__ import annotations

import contextlib
import io
import tempfile
import shutil
import unittest
from pathlib import Path
from unittest import mock

from _harness import backend, blobstore, filestore  # noqa: F401  ทุกไฟล์เทสต์ import _harness ก่อนเสมอ

# ค่าปลอมล้วน ๆ — โดเมน .test สงวนไว้สำหรับเอกสาร/เทสต์ (RFC 2606) ไม่มีทาง resolve ได้จริง
# และไม่มีเทสต์ไหนเรียก network จริงกับมันอยู่แล้ว
FAKE_S3 = {
    "S3_ENDPOINT": "https://fake-bucket.example.test",
    "S3_BUCKET": "fake-test-bucket",
    "S3_ACCESS_KEY_ID": "FAKE_ACCESS_KEY_DO_NOT_USE",
    "S3_SECRET_ACCESS_KEY": "FAKE_SECRET_DO_NOT_USE_1234",
    "S3_REGION": "auto",
}

# ค่าว่างของตัวแปรทั้งหมดที่เกี่ยว — ใช้เป็นฐานเพื่อไม่ให้ .env จริงหลุดเข้ามา
_ALL_BLANK = {
    "S3_ENDPOINT": "", "S3_BUCKET": "", "S3_ACCESS_KEY_ID": "",
    "S3_SECRET_ACCESS_KEY": "", "S3_REGION": "", "MEETING_AI_REMOTE_BLOBS": "",
}


def _env(**overrides):
    """mock.patch.dict(os.environ, ...) ที่เริ่มจากว่างทั้งหมดเสมอ กัน .env จริงหลุดเข้ามา."""
    values = dict(_ALL_BLANK)
    values.update(overrides)
    return mock.patch.dict("os.environ", values, clear=False)


@contextlib.contextmanager
def _captured_stderr():
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        yield buf


class TestBug045BlobStorageOptIn(unittest.TestCase):
    """ทดสอบ blobstore.get_storage() โดยตรง — ไม่ต้องมี HTTP server."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="mai-blob-test-"))
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        blobstore.reset()
        self.addCleanup(blobstore.reset)

    # ---------- AC1: S3_* ครบ, ไม่ cloud, ไม่ opt-in -> ดิสก์ + มีบรรทัดแจ้งวิธีเปิด ----------

    def test_ac1_full_s3_no_cloud_no_optin_falls_back_to_local_disk(self) -> None:
        with _env(**FAKE_S3):
            with _captured_stderr() as err:
                storage = blobstore.get_storage(self._tmp, allow_remote=False)

        self.assertEqual(storage.kind, "local")
        self.assertEqual(storage.root, self._tmp)
        notice = err.getvalue()
        self.assertIn("ดิสก์", notice)
        self.assertIn(FAKE_S3["S3_BUCKET"], notice)  # บอกชื่อ bucket ที่ข้ามไป (แค่ชื่อ ไม่ใช่คีย์)
        self.assertIn(blobstore.REMOTE_ENV, notice)  # ต้องบอกด้วยว่าตั้งตัวแปรอะไรถึงจะเปิดได้

    # ---------- AC2: เพิ่ม MEETING_AI_REMOTE_BLOBS=1 -> กลับไปใช้ S3 + แจ้งเตือน bucket ----------

    def test_ac2_explicit_optin_switches_to_s3(self) -> None:
        with _env(MEETING_AI_REMOTE_BLOBS="1", **FAKE_S3):
            with _captured_stderr() as err:
                storage = blobstore.get_storage(self._tmp, allow_remote=False)

        self.assertEqual(storage.kind, "s3")
        self.assertEqual(storage.bucket, FAKE_S3["S3_BUCKET"])
        self.assertEqual(storage.endpoint, FAKE_S3["S3_ENDPOINT"].rstrip("/"))
        notice = err.getvalue()
        self.assertIn(FAKE_S3["S3_BUCKET"], notice)
        self.assertIn(FAKE_S3["S3_ENDPOINT"], notice)

    # ---------- AC3: โหมด cloud (allow_remote=True) + S3_* -> S3 โดยไม่ต้อง opt-in ----------

    def test_ac3_cloud_mode_uses_s3_without_explicit_optin(self) -> None:
        """สำคัญที่สุด: ต้องไม่ทำให้ production (cloud + S3 จริง) หยุดทำงาน.

        โหมด cloud ส่ง allow_remote=True มาเอง (ผ่าน backend.storage(); ดู
        test_backend_storage_wiring ด้านล่างสำหรับการต่อสาย) — MEETING_AI_REMOTE_BLOBS
        ต้องไม่ถูกตั้งเลยในเคสนี้ เพื่อพิสูจน์ว่า cloud ไม่ต้องพึ่งตัวแปร opt-in ของโหมดไฟล์
        """
        with _env(**FAKE_S3):  # MEETING_AI_REMOTE_BLOBS ยังว่างอยู่โดยตั้งใจ
            self.assertEqual(blobstore.remote_opt_in(), False)
            with _captured_stderr() as err:
                storage = blobstore.get_storage(self._tmp, allow_remote=True)

        self.assertEqual(storage.kind, "s3")
        self.assertEqual(storage.bucket, FAKE_S3["S3_BUCKET"])
        self.assertIn(FAKE_S3["S3_BUCKET"], err.getvalue())

    # ---------- AC4: ไม่มี S3_* เลย -> ดิสก์ เงียบสนิท ----------

    def test_ac4_no_s3_env_is_silent_local_disk(self) -> None:
        with _env():  # ทุกอย่างว่างหมด รวม MEETING_AI_REMOTE_BLOBS
            with _captured_stderr() as err:
                storage = blobstore.get_storage(self._tmp, allow_remote=False)
                # โหมด cloud ที่ไม่มี S3_* เลยก็ต้องเงียบเหมือนกัน (ไม่มีอะไรให้เตือน)
                blobstore.reset()
                storage_cloud = blobstore.get_storage(self._tmp, allow_remote=True)

        self.assertEqual(storage.kind, "local")
        self.assertEqual(storage_cloud.kind, "local")
        self.assertEqual(err.getvalue(), "", "ไม่ควรมีบรรทัดแจ้งเตือนใด ๆ เมื่อไม่ได้ตั้ง S3_* เลย")

    # ---------- opt-in เป็นค่าว่าง/ขยะ -> ต้องนับเป็น "ไม่ opt-in" (ดิสก์) ----------

    def test_optin_blank_string_is_not_optin(self) -> None:
        with _env(MEETING_AI_REMOTE_BLOBS="", **FAKE_S3):
            with _captured_stderr():
                storage = blobstore.get_storage(self._tmp, allow_remote=False)
        self.assertEqual(storage.kind, "local")

    def test_optin_junk_value_is_not_optin(self) -> None:
        for junk in ("0", "no", "false", "off", "  ", "ใช่", "yesplease"):
            with self.subTest(junk=junk):
                blobstore.reset()
                with _env(MEETING_AI_REMOTE_BLOBS=junk, **FAKE_S3):
                    with _captured_stderr():
                        storage = blobstore.get_storage(self._tmp, allow_remote=False)
                self.assertEqual(storage.kind, "local", f"opt-in={junk!r} ต้องไม่เปิด S3")

    def test_optin_accepts_common_truthy_spellings(self) -> None:
        for truthy in ("1", "true", "True", "YES", "on"):
            with self.subTest(truthy=truthy):
                blobstore.reset()
                with _env(MEETING_AI_REMOTE_BLOBS=truthy, **FAKE_S3):
                    with _captured_stderr():
                        storage = blobstore.get_storage(self._tmp, allow_remote=False)
                self.assertEqual(storage.kind, "s3", f"opt-in={truthy!r} ควรเปิด S3")

    # ---------- opt-in=1 แต่ S3_* ไม่ครบ -> ยังตกไปดิสก์ แต่ต้องไม่เงียบ (บั๊กกลับด้านของ BUG-045:
    # พิมพ์ชื่อตัวแปรผิด/ลืมตั้งสักตัว ต้องรู้ว่าทำไมไม่เปิด S3 ตามที่สั่ง ไม่ใช่งงว่าทำไมยังใช้ดิสก์)
    # ตรวจแค่ชื่อตัวแปรที่หายและ .kind — ไม่ตรวจข้อความเต็มเพราะถ้อยคำอาจเปลี่ยนได้ทีหลัง

    def test_optin_with_incomplete_s3_falls_back_to_disk_and_names_missing_var(self) -> None:
        incomplete = dict(FAKE_S3)
        del incomplete["S3_ENDPOINT"]  # ลืมตั้งตัวนี้ไว้ตัวเดียว — S3_BUCKET ยังอยู่

        with _env(MEETING_AI_REMOTE_BLOBS="1", **incomplete):
            with _captured_stderr() as err:
                storage = blobstore.get_storage(self._tmp, allow_remote=False)

        notice = err.getvalue()
        self.assertEqual(storage.kind, "local", "opt-in ไว้แต่ S3_* ไม่ครบ ต้องตกไปดิสก์ ไม่ใช่พังหรือใช้ S3 บางส่วน")
        self.assertNotEqual(notice, "", "opt-in ไว้แต่ S3_* ไม่ครบ ต้องมีบรรทัดแจ้ง ไม่ใช่เงียบ")
        self.assertIn("S3_ENDPOINT", notice, "ต้องบอกชื่อตัวแปรที่ขาดจริง ไม่ใช่แค่บอกว่า 'ตั้งไม่ครบ' เฉย ๆ")
        # คีย์ลับต้องไม่รั่วแม้ในเคสนี้
        self.assertNotIn(FAKE_S3["S3_SECRET_ACCESS_KEY"], notice)
        self.assertNotIn(FAKE_S3["S3_ACCESS_KEY_ID"], notice)

    def test_optin_with_multiple_missing_s3_vars_names_each_one(self) -> None:
        incomplete = dict(FAKE_S3)
        del incomplete["S3_ACCESS_KEY_ID"]
        del incomplete["S3_SECRET_ACCESS_KEY"]

        with _env(MEETING_AI_REMOTE_BLOBS="1", **incomplete):
            with _captured_stderr() as err:
                storage = blobstore.get_storage(self._tmp, allow_remote=False)

        notice = err.getvalue()
        self.assertEqual(storage.kind, "local")
        self.assertIn("S3_ACCESS_KEY_ID", notice)
        self.assertIn("S3_SECRET_ACCESS_KEY", notice)

    # ---------- AC5/6: ห้ามมีคีย์หลุดใน notice ไหนเลย ----------

    def test_no_credentials_leak_in_any_notice(self) -> None:
        secret = FAKE_S3["S3_SECRET_ACCESS_KEY"]
        access_key = FAKE_S3["S3_ACCESS_KEY_ID"]

        # เคสที่ 1: ข้าม S3 (แจ้งวิธีเปิด) — ยังไม่ควรมีคีย์
        with _env(**FAKE_S3):
            with _captured_stderr() as err_skip:
                blobstore.get_storage(self._tmp, allow_remote=False)
        self.assertNotIn(secret, err_skip.getvalue())
        self.assertNotIn(access_key, err_skip.getvalue())

        # เคสที่ 2: เลือกใช้ S3 จริง (แจ้ง endpoint+bucket) — ยังไม่ควรมีคีย์
        blobstore.reset()
        with _env(MEETING_AI_REMOTE_BLOBS="1", **FAKE_S3):
            with _captured_stderr() as err_s3:
                blobstore.get_storage(self._tmp, allow_remote=False)
        self.assertNotIn(secret, err_s3.getvalue())
        self.assertNotIn(access_key, err_s3.getvalue())

    def test_missing_pieces_never_prints_partial_credentials(self) -> None:
        # ตั้งครบยกเว้นคีย์ลับ -> ยังไม่ "configured" (ready=False) แต่ต้องไม่พิมพ์อะไรที่มีคีย์
        partial = dict(FAKE_S3)
        partial["S3_SECRET_ACCESS_KEY"] = ""
        with _env(**partial):
            with _captured_stderr() as err:
                storage = blobstore.get_storage(self._tmp, allow_remote=False)
        self.assertEqual(storage.kind, "local")
        self.assertEqual(err.getvalue(), "")  # ยังไม่ครบ ไม่ถือว่า "ready" เลยไม่ต้องเตือนอะไร


class TestBug045BackendWiring(unittest.TestCase):
    """backend.storage() ต้องส่ง allow_remote=cloud ให้ blobstore.get_storage() (ไม่ใช่เรียกเปล่า ๆ).

    นี่คือจุดที่บั๊กเดิมอยู่จริง (backend.py:36-39 เดิมเรียก blobstore.get_storage() โดยไม่ส่ง
    allow_remote เลย) — เทสต์นี้ mock blobstore.get_storage() เพื่อตรวจ "สาย" การเรียก
    โดยตรง แยกจากพฤติกรรมข้างในของ get_storage() เอง (ทดสอบไปแล้วในคลาสด้านบน)
    """

    def setUp(self) -> None:
        self.addCleanup(blobstore.reset)

    def test_storage_passes_cloud_true_as_allow_remote(self) -> None:
        sentinel = object()
        with mock.patch.object(backend, "cloud", True), \
             mock.patch.object(blobstore, "get_storage", return_value=sentinel) as mocked:
            result = backend.storage()

        self.assertIs(result, sentinel)
        mocked.assert_called_once()
        _args, kwargs = mocked.call_args
        self.assertTrue(
            kwargs.get("allow_remote", _args[1] if len(_args) > 1 else None),
            "backend.storage() ในโหมด cloud ต้องส่ง allow_remote=True",
        )

    def test_storage_passes_cloud_false_as_allow_remote(self) -> None:
        sentinel = object()
        with mock.patch.object(backend, "cloud", False), \
             mock.patch.object(blobstore, "get_storage", return_value=sentinel) as mocked:
            result = backend.storage()

        self.assertIs(result, sentinel)
        mocked.assert_called_once()
        _args, kwargs = mocked.call_args
        got = kwargs.get("allow_remote", _args[1] if len(_args) > 1 else None)
        self.assertFalse(
            got,
            "backend.storage() ในโหมดไฟล์ (ไม่ cloud) ต้องส่ง allow_remote=False "
            "ไม่ใช่ปล่อยให้ get_storage() ตัดสินใจเองจาก S3_* เพียงอย่างเดียว (นี่คือบั๊กเดิม)",
        )


if __name__ == "__main__":
    unittest.main()
