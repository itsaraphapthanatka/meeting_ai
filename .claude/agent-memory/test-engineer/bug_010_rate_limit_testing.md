---
name: bug-010-rate-limit-testing
description: How to test the two-layer (in-memory + FakeStore/Postgres) auth rate limiter without wall-clock timing or real scrypt
metadata:
  type: project
---

BUG-010 added `web/ratelimit.py` (in-process fixed window) plus `pgstore.rate_hit`/`rate_reset`
(shared counter, needed because Vercel invocations don't share memory) guarding `_login`/`_signup`
in `meeting_ai/web/server.py` before any `verify_password` (scrypt) call. Regression suite:
`tests/test_bug_010_login_rate_limit.py`.

**Why this needed `_harness.py` changes, not just a new test file:** `FakeStore` had no auth
methods at all (no `verify_password`, `ensure_user`, `set_password`, `create_session`,
`has_password`, `invite_email`, `redeem_invite`, `create_invite`) and no `rate_hit`/`rate_reset`.
Without `rate_hit`/`rate_reset`, `_rate_limited()` in server.py hits an `AttributeError`, catches
it, and silently fails open to the in-memory layer only — a test suite that never adds these two
methods will pass 100% while never exercising the DB layer at all. Added all of the above to
`FakeStore` (fake scrypt-free password check, tracks `verify_password_calls` for assertions) plus
a `headers=` passthrough on `_HttpCaseMixin._do`/`post_json` (needed for `X-Forwarded-For` tests)
and a `post_raw()` helper (needed to send a literal 0-byte body).

**How to prove both layers independently** (the property the dev explicitly flagged): don't just
run the endpoint once — run it twice, each time neutralizing one layer so the other is the only
thing that can produce the 429:
- in-memory alone: `self.store.rate_hit = lambda key, limit, window: 0.0` (DB always says "fine")
  and confirm the block still happens.
- DB alone: call `ratelimit.clear()` before *every single request* in the loop (simulates a fresh
  Vercel invocation with no shared process memory) and confirm `FakeStore.rate_hit`'s own counter
  still blocks at the threshold.
If you only run the combined path once, a broken/absent DB layer is invisible.

**The property test that actually matters:** don't just assert the 11th request is 429 — assert
`len(store.verify_password_calls)` stops growing once blocked. A regression that moves the rate
check to *after* `verify_password()` still returns the right status codes (401 then 429) and would
pass a status-only test, but keeps calling scrypt on every blocked request. Verified this catches
it by monkeypatching `Handler._login` locally (never committed) to call `verify_password` before
`_rate_limited()` and watching only this assertion fail while status-code assertions still passed.

**Timing:** never assert wall-clock durations for rate-limit tests (flaky by machine load). The
ticket's own proof numbers (scrypt ~42.8ms vs blocked request ~0.94ms) belong in the report, not
in an assertion — count calls instead (`verify_password_calls`).

See also [[test-engineer-p0-harness]] for the general `_harness.py` conventions (env-before-import,
`ratelimit.clear()` in setUp because it's module-level state, CloudCase seeding).

**2026-09-17 review round 2** — code-reviewer found three more bypasses that the round-1 suite
missed entirely; added `tests/test_bug_010_login_rate_limit_bypasses.py` (8 tests, 81 total):
1. `Handler.forwarded_headers` (class attr, default `("X-Forwarded-For",)`) replaced a hardcoded
   `X-Vercel-Forwarded-For` check inside `_client_ip` — only `api/index.py`'s subclass should
   trust that header. Tested both the base Handler (spin up a *second* `server.Server` bound to a
   throwaway subclass in the test itself — reuse `self.post_json` by temporarily swapping
   `self.port`, no need to touch `_harness.py` for this).
2. `server._ip_key()` is a pure function (no HTTP needed) — table-test it directly: strips a
   trailing `:port` (Azure App Gateway / IIS ARR append one and it used to zero the limiter since
   `ip:port` was treated as a unique key per connection), collapses IPv6 to /64, unwraps
   `::ffff:a.b.c.d`, and returns `""` for junk (regex before this accepted `999.999.999.999`).
3. `ratelimit._prune` used to evict the very key being inserted in its own `hit()` call when the
   table was full of higher-count keys (sorted by count desc, kept top N) — a hot key under attack
   from a saturated table would get created→evicted→recreated forever, returning `0.0` always.
   Reproduce by filling `MAX_KEYS` filler keys with a higher count than the target *before* hitting
   the target repeatedly, then assert the target is still in `ratelimit._hits` and did eventually
   block. Also assert a `block()`ed key survives a flood of `MAX_KEYS+`-many new keys — this one is
   only meaningfully non-vacuous against insertion-order/FIFO-style eviction bugs, not the specific
   historical regression (a `_BLOCKED` count of `1<<30` naturally outranks any real limit under both
   old and new sort-by-count logic) — proved it via a hand-written "evict oldest inserted" variant.
4. A valid `mai_session` cookie should skip only the soft/narrow bucket (10/15min, cleared on
   success), never the hard bucket (60/hour, never cleared) — otherwise one real account makes
   scrypt unlimited. `self.user` is resolved in `_route()` before `_api()`/`_login()` runs, so
   `cookies={"mai_session": tok}` in `post_json` is enough; no harness change needed.

General lesson: when a coordinator says "these bypassed the suite", always diff the actual
app files first (`git diff -- <paths>`) before writing anything — the fix already changed
function names/signatures (`_rate_key`→`_rate_buckets`/`_bucket_hit`, new `_ip_key`,
`forwarded_headers`) and guessing from memory would have produced tests for code that no longer
exists.
