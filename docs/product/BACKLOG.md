# meeting_ai backlog

Ordered by what to do next · owner = agent role · source = QA report / ticket / PRD

Seeded 2026-09-16 from the full code review (see `docs/PROJECT-CONTEXT.md` → "Known state / hygiene red flags" for file:line). Re-verify each item before working on it; the code may have moved.

## P0 — users blocked, money or data at risk
| # | Item | Owner | Notes |
|---|---|---|---|
| 1 | ✅ Draft routes `tracks/upload-url`, `tracks/{name}`, `process` skip the ownership check — any logged-in user (or share-cookie holder) can upload into and start someone else's draft | backend-dev | **fixed 2026-09-16, merged to `main` (c0b1115, PR #1)** · `_draft_spec` + `_may_write_draft` · tests `test_p0_01_draft_ownership` |
| 2 | ✅ Share cookie passes the global auth gate → `GET /api/jobs`, `GET /api/workers` leak every user's active jobs and the worker roster | backend-dev | **fixed 2026-09-16, merged to `main` (c0b1115, PR #1)** · `_share_may_call` allow-list, `_workers_view` · tests `test_p0_02_share_gate` |
| 3 | ✅ `jobs.active()` / `store.job_active()` not scoped per user → other tenants' meeting titles inside `GET /api/meetings` | backend-dev | **fixed 2026-09-16, merged to `main` (c0b1115, PR #1)** · `_job_scope` → `jobs.active(owner_id, meeting_id)` · tests `test_p0_03_jobs_scoped` (team visibility → #37) |
| 4 | ✅ Concurrent bots share `recordings/bot/` as `/out`: `bot_status.txt` and screenshots collide, `_keep_debug_shot` deletes another job's live shots | backend-dev | **fixed 2026-09-16, merged to `main` (c0b1115, PR #1)** · `bot._job_slot`, `_stage_removable`, `_prune_stages(live=)` · tests `test_p0_04_bot_staging_per_job` |
| 5 | ✅ CLI (`mai process`, `record --process`, `bot`, `transcribe`) ignores `STT_PROVIDER` and always calls whisper.cpp | backend-dev | **fixed 2026-09-16, merged to `main` (c0b1115, PR #1)** · `stt.transcribe` in `pipeline`/`cli`, `--stt` flag · tests `test_p0_05_cli_uses_stt_provider` |
| 44 | ✅ **Arbitrary file write**: unvalidated translate `lang` → poisoned job id → worker audio endpoint wrote outside the data dir | backend-dev + security-engineer | **fixed (uncommitted) 2026-09-16** · `jobs.safe_job_id` + guard in `_worker_api()` and `claim()`, `MAX_ATTEMPTS=5`, `lang` allow-list · tests `test_bug_044_translate_lang_worker_path_write` (20 tests) · ticket [BUG-044](../tickets/BUG-044-translate-lang-worker-path-write.md) |
| 6 | ✅ Regression tests for #1-#5 (there are no tests at all today) | test-engineer | **done 2026-09-16** · `tests/_harness.py` + 5 modules, 64 tests · run `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests -v` |

## P1 — wrong behaviour
| # | Item | Owner | Notes |
|---|---|---|---|
| 7 | `summarizer._chat` hard-caps `max_tokens: 4000` and ignores `finish_reason` → long summaries silently truncated (introduced in commit f068e8c) | backend-dev | make configurable, raise/retry on `length` |
| 8 | No transcript chunking / map-reduce → multi-hour meetings overflow the LLM context | architect → backend-dev | ADR first: chunk by timestamp, summarize parts, merge |
| 9 | `summarize()` always outputs Thai; `--lang` / meeting language never reaches the prompt | backend-dev | add `target_lang`, use `LANGUAGE_NAMES` |
| 12 | ✅ Invite redemption TOCTOU: user created before `redeem_invite`, boolean result discarded | backend-dev | **fixed (uncommitted) 2026-09-17** · `claim_invite` / `claim_first_admin` decide in one statement before the user exists · review also closed an email-enumeration oracle the fix itself introduced · **proven on real PostgreSQL 16.15**: 12 concurrent signups on one invite gave 12 accounts before and 1 after; 12 concurrent first-signups gave 12 admins before and 1 after · tests `test_bug_012_invite_toctou` (12) · ticket [BUG-012](../tickets/BUG-012-invite-redemption-toctou.md) · no `db-init` needed |
| 10 | ✅ No rate limit on `POST /api/auth/login` (scrypt N=2¹⁴ also a CPU lever) | backend-dev + security-engineer | **fixed (uncommitted) 2026-09-17** · two buckets (10/15min cleared on success, 60/hour never cleared), rejected before scrypt, Postgres-backed so it works on serverless · review round closed 5 bypasses that made the first version ineffective · tests `test_bug_010_login_rate_limit*` (17) · ticket [BUG-010](../tickets/BUG-010-login-rate-limit.md) · **owner must run `./mai db-init`** |
| 11 | ✅ `_body_json` read an unbounded `Content-Length`; `_clean_segments` capped neither count nor text length | backend-dev | **fixed (uncommitted) 2026-09-16** · 64 KB control-plane / 8 MB transcript routes, 413, strict `Content-Length` · review round also closed keep-alive request smuggling, slowloris and a `NaN` that bricked a meeting permanently · tests `test_bug_011_body_size_caps` (43) · ticket [BUG-011](../tickets/BUG-011-unbounded-request-body.md) |
| 50 | Worker results over `MAX_JSON_TRANSCRIPT` (8 MB) come back 413, and `worker.py:344-353` turns that into a job error — the transcript is discarded with no retry. A very long meeting would lose completed work | backend-dev | fall back to chunked upload, or persist the result before reporting; found while auditing BUG-011 |
| 51 | `except Exception: self._error(500, str(e))` in `_route()` returns raw Python exception text to the client (e.g. `invalid literal for int() with base 10: 'abc'`) | backend-dev + security-engineer | log the detail, return a generic Thai message |
| 52 | Every response carries `Server: meeting_ai Python/3.12.10`, disclosing the interpreter version | devops-engineer | override `version_string()` on the handler |
| 53 | **Structured action items.** The summary prompt already emits `## 📋 สิ่งที่ต้องทำต่อ (Action Items)` (`summarizer.py:37,61,121`) but only as Markdown inside `summary`. A mobile action-item checkbox with done/pending state and an assignee needs them parsed and stored as records with their own state | architect → backend-dev → web-dev | blocks the design system's Action Item Checkbox component; UI was deliberately not built without it |
| 54 | **Ad-hoc Q&A over a meeting.** The design system's contextual prompt chips ("Summarize action items", "What was decided?") need a free-form question endpoint. Today there is only `resummarize` (fixed templates) and `translate` | architect → backend-dev | decide whether this is a new endpoint or a `resummarize` template; chips were deliberately not built without it |
| 46 | `GET /api/meetings/{id}/audio` with `Range` past EOF returns 200 + whole file instead of 416 + `Content-Range: bytes */<size>` (`server.py:1154`) | backend-dev | RFC 9110 §14.4; low real-world impact |
| 47 | Tab-share option is not hidden at mobile width — feature-detection only, and sets `disabled` not `hidden` (`app.js:704`, `index.html:161`) | web-dev | add `matchMedia('(max-width: 480px)')`; use `.closest('.mode').hidden` |
| 48 | `jobs.py:382` `store.set_translation(meeting_id, result["lang"], ...)` trusts the `lang` the WORKER returned instead of the one in the job spec — a worker-token holder can write junk keys into `translations` (not a path, not rendered) | backend-dev | use `(job.get("_spec") or {}).get("lang")`; changes the `worker.py` ↔ `apply_result` contract so it needs its own tests. Split out of BUG-044 |
| 49 | Before deploying the BUG-044 fix, audit production for pre-patch poisoned rows: `python scripts/audit_job_ids.py` (read-only; `psql` is not installed on the owner's machine, the script uses psycopg and filters with the app's own `jobs.safe_job_id`) | owner + devops-engineer | they now terminate on the next claim instead of looping; `--sql` prints an optional UPDATE to close them immediately |
| 13 | ✅ **fixed via #44** — Translate `lang` unvalidated → enters job id `<mid>.tr.<lang>` and the LLM prompt (prompt injection confirmed; junk key `../../pwn` stored in `translations`) | backend-dev | restrict to `LANGUAGE_NAMES`. Fix together with #14 |
| 14 | ✅ **fixed via #44** — Worker audio endpoint builds `WEB_DIR / f"{job_id}.{ext}"` without `valid_id`; `unquote(job_id)` at `server.py:877` happens after the path split, so `%2F..%2F` survives | backend-dev | `server.py:902-905`. Fix together with #13 |
| 15 | Static file guard uses `str.startswith` instead of `Path.is_relative_to` | backend-dev | `server.py:1127` |
| 16 | `GET /s/<token>` sets `mai_share` on plain navigation → share-cookie fixation + SPA auto-navigate | backend-dev + web-dev | confirm via POST from the SPA |
| 17 | API-path chunking uses `-c copy` but offsets `i * 900` → timestamp drift on long files | backend-dev | `stt.py:202-207` |
| 18 | `drop_hallucinations()` only on the local path; API transcripts keep repeated-phrase junk | backend-dev | `stt.py:243` |
| 19 | `stt.resolve()` silently falls back local → API when the model is missing (privacy) | backend-dev | surface a warning to the UI |
| 20 | `worker --once`: heartbeat stops before the in-flight job finishes → server reaps the job | backend-dev | `worker.py:311, 404` |
| 21 | Bot container runs as root with `--no-sandbox`, no `HEALTHCHECK`; `x11vnc -nopw`; Zoom passcode + URL via docker env | devops-engineer + security-engineer | `bot/Dockerfile`, `entrypoint.sh:29`, `bot.py:391-395` |
| 22 | Downloads (models, whisper binaries) unverified; `setup-ubuntu.sh` clones whisper.cpp without a tag | devops-engineer | pin + sha256 |
| 37 | Jobs list scoped by `spec->>'owner_id'` only: jobs on a `team` meeting submitted by another member, and pre-deploy jobs without owner_id, are invisible to the meeting owner (security audit 2026-09-16 #3 remainder) | backend-dev + web-dev | OR a `meeting_id = any(<writable meetings>)` branch in `pgstore.job_active`; accepted for now |
| 38 | `worker_tag()` truncates to 16 alnum chars: `meeting-ai-worker-01`/`-02` collide → one worker's `cleanup_stale`/`_prune_stages` can hit the other's containers and staging dirs (audit #5, review nit #6) | backend-dev + devops-engineer | `slug[:12] + md5(name)[:6]`; document unique worker names; round-2 `live` check mitigates dirs only |
| 39 | `bot.py` raw `subprocess.run` docker calls without timeout (`docker rm -f` in `join_and_record`, `docker stop` in `leave()`, `login()`), and `proc.wait(timeout=120)` raises uncaught `TimeoutExpired` instead of a Thai error | backend-dev | route through `bot._run()`; catch `TimeoutExpired` → `proc.kill()` + message |
| 42 | Verify production has no `meetings.owner_id is null` rows: `pgstore.access()` grants `owner` to every user for such rows (intended for data migrated from file mode) | owner / devops-engineer | `select count(*) from meeting_ai.meetings where owner_id is null;` |

| 45 | ✅ `backend.storage()` picked S3/R2 whenever `S3_BUCKET` was set, ignoring the mode — `files` mode on a box holding `.env` handed out presigned PUT URLs for the **production** bucket, which is why the R2 keys need rotating | backend-dev | **fixed (uncommitted) 2026-09-17** · remote blobs now require `MEETING_AI_REMOTE_BLOBS=1` outside cloud mode and every path announces which store it chose · tests `test_bug_045_blob_storage_optin` (13) · ticket [BUG-045](../tickets/BUG-045-implicit-remote-blob-storage.md) |
| 55 | ✅ `store.load_detail()` cached on file mtime while four of the five detail-write paths never invalidated it — in file mode an edit could be invisible to the very next read ("I saved it and it didn't save"), intermittently | backend-dev | **fixed (uncommitted) 2026-09-17** · measured: 300 consecutive writes produce 14 distinct mtimes and 286 of them repeat the previous one, so ~95% of rapid writes were invisible to that cache · found by running the suite on #2 and #5 merged together, which neither branch's own suite does · ticket [BUG-055](../tickets/BUG-055-stale-detail-cache.md) |
| 56 | **Lost update on `index.json` across processes.** Every file-store function does read-modify-write under a `threading.RLock`, which only guards one process. `mai web` and a CLI run share `recordings/web/`, so if both edit different meetings at once the later writer overwrites the whole file and the other edit is **gone from disk** — more severe than #55 and harder to notice | backend-dev | needs a real file lock (`msvcrt.locking` / `fcntl.flock`) or an append-log; found while fixing #55, deliberately not folded into it |

## P2 — quality, debt, docs
| # | Item | Owner | Notes |
|---|---|---|---|
| 23 | CI: GitHub Actions running `compileall`, `./mai --help`, and `tests/` | devops-engineer | none exists |
| 24 | Dead cleanup never scheduled: `pgstore.purge_expired()`, `workers_forget()`, `db.close()`, `blobstore.reset()`, `backend.health()` → sessions/dead workers accumulate | backend-dev | call from `jobs_reap` or a cron |
| 25 | Retention for `logs/` (real-meeting screenshots) and failed `recordings/bot/*.wav` | devops-engineer | e.g. 30 days |
| 26 | `store.py` / `pgstore.py` share ~70 duplicated lines (`new_id`, `valid_id`, `fmt_time`, `transcript_text`, `_snippet`) | backend-dev | `web/_common.py` |
| 27 | Delete root `meeting-ai-poster.html` (byte-identical to `web/static/`); add `bot/` to `.vercelignore` | devops-engineer | |
| 28 | `meeting-ai-worker.service` / `*.ps1` hardcode prod URL and username; `worker-service.ps1` builds a script without quote escaping | devops-engineer | `EnvironmentFile=`, escape `'` |
| 29 | Docs drift: poster platform table reversed vs README; README says 5-min Vercel limit vs `maxDuration 60`; README tree lists `join_meet.py`, omits 7 modules | owner (or add `tech-writer` via `/agent-team add tech-writer`) | |
| 30 | Add `LICENSE`; add `CLAUDE.md` capturing the conventions in PROJECT-CONTEXT | owner / devops-engineer | |
| 31 | `diarize.py` loads whole WAV as `list[float]` (GBs for 2 h) and O(segments×turns) labelling | backend-dev | `array`/two-pointer merge |
| 32 | `runner.audio_duration` hardcodes `"ffprobe"` instead of deriving from `config.ffmpeg_bin` → duration 0 when ffmpeg is not on PATH | backend-dev | `runner.py:76` |
| 33 | Duplicated constants: `stale_after: 75` (`server.py:735` vs `pgstore.WORKER_STALE_SECONDS`), `DEFAULT_MAX_BOTS` (`worker.py:42` vs `cli.py:104`) | backend-dev | |
| 34 | `server.py:437`, `:512` mangled whitespace; `runner._transcribe_track` return annotation says 2-tuple, returns 3 | backend-dev | cosmetic |
| 35 | Replace `print()` with `logging` in `meeting_ai/` (keep emoji-style user output in CLI only) | backend-dev | large but mechanical |
| 36 | `render.py` in the agent-team skill: pass `encoding="utf-8"` to all file I/O and normalize `relpath` to `/` for Windows | owner (skill repo) | `itsaraphapthanatka/claude-agent-team` |
| 40 | `_fail_reason` puts the worker's absolute path and 12 raw container log lines (may include the meeting link) into `jobs.error`, now visible to the owner/admin via `/api/jobs/{id}` | backend-dev | keep the log tail in worker stdout, send a short Thai reason + status |
| 41 | Admin `_job_scope()` returns `{}` (system-wide jobs) but `store.search(user_id=admin)` lists only own meetings → `pollJobs()` opens another tenant's meeting and shows a 403 banner | web-dev | separate "system queue" from "my jobs" in the SPA, or scope admin like a user by default |
| 43 | Accepted risk (documented): draft/job endpoints answer 404 before 403, so a logged-in user can confirm a foreign draft/job id exists (audit #6). `/api/meetings/{id}` answers 403 uniformly | — | revisit if ids ever become guessable |

## Ideas (not yet assessed)
- Zoom Meeting SDK integration as the supported path for Zoom recording (README lists it as not done)
- Server → browser SSE for partial summary rendering (today the browser polls every 1.5 s)
- add via `/prd <idea>`; product-manager prioritises
