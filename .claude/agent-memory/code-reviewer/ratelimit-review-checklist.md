---
name: ratelimit-review-checklist
description: What to probe first when reviewing a rate limiter / counter cache in meeting_ai (trusted IP headers, key normalization, eviction policy, purge wiring)
metadata:
  type: project
---

Reviewing any rate limiter here, run these four probes before reading anything else — each one found a
real bug in BUG-010 (branch `fix/backlog-10-login-rate-limit`, 2026-09-17):

1. **Who can choose the key?** A `trust_proxy` flag is not enough: the header *list* must differ per
   deployment. `X-Vercel-Forwarded-For` is unspoofable only on Vercel; behind nginx (`TRUST_PROXY=1`,
   which the README tells self-hosters to set) any client can send it and pick its own bucket.
2. **Is the key normalized?** Regex "IP shape" checks (`^[0-9a-fA-F:.]{3,45}$`) accept `aaa`, `....`,
   `1.2.3.4:56789` (Azure/ARR appends ports) and every IPv6 spelling of one address. Use
   `ipaddress.ip_address()` and bucket IPv6 by /64, or the counter is free to bypass.
3. **Eviction policy under saturation.** "Drop the least-hit key" keeps hot entries but means a *new*
   key can never become hot once the dict is full of count-1 entries: a stable sort puts the key just
   inserted last among the ties, so `hit()` evicts it inside its own call. Probe it — 30 consecutive
   hits on one key returned 0.0 with 4096 live entries.
4. **Does the cleanup path have a caller?** `pgstore.purge_expired()` still has zero call sites, so any
   new `delete ... where expires_at < now()` added to it is dead code and the table grows forever.

**Why:** the in-memory layer is the documented fallback when Postgres is down/not migrated, so a silent
bypass there is a real hole, not a nit. **How to apply:** write a 20-line probe that imports the module
and calls it directly (no server needed); `python -m compileall` + the 64-test suite will not catch any
of the four.
