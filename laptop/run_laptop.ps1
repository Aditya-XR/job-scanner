# The laptop run: job boards that refuse GitHub's servers or need a real browser window
# (python -m jobscan --group laptop --if-due). Started by the "Job Scanner laptop run" scheduled
# task, which laptop/install_task.ps1 registers, at log in, unlock, network connect and every
# hour; each start reads only the boards that are due. One log file a day in logs/.
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
New-Item -ItemType Directory -Force -Path logs | Out-Null
$log = Join-Path $repo ("logs\laptop-{0:yyyy-MM-dd}.log" -f (Get-Date))
# Switched off (jobscan off, laptop\scanner_switch.ps1): start nothing. The task is disabled then
# too; this catches a start that was already on its way.
if (Test-Path -LiteralPath (Join-Path $repo "state\OFF")) {
    try { Add-Content -LiteralPath $log -Value ("{0:yyyy-MM-dd HH:mm} Switched OFF (state\OFF): nothing started." -f (Get-Date)) } catch { }
    exit 0
}
$env:PYTHONUTF8 = "1"
# cmd does the redirect: PowerShell would turn Python's log lines on stderr into errors.
cmd /c "`"$repo\.venv\Scripts\python.exe`" -m jobscan --group laptop --if-due >> `"$log`" 2>&1"
exit $LASTEXITCODE
