---
name: file-store-mtime-cache
description: mtime is never a safe cache key on this machine (300 writes -> 26 distinct mtimes); cache only files that have been settled longer than the timestamp granularity
metadata:
  type: project
---

Any cache in `meeting_ai/web/store.py` (file mode) keyed on `st_mtime` is unsafe by
construction: measured on the owner's Windows 11 / NTFS box, **300 rapid `write + os.replace`
cycles produced only 26 distinct `st_mtime` values, gaps up to 15.8 ms** (the system clock
tick), and `time.time() - st_mtime` right after a write is `0.0`. FAT32/exFAT is 2 s.

**Why:** BUG-055 — `load_detail()` cached on mtime while five functions rewrote the detail
file and only `delete()` popped the cache. A write landing in the same tick as the cached read
left the old copy in place, so `set_translation`/`update`/`set_summary`/`set_segments` returned
pre-edit content ~15% of the time. It surfaced as an intermittent test failure (a different
`lang` each run) and as "I edited it and it did not save" for users. `st_mtime_ns` does not
help: the same tick gives the same nanoseconds.

**How to apply:** when you touch a filesystem cache here, (1) funnel every write through one
helper that invalidates, and (2) only store an entry when `time.time() - mtime >= G` where `G`
is the coarsest plausible timestamp granularity (2.0 s). That second rule is the one that also
covers the second process — the file store is explicitly not single-process (`mai web` and a
CLI run share `recordings/web/`), so pop-on-write alone leaves the cross-process hole open.
Proof recipe: patch `WEB_DIR` + `INDEX_PATH` + `SETTINGS_PATH`, loop 100 writes with a read-back
after each, and simulate the foreign writer with a direct `_write_json` (it cannot invalidate
our cache — exactly what another process looks like from here).

Still true as of 2026-09-17: `pgstore.py` has no cache at all (`load_detail()` = `get()` = one
DB read), so file-mode cache bugs never have a cloud twin. `_load_index()`/`_save_index()` have
no cache either — but they are read-modify-write under a `threading.RLock` with no file lock, so
two processes lose each other's index updates for real (reported, not fixed).
