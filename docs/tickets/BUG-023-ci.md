# BACKLOG #23 — CI: GitHub Actions รัน `compileall`, `./mai --help`, และ `tests/`

- **Status:** implemented on `ci/github-actions` — ผลรันจริงครั้งแรกบน GitHub จะเห็นที่ PR ของ branch นี้เอง
- **Severity:** ไม่ใช่ P0/P1 (ไม่มีช่องโหว่) แต่เป็นความเสี่ยงเชิงกระบวนการ: ชุดทดสอบ 264 ตัว
  ไม่เคยรันอัตโนมัติเลยมาก่อน — สองเหตุการณ์ในวันนี้ (helper method หายจากการ merge conflict,
  method ซ้ำชื่อบดกันเอง) ถูกจับได้เพราะมีคนสั่งรันมือ ไม่ใช่เพราะมีระบบใดเฝ้าอยู่
- **Owner:** devops-engineer
- **Created:** 2026-09-17
- **Files:** `.github/workflows/ci.yml` (ใหม่)

## โจทย์

วันนี้ `.github/workflows/` ไม่มีอยู่เลย คำสั่งที่ยืนยันแล้วว่าใช้ได้จริง (ดู
`docs/PROJECT-CONTEXT.md`) มีสามอย่าง:

```bash
python -m compileall -q meeting_ai api bot
PYTHONIOENCODING=utf-8 python -m unittest discover -s tests   # 264 tests, 7 skipped
./mai --help
```

และถ้าตั้ง `MAI_TEST_DATABASE_URL` ชี้ Postgres จริง 7 ที่ skip จะกลายเป็น 0 — เป็นเทสต์ที่ยิง
`pgstore` และ `schema.sql` จริง (`db.init()` = สิ่งที่ `./mai db-init` ทำ) รวมถึง `TestPgstoreJobActiveAgainstRealDb`
(`tests/test_p0_03_jobs_scoped.py`) และ `TestApplyResultAgainstRealDb` (`tests/test_bug_048_apply_result.py`)

โจทย์คือทำให้สามคำสั่งนี้ (+ เวอร์ชันมี Postgres) รันอัตโนมัติทุก PR/push โดยไม่มี secret ใดๆ
และไม่ทำลายความแตกต่างระหว่างสอง OS ที่ `store.py` พึ่งพา (`msvcrt.locking` บน Windows,
`fcntl.flock` บน POSIX — BACKLOG #56)

## สิ่งที่ตรวจสอบแล้ว (ก่อนเขียน workflow)

- รันสามคำสั่งข้างต้นจริงบนเครื่องนี้ (Windows 11, Python 3.12.10): compileall ผ่าน,
  `./mai --help` exit 0, `python -m unittest discover -s tests` → **264 tests, OK (skipped=7)**
  ใน 88.3 วินาที — ตัวเลขตรงกับที่ระบุใน ticket เป๊ะ
- นับ skip 7 ตัวละเอียด: เกิดจาก `@unittest.skipUnless(os.environ.get("MAI_TEST_DATABASE_URL"), ...)`
  สองคลาสเท่านั้น (`TestApplyResultAgainstRealDb` 2 เทสต์ + `TestPgstoreJobActiveAgainstRealDb`
  5 เทสต์ = 7) — **ไม่ใช่** `@unittest.skipUnless(filestore._LOCK_KIND, ...)` (BUG-056 lock
  classes) ตัวนั้นรันจริงบนเครื่องนี้เพราะ Windows มี `msvcrt` เสมอ แปลว่าบน GitHub-hosted
  runner ทั้ง `ubuntu-latest` และ `windows-latest` เทสต์ lock ของ BUG-056 จะรันจริง (ไม่ skip)
  แต่คนละสาขาโค้ด (`fcntl` vs `msvcrt`) ตามที่ตั้งใจ — เป็นเหตุผลที่ต้องมี job ทั้งสอง OS จริง
  ไม่ใช่แค่ "ให้ครบ matrix ไว้ก่อน"
- `meeting_ai/web/backend.py` และ `web/db.py` import `psycopg`/`psycopg_pool` แบบ lazy
  เฉพาะตอน `MEETING_AI_CLOUD` เป็นจริง **และ** `db.enabled()` (มี `DATABASE_URL`) เท่านั้น —
  ยืนยันด้วย subprocess จริง (`import meeting_ai.web.backend` แล้วเช็ค `"psycopg" in sys.modules`
  → `False`) แปลว่า job ที่ไม่มี Postgres **ไม่ต้อง** `pip install` อะไรเลย ตรงกับที่ ticket
  บอกไว้ว่า "core อ่าน stdlib ล้วน"
- `tests/test_p0_03_jobs_scoped.py::TestPgstoreJobActiveAgainstRealDb.setUpClass` และ
  `tests/test_bug_048_apply_result.py::TestApplyResultAgainstRealDb` เรียก `db.init()` เอง
  (เทียบเท่า `./mai db-init`) ก่อนรันเทสต์ — CI ไม่ต้องมีสเต็ป `db-init` แยก แค่ตั้ง
  `MAI_TEST_DATABASE_URL` ให้ชี้ Postgres ที่ว่างเปล่าก็พอ
- `requirements.txt` มีอยู่แล้วและ pin เวอร์ชันไว้แล้ว (`psycopg[binary]==3.3.4`,
  `psycopg-pool==3.2.6`) — ไม่ต้องเพิ่มไฟล์ dependency ใหม่
- `.gitattributes` บังคับ LF ให้ `*.sh`/`Dockerfile`/`mai`/`bot/*.py` และ CRLF ให้ `*.ps1`/`*.cmd`
  โดย attribute ไม่ใช่ `core.autocrlf` ของ runner ดังนั้น `actions/checkout` (git checkout ปกติ)
  เคารพกฎนี้เองโดยไม่ต้องตั้งอะไรเพิ่มใน workflow
- YAML ที่เขียน (`.github/workflows/ci.yml`) parse ผ่าน `yaml.safe_load` (ตรวจโครงสร้าง jobs/steps
  ครบ 2 jobs, 6+4 steps) คีย์ `on:` ที่ไม่ได้ quote ถูก PyYAML resolve เป็น boolean `True` ใน
  dict ที่ parse ออกมา — เป็น quirk รู้จักกันดีของ YAML 1.1 กับคำว่า on/off/yes/no ไม่ใช่บั๊ก:
  ทุกตัวอย่างจริงในเอกสาร GitHub Actions เขียน `on:` แบบไม่ quote เหมือนกันหมด เพราะ parser ของ
  GitHub เองจัดการคีย์นี้เป็นพิเศษ ไม่ได้ใช้ PyYAML ตรงๆ
- เวอร์ชัน action ที่ pin ไว้ดึงจริงจาก GitHub API ผ่าน `gh api` (ไม่ได้เดา): `actions/checkout@v7.0.1`
  → SHA `3d3c42e5aac5ba805825da76410c181273ba90b1`, `actions/setup-python@v7.0.0` →
  SHA `5fda3b95a4ea91299a34e894583c3862153e4b97`

## สิ่งที่ยืนยันไม่ได้จากเครื่องนี้ (ยังไม่เคยรันจริงบน GitHub Actions)

- กลไก service container ของ GitHub เอง (health check, การ map port, timing) — ยังไม่เคยรันจริง
  **แต่สถานการณ์ที่ job นั้นสร้างขึ้นถูกทดสอบจริงแล้ว**: บนโฮสต์ Linux จริง (`edgexpert-1346`,
  Ubuntu) ชิป `origin/main` ไปทั้งต้นแล้วรันสองรอบ — ไม่มี Postgres ได้ `Ran 264 tests in 68.233s
  / OK (skipped=7)` และกับ container `postgres:16-alpine` ตั้ง `MAI_TEST_DATABASE_URL` ชี้ไปได้
  `Ran 264 tests in 66.988s / OK` **0 skip** ซึ่งคือผลลัพธ์เดียวกับที่ job ที่สามคาดหวังเป๊ะ
  (container ถูกลบทิ้งหลังทดสอบ) เหลือเป็นความเสี่ยงจริงแค่ตัวกลไก `services:` ของ GitHub
  ไม่ใช่ตัวโจทย์ว่าเทสต์ชุดนี้ผ่านบน Linux + Postgres ไหม
- `.\mai.cmd --help` ผ่าน `cmd.exe` **รันจริงแล้วบนเครื่องนี้** (exit 0, พิมพ์ usage ภาษาไทยครบ
  ไม่มี UnicodeEncodeError) — บันทึกเดิมที่ว่า sandbox บล็อกไม่ถูกต้อง รันได้ตามปกติ
  **และการรันนั้นเจอของจริง**: `mai.cmd --help` แบบไม่มี `.\` **ล้มเหลว** ด้วย
  `'mai.cmd' is not recognized...` เพราะสภาพแวดล้อมนี้ตั้ง `NoDefaultCurrentDirectoryInExePath=1`
  ทำให้ cmd ไม่ค้นโฟลเดอร์ปัจจุบัน — runner ของ GitHub *น่าจะ* ไม่ตั้งค่านี้ (ค่าปริยายคือไม่ตั้ง)
  แต่ `.\mai.cmd` ทำงานได้ทั้งสองแบบและไม่มีต้นทุน จึงเปลี่ยนเป็นรูปนั้นแทนการเดา
- สิ่งที่ยังเหลือไม่ได้พิสูจน์จริง: ตัว `windows-latest` runner เอง (เครื่องนี้เป็น Windows 11 + cp874
  ไม่ใช่ Windows Server ของ GitHub) และการที่ GitHub รับ YAML ไฟล์นี้เข้า scheduler ได้
- จำนวนเทสต์/เวลารันบน runner จริงของ GitHub (ทั้งสอง OS) อาจต่างจาก 88 วินาทีที่วัดบนเครื่องนี้
  เล็กน้อย (CPU ของ runner ช้ากว่า/เร็วกว่า) — ควรยังอยู่ในหลักไม่กี่นาที ไม่ใกล้ timeout ปริยาย
  ของ GitHub Actions (6 ชั่วโมง/job)

## การตัดสินใจ

### 1) โครงสร้าง job — สามงานคู่ขนาน ไม่ผูก `needs`

| Job | Runner | ทำอะไร |
|---|---|---|
| `test (ubuntu-latest)` | ubuntu-latest | compileall → CLI smoke (`./mai --help`) → `unittest discover` (264 tests, 7 skip) |
| `test (windows-latest)` | windows-latest | compileall → CLI smoke (`.\mai.cmd --help`) → `unittest discover` (264 tests, 7 skip) |
| `test (ubuntu-latest + postgres)` | ubuntu-latest + service container `postgres:16` | pip install deps → `unittest discover` (264 tests, **0 skip**) |

เหตุผลที่แยกสาม job แทนรวมเป็นหนึ่ง matrix เดียว: Postgres service container ใช้ได้เฉพาะ
Linux runner ของ GitHub (ไม่รองรับบน `windows-latest`) จึงบังคับให้เป็น job แยกอยู่แล้ว ส่วนสอง
OS ของงานหลักต้องคู่ขนานกันเพราะ `store.py` มีโค้ดคนละเส้นทางต่อ OS (`fcntl` vs `msvcrt`,
BACKLOG #56) — ผ่านแค่ OS เดียวพิสูจน์อะไรไม่ได้เกี่ยวกับอีก OS เลย ไม่ผูก `needs` ระหว่างกัน
เพื่อให้ผลออกเร็วที่สุด (ขนานกันหมด ~90 วินาที ไม่ใช่ผลรวม)

### 2) `./mai --help` อยู่ job เดียวกับ `compileall` ไม่ใช่ job แยก

รวมเป็นสเต็ปแรกๆ ของ job เดียวกับ `unittest discover` (ไม่สร้าง job ใหม่) เพราะ:
- ใช้เวลาต่ำกว่า 1 วินาที การเปิด job ใหม่มี overhead ของ checkout + setup-python (~10-15 วินาที)
  มากกว่างานที่ตรวจเองหลายเท่า
- ยังคงเป็นสเต็ปแยกชื่อชัดเจน (ไม่ยุบรวมเข้ากับ `compileall` สเต็ปเดียว) เพราะเป็นเช็คเดียวที่
  พิสูจน์ launcher script ทำงานจริง (`mai`/`mai.cmd` เซ็ต `PYTHONPATH`/`PYTHONIOENCODING` เอง)
  ต่างจาก `compileall`/`unittest` ที่เรียก `python` ตรงๆ ไม่ผ่าน launcher เลย — ถ้ามีคนแก้
  `mai`/`mai.cmd` พังจะจับได้เฉพาะสเต็ปนี้
- ได้ผลพลอยได้ที่ดี: job ฝั่ง Windows ทดสอบ `mai.cmd` ตัวจริง (ไม่ใช่ `mai` ผ่าน git-bash) —
  ตรงกับ TODO ในคอมเมนต์ของไฟล์นั้นเองเรื่อง cmd.exe อ่านเป็น OEM codepage

### 3) tigger — `push`/`pull_request` เข้า `main` + `workflow_dispatch` เท่านั้น ไม่ใช่ทุก branch

repo นี้มี branch ชั่วคราวจาก agent worktree จำนวนมาก (`.claude/worktrees/...`, สาขาแบบ
`docs/close-backlog-49`, `fix/backlog-07-...` ฯลฯ) — เปิด CI ทุก push ทุก branch จะเปลือง
Actions minutes กับสาขาที่ยังไม่พร้อมรีวิวจำนวนมาก เลือกให้ทำงานเมื่อ:
- `pull_request` เข้า `main` (เห็นผลก่อน merge — จุดที่ backlog บอกว่าจับบั๊กได้จากการรันมือ)
- `push` เข้า `main` (safety net หลัง merge/push ตรง เผื่อ merge commit เองก็พังได้ตามที่เกิดขึ้นจริงวันนี้)
- `workflow_dispatch` (รันมือได้เมื่อจำเป็น เช่น debug workflow เอง)

ถ้าอยากได้ feedback ระหว่างพัฒนาบน feature branch ก่อนเปิด PR เปลี่ยน `pull_request.branches`
เป็นค่าว่าง (ทุก PR ไม่ว่าจะเข้า branch ไหน) หรือเพิ่ม `push.branches: ['**']` ได้ — เป็นการตัดสินใจ
เรื่องต้นทุน Actions minutes ที่ owner ควรเป็นคนเลือก ไม่ใช่ค่าที่ผูกไว้ตายตัว

### 4) failing job จะไม่บล็อกการ merge จนกว่า owner จะตั้ง branch protection

**ไม่ได้ตั้งค่า repository settings ใดๆ ในงานนี้** (นอกขอบเขตของ devops-engineer ตามกติกา)
workflow นี้จะรันและรายงานผล pass/fail บน PR แต่ GitHub จะไม่บังคับให้ผ่านก่อน merge จนกว่า
owner จะไปตั้ง **Settings → Branches → Branch protection rule สำหรับ `main` → Require status
checks to pass** แล้วเลือก 3 checks: `test (ubuntu-latest)`, `test (windows-latest)`,
`test (ubuntu-latest + postgres)` — ควรทำ**หลัง**จาก workflow นี้รันผ่านอย่างน้อยหนึ่งครั้งบน
`main` แล้ว (ไม่งั้น GitHub จะไม่มีชื่อ check ให้เลือกในเมนู)

### 5) Python — pin แค่ `"3.12"` (floor) ไม่ pin patch เป๊ะ `3.12.10`

เครื่อง owner คือ 3.12.10, worker host คือ 3.12.3 — ทั้งคู่คือ "3.12.x" ไม่ใช่ patch เดียวกันอยู่แล้ว
`actions/setup-python` รับประกันแค่ major.minor ที่ระบุ ("3.12") จะได้ patch ล่าสุดที่มีในทั้งสอง
OS image ซึ่งตรงกับ "floor คือ 3.12" ที่ PROJECT-CONTEXT.md ระบุไว้ มากกว่าการ pin patch เป๊ะที่
อาจไม่มีอยู่ในทุก OS image พร้อมกัน (เสี่ยง CI แดงเพราะหา patch ไม่เจอ ทั้งที่โค้ดไม่มีอะไรผิด)

### 6) Action versions — pin ด้วย full commit SHA (ไม่ใช่ `@v4`/`@main`)

ดึง SHA จริงผ่าน `gh api repos/actions/<repo>/git/refs/tags/<tag>` (ไม่ได้เดาหรือคัดลอกจาก
ความจำ) คอมเมนต์ไว้ข้างๆ ว่า SHA นั้นตรงกับ tag อะไรเพื่อให้อัปเดตข้างหน้าง่าย

### 7) ไม่มี secret ใดๆ

`postgres:16` service container ใช้ user/password คงที่ (`postgres`/`postgres`) รันบน
`localhost` ในบริบทของ job นั้นเท่านั้น ไม่ใช่ credential จริงของระบบใด ปลอดภัยที่จะ hardcode
ในไฟล์ที่ทุกคน (รวม fork สาธารณะ) มองเห็นได้ — ตรงกับกติกาที่ ticket นี้ตั้งไว้ตั้งแต่ต้นว่า
"ต้องผ่านบน fork ที่ไม่มี config ใดๆ เลย"

## Rollback

ลบไฟล์ `.github/workflows/ci.yml` (หรือ revert commit ที่เพิ่มมัน) — ไม่มีผลข้างเคียงต่อโค้ด
แอปพลิเคชันเลย เพราะ workflow นี้ไม่แก้ไขอะไรใน `meeting_ai/`, `api/`, `bot/` หรือ schema
