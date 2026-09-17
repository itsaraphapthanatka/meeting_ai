---
name: env-isolation-s3-r2
description: meeting_ai .env on the dev machine has live Cloudflare R2 credentials that leak into "isolated" file-mode test servers
metadata:
  type: project
---

`meeting_ai/.env` on this machine sets real `S3_ENDPOINT`/`S3_BUCKET=meeting-ai`/`S3_ACCESS_KEY_ID`/`S3_SECRET_ACCESS_KEY` (Cloudflare R2). `backend.storage()` (`meeting_ai/web/backend.py:36`) picks S3/R2 whenever those env vars are present, **independent of `MEETING_AI_CLOUD`** — so a plain local, no-auth `./mai web --port <mine> --no-open` (the documented file-mode black-box recipe) still returns real, validly-signed presigned PUT URLs from `GET .../tracks/{name}/upload-url` against the live bucket.

**Why:** I called that endpoint during a routine sweep, expecting `url: null` (local disk, per the recipe's stated file-mode behaviour) and instead got a working R2 presigned URL. No object was actually uploaded (I happened to use the direct-POST-bytes endpoint instead), but a credential-bearing signed URL was live for an hour and got echoed into a tool transcript. This is a `docs/PROJECT-CONTEXT.md` fact I corrected (see the file) and a `docs/LEARNINGS.md` bullet dated 2026-09-16.

**How to apply:** Before starting ANY server for testing (file or cloud mode), export empty overrides so `_load_dotenv`'s `os.environ.setdefault` cannot fill them from `.env`:
```
S3_BUCKET="" S3_ENDPOINT="" S3_ACCESS_KEY_ID="" S3_SECRET_ACCESS_KEY="" ./mai web --port <yours> --no-open
```
Then verify with `GET /api/meetings/<mid>/tracks/mixed/upload-url?ext=wav` → `{"url": null, ...}` before doing anything else. Never PUT bytes to a presigned URL a test run hands you without first checking the host is not `*.r2.cloudflarestorage.com` / a real S3 endpoint. If you ever see such a URL, stop, do not call it, and flag it — do not just quietly continue.
