---
name: blob-storage-env-leak
description: BUG-045 blobstore tests must fully override S3_* env vars, not rely on _harness's partial blanking
metadata:
  type: project
---

`tests/_harness.py` only sets `S3_BUCKET=""` before importing `meeting_ai.*` (to keep `blobstore.configured()`
false for the existing P0 suite). It does **not** blank `S3_ENDPOINT` / `S3_ACCESS_KEY_ID` /
`S3_SECRET_ACCESS_KEY` / `S3_REGION` / `MEETING_AI_REMOTE_BLOBS`.

`meeting_ai/config.py` runs `_load_dotenv(ROOT / ".env")` at import time using `os.environ.setdefault`,
and `server.py`/`jobs.py` import `config` — so importing the harness pulls the **owner's real R2
production credentials** from `.env` into `os.environ` for any of those four unblocked variables, for
the whole test process.

**Why it matters:** BUG-045 is specifically about this file's real credentials leaking into local runs.
A blobstore test that doesn't explicitly override all five `S3_*` vars (+ the opt-in flag) before calling
`blobstore.get_storage()` could silently pick up the real bucket/keys instead of a fake one.

**How to apply:** any test touching `blobstore.get_storage()` / `backend.storage()` must
`mock.patch.dict("os.environ", {...}, clear=False)` starting from a dict that blanks **all** of
`S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY, S3_REGION, MEETING_AI_REMOTE_BLOBS`,
then layers in fake values per scenario (used `*.example.test`, an RFC 2606 reserved TLD, as the fake
endpoint domain). Never call `S3Storage.put/get/exists` in tests — those make a real HTTP request even
with a fake host; only inspect `.kind`/`.bucket`/`.endpoint` and the stderr notice text, and always
`blobstore.reset()` in `setUp`/`addCleanup` since the chosen backend is cached in a module global.

See [[nonvacuous-proof-via-monkeypatch]] for how this was verified to actually catch a reintroduced bug.

**Update (same day, coordinator follow-up):** `tests/_harness.py` itself now blanks all six vars
(`S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`,
`MEETING_AI_REMOTE_BLOBS`) at the top before any `meeting_ai.*` import, not just `S3_BUCKET` — closing
the gap described above at the shared-harness level too, in addition to the per-test override belt above.
Both layers are kept: the harness stops the leak at import time for every test file; the per-test
`mock.patch.dict` in the BUG-045 test file stops it from depending on harness state a future editor might
loosen again.
