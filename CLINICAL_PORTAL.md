# OperationBloc Bridge — Operating Block Equipment Console

This is a standalone application for the head doctor. It does not start the
laboratory analyzer bridge, use its PostgreSQL result tables, or appear inside
the laboratory mapping console.

The home screen follows the physical suite: three large operation-block panels,
each containing its uMEC12 patient monitor and WATO anesthesia machine.

## Start everything

Activate the existing Python environment and run one command:

```bash
source .venv/bin/activate
python3 -u run_clinical.py
```

This starts, supervises, and stops together:

- the doctor portal on port `5051`;
- every enabled uMEC12 PDS connector;
- the WATO TCP/HL7 listener for each enabled operation block.

Open the portal locally at `http://127.0.0.1:5051` or from the clinic LAN at
`http://192.168.1.100:5051`.

`Ctrl+C` stops the portal and every device collector child process.
Raw device captures are kept separately under `clinical_portal/captures/`.

## Off-site demo mode

To preview the full portal without contacting clinic devices:

```bash
python3 -u run_clinical.py --demo
```

Demo mode disables every real collector and continuously generates synthetic
uMEC12 and WATO streams for all three blocks. The interface displays a permanent
`SIMULATED DATA` banner. It also cycles demonstration alarms and a temporary
SpO₂ no-signal state so those UI states can be reviewed safely.

## Configuration

Use the settings button on either machine card to edit its block name, machine
label, enabled state, connection port, uMEC12 IP address, block color, and
machine photo. Changes are persisted in `clinical_portal/config.json`.
Restart OperationBloc Bridge after changing connection settings so the device
collector processes reopen the new addresses and ports.

The known test setup is already entered for Operation Block 1:

```text
uMEC12: 192.168.1.113:4601
WATO destination: 192.168.1.100:6010
```

WATO listener ports are:

```text
Operation Block 1: 6010
Operation Block 2: 6011
Operation Block 3: 6012
```

The uMEC12 PDS service port is fixed at `4601`; OperationBloc Bridge connects
outbound to the monitor. Its monitor IP and optional bridge source port are
configurable. A source port of `6010`, for example, creates the flow
`bridge-IP:6010 -> monitor-IP:4601`; it is not a listener. Leave it blank to
let the operating system choose an ephemeral source port. The WATO flow is the
reverse: set the WATO's Destination IP to the bridge address and its Port to
that block's local listener port shown above. Every enabled local source or
listener port must be unique.

Operation Block 2 and 3 uMEC12 connectors remain disabled until their real IP
addresses are entered. Do not invent or reuse Operation Block 1's monitor
address. After adding an address, enable the monitor in its settings panel.

## Data behavior

The live-monitoring view combines:

- uMEC12 heart rate, respiration, SpO₂, pulse, perfusion index, NIBP, and
  temperature channels;
- WATO numeric ventilation, airway pressure, gas, volume, flow, and agent
  values actually transmitted through HL7.

Available windows are Live, 10 seconds, 20 seconds, 30 seconds, and 1 minute.
Each parameter shows latest, mean, minimum, maximum, count, and a short trend.
Mindray's `-100` no-reading sentinel is excluded from clinical display and
statistics.

Rolling data is bounded in memory and resets when the application restarts.
This is suitable for live supervision, not permanent medico-legal charting.
Authentication, TLS, audit logging, and agreed retention are required before
production use with identifiable patient data.
