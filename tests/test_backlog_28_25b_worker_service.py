"""BACKLOG #28 + #25b — ไฟล์ติดตั้ง worker: ค่าเฉพาะเครื่อง, การใส่เครื่องหมายคำพูด, การหมุน log.

ทั้งสองตั๋วอยู่ในไฟล์เดียวกันจึงทำพร้อมกันตามที่แบ็กล็อกสั่งไว้

เทสต์ชุดนี้ **ตัดตรรกะออกมาจากไฟล์จริงแล้วรัน** ไม่ใช่พิมพ์ซ้ำ: สคริปต์เชลล์/PowerShell
ที่ทดสอบด้วยการอ่านข้อความเอา จะผ่านได้ทั้งที่ของจริงพัง เพราะไม่มีใครรันมันในเทสต์เลย

สิ่งที่วัดได้ก่อนแก้ (บันทึกไว้เพราะตั๋วระบุอาการไว้เบากว่าความจริง):

- `-Name "x'; <คำสั่ง>; '"` ทำให้ตัวห่อที่ถูกสร้าง **พาร์สผ่าน 0 error แล้วรันคำสั่งนั้นจริง**
  ไม่ใช่แค่ "ไม่ escape แล้วพัง" · ชื่อธรรมดาอย่าง `O'Brien's PC` ทำให้ worker ได้
  `--name O` บวกอาร์กิวเมนต์ขยะอีกสองตัว โดยไม่มีอะไรเตือน
- การหมุน log เดิมเช็คขนาด **ครั้งเดียวก่อนเข้าไปป์ไลน์** ตั้งเพดาน 0 MB แล้วรันรอบเดียว
  ได้ log 890,994 ไบต์โดยไม่หมุนสักครั้ง — worker ที่ไม่พังจะไม่รีสตาร์ตเลยหลายเดือน
- `${s//@TOK@/$val}` ของ bash 5.2 ตีความ `&` ในฝั่งแทนที่ว่าเป็นข้อความที่แมตช์
  โฟลเดอร์ `/srv/a&b` จึงกลายเป็น `/srv/a@ROOT@b` เงียบ ๆ (เจอตอนเขียนตัวติดตั้งนี้เอง)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "worker-service.ps1"
INSTALLER = ROOT / "install-worker-service.sh"
TEMPLATE = ROOT / "meeting-ai-worker.service.in"
ENV_EXAMPLE = ROOT / "meeting-ai-worker.env.example"
PS1_TEXT = PS1.read_text(encoding="utf-8-sig")

# เหตุผลของ path เต็ม: เรียก "bash"/"powershell" ลอย ๆ ได้คนละตัวกับที่ which ชี้
BASH = shutil.which("bash")
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
PROD_HOST = "meeting-ai-swart.vercel.app"
# ค่าตั้งต้นต้องมาจากไฟล์จริง ไม่ใช่เขียนเลขซ้ำใน harness — ไม่งั้นเปลี่ยนค่าใน
# สคริปต์แล้วเทสต์ยังเขียวอยู่ เพราะมันทดสอบเลขของตัวเอง
KEEP = int(re.search(r"\[int\]\$MaxLogFiles = (\d+)", PS1_TEXT).group(1))


def _tmp() -> Path:
    # resolve(): mkdtemp บน Windows คืน path สั้นแบบ RUNNER~1 ซึ่งเทียบสตริงกับผลลัพธ์ไม่ตรง
    return Path(tempfile.mkdtemp(prefix="mai-svc-")).resolve()


class TestNothingShipsSomeoneElsesMachine(unittest.TestCase):
    """#28 — ไฟล์ที่ commit ไว้ต้องไม่ผูกกับเครื่องของเจ้าของ."""

    def test_the_installer_is_executable_and_lf_only(self):
        # CRLF ในไฟล์ .sh ทำให้ shebang ลงท้ายด้วยอักขระ CR แล้ว Linux ขึ้น
        # "/usr/bin/env: No such file or directory" ทั้งที่ bash มีอยู่
        # (.gitattributes บังคับ LF ไว้แล้ว เทสต์นี้กันไม่ให้ใครถอดออก)
        mode, _, _ = subprocess.run(
            ["git", "ls-files", "-s", "install-worker-service.sh"],
            cwd=str(ROOT), capture_output=True, text=True).stdout.partition(" ")
        self.assertEqual(mode, "100755", "ตัวติดตั้งต้องมีบิตรันได้")
        blob = subprocess.run(["git", "show", ":install-worker-service.sh"],
                              cwd=str(ROOT), capture_output=True).stdout
        self.assertEqual(blob.count(bytes([13])), 0, "มี CR ใน index")

    def test_the_old_ready_made_unit_is_gone(self):
        # ไฟล์เดิมฝัง User=, WorkingDirectory=, HOME= และ URL ของ production ไว้ทั้งหมด
        self.assertFalse((ROOT / "meeting-ai-worker.service").exists(),
                         "unit สำเร็จรูปกลับมาแล้ว — ต้องใช้ .in + install-worker-service.sh")

    def test_the_template_has_no_real_user_or_host(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        self.assertNotIn(PROD_HOST, text)
        self.assertNotIn("aistudiofebc", text)
        for token in ("@USER@", "@GROUP@", "@HOME@", "@ROOT@", "@ENVFILE@"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_the_variable_bits_come_from_an_environment_file(self):
        text = TEMPLATE.read_text(encoding="utf-8")
        # `-` ข้างหน้า = ไม่มีไฟล์ก็ยังสตาร์ตได้ ไม่งั้นเครื่องใหม่จะบูต unit ไม่ขึ้นเลย
        self.assertRegex(text, r"(?m)^EnvironmentFile=-@ENVFILE@$")
        self.assertRegex(text, r"(?m)^ExecStart=@ROOT@/mai worker --api \$\{MAI_API\}")

    def test_the_example_env_file_holds_no_secret(self):
        text = ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertNotIn(PROD_HOST, text)
        for secret in ("WORKER_TOKEN=", "DATABASE_URL=", "S3_SECRET", "LLM_API_KEY="):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, text)

    def test_no_powershell_script_defaults_to_production(self):
        # ตั๋วเขียนว่า `*.ps1` (พหูพจน์) — setup-worker.ps1 ก็ฝัง URL เดียวกันไว้
        for name in ("worker-service.ps1", "setup-worker.ps1"):
            with self.subTest(script=name):
                self.assertNotIn(PROD_HOST, (ROOT / name).read_text(encoding="utf-8-sig"))
        self.assertIn("$env:MAI_API", PS1_TEXT)


@unittest.skipUnless(BASH, "ต้องมี bash เพื่อรันตัวติดตั้งตัวจริง")
class TestTheInstallerRendersTheUnit(unittest.TestCase):
    """รัน install-worker-service.sh ตัวจริง — ไม่แตะ /etc ของเครื่องนี้."""

    def _run(self, *args: str, **over: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({f"MAI_SVC_{k.upper()}": v for k, v in over.items()})
        return subprocess.run([BASH, INSTALLER.as_posix(), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, cwd=str(ROOT))

    def test_printing_writes_nothing_and_fills_every_placeholder(self):
        out = self._run(user="alice", group="dev", home="/home/alice",
                        root="/srv/mai", envfile="/etc/default/mai").stdout
        self.assertRegex(out, r"(?m)^User=alice$")
        self.assertRegex(out, r"(?m)^Group=dev$")
        self.assertRegex(out, r"(?m)^WorkingDirectory=/srv/mai$")
        self.assertRegex(out, r"(?m)^Environment=HOME=/home/alice$")
        self.assertRegex(out, r"(?m)^EnvironmentFile=-/etc/default/mai$")
        for token in ("@USER@", "@GROUP@", "@HOME@", "@ROOT@", "@ENVFILE@"):
            with self.subTest(token=token):
                self.assertNotIn(token, out)

    def test_an_ampersand_in_a_path_survives(self):
        # bash 5.2: `&` ในฝั่งแทนที่ของ ${s//pat/rep} = "ข้อความที่แมตช์"
        # ถ้าตัวติดตั้งกลับไปใช้ทางนั้น บรรทัดนี้จะได้ /srv/a@ROOT@b
        out = self._run(user="u", group="g", home="/h", root="/srv/a&b/mai",
                        envfile="/e").stdout
        self.assertRegex(out, r"(?m)^WorkingDirectory=/srv/a&b/mai$")
        self.assertRegex(out, r"(?m)^ExecStart=/srv/a&b/mai/mai worker ")

    def test_installing_writes_the_unit_and_seeds_the_env_file(self):
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        units, envfile = tmp / "units", tmp / "etc" / "mai.env"
        r = self._run("--install", "--api", "https://one.example",
                      user="bob", group="bob", home="/home/bob", root="/srv/mai",
                      envfile=envfile.as_posix(), unit_dir=units.as_posix())
        self.assertEqual(r.returncode, 0, r.stderr)
        unit = (units / "meeting-ai-worker.service").read_text(encoding="utf-8")
        self.assertRegex(unit, r"(?m)^User=bob$")
        self.assertRegex(unit, r"(?m)^WorkingDirectory=/srv/mai$")
        self.assertIn("MAI_API=https://one.example",
                      envfile.read_text(encoding="utf-8"))

    def test_a_failing_daemon_reload_does_not_fail_the_install(self):
        """เครื่องนี้ไม่มี systemd จึงต้องปลอมมันขึ้นมา ไม่งั้นอาการนี้เห็นได้แต่บน CI.

        ของจริงที่ CI จับได้: รัน --install โดยไม่ใช่ root แล้ว systemctl ตอบ
        "Interactive authentication required" ทำให้ set -e ฆ่าสคริปต์ทิ้ง
        ทั้งที่ไฟล์ unit ถูกเขียนเรียบร้อยไปแล้ว
        """
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        fakebin = tmp / "bin"
        fakebin.mkdir()
        fake = fakebin / "systemctl"
        fake.write_text("#!/bin/sh\n"
                        "echo 'Interactive authentication required.' >&2\n"
                        "exit 1\n", encoding="utf-8", newline="\n")
        fake.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
        env.update({
            "MAI_SVC_USER": "bob", "MAI_SVC_GROUP": "bob", "MAI_SVC_HOME": "/h",
            "MAI_SVC_ROOT": "/srv/mai",
            "MAI_SVC_ENVFILE": (tmp / "mai.env").as_posix(),
            "MAI_SVC_UNIT_DIR": (tmp / "units").as_posix(),
        })
        r = subprocess.run([BASH, INSTALLER.as_posix(), "--install",
                            "--api", "https://x.example"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=str(ROOT))
        self.assertEqual(r.returncode, 0,
                         "daemon-reload ล้มไม่ควรทำให้การติดตั้งทั้งหมดล้ม: " + r.stderr)
        self.assertTrue((tmp / "units" / "meeting-ai-worker.service").exists())
        self.assertIn("daemon-reload", r.stdout)

    def test_running_it_twice_does_not_stack_up_api_lines(self):
        # ของที่ต่อท้ายไฟล์ตัวแปรทุกครั้งที่ติดตั้ง จะทำให้ค่าสุดท้ายชนะแบบเดาไม่ได้
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        units, envfile = tmp / "units", tmp / "mai.env"
        for url in ("https://one.example", "https://two.example", "https://three.example"):
            self._run("--install", "--api", url, user="bob", group="bob", home="/h",
                      root="/srv/mai", envfile=envfile.as_posix(),
                      unit_dir=units.as_posix())
        lines = [ln for ln in envfile.read_text(encoding="utf-8").split("\n")
                 if ln.startswith("MAI_API=")]
        self.assertEqual(lines, ["MAI_API=https://three.example"])


class _Wrapper:
    """ตัดบล็อกที่สร้าง run-worker-hidden.ps1 ออกมาจากไฟล์จริงแล้วรัน.

    ไม่เรียก `worker-service.ps1 install` ตรง ๆ เด็ดขาด: มันจะไปลงทะเบียน scheduled task
    จริงบนเครื่องที่รันเทสต์
    """

    @classmethod
    def setUpClass(cls) -> None:
        lines = PS1_TEXT.split("\n")
        q_start = next(i for i, l in enumerate(lines) if l.startswith("function Q("))
        q_end = next(i for i in range(q_start, len(lines)) if lines[i] == "}")
        here_start = next(i for i, l in enumerate(lines) if l.strip() == '@"')
        here_end = next(i for i, l in enumerate(lines) if l.startswith('"@ | Set-Content'))
        cls.block = lines[q_start:q_end + 1] + lines[here_start:here_end + 1]

    def _generate(self, tmp: Path, name: str, api: str = "https://x.example",
                  max_log_mb: int = 20, py: str = "") -> Path:
        # ต้องเป็นพาธเต็มของล่ามที่มีจริง: บน ubuntu มีแต่ `pwsh` ไม่มี `powershell`
        # ถ้าเรียกชื่อที่ไม่มีอยู่ ตัวห่อจะไม่ได้เอาต์พุตอะไรเลย แล้วเทสต์การหมุน log
        # จะเขียวทั้งที่ไม่ได้หมุนอะไร (CI จับได้ ไม่ใช่เครื่องนี้)
        py = py or POWERSHELL
        harness = [
            '$ErrorActionPreference = "Stop"',
            f'$root = {self._lit(tmp.as_posix())}',
            f'$py = {self._lit(py)}',
            f'$logFile = {self._lit((tmp / "worker.log").as_posix())}',
            f'$MaxLogMB = {max_log_mb}',
            f'$MaxLogFiles = {KEEP}',
            f'$Name = {self._lit(name)}',
            f'$Api = {self._lit(api)}',
            f'$runner = {self._lit((tmp / "generated.ps1").as_posix())}',
        ] + self.block
        gen = tmp / "gen.ps1"
        # BOM จำเป็น: PowerShell 5.1 อ่านไฟล์ UTF-8 ที่ไม่มี BOM เป็น cp874 แล้วภาษาไทย
        # ในบล็อกที่ตัดมาจะเพี้ยนจนพาร์สพัง — ซึ่งจะกลายเป็นเทสต์ที่แดงเพราะ harness เอง
        gen.write_text("\n".join(harness) + "\n", encoding="utf-8-sig", newline="\r\n")
        self._ps(gen)
        return tmp / "generated.ps1"

    @staticmethod
    def _lit(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    def _ps(self, script: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180)

    def _worker_args(self, generated: Path) -> list[str]:
        """อาร์กิวเมนต์ที่ worker จะได้จริง — อ่านจาก AST ไม่ใช่จากการเดาด้วย regex."""
        probe = generated.parent / "probe.ps1"
        probe.write_text(
            "$src = Get-Content -LiteralPath " + self._lit(str(generated)) +
            " -Raw -Encoding UTF8\n"
            "$ast = [System.Management.Automation.Language.Parser]::ParseInput("
            "$src, [ref]$null, [ref]$null)\n"
            "$cmd = $ast.FindAll({ param($x) $x -is "
            "[System.Management.Automation.Language.CommandAst] }, $true) | "
            "Where-Object { \"$_\" -like '*meeting_ai worker*' } | Select-Object -First 1\n"
            "$cmd.CommandElements | ForEach-Object { $_.Extent.Text }\n",
            encoding="utf-8-sig", newline="\r\n")
        out = self._ps(probe)
        self.assertEqual(out.returncode, 0, out.stderr)
        return [ln.strip() for ln in out.stdout.split("\n") if ln.strip()]

    def _ps_parse_errors(self, generated: Path) -> int:
        probe = generated.parent / "parse.ps1"
        probe.write_text(
            "$src = Get-Content -LiteralPath " + self._lit(str(generated)) +
            " -Raw -Encoding UTF8\n"
            "$e = $null\n"
            "[void][System.Management.Automation.Language.Parser]::ParseInput("
            "$src, [ref]$null, [ref]$e)\n"
            "$e.Count\n",
            encoding="utf-8-sig", newline="\r\n")
        out = self._ps(probe)
        self.assertEqual(out.returncode, 0, out.stderr)
        return int(out.stdout.strip())


@unittest.skipUnless(POWERSHELL, "ต้องมี powershell เพื่อรันตรรกะของสคริปต์ตัวจริง")
class TestTheQuoting(_Wrapper, unittest.TestCase):
    """#28 — ค่าที่ถูกฝังลงตัวห่อต้องไม่หลุดออกจากสตริง."""

    def test_a_plain_name_still_works(self):
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        args = self._worker_args(self._generate(tmp, "เครื่องหลัก (RTX 3050)"))
        self.assertEqual(args[-1], "'เครื่องหลัก (RTX 3050)'")
        self.assertEqual(args[-2], "--name")

    def test_an_apostrophe_in_the_name_stays_one_argument(self):
        # ก่อนแก้: ได้ 9 ชิ้น โดย --name ได้แค่ 'O' แล้วมีขยะตามมาอีกสองตัว
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        args = self._worker_args(self._generate(tmp, "O'Brien's PC"))
        self.assertEqual(args[-2], "--name")
        self.assertEqual(args[-1], "'O''Brien''s PC'")
        self.assertEqual(len(args), 8, f"อาร์กิวเมนต์แตกเป็น {len(args)} ชิ้น: {args}")

    def test_a_crafted_name_cannot_run_a_command(self):
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        marker = tmp / "PWNED.txt"
        hostile = ("x'; New-Item -ItemType File "
                   + self._lit(marker.as_posix()) + " -Force | Out-Null; '")
        generated = self._generate(tmp, hostile, py="powershell")
        run = self._ps(generated)
        self.assertFalse(marker.exists(),
                         f"คำสั่งที่ฉีดมากับ -Name ถูกรันจริง (rc={run.returncode})")
        args = self._worker_args(generated)
        self.assertEqual(args[-2], "--name")
        self.assertEqual(len(args), 8)

    def test_a_path_with_an_apostrophe_does_not_break_the_wrapper(self):
        # โฟลเดอร์โปรเจกต์ของคนอื่นอาจอยู่ใต้ C:\Users\O'Brien\...
        tmp = _tmp() / "O'Brien"
        tmp.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, tmp.parent, ignore_errors=True)
        generated = self._generate(tmp, "pc")
        text = generated.read_text(encoding="utf-8-sig")
        self.assertIn("O''Brien", text)
        errs = self._ps_parse_errors(generated)
        self.assertEqual(errs, 0, "ตัวห่อที่ได้พาร์สไม่ผ่าน")


@unittest.skipUnless(POWERSHELL, "ต้องมี powershell เพื่อรันตรรกะของสคริปต์ตัวจริง")
class TestTheLogRotates(_Wrapper, unittest.TestCase):
    """#25b — หมุน log ระหว่างที่ worker ยังรันอยู่ ไม่ใช่แค่ตอนเริ่มโพรเซส."""

    def _run_with_output(self, tmp: Path, lines: int, max_log_mb: int) -> None:
        fake = tmp / "fakepy.ps1"
        fake.write_text(f'for ($i = 1; $i -le {lines}; $i++) {{ "line $i " + '
                        f'[string]::new("x", 200) }}\n',
                        encoding="utf-8-sig", newline="\r\n")
        generated = self._generate(tmp, "pc", max_log_mb=max_log_mb)
        text = generated.read_text(encoding="utf-8-sig")
        # เปลี่ยนเฉพาะ "คำสั่งที่ถูกเรียก" — ตรรกะการหมุนยังเป็นของไฟล์จริงทั้งหมด
        text = re.sub(r"-m meeting_ai worker --api \S+ --name \S+",
                      f"-NoProfile -File '{fake.as_posix()}'", text)
        generated.write_text(text, encoding="utf-8-sig", newline="\r\n")
        run = self._ps(generated)
        self.assertEqual(run.returncode, 0, run.stderr)
        # ยืนยันว่าคำสั่งที่ตัวห่อเรียก ทำงานและเอาต์พุตไหลเข้า log จริง
        # (เพดาน 0 ทำให้เกือบทุกบรรทัดถูกทิ้งตามตั้งใจ จะวัดด้วยขนาดไฟล์ไม่ได้)
        seen = "".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in sorted(tmp.glob("worker.log*")))
        self.assertRegex(seen, r"line \d+",
                         "ไม่เจอเอาต์พุตของคำสั่งใน log — ตัวห่อเรียกอะไรไม่ได้ เทสต์จะกลวง")

    def test_it_rotates_while_the_worker_is_still_running(self):
        # ของเดิม: เช็คครั้งเดียวก่อนเข้าไปป์ไลน์ -> ไฟล์เดียวโตไปเรื่อย ๆ ไม่มี .1 เลย
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self._run_with_output(tmp, lines=300, max_log_mb=0)
        log = tmp / "worker.log"
        self.assertTrue(log.exists())
        self.assertTrue((tmp / "worker.log.1").exists(), "ไม่มีการหมุนเลยระหว่างรัน")
        self.assertLess(log.stat().st_size, 2000, "ไฟล์หลักยังโตทั้งที่ควรถูกหมุนไปแล้ว")

    def test_it_keeps_only_the_configured_number_of_old_files(self):
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self._run_with_output(tmp, lines=300, max_log_mb=0)
        olds = sorted(p.name for p in tmp.glob("worker.log.*"))
        self.assertEqual(olds, [f"worker.log.{i}" for i in range(1, KEEP + 1)])
        self.assertGreaterEqual(KEEP, 2, "เก็บย้อนหลังรุ่นเดียวคือแทบไม่ได้เก็บ")

    def test_a_small_log_is_left_alone(self):
        tmp = _tmp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self._run_with_output(tmp, lines=20, max_log_mb=20)
        self.assertTrue((tmp / "worker.log").exists())
        self.assertEqual(list(tmp.glob("worker.log.*")), [])


if __name__ == "__main__":
    unittest.main()
