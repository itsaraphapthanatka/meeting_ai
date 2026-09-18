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
SUMS="$MODEL_DIR/SHA256SUMS"             # ลายนิ้วมือของโมเดลที่รู้จัก (BACKLOG #22)

# เวอร์ชันของ whisper.cpp ที่จะ build — ปักหมุดไว้ ไม่ใช่ "master วันไหนก็ได้"
# ปักทั้ง tag และ commit: tag ถูกย้ายไปชี้ commit อื่นได้ ส่วน commit sha ย้ายไม่ได้
# อัปเกรด: เปลี่ยนสองค่านี้พร้อมกัน แล้วรันใหม่ (ลบ vendor/whisper.cpp ก่อนถ้าต้องการ build สะอาด)
WHISPER_REF="${WHISPER_REF:-v1.9.4}"
WHISPER_COMMIT="${WHISPER_COMMIT:-7d75b14994ae7f59623e2471445e2355fe506ed2}"

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
  echo "    clone $WHISPER_REF"
  git clone --depth 1 --branch "$WHISPER_REF" \
      https://github.com/ggml-org/whisper.cpp "$WHISPER_SRC"
else
  echo "    มี source อยู่แล้วที่ $WHISPER_SRC — ใช้ตัวเดิม"
fi

# ตรวจว่าที่ได้มาคือ commit ที่ปักหมุดไว้จริง — tag ถูกย้ายได้ การ clone ตาม tag เฉย ๆ
# จึงไม่ใช่การปักหมุด เราจึง build เฉพาะโค้ดชุดที่เคยถูกตรวจแล้วเท่านั้น
HEAD_SHA="$(git -C "$WHISPER_SRC" rev-parse HEAD)"
if [ "$HEAD_SHA" != "$WHISPER_COMMIT" ]; then
  echo "❌ source ของ whisper.cpp ไม่ตรงกับที่ปักหมุดไว้"
  echo "   ต้องการ: $WHISPER_COMMIT ($WHISPER_REF)"
  echo "   ได้:     $HEAD_SHA"
  echo "   ถ้าตั้งใจอัปเกรด แก้ WHISPER_REF/WHISPER_COMMIT ที่หัวสคริปต์"
  echo "   ถ้าเป็น source เก่าที่ค้างอยู่ ลบ $WHISPER_SRC แล้วรันใหม่"
  exit 1
fi
echo "    ✅ source ตรงกับที่ปักหมุด: $WHISPER_REF"

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

# ตรวจลายนิ้วมือของไฟล์ที่โหลดมา — ไฟล์โมเดลถูกโหลดจากอินเทอร์เน็ตแล้วเอาไปรันกับเสียง
# ประชุมจริง ถ้าระหว่างทางมีใครสลับไฟล์ได้ ก็เท่ากับรันโค้ด/โมเดลของคนอื่นโดยไม่รู้ตัว
verify() {  # $1 = path ของไฟล์
  local name got want
  name="$(basename "$1")"
  if [ ! -f "$SUMS" ]; then
    echo "    ⚠️  ไม่มี $SUMS — ข้ามการตรวจลายนิ้วมือของ $name"
    return 0
  fi
  want="$(awk -v n="$name" '$2 == n { print $1 }' "$SUMS")"
  if [ -z "$want" ]; then
    echo "    ⚠️  ยังไม่มีลายนิ้วมือของ $name ใน $SUMS — ไฟล์นี้ไม่ถูกตรวจ"
    echo "       เติมได้ด้วย: sha256sum \"$1\"  (เทียบกับที่ต้นทางประกาศก่อนเติม)"
    return 0
  fi
  got="$(sha256sum "$1" | cut -d" " -f1)"
  if [ "$got" != "$want" ]; then
    echo "❌ $name ไม่ตรงลายนิ้วมือที่บันทึกไว้ — ไฟล์เสียหายหรือถูกสลับระหว่างทาง"
    echo "   ต้องการ: $want"
    echo "   ได้:     $got"
    rm -f "$1"       # ลบทิ้ง ไม่ให้รอบหน้าเจอไฟล์แล้วคิดว่า "มีอยู่แล้ว"
    exit 1
  fi
  echo "    ✅ ลายนิ้วมือถูกต้อง: $name"
}

download() {  # $1 = ปลายทาง, $2 = url
  if [ -f "$1" ]; then
    echo "    มีอยู่แล้ว: $1"
  else
    echo "    กำลังโหลด: $(basename "$1")"
    curl -L --fail -o "$1" "$2"
  fi
  verify "$1"
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
