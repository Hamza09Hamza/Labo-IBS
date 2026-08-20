# OperationBloc API

API behind OperationBloc Bridge, the three-block operating-room console.
Base URL: `http://172.16.2.4:5051` (or `http://127.0.0.1:5051` locally).

No authentication today — reachable to anything on the clinic LAN that can
reach port 5051, same as the doctor portal itself. All responses are JSON.

**Block vs. machine identity.** `block_id`/`source` (e.g. "block 1's
umec12") identify a *slot*, not a physical device — if staff physically swap
which monitor sits in which block, that pairing changes. Every machine also
carries a `machine_id` (e.g. `UMEC-01`), a free-form ID independent of the
block, set once in configuration and moved with the physical device if it's
ever relocated. Use `block_id`/`source` for "what's in this block right
now"; use `machine_id` (and `/api/machines/by-id/...` below) when you need
to follow one physical device's history even if it's been moved.

## GET /api/health

Liveness check, no parameters.

```bash
curl -s http://172.16.2.4:5051/api/health
```

```json
{"ok": true, "application": "operationbloc-bridge"}
```

## GET /api/machines/{block_id}/{source}

Current reading of every parameter one machine reports, as a flat list of
name/value/unit.

- `block_id` — `1`, `2`, or `3`
- `source` — `umec12` (patient monitor) or `wato` (anesthesia workstation),
  case-insensitive

```bash
curl -s http://172.16.2.4:5051/api/machines/1/umec12
```

```json
{
  "block_id": 1,
  "block_name": "Operation Block 1",
  "source": "umec12",
  "machine_id": "UMEC-01",
  "machine_name": "Mindray uMEC12",
  "state": "live",
  "last_seen": "2026-08-19T09:41:07Z",
  "readings": [
    {"name": "Heart rate", "value": 78, "unit": "bpm"},
    {"name": "Oxygen saturation", "value": 97, "unit": "%"},
    {"name": "NIBP systolic", "value": 118, "unit": "mmHg"}
  ]
}
```

`value` is `null` when the latest reading isn't currently valid — most often
Mindray's `-100` no-signal sentinel. Treat `null` as "no current reading,"
never as zero.

## GET /api/machines/{block_id}/{source}/history

Every 10 seconds the bridge records a snapshot per parameter: that window's
latest value plus mean/min/max/count. This reads that log back, newest
first — unlike the live endpoint, it survives a bridge restart.

Query params:
- `limit` — default `100`, clamped to `1–1000`
- `code` — limit to one parameter (e.g. `101` for heart rate); omit for all
  parameters that machine reports. See [parameter codes](#parameter-codes).

```bash
curl -s -G http://172.16.2.4:5051/api/machines/1/umec12/history \
  --data-urlencode "code=101" \
  --data-urlencode "limit=20"
```

```json
{
  "block_id": 1,
  "source": "umec12",
  "machine_id": "UMEC-01",
  "rows": [
    {
      "captured_at": "2026-08-19T09:41:10Z",
      "chamber_id": 1,
      "source": "umec12",
      "machine_id": "UMEC-01",
      "code": "101",
      "label": "Heart rate",
      "unit": "bpm",
      "window_seconds": 10,
      "latest_value": 78,
      "valid": 1,
      "mean": 77.4,
      "min_value": 76,
      "max_value": 79,
      "count": 9
    }
  ]
}
```

## GET /api/machines/by-id/{machine_id}/history

Same shape as the endpoint above, but scoped to one physical device instead
of one block — every row that device has ever recorded, even across a
block move, since each row already carries the `machine_id` it was captured
under. Use this instead of the block-scoped history when a machine may have
been swapped between blocks and you need its whole history regardless.

Query params: same `limit` and `code` as above.

```bash
curl -s -G http://172.16.2.4:5051/api/machines/by-id/UMEC-01/history \
  --data-urlencode "code=101" \
  --data-urlencode "limit=20"
```

```json
{"machine_id": "UMEC-01", "rows": [ { "chamber_id": 1, "...": "..." } ]}
```

## POST /api/machines/{block_id}/{source}/ping

Checks whether the machine is reachable right now. The two machines connect
in opposite directions, so "ping" means something different for each:

- **umec12** is dialed by the bridge (outbound), so this opens a real TCP
  connection to its configured IP on port `4601`.
- **wato** only ever dials *into* the bridge — its own IP is never known to
  us — so there's nothing to dial out to. This instead reports its last
  known connection state.

```bash
curl -s -X POST http://172.16.2.4:5051/api/machines/1/umec12/ping
```

```json
{
  "block_id": 1, "source": "umec12", "machine_id": "UMEC-01", "pingable": true, "ok": true,
  "ip": "192.168.1.113", "port": 4601, "latency_ms": 4.2, "error": null
}
```

```bash
curl -s -X POST http://172.16.2.4:5051/api/machines/2/wato/ping
```

```json
{
  "block_id": 2, "source": "wato", "machine_id": "WATO-02", "pingable": false,
  "reason": "WATO is a listener; the bridge cannot dial out to it. Reporting its last known connection state instead.",
  "device_state": "live", "last_seen": "2026-08-19T09:41:02Z"
}
```

On uMEC12, `ok: false` with an `error` string means the connection failed
(wrong IP, monitor off, network down). A missing configured IP returns `400`
instead of attempting a connection.

## Parameter codes

Not exhaustive — every code a machine has ever reported is still recorded
and returned even if it isn't listed here.

| umec12 | code | unit |
|---|---|---|
| Heart rate | `101` | bpm |
| Respiration rate | `151` | rpm |
| Oxygen saturation | `160` | % |
| Pulse rate | `161` | bpm |
| Perfusion index | `162` | % |
| NIBP systolic | `170` | mmHg |
| NIBP diastolic | `171` | mmHg |
| NIBP mean | `172` | mmHg |
| NIBP pulse | `173` | bpm |
| Temperature 1 | `200` | °C |
| Temperature 2 | `201` | °C |
| Temperature difference | `202` | °C |

| wato | code | unit |
|---|---|---|
| Peak airway pressure | `MDC_VENT_PRESS_MAX` | cmH₂O |
| Mean airway pressure | `MDC_PRESS_AWAY_INSP_MEAN` | cmH₂O |
| Plateau pressure | `MDC_PRESS_RESP_PLAT` | cmH₂O |
| PEEP | `MDC_VENT_PRESS_AWAY_END_EXP_POS` | cmH₂O |
| Minute volume | `MDC_VOL_MINUTE_AWAY` | L/min |
| Expiratory tidal volume | `MDC_VOL_AWAY_TIDAL` | mL |
| Ventilator respiratory rate | `MDC_VENT_RESP_RATE` | rpm |
| O₂ fresh gas flow | `MDC_FLOW_O2_FG` | L/min |
| Inspired oxygen | `MDC_CONC_AWAY_O2_INSP` | % |
| End-tidal oxygen | `MDC_CONC_AWAY_O2_ET` | % |
| End-tidal CO₂ | `MDC_CONC_AWAY_CO2_ET` | mmHg |
| Inspired CO₂ | `MDC_CONC_AWAY_CO2_INSP` | mmHg |
| CO₂ respiratory rate | `MDC_CO2_RESP_RATE` | rpm |
| Minimum alveolar concentration | `MDC_CONC_MAC` | — |

## GET /api/chambers

All three blocks at once — both machines, every parameter with full stats,
plus patient and alarm state. This is what the doctor portal itself polls;
reach for the machine endpoints above unless you need everything together.

Query param `window` — stats window in seconds: `0` (latest point only),
`10` (default), `20`, `30`, or `60`.

```bash
curl -s -G http://172.16.2.4:5051/api/chambers --data-urlencode "window=30"
```

## GET /api/chambers/{block_id}

Same shape as one entry of `/api/chambers`, scoped to a single block.

```bash
curl -s http://172.16.2.4:5051/api/chambers/3?window=10
```

## GET /api/config

Editable configuration for all three blocks — names, colors, each machine's
connection settings. Read-only from this endpoint.

```bash
curl -s http://172.16.2.4:5051/api/config
```

## PUT /api/blocks/{block_id}/machines/{source}/config — administrative

Edits one machine's configuration. This is what the settings panel in the
doctor portal calls; most integrations should read `/api/config` rather than
write to it.

```bash
curl -s -X PUT http://172.16.2.4:5051/api/blocks/2/machines/umec12/config \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ip": "192.168.1.114"}'
```

This is also how a physical swap gets recorded: after moving a machine to a
different block, set its `machine_id` (letters, digits, `-`, `_`, up to 40
chars) on whichever slot it's now in. It's rejected with `400` if that ID is
already assigned to another block/machine — two slots can never claim the
same physical device at once.

```bash
curl -s -X PUT http://172.16.2.4:5051/api/blocks/1/machines/umec12/config \
  -H "Content-Type: application/json" \
  -d '{"machine_id": "UMEC-02"}'
```

Connection changes need a bridge restart before device collectors pick up
the new address or port — the response always includes
`"restart_required": true` as a reminder, not a live status.

## POST /api/chambers/{block_id}/readings — internal only

How the uMEC12 and WATO collector processes feed readings into the bridge.
Restricted to `127.0.0.1`/`::1` — calling this from anywhere else on the LAN
returns `403`. Not for external callers.

## Errors

Every error is a JSON body with a plain-language `error` string alongside
the status code.

| Status | Meaning | Example |
|---|---|---|
| 400 | Bad query value, or a ping requested for a machine with no IP configured | `"window must be one of 0, 10, 20, 30, 60 seconds"` |
| 403 | Reading ingestion called from outside localhost | `"reading ingestion is restricted to this host"` |
| 404 | Unknown block ID, or a machine name that isn't `umec12`/`wato` | `"unknown machine name 'wato12'; use one of ['umec12', 'wato']"` |
