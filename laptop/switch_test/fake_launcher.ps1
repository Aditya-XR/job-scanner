# run_tests.ps1's stand-in for run_laptop.ps1, started by a throwaway scheduled task: the same
# chain (task -> powershell -> cmd -> .venv Python -> Chrome) around fake_scan.py instead of a scan.
param([string]$Ready, [string]$Mode = "chrome")
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
cmd /c "`"$repo\.venv\Scripts\python.exe`" `"$PSScriptRoot\fake_scan.py`" `"$Ready`" $Mode"
exit $LASTEXITCODE
