"""เลือกแบ็กเอนด์เก็บข้อมูล — ไฟล์ JSON (ในเครื่อง) หรือ Postgres (cloud).

โหมด cloud ต้องเปิดอย่างชัดเจน: `mai web --cloud` หรือตั้ง MEETING_AI_CLOUD=1
**ไม่ใช่** แค่มี DATABASE_URL — เพราะมี DATABASE_URL ไว้ทดสอบแล้วเผลอทำให้เครื่องมือ
ในเครื่องเปลี่ยนพฤติกรรม (โผล่หน้าล็อกอิน + มองไม่เห็นข้อมูลเดิมที่อยู่ในไฟล์ JSON)

server.py เรียก backend.store.* เหมือนกันทั้งสองโหมด — signature ของสองโมดูลตรงกัน
"""

from __future__ import annotations

import os

from . import db

CLOUD_ENV = "MEETING_AI_CLOUD"


def _want_cloud() -> bool:
    return (os.environ.get(CLOUD_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


if _want_cloud() and db.enabled():
    from . import pgstore as store  # noqa: F401
    cloud = True
else:
    from . import store  # noqa: F401
    cloud = False
    # เปิดโหมด cloud มาแต่ยังไม่มี DATABASE_URL — ต้องบอก ไม่ใช่เงียบแล้วตกไปใช้ไฟล์
    if _want_cloud():
        import sys
        print("⚠️  ขอโหมด cloud แต่ยังต่อฐานข้อมูลไม่ได้: "
              + "; ".join(db.missing_pieces()) + " — ใช้แบบไฟล์ไปก่อน", file=sys.stderr)


def storage():
    """ที่เก็บไฟล์เสียง — ดิสก์ หรือ S3/R2 ถ้าตั้ง S3_* ไว้."""
    from . import blobstore
    return blobstore.get_storage(store.WEB_DIR)


def mode() -> str:
    return "postgres" if cloud else "files"


def auth_required() -> bool:
    """โหมด cloud ต้องล็อกอิน — โหมดในเครื่องไม่ต้อง (ผูก 127.0.0.1 อยู่แล้ว)."""
    return cloud


def health() -> dict:
    info = {"mode": mode(), "auth": auth_required()}
    if cloud:
        info["db_missing"] = db.missing_pieces()
    return info
