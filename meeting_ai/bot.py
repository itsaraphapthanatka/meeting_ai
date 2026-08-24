"""ฝั่ง host: สั่ง Docker รันบอทเข้าห้องประชุมออนไลน์ แล้วคืนไฟล์เสียงที่อัดได้.

บอททั้งหมดรันใน container (Chromium + เสียงเสมือน) จึงไม่แตะลำโพง/หน้าจอเครื่องนี้
— แชร์หน้าจอในโปรแกรมประชุมได้ตามปกติ. ดูโค้ดบอทที่ bot/join_meeting.py
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import threading
from collections import deque
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
# ชื่อ container ของ "บอทเข้าห้อง" ต้องแยกจากตัวล็อกอินให้ชัด
# ไม่งั้น cleanup_stale() ตอน worker เริ่ม จะไปฆ่าหน้าล็อกอินที่ผู้ใช้กำลังกรอกรหัสอยู่
PREFIX = "maibot_job_"
LOGIN_CONTAINER = "maibot_login"
# หน้าล็อกอินของแต่ละเจ้า — บาง Workspace/Zoom ห้าม guest ต้องล็อกอินก่อน
LOGIN_SITES = {
    "google": "https://accounts.google.com/",
    "teams": "https://login.microsoftonline.com/",
    "zoom": "https://zoom.us/signin",
}
DEFAULT_SITE_URL = LOGIN_SITES["google"]

# ที่เก็บหลักฐานตอนบอทเข้าห้องไม่สำเร็จ — ต้องอยู่นอกโฟลเดอร์ชั่วคราวของ worker
# ซึ่งถูกลบทันทีที่งานจบ (เคยชี้ผู้ใช้ไปหาไฟล์ที่ถูกลบไปแล้ว)
DEBUG_DIR = config.root / "logs"
# ที่พักไฟล์เสียงของบอท — ต้องอยู่ใต้โฟลเดอร์โปรเจกต์
# Docker Desktop บน Windows bind-mount โฟลเดอร์ชั่วคราวของระบบไม่ได้บางเครื่อง
# (ตอบ 'Access is denied' จาก daemon) แต่ path ใต้โปรเจกต์ใช้ได้ เพราะ
# bot/profile ก็ถูก mount จากที่นั้นและทำงานได้
STAGE_DIR = config.root / "recordings" / "bot"
LOG_TAIL_LINES = 40
STATUS_NAME = "bot_status.txt"   # คอนเทนเนอร์เขียนสถานะจริงไว้ให้อ่าน

# ทุกคำสั่ง docker ต้องมีเพดานเวลา — Docker Desktop ค้างได้ (อัปเดตตัวเอง/WSL สะดุด)
# ถ้าไม่ใส่ ตัวตรวจความสามารถที่ถูกเรียกจากเธรด heartbeat จะแขวนทั้ง worker
# แล้วเซิร์ฟเวอร์จะเห็นว่าเครื่องนี้หลุดไปเลย ทั้งที่โพรเซสยังอยู่
DOCKER_TIMEOUT = 25
PROBE_TIMEOUT = 60

# ไฟล์ที่ประกอบเป็น image — เปลี่ยนไฟล์พวกนี้แล้วต้อง build ใหม่
SOURCES = ("Dockerfile", "entrypoint.sh", "join_meeting.py",
           "platforms.py", "login.py")
SRC_LABEL = "mai.src"
BOT_DIR = config.root / "bot"
PROFILE_DIR = BOT_DIR / "profile"   # เก็บ session ที่ล็อกอิน Google ไว้ (ไม่ commit)


class _Timeout:
    """ผลลัพธ์ปลอมเมื่อคำสั่ง docker ไม่ตอบในเวลา — ให้ผู้เรียกอ่านเหมือน CompletedProcess."""

    returncode = 124   # เท่ากับที่ timeout(1) ใช้

    def __init__(self, cmd: list[str]) -> None:
        self.stdout = ""
        self.stderr = f"docker ไม่ตอบใน {DOCKER_TIMEOUT}s: {' '.join(cmd[1:3])}"


def _run(cmd: list[str], text: bool = False, timeout: int | None = None):
    """เรียก docker พร้อมเพดานเวลาเสมอ — ไม่โยน TimeoutExpired ออกไปให้ผู้เรียกจัดการ."""
    try:
        return subprocess.run(cmd, capture_output=True, text=text,
                              encoding="utf-8" if text else None,
                              errors="replace" if text else None,
                              timeout=timeout or DOCKER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return _Timeout(cmd)


def _docker() -> str:
    exe = shutil.which("docker")
    if not exe:
        raise RuntimeError("ไม่พบ docker — ติดตั้ง Docker Desktop แล้วเปิดโปรแกรมก่อน")
    # เช็กว่า daemon เปิดอยู่ไหม
    if _run([exe, "info"]).returncode != 0:
        raise RuntimeError("Docker daemon ยังไม่เปิด (หรือไม่ตอบภายใน เวลาที่รอ) — เปิดแอป Docker Desktop ก่อนแล้วลองใหม่")
    return exe


def _image_exists(docker: str) -> bool:
    r = _run([docker, "images", "-q", IMAGE], text=True)
    return bool(r.stdout.strip())


def source_hash() -> str:
    """ลายนิ้วมือของไฟล์ที่ประกอบเป็น image."""
    h = hashlib.sha256()
    for name in SOURCES:
        p = BOT_DIR / name
        h.update(name.encode())
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()[:12]


def _image_hash(docker: str) -> str:
    label = '{{index .Config.Labels "' + SRC_LABEL + '"}}'
    r = _run([docker, "image", "inspect", IMAGE, "--format", label], text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def build_image(force: bool = False) -> None:
    """build ถ้ายังไม่มี image หรือโค้ดในโฟลเดอร์ bot/ เปลี่ยนไปจากที่ build ไว้.

    เดิมเช็กแค่ว่า "มี image ไหม" — พอ git pull ได้โค้ดบอทใหม่มา image เก่าก็ยังถูกใช้ต่อ
    เงียบๆ การแก้บั๊กในบอทจึงไม่มีผลจนกว่าจะมีคนไป build มือเอง
    """
    docker = _docker()
    want = source_hash()
    if not force and _image_exists(docker) and _image_hash(docker) == want:
        return
    why = "ยังไม่มี image" if not _image_exists(docker) else "โค้ดบอทเปลี่ยน"
    print(f"🐳 build image ของบอท ({why}) — ครั้งแรกใช้เวลาหลายนาที...")
    subprocess.run([docker, "build", "-t", IMAGE,
                    "--label", f"{SRC_LABEL}={want}", str(BOT_DIR)], check=True)


def _probe_run(docker: str) -> str:
    """ลองรัน container สั้นๆ พร้อม bind mount จริง — คืนเหตุผลถ้าทำไม่ได้.

    `docker info` ผ่านไม่ได้แปลว่า `docker run` จะผ่าน: Docker Desktop ผูกกับ session
    ของผู้ใช้ที่ล็อกอิน worker ที่รันจาก Task Scheduler แบบ S4U (ไม่ต้องล็อกอิน)
    เรียก API ได้แต่ mount ไม่ได้ ตอบ "Access is denied"
    ถ้าไม่ตรวจจุดนี้ worker จะโฆษณาว่าส่งบอทได้ แล้วไปพังตอนมีงานจริง
    """
    r = _run([docker, "run", "--rm", "--entrypoint", "true",
              "-v", f"{_mount(PROFILE_DIR)}:/prof", IMAGE],
             text=True, timeout=PROBE_TIMEOUT)
    if r.returncode == 0:
        return ""
    detail = (r.stderr or r.stdout or "").strip().splitlines()
    first = detail[-1] if detail else f"exit {r.returncode}"
    if "Access is denied" in first:
        return ("Docker รัน container ไม่ได้จาก session นี้ (Access is denied) — "
                "worker ที่ตั้งเป็นโหมด S4U ใช้ Docker Desktop ไม่ได้ "
                "ติดตั้ง task ใหม่แบบ Interactive: worker-service.ps1 install")
    return f"Docker รัน container ไม่ได้: {first[:160]}"


def missing_pieces() -> list[str]:
    """สิ่งที่ยังขาดเพื่อให้ส่งบอทเข้าห้องได้ — ว่างเปล่า = พร้อม."""
    missing = []
    exe = shutil.which("docker")
    if not exe:
        missing.append("Docker (ติดตั้ง Docker Desktop)")
        return missing        # ไม่มี docker ก็ตรวจข้ออื่นต่อไม่ได้
    if _run([exe, "info"]).returncode != 0:
        missing.append("Docker daemon ไม่ตอบ (ค้างหรือยังไม่เปิด)")
        return missing
    if not _image_exists(exe):
        missing.append(f"image {IMAGE} (สร้างด้วย mai bot-login)")
    if not PROFILE_DIR.exists() or not any(PROFILE_DIR.iterdir()):
        missing.append("การล็อกอิน Google ของบอท (รัน mai bot-login)")
    if not missing:
        why = _probe_run(exe)
        if why:
            missing.append(why)
    return missing


def worker_tag(worker: str) -> str:
    """ส่วนของชื่อ container ที่บอกว่าเป็นของ worker ตัวไหน.

    ต้องมี เพราะเปิด worker หลายตัวบนเครื่องเดียวได้ (เพื่อประชุมพร้อมกันหลายห้อง)
    ถ้าไม่แยก cleanup_stale() ของตัวที่เพิ่งเริ่ม จะไปปิดบอทของตัวอื่นที่กำลังประชุมอยู่
    ชื่อเครื่องเป็นภาษาไทยได้ ซึ่งใช้เป็นชื่อ container ไม่ได้ จึงถอยไปใช้ hash
    """
    slug = re.sub(r"[^A-Za-z0-9]", "", worker or "")[:16]
    return slug or hashlib.md5((worker or "solo").encode("utf-8")).hexdigest()[:8]


def cleanup_stale(worker: str = "") -> list[str]:
    """หยุด container ของบอทที่ยังรันค้างอยู่ แล้วคืนรายชื่อที่หยุดไป (ไม่แตะตัวล็อกอิน).

    เรียกตอน worker เริ่มทำงาน: worker ใหม่หมายความว่าตัวเก่าตายไปแล้ว
    container ที่มันเปิดไว้จึงเป็นของกำพร้า — daemon เป็นคนคุม container ไม่ใช่
    โพรเซสที่สั่ง `docker run` ดังนั้นบอทจะนั่งอยู่ในห้องประชุมต่อไปเรื่อยๆ
    โดยไม่มีใครสั่งให้ออกได้ (ปุ่ม "ให้บอทออก" ในเว็บก็ไปไม่ถึง)
    """
    exe = shutil.which("docker")
    if not exe:
        return []
    # กรองเฉพาะของ worker ตัวนี้ (ไม่ส่งชื่อมา = โหมด CLI ดูทั้งหมด)
    scope = f"{PREFIX}{worker_tag(worker)}_" if worker else PREFIX
    r = _run([exe, "ps", "--filter", f"name={scope}", "--format", "{{.Names}}"], text=True)
    names = [x.strip() for x in r.stdout.splitlines() if x.strip()]
    for name in names:
        # stop ไม่ kill — ให้บอทออกจากห้องและปิดไฟล์เสียงให้เรียบร้อยก่อน
        _run([exe, "stop", "-t", "20", name], timeout=50)
    return names


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


def login(site: str = "google") -> None:
    """เปิดโหมดล็อกอินครั้งเดียว — ผู้ใช้เข้ามาล็อกอินให้บอทผ่านเบราว์เซอร์.

    profile เดียวเก็บได้ทุกเจ้า รันซ้ำด้วย --site อื่นเพื่อเพิ่ม session ได้
    """
    docker = _docker()
    build_image()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    container = LOGIN_CONTAINER
    subprocess.run([docker, "rm", "-f", container], capture_output=True)
    subprocess.run(
        [
            docker, "run", "-d", "--name", container,
            # ผูกกับ 127.0.0.1 เท่านั้น — จอบอทตอนล็อกอินมีหน้า Google อยู่ ห้ามเปิดให้เครือข่ายเห็น
            "-p", "127.0.0.1:6080:6080",   # noVNC (เบราว์เซอร์)
            "-p", "127.0.0.1:5900:5900",   # VNC client
            "-e", "MODE=login",
            "-e", f"LOGIN_URL={LOGIN_SITES.get(site, DEFAULT_SITE_URL)}",
            "-v", f"{_mount(PROFILE_DIR)}:/prof",
            IMAGE,
        ],
        check=True,
    )
    print("🔐 กำลังเปิดหน้าจอบอท...")
    time.sleep(6)  # รอ x11vnc + websockify + Chromium พร้อม
    print(f"\n  1) {_open_bot_screen()}\n"
          f"  2) ล็อกอินบัญชีของบอทให้เรียบร้อย ({site}) — แนะนำบัญชีเฉพาะบอท\n"
          "  3) เสร็จแล้วกลับมาที่นี่ กด Enter เพื่อบันทึก\n")
    try:
        input("   >>> ล็อกอินเสร็จแล้วกด Enter... ")
    except (EOFError, KeyboardInterrupt):
        pass
    print("💾 กำลังบันทึก profile...")
    subprocess.run([docker, "stop", "-t", "20", container], check=False)
    subprocess.run([docker, "rm", "-f", container], capture_output=True)
    print(f"✅ ล็อกอินเรียบร้อย — profile เก็บที่ {PROFILE_DIR}\n   ใช้ ./mai bot <ลิงก์> ได้เลย")


SHOTS = ("bot_debug.png", "bot_after_join.png", "bot_inroom.png")


def _read_status(out_dir: Path) -> str:
    """สถานะที่คอนเทนเนอร์รายงาน: waiting / inroom / left (ว่าง = ยังไม่บอก).

    จำเป็นเพราะฝั่ง host มองไม่เห็นหน้าจอในคอนเทนเนอร์ ถ้าเดาว่า "กดปุ่มแล้ว
    = อยู่ในห้อง" จะรายงานเท็จตอนไม่มีใครกดรับ (เจอจริงกับ Zoom)
    """
    try:
        return (out_dir / STATUS_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _keep_debug_shot(out_dir: Path, job_id: str | None) -> Path | None:
    """ย้ายภาพหน้าจอของบอทไปไว้ที่ที่ยังอยู่หลังงานจบ คืน path ตัวแรกที่เก็บได้.

    เก็บทุกครั้ง ไม่ใช่แค่ตอนพลาด — โฟลเดอร์ /out เป็นที่ชั่วคราวของ worker
    ซึ่งถูกลบทิ้ง ภาพจึงหายไปพร้อมกันทั้งที่เป็นหลักฐานเดียวว่าหน้าจอบอทเป็นอย่างไร
    และลบต้นฉบับด้วย ไม่ให้ภาพของรอบก่อนค้างมาปนกับรอบใหม่
    """
    tag = job_id or str(int(time.time()))
    first = None
    for name in SHOTS:
        src = out_dir / name
        if not src.exists():
            continue
        try:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            dest = DEBUG_DIR / f"{Path(name).stem}_{tag}.png"
            shutil.copy2(src, dest)
            src.unlink()
            first = first or dest
        except OSError:
            continue
    return first


def _fail_reason(out_wav: Path, tail, job_id: str | None) -> str:
    """ข้อความ error ที่ไล่ต่อได้ — บอกอาการที่เจอใน log ไม่ใช่แค่ลิสต์สาเหตุที่เป็นไปได้."""
    lines = list(tail)
    joined = chr(10).join(lines)
    hints = []
    if "Automated bots" in joined or "ตรวจพบว่าเป็นบอท" in joined:
        hints.append("Zoom ปฏิเสธเพราะตรวจพบว่าเป็นบอท — ลองล็อกอินบัญชี Zoom ให้บอทก่อน (mai bot-login --site zoom) ถ้ายังไม่ผ่าน ให้ใช้วิธีอัดจากเครื่องผู้เข้าร่วม หรืออัปโหลดไฟล์ที่ Zoom อัดไว้เองแทน")
    if "หาปุ่มเข้าห้องไม่เจอ" in joined:
        hints.append("หาปุ่มเข้าห้องไม่เจอ — UI ของ Meet เปลี่ยน หรือหน้ายังโหลดไม่เสร็จ")
    if "รอ host กดรับ" in joined:
        hints.append("บอทกดขอเข้าห้องแล้ว แต่ไม่มีใครกด Admit ให้")
    if "Access is denied" in joined or "Error response from daemon" in joined:
        hints.append("Docker ปฏิเสธคำสั่ง run — มักเป็นเรื่อง bind mount "
                     "ตรวจ Settings > Resources > File sharing ว่าแชร์ไดรฟ์ที่โปรเจกต์อยู่แล้ว")
    if "ผิดพลาด" in joined or "Timeout" in joined:
        hints.append("เปิดหน้าห้องไม่สำเร็จ (เน็ตช้า / ลิงก์ผิด / ห้องยังไม่เปิด)")

    shot = _keep_debug_shot(out_wav.parent, job_id)
    parts = ["ไม่ได้ไฟล์เสียง — บอทเข้าห้องไม่สำเร็จ"]
    if hints:
        parts.append("สาเหตุที่เจอใน log: " + " · ".join(hints))
    if shot:
        parts.append(f"ภาพหน้าจอตอนพลาด: {shot}")
    if lines:
        parts.append("log ท้ายสุดของบอท:" + chr(10) + chr(10).join(lines[-12:]))
    return chr(10).join(parts)


def join_and_record(
    url: str,
    out_wav: str | Path,
    name: str = "AI Notetaker",
    max_minutes: int = 180,
    on_tick=None,
    job_id: str | None = None,
    passcode: str = "",
    worker: str = "",
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
    # ให้ container เขียนลงที่พักใต้โปรเจกต์ก่อน แล้วค่อยย้ายไปปลายทางจริง
    staged = config.root not in out_wav.parents
    if staged:
        STAGE_DIR.mkdir(parents=True, exist_ok=True)
        cout = STAGE_DIR / out_wav.name
    else:
        cout = out_wav
    # ตั้งชื่อตาม job id เพื่อให้ไล่หา/สั่งหยุดจากภายนอกได้ (ชื่อ container ต้องเป็น [A-Za-z0-9_.-])
    tag = re.sub(r"[^A-Za-z0-9_.-]", "", job_id or "")[:40] or str(int(time.time()))
    container = f"{PREFIX}{worker_tag(worker)}_{tag}"
    # ชื่อซ้ำจากรอบก่อนที่ค้างอยู่ ต้องเก็บให้เรียบร้อยก่อน ไม่งั้น docker run จะฟ้องชื่อชนกัน
    subprocess.run([docker, "rm", "-f", container], capture_output=True)

    cmd = [
        docker, "run", "--rm", "--name", container,
        "-v", f"{_mount(cout.parent)}:/out",
        "-v", f"{_mount(PROFILE_DIR)}:/prof",
        "-e", f"MEET_URL={url}",
        "-e", f"BOT_NAME={name}",
        "-e", f"OUT_WAV=/out/{cout.name}",
        "-e", f"MAX_MINUTES={max_minutes}",
        "-e", f"PASSCODE={passcode}",
        IMAGE,
    ]

    print(f"🤖 ส่งบอท \"{name}\" เข้าห้องประชุม...")
    print("   ⚠️ อย่าลืมกด 'รับเข้าห้อง' (Admit) ให้บอทในโปรแกรมประชุม")
    print("   กด Ctrl+C เมื่อจบ เพื่อให้บอทออกจากห้องและหยุดอัด\n")
    # docker stop -> SIGTERM -> join_meeting.py ปิด ffmpeg ให้ wav สมบูรณ์ก่อนตาย
    # (ห้าม kill ตรงๆ ไม่งั้น header ของ wav ไม่ถูกเขียนปิด ไฟล์จะเสีย)
    def leave() -> None:
        subprocess.run([docker, "stop", "-t", "30", container], check=False)

    # เก็บ output ของ container ไว้ด้วย ไม่ใช่ปล่อยผ่านไปหน้าจอเฉยๆ
    # เวลาบอทเข้าห้องไม่สำเร็จ บรรทัด [bot] ... คือเบาะแสเดียวที่บอกว่าพังขั้นไหน
    # ต้องแนบไปกับ error ให้เห็นในหน้าเว็บ ไม่ใช่ให้ไปเปิด log ในเครื่อง worker เอง
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail: deque[str] = deque(maxlen=LOG_TAIL_LINES)

    def pump() -> None:
        for line in proc.stdout or ():
            tail.append(line.rstrip())
            print(line.rstrip(), flush=True)

    threading.Thread(target=pump, name="bot-log", daemon=True).start()
    started = time.monotonic()
    try:
        if on_tick is None:
            proc.wait()                     # โหมด CLI: รอจนบอทจบเอง
        else:
            while proc.poll() is None:
                time.sleep(TICK_SEC)
                if proc.poll() is not None:
                    break
                if on_tick(time.monotonic() - started, _read_status(cout.parent)):
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

    if not cout.exists() or cout.stat().st_size == 0:
        raise RuntimeError(_fail_reason(cout, tail, job_id))
    kept = _keep_debug_shot(cout.parent, job_id)
    if kept:
        print(f"🖼  ภาพหน้าจอบอท: {kept.parent}")
    if staged:
        shutil.move(str(cout), str(out_wav))
    print(f"✅ ได้ไฟล์เสียง: {out_wav}")
    return out_wav
