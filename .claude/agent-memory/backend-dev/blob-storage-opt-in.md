---
name: blob-storage-opt-in
description: How blob storage (LocalStorage vs S3Storage) is selected after BUG-045, and the traps when testing it locally
metadata:
  type: project
---

Since BUG-045 (fixed 2026-09-16) `blobstore.get_storage(local_root, allow_remote=False)` picks `S3Storage`
only when `S3_*` is complete **and** (`allow_remote` — passed as `backend.cloud` — or `MEETING_AI_REMOTE_BLOBS=1`).
Every selection announces itself on **stderr**; no selection is silent except "no `S3_*` at all".

**Why:** the dev `.env` holds production R2 credentials, so a plain `./mai web --port 5543X` in file mode
handed out a presigned PUT URL for the production bucket and the R2 keys had to be rotated. Blob storage stays
deliberately independent of the DB backend (cloud DB + local disk, and file mode + own bucket are both valid);
the defect was implicit activation, not the capability.

**How to apply:**
- `backend.storage()` reads the module global `cloud` at call time → tests can patch `backend.cloud` (unlike
  `jobs.py`, which freezes `cloud`/`store` at import — see [[MEMORY]] cloud-mode recipe).
- `get_storage` caches in `_current`, so the notice prints once per process; `blobstore.reset()` re-arms it.
  Never work around the cache in tests.
- Entry points that must keep S3: `api/index.py` and `cli._cmd_web(--cloud)` both set `MEETING_AI_CLOUD`
  **before** importing `backend`; check that ordering before changing anything about mode detection.
- Testing anything S3: export your own fake `S3_*` (e.g. endpoint under `.invalid`) — `config._load_dotenv`
  uses `setdefault`, so an explicit env var wins over `.env`, and `S3_BUCKET=""` reliably simulates "no S3".
- `serve()` prints its banner to stdout (block-buffered when redirected) while notices go to stderr: flush
  stdout before anything that writes to stderr or the log ordering looks wrong.

**Review lesson (round 1, 2026-09-16):** killing an implicit activation creates a *second* silent path —
"opt-in requested but the config is incomplete" (typo'd `S3_ENDPOINT`) fell back to disk with no output at all.
Whenever a feature becomes opt-in, enumerate all four states (off/on x complete/incomplete) and give each one
a line. `missing_pieces()` already returns the names to print. Same for notices that can now run on a request
thread: they must never raise (cp874 console + Thai text) — encode-fallback to ASCII and swallow the rest.
