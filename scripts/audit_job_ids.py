"""ตรวจหา job id ที่การ์ดของ BUG-044 จะปฏิเสธ (BACKLOG #49) — อ่านอย่างเดียว ไม่แก้ข้อมูล

ใช้แทน psql ซึ่งไม่ได้ติดตั้งบนเครื่องเจ้าของ อ่าน DATABASE_URL จาก .env เอง
(ไม่พิมพ์ค่าออกมา) แล้วกรองด้วย jobs.safe_job_id ตัวจริงของแอป ไม่ใช่ regex ที่เขียนซ้ำ
ซึ่งอาจเพี้ยนจากของจริง

    python scripts/audit_job_ids.py            # รายงานอย่างเดียว
    python scripts/audit_job_ids.py --sql      # พิมพ์ UPDATE ให้เอาไปรันเองถ้าต้องการ
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from meeting_ai.config import _load_dotenv  # noqa: E402

_load_dotenv(ROOT / ".env")

url = os.environ.get("DATABASE_URL", "").strip()
if not url:
    sys.exit("ไม่พบ DATABASE_URL ใน .env หรือ environment")

import psycopg  # noqa: E402

from meeting_ai.web import jobs  # noqa: E402

# Neon/Supabase pooler อยู่ใน transaction mode -> prepare_threshold=None (ดู PROJECT-CONTEXT)
with psycopg.connect(url, prepare_threshold=None) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "select id, status, attempts, created from meeting_ai.jobs order by created desc"
        )
        rows = cur.fetchall()

bad = [r for r in rows if not jobs.safe_job_id(r[0])]

print(f"job ทั้งหมด: {len(rows)}")
print(f"id ที่การ์ดใหม่จะปฏิเสธ: {len(bad)}")

if not bad:
    print("\nไม่พบแถวพิษ — deploy แพตช์ BUG-044 ได้เลย")
    raise SystemExit(0)

print()
stuck = 0
for job_id, status, attempts, created in bad:
    # แสดงแบบ repr เพื่อให้เห็นอักขระที่มองไม่เห็น เช่น newline / NUL
    flag = ""
    if status in ("queued", "running"):
        stuck += 1
        flag = "  <-- ยังอยู่ในคิว จะถูกหยิบไปทำอีกครั้งแล้วจบเป็น error"
    print(f"  {status:8} attempts={attempts:<3} {created:%Y-%m-%d %H:%M}  {job_id!r}{flag}")

print(f"\nที่ยังค้างคิว: {stuck} แถว")
print("หลัง deploy แพตช์ แถวเหล่านี้จะถูก claim ครั้งสุดท้ายแล้วกลายเป็น status=error เอง")
print("ไม่วนเรียก LLM ซ้ำอีก — ไม่จำเป็นต้องแก้ข้อมูลด้วยมือ")

if "--sql" in sys.argv:
    print("\n-- ถ้าต้องการปิดงานพวกนี้ทันทีโดยไม่รอรอบ claim (รันเองบน production):")
    print("update meeting_ai.jobs set status = 'error',")
    print("       error = 'id ของงานนี้ไม่ปลอดภัย จึงประมวลผลต่อไม่ได้ (BUG-044)'")
    print(" where status in ('queued', 'running')")
    print("   and id !~ '^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}(\\.[A-Za-z0-9._-]*)?$';")
