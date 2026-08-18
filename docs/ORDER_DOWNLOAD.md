# Analyzer order download workflows

## Core safety model

Orders are staged locally before an analyzer can receive them. The inbound API never opens a new connection to an analyzer. Delivery occurs only when the analyzer initiates the expected query on its existing socket.

```text
Clinic server -> stage -> local review/ready state -> analyzer query -> exact match -> response
```

An API `201 Created` means the bridge stored a valid order. It does not mean the analyzer queried, received, accepted, displayed, or executed it.

## Selectra

### Operator flow

1. Clinic server stages an order or operator creates a controlled test order.
2. Operator reviews the order on `http://<bridge>:5052/`.
3. Operator arms the exact order, or enables Selectra API auto-arm after validation.
4. Operator enters/scans the same Sample ID on Selectra.
5. Selectra sends a Q record.
6. Bridge matches Q-2 exactly and sends H/P/O/L.
7. Selectra ACKs the transport frame.
8. Any later application response is inspected for acceptance or O-26 `X` rejection.

### Matching and lifecycle

- Case-sensitive exact ID.
- Bridge does not truncate long IDs.
- If the analyzer truncates before querying, no match occurs.
- Transport ACK changes the order to `transport_acknowledged` and disarms it.
- Reposting the same sample replaces content and restages it.
- Cancellation retains an audit row but prevents delivery.

### Minimal payload principle

Tests and exact sample ID are the most strongly field-confirmed fields. Optional birth date, sex, specimen type, physician, and comment are disabled by default and individually controlled in the Selectra outbound-field panel. A transport ACK is not application acceptance.

## CYANVision

### Operator flow

1. Stage a single sample/test or queue API worklist items.
2. On CYANVision, open Patient Worklist.
3. Press Load from LIS.
4. Analyzer sends `QRY^Q02` over MLLP.
5. Bridge sends `DSR^Q03` worklist data.
6. Analyzer sends `ACK^Q03`.
7. Positive ACK consumes the item; rejection is recorded.

CYANVision is pull-driven but does not use Selectra's Q record. Do not reuse ASTM logic for it.

### Program-ID field trial

The normal result code and the outbound worklist Program ID are intentionally
separate. Current controlled-trial translations are ALP -> `3`, CRE -> `11`,
and LIPASE -> `23`, taken from real result `NTE.8` ProgramID metadata. CY014
shows `DSP|8||GLUC|||` but labels DSP data only as display text, so it does not
prove whether this firmware expects a name, result code, MethodID, or numeric
ProgramID. The numeric ProgramID trial must be confirmed on the analyzer
screen. Free text and tests without an observed ProgramID are rejected. Use
one worklist item per sample/test until multi-test behavior is proven.

## XN-330 boundary

XN-330 remains receive-only in this bridge: port `6001`, its decoder, result
mappings, PostgreSQL persistence, and the `5050` administration interface are
unchanged. The experimental Host Query order service and all `5052` staging,
arming, API, and audit-state code have been removed because that workflow is
no longer required.

## XS-500i future work

The XS-Series specification supports real-time Sample-ID inquiry through the IPU, but no XS order service is currently registered. Before implementing:

1. Photograph/export IPU host settings.
2. Determine selected wire format and Class A/B mode.
3. Enable inquiry only in a controlled window.
4. Capture a real query using a unique non-production ID.
5. Implement against that evidence, not merely the family specification.

## Mini VIDAS limitation

Do not build a direct Mini VIDAS staging tab. The direct serial host interface is upload-only. VIDAS PC/vendor middleware would be a separate supported product path requiring bioMérieux configuration and documentation.
