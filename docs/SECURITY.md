# Security and clinical safety

## Network boundary

The application is designed for a controlled internal laboratory network. Ports 5050 and 5052 should not be publicly exposed. Restrict analyzer listener ports to expected analyzer/middleware addresses and restrict the inbound order API to the authorized clinic server.

The web consoles do not substitute for an authenticated enterprise access layer. Use firewall controls and, where appropriate, an authenticated reverse proxy with TLS.

## Secrets

- Inbound order API token: `selectra_host_query/data/order_api_token.txt`.
- Outbound result API credentials: inspect configured client settings/environment.
- Database credentials: use protected configuration, not committed source.

Never paste real secrets into curl examples, screenshots, commits, tickets, chat, or docs. If a secret enters Git history, rotating it is mandatory; deleting the current line is insufficient.

## Patient data

Sensitive locations include:

- PostgreSQL result/sample tables;
- SQLite order database and WAL files;
- packet captures and raw session logs;
- application/NSSM logs;
- screenshots and exported traces;
- API request/response logs.

Use approved encrypted transfer and access-controlled retention. Generated artifacts are ignored by Git, but `.gitignore` is not an access-control mechanism.

## Clinical integrity rules

- Never guess test mappings.
- Never silently truncate an identifier to force a match.
- Never treat transport ACK as application acceptance.
- Never auto-arm an unvalidated analyzer workflow.
- Never broaden skip/filter rules without analyzer-specific evidence.
- Never test new order payloads with a production patient.
- Preserve exact raw evidence when diagnosing field placement.
- Require operator verification of patient, sample, and requested tests before clinical rollout.

## Packet capture caution

PktMon is machine-wide. The capture utilities refuse to start over another detected PktMon recording and use marker guards for stopping. An all-TCP/UDP capture may collect unrelated credentials or patient traffic; prefer a target-IP filter and a short window.

## Backups and recovery

Backups must be encrypted, access-controlled, tested, and include both result persistence and order/audit state. A restored API token must match the authorized sender configuration; otherwise rotate it in a coordinated maintenance window.
