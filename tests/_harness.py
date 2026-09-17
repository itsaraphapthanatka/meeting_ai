"""ฮาร์เนสร่วมของชุดทดสอบ P0 — ทุกไฟล์ทดสอบต้อง import จากที่นี่เป็นอันดับแรก.

ทำไมต้องตั้งค่า env ก่อน import meeting_ai.*: config.py อ่านตัวแปรแวดล้อมตอน import
(ค่าคงที่ระดับคลาส) และ .env ใช้ os.environ.setdefault (ค่าที่ตั้งไว้ชัดเจนชนะ .env เสมอ)
web/backend.py เลือก store (ไฟล์ หรือ Postgres) ตอน import เช่นกัน — ถ้า import
meeting_ai.* ไปก่อนแล้วค่อยตั้ง env ในเทสต์ จะไม่มีผลอะไรเลย

REMOTE_WORKER=1 ทำให้ jobs.start()/_enqueue()/create_bot() ข้าม _ensure_worker() เสมอ
(เธรดประมวลผลในเครื่องจะไม่ถูกสร้าง) และ _worker_caps() ในโหมด cloud จะถาม store แทนที่จะ
เรียก docker info จริง — ทั้งสองอย่างจำเป็นเพื่อไม่ให้เทสต์แตะ Docker/whisper/LLM จริง
"""

from __future__ import annotations

import os

os.environ["MEETING_AI_CLOUD"] = "0"
os.environ["DATABASE_URL"] = ""
os.environ["REMOTE_WORKER"] = "1"
os.environ["S3_BUCKET"] = ""

import hashlib  # noqa: E402
import http.client  # noqa: E402
import json  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from meeting_ai.web import backend, blobstore, jobs, server  # noqa: E402
from meeting_ai.web import store as filestore  # noqa: E402

__all__ = [
    "backend", "blobstore", "jobs", "server", "filestore",
    "FakeStore", "CloudCase", "LocalCase", "AuthCase", "new_mid",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_mid() -> str:
    """id รูปแบบเดียวกับ store.valid_id ยอมรับ — สุ่มพอที่จะไม่ชนกันข้ามเทสต์."""
    return filestore.new_id()


class FakeStore:
    """เลียนแบบ pgstore เท่าที่เส้นทางในชุดทดสอบ P0 เรียกใช้ — ไม่ต่อ DB จริง.

    เก็บทุกอย่างในหน่วยความจำของอินสแตนซ์เดียว (สร้างใหม่ทุก setUp) แยกจากกันเทสต์ต่อเทสต์
    ฟิลด์ `_owner_id`/`spec` เป็นรายละเอียดภายในของปลอมนี้เอง ไม่ใช่ทรงข้อมูลจริงของ pgstore
    (ของจริงเก็บ owner ไว้เป็นคอลัมน์ของ meetings และ spec เป็น jsonb ของ jobs) — ที่นี่จำลอง
    แค่พฤติกรรมที่ปลายทาง (ผลลัพธ์ของแต่ละเมธอด) ให้ตรงพอจะทดสอบชั้น server.py ได้
    """

    BOOTSTRAP_KEY = "signup_bootstrap"  # ต้องตรงกับ pgstore.BOOTSTRAP_KEY (ดู pgstore.py:241)

    def __init__(self) -> None:
        self.sessions: dict[str, dict] = {}          # token -> user dict
        self.shares: dict[str, dict] = {}             # token -> {"meeting_id", "can_edit"}
        self.meetings: dict[str, dict] = {}            # mid -> meeting (มี "owner_id" ตรงๆ)
        self.jobs: dict[str, dict] = {}                # job_id -> job (+ "spec" ภายใน)
        self.settings: dict[str, object] = {}
        self.job_active_calls: list[tuple] = []        # บันทึก (owner_id, meeting_id) ทุกครั้งที่เรียก
        self.workers: list[dict] = []                  # เทสต์ตั้งค่าตรงๆ ได้ (ดู workers_list())
        self.WEB_DIR: Path | None = None               # ตั้งจาก setUp ของเคส
        self.valid_id = filestore.valid_id
        self.new_id = filestore.new_id

        # ---- signup / invite (BUG-012) ----
        self.users: dict[str, dict] = {}               # email (lower) -> {"id","email","name","is_admin","password_hash"}
        self.invites: dict[str, dict] = {}             # code -> {"email","used_at","used_by","expired"}
        # ล็อกแยกต่อ "ตาราง" จำลอง — ล็อกเฉพาะช่วง statement เดียวกันในแต่ละเมธอด ไม่ครอบ
        # หลายเมธอดต่อกัน มิฉะนั้น fake นี้จะบังคับ atomicity ให้ทั้ง flow ของ _signup เอง
        # ทำให้เทสต์ concurrency ผ่านโดยไม่ได้พิสูจน์ว่า production code ใช้ผลลัพธ์ของ
        # claim_invite/claim_first_admin ถูกต้อง (ข้อสังเกตของ backend-dev ตอนรีวิว)
        self._invite_lock = threading.Lock()
        self._admin_lock = threading.Lock()
        self._user_lock = threading.Lock()
        # จุดซิงก์ที่เทสต์ concurrency ตั้งได้ (threading.Barrier ขนาด N เธรด) — ใช้แทน sleep
        # เพื่อบังคับให้ทุกเธรดผ่าน pre-check (count_users/invite_email) มาถึงจุดนี้พร้อมกัน
        # ก่อนแยกไปที่ claim_invite/claim_first_admin จริง (ดู has_password ข้างล่าง) แบบ
        # deterministic — ไม่ต้องเดาเวลา sleep ที่อาจ flaky ตามความเร็วเครื่อง
        self.sync_barrier: threading.Barrier | None = None

    # ---------- ผู้ใช้ / แชร์ ----------

    def user_for_session(self, token: str) -> dict | None:
        return self.sessions.get(token)

    def share_target(self, token: str) -> dict | None:
        return self.shares.get(token)

    def count_users(self) -> int:
        """จำนวนบัญชีจริง (self.users) — ตัดสิน first_run ใน _signup (BUG-012).

        ไม่ใช่ len(self.sessions) อีกต่อไป: session เป็นแค่ตั๋วล็อกอิน ไม่ใช่บัญชี และเทสต์
        signup ต้องเริ่มจากระบบว่างจริง (0) ได้ — add_user() ข้างล่างจึงลงทะเบียนผู้ใช้ใน
        self.users ให้ด้วยพร้อมกัน กัน CloudCase (ซึ่ง seed ผู้ใช้ผ่าน add_user) กลายเป็น
        "first_run เสมอ" โดยไม่ตั้งใจ.
        """
        return len(self.users)

    # ---------- การประชุม ----------

    def get(self, mid: str) -> dict | None:
        m = self.meetings.get(mid)
        return dict(m) if m else None

    def access(self, mid: str, user_id: str | None) -> str:
        """ตรงตาม pgstore.access: owner_id เป็น None ถือว่าใครล็อกอินอยู่ก็เป็น owner (ของเก่า)."""
        m = self.meetings.get(mid)
        if m is None:
            return "none"
        owner_id = m.get("owner_id")
        if user_id and (owner_id is None or owner_id == user_id):
            return "owner"
        if user_id and m.get("visibility") == "team":
            return "team"
        return "none"

    def search(self, query: str = "", user_id: str | None = None) -> list[dict]:
        out = []
        for m in self.meetings.values():
            owner = m.get("owner_id")
            if m.get("visibility") == "team" or owner == user_id or owner is None:
                out.append(dict(m))
        return out

    def stats(self, user_id: str | None = None) -> dict:
        return {"count": len(self.meetings), "total_duration": 0}

    def audio_path(self, meta: dict):
        return None

    # ---------- งาน ----------

    def job_get(self, job_id: str) -> dict | None:
        j = self.jobs.get(job_id)
        if j is None:
            return None
        out = {k: v for k, v in j.items() if k != "spec"}
        out["_spec"] = dict(j.get("spec") or {})
        return out

    def job_set_spec(self, job_id: str, spec: dict) -> bool:
        j = self.jobs.get(job_id)
        if j is None:
            return False
        j["spec"] = dict(spec)
        return True

    def job_start(self, job_id: str) -> dict | None:
        j = self.jobs.get(job_id)
        if j is None or j["status"] != "draft":
            return None
        j["status"] = "queued"
        j["step"] = "รอคิว"
        return self.job_get(job_id)

    def job_upsert(self, job_id: str, kind: str, title: str, spec: dict,
                   status: str = "queued", meeting_id: str | None = None) -> dict:
        existing = self.jobs.get(job_id, {})
        job = {
            "id": job_id,
            "meeting_id": meeting_id if meeting_id is not None else existing.get("meeting_id"),
            "kind": kind,
            "title": title,
            "status": status,
            "step": "รอคิว" if status == "queued" else "รออัปโหลดไฟล์",
            "progress": 0.0,
            "error": None,
            "warning": None,
            "created": existing.get("created") or _now(),
            "worker": existing.get("worker"),
            "spec": dict(spec or {}),
        }
        self.jobs[job_id] = job
        return self.job_get(job_id)

    def job_active(self, owner_id: str | None = None, meeting_id: str | None = None) -> list[dict]:
        self.job_active_calls.append((owner_id, meeting_id))
        out = []
        for j in self.jobs.values():
            if j["status"] not in ("queued", "running"):
                continue
            if owner_id is not None and (j.get("spec") or {}).get("owner_id") != owner_id:
                continue
            if meeting_id is not None and j.get("meeting_id") != meeting_id:
                continue
            out.append({k: v for k, v in j.items() if k != "spec"})
        return out

    def job_request_stop(self, job_id: str) -> str:
        j = self.jobs.get(job_id)
        if j is None or j["status"] not in ("queued", "running"):
            return ""
        j["status"] = "error"
        j["step"] = "ยกเลิกแล้ว"
        return "cancelled"

    # ---------- worker / ตั้งค่า ----------

    def workers_list(self) -> list[dict]:
        return [dict(w) for w in self.workers]

    def worker_capabilities(self) -> dict:
        return {"workers": 0}

    def get_setting(self, key: str, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key: str, value) -> None:
        self.settings[key] = value

    # ---------- helper สร้างข้อมูลตั้งต้นให้เทสต์ ----------

    def add_user(self, token: str, user_id: str, email: str, is_admin: bool = False) -> None:
        self.sessions[token] = {"id": user_id, "email": email, "name": None, "is_admin": is_admin}
        # เทสต์เดิม (CloudCase) seed ผู้ใช้ผ่านทางนี้ — ลงทะเบียนใน self.users ด้วยพร้อมกัน
        # ไม่งั้น count_users() จะเห็น 0 ทั้งที่มีคน login อยู่ 3 คน (ดู docstring count_users)
        self.users[email.strip().lower()] = {
            "id": user_id, "email": email, "name": None, "is_admin": is_admin,
            "password_hash": "seeded-for-test",
        }

    def add_invite(self, code: str, email: str | None = None, expired: bool = False,
                   used_by: str | None = None) -> None:
        """เตรียมรหัสเชิญให้เทสต์ — email=None แปลว่าใช้กับอีเมลไหนก็ได้.

        used_by ตั้งไว้ล่วงหน้าได้เพื่อจำลอง "ถูกใช้ไปแล้ว" (used_at ก็ถูกตั้งตามไปด้วย
        เหมือน pgstore จริงที่ used_by ไม่มีทางเป็นจริงได้ถ้า used_at ยังว่าง)
        """
        self.invites[code] = {
            "email": (email.strip().lower() if email else None),
            "used_at": bool(used_by), "used_by": used_by, "expired": expired,
        }

    # ---------- signup / invite atomic (BUG-012) ----------
    # ทั้งเจ็ดเมธอดข้างล่างจำลอง pgstore.py ตัวจริงทีละ "statement เดียว" ตาม docstring ของแต่ละ
    # ฟังก์ชันต้นทาง (pgstore.py:77-260) — ล็อกเฉพาะช่วงเดียวกับที่ pgstore ให้ Postgres ล็อกแถวเอง
    # เท่านั้น (ไม่ใช่ครอบทั้ง _signup) ไม่งั้นการแข่งกันจะไม่มีทางเกิดในเทสต์เลย

    def invite_email(self, code: str) -> tuple[bool, str | None]:
        """pre-check เท่านั้น (ตรง pgstore.invite_email) — ไม่ใช่จุดตัดสินสิทธิ์ ไม่ต้องล็อก."""
        inv = self.invites.get(code)
        if inv is None or inv["used_by"] is not None or inv["used_at"] or inv["expired"]:
            return (False, None)
        return (True, inv["email"])

    def has_password(self, email: str) -> bool:
        """pre-check ธรรมดา (ตรง pgstore.has_password) — แต่เป็นจุดที่ _signup เรียกเสมอ
        ไม่ว่า first_run หรือไม่ (ดู server.py:804) และเรียก **หลัง** pre-check อื่นทั้งหมด
        **ก่อน** เข้าสู่ claim_invite/claim_first_admin จริง — จึงเป็นจุดที่แม่นที่สุดในการ
        บังคับให้ N เธรดมาถึงพร้อมกันก่อนแยกไปแข่งกันที่จุดตัดสิน (แทน sleep เพื่อไม่ flaky).
        """
        if self.sync_barrier is not None:
            try:
                self.sync_barrier.wait()
            except threading.BrokenBarrierError:
                pass
        email = (email or "").strip().lower()
        u = self.users.get(email)
        return bool(u and u.get("password_hash"))

    def claim_invite(self, code: str, email: str) -> bool:
        """ตรง pgstore.claim_invite — `update ... where ... returning` แบบ statement เดียว.

        ล็อกครอบแค่การอ่าน+เขียนแถวนี้เท่านั้น (ไม่ครอบ ensure_user/set_password ที่ตามมา
        ใน server.py) ตามที่ backend-dev ขอ — มิฉะนั้นเทสต์จะผ่านเพราะ fake เองบังคับ
        atomicity ให้ทั้ง flow ไม่ใช่เพราะ production code ใช้ผลลัพธ์ถูกต้อง
        """
        email = (email or "").strip().lower()
        with self._invite_lock:
            inv = self.invites.get(code)
            if inv is None:
                return False
            if inv["used_by"] is not None or inv["used_at"] or inv["expired"]:
                return False
            if inv["email"] is not None and inv["email"] != email:
                return False
            inv["used_at"] = True
            return True

    def attach_invite(self, code: str, user_id: str) -> bool:
        """ตรง pgstore.attach_invite — ผูกเจ้าของหลังจองสำเร็จ (เรียกหลัง ensure_user จริง)."""
        with self._invite_lock:
            inv = self.invites.get(code)
            if inv is None or inv["used_by"] is not None or not inv["used_at"]:
                return False
            inv["used_by"] = user_id
            return True

    def claim_first_admin(self, email: str) -> bool:
        """ตรง pgstore.claim_first_admin — คีย์ settings[BOOTSTRAP_KEY] จองได้คนเดียว
        (อีเมลเดิมจองซ้ำได้ = idempotent ตาม docstring ต้นทาง, อีเมลอื่นชนแล้วแพ้).
        """
        email = (email or "").strip().lower()
        with self._admin_lock:
            current = self.settings.get(self.BOOTSTRAP_KEY)
            if current is not None and current != email:
                return False
            self.settings[self.BOOTSTRAP_KEY] = email
            return True

    def ensure_user(self, email: str, name: str | None = None, is_admin: bool = False) -> dict:
        """ตรง pgstore.ensure_user — upsert on conflict(email) แบบ statement เดียว."""
        email = (email or "").strip().lower()
        with self._user_lock:
            existing = self.users.get(email)
            if existing is None:
                existing = {
                    "id": f"u-{secrets.token_hex(6)}", "email": email,
                    "name": name, "is_admin": is_admin, "password_hash": None,
                }
                self.users[email] = existing
            elif name is not None:
                existing["name"] = name
            return {k: v for k, v in existing.items() if k != "password_hash"}

    def set_password(self, user_id: str, password: str) -> None:
        """ตรง pgstore.set_password — เก็บแค่ hash (sha256 พอสำหรับ fake) ไม่เก็บรหัสผ่านจริง."""
        with self._user_lock:
            for u in self.users.values():
                if u["id"] == user_id:
                    u["password_hash"] = hashlib.sha256(password.encode("utf-8")).hexdigest()
                    return

    def create_session(self, user_id: str, user_agent: str | None = None) -> str:
        """ตรง pgstore.create_session — _signup/_login เรียกต่อทันทีหลังสมัคร/ล็อกอินสำเร็จ
        เพื่อออกคุกกี้ (server.py:760, _login_response) — ไม่มีเมธอดนี้ _signup จะ 500 ทันที
        แม้จุดตัดสิน BUG-012 จะผ่านแล้วก็ตาม
        """
        token = f"tok-{secrets.token_hex(16)}"
        user = next((u for u in self.users.values() if u["id"] == user_id), None)
        if user is not None:
            self.sessions[token] = {k: v for k, v in user.items() if k != "password_hash"}
        else:
            self.sessions[token] = {"id": user_id, "email": None, "name": None, "is_admin": False}
        return token

    def add_meeting(self, mid: str, owner_id: str | None, title: str = "Meeting",
                     visibility: str = "private", **extra) -> None:
        # owner_id เป็นคีย์สาธารณะจริงๆ (ดู pgstore._meta_from_row row[1]) ไม่ใช่รายละเอียด
        # ภายในของปลอมนี้ — server.py อ่าน meeting.get("owner_id") ตรงๆ เป็น fallback เจ้าของงาน
        m = {
            "id": mid, "title": title, "owner_id": owner_id, "visibility": visibility,
            "summary": "", "segments_list": [], "transcript": "", "translations": {},
            "audio": None, "language": "th", "duration": 0, "segments": 0,
            "source": "upload", "speakers": [], "template": "general",
            "created": _now(), "updated": _now(),
        }
        m.update(extra)
        self.meetings[mid] = m

    def add_share(self, token: str, meeting_id: str, can_edit: bool = False) -> None:
        self.shares[token] = {"meeting_id": meeting_id, "can_edit": can_edit}

    def add_draft(self, mid: str, owner_id: str | None, tracks: dict | None = None,
                  title: str = "Draft") -> None:
        self.jobs[mid] = {
            "id": mid, "meeting_id": None, "kind": "process", "status": "draft",
            "step": "รออัปโหลดไฟล์", "progress": 0.0, "title": title,
            "error": None, "warning": None, "created": _now(), "worker": None,
            "spec": {"id": mid, "title": title, "owner_id": owner_id, "tracks": dict(tracks or {})},
        }

    def add_job(self, job_id: str, owner_id: str | None, title: str = "Job",
                status: str = "running", meeting_id: str | None = None,
                kind: str = "process") -> None:
        self.jobs[job_id] = {
            "id": job_id, "meeting_id": meeting_id, "kind": kind, "status": status,
            "step": "กำลังทำ", "progress": 0.2, "title": title,
            "error": None, "warning": None, "created": _now(), "worker": None,
            "spec": {"id": job_id, "title": title, "owner_id": owner_id},
        }


class _HttpCaseMixin:
    """ยิง HTTP เข้า self.httpd (ตั้งใน setUp ของคลาสลูก) ด้วย http.client (ไม่โยน exception ที่ 4xx)."""

    def _do(self, method: str, path: str, data: bytes | None = None,
            cookies: dict[str, str] | None = None, content_type: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            headers = {}
            if cookies:
                headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            if content_type:
                headers["Content-Type"] = content_type
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()
        body = None
        if raw:
            try:
                body = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                body = raw
        return resp.status, body, dict(resp.getheaders())

    def get(self, path: str, cookies: dict[str, str] | None = None):
        return self._do("GET", path, cookies=cookies)

    def post_json(self, path: str, body: dict | None = None,
                  cookies: dict[str, str] | None = None):
        return self._do("POST", path, data=json.dumps(body or {}).encode("utf-8"),
                        cookies=cookies, content_type="application/json")

    def post_bytes(self, path: str, data: bytes, cookies: dict[str, str] | None = None):
        return self._do("POST", path, data=data, cookies=cookies,
                        content_type="application/octet-stream")

    def delete(self, path: str, cookies: dict[str, str] | None = None):
        return self._do("DELETE", path, cookies=cookies)

    def patch_json(self, path: str, body: dict | None = None,
                   cookies: dict[str, str] | None = None):
        return self._do("PATCH", path, data=json.dumps(body or {}).encode("utf-8"),
                        cookies=cookies, content_type="application/json")

    def _start_server(self) -> None:
        self.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

        def _stop() -> None:
            try:
                self.httpd.shutdown()
            finally:
                self.httpd.server_close()

        self.addCleanup(_stop)


class CloudCase(_HttpCaseMixin, unittest.TestCase):
    """โหมด cloud จำลองด้วย FakeStore — ไม่ต่อ Postgres จริง.

    หว่านเมล็ดมาตรฐานตาม docstring ของทีม (uid-a/tokA, uid-b/tokB, uid-adm/tokAdm,
    M1/M2, shr1/shr2, D_A/D_B, J_A/J_B, M1.tr.en) ให้ครบทุกเทสต์ที่สืบทอดจากคลาสนี้
    id จริงถูกสุ่มใหม่ทุกครั้ง (new_mid()) กันชนกันข้ามเทสต์ที่รันขนาน/ไม่เรียงลำดับ
    """

    def setUp(self) -> None:
        self.store = FakeStore()
        self._tmp = tempfile.mkdtemp(prefix="mai-cloud-test-")
        self.store.WEB_DIR = Path(self._tmp)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

        patches = [
            mock.patch.object(backend, "cloud", True),
            mock.patch.object(backend, "store", self.store),
            mock.patch.object(jobs, "cloud", True),
            mock.patch.object(jobs, "store", self.store),
            mock.patch.object(server, "store", self.store),
            mock.patch.object(server.Handler, "log_message", lambda *a, **k: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        blobstore.reset()
        self.addCleanup(blobstore.reset)

        self._start_server()
        self._seed()

    def _seed(self) -> None:
        s = self.store
        self.uid_a, self.tokA = "uid-a", "tokA"
        self.uid_b, self.tokB = "uid-b", "tokB"
        self.uid_adm, self.tokAdm = "uid-adm", "tokAdm"
        s.add_user(self.tokA, self.uid_a, "a@example.com")
        s.add_user(self.tokB, self.uid_b, "b@example.com")
        s.add_user(self.tokAdm, self.uid_adm, "admin@example.com", is_admin=True)

        self.M1 = new_mid()
        self.M2 = new_mid()
        s.add_meeting(self.M1, self.uid_a, title="Meeting 1", visibility="private")
        s.add_meeting(self.M2, self.uid_b, title="Meeting 2", visibility="private")

        self.shr1, self.shr2 = "shr1", "shr2"
        s.add_share(self.shr1, self.M1, can_edit=False)
        s.add_share(self.shr2, self.M1, can_edit=True)

        self.D_A = new_mid()
        self.D_B = new_mid()
        s.add_draft(self.D_A, self.uid_a)
        s.add_draft(self.D_B, self.uid_b)

        self.J_A = new_mid()
        self.J_B = new_mid()
        s.add_job(self.J_A, self.uid_a, title="A secret", status="running")
        s.add_job(self.J_B, self.uid_b, title="B secret", status="running")

        self.M1_tr_en = f"{self.M1}.tr.en"
        s.jobs[self.M1_tr_en] = {
            "id": self.M1_tr_en, "meeting_id": self.M1, "kind": "translate", "status": "queued",
            "step": "รอคิว", "progress": 0.0, "title": "Meeting 1",
            "error": None, "warning": None, "created": _now(), "worker": None,
            "spec": {"id": self.M1_tr_en, "title": "Meeting 1", "owner_id": None,
                     "meeting": self.M1, "lang": "en"},
        }


class AuthCase(_HttpCaseMixin, unittest.TestCase):
    """โหมด cloud สำหรับเทสต์ signup/invite (BUG-012) — ต่างจาก CloudCase ตรงที่ **ไม่** เรียก
    _seed(): ระบบเริ่มว่างเปล่าจริง (count_users() == 0) เพื่อควบคุม first_run และปล่อยให้
    แต่ละเทสต์สร้างผู้ใช้/รหัสเชิญของตัวเองผ่าน self.signup()/self.store.add_invite() —
    จำเป็นสำหรับเทสต์ concurrency ที่ต้องรู้แน่ชัดว่าระบบว่างกี่คนก่อนยิงพร้อมกัน
    """

    def setUp(self) -> None:
        self.store = FakeStore()
        self._tmp = tempfile.mkdtemp(prefix="mai-auth-test-")
        self.store.WEB_DIR = Path(self._tmp)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

        patches = [
            mock.patch.object(backend, "cloud", True),
            mock.patch.object(backend, "store", self.store),
            mock.patch.object(jobs, "cloud", True),
            mock.patch.object(jobs, "store", self.store),
            mock.patch.object(server, "store", self.store),
            mock.patch.object(server.Handler, "log_message", lambda *a, **k: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        blobstore.reset()
        self.addCleanup(blobstore.reset)

        self._start_server()

    def signup(self, email: str, password: str = "Passw0rd!23", invite: str = "",
              name: str | None = None):
        """POST /api/auth/signup — คืน (status, body, headers) เหมือน self.post_json อื่นๆ."""
        body: dict = {"email": email, "password": password}
        if invite:
            body["invite"] = invite
        if name is not None:
            body["name"] = name
        return self.post_json("/api/auth/signup", body)


class LocalCase(_HttpCaseMixin, unittest.TestCase):
    """โหมดไฟล์ (ไม่มีล็อกอิน) — ใช้ store จริง (filestore) แต่ชี้ WEB_DIR ไปที่ temp dir.

    store.py คำนวณ INDEX_PATH/SETTINGS_PATH = WEB_DIR / "..." ครั้งเดียวตอน import
    (ไม่ใช่ property ที่คำนวณใหม่ทุกครั้ง) — แพตช์แค่ WEB_DIR เฉยๆ ตามที่ PROJECT-CONTEXT.md
    แนะนำไว้ "เพียงพอ" นั้นไม่จริง: _load_index/_save_index/get_setting/set_setting จะยัง
    อ่าน/เขียนไฟล์ index.json และ settings.json ตัวจริงของเจ้าของเครื่องอยู่ดี ต้องแพตช์
    ทั้งสามชื่อพร้อมกันให้ชี้เข้า temp dir เดียวกัน (ดู Lessons ในรายงาน)
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="mai-local-test-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        web_dir = Path(self._tmp)

        patches = [
            mock.patch.object(filestore, "WEB_DIR", web_dir),
            mock.patch.object(filestore, "INDEX_PATH", web_dir / "index.json"),
            mock.patch.object(filestore, "SETTINGS_PATH", web_dir / "settings.json"),
            mock.patch.object(server.Handler, "log_message", lambda *a, **k: None),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        blobstore.reset()
        self.addCleanup(blobstore.reset)

        self._start_server()
