---
name: bug-010-bug-012-fakestore-merge
description: How to reconcile FakeStore in tests/_harness.py when two auth-area tickets (BUG-010 login rate limit, BUG-012 invite TOCTOU) both extend it with different user/invite shapes
metadata:
  type: project
---

When merging PR branches for BUG-010 (login rate limit) and BUG-012 (invite redemption TOCTOU)
into `tests/_harness.py`, git left the `__init__` block as a real `<<<<<<<`/`>>>>>>>` conflict,
but the *method bodies* below it merged without markers because each PR inserted its own copies
into different byte ranges of the same class body — producing silent duplicate method
definitions (`ensure_user`, `set_password`, `has_password`, `create_session`, `invite_email`
each appeared twice). Python's last-definition-wins semantics meant the file *ran*, but half the
methods were dead code with a different, inconsistent field shape (`"password"` plaintext vs
`"password_hash"` hash) than the ones actually executing.

**How to detect this class of bug**: after resolving the textual conflict, run
`grep -n "^    def " file.py | awk -F'def ' '{print $2}' | awk -F'(' '{print $1}' | sort | uniq -d`
on the merged class — any name printed twice is a silent duplicate, not a real conflict, and git
will not flag it.

**Resolution used**: verified the real production contract first (`grep` for `store.<name>(` call
sites in `server.py`, then read the matching `pgstore.py` function bodies) before touching the
fake. BUG-012's shape (users keyed by lowercased email, `password_hash` field, real
`invite_email`/`has_password`/`claim_invite`/`attach_invite`/`claim_first_admin`/`ensure_user`/
`set_password`/`create_session`) is what `server.py._signup()` actually calls, so it won. BUG-010
only needed `verify_password` (call-counting via `verify_password_calls`) and `add_account`/
`rate_hit`/`rate_reset`/`drop_session`/`create_invite`, none of which BUG-012 touched — but
`verify_password` and `add_account` had to be rewritten to read/write the *same*
`"password_hash"` field (sha256 hex digest, not real scrypt) that `set_password`/`ensure_user`
now use, or a signup-then-login flow would silently never match. Dropped `redeem_invite`
entirely — grep confirmed zero callers in `meeting_ai/` or `tests/` (BUG-012's ticket replaced
it with atomic `claim_invite`/`claim_first_admin`; the only remaining reference was its own
now-inapplicable docstring).

Found one real (non-harness) cross-PR regression this way: BUG-012 intentionally changed the
signup response for an unrecognized/invalid invite code from 403 to 409 (documented in its own
ticket text), which breaks a pre-existing BUG-010 assertion
(`test_bug_010_signup_is_rate_limited_in_its_own_bucket` expects `[403] * LIMIT`). This is a
behavior-contract conflict between the two tickets, not a FakeStore shape issue — reported it
rather than editing the assertion, since 403 vs 409 may be an intentional external API decision
the owner should confirm, not something a test-engineer should silently normalize.
