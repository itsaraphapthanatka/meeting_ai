"""BUG-064 — บอทที่ไม่เคยถูกรับเข้าห้อง รายงานสาเหตุผิด.

เจ้าของแจ้งว่า "ส่ง bot เข้าห้อง error" (2026-09-19) log ของเครื่องประมวลผลบอกเรื่องทั้งหมด:

    [bot] ข้อความบนหน้า: You can't join this video call / ...
          No one can join a meeting unless invited or admitted by the host /
          Returning to home screen in 60 seconds.
    [bot] ตรวจพบว่าประชุมจบ/ออกจากห้องแล้ว
    ✅ ได้ไฟล์เสียง: ...
    ❌ งานล้มเหลว: ถอดเสียงไม่ได้ข้อความเลย — ไฟล์อาจไม่มีเสียงพูด หรือเงียบทั้งไฟล์

บอททำงานถูกทุกขั้น มันแค่ไม่เคยถูกกด Admit แล้ว Meet เตะออกใน 60 วินาที
แต่ข้อความที่ผู้ใช้เห็นพูดถึง "ไฟล์ไม่มีเสียงพูด" ซึ่งพาไปไล่หาปัญหาไมโครโฟน

`bot_job()` มีการ์ดดักกรณีนี้อยู่แล้ว (`mean_volume(wav) < SILENT_DB`) แต่**ไม่ทำงาน** —
ไฟล์ที่ได้ไม่เงียบพอจะต่ำกว่า -55 dB (หน้ารอของ Meet มีเสียง UI/เสียงแจ้งเตือนปนอยู่)
การ์ดที่อิงความดังจึงเป็นการอนุมาน ส่วนสถานะที่คอนเทนเนอร์รายงานเอง (`bot_status.txt`
-> `waiting` / `inroom` / `left`) เป็นสัญญาณตรง — ใช้ตัวนั้นแทน

**ข้อควรระวังที่ออกแบบไว้**: ไม่ตัดสินจากสถานะอย่างเดียว ต้องถอดเสียงไม่ได้ **ด้วย**
คอนเทนเนอร์รุ่นเก่าที่ไม่เขียน `bot_status.txt` จะได้ไม่ถูกทิ้งประชุมที่อัดมาดี ๆ
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import runner


class BotJobCase(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-bug064-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.spec = {"id": "20260919-041123-e0978b", "url": "https://meet.google.com/xoq-kpir-mek",
                     "title": "ประชุมที่บอทเข้าร่วม", "max_minutes": 180}

    def _run(self, *, statuses, transcribe, level=-30.0):
        """เรียก bot_job จริง โดยปลอมเฉพาะสิ่งที่ต้องมี Docker/ffmpeg."""
        def fake_join(url, wav, **kw):
            Path(wav).write_bytes(b"RIFF0000WAVEfake")
            tick = kw.get("on_tick")
            for i, st in enumerate(statuses, 1):
                if tick:
                    tick(i * 10.0, st)

        with mock.patch.object(runner.bot, "join_and_record", fake_join), \
             mock.patch.object(runner, "mean_volume", lambda p: level), \
             mock.patch.object(runner, "audio_duration", lambda p: 25.0), \
             mock.patch.object(runner, "transcribe_job", transcribe):
            return runner.bot_job(self.spec, lambda *a, **k: None, self.tmp)

    @staticmethod
    def _no_speech(*a, **k):
        raise RuntimeError(runner.NO_SPEECH_ERROR)

    # ---------- อาการที่รายงาน ----------

    def test_never_admitted_says_so_instead_of_blaming_the_audio(self):
        with self.assertRaises(RuntimeError) as e:
            self._run(statuses=["", "", ""], transcribe=self._no_speech)
        msg = str(e.exception)
        self.assertIn("ไม่เคยได้เข้าห้อง", msg)
        self.assertIn("Admit", msg)
        self.assertNotIn("ไม่มีเสียงพูด", msg, "ยังพาผู้ใช้ไปไล่หาปัญหาไมโครโฟนอยู่")

    def test_it_says_how_long_the_bot_waited(self):
        # ผู้ใช้ต้องรู้ว่ามีเวลากดรับแค่ไหน ไม่งั้นไม่รู้ว่าต้องรีบแค่ไหนรอบหน้า
        with self.assertRaises(RuntimeError) as e:
            self._run(statuses=["waiting"], transcribe=self._no_speech)
        self.assertIn("00:25", str(e.exception))

    def test_waiting_outside_the_room_counts_as_never_admitted(self):
        with self.assertRaises(RuntimeError) as e:
            self._run(statuses=["waiting", "waiting"], transcribe=self._no_speech)
        self.assertIn("ไม่เคยได้เข้าห้อง", str(e.exception))

    # ---------- ต้องไม่กลืนกรณีอื่น ----------

    def test_a_real_silent_meeting_keeps_the_original_message(self):
        """เข้าห้องได้จริงแต่ไม่มีใครพูด — คนละเรื่องกับไม่ได้ถูกรับเข้าห้อง."""
        with self.assertRaises(RuntimeError) as e:
            self._run(statuses=["waiting", "inroom", "inroom"], transcribe=self._no_speech)
        self.assertEqual(str(e.exception), runner.NO_SPEECH_ERROR)

    def test_other_failures_pass_through_untouched(self):
        def boom(*a, **k):
            raise RuntimeError("ffmpeg ไม่ตอบ")

        with self.assertRaises(RuntimeError) as e:
            self._run(statuses=[""], transcribe=boom)
        self.assertEqual(str(e.exception), "ffmpeg ไม่ตอบ")

    def test_a_good_recording_is_never_thrown_away(self):
        """คอนเทนเนอร์รุ่นเก่าไม่เขียนสถานะ แต่ถอดเสียงได้ — ต้องผ่านตามปกติ.

        นี่คือความล้มเหลวที่แย่ที่สุดของการแก้ครั้งนี้ ถ้าตัดสินจากสถานะอย่างเดียว
        ประชุมจริงที่อัดมาดี ๆ จะถูกทิ้งเพราะเราเดาผิด
        """
        out = self._run(statuses=["", ""], transcribe=lambda *a, **k: {"segments": [1]})
        self.assertEqual(out, {"segments": [1]})

    # ---------- การ์ดเดิมยังทำงาน ----------

    def test_a_truly_silent_file_still_stops_before_transcribing(self):
        def never(*a, **k):
            raise AssertionError("ไม่ควรถอดเสียงเลยเมื่อไฟล์เงียบสนิท")

        with self.assertRaises(RuntimeError) as e:
            self._run(statuses=["waiting"], transcribe=never, level=-90.0)
        self.assertIn("เงียบทั้งไฟล์", str(e.exception))


class TestTheMessageIsDefinedOnce(unittest.TestCase):

    def test_the_no_speech_text_lives_in_one_constant(self):
        src = (Path(runner.__file__)).read_text(encoding="utf-8")
        # ปรากฏได้แค่ที่ประกาศค่าคงที่ (คอมเมนต์อ้างถึงได้ แต่ห้าม raise ด้วยสตริงซ้ำ)
        self.assertEqual(src.count('raise RuntimeError("ถอดเสียงไม่ได้ข้อความเลย'), 0)
        self.assertIn("raise RuntimeError(NO_SPEECH_ERROR)", src)


if __name__ == "__main__":
    unittest.main()
