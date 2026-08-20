"""คลังการประชุมบน Postgres — ใช้ตอน deploy ขึ้น cloud.

มี API ชุดเดียวกับ web/store.py (แบบไฟล์ JSON) เพื่อให้ server.py เรียกได้เหมือนกัน
เลือกตัวไหนดูที่ web/backend.py

คิวงานอยู่ในตาราง jobs ด้วย ไม่ใช่ในหน่วยความจำ — บน serverless แต่ละ request
อาจไปคนละ instance สถานะที่เก็บใน process จะหายหรือมองไม่เห็นกัน
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from pathlib import Path

from .. import summarizer
from ..config import config
from . import db

# ที่เก็บไฟล์เสียง — ตอนนี้ยังเป็นดิสก์ในเครื่องเหมือนโหมดไฟล์
# บน Vercel เขียนดิสก์ไม่ได้ ต้องสลับไปที่เก็บภายนอก (ดู README หัวข้อ deploy)
WEB_DIR = config.root / "recordings" / "web"

_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
SNIPPET_PAD = 70
SESSION_DAYS = 30

# ---------- helpers ที่ใช้ร่วมกับแบ็กเอนด์แบบไฟล์ ----------

def new_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"


def valid_id(mid: str) -> bool:
    return bool(_ID_RE.match(mid or ""))


def fmt_time(sec: float) -> str:
    m, s = divmod(int(sec or 0), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def transcript_text(detail: dict) -> str:
    return " ".join((s.get("text") or "").strip() for s in detail.get("segments", [])).strip()


def timestamped(detail: dict) -> str:
    parts = []
    for s in detail.get("segments", []):
        head = f"[{fmt_time(s.get('start', 0))} - {fmt_time(s.get('end', 0))}]"
        if s.get("speaker"):
            head += f" {s['speaker']}:"
        parts.append(f"{head} {(s.get('text') or '').strip()}")
    return "\n".join(parts)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _search_text(title: str, summary: str, segments: list[dict]) -> str:
    return "\n".join([title or "", summary or "", transcript_text({"segments": segments})])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- ผู้ใช้ / session ----------

def ensure_user(email: str, name: str | None = None, is_admin: bool = False) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            """insert into meeting_ai.users (email, name, is_admin)
               values (%s, %s, %s)
               on conflict (email) do update set name = coalesce(excluded.name, users.name)
               returning id, email, name, is_admin""",
            (email.strip().lower(), name, is_admin),
        ).fetchone()
    return {"id": str(row[0]), "email": row[1], "name": row[2], "is_admin": row[3]}


def count_users() -> int:
    with db.connect() as conn:
        return conn.execute("select count(*) from meeting_ai.users").fetchone()[0]


# scrypt พารามิเตอร์ตามที่ RFC 7914 แนะนำสำหรับการล็อกอินแบบโต้ตอบ
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1, "dklen": 32}


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


def set_password(user_id: str, password: str) -> None:
    salt = secrets.token_bytes(16)
    with db.connect() as conn:
        conn.execute(
            "update meeting_ai.users set password_hash = %s, password_salt = %s where id = %s",
            (_derive(password, salt), salt, user_id),
        )


def verify_password(email: str, password: str) -> dict | None:
    """คืนข้อมูลผู้ใช้ถ้ารหัสถูก — เทียบแบบ constant-time."""
    with db.connect() as conn:
        row = conn.execute(
            """select id, email, name, is_admin, password_hash, password_salt
               from meeting_ai.users where email = %s""",
            (email.strip().lower(),),
        ).fetchone()
    if row is None or not row[4] or not row[5]:
        # ยังคำนวณ hash ทิ้งไว้ ให้เวลาตอบใกล้เคียงกับกรณีมีผู้ใช้จริง
        _derive(password, b"\x00" * 16)
        return None
    if not secrets.compare_digest(bytes(row[4]), _derive(password, bytes(row[5]))):
        return None
    return {"id": str(row[0]), "email": row[1], "name": row[2], "is_admin": row[3]}


def has_password(email: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "select password_hash is not null from meeting_ai.users where email = %s",
            (email.strip().lower(),),
        ).fetchone()
    return bool(row and row[0])


def create_session(user_id: str, user_agent: str | None = None) -> str:
    """คืน token ที่ต้องเอาไปใส่คุกกี้ — ในฐานเก็บแค่ hash."""
    token = secrets.token_urlsafe(32)
    with db.connect() as conn:
        conn.execute(
            """insert into meeting_ai.sessions (token_hash, user_id, user_agent, expires_at)
               values (%s, %s, %s, %s)""",
            (_hash(token), user_id, (user_agent or "")[:300],
             _now() + timedelta(days=SESSION_DAYS)),
        )
    return token


def user_for_session(token: str) -> dict | None:
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute(
            """select u.id, u.email, u.name, u.is_admin
               from meeting_ai.sessions s join meeting_ai.users u on u.id = s.user_id
               where s.token_hash = %s and s.expires_at > now()""",
            (_hash(token),),
        ).fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "email": row[1], "name": row[2], "is_admin": row[3]}


def drop_session(token: str) -> None:
    if token:
        with db.connect() as conn:
            conn.execute("delete from meeting_ai.sessions where token_hash = %s", (_hash(token),))


def purge_expired() -> None:
    with db.connect() as conn:
        conn.execute("delete from meeting_ai.sessions where expires_at < now()")


# ---------- คำเชิญ ----------

def create_invite(created_by: str | None, email: str | None = None, days: int = 14) -> str:
    code = secrets.token_urlsafe(12)
    with db.connect() as conn:
        conn.execute(
            """insert into meeting_ai.invites (code_hash, email, created_by, expires_at)
               values (%s, %s, %s, %s)""",
            (_hash(code), (email or None), created_by, _now() + timedelta(days=days)),
        )
    return code


def redeem_invite(code: str, user_id: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.invites
               set used_by = %s, used_at = now()
               where code_hash = %s and used_by is null
                 and (expires_at is null or expires_at > now())
               returning code_hash""",
            (user_id, _hash(code)),
        ).fetchone()
    return row is not None


def invite_email(code: str) -> tuple[bool, str | None]:
    """คืน (ใช้ได้ไหม, อีเมลที่ผูกไว้) — ใช้ตรวจก่อนสร้างผู้ใช้."""
    with db.connect() as conn:
        row = conn.execute(
            """select email from meeting_ai.invites
               where code_hash = %s and used_by is null
                 and (expires_at is null or expires_at > now())""",
            (_hash(code),),
        ).fetchone()
    return (row is not None, row[0] if row else None)


# ---------- การประชุม ----------

_META_COLS = """id, owner_id, title, visibility, language, duration, segment_count,
                source, template, speakers, summary_error, edited, transcript_edited,
                audio_key, created_at, updated_at"""


def _meta_from_row(row) -> dict:
    return {
        "id": row[0],
        "owner_id": str(row[1]) if row[1] else None,
        "title": row[2],
        "visibility": row[3],
        "language": row[4],
        "duration": round(row[5] or 0, 1),
        "segments": row[6],
        "source": row[7],
        "template": row[8],
        "speakers": list(row[9] or []),
        "summary_error": row[10],
        "edited": row[11],
        "transcript_edited": row[12],
        "audio": row[13],
        "created": row[14].isoformat(timespec="seconds") if row[14] else None,
        "updated": row[15].isoformat(timespec="seconds") if row[15] else None,
    }


def create(
    mid: str,
    title: str,
    audio_name: str,
    source: str,
    language: str,
    duration: float,
    segments: list[dict],
    summary: str,
    summary_error: str | None = None,
    template: str = summarizer.DEFAULT_TEMPLATE,
    speakers: list[str] | None = None,
    owner_id: str | None = None,
    visibility: str = "private",
) -> dict:
    with db.connect() as conn:
        conn.execute(
            f"""insert into meeting_ai.meetings
                  (id, owner_id, title, visibility, language, duration, segment_count,
                   source, template, speakers, summary, summary_error, segments,
                   translations, audio_key, search_text)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'{{}}'::jsonb,%s,%s)
                on conflict (id) do update set
                  title = excluded.title, language = excluded.language,
                  duration = excluded.duration, segment_count = excluded.segment_count,
                  speakers = excluded.speakers, summary = excluded.summary,
                  summary_error = excluded.summary_error, segments = excluded.segments,
                  audio_key = excluded.audio_key, search_text = excluded.search_text,
                  updated_at = now()""",
            (mid, owner_id, title, visibility, language, duration, len(segments),
             source, template, speakers or [], summary, summary_error,
             json.dumps(segments, ensure_ascii=False), audio_name,
             _search_text(title, summary, segments)),
        )
    return get(mid)


def get(mid: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            f"""select {_META_COLS}, summary, segments, translations
                from meeting_ai.meetings where id = %s""",
            (mid,),
        ).fetchone()
    if row is None:
        return None
    out = _meta_from_row(row)
    out["summary"] = row[16] or ""
    out["segments_list"] = row[17] or []
    out["translations"] = row[18] or {}
    out["transcript"] = timestamped({"segments": out["segments_list"]})
    return out


def load_detail(mid: str) -> dict:
    m = get(mid)
    return {"segments": (m or {}).get("segments_list", []), "summary": (m or {}).get("summary", "")}


def _refresh_search(conn, mid: str) -> None:
    """สร้าง search_text ใหม่จากแถวเดิมทั้งหมดใน SQL รอบเดียว

    ทำใน SQL เพราะถ้าดึง segments กลับมาต่อใน Python จะต้องเปิด connection ซ้อน
    ซึ่งเสี่ยง deadlock ตอนพูลเต็ม
    """
    conn.execute(
        """update meeting_ai.meetings set
             search_text = concat_ws(E'\\n', title, summary,
               (select coalesce(string_agg(seg->>'text', ' '), '')
                  from jsonb_array_elements(segments) seg)),
             updated_at = now()
           where id = %s""",
        (mid,),
    )


def update(mid: str, title: str | None = None, summary: str | None = None) -> dict | None:
    with db.connect() as conn:
        if summary is not None:
            conn.execute(
                """update meeting_ai.meetings
                   set summary = %s, edited = true, summary_error = null, updated_at = now()
                   where id = %s""",
                (summary, mid),
            )
        if title is not None and title.strip():
            conn.execute(
                "update meeting_ai.meetings set title = %s, updated_at = now() where id = %s",
                (title.strip(), mid),
            )
        exists = conn.execute(
            "select 1 from meeting_ai.meetings where id = %s", (mid,)
        ).fetchone()
        if exists is None:
            return None
        _refresh_search(conn, mid)
    return get(mid)


def set_summary(mid: str, summary: str, error: str | None = None) -> dict | None:
    """เขียนสรุปที่ได้จาก AI — ไม่ตั้ง flag edited เพราะไม่ใช่คนแก้."""
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.meetings
               set summary = %s, summary_error = %s, updated_at = now()
               where id = %s returning id""",
            (summary, error, mid),
        ).fetchone()
        if row is None:
            return None
        _refresh_search(conn, mid)
    return get(mid)


def set_translation(mid: str, lang: str, text: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.meetings
               set translations = translations || jsonb_build_object(%s::text, %s::text),
                   updated_at = now()
               where id = %s returning id""",
            (lang, text, mid),
        ).fetchone()
    return get(mid) if row else None


def set_segments(mid: str, segments: list[dict]) -> dict | None:
    speakers = sorted({s["speaker"] for s in segments if s.get("speaker")})
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.meetings
               set segments = %s, segment_count = %s, speakers = %s,
                   transcript_edited = true, updated_at = now()
               where id = %s returning id""",
            (json.dumps(segments, ensure_ascii=False), len(segments), speakers, mid),
        ).fetchone()
        if row is None:
            return None
        _refresh_search(conn, mid)
    return get(mid)


def access(mid: str, user_id: str | None) -> str:
    """สิทธิ์ของผู้ใช้กับการประชุมหนึ่ง: 'owner' | 'team' | 'none'.

    owner  = เจ้าของ (หรือการประชุมที่ไม่มีเจ้าของ เช่นย้ายมาจากโหมดไฟล์)
    team   = การประชุมถูกตั้งเป็น team ทุกคนในฐานนี้อ่าน/เกลาได้ แต่ลบ/แชร์ไม่ได้
    """
    with db.connect() as conn:
        row = conn.execute(
            "select owner_id, visibility from meeting_ai.meetings where id = %s", (mid,)
        ).fetchone()
    if row is None:
        return "none"
    owner_id, visibility = (str(row[0]) if row[0] else None), row[1]
    if user_id and (owner_id is None or owner_id == user_id):
        return "owner"
    if user_id and visibility == "team":
        return "team"
    return "none"


def set_visibility(mid: str, visibility: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.meetings set visibility = %s, updated_at = now()
               where id = %s returning id""",
            (visibility, mid),
        ).fetchone()
    return get(mid) if row else None


def delete(mid: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "delete from meeting_ai.meetings where id = %s returning audio_key", (mid,)
        ).fetchone()
        conn.execute("delete from meeting_ai.jobs where id = %s or meeting_id = %s", (mid, mid))
    return row is not None


def audio_path(meta: dict) -> Path | None:
    name = meta.get("audio")
    if not name:
        return None
    # ใช้แค่ basename กัน path ที่หลุดออกนอกโฟลเดอร์
    return WEB_DIR / Path(name).name


def _snippet(text: str, query: str) -> str:
    pos = (text or "").lower().find(query.lower())
    if pos < 0:
        return ""
    start = max(0, pos - SNIPPET_PAD)
    end = min(len(text), pos + len(query) + SNIPPET_PAD)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")


def search(query: str = "", user_id: str | None = None) -> list[dict]:
    """คืนรายการการประชุมที่ผู้ใช้คนนี้เห็นได้ กรองด้วย substring ถ้ามี query.

    ใช้ ILIKE เพราะภาษาไทยไม่มีช่องว่างระหว่างคำ full-text search จะพลาดมากกว่า
    """
    query = (query or "").strip()
    where = ["(visibility = 'team' or owner_id = %s or owner_id is null)"]
    params: list[Any] = [user_id]
    if query:
        where.append("search_text ilike %s")
        params.append(f"%{query}%")

    with db.connect() as conn:
        rows = conn.execute(
            f"""select {_META_COLS}, summary, search_text
                from meeting_ai.meetings
                where {' and '.join(where)}
                order by created_at desc limit 500""",
            params,
        ).fetchall()

    out = []
    for row in rows:
        meta = _meta_from_row(row)
        if query:
            meta["snippet"] = (_snippet(row[16] or "", query)
                               or _snippet(row[17] or "", query)
                               or meta["title"])
        out.append(meta)
    return out


def stats(user_id: str | None = None) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            """select count(*), coalesce(sum(duration), 0) from meeting_ai.meetings
               where visibility = 'team' or owner_id = %s or owner_id is null""",
            (user_id,),
        ).fetchone()
    return {"count": row[0], "total_duration": round(row[1] or 0, 1)}


# ---------- ลิงก์แชร์ ----------

def create_share(mid: str, created_by: str | None, can_edit: bool = False,
                 days: int | None = None) -> str:
    token = secrets.token_urlsafe(24)
    with db.connect() as conn:
        conn.execute(
            """insert into meeting_ai.shares (token_hash, meeting_id, created_by, can_edit, expires_at)
               values (%s, %s, %s, %s, %s)""",
            (_hash(token), mid, created_by, can_edit,
             (_now() + timedelta(days=days)) if days else None),
        )
    return token


def share_target(token: str) -> dict | None:
    """คืน {meeting_id, can_edit} ของลิงก์แชร์ที่ยังไม่หมดอายุ."""
    if not token:
        return None
    with db.connect() as conn:
        row = conn.execute(
            """select meeting_id, can_edit from meeting_ai.shares
               where token_hash = %s and (expires_at is null or expires_at > now())""",
            (_hash(token),),
        ).fetchone()
    return {"meeting_id": row[0], "can_edit": row[1]} if row else None


def list_shares(mid: str) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """select can_edit, expires_at, created_at from meeting_ai.shares
               where meeting_id = %s order by created_at desc""",
            (mid,),
        ).fetchall()
    return [
        {"can_edit": r[0],
         "expires": r[1].isoformat(timespec="seconds") if r[1] else None,
         "created": r[2].isoformat(timespec="seconds")}
        for r in rows
    ]


def revoke_shares(mid: str) -> int:
    with db.connect() as conn:
        cur = conn.execute("delete from meeting_ai.shares where meeting_id = %s", (mid,))
        return cur.rowcount


# ---------- คิวงาน ----------

def job_upsert(job_id: str, kind: str, title: str, spec: dict,
               status: str = "queued", meeting_id: str | None = None) -> dict:
    with db.connect() as conn:
        row = conn.execute(
            """insert into meeting_ai.jobs (id, meeting_id, kind, title, spec, status, step, progress)
               values (%s,%s,%s,%s,%s,%s,%s,0)
               on conflict (id) do update set
                 kind = excluded.kind, title = excluded.title, spec = excluded.spec,
                 status = excluded.status, step = excluded.step, progress = 0,
                 error = null, warning = null, updated_at = now()
               returning id""",
            (job_id, meeting_id, kind, title, json.dumps(spec, ensure_ascii=False),
             status, "รอคิว" if status == "queued" else "รออัปโหลดไฟล์"),
        ).fetchone()
    return job_get(row[0])


def job_get(job_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            """select id, meeting_id, kind, status, step, progress, title, error, warning,
                      spec, created_at
               from meeting_ai.jobs where id = %s""",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "meeting_id": row[1], "kind": row[2], "status": row[3],
        "step": row[4], "progress": row[5], "title": row[6], "error": row[7],
        "warning": row[8], "_spec": row[9] or {},
        "created": row[10].isoformat(timespec="seconds"),
    }


def job_set_spec(job_id: str, spec: dict) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "update meeting_ai.jobs set spec = %s, updated_at = now() where id = %s returning id",
            (json.dumps(spec, ensure_ascii=False), job_id),
        ).fetchone()
    return row is not None


def job_start(job_id: str) -> dict | None:
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.jobs set status = 'queued', step = 'รอคิว', updated_at = now()
               where id = %s and status = 'draft' returning id""",
            (job_id,),
        ).fetchone()
    return job_get(job_id) if row else None


def job_claim(worker: str | None = None, kinds: list[str] | None = None) -> dict | None:
    """หยิบงานเก่าสุดที่ยังรออยู่ แบบ atomic — กัน worker หลายตัวแย่งงานเดียวกัน.

    kinds = ชนิดงานที่เครื่องนี้ทำได้ (None = ทุกชนิด)
    จำเป็นเพราะงาน 'bot' ต้องมี Docker + image + profile ที่ล็อกอินแล้ว
    ถ้าปล่อยให้เครื่องที่ไม่มีของพวกนี้คว้าไป งานจะพังทันทีทั้งที่อีกเครื่องทำได้
    """
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.jobs set
                 status = 'running', step = 'worker รับงานแล้ว', progress = 0.01,
                 claimed_at = now(), attempts = attempts + 1, updated_at = now(),
                 worker = %s
               where id = (
                 select id from meeting_ai.jobs
                 where status = 'queued'
                   and (%s::text[] is null or kind = any(%s::text[]))
                 order by created_at
                 for update skip locked
                 limit 1)
               returning id""",
            (worker, kinds, kinds),
        ).fetchone()
    return job_get(row[0]) if row else None


# ไม่มี progress เข้ามานานเกินนี้ = ไม่มี worker ถืองานนี้อยู่จริง
# (worker รายงานทุกไม่กี่วินาที ส่วนงานบอทรายงานทุก 10 วินาที)
STOP_ORPHAN_SECONDS = 120

CANCEL_MSG = ("ยกเลิกโดยผู้ใช้ — งานนี้ไม่มีเครื่องประมวลผลถืออยู่แล้ว "
              "(worker หลุด/ถูกรีสตาร์ตกลางทาง)")


def job_request_stop(job_id: str) -> str:
    """ตั้งธงขอให้หยุดงาน — worker อ่านธงนี้ตอนรายงาน progress แล้วสั่งบอทออกจากห้อง.

    เก็บใน spec (JSONB) ไม่เพิ่มคอลัมน์ใหม่ เพื่อไม่ต้องให้ผู้ใช้ migrate ฐานข้อมูลที่ deploy แล้ว
    """
    with db.connect() as conn:
        # งานที่ไม่มีใครถืออยู่ ตั้งธงไปก็ไม่มีใครอ่าน กดปุ่มแล้วจะเงียบหายไปเฉยๆ
        # กรณีนั้นต้องยกเลิกงานให้เลย (queued = ยังไม่มีใครรับ, running แต่เงียบนาน = worker หลุด)
        cancelled = conn.execute(
            """update meeting_ai.jobs
               set status = 'error', step = 'ยกเลิกแล้ว', error = %s
               where id = %s and (status = 'queued' or (status = 'running'
                 and coalesce(updated_at, claimed_at) < now() - make_interval(secs => %s)))""",
            (CANCEL_MSG, job_id, STOP_ORPHAN_SECONDS),
        ).rowcount
        if cancelled:
            return "cancelled"
        # ห้ามแตะ updated_at — reaper ใช้ฟิลด์นี้วัดว่า worker เงียบไปนานแค่ไหน
        # ถ้าปุ่มหยุดไปรีเซ็ตมัน งานที่ worker ตายทิ้งไว้จะยืดเวลาค้างออกไปอีก 30 นาที
        cur = conn.execute(
            """update meeting_ai.jobs
               set spec = coalesce(spec, '{}'::jsonb) || '{"stop": true}'::jsonb
               where id = %s and status = 'running'""",
            (job_id,),
        )
        return "stopped" if cur.rowcount else ""


def job_stop_requested(job_id: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            "select coalesce(spec->>'stop', 'false') from meeting_ai.jobs where id = %s",
            (job_id,),
        ).fetchone()
    return bool(row) and row[0] == "true"


def job_progress(job_id: str, step: str, progress: float) -> None:
    with db.connect() as conn:
        conn.execute(
            """update meeting_ai.jobs
               set status = 'running', step = %s, progress = %s, updated_at = now()
               where id = %s""",
            (step, max(0.0, min(1.0, progress)), job_id),
        )


def job_done(job_id: str, meeting_id: str | None, step: str, warning: str | None) -> None:
    with db.connect() as conn:
        conn.execute(
            """update meeting_ai.jobs
               set status = 'done', step = %s, progress = 1, meeting_id = %s,
                   warning = %s, updated_at = now()
               where id = %s""",
            (step, meeting_id, warning, job_id),
        )


def job_fail(job_id: str, error: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """update meeting_ai.jobs
               set status = 'error', step = 'ผิดพลาด', error = %s, updated_at = now()
               where id = %s""",
            (error, job_id),
        )


def job_requeue(job_id: str) -> bool:
    with db.connect() as conn:
        row = conn.execute(
            """update meeting_ai.jobs
               set status = 'queued', step = 'รอคิว', progress = 0, updated_at = now()
               where id = %s returning id""",
            (job_id,),
        ).fetchone()
    return row is not None


def job_active() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """select id, meeting_id, kind, status, step, progress, title, error, warning,
                      created_at, worker
               from meeting_ai.jobs where status in ('queued','running')
               order by created_at"""
        ).fetchall()
    return [
        {"id": r[0], "meeting_id": r[1], "kind": r[2], "status": r[3], "step": r[4],
         "progress": r[5], "title": r[6], "error": r[7], "warning": r[8],
         "created": r[9].isoformat(timespec="seconds"), "worker": r[10]}
        for r in rows
    ]


# ---------- เครื่องประมวลผล ----------

# ไม่ได้ยิน heartbeat เกินนี้ = ถือว่าหลุดไป (worker เต้นทุก ~20 วิ)
WORKER_STALE_SECONDS = 75


def worker_seen(name: str, status: str = "idle", job_id: str | None = None,
                gpu: str | None = None, caps: dict | None = None) -> None:
    """อัปเดต heartbeat ของ worker — เรียกทั้งตอนว่างและตอนกำลังทำงาน."""
    with db.connect() as conn:
        conn.execute(
            """insert into meeting_ai.workers (name, status, job_id, gpu, caps, last_seen)
               values (%s, %s, %s, %s, coalesce(%s::jsonb, '{}'::jsonb), now())
               on conflict (name) do update set
                 status = excluded.status, job_id = excluded.job_id,
                 gpu = coalesce(excluded.gpu, workers.gpu),
                 caps = case when excluded.caps = '{}'::jsonb then workers.caps
                             else excluded.caps end,
                 last_seen = now()""",
            (name[:80], status, job_id, gpu,
             json.dumps(caps, ensure_ascii=False) if caps else None),
        )


def worker_capabilities() -> dict:
    """รวมความสามารถของ worker ที่ยังมีชีวิต — ใช้บอกผู้ใช้ว่าเลือกตัวถอดเสียงอะไรได้.

    ฝั่ง cloud ไม่มี whisper เอง จึงต้องถามจาก worker ไม่ใช่ดูจากตัวเอง
    """
    with db.connect() as conn:
        rows = conn.execute(
            """select caps, name from meeting_ai.workers
               where last_seen > now() - make_interval(secs => %s)""",
            (WORKER_STALE_SECONDS,),
        ).fetchall()
    out: dict = {"workers": len(rows), "local": False, "api": False, "diarize": False,
                 "bot": False, "stt_model": None, "stt_host": None, "by": {},
                 "diarize_missing": [], "bot_missing": []}
    for caps, name in rows:
        caps = caps or {}
        for key in ("local", "api", "diarize", "bot"):
            if caps.get(key):
                out[key] = True
                out["by"].setdefault(key, []).append(name)
        out["stt_model"] = caps.get("stt_model") or out["stt_model"]
        out["stt_host"] = caps.get("stt_host") or out["stt_host"]
        # งานหนึ่งงานไปลงที่ worker ตัวใดตัวหนึ่ง มีตัวไหนทำได้ก็ถือว่าทำได้
        # แต่ถ้าไม่มีใครทำได้เลย ต้องบอกว่าขาดอะไร -> เก็บรายการจากตัวที่ขาดไว้ก่อน
        for field in ("diarize_missing", "bot_missing"):
            for piece in caps.get(field) or []:
                if piece not in out[field]:
                    out[field].append(piece)
    for key in ("diarize", "bot"):
        if out[key]:
            out[f"{key}_missing"] = []
    return out


def worker_finished(name: str) -> None:
    with db.connect() as conn:
        conn.execute(
            """update meeting_ai.workers
               set jobs_done = jobs_done + 1, status = 'idle', job_id = null, last_seen = now()
               where name = %s""",
            (name[:80],),
        )


def workers_list() -> list[dict]:
    """รายชื่อ worker พร้อมบอกว่ายังมีชีวิตอยู่ไหม."""
    with db.connect() as conn:
        rows = conn.execute(
            """select w.name, w.status, w.job_id, w.jobs_done, w.gpu,
                      extract(epoch from (now() - w.last_seen))::int as quiet_for,
                      w.started_at, j.title, w.caps
               from meeting_ai.workers w
               left join meeting_ai.jobs j on j.id = w.job_id
               order by w.last_seen desc"""
        ).fetchall()
    out = []
    for r in rows:
        alive = r[5] is not None and r[5] <= WORKER_STALE_SECONDS
        caps = r[8] or {}
        out.append({
            "name": r[0],
            "status": r[1] if alive else "gone",
            "job_id": r[2] if alive else None,
            "job_title": r[7] if alive else None,
            "jobs_done": r[3],
            "gpu": r[4],
            "quiet_for": r[5],
            "alive": alive,
            "started": r[6].isoformat(timespec="seconds") if r[6] else None,
            # ให้หน้าเว็บบอกได้ว่าเครื่องไหนขาดอะไร ไม่ต้องไปไล่ดูทีละเครื่อง
            "can": [k for k in ("local", "api", "diarize", "bot") if caps.get(k)],
        })
    return out


def workers_forget(max_age_days: int = 7) -> int:
    """ลบ worker ที่หายไปนานแล้วออกจากรายการ."""
    with db.connect() as conn:
        cur = conn.execute(
            "delete from meeting_ai.workers where last_seen < now() - make_interval(days => %s)",
            (max_age_days,),
        )
        return cur.rowcount


def jobs_reap(stale_minutes: int = 30) -> int:
    """งานที่ worker รับไปแล้วเงียบหายเกินเวลา — คืนกลับคิว.

    วัดจาก updated_at (ครั้งสุดท้ายที่ worker รายงาน progress) ไม่ใช่ claimed_at
    ของเดิมวัดจากตอนรับงาน จึงดึงงานที่ยังทำอยู่จริงกลับคิวเมื่อครบ 30 นาที
    — พังทั้งบอทที่นั่งอยู่ในห้องยาวๆ และไฟล์เสียงยาวที่ถอดนานเกินครึ่งชั่วโมง
    """
    with db.connect() as conn:
        # งานบอทคืนคิวไม่ได้ — ประชุมจบไปแล้ว ส่งบอทเข้าไปอีกทีจะได้แต่ห้องร้าง
        # อัดความเงียบทิ้งไว้เป็นชั่วโมงแล้วเปลืองเวลา GPU ถอดเสียงเปล่าๆ
        failed = conn.execute(
            """update meeting_ai.jobs
               set status = 'error',
                   error = 'เครื่องประมวลผลหลุดกลางทาง — งานบอทเริ่มใหม่ไม่ได้'
                           ' เพราะการประชุมผ่านไปแล้ว ต้องส่งบอทใหม่เอง',
                   step = 'ผิดพลาด'
               where status = 'running' and kind = 'bot'
                 and coalesce(updated_at, claimed_at) < now() - make_interval(mins => %s)""",
            (stale_minutes,),
        ).rowcount
        cur = conn.execute(
            """update meeting_ai.jobs
               set status = 'queued', step = 'รอคิว (worker หลุด)', progress = 0
               where status = 'running' and kind <> 'bot'
                 and coalesce(updated_at, claimed_at) < now() - make_interval(mins => %s)""",
            (stale_minutes,),
        )
        return cur.rowcount + failed
