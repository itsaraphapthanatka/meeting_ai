"""ฝั่ง host: สั่ง Docker รันบอทเข้าห้องประชุมออนไลน์ แล้วคืนไฟล์เสียงที่อัดได้.

บอททั้งหมดรันใน container (Chromium + เสียงเสมือน) จึงไม่แตะลำโพง/หน้าจอเครื่องนี้
— แชร์หน้าจอในโปรแกรมประชุมได้ตามปกติ. ดูโค้ดบอทที่ bot/join_meet.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import config

# ดูจอบอทได้สองทาง: เบราว์เซอร์ (noVNC) หรือ VNC client จริง
NOVNC_URL = "http://localhost:6080/vnc.html?autoconnect=1&resize=scale"
VNC_URL = "vnc://localhost:5900"

# ถามฝั่งเรียกทุกกี่วินาทีว่า "หยุดไหม" และรายงานว่าอยู่ในห้องมานานเท่าไร
# ถี่กว่านี้เปลืองคำขอ HTTP ห่างกว่านี้กดปุ่มแล้วบอทออกช้า
TICK_SEC = 10

IMAGE = "meeting-ai-bot"
BOT_DIR = config.root / "bot"
PROFILE_DIR = BOT_DIR / "profile"   # เก็บ session ที่ล็อกอิน Google ไว้ (ไม่ commit)


def _docker() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise RuntimeError("ไม่พบ docker — ติดตั้ง Docker Desktop แล้วเปิดโปรแกรมก่อน")
    # เช็กว่า daemon เปิดอยู่ไหม
    if subprocess.run([exe, "info"], capture_output=True).returncode != 0:
        raise RuntimeError("Docker daemon ยังไม่เปิด — เปิดแอป Docker Desktop ก่อนแล้วลองใหม่")
    return exe


def _image_exists(docker: str) -> bool:
    r = subprocess.run([docker, "images", "-q", IMAGE], capture_output=True, text=True)
    return bool(r.stdout.strip())


def build_image(force: bool = False) -> None:
    docker = _docker()
    if _image_exists(docker) and not force:
        return
    print("🐳 กำลัง build image ของบอท (ครั้งแรกใช้เวลาหลายนาที ครั้งต่อไปไม่ต้องแล้ว)...")
    subprocess.run([docker, "build", "-t", IMAGE, str(BOT_DIR)], check=True)


def missing_pieces() -> list[str]:
    """สิ่งที่ยังขาดเพื่อให้ส่งบอทเข้าห้องได้ — ว่างเปล่า = พร้อม."""
    missing = []
    exe = shutil.which("docker")
    if not exe:
        missing.append("Docker (ติดตั้ง Docker Desktop)")
        return missing        # ไม่มี docker ก็ตรวจข้ออื่นต่อไม่ได้
    if subprocess.run([exe, "info"], capture_output=True).returncode != 0:
        missing.append("Docker daemon ยังไม่เปิด")
        return missing
    if not _image_exists(exe):
        missing.append(f"image {IMAGE} (สร้างด้วย mai bot-login)")
    if not PROFILE_DIR.exists() or not any(PROFILE_DIR.iterdir()):
        missing.append("การล็อกอิน Google ของบอท (รัน mai bot-login)")
    return missing


def available() -> bool:
    return not missing_pieces()


def _mount(path: Path) -> str:
    """path สำหรับ -v ของ docker — ใช้ / แม้บน Windows.

    docker แยก -v ด้วย ':' ซึ่งชนกับ 'C:\\...' รูป C:/Users/... ปลอดภัยกว่าและ Docker Desktop รับ
    """
    return path.resolve().as_posix()


def _open_bot_screen() -> str:
    """เปิดจอบอทให้ผู้ใช้เห็น แล้วคืนข้อความบอกว่าเปิดทางไหน.

    ใช้ noVNC ผ่านเบราว์เซอร์เป็นทางหลัก — ไม่ต้องลงโปรแกรมอะไรบน host
    (เดิมเรียก `open vnc://...` ซึ่งมีแต่บน macOS บน Windows โยน FileNotFoundError
     ทำให้ bot-login พังทั้งคำสั่งทั้งที่ container เปิดรออยู่แล้ว
     ส่วน VNC client ก็พึ่งไม่ได้ ตัวติดตั้งของ RealVNC เคย 404 มาแล้ว)
    """
    opened = False
    try:
        import webbrowser   # stdlib ใช้ได้ทุก OS ไม่ต้องเรียกคำสั่งของระบบ
        opened = webbrowser.open(NOVNC_URL)
    except Exception:
        pass
    head = ("เบราว์เซอร์เปิดจอบอทให้แล้ว" if opened
            else f"เปิดเบราว์เซอร์ไปที่ {NOVNC_URL}")
    return f"{head}\n     (ถ้าอยากใช้ VNC client จริงก็ต่อ localhost:5900 ได้ ไม่ต้องใส่รหัส)"


def login() -> None:
    """เปิดโหมดล็อกอินครั้งเดียว — ผู้ใช้ VNC เข้ามาล็อกอิน Google ให้บอท (profile เก็บถาวร)."""
    docker = _docker()
    build_image()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    container = "maibot_login"
    subprocess.run([docker, "rm", "-f", container], capture_output=True)
    subprocess.run(
        [
            docker, "run", "-d", "--name", container,
            # ผูกกับ 127.0.0.1 เท่านั้น — จอบอทตอนล็อกอินมีหน้า Google อยู่ ห้ามเปิดให้เครือข่ายเห็น
            "-p", "127.0.0.1:6080:6080",   # noVNC (เบราว์เซอร์)
            "-p", "127.0.0.1:5900:5900",   # VNC client
            "-e", "MODE=login",
            "-v", f"{_mount(PROFILE_DIR)}:/prof",
            IMAGE,
        ],
        check=True,
    )
    print("🔐 กำลังเปิดหน้าจอบอท...")
    time.sleep(6)  # รอ x11vnc + websockify + Chromium พร้อม
    print(f"\n  1) {_open_bot_screen()}\n"
          "  2) ล็อกอิน Google account ของบอทให้เรียบร้อย (แนะนำบัญชีเฉพาะบอท)\n"
          "  3) เสร็จแล้วกลับมาที่นี่ กด Enter เพื่อบันทึก\n")
    try:
        input("   >>> ล็อกอินเสร็จแล้วกด Enter... ")
    except (EOFError, KeyboardInterrupt):
        pass
    print("💾 กำลังบันทึก profile...")
    subprocess.run([docker, "stop", "-t", "20", container], check=False)
    subprocess.run([docker, "rm", "-f", container], capture_output=True)
    print(f"✅ ล็อกอินเรียบร้อย — profile เก็บที่ {PROFILE_DIR}\n   ใช้ ./mai bot <ลิงก์> ได้เลย")


def join_and_record(
    url: str,
    out_wav: str | Path,
    name: str = "AI Notetaker",
    max_minutes: int = 180,
    on_tick=None,
) -> Path:
    """ส่งบอทเข้าห้อง แล้วคืน path ไฟล์เสียงที่อัดได้.

    on_tick(วินาทีที่อยู่ในห้อง) -> bool ถูกเรียกทุก TICK_SEC วินาที
    คืน True = สั่งบอทออกจากห้องเดี๋ยวนี้ (ฝั่งเว็บใช้ทำปุ่ม "ให้บอทออกแล้วสรุป")
    ไม่ส่งมา = โหมด CLI รอจนบอทจบเอง กด Ctrl+C เพื่อให้ออก
    """
    docker = _docker()
    build_image()

    if not PROFILE_DIR.exists() or not any(PROFILE_DIR.iterdir()):
        raise RuntimeError(
            "ยังไม่ได้ล็อกอิน Google ให้บอท — ห้อง Workspace จะบล็อก guest\n"
            "   รันครั้งเดียวก่อน:  ./mai bot-login"
        )

    out_wav = Path(out_wav).resolve()
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    container = f"maibot_{int(time.time())}"

    cmd = [
        docker, "run", "--rm", "--name", container,
        "-v", f"{_mount(out_wav.parent)}:/out",
        "-v", f"{_mount(PROFILE_DIR)}:/prof",
        "-e", f"MEET_URL={url}",
        "-e", f"BOT_NAME={name}",
        "-e", f"OUT_WAV=/out/{out_wav.name}",
        "-e", f"MAX_MINUTES={max_minutes}",
        IMAGE,
    ]

    print(f"🤖 ส่งบอท \"{name}\" เข้าห้องประชุม...")
    print("   ⚠️ อย่าลืมกด 'รับเข้าห้อง' (Admit) ให้บอทในโปรแกรมประชุม")
    print("   กด Ctrl+C เมื่อจบ เพื่อให้บอทออกจากห้องและหยุดอัด\n")
    # docker stop -> SIGTERM -> join_meet.py ปิด ffmpeg ให้ wav สมบูรณ์ก่อนตาย
    # (ห้าม kill ตรงๆ ไม่งั้น header ของ wav ไม่ถูกเขียนปิด ไฟล์จะเสีย)
    def leave() -> None:
        subprocess.run([docker, "stop", "-t", "30", container], check=False)

    proc = subprocess.Popen(cmd)
    started = time.monotonic()
    try:
        if on_tick is None:
            proc.wait()                     # โหมด CLI: รอจนบอทจบเอง
        else:
            while proc.poll() is None:
                time.sleep(TICK_SEC)
                if proc.poll() is not None:
                    break
                if on_tick(time.monotonic() - started):
                    print("⏹  ได้รับคำสั่งให้บอทออกจากห้อง")
                    leave()
                    break
            proc.wait(timeout=120)
    except KeyboardInterrupt:
        print("\n⏹  กำลังสั่งบอทออกจากห้องอย่างสุภาพ...")
        leave()
        try:
            proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise RuntimeError(
            "ไม่ได้ไฟล์เสียง — บอทอาจเข้าห้องไม่สำเร็จ "
            f"(ห้องบังคับล็อกอิน/ไม่ได้กดรับ/UI เปลี่ยน) ดูภาพ {out_wav.parent / 'bot_debug.png'} และ log ด้านบน"
        )
    print(f"✅ ได้ไฟล์เสียง: {out_wav}")
    return out_wav
