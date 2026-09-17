---
name: id-guard-payload-matrix
description: The payload set that actually matters when auditing a path/id validator in meeting_ai, and the two regex traps (re.match + $, unbounded suffix)
metadata:
  type: project
---

Auditing `_safe_job_id` (BUG-044, `meeting_ai/web/server.py`) — the shape that works and the traps.

**Shape that holds:** `base, _, suffix = job_id.partition(".")` → `store.valid_id(base)` (exact meeting-id
regex, so no separators, no drive letters, no `..` prefix possible) + charset allow-list on the suffix.
Because the base is pinned to a 22-char fixed format, every traversal attempt has to live in the suffix, and
`[A-Za-z0-9._-]` has no `/` `\` `:` `%` `\x00`. `.`/`..` as the whole suffix is harmless: the result is still
`WEB_DIR / "<mid>....wav"`, a plain filename.

**Trap 1 — `re.match` + `$` accepts a trailing `\n`.** Both `_JOB_SUFFIX_RE` and `store._ID_RE`/`pgstore._ID_RE`
have it: `valid_id("20260916-120000-abcdef\n") is True`. `\x00` is correctly rejected. Ask for `re.fullmatch`
or `\Z`. See [[tightening-worker-callbacks-strands-jobs]].

**Trap 2 — no length cap on the suffix**: a 5000-char suffix passes; only the 65536-byte request-line limit
of `http.server` bounds it.

**Payloads worth running every time (all 400 here):** `%2F..%2F`, double-encoded `%252F`, `%2e%2e%2f`,
`%5C..%5C` (backslash — not a separator on POSIX but is on Windows), `%00`, `C%3A%2F` (absolute path kills
the `pathlib` base), fullwidth `ｅｎ`, combining accents, cyrillic homoglyphs, `;`-params, base with trailing
space. `[A-Za-z0-9]` ranges are literal codepoints so unicode normalization is a non-issue — `\w` would not be.

**Check first next time:** is the guard before EVERY action in the block (in `_worker_api` the `tracks` GET
sits before the "does this job exist" check), and does anything else in the block still read `rest[1]` raw?
Then diff the data directory AND its parent before/after the run — that is the only proof that matters.
