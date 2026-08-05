# 🎙️ meeting_ai — บันทึกและสรุปการประชุมด้วย AI

ระบบช่วยบันทึกเสียงประชุม (Google Meet / MS Teams / อื่นๆ) → ถอดเสียงเป็นข้อความ → สรุปเป็นภาษาไทยพร้อม action items อัตโนมัติ

- **ถอดเสียง:** whisper.cpp รันในเครื่อง (ฟรี, เร่งด้วย Metal, เสียงไม่ออกจากเครื่อง)
- **สรุป:** LLM `gemma-4-12b` ผ่าน endpoint แบบ OpenAI-compatible
- **รับได้ทั้ง** ไฟล์อัปโหลด และ อัดสดจากเสียงในเครื่อง+ไมค์
- ตัว orchestration เขียนด้วย Python stdlib ล้วน — ไม่ต้อง `pip install`

---

## ติดตั้งครั้งเดียว

```bash
./setup.sh          # ติดตั้ง ffmpeg, whisper-cpp, โหลดโมเดล large-v3 (+ BlackHole ถ้าจะอัดสด)
# อยากได้เล็ก/เร็วกว่า:  ./setup.sh medium
```

ค่าเชื่อมต่อ LLM อยู่ในไฟล์ `.env` (คัดลอกจาก `.env.example`)

---

## วิธีใช้

### 1) มีไฟล์เสียง/วิดีโออยู่แล้ว (ง่ายสุด)
รองรับ mp3, m4a, wav, mp4 ฯลฯ — เช่นไฟล์ที่ Meet/Teams อัดไว้

```bash
./mai process recording.m4a --title "ประชุมทีม Product"
```
ได้ผลลัพธ์ในโฟลเดอร์ `recordings/`:
- `..._สรุป.md` — สรุปประชุม + action items (เปิดดูสวยใน editor ที่อ่าน Markdown)
- `..._transcript.txt` — บทถอดเสียงเต็มพร้อม timestamp

### 2) อัดสดระหว่างประชุม
ต้องมี **BlackHole** และตั้ง Mac ให้ส่งเสียงประชุมผ่านมัน (ดูหัวข้อล่าง)

```bash
./mai devices                      # ดู index อุปกรณ์เสียง แล้วแก้ MIC_DEVICE/SYSTEM_DEVICE ใน .env
./mai record recordings/meet.wav   # เริ่มอัด, กด Ctrl+C เมื่อจบประชุม
./mai record recordings/meet.wav --process --title "Sprint Review"   # อัดเสร็จสรุปให้เลย
```

### คำสั่งย่อยอื่นๆ
```bash
./mai transcribe audio.mp3 -o transcript.txt   # ถอดเสียงอย่างเดียว
./mai summarize transcript.txt --title "..."   # สรุปจาก transcript ที่มีอยู่
```

---

## ตั้งค่าอัดเสียงประชุมออนไลน์ (BlackHole)

Meet/Teams ไม่เปิดให้ดึงเสียงตรงๆ จึงอัดจาก "เสียงที่ออกลำโพงเครื่อง" แทน:

1. `brew install blackhole-2ch` (setup.sh ถามให้แล้ว)
2. เปิดแอป **Audio MIDI Setup** → สร้าง **Multi-Output Device** = ลำโพงจริง + BlackHole
   (เพื่อให้ได้ยินเสียงประชุมด้วย และ BlackHole รับเสียงไปพร้อมกัน)
3. ตั้งเสียง output ของ Mac เป็น Multi-Output Device นั้น
4. `./mai devices` เพื่อดูว่า BlackHole เป็น index ไหน → ใส่ใน `SYSTEM_DEVICE` ของ `.env`
   และไมค์คุณใส่ใน `MIC_DEVICE`

> ⚠️ เรื่องมารยาท/กฎหมาย: ควรแจ้งผู้เข้าร่วมก่อนบันทึกการประชุมทุกครั้ง

---

## โครงสร้าง
```
meeting_ai/
├── mai                 # ตัวเรียกใช้ CLI
├── setup.sh            # ติดตั้ง deps + โหลดโมเดล
├── .env                # ค่าเชื่อมต่อ (ไม่ commit)
└── meeting_ai/
    ├── config.py       # โหลด .env
    ├── recorder.py     # อัดเสียงสด (ffmpeg + BlackHole)
    ├── transcriber.py  # ถอดเสียง (whisper.cpp)
    ├── summarizer.py   # สรุป (gemma endpoint)
    ├── pipeline.py     # ร้อยขั้นตอน → Markdown
    └── cli.py          # คำสั่งย่อย
```
