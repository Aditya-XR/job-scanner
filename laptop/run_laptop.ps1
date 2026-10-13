# The daily laptop run: job boards that refuse GitHub's servers or need a real browser window
# (python -m jobscan --group laptop). Started by the "Job Scanner laptop run" scheduled task,
# which laptop/install_task.ps1 registers. One log file a day in logs/.
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
New-Item -ItemType Directory -Force -Path logs | Out-Null
$log = Join-Path $repo ("logs\laptop-{0:yyyy-MM-dd}.log" -f (Get-Date))
$env:PYTHONUTF8 = "1"
# cmd does the redirect: PowerShell would turn Python's log lines on stderr into errors.
cmd /c "`"$repo\.venv\Scripts\python.exe`" -m jobscan --group laptop >> `"$log`" 2>&1"
exit $LASTEXITCODE
