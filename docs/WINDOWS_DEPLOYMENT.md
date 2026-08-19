# Windows deployment and NSSM operations

## Deployed layout

The known Windows deployment uses:

```text
C:\Users\administrator\Desktop\labo_bridge
```

NSSM service name:

```text
LaboBridge
```

Known NSSM configuration:

```text
Application:   C:\Users\administrator\Desktop\labo_bridge\.venv\Scripts\python.exe
AppDirectory: C:\Users\administrator\Desktop\labo_bridge
AppParameters:C:\Users\administrator\Desktop\labo_bridge\run_all.py
```

`run_all.py` is therefore the production entry point. Do not separately launch another copy while NSSM is running; duplicate processes will compete for ports 5050, 5052, and 6001–6006.

## First installation

From PowerShell:

```powershell
cd C:\Users\administrator\Desktop\labo_bridge
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Configure PostgreSQL and firewall rules before starting analyzers. Do not run `deploy_seed.py` against an existing production schema without first reviewing its behavior and taking a backup.

## Safe update procedure

Use a fast-forward update when the Windows checkout has no intended local commits:

```powershell
cd C:\Users\administrator\Desktop\labo_bridge
C:\nssm\win64\nssm.exe stop LaboBridge
git status --short
git fetch origin
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
C:\nssm\win64\nssm.exe start LaboBridge
C:\nssm\win64\nssm.exe status LaboBridge
git log -1 --oneline
```

If `git status --short` shows unexpected modifications, do not reset immediately. Preserve them and determine whether they are runtime artifacts, deliberate server configuration, or source edits.

## Recovering a server checkout with unwanted local commits

Only after preserving a backup branch and stopping the service:

```powershell
C:\nssm\win64\nssm.exe stop LaboBridge
git fetch origin
git branch backup-windows-before-sync
git reset --hard origin/main
C:\nssm\win64\nssm.exe start LaboBridge
```

This is destructive to uncommitted tracked changes. Use it only when the target state is explicitly confirmed.

## Common Git editor screen

If `git pull` opens Vim for a merge commit:

- Save and finish: press `Esc`, type `:wq`, press Enter.
- Abort only the editor entry: press `Esc`, type `:cq`, press Enter, then inspect Git state.

Prefer `git pull --ff-only` on the deployment server. It refuses accidental merge commits instead of opening an editor.

## Service checks

```powershell
C:\nssm\win64\nssm.exe status LaboBridge
C:\nssm\win64\nssm.exe get LaboBridge Application
C:\nssm\win64\nssm.exe get LaboBridge AppDirectory
C:\nssm\win64\nssm.exe get LaboBridge AppParameters
Get-NetTCPConnection -State Listen | Where-Object LocalPort -in 5050,5052,6001,6002,6003,6004,6005,6006
```

Expected service state is `SERVICE_RUNNING`. Also verify the HTTP interfaces:

```text
http://172.16.2.4:5050/
http://172.16.2.4:5052/
```

## Locked log files during Git update

If Git says it cannot unlink a tracked log, a running process probably has it open. Stop NSSM, answer `n` if retrying cannot succeed, and inspect why the runtime file is tracked. Runtime logs and captures should be ignored, not versioned.

## API token

On first startup, the bridge creates:

```text
selectra_host_query\data\order_api_token.txt
```

Read it locally:

```powershell
$orderApiToken = (Get-Content ".\selectra_host_query\data\order_api_token.txt" -Raw).Trim()
```

If the file does not exist, confirm that the deployed commit contains `selectra_host_query\order_api_auth.py`, that `run_all.py` started successfully, and that NSSM's working directory is correct.

## Backups

Protect at least:

- PostgreSQL `labo_bridge` schema
- `selectra_host_query/data/host_query.db`
- `selectra_host_query/data/order_api_token.txt`
- approved mapping source in Git
- runtime machine/port configuration

The SQLite order database and packet captures contain patient-identifying data.
