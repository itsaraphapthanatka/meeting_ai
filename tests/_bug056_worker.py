"""Helper process for BUG-056 (file store lost update) regression tests.

Spawned via `subprocess.Popen([sys.executable, __file__, ...])` by
tests/test_bug_056_file_store_concurrency.py — never imported directly, and never
run by unittest discovery (filename does not start with test_).

Why a real child process instead of a thread: threading.RLock in store.py only ever
guarded threads inside one process. BUG-056 is specifically about two *processes*
racing, so the regression test must use real processes or it proves nothing (see
docs/tickets/BUG-056-file-store-lost-update.md).

Talks to a real meeting_ai.web.store pointed at an isolated web_dir, patched after
import (same trick tests/_harness.py uses for LocalCase — WEB_DIR/INDEX_PATH/
SETTINGS_PATH are computed once at import time, so all three must be reassigned).
Writes its result as JSON to --results-dir instead of stdout, so the parent test
can read it back without racing interleaved output from several children.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# repo root on sys.path — this script runs as a plain file, not as part of the package
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os  # noqa: E402

os.environ.setdefault("MEETING_AI_CLOUD", "0")
os.environ.setdefault("DATABASE_URL", "")

from meeting_ai.web import store as filestore  # noqa: E402


def _wait_barrier(barrier: Path | None) -> None:
    if barrier is None:
        return
    while not barrier.exists():
        time.sleep(0.001)


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1", encoding="utf-8")


def op_create(count: int, worker_id: str) -> list[dict]:
    out = []
    for i in range(count):
        mid = filestore.new_id()
        try:
            filestore.create(
                mid, f"w{worker_id}-{i}", "a.wav", "upload", "th", 1.0,
                [], f"summary {worker_id}-{i}",
            )
            out.append({"mid": mid, "ok": True, "error": None})
        except Exception as exc:  # noqa: BLE001 - ต้องจับทุกอย่างที่หลุดมาจริง ไม่ใช่แค่ที่คาดไว้
            out.append({"mid": mid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return out


def op_translate(count: int, worker_id: str, mid: str) -> list[dict]:
    out = []
    for i in range(count):
        key = f"w{worker_id}_{i}"
        try:
            result = filestore.set_translation(mid, key, f"text-{key}")
            out.append({"key": key, "ok": result is not None, "error": None})
        except Exception as exc:  # noqa: BLE001
            out.append({"key": key, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return out


def op_reader(duration: float, mid: str) -> dict:
    errors: list[str] = []
    reads = 0
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            filestore.get(mid)
            filestore.search("")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
        reads += 1
    return {"reads": reads, "errors": errors}


def op_hold_lock(duration: float, locked_marker: Path) -> None:
    with filestore._guard():
        _touch(locked_marker)          # สัญญาณว่าล็อกไฟล์ถืออยู่จริงแล้ว — ให้พ่อฆ่า/รอได้
        time.sleep(duration)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("web_dir")
    p.add_argument("op", choices=["create", "translate", "reader", "hold_lock"])
    p.add_argument("worker_id")
    p.add_argument("--results-dir")
    p.add_argument("--barrier")
    p.add_argument("--ready-dir")
    p.add_argument("--locked-marker")
    p.add_argument("--count", type=int, default=10)
    p.add_argument("--duration", type=float, default=1.0)
    p.add_argument("--mid", default="")
    args = p.parse_args()

    web_dir = Path(args.web_dir)
    # ต้องแพตช์ทั้งสามตัว — INDEX_PATH/SETTINGS_PATH คำนวณตอน import ไม่ใช่ property (ดู _harness.py)
    filestore.WEB_DIR = web_dir
    filestore.INDEX_PATH = web_dir / "index.json"
    filestore.SETTINGS_PATH = web_dir / "settings.json"

    if args.op == "hold_lock":
        op_hold_lock(args.duration, Path(args.locked_marker))
        result: object = {"done": True}
    else:
        ready_dir = Path(args.ready_dir) if args.ready_dir else None
        barrier = Path(args.barrier) if args.barrier else None
        if ready_dir is not None:
            _touch(ready_dir / f"{args.worker_id}.ready")
        _wait_barrier(barrier)

        if args.op == "create":
            result = op_create(args.count, args.worker_id)
        elif args.op == "translate":
            result = op_translate(args.count, args.worker_id, args.mid)
        else:
            result = op_reader(args.duration, args.mid)

    if args.results_dir:
        out_dir = Path(args.results_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{args.worker_id}.json").write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
