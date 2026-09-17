---
name: whisper-cli-now-installed
description: whisper-cli + ggml model ARE installed on this dev machine now, contradicting older bootstrap notes
metadata:
  type: project
---

Older docs (`docs/PROJECT-CONTEXT.md`, `docs/LEARNINGS.md` bootstrap section) said `whisper-cli` and the ggml model were not installed, so local `process` jobs could never reach `done` here. As of 2026-09-16 that's false: `bin/whisper/Release/whisper-cli.exe` and `models/ggml-large-v3-turbo-q5_0.bin` exist, and `.env` has `STT_PROVIDER=local`. A real `process` job against a pure sine-tone WAV ran STT to completion and correctly errored with "ถอดเสียงไม่ได้ข้อความเลย" (no speech found) — a content error, not a missing-binary error.

**Why:** I fixed this fact in `docs/PROJECT-CONTEXT.md` directly (both the Local dev bullet and the hygiene red-flags bullet) rather than trusting the stale note — the LEARNINGS.md advice to "monkeypatch runner.transcribe_job; do not spend time fixing transcription locally" no longer applies verbatim.

**How to apply:** For a real end-to-end happy-path `process` test here, use actual speech audio (not `ffmpeg sine=...`), e.g. TTS output or a short spoken clip, and expect the job to reach `status: done` with a real transcript/summary (summary needs a working `LLM_API_KEY` too — check `.env`). Docker Desktop is installed but its daemon may not be running (`docker ps` fails) — check before assuming bot features are testable.
