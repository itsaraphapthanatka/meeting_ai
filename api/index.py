"""จุดเข้าของ Vercel — ใช้ handler เดียวกับ `mai web` ทุกประการ.

Vercel Python runtime รับคลาสชื่อ `handler` ที่สืบจาก BaseHTTPRequestHandler
ซึ่งตรงกับที่ meeting_ai.web.server เขียนไว้อยู่แล้ว จึงไม่ต้องมีโค้ดสองชุด

env ที่ต้องตั้งบน Vercel:
    MEETING_AI_CLOUD=1        เปิดโหมด Postgres + ล็อกอิน
    REMOTE_WORKER=1           งานหนักรอ `mai worker` บนเครื่องที่มี GPU
    WORKER_TOKEN=...          ต้องตรงกับฝั่ง worker
    DATABASE_URL=...          Neon (ใช้ host ที่มี -pooler)
    LLM_API_KEY=...           ถ้าจะให้ฝั่ง cloud สรุปเองได้ด้วย
    S3_ENDPOINT / S3_BUCKET / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY   (Cloudflare R2)
"""

import os
import sys
from pathlib import Path

# โค้ดหลักอยู่โฟลเดอร์บนสุดของโปรเจกต์ ไม่ได้อยู่ใน api/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ต้องตั้งก่อน import backend เพราะมันเลือกแบ็กเอนด์ตอน import
os.environ.setdefault("MEETING_AI_CLOUD", "1")

from meeting_ai.web.server import Handler  # noqa: E402


class handler(Handler):
    """Vercel มองหาชื่อนี้."""

    def _host_ok(self) -> bool:
        # บน Vercel โดเมนเป็นของ deployment เอง (*.vercel.app หรือโดเมนที่ผูกไว้)
        # การกัน DNS rebinding แบบเทียบ host ใช้ไม่ได้ที่นี่ — Vercel จัดการ routing ให้แล้ว
        return True
