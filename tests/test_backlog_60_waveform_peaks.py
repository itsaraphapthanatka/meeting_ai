"""BACKLOG #60 — waveform peaks: worker คำนวณ เซิร์ฟเวอร์ตรวจ หน้าเว็บวาด.

ทำไมต้องคำนวณฝั่ง worker: ตัวเล่นเสียงเวอร์ชันแรกถอดไฟล์ในเบราว์เซอร์ด้วย decodeAudioData
ซึ่งคลายไฟล์ทั้งไฟล์ลงแรมเป็น float32 (วินาที x sampleRate x 4 ไบต์ x ช่อง) ประชุมหนึ่ง
ชั่วโมงที่ 48 kHz สเตอริโอราว 1.4 GB จึงต้องตั้งเพดานไว้ 20 นาที แปลว่าประชุมจริงส่วนใหญ่
ไม่มี waveform — ฝั่ง worker มี ffmpeg กับไฟล์อยู่แล้ว ทำได้ในเวลาไม่กี่วินาทีและเก็บแค่ 64 ตัวเลข

ค่านี้มาจาก worker ที่เชื่อไม่ได้เต็มร้อย (ถือแค่ WORKER_TOKEN = "รับจ้างถอดเสียง") และถูก
ยัดลง style="height:N%" ในเบราว์เซอร์ตรง ๆ จึงต้องผ่าน sanitize เหมือนฟิลด์อื่นตาม BUG-048

พิสูจน์ว่าไม่ vacuous (ทำระหว่างพัฒนา ไม่ใช่โค้ดถาวร): แพตช์ sanitize.peaks() ให้เป็น
pass-through แล้วรันไฟล์นี้ซ้ำ — เทสต์ hostile ทุกตัวต้องล้ม
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _harness import CloudCase, new_mid
from meeting_ai.config import config as app_config
from meeting_ai.web import sanitize

WORKER_TOKEN = "wk-test-token-060"


class TestPeaksSanitize(unittest.TestCase):
    """ด่านตรวจค่าที่ worker ส่งมา — ค่าที่ผ่านต้องเอาไปใส่ CSS ได้อย่างปลอดภัยเสมอ."""

    def test_valid_list_passes_through(self):
        self.assertEqual(sanitize.peaks([0, 50, 100]), [0, 50, 100])

    def test_out_of_range_is_clamped_not_rejected(self):
        # ค่าหลุดกรอบมาจาก worker เวอร์ชันเก่า/ต่างเจ้าได้ ไม่ใช่การโจมตี — หนีบไว้ในกรอบพอ
        self.assertEqual(sanitize.peaks([-5, 300]), [0, 100])

    def test_floats_become_ints(self):
        self.assertEqual(sanitize.peaks([12.7, 90.2]), [12, 90])

    def test_non_finite_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                self.assertIsNone(sanitize.peaks([10, bad]))

    def test_strings_and_bools_rejected(self):
        # "50" ที่หลุดเข้า style="height:50%" ยังพอทำงาน แต่ "50%;position:fixed" ไม่ใช่ —
        # ไม่รับชนิดอื่นเลยตั้งแต่ต้นทางง่ายกว่าไล่ escape ปลายทาง
        self.assertIsNone(sanitize.peaks(["50"]))
        self.assertIsNone(sanitize.peaks([True]))
        self.assertIsNone(sanitize.peaks([None]))

    def test_non_list_and_empty_rejected(self):
        for bad in (None, "", 5, {"a": 1}, []):
            with self.subTest(bad=bad):
                self.assertIsNone(sanitize.peaks(bad))

    def test_too_long_is_truncated(self):
        out = sanitize.peaks([7] * 5000)
        self.assertEqual(len(out), sanitize.MAX_PEAKS)
        self.assertEqual(set(out), {7})


class TestPeaksThroughApplyResult(CloudCase):
    """เส้นทางจริง: worker POST ผลงาน -> jobs.apply_result() -> store.create(peaks=...)."""

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(app_config, "worker_token", WORKER_TOKEN)
        patch.start()
        self.addCleanup(patch.stop)

    def _result(self, mid: str, body: dict):
        return self.post_json(f"/api/worker/jobs/{mid}/result", body,
                              headers={"Authorization": f"Bearer {WORKER_TOKEN}"})

    def _draft(self):
        mid = new_mid()
        self.store.add_draft(mid, owner_id=self.uid_a, tracks={"mix": "s3://b/mix.wav"},
                             title="ทดสอบ waveform")
        return mid

    def _base(self):
        return {"segments": [{"start": 0, "end": 1, "text": "สวัสดี"}],
                "language": "th", "duration": 1.0, "summary": "สรุป"}

    def test_good_peaks_are_stored(self):
        mid = self._draft()
        status, resp, _ = self._result(mid, {**self._base(), "peaks": [10, 20, 100]})
        self.assertEqual(status, 200, resp)
        self.assertEqual(self.store.meetings[mid]["peaks"], [10, 20, 100])

    def test_hostile_peaks_do_not_reach_the_store(self):
        mid = self._draft()
        status, resp, _ = self._result(mid, {**self._base(),
                                             "peaks": ["<script>", float("nan")]})
        # งานยังสำเร็จ — บทถอดเสียงแพงเกินกว่าจะทิ้งทั้งก้อนเพราะกราฟเสีย
        self.assertEqual(status, 200, resp)
        self.assertIsNone(self.store.meetings[mid]["peaks"])
        self.assertEqual(len(self.store.meetings[mid]["segments_list"]), 1)

    def test_missing_peaks_is_not_an_error(self):
        mid = self._draft()
        status, resp, _ = self._result(mid, self._base())
        self.assertEqual(status, 200, resp)
        self.assertIsNone(self.store.meetings[mid]["peaks"])

    def test_peaks_come_back_from_the_api(self):
        mid = self._draft()
        self._result(mid, {**self._base(), "peaks": [5, 55, 95]})
        status, body, _ = self.get(f"/api/meetings/{mid}", cookies={"mai_session": self.tokA})
        self.assertEqual(status, 200, body)
        self.assertEqual(body["peaks"], [5, 55, 95])


def _ffmpeg() -> str | None:
    from meeting_ai.config import config
    return shutil.which(config.ffmpeg_bin) or shutil.which("ffmpeg")


@unittest.skipUnless(_ffmpeg(), "ต้องมี ffmpeg ถึงจะสร้างไฟล์เสียงและถอดค่าออกมาได้")
class TestAudioPeaksFromRealFile(unittest.TestCase):
    """runner.audio_peaks() กับไฟล์เสียงจริง — ต้องได้กราฟที่สะท้อนความดังจริง ไม่ใช่ค่าคงที่."""

    @classmethod
    def setUpClass(cls) -> None:
        from meeting_ai import runner
        cls.runner = runner
        cls.tmp = Path(tempfile.mkdtemp(prefix="mai-peaks-"))
        cls.ff = _ffmpeg()

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _make(self, name: str, args: list[str]) -> Path:
        path = self.tmp / name
        subprocess.run([self.ff, "-hide_banner", "-loglevel", "error", *args, str(path), "-y"],
                       check=True, capture_output=True)
        return path

    def test_loud_and_quiet_parts_give_different_bars(self):
        # เสียงดังสลับเบาเป็นจังหวะ (tremolo คาบ 4 วินาที) — กราฟต้องขึ้นลงตาม
        path = self._make("tremolo.wav", [
            "-f", "lavfi", "-i", "sine=frequency=220:duration=16",
            "-af", "volume='0.1+0.9*abs(sin(2*PI*t/4))':eval=frame", "-ac", "1", "-ar", "8000"])
        peaks = self.runner.audio_peaks(path)
        self.assertIsNotNone(peaks)
        self.assertEqual(len(peaks), self.runner.PEAK_BUCKETS)
        self.assertTrue(all(isinstance(v, int) and 0 <= v <= 100 for v in peaks), peaks)
        self.assertEqual(max(peaks), 100, "ต้อง normalise ให้ค่าสูงสุดเป็น 100")
        # ถ้าคำนวณผิดเป็นค่าคงที่ ค่านี้จะเป็น 1
        self.assertGreater(len(set(peaks)), 5, f"กราฟแบนเกินไป: {peaks}")

    def test_silence_returns_none(self):
        path = self._make("silence.wav", [
            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "3"])
        self.assertIsNone(self.runner.audio_peaks(path))

    def test_missing_file_returns_none(self):
        self.assertIsNone(self.runner.audio_peaks(self.tmp / "ไม่มีไฟล์นี้.wav"))

    def test_buckets_are_configurable(self):
        path = self._make("short.wav", [
            "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-ac", "1", "-ar", "8000"])
        self.assertEqual(len(self.runner.audio_peaks(path, buckets=16)), 16)


# ---------- Postgres จริง (ไม่บังคับ) — คอลัมน์ peaks ต้องเขียน-อ่านได้จริง ----------

@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"),
                     "ต้องมี Postgres ทดสอบจริงใน MAI_TEST_DATABASE_URL")
class TestPeaksAgainstRealDb(unittest.TestCase):
    """FakeStore เก็บ peaks ใน dict ซึ่งพิสูจน์ได้แค่ว่า apply_result ส่งค่าต่อถูก —
    คอลัมน์ jsonb จริงกับ migration ของฐานที่มีข้อมูลอยู่แล้วต้องยืนยันกับ Postgres เอง."""

    def setUp(self) -> None:
        from meeting_ai.web import db as pgdb
        from meeting_ai.web import pgstore

        self.pgdb = pgdb
        self.pgstore = pgstore
        env_patch = mock.patch.dict(
            os.environ, {"DATABASE_URL": os.environ["MAI_TEST_DATABASE_URL"]})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        pgdb.init()
        self.addCleanup(pgdb.close)
        self.mid = new_mid()
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        with self.pgdb.connect() as conn:
            conn.execute("delete from meeting_ai.meetings where id = %s", (self.mid,))

    def _create(self, peaks):
        return self.pgstore.create(
            mid=self.mid, title="ทดสอบ peaks", audio_name="a.ogg", source="upload",
            language="th", duration=3600.0,
            segments=[{"start": 0.0, "end": 1.0, "text": "สวัสดี", "speaker": ""}],
            summary="สรุป", peaks=peaks)

    def test_peaks_round_trip_through_jsonb(self):
        self._create([51, 96, 88, 37])
        self.assertEqual(self.pgstore.get(self.mid)["peaks"], [51, 96, 88, 37])

    def test_meeting_without_peaks_reads_back_as_none(self):
        self._create(None)
        self.assertIsNone(self.pgstore.get(self.mid)["peaks"])

    def test_upsert_replaces_peaks(self):
        # งานเดิมถูกรันซ้ำ (เช่นสั่งประมวลผลใหม่) ต้องทับของเก่า ไม่ใช่ปล่อยค้าง
        self._create([1, 2, 3])
        self._create([9, 9, 9])
        self.assertEqual(self.pgstore.get(self.mid)["peaks"], [9, 9, 9])

    def test_get_and_create_survive_a_database_without_the_column(self):
        """เจอจริงบน production 2026-09-18: deploy โค้ดใหม่แล้วแต่ยังไม่ได้รัน db-init

        `select ... peaks` ทำให้ **เปิดการประชุมไม่ได้ทั้งระบบ** ทั้งที่ waveform เป็นของเสริม
        บน Vercel การ deploy โค้ดกับการ migrate เป็นคนละขั้นตอน โค้ดขึ้นก่อน schema เสมอ
        """
        with self.pgdb.connect() as conn:
            conn.execute("alter table meeting_ai.meetings drop column if exists peaks")
        self.pgstore.reset_peaks_cache()
        try:
            self._create([1, 2, 3])          # ส่ง peaks มาแต่ฐานรับไม่ได้ — ต้องไม่ระเบิด
            got = self.pgstore.get(self.mid)
            self.assertIsNotNone(got, "เปิดการประชุมไม่ได้เมื่อฐานยังไม่มีคอลัมน์")
            self.assertIsNone(got["peaks"])
            self.assertEqual(got["title"], "ทดสอบ peaks")
            self.assertEqual(len(got["segments_list"]), 1)
        finally:
            self.pgdb.init()                 # คืนคอลัมน์ให้เทสต์ตัวอื่น + ล้างแคชให้เอง

    def test_column_exists_after_init(self):
        # db.init() รัน schema.sql ซึ่งมีทั้ง create table และ alter table add column if not exists
        with self.pgdb.connect() as conn:
            row = conn.execute(
                """select data_type from information_schema.columns
                   where table_schema = 'meeting_ai' and table_name = 'meetings'
                     and column_name = 'peaks'""").fetchone()
        self.assertIsNotNone(row, "ไม่มีคอลัมน์ peaks หลัง db.init()")
        self.assertEqual(row[0], "jsonb")


if __name__ == "__main__":
    unittest.main()
