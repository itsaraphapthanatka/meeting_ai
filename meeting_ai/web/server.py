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

from .. import diarize, summarizer
from ..config import config
from . import exports, jobs, store

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

_SAFE_TITLE_RE = re.compile(r"[\r\n\t]+")


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

    def _body_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

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
            if path.startswith("/api/"):
                self._api(path)
            elif self.command in ("GET", "HEAD"):
                self._static(path)
            else:
                self._error(HTTPStatus.METHOD_NOT_ALLOWED, "ใช้ method นี้กับ path นี้ไม่ได้")
        except (BrokenPipeError, ConnectionResetError):
            pass  # เบราว์เซอร์ปิดไปกลางทาง ไม่ใช่ปัญหา
        except Exception as e:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))

    def _api(self, path: str) -> None:
        parts = [p for p in path.split("/") if p][1:]  # ตัด 'api'
        get = self.command in ("GET", "HEAD")

        if parts == ["config"] and get:
            return self._json({
                "llm_ready": bool(config.llm_api_key and "your-key" not in config.llm_api_key),
                "llm_model": config.llm_model,
                "lang": config.whisper_lang,
                "diarize_available": diarize.available(),
                "diarize_missing": diarize.missing_pieces(),
                "templates": [
                    {"key": k, "label": v["label"]} for k, v in summarizer.TEMPLATES.items()
                ],
                "languages": summarizer.LANGUAGE_NAMES,
                "formats": sorted(exports.FORMATS),
                "stats": store.stats(),
            })

        if parts and parts[0] == "worker":
            return self._worker_api(parts[1:])

        if parts == ["live"] and self.command == "POST":
            return self._live()

        if parts == ["meetings"]:
            if get:
                return self._json({
                    "meetings": store.search(self._query().get("q", "")),
                    "jobs": jobs.active(),
                })
            if self.command == "POST":
                return self._create_draft()

        if parts == ["jobs"] and get:
            return self._json({"jobs": jobs.active()})

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

        mid = jobs.create_draft(
            title=title,
            language=lang,
            template=template,
            want_diarize=bool(body.get("diarize")),
            num_speakers=num_speakers,
            source=source,
        )
        self._json({"id": mid, "title": title}, HTTPStatus.CREATED)

    def _put_track(self, mid: str, name: str) -> None:
        if name not in TRACK_NAMES:
            return self._error(HTTPStatus.BAD_REQUEST,
                              f"ชื่อแทร็กต้องเป็น {', '.join(sorted(TRACK_NAMES))}")
        if jobs.draft(mid) is None:
            return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมที่รออัปโหลด")

        ext = (self._query().get("ext") or "").lower().lstrip(".")
        if ext not in ALLOWED_EXT:
            return self._error(HTTPStatus.BAD_REQUEST,
                              f"นามสกุล '{ext}' ไม่รองรับ (ที่รับ: {', '.join(sorted(ALLOWED_EXT))})")

        store.WEB_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "" if name == "mixed" else f"_{name}"
        dest = store.WEB_DIR / f"{mid}{suffix}.{ext}"
        err = self._read_body_to(dest, MAX_UPLOAD)
        if err:
            return self._error(HTTPStatus.BAD_REQUEST, err)
        jobs.register_track(mid, name, dest)
        self._json({"track": name, "bytes": dest.stat().st_size})

    def _start(self, mid: str) -> None:
        job = jobs.start(mid)
        if job is None:
            return self._error(HTTPStatus.BAD_REQUEST,
                               "ยังไม่มีไฟล์เสียงให้ประมวลผล (อัปโหลดแทร็กก่อน)")
        self._json(job, HTTPStatus.ACCEPTED)

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

        if rest == ["claim"] and self.command == "POST":
            spec = jobs.claim()
            if spec is None:
                self.send_response(HTTPStatus.NO_CONTENT)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if spec["kind"] == "process":
                spec["track_urls"] = {
                    name: f"/api/worker/jobs/{spec['id']}/tracks/{name}"
                    for name in spec["tracks"]
                }
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
                try:
                    jobs.apply_result(job_id, body)
                except Exception as e:
                    jobs.fail(job_id, str(e))
                    return self._error(HTTPStatus.BAD_REQUEST, str(e))
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
        lang = (q.get("lang") or "").strip() or None

        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / f"live.{ext}"
            err = self._read_body_to(clip, MAX_LIVE_CLIP)
            if err:
                return self._error(HTTPStatus.BAD_REQUEST, err)
            try:
                text = jobs.transcribe_clip(clip, lang)
            except Exception as e:
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
        self._json({"text": text})

    # ---------- meetings ----------

    def _meeting(self, mid: str, rest: list[str]) -> None:
        mid = urllib.parse.unquote(mid)

        # แทร็กและการสั่งประมวลผลทำกับ draft ที่ยังไม่มีในคลัง จึงเช็คก่อน store
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
            if not store.delete(mid):
                return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมนี้")
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

    def _static(self, path: str) -> None:
        if path in ("/", ""):
            rel = "index.html"
        elif path.startswith("/static/"):
            rel = path[len("/static/"):]
        else:
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
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("⚠️  ผูกกับ interface ภายนอก และไม่มีระบบล็อกอิน — ใครในเครือข่ายก็เปิดได้")
    if not (config.llm_api_key and "your-key" not in config.llm_api_key):
        print("⚠️  ยังไม่ได้ตั้ง LLM_API_KEY ใน .env — ถอดเสียงได้แต่จะสรุปไม่ได้")
    if not diarize.available():
        print("ℹ️  แยกผู้พูดยังใช้ไม่ได้ ขาด: " + "; ".join(diarize.missing_pieces()))
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
