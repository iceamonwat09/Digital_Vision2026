#Requires -RunAsAdministrator
<#
.SYNOPSIS
    VisionIQ — IIS Deployment Script  (Login + Artwork on Windows Server)

.DESCRIPTION
    Script นี้ทำทุกขั้นตอนที่จำเป็นในการ deploy ให้อัตโนมัติ:
      1. ตรวจหา project root และ Python 3.9
      2. สร้าง .venv และติดตั้ง packages (requirements-server.txt)
      3. รับค่า config (SQL, JWT, N8N) จากผู้ใช้
      4. สร้าง web.config ใน project root
      5. สร้าง IIS Application Pool + Website
      6. ตั้ง NTFS permission ให้ AppPool identity
      7. รัน check_server.py ตรวจความพร้อม
      8. แสดง next steps

.EXAMPLE
    # เปิด PowerShell เป็น Administrator แล้วรัน:
    Set-ExecutionPolicy -Scope Process Bypass
    C:\VisionIQ\Digital_Vision2026\deploy\deploy_iis.ps1

.EXAMPLE
    # กรณี Python 3.9 อยู่ที่อื่น:
    .\deploy\deploy_iis.ps1 -PythonExe "D:\Python39\python.exe"
#>

[CmdletBinding()]
param(
    [string]$PythonExe = "C:\Python39\python.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── helpers ──────────────────────────────────────────────────────────────────
function Write-Banner([string]$msg) {
    Write-Host "`n$("=" * 68)" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "$("=" * 68)" -ForegroundColor Cyan
}
function Write-Step([string]$n, [string]$msg) {
    Write-Host "`n[$n] $msg" -ForegroundColor Cyan
}
function Write-OK([string]$msg)   { Write-Host "    OK   $msg" -ForegroundColor Green  }
function Write-Warn([string]$msg) { Write-Host "    !!   $msg" -ForegroundColor Yellow }
function Write-Fail([string]$msg) { Write-Host "   FAIL  $msg" -ForegroundColor Red    }

function Ask([string]$label, [string]$default = "") {
    $hint = if ($default) { " [$default]" } else { "" }
    $val  = Read-Host "       $label$hint"
    return if ([string]::IsNullOrWhiteSpace($val)) { $default } else { $val }
}

function AskSecret([string]$label, [string]$default = "") {
    $hint = if ($default) { " [กด Enter = สุ่มอัตโนมัติ]" } else { "" }
    $val  = Read-Host "       $label$hint"
    return if ([string]::IsNullOrWhiteSpace($val)) { $default } else { $val }
}

# ── locate paths ─────────────────────────────────────────────────────────────
$Deploy = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root   = Split-Path -Parent $Deploy
$VPy    = Join-Path $Root ".venv\Scripts\python.exe"

Write-Banner "VisionIQ — IIS Deployment Script"
Write-Host "  Script path  : $PSCommandPath"
Write-Host "  Project root : $Root"

# ── STEP 1: verify project files ─────────────────────────────────────────────
Write-Step "1/9" "ตรวจไฟล์โปรเจกต์"
if (-not (Test-Path (Join-Path $Root "app.py"))) {
    Write-Fail "ไม่พบ app.py ที่: $Root"
    Write-Host ""
    Write-Host "         ตรวจสอบว่าโฟลเดอร์ที่ถูกต้องคือโฟลเดอร์ที่มี app.py อยู่" -ForegroundColor Yellow
    Write-Host "         ควรมีโครงสร้างแบบนี้:" -ForegroundColor Yellow
    Write-Host "           $Root\app.py"
    Write-Host "           $Root\config.py"
    Write-Host "           $Root\deploy\deploy_iis.ps1  <- script นี้"
    Write-Host ""

    # ลองหา app.py ใน subfolders ช่วยชี้ทาง
    $found = Get-ChildItem -Path (Split-Path -Parent $Root) -Filter "app.py" -Recurse -Depth 3 -ErrorAction SilentlyContinue |
             Select-Object -First 5
    if ($found) {
        Write-Host "         พบ app.py ที่อื่น — ลองรัน script จากโฟลเดอร์นั้นแทน:" -ForegroundColor Yellow
        $found | ForEach-Object { Write-Host "           $($_.DirectoryName)" -ForegroundColor Yellow }
    }
    exit 1
}
Write-OK "app.py พบที่ $Root"

# ── STEP 2: Python 3.9 ───────────────────────────────────────────────────────
Write-Step "2/9" "ตรวจ Python 3.9"
if (-not (Test-Path $PythonExe)) {
    Write-Fail "Python ไม่พบที่: $PythonExe"
    Write-Host "         รัน script ใหม่ด้วย: .\deploy\deploy_iis.ps1 -PythonExe 'D:\Python39\python.exe'" -ForegroundColor Yellow
    Write-Host "         หรือหาว่า Python อยู่ที่ไหน: where python" -ForegroundColor Yellow
    exit 1
}
$pyVer = & $PythonExe --version 2>&1
Write-OK $pyVer

# ── STEP 3: venv ─────────────────────────────────────────────────────────────
Write-Step "3/9" "Virtual environment (.venv)"
if (Test-Path $VPy) {
    Write-OK ".venv มีอยู่แล้ว — ใช้ต่อ"
} else {
    Write-Host "       กำลังสร้าง .venv ..."
    & $PythonExe -m venv (Join-Path $Root ".venv")
    if (-not (Test-Path $VPy)) {
        Write-Fail "สร้าง .venv ไม่สำเร็จ — ตรวจสิทธิ์เขียนที่ $Root"
        exit 1
    }
    Write-OK "สร้าง .venv สำเร็จ"
}

# upgrade pip ก่อนลงของ
Write-Host "       อัปเกรด pip / setuptools / wheel ..."
& $VPy -m pip install --upgrade pip setuptools wheel --quiet

# ── STEP 4: install packages ──────────────────────────────────────────────────
Write-Step "4/9" "ติดตั้ง packages (อาจใช้เวลา 10-20 นาที — torch ~2 GB)"
$ReqFile = Join-Path $Deploy "requirements-server.txt"
if (-not (Test-Path $ReqFile)) {
    Write-Fail "ไม่พบ requirements-server.txt ที่: $ReqFile"
    exit 1
}
Write-Host "       กำลังติดตั้ง — อย่าปิดหน้าต่างนี้ ..."
& $VPy -m pip install -r $ReqFile
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install ล้มเหลว — ตรวจ network / proxy แล้วรัน script ใหม่ (pip จะข้าม package ที่ลงแล้ว)"
    exit 1
}
Write-OK "ติดตั้ง packages สำเร็จ"

# ── STEP 5: collect config ────────────────────────────────────────────────────
Write-Step "5/9" "ตั้งค่า (กด Enter เพื่อใช้ค่า default)"
Write-Host ""
Write-Host "       --- SQL Server ---" -ForegroundColor White

$SqlServer = Ask "SQL Server IP หรือ hostname" "172.32.0.50"
$SqlDb     = Ask "ชื่อ Database"              "VisionIQ"
$SqlUser   = Ask "SQL login user"             "sa"
$SqlPass   = Read-Host "       SQL password"

Write-Host ""
Write-Host "       --- JWT Secret ---" -ForegroundColor White
# สร้าง default token สุ่ม
$autoJwt   = & $VPy -c "import secrets; print(secrets.token_urlsafe(48))"
$JwtSecret = AskSecret "JWT Secret (Enter = สุ่มให้อัตโนมัติ)" $autoJwt
Write-Host "       JWT Secret: $($JwtSecret.Substring(0,8))..." -ForegroundColor DarkGray

Write-Host ""
Write-Host "       --- N8N Webhooks (กด Enter เพื่อข้าม) ---" -ForegroundColor White
$N8nOcr   = Ask "OCR webhook URL"       "http://172.32.201.106:5678/webhook/artwork-ocr"
$N8nTrans = Ask "Translate webhook URL" "http://172.32.201.106:5678/webhook/artwork-translate"

Write-Host ""
Write-Host "       --- HTTPS / Cookie ---" -ForegroundColor White
Write-Host "       ⚠️  ถ้ายังไม่ได้ผูก HTTPS บน IIS ให้ตอบ 0 (http)" -ForegroundColor Yellow
Write-Host "          ตั้ง 1 ทั้งที่ยังเป็น http:// = ล็อกอินไม่ติด" -ForegroundColor Yellow
$CookieSecure = Ask "AUTH_COOKIE_SECURE (0=http ตอนนี้ | 1=https)" "0"

Write-Host ""
Write-Host "       --- IIS Site ---" -ForegroundColor White
$AppPool  = Ask "ชื่อ Application Pool"  "VisionIQPool"
$SiteName = Ask "ชื่อ IIS Website"       "VisionIQ"
$HttpPort = Ask "HTTP port"              "80"

# ── STEP 6: web.config ───────────────────────────────────────────────────────
Write-Step "6/9" "สร้าง web.config"

# สร้างโฟลเดอร์ logs ถ้ายังไม่มี
New-Item -ItemType Directory -Force (Join-Path $Root "logs") | Out-Null

$VPyEsc   = $VPy.Replace("&","&amp;")
$RootEsc  = $Root.Replace("&","&amp;")
$DeployEsc = $Deploy.Replace("&","&amp;")

$webconfig = @"
<?xml version="1.0" encoding="utf-8"?>
<!--
  web.config — สร้างโดย deploy_iis.ps1  $(Get-Date -Format "yyyy-MM-dd HH:mm")
  ห้ามเช็คไฟล์นี้เข้า git (.gitignore กันแล้ว) เพราะมี JWT secret และ SQL password
-->
<configuration>
  <system.webServer>

    <handlers>
      <remove name="httpplatformhandler" />
      <add name="httpplatformhandler"
           path="*" verb="*"
           modules="httpPlatformHandler"
           resourceType="Unspecified"
           requireAccess="Script" />
    </handlers>

    <httpPlatform
        processPath="$VPyEsc"
        arguments="$DeployEsc\wsgi_iis.py"
        workingDirectory="$RootEsc"
        stdoutLogEnabled="true"
        stdoutLogFile="$RootEsc\logs\iis-stdout"
        startupTimeLimit="180"
        startupRetryCount="3"
        requestTimeout="00:20:00">

      <environmentVariables>
        <environmentVariable name="PYTHONUNBUFFERED"  value="1" />
        <environmentVariable name="PYTHONIOENCODING"  value="utf-8" />
        <environmentVariable name="VISIONIQ_IIS_INIT" value="db" />

        <environmentVariable name="SQL_SERVER"   value="$SqlServer" />
        <environmentVariable name="SQL_DATABASE" value="$SqlDb" />
        <environmentVariable name="SQL_USER"     value="$SqlUser" />
        <environmentVariable name="SQL_PASSWORD" value="$SqlPass" />

        <environmentVariable name="AUTH_ENABLED"           value="1" />
        <environmentVariable name="AUTH_JWT_SECRET"        value="$JwtSecret" />
        <environmentVariable name="AUTH_COOKIE_SECURE"     value="$CookieSecure" />
        <environmentVariable name="AUTH_ACCESS_TTL_MIN"    value="60" />
        <environmentVariable name="AUTH_REFRESH_TTL_DAYS"  value="7" />
        <environmentVariable name="AUTH_MAX_FAILED"        value="5" />
        <environmentVariable name="AUTH_LOCK_MINUTES"      value="15" />

        <environmentVariable name="N8N_OCR_WEBHOOK_URL"       value="$N8nOcr" />
        <environmentVariable name="N8N_OCR_TIMEOUT_S"         value="60" />
        <environmentVariable name="N8N_TRANSLATE_WEBHOOK_URL" value="$N8nTrans" />

        <environmentVariable name="ARTWORK_HIGHLIGHT_TESS_LANG" value="eng" />
      </environmentVariables>
    </httpPlatform>

    <security>
      <requestFiltering>
        <requestLimits maxAllowedContentLength="83886080" />
      </requestFiltering>
    </security>

    <directoryBrowse enabled="false" />

    <httpProtocol>
      <customHeaders>
        <remove name="X-Powered-By" />
        <add name="X-Content-Type-Options" value="nosniff" />
        <add name="X-Frame-Options"        value="SAMEORIGIN" />
      </customHeaders>
    </httpProtocol>

  </system.webServer>
</configuration>
"@

$webconfigPath = Join-Path $Root "web.config"
$webconfig | Set-Content $webconfigPath -Encoding UTF8
Write-OK "web.config เขียนที่: $webconfigPath"

# ── STEP 7: IIS — App Pool + Website ─────────────────────────────────────────
Write-Step "7/9" "ตั้งค่า IIS (Application Pool + Website)"
try {
    Import-Module WebAdministration -ErrorAction Stop
} catch {
    Write-Fail "ไม่พบ module WebAdministration — ตรวจว่า IIS ถูกติดตั้งแล้ว"
    Write-Warn "ข้ามขั้นนี้ — ต้องสร้าง App Pool และ Website ใน IIS Manager เอง"
    goto SkipIIS
}

# Application Pool
if (Test-Path "IIS:\AppPools\$AppPool") {
    Write-Warn "App Pool '$AppPool' มีอยู่แล้ว — ข้ามการสร้าง"
} else {
    New-WebAppPool -Name $AppPool | Out-Null
    Set-ItemProperty "IIS:\AppPools\$AppPool" managedRuntimeVersion    ""
    Set-ItemProperty "IIS:\AppPools\$AppPool" enable32BitAppOnWin64    $false
    Set-ItemProperty "IIS:\AppPools\$AppPool" processModel.identityType "ApplicationPoolIdentity"
    Write-OK "สร้าง App Pool '$AppPool' (No Managed Code, ApplicationPoolIdentity)"
}

# Website
if (Test-Path "IIS:\Sites\$SiteName") {
    Write-Warn "Website '$SiteName' มีอยู่แล้ว — ข้ามการสร้าง"
    Write-Warn "ตรวจ IIS Manager: Physical Path = $Root และ App Pool = $AppPool"
} else {
    New-Website -Name $SiteName -Port ([int]$HttpPort) -PhysicalPath $Root `
                -ApplicationPool $AppPool | Out-Null
    Write-OK "สร้าง Website '$SiteName' พอร์ต $HttpPort → $Root"
}

:SkipIIS

# ── STEP 8: NTFS permissions ──────────────────────────────────────────────────
Write-Step "8/9" "ตั้ง NTFS permission"

function Grant-FolderAccess([string]$path, [string]$identity, [string]$rights) {
    try {
        $acl  = Get-Acl $path
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $identity, $rights,
            [System.Security.AccessControl.InheritanceFlags]"ContainerInherit,ObjectInherit",
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
        Set-Acl $path $acl
        Write-OK "Granted $rights → '$identity'"
    } catch {
        Write-Warn "ตั้ง ACL ไม่สำเร็จสำหรับ '$identity': $_"
        Write-Warn "ตั้งเองด้วย: icacls `"$path`" /grant `"${identity}:(OI)(CI)M`""
    }
}

Grant-FolderAccess $Root "IIS AppPool\$AppPool" "Modify"
Grant-FolderAccess $Root "IIS_IUSRS"            "ReadAndExecute"

# ── STEP 9: readiness check ───────────────────────────────────────────────────
Write-Step "9/9" "ตรวจความพร้อม (check_server.py)"
$CheckScript = Join-Path $Deploy "check_server.py"
if (Test-Path $CheckScript) {
    Write-Host ""
    & $VPy $CheckScript
    $rc = $LASTEXITCODE
    Write-Host ""
    if ($rc -eq 0) {
        Write-OK "ผ่านทุก check — พร้อม deploy"
    } else {
        Write-Warn "มีบาง check ที่ยังไม่ผ่าน — ดูผลด้านบนและแก้ไขก่อนเปิดใช้"
    }
} else {
    Write-Warn "ไม่พบ check_server.py — ข้ามการตรวจ"
}

# ── Summary ───────────────────────────────────────────────────────────────────
Write-Banner "Deploy เสร็จแล้ว!"
Write-Host ""
Write-Host "  ไฟล์ที่สร้าง:" -ForegroundColor White
Write-Host "    $webconfigPath" -ForegroundColor Gray
Write-Host "    $Root\logs\  (IIS stdout log)" -ForegroundColor Gray
Write-Host ""
Write-Host "  ขั้นตอนต่อไป:" -ForegroundColor White
Write-Host ""
Write-Host "  A) ตั้งฐานข้อมูล (ถ้ายังไม่ได้ทำ):" -ForegroundColor Yellow
Write-Host "     เปิด SQL Server Management Studio → ต่อ $SqlServer\$SqlDb"
Write-Host "     รัน: auth\auth_schema.sql"
Write-Host "     สร้าง admin: $VPy auth\seed_admin.py"
Write-Host ""
Write-Host "  B) ทดสอบแอปก่อน IIS:" -ForegroundColor Yellow
Write-Host "     $VPy deploy\wsgi_iis.py"
Write-Host "     เปิดเบราว์เซอร์: http://localhost:8000"
Write-Host ""
Write-Host "  C) เริ่มต้น IIS site:" -ForegroundColor Yellow
Write-Host "     Start-Website -Name '$SiteName'"
Write-Host "     หรือกด Start ใน IIS Manager"
Write-Host "     เปิดเบราว์เซอร์: http://localhost:$HttpPort"
Write-Host ""
if ($CookieSecure -eq "0") {
    Write-Host "  D) เมื่อพร้อมเพิ่ม HTTPS:" -ForegroundColor Yellow
    Write-Host "     ผูก HTTPS binding ใน IIS → แก้ web.config:"
    Write-Host "       AUTH_COOKIE_SECURE = 1"
    Write-Host "     แล้ว Recycle App Pool"
    Write-Host ""
}
Write-Host "  log ของ Python: $Root\logs\iis-stdout*.log" -ForegroundColor Gray
Write-Host "  ถ้าเว็บไม่ขึ้น: ดู log + รัน check อีกรอบ:" -ForegroundColor Gray
Write-Host "    $VPy deploy\check_server.py" -ForegroundColor Gray
Write-Host ""
Write-Host "$("=" * 68)" -ForegroundColor Cyan
