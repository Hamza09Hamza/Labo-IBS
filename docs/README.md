# Labo-IBS documentation

This folder is the entry point for operating, maintaining, validating, or extending Labo-IBS. It is written for laboratory staff, system administrators, software engineers, and AI agents that have no prior conversation context.

Labo-IBS has two distinct responsibilities:

1. Receive analyzer results, decode them, map analyzer codes to clinic identifiers, store them in PostgreSQL, and optionally forward them to the clinic API.
2. Stage analyzer orders and answer analyzer-initiated worklist/Host Query requests for the instruments that support bidirectional communication.

It is not a diagnostic system and must never manufacture, interpret, or clinically validate a result.

## Reading order

| Document | Read when you need to… |
|---|---|
| [Architecture](ARCHITECTURE.md) | Understand processes, ports, protocols, state, and data flow |
| [Analyzer evidence matrix](ANALYZERS.md) | Know what is proven, implemented, simulated, or still unknown for each instrument |
| [Windows deployment](WINDOWS_DEPLOYMENT.md) | Install, update, run, recover, or inspect the NSSM service |
| [Order download](ORDER_DOWNLOAD.md) | Understand Selectra and CYANVision staging and query handshakes |
| [Order API](ORDER_API.md) | Integrate an authorized clinic server with port 5052 |
| [Results and mappings](RESULTS_AND_MAPPINGS.md) | Follow result ingestion, PostgreSQL persistence, mapping, filtering, and outbound API behavior |
| [Packet capture](PACKET_CAPTURE.md) | Discover or troubleshoot TCP/UDP and serial-to-Ethernet traffic safely |
| [Testing and validation](TESTING.md) | Run automated tests and conduct controlled analyzer validation |
| [Troubleshooting](TROUBLESHOOTING.md) | Diagnose missing connections, queries, orders, ACKs, mappings, or database writes |
| [Security and clinical safety](SECURITY.md) | Protect patient data and avoid unsafe integration assumptions |
| [AI and engineer handoff](AI_HANDOFF.md) | Quickly orient a new maintainer without relying on chat history |

## Source-of-truth hierarchy

When documentation and behavior disagree, resolve the discrepancy rather than silently choosing one. Use this hierarchy:

1. Real packet capture from the exact deployed analyzer, firmware, middleware, and configuration.
2. Application-level analyzer response or rejection.
3. Vendor host-interface specification for the exact model/version.
4. Current production code and automated tests.
5. This documentation.
6. Product-family assumptions, remembered behavior, screenshots, or chat messages.

Code passing a simulated test proves our implementation behaves as designed. It does **not** prove the real analyzer is configured to send that message or accepts the response.

## Repository entry points

- `run_all.py`: normal process entry point; starts analyzer listeners and web interfaces.
- `labo_bridge/server.py`: listener registry, transport sessions, decoding dispatch, ingestion, and query-service hooks.
- `labo_bridge/decoders/`: machine-specific result decoding.
- `labo_bridge/mappings.py`: curated analyzer-code mappings; runtime mapping source of truth.
- `labo_bridge/pg.py`: PostgreSQL persistence.
- `selectra_host_query/`: shared order database, Selectra Host Query, port-5052 web/API application.
- `cyanvision_worklist/`: CYANVision QRY/DSR worklist service.
- `analyzer_packet_capture/`: non-binding Windows TCP/UDP discovery capture.
- `tests/`: protocol, API, persistence-state, and listener integration tests.

## Documentation maintenance rule

Any change to ports, payload fields, API schemas, order states, analyzer support, or deployment commands must update the corresponding document in this folder in the same commit. Never write credentials, real patient data, or raw capture payloads into documentation.
