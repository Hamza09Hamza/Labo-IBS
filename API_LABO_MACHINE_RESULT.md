# Labo Machine Result API

## Quick Start

**URL:** `http://localhost:8080/labo/api/machine/result`  
**Method:** POST  
**Token:** `labo@@2025`

## Headers
```
Content-Type: application/json
X-API-TOKEN: labo@@2025
```

## Important
The body must be a **JSON array**, even for one result.

Wrong (causes `End of file expected`):
```json
{ "sample_id": "2607040507", ... },
{ "sample_id": "2607040507", ... }
```

Correct:
```json
[
  { "sample_id": "2607040507", ... },
  { "sample_id": "2607040507", ... }
]
```

## Required Fields
- `sample_id` - Machine sample id (example: `2607040507`)
- `result_value` - Result value as text
- **One of:**
  - `param_id` - LaboParam id
  - `service_tarification_id` - ServiceTarification id (when no `param_id`)

## Optional Fields
- `unit` - Unit from the machine
- `machine_id` - Preferred. Id of `labo.labo_machine`. When set, the API **ignores** `machine` and stores the machine id + its name on the result.
- `machine` - Machine name fallback when `machine_id` is not sent (example: `xn330`)

## Not accepted for matching
- Requests without `param_id` and without `service_tarification_id` are rejected

## Matching Rules
- From `sample_id`, take the first 8 digits and prefix `LAB-`
  - Example: `2607040507` -> appointment number `LAB-26070405`
- **Tube check:** the full `sample_id` must match the exam's tube barcode
  - Format: `{appointmentNumber}{tubeId padded to 2 digits}`
  - Example: appointment `26070397`, FNS on tube `07` → expects `2607039707`
  - Sending `2607039705` for a FNS param returns an error (wrong tube)
- With `param_id`:
  - Find the appointment tarification linked to that `LaboParam` through `labo_test_param`
  - Fill `appointment_tarification_id` + `param_id` in `labo_result`
- With `service_tarification_id` (and no `param_id`):
  - Find the appointment tarification directly where `appointment_tarification.tarification_id = service_tarification_id`
  - Fill only `appointment_tarification_id` in `labo_result`
  - This skips the `labo_test_param` / parameter matching step
- With `machine_id`:
  - Look up `labo_machine` by id
  - **Must** have an enabled technique linking that machine to the exam (`service_tarification` of the matched appointment test)
  - Error if no technique exists for that exam + machine
  - Error if the technique or machine is disabled
  - On success: store `labo_result.labo_machine_id` + machine name, and link `technique_id`
- With `machine` (name only, no `machine_id`):
  - If the name matches a `labo_machine`, same technique rules as `machine_id`
  - If the name does not match any machine, it is stored as free text (legacy)

## Example Request
```json
[
  {
    "sample_id": "2607040507",
    "param_id": 81,
    "result_value": "7.33",
    "unit": "10^3/uL",
    "machine_id": 3
  },
  {
    "sample_id": "2607040507",
    "service_tarification_id": 1205,
    "result_value": "6.59",
    "unit": "10^6/uL",
    "machine": "xn330"
  }
]
```

## Example Response (Success)
```json
{
  "success": true,
  "message": "All machine results saved successfully",
  "total": 2,
  "saved": 2,
  "failed": 0,
  "results": [
    {
      "success": true,
      "message": "Machine result saved successfully",
      "sampleId": "2607040507",
      "appointmentNumber": "LAB-26070405",
      "appointmentId": 123,
      "appointmentTarificationId": 456,
      "paramId": 81,
      "laboResultId": 789,
      "resultValue": "7.33",
      "unit": "10^3/uL",
      "machine": "XN-330",
      "machineId": 3
    }
  ]
}
```

## Common Errors
- `"Invalid token"` - Wrong API token
- `"Request body must be a non-empty JSON array"` - Body is empty or not an array
- `"param_id or service_tarification_id is required"` - Missing both identifiers
- `"sample_id does not match exam tube. Exam 'FNS' expects tube barcode 2607039707 (tube_id=7, ...), got: 2607039705"` - Wrong tube for that exam
- `"Exam has no tube configured: ..."` - Exam has no `labo_tube`
- `"Machine not found for machine_id: ..."` - Unknown `machine_id`
- `"Machine is not configured for this exam (machine_id: ..., exam service_tarification_id: ...)"` - No technique for that machine + exam
- `"Machine/technique is disabled for this exam (machine_id: ..., exam service_tarification_id: ...)"` - Technique or machine disabled
- `"Appointment not found for sample_id: ..."` - No appointment matches `LAB-` + first 8 digits
- `"No appointment tarification found for param_id: ..."` - Param not linked to a test on that appointment
- `"No appointment tarification found for service_tarification_id: ..."` - Service not on that appointment
- `"Cannot update a validated result..."` - Result already validated
- `"Cannot update a locked result..."` - Result is locked

## Postman Setup
1. Method: POST
2. URL: `http://localhost:8080/labo/api/machine/result`
3. Headers:
   - `Content-Type` = `application/json`
   - `X-API-TOKEN` = `labo@@2025`
4. Body: Raw JSON array (see example above)

## Notes
- After save, status becomes `ANALYZED`
- Existing unlocked/non-validated results are updated
- `machine` is stored in `labo.labo_result.machine`
- Every API call is tracked in `labo.labo_api_tracking` with:
  - request fields (`sample_id`, `param_id`, `result_value`, `unit`, `machine`)
  - `success` boolean
  - `message` (success or error)
  - matched ids (`appointment_id`, `appointment_tarification_id`, `labo_result_id`)
  - full `request_json` and `response_json`
  - `created_at`
