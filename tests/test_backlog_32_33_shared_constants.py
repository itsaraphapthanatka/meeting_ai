"""BACKLOG #32 + #33 — ค่าที่ต้องตรงกัน ต้องมีที่มาที่เดียว.

**#32** `runner.audio_duration()` เรียก `"ffprobe"` ตรง ๆ ทั้งที่ทุกที่อื่นใช้ `config.ffmpeg_bin`
สองตัวนี้มาด้วยกันในแพ็กเกจเดียวเสมอ คนที่ตั้ง `FFMPEG_BIN` เป็นพาธเต็ม (เครื่อง Windows ที่ไม่ได้
ใส่ ffmpeg ลง PATH เป็นเคสปกติ) จึงมี ffprobe อยู่ข้าง ๆ แน่ ๆ แต่ไม่มีบน PATH — เรียกแล้วไม่เจอ
`audio_duration` จับ OSError แล้วคืน 0.0 **เงียบ ๆ** ความยาวประชุมขึ้นเป็น 0 ทั้งที่ไฟล์ดีทุกอย่าง

**#33** เลข 75 (เพดาน heartbeat) เขียนซ้ำอยู่สามที่: `pgstore` (ตัวที่ SQL ใช้จริง), `server.py`
(ตัวที่ตอบกลับไปบอก worker) และคอมเมนต์ใน `worker.py` ส่วนเลข 3 (`--max-bots`) อยู่ทั้งใน
`worker.DEFAULT_MAX_BOTS` และ `cli.worker_default_bots()` แก้ที่เดียวไม่ครบ = เซิร์ฟเวอร์ตัดคนที่
ยังเต้นอยู่ทิ้ง หรือเก็บคนที่ตายแล้วไว้ และ CLI โฆษณาค่าเริ่มต้นที่ไม่ตรงกับที่ worker ใช้จริง

เทสต์กลุ่ม "ที่มาที่เดียว" ตรวจว่าค่าเท่ากัน **และ** ว่ามันผูกกันจริง (เปลี่ยนต้นทางแล้วปลายทาง
เปลี่ยนตาม) เพราะแค่ `assertEqual(75, 75)` ผ่านได้ทั้งที่ยังก๊อปกันอยู่
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

from meeting_ai import cli, config as config_mod, runner, worker
from meeting_ai.config import Config, config
from meeting_ai.web import pgstore, server

SRC = Path(config_mod.__file__).parent


class TestFfprobeFollowsFfmpeg(unittest.TestCase):

    def _probe(self, ffmpeg: str, override: str = "") -> str:
        class C(Config):
            ffmpeg_bin = ffmpeg
            ffprobe_bin_raw = override

        return C.ffprobe_bin()

    def test_a_bare_name_stays_a_bare_name(self):
        # ค่าเริ่มต้นต้องไม่เปลี่ยนพฤติกรรมของเครื่องที่มี ffmpeg บน PATH อยู่แล้ว
        self.assertEqual(self._probe("ffmpeg"), "ffprobe")

    def test_a_full_windows_path_keeps_its_folder_and_extension(self):
        got = self._probe("C:/tools/ffmpeg/bin/ffmpeg.exe")
        self.assertTrue(got.endswith("ffprobe.exe"), got)
        self.assertIn("tools", got)
        self.assertNotIn("ffmpeg.exe", got)

    def test_a_full_posix_path_keeps_its_folder(self):
        got = self._probe("/opt/ffmpeg/bin/ffmpeg")
        self.assertTrue(got.replace("\\", "/").endswith("/opt/ffmpeg/bin/ffprobe"), got)

    def test_a_binary_not_named_ffmpeg_still_gets_a_sibling_ffprobe(self):
        got = self._probe("/usr/local/bin/avconv")
        self.assertTrue(got.replace("\\", "/").endswith("/usr/local/bin/ffprobe"), got)

    def test_an_explicit_override_wins(self):
        self.assertEqual(self._probe("C:/a/ffmpeg.exe", override="D:/other/probe.exe"),
                         "D:/other/probe.exe")

    def test_audio_duration_calls_the_derived_binary(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            return mock.Mock(returncode=1, stdout="")

        with mock.patch.object(config, "ffprobe_bin", lambda: "C:/tools/ff/ffprobe.exe"), \
             mock.patch.object(runner.subprocess, "run", fake_run):
            runner.audio_duration(Path("x.wav"))
        self.assertEqual(seen["cmd"][0], "C:/tools/ff/ffprobe.exe")

    def test_no_bare_ffprobe_string_is_left_in_the_package(self):
        for path in SRC.rglob("*.py"):
            with self.subTest(file=path.name):
                for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                    if isinstance(node, ast.Constant) and node.value == "ffprobe":
                        # อนุญาตเฉพาะในตัว config เองที่เป็นคนคำนวณชื่อ
                        self.assertEqual(path.name, "config.py",
                                         f"{path.name}:{node.lineno} เรียก ffprobe ลอย ๆ")


class TestOneSourceForWorkerTiming(unittest.TestCase):

    def test_the_values_agree_today(self):
        self.assertEqual(pgstore.WORKER_STALE_SECONDS, config_mod.WORKER_STALE_SECONDS)
        self.assertEqual(server.WORKER_STALE_SECONDS, config_mod.WORKER_STALE_SECONDS)
        self.assertEqual(worker.HEARTBEAT_SEC, config_mod.WORKER_HEARTBEAT_SECONDS)

    def test_the_stale_ceiling_is_not_written_twice(self):
        # แค่ "ค่าเท่ากัน" ไม่พอ — ต้องเป็นค่าเดียวกันจริง ๆ ไม่ใช่เลขที่บังเอิญตรง
        for module, name in ((pgstore, "pgstore"), (server, "server")):
            with self.subTest(module=name):
                src = Path(module.__file__).read_text(encoding="utf-8")
                literals = [n.lineno for n in ast.walk(ast.parse(src))
                            if isinstance(n, ast.Constant) and n.value == 75]
                self.assertEqual(literals, [], f"{name}.py ยังเขียนเลข 75 ไว้เอง: {literals}")

    def test_the_stale_ceiling_outlasts_several_missed_beats(self):
        # ความสัมพันธ์ที่ทำให้สองค่านี้ต้องอยู่ด้วยกัน: ตัดคนที่พลาดไปแค่จังหวะเดียวไม่ได้
        self.assertGreaterEqual(config_mod.WORKER_STALE_SECONDS,
                                config_mod.WORKER_HEARTBEAT_SECONDS * 3)

    def test_the_heartbeat_reply_carries_the_shared_value(self):
        src = Path(server.__file__).read_text(encoding="utf-8")
        self.assertIn('"stale_after": WORKER_STALE_SECONDS', src)


class TestOneSourceForMaxBots(unittest.TestCase):

    def test_the_cli_default_matches_what_the_worker_uses(self):
        self.assertEqual(cli.worker_default_bots(), worker.DEFAULT_MAX_BOTS)

    def test_both_read_it_from_config(self):
        self.assertEqual(worker.DEFAULT_MAX_BOTS, config_mod.DEFAULT_MAX_BOTS)
        self.assertEqual(cli.worker_default_bots(), config_mod.DEFAULT_MAX_BOTS)

    def test_the_cli_really_follows_config_not_a_copy(self):
        # เปลี่ยนต้นทางแล้วปลายทางต้องเปลี่ยนตาม ไม่งั้นก็แค่เลขที่บังเอิญตรงกันวันนี้
        with mock.patch.object(config_mod, "DEFAULT_MAX_BOTS", 9):
            self.assertEqual(cli.worker_default_bots(), 9)

    def test_the_parser_advertises_that_same_default(self):
        args = cli.build_parser().parse_args(["worker"])
        self.assertEqual(args.max_bots, config_mod.DEFAULT_MAX_BOTS)


if __name__ == "__main__":
    unittest.main()
