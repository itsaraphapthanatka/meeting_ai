#!/usr/bin/env bash
# ติดตั้ง dependency และดาวน์โหลดโมเดล whisper สำหรับ meeting_ai
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

echo "==> 1/4 ตรวจ Homebrew"
if ! command -v brew >/dev/null 2>&1; then
  echo "❌ ไม่พบ Homebrew — ติดตั้งก่อนที่ https://brew.sh" ; exit 1
fi

echo "==> 2/4 ติดตั้ง ffmpeg + whisper-cpp"
brew list ffmpeg >/dev/null 2>&1     || brew install ffmpeg
brew list whisper-cpp >/dev/null 2>&1 || brew install whisper-cpp

echo "==> 3/4 (ทางเลือก) ติดตั้ง BlackHole สำหรับอัดเสียงระบบ Meet/Teams"
if ! brew list blackhole-2ch >/dev/null 2>&1; then
  if [ -t 0 ]; then
    read -r -p "    ติดตั้ง BlackHole ตอนนี้เลยไหม? (ต้องใช้ตอนอัดสด) [y/N] " ans || ans="n"
    [[ "$ans" =~ ^[Yy]$ ]] && brew install blackhole-2ch || echo "    ข้าม — อัปโหลดไฟล์ยังใช้ได้ปกติ"
  else
    echo "    (non-interactive) ข้าม BlackHole — ติดตั้งภายหลังด้วย: brew install blackhole-2ch"
  fi
fi

echo "==> 4/4 ดาวน์โหลดโมเดล whisper"
MODEL_DIR="$DIR/models"
mkdir -p "$MODEL_DIR"
MODEL="${1:-large-v3-turbo-q5_0}"   # turbo = เร็วสุดคุ้มสุด; ใช้ large-v3 ถ้าเน้นแม่นสุด
MODEL_FILE="$MODEL_DIR/ggml-$MODEL.bin"
if [ -f "$MODEL_FILE" ]; then
  echo "    มีโมเดลอยู่แล้ว: $MODEL_FILE"
else
  URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"
  echo "    กำลังโหลด $MODEL ... (large-v3 ~3GB, medium ~1.5GB)"
  curl -L --fail -o "$MODEL_FILE" "$URL"
fi

# ปรับ .env ให้ชี้โมเดลที่โหลดจริง (ถ้าไม่ใช่ค่า default)
if [ ! -f "$DIR/.env" ]; then cp "$DIR/.env.example" "$DIR/.env"; fi

echo ""
echo "✅ ติดตั้งเสร็จ!"
echo "   โมเดล: $MODEL_FILE"
echo "   ต่อไป: หา index อุปกรณ์เสียงด้วย  ./mai devices"
echo "   ทดสอบสรุปไฟล์:  ./mai process path/to/meeting.mp3"
