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

The recent history must contain the implementation commit:

```text
443c3d4 Add persistent analyzer order APIs
```

## 2. Generate the token

The token is not supplied by GitHub or either analyzer. Generate it once on
the Windows bridge server. The following commands create 32 cryptographically
random bytes and encode them as a token:

```powershell
$tokenBytes = New-Object byte[] 32
$tokenGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$tokenGenerator.GetBytes($tokenBytes)
$orderApiToken = [Convert]::ToBase64String($tokenBytes)
$tokenGenerator.Dispose()
$orderApiToken
```

Copy the printed value into an approved password manager. Treat it like a
password. Do not put it in Git, source files, screenshots, chat messages, or
ordinary log files.

The same value must be configured in exactly two places:

1. The `LaboBridge` NSSM service as `LABO_ORDER_API_TOKEN`.
2. The authorized sending server, which places it in the `X-API-TOKEN`
   request header.

## 3. Configure the NSSM service

Open the service editor:

```powershell
C:\nssm\win64\nssm.exe edit LaboBridge
```

In the **Environment** tab, add this entry using the generated value:

```text
LABO_ORDER_API_TOKEN=<generated-token>
```

Use the NSSM editor rather than replacing `AppEnvironmentExtra` from the
command line, because the service may already have other environment entries
that must be preserved.

Start the bridge again:

```powershell
C:\nssm\win64\nssm.exe start LaboBridge
C:\nssm\win64\nssm.exe status LaboBridge
```

The process startup output reports `orders-api ENABLED`. The token is read at
process startup, so adding or rotating it always requires restarting
`LaboBridge`.

## 4. Configure the sending server

Store the same token in that server's secret/environment configuration. Every
request must contain:

```http
X-API-TOKEN: <generated-token>
Content-Type: application/json
```

Do not reuse the bridge's outbound machine-result API token. These are two
different integrations and must have different credentials.

## 5. Network access

The order API listens with the existing web console on TCP port `5052`:

```text
http://172.16.2.4:5052/api/v1/orders/
```

Allow port `5052` only from the authorized sending server or trusted internal
subnet. Use TLS through a reverse proxy if traffic crosses an untrusted
network. The analyzer ports remain unchanged:

- Selectra: TCP `6003`
- CYANVision: TCP `6004`

## 6. Verify authentication before sending patient data

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

## 7. Persistence and backups

Orders and the protocol audit trail are stored in:

```text
selectra_host_query/data/host_query.db
```

The database survives process restarts and can contain patient identifiers.
It is already excluded from Git. Include it in the server's protected backup
policy and restrict filesystem access to authorized service administrators.

## Token rotation

To rotate the credential:

1. Generate a new token using step 2.
2. Update the sending server and the NSSM environment entry in a coordinated
   maintenance window.
3. Restart `LaboBridge`.
4. Repeat the authentication test.
5. Revoke the old secret from the sending server's configuration.
