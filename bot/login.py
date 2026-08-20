#!/usr/bin/env python3
"""โหมดล็อกอินครั้งเดียว — เปิด Chromium (profile ถาวรที่ /prof) ให้ผู้ใช้ล็อกอิน Google ผ่าน VNC.

หลังล็อกอินเสร็จ profile จะถูกเก็บไว้ที่โฟลเดอร์ที่ mount มา (bot/profile ของ host)
แล้วโหมดปกติ (join_meet.py) จะใช้ session นี้เข้าห้องประชุมได้โดยไม่โดนบล็อก anonymous.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

from playwright.async_api import async_playwright

# หน้าล็อกอินของแต่ละเจ้า — profile เดียวเก็บได้หลาย session
LOGIN_URL = os.environ.get("LOGIN_URL", "https://accounts.google.com/")


def log(msg: str) -> None:
    print(f"[login] {msg}", flush=True)


async def run() -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir="/prof",
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled",  # ลดร่องรอย automation กัน Google บล็อกล็อกอิน
                "--window-position=0,0",
                "--window-size=1280,720",
            ],
            viewport={"width": 1280, "height": 720},
        )
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(LOGIN_URL, wait_until="load", timeout=60000)
        except Exception as e:
            log(f"เปิดหน้าล็อกอินไม่สำเร็จ: {e}")

        log("=" * 56)
        log("เปิดเบราว์เซอร์ที่  http://localhost:6080/vnc.html  แล้วล็อกอิน")
        log(f"หน้าที่เปิดไว้: {LOGIN_URL}")
        log("(หรือใช้ VNC client ต่อ localhost:5900 ก็ได้ ไม่ต้องใส่รหัส)")
        log("(แนะนำใช้บัญชีเฉพาะสำหรับบอท เช่น notetaker@โดเมนคุณ)")
        log("ล็อกอินเสร็จแล้ว กลับมากด Enter ที่ terminal เพื่อบันทึก")
        log("=" * 56)

        await stop.wait()

        log("กำลังบันทึก profile...")
        await context.close()
        log("เสร็จ — profile ถูกบันทึกแล้ว ใช้ ./mai bot ได้เลย")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
