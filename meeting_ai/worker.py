"""worker: ดึงงานจากเซิร์ฟเวอร์ (ในเครื่องหรือบน cloud) มาถอดเสียงด้วย GPU เครื่องนี้.

ใช้ตอน deploy หน้าเว็บขึ้น cloud ที่ไม่มี GPU — cloud ถือคิวกับข้อมูล เครื่องนี้ทำงานหนัก
    ฝั่งเซิร์ฟเวอร์: ตั้ง REMOTE_WORKER=1 และ WORKER_TOKEN
    ฝั่งนี้:        mai worker --api https://xxx.vercel.app --token <WORKER_TOKEN>

เสียงถูกดาวน์โหลดมาไว้ในโฟลเดอร์ชั่วคราวและลบทิ้งเมื่อทำงานเสร็จ
"""

from __future__ import annotations

import json
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import runner
from .config import (DEFAULT_MAX_BOTS as _DEFAULT_MAX_BOTS,
                     WORKER_HEARTBEAT_SECONDS, config)
from .web.blobstore import open_url

POLL_IDLE = 3.0        # วินาที รอเมื่อคิวว่าง
POLL_ERROR_MAX = 60.0  # เพดาน backoff เมื่อต่อเซิร์ฟเวอร์ไม่ได้
PROGRESS_MIN_GAP = 1.5  # ไม่ยิง progress ถี่กว่านี้ (นอกจากเปลี่ยนขั้น)
# เต้นบอกเซิร์ฟเวอร์ว่ายังอยู่ — คู่กับ WORKER_STALE_SECONDS ที่ฝั่งนั้นใช้ตัดสินว่าหลุด
HEARTBEAT_SEC = WORKER_HEARTBEAT_SECONDS
# รองานที่ค้างอยู่ให้จบก่อนออกได้นานแค่ไหน — ของเดิมตายตัวที่ 600 วิ ซึ่งสั้นกว่างานจริงมาก
# (บอทนั่งในห้องได้ถึง 180 นาที แล้วยังต้องถอดเสียง + สรุปต่ออีก) พอครบเวลาแล้วโปรเซสออก
# เธรดงานเป็น daemon จึงถูกฆ่ากลางคัน เซิร์ฟเวอร์เห็นแค่งานค้าง running แล้ว jobs_reap()
# คืนงานเข้าคิวใหม่ (งานบอทตีเป็น error) ทั้งที่งานเดิมเกือบเสร็จแล้ว
DRAIN_MAX_SEC = 6 * 3600
# ตรวจความสามารถของเครื่องใหม่ทุกกี่วินาที — Docker Desktop เปิด/ปิดได้ตลอดเวลา
# ถ้าเช็กครั้งเดียวตอนเปิด worker จะโฆษณาความสามารถผิดไปทั้ง session
# (เคยเจอจริง: เครื่องที่ Docker ดับไปแล้วยังคว้างานบอทมาทำ)
CAPS_REFRESH_SEC = 60.0
CHUNK = 1024 * 256
# นิยามอยู่ที่ config เพราะ cli.py ต้องใช้ค่าเดียวกันตอน parse args (BACKLOG #33)
DEFAULT_MAX_BOTS = _DEFAULT_MAX_BOTS


class WorkerError(RuntimeError):
    """status = รหัส HTTP ถ้ามี (None = ต่อไม่ติด/หมดเวลา ซึ่งลองใหม่แล้วมีโอกาสสำเร็จ)."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class AuthError(WorkerError):
    """token ไม่ถูกต้อง — ลองใหม่ไปก็เท่านั้น ต้องให้คนแก้ก่อน."""


class Client:
    def __init__(self, api: str, token: str, timeout: int = 120) -> None:
        self.api = api.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, *, data: bytes | None = None,
                 ctype: str | None = None, timeout: int | None = None):
        req = urllib.request.Request(self.api + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("User-Agent", "meeting_ai-worker/1.0")
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = resp.read()
                if resp.status == 204 or not body:
                    return resp.status, None
                if "json" in (resp.headers.get("Content-Type") or ""):
                    return resp.status, json.loads(body.decode("utf-8"))
                return resp.status, body
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:400]
            if e.code in (401, 403):
                raise AuthError(
                    f"เซิร์ฟเวอร์ปฏิเสธ (HTTP {e.code}): {detail}\n"
                    "   ตรวจว่า WORKER_TOKEN ในเครื่องนี้ตรงกับที่ตั้งไว้ฝั่งเซิร์ฟเวอร์"
                ) from e
            raise WorkerError(f"HTTP {e.code} จาก {path}: {detail}", e.code) from e
        except urllib.error.URLError as e:
            raise WorkerError(f"ต่อเซิร์ฟเวอร์ไม่ได้ ({path}): {e.reason}") from e
        except TimeoutError as e:
            raise WorkerError(f"หมดเวลารอเซิร์ฟเวอร์ ({path})") from e

    def get_json(self, path: str):
        return self._request("GET", path)[1]

    def post_json(self, path: str, payload: dict, timeout: int | None = None):
        # ensure_ascii=False: ภาษาไทยที่ถูก escape เป็น \uXXXX กินพื้นที่ 6 ไบต์
        # ต่อตัวอักษร เทียบกับ 3 ไบต์ของ utf-8 — บทถอดเสียงยาว ๆ ต่างกันเท่าตัว (BACKLOG #50)
        return self._request("POST", path,
                             data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                             ctype="application/json", timeout=timeout)[1]

    def claim(self, worker: str, kinds: list[str] | None = None) -> dict | None:
        payload = json.dumps({"worker": worker, "kinds": kinds}).encode("utf-8")
        status, body = self._request("POST", "/api/worker/claim",
                                     data=payload, ctype="application/json")
        return body if status == 200 else None

    def heartbeat(self, worker: str, status: str, job: str | None = None,
                  gpu: str | None = None, caps: dict | None = None) -> None:
        """บอกเซิร์ฟเวอร์ว่ายังอยู่ — ทำแม้ตอนว่าง หน้าเว็บจะได้เห็นว่ามีเครื่องพร้อม.

        ส่ง caps ไปด้วยเพราะฝั่ง cloud ถอดเสียงเองไม่ได้ ต้องรู้ว่าเครื่องนี้ทำอะไรได้
        """
        try:
            self.post_json("/api/worker/heartbeat",
                           {"worker": worker, "status": status, "job": job,
                            "gpu": gpu, "caps": caps},
                           timeout=20)
        except WorkerError:
            pass  # heartbeat หลุดไม่ใช่เรื่องคอขาดบาดตาย งานยังเดินได้

    def download(self, path: str, dest: Path) -> Path:
        # URL เต็ม = presigned ของที่เก็บภายนอก ห้ามแนบ Authorization ของเราไปด้วย
        # (S3/R2 จะปฏิเสธเมื่อมีกลไก auth สองแบบพร้อมกัน)
        external = path.startswith(("http://", "https://"))
        req = urllib.request.Request(path if external else self.api + path)
        if not external:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            # open_url เลือก IPv4 ก่อน — เครื่องที่ไม่มีเส้น IPv6 จะไม่เสียเวลารอ timeout
            with open_url(req, timeout=900) as resp, dest.open("wb") as fh:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
        except urllib.error.URLError as e:
            raise WorkerError(f"ดาวน์โหลด {path} ไม่สำเร็จ: {e}") from e
        return dest

    def upload(self, path: str, src: Path) -> dict:
        data = src.read_bytes()
        if path.startswith(("http://", "https://")):
            # presigned PUT ตรงเข้าที่เก็บ — ไม่ผ่านเซิร์ฟเวอร์ เลี่ยงเพดานขนาด body
            req = urllib.request.Request(path, data=data, method="PUT")
            req.add_header("Content-Type", "application/octet-stream")
            try:
                with open_url(req, timeout=900):
                    pass
            except urllib.error.URLError as e:
                raise WorkerError(f"อัปโหลดไฟล์เสียงผสมไม่สำเร็จ: {e}") from e
            return {}
        return self._request("POST", path, data=data,
                             ctype="application/octet-stream", timeout=600)[1] or {}


def _ext_of(url: str, fallback: str = "webm") -> str:
    name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else fallback


def describe_gpu() -> str | None:
    """ชื่อ GPU ไว้โชว์ในหน้าเว็บ — None ถ้าไม่มี/เรียกไม่ได้."""
    try:
        p = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=15)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip().splitlines()[0].strip()
    except Exception:
        pass
    return None


def _run_one(client: Client, spec: dict, tmp: Path, worker: str = "") -> dict:
    job_id = spec["id"]
    last = {"at": 0.0, "step": ""}
    # เซิร์ฟเวอร์ตอบธง stop กลับมาพร้อม progress — ไม่ต้องเปิด polling อีกเส้น
    # (ผู้ใช้กด "ให้บอทออกจากห้อง" ในหน้าเว็บ)
    stop_flag = {"on": False}

    def progress(step: str, value: float) -> None:
        now = time.monotonic()
        if step == last["step"] and now - last["at"] < PROGRESS_MIN_GAP:
            return
        last.update(at=now, step=step)
        print(f"   {int(value * 100):3d}%  {step}")
        try:
            reply = client.post_json(f"/api/worker/jobs/{urllib.parse.quote(job_id)}/progress",
                                     {"step": step, "progress": value}, timeout=20)
            if isinstance(reply, dict) and reply.get("stop"):
                stop_flag["on"] = True
        except WorkerError as e:
            print(f"   (รายงาน progress ไม่ได้: {e})", file=sys.stderr)

    if spec["kind"] == "bot":
        result = runner.bot_job(spec, progress, tmp,
                                stop_check=lambda: stop_flag["on"], work_dir=tmp,
                                worker=worker)
        return _upload_playback(client, job_id, result, spec)

    if spec["kind"] != "process":
        return runner.HANDLERS[spec["kind"]](spec, progress)

    urls = spec.get("track_urls") or {}

    def fetch(name: str) -> Path:
        url = urls.get(name)
        if not url:
            raise WorkerError(f"spec ไม่มี URL ของแทร็ก {name}")
        dest = tmp / f"{name}.{_ext_of(url)}"
        print(f"   ดาวน์โหลดแทร็ก {name} …")
        return client.download(url, dest)

    result = runner.transcribe_job(spec, fetch, progress, tmp)
    return _upload_playback(client, job_id, result, spec)


def _upload_playback(client: Client, job_id: str, result: dict, spec: dict | None = None) -> dict:
    """ไฟล์เสียงผสมเกิดบนเครื่องนี้ ต้องส่งขึ้นไปให้เซิร์ฟเวอร์เก็บแล้วใช้ path ของฝั่งนั้น.

    อัปไม่ขึ้นไม่ถือว่างานล่ม — บทถอดเสียงกับสรุปเป็นของแพงที่สุด (เวลา GPU + ค่า LLM
    และสำหรับงานบอทคือเวลาประชุมที่ย้อนกลับไปอัดใหม่ไม่ได้) เก็บของพวกนั้นไว้
    แล้วบอกผู้ใช้ว่าฟังเสียงย้อนไม่ได้ ดีกว่าทิ้งทั้งงาน
    """
    playback = result.get("playback")
    if not playback:
        return result
    src = Path(playback)
    print("   อัปโหลดไฟล์เสียงผสม …")
    target = (spec or {}).get("playback_upload_url")
    try:
        if target:
            client.upload(target, src)
            result["playback"] = (spec or {}).get("playback_key") or src.name
        else:
            out = client.upload(
                f"/api/worker/jobs/{urllib.parse.quote(job_id)}/audio?ext={src.suffix.lstrip('.')}",
                src,
            )
            result["playback"] = out.get("playback") or None
    except WorkerError as e:
        print(f"⚠️  เก็บไฟล์เสียงไม่สำเร็จ: {e}", file=sys.stderr)
        result["playback"] = None
        note = f"เก็บไฟล์เสียงไม่สำเร็จ ({e}) — บทถอดเสียงและสรุปยังอยู่ครบ แต่ฟังย้อนหลังไม่ได้"
        result["warning"] = f"{result['warning']} · {note}" if result.get("warning") else note
    return result


# ---------- เก็บกวาดของเก่าตามอายุ (BACKLOG #25) ----------

PRUNE_EVERY_SECONDS = 24 * 3600
_last_prune = 0.0


def prune_artifacts_if_due(force: bool = False) -> dict[str, int]:
    """เรียกจากลูปหลักได้ทุกงวด — ของจริงเกิดวันละครั้ง.

    แยกจาก cleanup_stale() เพราะคนละเรื่อง: อันนั้นคือ "บอทของรอบก่อนยังค้างอยู่ ปิดซะ"
    ส่วนอันนี้คือ "ของที่เก็บไว้เป็นเดือนแล้ว ไม่มีใครมาดู ลบทิ้ง"
    ล้มเหลวเมื่อไรต้องไม่ลากงานของ worker ล้มตาม — เก็บกวาดไม่ใช่งานหลัก
    """
    global _last_prune
    now = time.monotonic()
    if not force and _last_prune and now - _last_prune < PRUNE_EVERY_SECONDS:
        return {}
    _last_prune = now
    try:
        from . import bot as _bot

        removed = _bot.prune_old_artifacts()
        if removed.get("shots") or removed.get("stages"):
            print(f"🧹 ลบของเก่าเกิน {config.bot_retention_days} วัน: "
                  f"ภาพ {removed['shots']} ไฟล์, โฟลเดอร์พัก {removed['stages']} รายการ "
                  f"({removed['bytes'] / 1e6:.1f} MB)")
        return removed
    except Exception as e:
        print(f"⚠️  ลบของเก่าไม่สำเร็จ: {e}", file=sys.stderr)
        return {}


def _start_pruner() -> None:
    """เธรดเก็บกวาดของ worker — ทำทันทีหนึ่งรอบ แล้ววันละครั้ง (BACKLOG #25).

    **ห้ามวางบนลูปรับงาน** ซึ่งเป็นสิ่งที่ผมทำรอบแรกแล้ว CI ฝั่ง Windows จับได้:
    prune_old_artifacts() ถาม docker (รอได้ถึง DOCKER_TIMEOUT) แล้วเดินไล่โฟลเดอร์ทั้งชุด
    เอาไปคั่นทางหยิบงาน = หน่วงทุกงาน และทำให้ตอนสั่งปิดช้าลงจนชนเพดาน drain ของ BACKLOG #20

    เป็น daemon จึงถูกฆ่าทันทีตอนโพรเซสจบ ระหว่าง rmtree อาจเหลือโฟลเดอร์ลบค้างได้
    ไม่เป็นไร — รอบหน้าเก็บต่อเอง (ignore_errors=True อยู่แล้ว)
    """
    def loop() -> None:
        while True:
            prune_artifacts_if_due()
            time.sleep(PRUNE_EVERY_SECONDS)

    threading.Thread(target=loop, name="mai-bot-pruner", daemon=True).start()


# ---------- ข้อความ error ที่ขึ้นเว็บ (BACKLOG #40) ----------

MAX_ERROR_CHARS = 600

# เส้นทางไฟล์ในเครื่องประมวลผล: "C:\Users\..." / "D:/a/..." และรากมาตรฐานบนยูนิกซ์
# จงใจไม่จับ "/" ทุกอันแบบเหมารวม ไม่งั้น URL กับข้อความปกติจะโดนกินไปด้วย
_LOCAL_PATH = re.compile(
    r"""(?:(?<![A-Za-z])[A-Za-z]:[\\/]|(?<![\w.])/(?:home|Users|tmp|var|opt|mnt|root|srv|proc)/)[^\s"'()]*""")


def public_error(exc: BaseException) -> str:
    """ข้อความที่ส่งขึ้น jobs.error — เจ้าของการประชุมเป็นคนอ่าน ไม่ใช่คนดูแลเครื่อง.

    ทุกอย่างที่หลุดออกจาก work() ถูกส่งขึ้นไปดิบ ๆ ด้วย str(e) ซึ่งพา path ของเครื่อง worker
    ขึ้นเว็บได้ง่ายมาก (FileNotFoundError, PermissionError, ffmpeg, shutil ล้วนใส่ path มาให้)
    นี่เป็นด่านสุดท้ายเฉย ๆ — ข้อความที่เราเขียนเองควรสะอาดตั้งแต่ต้นทางอยู่แล้ว

    รายละเอียดเต็มไม่ได้หาย: work() เรียก traceback.print_exc() ลง stderr ของ worker ก่อนแล้ว
    """
    text = (str(exc) or exc.__class__.__name__).strip()
    safe = _LOCAL_PATH.sub("(ไฟล์ในเครื่องประมวลผล)", text)
    if len(safe) > MAX_ERROR_CHARS:
        safe = safe[:MAX_ERROR_CHARS - 1].rstrip() + "…"
    return safe


# ---------- ส่งผลงานกลับ: ห้ามทิ้งของที่ถอดเสียงมาแล้ว (BACKLOG #50) ----------

RESULT_RETRIES = 5
RESULT_BACKOFF = 4.0          # วินาที คูณสองไปเรื่อย ๆ: 4, 8, 16, 32
# ผลงานที่ส่งไม่สำเร็จรออยู่ตรงนี้ ไม่ใช่ temp dir ของงานซึ่งถูกลบทันทีที่ออกจาก with
PENDING_DIR = config.root / "recordings" / "pending-results"


def _transient(err: WorkerError) -> bool:
    """ลองใหม่แล้วมีโอกาสสำเร็จไหม — ต่อไม่ติด/หมดเวลา/เซิร์ฟเวอร์ล่ม/โดนจำกัดอัตรา

    4xx อื่น ๆ (เช่น 413 payload ใหญ่เกิน, 400 ข้อมูลไม่ผ่านการตรวจ) ลองกี่ครั้งก็ได้ผลเดิม
    """
    return err.status is None or err.status in (408, 429) or err.status >= 500


def _save_pending(job_id: str, result: dict, reason: str) -> Path | None:
    """เก็บผลงานลงดิสก์เมื่อส่งไม่สำเร็จ — ถอดเสียงประชุมสองชั่วโมงใหม่แพงกว่านี้หลายเท่า."""
    try:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)
        path = PENDING_DIR / f"{job_id}.json"
        path.write_text(
            json.dumps({"job_id": job_id, "reason": reason, "saved_at": time.time(),
                        "result": result}, ensure_ascii=False),
            encoding="utf-8")
        return path
    except OSError as e:
        print(f"⚠️  เก็บผลงานลงดิสก์ไม่ได้ด้วย: {e}", file=sys.stderr)
        return None


def post_result(client: "Client", job_id: str, result: dict) -> None:
    """ส่งผลงานพร้อมลองใหม่ ถ้าสุดท้ายยังไม่ได้ก็เก็บลงดิสก์ก่อนแล้วค่อยโยนต่อ.

    ของเดิมยิงครั้งเดียว พลาดเมื่อไรก็เข้า except ที่ไปแจ้ง /error แล้วบทถอดเสียงทั้งไฟล์
    หายถาวร — เน็ตสะดุดวินาทีเดียวก็เสียเวลา GPU เป็นชั่วโมง
    """
    path = f"/api/worker/jobs/{urllib.parse.quote(job_id)}/result"
    delay = RESULT_BACKOFF
    last: WorkerError | None = None
    for attempt in range(1, RESULT_RETRIES + 1):
        try:
            client.post_json(path, result, timeout=180)
            return
        except WorkerError as e:
            last = e
            if not _transient(e) or attempt == RESULT_RETRIES:
                break
            print(f"⚠️  ส่งผลงานไม่สำเร็จ (ครั้งที่ {attempt}/{RESULT_RETRIES}): {e}"
                  f" — ลองใหม่ใน {delay:.0f} วินาที", file=sys.stderr)
            time.sleep(delay)
            delay *= 2

    saved = _save_pending(job_id, result, str(last))
    if saved:
        # path เต็มพิมพ์ไว้ที่เครื่องนี้เท่านั้น ไม่ส่งไปกับข้อความ error — เส้นทางในเครื่อง
        # worker ไม่ใช่ข้อมูลที่เจ้าของการประชุมควรเห็นในหน้าเว็บ (BACKLOG #40)
        print(f"💾 เก็บผลงานไว้ที่ {saved} — จะส่งใหม่อัตโนมัติเมื่อ worker เริ่มรอบหน้า",
              file=sys.stderr)
        raise WorkerError(f"ส่งผลงานกลับไม่สำเร็จ ({last}) — "
                          "เก็บไว้ที่เครื่องประมวลผลแล้ว จะส่งใหม่เมื่อ worker เริ่มรอบหน้า",
                          getattr(last, "status", None))
    raise WorkerError(f"ส่งผลงานกลับไม่สำเร็จ: {last}", getattr(last, "status", None))


def flush_pending(client: "Client") -> int:
    """ส่งผลงานที่ค้างในดิสก์ซ้ำ — เรียกตอน worker เริ่มทำงาน คืนจำนวนที่ส่งสำเร็จ."""
    if not PENDING_DIR.exists():
        return 0
    sent = 0
    for path in sorted(PENDING_DIR.glob("*.json")):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            job_id, result = saved["job_id"], saved["result"]
        except (OSError, json.JSONDecodeError, KeyError) as e:
            print(f"⚠️  อ่านผลงานค้าง {path.name} ไม่ได้: {e}", file=sys.stderr)
            continue
        try:
            client.post_json(f"/api/worker/jobs/{urllib.parse.quote(job_id)}/result",
                             result, timeout=180)
        except WorkerError as e:
            print(f"⚠️  ส่งผลงานค้าง {path.name} ไม่สำเร็จ: {e}", file=sys.stderr)
            continue
        path.unlink(missing_ok=True)
        sent += 1
        print(f"📤 ส่งผลงานที่ค้างไว้สำเร็จ: {job_id}")
    return sent


def run(api: str, token: str, once: bool = False, poll: float = POLL_IDLE,
        name: str | None = None, max_bots: int = DEFAULT_MAX_BOTS) -> int:
    client = Client(api, token)
    stopping = {"flag": False}
    active: dict[str, str] = {}          # job id -> kind ของงานที่กำลังทำอยู่
    active_lock = threading.Lock()
    max_bots = max(1, int(max_bots))
    worker_name = (name or socket.gethostname())[:80]
    gpu = describe_gpu()
    # เก็บใน dict เพื่อให้เธรด heartbeat อัปเดตแล้ว loop หลักเห็นค่าใหม่ด้วย
    state = {"caps": runner.machine_caps(), "caps_at": time.monotonic()}
    caps = state["caps"]

    # ผลงานที่ค้างจากรอบก่อน (เน็ตล่ม/เซิร์ฟเวอร์ดับตอนส่ง) ต้องได้ออกก่อนรับงานใหม่
    try:
        flushed = flush_pending(client)
        if flushed:
            print(f"📤 ส่งผลงานที่ค้างไว้ {flushed} งาน")
    except Exception as e:
        print(f"⚠️  ส่งผลงานค้างไม่สำเร็จ: {e}", file=sys.stderr)

    def on_signal(signum, frame):
        stopping["flag"] = True
        print("\n⏹️  จะหยุดหลังงานปัจจุบันจบ (กดอีกครั้งเพื่อหยุดทันที)")
        signal.signal(signum, signal.SIG_DFL)

    try:
        signal.signal(signal.SIGINT, on_signal)
    except ValueError:
        pass  # ไม่ใช่เธรดหลัก

    # ตัวเองเพิ่งเริ่ม = worker ตัวก่อนตายไปแล้ว บอทที่มันเปิดไว้จึงไม่มีใครคุม
    # ต้องไล่ปิดก่อน ไม่งั้นบอทกำพร้าจะนั่งอยู่ในห้องประชุมต่อไปเรื่อยๆ
    if state["caps"].get("bot"):
        try:
            from . import bot as _bot
            for name in _bot.cleanup_stale(worker_name):
                print(f"🧹 ปิดบอทที่ค้างจากรอบก่อน: {name}")
        except Exception as e:
            print(f"⚠️  เก็บกวาดบอทที่ค้างไม่สำเร็จ: {e}", file=sys.stderr)
        _start_pruner()

    print(f"🛠️  worker พร้อม — เซิร์ฟเวอร์: {client.api}")
    print(f"   ชื่อเครื่อง: {worker_name}" + (f"   GPU: {gpu}" if gpu else "   (ไม่มี GPU)"))
    ways = [k for k in ("local", "api") if caps.get(k)]
    print(f"   ถอดเสียงได้: {', '.join(ways) or '(ไม่มีเลย!)'}"
          + (f"   API -> {caps['stt_host']} ({caps['stt_model']})" if caps.get("api") else ""))
    if caps.get("diarize"):
        print("   แยกผู้พูดได้ (sherpa-onnx)")
    else:
        print("   แยกผู้พูดไม่ได้ ขาด: " + "; ".join(caps.get("diarize_missing") or []))
    if caps.get("bot"):
        print("   ส่งบอทเข้าห้องประชุมได้ (Docker)")
    else:
        print("   ส่งบอทเข้าห้องไม่ได้ ขาด: " + "; ".join(caps.get("bot_missing") or []))
    if not config.llm_api_key:
        print("⚠️  worker ตัวนี้ยังไม่มี LLM_API_KEY — ถอดเสียงได้แต่จะสรุปไม่ได้")
    print("   กด Ctrl+C เพื่อหยุด\n", flush=True)

    # เต้นทุก HEARTBEAT_SEC วินาที ให้หน้าเว็บรู้ว่าเครื่องนี้ยังอยู่ แม้ตอนว่าง
    def refresh_caps() -> dict:
        """คำนวณความสามารถใหม่ถ้าเก่าเกิน CAPS_REFRESH_SEC."""
        if time.monotonic() - state["caps_at"] >= CAPS_REFRESH_SEC:
            try:
                fresh = runner.machine_caps()
            except Exception:
                return state["caps"]      # ตรวจไม่ได้ ใช้ค่าเดิมดีกว่าหยุดทำงาน
            state["caps_at"] = time.monotonic()
            if fresh != state["caps"]:
                gained = [k for k in ("local", "api", "diarize", "bot")
                          if fresh.get(k) and not state["caps"].get(k)]
                lost = [k for k in ("local", "api", "diarize", "bot")
                        if state["caps"].get(k) and not fresh.get(k)]
                if gained or lost:
                    print("ℹ️  ความสามารถเปลี่ยน"
                          + (f" ได้เพิ่ม: {', '.join(gained)}" if gained else "")
                          + (f" หายไป: {', '.join(lost)}" if lost else ""), flush=True)
                state["caps"] = fresh
        return state["caps"]

    def beat() -> None:
        """เต้นจนกว่าจะ "ถูกสั่งหยุด **และ** ไม่มีงานค้าง" — ไม่ใช่แค่ถูกสั่งหยุด.

        ผูกกับ stopping["flag"] อย่างเดียวไม่ได้: --once ตั้งธงนี้ทันทีที่คว้างานมาได้
        และ Ctrl+C ก็ตั้งระหว่างงานยังเดินอยู่ หยุดเต้นตอนนั้น = บอกเซิร์ฟเวอร์ว่าเครื่องนี้ตายแล้ว
        ทั้งที่งานยังทำอยู่จริง (ครบ 75 วิ หน้าเว็บขึ้น "เงียบไป…" และ worker_capabilities()
        มองไม่เห็นเครื่องนี้ จนคนสั่งงานใหม่ไม่ได้ทั้งที่มี worker ทำงานอยู่)
        """
        while True:
            with active_lock:
                busy = bool(active)
                first = next(iter(active), None)
            if stopping["flag"] and not busy:
                return
            client.heartbeat(worker_name, "busy" if busy else "idle", first, gpu,
                             refresh_caps())
            for _ in range(int(HEARTBEAT_SEC * 2)):
                with active_lock:
                    busy = bool(active)
                if stopping["flag"] and not busy:
                    return
                time.sleep(0.5)

    heart = threading.Thread(target=beat, name="worker-heartbeat", daemon=True)
    heart.start()

    def kinds_wanted() -> list[str] | None:
        """ชนิดงานที่ยังรับได้ตอนนี้ — บอทเต็มโควตาแล้วก็ขอแค่ชนิดอื่น และกลับกัน."""
        caps_now = refresh_caps()
        allowed = runner.job_kinds(caps_now)
        with active_lock:
            bots = sum(1 for k in active.values() if k == "bot")
            others = sum(1 for k in active.values() if k != "bot")
        if bots >= max_bots:
            allowed = [k for k in allowed if k != "bot"]
        if others >= 1:
            allowed = [k for k in allowed if k == "bot"]
        return allowed

    def work(spec: dict) -> None:
        """ทำงานหนึ่งงานจนจบแล้วส่งผล — รันในเธรดของตัวเอง."""
        job_id = spec["id"]
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="mai-worker-") as tmpdir:
                result = _run_one(client, spec, Path(tmpdir), worker_name)
                result["worker"] = worker_name
                post_result(client, job_id, result)
            print(f"✅ เสร็จใน {time.monotonic() - started:.1f}s: {spec.get('title') or job_id}")
            print()
        except Exception as e:
            traceback.print_exc()
            try:
                client.post_json(f"/api/worker/jobs/{urllib.parse.quote(job_id)}/error",
                                 {"error": public_error(e)}, timeout=30)
            except WorkerError:
                pass
            print(f"❌ งานล้มเหลว: {e}", file=sys.stderr)
        finally:
            with active_lock:
                active.pop(job_id, None)

    backoff = poll
    while not stopping["flag"]:
        wanted = kinds_wanted()
        if not wanted:
            time.sleep(poll)          # เต็มทุกช่อง รอให้งานใดงานหนึ่งจบก่อน
            continue
        try:
            spec = client.claim(worker_name, wanted)
            backoff = poll
        except AuthError as e:
            # token ผิดคือปัญหาที่ต้องให้คนแก้ วนซ้ำไปก็ไม่หาย
            print()
            print(f"❌ {e}", file=sys.stderr)
            return 2
        except WorkerError as e:
            print(f"⚠️  {e} — ลองใหม่ใน {int(backoff)}s", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(POLL_ERROR_MAX, backoff * 2)
            continue

        if spec is None:
            with active_lock:
                idle = not active
            if once and idle:
                print("คิวว่าง — จบ (--once)")
                return 0
            time.sleep(poll)
            continue

        with active_lock:
            active[spec["id"]] = spec["kind"]
            running = len(active)
        print(f"▶️  รับงาน {spec['kind']}: {spec.get('title') or spec['id']}"
              f"   (กำลังทำอยู่ {running} งาน)")
        threading.Thread(target=work, args=(spec,), daemon=True,
                         name=f"job-{spec['id']}").start()

        if once:
            stopping["flag"] = True
            break

    # รองานที่ยังค้างให้จบก่อนออก (บอทที่อยู่ในห้องจะได้ปิดไฟล์เสียงเรียบร้อย)
    # ออกก่อนงานจบ = เธรด daemon ถูกฆ่าเงียบ ๆ งานค้างสถานะ running จนโดน reaper คืนคิว/ตีเป็น error
    drain_until = time.monotonic() + DRAIN_MAX_SEC
    while True:
        with active_lock:
            left = list(active)
        if not left:
            break
        if time.monotonic() >= drain_until:
            print(f"⚠️  รองานค้างครบ {int(DRAIN_MAX_SEC)}s แล้วยังไม่จบ — ออกทั้งที่ยังทำอยู่: "
                  + ", ".join(left), file=sys.stderr)
            break
        time.sleep(1)

    # ถูกสั่งหยุดตอนไม่มีงานค้าง
    print("👋 worker หยุดแล้ว")
    return 0
