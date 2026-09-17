---
name: proving-fixes-against-pre-fix-code
description: How to prove a security fix really closes the hole — run the same PoC against a HEAD copy of the source in the scratchpad (no git stash/checkout), and clean the artifacts it writes
metadata:
  type: feedback
---

A fix is only proven when the same PoC **fails before** and **passes after**. Since git
`stash`/`checkout` are forbidden without the user asking, reproduce the pre-fix behaviour by
copying the tree into the scratchpad instead:

```
tar --exclude='.env' --exclude='__pycache__' -cf - meeting_ai tests | (cd $SCR/prefix && tar -xf -)
git show HEAD:meeting_ai/web/server.py > $SCR/prefix/meeting_ai/web/server.py   # read-only git
MAI_PROOF_REPO=$SCR/prefix python proof.py       # proof script takes the repo root from env
```

**Why:** the pre-fix run is the only evidence the guard is load-bearing; a green post-fix test
alone can pass because the PoC was written wrong. It also shows the reviewer the exact old
response (e.g. `202` + poisoned job id, `200` + a path outside `WEB_DIR`).

**How to apply:**
- Always exclude `meeting_ai/.env` from the copy — it holds live R2 credentials that
  `config._load_dotenv` would `setdefault` back in.
- A path-traversal PoC on pre-fix code **really writes the file** (mine landed at
  `%LOCALAPPDATA%\pwned.wav`, three levels above the temp `WEB_DIR`). Find and delete the
  artifacts afterwards, and never run the pre-fix copy pointed at the real `recordings/web/`.
- Keep the proof script parameterised (`os.environ["MAI_PROOF_REPO"]`) so the same file runs
  against both trees; it lives in the scratchpad, not in `tests/` (test-engineer owns those).

See also [[job-id-and-lang-are-filesystem-input]].
