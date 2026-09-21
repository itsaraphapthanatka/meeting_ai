"""BACKLOG #85 — รู้ว่าแทร็กไหนเงียบ ตั้งแต่ตอนอัด ไม่ใช่หลังถอดเสียงเสร็จ.

**เกิดจริงและหลุดถึงผู้ใช้ 2026-09-20**: เจ้าของอัดโหมด "ไมค์ + เสียงในเครื่อง" โดยเลือก
`BlackHole 2ch (Virtual)` เป็นอุปกรณ์เสียงในเครื่อง แล้วได้ข้อความ

    ไม่สำเร็จ: ถอดเสียงไม่ได้ข้อความเลย — ไฟล์อาจไม่มีเสียงพูด หรือเงียบทั้งไฟล์

ไล่ประวัติงานในฐาน production เมื่อ 2026-09-20 16:54 UTC: **โหมดสองแทร็กล้ม 5 ครั้งรวด
ใน 2 วัน** ส่วนโหมดไมค์เดียวสำเร็จปกติ · BlackHole เป็นอุปกรณ์เสมือนที่ไม่มีเสียงเข้าเลย
จนกว่าจะตั้งเส้นทางเสียงออกของระบบไปหามัน ซึ่งผู้ใช้ไม่มีทางรู้จากหน้าจอ

**แก้คำที่เคยเขียนไว้ที่นี่ว่า "ไม่เคยสำเร็จเลยสักครั้ง"**: จริง ณ ตอนวัด แต่เก่าไปแล้วตอน
ship — มีงานสองแทร็กสำเร็จหนึ่งครั้งเมื่อ 17:07 UTC วันเดียวกัน (13 นาทีหลังครั้งที่ล้ม
ครั้งสุดท้าย) ได้การประชุมยาว 84.8 วินาที ไม่ทราบว่าอะไรเปลี่ยนระหว่างนั้น
บทเรียน: ถ้าเวลาห่างจากตอนวัดหลายชั่วโมง ต้องวัดซ้ำก่อนเขียนลงตั๋ว

**สองช่องโหว่ที่เจอตอนไล่โค้ด:**

1. มิเตอร์วัดจาก `dest` ซึ่งเป็น **เสียงผสม** ของทุกแทร็ก ไมค์ดังอยู่ตัวเดียวก็ทำให้มิเตอร์
   ขยับปกติ แทร็กที่ตายสนิทจึงมองไม่เห็น และรู้ตัวหลังอัปโหลด + ถอดเสียงเสร็จเท่านั้น
2. `#rec-warn` เป็น `<p>` เปล่าในหน้า HTML และโค้ดตั้งแค่ `hidden` — **ข้อความเตือนสามแบบ
   ที่ `SILENT_WARN` เขียนไว้อย่างดีจึงไม่เคยถูกแสดงเลยสักครั้ง** ผู้ใช้เห็นกล่องว่าง ๆ

วัดกับเบราว์เซอร์จริงที่ 375x812 โดย stub `getUserMedia` ให้แทร็กไมค์เป็นคลื่นเสียงจริง
(oscillator 220Hz) และแทร็ก BlackHole เป็นความเงียบ แล้วกดอัดด้วยฟอร์มจริง:

    หลัง 6 วินาที  analyser รายแทร็ก: mic 0.3634 · system 0.0000
    แบนเนอร์บนสุด "ไม่ได้ยินเสียงจาก เสียงในเครื่อง “BlackHole 2ch (Virtual)” เลย — …"
    hit-test แบนเนอร์: อยู่ในจอ elementFromPoint กลางกล่องได้ตัวมันเอง

**บั๊กเลย์เอาต์ที่เจอเพราะ hit-test ไม่ใช่เพราะดูรูป**: `#rec-warn` อยู่ท้ายหน้าและถูก
แถบลอย (`.floatbar`) ทับ ถ้าผู้ใช้ยังไม่เลื่อนลงก็ไม่เห็นเลย (`onScreen: false`) จึงต้องดัน
ขึ้นแบนเนอร์บนสุดหนึ่งครั้งด้วย
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
STATIC = ROOT / "meeting_ai" / "web" / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _cut(pattern: str) -> str:
    m = re.search(pattern, APP_JS, re.S)
    if not m:
        raise AssertionError(f"ตัดโค้ดจาก app.js ไม่ได้: {pattern}")
    return m.group(0)


@unittest.skipUnless(NODE, "ต้องมี node เพื่อรันฟังก์ชันจริงจาก app.js")
class TestTheWarningItself(unittest.TestCase):
    """รันฟังก์ชันจริงที่ตัดออกมาจาก app.js — ไม่ใช่ assert ข้อความในไฟล์."""

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
            _cut(r"function deadTrackText\(names\) \{.*?\n\}"),
            _cut(r"function silentWarning\(now = Date\.now\(\)\) \{.*?\n\}"),
        ])
        cls.tmp = Path(tempfile.mkdtemp(prefix="mai-bl85-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)

    S = 1000

    def _warn(self, tracks, *, mode="device", started=0, heard_at=1, now=20_000) -> str:
        rec = {"recording": True, "muted": False, "mode": mode,
               "started": started, "heardAt": heard_at, "tracks": tracks}
        script = self.tmp / "run.js"
        script.write_text(
            f"const rec = {json.dumps(rec, ensure_ascii=False)};\n" + self.src
            + f"\nprocess.stdout.write(silentWarning({now}));\n",
            encoding="utf-8", newline="\n")
        out = subprocess.run([NODE, str(script)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    LIVE = {"label": "MacBook Pro Microphone", "heardAt": 5_000}
    DEAD = {"label": "BlackHole 2ch (Virtual)", "heardAt": 0}

    # ---------- กรณีของจริงที่ทำให้ต้องมีตั๋วนี้ ----------

    def test_the_real_blackhole_case_is_named(self):
        out = self._warn({"system": self.DEAD, "mic": self.LIVE})
        self.assertIn("เสียงในเครื่อง", out)
        self.assertIn("BlackHole 2ch (Virtual)", out)

    def test_it_says_what_to_do_about_it(self):
        # ชื่ออุปกรณ์อย่างเดียวไม่พอ คนที่ไม่รู้จักอุปกรณ์วนเสียงกลับจะไม่รู้ว่าต้องทำอะไร
        self.assertIn("เสียงออกของระบบ", self._warn({"system": self.DEAD, "mic": self.LIVE}))

    def test_a_dead_mic_is_named_too(self):
        out = self._warn({"system": self.LIVE, "mic": self.DEAD})
        self.assertIn("ไมค์ของคุณ", out)

    # ---------- กรณีที่ต้องเงียบ ----------

    def test_nothing_when_both_tracks_are_alive(self):
        self.assertEqual("", self._warn({"system": self.LIVE, "mic": self.LIVE}))

    def test_the_all_silent_case_is_left_to_the_old_warning(self):
        """ทุกแทร็กเงียบ = เตือนแบบเดิมซึ่งบอกวิธีแก้ตามโหมด — อย่าไปทับ."""
        out = self._warn({"system": self.DEAD, "mic": self.DEAD}, heard_at=0, now=20_000)
        self.assertIn("ยังไม่ได้ยินเสียงเลย", out)
        self.assertNotIn("อีกแทร็กได้ยินปกติ", out)

    def test_no_track_alive_means_no_other_track_to_point_at(self):
        """เสียงผสมเคยได้ยิน แต่ไม่มีแทร็กไหนเคยดังถึงเกณฑ์เลย.

        เกิดได้จริงตอนเสียงเบามาก ๆ (มิเตอร์รวมจับได้ แต่รายแทร็กไม่ถึงเกณฑ์) ถ้าไม่กันไว้
        จะได้ข้อความที่ขัดแย้งในตัวเอง: "…เลย — อีกแทร็กได้ยินปกติ" ทั้งที่ไม่มีอีกแทร็ก
        """
        out = self._warn({"system": self.DEAD, "mic": {"label": "x", "heardAt": 0}},
                         heard_at=1_000, now=20 * self.S)
        self.assertEqual("", out)

    def test_a_single_track_recording_is_not_nagged(self):
        # โหมดไมค์เดียวมีแทร็กเดียว ถ้ามันเงียบก็เข้ากรณี "เงียบทั้งหมด" ไปแล้ว
        self.assertEqual("", self._warn({"mixed": self.LIVE}, mode="room"))

    def test_it_waits_before_crying_wolf(self):
        """คนกดอัดแล้วรอสองสามวินาทีก่อนเปิดเสียงเป็นเรื่องปกติ."""
        self.assertEqual("", self._warn({"system": self.DEAD, "mic": self.LIVE},
                                        started=0, now=3 * self.S))

    def test_a_long_silence_still_wins(self):
        # เคยได้ยินแล้วเงียบยาว (BUG-062) สำคัญกว่า และมีข้อความของตัวเอง
        out = self._warn({"system": self.DEAD, "mic": self.LIVE},
                         heard_at=1_000, now=200 * self.S)
        self.assertIn("เงียบมานานแล้ว", out)

    def test_the_device_name_is_quoted_not_double_bracketed(self):
        """ชื่ออุปกรณ์มีวงเล็บของตัวเองอยู่แล้ว ครอบซ้ำได้ "(BlackHole 2ch (Virtual))"."""
        out = self._warn({"system": self.DEAD, "mic": self.LIVE})
        self.assertNotIn("(BlackHole 2ch (Virtual))", out)
        self.assertIn("“BlackHole 2ch (Virtual)”", out)

    def test_a_track_without_a_device_name_still_reads(self):
        out = self._warn({"system": {"label": "", "heardAt": 0}, "mic": self.LIVE})
        self.assertIn("เสียงในเครื่อง", out)
        self.assertNotIn("“”", out)


class TestEachTrackIsMeasuredSeparately(unittest.TestCase):
    """มิเตอร์รวมวัดจาก dest — บอกไม่ได้ว่าใครเงียบ จึงต้องมี analyser ต่อแทร็ก."""

    def test_every_opened_input_is_watched(self):
        self.assertIn("watchTrack('system', ss, ctx, pickedLabel('#d-sys'))", APP_JS)
        self.assertIn("watchTrack(micName, ms, ctx, pickedLabel('#d-mic'))", APP_JS)

    def test_the_watcher_has_its_own_analyser(self):
        body = APP_JS[APP_JS.index("function watchTrack("):]
        body = body[:body.index("\n}")]
        self.assertIn("ctx.createAnalyser()", body)
        self.assertIn("createMediaStreamSource(stream)", body)

    def test_the_meter_loop_updates_every_track(self):
        body = APP_JS[APP_JS.index("function startMeterLoop("):]
        body = body[:body.index("\nfunction ")]
        self.assertIn("for (const t of Object.values(rec.tracks))", body)
        self.assertIn("if (lv >= HEARD_LEVEL) t.heardAt = Date.now();", body)

    def test_the_label_comes_from_what_the_user_picked(self):
        self.assertIn("function pickedLabel(sel)", APP_JS)


class TestTheUserActuallySeesIt(unittest.TestCase):

    def test_the_warning_box_gets_its_text(self):
        """เดิมตั้งแค่ hidden — กล่องเปล่าโผล่มาโดยไม่มีข้อความสักตัว."""
        self.assertIn("$('#rec-warn').textContent = warn;", APP_JS)

    def test_it_is_also_pushed_to_the_top_banner_once(self):
        """`#rec-warn` อยู่ท้ายหน้าและถูกแถบลอยทับ — hit-test แล้ว onScreen: false."""
        self.assertIn("if (warn && !rec.warned && deadTracks().length)", APP_JS)
        self.assertIn("rec.warned = true;", APP_JS)

    def test_the_banner_does_not_repeat_every_tick(self):
        # setInterval ยิงทุก 500ms ถ้าไม่กันไว้จะทับข้อความอื่นตลอดการอัด
        self.assertIn("warned: false,", APP_JS)
        self.assertIn("rec.warned = false;", APP_JS)

    def test_the_flag_is_cleared_between_recordings(self):
        body = APP_JS[APP_JS.index("function cleanupRecording()"):]
        body = body[:body.index("\nfunction ")]
        self.assertIn("rec.warned = false;", body)
        self.assertIn("rec.tracks = {};", body)


class TestItIsSaidAgainAtTheEnd(unittest.TestCase):
    """คนที่ไม่ได้มองจอระหว่างอัด ต้องรู้ตอนกดหยุด ไม่ใช่ตอน worker ตอบกลับ."""

    def test_the_dead_tracks_are_read_before_cleanup_wipes_them(self):
        body = APP_JS[APP_JS.index("function stopRecording()"):]
        body = body[:body.index("async function finishRecording")]
        self.assertIn("const dead = deadTracks();", body)
        self.assertLess(body.index("deadTracks()"), body.index("finishRecording("))

    def test_it_is_handed_to_the_finisher(self):
        self.assertIn("finishRecording(tracks, seconds, silent, deadWhat)", APP_JS)

    def test_the_banner_names_them(self):
        body = APP_JS[APP_JS.index("async function finishRecording"):]
        body = body[:body.index("\nfunction cleanupRecording")]
        self.assertIn("deadWhat", body)
        self.assertIn("ไม่ได้ยินเสียงจาก ${deadWhat}", body)

    def test_the_older_all_quiet_warning_still_wins(self):
        # เบามากทั้งไฟล์เป็นอาการที่กว้างกว่า บอกอันนั้นก่อน
        body = APP_JS[APP_JS.index("async function finishRecording"):]
        body = body[:body.index("\nfunction cleanupRecording")]
        self.assertLess(body.index("ระดับเสียงตลอดการอัดเบามาก"), body.index("deadWhat\n"))


if __name__ == "__main__":
    unittest.main()
