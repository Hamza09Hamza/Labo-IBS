# AI and engineer handoff guide

## Mission

Maintain a laboratory integration bridge that receives real analyzer results and, for supported analyzers, returns staged orders only after analyzer-initiated queries. Favor captured evidence, exact protocol behavior, reversible changes, and clinical safety.

## First actions in a new session

1. Read `docs/README.md` and `docs/ANALYZERS.md`.
2. Inspect `git status --short`; existing changes may belong to the operator.
3. Read the exact code path involved before proposing a change.
4. Check relevant tests and real captures, but never commit capture artifacts.
5. State whether the feature is documented, implemented, tested, and field-confirmed.
6. Explain protocol/clinical consequences before changing analyzer payloads.

## Non-negotiable distinctions

- Listening port versus analyzer's own fixed remote port.
- TCP transport success versus ASTM/HL7 transport ACK.
- Transport ACK versus application acceptance.
- API staging success versus analyzer delivery.
- Simulated handshake versus physical-analyzer confirmation.
- Analyzer display name versus exact machine program code.
- Result upload versus order download.
- Serial-to-Ethernet conversion versus protocol capability.

## Current high-priority truth

- XN-330 is receive-only on port 6001. Its result decoder, mappings, and 5050
  administration remain; the experimental Host Query/5052 feature was removed.
- Selectra order tests were reported working, but optional demographic/specimen fields remain controlled because malformed fields caused application rejection.
- CYANVision worklist is implemented using QRY/DSR/ACK behavior. DSP.8 now
  receives a separate numeric ProgramID (ALP 3, CRE 11, LIPASE 23) inferred
  from real NTE.8 result metadata. This is not explicitly guaranteed by CY014
  and remains a controlled field trial; retain one-test semantics.
- XS-500i vendor documentation supports query/order exchange, but deployed IPU settings and actual query format are not yet captured; do not implement from assumption.
- Mini VIDAS direct interface is result-upload only; do not promise Host Query through its serial converter.
- `USE_MACHINE_RESULT_API` is currently true in source; verify destination behavior before changing it.

## Change protocol

Before changing an analyzer response:

1. Identify exact incoming query and analyzer model/version.
2. Cite vendor field definitions or real captures.
3. Describe intended field-by-field payload.
4. Preserve exact sample selector where required.
5. Add positive, negative, retry, and end-to-end listener tests.
6. Keep manual arming until physical validation is complete.
7. Tell the operator exactly what trace events and analyzer screen behavior prove success.

## Files that must not be committed

- `results/`, `results 2/`
- capture directories/archives and ETL/PCAP/PCAPNG
- SQLite databases/WAL files
- API tokens and `.env` secrets
- `.venv`, `node_modules`, build output
- real patient screenshots or payloads

## Useful verification commands

```bash
git status --short
python -m unittest discover -s tests -v
python -m compileall -q labo_bridge selectra_host_query cyanvision_worklist
git diff --check
git diff --cached --name-only
```

## Definition of done

A code change is not complete merely because it compiles. It must have:

- documented scope and evidence level;
- automated coverage at the relevant layer;
- no committed runtime/sensitive artifacts;
- safe default state;
- clear Windows deployment steps;
- explicit physical-analyzer validation instructions;
- updated documentation when contracts or behavior changed.
