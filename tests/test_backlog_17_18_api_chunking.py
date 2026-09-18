"""BACKLOG #17 + #18 — ทางถอดเสียงผ่าน API: timestamp ของท่อน และตัวกรองข้อความหลอน.

**#17** `_prepare()` ตัดไฟล์ยาวด้วย `ffmpeg -f segment -c copy` แล้วสมมติว่าท่อนที่ i เริ่มที่
`i * 900` วินาที ของจริงผิดสองชั้น:

1. Ogg เก็บ granule position เป็น "เวลาสัมบูรณ์ของสตรีมเดิม" ตัดด้วย `-c copy` เฉย ๆ ท่อนที่ห้า
   จึงเป็นไฟล์ที่ **ประกาศว่าตัวเองยาวตั้งแต่ 0** ทั้งที่มีเสียงอยู่แค่ช่วงท้าย — วัดจริงด้วย
   ffprobe กับไฟล์ที่ตัดท่อนละ 7 วินาที ได้ duration 7, 14, 21, 28... ไล่ขึ้นตามลำดับท่อน
   ตัวถอดเสียงฝั่ง API ก็ decode ด้วย libav เหมือนกัน มันจึงคืน timestamp ที่บวก offset มาแล้ว
   แล้วโค้ดบวก `i*900` ทับเข้าไปอีก = **เลื่อนสองเท่า** ไม่ใช่แค่คลาดนิดหน่อยอย่างที่ตั๋วเขียน
2. `-segment_time` แปลว่า "ตัดที่แพ็กเก็ตแรกตั้งแต่เวลานี้ไป" ไม่ใช่ตัดตรงเป๊ะ ความยาวจริงของ
   แต่ละท่อนจึงไม่เท่ากันเสมอไป และความคลาดสะสมทบกันไปทุกท่อน

แก้ด้วย `-reset_timestamps 1` (ให้ไทม์ไลน์ของแต่ละท่อนเริ่มที่ 0 ตามที่โค้ดสมมติไว้อยู่แล้ว)
และถาม offset จาก `-segment_list csv` ที่ ffmpeg เขียนเอง แทนการคำนวณจากลำดับท่อน

**#18** `drop_hallucinations()` ถูกเรียกเฉพาะทาง whisper ในเครื่อง การวนคำเดิมซ้ำ ๆ ตอนเจอ
ช่วงเงียบเป็นพฤติกรรมของ "โมเดล" ไม่ใช่ของ "ที่รัน" ฝั่ง API จึงวนเหมือนกัน แต่ไม่มีใครกรอง
ขยะไหลเข้าบทถอดเสียง ไปโผล่ในสรุป และกิน token ของ LLM ไปฟรี ๆ
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import stt, transcriber
from meeting_ai.config import config
from meeting_ai.transcriber import Segment

HAVE_FFMPEG = bool(shutil.which(config.ffmpeg_bin)) and bool(shutil.which("ffprobe"))


class TestChunkOffsetsComeFromFfmpeg(unittest.TestCase):

    def _prepare(self, calls: list[list[str]], csv: str | None):
        """เรียก _prepare() กับไฟล์ที่ 'ใหญ่เกิน' โดยปลอม ffmpeg ให้เขียนท่อน + รายการตามสั่ง."""
        tmp = Path(tempfile.mkdtemp(prefix="mai-chunk-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = tmp / "src.wav"
        src.write_bytes(b"x" * (stt.MAX_UPLOAD_BYTES + 1))
        work = tmp / "work"
        work.mkdir()

        def fake_ffmpeg(args):
            calls.append(args)
            if "-f" in args and "segment" in args:
                for i in range(3):
                    (work / f"part-{i:03d}.ogg").write_bytes(b"ogg")
                if csv is not None:
                    (work / "parts.csv").write_text(csv, encoding="utf-8")
            else:                                   # ขั้นบีบไฟล์ — ต้องยังใหญ่เกินเพื่อให้ไปต่อถึงขั้นตัด
                (work / "packed.ogg").write_bytes(b"x" * (stt.MAX_UPLOAD_BYTES + 1))

        with mock.patch.object(stt, "_ffmpeg", fake_ffmpeg):
            return stt._prepare(src, work)

    def test_it_asks_ffmpeg_to_reset_each_parts_timeline(self):
        calls: list[list[str]] = []
        self._prepare(calls, "part-000.ogg,0,900\npart-001.ogg,900,1800\npart-002.ogg,1800,2600\n")
        cut = [c for c in calls if "segment" in c][0]
        self.assertIn("-reset_timestamps", cut)
        self.assertEqual(cut[cut.index("-reset_timestamps") + 1], "1")

    def test_it_asks_ffmpeg_for_the_segment_list(self):
        calls: list[list[str]] = []
        self._prepare(calls, "part-000.ogg,0,900\npart-001.ogg,900,1800\npart-002.ogg,1800,2600\n")
        cut = [c for c in calls if "segment" in c][0]
        self.assertIn("-segment_list", cut)
        self.assertIn("csv", cut)

    def test_offsets_are_the_real_ones_not_the_nominal_ones(self):
        # ท่อนจริงยาวไม่เท่ากัน: 0, 898.4, 1795.2 — สูตรเดิมจะได้ 0, 900, 1800
        got = self._prepare([], "part-000.ogg,0.000,898.400\n"
                                "part-001.ogg,898.400,1795.200\n"
                                "part-002.ogg,1795.200,2600.000\n")
        self.assertEqual([off for _, off in got], [0.0, 898.4, 1795.2])

    def test_the_parts_are_returned_in_order(self):
        got = self._prepare([], "part-000.ogg,0,900\npart-001.ogg,900,1800\n"
                                "part-002.ogg,1800,2600\n")
        self.assertEqual([p.name for p, _ in got],
                         ["part-000.ogg", "part-001.ogg", "part-002.ogg"])

    def test_a_missing_list_falls_back_and_says_so(self):
        with mock.patch.object(stt, "_notice") as notice:
            got = self._prepare([], None)
        self.assertEqual([off for _, off in got],
                         [0.0, float(stt.CHUNK_SECONDS), 2 * float(stt.CHUNK_SECONDS)])
        notice.assert_called_once()

    def test_a_short_or_broken_list_falls_back_rather_than_guessing_wrong(self):
        # รายการไม่ครบ = จับคู่ผิดได้ง่ายกว่าค่าประมาณ ต้องไม่เดาเอาเอง
        with mock.patch.object(stt, "_notice"):
            got = self._prepare([], "part-000.ogg,0,900\nไม่ใช่ csv\n")
        self.assertEqual([off for _, off in got],
                         [0.0, float(stt.CHUNK_SECONDS), 2 * float(stt.CHUNK_SECONDS)])

    def test_a_small_file_is_sent_whole_with_no_offset(self):
        tmp = Path(tempfile.mkdtemp(prefix="mai-chunk-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        src = tmp / "small.wav"
        src.write_bytes(b"x" * 10)
        self.assertEqual(stt._prepare(src, tmp), [(src, 0.0)])


@unittest.skipUnless(HAVE_FFMPEG, "ต้องมี ffmpeg จริงเพื่อพิสูจน์พฤติกรรมของ Ogg granule position")
class TestTheRealFfmpegBehaviour(unittest.TestCase):
    """พิสูจน์กับ ffmpeg ตัวจริง ไม่ใช่เชื่อคำอธิบายในตั๋ว."""

    SEG = 7

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-ogg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.packed = self.tmp / "packed.ogg"
        try:
            self._ff(["-f", "lavfi", "-i", "sine=frequency=440:duration=35",
                      "-ac", "1", "-c:a", "libopus", "-b:a", "24k", str(self.packed)])
        except (subprocess.CalledProcessError, OSError) as e:
            # ffmpeg บางบิลด์ไม่มี libopus/lavfi — ข้ามไป ดีกว่าทำ CI แดงด้วยเรื่องที่ไม่ใช่โค้ดเรา
            self.skipTest(f"ffmpeg ตัวนี้สร้างไฟล์ทดสอบไม่ได้: {e}")

    def _ff(self, args: list[str]) -> None:
        subprocess.run([config.ffmpeg_bin, "-y", "-loglevel", "error", *args],
                       capture_output=True, check=True)

    def _cut(self, out: Path, reset: bool) -> list[Path]:
        out.mkdir()
        extra = ["-reset_timestamps", "1"] if reset else []
        self._ff(["-i", str(self.packed), "-f", "segment", "-segment_time", str(self.SEG),
                  *extra, "-c", "copy", str(out / "part-%03d.ogg")])
        return sorted(out.glob("part-*.ogg"))

    @staticmethod
    def _duration(path: Path) -> float:
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(path)],
                               capture_output=True, text=True, check=True)
        return float(probe.stdout.strip())

    def test_without_the_flag_each_part_claims_the_whole_timeline(self):
        # นี่คือบั๊ก: ท่อนที่ i รายงานความยาว (i+1)*7 ทั้งที่มีเสียงอยู่ 7 วินาที
        parts = self._cut(self.tmp / "plain", reset=False)
        self.assertGreater(len(parts), 3, "ต้องได้หลายท่อนถึงจะเห็นอาการ")
        durations = [self._duration(p) for p in parts]
        self.assertAlmostEqual(durations[0], self.SEG, delta=0.1)
        self.assertAlmostEqual(durations[2], 3 * self.SEG, delta=0.1,
                               msg=f"ท่อนที่สามควรอ้างว่ายาว {3 * self.SEG}s ได้ {durations[2]}")

    def test_with_the_flag_every_part_is_its_own_length(self):
        parts = self._cut(self.tmp / "reset", reset=True)
        for i, p in enumerate(parts[:-1]):
            with self.subTest(part=p.name):
                self.assertAlmostEqual(self._duration(p), self.SEG, delta=0.1,
                                       msg=f"ท่อนที่ {i} ต้องยาว {self.SEG}s ไม่ใช่สะสม")

    def test_prepare_produces_parts_whose_offsets_match_their_content(self):
        # ปลายทางที่แท้จริงของทั้งข้อ: offset ที่เราจะไปบวก ต้องตรงกับเสียงที่อยู่ในท่อนนั้นจริง ๆ
        work = self.tmp / "work"
        work.mkdir()
        with mock.patch.object(stt, "MAX_UPLOAD_BYTES", 1024), \
             mock.patch.object(stt, "CHUNK_SECONDS", self.SEG):
            got = stt._prepare(self.packed, work)
        self.assertGreater(len(got), 3)
        for i, (part, offset) in enumerate(got[:-1]):
            with self.subTest(part=part.name):
                self.assertAlmostEqual(offset, i * self.SEG, delta=0.2)
                self.assertAlmostEqual(self._duration(part), self.SEG, delta=0.2)


class TestHallucinationsAreFilteredOnBothPaths(unittest.TestCase):

    LOOP = "ที่สุด" * 40

    def test_the_filter_itself_still_catches_the_loop(self):
        # กันเทสต์ข้างล่างผ่านแบบว่างเปล่า ถ้าตัวกรองเลิกจับอะไรเลย
        self.assertTrue(transcriber.looks_hallucinated(self.LOOP))
        self.assertFalse(transcriber.looks_hallucinated("เปิดประชุมครับ วันนี้คุยเรื่องงบประมาณ"))

    def _api_transcript(self, texts: list[str]):
        segs = [Segment(start=float(i), end=float(i + 1), text=t) for i, t in enumerate(texts)]
        with mock.patch.object(stt.config, "stt_key", lambda: "k"), \
             mock.patch.object(stt, "_prepare", lambda src, work: [(src, 0.0)]), \
             mock.patch.object(stt, "_post_audio", lambda *a, **k: {"language": "th"}), \
             mock.patch.object(stt, "_segments_from", lambda data, off: segs):
            return stt._transcribe_api(Path("x.wav"), "th", None, None)

    def test_the_api_path_drops_the_loop(self):
        got = self._api_transcript(["เปิดประชุมครับ", self.LOOP, "สรุปงบประมาณ"])
        self.assertEqual([s.text for s in got.segments], ["เปิดประชุมครับ", "สรุปงบประมาณ"])

    def test_the_api_path_keeps_everything_else(self):
        texts = ["เปิดประชุมครับ", "ขอเริ่มที่งบก่อน", "ตกลงตามนี้"]
        self.assertEqual([s.text for s in self._api_transcript(texts).segments], texts)

    def test_the_detected_language_survives_the_filter(self):
        self.assertEqual(self._api_transcript(["เปิดประชุมครับ"]).language, "th")

    def test_both_paths_use_the_same_filter(self):
        # คนละตัวกรองสองชุด = วันหนึ่งจะเพี้ยนกันเอง ต้องเป็นฟังก์ชันเดียวกันจริง ๆ
        src = Path(stt.__file__).read_text(encoding="utf-8")
        self.assertIn("transcriber.drop_hallucinations(segments)", src)


if __name__ == "__main__":
    unittest.main()
