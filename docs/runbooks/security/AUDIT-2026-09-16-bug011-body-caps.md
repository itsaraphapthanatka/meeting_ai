# Security audit — BUG-011 request body caps (`fix/bug-011-body-size-caps`, commit 39cb064) — 2026-09-16

**Scope / method:** ตรวจ commit เดียว `git diff main..HEAD` (`meeting_ai/web/server.py` +105/-6) เทียบกับ
[docs/tickets/BUG-011-unbounded-request-body.md](../../tickets/BUG-011-unbounded-request-body.md)
วิธี: อ่านโค้ด + ยิง **socket ดิบ** เข้าอินสแตนซ์แยกของตัวเองที่ `127.0.0.1:55450`
(store ชี้ไป scratchpad, `MEETING_AI_CLOUD=0`, `DATABASE_URL=""`, `REMOTE_WORKER=1`, ล้าง `S3_*` ทั้งหมด)
และอินสแตนซ์ cloud จำลองผ่าน `tests/_harness.py` (`CloudCase` + `FakeStore`, พอร์ตสุ่ม)
ไม่แตะ production, ไม่แตะ `recordings/web/` ของเจ้าของ, ไม่แตะ `bot/profile/`
ชุดทดสอบเดิม: `PYTHONIOENCODING=utf-8 python -m unittest discover -s tests` → **64 tests OK, 1 skipped** (ไม่ถดถอย)

**Summary:** Critical 0 · High 2 · Medium 3 · Low 4 (+ informational 2)

คำตอบสั้น ๆ ต่อสามคำถามหลักที่ dev ฝากมา:
1. **Request smuggling — ยืนยันว่าเป็นจริง** บน keep-alive: `Transfer-Encoding: chunked` (และอีก 3 รูปแบบ)
   ทำให้เซิร์ฟเวอร์ตอบ **2 responses ต่อ 1 request** ที่ front-end นับ — ดูข้อ 1
2. **Drain ถูก abuse ได้ — `LINGER_SECONDS` ไม่ได้บังคับเวลาจริง** ส่ง 1 ไบต์ทุก 0.4 วินาที ตรึงเธรดได้ 17-21 วินาที
   และตรึงต่อได้เรื่อย ๆ ด้วย traffic 74 ไบต์ + 2.5 B/s — ดูข้อ 2
3. **เพดานบังคับก่อนจองหน่วยความจำจริง** (ttfb 0.00s ทั้งที่ยังไม่ส่ง body เลย) — ข้อนี้ถูกต้องแล้ว ดู Verified safe

---

## Findings (by severity)

### 1. [High] HTTP request smuggling / keep-alive desync — body ที่ไม่ถูกอ่านกลายเป็น request ถัดไป

**เหตุผลของ severity:** สร้าง request ปลอมในคอนเนกชันเดียวกันได้ 100% (พิสูจน์แล้ว 4 รูปแบบ) และโค้ดนี้
ออกแบบมาให้อยู่หลัง reverse proxy อยู่แล้ว (`server.py:236`, `server.py:1328` เชื่อ `X-Forwarded-Proto`
เพื่อตั้งแฟล็ก `Secure` ของคุกกี้ `mai_session`) ของที่ได้คือ session ของผู้ใช้คนอื่น

**Location:** `meeting_ai/web/server.py:409-420` (`_content_length()` ไม่รู้จัก `Transfer-Encoding` เลย),
`server.py:422-444` (`_body_json()` คืน `{}` เมื่อไม่มี Content-Length โดยไม่อ่าน socket),
`server.py:473-491` (`_read_body_to()` ปฏิเสธแล้วไม่ระบายและไม่ปิดคอนเนกชัน),
และทุกเส้นที่ตอบ 401/403/404/405 ก่อนอ่าน body (`server.py:552-554` auth gate, `server.py:1104` permission block)

**Proof** (socket ดิบ 1 คอนเนกชันต่อเคส นับจำนวน `HTTP/1.1 ` ใน byte stream ที่ได้กลับ):

| # | สิ่งที่ส่ง | ผล |
|---|---|---|
| a | `POST /api/settings` + `Transfer-Encoding: chunked` แล้วตามด้วย `GET /api/config HTTP/1.1…` เป็น "body" | **responses=2** (200 ของ POST แล้วตามด้วย 200 ของ GET ที่ลักลอบมา) |
| b | `POST /api/settings` + `Content-Length: 2` แต่ส่ง `{}` + request เต็ม ๆ ต่อท้าย | **responses=2** |
| c | `Content-Length: 2` และ `Content-Length: 100` สองบรรทัด (Python ใช้ค่าแรก) | **responses=2** |
| d | `POST /api/no-such-endpoint` + `Content-Length: N` + body = request เต็ม ๆ (ตอบ 404 โดยไม่อ่าน body) | **responses=2** |

byte stream จริงของเคส (d):

```
HTTP/1.1 404 Not Found … {"error": "ไม่พบ endpoint นี้"}HTTP/1.1 200 OK … {"llm_ready": true, …
```

โหมด cloud (CloudCase + FakeStore) ยืนยันซ้ำแบบ **ไม่ต้องล็อกอิน**:
`POST /api/auth/login` + `Transfer-Encoding: chunked` → `responses=2` (400 ของ login + 200 ของ `/api/config` ที่ลักลอบมา)

**Impact:** ถ้ามี front-end ที่ใช้คอนเนกชันซ้ำกับ origin (nginx / cloudflared tunnel / ngrok / LB ใด ๆ ที่ใครสักคน
เอามาวางหน้า `mai web`) ผู้โจมตีที่ไม่ต้องล็อกอินจะ "ฝาก" request ไว้ในคิว แล้ว response ของ request นั้นจะไปถึง
**ผู้ใช้คนถัดไป** ที่ใช้คอนเนกชันเดิม → ขโมย/ป้อนคุกกี้ `mai_session`, อ่าน transcript/summary ของทีมอื่น,
หรือยิง `PATCH /api/meetings/{id}` ในนามผู้ใช้จริง โหมด local (127.0.0.1 คนเดียว) ผลกระทบจำกัดที่ตัวเอง

**Fix (backend-dev):**
1. มี header `Transfer-Encoding` (ค่าใดก็ตาม) → ตอบ `501 Not Implemented` แล้ว `self.close_connection = True`
   (stdlib `http.server` ไม่ถอด chunked ให้ ห้ามปล่อยผ่านเด็ดขาด)
2. `Content-Length` ซ้ำหลายบรรทัด (`self.headers.get_all("Content-Length")`) หรือค่าไม่ใช่ `1*DIGIT` ล้วน
   → `400` + ปิดคอนเนกชัน (รายละเอียดข้อ 5)
3. **กฎกลาง:** ทุกครั้งที่ตอบกลับโดย body ยังไม่ถูกอ่านจนหมด ต้องตั้ง `self.close_connection = True`
   ที่คุมได้จุดเดียวคือ `_route()`: จำ `declared = self._content_length()` ตอนเข้า แล้วนับไบต์ที่อ่านจริง
   ถ้าอ่านไม่ครบตอนจะตอบ → ระบาย (ตามข้อ 2) หรือปิด ไม่ใช่ทำเฉพาะเส้น `BodyTooLarge` อย่างตอนนี้

---

### 2. [High] `_drain_rejected_body()` ตรึงเธรดได้ไม่จำกัดเวลา — deadline 2 วินาทีไม่มีผลกับ client ที่หยอดทีละไบต์

**เหตุผลของ severity:** เรียกได้โดยไม่ต้องล็อกอิน (`POST /api/auth/login` อยู่ใน `PUBLIC_API`),
ต้นทุนผู้โจมตี 74 ไบต์ + 2.5 B/s ต่อ 1 เธรด, `ThreadingHTTPServer` ไม่มีเพดานจำนวนเธรด
และบน Vercel คือเวลาประมวลผลที่ถูกเรียกเก็บเงินจริง (`vercel.json` `maxDuration: 60`)

**Location:** `meeting_ai/web/server.py:447-471`

```python
deadline = time.monotonic() + LINGER_SECONDS
self.connection.settimeout(LINGER_SECONDS)
while left > 0 and time.monotonic() < deadline:
    chunk = self.rfile.read(min(CHUNK, left))     # ← บล็อกจนครบ 64 KB หรือ EOF
```

`self.rfile` คือ `io.BufferedReader` (`rbufsize = -1`): `read(n)` **ไม่คืนค่าจนกว่าจะครบ n ไบต์**
ส่วน socket timeout 2 วินาทีนับจาก "ไม่มีข้อมูลเข้ามาเลย 2 วินาที" ไม่ใช่เวลารวม — ผู้โจมตีหยอด 1 ไบต์ทุก 0.4 วินาที
จึงรีเซ็ต timeout ได้ตลอด และเงื่อนไข `time.monotonic() < deadline` ไม่เคยถูกประเมินอีกเลยเพราะยังติดอยู่ใน `read()`

**Proof** (วัดจากเปิดคอนเนกชันถึงตอนเซิร์ฟเวอร์ปิด):

| สถานการณ์ | ttfb ของ 413 | เธรดถูกตรึงนาน |
|---|---|---|
| `POST /api/meetings`, `Content-Length: 67108864`, ไม่ส่ง body เลย | 0.00s | **2.00s** (ตามดีไซน์) |
| เหมือนกัน แต่หยอด 1 ไบต์ทุก 0.4s เป็นเวลา 20s | 0.00s | **21.62s** (ปิดตอนผู้โจมตีเลิกหยอดเอง) |
| โหมด cloud, `POST /api/auth/login` **ไม่มีคุกกี้**, หยอด 1 ไบต์ทุก 0.4s | 0.00s | **17.62s** |
| 100 คอนเนกชันพร้อมกัน (header 74 ไบต์/อัน = 7.4 KB รวม) + หยอด 1 ไบต์ทุก 0.4s | — | **100/100 ยังเปิดอยู่** ที่วินาทีที่ 6 |

ข้อเท็จจริงพื้นฐานประกอบ: เซิร์ฟเวอร์นี้ **ไม่มี socket timeout เลย** (`BaseHTTPRequestHandler.timeout = None`
และ `Handler` ไม่ได้ override) — ยิง `Content-Length: 60000` แล้วส่ง 1 ไบต์แล้วเงียบ → ไม่มีคำตอบและไม่ปิดเลยที่ 25 วินาที
เช่นเดียวกับ header ที่ส่งไม่จบ ข้อนี้มีมาก่อน commit นี้ แต่คอมเมนต์ที่บรรทัด 52-55 และ 450
เขียนว่า "จำกัดทั้งจำนวนไบต์และเวลา (กัน slowloris)" ซึ่ง **ไม่จริง** — อันตรายกว่าไม่มี เพราะคนอ่านโค้ดจะเชื่อว่ากันแล้ว

**Impact:** ผู้โจมตีนิรนามเครื่องเดียวตรึงเธรด/FD ของเซิร์ฟเวอร์ self-host ได้จนหมด (ผู้ใช้จริงเข้าไม่ได้ =
เอาบันทึกประชุมออกมาไม่ได้) บน Vercel = function time สูงสุด 60 วินาทีต่อ request 74 ไบต์ คูณจำนวนคอนเนกชัน
→ กินทั้งค่าใช้จ่ายและ concurrency ของทีม

**Fix (backend-dev):**
1. ใน drain ใช้ `self.rfile.read1(min(CHUNK, left))` (คืนทันทีที่ได้ข้อมูลจาก raw read ครั้งเดียว — ยืนยันแล้วว่า
   `io.BufferedReader` มี `read1`) และตั้ง timeout ต่อรอบเป็นเวลาที่เหลือจริง:
   `self.connection.settimeout(max(0.05, deadline - time.monotonic()))` → deadline 2 วินาทีจะถูกบังคับจริง
2. ตั้ง `timeout = 30` เป็น class attribute ของ `Handler` — `socketserver.StreamRequestHandler.setup()` จะ
   `settimeout()` ให้ทั้งคอนเนกชัน ทำให้ `_body_json()` / `_read_body_to()` ที่บล็อกอยู่ตอนนี้มีเพดานไปด้วย
3. ใส่เพดานเวลารวมต่อ request ใน loop ของ `_read_body_to()` ด้วย ไม่ใช่แค่เพดานไบต์

---

### 3. [Medium] `_read_body_to()` ยังแปลง `Content-Length` ด้วย `int()` ดิบ → 500 พร้อมข้อความ exception ของ Python

**เหตุผลของ severity:** ผิด acceptance criteria ข้อ 4 ของตั๋วตรง ๆ ยิงได้จากภายนอก และ response ปล่อยข้อความภายในออกไป

**Location:** `meeting_ai/web/server.py:475` — `length = int(self.headers.get("Content-Length") or 0)`
(เส้นที่ใช้: upload track `server.py:770`, worker audio `server.py:994`, `/api/live` `server.py:1053`)

**Proof:**

```
POST /api/live HTTP/1.1
Content-Length: abc
→ HTTP/1.1 500 Internal Server Error
  {"error": "invalid literal for int() with base 10: 'abc'"}
```

เทียบกับเส้นที่แก้แล้ว: `POST /api/settings` + `Content-Length: abc` → **400** ถูกต้อง
(`Content-Length: -5` → 400 "ไม่มีข้อมูลไฟล์ส่งมา" และ `3221225472` → 400 "ไฟล์ใหญ่เกิน 32 MB" สองอันนี้โอเค)

เส้นนี้ยังตอบ **400 ไม่ใช่ 413** เมื่อไฟล์ใหญ่เกิน (ตั๋วข้อ 3 ระบุ 413) และหลังปฏิเสธแล้ว
**ไม่ระบาย ไม่ปิดคอนเนกชัน** — ทดสอบแล้วยิง `GET /api/config` ต่อบนคอนเนกชันเดิมได้ 200 ทันที
เท่ากับเปิดช่องข้อ 1 ให้เส้นอัปโหลดทั้งหมด (และ client ที่กำลังส่ง 2 GB จะเห็น connection reset แทน error ที่เราเขียน)

**Impact:** ผู้ใช้ที่อัปโหลดผิดพลาดเห็น 500 (ทีมซัพพอร์ตไล่เหตุผิดจุด), ผู้โจมตีรู้ว่า backend เป็น Python และรู้จุดที่ไม่ validate,
และเส้นอัปโหลดยังเป็น desync primitive

**Fix (backend-dev):** ให้ `_read_body_to()` เรียก `self._content_length()` ตัวเดียวกับ `_body_json()` แล้วโยน
`BodyTooLarge(msg, length)` เมื่อเกิน `limit` เพื่อรวมเส้น 413 + drain ไว้ที่ `_route()` จุดเดียว
(ตอนนี้ไฟล์เดียวมีตรรกะ Content-Length สองชุด: ชุดใหม่ที่ถูกต้อง กับชุดเดิมที่บรรทัด 475)

---

### 4. [Medium] `_clean_segments()` รับ `NaN`/`Infinity` เป็น start/end → การประชุมพังถาวร (GET และ export ตอบ 500 ตลอดไป)

**เหตุผลของ severity:** ทำลายข้อมูลผู้ใช้ถาวรด้วย request เดียว และคนที่ทำได้รวมถึง
**ผู้ถือลิงก์แชร์แบบแก้ได้ (`can_edit`)** ซึ่งเป็นคนนอกทีม

**Location:** `meeting_ai/web/server.py:1358-1366` — `float(item.get("start", 0))` ไม่ได้ตรวจ `math.isfinite`
(commit นี้เพิ่มเพดานจำนวน item และตัด `text` แล้ว แต่ไม่ได้แตะค่าตัวเลข) → `store.set_segments()`
(`meeting_ai/web/store.py:203`) เขียนลงไฟล์ด้วย `json.dumps(...)` ที่ `allow_nan=True` เป็นค่าปริยาย (`store.py:62`)

**Proof** (อินสแตนซ์แยก + meeting fixture ที่สร้างเองในscratchpad):

```
PATCH /api/meetings/<mid>  {"segments":[{"start":"nan","end":"1e400","text":"pwn","speaker":"A"}]}
→ 500 {"error": "cannot convert float NaN to integer"}
ไฟล์ <mid>.json บนดิสก์:  "start": NaN,  "end": Infinity      ← เขียนสำเร็จไปแล้วก่อน error
GET /api/meetings/<mid>            → 500 (ทุกครั้ง ตลอดไป)
GET /api/meetings/<mid>/export.md  → 500
GET /api/meetings                  → 200 (รายการยังอยู่ แต่เปิดอันนั้นไม่ได้)
```

**Impact:** transcript ถูกทับด้วยของปลอม และเรกคอร์ดนั้นเปิดไม่ได้อีกเลย (อ่าน/ดาวน์โหลด/export ตายทั้งหมด)
เจ้าของต้องแก้ไฟล์ JSON ด้วยมือถึงจะกู้คืน ในโหมด cloud `pgstore.set_segments` ส่ง `json.dumps` ที่มี `NaN`
เข้าคอลัมน์ `jsonb` ซึ่ง Postgres ปฏิเสธ → คาดว่าได้ 500 พร้อมข้อความของ DB (ยังไม่ได้รันจริง ดู Not checked)

**Fix (backend-dev):** ใน `_clean_segments()` หลัง `float()` ให้
`if not (math.isfinite(start) and math.isfinite(end)): return None` แล้ว clamp ในช่วงที่เป็นไปได้
(เช่น `0 <= start <= end <= 24*3600`) เสริมด้วย `allow_nan=False` ใน `store._write_json()`
เพื่อกันการเขียนไฟล์ที่ `JSON.parse` ฝั่งเบราว์เซอร์อ่านไม่ได้

---

### 5. [Medium] `_content_length()` ยอมรับรูปแบบที่ RFC ห้าม (`1_0`, `+10`, เว้นวรรค) และใช้ค่าแรกเมื่อ header ซ้ำ

**เหตุผลของ severity:** เป็นวัตถุดิบมาตรฐานของการทำ smuggling ให้รอด front-end (parser differential) — คู่กับข้อ 1

**Location:** `meeting_ai/web/server.py:409-420` (`int(raw)` ของ Python ยอมรับ underscore, เครื่องหมาย `+` และ whitespace)

**Proof** (`POST /api/settings` + body 10 ไบต์ `{"a":"bc"}`):

```
Content-Length: 10       → 200   (ปกติ)
Content-Length: 1_0      → 200   ← int("1_0") == 10
Content-Length: +10      → 200
Content-Length: " 10 " / "10 "  → 200
Content-Length: 2 และ 100 (ซ้ำสองบรรทัด) → อ่าน 2 ไบต์ ที่เหลือกลายเป็น request ถัดไป (ข้อ 1c)
```

**Impact:** proxy/CDN ที่ตีความ `1_0` ว่า invalid (หรือใช้ header บรรทัดสุดท้าย) จะนับความยาว body ไม่ตรงกับ
เซิร์ฟเวอร์นี้ = desync ข้ามผู้ใช้ตามข้อ 1 โดยเพดาน 64 KB ไม่ช่วยอะไรเลย

**Fix (backend-dev):**

```python
raws = self.headers.get_all("Content-Length") or []
if len(raws) > 1:
    raise BadBody("Content-Length ซ้ำ")
raw = (raws[0] if raws else "").strip()
if raw and not (raw.isascii() and raw.isdigit()):
    raise BadBody("Content-Length ไม่ถูกต้อง")
```

---

### 6. [Low] response 413 ไม่มี header `Connection: close` ทั้งที่เซิร์ฟเวอร์กำลังจะปิดคอนเนกชัน

**Location:** `server.py:525-532` ตั้ง `self.close_connection = True` แต่ `_send()` (`server.py:203-211`)
ส่งแค่ Content-Type/Content-Length — ยืนยันจาก byte stream จริง:

```
HTTP/1.1 413 Request Entity Too Large
Server: meeting_ai Python/3.12.10
Date: …
Content-Type: application/json; charset=utf-8
Content-Length: 85
```

**Impact:** ผิด RFC 7230 §6.6 — client/proxy เก็บคอนเนกชันนี้ไว้ใน pool ต่อ แล้ว request ถัดไป
(อาจเป็นของผู้ใช้คนอื่นบน proxy เดียวกัน) ถูกส่งลงซ็อกเก็ตที่กำลังจะตาย → คำขอล้มเหลวแบบสุ่ม
**Fix:** ใน `_send()`/`_error()` ถ้า `self.close_connection` ให้ `self.send_header("Connection", "close")`

### 7. [Low] ข้อความ error รั่วรายละเอียดภายใน (exception ของ Python + เวอร์ชัน interpreter)

**Location:** `server.py:536-537` `except Exception as e: self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))`
**Proof:** `{"error": "invalid literal for int() with base 10: 'abc'"}` (ข้อ 3),
`{"error": "cannot convert float NaN to integer"}` (ข้อ 4) และทุก response มี `Server: meeting_ai Python/3.12.10`
**Impact:** ผู้โจมตีได้เวอร์ชัน Python ที่แน่นอนไว้จับคู่ CVE และรู้ว่า input ไหนไปโผล่ที่โค้ดจุดใด
**Fix:** ตอบข้อความไทยกลาง ("เกิดข้อผิดพลาดภายใน") แล้ว log รายละเอียดฝั่งเซิร์ฟเวอร์; ตั้ง `sys_version = ""` ใน `Handler`

### 8. [Low] `BodyTooLarge` และ `BadBody` สืบทอด `ValueError` — เสี่ยงถูกกลืนโดย `except ValueError` ในอนาคต

**Location:** `server.py:98-107` · ตรวจแล้ว **วันนี้ยังไม่มีจุดไหนกลืน** (ทั้ง 15 call site เรียก `_body_json()`
นอก try ที่จับ ValueError) แต่ `_create_draft()` มี `except (TypeError, ValueError)` อยู่ห่างไปไม่กี่บรรทัด (`server.py:663`)
**Impact:** ถ้าใครย้าย `_body_json()` เข้าไปใน try นั้น เพดาน 413 จะหายเงียบ ๆ โดยไม่มีเทสต์จับ
**Fix:** ให้ทั้งสองคลาสสืบทอด `Exception` แทน `ValueError`

### 9. [Low] worker ที่ส่ง result เกิน 8 MB ทำให้ผลงานทั้งงานหายถาวร

**Location:** `server.py:1000-1002` (413) + `meeting_ai/worker.py:344-353`: `post_json` ที่ได้ 413 → `WorkerError`
→ `except Exception` → ยิง `/error` → งานกลายเป็น `error` **ไม่มี retry และ transcript ถูกทิ้ง**
**Impact:** ประชุมที่ยาวเกิน ~24 เท่าของสถิติวันนี้จะเสียเวลา GPU ทั้งก้อน และสั่งประมวลผลใหม่ก็ล้มซ้ำเหมือนเดิม
ความน่าจะเป็นต่ำ แต่ผลคือ "ประชุมนั้นใช้ไม่ได้ถาวร" โดยผู้ใช้ไม่รู้สาเหตุ
**Fix:** ฝั่ง worker ตรวจขนาด payload ก่อนส่งแล้วแบ่ง `segments` เป็นก้อน (หรือส่งผ่าน blob storage เหมือนไฟล์เสียง)
อย่างน้อยที่สุดให้ log ชัดว่าโดนเพดาน 8 MB ไม่ใช่ error ทั่วไป

---

## Informational

- **เพดานที่บอกในข้อความ 413 (`ใหญ่เกิน 64 KB` / `8 MB`) ถือว่ายอมรับได้** — จำเป็นต่อ UX (ผู้ใช้ที่แก้ transcript
  ต้องรู้ว่าทำไมบันทึกไม่ผ่าน) และไม่ได้เปิดเผยโครงสร้างภายใน สิ่งที่ไม่ควรเพิ่มคือค่า `Content-Length` ที่ได้รับจริง
  หรือชื่อฟิลด์/พาธภายใน ที่ต้องแก้จริงคือข้อ 7 ไม่ใช่ข้อความ 413
- **commit 39cb064 เองไม่มีเทสต์** (`git diff main..HEAD` แตะแค่ `server.py` + เอกสาร + memory ของ backend-dev)
  ระหว่างที่ตรวจอยู่ test-engineer เพิ่ม `tests/test_bug_011_body_size_caps.py` (28 tests) + แก้ `tests/_harness.py`
  (`raw_request()`, `FakeStore.verify_password`, `set_visibility`) ไว้ใน working tree — **ยังไม่ commit ทั้งคู่**
  ต้องรวมเข้า commit เดียวกับโค้ด ไม่งั้นเพดานทั้งชุดไม่มีอะไรกันการถดถอย
  เทสต์ชุดนั้นครอบ AC1-AC5, Content-Length เพี้ยน 4 แบบ และทำ gap test ของ `_read_body_to` (ข้อ 3)
  กับ chunked ที่ข้ามเพดาน (ส่วนหนึ่งของข้อ 1) ไว้แล้ว **สิ่งที่ยังไม่มีใครครอบคือ**:
  (ก) การนับ response ต่อ 1 คอนเนกชัน (desync ข้อ 1 — `assert buf.count(b"HTTP/1.1 ") == 1`)
  (ข) **เวลาที่คอนเนกชันถูกตรึง** เมื่อ client หยอดทีละไบต์ (ข้อ 2 — assert ว่าปิดภายใน ~3 วินาที)
  (ค) `NaN`/`Infinity` ใน segments (ข้อ 4)

## Verified safe (ทดสอบแล้วผ่าน)

- **เพดานบังคับก่อนจองหน่วยความจำจริง**: `Content-Length: 67108864` และ `1099511627776` โดยไม่ส่ง body เลย
  ได้ 413 ที่ ttfb **0.00s** (ถ้ามีการ `read()` ก่อนจะบล็อก) และ `_body_json()` อ่านได้มากสุดเท่ากับ `limit`
  ต่อให้ Content-Length โกหกว่าน้อยกว่าของจริง (ส่วนเกินไปโผล่เป็น request ถัดไป = ข้อ 1 ไม่ใช่ปัญหา memory)
- **Content-Length หาย / ติดลบ / ไม่ใช่ตัวเลข บนเส้น `_body_json()` → 400 ไม่ใช่ 500** (ตั๋วข้อ 4) ผ่าน
- **ไม่มี double response จากเส้น 413**: ทั้ง 15 call site เรียก `_body_json()` ก่อนเขียน response ทุกจุด
- **เพดานใหญ่ 8 MB เข้าถึงได้หลังผ่านสิทธิ์แล้วเท่านั้น**: `_patch()` ถูกเรียกหลัง permission block (`server.py:1104`)
  และ worker result อยู่หลัง `_worker_authed()` (`server.py:902-905`, `hmac.compare_digest`)
- **Acceptance criteria ที่ยิงจริงได้:** AC1 `POST /api/meetings` body 20,000,013 B → **413** ·
  AC2 `/api/settings` และ `/translate` body 100 KB → **413** (`/visibility` → 501 เพราะโหมดไฟล์ไม่มีฟีเจอร์นี้
  ต้องทดสอบในโหมด cloud) · AC4 `PATCH` 3,000 segments (194,687 B) → **200 ใน 0.08s** ·
  AC5 50,001 segments → **413**, `text` 20,000 ตัวอักษร → บันทึกจริง **5,000 ตัวอักษร**
- ชุดทดสอบเดิม 64 tests ผ่านครบ (1 skipped) — ไม่มี regression
- โค้ดใหม่ไม่ log ข้อมูลส่วนบุคคลหรือโทเค็น และไม่แตะเส้นทาง auth/share ที่แก้ไปในรอบ P0

## Not checked and why

- **Postgres path ของข้อ 4** (`pgstore.set_segments` + `jsonb`) — ไม่มี throwaway DB บนเครื่องนี้
  (`MAI_TEST_DATABASE_URL` ว่าง) อ่านโค้ดแล้วคาดว่าได้ 500 จาก Postgres แต่ยังไม่ได้รันจริง
- **Vercel runtime ใช้คอนเนกชันซ้ำข้าม invocation หรือไม่** — โครงสร้างภายนอกที่ทดสอบจากที่นี่ไม่ได้
  จึงจัดข้อ 1 ที่ High จากสถานการณ์ self-host หลัง reverse proxy ซึ่งโค้ดรองรับอยู่แล้ว (`X-Forwarded-Proto`)
  ไม่ใช่จาก production วันนี้ ถ้าเจ้าของยืนยันว่า production ไม่มี proxy ที่ pool คอนเนกชันกับ origin ลดเป็น Medium ได้
- **worker จริงที่ส่ง result เกิน 8 MB** (ข้อ 9) — ต้องมีเครื่อง GPU และประชุมยาวจริง วิเคราะห์จากโค้ด `worker.py` เท่านั้น
- **`_parse_range`, static path `startswith`, translate `lang`, login rate limit** — ของเดิมใน BACKLOG ไม่อยู่ใน diff นี้
  (`Range: bytes=-0` ตรวจด้วยสายตาแล้ว: ได้ `Content-Length: 0` + Content-Range เพี้ยน ไม่ถึงขั้นเป็นช่องโหว่)
- ไม่ได้ยิงอะไรไปที่ production, ไม่ได้รัน bot, ไม่ได้แตะ `recordings/web/` ของเจ้าของ

## ลำดับที่ควรแก้

1. ข้อ 1 + 5 (แก้พร้อมกัน: ปฏิเสธ `Transfer-Encoding`, `Content-Length` เข้มงวด, ปิดคอนเนกชันเมื่อ body ยังไม่ถูกอ่านหมด)
2. ข้อ 2 (`read1` + deadline ต่อรอบ + `Handler.timeout`) — ถ้ายังแก้ไม่ทัน ให้ลบคำว่า "กัน slowloris" ออกจากคอมเมนต์ก่อน
3. ข้อ 3 (`_read_body_to()` ใช้ `_content_length()` ร่วมกัน) และข้อ 4 (`math.isfinite`)
4. ข้อ 6-9 และเทสต์ตามหัวข้อ Informational

ไม่มีความลับรั่วในรอบนี้ — **ไม่ต้องหมุน key ใด ๆ**

*ผู้ตรวจ: security-engineer · ความมั่นใจ: สูงสำหรับข้อ 1-8 (มี proof จาก socket จริงหรือ byte stream ทุกข้อ),
ปานกลางสำหรับข้อ 9 (อ่านโค้ดอย่างเดียว) · baseline ที่ใช้เทียบ: `AUDIT-2026-09-16-p0-authz.md`*
