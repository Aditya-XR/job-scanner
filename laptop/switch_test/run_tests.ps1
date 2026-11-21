<#
Tests for Job Scanner's on/off switch (laptop\scanner_switch.ps1). Windows only; run them with
Windows PowerShell, which is what the shortcuts use:

    powershell -ExecutionPolicy Bypass -File laptop\switch_test\run_tests.ps1          # unit tests
    powershell -ExecutionPolicy Bypass -File laptop\switch_test\run_tests.ps1 -Live    # + the real thing

-Live works on the real scheduled task and real processes. It switches the scanner off and on
several times; starts fake scans (the .venv's Python + Playwright + Chrome on
browser-profile\switch-test-*, no site opened; one Chrome window shows for a few seconds), a
throwaway scheduled task, and decoys that must survive (a Chrome on another profile, a plain
Python, a PowerShell whose command line names this folder); starts the real task right before
switching off; runs the shortcuts and `jobscan`. The real task only ever reads boards that are
due, and is killed seconds after it starts. At the end the switch is left as it was found.
#>
param([switch]$Live)
$ErrorActionPreference = "Continue"
$here = $PSScriptRoot
$root = Split-Path -Parent (Split-Path -Parent $here)
$switchPs1 = Join-Path $root "laptop\scanner_switch.ps1"
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$py = Join-Path $root ".venv\Scripts\python.exe"
$realTask = "Job Scanner laptop run"
$testTask = "zz Job Scanner switch test"
$off = Join-Path $root "state\OFF"

$script:passed = 0
$script:failed = @()
function Check([string]$Name, [bool]$Ok, [string]$Detail = "") {
    if ($Ok) { $script:passed++; Write-Host "  pass  $Name" -ForegroundColor Green }
    else { $script:failed += $Name; Write-Host "  FAIL  $Name   $Detail" -ForegroundColor Red }
}

# The switch's functions (Select-ScannerProcesses, Find-ScannerProcesses...), without running it.
. $switchPs1
$Repo = $root


# ---------- unit: which processes are the scanner's ----------

Write-Host "`n== which processes count as the scanner's (made-up process list) =="
function P($Id, $Parent, $Name, $Exe, $Cmd, $Minute) {
    [pscustomobject]@{ ProcessId = $Id; ParentProcessId = $Parent; Name = $Name; ExecutablePath = $Exe
        CommandLine = $Cmd; CreationDate = ([datetime]"2026-11-21 10:00").AddMinutes($Minute) }
}
$R = "C:\Users\Aditya Kumar\jobscan"
$S = "C:\Users\ADITYA~1\jobscan"
$chromeExe = "C:\Program Files\Google\Chrome\Application\chrome.exe"
$procs = @(
    # a scan, from the scheduled task down to Chrome's renderer
    (P 10 2 "powershell.exe" "$ps" "powershell.exe -NoProfile -WindowStyle Hidden -File `"$R\laptop\run_laptop.ps1`"" 1)
    (P 11 10 "cmd.exe" "C:\Windows\system32\cmd.exe" "cmd /c `"`"$R\.venv\Scripts\python.exe`" -m jobscan --group laptop --if-due >> x.log 2>&1`"" 2)
    (P 12 11 "python.exe" "$R\.venv\Scripts\python.exe" "`"$R\.venv\Scripts\python.exe`" -m jobscan --group laptop --if-due" 3)
    (P 13 12 "python.exe" "C:\Python313\python.exe" "C:\Python313\python.exe -m jobscan --group laptop --if-due" 4)
    (P 14 13 "node.exe" "$R\.venv\Lib\site-packages\playwright\driver\node.exe" "node.exe cli.js run-driver" 5)
    (P 15 14 "chrome.exe" $chromeExe "chrome.exe --user-data-dir=`"$R\browser-profile\naukri`" --remote-debugging-pipe" 6)
    (P 16 15 "chrome.exe" $chromeExe "chrome.exe --type=renderer --lang=en-US" 7)
    # left over from a scan whose parents are gone: short folder name, forward slashes, capitals
    (P 17 999 "chrome.exe" $chromeExe "chrome.exe --type=gpu-process --user-data-dir=`"$S\browser-profile\linkedin`"" 8)
    (P 18 998 "python.exe" "C:\Python313\python.exe" "C:\Python313\python.exe -m jobscan --group laptop" 9)
    (P 19 997 "chrome.exe" $chromeExe "chrome.exe --user-data-dir=C:/Users/Aditya Kumar/jobscan/browser-profile/naukri" 10)
    (P 27 996 "python.exe" "C:\USERS\ADITYA KUMAR\JOBSCAN\.VENV\SCRIPTS\PYTHON.EXE" "PYTHON.EXE -c pass" 11)
    # not the scanner's
    (P 20 1 "chrome.exe" $chromeExe "`"$chromeExe`"" 0)                                    # your Chrome
    (P 21 20 "chrome.exe" $chromeExe "chrome.exe --type=renderer" 1)
    (P 30 1 "chrome.exe" $chromeExe "chrome.exe --user-data-dir=`"C:\Users\Aditya Kumar\AppData\Local\Google\Chrome\User Data`"" 1)
    (P 22 1 "Code.exe" "C:\Code\Code.exe" "Code.exe `"$R`"" 2)                              # an editor on the folder
    (P 23 1 "python.exe" "C:\Python313\python.exe" "python -m pytest tests" 3)
    (P 24 1 "python.exe" "C:\Python313\python.exe" "python -m jobscanner_other" 4)
    (P 25 12 "notepad.exe" "C:\Windows\notepad.exe" "notepad.exe" 2)   # names 12 as parent but is older: a reused ID
    (P 26 1 "powershell.exe" "$ps" "powershell.exe -File `"$R\laptop\scanner_switch.ps1`" off -Wait")
    (P 28 1 "cmd.exe" "C:\Windows\system32\cmd.exe" "cmd /c jobscan off" 6)
    (P 29 1 "chrome.exe" $null $null 7)                                                    # unreadable
    (P 31 1 "python.exe" "$R\.venv\Scripts\python.exe" "`"$R\.venv\Scripts\python.exe`" -m jobscan" 1)   # excluded below
)
$markers = @(Get-ScannerMarkers $R)
Check "markers cover the long and the short (8.3) folder name" (($markers -contains "$R\.venv\") -and ($markers -contains "$S\.venv\")) ($markers -join " ; ")
$found = @(Select-ScannerProcesses -Processes $procs -Markers $markers -Exclude @(31))
$got = @($found | ForEach-Object { [int]$_.ProcessId } | Sort-Object)
$want = @(10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 27)
Check "finds the whole scan (launcher, cmd, both Pythons, driver, Chrome and its children) and leftovers" (-not (Compare-Object $got $want)) "got $($got -join ',')"
Check "leaves your Chrome, an editor, pytest, other modules, the switch itself and a reused ID alone" (-not (@(20, 21, 22, 23, 24, 25, 26, 28, 29, 30) | Where-Object { $got -contains $_ }))
Check "never touches an excluded process (this script and what started it)" ($got -notcontains 31)
Check "oldest first, so parents are stopped before their children" ([int]$found[0].ProcessId -eq 10 -and [int]$found[-1].ProcessId -eq 27)
Check "an empty process list finds nothing" (@(Select-ScannerProcesses -Processes @() -Markers $markers).Count -eq 0)


if (-not $Live) {
    Write-Host "`n$($script:passed) passed, $($script:failed.Count) failed (unit tests only; -Live for the real thing)"
    exit [int]($script:failed.Count -gt 0)
}


# ---------- live helpers ----------

function Run-Switch([string]$Action, [string]$Task = $realTask) {
    $t = [Diagnostics.Stopwatch]::StartNew()
    $text = & $ps -NoProfile -ExecutionPolicy Bypass -File $switchPs1 $Action -TaskName $Task | Out-String
    [pscustomobject]@{ Code = $LASTEXITCODE; Text = $text; Seconds = [math]::Round($t.Elapsed.TotalSeconds, 1) }
}
function Get-Tree([int]$RootId) {
    $all = @(Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId)
    $ids = @($RootId)
    do {
        $n = $ids.Count
        $ids = @($ids + @($all | Where-Object { $ids -contains [int]$_.ParentProcessId } | ForEach-Object { [int]$_.ProcessId }) | Select-Object -Unique)
    } while ($ids.Count -gt $n)
    $ids
}
function Alive($Ids) { , @(@($Ids) | Where-Object { $_ } | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue }) }
function Names($Ids) { , @(@($Ids) | Where-Object { $_ } | ForEach-Object { (Get-Process -Id $_ -ErrorAction SilentlyContinue).ProcessName }) }
function Wait-For([scriptblock]$Condition, [int]$Seconds) {
    $end = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $end) { if (& $Condition) { return $true }; Start-Sleep -Milliseconds 300 }
    $false
}
$script:started = @()   # every process this script started, killed at the end whatever happens
$script:readyFiles = @()
function Start-FakeScan([string]$Mode) {
    $ready = Join-Path $env:TEMP ("jobscan-switch-test-{0}.txt" -f [guid]::NewGuid())
    $script:readyFiles += $ready
    $p = Start-Process -FilePath $py -ArgumentList "`"$here\fake_scan.py`"", "`"$ready`"", $Mode -WindowStyle Hidden -PassThru
    $script:started += $p.Id
    $ok = Wait-For { Test-Path -LiteralPath $ready } 90
    $tree = @(Get-Tree $p.Id)
    $script:started += $tree
    [pscustomobject]@{ Ok = $ok; Root = $p.Id; Tree = $tree; Names = (Names $tree) }
}
function Task-Enabled([string]$Name) { [bool](Get-ScheduledTask -TaskName $Name).Settings.Enabled }
function Today-Log { Join-Path $root ("logs\laptop-{0:yyyy-MM-dd}.log" -f (Get-Date)) }

$wasOff = Test-Path -LiteralPath $off
$browserMains = { @(Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -Property ProcessId, CommandLine |
    Where-Object { "$($_.CommandLine)" -notmatch '--type=' -and "$($_.CommandLine)" -notmatch 'jobscan' } | ForEach-Object { [int]$_.ProcessId }) }

try {
    # ---------- on ----------
    Write-Host "`n== ON =="
    $r = Run-Switch on
    Check "jobscan on: exit 0 and says ON" ($r.Code -eq 0 -and $r.Text -match "(?m)^ON\.") $r.Text
    Check "task enabled, state\OFF gone" ((Task-Enabled $realTask) -and -not (Test-Path $off))
    $r = Run-Switch status
    Check "status says ON (exit 0)" ($r.Code -eq 0 -and $r.Text -match "is ON") $r.Text

    # ---------- off kills running scans, and nothing else ----------
    Write-Host "`n== OFF while scans are running =="
    $a = Start-FakeScan chrome
    $b = Start-FakeScan plain
    $c = Start-FakeScan headed
    Check "fake scan with Chrome is up: $($a.Names -join ', ')" ($a.Ok -and ($a.Names -contains "node") -and (@($a.Names | Where-Object { $_ -eq "chrome" }).Count -ge 2) -and (@($a.Names | Where-Object { $_ -eq "python" }).Count -ge 2))
    Check "fake scan without a browser is up" ($b.Ok)
    Check "fake scan with a visible Chrome window is up" ($c.Ok -and ($c.Names -contains "chrome"))

    $decoyProfile = Join-Path $env:TEMP "jobscan-switch-decoy-profile"
    $chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
    $d1 = Start-Process $chrome -ArgumentList "--headless=new", "--user-data-dir=`"$decoyProfile`"", "--remote-debugging-port=0", "--no-first-run", "about:blank" -PassThru
    $d2 = Start-Process "C:\Python313\python.exe" -ArgumentList "-c", "`"import time; time.sleep(600)`"" -WindowStyle Hidden -PassThru
    $d3 = Start-Process $ps -ArgumentList "-NoProfile", "-Command", "`"Start-Sleep 600; '$root\notes'`"" -WindowStyle Hidden -PassThru
    $script:started += $d1.Id, $d2.Id, $d3.Id
    Start-Sleep 3
    $decoys = @($d1.Id, $d2.Id, $d3.Id)
    Check "decoys are running (Chrome on another profile, plain Python, PowerShell naming this folder)" ((Alive $decoys).Count -eq 3)
    $yourChrome = & $browserMains
    $fake = @($a.Tree + $b.Tree + $c.Tree)

    $r = Run-Switch off
    Write-Host "     (jobscan off took $($r.Seconds) s)"
    Check "jobscan off: exit 0 and 'OFF - verified'" ($r.Code -eq 0 -and $r.Text -match "OFF - verified") $r.Text
    Check "it reports the scan processes it stopped" ($r.Text -match "Stopped \d+ scanner processes")
    $left = Alive $fake
    Check "every fake-scan process is gone ($($fake.Count) of them)" ($left.Count -eq 0) "still alive: $($left -join ',') $(Names $left)"
    Check "decoys are untouched" ((Alive $decoys).Count -eq 3) "alive: $(Alive $decoys)"
    Check "your own Chrome is untouched ($($yourChrome.Count) browser processes)" ((Alive $yourChrome).Count -eq $yourChrome.Count)
    Check "task disabled and state\OFF written" ((-not (Task-Enabled $realTask)) -and "$((Get-ScheduledTask -TaskName $realTask).State)" -eq "Disabled" -and (Test-Path $off))
    $xml = [xml](Export-ScheduledTask -TaskName $realTask)
    Check "the saved task definition says disabled (so it stays off after a restart)" ($xml.Task.Settings.Enabled -eq "false")

    # ---------- nothing can start while off ----------
    Write-Host "`n== nothing starts while OFF =="
    $err = ""
    try { Start-ScheduledTask -TaskName $realTask -ErrorAction Stop } catch { $err = $_.Exception.Message }
    Check "Windows refuses to start the task: '$err'" ($err -match "disabled")
    Start-Sleep 4
    Check "no scanner process appeared" (@(Find-ScannerProcesses).Count -eq 0)

    $t = [Diagnostics.Stopwatch]::StartNew()
    & $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "laptop\run_laptop.ps1")
    $code = $LASTEXITCODE
    $last = Get-Content (Today-Log) -Tail 1
    Check "run_laptop.ps1 started anyway exits at once and logs it ($([math]::Round($t.Elapsed.TotalSeconds,1)) s)" ($code -eq 0 -and $last -match "Switched OFF") "exit $code; log: $last"

    $t = [Diagnostics.Stopwatch]::StartNew()
    $text = cmd /c "`"$py`" -m jobscan --group laptop --if-due 2>&1" | Out-String
    $code = $LASTEXITCODE
    Check "python -m jobscan refuses to read ($([math]::Round($t.Elapsed.TotalSeconds,1)) s): $($text.Trim())" ($code -ne 0 -and $text -match "switched OFF")
    $text = cmd /c "`"$py`" -m jobscan --dry-run --only unstop 2>&1" | Out-String
    Check "so does a manual dry run" ($LASTEXITCODE -ne 0 -and $text -match "switched OFF")

    $text = & $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "laptop\install_task.ps1") | Out-String
    Check "reinstalling the task keeps it disabled" ((-not (Task-Enabled $realTask)) -and $text -match "installed disabled") $text

    $before = Get-Content $off -Raw
    $r = Run-Switch off
    Check "jobscan off again: still 'OFF - verified', keeps the original 'since' time" ($r.Code -eq 0 -and $r.Text -match "OFF - verified" -and (Get-Content $off -Raw) -eq $before) $r.Text

    # ---------- half-off states are caught and repaired ----------
    Write-Host "`n== half-off states =="
    Enable-ScheduledTask -TaskName $realTask | Out-Null          # something turned the task back on
    $r = Run-Switch status
    Check "status: task re-enabled behind the switch's back -> PARTLY OFF, exit 1" ($r.Code -eq 1 -and $r.Text -match "PARTLY OFF") $r.Text
    $lines = @(Get-Content (Today-Log)).Count
    Start-ScheduledTask -TaskName $realTask
    $sawScan = $false
    $end = (Get-Date).AddSeconds(8)
    while ((Get-Date) -lt $end) {
        if (@(Find-ScannerProcesses | Where-Object { $_.Name -match "python|chrome|node" }).Count) { $sawScan = $true }
        Start-Sleep -Milliseconds 200
    }
    $new = @(Get-Content (Today-Log)) | Select-Object -Skip $lines
    Check "...and even then state\OFF stops the start before any Python or Chrome" ((-not $sawScan) -and ($new -match "Switched OFF")) "saw scan: $sawScan; log: $new"
    $r = Run-Switch off
    Check "jobscan off repairs it" ($r.Code -eq 0 -and -not (Task-Enabled $realTask)) $r.Text

    Remove-Item $off
    $r = Run-Switch status
    Check "status: state\OFF deleted -> PARTLY OFF, exit 1" ($r.Code -eq 1 -and $r.Text -match "PARTLY OFF") $r.Text
    $r = Run-Switch off
    Check "jobscan off repairs it" ($r.Code -eq 0 -and (Test-Path $off)) $r.Text

    # ---------- the task is running when you switch off ----------
    Write-Host "`n== OFF while the task itself is running a scan =="
    $ready = Join-Path $env:TEMP ("jobscan-switch-test-{0}.txt" -f [guid]::NewGuid())
    $script:readyFiles += $ready
    $act = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$here\fake_launcher.ps1`" -Ready `"$ready`" -Mode chrome"
    $who = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
    $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $testTask -Action $act -Principal $who -Settings $set -Force | Out-Null
    Start-ScheduledTask -TaskName $testTask
    $up = Wait-For { Test-Path -LiteralPath $ready } 90
    $launcher = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -Property ProcessId, CommandLine | Where-Object { "$($_.CommandLine)" -match "fake_launcher" })
    $tree = @(); foreach ($l in $launcher) { $tree += Get-Tree ([int]$l.ProcessId) }
    $script:started += $tree
    Check "throwaway task is running a fake scan: $((Names $tree) -join ', ')" ($up -and "$((Get-ScheduledTask -TaskName $testTask).State)" -eq "Running" -and $tree.Count -ge 5)
    $r = Run-Switch off $testTask
    Check "jobscan off: exit 0, 'OFF - verified'" ($r.Code -eq 0 -and $r.Text -match "OFF - verified") $r.Text
    Check "the whole tree is gone, launcher and cmd included" ((Alive $tree).Count -eq 0) "alive: $(Names (Alive $tree))"
    Check "the task is stopped and disabled" ("$((Get-ScheduledTask -TaskName $testTask).State)" -eq "Disabled")
    Unregister-ScheduledTask -TaskName $testTask -Confirm:$false

    # ---------- switching off just as the real task starts ----------
    Write-Host "`n== OFF a moment after the real task starts =="
    foreach ($delay in 0, 1, 2, 4) {
        $null = Run-Switch on
        Start-ScheduledTask -TaskName $realTask
        Start-Sleep -Seconds $delay
        $r = Run-Switch off
        Start-Sleep 3
        $after = @(Find-ScannerProcesses)
        Check "started, then off after $delay s: verified, and nothing running 3 s later" ($r.Code -eq 0 -and $r.Text -match "OFF - verified" -and $after.Count -eq 0) "$($r.Text) after: $(Format-Processes $after)"
    }

    # ---------- the shortcuts and the jobscan command ----------
    Write-Host "`n== shortcuts and the jobscan command =="
    $shell = New-Object -ComObject WScript.Shell
    $menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Job Scanner"
    $desk = [Environment]::GetFolderPath("Desktop")
    $links = @("$menu\Job Scanner OFF.lnk", "$menu\Job Scanner ON.lnk", "$menu\Job Scanner status.lnk", "$desk\Job Scanner OFF.lnk", "$desk\Job Scanner ON.lnk")
    Check "all 5 shortcuts exist" (@($links | Where-Object { Test-Path -LiteralPath $_ }).Count -eq 5)
    $empty = [IO.Path]::GetTempFileName()
    foreach ($name in "Job Scanner ON", "Job Scanner status", "Job Scanner OFF") {
        $lnk = $shell.CreateShortcut("$menu\$name.lnk")
        $out = [IO.Path]::GetTempFileName()
        $p = Start-Process -FilePath $lnk.TargetPath -ArgumentList $lnk.Arguments -WorkingDirectory $lnk.WorkingDirectory `
            -RedirectStandardInput $empty -RedirectStandardOutput $out -WindowStyle Hidden -PassThru
        $null = $p.Handle
        $done = $p.WaitForExit(120000)
        $text = Get-Content $out -Raw
        Remove-Item $out -ErrorAction SilentlyContinue
        $ok = $done -and $p.ExitCode -eq 0 -and $text -match "Press any key"
        if ($name -match "ON$") { $ok = $ok -and $text -match "(?m)^ON\." }
        if ($name -match "OFF$") { $ok = $ok -and $text -match "OFF - verified" }
        Check "shortcut '$name' works and its window waits for a key" $ok "done=$done exit=$($p.ExitCode) $text"
    }
    Remove-Item $empty -ErrorAction SilentlyContinue
    foreach ($step in @(@("status", "is OFF"), @("on", "(?m)^ON\."), @("status", "is ON"), @("off", "OFF - verified"))) {
        $text = cmd /c "cd /d %USERPROFILE% && jobscan $($step[0])" | Out-String
        Check "'jobscan $($step[0])' from any folder" ($LASTEXITCODE -eq 0 -and $text -match $step[1]) $text
    }
} catch {
    Check "the test run itself didn't crash" $false "$($_.Exception.Message) (line $($_.InvocationInfo.ScriptLineNumber))"
} finally {
    # ---------- clean up, and leave the switch as it was ----------
    Write-Host "`n== cleanup =="
    foreach ($id in ($script:started | Select-Object -Unique)) { Stop-Process -Id $id -Force -ErrorAction SilentlyContinue }
    if (Get-ScheduledTask -TaskName $testTask -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $testTask -Confirm:$false }
    Start-Sleep 2
    Get-ChildItem (Join-Path $root "browser-profile") -Filter "switch-test-*" -Directory -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $env:TEMP "jobscan-switch-decoy-profile") -Recurse -Force -ErrorAction SilentlyContinue
    $script:readyFiles | ForEach-Object { Remove-Item -LiteralPath $_ -ErrorAction SilentlyContinue }
    $back = if ($wasOff) { "off" } else { "on" }
    $r = Run-Switch $back
    Write-Host "  switch left $($back.ToUpper()) as it was found (exit $($r.Code))"
}

Write-Host "`n$($script:passed) passed, $($script:failed.Count) failed"
if ($script:failed.Count) { $script:failed | ForEach-Object { Write-Host "  FAILED: $_" -ForegroundColor Red } }
exit [int]($script:failed.Count -gt 0)
