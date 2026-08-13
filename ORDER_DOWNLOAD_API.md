# Analyzer order download API

The order API accepts work from another authorized server, validates it,
stores it in the local SQLite database, and marks it ready for an analyzer to
pull. It is served by `run_all.py` on:

```text
http://<bridge-IP>:5052/api/v1/orders/
```

This API does not push a connection to either analyzer:

- Selectra sends a Host Query containing a sample ID. The bridge returns the
  exact ready Selectra order with that sample ID.
- CYANVision sends `QRY^Q02` when the operator presses **Patient Worklist ->
  Load from LIS**. The bridge returns the ready CYANVision queue in creation
  order, waiting for `ACK^Q03` between `DSR^Q03` items. The final item has an
  empty `DSC` continuation pointer.

## Authentication

Every endpoint in this document requires the private token configured during
bridge setup. Every order API request must include:

```http
X-API-TOKEN: <the-same-secret>
Content-Type: application/json
```

If the variable is absent, the endpoints return `503`. A missing or incorrect
header returns `401`. Token generation, NSSM configuration, firewall setup,
deployment, and connection testing are documented separately in
`ORDER_DOWNLOAD_SETUP.md`.

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
  "specimen_type": "SERUM",
  "tests": ["Creatinine", "SGPT"]
}
```

Rules:

- `sample_id` is required, case-sensitive, and limited to 12 characters.
- `family_name` plus a space plus `given_name` must fit the Selectra's
  20-character patient-name limit.
- `birth_date` uses `YYYY-MM-DD`; `sex` is `M`, `F`, or `U`.
- `tests` is a non-empty array of installed Selectra method names or their
  confirmed short codes. Unknown values are rejected before staging.
- Reposting the same `sample_id` replaces the stored content and makes it
  ready again. `external_order_id` is optional correlation metadata.

An API order does not require the manual **Arm exact-ID replies** switch. It is
individually ready and survives a LaboBridge restart. Once Selectra transport-
ACKs the response, it becomes `transport_acknowledged` and is no longer ready.

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
  "test_code": "ALP"
}
```

Rules:

- Values must use printable ASCII because the documented outbound message
  declares `ASCII` in `MSH-18`.
- `sex` is `M` or `F`.
- `test_code` must exactly match a CYANVision code already curated or received
  by the bridge. It is sent as `DSP|8||<test_code>|||`; numeric NTE Program IDs
  are not substituted.
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
  --data '{"external_order_id":"LIS-ORDER-7812","sample_id":"2608130012","patient_id":"PAT-4821","family_name":"BENCH","given_name":"PATIENT","birth_date":"1980-06-15","sex":"F","specimen_type":"SERUM","tests":["Creatinine","SGPT"]}'
```

A successful stage returns HTTP `201`, `state: "ready"`, the persisted order,
and a protocol preview. Validation failures return `400` and do not store or
arm anything.
