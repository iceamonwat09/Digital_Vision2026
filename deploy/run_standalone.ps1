<#
.SYNOPSIS
    แผน B — รัน VisionIQ เป็นบริการถาวรด้วย waitress โดยไม่ผ่าน HttpPlatformHandler

.DESCRIPTION
    ใช้เมื่อ IIS + HttpPlatformHandler เปิดโปรเซสลูกไม่ได้ (HTTP 502.3 แบบไม่มี
    โปรเซสและไม่มี log) ซึ่งเป็นปัญหาที่อยู่นอกเหนือการควบคุมของโค้ดแอปเอง

    สิ่งที่สคริปต์นี้ทำ
      1) อ่านค่าตั้ง (SQL / JWT / N8N / ฯลฯ) จาก web.config ที่ตั้งไว้แล้ว
         → ไม่ต้องพิมพ์ค่าซ้ำ และไม่มีโอกาสพิมพ์ผิด
      2) สร้างไฟล์ start_visioniq.cmd ที่ตั้ง environment variable แล้วเรียก waitress
      3) จำกัดสิทธิ์ไฟล์นั้นให้เหลือเฉพาะ Administrators + SYSTEM + บัญชีที่รัน
         (เพราะไฟล์มีรหัสผ่าน SQL และ JWT secret อยู่ข้างใน)
      4) ลงทะเบียน Scheduled Task ให้รันอัตโนมัติตั้งแต่บูตเครื่อง และรีสตาร์ตเองถ้าล่ม
      5) เปิด firewall ให้พอร์ตที่ใช้
      6) ทดสอบว่าเว็บตอบกลับจริง

    ข้อดีเทียบกับ HttpPlatformHandler
      • ชิ้นส่วนน้อยกว่า — ถ้าพังจะเห็น error ตรง ๆ ในไฟล์ log ทันที
      • log ของโปรเซสอยู่ที่เดียว ไม่ต้องพึ่งกลไก stdout redirect ของ IIS
      • ทดสอบซ้ำได้ด้วยการรันไฟล์ .cmd ด้วยมือ (เห็นทุกอย่างที่เกิดขึ้น)

    ข้อแลก
      • ไม่ได้ TLS/พอร์ต 80 จาก IIS ให้ฟรี ๆ — เลือกได้ 2 ทาง
          (ก) ให้ waitress ฟังพอร์ต 80 ตรง ๆ (ง่ายสุด ใช้ -Port 80)
          (ข) ให้ waitress ฟัง 8000 แล้วให้ IIS ทำ reverse proxy ด้วย ARR
              (ได้ TLS + log ของ IIS ตามเดิม — ดู -PrintArrGuide)

.PARAMETER Root
    โฟลเดอร์รากของโปรเจกต์

.PARAMETER Port
    พอร์ตที่ waitress จะเปิดรับ (80 = เข้าเว็บได้ตรงโดยไม่ต้องระบุพอร์ต)

.PARAMETER BindHost
    ที่อยู่ที่เปิดรับ — 0.0.0.0 = ทุกการ์ดเครือข่าย, 127.0.0.1 = เฉพาะในเครื่อง
    (ใช้ 127.0.0.1 เมื่อจะให้ IIS ทำ reverse proxy อยู่ข้างหน้า)

.PARAMETER RunAs
    บัญชีที่ใช้รัน — ค่าเริ่มต้น NETWORK SERVICE (สิทธิ์ต่ำพอและใช้งานได้จริง)

.PARAMETER TaskName
    ชื่อ Scheduled Task

.PARAMETER Uninstall
    ถอน Scheduled Task และ firewall rule ออก

.PARAMETER PrintArrGuide
    พิมพ์ขั้นตอนตั้ง IIS เป็น reverse proxy (ARR) แล้วจบ ไม่แก้อะไร

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\run_standalone.ps1 -Port 80

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\run_standalone.ps1 -Port 8000 -BindHost 127.0.0.1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\run_standalone.ps1 -Uninstall

.NOTES
    ต้องรันด้วยสิทธิ์ Administrator
#>

[CmdletBinding()]
param(
    [string] $Root     = "C:\VisionIQ\Digital_Vision2026",
    [int]    $Port     = 80,
    [string] $BindHost = "0.0.0.0",
    [string] $RunAs    = "NT AUTHORITY\NETWORK SERVICE",
    [string] $TaskName = "VisionIQ Web",
    [switch] $Uninstall,
    [switch] $PrintArrGuide
)

$ErrorActionPreference = "Stop"

function Say {
    param([string] $m, [string] $c = "Gray")
    Write-Host $m -ForegroundColor $c
}
function Ok   { param([string] $m); Say "  [OK]   $m" Green }
function Warn { param([string] $m); Say "  [WARN] $m" Yellow }
function Die  { param([string] $m); Say "  [FAIL] $m" Red; exit 1 }

if (-not (New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Die "ต้องรันด้วยสิทธิ์ Administrator"
}

# ────────────────────────────────────────────────────────── คู่มือ ARR อย่างเดียว
if ($PrintArrGuide) {
    Say ""
    Say "ตั้ง IIS ให้เป็น reverse proxy หน้า waitress (ทางเลือก ข)" Cyan
    Say "─────────────────────────────────────────────────────────" DarkCyan
    Say @"
  1) ติดตั้ง 2 ตัวนี้จาก Microsoft (ติดตั้งครั้งเดียว)
       • URL Rewrite 2.1
       • Application Request Routing 3.0
  2) เปิดใช้ proxy ที่ระดับเครื่อง
       & "`$env:windir\system32\inetsrv\appcmd.exe" set config -section:system.webServer/proxy /enabled:"True" /commit:apphost
  3) รันสคริปต์นี้ให้ waitress ฟังเฉพาะในเครื่อง
       run_standalone.ps1 -Port 8000 -BindHost 127.0.0.1
  4) วาง web.config นี้ที่รากเว็บไซต์ (แทนตัวที่ใช้ httpPlatform)

  <?xml version="1.0" encoding="utf-8"?>
  <configuration>
    <system.webServer>
      <rewrite>
        <rules>
          <rule name="VisionIQ proxy" stopProcessing="true">
            <match url="(.*)" />
            <action type="Rewrite" url="http://127.0.0.1:8000/{R:1}" />
            <serverVariables>
              <set name="HTTP_X_FORWARDED_PROTO" value="{CACHE_URL_SCHEME}" />
            </serverVariables>
          </rule>
        </rules>
      </rewrite>
      <security>
        <requestFiltering>
          <requestLimits maxAllowedContentLength="83886080" />
        </requestFiltering>
      </security>
    </system.webServer>
  </configuration>

  5) ขยาย timeout ของ ARR ให้ยาวกว่าเวลาตรวจ Artwork ที่นานที่สุด
       & "`$env:windir\system32\inetsrv\appcmd.exe" set config -section:system.webServer/proxy /timeout:"00:20:00" /commit:apphost

  หมายเหตุ: ต้องเพิ่ม HTTP_X_FORWARDED_PROTO ในรายการ Allowed Server Variables
            ของ URL Rewrite ก่อน ไม่งั้นกฎจะ error 500.50
"@ Gray
    Say ""
    exit 0
}

$cmdFile  = Join-Path $Root "deploy\start_visioniq.cmd"
$logFile  = Join-Path $Root "logs\standalone.log"
$fwRule   = "VisionIQ Web ($Port)"

# ────────────────────────────────────────────────────────────────────── ถอนออก
if ($Uninstall) {
    Say ""
    Say "ถอน VisionIQ standalone ออก" Cyan
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask   -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Ok "ลบ Scheduled Task '$TaskName' แล้ว"
    } else { Warn "ไม่พบ Scheduled Task '$TaskName'" }

    Get-NetFirewallRule -DisplayName "VisionIQ Web*" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-NetFirewallRule -Name $_.Name; Ok "ลบ firewall rule '$($_.DisplayName)'" }

    Get-Process python -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$Root*" } |
        ForEach-Object { Stop-Process -Id $_.Id -Force; Ok "หยุดโปรเซส PID $($_.Id)" }

    Say ""
    Say "เสร็จสิ้น (ไฟล์ $cmdFile ยังอยู่ ลบเองได้ถ้าไม่ใช้แล้ว)" Green
    exit 0
}

# ───────────────────────────────────────────────────────────── ตรวจของที่ต้องมี
Say ""
Say "═══ VisionIQ — ติดตั้งโหมด standalone (waitress + Scheduled Task) ═══" Cyan
Say ""

if (-not (Test-Path $Root)) { Die "ไม่พบโฟลเดอร์โปรเจกต์: $Root" }

$python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Die "ไม่พบ $python — สร้าง venv ก่อน (ดู deploy\setup_venv.bat)" }
Ok "พบ python ของ venv"

$entry = Join-Path $Root "deploy\wsgi_iis.py"
if (-not (Test-Path $entry)) { Die "ไม่พบ $entry" }
Ok "พบ entry point"

& $python -c "import waitress" 2>$null
if ($LASTEXITCODE -ne 0) { Die "venv ยังไม่มี waitress — รัน: `"$python`" -m pip install waitress" }
Ok "waitress พร้อมใช้งาน"

$logDir = Join-Path $Root "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
Ok "โฟลเดอร์ log พร้อม"

# ───────────────────────────────── อ่านค่าตั้งจาก web.config ที่ตั้งไว้แล้ว
$envVars = [ordered]@{}
$webConfig = Join-Path $Root "web.config"
if (Test-Path $webConfig) {
    try {
        [xml]$xml = Get-Content $webConfig -Raw
        $nodes = $xml.configuration.'system.webServer'.httpPlatform.environmentVariables.environmentVariable
        foreach ($n in @($nodes)) {
            if ($n -and $n.name) { $envVars[$n.name] = [string]$n.value }
        }
        Ok "อ่านค่าตั้ง $($envVars.Count) รายการจาก web.config"
    } catch {
        Warn "อ่าน web.config ไม่สำเร็จ ($($_.Exception.Message)) — จะใช้ค่าเริ่มต้นในโค้ดแทน"
    }
} else {
    Warn "ไม่พบ web.config — จะใช้ค่าเริ่มต้นใน config.py แทน"
}

# ค่าที่โหมด standalone ต้องกำหนดเอง (ทับค่าที่อ่านมาจาก web.config)
$envVars["PYTHONUNBUFFERED"]   = "1"
$envVars["PYTHONIOENCODING"]   = "utf-8"
$envVars["VISIONIQ_WSGI_PORT"] = "$Port"
$envVars["VISIONIQ_WSGI_HOST"] = $BindHost
if (-not $envVars.Contains("VISIONIQ_IIS_INIT")) { $envVars["VISIONIQ_IIS_INIT"] = "db" }

# HTTP_PLATFORM_PORT ต้องไม่มี ไม่งั้นจะไปทับพอร์ตที่เราตั้งเอง
if ($envVars.Contains("HTTP_PLATFORM_PORT")) { $envVars.Remove("HTTP_PLATFORM_PORT") }

if ($envVars["AUTH_COOKIE_SECURE"] -eq "1") {
    Warn "AUTH_COOKIE_SECURE=1 — โหมดนี้ยังเป็น http:// ล้วน ล็อกอินจะวนกลับหน้า login"
    Warn "  ตั้งเป็น 0 ใน web.config ก่อน แล้วรันสคริปต์นี้ใหม่ (หรือแก้ในไฟล์ .cmd ที่สร้างขึ้น)"
}

# ────────────────────────────────────────────────────── สร้างไฟล์ .cmd
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("@echo off")
[void]$sb.AppendLine("rem ============================================================")
[void]$sb.AppendLine("rem  สร้างโดย deploy\run_standalone.ps1 — แก้ด้วยมือได้")
[void]$sb.AppendLine("rem  ⚠️ ไฟล์นี้มีรหัสผ่าน SQL และ JWT secret — อย่าคัดลอกออกนอกเครื่อง")
[void]$sb.AppendLine("rem ============================================================")
[void]$sb.AppendLine("cd /d `"$Root`"")
foreach ($k in $envVars.Keys) {
    # ใน .cmd เครื่องหมาย % ต้องเขียนเป็น %% ไม่งั้นจะถูกตีความเป็นชื่อตัวแปร
    $v = ([string]$envVars[$k]).Replace("%", "%%")
    [void]$sb.AppendLine("set `"$k=$v`"")
}
[void]$sb.AppendLine("")
[void]$sb.AppendLine("`"$python`" `"$entry`" >> `"$logFile`" 2>&1")
[IO.File]::WriteAllText($cmdFile, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
Ok "สร้าง $cmdFile"

# จำกัดสิทธิ์ไฟล์ที่มีความลับอยู่ข้างใน
& icacls.exe $cmdFile /inheritance:r /grant "*S-1-5-32-544:(F)" /grant "*S-1-5-18:(F)" /grant "${RunAs}:(RX)" | Out-Null
if ($LASTEXITCODE -eq 0) { Ok "จำกัดสิทธิ์ไฟล์ .cmd เรียบร้อย (Administrators + SYSTEM + $RunAs)" }
else { Warn "ตั้งสิทธิ์ไฟล์ .cmd ไม่สำเร็จ — ตรวจเองด้วย icacls" }

# ────────────────────────────────────────────────── สิทธิ์บนโฟลเดอร์โปรเจกต์
& icacls.exe $Root /grant "${RunAs}:(OI)(CI)M" /T /C | Out-Null
Ok "ให้สิทธิ์ $RunAs เขียนไฟล์ในโปรเจกต์แล้ว"

# ─────────────────────────────────────────── ตรวจว่าพอร์ตว่างจริง
$busy = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
if ($busy.Count -gt 0) {
    $owners = ($busy | ForEach-Object {
        $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
        if ($p) { "$($p.ProcessName) (PID $($p.Id))" } else { "PID $($_.OwningProcess)" }
    }) -join ", "
    Warn "พอร์ต $Port ถูกใช้อยู่แล้วโดย: $owners"
    if ($Port -eq 80) {
        Warn "  ถ้าเป็น IIS ให้หยุด website ที่จับพอร์ต 80 ก่อน เช่น"
        Warn "    Stop-Website -Name 'VisionIQ' ; Stop-Website -Name 'Default Web Site'"
        Warn "  หรือหยุดบริการ W3SVC ทั้งหมด: Stop-Service W3SVC -Force"
    }
}

# ───────────────────────────────────────────────── ลงทะเบียน Scheduled Task
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Ok "ลบ Scheduled Task เดิมออกก่อน"
}

$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$cmdFile`"" -WorkingDirectory $Root
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId $RunAs -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "VisionIQ (Login + Artwork) — waitress standalone" | Out-Null
Ok "ลงทะเบียน Scheduled Task '$TaskName' (เริ่มอัตโนมัติตอนบูต + รีสตาร์ตเองถ้าล่ม)"

# ───────────────────────────────────────────────────────────── firewall
if (-not (Get-NetFirewallRule -DisplayName $fwRule -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $fwRule -Direction Inbound -Protocol TCP `
        -LocalPort $Port -Action Allow -Profile Any | Out-Null
    Ok "เปิด firewall ขาเข้า TCP $Port"
} else { Ok "firewall rule มีอยู่แล้ว" }

# ───────────────────────────────────────────────────────────── เริ่มและทดสอบ
Say ""
Say "เริ่มบริการและทดสอบ..." Cyan
Start-ScheduledTask -TaskName $TaskName

$deadline = (Get-Date).AddSeconds(90)
$ok = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 10 `
                               -MaximumRedirection 0 -ErrorAction Stop
        $ok = $true; $code = $r.StatusCode; break
    } catch {
        # 302 ไปหน้า login = ทำงานถูกต้องแล้ว (Invoke-WebRequest ถือว่าเป็น error เมื่อห้าม redirect)
        $resp = $_.Exception.Response
        if ($resp -and [int]$resp.StatusCode -in @(200,301,302,303,307,308,401,403)) {
            $ok = $true; $code = [int]$resp.StatusCode; break
        }
    }
}

Say ""
if ($ok) {
    Ok "เว็บตอบกลับแล้ว (HTTP $code)"
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 |
             Where-Object { $_.IPAddress -notlike "127.*" } | Select-Object -ExpandProperty IPAddress)
    $suffix = if ($Port -eq 80) { "" } else { ":$Port" }
    foreach ($ip in $ips) { Say "         เข้าใช้งานที่  http://$ip$suffix" White }
} else {
    Warn "ยังไม่ตอบกลับภายใน 90 วินาที — ดูสาเหตุได้ที่:"
    Warn "    $logFile"
    Say ""
    if (Test-Path $logFile) {
        Say "ท้ายไฟล์ log:" Yellow
        Get-Content $logFile -Tail 30 | ForEach-Object { Say "    $_" DarkGray }
    }
    Say ""
    Say "ทดสอบด้วยมือเพื่อดู error สด ๆ ได้ที่: $cmdFile" Yellow
}

Say ""
Say "คำสั่งที่ใช้บ่อยต่อจากนี้" Cyan
Say "  ดูสถานะ  : Get-ScheduledTask -TaskName '$TaskName'"
Say "  หยุด     : Stop-ScheduledTask -TaskName '$TaskName'"
Say "  เริ่มใหม่ : Start-ScheduledTask -TaskName '$TaskName'"
Say "  ดู log   : Get-Content '$logFile' -Tail 50 -Wait"
Say "  ถอนออก   : run_standalone.ps1 -Uninstall"
Say ""
