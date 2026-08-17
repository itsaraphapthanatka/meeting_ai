# บอทเข้าห้องประชุมออนไลน์ (Google Meet) — Chromium + เสียงเสมือน (PulseAudio) + ffmpeg
# รันแยกใน container ทั้งหมด จึงไม่แตะลำโพง/หน้าจอของเครื่อง host เลย
#
# ใช้ official Playwright image ที่มี Chromium + OS deps ครบแล้ว (รองรับ amd64/arm64)
FROM mcr.microsoft.com/playwright/python:v1.48.0-jammy

ENV PYTHONUNBUFFERED=1 DEBIAN_FRONTEND=noninteractive

# เพิ่มเสียงเสมือน (pulseaudio), จอเสมือน (xvfb), ffmpeg สำหรับอัดเสียง,
# x11vnc + fluxbox สำหรับโหมดล็อกอินครั้งเดียว (ให้ผู้ใช้ VNC เข้ามาล็อกอิน Google)
RUN apt-get update && apt-get install -y --no-install-recommends \
        pulseaudio ffmpeg xvfb procps x11vnc fluxbox \
    && rm -rf /var/lib/apt/lists/*

# image นี้มีแต่ browsers ที่ /ms-playwright — ต้องลง playwright pip package เอง
# (เวอร์ชันต้องตรงกับ browsers ในภาพ: 1.48.0 = chromium-1140)
RUN python3 -m pip install --no-cache-dir playwright==1.48.0

WORKDIR /app
COPY join_meet.py /app/join_meet.py
COPY login.py /app/login.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# ไฟล์เสียงที่อัดจะถูกเขียนลง /out (mount มาจากโฟลเดอร์ recordings/ ของ host)
VOLUME ["/out"]

ENTRYPOINT ["/app/entrypoint.sh"]
