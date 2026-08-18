[CmdletBinding()]
param(
    [string]$TargetIP = '',

    [switch]$AllTcpUdp,

    [ValidateRange(0, 86400)]
    [int]$DurationSeconds = 0,

    [ValidateRange(64, 4096)]
    [int]$FileSizeMB = 512,

    [switch]$StopOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$MarkerPath = Join-Path $ScriptRoot '.analyzer_pktmon_active.json'

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
        '-DurationSeconds', [string]$DurationSeconds,
        '-FileSizeMB', [string]$FileSizeMB
    )
    if ($TargetIP) {
        $arguments += @('-TargetIP', $TargetIP)
    }
    if ($AllTcpUdp) {
        $arguments += '-AllTcpUdp'
    }
    if ($StopOnly) {
        $arguments += '-StopOnly'
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
    throw 'pktmon.exe is unavailable. Windows 10/11 or Windows Server 2016 or newer is required.'
}

if ($StopOnly) {
    if (-not (Test-Path -LiteralPath $MarkerPath)) {
        Write-Host 'No analyzer capture marker exists. Nothing was stopped.' -ForegroundColor Yellow
        Write-Host 'This guard prevents stopping an unrelated PktMon session.'
        Read-Host 'Press Enter to close'
        exit 1
    }
    Stop-OurCapture | Out-Null
    Write-Host 'The analyzer capture was stopped and its filters were removed.' -ForegroundColor Green
    Read-Host 'Press Enter to close'
    exit 0
}

if ($TargetIP -and $AllTcpUdp) {
    throw 'Choose either -TargetIP or -AllTcpUdp, not both.'
}
if (-not $TargetIP -and -not $AllTcpUdp) {
    throw 'Provide -TargetIP or use -AllTcpUdp.'
}

if ($TargetIP) {
    $parsedAddress = $null
    if (-not [Net.IPAddress]::TryParse($TargetIP, [ref]$parsedAddress) -or
        $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "Invalid IPv4 address: $TargetIP"
    }
}

if (Test-Path -LiteralPath $MarkerPath) {
    throw "A previous analyzer capture marker still exists at $MarkerPath. Run STOP_ANALYZER_CAPTURE.bat first."
}

$statusText = (& pktmon.exe status 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    throw "Could not query PktMon status:`r`n$statusText"
}
if ($statusText -match '(?im)^\s*Status\s*:\s*(Running|Started)\s*$') {
    throw 'PktMon is already recording another capture. Stop that capture before starting this one.'
}

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$captureLabel = if ($AllTcpUdp) { 'all-tcp-udp' } else { $TargetIP.Replace('.', '-') }
$outputDirectory = Join-Path $ScriptRoot "analyzer_capture_${captureLabel}_$stamp"
$etlPath = Join-Path $outputDirectory "analyzer_${captureLabel}.etl"
$metadataPath = Join-Path $outputDirectory 'metadata.txt'
$statusPath = Join-Path $outputDirectory 'pktmon_status.txt'
$filtersBeforePath = Join-Path $outputDirectory 'pktmon_filters_before.txt'

New-Item -ItemType Directory -Path $outputDirectory -ErrorAction Stop | Out-Null

@(
    'Generic analyzer TCP/UDP packet capture'
    "Capture started UTC: $((Get-Date).ToUniversalTime().ToString('o'))"
    $(if ($AllTcpUdp) {
        'Target: every TCP and UDP flow visible to this Windows server'
    } else {
        "Target: TCP and UDP traffic to or from $TargetIP"
    })
    'Directions: inbound and outbound'
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
    & pktmon.exe filter remove 2>&1 | Add-Content -LiteralPath $statusPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not clear inactive PktMon filters.'
    }

    if ($AllTcpUdp) {
        & pktmon.exe filter add AllTCP -t TCP 2>&1 | Add-Content -LiteralPath $statusPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not add the all-TCP filter.' }
        & pktmon.exe filter add AllUDP -t UDP 2>&1 | Add-Content -LiteralPath $statusPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not add the all-UDP filter.' }
    } else {
        # Conditions inside one PktMon filter are combined, while separate
        # filters are alternatives. These two filters mean:
        # (target IP AND TCP) OR (target IP AND UDP).
        & pktmon.exe filter add AnalyzerTCP -i $TargetIP -t TCP 2>&1 | Add-Content -LiteralPath $statusPath
        if ($LASTEXITCODE -ne 0) { throw "Could not add the TCP filter for $TargetIP." }
        & pktmon.exe filter add AnalyzerUDP -i $TargetIP -t UDP 2>&1 | Add-Content -LiteralPath $statusPath
        if ($LASTEXITCODE -ne 0) { throw "Could not add the UDP filter for $TargetIP." }
    }

    @{
        target_ip = $TargetIP
        all_tcp_udp = [bool]$AllTcpUdp
        output_directory = $outputDirectory
        etl_path = $etlPath
        started_utc = (Get-Date).ToUniversalTime().ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $MarkerPath -Encoding UTF8

    Write-Host ''
    Write-Host 'Starting analyzer TCP/UDP packet capture...' -ForegroundColor Cyan
    if ($AllTcpUdp) {
        Write-Host 'Target: ALL TCP and UDP visible to this server' -ForegroundColor Yellow
    } else {
        Write-Host "Target: TCP and UDP to/from $TargetIP"
    }
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
    Write-Host 'Have the operator perform the complete analyzer workflow now.'
    if ($DurationSeconds -gt 0) {
        Write-Host "The capture will stop automatically after $DurationSeconds seconds."
        Start-Sleep -Seconds $DurationSeconds
    } else {
        Read-Host 'After the workflow is fully finished, press Enter to stop'
    }
}
finally {
    if ($captureStarted -or (Test-Path -LiteralPath $MarkerPath)) {
        Stop-OurCapture | Out-Null
    }
}

Write-Host ''
Write-Host 'Converting ETL into PCAPNG and readable hex text...' -ForegroundColor Cyan
$etlFiles = @(Get-ChildItem -LiteralPath $outputDirectory -Filter '*.etl' | Sort-Object Name)
if ($etlFiles.Count -eq 0) {
    throw "No ETL capture was produced in $outputDirectory"
}

foreach ($etlFile in $etlFiles) {
    $baseName = [IO.Path]::GetFileNameWithoutExtension($etlFile.Name)
    & pktmon.exe etl2pcap $etlFile.FullName --out (Join-Path $outputDirectory "$baseName.pcapng") 2>&1 |
        Add-Content -LiteralPath $statusPath
    if ($LASTEXITCODE -ne 0) { Write-Warning "Could not convert $($etlFile.Name) to PCAPNG. Keep the ETL." }

    & pktmon.exe etl2txt $etlFile.FullName --out (Join-Path $outputDirectory "$baseName.txt") --verbose 3 --hex 2>&1 |
        Add-Content -LiteralPath $statusPath
    if ($LASTEXITCODE -ne 0) { Write-Warning "Could not convert $($etlFile.Name) to text. Keep the ETL." }
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
Write-Warning 'The archive may contain patient identifiers, results, or credentials. Transfer it securely.'
Read-Host 'Press Enter to close'
