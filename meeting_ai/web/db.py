"""การเชื่อมต่อ Postgres — ใช้เฉพาะโหมด cloud (ตั้ง DATABASE_URL แล้ว).

โหมดในเครื่องไม่ต้องมี psycopg และไม่ต้องมี DB — โมดูลนี้ import ได้เสมอแต่จะบอกว่า disabled
"""

from __future__ import annotations

import os
import socket
import threading
import urllib.parse
from contextlib import contextmanager
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
CONNECT_TIMEOUT = 15

_pool = None
_pool_error: str | None = None
_lock = threading.Lock()
_hostaddr_cache: dict[str, str | None] = {}


def url() -> str:
    return (os.environ.get("DATABASE_URL") or "").strip()


def enabled() -> bool:
    return bool(url())


def missing_pieces() -> list[str]:
    gaps = []
    if not url():
        gaps.append("ตัวแปร DATABASE_URL")
    try:
        import psycopg  # noqa: F401
        import psycopg_pool  # noqa: F401
    except ImportError:
        gaps.append("แพ็กเกจ psycopg (pip install 'psycopg[binary]' psycopg-pool)")
    return gaps


def _pick_ipv4(host: str) -> str | None:
    """หา IPv4 ของ host — คืน None ถ้าไม่มี A record.

    เครื่องที่ DNS คืน AAAA มาก่อนแต่ไม่มีเส้น IPv6 จริง จะเสียเวลารอ timeout ทีละที่อยู่
    (เจอจริง: 3 × 21 วิ = 63 วิ ก่อนจะตกมา IPv4) ส่ง hostaddr ให้ libpq เลยจะข้ามปัญหานี้
    ส่วน host เดิมยังถูกใช้ทำ TLS/SNI ตามปกติ
    """
    if host in _hostaddr_cache:
        return _hostaddr_cache[host]
    addr = None
    try:
        infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
        if infos:
            addr = infos[0][4][0]
    except socket.gaierror:
        addr = None
    _hostaddr_cache[host] = addr
    return addr


def _connect_kwargs() -> dict:
    # prepare_threshold=None ปิด prepared statement อัตโนมัติของ psycopg3
    # จำเป็นกับ connection pooler แบบ transaction mode (Supabase :6543, PgBouncer)
    # ที่สลับ backend ต่อ transaction — ไม่งั้นเจอ error
    # "prepared statement _pg3_x does not exist / requires N parameters"
    kwargs: dict = {
        "autocommit": True,
        "connect_timeout": CONNECT_TIMEOUT,
        "prepare_threshold": None,
    }
    raw = url()
    if "hostaddr" in raw:
        return kwargs  # ผู้ใช้ระบุมาเองแล้ว ไม่ต้องยุ่ง
    host = urllib.parse.urlparse(raw).hostname
    if host:
        ipv4 = _pick_ipv4(host)
        if ipv4:
            kwargs["hostaddr"] = ipv4
    return kwargs


def _get_pool():
    """พูลเล็กๆ — Neon pooler ทำ pooling ตัวจริงให้อยู่แล้ว ฝั่งเราแค่ไม่ต้องต่อใหม่ทุก request

    min_size=0 เพื่อให้บน serverless ที่ instance ถูก freeze ไม่มี connection ค้าง
    """
    global _pool, _pool_error
    if _pool is not None:
        return _pool
    with _lock:
        if _pool is not None:
            return _pool
        if _pool_error:
            raise RuntimeError(_pool_error)
        gaps = missing_pieces()
        if gaps:
            _pool_error = "ต่อฐานข้อมูลไม่ได้ ยังขาด: " + "; ".join(gaps)
            raise RuntimeError(_pool_error)

        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            url(),
            min_size=0,
            max_size=4,
            timeout=CONNECT_TIMEOUT + 10,
            max_idle=120,
            # instance ที่ตื่นจากการ freeze อาจถือ connection ที่ตายแล้ว ให้เช็คก่อนใช้
            check=ConnectionPool.check_connection,
            kwargs=_connect_kwargs(),
            open=True,
        )
        return _pool


@contextmanager
def connect():
    """ยืม connection จากพูล (autocommit) — ใช้เป็น context manager."""
    with _get_pool().connection() as conn:
        yield conn


@contextmanager
def transaction():
    """ทำหลาย statement ให้เป็นหน่วยเดียว."""
    with connect() as conn, conn.transaction():
        yield conn


def init() -> list[str]:
    """สร้าง schema/ตาราง (รันซ้ำได้) คืนรายชื่อตารางที่มีอยู่หลังรัน."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect() as conn:
        conn.execute(sql)
        rows = conn.execute(
            """select table_name from information_schema.tables
               where table_schema = 'meeting_ai' order by table_name"""
        ).fetchall()
    return [r[0] for r in rows]


def close() -> None:
    global _pool
    with _lock:
        if _pool is not None:
            _pool.close()
            _pool = None
