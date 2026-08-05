"""อัดเสียงประชุมสด (system audio + ไมค์) ด้วย ffmpeg.

macOS ใช้ avfoundation, Windows ใช้ dshow (DirectShow) — API ของโมดูลนี้เหมือนกันทั้งสองแพลตฟอร์ม
ค่า MIC_DEVICE / SYSTEM_DEVICE ใน .env ใส่ได้ทั้ง index (ตามที่ `mai devices` แสดง) หรือชื่ออุปกรณ์
"""

from __future__ import annotations

import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .config import config

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# CLSID_AudioInputDeviceCategory — ffmpeg บางรุ่นรายงาน type เป็น (none) แม้เป็นอุปกรณ์เสียง
# จึงยืนยันจาก category GUID ใน alternative name อีกทาง
_DSHOW_AUDIO_CATEGORY = "33D9A762-90C8-11D0-BD43-00A0C911CE86"

_LOOPBACK_INSTALL_HINT = (
    "Windows ไม่มีอุปกรณ์ดักเสียงลำโพงมาให้ในตัว ต้องติดตั้งเพิ่มอย่างใดอย่างหนึ่ง:\n"
    "  • VB-CABLE (ฟรี, เทียบเท่า BlackHole บน Mac) — https://vb-audio.com/Cable/\n"
    "    ตั้ง output ของ Windows เป็น 'CABLE Input' แล้วเปิด Listen ไปลำโพงจริงเพื่อให้ยังได้ยินเสียง\n"
    "  • VoiceMeeter — https://vb-audio.com/Voicemeeter/ (ยืดหยุ่นกว่า ตั้งค่าซับซ้อนกว่า)\n"
    "  • เปิด 'Stereo Mix' ใน Sound Control Panel ถ้าการ์ดเสียงรองรับ\n"
    "แล้วรัน `mai devices` ใหม่ เอาค่าไปใส่ SYSTEM_DEVICE ใน .env\n"
    "ถ้าจะอัดแค่ไมค์ตัวเองก่อน ใช้: mai record <ไฟล์> --no-system"
)


@dataclass
class Device:
    """อุปกรณ์เสียงหนึ่งตัว. `spec` = ค่าที่ส่งให้ ffmpeg ใช้อ้างถึงอุปกรณ์นี้."""

    index: int
    name: str
    spec: str
    kind: str = "audio"
    alt: str = field(default="")

    @property
    def is_audio(self) -> bool:
        return self.kind == "audio" or _DSHOW_AUDIO_CATEGORY in self.alt.upper()


def _check_ffmpeg() -> None:
    if shutil.which(config.ffmpeg_bin) is None and not Path(config.ffmpeg_bin).exists():
        hint = (
            "ติดตั้งด้วย: winget install Gyan.FFmpeg (หรือดาวน์โหลดจาก ffmpeg.org)"
            if IS_WINDOWS
            else "ติดตั้งด้วย: brew install ffmpeg"
        )
        raise RuntimeError(f"ไม่พบ ffmpeg — {hint}")


def _ffmpeg_list_output(fmt: str, dummy_input: str) -> str:
    """รัน ffmpeg -list_devices (จบด้วย error เสมอตามปกติ) แล้วคืน stderr."""
    proc = subprocess.run(
        [config.ffmpeg_bin, "-hide_banner", "-f", fmt, "-list_devices", "true", "-i", dummy_input],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stderr or ""


def _list_avfoundation() -> list[Device]:
    devices: list[Device] = []
    grab = False
    for line in _ffmpeg_list_output("avfoundation", "").splitlines():
        if "AVFoundation audio devices" in line:
            grab = True
            continue
        if "AVFoundation video devices" in line:
            grab = False
        if not grab:
            continue
        m = re.search(r"\[(\d+)\]\s+(.*)", line)
        if m:
            idx = int(m.group(1))
            devices.append(Device(index=idx, name=m.group(2).strip(), spec=f":{idx}"))
    return devices


def _list_dshow() -> list[Device]:
    """แจง DirectShow devices. ffmpeg พิมพ์เป็นคู่: บรรทัดชื่อ แล้วบรรทัด Alternative name."""
    parsed: list[Device] = []
    for line in _ffmpeg_list_output("dshow", "dummy").splitlines():
        body = re.sub(r"^\[dshow @ [0-9a-fx]+\]\s*", "", line).strip()
        m = re.match(r'^"(.+)"\s+\((\w+)\)$', body)
        if m:
            parsed.append(Device(index=-1, name=m.group(1), spec="", kind=m.group(2)))
            continue
        m = re.match(r'^Alternative name\s+"(.+)"$', body)
        if m and parsed:
            parsed[-1].alt = m.group(1)

    audio = [d for d in parsed if d.is_audio]
    for i, d in enumerate(audio):
        d.index = i
        # ใช้ alternative name เมื่อมี — ทนต่อชื่อซ้ำและอักขระพิเศษได้ดีกว่าชื่อที่คนอ่าน
        d.spec = f"audio={d.alt or d.name}"
    return audio


def list_audio_devices() -> list[Device]:
    _check_ffmpeg()
    if IS_WINDOWS:
        return _list_dshow()
    return _list_avfoundation()


def list_devices() -> str:
    """คืนข้อความรายชื่ออุปกรณ์เสียงพร้อม index สำหรับใส่ใน .env."""
    devices = list_audio_devices()
    backend = "dshow" if IS_WINDOWS else "avfoundation"
    lines = [f"อุปกรณ์เสียง ({backend}):"]
    lines += [f"  [{d.index}] {d.name}" for d in devices] or ["  (ไม่พบ)"]
    lines.append("")
    lines.append("เอา index ไปใส่ MIC_DEVICE (ไมค์คุณ) และ SYSTEM_DEVICE (เสียงประชุม) ใน .env")
    if IS_WINDOWS:
        lines.append("")
        lines.append(_LOOPBACK_INSTALL_HINT)
    return "\n".join(lines)


def _resolve(value: str, role: str) -> str:
    """แปลงค่าจาก .env (index หรือชื่อ) เป็น spec ที่ ffmpeg เข้าใจ."""
    value = (value or "").strip()
    if not value:
        raise RuntimeError(f"ยังไม่ได้ตั้งค่า {role} ใน .env\n\n{list_devices()}")

    if not IS_WINDOWS and value.isdigit():
        # avfoundation อ้าง index ตรงๆ ได้ ไม่ต้องแจงอุปกรณ์ก่อน
        return f":{value}"

    devices = list_audio_devices()
    if value.isdigit():
        idx = int(value)
        match = next((d for d in devices if d.index == idx), None)
        if match is None:
            raise RuntimeError(
                f"{role}={value} ไม่ตรงกับอุปกรณ์ใดในเครื่อง\n\n{list_devices()}"
            )
        return match.spec

    if value.startswith(("audio=", "@device")):
        return value if value.startswith("audio=") else f"audio={value}"

    match = next((d for d in devices if d.name.lower() == value.lower()), None)
    if match is None:
        raise RuntimeError(f"{role}=\"{value}\" ไม่ตรงกับอุปกรณ์ใดในเครื่อง\n\n{list_devices()}")
    return match.spec


def _input_args(spec: str) -> list[str]:
    return ["-f", "dshow" if IS_WINDOWS else "avfoundation", "-i", spec]


def _stop(proc: subprocess.Popen) -> None:
    """หยุด ffmpeg อย่างสะอาดเพื่อให้ปิด header ของไฟล์ให้ถูก."""
    try:
        proc.communicate(input=b"q", timeout=10)
        return
    except Exception:
        pass
    try:
        # Windows ไม่รับ SIGINT ผ่าน send_signal — terminate คือทางที่มีให้
        proc.terminate() if IS_WINDOWS else proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


def record(output: str | Path, mic: bool = True, system: bool = True) -> Path:
    """อัดเสียงจนกด Ctrl+C แล้ว mix เป็นไฟล์เดียว.

    system = เสียงที่ออกลำโพง (Mac: BlackHole / Windows: VB-CABLE หรือ Stereo Mix)
    mic    = ไมโครโฟนของคุณ
    """
    _check_ffmpeg()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if not (mic or system):
        raise ValueError("ต้องเลือกอัดอย่างน้อยหนึ่งแหล่ง (mic หรือ system)")

    specs: list[str] = []
    if system:
        # ข้อความ error ของ _resolve พา list_devices() มาด้วย ซึ่งมีวิธีติดตั้ง loopback อยู่แล้ว
        specs.append(_resolve(config.system_device, "SYSTEM_DEVICE"))
    if mic:
        specs.append(_resolve(config.mic_device, "MIC_DEVICE"))

    cmd = [config.ffmpeg_bin, "-y"]
    for spec in specs:
        cmd += _input_args(spec)
    if len(specs) == 2:
        # ผสมสองแหล่งเป็นแทร็กเดียว (ffmpeg แทรก resampler ให้เองถ้า sample rate ต่างกัน)
        cmd += ["-filter_complex", "amix=inputs=2:duration=longest:normalize=0"]
    cmd += ["-ar", "16000", "-ac", "1", str(output)]

    print(f"🎙️  กำลังอัดเสียง → {output}")
    print("   กด Ctrl+C เพื่อหยุดอัด\n")

    kwargs: dict = {"stdin": subprocess.PIPE}
    if IS_WINDOWS:
        # กัน Ctrl+C ของ console ไม่ให้ฆ่า ffmpeg ก่อนที่เราจะส่ง 'q' ให้ปิดไฟล์เอง
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(cmd, **kwargs)
    try:
        proc.wait()
    except KeyboardInterrupt:
        _stop(proc)
        print(f"\n✅ หยุดอัดแล้ว: {output}")
    if not output.exists():
        print("⚠️  ไม่พบไฟล์ผลลัพธ์ — ตรวจ index อุปกรณ์ด้วย: mai devices", file=sys.stderr)
    return output
