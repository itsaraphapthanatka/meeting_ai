"""ฝั่ง host: สั่ง Docker รันบอทเข้าห้องประชุมออนไลน์ แล้วคืนไฟล์เสียงที่อัดได้.

บอททั้งหมดรันใน container (Chromium + เสียงเสมือน) จึงไม่แตะลำโพง/หน้าจอเครื่องนี้
— แชร์หน้าจอในโปรแกรมประชุมได้ตามปกติ. ดูโค้ดบอทที่ bot/join_meet.py
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .config import config

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
            "-p", "127.0.0.1:5900:5900",
            "-e", "MODE=login",
            "-v", f"{PROFILE_DIR}:/prof",
            IMAGE,
        ],
        check=True,
    )
    print("🔐 กำลังเปิดหน้าจอบอทผ่าน VNC...")
    time.sleep(5)  # รอ x11vnc + Chromium พร้อม
    # macOS: เปิด Screen Sharing ให้อัตโนมัติ
    subprocess.run(["open", "vnc://localhost:5900"], capture_output=True)
    print(
        "\n  1) หน้าต่าง Screen Sharing จะเปิดขึ้น (ถ้าไม่ขึ้น เปิดเอง: vnc://localhost:5900)\n"
        "  2) ล็อกอิน Google account ของบอทให้เรียบร้อย (แนะนำบัญชีเฉพาะบอท)\n"
        "  3) เสร็จแล้วกลับมาที่นี่ กด Enter เพื่อบันทึก\n"
    )
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
) -> Path:
    """ส่งบอทเข้าห้อง แล้วคืน path ไฟล์เสียงที่อัดได้. กด Ctrl+C เพื่อให้บอทออกและหยุดอัด."""
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
        "-v", f"{out_wav.parent}:/out",
        "-v", f"{PROFILE_DIR}:/prof",
        "-e", f"MEET_URL={url}",
        "-e", f"BOT_NAME={name}",
        "-e", f"OUT_WAV=/out/{out_wav.name}",
        "-e", f"MAX_MINUTES={max_minutes}",
        IMAGE,
    ]

    print(f"🤖 ส่งบอท \"{name}\" เข้าห้องประชุม...")
    print("   ⚠️ อย่าลืมกด 'รับเข้าห้อง' (Admit) ให้บอทในโปรแกรมประชุม")
    print("   กด Ctrl+C เมื่อจบ เพื่อให้บอทออกจากห้องและหยุดอัด\n")
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print("\n⏹  กำลังสั่งบอทออกจากห้องอย่างสุภาพ...")
        subprocess.run([docker, "stop", "-t", "30", container], check=False)

    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise RuntimeError(
            "ไม่ได้ไฟล์เสียง — บอทอาจเข้าห้องไม่สำเร็จ "
            f"(ห้องบังคับล็อกอิน/ไม่ได้กดรับ/UI เปลี่ยน) ดูภาพ {out_wav.parent / 'bot_debug.png'} และ log ด้านบน"
        )
    print(f"✅ ได้ไฟล์เสียง: {out_wav}")
    return out_wav
