# Inbound analyzer order API

## Purpose

An authorized clinic server stages orders in the bridge on TCP port 5052. The API response is intentionally small and does not echo patient payloads or protocol frames.

Base URL:

```text
http://<bridge-IP>:5052/api/v1/orders
```

## Authentication

Every endpoint requires:

```http
X-API-TOKEN: <bridge-generated-secret>
Content-Type: application/json
```

Responses:

- `401`: missing or invalid token.
- `503`: bridge authentication is unavailable.
- `400`: validation, unknown mapping, ambiguity, or unsupported value.
- `404`: authenticated request, order not found.
- `201`: order successfully staged/replaced.
- `200`: successful read/cancel operation where applicable.

The token is generated locally at startup and stored in `selectra_host_query/data/order_api_token.txt`. Never commit it.

## Endpoints

| Analyzer | Stage/replace | Read | Cancel |
|---|---|---|---|
| Selectra | `POST /selectra` | `GET /selectra/<sample_id>` | `DELETE /selectra/<sample_id>` |
| CYANVision | `POST /cyanvision` | `GET /cyanvision/<sample_id>` | `DELETE /cyanvision/<sample_id>` |

## Compact success response

```json
{
  "ok": true,
  "analyzer": "selectra",
  "sample_id": "2608130012",
  "state": "staged"
}
```

`state` describes bridge staging only.

## Selectra example

```json
{
  "external_order_id": "LIS-ORDER-7812",
  "sample_id": "2608130012",
  "patient_id": "PAT-4821",
  "family_name": "BENCH",
  "given_name": "PATIENT",
  "birth_date": "1980-06-15",
  "sex": "F",
  "tests": [
    {"service_tarification_id": 392},
    {"param_id": 99953, "service_tarification_id": 528}
  ]
}
```

The bridge reverse-maps clinic identifiers to installed Selectra codes. Ambiguous tarifications require `param_id`. Each test is validated independently: a request containing both valid and invalid tests returns `201`, stages only the valid tests, and includes a persistent `rejected_tests` warning. If every test is invalid, the bridge returns `400` and stages nothing. Patient ID and optional demographics may be retained locally while omitted from the analyzer payload according to outbound-field settings.

## CYANVision example

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

Current conservative model: one test per worklist item. The clinic identifier
resolves first to a result code and then to a separate numeric outbound
ProgramID. Current field-trial values are ALP `3`, CRE `11`, and LIPASE `23`.
XN-330 has no order endpoint; it remains receive-only.

## Replacement and duplicate semantics

The analyzer-specific sample ID is the local identity key. Reposting the same sample ID is an intentional upsert: stored content is replaced and the order returns to its analyzer-specific ready/staged state. It does not create two independently deliverable orders with the same key.

If the clinic needs two test groups for one physical sample, combine them into one analyzer order when the protocol supports multiple test identifiers. Do not append arbitrary suffixes unless the analyzer will query that exact suffixed ID.

## Full field rules

The older root-level `ORDER_DOWNLOAD_API.md` contains additional compatibility notes and exact limits. When modifying the API, update this canonical overview and the detailed contract together until the older file is retired.
