"""BUG-056 — coverage hole found alongside the lock fix.

ก่อนใบนี้ ชุดทดสอบทั้งหมดไม่เคยเรียก store.create/update/set_segments/set_summary/
set_translation/delete แบบเขียนไฟล์จริงเลยสักครั้ง (มีแค่ LocalCase หนึ่งคลาสที่ทดสอบ
draft ownership ผ่าน jobs ไม่ใช่ store) — พิสูจน์แล้วระหว่างพัฒนาบั๊กนี้: แพตช์แรกใช้
`msvcrt.LOCK_NBLCK` ซึ่งไม่มีจริง (ชื่อถูกคือ `LK_NBLCK`) ทำให้ store.create()
โยน AttributeError ทุกครั้งที่เรียก แต่ชุดทดสอบเดิม 64/64 ยังผ่านหมด เพราะไม่มีเทสต์ไหน
เรียกเส้นทางเขียนจริงเลย ใบนี้ปิดช่องว่างนั้นด้วยเทสต์ตรงไปตรงมา: เรียกจริง อ่านไฟล์จริง

ไม่ใช้ HTTP layer/LocalCase (เร็วกว่าและตรงประเด็นกว่า) — แพตช์ WEB_DIR/INDEX_PATH/
SETTINGS_PATH เหมือนที่ tests/_harness.py ทำให้ LocalCase (ทั้งสามตัวคำนวณตอน import
ไม่ใช่ property — แพตช์ตัวเดียวไม่พอ).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _harness import filestore, new_mid


class FileStoreWritePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="mai-bug056-basic-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        web_dir = Path(self._tmp)

        patches = [
            mock.patch.object(filestore, "WEB_DIR", web_dir),
            mock.patch.object(filestore, "INDEX_PATH", web_dir / "index.json"),
            mock.patch.object(filestore, "SETTINGS_PATH", web_dir / "settings.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        # ล็อกจริงยังทำงานได้ (ไม่ได้ปิด) — เทสต์นี้ทดสอบ "เขียนสำเร็จไหม" ไม่ใช่ concurrency
        filestore._detail_cache.clear()
        self.addCleanup(filestore._detail_cache.clear)

    def test_create_writes_index_and_detail(self) -> None:
        mid = new_mid()
        meta = filestore.create(
            mid, "หัวข้อประชุม", "a.wav", "upload", "th", 12.5,
            [{"speaker": "A", "start": 0, "end": 1, "text": "สวัสดี"}],
            "สรุปการประชุม", template="general", speakers=["A"],
        )
        self.assertEqual(meta["id"], mid)
        self.assertEqual(meta["segments"], 1)

        got = filestore.get(mid)
        self.assertIsNotNone(got)
        self.assertEqual(got["title"], "หัวข้อประชุม")
        self.assertEqual(got["summary"], "สรุปการประชุม")
        self.assertEqual(got["segments_list"][0]["text"], "สวัสดี")

        # ต้องอยู่ในดัชนีจริงบนดิสก์ ไม่ใช่แค่แคชในหน่วยความจำ
        index_on_disk = filestore._read_json(filestore.INDEX_PATH, {})
        ids = [m["id"] for m in index_on_disk.get("meetings", [])]
        self.assertIn(mid, ids)
        self.assertTrue((filestore.WEB_DIR / f"{mid}.json").exists())

    def test_update_changes_title_and_summary(self) -> None:
        mid = new_mid()
        filestore.create(mid, "เดิม", "a.wav", "upload", "th", 1.0, [], "สรุปเดิม")

        out = filestore.update(mid, title="ใหม่", summary="สรุปใหม่")
        self.assertEqual(out["title"], "ใหม่")
        self.assertEqual(out["summary"], "สรุปใหม่")
        self.assertTrue(out["edited"])

        # ยืนยันติดถาวรบนดิสก์ ไม่ใช่แค่ค่าที่ update() คืนมา
        again = filestore.get(mid)
        self.assertEqual(again["title"], "ใหม่")
        self.assertEqual(again["summary"], "สรุปใหม่")

    def test_set_segments_updates_count_and_speakers(self) -> None:
        mid = new_mid()
        filestore.create(mid, "t", "a.wav", "upload", "th", 1.0, [], "s")

        segs = [
            {"speaker": "A", "start": 0, "end": 1, "text": "หนึ่ง"},
            {"speaker": "B", "start": 1, "end": 2, "text": "สอง"},
        ]
        out = filestore.set_segments(mid, segs)
        self.assertEqual(out["segments"], 2)
        self.assertEqual(out["speakers"], ["A", "B"])
        self.assertTrue(out["transcript_edited"])

        again = filestore.get(mid)
        self.assertEqual(len(again["segments_list"]), 2)

    def test_set_summary_updates_summary_and_clears_no_edited_flag(self) -> None:
        mid = new_mid()
        filestore.create(mid, "t", "a.wav", "upload", "th", 1.0, [], "s", summary_error="เดิมพัง")

        out = filestore.set_summary(mid, "สรุปจาก AI ใหม่", error=None)
        self.assertEqual(out["summary"], "สรุปจาก AI ใหม่")
        self.assertIsNone(out["summary_error"])
        # set_summary ไม่ใช่คนแก้เอง ต้องไม่ตั้ง edited (ต่างจาก update())
        self.assertFalse(out.get("edited", False))

    def test_set_translation_stores_multiple_languages(self) -> None:
        mid = new_mid()
        filestore.create(mid, "t", "a.wav", "upload", "th", 1.0, [], "s")

        filestore.set_translation(mid, "en", "hello")
        out = filestore.set_translation(mid, "ja", "konnichiwa")
        self.assertEqual(out["translations"], {"en": "hello", "ja": "konnichiwa"})

    def test_delete_removes_index_entry_and_files(self) -> None:
        mid = new_mid()
        filestore.create(mid, "t", "a.wav", "upload", "th", 1.0, [], "s")
        self.assertTrue((filestore.WEB_DIR / f"{mid}.json").exists())

        self.assertTrue(filestore.delete(mid))
        self.assertIsNone(filestore.get(mid))
        self.assertFalse((filestore.WEB_DIR / f"{mid}.json").exists())
        # เรียกซ้ำกับ id ที่ไม่มีแล้วต้องคืน False ไม่ใช่ throw
        self.assertFalse(filestore.delete(mid))

    def test_bug_056_broken_lock_constant_fails_loudly(self) -> None:
        """ปิดช่องว่างที่ทำให้บั๊กจริงหลุดผ่าน 64/64 มาได้ (ดู docstring ของไฟล์).

        จำลองแพตช์ผิดตัวเดิม (`msvcrt.LOCK_NBLCK` ไม่มีจริง) โดยแทน `_try_lock` ด้วยฟังก์ชัน
        ที่โยน AttributeError แบบเดียวกัน แล้วยืนยันว่า store.create() ทำให้เทสต์นี้ล้มเหลว
        (ไม่ใช่ผ่านเงียบ ๆ เหมือนที่เกิดขึ้นจริงตอนพัฒนาบั๊กนี้)
        """
        def _broken(_fd: int) -> bool:
            raise AttributeError("module 'msvcrt' has no attribute 'LOCK_NBLCK'")

        mid = new_mid()
        with mock.patch.object(filestore, "_try_lock", _broken):
            with self.assertRaises(AttributeError):
                filestore.create(mid, "t", "a.wav", "upload", "th", 1.0, [], "s")


class LockKindTests(unittest.TestCase):
    """กลไกล็อกถูกเลือกตอน import ด้วย hasattr() — เลือกพลาดแล้วเงียบกว่าบั๊กเดิมมาก.

    วัดไว้ตอนตรวจตั๋ว #57: ใส่บั๊กจริง (`LK_NBLCK` -> `LOCK_NBLCK`) กลับเข้าไปใน
    `_try_lock` ทำให้สวีททั้งชุดแดง 62 ตัว แต่พิมพ์ผิดชื่อเดียวกันบน **บรรทัดที่เลือกกลไก**
    แดงแค่ 1 ตัว เพราะ `hasattr()` คืน False เฉย ๆ แล้วตกไปสาขาอื่นแบบไม่มีใครบ่น
    ถ้าตกไปจนได้ `_LOCK_KIND == ""` จะไม่มีล็อกข้ามโพรเซสเลย ซึ่งคือสภาพก่อน BUG-056
    ทั้งที่โค้ดดูเหมือนมีล็อก
    """

    def test_a_lock_mechanism_was_actually_chosen(self):
        self.assertIn(filestore._LOCK_KIND, ("msvcrt", "fcntl"),
                      "ไม่มีกลไกล็อกข้ามโพรเซส = กลับไปเป็น BUG-056 โดยไม่มีอะไรเตือน")

    def test_it_matches_the_platform(self):
        # บน Windows สาขา fcntl เป็นโค้ดตาย และกลับกันบน Linux — เทสต์นี้จึงเป็นตัวเดียว
        # ที่จับการพิมพ์ผิดบนบรรทัดเลือกกลไกได้จากทั้งสองฝั่ง
        expected = "msvcrt" if os.name == "nt" else "fcntl"
        self.assertEqual(filestore._LOCK_KIND, expected)

    def test_the_constant_it_selects_on_really_exists(self):
        if filestore._LOCK_KIND == "msvcrt":
            for name in ("LK_NBLCK", "LK_UNLCK"):
                with self.subTest(const=name):
                    self.assertTrue(hasattr(filestore.msvcrt, name))
        else:
            for name in ("LOCK_EX", "LOCK_NB", "LOCK_UN"):
                with self.subTest(const=name):
                    self.assertTrue(hasattr(filestore.fcntl, name))


if __name__ == "__main__":
    unittest.main()
