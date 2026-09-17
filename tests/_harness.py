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
# BUG-045: blank *every* S3_* var (not just S3_BUCKET) plus the remote-blobs opt-in flag.
# config._load_dotenv() runs os.environ.setdefault() at import time (server.py/jobs.py import
# config), so any of these left unset here would pick up the owner's real R2 production
# credentials from .env into this test process — the exact class of accident BUG-045 already
# cost a full key rotation for. No test in this suite is allowed to hold a usable credential.
os.environ["S3_ENDPOINT"] = ""
os.environ["S3_BUCKET"] = ""
os.environ["S3_ACCESS_KEY_ID"] = ""
os.environ["S3_SECRET_ACCESS_KEY"] = ""
os.environ["S3_REGION"] = ""
os.environ["MEETING_AI_REMOTE_BLOBS"] = ""

import http.client  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from meeting_ai.web import backend, blobstore, jobs, server  # noqa: E402
from meeting_ai.web import store as filestore  # noqa: E402

__all__ = [
    "backend", "blobstore", "jobs", "server", "filestore",
    "FakeStore", "CloudCase", "LocalCase", "new_mid",
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

        # ---- auth / rate limit (BUG-010) ----
        self.users: dict[str, dict] = {}               # email -> {"id","email","name","is_admin","password"}
        self.invites: dict[str, dict] = {}              # code -> {"email"}
        self.rate_limits: dict[str, list] = {}          # key -> [expires_at (monotonic), hits] เลียน pgstore.rate_limits
        self.verify_password_calls: list[str] = []      # บันทึกทุกครั้งที่ "คำนวณ hash" (พิสูจน์ว่าไม่ถูกเรียกหลังบล็อก)

    # ---------- ผู้ใช้ / แชร์ ----------

    def user_for_session(self, token: str) -> dict | None:
        return self.sessions.get(token)

    def share_target(self, token: str) -> dict | None:
        return self.shares.get(token)

    def count_users(self) -> int:
        return max(len(self.sessions), 1)

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

    def job_done(self, job_id: str, meeting_id: str, step: str, warning: str | None = None) -> None:
        j = self.jobs.get(job_id)
        if j is None:
            return
        j["status"] = "done"
        j["step"] = step
        j["meeting_id"] = meeting_id
        j["warning"] = warning

    def set_translation(self, mid: str, lang: str, text: str) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        translations = dict(m.get("translations") or {})
        translations[lang] = text
        m["translations"] = translations
        return dict(m)

    def verify_password(self, email: str, password: str) -> dict | None:
        """เทสต์ BUG-011 เท่านั้นสนใจว่า body ผ่านเพดานหรือไม่ ไม่สนใจล็อกอินจริง — ไม่มี
        รหัสผ่านจริงเก็บใน FakeStore เลยตอบ None (อีเมล/รหัสผ่านผิด) เสมอ."""
        return None

    def set_visibility(self, mid: str, visibility: str) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        m["visibility"] = visibility
        return dict(m)
    # ---------- auth (BUG-010) — เลียน pgstore เท่าที่ _login/_signup เรียกใช้ ----------
    # ไม่มี scrypt จริงที่นี่ (ปลอมนี้ไม่ต้องช้าเท่าของจริง) — verify_password_calls ต่างหาก
    # คือสิ่งที่พิสูจน์ว่า "ไม่มีการคำนวณ hash หลังถูกบล็อก" (ดูตั๋ว ข้อพิสูจน์ D)

    def ensure_user(self, email: str, name: str | None = None, is_admin: bool = False) -> dict:
        email = email.strip().lower()
        u = self.users.get(email)
        if u is None:
            u = {"id": f"uid-{len(self.users) + 1}", "email": email, "name": name,
                 "is_admin": is_admin, "password": None}
            self.users[email] = u
        elif is_admin:
            u["is_admin"] = True
        return {k: v for k, v in u.items() if k != "password"}

    def set_password(self, user_id: str, password: str) -> None:
        for u in self.users.values():
            if u["id"] == user_id:
                u["password"] = password
                return

    def verify_password(self, email: str, password: str) -> dict | None:
        self.verify_password_calls.append(email)
        u = self.users.get(email.strip().lower())
        if u is None or u.get("password") is None or u["password"] != password:
            return None
        return {"id": u["id"], "email": u["email"], "name": u.get("name"),
                "is_admin": u.get("is_admin", False)}

    def has_password(self, email: str) -> bool:
        u = self.users.get(email.strip().lower())
        return bool(u and u.get("password") is not None)

    def create_session(self, user_id: str, user_agent: str | None = None) -> str:
        token = f"sess-{user_id}-{len(self.sessions) + 1}"
        u = next((v for v in self.users.values() if v["id"] == user_id), None)
        self.sessions[token] = {"id": user_id, "email": u["email"] if u else None,
                                 "name": u.get("name") if u else None,
                                 "is_admin": u.get("is_admin", False) if u else False}
        return token

    def drop_session(self, token: str) -> None:
        self.sessions.pop(token, None)

    def create_invite(self, created_by: str | None, email: str | None = None) -> str:
        code = f"inv-{len(self.invites) + 1}"
        self.invites[code] = {"email": email}
        return code

    def invite_email(self, code: str) -> tuple:
        inv = self.invites.get(code)
        if inv is None:
            return False, None
        return True, inv.get("email")

    def redeem_invite(self, code: str, user_id: str) -> bool:
        return self.invites.pop(code, None) is not None

    # ---------- rate limit ชั้น "Postgres" จำลอง (BUG-010) ----------
    # fixed window เหมือน pgstore.rate_hit เป๊ะ (ดู pgstore.py) แต่เก็บในหน่วยความจำของ
    # อินสแตนซ์ FakeStore เอง (คนละที่กับ web/ratelimit.py) เพื่อพิสูจน์ว่าชั้นนี้บล็อกได้เอง
    # จริง แม้ตัวนับในหน่วยความจำของ process (web/ratelimit.py) ถูกล้างทุกคำขอ (จำลองว่าคนละ
    # invocation บน Vercel ที่ไม่แชร์หน่วยความจำกัน)

    def rate_hit(self, key: str, limit: int, window: float) -> float:
        now = time.monotonic()
        entry = self.rate_limits.get(key)
        if entry is None or entry[0] <= now:
            entry = [now + window, 0]
            self.rate_limits[key] = entry
        entry[1] += 1
        if entry[1] > limit:
            return max(1.0, entry[0] - now)
        return 0.0

    def rate_reset(self, key: str) -> None:
        self.rate_limits.pop(key, None)

    # ---------- helper สร้างข้อมูลตั้งต้นให้เทสต์ ----------

    def add_account(self, email: str, password: str, user_id: str | None = None,
                     is_admin: bool = False, name: str | None = None) -> str:
        """สร้างบัญชีที่ตั้งรหัสผ่านไว้แล้ว (สำหรับเทส login) — ไม่ผ่าน scrypt จริง."""
        email = email.strip().lower()
        uid = user_id or f"uid-{len(self.users) + 1}"
        self.users[email] = {"id": uid, "email": email, "name": name,
                              "is_admin": is_admin, "password": password}
        return uid

    def add_user(self, token: str, user_id: str, email: str, is_admin: bool = False) -> None:
        self.sessions[token] = {"id": user_id, "email": email, "name": None, "is_admin": is_admin}

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
            cookies: dict[str, str] | None = None, content_type: str | None = None,
            headers: dict[str, str] | None = None, timeout: float = 20,
            extra_headers: dict[str, str] | None = None):
        # สองสาขาตั้งชื่อพารามิเตอร์นี้ต่างกัน (headers / extra_headers) รับทั้งคู่
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            hdrs = dict(headers or {})
            if cookies:
                hdrs["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
            if content_type:
                hdrs["Content-Type"] = content_type
            if extra_headers:
                hdrs.update(extra_headers)  # เช่น Authorization: Bearer ... สำหรับ worker API
            conn.request(method, path, body=data, headers=hdrs)
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

    def get(self, path: str, cookies: dict[str, str] | None = None,
            extra_headers: dict[str, str] | None = None):
        return self._do("GET", path, cookies=cookies, extra_headers=extra_headers)

    def post_json(self, path: str, body: dict | None = None,
                  cookies: dict[str, str] | None = None,
                  headers: dict[str, str] | None = None, timeout: float = 20,
                  extra_headers: dict[str, str] | None = None):
        return self._do("POST", path, data=json.dumps(body or {}).encode("utf-8"),
                        cookies=cookies, content_type="application/json",
                        headers=headers, timeout=timeout, extra_headers=extra_headers)

    def post_raw(self, path: str, data: bytes, cookies: dict[str, str] | None = None,
                 content_type: str = "application/json",
                 headers: dict[str, str] | None = None,
                 extra_headers: dict[str, str] | None = None):
        """เหมือน post_json แต่ส่ง body ดิบ — ใช้ทดสอบ body ว่างสนิท (0 ไบต์) ของ BUG-010."""
        return self._do("POST", path, data=data, cookies=cookies,
                        content_type=content_type, headers=headers,
                        extra_headers=extra_headers)

    def post_bytes(self, path: str, data: bytes, cookies: dict[str, str] | None = None,
                   extra_headers: dict[str, str] | None = None):
        return self._do("POST", path, data=data, cookies=cookies,
                        content_type="application/octet-stream", extra_headers=extra_headers)

    def delete(self, path: str, cookies: dict[str, str] | None = None):
        return self._do("DELETE", path, cookies=cookies)

    def patch_json(self, path: str, body: dict | None = None,
                   cookies: dict[str, str] | None = None,
                   headers: dict[str, str] | None = None, timeout: float = 20):
        return self._do("PATCH", path, data=json.dumps(body or {}).encode("utf-8"),
                        cookies=cookies, content_type="application/json",
                        headers=headers, timeout=timeout)

    def raw_request(self, method: str, path: str,
                    headers: dict[str, str] | list[tuple[str, str]],
                    body: bytes = b"", send_body: bool = True,
                    timeout: float = 5.0) -> tuple[int, str, float]:
        """ยิง HTTP ดิบผ่าน socket แทน http.client (BUG-011) — ใช้ตอนต้องคุม Content-Length

        เองแบบไม่ตรงกับความยาว body จริง (ประกาศใหญ่แต่ส่งนิดเดียว, ไม่ใช่ตัวเลข, ติดลบ, ว่าง,
        หายไปเลย) http.client คำนวณ Content-Length ให้เองตามความยาว body เสมอ ใช้พิสูจน์กรณี
        เหล่านี้ไม่ได้ คืน (status, response text ทั้งก้อนเท่าที่อ่านได้, วินาทีที่ใช้)
        status -1 = ส่ง body ไม่สำเร็จ, -2 = อ่านตอบกลับไม่สำเร็จ/timeout, -3 = ไม่ใช่ HTTP response
        เจตนาไม่อ่าน body ของ response ให้ครบ (พอเจอ header จบก็หยุด) เพราะบางเทสต์ส่ง body
        ใหญ่มากและ response อาจสะท้อนกลับมาใหญ่พอกัน — ใช้ text นี้เช็คแค่ status/หัวข้อความ error

        headers รับ dict (ปกติ) หรือ list ของ (key, value) — ต้องใช้ list เมื่อต้องส่ง header
        ชื่อซ้ำกันหลายบรรทัด (เช่น สอง `Content-Length`) ซึ่ง dict ทำไม่ได้
        """
        t0 = time.monotonic()
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        chunks: list[bytes] = []
        try:
            head = f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
            items = headers.items() if isinstance(headers, dict) else headers
            for k, v in items:
                head += f"{k}: {v}\r\n"
            head += "\r\n"
            s.sendall(head.encode("utf-8"))
            if send_body and body:
                try:
                    s.sendall(body)
                except OSError as e:
                    return -1, f"send failed: {e!r}", time.monotonic() - t0
            try:
                while True:
                    b = s.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
                    blob = b"".join(chunks)
                    if b"\r\n\r\n" in blob and len(blob) > 40:
                        break
            except OSError as e:
                return -2, f"recv failed: {e!r}", time.monotonic() - t0
        finally:
            s.close()
        text = b"".join(chunks).decode("utf-8", "replace")
        status = int(text.split(" ")[1]) if text.startswith("HTTP/") else -3
        return status, text, time.monotonic() - t0

    def raw_send_and_collect(self, data: bytes, timeout: float = 1.5) -> bytes:
        """ส่ง raw bytes ก้อนเดียว (คุมทั้งคำขอเอง รวม header/แนวการเข้ารหัส body) เข้า socket

        เดียวกัน แล้วอ่านทุกอย่างที่ตอบกลับมาจนกว่าคอนเนกชันจะปิด (EOF) หรือหมดเวลา — ต่างจาก
        `raw_request` ที่หยุดอ่านทันทีที่เจอ header block แรก ตัวนี้ตั้งใจอ่าน **ทุก response**
        บนคอนเนกชันเดียว ใช้นับจำนวน `HTTP/1.1 ` ทั้งหมด (BUG-011 request smuggling / keep-alive
        regression) คืน raw bytes ทั้งก้อน (ไม่ decode ให้ เพราะเทสต์พวกนี้สนใจ byte count ตรงๆ)
        """
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        chunks: list[bytes] = []
        try:
            s.sendall(data)
            try:
                while True:
                    b = s.recv(65536)
                    if not b:
                        break
                    chunks.append(b)
            except OSError:
                pass  # timeout หรือคอนเนกชันหลุด — ถือว่าอ่านจบเท่าที่ได้
        finally:
            s.close()
        return b"".join(chunks)

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
