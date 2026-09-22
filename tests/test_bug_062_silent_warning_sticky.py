"""BUG-062 — คำเตือน "ยังไม่ได้ยินเสียงเลย" ค้างถาวร และเตือนไมค์ที่ตายกลางทางไม่ได้.

เจ้าของรายงานระหว่างอัดจริง (2026-09-19): พูดอยู่แต่หน้าเว็บขึ้น
"ยังไม่ได้ยินเสียงเลย — ตรวจว่าเลือกไมค์ถูกตัว..." ที่นาทีที่ 00:42

สองอาการที่ไล่เจอ:

1. **คำเตือนค้าง** — `$('#rec-warn').hidden` ถูกตั้งเป็น `false` ใน `setInterval` แต่ไม่มี
   ใครตั้งกลับเป็น `true` เลย คนกดอัดแล้วเงียบ 6 วินาทีก่อนเริ่มพูด (ปกติมาก) จะเห็นคำเตือน
   ค้างไปจนจบการอัด ทั้งที่เสียงเข้ามาตั้งนานแล้ว **และไฟล์เสียงไม่ได้เสียอะไรเลย** เพราะ
   ตัวอัดไฟล์อัดจากสตรีมต้นทางตรง ๆ ไม่ได้ผ่าน AudioContext ที่มิเตอร์ใช้ (คอมเมนต์ใน
   `startRecording()` เขียนข้อนี้ไว้เองอยู่แล้ว) — คำเตือนที่ผิดจึงชวนให้คนหยุดอัดทิ้งของดี

2. **เตือนไมค์ที่ตายกลางทางไม่ได้** — เงื่อนไขเดิมเทียบกับ `rec.peak` ซึ่งเป็นค่าสูงสุด
   *ตลอดกาล* พอได้ยินเสียงครั้งเดียว เงื่อนไขก็เป็นเท็จตลอดไป ไมค์ที่หลุดตอนนาทีที่ 5 จึง
   อัดความเงียบไปจนจบโดยไม่มีอะไรบอก (`track.onended` จับได้เฉพาะแทร็กที่ตายสนิท ไม่ใช่
   แทร็กที่ยังอยู่แต่ส่งความเงียบมา เช่นถูกปิดที่ระดับ OS หรือ Bluetooth สลับโปรไฟล์)

เทสต์นี้ **ตัดฟังก์ชันจริงออกมาจาก app.js แล้วรันด้วย node** ไม่ใช่ assert ข้อความในไฟล์ —
เงื่อนไขเวลาแบบนี้เขียน regex ครอบให้ถูกแทบไม่ได้ และของที่ shipped คือโค้ดที่รันจริง
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "meeting_ai" / "web" / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _cut(pattern: str) -> str:
    m = re.search(pattern, APP_JS, re.S)
    if not m:
        raise AssertionError(f"ตัดโค้ดจาก app.js ไม่ได้: {pattern}")
    return m.group(0)


@unittest.skipUnless(NODE, "ต้องมี node เพื่อรันฟังก์ชันจริงจาก app.js")
class TestSilentWarning(unittest.TestCase):
    """รัน silentWarning() ตัวจริง ด้วย rec ปลอมที่เราคุมเวลาได้."""

    @classmethod
    def setUpClass(cls) -> None:
        # ค่าคงที่ + ข้อความ + ตัวฟังก์ชัน ตัดมาจากไฟล์จริงทั้งหมด
        cls.src = "\n".join([
            _cut(r"const HEARD_LEVEL = [\d.]+;"),
            _cut(r"const SILENT_START_SEC = \d+;"),
            _cut(r"const SILENT_LOST_SEC = \d+;"),
            _cut(r"const SILENT_WARN = \{.*?\n\};"),
            _cut(r"const LOST_WARN = .*?;\n"),
            # BACKLOG #85 ทำให้ silentWarning() พึ่งสองตัวนี้ — ไม่ตัดมาด้วย node จะ
            # โยน ReferenceError แล้วเทสต์ทั้งคลาสล้มโดยไม่เกี่ยวกับสิ่งที่มันตั้งใจวัด
            _cut(r"function deadTracks\(\) \{.*?\n\}"),
            _cut(r"function stalledTracks\(now\) \{.*?\n\}"),
            _cut(r"function deadTrackText\(names\) \{.*?\n\}"),
            _cut(r"function silentWarning\(now = Date\.now\(\)\) \{.*?\n\}"),
        ])
        cls.tmp = Path(tempfile.mkdtemp(prefix="mai-bug062-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)

    def _warn(self, *, recording=True, muted=False, mode="room",
              started=0, heard_at=0, now=0) -> str:
        script = self.tmp / "run.js"
        script.write_text(
            f"const rec = {json.dumps({'recording': recording, 'muted': muted, 'mode': mode, 'started': started, 'heardAt': heard_at})};\n"
            + self.src
            + f"\nprocess.stdout.write(silentWarning({now}));\n",
            encoding="utf-8", newline="\n")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    S = 1000  # มิลลิวินาทีต่อวินาที

    # ---------- อาการที่ 1: ต้องหายเอง ----------

    def test_quiet_at_the_start_warns(self):
        # ของเดิมก็ทำได้ ต้องไม่หายไปตอนแก้
        self.assertIn("ยังไม่ได้ยินเสียงเลย",
                      self._warn(started=0, heard_at=0, now=10 * self.S))

    def test_the_warning_clears_once_audio_arrives(self):
        """หัวใจของบั๊ก: เงียบตอนต้น 10 วินาที แล้วเริ่มพูด -> ต้องไม่เตือนแล้ว."""
        self.assertEqual("", self._warn(started=0, heard_at=10 * self.S,
                                        now=12 * self.S))

    def test_it_stays_clear_for_a_normal_pause(self):
        # เงียบ 30 วินาทีกลางประชุมเป็นเรื่องปกติ ห้ามเตือน
        self.assertEqual("", self._warn(started=0, heard_at=10 * self.S,
                                        now=40 * self.S))

    def test_it_does_not_warn_before_the_grace_period(self):
        self.assertEqual("", self._warn(started=0, heard_at=0, now=3 * self.S))

    # ---------- อาการที่ 2: ไมค์ตายกลางทาง ----------

    def test_a_mic_that_dies_mid_meeting_is_reported(self):
        """ของเดิมเตือนกรณีนี้ไม่ได้เลย เพราะ rec.peak ค้างสูงอยู่ตลอดกาล."""
        got = self._warn(started=0, heard_at=60 * self.S, now=(60 + 90) * self.S)
        self.assertIn("เงียบมานานแล้ว", got)

    def test_that_warning_also_clears_when_sound_comes_back(self):
        self.assertEqual("", self._warn(started=0, heard_at=150 * self.S,
                                        now=151 * self.S))

    # ---------- ไม่รบกวนตอนผู้ใช้ตั้งใจปิดไมค์ ----------

    def test_muting_never_warns(self):
        for heard_at in (0, 5 * self.S):
            with self.subTest(heard_at=heard_at):
                self.assertEqual("", self._warn(muted=True, heard_at=heard_at,
                                                now=300 * self.S))

    def test_nothing_warns_when_not_recording(self):
        self.assertEqual("", self._warn(recording=False, now=300 * self.S))

    # ---------- ข้อความตรงกับโหมด ----------

    def test_each_mode_gets_its_own_first_message(self):
        for mode, needle in (("room", "เลือกไมค์ถูกตัว"),
                             ("device", "อุปกรณ์วนเสียงกลับ"),
                             ("tab", "แชร์เสียงแท็บ")):
            with self.subTest(mode=mode):
                self.assertIn(needle, self._warn(mode=mode, now=10 * self.S))

    def test_an_unknown_mode_falls_back_instead_of_showing_undefined(self):
        self.assertIn("ยังไม่ได้ยินเสียงเลย", self._warn(mode="เหลือเชื่อ",
                                                          now=10 * self.S))


class TestTheCallerActuallyUsesIt(unittest.TestCase):
    """ฟังก์ชันที่ถูกต้องแต่ไม่มีใครเรียก ก็ยังเป็นบั๊กเดิม."""

    def test_the_timer_hides_and_shows_from_the_same_decision(self):
        """ต้องตั้ง hidden จากผลของ silentWarning() ทั้งสองทาง ไม่ใช่ตั้ง false ทางเดียว.

        BACKLOG #85 แยกเป็นสองบรรทัดเพราะต้องเอา **ข้อความ** ไปแสดงด้วย — ของเดิม
        ตั้งแค่ hidden ทำให้กล่องเปล่าโผล่มา ข้อความที่คิดไว้สามแบบไม่เคยถูกแสดงเลย
        """
        self.assertIn("const warn = silentWarning();", APP_JS)
        self.assertIn("$('#rec-warn').hidden = !warn;", APP_JS)
        self.assertIn("$('#rec-warn').textContent = warn;", APP_JS)

    def test_nothing_sets_the_warning_visible_by_hand_any_more(self):
        # บรรทัดแบบ `warn.hidden = false` คือที่มาของอาการค้าง
        self.assertNotRegex(APP_JS, r"rec-warn'\)\.hidden = false")
        self.assertNotRegex(APP_JS, r"warn\.hidden = false")

    def test_the_meter_records_when_it_heard_something(self):
        self.assertIn("if (level >= HEARD_LEVEL) rec.heardAt = Date.now();", APP_JS)

    def test_unmuting_restarts_the_silence_clock(self):
        # ไม่งั้นเปิดไมค์กลับมาแล้วโดนเตือนทันที ทั้งที่ยังไม่ทันพูด
        self.assertIn("if (!rec.muted) rec.heardAt = Date.now();", APP_JS)


if __name__ == "__main__":
    unittest.main()
