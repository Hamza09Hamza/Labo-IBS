# Packet capture and protocol discovery

## Choose the correct tool

| Tool | Use | Binds a port? | Can run beside `run_all`? |
|---|---|---:|---:|
| `analyzer_packet_capture/` | Sniff target TCP and UDP or all visible TCP/UDP on Windows | No | Yes |
| `selectra_packet_capture/` | Focused historical Selectra/6003 investigation | No | Yes |
| `capture_listener.py` | Become the receiving endpoint and store raw bytes | Yes | Only on unused ports |
| analyzer-specific capture scripts | Controlled protocol discovery | Usually yes | Check port conflicts first |

## Generic Windows TCP/UDP capture

Run:

```powershell
.\analyzer_packet_capture\START_ANALYZER_CAPTURE.bat
```

Enter the analyzer IP whenever known. The script applies two filters:

```text
(target IP AND TCP) OR (target IP AND UDP)
```

Leaving the IP blank captures every TCP and UDP flow visible to the server and can generate large, sensitive output. Keep that window short.

The output ZIP contains:

- ETL: authoritative PktMon capture and Windows component metadata;
- PCAPNG: Wireshark-readable packets;
- TXT: verbose packet/hex report;
- metadata and routes;
- PktMon status/filter record;
- SHA-256 hashes.

## Capture procedure

1. Record analyzer model, firmware/software version, IP, middleware, and time.
2. Start capture and confirm `CAPTURE ACTIVE`.
3. Use a distinctive non-production sample ID.
4. Perform the complete operator workflow, including query/load, test start, validation, and result transmission.
5. Wait briefly for acknowledgements and disconnects.
6. Stop capture normally and wait for conversion/ZIP completion.
7. Copy the entire ZIP securely; do not paste only the readable text if PCAPNG/ETL are available.
8. Correlate timestamps with operator actions.

## What the server cannot see

On a switched network, the bridge normally sees traffic addressed to/from itself and broadcasts delivered to it. It cannot see analyzer-to-third-party unicast unless capture runs on an endpoint or the switch mirrors that traffic to the bridge.

## Serial-to-Ethernet adapters

An adapter preserves or wraps serial bytes in TCP/UDP; it does not upgrade the analyzer protocol. Capture both sides when possible:

- serial settings: baud, bits, parity, stop bits, flow control;
- adapter mode: TCP client/server or UDP;
- local/remote IP and port;
- packetization timeout and keepalive;
- analyzer application handshake.

## Evidence handling

Raw captures may contain patient names, identifiers, results, tokens, or credentials. They are ignored by Git and must remain in access-controlled storage. Redact only copies; preserve the original evidence securely.
