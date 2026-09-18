"""BACKLOG #31 — diarize.py โหลด WAV ทั้งไฟล์เป็น list ของ float และจับคู่ผู้พูดแบบ O(segments x turns).

**หน่วยความจำ** — `[s / 32768.0 for s in ints]` สร้าง float ของ Python หนึ่งตัวต่อหนึ่ง sample
ตัวละ 24 ไบต์ บวกพอยน์เตอร์ในลิสต์อีก 8 วัดจริงด้วย tracemalloc ได้ **32.8 ไบต์ต่อ sample**
= 3.78 GB สำหรับประชุม 2 ชั่วโมงที่ 16 kHz บัฟเฟอร์ float32 ใช้ 4 ไบต์ = 0.46 GB (ลดลง 8 เท่า)
sherpa-onnx รับ numpy float32 อยู่แล้วตามตัวอย่างของมันเอง จึงไม่ต้องแปลงอะไรเพิ่ม

**เวลา** — `label_segments()` วน turns ทั้งหมดต่อหนึ่ง segment ประชุม 2 ชั่วโมงมีราว 2,900
segment และ turn ได้เป็นพัน = สิบล้านรอบใน Python ทั้งสองรายการเรียงตามเวลาอยู่แล้ว จึงเดิน
สองตัวชี้พร้อมกันได้

**กับดักที่เกือบพลาด** — segment ที่อยู่ในหลาย turn พร้อมกัน (คนพูดทับกัน) ทุก turn จะทับ
เท่ากับความยาว segment **เป๊ะ ๆ** ของเดิมวน turns ตามลำดับที่รับมาแล้วแทนที่เฉพาะตอน "มากกว่า"
ผู้ชนะจึงเป็นตัวแรกในลำดับนั้น เรียงใหม่โดยไม่คุมจุดนี้ = ผลเปลี่ยนเงียบ ๆ (วัดตอนพัฒนา:
ต่างกัน 84 ใน 300 ชุดสุ่ม) เทสต์กลุ่ม TestLabellingIsUnchanged คุมเส้นนี้ไว้
"""

from __future__ import annotations

import array
import random
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

from meeting_ai import diarize
from meeting_ai.diarize import SpeakerTurn

RATE = 16000


def write_wav(path: Path, samples: list[int], rate: int = RATE,
              channels: int = 1, width: int = 2) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        if width == 2:
            wf.writeframes(array.array("h", samples).tobytes())
        else:
            wf.writeframes(bytes(samples))


def reference_label(segments: list[dict], turns: list[SpeakerTurn], namer=None) -> list[dict]:
    """อัลกอริทึมเดิมทั้งดุ้น — เป็น "คำตอบที่ถูก" ที่ตัวใหม่ต้องได้เท่ากัน."""
    if not turns:
        return segments
    order: dict[int, int] = {}
    for turn in sorted(turns, key=lambda t: t.start):
        if turn.speaker not in order:
            order[turn.speaker] = len(order)

    def name_of(raw: int) -> str:
        idx = order.get(raw, raw)
        return namer(idx) if namer else f"ผู้พูด {idx + 1}"

    for seg in segments:
        s_start, s_end = seg.get("start", 0.0), seg.get("end", 0.0)
        best_overlap, best_speaker = 0.0, None
        for turn in turns:
            overlap = min(s_end, turn.end) - max(s_start, turn.start)
            if overlap > best_overlap:
                best_overlap, best_speaker = overlap, turn.speaker
        if best_speaker is None:
            nearest = min(turns, key=lambda t: min(abs(t.start - s_start), abs(t.end - s_end)))
            best_speaker = nearest.speaker
        seg["speaker"] = name_of(best_speaker)
    return segments


class WavCase(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-diar-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def wav(self, samples: list[int], **kw) -> Path:
        path = self.tmp / f"a{len(list(self.tmp.glob('*.wav')))}.wav"
        write_wav(path, samples, **kw)
        return path


class TestTheReaderKeepsItsValues(WavCase):

    def test_values_match_the_plain_conversion(self):
        raw = [0, 1, -1, 32767, -32768, 1234, -4321]
        got, rate = diarize._read_wav_mono16k(self.wav(raw))
        self.assertEqual(rate, RATE)
        self.assertEqual(len(got), len(raw))
        for i, want in enumerate(v / 32768.0 for v in raw):
            self.assertAlmostEqual(float(got[i]), want, places=6)

    def test_an_empty_file_is_not_an_error(self):
        got, _ = diarize._read_wav_mono16k(self.wav([]))
        self.assertEqual(len(got), 0)

    def test_it_reads_past_one_chunk(self):
        # อ่านทีละก้อนแล้วต่อกันผิด = ได้เสียงขาดหายโดยไม่มี error ให้ใครเห็น
        raw = [(i % 2000) - 1000 for i in range(5000)]
        with mock.patch.object(diarize, "READ_FRAMES", 64):
            got, _ = diarize._read_wav_mono16k(self.wav(raw))
        self.assertEqual(len(got), len(raw))
        self.assertAlmostEqual(float(got[-1]), raw[-1] / 32768.0, places=6)
        self.assertAlmostEqual(float(got[2500]), raw[2500] / 32768.0, places=6)

    def test_stereo_is_rejected(self):
        with self.assertRaises(RuntimeError):
            diarize._read_wav_mono16k(self.wav([0, 0, 0, 0], channels=2))

    def test_8_bit_is_rejected(self):
        with self.assertRaises(RuntimeError):
            diarize._read_wav_mono16k(self.wav([0, 1, 2, 3], width=1))


class TestTheReaderIsNotAListOfPythonFloats(WavCase):

    def test_four_bytes_per_sample_not_thirty_two(self):
        n = 20000
        got, _ = diarize._read_wav_mono16k(self.wav([100] * n))
        self.assertNotIsInstance(got, list, "list ของ float คือบั๊กเดิมทั้งข้อ")
        per_sample = sys.getsizeof(got) / n
        self.assertLess(per_sample, 8,
                        f"ใช้ {per_sample:.1f} ไบต์ต่อ sample — float32 ต้องได้ราว 4")

    def test_the_buffer_is_float32(self):
        got, _ = diarize._read_wav_mono16k(self.wav([100, 200]))
        itemsize = getattr(got, "itemsize", None)
        self.assertEqual(itemsize, 4, f"itemsize = {itemsize}")

    def test_the_array_fallback_works_without_numpy(self):
        # numpy เป็น dependency ของ sherpa-onnx อยู่แล้ว แต่ตัวอ่านต้องไม่ล้มถ้าไม่มี
        raw = [0, 500, -500, 32767]
        real_import = __import__

        def no_numpy(name, *a, **k):
            if name == "numpy":
                raise ImportError("ปิดไว้เพื่อทดสอบ")
            return real_import(name, *a, **k)

        with mock.patch("builtins.__import__", no_numpy):
            got, _ = diarize._read_wav_mono16k(self.wav(raw))
        self.assertIsInstance(got, array.array)
        self.assertEqual(got.itemsize, 4)
        for i, want in enumerate(v / 32768.0 for v in raw):
            self.assertAlmostEqual(got[i], want, places=6)


class TestLabellingIsUnchanged(unittest.TestCase):
    """ตัวเร่งความเร็วที่เปลี่ยนคำตอบ ไม่ใช่ตัวเร่งความเร็ว."""

    @staticmethod
    def _case(rng: random.Random) -> tuple[list[dict], list[SpeakerTurn]]:
        turns, cursor = [], 0.0
        for _ in range(rng.randint(1, 40)):
            # ก้าวติดลบได้ เพื่อให้ turn ซ้อนกันจริงแบบตอนคนพูดพร้อมกัน และลำดับที่รับเข้ามาไม่เรียง
            cursor += rng.uniform(-0.5, 3.0)
            start = max(0.0, cursor)
            turns.append(SpeakerTurn(start=start, end=start + rng.uniform(0.1, 4.0),
                                     speaker=rng.randint(0, 4)))
        segments, cursor = [], 0.0
        for _ in range(rng.randint(1, 60)):
            cursor += rng.uniform(0.0, 2.5)
            segments.append({"start": cursor, "end": cursor + rng.uniform(0.05, 3.0)})
        return segments, turns

    def test_it_matches_the_old_algorithm_on_random_data(self):
        rng = random.Random(20260918)
        for trial in range(300):
            segments, turns = self._case(rng)
            want = reference_label([dict(s) for s in segments], list(turns))
            got = diarize.label_segments([dict(s) for s in segments], list(turns))
            with self.subTest(trial=trial):
                self.assertEqual([s["speaker"] for s in got], [s["speaker"] for s in want])

    def test_a_tie_goes_to_the_first_turn_in_the_given_order(self):
        # segment ที่อยู่ในสาม turn พร้อมกัน ทุกตัวทับเท่ากับความยาว segment เป๊ะ ๆ
        turns = [SpeakerTurn(start=5.0, end=9.0, speaker=7),
                 SpeakerTurn(start=1.0, end=9.0, speaker=3),
                 SpeakerTurn(start=4.0, end=8.0, speaker=5)]
        segments = [{"start": 6.0, "end": 7.0}]
        want = reference_label([dict(s) for s in segments], list(turns))
        got = diarize.label_segments([dict(s) for s in segments], list(turns))
        self.assertEqual(got[0]["speaker"], want[0]["speaker"])

    def test_no_turns_leaves_segments_alone(self):
        segments = [{"start": 0.0, "end": 1.0}]
        self.assertEqual(diarize.label_segments(segments, []), segments)
        self.assertNotIn("speaker", segments[0])

    def test_a_segment_overlapping_nothing_takes_the_nearest_turn(self):
        turns = [SpeakerTurn(start=100.0, end=101.0, speaker=2),
                 SpeakerTurn(start=0.0, end=1.0, speaker=1)]
        got = diarize.label_segments([{"start": 50.0, "end": 50.5}], turns)
        want = reference_label([{"start": 50.0, "end": 50.5}], turns)
        self.assertEqual(got[0]["speaker"], want[0]["speaker"])

    def test_speaker_numbers_follow_first_appearance(self):
        turns = [SpeakerTurn(start=10.0, end=11.0, speaker=9),
                 SpeakerTurn(start=0.0, end=1.0, speaker=4)]
        got = diarize.label_segments([{"start": 0.0, "end": 1.0},
                                      {"start": 10.0, "end": 11.0}], turns)
        self.assertEqual([s["speaker"] for s in got], ["ผู้พูด 1", "ผู้พูด 2"])

    def test_a_custom_namer_is_used(self):
        turns = [SpeakerTurn(start=0.0, end=1.0, speaker=3)]
        got = diarize.label_segments([{"start": 0.0, "end": 1.0}], turns,
                                     namer=lambda i: f"คนที่ {i}")
        self.assertEqual(got[0]["speaker"], "คนที่ 0")


class CountingTurn(SpeakerTurn):
    """นับจำนวนครั้งที่ถูกเปรียบเทียบ — พิสูจน์ว่าเลิกวนทั้งรายการต่อหนึ่ง segment จริง."""

    looks = 0

    @property
    def end(self):
        CountingTurn.looks += 1
        return self._end

    @end.setter
    def end(self, value):
        self._end = value


class TestItStopsScanningEverything(unittest.TestCase):

    def test_the_work_does_not_grow_with_segments_times_turns(self):
        n = 400
        turns = [CountingTurn(start=i * 1.0, end=i * 1.0 + 0.9, speaker=i % 3) for i in range(n)]
        segments = [{"start": i * 1.0, "end": i * 1.0 + 0.8} for i in range(n)]
        CountingTurn.looks = 0
        diarize.label_segments(segments, turns)
        self.assertLess(CountingTurn.looks, n * n // 10,
                        f"ยังดู turn ไป {CountingTurn.looks:,} ครั้ง จากที่เป็นไปได้ {n * n:,}")

    def test_every_segment_still_gets_a_speaker(self):
        n = 400
        turns = [SpeakerTurn(start=i * 1.0, end=i * 1.0 + 0.9, speaker=i % 3) for i in range(n)]
        segments = [{"start": i * 1.0, "end": i * 1.0 + 0.8} for i in range(n)]
        got = diarize.label_segments(segments, turns)
        self.assertTrue(all(s.get("speaker") for s in got))


if __name__ == "__main__":
    unittest.main()
