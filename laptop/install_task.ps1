# Registers (or updates) the "Job Scanner laptop run" scheduled task for the current user:
# every day at 08:30, or as soon as the laptop is on after a missed 08:30, only with a network.
# It runs only while you are logged in, because Naukri needs a visible browser window.
#
#   powershell -ExecutionPolicy Bypass -File laptop\install_task.ps1 [-At 09:00]
#   Start-ScheduledTask "Job Scanner laptop run"        # run it now
#   Unregister-ScheduledTask "Job Scanner laptop run"   # remove it
param([string]$At = "08:30")
$repo = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $repo `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$repo\laptop\run_laptop.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "Job Scanner laptop run" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force `
    -Description "Job Scanner: reads LinkedIn, Naukri and Foundit into the sheet (python -m jobscan --group laptop)." |
    Select-Object TaskName, State
