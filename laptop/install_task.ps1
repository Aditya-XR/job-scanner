# Registers (or updates) the "Job Scanner laptop run" scheduled task for the current user.
# The laptop isn't on at a fixed time, so the task starts often: when you log in, when you unlock
# the laptop, when it connects to a network, and every hour while you're logged in. Each start
# reads only the boards that are due (jobscan/laptop.py), so most starts finish in a second.
# It runs only while you are logged in, because Naukri needs a visible browser window.
#
#   powershell -ExecutionPolicy Bypass -File laptop\install_task.ps1
#   Start-ScheduledTask "Job Scanner laptop run"        # check now
#   Unregister-ScheduledTask "Job Scanner laptop run"   # remove it
param([string]$TaskName = "Job Scanner laptop run")
$repo = Split-Path -Parent $PSScriptRoot
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $repo `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$repo\laptop\run_laptop.ps1`""

$cim = "root/Microsoft/Windows/TaskScheduler"
$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
# Every hour from midnight, with no end. A start missed while the laptop slept runs on waking.
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
$unlock = New-CimInstance -ClientOnly -CimClass (Get-CimClass -Namespace $cim -ClassName MSFT_TaskSessionStateChangeTrigger) `
    -Property @{StateChange = [uint32]8; UserId = $user; Delay = "PT1M"}      # 8: the session was unlocked
$network = "Microsoft-Windows-NetworkProfile/Operational"
$online = New-CimInstance -ClientOnly -CimClass (Get-CimClass -Namespace $cim -ClassName MSFT_TaskEventTrigger) `
    -Property @{Delay = "PT1M"; Subscription =                                 # event 10000: connected to a network
        "<QueryList><Query Id=`"0`" Path=`"$network`"><Select Path=`"$network`">*[System[(EventID=10000)]]</Select></Query></QueryList>"}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $unlock, $online, $hourly) `
    -Settings $settings -Principal $principal -Force `
    -Description "Job Scanner: reads LinkedIn, Naukri and Foundit into the sheet when they're due (python -m jobscan --group laptop --if-due)." |
    Select-Object TaskName, State

# Task Scheduler's history (each start, skip and failure, with the reason) is off by default, and
# turning it on needs an administrator.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($admin) {
    wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true
} else {
    Write-Host "To keep Task Scheduler's history, run this once in an administrator PowerShell:"
    Write-Host "  wevtutil set-log Microsoft-Windows-TaskScheduler/Operational /enabled:true"
}
