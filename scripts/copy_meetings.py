"""ย้ายการประชุมจากฐาน Postgres หนึ่งไปอีกฐาน โดยไม่แตะข้อมูลที่ปลายทางมีอยู่แล้ว.

ต่างจาก `scripts/migrate_db.sh` ซึ่ง dump/restore ทั้ง schema — อันนั้นใช้ตอนย้ายฐานทั้งใบ
ไปที่ว่าง ๆ ส่วนอันนี้ใช้ตอน **ปลายทางมีข้อมูลจริงอยู่แล้ว** และอยากเอาของจากฐานเก่ามารวม

ทำไมต้องมี (BACKLOG #42/#53/#54): `.env` ของเครื่องเจ้าของชี้ไปฐาน Neon เก่าที่มีการประชุม
13 รายการ ส่วน Vercel ตั้ง `DATABASE_URL` เป็น **sensitive** ซึ่งอ่านค่ากลับไม่ได้เลยแม้แต่
ด้วย `VERCEL_TOKEN` (ตรวจแล้ว 2026-09-20 — ทุกตัวแปรของโปรเจกต์นี้เป็น sensitive หมด)
สคริปต์จึงรับสองค่าจาก **environment ของคนรัน** เอง ไม่มีค่าลับผ่านมือใคร

    # ต้นทางไม่ต้องใส่ถ้าเป็นฐานที่ .env ชี้อยู่แล้ว ใส่แต่ปลายทางพอ
    DST_DATABASE_URL='...ฐาน production...' python scripts/copy_meetings.py

    SRC_DATABASE_URL='...ฐานเก่า...' DST_DATABASE_URL='...ฐาน production...' \
        python scripts/copy_meetings.py                       # ดูอย่างเดียว (ค่าเริ่มต้น)

    # กันชี้ผิดฐาน: ถ้าปลายทางไม่ได้มี 8 รายการพอดี ให้หยุดทันที (ใช้ได้ทั้ง dry-run และ apply)
    ... --expect-dst-meetings 8

    SRC_DATABASE_URL=... DST_DATABASE_URL=... \
        python scripts/copy_meetings.py --owner you@example.com --apply

กติกาที่เขียนไว้ตั้งใจ:

* **ค่าเริ่มต้นคือ dry-run** ต้องพิมพ์ `--apply` เองถึงจะเขียนจริง
* **ต้องระบุ `--owner` เป็นอีเมลของผู้ใช้ที่ *ปลายทาง*** — id ของผู้ใช้เป็นคนละชุดกันสองฐาน
  ถ้าคัดลอก `owner_id` เดิมมาตรง ๆ จะชน foreign key หรือกลายเป็นแถวไม่มีเจ้าของ ซึ่งตั้งแต่
  BACKLOG #42 แปลว่า **ไม่มีใครเปิดได้เลย**
* **ข้ามแถวที่ id ซ้ำ** ไม่เขียนทับของที่ปลายทางมีอยู่ (`on conflict do nothing`)
* **ไฟล์เสียงไม่ได้ถูกย้าย** — ตารางเก็บแค่ `audio_key` ถ้าสองฐานใช้คนละบัคเก็ต R2
  เสียงจะเปิดไม่ได้ สคริปต์พิมพ์รายการ key ให้เอาไปคัดลอกเอง
* คอลัมน์ที่ปลายทางยังไม่มี (`peaks`, `action_items`, `qa` ถ้ายังไม่ได้รัน `db-init`)
  จะถูกข้ามโดยอัตโนมัติ ไม่ใช่ทำให้ทั้งงานล้ม
* **`--expect-dst-meetings N` คือด่านกันชี้ผิดฐาน** — ฐานเก่ากับ production ชื่อเหมือนกัน
  ทั้งคู่ (`neondb`) ดูจากชื่อไม่ออก ต้องนับแถวเอา · ล้มทั้งใน dry-run ด้วย
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from meeting_ai.config import _load_dotenv  # noqa: E402

_load_dotenv(ROOT / ".env")

import psycopg  # noqa: E402

# คอลัมน์ที่คัดลอก — ไม่รวม owner_id (ตั้งใหม่จาก --owner) และไม่รวม search_text
# ซึ่งปลายทางสร้างใหม่เองได้จาก title/summary/segments
BASE_COLS = (
    "id", "title", "visibility", "language", "duration", "segment_count", "source",
    "template", "speakers", "summary", "summary_error", "edited", "transcript_edited",
    "segments", "translations", "audio_key", "created_at", "updated_at",
)
OPTIONAL_COLS = ("peaks", "action_items", "qa")


def _cols(conn) -> set[str]:
    rows = conn.execute(
        """select column_name from information_schema.columns
           where table_schema = 'meeting_ai' and table_name = 'meetings'"""
    ).fetchall()
    return {r[0] for r in rows}


def _users(conn) -> list[tuple[str, str]]:
    return [(str(r[0]), r[1]) for r in conn.execute(
        "select id, email from meeting_ai.users order by created_at").fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser(description="ย้ายการประชุมข้ามฐาน Postgres")
    ap.add_argument("--owner", help="อีเมลของผู้ใช้ที่ปลายทาง ที่จะเป็นเจ้าของทุกแถวที่ย้ายมา")
    ap.add_argument("--apply", action="store_true", help="เขียนจริง (ไม่ใส่ = ดูอย่างเดียว)")
    ap.add_argument("--only", help="ย้ายเฉพาะ id นี้ (ใส่ได้หลายตัว คั่นด้วยจุลภาค)")
    ap.add_argument("--expect-dst-meetings", type=int, metavar="N",
                    help="หยุดถ้าปลายทางไม่ได้มีการประชุม N รายการพอดี "
                         "— ด่านกันชี้ผิดฐาน")
    args = ap.parse_args()

    # ต้นทางปกติคือฐานที่ `.env` ของเครื่องนี้ชี้อยู่แล้ว — ไม่ต้องให้คนไปเปิดไฟล์คัดลอกค่า
    # ออกมาเอง (ยิ่งคัดลอกไปมา ยิ่งมีโอกาสไปโผล่ในที่ที่ไม่ควร) ตั้ง SRC_DATABASE_URL เองได้
    # ถ้าต้นทางไม่ใช่ฐานนั้น
    src_url = os.environ.get("SRC_DATABASE_URL") or os.environ.get("DATABASE_URL")
    src_from_env_file = not os.environ.get("SRC_DATABASE_URL") and bool(src_url)
    dst_url = os.environ.get("DST_DATABASE_URL")
    if not src_url:
        print("ไม่มีต้นทาง — ตั้ง SRC_DATABASE_URL หรือให้ .env มี DATABASE_URL")
        return 2
    if not dst_url:
        print("ต้องตั้ง DST_DATABASE_URL ใน environment "
              "(ไม่รับเป็น argument เพื่อไม่ให้ค่าลับไปอยู่ใน shell history)")
        return 2
    if src_from_env_file:
        print("ต้นทาง: ใช้ DATABASE_URL จาก .env ของเครื่องนี้")

    wanted = {s.strip() for s in (args.only or "").split(",") if s.strip()}

    with psycopg.connect(src_url, prepare_threshold=None) as src, \
            psycopg.connect(dst_url, prepare_threshold=None) as dst:
        src_cols, dst_cols = _cols(src), _cols(dst)
        cols = [c for c in BASE_COLS if c in src_cols and c in dst_cols]
        extra = [c for c in OPTIONAL_COLS if c in src_cols and c in dst_cols]
        skipped_cols = [c for c in OPTIONAL_COLS if c not in dst_cols]

        print(f"ต้นทาง: {src.execute('select current_database()').fetchone()[0]} "
              f"· ปลายทาง: {dst.execute('select current_database()').fetchone()[0]}")

        # ด่านกันชี้ผิดฐาน — เกิดขึ้นจริงมาแล้ว 2026-09-20: `.env` ของเครื่องเจ้าของชี้ไป
        # ฐาน Neon เก่าที่เลิกใช้แล้ว ส่วน production ใช้อีกฐาน กว่าจะรู้ก็หลังรัน `db-init`
        # ไปสองรอบ · ชื่อฐานเหมือนกันทั้งคู่ (`neondb`) จึงดูจากชื่อไม่ออก ต้องนับแถวเอา
        # ตรวจก่อนทุกอย่าง และ **ล้มทั้งใน dry-run ด้วย** ไม่งั้นคนอ่านรายงานผิดฐานไปทั้งหน้า
        if args.expect_dst_meetings is not None:
            have_n = dst.execute(
                "select count(*) from meeting_ai.meetings").fetchone()[0]
            if have_n != args.expect_dst_meetings:
                print(f"{chr(10)}❌ ปลายทางมีการประชุม {have_n} รายการ "
                      f"แต่สั่งไว้ว่าต้องเป็น {args.expect_dst_meetings} — หยุดก่อน "
                      "น่าจะชี้ผิดฐาน")
                return 1
            print(f"✅ ปลายทางมี {have_n} รายการ ตรงกับที่คาดไว้")
        print("ผู้ใช้ที่ต้นทาง:", ", ".join(e for _, e in _users(src)) or "(ไม่มี)")
        print("ผู้ใช้ที่ปลายทาง:", ", ".join(e for _, e in _users(dst)) or "(ไม่มี)")
        if skipped_cols:
            print("ปลายทางยังไม่มีคอลัมน์:", ", ".join(skipped_cols),
                  "— ข้ามไป (รัน `mai db-init` ก่อนถ้าต้องการ)")

        rows = src.execute(
            f"select {', '.join(cols + extra)} from meeting_ai.meetings order by created_at"
        ).fetchall()
        if wanted:
            rows = [r for r in rows if r[0] in wanted]
        have = {r[0] for r in dst.execute("select id from meeting_ai.meetings").fetchall()}
        todo = [r for r in rows if r[0] not in have]
        dup = [r[0] for r in rows if r[0] in have]

        print(f"\nที่ต้นทาง {len(rows)} แถว · ปลายทางมีอยู่แล้ว {len(dup)} · จะย้าย {len(todo)}")
        for r in todo:
            print("  ", r[0], "|", (r[1] or "")[:40])
        if dup:
            print("  ข้ามเพราะ id ซ้ำ:", ", ".join(dup))

        keys = [r[cols.index("audio_key")] for r in todo if "audio_key" in cols]
        if keys:
            print("\n⚠️ ไฟล์เสียงไม่ได้ถูกย้ายไปด้วย — ถ้าสองฐานใช้คนละบัคเก็ต ต้องคัดลอกเอง:")
            for k in keys:
                print("    ", k)

        if not args.owner:
            print("\nยังไม่ได้ระบุ --owner จึงหยุดแค่รายงาน "
                  "(id ของผู้ใช้เป็นคนละชุดกันสองฐาน ต้องบอกว่าใครเป็นเจ้าของที่ปลายทาง)")
            return 0
        owner = next((i for i, e in _users(dst) if e == args.owner), None)
        if owner is None:
            print(f"\nไม่พบผู้ใช้ {args.owner} ที่ปลายทาง — ตรวจอีเมลอีกครั้ง")
            return 1

        if not args.apply:
            print(f"\n(dry-run) จะย้าย {len(todo)} แถว ให้ {args.owner} เป็นเจ้าของ "
                  "— ใส่ --apply เพื่อเขียนจริง")
            return 0

        all_cols = cols + extra
        placeholders = ", ".join(["%s"] * (len(all_cols) + 1))
        sql = (f"insert into meeting_ai.meetings ({', '.join(all_cols)}, owner_id) "
               f"values ({placeholders}) on conflict (id) do nothing")
        done = 0
        for r in todo:
            dst.execute(sql, (*r, owner))
            done += 1
        # search_text สร้างใหม่จากของที่เพิ่งใส่ ไม่คัดลอกมาเพราะอาจค้างของเก่า
        dst.execute(
            """update meeting_ai.meetings set
                 search_text = concat_ws(E'\\n', title, summary,
                   (select coalesce(string_agg(seg->>'text', ' '), '')
                      from jsonb_array_elements(segments) seg))
               where owner_id = %s and (search_text is null or search_text = '')""",
            (owner,))
        print(f"\n✅ ย้ายแล้ว {done} แถว ให้ {args.owner}")
        print("   ตรวจซ้ำด้วย `mai db-check` และเปิดหน้าเว็บดูว่าเสียงเล่นได้ไหม")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
