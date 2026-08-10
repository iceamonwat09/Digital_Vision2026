#Requires -RunAsAdministrator
<#
.SYNOPSIS
    VisionIQ — IIS Deployment Script  (Login + Artwork on Windows Server)

.DESCRIPTION
    Script นี้ทำทุกขั้นตอนที่จำเป็นในการ deploy ให้อัตโนมัติ:
       1. ตรวจหา project root และ Python 3.9
       2. สร้าง .venv และติดตั้ง packages (requirements-server.txt)
       3. รับค่า config (SQL, JWT, N8N) จากผู้ใช้
       4. สร้าง web.config (escape อักขระ XML ให้ + ตรวจว่าไฟล์ที่ได้เป็น XML ที่ถูกต้อง)
       5. ตรวจว่า HttpPlatformHandler ติดตั้งแล้ว
       6. ปลดล็อก config section ที่ IIS ล็อกไว้ (ไม่ทำ = HTTP 500.19)
       7. สร้าง/ปรับ IIS Application Pool + Website (จัดการพอร์ตที่ชนกันให้ด้วย)
       8. ตั้ง NTFS permission ให้ AppPool identity
       9. รัน check_server.py ตรวจความพร้อม
      10. เริ่ม Website แล้ว "ยิง request ทดสอบจริง" จนกว่าจะตอบกลับ
          — ถ้ายังไม่ได้ จะบอกคำสั่งวินิจฉัยและแผนสำรองให้

    ถ้าเว็บยังไม่ขึ้นหลังรันสคริปต์นี้ ให้ไล่ตามลำดับ:
      deploy\check_server.py      (ฝั่งแอป)
      deploy\diagnose_iis.ps1     (ฝั่ง IIS — ไล่ตรวจ 11 ชั้น)
      deploy\run_standalone.ps1   (แผนสำรอง: ไม่ใช้ HttpPlatformHandler)

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

# ⚠️ `return if (...) {...} else {...}` ใช้ไม่ได้ใน PowerShell (if เป็น statement
#    ไม่ใช่ expression ที่ต่อท้าย return ได้) — เคยเขียนแบบนั้นแล้วสคริปต์
#    พังตั้งแต่ตอน parse คือ "รันไม่ได้เลยทั้งไฟล์" ไม่ใช่แค่บรรทัดนี้
function Ask([string]$label, [string]$default = "") {
    $hint = ""
    if ($default) { $hint = " [$default]" }
    $val = Read-Host "       $label$hint"
    if ([string]::IsNullOrWhiteSpace($val)) { return $default }
    return $val
}

function AskSecret([string]$label, [string]$default = "") {
    $hint = ""
    if ($default) { $hint = " [กด Enter = สุ่มอัตโนมัติ]" }
    $val = Read-Host "       $label$hint"
    if ([string]::IsNullOrWhiteSpace($val)) { return $default }
    return $val
}

function Esc-Xml([string]$s) {
    <#  ค่าที่ผู้ใช้พิมพ์เข้ามาถูกยัดลง attribute ของ XML โดยตรง — รหัสผ่านที่มี
        & < > " ' จะทำให้ web.config เสียรูปและ IIS ขึ้น 500.19 ทันที
        ต้อง escape ทุกค่า ไม่ใช่แค่ & ของ path                                #>
    if ($null -eq $s) { return "" }
    $s.Replace("&","&amp;").Replace("<","&lt;").Replace(">","&gt;").
       Replace('"',"&quot;").Replace("'","&apos;")
}

# ── locate paths ─────────────────────────────────────────────────────────────
$Deploy = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root   = Split-Path -Parent $Deploy
$VPy    = Join-Path $Root ".venv\Scripts\python.exe"

Write-Banner "VisionIQ — IIS Deployment Script"
Write-Host "  Script path  : $PSCommandPath"
Write-Host "  Project root : $Root"

# ── STEP 1: verify project files ─────────────────────────────────────────────
Write-Step "1/10" "ตรวจไฟล์โปรเจกต์"
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
Write-Step "2/10" "ตรวจ Python 3.9"
if (-not (Test-Path $PythonExe)) {
    Write-Fail "Python ไม่พบที่: $PythonExe"
    Write-Host "         รัน script ใหม่ด้วย: .\deploy\deploy_iis.ps1 -PythonExe 'D:\Python39\python.exe'" -ForegroundColor Yellow
    Write-Host "         หรือหาว่า Python อยู่ที่ไหน: where python" -ForegroundColor Yellow
    exit 1
}
$pyVer = & $PythonExe --version 2>&1
Write-OK $pyVer

# ── STEP 3: venv ─────────────────────────────────────────────────────────────
Write-Step "3/10" "Virtual environment (.venv)"
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
Write-Step "4/10" "ติดตั้ง packages (อาจใช้เวลา 10-20 นาที — torch ~2 GB)"
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
Write-Step "5/10" "ตั้งค่า (กด Enter เพื่อใช้ค่า default)"
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
Write-Step "6/10" "สร้าง web.config"

# สร้างโฟลเดอร์ logs ถ้ายังไม่มี
New-Item -ItemType Directory -Force (Join-Path $Root "logs") | Out-Null

$VPyEsc    = Esc-Xml $VPy
$RootEsc   = Esc-Xml $Root
$DeployEsc = Esc-Xml $Deploy

# ค่าที่รับจากผู้ใช้ต้อง escape ด้วย — รหัสผ่านที่มี & หรือ " พบได้บ่อยมาก
$SqlServerX  = Esc-Xml $SqlServer
$SqlDbX      = Esc-Xml $SqlDb
$SqlUserX    = Esc-Xml $SqlUser
$SqlPassX    = Esc-Xml $SqlPass
$JwtSecretX  = Esc-Xml $JwtSecret
$N8nOcrX     = Esc-Xml $N8nOcr
$N8nTransX   = Esc-Xml $N8nTrans

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

        <environmentVariable name="SQL_SERVER"   value="$SqlServerX" />
        <environmentVariable name="SQL_DATABASE" value="$SqlDbX" />
        <environmentVariable name="SQL_USER"     value="$SqlUserX" />
        <environmentVariable name="SQL_PASSWORD" value="$SqlPassX" />

        <environmentVariable name="AUTH_ENABLED"           value="1" />
        <environmentVariable name="AUTH_JWT_SECRET"        value="$JwtSecretX" />
        <environmentVariable name="AUTH_COOKIE_SECURE"     value="$CookieSecure" />
        <environmentVariable name="AUTH_ACCESS_TTL_MIN"    value="60" />
        <environmentVariable name="AUTH_REFRESH_TTL_DAYS"  value="7" />
        <environmentVariable name="AUTH_MAX_FAILED"        value="5" />
        <environmentVariable name="AUTH_LOCK_MINUTES"      value="15" />

        <environmentVariable name="N8N_OCR_WEBHOOK_URL"       value="$N8nOcrX" />
        <environmentVariable name="N8N_OCR_TIMEOUT_S"         value="60" />
        <environmentVariable name="N8N_TRANSLATE_WEBHOOK_URL" value="$N8nTransX" />

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
# เขียนแบบไม่มี BOM — Set-Content -Encoding UTF8 บน PowerShell 5.1 ใส่ BOM ให้เสมอ
[IO.File]::WriteAllText($webconfigPath, $webconfig, (New-Object System.Text.UTF8Encoding($false)))
Write-OK "web.config เขียนที่: $webconfigPath"

# ตรวจทันทีว่า XML ที่เพิ่งเขียนใช้ได้จริง — ดีกว่าไปเจอเป็น 500.19 ตอนเปิดเว็บ
try {
    [xml](Get-Content $webconfigPath -Raw) | Out-Null
    Write-OK "web.config เป็น XML ที่ถูกต้อง"
} catch {
    Write-Fail "web.config ที่สร้างขึ้นไม่ใช่ XML ที่ถูกต้อง: $($_.Exception.Message)"
    Write-Warn "มักเกิดจากอักขระพิเศษในรหัสผ่าน — ตรวจไฟล์แล้วแก้ก่อนไปต่อ"
    exit 1
}

# ── STEP 7: IIS — App Pool + Website ─────────────────────────────────────────
# ⚠️ PowerShell ไม่มี goto/label — เคยเขียน `goto SkipIIS` ไว้ ทำให้สคริปต์
#    พังตั้งแต่ตอน parse ต้องใช้ if/else ครอบแทน
Write-Step "7/10" "ตั้งค่า IIS (Application Pool + Website)"

$iisReady = $true
try {
    Import-Module WebAdministration -ErrorAction Stop
} catch {
    $iisReady = $false
    Write-Fail "ไม่พบ module WebAdministration — ตรวจว่า IIS ถูกติดตั้งแล้ว"
    Write-Warn "ข้ามขั้นนี้ — ต้องสร้าง App Pool และ Website ใน IIS Manager เอง"
}

if ($iisReady) {

    # ── 7.1 HttpPlatformHandler ต้องมีก่อน ไม่งั้นได้ 500.19 แน่นอน ──────────
    $hphDll = Join-Path $env:windir "system32\inetsrv\httpPlatformHandler.dll"
    if (Test-Path $hphDll) {
        $hphVer = (Get-Item $hphDll).VersionInfo.FileVersion
        Write-OK "HttpPlatformHandler ติดตั้งแล้ว (เวอร์ชัน $hphVer)"
        if ($hphVer -and $hphVer -notmatch "^1\.2") {
            Write-Warn "เวอร์ชันไม่ใช่ 1.2.x — รุ่น 1.0 ไม่รองรับ <environmentVariables>"
        }
    } else {
        Write-Fail "ไม่พบ HttpPlatformHandler — IIS จะขึ้น 500.19 เพราะไม่รู้จัก <httpPlatform>"
        Write-Warn "ติดตั้งก่อนจาก Microsoft (HttpPlatformHandler v1.2) แล้วรันสคริปต์นี้ใหม่"
    }

    # ── 7.2 ปลดล็อก config section ────────────────────────────────────────────
    # IIS ล็อก 2 section นี้ไว้ที่ระดับเครื่องโดยค่าเริ่มต้น (overrideModeDefault="Deny")
    # ทำให้ web.config ของ site ใช้ไม่ได้ → HTTP 500.19 (0x80070021)
    # เป็นด่านที่คนติดกันมากที่สุดและข้อความ error ไม่ได้บอกวิธีแก้
    $appcmd = Join-Path $env:windir "system32\inetsrv\appcmd.exe"
    foreach ($sec in @("system.webServer/handlers", "system.webServer/httpPlatform")) {
        # appcmd เป็นโปรแกรมภายนอก — มันไม่โยน exception ให้ try/catch จับ
        # ต้องดู $LASTEXITCODE เอง ไม่งั้นจะรายงานว่าสำเร็จทั้งที่ล้มเหลว
        $out = & $appcmd unlock config "-section:$sec" 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-OK "ปลดล็อก section '$sec'"
        } else {
            # ปลดไว้อยู่แล้วก็คืนค่าไม่เป็นศูนย์ได้ — ตรวจสถานะจริงอีกที
            $mode = ""
            try {
                $mode = (Get-WebConfiguration -PSPath "MACHINE/WEBROOT/APPHOST" `
                            -Filter $sec -Metadata).Metadata.effectiveOverrideMode
            } catch { }
            if ($mode -eq "Allow") { Write-OK "section '$sec' ปลดล็อกอยู่แล้ว" }
            else { Write-Warn "ปลดล็อก '$sec' ไม่สำเร็จ ($($out -join ' ')) — จะได้ 500.19 ตอนเปิดเว็บ" }
        }
    }

    # ── 7.3 Application Pool ──────────────────────────────────────────────────
    if (Test-Path "IIS:\AppPools\$AppPool") {
        Write-Warn "App Pool '$AppPool' มีอยู่แล้ว — ปรับค่าให้ถูกต้องแทนการสร้างใหม่"
    } else {
        New-WebAppPool -Name $AppPool | Out-Null
        Write-OK "สร้าง App Pool '$AppPool'"
    }
    # ตั้งค่าทุกครั้ง (ไม่ใช่เฉพาะตอนสร้าง) เผื่อ pool เดิมถูกตั้งไว้ผิด
    Set-ItemProperty "IIS:\AppPools\$AppPool" -Name managedRuntimeVersion     -Value ""
    Set-ItemProperty "IIS:\AppPools\$AppPool" -Name enable32BitAppOnWin64     -Value $false
    Set-ItemProperty "IIS:\AppPools\$AppPool" -Name processModel.identityType -Value "ApplicationPoolIdentity"
    # ปิด recycle ตามเวลา — ไม่งั้นแอปจะรีสตาร์ตกลางวันทำงาน (default 1740 นาที)
    Set-ItemProperty "IIS:\AppPools\$AppPool" -Name recycling.periodicRestart.time -Value "00:00:00"
    Write-OK "ตั้ง App Pool: No Managed Code, 64-bit, ApplicationPoolIdentity, ไม่ recycle ตามเวลา"

    # ── 7.4 หา website อื่นที่จับพอร์ตเดียวกันอยู่ ─────────────────────────────
    # ถ้าไม่จัดการก่อน Start-Website จะขึ้น "Cannot create a file when that file
    # already exists" ซึ่งเป็นข้อความที่ไม่ได้บอกเลยว่าปัญหาคือพอร์ตชนกัน
    $conflicts = @()
    foreach ($w in (Get-Website)) {
        if ($w.Name -eq $SiteName) { continue }
        foreach ($b in $w.Bindings.Collection) {
            if ($b.protocol -eq "http" -and $b.bindingInformation -match ":$HttpPort:") {
                $conflicts += $w
            }
        }
    }
    foreach ($c in ($conflicts | Select-Object -Unique)) {
        Write-Warn "Website '$($c.Name)' จับพอร์ต $HttpPort อยู่ (สถานะ $($c.State))"
        if ($c.State -eq "Started") {
            $ans = Ask "หยุด '$($c.Name)' เพื่อให้ '$SiteName' ใช้พอร์ต $HttpPort ได้? (y/n)" "y"
            if ($ans -match '^[yY]') {
                Stop-Website -Name $c.Name
                Write-OK "หยุด Website '$($c.Name)' แล้ว"
            } else {
                Write-Warn "ไม่ได้หยุด — '$SiteName' จะสตาร์ตไม่ขึ้นจนกว่าจะแก้พอร์ตชนกัน"
            }
        }
    }

    # ── 7.5 Website ───────────────────────────────────────────────────────────
    if (Test-Path "IIS:\Sites\$SiteName") {
        Write-Warn "Website '$SiteName' มีอยู่แล้ว — ปรับ physical path และ app pool ให้ตรง"
        Set-ItemProperty "IIS:\Sites\$SiteName" -Name physicalPath    -Value $Root
        Set-ItemProperty "IIS:\Sites\$SiteName" -Name applicationPool -Value $AppPool
    } else {
        New-Website -Name $SiteName -Port ([int]$HttpPort) -PhysicalPath $Root `
                    -ApplicationPool $AppPool | Out-Null
        Write-OK "สร้าง Website '$SiteName' พอร์ต $HttpPort → $Root"
    }
}

# ── STEP 8: NTFS permissions ──────────────────────────────────────────────────
Write-Step "8/10" "ตั้ง NTFS permission"

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
Write-Step "9/10" "ตรวจความพร้อม (check_server.py)"
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

# ── STEP 10: เริ่ม site แล้วพิสูจน์ว่าใช้งานได้จริง ──────────────────────────
# ขั้นนี้เคยขาดไป ทำให้สคริปต์ "จบแบบสำเร็จ" ทั้งที่เว็บยังเปิดไม่ได้
Write-Step "10/10" "เริ่ม Website แล้วทดสอบจริง"

$siteWorks = $false
if ($iisReady) {
    try {
        Start-WebAppPool -Name $AppPool -ErrorAction SilentlyContinue
        Start-Website    -Name $SiteName -ErrorAction Stop
        Write-OK "Website '$SiteName' เริ่มทำงานแล้ว"
    } catch {
        Write-Warn "Start-Website ไม่สำเร็จ: $($_.Exception.Message)"
        Write-Warn "ข้อความนี้เกือบทุกครั้งแปลว่า 'พอร์ต $HttpPort ถูก website อื่นจับอยู่'"
    }

    Write-Host "       ยิง request ทดสอบ (ครั้งแรกช้าได้ถึง 1-2 นาที เพราะ import torch) ..."
    $deadline = (Get-Date).AddSeconds(180)
    $lastErr  = ""
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:$HttpPort/" -UseBasicParsing `
                                   -TimeoutSec 30 -MaximumRedirection 0 -ErrorAction Stop
            $siteWorks = $true
            Write-OK "เว็บตอบกลับแล้ว (HTTP $($r.StatusCode))"
            break
        } catch {
            # 302 ไปหน้า login = ทำงานถูกต้อง แต่ Invoke-WebRequest ถือเป็น error
            # เพราะสั่งห้าม redirect ไว้ ต้องแยกกรณีนี้ออกจาก error จริง
            $resp = $_.Exception.Response
            if ($resp -and ([int]$resp.StatusCode) -in @(200,301,302,303,307,308,401,403)) {
                $siteWorks = $true
                Write-OK "เว็บตอบกลับแล้ว (HTTP $([int]$resp.StatusCode) — เด้งไปหน้า login ตามปกติ)"
                break
            }
            $lastErr = $_.Exception.Message
            Start-Sleep -Seconds 5
        }
    }

    if (-not $siteWorks) {
        Write-Fail "เว็บยังไม่ตอบกลับ: $lastErr"
        Write-Host ""
        Write-Host "       อย่าเดาทีละอย่าง — รันตัววินิจฉัยที่ไล่ตรวจให้ครบทุกชั้น:" -ForegroundColor Yellow
        Write-Host "         powershell -ExecutionPolicy Bypass -File `"$Deploy\diagnose_iis.ps1`" -OutFile C:\Temp\visioniq-diag.txt" -ForegroundColor White
        Write-Host ""
        Write-Host "       ถ้าผลบอกว่า IIS เปิดโปรเซสลูกไม่ได้เลย ให้ใช้แผนสำรอง:" -ForegroundColor Yellow
        Write-Host "         powershell -ExecutionPolicy Bypass -File `"$Deploy\run_standalone.ps1`" -Port $HttpPort" -ForegroundColor White
    }
}

# ── Summary ───────────────────────────────────────────────────────────────────
if ($siteWorks) { Write-Banner "Deploy เสร็จแล้ว — เว็บใช้งานได้จริง" }
else            { Write-Banner "Deploy เสร็จบางส่วน — เว็บยังเปิดไม่ได้ ดูคำแนะนำด้านบน" }

Write-Host ""
Write-Host "  ไฟล์ที่สร้าง:" -ForegroundColor White
Write-Host "    $webconfigPath" -ForegroundColor Gray
Write-Host "    $Root\logs\  (IIS stdout log)" -ForegroundColor Gray
Write-Host ""
Write-Host "  ขั้นตอนต่อไป:" -ForegroundColor White
Write-Host ""
Write-Host "  A) ตั้งฐานข้อมูล (ถ้ายังไม่ได้ทำ):" -ForegroundColor Yellow
Write-Host "     รัน schema (ต้องใส่ -f 65001 ไม่งั้นข้อความไทยเพี้ยน):"
Write-Host "       sqlcmd -f 65001 -S $SqlServer -d $SqlDb -U $SqlUser -P <pwd> -i Connection_sql\auth_schema.sql"
Write-Host "     สร้าง/รีเซ็ตผู้ใช้แอดมิน (ต้องเรียกแบบ module ไม่ใช่เรียกไฟล์ตรง ๆ):"
Write-Host "       $VPy -m auth.seed_admin --username admin"
Write-Host "     ⚠️ seed_admin เป็น upsert — ถ้ามี user ชื่อนั้นอยู่แล้วจะ" -ForegroundColor Yellow
Write-Host "        ทับรหัสผ่านเดิม เช็คก่อนด้วย: SELECT Username FROM AuthUsers" -ForegroundColor Yellow
Write-Host ""
Write-Host "  B) ทดสอบแอปโดยไม่ผ่าน IIS (ใช้แยกว่าปัญหาอยู่ที่แอปหรือที่ IIS):" -ForegroundColor Yellow
Write-Host "     $VPy deploy\wsgi_iis.py"
Write-Host "     เปิดเบราว์เซอร์: http://localhost:8000"
Write-Host ""
Write-Host "  C) เข้าใช้งานผ่าน IIS:" -ForegroundColor Yellow
$ipList = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -notlike "127.*" } | Select-Object -ExpandProperty IPAddress)
$portSuffix = ""
if ($HttpPort -ne "80") { $portSuffix = ":$HttpPort" }
foreach ($ip in $ipList) { Write-Host "     http://$ip$portSuffix" }
Write-Host ""
if ($CookieSecure -eq "0") {
    Write-Host "  D) เมื่อพร้อมเพิ่ม HTTPS:" -ForegroundColor Yellow
    Write-Host "     ผูก HTTPS binding ใน IIS → แก้ web.config:"
    Write-Host "       AUTH_COOKIE_SECURE = 1"
    Write-Host "     แล้ว Recycle App Pool"
    Write-Host "     (ตั้ง 1 ทั้งที่ยังเป็น http:// = ล็อกอินแล้ววนกลับหน้า login)"
    Write-Host ""
}
Write-Host "  log ของ Python: $Root\logs\iis-stdout*.log" -ForegroundColor Gray
Write-Host "  ถ้าเว็บไม่ขึ้น เรียงลำดับการตรวจแบบนี้:" -ForegroundColor Gray
Write-Host "    1) $VPy deploy\check_server.py            (ฝั่งแอป: package/DB/OCR)" -ForegroundColor Gray
Write-Host "    2) powershell -File deploy\diagnose_iis.ps1  (ฝั่ง IIS: 11 ชั้น)" -ForegroundColor Gray
Write-Host "    3) powershell -File deploy\run_standalone.ps1 (แผนสำรอง ไม่ใช้ IIS)" -ForegroundColor Gray
Write-Host ""
Write-Host "$("=" * 68)" -ForegroundColor Cyan
