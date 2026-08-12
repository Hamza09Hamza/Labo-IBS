[CmdletBinding()]
param(
    [ValidatePattern('^(?:\d{1,3}\.){3}\d{1,3}$')]
    [string]$TargetIP = '10.10.12.52',

    [ValidateRange(1, 65535)]
    [int]$SelectraPort = 6003,

    [ValidateRange(0, 86400)]
    [int]$DurationSeconds = 0,

    [ValidateRange(64, 4096)]
    [int]$FileSizeMB = 512,

    [switch]$StopOnly,

    [switch]$CaptureAll
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$MarkerPath = Join-Path $ScriptRoot '.selectra_pktmon_active.json'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Start-ElevatedCopy {
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', ('"{0}"' -f $PSCommandPath),
        '-TargetIP', $TargetIP,
        '-SelectraPort', [string]$SelectraPort,
        '-DurationSeconds', [string]$DurationSeconds,
        '-FileSizeMB', [string]$FileSizeMB
    )
    if ($StopOnly) {
        $arguments += '-StopOnly'
    }
    if ($CaptureAll) {
        $arguments += '-CaptureAll'
    }
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments
}

function Stop-OurCapture {
    param([switch]$KeepMarker)

    Write-Host 'Stopping Windows Packet Monitor...' -ForegroundColor Yellow
    & pktmon.exe stop 2>&1 | ForEach-Object { Write-Host $_ }
    $stopCode = $LASTEXITCODE
    & pktmon.exe filter remove 2>&1 | ForEach-Object { Write-Host $_ }
    if (-not $KeepMarker -and (Test-Path -LiteralPath $MarkerPath)) {
        Remove-Item -LiteralPath $MarkerPath -Force
    }
    return $stopCode
}

if (-not (Test-IsAdministrator)) {
    Write-Host 'Administrator permission is required. Opening the Windows confirmation prompt...' -ForegroundColor Yellow
    Start-ElevatedCopy
    exit 0
}

if (-not (Get-Command pktmon.exe -ErrorAction SilentlyContinue)) {
    throw 'pktmon.exe is unavailable. This requires Windows 10/11 or Windows Server 2016 or newer.'
}

if ($StopOnly) {
    if (-not (Test-Path -LiteralPath $MarkerPath)) {
        Write-Host 'No Selectra capture marker exists. Nothing was stopped.' -ForegroundColor Yellow
        Write-Host 'This guard prevents stopping another administrator''s unrelated PktMon session.'
        Read-Host 'Press Enter to close'
        exit 1
    }
    Stop-OurCapture | Out-Null
    Write-Host 'The Selectra packet capture was stopped and its filter was removed.' -ForegroundColor Green
    Read-Host 'Press Enter to close'
    exit 0
}

$parsedAddress = $null
if (-not [Net.IPAddress]::TryParse($TargetIP, [ref]$parsedAddress) -or
    $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
    throw "Invalid IPv4 address: $TargetIP"
}

if (Test-Path -LiteralPath $MarkerPath) {
    throw "A previous Selectra capture marker still exists at $MarkerPath. Run STOP_SELECTRA_CAPTURE.bat first."
}

$statusText = (& pktmon.exe status 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "Could not query PktMon status:`r`n$statusText"
}
if ($statusText -match '(?im)^\s*Status\s*:\s*(Running|Started)\s*$') {
    throw 'PktMon is already recording another capture. Stop that capture before starting this one.'
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$safeIP = $TargetIP.Replace('.', '-')
$captureLabel = if ($CaptureAll) { 'all' } else { $safeIP }
$outputDirectory = Join-Path $ScriptRoot "selectra_capture_${captureLabel}_$stamp"
$etlPath = Join-Path $outputDirectory "selectra_${captureLabel}.etl"
$metadataPath = Join-Path $outputDirectory 'metadata.txt'
$statusPath = Join-Path $outputDirectory 'pktmon_status.txt'
$filtersBeforePath = Join-Path $outputDirectory 'pktmon_filters_before.txt'

New-Item -ItemType Directory -Path $outputDirectory -ErrorAction Stop | Out-Null

@(
    'Selectra Windows full-packet capture'
    "Capture started UTC: $((Get-Date).ToUniversalTime().ToString('o'))"
    "Target IP: $TargetIP"
    "LaboBridge Selectra port: $SelectraPort"
    $(if ($CaptureAll) {
        'Filters: NONE - every packet on every captured component (use only when the suspected IP/port filter might be wrong)'
    } else {
        'Filters: suspected IP OR any TCP traffic on the Selectra listener port'
    })
    'Network components: all (physical NIC, TCP/IP stack, and Hyper-V paths)'
    'Packet length: full packet (--pkt-size 0)'
    "Server: $env:COMPUTERNAME"
    "Windows: $([Environment]::OSVersion.VersionString)"
    "PowerShell: $($PSVersionTable.PSVersion)"
    "Maximum ETL segment: $FileSizeMB MB"
    ''
    '[IPv4 addresses]'
    (Get-NetIPAddress -AddressFamily IPv4 | Format-Table InterfaceAlias,IPAddress,PrefixLength -AutoSize | Out-String)
    '[IPv4 routes]'
    (Get-NetRoute -AddressFamily IPv4 | Sort-Object RouteMetric | Format-Table InterfaceAlias,DestinationPrefix,NextHop,RouteMetric -AutoSize | Out-String)
) | Set-Content -LiteralPath $metadataPath -Encoding UTF8

& pktmon.exe filter list 2>&1 | Set-Content -LiteralPath $filtersBeforePath -Encoding UTF8

$captureStarted = $false
try {
    # Separate PktMon filters are OR conditions. This catches traffic using
    # either the suspected Selectra-PC IP or the configured LaboBridge port,
    # even when the real source IP differs from the assumption. -CaptureAll
    # skips both filters entirely - useful when we don't trust the IP/port
    # assumption at all (e.g. a reply landing somewhere unexpected).
    & pktmon.exe filter remove 2>&1 | Add-Content -LiteralPath $statusPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not clear inactive PktMon filters.'
    }

    if (-not $CaptureAll) {
        & pktmon.exe filter add SelectraSuspectedIP -i $TargetIP 2>&1 | Add-Content -LiteralPath $statusPath
        if ($LASTEXITCODE -ne 0) {
            throw "Could not add the PktMon IP filter for $TargetIP."
        }

        & pktmon.exe filter add LaboBridgeSelectraPort -t TCP -p $SelectraPort 2>&1 | Add-Content -LiteralPath $statusPath
        if ($LASTEXITCODE -ne 0) {
            throw "Could not add the PktMon TCP-port filter for $SelectraPort."
        }
    }

    $marker = @{
        target_ip = $TargetIP
        capture_all = [bool]$CaptureAll
        output_directory = $outputDirectory
        etl_path = $etlPath
        started_utc = (Get-Date).ToUniversalTime().ToString('o')
    }
    $marker | ConvertTo-Json | Set-Content -LiteralPath $MarkerPath -Encoding UTF8

    Write-Host ''
    Write-Host 'Starting full-packet capture on the Windows server...' -ForegroundColor Cyan
    if ($CaptureAll) {
        Write-Host 'Targets: EVERYTHING - no IP or port filter' -ForegroundColor Yellow
    } else {
        Write-Host "Targets: IP $TargetIP OR TCP port $SelectraPort, both directions"
    }
    Write-Host 'Capture points: every Windows network component, including Hyper-V'
    Write-Host 'This does not bind a port and does not stop or restart run_all.' -ForegroundColor Green

    & pktmon.exe start --capture --comp all --pkt-size 0 --file-name $etlPath --file-size $FileSizeMB --log-mode multi-file 2>&1 |
        Add-Content -LiteralPath $statusPath
    if ($LASTEXITCODE -ne 0) {
        throw 'PktMon could not start. See pktmon_status.txt in the new capture directory.'
    }
    $captureStarted = $true

    & pktmon.exe status --buffer-info 2>&1 | Add-Content -LiteralPath $statusPath -Encoding UTF8
    Write-Host ''
    Write-Host 'CAPTURE ACTIVE' -ForegroundColor Black -BackgroundColor Green
    Write-Host 'Have the operator perform the complete Selectra action now.'

    if ($DurationSeconds -gt 0) {
        Write-Host "The capture will stop automatically after $DurationSeconds seconds."
        Start-Sleep -Seconds $DurationSeconds
    } else {
        Read-Host 'After the Selectra workflow is fully finished, press Enter to stop'
    }
}
finally {
    if ($captureStarted -or (Test-Path -LiteralPath $MarkerPath)) {
        Stop-OurCapture | Out-Null
    }
}

Write-Host ''
Write-Host 'Converting the native ETL capture into PCAPNG and readable text...' -ForegroundColor Cyan
$etlFiles = @(Get-ChildItem -LiteralPath $outputDirectory -Filter '*.etl' | Sort-Object Name)
if ($etlFiles.Count -eq 0) {
    throw "No ETL capture was produced in $outputDirectory"
}

foreach ($etlFile in $etlFiles) {
    $baseName = [IO.Path]::GetFileNameWithoutExtension($etlFile.Name)
    $pcapPath = Join-Path $outputDirectory "$baseName.pcapng"
    $textPath = Join-Path $outputDirectory "$baseName.txt"

    & pktmon.exe etl2pcap $etlFile.FullName --out $pcapPath 2>&1 |
        Add-Content -LiteralPath $statusPath
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not convert $($etlFile.Name) to PCAPNG. Keep the original ETL."
    }

    & pktmon.exe etl2txt $etlFile.FullName --out $textPath --verbose 3 --hex 2>&1 |
        Add-Content -LiteralPath $statusPath
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not convert $($etlFile.Name) to readable text. Keep the original ETL."
    }
}

@(
    ''
    "Capture stopped UTC: $((Get-Date).ToUniversalTime().ToString('o'))"
    "ETL segments: $($etlFiles.Count)"
) | Add-Content -LiteralPath $metadataPath -Encoding UTF8

$hashableFiles = @(Get-ChildItem -LiteralPath $outputDirectory -File | Where-Object Name -ne 'SHA256SUMS.txt')
$hashLines = foreach ($file in $hashableFiles) {
    $hash = Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $($file.Name)"
}
$hashLines | Set-Content -LiteralPath (Join-Path $outputDirectory 'SHA256SUMS.txt') -Encoding ASCII

$archivePath = "$outputDirectory.zip"
Compress-Archive -Path (Join-Path $outputDirectory '*') -DestinationPath $archivePath -CompressionLevel Optimal -Force

Write-Host ''
Write-Host 'CAPTURE COMPLETE' -ForegroundColor Black -BackgroundColor Green
Write-Host "Archive to bring back: $archivePath"
Write-Host 'Keep the ETL and PCAPNG files; they contain the authoritative packet data.'
Write-Host ''
Write-Warning 'The archive may contain patient identifiers, results, or credentials. Transfer it securely.'
Read-Host 'Press Enter to close'
