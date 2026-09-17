"""BACKLOG #15 / BUG-015 — ด่านไฟล์ static เทียบ path ด้วย str.startswith.

`_static()` เดิมตรวจว่า "ไฟล์ปลายทางอยู่ใน STATIC_DIR" ด้วยการเทียบสตริง:

    if not str(target).startswith(str(STATIC_DIR.resolve())):

การเทียบคำนำหน้าแบบสตริงไม่รู้จักขอบของโฟลเดอร์ — path ของโฟลเดอร์ *พี่น้อง* ที่ชื่อขึ้นต้น
เหมือนกัน (`.../web/static_backup`, `.../web/static-old`) ก็ขึ้นต้นด้วย `.../web/static`
เช่นกัน จึงผ่านด่านนี้ได้ด้วย `GET /static/../static_backup/<ไฟล์>`

วัดจริงก่อนแก้ (PoC ผ่าน HTTP จริง, STATIC_DIR ชี้ไปโฟลเดอร์ชั่วคราวที่มี static/ กับ
static_backup/ อยู่ข้างกัน): `/static/../static_backup/secret.txt` → **200 พร้อมเนื้อไฟล์**
หลังแก้ (`Path.is_relative_to`) → 404 ส่วน `/static/../../../.env` เป็น 404 ทั้งก่อนและหลัง
(ออกนอกคำนำหน้าไปเลย ด่านเดิมก็กันได้อยู่แล้ว)

ขอบเขตจริงของช่องนี้: ต้องมีไฟล์/โฟลเดอร์ชื่อขึ้นต้นด้วย "static" วางอยู่ข้าง
`meeting_ai/web/static` ซึ่ง **ไม่มีในซอร์สทรีปัจจุบัน** เทสต์ชุดนี้จึงจำลองโครงสร้างนั้นขึ้นมา
เอง = เป็นการปิดคลาสของบั๊ก (prefix confusion) ไม่ใช่การปิดช่องที่ยิงได้ทันทีบน production
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _harness import LocalCase, server


class TestStaticPathGuard(LocalCase):
    """ย้าย STATIC_DIR ไปโฟลเดอร์ชั่วคราวที่มีเพื่อนบ้านชื่อขึ้นต้นเหมือนกัน (ซอร์สทรีจริงไม่มี)."""

    def setUp(self) -> None:
        super().setUp()
        self._static_tmp = Path(tempfile.mkdtemp(prefix="mai-static-test-"))
        self.addCleanup(lambda: shutil.rmtree(self._static_tmp, ignore_errors=True))

        root = self._static_tmp / "static"
        root.mkdir()
        (root / "index.html").write_text("<html>shell</html>", encoding="utf-8")
        (root / "app.js").write_text("// app", encoding="utf-8")
        (root / "sw.js").write_text("// sw", encoding="utf-8")
        (root / "manifest.webmanifest").write_text('{"name":"x"}', encoding="utf-8")

        sibling = self._static_tmp / "static_backup"
        sibling.mkdir()
        (sibling / "secret.txt").write_text("LEAKED-CONTENT-12345", encoding="utf-8")
        # ไฟล์ (ไม่ใช่โฟลเดอร์) ที่ชื่อขึ้นต้นเหมือนกันก็ผ่านด่านสตริงได้เหมือนกัน
        (self._static_tmp / "static.bak").write_text("OLD-BUNDLE", encoding="utf-8")

        p = mock.patch.object(server, "STATIC_DIR", root)
        p.start()
        self.addCleanup(p.stop)

    # ---------- สิ่งที่ต้องยังทำงานได้ ----------

    def test_normal_static_file_still_served(self):
        status, body, _ = self.get("/static/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"// app")

    def test_root_and_root_level_files_still_served(self):
        for path in ("/", "/sw.js", "/manifest.webmanifest"):
            with self.subTest(path=path):
                status, _, _ = self.get(path)
                self.assertEqual(status, 200)

    # ---------- สิ่งที่ต้องถูกปฏิเสธ ----------

    def test_sibling_directory_with_same_prefix_is_rejected(self):
        """ก่อนแก้: 200 + 'LEAKED-CONTENT-12345' (วัดจริง) หลังแก้ต้องเป็น 404."""
        status, body, _ = self.get("/static/../static_backup/secret.txt")
        self.assertEqual(status, 404, f"อ่านไฟล์นอก STATIC_DIR ได้: {body!r}")
        self.assertNotIn(b"LEAKED", body if isinstance(body, bytes) else b"")

    def test_sibling_file_with_same_prefix_is_rejected(self):
        status, body, _ = self.get("/static/../static.bak")
        self.assertEqual(status, 404, f"อ่านไฟล์นอก STATIC_DIR ได้: {body!r}")

    def test_escape_above_static_dir_is_rejected(self):
        for path in ("/static/../../../.env",
                     "/static/../../store.py",
                     "/static/..%2f..%2f.env"):
            with self.subTest(path=path):
                status, _, _ = self.get(path)
                self.assertEqual(status, 404)

    def test_directory_itself_is_not_served(self):
        status, _, _ = self.get("/static/../static")
        self.assertEqual(status, 404)


class TestGuardIsNotVacuous(LocalCase):
    """พิสูจน์ว่าเทสต์ข้างบนจับกลไกที่ตั้งใจจับ: ปลอม is_relative_to กลับไปเป็นการเทียบสตริง
    แบบเดิมแล้วคำขอเดิมต้องกลับมาอ่านไฟล์ได้ 200 อีกครั้ง (ทำในหน่วยความจำ ไม่แตะซอร์สจริง)."""

    def setUp(self) -> None:
        super().setUp()
        self._static_tmp = Path(tempfile.mkdtemp(prefix="mai-static-vac-"))
        self.addCleanup(lambda: shutil.rmtree(self._static_tmp, ignore_errors=True))
        root = self._static_tmp / "static"
        root.mkdir()
        (root / "index.html").write_text("<html>shell</html>", encoding="utf-8")
        (self._static_tmp / "static_backup").mkdir()
        (self._static_tmp / "static_backup" / "secret.txt").write_text("LEAKED", encoding="utf-8")
        p = mock.patch.object(server, "STATIC_DIR", root)
        p.start()
        self.addCleanup(p.stop)
        self.root = root

    def test_old_string_prefix_rule_would_have_leaked(self):
        real_root = self.root.resolve()

        class _PrefixPath(type(real_root)):
            """Path ที่ is_relative_to ทำงานแบบ str.startswith (= พฤติกรรมก่อนแก้)."""

            def is_relative_to(self, other) -> bool:  # type: ignore[override]
                return str(self).startswith(str(other))

        real_resolve = Path.resolve

        def fake_resolve(self, *a, **k):
            return _PrefixPath(real_resolve(self, *a, **k))

        with mock.patch.object(Path, "resolve", fake_resolve):
            status, body, _ = self.get("/static/../static_backup/secret.txt")
        self.assertEqual(status, 200, "ปลอมกฎเดิมแล้วยังไม่รั่ว = เทสต์ข้างบนไม่ได้จับกฎนี้")
        self.assertEqual(body, b"LEAKED")


if __name__ == "__main__":
    unittest.main()
