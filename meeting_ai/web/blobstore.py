"""ที่เก็บไฟล์เสียง — ดิสก์ในเครื่อง หรือ S3-compatible (Cloudflare R2 / S3 / B2).

ทำไมต้อง presigned URL: Vercel Function รับ body ได้แค่ 4.5 MB ส่งไฟล์ประชุมผ่านไม่ได้
เบราว์เซอร์จึงต้อง PUT ตรงเข้า R2 ด้วยลิงก์ที่เซ็นมาแล้ว และ worker ก็ GET ตรงเช่นกัน
ตัวเซิร์ฟเวอร์ไม่ต้องแตะไบต์ของไฟล์เลย

เซ็น SigV4 เองด้วย hmac/hashlib จาก stdlib — ไม่ต้องลง boto3 (ซึ่งใหญ่เกินไปสำหรับ serverless)
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import os
import socket
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED = "UNSIGNED-PAYLOAD"
DEFAULT_EXPIRES = 3600


# ---------- ต่อ HTTPS โดยเลือก IPv4 ก่อน ----------

class _IPv4First(http.client.HTTPSConnection):
    """ลอง IPv4 ก่อนเสมอ แล้วค่อยตกไป IPv6.

    เครื่องที่ DNS คืน AAAA มาก่อนแต่ไม่มีเส้น IPv6 จริง จะรอ timeout ทีละที่อยู่
    (เจอจริงกับ R2 บนเครื่องนี้: 2 × 21 วิ = 42 วิ ต่อ request) เรียง IPv4 ขึ้นก่อน
    แก้ได้โดยไม่พังเครือข่ายที่มีแต่ IPv6 เพราะยังเหลือ IPv6 เป็นตัวสำรอง
    """

    def connect(self) -> None:
        infos = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)
        infos.sort(key=lambda i: 0 if i[0] == socket.AF_INET else 1)
        last: Exception | None = None
        for family, kind, proto, _canon, addr in infos:
            sock = None
            try:
                sock = socket.socket(family, kind, proto)
                sock.settimeout(self.timeout if self.timeout is not None else 30)
                if self.source_address:
                    sock.bind(self.source_address)
                sock.connect(addr)
                self.sock = sock
                break
            except OSError as e:
                last = e
                if sock is not None:
                    sock.close()
        else:
            raise last or OSError(f"ต่อ {self.host}:{self.port} ไม่ได้")

        if self._tunnel_host:
            self._tunnel()
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self._tunnel_host or self.host)


class _IPv4Handler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4First, req, context=self._context)


_opener = urllib.request.build_opener(_IPv4Handler())


def open_url(req, timeout: int = 300):
    """เปิด URL ด้วย opener ที่เลือก IPv4 ก่อน — ใช้กับที่เก็บภายนอกทั้งหมด."""
    return _opener.open(req, timeout=timeout)


# ---------- SigV4 ----------

def _quote(value: str, safe: str = "/") -> str:
    return urllib.parse.quote(value, safe=safe)


def _signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    key = f"AWS4{secret}".encode()
    for part in (datestamp, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def presign(
    method: str,
    endpoint: str,
    bucket: str,
    key: str,
    access_key: str,
    secret_key: str,
    region: str = "auto",
    expires: int = DEFAULT_EXPIRES,
    now: datetime | None = None,
    extra_query: dict[str, str] | None = None,
) -> str:
    """สร้าง presigned URL แบบ query-string (SigV4).

    `now` มีไว้ให้เทสต์ล็อกเวลาได้ ปกติไม่ต้องส่ง
    """
    now = now or datetime.now(timezone.utc)
    amzdate = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")

    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.netloc
    base_path = parsed.path.rstrip("/")
    # bucket ว่าง = endpoint ชี้ที่ bucket อยู่แล้ว (virtual-hosted style)
    canonical_uri = f"{base_path}/{_quote(bucket)}/{_quote(key)}" if bucket else f"{base_path}/{_quote(key)}"

    credential = f"{access_key}/{datestamp}/{region}/s3/aws4_request"
    query = {
        "X-Amz-Algorithm": ALGORITHM,
        "X-Amz-Credential": credential,
        "X-Amz-Date": amzdate,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    query.update(extra_query or {})
    canonical_query = "&".join(
        f"{_quote(k, safe='')}={_quote(v, safe='')}" for k, v in sorted(query.items())
    )

    canonical_request = "\n".join([
        method.upper(), canonical_uri, canonical_query,
        f"host:{host}\n", "host", UNSIGNED,
    ])
    string_to_sign = "\n".join([
        ALGORITHM, amzdate, f"{datestamp}/{region}/s3/aws4_request",
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(
        _signing_key(secret_key, datestamp, region, "s3"),
        string_to_sign.encode(), hashlib.sha256,
    ).hexdigest()

    return f"{parsed.scheme}://{host}{canonical_uri}?{canonical_query}&X-Amz-Signature={signature}"


# ---------- interface ----------

class Storage(ABC):
    kind = "abstract"

    @abstractmethod
    def upload_url(self, key: str, content_type: str, expires: int = DEFAULT_EXPIRES) -> str | None:
        """ลิงก์ให้เบราว์เซอร์ PUT ตรง — None ถ้าที่เก็บนี้ไม่รองรับ (ต้องผ่านเซิร์ฟเวอร์)."""

    @abstractmethod
    def download_url(self, key: str, expires: int = DEFAULT_EXPIRES) -> str | None:
        """ลิงก์ให้ดาวน์โหลดตรง — None ถ้าต้องให้เซิร์ฟเวอร์สตรีมให้."""

    @abstractmethod
    def put(self, key: str, data: bytes) -> str: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    def local_path(self, key: str) -> Path | None:
        """path บนดิสก์ถ้ามี — ให้เซิร์ฟเวอร์สตรีมแบบ Range ได้โดยไม่ต้องโหลดทั้งไฟล์."""
        return None


class LocalStorage(Storage):
    """เก็บบนดิสก์ — โหมดในเครื่อง และตอนรัน cloud stack บนเครื่องตัวเอง."""

    kind = "local"

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        # ใช้แค่ basename กัน path หลุดออกนอกโฟลเดอร์
        return self.root / Path(key).name

    def upload_url(self, key, content_type, expires=DEFAULT_EXPIRES):
        return None

    def download_url(self, key, expires=DEFAULT_EXPIRES):
        return None

    def put(self, key: str, data: bytes) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        self._path(key).write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def local_path(self, key: str) -> Path | None:
        p = self._path(key)
        return p if p.is_file() else None


class S3Storage(Storage):
    """Cloudflare R2 หรือที่เก็บอื่นที่พูด S3 ได้."""

    kind = "s3"

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str,
                 region: str = "auto") -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region

    def _sign(self, method: str, key: str, expires: int, extra=None) -> str:
        return presign(method, self.endpoint, self.bucket, key,
                       self.access_key, self.secret_key, self.region, expires,
                       extra_query=extra)

    def upload_url(self, key, content_type, expires=DEFAULT_EXPIRES):
        # ไม่เซ็น content-type ไว้ใน header เพราะเบราว์เซอร์บางตัวเติม charset เองแล้วลายเซ็นพัง
        return self._sign("PUT", key, expires)

    def download_url(self, key, expires=DEFAULT_EXPIRES):
        return self._sign("GET", key, expires)

    def _request(self, method: str, key: str, data: bytes | None = None) -> bytes:
        url = self._sign(method, key, 300)
        req = urllib.request.Request(url, data=data, method=method)
        with open_url(req) as resp:
            return resp.read()

    def put(self, key: str, data: bytes) -> str:
        self._request("PUT", key, data)
        return key

    def get(self, key: str) -> bytes:
        return self._request("GET", key)

    def exists(self, key: str) -> bool:
        try:
            self._request("HEAD", key)
            return True
        except Exception:
            return False

    def delete(self, key: str) -> None:
        try:
            self._request("DELETE", key)
        except Exception:
            pass  # ลบไฟล์ที่ไม่มีอยู่ไม่ถือเป็นความผิดพลาด


# ---------- เลือกตัวที่จะใช้ ----------

_current: Storage | None = None


def missing_pieces() -> list[str]:
    """สิ่งที่ยังขาดถ้าตั้งใจจะใช้ S3 — ว่าง = พร้อม (หรือไม่ได้ตั้งใจใช้)."""
    if not (os.environ.get("S3_BUCKET") or os.environ.get("S3_ENDPOINT")):
        return []
    gaps = []
    for name in ("S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY"):
        if not os.environ.get(name):
            gaps.append(name)
    return gaps


def get_storage(local_root: Path) -> Storage:
    global _current
    if _current is not None:
        return _current
    if not missing_pieces() and os.environ.get("S3_BUCKET"):
        _current = S3Storage(
            endpoint=os.environ["S3_ENDPOINT"],
            bucket=os.environ["S3_BUCKET"],
            access_key=os.environ["S3_ACCESS_KEY_ID"],
            secret_key=os.environ["S3_SECRET_ACCESS_KEY"],
            region=os.environ.get("S3_REGION", "auto"),
        )
    else:
        _current = LocalStorage(local_root)
    return _current


def reset() -> None:
    """ให้เทสต์สลับที่เก็บได้."""
    global _current
    _current = None
