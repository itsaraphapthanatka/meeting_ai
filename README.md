# 🎙️ meeting_ai — บันทึกและสรุปการประชุมด้วย AI

ระบบช่วยบันทึกเสียงประชุม (Google Meet / MS Teams / อื่นๆ) → ถอดเสียงเป็นข้อความ → สรุปเป็นภาษาไทยพร้อม action items อัตโนมัติ

- **ใช้ได้ทั้ง** หน้าเว็บ (`mai web`) และ CLI
- **ถอดเสียง:** whisper.cpp รันในเครื่อง (ฟรี, เร่งด้วย Metal บน Mac / CUDA บน Windows, เสียงไม่ออกจากเครื่อง)
- **สรุป:** LLM `gemma-4-12b` ผ่าน endpoint แบบ OpenAI-compatible
- **รับได้ทั้ง** ไฟล์อัปโหลด และ อัดสดจากเสียงในเครื่อง+ไมค์
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
| Windows (cmd / PowerShell) | `mai.cmd ...` |

ตัวอย่างในเอกสารนี้เขียนเป็น `./mai` — บน Windows เปลี่ยนเป็น `mai.cmd` ได้ตรงๆ

---

## หน้าเว็บ (แนะนำ)

```bash
./mai web            # เปิด http://127.0.0.1:8765 ให้อัตโนมัติ
./mai web --port 9000 --no-open
```

ในหน้าเว็บทำได้:

- **อัปโหลดไฟล์** — ลากมาวาง หรือเลือกไฟล์ เห็นแถบความคืบหน้าตอนถอดเสียงเป็น %
- **อัดสดจากเบราว์เซอร์** — ดักเสียงประชุมจากแท็บผ่าน `getDisplayMedia` + ไมค์
  **ไม่ต้องลง BlackHole/VB-CABLE** มีมิเตอร์ระดับเสียงคอยเตือนถ้าลืมติ๊ก "แชร์เสียงแท็บ"
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

**เบราว์เซอร์:** การอัดสดต้องใช้ Chrome หรือ Edge — Firefox/Safari ยังแชร์เสียงแท็บไม่ได้
(อัปโหลดไฟล์ใช้ได้ทุกเบราว์เซอร์)

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
เวลาอัดสดพร้อมกันทั้งแท็บและไมค์ ระบบอัดเป็น **2 แทร็กแยกกัน** แล้วถอดเสียงคนละรอบ
เสียงจากไมค์ = `ฉัน` เสียงจากแท็บ = `ผู้ร่วมประชุม` — รู้จากแหล่งที่มาเลย ไม่ต้องเดา
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
