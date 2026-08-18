# CYANVision one-load worklist

This module implements the worklist download flow documented in CY014,
section 3, on the existing CYANVision HL7/MLLP listener (normally TCP `6004`).

```text
CYANVision                       Labo Bridge
    |                                |
    | QRY^Q02  (Load from LIS)       |
    |------------------------------->|
    |                                |
    | DSR^Q03 (one final dataset)    |
    |<-------------------------------|
    |                                |
    | ACK^Q03                        |
    |------------------------------->|
```

The outgoing message contains the manual's exact positional data records:

- `DSP.1`: sample/patient ID
- `DSP.2`: `Y`
- `DSP.3`: given name
- `DSP.4`: family name
- `DSP.5`: sex (`M` or `F`)
- `DSP.6`: birth date as `YYYYMMDD000000`
- `DSP.7`: `1`
- `DSP.8`: one outbound analyzer program selector. CY014 demonstrates `GLUC`
  but does not define the selector namespace. This implementation currently
  trials numeric ProgramID values observed in real result `NTE.8` metadata:
  ALP `3`, CRE `11`, and LIPASE `23`.
- empty `DSC`: final dataset, with no continuation page

The web form is served by `run_all.py` at `http://<server-IP>:5052/`.
Manual CYANVision work starts disarmed after every bridge restart. Orders
received through the authenticated order API are stored in SQLite and remain
ready across restarts. Multiple ready API items use the documented
`DSR^Q03 -> ACK^Q03` continuation loop; an empty `DSC` marks the final item.
Result upload in the opposite direction remains handled by the existing
CYANVision decoder and receives the normal HL7 ACK.

Result-code mappings and outbound ProgramIDs are intentionally separate. A
successful `ACK^Q03` proves message acceptance, not that the intended program
appeared on screen; the operator must confirm the selected test during this
trial.
