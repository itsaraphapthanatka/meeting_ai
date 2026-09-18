"""BACKLOG #4 — บอทหลายตัวพร้อมกันเคย mount recordings/bot/ ทั้งโฟลเดอร์เป็น /out ร่วมกัน:
สถานะ (bot_status.txt) ของห้องหนึ่งไปทับของอีกงาน และงานที่จบก่อนลบภาพหน้าจอของงานที่ยัง
อยู่ในห้อง (prod รัน --max-bots 6 พร้อมกันจริง)

หลังแก้: bot._job_slot(job_id, worker) คืนโฟลเดอร์ย่อยเฉพาะของงานนั้น (ติด worker tag ด้วย
กัน worker คนละตัวบนเครื่องเดียวกันไปยุ่งกับโฟลเดอร์ของกันเอง) และ bot._prune_stages(worker)
เก็บกวาดเฉพาะโฟลเดอร์ของ worker ตัวเอง โดยรักษาไฟล์ wav ที่มีข้อมูลจริงไว้ให้คนตัดสินใจ
"""

import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _harness import backend  # noqa: F401  ทุกไฟล์เทสต์ import _harness ก่อนเสมอ

from meeting_ai import bot

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class TestBotStagingPerJob(unittest.TestCase):
    def setUp(self) -> None:
        self.stage_dir = Path(tempfile.mkdtemp(prefix="mai-stage-"))
        self.debug_dir = Path(tempfile.mkdtemp(prefix="mai-debug-"))
        self.addCleanup(shutil.rmtree, self.stage_dir, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.debug_dir, ignore_errors=True)

        patches = [
            mock.patch.object(bot, "STAGE_DIR", self.stage_dir),
            mock.patch.object(bot, "DEBUG_DIR", self.debug_dir),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_import_needs_no_docker(self) -> None:
        # การ import "from meeting_ai import bot" ที่หัวไฟล์นี้ไม่แตะ docker เลย —
        # ยืนยันอีกครั้งว่าฟังก์ชันล้วน (pure) ที่จะทดสอบต่อไปนี้เรียกได้โดยไม่ต้องมี Docker Desktop
        self.assertTrue(hasattr(bot, "PREFIX"))
        self.assertTrue(hasattr(bot, "STAGE_DIR"))
        self.assertTrue(hasattr(bot, "SHOTS"))

    def test_job_slot_distinct_dirs_and_valid_names(self) -> None:
        name1, path1 = bot._job_slot("jobA", "workerX")
        name2, path2 = bot._job_slot("jobB", "workerX")

        self.assertNotEqual(path1, path2)
        self.assertNotEqual(name1, name2)
        self.assertRegex(path1.name, _NAME_RE.pattern)
        self.assertRegex(path2.name, _NAME_RE.pattern)
        self.assertRegex(name1, _NAME_RE.pattern)
        self.assertRegex(name2, _NAME_RE.pattern)
        self.assertTrue(str(path1).startswith(str(self.stage_dir)))
        self.assertTrue(str(path2).startswith(str(self.stage_dir)))

    def test_job_slot_container_name_is_prefix_plus_dir_name(self) -> None:
        name, path = bot._job_slot("jobA", "workerX")
        self.assertEqual(name, bot.PREFIX + path.name)

    def test_job_slot_idempotent_for_same_job_and_worker(self) -> None:
        name1, path1 = bot._job_slot("jobA", "workerX")
        name2, path2 = bot._job_slot("jobA", "workerX")
        self.assertEqual(name1, name2)
        self.assertEqual(path1, path2)

    def test_job_slot_differs_by_worker(self) -> None:
        _, path1 = bot._job_slot("jobA", "workerX")
        _, path2 = bot._job_slot("jobA", "workerY")
        self.assertNotEqual(path1, path2)

    def test_read_status_reads_own_dir_only(self) -> None:
        _, dir_a = bot._job_slot("jobA", "w")
        _, dir_b = bot._job_slot("jobB", "w")
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        (dir_a / bot.STATUS_NAME).write_text("inroom", encoding="utf-8")
        (dir_b / bot.STATUS_NAME).write_text("waiting", encoding="utf-8")

        self.assertEqual(bot._read_status(dir_a), "inroom")
        self.assertEqual(bot._read_status(dir_b), "waiting")

    def test_read_status_missing_file_is_blank(self) -> None:
        _, dir_a = bot._job_slot("jobA", "w")
        dir_a.mkdir(parents=True)
        self.assertEqual(bot._read_status(dir_a), "")

    def test_keep_debug_shot_touches_only_its_own_dir(self) -> None:
        _, dir_a = bot._job_slot("jobA", "w")
        _, dir_b = bot._job_slot("jobB", "w")
        dir_a.mkdir(parents=True)
        dir_b.mkdir(parents=True)
        (dir_a / "bot_debug.png").write_bytes(b"shot-a")
        (dir_b / "bot_debug.png").write_bytes(b"shot-b")

        kept = bot._keep_debug_shot(dir_a, "jobA")

        expected = self.debug_dir / "bot_debug_jobA.png"
        self.assertEqual(kept, expected)
        self.assertTrue(expected.exists())
        self.assertEqual(expected.read_bytes(), b"shot-a")
        # ต้นฉบับใน dir_a ถูกลบไปแล้ว
        self.assertFalse((dir_a / "bot_debug.png").exists())
        # dir_b ไม่ถูกแตะเลย
        self.assertTrue((dir_b / "bot_debug.png").exists())
        self.assertEqual((dir_b / "bot_debug.png").read_bytes(), b"shot-b")

    # ชื่อโฟลเดอร์พักคือ "<worker tag>_<job id>" ไม่ใช่ "<ชื่อเครื่อง>_<job id>" — ตั้งแต่
    # BACKLOG #38 worker_tag() ต่อ hash ของชื่อเต็มไว้เสมอ เทสต์จึงต้องถาม worker_tag()
    # ไม่ใช่เดาเอาว่าเท่ากับชื่อเครื่อง ไม่งั้น glob ไม่เจออะไรแล้ว "ไม่ลบอะไรเลย" จะกลายเป็น
    # ผลที่ผ่านทั้งที่ไม่ได้ทดสอบอะไร (เทสต์ live/ไม่ลบ ด้านล่างเป็นแบบนั้นได้ง่ายที่สุด)
    def _stage(self, worker: str, job: str) -> Path:
        d = self.stage_dir / f"{bot.worker_tag(worker)}_{job}"
        d.mkdir(parents=True)
        return d

    def test_prune_stages_removes_only_own_worker_screenshot_only_dirs(self) -> None:
        gb10_j1 = self._stage("gb10", "j1")       # ของ worker gb10 มีแต่ภาพ -> ลบ
        other_j2 = self._stage("other", "j2")     # worker อื่น -> ห้ามแตะ
        gb10_j3 = self._stage("gb10", "j3")       # ของ worker gb10 แต่มี wav จริง -> เก็บไว้
        (gb10_j1 / "bot_debug.png").write_bytes(b"shot")
        (other_j2 / "bot_debug.png").write_bytes(b"other-shot")
        (gb10_j3 / "audio.wav").write_bytes(b"x" * 10)
        loose = self.stage_dir / "legacy.wav"     # ไฟล์หลวมนอกโฟลเดอร์ย่อย -> ห้ามแตะ
        loose.write_bytes(b"legacy")

        removed = bot._prune_stages("gb10")

        self.assertEqual({p.name for p in removed}, {gb10_j1.name})
        self.assertFalse(gb10_j1.exists())
        self.assertTrue(other_j2.exists())
        self.assertTrue((other_j2 / "bot_debug.png").exists())
        self.assertTrue(gb10_j3.exists())
        self.assertTrue((gb10_j3 / "audio.wav").exists())
        self.assertTrue(loose.exists())
        # _prune_stages ตัด worker tag ออกจากชื่อไฟล์ที่กู้มา (d.name.split("_", 1)[-1])
        # ให้ตรงกับรูปแบบเดียวกับทางปกติ: logs/bot_debug_<job id>.png (ไม่ใช่ <worker>_<job id>)
        self.assertTrue((self.debug_dir / "bot_debug_j1.png").exists())

    def test_prune_stages_without_worker_name_deletes_nothing(self) -> None:
        d = self._stage("gb10", "j1")
        (d / "bot_debug.png").write_bytes(b"shot")

        removed = bot._prune_stages("")

        self.assertEqual(removed, [])
        self.assertTrue(d.exists())
        self.assertTrue((d / "bot_debug.png").exists())

    def test_prune_stages_skips_dirs_still_live(self) -> None:
        """live = ชื่อ container ที่ยังรันอยู่จริง — ห้ามแตะทั้งลบและเก็บภาพ (ffmpeg อาจกำลังเขียน)."""
        d = self._stage("gb10", "j1")
        (d / "bot_debug.png").write_bytes(b"shot")
        container_name = bot.PREFIX + d.name
        # กันเทสต์ผ่านแบบว่างเปล่า: ถ้าไม่มี live โฟลเดอร์นี้ต้องเข้าข่ายถูกลบจริง
        self.assertEqual({p.name for p in bot._prune_stages("gb10", live=frozenset())},
                         {d.name})
        d = self._stage("gb10", "j1")
        (d / "bot_debug.png").write_bytes(b"shot")
        (self.debug_dir / "bot_debug_j1.png").unlink()

        removed = bot._prune_stages("gb10", live=frozenset({container_name}))

        self.assertEqual(removed, [])
        self.assertTrue(d.exists())
        # ภาพต้องไม่ถูกย้าย/ลบเลย ไม่ใช่แค่โฟลเดอร์ไม่ถูกลบ
        self.assertTrue((d / "bot_debug.png").exists())
        self.assertFalse((self.debug_dir / "bot_debug_j1.png").exists())

    def test_prune_stages_default_live_behaves_as_before(self) -> None:
        """ไม่ส่ง live มา (ค่าเริ่มต้น frozenset ว่าง) ต้องลบเหมือนก่อนมีพารามิเตอร์นี้."""
        d = self._stage("gb10", "j1")

        removed = bot._prune_stages("gb10")

        self.assertEqual({p.name for p in removed}, {d.name})
        self.assertFalse(d.exists())

    # ---------- _stage_removable ----------

    def test_stage_removable_moved_true_ignores_wav_contents(self) -> None:
        stage = self.stage_dir / "s1"
        stage.mkdir(parents=True)
        (stage / "audio.wav").write_bytes(b"x" * 10)
        self.assertTrue(bot._stage_removable(stage, moved=True))

    def test_stage_removable_not_moved_nonempty_wav_is_false(self) -> None:
        # จุดวิกฤต: ย้ายไฟล์เสียงปลายทางไม่สำเร็จ (เช่น shutil.move พังกลางทาง) ต้องไม่ลบ
        # โฟลเดอร์พักทิ้ง ไม่งั้นเสียงประชุมทั้งชั่วโมงหายถาวรไม่มีที่กู้
        stage = self.stage_dir / "s2"
        stage.mkdir(parents=True)
        (stage / "audio.wav").write_bytes(b"x" * 10)
        self.assertFalse(bot._stage_removable(stage, moved=False))

    def test_stage_removable_not_moved_empty_wav_is_true(self) -> None:
        stage = self.stage_dir / "s3"
        stage.mkdir(parents=True)
        (stage / "audio.wav").write_bytes(b"")
        self.assertTrue(bot._stage_removable(stage, moved=False))

    def test_stage_removable_not_moved_no_wav_is_true(self) -> None:
        stage = self.stage_dir / "s4"
        stage.mkdir(parents=True)
        self.assertTrue(bot._stage_removable(stage, moved=False))

    def test_stage_removable_missing_dir_is_true(self) -> None:
        stage = self.stage_dir / "does-not-exist"
        self.assertTrue(bot._stage_removable(stage, moved=False))

    # ---------- _keep_debug_shot: กัน path traversal / job_id ประหลาด ----------

    def test_keep_debug_shot_sanitizes_path_traversal_job_id(self) -> None:
        _, dir_a = bot._job_slot("jobA", "w")
        dir_a.mkdir(parents=True)
        (dir_a / "bot_debug.png").write_bytes(b"shot")

        kept = bot._keep_debug_shot(dir_a, "../../evil")

        self.assertIsNotNone(kept)
        # ต้องอยู่ใต้ DEBUG_DIR เท่านั้น — job_id ที่มี '/'/'..' ต้องไม่ทำให้ไฟล์หลุดออกไปที่อื่น
        self.assertEqual(kept.parent.resolve(), self.debug_dir.resolve())
        self.assertTrue(kept.is_file())

    def test_keep_debug_shot_blank_job_id_falls_back_to_timestamp(self) -> None:
        _, dir_a = bot._job_slot("jobA", "w")
        dir_a.mkdir(parents=True)
        (dir_a / "bot_debug.png").write_bytes(b"shot")

        kept = bot._keep_debug_shot(dir_a, "///")  # sanitize เหลือ "" ทั้งหมด

        self.assertIsNotNone(kept)
        self.assertEqual(kept.parent.resolve(), self.debug_dir.resolve())
        tag = kept.stem.split("bot_debug_", 1)[-1]
        self.assertTrue(tag.isdigit(), f"คาดว่าเป็น timestamp ตัวเลขล้วน ได้ {tag!r}")


if __name__ == "__main__":
    unittest.main()
