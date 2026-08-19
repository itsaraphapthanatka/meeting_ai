<#
.SYNOPSIS
  รัน worker ของ meeting_ai เบื้องหลัง (ไม่มีหน้าต่างค้าง) ผ่าน Windows Task Scheduler

.DESCRIPTION
  ติดตั้งเป็น scheduled task ของผู้ใช้ปัจจุบัน — ไม่ต้องสิทธิ์ admin
    - เริ่มเองตอนล็อกอิน
    - พังแล้วรีสตาร์ตเองทุก 1 นาที (ไม่จำกัดครั้ง)
    - เขียน log ไว้ที่ logs\worker.log
    - ไม่มีหน้าต่างโผล่

.EXAMPLE
  .\worker-service.ps1 install -Api https://meeting-ai-swart.vercel.app -Name "เครื่องหลัก"
  .\worker-service.ps1 status
  .\worker-service.ps1 log
  .\worker-service.ps1 stop
  .\worker-service.ps1 uninstall

.NOTES
  ถ้ารันไม่ได้เพราะ execution policy:
    powershell -ExecutionPolicy Bypass -File .\worker-service.ps1 install
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "uninstall", "start", "stop", "restart", "status", "log")]
    [string]$Action = "status",

    [string]$Api = "https://meeting-ai-swart.vercel.app",
    [string]$Name,
    [string]$TaskName = "meeting_ai worker",
    [int]$LogTailLines = 40,
    [int]$MaxLogMB = 20,

    # รันแม้ยังไม่ได้ล็อกอิน (เริ่มตอนบูตเครื่องเลย)
    # ลอง S4U ก่อน — ไม่ต้องเก็บรหัส Windows ไว้ที่ไหน ถ้าไม่ได้จะถามรหัสให้ใส่เอง
    [switch]$RunAlways
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
$logDir = Join-Path $root "logs"
$logFile = Join-Path $logDir "worker.log"

function Info($m) { Write-Host $m }
function Ok($m)   { Write-Host "  OK   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !!   $m" -ForegroundColor Yellow }
function Die($m)  { Write-Host "ล้มเหลว: $m" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $root "meeting_ai\worker.py"))) {
    Die "ไม่พบ meeting_ai\worker.py — ต้องรันสคริปต์นี้จากในโฟลเดอร์โปรเจกต์"
}

function Get-Task { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }

function Install-WorkerTask {
    $py = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $py) { Die "ไม่พบ python ใน PATH" }
    if (-not $Name) { $Name = $env:COMPUTERNAME }

    New-Item -ItemType Directory -Force $logDir | Out-Null

    # ตัวห่อ: ตั้ง encoding ให้ไทยไม่เพี้ยนใน log, ตัด log ที่ใหญ่เกิน, แล้วรัน worker
    $runner = Join-Path $root "run-worker-hidden.ps1"
    @"
# สร้างโดย worker-service.ps1 — ตัวห่อสำหรับรันเบื้องหลัง (แก้ไฟล์นี้เองไม่จำเป็น)
`$ErrorActionPreference = 'Continue'
Set-Location '$root'
`$env:PYTHONPATH = '$root'
`$env:PYTHONIOENCODING = 'utf-8'
# PowerShell อ่าน stdout ของโปรแกรมภายนอกด้วย codepage ของ console (cp874 บนเครื่องไทย)
# ทำให้ข้อความ UTF-8 จาก python เพี้ยนทั้งหมด ต้องบอกให้อ่านเป็น UTF-8 ก่อน
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
`$OutputEncoding = [System.Text.Encoding]::UTF8
`$log = '$logFile'
# ตัด log ถ้าใหญ่เกิน ${MaxLogMB}MB (เก็บก้อนเก่าไว้หนึ่งรุ่น)
if ((Test-Path `$log) -and ((Get-Item `$log).Length -gt ${MaxLogMB}MB)) {
    Move-Item `$log "`$log.old" -Force
}
"[`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] เริ่ม worker" | Out-File `$log -Append -Encoding utf8
& '$py' -m meeting_ai worker --api '$Api' --name '$Name' 2>&1 |
    ForEach-Object { "[`$(Get-Date -Format 'HH:mm:ss')] `$_" } |
    Out-File `$log -Append -Encoding utf8
"[`$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] worker หยุด (exit `$LASTEXITCODE)" | Out-File `$log -Append -Encoding utf8
"@ | Set-Content $runner -Encoding UTF8

    $psExe = (Get-Command powershell).Source
    $action = New-ScheduledTaskAction -Execute $psExe `
        -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`"" `
        -WorkingDirectory $root
    $me = "$env:USERDOMAIN\$env:USERNAME"
    $triggers = @(New-ScheduledTaskTrigger -AtLogOn -User $me)
    if ($RunAlways) { $triggers += New-ScheduledTaskTrigger -AtStartup }

    # พังแล้วขึ้นใหม่เองเรื่อยๆ — worker เจอ token ผิดจะออกเลย จะได้เห็นใน log ว่าวนขึ้นใหม่
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -Hidden

    if (Get-Task) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Warn "ทับ task เดิม"
    }

    $registered = $false
    if ($RunAlways) {
        # S4U = รันในนามเราได้แม้ไม่ได้ล็อกอิน โดยไม่ต้องเก็บรหัสไว้ (ต้องมีสิทธิ์ batch logon)
        try {
            $p = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Limited
            Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
                -Settings $settings -Principal $p -Description "meeting_ai worker ($Name)" `
                -ErrorAction Stop | Out-Null
            $registered = $true
            Ok "ตั้งแบบ S4U — รันแม้ยังไม่ล็อกอิน ไม่ต้องเก็บรหัส Windows"
        } catch {
            Warn "S4U ไม่ผ่าน ($($_.Exception.Message.Split([char]10)[0]))"
            Info "  จะขอรหัส Windows ของคุณเพื่อให้ task รันตอนยังไม่ล็อกอินได้"
            Info "  (รหัสถูกเก็บไว้ใน Credential Manager ของ Windows ไม่ได้เก็บในไฟล์โปรเจกต์)"
            try {
                $cred = Get-Credential -UserName $me -Message "รหัส Windows สำหรับรัน meeting_ai worker"
                Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
                    -Settings $settings -User $cred.UserName `
                    -Password $cred.GetNetworkCredential().Password `
                    -Description "meeting_ai worker ($Name)" -ErrorAction Stop | Out-Null
                $registered = $true
                Ok "ตั้งด้วยรหัส Windows — รันแม้ยังไม่ล็อกอิน"
            } catch {
                Warn "ใส่รหัสไม่สำเร็จ — จะถอยไปใช้แบบเริ่มตอนล็อกอินแทน"
            }
        }
    }

    if (-not $registered) {
        $p = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive
        Register-ScheduledTask -TaskName $TaskName -Action $action `
            -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $me) `
            -Settings $settings -Principal $p -Description "meeting_ai worker ($Name)" | Out-Null
        Ok "ตั้งแบบเริ่มตอนล็อกอิน (ต้องล็อกอินก่อน worker จะเริ่ม)"
    }

    Ok "ติดตั้ง task `"$TaskName`" แล้ว  (เครื่อง: $Name)"
    Ok "log: $logFile"

    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 4
    Show-Status
    Info ""
    Info "ดู log สดๆ:  .\worker-service.ps1 log"
}

function Show-Status {
    $t = Get-Task
    if (-not $t) { Warn "ยังไม่ได้ติดตั้ง task (`"$TaskName`")"; return }
    $i = Get-ScheduledTaskInfo -TaskName $TaskName
    Info "task    : $TaskName"
    $lt = $t.Principal.LogonType
    $mode = switch ($lt) {
        "Interactive" { "เริ่มตอนล็อกอิน" }
        "S4U"         { "รันแม้ไม่ได้ล็อกอิน (S4U ไม่เก็บรหัส)" }
        "Password"    { "รันแม้ไม่ได้ล็อกอิน (ใช้รหัส Windows)" }
        default       { $lt }
    }
    Info "โหมด    : $mode"
    Info "สถานะ   : $($t.State)"
    # 267009 = 0x41301 = SCHED_S_TASK_RUNNING — ไม่ใช่ error แต่คนอ่านมักตกใจ
    $lastResult = switch ($i.LastTaskResult) {
        267009     { "0x41301 (กำลังรันอยู่)" }
        0          { "0 (จบปกติ)" }
        3221225786 { "0xC000013A (ถูกสั่งหยุด — ปกติถ้าเพิ่ง stop/restart)" }
        2          { "2 (token ไม่ถูกต้อง — worker ปิดตัวเอง ดู log)" }
        default    { $i.LastTaskResult }
    }
    Info "รันล่าสุด: $($i.LastRunTime)   ผลล่าสุด: $lastResult"
    Info "ครั้งถัดไป: $($i.NextRunTime)"

    $pythons = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'")
    $proc = @($pythons | Where-Object { $_.CommandLine -like "*meeting_ai worker*" })
    if ($proc.Count) {
        Info "โพรเซส  : กำลังรัน (PID $(($proc | ForEach-Object { $_.ProcessId }) -join ', '))"
    } elseif ($t.State -eq "Running") {
        # โหมด S4U รัน worker คนละ session — PowerShell ที่ไม่ได้เปิดแบบแอดมินอ่าน
        # CommandLine ข้าม session ไม่ได้ (ได้ $null) จึงแมตช์ไม่เจอทั้งที่โพรเซสมีจริง
        # อย่าเตือนว่าตาย เพราะ task บอกว่ากำลังรัน ให้บอกว่าตรวจลึกกว่านี้ไม่ได้
        $blind = @($pythons | Where-Object { -not $_.CommandLine }).Count
        if ($blind) {
            Info "โพรเซส  : task = Running (ยืนยัน PID ไม่ได้ ต้องเปิด PowerShell แบบแอดมิน)"
        } else {
            Warn "โพรเซส  : task = Running แต่ไม่เจอ python — น่าจะพังตอนเริ่ม ดู log ด้านล่าง"
        }
    } else {
        Warn "โพรเซส  : ไม่พบ python ที่รัน worker อยู่"
    }

    if (Test-Path $logFile) {
        $age = [int]((Get-Date) - (Get-Item $logFile).LastWriteTime).TotalMinutes
        Info "log     : $logFile ($([math]::Round((Get-Item $logFile).Length/1KB)) KB, เขียนล่าสุด $age นาทีที่แล้ว)"
    }
    Info "ยืนยันว่าเซิร์ฟเวอร์เห็นเครื่องนี้จริง: ดูแผง ""เครื่องประมวลผล"" ในหน้าเว็บ"
}

switch ($Action) {
    "install"   { Install-WorkerTask }
    "uninstall" {
        if (Get-Task) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Ok "ถอน task แล้ว"
        } else { Warn "ไม่มี task ให้ถอน" }
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like "*meeting_ai worker*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Ok "ปิดโพรเซส PID $($_.ProcessId)" }
    }
    "start"   { if (Get-Task) { Start-ScheduledTask -TaskName $TaskName; Ok "สั่งเริ่มแล้ว"; Start-Sleep 3; Show-Status } else { Die "ยังไม่ได้ติดตั้ง" } }
    "stop"    {
        if (Get-Task) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like "*meeting_ai worker*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Ok "ปิดโพรเซส PID $($_.ProcessId)" }
        Ok "หยุดแล้ว (task ยังอยู่ จะเริ่มใหม่ตอนล็อกอินครั้งหน้า)"
    }
    "restart" {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like "*meeting_ai worker*" } |
            ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Start-Sleep -Seconds 2
        Start-ScheduledTask -TaskName $TaskName
        Ok "รีสตาร์ตแล้ว"; Start-Sleep 3; Show-Status
    }
    "status"  { Show-Status }
    "log"     {
        if (-not (Test-Path $logFile)) { Die "ยังไม่มี log ที่ $logFile" }
        Info "ดู log สดๆ (Ctrl+C เพื่อออก) — $logFile`n"
        Get-Content $logFile -Tail $LogTailLines -Wait -Encoding UTF8
    }
}
