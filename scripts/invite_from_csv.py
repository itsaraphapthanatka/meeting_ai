"""ออกรหัสเชิญให้พนักงานจากไฟล์ CSV ของ HR — ข้ามคนที่มีบัญชีอยู่แล้ว.

ใช้ HTTP API ของเว็บ ไม่ใช่ต่อฐานข้อมูลตรง: สิทธิ์ admin, การตรวจ max_uses และ
การผูกอีเมลเข้ากับรหัส จะได้เดินผ่านด่านเดียวกับที่หน้าเว็บใช้ ไม่ต้องมีสองที่ที่
ต้องถูกต้องพร้อมกัน (และไม่ต้องรู้ DATABASE_URL ของ production ซึ่งเอาออกมาไม่ได้)

ค่าเริ่มต้นคือ **ซ้อมอย่างเดียว** — ต้องใส่ --apply ถึงจะออกรหัสจริง เพราะการออกรหัส
เขียนลงฐาน production และย้อนคืนไม่ได้ (ลบได้เฉพาะในฐาน) รันซ้ำจะได้รหัสใบใหม่
ทับใบเก่าเสมอ ใบเก่าที่ยังไม่ถูกใช้จะค้างอยู่จนหมดอายุ 14 วัน

รหัสเชิญไม่ถูกพิมพ์ออกหน้าจอ — ลงไฟล์ผลลัพธ์อย่างเดียว กัน shoulder surfing และ
กันรหัสติดไปกับ log/สกรีนช็อตที่ส่งต่อกัน

    python scripts/invite_from_csv.py employees.csv --base https://example.com
    python scripts/invite_from_csv.py employees.csv --base https://example.com --apply
"""
from __future__ import annotations

import argparse
import csv
import getpass
import http.cookiejar
import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

EMAIL_COLUMNS = ("work_email", "email")
SESSION_COOKIE = "mai_session"


def read_csv_emails(path: Path) -> list[dict]:
    """อ่านไฟล์ HR แล้วคืนแถวที่มีอีเมลใช้ได้ เรียงตามไฟล์ ไม่ซ้ำอีเมล.

    อีเมลถูก normalize เป็นตัวพิมพ์เล็ก เพราะฝั่งเซิร์ฟเวอร์ก็ lower() ก่อนเทียบ
    ถ้าไม่ทำตรงกัน จะได้รหัสเชิญซ้ำให้คนเดิมที่ HR พิมพ์ชื่อขึ้นต้นด้วยตัวใหญ่
    """
    rows: list[dict] = []
    seen: set[str] = set()
    with io.open(path, encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            email = ""
            for col in EMAIL_COLUMNS:
                email = str(raw.get(col) or "").strip().lower()
                if email:
                    break
            # แถวที่ไม่มีอีเมล (เช่นแม่บ้านที่ไม่มีอีเมลบริษัท) ไม่ใช่ความผิดพลาด
            # ข้ามเงียบ ๆ ไม่ได้ — ต้องรายงานท้ายงานว่าใครตกหล่นบ้าง
            if not email or "@" not in email:
                rows.append({"email": "", "name": str(raw.get("full_name") or "").strip(),
                             "nickname": str(raw.get("nickname") or "").strip(),
                             "dept": str(raw.get("department_code") or "").strip()})
                continue
            if email in seen:
                continue
            seen.add(email)
            rows.append({"email": email, "name": str(raw.get("full_name") or "").strip(),
                         "nickname": str(raw.get("nickname") or "").strip(),
                         "dept": str(raw.get("department_code") or "").strip()})
    return rows


def split_rows(rows: list[dict], existing: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """แยกเป็น (ต้องออกรหัส, มีบัญชีแล้ว, ไม่มีอีเมล) — ไม่แตะเครือข่าย ทดสอบได้ตรง ๆ."""
    todo, have, noemail = [], [], []
    for r in rows:
        if not r["email"]:
            noemail.append(r)
        elif r["email"] in existing:
            have.append(r)
        else:
            todo.append(r)
    return todo, have, noemail


class Client:
    """ตัวเรียก API ที่ถือคุกกี้ session ไว้ให้."""

    def __init__(self, base: str, timeout: float = 30.0):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(raw).get("error") or raw
            except ValueError:
                msg = raw
            raise SystemExit(f"{method} {path} ตอบ HTTP {e.code}: {msg}") from None
        except urllib.error.URLError as e:
            raise SystemExit(f"ต่อ {self.base} ไม่ได้: {e.reason}") from None

    def login(self, email: str, password: str) -> dict:
        user = self._call("POST", "/api/auth/login",
                          {"email": email, "password": password}).get("user") or {}
        if not any(c.name == SESSION_COOKIE for c in self.jar):
            raise SystemExit("ล็อกอินผ่านแต่ไม่ได้คุกกี้ session กลับมา — หยุดไว้ก่อน")
        return user

    def users(self) -> list[dict]:
        return self._call("GET", "/api/admin/users").get("users") or []

    def invite(self, email: str) -> str:
        code = self._call("POST", "/api/auth/invite", {"email": email}).get("code")
        if not code:
            raise SystemExit(f"เซิร์ฟเวอร์ไม่คืนรหัสเชิญสำหรับ {email}")
        return str(code)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="ออกรหัสเชิญจากไฟล์ CSV ของ HR")
    ap.add_argument("csv", type=Path, help="ไฟล์ CSV ที่มีคอลัมน์ work_email")
    ap.add_argument("--base", required=True, help="URL ของเว็บ เช่น https://example.com")
    ap.add_argument("--apply", action="store_true", help="ออกรหัสจริง (ไม่ใส่ = ซ้อมอย่างเดียว)")
    ap.add_argument("--out", type=Path, default=None, help="ไฟล์ผลลัพธ์ (ค่าเริ่มต้น: ข้าง CSV)")
    ap.add_argument("--admin", default="", help="อีเมลแอดมินที่ใช้ล็อกอิน (ไม่ใส่จะถาม)")
    args = ap.parse_args(argv)

    if not args.csv.is_file():
        print(f"ไม่พบไฟล์ {args.csv}")
        return 2

    rows = read_csv_emails(args.csv)
    admin_email = args.admin or input("อีเมลแอดมิน: ").strip()
    password = getpass.getpass("รหัสผ่าน (ไม่แสดงบนจอ): ")

    c = Client(args.base)
    me = c.login(admin_email, password)
    if not me.get("is_admin"):
        print("บัญชีนี้ไม่ใช่แอดมิน — ออกรหัสเชิญไม่ได้")
        return 1
    existing = {str(u.get("email") or "").strip().lower() for u in c.users()}
    todo, have, noemail = split_rows(rows, existing)

    print(f"ใน CSV {len([r for r in rows if r['email']])} อีเมล · "
          f"มีบัญชีแล้ว {len(have)} · ต้องออกรหัส {len(todo)} · ไม่มีอีเมล {len(noemail)}")
    for r in noemail:
        print(f"  ข้าม (ไม่มีอีเมลในไฟล์): {r['name'] or '(ไม่มีชื่อ)'}")

    if not args.apply:
        for r in todo:
            print(f"  จะออกรหัสให้: {r['email']}")
        print("ซ้อมอย่างเดียว ยังไม่ได้ออกรหัสจริง — ใส่ --apply เมื่อตรวจรายชื่อแล้ว")
        return 0
    if not todo:
        print("ทุกคนในไฟล์มีบัญชีแล้ว ไม่มีอะไรต้องทำ")
        return 0

    out = args.out or args.csv.with_name(args.csv.stem + "-invites.csv")
    done = 0
    with io.open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["email", "full_name", "nickname", "department", "invite_link"])
        for r in todo:
            code = c.invite(r["email"])
            w.writerow([r["email"], r["name"], r["nickname"], r["dept"],
                        f"{c.base}/?invite={code}"])
            done += 1
    # ไม่พิมพ์รหัส/ลิงก์ออกจอ — อยู่ในไฟล์อย่างเดียว
    print(f"ออกรหัสแล้ว {done} ใบ · ลิงก์อยู่ใน {out} · รหัสหมดอายุใน 14 วัน")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    raise SystemExit(main())
