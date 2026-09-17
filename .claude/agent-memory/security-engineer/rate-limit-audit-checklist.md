---
name: rate-limit-audit-checklist
description: What actually defeats a per-IP rate limiter in meeting_ai (reset-on-success, spoofable proxy header, unparsed IP key, silent fail-open) and the six probes that prove it
metadata:
  type: project
---

Auditing any rate limiter here: the happy path is almost always right; the bypasses live in
key derivation, the reset rule, and the failure mode. Findings from BUG-010 (2026-09-17,
report `docs/runbooks/security/AUDIT-2026-09-17-login-rate-limit.md`).

**Why:** the BUG-010 dev proved A-H in the ticket and every claim held when I re-ran it —
yet four of my five worst findings were in behaviour the ticket never tested.

**How to apply — run these six probes before reading anything else:**
1. *Reset rule.* If success clears the bucket, loop `limit-1` failures + 1 valid login. In
   meeting_ai this gave 45 password guesses against another account with zero 429s. Any
   "success clears the counter" rule = no limit for anyone holding one account. Fix shape:
   second, non-resettable ceiling per IP.
2. *Key source.* Trusting `X-Vercel-Forwarded-For` is only safe *on Vercel*; nginx/Cloudflare
   pass that header through from the client verbatim, so `TRUST_PROXY=1` self-hosted =
   attacker picks their own key. Rightmost `X-Forwarded-For` is the correct read for nginx.
3. *Key shape.* A regex is not an IP parser. `^[0-9a-fA-F:.]{3,45}$` accepts `....`, `:::`,
   `1.2.3.4:5678` (proxies that append a port silently disable the limiter) and treats
   `::ffff:X` as a different bucket from `X`. Use `ipaddress.ip_address()` and truncate IPv6
   to /64 — a /128 key means one client with a normal /64 has unlimited buckets.
4. *Cleanup.* Grep the cleanup function for call sites. `pgstore.purge_expired()` has none, so
   every counter row is permanent.
5. *Fail-open.* Force the store call to raise and re-run with the process cache cleared before
   each request (that is the serverless shape): 60 requests, 60 scrypt, 0 blocks, 0 log lines.
   Fail-open is defensible; fail-open *silently* is not.
6. *Shared egress IP.* 11 requests lock out everyone behind one NAT, and the "success clears
   the bucket" escape hatch cannot fire once blocked — nobody can succeed. Default here is
   `TRUST_PROXY=0`, so any self-hosted box behind a proxy is one bucket for the whole org.

**Harness:** reuse the five-patch cloud recipe (see [[MEMORY]] 2026-09-16 entry) but give the
fake store a *real* `hashlib.scrypt` with `pgstore._SCRYPT` params and an in-memory twin of the
`rate_hit` SQL. Counting scrypt calls is what proves "no expensive work after the block".
Timing medians over 10 reps are enough to rule out an enumeration oracle (0.5 ms vs 50 ms).
Windows note: a 429 sent before reading a large body arrives as `ConnectionAbortedError`
(RST), so test big bodies explicitly — the fix is a bounded drain before close.
