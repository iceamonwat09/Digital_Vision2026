<#
.SYNOPSIS
    ตรวจวินิจฉัยปัญหา VisionIQ บน IIS + HttpPlatformHandler (โดยเฉพาะ HTTP 502.3)

.DESCRIPTION
    สคริปต์นี้ "ไล่ตรวจทีละชั้น" ตามลำดับที่ request เดินทางจริง แล้วสรุปว่าพังที่ชั้นไหน
    แทนการเดาทีละอย่าง ทุกหัวข้อรายงานเป็น OK / WARN / FAIL พร้อมเหตุผล

        ชั้น 1  เครื่อง + สิทธิ์ที่รันสคริปต์
        ชั้น 2  IIS wiring (Website / Application Pool / binding)
        ชั้น 3  การล็อก configuration section (สาเหตุของ 500.19)
        ชั้น 4  ตัว HttpPlatformHandler เอง (module + เวอร์ชัน)
        ชั้น 5  web.config และ path ทุกตัวที่อ้างถึงในนั้น
        ชั้น 6  พอร์ตที่ระบบสงวนไว้ (สาเหตุที่ทำให้ spawn ล้มเหลวแบบไร้ร่องรอย)
        ชั้น 7  ตัว Python / venv (รันตรง ๆ ได้ไหม, package ครบไหม)
        ชั้น 8  ซอฟต์แวร์ความปลอดภัยที่บล็อกการสร้าง process (AppLocker / Defender)
        ชั้น 9  Event log ย้อนหลัง
        ชั้น 10 ทดสอบจริง: ให้ IIS เปิด process แล้วดูว่าเกิดขึ้นจริงไหม
        ชั้น 11 ทดสอบชี้ขาด: เปลี่ยนให้ IIS เปิด cmd.exe แทน python ชั่วคราว
                (แยกให้ขาดว่า "เปิด process ไม่ได้เลย" หรือ "เปิดได้ แต่ติดที่ python")

    ⚠️ ชั้น 11 จะแก้ web.config ชั่วคราวแล้ว "คืนค่าเดิมเสมอ" ผ่าน try/finally
       (สำรองไว้ที่ web.config.diagbak) ถ้าไม่ต้องการให้แตะไฟล์เลย ใส่ -SkipDeepTest

.PARAMETER Root
    โฟลเดอร์รากของโปรเจกต์บนเซิร์ฟเวอร์

.PARAMETER Site
    ชื่อ Website ใน IIS

.PARAMETER Pool
    ชื่อ Application Pool ใน IIS

.PARAMETER Url
    URL ที่ใช้ทดสอบ (ควรเป็น localhost เพื่อตัดปัจจัยเรื่อง firewall/เครือข่ายออก)

.PARAMETER SkipLiveTest
    ข้ามชั้น 10 (ไม่ recycle app pool และไม่ยิง request)

.PARAMETER SkipDeepTest
    ข้ามชั้น 11 (ไม่แตะ web.config เลย)

.PARAMETER OutFile
    เขียนผลทั้งหมดลงไฟล์ข้อความด้วย (ส่งต่อให้คนอื่นดูได้)

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\diagnose_iis.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy\diagnose_iis.ps1 -OutFile C:\Temp\visioniq-diag.txt

.NOTES
    ต้องรันด้วยสิทธิ์ Administrator
    รองรับ Windows PowerShell 5.1 (ที่มากับ Windows Server) — ไม่ต้องลง PowerShell 7
#>

[CmdletBinding()]
param(
    [string] $Root         = "C:\VisionIQ\Digital_Vision2026",
    [string] $Site         = "VisionIQ",
    [string] $Pool         = "VisionIQPool",
    [string] $Url          = "http://localhost/",
    [switch] $SkipLiveTest,
    [switch] $SkipDeepTest,
    [string] $OutFile
)

$ErrorActionPreference = "Continue"

# ────────────────────────────────────────────────────────────────────────────
#  โครงพื้นฐานของการรายงานผล
# ────────────────────────────────────────────────────────────────────────────

$script:Findings = @()
$script:Lines    = @()

function Write-Line {
    param([string] $Text, [string] $Color = "Gray")
    Write-Host $Text -ForegroundColor $Color
    $script:Lines += $Text
}

function Write-Section {
    param([string] $Title)
    Write-Line ""
    Write-Line ("=" * 78) "DarkCyan"
    Write-Line "  $Title" "Cyan"
    Write-Line ("=" * 78) "DarkCyan"
}

function Add-Result {
    <#  บันทึกผลตรวจ 1 ข้อ
        Status: OK / WARN / FAIL / INFO
        Fix:    สิ่งที่ควรทำถ้าข้อนี้ FAIL (ว่างได้)  #>
    param(
        [Parameter(Mandatory)][ValidateSet("OK","WARN","FAIL","INFO")][string] $Status,
        [Parameter(Mandatory)][string] $Name,
        [string] $Detail = "",
        [string] $Fix    = ""
    )
    $color = switch ($Status) {
        "OK"   { "Green"  }
        "WARN" { "Yellow" }
        "FAIL" { "Red"    }
        default{ "Gray"   }
    }
    $tag = "[{0,-4}]" -f $Status
    Write-Line ("{0} {1}" -f $tag, $Name) $color
    if ($Detail) {
        foreach ($d in ($Detail -split "`n")) { Write-Line ("        " + $d) "DarkGray" }
    }
    $script:Findings += [pscustomobject]@{
        Status = $Status; Name = $Name; Detail = $Detail; Fix = $Fix
    }
}

function Test-Elevated {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Safe {
    <# เรียกคำสั่งโดยไม่ให้ error ตัวเดียวทำให้สคริปต์ทั้งตัวหยุด #>
    param([scriptblock] $Script, [string] $What)
    try { & $Script }
    catch {
        Add-Result WARN "ตรวจ '$What' ไม่สำเร็จ" $_.Exception.Message
        $null
    }
}

function Get-NewProcess {
    <#  คืนโปรเซสชื่อที่ระบุซึ่งเริ่มหลังเวลา $Since

        การอ่าน .StartTime ของโปรเซสที่เป็นของบัญชีอื่นจะโยน exception ได้
        ถ้าปล่อยไว้จะพ่นข้อความแดงเต็มจอจนอ่านผลตรวจไม่ออก จึงต้องกันทีละตัว  #>
    param([string[]] $Name, [datetime] $Since)
    $out = @()
    foreach ($p in @(Get-Process -Name $Name -ErrorAction SilentlyContinue)) {
        try { if ($p.StartTime -gt $Since) { $out += $p } } catch { }
    }
    $out
}

# ────────────────────────────────────────────────────────────────────────────

Write-Line ""
Write-Line "###############################################################################" "White"
Write-Line "#  VisionIQ — ตรวจวินิจฉัย IIS + HttpPlatformHandler                          #" "White"
Write-Line ("#  เวลา: {0,-66}#" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) "White"
Write-Line "###############################################################################" "White"

# ════════════════════════════════════════════════════════════════════ ชั้น 1
Write-Section "ชั้น 1 — เครื่องและสิทธิ์ที่รันสคริปต์"

if (Test-Elevated) {
    Add-Result OK "รันด้วยสิทธิ์ Administrator"
} else {
    Add-Result FAIL "ไม่ได้รันด้วยสิทธิ์ Administrator" `
        "ผลตรวจหลายข้อจะอ่านค่าไม่ได้และคลาดเคลื่อน" `
        "ปิดหน้าต่างนี้ แล้วเปิด PowerShell ใหม่แบบ 'Run as Administrator'"
}

Invoke-Safe {
    $os = Get-CimInstance Win32_OperatingSystem
    Add-Result INFO "ระบบปฏิบัติการ" ("{0} (build {1})" -f $os.Caption, $os.BuildNumber)
} "OS"

Add-Result INFO "PowerShell" $PSVersionTable.PSVersion.ToString()

if (-not (Get-Module -ListAvailable -Name WebAdministration)) {
    Add-Result FAIL "ไม่พบ module WebAdministration" `
        "อ่านค่าจาก IIS ไม่ได้เลย" `
        "ติดตั้ง IIS Management Scripts and Tools ผ่าน Server Manager"
} else {
    Import-Module WebAdministration -ErrorAction SilentlyContinue
    Add-Result OK "module WebAdministration พร้อมใช้งาน"
}

# ════════════════════════════════════════════════════════════════════ ชั้น 2
Write-Section "ชั้น 2 — Website / Application Pool / Binding"

$siteObj = Invoke-Safe { Get-Website -Name $Site -ErrorAction Stop } "Website"
if (-not $siteObj) {
    Add-Result FAIL "ไม่พบ Website '$Site'" "" "สร้าง Website ก่อน หรือส่งชื่อที่ถูกต้องผ่าน -Site"
} else {
    if ($siteObj.State -eq "Started") {
        Add-Result OK "Website '$Site' สถานะ Started"
    } else {
        Add-Result FAIL "Website '$Site' สถานะ $($siteObj.State)" `
            "" "Start-Website -Name '$Site'  (ถ้า error ว่าไฟล์มีอยู่แล้ว = พอร์ตชนกับ site อื่น)"
    }

    $phys = $siteObj.physicalPath
    if ($phys -and (Test-Path $phys)) {
        Add-Result OK "Physical path ของ Website" $phys
        if ($phys.TrimEnd('\') -ne $Root.TrimEnd('\')) {
            Add-Result WARN "Physical path ไม่ตรงกับ -Root ที่ส่งเข้ามา" `
                "site = $phys`nroot = $Root" `
                "ตรวจว่ากำลังดูโฟลเดอร์เดียวกันหรือไม่"
        }
    } else {
        Add-Result FAIL "Physical path ไม่มีอยู่จริง" $phys "แก้ physical path ของ Website ใน IIS"
    }

    foreach ($b in $siteObj.Bindings.Collection) {
        Add-Result INFO "Binding" ("{0} {1}" -f $b.protocol, $b.bindingInformation)
    }
}

# หา website อื่นที่ชนพอร์ตเดียวกัน — สาเหตุที่ Start-Website ขึ้น "file already exists"
Invoke-Safe {
    $all = Get-Website
    $mine = @()
    if ($siteObj) { $mine = @($siteObj.Bindings.Collection | ForEach-Object { $_.bindingInformation }) }
    foreach ($w in $all) {
        if ($w.Name -eq $Site) { continue }
        foreach ($b in $w.Bindings.Collection) {
            if ($mine -contains $b.bindingInformation) {
                $st = if ($w.State -eq "Started") { "FAIL" } else { "INFO" }
                Add-Result $st "Website '$($w.Name)' ใช้ binding ซ้ำกัน" `
                    ("{0} (สถานะ {1})" -f $b.bindingInformation, $w.State) `
                    "Stop-Website -Name '$($w.Name)'  หรือเปลี่ยนพอร์ตของ site ใดsite หนึ่ง"
            }
        }
    }
} "binding ซ้ำ"

$poolObj = Invoke-Safe { Get-Item "IIS:\AppPools\$Pool" -ErrorAction Stop } "AppPool"
if (-not $poolObj) {
    Add-Result FAIL "ไม่พบ Application Pool '$Pool'" "" "สร้าง App Pool ก่อน"
} else {
    $state = (Get-WebAppPoolState -Name $Pool).Value
    if ($state -eq "Started") { Add-Result OK "App Pool '$Pool' สถานะ Started" }
    else { Add-Result FAIL "App Pool '$Pool' สถานะ $state" "" "Start-WebAppPool -Name '$Pool'" }

    $runtime = $poolObj.managedRuntimeVersion
    if ([string]::IsNullOrEmpty($runtime)) {
        Add-Result OK "App Pool ตั้ง .NET CLR = No Managed Code (ถูกต้อง)"
    } else {
        Add-Result WARN "App Pool ตั้ง .NET CLR = '$runtime'" `
            "โหมดนี้ควรเป็น No Managed Code" `
            "Set-ItemProperty 'IIS:\AppPools\$Pool' -Name managedRuntimeVersion -Value ''"
    }

    Add-Result INFO "App Pool identity" $poolObj.processModel.identityType
    if ($poolObj.processModel.identityType -ne "ApplicationPoolIdentity") {
        Add-Result WARN "identity ไม่ใช่ ApplicationPoolIdentity" `
            "ถ้าเปลี่ยนไว้ตอน debug อย่าลืมเปลี่ยนกลับ (สิทธิ์สูงเกินจำเป็น = ความเสี่ยง)" `
            "Set-ItemProperty 'IIS:\AppPools\$Pool' -Name processModel.identityType -Value ApplicationPoolIdentity"
    }

    Add-Result INFO "enable32BitAppOnWin64" $poolObj.enable32BitAppOnWin64
    if ($poolObj.enable32BitAppOnWin64 -eq $true) {
        Add-Result WARN "App Pool ตั้งเป็น 32-bit" `
            "Python ที่ติดตั้งเป็น 64-bit — ไม่ควรเปิดตัวเลือกนี้" `
            "Set-ItemProperty 'IIS:\AppPools\$Pool' -Name enable32BitAppOnWin64 -Value False"
    }

    # Website ผูกกับ App Pool ตัวนี้จริงหรือไม่
    Invoke-Safe {
        $appPoolOfSite = (Get-ItemProperty "IIS:\Sites\$Site" -Name applicationPool -ErrorAction Stop).Value
        if ($appPoolOfSite -eq $Pool) {
            Add-Result OK "Website '$Site' ผูกกับ App Pool '$Pool'"
        } else {
            Add-Result FAIL "Website '$Site' ผูกกับ App Pool '$appPoolOfSite' ไม่ใช่ '$Pool'" `
                "" "Set-ItemProperty 'IIS:\Sites\$Site' -Name applicationPool -Value '$Pool'"
        }
    } "การผูก site กับ pool"
}

# ════════════════════════════════════════════════════════════════════ ชั้น 3
Write-Section "ชั้น 3 — การล็อก configuration section (ต้นเหตุของ HTTP 500.19)"

foreach ($sec in @("system.webServer/handlers", "system.webServer/httpPlatform")) {
    Invoke-Safe {
        $cfg = Get-WebConfiguration -PSPath "MACHINE/WEBROOT/APPHOST" -Filter $sec -Metadata -ErrorAction Stop
        $mode = $cfg.Metadata.effectiveOverrideMode
        if ($mode -eq "Allow") {
            Add-Result OK "section '$sec' ปลดล็อกแล้ว (overrideMode=Allow)"
        } else {
            Add-Result FAIL "section '$sec' ถูกล็อก (overrideMode=$mode)" `
                "web.config ของ site จะใช้ section นี้ไม่ได้ → HTTP 500.19" `
                "& `"`$env:windir\system32\inetsrv\appcmd.exe`" unlock config -section:$sec"
        }
    } "override mode ของ $sec"
}

# ════════════════════════════════════════════════════════════════════ ชั้น 4
Write-Section "ชั้น 4 — HttpPlatformHandler"

$hphDll = Join-Path $env:windir "system32\inetsrv\httpPlatformHandler.dll"
if (Test-Path $hphDll) {
    $ver = (Get-Item $hphDll).VersionInfo.FileVersion
    Add-Result OK "พบไฟล์ httpPlatformHandler.dll" "เวอร์ชัน $ver`n$hphDll"
    if ($ver -and $ver -notmatch "^1\.2") {
        Add-Result WARN "เวอร์ชันไม่ใช่ 1.2.x" `
            "เวอร์ชัน 1.0 ไม่รองรับ <environmentVariables>" `
            "ติดตั้ง HttpPlatformHandler v1.2 ทับ"
    }
} else {
    Add-Result FAIL "ไม่พบ httpPlatformHandler.dll" $hphDll `
        "ติดตั้ง HttpPlatformHandler v1.2 จาก Microsoft"
}

Invoke-Safe {
    $gm = Get-WebGlobalModule | Where-Object { $_.Name -like "*httpPlatform*" }
    if ($gm) { Add-Result OK "ลงทะเบียนเป็น global module แล้ว" ("{0} -> {1}" -f $gm.Name, $gm.Image) }
    else {
        Add-Result FAIL "ไม่ได้ลงทะเบียนเป็น global module" "" `
            "ติดตั้ง HttpPlatformHandler ใหม่ (installer จะลงทะเบียนให้เอง)"
    }
} "global module"

# ════════════════════════════════════════════════════════════════════ ชั้น 5
Write-Section "ชั้น 5 — web.config และ path ทุกตัวที่อ้างถึง"

$webConfig = Join-Path $Root "web.config"
$hp = $null
if (-not (Test-Path $webConfig)) {
    Add-Result FAIL "ไม่พบ web.config" $webConfig "คัดลอก deploy\web.config.example ไปวางที่รากเว็บไซต์"
} else {
    Add-Result OK "พบ web.config" $webConfig
    try {
        [xml]$xml = Get-Content $webConfig -Raw -ErrorAction Stop
        Add-Result OK "web.config เป็น XML ที่ถูกต้อง"
        $hp = $xml.configuration.'system.webServer'.httpPlatform
        if (-not $hp) {
            Add-Result FAIL "ไม่มี element <httpPlatform> ใน web.config" "" `
                "ใช้ deploy\web.config.example เป็นต้นแบบ"
        }
    } catch {
        Add-Result FAIL "web.config ไม่ใช่ XML ที่ถูกต้อง" $_.Exception.Message "แก้ไวยากรณ์ XML"
    }
}

if ($hp) {
    $processPath = $hp.processPath
    $arguments   = $hp.arguments
    $workDir     = $hp.workingDirectory
    $stdoutFile  = $hp.stdoutLogFile
    $stdoutOn    = $hp.stdoutLogEnabled

    Add-Result INFO "processPath"      $processPath
    Add-Result INFO "arguments"        $arguments
    Add-Result INFO "workingDirectory" $workDir
    Add-Result INFO "stdoutLogFile"    ("{0}  (stdoutLogEnabled={1})" -f $stdoutFile, $stdoutOn)

    # processPath ต้องมีอยู่จริง
    if ($processPath -and (Test-Path $processPath)) {
        Add-Result OK "processPath มีไฟล์อยู่จริง"
    } else {
        Add-Result FAIL "processPath ไม่มีไฟล์อยู่จริง" $processPath `
            "แก้ processPath ใน web.config ให้ตรงกับ python.exe ที่มีจริง"
    }

    # arguments ตัวแรก = ไฟล์สคริปต์ ต้องมีอยู่จริง
    if ($arguments) {
        $firstArg = ($arguments -split '\s+')[0]
        if ($firstArg -match '^[A-Za-z]:\\' ) {
            if (Test-Path $firstArg) { Add-Result OK "ไฟล์สคริปต์ใน arguments มีอยู่จริง" $firstArg }
            else { Add-Result FAIL "ไฟล์สคริปต์ใน arguments ไม่มีอยู่จริง" $firstArg "แก้ path ใน arguments" }
        }
    }

    if ($workDir -and (Test-Path $workDir)) { Add-Result OK "workingDirectory มีอยู่จริง" }
    elseif ($workDir) { Add-Result FAIL "workingDirectory ไม่มีอยู่จริง" $workDir "แก้ path" }

    # โฟลเดอร์ของ stdoutLogFile ต้องมีอยู่ก่อน ไม่งั้น handler เปิด process ไม่ได้
    if ($stdoutOn -eq "true" -and $stdoutFile) {
        $logDir = Split-Path $stdoutFile -Parent
        if (Test-Path $logDir) {
            Add-Result OK "โฟลเดอร์ของ stdoutLogFile มีอยู่จริง" $logDir
            # ทดสอบเขียนไฟล์จริงในโฟลเดอร์นั้น
            try {
                $probe = Join-Path $logDir ("_diag_probe_{0}.tmp" -f $PID)
                [IO.File]::WriteAllText($probe, "probe")
                Remove-Item $probe -Force -ErrorAction SilentlyContinue
                Add-Result OK "เขียนไฟล์ลงโฟลเดอร์ log ได้ (ในสิทธิ์ของผู้รันสคริปต์)"
            } catch {
                Add-Result WARN "เขียนไฟล์ลงโฟลเดอร์ log ไม่ได้" $_.Exception.Message
            }
        } else {
            Add-Result FAIL "โฟลเดอร์ของ stdoutLogFile ไม่มีอยู่จริง" $logDir `
                "New-Item -ItemType Directory -Path '$logDir' -Force  (ถ้าโฟลเดอร์ไม่มี handler จะเปิด process ไม่ได้)"
        }
    }

    # environment variables — ชื่อ/ค่าที่ว่างทำให้ environment block เสีย
    $envNodes = @()
    if ($hp.environmentVariables) { $envNodes = @($hp.environmentVariables.environmentVariable) }
    Add-Result INFO "จำนวน environment variables" $envNodes.Count
    $badEnv = @($envNodes | Where-Object { [string]::IsNullOrWhiteSpace($_.name) })
    if ($badEnv.Count -gt 0) {
        Add-Result FAIL "มี environmentVariable ที่ไม่มีชื่อ" "$($badEnv.Count) รายการ" "ลบรายการที่ name ว่างออก"
    }
    $dupEnv = @($envNodes | Group-Object name | Where-Object { $_.Count -gt 1 })
    if ($dupEnv.Count -gt 0) {
        Add-Result FAIL "มี environmentVariable ชื่อซ้ำ" (($dupEnv | ForEach-Object { $_.Name }) -join ", ") `
            "ลบตัวซ้ำออกให้เหลือชื่อละ 1 รายการ"
    }
    $secureCookie = @($envNodes | Where-Object { $_.name -eq "AUTH_COOKIE_SECURE" })
    if ($secureCookie -and $secureCookie[0].value -eq "1") {
        $hasHttps = $false
        if ($siteObj) {
            $hasHttps = @($siteObj.Bindings.Collection | Where-Object { $_.protocol -eq "https" }).Count -gt 0
        }
        if (-not $hasHttps) {
            Add-Result FAIL "AUTH_COOKIE_SECURE=1 แต่ Website ยังไม่มี binding https" `
                "เบราว์เซอร์จะทิ้งคุกกี้ → ล็อกอินแล้ววนกลับหน้า login" `
                "ตั้ง AUTH_COOKIE_SECURE=0 จนกว่าจะผูก HTTPS เสร็จ"
        }
    }
}

# แอตทริบิวต์ read-only ที่ติดมาจาก xcopy — พบได้บ่อยเมื่อคัดลอกโปรเจกต์ด้วยมือ
Invoke-Safe {
    if (Test-Path $Root) {
        $ro = @(Get-ChildItem $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
                Where-Object { $_.IsReadOnly } | Select-Object -First 5)
        if ($ro.Count -gt 0) {
            Add-Result WARN "มีไฟล์ที่ติดแอตทริบิวต์ read-only" `
                (($ro | ForEach-Object { $_.FullName }) -join "`n") `
                "attrib -R `"$Root\*.*`" /S  (แอตทริบิวต์นี้มักติดมาจาก xcopy)"
        } else {
            Add-Result OK "ไม่พบไฟล์ที่ติด read-only ในโปรเจกต์"
        }
    }
} "แอตทริบิวต์ read-only"

# ════════════════════════════════════════════════════════════════════ ชั้น 6
Write-Section "ชั้น 6 — พอร์ตที่ระบบสงวนไว้ (ทำให้ handler จองพอร์ตให้ลูกไม่ได้)"

<#  HttpPlatformHandler จะ "เลือกพอร์ตว่างในช่วง dynamic port" ให้โปรเซสลูกก่อน
    แล้วส่งมาทาง HTTP_PLATFORM_PORT. ถ้าช่วงนั้นถูก Hyper-V / WinNAT / Docker /
    WSL จองไว้เกือบหมด การจองจะล้มเหลว → 502.3 โดยไม่มีโปรเซสและไม่มี log เลย
    ซึ่งเป็นอาการเดียวกับที่พบ จึงต้องตรวจข้อนี้ทุกครั้ง                        #>

# ⚠️ scriptblock ที่เรียกด้วย & ทำงานใน child scope — ต้องเขียนกลับด้วย $script:
#    ไม่งั้นค่าจะหายไปก่อนถึงบล็อกถัดไปที่ต้องใช้เทียบ
$script:dynStart = $null
$script:dynNum   = $null
Invoke-Safe {
    $out = netsh int ipv4 show dynamicport tcp 2>&1 | Out-String
    if ($out -match "Start Port\s*:\s*(\d+)")      { $script:dynStart = [int]$Matches[1] }
    if ($out -match "Number of Ports\s*:\s*(\d+)") { $script:dynNum   = [int]$Matches[1] }
    if ($null -ne $script:dynStart -and $null -ne $script:dynNum) {
        Add-Result INFO "ช่วง dynamic port (TCP)" `
            ("{0} - {1}  (จำนวน {2})" -f $script:dynStart,
                                        ($script:dynStart + $script:dynNum - 1),
                                        $script:dynNum)
    }
} "dynamic port range"

Invoke-Safe {
    $dynStart = $script:dynStart
    $dynNum   = $script:dynNum
    $out = netsh int ipv4 show excludedportrange protocol=tcp 2>&1 | Out-String
    $ranges = @()
    foreach ($line in ($out -split "`r?`n")) {
        if ($line -match '^\s*(\d+)\s+(\d+)\s*$') {
            $ranges += [pscustomobject]@{ Start = [int]$Matches[1]; End = [int]$Matches[2] }
        }
    }
    if ($ranges.Count -eq 0) {
        Add-Result OK "ไม่มีช่วงพอร์ตที่ถูกสงวนไว้"
    } else {
        $total = ($ranges | ForEach-Object { $_.End - $_.Start + 1 } | Measure-Object -Sum).Sum
        Add-Result INFO "จำนวนช่วงพอร์ตที่ถูกสงวน" ("{0} ช่วง รวม {1} พอร์ต" -f $ranges.Count, $total)

        if ($dynStart -ne $null -and $dynNum -ne $null) {
            $dynEnd  = $dynStart + $dynNum - 1
            $overlap = 0
            foreach ($r in $ranges) {
                $s = [Math]::Max($r.Start, $dynStart)
                $e = [Math]::Min($r.End,   $dynEnd)
                if ($e -ge $s) { $overlap += ($e - $s + 1) }
            }
            $pct = if ($dynNum -gt 0) { [Math]::Round(100.0 * $overlap / $dynNum, 1) } else { 0 }
            $detail = "ทับซ้อน $overlap จาก $dynNum พอร์ต ($pct%)"
            if ($pct -ge 90) {
                Add-Result FAIL "ช่วง dynamic port ถูกสงวนไปเกือบหมด" $detail `
                    "หยุดบริการที่จองพอร์ต (net stop winnat) แล้วรีสตาร์ต หรือย้ายช่วง dynamic port ด้วย netsh int ipv4 set dynamicport tcp start=<n> num=<n>"
            } elseif ($pct -ge 50) {
                Add-Result WARN "ช่วง dynamic port ถูกสงวนไปมาก" $detail `
                    "ถ้าอาการเป็น ๆ หาย ๆ ให้สงสัยข้อนี้"
            } else {
                Add-Result OK "ช่วง dynamic port ยังเหลือให้ใช้เพียงพอ" $detail
            }
        }
        foreach ($r in ($ranges | Select-Object -First 12)) {
            Add-Result INFO "  ช่วงที่สงวน" ("{0} - {1}" -f $r.Start, $r.End)
        }
    }
} "excluded port range"

Invoke-Safe {
    $winnat = Get-Service winnat -ErrorAction SilentlyContinue
    if ($winnat) { Add-Result INFO "บริการ WinNAT" ("สถานะ {0}" -f $winnat.Status) }
} "winnat"

# ════════════════════════════════════════════════════════════════════ ชั้น 7
Write-Section "ชั้น 7 — Python / venv"

# หมายเหตุ: อย่าตั้งชื่อตัวแปรว่า $script — ชนกับ scope modifier $script: ที่ใช้อยู่
$python      = if ($hp -and $hp.processPath) { $hp.processPath } else { Join-Path $Root ".venv\Scripts\python.exe" }
$entryScript = if ($hp -and $hp.arguments)   { ($hp.arguments -split '\s+')[0] } else { Join-Path $Root "deploy\wsgi_iis.py" }

if (Test-Path $entryScript) {
    Add-Result OK "พบไฟล์ entry point ที่ IIS จะเรียก" $entryScript
} else {
    Add-Result FAIL "ไม่พบไฟล์ entry point" $entryScript "ตรวจ arguments ใน web.config"
}

if (Test-Path $python) {
    Invoke-Safe {
        $v = & $python -c "import sys;print(sys.version.replace(chr(10),' '))" 2>&1 | Out-String
        Add-Result OK "รัน python ได้" $v.Trim()
    } "python version"

    Invoke-Safe {
        $code = "import importlib" +
                "`nmods=['waitress','flask','pyodbc','bcrypt','jwt','fitz','spellchecker','pytesseract']" +
                "`nfor m in mods:" +
                "`n    try:" +
                "`n        importlib.import_module(m); print(m+'=OK')" +
                "`n    except Exception as e:" +
                "`n        print(m+'=MISSING')"
        $tmp = Join-Path $env:TEMP ("visioniq_impcheck_{0}.py" -f $PID)
        [IO.File]::WriteAllText($tmp, $code)
        $res = & $python $tmp 2>&1 | Out-String
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        $missing = @()
        foreach ($l in ($res -split "`r?`n")) {
            if ($l -match '^(\w+)=MISSING$') { $missing += $Matches[1] }
        }
        Add-Result INFO "ผลตรวจ package" $res.Trim()
        if ($missing -contains "waitress") {
            Add-Result FAIL "ไม่มี waitress" "" "$python -m pip install waitress"
        }
        if ($missing -contains "spellchecker") {
            Add-Result WARN "ไม่มี pyspellchecker" `
                "ชั้นตรวจ dictionary จะถูกข้ามเงียบ ๆ = จุดบอด QC (คำผิดจะขึ้นผ่าน)" `
                "$python -m pip install pyspellchecker"
        }
        if ($missing -contains "pytesseract") {
            Add-Result WARN "ไม่มี pytesseract" `
                "กรอบแดงชี้คำผิดจะไม่ขึ้นบนไฟล์ outline/ภาพถ่าย (ผลตรวจ QC เท่าเดิม)" `
                "$python -m pip install pytesseract  + ติดตั้ง Tesseract-OCR binary"
        }
    } "package"

    $cfgVenv = Join-Path $Root ".venv\pyvenv.cfg"
    if (Test-Path $cfgVenv) {
        $cfgText = (Get-Content $cfgVenv -Raw).Trim()
        Add-Result INFO "pyvenv.cfg" $cfgText
        if ($cfgText -match '(?m)^\s*home\s*=\s*(.+?)\s*$') {
            $venvHome = $Matches[1]
            if (Test-Path $venvHome) {
                Add-Result OK "Python ต้นทางของ venv ยังอยู่" $venvHome
            } else {
                Add-Result FAIL "venv ชี้ไป Python ที่ไม่มีอยู่แล้ว" $venvHome `
                    "สร้าง venv ใหม่ด้วย Python ที่มีจริง"
            }
        }
    }
} else {
    Add-Result FAIL "ไม่พบ python.exe ตาม processPath" $python
}

# ════════════════════════════════════════════════════════════════════ ชั้น 8
Write-Section "ชั้น 8 — ซอฟต์แวร์ความปลอดภัยที่อาจบล็อกการสร้าง process"

<#  ถ้า w3wp.exe ถูกนโยบาย (AppLocker / WDAC / EDR) ห้ามสร้างโปรเซสลูก
    อาการจะเหมือนเป๊ะกับที่พบ: 502.3 ทันที ไม่มีโปรเซส ไม่มี log และ
    "เปลี่ยนเป็น LocalSystem แล้วก็ยังพัง" เพราะนโยบายไม่ได้ดูที่สิทธิ์บัญชี #>

Invoke-Safe {
    $pol = Get-AppLockerPolicy -Effective -ErrorAction Stop
    $exeRules = @($pol.RuleCollections | Where-Object { $_.RuleCollectionType -eq "Exe" })
    if ($exeRules.Count -eq 0 -or $exeRules[0].Count -eq 0) {
        Add-Result OK "ไม่มีนโยบาย AppLocker สำหรับไฟล์ .exe"
    } else {
        Add-Result WARN "มีนโยบาย AppLocker สำหรับไฟล์ .exe" `
            ("จำนวนกฎ {0}, EnforcementMode = {1}" -f $exeRules[0].Count, $exeRules[0].EnforcementMode) `
            "ถ้า EnforcementMode=Enabled ให้เพิ่มข้อยกเว้นสำหรับ python.exe ใน venv"
    }
} "AppLocker"

Invoke-Safe {
    $ev = Get-WinEvent -LogName "Microsoft-Windows-AppLocker/EXE and DLL" -MaxEvents 20 -ErrorAction Stop |
          Where-Object { $_.TimeCreated -gt (Get-Date).AddHours(-3) }
    if ($ev) {
        foreach ($e in ($ev | Select-Object -First 5)) {
            Add-Result WARN "AppLocker event" ("{0} : {1}" -f $e.TimeCreated, ($e.Message -split "`r?`n")[0])
        }
    } else {
        Add-Result OK "ไม่มี AppLocker event ใน 3 ชั่วโมงที่ผ่านมา"
    }
} "AppLocker events"

Invoke-Safe {
    $mp = Get-MpComputerStatus -ErrorAction Stop
    Add-Result INFO "Microsoft Defender" ("RealTimeProtection = {0}" -f $mp.RealTimeProtectionEnabled)
    $pref = Get-MpPreference -ErrorAction Stop
    $asr = @($pref.AttackSurfaceReductionRules_Ids)
    if ($asr.Count -gt 0) {
        Add-Result WARN "มี Attack Surface Reduction rules เปิดอยู่" `
            ("จำนวน {0} กฎ" -f $asr.Count) `
            "ตรวจ Event log 'Microsoft-Windows-Windows Defender/Operational' ว่ามีการบล็อก python.exe หรือไม่"
    } else {
        Add-Result OK "ไม่มี ASR rules ที่ตั้งไว้"
    }
    $exc = @($pref.ExclusionPath)
    if ($exc -notcontains $Root) {
        Add-Result INFO "โฟลเดอร์โปรเจกต์ยังไม่อยู่ในรายการยกเว้นของ Defender" `
            "ถ้าสงสัยว่า Defender บล็อก ให้ลองเพิ่มชั่วคราว: Add-MpPreference -ExclusionPath '$Root'"
    }
} "Defender"

Invoke-Safe {
    $av = Get-CimInstance -Namespace "root\SecurityCenter2" -ClassName AntiVirusProduct -ErrorAction Stop
    foreach ($a in $av) { Add-Result INFO "โปรแกรมป้องกันไวรัสที่ติดตั้ง" $a.displayName }
} "รายชื่อ AV"

# ════════════════════════════════════════════════════════════════════ ชั้น 9
Write-Section "ชั้น 9 — Event log ย้อนหลัง 3 ชั่วโมง"

Invoke-Safe {
    $since = (Get-Date).AddHours(-3)
    $keys  = @("httpPlatform","w3wp","python","VisionIQ","WAS","IIS-W3SVC")
    $found = 0
    foreach ($log in @("Application","System")) {
        # Level 1=Critical 2=Error 3=Warning — กรองที่ต้นทางเพื่อไม่ให้ดึง event ทั้งหมดมาไล่เอง
        $evts = @(Get-WinEvent -FilterHashtable @{
                     LogName = $log; StartTime = $since; Level = @(1,2,3)
                  } -ErrorAction SilentlyContinue)
        foreach ($e in $evts) {
            $msg = ($e.Message -replace "`r?`n", " ")
            $hit = $false
            foreach ($k in $keys) { if ($msg -like "*$k*" -or $e.ProviderName -like "*$k*") { $hit = $true; break } }
            if ($hit) {
                $found++
                if ($found -le 10) {
                    Add-Result WARN "[$log] $($e.TimeCreated)" `
                        ("{0} : {1}" -f $e.ProviderName, $msg.Substring(0, [Math]::Min(220, $msg.Length)))
                }
            }
        }
    }
    if ($found -eq 0) { Add-Result OK "ไม่มี event ที่เกี่ยวข้องใน 3 ชั่วโมงที่ผ่านมา" }
    else { Add-Result INFO "จำนวน event ที่เกี่ยวข้องทั้งหมด" $found }
} "event log"

# ════════════════════════════════════════════════════════════════════ ชั้น 10
if (-not $SkipLiveTest) {
    Write-Section "ชั้น 10 — ทดสอบจริง: ให้ IIS เปิดโปรเซส Python"

    $logDir = if ($hp -and $hp.stdoutLogFile) { Split-Path $hp.stdoutLogFile -Parent } else { Join-Path $Root "logs" }
    $logPrefix = if ($hp -and $hp.stdoutLogFile) { Split-Path $hp.stdoutLogFile -Leaf } else { "iis-stdout" }

    $t0 = Get-Date

    Invoke-Safe { Restart-WebAppPool -Name $Pool } "recycle app pool"
    Start-Sleep -Seconds 2

    $status = ""
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 120 -ErrorAction Stop
        $status = "HTTP $($resp.StatusCode)"
        Add-Result OK "เว็บตอบกลับสำเร็จ" $status
    } catch {
        $status = $_.Exception.Message
        Add-Result FAIL "เว็บตอบกลับไม่สำเร็จ" $status
    }

    Start-Sleep -Seconds 3
    $after = @(Get-NewProcess -Name @("python","pythonw") -Since $t0)
    if ($after.Count -gt 0) {
        Add-Result OK "IIS เปิดโปรเซส Python ได้" `
            (($after | ForEach-Object { "PID $($_.Id) เริ่ม $($_.StartTime)" }) -join "`n")
    } else {
        Add-Result FAIL "IIS ไม่ได้เปิดโปรเซส Python เลย" `
            "ไม่มีโปรเซสใหม่หลังยิง request — แปลว่าล้มเหลวตั้งแต่ขั้น CreateProcess" `
            "ดูผลชั้น 11 เพื่อแยกว่า 'เปิด process ไม่ได้เลย' หรือ 'ติดที่ python'"
    }

    if (Test-Path $logDir) {
        $newLogs = @(Get-ChildItem $logDir -Filter "$logPrefix*" -ErrorAction SilentlyContinue |
                     Where-Object { $_.LastWriteTime -gt $t0 })
        if ($newLogs.Count -gt 0) {
            foreach ($f in $newLogs) {
                Add-Result OK "พบไฟล์ stdout log" ("{0} ({1} bytes)" -f $f.Name, $f.Length)
                $tail = Get-Content $f.FullName -Tail 40 -ErrorAction SilentlyContinue
                if ($tail) { Add-Result INFO "ท้ายไฟล์ log" (($tail) -join "`n") }
            }
        } else {
            Add-Result FAIL "ไม่มีไฟล์ stdout log เกิดขึ้นเลย" `
                "handler สร้างไฟล์นี้ตอนเปิดโปรเซส — ไม่มีไฟล์ = โปรเซสไม่เคยถูกเปิด"
        }
    }
} else {
    Write-Section "ชั้น 10 — ข้าม (-SkipLiveTest)"
}

# ════════════════════════════════════════════════════════════════════ ชั้น 11
if (-not $SkipDeepTest -and (Test-Path $webConfig)) {
    Write-Section "ชั้น 11 — ทดสอบชี้ขาด: ให้ IIS เปิด cmd.exe แทน python"

    Write-Line "  จุดประสงค์: แยกให้ขาดระหว่าง 2 สาเหตุที่อาการเหมือนกันทุกอย่าง" "DarkGray"
    Write-Line "    (ก) IIS เปิดโปรเซสลูกไม่ได้เลย  → ปัญหาระดับ handler/นโยบาย/พอร์ต" "DarkGray"
    Write-Line "    (ข) IIS เปิดได้ แต่ python ตาย   → ปัญหาที่ interpreter/venv/โค้ด" "DarkGray"
    Write-Line "  หมายเหตุ: รอบนี้ 'คาดว่าจะได้ 502' เป็นเรื่องปกติ เพราะ cmd ไม่ได้เปิดพอร์ตรอ" "DarkGray"
    Write-Line "            สิ่งที่ดูคือ 'มีโปรเซส cmd/PING เกิดขึ้นหรือไม่' เท่านั้น" "DarkGray"

    $backup   = Join-Path $Root "web.config.diagbak"
    $modified = $false      # แก้ไฟล์จริงแล้วหรือยัง — ตัวชี้ว่าต้องคืนค่าหรือไม่
    $restored = $false
    try {
        Copy-Item $webConfig $backup -Force
        Add-Result INFO "สำรอง web.config ไว้แล้ว" $backup

        $testXml = @'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <handlers>
      <remove name="httpplatformhandler" />
      <add name="httpplatformhandler" path="*" verb="*" modules="httpPlatformHandler"
           resourceType="Unspecified" requireAccess="Script" />
    </handlers>
    <httpPlatform processPath="C:\Windows\System32\cmd.exe"
                  arguments="/c ping -n 600 127.0.0.1"
                  stdoutLogEnabled="false"
                  startupTimeLimit="20" />
  </system.webServer>
</configuration>
'@
        [IO.File]::WriteAllText($webConfig, $testXml, (New-Object System.Text.UTF8Encoding($false)))
        $modified = $true

        $t1 = Get-Date
        Invoke-Safe { Restart-WebAppPool -Name $Pool } "recycle (deep test)"
        Start-Sleep -Seconds 2
        try { Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 45 -ErrorAction Stop | Out-Null }
        catch { Write-Line "        (ได้ error ตามคาด: $($_.Exception.Message))" "DarkGray" }
        Start-Sleep -Seconds 3

        $spawned = @(Get-NewProcess -Name @("cmd","PING") -Since $t1)
        if ($spawned.Count -gt 0) {
            Add-Result OK "IIS เปิดโปรเซสลูกได้ (เห็น cmd/PING)" `
                (($spawned | ForEach-Object { "$($_.ProcessName) PID $($_.Id)" }) -join "`n") `
                "สรุป: กลไก spawn ปกติ → ปัญหาอยู่ที่ Python/venv/โค้ด ให้ดูชั้น 7 และไฟล์ stdout log"
        } else {
            Add-Result FAIL "IIS เปิดโปรเซสลูกไม่ได้เลย แม้แต่ cmd.exe" `
                "ตัดความเป็นไปได้เรื่อง Python/venv ออกทั้งหมด" `
                "สาเหตุที่เหลือ: HttpPlatformHandler เสียหาย / นโยบายบล็อกการสร้างโปรเซส (ชั้น 8) / จองพอร์ตไม่ได้ (ชั้น 6) → พิจารณาเปลี่ยนไปใช้แผน B (รันเป็น Windows Service แล้วให้ IIS ทำ reverse proxy)"
        }
    }
    finally {
        if (-not $modified) {
            # ยังไม่ได้แตะไฟล์จริง (ล้มก่อนถึงขั้นเขียน) — ลบไฟล์สำรองทิ้งพอ
            Remove-Item $backup -Force -ErrorAction SilentlyContinue
            Add-Result INFO "ไม่ได้แก้ web.config (ล้มก่อนถึงขั้นเขียน)"
        }
        elseif (Test-Path $backup) {
            try {
                Copy-Item $backup $webConfig -Force
                Remove-Item $backup -Force -ErrorAction SilentlyContinue
                $restored = $true
                Invoke-Safe { Restart-WebAppPool -Name $Pool } "recycle (restore)"
                Add-Result OK "คืนค่า web.config เดิมเรียบร้อย"
            } catch {
                Add-Result FAIL "คืนค่า web.config ไม่สำเร็จ" $_.Exception.Message `
                    "คัดลอกกลับเองด้วย: Copy-Item '$backup' '$webConfig' -Force"
            }
        }
        else {
            Add-Result FAIL "ไฟล์สำรองหายไป — web.config ยังเป็นตัวทดสอบอยู่!" "" `
                "สร้าง web.config ใหม่จาก deploy\web.config.example ทันที"
        }
    }
} else {
    Write-Section "ชั้น 11 — ข้าม (-SkipDeepTest หรือไม่พบ web.config)"
}

# ════════════════════════════════════════════════════════════════════ สรุป
Write-Section "สรุปผล"

$fails = @($script:Findings | Where-Object { $_.Status -eq "FAIL" })
$warns = @($script:Findings | Where-Object { $_.Status -eq "WARN" })

Write-Line ("  OK   : {0}" -f @($script:Findings | Where-Object { $_.Status -eq "OK" }).Count) "Green"
Write-Line ("  WARN : {0}" -f $warns.Count) "Yellow"
Write-Line ("  FAIL : {0}" -f $fails.Count) "Red"

if ($fails.Count -gt 0) {
    Write-Line ""
    Write-Line "  รายการที่ต้องแก้ (เรียงตามลำดับที่ควรลงมือ):" "Red"
    $i = 1
    foreach ($f in $fails) {
        Write-Line ("   {0}. {1}" -f $i, $f.Name) "Red"
        if ($f.Fix) { Write-Line ("      → {0}" -f $f.Fix) "White" }
        $i++
    }
}

if ($warns.Count -gt 0) {
    Write-Line ""
    Write-Line "  รายการที่ควรดู (ยังไม่ทำให้พัง แต่มีผลภายหลัง):" "Yellow"
    foreach ($w in $warns) { Write-Line ("   • {0}" -f $w.Name) "Yellow" }
}

if ($fails.Count -eq 0) {
    Write-Line ""
    Write-Line "  ไม่พบข้อผิดพลาดที่ตรวจจับได้ — ถ้าเว็บยังไม่ทำงาน ให้ส่งผลทั้งหมดนี้ไปวิเคราะห์ต่อ" "Green"
}

Write-Line ""
Write-Line ("เสร็จสิ้น {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) "White"

if ($OutFile) {
    try {
        [IO.File]::WriteAllLines($OutFile, [string[]]$script:Lines,
                                 (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "`nเขียนผลลงไฟล์แล้ว: $OutFile" -ForegroundColor Cyan
    } catch {
        Write-Host "`nเขียนไฟล์ไม่สำเร็จ: $($_.Exception.Message)" -ForegroundColor Red
    }
}
