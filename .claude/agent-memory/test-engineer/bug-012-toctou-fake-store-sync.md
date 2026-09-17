---
name: bug-012-toctou-fake-store-sync
description: How to deterministically reproduce a TOCTOU race in a FakeStore-based unittest without sleeps (BUG-012 invite/first-admin race)
metadata:
  type: project
---

For a TOCTOU regression test (concurrent HTTP requests through `tests/_harness.py`'s
`ThreadingHTTPServer`), do not use `time.sleep()` to widen the race window even though a
throwaway proof script in the ticket did — the suite rule is "no sleeps, deterministic".
Instead, put a `threading.Barrier(N, timeout=5)` hook inside whichever `FakeStore` method the
production code calls **unconditionally, exactly once per request, immediately before the real
atomic decision point** (here: `has_password()`, called right before `claim_invite`/
`claim_first_admin` in `server._signup`). Test sets `store.sync_barrier = threading.Barrier(N)`
right before firing N real client threads (also synchronized with their own `threading.Barrier`
so they start together); every request thread blocks in that one method until all N arrive, then
all N race into the atomic call together — deterministic, no timing guesswork, reruns clean.

Picking the right hook method matters: it must be on every code path being tested (both the
"first admin" and "existing invite" branches skip different pre-checks, but both always call
`has_password()` right before the branch), otherwise the barrier's party count never gets
satisfied for some scenario and the test hangs until the 5 s timeout.

**Why:** the ticket's own proof script used real `time.sleep()` delays inside `has_password`/
`ensure_user` to open the race window, which is fine for a throwaway script but would make the
committed suite flaky/slow. A Barrier gives the same guarantee (all N threads provably interleave
at the exact spot the bug lived) without any wall-clock assumption.

**How to apply:** any future TOCTOU/race regression test on this harness — grep the production
code for the pre-check function called unconditionally right before the real atomic decision,
add a `sync_barrier`-style hook there (only active when the test sets it, so unrelated tests pay
zero cost), and drive it with real OS threads + `threading.Barrier` on the client side too.

See also [[fake-store-atomicity-per-statement]].
