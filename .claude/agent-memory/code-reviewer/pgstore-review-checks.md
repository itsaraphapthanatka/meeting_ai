---
name: pgstore-review-checks
description: What to verify first when reviewing new pgstore SQL in meeting_ai — schema defaults, single writer of a column, ambiguous state rows, connection held across scrypt
metadata:
  type: project
---

Checks that paid off reviewing `claim_invite` / `attach_invite` / `claim_first_admin` (BUG-012),
in order:

1. New predicate on an existing column (`used_at is null`): read `web/schema.sql` for a DEFAULT
   and grep every writer. A `default now()` would have made every existing invite unclaimable —
   here `used_at timestamptz` is nullable with no default and only these functions write it, so
   no migration is needed.
2. Splitting one write into claim + attach creates a **new intermediate row state**
   (`used_at set, used_by null`). Ask whether that state is distinguishable from the failure
   state — the ticket's recovery SQL could not tell "signup failed" from "attach failed" and
   would have re-opened a consumed invite.
3. Placeholder count vs params, and param order vs SQL order — cheap, do it every time.
4. `db.connect()` is autocommit with `max_size=4`; `pgstore.set_password` computes scrypt
   (~100 ms) *inside* the `with db.connect()` block while `verify_password` does it outside.
   Any new code between two writes inherits that pool pressure.
5. Borrowing a shared table (`settings`) as a mutex works (primary key serialises), but check
   `get_setting`/`set_setting` call sites and the admin route: today `_update_settings` only
   handles one hard-coded key, so no UI can clear the latch. A generic settings editor later
   would re-open first-run.

Related: [[early-return-hides-oracle]].
