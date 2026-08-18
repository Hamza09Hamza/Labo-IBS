# Generic analyzer TCP/UDP capture — Windows server

This separate diagnostic tool discovers how an analyzer communicates with the Windows server. It captures full TCP and UDP packets without opening or binding any port, so it can run beside `run_all.py` and the NSSM `LaboBridge` service.

## Run it

1. Pull the repository on the Windows server.
2. Double-click `analyzer_packet_capture\START_ANALYZER_CAPTURE.bat`.
3. Accept the Administrator/UAC prompt.
4. Enter the analyzer's IPv4 address. If the address is unknown, leave it blank to capture every TCP and UDP flow visible to the server.
5. Wait for the green `CAPTURE ACTIVE` message.
6. Have the operator perform the complete analyzer workflow: identify the sample, request/load work, start it, validate it, and transmit the result.
7. Return to the capture window and press **Enter**.
8. Wait for `CAPTURE COMPLETE` and bring back the generated `analyzer_capture_...zip` file.

Entering an IP is strongly preferred because an unfiltered TCP/UDP capture can become large and will include unrelated server traffic.

## What it produces

- `.etl`: authoritative native Windows PktMon capture.
- `.pcapng`: Wireshark-readable packets.
- `.txt`: verbose readable report with packet hex payloads.
- `metadata.txt`: interfaces, routes, target and timestamps.
- `pktmon_status.txt`: PktMon filters and status.
- `SHA256SUMS.txt`: integrity hashes.
- One ZIP containing the complete capture folder.

If the capture window closes unexpectedly, run `STOP_ANALYZER_CAPTURE.bat`. Its marker guard prevents it from stopping an unrelated PktMon recording.

## Optional PowerShell usage

Target one analyzer for 30 minutes:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\analyzer_packet_capture\capture_analyzer_windows.ps1 -TargetIP 172.16.2.50 -DurationSeconds 1800
```

Capture every TCP and UDP flow visible to the server:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\analyzer_packet_capture\capture_analyzer_windows.ps1 -AllTcpUdp
```

## Visibility limitation

The server can capture traffic addressed to/from itself and broadcast traffic it receives. On a switched network it cannot normally see a conversation between an analyzer and an unrelated third computer. Capturing that requires running the kit on one endpoint or configuring a switch SPAN/mirror port.

The output can contain patient identifiers, results, orders, or credentials. Transfer and retain it securely.
