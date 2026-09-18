"""ฝั่ง host: สั่ง Docker รันบอทเข้าห้องประชุมออนไลน์ แล้วคืนไฟล์เสียงที่อัดได้.

บอททั้งหมดรันใน container (Chromium + เสียงเสมือน) จึงไม่แตะลำโพง/หน้าจอเครื่องนี้
— แชร์หน้าจอในโปรแกรมประชุมได้ตามปกติ. ดูโค้ดบอทที่ bot/join_meeting.py
"""

from __future__ import annotations

import hashlib
import random
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
# ห้าม mount โฟลเดอร์นี้ตรงๆ ให้ container: งานหนึ่งได้โฟลเดอร์ย่อยของตัวเอง (ดู _job_slot)
# เพราะฝั่ง container เขียนชื่อไฟล์ตายตัว ถ้าใช้ร่วมกันบอทหลายตัวจะทับกันเอง
STAGE_DIR = config.root / "recordings" / "bot"
LOG_TAIL_LINES = 40
STATUS_NAME = "bot_status.txt"   # คอนเทนเนอร์เขียนสถานะจริงไว้ให้อ่าน

# ทุกคำสั่ง docker ต้องมีเพดานเวลา — Docker Desktop ค้างได้ (อัปเดตตัวเอง/WSL สะดุด)
# ถ้าไม่ใส่ ตัวตรวจความสามารถที่ถูกเรียกจากเธรด heartbeat จะแขวนทั้ง worker
# แล้วเซิร์ฟเวอร์จะเห็นว่าเครื่องนี้หลุดไปเลย ทั้งที่โพรเซสยังอยู่
DOCKER_TIMEOUT = 25
PROBE_TIMEOUT = 60
BUILD_TIMEOUT = 45 * 60   # build ครั้งแรกดึง base image + ติดตั้ง Chromium จริงๆ นานได้
EXIT_TIMEOUT = 120        # รอ docker run ตัวนี้จบหลังสั่งหยุด ก่อนจะขึ้นไม้แข็ง

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


# ระยะผ่อนผันตอนสั่งหยุด container: docker ส่ง SIGTERM แล้วรอเท่านี้ก่อนจะ SIGKILL
# join_meeting.py ใช้ช่วงนี้ปิด ffmpeg ให้ header ของ wav ถูกเขียนปิด — สั้นไปไฟล์เสียงเสีย
STOP_GRACE = 20         # ปกติ (เก็บกวาดของค้าง / จบโหมดล็อกอิน)
STOP_GRACE_INROOM = 30  # บอทที่กำลังอัดอยู่ ให้เวลาปิดไฟล์นานกว่า
STOP_MARGIN = 30        # เผื่อเวลาที่ docker เองใช้เกินระยะผ่อนผัน


def _stop(docker: str, container: str, grace: int = STOP_GRACE):
    """สั่งหยุด container อย่างสุภาพ พร้อมเพดานเวลาที่ **ยาวกว่าระยะผ่อนผันเสมอ**.

    จุดที่พลาดง่าย: _run() มีเพดานเริ่มต้น DOCKER_TIMEOUT = 25 วินาที ถ้าส่ง
    `stop -t 30` เข้าไปเฉยๆ เพดานจะมาถึงก่อนระยะผ่อนผันจะหมดด้วยซ้ำ — เรายกเลิกคำสั่ง
    หยุดของตัวเองกลางคัน ทั้งที่บอทกำลังปิดไฟล์เสียงอยู่พอดี
    """
    return _run([docker, "stop", "-t", str(grace), container],
                timeout=grace + STOP_MARGIN)


def _rm(docker: str, container: str):
    """ลบ container ทิ้ง — ชื่อซ้ำจากรอบก่อนทำให้ docker run ตัวใหม่ไม่ขึ้น."""
    return _run([docker, "rm", "-f", container])


def _wait_or_kill(proc, docker: str, container: str) -> str:
    """รอให้ `docker run` ตัวนี้จบ ถ้าไม่จบให้ขึ้นไม้แข็ง — คืนคำเตือน (ว่าง = เรียบร้อยดี).

    เดิมเป็น `proc.wait(timeout=120)` เปล่าๆ นอก except: พอ container ไม่ยอมตาย
    TimeoutExpired จะหลุดออกจาก join_and_record ทั้งดุ้น ข้ามท่อนที่ย้ายไฟล์เสียงไปปลายทาง
    และท่อนเก็บกวาดไปทั้งหมด — **เสียงที่อัดมาทั้งชั่วโมงค้างอยู่ในโฟลเดอร์พัก** แล้วงานถูก
    รายงานว่าล้มด้วย traceback ภาษาอังกฤษที่ไปโผล่ในการ์ดงานของเจ้าของการประชุม

    ฆ่า proc เฉยๆ ไม่พอ: proc คือ docker client ไม่ใช่ container ปล่อยไว้ container จะยัง
    เขียน /out อยู่ขณะที่เรากำลังย้ายไฟล์ ได้ wav ที่ขาดกลาง จึงต้อง `docker kill` ตัวจริงก่อน
    """
    try:
        proc.wait(timeout=EXIT_TIMEOUT)
        return ""
    except subprocess.TimeoutExpired:
        pass
    _run([docker, "kill", container])
    try:
        proc.wait(timeout=DOCKER_TIMEOUT)
    except subprocess.TimeoutExpired:
        proc.kill()
    return (f"บอทไม่ยอมหยุดใน {EXIT_TIMEOUT} วินาที จึงถูกบังคับปิด — "
            "ไฟล์เสียงอาจขาดช่วงท้าย")


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
    # ไม่ capture output: การ build ครั้งแรกใช้เวลาหลายนาที คนต้องเห็นความคืบหน้า
    # แต่ต้องมีเพดานเวลา ไม่งั้น docker ที่ค้างรอเครือข่ายจะแขวน worker ไว้ทั้งวันโดยไม่มีใครรู้
    # และต้องไม่โยน CalledProcessError ดิบๆ ออกไป — มันไปโผล่เป็น traceback ในการ์ดงาน
    try:
        r = subprocess.run([docker, "build", "-t", IMAGE, "--label",
                            f"{SRC_LABEL}={want}", str(BOT_DIR)],
                           timeout=BUILD_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"build image ของบอทไม่จบใน {BUILD_TIMEOUT // 60} นาที — "
            "มักเป็นเน็ตช้า/ดึง base image ไม่ได้ ลองใหม่หรือรัน `docker build` เองเพื่อดูสาเหตุ"
        ) from e
    if r.returncode != 0:
        raise RuntimeError(
            f"build image ของบอทไม่สำเร็จ (rc={r.returncode}) — "
            "ดูข้อความของ docker ด้านบนเพื่อหาสาเหตุ")


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
    ชื่อเครื่องเป็นภาษาไทยได้ ซึ่งใช้เป็นชื่อ container ไม่ได้ จึงต้องมี hash ประกอบเสมอ

    เดิมตัด slug ที่ 16 ตัวแล้วใช้ hash เฉพาะตอน slug ว่าง ซึ่งชนกันได้จริง (BACKLOG #38):
    "meeting-ai-worker-01" กับ "-02" เหลือ "meetingaiworker0" เท่ากันทั้งคู่ — ชื่อยูนิตตาม
    เอกสาร deploy เลยพากันชน ผลคือ worker ตัวที่เพิ่งเริ่มสั่ง docker stop บอทของอีกตัว
    **ที่กำลังนั่งอยู่ในห้องประชุมจริง** (cleanup_stale สั่ง stop ไม่ใช่แค่ลบของเก่า)

    ตอนนี้ต่อ hash ของ "ชื่อเต็ม" ไว้เสมอ ความไม่ซ้ำจึงไม่ขึ้นกับว่า slug ถูกตัดตรงไหน

    ตอนอัปเกรด: tag เปลี่ยนรูป container/โฟลเดอร์ที่สร้างด้วยโค้ดเก่าจึงไม่เข้าขอบเขตของ
    cleanup_stale() อีก ถ้ารีสตาร์ต worker ตอนบอทยังอยู่ในห้อง ตัวนั้นจะกลายเป็นของกำพร้า
    ที่โค้ดใหม่ไม่รู้จัก — รีสตาร์ตตอนไม่มีบอททำงาน หรือกวาดเองครั้งเดียวด้วย
    `docker ps --filter name=maibot_job_` แล้ว `docker stop`
    """
    slug = re.sub(r"[^A-Za-z0-9]", "", worker or "")[:12]
    digest = hashlib.md5((worker or "solo").encode("utf-8")).hexdigest()[:6]
    return f"{slug}{digest}"


def _job_slot(job_id: str | None, worker: str = "") -> tuple[str, Path]:
    """ชื่อ container กับโฟลเดอร์พักที่จะ mount เป็น /out ของงานหนึ่งงาน — ใช้ suffix เดียวกัน.

    ฝั่ง container เขียนชื่อไฟล์ตายตัว (/out/bot_status.txt, /out/bot_*.png ดู bot/join_meeting.py)
    เดิม mount recordings/bot ทั้งโฟลเดอร์ให้ทุกบอท: สถานะของห้องหนึ่งถูกรายงานให้อีกงาน
    และงานที่จบก่อนลบภาพหน้าจอของงานที่ยังอยู่ในห้อง (prod รัน --max-bots 6)
    แยกโฟลเดอร์ต่องานฝั่ง host ก็จบ ไม่ต้องแก้ฝั่ง container
    ติด worker tag ไว้ด้วยให้ cleanup_stale() ลบได้เฉพาะของตัวเอง — อีก worker บนเครื่องเดียวกัน
    อาจกำลัง mount โฟลเดอร์ของมันอยู่
    """
    tag = re.sub(r"[^A-Za-z0-9_.-]", "", job_id or "")[:40] or str(int(time.time()))
    suffix = f"{worker_tag(worker)}_{tag}"
    return f"{PREFIX}{suffix}", STAGE_DIR / suffix


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
        _stop(exe, name)
    # เก็บโฟลเดอร์พักที่ค้างจากรอบก่อน — ต้องทำหลังสั่ง stop ครบแล้ว เพราะ container
    # ที่ยังรันอยู่ mount โฟลเดอร์นั้นเป็น /out อยู่ ลบตอนนั้นไฟล์เสียงที่กำลังเขียนจะพัง
    # แต่ "สั่ง stop แล้ว" ไม่เท่ากับ "หยุดแล้ว": _run() กลืน timeout ไว้เงียบๆ
    # จึงต้องถามใหม่ว่าเหลือตัวไหนรันอยู่ แล้วเว้นโฟลเดอร์ของพวกนั้นไว้
    # ถามไม่สำเร็จ (rc != 0 รวมถึง timeout) = ไม่รู้ว่าใครยังอยู่ ไม่ลบอะไรเลยปลอดภัยกว่า
    if worker:
        still = _run([exe, "ps", "--filter", f"name={PREFIX}{worker_tag(worker)}_",
                      "--format", "{{.Names}}"], text=True)
        if still.returncode == 0:
            _prune_stages(worker, frozenset(
                x.strip() for x in still.stdout.splitlines() if x.strip()))
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
    _rm(docker, container)
    started = _run(
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
        text=True,
    )
    if started.returncode != 0:
        raise RuntimeError(f"เปิด container สำหรับล็อกอินไม่สำเร็จ — {started.stderr.strip()}")
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
    _stop(docker, container)
    _rm(docker, container)
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

    เก็บทุกครั้ง ไม่ใช่แค่ตอนพลาด — โฟลเดอร์ /out คือโฟลเดอร์พักต่องาน ซึ่ง join_and_record()
    ลบทิ้งเมื่องานจบ ภาพจึงหายไปพร้อมกันทั้งที่เป็นหลักฐานเดียวว่าหน้าจอบอทเป็นอย่างไร
    และลบต้นฉบับด้วย ไม่ให้ภาพของรอบก่อนค้างมาปนกับรอบใหม่
    """
    # job_id กลายเป็นชื่อไฟล์ใน logs/ จึงต้องกรองด้วยชุดเดียวกับ _job_slot ก่อน
    # (ผู้เรียกบางทางไม่ได้ผ่าน store.valid_id มา ห้ามให้ '/' หรือ '..' หลุดเข้ามาประกอบ path)
    tag = re.sub(r"[^A-Za-z0-9_.-]", "", job_id or "") or str(int(time.time()))
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


def _prune_stages(worker: str, live: frozenset[str] = frozenset()) -> list[Path]:
    """ลบโฟลเดอร์พักของงานที่ค้างไว้ตอน worker ตายกลางคัน คืนรายการที่ลบไป.

    ปกติ join_and_record() ลบโฟลเดอร์ของตัวเองใน finally อยู่แล้ว ที่เหลือค้างคือรอบที่
    โพรเซสถูกฆ่า/เครื่องดับ ไม่มีใครลบให้ — ปล่อยไว้ recordings/bot/ จะโตขึ้นหนึ่งโฟลเดอร์ต่องาน
    เก็บเฉพาะของ worker ตัวนี้ (prefix = worker_tag) เพราะ worker ตัวอื่นบนเครื่องเดียวกัน
    อาจกำลัง mount โฟลเดอร์ของมันเป็น /out ให้บอทที่ยังประชุมอยู่
    ไม่ส่งชื่อ worker มา (โหมด CLI) = ไม่รู้ว่าอันไหนของใคร ไม่ลบอะไรเลยปลอดภัยกว่า
    live = ชื่อ container ที่ยังรันอยู่ ณ ตอนเรียก ห้ามแตะโฟลเดอร์ของพวกนี้เลย ทั้งลบและ
    เก็บภาพ — _run() กลืน timeout (คืน _Timeout rc 124) สั่ง stop ไปแล้วไม่ได้แปลว่าหยุดจริง
    ส่วนบอทที่ยังรอหน้าห้องก็ยังไม่มี wav ตัวกันเรื่อง wav จึงช่วยไม่ได้
    (เดิมมีอีกเหตุผลคือ worker_tag ชนกัน ซึ่ง BACKLOG #38 ปิดไปแล้ว แต่สองเหตุผลข้างต้นยังอยู่)
    โฟลเดอร์ที่มี wav ขนาดไม่ใช่ศูนย์ = เสียงประชุมจริงที่กำพร้า เก็บไว้ให้คนตัดสินใจ (backlog #25)
    แต่ภาพหน้าจอย้ายเข้า logs/ ก่อน ไม่งั้นหลักฐานหายไปกับโฟลเดอร์
    """
    if not worker:
        return []
    removed: list[Path] = []
    try:
        stages = sorted(STAGE_DIR.glob(f"{worker_tag(worker)}_*"))
    except OSError:
        return []
    for d in stages:
        if not d.is_dir():
            continue
        if PREFIX + d.name in live:
            continue        # บอทตัวนี้ยังอยู่ในห้อง โฟลเดอร์คือ /out ที่ ffmpeg กำลังเขียน
        # ชื่อโฟลเดอร์คือ <worker tag>_<job id> ตัดส่วน worker ออก ให้ไฟล์ใน logs/
        # ชื่อรูปแบบเดียวกับทางปกติ (bot_debug_<job id>.png ตามที่ README บอกไว้)
        _keep_debug_shot(d, d.name.split("_", 1)[-1])
        try:
            if any(w.stat().st_size > 0 for w in d.glob("*.wav")):
                continue
        except OSError:
            continue
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d)
    return removed


def _stage_removable(stage: Path, moved: bool) -> bool:
    """ลบโฟลเดอร์พักของงานนี้ได้ไหม — ย้ายไม่สำเร็จ = เสียงประชุมยังอยู่ที่นี่ที่เดียว ห้ามลบ.

    นโยบายเดียวกับ _prune_stages: wav ที่มีข้อมูลจริงห้ามหายเพราะโค้ดเก็บกวาดของเราเอง
    shutil.move() ข้ามคนละ filesystem (โหมด worker ปลายทางคือ tempdir ของ job ที่อาจอยู่
    คนละ mount) ไม่ใช่ rename แต่เป็น copy+unlink ซึ่งพังกลางทางได้จริง — ดิสก์เต็มตอนรัน
    --max-bots 6 พร้อมกัน หรือไฟล์ถูกโปรแกรมแอนตี้ไวรัสล็อกบน Windows
    ถ้า finally ลบทิ้งตรงนั้น เสียงประชุมทั้งชั่วโมงหายถาวร ไม่มีที่ไหนเหลือให้กู้
    """
    if moved:
        return True
    try:
        return not any(w.stat().st_size > 0 for w in stage.glob("*.wav"))
    except OSError:
        return False        # อ่านโฟลเดอร์ไม่ได้ = ไม่รู้ว่ามีเสียงอยู่ไหม อย่าเพิ่งลบ


def _fail_reason(out_wav: Path, tail, job_id: str | None) -> str:
    """ข้อความ error ที่ไล่ต่อได้ — บอกอาการที่เจอใน log ไม่ใช่แค่ลิสต์สาเหตุที่เป็นไปได้.

    ข้อความนี้เดินทางไปไกลกว่าที่คนเขียนคิด: worker ส่งเข้า jobs.error แล้วเจ้าของการประชุม
    อ่านได้ผ่าน /api/jobs/{id} จึงต้องเป็น "สิ่งที่ผู้ใช้ทำอะไรต่อได้" ไม่ใช่ของสำหรับคนดูแล
    เครื่อง — เดิมยัด path เต็มของเครื่อง worker กับ log ดิบ 12 บรรทัดของ container ลงไปด้วย
    ซึ่งมีลิงก์ห้องประชุม/ชื่อไฟล์/โครงสร้างโฟลเดอร์ของเครื่องคนอื่นปนได้ (BACKLOG #40)

    ของพวกนั้นไม่ได้หายไป — พิมพ์ลง stdout ของ worker พร้อมรหัสอ้างอิงเดียวกับที่แนบไปใน
    ข้อความ คนดูแลเครื่องจึงยังไล่ต่อได้ และผู้ใช้มีรหัสไว้บอกว่าให้ดูงานไหน
    """
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

    # สถานะสุดท้ายที่คอนเทนเนอร์เขียนไว้ บอกได้ว่าไปตายขั้นไหน โดยไม่ต้องอ่าน log เป็น
    # "waiting" = บอทกดขอเข้าห้องแล้วแต่ไม่มีใครกด Admit ให้ (สาเหตุที่พบบ่อยที่สุด)
    # ต้องอ่านก่อนโฟลเดอร์พักถูกลบใน finally ของ join_and_record()
    status = _read_status(out_wav.parent)
    shot = _keep_debug_shot(out_wav.parent, job_id)
    ref = f"{random.randrange(16 ** 6):06x}"
    parts = ["ไม่ได้ไฟล์เสียง — บอทเข้าห้องไม่สำเร็จ"]
    if hints:
        # hints เป็นข้อความคงที่ที่เราเขียนเอง ไม่ได้เอาบรรทัด log มาต่อ จึงไม่มีอะไรรั่ว
        parts.append("สาเหตุที่เจอใน log: " + " · ".join(hints))
    if status:
        parts.append(f"สถานะล่าสุดที่บอทรายงาน: {status}")
    if shot:
        parts.append("มีภาพหน้าจอตอนพลาดเก็บไว้ที่เครื่องประมวลผล")
    parts.append(f"ให้ผู้ดูแลเครื่องดู log ของ worker ที่รหัส {ref}")

    # ส่วนที่คนดูแลเครื่องต้องใช้ ออกทาง stdout ของ worker เท่านั้น ไม่ขึ้นเว็บ
    detail = [f"[bot {ref}] เข้าห้องไม่สำเร็จ (job {job_id or '-'})"]
    if shot:
        detail.append(f"[bot {ref}] ภาพหน้าจอ: {shot}")
    if lines:
        detail.append(f"[bot {ref}] log ท้ายสุดของบอท:" + chr(10) + chr(10).join(lines[-12:]))
    print(chr(10).join(detail), file=sys.stderr, flush=True)
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
    # ตั้งชื่อตาม job id เพื่อให้ไล่หา/สั่งหยุดจากภายนอกได้ (ชื่อ container ต้องเป็น [A-Za-z0-9_.-])
    # และได้โฟลเดอร์พักของงานนี้มาด้วย — ให้ container เขียนลงที่พักใต้โปรเจกต์ก่อน
    # แล้วค่อยย้ายไปปลายทางจริง (พักเสมอ ไม่ว่าปลายทางจะอยู่ในโปรเจกต์หรือไม่
    # เพราะ /out มีไฟล์ชื่อตายตัวของ container ปนอยู่ ห้ามให้ไปโผล่ปลายทาง)
    container, stage = _job_slot(job_id, worker)
    # ชื่อซ้ำจากรอบก่อนที่ค้างอยู่ ต้องเก็บให้เรียบร้อยก่อน ไม่งั้น docker run จะฟ้องชื่อชนกัน
    # ต้องมาก่อนล้างโฟลเดอร์ ไม่งั้นลบ /out ใต้เท้า container เก่าที่ยังเขียนไฟล์อยู่
    _rm(docker, container)
    # ล้างให้ว่างก่อนเริ่ม: งานเดิมที่ถูกสั่งรันซ้ำด้วย job id เดิมจะได้ไม่ไปอ่าน
    # bot_status.txt ของรอบก่อน แล้วรายงานว่า "อยู่ในห้อง" ทั้งที่ container ใหม่ยังไม่ทันเปิดหน้าเว็บ
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True, exist_ok=True)
    cout = stage / out_wav.name

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
        _stop(docker, container, STOP_GRACE_INROOM)

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
    warn = ""
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
            warn = _wait_or_kill(proc, docker, container)
    except KeyboardInterrupt:
        print("\n⏹  กำลังสั่งบอทออกจากห้องอย่างสุภาพ...")
        leave()
        warn = _wait_or_kill(proc, docker, container)
    if warn:
        print(f"⚠️  {warn}")
        tail.append(warn)

    moved = False
    try:
        if not cout.exists() or cout.stat().st_size == 0:
            raise RuntimeError(_fail_reason(cout, tail, job_id))
        kept = _keep_debug_shot(cout.parent, job_id)
        if kept:
            print(f"🖼  ภาพหน้าจอบอท: {kept.parent}")
        try:
            shutil.move(str(cout), str(out_wav))
        except OSError as e:
            # ย้ายข้าม filesystem (ปลายทางเป็น tempdir) ล้มได้ เช่น ENOSPC — เสียงยังอยู่ครบที่โฟลเดอร์พัก
            # (_stage_removable กันไม่ให้ถูกลบ) ต้องบอก path ไปด้วย ไม่งั้นคนอ่าน error จะสรุปว่าเสียงหาย
            # path ของเครื่อง worker ไม่ใช่ข้อมูลของเจ้าของการประชุม (BACKLOG #40) แต่คนอ่าน
            # ต้องรู้ว่า "เสียงไม่ได้หาย" ไม่งั้นจะไปนั่งอัดใหม่ทั้งที่ไฟล์ยังอยู่
            print(f"⚠️  ย้ายไฟล์เสียงไม่สำเร็จ ({e}) — ไฟล์ที่อัดได้ยังอยู่ที่ {cout}",
                  file=sys.stderr, flush=True)
            raise RuntimeError("ย้ายไฟล์เสียงไปปลายทางไม่สำเร็จ — "
                               "ไฟล์ที่อัดได้ยังอยู่ที่เครื่องประมวลผล ยังไม่หาย "
                               "ให้ผู้ดูแลเครื่องกู้ให้") from e
        moved = True
    finally:
        # ถึงตรงนี้ container จบแล้ว เสียงย้ายไปปลายทาง ภาพอยู่ใน logs/ แล้ว
        # โฟลเดอร์พักของงานนี้จึงต้องหายไปด้วย ไม่งั้น recordings/bot/ โตขึ้นหนึ่งโฟลเดอร์ต่องาน
        # (ตัวที่ค้างเพราะ worker ตายกลางคัน ให้ _prune_stages() ตอนเริ่มรอบใหม่เก็บ)
        # ยกเว้นตอน move พัง — เสียงยังอยู่ที่นี่ที่เดียว ดู _stage_removable()
        if _stage_removable(cout.parent, moved):
            shutil.rmtree(cout.parent, ignore_errors=True)
    print(f"✅ ได้ไฟล์เสียง: {out_wav}")
    return out_wav
