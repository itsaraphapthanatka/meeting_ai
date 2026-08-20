#!/usr/bin/env python3
"""บอทเข้าห้อง Google Meet เป็นผู้ร่วมประชุม แล้วอัดเสียงในห้อง (รันภายใน Docker).

ทำงานเป็นขั้น:
  1. เปิด Chromium ไปที่ลิงก์ห้องประชุม
  2. เริ่มอัดเสียงจากลำโพงเสมือน (pulse: meet.monitor) ด้วย ffmpeg ทันที
  3. ใส่ชื่อบอท (โหมด guest) + ปิดไมค์/กล้อง + กดเข้าห้อง
     (ขั้นนี้แข่งกับสัญญาณหยุด — ถ้าถูกสั่งหยุดระหว่างทางจะยกเลิกแล้วปิดอัดทันที)
  4. อยู่ในห้องจนกว่าจะ: จบประชุม / ถูกสั่งหยุด (docker stop) / ครบเวลาสูงสุด
  5. หยุดอัด (finalize wav) เขียนไฟล์ /out/<ชื่อ>.wav (16kHz mono พร้อมป้อน whisper)

ปรับพฤติกรรมผ่าน env: MEET_URL, BOT_NAME, OUT_WAV, MAX_MINUTES
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time

from playwright.async_api import async_playwright

MEET_URL = os.environ.get("MEET_URL", "")
BOT_NAME = os.environ.get("BOT_NAME", "AI Notetaker")
OUT_WAV = os.environ.get("OUT_WAV", "/out/recording.wav")
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "180"))
DEBUG_PNG = "/out/bot_debug.png"


def log(msg: str) -> None:
    print(f"[bot] {msg}", flush=True)


async def _click_first(page, selectors, timeout=4000) -> bool:
    """คลิก element แรกที่เจอจากรายการ selector (ทนต่อ UI ที่เปลี่ยนบ่อย)."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=timeout)
            await el.click()
            return True
        except Exception:
            continue
    return False


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


async def _prepare_and_join(page) -> None:
    """เตรียมหน้า pre-join แล้วกดเข้าห้อง. timeout สั้นเพื่อไม่บล็อกนานถ้า UI ไม่ตรง."""
    await asyncio.sleep(3)  # ให้หน้า pre-join โหลดนิ่ง

    await _click_first(page, [
        'button:has-text("Got it")',
        'button:has-text("Dismiss")',
        'button:has-text("No thanks")',
    ], timeout=2000)

    # ใส่ชื่อบอท (โหมด guest — ห้องที่อนุญาต guest จะมีช่องนี้)
    try:
        name_box = page.locator('input[type="text"]').first
        await name_box.wait_for(state="visible", timeout=6000)
        await name_box.fill(BOT_NAME)
        log(f"ตั้งชื่อบอท: {BOT_NAME}")
    except Exception:
        log("ไม่มีช่องกรอกชื่อ — ปกติถ้าบอทล็อกอิน Google อยู่แล้ว")

    # ปิดไมค์/กล้องก่อนเข้า (best-effort ไม่เจอก็ข้าม)
    await _click_first(page, [
        '[aria-label*="Turn off microphone"]', '[aria-label*="ปิดไมโครโฟน"]',
    ], timeout=2000)
    await _click_first(page, [
        '[aria-label*="Turn off camera"]', '[aria-label*="ปิดกล้อง"]',
    ], timeout=2000)

    joined = await _click_first(page, [
        'button:has-text("Join now")',
        'button:has-text("Ask to join")',
        'button:has-text("เข้าร่วมเลย")',
        'button:has-text("ขอเข้าร่วม")',
    ], timeout=4000)
    log("กดปุ่มเข้าห้องแล้ว — รอ host กดรับถ้าเป็นห้องที่ต้องอนุมัติ"
        if joined else "หาปุ่มเข้าห้องไม่เจอ (UI อาจเปลี่ยน) — ดู bot_debug.png")


async def _monitor(page, stop: asyncio.Event) -> None:
    """อยู่ในห้องจนจบประชุม / ถูกสั่งหยุด / ครบเวลา. ตอบสัญญาณหยุดภายใน ~1 วิ."""
    start = time.time()
    last_end_check = 0.0
    end_markers = 'text=/You.?ve been removed|left the meeting|Return to home|call ended|การประชุมสิ้นสุด/i'
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

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir="/prof",   # profile ถาวร (mount จาก host) ที่ล็อกอิน Google ไว้แล้ว
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-fake-ui-for-media-stream",      # ตอบ allow ให้ prompt ไมค์/กล้องอัตโนมัติ
                "--use-fake-device-for-media-stream",  # ป้อนไมค์/กล้องปลอม Meet จะได้ไม่ค้างรอ
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
            # อัดก่อนเปิดหน้าเลย — ถ้าเปิดหน้าพังแล้วยังไม่ได้เริ่มอัด
            # จะไม่ได้ไฟล์เสียงเลยแม้แต่ความเงียบ ซึ่งทำให้ไล่สาเหตุไม่ได้
            # และถ้าห้องรับบอทช้า เสียงช่วงต้นก็ไม่หาย
            log("เริ่มอัดเสียง")
            ff = _start_recording()

            log(f"เปิดลิงก์: {MEET_URL}")
            try:
                # domcontentloaded พอสำหรับกดปุ่ม — รอ event load ของ Meet
                # ช้าเกินจนหมดเวลาได้บ่อยทั้งที่หน้าใช้งานได้แล้ว
                await page.goto(MEET_URL, wait_until="domcontentloaded", timeout=90000)
            except Exception as e:
                # เปิดหน้าไม่จบก็ยังลองต่อ — บางทีหน้าโหลดพอใช้งานแล้วแต่ event ไม่มา
                log(f"เปิดหน้าห้องไม่เรียบร้อย ({e}) — ลองกดเข้าห้องต่อ")

            # เตรียม+เข้าห้อง (ยกเลิกได้ทันทีถ้าถูกสั่งหยุด)
            if not stop.is_set():
                await _race_stop(_prepare_and_join(page), stop)

            # อยู่ในห้องจนจบ
            if not stop.is_set():
                await _monitor(page, stop)

            log("กำลังปิดการอัดเสียง")
            _stop_recording(ff)
            ff = None

            # ออกจากห้องอย่างสุภาพ (best-effort)
            await _click_first(page, [
                '[aria-label*="Leave call"]', '[aria-label*="ออกจากสาย"]',
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
