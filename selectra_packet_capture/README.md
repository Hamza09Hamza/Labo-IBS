# Selectra traffic capture kit — Windows server

This is designed for the Windows server at `172.16.2.4` that already hosts `run_all` and the interface on port `5050`. It records complete packets matching either the suspected Selectra Windows host `10.10.12.52` or any TCP connection using the LaboBridge Selectra port `6003`, in both directions and across physical, TCP/IP, and Hyper-V network components.

It uses Windows' built-in Packet Monitor. It does not open a listening port, replace the existing service, restart `run_all`, or change port `5050`.

PktMon itself is a machine-wide diagnostic facility. The script refuses to start when it detects another active PktMon recording. It records the previous inactive filter listing in `pktmon_filters_before.txt`, then temporarily replaces those filters with the exact Selectra IP filter.

## Run on the Windows server

Copy this whole folder onto the server. Then:

1. Double-click `START_SELECTRA_CAPTURE.bat`.
2. Accept the Windows Administrator/UAC prompt.
3. Wait until the green `CAPTURE ACTIVE` message appears.
4. Have the laboratory operator complete the Selectra workflow.
5. Return to the capture window and press **Enter**.
6. Wait for `CAPTURE COMPLETE`.
7. Bring back the generated `selectra_capture_10-10-12-52_<timestamp>.zip`.

If the capture window is accidentally closed while recording, double-click `STOP_SELECTRA_CAPTURE.bat`. Its marker guard prevents it from stopping an unrelated PktMon capture.

The ZIP contains:

- `.etl` — original Windows PktMon capture, including Windows packet/drop metadata.
- `.pcapng` — Wireshark-readable packets.
- `.txt` — verbose packet report including hex payload.
- `pktmon_status.txt` — capture state and buffer information.
- `metadata.txt` — capture configuration and server network context.
- `SHA256SUMS.txt` — integrity hashes.

Keep both ETL and PCAPNG files. Microsoft notes that conversion to PCAPNG does not preserve all PktMon component and drop-report metadata.

## PowerShell options

The BAT file uses the correct defaults. An administrator may alternatively run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\capture_selectra_windows.ps1
```

For an automatic 30-minute capture:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\capture_selectra_windows.ps1 -DurationSeconds 1800
```

## Unfiltered capture (everything, no IP/port assumption)

Double-click `START_CAPTURE_ALL.bat`, or run:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\capture_selectra_windows.ps1 -CaptureAll
```

Skips the `10.10.12.52` IP filter and the `6003` port filter entirely -
records every packet the server's PktMon can see, on every component.
Use this when the suspected IP or port might be wrong (e.g. a Host Query
reply going somewhere unexpected), not as the default - it produces much
larger files and more traffic to sift through. Output goes to
`selectra_capture_all_<timestamp>/` instead of the IP-named folder.

## What “everything” means on a switched network

The PktMon filter matches `10.10.12.52` as either source or destination. NIC capture and `--pkt-size 0` retain full packets. No TCP/UDP port or IP protocol is excluded.

The server cannot see unrelated traffic that the network switch sends only to another port. Therefore:

- Traffic between `10.10.12.52` and server `172.16.2.4` will be captured.
- Traffic broadcast to the server may be captured.
- Traffic between `10.10.12.52` and a third machine will require a switch SPAN/mirror port feeding the server, or a capture running on the Windows machine.
- Encrypted traffic is captured in full, but its application payload cannot be read without the relevant session keys.

## Security and privacy

The capture may contain patient identifiers, test orders/results, authentication tokens, or other confidential traffic. Output inherits the Windows permissions of the folder where the kit is copied. Put it in an administrator-controlled location, use an approved secure transfer method, and delete extra copies when the analysis is complete.
