# Puts Job Scanner's on/off switch (laptop\scanner_switch.ps1) where you can reach it:
#   - any terminal:  jobscan off | jobscan on | jobscan status
#     (jobscan.cmd in %USERPROFILE%\.local\bin, which is on PATH)
#   - Start menu:    press the Windows key, type "Job Scanner OFF" (or ON, or status), Enter
#   - desktop:       "Job Scanner OFF" and "Job Scanner ON"
# laptop\install_task.ps1 runs this too. Running it again just rewrites these files.
#
#   powershell -ExecutionPolicy Bypass -File laptop\install_switch.ps1
param([string]$BinDir = (Join-Path $HOME ".local\bin"))
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$switch = Join-Path $repo "laptop\scanner_switch.ps1"
$ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

# jobscan.cmd
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
$cmd = Join-Path $BinDir "jobscan.cmd"
[IO.File]::WriteAllText($cmd, (
    "@echo off`r`n" +
    "rem Job Scanner's on/off switch: jobscan off, jobscan on, jobscan status. Written by $repo\laptop\install_switch.ps1`r`n" +
    "`"$ps`" -NoProfile -ExecutionPolicy Bypass -File `"$switch`" %*`r`n" +
    "exit /b %ERRORLEVEL%`r`n"), [Text.Encoding]::Default)
$onPath = ($env:Path -split ";") | Where-Object { $_.TrimEnd("\") -ieq $BinDir.TrimEnd("\") }
if (-not $onPath) {
    Write-Host "Note: $BinDir isn't on your PATH, so 'jobscan off' only works there; the shortcuts work anywhere."
}

# Shortcuts
$shell = New-Object -ComObject WScript.Shell
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Job Scanner"
$desktop = [Environment]::GetFolderPath("Desktop")
New-Item -ItemType Directory -Force -Path $menu | Out-Null
$icons = @{
    off = "$env:SystemRoot\System32\shell32.dll,27"      # red power button
    on = "$env:SystemRoot\System32\shell32.dll,137"      # play
    status = "$env:SystemRoot\System32\imageres.dll,76"  # info
}
$made = @()
foreach ($action in "off", "on", "status") {
    $name = if ($action -eq "status") { "Job Scanner status" } else { "Job Scanner $($action.ToUpper())" }
    $folders = @($menu)
    if ($action -ne "status") { $folders += $desktop }
    foreach ($folder in $folders) {
        $path = Join-Path $folder "$name.lnk"
        $link = $shell.CreateShortcut($path)
        $link.TargetPath = $ps
        $link.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$switch`" $action -Wait"
        $link.WorkingDirectory = $repo
        $link.IconLocation = $icons[$action]
        $link.Description = "Job Scanner: switch the laptop scanner $action (jobscan $action)"
        $link.Save()
        $made += $path
    }
}
Write-Host "Job Scanner switch installed:"
Write-Host "  jobscan off | on | status   ($cmd)"
$made | ForEach-Object { Write-Host "  $_" }
