---
name: project-e2e-harness-recipe
description: How to drive a full meeting_ai lifecycle on port 55431 without whisper/LLM risk, and the two API shapes that broke my first harness
metadata:
  type: project
---

Driving the whole lifecycle (draft → upload → process → job → meeting → export → audio) needs a **custom bootstrap script**, not `./mai web`: set `MEETING_AI_CLOUD=0`, `DATABASE_URL=""`, `S3_BUCKET=""` before importing `meeting_ai`, then patch `store.WEB_DIR` + `store.INDEX_PATH` + `store.SETTINGS_PATH` to a temp dir, replace `runner.transcribe_job` and `runner.HANDLERS["summarize"|"translate"]` with fakes, and start `server.Server(("127.0.0.1", 55431), server.Handler)` with `httpd.bound_host = "127.0.0.1"`. Adding `REMOTE_WORKER=1 WORKER_TOKEN=<random>` to the same script turns it into a worker-loop harness (claim → track fetch → progress/stop → audio → result) with no Postgres.

**Why:** file mode shares one `recordings/web/` across every agent, `.env` carries live R2 credentials, and the real summarizer would call the owner's LLM. The bootstrap is the only way to be genuinely isolated and still exercise real routing.

**How to apply:** start from this recipe on the next e2e task; first two things that will break a fresh harness are `GET /api/meetings/{id}` returning `segments` as a **count** (list is `segments_list`) and `GET /api/jobs` listing **only queued/running** (failed jobs vanish — fetch `GET /api/jobs/{id}`). Real `whisper-cli` + model ARE installed on this machine, so a pure sine tone fails with "ถอดเสียงไม่ได้ข้อความเลย" — use real speech if you want `done`. See [[feedback-e2e-tooling-traps]].
