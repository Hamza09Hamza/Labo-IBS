# Result ingestion, mappings, and persistence

## Normalized result

Machine decoders convert raw records into a common shape containing some or all of:

- `test_code`
- `test_name`
- `value`
- `unit`
- `ref_range`
- `flag`
- `status`
- raw record evidence

The session supplies sample ID, patient, analyzer, source address, and specimen metadata when present.

## Curated matching

`labo_bridge/mappings.py` is the runtime mapping source of truth. `matcher.match_all(machine, test_code)` may resolve one analyzer code to one or more clinic destinations:

- composed parameter: `param_id` plus its service context;
- complete non-composed examination: `service_tarification_id` without a parameter.

Never map by display-name similarity alone. Confirm the analyzer code, method, unit, clinic exam, and parameter identity with laboratory staff and database evidence.

Unknown codes are not discarded. They update a machine/code row in `pending_params` with occurrence count and examples so an authorized human can decide.

## Evidence-based filters

Current narrowly scoped rules include:

- ASTM result status `R`: skip as retransmission.
- Literal value `REJECT`: skip as analyzer-declared unusable measurement.
- I-Smart `Ca2+` with literal `-`: skip only because that exact channel is disabled.
- Selectra `CAL elitech`: skip as calibration, not patient result.
- Decoder-recognized calibration batches: do not create patient results.

Do not broaden these into generic filters without captures proving the same meaning on another analyzer.

## PostgreSQL behavior

Matched results are written to `labo_bridge.labo_bridge_results`. Unknown codes go to `labo_bridge.pending_params`. Sample context goes to `labo_bridge.samples`.

There is no durable local fallback if PostgreSQL is down. A warning does not mean the result was queued for later.

## Outbound clinic result API

`labo_bridge/config.py` controls `USE_MACHINE_RESULT_API`. When true:

1. matched result is still written locally;
2. an outbound item is appended to the session batch;
3. the batch is sent as one JSON array at ASTM EOT or end of HL7 message;
4. accepted local rows are marked `api_sent` with any returned ID.

Inspect the actual flag before deployment; do not rely on older README language. The current source has it enabled.

Failure semantics must be understood operationally: local rows remain evidence of receipt, but outbound API delivery may fail. Monitor `api_sent`, service logs, and destination availability.

## Mapping-change checklist

1. Capture or retrieve the exact raw analyzer code.
2. Confirm analyzer/test identity with operator and manual if available.
3. Confirm clinic `service_tarification_id` and `param_id`.
4. Check whether another machine already maps the same code differently.
5. Add/update through the mapping administration workflow.
6. Run tests and a non-production sample.
7. Verify database row, unit, sample identity, and outbound API payload.
8. Record evidence, not patient data, in the commit message or documentation.
