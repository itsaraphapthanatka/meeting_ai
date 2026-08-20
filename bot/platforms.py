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

async def join_teams(page, name, log, passcode=None) -> None:
    # หน้าแรกมักถามว่าจะเปิดในแอปหรือในเบราว์เซอร์ — ต้องเลือกเบราว์เซอร์
    if await click_first(page, [
        'button:has-text("Continue on this browser")',
        'a:has-text("Continue on this browser")',
        'button:has-text("Join on the web instead")',
        'a:has-text("Join on the web instead")',
        'button:has-text("ดำเนินการต่อในเบราว์เซอร์นี้")',
        'button:has-text("ใช้เว็บแอปแทน")',
    ], timeout=8000):
        log("เลือกเข้าร่วมผ่านเบราว์เซอร์")
    else:
        log("ไม่เจอปุ่มเลือกเบราว์เซอร์ — อาจอยู่หน้า pre-join แล้ว")

    await page.wait_for_timeout(3000)

    if await fill_first(page, [
        'input[data-tid="prejoin-display-name-input"]',
        'input[placeholder*="name" i]',
        'input[placeholder*="ชื่อ"]',
        'input[type="text"]',
    ], name, timeout=8000):
        log("ตั้งชื่อบอท: " + name)
    else:
        log("ไม่มีช่องกรอกชื่อ — ปกติถ้าบอทล็อกอิน Microsoft อยู่แล้ว")

    await click_first(page, ['[data-tid="toggle-mute"]',
                             '[aria-label*="Mute" i]'], timeout=2500)
    await click_first(page, ['[data-tid="toggle-video"]',
                             '[aria-label*="camera" i]'], timeout=2500)

    joined = await click_first(page, [
        'button[data-tid="prejoin-join-button"]',
        'button:has-text("Join now")',
        'button:has-text("เข้าร่วมเลย")',
    ], timeout=6000)
    log("กดปุ่มเข้าห้องแล้ว — รอ host กดรับถ้าห้องมี lobby" if joined
        else "หาปุ่มเข้าห้องไม่เจอ (UI อาจเปลี่ยน) — ดู bot_debug.png")


TEAMS_END = ("text=/meeting has ended|You.?re not in this meeting|Rejoin"
             "|call ended|การประชุมสิ้นสุด/i")


# ---------- Zoom ----------

async def join_zoom(page, name, log, passcode=None) -> None:
    # บางหน้ามีปุ่มยอมรับข้อตกลง/คุกกี้ขึ้นมาก่อน
    await click_first(page, [
        'button:has-text("I Agree")',
        'button:has-text("Agree")',
        'button:has-text("Accept Cookies")',
    ], timeout=3000)

    if await fill_first(page, [
        '#input-for-name',
        'input[placeholder*="Your Name" i]',
        'input[placeholder*="name" i]',
        'input[type="text"]',
    ], name, timeout=10000):
        log("ตั้งชื่อบอท: " + name)
    else:
        log("ไม่มีช่องกรอกชื่อ — หน้าอาจยังไม่ใช่ web client (ดู bot_debug.png)")

    if passcode:
        if await fill_first(page, ['#input-for-pwd',
                                   'input[type="password"]'], passcode, timeout=3000):
            log("ใส่รหัสเข้าห้องแล้ว")

    joined = await click_first(page, [
        '#joinBtn',
        'button:has-text("Join")',
        'button:has-text("เข้าร่วม")',
    ], timeout=6000)
    log("กดปุ่มเข้าห้องแล้ว — รอ host กดรับถ้าห้องเปิดห้องรอไว้" if joined
        else "หาปุ่มเข้าห้องไม่เจอ (UI อาจเปลี่ยน) — ดู bot_debug.png")


ZOOM_END = ("text=/This meeting has been ended|meeting has ended"
            "|host has ended|การประชุมสิ้นสุด/i")


ADAPTERS = {
    MEET: (join_meet, MEET_END),
    TEAMS: (join_teams, TEAMS_END),
    ZOOM: (join_zoom, ZOOM_END),
}

LABELS = {MEET: "Google Meet", TEAMS: "Microsoft Teams", ZOOM: "Zoom"}
