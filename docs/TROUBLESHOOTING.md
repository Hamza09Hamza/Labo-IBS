# Troubleshooting runbook

## Service is not reachable

1. Check NSSM: `nssm status LaboBridge`.
2. Confirm `Application`, `AppDirectory`, and `AppParameters`.
3. Check listening ports with `Get-NetTCPConnection`.
4. Run `run_all.py` interactively only after stopping NSSM, to see startup exceptions.
5. Check firewall and address binding.

Do not run an interactive copy while NSSM owns the ports.

## Analyzer does not connect

- Confirm effective listener port, not only source default.
- Confirm analyzer/middleware destination IP and port.
- Confirm serial-to-Ethernet mode and serial settings.
- Check routing/VLAN/firewall.
- Use generic packet capture with the analyzer IP.
- If SYN arrives but no connection completes, inspect firewall/listener.
- If nothing arrives, capture closer to the analyzer or configure switch mirroring.

## Results connect but do not appear

- Check protocol ACK sequence.
- Inspect raw capture and decoder output.
- Confirm sample/order record appears before results.
- Check PostgreSQL availability.
- Look in `pending_params` for unknown codes.
- Check evidence-based skip rules (`R`, `REJECT`, calibration, disabled Ca2+).
- Check whether result was mapped but outbound API failed.

## Selectra ACKs but order is not shown

Transport ACK proves bytes were received, not accepted. Search trace for `application_rejected` and O-26 `X`. Return optional outbound fields to minimal, verify test codes, and retry with a new controlled ID.

## CYANVision worklist does not load

- Confirm operator used Patient Worklist -> Load from LIS.
- Look for QRY^Q02.
- Confirm the selected test has a configured outbound ProgramID.
- Inspect ACK^Q03 status.
- If connection closes before ACK, item should remain ready for retry.
- Inspect the DSR preview: Creatinine should currently be `DSP|8||11|||`, not
  `DSP|8||CRE|||`. This is a controlled trial based on observed NTE.8 metadata.

## Port change appears ignored

Runtime rebind occurs between connections. Disconnect the current analyzer session or restart during a controlled window. Confirm the new effective port in live status.

## API returns 401

- Read token from the deployed server's runtime file.
- Remove trailing spaces/newlines from copied secrets.
- Confirm header name is exactly `X-API-TOKEN`.
- Confirm the request reaches port 5052 on the intended server.
- Never place the real token in documentation or Git.

## API returns 400

Read the error body. Common causes are missing fields, non-ASCII analyzer values, unsupported sex/date format, unknown parameter/tarification mapping, ambiguity, or a test code outside the allow-list.

## Disk usage grows

- Check `results/`, packet captures, NSSM output logs, SQLite WAL files, and PostgreSQL storage.
- Stop long unfiltered packet captures.
- Do not delete evidence until copied and retention requirements are satisfied.
- Raw session writing is intentionally limited to selected analyzers during active audit.
