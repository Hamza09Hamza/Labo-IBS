# Analyzer order API setup

This guide configures the Windows LaboBridge server to receive Selectra and
CYANVision orders from another authorized server. The endpoint schemas are in
`ORDER_DOWNLOAD_API.md`.

## 1. Update LaboBridge

Run these commands from an Administrator PowerShell in the repository:

```powershell
C:\nssm\win64\nssm.exe stop LaboBridge
git pull --ff-only origin main
git log -5 --oneline
```

Confirm that the automatic token support exists:

```powershell
Test-Path ".\selectra_host_query\order_api_auth.py"
```

Expected output: `True`.

## 2. Start the bridge and obtain its token

Start the service normally:

```powershell
C:\nssm\win64\nssm.exe start LaboBridge
C:\nssm\win64\nssm.exe status LaboBridge
```

At its first startup, LaboBridge automatically generates a cryptographically
random token. It stores and reuses it at:

```text
selectra_host_query/data/order_api_token.txt
```

Read it locally from Administrator PowerShell:

```powershell
$orderApiToken = (Get-Content ".\selectra_host_query\data\order_api_token.txt" -Raw).Trim()
$orderApiToken
```

The entire `selectra_host_query/data/` directory is excluded from Git. Treat
the token like a password: transfer it directly to the authorized sending
server and store it in that server's secret/environment configuration. Do not
paste it into `ORDER_DOWNLOAD_API.md`, source code, commits, screenshots, chat,
or ordinary logs. Deleting a committed secret later does not remove it from
Git history.

## 3. Configure the sending server

Store the same token in that server's secret/environment configuration. Every
request must contain:

```http
X-API-TOKEN: <generated-token>
Content-Type: application/json
```

Do not reuse the bridge's outbound machine-result API token. These are two
different integrations and must have different credentials.

## 4. Network access

The order API listens with the existing web console on TCP port `5052`:

```text
http://172.16.2.4:5052/api/v1/orders/
```

Allow port `5052` only from the authorized sending server or trusted internal
subnet. Use TLS through a reverse proxy if traffic crosses an untrusted
network. The analyzer ports remain unchanged:

- Selectra: TCP `6003`
- CYANVision: TCP `6004`

## 5. Verify authentication before sending patient data

First send a request without a token. It must return HTTP `401`:

```powershell
try {
  Invoke-WebRequest `
    -Method Get `
    -Uri "http://172.16.2.4:5052/api/v1/orders/selectra/DOES-NOT-EXIST" `
    -UseBasicParsing
} catch {
  $_.Exception.Response.StatusCode.value__
}
```

Expected output:

```text
401
```

Then test the configured token. A `404` response is correct here: it proves
authentication succeeded and only the deliberately nonexistent order was not
found.

```powershell
$headers = @{ "X-API-TOKEN" = $orderApiToken }
try {
  Invoke-WebRequest `
    -Method Get `
    -Uri "http://172.16.2.4:5052/api/v1/orders/selectra/DOES-NOT-EXIST" `
    -Headers $headers `
    -UseBasicParsing
} catch {
  $_.Exception.Response.StatusCode.value__
}
```

Expected output:

```text
404
```

After authentication is verified, use a non-production patient and the JSON
examples in `ORDER_DOWNLOAD_API.md` for the first end-to-end analyzer test.

## 6. Persistence and backups

Orders and the protocol audit trail are stored in:

```text
selectra_host_query/data/host_query.db
```

The database and API token survive process restarts and are excluded from Git.
The database can contain patient identifiers. Include both files in the
server's protected backup policy and restrict filesystem access to authorized
service administrators.

## Token rotation

Rotate only during a coordinated maintenance window. Stop `LaboBridge`, move
`order_api_token.txt` to a protected backup location, and start the service.
It creates a new token automatically. Read the new value using step 2, update
the sending server, repeat the authentication test, and securely destroy the
old copy after validation.
