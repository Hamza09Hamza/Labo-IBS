# System architecture

## Runtime topology

One `run_all.py` process starts six analyzer listeners and two Flask web servers:

```text
                                      +-------------------------+
Analyzers / middleware --TCP--------> | analyzer listeners      |
                                      | 6001 through 6006       |
                                      +------------+------------+
                                                   |
                                                   v
                                      transport framing + decoder
                                                   |
                         +-------------------------+----------------------+
                         |                                                |
                         v                                                v
                 curated code match                               pending code
                         |                                                |
                         +-------------------------+----------------------+
                                                   v
                                         PostgreSQL labo_bridge

Clinic order server --HTTP 5052--> SQLite order staging <--5052--> operator
                                              |
                                              v
                                analyzer-initiated query response

Administrator ----------------HTTP 5050----------------> admin console
```

### Default ports

| Port | Owner | Purpose |
|---:|---|---|
| 5050/TCP | Flask admin | Machines, mappings, results, pending codes, settings |
| 5052/TCP | Flask order app | Local order console, trace, authenticated inbound order API |
| 6001/TCP | `xn330` | XN-330 ASTM result receiving |
| 6002/TCP | `ismart` | I-Smart ASTM result upload |
| 6003/TCP | `selectra` | Selectra LIS2-A results and Host Query |
| 6004/TCP | `cyanvision` | CYANVision HL7/MLLP results and worklist query |
| 6005/TCP | `xs500i` | XS-500i result forwarding through its IPU/Windows host |
| 6006/TCP | `minividas` | Mini VIDAS result upload through serial-to-Ethernet adapter |

Runtime port overrides are supported. The effective deployed port may differ from the default in source, so confirm it through the admin UI and `runtime_ports.json`/database configuration before troubleshooting.

## Process model

- One daemon thread listens per analyzer.
- The two Flask applications run in daemon threads with `threaded=True` and without the development reloader.
- Each accepted analyzer connection is handled synchronously within its listener thread.
- A 90-second silent read timeout releases dead connections.
- Port changes are noticed between connections; an active connection completes on its original socket.
- Each analyzer has a dedicated listener because the wire protocols do not reliably identify the machine before dispatch.

## Protocol layers

Transport and semantics are deliberately separated:

```text
TCP bytes
  -> protocol framing and ACK behavior
  -> machine decoder
  -> normalized event (header, patient, order, result, calibration)
  -> session state
  -> mapping and persistence
```

### ASTM/LIS2-A

The normal analyzer-to-host sequence is:

```text
Analyzer  ENQ ---------------------------------> Host
Analyzer      <-------------------------------- ACK
Analyzer  STX frame checksum CR LF ------------> Host
Analyzer      <-------------------------------- ACK
Analyzer  EOT ---------------------------------> Host
```

At `EOT`, the bridge flushes the result/API batch. Selectra records are also
offered to its configured Host Query service, which can seize the same line in
the opposite direction with `ENQ`. XN-330 is receive-only.

### HL7 over MLLP

CYANVision messages use `VT ... FS CR` envelopes. Result messages receive an HL7 ACK. Worklist `QRY^Q02` messages are routed to the CYANVision worklist service, which sends `DSR^Q03` and waits for `ACK^Q03`.

### Mini VIDAS framing

Mini VIDAS uses ASTM-like control characters but not ASTM record bodies. Its body contains record-separator (`0x1e`) fields with two-letter tags, plus a group-separator/check sequence. It therefore has a dedicated handler and decoder.

## Persistence boundaries

Two databases serve different purposes:

### PostgreSQL: analyzer results and configuration

Schema: `labo_bridge`

- `samples`
- `labo_bridge_results`
- `pending_params`
- `mappings` mirror
- `machine_config`

PostgreSQL is the only durable result store. If it is unavailable, there is no offline result queue.

### SQLite: staged orders and protocol audit

Default path:

```text
selectra_host_query/data/host_query.db
```

It stores Selectra and CYANVision order state, settings, and the 5052 protocol
event trace. It is intentionally separate from clinic result persistence.

The generated inbound API credential is stored beside it at:

```text
selectra_host_query/data/order_api_token.txt
```

Both are runtime secrets/data and are excluded from Git.

## Result path

1. Protocol handler acknowledges and extracts records.
2. Decoder produces normalized events.
3. `_Session` carries patient/sample context across records.
4. Evidence-based filters reject known non-clinical values or retransmissions.
5. `matcher.match_all()` resolves the analyzer code using curated mappings.
6. Matched values are written to PostgreSQL.
7. Unmatched codes are aggregated in `pending_params` for human review.
8. When `USE_MACHINE_RESULT_API` is true, matched values are additionally queued and sent as one array at the message boundary.

## Order path

1. An authorized server or local operator stages an order in SQLite.
2. The order is reviewed and becomes ready according to analyzer-specific rules.
3. The analyzer initiates a query; the bridge does not invent one.
4. The query must satisfy the analyzer's matching rules, normally exact sample ID.
5. The bridge sends the documented response over the existing analyzer socket.
6. Transport ACK updates local state. Where available, application-level ACK/rejection provides stronger evidence.

## Critical configuration fact

At the time this document was written, `labo_bridge/config.py` sets `USE_MACHINE_RESULT_API = True`. Therefore matched results are written locally and also queued for the configured clinic API. Treat the source file as authoritative and verify the destination and credential before production operation.
