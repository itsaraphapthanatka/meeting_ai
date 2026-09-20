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

import hashlib  # noqa: E402
import http.client  # noqa: E402
import json  # noqa: E402
import secrets  # noqa: E402
import shutil  # noqa: E402
import socket  # noqa: E402
import tempfile  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from unittest import mock  # noqa: E402

from meeting_ai.web import actionitems, backend, blobstore, jobs, server  # noqa: E402
from meeting_ai.web import store as filestore  # noqa: E402

__all__ = [
    "actionitems", "backend", "blobstore", "jobs", "server", "filestore",
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

        # ---- auth / signup / invite / rate limit (BUG-010 + BUG-012 รวมกัน) ----
        # ทั้งสองตั๋วแก้คนละจุดของ auth pipeline เดียวกัน: BUG-010 เติม rate limit หน้า
        # _login()/_signup() ส่วน BUG-012 เปลี่ยนจุดตัดสินสิทธิ์ของ _signup() จาก "สร้างผู้ใช้
        # ก่อนแล้วค่อย redeem" (มีช่องแข่ง) เป็น claim_invite/claim_first_admin แบบ atomic
        # ก่อนเสมอ (pgstore.py:238-313) — ทรงข้อมูลที่นี่จึงต้องตรงกับ pgstore.py จริง (BUG-012
        # เป็นฝ่ายกำหนด เพราะ signup flow จริงเรียก ensure_user/set_password/has_password ผ่าน
        # server.py._signup() เทียบ pgstore.py:81-138): self.users คีย์ด้วยอีเมลตัวพิมพ์เล็ก
        # เก็บ "password_hash" (ไม่ใช่ "password" ตรงๆ) BUG-010 ไม่แคร์ทรงภายในนี้ แค่ต้องมี
        # verify_password_calls นับจำนวนครั้งจริงที่ verify_password ถูกเรียก เพื่อพิสูจน์ว่า
        # ไม่มีการคำนวณ hash หลังถูกบล็อก (ดูตั๋ว BUG-010 ข้อพิสูจน์ D)
        self.users: dict[str, dict] = {}          # email (lower) -> {"id","email","name","is_admin","password_hash"}
        self.invites: dict[str, dict] = {}         # code -> {"email","used_at","used_by","expired"}
        self.rate_limits: dict[str, list] = {}     # key -> [expires_at (monotonic), hits] เลียน pgstore.rate_limits
        self.verify_password_calls: list[str] = [] # บันทึกทุกครั้งที่ "คำนวณ hash" (พิสูจน์ว่าไม่ถูกเรียกหลังบล็อก)
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

    def create(self, mid: str, title: str, audio_name: str, source: str, language: str,
               duration: float, segments: list[dict], summary: str,
               summary_error: str | None = None, template: str = "general",
               speakers: list[str] | None = None, owner_id: str | None = None,
               visibility: str = "private", peaks: list[int] | None = None) -> dict:
        """เทียบเท่า store.create()/pgstore.create() — ใช้ทดสอบ jobs.apply_result() เส้น process/bot

        โดยไม่ต่อ Postgres จริง (BUG-048): ต้องเก็บค่าที่ sanitize.py คัดมาแล้วตรงๆ ไม่ตรวจซ้ำ
        เหมือนกับ store จริงสองตัว — ถ้า apply_result() ส่ง NaN/Infinity มาที่นี่ (คือบั๊กเดิม)
        เทสต์จะเห็นค่าที่พังในการประชุมทันที
        """
        m = {
            "id": mid, "title": title, "owner_id": owner_id, "visibility": visibility,
            "language": language, "duration": round(duration, 1), "segments": len(segments),
            "audio": audio_name, "source": source, "template": template,
            "speakers": list(speakers or []), "summary_error": summary_error,
            "edited": False, "transcript_edited": False,
            "created": _now(), "updated": _now(),
            "summary": summary, "segments_list": list(segments), "translations": {},
            "peaks": list(peaks) if peaks else None,
            "action_items": actionitems.reconcile(None, summary),
            "qa": [],
        }
        self.meetings[mid] = m
        return dict(m)

    def set_translation(self, mid: str, lang: str, text: str) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        translations = dict(m.get("translations") or {})
        translations[lang] = text
        m["translations"] = translations
        m["updated"] = _now()
        return dict(m)

    def set_summary(self, mid: str, summary: str, error: str | None = None) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        m["summary"] = summary
        m["summary_error"] = error
        m["action_items"] = actionitems.reconcile(m.get("action_items"), summary)
        m["updated"] = _now()
        return dict(m)

    # ---------- ถาม-ตอบ (BACKLOG #54) ----------

    def add_qa(self, mid: str, question: str, answer: str,
               enough: bool = True) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        rows = [dict(x) for x in (m.get("qa") or [])]
        rows.append({"id": secrets.token_hex(6), "question": question, "answer": answer,
                     "enough": bool(enough), "asked": _now()})
        m["qa"] = rows[-20:]
        m["updated"] = _now()
        return dict(m)

    def delete_qa(self, mid: str, qa_id: str) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        rows = [dict(x) for x in (m.get("qa") or [])]
        left = [x for x in rows if x.get("id") != qa_id]
        if len(left) == len(rows):
            return None
        m["qa"] = left
        m["updated"] = _now()
        return dict(m)

    # ---------- action items (BACKLOG #53) ----------
    # ของปลอมนี้ต้องมีเมธอดครบเหมือน pgstore จริง ไม่งั้นเส้น cloud จะตายด้วย AttributeError
    # ตอนรันจริงแทนที่จะแดงในเทสต์ — บทเรียนเดียวกับตอน rate_hit/rate_reset หายไป

    def set_action_item(self, mid: str, item_id: str, *, done: bool | None = None,
                        assignee: str | None = None) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        items = [dict(x) for x in (m.get("action_items") or [])]
        hit = next((x for x in items if x.get("id") == item_id), None)
        if hit is None:
            return None
        if done is not None:
            hit["done"] = bool(done)
        if assignee is not None:
            hit["assignee"] = assignee.strip()[:actionitems.MAX_TEXT]
            hit["assignee_edited"] = True
        m["action_items"] = items
        m["updated"] = _now()
        return dict(m)

    def delete_action_item(self, mid: str, item_id: str) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        items = [dict(x) for x in (m.get("action_items") or [])]
        left = [x for x in items if x.get("id") != item_id]
        if len(left) == len(items):
            return None
        m["action_items"] = left
        m["updated"] = _now()
        return dict(m)

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

    def job_done(self, job_id: str, meeting_id: str | None, step: str,
                 warning: str | None) -> None:
        j = self.jobs.get(job_id)
        if j is None:
            return
        j.update(status="done", step=step, progress=1.0, meeting_id=meeting_id, warning=warning)

    def job_fail(self, job_id: str, error: str) -> None:
        j = self.jobs.get(job_id)
        if j is None:
            return
        j.update(status="error", step="ผิดพลาด", error=error)

    def job_progress(self, job_id: str, step: str, progress: float) -> None:
        j = self.jobs.get(job_id)
        if j is None:
            return
        j.update(status="running", step=step, progress=max(0.0, min(1.0, progress)))

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

    def set_visibility(self, mid: str, visibility: str) -> dict | None:
        m = self.meetings.get(mid)
        if m is None:
            return None
        m["visibility"] = visibility
        return dict(m)

    # ---------- auth (BUG-010) — เลียน pgstore เท่าที่ _login เรียกใช้ ----------
    # ไม่มี scrypt จริงที่นี่ (ปลอมนี้ไม่ต้องช้าเท่าของจริง — sha256 ธรรมดาพอ) —
    # verify_password_calls ต่างหากคือสิ่งที่พิสูจน์ว่า "ไม่มีการคำนวณ hash หลังถูกบล็อก"
    # (ดูตั๋ว ข้อพิสูจน์ D) ทรง users/password_hash เดียวกับที่ ensure_user/set_password
    # (ส่วน BUG-012 ข้างล่าง) ใช้ — ไฟล์นี้มี "จุดเขียนรหัสผ่าน" เดียว (set_password + add_account)
    # และ "จุดอ่านรหัสผ่าน" เดียว (verify_password) ไม่ให้มีสองทรงชนกันเหมือนก่อนรวม PR

    def verify_password(self, email: str, password: str) -> dict | None:
        self.verify_password_calls.append(email)
        u = self.users.get((email or "").strip().lower())
        if u is None or not u.get("password_hash"):
            return None
        if u["password_hash"] != hashlib.sha256(password.encode("utf-8")).hexdigest():
            return None
        return {"id": u["id"], "email": u["email"], "name": u.get("name"),
                "is_admin": u.get("is_admin", False)}

    def drop_session(self, token: str) -> None:
        self.sessions.pop(token, None)

    def create_invite(self, created_by: str | None, email: str | None = None) -> str:
        code = f"inv-{len(self.invites) + 1}"
        self.invites[code] = {"email": (email.strip().lower() if email else None),
                              "used_at": False, "used_by": None, "expired": False}
        return code

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
        """สร้างบัญชีที่ตั้งรหัสผ่านไว้แล้ว (สำหรับเทส login) — ไม่ผ่าน scrypt จริง แต่เก็บเป็น
        password_hash แบบเดียวกับ set_password (ทรงเดียวกันทั้งไฟล์ — ไม่งั้น verify_password
        จะไม่มีวันเจอ)."""
        email = email.strip().lower()
        uid = user_id or f"uid-{len(self.users) + 1}"
        self.users[email] = {"id": uid, "email": email, "name": name, "is_admin": is_admin,
                              "password_hash": hashlib.sha256(password.encode("utf-8")).hexdigest()}
        return uid

    def add_user(self, token: str, user_id: str, email: str, is_admin: bool = False) -> None:
        self.sessions[token] = {"id": user_id, "email": email, "name": None, "is_admin": is_admin}
        # เทสต์เดิม (CloudCase) seed ผู้ใช้ผ่านทางนี้ — ลงทะเบียนใน self.users ด้วยพร้อมกัน
        # ไม่งั้น count_users() จะเห็น 0 ทั้งที่มีคน login อยู่ 3 คน (ดู docstring count_users)
        # password_hash เป็นค่าปลอมที่ไม่ตรงกับ sha256 ของรหัสผ่านจริงใดๆ (ตั้งใจ — บัญชีที่ seed
        # ผ่านทางนี้ใช้ล็อกอินด้วยรหัสผ่านจริงไม่ได้ ต้องผ่าน add_account/set_password เท่านั้น)
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

    def post_worker_json(self, path: str, body: dict | None = None, token: str = ""):
        """POST เข้า /api/worker/... พร้อม header Bearer — endpoint นี้ไม่ใช้คุกกี้เลย.

        ผู้เรียกต้อง patch config.worker_token ให้ตรงกับ token ก่อน (ดู WorkerAuthMixin
        ใน test_bug_048_apply_result.py) ไม่งั้น _worker_authed() ปฏิเสธด้วย 403 เสมอ
        """
        return self.post_json(path, body, headers={"Authorization": f"Bearer {token}"})

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

        **ส่ง header กับ body เป็นก้อนเดียว** (BACKLOG #75) ของเดิมแยกเป็นสอง sendall ซึ่งเปิด
        ช่องให้เซิร์ฟเวอร์ปฏิเสธตั้งแต่อ่าน header จบ (เช่น Content-Length ผิดรูป -> 400) แล้ว
        close() ทั้งที่ body ยังค้างใน receive buffer ของมัน — TCP ตอบด้วย RST ไม่ใช่ FIN และ
        Windows ทิ้งไบต์ที่เรารับมาแล้วแต่ยังไม่ได้อ่านไปพร้อมกัน คำตอบ 400 ที่มาถึงแล้วจึงหาย
        ทั้งก้อน เทสต์แดงสุ่มทั้งที่เซิร์ฟเวอร์ทำถูกทุกอย่าง
        วัดกับ body 10 ไบต์ 40 ครั้งต่อแบบ: แยกส่ง = ได้ 400 กลับมา 28/40 (คั่น 30ms ระหว่าง
        สองก้อน = 0/40) · ส่งก้อนเดียว = 40/40 ทั้งตอนเซิร์ฟเวอร์ตอบไวและตอบช้า
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
            payload = head.encode("utf-8")
            if send_body and body:
                payload += body          # ก้อนเดียว — ดู docstring ว่าทำไม
            try:
                s.sendall(payload)
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
                # อ่านมาได้บางส่วนก่อนโดนตัด = คำตอบมาถึงจริง ใช้เท่าที่มี อย่าทิ้งทั้งก้อน
                if not chunks:
                    return -2, f"recv failed: {e!r}", time.monotonic() - t0
        finally:
            s.close()
        text = b"".join(chunks).decode("utf-8", "replace")
        status = int(text.split(" ")[1]) if text.startswith("HTTP/") else -3
        return status, text, time.monotonic() - t0

    def raw_send_and_collect(self, data: bytes, timeout: float = 1.5,
                             expect: int | None = None, settle: float = 0.3) -> bytes:
        """ส่ง raw bytes ก้อนเดียว (คุมทั้งคำขอเอง รวม header/แนวการเข้ารหัส body) เข้า socket

        เดียวกัน แล้วอ่านทุกอย่างที่ตอบกลับมาจนกว่าคอนเนกชันจะปิด (EOF) หรือหมดเวลารวม —
        ต่างจาก `raw_request` ที่หยุดอ่านทันทีที่เจอ header block แรก ตัวนี้ตั้งใจอ่าน
        **ทุก response** บนคอนเนกชันเดียว ใช้นับจำนวน `HTTP/1.1 ` ทั้งหมด (BUG-011 request
        smuggling / keep-alive regression) คืน raw bytes ทั้งก้อน (ไม่ decode ให้ เพราะเทสต์
        พวกนี้สนใจ byte count ตรงๆ)

        `timeout` คือเพดานเวลารวมของทั้งฟังก์ชัน (ไม่ใช่ recv() เดี่ยว) — บน CI ที่ CPU ถูก
        แย่งหนัก serve_forever อาจใช้เวลานานกว่าจะได้ CPU มาตอบ ฟังก์ชันนี้จึง recv() แบบ
        สั้นๆ วนซ้ำจนกว่าจะครบ deadline แทนที่จะยอมแพ้ทันทีที่ recv() รอบแรกหมดเวลา (บั๊กเดิม:
        `timeout=1.0/1.5` เป็นทั้ง socket timeout และเพดานรวมในตัวเดียว — ภายใต้ CPU ที่ถูกแย่ง
        เต็ม 10 คอร์ วัดจริงว่าเซิร์ฟเวอร์ตอบ 0 ไบต์ภายใน 1 วินาที ทำให้เทสต์ที่คาด 2 responses
        เห็น blob ว่างเปล่า — ไม่ใช่บั๊กของ server.py แต่เป็นความเปราะของฮาร์เนสเอง)

        `expect` (ถ้าระบุ) คือจำนวน response ที่ต้องการให้ครบก่อนหยุดรออ่านเพิ่ม — เมื่อครบแล้ว
        ฟังก์ชันจะรอ **ต่ออีก `settle` วินาที** ก่อนปิด socket แทนที่จะคืนค่าทันที

        กับดักสำคัญที่สุด (อย่าพลาด): ถ้าหยุดอ่านทันทีที่นับ `HTTP/1.1 ` ครบ `expect` โดยไม่รอ
        `settle` ต่อ เทสต์ "ต้องมีแค่ 1 response เท่านั้น" (request smuggling / keep-alive
        desync) จะผ่านแบบไม่มีความหมาย — มันไม่เคยให้เวลาเซิร์ฟเวอร์ตีความ byte ที่เหลือ (คำขอ
        ที่แอบแนบมา) เป็นคำขอที่สองเลย `settle` คือสิ่งที่พิสูจน์ "ไม่มีอันที่สองตามมาจริงๆ"
        ไม่ใช่แค่ "ยังไม่มีตอนที่เรามองครั้งแรก"
        """
        deadline = time.monotonic() + timeout
        s = socket.create_connection(("127.0.0.1", self.port), timeout=min(5.0, timeout))
        chunks: list[bytes] = []
        try:
            s.sendall(data)
            settle_until: float | None = None
            while True:
                now = time.monotonic()
                if now >= deadline:
                    break
                if settle_until is not None and now >= settle_until:
                    break
                per_recv = settle_until if settle_until is not None else deadline
                s.settimeout(max(0.05, min(per_recv - now, 0.5)))
                try:
                    b = s.recv(65536)
                except OSError:
                    continue  # recv() รอบนี้หมดเวลา/ชั่วคราว — เช็ค deadline/settle รอบถัดไป
                if not b:
                    break  # เซิร์ฟเวอร์ปิดคอนเนกชันจริง (EOF) ไม่ใช่แค่เงียบ
                chunks.append(b)
                if expect is not None and settle_until is None:
                    if b"".join(chunks).count(b"HTTP/1.1 ") >= expect:
                        settle_until = time.monotonic() + settle
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
        self._wait_until_ready()

    def _wait_until_ready(self, deadline: float = 180.0) -> None:
        """วนยิง GET /api/config จริงจนกว่าจะได้คำตอบ 200 หรือหมดเวลาใจกว้าง (default 180s).

        เหตุผล: `server.Server(...)` bind()+listen() เสร็จในตัว constructor แล้ว คอนเนกชัน
        ใหม่จึงเข้า backlog ของ OS ได้ทันทีตั้งแต่ `thread.start()` คืนค่า — แต่ยังไม่มีใครตอบ
        จนกว่าเธรด `serve_forever` จะได้ CPU มา accept()/dispatch จริง บน CI ที่ CPU ถูกแย่ง
        เต็มทุกคอร์ ช่วงว่างนี้วัดได้เกิน 20 วินาที (เกินเพดาน timeout ของ `_do`) ทำให้คำขอ
        แรกสุดของเทสต์ค้าง/ได้ 0 ไบต์อย่างเข้าใจผิดว่าเป็นบั๊กของ server.py — probe นี้ดูดซับ
        ต้นทุน "cold start ตอน CPU ไม่ว่าง" ไว้ใน setUp (ที่ยังไม่มี assertion ใดๆ) แทน

        ทำไม 180s (วัดจริง 2026-09-17 ด้วยสคริปต์โหลด `n = cpu_count()*2` ที่ผู้ว่าจ้างให้ใช้ บน
        เครื่องนี้ 10 คอร์): การสปอว์นโปรเซสกิน CPU 20 ตัวพร้อมกันด้วย `multiprocessing.Process`
        บน Windows ("spawn" ไม่ใช่ "fork" — รี-อิมพอร์ต interpreter ทั้งชุดต่อโปรเซส) ทำให้ OS
        เข้าสู่ช่วง "storm" ที่เธรดของเราแทบไม่ได้ CPU เลย **ความยาวของ storm ผันผวนมาก** —
        วัดได้ตั้งแต่ ~89 วินาที (หนึ่งรอบ, เครื่องว่างก่อนเริ่ม) ถึง >240 วินาที (สองเทสต์ติดกัน
        ชนเพดานเดิม 120s รวมกัน 244.8s ในอีกรอบ) เมื่อพ้นช่วง storm แล้วเซิร์ฟเวอร์ตอบปกติทันที
        (~0.3-0.4 วินาที) แม้ 20 โปรเซสนั้นจะยังรันอยู่เต็มกำลังก็ตาม — ไม่ใช่การอดอาหารตลอดไป
        เป็นคอขวดช่วงสปอว์นโปรเซสเท่านั้น แต่ความยาวของมันคาดเดาไม่ได้แม่นยำบนเครื่องนี้
        (น่าจะเกี่ยวกับ antivirus/disk cache ตอนโหลด interpreter image ซ้ำ 20 รอบพร้อมกัน ไม่ใช่
        คุณสมบัติทั่วไปของ CPU contention เฉยๆ) 180s คือค่าที่เผื่อ margin เหนือกรณีเลวร้ายที่วัด
        ได้จริงบนเครื่องนี้ ไม่ใช่เดา — แต่ก็ไม่ใช่การรับประกัน 100% ถ้า storm ยาวกว่านี้ (ดู
        docs/tickets/BUG-059-ci-flaky-body-caps-harness.md หัวข้อ "ความเสี่ยงที่เหลือ") บน CI จริง
        (runner เดี่ยวของ GitHub ไม่มีใครมาแย่ง CPU ขนาดนี้พร้อมกัน) เพดานนี้แทบไม่เคยถูกใช้เกิน
        เสี้ยววินาทีแรกเลย
        """
        t0 = time.monotonic()
        delay = 0.02
        last_err: BaseException | None = None
        while time.monotonic() - t0 < deadline:
            try:
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1.0)
                try:
                    conn.request("GET", "/api/config")
                    resp = conn.getresponse()
                    resp.read()
                    if resp.status == 200:
                        return
                    last_err = AssertionError(f"unexpected status {resp.status}")
                finally:
                    conn.close()
            except OSError as e:
                last_err = e
            time.sleep(delay)
            delay = min(delay * 1.5, 0.5)
        self.fail(
            f"test server on 127.0.0.1:{self.port} ไม่ตอบ GET /api/config ภายใน "
            f"{deadline:.0f} วินาที (last_err={last_err!r}) — เธรด serve_forever อาจถูก CPU "
            "แย่งจนไม่ได้ทำงานเลย ไม่ใช่แค่เทสต์เปราะ"
        )


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
