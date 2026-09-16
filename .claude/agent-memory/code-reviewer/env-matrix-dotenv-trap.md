---
name: env-matrix-dotenv-trap
description: Verifying env-driven behaviour (S3/cloud/STT selection) — `env -u VAR` does not unset anything because .env refills it; pass `VAR=` instead
metadata:
  type: feedback
---

When proving an env-driven code path in meeting_ai, build the truth table with **explicit empty values**
(`env S3_BUCKET= DATABASE_URL= MEETING_AI_CLOUD= python …`), never with `env -u S3_BUCKET`.

**Why:** `meeting_ai/config.py` calls `_load_dotenv(ROOT/".env")` at import with `os.environ.setdefault`,
and importing almost anything (`web.backend` → `web.store` → `config`) pulls it in. `env -u` leaves the key
absent, so `setdefault` fills it from the owner's real `.env` — which holds the production R2 bucket and
`DATABASE_URL`. My first BUG-045 matrix run reported "no S3 configured" cases that actually ran with the
production bucket and `cloud=True`; every row was wrong and two would have been false findings.

**How to apply:** any review that claims "with X unset the code does Y" must be re-run with `X=` before it is
written down. Same trap for `MEETING_AI_CLOUD`, `WORKER_TOKEN`, `STT_*`. Cross-check the printed result
against a value you injected (e.g. a fake bucket name) — if the output shows a name you did not inject, the
isolation failed. Related: [[MEMORY]] cheap-checks line.
