# Security audit — login/signup rate limit (BUG-010, branch `fix/backlog-10-login-rate-limit`) — 2026-09-17

**Scope / method:** `git diff main..HEAD` ของสาขา `fix/backlog-10-login-rate-limit`
(`meeting_ai/web/ratelimit.py` ใหม่, `pgstore.rate_hit/rate_reset` + ตาราง `rate_limits`,
`Handler.trust_proxy` / `_client_ip` / `_rate_key` / `_rate_limited` / `_rate_ok` / `_too_many`
ใน `server.py`, `config.trust_proxy`, `api/index.py`, `schema.sql`, `README.md`)
เทียบกับ `docs/tickets/BUG-010-login-rate-limit.md`

วิธีพิสูจน์: รัน `server.Server` จริงในโพรเซสเดียวบน **127.0.0.1 พอร์ต 55490-55497**
(โหมด cloud จำลองตามสูตรใน PROJECT-CONTEXT: patch `backend.cloud/backend.store/jobs.cloud/
jobs.store/server.store` + `Handler.trust_proxy`) store ปลอมทำ **scrypt พารามิเตอร์เดียวกับ
`pgstore._SCRYPT`** ของจริง และมีตัวนับกลางที่จำลอง semantics ของ SQL ใน `rate_hit()` ทีละบรรทัด
ล้าง `S3_*` ก่อนทุกครั้ง ไม่แตะ production, ไม่แตะ `recordings/`, ไม่ต่อ Postgres จริง
(เครื่องนี้ไม่มี `psql` / Docker daemon ไม่ได้เปิด — SQL ตรวจด้วยการอ่านโค้ดเท่านั้น)

**สรุป:** Critical 0 · High 2 · Medium 4 · Low 3
ตัวจำกัดอัตรา **ทำงานได้จริงตามที่ตั๋วอ้างในเส้นทางหลัก** (ผมยืนยันซ้ำได้ทุกข้อ รวมเรื่องเวลา)
แต่มีสามช่องที่ทำให้มัน "ไม่ได้กันอะไร" ในสถานการณ์ที่เกิดขึ้นจริง: การล้างตัวนับเมื่อล็อกอินสำเร็จ,
การเชื่อหัวข้อ `X-Vercel-Forwarded-For` นอก Vercel, และคีย์ที่ไม่ได้ถูก parse เป็น IP จริง

---

## Findings (เรียงตามความรุนแรง)

### 1. [High] ล็อกอินสำเร็จหนึ่งครั้ง = รีเซ็ตโควตาฟรี → เดารหัสผ่านคนอื่นได้ไม่จำกัดจาก IP เดียว

**เหตุผลของระดับ:** ใครก็ตามที่มีบัญชีใช้ได้หนึ่งใบ (พนักงานเก่า, บัญชีที่หลุดมาจาก list,
คนในทีมเอง) ลบเพดานทิ้งได้ทั้งหมด — ทั้งภัยที่ตั๋วตั้งใจปิด (credential stuffing และ scrypt
amplifier) กลับมาครบ

**Location:** `meeting_ai/web/server.py:860` (`self._rate_ok("login")`), `server.py:244-252`,
`meeting_ai/web/ratelimit.py:71` (`reset`), `meeting_ai/web/pgstore.py:206` (`rate_reset` = `delete`)

**Proof** (`exp2.py`, พอร์ต 55491 — วน 5 รอบ: เดารหัสของ `victim@example.com` 9 ครั้ง แล้วล็อกอิน
ด้วยบัญชีของตัวเอง 1 ครั้ง):

```
round 1: 9 guesses ok, own login -> 200 cookie=yes central_rows={}
round 2: 9 guesses ok, own login -> 200 cookie=yes central_rows={}
...
total guesses against victim = 45, no 429 at any point; scrypt ops burned = 50
```

ตัวนับกลางถูก **ลบทิ้งทั้งแถว** ทุกครั้งที่สำเร็จ (`central_rows={}`) จึงเริ่มนับหนึ่งใหม่เสมอ
เพดานจริงจึงไม่ใช่ "10 ครั้ง/15 นาที" แต่เป็น "9 ครั้งต่อการล็อกอินสำเร็จ 1 ครั้ง" = ไม่จำกัด

**Impact:** ผู้ใช้ทุกคนในระบบ — รหัสผ่านของทุกบัญชีถูกเดาออนไลน์ได้ไม่จำกัดจาก IP เดียว
บัญชีที่แตกแล้วเปิดทางไปยังไฟล์เสียง/ถอดเสียง/สรุปประชุม (PII เต็มรูปแบบ) ส่วนบน Vercel
งาน scrypt (~43 ms + ~16 MB ต่อครั้ง) ที่ไม่มีเพดานคือค่า function invocation และคิวที่ยาวขึ้น
ของผู้ใช้จริง — ไม่มีเงินเคลื่อนในระบบนี้ แต่บิล Vercel/Neon เคลื่อน

**Fix (backend-dev):** อย่าล้างถังเมื่อสำเร็จ ใช้สองชั้นแทน
1. ถัง "ความพยายามที่ล้มเหลว" ต่อ IP 10/15 นาที — ล้างได้เมื่อล็อกอินสำเร็จ (คงพฤติกรรมเดิมเพื่อ UX)
2. ถัง "ราคารวม" ต่อ IP ที่ **ความสำเร็จไม่ล้าง** เช่น 60 ครั้ง/ชั่วโมง นับทุกคำขอบนเส้น auth
   (`_rate_limited` เรียกทั้งสองถัง, `_rate_ok` ล้างเฉพาะถังแรก)
เพดานชั้นที่สองคือสิ่งเดียวที่กัน "คนมีบัญชี" ได้ และไม่ทำให้ออฟฟิศปกติเดือดร้อน (60 ครั้ง/ชม.
สูงกว่าการใช้งานจริงมาก)

---

### 2. [High] `X-Vercel-Forwarded-For` ถูกเชื่อในทุก deployment ที่ตั้ง `TRUST_PROXY=1` ไม่ใช่เฉพาะ Vercel

**เหตุผลของระดับ:** README ของสาขานี้สั่งให้คนที่รันเองหลัง nginx/Cloudflare ตั้ง `TRUST_PROXY=1`
— พอตั้งแล้ว ผู้โจมตีนิรนามเลือกคีย์ของตัวเองได้ = ข้ามตัวจำกัดทั้งระบบ และเขียนแถวใหม่ลง
`rate_limits` ได้ตามใจ

**Location:** `meeting_ai/web/server.py:210-218` (`for name in ("X-Vercel-Forwarded-For",
"X-Forwarded-For")`), `README.md:238`, `api/index.py:33`

nginx/Cloudflare เขียนทับหรือ **ต่อท้าย** `X-Forwarded-For` เท่านั้น (ตัวขวาสุดจึงเชื่อได้จริง
— ส่วนนี้ dev ทำถูก) แต่ไม่มี proxy ตัวไหนรู้จัก `X-Vercel-Forwarded-For` มันถูกส่งผ่านไป
ถึง `Handler` ตรงๆ จากผู้เรียก และโค้ดอ่านมัน **ก่อน** เสมอ

**Proof** (`exp3.py`, พอร์ต 55492, `trust_proxy=True` — 40 คำขอ แต่ละครั้งเปลี่ยนหัวข้อเอง):

```
POST /api/auth/login   X-Vercel-Forwarded-For: 9.9.0.<i>
statuses: [401] count401 = 40 count429 = 0
central rows created: 40  e.g. ['login:9.9.0.0', 'login:9.9.0.1', 'login:9.9.0.2']
```

**Production Vercel ไม่โดนช่องนี้** — เอกสาร Vercel ระบุว่า "we currently overwrite the
`X-Forwarded-For` header and **do not forward external IPs**. This restriction is in place to
prevent IP spoofing" และ `x-vercel-forwarded-for` คือค่าเดียวกัน (https://vercel.com/docs/headers/request-headers)
ผมทดสอบกับ production ไม่ได้ (ห้ามตามกติกา) จึงถือเอกสารเป็นหลักฐาน — ความเสี่ยงที่เหลือคือ
deployment แบบ self-hosted ซึ่ง README เพิ่งแนะนำให้เปิด `TRUST_PROXY=1`

**Impact:** self-hosted ที่ทำตาม README = ไม่มี rate limit เลย (เดารหัสไม่จำกัด + เผา scrypt
ไม่จำกัด) และตาราง `rate_limits` โตตามใจคนนอก

**Fix (backend-dev):** แยก flag สองตัว — `trust_proxy` (อ่าน `X-Forwarded-For` ตัวขวาสุด) กับ
`vercel_headers` ที่ `api/index.py` เท่านั้นตั้งเป็น `True` (หรือเช็ค `os.environ.get("VERCEL")`
ซึ่ง Vercel ตั้งให้ในรันไทม์) แล้วอ่าน `X-Vercel-Forwarded-For` เฉพาะเมื่อ flag ตัวหลังเป็นจริง

---

### 3. [Medium] `_IP_RE` ไม่ได้ตรวจว่าเป็น IP — คีย์ขยะผ่านได้ และ proxy ที่ใส่พอร์ตทำให้ข้ามได้เงียบๆ

**เหตุผลของระดับ:** เป็นตัวกันที่ dev ใส่มาเพื่อปิดปัญหา "แถวงอกไม่จำกัด" โดยตรง แต่มันไม่ได้กัน
และในสภาพแวดล้อมที่ proxy ต่อ `:port` ท้ายที่อยู่ ตัวจำกัดจะเงียบไปทั้งตัวโดยไม่มีใครรู้

**Location:** `meeting_ai/web/server.py:51` — `_IP_RE = re.compile(r"^[0-9a-fA-F:.]{3,45}$")`

**Proof** (`exp3.py`):

```
'999.999.999.999'  -> ACCEPT      '....'              -> ACCEPT
':::'              -> ACCEPT      'abc'               -> ACCEPT
'dead.beef.cafe'   -> ACCEPT      'a'*45              -> ACCEPT
'127.0.0.1:8080'   -> ACCEPT      '1.2.3.4.5.6.7.8.9' -> ACCEPT
'::ffff:203.0.113.9' / '::FFFF:203.0.113.9' / '0:0:0:0:0:ffff:cb00:7109'
        -> คนละถังกัน ทั้งที่เป็นที่อยู่เดียวกับ 203.0.113.9
```

แถวที่เกิดจริงจากการยิงหกครั้ง: `login:....`, `login::::`, `login:dead.beef`,
`login:aaaa…(45)`, `login:cafe:babe`
ตัวอักษรที่ยอมรับมี 24 ตัว ยาว 3-45 → จำนวนคีย์ที่เป็นไปได้มากกว่าจำนวนแถวที่ Postgres ไหว

**Impact:** (ก) เมื่อรวมกับข้อ 2 = แถวใน `rate_limits` งอกได้ไม่จำกัด (ข) proxy ที่ส่ง `ip:port`
(Azure Application Gateway และ CDN บางเจ้าทำแบบนี้) ทำให้ทุกการเชื่อมต่อได้ถังของตัวเอง =
ไม่มีการจำกัดเลย โดย log ไม่บอกอะไร (ค) `::ffff:` mapped form แยกถังจาก IPv4 ปกติ

**Fix (backend-dev):**

```python
import ipaddress
try:
    ip = ipaddress.ip_address(addr.strip("[]").rsplit(":", 1)[0] if addr.count(":") == 1 else addr)
except ValueError:
    continue                       # ไม่ใช่ IP → ตกไปใช้ client_address
if ip.version == 6:
    ip = ipaddress.ip_network(f"{ip}/64", strict=False).network_address   # ดูข้อ 4
return str(ipaddress.ip_address(ip).ipv4_mapped or ip)
```

`ipaddress` เป็น stdlib จึงไม่ผิดกติกาของโปรเจกต์

---

### 4. [Medium] IPv6: คีย์เป็นที่อยู่ /128 — ลูกค้าหนึ่งรายมี /64 จึงมีถังไม่จำกัด (มีผลกับ production Vercel)

**เหตุผลของระดับ:** ใช้ได้กับ production จริง ไม่ต้องปลอมหัวข้อใดๆ แค่เป็นลูกค้า IPv6 ปกติ
(ISP/VPS แจก /64 หรือใหญ่กว่าเป็นมาตรฐาน)

**Location:** `meeting_ai/web/server.py:203-221` (`_client_ip` คืนสตริงที่อยู่ดิบ),
`server.py:224` (`_rate_key`)

**Proof** (`exp7.py`, พอร์ต 55496 — 50 คำขอจาก 50 ที่อยู่ใน /64 เดียว):

```
50 guesses from one /64: [401]  429s = 0
rate_limits rows created by one client: 50
scrypt ops bought with 50 requests: 50
```

**Impact:** ผู้โจมตีที่มี IPv6 /64 (ราคาเท่ากับ VPS หนึ่งเครื่อง) เดารหัสได้ไม่จำกัดและเผา
scrypt ได้ไม่จำกัดบน production พร้อมกับเขียนแถวใหม่ลง Neon ทุกคำขอ

**Fix:** ตัดเป็น /64 ตามโค้ดในข้อ 3 (IPv4 คงเป็น /32 ตามเดิม)

---

### 5. [Medium] แถวใน `rate_limits` ไม่เคยถูกลบ — `purge_expired()` ไม่มีที่เรียกในทั้ง repo

**เหตุผลของระดับ:** ทำให้ข้อ 2/3/4 กลายเป็นความเสียหายถาวรกับฐานข้อมูล production ไม่ใช่แค่ชั่วคราว

**Location:** `meeting_ai/web/pgstore.py:171-174` (เพิ่ม `delete … rate_limits` เข้าไปใน
`purge_expired`) — `grep -rn "purge_expired" --include=*.py` ทั้ง repo คืนบรรทัด **นิยามอย่างเดียว**
ไม่มี call site (ข้อนี้ตรงกับ "Ops" ใน PROJECT-CONTEXT: `purge_expired()` ไม่เคยถูกเรียก)

**Proof:**

```
$ grep -rn "purge_expired" --include=*.py .
./meeting_ai/web/pgstore.py:171:def purge_expired() -> None:
```

**Impact:** ทุก IP ที่เคยล็อกอิน (และทุกคีย์ขยะจากข้อ 2-4) ทิ้งแถวไว้ตลอดกาล — พื้นที่และ
index บวมบน Neon, ต้นทุนเพิ่ม, และการยิงหนึ่งครั้ง = หนึ่ง INSERT ที่ไม่มีวันถูกเก็บกวาด

**Fix (backend-dev):** เก็บกวาดแบบฉวยโอกาสในเส้นเดิม เช่นใน `rate_hit()` สุ่ม 1/500 ครั้งให้รัน
`delete from meeting_ai.rate_limits where expires_at < now()` หรือเรียก `purge_expired()`
จาก reaper ที่มีอยู่แล้ว (devops: ถ้าใช้ Neon ตั้ง cron ก็ได้)

---

### 6. [Medium] Fail-open แบบเงียบสนิทเมื่อยังไม่ได้ `./mai db-init` — บน Vercel = ไม่มี rate limit เลย

**เหตุผลของระดับ:** โอกาสเกิดสูง (deploy โค้ดก่อน migrate คือลำดับปกติ) ตรวจจับไม่ได้เลย
และผลคือ backlog ขึ้นว่า "แก้แล้ว" ทั้งที่ระบบยังเปิดโล่ง — ซึ่งตั๋วเองบอกว่าแย่กว่าไม่แก้

**Location:** `meeting_ai/web/server.py:233-239`

```python
try:
    wait = store.rate_hit(key, AUTH_RATE_LIMIT, AUTH_RATE_WINDOW)
except Exception:
    return 0.0        # ไม่ log, ไม่นับ, ไม่มีสัญญาณใดๆ
```

**Proof** (`exp4.py`, พอร์ต 55493 — `rate_hit` โยน `relation "meeting_ai.rate_limits" does not
exist` ทุกครั้ง, ล้างหน่วยความจำก่อนทุก request เพื่อจำลองคนละ invocation ตามทรง Vercel):

```
=== A) โพรเซสเดียวอยู่ยาว (self-hosted) ===
[401 ×10, 429, 429]   scrypt = 10          ← ชั้นหน่วยความจำยังกันได้
=== B) ทรง serverless (หน่วยความจำว่างทุกคำขอ) ===
statuses: [401]  count401 = 60  count429 = 0   scrypt ops = 60
rate_hit attempts that raised: 70 -> all swallowed, nothing logged
```

**Impact:** production (Vercel) ที่เจ้าของยังไม่ได้รัน `./mai db-init` จะไม่มีการจำกัดใดๆ
ตลอดไป และไม่มีใครรู้ — ไม่โผล่ใน `/api/config`, ไม่โผล่ใน `backend.health()`, ไม่มีบรรทัด log

**Fix (backend-dev + devops):**
- `except Exception as e:` แล้ว `print` หนึ่งครั้งต่อโพรเซส (flag ระดับโมดูล) ว่า
  "rate limiter degraded: <ชนิด error>" — บน Vercel บรรทัดนี้จะไปอยู่ใน function log
- ใส่สถานะลง `backend.health()` (`{"rate_limit": "db" | "memory-only"}`) เพื่อให้ตรวจได้จากภายนอก
- ใน `_cmd_db_init` พิมพ์รายชื่อตารางที่คาดหวังแต่ไม่พบอยู่แล้ว — เพิ่ม `rate_limits` เข้าเช็กลิสต์
  หลัง deploy ของ runbook

---

### 7. [Medium] คีย์เป็น IP อย่างเดียว → คนนอกหนึ่งคนล็อกการล็อกอินของทั้งออฟฟิศได้ด้วย 11 คำขอ/15 นาที

**เหตุผลของระดับ:** ขัดข้อกำหนดข้อ 4 ของตั๋วเอง ("ผู้ใช้จริงต้องไม่ถูกล็อก") และค่า default
ของ `TRUST_PROXY` คือ `0` — self-hosted หลัง nginx ที่ลืมตั้ง env จึงเป็นถังเดียวทั้งองค์กร

**Location:** `meeting_ai/web/server.py:224` (`_rate_key` = `scope:ip`),
`meeting_ai/config.py:67` (`TRUST_PROXY` default `"0"`), `README.md:238`

**Proof** (`exp6.py`, พอร์ต 55495 — ย่อ window เหลือ 3 วินาทีเพื่อให้รันจบ semantics เดิมทุกอย่าง):

```
=== ผู้โจมตีใช้ IP ขาออกเดียวกับออฟฟิศ ยิง 11 ครั้ง ===
  legit user alice@example.com with the CORRECT password -> 429 cookie=NO
  legit user bob@example.com   with the CORRECT password -> 429 cookie=NO
=== ยืดเวลา: ยิงซ้ำ 11 ครั้งทุกครั้งที่หน้าต่างหมดอายุ ===
  cycle 0: right after expiry alice=200; after 11 attacker requests alice=429
  cycle 1: right after expiry alice=200; after 11 attacker requests alice=429
  cycle 2: right after expiry alice=200; after 11 attacker requests alice=429
```

ทางออกที่ตั๋วบอกไว้ ("ล็อกอินสำเร็จล้างตัวนับ จึงแทบไม่มีทางชนเพดาน") **ทำงานไม่ได้ตอนที่ต้องการ
มันที่สุด** — เมื่อถังเต็มแล้ว ไม่มีใครล็อกอินสำเร็จได้ จึงไม่มีอะไรมาล้างถัง (ยืนยันใน exp1 ข้อ C
ด้วย: รหัสถูกต้อง 100% → 429, ไม่มี `Set-Cookie`)

**Impact:** self-hosted หลัง proxy ที่ยังไม่ได้ตั้ง `TRUST_PROXY=1` (ค่าเริ่มต้น) — คนนอกนิรนาม
ปิดการล็อกอินของทั้งองค์กรได้ต่อเนื่องด้วยต้นทุน 11 คำขอ/15 นาที; บน production Vercel ผลกระทบ
แคบกว่า (ต้องอยู่หลัง NAT/CGNAT เดียวกับเหยื่อ) แต่ออฟฟิศที่ออกเน็ต IP เดียวชนกันเองได้จริง

**Fix (backend-dev):**
- เมื่อถังเต็ม อย่าปฏิเสธ 100% — ปล่อยให้ผ่านแบบหยดน้ำ 1 คำขอ/60 วินาทีต่อ IP (เพิ่มงาน scrypt
  สูงสุด ~14 ครั้ง/15 นาที ซึ่งยังถูกกว่าเดิมมาก) ผู้ใช้จริงที่รหัสถูกจะเข้าได้ภายในหนึ่งนาที
  และการล้างถังเมื่อสำเร็จจะกลับมาใช้งานได้จริง
- เตือนตอนสตาร์ท: ถ้า `--host` ไม่ใช่ 127.0.0.1 และ `trust_proxy` เป็นเท็จ ให้ `print` ว่า
  "ทุกคำขอจะถูกนับเป็น IP เดียวกัน — ตั้ง TRUST_PROXY=1 ถ้าอยู่หลัง reverse proxy"
- เก็บ `ipaddress` fix จากข้อ 3/4 ไว้ด้วย ไม่งั้น /64 เดียวยังเลี่ยงได้

---

### 8. [Low] คำขอที่ถูกบล็อกและพก body ใหญ่มาด้วย ได้ TCP reset แทน 429

**Location:** `meeting_ai/web/server.py:254-268` (`_too_many` ปิดการเชื่อมต่อโดยไม่อ่าน body ทิ้ง)

**Proof** (`exp5.py` ข้อ B, พอร์ต 55494 — body 2 MB, ลอง 3 ครั้ง, Windows):

```
attempt 0: EXCEPTION ConnectionAbortedError: [WinError 10053] ...
attempt 1: EXCEPTION ConnectionAbortedError: [WinError 10053] ...
attempt 2: status=429 body=b'{"error": "\xe0\xb8\x9e\xe0\xb8\xa2...'
```

ปิดซ็อกเก็ตขณะยังมีข้อมูลค้างใน receive buffer ทำให้ระบบส่ง RST ซึ่งทิ้ง response ที่เขียนไปแล้ว
(บน Linux ผู้เรียกจะเห็น `ECONNRESET`) — ผู้ใช้เห็น "network error" แทนข้อความไทย

**Impact:** ต่ำ เพราะ body ล็อกอินของ `app.js` เล็กมาก (~100 ไบต์ ทดสอบแล้วได้ 429 ครบทุกครั้ง)
แต่ client ใดก็ตามที่ส่ง body ใหญ่บนเส้นนี้จะไม่ได้เห็นเหตุผลว่าทำไมถูกปฏิเสธ

**Fix:** ก่อน `self._send(...)` ใน `_too_many()` ให้ดูดทิ้งแบบมีเพดาน
`self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536))`
(ราคาคงที่ ≤ 64 KB, ยังคง `Connection: close` ไว้เหมือนเดิม)

---

### 9. [Low] `_signup` เป็นถังแยก: งาน scrypt สูงสุดต่อ IP เป็น 20 ครั้ง/15 นาที (เฉพาะคนที่ถือรหัสเชิญ)

**Location:** `meeting_ai/web/server.py:866` (ถัง `signup`), `server.py:882-897`

การแยกถังเป็นการตัดสินใจที่สมเหตุผล (ไม่ให้การสมัครไปกินโควตาการล็อกอิน) แต่ตัวเลขจริงคือ
10 (login) + 10 (signup) = สูงสุด 20 งาน scrypt ต่อ IP ต่อ 15 นาที ไม่ใช่ 10
**ข้อแก้ให้ตั๋ว:** คนที่ **ไม่มี** รหัสเชิญที่ใช้ได้ ไปไม่ถึง `set_password` เลย — `store.invite_email()`
คืนไม่ผ่านแล้ว 403 ที่ `server.py:886` ก่อน (`server.py:897` คือจุดเดียวที่ทำ scrypt)
ดังนั้น "signup เป็น scrypt amplifier สำหรับคนนอก" ตามที่คอมเมนต์ที่ `server.py:864-865` เขียนไว้
**ไม่ถูกต้อง** — สำหรับคนนอก signup คือ *DB query* amplifier (`count_users()` + `invite_email()`
= 2 query ต่อคำขอ) ซึ่งก็ยังควรจำกัดอยู่ดี แต่คอมเมนต์ควรแก้ให้ตรง
ส่วน 409 "อีเมลนี้มีบัญชีอยู่แล้ว" (`server.py:892-893`) เป็นช่องแจกแจงอีเมลที่มีมาก่อนตั๋วนี้
(ต้องถือรหัสเชิญที่ใช้ได้ถึงจะไปถึง) — ตอนนี้ถูกจำกัดที่ 10 ครั้ง/15 นาที/IP แล้ว แต่ข้อ 1-4
ปลดเพดานนี้ได้เช่นกัน
**Fix:** ไม่เร่งด่วน — ให้ถัง "ราคารวม" ในข้อ 1 ครอบทั้งสอง endpoint รวมกัน และแก้คอมเมนต์

---

### 10. [Low] คำขอที่ถูกบล็อกไม่ได้ "ไม่แตะ DB" ถ้าผู้เรียกแนบคุกกี้มั่วมาด้วย

**Location:** `meeting_ai/web/server.py:516` (`self._resolve_user()` ทำงาน **ก่อน** `_api()` จึง
ก่อน `_rate_limited` เสมอ), `server.py:295-302`, `meeting_ai/web/pgstore.py:user_for_session`

`user_for_session("")` คืน `None` ทันทีเมื่อไม่มีคุกกี้ (จึงเป็นที่มาของตัวเลข 0.5-1.4 ms ที่ผมวัดได้
และตรงกับที่ตั๋วอ้าง) แต่ถ้าผู้โจมตีส่ง `Cookie: mai_session=<ขยะ>; mai_share=<ขยะ>` มาด้วย
ทุกคำขอ **รวมคำขอที่ได้ 429** จะเสีย query ไป Postgres สองครั้ง (`sessions join users` และ
`share_target`) โดยที่ตัวจำกัดอัตราแตะไม่ถึง เพราะมันถูกเรียกทีหลัง

**Impact:** ต่ำเมื่อเทียบกับ scrypt (network roundtrip ไป Neon ไม่กี่ ms ต่อครั้ง) แต่เป็น
ทรัพยากรที่ทุก tenant ใช้ร่วมกัน และไม่มีเพดาน — ควรรู้ไว้ว่าคำกล่าว "คำขอที่ถูกบล็อกราคาเกือบศูนย์"
เป็นจริงเฉพาะเมื่อผู้เรียกไม่ส่งคุกกี้

**Fix:** ถ้าจะปิดจริงต้องย้ายการตัดสินใจ rate limit ขึ้นไปก่อน `_resolve_user()` สำหรับเส้น
`/api/auth/{login,signup}` (เช็ก `self.path` ก่อน resolve) — คุ้มค่าเฉพาะเมื่อเจอการยิงจริง
ระหว่างนี้บันทึกไว้เป็นข้อจำกัดที่รู้ตัว

---

## Verified safe (ยืนยันด้วยการรันจริง ไม่ใช่การอ่านอย่างเดียว)

| สิ่งที่อ้างในตั๋ว | ผลที่ผมรันเอง |
|---|---|
| ตัดสินใจก่อน scrypt | 12 คำขอ → `verify_password` ถูกเรียก **10 ครั้ง**; ระหว่างถูกบล็อก 30 คำขอ → **0 ครั้ง** (`exp1`) |
| 429 ไม่รั่วว่ามีบัญชีจริงไหม | "อีเมลจริง+รหัสถูก" / "อีเมลไม่มีจริง" / "body ว่าง" ได้ status, body, `Retry-After`, `Connection` **ชุดเดียวกันทุกไบต์** |
| ไม่รั่วทางเวลา | median 0.54 / 0.68 / 0.54 ms (ต่ำกว่าเส้น 401 ที่ต้อง hash ~50 ms ราว 90 เท่า) — ไม่มี oracle ทางเวลา |
| ราคาของคำขอที่ถูกบล็อก | 429 = 0.5-1.4 ms เทียบกับ 401 = 44-57 ms บนเครื่องเดียวกัน |
| ไม่มีการล็อกถาวร | หน้าต่างไม่ถูกต่ออายุตอนถูกบล็อก — พอหมดหน้าต่าง ล็อกอินถูกต้อง → 200 + `Set-Cookie` (`exp6`) |
| `Connection: close` แก้ปัญหา keep-alive จริง | คำขอ 1-10 ใช้สายเดิม (`will_close=False`), ครั้งที่ 11 `will_close=True`; ส่ง 2 คำขอ pipeline บนสายเดียวตอนถูกบล็อก → ตอบ **1** ครั้งแล้วปิด ไม่มี request desync/smuggling; raw socket ที่ประกาศ `Content-Length: 1000000` แล้วส่ง 10 ไบต์ ได้ 429 ปกติ ไม่ค้าง (`exp5` A/C/D) |
| ถูกต้องเมื่อมีการแข่งกัน | 40 คำขอพร้อมกัน 20 เธรด → 401 พอดี 10, 429 พอดี 30, scrypt 10 (`exp8`) — ตัวนับในหน่วยความจำมี lock จริงและ upsert ของ SQL เป็น atomic |
| `trust_proxy=False` ไม่เชื่อหัวข้อ | ยืนยันตามโค้ด `server.py:210` + เทสต์ของ test-engineer (`tests/test_bug_010_*.py:137`) |

อื่นๆ ที่ตรวจแล้วไม่พบปัญหา:
- **ไม่มีเส้นอื่นที่ไปถึง scrypt**: `verify_password` ถูกเรียกที่ `server.py:856` ที่เดียว,
  `set_password` ที่ `server.py:897` ที่เดียว — ทั้งคู่อยู่หลัง `_rate_limited` ไม่มี endpoint
  เปลี่ยน/รีเซ็ตรหัสผ่านในระบบ และ `/api/auth/invite` ต้องเป็นแอดมิน
- **SQL**: parameterized ครบ, `on conflict (key) do update` เป็น atomic ต่อแถว,
  `%s::double precision` cast ชัดเจนตามบทเรียน `job_claim`, `returning` คืนค่าแถวใหม่ถูกต้อง
- **connection pool ไม่เน่าเมื่อ `rate_hit` ล้มเหลว**: `db._connect_kwargs()` ตั้ง `autocommit=True`
  (`web/db.py`) แต่ละ statement จึงเป็น transaction ของตัวเอง — ตารางหายไม่ทำให้ query ถัดไป
  บน connection เดิมพังตามแบบ "current transaction is aborted"
- **ไม่มี PII / ความลับใน 429 หรือ log ใหม่**: body มีแค่ข้อความไทยกับ `retry_after`,
  `_client_ip()` ไม่ถูก log ที่ไหน, ไม่มี secret ใหม่ใน diff (`git diff main..HEAD` ทั้งก้อน)
- **`ratelimit.MAX_KEYS`**: เมื่อเกิน 4096 จะทิ้งคีย์ที่นับได้น้อยสุดก่อน คีย์ที่กำลังถูกบล็อก
  (`_BLOCKED = 1<<30`) จึงอยู่รอด — ตรรกะนี้ถูกต้อง หน่วยความจำของโพรเซสมีเพดานจริง

---

## Not checked and why

- **Postgres จริง** — เครื่องนี้ไม่มี `psql` และ Docker daemon ไม่ได้เปิด ตัวนับกลางในการทดสอบเป็น
  ของจำลองที่เขียนตาม semantics ของ SQL ทีละบรรทัด **ยังต้องมีคนรัน `./mai db-init` บน throwaway DB
  แล้วยิง `rate_hit` ซ้ำ ๆ ก่อนขึ้น production** (ตรงกับที่ตั๋วบอกไว้เอง)
- **พฤติกรรมจริงของ Vercel edge** — ห้ามยิง production อ้างอิงเอกสาร Vercel เท่านั้น (ข้อ 2)
  ถ้าเจ้าของอยากยืนยัน: deploy preview แล้วเรียก endpoint ที่สะท้อน `x-vercel-forwarded-for` กลับมา
  พร้อมส่งหัวข้อปลอมไปหนึ่งครั้ง — ทำบน preview deployment เท่านั้น
- **credential stuffing แบบกระจาย IP (botnet/residential proxy)** — อยู่นอกตั๋วโดยเจตนา และ
  ยังเปิดอยู่จริง (ต้องมีตั๋วใหม่: captcha / device signal / แจ้งเตือนเจ้าของบัญชี)
- **`_body_json` ไม่มีเพดาน** (`server.py:461-464`) — ของเดิม ไม่ใช่ของตั๋วนี้ แต่ขอบันทึกว่า
  "เพดาน 10 ครั้ง/IP" ที่ตั๋วอ้างว่าช่วยจำกัดความเสียหายนั้น ใช้ไม่ได้เมื่อผู้โจมตีหมุน IPv6 /64
  (ข้อ 4) — bug-triager ควรถือเป็นเหตุผลเพิ่มในการปิดช่องนั้น

---

## ลำดับที่ควรแก้ก่อนรวมเข้า main

1. ข้อ 1 (reset ฟรี) และ ข้อ 2 (`X-Vercel-Forwarded-For`) — ทั้งคู่ทำให้ตัวจำกัด "ไม่มีผล" ในทางปฏิบัติ
2. ข้อ 3 + 4 (`ipaddress` + /64) แก้ครั้งเดียวได้ทั้งสองข้อ และปิดข้อ 5 ไปครึ่งหนึ่ง
3. ข้อ 6 (log fail-open) — ราคาถูกมากและเป็นสิ่งเดียวที่ทำให้ "แก้แล้วจริงไหม" ตรวจสอบได้
4. ข้อ 7 (หยดน้ำตอนถูกบล็อก) — ปิดช่อง DoS การล็อกอินและทำให้ข้อกำหนด "ไม่ล็อกผู้ใช้จริง" เป็นจริง
5. ข้อ 5, 8, 9 ตามลำดับ

**ความมั่นใจ:** สูงสำหรับข้อ 1, 3, 4, 6, 7, 8 (รันจริง เห็นผลซ้ำได้ทุกครั้ง) · สูงสำหรับข้อ 5
(grep ทั้ง repo) · ปานกลางสำหรับข้อ 2 เฉพาะส่วน "production Vercel ปลอดภัย" ซึ่งอ้างเอกสาร
ไม่ใช่การทดลอง — ส่วน "self-hosted โดนแน่" นั้นรันจริงแล้ว

**Key rotation runbook:** ไม่จำเป็น — ไม่มีความลับรั่วในสาขานี้ (ไม่มีไฟล์ env/คีย์ใน diff,
ไม่มีการพิมพ์ค่าลับ, ตาราง `rate_limits` เก็บแค่คีย์ `scope:ip` ไม่ใช่ข้อมูลส่วนบุคคลของผู้ใช้
— แม้ IP จะถือเป็นข้อมูลส่วนบุคคลตาม PDPA ก็ยังจำเป็นต่อความปลอดภัยและควรถูกลบตามข้อ 5)
