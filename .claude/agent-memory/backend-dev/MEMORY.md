# backend-dev memory

One line per lesson; newest first. No secrets, no personal data.

## 2026-09-17 — auth rate limit (BUG-010)
- [กับดัก 4 ข้อของ rate limiter](rate-limit-design-traps.md) — สำเร็จแล้วล้างโควตา / เชื่อหัวข้อ proxy ผิดตัว / regex แปลง IP + IPv6 /128 / แคชทิ้งคีย์ที่เพิ่งนับ
- อย่าใช้ `sed`/replace ทั้งไฟล์กับ SQL: รอบแรกเติม `::double precision` หลุดไปสอง statement ที่ไม่เกี่ยวกับตั๋วและทดสอบกับ Postgres จริงไม่ได้ — ตรวจ `git diff` ของไฟล์ SQL ทีละบรรทัดเสมอ
- [Early reject + keep-alive](http-server-early-reject.md) — ตอบก่อนอ่าน body ต้องส่ง `Connection: close` ไม่งั้นคำขอถัดไปบนสายเดิมเพี้ยน
- [ทรง deploy กำหนดที่เก็บ state](deploy-shape-matters-serverless.md) — ตัวนับต้องอยู่ใน Postgres; ล็อกอินมีเฉพาะโหมด cloud จึงไม่ต้องทำฝาแฝดใน store.py

## 2026-09-16 — P0 fix round
- Cloud-mode test/proof recipe: patch backend.cloud, backend.store, jobs.cloud, jobs.store, server.store (jobs.py freezes bindings at import); set REMOTE_WORKER=1 before import so jobs.start() spawns no thread.
- jobs.draft(mid) in cloud = spec of ANY job status; check status == 'draft' explicitly before treating it as a draft.
- submit_summarize job id == meeting id + job_upsert overwrites spec; always pass owner_id=self.user_id or meeting.get('owner_id') — share-edit callers have user_id None.
- Guard owner comparisons: `if owner and owner == self.user_id`; never bare == (None == None).
- psycopg None params: `%s::text is null` (see job_claim). Prove SQL with a fake db.connect recording (sql, params).
- bot.py: docker rm -f before rmtree; _run() swallows timeouts, so confirm with docker ps before deleting anything mounted; never finally-delete a staging dir holding a non-empty WAV (move is cross-filesystem copy+unlink).
- config.py freezes env at import; CLI provider tests must set env before import or patch stt.resolve / stt.local_available.
- Scope `git diff --stat -- <own files>` when other agents share the worktree.
