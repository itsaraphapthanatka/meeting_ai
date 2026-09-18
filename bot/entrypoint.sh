#!/usr/bin/env bash
# เตรียมเสียงเสมือน + จอเสมือน แล้วรันบอท
#   MODE=login  → เปิด VNC ให้ล็อกอิน Google ครั้งเดียว (profile เก็บถาวรที่ /prof)
#   ไม่ตั้ง MODE → เข้าห้องประชุม + อัดเสียง (ใช้ profile ที่ล็อกอินไว้)
set -e

# 0) ตรวจสิทธิ์เขียนก่อนทำอะไรทั้งสิ้น (BACKLOG #21)
# ตั้งแต่ container ไม่ได้รันเป็น root แล้ว โฟลเดอร์ที่ mount มาจาก host อาจเขียนไม่ได้
# ถ้าไม่ตรวจ อาการจะเป็น "บอทเข้าห้อง นั่งจนจบ แล้วไม่มีไฟล์เสียง" ซึ่งไล่ยากมาก
# — ล้มตรงนี้พร้อมบอกวิธีแก้ ดีกว่าเสียการประชุมทั้งห้องไปหนึ่งครั้ง
for d in /out /prof; do
    [ -d "$d" ] || continue
    if ! touch "$d/.mai-write-test" 2>/dev/null; then
        echo "[entrypoint] ❌ เขียน $d ไม่ได้ (รันในนามผู้ใช้ $(id -u):$(id -g))" >&2
        echo "[entrypoint]    โฟลเดอร์ที่ mount มาจากเครื่อง host ต้องให้ผู้ใช้นี้เขียนได้" >&2
        echo "[entrypoint]    บน Linux: chown -R 1000:1000 <โฟลเดอร์นั้น>" >&2
        echo "[entrypoint]    หรือสั่ง docker run ด้วย --user \$(id -u):\$(id -g)" >&2
        exit 1
    fi
    rm -f "$d/.mai-write-test"
done

# 1) เสียงเสมือน (null-sink ชื่อ meet) — เสียง Chromium ไหลเข้ามาให้ ffmpeg อัดจาก meet.monitor
pulseaudio -D --exit-idle-time=-1 --disable-shm=1 2>/dev/null || true
for i in $(seq 1 10); do pactl info >/dev/null 2>&1 && break; sleep 0.5; done
pactl load-module module-null-sink sink_name=meet sink_properties=device.description=meet >/dev/null
pactl set-default-sink meet

# ไมค์เสมือน (เงียบ) — ต้องมีอุปกรณ์อินพุตจริงในระบบ ไม่งั้นโปรแกรมประชุมค้างรอไมค์
# ห้ามใช้ --use-fake-device-for-media-stream ของ Chromium แทน เพราะมันสร้าง
# ลำโพงปลอมด้วย แล้วโปรแกรมประชุมจะเล่นเสียงลงลำโพงปลอมนั้น ไม่ลง sink meet
# ที่ ffmpeg อัดอยู่ ผลคือได้ไฟล์เงียบทั้งไฟล์
pactl load-module module-null-sink sink_name=micsink sink_properties=device.description=micsink >/dev/null
pactl load-module module-remap-source source_name=virtmic master=micsink.monitor source_properties=device.description=virtmic >/dev/null
pactl set-default-source virtmic

# 2) จอเสมือน
Xvfb :99 -screen 0 1280x720x24 >/dev/null 2>&1 &
export DISPLAY=:99
sleep 1

if [ "$MODE" = "login" ]; then
    # โหมดล็อกอิน: มี window manager + VNC ให้ผู้ใช้เข้ามาคลิกล็อกอินได้จริง
    fluxbox >/dev/null 2>&1 &
    # ต้องมีรหัสผ่าน (BACKLOG #21): จอนี้คือเบราว์เซอร์ที่กำลังล็อกอินบัญชี Google ของเจ้าของ
    # -nopw แปลว่าใครที่ต่อพอร์ตนี้ได้ก็เห็นและ "คลิกแทน" ได้ทันที ต่อให้ผูกไว้ที่ 127.0.0.1
    # ก็ยังหมายถึงทุกโพรเซส/ทุกผู้ใช้บนเครื่องนั้น ซึ่งเครื่อง worker เป็นเครื่องที่แชร์กัน
    # VNC_PASSWORD ถูกสุ่มและส่งมาจากฝั่ง host (bot.login) แล้วแสดงให้ผู้ใช้เห็นตอนสั่ง
    if [ -z "${VNC_PASSWORD:-}" ]; then
        echo "[entrypoint] ❌ ไม่ได้รับ VNC_PASSWORD — ไม่เปิดจอให้ดูโดยไม่มีรหัส" >&2
        exit 1
    fi
    mkdir -p "$HOME/.vnc"
    x11vnc -storepasswd "$VNC_PASSWORD" "$HOME/.vnc/passwd" >/dev/null 2>&1
    unset VNC_PASSWORD
    x11vnc -display :99 -forever -shared -rfbauth "$HOME/.vnc/passwd" \
           -rfbport 5900 -bg -quiet >/dev/null 2>&1
    # เปิดทางที่สองผ่านเบราว์เซอร์ (noVNC) — ไม่ต้องลงโปรแกรม VNC บนเครื่อง host
    # ยังเปิด 5900 ไว้ให้คนที่อยากใช้ client จริงด้วย
    websockify -D --web=/usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 || \
        echo "[entrypoint] เปิด noVNC ไม่สำเร็จ — ยังใช้ VNC client ต่อ localhost:5900 ได้"
    exec python3 /app/login.py
fi

# โหมดปกติ: ก็อปโปรไฟล์ที่ล็อกอินไว้มาเป็นสำเนาของ container นี้ก่อน
# Chromium ล็อก user-data-dir ได้ตัวเดียว ถ้าหลายบอททำงานพร้อมกันแล้วชี้ /prof ตัวเดียวกัน
# ตัวที่สองจะเปิดโปรไฟล์ไม่ได้ — สำเนาทำให้ประชุมพร้อมกันหลายห้องได้ และ session ที่
# ล็อกอินไว้ (ซึ่งอยู่ใน /prof) ไม่ถูกเขียนทับด้วย
# passcode ของห้องประชุมมาทางไฟล์ ไม่ใช่ตัวแปรสภาพแวดล้อม (BACKLOG #21)
# `docker inspect` แสดง env ทั้งหมดให้ทุกคนที่อยู่ในกลุ่ม docker บนเครื่องนั้นเห็น
# และมันติดอยู่กับ container ไปตลอดอายุ ไม่ใช่แค่ตอนสั่ง
if [ -n "${PASSCODE_FILE:-}" ] && [ -f "$PASSCODE_FILE" ]; then
    PASSCODE="$(cat "$PASSCODE_FILE")"
    export PASSCODE
    rm -f "$PASSCODE_FILE"      # ใช้ครั้งเดียว ไม่ต้องค้างอยู่ในโฟลเดอร์ที่ mount ร่วมกับ host
fi

export PROFILE_DIR=/profwork
mkdir -p "$PROFILE_DIR"
cp -a /prof/. "$PROFILE_DIR"/ 2>/dev/null || true

# python เป็น PID 1 (docker stop → SIGTERM ถึง python → ปิดอัดสุภาพ)
exec python3 /app/join_meeting.py
