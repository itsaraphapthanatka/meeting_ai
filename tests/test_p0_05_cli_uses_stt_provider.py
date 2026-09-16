"""BACKLOG #5 — เส้นทาง CLI (transcribe/process) เดิมเรียก transcriber.transcribe() ตรงๆ
ข้าม stt.resolve()/stt.transcribe() ไปเลย ทำให้ STT_PROVIDER=api ใน .env ไม่มีผลกับ CLI
(ใช้ได้แต่กับหน้าเว็บที่เรียก stt.resolve() เอง)

หลังแก้: cli._cmd_transcribe และ pipeline.process_file (ที่ cli._cmd_process เรียก) เลือก
provider ผ่าน stt.resolve() แล้วถอดเสียงผ่าน stt.transcribe(..., provider=used) เสมอ
เทสต์นี้ยืนยันด้วยการปลอม transcriber.transcribe ให้ระเบิดถ้าถูกเรียก (เส้นทางเดิม) —
ถ้าใครย้อนกลับไปเรียก transcriber ตรงๆ อีก เทสต์นี้จะพังทันที

Round 2: เพิ่มแฟลก --stt {local,api} ให้ record/transcribe/process/bot แล้ว _cmd_transcribe
เรียก stt.resolve(args.stt) ตรงๆ, process_file(..., stt_provider=args.stt) — เทสต์รอบแรก
ปลอม stt.resolve ด้วย lambda ที่กลืนอาร์กิวเมนต์ทิ้ง (`lambda name=None: "api"`) พิสูจน์ไม่ได้
ว่าค่าอะไรถูกส่งเข้าไปจริง จึงเปลี่ยนมาใช้ mock.Mock(return_value="api") ที่ยังคงพฤติกรรมเดิม
(คืน "api" เสมอ ไม่ว่าจะรับอาร์กิวเมนต์อะไร) แต่บันทึก call args ไว้ให้ตรวจสอบได้ด้วย
"""

import contextlib
import io
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from _harness import backend  # noqa: F401  ทุกไฟล์เทสต์ import _harness ก่อนเสมอ

import meeting_ai
from meeting_ai import cli, pipeline, stt, summarizer, transcriber


class _SttProviderPatchMixin:
    """ปลอม stt.resolve/stt.transcribe ให้คืนค่า "api" เสมอ และให้ transcriber.transcribe
    (เส้นทาง whisper ในเครื่อง) ระเบิดถ้าถูกเรียก — พิสูจน์ว่าไม่มีใครหลุดไปเรียกเส้นนั้น

    self.resolve_mock เป็น mock.Mock (ไม่ใช่ lambda ธรรมดา) เพื่อให้เทสต์ตรวจ call_args ได้ว่า
    stt.resolve() ถูกเรียกด้วยค่าอะไรจริงๆ (เช่น args.stt จากแฟลก --stt) ไม่ใช่แค่ดูผลลัพธ์ปลายทาง
    """

    def _patch_stt(self) -> None:
        self.fake_transcript = transcriber.Transcript(
            language="th",
            segments=[transcriber.Segment(start=0.0, end=1.5, text="สวัสดี")],
        )
        self.resolve_mock = mock.Mock(return_value="api")
        self.transcribe_recorder = mock.Mock(return_value=(self.fake_transcript, "api"))
        self.whisper_mock = mock.Mock(
            side_effect=AssertionError("ห้ามเรียก transcriber.transcribe (เส้นทาง whisper ในเครื่อง)"))

        patches = [
            mock.patch.object(stt, "resolve", self.resolve_mock),
            mock.patch.object(stt, "transcribe", self.transcribe_recorder),
            mock.patch.object(transcriber, "transcribe", self.whisper_mock),
            mock.patch.object(summarizer, "summarize", lambda *a, **k: "สรุป"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class TestPipelineUsesSttProvider(_SttProviderPatchMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-pipeline-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._patch_stt()

    def test_process_file_routes_through_resolved_provider(self) -> None:
        wav = self.tmp / "meeting.wav"
        wav.write_bytes(b"")
        out_dir = self.tmp / "out"

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result = pipeline.process_file(wav, title="ประชุมทดสอบ", language="th", out_dir=out_dir)

        self.assertEqual(self.transcribe_recorder.call_count, 1)
        _, kwargs = self.transcribe_recorder.call_args
        self.assertEqual(kwargs.get("provider"), "api")
        self.assertEqual(kwargs.get("language"), "th")
        self.assertEqual(self.whisper_mock.call_count, 0)

        self.assertEqual(set(result), {"report", "transcript", "summary"})
        self.assertTrue(Path(result["report"]).exists())
        self.assertTrue(Path(result["transcript"]).exists())
        self.assertEqual(result["summary"], "สรุป")


class TestCliUsesSttProvider(_SttProviderPatchMixin, unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-cli-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._patch_stt()

    def test_transcribe_subcommand_uses_resolved_provider(self) -> None:
        wav = self.tmp / "meeting.wav"
        wav.write_bytes(b"")
        out = self.tmp / "out.txt"

        stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            rc = cli.main(["transcribe", str(wav), "-o", str(out), "--lang", "th"])

        self.assertEqual(rc, 0)
        self.assertEqual(out.read_text(encoding="utf-8"), self.fake_transcript.to_timestamped())
        # ประกาศ provider ไปที่ stderr เท่านั้น (กัน pipe stdout ต่อไม่ให้ปนข้อความนี้)
        self.assertIn("API", stderr_buf.getvalue())
        # หมายเหตุ: เมื่อใส่ -o มา cli.py ยังพิมพ์บรรทัดยืนยันไปที่ stdout ("เขียน transcript: ...")
        # ไม่ใช่ stdout ว่างเปล่าเสียทีเดียว — ตรวจแค่ว่า "ตัว transcript" ไม่ได้ถูกพิมพ์ไปสองที่
        self.assertIn(str(out), stdout_buf.getvalue())
        self.assertNotIn(self.fake_transcript.to_timestamped(), stdout_buf.getvalue())
        self.assertEqual(self.transcribe_recorder.call_args.kwargs.get("provider"), "api")
        self.assertEqual(self.whisper_mock.call_count, 0)

    def test_process_subcommand_uses_resolved_provider(self) -> None:
        wav = self.tmp / "meeting.wav"
        wav.write_bytes(b"")
        out_dir = self.tmp / "out"

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cli.main(["process", str(wav), "--out-dir", str(out_dir)])

        self.assertEqual(rc, 0)
        self.assertTrue((out_dir / "meeting_สรุป.md").exists())
        self.assertEqual(self.transcribe_recorder.call_args.kwargs.get("provider"), "api")
        self.assertEqual(self.whisper_mock.call_count, 0)

    # ---------- Round 2: --stt ต้องไหลไปถึง stt.resolve()/process_file() จริง ----------

    def test_transcribe_stt_flag_reaches_stt_resolve(self) -> None:
        wav = self.tmp / "meeting.wav"
        wav.write_bytes(b"")
        out = self.tmp / "out.txt"

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["transcribe", str(wav), "-o", str(out), "--stt", "local"])

        self.assertEqual(rc, 0)
        self.resolve_mock.assert_called_once_with("local")

    def test_transcribe_without_stt_flag_resolve_gets_none(self) -> None:
        wav = self.tmp / "meeting.wav"
        wav.write_bytes(b"")
        out = self.tmp / "out.txt"

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = cli.main(["transcribe", str(wav), "-o", str(out)])

        self.assertEqual(rc, 0)
        self.resolve_mock.assert_called_once_with(None)


class TestCliSttFlagWiring(unittest.TestCase):
    """--stt ต้องไหลจาก argparse ไปถึง pipeline.process_file() ทุกซับคำสั่งที่ประมวลผลต่อ."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mai-cli-flag-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self._fake_result = {"report": Path("r"), "transcript": Path("t"), "summary": "s"}

    def test_process_stt_flag_reaches_process_file(self) -> None:
        wav = self.tmp / "meeting.wav"
        wav.write_bytes(b"")
        out_dir = self.tmp / "out"
        process_mock = mock.Mock(return_value=self._fake_result)

        with mock.patch.object(pipeline, "process_file", process_mock):
            with contextlib.redirect_stdout(io.StringIO()):
                rc = cli.main(["process", str(wav), "--out-dir", str(out_dir), "--stt", "api"])

        self.assertEqual(rc, 0)
        self.assertEqual(process_mock.call_args.kwargs.get("stt_provider"), "api")

    def test_invalid_stt_choice_exits_code_2(self) -> None:
        # ไม่ผ่าน cli.main() เพราะ SystemExit จาก argparse หลุดออกมาก่อนถึง try/except ของมัน
        # อยู่แล้ว (parser.parse_args() เรียกนอก try) — เทสต์ตรงที่ระดับ parser ตามที่ตกลงไว้
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                cli.build_parser().parse_args(["transcribe", "x.wav", "--stt", "azure"])
        self.assertEqual(ctx.exception.code, 2)

    @staticmethod
    @contextlib.contextmanager
    def _fake_submodule(name: str, fake_module):
        """ปลอม meeting_ai.<name> ทั้งใน sys.modules และ attribute ของแพ็กเกจ meeting_ai เอง.

        `from . import name` ที่ cli.py ใช้ทำ getattr(meeting_ai, name) ก่อนเสมอ ถ้าโมดูลจริง
        เคยถูก import มาแล้วครั้งหนึ่ง (เช่น test_p0_04 import "from meeting_ai import bot"
        ไปแล้วตั้งแต่ไฟล์ก่อนหน้าในลำดับ discover) แพ็กเกจจะมี attribute นี้ค้างอยู่แล้ว —
        แพตช์แค่ sys.modules เฉยๆ จะไม่มีผลอะไรเลย เพราะ import machinery ไม่ย้อนกลับไปเช็ค
        sys.modules ซ้ำเมื่อ getattr(package, name) หาเจอไปแล้ว (เจอเองตอนเขียนเทสต์นี้ —
        ดู Lessons ในรายงาน)
        """
        key = f"meeting_ai.{name}"
        with mock.patch.dict(sys.modules, {key: fake_module}):
            with mock.patch.object(meeting_ai, name, fake_module, create=True):
                yield

    def test_record_process_passes_stt_provider(self) -> None:
        """cli._cmd_record ทำ `from . import recorder` แบบ lazy ต้องปลอมทั้งโมดูลก่อนเรียก

        cli.main เพื่อกัน mai `record` จริงไปแตะไมค์/อุปกรณ์เสียง — ปลอมแค่ recorder.record()
        เพราะนั่นคือแอตทริบิวต์เดียวที่ _cmd_record เรียกใช้จากโมดูลนี้
        """
        fake_recorder = types.ModuleType("meeting_ai.recorder")
        fake_recorder.record = mock.Mock(return_value=None)
        process_mock = mock.Mock(return_value=self._fake_result)
        out = self.tmp / "rec.wav"

        with self._fake_submodule("recorder", fake_recorder):
            with mock.patch.object(pipeline, "process_file", process_mock):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = cli.main(["record", str(out), "--process", "--stt", "local"])

        self.assertEqual(rc, 0)
        fake_recorder.record.assert_called_once()
        self.assertEqual(process_mock.call_args.kwargs.get("stt_provider"), "local")

    def test_bot_process_passes_stt_provider(self) -> None:
        """cli._cmd_bot ทำ `from . import bot` แบบ lazy — ปลอมทั้งโมดูลกัน mai `bot` จริงแตะ Docker

        แอตทริบิวต์เดียวที่ _cmd_bot เรียกจากโมดูลนี้คือ bot.join_and_record(...) -> Path
        """
        wav_path = self.tmp / "bot_meeting.wav"
        fake_bot = types.ModuleType("meeting_ai.bot")
        fake_bot.join_and_record = mock.Mock(return_value=wav_path)
        process_mock = mock.Mock(return_value=self._fake_result)

        with self._fake_submodule("bot", fake_bot):
            with mock.patch.object(pipeline, "process_file", process_mock):
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = cli.main(["bot", "https://meet.google.com/xyz-xyzx-xyz",
                                  "--out-dir", str(self.tmp), "--stt", "local"])

        self.assertEqual(rc, 0)
        fake_bot.join_and_record.assert_called_once()
        self.assertEqual(process_mock.call_args.kwargs.get("stt_provider"), "local")


if __name__ == "__main__":
    unittest.main()
