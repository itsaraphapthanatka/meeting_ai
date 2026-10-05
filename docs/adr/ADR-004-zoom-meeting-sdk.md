# ADR-004 — เอาเสียงจากห้อง Zoom: Meeting SDK, RTMS หรือคงของเดิม

- **สถานะ**: ประเมินแล้ว — **ยังไม่ทำ Meeting SDK และยังไม่ทำ RTMS** (2026-10-05)
  คงทางเดิมไว้ (ให้ Zoom อัดเองแล้วอัปโหลด · อัดสด) และ**แก้คำอธิบายในหน้าเว็บกับ README
  ให้ตรงกับความจริงใหม่** ว่าทางที่ Zoom รองรับคือ RTMS ไม่ใช่ "ล็อกอินบัญชีให้บอท"
- **ตั๋ว**: Ideas — *"Zoom Meeting SDK (ทางที่ Zoom รองรับสำหรับทำบอทอัดเสียง) — ยังไม่ได้ทำในโปรเจกต์นี้"*
  (`README.md` หัวข้อ "Zoom ปฏิเสธบอท" ข้อ 4)
- **ผู้ตัดสินใจ**: เจ้าของโครงการ · ร่างโดย architect
- **ขอบเขต**: เฉพาะการเอา**เสียงจากห้อง Zoom** เข้า meeting_ai · ไม่เกี่ยวกับ Meet/Teams
  ซึ่งบอทปัจจุบันเข้าได้อยู่แล้ว

---

## 1. ของที่มีอยู่ตอนนี้ (ตรวจจากโค้ดจริง)

| สิ่งที่มี | ที่ไหน |
|---|---|
| บอทเป็น Playwright + Chromium ใน Docker | `bot/Dockerfile` — `mcr.microsoft.com/playwright/python:v1.48.0-jammy` |
| รู้จักลิงก์ Zoom อยู่แล้ว | `bot/platforms.py` `ZOOM_JOIN_RE = /(?:j|wc/join)/([0-9]{9,12})/` |
| ยอมรับโฮสต์ Zoom ที่ฝั่งเซิร์ฟเวอร์ | `web/server.py:115` `BOT_HOSTS` + `BOT_HOST_SUFFIX = (".zoom.us",)` |
| มีขั้นตอนกรอกชื่อ/รหัส/กดเข้าห้องของ Zoom เขียนไว้ครบ | `bot/platforms.py` `join_zoom()` · `ZOOM_NAME_FIELDS` · `ZOOM_PWD_FIELDS` · `ZOOM_JOIN_BUTTON` |
| **ตัวตรวจจับว่าโดนบล็อก** | `bot/platforms.py` `ZOOM_BOT_BLOCK = "text=/Automated bots aren.?t allowed/i"` |

พูดอีกอย่าง: **โค้ดฝั่ง Zoom เขียนเสร็จไปแล้วทั้งเส้น** สิ่งที่ขวางอยู่ไม่ใช่งานที่ยังไม่ได้ทำ
แต่เป็นนโยบายของ Zoom — web client ตรวจเจอเบราว์เซอร์ที่ถูกสั่งงานอัตโนมัติแล้วขึ้นว่า
*"Automated bots aren't allowed to join this meeting."* (มี reCAPTCHA คุมหน้านั้นอยู่)
และโปรเจกต์นี้ตั้งใจไม่หลบเลี่ยง (`README.md` ย่อหน้าสุดท้ายของหัวข้อนั้น)

---

## 2. ข้อเท็จจริงจากเอกสารของ Zoom (ตรวจ 2026-10-05)

### 2.1 เอกสารของ Zoom เองบอกให้ไปใช้ RTMS

หน้า Meeting SDK for Linux เขียนไว้ตรง ๆ ว่า

> "To build an AI notetaker application or access realtime media, use Zoom RTMS (Real-time media streams)."

— [developers.zoom.us/docs/meeting-sdk/linux](https://developers.zoom.us/docs/meeting-sdk/linux/)

นี่เปลี่ยนคำถามของตั๋วนี้ทั้งหมด ตั๋วตั้งไว้ว่า "Meeting SDK คือทางที่ Zoom รองรับสำหรับทำบอทอัดเสียง"
ซึ่ง **ไม่จริงอีกต่อไปแล้ว** — Zoom ย้ายงานประเภทนี้ไป RTMS และหน้า RTMS เขียนว่า

> "Instead of having participant bots or automated clients in meetings, use RTMS apps to collect the media data from the meeting."

> "Apps eliminate the need for bots or device software."

— [developers.zoom.us/docs/rtms](https://developers.zoom.us/docs/rtms/) · [RTMS for meetings](https://developers.zoom.us/docs/rtms/meetings/)

### 2.2 ประตูของ Meeting SDK ปิดไปแล้วตั้งแต่ 2 มี.ค. 2026

> "Beginning March 2, 2026, apps joining meetings outside their account must be authorized.
> Authorize apps by using either ZAK or OBF tokens, or RTMS."

— [developers.zoom.us/docs/meeting-sdk](https://developers.zoom.us/docs/meeting-sdk/)

และ FAQ ของ OBF อธิบายว่าเงื่อนไขคืออะไร:

> "OBF tokens represent an app. Use them when the MSDK app joins a meeting as an automated
> participant like a recording or note-taking app."

> OBF tokens require "an associated user with a ZAK token, and that user must already be in
> the meeting for the join to succeed" · "OBF tokens can only be obtained for participants
> who have authorized the app via OAuth"

> "Starting March 2, 2026, enforcement applies to all MSDK apps" · ใช้ได้กับแอปที่
> "joining meetings outside of their own account, meaning the host is external to the app"

— [developers.zoom.us/docs/meeting-sdk/obf-faq](https://developers.zoom.us/docs/meeting-sdk/obf-faq/)

**วันที่นั้นผ่านมาแล้วเจ็ดเดือน** ตอนที่ตั๋วนี้ถูกเขียน Meeting SDK ยังเป็นทางที่เปิดอยู่
ตอนนี้ไม่ใช่แล้ว

### 2.3 RTMS: เงื่อนไขและราคา

| เรื่อง | ค่า | ที่มา |
|---|---|---|
| ต้องมี | Zoom Developer Pack credits บนบัญชี | [Add RTMS to your app](https://developers.zoom.us/docs/rtms/meetings/add-features/) |
| ชนิดแอป | "RTMS apps must be user-managed apps" | เดียวกัน |
| scope ที่ต้องใช้ | `meeting:read:meeting_audio` · `meeting:read:meeting_transcript` · `meeting:update:participant_rtms_app_status` | เดียวกัน |
| ผู้ร่วมประชุมเห็นไหม | เห็น — "Scopes … appear to hosts and participants in meetings and webinars" | เดียวกัน |
| เริ่มสตรีมได้สามทาง | อัตโนมัติตอนผู้ใช้เข้าห้อง · REST API · `startRTMS()` จาก Zoom App | [Getting started](https://developers.zoom.us/docs/rtms/meetings/getting-started/) |
| เพดานสตรีมพร้อมกัน | 2,000 (ขอเพิ่มได้) | เดียวกัน |
| ราคา | **$0.01 ต่อนาที** (ไม่มีถอดเสียง) · **$0.02 ต่อนาที** (รวมถอดเสียงของ Zoom) | [Developer Forum](https://devforum.zoom.us/t/rtms-credit-consumption-per-minute-and-whether-the-initiating-participant-needs-a-paid-plan/145391) |
| แพ็กที่ต้องซื้อ | $100/100 เครดิต/เดือน หรือ $450/500 เครดิต/เดือน · ทดลองฟรี 20 เครดิตใช้กับ RTMS ไม่ได้ | [Zoom Build Platform pricing](https://trtc.io/blog/details/zoom-video-sdk-pricing-2026) |

คิดเป็นเงินจริง: ประชุมหนึ่งชั่วโมง = **$0.60 ≈ 21 บาท** ต่อครั้ง ถ้าใช้แค่เสียงดิบ
และเพดานขั้นต่ำคือ **$100 ต่อเดือน** ไม่ว่าจะใช้กี่นาที

### 2.4 ข้อจำกัดที่สำคัญที่สุดกับการใช้งานจริงของโปรเจกต์นี้

> "Your app can start RTMS for a participant when both of the following are true:
> The participant is an invitee to the meeting. **An invitee is a participant who was added
> to the meeting's invite list, not a participant who joined with a shared meeting link.**
> The participant has already joined the meeting."

— [Add RTMS to your app](https://developers.zoom.us/docs/rtms/meetings/add-features/)

นี่คือข้อความที่ตัดสินเรื่องนี้ ประโยคนี้พูดถึง**ทางเริ่มสตรีมผ่าน REST API** โดยเฉพาะ
ซึ่งเป็นทางเดียวที่เข้ากับรูปแบบของ meeting_ai ได้ (ผู้ใช้วางลิงก์ลงเว็บ แล้วเซิร์ฟเวอร์สั่งงานให้)
— และ meeting_ai ถูกใช้กับ**ลิงก์ที่คนอื่นส่งมาให้** เป็นหลัก ซึ่งเป็นเคสที่ประโยคนี้ตัดออกตรง ๆ

---

## 3. สามทางที่เป็นไปได้ และทำไมถึงยังไม่เลือกทางใหม่

### ทาง ก. เขียนบอทด้วย Meeting SDK (ทางที่ตั๋วเดิมเสนอ)

ต้องรัน Meeting SDK for Linux ในคอนเทนเนอร์แทน Chromium ที่ใช้อยู่ ได้ raw audio บน
native platform จริง แต่:

- ตั้งแต่ 2 มี.ค. 2026 ห้องที่ host เป็นคนนอกบัญชีเราต้องมี **OBF token** และ OBF ต้องมี
  **ผู้ใช้จริงที่อนุญาตแอปผ่าน OAuth และ "ต้องอยู่ในห้องนั้นอยู่แล้ว"** ถึงจะ join ได้
  → แปลว่าถ้าจะส่งบอทเข้าห้องลูกค้า ลูกค้า (หรือคนของเราที่อยู่ในห้อง) ต้องติดตั้งและอนุญาตแอปของเราก่อน
  ซึ่งทำให้ "วางลิงก์แล้วจบ" เป็นไปไม่ได้
- ต้องเข้า Zoom ISV Partner Program (`"The Meeting SDK follows the Zoom license model"`)
- **แลกมาด้วยการแหกข้อจำกัดหลักของโปรเจกต์**: `meeting_ai/` ใช้ stdlib เท่านั้น
  SDK ของ Zoom เป็นไลบรารี C++ ต้องมี binding, ต้อง build image ใหม่ทั้งใบ, และกลายเป็น
  dependency ไบนารีก้อนใหญ่ที่อัปเดตตามรุ่นขั้นต่ำที่ Zoom บังคับ
  (`"We enforce a required minimum version of the Meeting SDK"`)

**ไม่เลือก** — ต้นทุนสูงที่สุด แหกข้อจำกัดของโปรเจกต์ และยังแก้ปัญหาหลักไม่ได้อยู่ดี
เพราะยังต้องให้คนในห้องอนุญาตแอป

### ทาง ข. ทำแอป RTMS

ถูกต้องตามที่ Zoom ออกแบบ ได้เสียงแยกรายคน ได้ transcript ที่บอกว่าใครพูด ไม่ต้องมีบอท
เข้าห้องเลย และ **ไม่ต้องแตะ Docker/Playwright เลยสักบรรทัด** — เป็นเว็บฮุกเข้ามาที่เซิร์ฟเวอร์

แต่ติดสามข้อ:

1. **ค่าใช้จ่ายเป็นรายนาทีและมีขั้นต่ำรายเดือน** — $100/เดือน + $0.01/นาที
   โปรเจกต์นี้ตอนนี้ไม่มีค่าใช้จ่ายต่อการประชุมเลย (ถอดเสียงบนเครื่อง worker ของเจ้าของ)
2. **ต้องขึ้น Zoom App Marketplace และให้แต่ละบัญชีติดตั้ง** — "RTMS apps must be
   user-managed apps" และแอดมินต้อง add app ให้ users/groups
3. **ข้อ 2.4 ตัดเคสหลักของเราทิ้ง** — ทางเริ่มผ่าน REST ใช้ได้เฉพาะกับคนที่อยู่ใน
   invite list ไม่ใช่คนที่กดลิงก์เข้ามา ซึ่งเป็นวิธีที่เจ้าของใช้จริงเกือบทั้งหมด

ทางที่เหลือคือให้ผู้ใช้แต่ละคนติดตั้งแอปแล้วให้สตรีมเริ่ม**อัตโนมัติตอนเขาเข้าห้อง**
ซึ่งเป็นผลิตภัณฑ์คนละตัวกับที่ meeting_ai เป็นอยู่ (จาก "วางลิงก์แล้วระบบไปอัดให้"
กลายเป็น "ทุกคนในทีมต้องติดตั้งส่วนขยาย Zoom แล้วระบบดักเสียงให้ทุกห้องที่เขาเข้า")

**ไม่เลือกตอนนี้** — แต่เป็นทางที่ถูกต้องทางเทคนิคที่สุด และควรกลับมาดูใหม่ถ้าเงื่อนไข
ข้อใดข้อหนึ่งเปลี่ยน (ดูหัวข้อ 5)

### ทาง ค. คงของเดิม

ที่ใช้อยู่ตอนนี้และได้ผลเท่ากันทุกอย่าง:

1. ให้ Zoom อัดเอง (Local/Cloud Recording) แล้ว **อัปโหลดไฟล์** เข้า meeting_ai
2. **อัดสด** จากเบราว์เซอร์ระหว่างประชุม — โหมด "ไมค์ + เสียงในเครื่อง" แยกได้ด้วยว่าใครพูด

ทั้งสองทางไม่มีค่าใช้จ่ายต่อนาที ไม่ต้องให้ใครติดตั้งอะไร และ**ไม่ต้องขออนุญาต Zoom**

**เลือกทางนี้ต่อ**

---

## 4. ผลของการตัดสินใจ

| ทำ | ไม่ทำ |
|---|---|
| แก้ `README.md` หัวข้อ "Zoom ปฏิเสธบอท" ให้ตรงกับความจริงปี 2026 | ไม่เขียนโค้ดเพิ่มสักบรรทัด |
| เอาข้อเสนอ **"ล็อกอินบัญชี Zoom ให้บอท"** ออกจากรายการทางเลือก | ไม่แตะ `bot/` |
| ชี้ไปที่ ADR นี้แทนข้อ "Zoom Meeting SDK — ยังไม่ได้ทำ" | ไม่แตะคู่มือในหน้าเว็บ (ข้อความที่นั่นถูกอยู่แล้ว) |

เหตุผลที่ต้องถอดข้อ "ล็อกอินบัญชี Zoom ให้บอท" ออก: README เขียนว่าเป็น *"ทางที่ Zoom เขียน
บอกเองในหน้านั้น"* และหมายเหตุไว้ว่า *"(ยังไม่ได้ยืนยันว่าผ่าน)"* — ตอนนี้เรารู้แล้วว่าถึงผ่าน
หน้าล็อกอินได้ มันก็เป็น "automated client" ที่ Zoom ประกาศชัดว่าไม่ต้องการ และตั้งแต่
2 มี.ค. 2026 ก็ถูกคุมด้วยกฎ OBF เหมือนกัน **ปล่อยข้อนี้ไว้ = ชวนให้คนไปลองหลบเลี่ยง**
ซึ่งขัดกับย่อหน้าสุดท้ายของหัวข้อเดียวกันในไฟล์เดียวกัน

---

## 5. กลับมาดูใหม่เมื่อไหร่

ทบทวน ADR นี้ถ้าข้อใดข้อหนึ่งเป็นจริง:

- **Zoom ยอมให้เริ่ม RTMS กับคนที่เข้าห้องด้วยลิงก์** (ไม่ใช่เฉพาะ invitee) — ข้อนี้คนเดียว
  ก็พลิกคำตอบได้ เพราะที่เหลือเป็นแค่เรื่องเงินกับการติดตั้ง
- **มีลูกค้าที่ประชุมบน Zoom เป็นหลักและยอมจ่าย** — $100/เดือนขั้นต่ำกลายเป็นเรื่องเล็ก
  ถ้ามีผู้ใช้ประจำ และตอนนั้น "ทุกคนติดตั้งแอป Zoom ของเรา" ก็สมเหตุสมผลขึ้น
- **Zoom เลิกคิดเงิน RTMS รายนาที หรือมีชั้นฟรี**
- **โปรเจกต์ยอมรับ dependency ไบนารี** — ถ้าวันหนึ่งข้อจำกัด "stdlib เท่านั้น" ถูกผ่อน
  Meeting SDK ก็ยังไม่ใช่คำตอบอยู่ดีเพราะเรื่อง OBF แต่ควรประเมินใหม่พร้อมกัน

**ที่ยังยืนยันไม่ได้** และต้องทำ spike จริงถึงจะรู้: การเริ่ม RTMS **แบบอัตโนมัติ**
(ไม่ใช่ผ่าน REST) ใช้ได้ไหมกับห้องที่ host เป็นบัญชีภายนอก — เอกสารเขียนแค่ว่า
"Apps can auto-start when users join meetings and webinars" โดยไม่ได้บอกว่าห้องของใคร
ข้อจำกัด invitee ที่ยกมาในหัวข้อ 2.4 ระบุไว้ใต้หัวข้อ REST API เท่านั้น
การจะรู้ต้องสมัคร Developer Pack ($100) สร้างแอป แล้วลองกับห้องของบัญชีอื่นจริง ๆ
— ยังไม่คุ้มที่จะจ่ายเพื่อตอบคำถามนี้ ตราบใดที่ทาง ค. ยังใช้ได้อยู่
