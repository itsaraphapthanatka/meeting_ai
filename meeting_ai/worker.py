"""worker: ดึงงานจากเซิร์ฟเวอร์ (ในเครื่องหรือบน cloud) มาถอดเสียงด้วย GPU เครื่องนี้.

ใช้ตอน deploy หน้าเว็บขึ้น cloud ที่ไม่มี GPU — cloud ถือคิวกับข้อมูล เครื่องนี้ทำงานหนัก
    ฝั่งเซิร์ฟเวอร์: ตั้ง REMOTE_WORKER=1 และ WORKER_TOKEN
    ฝั่งนี้:        mai worker --api https://xxx.vercel.app --token <WORKER_TOKEN>

เสียงถูกดาวน์โหลดมาไว้ในโฟลเดอร์ชั่วคราวและลบทิ้งเมื่อทำงานเสร็จ
"""

from __future__ import annotations

import json
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
from .config import config
from .web.blobstore import open_url

POLL_IDLE = 3.0        # วินาที รอเมื่อคิวว่าง
POLL_ERROR_MAX = 60.0  # เพดาน backoff เมื่อต่อเซิร์ฟเวอร์ไม่ได้
PROGRESS_MIN_GAP = 1.5  # ไม่ยิง progress ถี่กว่านี้ (นอกจากเปลี่ยนขั้น)
HEARTBEAT_SEC = 20.0    # เต้นบอกเซิร์ฟเวอร์ว่ายังอยู่ (ฝั่งนั้นถือว่าหลุดที่ 75 วิ)
CHUNK = 1024 * 256


class WorkerError(RuntimeError):
    pass


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
            raise WorkerError(f"HTTP {e.code} จาก {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise WorkerError(f"ต่อเซิร์ฟเวอร์ไม่ได้ ({path}): {e.reason}") from e

    def get_json(self, path: str):
        return self._request("GET", path)[1]

    def post_json(self, path: str, payload: dict, timeout: int | None = None):
        return self._request("POST", path, data=json.dumps(payload).encode("utf-8"),
                             ctype="application/json", timeout=timeout)[1]

    def claim(self, worker: str) -> dict | None:
        payload = json.dumps({"worker": worker}).encode("utf-8")
        status, body = self._request("POST", "/api/worker/claim",
                                     data=payload, ctype="application/json")
        return body if status == 200 else None

    def heartbeat(self, worker: str, status: str, job: str | None = None,
                  gpu: str | None = None) -> None:
        """บอกเซิร์ฟเวอร์ว่ายังอยู่ — ทำแม้ตอนว่าง หน้าเว็บจะได้เห็นว่ามีเครื่องพร้อม."""
        try:
            self.post_json("/api/worker/heartbeat",
                           {"worker": worker, "status": status, "job": job, "gpu": gpu},
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


def _run_one(client: Client, spec: dict, tmp: Path) -> dict:
    job_id = spec["id"]
    last = {"at": 0.0, "step": ""}

    def progress(step: str, value: float) -> None:
        now = time.monotonic()
        if step == last["step"] and now - last["at"] < PROGRESS_MIN_GAP:
            return
        last.update(at=now, step=step)
        print(f"   {int(value * 100):3d}%  {step}")
        try:
            client.post_json(f"/api/worker/jobs/{urllib.parse.quote(job_id)}/progress",
                             {"step": step, "progress": value}, timeout=20)
        except WorkerError as e:
            print(f"   (รายงาน progress ไม่ได้: {e})", file=sys.stderr)

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

    # ไฟล์เสียงผสมเกิดขึ้นบนเครื่องนี้ ต้องส่งขึ้นไปให้เซิร์ฟเวอร์เก็บแล้วใช้ path ของฝั่งนั้น
    playback = result.get("playback")
    if playback:
        src = Path(playback)
        print("   อัปโหลดไฟล์เสียงผสม …")
        target = spec.get("playback_upload_url")
        if target:
            client.upload(target, src)
            result["playback"] = spec.get("playback_key") or src.name
        else:
            out = client.upload(
                f"/api/worker/jobs/{urllib.parse.quote(job_id)}/audio?ext={src.suffix.lstrip('.')}",
                src,
            )
            result["playback"] = out.get("playback") or None
    return result


def run(api: str, token: str, once: bool = False, poll: float = POLL_IDLE,
        name: str | None = None) -> int:
    client = Client(api, token)
    stopping = {"flag": False}
    current: dict = {"id": None, "status": "idle"}
    worker_name = (name or socket.gethostname())[:80]
    gpu = describe_gpu()

    def on_signal(signum, frame):
        stopping["flag"] = True
        print("\n⏹️  จะหยุดหลังงานปัจจุบันจบ (กดอีกครั้งเพื่อหยุดทันที)")
        signal.signal(signum, signal.SIG_DFL)

    try:
        signal.signal(signal.SIGINT, on_signal)
    except ValueError:
        pass  # ไม่ใช่เธรดหลัก

    print(f"🛠️  worker พร้อม — เซิร์ฟเวอร์: {client.api}")
    print(f"   ชื่อเครื่อง: {worker_name}" + (f"   GPU: {gpu}" if gpu else "   (ไม่มี GPU)"))
    if not config.llm_api_key:
        print("⚠️  worker ตัวนี้ยังไม่มี LLM_API_KEY — ถอดเสียงได้แต่จะสรุปไม่ได้")
    print("   กด Ctrl+C เพื่อหยุด\n", flush=True)

    # เต้นทุก HEARTBEAT_SEC วินาที ให้หน้าเว็บรู้ว่าเครื่องนี้ยังอยู่ แม้ตอนว่าง
    def beat() -> None:
        while not stopping["flag"]:
            client.heartbeat(worker_name, current["status"], current["id"], gpu)
            for _ in range(int(HEARTBEAT_SEC * 2)):
                if stopping["flag"]:
                    return
                time.sleep(0.5)

    heart = threading.Thread(target=beat, name="worker-heartbeat", daemon=True)
    heart.start()

    backoff = poll
    while not stopping["flag"]:
        try:
            spec = client.claim(worker_name)
            backoff = poll
        except AuthError as e:
            # token ผิดคือปัญหาที่ต้องให้คนแก้ วนซ้ำไปก็ไม่หาย
            print(f"\n❌ {e}", file=sys.stderr)
            return 2
        except WorkerError as e:
            print(f"⚠️  {e} — ลองใหม่ใน {int(backoff)}s", file=sys.stderr)
            time.sleep(backoff)
            backoff = min(POLL_ERROR_MAX, backoff * 2)
            continue

        if spec is None:
            if once:
                print("คิวว่าง — จบ (--once)")
                return 0
            time.sleep(poll)
            continue

        job_id = spec["id"]
        current["id"] = job_id
        current["status"] = "busy"
        print(f"▶️  รับงาน {spec['kind']}: {spec.get('title') or job_id}")
        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="mai-worker-") as tmpdir:
                result = _run_one(client, spec, Path(tmpdir))
                result["worker"] = worker_name
                client.post_json(f"/api/worker/jobs/{urllib.parse.quote(job_id)}/result",
                                 result, timeout=120)
            print(f"✅ เสร็จใน {time.monotonic() - started:.1f}s\n")
        except Exception as e:
            traceback.print_exc()
            try:
                client.post_json(f"/api/worker/jobs/{urllib.parse.quote(job_id)}/error",
                                 {"error": str(e)}, timeout=30)
            except WorkerError:
                pass
            print(f"❌ งานล้มเหลว: {e}\n", file=sys.stderr)
        finally:
            current["id"] = None
            current["status"] = "idle"
            client.heartbeat(worker_name, "idle", None, gpu)

        if once:
            stopping["flag"] = True
            return 0

    # ถูกสั่งหยุดตอนไม่มีงานค้าง
    print("👋 worker หยุดแล้ว")
    return 0
