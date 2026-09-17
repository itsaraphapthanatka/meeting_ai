"""BUG-056 — cross-process lost update in the file store (docs/tickets/BUG-056-file-store-lost-update.md).

Root cause: store.py guarded read-modify-write with threading.RLock only, which is
process-local. Two real OS processes (e.g. `mai web` left open + `mai process` in
another terminal) both read index.json, both append, both write back — the later
writer wins and the earlier one's record vanishes from disk even though the store
already told the caller it was saved. The fix adds a cross-process file lock
(`store._guard()` = RLock + msvcrt/fcntl file lock) around every write path, clears
the mtime-keyed detail cache on lock entry/exit, and retries `os.replace` on Windows
sharing violations caused by a concurrent reader.

Why real subprocesses and not threads: threading.RLock already handled the
same-process case before this bug; only separate processes reproduce it, so a test
using threads would be vacuous. tests/_bug056_worker.py is spawned via
subprocess.Popen and talks to a real meeting_ai.web.store pointed at an isolated
web_dir. All workers block on a file-based barrier so they start together instead of
serializing on process-launch order.

Non-vacuousness was checked by hand while writing this file (temporary edit, not
committed): patching `store._guard` to a null contextmanager made 6 of the 7 tests
below fail with real missing records / wrong warning behaviour (only the
dead-lock-holder test still passed, which is expected — it does not depend on the
lock actually doing anything). Separately, reverting `_try_lock` to raise like the
original `msvcrt.LOCK_NBLCK` typo made test_bug_056_file_store_basic fail loudly
instead of silently passing.

Windows-only note: the `os.replace` PermissionError this ticket also fixed is a
Windows sharing-violation behaviour (Python's `open()` does not request
FILE_SHARE_DELETE, so a reader holding the destination file open makes a
concurrent `os.replace` fail). test_bug_056_writer_and_reader_concurrent_no_lost_writes
exercises exactly that interleaving; on POSIX the same assertions hold (rename over
an open file is allowed there) so the test is not skipped, but it is Windows where
it actually catches a regression of that specific defect.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from _harness import filestore, new_mid

WORKER = Path(__file__).resolve().parent / "_bug056_worker.py"


class _SubprocessCaseMixin:
    """เครื่องมือรวมสำหรับสปอว์นโพรเซสลูกจริงพร้อม barrier แบบไฟล์."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="mai-bug056-conc-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        self.web_dir = Path(self._tmp) / "web"
        self.coord_dir = Path(self._tmp) / "coord"
        self.web_dir.mkdir(parents=True, exist_ok=True)
        self.coord_dir.mkdir(parents=True, exist_ok=True)
        super().setUp()  # type: ignore[misc]

    def _spawn(self, op: str, worker_id: str, *, count=None, duration=None, mid=None,
               ready_dir=None, barrier=None, results_dir=None, locked_marker=None):
        args = [sys.executable, str(WORKER), str(self.web_dir), op, worker_id]
        if results_dir is not None:
            args += ["--results-dir", str(results_dir)]
        if ready_dir is not None:
            args += ["--ready-dir", str(ready_dir)]
        if barrier is not None:
            args += ["--barrier", str(barrier)]
        if locked_marker is not None:
            args += ["--locked-marker", str(locked_marker)]
        if count is not None:
            args += ["--count", str(count)]
        if duration is not None:
            args += ["--duration", str(duration)]
        if mid is not None:
            args += ["--mid", mid]
        return subprocess.Popen(args, env=os.environ.copy())

    def _release_barrier(self, ready_dir: Path, barrier: Path, n: int, timeout: float = 15.0) -> None:
        """รอให้ลูกทุกตัว 'พร้อม' (import + patch เสร็จ กำลังรอ barrier) แล้วปล่อยพร้อมกัน.

        เพื่อให้ระยะเวลาที่แข่งกันจริงกว้างที่สุด — ถ้าปล่อยทันทีที่ spawn แต่ละลูกจะ
        เริ่มงานตามลำดับที่ OS สร้างโพรเซสเสร็จ ไม่ใช่พร้อมกันจริง
        """
        deadline = time.monotonic() + timeout
        while len(list(ready_dir.glob("*.ready"))) < n:
            if time.monotonic() > deadline:
                self.fail(f"only {len(list(ready_dir.glob('*.ready')))}/{n} workers "
                          f"became ready within {timeout}s")
            time.sleep(0.005)
        barrier.write_text("go", encoding="utf-8")

    def _wait_all(self, procs, timeout: float = 30.0) -> None:
        for p in procs:
            rc = p.wait(timeout=timeout)
            self.assertEqual(rc, 0, f"worker process exited with code {rc} (see its stderr above)")

    def _wait_for_file(self, path: Path, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while not path.exists():
            if time.monotonic() > deadline:
                self.fail(f"{path} never appeared within {timeout}s")
            time.sleep(0.005)

    def _load_results(self, results_dir: Path, worker_ids: list[str]) -> list:
        out = []
        for wid in worker_ids:
            path = results_dir / f"{wid}.json"
            self.assertTrue(path.exists(), f"missing result file for worker {wid}")
            out.append(json.loads(path.read_text(encoding="utf-8")))
        return out


@unittest.skipUnless(filestore._LOCK_KIND, "no cross-process lock mechanism (msvcrt/fcntl) on this platform")
class ConcurrentWritersTests(_SubprocessCaseMixin, unittest.TestCase):
    def test_bug_056_concurrent_creates_across_processes_lose_nothing(self) -> None:
        n_workers, count = 4, 10
        ready_dir, barrier = self.coord_dir / "ready", self.coord_dir / "barrier"
        results_dir = self.coord_dir / "results"
        ready_dir.mkdir()

        procs = [
            self._spawn("create", f"c{i}", count=count,
                        ready_dir=ready_dir, barrier=barrier, results_dir=results_dir)
            for i in range(n_workers)
        ]
        self.addCleanup(lambda: [p.kill() for p in procs if p.poll() is None])
        self._release_barrier(ready_dir, barrier, n_workers)
        self._wait_all(procs)

        results = self._load_results(results_dir, [f"c{i}" for i in range(n_workers)])
        all_mids = []
        for worker_result in results:
            for item in worker_result:
                self.assertTrue(item["ok"], item.get("error"))
                all_mids.append(item["mid"])
        self.assertEqual(len(all_mids), n_workers * count)
        self.assertEqual(len(set(all_mids)), len(all_mids), "duplicate ids — new_id() collided")

        index = filestore._read_json(self.web_dir / "index.json", {})
        ids_on_disk = {m["id"] for m in index.get("meetings", [])}
        lost = set(all_mids) - ids_on_disk
        self.assertEqual(lost, set(), f"lost {len(lost)}/{len(all_mids)} index records to a race")
        self.assertEqual(len(index.get("meetings", [])), n_workers * count)

    def test_bug_056_concurrent_set_translation_across_processes_lose_nothing(self) -> None:
        mid = new_mid()
        with mock.patch.object(filestore, "WEB_DIR", self.web_dir), \
             mock.patch.object(filestore, "INDEX_PATH", self.web_dir / "index.json"), \
             mock.patch.object(filestore, "SETTINGS_PATH", self.web_dir / "settings.json"):
            filestore.create(mid, "shared meeting", "a.wav", "upload", "th", 1.0, [], "s")

        n_workers, count = 4, 10
        ready_dir, barrier = self.coord_dir / "ready", self.coord_dir / "barrier"
        results_dir = self.coord_dir / "results"
        ready_dir.mkdir()

        procs = [
            self._spawn("translate", f"t{i}", count=count, mid=mid,
                        ready_dir=ready_dir, barrier=barrier, results_dir=results_dir)
            for i in range(n_workers)
        ]
        self.addCleanup(lambda: [p.kill() for p in procs if p.poll() is None])
        self._release_barrier(ready_dir, barrier, n_workers)
        self._wait_all(procs)

        results = self._load_results(results_dir, [f"t{i}" for i in range(n_workers)])
        all_keys = []
        for worker_result in results:
            for item in worker_result:
                self.assertTrue(item["ok"], item.get("error"))
                all_keys.append(item["key"])
        self.assertEqual(len(all_keys), n_workers * count)

        detail = filestore._read_json(self.web_dir / f"{mid}.json", {})
        translations = detail.get("translations", {})
        lost = set(all_keys) - set(translations.keys())
        self.assertEqual(lost, set(), f"lost {len(lost)}/{len(all_keys)} translations to a race")
        self.assertEqual(len(translations), len(all_keys))

    def test_bug_056_writer_and_reader_concurrent_no_lost_writes(self) -> None:
        """โพรเซสอ่านพร้อมโพรเซสเขียน — เคสที่จับ os.replace PermissionError บน Windows ได้.

        เลียนแบบหน้าเว็บที่เปิดค้างไว้ (poll ทุก 1.5 วิ) ชนกับ `mai process`/`mai bot`
        ที่เพิ่งเขียนเสร็จ ก่อนแก้: ผู้อ่านทำให้ os.replace ของผู้เขียนพัง ~83% บน Windows
        (วัดจริงในทิกเก็ต) หลังแก้ ต้องไม่มี record หาย และไม่มี exception หลุดถึงผู้อ่าน
        """
        mid = new_mid()
        with mock.patch.object(filestore, "WEB_DIR", self.web_dir), \
             mock.patch.object(filestore, "INDEX_PATH", self.web_dir / "index.json"), \
             mock.patch.object(filestore, "SETTINGS_PATH", self.web_dir / "settings.json"):
            filestore.create(mid, "poll target", "a.wav", "upload", "th", 1.0, [], "s")

        n_writers, writes_each, n_readers, read_duration = 2, 20, 2, 1.0
        ready_dir, barrier = self.coord_dir / "ready", self.coord_dir / "barrier"
        results_dir = self.coord_dir / "results"
        ready_dir.mkdir()

        writer_ids = [f"w{i}" for i in range(n_writers)]
        reader_ids = [f"r{i}" for i in range(n_readers)]
        procs = [
            self._spawn("create", wid, count=writes_each,
                        ready_dir=ready_dir, barrier=barrier, results_dir=results_dir)
            for wid in writer_ids
        ] + [
            self._spawn("reader", rid, duration=read_duration, mid=mid,
                        ready_dir=ready_dir, barrier=barrier, results_dir=results_dir)
            for rid in reader_ids
        ]
        self.addCleanup(lambda: [p.kill() for p in procs if p.poll() is None])
        self._release_barrier(ready_dir, barrier, n_writers + n_readers)
        self._wait_all(procs)

        writer_results = self._load_results(results_dir, writer_ids)
        all_mids = []
        for wr in writer_results:
            for item in wr:
                self.assertTrue(item["ok"], item.get("error"))
                all_mids.append(item["mid"])
        self.assertEqual(len(all_mids), n_writers * writes_each)

        index = filestore._read_json(self.web_dir / "index.json", {})
        ids_on_disk = {m["id"] for m in index.get("meetings", [])}
        lost = set(all_mids) - ids_on_disk
        self.assertEqual(lost, set(), f"lost {len(lost)}/{len(all_mids)} writes while a reader was active")

        reader_results = self._load_results(results_dir, reader_ids)
        for rid, rr in zip(reader_ids, reader_results):
            self.assertEqual(rr["errors"], [],
                              f"reader {rid} saw exception(s) escape the store "
                              "(likely the os.replace sharing-violation regression)")
            self.assertGreater(rr["reads"], 0, f"reader {rid} never got to run")


class ThreadAndNestingTests(unittest.TestCase):
    """เธรดหลายตัวในโพรเซสเดียว + การเรียก _guard() ซ้อนกัน — ต้องไม่ deadlock."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="mai-bug056-threads-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))
        web_dir = Path(self._tmp)
        patches = [
            mock.patch.object(filestore, "WEB_DIR", web_dir),
            mock.patch.object(filestore, "INDEX_PATH", web_dir / "index.json"),
            mock.patch.object(filestore, "SETTINGS_PATH", web_dir / "settings.json"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_bug_056_threads_in_one_process_no_lost_writes_no_deadlock(self) -> None:
        n_threads, per_thread = 5, 10
        results: dict[int, list[str]] = {}
        results_lock = threading.Lock()

        def worker(i: int) -> None:
            local = []
            for j in range(per_thread):
                mid = filestore.new_id()
                filestore.create(mid, f"t{i}-{j}", "a.wav", "upload", "th", 1.0, [], "s")
                local.append(mid)
            with results_lock:
                results[i] = local

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        for t in threads:
            self.assertFalse(t.is_alive(), "a thread is still running — possible deadlock "
                              "between threading.RLock and the file lock")

        all_mids = [mid for ids in results.values() for mid in ids]
        self.assertEqual(len(all_mids), n_threads * per_thread)
        index = filestore._read_json(filestore.INDEX_PATH, {})
        ids_on_disk = {m["id"] for m in index.get("meetings", [])}
        self.assertEqual(set(all_mids) - ids_on_disk, set())

    def test_bug_056_nested_guard_does_not_deadlock(self) -> None:
        depths = []
        with filestore._guard():
            depths.append(filestore._lock_depth)
            with filestore._guard():
                depths.append(filestore._lock_depth)
            depths.append(filestore._lock_depth)
        self.assertEqual(depths, [1, 2, 1])
        self.assertEqual(filestore._lock_depth, 0)

        # ต้องยังเขียนได้จริงหลังซ้อนล็อก ไม่ใช่แค่ตัวนับไม่พัง
        mid = new_mid()
        filestore.create(mid, "t", "a.wav", "upload", "th", 1.0, [], "s")
        self.assertIsNotNone(filestore.get(mid))


@unittest.skipUnless(filestore._LOCK_KIND, "no cross-process lock mechanism (msvcrt/fcntl) on this platform")
class LockTimeoutAndCrashTests(_SubprocessCaseMixin, unittest.TestCase):
    """เจ้าของล็อกตาย และเส้นทาง timeout — ทั้งสองต้องไม่ทำให้ store ค้างหรือทิ้งงาน."""

    def setUp(self) -> None:
        super().setUp()
        self._patches = [
            mock.patch.object(filestore, "WEB_DIR", self.web_dir),
            mock.patch.object(filestore, "INDEX_PATH", self.web_dir / "index.json"),
            mock.patch.object(filestore, "SETTINGS_PATH", self.web_dir / "settings.json"),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def test_bug_056_lock_holder_dies_no_stale_lock(self) -> None:
        locked_marker = self.coord_dir / "locked.marker"
        proc = self._spawn("hold_lock", "holder", duration=30.0, locked_marker=locked_marker)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())

        self._wait_for_file(locked_marker)   # ยืนยันว่าลูกถือ OS lock อยู่จริงแล้ว ไม่ใช่แค่ spawn เสร็จ
        proc.kill()                          # SIGKILL/TerminateProcess — ไม่มีโอกาสรัน finally ของ _file_lock
        proc.wait(timeout=10)                # รอให้ OS เก็บกวาด handle ให้เสร็จก่อน ไม่ใช่แค่สั่งฆ่า

        with mock.patch.object(filestore, "_warn") as warn:
            mid = new_mid()
            meta = filestore.create(mid, "after-crash", "a.wav", "upload", "th", 1.0, [], "s")

        # ถ้า OS ไม่ปล่อยล็อกให้จริง create() จะรอจน LOCK_TIMEOUT แล้วเตือนก่อนเขียน — ไม่ควรเกิด
        warn.assert_not_called()
        self.assertEqual(meta["id"], mid)
        self.assertIsNotNone(filestore.get(mid))

    def test_bug_056_lock_busy_timeout_warns_and_still_saves(self) -> None:
        locked_marker = self.coord_dir / "locked2.marker"
        proc = self._spawn("hold_lock", "holder2", duration=2.0, locked_marker=locked_marker)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        self._wait_for_file(locked_marker)

        with mock.patch.object(filestore, "LOCK_TIMEOUT", 0.2), \
             mock.patch.object(filestore, "_warn") as warn:
            mid = new_mid()
            meta = filestore.create(mid, "timeout-path", "a.wav", "upload", "th", 1.0, [], "s")

        self.assertEqual(meta["id"], mid)
        self.assertIsNotNone(filestore.get(mid), "write must still happen even without the lock")
        self.assertTrue(warn.called, "expected a warning once the lock could not be taken in time")
        msg = warn.call_args[0][0]
        self.assertIn("writing without it", msg)

        proc.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
