# Analyzer integration and evidence matrix

This document prevents “code exists” from being mistaken for “the real instrument is proven.”

## Evidence labels

- **Captured:** observed in real traffic from the deployed environment.
- **Field-confirmed:** operator/analyzer behavior confirmed the payload was accepted and used.
- **Implemented:** production code exists.
- **Tested:** automated tests verify our side without the physical analyzer.
- **Documented:** supported by a vendor host-interface document.
- **Unknown:** requires capture, configuration inspection, or operator confirmation.

## Matrix

| Analyzer | Results | Order download | Current evidence and limits |
|---|---|---|---|
| Sysmex XN-330 | Implemented; ASTM decoder | Not implemented | Receive-only on port 6001. Experimental Host Query code was removed; result mappings and 5050 administration remain. |
| I-Smart 30 PRO | Captured and implemented | Not implemented | ASTM upload with initial ACK. Retransmission and disabled-Ca2+ behavior came from real captures. |
| Selectra | Captured and implemented | Implemented; tests selection field-confirmed | Real Q records, ASTM ACKs, H/P/O/L delivery, transport ACK and application rejection were captured. Requested tests were reported as appearing on the analyzer. Demographic field placement remains less certain than test selection. |
| CYANVision | Captured and implemented | Implemented from host manual; ProgramID trial awaiting field confirmation | Results use HL7/MLLP. Worklist uses QRY^Q02, DSR^Q03 and ACK^Q03. DSP.8 now uses numeric ProgramID values observed in result NTE.8 metadata. |
| Sysmex XS-500i | Captured results; currently reuses XN decoder | Vendor-documented capability, not implemented | XS-Series supports real-time Sample-ID inquiry over IPU serial/TCP. The deployed IPU format and query settings must be inspected and captured before code is added. |
| Mini VIDAS | Captured and implemented | Not supported by direct interface | The direct Mini VIDAS computer interface is upload-only. The serial-to-Ethernet adapter changes transport, not analyzer capability. |

## Sysmex XN-330

### Result side

- Machine key: `xn330`
- Default port: `6001`
- Protocol: ASTM E1381/E1394 style
- Decoder: `labo_bridge/decoders/xn330.py`

The decoder recognizes CBC, platelet, differential, histogram/scattergram references, and suspect flags. Graphic records contain filenames/references, not necessarily image bytes.

### Order-download boundary

No XN-330 Host Query service is registered. Port `6001` receives and decodes
results only. Do not add XN staging or arming controls back to port `5052`
without a new confirmed operational requirement.

## Selectra

- Machine key: `selectra`
- Default port: `6003`
- Result decoder: `labo_bridge/decoders/selectra.py`
- Host Query: `selectra_host_query/server.py`

The deployed analyzer sends LIS2-A Q records. The bridge performs exact-ID matching and returns H/P/O/L. A transport ACK only proves frame receipt. Selectra may later send an O record with O-26 `X`, which is an application-level rejection and must override any optimistic interpretation of the earlier ACK.

The safest proven payload is minimal: exact sample ID plus installed analyzer test codes. Optional demographic/specimen fields are controlled from port 5052 because incorrectly populated fields caused application rejection during development.

## CYANVision

- Machine key: `cyanvision`
- Default port: `6004`
- Protocol: HL7 v2 over MLLP
- Result decoder: `labo_bridge/decoders/cyanvision.py`
- Worklist service: `cyanvision_worklist/`

The worklist is analyzer-pulled. The operator opens Patient Worklist and selects Load from LIS. The bridge answers a QRY with one or more DSR messages and tracks ACK^Q03. One sample/test representation is the conservative format. Result codes are translated through a separate outbound ProgramID table. ALP `3`, CRE `11`, and LIPASE `23` came from real NTE.8 metadata; their use in DSP.8 is still a controlled trial pending analyzer-screen confirmation.

## XS-500i

- Machine key: `xs500i`
- Default port: `6005`
- Current decoder: reused XN decoder
- Physical path: analyzer -> IPU/Windows PC -> bridge

Real result captures showed compatible Sysmex test codes. The XS-Series host specification documents real-time inquiry by Sample ID and response with patient/order information over RS-232 or TCP/IP through the IPU. Do not implement it by copying the XN service blindly: first capture the deployed IPU's selected format (`XS`, `ASTM`, or compatibility mode), Class A/B setting, and query frame.

## Mini VIDAS

- Machine key: `minividas`
- Default port: `6006`
- Path: Mini VIDAS serial -> FCT-201-F serial/Ethernet adapter -> TCP bridge

Observed result bodies use tags such as `mt`, `pi`, `pn`, `si`, `ci`, `rt`, `rn`, `tt`, `td`, `ql`, `qn`, and `qd`. The important limitation is architectural: the Mini VIDAS manual states that its direct interface only uploads results and that the download portion of the interface specification does not apply. Sending bytes back through the converter cannot add Host Query support.

## I-Smart

- Machine key: `ismart`
- Default port: `6002`
- Protocol: ASTM with initial acknowledgement

The code skips only narrowly confirmed cases: `status=R` retransmissions and the exact disabled `Ca2+` placeholder. Do not generalize those rules to other analyzers or values.

## WATO and other discovery targets

WATO discovery utilities are separate from the six default laboratory listeners. WATO HL7 typically uses MLLP, while serial mode may append a CRC before the MLLP terminator. Always retain raw bytes. The generic Windows packet capture can inspect both TCP and UDP without taking ownership of a port.
