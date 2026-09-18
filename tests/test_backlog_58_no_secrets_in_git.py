"""BACKLOG #58 — ค่าลับที่ยังใช้อยู่ ต้องไม่อยู่ในไฟล์ที่ git เก็บ.

เทสต์ชุดนี้เกิดขึ้นเพราะ runbook เขียนไว้เองว่า *"ไม่มี credential อยู่ใน git …
ไม่ต้องล้าง git history"* แล้วพอไปวัดจริงกลับพบว่า **ไม่จริง**: คอมมิต `b6daf68`
(2026-08-10) มีไฟล์สองใบที่เกิดจากพิมพ์คำสั่ง PowerShell พลาด ซึ่งข้างในมี
`LLM_API_KEY` ตัวที่ยังใช้อยู่ทุกวันนี้แบบเต็มค่า และ repo นี้เปิดสาธารณะ

บทเรียนคือ **ข้ออ้างเรื่องความปลอดภัยที่ไม่มีใครรันซ้ำ จะค่อย ๆ กลายเป็นเท็จโดยไม่มีใครรู้**
ไฟล์นี้จึงเปลี่ยนข้ออ้างนั้นให้เป็นสิ่งที่รันได้

วิธีตรวจ: อ่านค่าจริงจาก `.env` (ซึ่งไม่เคยถูก track) แล้วหาสตริงนั้นตรง ๆ ในของที่ git เก็บ
ไม่มีการพิมพ์ค่าออกมาเลย แม้ตอนเทสต์ล้ม — ข้อความบอกแค่ **ชื่อ**ตัวแปรกับไฟล์ที่เจอ

เครื่องที่ไม่มี `.env` (เช่น CI) จะข้ามไป เพราะไม่มีอะไรให้เทียบ
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"

# ตัวแปรที่ถือว่าเป็นค่าลับจริง — ที่เหลือใน .env เป็นพาธ/ชื่อโมเดล/หมายเลขอุปกรณ์
SECRET_KEYS = ("DATABASE_URL", "LLM_API_KEY", "STT_API_KEY", "WORKER_TOKEN",
               "VERCEL_TOKEN", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY")
# สั้นกว่านี้ค้นแล้วชนของอื่นมั่ว ๆ (เช่น "th" หรือเลขพอร์ต)
MIN_LEN = 12


def _secrets() -> dict[str, str]:
    out: dict[str, str] = {}
    text = ENV.read_text(encoding="utf-8", errors="replace")
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key in SECRET_KEYS and len(value) >= MIN_LEN:
            out[key] = value
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, errors="replace").stdout


@unittest.skipUnless(ENV.exists(), "ไม่มี .env บนเครื่องนี้ จึงไม่มีค่าจริงให้เทียบ")
class TestNoLiveSecretIsTracked(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.secrets = _secrets()

    def test_the_env_file_itself_is_ignored(self):
        # ชั้นแรกสุด: ถ้า .env หลุด track เข้าไป ทุกอย่างที่เหลือไม่มีความหมาย
        self.assertTrue(_git("check-ignore", ".env").strip(),
                        ".env ไม่ได้อยู่ใน .gitignore แล้ว")
        self.assertNotIn(".env", _git("ls-files", ".env").split(),
                         ".env ถูก track อยู่")

    def test_we_found_something_to_check(self):
        # กันเทสต์กลวง: ถ้าอ่าน .env ไม่ออก ทุกข้อข้างล่างจะผ่านโดยไม่ได้ตรวจอะไร
        self.assertGreaterEqual(len(self.secrets), 3,
                                f"อ่านค่าลับจาก .env ได้แค่ {len(self.secrets)} ตัว "
                                f"— รูปแบบไฟล์เปลี่ยนไปหรือเปล่า")

    def test_no_tracked_file_contains_a_live_secret(self):
        tracked = [p for p in _git("ls-files").split("\n") if p.strip()]
        self.assertGreater(len(tracked), 50, "อ่านรายการไฟล์ที่ track ไม่ได้")
        hits: list[str] = []
        for rel in tracked:
            path = ROOT / rel
            try:
                blob = path.read_bytes()
            except OSError:
                continue
            try:
                text = blob.decode("utf-8")
            except UnicodeDecodeError:
                continue
            for name in self.secrets:
                if self.secrets[name] in text:
                    hits.append(f"{name} -> {rel}")
        # ข้อความแสดงแค่ชื่อตัวแปรกับชื่อไฟล์ ไม่มีค่าหลุดออกมา
        self.assertEqual(hits, [], f"ค่าลับที่ยังใช้อยู่ไปอยู่ในไฟล์ที่ track: {hits}")


@unittest.skipUnless(ENV.exists(), "ไม่มี .env บนเครื่องนี้ จึงไม่มีค่าจริงให้เทียบ")
class TestWhatIsStillInHistory(unittest.TestCase):
    """history เปลี่ยนย้อนหลังไม่ได้ เทสต์นี้จึงเป็น "ตัวนับ" ไม่ใช่ "ประตู".

    ทำให้เห็นชัดว่าตอนนี้เหลืออะไรค้างอยู่ และถ้าเจอตัวใหม่เพิ่ม จะได้รู้ทันที
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.secrets = _secrets()

    def _commits_with(self, value: str) -> list[str]:
        out = _git("log", "--all", "--format=%h", "-S", value)
        return [h for h in out.split("\n") if h.strip()]

    def test_only_the_known_key_is_in_history(self):
        """`LLM_API_KEY` คือตัวเดียวที่รู้ว่าหลุด — ตัวอื่นต้องเป็นศูนย์.

        ถ้าข้อนี้แดงเพราะมีชื่อใหม่โผล่มา แปลว่ามีค่าลับหลุดเข้า git เพิ่ม ให้หมุนตัวนั้น
        ทันทีแล้วค่อยมาแก้เทสต์ ไม่ใช่กลับกัน
        """
        leaked = sorted(name for name, v in self.secrets.items() if self._commits_with(v))
        self.assertEqual(leaked, ["LLM_API_KEY"],
                         "รายชื่อค่าลับที่อยู่ใน git history เปลี่ยนไปจากที่บันทึกไว้")

    def test_the_runbook_says_it_out_loud(self):
        # ข้ออ้างเดิมของ runbook คือ "ไม่มี credential อยู่ใน git" ซึ่งผิด
        text = (ROOT / "docs" / "runbooks" / "rotate-credentials.md").read_text(
            encoding="utf-8")
        self.assertIn("b6daf68", text, "runbook ต้องชี้คอมมิตที่หลุดไว้ตรง ๆ")
        self.assertIn("LLM_API_KEY", text)
        self.assertNotIn("ไม่ต้องล้าง git history", text,
                         "ข้อสรุปเดิมที่ผิดยังอยู่ใน runbook")


if __name__ == "__main__":
    unittest.main()
