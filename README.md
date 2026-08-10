# 🎙️ meeting_ai — บันทึกและสรุปการประชุมด้วย AI

ระบบช่วยบันทึกเสียงประชุม (Google Meet / MS Teams / อื่นๆ) → ถอดเสียงเป็นข้อความ → สรุปเป็นภาษาไทยพร้อม action items อัตโนมัติ

- **ใช้ได้ทั้ง** หน้าเว็บ (`mai web`) และ CLI
- **ถอดเสียง:** whisper.cpp รันในเครื่อง (ฟรี, เร่งด้วย Metal บน Mac / CUDA บน Windows, เสียงไม่ออกจากเครื่อง)
- **สรุป:** LLM `gemma-4-12b` ผ่าน endpoint แบบ OpenAI-compatible
- **รับได้ทั้ง** ไฟล์อัปโหลด และ อัดสด — กดอัดจากไมค์ได้เลยไม่ต้องแชร์หน้าจอ
  หรือแยกเสียงคุณ/อีกฝ่ายเป็น 2 แทร็กก็ได้
- ตัว orchestration เขียนด้วย Python stdlib ล้วน — ไม่ต้อง `pip install`

---

## ติดตั้งครั้งเดียว

### macOS
```bash
./setup.sh          # ติดตั้ง ffmpeg, whisper-cpp, โหลดโมเดล large-v3 (+ BlackHole ถ้าจะอัดสด)
# อยากได้เล็ก/เร็วกว่า:  ./setup.sh medium
```

### Windows
`setup.sh` ใช้ Homebrew จึงรันไม่ได้ — ทำสี่ขั้นนี้แทน (PowerShell):

```powershell
# 1) ffmpeg
winget install Gyan.FFmpeg

# 2) whisper.cpp — เลือก build เดียว
#    มี NVIDIA GPU: cuBLAS (เร็วกว่ามาก, ~670MB)
curl.exe -L -o whisper.zip https://github.com/ggml-org/whisper.cpp/releases/download/v1.9.2/whisper-cublas-12.4.0-bin-x64.zip
#    ไม่มี GPU:     CPU build (~8MB) — เปลี่ยนเป็น whisper-bin-x64.zip
Expand-Archive whisper.zip -DestinationPath bin\whisper

# 3) โมเดล
New-Item -ItemType Directory -Force models
curl.exe -L -o models\ggml-large-v3-turbo-q5_0.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin

# 4) คอนฟิก — คัดลอกแล้วแก้ WHISPER_BIN ให้ชี้ที่ bin\whisper\Release\whisper-cli.exe
Copy-Item .env.example .env
```

ค่าเชื่อมต่อ LLM อยู่ในไฟล์ `.env` (คัดลอกจาก `.env.example`) — ต้องใส่ `LLM_API_KEY` ก่อนใช้คำสั่งสรุป

> ตัวอย่างเวลาที่วัดได้จริง (RTX 3050 6GB + โมเดล turbo-q5): เสียง 11 วินาที ถอดเสร็จใน 1.6 วินาที รวมโหลดโมเดล

---

## ตัวเรียกใช้

| ระบบ | คำสั่ง |
|---|---|
| macOS / Linux / Git Bash | `./mai ...` |
| Windows PowerShell | `.\mai.cmd ...` |
| Windows cmd.exe | `mai.cmd ...` |

ตัวอย่างในเอกสารนี้เขียนเป็น `./mai` — บน Windows เปลี่ยนเป็น `.\mai.cmd` ได้ตรงๆ

> PowerShell ไม่รันคำสั่งจากโฟลเดอร์ปัจจุบันถ้าไม่มี `.\` นำหน้า (จะขึ้น
> *"is not recognized as the name of a cmdlet"*) — cmd.exe ไม่ต้องใส่

---

## หน้าเว็บ (แนะนำ)

```bash
./mai web            # เปิด http://127.0.0.1:8765 ให้อัตโนมัติ
./mai web --port 9000 --no-open
```

ในหน้าเว็บทำได้:

- **อัปโหลดไฟล์** — ลากมาวาง หรือเลือกไฟล์ เห็นแถบความคืบหน้าตอนถอดเสียงเป็น %
- **อัดสดจากเบราว์เซอร์ 3 โหมด** — เลือกได้ว่าจะกดอัดเลยแบบไม่ต้องแชร์หน้าจอ
  หรือดักเสียงจากแท็บให้คมที่สุด (ดูตารางเทียบด้านล่าง)
  **ไม่ต้องลง BlackHole/VB-CABLE** มีมิเตอร์ระดับเสียงคอยเตือนถ้าไม่ได้ยินเสียงเลย
- **แยกผู้พูด** — ดูหัวข้อล่าง
- **ถอดเสียงสดระหว่างประชุม** — ตัดคลิปทุก 15 วินาทีส่งไปถอด เห็นข้อความไหลออกมาระหว่างคุย
  (เป็นพรีวิว บทถอดเสียงชุดจริงถอดจากไฟล์เต็มตอนจบ จึงแม่นกว่า)
- **คลิกบรรทัดในบทถอดเสียงเพื่อกระโดดไปฟังเสียงตรงจุดนั้น** และบรรทัดที่กำลังเล่นจะไฮไลต์ตาม
- **แก้ได้ทั้งสรุปและบทถอดเสียง** รวมถึงเปลี่ยนชื่อผู้พูดทีเดียวทุกบรรทัด
- **เทมเพลตสรุป 5 แบบ** — ประชุมทั่วไป, 1:1/feedback, sales call, สัมภาษณ์งาน, daily standup
  (โครงหัวข้อต่างกันจริง ไม่ใช่แค่เปลี่ยนชื่อ)
- **แปลสรุป** เป็นอังกฤษ/ญี่ปุ่น/จีน/เกาหลี โดยคงโครงสร้าง Markdown เดิม
- **คลังการประชุม + ค้นหา** แบบ substring ทั้งชื่อเรื่อง สรุป และบทถอดเสียง
  (เหมาะกับภาษาไทยที่ไม่มีช่องว่างระหว่างคำ การตัดคำจะพลาดมากกว่า)
- **ดาวน์โหลด** `.md` `.txt` `.srt` `.vtt` `.docx` — เล่นเสียงย้อนได้ (รองรับ HTTP Range เลื่อนหาตำแหน่งได้)

> เซิร์ฟเวอร์ผูกกับ `127.0.0.1` เท่านั้นและ**ไม่มีระบบล็อกอิน** — ถ้าจะเปิดให้เครื่องอื่นในเครือข่ายเข้า
> (`--host 0.0.0.0`) ให้รู้ตัวว่าใครในเครือข่ายก็เปิดดูบันทึกประชุมได้

ข้อมูลเก็บที่ `recordings/web/` (`index.json` + ไฟล์ต่อการประชุม) ไม่ใช้ฐานข้อมูล

**เบราว์เซอร์:** โหมดแชร์แท็บต้องใช้ Chrome หรือ Edge — Firefox/Safari ยังแชร์เสียงแท็บไม่ได้
(อัปโหลดไฟล์และโหมดไมค์เดียวใช้ได้ทุกเบราว์เซอร์ รวมมือถือ)

### 3 โหมดอัดสด

| โหมด | ต้องแชร์หน้าจอ | แทร็ก | รู้ว่าใครพูดจาก | เหมาะกับ |
|---|---|---|---|---|
| **ไมค์เดียว** | ไม่ | `mixed` 1 แทร็ก | diarization | ประชุมในห้อง, เปิดลำโพงคุย, มือถือ |
| **ไมค์ + เสียงในเครื่อง** | ไม่ | `mic` + `system` | แหล่งเสียง (แม่นกว่า) | ประชุมออนไลน์ในแอป (Zoom/Teams desktop) |
| **แชร์แท็บ** | ใช่ | `mic` + `system` | แหล่งเสียง (แม่นกว่า) | ประชุมในเบราว์เซอร์ (Google Meet) |

โหมดที่เลือกไว้ถูกจำใน `localStorage` ครั้งหน้าเปิดมาเป็นโหมดเดิม

**โหมดไมค์เดียว** เปิดลำโพงไว้ ไมค์จะได้ทั้งเสียงคุณและอีกฝ่ายรวมเป็นแทร็กเดียว
จึงส่งเป็นแทร็ก `mixed` แล้วให้ diarization แยกผู้พูด (ไม่ใช่ `mic` ที่ระบบตีเป็น "ฉัน" ทุกประโยค)
ระบบปิด echo cancellation กับ noise suppression ในโหมดนี้ เพราะสองตัวนั้นออกแบบมาให้คนพูดใกล้ไมค์
ถ้าเปิดไว้จะกินเสียงคนที่นั่งไกลทิ้ง

**โหมดไมค์ + เสียงในเครื่อง** ต้องมีอุปกรณ์ *อินพุต* ที่วนเสียงลำโพงกลับเข้ามา:
- Windows: **Stereo Mix** (Sound settings ▸ Recording ▸ คลิกขวา ▸ Show Disabled Devices)
  หรือ **VB-CABLE** / VoiceMeeter
- macOS: **BlackHole** หรือ Soundflower

หน้าเว็บไล่หาจากชื่ออุปกรณ์ให้เองแล้วเลือกตัวที่เจอเป็นค่าเริ่มต้น
ถ้าไม่เจอจะบอกวิธีเปิด เบราว์เซอร์ปิดชื่ออุปกรณ์ไว้จนกว่าจะเคยได้สิทธิ์ไมค์
จึงมีปุ่ม "อนุญาตไมค์เพื่อดูรายชื่อ" ให้กดครั้งเดียว

---

## Deploy ขึ้น Vercel (ทีม/แชร์/มือถือ)

สถาปัตยกรรม: **Vercel ถือ UI + ข้อมูล + คิว ส่วนงานหนักอยู่บนเครื่องที่มี GPU ของคุณ**
Vercel ไม่มี GPU และ function ยาวสุด 5 นาที ถอดเสียงบนนั้นไม่ได้

```
เบราว์เซอร์/มือถือ ──► Vercel (API + Postgres + ล็อกอิน + ลิงก์แชร์)
        │                          │
        └── อัปไฟล์ตรงเข้า ──► Cloudflare R2 ◄── worker ดึงไปถอดเสียง
                                              (mai worker บนเครื่องคุณ)
```

**ทำไมต้องอัปตรงเข้า R2:** Vercel Function รับ body ได้แค่ 4.5 MB ไฟล์ประชุมผ่านไม่ได้
เบราว์เซอร์จึง PUT ตรงด้วย presigned URL (เซ็น SigV4 เอง ไม่ต้องลง boto3)

### ขั้นตอน

```bash
# 1) ฐานข้อมูล — สร้าง project ที่ neon.com เอา connection string (ตัวที่มี -pooler) ใส่ .env
./mai db-init                       # สร้างตารางใน schema meeting_ai

# 2) ที่เก็บไฟล์เสียง — สร้าง R2 bucket ที่ Cloudflare แล้วออก API token (Object Read & Write)
#    ใส่ S3_ENDPOINT / S3_BUCKET / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY ใน .env
#    แล้วตั้ง CORS ของ bucket ให้ origin ของเว็บ PUT/GET ได้

# 3) ลองในเครื่องก่อน deploy
./mai web --cloud                   # จะมีหน้าสมัคร/ล็อกอิน คนแรกเป็นแอดมิน

# 4) ขึ้น Vercel
npm i -g vercel && vercel login
vercel deploy --prod
```

env ที่ต้องตั้งบน Vercel: `MEETING_AI_CLOUD=1`, `REMOTE_WORKER=1`, `WORKER_TOKEN`,
`DATABASE_URL`, `LLM_API_KEY`, และชุด `S3_*`

> เลือก region ของ Vercel ให้ตรงกับ Neon (ค่าเริ่มต้น `iad1` = us-east-1)
> ไม่งั้นทุก query จะเดินทางข้ามทวีป

CORS ของ R2 bucket (แก้ origin เป็นโดเมนจริงของคุณ):
```json
[{ "AllowedOrigins": ["https://your-app.vercel.app"],
   "AllowedMethods": ["GET", "PUT", "HEAD"],
   "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600 }]
```

### มือถือ
หน้าเว็บเป็น PWA — เปิดบนมือถือแล้ว "เพิ่มไปหน้าจอโฮม" ได้ ใช้ดู/ค้น/แก้สรุปได้ครบ
**อัดได้ด้วยโหมดไมค์เดียว** ซึ่งไม่ต้องแชร์หน้าจอ (มือถือไม่มี `getDisplayMedia`
หน้าเว็บจึงปิดตัวเลือกแชร์แท็บให้เองเมื่อเปิดบนมือถือ)

---

## เลือกตัวถอดเสียง: ในเครื่อง หรือ API

เลือกได้**ต่อการประชุม** จาก dropdown "ตัวถอดเสียง" ในหน้าประชุมใหม่

| | ในเครื่อง (whisper.cpp) | API (OpenAI-compatible) |
|---|---|---|
| ค่าใช้จ่าย | ฟรี | จ่ายตามนาที |
| ความเป็นส่วนตัว | เสียงไม่ออกจากเครื่อง | เสียงถูกอัปโหลด |
| ต้องมี GPU | ควรมี (ไม่มีก็ได้แต่ช้า 5-10 เท่า) | ไม่ต้อง |
| แยกผู้พูดจากไฟล์รวม | ได้ (sherpa-onnx) | ไม่ได้ — ต้องอัด 2 แทร็ก |

ตั้งค่าใน `.env`:
```bash
STT_PROVIDER=local          # ค่าเริ่มต้น (local | api)
STT_MODEL=whisper-1
# ว่างไว้ = ใช้ค่าเดียวกับ LLM_BASE_URL / LLM_API_KEY
STT_BASE_URL=https://api.openai.com/v1
STT_API_KEY=sk-...
```

ใช้ได้กับอะไรก็ได้ที่พูดสเปก `/audio/transcriptions` — OpenAI, Groq, LiteLLM proxy

**ไฟล์ใหญ่จัดการให้เอง:** เกิน 24 MB จะบีบเป็น opus 24kbps ก่อน ถ้ายังใหญ่เกินก็ตัดเป็นท่อนละ
15 นาทีแล้วต่อ timestamp ให้เนียน (ประชุม 2 ชม.+ ก็ได้)

**ถ้า endpoint ไม่รองรับ `verbose_json`** (เช่น `gpt-4o-transcribe`) จะตกไปขอ `json` ธรรมดา
ได้ข้อความแต่ไม่มี timestamp รายประโยค

---

## แยกงานหนักไปเครื่องที่มี GPU (โหมด worker)

ปกติ `mai web` ทำทุกอย่างในโพรเซสเดียว แต่ถ้าจะเอาหน้าเว็บขึ้น cloud (ซึ่งไม่มี GPU)
ให้เว็บถือแค่คิวกับข้อมูล แล้วให้เครื่องที่มี GPU มารับงานไปทำ

**ฝั่งเซิร์ฟเวอร์** — ตั้งใน `.env` (หรือ env vars ของ cloud):
```bash
REMOTE_WORKER=1
WORKER_TOKEN=<สุ่มมาให้ยาวๆ>     # python -c "import secrets; print(secrets.token_urlsafe(32))"
```

**ฝั่งเครื่องที่มี GPU:**
```bash
./mai worker --api https://xxx.vercel.app --token <WORKER_TOKEN ตัวเดียวกัน>
./mai worker --api http://127.0.0.1:8765 --once      # ทดสอบ: ทำงานเดียวแล้วออก
```

worker จะ: รับงาน → ดาวน์โหลดไฟล์เสียง → ถอดเสียง+แยกผู้พูดด้วย GPU เครื่องนี้ → สรุปด้วย LLM
→ อัปโหลดไฟล์เสียงผสมกลับ → ส่งผลลัพธ์เข้าคลัง รายงาน % ตลอดทาง ให้หน้าเว็บเห็นเหมือนกัน
ไฟล์เสียงที่ดาวน์โหลดมาอยู่ในโฟลเดอร์ชั่วคราวและถูกลบเมื่อจบงาน

`WORKER_TOKEN` ว่าง = ปิด worker API ทั้งชุด (กันเปิดช่องไว้เฉยๆ) เทียบ token แบบ constant-time
ทั้งสองโหมดใช้ตัวประมวลผลตัวเดียวกัน (`meeting_ai/runner.py`) ผลลัพธ์จึงเหมือนกัน

---

## แยกผู้พูด (ใครพูดประโยคไหน)

มีสองกลไก ทำงานร่วมกันได้:

### 1) แยกจากแหล่งเสียง — แม่น 100% ไม่ต้องลงอะไรเพิ่ม
โหมด **แชร์แท็บ** และ **ไมค์ + เสียงในเครื่อง** อัดเป็น **2 แทร็กแยกกัน** แล้วถอดเสียงคนละรอบ
เสียงจากไมค์ = `ฉัน` เสียงจากแท็บ/อุปกรณ์วนเสียง = `ผู้ร่วมประชุม` — รู้จากแหล่งที่มาเลย ไม่ต้องเดา
(แลกกับเวลาถอดเสียงเป็นสองเท่า และไฟล์ที่ฟังย้อนเป็นไฟล์ผสมของทั้งสองแทร็ก)

### 2) แยกด้วยเสียงพูด (diarization) — สำหรับไฟล์ที่อัดรวมมาแล้ว
ใช้ **sherpa-onnx** (ONNX ไม่ต้องมี torch) แยกได้หลายคนจากไฟล์เดียว ตั้งชื่อให้เป็น `ผู้พูด 1..N`
แล้วเปลี่ยนชื่อเป็นชื่อจริงเองได้ในหน้าเว็บ ถ้ารู้จำนวนคนแน่ๆ ให้ระบุไว้จะแม่นขึ้น

ติดตั้ง (ทำครั้งเดียว รวม ~35 MB):
```bash
pip install sherpa-onnx

# โมเดล segmentation
curl -L -o seg.tar.bz2 https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2
tar -xjf seg.tar.bz2 -C models

# โมเดล speaker embedding
curl -L -o models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx
```

ไม่ได้ติดตั้งก็ใช้ได้ปกติ — หน้าเว็บจะปิดตัวเลือกนี้เองแล้วบอกว่าขาดอะไร

> ทั้งสองกลไกป้อนชื่อผู้พูดเข้าไปใน prompt ของ LLM ด้วย ทำให้ตาราง action items
> ระบุ "ผู้รับผิดชอบ" ได้เองแทนที่จะเป็น "ไม่ได้ระบุ" ทุกช่อง

---

## วิธีใช้ผ่าน CLI

### 1) มีไฟล์เสียง/วิดีโออยู่แล้ว (ง่ายสุด)
รองรับ mp3, m4a, wav, mp4 ฯลฯ — เช่นไฟล์ที่ Meet/Teams อัดไว้

```bash
./mai process recording.m4a --title "ประชุมทีม Product"
```
ได้ผลลัพธ์ในโฟลเดอร์ `recordings/`:
- `..._สรุป.md` — สรุปประชุม + action items (เปิดดูสวยใน editor ที่อ่าน Markdown)
- `..._transcript.txt` — บทถอดเสียงเต็มพร้อม timestamp

### 2) อัดสดระหว่างประชุม
ต้องมีอุปกรณ์ดักเสียงลำโพง — **BlackHole** บน Mac หรือ **VB-CABLE** บน Windows (ดูหัวข้อล่าง)

> ถ้าไม่อยากลงอะไรเพิ่ม ใช้ `mai web` แล้วเลือกโหมด "ไมค์เดียว" อัดได้เลยโดยไม่ต้องแชร์หน้าจอ

```bash
./mai devices                      # ดู index อุปกรณ์เสียง แล้วแก้ MIC_DEVICE/SYSTEM_DEVICE ใน .env
./mai record recordings/meet.wav   # เริ่มอัด, กด Ctrl+C เมื่อจบประชุม
./mai record recordings/meet.wav --process --title "Sprint Review"   # อัดเสร็จสรุปให้เลย
```

### คำสั่งย่อยอื่นๆ
```bash
./mai transcribe audio.mp3 -o transcript.txt   # ถอดเสียงอย่างเดียว
./mai summarize transcript.txt --title "..."   # สรุปจาก transcript ที่มีอยู่

# เลือกเทมเพลตสรุปได้ทั้ง process และ summarize
./mai process call.m4a --template sales --title "คุยกับลูกค้า ACME"
./mai summarize t.txt --template standup
```

> การแยกผู้พูดมีเฉพาะในหน้าเว็บ (`mai web`) — ฝั่ง CLI ยังถอดเสียงรวมเป็นก้อนเดียว

---

## ตั้งค่าอัดเสียงประชุมออนไลน์

Meet/Teams ไม่เปิดให้ดึงเสียงตรงๆ จึงอัดจาก "เสียงที่ออกลำโพงเครื่อง" แทน ต้องมีอุปกรณ์เสียงเสมือน
มาดักเสียงนั้นกลับเข้าไปเป็น input — คนละตัวกันในแต่ละระบบ

### macOS — BlackHole
1. `brew install blackhole-2ch` (setup.sh ถามให้แล้ว)
2. เปิดแอป **Audio MIDI Setup** → สร้าง **Multi-Output Device** = ลำโพงจริง + BlackHole
   (เพื่อให้ได้ยินเสียงประชุมด้วย และ BlackHole รับเสียงไปพร้อมกัน)
3. ตั้งเสียง output ของ Mac เป็น Multi-Output Device นั้น

### Windows — VB-CABLE
Windows ไม่มีอุปกรณ์ loopback มาให้ในตัว เลือกอย่างใดอย่างหนึ่ง:
1. **VB-CABLE** — https://vb-audio.com/Cable/ (ฟรี, เทียบเท่า BlackHole)
   ตั้ง output ของ Windows เป็น *CABLE Input* แล้วเปิด **Listen** ของ *CABLE Output*
   ส่งไปลำโพงจริง เพื่อให้ยังได้ยินเสียงประชุมอยู่
2. **VoiceMeeter** — https://vb-audio.com/Voicemeeter/ (ยืดหยุ่นกว่า ตั้งค่าซับซ้อนกว่า)
3. **Stereo Mix** — เปิดใน Sound Control Panel → แท็บ Recording ถ้าการ์ดเสียงรองรับ
   (การ์ดเสียง USB/HDMI ส่วนมากไม่มีให้)


## ช่วงเงียบกับอาการหลอนของ whisper

whisper จะ "แต่งคำ" ออกมาเมื่อป้อนความเงียบหรือเสียงรบกวนเบาๆ ให้ เช่นแทร็กไมค์ที่เจ้าของเครื่อง
ไม่ได้พูดเลย จะได้ข้อความอย่าง `เจ้าไหรือเจ้าไหรือ…` ซ้ำ 20 รอบ ยาว 30 วินาทีพอดี (= ขนาดหน้าต่างโมเดล)

แก้สองชั้น:

1. **VAD** — ตัดช่วงเงียบออกก่อนป้อนโมเดล ต้องมีไฟล์ `models/ggml-silero-v5.1.2.bin`
   ```bash
   curl -L -o models/ggml-silero-v5.1.2.bin      https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin
   ```
2. **ตัวกรองคำซ้ำ** — เผื่อ VAD ปล่อยผ่าน ช่วงที่วนวลีเดิมเกิน 6 รอบและกินพื้นที่เกิน 70%
   ของข้อความจะถูกทิ้ง

ไม่มีไฟล์ VAD ก็ยังถอดเสียงได้ แค่เสี่ยงได้ข้อความขยะบนแทร็กที่เงียบ

---

### ทั้งสองระบบ
`./mai devices` เพื่อดู index อุปกรณ์ → ใส่ตัวดักเสียงลำโพงใน `SYSTEM_DEVICE` ของ `.env`
และไมค์คุณใส่ใน `MIC_DEVICE` (ใส่ชื่ออุปกรณ์ตรงๆ แทน index ก็ได้)

ยังไม่มีอุปกรณ์ loopback ก็อัดแค่ไมค์ตัวเองได้: `./mai record out.wav --no-system`

> ⚠️ เรื่องมารยาท/กฎหมาย: ควรแจ้งผู้เข้าร่วมก่อนบันทึกการประชุมทุกครั้ง

---

## โครงสร้าง
```
meeting_ai/
├── mai                 # ตัวเรียกใช้ CLI (macOS/Linux/Git Bash)
├── mai.cmd             # ตัวเรียกใช้ CLI (Windows cmd/PowerShell)
├── setup.sh            # ติดตั้ง deps + โหลดโมเดล (macOS เท่านั้น)
├── .env                # ค่าเชื่อมต่อ (ไม่ commit)
└── meeting_ai/
    ├── config.py       # โหลด .env
    ├── recorder.py     # อัดเสียงสด (ffmpeg: avfoundation/dshow)
    ├── transcriber.py  # ถอดเสียง (whisper.cpp)
    ├── summarizer.py   # สรุป + เทมเพลต + แปลภาษา (gemma endpoint)
    ├── diarize.py      # แยกผู้พูด (sherpa-onnx, ออปชัน)
    ├── runner.py       # ตัวประมวลผลหนึ่งงาน — ใช้ร่วมทั้งโหมดในเครื่องและโหมด worker
    ├── worker.py       # ตัวรับงานจากเซิร์ฟเวอร์ไกล (mai worker)
    ├── pipeline.py     # ร้อยขั้นตอน → Markdown
    ├── cli.py          # คำสั่งย่อย
    └── web/            # หน้าเว็บ (http.server จาก stdlib)
        ├── server.py   # routing + REST API
        ├── jobs.py     # คิวงานเบื้องหลัง (worker เดียว) + progress
        ├── store.py    # คลังการประชุมเป็นไฟล์ JSON
        ├── exports.py  # md / txt / srt / vtt / docx
        └── static/     # index.html, app.js, style.css (ไม่มี dependency)
```

**เรื่อง dependency:** แกนหลักยังเป็น stdlib ล้วนตามเดิม — `sherpa-onnx` เป็นตัวเดียวที่ต้อง `pip install`
และเป็นออปชัน ไม่ลงก็ใช้ทุกอย่างได้ยกเว้นแยกผู้พูดจากไฟล์รวม
