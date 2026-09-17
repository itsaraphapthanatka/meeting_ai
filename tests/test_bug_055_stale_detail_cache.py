"""BUG-055 — แคชรายละเอียดการประชุมค้าง (โหมดไฟล์): "แก้แล้วไม่เซฟ".

root cause (docs/tickets/BUG-055-stale-detail-cache.md): load_detail() เดิมแคชรายละเอียด
ที่ parse แล้วด้วย mtime ของไฟล์เป็นกุญแจตรวจความสด แต่ mtime หยาบกว่าจังหวะเขียนไฟล์มาก
(วัดได้บนเครื่อง dev: เขียน 300 ครั้งติดกันได้ mtime ต่างกันแค่ 14 ค่า, 286/300 ชนกับ
ครั้งก่อนหน้า) จุดเขียนไฟล์ detail มี 5 แห่ง (create, set_translation, set_segments,
set_summary, update) แต่มีที่เดียวที่ล้างแคชคือ delete() — ทุกเส้นทางเขียนจึงทิ้งแคชเก่าไว้

ของที่แก้ (uncommitted ใน store.py ตอนเขียนเทสต์นี้):
  1. _write_detail() เป็นทางเดียวที่เขียนไฟล์ detail แล้ว pop cache เสมอ
  2. load_detail() แคชเฉพาะไฟล์ที่นิ่งมาแล้ว >= _CACHE_MIN_AGE (2.0s) — กรณีอื่น pop ทิ้ง
     (รวมถึงตอนไฟล์หาย)

ห้าม sleep เพื่อรอ 2 วินาที: ที่ที่ต้องการไฟล์ "นิ่งมานานแล้ว" ใช้ os.utime() ย้อน mtime
กลับไปข้างหลัง (ถูกต้องตามหลักฟิสิกส์ของไฟล์จริง — ไฟล์เก่าจริงก็มี mtime แบบนี้) ส่วนที่
ต้องการ "ชนกัน tick เดียวกัน" (ตัวบั๊กจริง) ใช้ loop ที่เขียนเร็วพอจะชน mtime tick เดียวกันเอง
ตามที่ ticket วัดไว้ — ไม่ยัด mtime ปลอมให้ชนกันตรงๆ เพราะนั่นจำลอง "นาฬิกาเครื่องถอยหลัง"
ซึ่งเป็นข้อจำกัดที่ ticket ยอมรับไว้แล้วว่าแก้ไม่ได้ (ไม่ใช่ช่องโหว่ของบั๊กนี้)
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _harness import LocalCase, filestore, new_mid


class _FileStoreCase(unittest.TestCase):
    """เข้าถึง store.py ตรงๆ (ไม่ผ่าน HTTP) — ชี้ WEB_DIR/INDEX_PATH/SETTINGS_PATH ไปโฟลเดอร์ชั่วคราว

    (ทั้งสามชื่อต้องแพตช์พร้อมกัน มิฉะนั้นจะไปแตะ recordings/web/ ของเจ้าของเครื่องจริง —
    ดู docstring ของ LocalCase ใน _harness.py)
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="mai-bug055-test-")
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
        # _detail_cache เป็น dict ระดับโมดูล ใช้ร่วมกับไฟล์เทสต์อื่น — เคลียร์ก่อน/หลัง
        # แต่ละเทสต์กันไม่ให้ mid ที่สุ่มมาชนกับเทสต์ก่อนหน้าโดยบังเอิญ
        filestore._detail_cache.clear()
        self.addCleanup(filestore._detail_cache.clear)

    def _seed(self, summary: str = "เริ่มต้น") -> str:
        mid = new_mid()
        filestore.create(
            mid=mid, title="ประชุมทดสอบ BUG-055", audio_name=f"{mid}.wav",
            source="upload", language="th", duration=1.0, segments=[], summary=summary,
        )
        return mid

    def _age_past_min_cache_age(self, mid: str) -> None:
        """ย้อน mtime ของไฟล์ detail ให้ดูเหมือน "นิ่งมานานแล้ว" โดยไม่ต้องรอจริง 2 วินาที."""
        path = filestore._detail_path(mid)
        old = time.time() - (filestore._CACHE_MIN_AGE + 5)
        os.utime(path, (old, old))


class TestTightLoopNeverStale(_FileStoreCase):
    """เกณฑ์ผ่านหลักของ BUG-055: วนเขียน-อ่านแน่นๆ (ไม่ sleep) บนแต่ละใน 5 จุดเขียน detail

    ต้องไม่มีการอ่านค่าเก่าเลยสักครั้ง ก่อนแก้ ticket วัดได้ 15/100 ครั้งอ่านค่าเก่า
    (set_translation, 20 รอบ x 5 ภาษา) ใช้ N=200 ที่นี่ให้แน่นกว่าที่ dev ใช้ทดสอบ (100+)
    """

    N = 200

    def test_set_translation_tight_loop_never_stale(self):
        mid = self._seed()
        langs = ["en", "ja", "zh", "ko", "th"]
        stale = 0
        for i in range(self.N):
            lang = langs[i % len(langs)]
            text = f"translation-{i}"
            filestore.set_translation(mid, lang, text)
            got = filestore.get(mid)["translations"].get(lang)
            if got != text:
                stale += 1
        self.assertEqual(stale, 0)

    def test_set_segments_tight_loop_never_stale(self):
        mid = self._seed()
        stale = 0
        for i in range(self.N):
            segs = [{"start": 0, "end": 1, "text": f"seg-{i}", "speaker": "A"}]
            filestore.set_segments(mid, segs)
            got = filestore.get(mid)["segments_list"]
            if got != segs:
                stale += 1
        self.assertEqual(stale, 0)

    def test_set_summary_tight_loop_never_stale(self):
        mid = self._seed()
        stale = 0
        for i in range(self.N):
            summary = f"summary-{i}"
            filestore.set_summary(mid, summary)
            got = filestore.get(mid)["summary"]
            if got != summary:
                stale += 1
        self.assertEqual(stale, 0)

    def test_update_title_tight_loop_never_stale(self):
        mid = self._seed()
        stale = 0
        for i in range(self.N):
            title = f"หัวข้อ-{i}"
            filestore.update(mid, title=title)
            got = filestore.get(mid)["title"]
            if got != title:
                stale += 1
        self.assertEqual(stale, 0)

    def test_update_summary_tight_loop_never_stale(self):
        mid = self._seed()
        stale = 0
        for i in range(self.N):
            summary = f"เกลาสรุป-{i}"
            filestore.update(mid, summary=summary)
            got = filestore.get(mid)["summary"]
            if got != summary:
                stale += 1
        self.assertEqual(stale, 0)

    def test_create_tight_loop_never_stale(self):
        """create() เขียนทับ mid เดิมซ้ำๆ (ตัวเดียวกับอีก 4 จุด) ก็ต้องอ่านค่าล่าสุดได้เสมอ."""
        mid = new_mid()
        stale = 0
        for i in range(self.N):
            summary = f"created-{i}"
            filestore.create(
                mid=mid, title="ประชุมทดสอบ BUG-055", audio_name=f"{mid}.wav",
                source="upload", language="th", duration=1.0, segments=[], summary=summary,
            )
            got = filestore.get(mid)["summary"]
            if got != summary:
                stale += 1
        self.assertEqual(stale, 0)


class TestPatchThenGetNeverStale(LocalCase):
    """เส้นทางเดียวกันผ่าน HTTP จริง: PATCH /api/meetings/{id} ตามด้วย GET ซ้ำๆ.

    POST /api/meetings สร้างได้แค่ draft (งานรออัปโหลด ยังไม่มีไฟล์ detail ให้ PATCH/GET
    — ต้องผ่าน tracks/upload-url + process ซึ่งพึ่ง whisper/ffmpeg) จึงสร้างการประชุมจริง
    ด้วย filestore.create() ตรงๆ (WEB_DIR ถูกแพตช์เป็น temp dir เดียวกับที่ server ใช้อยู่แล้ว
    จาก LocalCase.setUp) แล้วค่อยยิง PATCH/GET ผ่าน HTTP จริงเพื่อทดสอบเส้นทาง server.py
    """

    N = 200

    def test_patch_summary_then_get_matches_every_time(self):
        mid = new_mid()
        filestore.create(
            mid=mid, title="BUG-055 http", audio_name=f"{mid}.wav", source="upload",
            language="th", duration=1.0, segments=[], summary="เริ่มต้น",
        )

        stale = 0
        for i in range(self.N):
            summary = f"http-summary-{i}"
            status, body, _ = self.patch_json(f"/api/meetings/{mid}", {"summary": summary})
            self.assertEqual(status, 200)
            self.assertEqual(body["summary"], summary)  # PATCH เองก็ตอบผ่าน store.get() รอบใหม่

            status, body, _ = self.get(f"/api/meetings/{mid}")
            self.assertEqual(status, 200)
            if body["summary"] != summary:
                stale += 1
        self.assertEqual(stale, 0)


class TestForeignWriterSeenOnNextRead(_FileStoreCase):
    """โพรเซสอื่น (เช่น mai transcribe ที่ใช้ recordings/web/ เดียวกับ mai web) เขียนไฟล์ detail

    ตรงๆ โดยไม่ผ่าน _write_detail() ของโพรเซสเรา (จึงไม่ pop _detail_cache ของเรา — เหมือน
    โพรเซสจริงที่ไม่รู้จักแคชในหน่วยความจำของอีกฝั่งเลย) ทั้งกรณีที่เรายังไม่เคยแคชไฟล์นี้
    และกรณีที่เราแคชไว้แล้ว (ไฟล์นิ่งมานาน) — โพรเซสเราต้องเห็นค่าใหม่เสมอเมื่ออ่านครั้งถัดไป
    """

    def _foreign_write(self, mid: str, detail: dict) -> None:
        filestore._write_json(filestore._detail_path(mid), detail)

    def test_foreign_write_seen_when_not_previously_cached(self):
        mid = self._seed()
        self.assertNotIn(mid, filestore._detail_cache)  # create() pop ไปแล้ว ยังไม่มีใครอ่านซ้ำ

        detail_v2 = {"id": mid, "segments": [], "summary": "จากโพรเซสอื่น", "translations": {}}
        self._foreign_write(mid, detail_v2)
        self.assertEqual(filestore.load_detail(mid)["summary"], "จากโพรเซสอื่น")

    def test_foreign_write_seen_when_already_cached(self):
        mid = self._seed()
        self._age_past_min_cache_age(mid)

        cached = filestore.load_detail(mid)
        self.assertIn(mid, filestore._detail_cache)  # ยืนยันว่าแคชจริง (ไฟล์นิ่งพอ)
        self.assertEqual(cached["summary"], "เริ่มต้น")

        detail_v2 = {"id": mid, "segments": [], "summary": "จากโพรเซสอื่น-2", "translations": {}}
        self._foreign_write(mid, detail_v2)  # mtime ใหม่ = เวลาจริงตอนนี้ ต่างจากที่ย้อนไว้แน่นอน
        self.assertEqual(filestore.load_detail(mid)["summary"], "จากโพรเซสอื่น-2")


class TestCacheStillCaches(_FileStoreCase):
    """เกณฑ์ที่ dev ระบุไว้ตรงๆ ใน ticket: ไฟล์ที่นิ่งเกิน _CACHE_MIN_AGE ต้องเสิร์ฟจากแคช

    ไม่ใช่อ่านดิสก์ทุกครั้ง — ถ้าไม่มีเทสต์นี้ การ "แก้บั๊ก" ในอนาคตแบบตัดแคชทิ้งทั้งหมด
    (ทางเลือก C ที่ ticket ปัดตกเพราะ search() จะอ่านทุกไฟล์ทุกครั้ง) จะผ่านเทสต์ freshness
    ทุกตัวข้างบนได้สบายๆ แต่ทำร้ายประสิทธิภาพเงียบๆ
    """

    def test_stale_enough_file_served_from_cache_without_rereading_disk(self):
        mid = self._seed()
        self._age_past_min_cache_age(mid)

        filestore.load_detail(mid)  # อ่านครั้งแรก -> เข้าเงื่อนไขนิ่งพอ -> แคช
        self.assertIn(mid, filestore._detail_cache)

        with mock.patch.object(filestore, "_read_json", wraps=filestore._read_json) as spy:
            for _ in range(20):
                out = filestore.load_detail(mid)
                self.assertEqual(out["summary"], "เริ่มต้น")
            spy.assert_not_called()  # ทุกครั้งมาจากแคชล้วน ไม่แตะดิสก์เลย


class TestMissingFileClearsCache(_FileStoreCase):
    """ไฟล์ detail หาย (ลบตรงๆ ไม่ผ่าน store.delete()) ต้องไม่ทิ้งรายการแคชเก่าไว้."""

    def test_missing_file_pops_cache_entry_and_recreate_is_fresh(self):
        mid = self._seed()
        path = filestore._detail_path(mid)
        self._age_past_min_cache_age(mid)

        filestore.load_detail(mid)
        self.assertIn(mid, filestore._detail_cache)  # แคชไว้จริงก่อนไฟล์หาย

        path.unlink()
        got = filestore.load_detail(mid)
        self.assertEqual(got, {})
        self.assertNotIn(mid, filestore._detail_cache)  # ห้ามทิ้งรายการเก่าไว้ (BUG-055)

        detail_v2 = {"id": mid, "segments": [], "summary": "สร้างใหม่หลังไฟล์หาย", "translations": {}}
        filestore._write_json(path, detail_v2)
        self.assertEqual(filestore.load_detail(mid)["summary"], "สร้างใหม่หลังไฟล์หาย")


if __name__ == "__main__":
    unittest.main()
