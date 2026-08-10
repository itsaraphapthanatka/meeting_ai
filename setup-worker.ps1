<#
.SYNOPSIS
  ติดตั้งเครื่องให้พร้อมเป็น worker ของ meeting_ai แล้วรันได้เลย

.DESCRIPTION
  worker คือเครื่องที่ทำงานหนัก (ถอดเสียง / แยกผู้พูด / สรุป) ให้กับเว็บที่อยู่บน cloud
  สคริปต์นี้ลง ffmpeg + whisper.cpp + โมเดล แล้วเขียน .env ให้ครบ

  worker ไม่ต้องใช้ฐานข้อมูลและไม่ต้องมีคีย์ของ R2 — ไฟล์เสียงมาเป็นลิงก์ที่เซ็นแล้วจากเซิร์ฟเวอร์

.EXAMPLE
  .\setup-worker.ps1 -WorkerToken "xxx" -LlmApiKey "sk-xxx"
  .\setup-worker.ps1 -WorkerToken "xxx" -LlmApiKey "sk-xxx" -Start
  .\setup-worker.ps1 -WorkerToken "xxx" -LlmApiKey "sk-xxx" -Cpu -NoDiarize

.NOTES
  ต้องคัดลอกโฟลเดอร์โปรเจกต์มาที่เครื่องนี้ก่อน แล้วรันสคริปต์จากในโฟลเดอร์นั้น
#>
param(
    [Parameter(Mandatory = $true)][string]$WorkerToken,
    [Parameter(Mandatory = $true)][string]$LlmApiKey,
    [string]$Api = "https://meeting-ai-swart.vercel.app",
    [string]$LlmBaseUrl = "https://consoletoken.aunjai.org/api/v1",
    [string]$LlmModel = "gemma-4-12b",
    [string]$Lang = "th",
    [string]$WhisperVersion = "v1.9.2",
    [string]$WhisperModel = "large-v3-turbo-q5_0",
    [switch]$Cpu,        # บังคับใช้ build ที่ไม่ใช้ GPU
    [switch]$NoDiarize,  # ข้ามการติดตั้งตัวแยกผู้พูด
    [switch]$Start       # ติดตั้งเสร็จแล้วรัน worker ต่อเลย
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location $root

function Step($n, $msg) { Write-Host "`n==> [$n] $msg" -ForegroundColor Cyan }
function Ok($msg)       { Write-Host "    OK   $msg" -ForegroundColor Green }
function Skip($msg)     { Write-Host "    ข้าม $msg" -ForegroundColor DarkGray }
function Warn($msg)     { Write-Host "    !!   $msg" -ForegroundColor Yellow }
function Die($msg)      { Write-Host "`nล้มเหลว: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $root "meeting_ai\worker.py"))) {
    Die "ไม่พบ meeting_ai\worker.py — ต้องรันสคริปต์นี้จากในโฟลเดอร์โปรเจกต์ที่คัดลอกมา"
}

# ---------- 1. Python ----------
Step 1 "ตรวจ Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Die "ไม่พบ python — ติดตั้งจาก https://python.org (3.12 ขึ้นไป) แล้วติ๊ก Add to PATH" }
$pyver = (& python -c "import sys; print('%d.%d' % sys.version_info[:2])")
if ([version]$pyver -lt [version]"3.10") { Die "Python $pyver เก่าเกินไป ต้อง 3.10 ขึ้นไป" }
Ok "Python $pyver ($($py.Source))"

# ---------- 2. ffmpeg ----------
Step 2 "ตรวจ ffmpeg"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Ok "มีอยู่แล้ว"
} else {
    Warn "ไม่พบ ffmpeg — กำลังลงด้วย winget"
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Die "ไม่มี winget ด้วย — ติดตั้ง ffmpeg เองจาก https://ffmpeg.org แล้วใส่ใน PATH"
    }
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        Die "ลง ffmpeg แล้วแต่ยังเรียกไม่ได้ — ปิด PowerShell แล้วเปิดใหม่ จากนั้นรันสคริปต์นี้ซ้ำ"
    }
    Ok "ติดตั้ง ffmpeg แล้ว"
}

# ---------- 3. whisper.cpp ----------
Step 3 "ติดตั้ง whisper.cpp"
$binDir = Join-Path $root "bin\whisper"
$whisperExe = Join-Path $binDir "Release\whisper-cli.exe"

if (Test-Path $whisperExe) {
    Skip "มี whisper-cli อยู่แล้ว"
} else {
    $gpu = $null
    if (-not $Cpu) {
        try { $gpu = (& nvidia-smi --query-gpu=name --format=csv,noheader 2>$null | Select-Object -First 1) } catch {}
    }
    if ($gpu) {
        Write-Host "    พบ GPU: $gpu -> ใช้ build ที่เร่งด้วย CUDA (ดาวน์โหลด ~670MB)"
        $asset = "whisper-cublas-12.4.0-bin-x64.zip"
    } else {
        if ($Cpu) { Write-Host "    บังคับใช้ CPU build" } else { Write-Host "    ไม่พบ NVIDIA GPU -> ใช้ CPU build (~8MB, ช้ากว่าราว 5-10 เท่า)" }
        $asset = "whisper-bin-x64.zip"
    }
    $url = "https://github.com/ggml-org/whisper.cpp/releases/download/$WhisperVersion/$asset"
    $zip = Join-Path $env:TEMP $asset
    Write-Host "    ดาวน์โหลด $asset ..."
    curl.exe -L --fail --progress-bar -o $zip $url
    if ($LASTEXITCODE -ne 0) { Die "ดาวน์โหลด whisper.cpp ไม่สำเร็จ" }
    New-Item -ItemType Directory -Force $binDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $binDir -Force
    Remove-Item $zip -Force
    if (-not (Test-Path $whisperExe)) {
        # บาง build แตกไฟล์ลงรากเลย ไม่มีโฟลเดอร์ Release
        $found = Get-ChildItem $binDir -Recurse -Filter "whisper-cli.exe" | Select-Object -First 1
        if (-not $found) { Die "แตกไฟล์แล้วแต่ไม่เจอ whisper-cli.exe" }
        $whisperExe = $found.FullName
    }
    Ok "whisper-cli: $whisperExe"
}

# ---------- 4. โมเดลถอดเสียง ----------
Step 4 "ดาวน์โหลดโมเดลถอดเสียง"
$modelDir = Join-Path $root "models"
New-Item -ItemType Directory -Force $modelDir | Out-Null
$modelFile = Join-Path $modelDir "ggml-$WhisperModel.bin"
if (Test-Path $modelFile) {
    Skip "มีโมเดลอยู่แล้ว ($([math]::Round((Get-Item $modelFile).Length/1MB)) MB)"
} else {
    Write-Host "    ดาวน์โหลด ggml-$WhisperModel.bin (~550MB) ..."
    curl.exe -L --fail --progress-bar -o $modelFile `
        "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-$WhisperModel.bin"
    if ($LASTEXITCODE -ne 0) { Remove-Item $modelFile -ErrorAction SilentlyContinue; Die "ดาวน์โหลดโมเดลไม่สำเร็จ" }
    Ok "โมเดล: $([math]::Round((Get-Item $modelFile).Length/1MB)) MB"
}

# ---------- 5. แยกผู้พูด (ออปชัน) ----------
Step 5 "ติดตั้งตัวแยกผู้พูด"
if ($NoDiarize) {
    Skip "ข้ามตามที่สั่ง (-NoDiarize) — ยังถอดเสียงได้ แต่จะไม่บอกว่าใครพูด"
} else {
    python -m pip install --quiet --disable-pip-version-check sherpa-onnx
    if ($LASTEXITCODE -ne 0) { Warn "ลง sherpa-onnx ไม่สำเร็จ — ข้ามไป (ส่วนอื่นยังใช้ได้)" }

    $segDir = Join-Path $modelDir "sherpa-onnx-pyannote-segmentation-3-0"
    if (Test-Path (Join-Path $segDir "model.onnx")) {
        Skip "มีโมเดล segmentation แล้ว"
    } else {
        $tar = Join-Path $env:TEMP "seg.tar.bz2"
        curl.exe -L --fail --progress-bar -o $tar `
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
        if ($LASTEXITCODE -eq 0) { tar -xjf $tar -C $modelDir; Remove-Item $tar -Force; Ok "โมเดล segmentation" }
        else { Warn "ดาวน์โหลดโมเดล segmentation ไม่สำเร็จ" }
    }

    $embFile = Join-Path $modelDir "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
    if (Test-Path $embFile) {
        Skip "มีโมเดล embedding แล้ว"
    } else {
        curl.exe -L --fail --progress-bar -o $embFile `
            "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"
        if ($LASTEXITCODE -eq 0) { Ok "โมเดล embedding" }
        else { Remove-Item $embFile -ErrorAction SilentlyContinue; Warn "ดาวน์โหลดโมเดล embedding ไม่สำเร็จ" }
    }
}

# ---------- 6. .env ----------
Step 6 "เขียน .env"
$threads = [Math]::Max(4, [Environment]::ProcessorCount - 2)
$envPath = Join-Path $root ".env"
$rel = $whisperExe.Replace($root + "\", "").Replace("\", "/")
$envText = @"
# ---- worker ของ meeting_ai (สร้างโดย setup-worker.ps1) ----
# ต้องตรงกับ WORKER_TOKEN ที่ตั้งไว้บนเซิร์ฟเวอร์
WORKER_TOKEN=$WorkerToken

# ---- LLM สำหรับสรุป ----
LLM_BASE_URL=$LlmBaseUrl
LLM_API_KEY=$LlmApiKey
LLM_MODEL=$LlmModel

# ---- ถอดเสียง ----
WHISPER_BIN=$rel
WHISPER_MODEL=models/ggml-$WhisperModel.bin
WHISPER_LANG=$Lang
WHISPER_THREADS=$threads
FFMPEG_BIN=ffmpeg

# worker ไม่ต้องใช้ DATABASE_URL และไม่ต้องมีคีย์ R2
# ไฟล์เสียงมาเป็นลิงก์ที่เซ็นแล้วจากเซิร์ฟเวอร์
"@
if (Test-Path $envPath) {
    Copy-Item $envPath "$envPath.bak" -Force
    Warn "มี .env อยู่แล้ว — สำรองไว้ที่ .env.bak"
}
[System.IO.File]::WriteAllText($envPath, $envText, [System.Text.UTF8Encoding]::new($false))
Ok ".env เขียนแล้ว (threads=$threads)"

# ---------- 7. ตรวจ ----------
Step 7 "ตรวจความพร้อม"
$env:PYTHONPATH = $root
$env:PYTHONIOENCODING = "utf-8"
$check = @'
import os, shutil, sys
from pathlib import Path
from meeting_ai.config import ROOT, _load_dotenv, config
_load_dotenv(ROOT / ".env")
import importlib
importlib.reload(sys.modules["meeting_ai.config"])
from meeting_ai.config import config
from meeting_ai import diarize
rows = []
rows.append(("whisper-cli", Path(config.whisper_bin).exists() or shutil.which(config.whisper_bin) is not None))
rows.append(("โมเดลถอดเสียง", config.whisper_model_path().exists()))
rows.append(("ffmpeg", shutil.which(config.ffmpeg_bin) is not None))
rows.append(("WORKER_TOKEN", bool(config.worker_token)))
rows.append(("LLM_API_KEY", bool(config.llm_api_key)))
rows.append(("แยกผู้พูด (ออปชัน)", diarize.available()))
bad = 0
for name, ok in rows:
    print(("    OK   " if ok else "    ขาด ") + name)
    if not ok and name != "แยกผู้พูด (ออปชัน)":
        bad += 1
sys.exit(1 if bad else 0)
'@
$check | python -
if ($LASTEXITCODE -ne 0) { Die "ยังขาดของจำเป็น ดูรายการข้างบน" }

Write-Host "`nพร้อมใช้งานแล้ว" -ForegroundColor Green
Write-Host "  รัน worker:  .\mai.cmd worker --api $Api" -ForegroundColor Green

if ($Start) {
    Write-Host "`n==> เริ่ม worker (Ctrl+C เพื่อหยุด)`n" -ForegroundColor Cyan
    & (Join-Path $root "mai.cmd") worker --api $Api
}
