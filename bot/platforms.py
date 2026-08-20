#!/usr/bin/env python3
"""ขั้นตอน "กดเข้าห้อง" ของแต่ละแพลตฟอร์ม — ส่วนเดียวที่ต่างกันจริง.

การอัดเสียง เฝ้าห้อง และการหยุดอย่างสุภาพ ใช้โค้ดชุดเดียวกันหมด (join_meeting.py)
ที่ต่างกันคือหน้า pre-join: ชื่อปุ่ม ช่องกรอกชื่อ และวิธีเข้าถึง web client

หมายเหตุความเปราะ: ทั้งสามเจ้าเปลี่ยน UI เองได้ทุกเมื่อ selector จึงเขียนเป็น
"ลองหลายตัวเรียงกัน" แทนที่จะยึดตัวเดียว และทุกขั้นเป็น best-effort
หาไม่เจอก็ไปต่อ แล้วให้ bot_debug.png กับ log เป็นตัวบอกว่าติดที่ไหน
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

MEET = "meet"
TEAMS = "teams"
ZOOM = "zoom"

ZOOM_JOIN_RE = re.compile(r"/(?:j|wc/join)/([0-9]{9,12})")


def detect(url: str) -> str | None:
    """ดูจากโฮสต์ว่าเป็นแพลตฟอร์มไหน."""
    host = (urlparse(url).hostname or "").lower()
    if host == "meet.google.com":
        return MEET
    if host in ("teams.microsoft.com", "teams.live.com"):
        return TEAMS
    if host == "zoom.us" or host.endswith(".zoom.us"):
        return ZOOM
    return None


def prepare_url(url: str, platform: str) -> str:
    """ปรับ URL ให้พาไปที่ web client ตรงๆ ถ้าทำได้.

    Zoom: ลิงก์ /j/<id> จะโชว์หน้า "Launch Meeting" ที่พยายามเปิดโปรแกรมเดสก์ท็อป
    แล้วซ่อนลิงก์ "Join from your browser" ไว้ใต้เงื่อนไข — เปลี่ยนเป็น /wc/join/<id>
    ข้ามหน้านั้นไปที่ web client เลย ตัดจุดที่พลาดได้หนึ่งจุด
    """
    if platform != ZOOM:
        return url
    parts = urlparse(url)
    m = ZOOM_JOIN_RE.search(parts.path)
    if not m:
        return url
    return urlunparse(parts._replace(path="/wc/join/" + m.group(1)))


# ---------- ตัวช่วยที่ทนต่อ UI ที่เปลี่ยนบ่อย ----------

async def click_first(page, selectors, timeout=4000) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=timeout)
            await el.click()
            return True
        except Exception:
            continue
    return False


async def describe_inputs(page) -> str:
    """ลิสต์ input ทั้งหมดบนหน้า — ใช้ตอน selector ไม่ตรงแล้วต้องรู้ว่า UI จริงเป็นอย่างไร.

    ไม่ส่งค่าที่ผู้ใช้พิมพ์ออกมา (อาจเป็นรหัส) เอาแค่โครงสร้างพอชี้ selector ได้
    """
    try:
        rows = await page.evaluate(
            "Array.from(document.querySelectorAll('input')).map(function (e) {"
            "  var r = e.getBoundingClientRect();"
            "  return [e.type, e.id, e.name, e.placeholder,"
            "          e.getAttribute('aria-label'), r.width > 0 && r.height > 0].join('|');"
            "})")
    except Exception as e:
        return f"(อ่าน input ไม่ได้: {e})"
    return " ;; ".join(rows) if rows else "(ไม่มี input บนหน้าเลย)"


async def wait_any(page, selectors, timeout=30000) -> bool:
    """รอให้ element ตัวใดตัวหนึ่งโผล่ — ใช้รอหน้าเว็บที่ยัง redirect ไม่จบ."""
    joined = ", ".join(selectors)
    try:
        await page.locator(joined).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


async def fill_first(page, selectors, value, timeout=5000) -> bool:
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=timeout)
            await el.fill(value)
            return True
        except Exception:
            continue
    return False


# ---------- Google Meet ----------

async def join_meet(page, name, log, passcode=None) -> None:
    await click_first(page, [
        'button:has-text("Got it")',
        'button:has-text("Dismiss")',
        'button:has-text("No thanks")',
    ], timeout=2000)

    if await fill_first(page, ['input[type="text"]'], name, timeout=6000):
        log("ตั้งชื่อบอท: " + name)
    else:
        log("ไม่มีช่องกรอกชื่อ — ปกติถ้าบอทล็อกอิน Google อยู่แล้ว")

    await click_first(page, ['[aria-label*="Turn off microphone"]',
                             '[aria-label*="ปิดไมโครโฟน"]'], timeout=2000)
    await click_first(page, ['[aria-label*="Turn off camera"]',
                             '[aria-label*="ปิดกล้อง"]'], timeout=2000)

    joined = await click_first(page, [
        'button:has-text("Join now")',
        'button:has-text("Ask to join")',
        'button:has-text("เข้าร่วมเลย")',
        'button:has-text("ขอเข้าร่วม")',
    ], timeout=5000)
    log("กดปุ่มเข้าห้องแล้ว — รอ host กดรับถ้าเป็นห้องที่ต้องอนุมัติ" if joined
        else "หาปุ่มเข้าห้องไม่เจอ (UI อาจเปลี่ยน) — ดู bot_debug.png")


MEET_END = ("text=/You.?ve been removed|left the meeting|Return to home"
            "|call ended|การประชุมสิ้นสุด/i")


# ---------- Microsoft Teams ----------

# คอนเทนเนอร์ไม่มีกล้อง/ไมค์จริง Teams จึงเด้งกล่อง "Are you sure you don't want
# audio or video?" ซึ่งบัง Join now ไว้ ปุ่มในกล่องหมายถึง "ไม่ส่ง" ออกไป ไม่ใช่ "ไม่รับ"
# — บอทยังได้ยินเสียงห้องอยู่ ต้องไล่กล่องนี้ก่อนถึงจะกด Join ได้
NO_AV_BUTTON = [
    'button:has-text("Continue without audio or video")',
    '[role="button"]:has-text("Continue without audio or video")',
    'text="Continue without audio or video"',
    'button:has-text("ดำเนินการต่อโดยไม่ใช้เสียงหรือวิดีโอ")',
]

TEAMS_JOIN_BUTTON = [
    'button[data-tid="prejoin-join-button"]',
    'button:has-text("Join now")',
    'button:has-text("เข้าร่วมเลย")',
]


async def dismiss_no_av(page, log) -> bool:
    if await click_first(page, NO_AV_BUTTON, timeout=2500):
        log("ปิดกล่องถามเรื่องเสียง/ภาพแล้ว")
        return True
    return False


async def join_teams(page, name, log, passcode=None) -> None:
    # หน้าแรกมักถามว่าจะเปิดในแอปหรือในเบราว์เซอร์ — ต้องเลือกเบราว์เซอร์
    if await click_first(page, [
        'button:has-text("Continue on this browser")',
        'a:has-text("Continue on this browser")',
        'button:has-text("Join on the web instead")',
        'a:has-text("Join on the web instead")',
        'button:has-text("ดำเนินการต่อในเบราว์เซอร์นี้")',
        'button:has-text("ใช้เว็บแอปแทน")',
    ], timeout=2500):
        log("เลือกเข้าร่วมผ่านเบราว์เซอร์")
    else:
        log("ไม่เจอปุ่มเลือกเบราว์เซอร์ — อาจอยู่หน้า pre-join แล้ว")

    await page.wait_for_timeout(3000)

    if await fill_first(page, [
        'input[data-tid="prejoin-display-name-input"]',
        'input[placeholder*="name" i]',
        'input[placeholder*="ชื่อ"]',
        'input[type="text"]',
    ], name, timeout=4000):
        log("ตั้งชื่อบอท: " + name)
    else:
        log("ไม่มีช่องกรอกชื่อ — ปกติถ้าบอทล็อกอิน Microsoft อยู่แล้ว")

    # ไม่กด toggle ไมค์/กล้อง: อ่านสถานะไม่ได้ว่าตอนนี้เปิดหรือปิด กดมั่วแล้วอาจ
    # เปิดไมค์ขึ้นมาเอง (เจอจริงจากภาพหน้าจอ) และบอทไม่มีกล้องอยู่แล้ว

    # ไล่กล่องก่อน ไม่งั้นมันบัง Join now อยู่ แล้วเสียเวลารอ actionability เปล่าๆ
    await dismiss_no_av(page, log)
    joined = await click_first(page, TEAMS_JOIN_BUTTON, timeout=6000)
    # บางครั้งกล่องเด้งหลังกด Join — ไล่แล้วกดอีกรอบ
    if await dismiss_no_av(page, log):
        joined = await click_first(page, TEAMS_JOIN_BUTTON, timeout=6000) or joined
    log("กดปุ่มเข้าห้องแล้ว — รอ host กดรับถ้าห้องมี lobby" if joined
        else "หาปุ่มเข้าห้องไม่เจอ (UI อาจเปลี่ยน) — ดู bot_debug.png")


# ระวังคำกว้างเกิน: "Rejoin" กับ "You are not in this meeting" โผล่บนหน้า pre-join/launch
# ของ Teams ได้ด้วย ทำให้ตัวเฝ้าห้องเข้าใจว่าประชุมจบแล้วออกทันทีที่เพิ่งกดเข้า
TEAMS_END = ("text=/meeting has ended|host has ended the meeting"
             "|You have been removed|การประชุมสิ้นสุด/i")


# ---------- Zoom ----------

ZOOM_NAME_FIELDS = ['#input-for-name', 'input[placeholder*="Your Name" i]',
                    'input[placeholder*="name" i]']
ZOOM_PWD_FIELDS = ['#input-for-pwd', 'input[type="password"]',
                   'input[placeholder*="passcode" i]', 'input[id*="pwd" i]']
ZOOM_JOIN_BUTTON = ['#joinBtn', 'button:has-text("Join")', 'button:has-text("เข้าร่วม")']
ZOOM_BAD_PWD = 'text=/Incorrect Password|passcode is incorrect/i'


async def join_zoom(page, name, log, passcode=None) -> None:
    # บางหน้ามีปุ่มยอมรับข้อตกลง/คุกกี้ขึ้นมาก่อน
    await click_first(page, [
        'button:has-text("I Agree")',
        'button:has-text("Agree")',
        'button:has-text("Accept Cookies")',
    ], timeout=3000)

    # ฟอร์มโผล่หลัง Zoom redirect จาก us0Xweb.zoom.us ไป app.zoom.us — รอ 3 วิไม่พอ
    if not await wait_any(page, ZOOM_NAME_FIELDS, timeout=40000):
        log("รอฟอร์มเข้าห้องของ Zoom ไม่ขึ้น — input ที่มี: " + await describe_inputs(page))

    if await fill_first(page, ZOOM_NAME_FIELDS + ['input[type="text"]'], name, timeout=5000):
        log("ตั้งชื่อบอท: " + name)
    else:
        log("ไม่มีช่องกรอกชื่อ — input ที่มี: " + await describe_inputs(page))

    joined = await click_first(page, ZOOM_JOIN_BUTTON, timeout=8000)
    if joined:
        log("กดปุ่มเข้าห้องแล้ว")

    # ช่องรหัสยังไม่มีในหน้าตอนแรก — Zoom เพิ่งสร้างมันหลังกด Join แล้วปฏิเสธ
    # token ที่มาจาก ?pwd= ในลิงก์ (มัก "Incorrect Password" เมื่อ token หมดอายุ)
    # ต้องรอให้ช่องโผล่ก่อน แล้วค่อยเขียนรหัสตัวเลขทับ
    if await wait_any(page, ZOOM_PWD_FIELDS, timeout=10000):
        if not passcode:
            log("ห้องนี้ขอรหัสเข้าห้อง แต่ไม่ได้ส่งรหัสมา — "
                "ใส่ในช่อง “รหัสเข้าห้อง (Zoom)” แล้วส่งใหม่")
            return
        if await fill_first(page, ZOOM_PWD_FIELDS, passcode, timeout=4000):
            log("ใส่รหัสเข้าห้องแล้ว กดเข้าห้องอีกครั้ง")
            joined = await click_first(page, ZOOM_JOIN_BUTTON, timeout=8000) or joined
        else:
            log("เขียนรหัสลงช่องไม่ได้ — input ที่มี: " + await describe_inputs(page))

    if await page_has(page, ZOOM_BAD_PWD, timeout=2500):
        log("Zoom ตอบว่ารหัสไม่ผ่าน — ตรวจรหัสตัวเลขของห้องอีกครั้ง "
            "(อีกสาเหตุที่เจอ: ห้องนั้นปิดไปแล้ว)")
    elif joined:
        log("รอ host กดรับถ้าห้องเปิดห้องรอไว้")
    else:
        log("กดปุ่มเข้าห้องไม่ได้ (ปุ่มถูกปิดหรือ UI เปลี่ยน) — ดู bot_debug.png")


async def page_has(page, selector, timeout=1200) -> bool:
    try:
        await page.locator(selector).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


ZOOM_END = ("text=/This meeting has been ended|meeting has ended"
            "|host has ended|การประชุมสิ้นสุด/i")


# ปุ่มที่มีเฉพาะเมื่ออยู่ในห้องประชุมแล้วจริง (ปุ่มวางสาย/แถบควบคุม)
# ใช้แยก "เข้าห้องแล้ว" ออกจาก "ยังรออยู่หน้าห้อง" — เดิมนับเวลาหลังกดปุ่ม
# โดยไม่ตรวจอะไรเลย จึงรายงานว่าอยู่ในห้องทั้งที่ยังไม่มีใครกดรับ
MEET_IN = ('[aria-label*="Leave call"], [data-tooltip*="Leave call"],'
           ' [aria-label*="ออกจากสาย"]')
TEAMS_IN = '[data-tid="hangup-button"], [aria-label*="Leave" i]'
ZOOM_IN = '#foot-bar, .footer__leave-btn, [aria-label*="Leave" i]'


ADAPTERS = {
    MEET: (join_meet, MEET_END, MEET_IN),
    TEAMS: (join_teams, TEAMS_END, TEAMS_IN),
    ZOOM: (join_zoom, ZOOM_END, ZOOM_IN),
}

LABELS = {MEET: "Google Meet", TEAMS: "Microsoft Teams", ZOOM: "Zoom"}
