# backend-dev memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — BACKLOG #15/#16 (static guard + share cookie)
- [GET ที่ตั้งคุกกี้ = fixation](cookie-set-by-get-is-fixation.md) — ยืนยันด้วย POST + application/json (415 ถ้าไม่ใช่) และเช็คโทเคนใน URL ก่อน needsAuth()
- [str.startswith ไม่ใช่การตรวจ containment](prefix-string-path-guard.md) — โฟลเดอร์พี่น้อง static_backup/ หลุด; PoC ด้วยการ patch server.STATIC_DIR
- [เทสต์เก่าอาจล็อกพฤติกรรมที่เป็นบั๊กไว้](tests-can-encode-the-bug.md) — grep tests/ ก่อนแก้ finding เก่า; ธรรมเนียม import คือ `from _harness import`; test_bug_011 flake ตอนเครื่องโหลดหนัก

## 2026-09-17 — BUG-048
- [Worker results are untrusted input](worker-result-is-untrusted-input.md) — `apply_result` = worker POST + the only meeting-create path; spec fields beat body fields, cloud jobs have no `_lang`.
- Parallel agents work on sibling branches from the same commit: my worktree's `main` was 2 commits behind the owner's checkout and did NOT contain the BUG-011 `_clean_segments` hardening the ticket assumed. `git log --oneline main -3` + read the function before trusting a ticket's "X already does Y".
## 2026-09-17 — BUG-012 invite/first-admin TOCTOU
- [Atomic claims for read-then-write races](toctou-atomic-claims.md) — claim before create, single statement, `not exists` does not serialize; proving races without Postgres + negative control.
- Same file: moving an authz decision later makes guards the old early return hid newly reachable — my TOCTOU fix opened an email-enumeration oracle. Probe for it.
- Another session may commit my worktree mid-task (`git status` clean, HEAD = a wip commit on a new branch): check `git log`/`git branch --show-current` before concluding nothing changed.
- [File store concurrency (BUG-056)](file-store-concurrency.md) — msvcrt ใช้ `LK_NBLCK` ไม่ใช่ `LOCK_NBLCK`; ผู้อ่านทำให้ `os.replace` ล้ม 83% บน Windows; เทสชุดเดิมไม่แตะเส้นทางเขียนของ store
## 2026-09-17 — auth rate limit (BUG-010)
- [กับดัก 4 ข้อของ rate limiter](rate-limit-design-traps.md) — สำเร็จแล้วล้างโควตา / เชื่อหัวข้อ proxy ผิดตัว / regex แปลง IP + IPv6 /128 / แคชทิ้งคีย์ที่เพิ่งนับ
- อย่าใช้ `sed`/replace ทั้งไฟล์กับ SQL: รอบแรกเติม `::double precision` หลุดไปสอง statement ที่ไม่เกี่ยวกับตั๋วและทดสอบกับ Postgres จริงไม่ได้ — ตรวจ `git diff` ของไฟล์ SQL ทีละบรรทัดเสมอ
- [Early reject + keep-alive](http-server-early-reject.md) — ตอบก่อนอ่าน body ต้องส่ง `Connection: close` ไม่งั้นคำขอถัดไปบนสายเดิมเพี้ยน
- [ทรง deploy กำหนดที่เก็บ state](deploy-shape-matters-serverless.md) — ตัวนับต้องอยู่ใน Postgres; ล็อกอินมีเฉพาะโหมด cloud จึงไม่ต้องทำฝาแฝดใน store.py
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
