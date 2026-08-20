#!/usr/bin/env python3
"""บอทเข้าห้องประชุมออนไลน์เป็นผู้ร่วมประชุม แล้วอัดเสียงในห้อง (รันภายใน Docker).

รองรับ Google Meet, Microsoft Teams, Zoom — ดูขั้นตอนกดเข้าห้องของแต่ละเจ้าที่ platforms.py

ทำงานเป็นขั้น:
  1. เริ่มอัดเสียงจากลำโพงเสมือน (pulse: meet.monitor) ทันที ก่อนเปิดหน้าเว็บ
     (ถ้าเปิดหน้าพังแล้วยังไม่ได้เริ่มอัด จะไม่ได้ไฟล์เลย แม้แต่ความเงียบ ซึ่งไล่สาเหตุไม่ได้)
  2. เปิด Chromium ไปที่ลิงก์ห้องประชุม
  3. ใส่ชื่อบอท + ปิดไมค์/กล้อง + กดเข้าห้อง ตามแพลตฟอร์มที่ตรวจได้จาก URL
     (ขั้นนี้แข่งกับสัญญาณหยุด — ถูกสั่งหยุดกลางทางจะยกเลิกแล้วปิดอัดทันที)
  4. อยู่ในห้องจนกว่าจะ: จบประชุม / ถูกสั่งหยุด (docker stop) / ครบเวลาสูงสุด
  5. หยุดอัด (finalize wav) เขียนไฟล์ /out/<ชื่อ>.wav (16kHz mono พร้อมป้อน whisper)

ปรับพฤติกรรมผ่าน env: MEET_URL, BOT_NAME, OUT_WAV, MAX_MINUTES, PASSCODE
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time

import platforms
from playwright.async_api import async_playwright

MEET_URL = os.environ.get("MEET_URL", "")
BOT_NAME = os.environ.get("BOT_NAME", "AI Notetaker")
OUT_WAV = os.environ.get("OUT_WAV", "/out/recording.wav")
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "180"))
PASSCODE = os.environ.get("PASSCODE", "")
DEBUG_PNG = "/out/bot_debug.png"
SHOT_AFTER_JOIN = "/out/bot_after_join.png"
SHOT_IN_ROOM = "/out/bot_inroom.png"
SHOT_EVERY_SEC = 60      # ถ่ายทับไฟล์เดิมระหว่างอยู่ในห้อง


def log(msg: str) -> None:
    print(f"[bot] {msg}", flush=True)


async def shoot(page, path: str) -> None:
    """ถ่ายหน้าจอบอทแบบไม่ให้ล้มงานถ้าถ่ายไม่ได้."""
    try:
        await page.screenshot(path=path)
    except Exception as e:
        log("ถ่ายภาพหน้าจอไม่ได้: " + str(e))


async def where(page) -> str:
    """บอกว่าหน้าจอบอทอยู่ที่ไหน — URL กับ title พอชี้ได้ว่าติดหน้าไหน."""
    try:
        return f"{page.url} | {await page.title()}"
    except Exception:
        return "(อ่านสถานะหน้าไม่ได้)"


async def _visible(page, selector, timeout=800) -> bool:
    try:
        await page.locator(selector).first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False


def _start_recording() -> subprocess.Popen:
    """อัดเสียงจากลำโพงเสมือนเป็น wav 16kHz mono (ฟอร์แมตที่ whisper ชอบ)."""
    return subprocess.Popen(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "pulse", "-i", "meet.monitor",
            "-ar", "16000", "-ac", "1",
            OUT_WAV,
        ],
        stdin=subprocess.PIPE,
    )


def _stop_recording(ff: subprocess.Popen) -> None:
    """สั่ง ffmpeg ปิดไฟล์ให้เรียบร้อย (ส่ง 'q' ก่อน ค่อย terminate ถ้าไม่ยอมจบ)."""
    if ff.poll() is not None:
        return
    try:
        ff.communicate(input=b"q", timeout=10)
        return
    except Exception:
        pass
    for stop in (ff.terminate, ff.kill):
        try:
            stop()
            ff.wait(timeout=8)
            return
        except Exception:
            continue


async def _monitor(page, stop: asyncio.Event, end_markers: str) -> None:
    """อยู่ในห้องจนจบประชุม / ถูกสั่งหยุด / ครบเวลา. ตอบสัญญาณหยุดภายใน ~1 วิ.

    ถ่ายภาพหน้าจอทับไฟล์เดิมเป็นระยะด้วย — เวลาบอทค้าง (เช่น ติดอยู่ห้องรอ)
    ภาพล่าสุดคือหลักฐานเดียวที่บอกได้ว่าหน้าจอฝั่งบอทเป็นอย่างไร
    """
    start = time.time()
    last_end_check = 0.0
    last_shot = 0.0
    while not stop.is_set():
        now = time.time()
        if now - start > MAX_MINUTES * 60:
            log("ครบเวลาสูงสุดที่ตั้งไว้ — หยุด")
            return
        if now - last_end_check >= 5:
            last_end_check = now
            if await _visible(page, end_markers, timeout=800):
                log("ตรวจพบว่าประชุมจบ/ออกจากห้องแล้ว")
                return
        if now - last_shot >= SHOT_EVERY_SEC:
            last_shot = now
            await shoot(page, SHOT_IN_ROOM)
            log(f"อยู่ในห้องมา {int(now - start)}s — {await where(page)}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
            return  # ถูกสั่งหยุด (SIGTERM/SIGINT)
        except asyncio.TimeoutError:
            pass


async def _race_stop(coro, stop: asyncio.Event) -> None:
    """รัน coro แต่ถ้า stop ถูกตั้งก่อน ให้ยกเลิก coro ทันที (ปิดอัดได้ไว)."""
    task = asyncio.ensure_future(coro)
    stopper = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({task, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        stopper.cancel()
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def run() -> int:
    if not MEET_URL:
        log("ไม่ได้ตั้ง MEET_URL")
        return 2

    platform = platforms.detect(MEET_URL)
    if platform is None:
        log(f"ไม่รู้จักแพลตฟอร์มของลิงก์นี้: {MEET_URL}")
        return 2
    join, end_markers = platforms.ADAPTERS[platform]
    url = platforms.prepare_url(MEET_URL, platform)
    log(f"แพลตฟอร์ม: {platforms.LABELS[platform]}")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir="/prof",   # profile ถาวร (mount จาก host) ที่ล็อกอินไว้แล้ว
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-fake-ui-for-media-stream",      # ตอบ allow ให้ prompt ไมค์/กล้องอัตโนมัติ
                # ไม่ใช้ --use-fake-device-for-media-stream: มันสร้างลำโพงปลอมด้วย
                # แล้วโปรแกรมประชุมจะเล่นเสียงลงตัวนั้น ไม่ลง sink ที่ ffmpeg อัด (ได้ไฟล์เงียบ)
                # ไมค์เสมือนทำที่ PulseAudio แทน (ดู entrypoint.sh)
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled",  # ลดร่องรอย automation
                "--disable-gpu",
                "--window-size=1280,720",
            ],
            permissions=["microphone", "camera"],
            viewport={"width": 1280, "height": 720},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        ff = None
        try:
            # อัดก่อนเปิดหน้าเลย — เปิดหน้าพังแล้วยังไม่ได้เริ่มอัด จะไม่ได้ไฟล์เสียงเลย
            # แม้แต่ความเงียบ ทำให้ไล่สาเหตุไม่ได้ และถ้าห้องรับบอทช้า เสียงช่วงต้นก็ไม่หาย
            log("เริ่มอัดเสียง")
            ff = _start_recording()

            log(f"เปิดลิงก์: {url}")
            try:
                # domcontentloaded พอสำหรับกดปุ่ม — รอ event load ของหน้าประชุม
                # ช้าเกินจนหมดเวลาได้บ่อยทั้งที่หน้าใช้งานได้แล้ว
                await page.goto(url, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                log(f"เปิดหน้าห้องไม่เรียบร้อย ({e}) — ลองกดเข้าห้องต่อ")

            await asyncio.sleep(3)  # ให้หน้า pre-join นิ่งก่อน

            # เตรียม+เข้าห้อง (ยกเลิกได้ทันทีถ้าถูกสั่งหยุด)
            if not stop.is_set():
                await _race_stop(join(page, BOT_NAME, log, PASSCODE or None), stop)

            log(f"หลังกดเข้าห้อง: {await where(page)}")
            await shoot(page, SHOT_AFTER_JOIN)

            # อยู่ในห้องจนจบ
            if not stop.is_set():
                await _monitor(page, stop, end_markers)

            log("กำลังปิดการอัดเสียง")
            _stop_recording(ff)
            ff = None

            # ออกจากห้องอย่างสุภาพ (best-effort)
            await platforms.click_first(page, [
                '[aria-label*="Leave call"]', '[aria-label*="ออกจากสาย"]',
                '[data-tid="hangup-button"]', 'button:has-text("Leave")',
            ], timeout=3000)
            log(f"เสร็จ — บันทึกไฟล์: {OUT_WAV}")
            return 0

        except Exception as e:
            log(f"ผิดพลาด: {e}")
            try:
                await page.screenshot(path=DEBUG_PNG)
                log(f"บันทึกภาพหน้าจอเพื่อ debug: {DEBUG_PNG}")
            except Exception:
                pass
            return 1
        finally:
            if ff:
                _stop_recording(ff)
            try:
                await context.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
