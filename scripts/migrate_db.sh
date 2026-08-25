#!/usr/bin/env bash
# ย้าย schema meeting_ai จาก Postgres หนึ่งไปอีกที่ (เช่น Neon → Supabase)
#
# วิธีใช้ (ใส่ connection string แบบ DIRECT ไม่ใช่ pooler สำหรับ dump/restore):
#   SRC_URL='postgresql://...neon-direct.../neondb?sslmode=require' \
#   DST_URL='postgresql://postgres:pw@db.xxx.supabase.co:5432/postgres' \
#   ./scripts/migrate_db.sh
#
# ทำอะไรบ้าง: dump เฉพาะ schema meeting_ai → restore เข้าปลายทาง → นับแถวเทียบ
set -euo pipefail

: "${SRC_URL:?ต้องตั้ง SRC_URL (Postgres ต้นทาง เช่น Neon direct)}"
: "${DST_URL:?ต้องตั้ง DST_URL (Postgres ปลายทาง เช่น Supabase direct)}"

STAMP=$(date +%Y%m%d_%H%M)
DUMP="backups/migrate_${STAMP}.sql"
mkdir -p backups

echo "1/3 📤 dump schema meeting_ai จากต้นทาง..."
pg_dump "$SRC_URL" --schema=meeting_ai --no-owner --no-privileges -f "$DUMP"
echo "    ได้ไฟล์: $DUMP ($(du -h "$DUMP" | cut -f1))"

echo "2/3 📥 restore เข้าปลายทาง..."
psql "$DST_URL" -v ON_ERROR_STOP=1 -f "$DUMP"

echo "3/3 🔍 นับแถวแต่ละตารางที่ปลายทาง..."
psql "$DST_URL" -At -c "
  select table_name || ': ' || (xpath('/row/c/text()',
    query_to_xml(format('select count(*) as c from meeting_ai.%I', table_name), false, true, '')))[1]::text
  from information_schema.tables where table_schema='meeting_ai' order by table_name;"

echo ""
echo "✅ ย้ายเสร็จ — เทียบจำนวนแถวกับต้นทางให้ตรง แล้วค่อยชี้ DATABASE_URL ไปปลายทาง"
