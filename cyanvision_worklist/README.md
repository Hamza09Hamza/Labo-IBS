# CYANVision one-load worklist

This module implements the worklist download flow documented in CY014,
section 3, on the existing CYANVision HL7/MLLP listener (normally TCP `6004`).

```text
CYANVision                       Labo Bridge
    |                                |
    | QRY^Q02  (Load from LIS)       |
    |------------------------------->|
    |                                |
    | DSR^Q03 (one item)             |
    |<-------------------------------|
    |                                |
    | ACK^Q03                        |
    |------------------------------->|
    |                                |
    | DSR^Q03 (next item, if any)    |
    |<-------------------------------|
    ...repeats until an empty DSC marks the final item
```

The outgoing message contains the manual's exact positional data records:

- `DSP.1`: sample/patient ID
- `DSP.2`: `Y`
- `DSP.3`: given name
- `DSP.4`: family name
- `DSP.5`: sex (`M` or `F`)
- `DSP.6`: birth date as `YYYYMMDD000000`
- `DSP.7`: `1`
- `DSP.8`: the outbound analyzer program selector. **Confirmed 2026-08-20**:
  this must be the exact literal program name shown on the analyzer's own
  "Selection du programme" screen (`ALP`, `CRE`, `GPT`, `GLUC`, `GGT`,
  `LIPASE`, `IRON`, ...), not a numeric ID. A value the analyzer doesn't
  recognize makes it drop the connection without sending `ACK^Q03`; a
  recognized one gets acknowledged and the program checkbox visibly changes
  on screen. See `cyanvision_worklist/programs.py` for the confirmed table
  and how it was found.
- empty `DSC`: final dataset, with no continuation page

The web form is served by `run_all.py` at `http://<server-IP>:5052/`.
Manual CYANVision work starts disarmed after every bridge restart. Orders
received through the authenticated order API are stored in SQLite and remain
ready across restarts. Multiple ready items - from the API, or staged
together via `POST /api/cyanvision/worklist/batch` - use the documented
`DSR^Q03 -> ACK^Q03` continuation loop to deliver in one `Load from LIS`
press; an empty `DSC` marks the final item. Result upload in the opposite
direction remains handled by the existing CYANVision decoder and receives
the normal HL7 ACK.

## Staging a full worklist (multiple items, one download)

`POST /api/cyanvision/worklist/batch` stages several orders at once so they
deliver together instead of one `Load from LIS` press per sample:

```bash
curl -X POST http://<server-IP>:5052/api/cyanvision/worklist/batch \
  -H "Content-Type: application/json" \
  -d '{
    "confirmation": "ARM CYANVISION WORKLIST",
    "orders": [
      {"sample_id": "S-001", "given_name": "...", "family_name": "...",
       "birth_date": "1980-06-15", "sex": "F", "test_code": "ALP"},
      {"sample_id": "S-002", "given_name": "...", "family_name": "...",
       "birth_date": "1975-03-22", "sex": "M", "test_code": "CRE"}
    ]
  }'
```

Every `test_code` must already have a confirmed entry in
`programs.py`'s `WORKLIST_PROGRAM_IDS`, sample IDs must be unique within the
batch, and it refuses to stage while a connection is already mid-handshake
(`pending_ack`). Refer to `docs/OPERATIONBLOC_API.md`-style usage: check
`/api/cyanvision/worklist` afterward, or watch `/api/events`, to see the
continuation loop deliver each item as the analyzer ACKs the previous one.

## Controlled DSP.8 selector trial

The `5052` CYANVision tab also contains a supervised control-and-candidate
trial, kept as reusable tooling for validating any *new* test code before it
goes live (this is how `CRE`'s correct value was actually found). It stages
the manufacturer's exact `JD123 / Johnathana / Does / GLUC` example first,
then a set of named candidates for whatever code is currently under test.

Because a value the analyzer doesn't recognize drops the connection instead
of returning a negative ACK, the bridge can't safely tell "accepted" from
"silently ignored" - so it never guesses. After each **Load from LIS**,
record which exam CYANVision actually selected, then click
**Mark checked → next** (or turn on auto-advance to skip that click after a
dropped connection specifically - a real ACK still requires you to check the
screen yourself, since acceptance and selection turned out to be different
things). The staging endpoint refuses to mix this sequence with any
already-ready non-trial CYANVision order.
