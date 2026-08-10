"""เว็บเซิร์ฟเวอร์ของ meeting_ai — http.server จาก stdlib ไม่ต้องลง framework.

เปิดด้วย:  mai web
ค่าเริ่มต้นผูกกับ 127.0.0.1 เท่านั้น และไม่มีระบบล็อกอิน — ตั้งใจให้เป็นเครื่องมือบนเครื่องตัวเอง
"""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import tempfile
import urllib.parse
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .. import diarize, stt, summarizer
from ..config import config
from . import backend, exports, jobs
from .backend import store

STATIC_DIR = Path(__file__).resolve().parent / "static"

# นามสกุลที่ ffmpeg อ่านได้และเรายอมรับให้อัปโหลด
ALLOWED_EXT = {
    "mp3", "m4a", "m4v", "wav", "mp4", "webm", "ogg", "oga", "opus",
    "flac", "aac", "mov", "mkv", "avi", "wma", "3gp", "amr",
}
TRACK_NAMES = {"mixed", "mic", "system"}
MAX_UPLOAD = 2 * 1024**3      # 2 GB
MAX_LIVE_CLIP = 32 * 1024**2  # 32 MB — คลิปสดยาวไม่กี่สิบวินาที
CHUNK = 1024 * 256

SESSION_COOKIE = "mai_session"
# endpoint ที่เข้าได้ก่อนล็อกอิน (ไม่งั้นจะล็อกอินไม่ได้เลย)
PUBLIC_API = {("config",), ("auth", "me"), ("auth", "login"), ("auth", "signup")}

_SAFE_TITLE_RE = re.compile(r"[\r\n\t]+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


class BadBody(ValueError):
    """body ของคำขออ่านไม่ได้ — ตอบ 400 ไม่ใช่ 500."""


def _live_available() -> bool:
    """เซิร์ฟเวอร์นี้ถอดเสียงเองได้ไหม (ต้องมี whisper + โมเดล + ffmpeg)."""
    import shutil

    from ..config import config as cfg
    if not cfg.whisper_model_path().exists():
        return False
    for binary in (cfg.whisper_bin, cfg.ffmpeg_bin):
        if shutil.which(binary) is None and not Path(binary).exists():
            return False
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "meeting_ai"
    protocol_version = "HTTP/1.1"

    # ---------- helpers ----------

    def log_message(self, fmt: str, *args) -> None:  # เงียบกว่า default ที่พิมพ์ทุก request
        if not self.path.startswith(("/static/", "/api/jobs", "/api/live")):
            print(f"  {self.command} {self.path}", flush=True)

    def _host_ok(self) -> bool:
        """กัน DNS rebinding — หน้าเว็บภายนอกจะยิงเข้าพอร์ตนี้ผ่านโดเมนตัวเองไม่ได้."""
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]").lower()
        return host in ("localhost", "127.0.0.1", "::1", "") or host == self.server.bound_host

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    def _query(self) -> dict[str, str]:
        raw = urllib.parse.urlparse(self.path).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    # ---------- คุกกี้ / ผู้ใช้ ----------

    def _cookie(self, name: str) -> str:
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return urllib.parse.unquote(value)
        return ""

    def _cookie_header(self, token: str | None) -> str:
        """token=None = สั่งลบคุกกี้."""
        # Secure ใส่เมื่อมาทาง https เท่านั้น ไม่งั้น localhost แบบ http จะเก็บคุกกี้ไม่ได้
        https = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        flags = "HttpOnly; SameSite=Lax; Path=/" + ("; Secure" if https else "")
        if token is None:
            return f"{SESSION_COOKIE}=; Max-Age=0; {flags}"
        return f"{SESSION_COOKIE}={urllib.parse.quote(token)}; Max-Age={30 * 86400}; {flags}"

    def _resolve_user(self) -> None:
        """หา user จากคุกกี้ และ share token จาก query — ทำครั้งเดียวต่อ request."""
        self.user = None
        self.share = None
        if not backend.auth_required():
            return
        try:
            self.user = store.user_for_session(self._cookie(SESSION_COOKIE))
        except Exception:
            self.user = None  # DB ล่ม — ให้ตอบ 401 แทนที่จะ 500 ทุกเส้น
        token = self._query().get("share") or self._cookie("mai_share")
        if token:
            try:
                self.share = store.share_target(token)
            except Exception:
                self.share = None

    @property
    def user_id(self) -> str | None:
        return (self.user or {}).get("id")

    def _level(self, mid: str) -> str:
        """สิทธิ์กับการประชุมนี้: owner | team | share-edit | share-read | none.

        ห้ามคืน owner/team แค่เพราะล็อกอินอยู่ — ไม่งั้นใครก็เดา id เปิดของคนอื่นได้
        """
        if self.user:
            lvl = store.access(mid, self.user_id)
            if lvl != "none":
                return lvl
        if self.share and self.share.get("meeting_id") == mid:
            return "share-edit" if self.share.get("can_edit") else "share-read"
        return "none"

    def _may_read(self, mid: str) -> bool:
        return self._level(mid) != "none"

    def _may_write(self, mid: str) -> bool:
        return self._level(mid) in ("owner", "team", "share-edit")

    def _is_owner(self, mid: str) -> bool:
        return self._level(mid) == "owner"

    def _body_json(self) -> dict:
        """อ่าน body เป็น JSON — body ว่างถือว่า {} แต่ถ้าเสียให้ฟ้องตรงๆ

        เดิมกลืน error แล้วคืน {} ซึ่งทำให้ error ไปโผล่เป็น "ฟิลด์ที่จำเป็นหายไป"
        ชี้ผิดจุดจนไล่ปัญหายาก
        """
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise BadBody(f"อ่าน JSON ในคำขอไม่ได้: {e}") from e
        if not isinstance(data, dict):
            raise BadBody("body ต้องเป็น JSON object")
        return data

    def _read_body_to(self, dest: Path, limit: int) -> str | None:
        """สตรีม request body ลงไฟล์ คืนข้อความ error ถ้าไม่สำเร็จ."""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return "ไม่มีข้อมูลไฟล์ส่งมา"
        if length > limit:
            return f"ไฟล์ใหญ่เกิน {limit // 1024**2} MB"
        remaining = length
        with dest.open("wb") as fh:
            while remaining > 0:
                chunk = self.rfile.read(min(CHUNK, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        if remaining > 0:
            dest.unlink(missing_ok=True)
            return "อัปโหลดไม่ครบ — ลองใหม่อีกครั้ง"
        return None

    # ---------- routing ----------

    def do_GET(self) -> None:
        self._route()

    def do_HEAD(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def _route(self) -> None:
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "Host ไม่ได้รับอนุญาต")
            return
        path = urllib.parse.urlparse(self.path).path
        try:
            self._resolve_user()
            if path.startswith("/api/"):
                self._api(path)
            elif path.startswith("/s/") and self.command in ("GET", "HEAD"):
                self._share_entry(path[len("/s/"):])
            elif self.command in ("GET", "HEAD"):
                self._static(path)
            else:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "ใช้ method นี้กับ path นี้ไม่ได้")
        except BadBody as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # เบราว์เซอร์ปิดไปกลางทาง ไม่ใช่ปัญหา
        except Exception as e:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def _api(self, path: str) -> None:
        parts = [p for p in path.split("/") if p][1:]  # ตัด 'api'
        get = self.command in ("GET", "HEAD")

        if parts and parts[0] == "worker":
            return self._worker_api(parts[1:])

        if parts and parts[0] == "auth":
            return self._auth_api(parts[1:])

        # ต้องล็อกอินก่อน (โหมด cloud) ยกเว้น endpoint สาธารณะและคนที่ถือลิงก์แชร์
        if backend.auth_required() and not self.user and tuple(parts) not in PUBLIC_API:
            if not self.share:
                return self._error(HTTPStatus.UNAUTHORIZED, "ต้องเข้าสู่ระบบก่อน")

        if parts == ["config"] and get:
            return self._json({
                "llm_ready": bool(config.llm_api_key and "your-key" not in config.llm_api_key),
                "llm_model": config.llm_model,
                "lang": config.whisper_lang,
                "diarize_available": diarize.available(),
                "diarize_missing": diarize.missing_pieces(),
                # ถอดเสียงสดทำที่ฝั่งเซิร์ฟเวอร์ ต้องมี whisper + โมเดล + ffmpeg ครบ
                # บน cloud อย่าง Vercel ไม่มีทั้งสามอย่าง ต้องบอกหน้าเว็บให้ปิดตัวเลือกนี้
                "live_available": _live_available(),
                "stt_providers": [
                    {"key": k, **v} for k, v in stt.providers().items()
                ],
                "stt_default": config.stt_provider,
                "templates": [
                    {"key": k, "label": v["label"]} for k, v in summarizer.TEMPLATES.items()
                ],
                "languages": summarizer.LANGUAGE_NAMES,
                "formats": sorted(exports.FORMATS),
                "mode": backend.mode(),
                "auth_required": backend.auth_required(),
                "user": self.user,
                "stats": store.stats(self.user_id) if (self.user or not backend.auth_required())
                         else {"count": 0, "total_duration": 0},
            })

        if parts == ["live"] and self.command == "POST":
            return self._live()

        if parts == ["meetings"]:
            if get:
                if not self.user and self.share:
                    # ถือลิงก์แชร์ เห็นได้แค่การประชุมนั้นอันเดียว
                    one = store.get(self.share["meeting_id"])
                    return self._json({"meetings": [one] if one else [], "jobs": []})
                return self._json({
                    "meetings": store.search(self._query().get("q", ""), user_id=self.user_id),
                    "jobs": jobs.active(),
                })
            if self.command == "POST":
                return self._create_draft()

        if parts == ["jobs"] and get:
            out = {"jobs": jobs.active()}
            if backend.cloud:
                out["workers"] = store.workers_list()
            return self._json(out)

        if parts == ["workers"] and get:
            if not backend.cloud:
                return self._json({"workers": []})
            return self._json({"workers": store.workers_list()})

        if len(parts) == 2 and parts[0] == "jobs":
            job = jobs.get(urllib.parse.unquote(parts[1]))
            if job is None:
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบงานนี้")
            return self._json(jobs.public(job))

        if len(parts) >= 2 and parts[0] == "meetings":
            return self._meeting(parts[1], parts[2:])

        self._error(HTTPStatus.NOT_FOUND, "ไม่พบ endpoint นี้")

    # ---------- สร้างการประชุม: draft -> tracks -> process ----------

    def _create_draft(self) -> None:
        body = self._body_json()
        title = _SAFE_TITLE_RE.sub(" ", str(body.get("title") or "")).strip() or "การประชุมไม่มีชื่อ"
        lang = (str(body.get("lang") or "")).strip() or None
        template = str(body.get("template") or summarizer.DEFAULT_TEMPLATE)
        if template not in summarizer.TEMPLATES:
            template = summarizer.DEFAULT_TEMPLATE
        try:
            num_speakers = max(0, min(20, int(body.get("num_speakers") or 0)))
        except (TypeError, ValueError):
            num_speakers = 0
        source = "record" if body.get("source") == "record" else "upload"

        if backend.auth_required() and not self.user:
            return self._error(HTTPStatus.UNAUTHORIZED, "ต้องเข้าสู่ระบบก่อนสร้างการประชุม")

        provider = str(body.get("stt") or "").strip().lower() or None
        if provider and provider not in (stt.LOCAL, stt.API):
            return self._error(HTTPStatus.BAD_REQUEST,
                               f"ตัวถอดเสียงต้องเป็น {stt.LOCAL} หรือ {stt.API}")

        mid = jobs.create_draft(
            title=title,
            language=lang,
            template=template,
            want_diarize=bool(body.get("diarize")),
            num_speakers=num_speakers,
            source=source,
            owner_id=self.user_id,
            stt_provider=provider,
        )
        self._json({"id": mid, "title": title}, HTTPStatus.CREATED)

    def _put_track(self, mid: str, name: str) -> None:
        if name not in TRACK_NAMES:
            return self._error(HTTPStatus.BAD_REQUEST,
                              f"ชื่อแทร็กต้องเป็น {', '.join(sorted(TRACK_NAMES))}")
        if jobs.draft(mid) is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมที่รออัปโหลด")

        q = self._query()
        ext = (q.get("ext") or "").lower().lstrip(".")
        if ext not in ALLOWED_EXT:
            return self._error(HTTPStatus.BAD_REQUEST,
                              f"นามสกุล '{ext}' ไม่รองรับ (ที่รับ: {', '.join(sorted(ALLOWED_EXT))})")

        # เบราว์เซอร์อัปตรงเข้าที่เก็บไปแล้ว มาแจ้งคีย์เฉยๆ ไม่ได้ส่งไบต์มา
        confirm = (q.get("key") or "").strip()
        if confirm:
            expected = f"{mid}{'' if name == 'mixed' else '_' + name}.{ext}"
            if Path(confirm).name != expected:
                return self._error(HTTPStatus.BAD_REQUEST, "คีย์ไม่ตรงกับที่ออกให้")
            if not backend.storage().exists(expected):
                return self._error(HTTPStatus.BAD_REQUEST,
                                   "ยังไม่พบไฟล์ในที่เก็บ — อัปโหลดอาจไม่สำเร็จ")
            return self._confirm_track(mid, name, expected)

        store.WEB_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "" if name == "mixed" else f"_{name}"
        dest = store.WEB_DIR / f"{mid}{suffix}.{ext}"
        err = self._read_body_to(dest, MAX_UPLOAD)
        if err:
            return self._error(HTTPStatus.BAD_REQUEST, err)
        jobs.register_track(mid, name, dest)
        self._json({"track": name, "bytes": dest.stat().st_size})

    def _track_upload_url(self, mid: str, name: str) -> None:
        """ขอลิงก์ให้เบราว์เซอร์ PUT ไฟล์ตรงเข้าที่เก็บ (เลี่ยง limit 4.5MB ของ Vercel).

        ถ้าที่เก็บเป็นดิสก์ในเครื่องจะคืน url=null แปลว่าให้ POST ไบต์มาที่เซิร์ฟเวอร์เหมือนเดิม
        """
        if name not in TRACK_NAMES:
            return self._error(HTTPStatus.BAD_REQUEST,
                               f"ชื่อแทร็กต้องเป็น {', '.join(sorted(TRACK_NAMES))}")
        if jobs.draft(mid) is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมที่รออัปโหลด")
        ext = (self._query().get("ext") or "").lower().lstrip(".")
        if ext not in ALLOWED_EXT:
            return self._error(HTTPStatus.BAD_REQUEST, f"นามสกุล '{ext}' ไม่รองรับ")

        storage = backend.storage()
        suffix = "" if name == "mixed" else f"_{name}"
        key = f"{mid}{suffix}.{ext}"
        url = storage.upload_url(key, mimetypes.guess_type(key)[0] or "application/octet-stream",
                                 expires=3600)
        self._json({"url": url, "key": key})

    def _confirm_track(self, mid: str, name: str, key: str) -> None:
        jobs.register_track(mid, name, Path(key))
        self._json({"track": name, "key": key})

    def _start(self, mid: str) -> None:
        job = jobs.start(mid)
        if job is None:
            return self._error(HTTPStatus.BAD_REQUEST,
                               "ยังไม่มีไฟล์เสียงให้ประมวลผล (อัปโหลดแทร็กก่อน)")
        self._json(job, HTTPStatus.ACCEPTED)

    # ---------- auth ----------

    def _auth_api(self, rest: list[str]) -> None:
        if not backend.auth_required():
            return self._error(HTTPStatus.NOT_IMPLEMENTED,
                               "โหมดในเครื่องไม่มีระบบล็อกอิน (ตั้ง DATABASE_URL เพื่อเปิดโหมด cloud)")

        if rest == ["me"] and self.command in ("GET", "HEAD"):
            try:
                first_run = store.count_users() == 0
            except Exception as e:
                return self._error(HTTPStatus.SERVICE_UNAVAILABLE, f"ต่อฐานข้อมูลไม่ได้: {e}")
            return self._json({"user": self.user, "first_run": first_run,
                               "share": self.share})

        if rest == ["logout"] and self.command == "POST":
            store.drop_session(self._cookie(SESSION_COOKIE))
            body = json.dumps({"ok": True}).encode()
            return self._send(200, body, "application/json; charset=utf-8",
                              {"Set-Cookie": self._cookie_header(None)})

        if rest == ["signup"] and self.command == "POST":
            return self._signup()

        if rest == ["login"] and self.command == "POST":
            return self._login()

        if rest == ["invite"] and self.command == "POST":
            if not self.user:
                return self._error(HTTPStatus.UNAUTHORIZED, "ต้องเข้าสู่ระบบก่อน")
            if not self.user.get("is_admin"):
                return self._error(HTTPStatus.FORBIDDEN, "ต้องเป็นแอดมินจึงเชิญคนอื่นได้")
            body = self._body_json()
            email = (str(body.get("email") or "").strip().lower()) or None
            code = store.create_invite(self.user["id"], email=email)
            return self._json({"code": code, "email": email})

        self._error(HTTPStatus.NOT_FOUND, "ไม่พบ endpoint นี้")

    def _login_response(self, user: dict) -> None:
        token = store.create_session(user["id"], self.headers.get("User-Agent"))
        body = json.dumps({"user": user}, ensure_ascii=False).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8",
                   {"Set-Cookie": self._cookie_header(token)})

    def _login(self) -> None:
        body = self._body_json()
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        if not email or not password:
            return self._error(HTTPStatus.BAD_REQUEST, "ต้องกรอกอีเมลและรหัสผ่าน")
        user = store.verify_password(email, password)
        if user is None:
            # ไม่บอกว่าอีเมลผิดหรือรหัสผิด กัน enumerate อีเมลในระบบ
            return self._error(HTTPStatus.UNAUTHORIZED, "อีเมลหรือรหัสผ่านไม่ถูกต้อง")
        self._login_response(user)

    def _signup(self) -> None:
        body = self._body_json()
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        invite = str(body.get("invite") or "").strip()
        name = (str(body.get("name") or "").strip() or None)

        if not _EMAIL_RE.match(email):
            return self._error(HTTPStatus.BAD_REQUEST, "อีเมลไม่ถูกต้อง")
        if len(password) < MIN_PASSWORD:
            return self._error(HTTPStatus.BAD_REQUEST,
                               f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร")

        first_run = store.count_users() == 0
        invite_ok, invite_email = (True, None) if first_run else store.invite_email(invite)
        if not first_run and not invite_ok:
            # ไม่บอกว่าอีเมลนี้มีบัญชีอยู่แล้วหรือไม่ (กัน enumerate) แต่ต้องไม่ทำให้คนที่มี
            # บัญชีอยู่แล้วไปตันตายที่การขอรหัสเชิญ
            return self._error(HTTPStatus.FORBIDDEN,
                               "ต้องมีรหัสเชิญที่ยังใช้ได้ (ขอจากแอดมินของทีม) "
                               "— ถ้ามีบัญชีอยู่แล้วให้เข้าสู่ระบบแทน")
        if invite_email and invite_email != email:
            return self._error(HTTPStatus.FORBIDDEN,
                               f"รหัสเชิญนี้ออกให้อีเมล {invite_email} เท่านั้น")
        if store.has_password(email):
            return self._error(HTTPStatus.CONFLICT, "อีเมลนี้มีบัญชีอยู่แล้ว — เข้าสู่ระบบเลย")

        # คนแรกของระบบเป็นแอดมิน (ยังไม่มีใครเชิญได้)
        user = store.ensure_user(email, name=name, is_admin=first_run)
        store.set_password(user["id"], password)
        if not first_run:
            store.redeem_invite(invite, user["id"])
        user["is_admin"] = first_run or user.get("is_admin", False)
        self._login_response(user)

    # ---------- API สำหรับ worker แยกเครื่อง ----------

    def _worker_authed(self) -> bool:
        """ต้องตั้ง WORKER_TOKEN ก่อน ไม่งั้นปิด API นี้ทั้งชุด — กันเปิดช่องไว้เฉยๆ."""
        expected = config.worker_token
        if not expected:
            return False
        got = (self.headers.get("Authorization") or "")
        prefix = "Bearer "
        if not got.startswith(prefix):
            return False
        return hmac.compare_digest(got[len(prefix):].strip(), expected)

    def _worker_api(self, rest: list[str]) -> None:
        if not self._worker_authed():
            detail = ("ยังไม่ได้ตั้ง WORKER_TOKEN ฝั่งเซิร์ฟเวอร์"
                      if not config.worker_token else "token ไม่ถูกต้อง")
            return self._error(HTTPStatus.FORBIDDEN, f"worker API ปฏิเสธ: {detail}")

        if rest == ["heartbeat"] and self.command == "POST":
            body = self._body_json()
            name = str(body.get("worker") or "").strip()[:80]
            if not name:
                return self._error(HTTPStatus.BAD_REQUEST, "ต้องส่งชื่อ worker")
            if backend.cloud:
                store.worker_seen(name, str(body.get("status") or "idle")[:16],
                                  body.get("job") or None, str(body.get("gpu") or "")[:120] or None)
            return self._json({"ok": True, "stale_after": 75})

        if rest == ["claim"] and self.command == "POST":
            worker = str((self._body_json().get("worker") or "")).strip()[:80] or None
            spec = jobs.claim(worker)
            if spec is None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if spec["kind"] == "process":
                storage = backend.storage()
                external = storage.kind != "local"
                urls = {}
                for name in spec["tracks"]:
                    key = jobs.track_path(spec["id"], name)
                    signed = storage.download_url(Path(key).name, 6 * 3600) if (external and key) else None
                    # ที่เก็บภายนอก: ให้ worker ดึงตรง ไม่ต้องไหลผ่าน serverless function
                    urls[name] = signed or f"/api/worker/jobs/{spec['id']}/tracks/{name}"
                spec["track_urls"] = urls
                if external:
                    playback_key = f"{spec['id']}.ogg"
                    spec["playback_key"] = playback_key
                    spec["playback_upload_url"] = storage.upload_url(
                        playback_key, "audio/ogg", expires=6 * 3600)
            return self._json(spec)

        if len(rest) >= 3 and rest[0] == "jobs":
            job_id, action = rest[1], rest[2]
            job_id = urllib.parse.unquote(job_id)

            if action == "tracks" and len(rest) == 4 and self.command in ("GET", "HEAD"):
                path = jobs.track_path(job_id, rest[3])
                if path is None or not path.exists():
                    return self._error(HTTPStatus.NOT_FOUND, "ไม่พบไฟล์แทร็ก")
                return self._send_file(path)

            if len(rest) != 3 or self.command != "POST":
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบ endpoint นี้")

            if jobs.get(job_id) is None:
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบงานนี้")

            if action == "progress":
                body = self._body_json()
                try:
                    value = float(body.get("progress", 0))
                except (TypeError, ValueError):
                    value = 0.0
                jobs.report_progress(job_id, str(body.get("step") or "")[:120], value)
                return self._json({"ok": True})

            if action == "audio":
                ext = (self._query().get("ext") or "ogg").lower().lstrip(".")
                if ext not in ALLOWED_EXT:
                    return self._error(HTTPStatus.BAD_REQUEST, f"นามสกุล '{ext}' ไม่รองรับ")
                store.WEB_DIR.mkdir(parents=True, exist_ok=True)
                dest = store.WEB_DIR / f"{job_id}.{ext}"
                err = self._read_body_to(dest, MAX_UPLOAD)
                if err:
                    return self._error(HTTPStatus.BAD_REQUEST, err)
                return self._json({"playback": str(dest)})

            if action == "result":
                body = self._body_json()
                worker = str(body.pop("worker", "") or "").strip()[:80]
                try:
                    jobs.apply_result(job_id, body)
                except Exception as e:
                    jobs.fail(job_id, str(e))
                    return self._error(HTTPStatus.BAD_REQUEST, str(e))
                if worker and backend.cloud:
                    store.worker_finished(worker)
                return self._json({"ok": True})

            if action == "error":
                jobs.fail(job_id, str(self._body_json().get("error") or "worker ไม่ได้บอกสาเหตุ"))
                return self._json({"ok": True})

            if action == "requeue":
                return self._json({"requeued": jobs.requeue(job_id)})

        self._error(HTTPStatus.NOT_FOUND, "ไม่พบ endpoint นี้")

    def _send_file(self, path: Path) -> None:
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _live(self) -> None:
        """ถอดเสียงคลิปสั้นเพื่อแสดงสดระหว่างประชุม — ไม่บันทึกอะไรลงคลัง."""
        q = self._query()
        ext = (q.get("ext") or "webm").lower().lstrip(".")
        if ext not in ALLOWED_EXT:
            return self._error(HTTPStatus.BAD_REQUEST, f"นามสกุล '{ext}' ไม่รองรับ")
        if not _live_available():
            return self._error(HTTPStatus.NOT_IMPLEMENTED,
                               "เซิร์ฟเวอร์นี้ถอดเสียงเองไม่ได้ (ไม่มี whisper/ffmpeg) "
                               "— ข้อความสดใช้ได้เฉพาะตอนรันบนเครื่องที่ติดตั้งครบ")
        lang = (q.get("lang") or "").strip() or None
        # ข้อความจากคลิปก่อนหน้า ใช้เป็นบริบทให้ถอดต่อเนื่องแม่นขึ้น
        prompt = (q.get("prompt") or "").strip()[:600] or None

        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / f"live.{ext}"
            err = self._read_body_to(clip, MAX_LIVE_CLIP)
            if err:
                return self._error(HTTPStatus.BAD_REQUEST, err)
            try:
                text = jobs.transcribe_clip(clip, lang, prompt=prompt)
            except Exception as e:
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        self._json({"text": text})

    # ---------- meetings ----------

    def _meeting(self, mid: str, rest: list[str]) -> None:
        mid = urllib.parse.unquote(mid)

        # แทร็กและการสั่งประมวลผลทำกับ draft ที่ยังไม่มีในคลัง จึงเช็คก่อน store
        if len(rest) == 3 and rest[0] == "tracks" and rest[2] == "upload-url":
            if not store.valid_id(mid):
                return self._error(HTTPStatus.BAD_REQUEST, "id ไม่ถูกต้อง")
            return self._track_upload_url(mid, rest[1])
        if len(rest) == 2 and rest[0] == "tracks" and self.command == "POST":
            if not store.valid_id(mid):
                return self._error(HTTPStatus.BAD_REQUEST, "id ไม่ถูกต้อง")
            return self._put_track(mid, rest[1])
        if rest == ["process"] and self.command == "POST":
            if not store.valid_id(mid):
                return self._error(HTTPStatus.BAD_REQUEST, "id ไม่ถูกต้อง")
            return self._start(mid)

        if not store.valid_id(mid):
            return self._error(HTTPStatus.BAD_REQUEST, "id ไม่ถูกต้อง")

        # เจ้าของเท่านั้นที่ลบ/เปลี่ยน visibility/จัดการลิงก์แชร์ได้
        # ส่วนคนในทีมและคนถือลิงก์แบบแก้ได้ เกลาสรุป/บทถอดเสียงได้
        if backend.auth_required():
            owner_only = (self.command == "DELETE" and not rest) or rest[:1] in (["share"], ["visibility"])
            writing = self.command in ("PATCH", "DELETE", "POST")
            if owner_only:
                allowed = self._is_owner(mid)
            else:
                allowed = self._may_write(mid) if writing else self._may_read(mid)
            if not allowed:
                return self._error(HTTPStatus.FORBIDDEN, "ไม่มีสิทธิ์กับการประชุมนี้")

        if rest == ["share"]:
            return self._share(mid)
        if rest == ["visibility"] and self.command == "PATCH":
            return self._visibility(mid)
        if rest == ["audio"]:
            return self._audio(mid)
        if len(rest) == 1 and rest[0].startswith("export."):
            return self._export(mid, rest[0].split(".", 1)[1])
        if rest == ["resummarize"]:
            if self.command != "POST":
                return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "ต้องใช้ POST")
            meeting = store.get(mid)
            if meeting is None:
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
            return self._json(jobs.submit_summarize(mid, meeting["title"]), HTTPStatus.ACCEPTED)
        if rest == ["translate"]:
            if self.command != "POST":
                return self._error(HTTPStatus.METHOD_NOT_ALLOWED, "ต้องใช้ POST")
            return self._translate(mid)
        if rest:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบ endpoint นี้")

        if self.command in ("GET", "HEAD"):
            meeting = store.get(mid)
            if meeting is None:
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
            return self._json(meeting)

        if self.command == "PATCH":
            return self._patch(mid)

        if self.command == "DELETE":
            meeting = store.get(mid)
            if not store.delete(mid):
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
            # เก็บกวาดไฟล์ในที่เก็บภายนอกด้วย ไม่งั้นจ่ายค่าเก็บของที่ลบไปแล้ว
            storage = backend.storage()
            if storage.kind != "local" and (meeting or {}).get("audio"):
                for key in {meeting["audio"], f"{mid}.ogg", f"{mid}_mic.webm", f"{mid}_system.webm"}:
                    storage.delete(key)
            return self._json({"deleted": mid})

        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "ใช้ method นี้กับ path นี้ไม่ได้")

    def _patch(self, mid: str) -> None:
        body = self._body_json()
        title, summary, segments = body.get("title"), body.get("summary"), body.get("segments")
        if title is None and summary is None and segments is None:
            return self._error(HTTPStatus.BAD_REQUEST,
                               "ต้องส่ง title, summary หรือ segments มาอย่างน้อยหนึ่งอย่าง")

        if segments is not None:
            clean = _clean_segments(segments)
            if clean is None:
                return self._error(HTTPStatus.BAD_REQUEST, "รูปแบบ segments ไม่ถูกต้อง")
            if store.set_segments(mid, clean) is None:
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")

        updated = store.get(mid) if (title is None and summary is None) else \
            store.update(mid, title=title, summary=summary)
        if updated is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
        self._json(updated)

    def _share(self, mid: str) -> None:
        """สร้าง/ดู/ยกเลิกลิงก์แชร์ — ต้องเป็นคนที่ล็อกอินอยู่ (คนถือลิงก์ต่อลิงก์ใหม่ไม่ได้)."""
        if not backend.auth_required():
            return self._error(HTTPStatus.NOT_IMPLEMENTED,
                               "โหมดในเครื่องยังไม่มีลิงก์แชร์ (ตั้ง DATABASE_URL เพื่อเปิดโหมด cloud)")
        if not self.user:
            return self._error(HTTPStatus.UNAUTHORIZED, "ต้องเข้าสู่ระบบก่อน")
        if store.get(mid) is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")

        if self.command in ("GET", "HEAD"):
            return self._json({"shares": store.list_shares(mid)})

        if self.command == "POST":
            body = self._body_json()
            days = body.get("days")
            try:
                days = int(days) if days else None
            except (TypeError, ValueError):
                days = None
            token = store.create_share(mid, self.user["id"],
                                       can_edit=bool(body.get("can_edit")), days=days)
            return self._json({"token": token, "path": f"/s/{token}"}, HTTPStatus.CREATED)

        if self.command == "DELETE":
            return self._json({"revoked": store.revoke_shares(mid)})

        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "ใช้ method นี้กับ path นี้ไม่ได้")

    def _visibility(self, mid: str) -> None:
        if not backend.auth_required():
            return self._error(HTTPStatus.NOT_IMPLEMENTED, "โหมดในเครื่องไม่มีการแชร์ในทีม")
        if not self.user:
            return self._error(HTTPStatus.UNAUTHORIZED, "ต้องเข้าสู่ระบบก่อน")
        value = str(self._body_json().get("visibility") or "")
        if value not in ("private", "team"):
            return self._error(HTTPStatus.BAD_REQUEST, "visibility ต้องเป็น private หรือ team")
        if store.set_visibility(mid, value) is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
        self._json(store.get(mid))

    def _translate(self, mid: str) -> None:
        lang = str(self._body_json().get("lang") or "").strip()
        if not lang:
            return self._error(HTTPStatus.BAD_REQUEST, "ต้องระบุ lang")
        meeting = store.get(mid)
        if meeting is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
        self._json(jobs.submit_translate(mid, meeting["title"], lang), HTTPStatus.ACCEPTED)

    def _parse_range(self, size: int) -> tuple[int, int] | None:
        """แปลง header Range เป็น (start, end) แบบรวมปลาย — None ถ้าไม่มีหรืออ่านไม่ได้."""
        raw = self.headers.get("Range")
        if not raw:
            return None
        m = re.match(r"bytes=(\d*)-(\d*)$", raw.strip())
        if not m or not (m.group(1) or m.group(2)):
            return None
        if not m.group(1):                       # bytes=-N  = N ไบต์ท้ายไฟล์
            length = min(int(m.group(2)), size)
            return (size - length, size - 1)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
        if start >= size:
            return None
        return (start, min(end, size - 1))

    def _audio(self, mid: str) -> None:
        meeting = store.get(mid)
        if meeting is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")

        key = meeting.get("audio")
        storage = backend.storage()
        if key and storage.kind != "local":
            # ให้เบราว์เซอร์ไปดึงจากที่เก็บโดยตรง (รองรับ Range ของฝั่งนั้นเอง)
            # ไฟล์ประชุมใหญ่เกินกว่าจะให้ไหลผ่าน serverless function
            url = storage.download_url(key, expires=6 * 3600)
            if url:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", url)
                self.send_header("Cache-Control", "private, max-age=300")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        path = store.audio_path(meeting)
        if path is None or not path.exists():
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบไฟล์เสียง")

        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        size = path.stat().st_size
        # ต้องรองรับ Range ไม่งั้นเลื่อนหาตำแหน่งในไฟล์ประชุมยาวๆ ไม่ได้
        span = self._parse_range(size)
        start, end = span if span else (0, size - 1)
        length = end - start + 1

        self.send_response(HTTPStatus.PARTIAL_CONTENT if span else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if span:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return

        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _export(self, mid: str, fmt: str) -> None:
        meeting = store.get(mid)
        if meeting is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
        try:
            body, ctype = exports.render(meeting, fmt.lower())
        except ValueError as e:
            return self._error(HTTPStatus.BAD_REQUEST, str(e))
        ext = exports.FORMATS[fmt.lower()][1]
        name = urllib.parse.quote(f"{meeting['title']}{ext}")
        self._send(200, body, ctype,
                   {"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})

    # ---------- static ----------

    def _share_entry(self, token: str) -> None:
        """เปิดลิงก์แชร์: ฝากโทเคนไว้ในคุกกี้แล้วเสิร์ฟหน้าเว็บตัวเดิม (โหมดอ่าน)."""
        token = urllib.parse.unquote(token).strip("/")
        target = None
        if backend.auth_required() and token:
            try:
                target = store.share_target(token)
            except Exception:
                target = None
        if target is None:
            return self._error(HTTPStatus.NOT_FOUND, "ลิงก์แชร์นี้ใช้ไม่ได้แล้ว")

        page = (STATIC_DIR / "index.html").read_bytes()
        https = (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        flags = "HttpOnly; SameSite=Lax; Path=/" + ("; Secure" if https else "")
        self._send(200, page, "text/html; charset=utf-8", {
            "Set-Cookie": f"mai_share={urllib.parse.quote(token)}; Max-Age={7 * 86400}; {flags}",
            "Cache-Control": "no-store",
        })

    def _static(self, path: str) -> None:
        if path in ("/", ""):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
            # manifest กับ service worker ต้องอยู่ราก ไม่งั้น scope ของ PWA จะแคบเกินไป
            rel = path.lstrip("/")
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบไฟล์")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype, {"Cache-Control": "no-cache"})


def _clean_segments(raw) -> list[dict] | None:
    """ตรวจและทำความสะอาด segments ที่ผู้ใช้แก้มาจากหน้าเว็บ."""
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", 0))
        except (TypeError, ValueError):
            return None
        text = str(item.get("text", "")).strip()
        seg = {"start": round(start, 2), "end": round(end, 2), "text": text}
        speaker = item.get("speaker")
        if speaker:
            seg["speaker"] = _SAFE_TITLE_RE.sub(" ", str(speaker)).strip()[:60]
        out.append(seg)
    return out


class Server(ThreadingHTTPServer):
    daemon_threads = True
    bound_host = "127.0.0.1"


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    mimetypes.add_type("application/javascript", ".js")
    httpd = Server((host, port), Handler)
    httpd.bound_host = host
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"

    print(f"🌐 meeting_ai web  →  {url}")
    print(f"   เก็บข้อมูลแบบ: {backend.mode()}"
          + ("  (มีระบบล็อกอิน)" if backend.auth_required() else "  (ไม่มีล็อกอิน)"))
    if host not in ("127.0.0.1", "localhost", "::1") and not backend.auth_required():
        print("⚠️  ผูกกับ interface ภายนอก และไม่มีระบบล็อกอิน — ใครในเครือข่ายก็เปิดได้")
    if not (config.llm_api_key and "your-key" not in config.llm_api_key):
        print("⚠️  ยังไม่ได้ตั้ง LLM_API_KEY ใน .env — ถอดเสียงได้แต่จะสรุปไม่ได้")
    if not diarize.available():
        print("ℹ️  แยกผู้พูดยังใช้ไม่ได้ ขาด: " + "; ".join(diarize.missing_pieces()))
    if config.remote_worker:
        print("ℹ️  REMOTE_WORKER=1 — งานหนักรอ `mai worker` มารับ ไม่ประมวลผลในโพรเซสนี้")
    elif backend.cloud:
        # คิวอยู่ใน DB ต้องมีเธรดคอย poll ไม่ใช่รอ notify ในโพรเซส
        jobs._ensure_worker()
    # flush เอง — เวลา redirect output ลงไฟล์ stdout จะเป็น block-buffered แล้ว URL ไม่โผล่ให้เห็น
    print("   กด Ctrl+C เพื่อปิด\n", flush=True)

    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 ปิดเซิร์ฟเวอร์แล้ว")
    finally:
        httpd.server_close()
