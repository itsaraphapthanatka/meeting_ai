# backend-dev memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-16 — BUG-011 body caps
- [413 ต้อง lingering drain](http-413-needs-lingering-drain.md) — ตอบ 413 แล้วปิด socket ทันที = client เห็น connection reset ไม่ใช่ status
## 2026-09-17 — BUG-055 stale detail cache (file mode)
- [mtime is not a cache key](file-store-mtime-cache.md) — 300 writes = 26 distinct mtimes here; cache only files settled > 2 s, and the file store is not single-process.
## 2026-09-16 — BUG-045 blob storage opt-in
- [Blob storage opt-in](blob-storage-opt-in.md) — S3 needs cloud mode or MEETING_AI_REMOTE_BLOBS=1; test with fake S3_* because .env holds production R2 keys.
## 2026-09-16 — BUG-044 (path traversal via translate lang + worker audio)
- [Job id = filename](job-id-and-lang-are-filesystem-input.md) — allow-list user strings that get spliced into ids; validate the id before the 404 check in `_worker_api`.
- [Prove fixes against a pre-fix copy](proving-fixes-against-pre-fix-code.md) — `git show HEAD:<file>` into a scratchpad tree (exclude `.env`), then clean the files the PoC wrote.

## 2026-09-16 — P0 fix round
- Cloud-mode test/proof recipe: patch backend.cloud, backend.store, jobs.cloud, jobs.store, server.store (jobs.py freezes bindings at import); set REMOTE_WORKER=1 before import so jobs.start() spawns no thread.
- jobs.draft(mid) in cloud = spec of ANY job status; check status == 'draft' explicitly before treating it as a draft.
- submit_summarize job id == meeting id + job_upsert overwrites spec; always pass owner_id=self.user_id or meeting.get('owner_id') — share-edit callers have user_id None.
- Guard owner comparisons: `if owner and owner == self.user_id`; never bare == (None == None).
- psycopg None params: `%s::text is null` (see job_claim). Prove SQL with a fake db.connect recording (sql, params).
- bot.py: docker rm -f before rmtree; _run() swallows timeouts, so confirm with docker ps before deleting anything mounted; never finally-delete a staging dir holding a non-empty WAV (move is cross-filesystem copy+unlink).
- config.py freezes env at import; CLI provider tests must set env before import or patch stt.resolve / stt.local_available.
- Scope `git diff --stat -- <own files>` when other agents share the worktree.
