<#
.SYNOPSIS
  Push the latest Sound Safari code to the kids' PCs from YOUR machine.

.DESCRIPTION
  Run this on YOUR computer after you've pushed changes to GitHub. For each
  target PC listed in hosts.txt it connects over SSH and:
    1. git pull --ff-only   (gets your latest code)
    2. reinstalls Python deps ONLY if requirements changed (-Full forces it)
    3. re-runs the doctor so chopshop.json stays correct

  No file copying, no servers - the kids' PCs pull from GitHub, you just trigger
  it remotely. Targets are defined in deploy\hosts.txt (see hosts.example.txt).

.PARAMETER HostsFile
  Path to the hosts list. Defaults to deploy\hosts.txt next to this script.

.PARAMETER Full
  Force a full dependency reinstall (use when you've added/changed packages).

.PARAMETER Only
  Redeploy just one host by name (matches the 'name' column in hosts.txt).

.EXAMPLE
  .\redeploy.ps1                 # update all kid PCs (code only)
  .\redeploy.ps1 -Full           # update all + reinstall dependencies
  .\redeploy.ps1 -Only lucas-pc  # update just one
#>

[CmdletBinding()]
param(
  [string]$HostsFile = (Join-Path $PSScriptRoot "hosts.txt"),
  [switch]$Full,
  [string]$Only = ""
)

$ErrorActionPreference = "Stop"
function Write-Step($m){ Write-Host "`n=== $m ===" -ForegroundColor Cyan }

if (-not (Test-Path $HostsFile)) {
  Write-Error "No hosts file at $HostsFile. Copy hosts.example.txt to hosts.txt and fill it in."
}

# Parse hosts.txt: lines of  name  user@host  [appdir]   (whitespace-separated, # = comment)
$targets = @()
foreach ($line in Get-Content $HostsFile) {
  $t = $line.Trim()
  if (-not $t -or $t.StartsWith("#")) { continue }
  $parts = $t -split "\s+"
  if ($parts.Count -lt 2) { Write-Warning "Skipping malformed line: $line"; continue }
  $targets += [pscustomobject]@{
    Name   = $parts[0]
    Target = $parts[1]                                   # user@host
    AppDir = if ($parts.Count -ge 3) { $parts[2] } else { "C:\SoundSafari\chopshop" }
  }
}
if ($Only) { $targets = $targets | Where-Object { $_.Name -eq $Only } }
if (-not $targets) { Write-Error "No matching targets to redeploy." }

# The remote command run on each kid PC. Written as a single PowerShell line
# so we can hand it to ssh. It pulls, conditionally reinstalls, re-runs doctor.
function Build-RemoteCommand($appDir, $forceFull) {
  $venvPy = Join-Path $appDir ".venv\Scripts\python.exe"
  $full = if ($forceFull) { '$true' } else { '$false' }
  @"
`$ErrorActionPreference='Stop'
Set-Location '$appDir'
`$before = (git rev-parse HEAD)
git pull --ff-only
`$after = (git rev-parse HEAD)
Write-Host ("  code: {0} -> {1}" -f `$before.Substring(0,7), `$after.Substring(0,7))
`$venv = '$venvPy'
`$depsChanged = (git diff --name-only `$before `$after) -match 'requirements|setup|deploy/setup-kid-pc'
if ($full -or `$depsChanged) {
  Write-Host '  dependencies changed - reinstalling core deps'
  & `$venv -m pip install --upgrade numpy soundfile librosa ableton-device-creator laion-clap torch
} else {
  Write-Host '  no dependency change - skipping pip'
}
& `$venv chopshop_doctor.py --config (Join-Path '$appDir' 'chopshop.json') | Out-Null
Write-Host '  doctor config refreshed'
"@
}

Write-Step "Redeploying to $($targets.Count) machine(s)"
$ok = 0; $fail = 0
foreach ($t in $targets) {
  Write-Host "`n--- $($t.Name)  [$($t.Target)] ---" -ForegroundColor Yellow
  $remoteCmd = Build-RemoteCommand $t.AppDir $Full.IsPresent
  # encode to survive quoting across ssh; run via powershell -EncodedCommand
  $bytes = [Text.Encoding]::Unicode.GetBytes($remoteCmd)
  $enc = [Convert]::ToBase64String($bytes)
  try {
    ssh $t.Target "powershell -NoProfile -EncodedCommand $enc"
    if ($LASTEXITCODE -eq 0) { Write-Host "  OK" -ForegroundColor Green; $ok++ }
    else { Write-Warning "  ssh returned exit $LASTEXITCODE"; $fail++ }
  } catch {
    Write-Warning "  failed: $_"; $fail++
  }
}

Write-Step "Summary"
Write-Host "  updated: $ok   failed: $fail"
if ($fail -gt 0) {
  Write-Host "  Tip: confirm the PC is on, SSH is enabled (setup script does this)," -ForegroundColor DarkYellow
  Write-Host "       and your key/password works:  ssh <user@host>" -ForegroundColor DarkYellow
}
