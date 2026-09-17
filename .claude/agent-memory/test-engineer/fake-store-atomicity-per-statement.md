---
name: fake-store-atomicity-per-statement
description: When adding atomic-decision methods to tests/_harness.py FakeStore, lock only the single method body, never the whole request flow
metadata:
  type: feedback
---

When a fake test double stands in for a database function that is supposed to be one atomic
SQL statement (e.g. `pgstore.claim_invite`, `claim_first_admin`, `attach_invite` — each a single
`UPDATE ... WHERE ... RETURNING` or `INSERT ... ON CONFLICT`), give each such method its **own**
short-lived `threading.Lock()` held only for that method's own body. Do not share one lock across
multiple store methods, and never let a lock span more than one method call.

**Why:** a backend-dev reviewer flagged this directly — if the fake store serializes the whole
`_signup` flow (or several of its methods) under one lock, a concurrency regression test will
pass even when the production code under test is still buggy (ignores the real return value of
the atomic call), because the fake accidentally prevents the interleaving the bug depends on.
That is "worse than no test": green CI hiding a real race. Confirmed by reverting the mechanism
(monkeypatched `FakeStore.claim_invite`/`claim_first_admin` to always return `True`, mimicking
the old code that ignored `redeem_invite`'s return value) — with correctly-scoped per-method
locks, the concurrency tests immediately produced 8 accounts from 1 invite / 8 admins from an
empty system, matching the ticket's negative control exactly.

**How to apply:** for every new FakeStore method that models a single-statement atomic decision,
ask "does this lock's scope match exactly the SQL statement it fakes, or does it leak into
adjacent calls?" Add the negative-control revert as a one-time manual check (not a committed
test) before trusting the concurrency assertions.

See also [[bug-012-toctou-fake-store-sync]].
