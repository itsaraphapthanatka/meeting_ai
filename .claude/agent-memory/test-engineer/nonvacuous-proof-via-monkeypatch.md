---
name: nonvacuous-proof-via-monkeypatch
description: How to prove a regression test is non-vacuous without editing app code — scratch script + mock.patch.object with the old buggy function body
metadata:
  type: feedback
---

To prove new regression tests actually catch the bug they claim to, without touching application code
(test-engineer may only edit test dirs): write a **throwaway script outside the repo** (scratchpad dir)
that imports the test module, then wraps a subset of the test suite in
`mock.patch.object(<module>, "<func>", <old_buggy_implementation>)` reproducing the pre-fix behaviour
inline, and runs it with `unittest.TextTestRunner`. Confirm the expected tests fail for the right reason
(assertion message names the wrong value), then let the `with` block exit — no repo file is ever touched,
so there is nothing to revert.

**Why:** used for BUG-045 (`blobstore.get_storage`/`backend.storage`) — same technique as the 2026-09-16 P0
round, but this time applied to two independent mechanisms (the opt-in gate inside `get_storage`, and the
`allow_remote=cloud` wiring in `backend.storage`) with two separate monkeypatched stand-ins, run in one
script. Reusable pattern: pick the *smallest* stand-in that reproduces just the reverted mechanism (e.g. a
`get_storage` clone missing the `allow_remote`/`remote_opt_in()` gate) rather than trying to literally revert
the whole file via git, which would require touching tracked files this role isn't allowed to edit.

**How to apply:** whenever a fix has more than one independent guard (here: the local opt-in check AND the
cloud-mode `allow_remote` plumbing), write and run one stand-in per guard — a single revert can hide that
the other guard's test was accidentally vacuous.
