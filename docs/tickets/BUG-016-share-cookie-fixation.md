# BUG-016 (BACKLOG #16) — `GET /s/<token>` ตั้งคุกกี้ `mai_share` ให้เองตั้งแต่เปิด URL

**Severity:** P1 — share-cookie fixation: เว็บอื่นยัดลิงก์แชร์ของตัวเองเข้าเบราว์เซอร์คนอื่นได้ แล้วหน้าเว็บก็เด้งไปเปิดการประชุมของคนยัดให้ทุกครั้งที่โหลด
**Component:** `meeting_ai/web/server.py` (`_share_entry`, `_auth_api`) + `meeting_ai/web/static/app.js` + `index.html` · **โหมด cloud เท่านั้น** (โหมดไฟล์ไม่มีระบบแชร์ — `_share_entry` ตอบ 404 เสมอ)
**Reported by:** code review 2026-09-16 (BACKLOG #16, หมายเหตุ "confirm via POST from the SPA") · **Fixed by:** backend-dev · **Date:** 2026-09-17

## สาเหตุ (root cause)

`_share_entry()` ทำสองอย่างในคำขอเดียว: ตรวจโทเคน **และ** ฝากโทเคนลงคุกกี้ถาวร 7 วัน

```python
self._send(200, page, "text/html; charset=utf-8", {
    "Set-Cookie": f"mai_share={quote(token)}; Max-Age={7 * 86400}; HttpOnly; SameSite=Lax; Path=/",
    ...
})
```

คำขอที่ทำให้เกิดคุกกี้นี้คือ **GET ธรรมดาที่ไม่ต้องมี header พิเศษอะไรเลย** = เว็บไหนก็สั่งให้
เบราว์เซอร์ของเหยื่อยิงได้ด้วยลิงก์ / `window.open` / redirect / `<meta refresh>` และ `SameSite=Lax`
**ไม่กัน** เพราะ top-level navigation แบบ GET คือสิ่งที่ Lax อนุญาตโดยนิยาม ผลคือ:

1. โทเคนแชร์ของ *คนอื่น* ฝังอยู่ในเบราว์เซอร์เหยื่อ 7 วัน โดยเหยื่อไม่ได้ตัดสินใจอะไร
2. `/api/auth/me` รายงาน `share` ของคนยัดให้ **แม้เหยื่อจะล็อกอินอยู่**
3. `app.js` บรรทัด `if (state.share) return openMeeting(state.share.meeting_id)` พาเหยื่อไปเปิด
   การประชุมของคนยัดให้ทุกครั้งที่เข้าเว็บ — เนื้อหาที่ผู้โจมตีคุมได้ (ชื่อเรื่อง/สรุป/บทถอดเสียง)
   ถูกแสดงในแอปที่เหยื่อไว้ใจ และถ้าลิงก์นั้น `can_edit` สิ่งที่เหยื่อพิมพ์แก้ไปจะไปลงการประชุมของ
   ผู้โจมตี (ข้อมูลรั่วออกไปทางที่เหยื่อพิมพ์เอง)
4. เหยื่อปิดสถานะนี้เองไม่ได้จาก UI (ไม่มีปุ่ม "ออกจากโหมดแชร์" — คุกกี้ `HttpOnly` ลบเองก็ไม่ได้)

ที่ไม่ใช่ปัญหาของตั๋วนี้: คุกกี้ไม่ได้ยกสิทธิ์ของผู้ใช้ที่ล็อกอินอยู่ (`_level()` ให้ `store.access()`
ชนะเสมอ) และไม่ทำให้ใครอ่านการประชุมที่ไม่ได้แชร์ให้ตัวเองได้ (ด่าน `_share_may_call` จาก P0 ยังอยู่ครบ)

## พิสูจน์ก่อนแก้ (รันจริง)

PoC บน `tests/_harness.py` (โหมด cloud จำลอง, HTTP จริง):

```
[1] GET /s/<token>                 -> 200  Set-Cookie: mai_share=shr1; Max-Age=604800; HttpOnly; SameSite=Lax; Path=/
[2] GET /api/auth/me (คุกกี้ที่ถูกยัด) -> 200  share={"meeting_id": "<M1>", "can_edit": false}
[3] GET /api/auth/me (ล็อกอินอยู่ + คุกกี้ที่ถูกยัด) -> 200 user=a@example.com share={"meeting_id": "<M1>", ...}
[4] POST /api/auth/share           -> 404  (ยังไม่มีเส้นทาง "ยืนยันก่อนแล้วค่อยตั้งคุกกี้" เลย)
```

ขั้นที่รันจริงคือ [1]-[4] · ขั้นที่ **ยังไม่ได้พิสูจน์ด้วยเบราว์เซอร์จริง** คือ "เบราว์เซอร์ยอมรับ
`Set-Cookie` นี้เมื่อถูกพาไปจากโดเมนอื่น" (อนุมานจากสเปก `SameSite=Lax` + top-level navigation)
และ "หน้าเว็บเด้งไปเปิดการประชุมของคนยัด" (อ่านจาก `app.js` บรรทัด `if (state.share) ...`)

## ทางเลือกที่พิจารณา

| ทางเลือก | ปิดการยัดคุกกี้ | ต้นทุน / ทำไมไม่เลือก |
|---|---|---|
| A. เปลี่ยนคุกกี้เป็น `SameSite=Strict` | **ไม่ปิด** — Strict คุมว่าคุกกี้จะถูก *ส่ง* ตอนไหน ไม่ได้ห้าม *ตั้ง* จาก navigation ที่มาจากเว็บอื่น | ไม่แก้ปัญหา |
| B. เช็ค `Referer`/`Sec-Fetch-Site` ของ `GET /s/` | บางส่วน | header ทั้งสองหายไปได้ (ลิงก์จาก `rel=noreferrer`, แอปแชต, QR code) จะกลายเป็นลิงก์แชร์ที่ "ใช้ได้บ้างไม่ได้บ้าง" |
| C. ไม่ใช้คุกกี้เลย ใส่ `?share=<token>` ทุกคำขอจากหน้าแชร์ | ปิด | `_resolve_user()` รองรับอยู่แล้ว แต่ต้องแก้ทุกจุดที่ `app.js` เรียก API + โทเคนไปโผล่ใน URL/Referer/ประวัติ = งานใหญ่และเสี่ยงกว่า |
| D. redirect `GET /s/<token>` ไปหน้ายืนยันที่ถือโทเคนไว้ใน URL แล้วให้กดปุ่มที่เป็น `<form method=POST>` | ปิด | ต้องเพิ่มหน้า HTML ที่เซิร์ฟเวอร์เรนเดอร์เอง (โปรเจกต์นี้ไม่มี template engine) และ CSRF ของฟอร์มต้องมีโทเคนอีกชุด |
| **E. `GET /s/` แค่ตรวจโทเคน + เสิร์ฟ SPA, ตั้งคุกกี้ที่ `POST /api/auth/share` หลังผู้ใช้กดยืนยัน** | ปิด | **เลือกข้อนี้** — ตรงกับหมายเหตุใน BACKLOG ("confirm via POST from the SPA") ใช้ของที่มีอยู่แล้วทั้งหมด ไม่เพิ่มสคีมา ไม่เพิ่ม dependency |

### ทำไม POST + `Content-Type: application/json` ถึงพอกัน CSRF (ไม่ต้องมี CSRF token)

ฟอร์ม HTML ข้ามเว็บส่ง `Content-Type` ได้แค่ `application/x-www-form-urlencoded`, `text/plain`,
`multipart/form-data` เท่านั้น ส่วน `fetch()` ข้ามโดเมนที่ตั้ง `Content-Type: application/json`
เองจะกลายเป็น non-simple request ที่ต้อง preflight (`OPTIONS`) ก่อน — เซิร์ฟเวอร์นี้ไม่ตอบ CORS
header ใด ๆ เลย preflight จึงล้มทุกครั้ง ดังนั้นคำขอนี้เกิดได้จากหน้าเว็บของเราเองเท่านั้น
เส้นนี้จึง **ปฏิเสธ Content-Type อื่นด้วย 415 แบบชัดเจน** (ไม่ปล่อยผ่านเพราะ `_body_json()` แค่ parse
ได้ — body `{"token": "..."}` ส่งมาเป็น `text/plain` ได้จากฟอร์มข้ามเว็บ)

**ทางเลือกที่ปฏิเสธอย่างตั้งใจ:** เช็ค `Origin` ให้ตรงกับ `Host` เพิ่ม — ได้ความแข็งแรงน้อยมาก
(การเช็ค Content-Type ครอบกรณีเดียวกันอยู่แล้ว) แต่เพิ่มความเสี่ยงจริงว่า proxy/Vercel ที่เขียน
`Host` ใหม่จะทำให้ปุ่ม "เปิดลิงก์แชร์" พังบน production โดยไม่มีใครทดสอบทันก่อน deploy

## สิ่งที่แก้

**`meeting_ai/web/server.py`**

1. `_share_entry()` — ตรวจโทเคนเหมือนเดิม (`404` ถ้าใช้ไม่ได้) แล้วเสิร์ฟ `index.html` เฉย ๆ
   **ไม่มี `Set-Cookie`** อีกต่อไป (คง `Cache-Control: no-store`)
2. `_share_cookie_header(token)` (ใหม่) — สตริงคุกกี้ชุดเดิมทุกแอตทริบิวต์
   (`HttpOnly; SameSite=Lax; Path=/`, `Secure` เมื่อ `X-Forwarded-Proto: https`, `Max-Age=604800`)
3. `_share_accept()` (ใหม่) = `POST /api/auth/share` body `{"token": "<token>"}`
   → `415` ถ้า Content-Type ไม่ใช่ `application/json`
   → `404` ถ้าโทเคนว่าง/ใช้ไม่ได้ (ข้อความเดียวกับ `_share_entry`, ไม่บอกว่าไม่มีจริงหรือหมดอายุ)
   → `503` ถ้าต่อฐานข้อมูลไม่ได้ (ไม่ปล่อยเป็น 500)
   → `200 {"share": {"meeting_id", "can_edit"}}` + `Set-Cookie: mai_share=...`
4. `PUBLIC_API` += `("auth", "share")` — ผู้เยี่ยมชมยังไม่มีคุกกี้อะไรเลยตอนกดยืนยัน
   (เส้นนี้อยู่ใน `_auth_api` จึงตอบ `501` ในโหมดไฟล์เหมือนเส้น auth อื่น ๆ)

**`meeting_ai/web/static/index.html` / `app.js`**

5. `<template id="tpl-share-confirm">` — การ์ดยืนยัน (ใช้คลาส `auth-wrap`/`auth-card` ที่มีอยู่แล้ว)
   บอกผลของการกด ("เบราว์เซอร์นี้จะจำลิงก์แชร์นี้ไว้ 7 วัน") + ปุ่มเปิด + ปุ่มไม่เปิด
6. `pendingShareToken()` — อ่านโทเคนจาก `location.pathname` (`/s/<token>`) ถอดรหัสแบบเดียวกับฝั่ง
   เซิร์ฟเวอร์ (`decodeURIComponent` + ตัด `/` หัวท้าย); `%` ไม่ครบคู่ → ถือว่าไม่มีโทเคน
7. `showShareConfirm(token)` — กดปุ่มแล้ว `POST /api/auth/share` (ผ่าน `jsonPost` ซึ่งตั้ง
   `Content-Type: application/json` อยู่แล้ว) สำเร็จ → `location.replace('/')` (โหลดใหม่ให้สถานะ
   สะอาด และไม่เก็บโทเคนไว้ในประวัติ/Referer) · ล้มเหลว → แสดงข้อความ error จากเซิร์ฟเวอร์
8. `init()` — เช็ค `pendingShareToken()` **ก่อน** `needsAuth()` ไม่งั้นผู้เยี่ยมชมที่ยังไม่มีคุกกี้
   จะเจอหน้าล็อกอินแทนหน้ายืนยัน

ไม่แตะสคีมา ไม่แตะ `pgstore.py`/`store.py` ไม่เปลี่ยนรูปแบบลิงก์แชร์ (`/s/<token>` เหมือนเดิม
ลิงก์ที่แจกไปแล้วยังใช้ได้) และไม่เปลี่ยนแอตทริบิวต์ของคุกกี้

## เกณฑ์ผ่าน (acceptance)

- `GET /s/<token ที่ใช้ได้>` → 200, `text/html`, `Cache-Control: no-store`, **ไม่มี `Set-Cookie`**
- `GET /s/<token มั่ว>` → 404 และไม่มีคุกกี้ (เหมือนเดิม)
- `POST /api/auth/share {"token": ...}` (JSON) → 200 + คุกกี้ครบทุกแอตทริบิวต์เดิม และคุกกี้นั้น
  ใช้กับ `GET /api/auth/me` ได้จริง (`share.meeting_id` ถูกต้อง)
- `POST` ด้วย `text/plain` / `x-www-form-urlencoded` / `multipart/form-data` → **415 และไม่มีคุกกี้**
- token ว่าง / ไม่มีฟิลด์ / ไม่ใช่สตริง / ใช้ไม่ได้ → 404 และไม่มีคุกกี้ · JSON เสีย → 400 (ไม่ใช่ 500)
- `X-Forwarded-Proto: https` → คุกกี้มี `Secure`
- โหมดไฟล์ → 501 · `GET /api/auth/share` → 404 (ไม่ใช่เส้นทาง)
- ชุดทดสอบเดิมทั้งหมดยังผ่าน (`tests/test_p0_02_share_gate.py::test_share_entry_sets_cookie`
  ถูกเขียนใหม่เป็น `test_share_entry_serves_the_page_without_planting_the_cookie` เพราะเทสต์เดิม
  ยืนยัน *พฤติกรรมที่เป็นบั๊กเอง*)

regression test: `tests/test_bug_016_share_cookie_confirm.py` (13 เคส)

## สิ่งที่ยังไม่ปิดในตั๋วนี้ (ส่งต่อ)

- **ยังไม่ได้ทดสอบด้วยเบราว์เซอร์จริง** (`web-tester`/`e2e-tester`): หน้ายืนยันแสดงถูกต้อง, กดแล้ว
  เปิดการประชุมที่แชร์ได้, กด "ไม่ใช่ตอนนี้" แล้วกลับหน้าหลัก, เปิดลิงก์ที่สองทับลิงก์เดิมแล้วได้
  หน้ายืนยันของลิงก์ใหม่ (ไม่ใช่เด้งไปการประชุมเดิม) — โหมด cloud ต้องมี Postgres จึงทดสอบ
  ที่เครื่องนี้ไม่ได้
- **ยังไม่มีปุ่ม "ออกจากโหมดแชร์"** (ลบคุกกี้ `mai_share`) — คนที่เคยกดยืนยันไปแล้วยังติดอยู่ 7 วัน
  เหมือนเดิม เสนอเป็นงานของ `web-dev` + endpoint `DELETE /api/auth/share` คู่กัน
- **ไม่มี rate limit บนการเดาโทเคนแชร์** ทั้ง `GET /s/<token>` (ของเดิม) และ `POST /api/auth/share`
  (ของใหม่) — โทเคนเป็น `secrets.token_urlsafe(24)` (192 บิต) จึงเดาไม่ออกในทางปฏิบัติ แต่ทั้งสอง
  เส้นยังยิงฐานข้อมูลได้ไม่จำกัดต่อ IP ต่างจาก `/api/auth/login|signup` ที่มีถังของ BUG-010
