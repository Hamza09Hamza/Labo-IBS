# Labo-IBS

Labo-IBS is a laboratory integration bridge that receives results from several clinical analyzers, decodes their machine-specific wire formats, maps analyzer test codes to the clinic information system, and stores the resulting data in PostgreSQL.

The project was built around real laboratory integration work. It handles multiple analyzer protocols, concurrent TCP connections, machine-specific decoding rules, curated test mappings, pending-code review, and an optional downstream API path.

> **Important:** this repository is integration software, not a diagnostic system. It transports and maps results produced by laboratory analyzers; it does not interpret results or make clinical decisions.

## What the system does

```text
Laboratory analyzer
        |
        | TCP connection on a dedicated port
        v
Protocol session handler
(ASTM / LIS2-A / HL7 MLLP / Mini VIDAS framing)
        |
        v
Machine-specific decoder
        |
        v
Curated test-code matcher
        |
        +--------------------+
        |                    |
        v                    v
Matched result          Unknown test code
        |                    |
        v                    v
PostgreSQL result       PostgreSQL pending backlog
        |                    |
        +----------+---------+
                   |
                   v
             Local admin UI
                   |
                   v
        Optional clinic API batch
```

Each analyzer receives its own listener port because the supported protocols do not reliably identify the sending machine after a shared connection has been accepted.

## Supported analyzers and protocols

The default listener configuration lives in `labo_bridge/server.py`.

| Machine key | Analyzer | Protocol handling | Default port |
|---|---|---|---:|
| `xn330` | Sysmex XN-330 | ASTM records | `6001` |
| `ismart` | I-Smart 30 PRO | ASTM records with initial acknowledgement | `6002` |
| `selectra` | Selectra chemistry analyzer | ASTM/LIS2-A records | `6003` |
| `cyanvision` | CyanVision | HL7 v2 messages over MLLP | `6004` |
| `xs500i` | Sysmex XS-500i through its IPU/host PC | ASTM, initially reusing the XN-330 decoder | `6005` |
| `minividas` | bioMérieux Mini VIDAS through a serial-to-Ethernet adapter | ASTM-like handshake with Mini VIDAS-specific frame decoding | `6006` |

The ports shown above are defaults. Runtime port selection and machine metadata are exposed through the administration layer and stored in PostgreSQL.

## Main components

### TCP listeners and protocol dispatch

`labo_bridge/server.py` starts one listener thread per analyzer and dispatches each connection to the appropriate protocol implementation.

The server currently supports:

- ASTM-style ENQ/ACK/STX/EOT sessions
- LIS2-A records used by the Selectra environment
- HL7 messages transported with MLLP framing and HL7 acknowledgements
- Mini VIDAS frames with record-separator-delimited fields

A connection idle timeout prevents dead analyzer sessions from remaining open indefinitely.

### Protocol implementations

`labo_bridge/protocols/` contains the low-level framing and acknowledgement logic:

- `astm.py` handles ASTM control characters, frames, records, and acknowledgements
- `hl7_mllp.py` handles MLLP envelopes and HL7 acknowledgements

Protocol code is intentionally separate from analyzer decoders. The protocol layer answers *how bytes are transported*; decoder modules answer *what each field means for a specific analyzer*.

### Analyzer decoders

`labo_bridge/decoders/` converts raw analyzer records into normalized result dictionaries.

Each decoder is based on the actual format used by that analyzer. A decoder generally extracts information such as:

- sample or order identifier
- analyzer test code and display name
- result value
- unit
- result status or abnormal flag
- analyzer and specimen metadata when available

A new analyzer should receive a dedicated decoder when its real wire format differs from an existing model. Reusing another decoder is only a starting point and must be confirmed against captured messages.

### Test-code mapping

`labo_bridge/matcher.py` resolves normalized analyzer test codes against the curated maps in `labo_bridge/mappings.py`.

A successful mapping can target either:

- a composed laboratory parameter (`labo_param`), or
- a complete billable examination/service when no parameter-level row is appropriate

Mappings are deliberately curated rather than guessed. Unknown codes are written to the pending backlog so a human can inspect the analyzer name, example value, unit, and raw record before assigning a destination.

The administration tools update both the Python mapping source and its PostgreSQL mirror. The Python mapping remains the runtime source of truth; the database mirror makes mappings visible for inspection and recovery.

### PostgreSQL persistence

`labo_bridge/pg.py` is the persistence layer. Local SQLite persistence was retired; PostgreSQL is the only active store.

The project creates and uses the following tables under the `labo_bridge` schema:

| Table | Purpose |
|---|---|
| `samples` | One row per captured machine/sample pair, including available source and patient metadata |
| `labo_bridge_results` | Results whose analyzer test codes were confidently mapped |
| `pending_params` | One backlog row per unknown machine/test-code pair, with occurrence counts and latest examples |
| `mappings` | Read-only database mirror of the curated Python mappings |
| `machine_config` | Machine labels, protocol labels, ports, display settings, photos, addresses, and clinic machine IDs |

Database connections are thread-local. Each listener and the Flask administration thread receives its own PostgreSQL connection so concurrent analyzer writes and UI queries do not share one mutable connection.

When PostgreSQL is unavailable, the bridge warns and skips the affected read or write rather than crashing every listener. Because PostgreSQL is the only store, results received during an outage are not durably captured. Keeping the database available is therefore an operational requirement.

### Result filtering and safety rules

The ingestion path contains narrowly scoped rules derived from analyzer behavior observed during integration. Examples include:

- skipping ASTM results marked as retransmissions (`status=R`)
- rejecting literal `REJECT` values that represent failed analyzer quality checks
- ignoring the disabled I-Smart `Ca2+` channel only when that exact machine, code, and placeholder value match
- retaining unknown but potentially valid codes in the pending backlog instead of silently discarding them

These rules should remain evidence-based and machine-specific. Avoid broad filters that could hide valid clinical results.

### Optional clinic API

`labo_bridge/api_client.py` and `API_LABO_MACHINE_RESULT.md` describe the optional downstream API integration.

When `USE_MACHINE_RESULT_API` is enabled in `labo_bridge/config.py`, matched results are queued during the analyzer session and sent as one JSON array at the natural message or batch boundary. Local rows track whether the clinic API accepted each result.

The flag is disabled by default. Confirm the target API contract, authentication, error handling, and source-of-truth behavior before enabling it in a deployment.

### Administration interface

`labo_bridge/admin/` provides a Flask-based administration interface for:

- reviewing machines and their live status
- changing machine settings and listener ports
- browsing captured samples and mapped results
- reviewing unknown analyzer codes
- adding, changing, and deleting curated mappings
- configuring integration settings
- managing analyzer display information and images

The UI polls the backend for live status and statistics. Machine port changes can trigger runtime listener rebinds without restarting the full process.

## Project structure

```text
Labo-IBS/
├── run_all.py                         # starts listeners and the admin application
├── deploy_seed.py                     # creates/seeds a fresh PostgreSQL schema
├── requirements.txt
├── API_LABO_MACHINE_RESULT.md         # downstream API contract notes
└── labo_bridge/
    ├── server.py                      # listeners, sessions, ingestion and API batching
    ├── pg.py                          # PostgreSQL persistence
    ├── matcher.py                     # curated mapping lookup
    ├── mappings.py                    # runtime mapping source of truth
    ├── api_client.py                  # optional clinic API client
    ├── config.py                      # runtime feature switches
    ├── runtime_ports.py               # live listener port configuration
    ├── live_status.py                 # connection and listener status
    ├── protocols/
    │   ├── astm.py
    │   └── hl7_mllp.py
    ├── decoders/
    │   ├── xn330.py
    │   ├── ismart.py
    │   ├── selectra.py
    │   ├── cyanvision.py
    │   └── minividas.py
    └── admin/
        ├── app.py
        ├── mappings_editor.py
        ├── machines_editor.py
        ├── config_editor.py
        └── static/
```

## Initial setup

### Requirements

- Python 3 with virtual-environment support
- PostgreSQL accessible from the bridge host
- Network routes from each analyzer, adapter, or analyzer host PC to the bridge
- Firewall rules permitting only the required analyzer ports

Install the Python dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Configure PostgreSQL

The current connection settings are defined in `labo_bridge/pg.py`. Adjust the DSN for the deployment environment and use secure credential management such as PostgreSQL service files, `.pgpass`, or environment-based configuration.

Do not commit production passwords or patient data to the repository.

For a fresh database, review the seed values and run:

```bash
python deploy_seed.py
```

`deploy_seed.py` creates the project schema and tables, mirrors curated mappings, seeds machine configuration, and optionally initializes the known pending-code backlog. It does not seed fake sample or result history.

### Configure analyzers

For each analyzer or adapter:

1. Assign a stable IP address to the bridge host.
2. Point the analyzer, middleware PC, or serial-to-Ethernet adapter to the corresponding bridge IP and port.
3. Confirm the expected transport mode, framing, character encoding, and acknowledgement behavior.
4. Capture and verify a non-production test transmission before accepting real results.
5. Confirm that sample identifiers and test-code fields are decoded correctly.

The XS-500i depends on its attached IPU/Windows host because the analyzer itself may not expose a network destination setting. Its forwarding configuration must be confirmed on that host.

## Running the bridge

Start all configured analyzer listeners and the administration application:

```bash
python run_all.py
```

The default analyzer listeners use ports `6001` through `6006`. The administration application uses port `5050`.

Press `Ctrl+C` to stop the process.

## Network and security notes

This application is intended for a controlled laboratory network.

- `run_all.py` currently starts the Flask administration server on `0.0.0.0:5050`.
- The repository does not document a built-in user-authentication layer for the admin UI.
- Do not expose the admin port directly to the public internet.
- Restrict analyzer and admin ports with host and network firewalls.
- Prefer binding the admin UI to `127.0.0.1` and reaching it through an authenticated reverse proxy or secure tunnel when remote access is required.
- Use TLS and authenticated requests for any downstream API connection.
- Treat raw analyzer captures, sample identifiers, patient fields, and database exports as sensitive clinical data.
- Review logs, seed data, screenshots, and capture files before sharing them outside the authorized environment.

## Deployment and operations

Before production deployment:

- confirm every machine mapping against the clinic database
- test acknowledgements and retry behavior with each analyzer
- test database outages and recovery
- define monitoring for listener status, database availability, pending-code growth, and failed API batches
- back up the PostgreSQL schema and curated mapping source
- restrict access to the administration UI
- document who is authorized to approve new mappings
- verify the bridge host clock and timezone

Raw per-session file dumps are disabled in the current server code to avoid unbounded disk growth. Re-enable them only temporarily for controlled protocol debugging, and handle the generated files as sensitive data.

## Adding another analyzer

A new integration normally requires:

1. Capturing representative messages from the real analyzer or middleware.
2. Identifying the transport protocol and handshake.
3. Implementing or selecting a protocol session handler.
4. Writing a decoder for the analyzer's field layout.
5. Registering the machine, port, and decoder in the server configuration.
6. Adding display and database configuration.
7. Verifying sample identifiers, units, flags, retransmissions, and error values.
8. Building curated mappings from confirmed analyzer codes to clinic records.

Do not generate a decoder from a product name alone. Analyzer firmware, middleware, host software, and local configuration can all change the transmitted format.

## Current limitations

- PostgreSQL is a single required persistence layer; there is no durable offline queue during a database outage.
- Analyzer decoders are tied to formats observed during this integration and may require adjustment for other firmware or middleware versions.
- The administration interface must be protected by the deployment network because built-in authentication is not documented.
- The optional clinic API path must be validated in the target environment before being enabled.
- The project does not replace LIS/HIS validation, analyzer quality control, or clinical review.

## Responsible use

Use this software only in an authorized environment and validate every integration with laboratory staff and the owners of the receiving information system. A successfully parsed message is not sufficient proof that the correct patient, examination, unit, or result destination was selected.
