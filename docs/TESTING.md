# Testing and analyzer validation

## Automated suite

From the repository virtual environment:

```bash
python -m unittest discover -s tests -v
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Also check syntax and patch whitespace before committing:

```bash
python -m compileall -q labo_bridge selectra_host_query cyanvision_worklist
git diff --check
git status --short
```

XN-330 testing now covers result receiving/decoding only. There is no XN-330
Host Query package, API, or port-5052 workflow to validate.

## Field-validation ladder

Use a non-production patient and progress one step at a time:

1. **Connectivity:** analyzer connects to correct port.
2. **Transport:** ENQ/frame/EOT and ACK behavior is correct.
3. **Query recognition:** trace shows parsed sample ID.
4. **Exact match:** intended staged order matches; a near-miss does not.
5. **Payload receipt:** analyzer transport-ACKs response.
6. **Application acceptance:** analyzer does not reject it.
7. **Display verification:** operator sees correct sample, name, demographics, and tests.
8. **Execution:** only intended tests are selected.
9. **Round trip:** resulting upload returns with correct identifier and mapping.
10. **Retry/idempotency:** duplicate query, disconnect, NAK, restart, and cancellation behave safely.

Transport ACK alone completes only step 5.

## Required negative tests

- missing token -> 401;
- unknown clinic test mapping -> 400 and no stored order;
- unarmed exact query -> trace only, no order payload;
- armed wrong ID -> no payload;
- duplicate POST -> deterministic replacement, no double order;
- analyzer NAK -> retry limit and error state;
- disconnect before ACK -> safe retry state where protocol permits;
- PostgreSQL unavailable -> visible warning and understood data-loss behavior;
- SQLite/order store unavailable -> 5052 failure without analyzer payload invention.

## Before pushing

Confirm generated data is absent:

```bash
git status --short
git diff --cached --name-only
```

Do not commit `results/`, `results 2/`, captures, databases, tokens, `.venv`, or `node_modules`.
