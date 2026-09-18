"""เลือกแบ็กเอนด์เก็บข้อมูล — ไฟล์ JSON (ในเครื่อง) หรือ Postgres (cloud).

โหมด cloud ต้องเปิดอย่างชัดเจน: `mai web --cloud` หรือตั้ง MEETING_AI_CLOUD=1
**ไม่ใช่** แค่มี DATABASE_URL — เพราะมี DATABASE_URL ไว้ทดสอบแล้วเผลอทำให้เครื่องมือ
ในเครื่องเปลี่ยนพฤติกรรม (โผล่หน้าล็อกอิน + มองไม่เห็นข้อมูลเดิมที่อยู่ในไฟล์ JSON)

server.py เรียก backend.store.* เหมือนกันทั้งสองโหมด — signature ของสองโมดูลตรงกัน
"""

from __future__ import annotations

import os

from . import db
from .. import log as _log

log = _log.get(__name__)

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
        log.warning('⚠️  ขอโหมด cloud แต่ยังต่อฐานข้อมูลไม่ได้: ' + '; '.join(db.missing_pieces()) + ' — ใช้แบบไฟล์ไปก่อน')


def storage():
    """ที่เก็บไฟล์เสียง — ดิสก์ หรือ S3/R2

    แยกจากที่เก็บข้อมูลโดยตั้งใจ (cloud DB + ดิสก์ในเครื่อง ก็เป็นการตั้งค่าที่ถูก)
    แต่ของจริงต้องมีคนสั่ง: โหมด cloud ใช้ S3 เป็นค่าเริ่มต้น ส่วนโหมดไฟล์ต้องตั้ง
    MEETING_AI_REMOTE_BLOBS=1 เอง — ไม่งั้นแค่มี S3_* ใน .env ก็ต่อบัคเก็ต production
    ให้เองเงียบ ๆ (BUG-045)

    ส่ง `cloud` (= "ขอโหมด cloud **และ** ต่อ DB ได้") ไม่ใช่ `_want_cloud()` โดยตั้งใจ:
    ถ้า MEETING_AI_CLOUD=1 แต่ DATABASE_URL หาย/พิมพ์ผิด แปลว่า deployment นั้นตั้งค่า
    ไม่ครบอยู่แล้ว — เลือกไม่แตะบัคเก็ตของจริงดีกว่า (พังแบบอ่านง่ายกว่าพังแบบเขียนข้อมูล
    ไปคนละที่) ผลคือฝั่ง serverless จะเห็น error ตอนอัปโหลดแทน ซึ่ง backend.py แจ้งไว้
    ตั้งแต่ตอน import อยู่แล้วว่าต่อ DB ไม่ได้
    """
    from . import blobstore
    return blobstore.get_storage(store.WEB_DIR, allow_remote=cloud)


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
