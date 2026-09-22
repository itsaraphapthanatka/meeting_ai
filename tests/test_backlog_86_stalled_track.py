"""BACKLOG #86 — แทร็กที่ **เคยดังแล้วหายไปกลางทาง** ขณะที่แทร็กอื่นยังดังอยู่.

ต่อจาก #85 ซึ่งจับได้เฉพาะแทร็กที่ **ไม่เคยดังเลย** ตั้งแต่เริ่มอัด · กรณีที่เหลือคือไมค์หลุด
ถูกปิดที่ระดับ OS หรือ Bluetooth สลับโปรไฟล์ **กลางการอัด** ซึ่ง:

* `track.onended` จับไม่ได้ เพราะแทร็กยังมีชีวิตอยู่ แค่ส่งความเงียบมา (บทเรียนจาก BUG-062)
* `LOST_WARN` จับไม่ได้ เพราะมันดูจาก `rec.heardAt` ซึ่งเป็นเสียง **ผสม** ของทุกแทร็ก —
  อีกแทร็กดังอยู่ตัวเดียวก็กลบให้เงียบสนิททั้งชุด

ของที่ต้องใช้มีอยู่แล้วตั้งแต่ #85: `rec.tracks[].heardAt` ถูกอัปเดตทุกเฟรมอยู่แล้ว
ตั๋วนี้แค่เอามาอ่าน

กติกาที่ตรึงไว้ — เตือนเฉพาะเมื่อ **ยังเหลือแทร็กที่ได้ยินอยู่จริง**:
ถ้าเงียบหมดทุกแทร็กคือกรณีของ `LOST_WARN` ซึ่งมีข้อความของตัวเองอยู่แล้ว การเตือนว่า
"แทร็กอื่นยังได้ยินอยู่" ตอนที่ไม่มีแทร็กอื่นเลย คือการโกหกผู้ใช้
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
class TestStalledTrack(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = "\n".join([
            _cut(r"const HEARD_LEVEL = [\d.]+;"),
            _cut(r"const SILENT_START_SEC = \d+;"),
            _cut(r"const SILENT_LOST_SEC = \d+;"),
            _cut(r"const SILENT_WARN = \{.*?\n\};"),
            _cut(r"const LOST_WARN = .*?;\n"),
            _cut(r"const TRACK_ROLE = \{.*?\};"),
            _cut(r"function deadTracks\(\) \{.*?\n\}"),
            _cut(r"function stalledTracks\(now\) \{.*?\n\}"),
            _cut(r"function deadTrackText\(names\) \{.*?\n\}"),
            _cut(r"function silentWarning\(now = Date\.now\(\)\) \{.*?\n\}"),
        ])
        cls.tmp = Path(tempfile.mkdtemp(prefix="mai-bl86-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)

    S = 1000
    NOW = 300_000            # นาทีที่ 5 ของการอัด

    def _warn(self, tracks, *, mode="device", heard_at=None, now=None) -> str:
        now = self.NOW if now is None else now
        rec = {"recording": True, "muted": False, "mode": mode, "started": 0,
               # เสียงผสมยังดังอยู่ (มีแทร็กหนึ่งยังทำงาน) — นี่คือจุดที่ LOST_WARN ตาบอด
               "heardAt": now - 1_000 if heard_at is None else heard_at,
               "tracks": tracks}
        script = self.tmp / "run.js"
        script.write_text(
            f"const rec = {json.dumps(rec, ensure_ascii=False)};\n" + self.src
            + f"\nprocess.stdout.write(silentWarning({now}));\n",
            encoding="utf-8", newline="\n")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def live(self, label="MacBook Pro Microphone"):
        return {"label": label, "heardAt": self.NOW - 2_000}      # ดังเมื่อ 2 วิที่แล้ว

    def stalled(self, label="Jabra Speak (Bluetooth)"):
        return {"label": label, "heardAt": self.NOW - 120_000}    # เงียบมา 2 นาที

    # ---------- กรณีที่ต้องเตือน ----------

    def test_a_mic_that_died_mid_meeting_is_named(self):
        out = self._warn({"system": self.live(), "mic": self.stalled()})
        self.assertIn("ไมค์ของคุณ", out)
        self.assertIn("Jabra Speak (Bluetooth)", out)

    def test_it_says_the_other_track_is_fine(self):
        # ไม่งั้นผู้ใช้จะหยุดอัดทิ้งทั้งที่อีกครึ่งยังดีอยู่
        self.assertIn("แทร็กอื่นยังได้ยินอยู่",
                      self._warn({"system": self.live(), "mic": self.stalled()}))

    def test_it_says_the_warning_clears_itself(self):
        # บทเรียน BUG-062: คำเตือนที่ไม่บอกว่าจะหายเอง ทำให้คนกดหยุดอัด
        self.assertIn("หายเองเมื่อได้ยินเสียงอีกครั้ง",
                      self._warn({"system": self.live(), "mic": self.stalled()}))

    def test_the_loopback_track_can_be_the_one_that_dies(self):
        out = self._warn({"system": self.stalled("BlackHole 2ch"), "mic": self.live()})
        self.assertIn("เสียงในเครื่อง", out)

    # ---------- กรณีที่ต้องเงียบ ----------

    def test_nothing_while_both_tracks_are_speaking(self):
        self.assertEqual("", self._warn({"system": self.live(), "mic": self.live()}))

    def test_a_short_pause_is_not_a_dead_device(self):
        """คนเงียบไป 20 วินาทีระหว่างประชุมเป็นเรื่องปกติ — ต้องไม่เตือน."""
        pause = {"label": "x", "heardAt": self.NOW - 20_000}
        self.assertEqual("", self._warn({"system": self.live(), "mic": pause}))

    def test_when_everything_is_stalled_the_old_warning_speaks(self):
        """เงียบหมดทุกแทร็ก = LOST_WARN ซึ่งมีข้อความของตัวเอง — ห้ามบอกว่า "แทร็กอื่นยังได้ยิน"."""
        out = self._warn({"system": self.stalled(), "mic": self.stalled()},
                         heard_at=self.NOW - 120_000)
        self.assertIn("เงียบมานานแล้ว", out)
        self.assertNotIn("แทร็กอื่นยังได้ยินอยู่", out)

    def test_it_never_claims_another_track_is_fine_when_none_is(self):
        """เสียงผสมยังดัง แต่ไม่มีแทร็กไหนดังถึงเกณฑ์เลย.

        เกิดได้เมื่อเสียงเบามาก (มิเตอร์รวมจับได้ แต่รายแทร็กไม่ถึง `HEARD_LEVEL`)
        ถ้าไม่กันไว้ จะได้ข้อความที่โกหกว่า "แทร็กอื่นยังได้ยินอยู่" ทั้งที่ไม่มีเลย
        """
        out = self._warn({"system": self.stalled(), "mic": self.stalled("Jabra 2")},
                         heard_at=self.NOW - 1_000)
        self.assertEqual("", out)

    def test_a_single_track_recording_is_left_to_the_old_warning(self):
        self.assertEqual("", self._warn({"mixed": self.stalled()}, mode="room"))

    def test_a_track_that_never_spoke_is_the_other_ticket(self):
        """ไม่เคยดังเลย = #85 ซึ่งมีข้อความและวิธีแก้คนละแบบ (เรื่องตั้งเส้นทางเสียง)."""
        never = {"label": "BlackHole 2ch", "heardAt": 0}
        out = self._warn({"system": never, "mic": self.live()})
        self.assertIn("ไม่ได้ยินเสียงจาก", out)
        self.assertNotIn("อุปกรณ์อาจหลุด", out)

    def test_a_muted_recording_says_nothing(self):
        rec_muted = self._warn({"system": self.live(), "mic": self.stalled()},
                               now=self.NOW)
        self.assertNotEqual("", rec_muted)          # กันเทสต์ข้างล่างเป็น vacuous
        script = self.tmp / "muted.js"
        rec = {"recording": True, "muted": True, "mode": "device", "started": 0,
               "heardAt": self.NOW - 1_000,
               "tracks": {"system": self.live(), "mic": self.stalled()}}
        script.write_text(
            f"const rec = {json.dumps(rec, ensure_ascii=False)};\n" + self.src
            + f"\nprocess.stdout.write(silentWarning({self.NOW}));\n",
            encoding="utf-8", newline="\n")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual("", out.stdout)


class TestItReusesWhatAlreadyExists(unittest.TestCase):
    """#85 อัปเดต `heardAt` รายแทร็กทุกเฟรมอยู่แล้ว ตั๋วนี้แค่เอามาอ่าน."""

    def test_no_new_state_was_added_to_rec(self):
        body = APP_JS[APP_JS.index("function stalledTracks("):]
        body = body[:body.index("\nfunction ")]
        self.assertIn("rec.tracks", body)
        self.assertNotIn("rec.stalled", APP_JS)

    def test_it_uses_the_same_threshold_as_the_mixed_warning(self):
        # สองที่นี้ควรเห็นตรงกันว่า "เงียบนานแค่ไหนถึงผิดปกติ"
        body = APP_JS[APP_JS.index("function stalledTracks("):]
        body = body[:body.index("\nfunction ")]
        self.assertIn("SILENT_LOST_SEC", body)

    def test_the_warning_comes_before_the_never_heard_one(self):
        """เคยดังแล้วหาย เร่งด่วนกว่าไม่เคยดังเลย — ของหลังผู้ใช้เห็นตั้งแต่ 6 วินาทีแรกแล้ว."""
        body = APP_JS[APP_JS.index("function silentWarning("):]
        body = body[:body.index("\n}")]
        self.assertLess(body.index("stalledTracks(now)"), body.index("deadTracks()"))


if __name__ == "__main__":
    unittest.main()
