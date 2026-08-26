#!/usr/bin/env bash
# ติดตั้ง dependency, build whisper.cpp และดาวน์โหลดโมเดล whisper สำหรับ meeting_ai บน Ubuntu/Debian
# รองรับทั้ง x86_64 และ ARM (aarch64) — ตรวจ GPU NVIDIA อัตโนมัติ ถ้ามีจะ build แบบ CUDA ให้เอง
#
# วิธีใช้:
#   ./setup-ubuntu.sh                     # โมเดล default = large-v3-turbo-q5_0 (เร็ว+เบา แนะนำ)
#   ./setup-ubuntu.sh large-v3            # เน้นแม่นสุด (~3GB) เหมาะกับเครื่องที่มี GPU/RAM เยอะ
#   WHISPER_CUDA=0 ./setup-ubuntu.sh      # บังคับ build แบบ CPU ล้วน (ข้ามการตรวจ GPU)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

MODEL="${1:-large-v3-turbo-q5_0}"        # turbo-q5 = คุ้มสุด · large-v3 = แม่นสุดแต่ช้า
WHISPER_SRC="$DIR/vendor/whisper.cpp"    # ที่เก็บ source + binary ที่ build เอง
MODEL_DIR="$DIR/models"

# ---------- 1/5 ตรวจและติดตั้ง system dependency ----------
echo "==> 1/5 ติดตั้ง system dependency (ffmpeg, cmake, build tools, git)"
if ! command -v apt-get >/dev/null 2>&1; then
  echo "❌ สคริปต์นี้ใช้ apt (Ubuntu/Debian) — ระบบอื่นให้ติดตั้ง ffmpeg/cmake/gcc/git เองแล้วข้ามขั้นนี้"
  exit 1
fi
NEED=()
for pkg in ffmpeg git curl cmake build-essential python3; do
  dpkg -s "$pkg" >/dev/null 2>&1 || NEED+=("$pkg")
done
if [ "${#NEED[@]}" -gt 0 ]; then
  echo "    จะติดตั้ง: ${NEED[*]}  (ต้องใช้ sudo)"
  sudo apt-get update
  sudo apt-get install -y "${NEED[@]}"
else
  echo "    ครบแล้ว — ข้าม"
fi

# ---------- 2/5 ตรวจ GPU / CUDA ----------
echo "==> 2/5 ตรวจ GPU NVIDIA + CUDA toolkit"
USE_CUDA=0
CUDA_ARCH=""
if [ "${WHISPER_CUDA:-1}" = "1" ] \
   && command -v nvidia-smi >/dev/null 2>&1 \
   && command -v nvcc >/dev/null 2>&1; then
  USE_CUDA=1
  GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || true)"
  # compute_cap เช่น "12.1" -> "121" สำหรับ -DCMAKE_CUDA_ARCHITECTURES
  CC="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -1 | tr -d ' ' || true)"
  if [ -n "$CC" ]; then CUDA_ARCH="${CC//./}"; fi
  echo "    พบ GPU: ${GPU_NAME:-unknown}  (compute cap ${CC:-?}) → จะ build แบบ CUDA"
else
  echo "    ไม่พบ GPU/CUDA (หรือถูกปิดด้วย WHISPER_CUDA=0) → จะ build แบบ CPU ล้วน"
fi

# ---------- 3/5 clone + build whisper.cpp ----------
echo "==> 3/5 clone + build whisper.cpp ($(uname -m))"
mkdir -p "$DIR/vendor"
if [ ! -d "$WHISPER_SRC/.git" ]; then
  git clone --depth 1 https://github.com/ggml-org/whisper.cpp "$WHISPER_SRC"
else
  echo "    มี source อยู่แล้วที่ $WHISPER_SRC — ใช้ตัวเดิม (git pull ถ้าอยากอัปเดต)"
fi

CMAKE_ARGS=(-B "$WHISPER_SRC/build" -DCMAKE_BUILD_TYPE=Release)
if [ "$USE_CUDA" = "1" ]; then
  CMAKE_ARGS+=(-DGGML_CUDA=1)
  [ -n "$CUDA_ARCH" ] && CMAKE_ARGS+=(-DCMAKE_CUDA_ARCHITECTURES="$CUDA_ARCH")
fi
cmake -S "$WHISPER_SRC" "${CMAKE_ARGS[@]}"
cmake --build "$WHISPER_SRC/build" -j"$(nproc)" --config Release

WHISPER_BIN="$WHISPER_SRC/build/bin/whisper-cli"
if [ ! -x "$WHISPER_BIN" ]; then
  echo "❌ build ไม่สำเร็จ — ไม่พบ $WHISPER_BIN"
  exit 1
fi
echo "    ✅ ได้ไบนารี: $WHISPER_BIN"

# ---------- 4/5 ดาวน์โหลดโมเดล ----------
echo "==> 4/5 ดาวน์โหลดโมเดล whisper + VAD"
mkdir -p "$MODEL_DIR"

download() {  # $1 = ปลายทาง, $2 = url
  if [ -f "$1" ]; then
    echo "    มีอยู่แล้ว: $1"
  else
    echo "    กำลังโหลด: $(basename "$1")"
    curl -L --fail -o "$1" "$2"
  fi
}

WHISPER_MODEL_FILE="$MODEL_DIR/ggml-$MODEL.bin"
download "$WHISPER_MODEL_FILE" \
  "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$MODEL.bin"

VAD_MODEL_FILE="$MODEL_DIR/ggml-silero-v5.1.2.bin"
download "$VAD_MODEL_FILE" \
  "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"

# ---------- 5/5 ตั้งค่า .env ----------
echo "==> 5/5 ตั้งค่า .env"
[ -f "$DIR/.env" ] || cp "$DIR/.env.example" "$DIR/.env"

# ตั้งค่า key=value ใน .env (แทนที่ถ้ามีอยู่ ไม่งั้น append)
set_env() {  # $1 = key, $2 = value
  local key="$1" val="$2"
  if grep -qE "^${key}=" "$DIR/.env"; then
    # ใช้ | เป็น delimiter กัน / ใน path ทำ sed พัง
    sed -i "s|^${key}=.*|${key}=${val}|" "$DIR/.env"
  else
    printf '%s=%s\n' "$key" "$val" >> "$DIR/.env"
  fi
}

set_env WHISPER_BIN     "$WHISPER_BIN"
set_env WHISPER_MODEL   "models/ggml-$MODEL.bin"
set_env VAD_MODEL       "models/ggml-silero-v5.1.2.bin"
set_env WHISPER_THREADS "$(nproc)"

echo ""
echo "✅ ติดตั้งเสร็จ!"
echo "   whisper-cli : $WHISPER_BIN   ($([ "$USE_CUDA" = 1 ] && echo "CUDA/GPU" || echo "CPU"))"
echo "   โมเดล        : $WHISPER_MODEL_FILE"
echo "   VAD          : $VAD_MODEL_FILE"
echo "   threads      : $(nproc)"
echo ""
echo "   ⚠️  อย่าลืมใส่ LLM_API_KEY ใน .env ก่อนใช้คำสั่งสรุป"
echo "   ต่อไป: หา index อุปกรณ์เสียง  ./mai devices"
echo "          เปิดหน้าเว็บ           ./mai web"
echo "          ทดสอบสรุปไฟล์         ./mai process path/to/meeting.mp3"
