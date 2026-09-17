---
name: config-defaults-hide-user-intent
description: In meeting_ai a config value with a non-empty default cannot tell "user chose it" from "nobody set it" — plus how Config attributes must be patched in tests (classmethods read the class, not the instance)
metadata:
  type: project
---

Learned while fixing BUG-019 (`stt.resolve()` silently falling back local → API).

**1. A default hides intent.** `config.stt_provider = _get("STT_PROVIDER", "local")` is `"local"` on every
machine, so `resolve()` could not tell `STT_PROVIDER=local` ("never send my audio out") from "nobody
configured anything" (the normal Vercel path where falling back to `api` is correct). Any rule of the form
"only do X if the user actually asked for it" needs a **separate `*_set` flag** next to the value
(`config.stt_provider_set = bool(_get("STT_PROVIDER", "").strip())`), not a comparison against the default.
Check this before designing fail-closed behaviour on top of any `config.py` field — most of them have
non-empty defaults (`llm_model`, `whisper_bin`, `stt_model`, `whisper_lang`…).

**2. Patching `config` in tests often does nothing.** `Config.stt_key()`, `stt_base_url()`,
`whisper_model_path()`, `vad_model_path()` are `@classmethod`s that read `cls.<attr>`.
`mock.patch.object(config, "stt_api_key_raw", "x")` sets an attribute on the *instance* and the classmethod
keeps reading the class → my first proof run reported "no API key" while I thought I had set one.
Patch the class: `from meeting_ai.config import Config; mock.patch.object(Config, "stt_api_key_raw", …)`.
Plain attribute reads (`config.stt_provider`) work either way, which is why this fails silently instead of loudly.

**3. Agent worktrees have no `.env`.** `.env` lives only in the owner's main checkout, and
`config.ROOT = <repo>/meeting_ai/..` resolves to the worktree — so inside a worktree there are no LLM/STT/R2
credentials and `local_available()` is False (no `models/`, no `bin/whisper/`). That makes env-driven proofs
honest and safe here, but it also means "it behaved like X in my worktree" does not predict the owner's
machine, where `STT_PROVIDER=local` and whisper both exist. See [[blob-storage-opt-in]] for the opposite
trap in the main checkout.

**Design rule this produced (same shape as BUG-045):** when killing a silent fallback, split it by
*direction of harm*, not symmetry — `local → api` sends meeting audio to a third party (refuse when chosen,
announce when defaulted), `api → local` keeps it home (just say so). Every branch that ends in "audio leaves
the machine" must print the destination host, and the refusal message must name the flag that opts in.
