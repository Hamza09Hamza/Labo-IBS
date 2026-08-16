# Analyzer order download API

The order API accepts work from another authorized server, validates it, and
stores it in the local SQLite database. It is served by `run_all.py` on:

```text
http://<bridge-IP>:5052/api/v1/orders/
```

This API does not push a connection to either analyzer:

- Selectra orders remain inactive until a local operator arms the exact sample
  on the `5052` console. A matching Host Query can then receive it.
- CYANVision sends `QRY^Q02` when the operator presses **Patient Worklist ->
  Load from LIS**. The bridge returns the ready CYANVision queue in creation
  order, waiting for `ACK^Q03` between `DSR^Q03` items. The final item has an
  empty `DSC` continuation pointer.

## Authentication

Every endpoint in this document requires the private token generated locally
by the bridge during setup. Every order API request must include:

```http
X-API-TOKEN: <the-same-secret>
Content-Type: application/json
```

If bridge authentication is unavailable, the endpoints return `503`. A missing
or incorrect header returns `401`. Local token retrieval, firewall setup,
deployment, and connection testing are documented separately in
`ORDER_DOWNLOAD_SETUP.md`. The real token must never be added to this file.

## Selectra

### Stage or replace an order

```http
POST /api/v1/orders/selectra
```

```json
{
  "external_order_id": "LIS-ORDER-7812",
  "sample_id": "2608130012",
  "patient_id": "PAT-4821",
  "family_name": "BENCH",
  "given_name": "PATIENT",
  "birth_date": "1980-06-15",
  "sex": "F",
  "specimen_type": "Normal",
  "ordering_physician": "DR LAB",
  "comment": "Fasting sample",
  "tests": [
    {"service_tarification_id": 392},
    {"param_id": 99953, "service_tarification_id": 528}
  ]
}
```

Rules:

- `sample_id` is required and case-sensitive. The bridge does not truncate it
  or impose the Selectra manual's historical 12-character limit. Delivery
  still requires Selectra to send the exact same complete value in `Q-2`.
  If the analyzer firmware truncates an ID, it remains safely unmatched.
- `family_name` plus a space plus `given_name` must fit the Selectra's
  20-character patient-name limit.
- `birth_date` uses `YYYY-MM-DD`; `sex` is `M`, `F`, or `U`.
- `patient_id` is retained for local/clinic correlation. Selectra documents
  its patient-ID fields as ignored, so it does not appear in the analyzer form.
- `specimen_type` is optional and is sent in `O-16`. It must exactly match a
  case-sensitive sample type configured on that Selectra (for example
  `Normal`); omit it when the analyzer's configured label is unknown.
- `ordering_physician` is optional, sent in `O-17`, and limited to 20
  characters. `comment` is optional, sent in a following `C` record, and
  limited to the 100 characters stored by Selectra.
- `tests` should contain clinic identifiers. Each entry requires `param_id`,
  `service_tarification_id`, or both. LaboBridge reverses its curated Selectra
  mappings and transmits the exact installed analyzer code.
- Supply both identifiers when available. For example, tarification `528`
  contains both SGOT and SGPT and is rejected as ambiguous unless its
  `param_id` is also supplied.
- Unknown or ambiguous identifiers return HTTP `400`; LaboBridge never guesses
  a clinical test. Legacy method-name strings remain accepted temporarily for
  compatibility, but new integrations should use identifiers.
- Reposting the same `sample_id` replaces the stored content and returns it to
  the inactive staged state. `external_order_id` is optional correlation
  metadata.

The API only stages the order. It remains inactive until a local operator opens
the `5052` console and clicks **Arm for Selectra** on that exact sample. Once
armed, it survives a LaboBridge restart. After Selectra transport-ACKs the
response, it becomes `transport_acknowledged` and is no longer armed.

### Read status or cancel

```http
GET    /api/v1/orders/selectra/<sample_id>
DELETE /api/v1/orders/selectra/<sample_id>
```

Cancellation preserves the audit row but changes it to `cancelled` and makes
it unavailable to the analyzer.

## CYANVision

### Stage or replace an order

```http
POST /api/v1/orders/cyanvision
```

```json
{
  "external_order_id": "LIS-ORDER-7813",
  "sample_id": "CYAN-001",
  "given_name": "BENCH",
  "family_name": "PATIENT",
  "birth_date": "1980-06-15",
  "sex": "F",
  "test": {"service_tarification_id": 481}
}
```

Rules:

- Values must use printable ASCII because the documented outbound message
  declares `ASCII` in `MSH-18`.
- `sex` is `M` or `F`.
- `test` requires `param_id`, `service_tarification_id`, or both. LaboBridge
  resolves it through the curated CYANVision mappings and sends the resulting
  program code as `DSP|8||<code>|||`.
- Unknown or ambiguous identifiers return HTTP `400`. The legacy `test_code`
  field remains accepted temporarily for compatibility.
- The currently verified message layout represents one test per sample.
  Submit another sample/order item for another test until the instrument's
  multi-test worklist representation is confirmed.
- Reposting the same `sample_id` replaces it and makes it ready again.

Ready items survive restarts. A matching positive `ACK^Q03` changes an item to
`acknowledged` and consumes it. A negative ACK changes it to `rejected`. If the
connection closes before an ACK, the current item remains ready for retry.

### Read status or cancel

```http
GET    /api/v1/orders/cyanvision/<sample_id>
DELETE /api/v1/orders/cyanvision/<sample_id>
```

## Example request

```bash
curl -X POST "http://172.16.2.4:5052/api/v1/orders/selectra" \
  -H "Content-Type: application/json" \
  -H "X-API-TOKEN: replace-with-the-configured-secret" \
  --data '{"external_order_id":"LIS-ORDER-7812","sample_id":"2608130012","patient_id":"PAT-4821","family_name":"BENCH","given_name":"PATIENT","birth_date":"1980-06-15","sex":"F","tests":[{"service_tarification_id":392},{"param_id":99953,"service_tarification_id":528}]}'
```

A successful Selectra stage returns only a small acknowledgement:

```json
{"analyzer":"selectra","ok":true,"sample_id":"2608130012","state":"staged"}
```

Patient data, database timestamps, and raw protocol frames are deliberately not
echoed to the sending server. Validation failures return `400` and do not store
or arm anything.

CYANVision staging uses the same compact acknowledgement shape, with
`"state":"ready"` because its queue is delivered through the operator's
explicit **Load from LIS** action on the analyzer.
