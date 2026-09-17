---
name: toctou-atomic-claims
description: How to close read-then-write races in meeting_ai signup/claim paths (autocommit pool, single-statement claims), the disclosure oracle such a fix can open, and how to prove both without Postgres
metadata:
  type: project
---

`web/db.py` `connect()` is `autocommit=True` with `max_size=4`; a multi-statement transaction across
scrypt hashing would pin a quarter of the pool. Close TOCTOU with **one statement whose `where` carries
the condition**, in the style of the old `redeem_invite` — never with a lock table or a retry loop.

**Why:** BUG-012 (invite redemption) — `select` then create-user then `update` with the boolean thrown
away gave N accounts per invite; the same shape in `first_run = count_users() == 0` gave N admins.

**How to apply:**
- Claim BEFORE creating anything, attach ids after. Losing the claim then costs nothing (no orphan
  user); the accepted cost is a burned invite if the next step dies.
- A `where not exists (select 1 from users)` does NOT serialize two inserts of *different* rows under
  READ COMMITTED — both pass. To make two sessions collide you need a real unique key:
  `insert into meeting_ai.settings … on conflict (key) do update set … where settings.value = excluded.value`
  latches "first admin" on the settings PK and stays retry-safe for the same email.
- Keep the old cheap `select` only for nicer error text, and say so in a comment — otherwise the next
  reader will trust it again.
- Watch the status codes: an early-return pre-check (403) plus an atomic failure (409) means the *same*
  failure answers differently sequentially vs. concurrently. My first proof run caught exactly that.

**Proving it without a DB** (no Postgres on this machine): fake store where each method takes one
`threading.Lock` (= one statement), `threading.Barrier` + a `time.sleep` in the step just before the
atomic point to widen the window, N threads over real HTTP. Count how many threads passed the
pre-check simultaneously and assert >= 2 — otherwise the test may be serial and proves nothing.
Always run the same harness against the OLD logic (bind a copy of the old handler to
`server.Handler._signup`) as a negative control: it printed 6 accounts / 6 admins.
See [[cloud-mode-test-recipe]] for the five bindings to patch.

**An early return you remove was probably doing two jobs.** Moving the authorisation decision
later (the whole point of "claim before create") makes every guard the old early return
short-circuited newly reachable. In `_signup` the old "invalid invite -> 403" also hid the
`has_password()` 409, so once it went away an anonymous caller could send a junk invite plus a
target email and read the two different 409 texts as "does this account exist?". The fix that
closed the race opened an enumeration oracle; the original code even had a comment saying that
guard existed to prevent enumeration, and I still missed it on the first pass.
- Gate the newly reachable check on the pre-check result (`if (first_run or invite_ok) and
  store.has_password(email)`) rather than moving it after the claim — keeps the precise message
  for legitimate invite holders and does not burn their invite on a duplicate-email typo.
- Safe only because invite state is one-way (free -> claimed) and `code_hash` is the PK, so
  `invite_ok == False` can never be followed by a successful claim (no password-overwrite path).
  Write that argument down; it is the thing a reviewer will ask about.
- Add the oracle probe to the proof harness, not just the race probe: same junk credential
  against an existing and a non-existing email must return byte-identical strings.
