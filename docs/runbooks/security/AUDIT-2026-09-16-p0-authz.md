# Security audit — การแก้ P0 เรื่องสิทธิ์ (BACKLOG #1/#2/#3) + staging ของบอท — 2026-09-16

**Scope / method**

ตรวจเฉพาะการแก้ที่ยังไม่ commit: `git diff -- meeting_ai/web/server.py meeting_ai/web/jobs.py meeting_ai/web/pgstore.py`
และ `meeting_ai/bot.py` (ข้อมูลส่วนบุคคล: เสียงประชุม + ภาพหน้าจอของห้องประชุมจริง)
คำถามหลัก: BACKLOG P0 #1 (draft route ข้ามการเช็คเจ้าของ), #2 (คุกกี้แชร์ผ่านด่านล็อกอินทั้ง API),
#3 (`jobs.active()` ไม่แยกตามผู้ใช้) ปิดจริงหรือยัง และมีทางอ้อมรอบด่านใหม่ไหม

วิธี:

1. อ่านโค้ดทั้งเส้นทาง `_route → _api → _meeting` และ `jobs.py` / `pgstore.py` ที่เกี่ยวข้อง (read-only)
2. สร้าง instance แยกในโพรเซส (PROJECT-CONTEXT "Isolated test recipe A") พอร์ต **55450**
   จำลองโหมด cloud ด้วย fake store ในหน่วยความจำ โดย patch `backend.cloud`, `backend.store`,
   `jobs.cloud`, `jobs.store`, `server.store` และตั้ง `config.remote_worker = True`
   (fake store เลียนความหมายของ SQL รวมพฤติกรรม NULL ของ `spec->>'owner_id'`)
   **ไม่แตะฐานข้อมูลจริง ไม่เรียก production ไม่รัน Docker ไม่ส่งบอทเข้าห้องจริง**
3. ยิงเมทริกซ์ ~45 คำขอ (share-read / share-edit / ผู้ใช้อื่น / เจ้าของ / แอดมิน / ไม่มีคุกกี้)
4. `python -m compileall -q meeting_ai api bot` → **OK**

**Summary:** Critical 0 · High 1 · Medium 4 · Low 4

| P0 | สถานะ |
|---|---|
| #1 draft-route ownership bypass | **CLOSED** |
| #2 share cookie ผ่านด่านล็อกอิน (`/api/jobs`, `/api/workers`) | **CLOSED** |
| #3 `jobs.active()` ไม่แยก tenant | **ปิดครึ่งเดียว** — `jobs[]` แยกแล้ว แต่ `workers[].job_title` ยังรั่ว (finding 1) |

---

## Findings (by severity)

### 1. [High] ชื่อการประชุมของ tenant อื่นยังรั่วผ่าน `workers[].job_title`

รุนแรงเพราะเป็นข้อมูลส่วนบุคคลชนิดเดียวกับที่ P0 #3 ตั้งใจปิด ผู้ใช้ที่ล็อกอินคนไหนก็ได้เห็นได้ตลอดเวลาโดยไม่ต้องทำอะไรเลย

**Location**

- `meeting_ai/web/server.py:498` — `/api/jobs` แนบ `out["workers"] = store.workers_list()` ให้ทุกคนที่ล็อกอิน (เช็คแค่ `self.user` ไม่ได้เช็ค `is_admin`)
- `meeting_ai/web/server.py:501-504` — `GET /api/workers` ไม่มีการเช็ค role เลย
- `meeting_ai/web/pgstore.py:816-845` — `workers_list()` ทำ `left join meeting_ai.jobs j` แล้วคืน `"job_title": j.title`
- `meeting_ai/web/static/app.js:224-225` — หน้าเว็บแสดง `w.job_title` ตรงๆ

**Proof** (instance แยก พอร์ต 55450; U1 กับ U2 คนละเจ้าของ ไม่มีความเกี่ยวข้องกัน)

```
GET /api/workers          Cookie: mai_session=<U1>
200 {"workers":[{"name":"gb10","status":"busy","job_id":"20260916-100001-bbbbbb",
                 "job_title":"M&A ของ U2","gpu":"RTX 4090","alive":true}]}

GET /api/jobs             Cookie: mai_session=<U1>
200 {"jobs":[], "workers":[{... "job_title":"M&A ของ U2" ...}]}
```

`jobs[]` ว่างถูกต้องแล้ว (ตัวกรอง `owner_id` ทำงาน) แต่ข้อมูลชุดเดียวกันไหลออกทาง `workers[]`

**Impact** ผู้ใช้ที่ล็อกอินคนไหนก็ได้ (ไม่ต้องเป็นแอดมิน) เห็นชื่อการประชุมที่กำลังประมวลผลของทุกทีม
หน้าเว็บ poll ทุก 1.5 วินาที คนหนึ่งจึงเก็บชื่อการประชุมของทั้งระบบได้ต่อเนื่อง
พ่วงด้วยชื่อเครื่อง worker และรุ่น GPU (ข้อมูลโครงสร้างพื้นฐาน)

**Fix** เลือกทางใดทางหนึ่ง

- ตัด `job_title` (และ `job_id`) ออกจาก `workers_list()` สำหรับคนที่ไม่ใช่แอดมิน หรือ
- เปลี่ยนเงื่อนไขที่ `server.py:498` และ `:501` เป็น `self.user and self.user.get("is_admin")`
  แล้วให้ผู้ใช้ทั่วไปเห็นแค่จำนวนเครื่องที่ออนไลน์

**Owner role** backend-dev

---

### 2. [Medium] `_may_write_job` ยังใช้ `job["id"]` แทน `meeting_id` — เจ้าของหยุดงานบนการประชุมของตัวเองไม่ได้

กระทบการควบคุมค่าใช้จ่ายและสิทธิ์ของเจ้าของข้อมูล ไม่ใช่การรั่วของข้อมูล จึงไม่ถึง High

**Location** `meeting_ai/web/server.py:252-266`

```python
return owner == self.user_id if owner else self._may_write(job["id"])
```

เทียบกับพี่น้องที่เพิ่งเขียนใหม่ `server.py:268-285` ซึ่งทำถูก:
`return self._may_read(job.get("meeting_id") or job["id"])`

งาน `translate` มี id เป็น `<mid>.tr.<lang>` ซึ่งไม่ใช่ id การประชุมที่มีอยู่จริง `_may_write("<mid>.tr.en")` จึงได้ `none` เสมอ

**Proof**

```
POST /api/meetings/M1/translate   Cookie: mai_share=<share-edit ของ M1>   {"lang":"en"}
202 {"id":"20260916-100000-aaaaaa.tr.en", ... "title":"ประชุมลับ U1"}

POST /api/jobs/20260916-100000-aaaaaa.tr.en/stop   Cookie: mai_session=<U1 = เจ้าของ M1>
403 {"error":"ไม่มีสิทธิ์สั่งหยุดงานนี้"}
```

**Impact** คนที่ถือลิงก์แชร์แบบแก้ได้ สั่ง `translate` / `resummarize` บนการประชุมของเจ้าของได้ไม่จำกัด
(แต่ละครั้ง = การเรียก LLM = ค่าใช้จ่ายจริง + กินคิว worker) โดยเจ้าของ **หยุดไม่ได้**
ทางเดียวคือยกเลิกลิงก์แชร์ทั้งใบ งานเก่าก่อน deploy นี้ (spec ไม่มี `owner_id`) ก็ติดเงื่อนไขเดียวกัน

**Fix**

```python
return owner == self.user_id if owner else self._may_write(job.get("meeting_id") or job["id"])
```

**Owner role** backend-dev

---

### 3. [Medium] `_job_scope()` กรองด้วย `owner_id` อย่างเดียว — งานบนการประชุมของตัวเองหายไปจากคิว

กระทบทุกคนทันทีตอน deploy (งานที่ค้างในคิวหายจากหน้าเว็บ) แต่ไม่ทำให้ข้อมูลรั่ว

**Location** `meeting_ai/web/server.py:302-314` · `meeting_ai/web/pgstore.py:721-746` · `meeting_ai/web/jobs.py:232-250`

`jobs.active(owner_id=self.user_id)` แปลเป็น SQL `spec->>'owner_id' = %s` ซึ่ง **ไม่แมตช์** สามกรณี:

1. งานที่คนถือลิงก์แชร์เป็นคนสั่ง (`owner_id` เป็น JSON null เพราะ `self.user_id` เป็น None)
2. งาน `summarize` / `translate` ทั้งหมดที่สร้างก่อน deploy นี้ (spec ไม่มีคีย์ `owner_id` เลย)
3. งานบนการประชุม `visibility = team` ที่คนอื่นในทีมสั่ง

**Proof**

```
GET /api/jobs   Cookie: mai_share=<share-edit ของ M1>
200 {"jobs":[{"id":"20260916-100000-aaaaaa.tr.en","status":"queued","title":"ประชุมลับ U1"}]}

GET /api/jobs   Cookie: mai_session=<U1 = เจ้าของ M1>      <-- งานเดียวกัน ยัง queued อยู่
200 {"jobs":[], "workers":[...]}

GET /api/jobs   Cookie: mai_session=<U1>   (กรณีงาน translate บนการประชุม team ของ U2)
200 {"jobs":[]}
```

**Impact** คู่กับ finding 2 แล้วกลายเป็น "มีงานกิน LLM อยู่บนการประชุมของฉัน ฉันมองไม่เห็นและหยุดไม่ได้"
ช่วง deploy: งานทุกชิ้นที่อยู่ในคิวอยู่แล้วหายจากหน้าเว็บของทุกคนที่ไม่ใช่แอดมินทันที
ผู้ใช้จะอ่านว่า "งานหาย" แล้วกดสั่งใหม่ซ้ำ → งานซ้อน

**Fix** เพิ่มสาขา OR ให้เห็นงานของการประชุมที่ตัวเองมีสิทธิ์เขียนด้วย เช่นให้ `job_active()`
รับ `meeting_ids: list[str] | None` เพิ่ม แล้วต่อเงื่อนไขเป็น
`(spec->>'owner_id' = %s or meeting_id = any(%s::text[]))`
อย่างน้อยที่สุด: หน้ารายละเอียดการประชุมควร poll ด้วย `meeting_id` ของการประชุมที่เปิดอยู่

**Owner role** backend-dev (ประสานกับ web-dev เรื่องการ poll)

---

### 4. [Medium] อัปโหลดแทร็กทับการประชุมที่ทำเสร็จแล้วได้ (เจ้าของ และ **แอดมินทำกับของคนอื่นได้**)

ความถูกต้องของหลักฐาน ไม่ใช่การรั่ว และวงผู้ทำได้แคบลงมากแล้วจากการแก้รอบนี้ จึงเป็น Medium

**Location** `meeting_ai/web/server.py:941-965` (บล็อก `draft_route`) + `meeting_ai/web/jobs.py:79-86`

`jobs.draft(mid)` ในโหมด cloud คือ `store.job_get(mid)["_spec"]` ซึ่งคืน spec ของงานใน **ทุกสถานะ** รวม `done`
บล็อกใหม่จึงเช็คแค่ "มี spec + เป็นเจ้าของ" ไม่ได้เช็คว่า `status == "draft"`

**Proof** (M2 = การประชุมของ U2 ที่ทำเสร็จแล้ว งานสถานะ `done`, `meeting["audio"] = "<M2>.wav"`)

```
GET /api/meetings/<M2>/tracks/mixed/upload-url?ext=wav   Cookie: mai_session=<U2 เจ้าของ>
200 {"url":null,"key":"20260916-100001-bbbbbb.wav"}       <-- คีย์เดียวกับไฟล์เสียงที่เก็บไว้

GET /api/meetings/<M2>/tracks/mixed/upload-url?ext=wav   Cookie: mai_session=<ADMIN>
200 {"url":null,"key":"20260916-100001-bbbbbb.wav"}       <-- แอดมินทำกับการประชุมของ U2

POST /api/meetings/<M2>/process                          Cookie: mai_session=<U2>
400 {"error":"ยังไม่มีไฟล์เสียงให้ประมวลผล (อัปโหลดแทร็กก่อน)"}
```

`job_start()` (`pgstore.py:598-605`) มี `where ... and status = 'draft'` จึง **ประมวลผลซ้ำไม่ได้** (ดีแล้ว)
แต่ presigned PUT ที่ออกให้ชี้ไปที่คีย์ของไฟล์เสียงจริง และ `register_track()` เขียนทับ `spec.tracks` ของงานที่ done ไปแล้ว

**Impact** ไฟล์เสียงของการประชุมที่เสร็จแล้วถูกแทนที่เงียบๆ โดยบทถอดเสียง/สรุปยังเป็นของเดิม
เสียงกับข้อความไม่ตรงกันโดยไม่มีร่องรอย — สำหรับงานที่ใช้บันทึกประชุมเป็นหลักฐาน คือการทำลายความน่าเชื่อถือ
แอดมินทำกับการประชุมของผู้ใช้คนไหนก็ได้ และไม่มี audit log
(ของเดิมก่อนแก้: ใครที่ล็อกอินก็ทำได้ การแก้นี้ลดวงลงมาก แต่ยังไม่ปิด)

**Fix** ในบล็อก `draft_route` เช็คสถานะก่อน:

```python
job = jobs.get(mid)
if job is None or job.get("status") != "draft":
    return self._error(HTTPStatus.NOT_FOUND, "ไม่พบการประชุมที่รออัปโหลด")
spec = job.get("_spec") or {}
```

**Owner role** backend-dev

---

### 5. [Medium] `_prune_stages()` ลบโฟลเดอร์พักของ worker ตัวอื่นได้ เมื่อ `worker_tag` ชนกัน

ทำให้เสียงประชุมจริงหายทั้งงาน แต่ต้องมีสองเงื่อนไขพร้อมกัน (ชื่อ worker ชนกัน + จังหวะเวลา) จึงเป็น Medium

**Location** `meeting_ai/bot.py:180-189` (`worker_tag`) · `bot.py:339-367` (`_prune_stages`)
เรียกจาก `bot.py:226` ใน `cleanup_stale()` ซึ่งถูกเรียกตอน worker เริ่ม (`meeting_ai/worker.py:267`)

**สิ่งที่ตรวจแล้วว่าปลอดภัย** `worker_tag` = `re.sub(r"[^A-Za-z0-9]", "", worker)[:16]` → มี `_` ไม่ได้เลย
ดังนั้น glob `"gb10_*"` จึง **ไม่** แมตช์ `"gb10x_<job>"` — สมมติฐานเรื่อง prefix ซ้อน (tag `gb10` กับ `gb10_x`) เป็นไปไม่ได้จริง

**สิ่งที่ยังเป็นปัญหา** การตัดที่ 16 ตัวอักษรและการทิ้งอักขระที่ไม่ใช่ alnum ทำให้ tag **ชนกันแบบตรงๆ** ได้ง่าย:

| ชื่อ worker | `worker_tag` |
|---|---|
| `meeting-ai-worker-01` | `meetingaiworker0` |
| `meeting-ai-worker-02` | `meetingaiworker0` |
| `gpu-1` / `gpu_1` / `gpu 1` | `gpu1` |

เมื่อชนกัน worker ที่เพิ่งเริ่มจะ (ก) `docker stop` บอทของอีกตัวที่กำลังประชุมอยู่ (ของเดิม)
และ (ข) **ใหม่จากการแก้นี้** — `shutil.rmtree()` โฟลเดอร์พักของอีกตัว
เกราะ `any(w.stat().st_size > 0 for w in d.glob("*.wav"))` เป็นการแข่งกัน ไม่ใช่ล็อก:
ช่วงไม่กี่วินาทีแรกที่ ffmpeg ยังไม่เขียนไบต์แรก ไฟล์ยัง 0 → โฟลเดอร์ที่ container mount เป็น `/out` โดนลบใต้เท้า

**Impact** ไฟล์เสียงการประชุมจริงของอีกงานหายทั้งงาน (ประชุมจบไปแล้ว อัดใหม่ไม่ได้)
และ `_keep_debug_shot(d, d.name)` (`bot.py:360`) ย้ายภาพหน้าจอของงานที่ยังมีชีวิตไปไว้ใต้ชื่อของอีกงาน
prod รัน `--max-bots 6` และออกแบบให้รันหลาย worker ต่อเครื่องได้ จึงไม่ใช่กรณีทฤษฎี

**Fix**

```python
slug = re.sub(r"[^A-Za-z0-9]", "", worker or "")[:12]
return f"{slug}{hashlib.md5((worker or 'solo').encode('utf-8')).hexdigest()[:6]}"
```

และ/หรือ เขียนไฟล์ marker (pid + เวลาเริ่ม) ในโฟลเดอร์พัก แล้วให้ `_prune_stages` ข้ามโฟลเดอร์ที่อายุน้อยกว่า `MAX_BOT_MINUTES`

**Owner role** backend-dev (ร่วมกับ devops-engineer เรื่องการตั้งชื่อ worker ใน systemd unit)

---

### 6. [Low] 404 ก่อน 403 กลายเป็น oracle บอกว่า job/draft id นั้นมีอยู่จริง

**Location** `meeting_ai/web/server.py:952-957` (`ไม่พบการประชุมที่รออัปโหลด` มาก่อน `ไม่มีสิทธิ์กับการประชุมนี้`)
· `server.py:519-523` (`ไม่พบงานนี้` มาก่อน `_may_read_job`)

**Proof**

```
GET /api/meetings/20260101-000000-ffffff/tracks/mixed/upload-url?ext=wav  (U1) -> 404 ไม่พบการประชุมที่รออัปโหลด
GET /api/meetings/<draft ของ U2>/tracks/mixed/upload-url?ext=wav          (U1) -> 403 ไม่มีสิทธิ์กับการประชุมนี้
GET /api/jobs/20260101-000000-ffffff                                      (U1) -> 404 ไม่พบงานนี้
GET /api/jobs/<job ของ U2>                                                (U1) -> 403 ไม่มีสิทธิ์ดูงานนี้
```

**Impact** ผู้ใช้ที่ล็อกอินคนไหนก็ได้ และคนถือลิงก์แชร์ ยืนยันการมีอยู่ของ id ได้
id เป็น `YYYYMMDD-HHMMSS-<6 hex>` = 24 บิตต่อวินาที การไล่เดาจึงแพง
แต่เส้น `/api/meetings/{id}` ตอบ 403 เหมือนกันหมดอยู่แล้ว การเพิ่ม 404 ตรงนี้จึงเป็นความไม่สม่ำเสมอใหม่
ที่บอกได้ว่า "มีการประชุมเกิดขึ้นในวินาทีนั้น"

**Fix** ตอบ 404 ทั้งสองกรณี (หรือ 403 ทั้งสองกรณี) ให้เหมือนเส้น meetings
หมายเหตุ: คอมเมนต์ในโค้ดเลือกลำดับนี้อย่างตั้งใจ ("404 ก่อน 403 เหมือน /api/jobs/{id}/stop") — ถ้ายืนยันตามเดิม ให้บันทึกเป็น accepted risk

**Owner role** backend-dev

---

### 7. [Low] `_keep_debug_shot()` เอา `job_id` ดิบมาต่อเป็นชื่อไฟล์ ไม่ผ่านตัวกรองเหมือน `_job_slot()`

**Location** `meeting_ai/bot.py:315-336`

```python
tag = job_id or str(int(time.time()))
dest = DEBUG_DIR / f"{Path(name).stem}_{tag}.png"
```

เทียบกับ `bot.py:191-203` `_job_slot()` ที่กรองด้วย `re.sub(r"[^A-Za-z0-9_.-]", "", ...)`
ผู้เรียกที่ส่ง `job_id` ดิบเข้ามา: `bot.py:392` (`_fail_reason`) และใน `join_and_record`

**Impact** วันนี้ยังไม่มีทางโจมตี — `job_id` มาจาก `store.new_id()` (`^\d{8}-\d{6}-[0-9a-f]{6}$`)
หรือ `str(int(time.time()))` ของ CLI ไม่มีทางให้ผู้ใช้กำหนดเอง
แต่เป็นจุดเดียวในไฟล์ที่แก้รอบนี้ที่สร้าง path จาก id โดยไม่ผ่านตัวกรอง
ถ้าวันหนึ่งมี job kind ใหม่ที่ id มาจาก input (เช่น `translate` ที่ `lang` ยังไม่ validate — BACKLOG #13)
ก็จะกลายเป็นการเขียนไฟล์นอก `logs/` ทันที

**Fix** ใช้ตัวกรองเดียวกัน หรืออย่างน้อย `tag = Path(str(tag)).name`

**Owner role** backend-dev

---

### 8. [Low] ข้อความ error ของบอทพา path บนเครื่อง worker และ log ดิบของ container ขึ้นหน้าเว็บ

**Location** `meeting_ai/bot.py:371-403` (`_fail_reason`)
`f"ภาพหน้าจอตอนพลาด: {shot}"` (absolute path) + `"log ท้ายสุดของบอท:"` + 12 บรรทัดดิบจาก container
ข้อความนี้ถูกเก็บเป็น `jobs.error` แล้วส่งออกทาง `GET /api/jobs/{id}`

**Impact** ตอนนี้จำกัดให้เจ้าของงาน/แอดมินเห็นแล้ว (ผลพลอยได้จาก `_may_read_job` ในรอบนี้ — ก่อนหน้านี้ใครก็อ่านได้)
แต่ยังเปิดเผย path จริงบนเครื่อง worker (`C:\...\logs\bot_debug_<jobid>.png`) และเนื้อ log ของ container
ซึ่งมีโอกาสมีลิงก์ห้องประชุมติดมาด้วย รอบนี้ยังเพิ่ม `สถานะล่าสุดที่บอทรายงาน: <waiting|inroom|left>` เข้าไปอีก (อันนี้ไม่ใช่ PII)

**Fix** แสดงแค่ `shot.name`; กรองบรรทัดที่มี `http://` / `https://` ออกจาก tail ก่อนแนบ

**Owner role** backend-dev

---

### 9. [Low] `logs/` สะสมภาพหน้าจอของห้องประชุมจริงโดยไม่มีอายุ และรอบนี้เพิ่มทางไหลเข้าอีกทาง

**Location** `meeting_ai/bot.py:360` — `_prune_stages()` เรียก `_keep_debug_shot(d, d.name)` กับทุกโฟลเดอร์ที่ค้าง ก่อนลบทิ้ง

**Impact** `logs/` อยู่ใน `.gitignore` แล้ว (ตรวจแล้ว) แต่บนเครื่อง worker คือภาพหน้าจอห้องประชุมจริง
(หน้าคน สไลด์ที่แชร์ รายชื่อผู้เข้าร่วม) เก็บแบบไม่เข้ารหัส ไม่มีนโยบายลบ — ตรงกับ BACKLOG #25 ที่ยังเปิดอยู่
การแก้รอบนี้ทำให้มีงานมากขึ้นที่ฝากภาพไว้ที่นั่น (ไม่ใช่เฉพาะงานที่พลาด)

**ด้านบวก** `finally: shutil.rmtree(cout.parent)` (`bot.py:512`) ทำให้ WAV ของงานที่ล้มเหลวไม่ค้างใน `recordings/bot/` อีกต่อไป
— ส่วนหนึ่งของ BACKLOG #25 ดีขึ้น

**Fix** cron / systemd timer ลบไฟล์ใน `logs/` ที่เก่ากว่า 30 วัน และเขียนไว้ในเอกสารความเป็นส่วนตัว

**Owner role** devops-engineer

---

## Verified safe

### P0 #1 — draft-route ownership bypass: CLOSED

เส้นทางที่พิสูจน์: `server.py:944-965` รวมสามเส้น (`tracks/{name}/upload-url`, `POST tracks/{name}`, `POST process`)
เป็น `draft_route` เดียว แล้วบังคับ `store.valid_id` → `jobs.draft(mid)` (404 ถ้าไม่มี)
→ `_may_write_draft(spec)` (`server.py:286-300`) ก่อนเรียก handler ใดๆ
`_may_write_draft` fail closed: ไม่ล็อกอิน = False, spec ที่ไม่มี `owner_id` = False, แอดมิน = True

| ผู้เรียก | คำขอกับ draft ของ U2 | ผล |
|---|---|---|
| U1 (ล็อกอิน คนอื่น) | upload-url / POST track / POST process | **403** ทั้งสามเส้น |
| คนถือลิงก์แชร์แบบแก้ได้ | upload-url | **401** (ไม่ผ่าน `_share_may_call`) |
| คนถือลิงก์แชร์ บนการประชุมที่แชร์ให้เอง (มี job row `done`) | upload-url / POST track | **403** |
| U2 (เจ้าของ) | upload-url | 200 |
| แอดมิน | upload-url | 200 (ตั้งใจ) |
| draft เก่าที่ spec ไม่มี `owner_id` | U1 | **403** (fail closed) |

ยืนยันเพิ่ม:

- การเช็คสิทธิ์เกิด **ก่อน** อ่าน body — `_put_track` เรียก `self._read_body_to()` หลังจาก `_meeting()` อนุญาตแล้ว
  (`server.py:618-645`) ไม่มีการ parse body ก่อนด่าน
- `owner_id` ใน spec มาจาก `self.user_id` เท่านั้น (`server.py:559`, `:604`) ไม่มี `str(body.get(...))` ที่ไหนแตะมัน
  → สร้าง spec ที่ `owner_id == ""` จากฝั่งไคลเอนต์ไม่ได้

### P0 #2 — share cookie ผ่านด่านล็อกอินทั้ง API: CLOSED

เส้นทางที่พิสูจน์: `server.py:428-431` เปลี่ยนจาก `if not self.share` เป็น `if not (self.share and self._share_may_call(parts))`
โดย `_share_may_call` (`server.py:316-331`) เป็น allow-list: `GET /api/meetings`, `GET /api/jobs`, `GET /api/jobs/{id}`
และ `/api/meetings/{mid}/...` เฉพาะ mid ที่แชร์

| คำขอด้วยคุกกี้แชร์ของ M1 | ผล |
|---|---|
| `GET /api/workers` | **401** |
| `POST /api/settings` | **401** |
| `POST /api/meetings` (สร้าง draft) | **401** |
| `POST /api/meetings/bot` (ส่งบอท) | **401** |
| `POST /api/jobs/{id}/stop` | **401** |
| `GET /api/meetings/M2` · `/audio` · `/export.md` · `/tracks/mixed/upload-url` · `POST /process` | **401** ทั้งหมด |
| `GET /api/jobs` | 200 แต่กรองด้วย `meeting_id = M1` |
| `GET /api/jobs/{ของ M2}` · `{M2}.tr.en` · `{draft ของ U2}` · `{งานเก่าไม่มีเจ้าของ}` | **403** ทั้งสี่ |
| `GET /api/jobs` แล้วดูคีย์ `workers` | ไม่มีคีย์นี้เลย (`server.py:498` เพิ่ม `and self.user`) |

- **ไม่มี parser differential**: `_share_may_call` และ `_meeting()` ใช้ `parts` ชุดเดียวกันและเรียก
  `urllib.parse.unquote(parts[1])` เหมือนกันทั้งคู่ ทดสอบแล้ว `%2D` (`20260916%2D100001%2Dbbbbbb`),
  ตัวพิมพ์ใหญ่, ท้ายมี `/`, `%00` ต่อท้าย → 401 ทุกอัน
- `?share=<token>` แทนคุกกี้ก็เข้าด่านเดียวกัน (`server.py:218-222`) ไม่ได้ข้ามอะไร
- คุกกี้แชร์สองใบ: `_cookie()` คืนใบแรกที่เจอ แต่ละ token ผูกกับการประชุมเดียว จึงไม่ขยายสิทธิ์
- `_may_read_job` (`server.py:268-285`) **ไม่** เทียบ `None == None`: เงื่อนไข `if owner and owner == self.user_id`
  กันคนถือลิงก์แชร์ (ซึ่ง `self.user_id` เป็น None) ไม่ให้ไปแมตช์กับงานเก่าที่ไม่มีเจ้าของ — ยืนยันด้วยการทดสอบ (403)
- `is_admin` มาจาก `store.user_for_session()` → ตาราง `sessions` join `users` เท่านั้น ไม่มีทางมาจาก request

### P0 #3 — `jobs.active()` ไม่แยก tenant: ปิดเฉพาะ `jobs[]`

`jobs.py:60-71` + `pgstore.py:721-746` + `_job_scope()` (`server.py:302-314`) ใช้ที่ call site ทั้งสองแห่ง
(`server.py:481`, `:484`, `:496`)

- U1 ยิง `/api/jobs` และ `/api/meetings` ขณะที่งานของ U2 (process + translate + งานไม่มีเจ้าของ) กำลัง running → `jobs: []`
- แอดมินยิง → เห็นทั้งระบบ (ตั้งใจ)
- ความหมายของ SQL ตรวจแล้ว: `spec->>'owner_id'` คืน SQL NULL เมื่อคีย์หายหรือเป็น JSON null
  → `NULL = 'u1'` ให้ NULL → แถวตกไป = **fail closed** เช่นเดียวกับ `meeting_id = ''` ที่ไม่แมตช์อะไรเลย
  (`_job_scope` ใส่ `or ""` กัน `meeting_id` หายกลายเป็น None = ไม่กรอง — ถูกต้อง)
- สาขา `return {"owner_id": ""}` ที่ท้าย `_job_scope` **เข้าไม่ถึงจริง** (ด่านที่ `server.py:429` ตอบ 401 ก่อน)
  และถึงเข้าถึงก็ไม่แมตช์อะไร เพราะไม่มีทางสร้าง spec ที่ `owner_id == ""` จาก input
- โหมดไฟล์: `backend.auth_required()` เป็น False → `_job_scope()` คืน `{}` และ `jobs.active()` ข้ามการกรองตามที่ documented
  (ไม่มีลิงก์แชร์ในโหมดไฟล์ — `_share()` ตอบ 501)
- **ยังไม่ปิด**: `workers[].job_title` (finding 1)

### bot.py staging — ไม่มีทางหลุดออกนอก `STAGE_DIR`

`bot.py:191-203` `_job_slot()` สร้าง path component เดียวเสมอคือ `f"{worker_tag(worker)}_{tag}"`

- `worker_tag()` คืนค่าไม่ว่างเสมอ (ถอยไปใช้ md5 เมื่อ slug ว่าง) → component ขึ้นต้นด้วย alnum เสมอ
- `re.sub(r"[^A-Za-z0-9_.-]", "", job_id)` ตัด `/` และ `\` ทิ้ง → ไม่มี separator
- `job_id` เป็น `..` / `...` / `.` → ได้ component `gb10_..` / `gb10_.` ซึ่งเป็น **ชื่อโฟลเดอร์ธรรมดา** ไม่ใช่ `..`
  จึงไม่มีทางไต่ขึ้นเหนือ `STAGE_DIR`
- แหล่งของ `job_id`: `runner.py:310` ส่ง `spec["id"]` (= `store.new_id()`), CLI ส่ง `job_id=None` → `str(int(time.time()))`
  ไม่มี path ที่ผู้ใช้กำหนดเอง (งาน `translate` ที่ id มี `lang` ดิบ ไม่เคยเดินมาถึง `join_and_record`)
- glob `f"{worker_tag(worker)}_*"` ไม่แมตช์ tag ที่ยาวกว่า เพราะ `_` เป็นไปไม่ได้ใน `worker_tag`
  (การชนแบบตรงๆ ดู finding 5)
- `_prune_stages("")` (โหมด CLI) คืน `[]` ทันที — ไม่ลบของใคร

### อื่นๆ

- `jobs.public()` (`jobs.py:74-76`) ตัดคีย์ที่ขึ้นต้นด้วย `_` ทิ้ง `_spec` (ซึ่งมี `passcode` ของ Zoom และลิงก์ห้องประชุม)
  จึงไม่หลุดออกทาง `GET /api/jobs/{id}` — ตรวจ response จริงแล้ว
- `api/index.py` override แค่ `_host_ok()` ไม่มีความต่างเรื่องสิทธิ์บน Vercel
- `python -m compileall -q meeting_ai api bot` → OK
- การเปลี่ยน `.gitignore` (`!.claude/agent-team.json`) — เปิดไฟล์ดูแล้ว ไม่มี secret (ชื่อโปรเจกต์ / บทบาท / สแต็ก)

---

## Not checked and why

- **SQL จริงบน Postgres**: เครื่องนี้ไม่มี Postgres และห้ามแตะฐานจริง จึงวิเคราะห์ `job_active()` จากความหมายของ Postgres
  (`->>` คืน NULL, `NULL = 'x'` → NULL → ตกเงื่อนไข) แล้วจำลองด้วย store ในหน่วยความจำที่เลียนความหมายนั้น
  — **ยังไม่ได้รันกับ Postgres จริง** ควรให้ test-engineer ยืนยันด้วย throwaway DB
- **Docker / บอทจริง**: ไม่รัน container ไม่ส่งบอทเข้าห้องประชุมใดๆ `_job_slot` / `_prune_stages` / `join_and_record`
  ตรวจด้วยการไล่โค้ดอย่างเดียว การชนของ `worker_tag` คำนวณจากกฎ ไม่ได้ทดลองจริง
- **production** ไม่ถูกเรียกเลย ไม่มี request ออกนอกเครื่อง
- **นอกขอบเขตรอบนี้** (อยู่ใน BACKLOG อยู่แล้วและ diff นี้ไม่ได้แตะ): #10 rate limit ของ login,
  #11 `_body_json` ไม่จำกัดขนาด, #12 invite TOCTOU, #13 `lang` ของ translate ไม่ validate,
  #14 worker audio path ไม่ผ่าน `valid_id`, #15 static guard ใช้ `startswith`,
  #16 share-cookie fixation ที่ `GET /s/<token>`, #21 บอทรันเป็น root/`--no-sandbox`, #22 ไม่มี checksum ของไฟล์ที่ดาวน์โหลด
- **`tests/`** ข้ามตามคำสั่ง (test-engineer กำลังเขียนอยู่พร้อมกัน)
- **front-end** อ่านเฉพาะจุดที่ใช้ `workers[].job_title` ไม่ได้ตรวจ `app.js` ทั้งไฟล์
- **secrets / git history** ไม่สแกนซ้ำรอบนี้ (ใช้ baseline เดิมใน PROJECT-CONTEXT)
- **`access()` กับการประชุมที่ `owner_id` เป็น NULL** (`pgstore.py:405-423` คืน `"owner"` ให้ผู้ใช้ที่ล็อกอิน **คนไหนก็ได้**)
  เป็นพฤติกรรมเดิมที่ตั้งใจ (รองรับข้อมูลที่ย้ายมาจากโหมดไฟล์) และ `_may_read_job` ถอยไปพึ่ง `_may_read` ตัวนี้
  ตรวจแล้วว่าใน cloud ทุกการประชุมถูกสร้างพร้อม `owner_id` จาก spec (`jobs.apply_result` → `store.create(owner_id=d["owner_id"])`)
  แต่ **ไม่ได้ตรวจฐานข้อมูล production ว่ามีแถวที่ `owner_id is null` ค้างอยู่จริงหรือไม่**
  ถ้ามี แถวนั้นเปิดให้ผู้ใช้ทุกคนอ่าน/แก้/ลบได้ — แนะนำให้เจ้าของรัน
  `select count(*) from meeting_ai.meetings where owner_id is null;` แล้วแจ้งกลับ

## Key rotation runbook

ไม่พบ secret รั่วในรอบนี้ — ไม่ต้องหมุนคีย์
