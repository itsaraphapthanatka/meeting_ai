#!/usr/bin/env bash
# เตรียมเสียงเสมือน + จอเสมือน แล้วรันบอท
#   MODE=login  → เปิด VNC ให้ล็อกอิน Google ครั้งเดียว (profile เก็บถาวรที่ /prof)
#   ไม่ตั้ง MODE → เข้าห้องประชุม + อัดเสียง (ใช้ profile ที่ล็อกอินไว้)
set -e

# 1) เสียงเสมือน (null-sink ชื่อ meet) — เสียง Chromium ไหลเข้ามาให้ ffmpeg อัดจาก meet.monitor
pulseaudio -D --exit-idle-time=-1 --disable-shm=1 2>/dev/null || true
for i in $(seq 1 10); do pactl info >/dev/null 2>&1 && break; sleep 0.5; done
pactl load-module module-null-sink sink_name=meet sink_properties=device.description=meet >/dev/null
pactl set-default-sink meet

# 2) จอเสมือน
Xvfb :99 -screen 0 1280x720x24 >/dev/null 2>&1 &
export DISPLAY=:99
sleep 1

if [ "$MODE" = "login" ]; then
    # โหมดล็อกอิน: มี window manager + VNC ให้ผู้ใช้เข้ามาคลิกล็อกอินได้จริง
    fluxbox >/dev/null 2>&1 &
    x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -bg -quiet >/dev/null 2>&1
    # เปิดทางที่สองผ่านเบราว์เซอร์ (noVNC) — ไม่ต้องลงโปรแกรม VNC บนเครื่อง host
    # ยังเปิด 5900 ไว้ให้คนที่อยากใช้ client จริงด้วย
    websockify -D --web=/usr/share/novnc 6080 localhost:5900 >/dev/null 2>&1 || \
        echo "[entrypoint] เปิด noVNC ไม่สำเร็จ — ยังใช้ VNC client ต่อ localhost:5900 ได้"
    exec python3 /app/login.py
fi

# โหมดปกติ: python เป็น PID 1 (docker stop → SIGTERM ถึง python → ปิดอัดสุภาพ)
exec python3 /app/join_meet.py
