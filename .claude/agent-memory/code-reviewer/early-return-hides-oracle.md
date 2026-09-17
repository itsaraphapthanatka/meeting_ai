---
name: early-return-hides-oracle
description: Reordering/removing an early return in an auth flow can open an information oracle — check what each removed guard was concealing (BUG-012 signup)
metadata:
  type: project
---

Fixing a TOCTOU by moving the authorisation decision later can open a *new* disclosure hole:
in `_signup` (BUG-012) the old "invalid invite → 403" early return also hid the later
`has_password()` 409. Once it was removed, a junk invite string + any email told an
anonymous caller whether that email has an account (two different 409 messages).

**Why:** the early return was doing two jobs — deciding authorisation *and* short-circuiting
every later branch that answers a different question. Only the first job was documented.

**How to apply:** when a diff deletes or moves a `return self._error(...)`, enumerate every
branch that becomes newly reachable and ask what each reveals to an unauthenticated caller.
Reproduce with a small fake-store server (pattern in `tests/_harness.py`) rather than arguing.
Related: [[pgstore-review-checks]].
