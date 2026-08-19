# OperationBloc Bridge — Operating Block Equipment Console

This is a self-contained application for the head doctor: its own code, its
own config file, its own SQLite history database, and its own web port. It
still does not share the laboratory bridge's PostgreSQL result tables or
appear inside the laboratory mapping console - but `run_all.py` now starts it
in the same process as everything else (see below), so one command runs both.

The home screen follows the physical suite: three large operation-block panels,
each containing its uMEC12 patient monitor and WATO anesthesia machine.

## Start everything

Either run it together with the laboratory bridge:

```bash
source .venv/bin/activate
python3 -u run_all.py
```

...or run it alone, standalone, exactly as before:

```bash
source .venv/bin/activate
python3 -u run_clinical.py
```

Either way this starts, supervises, and stops together:

- the doctor portal on port `5051`;
- every enabled uMEC12 PDS connector;
- the WATO TCP/HL7 listener for each enabled operation block;
- the 10-second history snapshot writer (see Data behavior below).

Open the portal locally at `http://127.0.0.1:5051` or from the clinic LAN at
`http://192.168.1.100:5051`.

`Ctrl+C` stops the portal and every device collector child process (and, when
run via `run_all.py`, the laboratory bridge listeners too).
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

The live-monitoring view itself is still bounded in memory and resets when the
application restarts - that part is suitable for live supervision, not
medico-legal charting. Separately, every 10 seconds `clinical_portal/history.py`
reads that same already-validated window straight from the store (it never
re-parses raw device bytes) and appends one row per parameter with data to
`clinical_portal/data/history.db` (SQLite): that window's latest value plus
its mean/min/max/count. This history survives restarts and answers "what was
this machine reading at time X," but it is still not a substitute for an
audited, retention-agreed medico-legal record. Authentication, TLS, audit
logging, and agreed retention are required before production use with
identifiable patient data.

## API

- `GET /api/machines/<block_id>/<source>` - latest reading name/value/unit for
  one machine. `source` is `umec12` or `wato` (case-insensitive). Example:
  `GET /api/machines/1/umec12`.
- `GET /api/machines/<block_id>/<source>/history?limit=100&code=<param code>` -
  persisted 10s snapshots from `history.db`, newest first. `code` is optional
  (e.g. `101` for uMEC12 heart rate); omit it to get every parameter for that
  machine.
- `POST /api/machines/<block_id>/<source>/ping` - for `umec12`, opens a real
  TCP connection to the configured monitor IP on port `4601` and reports
  `ok`/`latency_ms`/`error`. For `wato`, there is nothing to dial: WATO only
  ever connects outbound *to* the bridge, so its own IP is never known to us.
  That call instead reports `pingable: false` plus WATO's last known
  connection state (`device_state`, `last_seen`) as the closest honest signal.
