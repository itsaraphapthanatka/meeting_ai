"""อัดเสียงประชุมสด (system audio + ไมค์) ด้วย ffmpeg + avfoundation บน macOS."""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from .config import config


def _check_ffmpeg() -> None:
    if shutil.which(config.ffmpeg_bin) is None:
        raise RuntimeError("ไม่พบ ffmpeg — ติดตั้งด้วย: brew install ffmpeg")


def list_devices() -> str:
    """คืนรายชื่ออุปกรณ์เสียง (index) จาก avfoundation."""
    _check_ffmpeg()
    proc = subprocess.run(
        [config.ffmpeg_bin, "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True,
    )
    out = proc.stderr
    audio_lines = []
    grab = False
    for line in out.splitlines():
        if "AVFoundation audio devices" in line:
            grab = True
            continue
        if "AVFoundation video devices" in line:
            grab = False
        if grab:
            m = re.search(r"\[(\d+)\]\s+(.*)", line)
            if m:
                audio_lines.append(f"  [{m.group(1)}] {m.group(2)}")
    return "อุปกรณ์เสียง (avfoundation):\n" + ("\n".join(audio_lines) or "  (ไม่พบ)")


def record(output: str | Path, mic: bool = True, system: bool = True) -> Path:
    """อัดเสียงจนกด Ctrl+C แล้ว mix เป็นไฟล์เดียว.

    system = เสียงที่ออกลำโพง (ต้องตั้ง BlackHole เป็น output/aggregate device)
    mic    = ไมโครโฟนของคุณ
    """
    _check_ffmpeg()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not (mic or system):
        raise ValueError("ต้องเลือกอัดอย่างน้อยหนึ่งแหล่ง (mic หรือ system)")

    cmd = [config.ffmpeg_bin, "-y"]
    inputs = 0
    if system:
        cmd += ["-f", "avfoundation", "-i", f":{config.system_device}"]
        inputs += 1
    if mic:
        cmd += ["-f", "avfoundation", "-i", f":{config.mic_device}"]
        inputs += 1

    if inputs == 2:
        # ผสมสองแหล่งเป็นแทร็กเดียว
        cmd += ["-filter_complex", "amix=inputs=2:duration=longest:normalize=0"]

    cmd += ["-ar", "16000", "-ac", "1", str(output)]

    print(f"🎙️  กำลังอัดเสียง → {output}")
    print("   กด Ctrl+C เพื่อหยุดอัด\n")

    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        proc.wait()
    except KeyboardInterrupt:
        # ส่ง 'q' ให้ ffmpeg ปิดไฟล์อย่างสะอาด
        try:
            proc.communicate(input=b"q", timeout=10)
        except Exception:
            proc.send_signal(signal.SIGINT)
            proc.wait()
        print(f"\n✅ หยุดอัดแล้ว: {output}")
    if not output.exists():
        print("⚠️  ไม่พบไฟล์ผลลัพธ์ — ตรวจ index อุปกรณ์ด้วย: mai devices", file=sys.stderr)
    return output
