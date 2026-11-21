<#
Job Scanner's on/off switch for this laptop.

    jobscan off       stop everything now; it stays off until you turn it on
    jobscan on
    jobscan status

jobscan.cmd and the Start menu / desktop shortcuts run this file (laptop\install_switch.ps1 puts
them there). It runs on Windows PowerShell 5.1, which is part of Windows, so it works even if
Python or the .venv is broken.

OFF, in this order:
  1. writes state\OFF. run_laptop.ps1 and every `python -m jobscan` refuse to start while it is
     there, and a run already going opens no more boards;
  2. disables the "Job Scanner laptop run" scheduled task. Windows then starts nothing at all (no
     window, no hidden process) at log in, unlock, network connect or on the hour, and a disabled
     task stays disabled through restarts;
  3. stops the task if it is running, and kills every scanner process together with everything it
     started: run_laptop.ps1, the scanner's Python (.venv), Playwright's driver and the Chrome
     windows on the scanner's own profiles (browser-profile\). Your own Chrome isn't touched;
  4. checks that the task is disabled, state\OFF is there and no scanner process is left. Only then
     does it say "OFF - verified"; otherwise it says "NOT OFF", what is wrong, and exits with 1.
If it stopped a scan that was writing to the sheet, it also clears that scan's claim on the
sheet's Lock tab (python -m jobscan.release_lock), so GitHub's next run doesn't wait for it.

ON removes state\OFF and enables the task again. STATUS says which it is, and exits with 1 if it
is neither fully on nor fully off.
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)][ValidateSet("off", "on", "status")][string]$Action = "status",
    [string]$TaskName = "Job Scanner laptop run",
    [string]$Repo = "",   # the scanner's folder; default: the one this file is in
    [switch]$Wait         # keep the window open until a key is pressed (the shortcuts)
)

# (Not a parameter default: Windows PowerShell leaves $PSScriptRoot empty there under -File.)
if (-not $Repo) { $Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
$OffFile = Join-Path $Repo "state\OFF"


# ---------- which processes are the scanner's ----------

function Get-ScannerMarkers([string]$Root) {
    # Text in a process's path or command line that makes it the scanner's: the .venv (Python,
    # Playwright's driver), the Chrome profiles and the launcher, with the folder written both
    # long and short (C:\Users\ADITYA~1\...).
    $roots = @($Root.TrimEnd('\'))
    try { $roots += (New-Object -ComObject Scripting.FileSystemObject).GetFolder($Root).ShortPath.TrimEnd('\') } catch { }
    foreach ($r in ($roots | Select-Object -Unique)) {
        "$r\.venv\"
        "$r\browser-profile"
        "$r\laptop\run_laptop.ps1"
    }
}

function Select-ScannerProcesses {
    # From a process list (Win32_Process objects), the scanner's processes and everything they
    # started, oldest first. Pure: the tests call it with made-up processes.
    param([object[]]$Processes, [string[]]$Markers, [int[]]$Exclude = @())
    $marks = @($Markers | ForEach-Object { $_.Replace('/', '\').ToLowerInvariant() })
    $hit = @{}
    foreach ($p in $Processes) {
        $id = [int]$p.ProcessId
        if ($Exclude -contains $id) { continue }
        $text = ("$($p.ExecutablePath) $($p.CommandLine)").Replace('/', '\').ToLowerInvariant()
        $mine = $false
        foreach ($m in $marks) { if ($text.Contains($m)) { $mine = $true; break } }
        # python -m jobscan, even run by a Python outside the .venv
        if (-not $mine -and "$($p.Name)" -match '^(pythonw?[0-9.]*|py)\.exe$' -and "$($p.CommandLine)" -match '(^|\s)-m\s+jobscan\b') {
            $mine = $true
        }
        if ($mine) { $hit[$id] = $p }
    }
    # Their children, grandchildren... A child can't be older than its parent: an older process
    # naming the same parent ID was started by an earlier process whose ID Windows has reused.
    do {
        $added = 0
        foreach ($p in $Processes) {
            $id = [int]$p.ProcessId
            if ($hit.ContainsKey($id) -or $Exclude -contains $id) { continue }
            $parent = $hit[[int]$p.ParentProcessId]
            if ($parent -and $p.CreationDate -and $parent.CreationDate -and $p.CreationDate -ge $parent.CreationDate) {
                $hit[$id] = $p
                $added++
            }
        }
    } while ($added)
    @($hit.Values | Sort-Object CreationDate)
}

function Get-OwnProcessIds([object[]]$Processes) {
    # This script and the processes that started it (the shortcut's window, your terminal).
    $byId = @{}
    foreach ($p in $Processes) { $byId[[int]$p.ProcessId] = $p }
    $ids = @($PID)
    $id = $PID
    while ($byId.ContainsKey($id)) {
        $id = [int]$byId[$id].ParentProcessId
        if (-not $id -or $ids -contains $id) { break }
        $ids += $id
    }
    $ids
}

function Find-ScannerProcesses {
    $all = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, CreationDate -ErrorAction Stop)
    Select-ScannerProcesses -Processes $all -Markers @(Get-ScannerMarkers $Repo) -Exclude @(Get-OwnProcessIds $all)
}

function Stop-ScannerProcesses([object[]]$Targets) {
    # Oldest (parents) first, so nothing gets to start something new. Returns the ones still alive.
    foreach ($p in $Targets) {
        $id = [int]$p.ProcessId
        $live = Get-Process -Id $id -ErrorAction SilentlyContinue
        if (-not $live) { continue }
        try { $started = $live.StartTime } catch { $started = $null }
        if ($started -and $p.CreationDate -and [math]::Abs(($started - $p.CreationDate).TotalSeconds) -gt 2) {
            continue   # it ended and Windows gave its ID to another program
        }
        try { Stop-Process -Id $id -Force -ErrorAction Stop } catch { }
    }
    foreach ($p in $Targets) {
        if (Get-Process -Id ([int]$p.ProcessId) -ErrorAction SilentlyContinue) { $p }
    }
}

function Format-Processes([object[]]$Processes) {
    $groups = $Processes | Group-Object Name | Sort-Object Name | ForEach-Object {
        if ($_.Count -gt 1) { "$($_.Name) x$($_.Count)" } else { $_.Name }
    }
    $groups -join ", "
}


# ---------- the task and state\OFF ----------

function Get-Task {
    # The scheduled task, or $null if it isn't installed. Any other failure throws: unknown isn't off.
    try { Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Select-Object -First 1 }
    catch {
        if ($_.CategoryInfo.Category -eq "ObjectNotFound") { return $null }
        throw
    }
}

function Get-SwitchState {
    $s = [pscustomobject]@{
        Task = $null; TaskError = ""; TaskOff = $false; TaskOn = $false
        OffFile = $false; OffText = ""
        Processes = @(); ProcessError = ""
        IsOff = $false; IsOn = $false
    }
    try {
        $s.Task = Get-Task
        $enabled = $s.Task -and $s.Task.Settings.Enabled
        $s.TaskOff = (-not $s.Task) -or ((-not $enabled) -and "$($s.Task.State)" -eq "Disabled")
        $s.TaskOn = $s.Task -and $enabled -and "$($s.Task.State)" -ne "Disabled"
    } catch { $s.TaskError = $_.Exception.Message }
    $s.OffFile = Test-Path -LiteralPath $OffFile
    if ($s.OffFile) {
        try { $s.OffText = "$(Get-Content -LiteralPath $OffFile -Raw -ErrorAction Stop)".Trim() } catch { }
    }
    try { $s.Processes = @(Find-ScannerProcesses) } catch { $s.ProcessError = $_.Exception.Message }
    $s.IsOff = $s.TaskOff -and $s.OffFile -and -not $s.ProcessError -and $s.Processes.Count -eq 0
    $s.IsOn = $s.TaskOn -and -not $s.OffFile
    $s
}

function Write-Check([bool]$Ok, [string]$Text) {
    if ($Ok) { Write-Host "  [ok] $Text" -ForegroundColor Green } else { Write-Host "  [!!] $Text" -ForegroundColor Red }
}

function Write-OffChecks($s) {
    if ($s.TaskError) { Write-Check $false "scheduled task: couldn't read it ($($s.TaskError))" }
    elseif (-not $s.Task) { Write-Check $true "scheduled task `"$TaskName`": not installed, so nothing starts the scanner" }
    elseif ($s.TaskOff) { Write-Check $true "scheduled task `"$TaskName`": disabled - Windows won't start it, even after a restart" }
    else { Write-Check $false "scheduled task `"$TaskName`": $($s.Task.State) (enabled: $($s.Task.Settings.Enabled))" }
    if ($s.OffFile) { Write-Check $true "state\OFF: there - the scanner refuses to start" }
    else { Write-Check $false "state\OFF: missing" }
    if ($s.ProcessError) { Write-Check $false "scanner processes: couldn't list them ($($s.ProcessError))" }
    elseif ($s.Processes.Count) { Write-Check $false "scanner processes still running: $(Format-Processes $s.Processes) (IDs $(($s.Processes | ForEach-Object { $_.ProcessId }) -join ', '))" }
    else { Write-Check $true "scanner processes: none running" }
}


# ---------- off, on, status ----------

function Clear-SheetLock {
    # A scan was killed: if it was writing to the sheet, its claim on the Lock tab is left behind.
    $py = Join-Path $Repo ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $py)) { return "skipped (no .venv)" }
    $out = [IO.Path]::GetTempFileName()
    $err = [IO.Path]::GetTempFileName()
    try {
        $p = Start-Process -FilePath $py -ArgumentList "-m", "jobscan.release_lock" -WorkingDirectory $Repo `
            -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
        $null = $p.Handle   # Windows PowerShell only keeps the exit code if the handle was read
        if (-not $p.WaitForExit(30000)) {
            $null = Stop-ScannerProcesses @(Find-ScannerProcesses)
            return "gave up after 30 seconds (no internet?); GitHub's next run waits until the claim expires (40 minutes)"
        }
        $text = "$(Get-Content -LiteralPath $out -Raw)".Trim()
        if ($p.ExitCode -eq 0 -and $text) { return $text }
        $last = "$(Get-Content -LiteralPath $err -Tail 1)".Trim()
        return "couldn't check it ($last); GitHub's next run waits at most until the claim expires (40 minutes)"
    } finally {
        Remove-Item -LiteralPath $out, $err -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-Off {
    $problems = New-Object System.Collections.ArrayList

    # 1. state\OFF first: a start already on its way sees it and stops by itself.
    try {
        if (-not (Test-Path -LiteralPath $OffFile)) {
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OffFile) | Out-Null
            $since = (Get-Date).ToString("dd MMM yyyy HH:mm", [Globalization.CultureInfo]::InvariantCulture)
            [IO.File]::WriteAllText($OffFile, "off since $since")
        }
    } catch { [void]$problems.Add("couldn't write state\OFF: $($_.Exception.Message)") }

    # 2. The scheduled task: disabled, so Windows starts nothing; stopped, if it is running.
    try {
        if (Get-Task) {
            Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
            try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop } catch { }
        }
    } catch { [void]$problems.Add("couldn't disable the scheduled task: $($_.Exception.Message)") }

    # 3. Kill, until the process list has come back empty twice in a row, 1.5 seconds apart.
    $killed = @{}
    $empty = 0
    for ($round = 1; $round -le 20 -and $empty -lt 2; $round++) {
        try { $found = @(Find-ScannerProcesses) }
        catch { [void]$problems.Add("couldn't list processes: $($_.Exception.Message)"); break }
        if ($found.Count) {
            $empty = 0
            foreach ($p in $found) { $killed[[int]$p.ProcessId] = $p }
            $null = Stop-ScannerProcesses $found
            Start-Sleep -Milliseconds 300
        } else {
            $empty++
            if ($empty -lt 2) { Start-Sleep -Milliseconds 1500 }
        }
    }
    $lock = ""
    if ($killed.Values | Where-Object { "$($_.Name)" -match '^pythonw?[0-9.]*\.exe$' }) {
        Write-Host "A scan was running and has been stopped; checking the sheet's Lock tab..."
        $lock = Clear-SheetLock
    }

    # 4. Check.
    $s = Get-SwitchState
    Write-Host ""
    if ($s.IsOff) {
        Write-Host "OFF - verified." -ForegroundColor Green
    } else {
        Write-Host "NOT OFF - something is still wrong:" -ForegroundColor Red
    }
    Write-OffChecks $s
    foreach ($problem in $problems) { Write-Check $false $problem }
    if ($killed.Count) { Write-Host "  Stopped $($killed.Count) scanner processes: $(Format-Processes @($killed.Values))." }
    if ($lock) { Write-Host "  Sheet lock: $lock" }
    Write-Host ""
    if ($s.IsOff) {
        Write-Host "It stays off, through restarts, until you run:  jobscan on   (or the `"Job Scanner ON`" shortcut)"
        return 0
    }
    Write-Host "Run `"jobscan off`" again. If it still says NOT OFF, turn the task off by hand in Task Scheduler" -ForegroundColor Red
    Write-Host "(`"$TaskName`" > Disable) and end the processes listed above in Task Manager." -ForegroundColor Red
    return 1
}

function Invoke-On {
    $problems = New-Object System.Collections.ArrayList
    try {
        if (Test-Path -LiteralPath $OffFile) { Remove-Item -LiteralPath $OffFile -Force -Recurse -ErrorAction Stop }
    } catch { [void]$problems.Add("couldn't remove state\OFF: $($_.Exception.Message)") }
    $task = $null
    try {
        $task = Get-Task
        if ($task) { Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null }
    } catch { [void]$problems.Add("couldn't enable the scheduled task: $($_.Exception.Message)") }

    $s = Get-SwitchState
    Write-Host ""
    if ($s.IsOn) { Write-Host "ON." -ForegroundColor Green } else { Write-Host "NOT ON - something is wrong:" -ForegroundColor Red }
    Write-OnChecks $s
    foreach ($problem in $problems) { Write-Check $false $problem }
    if (-not $s.Task -and -not $s.TaskError) {
        Write-Host "  The scheduled task isn't installed: run  powershell -ExecutionPolicy Bypass -File laptop\install_task.ps1" -ForegroundColor Yellow
    }
    if ($s.IsOn) { return 0 }
    return 1
}

function Write-OnChecks($s) {
    if ($s.TaskError) { Write-Check $false "scheduled task: couldn't read it ($($s.TaskError))" }
    elseif (-not $s.Task) { Write-Check $false "scheduled task `"$TaskName`": not installed" }
    elseif ($s.TaskOn) {
        $next = ""
        try {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop
            if ($info.NextRunTime) { $next = "; next hourly start $($info.NextRunTime.ToString('HH:mm'))" }
        } catch { }
        Write-Check $true "scheduled task `"$TaskName`": enabled (starts at log in, unlock, network connect and every hour$next)"
    } else { Write-Check $false "scheduled task `"$TaskName`": $($s.Task.State) (enabled: $($s.Task.Settings.Enabled))" }
    if ($s.OffFile) { Write-Check $false "state\OFF: still there ($($s.OffText))" } else { Write-Check $true "state\OFF: not there" }
    if ($s.ProcessError) { Write-Host "  - scanner processes: couldn't list them ($($s.ProcessError))" }
    elseif ($s.Processes.Count) { Write-Host "  - a scan is running now: $(Format-Processes $s.Processes)" }
    else { Write-Host "  - scanner processes: none running right now" }
}

function Show-Status {
    $s = Get-SwitchState
    Write-Host ""
    if ($s.IsOff) {
        Write-Host "Job Scanner is OFF ($($s.OffText))." -ForegroundColor Green
        Write-OffChecks $s
        Write-Host ""
        Write-Host "Turn it on with:  jobscan on"
        return 0
    }
    if ($s.IsOn) {
        Write-Host "Job Scanner is ON." -ForegroundColor Yellow
        Write-OnChecks $s
        Write-Host ""
        Write-Host "Turn it off with:  jobscan off"
        return 0
    }
    Write-Host "Job Scanner is PARTLY OFF - not safe to rely on:" -ForegroundColor Red
    Write-OffChecks $s
    Write-Host ""
    Write-Host "Run  jobscan off  (or  jobscan on)  to make it one or the other." -ForegroundColor Red
    return 1
}


# ---------- main ----------

if ($MyInvocation.InvocationName -eq '.') { return }   # dot-sourced by switch_test\run_tests.ps1: functions only

$code = 2
try {
    switch ($Action) {
        "off" { $code = Invoke-Off | Select-Object -Last 1 }
        "on" { $code = Invoke-On | Select-Object -Last 1 }
        "status" { $code = Show-Status | Select-Object -Last 1 }
    }
} catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "The switch didn't finish: run  jobscan status  to see where things are." -ForegroundColor Red
    $code = 2
}
if ($Wait) {
    Write-Host ""
    Write-Host "Press any key to close this window."
    try { [void][Console]::ReadKey($true) } catch { }
}
exit $code
