"""BACKLOG #58 — เครื่อง worker ไม่ต้องมีกุญแจที่เก็บไฟล์.

runbook แนะนำให้ `.env` ของเครื่อง worker มีเฉพาะค่าที่มันใช้จริง เพราะเครื่องนั้น
(`edgexpert-1346`) เป็นเครื่องที่ใช้ร่วมกับงานอื่นอีกสิบกว่าคอนเทนเนอร์ — ทุกค่าลับที่วางไว้
ตรงนั้นโดยไม่จำเป็นคือพื้นที่โดนเพิ่มฟรี ๆ

ข้ออ้างนั้นจะจริงก็ต่อเมื่อ **worker ไม่เคยเซ็นคำขอไปที่ R2 ด้วยตัวเอง** ซึ่งตอนนี้จริง:
มันรับ **presigned URL ที่เซิร์ฟเวอร์ออกให้** แล้วเปิด URL นั้นตรง ๆ ทั้งขาดาวน์โหลดและขาอัปโหลด
(`worker.py` บรรทัดคอมเมนต์ "presigned PUT ตรงเข้าที่เก็บ — ไม่ผ่านเซิร์ฟเวอร์")

เทสต์ชุดนี้ตรึงข้อนั้นไว้ ถ้าวันหนึ่งมีคนทำให้ worker ไปคุยกับ R2 เอง เทสต์จะล้มพร้อมเตือนว่า
**คำแนะนำใน runbook กลายเป็นคำแนะนำที่ผิดแล้ว** ไม่ใช่ปล่อยให้เอกสารค้างอยู่เงียบ ๆ
อย่างที่ตั๋วนี้เคยเจอมาสองรอบ

วัดจริงบนเครื่อง worker 2026-10-05 (พิมพ์แค่ชื่อตัวแปรกับ sha256 12 ตัวแรก ไม่มีค่าใด ๆ):
`.env` ที่นั่นมี 20 ตัวแปร · `DATABASE_URL` และ `VERCEL_TOKEN` **หายไปแล้ว** ·
ยังมี `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` / `S3_BUCKET` / `S3_ENDPOINT`
ซึ่งลายนิ้วมือตรงกับของเครื่องเจ้าของเป๊ะ และ**ไม่มีโค้ดเส้นไหนบนเครื่องนั้นอ่านมันเลย**
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_SRC = (ROOT / "meeting_ai" / "worker.py").read_text(encoding="utf-8")
RUNNER_SRC = (ROOT / "meeting_ai" / "runner.py").read_text(encoding="utf-8")
BLOBSTORE = (ROOT / "meeting_ai" / "web" / "blobstore.py").read_text(encoding="utf-8")
RUNBOOK = (ROOT / "docs" / "runbooks" / "rotate-credentials.md").read_text(encoding="utf-8")

# ชื่อที่ "ต้องมีกุญแจถึงจะเรียกได้" ใน blobstore
NEEDS_KEYS = {"client", "presign", "Client"}


def imported_from_blobstore(src: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.ImportFrom) and node.module and "blobstore" in node.module:
            names.update(a.name for a in node.names)
    return names


class TestTheWorkerNeverSignsItsOwnRequests(unittest.TestCase):

    def test_it_borrows_only_the_plain_url_opener(self):
        for name, src in (("worker.py", WORKER_SRC), ("runner.py", RUNNER_SRC)):
            with self.subTest(file=name):
                self.assertFalse(imported_from_blobstore(src) & NEEDS_KEYS,
                                 "โค้ดฝั่ง worker ดึงของที่ต้องใช้กุญแจมาจาก blobstore")

    def test_it_reads_no_storage_credential_from_the_environment(self):
        for name, src in (("worker.py", WORKER_SRC), ("runner.py", RUNNER_SRC)):
            for var in ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_BUCKET", "S3_ENDPOINT"):
                with self.subTest(file=name, var=var):
                    self.assertNotIn(var, src)

    def test_the_download_path_uses_a_url_the_server_handed_it(self):
        """URL เต็มจากเซิร์ฟเวอร์ = presigned — และต้องไม่แนบ Authorization ของเราไปด้วย."""
        body = WORKER_SRC[WORKER_SRC.index("def download"):]
        body = body[:body.index("def ", 10)]
        self.assertIn('startswith(("http://", "https://"))', body)
        self.assertRegex(body, r"if not external:\s*\n\s*req\.add_header\(\s*[\"']Authorization")

    def test_the_upload_path_goes_straight_to_the_store(self):
        body = WORKER_SRC[WORKER_SRC.index("def upload"):]
        body = body[:body.index("def ", 10)]
        self.assertIn("presigned PUT", body + WORKER_SRC[:WORKER_SRC.index("def upload")][-400:])

    def test_blobstore_really_would_need_the_keys(self):
        """กันเทสต์ข้างบนกลายเป็นเทสต์เปล่า ถ้าวันหนึ่ง blobstore เลิกใช้กุญแจไปเอง."""
        for var in ("S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
            with self.subTest(var=var):
                self.assertIn(var, BLOBSTORE)


class TestTheRunbookStillMatchesTheCode(unittest.TestCase):

    def test_it_tells_the_owner_which_values_the_worker_actually_needs(self):
        self.assertIn("แยกไฟล์ตามหน้าที่", RUNBOOK)

    def test_it_names_the_storage_keys_as_removable(self):
        """ถ้าโค้ดไม่อ่าน แต่ runbook ไม่บอก เจ้าของก็ไม่มีทางรู้ว่าลบได้."""
        section = RUNBOOK[RUNBOOK.index("แยกไฟล์ตามหน้าที่"):]
        section = section[:section.index("\n- ", 10) if "\n- " in section[10:] else len(section)]
        self.assertRegex(section, r"S3|R2", "runbook ไม่ได้บอกว่ากุญแจที่เก็บไฟล์เอาออกได้")


if __name__ == "__main__":
    unittest.main()
